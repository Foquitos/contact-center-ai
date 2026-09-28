"""Hidra Técnico en el planificador: la fuente de Mitrol (campaña HIDRAIN).

- El SQL de `FuenteHidraTecnico` se valida con `describe` contra las tablas reales
  de Mitrol (sin ejecutar): nombres con acentos (`CAMPAÑA`) y paréntesis
  (`[LOGIN (s)]`) que un error de tipeo rompe recién en producción.
- El reparto del forecast del cliente (por hora y sin skill) es lógica pura.
- La migración de alta se chequea leyendo el texto, si está en scripts/migrations
  (la carpeta no viaja por git).

Correr: pytest tests/test_planificador_hidra.py -m "not tokens"
"""
import math
import os
import re
from datetime import date, datetime, time

import pytest

from app import planificador_datos as pd
from app import planificador_salud as salud


def _literales(sql: str) -> str:
    """Los `:param` de SQLAlchemy a literales, para que `describe` lo entienda
    (igual que en test_planificador_datos_sql)."""
    sql = sql.replace("IN :skills", "IN (481)")
    sql = re.sub(r":desde\b", "'2026-09-14'", sql)
    sql = re.sub(r":hasta\b", "'2026-09-21'", sql)
    return re.sub(r":\w+", "1", sql)


def _batches(sql_text: str):
    batch, batches = [], []
    for linea in sql_text.splitlines():
        if linea.strip().upper() == "GO":
            if batch:
                batches.append("\n".join(batch))
                batch = []
        else:
            batch.append(linea)
    if batch and "\n".join(batch).strip():
        batches.append("\n".join(batch))
    return batches


FUENTE = pd.FuenteHidraTecnico
CONSULTAS = [(n, getattr(FUENTE, n)) for n in (
    "SERIE", "PACIENCIA", "PACIENCIA_CURVA", "CONECTADOS", "CONECTADOS_POR_CAMPANA",
    "RINDE_POR_ANTIGUEDAD", "LLAMADAS_POR_ANTIGUEDAD", "CALIBRACION")]

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRACION = os.path.join(REPO_ROOT, "scripts", "migrations",
                         "2026-09-22_planificador_hidra_tecnico.sql")


@pytest.mark.parametrize("nombre,sql", CONSULTAS, ids=[n for n, _ in CONSULTAS])
def test_las_consultas_de_hidra_resuelven_nombres(validar_sql, nombre, sql):
    resultado = validar_sql(_literales(sql))
    assert resultado.ok, f"{nombre}: {resultado.error}"


def test_hidra_tiene_fuente_y_forecast_del_cliente():
    assert pd.FUENTES[pd.CAMPANA_HIDRA_TECNICO] is FUENTE
    assert pd.CAMPANA_EN_FORECAST[pd.CAMPANA_HIDRA_TECNICO] == "Hidra"
    # Sin la regla de "sin skill", dbo.Forecast de Hidra (Skill ID NULL) se
    # descartaba entero y la comparación quedaba vacía.
    assert pd.FORECAST_SIN_SKILL[pd.CAMPANA_HIDRA_TECNICO] == (481, 60)


def test_todas_las_consultas_son_de_la_cola_tecnica():
    """HIDRA001 es el IVR y HIDRA004 no tiene agentes: cualquier consulta que se
    olvide el filtro de campaña mezcla millones de llamadas que no son demanda.
    La calibración recibe el skill por parámetro, como la de Voltara."""
    for nombre, sql in CONSULTAS:
        assert "481" in sql or "'HIDRAIN'" in sql or "IN :skills" in sql, nombre


def test_la_paciencia_se_cruza_con_el_nombre_del_skill():
    """La paciencia medida se aplica por nombre (`_resolver_skills`): el literal de
    la consulta tiene que ser el nombre del skill que siembra la migración."""
    for sql in (FUENTE.PACIENCIA, FUENTE.PACIENCIA_CURVA):
        assert re.search(r"'HIDRAIN'\s+AS skill", sql)
    if os.path.exists(MIGRACION):
        assert re.search(r"\(\s*1\s*,\s*481\s*,\s*'HIDRAIN'", _migracion())


def test_la_paciencia_mira_sólo_la_entrada_a_la_cola():
    """El detalle por campaña trae una fila por segmento; sólo `Entrante = 1` es la
    llamada que entró a la cola (medido: 12.328 de 12.474 filas en una semana)."""
    for sql in (FUENTE.PACIENCIA, FUENTE.PACIENCIA_CURVA):
        assert "d.Entrante = 1" in sql


def test_la_calibracion_mide_a_20_segundos_desde_el_detalle():
    """eficiencia_de_campana trae el nivel de servicio hecho pero a 30 s; el
    numerador de 20 s se cuenta llamada por llamada."""
    sql = FUENTE.CALIBRACION
    assert "EnCola <= 20" in sql
    assert "eficiencia_de_campana" not in sql
    assert "IN :skills" in sql


def test_el_tmo_va_ponderado_por_atendidas():
    for sql in (FUENTE.SERIE, FUENTE.CALIBRACION):
        assert "CAST(a.AHT AS float) * CAST(a.AgentesAtendidas AS float)" in sql


def test_el_usuario_se_deduplica_antes_del_cruce():
    """dbo.usuarios tiene logins reciclados entre personas: un JOIN directo
    duplicaría a quien tenga uno."""
    for sql in (FUENTE.CONECTADOS_POR_CAMPANA, FUENTE.RINDE_POR_ANTIGUEDAD,
                FUENTE.LLAMADAS_POR_ANTIGUEDAD):
        assert "MAX(nomina_id) AS nomina_id" in sql
        assert "GROUP BY usuario" in sql


# ------------------------------------------------ forecast del cliente, por hora

def _fila(fecha, hora, llamadas, skill=None):
    return {"Fecha": fecha, "Intervalo": hora, "skill_id": skill, "llamadas": llamadas}


def test_la_hora_sin_skill_se_reparte_en_dos_medias_horas():
    filas = [_fila(date(2026, 9, 17), time(9, 0), 210),
             _fila(date(2026, 9, 17), time(10, 0), 224)]
    salida = pd.forecast_por_intervalo(filas, (481, 60))
    assert salida == {
        (datetime(2026, 9, 17, 9, 0), 481): 105.0,
        (datetime(2026, 9, 17, 9, 30), 481): 105.0,
        (datetime(2026, 9, 17, 10, 0), 481): 112.0,
        (datetime(2026, 9, 17, 10, 30), 481): 112.0,
    }
    assert sum(salida.values()) == 434


def test_voltara_sigue_igual_por_media_hora_y_sin_skill_se_descarta():
    filas = [_fila(date(2026, 9, 17), time(9, 0), 40, skill=6),
             _fila(date(2026, 9, 17), time(9, 0), 5, skill=6),
             _fila(date(2026, 9, 17), time(9, 30), 7, skill=None)]
    assert pd.forecast_por_intervalo(filas) == {(datetime(2026, 9, 17, 9, 0), 6): 45.0}


def test_filas_sin_fecha_no_rompen():
    filas = [_fila(None, time(9, 0), 10), _fila(date(2026, 9, 17), None, 10)]
    assert pd.forecast_por_intervalo(filas, (481, 60)) == {}


# ------------------------------------------------------------------ salud

def test_la_salud_mira_los_acumuladores_de_mitrol_sólo_en_hidra():
    claves = [clave for clave, _, _ in salud.FUENTES]
    assert "acumuladores_mitrol" in claves
    assert salud._acumuladores_mitrol(None, pd.CAMPANA_VOLTARA, datetime.now()) is None
    # Y al revés: lo propio de Voltara no se le pide a Hidra.
    assert salud._informe_skills(None, pd.CAMPANA_HIDRA_TECNICO, datetime.now()) is None
    assert salud._demanda_total(None, pd.CAMPANA_HIDRA_TECNICO, datetime.now()) is None


def test_la_migracion_de_hidra_esta_registrada():
    archivos = [m["archivo"] for m in salud.REGISTRO_MIGRACIONES]
    assert "2026-09-22_planificador_hidra_tecnico.sql" in archivos


# ------------------------------------------------------------------ migración

def _migracion() -> str:
    with open(MIGRACION, encoding="utf-8") as f:
        return f.read()


requiere_migracion = pytest.mark.skipif(
    not os.path.exists(MIGRACION), reason="scripts/migrations no viaja por git")


@requiere_migracion
def test_la_siembra_es_idempotente():
    sql = _migracion()
    for tabla in ("Campana", "Pool", "Skill", "PoolOrigen", "Disponibilidad"):
        assert re.search(rf"IF NOT EXISTS \(SELECT 1 FROM planificacion\.{tabla}\b", sql), tabla


@requiere_migracion
def test_la_migracion_no_toca_voltara_ni_permisos():
    sql = _migracion()
    assert "CampanaID = 20" not in sql
    assert "RolePermissions" not in sql
    assert not re.search(r"\b(DELETE|DROP|TRUNCATE)\b", sql, re.I)


@requiere_migracion
def test_el_pool_toma_gente_solo_del_canal_telefonico():
    """Sub-campaña 57 (Hidra Canal Telefónico): la única que se loguea en HIDRAIN.
    La 55 (Hidra) son referentes (puesto 10, fuera de la malla) y la 161 es
    Hidra Comercial, que es otra operación."""
    sql = _migracion()
    bloque = sql[sql.index("INSERT INTO planificacion.PoolOrigen"):]
    bloque = bloque[:bloque.index(";")]
    assert re.search(r"@pool\s*,\s*57\b", bloque)
    assert "161" not in bloque and "168" not in bloque


@requiere_migracion
def test_sintaxis_de_la_migracion(engine):
    from conftest import _validar_parseonly
    for i, batch in enumerate(_batches(_migracion())):
        if batch.strip() and not batch.strip().upper().startswith("USE "):
            resultado = _validar_parseonly(engine, batch)
            assert resultado.ok, f"batch {i}: {resultado.error}"


# --------------------------------------- calendario propio, persistencia, intradía

from datetime import timedelta

from app import planificador as pl


def _historia(semanas: int, hasta: date, por_dia=None, valor=10.0):
    """Media hora a media hora, `valor` llamadas en cada una (o `por_dia(d)`)."""
    salida = {}
    d = hasta - timedelta(weeks=semanas)
    while d < hasta:
        v = por_dia(d) if por_dia else valor
        for i in range(48):
            salida[datetime.combine(d, time(0, 0)) + timedelta(minutes=30 * i)] = v
        d += timedelta(days=1)
    return salida


def test_el_calendario_por_defecto_no_cambia_nada():
    fer = {date(2026, 7, 9), date(2026, 7, 10)}
    assert pl.calendario_del_modelo(fer, {date(2026, 7, 10)}) == (fer, {}, {})


def test_feriado_como_sabado_salvo_navidad_y_año_nuevo():
    fer = {date(2026, 7, 9), date(2026, 12, 25), date(2027, 1, 1)}
    _, forzado, _ = pl.calendario_del_modelo(fer, (), feriado_como="sabado")
    assert forzado == {date(2026, 7, 9): 6}


def test_el_puente_deja_de_ser_feriado_y_lleva_su_factor():
    fer = {date(2026, 7, 9), date(2026, 7, 10)}
    feriados, _, factor = pl.calendario_del_modelo(fer, {date(2026, 7, 10)},
                                                   puente_factor=0.6)
    assert feriados == {date(2026, 7, 9)}
    assert factor == {date(2026, 7, 10): 0.6}


def test_un_feriado_de_hidra_se_pronostica_con_el_perfil_del_sabado():
    hoy = date(2026, 7, 6)                     # lunes
    # Sábados 6, domingos 2, hábiles 10.
    hist = _historia(12, hoy, por_dia=lambda d: {5: 6.0, 6: 2.0}.get(d.weekday(), 10.0))
    miercoles = date(2026, 7, 8)
    kw = dict(feriados=[miercoles], hoy=hoy, semanas_base=12, dias_nivel=28)
    domingo = pl.baseline_estacional(hist, [miercoles], **kw)
    sabado = pl.baseline_estacional(hist, [miercoles], feriado_como="sabado", **kw)
    assert sum(domingo.values()) == pytest.approx(2.0 * 48)
    assert sum(sabado.values()) == pytest.approx(6.0 * 48)


def test_el_puente_es_su_dia_habil_por_el_factor():
    hoy = date(2026, 7, 6)
    hist = _historia(12, hoy, por_dia=lambda d: 2.0 if d.weekday() >= 5 else 10.0)
    viernes = date(2026, 7, 10)
    salida = pl.baseline_estacional(hist, [viernes], feriados=[viernes], hoy=hoy,
                                    semanas_base=12, puentes=[viernes], puente_factor=0.6)
    assert sum(salida.values()) == pytest.approx(10.0 * 48 * 0.6)


def test_factor_de_persistencia_por_horizonte():
    r = 0.4
    assert pl.factor_de_persistencia(r, 0, 0.5, 0.3, 7) == pytest.approx(math.exp(0.2))
    assert pl.factor_de_persistencia(r, 3, 0.5, 0.3, 7) == pytest.approx(math.exp(0.12))
    assert pl.factor_de_persistencia(r, 7, 0.5, 0.3, 7) == 1.0
    assert pl.factor_de_persistencia(r, -1, 0.5, 0.3, 7) == 1.0
    # El residuo se topea: un día x10 no se arrastra entero.
    assert pl.factor_de_persistencia(math.log(10), 0, 1.0, 0, 1) == pytest.approx(math.exp(0.7))


def test_sin_pesos_la_persistencia_no_toca_nada():
    hoy = date(2026, 9, 21)
    hist = _historia(10, hoy)
    dias = [hoy, hoy + timedelta(days=1)]
    kw = dict(hoy=hoy, semanas_base=10)
    assert (pl.baseline_estacional(hist, dias, persistencia=(0.0, 0.0, 7), **kw)
            == pl.baseline_estacional(hist, dias, **kw))


def test_la_persistencia_arrastra_el_desvio_de_ayer():
    hoy = date(2026, 9, 21)
    ayer = hoy - timedelta(days=1)
    hist = _historia(10, hoy, por_dia=lambda d: 20.0 if d == ayer else 10.0)
    dias = [hoy, hoy + timedelta(days=1), hoy + timedelta(days=3)]
    kw = dict(hoy=hoy, semanas_base=10, dias_nivel=28)
    base = pl.baseline_estacional(hist, dias, **kw)
    con = pl.baseline_estacional(hist, dias, persistencia=(0.5, 0.3, 2), **kw)

    def dia(p, d):
        return sum(v for m, v in p.items() if m.date() == d)
    assert dia(con, hoy) > dia(base, hoy) * 1.2       # ayer vino ~x2
    assert dia(base, dias[1]) < dia(con, dias[1]) < dia(con, hoy)
    assert dia(con, dias[2]) == pytest.approx(dia(base, dias[2]))   # fuera de los 2 días


def test_reescalar_intradia_usa_los_cerrados_menos_el_ultimo():
    hoy = date(2026, 9, 22)
    pron = {datetime.combine(hoy, time(h, m)): 10.0 for h in range(24) for m in (0, 30)}
    real = {datetime.combine(hoy, time(h, m)): 20.0 for h in range(8, 12) for m in (0, 30)}
    ahora = datetime.combine(hoy, time(12, 10))
    g, nuevos = pl.reescalar_intradia(pron, real, ahora)
    # 8:00 a 11:00 cerrados = 7 medias horas (11:30 queda afuera por ser la última)
    assert g == pytest.approx((140 + 20) / (70 + 20))
    assert min(nuevos) == datetime.combine(hoy, time(12, 0))
    assert nuevos[datetime.combine(hoy, time(15, 0))] == pytest.approx(10.0 * g)


def test_reescalar_intradia_sin_nada_cerrado_no_hace_nada():
    hoy = date(2026, 9, 22)
    pron = {datetime.combine(hoy, time(8, 0)): 10.0}
    assert pl.reescalar_intradia(pron, {}, datetime.combine(hoy, time(8, 10))) == (None, {})


def test_el_intradia_no_lee_como_cerradas_las_medias_horas_en_cero_sin_cargar(monkeypatch):
    """El informe de Voltara trae el día entero desde temprano, en cero hasta que
    carga. Esos ceros no son medias horas sin llamadas: se corta en la última con
    llamadas de la campaña, y esa también se descarta porque viene a medias."""
    from types import SimpleNamespace
    from app import planificador_servicio as serv
    hoy = date(2026, 9, 24)
    real = {}
    for h in range(24):
        for m in (0, 30):
            ll = 20.0 if (h, m) <= (12, 30) else 0.0   # cargado hasta 12:30
            real[(datetime.combine(hoy, time(h, m)), 6)] = (ll, 300.0)
    monkeypatch.setattr(pd, "serie_por_skill", lambda conn, cid, a, b: real)
    filas = [{"skill": 6, "momento": datetime.combine(hoy, time(h, m)), "acme": 10.0,
              "total": 20.0} for h in range(24) for m in (0, 30)]
    cfg = SimpleNamespace(intradia_desde_hora=14, intervalo_min=30,
                          skills=[SimpleNamespace(skill_id=6, nombre="EMERGENCIAS")])
    r = serv._reescalar_hoy(None, cfg, 20, {}, filas, ahora=datetime.combine(hoy, time(14, 5)))
    # 0:00 a 12:00 = 25 medias horas de 20 contra 10 pronosticadas (12:30 afuera)
    g = (500 + pl.PRIOR_INTRADIA) / (250 + pl.PRIOR_INTRADIA)
    assert r["factores"]["EMERGENCIAS"] == pytest.approx(round(min(g, pl.TOPE_INTRADIA[1]), 3))
    tarde = next(f for f in filas if f["momento"] == datetime.combine(hoy, time(15, 0)))
    g = min(g, pl.TOPE_INTRADIA[1])
    assert tarde["acme"] == pytest.approx(round(10.0 * g, 2))
    assert tarde["total"] == pytest.approx(round(20.0 * g, 2))


def test_los_puentes_salen_de_la_libreria():
    assert date(2026, 7, 10) in pd.puentes(date(2026, 1, 1), date(2026, 12, 31))
    assert date(2026, 7, 9) not in pd.puentes(date(2026, 1, 1), date(2026, 12, 31))


def test_la_persistencia_saltea_el_feriado_de_ayer():
    """El desvío de un feriado es del calendario, no del nivel: no se arrastra.
    Se toma el último día normal y hoy recibe el peso de «los días siguientes»."""
    hoy = date(2026, 7, 13)                    # lunes
    feriado = hoy - timedelta(days=1)          # domingo feriado con x3
    hist = _historia(10, hoy, por_dia=lambda d: 30.0 if d == feriado else 10.0)
    kw = dict(hoy=hoy, semanas_base=10, dias_nivel=28)
    base = pl.baseline_estacional(hist, [hoy], feriados=[feriado], **kw)
    con = pl.baseline_estacional(hist, [hoy], feriados=[feriado],
                                 persistencia=(0.5, 0.3, 7), **kw)
    # El sábado anterior fue normal: no hay desvío que arrastrar.
    assert sum(con.values()) == pytest.approx(sum(base.values()), rel=0.02)
    # Sin marcarlo feriado, el x3 sí se arrastra.
    sin_marcar = pl.baseline_estacional(hist, [hoy], persistencia=(0.5, 0.3, 7), **kw)
    assert sum(sin_marcar.values()) > sum(pl.baseline_estacional(hist, [hoy], **kw).values()) * 1.2


def test_el_clima_apagado_no_figura_como_faltante(monkeypatch):
    monkeypatch.setattr(pd, "_tiene_tabla", lambda conn, t: True)
    monkeypatch.setattr(pd, "clima_de_la_campana",
                        lambda conn, c: {"lat": -34.6, "lon": -58.4, "activo": False})
    r = salud._clima(None, pd.CAMPANA_HIDRA_TECNICO, datetime.now())
    assert r["estado"] == "no_aplica"
