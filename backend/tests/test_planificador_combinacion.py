"""Tests del reparto por tipo de día y de la combinación con el pronóstico del cliente.

Offline: no toca ni base ni red. Todo lo de acá es lógica pura con series armadas
a mano, así que se puede afirmar exactamente qué tiene que dar.

Correr: pytest tests/test_planificador_combinacion.py -m "not tokens"
"""
from datetime import date, datetime, time, timedelta

import pytest

from app import planificador as pl
from app import planificador_combinacion as pcomb
from app import planificador_datos as pdatos


# =========================================================================
# EL REPARTO DE UN SÁBADO NO ES EL DE UN MARTES
# =========================================================================

TRAMO = [{"AsignacionID": 1, "SkillID": None, "VigenteDesde": date(2025, 1, 1),
          "VigenteHasta": None, "Porcentaje": 0.40, "Nota": None}]


def _reparto(desde: date, dias: int, share_habil: float, share_no_habil: float,
             total: float = 1000.0, feriados=()):
    """(día, skill) -> (demanda total, lo que nos llegó) con el share que se pida."""
    salida = {}
    for i in range(dias):
        d = desde + timedelta(days=i)
        share = share_no_habil if pl.tipo_de_dia(d, set(feriados)) == "no_habil" \
            else share_habil
        salida[(d, 6)] = (total, total * share)
    return salida


def test_el_factor_es_el_cociente_entre_los_dos_tipos_de_dia():
    """Hábiles al 40% (que es el tramo) y fines de semana al 34%: 0,85."""
    corte = date(2026, 9, 1)
    diario = _reparto(corte - timedelta(days=364), 364, 0.40, 0.34)
    f = pdatos.factor_reparto_no_habil(TRAMO, diario, corte, 365, [])
    assert f == pytest.approx(0.85, abs=1e-6)


def test_el_factor_se_mide_contra_los_habiles_y_no_contra_el_tramo():
    """Si el reparto entero se corrió, eso NO es efecto del tipo de día.

    Los dos tipos bajan a la mitad del tramo: el cociente sigue siendo 1 y el
    nivel general lo tiene que corregir la deriva, que es otra cosa. Sin esta
    propiedad las dos correcciones se pisarían y descontarían dos veces lo mismo.
    """
    corte = date(2026, 9, 1)
    diario = _reparto(corte - timedelta(days=364), 364, 0.20, 0.20)
    assert pdatos.factor_reparto_no_habil(TRAMO, diario, corte, 365, []) \
        == pytest.approx(1.0, abs=1e-6)


def test_el_feriado_cuenta_como_dia_no_habil():
    corte = date(2026, 9, 1)
    fer = [corte - timedelta(days=i) for i in range(1, 300, 30)]
    diario = _reparto(corte - timedelta(days=364), 364, 0.40, 0.50, feriados=fer)
    f = pdatos.factor_reparto_no_habil(TRAMO, diario, corte, 365, fer)
    assert f == pytest.approx(1.25, abs=1e-6)   # topeado en +25%


def test_el_factor_respeta_el_tope():
    corte = date(2026, 9, 1)
    diario = _reparto(corte - timedelta(days=364), 364, 0.40, 0.10)
    assert pdatos.factor_reparto_no_habil(TRAMO, diario, corte, 365, [],
                                          tope=0.25) == pytest.approx(0.75)


def test_sin_dias_suficientes_no_devuelve_nada():
    """Es más honesto que corregir con cuatro sábados: el que llama deja el tramo
    como está."""
    corte = date(2026, 9, 1)
    diario = _reparto(corte - timedelta(days=20), 20, 0.40, 0.30)
    assert pdatos.factor_reparto_no_habil(TRAMO, diario, corte, 365, []) is None


def test_solo_mira_la_ventana_pedida():
    """Los días de antes de la ventana no pueden influir."""
    corte = date(2026, 9, 1)
    viejo = _reparto(corte - timedelta(days=700), 300, 0.40, 0.10)
    nuevo = _reparto(corte - timedelta(days=200), 200, 0.40, 0.36)
    f = pdatos.factor_reparto_no_habil(TRAMO, {**viejo, **nuevo}, corte, 200, [])
    assert f == pytest.approx(0.90, abs=1e-6)


def test_en_cero_queda_apagado():
    corte = date(2026, 9, 1)
    diario = _reparto(corte - timedelta(days=364), 364, 0.40, 0.30)
    assert pdatos.factor_reparto_no_habil(TRAMO, diario, corte, 0, []) is None


# =========================================================================
# CUÁNTO PESA EL PRONÓSTICO DEL CLIENTE
# =========================================================================

def _historia(desde: date, dias: int, real, nuestro, cliente):
    """[(día, real, nuestro, cliente)] con valores fijos o funciones del día."""
    salida = []
    for i in range(dias):
        d = desde + timedelta(days=i)
        v = lambda x: x(d) if callable(x) else x
        salida.append((d, v(real), v(nuestro), v(cliente)))
    return salida


def test_si_nuestro_pronostico_es_perfecto_el_peso_es_cero():
    h = _historia(date(2026, 1, 1), 120, 1000, 1000, 1500)
    pesos = pcomb.pesos_por_tipo(h, solo_no_habiles=False)
    assert pesos["habil"].peso == pytest.approx(0.0)
    assert pesos["no_habil"].peso == pytest.approx(0.0)


def test_si_el_del_cliente_es_perfecto_el_peso_sube_hasta_el_tope():
    h = _historia(date(2026, 1, 1), 120, 1000, 1500, 1000)
    pesos = pcomb.pesos_por_tipo(h, solo_no_habiles=False)
    # El peso crudo es 1 (el cliente explica todo el residuo) y se acota al tope.
    assert pesos["habil"].peso_crudo == pytest.approx(1.0)
    assert pesos["habil"].peso == pytest.approx(
        pcomb.PESO_MAXIMO * pesos["habil"].n / (pesos["habil"].n + pcomb.K_ENCOGIMIENTO))


def test_el_peso_nunca_apuesta_contra_el_cliente():
    """Un peso negativo anda en la ventana medida y explota fuera de ella."""
    # El cliente erra para el MISMO lado que nosotros pero el doble (en el
    # logaritmo): el óptimo sin acotar es -1, o sea "restale al cliente".
    h = _historia(date(2026, 1, 1), 120, 1000,
                  lambda d: 1100 if d.day % 2 else 900,
                  lambda d: 1210 if d.day % 2 else 810)
    pesos = pcomb.pesos_por_tipo(h, solo_no_habiles=False)
    assert pesos["habil"].peso_crudo == pytest.approx(-1.0, abs=0.05)
    assert pesos["habil"].peso == 0.0


def test_solo_no_habiles_apaga_el_peso_habil_pero_informa_la_medicion():
    """La medición del hábil se sigue mostrando: es lo que sostiene la decisión
    de no combinarlos."""
    h = _historia(date(2026, 1, 1), 120, 1000, 1500, 1000)
    pesos = pcomb.pesos_por_tipo(h)
    assert pesos["habil"].peso == 0.0
    assert pesos["habil"].mape_cliente == pytest.approx(0.0)
    assert pesos["habil"].mape_nuestro == pytest.approx(0.5)
    assert pesos["no_habil"].peso > 0


def test_con_pocos_dias_no_hay_peso():
    h = _historia(date(2026, 1, 1), 4, 1000, 1500, 1000)
    assert pcomb.pesos_por_tipo(h) == {}


def test_los_dias_sin_pronostico_del_cliente_se_descartan():
    h = _historia(date(2026, 1, 1), 120, 1000, 1500, 0)
    assert pcomb.pesos_por_tipo(h, solo_no_habiles=False) == {}


# ------------------------------------------------------- el factor de cada día

def _peso(valor, tipo="no_habil"):
    return {tipo: pcomb.PesoCliente(tipo, valor, 50, 0.3, 0.2, valor)}


def test_el_factor_diario_mueve_hacia_el_cliente_en_el_logaritmo():
    """Con peso 0,5 el combinado es la media geométrica: raíz de (mío x suyo)."""
    dom = date(2026, 9, 6)
    f = pcomb.factores_diarios({dom: 100.0}, {dom: 400.0}, _peso(0.5))
    # 100 x factor = sqrt(100 x 400) = 200  ->  factor = 2, acotado al tope 1,4.
    assert f[dom] == pytest.approx(1.4)


def test_el_factor_diario_se_acota():
    dom = date(2026, 9, 6)
    f = pcomb.factores_diarios({dom: 100.0}, {dom: 10.0}, _peso(0.5), tope=0.3)
    assert f[dom] == pytest.approx(0.7)


def test_no_se_toca_el_dia_que_el_cliente_no_pronostico():
    dom, sab = date(2026, 9, 6), date(2026, 9, 5)
    f = pcomb.factores_diarios({dom: 100.0, sab: 100.0}, {dom: 200.0}, _peso(0.4))
    assert sab not in f and dom in f


def test_un_dia_habil_no_se_toca_si_solo_hay_peso_de_no_habiles():
    lunes = date(2026, 9, 7)
    assert pcomb.factores_diarios({lunes: 100.0}, {lunes: 200.0}, _peso(0.4)) == {}


# ------------------------------------------------------- aplicar el factor

def test_aplicar_mueve_la_demanda_y_las_filas_a_la_vez():
    """Si se desacoplan, la pantalla muestra un número y el Erlang usa otro."""
    momento = datetime.combine(date(2026, 9, 6), time(10, 0))
    demanda = {momento: [pl.DemandaSkill(6, 100.0, 180.0),
                         pl.DemandaSkill(4, 50.0, 300.0)]}
    filas = [{"skill": 6, "momento": momento, "acme": 100.0},
             {"skill": 4, "momento": momento, "acme": 50.0}]
    tocados = pcomb.aplicar(demanda, filas, {date(2026, 9, 6): 1.2})
    assert tocados == 1
    assert [d.llamadas for d in demanda[momento]] == pytest.approx([120.0, 60.0])
    assert [f["acme"] for f in filas] == pytest.approx([120.0, 60.0])


def test_aplicar_no_agrega_claves_a_las_filas():
    """Las filas van tal cual a un INSERT con nombre por columna: una clave de
    más lo rompe."""
    momento = datetime.combine(date(2026, 9, 6), time(10, 0))
    filas = [{"skill": 6, "momento": momento, "acme": 100.0}]
    pcomb.aplicar({}, filas, {date(2026, 9, 6): 1.2})
    assert set(filas[0]) == {"skill", "momento", "acme"}


def test_sin_factores_no_toca_nada():
    momento = datetime.combine(date(2026, 9, 6), time(10, 0))
    demanda = {momento: [pl.DemandaSkill(6, 100.0, 180.0)]}
    assert pcomb.aplicar(demanda, [], {}) == 0
    assert demanda[momento][0].llamadas == 100.0


# =========================================================================
# LA SIMULACIÓN NO PUEDE MIRAR EL FUTURO
# =========================================================================

def test_la_simulacion_no_usa_dias_que_no_se_conocian_al_corte():
    """LA propiedad de la calibración.

    El cliente es perfecto y nosotros erramos el 50% desde el primer día. Con 7
    días de antelación, el pronóstico del día D se hizo el día D-7, así que sólo
    pudo medir el peso con días anteriores a D-7: los primeros días TIENEN que
    salir sin peso, y el peso tiene que ir subiendo después.

    Si alguien 'mejora' la simulación dejándola ver todo, el primer día aparece
    combinado y este test se pone rojo.
    """
    h = _historia(date(2026, 1, 3), 90, 1000, 1500, 1000)
    filas, pesos = pcomb.simular(h, antelacion=7, solo_no_habiles=False, metodo="ls")
    assert filas[0]["peso"] == 0.0
    assert filas[0]["combinado"] == pytest.approx(1500.0)
    # A los ~15 días del tipo ya hay peso, y crece.
    con_peso = [f for f in filas if f["peso"] > 0]
    assert con_peso, "el peso nunca llegó a medirse"
    assert con_peso[-1]["peso"] > con_peso[0]["peso"]
    assert con_peso[-1]["combinado"] < 1500.0
    assert pesos["habil"].peso > 0


def test_la_simulacion_respeta_la_antelacion():
    """A un día de antelación el peso arranca antes que a treinta. Es la
    contraprueba de que la demora no está clavada."""
    h = _historia(date(2026, 1, 3), 90, 1000, 1500, 1000)
    corto, _ = pcomb.simular(h, antelacion=1, solo_no_habiles=False, metodo="ls")
    largo, _ = pcomb.simular(h, antelacion=30, solo_no_habiles=False, metodo="ls")
    primero_corto = next(i for i, f in enumerate(corto) if f["peso"] > 0)
    primero_largo = next(i for i, f in enumerate(largo) if f["peso"] > 0)
    assert primero_corto < primero_largo


# =========================================================================
# POR ACIERTO RECIENTE (2026-09-14): sólo domingos y feriados
# =========================================================================

def test_el_peso_reciente_premia_al_que_erro_menos():
    # nosotros erramos 50%, el cliente 10%: el cliente tiene que pesar más que
    # nosotros, pero no más del tope.
    h = _historia(date(2026, 1, 1), 120, 1000, 1500, 1100)
    pesos = pcomb.pesos_recientes(h)
    assert pesos["no_habil"].peso_crudo > 0.5
    assert pesos["no_habil"].peso == pytest.approx(pcomb.PESO_MAXIMO)


def test_el_peso_reciente_mira_solo_los_ultimos_dias_del_grupo():
    """Si el cliente erró todo el año y acertó los últimos domingos, manda lo reciente."""
    def cliente(d):
        return 1000 if d >= date(2026, 3, 1) else 3000
    h = _historia(date(2025, 9, 1), 240, 1000, 1300, cliente)
    reciente = pcomb.pesos_recientes(h, n=8)["no_habil"]
    todo = pcomb.pesos_recientes(h, n=1000)["no_habil"]
    assert reciente.peso_crudo > 0.9
    assert todo.peso_crudo < reciente.peso_crudo


def test_el_sabado_y_el_habil_se_miden_pero_no_se_combinan():
    h = _historia(date(2026, 1, 1), 120, 1000, 1500, 1000)
    pesos = pcomb.pesos_recientes(h)
    assert pesos["sabado"].peso == 0.0 and pesos["sabado"].mape_cliente == pytest.approx(0.0)
    assert pesos["habil"].peso == 0.0
    assert pesos["no_habil"].peso > 0


def test_un_feriado_en_dia_habil_se_combina_como_domingo():
    feriado = date(2026, 7, 9)                                   # jueves
    assert pcomb.grupo_de_dia(feriado, {feriado}) == "no_habil"
    assert pcomb.grupo_de_dia(date(2026, 7, 11), set()) == "sabado"


def test_el_sabado_no_se_toca_aunque_haya_peso_de_no_habiles_guardado():
    """Un peso "no hábil" guardado antes de separar el sábado no lo puede mover."""
    sab, dom = date(2026, 9, 5), date(2026, 9, 6)
    f = pcomb.factores_diarios({sab: 100.0, dom: 100.0}, {sab: 130.0, dom: 130.0}, _peso(0.4))
    assert sab not in f and dom in f


def test_la_simulacion_reciente_tampoco_mira_el_futuro():
    h = _historia(date(2026, 1, 3), 120, 1000, 1500, 1000)
    filas, _ = pcomb.simular(h, antelacion=2)
    primer_domingo = next(f for f in filas if f["tipo"] == "no_habil")
    assert primer_domingo["peso"] == 0.0
    assert primer_domingo["combinado"] == pytest.approx(1500.0)
    assert any(f["peso"] > 0 for f in filas if f["tipo"] == "no_habil")
    assert all(f["peso"] == 0.0 for f in filas if f["tipo"] in ("habil", "sabado"))
