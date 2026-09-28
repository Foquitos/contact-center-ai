"""Tests de la pantalla del plan: el pronóstico y las dos series con las que se
lo lee —lo que pronosticó el cliente y lo que efectivamente entró—.

Lo que se defiende acá es una sola cosa, y es la que hace que el gráfico se
pueda leer: la ventana del real tiene que terminar donde termina el dato. Un
intervalo en curso viene a medias y dibuja un desplome; un intervalo futuro sin
dato no es un cero. Las dos confusiones llevan a la misma decisión equivocada
—"sobra gente, sacá el refuerzo"— así que las dos tienen test.

Offline: no toca ni BD ni red.

Correr: pytest tests/test_planificador_plan.py -m "not tokens"
"""
from datetime import date, datetime, time, timedelta

import pytest

from app import planificador_datos as pdatos
from app import planificador_servicio as servicio

SKILL = 6
OTRO = 4


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Engine:
    def connect(self):
        return _Conn()


def _momentos(dia: date, desde_hora: int, hasta_hora: int):
    """Las medias horas de un día entre dos horas, `hasta` exclusivo."""
    m = datetime.combine(dia, time(desde_hora, 0))
    fin = datetime.combine(dia, time(0, 0)) + timedelta(hours=hasta_hora)
    while m < fin:
        yield m
        m += timedelta(minutes=30)


@pytest.fixture
def enchufado(monkeypatch):
    """Reemplaza la capa de datos. Devuelve una función que arma el escenario."""
    def _armar(serie=None, cliente=None, hay_cliente=True):
        monkeypatch.setattr(pdatos, "serie_por_skill",
                            lambda c, i, a, b: dict(serie or {}))
        monkeypatch.setattr(pdatos, "hay_forecast_en_produccion",
                            lambda c, i: hay_cliente)
        monkeypatch.setattr(pdatos, "forecast_en_produccion",
                            lambda c, i, a, b: dict(cliente or {}))
    return _armar


# ------------------------------------------------ dónde termina la curva real

def test_el_intervalo_en_curso_de_hoy_no_entra_en_el_real(enchufado):
    """El último con datos está a medias: incluirlo dibuja un desplome justo en
    la lectura que alguien está mirando para decidir si refuerza el turno."""
    hoy = date.today()
    serie = {(m, SKILL): (100.0, 300.0) for m in _momentos(hoy, 8, 12)}
    enchufado(serie=serie)

    r = servicio._cotejo_del_plan(None, 20, hoy, hoy + timedelta(days=1))

    ultimo_con_datos = max(m for (m, _) in serie)
    assert r["real_hasta"] == ultimo_con_datos - timedelta(minutes=30)
    assert all(f["Intervalo"] != ultimo_con_datos for f in r["real"])


def test_en_una_corrida_vieja_no_se_tira_el_ultimo_intervalo(enchufado):
    """Si el último dato es de anteayer ya cerró hace rato. Descartarlo sería
    perder un dato bueno por aplicar una regla pensada para hoy."""
    ayer = date.today() - timedelta(days=2)
    serie = {(m, SKILL): (100.0, 300.0) for m in _momentos(ayer, 8, 12)}
    enchufado(serie=serie)

    r = servicio._cotejo_del_plan(None, 20, ayer, date.today())

    assert r["real_hasta"] == max(m for (m, _) in serie)
    assert len(r["real"]) == len(serie)


def test_el_real_no_manda_los_ceros_pero_si_hasta_donde_llega(enchufado):
    """Las filas traen sólo los intervalos con llamadas —de madrugada hay medias
    horas en cero— y `real_hasta` dice hasta dónde hay dato. Con las dos cosas la
    pantalla puede rellenar el cero adentro de la ventana y dejar hueco afuera,
    que es la diferencia entre "entraron cero" y "todavía no pasó"."""
    hoy = date.today()
    serie = {(m, SKILL): (0.0, 0.0) for m in _momentos(hoy, 0, 8)}
    serie.update({(m, SKILL): (80.0, 300.0) for m in _momentos(hoy, 8, 12)})
    enchufado(serie=serie)

    r = servicio._cotejo_del_plan(None, 20, hoy, hoy + timedelta(days=1))

    assert all(f["Llamadas"] > 0 for f in r["real"])
    assert r["real_hasta"] == datetime.combine(hoy, time(11, 0))
    # La madrugada en cero queda adentro de la ventana, no afuera.
    assert r["real_hasta"] > datetime.combine(hoy, time(8, 0))


def test_sin_ninguna_llamada_todavia_no_hay_ventana(enchufado):
    hoy = date.today()
    enchufado(serie={(m, SKILL): (0.0, 0.0) for m in _momentos(hoy, 0, 6)})

    r = servicio._cotejo_del_plan(None, 20, hoy, hoy + timedelta(days=1))

    assert r["real_hasta"] is None
    assert r["real"] == []


# ------------------------------------------------- el pronóstico del cliente

def test_el_cliente_viene_por_skill_para_poder_filtrar_por_pool(enchufado):
    """La pantalla suma por pool, así que la serie tiene que llegar abierta por
    skill. Sumada de antemano no se podría filtrar."""
    hoy = date.today()
    enchufado(serie={},
              cliente={(m, sk): 50.0
                       for m in _momentos(hoy, 8, 10) for sk in (SKILL, OTRO)})

    r = servicio._cotejo_del_plan(None, 20, hoy, hoy + timedelta(days=1))

    assert {f["SkillID"] for f in r["cliente"]} == {SKILL, OTRO}


def test_sin_forecast_del_cliente_cargado_la_serie_viene_vacia(enchufado):
    hoy = date.today()
    enchufado(serie={}, hay_cliente=False)

    r = servicio._cotejo_del_plan(None, 20, hoy, hoy + timedelta(days=1))

    assert r["cliente"] == []


# ----------------------------------------------------- son referencias, no el plan

def test_si_falla_el_forecast_del_cliente_el_plan_se_muestra_igual(monkeypatch):
    hoy = date.today()
    serie = {(m, SKILL): (10.0, 300.0) for m in _momentos(hoy, 8, 10)}
    monkeypatch.setattr(pdatos, "serie_por_skill", lambda c, i, a, b: serie)
    monkeypatch.setattr(pdatos, "hay_forecast_en_produccion", lambda c, i: True)
    monkeypatch.setattr(pdatos, "forecast_en_produccion",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    r = servicio._cotejo_del_plan(None, 20, hoy, hoy + timedelta(days=1))

    assert r["cliente"] == []
    assert r["real"]


def test_si_falla_el_real_el_plan_se_muestra_igual(monkeypatch):
    hoy = date.today()
    monkeypatch.setattr(pdatos, "serie_por_skill",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(pdatos, "hay_forecast_en_produccion", lambda c, i: True)
    monkeypatch.setattr(pdatos, "forecast_en_produccion",
                        lambda c, i, a, b: {(datetime.combine(hoy, time(9, 0)), SKILL): 7.0})

    r = servicio._cotejo_del_plan(None, 20, hoy, hoy + timedelta(days=1))

    assert r["real"] == [] and r["real_hasta"] is None
    assert len(r["cliente"]) == 1


# --------------------------------------------------------- el endpoint entero

def test_plan_vigente_devuelve_las_dos_series(monkeypatch, enchufado):
    hoy = date.today()
    enchufado(serie={(m, SKILL): (9.0, 300.0) for m in _momentos(hoy, 8, 10)},
              cliente={(m, SKILL): 11.0 for m in _momentos(hoy, 8, 10)})
    monkeypatch.setattr(pdatos, "schema_disponible", lambda c: True)
    monkeypatch.setattr(pdatos, "corrida_vigente", lambda c, i, h: {
        "CorridaID": 1, "Desde": hoy, "Hasta": hoy + timedelta(days=14)})
    monkeypatch.setattr(pdatos, "leer_requerimiento", lambda c, i: [])
    monkeypatch.setattr(pdatos, "leer_pronostico", lambda c, i: [])
    monkeypatch.setattr(pdatos, "hay_demanda_total", lambda c, i: True)

    plan = servicio.plan_vigente(_Engine(), 20)

    assert plan["real"] and plan["cliente"]
    assert plan["real_hasta"] is not None


def test_sin_corrida_las_claves_estan_igual(monkeypatch):
    """La pantalla lee `plan.real` sin preguntar: si la clave falta cuando no hay
    corrida, el primer render explota antes de mostrar el aviso de que hay que
    apretar Recalcular."""
    monkeypatch.setattr(pdatos, "schema_disponible", lambda c: True)
    monkeypatch.setattr(pdatos, "corrida_vigente", lambda c, i, h: None)

    plan = servicio.plan_vigente(_Engine(), 20)

    assert plan["real"] == [] and plan["cliente"] == []
    assert plan["real_hasta"] is None


# --------------------------------------------- operadores realmente conectados

def test_los_conectados_vienen_resueltos_por_intervalo(enchufado, monkeypatch):
    """Vienen de la misma vista que usa el tablero de la operación, deduplicados
    por persona y en operadores-EQUIVALENTES: el que entró a la mitad del
    intervalo cuenta medio. No hay nada que agregar del lado de la pantalla."""
    hoy = date.today()
    m = datetime.combine(hoy, time(9, 0))
    enchufado(serie={(m, SKILL): (50.0, 300.0),
                     (m + timedelta(minutes=30), SKILL): (50.0, 300.0)})
    monkeypatch.setattr(pdatos, "agentes_conectados", lambda c, i, a, b: {m: 62.1})

    r = servicio._cotejo_del_plan(None, 20, hoy, hoy + timedelta(days=1))

    assert r["conectados"] == [{"Intervalo": m, "Operadores": 62.1}]


def test_con_los_pools_los_conectados_vienen_partidos_y_sin_pausas(enchufado, monkeypatch):
    """Igual que en Comparación: telefónicos y refuerzo, logueados y en línea."""
    hoy = date.today()
    m = datetime.combine(hoy, time(9, 0))
    enchufado(serie={(m, SKILL): (50.0, 300.0),
                     (m + timedelta(minutes=30), SKILL): (50.0, 300.0)})
    monkeypatch.setattr(pdatos, "agentes_conectados", lambda c, i, a, b: {m: 62.1})
    pedidos = {}

    def origen(conn, c, pools, d, h, con_en_linea=False):
        pedidos.update(pools=pools, con_en_linea=con_en_linea)
        return {m: {"pool": 55.88, "refuerzo": 6.0, "digital": 1.04, "otras": 0.26,
                    "pool_en_linea": 49.27, "refuerzo_en_linea": 4.97,
                    "digital_en_linea": 0.91, "otras_en_linea": 0.26}}
    monkeypatch.setattr(pdatos, "conectados_por_origen", origen)

    r = servicio._cotejo_del_plan(None, 20, hoy, hoy + timedelta(days=1), pool_ids=[1])

    assert pedidos == {"pools": [1], "con_en_linea": True}
    assert r["conectados"] == [{"Intervalo": m, "Operadores": 62.1,
                                "Telefonicos": 55.9, "Refuerzo": 6.0, "Otras": 0.3,
                                "Digital": 1.0, "Dedicada": 0.0, "TelefonicosEnLinea": 49.3,
                                "RefuerzoEnLinea": 5.0, "OtrasEnLinea": 0.3,
                                "DigitalEnLinea": 0.9, "DedicadaEnLinea": 0.0}]


def test_si_falla_partir_los_conectados_del_plan_queda_el_total(enchufado, monkeypatch):
    hoy = date.today()
    m = datetime.combine(hoy, time(9, 0))
    enchufado(serie={(m, SKILL): (50.0, 300.0),
                     (m + timedelta(minutes=30), SKILL): (50.0, 300.0)})
    monkeypatch.setattr(pdatos, "agentes_conectados", lambda c, i, a, b: {m: 62.1})
    monkeypatch.setattr(pdatos, "conectados_por_origen",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("timeout")))

    r = servicio._cotejo_del_plan(None, 20, hoy, hoy + timedelta(days=1), pool_ids=[1])

    assert r["conectados"] == [{"Intervalo": m, "Operadores": 62.1}]


def test_los_conectados_cortan_donde_corta_el_real(enchufado, monkeypatch):
    """Mismo corte que las llamadas: el intervalo en curso viene a medias."""
    hoy = date.today()
    cerrado = datetime.combine(hoy, time(9, 0))
    en_curso = datetime.combine(hoy, time(9, 30))
    enchufado(serie={(cerrado, SKILL): (50.0, 300.0),
                     (en_curso, SKILL): (10.0, 300.0)})
    monkeypatch.setattr(pdatos, "agentes_conectados",
                        lambda c, i, a, b: {cerrado: 54.0, en_curso: 20.0})

    r = servicio._cotejo_del_plan(None, 20, hoy, hoy + timedelta(days=1))

    assert [f["Intervalo"] for f in r["conectados"]] == [cerrado]


def test_si_falla_la_lectura_de_conectados_el_plan_se_muestra_igual(monkeypatch):
    hoy = date.today()
    monkeypatch.setattr(pdatos, "serie_por_skill", lambda c, i, a, b: {})
    monkeypatch.setattr(pdatos, "hay_forecast_en_produccion", lambda c, i: False)
    monkeypatch.setattr(pdatos, "agentes_conectados",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    r = servicio._cotejo_del_plan(None, 20, hoy, hoy + timedelta(days=1))

    assert r["conectados"] == []


def test_los_conectados_salen_de_la_vista_del_tablero():
    """Y no del informe de skills, donde `[Agentes Logueados]` es "logueados que
    tienen este skill": sumarlo entre colas cuenta a la misma persona una vez por
    cada una (283 contra 62 reales el 9/9 a las 11:00). La vista deduplica por
    `nomina_id` y reproduce los números del tablero exactamente."""
    sql = pdatos.FuenteVoltara.CONECTADOS
    assert "Tablero_Agentes_Voltara" in sql
    assert "Agentes Logueados" not in sql


# ------------------------------------------- la cuenta de en línea a a citar

def test_cada_intervalo_trae_los_descuentos_que_rehacen_a_planificar(monkeypatch):
    """La pantalla parte «en línea → a citar» en pausas/break y ausentismo. Para
    eso cada fila trae los tres factores, sin redondear, y rehechos en el mismo
    orden que `dimensionar_intervalo` tienen que dar lo guardado."""
    import math
    from app import planificador as pl
    from app.planificador_erlang import _sin_shrinkage as erlang_sin

    lunes = datetime(2026, 9, 14, 11, 0)
    cfg = pl.CampanaCfg(campana_id=20, shrinkage=0.064, break_min_por_hora=5.0,
                        disponibilidad=[pl.FranjaDisponibilidad(0, 0, 24, 0.903)])
    monkeypatch.setattr(pdatos, "cargar_config", lambda c, i: cfg)
    monkeypatch.setattr(pdatos, "feriados", lambda c, a, b, *_: [])
    filas = [{"Intervalo": lunes, "OperadoresLinea": 50,
              "OperadoresPlanificar": int(math.ceil(
                  erlang_sin(erlang_sin(50 / 0.903, 0.064), 5 / 60)))}]

    servicio._anotar_cadena(None, 20, filas, lunes.date(), lunes.date())

    f = filas[0]
    assert f["Disponibilidad"] == 0.903 and f["Ausentismo"] == 0.064
    assert f["Break"] == 5 / 60
    assert f["Redondeo"] == "arriba"
    rehecho = f["OperadoresLinea"] / f["Disponibilidad"] / (1 - f["Ausentismo"]) / (1 - f["Break"])
    assert math.ceil(rehecho) == f["OperadoresPlanificar"]


def test_si_falla_la_cuenta_el_plan_sale_sin_anotar(monkeypatch):
    def rompe(c, i):
        raise RuntimeError("sin config")
    monkeypatch.setattr(pdatos, "cargar_config", rompe)
    filas = [{"Intervalo": datetime(2026, 9, 14, 11, 0), "OperadoresLinea": 50}]

    servicio._anotar_cadena(None, 20, filas, date(2026, 9, 14), date(2026, 9, 14))

    assert "Disponibilidad" not in filas[0]


def test_de_madrugada_la_cuenta_dice_que_se_redondea_para_abajo(monkeypatch):
    from app import planificador as pl

    cfg = pl.CampanaCfg(campana_id=20, shrinkage=0.064,
                        redondeo_abajo_desde=0, redondeo_abajo_hasta=8)
    monkeypatch.setattr(pdatos, "cargar_config", lambda c, i: cfg)
    monkeypatch.setattr(pdatos, "feriados", lambda c, a, b, *_: [])
    filas = [{"Intervalo": datetime(2026, 9, 14, 3, 0), "OperadoresLinea": 2},
             {"Intervalo": datetime(2026, 9, 14, 9, 0), "OperadoresLinea": 20}]

    servicio._anotar_cadena(None, 20, filas, date(2026, 9, 14), date(2026, 9, 14))

    assert [f["Redondeo"] for f in filas] == ["abajo", "arriba"]


def test_la_franja_de_redondeo_se_pide_completa():
    from pydantic import ValidationError
    from app.routers.planificador import CampanaRequest

    assert CampanaRequest(redondeo_abajo_desde=0, redondeo_abajo_hasta=8)
    assert CampanaRequest(redondeo_abajo_desde=None, redondeo_abajo_hasta=None)
    with pytest.raises(ValidationError):
        CampanaRequest(redondeo_abajo_desde=0, redondeo_abajo_hasta=None)
    with pytest.raises(ValidationError):
        CampanaRequest(redondeo_abajo_desde=5, redondeo_abajo_hasta=5)
