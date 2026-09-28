"""Configuración global de LlamaIndex compartida por todos los chatbots.

Antes cada instancia de ChatBot reconfiguraba Settings.llm/embed_model y creaba
su propio SentenceTransformerRerank (13 copias del mismo modelo). Ahora se
configura UNA vez por proceso (backend en el startup del registry, scheduler
antes de indexar) y los bots reciben el reranker/token counter compartidos —
imprescindible con hot-reload, que re-instancia bots durante la vida del proceso.
"""
import asyncio
import logging
from typing import Optional, Tuple

import tiktoken
from llama_index.core import Settings
from llama_index.core.callbacks import CallbackManager, TokenCountingHandler
from llama_index.core.postprocessor import SentenceTransformerRerank
from google.genai import types
from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
from llama_index.llms.google_genai import GoogleGenAI

from app.config import settings
from AuditorIA import razonamiento

logger = logging.getLogger(__name__)

_token_counter: Optional[TokenCountingHandler] = None
_reranker: Optional[SentenceTransformerRerank] = None
# LLMs adicionales indexados por temperatura. NO hay uno por bot a propósito:
# cada cliente de Gemini tiene su propio pool de conexiones, así que 13 bots serían
# 13 conexiones TLS que hay que mantener calientes o pagan el handshake frío en el
# TTFT. Con una instancia por temperatura DISTINTA son 2-3, y el keepalive las
# pinguea todas (ver _ping_llm).
_llms_por_temp: dict[float, "GoogleGenAI"] = {}

# Configuración de seguridad compartida: los manuales de call center hablan de
# cortes, deudas y emergencias, y los filtros por defecto bloqueaban respuestas
# legítimas.
_SAFETY_SETTINGS = [
    types.SafetySetting(category=categoria, threshold="BLOCK_NONE")  # type: ignore[arg-type]
    for categoria in (
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    )
]


def configure_global_settings() -> Tuple[SentenceTransformerRerank, TokenCountingHandler]:
    """Configura LLM, embeddings, callback manager y reranker. Idempotente."""
    global _token_counter, _reranker
    if _token_counter is not None and _reranker is not None:
        return _reranker, _token_counter

    logger.info("Configuring global LlamaIndex settings...")

    _token_counter = TokenCountingHandler(
        tokenizer=tiktoken.get_encoding("cl100k_base").encode
    )
    Settings.callback_manager = CallbackManager([_token_counter])

    # El batch y los reintentos van en el CONSTRUCTOR, no en Settings: `Settings` no
    # tiene atributo embed_batch_size, así que el `Settings.embed_batch_size = 2` que
    # había acá no configuraba nada (quedaba una propiedad suelta y el modelo seguía
    # con su default de 10 textos por request). Con 100 —el tope de la API— un
    # reindexado hace 10 veces menos llamadas, que es la causa de fondo de los 429
    # RESOURCE_EXHAUSTED; los reintentos con backoff cubren el resto.
    logger.info(f"Using remote Embedding Model: {settings.DEFAULT_REMOTE_EMBED_MODEL}")
    Settings.embed_model = GoogleGenAIEmbedding(
        model_name=settings.DEFAULT_REMOTE_EMBED_MODEL,
        trust_remote_code=True,
        api_key=settings.GEMINI_CHATBOT_API_KEY,
        embed_batch_size=settings.EMBED_BATCH_SIZE,
        retries=settings.EMBED_REINTENTOS,
        retry_min_seconds=settings.EMBED_BACKOFF_MIN_SEG,
        retry_max_seconds=settings.EMBED_BACKOFF_MAX_SEG,
    )

    logger.info(f"Using remote LLM (Gemini): {settings.DEFAULT_REMOTE_LLM_MODEL}")
    Settings.llm = _construir_llm(settings.DEFAULT_LLM_TEMP_REMOTE)
    _llms_por_temp[round(settings.DEFAULT_LLM_TEMP_REMOTE, 2)] = Settings.llm  # type: ignore[assignment]

    _reranker = SentenceTransformerRerank(
        model=settings.DEFAULT_RERANKER_MODEL, top_n=settings.DEFAULT_RERANKER_TOP_N
    )
    logger.info(
        f"Using Reranker: {settings.DEFAULT_RERANKER_MODEL} "
        f"with top_n={settings.DEFAULT_RERANKER_TOP_N}"
    )
    avisar_si_reranker_sin_calibrar()
    return _reranker, _token_counter


def avisar_si_reranker_sin_calibrar() -> bool:
    """Avisa si se cambió el reranker sin recalibrar los umbrales que dependen de él.

    Cada modelo tiene su propia ESCALA y varias decisiones del sistema comparan el
    score contra un número fijo (CHATBOT_VACIO_SCORE_MIN / _EXISTE, el "ruido" de
    eval_rag, los pisos de test_rag_calidad). Los cross-encoder de MS MARCO devuelven
    logits de ~-11 a +10; los BAAI/bge devuelven probabilidades de 0 a 1, así que con
    uno de esos NADA cae por debajo de -5 y la detección de vacíos deja de marcar EN
    SILENCIO — nadie se entera hasta que la pantalla de Calidad aparece vacía.

    Devuelve True si hay desajuste (para poder testearlo sin leer logs).
    """
    if settings.DEFAULT_RERANKER_MODEL == settings.RERANKER_MODELO_CALIBRADO:
        return False
    logger.warning(
        f"El reranker ({settings.DEFAULT_RERANKER_MODEL}) NO es aquel con el que se "
        f"calibraron los umbrales ({settings.RERANKER_MODELO_CALIBRADO}). Cada modelo "
        f"tiene su propia escala de score: revisá CHATBOT_VACIO_SCORE_MIN "
        f"(={settings.CHATBOT_VACIO_SCORE_MIN}) y CHATBOT_VACIO_SCORE_EXISTE "
        f"(={settings.CHATBOT_VACIO_SCORE_EXISTE}) con scripts/bench_reranker.py, "
        f"o la detección de vacíos puede dejar de marcar sin dar error."
    )
    return True


def _construir_llm(temperatura: float) -> GoogleGenAI:
    # OJO: la temperatura va DENTRO de generation_config, no como argumento suelto.
    # GoogleGenAI ignora el parámetro `temperature` cuando se le pasa un
    # generation_config propio (lo necesitamos para las safety settings), así que
    # dejarla afuera la reseteaba en silencio al default de la API y se perdía la
    # temperatura por bot. Cubierto por tests/test_rag_settings_llm.py.
    #
    # Razonamiento MÍNIMO: el bot responde con el contexto RAG ya recuperado delante,
    # o sea que la tarea es redactar a partir de fragmentos, no razonar. Cada token de
    # pensamiento se factura a tarifa de SALIDA y acá el volumen es alto (una consulta
    # por interacción de operador). El modelo por defecto es un "lite", que acepta
    # MINIMAL; si el .env lo apunta a otro que no lo soporta, nivel_para_modelo lo
    # degrada a LOW solo en vez de reventar con un 400 (ver AuditorIA/razonamiento.py).
    return GoogleGenAI(
        model=settings.DEFAULT_REMOTE_LLM_MODEL,
        api_key=settings.GEMINI_CHATBOT_API_KEY,
        generation_config=types.GenerateContentConfig(
            temperature=temperatura,
            safety_settings=_SAFETY_SETTINGS,
            thinking_config=razonamiento.thinking_config(
                razonamiento.NIVEL_RAZONAMIENTO_MECANICO,
                modelo=settings.DEFAULT_REMOTE_LLM_MODEL,
                include_thoughts=False,
            ),
        ),
    )


def get_llm(temperatura: Optional[float] = None) -> GoogleGenAI:
    """LLM para una temperatura dada, compartido entre los bots que usen la misma.

    Un bot de procedimientos quiere temperatura baja (que repita el manual, no que
    lo reformule); uno que redacta cartas al cliente necesita margen para adaptar
    el texto. Como el resto de la config es idéntica, se cachea por temperatura y
    no por bot: así no se multiplican las conexiones a Gemini (ver _llms_por_temp).
    """
    configure_global_settings()
    if temperatura is None:
        temperatura = settings.DEFAULT_LLM_TEMP_REMOTE
    clave = round(float(temperatura), 2)
    if clave not in _llms_por_temp:
        logger.info(f"Creando LLM para temperatura {clave} (modelo {settings.DEFAULT_REMOTE_LLM_MODEL}).")
        _llms_por_temp[clave] = _construir_llm(clave)
    return _llms_por_temp[clave]


def get_reranker() -> SentenceTransformerRerank:
    reranker, _ = configure_global_settings()
    return reranker


def get_token_counter() -> TokenCountingHandler:
    _, token_counter = configure_global_settings()
    return token_counter


async def _ping_embeddings() -> None:
    try:
        await Settings.embed_model.aget_query_embedding("keepalive")
    except Exception:
        # Nunca debe tumbar el worker; en el próximo ciclo se reintenta (y de paso
        # se reabre la conexión, que es justo lo que queremos).
        logger.warning("Keepalive de embeddings falló; se reintenta.", exc_info=True)


async def _ping_llm() -> None:
    # Se pinguean TODAS las instancias vivas (una por temperatura), no solo
    # Settings.llm: cada cliente de Gemini tiene su propio pool de conexiones, así
    # que la conexión que reutiliza un bot con temperatura propia solo se mantiene
    # caliente si se pinguea ESE objeto. Si no, el primer request de ese bot paga el
    # handshake frío (~0.5-0.7s de TTFT). El prompt es mínimo; el path de streaming
    # NO usa el TokenCountingHandler (estima con tiktoken), así que no contamina el
    # conteo de tokens facturado.
    instancias = list(_llms_por_temp.values()) or [Settings.llm]
    for llm in instancias:
        try:
            await llm.acomplete("ok")
        except Exception:
            logger.warning("Keepalive del LLM falló; se reintenta.", exc_info=True)


async def _keepalive_connections(interval_seconds: int) -> None:
    """Mantiene vivas las conexiones HTTP/TLS a los APIs de Gemini (embeddings y LLM).

    Un worker ocioso paga el handshake frío en el primer request: ~2.5s el embedding y
    ~0.5-0.7s el LLM (juntos, el mayor componente del TTFT). Un ping barato de cada uno
    cada `interval_seconds` evita que la conexión caduque, así los requests reales la
    encuentran establecida. Embeddings y LLM usan clientes/pools distintos: hay que
    pingear ambos.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        await _ping_embeddings()
        await _ping_llm()


async def start_rag_warmup(interval_seconds: int) -> Optional["asyncio.Task"]:
    """Calienta las conexiones de embeddings y LLM al arrancar y lanza el keepalive.

    Devuelve la task del keepalive (para cancelarla en el shutdown) o None si está
    desactivado (interval<=0). Idempotencia de Settings garantizada por
    configure_global_settings().
    """
    configure_global_settings()
    try:
        await Settings.embed_model.aget_query_embedding("warmup")
        logger.info("Warmup de embeddings OK: conexión TLS establecida.")
    except Exception:
        logger.warning("Warmup de embeddings falló al arrancar.", exc_info=True)
    try:
        await Settings.llm.acomplete("ok")
        logger.info("Warmup del LLM OK: conexión establecida.")
    except Exception:
        logger.warning("Warmup del LLM falló al arrancar.", exc_info=True)

    if interval_seconds <= 0:
        logger.info("Keepalive de conexiones desactivado (interval<=0).")
        return None
    logger.info(f"Keepalive de conexiones (embeddings + LLM) cada {interval_seconds}s.")
    return asyncio.create_task(_keepalive_connections(interval_seconds))
