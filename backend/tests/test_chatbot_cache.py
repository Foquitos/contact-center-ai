"""
Verifica la puerta de entrada al caché semántico del chatbot RAG (cero tokens).

Una respuesta cacheada se sirve sin historial y sin retrieval, así que solo es válida
para preguntas auto-contenidas. El caso que motivó estos tests: un seguimiento como
"¿cuánto tiempo tarda?" hecho en dos hilos sobre temas distintos tiene similitud 1.0
consigo mismo, así que ningún umbral de score lo distingue; hay que excluirlo del caché.
"""
from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.core.memory import ChatMemoryBuffer

from chatBot import ChatBot

PREGUNTA_AUTOCONTENIDA = "¿Cuánto tiempo tarda la baja del servicio de luz?"
SEGUIMIENTO = "¿Cuánto tiempo tarda?"


def _memoria(*mensajes: str) -> ChatMemoryBuffer:
    memory = ChatMemoryBuffer.from_defaults(token_limit=3000)
    for texto in mensajes:
        memory.put(ChatMessage(role=MessageRole.USER, content=texto))
    return memory


def _es_cacheable(query: str, memory: ChatMemoryBuffer) -> bool:
    # El método no usa self; se invoca sin instanciar el bot (que necesitaría Qdrant).
    return ChatBot._es_consulta_cacheable(None, query, memory)  # pyright: ignore[reportArgumentType]


def test_pregunta_autocontenida_sin_historial_es_cacheable():
    assert _es_cacheable(PREGUNTA_AUTOCONTENIDA, _memoria()) is True


def test_seguimiento_no_es_cacheable_aunque_no_haya_historial():
    """Una pregunta corta depende del hilo aunque sea el primer turno registrado."""
    assert _es_cacheable(SEGUIMIENTO, _memoria()) is False


def test_pregunta_con_historial_previo_no_es_cacheable():
    """Con historial, hasta una pregunta larga se responde en contexto: no se puede cachear."""
    memory = _memoria("¿Cómo doy de baja el servicio de luz?")
    assert _es_cacheable(PREGUNTA_AUTOCONTENIDA, memory) is False
