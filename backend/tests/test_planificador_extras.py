"""Tests de extras habituales: función pura, clasificación de días y cache.

Verifica:
  1. `tipo_de_dia_extras`: domingos y feriados agrupados, sábados y hábiles separados.
  2. `extras_habituales`: función pura donde cada día del tipo cuenta en el divisor
     (un intervalo sin diferencia cuenta 0).
  3. Caso medido de producción: noches de fin de semana con +1,5 personas sobre la malla.
  4. Integración en `plan_vigente` (best-effort, sin romper el plan si falla) y cache en memoria.

Offline: no toca BD ni red.
"""
from datetime import date, datetime, time, timedelta

import pytest

from app import planificador_datos as pdatos
from app import planificador_extras as pextras
from app import planificador_servicio as servicio


# ------------------------------------------------------------------ helpers

def _momentos(dia: date, horas: list[int]):
    for h in horas:
        for m in (0, 30):
            yield datetime.combine(dia, time(h, m))


# ----------------------------------------------------------- tipo_de_dia_extras

def test_tipo_de_dia_extras_clasifica_correctamente():
    # Lunes feriado nacional (ej. 17 de agosto)
    feriados = {date(2026, 8, 17)}

    # Lunes normal -> habil
    assert pextras.tipo_de_dia_extras(date(2026, 8, 10), feriados) == "habil"
    # Viernes normal -> habil
    assert pextras.tipo_de_dia_extras(date(2026, 8, 14), feriados) == "habil"
    # Sábado normal -> sabado
    assert pextras.tipo_de_dia_extras(date(2026, 8, 15), feriados) == "sabado"
    # Domingo normal -> domingo_feriado
    assert pextras.tipo_de_dia_extras(date(2026, 8, 16), feriados) == "domingo_feriado"
    # Lunes feriado -> domingo_feriado
    assert pextras.tipo_de_dia_extras(date(2026, 8, 17), feriados) == "domingo_feriado"
    # Sábado feriado -> domingo_feriado (el feriado manda sobre el sábado)
    feriados_con_sabado = {date(2026, 6, 20)}
    assert pextras.tipo_de_dia_extras(date(2026, 6, 20), feriados_con_sabado) == "domingo_feriado"
    # Soporta también secuencia de strings ISO
    assert pextras.tipo_de_dia_extras(date(2026, 8, 17), ["2026-08-17"]) == "domingo_feriado"


# ------------------------------------------------------------ función pura

def test_extras_habituales_neto_cero_cuando_registro_igual_a_malla():
    """Si nadie hizo horas extra ni hubo refuerzos, el neto registro - malla es 0."""
    dias = [date(2026, 8, 10) + timedelta(days=i) for i in range(14)]
    registro = {}
    malla = {}
    for d in dias:
        for dt in _momentos(d, [9, 10, 11]):
            registro[dt] = 10
            malla[dt] = 10

    resultado = pextras.extras_habituales(registro, malla, tipo_de_dia=lambda d: pextras.tipo_de_dia_extras(d))
    assert len(resultado) > 0
    # En todos los intervalos el promedio debe ser exactamente 0.0
    for fila in resultado:
        assert fila["personas"] == 0.0
        assert fila["tipo_dia"] in pextras.TIPOS_DE_DIA
        assert ":" in fila["hora"]


def test_extras_habituales_promedio_con_dias_en_cero():
    """Cada día del tipo cuenta: un intervalo sin diferencia suma 0 y cuenta en el divisor."""
    sabado_1 = date(2026, 8, 15)
    sabado_2 = date(2026, 8, 22)
    dias = [sabado_1, sabado_2]

    registro = {}
    malla = {}

    # En el sábado 1 a las 19:00 hubo +2 extras (10 en registro vs 8 en malla)
    dt1 = datetime.combine(sabado_1, time(19, 0))
    registro[dt1] = 10
    malla[dt1] = 8

    # En el sábado 2 a las 19:00 no hubo extras (8 en registro vs 8 en malla)
    dt2 = datetime.combine(sabado_2, time(19, 0))
    registro[dt2] = 8
    malla[dt2] = 8

    resultado = pextras.extras_habituales(
        registro, malla,
        tipo_de_dia=lambda d: pextras.tipo_de_dia_extras(d),
        dias=dias,
    )

    por_clave = {(f["tipo_dia"], f["hora"]): f["personas"] for f in resultado}
    # (2 + 0) / 2 sábados = 1.0 persona
    assert por_clave[("sabado", "19:00")] == 1.0
    # A las 20:00 no hubo datos en ninguno de los dos sábados: 0.0
    assert por_clave[("sabado", "20:00")] == 0.0


def test_extras_habituales_caso_medido_produccion():
    """Recrea el caso medido: 8 sábados donde en 6 hubo +2 extras de 18 a 22h y en 2 no hubo.

    Promedio = 12 / 8 = +1,5 personas.
    """
    sabados = [date(2026, 7, 4) + timedelta(weeks=i) for i in range(8)]
    registro = {}
    malla = {}

    for i, sab in enumerate(sabados):
        for h in range(18, 22):
            dt = datetime.combine(sab, time(h, 0))
            malla[dt] = 8
            # 6 sábados con extras (+2 personas = 10), 2 sábados normales (8)
            if i < 6:
                registro[dt] = 10
            else:
                registro[dt] = 8

    resultado = pextras.extras_habituales(
        registro, malla,
        tipo_de_dia=lambda d: pextras.tipo_de_dia_extras(d),
        dias=sabados,
    )

    por_clave = {(f["tipo_dia"], f["hora"]): f["personas"] for f in resultado}
    for h in range(18, 22):
        assert por_clave[("sabado", f"{h:02d}:00")] == 1.5


def test_extras_habituales_sin_dias_de_un_tipo():
    """Si no hay domingos en los días analizados, retorna 0.0 personas sin fallar."""
    habil = date(2026, 8, 10)
    resultado = pextras.extras_habituales(
        {}, {},
        tipo_de_dia=lambda d: "habil",
        dias=[habil],
    )
    domingos = [f for f in resultado if f["tipo_dia"] == "domingo_feriado"]
    assert len(domingos) > 0
    assert all(f["personas"] == 0.0 for f in domingos)


def test_extras_habituales_acepta_set_de_feriados():
    """La función pura debe aceptar un set de feriados directamente como tipo_de_dia."""
    feriados = {date(2026, 8, 17)}
    dias = [date(2026, 8, 17)]
    dt = datetime.combine(date(2026, 8, 17), time(10, 0))
    registro = {dt: 12}
    malla = {dt: 10}

    resultado = pextras.extras_habituales(registro, malla, tipo_de_dia=feriados, dias=dias)
    por_clave = {(f["tipo_dia"], f["hora"]): f["personas"] for f in resultado}
    # Lunes feriado clasifica como domingo_feriado
    assert por_clave[("domingo_feriado", "10:00")] == 2.0


# ------------------------------------------------- integración y cache en servicio

class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeEngine:
    def connect(self):
        return _FakeConn()


def test_extras_del_plan_cachea_resultado(monkeypatch):
    """Verifica que _extras_del_plan almacene el resultado en cache por (campaña, hoy)."""
    servicio._CACHE_EXTRAS.clear()
    engine = _FakeEngine()
    campana_id = 20
    hoy = date(2026, 9, 15)

    llamadas_dotacion = 0

    def fake_schema(conn):
        return True

    def fake_config(conn, cid):
        class _Pool:
            pool_id = 1
        class _Cfg:
            pools = {1: _Pool()}
            intervalo_min = 30
        return _Cfg()

    def fake_feriados(conn, d, h, *_):
        return []

    def fake_dotacion_real(conn, pool_id, d, h, i=30):
        nonlocal llamadas_dotacion
        llamadas_dotacion += 1
        return {}

    def fake_dotacion_planificada(conn, pool_id, d, h, i=30):
        return {}

    monkeypatch.setattr(pdatos, "schema_disponible", fake_schema)
    monkeypatch.setattr(pdatos, "cargar_config", fake_config)
    monkeypatch.setattr(pdatos, "feriados", fake_feriados)
    monkeypatch.setattr(pdatos, "dotacion_real", fake_dotacion_real)
    monkeypatch.setattr(pdatos, "dotacion_planificada", fake_dotacion_planificada)

    # Primera llamada: consulta y calcula
    res1 = servicio._extras_del_plan(engine, campana_id, hoy)
    assert llamadas_dotacion == 1
    assert "semanas" in res1
    assert 1 in res1["por_pool"]

    # Segunda llamada: viene de cache, llamadas_dotacion sigue siendo 1
    res2 = servicio._extras_del_plan(engine, campana_id, hoy)
    assert llamadas_dotacion == 1
    assert res1 is res2


def test_extras_del_plan_falla_best_effort(monkeypatch):
    """Si la lectura de dotación falla, no rompe y devuelve {'motivo': ...}."""
    servicio._CACHE_EXTRAS.clear()
    engine = _FakeEngine()

    def fake_schema(conn):
        raise RuntimeError("Error simulado de base de datos")

    monkeypatch.setattr(pdatos, "schema_disponible", fake_schema)

    res = servicio._extras_del_plan(engine, 20, date(2026, 9, 15))
    assert "motivo" in res
    assert "Error simulado" in res["motivo"]


def test_plan_vigente_incluye_extras_habituales(monkeypatch):
    """Verifica que plan_vigente incluya extras_habituales en el payload que devuelve a la pantalla."""
    engine = _FakeEngine()
    campana_id = 20
    hoy = date.today()

    monkeypatch.setattr(pdatos, "schema_disponible", lambda conn: True)
    monkeypatch.setattr(pdatos, "corrida_vigente",
                        lambda conn, cid, horiz="operativo": {"CorridaID": 999, "Desde": hoy, "Hasta": hoy + timedelta(days=7)})
    monkeypatch.setattr(pdatos, "leer_requerimiento", lambda conn, cid: [])
    monkeypatch.setattr(pdatos, "leer_pronostico", lambda conn, cid: [])
    monkeypatch.setattr(pdatos, "hay_demanda_total", lambda conn, cid: True)
    monkeypatch.setattr(servicio, "_cotejo_del_plan",
                        lambda conn, cid, d, h, pool_ids=None: {"real": [], "cliente": [], "conectados": [], "real_hasta": None})
    monkeypatch.setattr(servicio, "_extras_del_plan",
                        lambda eng, cid, hoy_: {"semanas": 8, "por_pool": {1: []}})

    res = servicio.plan_vigente(engine, campana_id)
    assert "extras_habituales" in res
    assert res["extras_habituales"]["semanas"] == 8
    assert 1 in res["extras_habituales"]["por_pool"]
