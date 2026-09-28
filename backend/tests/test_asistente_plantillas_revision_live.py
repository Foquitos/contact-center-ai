"""
Smoke test EN VIVO de la revisión integral de plantillas (`revisar_plantilla`).

POR QUÉ EXISTE
--------------
Todo lo demás de esta función se prueba offline (ver
`tests/test_asistente_plantillas_revision.py`). Lo único que NO se puede verificar sin
llamar a la API es lo más caro de descubrir en producción: que Gemini **acepte el
response_schema**. Es el esquema más grande del asistente (objeto anidado para la
cabecera + array de objetos con enums para los atributos) y, si un modelo lo rechazara,
el error aparecería recién cuando alguien toca "Revisar todo con IA", con los 3
reintentos agotados y sin propuesta.

De paso comprueba que el modelo entiende el contrato de la salida: devolver SOLO los
campos que cambian (con los sentinelas) y no la plantilla entera reescrita.

Se le pasa a propósito una plantilla con problemas evidentes —un criterio de
cumplimiento como Si/No (no puntúa), una lista sin salida segura y un atributo de
calidad con peso 0—, así que una revisión que no proponga NADA es señal de que algo
se rompió.

CONSUME TOKENS (un solo llamado, plantilla chica).
Ejecutar solo esta suite:   pytest tests/test_asistente_plantillas_revision_live.py -m tokens
Excluirla del resto:        pytest -m "not tokens"
"""
import pytest

from AuditorIA import asistente_plantillas as ap

pytestmark = pytest.mark.tokens


PLANTILLA = {
    "nombre": "Calidad Atención Telefónica",
    "descripcion": "Auditoría de llamados entrantes de atención al cliente.",
    "system_prompt": "Sos un analista de calidad. Evaluá el llamado.",
    "recordatorio": "",
    "atributos": [
        # Criterio de cumplimiento como Si/No: hoy no suma al puntaje.
        {"id": 1, "nombre": "Saludo institucional", "tipo": "boolean", "restricciones": None,
         "prompt": "¿Saludó bien?", "orden": 0, "ponderacion": 0, "es_opcional": False},
        # Lista sin salida segura: si el caso real no está, la IA tiene que elegir mal.
        {"id": 2, "nombre": "Motivo del llamado", "tipo": "enum",
         "restricciones": {"enum": ["Reclamo", "Consulta"]},
         "prompt": "Clasificá el motivo por el que llamó el cliente.", "orden": 1,
         "ponderacion": 0, "es_opcional": False},
        # Atributo de calidad con peso 0: no puntúa.
        {"id": 3, "nombre": "Resolución del caso", "tipo": "critical_audit",
         "restricciones": {"enum": ["OK", "NO OK", "EC"]},
         "prompt": "Evaluá si el operador resolvió el motivo del llamado o dejó la gestión encaminada.",
         "orden": 2, "ponderacion": 0, "es_opcional": False},
    ],
}


@pytest.fixture(scope="module")
def revision():
    return ap.revisar_plantilla(PLANTILLA, permitir_eliminar=False)


def test_la_api_acepta_el_esquema_y_devuelve_un_plan(revision):
    assert isinstance(revision["diagnostico"], str) and revision["diagnostico"]
    assert isinstance(revision["atributos"], list)
    assert revision["resumen"]["total"] >= 1, "Una plantilla con problemas evidentes no debería salir sin propuestas."


def test_cada_cambio_viene_con_su_diff_y_su_motivo(revision):
    for attr in revision["atributos"]:
        assert attr["accion"] in ("modificar", "agregar", "eliminar")
        assert attr["cambios"], f"'{attr['nombre_actual']}' se propone sin ningún campo cambiado."
        assert attr["propuesta"]["tipo"] in ap.TIPOS_ATRIBUTO
        for cambio in attr["cambios"]:
            assert cambio["antes"] != cambio["despues"]


def test_no_propone_bajas_cuando_no_se_habilitaron(revision):
    assert all(a["accion"] != "eliminar" for a in revision["atributos"])


def test_respeta_las_reglas_del_sistema(revision):
    """Lo que devuelve tiene que ser guardable tal cual por el editor."""
    for attr in revision["atributos"]:
        propuesta = attr["propuesta"]
        if propuesta["tipo"] == "critical_audit":
            assert propuesta["es_opcional"] is False
            assert propuesta["ponderacion"] > 0
            assert {"OK", "NO OK"} <= {o.upper() for o in propuesta["opciones"]}
        elif propuesta["tipo"] not in ap.TIPOS_CON_OPCIONES:
            assert propuesta["opciones"] == []
