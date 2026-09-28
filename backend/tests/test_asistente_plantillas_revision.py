"""
Revisión integral de una plantilla con IA (asistente_plantillas.revisar_plantilla).

Lo que se prueba NO es la calidad de lo que escribe Gemini (eso no es testeable), sino
la capa determinística que lo envuelve, que es donde están los riesgos reales:

- el DIFF campo por campo se calcula acá y no se le cree al modelo (si dice que cambió
  algo y devolvió lo mismo, el usuario no tiene que ver un cambio vacío);
- una propuesta se resuelve contra un atributo REAL de la plantilla (por id, o por
  nombre si el modelo perdió el id); un id que no existe no puede terminar tocando otro;
- las reglas del sistema se imponen sobre lo que diga el modelo (critical_audit lleva
  OK/NO OK, no usa la marca de opcional y no puede quedar con peso 0; los tipos sin
  lista no llevan opciones);
- lo que el guardado va a rechazar (el "atributo transcripción") se marca bloqueado
  ANTES de ofrecerlo, no cuando falla al aplicar;
- eliminar atributos solo se propone si el usuario lo habilitó.

Test 100% offline: no toca DB, Gemini ni tokens.
Correr: pytest tests/test_asistente_plantillas_revision.py -m "not tokens"
"""
import pytest

from AuditorIA import asistente_plantillas as ap


PLANTILLA = {
    "nombre": "Calidad Cobranzas",
    "descripcion": "Auditoría de llamados de cobranza",
    "system_prompt": "Sos un analista de calidad senior.",
    "recordatorio": "Basate solo en lo que se escucha.",
    "atributos": [
        {"id": 10, "nombre": "Saludo inicial", "prompt": "¿El operador saludó con nombre y empresa? " * 5,
         "tipo": "boolean", "restricciones": None, "orden": 0, "ponderacion": 0, "es_opcional": False},
        {"id": 11, "nombre": "Motivo del contacto", "prompt": "Clasificá el motivo del llamado. " * 5,
         "tipo": "enum", "restricciones": {"enum": ["Pago", "Reclamo"]}, "orden": 1,
         "ponderacion": 0, "es_opcional": False},
        {"id": 12, "nombre": "Cierre correcto", "prompt": "Evaluá si cerró la gestión como corresponde. " * 5,
         "tipo": "critical_audit", "restricciones": {"enum": ["OK", "NO OK", "EC"]}, "orden": 2,
         "ponderacion": 100, "es_opcional": False},
    ],
}


@pytest.fixture
def ia(monkeypatch):
    """Reemplaza el llamado a Gemini por una respuesta fija (la que quiera cada test)."""
    def _instalar(respuesta, **kwargs):
        monkeypatch.setattr(ap, "_generar_json", lambda *a, **k: respuesta)
        return ap.revisar_plantilla(PLANTILLA, **kwargs)
    return _instalar


def _por_id(resultado, atributo_id):
    return next((a for a in resultado["atributos"] if a["id"] == atributo_id), None)


def test_cambio_de_tipo_arrastra_opciones_peso_y_advertencia(ia):
    """El caso que motivó la función: un criterio de cumplimiento guardado como Si/No
    que en realidad tiene que puntuar. mejorar_prompt() no podía tocar esto."""
    resultado = ia({
        "diagnostico": "El saludo debería puntuar.",
        "atributos": [{"accion": "modificar", "id": 10, "nombre_actual": "Saludo inicial",
                       "tipo": "critical_audit", "opciones": ["OK", "NO OK", "EC", "N/A"],
                       "ponderacion": 20, "motivos": ["Es un criterio de cumplimiento."]}],
    })
    attr = _por_id(resultado, 10)
    campos = {c["campo"]: c for c in attr["cambios"]}
    assert attr["accion"] == "modificar"
    assert campos["tipo"]["antes"] == "boolean" and campos["tipo"]["despues"] == "critical_audit"
    assert campos["opciones"]["despues"] == ["OK", "NO OK", "EC", "N/A"]
    assert campos["ponderacion"]["despues"] == 20
    # El prompt no vino en la propuesta: no cambia y no aparece en el diff.
    assert "prompt" not in campos
    assert attr["propuesta"]["prompt"] == PLANTILLA["atributos"][0]["prompt"].strip()
    assert attr["propuesta"]["restricciones"] == {"enum": ["OK", "NO OK", "EC", "N/A"]}
    assert attr["impacto"] == "alto"
    assert any("tipo de dato" in a for a in attr["advertencias"])
    assert any("puntaje" in a for a in attr["advertencias"])


def test_propuesta_identica_no_genera_cambio(ia):
    """El modelo dice 'modificar' y devuelve exactamente lo mismo: no se ofrece nada."""
    resultado = ia({
        "diagnostico": "",
        "atributos": [{"accion": "modificar", "id": 11, "opciones": ["Pago", "Reclamo"],
                       "tipo": "enum", "es_opcional": "no", "ponderacion": -1}],
    })
    assert resultado["atributos"] == []
    assert resultado["resumen"]["total"] == 0
    assert resultado["resumen"]["sin_cambios"] == 3


def test_enum_sin_salida_segura_se_advierte_y_la_senal_lo_detecta_sin_ia(ia):
    resultado = ia({
        "diagnostico": "",
        "atributos": [{"accion": "modificar", "id": 11, "opciones": ["Pago", "Reclamo", "Promesa"]}],
    })
    attr = _por_id(resultado, 11)
    assert any("salida segura" in a for a in attr["advertencias"])
    # La señal se calcula sin IA sobre el estado actual: sirve aunque el modelo no diga nada.
    assert any(s["clave"] == "enum_sin_salida_segura" for s in resultado["senales"])


def test_quitar_opciones_avisa_que_el_historico_las_conserva(ia):
    resultado = ia({
        "diagnostico": "",
        "atributos": [{"accion": "modificar", "id": 11, "opciones": ["Pago", "Otros"]}],
    })
    attr = _por_id(resultado, 11)
    assert any("Reclamo" in a for a in attr["advertencias"])


def test_critical_audit_no_queda_opcional_ni_con_peso_cero(ia):
    """Reglas del sistema por encima de lo que diga el modelo: en calidad ponderada el
    'no aplica' es la opción N/A, y un peso 0 haría que el atributo no puntúe."""
    resultado = ia({
        "diagnostico": "",
        "atributos": [{"accion": "modificar", "id": 12, "es_opcional": "si", "ponderacion": 0,
                       "opciones": ["Cumple", "No cumple"]}],
    })
    attr = _por_id(resultado, 12)
    assert attr["propuesta"]["es_opcional"] is False
    assert attr["propuesta"]["ponderacion"] > 0
    # OK / NO OK son obligatorias: sin ellas scoring.py no puede puntuar.
    assert {"OK", "NO OK"} <= set(attr["propuesta"]["opciones"])


def test_tipo_sin_lista_se_queda_sin_opciones(ia):
    resultado = ia({
        "diagnostico": "",
        "atributos": [{"accion": "modificar", "id": 11, "tipo": "string",
                       "prompt": "Resumí en una línea el motivo del llamado."}],
    })
    attr = _por_id(resultado, 11)
    assert attr["propuesta"]["opciones"] == []
    assert attr["propuesta"]["restricciones"] is None


def test_marca_de_opcional_se_puede_prender_y_apagar(ia):
    resultado = ia({
        "diagnostico": "",
        "atributos": [{"accion": "modificar", "id": 10, "es_opcional": "si"}],
    })
    attr = _por_id(resultado, 10)
    assert attr["propuesta"]["es_opcional"] is True
    assert [c["campo"] for c in attr["cambios"]] == ["es_opcional"]


def test_id_desconocido_no_toca_otro_atributo_y_entra_como_alta(ia):
    """Un id que no es de esta plantilla no puede terminar modificando algo: si no
    matchea (ni por id ni por nombre) se ofrece como atributo nuevo."""
    resultado = ia({
        "diagnostico": "",
        "atributos": [{"accion": "modificar", "id": 99999, "nombre": "Empatía",
                       "prompt": "Evaluá si el operador mostró empatía.", "tipo": "critical_audit",
                       "ponderacion": 10}],
    })
    assert len(resultado["atributos"]) == 1
    attr = resultado["atributos"][0]
    assert attr["accion"] == "agregar" and attr["id"] is None
    assert attr["propuesta"]["orden"] == 3  # al fondo de la lista


def test_atributo_existente_se_resuelve_por_nombre_si_falta_el_id(ia):
    resultado = ia({
        "diagnostico": "",
        "atributos": [{"accion": "modificar", "nombre_actual": "saludo inicial ",
                       "es_opcional": "si"}],
    })
    attr = resultado["atributos"][0]
    assert attr["id"] == 10 and attr["accion"] == "modificar"


def test_alta_sin_prompt_no_se_ofrece(ia):
    resultado = ia({
        "diagnostico": "",
        "atributos": [{"accion": "agregar", "nombre": "Algo", "tipo": "boolean"}],
    })
    assert resultado["atributos"] == []


def test_no_propone_bajas_si_no_se_habilitaron(ia):
    respuesta = {"diagnostico": "", "atributos": [
        {"accion": "eliminar", "id": 11, "motivos": ["Se solapa con el cierre."]}]}
    assert ia(respuesta)["atributos"] == []
    assert ia(respuesta, permitir_eliminar=True)["atributos"][0]["accion"] == "eliminar"


def test_baja_avisa_que_el_atributo_deja_de_auditarse(ia):
    resultado = ia({"diagnostico": "", "atributos": [{"accion": "eliminar", "id": 11}]},
                   permitir_eliminar=True)
    attr = _por_id(resultado, 11)
    assert attr["impacto"] == "alto"
    assert any("deja de auditarse" in a for a in attr["advertencias"])
    assert resultado["resumen"]["eliminar"] == 1


def test_pedido_de_transcripcion_queda_bloqueado_antes_de_aplicar(ia):
    """El guardado lo rechaza con un 400 (AuditorIA/limites_texto.py). Ofrecerlo y que
    falle recién al aplicar sería la peor versión de la misma regla."""
    resultado = ia({
        "diagnostico": "",
        "atributos": [{"accion": "agregar", "nombre": "Detalle", "tipo": "string",
                       "prompt": "Transcribí el llamado completo palabra por palabra."}],
    })
    assert resultado["atributos"][0]["bloqueado"]


def test_enum_nuevo_sin_opciones_queda_bloqueado(ia):
    resultado = ia({
        "diagnostico": "",
        "atributos": [{"accion": "agregar", "nombre": "Canal", "tipo": "enum",
                       "prompt": "Indicá por qué canal se contactó al cliente."}],
    })
    assert resultado["atributos"][0]["bloqueado"] == "la lista quedaría sin opciones cargadas"


def test_cabecera_solo_reporta_lo_que_el_modelo_reescribio(ia):
    resultado = ia({
        "diagnostico": "Falta la regla de N/A.",
        "atributos": [],
        "cabecera": {"recordatorio": "Basate solo en lo que se escucha. Si un criterio no aplica, respondé N/A.",
                     "motivos": ["Sumé la regla de N/A."]},
    })
    cabecera = resultado["cabecera"]
    assert cabecera["cambia"] is True
    assert [c["campo"] for c in cabecera["cambios"]] == ["recordatorio"]
    # Los campos que no tocó conservan el valor actual (el frontend manda el set completo).
    assert cabecera["valores"]["system_prompt"] == PLANTILLA["system_prompt"]
    assert resultado["resumen"]["cabecera"] == 1


def test_las_senales_no_dependen_del_modelo(ia):
    """Se calculan sobre el estado actual, así que el diagnóstico sirve incluso si el
    modelo no propone nada. Cada una viene con su clave, su severidad y el atributo al
    que apunta: así el chequeo de salud puede agrupar sin volver a parsear texto."""
    resultado = ia({"diagnostico": "", "atributos": []})
    por_clave = {s["clave"]: s for s in resultado["senales"]}
    assert "no_opcional" in por_clave                    # boolean que la IA no puede omitir
    assert "critical_sin_na" in por_clave                # calidad ponderada sin N/A
    assert por_clave["enum_sin_salida_segura"]["atributo_id"] == 11
    assert por_clave["enum_sin_salida_segura"]["severidad"] == ap.SEVERIDAD_ALTA
    assert ap.frases_de_senales(resultado["senales"])[0]


# --------------------------------------------------------------------------- #
# Re-pedir UN cambio ("no me convence, hacelo así")                            #
# --------------------------------------------------------------------------- #
# Sin esto, la única salida era descartar la tarjeta y volver a revisar la plantilla
# entera: otro llamado caro, y encima se perdía el resto de la propuesta.
@pytest.fixture
def ia_atributo(monkeypatch):
    def _instalar(respuesta, **kwargs):
        monkeypatch.setattr(ap, "_generar_json", lambda *a, **k: respuesta)
        return ap.revisar_atributo(PLANTILLA, **kwargs)
    return _instalar


def test_el_repedido_parte_de_la_propuesta_anterior(ia_atributo):
    """El usuario solo pide tocar las opciones: lo que ya se había propuesto para el
    resto (acá, el prompt reescrito) tiene que sobrevivir."""
    item = ia_atributo(
        {"accion": "modificar", "opciones": ["Pago", "Reclamo", "Promesa de pago", "Otros"]},
        atributo_id=11,
        instruccion_usuario="sumale 'Promesa de pago'",
        propuesta_previa={"prompt": "Prompt ya mejorado en la propuesta anterior.",
                          "opciones": ["Pago", "Reclamo", "Otros"]},
    )
    campos = {c["campo"]: c for c in item["cambios"]}
    assert item["accion"] == "modificar" and item["id"] == 11
    assert "Promesa de pago" in campos["opciones"]["despues"]
    assert campos["prompt"]["despues"] == "Prompt ya mejorado en la propuesta anterior."


def test_el_repedido_trae_advertencias_y_bloqueo_como_cualquier_cambio(ia_atributo):
    """Se arma con el mismo constructor que la revisión completa: si no, una tarjeta
    rehecha llegaría sin advertencias y se podría aplicar algo que el guardado rechaza."""
    item = ia_atributo(
        {"accion": "modificar", "tipo": "string", "nombre": "Transcripción del llamado",
         "prompt": "Transcribí el llamado completo palabra por palabra."},
        atributo_id=11, instruccion_usuario="que sea texto libre",
    )
    assert item["bloqueado"]
    assert any("tipo de dato" in a for a in item["advertencias"])


def test_el_repedido_de_un_alta_sigue_siendo_un_alta(ia_atributo):
    item = ia_atributo(
        {"accion": "agregar", "nombre": "Empatía", "prompt": "Evaluá el trato.",
         "tipo": "critical_audit", "ponderacion": 5},
        atributo_id=None, accion="agregar", instruccion_usuario="más corto",
    )
    assert item["accion"] == "agregar" and item["id"] is None
    assert item["propuesta"]["orden"] == 3


def test_el_repedido_necesita_saber_que_cambiar(ia_atributo):
    with pytest.raises(ValueError):
        ia_atributo({}, atributo_id=11, instruccion_usuario="   ")


def test_si_vuelve_igual_lo_avisa(ia_atributo):
    item = ia_atributo({"accion": "modificar"}, atributo_id=11,
                       instruccion_usuario="dejalo como está")
    assert item["sin_cambios"] is True
