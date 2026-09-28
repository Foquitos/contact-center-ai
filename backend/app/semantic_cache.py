"""Caché semántico de respuestas del chatbot sobre Qdrant.

Equivalente de la colección Chroma `{collection}_cache`: una colección
`cache_{slug}` por bot con el embedding de la pregunta y la respuesta en el
payload. La caché sobrevive al swap de alias del reindexado; el indexer llama
clear() al terminar para no servir respuestas basadas en docs viejos.

Umbral: Chroma comparaba distancia < 0.2; acá se compara similitud coseno
>= min_score (settings.CHATBOT_CACHE_MIN_SCORE). Caducidad de entradas: 3 días,
igual que antes.
"""
import logging
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from qdrant_client import QdrantClient
from qdrant_client import models as qmodels

logger = logging.getLogger(__name__)

_TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S.%f"


class SemanticCache:
    def __init__(self, client: QdrantClient, slug: str, min_score: float, ttl_days: int = 3):
        self.client = client
        self.collection = f"cache_{slug}"
        self.min_score = min_score
        self.ttl = timedelta(days=ttl_days)

    def _ensure_collection(self, vector_size: int) -> None:
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=qmodels.VectorParams(
                    size=vector_size, distance=qmodels.Distance.COSINE
                ),
            )

    def check(self, query_text: str, query_embedding: List[float]) -> Optional[str]:
        """Devuelve la respuesta cacheada si hay un hit vigente; None si no."""
        try:
            if not self.client.collection_exists(self.collection):
                return None

            hits = self.client.query_points(
                collection_name=self.collection,
                query=query_embedding,
                limit=1,
                score_threshold=self.min_score,
                with_payload=True,
            ).points

            if not hits:
                logger.info("Cache MISS")
                return None

            hit = hits[0]
            payload = hit.payload or {}

            # Caducidad: las entradas viejas se borran y cuentan como miss.
            timestamp_str = payload.get("timestamp")
            if timestamp_str:
                try:
                    stored_time = datetime.strptime(timestamp_str, _TIMESTAMP_FMT)
                    if datetime.now() - stored_time > self.ttl:
                        logger.info(f"Cache entry expired (> {self.ttl.days} days). Deleting {hit.id}")
                        self.client.delete(
                            collection_name=self.collection,
                            points_selector=qmodels.PointIdsList(points=[hit.id]),
                        )
                        return None
                except ValueError:
                    logger.warning("Error parsing cache timestamp. Treating as expired.")
                    return None

            response = payload.get("response")
            if response:
                logger.info(f"Cache HIT (Score: {hit.score:.4f}): {query_text[:30]}...")
                return response
            return None

        except Exception as e:
            logger.error(f"Error checking cache '{self.collection}': {e}")
            return None

    def save(self, query_text: str, response_text: str, query_embedding: List[float]) -> None:
        try:
            self._ensure_collection(len(query_embedding))
            self.client.upsert(
                collection_name=self.collection,
                points=[
                    qmodels.PointStruct(
                        id=str(uuid.uuid4()),
                        vector=query_embedding,
                        payload={
                            "original_query": query_text,
                            "response": response_text,
                            "timestamp": datetime.now().strftime(_TIMESTAMP_FMT),
                        },
                    )
                ],
            )
            logger.info(f"Saved to cache: {query_text[:50]}...")
        except Exception as e:
            logger.error(f"Error saving to cache '{self.collection}': {e}")

    def clear(self) -> None:
        """Purga la caché completa del bot (tras cada reindexado)."""
        try:
            if self.client.collection_exists(self.collection):
                self.client.delete_collection(self.collection)
                logger.info(f"Cache collection '{self.collection}' deleted.")
        except Exception as e:
            logger.error(f"Error clearing cache '{self.collection}': {e}")
