"""Indexador de chatbots: procesa la cola pagina_web.ChatbotIndexJobs.

Reemplaza el flujo viejo (actualizar_rag.flag + subprocess init_knowledge.py +
systemctl restart backend): con Qdrant en modo servidor no hay lock de SQLite,
así que el scheduler indexa en su propio proceso y publica el índice nuevo con
un swap atómico de alias — los workers del backend lo detectan por el bump de
index_version y re-instancian el bot sin reiniciar nada.

Flujo por job:
  1. Materializar a {storage}/{slug}/docs/ el markdown del bot (propio y prestado).
  2. Construir el índice en la colección física bot_{slug}_v{N+1} y persistir
     docstore/index_store en {storage}/{slug}/v{N+1}/.
  3. Swapear el alias bot_{slug} -> colección nueva (una sola llamada, atómica).
  4. UPDATE Chatbots (index_version, index_status, last_indexed_at, updated_at).
  5. Limpiar la versión N-1 anterior (se conserva la N como colchón para los
     workers que aún no recargaron) y purgar el caché semántico.

Si algo falla, el job queda 'failed' con el error legible y NO se toca ni el
alias ni index_version: el índice viejo sigue sirviendo. La excepción es la falta
de cupo de Gemini (429/503): eso no se arregla mirando el error, se arregla
esperando, así que el job vuelve a 'pending' y se reintenta solo (ver
_es_falta_de_cupo e INDEX_REINTENTOS_SIN_CUPO).
"""
import logging
import os
import re
import shutil
from typing import List, Optional

from sqlalchemy import text

from app.config import settings
from app.chatbot_config import ChatbotConfig
from app.semantic_cache import SemanticCache
from app import rag_settings

logger = logging.getLogger(__name__)

_qdrant_client = None

# Resultado de ejecutar_index_job (lo mira el tick del scheduler para decidir si
# sigue drenando la cola o la deja para el próximo).
RESULTADO_COMPLETADO = "completed"
RESULTADO_FALLIDO = "failed"
RESULTADO_REENCOLADO = "requeued"

# Códigos y marcas con las que Gemini avisa que NO hay cupo ahora. Mismo criterio
# que AuditorIA/transcripcion_cola._es_falta_de_capacidad.
_CODIGOS_SIN_CUPO = (429, 503)
_MARCAS_SIN_CUPO = ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "429", "503", "OVERLOADED", "QUOTA")

# Nota que queda en el job mientras espera su próximo intento. El contador viaja en
# el propio texto (y no en una columna nueva) para no sumar una migración a algo que
# solo necesita acordarse de cuántas veces reintentó.
_NOTA_SIN_CUPO = ("Gemini no tenía cupo para generar los embeddings; el reindexado se "
                  "reintenta solo (intento {intento} de {tope}).")
_RE_INTENTO = re.compile(r"intento (\d+) de \d+")


def _es_falta_de_cupo(error: Exception) -> bool:
    """¿El reindexado murió porque Gemini no tiene cupo (429/503) y no por el material?

    Importa la diferencia: un documento roto se arregla mirando el error, pero una
    cuota agotada se arregla esperando. Marcar eso como "falló" manda a Calidad a
    buscar un problema que no existe.
    """
    codigo = getattr(error, "code", None)
    if isinstance(codigo, int) and codigo in _CODIGOS_SIN_CUPO:
        return True
    texto = str(error).upper()
    return any(marca in texto for marca in _MARCAS_SIN_CUPO)


def _intentos_ya_hechos(error_previo: Optional[str]) -> int:
    """Cuántas veces se reencoló ya este job, leído de la nota que dejó el anterior."""
    if not isinstance(error_previo, str):
        return 0
    coincidencia = _RE_INTENTO.search(error_previo)
    return int(coincidencia.group(1)) if coincidencia else 0


def _get_qdrant_client():
    global _qdrant_client
    if _qdrant_client is None:
        from qdrant_client import QdrantClient
        _qdrant_client = QdrantClient(
            url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY or None
        )
    return _qdrant_client


def _get_engine():
    from app.database import engine
    return engine


# ------------------------------------------------------------------- cola

# Claim atómico FIFO: toma el job pending más viejo cuyo bot no tenga otro job
# corriendo. El UPDATE sobre el CTE evita que dos ticks solapados del scheduler
# procesen el mismo job.
_CLAIM_QUERY = text("""
    WITH siguiente AS (
        SELECT TOP (1) *
        FROM pagina_web.ChatbotIndexJobs j
        WHERE j.status = 'pending'
          AND j.environment = :env
          AND NOT EXISTS (
                SELECT 1 FROM pagina_web.ChatbotIndexJobs r
                WHERE r.chatbot_id = j.chatbot_id AND r.status = 'running'
                  AND r.environment = :env
          )
        ORDER BY j.id
    )
    UPDATE siguiente
    SET status = 'running', started_at = SYSDATETIME()
    OUTPUT INSERTED.id, INSERTED.chatbot_id
""")


def procesar_cola_reindexado() -> None:
    """Tick del scheduler (cada ~60s): drena la cola de a un job por vez.
    Serial a propósito: los embeddings de Gemini ya van con rate limit."""
    engine = _get_engine()
    while True:
        try:
            with engine.begin() as conn:
                claimed = conn.execute(_CLAIM_QUERY, {"env": settings.ENVIRONMENT}).first()
        except Exception:
            logger.exception("No se pudo consultar la cola de reindexado.")
            return

        if claimed is None:
            return

        # La config global de llama_index se hace recién acá (lazy): el scheduler
        # no carga los modelos si no hay jobs.
        rag_settings.configure_global_settings()
        if ejecutar_index_job(claimed.id, claimed.chatbot_id) == RESULTADO_REENCOLADO:
            # El job volvió a 'pending' porque no hay cupo en Gemini. Cortar el tick
            # es parte del reintento: si siguiéramos drenando la cola lo tomaríamos
            # de nuevo al instante y quemaríamos los intentos en el mismo minuto.
            logger.warning("Sin cupo de Gemini: se corta el tick; la cola sigue en el próximo.")
            return


def encolar_reindexado_todos() -> None:
    """Job nocturno: encola un reindexado por cada bot activo con docs, para
    mantener el conocimiento fresco (equivalente al 'all' del flujo viejo)."""
    engine = _get_engine()
    query = text("""
        INSERT INTO pagina_web.ChatbotIndexJobs (chatbot_id, requested_by, environment)
        SELECT c.id, NULL, :env
        FROM pagina_web.Chatbots c
        WHERE c.activo = 1
          AND (EXISTS (SELECT 1 FROM pagina_web.ChatbotDocMarkdown m
                       WHERE m.chatbot_id = c.id AND m.activo = 1)
               OR EXISTS (SELECT 1 FROM pagina_web.ChatbotDocVinculo v
                          JOIN pagina_web.ChatbotDocMarkdown m2 ON m2.id = v.doc_id AND m2.activo = 1
                          WHERE v.chatbot_id = c.id))
          AND NOT EXISTS (SELECT 1 FROM pagina_web.ChatbotIndexJobs j
                          WHERE j.chatbot_id = c.id AND j.status IN ('pending', 'running')
                            AND j.environment = :env)
    """)
    try:
        with engine.begin() as conn:
            result = conn.execute(query, {"env": settings.ENVIRONMENT})
        logger.info(f"Reindexado nocturno [{settings.ENVIRONMENT}]: {result.rowcount} bots encolados.")
    except Exception:
        logger.exception("No se pudo encolar el reindexado nocturno.")


def marcar_jobs_huerfanos() -> None:
    """Al arrancar el scheduler: jobs que quedaron 'running' por un proceso caído
    pasan a 'failed' (si no, bloquearían el claim de su bot para siempre)."""
    engine = _get_engine()
    try:
        with engine.begin() as conn:
            # Solo los de ESTE entorno: dev y prod comparten la cola y un scheduler no
            # debe marcar como caído un job que el otro entorno tiene realmente en curso.
            result = conn.execute(text("""
                UPDATE pagina_web.ChatbotIndexJobs
                SET status = 'failed',
                    error = 'El proceso de indexado se reinició con el job en curso.',
                    finished_at = SYSDATETIME()
                WHERE status = 'running' AND environment = :env
            """), {"env": settings.ENVIRONMENT})
        if result.rowcount:
            logger.warning(f"{result.rowcount} jobs de reindexado huérfanos [{settings.ENVIRONMENT}] marcados como failed.")
    except Exception:
        logger.exception("No se pudieron marcar los jobs huérfanos.")


# ------------------------------------------------------------------- indexado

def _cargar_config_y_docs(chatbot_id: int):
    engine = _get_engine()
    with engine.connect() as conn:
        # La versión "actual" para calcular la próxima sale del estado de ESTE entorno
        # (ChatbotIndexState), no del escalar compartido de Chatbots: así dev numera y
        # publica independiente de prod.
        row = conn.execute(text("""
            SELECT c.id, c.slug, c.nombre, c.descripcion, c.system_prompt, c.grupo,
                   p.code AS permission_code, c.activo,
                   COALESCE(s.index_version, 0) AS index_version,
                   s.index_status, s.last_indexed_at, c.updated_at
            FROM pagina_web.Chatbots c
            JOIN pagina_web.Permissions p ON p.id = c.permission_id
            LEFT JOIN pagina_web.ChatbotIndexState s
                   ON s.chatbot_id = c.id AND s.environment = :env
            WHERE c.id = :cid
        """), {"cid": chatbot_id, "env": settings.ENVIRONMENT}).mappings().first()
        if row is None:
            return None, []
        # Documentos markdown in-app (pagina_web.ChatbotDocMarkdown, armados con el
        # asistente de documentación): única fuente de conocimiento. El indexador los
        # materializa a disco para que SimpleDirectoryReader los levante.
        #
        # Incluye los COMPARTIDOS desde otro bot (ChatbotDocVinculo): dos bots de la misma
        # operación suelen necesitar el mismo conocimiento base y duplicar el documento
        # garantizaría que se desincronicen (ej.: Voltara telefónico y Voltara Digital
        # comparten los procedimientos, pero solo Digital tiene el catálogo de cartas).
        docs_md = conn.execute(text("""
            SELECT m.id, m.titulo, m.orden, m.contenido_md
            FROM pagina_web.ChatbotDocMarkdown m
            WHERE m.activo = 1
              AND (m.chatbot_id = :cid
                   OR EXISTS (SELECT 1 FROM pagina_web.ChatbotDocVinculo v
                              WHERE v.doc_id = m.id AND v.chatbot_id = :cid))
            ORDER BY m.orden, m.id
        """), {"cid": chatbot_id}).mappings().all()
    return ChatbotConfig.from_row(row), list(docs_md)


def _limpiar_directorio(path: str) -> None:
    if os.path.exists(path):
        shutil.rmtree(path)
    os.makedirs(path, exist_ok=True)


# ------------------------------------------------------- markdown -> nodos

# El mismo criterio con el que MarkdownNodeParser reconoce un encabezado: numerales
# en la primera columna y un espacio (" ### Título", con sangría, no cuenta).
_RE_ENCABEZADO = re.compile(r"^#+\s")
# Línea divisoria (---, ***, ___): separa, no informa.
_RE_SEPARADOR = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
# Metadata donde viaja el encabezado propio de cada sección mientras se parte en
# pedazos. Es temporal: no llega a los nodos que se guardan.
_META_ENCABEZADO = "encabezado_seccion"


def _encabezado_propio(texto: str) -> Optional[str]:
    """La línea de encabezado con la que arranca el texto, o None si no arranca con una."""
    primera = (texto or "").lstrip("\r\n").split("\n", 1)[0].rstrip()
    return primera if _RE_ENCABEZADO.match(primera) else None


def _sin_cuerpo(texto: str) -> bool:
    """¿El texto es solo encabezados y separadores (o nada)?"""
    return all(
        _RE_ENCABEZADO.match(linea) or _RE_SEPARADOR.match(linea)
        for linea in (texto or "").split("\n")
        if linea.strip()
    )


def _leer_documentos(docs_dir: str) -> list:
    from llama_index.core import SimpleDirectoryReader

    return SimpleDirectoryReader(
        docs_dir,
        filename_as_id=True,
        required_exts=[".md"],
    ).load_data(show_progress=True)


def _nodos_desde_documentos(documents) -> list:
    """Parte los documentos en los nodos que se indexan: una sección por encabezado y,
    si no entra en 512 tokens, en pedazos con solape. Cada pedazo arranca con el
    encabezado de su sección.

    El SentenceSplitter solo le deja el encabezado al PRIMER pedazo; `header_path`
    trae los ancestros, no el título propio. Entonces del segundo pedazo en adelante
    el texto no dice de qué tema es, y ni BM25 ni el embedding ni el reranker lo
    asocian con la consulta. Caso real (csv_isla_de_productos, 2026-09-14): la sección
    "IP para Aplicar Flag 003 - Marca no molestar" medía 4.500 caracteres y quedaba
    en 6 pedazos. El que tenía las 3 preguntas no decía "003" ni "Marca No Molestar",
    así que de 13 consultas sobre el tema le llegó al bot una sola vez. El bot
    contestaba el principio (la tabla por whisper) y el final (qué hacer si las 3
    preguntas están bien), sin las preguntas.

    Mientras se parte, el encabezado viaja en la metadata, así el splitter lo descuenta
    del tamaño de cada pedazo; después se pasa al texto. Se repite EXACTO, sin
    "(continuación)": chatbot_desambiguacion agrupa los pedazos por título y con otro
    texto vería dos temas donde hay uno.

    Los nodos que son solo un encabezado ("# Manual" seguido de "## Capítulo") se
    descartan: no informan nada (su título ya viaja en el `header_path` de los hijos)
    y ocupaban lugar entre los candidatos: 2 de los 12 en la consulta de arriba.

    Ojo con la sangría: para el parser " ### Título" (con un espacio adelante) NO es
    un encabezado y queda como texto de la sección anterior. Normalizarla parece el
    arreglo obvio y está medido que empeora (2026-09-14): el doc 67 de
    csv_isla_de_productos tiene 314 encabezados así en 75k caracteres. Reconocidos, el bot
    pasa de 128 a 400 nodos con mediana de 154 caracteres, el contexto que recibe el
    LLM cae de ~9.500 a ~3.000 caracteres y el score top-1 de 67 consultas reales baja
    de 3,36 a 2,44. Para reconocerlos hay que agrupar además las secciones chicas.
    """
    from llama_index.core.node_parser import MarkdownNodeParser, SentenceSplitter

    secciones = MarkdownNodeParser(include_metadata=True).get_nodes_from_documents(documents)
    secciones = [s for s in secciones if not _sin_cuerpo(s.get_content())]
    for seccion in secciones:
        encabezado = _encabezado_propio(seccion.get_content())
        if encabezado:
            seccion.metadata[_META_ENCABEZADO] = encabezado

    pedazos = SentenceSplitter(chunk_size=512, chunk_overlap=50).get_nodes_from_documents(secciones)
    for pedazo in pedazos:
        encabezado = pedazo.metadata.get(_META_ENCABEZADO)
        # Se reasigna el dict en vez de hacer pop: los pedazos de una misma sección
        # pueden compartir el objeto de metadata.
        pedazo.metadata = {k: v for k, v in pedazo.metadata.items() if k != _META_ENCABEZADO}
        texto = pedazo.get_content()
        if encabezado and _encabezado_propio(texto) != encabezado:
            pedazo.set_content(f"{encabezado}\n\n{texto.lstrip()}")

    return [p for p in pedazos if not _sin_cuerpo(p.get_content())]


def _preparar_docs(cfg: ChatbotConfig, docs_md: List[dict]) -> None:
    """Deja en cfg.docs_dir el markdown a indexar (pagina_web.ChatbotDocMarkdown,
    propio o prestado por otro bot), escrito directo a disco.

    El nombre orden+id da un filename_as_id estable entre reindexados."""
    _limpiar_directorio(cfg.docs_dir)

    for doc in docs_md:
        destino = os.path.join(cfg.docs_dir, f"{doc['orden']:02d}_dbdoc_{doc['id']}.md")
        contenido = (doc.get("contenido_md") or "").strip()
        if not contenido:
            continue
        # Un título como H1 al frente ayuda al MarkdownNodeParser a agrupar el doc
        # cuando el contenido no arranca con un encabezado propio.
        titulo = (doc.get("titulo") or "").strip()
        if titulo and not contenido.lstrip().startswith("#"):
            contenido = f"# {titulo}\n\n{contenido}"
        with open(destino, "w", encoding="utf-8") as fh:
            fh.write(contenido)


def construir_indice(cfg: ChatbotConfig, version: int, qdrant_client) -> int:
    """Construye el índice de cfg.docs_dir en la colección física de `version` y
    persiste docstore/index_store en cfg.persist_dir(version). Devuelve la
    cantidad de nodos indexados. Requiere rag_settings ya configurado."""
    from llama_index.core import StorageContext, VectorStoreIndex
    from llama_index.vector_stores.qdrant import QdrantVectorStore

    documents = _leer_documentos(cfg.docs_dir)
    if not documents:
        raise RuntimeError("No se materializó ningún documento markdown; no hay nada que indexar.")

    logger.info(f"[{cfg.slug}] {len(documents)} documentos. Parseo estructurado por Markdown...")
    # 1. Parseo primario respetando la estructura de títulos del Markdown.
    # 2. Splitter secundario: reparte en ventanas de 512 tokens con solape solo
    #    los nodos que excedan ese tamaño (los más chicos pasan casi intactos).
    #    Evita la doble pasada de embeddings del SemanticSplitter y acota el
    #    tamaño de chunk, que antes quedaba libre.
    # Los nodos vacíos (rompen BM25 en los workers) y los de solo encabezado se
    # descartan ahí adentro.
    nodes = _nodos_desde_documentos(documents)
    if not nodes:
        raise RuntimeError("El parseo no produjo ningún nodo con contenido.")
    logger.info(f"[{cfg.slug}] {len(nodes)} nodos válidos. Generando embeddings...")

    fisica = cfg.collection_fisica(version)
    vector_store = QdrantVectorStore(collection_name=fisica, client=qdrant_client)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    storage_context.docstore.add_documents(nodes)
    index = VectorStoreIndex(nodes, storage_context=storage_context, show_progress=True)

    persist_dir = cfg.persist_dir(version)
    _limpiar_directorio(persist_dir)
    index.storage_context.persist(persist_dir=persist_dir)
    logger.info(f"[{cfg.slug}] Índice v{version} persistido en {persist_dir} y colección '{fisica}'.")
    return len(nodes)


def _swap_alias(qdrant_client, alias: str, fisica: str) -> None:
    """Apunta el alias a la colección nueva en UNA llamada (atómica en Qdrant)."""
    from qdrant_client import models as qmodels

    ops = []
    alias_names = {a.alias_name for a in qdrant_client.get_aliases().aliases}
    if alias in alias_names:
        ops.append(qmodels.DeleteAliasOperation(
            delete_alias=qmodels.DeleteAlias(alias_name=alias)
        ))
    elif qdrant_client.collection_exists(alias):
        # Colección REAL con el nombre del alias (resto de un bug/instanciación
        # incorrecta): hay que borrarla o el create_alias falla.
        logger.warning(f"Existe una colección real llamada '{alias}'; se elimina para crear el alias.")
        qdrant_client.delete_collection(alias)
    ops.append(qmodels.CreateAliasOperation(
        create_alias=qmodels.CreateAlias(collection_name=fisica, alias_name=alias)
    ))
    qdrant_client.update_collection_aliases(change_aliases_operations=ops)


def _limpiar_versiones_viejas(cfg: ChatbotConfig, version_nueva: int, qdrant_client) -> None:
    """Borra colecciones y persist_dirs anteriores a la versión N-1 (la N-1 se
    conserva como colchón para workers que aún sirven la instancia vieja)."""
    for v in range(1, version_nueva - 1):
        coleccion = cfg.collection_fisica(v)
        try:
            if qdrant_client.collection_exists(coleccion):
                qdrant_client.delete_collection(coleccion)
        except Exception:
            logger.warning(f"[{cfg.slug}] No se pudo borrar la colección vieja '{coleccion}'.")
        persist_dir = cfg.persist_dir(v)
        if os.path.exists(persist_dir):
            shutil.rmtree(persist_dir, ignore_errors=True)


def _upsert_index_state(conn, chatbot_id: int, *, version: Optional[int] = None,
                        status: Optional[str] = None, set_last_indexed: bool = False) -> None:
    """Escribe el estado de índice del bot para el ENTORNO actual (ChatbotIndexState).

    UPDATE + INSERT si no existía la fila (dev estrena su fila en el primer reindex).
    Solo actualiza los campos provistos; version/status son opcionales según el momento
    del job (indexing -> ready -> [failed])."""
    sets = ["updated_at = SYSDATETIME()"]
    params = {"cid": chatbot_id, "env": settings.ENVIRONMENT}
    if version is not None:
        sets.append("index_version = :v")
        params["v"] = version
    if status is not None:
        sets.append("index_status = :st")
        params["st"] = status
    if set_last_indexed:
        sets.append("last_indexed_at = SYSDATETIME()")

    conn.execute(text(f"""
        UPDATE pagina_web.ChatbotIndexState
        SET {", ".join(sets)}
        WHERE chatbot_id = :cid AND environment = :env;
        IF @@ROWCOUNT = 0
            INSERT INTO pagina_web.ChatbotIndexState
                (chatbot_id, environment, index_version, index_status, last_indexed_at, updated_at)
            VALUES (:cid, :env, :v_ins, :st_ins,
                    CASE WHEN :last_ins = 1 THEN SYSDATETIME() ELSE NULL END, SYSDATETIME());
    """), {
        **params,
        "v_ins": version if version is not None else 0,
        "st_ins": status,
        "last_ins": 1 if set_last_indexed else 0,
    })


def ejecutar_index_job(job_id: int, chatbot_id: int) -> str:
    """Corre un job de reindexado. Devuelve RESULTADO_COMPLETADO, RESULTADO_FALLIDO
    o RESULTADO_REENCOLADO (volvió a la cola porque Gemini no tenía cupo)."""
    engine = _get_engine()
    cfg: Optional[ChatbotConfig] = None
    try:
        cfg, docs_md = _cargar_config_y_docs(chatbot_id)
        if cfg is None:
            raise RuntimeError(f"El chatbot id {chatbot_id} no existe.")
        if not docs_md:
            raise RuntimeError("El chatbot no tiene documentos activos.")

        logger.info(f"[{cfg.slug}] Iniciando job de reindexado {job_id}...")

        # 'indexing' solo se refleja si es el primer indexado; si el bot ya está
        # 'ready' el índice viejo sigue sirviendo durante todo el build.
        if cfg.index_version == 0:
            with engine.begin() as conn:
                _upsert_index_state(conn, chatbot_id, status="indexing")

        qdrant_client = _get_qdrant_client()
        version_nueva = cfg.index_version + 1
        fisica = cfg.collection_fisica(version_nueva)

        # Restos de un intento fallido anterior con la misma versión.
        if qdrant_client.collection_exists(fisica):
            qdrant_client.delete_collection(fisica)

        _preparar_docs(cfg, docs_md)
        nodos = construir_indice(cfg, version_nueva, qdrant_client)
        _swap_alias(qdrant_client, cfg.collection_alias, fisica)

        # Publicar: el bump de index_version del estado de ESTE entorno dispara el
        # hot-reload en los workers del backend del mismo entorno (<= TTL). El de otros
        # entornos no se entera (lee su propia fila de ChatbotIndexState).
        with engine.begin() as conn:
            _upsert_index_state(
                conn, chatbot_id, version=version_nueva,
                status="ready", set_last_indexed=True,
            )

        # Best-effort: versiones viejas y caché semántico (respuestas basadas en
        # los docs anteriores).
        _limpiar_versiones_viejas(cfg, version_nueva, qdrant_client)
        SemanticCache(qdrant_client, cfg.slug, settings.CHATBOT_CACHE_MIN_SCORE).clear()

        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE pagina_web.ChatbotIndexJobs
                SET status = 'completed', error = NULL, finished_at = SYSDATETIME()
                WHERE id = :jid
            """), {"jid": job_id})
        logger.info(f"[{cfg.slug}] Job {job_id} completado: v{version_nueva}, {nodos} nodos.")
        return RESULTADO_COMPLETADO

    except Exception as e:
        slug = cfg.slug if cfg else f"id={chatbot_id}"
        try:
            with engine.begin() as conn:
                # Falta de cupo: NO es un job fallido, es uno que hay que volver a
                # correr más tarde. Vuelve a 'pending' con la nota de en qué intento
                # va, así el panel muestra "pendiente" y nadie sale a buscar un
                # problema en la documentación. Los intentos están acotados: si la
                # cuota no vuelve, el último sí queda como fallido.
                if _es_falta_de_cupo(e):
                    previo = conn.execute(text(
                        "SELECT error FROM pagina_web.ChatbotIndexJobs WHERE id = :jid"
                    ), {"jid": job_id}).scalar()
                    intento = _intentos_ya_hechos(previo) + 1
                    tope = max(0, settings.INDEX_REINTENTOS_SIN_CUPO)
                    if intento <= tope:
                        conn.execute(text("""
                            UPDATE pagina_web.ChatbotIndexJobs
                            SET status = 'pending', started_at = NULL, finished_at = NULL,
                                error = :nota
                            WHERE id = :jid
                        """), {"jid": job_id,
                               "nota": _NOTA_SIN_CUPO.format(intento=intento, tope=tope)})
                        logger.warning(
                            f"[{slug}] Job {job_id}: Gemini sin cupo ({e.__class__.__name__}). "
                            f"Vuelve a la cola, intento {intento} de {tope}."
                        )
                        return RESULTADO_REENCOLADO

                logger.exception(f"[{slug}] Job de reindexado {job_id} falló.")
                conn.execute(text("""
                    UPDATE pagina_web.ChatbotIndexJobs
                    SET status = 'failed', error = :error, finished_at = SYSDATETIME()
                    WHERE id = :jid
                """), {"jid": job_id, "error": str(e)[:4000]})
                if cfg is not None and cfg.index_version == 0:
                    # Primer indexado fallido: el bot queda marcado (nunca sirvió) en ESTE
                    # entorno. Con índice previo no se toca nada: el alias viejo sigue vivo.
                    _upsert_index_state(conn, chatbot_id, status="failed")
        except Exception:
            logger.exception(f"No se pudo registrar el fallo del job {job_id}.")
        return RESULTADO_FALLIDO
