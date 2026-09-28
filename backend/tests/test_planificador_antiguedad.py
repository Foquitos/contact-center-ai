"""Rinde de la gente nueva (planificador_antiguedad) y cómo entra al plan.

La mayoría es lógica pura, sin base. Las dos consultas nuevas a tablas que ya
existen se validan con `describe` (resuelve nombres sin ejecutar), igual que el
resto de la capa de datos.
"""
import dataclasses
import re
from datetime import date, datetime, timedelta

import pytest

from app import planificador as pl
from app import planificador_antiguedad as pant
from app import planificador_datos as pdatos
from app import planificador_servicio as servicio
from app.routers import planificador as router

ANTIGUO = pant.DIAS_ANTIGUO


# ------------------------------------------------------------------ tramos

def test_tramo_de_cada_antiguedad():
    assert pant.tramo_de(None) == ANTIGUO          # sin fecha de piso: cuenta entero
    assert pant.tramo_de(-3) == 0                   # atiende antes del pase a piso
    assert pant.tramo_de(0) == 0
    assert pant.tramo_de(13) == 0
    assert pant.tramo_de(14) == 14
    assert pant.tramo_de(27) == 14
    assert pant.tramo_de(28) == 28
    assert pant.tramo_de(55) == 28
    assert pant.tramo_de(56) == ANTIGUO
    assert pant.tramo_de(900) == ANTIGUO


# ------------------------------------------------------------------ rinde

def test_el_rinde_se_compara_dentro_de_cada_cola():
    """Un nuevo que sólo atiende la cola rápida tiene un TMO crudo más bajo que el
    promedio de los antiguos y parecería rendir más. Comparado contra el antiguo de
    su misma cola, rinde menos."""
    filas = [
        {"dias": 400, "skill_id": 1, "llamadas": 1000, "segundos": 100_000},   # 100 s
        {"dias": 400, "skill_id": 2, "llamadas": 1000, "segundos": 300_000},   # 300 s
        {"dias": 3, "skill_id": 1, "llamadas": 1_000_000, "segundos": 150_000_000},
    ]
    rinde = pant.medir_rinde(filas)
    assert rinde[0]["crudo"] == pytest.approx(100 / 150, abs=1e-4)
    assert rinde[0]["rinde"] == pytest.approx(100 / 150, abs=1e-3)
    assert rinde[ANTIGUO]["rinde"] == 1.0


def test_pocas_llamadas_encogen_y_una_cola_sin_antiguos_no_cuenta():
    filas = [
        {"dias": 100, "skill_id": 1, "llamadas": 100, "segundos": 10_000},     # 100 s
        {"dias": 20, "skill_id": 1, "llamadas": 1000, "segundos": 200_000},    # 200 s: crudo 0,5
        {"dias": 20, "skill_id": 9, "llamadas": 5000, "segundos": 5_000},      # cola sin antiguos
    ]
    rinde = pant.medir_rinde(filas, llamadas_encogimiento=1000)
    assert rinde[14]["crudo"] == pytest.approx(0.5)
    assert rinde[14]["llamadas"] == 1000
    assert rinde[14]["rinde"] == pytest.approx(0.75)   # a mitad de camino: 1000/(1000+1000)
    assert 0 not in rinde and 28 not in rinde          # sin datos no se inventa un tramo


def test_el_rinde_queda_acotado():
    filas = [
        {"dias": 100, "skill_id": 1, "llamadas": 10_000, "segundos": 1_000_000},
        {"dias": 1, "skill_id": 1, "llamadas": 10_000_000, "segundos": 100_000_000_000},
    ]
    assert pant.medir_rinde(filas)[0]["rinde"] == pant.RINDE_MINIMO


# ------------------------------------------------------------ normalización

def test_una_malla_con_la_mezcla_de_la_historia_suma_lo_mismo_en_equivalentes():
    """La propiedad que evita contar dos veces: el TMO del pronóstico ya trae a la
    gente nueva de la historia. Con horas en la misma proporción que esa historia,
    los equivalentes suman lo mismo que las personas."""
    rinde = {0: 0.6, 14: 0.75, 28: 0.9, ANTIGUO: 1.0}
    llamadas = {0: 800, 14: 900, 28: 2000, ANTIGUO: 20_000}
    factores = pant.normalizar(rinde, llamadas)
    # Horas de cada tramo en la historia: llamadas × TMO, y el TMO va como 1/rinde.
    horas = {t: llamadas[t] / rinde[t] for t in llamadas}
    assert sum(horas[t] * factores[t] for t in horas) == pytest.approx(sum(horas.values()))
    assert factores[0] < factores[14] < factores[28] < factores[ANTIGUO]
    # El antiguo rinde algo más que el promedio que ya trae el TMO.
    assert factores[ANTIGUO] > 1


def test_sin_historia_el_peso_es_el_rinde_y_un_tramo_sin_rinde_vale_como_antiguo():
    factores = pant.normalizar({0: 0.7}, {})
    assert factores[0] == pytest.approx(0.7)
    assert factores[14] == factores[28] == factores[ANTIGUO] == 1.0


def test_armar_curva_guarda_todos_los_tramos():
    rinde = {0: {"rinde": 0.6, "llamadas": 500}, ANTIGUO: {"rinde": 1.0, "llamadas": 9000}}
    filas = pant.armar_curva(rinde, {0: 100, ANTIGUO: 900})
    assert [f["dia_desde"] for f in filas] == [0, 14, 28, ANTIGUO]
    assert [f["dia_hasta"] for f in filas] == [14, 28, 56, None]
    assert sum(f["participacion"] for f in filas) == pytest.approx(1.0)
    assert filas[1]["rinde"] == 1.0 and filas[1]["llamadas"] == 0
    curva = pant.como_curva(filas)
    assert curva[0] < curva[ANTIGUO]
    assert curva[14] == curva[28] == curva[ANTIGUO]


# -------------------------------------------------------------- equivalentes

def test_contar_equivalentes_cuenta_a_cada_persona_una_vez_con_su_peso():
    curva = {0: 0.5, 14: 0.8, 28: 0.9, ANTIGUO: 1.1}
    ocho = datetime(2026, 9, 20, 8, 0)
    turnos = [
        (1, ocho, ocho + timedelta(hours=1), date(2026, 9, 18)),          # 2 días de piso
        (1, ocho, ocho + timedelta(minutes=30), date(2026, 9, 18)),       # fila que se pisa
        (2, ocho, ocho + timedelta(hours=1), datetime(2025, 1, 1, 0, 0)),  # antiguo
        (3, ocho, ocho + timedelta(hours=1), None),                        # sin fecha
    ]
    eq = pant.contar_equivalentes(turnos, curva, 30)
    assert eq[ocho] == pytest.approx(0.5 + 1.1 + 1.1)
    assert eq[ocho + timedelta(minutes=30)] == pytest.approx(2.7)
    assert ocho + timedelta(hours=1) not in eq


def test_sin_curva_cada_persona_vale_uno():
    ocho = datetime(2026, 9, 20, 8, 0)
    eq = pant.contar_equivalentes([(1, ocho, ocho + timedelta(minutes=30), date(2026, 9, 19))],
                                  {}, 30)
    assert eq[ocho] == 1.0


def _requerimiento(a_planificar: int, citados, equivalentes=None) -> pl.RequerimientoPool:
    return pl.RequerimientoPool(
        pool_id=1, momento=datetime(2026, 9, 20, 8), llamadas=50, tmo_seg=200,
        trafico=5.5, operadores_en_linea=8, operadores_presentes=9,
        operadores_a_planificar=a_planificar, nds_contractual=0.8,
        nds_sobre_atendidas=0.8, nds_sobre_entrantes=0.78, abandono=0.01,
        ocupacion=0.7, asa_seg=12, nivel_atencion_b=0.95, motivo="nivel de servicio",
        disponibilidad=0.85, planificados=citados, planificados_equivalentes=equivalentes)


def test_la_brecha_va_contra_los_equivalentes_cuando_hay_curva():
    assert _requerimiento(10, 10).brecha == 0
    assert _requerimiento(10, 10, 8.6).brecha == -1      # redondea 8,6 a 9
    assert _requerimiento(10, 10, 10.5).brecha == 1
    assert _requerimiento(10, None, 8.0).brecha is None  # sin malla no hay brecha
    r = _requerimiento(10, 10, 7.2)
    r.refuerzo_disponible = 2
    assert r.refuerzo_cubre == 2 and r.faltante_neto == 1


# ------------------------------------------------------------------- datos

def _where(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.split("WHERE", 1)[1]).strip()


def test_la_malla_con_piso_es_la_misma_poblacion_que_la_malla():
    """Si citados y equivalentes salieran de poblaciones distintas, la diferencia
    no sería el rinde."""
    assert _where(pdatos._PAYROLL_TURNOS_CON_PISO) == _where(pdatos._PAYROLL_TURNOS)
    assert "LEFT JOIN dbo.nomina n ON n.id = o.legajo_id" in pdatos._PAYROLL_TURNOS_CON_PISO
    assert "n.fecha_piso" in pdatos._PAYROLL_TURNOS_CON_PISO


def test_el_rinde_se_mide_por_cola_y_sin_homonimos():
    sql = pdatos.FuenteVoltara.RINDE_POR_ANTIGUEDAD
    assert "HAVING COUNT(*) = 1" in sql
    assert "t.[Skill ID]" in sql
    assert "GROUP BY skill_id, dias" in sql
    # El tope de días viaja como parámetro: los tramos viven sólo en Python.
    assert ":antiguo" in sql and "56" not in sql


def _literales(sql: str) -> str:
    sql = sql.replace(":desde", "'2026-08-01'").replace(":hasta", "'2026-09-01'")
    return sql.replace(":antiguo", "56")


@pytest.mark.parametrize("nombre", ["RINDE_POR_ANTIGUEDAD", "LLAMADAS_POR_ANTIGUEDAD"])
def test_las_consultas_de_antiguedad_resuelven_nombres(validar_sql, nombre):
    resultado = validar_sql(_literales(getattr(pdatos.FuenteVoltara, nombre)))
    assert resultado.ok, f"{nombre}: {resultado.error}"


def test_campanas_con_fuente_deja_afuera_inactivas_y_sin_fuente(monkeypatch):
    monkeypatch.setattr(pdatos, "campanas_del_planificador", lambda conn: [
        {"campana_id": 20, "activa": True, "con_fuente": True},
        {"campana_id": 24, "activa": True, "con_fuente": False},
        {"campana_id": 5, "activa": False, "con_fuente": True},
    ])
    assert pdatos.campanas_con_fuente(None) == [20]


def test_sin_la_migracion_no_hay_curva_y_guardar_pide_la_migracion(monkeypatch):
    monkeypatch.setattr(pdatos, "_tiene_tabla", lambda conn, tabla: False)
    assert pdatos.leer_curva_antiguedad(None, 20) == []
    with pytest.raises(pdatos.MigracionPendiente):
        pdatos.guardar_curva_antiguedad(None, 20, [{"dia_desde": 0}])


# ------------------------------------------------------------- laboratorio

def test_validar_ajustes_de_modelo():
    assert servicio.validar_ajustes_de_modelo(None) == {}
    assert servicio.validar_ajustes_de_modelo(
        {"semanas_base": "26", "dias_nivel": None}) == {"semanas_base": 26}
    assert servicio.validar_ajustes_de_modelo(
        {"nivel_por_tipo_de_dia": False}) == {"nivel_por_tipo_de_dia": False}
    with pytest.raises(ValueError):
        servicio.validar_ajustes_de_modelo({"semanas_base": 2})
    with pytest.raises(ValueError):
        servicio.validar_ajustes_de_modelo({"reparto_deriva_tope": "mucho"})
    # Un parámetro de dimensionamiento no es del laboratorio: eso es Escenarios.
    with pytest.raises(ValueError):
        servicio.validar_ajustes_de_modelo({"max_ocupacion": 0.9})


def test_los_ajustes_del_laboratorio_son_campos_de_la_config():
    campos = {f.name for f in dataclasses.fields(pl.CampanaCfg)}
    assert set(servicio.AJUSTES_DE_MODELO) <= campos


# ---------------------------------------------------------------- campañas

def test_el_selector_respeta_el_alcance_por_empresa():
    filas = [{"campana_id": 20, "empresa_id": 11}, {"campana_id": 24, "empresa_id": 14},
             {"campana_id": 99, "empresa_id": None}]
    assert router._campanas_visibles(filas, None) == filas
    assert [c["campana_id"] for c in router._campanas_visibles(filas, {11})] == [20]
    assert router._campanas_visibles(filas, set()) == []
