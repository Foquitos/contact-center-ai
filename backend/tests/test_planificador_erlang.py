"""Tests del motor de dimensionamiento (app/planificador_erlang.py).

Offline: no toca ni BD ni red. Se prueba contra valores calculados a mano y, para
Erlang A, contra la única referencia que tenemos gratis: con paciencia infinita
tiene que dar exactamente lo mismo que Erlang C.

Correr: pytest tests/test_planificador_erlang.py -m "not tokens"
"""
import math

import pytest

from app import planificador_erlang as pe


# ------------------------------------------------------------------- Erlang B/C

def test_erlang_b_a_mano():
    """B(1,1)=1/2 y B(2,1)=1/5, de la recursión B(n)=aB(n-1)/(n+aB(n-1))."""
    assert pe.erlang_b(1, 1.0) == pytest.approx(0.5)
    assert pe.erlang_b(2, 1.0) == pytest.approx(0.2)


def test_erlang_c_a_mano():
    """C(2,1) = B/(1-rho(1-B)) = 0.2/(1-0.5*0.8) = 1/3."""
    assert pe.erlang_c(2, 1.0) == pytest.approx(1 / 3)


def test_erlang_c_cola_inestable():
    """Con tráfico >= operadores no hay capacidad: espera todo el mundo."""
    assert pe.erlang_c(5, 5.0) == 1.0
    assert pe.erlang_c(5, 7.0) == 1.0
    assert pe.nivel_servicio_c(5, 7.0, 180, 20) == 0.0


def test_erlang_b_estable_con_trafico_alto():
    """La recursión sobre la inversa no puede desbordar ni con tráficos grandes."""
    b = pe.erlang_b(900, 800.0)
    assert 0.0 < b < 1.0 and math.isfinite(b)


def test_nivel_servicio_a_mano():
    """SL = 1 - C*exp(-(n-a)*T/TMO) con n=2, a=1, TMO=180, T=20."""
    esperado = 1 - (1 / 3) * math.exp(-20 / 180)
    assert pe.nivel_servicio_c(2, 1.0, 180, 20) == pytest.approx(esperado)


def test_trafico_en_erlangs():
    """100 llamadas de 180s en media hora = 10 Erlangs."""
    assert pe.trafico_erlangs(100, 180) == pytest.approx(10.0)


# --------------------------------------------------------------------- Erlang A

def test_sin_paciencia_es_erlang_c():
    """Apagar la paciencia tiene que devolver Erlang C exacto, no parecido."""
    m = pe.metricas_a(12, 10.0, 180, 20, paciencia_seg=None)
    assert m.p_abandono == 0.0
    assert m.p_espera == pytest.approx(pe.erlang_c(12, 10.0))
    assert m.nds_sobre_atendidas == pytest.approx(pe.nivel_servicio_c(12, 10.0, 180, 20))
    assert m.nds_sobre_atendidas == m.nds_sobre_entrantes


def test_paciencia_enorme_converge_a_erlang_c():
    """El caso límite del modelo: si nadie corta nunca, Erlang A ES Erlang C.

    Es la verificación de que la cadena M/M/n+M y la uniformización de la espera
    están bien armadas, sin depender de tablas de terceros.
    """
    m = pe.metricas_a(12, 10.0, 180, 20, paciencia_seg=5_000_000)
    assert m.p_espera == pytest.approx(pe.erlang_c(12, 10.0), rel=1e-3)
    assert m.p_abandono == pytest.approx(0.0, abs=1e-3)
    assert m.nds_sobre_entrantes == pytest.approx(
        pe.nivel_servicio_c(12, 10.0, 180, 20), rel=1e-3)


def test_menos_paciencia_es_mas_abandono():
    abandonos = [pe.metricas_a(11, 10.0, 180, 20, p).p_abandono
                 for p in (600, 300, 120, 60, 30)]
    assert abandonos == sorted(abandonos), "menos paciencia tiene que abandonar más"


def test_mas_operadores_es_menos_abandono():
    abandonos = [pe.metricas_a(n, 10.0, 180, 20, 90).p_abandono
                 for n in (10, 11, 12, 14, 18)]
    assert abandonos == sorted(abandonos, reverse=True)


def test_nds_sobre_atendidas_es_mayor_que_sobre_entrantes():
    """El hallazgo que motiva medir las dos cosas: como Voltara calcula el NDS
    sobre las atendidas, las que abandonan no restan y el indicador se ve mejor
    de lo que fue la experiencia real del cliente."""
    m = pe.metricas_a(10, 9.5, 200, 20, paciencia_seg=60)
    assert m.p_abandono > 0.01, "el caso de prueba tiene que tener abandono real"
    assert m.nds_sobre_atendidas > m.nds_sobre_entrantes


def test_nivel_atencion_es_el_complemento_del_abandono():
    m = pe.metricas_a(11, 10.0, 180, 20, 90)
    assert m.nivel_atencion == pytest.approx(1 - m.p_abandono)


def test_probabilidades_en_rango():
    """Barrido amplio: nada se puede ir de [0,1] ni volverse NaN."""
    for n in (1, 3, 12, 40, 120):
        for trafico in (0.5, 5.0, 11.0, 39.0, 130.0):
            m = pe.metricas_a(n, trafico, 200, 20, 75)
            for valor in (m.p_espera, m.p_abandono,
                          m.nds_sobre_atendidas, m.nds_sobre_entrantes):
                assert math.isfinite(valor) and 0.0 <= valor <= 1.0


# ------------------------------------------------------------------ dimensionar

def test_dimensionar_cumple_el_objetivo():
    r = pe.dimensionar(llamadas=100, tmo_seg=180, objetivo_nds=0.80, umbral_seg=20,
                       max_ocupacion=None)
    assert r.nds_c >= 0.80
    # Y es el mínimo: con uno menos no alcanza.
    assert pe.nivel_servicio_c(r.operadores_en_linea - 1, r.trafico, 180, 20) < 0.80


def test_dimensionar_nunca_baja_del_trafico():
    """Menos operadores que Erlangs es una cola que crece sin techo."""
    r = pe.dimensionar(llamadas=300, tmo_seg=200, max_ocupacion=None)
    assert r.operadores_en_linea > r.trafico


def test_mas_llamadas_pide_mas_gente():
    previos = 0
    for llamadas in (10, 50, 100, 200, 400):
        r = pe.dimensionar(llamadas=llamadas, tmo_seg=180, max_ocupacion=None)
        assert r.operadores_en_linea >= previos
        previos = r.operadores_en_linea


def test_economia_de_escala():
    """Duplicar el volumen NO duplica la gente: es el argumento para dimensionar
    el pool grande junto y no skill por skill."""
    chico = pe.dimensionar(llamadas=50, tmo_seg=180, max_ocupacion=None)
    grande = pe.dimensionar(llamadas=100, tmo_seg=180, max_ocupacion=None)
    assert grande.operadores_en_linea < 2 * chico.operadores_en_linea


def test_techo_de_ocupacion_agrega_gente():
    sin_techo = pe.dimensionar(llamadas=400, tmo_seg=180, max_ocupacion=None)
    con_techo = pe.dimensionar(llamadas=400, tmo_seg=180, max_ocupacion=0.85)
    assert con_techo.operadores_en_linea > sin_techo.operadores_en_linea
    assert con_techo.ocupacion <= 0.85
    assert con_techo.motivo == "techo de ocupación"


def test_techo_de_abandono_agrega_gente():
    """El caso Electrodependientes: el compromiso no es de espera sino de que no
    se pierda ninguna."""
    sin_techo = pe.dimensionar(llamadas=100, tmo_seg=180, paciencia_seg=60,
                               max_ocupacion=None)
    con_techo = pe.dimensionar(llamadas=100, tmo_seg=180, paciencia_seg=60,
                               max_abandono=0.001, max_ocupacion=None)
    assert con_techo.operadores_en_linea > sin_techo.operadores_en_linea
    assert con_techo.abandono_esperado <= 0.001
    assert con_techo.motivo == "techo de abandono"


def test_shrinkage():
    """Con 30% de shrinkage, para tener 10 en línea hay que planificar 15."""
    assert pe._con_shrinkage(10, 0.0) == 10
    assert pe._con_shrinkage(10, 0.3333333) == 15
    r = pe.dimensionar(llamadas=100, tmo_seg=180, shrinkage=0.30, max_ocupacion=None)
    assert r.operadores_a_planificar == math.ceil(r.operadores_en_linea / 0.70)


def test_intervalo_sin_llamadas():
    r = pe.dimensionar(llamadas=0, tmo_seg=180)
    assert r.operadores_en_linea == 0
    assert r.operadores_a_planificar == 0
    assert r.motivo == "sin llamadas"


def test_cobertura_minima():
    """La madrugada no puede quedar en cero aunque el pronóstico dé casi nada."""
    r = pe.dimensionar(llamadas=0, tmo_seg=180, minimo_operadores=2)
    assert r.operadores_en_linea == 2
    assert r.motivo == "cobertura mínima"

    r2 = pe.dimensionar(llamadas=1, tmo_seg=180, minimo_operadores=3,
                        max_ocupacion=None)
    assert r2.operadores_en_linea == 3


def test_tmo_disparatado_avisa_y_no_cuelga():
    """Un TMO cargado en minutos en vez de segundos no puede colgar el recálculo."""
    r = pe.dimensionar(llamadas=5000, tmo_seg=3600, max_ocupacion=None)
    assert r.operadores_en_linea == pe.MAX_OPERADORES
    assert r.avisos and "Revisar el dato" in r.avisos[0]


def test_perfil_real_emergencias():
    """Un intervalo pico real de EMERGENCIAS (agosto 2026): 195 llamadas de
    TMO 180s en media hora. Sirve de ancla de que los órdenes de magnitud son
    los de la operación y no los de un ejemplo de manual.

    El TMO es el PONDERADO por llamadas atendidas. El promedio simple de los
    intervalos da 176s y para Comercial daba 89s, que es un artefacto: incluye
    los intervalos vacíos, donde el reporte deja el TMO en cero."""
    r = pe.dimensionar(llamadas=195, tmo_seg=180, objetivo_nds=0.80, umbral_seg=20,
                       paciencia_seg=90, shrinkage=0.30, max_ocupacion=0.85)
    assert r.trafico == pytest.approx(195 * 180 / 1800, rel=1e-6)
    assert 20 <= r.operadores_en_linea <= 40
    assert r.operadores_a_planificar > r.operadores_en_linea
    assert r.nds_c >= 0.80


# ================================================================
# Equivalencia con la planilla de Excel que se usaba antes
# ================================================================
# El módulo VBA de T&C Limited (ErlangB/ErlangC/Agents/ASA/SLA) más la función
# `asesores` que le agregó Acme son la regla con la que se dimensionó Voltara
# históricamente. Se reimplementa acá, literal y sin corregir nada, como
# REFERENCIA: si el motor deja de dar el mismo número, el planificador deja de
# ser comparable con lo que la operación conoce y eso tiene que saltar como falla.


def _vba_erlang_b(servers: float, intensity: float) -> float:
    """Erlang B tal cual el VBA, incluido su redondeo casero de servidores."""
    if servers < 0.1 or intensity < 0:
        return 1.0
    max_it = int(servers) if (servers - int(servers)) < 0.1 else int(servers) + 1
    if max_it <= 0:
        return 1.0
    cant = servers / max_it
    last, b = 1.0, 0.0
    for count in range(1, max_it + 1):
        b = (intensity * last) / (cant * count + (intensity * last))
        last = b
    return min(max(b, 0.0), 1.0)


def _vba_erlang_c(servers: float, intensity: float) -> float:
    b = _vba_erlang_b(servers, intensity)
    denom = ((intensity / servers) * b) + (1 - (intensity / servers))
    return min(max(b / denom if denom else 1.0, 0.0), 1.0)


def _vba_agents(sla_obj: float, service_time: int, calls_per_hour: float,
                aht: int) -> int:
    """La función Agents del VBA: arranca en la ocupación 100% y sube de a uno."""
    death = 3600 / aht
    traffic = calls_per_hour / death
    erlangs = int(calls_per_hour * aht / 3600 + 0.5)
    n = 1 if erlangs < 1 else int(erlangs)
    while traffic / n >= 1:
        n += 1
    for _ in range(n * 100):
        if traffic / n < 1:
            c = _vba_erlang_c(n, traffic)
            slq = max(1 - c * math.exp((traffic - n) * service_time / aht), 0.0)
            if slq >= min(sla_obj, 1.0) or slq > (1 - 1e-5):
                return n
        n += 1
    return n


def _vba_asa(agents: float, calls_per_hour: float, aht: int) -> int:
    death = 3600 / aht
    traffic = calls_per_hour / death
    util = min(traffic / agents, 0.99)
    c = _vba_erlang_c(agents, traffic)
    return int(c / (agents * death * (1 - util)) * 3600 + 0.5)


CASOS = [(20, 180), (50, 180), (100, 180), (195, 180), (300, 180),
         (100, 335), (200, 335), (334, 250), (297, 313), (5, 200)]


@pytest.mark.parametrize("llamadas,tmo", CASOS,
                         ids=[f"{c}llam-{t}s" for c, t in CASOS])
def test_las_primitivas_dan_igual_que_la_planilla(llamadas, tmo):
    """Erlang B, Erlang C y el nivel de servicio, contra la implementación vieja."""
    trafico = pe.trafico_erlangs(llamadas, tmo)
    n = max(1, int(trafico) + 3)
    assert pe.erlang_b(n, trafico) == pytest.approx(_vba_erlang_b(float(n), trafico), abs=1e-12)
    assert pe.erlang_c(n, trafico) == pytest.approx(_vba_erlang_c(float(n), trafico), abs=1e-12)
    esperado = 1 - _vba_erlang_c(float(n), trafico) * math.exp((trafico - n) * 20 / tmo)
    assert pe.nivel_servicio_c(n, trafico, tmo, 20) == pytest.approx(esperado, abs=1e-12)


@pytest.mark.parametrize("llamadas,tmo", CASOS,
                         ids=[f"{c}llam-{t}s" for c, t in CASOS])
def test_la_dotacion_da_igual_que_la_planilla(llamadas, tmo):
    """Mismo objetivo (80% en 20s) y sin restricciones extra: mismo número de
    operadores que daba el Excel. Es lo que permite migrar sin que a nadie le
    cambie el número debajo de los pies."""
    viejo = _vba_agents(0.80, 20, 2 * llamadas, tmo)
    nuevo = pe.dimensionar(llamadas=llamadas, tmo_seg=tmo, objetivo_nds=0.80,
                           umbral_seg=20, max_ocupacion=None).operadores_en_linea
    assert nuevo == viejo


@pytest.mark.parametrize("llamadas,tmo", CASOS,
                         ids=[f"{c}llam-{t}s" for c, t in CASOS])
def test_el_asa_da_igual_que_la_planilla(llamadas, tmo):
    """El ASA (el "TME" de la planilla) redondeado al segundo."""
    trafico = pe.trafico_erlangs(llamadas, tmo)
    n = max(1, int(trafico) + 3)
    assert round(pe.asa_seg(n, trafico, tmo)) == pytest.approx(
        _vba_asa(float(n), 2 * llamadas, tmo), abs=1)


# --------------------------------------------- las restricciones de la planilla

def test_el_techo_de_espera_agrega_gente():
    """El "TME": la dotación de 80/20 deja un ASA de ~10s; pedir 3s exige más."""
    base = pe.dimensionar(llamadas=195, tmo_seg=180, max_ocupacion=None)
    assert base.asa_seg > 3
    con_tme = pe.dimensionar(llamadas=195, tmo_seg=180, max_ocupacion=None,
                             max_asa_seg=3)
    assert con_tme.operadores_en_linea > base.operadores_en_linea
    assert con_tme.asa_seg <= 3
    assert con_tme.motivo == "tiempo medio de espera"


def test_el_segundo_nivel_de_servicio_agrega_gente():
    """Un objetivo adicional más exigente (95% en 20s sobre 80% en 20s)."""
    base = pe.dimensionar(llamadas=195, tmo_seg=180, max_ocupacion=None)
    doble = pe.dimensionar(llamadas=195, tmo_seg=180, max_ocupacion=None,
                           objetivo_nds_2=0.95, umbral_seg_2=20)
    assert doble.operadores_en_linea > base.operadores_en_linea
    assert doble.motivo == "segundo nivel de servicio"


def test_el_nivel_de_atencion_de_la_planilla_es_pesimista():
    """Erlang B supone que la llamada que no encuentra operador se PIERDE, y en un
    call center con cola casi todas esperan y se atienden. Por eso el criterio
    viejo pide bastante más gente para el mismo compromiso.

    No es un detalle: con 195 llamadas de TMO 180s, la dotación de 80/20 da 94,3%
    por Erlang B y 99,4% por Erlang A con la paciencia medida."""
    r = pe.dimensionar(llamadas=195, tmo_seg=180, max_ocupacion=None,
                       paciencia_seg=1126)
    assert r.nivel_atencion_b < 0.96
    assert (1 - r.abandono_esperado) > 0.99
    assert r.nivel_atencion_b < (1 - r.abandono_esperado)

    exigente = pe.dimensionar(llamadas=195, tmo_seg=180, max_ocupacion=None,
                              min_nivel_atencion_b=0.98)
    assert exigente.operadores_en_linea > r.operadores_en_linea
    assert exigente.motivo == "nivel de atención"


# ============================================================================
# COLA CON PRIORIDAD
# ============================================================================
# Electrodependientes tiene prioridad en el ACD: cuando entra una de esas, es la
# primera en atenderse. Lo único que cambia es cuánta gente tiene adelante en la
# cola —ninguna—, y Erlang A ya tiene esa pieza: `pesos[k]` es la probabilidad de
# encontrar k adelante, y para la prioritaria toda la masa está en k = 0.

def _pico_voltara():
    """El pico real: 200 llamadas en la media hora con TMO de 300s."""
    return pe.trafico_erlangs(200, 300, 1800), 300.0, 1267.0


def test_la_cola_prioritaria_abandona_mucho_menos():
    trafico, tmo, paciencia = _pico_voltara()

    comun = pe.metricas_a(35, trafico, tmo, 20.0, paciencia)
    prio = pe.metricas_a(35, trafico, tmo, 20.0, paciencia, prioritario=True)

    assert comun.p_abandono == pytest.approx(0.027, abs=0.002)
    assert prio.p_abandono == pytest.approx(0.0036, abs=0.0005)
    assert prio.p_abandono < comun.p_abandono / 5


def test_la_cola_prioritaria_espera_LO_MISMO():
    """Una llamada prioritaria espera igual a que se libere un operador: eso
    depende de que el sistema esté lleno, no de su prioridad. Lo que cambia es
    que cuando se libera uno, es la que entra."""
    trafico, tmo, paciencia = _pico_voltara()

    comun = pe.metricas_a(35, trafico, tmo, 20.0, paciencia)
    prio = pe.metricas_a(35, trafico, tmo, 20.0, paciencia, prioritario=True)

    assert prio.p_espera == pytest.approx(comun.p_espera, rel=1e-9)


def test_la_prioridad_no_cambia_la_ocupacion_ni_el_trafico():
    """Reordena la cola; no hace desaparecer llamadas."""
    trafico, tmo, paciencia = _pico_voltara()

    prio = pe.metricas_a(35, trafico, tmo, 20.0, paciencia, prioritario=True)

    assert prio.trafico == trafico
    assert prio.ocupacion == pytest.approx(trafico / 35)


def test_sin_paciencia_la_prioridad_no_hace_nada():
    """Con la paciencia apagada esto colapsa en Erlang C, donde nadie abandona y
    no hay cola que reordenar."""
    trafico, tmo, _ = _pico_voltara()

    a = pe.metricas_a(35, trafico, tmo, 20.0, None)
    b = pe.metricas_a(35, trafico, tmo, 20.0, None, prioritario=True)

    assert a == b


def test_la_prioridad_nace_apagada():
    trafico, tmo, paciencia = _pico_voltara()

    assert pe.metricas_a(35, trafico, tmo, 20.0, paciencia) == \
        pe.metricas_a(35, trafico, tmo, 20.0, paciencia, prioritario=False)


def test_el_motivo_dice_cobertura_minima_cuando_manda_el_piso():
    """Con una llamada en la media hora el nivel de servicio se cumple con 1
    operador y los 2 los pide el piso del pool. Antes la búsqueda arrancaba en el
    piso, ninguna restricción asignaba motivo y quedaba «nivel de servicio» por
    defecto, al lado de un NDS de 100%."""
    r = pe.dimensionar(llamadas=1, tmo_seg=180, objetivo_nds=0.80, umbral_seg=20,
                       minimo_operadores=2, max_ocupacion=None)
    assert r.operadores_en_linea == 2
    assert r.motivo == "cobertura mínima"


def test_si_el_nivel_de_servicio_pide_lo_mismo_que_el_piso_el_motivo_es_suyo():
    """Dos llamadas de 270s: con un operador el NDS da 71% y no llega al 80%. El
    nivel de servicio pediría 2 aunque no hubiera piso, así que el motivo es suyo."""
    r = pe.dimensionar(llamadas=2, tmo_seg=270, objetivo_nds=0.80, umbral_seg=20,
                       minimo_operadores=2, max_ocupacion=None)
    assert r.operadores_en_linea == 2
    assert r.motivo == "nivel de servicio"


def test_aplicar_el_piso_al_final_no_cambia_la_dotacion():
    """El arreglo del motivo no puede mover el número. Todas las restricciones son
    monótonas en n, así que la dotación es el máximo entre lo que piden y el piso."""
    for llamadas in (0.5, 1, 2, 5, 12, 40, 120, 300):
        sin_piso = pe.dimensionar(llamadas=llamadas, tmo_seg=240,
                                  objetivo_nds=0.80, umbral_seg=20)
        for piso in (0, 2, 5, 30):
            con = pe.dimensionar(llamadas=llamadas, tmo_seg=240, objetivo_nds=0.80,
                                 umbral_seg=20, minimo_operadores=piso)
            assert con.operadores_en_linea == max(sin_piso.operadores_en_linea, piso), \
                (llamadas, piso)
