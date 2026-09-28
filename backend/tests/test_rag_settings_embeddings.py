"""Tests offline de cómo queda configurado el modelo de embeddings.

POR QUÉ IMPORTA: los 429 RESOURCE_EXHAUSTED que hacían fallar los reindexados
salían de acá. Había un `Settings.embed_batch_size = 2` que no configuraba NADA
(`Settings` no tiene ese atributo: quedaba una propiedad suelta y el modelo seguía
con su default de 10 textos por request), así que un bot de 800 nodos disparaba 80
llamadas en ráfaga contra la cuota. Con el tope real de la API —100 por request,
medido el 2026-09-03: con 250 contesta 400 "at most 100 requests can be in one
batch"— son 8, y los reintentos con backoff cubren lo que igual rebote.
"""
from unittest.mock import MagicMock

import pytest
from llama_index.core.embeddings.mock_embed_model import MockEmbedding
from llama_index.core.llms import MockLLM

from app import rag_settings
from app.config import settings


@pytest.fixture
def configuracion_capturada(monkeypatch):
    """Corre configure_global_settings() sin red y devuelve los kwargs con los que
    se construyó el modelo de embeddings."""
    capturado = {}

    def fake_embedding(**kwargs):
        capturado.update(kwargs)
        return MockEmbedding(embed_dim=8)

    monkeypatch.setattr(rag_settings, "GoogleGenAIEmbedding", fake_embedding)
    monkeypatch.setattr(rag_settings, "SentenceTransformerRerank", lambda **kw: MagicMock())
    monkeypatch.setattr(rag_settings, "_construir_llm", lambda temperatura: MockLLM())
    # Los globales se restauran solos al terminar el test (monkeypatch), así que
    # configure_global_settings vuelve a construir todo en vez de salir por el cache.
    monkeypatch.setattr(rag_settings, "_token_counter", None)
    monkeypatch.setattr(rag_settings, "_reranker", None)
    monkeypatch.setattr(rag_settings, "_llms_por_temp", {})

    rag_settings.configure_global_settings()
    return capturado


def test_los_embeddings_van_en_lotes_del_tope_de_la_api(configuracion_capturada):
    assert configuracion_capturada["embed_batch_size"] == settings.EMBED_BATCH_SIZE
    # 100 es el máximo que acepta batchEmbedContents; más es un 400 seguro.
    assert 1 < settings.EMBED_BATCH_SIZE <= 100


def test_los_embeddings_reintentan_con_paciencia_de_minutos(configuracion_capturada):
    """El default de la librería son 3 intentos (~6 segundos): no alcanza para una
    ventana de cuota por minuto, y del otro lado hay un job de fondo que puede esperar."""
    assert configuracion_capturada["retries"] == settings.EMBED_REINTENTOS
    assert configuracion_capturada["retries"] >= 5
    assert configuracion_capturada["retry_max_seconds"] >= 60


def test_setear_el_batch_en_Settings_no_configura_nada():
    """La trampa que hacía fallar los reindexados: `Settings.embed_batch_size` no es
    una opción de Settings —se queda como un atributo suelto— y el modelo sigue con
    el batch con el que se construyó. Por eso ahora va en el constructor."""
    from llama_index.core import Settings

    previo = Settings.embed_model
    try:
        Settings.embed_model = MockEmbedding(embed_dim=8, embed_batch_size=7)
        Settings.embed_batch_size = 2  # exactamente lo que había en rag_settings
        assert Settings.embed_model.embed_batch_size == 7
    finally:
        Settings.embed_model = previo
