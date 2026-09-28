"""Tests del cotejo de DOTACIÓN del backtest: cuántos operadores pedíamos y
cuántos hubo.

Por qué existe este archivo. El cotejo de llamadas no alcanza para saber si el
planificador pide bien la gente: el error de llamadas no se traduce uno a uno en
error de dotación —Erlang no es lineal y la cadena de descuentos (disponibilidad,
shrinkage, break) mete su propio error encima—. Por eso cada intervalo se
dimensiona DOS veces y la brecha contra la malla se parte en dos sumandos:

    (pedíamos - citados) = (pedíamos - hacía falta) + (hacía falta - citados)
                            error de                  error de
                            pronóstico                dimensionamiento

Sin esa partición la pantalla miente por omisión: el 2026-09-10 el plan pedía
+15,4% sobre los citados y parecía un problema de dimensionamiento, pero con la
demanda real pedía -10,6%, o sea que la brecha era del pronóstico entera.

Las invariantes que se defienden acá: los dos sumandos cierran, el día en curso no
entra, el día de poco volumen no promedia, y el TMO del pronóstico NO mira el
futuro.

Offline: no toca ni BD ni red.

Correr: pytest tests/test_planificador_cotejo_dotacion.py -m "not tokens"
"""
from datetime import date, datetime, time, timedelta

import pytest

from app import planificador as pl
from app import planificador_datos as pdatos
from app import planificador_servicio as ps


# ------------------------------------------------------------------ andamiaje

SKILL, POOL = 1, 1
HOY = date(2026, 6, 10)


def _cfg():
    cfg = pl.CampanaCfg(campana_id=pdatos.CAMPANA_VOLTARA, intervalo_min=30,
                        paciencia_seg=800)
    cfg.skills = [pl.SkillCfg(skill_id=SKILL, nombre="CNR", pool_id=POOL,
                              objetivo_nds=0.8, umbral_seg=20)]
    cfg.pools = {POOL: pl.PoolCfg(pool_id=POOL, nombre="General")}
    return cfg


class _FakeEngine:
    """Sólo tiene que poder abrir una conexión: lo que devuelve cada consulta lo
    deciden los monkeypatch de `dotacion_planificada`, `agentes_conectados` y
    `servicio_real`."""
    def connect(self):
        class _C:
            def __enter__(self_):
                return self_
            def __exit__(self_, *a):
                return False
        return _C()


@pytest.fixture
def sin_base(monkeypatch):
    # `dotacion_real` es la que manda (el registro, con las horas extras adentro);
    # `dotacion_planificada` es la malla que se informa al lado.
    monkeypatch.setattr(pdatos, "dotacion_real",
                        lambda conn, pool, d, h, i=30: dict(CITADOS))
    monkeypatch.setattr(pdatos, "dotacion_planificada",
                        lambda conn, pool, d, h, i=30: dict(MALLA))
    monkeypatch.setattr(pdatos, "agentes_conectados",
                        lambda conn, c, d, h: dict(CONECTADOS))
    monkeypatch.setattr(pdatos, "servicio_real",
                        lambda conn, c, s, d, h: dict(SERVICIO))
    # Digital como palanca: 3 con turno por intervalo, y los conectados partidos.
    monkeypatch.setattr(pdatos, "campanas_refuerzo_del_pool",
                        lambda conn, pool: [53])
    monkeypatch.setattr(pdatos, "refuerzo_real",
                        lambda conn, pool, d, h, i=30: dict(REFUERZO))
    monkeypatch.setattr(pdatos, "refuerzo_planificado",
                        lambda conn, pool, d, h, i=30: dict(REFUERZO))
    monkeypatch.setattr(pdatos, "conectados_por_origen",
                        lambda conn, c, pools, d, h, con_en_linea=False: dict(ORIGEN))
    monkeypatch.setattr(pdatos, "dotacion_parcial_real",
                        lambda conn, c, pool, d, h: {})


def _momentos(dia, desde=9, hasta=11):
    m = datetime.combine(dia, time(desde, 0))
    fin = datetime.combine(dia, time(hasta, 0))
    while m < fin:
        yield m
        m += timedelta(minutes=30)


DIA = date(2026, 6, 9)            # cerrado: HOY es el 10
CITADOS = {m: 40 for m in _momentos(DIA)}       # registro: 40, con extras adentro
MALLA = {m: 38 for m in _momentos(DIA)}         # la malla prometía 38
CONECTADOS = {m: 38.0 for m in _momentos(DIA)}
REFUERZO = {m: 3 for m in _momentos(DIA)}      # Digital con turno
# Suma 38, igual que CONECTADOS: es el mismo total partido.
ORIGEN = {m: {"pool": 33.0, "refuerzo": 3.0, "otras": 2.0,
             # Sin pausas: el break y demás pausas salen de la línea.
             "pool_en_linea": 29.0, "refuerzo_en_linea": 2.5, "otras_en_linea": 1.5}
          for m in _momentos(DIA)}
SERVICIO = {m: {"entrantes": 200.0, "atendidas": 198.0, "hasta_umbral": 170.0}
            for m in _momentos(DIA)}
# El pronóstico sobreestima: 260 contra 200 reales.
PRON = {m: {SKILL: 260.0} for m in _momentos(DIA)}
REAL = {(m, SKILL): (200.0, 300.0) for m in _momentos(DIA)}
PERFIL = pdatos.perfil_de_tmo(REAL)


def _correr(cfg=None, pron=None, real=None, dias=(DIA,), hoy=HOY, perfil=None):
    return ps._dotacion_del_backtest(
        _FakeEngine(), cfg or _cfg(), pdatos.CAMPANA_VOLTARA,
        PRON if pron is None else pron,
        REAL if real is None else real,
        {SKILL}, list(dias), hoy, (), PERFIL if perfil is None else perfil)


# ---------------------------------------------------------------------- tests

def test_dimensiona_las_dos_veces_y_el_pronostico_pide_mas(sin_base):
    """Es el punto del cotejo: con el pronóstico inflado pide más gente que con la
    demanda que entró, y las dos cifras conviven para poder restarlas."""
    d = _correr()
    f = d["por_dia"][0]

    assert f["a_planificar_pron"] > f["a_planificar_real"] > 0
    assert f["de_mas_por_pronostico"] == f["a_planificar_pron"] - f["a_planificar_real"]


def test_los_dos_sumandos_cierran_contra_la_brecha(sin_base):
    """La identidad que hace que la partición signifique algo:
    (pedíamos - citados) = (pedíamos - hacía falta) + (hacía falta - citados)."""
    f = _correr()["por_dia"][0]

    brecha = f["a_planificar_pron"] - f["citados"]
    pronostico = f["de_mas_por_pronostico"]
    dimensionamiento = f["a_planificar_real"] - f["citados"]
    assert brecha == pronostico + dimensionamiento


def test_los_cocientes_usan_el_denominador_que_dicen(sin_base):
    f = _correr()["por_dia"][0]

    assert f["pron_vs_citados"] == pytest.approx(
        f["a_planificar_pron"] / f["citados"] - 1, abs=1e-4)
    assert f["real_vs_citados"] == pytest.approx(
        f["a_planificar_real"] / f["citados"] - 1, abs=1e-4)
    # Contra los conectados y entre pronóstico y real, EN LÍNEA: no se divide una
    # columna a citar por una en línea.
    assert "real_vs_conectados" not in f
    assert f["linea_real_vs_conectados"] == pytest.approx(
        f["en_linea_real"] / f["conectados"] - 1, abs=1e-4)
    assert f["linea_pron_vs_real"] == pytest.approx(
        f["en_linea_pron"] / f["en_linea_real"] - 1, abs=1e-4)


def test_los_totales_en_linea_suman_los_intervalos(sin_base):
    d = _correr()
    dia, g = d["por_dia"][0], d["resumen"]

    assert dia["en_linea_pron"] == sum(f["en_linea_pron"] for f in d["por_intervalo"])
    assert dia["en_linea_real"] == sum(f["en_linea_real"] for f in d["por_intervalo"])
    assert g["en_linea_pron"] == dia["en_linea_pron"]
    assert g["linea_pron_vs_real"] == pytest.approx(
        g["en_linea_pron"] / g["en_linea_real"] - 1, abs=1e-4)


def test_el_servicio_real_viaja_al_lado(sin_base):
    """Sin el nivel de servicio la brecha se lee al revés: citar menos de lo que el
    plan pedía y cumplir el objetivo igual significa que el plan pide de más."""
    d = _correr()
    f = d["por_dia"][0]

    assert f["nds_real"] == pytest.approx(170.0 / 200.0, abs=1e-4)
    assert f["abandono_real"] == pytest.approx(1 - 198.0 / 200.0, abs=1e-4)
    assert d["objetivo_nds"] == 0.8
    assert d["umbral_seg"] == 20


def test_el_dia_en_curso_no_entra(sin_base):
    """Un día abierto tiene media jornada de llamadas y la malla entera: entraría
    como una sub-dotación gigante que no existió."""
    d = _correr(dias=(DIA, HOY))

    assert [f["dia"] for f in d["por_dia"]] == [DIA]


def test_sin_dias_cerrados_avisa_en_vez_de_devolver_vacio(sin_base):
    """Una tabla vacía sin explicación se lee como "el cotejo dice que está todo
    bien". Tiene que decir por qué no hay nada."""
    d = _correr(dias=(HOY,))

    assert "por_dia" not in d
    assert d["motivo"] == "no hay días cerrados en el período"


def test_el_dia_de_poco_volumen_se_muestra_pero_no_promedia(sin_base, monkeypatch):
    """Con doscientas llamadas manda la cobertura mínima del pool y el cociente no
    dice nada sobre si el dimensionamiento acierta. Se marca y se excluye del
    resumen, pero se sigue viendo: esconderlo sería peor."""
    monkeypatch.setattr(ps, "MIN_LLAMADAS_PARA_DOTACION", 10_000)
    d = _correr()

    assert d["por_dia"][0]["poco_volumen"] is True
    assert d["resumen"] is None


def test_el_resumen_pondera_el_servicio_por_llamadas(sin_base, monkeypatch):
    """Un domingo de 700 llamadas no pesa igual que un lunes de 6.000: el promedio
    de promedios le daría el mismo peso a los dos y movería el NDS del resumen a un
    número que ningún día tuvo."""
    otro = date(2026, 6, 8)
    # Los dos días entran al resumen (los dos pasan el mínimo), pero el segundo
    # tiene la cuarta parte de las llamadas y un NDS perfecto.
    citados = {**CITADOS, **{m: 40 for m in _momentos(otro)}}
    conect = {**CONECTADOS, **{m: 38.0 for m in _momentos(otro)}}
    serv = {**SERVICIO, **{m: {"entrantes": 50.0, "atendidas": 50.0,
                               "hasta_umbral": 50.0} for m in _momentos(otro)}}
    real = {**REAL, **{(m, SKILL): (50.0, 300.0) for m in _momentos(otro)}}
    pron = {**PRON, **{m: {SKILL: 50.0} for m in _momentos(otro)}}
    monkeypatch.setattr(pdatos, "dotacion_real",
                        lambda conn, p, d, h, i=30: citados)
    monkeypatch.setattr(pdatos, "dotacion_planificada",
                        lambda conn, p, d, h, i=30: citados)
    monkeypatch.setattr(pdatos, "agentes_conectados", lambda conn, c, d, h: conect)
    monkeypatch.setattr(pdatos, "servicio_real", lambda conn, c, s, d, h: serv)
    monkeypatch.setattr(ps, "MIN_LLAMADAS_PARA_DOTACION", 100)

    d = _correr(pron=pron, real=real, dias=(otro, DIA))

    assert d["resumen"]["dias"] == 2
    # Ponderado: (800 llamadas a 85% + 200 a 100%) / 1.000 = 88%.
    # Promedio de promedios daría 92,5%, que es el número que no hay que dar.
    assert d["resumen"]["nds_real"] == pytest.approx(0.88, abs=0.005)


def test_el_perfil_de_tmo_del_pronostico_no_mira_el_futuro():
    """El backtest arma el perfil sólo con lo anterior al primer corte. Acá se fija
    la invariante en el llamador, que es donde se puede romper sin que nada falle:
    con el perfil de toda la ventana el pronóstico dimensionaría con el TMO que
    después resultó."""
    import inspect
    fuente = inspect.getsource(ps.backtest)
    assert "perfil_de_tmo({k: v for k, v in nuestra.items()" in fuente
    assert "if k[0].date() < corte_min}" in fuente


def test_si_no_se_puede_leer_la_malla_el_backtest_no_se_cae(monkeypatch):
    """La dotación real son las referencias que van AL LADO de la medición del
    pronóstico, no la medición. Con la migración de la malla sin correr o el
    informe del día sin cargar, el backtest tiene que seguir midiendo el
    pronóstico y decir qué le falta."""
    def _explota(*a, **k):
        raise RuntimeError("Invalid object name 'planificacion.PoolOrigen'")
    monkeypatch.setattr(pdatos, "dotacion_real", _explota)

    d = _correr()

    assert "por_dia" not in d
    assert "no se pudo leer la dotación real" in d["motivo"]
    assert "PoolOrigen" in d["motivo"]


def test_el_registro_manda_y_la_malla_se_informa_al_lado(sin_base):
    """`payroll_futuro` —la malla— no tiene NINGUNA hora extra cargada: 90 días del
    pool telefónico dan 1.426,4 h de extras en `dbo.payroll` y cero en la malla. Por
    eso "cuántos hubo" se lee del registro. La malla se conserva al lado porque es
    contra lo que se negocia la dotación, y porque la distancia entre las dos es en
    sí misma un dato: son horas extras y cambios de último momento."""
    f = _correr()["por_dia"][0]

    assert f["citados"] > f["malla"]                  # el registro trae las extras
    # Los cocientes se miden contra el registro, no contra la malla.
    assert f["pron_vs_citados"] == pytest.approx(
        f["a_planificar_pron"] / f["citados"] - 1, abs=1e-4)

    g = _correr()["resumen"]
    assert g["registro_vs_malla"] == pytest.approx(
        g["citados"] / g["malla"] - 1, abs=1e-4)
    assert g["conectados_vs_citados"] == pytest.approx(
        g["conectados"] / g["citados"] - 1, abs=1e-4)


def test_sin_registro_cargado_se_cae_a_la_malla(monkeypatch):
    """Con la carga del payroll atrasada, el registro viene vacío. Usarlo igual
    daría una brecha infinita en todos los intervalos; se usa la malla y se sigue."""
    monkeypatch.setattr(pdatos, "dotacion_real", lambda conn, p, d, h, i=30: {})
    monkeypatch.setattr(pdatos, "dotacion_planificada",
                        lambda conn, p, d, h, i=30: dict(MALLA))
    monkeypatch.setattr(pdatos, "agentes_conectados",
                        lambda conn, c, d, h: dict(CONECTADOS))
    monkeypatch.setattr(pdatos, "servicio_real",
                        lambda conn, c, s, d, h: dict(SERVICIO))

    f = _correr()["por_dia"][0]

    assert f["citados"] == f["malla"] > 0


def test_las_dos_ocupaciones_usan_la_misma_definicion(sin_base):
    """Tráfico ofrecido sobre operadores en la cola, las dos. Es lo único que las
    hace comparables, y la diferencia entre ellas queda siendo sólo la dotación.

    No se usa la ocupación del tablero de la operación, que mide otra cosa: medida
    sobre 16 días da 84% incluso a las 3 de la mañana, donde el tráfico sobre
    conectados es del 10%."""
    f = _correr()["por_dia"][0]

    # El modelo, sobre los operadores que pide.
    assert f["ocupacion_modelo"] == pytest.approx(
        f["trafico"] / f["en_linea_real"], abs=1e-4)
    # La real, sobre los conectados por el mismo factor de disponibilidad.
    assert f["ocupacion_real"] == pytest.approx(
        f["trafico"] / f["en_cola_real"], abs=1e-4)
    # Y por ser el MISMO tráfico sobre denominadores distintos, el cociente entre
    # las dos ocupaciones es exactamente el cociente inverso de los operadores. Eso
    # es lo que permite leer la diferencia como dotación y no como otra definición:
    # donde hubo más gente en la cola que la que el modelo pide, la ocupación real
    # da más baja, y al revés.
    assert (f["ocupacion_modelo"] / f["ocupacion_real"]) == pytest.approx(
        f["en_cola_real"] / f["en_linea_real"], abs=1e-3)


def test_el_resumen_reconstruye_la_ocupacion_y_no_promedia_cocientes(sin_base,
                                                                    monkeypatch):
    """Promediar las ocupaciones diarias le daría el mismo peso al domingo de
    madrugada (10% de ocupación) que al lunes pico (85%), y el resultado no
    describiría ningún intervalo. Se reconstruye del tráfico y los operadores."""
    otro = date(2026, 6, 8)
    citados = {**CITADOS, **{m: 40 for m in _momentos(otro)}}
    conect = {**CONECTADOS, **{m: 38.0 for m in _momentos(otro)}}
    serv = {**SERVICIO, **{m: {"entrantes": 300.0, "atendidas": 300.0,
                               "hasta_umbral": 290.0} for m in _momentos(otro)}}
    real = {**REAL, **{(m, SKILL): (300.0, 300.0) for m in _momentos(otro)}}
    pron = {**PRON, **{m: {SKILL: 300.0} for m in _momentos(otro)}}
    monkeypatch.setattr(pdatos, "dotacion_real", lambda conn, p, d, h, i=30: citados)
    monkeypatch.setattr(pdatos, "dotacion_planificada",
                        lambda conn, p, d, h, i=30: citados)
    monkeypatch.setattr(pdatos, "agentes_conectados", lambda conn, c, d, h: conect)
    monkeypatch.setattr(pdatos, "servicio_real", lambda conn, c, s, d, h: serv)

    d = _correr(pron=pron, real=real, dias=(otro, DIA))
    g, filas = d["resumen"], d["por_dia"]

    assert g["ocupacion_modelo"] == pytest.approx(
        sum(f["trafico"] for f in filas) / sum(f["en_linea_real"] for f in filas),
        abs=1e-4)
    # Y no es el promedio de los dos cocientes diarios.
    promedio = sum(f["ocupacion_modelo"] for f in filas) / len(filas)
    assert g["ocupacion_modelo"] != pytest.approx(promedio, abs=1e-4)


def test_agrupa_los_motivos_en_familias():
    """El motor informa el motivo con más detalle del que conviene leer en una
    tabla. Se agrupa en las cuatro familias que corresponden a decisiones distintas,
    y la que no entra en ninguna queda con su nombre a la vista: una familia nueva
    tiene que verse, no diluirse en «otros»."""
    assert ps._familia_de_motivo("nivel de servicio") == "nivel de servicio"
    assert ps._familia_de_motivo("segundo nivel de servicio") == "nivel de servicio"
    assert ps._familia_de_motivo("techo de ocupación") == "techo de ocupación"
    assert ps._familia_de_motivo("cobertura mínima") == "cobertura mínima"
    assert ps._familia_de_motivo("sin llamadas") == "cobertura mínima"
    # El techo de abandono llega con el nombre del skill pegado.
    assert ps._familia_de_motivo("abandono de ELECTRODEPENDIENTES") == "abandono"
    assert ps._familia_de_motivo("techo de abandono") == "abandono"
    assert ps._familia_de_motivo("nivel de atención") == "abandono"
    # Una restricción nueva no se esconde.
    assert ps._familia_de_motivo("restricción nueva") == "restricción nueva"


def test_informa_el_reparto_de_quien_mando(sin_base):
    """En un día mandan varias restricciones —el techo en el pico, el nivel de
    servicio en el resto— así que un solo nombre mentiría: va el reparto completo,
    ordenado por peso y sumando uno."""
    f = _correr()["por_dia"][0]

    assert f["motivos"]
    assert sum(m["parte"] for m in f["motivos"]) == pytest.approx(1.0, abs=0.002)
    # Ordenado por peso, para que el primero sea el que más manda.
    partes = [m["parte"] for m in f["motivos"]]
    assert partes == sorted(partes, reverse=True)
    # Y los intervalos-operador de cada familia suman el requerimiento del día.
    assert sum(m["operadores"] for m in f["motivos"]) == f["a_planificar_real"]


def test_el_peso_va_en_intervalos_operador_y_no_en_intervalos(sin_base):
    """Que el techo de ocupación mande en 16 intervalos y el nivel de servicio en 26
    no dice cuál pesa más, porque los 16 son el pico. Medido el 07/09: 985
    intervalos-operador contra 285, o sea 75% contra 22%."""
    reparto = ps._repartir_motivos({
        "techo de ocupación": {"operadores": 985, "intervalos": 16},
        "nivel de servicio": {"operadores": 285, "intervalos": 26},
    })

    assert reparto[0]["motivo"] == "techo de ocupación"
    assert reparto[0]["parte"] == pytest.approx(985 / 1270, abs=1e-4)
    # Y el que tiene más intervalos queda segundo, que es el punto.
    assert reparto[1]["intervalos"] > reparto[0]["intervalos"]


def test_una_familia_sin_peso_no_figura(sin_base):
    """Una familia con cero intervalos-operador no es información: es una fila de
    ruido en una barra que ya tiene cuatro tramos."""
    reparto = ps._repartir_motivos({
        "nivel de servicio": {"operadores": 100, "intervalos": 10},
        "cobertura mínima": {"operadores": 0, "intervalos": 3},
    })

    assert [m["motivo"] for m in reparto] == ["nivel de servicio"]


def test_dice_que_parte_del_requerimiento_la_fija_el_techo(sin_base):
    """Es la respuesta a «cumplimos el nivel de servicio, por qué pide más gente»:
    porque en esa parte del requerimiento no manda el nivel de servicio. Medido, en
    día hábil fija el 40%; sábado y domingo, cero, porque ahí la ocupación no llega
    al 50% y lo que pide gente es el servicio de verdad."""
    f = _correr()["por_dia"][0]

    assert 0.0 <= f["peso_techo_ocupacion"] <= 1.0
    assert f["max_ocupacion"] == _cfg().max_ocupacion


def test_sin_techo_configurado_nada_lo_fija(sin_base):
    cfg = _cfg()
    cfg.max_ocupacion = None

    f = _correr(cfg=cfg)["por_dia"][0]

    assert f["peso_techo_ocupacion"] == 0.0
    assert f["max_ocupacion"] is None


# ------------------------------------------------------- intervalo por intervalo

def test_cada_dia_se_abre_en_sus_intervalos(sin_base):
    """El día agregado dice CUÁNTO se erró; el detalle dice DÓNDE. Un +19% de un
    domingo puede ser toda la tarde un poco o dos medias horas mucho."""
    d = _correr()
    filas = d["por_intervalo"]

    assert len(filas) == 48                              # un día, medias horas
    assert [f["momento"] for f in filas] == sorted(f["momento"] for f in filas)


def test_los_intervalos_suman_el_dia(sin_base):
    """Si el detalle no suma el agregado, la pantalla muestra dos verdades."""
    d = _correr()
    filas, dia = d["por_intervalo"], d["por_dia"][0]

    for campo in ("a_planificar_pron", "a_planificar_real", "citados", "malla"):
        assert sum(f[campo] for f in filas) == dia[campo], campo
    assert round(sum(f["conectados"] for f in filas)) == dia["conectados"]


def test_el_intervalo_sin_llamadas_no_inventa_nivel_de_servicio(sin_base):
    """La madrugada sin llamadas no tiene NDS: poner 100% o 0% sería un dato
    inventado en la columna que más se mira."""
    filas = _correr()["por_intervalo"]
    madrugada = next(f for f in filas if f["momento"].hour == 3)
    pico = next(f for f in filas if f["momento"].hour == 9)

    assert madrugada["nds_real"] is None
    assert madrugada["abandono_real"] is None
    assert pico["nds_real"] == pytest.approx(170.0 / 200.0, abs=1e-4)


def test_el_intervalo_dice_que_mando_con_familia_y_detalle(sin_base):
    """La familia sirve para colorear y agrupar; el detalle crudo no se pierde,
    porque «abandono» sin decir de qué skill no alcanza para actuar."""
    filas = _correr()["por_intervalo"]
    pico = next(f for f in filas if f["momento"].hour == 9)

    assert pico["motivo"] == ps._familia_de_motivo(pico["motivo_detalle"])
    assert pico["motivo_detalle"]


def test_con_varios_pools_manda_el_motivo_del_que_mas_pide(sin_base):
    """El total del intervalo lo mueve el pool que más gente pide, así que su
    motivo es el que explica la fila."""
    cfg = _cfg()
    cfg.pools[2] = pl.PoolCfg(pool_id=2, nombre="Refuerzo", min_operadores=500)

    filas = _correr(cfg=cfg)["por_intervalo"]

    assert all(f["motivo"] == "cobertura mínima" for f in filas)


# ------------------------------------------------------------------------- CPH

def test_cph_de_cada_lado_con_su_gente_y_su_volumen(sin_base):
    """Llamadas por operador por hora. El pronosticado divide las llamadas
    pronosticadas por las horas que PEDÍAMOS; el real divide las ATENDIDAS por las
    horas CONECTADAS. Los conectados ya son equivalentes, así que por la media hora
    dan horas-operador directo."""
    pico = next(f for f in _correr()["por_intervalo"] if f["momento"].hour == 9)

    assert pico["cph_pron"] == pytest.approx(
        pico["llamadas_pron"] / (pico["a_planificar_pron"] * 0.5), abs=0.01)
    # 198 atendidas con 38 conectados en media hora.
    assert pico["cph_real"] == pytest.approx(198.0 / (38.0 * 0.5), abs=0.01)


def test_el_cph_real_usa_atendidas_y_no_entrantes(sin_base):
    """La llamada que se cortó esperando no la atendió nadie: contarla inflaría la
    productividad justo en las medias horas donde faltó gente."""
    pico = next(f for f in _correr()["por_intervalo"] if f["momento"].hour == 9)

    assert pico["atendidas"] < pico["entrantes"]
    assert pico["cph_real"] == pytest.approx(
        pico["atendidas"] / (pico["conectados"] * 0.5), abs=0.01)


def test_el_cph_del_dia_se_reconstruye_de_las_sumas(sin_base):
    """El CPH de un día es el total de llamadas sobre el total de horas, no el
    promedio de 48 CPH de media hora, que pesaría igual las 3 de la mañana que el
    pico."""
    d = _correr()
    dia, filas = d["por_dia"][0], d["por_intervalo"]

    horas_con = sum(f["conectados"] for f in filas) * 0.5
    assert dia["cph_real"] == pytest.approx(
        sum(f["atendidas"] for f in filas) / horas_con, abs=0.01)
    horas_ped = sum(f["a_planificar_pron"] for f in filas) * 0.5
    assert dia["cph_pron"] == pytest.approx(
        sum(f["llamadas_pron"] for f in filas) / horas_ped, abs=0.01)


def test_sin_gente_no_hay_cph():
    """Sin horas-operador el cociente no existe: poner cero diría "no atendieron
    nada", que es otra cosa."""
    assert ps._cph(10.0, 0, 30) is None
    assert ps._cph(0.0, 4, 30) == 0.0
    assert ps._cph(30.0, 4, 30) == 15.0          # 30 llamadas / 2 horas-operador


def test_el_intervalo_trae_la_cadena_de_en_linea_a_hacia_falta(sin_base):
    """En la madrugada «hacía falta 3» contra 2 con turno y NDS 100% no se entiende
    sin la cuenta: 2 en línea ÷ disponibilidad ÷ (1 − shrinkage) ÷ (1 − break) =
    2,64 → 3. Cada intervalo trae los factores y el número sin redondear, y el
    redondeo es uno solo, hacia arriba."""
    import math

    filas = [f for f in _correr()["por_intervalo"] if f["en_linea_real"]]
    assert filas
    for f in filas:
        bruto = (f["en_linea_real"] / f["disponibilidad"]
                 / (1 - f["shrinkage"]) / (1 - f["factor_break"]))
        assert f["a_planificar_bruto"] == pytest.approx(bruto, abs=0.01)
        assert f["a_planificar_real"] == math.ceil(bruto - 1e-9)


# ------------------------------------------------- Digital como palanca

def test_los_conectados_partidos_suman_los_conectados(sin_base):
    """El detalle por origen es el mismo total partido, no otro número."""
    d = _correr()
    for f in d["por_intervalo"]:
        partes = f["conectados_pool"] + f["conectados_refuerzo"] + f["conectados_otras"]
        assert partes == pytest.approx(f["conectados"], abs=0.11)
    dia = d["por_dia"][0]
    assert dia["conectados_refuerzo"] == pytest.approx(3.0 * len(ORIGEN))


def test_digital_cubre_lo_que_falta_hasta_la_gente_que_tiene(sin_base, monkeypatch):
    """Con 20 con turno falta gente en todos los intervalos: Digital tapa hasta sus
    3 y el resto falta igual."""
    monkeypatch.setattr(pdatos, "dotacion_real",
                        lambda conn, pool, d, h, i=30: {m: 20 for m in _momentos(DIA)})
    d = _correr()

    con_turno = set(_momentos(DIA))
    for f in (f for f in d["por_intervalo"] if f["momento"] in con_turno):
        falta = max(f["a_planificar_real"] - f["citados"], 0)
        assert falta > 3
        assert f["refuerzo_turno"] == 3
        assert f["cubre_refuerzo"] == 3
        assert f["faltante_neto"] == falta - 3
    dia = d["por_dia"][0]
    assert dia["cubre_refuerzo"] == sum(f["cubre_refuerzo"] for f in d["por_intervalo"])
    assert dia["faltante_neto"] == dia["faltante"] - dia["cubre_refuerzo"]


def test_el_faltante_es_por_intervalo_y_no_la_resta_del_dia(sin_base, monkeypatch):
    """El sobrante de un intervalo no tapa el faltante de otro."""
    momentos = list(_momentos(DIA))
    turnos = {m: (10 if i == 0 else 200) for i, m in enumerate(momentos)}
    monkeypatch.setattr(pdatos, "dotacion_real",
                        lambda conn, pool, d, h, i=30: dict(turnos))
    d = _correr()

    primero = d["por_intervalo"][[f["momento"] for f in d["por_intervalo"]].index(momentos[0])]
    dia = d["por_dia"][0]
    assert dia["faltante"] == sum(max(f["a_planificar_real"] - f["citados"], 0)
                                  for f in d["por_intervalo"])
    assert dia["faltante"] == primero["a_planificar_real"] - 10


def test_sin_refuerzo_configurado_no_hay_columnas_de_digital(sin_base, monkeypatch):
    monkeypatch.setattr(pdatos, "campanas_refuerzo_del_pool", lambda conn, pool: [])
    d = _correr()

    f = d["por_intervalo"][0]
    assert f["refuerzo_turno"] is None and f["cubre_refuerzo"] is None
    assert d["por_dia"][0]["faltante_neto"] is None
    assert d["resumen"]["cubre_refuerzo"] is None


def test_los_conectados_en_linea_vienen_sin_pausas_por_intervalo_dia_y_resumen(sin_base):
    d = _correr()
    for f in d["por_intervalo"]:
        if f["momento"] in ORIGEN:
            assert f["conectados_pool_en_linea"] == 29.0
            assert f["conectados_refuerzo_en_linea"] == 2.5
            assert f["conectados_pool_en_linea"] <= f["conectados_pool"]
    dia = d["por_dia"][0]
    assert dia["conectados_pool_en_linea"] == pytest.approx(29.0 * len(ORIGEN))
    assert d["resumen"]["conectados_pool_en_linea"] == pytest.approx(29.0 * len(ORIGEN))


def test_los_turnos_parciales_suman_a_tenian_turno_y_se_informan(sin_base, monkeypatch):
    """Contingencia/SVP/Anfitrión en la línea cuentan como gente con turno en esa
    media hora, y se dice cuántos eran."""
    momentos = list(_momentos(DIA))
    monkeypatch.setattr(pdatos, "dotacion_parcial_real",
                        lambda conn, c, pool, d, h: {momentos[0]: 4})
    d = _correr()

    fila = next(f for f in d["por_intervalo"] if f["momento"] == momentos[0])
    otra = next(f for f in d["por_intervalo"] if f["momento"] == momentos[1])
    assert fila["citados"] == 44 and fila["citados_parciales"] == 4
    assert otra["citados"] == 40 and otra["citados_parciales"] == 0
    assert d["por_dia"][0]["citados_parciales"] == 4
    assert d["resumen"]["citados_parciales"] == 4


def test_sin_sub_campanas_parciales_no_se_informan(sin_base):
    d = _correr()
    assert d["por_intervalo"][0]["citados_parciales"] is None
    assert d["resumen"]["citados_parciales"] is None


def test_sin_el_dato_de_pausas_los_en_linea_vienen_vacios(sin_base, monkeypatch):
    """Una lectura sin la columna no puede mostrar ceros: sería decir que nadie
    estuvo en la línea."""
    solo_logueo = {m: {"pool": 33.0, "refuerzo": 3.0, "otras": 2.0} for m in ORIGEN}
    monkeypatch.setattr(pdatos, "conectados_por_origen",
                        lambda conn, c, pools, d, h, con_en_linea=False: dict(solo_logueo))
    d = _correr()

    assert d["por_intervalo"][0]["conectados_pool_en_linea"] is None
    assert d["por_dia"][0]["conectados_pool_en_linea"] is None
    assert d["por_dia"][0]["conectados_pool"] is not None


def test_si_falla_partir_los_conectados_el_cotejo_sale_igual(sin_base, monkeypatch):
    def _rompe(*a, **k):
        raise RuntimeError("timeout")
    monkeypatch.setattr(pdatos, "conectados_por_origen", _rompe)
    d = _correr()

    assert d["por_dia"]
    assert d["por_intervalo"][0]["conectados_pool"] is None
    assert d["por_dia"][0]["conectados"] > 0


def test_el_resumen_dice_que_parte_de_su_turno_estuvo_digital_en_la_linea(sin_base):
    g = _correr()["resumen"]

    assert g["refuerzo_turno"] == 3 * 4
    assert g["refuerzo_en_linea"] == pytest.approx(
        g["conectados_refuerzo"] / g["refuerzo_turno"], abs=1e-4)
