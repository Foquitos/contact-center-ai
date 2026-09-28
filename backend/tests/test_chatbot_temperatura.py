"""Temperatura del LLM por chatbot (pagina_web.Chatbots.temperatura).

Lo que importa verificar acá no es que el número llegue —eso es trivial— sino que
NO se cree una instancia de Gemini por bot: cada cliente tiene su propio pool de
conexiones y el keepalive las mantiene calientes una por una, así que multiplicarlas
degradaría el TTFT, que es el punto sensible del chatbot.
"""
from unittest.mock import MagicMock, patch

import pytest

from app import rag_settings
from app.chatbot_config import ChatbotConfig


@pytest.fixture(autouse=True)
def _pool_limpio():
    """El pool es global al proceso: se aísla para no arrastrar estado entre tests."""
    original = dict(rag_settings._llms_por_temp)
    rag_settings._llms_por_temp.clear()
    yield
    rag_settings._llms_por_temp.clear()
    rag_settings._llms_por_temp.update(original)


@pytest.fixture
def construir():
    """Evita instanciar Gemini de verdad (y de paso permite contar llamadas)."""
    with patch.object(rag_settings, "configure_global_settings", return_value=(None, None)), \
         patch.object(rag_settings, "_construir_llm", side_effect=lambda t: MagicMock(temp=t)) as m:
        yield m


def test_una_instancia_por_temperatura_no_por_bot(construir):
    """Ocho bots con la misma temperatura comparten un solo cliente de Gemini."""
    for _ in range(8):
        rag_settings.get_llm(0.15)
    assert construir.call_count == 1
    assert len(rag_settings._llms_por_temp) == 1


def test_temperaturas_distintas_dan_instancias_distintas(construir):
    bajo = rag_settings.get_llm(0.15)
    alto = rag_settings.get_llm(0.65)
    assert bajo is not alto
    assert construir.call_count == 2


def test_sin_temperatura_cae_al_default(construir):
    from app.config import settings

    rag_settings.get_llm(None)
    construir.assert_called_once_with(round(settings.DEFAULT_LLM_TEMP_REMOTE, 2))


def test_la_clave_se_redondea(construir):
    """0.150000001 y 0.15 no pueden abrir dos conexiones distintas."""
    rag_settings.get_llm(0.15)
    rag_settings.get_llm(0.1500000001)
    assert construir.call_count == 1


def test_keepalive_pinguea_todas_las_instancias(construir):
    """Si el keepalive solo tocara Settings.llm, el bot con temperatura propia
    pagaría el handshake frío (~0.5-0.7s de TTFT) en su primera consulta."""
    import asyncio

    llms = [rag_settings.get_llm(0.15), rag_settings.get_llm(0.65)]
    for llm in llms:
        llm.acomplete = MagicMock(return_value=asyncio.sleep(0))

    asyncio.run(rag_settings._ping_llm())

    for llm in llms:
        assert llm.acomplete.called


# ------------------------------------------------------------------ config

def _row(**extra):
    base = {
        "id": 1, "slug": "voltara", "nombre": "Voltara", "descripcion": None,
        "system_prompt": "sos un asistente", "grupo": None,
        "permission_code": "chatbot:voltara", "activo": True, "index_version": 4,
        "index_status": "ready", "last_indexed_at": None, "updated_at": None,
    }
    base.update(extra)
    return base


def test_config_lee_la_temperatura():
    cfg = ChatbotConfig.from_row(_row(temperatura=0.65))
    assert cfg.temperatura == 0.65


def test_config_sin_la_columna_no_rompe():
    """El indexador y los scripts hacen SELECTs propios que no la traen; tienen que
    seguir construyendo la config sin fallar."""
    cfg = ChatbotConfig.from_row(_row())
    assert cfg.temperatura is None


def test_config_con_temperatura_nula_cae_al_default():
    cfg = ChatbotConfig.from_row(_row(temperatura=None))
    assert cfg.temperatura is None


# ------------------------------------------- guarda de calibración del reranker

def test_avisa_si_el_reranker_no_es_el_calibrado(monkeypatch, caplog):
    """Cambiar de reranker cambia la ESCALA del score, y varias decisiones comparan
    contra números fijos. Con un modelo tipo bge (0..1) nada cae bajo -5 y la
    detección de vacíos deja de marcar en silencio: nadie se entera hasta que la
    pantalla de Calidad aparece vacía."""
    import logging

    from app.config import settings
    from app.rag_settings import avisar_si_reranker_sin_calibrar

    monkeypatch.setattr(settings, "DEFAULT_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
    monkeypatch.setattr(settings, "RERANKER_MODELO_CALIBRADO", "cross-encoder/ms-marco-MiniLM-L12-v2")

    with caplog.at_level(logging.WARNING):
        assert avisar_si_reranker_sin_calibrar() is True

    aviso = caplog.text
    assert "bge-reranker-v2-m3" in aviso
    assert "CHATBOT_VACIO_SCORE_MIN" in aviso      # dice QUÉ recalibrar
    assert "bench_reranker" in aviso               # y con qué


def test_no_avisa_cuando_coincide(monkeypatch):
    from app.config import settings
    from app.rag_settings import avisar_si_reranker_sin_calibrar

    monkeypatch.setattr(settings, "DEFAULT_RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L12-v2")
    monkeypatch.setattr(settings, "RERANKER_MODELO_CALIBRADO", "cross-encoder/ms-marco-MiniLM-L12-v2")

    assert avisar_si_reranker_sin_calibrar() is False
