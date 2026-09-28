"""
Tests offline (cero tokens, cero red) de la construcción del LLM del chatbot.

Cubren el modo de falla silenciosa que apareció al migrar de `llama-index-llms-gemini`
(deprecado, sobre el SDK retirado google-generativeai) a `llama-index-llms-google-genai`:
GoogleGenAI IGNORA el argumento suelto `temperature` cuando recibe un
`generation_config` propio —y nosotros se lo pasamos sí o sí, porque ahí viajan las
safety settings—. Si la temperatura no va DENTRO del config, los 13 bots pasan a
responder con la temperatura por defecto de la API sin que falle nada.
"""
import pytest
from google.genai import types

from app import rag_settings


class _FakeModels:
    def get(self, model):  # noqa: ARG002 - la firma la fija el SDK
        return types.Model(
            name="models/fake", input_token_limit=1_000_000, output_token_limit=8192
        )


class _FakeClient:
    """Evita el `client.models.get(...)` que GoogleGenAI hace al construirse."""

    def __init__(self, *args, **kwargs):
        self.models = _FakeModels()
        self.aio = None
        self.vertexai = False


@pytest.fixture
def sin_red(monkeypatch):
    import google.genai

    monkeypatch.setattr(google.genai, "Client", _FakeClient)


def _config_de(llm) -> dict:
    return llm._generation_config or {}


@pytest.mark.parametrize("temperatura", [0.0, 0.1, 0.4, 0.9])
def test_temperatura_llega_al_generation_config(sin_red, temperatura):
    """La temperatura por bot (pagina_web.Chatbots.temperatura) tiene que llegar
    a la config que se manda a la API, no quedarse en el parámetro suelto."""
    llm = rag_settings._construir_llm(temperatura)

    assert _config_de(llm)["temperature"] == pytest.approx(temperatura)


def test_safety_settings_siguen_en_block_none(sin_red):
    """Los manuales de call center hablan de cortes, deudas y emergencias: con los
    filtros por defecto de Gemini se bloqueaban respuestas legítimas."""
    config = _config_de(rag_settings._construir_llm(0.4))
    safety = config.get("safety_settings") or []

    assert len(safety) == 4, "se perdió alguna categoría de safety settings"
    for ajuste in safety:
        umbral = ajuste["threshold"] if isinstance(ajuste, dict) else ajuste.threshold
        assert str(umbral).endswith("BLOCK_NONE")


def test_get_llm_cachea_por_temperatura_y_no_las_mezcla(sin_red, monkeypatch):
    """Una instancia por temperatura distinta (no una por bot): así no se
    multiplican las conexiones a Gemini, pero cada temperatura conserva la suya."""
    monkeypatch.setattr(rag_settings, "_llms_por_temp", {})
    monkeypatch.setattr(rag_settings, "configure_global_settings", lambda: (None, None))

    frio = rag_settings.get_llm(0.1)
    calido = rag_settings.get_llm(0.8)
    frio_otra_vez = rag_settings.get_llm(0.1)

    assert frio is frio_otra_vez, "misma temperatura debería reutilizar la instancia"
    assert frio is not calido
    assert _config_de(frio)["temperature"] == pytest.approx(0.1)
    assert _config_de(calido)["temperature"] == pytest.approx(0.8)


def test_el_bot_no_gasta_en_razonamiento(sin_red):
    """El bot responde con el contexto RAG ya recuperado delante: la tarea es redactar
    a partir de fragmentos, no razonar, y es la ruta de MÁS volumen del sistema (una
    consulta por interacción de operador). Cada token de pensamiento se factura a
    tarifa de salida.

    El nivel efectivo depende del modelo: los "lite" aceptan MINIMAL, y si el .env
    apunta DEFAULT_REMOTE_LLM_MODEL a uno que no lo soporta, razonamiento.py lo degrada
    a LOW en vez de que la API responda 400 (ver AuditorIA/razonamiento.py)."""
    config = _config_de(rag_settings._construir_llm(0.4))
    thinking = config.get("thinking_config")

    assert thinking is not None, "el LLM del chatbot se quedó sin thinking_config"
    nivel = thinking["thinking_level"] if isinstance(thinking, dict) else thinking.thinking_level
    presupuesto = thinking["thinking_budget"] if isinstance(thinking, dict) else thinking.thinking_budget

    assert nivel in ("MINIMAL", "LOW")
    # thinking_budget es letra muerta en Gemini 3.x: si vuelve, vuelve a no significar nada.
    assert presupuesto is None
