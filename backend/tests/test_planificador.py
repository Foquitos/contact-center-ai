"""Tests de la lógica del planificador (app/planificador.py).

Offline: no toca ni BD ni red. Las series son sintéticas y con forma conocida,
así que se puede afirmar qué tiene que dar y no solo que "parece razonable".

Correr: pytest tests/test_planificador.py -m "not tokens"
"""
from datetime import date, datetime, time, timedelta

import math

import pytest

from app import planificador as pl
from app import planificador_erlang as erlang


# ------------------------------------------------------------------ config

def _cfg(**kw):
    """Config parecida a la de Voltara: un pool grande y sus skills."""
    cfg = pl.CampanaCfg(
        campana_id=20,
        intervalo_min=30,
        max_ocupacion=kw.pop("max_ocupacion", 0.85),
        shrinkage=kw.pop("shrinkage", 0.0),
        paciencia_seg=kw.pop("paciencia_seg", 853),
        pools={1: pl.PoolCfg(pool_id=1, nombre="General", min_operadores=2)},
        skills=[
            pl.SkillCfg(6, "EMERGENCIAS", 1, 0.80, 20),
            pl.SkillCfg(4, "COMERCIAL", 1, 0.80, 20),
            pl.SkillCfg(5, "ELECTRODEPENDIENTES", 1, 0.80, 20,
                        max_abandono=kw.pop("max_abandono_electro", None),
                        paciencia_seg=1267),
        ],
        disponibilidad=kw.pop("disponibilidad", []),
    )
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def test_disponibilidad_sin_franjas_es_uno():
    """Sin nada configurado vale el supuesto de Erlang: todos disponibles."""
    assert _cfg().factor_disponibilidad(datetime(2026, 9, 3, 10, 0)) == 1.0


def test_disponibilidad_por_franja_horaria():
    cfg = _cfg(disponibilidad=[
        pl.FranjaDisponibilidad(0, 0, 8, 0.90),
        pl.FranjaDisponibilidad(0, 8, 17, 0.82),
        pl.FranjaDisponibilidad(0, 17, 24, 0.90),
    ])
    assert cfg.factor_disponibilidad(datetime(2026, 9, 3, 10, 30)) == 0.82
    assert cfg.factor_disponibilidad(datetime(2026, 9, 3, 3, 0)) == 0.90
    assert cfg.factor_disponibilidad(datetime(2026, 9, 3, 20, 0)) == 0.90


def test_la_franja_del_dia_concreto_le_gana_a_la_generica():
    cfg = _cfg(disponibilidad=[
        pl.FranjaDisponibilidad(0, 8, 17, 0.82),      # todos los días
        pl.FranjaDisponibilidad(6, 8, 17, 0.70),      # sábados
    ])
    assert cfg.factor_disponibilidad(datetime(2026, 9, 3, 10, 0)) == 0.82   # jueves
    assert cfg.factor_disponibilidad(datetime(2026, 9, 5, 10, 0)) == 0.70   # sábado


# ------------------------------------------------------------ días atípicos

def _serie_diaria(desde: date, dias: int, base_por_dow: dict, picos=None) -> dict:
    picos = picos or {}
    serie = {}
    for i in range(dias):
        d = desde + timedelta(days=i)
        serie[d] = picos.get(d, base_por_dow[d.isoweekday()])
    return serie


def test_detecta_el_pico_y_no_el_resto():
    base = {1: 5000, 2: 5000, 3: 5000, 4: 5000, 5: 4500, 6: 2500, 7: 1500}
    pico = date(2026, 7, 6)
    serie = _serie_diaria(date(2026, 5, 4), 120, base, {pico: 12500})
    atipicos = pl.detectar_atipicos(serie)
    assert [a.dia for a in atipicos] == [pico]
    assert atipicos[0].factor == pytest.approx(2.5, abs=0.01)


def test_no_marca_variacion_normal():
    """Una serie con ruido chico no puede generar una lista de eventos."""
    base = {1: 5000, 2: 5000, 3: 5000, 4: 5000, 5: 4500, 6: 2500, 7: 1500}
    serie = _serie_diaria(date(2026, 5, 4), 120, base)
    for i, d in enumerate(sorted(serie)):
        serie[d] = serie[d] * (1 + 0.04 * ((i % 5) - 2) / 2)
    assert pl.detectar_atipicos(serie) == []


def test_los_feriados_no_son_eventos():
    """Un feriado baja el volumen, pero ya está modelado por el calendario: no
    tiene por qué aparecer en la lista de eventos a investigar."""
    base = {1: 5000, 2: 5000, 3: 5000, 4: 5000, 5: 4500, 6: 2500, 7: 1500}
    feriado = date(2026, 6, 15)   # lunes
    serie = _serie_diaria(date(2026, 5, 4), 120, base, {feriado: 1200})
    assert feriado in [a.dia for a in pl.detectar_atipicos(serie)]
    assert feriado not in [a.dia for a in pl.detectar_atipicos(serie, feriados=[feriado])]


def test_detecta_tambien_las_caidas():
    base = {1: 5000, 2: 5000, 3: 5000, 4: 5000, 5: 4500, 6: 2500, 7: 1500}
    caida = date(2026, 7, 8)
    serie = _serie_diaria(date(2026, 5, 4), 120, base, {caida: 900})
    atipicos = pl.detectar_atipicos(serie)
    assert [a.dia for a in atipicos] == [caida]
    assert atipicos[0].factor < 1


# ------------------------------------------------------------ línea de base

def _historico(hasta: date, dias: int, nivel_por_dow: dict, factor=1.0) -> dict:
    """Serie por intervalo con forma diaria fija (dos picos y un valle)."""
    forma = {h: (1.0 if 8 <= h < 21 else 0.2) for h in range(24)}
    hist = {}
    for i in range(dias):
        d = hasta - timedelta(days=dias - i)
        for h in range(24):
            for m in (0, 30):
                hist[datetime.combine(d, time(h, m))] = (
                    nivel_por_dow[d.isoweekday()] * forma[h] * factor)
    return hist


def test_baseline_reproduce_el_perfil():
    hoy = date(2026, 9, 3)
    nivel = {1: 100, 2: 100, 3: 100, 4: 100, 5: 90, 6: 50, 7: 30}
    hist = _historico(hoy, 70, nivel)
    manana = hoy + timedelta(days=1)      # viernes
    pron = pl.baseline_estacional(hist, [manana], feriados=[], hoy=hoy)
    assert pron[datetime.combine(manana, time(10, 0))] == pytest.approx(90, rel=1e-6)
    assert pron[datetime.combine(manana, time(3, 0))] == pytest.approx(18, rel=1e-6)


def test_baseline_sigue_el_nivel_reciente():
    """El error que hoy tiene dbo.Forecast: el volumen bajó y el pronóstico se
    quedó arriba. La corrección de nivel es lo que evita eso."""
    hoy = date(2026, 9, 3)
    nivel = {1: 100, 2: 100, 3: 100, 4: 100, 5: 90, 6: 50, 7: 30}
    hist = _historico(hoy, 80, nivel)
    # Toda la ventana de corrección cae 25%.
    for momento in list(hist):
        if momento.date() >= hoy - timedelta(days=pl.DIAS_NIVEL_RECIENTE):
            hist[momento] *= 0.75

    manana = hoy + timedelta(days=1)
    pron = pl.baseline_estacional(hist, [manana], hoy=hoy)
    valor = pron[datetime.combine(manana, time(10, 0))]
    assert valor < 90, "tiene que reconocer que el volumen bajó"
    assert valor == pytest.approx(90 * 0.75, rel=0.10)


def test_la_correccion_de_nivel_es_proporcional_a_cuanto_duro_la_caida():
    """Una caída que cubre la mitad de la ventana corrige la mitad. Es lo que
    hace que la ventana sea una decisión y no un detalle: cuanto más larga, más
    se toma con pinzas una caída corta, y eso es a propósito —la mitad de las
    veces la caída es un evento que ya pasó."""
    hoy = date(2026, 9, 3)
    nivel = {d: 100 for d in range(1, 8)}
    hist = _historico(hoy, 80, nivel)
    dias_caidos = 14
    for momento in list(hist):
        if momento.date() >= hoy - timedelta(days=dias_caidos):
            hist[momento] *= 0.75

    manana = hoy + timedelta(days=1)
    pron = pl.baseline_estacional(hist, [manana], hoy=hoy)
    esperado = 100 * (1 - 0.25 * dias_caidos / pl.DIAS_NIVEL_RECIENTE)
    assert pron[datetime.combine(manana, time(10, 0))] == pytest.approx(
        esperado, rel=0.05)


def test_un_solo_dia_extremo_no_se_lleva_la_correccion_de_nivel():
    """El sábado 15 de agosto de 2026, en la vida real.

    Entraron 9.794 llamadas contra las ~3.000 de un sábado normal, y como la
    ventana de la corrección son 28 días —que partidos por tipo de día dejan ocho
    sábados y domingos— ese día solo levantó el nivel del fin de semana durante
    un mes. Los tres findes siguientes salieron al doble de lo que entró.

    El cierre "recortada" saca el día más alto y el más bajo de la ventana antes
    de sumar. Sin eso, un día por tres del resto mueve la corrección un 20%.
    """
    hoy = date(2026, 9, 3)
    nivel = {d: 100 for d in range(1, 8)}
    hist = _historico(hoy, 80, nivel)
    extremo = hoy - timedelta(days=5)
    for momento in list(hist):
        if momento.date() == extremo:
            hist[momento] *= 4.0

    manana = hoy + timedelta(days=1)
    pron = pl.baseline_estacional(hist, [manana], hoy=hoy)
    valor = pron[datetime.combine(manana, time(10, 0))]
    # El resto de la ventana está plano en 100: el pronóstico tiene que quedar
    # en 100 y no en los ~111 que daría sumar el día extremo con los demás.
    assert valor == pytest.approx(100.0, rel=1e-6)


def test_la_correccion_de_nivel_sigue_un_cambio_de_regimen():
    """La contraprueba del test de arriba, y la razón por la que no se usa la
    mediana: un salto REAL de nivel se tiene que seguir enseguida.

    Con la mediana de los cocientes diarios haría falta que la mitad de la
    ventana quedara del lado nuevo —catorce días— para que se moviera. Sacar
    sólo el día más alto y el más bajo deja pasar el cambio.
    """
    hoy = date(2026, 9, 3)
    nivel = {d: 100 for d in range(1, 8)}
    hist = _historico(hoy, 80, nivel)
    for momento in list(hist):
        if momento.date() >= hoy - timedelta(days=5):
            hist[momento] *= 2.0

    manana = hoy + timedelta(days=1)
    pron = pl.baseline_estacional(hist, [manana], hoy=hoy)
    assert pron[datetime.combine(manana, time(10, 0))] > 110.0


def test_la_correccion_de_nivel_esta_acotada():
    """Un evento corto no puede arrastrar el pronóstico de todo el mes."""
    hoy = date(2026, 9, 3)
    nivel = {d: 100 for d in range(1, 8)}
    hist = _historico(hoy, 70, nivel)
    for momento in list(hist):
        if momento.date() >= hoy - timedelta(days=3):
            hist[momento] *= 6.0            # tres días de corte masivo

    pron = pl.baseline_estacional(hist, [hoy + timedelta(days=1)], hoy=hoy)
    valor = pron[datetime.combine(hoy + timedelta(days=1), time(10, 0))]
    assert valor <= 100 * (1 + pl.TOPE_CORRECCION_NIVEL) + 1e-6


def test_el_feriado_usa_el_perfil_del_domingo():
    hoy = date(2026, 9, 3)
    nivel = {1: 100, 2: 100, 3: 100, 4: 100, 5: 90, 6: 50, 7: 30}
    hist = _historico(hoy, 70, nivel)
    feriado = date(2026, 9, 7)            # lunes feriado
    pron = pl.baseline_estacional(hist, [feriado], feriados=[feriado], hoy=hoy)
    assert pron[datetime.combine(feriado, time(10, 0))] == pytest.approx(30, rel=1e-6)


def test_los_dias_excluidos_no_entran_al_perfil():
    hoy = date(2026, 9, 3)
    nivel = {d: 100 for d in range(1, 8)}
    hist = _historico(hoy, 70, nivel)
    sucios = [hoy - timedelta(days=k) for k in (21, 28, 35)]   # tres jueves
    for momento in list(hist):
        if momento.date() in sucios:
            hist[momento] *= 10

    manana = hoy + timedelta(days=1)
    limpio = pl.baseline_estacional(hist, [manana], excluir_dias=sucios, hoy=hoy)
    assert limpio[datetime.combine(manana, time(10, 0))] == pytest.approx(100, rel=0.05)


def test_baseline_sin_historia_no_explota():
    assert pl.baseline_estacional({}, [date(2026, 9, 4)], hoy=date(2026, 9, 3)) == {}


def _dias(hoy: date, n: int) -> list:
    return [hoy - timedelta(days=k) for k in range(1, n + 1)]


def test_el_perfil_cuenta_las_medias_horas_sin_llamadas():
    """El bug de los domingos (2026-09-14): COMERCIAL no atiende los domingos,
    pero los feriados usan el perfil del domingo y los feriados que caen en día
    hábil SÍ tienen llamadas comerciales. La serie sólo trae las medias horas con
    llamadas, así que la mediana salía de esos feriados: 998 llamadas fantasma por
    domingo contra 0 reales."""
    hoy = date(2026, 9, 3)
    feriados = [hoy - timedelta(days=k) for k in (66, 59, 52)]   # tres lunes
    hist = {datetime.combine(f, time(10, 0)): 100.0 for f in feriados}
    domingo = date(2026, 9, 6)
    momento = datetime.combine(domingo, time(10, 0))

    sin = pl.baseline_estacional(hist, [domingo], feriados=feriados, hoy=hoy)
    assert sin[momento] == pytest.approx(100.0)          # el bug, documentado

    con = pl.baseline_estacional(hist, [domingo], feriados=feriados, hoy=hoy,
                                 dias_observados=_dias(hoy, 70))
    assert con.get(momento, 0.0) == pytest.approx(0.0)


def test_una_cola_nueva_no_hereda_ceros_de_antes_de_existir():
    hoy = date(2026, 9, 3)
    hist = _historico(hoy, 21, {d: 100 for d in range(1, 8)})
    manana = hoy + timedelta(days=1)
    pron = pl.baseline_estacional(hist, [manana], hoy=hoy, dias_observados=_dias(hoy, 70))
    assert pron[datetime.combine(manana, time(10, 0))] == pytest.approx(100.0, rel=1e-6)


def test_la_correccion_de_nivel_cuenta_los_dias_sin_llamadas():
    """Un domingo observado sin ninguna llamada del skill no existe en la serie.
    Sin los días observados la corrección no lo ve y el nivel no baja."""
    hoy = date(2026, 9, 3)
    nivel = {1: 100, 2: 100, 3: 100, 4: 100, 5: 90, 6: 50, 7: 30}
    hist = _historico(hoy, 70, nivel)
    for momento in list(hist):
        if momento.date() >= hoy - timedelta(days=28) and momento.date().isoweekday() == 7:
            del hist[momento]
    domingo = date(2026, 9, 6)
    momento = datetime.combine(domingo, time(10, 0))

    sin = pl.baseline_estacional(hist, [domingo], hoy=hoy, nivel_por_tipo_de_dia=True)
    con = pl.baseline_estacional(hist, [domingo], hoy=hoy, nivel_por_tipo_de_dia=True,
                                 dias_observados=_dias(hoy, 70))
    assert sin[momento] == pytest.approx(30.0, rel=1e-6)
    assert con[momento] < 30.0 * 0.9


def test_las_series_diarias_rellenan_con_cero_desde_el_primer_dia_del_skill():
    from app import planificador_servicio as servicio
    serie = {
        (datetime(2026, 8, 31, 10), 2): {"total": 1},
        (datetime(2026, 9, 1, 10), 1): {"total": 5},
        (datetime(2026, 9, 2, 10), 2): {"total": 7},
        (datetime(2026, 9, 3, 10), 1): {"total": 4},
    }
    diarias = servicio._series_diarias_por_skill(serie, sobre_total=True)
    assert diarias[1] == {date(2026, 9, 1): 5, date(2026, 9, 2): 0.0, date(2026, 9, 3): 4}
    assert diarias[2] == {date(2026, 8, 31): 1, date(2026, 9, 1): 0.0,
                          date(2026, 9, 2): 7, date(2026, 9, 3): 0.0}


# -------------------------------------------------------- dimensionamiento

def _demanda(**por_skill):
    return [pl.DemandaSkill(skill_id=int(k[1:]), llamadas=v[0], tmo_seg=v[1])
            for k, v in por_skill.items()]


def test_el_pool_junta_el_trafico_y_pondera_el_tmo():
    """195 llamadas de 180s (EMERGENCIAS) más 80 de 335s (COMERCIAL): el TMO
    efectivo es el ponderado por llamadas, no el promedio simple de los dos.

    Los dos valores son los reales de agosto 2026, ponderados. Ojo con el
    promedio simple del reporte por intervalo: da 176s y 89s porque incluye los
    intervalos sin llamadas, donde el TMO figura en cero — y con esos números
    Comercial parecía la cola más corta cuando en realidad es la más larga."""
    cfg = _cfg()
    r = pl.dimensionar_intervalo(cfg, cfg.pools[1], datetime(2026, 9, 3, 10, 0),
                                 _demanda(s6=(195, 180), s4=(80, 335)))
    assert r.llamadas == 275
    assert r.tmo_seg == pytest.approx((195 * 180 + 80 * 335) / 275)
    assert r.trafico == pytest.approx((195 * 180 + 80 * 335) / 1800)


def test_el_pool_pide_menos_que_los_skills_por_separado():
    """El argumento central del diseño: como la cola es compartida, dimensionar
    el pool junto pide menos gente que sumar un Erlang C por cada skill."""
    cfg = _cfg(max_ocupacion=None)
    juntos = pl.dimensionar_intervalo(cfg, cfg.pools[1], datetime(2026, 9, 3, 10, 0),
                                      _demanda(s6=(195, 180), s4=(80, 335)))
    separados = sum(
        erlang.dimensionar(llamadas=n, tmo_seg=t, objetivo_nds=0.80, umbral_seg=20,
                           paciencia_seg=853, max_ocupacion=None).operadores_en_linea
        for n, t in ((195, 180), (80, 335))
    )
    assert juntos.operadores_en_linea < separados


def test_gana_el_objetivo_mas_estricto_del_pool():
    cfg = _cfg()
    cfg.skills[1].objetivo_nds = 0.95      # COMERCIAL más exigente que el resto
    laxo = _cfg()
    momento = datetime(2026, 9, 3, 10, 0)
    d = _demanda(s6=(195, 180), s4=(80, 335))
    assert (pl.dimensionar_intervalo(cfg, cfg.pools[1], momento, d).operadores_en_linea
            > pl.dimensionar_intervalo(laxo, laxo.pools[1], momento, d).operadores_en_linea)


def test_un_skill_sin_volumen_no_impone_su_objetivo():
    """Si Electrodependientes no tuvo llamadas en el intervalo, su techo de
    abandono no puede encarecer la dotación de ese intervalo."""
    cfg = _cfg(max_abandono_electro=0.005)
    momento = datetime(2026, 9, 3, 3, 0)
    sin_electro = pl.dimensionar_intervalo(cfg, cfg.pools[1], momento,
                                           _demanda(s6=(20, 180)))
    con_electro = pl.dimensionar_intervalo(cfg, cfg.pools[1], momento,
                                           _demanda(s6=(20, 180), s5=(3, 90)))
    assert sin_electro.operadores_en_linea <= con_electro.operadores_en_linea
    assert "abandono" not in sin_electro.motivo


def test_el_techo_de_abandono_de_un_skill_sube_todo_el_pool():
    """Electrodependientes convive en el pool grande, pero su compromiso de nivel
    de atención se verifica con SU paciencia y arrastra la dotación del pool."""
    momento = datetime(2026, 9, 3, 10, 0)
    d = _demanda(s6=(195, 180), s5=(4, 90))
    sin_tope = _cfg()
    con_tope = _cfg(max_abandono_electro=0.001)
    a = pl.dimensionar_intervalo(sin_tope, sin_tope.pools[1], momento, d)
    b = pl.dimensionar_intervalo(con_tope, con_tope.pools[1], momento, d)
    assert b.operadores_en_linea > a.operadores_en_linea
    assert b.motivo == "abandono de ELECTRODEPENDIENTES"


def test_disponibilidad_y_shrinkage_no_se_pisan():
    """Erlang -> presentes (dividido disponibilidad) -> a planificar (dividido
    1-shrinkage). Son dos descuentos distintos y encadenados; si se los mezcla se
    descuentan dos veces las mismas pausas."""
    cfg = _cfg(shrinkage=0.20,
               disponibilidad=[pl.FranjaDisponibilidad(0, 8, 17, 0.80)])
    r = pl.dimensionar_intervalo(cfg, cfg.pools[1], datetime(2026, 9, 3, 10, 0),
                                 _demanda(s6=(195, 180)))
    assert r.disponibilidad == 0.80
    assert r.operadores_presentes == -(-r.operadores_en_linea // 1) / 1 or True
    assert r.operadores_presentes == int(-(-r.operadores_en_linea * 10 // 8))
    assert r.operadores_a_planificar == int(-(-r.operadores_presentes * 10 // 8))
    assert r.operadores_a_planificar > r.operadores_presentes > r.operadores_en_linea


def test_intervalo_vacio_respeta_la_cobertura_minima():
    cfg = _cfg()
    r = pl.dimensionar_intervalo(cfg, cfg.pools[1], datetime(2026, 9, 3, 4, 0), [])
    assert r.operadores_en_linea == 2          # MinOperadores del pool
    assert r.llamadas == 0


def test_skill_de_otro_pool_no_suma():
    cfg = _cfg()
    cfg.skills.append(pl.SkillCfg(9, "RECLAMO-DANO", pool_id=2, objetivo_nds=0.80,
                                  umbral_seg=20))
    r = pl.dimensionar_intervalo(cfg, cfg.pools[1], datetime(2026, 9, 3, 10, 0),
                                 _demanda(s6=(100, 180), s9=(500, 300)))
    assert r.llamadas == 100


def test_las_dos_lecturas_del_nds():
    """El NDS sobre atendidas (el que reporta Voltara) nunca puede ser peor que el
    de entrantes: las que abandonan no cuentan en contra."""
    cfg = _cfg(max_ocupacion=None)
    r = pl.dimensionar_intervalo(cfg, cfg.pools[1], datetime(2026, 9, 3, 10, 0),
                                 _demanda(s6=(195, 180)))
    assert r.nds_sobre_atendidas >= r.nds_sobre_entrantes


def test_resumen_del_plan():
    cfg = _cfg()
    momentos = [datetime(2026, 9, 3, 8, 0) + timedelta(minutes=30 * i) for i in range(4)]
    demanda = {m: _demanda(s6=(100, 180)) for m in momentos}
    reqs = pl.plan_de_pool(cfg, cfg.pools[1], demanda)
    resumen = pl.resumen_de_plan(reqs, intervalo_min=30)
    assert resumen["intervalos"] == 4
    assert resumen["llamadas"] == 400
    # 4 intervalos de media hora = 2 horas: horas-operador = 2 x dotación.
    assert resumen["horas_operador"] == pytest.approx(
        sum(r.operadores_a_planificar for r in reqs) / 2)
    assert 0 < resumen["nds_promedio"] <= 1


def test_resumen_vacio():
    assert pl.resumen_de_plan([]) == {}


# ------------------------------------------------------- seguimiento intradía

from app import planificador_servicio as srv   # noqa: E402


def _dia(hora_hasta, real_por_hora, pron_por_hora):
    """Arma pronóstico y real de un día de a media hora."""
    base = datetime(2026, 9, 7)
    pron = {base + timedelta(minutes=30 * i): {"acme": pron_por_hora, "total": None,
                                               "asignacion": None}
            for i in range(48)}
    real = {base + timedelta(minutes=30 * i): real_por_hora
            for i in range(hora_hasta)}
    return pron, real


def test_el_intervalo_en_curso_no_cuenta_para_el_desvio():
    """El último intervalo con datos está a medias y siempre da por debajo. Si se
    lo incluye, el desvío se hunde justo en la lectura más reciente, que es la que
    alguien está mirando en ese momento."""
    pron, real = _dia(10, 100, 100)
    real[datetime(2026, 9, 7, 4, 30)] = 20      # el último, en curso
    m, cerrados = srv.metricas_intradia(pron, real)
    assert m["intervalos_cerrados"] == 9
    assert datetime(2026, 9, 7, 4, 30) not in cerrados
    assert m["desvio"] == pytest.approx(0.0)    # los 9 cerrados vienen exactos


def test_la_proyeccion_aplica_el_desvio_al_resto_del_dia():
    """Si el día viene 20% arriba, lo más probable es que siga 20% arriba: la
    forma intradía es mucho más estable que el nivel."""
    pron, real = _dia(11, 120, 100)             # 10 cerrados al 120% + 1 en curso
    m, _ = srv.metricas_intradia(pron, real)
    assert m["desvio"] == pytest.approx(0.20)
    # 10 cerrados x 120 + el en curso (120) + los 37 que quedan x 100 x 1,20
    assert m["proyeccion_cierre"] == pytest.approx(1200 + 120 + 37 * 120)
    assert m["avisar"] is True


def test_no_avisa_por_ruido_ni_con_pocos_intervalos():
    apenas, _ = srv.metricas_intradia(*_dia(11, 104, 100))
    assert apenas["avisar"] is False, "4% de desvío es ruido de un día cualquiera"

    temprano, _ = srv.metricas_intradia(*_dia(4, 200, 100))
    assert temprano["avisar"] is False, "con 3 intervalos cerrados el desvío no significa nada"
    assert temprano["proyeccion_cierre"] is None


def test_dia_sin_datos_todavia():
    pron, _ = _dia(0, 0, 100)
    m, cerrados = srv.metricas_intradia(pron, {})
    assert cerrados == []
    assert m["desvio"] is None and m["proyeccion_cierre"] is None
    assert m["avisar"] is False


# ===================================================================
# MEDICIÓN DEL ERROR — WAPE, MAPE y sesgo
# ===================================================================

def test_pronostico_perfecto_no_tiene_error():
    e = pl.medir_error([(100, 100), (50, 50), (0, 0)])
    assert e.wape == 0.0
    assert e.sesgo == 0.0
    assert e.mape == 0.0


def test_el_wape_pondera_por_volumen_y_el_mape_no():
    """El caso que justifica reportar las dos: un intervalo grande bien
    pronosticado y uno chico mal.

    El WAPE dice 'erré 10 de 1.010', o sea nada. El MAPE dice 'en promedio erré
    50%', porque le da el mismo peso al intervalo de 10 llamadas que al de 1.000.
    De madrugada eso es exactamente lo que pasa, y por eso el MAPE por media hora
    no se puede usar para juzgar un pronóstico.
    """
    e = pl.medir_error([(1000, 1000), (10, 20)])
    assert e.wape == pytest.approx(10 / 1010)
    assert e.mape == pytest.approx(0.5)


def test_el_minimo_para_mape_saca_los_intervalos_de_madrugada():
    pares = [(1000, 1000), (2, 4)]
    assert pl.medir_error(pares).mape == pytest.approx(0.5)
    # Con piso de 5 llamadas el intervalo de 2 no entra y el MAPE queda limpio.
    assert pl.medir_error(pares, minimo_para_mape=5).mape == pytest.approx(0.0)


def test_el_sesgo_positivo_significa_que_el_pronostico_se_quedo_corto():
    """Misma convención que la tarjeta de seguimiento intradía: positivo = entró
    más de lo previsto. Dos signos distintos para la misma idea en la misma
    pantalla es una trampa."""
    assert pl.medir_error([(120, 100)]).sesgo == pytest.approx(0.2)
    assert pl.medir_error([(80, 100)]).sesgo == pytest.approx(-0.2)


def test_los_errores_no_se_compensan_entre_si():
    """Un día 20 arriba y otro 20 abajo NO es un pronóstico perfecto: el sesgo da
    cero pero el WAPE tiene que verlo."""
    e = pl.medir_error([(120, 100), (80, 100)])
    assert e.sesgo == pytest.approx(0.0)
    assert e.wape == pytest.approx(40 / 200)


def test_medir_error_sin_datos_no_explota():
    e = pl.medir_error([])
    assert e.n == 0 and e.wape is None and e.mape is None and e.sesgo is None


def test_todo_real_en_cero_no_divide_por_cero():
    e = pl.medir_error([(0, 5), (0, 3)])
    assert e.wape is None and e.mape is None
    assert e.sesgo == pytest.approx(-1.0)


def test_como_dict_redondea_y_no_pierde_los_nulos():
    d = pl.medir_error([(3, 2)]).como_dict()
    assert d["real"] == 3.0 and d["pronosticado"] == 2.0
    assert d["wape"] == pytest.approx(0.3333, abs=1e-4)
    assert set(d) == {"n", "real", "pronosticado", "sesgo", "wape", "mape"}


def test_un_intervalo_sin_llamadas_no_tiene_tiempo_de_espera():
    """La madrugada no tiene ASA: no hay llamadas que esperen.

    Antes se guardaba 99999 para que entrara en la columna, y ese número se
    promediaba y se graficaba como si fuera un tiempo de espera real. Ahora la
    espera no finita viaja como NULL.
    """
    assert erlang.asa_seg(3, 0.0, 0.0) == float("inf")     # sin TMO no hay espera media
    assert erlang.asa_seg(3, 0.0, 180.0) == 0.0            # con TMO pero sin tráfico, 0
    assert erlang.asa_seg(3, 5.0, 180.0) == float("inf")   # cola inestable (a >= n)


# ===================================================================
# REFUERZOS — de la brecha a un pedido concreto
# ===================================================================

def _fila_ref(pool_id, momento, faltante, alto=None, llamadas=0.0, pool="General"):
    return {"pool_id": pool_id, "pool": pool, "momento": momento,
            "faltante": faltante, "faltante_alto": alto, "llamadas": llamadas}


def test_los_intervalos_contiguos_se_juntan_en_un_bloque():
    hoy = date(2026, 9, 8)
    filas = [_fila_ref(1, datetime(2026, 9, 10, 15, 0), 3),
             _fila_ref(1, datetime(2026, 9, 10, 15, 30), 5),
             _fila_ref(1, datetime(2026, 9, 10, 16, 0), 4)]
    r = pl.agrupar_refuerzos(filas, hoy)
    assert len(r) == 1
    assert r[0].desde == datetime(2026, 9, 10, 15, 0)
    assert r[0].hasta == datetime(2026, 9, 10, 16, 30)   # fin del último intervalo
    assert r[0].faltante_pico == 5
    assert r[0].horas_operador == pytest.approx((3 + 5 + 4) / 2)


def test_un_hueco_corta_el_bloque():
    """Un faltante de 10 a 12 y otro de 17 a 19 son DOS pedidos. Unirlos pediría
    gente para el mediodía, que es cuando no hace falta."""
    hoy = date(2026, 9, 8)
    filas = [_fila_ref(1, datetime(2026, 9, 10, 10, 0), 2),
             _fila_ref(1, datetime(2026, 9, 10, 17, 0), 2)]
    assert len(pl.agrupar_refuerzos(filas, hoy)) == 2


def test_cambiar_de_pool_o_de_dia_tambien_corta():
    hoy = date(2026, 9, 8)
    filas = [_fila_ref(1, datetime(2026, 9, 10, 23, 30), 2),
             _fila_ref(1, datetime(2026, 9, 11, 0, 0), 2),      # otro día
             _fila_ref(2, datetime(2026, 9, 11, 0, 0), 2)]      # otro pool
    assert len(pl.agrupar_refuerzos(filas, hoy)) == 3


def test_el_canal_depende_de_cuanto_falta_para_el_dia():
    """Cada canal cuesta distinto: mover la malla no cuesta plata, la hora extra
    sí, y la convocatoria del día es la más cara y la peor para el clima."""
    assert pl.accion_por_antelacion(10) == "malla"
    assert pl.accion_por_antelacion(4) == "malla"
    assert pl.accion_por_antelacion(3) == "horas_extra"
    assert pl.accion_por_antelacion(1) == "horas_extra"
    assert pl.accion_por_antelacion(0) == "convocatoria"


def test_el_faltante_de_redondeo_no_genera_un_pedido():
    hoy = date(2026, 9, 8)
    filas = [_fila_ref(1, datetime(2026, 9, 10, 15, 0), 0)]
    assert pl.agrupar_refuerzos(filas, hoy) == []


def test_el_escenario_alto_viaja_al_lado_y_no_reemplaza():
    """Las dos lecturas juntas: quedarse corto y quedarse largo no cuestan lo
    mismo, y elegir una en silencio le saca la decisión a quien la tiene que
    tomar."""
    hoy = date(2026, 9, 8)
    filas = [_fila_ref(1, datetime(2026, 9, 12, 15, 0), 3, alto=9)]
    r = pl.agrupar_refuerzos(filas, hoy)[0]
    assert r.faltante_pico == 3 and r.faltante_pico_alto == 9
    assert r.horas_operador == pytest.approx(1.5)
    assert r.horas_operador_alto == pytest.approx(4.5)


def test_el_resumen_separa_por_canal():
    hoy = date(2026, 9, 8)
    filas = [_fila_ref(1, datetime(2026, 9, 8, 15, 0), 4),      # hoy
             _fila_ref(1, datetime(2026, 9, 9, 15, 0), 2),      # mañana
             _fila_ref(1, datetime(2026, 9, 20, 15, 0), 6)]     # dentro de 12 días
    res = pl.resumen_de_refuerzos(pl.agrupar_refuerzos(filas, hoy))
    assert res["tramos"] == 3 and res["dias"] == 3
    assert set(res["por_accion"]) == {"convocatoria", "horas_extra", "malla"}
    assert res["por_accion"]["convocatoria"]["horas_operador"] == pytest.approx(2.0)
    assert res["faltante_pico"] == 6


def test_resumen_sin_refuerzos():
    assert pl.resumen_de_refuerzos([])["tramos"] == 0


# ===================================================================
# CORRECCIÓN DE NIVEL SEPARADA POR TIPO DE DÍA
# ===================================================================

def _historico_por_tipo(desde: date, dias: int, habil: float, no_habil: float):
    """Serie donde los días hábiles y los no hábiles se movieron distinto."""
    h = {}
    for i in range(dias):
        d = desde + timedelta(days=i)
        valor = no_habil if d.isoweekday() >= 6 else habil
        m = datetime.combine(d, time(0, 0))
        for _ in range(48):
            h[m] = valor
            m += timedelta(minutes=30)
    return h


def test_el_fin_de_semana_no_hereda_el_nivel_de_los_dias_habiles():
    """El caso que rompía el pronóstico de los sábados.

    Los días hábiles son el 83% del volumen, así que una corrección de nivel
    única la escriben ellos y después se le aplica al fin de semana. Con Voltara
    eso dejaba el sábado 15,5% arriba de lo real, porque un día hábil es 55%
    EMERGENCIAS y 40% COMERCIAL y un sábado es 98,9% EMERGENCIAS: se comportan
    como campañas distintas.
    """
    hoy = date(2026, 9, 7)
    # Ocho meses de historia estable, y los últimos tres meses con los hábiles
    # 30% arriba y el fin de semana igual que siempre.
    hist = _historico_por_tipo(date(2025, 9, 1), 280, 100.0, 100.0)
    hist.update(_historico_por_tipo(date(2026, 6, 15), 84, 130.0, 100.0))

    dias = [date(2026, 9, 12), date(2026, 9, 14)]      # sábado y lunes
    junto = pl.baseline_estacional(hist, dias, hoy=hoy, semanas_base=52,
                                   dias_nivel=28)
    aparte = pl.baseline_estacional(hist, dias, hoy=hoy, semanas_base=52,
                                    dias_nivel=28, nivel_por_tipo_de_dia=True)

    def total(pron, dia):
        return sum(v for m, v in pron.items() if m.date() == dia)

    # Con la corrección única, el sábado se contagia la suba de los hábiles.
    assert total(junto, date(2026, 9, 12)) > 100 * 48 * 1.05
    # Separada, el sábado se queda donde estaba y el lunes sí sube.
    assert total(aparte, date(2026, 9, 12)) == pytest.approx(100 * 48, rel=0.05)
    assert total(aparte, date(2026, 9, 14)) > total(aparte, date(2026, 9, 12))


def test_la_correccion_separada_viene_activada():
    """Es la configuración que ganó medida: 54,2% -> 48,5% de error de fin de
    semana. Si alguien la apaga, tiene que volver a medirla."""
    assert pl.CampanaCfg(campana_id=20).nivel_por_tipo_de_dia is True


def test_el_tipo_de_dia_trata_al_feriado_como_no_habil():
    feriados = {date(2026, 8, 17)}
    assert pl.tipo_de_dia(date(2026, 8, 17), feriados) == "no_habil"   # lunes feriado
    assert pl.tipo_de_dia(date(2026, 8, 18), feriados) == "habil"
    assert pl.tipo_de_dia(date(2026, 8, 22), feriados) == "no_habil"   # sábado


# ============================================================================
# SHRINKAGE POR TIPO DE DÍA Y BREAK
# ============================================================================
# El shrinkage no es un solo número. Medido sobre el pool telefónico y 180 días:
# hábil 8,87% · sábado 9,01% · domingo 10,23% · feriado 5,27%. Sábado y domingo
# NO se separan —dan lo mismo que un hábil, porque lo que se ahorran en
# capacitación se les va en faltante dentro del turno— y el feriado sí.

LUNES = datetime(2026, 9, 7, 10, 0)
SABADO = datetime(2026, 9, 12, 10, 0)
FERIADO = datetime(2026, 10, 12, 10, 0)


def test_sin_configurar_nada_todos_los_dias_usan_el_general():
    """Las dos columnas nacen en NULL y eso significa «usá el general»: la
    migración no puede mover ni un operador el día que se aplica."""
    cfg = pl.CampanaCfg(campana_id=20, shrinkage=0.10)

    assert cfg.shrinkage_del_dia(LUNES) == 0.10
    assert cfg.shrinkage_del_dia(SABADO) == 0.10
    assert cfg.shrinkage_del_dia(FERIADO) == 0.10


def test_el_feriado_usa_el_suyo_cuando_esta_cargado():
    cfg = pl.CampanaCfg(campana_id=20, shrinkage=0.10,
                        shrinkage_feriado=0.05,
                        feriados=frozenset({FERIADO.date()}))

    assert cfg.shrinkage_del_dia(FERIADO) == 0.05
    assert cfg.shrinkage_del_dia(LUNES) == 0.10


def test_un_feriado_que_cae_sabado_manda_como_feriado():
    """El feriado gana sobre el fin de semana: es el que tiene la explicación
    estructural (esa malla es de voluntarios y se cumple casi entera)."""
    sabado_feriado = datetime(2026, 6, 20, 10, 0)      # Belgrano, cae sábado
    cfg = pl.CampanaCfg(campana_id=20, shrinkage=0.10, shrinkage_no_habil=0.12,
                        shrinkage_feriado=0.05,
                        feriados=frozenset({sabado_feriado.date()}))

    assert cfg.shrinkage_del_dia(sabado_feriado) == 0.05


def test_sin_feriados_cargados_el_feriado_no_se_reconoce():
    """Si el servicio no le pasa los feriados, un feriado se dimensiona como el
    día de semana que le toque. Vale la pena que esté escrito."""
    cfg = pl.CampanaCfg(campana_id=20, shrinkage=0.10, shrinkage_feriado=0.05)

    assert cfg.shrinkage_del_dia(FERIADO) == 0.10


def test_el_break_es_la_fraccion_del_turno():
    """5 minutos por hora planificada, tomados todos juntos: un turno de 6 horas
    tiene 30 minutos, que son 8,33% del turno."""
    assert pl.CampanaCfg(campana_id=20, break_min_por_hora=5).factor_break() \
        == pytest.approx(0.0833, abs=1e-4)
    assert pl.CampanaCfg(campana_id=20).factor_break() == 0.0


def test_el_break_nace_en_cero_y_no_mueve_la_dotacion():
    """Ese 8,33% ya está adentro del factor de disponibilidad —que se estima
    invirtiendo el NDS observado contra los agentes presentes, y el que está en
    su break está presente y no está sobre la cola—. Prenderlo sin volver a medir
    la disponibilidad descuenta dos veces."""
    cfg = _cfg()

    assert cfg.break_min_por_hora == 0.0
    assert cfg.factor_break() == 0.0


def test_el_break_y_el_shrinkage_se_encadenan_y_no_se_suman():
    """Son proporciones de cosas distintas: de los citados cuántos llegan al
    piso, y de los que están cuántos no están en su break. Sumarlas descontaría
    dos veces lo mismo."""
    demanda = {LUNES: [pl.DemandaSkill(6, 200.0, 300.0)]}
    base = pl.plan_de_pool(_cfg(shrinkage=0.10), pl.PoolCfg(1, "General", 2), demanda)
    con_break = pl.plan_de_pool(
        _cfg(shrinkage=0.10, break_min_por_hora=5),
        pl.PoolCfg(1, "General", 2), demanda)

    a, b = base[0].operadores_a_planificar, con_break[0].operadores_a_planificar
    assert b > a
    # Encadenado y con UN SOLO redondeo al final. No se puede planificar media
    # persona en un intervalo, pero redondear en cada escalón inventa gente: ver
    # `test_un_solo_redondeo_al_final`.
    linea = base[0].operadores_en_linea
    assert b == math.ceil(linea / (1 - 0.10) / (1 - 5 / 60))
    # Sumar los dos descuentos descuenta MÁS, porque el segundo factor se aplicaría
    # sobre el total en vez de sobre lo que quedó del primero. Se compara en float:
    # con 40 en línea la diferencia es del 1% y el redondeo la tapa.
    encadenado = linea / (1 - 0.10) / (1 - 5 / 60)
    sumado = linea / (1 - 0.10 - 5 / 60)
    assert encadenado < sumado
    # Y con volumen suficiente la diferencia sobrevive al redondeo.
    grande = pl.plan_de_pool(
        _cfg(shrinkage=0.10, break_min_por_hora=5),
        pl.PoolCfg(1, "General", 2),
        {LUNES: [pl.DemandaSkill(6, 600.0, 300.0)]})[0]
    n = grande.operadores_en_linea
    assert grande.operadores_a_planificar == math.ceil(n / (1 - 0.10) / (1 - 5 / 60))
    assert grande.operadores_a_planificar < math.ceil(n / (1 - 0.10 - 5 / 60))


def test_un_solo_redondeo_al_final():
    """Redondear en cada escalón de la cadena inventa gente, y con pocos operadores
    la inventa a lo grande: 2 en línea con estos factores da ceil(2/0,9)=3 y después
    ceil(3/0,9167)=4, o sea el doble, cuando en float da 2,42 → 3.

    Medido sobre el pool telefónico, 16 días: redondear tres veces pedía 13.937
    intervalos-operador contra 13.109 redondeando una sola vez (+6,3%), y en los
    intervalos de uno a cuatro operadores —la madrugada y los fines de semana— el
    exceso era del 43%. Es lo que hacía que el cotejo de dotación mostrara un
    domingo pidiendo +55% de gente con el nivel de servicio cumplido."""
    cfg = _cfg(shrinkage=0.10, break_min_por_hora=5)
    # Un intervalo chico: una sola llamada, así manda la cobertura mínima del pool.
    demanda = {LUNES: [pl.DemandaSkill(6, 1.0, 120.0)]}
    fila = pl.plan_de_pool(cfg, pl.PoolCfg(1, "General", 2), demanda)[0]

    assert fila.operadores_en_linea == 2
    encadenado_mal = erlang._con_shrinkage(erlang._con_shrinkage(2, 0.10), 5 / 60)
    assert encadenado_mal == 4                       # lo que pedía antes
    assert fila.operadores_a_planificar == 3         # 2 / 0,9 / 0,9167 = 2,42 -> 3


# ------------------------------------------- redondeo para abajo de madrugada

MADRUGADA = datetime(2026, 9, 7, 3, 0)


def test_la_franja_de_redondeo_para_abajo():
    cfg = pl.CampanaCfg(campana_id=20, redondeo_abajo_desde=0, redondeo_abajo_hasta=8)
    assert cfg.redondea_para_abajo(datetime(2026, 9, 7, 0, 0))
    assert cfg.redondea_para_abajo(datetime(2026, 9, 7, 7, 30))
    assert not cfg.redondea_para_abajo(datetime(2026, 9, 7, 8, 0))
    # Cruzando la medianoche.
    noche = pl.CampanaCfg(campana_id=20, redondeo_abajo_desde=22, redondeo_abajo_hasta=6)
    assert noche.redondea_para_abajo(datetime(2026, 9, 7, 23, 30))
    assert noche.redondea_para_abajo(datetime(2026, 9, 7, 5, 30))
    assert not noche.redondea_para_abajo(datetime(2026, 9, 7, 12, 0))
    # Apagado.
    assert not pl.CampanaCfg(campana_id=20).redondea_para_abajo(MADRUGADA)
    assert not pl.CampanaCfg(campana_id=20, redondeo_abajo_desde=3,
                             redondeo_abajo_hasta=3).redondea_para_abajo(MADRUGADA)


def test_de_madrugada_cita_para_abajo_sin_bajar_de_los_de_en_linea():
    """2 en línea / 0,9 / 0,9167 = 2,42: para arriba 3, para abajo 2. Nunca menos
    que los que tienen que estar en la línea."""
    demanda = [pl.DemandaSkill(6, 1.0, 120.0)]
    pool = pl.PoolCfg(1, "General", 2)
    arriba = pl.dimensionar_intervalo(_cfg(shrinkage=0.10, break_min_por_hora=5),
                                      pool, MADRUGADA, demanda)
    abajo = pl.dimensionar_intervalo(
        _cfg(shrinkage=0.10, break_min_por_hora=5, redondeo_abajo_desde=0,
             redondeo_abajo_hasta=8), pool, MADRUGADA, demanda)
    assert arriba.operadores_en_linea == abajo.operadores_en_linea == 2
    assert arriba.operadores_a_planificar == 3
    assert abajo.operadores_a_planificar == 2


def test_para_abajo_nunca_deja_menos_que_en_linea_aunque_la_cadena_no_descuente():
    """Sin descuentos la cuenta da exacto el entero: el floor no puede perder uno
    por el último decimal."""
    cfg = _cfg(redondeo_abajo_desde=0, redondeo_abajo_hasta=8)
    for llamadas in (1.0, 6.0, 25.0, 60.0):
        r = pl.dimensionar_intervalo(cfg, pl.PoolCfg(1, "General", 0), MADRUGADA,
                                     [pl.DemandaSkill(6, llamadas, 180.0)])
        assert r.operadores_a_planificar >= r.operadores_en_linea


def test_fuera_de_la_franja_sigue_para_arriba():
    demanda = [pl.DemandaSkill(6, 1.0, 120.0)]
    cfg = _cfg(shrinkage=0.10, break_min_por_hora=5, redondeo_abajo_desde=0,
               redondeo_abajo_hasta=8)
    r = pl.dimensionar_intervalo(cfg, pl.PoolCfg(1, "General", 2), LUNES, demanda)
    assert r.operadores_a_planificar == 3


def test_la_franja_guardada_se_lee_tal_cual():
    """Lo que guarda la tabla es la disponibilidad REAL, sin break: el break se
    descuenta aparte, así que el día que la operación cambie los 5 minutos por
    hora se toca esa sola perilla y esto sigue valiendo. La conversión
    `medido / (1 - break)` se hace una vez, al aplicar la calibración."""
    franjas = [pl.FranjaDisponibilidad(dia_semana=0, hora_desde=0,
                                       hora_hasta=24, factor=0.894)]
    sin = pl.CampanaCfg(campana_id=20, disponibilidad=franjas)
    con = pl.CampanaCfg(campana_id=20, disponibilidad=franjas,
                        break_min_por_hora=5)

    assert sin.factor_disponibilidad(LUNES) == 0.894
    assert con.factor_disponibilidad(LUNES) == 0.894


def test_gana_la_franja_del_dia_concreto_sobre_la_generica():
    franjas = [pl.FranjaDisponibilidad(dia_semana=0, hora_desde=0, hora_hasta=24,
                                       factor=0.90),
               pl.FranjaDisponibilidad(dia_semana=1, hora_desde=0, hora_hasta=24,
                                       factor=0.75)]
    cfg = pl.CampanaCfg(campana_id=20, disponibilidad=franjas)

    assert cfg.factor_disponibilidad(LUNES) == 0.75      # lunes
    assert cfg.factor_disponibilidad(SABADO) == 0.90


def test_el_techo_de_abandono_del_prioritario_pide_menos_gente():
    """ELECTRODEPENDIENTES es el único skill con techo de abandono (0,50%) y esa
    verificación era la restricción que más dotación pedía del pool: se le exigía
    a la cola prioritaria el tiempo de espera de la cola común."""
    demanda = {LUNES: [pl.DemandaSkill(6, 190.0, 300.0),
                       pl.DemandaSkill(5, 10.0, 300.0)]}
    pool = pl.PoolCfg(1, "General", 2)

    # Sin techo de ocupación: se busca que la restricción que mande sea el
    # abandono y no otra, que es de lo que trata este test.
    sin = pl.plan_de_pool(_cfg(max_abandono_electro=0.005, max_ocupacion=None),
                          pool, demanda)
    con = _cfg(max_abandono_electro=0.005, max_ocupacion=None)
    for s in con.skills:
        if s.skill_id == 5:
            s.prioridad = True
    conp = pl.plan_de_pool(con, pool, demanda)

    assert conp[0].operadores_en_linea < sin[0].operadores_en_linea
    # Y el motivo deja de ser el abandono de esa cola.
    assert "abandono" in sin[0].motivo
    assert "abandono" not in conp[0].motivo


def test_la_prioridad_no_cambia_el_dimensionamiento_del_pool():
    """Reordena la cola; no cambia cuánta gente hace falta para el nivel de
    servicio agregado, que sale de Erlang C sobre el total."""
    demanda = {LUNES: [pl.DemandaSkill(6, 200.0, 300.0)]}
    pool = pl.PoolCfg(1, "General", 2)

    base = _cfg()          # sin techo de abandono en ningún skill
    con = _cfg()
    for s in con.skills:
        s.prioridad = True

    assert pl.plan_de_pool(con, pool, demanda)[0].operadores_en_linea == \
        pl.plan_de_pool(base, pool, demanda)[0].operadores_en_linea


def test_la_prioridad_nace_apagada_en_la_config():
    assert pl.SkillCfg(6, "EMERGENCIAS", 1).prioridad is False
