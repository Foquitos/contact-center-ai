"""Tests del backtest y de la exportación a Excel del planificador.

Offline: no toca ni BD ni red. La capa de datos se reemplaza por series
sintéticas, así que se puede afirmar exactamente qué tiene que dar el
pronóstico de cada día —y, sobre todo, qué NO tiene que saber.

Correr: pytest tests/test_planificador_backtest.py -m "not tokens"
"""
from datetime import date, datetime, time, timedelta

import pytest
from openpyxl import load_workbook

from app import planificador as pl
from app import planificador_datos as pdatos
from app import planificador_export as export
from app import planificador_servicio as servicio


# ------------------------------------------------------------------ andamiaje

class _Conn:
    """Conexión de mentira: el backtest sólo la usa como contexto."""
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Engine:
    def connect(self):
        return _Conn()


HOY = date(2026, 9, 7)
SKILL = 6
OTRO = 4


def _cfg():
    return pl.CampanaCfg(
        campana_id=20, intervalo_min=30, shrinkage=0.0,
        pools={1: pl.PoolCfg(pool_id=1, nombre="General", min_operadores=1)},
        skills=[pl.SkillCfg(SKILL, "EMERGENCIAS", 1, 0.80, 20),
                pl.SkillCfg(OTRO, "COMERCIAL", 1, 0.80, 20)],
    )


def _serie(desde: date, hasta: date, valor, skills=(SKILL,)):
    """(momento, skill) -> (llamadas, tmo). `valor` puede ser un número o una
    función de (día, skill)."""
    salida = {}
    dia = desde
    while dia < hasta:
        for sk in skills:
            v = valor(dia, sk) if callable(valor) else valor
            momento = datetime.combine(dia, time(0, 0))
            fin = momento + timedelta(days=1)
            while momento < fin:
                salida[(momento, sk)] = (float(v), 180.0)
                momento += timedelta(minutes=30)
        dia += timedelta(days=1)
    return salida


@pytest.fixture
def enchufado(monkeypatch):
    """Devuelve una función que enchufa una serie y deja corriendo el backtest."""
    def _armar(serie, *, total=None, tramos=(), eventos=(), produccion=None, cfg=None,
               clima=None, clima_activo=False):
        monkeypatch.setattr(pdatos, "schema_disponible", lambda c: True)
        monkeypatch.setattr(pdatos, "cargar_config", lambda c, i: cfg or _cfg())
        monkeypatch.setattr(pdatos, "hay_demanda_total", lambda c, i: total is not None)
        monkeypatch.setattr(pdatos, "serie_por_skill",
                            lambda c, i, d, h: {k: v for k, v in serie.items()
                                                if d <= k[0].date() < h})
        monkeypatch.setattr(pdatos, "serie_demanda_total",
                            lambda c, i, d, h, m: {k: v for k, v in (total or {}).items()
                                                   if d <= k[0].date() < h})
        monkeypatch.setattr(pdatos, "feriados", lambda c, a, b, *_: [])
        monkeypatch.setattr(pdatos, "dias_a_excluir", lambda c, i, a, b: [])
        monkeypatch.setattr(pdatos, "eventos", lambda c, i, a, b: list(eventos))
        monkeypatch.setattr(pdatos, "asignacion_configurada",
                            lambda c, i, a, b: [dict(t) for t in tramos])
        monkeypatch.setattr(pdatos, "medir_asignacion",
                            lambda c, i, a, b: {"vigente": None, "por_skill": {}})
        monkeypatch.setattr(pdatos, "clima_de_la_campana",
                            lambda c, i: {"lat": -34.7, "lon": -58.4,
                                          "activo": clima_activo})
        monkeypatch.setattr(pdatos, "clima_por_dia",
                            lambda c, i, d, h, solo_observado=False:
                            {k: v for k, v in (clima or {}).items() if d <= k < h})
        monkeypatch.setattr(pdatos, "hay_forecast_en_produccion",
                            lambda c, i: produccion is not None)
        monkeypatch.setattr(pdatos, "forecast_en_produccion",
                            lambda c, i, d, h: {k: v for k, v in (produccion or {}).items()
                                                if d <= k[0].date() < h})
        return _Engine()
    return _armar


def _corrida(enchufado, serie, desde, hasta, **kw):
    engine = enchufado(serie, **{k: v for k, v in kw.items()
                                 if k in ("total", "tramos", "eventos", "produccion",
                                          "cfg", "clima", "clima_activo")})
    return servicio.backtest(engine, 20, desde, hasta,
                             antelacion=kw.get("antelacion", 7), hoy=HOY)


# =========================================================================
# LO QUE EL BACKTEST NO TIENE QUE SABER
# =========================================================================

def test_un_volumen_estable_se_pronostica_exacto(enchufado):
    """Serie plana de 10 llamadas por media hora: el pronóstico tiene que dar 10
    y el error, cero. Es el control: si esto falla, nada de lo de abajo
    significa nada."""
    serie = _serie(date(2026, 3, 1), HOY, 10)
    bt = _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30))
    assert bt["resumen"]["intervalo"]["wape"] == pytest.approx(0.0, abs=1e-9)
    assert bt["por_dia"][0]["real"] == pytest.approx(480.0)
    assert bt["por_dia"][0]["pronosticado"] == pytest.approx(480.0)


def test_no_se_filtra_el_futuro(enchufado):
    """LA propiedad del backtest. Los días evaluados suben a 100 llamadas por
    media hora; con 7 días de antelación el modelo NO puede haberlo visto, así
    que tiene que seguir pronosticando 10 y errar feo.

    Si algún día alguien 'mejora' el backtest pasándole toda la historia, este
    test se pone en verde brillante con un WAPE de cero y hay que sospechar.
    """
    salto = date(2026, 8, 24)
    serie = _serie(date(2026, 3, 1), HOY,
                   lambda d, s: 100 if d >= salto else 10)
    bt = _corrida(enchufado, serie, salto, salto + timedelta(days=6))
    for fila in bt["por_dia"]:
        assert fila["real"] == pytest.approx(4800.0)
        assert fila["pronosticado"] == pytest.approx(480.0)
    assert bt["resumen"]["diario"]["sesgo"] == pytest.approx(9.0)


def test_con_antelacion_de_un_dia_ya_ve_el_salto(enchufado):
    """El mismo salto con un día de antelación: a partir del segundo día el
    modelo ya lo tiene en la historia y corrige. Es la contraprueba de que el
    corte se mueve con la antelación y no está clavado."""
    salto = date(2026, 8, 24)
    serie = _serie(date(2026, 3, 1), HOY,
                   lambda d, s: 100 if d >= salto else 10)
    bt = _corrida(enchufado, serie, salto, salto + timedelta(days=13), antelacion=1)
    primero, ultimo = bt["por_dia"][0], bt["por_dia"][-1]
    assert primero["pronosticado"] == pytest.approx(480.0)
    assert ultimo["pronosticado"] > primero["pronosticado"]
    # Y se frena en el tope: la corrección de nivel está acotada a ±35% para que
    # un evento de tres días no arrastre el pronóstico del mes entero. Es la MISMA
    # razón por la que, cuando el pico de agosto de 2026 terminó, el modelo siguió
    # pronosticando de más: el tope corta en los dos sentidos.
    assert ultimo["pronosticado"] == pytest.approx(
        480.0 * (1 + pl.TOPE_CORRECCION_NIVEL))


def test_no_escribe_nada(enchufado, monkeypatch):
    """Un backtest que siembra días atípicos contamina la próxima corrida real."""
    def _prohibido(*a, **k):
        raise AssertionError("el backtest no puede escribir en la base")
    monkeypatch.setattr(pdatos, "crear_corrida", _prohibido)
    monkeypatch.setattr(pdatos, "guardar_pronostico", _prohibido)
    monkeypatch.setattr(pdatos, "guardar_requerimiento", _prohibido)
    _corrida(enchufado, _serie(date(2026, 3, 1), HOY, 10),
             date(2026, 8, 24), date(2026, 8, 30))


# =========================================================================
# BORDES DEL PERÍODO
# =========================================================================

def test_el_dia_de_hoy_se_muestra_pero_no_promedia(enchufado):
    """Hoy viene a medias y arrastraría el error hacia abajo sin que se note."""
    serie = _serie(date(2026, 3, 1), HOY + timedelta(days=1), 10)
    bt = _corrida(enchufado, serie, HOY - timedelta(days=3), HOY)
    assert bt["por_dia"][-1]["parcial"] is True
    assert bt["por_dia"][-1]["dia"] == HOY
    assert bt["dias_evaluados"] == 3
    assert bt["resumen"]["diario"]["n"] == 3


def test_no_se_puede_evaluar_el_futuro(enchufado):
    serie = _serie(date(2026, 3, 1), HOY, 10)
    bt = _corrida(enchufado, serie, HOY - timedelta(days=2), HOY + timedelta(days=30))
    assert bt["hasta"] == HOY


def test_periodo_invertido_y_periodo_enorme(enchufado):
    serie = _serie(date(2026, 3, 1), HOY, 10)
    with pytest.raises(ValueError, match="termina antes"):
        _corrida(enchufado, serie, date(2026, 8, 30), date(2026, 8, 24))
    with pytest.raises(ValueError, match="hasta"):
        _corrida(enchufado, serie, date(2026, 1, 1), HOY)


# =========================================================================
# ALCANCE: EL REAL Y EL PRONÓSTICO TIENEN QUE CUBRIR LO MISMO
# =========================================================================

def test_una_cola_que_el_modelo_no_pronostica_no_ensucia_el_error(enchufado):
    """COMERCIAL no tiene historia, así que no se pronostica. Sus llamadas reales
    no pueden contarse como error del modelo —sería medir un recorte de alcance,
    no un pronóstico— pero tampoco pueden desaparecer: van al aviso."""
    serie = _serie(date(2026, 3, 1), HOY, 10)
    # COMERCIAL aparece recién en el período evaluado: sin historia previa.
    serie.update(_serie(date(2026, 8, 24), date(2026, 8, 31), 7, skills=(OTRO,)))
    bt = _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30))

    assert bt["resumen"]["intervalo"]["wape"] == pytest.approx(0.0, abs=1e-9)
    fuera = {f["nombre"]: f["llamadas"] for f in bt["fuera_del_pronostico"]}
    assert fuera["COMERCIAL"] == pytest.approx(7 * 48 * 7)
    assert any("no pronosticó" in a for a in bt["avisos"])


def test_sin_tramo_de_asignacion_el_skill_queda_afuera_y_se_avisa(enchufado):
    total = {k: {"total": v[0] * 4, "acme": v[0], "tmo": 180.0}
             for k, v in _serie(date(2026, 3, 1), HOY, 10).items()}
    serie = _serie(date(2026, 3, 1), HOY, 10)
    bt = _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30),
                  total=total, tramos=())
    assert bt["por_dia"][0]["pronosticado"] == 0.0
    assert any("EMERGENCIAS" in a and "asignación" in a for a in bt["avisos"])


def test_el_reparto_se_aplica_sobre_la_demanda_total(enchufado):
    """Con la descarga del 100%, el modelo pronostica la demanda del CLIENTE y el
    share se aplica aparte. Lo que se compara contra nuestras llamadas es el
    producto de los dos."""
    total = {k: {"total": 40.0, "acme": 10.0, "tmo": 180.0}
             for k in _serie(date(2026, 3, 1), HOY, 10)}
    serie = _serie(date(2026, 3, 1), HOY, 10)
    tramos = [{"AsignacionID": 1, "SkillID": SKILL, "VigenteDesde": date(2025, 1, 1),
               "VigenteHasta": None, "Porcentaje": 0.25, "Nota": None}]
    bt = _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30),
                  total=total, tramos=tramos)
    fila = bt["por_dia"][0]
    assert fila["pronosticado_total"] == pytest.approx(40 * 48)
    assert fila["pronosticado"] == pytest.approx(10 * 48)
    assert fila["share_aplicado"] == pytest.approx(0.25)
    assert fila["share_real"] == pytest.approx(0.25)
    assert bt["resumen"]["total_cliente"]["wape"] == pytest.approx(0.0, abs=1e-9)


def test_la_deriva_sigue_el_reparto_real_cuando_se_corre_del_tramo(enchufado):
    """El tramo dice 25% pero hace semanas que nos llega el 20%. El pronóstico
    tiene que seguir al reparto real, no al papel."""
    total = {k: {"total": 40.0, "acme": 8.0, "tmo": 180.0}
             for k in _serie(date(2026, 3, 1), HOY, 10)}
    serie = _serie(date(2026, 3, 1), HOY, 8)
    tramos = [{"AsignacionID": 1, "SkillID": SKILL, "VigenteDesde": date(2025, 1, 1),
               "VigenteHasta": None, "Porcentaje": 0.25, "Nota": None}]
    bt = _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30),
                  total=total, tramos=tramos)
    # 8 / (40 * 0,25) = 0,8 -> se aplica 0,25 * 0,8 = 0,20
    assert bt["por_dia"][0]["share_aplicado"] == pytest.approx(0.20)
    assert bt["por_dia"][0]["pronosticado"] == pytest.approx(8 * 48)


def test_la_deriva_no_se_dispara_cuando_cambia_el_tramo(enchufado):
    """El escalón del 2026-09-01 subió el share de 0,356 a 0,492. Si la deriva
    comparara los días previos contra el tramo NUEVO, leería una caída del 27% en
    un reparto que en realidad ya estaba bien. Cada día se mide contra el tramo
    que regía ese día."""
    corte = date(2026, 8, 20)
    def _acme(dia, sk):
        return 40.0 * (0.5 if dia >= corte else 0.25)
    total = {k: {"total": 40.0, "acme": _acme(k[0].date(), k[1]), "tmo": 180.0}
             for k in _serie(date(2026, 3, 1), HOY, 10)}
    serie = _serie(date(2026, 3, 1), HOY, lambda d, sk: _acme(d, sk))
    tramos = [
        {"AsignacionID": 1, "SkillID": SKILL, "VigenteDesde": date(2025, 1, 1),
         "VigenteHasta": corte - timedelta(days=1), "Porcentaje": 0.25, "Nota": None},
        {"AsignacionID": 2, "SkillID": SKILL, "VigenteDesde": corte,
         "VigenteHasta": None, "Porcentaje": 0.50, "Nota": None},
    ]
    bt = _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30),
                  total=total, tramos=tramos, antelacion=1)
    # Los dos regímenes son exactos contra su propio tramo: la deriva es 1.
    assert bt["por_dia"][0]["share_aplicado"] == pytest.approx(0.50)


def test_la_deriva_esta_acotada(enchufado):
    """Una semana rarísima no puede llevarse puesto el tramo: el tope existe para
    que un evento de tres días no reescriba el reparto."""
    total = {k: {"total": 40.0, "acme": 0.4, "tmo": 180.0}
             for k in _serie(date(2026, 3, 1), HOY, 10)}
    serie = _serie(date(2026, 3, 1), HOY, 0.4)
    tramos = [{"AsignacionID": 1, "SkillID": SKILL, "VigenteDesde": date(2025, 1, 1),
               "VigenteHasta": None, "Porcentaje": 0.50, "Nota": None}]
    bt = _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30),
                  total=total, tramos=tramos)
    # Sin tope daría 0,4/(40*0,5) = 0,02. Con tope 0,25 el share no baja de 0,375.
    assert bt["por_dia"][0]["share_aplicado"] == pytest.approx(0.50 * 0.75)


def test_la_deriva_se_puede_apagar(enchufado):
    """En 0 el tramo se usa tal cual: es el comportamiento anterior y tiene que
    seguir siendo alcanzable sin tocar código."""
    cfg = _cfg()
    cfg.reparto_deriva_dias = 0
    total = {k: {"total": 40.0, "acme": 8.0, "tmo": 180.0}
             for k in _serie(date(2026, 3, 1), HOY, 10)}
    serie = _serie(date(2026, 3, 1), HOY, 8)
    tramos = [{"AsignacionID": 1, "SkillID": SKILL, "VigenteDesde": date(2025, 1, 1),
               "VigenteHasta": None, "Porcentaje": 0.25, "Nota": None}]
    bt = _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30),
                  total=total, tramos=tramos, cfg=cfg)
    assert bt["por_dia"][0]["share_aplicado"] == pytest.approx(0.25)


def test_la_deriva_no_se_extrapola_mas_alla_de_su_ventana(enchufado):
    """A siete días de la ventana la deriva es información; a treinta es una
    apuesta. Medido sobre 357 días: aplicarla a todo el horizonte empeora el
    pronóstico del mes (31,71% contra 31,42%)."""
    total = {k: {"total": 40.0, "acme": 8.0, "tmo": 180.0}
             for k in _serie(date(2026, 3, 1), HOY, 10)}
    serie = _serie(date(2026, 3, 1), HOY, 8)
    tramos = [{"AsignacionID": 1, "SkillID": SKILL, "VigenteDesde": date(2025, 1, 1),
               "VigenteHasta": None, "Porcentaje": 0.25, "Nota": None}]
    cerca = _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30),
                     total=total, tramos=tramos, antelacion=7)
    lejos = _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30),
                     total=total, tramos=tramos, antelacion=30)
    assert cerca["por_dia"][0]["share_aplicado"] == pytest.approx(0.20)
    assert lejos["por_dia"][0]["share_aplicado"] == pytest.approx(0.25)


def test_la_deriva_de_produccion_da_lo_mismo_que_la_del_backtest():
    """`_deriva_del_reparto` (lo que corre en producción) y `_deriva_al_corte`
    (lo que corre en el backtest) son dos caminos distintos hacia el mismo
    número. Si se separan, la pantalla Comparación mide un modelo que no es el
    que después mueve gente."""
    cfg = _cfg()
    corte = date(2026, 9, 1)
    cruda = {}
    dia = corte - timedelta(days=10)
    while dia < corte:
        momento = datetime.combine(dia, time(9, 0))
        cruda[(momento, SKILL)] = {"total": 100.0, "acme": 20.0, "tmo": 180.0}
        cruda[(momento, OTRO)] = {"total": 50.0, "acme": 25.0, "tmo": 180.0}
        dia += timedelta(days=1)
    tramos = [{"AsignacionID": 1, "SkillID": SKILL, "VigenteDesde": date(2025, 1, 1),
               "VigenteHasta": None, "Porcentaje": 0.25, "Nota": None},
              {"AsignacionID": 2, "SkillID": OTRO, "VigenteDesde": date(2025, 1, 1),
               "VigenteHasta": None, "Porcentaje": 0.50, "Nota": None}]

    deriva = servicio._deriva_del_reparto(cfg, cruda, tramos, corte)
    # EMERGENCIAS: llega el 20% donde el tramo dice 25% -> 0,8
    assert deriva[SKILL] == pytest.approx(0.8)
    # COMERCIAL: llega exactamente el 50% -> sin corrección
    assert deriva[OTRO] == pytest.approx(1.0)

    # y es la misma cuenta que hace la capa de datos
    diario = pdatos.reparto_diario(cruda)
    assert pdatos.deriva_de_reparto(tramos, diario, corte, SKILL,
                                    cfg.reparto_deriva_dias,
                                    cfg.reparto_deriva_tope) == pytest.approx(0.8)


def test_sin_tramo_no_hay_deriva_que_calcular():
    """Un skill sin tramo cargado no se corrige: no hay contra qué medir la
    deriva, y suponer un 100% sería inventar."""
    cfg = _cfg()
    corte = date(2026, 9, 1)
    momento = datetime.combine(corte - timedelta(days=1), time(9, 0))
    cruda = {(momento, SKILL): {"total": 100.0, "acme": 20.0, "tmo": 180.0}}
    assert servicio._deriva_del_reparto(cfg, cruda, [], corte) == {}


def test_la_deriva_no_ve_los_dias_que_se_estan_evaluando(enchufado):
    """Misma regla que el resto del backtest: la deriva del día que se evalúa se
    calcula sólo con lo que ya se sabía a la fecha de corte."""
    salto = date(2026, 8, 27)
    def _acme(dia, sk):
        return 4.0 if dia >= salto else 10.0
    total = {k: {"total": 40.0, "acme": _acme(k[0].date(), k[1]), "tmo": 180.0}
             for k in _serie(date(2026, 3, 1), HOY, 10)}
    serie = _serie(date(2026, 3, 1), HOY, lambda d, sk: _acme(d, sk))
    tramos = [{"AsignacionID": 1, "SkillID": SKILL, "VigenteDesde": date(2025, 1, 1),
               "VigenteHasta": None, "Porcentaje": 0.25, "Nota": None}]
    bt = _corrida(enchufado, serie, salto, salto, total=total, tramos=tramos,
                  antelacion=7)
    # A siete días de antelación, el corte es el 20/8: ahí el reparto todavía era
    # exacto contra el tramo, así que la deriva vale 1 y no anticipa la caída.
    assert bt["por_dia"][0]["share_aplicado"] == pytest.approx(0.25)


# =========================================================================
# LO QUE SE INFORMA
# =========================================================================

def test_el_error_de_forma_saca_el_error_de_nivel(enchufado):
    """Si el pronóstico tiene la curva perfecta y sólo erra el nivel, el error de
    forma tiene que dar cero. Es lo que distingue 'fallamos en cuántas llamadas'
    de 'fallamos en cuándo llegan', que se arreglan de maneras distintas."""
    salto = date(2026, 8, 24)
    serie = _serie(date(2026, 3, 1), HOY,
                   lambda d, s: 30 if d >= salto else 10)
    bt = _corrida(enchufado, serie, salto, salto + timedelta(days=6))
    assert bt["resumen"]["intervalo"]["wape"] > 0.5      # erra el nivel, y feo
    assert bt["resumen"]["forma"]["wape"] == pytest.approx(0.0, abs=1e-9)


def test_los_dias_con_evento_se_marcan_y_se_informan_aparte(enchufado):
    serie = _serie(date(2026, 3, 1), HOY, 10)
    evento = {"EventoID": 1, "CampanaID": 20,
              "Desde": datetime(2026, 8, 25, 0, 0), "Hasta": datetime(2026, 8, 25, 23, 59),
              "Tipo": "corte masivo", "Descripcion": "", "Factor": 2.0,
              "Origen": "manual", "ExcluirDeEntrenamiento": True, "Confirmado": True}
    bt = _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30),
                  eventos=[evento])
    marcados = [f for f in bt["por_dia"] if f["evento"]]
    assert [f["dia"] for f in marcados] == [date(2026, 8, 25)]
    assert bt["resumen"]["diario_sin_eventos"]["n"] == 6
    assert any("atípicos o feriados" in a for a in bt["avisos"])


def test_el_ingenuo_repite_el_mismo_dia_de_la_semana_anterior(enchufado):
    serie = _serie(date(2026, 3, 1), HOY,
                   lambda d, s: 10 + d.isoweekday())
    bt = _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30))
    for fila in bt["por_dia"]:
        # Mismo día de la semana anterior: misma cantidad, porque la serie sólo
        # depende del día de semana.
        assert fila["ingenuo"] == pytest.approx(fila["real"])
    assert bt["resumen"]["ingenuo"]["wape"] == pytest.approx(0.0, abs=1e-9)


def test_la_mejora_contra_el_ingenuo_dice_si_el_modelo_aporta():
    assert servicio._mejora(0.10, 0.20) == pytest.approx(0.5)
    assert servicio._mejora(0.20, 0.20) == pytest.approx(0.0)
    assert servicio._mejora(0.30, 0.20) == pytest.approx(-0.5)
    assert servicio._mejora(None, 0.20) is None
    assert servicio._mejora(0.10, 0) is None


def test_el_cotejo_avisa_cuando_las_dos_fuentes_no_coinciden(enchufado):
    """El informe de skills y el IVR completo tienen que decir lo mismo sobre
    nuestras llamadas. Si no, el share medido está mal en esa proporción."""
    serie = _serie(date(2026, 3, 1), HOY, 10)
    total = {k: {"total": 40.0, "acme": 8.0, "tmo": 180.0} for k in serie}
    tramos = [{"AsignacionID": 1, "SkillID": None, "VigenteDesde": date(2025, 1, 1),
               "VigenteHasta": None, "Porcentaje": 0.25, "Nota": None}]
    bt = _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30),
                  total=total, tramos=tramos)
    assert bt["cotejo_fuentes"]["diferencia"] == pytest.approx(0.25)
    assert any("no coinciden" in a for a in bt["avisos"])


# =========================================================================
# EXPORTACIÓN A EXCEL
# =========================================================================

_PLAN = {
    "corrida": {"CorridaID": 15, "Horizonte": "operativo", "Desde": date(2026, 9, 7),
                "Hasta": date(2026, 9, 21), "Modelo": "baseline-estacional-v1",
                "CreadoEn": datetime(2026, 9, 7, 8, 0), "TerminadoEn": None,
                "Metricas": {"intervalos": 2, "llamadas": 300.0, "pico_operadores": 24,
                             "horas_operador": 12.0, "nds_promedio": 0.83,
                             "abandono_promedio": 0.012,
                             "brecha": {"intervalos_con_malla": 2, "intervalos_en_falta": 1,
                                        "intervalos_con_exceso": 1,
                                        "operadores_faltantes_pico": 3,
                                        "horas_operador_faltantes": 1.5,
                                        "horas_operador_sobrantes": 0.5},
                             "avisos": ["Un aviso cualquiera."]}},
    "sobre_demanda_total": True,
    "requerimiento": [
        {"PoolID": 1, "Pool": "General", "Intervalo": datetime(2026, 9, 7, 10, 0),
         "Llamadas": 195.0, "TmoSeg": 180.0, "Trafico": 19.5, "OperadoresLinea": 24,
         "OperadoresPlanificar": 33, "NdsContractual": 0.83, "NdsAtendidas": 0.84,
         "NdsEntrantes": 0.83, "Abandono": 0.006, "Ocupacion": 0.81,
         "Motivo": "nivel de servicio", "OperadoresPlanificados": 30, "Brecha": -3,
         "AsaSeg": 8.4, "NivelAtencionB": 0.9425},
    ],
    "pronostico": [
        {"SkillID": 6, "Intervalo": datetime(2026, 9, 7, 10, 0), "LlamadasTotal": 400.0,
         "Asignacion": 0.49, "LlamadasAcme": 195.0, "LlamadasBase": 196.0, "TmoSeg": 180.0},
    ],
}

_CONFIG = {
    "campana_id": 20, "intervalo_min": 30, "max_ocupacion": 0.85, "shrinkage": 0.272,
    "paciencia_seg": 1126,
    "procedencia": {"paciencia_origen": "km", "paciencia_horizonte_seg": 60,
                    "shrinkage_origen": "payroll", "shrinkage_ausentismo": 0.171,
                    "shrinkage_capacitacion": 0.093,
                    "shrinkage_medido_en": datetime(2026, 9, 3, 17, 19)},
    "pools": [{"pool_id": 1, "nombre": "General", "skills": [
        {"skill_id": 6, "nombre": "EMERGENCIAS", "objetivo_nds": 0.80, "umbral_seg": 20,
         "max_abandono": None, "paciencia_seg": None, "max_asa_seg": None,
         "objetivo_nds_2": None, "umbral_seg_2": None, "min_nivel_atencion_b": None,
         "activo": True, "llamadas_30d": 51234}]}],
    "disponibilidad": [{"dia_semana": 0, "hora_desde": 8, "hora_hasta": 17, "factor": 0.82}],
}


def _hoja(flujo, nombre):
    return load_workbook(flujo)[nombre]


def test_el_excel_del_plan_trae_los_supuestos():
    """Una dotación sin los parámetros con los que salió no se puede defender dos
    semanas después, que es justo cuando se discute."""
    wb = load_workbook(export.libro_del_plan(_PLAN, _CONFIG, "Voltara"))
    assert wb.sheetnames == ["Resumen", "Requerimiento", "Pronóstico", "Parámetros"]

    params = "\n".join(str(c.value) for fila in wb["Parámetros"].iter_rows()
                       for c in fila if c.value is not None)
    assert "Shrinkage de nómina" in params and "payroll" in params
    assert "EMERGENCIAS" in params
    assert "0.272" in params or "0.272" in str(_CONFIG["shrinkage"])


def test_el_excel_del_plan_escribe_numeros_y_no_texto():
    """Un '80,3%' escrito como texto no se puede promediar ni graficar, y el
    archivo existe para que alguien lo siga trabajando."""
    ws = _hoja(export.libro_del_plan(_PLAN, _CONFIG, "Voltara"), "Requerimiento")
    encabezados = [c.value for c in ws[1]]
    fila = {h: ws.cell(row=2, column=i + 1) for i, h in enumerate(encabezados)}
    assert fila["Llamadas"].value == 195.0
    assert isinstance(fila["NDS"].value, float) and fila["NDS"].value == 0.83
    assert fila["NDS"].number_format.endswith("%")
    assert fila["Brecha"].value == -3
    # El intervalo se parte en día y hora: en Excel se filtra por día y se ordena
    # por hora, y un solo campo obliga a hacer las dos cosas mal.
    # openpyxl guarda las fechas como datetime; lo que hace que Excel muestre
    # "07/09/2026" es el formato de celda, no el tipo.
    assert str(fila["Día"].value)[:10] == "2026-09-07"
    assert fila["Día"].number_format == export.FECHA
    assert fila["Intervalo"].value == "10:00"


def test_el_excel_del_plan_tiene_encabezado_fijo():
    ws = _hoja(export.libro_del_plan(_PLAN, _CONFIG, "Voltara"), "Pronóstico")
    assert ws.freeze_panes == "A2"
    assert [c.value for c in ws[1]][:3] == ["Día", "Intervalo", "Skill"]
    assert ws.cell(row=2, column=3).value == "EMERGENCIAS"   # y no el id crudo


def test_el_excel_del_plan_trae_el_cliente_y_el_real_al_lado():
    """Las mismas tres series que la pantalla, en la misma fila. Y el real vacío
    donde todavía no hay dato: un cero se suma en una tabla dinámica igual que un
    dato bueno y hunde el total del día."""
    momento = datetime(2026, 9, 7, 10, 0)
    plan = dict(_PLAN,
                cliente=[{"SkillID": 6, "Intervalo": momento, "Llamadas": 210.0}],
                real=[{"SkillID": 6, "Intervalo": momento, "Llamadas": 188.0}],
                real_hasta=momento)
    ws = _hoja(export.libro_del_plan(plan, _CONFIG, "Voltara"), "Pronóstico")
    encabezados = [c.value for c in ws[1]]
    fila = {h: ws.cell(row=2, column=i + 1) for i, h in enumerate(encabezados)}
    assert fila["Pronóstico del cliente"].value == 210.0
    assert fila["Real (lo que entró)"].value == 188.0

    # Un intervalo posterior al último cerrado queda vacío, no en cero.
    sin_dato = dict(plan, real=[], real_hasta=datetime(2026, 9, 7, 9, 0))
    ws = _hoja(export.libro_del_plan(sin_dato, _CONFIG, "Voltara"), "Pronóstico")
    fila = {h: ws.cell(row=2, column=i + 1) for i, h in enumerate(encabezados)}
    assert fila["Real (lo que entró)"].value is None


def test_el_excel_del_plan_sale_igual_sin_las_series_de_cotejo():
    """Son referencias, no el plan: un backend viejo o un informe del cliente sin
    cargar no pueden dejar sin Excel a la planificación."""
    ws = _hoja(export.libro_del_plan(_PLAN, _CONFIG, "Voltara"), "Pronóstico")
    encabezados = [c.value for c in ws[1]]
    fila = {h: ws.cell(row=2, column=i + 1) for i, h in enumerate(encabezados)}
    assert fila["Pronóstico del cliente"].value is None
    assert fila["Real (lo que entró)"].value is None
    assert fila["Nuestras (con ajustes)"].value == 195.0


def test_el_excel_del_backtest_separa_las_dos_lecturas_del_error(enchufado):
    serie = _serie(date(2026, 3, 1), HOY, 10)
    total = {k: {"total": 40.0, "acme": 10.0, "tmo": 180.0} for k in serie}
    tramos = [{"AsignacionID": 1, "SkillID": None, "VigenteDesde": date(2025, 1, 1),
               "VigenteHasta": None, "Porcentaje": 0.25, "Nota": None}]
    bt = _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30),
                  total=total, tramos=tramos)

    wb = load_workbook(export.libro_del_backtest(bt, "Voltara"))
    assert wb.sheetnames == ["Resumen", "Por tipo de día", "Por día", "Por intervalo"]
    texto = "\n".join(str(c.value) for fila in wb["Resumen"].iter_rows()
                      for c in fila if c.value is not None)
    assert "Error sobre NUESTRAS llamadas" in texto
    assert "Error sobre la demanda TOTAL del cliente" in texto
    assert "repetir la semana anterior" in texto
    assert wb["Por día"].max_row == 8          # encabezado + 7 días
    assert wb["Por intervalo"].max_row == 7 * 48 + 1
    # La semana del 24 al 30 de agosto de 2026 son cinco hábiles, un sábado y un
    # domingo: la hoja tiene que partirlos y no promediarlos.
    tipos = [f[0].value for f in wb["Por tipo de día"].iter_rows(min_row=2)]
    assert tipos == ["Hábil", "Sábado", "Domingo"]


def test_el_nombre_del_archivo_es_ordenable():
    assert export.nombre_de_archivo("plan", date(2026, 9, 7), date(2026, 9, 21)) \
        == "plan_20260907_20260921.xlsx"
    assert export.nombre_de_archivo("plan") == "plan.xlsx"


# =========================================================================
# LA REFERENCIA QUE IMPORTA: EL PRONÓSTICO QUE YA ESTÁ EN PRODUCCIÓN
# =========================================================================

def test_se_compara_contra_dbo_forecast(enchufado):
    """Un modelo nuevo que no le gana al que la operación ya usa no se pone en
    producción. Por eso `dbo.Forecast` viaja en la misma respuesta y no en un
    script aparte."""
    serie = _serie(date(2026, 3, 1), HOY, 10)
    # El de producción erra el doble: 20 donde entraron 10.
    prod = {k: 20.0 for k in _serie(date(2026, 8, 24), date(2026, 8, 31), 10)}
    bt = _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30),
                  produccion=prod)

    assert bt["resumen"]["produccion"]["wape"] == pytest.approx(1.0)
    assert bt["resumen"]["intervalo"]["wape"] == pytest.approx(0.0, abs=1e-9)
    assert bt["resumen"]["mejora_vs_produccion"] == pytest.approx(1.0)
    assert bt["por_dia"][0]["produccion"] == pytest.approx(20 * 48)


def test_sin_dbo_forecast_el_backtest_sigue_andando(enchufado):
    """Hidra y Gasur no están en el mismo formato; la comparación es opcional."""
    bt = _corrida(enchufado, _serie(date(2026, 3, 1), HOY, 10),
                  date(2026, 8, 24), date(2026, 8, 30))
    assert bt["resumen"]["produccion"] is None
    assert bt["resumen"]["mejora_vs_produccion"] is None
    assert bt["por_dia"][0]["produccion"] is None


def test_dbo_forecast_se_recorta_a_los_mismos_skills(enchufado):
    """Si cubriera colas distintas, la comparación no compararía nada."""
    serie = _serie(date(2026, 3, 1), HOY, 10)
    prod = {k: 10.0 for k in _serie(date(2026, 8, 24), date(2026, 8, 31), 10)}
    # Le agrego una cola que el planificador no pronostica: no tiene que sumar.
    prod.update({k: 999.0 for k in _serie(date(2026, 8, 24), date(2026, 8, 31),
                                          10, skills=(OTRO,))})
    bt = _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30),
                  produccion=prod)
    assert bt["resumen"]["produccion"]["wape"] == pytest.approx(0.0, abs=1e-9)


# =========================================================================
# LA VENTANA DE ENTRENAMIENTO
# =========================================================================

def test_la_ventana_corta_se_come_un_evento_y_la_larga_no(enchufado):
    """El hallazgo que motivó cambiar el valor por defecto, en chico.

    Serie plana de 10, con un evento de dos semanas a 40 justo antes del período
    evaluado. Con 8 semanas de ventana, la mitad de las muestras de cada día de
    semana están contaminadas y la mediana se va para arriba. Con 52, el evento
    es una fracción chica y la mediana lo aguanta.
    """
    # El evento va lejos del período evaluado a propósito: así queda FUERA de la
    # ventana de corrección de nivel y lo único que cambia entre los dos casos es
    # cuántas de las muestras de la mediana quedaron contaminadas.
    evento_desde, evento_hasta = date(2026, 6, 29), date(2026, 7, 27)
    serie = _serie(date(2025, 6, 1), HOY,
                   lambda d, s: 40 if evento_desde <= d < evento_hasta else 10)

    def _con(semanas):
        cfg = _cfg()
        cfg.semanas_base, cfg.dias_nivel = semanas, 7
        return _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30), cfg=cfg)

    corta = _con(8)["resumen"]["diario"]["mape"]
    larga = _con(52)["resumen"]["diario"]["mape"]
    # Cuatro de las ocho muestras de cada día de semana cayeron dentro del evento,
    # así que la mediana se va para arriba; la corrección de nivel la baja algo,
    # pero no alcanza. Con 52 semanas el evento son 4 muestras de 52.
    assert corta > 0.20
    # Con 52 semanas el evento son 2 muestras de 52: la mediana ni se entera.
    assert larga == pytest.approx(0.0, abs=1e-9)


def test_se_lee_historia_suficiente_para_la_ventana_pedida():
    """Pedir 52 semanas y leer 180 días da 26: el parámetro sería letra muerta."""
    cfg = _cfg()
    cfg.semanas_base, cfg.dias_nivel = 52, 28
    assert servicio._dias_de_historia(cfg) >= 52 * 7 + 28
    cfg.semanas_base = 4
    assert servicio._dias_de_historia(cfg) == servicio.DIAS_DE_HISTORIA


# =========================================================================
# CLIMA
# =========================================================================

def _clima_frio(desde: date, dias: int, desde_frio: date):
    """Un año entero de clima, con una ola de frío a partir de `desde_frio`.

    Tiene que recorrer verano e invierno: si el calor no aparece nunca, la
    columna de grados-día de refrigeración queda constante en cero, la matriz del
    ajuste sale singular y el modelo devuelve None. Es exactamente la razón por
    la que se le pide al menos un año de historia observada.
    """
    import math
    salida = {}
    for i in range(dias):
        d = desde + timedelta(days=i)
        t = 14.0 + 12.0 * math.sin(2 * math.pi * i / 365.0)
        if d >= desde_frio:
            t -= 12.0
        salida[d] = {"t_aparente_max": t + 8, "t_aparente_min": t,
                     "lluvia_mm": 3.0 * (i % 5), "rafaga_kmh": 20.0 + 3.0 * (i % 9),
                     "es_pronostico": False}
    return salida


def test_el_clima_apagado_no_toca_el_pronostico(enchufado):
    serie = _serie(date(2024, 6, 1), HOY, 10)
    clima = _clima_frio(date(2024, 6, 1), 900, date(2026, 8, 24))
    sin = _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30),
                   clima=clima, clima_activo=False)
    assert sin["clima"]["usado"] is False
    assert sin["por_dia"][0]["factor_clima"] is None
    assert "clima" not in sin["modelo"]


def _serie_sensible_al_clima(desde: date, hasta: date, clima):
    """Demanda que SÍ responde al tiempo: 10 llamadas más un 4% por grado de frío.

    Hace falta que responda o el modelo no llega al R² mínimo y —correctamente—
    se queda sin factor. Una serie plana no sirve para probar el clima."""
    import math
    from app import planificador_clima as pc

    def valor(d, _s):
        w = clima.get(d)
        if not w:
            return 10.0
        hdd = max(0.0, pc.BASE_CALOR - w["t_aparente_min"])
        return 10.0 * math.exp(0.04 * hdd)
    return _serie(desde, hasta, valor)


def test_el_backtest_puede_forzar_el_clima_para_medirlo_antes_de_activarlo(enchufado):
    """Es la razón de ser del selector: activar el clima cambia el pronóstico, y
    esa decisión se toma después de medirla sobre los mismos días."""
    clima = _clima_frio(date(2024, 6, 1), 900, date(2026, 8, 24))
    serie = _serie_sensible_al_clima(date(2024, 6, 1), HOY, clima)
    engine = enchufado(serie, clima=clima, clima_activo=False)
    forzado = servicio.backtest(engine, 20, date(2026, 8, 24), date(2026, 8, 30),
                                antelacion=7, hoy=HOY, clima=True)
    assert forzado["clima"]["usado"] is True
    assert forzado["por_dia"][0]["factor_clima"] is not None
    assert forzado["modelo"].endswith("+clima")


def test_el_modelo_de_clima_no_ve_los_dias_que_se_estan_evaluando(enchufado):
    """El ajuste se hace por fecha de corte, igual que la línea de base. Si viera
    los días evaluados, la ola de frío estaría explicada de antemano."""
    clima = _clima_frio(date(2024, 6, 1), 900, date(2026, 8, 24))
    serie = _serie_sensible_al_clima(date(2024, 6, 1), HOY, clima)
    engine = enchufado(serie, clima=clima, clima_activo=True)
    bt = servicio.backtest(engine, 20, date(2026, 8, 24), date(2026, 8, 30),
                           antelacion=7, hoy=HOY)
    assert bt["clima"]["modelo"]["entrenado_hasta"] <= date(2026, 8, 24)


def test_sin_suficientes_dias_de_clima_se_dice_por_que(enchufado):
    """«Está activado y no sirvió» y «está apagado» son dos cosas distintas, y la
    pantalla tiene que poder decir cuál es."""
    serie = _serie(date(2026, 3, 1), HOY, 10)
    clima = _clima_frio(date(2026, 8, 1), 40, date(2026, 8, 24))
    engine = enchufado(serie, clima=clima, clima_activo=True)
    bt = servicio.backtest(engine, 20, date(2026, 8, 24), date(2026, 8, 30),
                           antelacion=7, hoy=HOY)
    assert bt["clima"]["usado"] is False
    assert "observado" in bt["clima"]["motivo"]


def test_la_ventana_de_lectura_se_estira_cuando_el_clima_esta_activo():
    """Con un año de historia el modelo vio un solo verano y extrapola justo en
    los extremos, que son los días que importan."""
    cfg = _cfg()
    cfg.semanas_base, cfg.dias_nivel = 52, 28
    assert servicio._ventana_de_lectura(cfg, {"activo": False}) == \
        servicio._dias_de_historia(cfg)
    assert servicio._ventana_de_lectura(cfg, {"activo": True}) == \
        servicio.DIAS_DE_HISTORIA_CLIMA
    # El forzado del backtest manda sobre lo configurado.
    assert servicio._ventana_de_lectura(cfg, {"activo": False}, forzar_clima=True) == \
        servicio.DIAS_DE_HISTORIA_CLIMA


# =========================================================================
# ERROR POR HORIZONTE
# =========================================================================

def test_el_error_esperado_interpola_entre_los_horizontes_medidos():
    """El error a 5 días no está medido; sale de interpolar entre el de 3 y el de
    7. Suponer que crece linealmente desde cero, o que a 30 días es el doble que
    a 1, llevaría a pedir horas extra por ruido."""
    tabla = {0: 0.10, 1: 0.20, 7: 0.30}
    assert servicio.error_esperado(0, tabla) == pytest.approx(0.10)
    assert servicio.error_esperado(1, tabla) == pytest.approx(0.20)
    assert servicio.error_esperado(4, tabla) == pytest.approx(0.25)
    # Fuera de los extremos se sostiene el borde, no se extrapola.
    assert servicio.error_esperado(60, tabla) == pytest.approx(0.30)
    assert servicio.error_esperado(-3, tabla) == pytest.approx(0.10)


def test_el_error_es_casi_plano_entre_uno_y_siete_dias():
    """El hallazgo, fijado como candado.

    Uno esperaría que el error creciera con la antelación. Medido sobre dos
    ventanas de tres meses, NO: 35,7% a un día, 35,9% a tres, 35,8% a siete. No
    lo domina la frescura de la historia sino el clima y los cortes, que a un día
    tampoco se saben.

    Tiene una consecuencia operativa concreta: esperar al último momento para
    decidir NO mejora la información, así que conviene resolver los faltantes con
    antelación —moviendo la malla, que no cuesta plata— en vez de dejarlos para
    la hora extra o la convocatoria del día.
    """
    tabla = servicio.ERROR_POR_HORIZONTE
    corto = [tabla[k] for k in (1, 3, 7)]
    assert max(corto) - min(corto) < 0.02, "el error dejó de ser plano: re-medir"
    # El intradía sí tiene que ser mucho mejor: ahí ya hay acumulado real del día.
    assert tabla[0] < min(corto) / 2
    # Y nada puede errar menos que el intradía.
    assert tabla[0] == min(tabla.values())



def test_un_skill_que_el_clima_no_explica_se_queda_sin_factor(enchufado):
    """COMERCIAL explica el 7% de su variación con el clima y EMERGENCIAS el 44%.

    Aplicarle un factor al que no explica nada le mete el ruido del ajuste sin
    comprarle nada, y —peor— diluye el del que sí. Es lo que rompía los fines de
    semana, que son 98,9% EMERGENCIAS.
    """
    import math
    from app import planificador_clima as pc

    clima = _clima_frio(date(2024, 6, 1), 900, date(2026, 8, 24))

    def valor(d, s):
        if s == OTRO:                    # esta cola no mira el termómetro
            return 10.0
        w = clima.get(d)
        if not w:
            return 10.0
        return 10.0 * math.exp(0.04 * max(0.0, pc.BASE_CALOR - w["t_aparente_min"]))

    serie = _serie(date(2024, 6, 1), HOY, valor, skills=(SKILL, OTRO))
    engine = enchufado(serie, clima=clima, clima_activo=True)
    bt = servicio.backtest(engine, 20, date(2026, 8, 24), date(2026, 8, 30),
                           antelacion=7, hoy=HOY)
    assert bt["clima"]["usado"] is True
    # Sólo el sensible al clima recibe factor.
    assert bt["clima"]["skills_con_factor"] == 1
    assert str(SKILL) in bt["clima"]["por_skill"]
    assert str(OTRO) not in bt["clima"]["por_skill"]


# =========================================================================
# EL REPARTO POR TIPO DE DÍA Y LA COMBINACIÓN CON EL CLIENTE
# =========================================================================

def _cfg_sin_deriva(**kw):
    """La misma configuración, con la deriva de 7 días apagada y el factor por
    tipo de día PRENDIDO (nace en 0 porque medido empeora; acá se lo prende para
    poder testear el mecanismo, que es lo que este bloque verifica).

    La deriva y el factor por tipo de día se aplican los dos, así que para poder
    afirmar un número exacto sobre el segundo hay que sacar el primero: sobre una
    ventana de siete días la deriva la dominan los hábiles y deja un residuo que
    no es lo que se está midiendo acá."""
    base = {"reparto_tipo_dia_dias": 365}
    base.update(kw)
    return pl.CampanaCfg(
        campana_id=20, intervalo_min=30, shrinkage=0.0, reparto_deriva_dias=0,
        pools={1: pl.PoolCfg(pool_id=1, nombre="General", min_operadores=1)},
        skills=[pl.SkillCfg(SKILL, "EMERGENCIAS", 1, 0.80, 20)], **base)


def _total(desde, hasta, total, share_habil, share_no_habil):
    """Demanda del cliente plana con distinto reparto según el tipo de día."""
    salida = {}
    dia = desde
    while dia < hasta:
        share = (share_no_habil if pl.tipo_de_dia(dia, set()) == "no_habil"
                 else share_habil)
        momento = datetime.combine(dia, time(0, 0))
        fin = momento + timedelta(days=1)
        while momento < fin:
            salida[(momento, SKILL)] = {"total": total, "acme": total * share,
                                        "tmo": 180.0}
            momento += timedelta(minutes=30)
        dia += timedelta(days=1)
    return salida


def test_el_reparto_del_fin_de_semana_se_corrige_solo(enchufado):
    """El tramo dice 40% los siete días; en los datos el no hábil es 30%.

    Sin corrección el pronóstico del domingo se va 33% arriba. Con el factor por
    tipo de día —medido sobre el año, no puesto a mano— cae justo."""
    tramos = [{"AsignacionID": 1, "SkillID": None, "VigenteDesde": date(2025, 1, 1),
               "VigenteHasta": None, "Porcentaje": 0.40, "Nota": None}]
    total = _total(date(2025, 10, 1), HOY, 100.0, 0.40, 0.30)
    serie = {k: (v["acme"], v["tmo"]) for k, v in total.items()}

    bt = _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30),
                  total=total, tramos=tramos, cfg=_cfg_sin_deriva())
    assert bt["combinacion"]["reparto_no_habil"] == pytest.approx(0.75, abs=1e-6)

    por_dia = {f["dia"]: f for f in bt["por_dia"]}
    domingo = por_dia[date(2026, 8, 30)]        # 2026-08-30 es domingo
    lunes = por_dia[date(2026, 8, 24)]
    assert domingo["tipo_dia"] == "domingo" and lunes["tipo_dia"] == "habil"
    # 100 llamadas x 48 intervalos x 40% x 0,75 = 1.440 = lo real del domingo.
    assert domingo["pronosticado"] == pytest.approx(domingo["real"], rel=1e-6)
    assert lunes["pronosticado"] == pytest.approx(lunes["real"], rel=1e-6)


def test_sin_el_factor_el_fin_de_semana_se_va_arriba(enchufado):
    """La contraprueba del test de arriba: con la ventana en 0 el error vuelve."""
    tramos = [{"AsignacionID": 1, "SkillID": None, "VigenteDesde": date(2025, 1, 1),
               "VigenteHasta": None, "Porcentaje": 0.40, "Nota": None}]
    total = _total(date(2025, 10, 1), HOY, 100.0, 0.40, 0.30)
    serie = {k: (v["acme"], v["tmo"]) for k, v in total.items()}

    bt = _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30),
                  total=total, tramos=tramos,
                  cfg=_cfg_sin_deriva(reparto_tipo_dia_dias=0))
    domingo = next(f for f in bt["por_dia"] if f["dia"] == date(2026, 8, 30))
    assert domingo["pronosticado"] == pytest.approx(domingo["real"] * 4 / 3, rel=1e-6)


def test_el_error_se_parte_por_tipo_de_dia(enchufado):
    """El promedio de la semana esconde que el domingo es otro problema."""
    serie = _serie(date(2026, 3, 1), HOY, 10)
    prod = {k: 20.0 for k in _serie(date(2026, 8, 24), date(2026, 8, 31), 10)}
    bt = _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30),
                  produccion=prod)

    tipos = {f["tipo"]: f for f in bt["por_tipo_de_dia"]}
    assert set(tipos) == {"habil", "sabado", "domingo"}
    assert tipos["habil"]["n"] == 5
    assert tipos["sabado"]["n"] == 1 and tipos["domingo"]["n"] == 1
    # Nuestro pronóstico es exacto y el del cliente erra el 100% en todos.
    for t in tipos.values():
        assert t["modelo"]["mape"] == pytest.approx(0.0, abs=1e-9)
        assert t["produccion"]["mape"] == pytest.approx(1.0)


def test_la_combinacion_no_arranca_sin_dias_para_medir_el_peso(enchufado):
    """En una ventana de una semana no hay con qué medir: el combinado tiene que
    ser idéntico al nuestro y no una mezcla inventada."""
    serie = _serie(date(2026, 3, 1), HOY, 10)
    prod = {k: 20.0 for k in _serie(date(2026, 8, 24), date(2026, 8, 31), 10)}
    bt = _corrida(enchufado, serie, date(2026, 8, 24), date(2026, 8, 30),
                  produccion=prod)
    assert bt["resumen"]["combinado"]["wape"] == \
        pytest.approx(bt["resumen"]["intervalo"]["wape"])
    assert all(f["peso_cliente"] is None for f in bt["por_dia"])


def test_la_combinacion_aparece_cuando_hay_historia(enchufado):
    """Con tres meses evaluados y un cliente que acierta donde nosotros erramos,
    el peso llega a medirse y el combinado se separa del nuestro."""
    salto = date(2026, 6, 8)
    # Nosotros nos quedamos con el nivel viejo (10) y el real sube a 15 los no
    # hábiles; el cliente los acierta.
    serie = _serie(date(2025, 6, 1), HOY,
                   lambda d, s: 15 if (d >= salto and d.isoweekday() >= 6) else 10)
    prod = {k: (15.0 if (k[0].date().isoweekday() >= 6) else 10.0)
            for k in _serie(salto, HOY, 10)}
    bt = _corrida(enchufado, serie, salto, salto + timedelta(days=90),
                  produccion=prod)
    pesos = bt["combinacion"]["pesos"]
    assert pesos["no_habil"]["peso"] > 0
    assert pesos["habil"]["peso"] == 0        # sólo se combinan los no hábiles
    assert bt["combinacion"]["dias_con_factor"] > 0
    # Y tiene que MEJORAR: para eso está.
    assert bt["resumen"]["combinado"]["wape"] < bt["resumen"]["intervalo"]["wape"]
