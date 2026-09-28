"""
Atributos OPCIONALES de las plantillas de auditoría.

Un atributo marcado como opcional (calidad.Atributos.EsOpcional, migración
2026-08-05b) sale del `required` del response_schema de Gemini: si la interacción no
da evidencia, la IA omite la clave y ese atributo queda SIN auditar en vez de
responderse igual y castigar mal al operador (el caso típico: un Si/No que el llamado
no permite contestar).

Test 100% offline: no toca DB, Gemini ni tokens.
Correr: pytest tests/test_atributos_opcionales.py -m "not tokens"
"""
import json

import pytest

from AuditorIA import gemini
from app.models import PlantillasIA


def _plantilla(atributos, prompt_text="Auditá este llamado."):
    return PlantillasIA(
        system_prompts="Sos un auditor.",
        text=prompt_text,
        response_schema=json.dumps(atributos, ensure_ascii=False),
        modelo="gemini-fake",
    )


ATRIBUTOS = [
    {"name": "cumple_saludo", "type": "critical_audit", "constraints": {"enum": ["OK", "NO OK", "EC"]}, "id": 1},
    {"name": "ofrecio_promo", "type": "boolean", "constraints": None, "id": 2, "optional": True},
    {"name": "feedback", "type": "string", "constraints": None, "id": 3, "optional": False},
]


@pytest.fixture
def plantilla_falsa(monkeypatch):
    """Reemplaza la lectura de la plantilla desde la BD por un doble en memoria."""
    def _instalar(atributos, prompt_text="Auditá este llamado."):
        monkeypatch.setattr(
            gemini.plantillas_manager_instance,
            "obtener_plantilla_para_IA",
            lambda plantilla_id: _plantilla(atributos, prompt_text),
        )
        return gemini.prompt(plantilla_id=1)
    return _instalar


def test_el_opcional_sale_del_required_pero_sigue_en_properties(plantilla_falsa):
    """Lo que arregla la feature: la IA puede omitir el boolean que no puede responder."""
    info = plantilla_falsa(ATRIBUTOS)
    schema = info["response_schema"]

    # Sigue ofreciéndose para responder...
    # (`Incidencia` es un campo fijo que se agrega a toda plantilla, ver
    # test_incidencias_auditoria.py; acá se lo descuenta para mirar los atributos.)
    assert set(schema.properties or {}) - {gemini.CAMPO_INCIDENCIA} == {"cumple_saludo", "ofrecio_promo", "feedback"}
    # ...pero no es obligatorio.
    assert "ofrecio_promo" not in (schema.required or [])
    assert set(schema.required or []) - {gemini.CAMPO_INCIDENCIA} == {"cumple_saludo", "feedback"}


def test_sin_la_marca_todo_sigue_siendo_obligatorio(plantilla_falsa):
    """Retrocompatibilidad: si la migración no está aplicada, el SP no manda 'optional'."""
    sin_marca = [{k: v for k, v in a.items() if k != "optional"} for a in ATRIBUTOS]
    info = plantilla_falsa(sin_marca)

    assert set(info["response_schema"].required or []) - {gemini.CAMPO_INCIDENCIA} == {"cumple_saludo", "ofrecio_promo", "feedback"}
    assert "ATRIBUTOS OPCIONALES" not in info["text"]


def test_el_prompt_le_avisa_a_la_ia_cuales_puede_omitir(plantilla_falsa):
    """Sacarlo del required no alcanza: el modelo completa igual si nadie se lo dice."""
    info = plantilla_falsa(ATRIBUTOS)

    assert info["text"].startswith("Auditá este llamado.")
    assert "ATRIBUTOS OPCIONALES" in info["text"]
    assert "'ofrecio_promo'" in info["text"]
    assert "cumple_saludo" not in info["text"].split("ATRIBUTOS OPCIONALES")[1]


def test_el_mapa_nombre_id_incluye_a_los_opcionales(plantilla_falsa):
    """Si la IA sí lo responde, tiene que poder guardarse contra su AtributoID."""
    info = plantilla_falsa(ATRIBUTOS)
    assert info["nombre_id_map"] == {"cumple_saludo": 1, "ofrecio_promo": 2, "feedback": 3}


def test_todos_opcionales_no_rompe_el_esquema(plantilla_falsa):
    """Caso borde: plantilla enteramente opcional -> required vacío, sin excepción."""
    todos = [dict(a, optional=True) for a in ATRIBUTOS]
    info = plantilla_falsa(todos)

    assert not (set(info["response_schema"].required or []) - {gemini.CAMPO_INCIDENCIA})
    assert len(set(info["response_schema"].properties or {}) - {gemini.CAMPO_INCIDENCIA}) == 3
