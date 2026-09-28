"""Tests de las métricas del Golden Set. Puros, sin DB ni tokens.

Correr: pytest tests/test_golden_metricas.py -m "not tokens"
"""
import pytest

from AuditorIA.golden_metricas import (
    SIN_RESPUESTA,
    CasoEvaluado,
    ComparacionAtributo,
    acuerdo_entre_revisores,
    coinciden,
    evaluar,
    diagnosticar,
    es_revisable,
    kappa_cohen,
    metricas_de_puntaje,
    metricas_por_atributo,
    normalizar,
)
from AuditorIA import golden_metricas as gm
from AuditorIA.scoring import EC, NA, NO_OK, OK, TIPO_CRITICAL


def _attr(atributo_id, valor_ia, valor_humano, tipo=TIPO_CRITICAL, ponderacion=10.0, nombre=None):
    return ComparacionAtributo(
        atributo_id=atributo_id,
        nombre=nombre or f"Atributo {atributo_id}",
        tipo=tipo,
        ponderacion=ponderacion,
        valor_ia=valor_ia,
        valor_humano=valor_humano,
    )


def _caso(id_aplicativo, *atributos, split="train", auditoria_id=None):
    return CasoEvaluado(
        id_aplicativo=id_aplicativo,
        auditoria_id=auditoria_id,
        split=split,
        atributos=list(atributos),
    )


# --------------------------------------------------------------------------- #
# Normalización                                                                #
# --------------------------------------------------------------------------- #
def test_normaliza_criticos_con_los_alias_de_scoring():
    assert normalizar("ok", TIPO_CRITICAL) == OK
    assert normalizar("NO_OK", TIPO_CRITICAL) == NO_OK
    assert normalizar("Error Crítico", TIPO_CRITICAL) == EC
    # El N/A cae en la clase "sin responder" (None): es la misma verdad que un
    # atributo opcional que la IA dejó vacío. Ver test_las_dos_formas_de_escaparse.
    assert normalizar("no aplica", TIPO_CRITICAL) is None
    assert normalizar(NA, TIPO_CRITICAL) is None


def test_normaliza_booleanos_escritos_de_las_dos_puntas():
    # La IA guarda str(True); el revisor manda true/"Sí" desde la UI.
    assert normalizar(True, "boolean") == normalizar("True", "boolean")
    assert normalizar("true", "boolean") == normalizar("Sí", "boolean")
    assert normalizar(False, "boolean") == normalizar("no", "boolean")


def test_normaliza_numeros_equivalentes():
    assert normalizar("3", "integer") == normalizar(3, "integer") == normalizar(3.0, "number")
    assert normalizar("4,5", "number") == normalizar(4.5, "number")


def test_normaliza_listas_sin_importar_orden_ni_serializacion():
    # repr de Python (lo que queda al guardar la respuesta de la IA con str())
    # vs JSON (lo que manda el revisor). Sin esto, todo array_* daría desacuerdo.
    assert coinciden("['Saludo', 'Despedida']", '["Despedida","Saludo"]', "array_enum")
    assert coinciden(["a", "b"], "['b', 'a']", "array_enum")


def test_vacios_son_todos_lo_mismo():
    assert normalizar(None) is None
    assert normalizar("") is None
    assert normalizar("   ") is None
    assert normalizar([]) is None
    assert coinciden(None, "", "string")


# --------------------------------------------------------------------------- #
# Kappa                                                                        #
# --------------------------------------------------------------------------- #
def test_kappa_acuerdo_perfecto_con_dos_clases():
    pares = [(OK, OK), (NO_OK, NO_OK)] * 5
    assert kappa_cohen(pares) == 1.0


def test_kappa_desenmascara_al_que_siempre_dice_ok():
    # 18 de 20 aciertos (90% de accuracy) respondiendo SIEMPRE OK: el kappa tiene
    # que delatar que no está midiendo nada. Es el motivo de existir de la métrica.
    pares = [(OK, OK)] * 18 + [(OK, NO_OK)] * 2
    aciertos = sum(1 for a, b in pares if a == b)
    assert aciertos / len(pares) == 0.9
    assert kappa_cohen(pares) == 0.0


def test_kappa_es_none_cuando_no_es_calculable():
    # Todos de una sola clase y con desacuerdo: no hay kappa posible.
    assert kappa_cohen([]) is None
    assert kappa_cohen([(OK, OK)]) == 1.0


def test_kappa_negativo_si_es_peor_que_el_azar():
    pares = [(OK, NO_OK), (NO_OK, OK)] * 5
    assert kappa_cohen(pares) < 0


# --------------------------------------------------------------------------- #
# Métricas por atributo                                                        #
# --------------------------------------------------------------------------- #
def test_cuenta_aciertos_y_confusion():
    casos = [
        _caso("A", _attr(1, OK, OK)),
        _caso("B", _attr(1, OK, NO_OK)),
        _caso("C", _attr(1, NO_OK, NO_OK)),
    ]
    (metrica,) = metricas_por_atributo(casos)
    assert metrica.n == 3
    assert metrica.aciertos == 2
    assert metrica.accuracy == pytest.approx(66.67, abs=0.01)
    # La confusión se indexa por la verdad: "cuando era NO OK, ¿qué dijo la IA?"
    assert metrica.confusion[NO_OK] == {OK: 1, NO_OK: 1}


def test_separa_falsos_ec_de_ec_omitidos():
    casos = [
        _caso("A", _attr(1, EC, OK)),      # falso EC: reprueba a quien no falló
        _caso("B", _attr(1, OK, EC)),      # EC omitido: deja pasar un error grave
        _caso("C", _attr(1, EC, EC)),
    ]
    (metrica,) = metricas_por_atributo(casos)
    assert metrica.falsos_ec == 1
    assert metrica.ec_omitidos == 1


def test_las_dos_formas_de_escaparse_cuentan_igual():
    """N/A (critical_audit) y campo omitido (atributo opcional) son el MISMO error:
    la IA no evaluó donde el humano sí pudo. Se cuentan juntos y caen en la misma
    clase de la matriz, así la métrica no depende de con qué tipo se modeló el
    criterio."""
    casos = [
        _caso("A", _attr(1, NA, OK)),       # se escapó por N/A
        _caso("B", _attr(1, None, OK)),     # directamente no respondió (opcional)
    ]
    (metrica,) = metricas_por_atributo(casos)
    assert metrica.sin_responder == 2
    assert metrica.confusion[OK][SIN_RESPUESTA] == 2
    assert not hasattr(metrica, "falsos_na")


def test_na_y_sin_responder_son_acuerdo_entre_si():
    """Si la IA dice N/A y el humano dice "no correspondía responderlo" (o al revés),
    están diciendo lo mismo: es acuerdo, no corrección."""
    assert coinciden(NA, None, "critical_audit")
    assert coinciden(None, NA, "critical_audit")
    assert coinciden("no aplica", "", "critical_audit")


def test_en_un_enum_comun_no_aplica_sigue_siendo_una_respuesta():
    """La unificación es SOLO de critical_audit, donde N/A tiene un significado
    definido (queda fuera del puntaje). En un enum, "No aplica" puede ser una
    categoría legítima elegida por la IA y no debe leerse como "no contestó"."""
    assert normalizar("No aplica", "enum") == "no aplica"
    assert not coinciden("No aplica", None, "enum")


def test_ordena_de_peor_a_mejor_kappa():
    casos = [
        _caso("A", _attr(1, OK, NO_OK, nombre="Roto"), _attr(2, OK, OK, nombre="Sano")),
        _caso("B", _attr(1, NO_OK, OK, nombre="Roto"), _attr(2, NO_OK, NO_OK, nombre="Sano")),
    ]
    metricas = metricas_por_atributo(casos)
    assert metricas[0].nombre == "Roto"


def test_guarda_el_motivo_del_desacuerdo():
    attr = _attr(1, OK, NO_OK)
    attr.motivo = "No verificó identidad"
    (metrica,) = metricas_por_atributo([_caso("A", attr)])
    assert metrica.errores[0]["motivo"] == "No verificó identidad"


# --------------------------------------------------------------------------- #
# Puntaje                                                                      #
# --------------------------------------------------------------------------- #
def test_mae_de_puntaje_usa_el_motor_de_produccion():
    # IA: 70/100 (un NO OK de peso 30). Humano: todo OK -> 100. Desvío = 30.
    caso = _caso("A", _attr(1, OK, OK, ponderacion=70), _attr(2, NO_OK, OK, ponderacion=30))
    resultado = metricas_de_puntaje([caso])
    assert resultado.n == 1
    assert resultado.mae == 30.0


def test_un_falso_ec_cuenta_como_discrepancia_de_veredicto():
    caso = _caso("A", _attr(1, EC, OK, ponderacion=50), _attr(2, OK, OK, ponderacion=50))
    resultado = metricas_de_puntaje([caso])
    assert resultado.ec_falsos == 1
    assert resultado.ec_omitidos == 0
    # El EC pone el llamado en 0 contra los 100 del humano: el desvío es máximo.
    assert resultado.max_error == 100.0


def test_na_renormaliza_igual_que_en_produccion():
    # Humano marca N/A en uno: el otro se renormaliza a 100. La IA lo respondió
    # NO OK, así que publica 50. El desvío es de 50 puntos.
    caso = _caso("A", _attr(1, OK, OK, ponderacion=50), _attr(2, NO_OK, NA, ponderacion=50))
    resultado = metricas_de_puntaje([caso])
    assert resultado.mae == 50.0


def test_plantilla_sin_atributos_ponderados_no_rompe():
    caso = _caso("A", _attr(1, "hola", "chau", tipo="string", ponderacion=0))
    resultado = metricas_de_puntaje([caso])
    assert resultado.n == 0
    assert resultado.mae is None


# --------------------------------------------------------------------------- #
# Resumen                                                                      #
# --------------------------------------------------------------------------- #
def test_evaluar_arma_el_resumen_completo():
    casos = [
        _caso("A", _attr(1, OK, OK, ponderacion=50), _attr(2, OK, OK, ponderacion=50)),
        _caso("B", _attr(1, OK, NO_OK, ponderacion=50), _attr(2, EC, OK, ponderacion=50)),
    ]
    resumen = evaluar(casos)
    assert resumen.casos == 2
    assert resumen.comparaciones == 4
    assert resumen.aciertos == 2
    assert resumen.accuracy == 50.0
    assert len(resumen.por_atributo) == 2
    assert resumen.puntaje.ec_falsos == 1
    assert resumen.as_dict()["kappa_global_txt"]


def test_resumen_vacio_no_explota():
    resumen = evaluar([])
    assert resumen.casos == 0
    assert resumen.accuracy is None
    assert resumen.as_dict()["puntaje"]["n"] == 0


# --------------------------------------------------------------------------- #
# Acuerdo entre humanos (el techo de la IA)                                    #
# --------------------------------------------------------------------------- #
def test_acuerdo_entre_revisores_solo_mira_lo_solapado():
    por_revisor = {
        7: [_caso("A", _attr(1, OK, OK)), _caso("B", _attr(1, OK, OK))],
        9: [_caso("A", _attr(1, OK, NO_OK)), _caso("C", _attr(1, OK, OK))],
    }
    resultado = acuerdo_entre_revisores(por_revisor)
    # Solo el llamado A lo revisaron los dos, y no coincidieron.
    assert resultado["llamados_solapados"] == 1
    assert resultado["comparaciones"] == 1
    assert resultado["acuerdo"] == 0.0


def test_acuerdo_es_none_sin_solapamiento():
    por_revisor = {7: [_caso("A", _attr(1, OK, OK))], 9: [_caso("B", _attr(1, OK, OK))]}
    assert acuerdo_entre_revisores(por_revisor) is None


# --------------------------------------------------------------------------- #
# Texto libre: no se revisa ni se mide                                         #
# --------------------------------------------------------------------------- #
def test_los_atributos_de_texto_libre_no_son_revisables():
    # Un resumen o un feedback no tienen una redacción correcta única: compararlos
    # por igualdad daría desacuerdo siempre y hundiría el kappa sin que nadie se
    # haya equivocado.
    assert not es_revisable("string")
    assert not es_revisable("array_string")
    assert not es_revisable("  STRING  ")
    assert es_revisable("critical_audit")
    assert es_revisable("enum")
    assert es_revisable("boolean")
    assert es_revisable(None)  # sin tipo declarado no se bloquea nada


# --------------------------------------------------------------------------- #
# Diagnóstico: nombrar el patrón del error                                     #
# --------------------------------------------------------------------------- #
def _diagnostico(*atributos):
    (metrica,) = metricas_por_atributo([_caso(f"caso{i}", a) for i, a in enumerate(atributos)])
    return diagnosticar(metrica)


def test_sin_desacuerdos_no_hay_diagnostico():
    assert _diagnostico(_attr(1, OK, OK), _attr(1, NO_OK, NO_OK)) is None


def test_detecta_que_la_ia_perdona_de_mas():
    # Todas las fallas para el mismo lado: es el patrón más accionable.
    dx = _diagnostico(*([_attr(1, OK, NO_OK)] * 6), _attr(1, OK, OK))
    assert dx["codigo"] == "indulgente"
    assert dx["casos"] == 6
    assert dx["es_indicio"] is False


def test_detecta_que_la_ia_exige_de_mas():
    dx = _diagnostico(*([_attr(1, NO_OK, OK)] * 5))
    assert dx["codigo"] == "severa"


def test_los_falsos_ec_mandan_sobre_cualquier_otro_patron():
    # Aunque sean pocos frente al resto: un EC mal puesto deja el llamado en cero.
    dx = _diagnostico(_attr(1, EC, OK), *([_attr(1, OK, NO_OK)] * 5))
    assert dx["codigo"] == "falsos_ec"


def test_detecta_ec_omitidos():
    dx = _diagnostico(_attr(1, OK, EC), _attr(1, OK, OK))
    assert dx["codigo"] == "ec_omitidos"


def test_detecta_que_se_escapa_sin_evaluar():
    dx = _diagnostico(*([_attr(1, NA, OK)] * 4))
    assert dx["codigo"] == "sin_responder"


def test_detecta_confusion_entre_dos_opciones_de_un_enum():
    dx = _diagnostico(*([_attr(1, "Reclamo", "Consulta", tipo="enum", ponderacion=0)] * 4))
    assert dx["codigo"] == "confusion"
    assert "consulta" in dx["titulo"].lower() and "reclamo" in dx["titulo"].lower()


def test_pocos_desacuerdos_se_marcan_como_indicio():
    # Con menos de 5 desacuerdos se informa igual (es lo útil al arrancar, cuando
    # hay pocas revisiones) pero avisando que es una pista, no una conclusión.
    dx = _diagnostico(_attr(1, OK, NO_OK), _attr(1, OK, NO_OK))
    assert dx["es_indicio"] is True


def test_errores_dispersos_se_reportan_como_tales():
    dx = _diagnostico(
        _attr(1, "a", "b", tipo="enum", ponderacion=0),
        _attr(1, "c", "d", tipo="enum", ponderacion=0),
        _attr(1, "e", "f", tipo="enum", ponderacion=0),
    )
    assert dx["codigo"] == "disperso"


def test_el_diccionario_expone_casos_y_diagnostico():
    # Es lo que consume la pantalla: sin esto los desacuerdos concretos y el
    # patrón quedaban calculados pero invisibles.
    (metrica,) = metricas_por_atributo([_caso("A", _attr(1, OK, NO_OK))])
    datos = metrica.as_dict()
    assert datos["errores"][0]["valor_ia"] == OK
    assert datos["diagnostico"]["codigo"] == "indulgente"


# --------------------------------------------------------------------------- #
# ¿Se puede leer este kappa?                                                   #
# --------------------------------------------------------------------------- #
# El caso real que motivó esto: 34 llamados revisados, la IA respondió OK en los
# 34, el humano encontró un solo NO OK. Kappa exactamente 0.00 — pero mostrarlo
# como "muy bajo" manda a reescribir un prompt que quizás está bien.
def _metrica(confusion, n=None, aciertos=None, kappa=0.0):
    total = n if n is not None else sum(
        c for fila in confusion.values() for c in fila.values()
    )
    justos = aciertos if aciertos is not None else sum(
        fila.get(v, 0) for v, fila in confusion.items()
    )
    return gm.MetricasAtributo(
        atributo_id=1, nombre="Escucha activa", tipo="critical_audit",
        n=total, aciertos=justos, kappa=kappa, confusion=confusion,
    )


def test_kappa_cero_por_ia_que_contesta_siempre_lo_mismo_no_es_concluyente():
    metrica = _metrica({"OK": {"OK": 33}, "NO OK": {"OK": 1}})
    # Primero: el 0.00 es real, no un bug de cálculo.
    pares = [("OK", "OK")] * 33 + [("NO OK", "OK")]
    assert gm.kappa_cohen(pares) == 0.0

    conf = gm.confiabilidad_kappa(metrica)
    assert conf["concluyente"] is False
    assert conf["etiqueta"] == "no concluyente"
    assert "OK" in conf["motivo"] and "34" in conf["motivo"]
    assert conf["falta"]


def test_si_el_humano_vio_una_sola_clase_no_hay_nada_que_distinguir():
    conf = gm.confiabilidad_kappa(_metrica({"OK": {"OK": 20}}, kappa=1.0))
    assert conf["concluyente"] is False
    assert "siempre" in conf["motivo"]


def test_una_clase_minoritaria_de_menos_de_cinco_casos_tampoco_concluye():
    """La IA sí usó las dos opciones, pero la muestra tiene 2 NO OK: el kappa lo
    define la composición de la muestra."""
    conf = gm.confiabilidad_kappa(
        _metrica({"OK": {"OK": 30, "NO OK": 1}, "NO OK": {"OK": 1, "NO OK": 1}}, kappa=0.44)
    )
    assert conf["concluyente"] is False
    assert "minoritario" in conf["motivo"]


def test_con_muestra_balanceada_el_kappa_se_lee_con_la_escala_de_siempre():
    conf = gm.confiabilidad_kappa(
        _metrica({"OK": {"OK": 20, "NO OK": 2}, "NO OK": {"OK": 2, "NO OK": 8}}, kappa=0.72)
    )
    assert conf["concluyente"] is True
    assert conf["etiqueta"] == "bueno"
    assert conf["motivo"] is None


def test_sin_revisiones_no_se_inventa_una_lectura():
    conf = gm.confiabilidad_kappa(_metrica({}, n=0, aciertos=0, kappa=None))
    assert conf["concluyente"] is False
    assert "Todavía no hay" in conf["motivo"]


def test_la_confiabilidad_viaja_en_el_diccionario_que_consume_la_pantalla():
    datos = _metrica({"OK": {"OK": 33}, "NO OK": {"OK": 1}}).as_dict()
    assert datos["kappa_confiabilidad"]["concluyente"] is False
    # El número sigue estando: lo que cambia es cómo se lo etiqueta.
    assert datos["kappa"] == 0.0
    assert datos["kappa_txt"] == "muy bajo"
