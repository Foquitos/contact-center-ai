"""Registry de chatbots: carga los bots activos desde la BD y los re-instancia
en caliente cuando cambian.

Reemplaza a las 13 instancias módulo-level de services.py. Cada worker de
Gunicorn tiene su propio registry; la coherencia entre workers se logra
releyendo pagina_web.Chatbots (una query barata) como máximo una vez por TTL
(settings.CHATBOT_REGISTRY_TTL_SECONDS):

- El indexador bumpea index_version al terminar un reindexado -> el worker ve
  la versión nueva y re-instancia el bot (el alias de Qdrant ya apunta al
  índice nuevo; docstore/BM25 se recargan del persist_dir versionado).
- El panel admin bumpea updated_at al editar prompt/nombre -> mismo mecanismo.
- Un bot creado después del startup aparece en <= TTL sin reiniciar el backend
  (un slug desconocido fuerza además un refresh inmediato).

Guardia importante: NUNCA se instancia un bot sin índice (index_version=0 o
alias inexistente en Qdrant) — QdrantVectorStore crearía una colección real con
el nombre del alias y rompería el swap atómico del indexador.
"""
import logging
import os
import threading
import time
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text

from app.config import settings
from app.chatbot_config import ChatbotConfig
from app import rag_settings

logger = logging.getLogger(__name__)

# index_version/index_status/last_indexed_at salen de ChatbotIndexState del ENTORNO
# actual (dev y prod comparten SQL pero cada uno tiene su propio índice). Si el entorno
# no tiene fila (bot nunca indexado acá), COALESCE deja version 0 y get() cae al índice
# local si existe. updated_at sigue viniendo de Chatbots (cambios de prompt, compartidos).
_CHATBOTS_QUERY = text("""
    SELECT c.id, c.slug, c.nombre, c.descripcion, c.system_prompt, c.grupo,
           p.code AS permission_code, c.activo, c.temperatura, c.permite_adjuntos,
           COALESCE(s.index_version, 0) AS index_version,
           s.index_status, s.last_indexed_at, c.updated_at
    FROM [Acme].[pagina_web].[Chatbots] c
    JOIN [Acme].[pagina_web].[Permissions] p ON p.id = c.permission_id
    LEFT JOIN [Acme].[pagina_web].[ChatbotIndexState] s
           ON s.chatbot_id = c.id AND s.environment = :env
    WHERE c.activo = 1
""")


class ChatbotRegistry:
    def __init__(self, engine, ttl_seconds: Optional[int] = None):
        self.engine = engine
        self.ttl = ttl_seconds if ttl_seconds is not None else settings.CHATBOT_REGISTRY_TTL_SECONDS
        self._snapshot: Dict[str, ChatbotConfig] = {}
        self._snapshot_at: float = 0.0
        self._snapshot_lock = threading.Lock()
        self._instances: Dict[str, object] = {}
        # (index_version, updated_at) con los que se instanció cada bot vivo
        self._instance_meta: Dict[str, Tuple[int, object]] = {}
        self._slug_locks: Dict[str, threading.Lock] = {}
        self._slug_locks_guard = threading.Lock()
        self._qdrant_client = None
        self._qdrant_aclient = None

    # ------------------------------------------------------------------ setup

    def configure_global_settings(self) -> None:
        """LLM/embeddings/reranker globales de LlamaIndex, una vez por proceso."""
        rag_settings.configure_global_settings()

    def _ensure_qdrant_clients(self):
        if self._qdrant_client is None:
            from qdrant_client import QdrantClient
            from qdrant_client.async_qdrant_client import AsyncQdrantClient

            api_key = settings.QDRANT_API_KEY or None
            self._qdrant_client = QdrantClient(url=settings.QDRANT_URL, api_key=api_key)
            self._qdrant_aclient = AsyncQdrantClient(url=settings.QDRANT_URL, api_key=api_key)
        return self._qdrant_client, self._qdrant_aclient

    def load_all(self) -> None:
        """Startup: carga el snapshot e instancia (best-effort) los bots con índice."""
        self._refresh_snapshot(force=True)
        for slug in list(self._snapshot.keys()):
            try:
                self.get(slug)
            except Exception:
                logger.exception(f"Fallo instanciando el chatbot '{slug}' en el startup.")
        disponibles = sorted(self._instances.keys())
        logger.info(f"ChatbotRegistry: {len(disponibles)} bots disponibles: {disponibles}")

    # ------------------------------------------------------------------ snapshot

    def _refresh_snapshot(self, force: bool = False) -> None:
        with self._snapshot_lock:
            if not force and (time.monotonic() - self._snapshot_at) < self.ttl:
                return
            try:
                with self.engine.connect() as conn:
                    rows = conn.execute(
                        _CHATBOTS_QUERY, {"env": settings.ENVIRONMENT}
                    ).mappings().all()
            except Exception:
                # Con BD caída seguimos sirviendo con el snapshot/instancias viejas.
                logger.exception("No se pudo refrescar el snapshot de chatbots; se mantiene el anterior.")
                self._snapshot_at = time.monotonic()
                return

            self._snapshot = {row["slug"]: ChatbotConfig.from_row(row) for row in rows}
            self._snapshot_at = time.monotonic()

            # Bots desactivados/borrados: soltar la instancia para liberar memoria.
            for slug in list(self._instances.keys()):
                if slug not in self._snapshot:
                    logger.info(f"Chatbot '{slug}' ya no está activo; se descarta su instancia.")
                    self._instances.pop(slug, None)
                    self._instance_meta.pop(slug, None)

    # ------------------------------------------------------------------ acceso

    def get_config(self, slug: str) -> Optional[ChatbotConfig]:
        self._refresh_snapshot()
        cfg = self._snapshot.get(slug)
        if cfg is None:
            # Bot posiblemente creado hace segundos: un refresh fuera de TTL.
            self._refresh_snapshot(force=True)
            cfg = self._snapshot.get(slug)
        return cfg

    def list_active(self) -> List[ChatbotConfig]:
        self._refresh_snapshot()
        return list(self._snapshot.values())

    def get(self, slug: str):
        """Instancia viva del bot, o None si no existe/está inactivo/no tiene índice."""
        cfg = self.get_config(slug)
        if cfg is None:
            return None

        # cfg.index_version ya es la versión de ESTE entorno (ChatbotIndexState). Si el
        # entorno todavía no publicó ninguna (fila ausente => 0) pero hay un índice en el
        # filesystem local, se sirve ese: cubre la transición de dev (tenía índices locales
        # antes de existir su fila de estado) y es red de seguridad si el estado quedara en 0.
        if cfg.index_version <= 0:
            local_v = self._local_index_version(cfg)
            if local_v > 0:
                logger.info(
                    f"Chatbot '{slug}': sin versión publicada en '{settings.ENVIRONMENT}'; "
                    f"se sirve el índice local v{local_v}."
                )
                cfg = replace(cfg, index_version=local_v)

        if cfg.index_version <= 0:
            logger.warning(f"Chatbot '{slug}' todavía no fue indexado; no está disponible.")
            return None

        instance = self._instances.get(slug)
        # Pin opcional (CHATBOT_FOLLOW_INDEX_RELOAD=False): fija la instancia del arranque y
        # no recarga ante cambios. Con estado por entorno ya no hace falta para aislar dev de
        # prod (cada uno sigue su propia versión); queda como interruptor de depuración.
        if instance is not None and not settings.CHATBOT_FOLLOW_INDEX_RELOAD:
            return instance

        meta = (cfg.index_version, cfg.updated_at)
        if instance is not None and self._instance_meta.get(slug) == meta:
            return instance

        with self._get_slug_lock(slug):
            # Double-check: otro thread pudo instanciarlo mientras esperábamos.
            instance = self._instances.get(slug)
            if instance is not None and self._instance_meta.get(slug) == meta:
                return instance
            nuevo = self._instantiate(cfg)
            if nuevo is not None:
                # La instancia vieja sigue atendiendo los requests en curso hasta
                # que el GC la libere; el swap de referencia es atómico.
                self._instances[slug] = nuevo
                self._instance_meta[slug] = meta
                return nuevo
            # Si la re-instanciación falló pero había una instancia vieja sana,
            # seguimos sirviendo con ella antes que devolver None.
            return instance

    def _local_index_version(self, cfg: ChatbotConfig) -> int:
        """Máxima versión v{N} con índice persistido en el filesystem local (docstore.json).

        En dev el index_version de la BD es el de prod (comparten SQL), pero los persist_dir
        y las colecciones Qdrant son locales por server. Esta es la versión que dev puede
        servir de verdad. Devuelve 0 si no hay ninguna indexada localmente."""
        base = cfg.storage_dir
        if not os.path.isdir(base):
            return 0
        best = 0
        for name in os.listdir(base):
            if name.startswith("v") and name[1:].isdigit():
                v = int(name[1:])
                if v > best and os.path.exists(os.path.join(base, name, "docstore.json")):
                    best = v
        return best

    def _get_slug_lock(self, slug: str) -> threading.Lock:
        with self._slug_locks_guard:
            if slug not in self._slug_locks:
                self._slug_locks[slug] = threading.Lock()
            return self._slug_locks[slug]

    def _instantiate(self, cfg: ChatbotConfig):
        from chatBot import ChatBot

        try:
            client, aclient = self._ensure_qdrant_clients()

            # Guardia anti-footgun: si el alias no existe, QdrantVectorStore crearía
            # una colección REAL con su nombre y el próximo swap de alias fallaría.
            alias_names = {a.alias_name for a in client.get_aliases().aliases}
            if cfg.collection_alias not in alias_names:
                logger.error(
                    f"El alias '{cfg.collection_alias}' no existe en Qdrant "
                    f"(index_version={cfg.index_version}). Reindexar el bot '{cfg.slug}'."
                )
                return None

            logger.info(f"Instanciando chatbot '{cfg.slug}' (index v{cfg.index_version})...")
            return ChatBot(
                config=cfg,
                qdrant_client=client,
                qdrant_aclient=aclient,
                sql_engine=self.engine,
            )
        except Exception:
            logger.exception(f"No se pudo instanciar el chatbot '{cfg.slug}'.")
            return None
