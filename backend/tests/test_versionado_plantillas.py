"""Tests del versionado de plantillas (hash y diff). Puros, sin DB ni tokens.

Correr: pytest tests/test_versionado_plantillas.py -m "not tokens"
"""
import copy

from AuditorIA.versionado import diff_snapshots, hash_snapshot


def _snapshot(**cambios):
    base = {
        "plantilla_id": 7,
        "nombre": "Calidad Cobranzas",
        "system_prompt": "Sos un analista de calidad senior.",
        "recordatorio": "Si el criterio no aplica, respondé N/A.",
        "modelo": "gemini-3.8-flash",
        "atributos": [
            {
                "atributo_id": 1, "nombre": "Saludo", "tipo": "critical_audit",
                "prompt": "¿Saludó con el protocolo?", "restricciones": '{"enum":["OK","NO OK"]}',
                "ponderacion": 30.0, "es_opcional": False,
            },
            {
                "atributo_id": 2, "nombre": "Identidad", "tipo": "critical_audit",
                "prompt": "¿Verificó identidad?", "restricciones": '{"enum":["OK","NO OK","EC"]}',
                "ponderacion": 70.0, "es_opcional": False,
            },
        ],
    }
    base.update(cambios)
    return base


# --------------------------------------------------------------------------- #
# Hash: la identidad de una versión                                            #
# --------------------------------------------------------------------------- #
def test_mismo_contenido_mismo_hash():
    # Es lo que hace idempotente el alta automática: auditar mil veces con la
    # misma plantilla no puede crear mil versiones.
    assert hash_snapshot(_snapshot()) == hash_snapshot(_snapshot())


def test_el_orden_de_las_claves_no_cambia_el_hash():
    a = _snapshot()
    b = {clave: a[clave] for clave in reversed(list(a))}
    assert hash_snapshot(a) == hash_snapshot(b)


def test_cambiar_el_prompt_cambia_el_hash():
    otro = _snapshot(system_prompt="Sos un auditor estricto.")
    assert hash_snapshot(_snapshot()) != hash_snapshot(otro)


def test_cambiar_la_ponderacion_cambia_el_hash():
    # La ponderación cambia el puntaje publicado: es una versión distinta aunque
    # el texto del prompt no se haya tocado.
    otro = _snapshot()
    otro["atributos"][0]["ponderacion"] = 50.0
    assert hash_snapshot(_snapshot()) != hash_snapshot(otro)


def test_volver_atras_recupera_el_hash_original():
    # Editar y arrepentirse NO genera una versión nueva: vuelve a la que ya existía.
    original = _snapshot()
    editado = _snapshot(recordatorio="Otra cosa")
    vuelto = _snapshot()
    assert hash_snapshot(original) != hash_snapshot(editado)
    assert hash_snapshot(original) == hash_snapshot(vuelto)


# --------------------------------------------------------------------------- #
# Diff: qué se vence del Golden Set                                            #
# --------------------------------------------------------------------------- #
def test_sin_cambios_no_vence_nada():
    diff = diff_snapshots(_snapshot(), _snapshot())
    assert diff["hay_cambios"] is False
    assert diff["atributos_afectados"] == []


def test_solo_vence_el_atributo_que_cambio():
    # El punto de todo el diseño: si cambió el criterio del atributo 2, la verdad
    # humana del atributo 1 sigue valiendo. Vencer el set entero sería tirar trabajo.
    despues = _snapshot()
    despues["atributos"][1]["prompt"] = "¿Verificó identidad con DNI y domicilio?"
    diff = diff_snapshots(_snapshot(), despues)
    assert diff["atributos_afectados"] == [2]
    assert diff["atributos"]["modificados"][0]["nombre"] == "Identidad"
    assert diff["atributos"]["modificados"][0]["campos"][0]["campo"] == "prompt"


def test_detecta_atributos_agregados_y_quitados():
    despues = _snapshot()
    despues["atributos"] = [despues["atributos"][0], {
        "atributo_id": 3, "nombre": "Despedida", "tipo": "boolean",
        "prompt": "¿Se despidió?", "restricciones": None,
        "ponderacion": 0.0, "es_opcional": True,
    }]
    diff = diff_snapshots(_snapshot(), despues)
    assert [a["atributo_id"] for a in diff["atributos"]["agregados"]] == [3]
    assert [a["atributo_id"] for a in diff["atributos"]["quitados"]] == [2]
    assert diff["atributos_afectados"] == [2, 3]


def test_cambio_de_cabecera_se_reporta_aparte():
    diff = diff_snapshots(_snapshot(), _snapshot(recordatorio="Nueva regla transversal."))
    assert diff["hay_cambios"] is True
    assert diff["cabecera"][0]["campo"] == "recordatorio"
    # Un cambio de recordatorio afecta a TODOS los atributos, pero no se listan
    # uno por uno: se informa como cambio de cabecera y el evaluador lo muestra.
    assert diff["atributos_afectados"] == []


def test_reordenar_atributos_no_cuenta_como_cambio():
    # El snapshot ordena por AtributoID justamente para esto: mover un criterio de
    # lugar en el editor no cambia lo que la IA responde.
    despues = _snapshot()
    despues["atributos"] = list(reversed(copy.deepcopy(despues["atributos"])))
    diff = diff_snapshots(_snapshot(), despues)
    assert diff["hay_cambios"] is False


def test_diff_con_version_faltante_no_explota():
    diff = diff_snapshots(None, _snapshot())
    assert diff["hay_cambios"] is False
    assert diff["atributos_afectados"] == []
