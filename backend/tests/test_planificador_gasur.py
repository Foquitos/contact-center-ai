"""Gasur en el planificador: la fuente llamada por llamada y el calendario uruguayo.

- El SQL de `FuenteGasur` se valida con `describe` contra las tablas reales (sin
  ejecutar): columnas con acentos y paréntesis (`[Duración (seg.)]`,
  `[Espera (seg.)]`) que un error de tipeo rompe recién en producción.
- Los feriados de Uruguay y el umbral de 10 s son lógica pura.
- La migración de alta se chequea leyendo el texto, si está en scripts/migrations.

Correr: pytest tests/test_planificador_gasur.py -m "not tokens"
"""
import os
import re
from datetime import date, datetime, time

import pytest

from app import planificador_datos as pd
from app import planificador_salud as salud

from test_planificador_hidra import _batches


def _literales(sql: str) -> str:
    sql = sql.replace("IN :skills", "IN (1)")
    sql = re.sub(r":desde\b", "'2026-09-14'", sql)
    sql = re.sub(r":hasta\b", "'2026-09-21'", sql)
    return re.sub(r":\w+", "1", sql)


FUENTE = pd.FuenteGasur
CONSULTAS = [(n, getattr(FUENTE, n)) for n in (
    "SERIE", "PACIENCIA", "PACIENCIA_CURVA", "CONECTADOS", "CONECTADOS_POR_CAMPANA",
    "RINDE_POR_ANTIGUEDAD", "LLAMADAS_POR_ANTIGUEDAD", "CALIBRACION")]

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRACION = os.path.join(REPO_ROOT, "scripts", "migrations",
                         "2026-09-24d_planificador_gasur.sql")


@pytest.mark.parametrize("nombre,sql", CONSULTAS, ids=[n for n, _ in CONSULTAS])
def test_las_consultas_de_gasur_resuelven_nombres(validar_sql, nombre, sql):
    resultado = validar_sql(_literales(sql))
    assert resultado.ok, f"{nombre}: {resultado.error}"


def test_gasur_tiene_fuente_y_forecast_del_cliente():
    assert pd.FUENTES[pd.CAMPANA_GASUR] is FUENTE
    assert pd.CAMPANA_EN_FORECAST[pd.CAMPANA_GASUR] == "Gasur"
    # dbo.Forecast de Gasur viene por hora y sin skill, como el de Hidra.
    assert pd.FORECAST_SIN_SKILL[pd.CAMPANA_GASUR] == (FUENTE.SKILL_ID, 60)


def test_sólo_las_entrantes_son_demanda():
    """El reporte trae también las salientes (externas y locales): no son cola."""
    for nombre in ("SERIE", "PACIENCIA", "PACIENCIA_CURVA", "CALIBRACION",
                   "RINDE_POR_ANTIGUEDAD", "LLAMADAS_POR_ANTIGUEDAD"):
        assert "l.Tipo = 'Entrante'" in getattr(FUENTE, nombre), nombre


def test_la_paciencia_se_cruza_con_el_nombre_del_skill():
    for sql in (FUENTE.PACIENCIA, FUENTE.PACIENCIA_CURVA):
        assert re.search(r"'Gasur'\s+AS skill", sql)
    if os.path.exists(MIGRACION):
        assert re.search(r"\(\s*30\s*,\s*1\s*,\s*'Gasur'", _migracion())


def test_el_skill_de_las_consultas_es_el_de_la_fuente():
    assert FUENTE.SKILL_ID == 1
    assert "1 AS skill_id" in FUENTE.SERIE
    assert "1 AS skill_id" in FUENTE.RINDE_POR_ANTIGUEDAD
    assert "1 IN :skills" in FUENTE.CALIBRACION


def test_el_nivel_de_servicio_real_es_a_10_segundos():
    """El `Nivel de Servicio %` de Gasur Eficiencia es atendidas en <= 10 s sobre
    entrantes; la calibración y el «NDS real» tienen que medir lo mismo."""
    assert "l.[Espera (seg.)] <= 10" in FUENTE.CALIBRACION
    assert pd.umbral_real_de(pd.CAMPANA_GASUR) == 10
    assert pd.umbral_real_de(pd.CAMPANA_VOLTARA) == 20
    assert pd.umbral_real_de(pd.CAMPANA_HIDRA_TECNICO) == 20


def test_la_calibracion_no_descarta_los_intervalos_sin_conectados():
    """Actividad carga con el día cerrado: si la calibración exigiera conectados,
    el servicio real de hoy desaparecería de la pantalla."""
    assert "WHERE p.atendidas > 0" in FUENTE.CALIBRACION
    assert "agentes > 0" not in FUENTE.CALIBRACION


def test_los_logout_se_aparean_por_orden_y_no_fila_por_fila():
    """El reporte le pega a cada login el logout de otra sesión del día."""
    for sql in (FUENTE.CONECTADOS, FUENTE.CONECTADOS_POR_CAMPANA, FUENTE.CALIBRACION):
        assert "ROW_NUMBER() OVER (PARTITION BY Agente, Fecha ORDER BY Login)" in sql
        assert "ORDER BY al_dia_siguiente, Logout" in sql
        assert "s.k = e.k" in sql
        assert "WHERE s.fin > e.ini" in sql


def test_la_persona_del_login_sale_del_turno_de_ese_dia():
    """Los logins 5xx están reciclados: no se deduplica por usuario sino que se
    elige a quien tenía turno, primero la gente de Gasur y Despacho."""
    for sql in (FUENTE.CONECTADOS_POR_CAMPANA, FUENTE.RINDE_POR_ANTIGUEDAD,
                FUENTE.LLAMADAS_POR_ANTIGUEDAD):
        assert "OUTER APPLY" in sql
        assert "o.campana_id IN (121, 135)" in sql
        assert "MAX(nomina_id)" not in sql


# ------------------------------------------------------------------ feriados

class _SinTabla:
    """Si se consultara dbo.Feriados (el calendario argentino) el test lo ve."""
    def execute(self, *a, **k):
        raise AssertionError("Gasur no tiene que leer dbo.Feriados")


def test_gasur_usa_los_feriados_no_laborables_de_uruguay():
    dias = pd.feriados(_SinTabla(), date(2026, 1, 1), date(2026, 12, 31), pd.CAMPANA_GASUR)
    assert dias == [date(2026, 1, 1), date(2026, 5, 1), date(2026, 7, 18),
                    date(2026, 8, 25), date(2026, 12, 25)]
    # Carnaval y Turismo son laborables: medido, no se comportan como feriado.
    assert date(2026, 2, 16) not in dias and date(2026, 3, 30) not in dias
    # Y ningún feriado argentino.
    assert date(2026, 5, 25) not in dias and date(2026, 7, 9) not in dias


def test_uruguay_no_tiene_puentes():
    assert pd.puentes(date(2026, 1, 1), date(2026, 12, 31), pd.CAMPANA_GASUR) == []
    # Argentina sigue igual (el 10/07/2026 es puente).
    assert date(2026, 7, 10) in pd.puentes(date(2026, 1, 1), date(2026, 12, 31),
                                           pd.CAMPANA_HIDRA_TECNICO)


def test_sin_campana_los_feriados_son_argentinos():
    assert pd.pais_de_la_campana(None) == "AR"
    assert pd.pais_de_la_campana(pd.CAMPANA_VOLTARA) == "AR"
    assert pd.pais_de_la_campana(pd.CAMPANA_GASUR) == "UY"


# ------------------------------------------------------------------ salud

def test_la_salud_mira_las_tablas_de_gasur_sólo_en_gasur():
    claves = [clave for clave, _, _ in salud.FUENTES]
    assert "llamadas_gasur" in claves and "actividad_gasur" in claves
    ahora = datetime.combine(date(2026, 9, 24), time(12, 0))
    for otra in (pd.CAMPANA_VOLTARA, pd.CAMPANA_HIDRA_TECNICO):
        assert salud._llamadas_gasur(None, otra, ahora) is None
        assert salud._actividad_gasur(None, otra, ahora) is None
    assert salud._acumuladores_mitrol(None, pd.CAMPANA_GASUR, ahora) is None
    assert salud._informe_skills(None, pd.CAMPANA_GASUR, ahora) is None


def test_la_migracion_de_gasur_esta_registrada():
    archivos = [m["archivo"] for m in salud.REGISTRO_MIGRACIONES]
    assert "2026-09-24d_planificador_gasur.sql" in archivos


# ------------------------------------------------------------------ migración

def _migracion() -> str:
    with open(MIGRACION, encoding="utf-8") as f:
        return f.read()


requiere_migracion = pytest.mark.skipif(
    not os.path.exists(MIGRACION), reason="scripts/migrations no viaja por git")


@requiere_migracion
def test_la_siembra_es_idempotente():
    sql = _migracion()
    for tabla in ("Campana", "Pool", "Skill", "Disponibilidad"):
        assert re.search(rf"IF NOT EXISTS \(SELECT 1 FROM planificacion\.{tabla}\b", sql), tabla
    assert "IF NOT EXISTS (SELECT 1 FROM calidad.Campanas WHERE CampanaID = 30)" in sql


@requiere_migracion
def test_el_id_de_la_campaña_es_el_del_código():
    """El código busca la campaña por id: la migración la crea con ese id fijo y
    no siembra nada si ya es otra."""
    sql = _migracion()
    assert pd.CAMPANA_GASUR == 30
    assert "SET IDENTITY_INSERT calidad.Campanas ON" in sql
    assert "CampanaID = 30 AND Nombre <> N'Gasur'" in sql
    assert "SET NOEXEC ON" in sql


@requiere_migracion
def test_el_permiso_nace_sin_asignar():
    sql = _migracion()
    assert "'template:gasur'" in sql
    assert "RolePermissions" not in sql
    assert "CampanaID = 20" not in sql and "CampanaID = 1 " not in sql
    assert not re.search(r"\b(DELETE|DROP|TRUNCATE)\b", sql, re.I)


@requiere_migracion
def test_el_pool_es_gasur_y_despacho_es_refuerzo():
    sql = _migracion()
    origen = sql[sql.index("INSERT INTO planificacion.PoolOrigen"):]
    origen = origen[:origen.index(";")]
    assert re.search(r"@pool\s*,\s*121\b", origen)
    refuerzo = sql[sql.index("INSERT INTO planificacion.PoolRefuerzo"):]
    refuerzo = refuerzo[:refuerzo.index(";")]
    assert re.search(r"@pool\s*,\s*135\b", refuerzo)


@requiere_migracion
def test_sintaxis_de_la_migracion(engine):
    from conftest import _validar_parseonly
    for i, batch in enumerate(_batches(_migracion())):
        if batch.strip() and not batch.strip().upper().startswith("USE "):
            resultado = _validar_parseonly(engine, batch)
            assert resultado.ok, f"batch {i}: {resultado.error}"


# ------------------------------------- forma reciente y persistencia sin eventos

from datetime import timedelta

from app import planificador as pl


def _historia(dias, forma, total=100.0):
    """`dias` días hábiles con `total` llamadas repartidas según `forma`
    (minuto del día -> proporción)."""
    salida = {}
    for d in dias:
        for minuto, parte in forma.items():
            salida[datetime.combine(d, time(0, 0)) + timedelta(minutes=minuto)] = total * parte
    return salida


def test_la_forma_reciente_reparte_el_total_sin_cambiarlo():
    hoy = date(2026, 9, 21)                                  # lunes
    habiles = [hoy - timedelta(days=k) for k in range(1, 15)
               if (hoy - timedelta(days=k)).weekday() < 5]
    pron = {datetime(2026, 9, 21, 10, 0): 60.0, datetime(2026, 9, 21, 11, 0): 40.0}
    # La historia reciente pone el 25% a las 10 y el 75% a las 22 (horario nuevo).
    hist = _historia(habiles, {600: 0.25, 1320: 0.75})
    salida = pl._forma_reciente(pron, hist, 28, hoy, set(), {}, set(), None,
                                timedelta(minutes=30))
    assert sum(salida.values()) == pytest.approx(100.0)
    assert salida[datetime(2026, 9, 21, 10, 0)] == pytest.approx(25.0)
    assert salida[datetime(2026, 9, 21, 22, 0)] == pytest.approx(75.0)
    assert datetime(2026, 9, 21, 11, 0) not in salida


def test_la_forma_reciente_sólo_mira_el_mismo_tipo_de_día_y_saca_los_atípicos():
    hoy = date(2026, 9, 21)
    habiles = [hoy - timedelta(days=k) for k in range(1, 15)
               if (hoy - timedelta(days=k)).weekday() < 5]
    domingos = [date(2026, 9, 13), date(2026, 9, 20)]
    hist = _historia(habiles, {600: 1.0})
    hist.update(_historia(domingos, {1320: 1.0}))                 # no cuenta
    atipico = habiles[0]
    hist.update(_historia([atipico], {900: 1.0}, total=10_000))   # paro: se saca
    pron = {datetime(2026, 9, 21, 12, 0): 50.0}
    salida = pl._forma_reciente(pron, hist, 28, hoy, set(), {}, {atipico}, None,
                                timedelta(minutes=30))
    assert salida == {datetime(2026, 9, 21, 10, 0): pytest.approx(50.0)}


def test_sin_suficientes_días_queda_la_forma_del_perfil():
    hoy = date(2026, 9, 21)
    hist = _historia([date(2026, 9, 18)], {600: 1.0})             # un solo hábil
    pron = {datetime(2026, 9, 21, 12, 0): 50.0}
    assert pl._forma_reciente(pron, hist, 28, hoy, set(), {}, set(), None,
                              timedelta(minutes=30)) == pron


def _persistencia(saltea: bool):
    """Cinco semanas planas de 100 por día y ayer un paro de 400."""
    hoy = date(2026, 9, 22)
    hist = {}
    for k in range(1, 36):
        d = hoy - timedelta(days=k)
        hist[datetime.combine(d, time(10, 0))] = 400.0 if k == 1 else 100.0
    return pl.baseline_estacional(
        hist, [hoy], hoy=hoy, excluir_dias=[hoy - timedelta(days=1)],
        semanas_base=4, dias_nivel=14, persistencia=(0.7, 0.5, 7),
        persistencia_saltea_eventos=saltea)


def test_la_persistencia_puede_saltear_el_día_atípico():
    arrastra = sum(_persistencia(False).values())
    saltea = sum(_persistencia(True).values())
    assert saltea == pytest.approx(100.0, rel=0.05)
    assert arrastra > 150


def test_la_config_de_gasur_admite_los_parámetros_nuevos():
    from app import planificador_servicio as serv
    assert serv.validar_ajustes_de_modelo(
        {"forma_dias": 28, "persistencia_saltea_eventos": True}) == {
        "forma_dias": 28, "persistencia_saltea_eventos": True}
    with pytest.raises(ValueError):
        serv.validar_ajustes_de_modelo({"forma_dias": 3})


MIGRACION_E = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-24e_planificador_gasur_forma_paros.sql")


def test_la_migracion_de_forma_y_paros_esta_registrada():
    archivos = [m["archivo"] for m in salud.REGISTRO_MIGRACIONES]
    assert "2026-09-24e_planificador_gasur_forma_paros.sql" in archivos


@pytest.mark.skipif(not os.path.exists(MIGRACION_E), reason="scripts/migrations no viaja por git")
def test_la_migracion_de_forma_sólo_prende_gasur(engine):
    from conftest import _validar_parseonly
    with open(MIGRACION_E, encoding="utf-8") as f:
        sql = f.read()
    assert "WHERE CampanaID = 30" in sql and "FormaDias IS NULL" in sql
    assert not re.search(r"\b(DELETE|DROP|TRUNCATE)\b", sql, re.I)
    for i, batch in enumerate(_batches(sql)):
        if batch.strip():
            resultado = _validar_parseonly(engine, batch)
            assert resultado.ok, f"batch {i}: {resultado.error}"


# ------------------------------------------------ ancla al total del mes del cliente

def _dias(desde, n, valor=100.0):
    return {desde + timedelta(days=i): valor for i in range(n)}


def test_el_ancla_lleva_el_resto_del_mes_al_total_del_cliente():
    hoy = date(2026, 9, 10)
    nuestro = _dias(hoy, 21)                    # 10/09 al 30/09: 2.100
    # El cliente dijo 3.000 y ya entraron 600: faltan 2.400 -> x1,1429.
    f = pl.factores_ancla_mensual(nuestro, {(2026, 9): 600.0}, {(2026, 9): 3000.0},
                                  hoy, peso=1.0, desde_dias=7)
    assert date(2026, 9, 16) not in f            # 6 días: no se toca
    assert f[date(2026, 9, 17)] == pytest.approx(2400 / 2100)
    assert f[date(2026, 9, 30)] == pytest.approx(2400 / 2100)
    con_peso = pl.factores_ancla_mensual(nuestro, {(2026, 9): 600.0}, {(2026, 9): 3000.0},
                                         hoy, peso=0.5, desde_dias=7)
    assert con_peso[date(2026, 9, 20)] == pytest.approx((2400 / 2100) ** 0.5)


def test_el_ancla_no_se_aplica_con_poco_mes_o_sin_el_resto_completo():
    hoy = date(2026, 9, 26)                     # quedan 5 días
    assert pl.factores_ancla_mensual(_dias(hoy, 30), {}, {(2026, 9): 3000.0,
                                     (2026, 10): 3000.0}, hoy, 1.0, 0).keys() \
        .isdisjoint({date(2026, 9, 28)})
    # Octubre no está entero en el pronóstico (llega al 25/10): tampoco.
    f = pl.factores_ancla_mensual(_dias(hoy, 30), {}, {(2026, 10): 3000.0}, hoy, 1.0, 0)
    assert f == {}


def test_el_ancla_está_acotada_y_apagada_con_peso_cero():
    hoy = date(2026, 9, 1)
    nuestro = _dias(hoy, 30)
    f = pl.factores_ancla_mensual(nuestro, {}, {(2026, 9): 100_000.0}, hoy, 1.0, 0)
    assert max(f.values()) == pytest.approx(pl.TOPE_ANCLA[1])
    assert pl.factores_ancla_mensual(nuestro, {}, {(2026, 9): 100_000.0}, hoy, 0.0, 0) == {}


def test_el_ancla_se_aplica_a_las_filas_y_a_la_demanda(monkeypatch):
    from app import planificador_servicio as serv
    hoy = date(2026, 9, 10)
    filas = [{"momento": datetime.combine(hoy + timedelta(days=i), time(12, 0)),
              "skill": 1, "acme": 100.0} for i in range(21)]
    demanda = {f["momento"]: [pl.DemandaSkill(1, 100.0, 80.0)] for f in filas}
    serie = {(datetime(2026, 9, 5, 12, 0), 1): (600.0, 80.0)}
    monkeypatch.setattr(pd, "hay_forecast_en_produccion", lambda c, cid: True)
    monkeypatch.setattr(pd, "forecast_en_produccion", lambda c, cid, a, b: {
        (datetime(2026, 9, 15, 12, 0), 1): 3000.0})
    cfg = pl.CampanaCfg(campana_id=pd.CAMPANA_GASUR, ancla_mensual_peso=1.0,
                        ancla_mensual_desde_dias=7)
    r = serv._anclar_al_mes_del_cliente(None, cfg, pd.CAMPANA_GASUR, serie, filas,
                                        demanda, hoy)
    assert r["aplicado"] and r["total_cliente"] == {"2026-09": 3000}
    assert filas[6]["acme"] == 100.0                        # 16/09: 6 días, no
    assert filas[7]["acme"] == pytest.approx(100 * 2400 / 2100, abs=0.01)
    assert demanda[filas[7]["momento"]][0].llamadas == pytest.approx(100 * 2400 / 2100)


def test_sin_peso_el_ancla_no_consulta_nada():
    from app import planificador_servicio as serv
    cfg = pl.CampanaCfg(campana_id=pd.CAMPANA_GASUR)
    assert serv._anclar_al_mes_del_cliente(None, cfg, 30, {}, [{"x": 1}], {}, date.today()) is None
