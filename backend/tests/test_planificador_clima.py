"""Tests del modelo de clima del planificador (app/planificador_clima.py).

Offline: series sintéticas con una relación conocida entre el tiempo y la
demanda, así que se puede afirmar que el ajuste la recupera —y, sobre todo, que
no ve lo que no tiene que ver.

Correr: pytest tests/test_planificador_clima.py -m "not tokens"
"""
import math
from datetime import date, timedelta

import pytest

from app import planificador_clima as pc


def _dia(t_max=20.0, t_min=15.0, lluvia=0.0, rafaga=20.0):
    return {"t_aparente_max": t_max, "t_aparente_min": t_min,
            "lluvia_mm": lluvia, "rafaga_kmh": rafaga}


def _clima_plano(desde: date, dias: int, **kw):
    return {desde + timedelta(days=i): _dia(**kw) for i in range(dias)}


# ------------------------------------------------------------------- rasgos

def test_un_dia_templado_no_tiene_ni_calor_ni_frio():
    """Entre 14 y 24 grados aparentes no se prende ni el aire ni la estufa, y el
    modelo tiene que ver ceros: es el tramo plano de la U."""
    clima = _clima_plano(date(2026, 1, 1), 3, t_max=22.0, t_min=16.0)
    r = pc.rasgos_del_dia(date(2026, 1, 3), clima)
    assert r["cdd"] == 0 and r["hdd"] == 0
    assert r["cdd2"] == 0 and r["hdd2"] == 0


def test_el_calor_y_el_frio_son_rasgos_DISTINTOS():
    """LA razón de ser del módulo. Una regresión sobre la temperatura a secas le
    pone un signo a la U y se pierde uno de los dos picos: los quince días de más
    demanda de Voltara son ocho de calor y siete de frío."""
    clima = _clima_plano(date(2026, 1, 1), 3, t_max=34.0, t_min=26.0)
    calor = pc.rasgos_del_dia(date(2026, 1, 3), clima)
    clima = _clima_plano(date(2026, 7, 1), 3, t_max=8.0, t_min=2.0)
    frio = pc.rasgos_del_dia(date(2026, 7, 3), clima)

    assert calor["cdd"] == pytest.approx(10.0) and calor["hdd"] == 0
    assert frio["hdd"] == pytest.approx(12.0) and frio["cdd"] == 0
    # Los dos empujan hacia arriba, cada uno por su rasgo. Si compartieran uno,
    # uno de los dos tendría que salir negativo y se anularían.
    assert calor["cdd2"] > 0 and frio["hdd2"] > 0


def test_sin_el_dia_anterior_no_hay_rasgos():
    """El efecto se acumula: la segunda jornada de una ola rompe más que la
    primera. Sin el día previo el rasgo no existe, y se dice."""
    clima = {date(2026, 1, 5): _dia()}
    assert pc.rasgos_del_dia(date(2026, 1, 5), clima) is None


def test_la_lluvia_y_las_rafagas_solo_cuentan_cuando_son_temporal():
    clima = _clima_plano(date(2026, 5, 1), 3)
    suave = pc.rasgos_del_dia(date(2026, 5, 3), clima)
    assert suave["lluvia"] == 0 and suave["rafaga"] == 0

    clima = _clima_plano(date(2026, 5, 1), 3, lluvia=40.0, rafaga=70.0)
    temporal = pc.rasgos_del_dia(date(2026, 5, 3), clima)
    assert temporal["lluvia"] == pytest.approx(math.log1p(40))
    assert temporal["rafaga"] == pytest.approx(3.0)


# -------------------------------------------------------------------- ajuste

def _serie_con_clima(desde: date, dias: int, efecto_frio=0.03):
    """Demanda = 1000, más un 3% por cada grado de frío. Relación conocida.

    El clima recorre un año entero —de 2 a 26 grados de mínima— porque hace falta
    que TODOS los rasgos varíen: si el verano no aparece, la columna de calor
    queda constante en cero, la matriz sale singular y el ajuste devuelve None.
    Es el mismo motivo por el que el modelo pide al menos un año de historia.
    """
    real, clima = {}, {}
    for i in range(dias):
        d = desde + timedelta(days=i)
        t_min = 14.0 + 12.0 * math.sin(2 * math.pi * i / 365.0)
        # La lluvia y las ráfagas también tienen que moverse, o sus columnas
        # quedan constantes y pasa lo mismo.
        clima[d] = _dia(t_max=t_min + 10, t_min=t_min,
                        lluvia=5.0 * (i % 7), rafaga=20.0 + 4.0 * (i % 11))
        hdd = max(0.0, pc.BASE_CALOR - t_min)
        real[d] = 1000.0 * math.exp(efecto_frio * hdd)
    return real, clima


def test_el_ajuste_recupera_una_relacion_conocida():
    real, clima = _serie_con_clima(date(2024, 1, 1), 800)
    m = pc.ajustar(real, clima, date(2026, 3, 11), semanas=52)
    assert m is not None
    assert m.r2 > 0.9            # la relación es determinista, tiene que verla
    # Un día 10 grados por debajo del umbral tiene que dar cerca de exp(0.3).
    frio = {date(2026, 3, 9): _dia(t_max=12, t_min=4),
            date(2026, 3, 10): _dia(t_max=12, t_min=4)}
    templado = {date(2026, 3, 9): _dia(t_max=22, t_min=16),
                date(2026, 3, 10): _dia(t_max=22, t_min=16)}
    assert (m.factor(date(2026, 3, 10), frio)
            / m.factor(date(2026, 3, 10), templado)) == pytest.approx(math.exp(0.3), rel=0.15)


def test_no_ajusta_con_poca_historia():
    """Con menos de un año no se vio un verano y un invierno completos, y el
    modelo extrapola a ciegas justo en los extremos, que son los días que
    importan."""
    real, clima = _serie_con_clima(date(2026, 1, 1), 100)
    assert pc.ajustar(real, clima, date(2026, 4, 11), semanas=52) is None


def test_el_ajuste_no_ve_nada_posterior_al_corte():
    """Misma regla que el backtest: entrenar con los días que después se evalúan
    da un resultado espectacular y falso."""
    real, clima = _serie_con_clima(date(2024, 1, 1), 800)
    corte = date(2025, 6, 1)
    m = pc.ajustar(real, clima, corte, semanas=52)
    assert m.entrenado_hasta == corte
    # Rompo la serie DESPUÉS del corte: el ajuste tiene que dar exactamente igual.
    for d in list(real):
        if d >= corte:
            real[d] = 99999.0
    igual = pc.ajustar(real, clima, corte, semanas=52)
    assert igual.coeficientes == pytest.approx(m.coeficientes)


def test_el_factor_no_extrapola_fuera_de_lo_visto():
    """Con término cuadrático, un día más caluroso que todo lo entrenado devuelve
    una parábola disparada. Se recorta al borde del rango en vez de creerle."""
    real, clima = _serie_con_clima(date(2024, 1, 1), 800)
    m = pc.ajustar(real, clima, date(2026, 3, 11), semanas=52)
    borde = m.rango["hdd"][1]
    en_el_borde = {date(2026, 3, 9): _dia(t_max=20, t_min=pc.BASE_CALOR - borde),
                   date(2026, 3, 10): _dia(t_max=20, t_min=pc.BASE_CALOR - borde)}
    absurdo = {date(2026, 3, 9): _dia(t_max=-30, t_min=-40),
               date(2026, 3, 10): _dia(t_max=-30, t_min=-40)}
    assert m.factor(date(2026, 3, 10), absurdo) == pytest.approx(
        m.factor(date(2026, 3, 10), en_el_borde))


def test_el_factor_siempre_queda_acotado():
    real, clima = _serie_con_clima(date(2024, 1, 1), 800, efecto_frio=0.5)
    m = pc.ajustar(real, clima, date(2026, 3, 11), semanas=52)
    extremo = {date(2026, 3, 9): _dia(t_max=50, t_min=-20),
               date(2026, 3, 10): _dia(t_max=50, t_min=-20)}
    f = m.factor(date(2026, 3, 10), extremo)
    assert pc.FACTOR_MIN <= f <= pc.FACTOR_MAX


def test_un_dia_sin_clima_no_recibe_factor_de_uno_en_silencio():
    """Un agujero en la descarga meteorológica se vería igual que un día normal.
    Devolver None obliga al que llama a saber que no hubo corrección."""
    real, clima = _serie_con_clima(date(2024, 1, 1), 800)
    m = pc.ajustar(real, clima, date(2026, 3, 11), semanas=52)
    assert m.factor(date(2027, 1, 1), clima) is None
    dias = [date(2026, 3, 10), date(2027, 1, 1)]
    assert list(pc.factores(m, dias, clima)) == [date(2026, 3, 10)]


def test_sin_modelo_no_hay_factores():
    assert pc.factores(None, [date(2026, 1, 1)], {}) == {}


def test_una_columna_constante_no_rompe_el_ajuste():
    """Entrenar sólo con invierno deja el rasgo de calor en cero todos los días.

    Con mínimos cuadrados puro la matriz sale singular y el ajuste se cae. Con la
    penalización de cresta el rasgo inútil se va a coeficiente cero y el resto
    sigue andando, que es lo que se quiere: un rasgo sin variación tiene que
    valer cero, no tirar abajo el modelo entero.
    """
    real, clima = {}, {}
    for i in range(500):
        d = date(2024, 6, 1) + timedelta(days=i)
        clima[d] = _dia(t_max=10.0, t_min=5.0)
        real[d] = 1000.0
    m = pc.ajustar(real, clima, date(2025, 10, 1), semanas=52)
    assert m is not None
    assert m.como_dict()["coeficientes"]["cdd"] == pytest.approx(0.0, abs=1e-6)


def test_la_cresta_no_deforma_un_ajuste_bien_condicionado():
    """El ridge tiene que estabilizar lo mal condicionado sin cambiar lo demás."""
    real, clima = _serie_con_clima(date(2024, 1, 1), 800)
    con = pc._minimos_cuadrados([[1.0, 1.0], [1.0, 2.0], [1.0, 3.0]], [2.0, 4.0, 6.0])
    sin = pc._minimos_cuadrados([[1.0, 1.0], [1.0, 2.0], [1.0, 3.0]], [2.0, 4.0, 6.0],
                                ridge=0.0)
    assert con == pytest.approx(sin, abs=0.02)


# =========================================================================
# QUÉ RASGOS QUEDARON ACTIVOS, Y POR QUÉ
# =========================================================================

def test_los_rasgos_activos_son_los_que_ganaron_medidos():
    """Fija la decisión que salió del backtest sobre cinco períodos.

    No es un test de comportamiento: es un candado. Agregar rasgos "porque
    suenan razonables" sobre 1.100 días de entrenamiento es la forma más rápida
    de aprender el ruido de tres veranos y creer que se mejoró, y el error
    absoluto medio del total diario lo mostró:

        sin clima                39,1%
        clima solo (8 rasgos)    32,1%
        clima + persistencia     30,2%   <-- el elegido
        los 25 rasgos            30,3%   (empata, con 13 parámetros de más)

    Si alguien cambia esta lista, tiene que volver a correr la comparación y
    actualizar los números del encabezado de planificador_servicio.
    """
    from app import planificador_servicio as servicio

    # 2026-09-14: entra el frío modulado por la época del año (ver el encabezado
    # de RASGOS_ACTIVOS en planificador_servicio, medido sobre un año a 1-3 días).
    assert servicio.RASGOS_ACTIVOS == (pc.GRUPOS["clima"] + pc.GRUPOS["persistencia"]
                                       + pc.GRUPOS["estacional"])
    assert len(servicio.RASGOS_ACTIVOS) == 18
    # Los grupos que se midieron y NO entraron siguen implementados —para poder
    # volver a probarlos cuando haya más historia— pero fuera del modelo.
    for grupo in ("clima_extra", "anual", "calendario"):
        assert not set(pc.GRUPOS[grupo]) & set(servicio.RASGOS_ACTIVOS)


def test_todos_los_rasgos_declarados_se_calculan():
    """Un nombre en GRUPOS que rasgos_del_dia no produce revienta con KeyError
    recién al ajustar, y en producción eso es a las 4 de la mañana."""
    clima = _clima_plano(date(2026, 1, 1), 10, t_max=30.0, t_min=20.0)
    r = pc.rasgos_del_dia(date(2026, 1, 10), clima, feriados={date(2026, 1, 9)})
    faltantes = set(pc.RASGOS_COMPLETOS) - set(r)
    assert not faltantes, f"rasgos declarados que nadie calcula: {faltantes}"


def test_la_persistencia_ve_la_ola_y_no_el_dia_suelto():
    """Lo que rompe la red no es el día caluroso: es el tercero seguido."""
    base = _clima_plano(date(2026, 1, 1), 10, t_max=20.0, t_min=15.0)
    suelto = dict(base)
    suelto[date(2026, 1, 10)] = _dia(t_max=36.0, t_min=26.0)
    ola = dict(base)
    for d in (date(2026, 1, 8), date(2026, 1, 9), date(2026, 1, 10)):
        ola[d] = _dia(t_max=36.0, t_min=26.0)

    r_suelto = pc.rasgos_del_dia(date(2026, 1, 10), suelto)
    r_ola = pc.rasgos_del_dia(date(2026, 1, 10), ola)
    # El calor del día es idéntico; lo que los distingue es el acumulado.
    assert r_suelto["cdd"] == r_ola["cdd"]
    assert r_ola["cdd_3d"] > r_suelto["cdd_3d"] * 2
    assert r_ola["cdd_7d"] > r_suelto["cdd_7d"]


def test_la_amplitud_estira_el_apartamiento_y_no_el_nivel():
    """La regresión atenúa la respuesta al clima, así que se la estira.

    Lo importante es QUÉ se estira: el apartamiento respecto del intercepto, no
    el intercepto. El nivel medio ya lo corrige `_correccion_de_nivel`; estirarlo
    acá sería moverlo dos veces.
    """
    modelo = pc.ModeloClima(
        coeficientes=[0.1, 0.02], n=400, r2=0.5, reduccion_residuo=0.3,
        entrenado_hasta=date(2026, 6, 1), rango={"cdd": (0.0, 20.0)},
        rasgos=("cdd",))
    clima = _clima_plano(date(2026, 1, 1), 3, t_max=34.0, t_min=26.0)
    f = modelo.factor(date(2026, 1, 3), clima)
    # cdd = 34 - 24 = 10 -> log crudo = 0,1 + 0,02*10 = 0,3
    # estirado: 0,1 + (0,3 - 0,1) * AMPLITUD
    esperado = math.exp(0.1 + 0.2 * pc.AMPLITUD)
    assert f == pytest.approx(esperado)

    # Un día templado no tiene apartamiento: la amplitud no lo toca.
    templado = _clima_plano(date(2026, 4, 1), 3, t_max=22.0, t_min=16.0)
    assert modelo.factor(date(2026, 4, 3), templado) == pytest.approx(math.exp(0.1))


def test_la_amplitud_esta_medida():
    """Candado del hallazgo, re-medido el 2026-09-14.

    Con los rasgos de clima de antes, x1,2 le ganaba a x1,0 (el pronóstico se
    quedaba 10,1% corto entre mayo y agosto). Ese faltante era el frío que pesa
    distinto según la época del año: con el grupo "estacional" adentro, x1,0 gana
    (18,1% contra 18,4% sobre un año a 1-3 días; 17,9% contra 19,6% el último mes).
    Si alguien vuelve a estirar sin medir, este test lo frena."""
    assert pc.AMPLITUD == 1.0
    from app import planificador_servicio as servicio
    assert set(pc.GRUPOS["estacional"]) <= set(servicio.RASGOS_ACTIVOS)


# ------------------------------------- elasticidad por tipo de día (domingo)

def _serie_diaria(desde: date, dias: int, base=1000.0, clima=None,
                  respuesta_domingo=1.0):
    """Demanda que responde al clima con fuerza distinta según el día.

    `respuesta_domingo` es cuánto del efecto del clima se cumple de verdad el
    domingo: con 0,5, la demanda del domingo se mueve la mitad de lo que se
    movería si respondiera como un día hábil.
    """
    out = {}
    for i in range(dias):
        d = desde + timedelta(days=i)
        c = (clima or {}).get(d, {})
        hdd = max(0.0, pc.BASE_CALOR - c.get("t_aparente_min", 15.0))
        efecto = 1 + 0.06 * hdd
        if d.isoweekday() == 7:
            efecto = efecto ** respuesta_domingo
        out[d] = base * (0.5 if d.isoweekday() >= 6 else 1.0) * efecto
    return out


def _clima_con_frio(desde: date, dias: int):
    out = {}
    for i in range(dias):
        d = desde + timedelta(days=i)
        frio = (i // 3) % 4 == 0        # olas de frío salpicadas
        out[d] = _dia(t_max=9.0, t_min=2.0) if frio else _dia()
    return out


def test_el_sabado_y_el_domingo_no_son_el_mismo_tipo_de_dia():
    """Toda la corrección se apoya en esto: su elasticidad al clima es 0,90 y
    0,58, así que meterlos en la misma bolsa 'no hábil' promedia dos cosas
    distintas."""
    assert pc.tipo_de_dia_fino(date(2026, 9, 5), set()) == "sabado"
    assert pc.tipo_de_dia_fino(date(2026, 9, 6), set()) == "domingo"
    assert pc.tipo_de_dia_fino(date(2026, 9, 7), set()) == "habil"
    assert pc.tipo_de_dia_fino(date(2026, 9, 7), {date(2026, 9, 7)}) == "feriado"


def test_recupera_una_elasticidad_del_domingo_MENOR_que_uno():
    """Si el domingo sólo cumple la mitad de lo que el factor le pide, la
    medición tiene que verlo. Es el caso real: 0,58 medido sobre 66 domingos."""
    desde = date(2025, 1, 1)
    clima = _clima_con_frio(desde, 600)
    serie = _serie_diaria(desde, 600, clima=clima, respuesta_domingo=0.4)
    hasta = desde + timedelta(days=600)
    # El factor que el modelo aplicaría: el que sale de ajustar sobre TODO
    modelo = pc.ajustar(serie, clima, hasta, semanas=52)
    assert modelo is not None
    factores = {1: pc.factores(modelo, sorted(serie), clima)}
    alfas = pc.elasticidad_por_tipo_de_dia(
        {1: serie}, factores, hasta, semanas=52, ventana=0)
    assert "domingo" in alfas
    assert alfas["domingo"] < 0.95


def test_no_corrige_cuando_el_domingo_SI_responde():
    """La contracara, y es la que evita romper lo que anda.

    Ojo con lo que se afirma acá: el estimador NO devuelve 1,00 cuando la
    respuesta es correcta, devuelve 0,855. Está sesgado hacia abajo porque
    regresa contra el factor de clima, que es una estimación con ruido, y eso
    atenúa la pendiente. Para eso está `UMBRAL_ELASTICIDAD`: la corrección no se
    aplica hasta que el desvío es claramente mayor que ese sesgo."""
    desde = date(2025, 1, 1)
    clima = _clima_con_frio(desde, 600)
    serie = _serie_diaria(desde, 600, clima=clima, respuesta_domingo=1.0)
    hasta = desde + timedelta(days=600)
    modelo = pc.ajustar(serie, clima, hasta, semanas=52)
    factores = {1: pc.factores(modelo, sorted(serie), clima)}
    alfas = pc.elasticidad_por_tipo_de_dia(
        {1: serie}, factores, hasta, semanas=52, ventana=0)
    assert "domingo" not in alfas


def test_con_pocos_domingos_no_corrige():
    """Se encoge hacia 1 con n/(n+15): con diez domingos la pendiente es ruido y
    corregir por ruido es peor que no corregir."""
    desde = date(2026, 6, 1)
    clima = _clima_con_frio(desde, 60)
    serie = _serie_diaria(desde, 60, clima=clima, respuesta_domingo=0.2)
    hasta = desde + timedelta(days=60)
    modelo = pc.ajustar(serie, clima, hasta, semanas=52)
    factores = {1: pc.factores(modelo, sorted(serie), clima)} if modelo else {}
    alfas = pc.elasticidad_por_tipo_de_dia({1: serie}, factores, hasta,
                                           semanas=52, ventana=0)
    assert "domingo" not in alfas


def test_solo_toca_los_domingos():
    """El sábado converge a 0,95, o sea que no lo necesita, y aplicárselo igual
    le mete el ruido del ajuste: medido, 23,44% -> 24,32% de MAPE diario."""
    assert pc.TIPOS_ELASTICIDAD == ("domingo",)
    f = {7: {date(2026, 9, 5): 2.0, date(2026, 9, 6): 2.0,
             date(2026, 9, 7): 2.0}}
    out = pc.aplicar_elasticidad(f, {"domingo": 0.5}, [])
    assert out[7][date(2026, 9, 5)] == 2.0          # sábado, intacto
    assert out[7][date(2026, 9, 7)] == 2.0          # lunes, intacto
    assert out[7][date(2026, 9, 6)] == pytest.approx(math.sqrt(2.0))


def test_sin_alfas_devuelve_los_factores_TAL_CUAL():
    """La garantía que permite que la migración nazca apagada."""
    f = {7: {date(2026, 9, 6): 1.87}}
    assert pc.aplicar_elasticidad(f, {}, []) is f


def test_la_elasticidad_esta_acotada():
    """El 0 sería 'el clima no existe el domingo', que está medido y es PEOR
    (32,14% contra 29,65%). La señal existe, está mal escalada."""
    assert pc.ELASTICIDAD_MIN >= 0.2
    assert pc.ELASTICIDAD_MAX == 1.0


def test_con_una_ventana_corta_de_factores_NO_inventa_una_elasticidad():
    """Regresión de un fallo SILENCIOSO que se coló y costó una corrida entera.

    La elasticidad se mide sobre los días ya cerrados, y el que llama le pasa el
    diccionario de factores que tiene a mano. El backtest calculaba factores para
    una ventana de treinta días —lo que necesita la corrección de nivel— así que
    la regresión veía cuatro domingos, no llegaba al mínimo y devolvía {} sin
    que nadie se enterara: el pronóstico salía idéntico al de siempre y parecía
    que la corrección "no servía".

    La función se defiende sola (devuelve {} en vez de una pendiente de cuatro
    puntos), pero el que llama tiene que darle la ventana larga."""
    desde = date(2025, 1, 1)
    clima = _clima_con_frio(desde, 600)
    serie = _serie_diaria(desde, 600, clima=clima, respuesta_domingo=0.4)
    hasta = desde + timedelta(days=600)
    modelo = pc.ajustar(serie, clima, hasta, semanas=52)
    todos = sorted(serie)

    # Ventana larga: la mide.
    largos = {1: pc.factores(modelo, todos, clima)}
    assert "domingo" in pc.elasticidad_por_tipo_de_dia(
        {1: serie}, largos, hasta, semanas=52, ventana=0)

    # Ventana de treinta días, que es la que necesita la corrección de nivel:
    # cuatro domingos, y no alcanza.
    cortos = {1: pc.factores(modelo, [d for d in todos
                                      if d >= hasta - timedelta(days=30)], clima)}
    assert pc.elasticidad_por_tipo_de_dia(
        {1: serie}, cortos, hasta, semanas=52, ventana=0) == {}


def test_el_backtest_pide_la_ventana_larga_cuando_la_elasticidad_esta_prendida():
    """El otro lado del mismo bug: que el llamador no vuelva a quedarse corto.

    Se mira el código porque el camino que fallaba no se puede ejercitar sin
    base: lo que hay que garantizar es que la ventana de días para la que se
    calculan los factores dependa de VENTANA_ELASTICIDAD y no sólo de
    `dias_nivel`."""
    import inspect
    from app import planificador_servicio as sv
    fuente = inspect.getsource(sv.backtest)
    assert "VENTANA_ELASTICIDAD" in fuente
