"""Historial de conversación del chatbot (pagina_web.query_chatbots_logs).

El historial es por usuario Y por bot (columna effective_campana): cada bot tiene su
propio hilo, igual que la memoria que arma ChatBot._get_memory_for_user. Antes era solo
por usuario y la conversación de un bot aparecía en la pantalla de otro.
"""
import logging
from typing import Dict, List

from sqlalchemy import text

logger = logging.getLogger(__name__)


def get_history(sql_engine, user_id: int, slug: str, limit: int = 20) -> List[Dict[str, str]]:
    """Últimos mensajes del usuario con ese bot, en orden cronológico, para el frontend."""
    if sql_engine is None:
        return []

    history: List[Dict[str, str]] = []
    try:
        query = text("""
            SELECT TOP (:limit) query, response
            FROM [Acme].[pagina_web].[query_chatbots_logs]
            WHERE user_id = :uid and active = 1 and effective_campana = :slug
            ORDER BY fecha DESC
        """)
        with sql_engine.connect() as conn:
            result = conn.execute(
                query, {"limit": limit, "uid": user_id, "slug": slug}
            ).fetchall()

        for row in reversed(result):
            if row.query:
                history.append({"role": "user", "content": str(row.query)})
            if row.response:
                history.append({"role": "bot", "content": str(row.response)})
    except Exception as e:
        logger.error(f"Error leyendo historial desde SQL: {e}")
        return []

    return history


def clear_history(sql_engine, user_id: int, slug: str) -> None:
    """Borra (soft delete) el historial del usuario con ese bot; no toca los demás bots."""
    if sql_engine is None:
        return
    try:
        query = text(
            "UPDATE [Acme].[pagina_web].[query_chatbots_logs] SET active = 0 "
            "WHERE user_id = :uid and effective_campana = :slug"
        )
        with sql_engine.begin() as conn:
            conn.execute(query, {"uid": user_id, "slug": slug})
        logger.info(f"Historial SQL borrado para el usuario {user_id} en el bot '{slug}'")
    except Exception as e:
        logger.error(f"Error borrando historial en SQL: {e}")
