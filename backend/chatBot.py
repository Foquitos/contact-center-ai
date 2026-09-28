import os
import logging
import asyncio
import tiktoken
import sqlalchemy
import Stemmer

from sqlalchemy import bindparam, text
from sqlalchemy.types import NVARCHAR
from datetime import datetime, timedelta
from typing import Dict, List, Optional, TypedDict

from app.config import settings
from app.chatbot_config import ChatbotConfig
from app import chatbot_adjuntos
from app import chatbot_desambiguacion
from app import chatbot_tablas
from app.semantic_cache import SemanticCache
from app.rag_settings import get_llm, get_reranker, get_token_counter
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.core.schema import MetadataMode
from llama_index.core.chat_engine import CondensePlusContextChatEngine
from llama_index.core.prompts import PromptTemplate
from llama_index.core.base.llms.generic_utils import messages_to_history_str
from llama_index.core.llms import ChatMessage, MessageRole, TextBlock
from llama_index.core.retrievers import BaseRetriever, QueryFusionRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.async_qdrant_client import AsyncQdrantClient

from llama_index.core import VectorStoreIndex, StorageContext, Settings, ChatPromptTemplate, load_index_from_storage


# --- Configuration Constants ---

# Logging
LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'

logger = logging.getLogger(__name__)


# --- Type Definitions ---
class QueryResponse(TypedDict):
    """Structured response for the query method."""
    response: str
    context: str
    source_nodes: List[Dict] # Store simplified source node info
    input_tokens: Optional[int]
    output_tokens: Optional[int]

class DocumentInfo(TypedDict):
    """Information about a document in the index."""
    filename: str
    doc_id: str


# BM25 en español: el stemmer reduce las palabras a su raíz (facturación/facturas -> factur)
# y `BM25_LANGUAGE` elige la lista de stopwords al indexar. Se crea una sola vez porque
# se reutiliza en cada chatbot.
BM25_LANGUAGE = "spanish"
BM25_STEMMER = Stemmer.Stemmer(BM25_LANGUAGE)


# INSERT explícito del log de consultas, en reemplazo de pandas.to_sql.
#
# to_sql PERDÍA LOS EMOJI: con dtype `object` bindea el parámetro como VARCHAR, así
# que el driver convertía el texto a su code page ANTES de llegar a la tabla y los
# emoji del system prompt (🖥️ / 🗣️) se guardaban como '?'. Migrar las columnas a
# NVARCHAR no alcanzó, porque el daño ocurre en el bind, no en la columna. Eso
# además degradaba el historial: _get_memory_for_user relee `response` y le devolvía
# al bot su propia respuesta rota.
#
# Con bindparam(type_=NVARCHAR) el parámetro viaja como Unicode. De paso se evita
# construir un DataFrame para insertar una sola fila. `fecha` y `active` los pone la
# BD (DEFAULT getdate() / 1).
def _num(valor, tipo):
    """Convierte a un numérico NATIVO de Python, o None.

    pyodbc no sabe bindear los tipos de numpy ("Invalid parameter type.
    param-type=numpy.float32") y los scores del reranker son numpy.float32.
    pandas.to_sql hacía esta conversión sola; con el INSERT explícito hay que
    hacerla a mano. Se aplica a TODOS los numéricos y no solo al score porque el
    log no puede caerse por el tipo de un valor que además es accesorio.
    """
    if valor is None:
        return None
    try:
        return tipo(valor)
    except (TypeError, ValueError):
        return None


_INSERT_LOG = text("""
    INSERT INTO [Acme].[pagina_web].[query_chatbots_logs]
        (user_id, effective_campana, [query], query_condensada, response, context, task_id,
         input_tokens, output_tokens, embedding_tokens, score_max, sin_cobertura)
    VALUES
        (:user_id, :effective_campana, :query, :query_condensada, :response, :context, :task_id,
         :input_tokens, :output_tokens, :embedding_tokens, :score_max, :sin_cobertura)
""").bindparams(
    bindparam("effective_campana", type_=NVARCHAR),
    bindparam("query", type_=NVARCHAR),
    bindparam("query_condensada", type_=NVARCHAR),
    bindparam("response", type_=NVARCHAR),
    bindparam("context", type_=NVARCHAR),
)


def consulta_condensada(respuesta, query_text: str) -> Optional[str]:
    """La consulta REESCRITA con la que se buscó en el índice, o None.

    CondensePlusContextChatEngine reescribe la pregunta junto con el historial para
    dejarla auto-contenida, y busca con ESA. Cuando el operador escribe una
    repregunta ("tiene costo adicional?"), lo reescrito es lo único que explica
    contra qué se buscó — sin esto, revisar el log de solicitudes no alcanza para
    entender por qué el resultado no tenía nada que ver.

    El motor la deja en sources[0].raw_input['message'] (ver _arun_c3). Devuelve
    None cuando coincide con lo que se tipeó: en el primer mensaje no hay historial
    que condensar y guardarla sería repetir la columna de al lado.
    """
    for fuente in (getattr(respuesta, "sources", None) or []):
        entrada = getattr(fuente, "raw_input", None)
        if not isinstance(entrada, dict):
            continue
        candidato = (entrada.get("message") or "").strip()
        if candidato and candidato != (query_text or "").strip():
            return candidato[:2000]
    return None


class PrecomputedQueryEmbeddingRetriever(BaseRetriever):
    """
    Envuelve un retriever e inyecta el embedding ya calculado de la consulta original,
    para que el retriever vectorial no vuelva a llamar a la API de embeddings.
    """

    def __init__(self, retriever: BaseRetriever, query_text: str, query_embedding: List[float]):
        super().__init__()
        self._retriever = retriever
        self._query_text = query_text
        self._query_embedding = query_embedding

    def _inject(self, query_bundle: QueryBundle) -> QueryBundle:
        if query_bundle.embedding is None and query_bundle.query_str == self._query_text:
            query_bundle.embedding = self._query_embedding
        return query_bundle

    def _retrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        return self._retriever.retrieve(self._inject(query_bundle))

    async def _aretrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        return await self._retriever.aretrieve(self._inject(query_bundle))


class ChatBot:
    """
    Chatbot RAG genérico (LlamaIndex + Qdrant) construido desde una fila de la
    tabla pagina_web.Chatbots (ChatbotConfig). Antes existía una subclase por
    marca (ChatVoltara, ChatCSV, ...) que solo variaba rutas y system prompt;
    ahora eso vive en la BD.

    Las instancias son SIEMPRE de solo lectura: leen el índice construido por el
    indexador (app/chatbot_indexer.py) — docstore/index_store en
    config.persist_dir(index_version) y vectores en Qdrant vía el alias
    config.collection_alias. Si el índice no existe o está inconsistente, el
    constructor lanza RuntimeError y el registry marca el bot como no disponible
    (nunca se debe instanciar un bot con index_version=0: QdrantVectorStore
    crearía una colección real con el nombre del alias y rompería el swap).
    """

    DEFAULT_SYSTEM_PROMPT = (
        "Eres un asistente experto que responde preguntas basándose únicamente en la documentación proporcionada. "
        "Tu objetivo es proporcionar respuestas precisas, concisas y útiles, basadas exclusivamente en la información disponible en el contexto. "
        "No uses tu conocimiento previo ni menciones las fuentes de información en tus respuestas. "
        "Si la información proporcionada es insuficiente para responder completamente, indica solo lo que puedas extraer del contexto sin especular. "
        "Presenta tus respuestas de manera natural y conversacional, como si la información formara parte de tu conocimiento."
    )
    DEFAULT_QA_PROMPT_STR = (
        "Información relevante de la documentación:\n"
        "---------------------\n"
        "{context_str}\n"
        "---------------------\n"
        "Responde a la siguiente pregunta basándote principalmente en la información proporcionada arriba.\n"
        "Sé preciso y conciso. Si la información no es suficiente para responder completamente, menciona solo lo que puedas extraer de la documentación.\n"
        "No indiques al usuario que estás utilizando documentación externa.\n\n"
        "Pregunta: {query_str}\n"
        "Respuesta:"
    )
    DEFAULT_REFINE_PROMPT_STR = (
        "Has proporcionado una respuesta inicial. Ahora tienes acceso a información adicional que podría ser relevante:\n"
        "------------\n"
        "{context_msg}\n"
        "------------\n"
        "Evalúa si esta nueva información mejora tu respuesta anterior a la pregunta: {query_str}\n"
        "- Si la nueva información es relevante, incorpora estos detalles en tu respuesta original para hacerla más precisa o completa.\n"
        "- Si la nueva información no aporta valor o contradice la respuesta original, mantén la respuesta original.\n"
        "- No menciones el proceso de refinamiento ni las fuentes de información en tu respuesta.\n\n"
        "Respuesta anterior: {existing_answer}\n"
        "Respuesta refinada:"
    )

    # Reescribe un seguimiento ("¿cuánto tarda?") como pregunta autónoma usando el
    # historial, ANTES del retrieval: sin esto el embedding de la pregunta cruda no
    # tiene contexto y recupera cualquier cosa. En español para no cambiar el idioma.
    DEFAULT_CONDENSE_PROMPT_STR = (
        "Dada la siguiente conversación entre un usuario y un asistente, y una pregunta "
        "de seguimiento del usuario, reformulá la pregunta de seguimiento para que sea una "
        "pregunta autónoma y completa, en español, preservando toda referencia implícita al "
        "tema que se venía hablando. Si la pregunta ya es autónoma, devolvela igual.\n\n"
        "Historial de la conversación:\n{chat_history}\n"
        "Pregunta de seguimiento: {question}\n"
        "Pregunta autónoma:"
    )
    # Envuelve los documentos recuperados. El system prompt del bot (BD) manda el tono
    # y el idioma; acá solo se inyecta el contexto y se refuerza el anclaje a las fuentes.
    DEFAULT_CONTEXT_PROMPT_STR = (
        "A continuación se listan los documentos relevantes para responder:\n\n"
        "{context_str}\n\n"
        "Instrucción: respondé la pregunta del usuario basándote únicamente en los documentos "
        "de arriba. Si la información no alcanza para responder, decilo con claridad en lugar "
        "de inventar."
    )

    def __init__(
        self,
        config: ChatbotConfig,
        qdrant_client: QdrantClient,
        qdrant_aclient: AsyncQdrantClient,
        sql_engine: Optional[sqlalchemy.engine.base.Engine] = None,
    ):
        logger.info(f"Initializing ChatBot '{config.slug}' (index v{config.index_version})...")
        self.config = config
        self.slug = config.slug
        self.sql_engine = sql_engine
        self.collection_name = config.collection_alias

        os.makedirs(config.log_dir, exist_ok=True)
        self._setup_logging(config.log_dir)

        # Recursos globales compartidos entre bots (configurados una vez por proceso).
        self.reranker = get_reranker()
        self.token_counter = get_token_counter()
        # Temperatura propia del bot (columna Chatbots.temperatura). Los bots de
        # procedimientos van bajos para no reformular el manual; el de cartas, más
        # alto para poder adaptar la redacción al caso.
        self.llm = get_llm(config.temperatura)

        self._initialize_vector_store(qdrant_client, qdrant_aclient)
        self._initialize_index()
        self._setup_prompts(config.system_prompt)
        self._setup_query_engine()
        self._setup_bm25_retriever()
        self.cache = SemanticCache(
            client=qdrant_client,
            slug=config.slug,
            min_score=settings.CHATBOT_CACHE_MIN_SCORE,
        )
        logger.info(f"ChatBot '{config.slug}' initialization complete.")

    def _setup_bm25_retriever(self):
        """
        Construye el retriever BM25 una sola vez al iniciar (construirlo por consulta
        costaba ~1s por request). Si falla, las consultas usan solo retrieval vectorial.
        """
        self.bm25_retriever = None
        try:
            if not self.index.docstore or len(self.index.docstore.docs) == 0:
                raise ValueError("Docstore vacío")

            self.bm25_retriever = BM25Retriever.from_defaults(
                docstore=self.index.docstore,
                similarity_top_k=settings.CHATBOT_RETRIEVAL_TOP_K,
                stemmer=BM25_STEMMER,
                language=BM25_LANGUAGE,
            )
            logger.info("BM25 Retriever precomputado y listo para reutilizar.")
        except Exception as e:
            logger.warning(f"⚠️ Falló inicialización BM25 ({e}). Se usará SOLO retrieval vectorial.")

    async def _get_memory_for_user_async(self, user_id: int):
        loop = asyncio.get_running_loop()
        # Ejecuta la función sincrónica en un thread pool para no congelar la API
        return await loop.run_in_executor(None, self._get_memory_for_user, user_id)

    def _filtrar_sesion_actual(self, rows: list) -> list:
        """Recorta las filas del historial (ordenadas por fecha DESC) a la 'sesión'
        de conversación actual.

        Un operador de call center atiende un llamado, y al siguiente vuelve a
        consultar el bot: sin esto, la condensación arrastra el contexto de la
        gestión anterior y el bot responde sobre el llamado equivocado. Una nueva
        gestión se detecta por un hueco temporal mayor a CHATBOT_SESSION_GAP_MINUTES
        entre interacciones consecutivas:

        - Si la última interacción registrada ya quedó más vieja que el umbral
          respecto de AHORA, la consulta actual abre una sesión nueva -> memoria
          vacía (no arrastra nada).
        - Si no, se conservan las interacciones hacia atrás hasta el primer hueco
          que supere el umbral (todo lo anterior es de otra gestión).

        Los tiempos salen del reloj del SQL Server (columna now_sql = SYSDATETIME())
        para no depender del reloj del app server, que puede diferir del de la BD.
        """
        if not rows:
            return []
        # 0 (o negativo) desactiva la ventana: todo el historial reciente es una sola sesión.
        if settings.CHATBOT_SESSION_GAP_MINUTES <= 0:
            return list(rows)

        gap = timedelta(minutes=settings.CHATBOT_SESSION_GAP_MINUTES)
        now_sql = rows[0].now_sql
        newest = rows[0].fecha

        if now_sql is not None and newest is not None and (now_sql - newest) > gap:
            logger.info("Nueva sesión: la última consulta quedó fuera de la ventana; memoria limpia.")
            return []

        session = [rows[0]]
        prev = newest
        for row in rows[1:]:
            if prev is None or row.fecha is None or (prev - row.fecha) > gap:
                break
            session.append(row)
            prev = row.fecha
        return session

    def _get_memory_for_user(self, user_id: int) -> ChatMemoryBuffer:
        """
        Reconstruye el historial del usuario desde la base de datos SQL.
        Esto permite que funcione con múltiples workers de Gunicorn.

        Se filtra por bot (effective_campana): un usuario que consulta varios bots tenía
        el historial de uno entrando como contexto del otro. Además se recorta a la
        sesión de conversación actual (ver _filtrar_sesion_actual): un llamado nuevo
        arranca con memoria limpia sin que el operador tenga que borrar el chat.
        """
        # Límite de mensajes a recuperar para no saturar el contexto (ej. últimos 5 pares = 10 mensajes)
        history_limit = 5

        # Inicializar buffer vacío
        memory = ChatMemoryBuffer.from_defaults(token_limit=3000)

        if not self.sql_engine:
            logger.warning("SQL Engine no disponible. Usando memoria volátil vacía.")
            return memory

        try:
            # now_sql (mismo valor en todas las filas) da el "ahora" del reloj del SQL
            # para medir los huecos entre consultas sin depender del reloj del app server.
            query = text("""
                SELECT TOP (:limit) query, response, fecha, SYSDATETIME() AS now_sql
                FROM [Acme].[pagina_web].[query_chatbots_logs]
                WHERE user_id = :uid and active = 1 and effective_campana = :slug
                ORDER BY fecha DESC
            """)

            with self.sql_engine.connect() as conn:
                result = conn.execute(
                    query, {"limit": history_limit, "uid": user_id, "slug": self.slug}
                ).fetchall()

            session_rows = self._filtrar_sesion_actual(result)

            # Las filas vienen del más reciente al más antiguo, así que las invertimos
            for row in reversed(session_rows):
                user_msg = row.query
                bot_msg = row.response

                if user_msg:
                    memory.put(ChatMessage(role=MessageRole.USER, content=str(user_msg)))
                if bot_msg:
                    memory.put(ChatMessage(role=MessageRole.ASSISTANT, content=str(bot_msg)))

            logger.info(
                f"Historial reconstruido para usuario {user_id}: {len(session_rows)} de "
                f"{len(result)} interacciones recientes (recorte por ventana de sesión)."
            )

        except Exception as e:
            logger.error(f"Error recuperando historial de SQL: {e}")
            # Devolvemos memoria vacía en caso de error para no romper el flujo

        return memory

    def _es_consulta_cacheable(self, query_text: str, user_memory: ChatMemoryBuffer,
                               adjuntos: Optional[List] = None) -> bool:
        """
        Un hit de caché se sirve sin historial y sin retrieval, así que solo es correcto
        para preguntas auto-contenidas: la respuesta guardada se generó con el historial
        de OTRA conversación y la clave es únicamente el embedding de la pregunta.

        Un seguimiento ("¿cuánto tiempo tarda?") escrito en dos hilos sobre temas distintos
        da similitud 1.0 contra sí mismo: ningún umbral lo distingue, por eso se excluye
        del caché en vez de intentar filtrarlo por score.
        """
        # Un turno con adjunto tampoco: la clave es el embedding del TEXTO, así que
        # "¿qué dice esta factura?" con la factura de un cliente le devolvería esa misma
        # respuesta al siguiente operador que escriba lo mismo con OTRO archivo. Además
        # de estar mal, filtraría datos de un tercero.
        if adjuntos:
            logger.info("Caché omitido: la consulta trae adjuntos.")
            return False

        if user_memory.get_all():
            logger.info("Caché omitido: la consulta es un seguimiento (hay historial previo).")
            return False

        if len(query_text.split()) < settings.CHATBOT_CACHE_MIN_PALABRAS:
            logger.info("Caché omitido: consulta demasiado corta para ser auto-contenida.")
            return False

        return True

    def _setup_logging(self, log_dir: str):
        """Sets up the directory for query-specific logs."""
        self.query_log_dir = os.path.join(log_dir, "queries")
        try:
            os.makedirs(self.query_log_dir, exist_ok=True)
            logger.info(f"Query logs will be saved in: {self.query_log_dir}")
        except OSError as e:
            logger.error(f"Failed to create query log directory '{self.query_log_dir}': {e}")
            # Decide if this is fatal or not. For now, we'll log and continue.
            self.query_log_dir = None # Disable query logging if dir creation fails

    def _initialize_vector_store(self, qdrant_client: QdrantClient, qdrant_aclient: AsyncQdrantClient):
        """Inicializa el vector store de Qdrant apuntando al ALIAS del bot.

        El aclient es obligatorio: stream_query hace retrieval async
        (QueryFusionRetriever con use_async=True).
        """
        logger.info(f"Initializing Qdrant vector store (alias: {self.config.collection_alias})")
        try:
            self.vector_store = QdrantVectorStore(
                collection_name=self.config.collection_alias,
                client=qdrant_client,
                aclient=qdrant_aclient,
            )
            self.storage_context = StorageContext.from_defaults(vector_store=self.vector_store)
            logger.info("Vector store initialized successfully.")
        except Exception as e:
            logger.exception(f"Unexpected error initializing vector store: {e}")
            raise RuntimeError(f"Failed to initialize vector store: {e}") from e

    def _initialize_index(self):
        """Carga el índice (docstore/index_store) de la versión vigente desde disco.

        A diferencia del modelo Chroma (que reconstruía si faltaba), acá la
        construcción es trabajo exclusivo del indexador: si el índice no está o
        es inconsistente se lanza RuntimeError y el registry deja el bot como no
        disponible en lugar de servir un bot vacío.
        """
        persist_dir = self.config.persist_dir(self.config.index_version)
        docstore_path = os.path.join(persist_dir, "docstore.json")
        index_store_path = os.path.join(persist_dir, "index_store.json")

        if not (os.path.exists(docstore_path) and os.path.exists(index_store_path)):
            raise RuntimeError(
                f"No hay índice persistido para '{self.slug}' en {persist_dir} "
                f"(index_version={self.config.index_version})."
            )

        logger.info(f"Loading index for '{self.slug}' from: {persist_dir}")
        reconstructed_storage_context = StorageContext.from_defaults(
            vector_store=self.vector_store,
            persist_dir=persist_dir
        )
        self.index = load_index_from_storage(
            storage_context=reconstructed_storage_context,
            show_progress=True,
        )
        self.storage_context = reconstructed_storage_context

        # Si cargó pero el docstore está vacío, BM25 y el retrieval fallarán.
        if not self.index.docstore or len(self.index.docstore.docs) == 0:
            raise RuntimeError(
                f"Índice de '{self.slug}' cargó con docstore vacío (inconsistente). "
                "Reindexar el bot."
            )
        logger.info(f"Successfully loaded index with {len(self.index.docstore.docs)} documents.")

    def _setup_prompts(
        self,
        system_prompt: Optional[str] = None,
        qa_prompt_str: Optional[str] = None,
        refine_prompt_str: Optional[str] = None,
    ):
        """Sets up the chat prompt templates using provided or default strings."""
        logger.info("Setting up chat prompt templates...")

        _system = system_prompt or self.DEFAULT_SYSTEM_PROMPT
        self.system_prompt = _system
        logger.debug(f"System Prompt configured: {_system}")

        _qa_str = qa_prompt_str or self.DEFAULT_QA_PROMPT_STR
        _refine_str = refine_prompt_str or self.DEFAULT_REFINE_PROMPT_STR

        # Text QA Prompt
        chat_text_qa_msgs = [("system", _system), ("user", _qa_str)]
        self.text_qa_template = ChatPromptTemplate.from_messages(chat_text_qa_msgs)
        logger.debug(f"QA Template configured: {chat_text_qa_msgs}")

        # Refine Prompt
        chat_refine_msgs = [("system", _system), ("user", _refine_str)]
        self.refine_template = ChatPromptTemplate.from_messages(chat_refine_msgs)
        logger.debug(f"Refine Template configured: {chat_refine_msgs}")

    def _setup_query_engine(self):
        """Creates the query engine from the initialized index."""
        if not hasattr(self, 'index') or self.index is None:
             logger.error("Cannot setup query engine: Index is not initialized.")
             raise RuntimeError("Index must be initialized before setting up the query engine.")
        if not hasattr(self, 'text_qa_template') or not hasattr(self, 'refine_template'):
             logger.error("Cannot setup query engine: Prompt templates are not set up.")
             raise RuntimeError("Prompts must be set up before the query engine.")
        if not hasattr(self, 'reranker'):
             logger.error("Cannot setup query engine: Reranker is not configured.")
             # Handle this - maybe proceed without reranker? For now, raise error.
             raise RuntimeError("Reranker must be configured before the query engine.")


        logger.info("Setting up query engine...")
        try:
            self.query_engine = self.index.as_query_engine(
                text_qa_template=self.text_qa_template,
                refine_template=self.refine_template,
                # LLM is usually taken from Settings, but can be specified: llm=self.llm,
                response_mode='compact', # Or other modes like 'refine'
                similarity_top_k=10, # Number of nodes to retrieve initially
                node_postprocessors=[self.reranker], # Apply reranking after retrieval
                streaming=False, # Default to False for the standard query engine
            )
            logger.info("Query engine setup complete.")
        except Exception as e:
            logger.exception(f"Failed to set up query engine: {e}")
            raise RuntimeError(f"Query engine setup failed: {e}") from e
    def _log_query_details(self, query: str, response: str, context: str, user_id: int, effective_campana: str, task_id: str, input_tokens: Optional[int] = None, output_tokens: Optional[int] = None, embedding_tokens: Optional[int] = None, score_max: Optional[float] = None, query_condensada: Optional[str] = None, con_adjuntos: bool = False, respuesta_modelo: Optional[str] = None):
        """Logs query, response, and context to separate files."""

        # Señal barata de cobertura: ¿el bot pudo responder con la documentación?
        # Se calcula acá (cero tokens, cero latencia) y el scheduler después analiza
        # solo los candidatos. Ver app/vacios_conocimiento.py. `con_adjuntos` evita
        # que una consulta respondida desde una captura se marque como hueco de
        # documentación (el score del reranker es bajo por diseño en ese caso).
        #
        # `respuesta_modelo` llega solo cuando lo que se guardó NO es lo que el modelo
        # escribió: la negativa que se cambió por la lista de temas. La cobertura se
        # juzga sobre ESA, porque el modelo se negó igual y el hueco puede ser real.
        # Sin esto el reemplazo borraba la señal: de las negativas históricas con
        # score >= 3,0 que el clasificador diferido alcanzó a leer, 60 de 100 eran
        # huecos de documentación de verdad (lo decide leyendo el contexto servido,
        # que es mejor criterio que el score). El que decide sigue siendo el
        # clasificador; acá solo se lo deja llegar.
        from app.vacios_conocimiento import evaluar_cobertura

        sin_cobertura = evaluar_cobertura(respuesta_modelo or response, score_max, con_adjuntos)

        diccionario = {
            "user_id": _num(user_id, int),
            "effective_campana":effective_campana,
            "query":query,
            "query_condensada": query_condensada,
            "response":response,
            "context":context,
            'task_id':task_id,
            'input_tokens': _num(input_tokens, int),
            'output_tokens': _num(output_tokens, int),
            'embedding_tokens': _num(embedding_tokens, int),
            'score_max': _num(score_max, float),
            'sin_cobertura': 1 if sin_cobertura else 0,
        }

        if self.sql_engine:
            try:
                with self.sql_engine.begin() as conn:
                    conn.execute(_INSERT_LOG, diccionario)
            except Exception:
                # El log nunca puede tumbar una respuesta ya entregada al operador.
                logger.exception("No se pudo guardar el log de la consulta en SQL.")

        # Libro centralizado de consumo de IA (pagina_web.IA_Uso). effective_campana es
        # un texto (no un campana_id) => va en extras. Best-effort, nunca rompe el chat.
        try:
            from app.uso_ia import registrar_uso_ia
            registrar_uso_ia(
                feature="chatbot",
                modelo=settings.DEFAULT_REMOTE_LLM_MODEL,
                modo="sync",
                user_id=user_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                embedding_tokens=embedding_tokens,
                ref_id=f"chatbot:{task_id}" if task_id else None,
                extras={"effective_campana": effective_campana} if effective_campana else None,
            )
        except Exception:
            logger.exception("No se pudo registrar el consumo de IA del chatbot.")


        if not self.query_log_dir:
            logger.warning("Query logging disabled because log directory setup failed.")
            return

        try:
            timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S-%f')
            base_filename = os.path.join(self.query_log_dir, f'{timestamp}')

            # Save query
            with open(f'{base_filename}_query.txt', mode='w', encoding='utf-8') as f:
                f.write(query)
            # Save response
            with open(f'{base_filename}_response.txt', mode='w', encoding='utf-8') as f:
                f.write(response)
            # Save context
            with open(f'{base_filename}_context.txt', mode='w', encoding='utf-8') as f:
                f.write(context)
        except Exception as e:
            logger.error(f"Failed to log query details for timestamp {timestamp}: {e}")


    def query(self, query_text: str, user_id, effective_campana, task_id) -> QueryResponse:
        """
        Performs a query against the indexed documents.

        Args:
            query_text: The user's question.

        Returns:
            A dictionary containing the response, context, and source node details.
        """
        if not hasattr(self, 'query_engine') or self.query_engine is None:
             logger.error("Query engine is not available.")
             # Return an error structure or raise exception
             return QueryResponse(response="Error: Query engine not initialized.", context="", source_nodes=[], input_tokens=0, output_tokens=0)

        logger.info(f"Received query: '{query_text[:100]}...'") # Log truncated query
        try:
            self.token_counter.reset_counts()
            response_obj = self.query_engine.query(query_text)
            response_text = str(response_obj)
            logger.info(f"Generated response: '{response_text[:100]}...'")

            input_tokens = self.token_counter.prompt_llm_token_count or 0
            output_tokens = self.token_counter.completion_llm_token_count or 0
            embedding_tokens = self.token_counter.total_embedding_token_count
            # Extract context/source nodes
            source_nodes_data = []
            context_str = "Context not available."
            if hasattr(response_obj, 'source_nodes') and response_obj.source_nodes:
                context_parts = []
                for node in response_obj.source_nodes:
                    filename = node.metadata.get('file_name', 'Unknown Document')
                    node_text = node.get_content(metadata_mode=MetadataMode.NONE)
                    score = node.get_score()
                    context_parts.append(f"Document: {os.path.basename(filename)}, Score: {score:.4f}\nText: {node_text}...") # Truncate text
                    source_nodes_data.append({
                        "filename": os.path.basename(filename),
                        "doc_id": node.node_id,
                        "score": score,
                        "text_preview": node_text[:200] + "..."
                    })
                context_str = "\n\n".join(context_parts)
            else:
                logger.warning("No source nodes found in the response object.")

            # Log details
            from app.vacios_conocimiento import score_maximo

            self._log_query_details(
                query_text, response_text, context_str, user_id, effective_campana,
                task_id, input_tokens, output_tokens, embedding_tokens,
                score_max=score_maximo(getattr(response_obj, "source_nodes", None)),
                query_condensada=consulta_condensada(response_obj, query_text),
            )

            return QueryResponse(
                response=response_text,
                context=context_str,
                source_nodes=source_nodes_data,
                input_tokens=input_tokens,
                output_tokens=output_tokens
            )

        except Exception as e:
            logger.exception(f"Error during query execution: {e}")
            # Return an error structure
            return QueryResponse(response=f"Error during query: {e}", context="", source_nodes=[], input_tokens=0, output_tokens=0)

    # --- Camino manual (adjuntos y desambiguación) -------------------------------
    #
    # Los tres helpers de abajo reproducen a mano lo que CondensePlusContextChatEngine
    # hace internamente (condensar -> recuperar -> armar los mensajes). Se usan cuando
    # el motor no alcanza:
    #
    #   - con adjuntos: astream_chat() recibe un str, así que no hay por dónde meterle
    #     una imagen;
    #   - con desambiguación: hay que VER lo recuperado antes de generar, para decidir
    #     si conviene ofrecerle temas al operador en lugar de una respuesta.
    #
    # En los dos casos se usan los mismos prompts que el motor a propósito: si divergen,
    # el bot contesta distinto según el camino y eso es imposible de explicar desde
    # afuera.

    async def _condensar_pregunta(self, query_text: str, user_memory: ChatMemoryBuffer) -> str:
        """Reescribe la consulta como pregunta autónoma usando el historial.

        Igual que el motor: sin historial no condensa (el primer turno no paga la
        reescritura). Si la reescritura falla, se sigue con la pregunta original en
        lugar de tumbar la consulta: peor recuperación es mejor que ninguna respuesta.
        """
        historial = user_memory.get_all()
        if not historial:
            return query_text
        try:
            prompt = self.DEFAULT_CONDENSE_PROMPT_STR.format(
                chat_history=messages_to_history_str(historial), question=query_text
            )
            return str(await self.llm.acomplete(prompt)).strip() or query_text
        except Exception:
            logger.exception("Falló la condensación de la pregunta con adjuntos; se usa la original.")
            return query_text

    async def _recuperar_nodos(self, retriever: BaseRetriever, pregunta: str) -> List[NodeWithScore]:
        """Recupera y rerankea, igual que los node_postprocessors del motor."""
        nodos = await retriever.aretrieve(pregunta)
        return await self.reranker.apostprocess_nodes(nodos, query_bundle=QueryBundle(pregunta))

    def _armar_mensajes(
        self,
        query_text: str,
        nodos: List[NodeWithScore],
        user_memory: ChatMemoryBuffer,
        adjuntos: Optional[List["chatbot_adjuntos.Adjunto"]] = None,
    ) -> List[ChatMessage]:
        """Arma la conversación que se le manda al modelo, con los archivos adentro
        si los hay.

        Réplica del layout de LlamaIndex (chat_engine/utils.get_prefix_messages_with_context):
        un mensaje de sistema con el contexto recuperado + el system prompt del bot, el
        historial, y recién ahí el mensaje del usuario — que lleva bloques (texto +
        imagen/PDF) en vez de texto suelto.

        Sin refinamiento iterativo a propósito: el reranker ya recorta a
        DEFAULT_RERANKER_TOP_N fragmentos, que entran holgados en la ventana de Gemini.
        """
        context_str = "\n\n".join(
            nodo.get_content(metadata_mode=MetadataMode.LLM) for nodo in nodos
        )
        sistema = (
            self.DEFAULT_CONTEXT_PROMPT_STR.format(context_str=context_str)
            + "\n\n"
            + (self.system_prompt or "").strip()
        )

        bloques: List = [TextBlock(text=query_text)]
        bloques.extend(chatbot_adjuntos.bloques(adjuntos or []))

        return [
            ChatMessage(role=MessageRole.SYSTEM, content=sistema),
            *user_memory.get_all(),
            ChatMessage(role=MessageRole.USER, blocks=bloques),
        ]

    async def stream_query(self, query_text: str, user_id: int, effective_campana: str, task_id: str,
                           adjuntos: Optional[List["chatbot_adjuntos.Adjunto"]] = None):
        """
        Flujo completo: Mejora Pregunta -> Búsqueda Híbrida (BM25+Vector) -> Respuesta con Memoria.
        """
        if not hasattr(self, 'index') or self.index is None:
            yield "Error: El índice no está inicializado."
            return

        adjuntos = list(adjuntos or [])
        logger.info(
            f"Task {task_id}: Starting processing for user {user_id}"
            + (f" con {len(adjuntos)} adjunto(s)" if adjuntos else "")
        )

        try:
            # 1. Embedding de la consulta (async, no bloquea el event loop) en paralelo
            #    con la reconstrucción del historial desde SQL. El embedding se calcula
            #    UNA sola vez y se reutiliza en: caché, retrieval vectorial y guardado.
            embedding_task = asyncio.create_task(
                Settings.embed_model.aget_query_embedding(query_text)
            )
            memory_task = asyncio.create_task(self._get_memory_for_user_async(user_id))

            query_embedding, user_memory = await asyncio.gather(embedding_task, memory_task)

            # 1.b ¿La responde una tabla de datos? (app/chatbot_tablas.py). Va acá,
            #     ANTES del caché y del retrieval, porque cuando la respuesta está en
            #     una fila la búsqueda vectorial no aporta nada: solo agrega chunks
            #     que compiten con el dato exacto. Reutiliza el embedding ya
            #     calculado como una de las tres señales de ruteo, así que no cuesta
            #     ninguna llamada extra. Si no rutea, devuelve None y sigue todo igual.
            #
            #     Con adjuntos no se intenta: ahí la pregunta es sobre el archivo, y
            #     una razón social que aparezca en una captura no tiene por qué ser
            #     lo que se está preguntando.
            consulta_tabla = None
            if not adjuntos:
                consulta_tabla = await asyncio.to_thread(
                    chatbot_tablas.resolver, self.config.id, query_text, query_embedding
                )

            # 2. Verificar Caché (reutiliza el embedding), solo si la consulta es cacheable.
            #    Una consulta que rutea a una tabla NUNCA es cacheable: la clave del
            #    caché es el embedding del texto, y "el gestor del cliente 33815.1" y
            #    "...33816.1" están muy por encima del umbral de 0.95 siendo dos
            #    preguntas con respuestas distintas. Un hit ahí sería servir el dato
            #    equivocado con total seguridad.
            es_cacheable = (
                consulta_tabla is None
                and self._es_consulta_cacheable(query_text, user_memory, adjuntos)
            )
            if es_cacheable:
                cached_response = self.cache.check(query_text, query_embedding)
                if cached_response:
                    # Simulamos streaming para mantener la compatibilidad con el frontend
                    yield cached_response
                    # Logueamos sin bloquear el event loop
                    await asyncio.to_thread(
                        self._log_query_details, query_text, cached_response,
                        "Respuesta desde Caché", user_id, effective_campana, task_id
                    )
                    return

            # 3. Retriever Híbrido: BM25 precomputado en init + vectorial. Se recupera
            #    de más (CHATBOT_RETRIEVAL_TOP_K) y el reranker filtra a top_n: prioriza
            #    recall (que el chunk correcto entre) delegando la precisión al cross-encoder.
            #    El wrapper inyecta el embedding ya calculado para evitar otra llamada HTTP;
            #    en un seguimiento condensado la query cambia y ahí sí se re-embebe (correcto).
            #    Con una tabla ruteada no se construye: la respuesta ya está identificada
            #    y los chunks solo competirían con el dato exacto.
            retriever = None
            if consulta_tabla is None:
                top_k = settings.CHATBOT_RETRIEVAL_TOP_K
                vector_retriever = self.index.as_retriever(similarity_top_k=top_k)

                if self.bm25_retriever is not None:
                    retriever = QueryFusionRetriever(
                        [vector_retriever, self.bm25_retriever],
                        similarity_top_k=top_k,
                        num_queries=1,
                        mode="reciprocal_rerank", # pyright: ignore[reportArgumentType]
                        use_async=True
                    )
                    logger.info("Hybrid Retriever (BM25 + Vector) READY.")
                else:
                    logger.warning("⚠️ BM25 no disponible. Usando SOLO Vectorial.")
                    retriever = vector_retriever

                retriever = PrecomputedQueryEmbeddingRetriever(
                    retriever, query_text, query_embedding
                )

            # 4. y 5. Generación en streaming. Tres caminos con los mismos prompts: el
            #    motor de LlamaIndex para el turno de solo texto, y el equivalente armado
            #    a mano cuando hay adjuntos (astream_chat no acepta bloques), cuando el
            #    bot desambigua (hay que mirar lo recuperado ANTES de generar) o cuando
            #    la respuesta salió de una tabla de datos (no hay nada que recuperar).
            self.token_counter.reset_counts()
            full_response = ""
            opciones: List[str] = []
            # Cuando la lista de opciones sale ANTES de generar no hay llamada al
            # modelo y el turno no se cobra. No alcanza con mirar `opciones`: la lista
            # también puede salir DESPUÉS de generar (ver más abajo), y ahí el modelo
            # se llamó igual. `texto_generado` guarda, en ese caso, lo que el modelo
            # escribió de verdad — que no es lo que ve el operador.
            sin_llamada_al_modelo = False
            texto_generado: Optional[str] = None
            desambigua = bool(
                chatbot_desambiguacion.activo(self.slug) and not adjuntos
                and consulta_tabla is None
            )
            textos_adjuntos: List["chatbot_adjuntos.TextoAdjunto"] = []
            source_nodes: List[NodeWithScore] = []
            query_condensada_val: Optional[str] = None

            if consulta_tabla is not None:
                # La respuesta está en la tabla: el contexto son las filas, no chunks.
                # Se arma la conversación a mano igual que en el camino de adjuntos —
                # mismo layout de mensajes, mismo historial— pero con el bloque de datos
                # en lugar de los documentos recuperados.
                logger.info(
                    f"Task {task_id}: tabla '{consulta_tabla.tabla.nombre}' "
                    f"(forma={consulta_tabla.forma}, motivo={consulta_tabla.motivo}, "
                    f"filas={len(consulta_tabla.filas)})."
                )
                sistema = (
                    chatbot_tablas.armar_bloque(consulta_tabla)
                    + "\n\n"
                    + (self.system_prompt or "").strip()
                )
                mensajes = [
                    ChatMessage(role=MessageRole.SYSTEM, content=sistema),
                    *user_memory.get_all(),
                    ChatMessage(role=MessageRole.USER, content=query_text),
                ]
                async for chunk in await self.llm.astream_chat(mensajes):
                    delta = chunk.delta or ""
                    if delta:
                        full_response += delta
                        yield delta

            elif adjuntos or desambigua:
                # Condensar -> LEER los adjuntos -> recuperar -> responder.
                # Qdrant y BM25 solo indexan texto, así que al índice hay que pedirle
                # con texto: la pregunta condensada MÁS lo que se pudo leer del adjunto.
                # Sin eso, "¿por qué paga tanto?" no recupera nada aunque la factura
                # diga "cargo fijo" e "impuestos". El modelo igual mira el archivo al
                # responder: esto es para BUSCAR, no lo reemplaza.
                #
                # Las dos cosas son independientes y leer puede costar una llamada al
                # modelo, así que van juntas y el turno paga la más lenta, no la suma.
                pregunta_busqueda, textos_adjuntos = await asyncio.gather(
                    self._condensar_pregunta(query_text, user_memory),
                    chatbot_adjuntos.extraer_textos(adjuntos, self.slug),
                )
                query_condensada_val = (
                    pregunta_busqueda[:2000] if pregunta_busqueda != query_text else None
                )

                terminos = chatbot_adjuntos.terminos_para_recuperacion(textos_adjuntos)
                if terminos:
                    logger.info(
                        f"Task {task_id}: leídos {len(textos_adjuntos)} adjunto(s), "
                        f"{chatbot_adjuntos.tokens_de_lectura(textos_adjuntos)} tokens."
                    )
                source_nodes = await self._recuperar_nodos(
                    retriever,
                    f"{pregunta_busqueda}\n{terminos}" if terminos else pregunta_busqueda,
                )

                # Lo leído, a la vista del operador. Si el bot contesta cualquier cosa
                # porque leyó mal la factura, sin esto no hay forma de distinguirlo de
                # documentación faltante. Va por fuera de full_response: es un aviso
                # del sistema, no parte de la respuesta que se guarda y se cachea.
                lectura = chatbot_adjuntos.resumen_lectura(textos_adjuntos)
                if lectura:
                    yield f"_{lectura}_\n\n"

                if desambigua:
                    opciones = chatbot_desambiguacion.proponer(
                        source_nodes, user_memory.get_all()
                    )

                if opciones:
                    # Nada de lo recuperado responde con claridad, pero hay varios temas
                    # documentados compitiendo: se le ofrecen al operador en vez de la
                    # frase de "no encontré". Sin LLM (la lista sale de los encabezados
                    # del propio manual), así que este turno no consume tokens.
                    logger.info(
                        f"Task {task_id}: desambiguación con {len(opciones)} opciones."
                    )
                    full_response = chatbot_desambiguacion.render(opciones)
                    sin_llamada_al_modelo = True
                    yield full_response
                else:
                    mensajes = self._armar_mensajes(
                        query_text, source_nodes, user_memory, adjuntos
                    )
                    # Segunda oportunidad para la desambiguación. El gate de arriba le
                    # cede el turno al modelo apenas algo puntúa alto, y el modelo se
                    # niega igual más seguido de lo que parece (23% de los "no encontré"
                    # de voltara en agosto salieron con score >= 3,0). Mientras lo
                    # generado todavía pueda terminar siendo esa negación sola se retiene
                    # sin mostrarlo, para poder cambiarlo por la lista de temas; una
                    # respuesta normal se suelta en la primera palabra, así que el TTFT
                    # —lo único que el operador siente— no se toca.
                    retenido: Optional[str] = "" if desambigua else None
                    async for chunk in await self.llm.astream_chat(mensajes):
                        delta = chunk.delta or ""
                        if not delta:
                            continue
                        if retenido is not None:
                            retenido += delta
                            if chatbot_desambiguacion.retener(retenido):
                                continue
                            # No era (o dejó de ser) una negación: sale todo junto.
                            delta, retenido = retenido, None
                        full_response += delta
                        yield delta

                    if retenido:
                        opciones = chatbot_desambiguacion.proponer_tras_negativa(
                            source_nodes, user_memory.get_all()
                        )
                        if opciones:
                            logger.info(
                                f"Task {task_id}: el modelo se negó sobre material que "
                                f"puntúa alto; se reemplaza por {len(opciones)} opciones."
                            )
                            texto_generado = retenido
                        salida = (
                            chatbot_desambiguacion.render(opciones) if opciones else retenido
                        )
                        full_response += salida
                        yield salida
            else:
                # Motor de Chat con condensación: reescribe el seguimiento a una pregunta
                # autónoma usando el historial ANTES de recuperar. Sin esto, "¿cuánto tarda?"
                # se embebe sin contexto y recupera cualquier cosa. from_defaults no condensa
                # si el historial está vacío, así que el primer turno no paga la reescritura.
                chat_engine = CondensePlusContextChatEngine.from_defaults(
                    retriever=retriever,
                    memory=user_memory,
                    llm=self.llm,
                    system_prompt=self.system_prompt,
                    context_prompt=PromptTemplate(self.DEFAULT_CONTEXT_PROMPT_STR),
                    condense_prompt=PromptTemplate(self.DEFAULT_CONDENSE_PROMPT_STR),
                    node_postprocessors=[self.reranker],
                )

                streaming_response = await chat_engine.astream_chat(query_text)

                async for token in streaming_response.async_response_gen():
                    full_response += token
                    yield token

                source_nodes = streaming_response.source_nodes
                query_condensada_val = consulta_condensada(streaming_response, query_text)

            # 6. Guardar en Caché al finalizar, reutilizando el embedding (sin llamada HTTP).
            # Solo si la consulta era cacheable (auto-contenida) y la respuesta es sustancial:
            # cachear un seguimiento contamina el caché para todos los usuarios del bot.
            # Una lista de opciones tampoco se cachea: es una repregunta, no una respuesta,
            # y congelarla dejaría al bot preguntando lo mismo aunque el corpus cambie.
            if es_cacheable and not opciones and len(full_response) > 20 and "Error" not in full_response:
                await asyncio.to_thread(self.cache.save, query_text, full_response, query_embedding)

            # 7. Logging
            context_str = f"Q Original: {query_text}\n\n\nFuentes:\n"
            if consulta_tabla is not None:
                # Se guarda el contexto REAL que vio el modelo, igual que con los
                # chunks: es lo único que después permite entender por qué respondió
                # lo que respondió, y lo que hace auditable el camino de tablas desde
                # la pantalla de solicitudes.
                context_str += consulta_tabla.para_log()
            elif source_nodes:
                for node in source_nodes:
                    fname = node.metadata.get('file_name', 'N/A')
                    score = node.score or 0.0
                    # Extraer el texto real que se utilizó como contexto
                    node_text = node.get_content(metadata_mode=MetadataMode.NONE)

                    # Guardar archivo, score y el contenido completo del nodo
                    context_str += f"--- Archivo: {fname} (Score: {score:.4f}) ---\n"
                    context_str += f"Contenido:\n{node_text}\n\n"

            # El path streaming NO puebla de forma fiable el TokenCountingHandler (su callback
            # no observa la respuesta ya completa); además un ping de keepalive del LLM podría
            # dejar el contador con tokens ajenos a esta consulta. Por eso estimamos SIEMPRE con
            # el mismo tokenizer (cl100k_base): output = respuesta; input ≈ system_prompt +
            # contexto + query. Corre post-streaming (no afecta la latencia/TTFT).
            #
            # El turno de desambiguación previa queda en 0/0: no hubo llamada al modelo,
            # así que cargarle el contexto recuperado inflaría el libro de uso de IA con
            # tokens que nadie pagó. Cuando la lista salió DESPUÉS de generar sí se
            # cobra, y se cobra lo que el modelo escribió (la negación), no la lista que
            # terminó viendo el operador.
            input_tokens = 0
            output_tokens = 0
            if not sin_llamada_al_modelo:
                try:
                    enc = tiktoken.get_encoding("cl100k_base")
                    generado = texto_generado if texto_generado is not None else full_response
                    output_tokens = len(enc.encode(generado)) if generado else 0
                    prompt_aprox = f"{self.system_prompt or ''}\n{context_str}"
                    input_tokens = len(enc.encode(prompt_aprox))
                    # Los adjuntos no son texto: el tokenizer no los ve y quedarían gratis en
                    # el libro de uso de IA, que es justo donde se mira si el feature se fue
                    # de presupuesto. Se suma una estimación (ver chatbot_adjuntos).
                    input_tokens += chatbot_adjuntos.tokens_estimados(adjuntos)
                except Exception:
                    logger.exception("No se pudieron estimar los tokens del chatbot (tokenizer).")

            # La consulta se guarda con la marca de los adjuntos: es lo que el operador
            # relee en su historial y lo que reconstruye la memoria del bot en la
            # repregunta ("¿y el importe?" un turno después de mandar la factura).
            query_para_log = query_text
            marca = chatbot_adjuntos.resumen(adjuntos)
            if marca:
                query_para_log = f"{query_text}\n{marca}"
            # Además de QUÉ archivos vinieron, QUÉ decían: el historial se reconstruye
            # leyendo esta columna, así que sin el texto el turno siguiente ("¿y el
            # importe?") se relee como una pregunta sin sujeto. Encuadrado como dato:
            # se va a releer como si lo hubiera escrito el operador.
            lectura_historial = chatbot_adjuntos.marca_historial(textos_adjuntos)
            if lectura_historial:
                query_para_log = f"{query_para_log}\n{lectura_historial}"

            # Logging (SQL + archivos) en un thread para no bloquear el event loop
            from app.vacios_conocimiento import score_maximo

            await asyncio.to_thread(
                self._log_query_details,
                query_para_log, full_response, context_str,
                user_id, effective_campana, task_id,
                input_tokens, output_tokens, None,
                score_maximo(source_nodes),
                query_condensada_val,
                con_adjuntos=bool(adjuntos),
                respuesta_modelo=texto_generado,
            )

        except Exception as e:
            logger.exception(f"Critical error in stream_query: {e}")
            yield f"\n[Error del Sistema]: {str(e)}"
