"""Muestreo balanceado por atributo: qué llamados conviene mandar a revisar.

Sin tokens y sin BD: golden_muestreo es puro. Lo que se prueba acá es el criterio
que decide en qué gasta Calidad su tiempo de escucha, así que las expectativas
están escritas en términos de negocio ("elige el que destraba el atributo que no
se puede medir"), no de la implementación del greedy.
"""
import pytest

from AuditorIA import golden_muestreo as gm


# --------------------------------------------------------------------------- #
# Déficit                                                                      #
# --------------------------------------------------------------------------- #
def test_una_celda_completa_no_figura_como_faltante():
    faltan = gm.deficit_por_celda({1: {"OK": 5, "NO OK": 2}}, objetivo=5)
    assert (1, "OK") not in faltan
    assert faltan[(1, "NO OK")] == 3


def test_las_opciones_que_nunca_aparecieron_son_las_que_mas_faltan():
    """Una opción declarada en la plantilla que nadie revisó nunca es el caso más
    grave: no hay ninguna revisión que la nombre, así que sin `valores_esperados`
    sería invisible."""
    faltan = gm.deficit_por_celda(
        {1: {"OK": 5}}, objetivo=5, valores_esperados={1: ["OK", "NO OK", "EC"]}
    )
    assert faltan[(1, "NO OK")] == 5
    assert faltan[(1, "EC")] == 5
    assert (1, "OK") not in faltan


def test_un_atributo_con_demasiadas_opciones_no_se_persigue_celda_por_celda():
    """Una tipología de 30 valores se comería la muestra entera para medir algo que
    casi no mueve el puntaje."""
    muchos = {f"v{i}": 0 for i in range(20)}
    faltan = gm.deficit_por_celda({7: muchos}, objetivo=5)
    assert faltan == {}


# --------------------------------------------------------------------------- #
# Elección                                                                     #
# --------------------------------------------------------------------------- #
def _cand(auditoria_id, valores):
    return {"AuditoriaID": auditoria_id, "valores": valores}


def test_elige_el_llamado_que_destraba_el_atributo_sin_medir():
    """El escenario que motivó todo esto: 33 llamados en OK y uno solo con NO OK.
    El que aporta es el que trae el valor que falta, no el más reciente."""
    faltan = gm.deficit_por_celda(
        {1: {"OK": 30, "NO OK": 0}}, objetivo=5, valores_esperados={1: ["OK", "NO OK"]}
    )
    elegidas = gm.elegir_balanceado(
        [_cand(10, {1: "OK"}), _cand(11, {1: "OK"}), _cand(12, {1: "NO OK"})],
        faltan, 1,
    )
    assert [c["AuditoriaID"] for c in elegidas] == [12]
    assert elegidas[0]["aporte"] == 1


def test_no_alcanza_con_balancear_el_llamado_hay_que_balancear_los_atributos():
    """Dos llamados 'con fallas' son distintos si fallan en atributos distintos.
    La estratificación por resultado los trata igual; esta no."""
    faltan = gm.deficit_por_celda(
        {1: {"OK": 5, "NO OK": 5}, 2: {"OK": 5, "NO OK": 0}},
        objetivo=5,
        valores_esperados={1: ["OK", "NO OK"], 2: ["OK", "NO OK"]},
    )
    # El primero falla en el atributo ya medido; el segundo en el que falta.
    elegidas = gm.elegir_balanceado(
        [_cand(10, {1: "NO OK", 2: "OK"}), _cand(11, {1: "OK", 2: "NO OK"})],
        faltan, 1,
    )
    assert [c["AuditoriaID"] for c in elegidas] == [11]


def test_prefiere_el_que_llena_varias_celdas_de_una_escucha():
    """Una escucha cubre todos los atributos del llamado a la vez: por eso conviene
    el que trae varios valores faltantes juntos."""
    faltan = gm.deficit_por_celda(
        {1: {"OK": 5, "NO OK": 0}, 2: {"OK": 5, "NO OK": 0}},
        objetivo=5,
        valores_esperados={1: ["OK", "NO OK"], 2: ["OK", "NO OK"]},
    )
    elegidas = gm.elegir_balanceado(
        [_cand(10, {1: "NO OK", 2: "OK"}), _cand(11, {1: "NO OK", 2: "NO OK"})],
        faltan, 1,
    )
    assert [c["AuditoriaID"] for c in elegidas] == [11]
    assert elegidas[0]["aporte"] == 2


def test_deja_de_insistir_con_una_celda_ya_cubierta():
    """Después de elegir suficientes casos de un valor, el algoritmo pasa a otro:
    si no, se llevaría 20 llamados idénticos."""
    faltan = {(1, "NO OK"): 2, (2, "NO OK"): 2}
    candidatas = [_cand(i, {1: "NO OK"}) for i in range(10, 15)]
    candidatas.append(_cand(99, {2: "NO OK"}))
    elegidas = gm.elegir_balanceado(candidatas, faltan, 3)
    assert 99 in [c["AuditoriaID"] for c in elegidas]


def test_los_motivos_explican_por_que_esta_recomendado():
    faltan = {(1, "NO OK"): 4}
    elegidas = gm.elegir_balanceado(
        [_cand(10, {1: "NO OK"})], faltan, 1, nombres={1: "Verifica identidad"}
    )
    motivo = elegidas[0]["motivos"][0]
    assert motivo["nombre"] == "Verifica identidad"
    assert motivo["valor"] == "NO OK"
    assert motivo["faltaban"] == 4


def test_completa_el_cupo_aunque_ya_no_haya_nada_que_aportar():
    """Con la grilla cubierta, más volumen sigue sirviendo; devolver menos llamados
    de los pedidos sería confuso."""
    elegidas = gm.elegir_balanceado(
        [_cand(10, {1: "OK"}), _cand(11, {1: "OK"})], {}, 2
    )
    assert len(elegidas) == 2
    assert all(c["aporte"] == 0 for c in elegidas)
    assert all(c["motivos"] == [] for c in elegidas)


def test_no_devuelve_mas_de_lo_pedido():
    elegidas = gm.elegir_balanceado(
        [_cand(i, {1: "NO OK"}) for i in range(10)], {(1, "NO OK"): 9}, 3
    )
    assert len(elegidas) == 3


def test_no_muta_el_deficit_ni_las_candidatas_recibidas():
    """El déficit se reusa para calcular el resumen de aporte: si el greedy lo
    mutara, el 'antes' y el 'después' saldrían iguales."""
    faltan = {(1, "NO OK"): 3}
    candidatas = [_cand(10, {1: "NO OK"})]
    gm.elegir_balanceado(candidatas, faltan, 1)
    assert faltan == {(1, "NO OK"): 3}
    assert "aporte" not in candidatas[0]


# --------------------------------------------------------------------------- #
# Resumen                                                                      #
# --------------------------------------------------------------------------- #
def test_el_resumen_dice_cuantos_criterios_se_destraban():
    faltan = {(1, "NO OK"): 1, (2, "NO OK"): 1}
    elegidas = gm.elegir_balanceado(
        [_cand(10, {1: "NO OK", 2: "NO OK"})], faltan, 1
    )
    resumen = gm.resumen_aporte(elegidas, faltan)
    assert resumen["celdas_flojas_antes"] == 2
    assert resumen["celdas_flojas_despues"] == 0


def test_celdas_flojas_ordena_lo_peor_primero():
    filas = gm.celdas_flojas({(1, "OK"): 1, (2, "NO OK"): 5}, {1: "Uno", 2: "Dos"}, objetivo=5)
    assert filas[0]["nombre"] == "Dos"
    assert filas[0]["revisados"] == 0


# --------------------------------------------------------------------------- #
# Filtro del listado de Auditorías Realizadas                                  #
# --------------------------------------------------------------------------- #
# Quien revisa vive en "Auditorías Realizadas", no en la pantalla de Golden Set:
# el trabajo de revisión tiene que poder pedirse desde el listado.
def test_los_filtros_de_revision_son_una_lista_cerrada():
    """Un nombre desconocido tiene que fallar, no caer en un `else` que devuelva
    todo: filtrar mal y traer de más es peor que no filtrar, porque nadie lo nota."""
    from AuditorIA import golden_set

    with pytest.raises(ValueError):
        golden_set.ids_para_filtro(None, plantilla_id=1, filtro="lo_que_sea")

    assert set(golden_set.FILTROS_REVISION) == {
        "golden_set", "para_reauditar", "recomendadas", "revisadas", "sin_revisar",
    }
