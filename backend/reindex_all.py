"""CLI de reindexado de chatbots (reemplaza a init_knowledge.py / trigger_rag_update.py).

Encola y procesa síncronamente el reindexado de uno o varios bots leyendo la
definición desde la BD (pagina_web.Chatbots). No hace falta reiniciar el
backend: publica el índice con swap de alias en Qdrant y los workers recargan
solos al ver el bump de index_version.

Uso (desde backend/, con el venv activo y Qdrant corriendo):
    python reindex_all.py all                 # todos los bots activos con docs
    python reindex_all.py voltara              # un bot por slug
    python reindex_all.py csv_premium vantix   # varios

Se usa también en el cutover inicial para construir las colecciones v1.
"""
import argparse
import logging
import sys

from sqlalchemy import text

from app.config import settings
from app.logging_config import setup_logging
from app import rag_settings
from app.chatbot_indexer import ejecutar_index_job
from app.database import engine

setup_logging()
logger = logging.getLogger("reindex_all")


def _bots_activos_con_docs() -> list[dict]:
    query = text("""
        SELECT c.id, c.slug
        FROM pagina_web.Chatbots c
        WHERE c.activo = 1
          AND (EXISTS (SELECT 1 FROM pagina_web.ChatbotDocMarkdown m
                       WHERE m.chatbot_id = c.id AND m.activo = 1)
               OR EXISTS (SELECT 1 FROM pagina_web.ChatbotDocVinculo v
                          JOIN pagina_web.ChatbotDocMarkdown m2 ON m2.id = v.doc_id AND m2.activo = 1
                          WHERE v.chatbot_id = c.id))
        ORDER BY c.slug
    """)
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(query).mappings().all()]


def _bot_por_slug(slug: str) -> dict | None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, slug FROM pagina_web.Chatbots WHERE slug = :slug"),
            {"slug": slug},
        ).mappings().first()
    return dict(row) if row else None


def _encolar_y_procesar(chatbot_id: int, slug: str) -> bool:
    """Inserta el job como 'running' (claim directo) y lo ejecuta acá mismo."""
    with engine.begin() as conn:
        pendiente = conn.execute(text("""
            SELECT id FROM pagina_web.ChatbotIndexJobs
            WHERE chatbot_id = :cid AND status IN ('pending', 'running')
              AND environment = :env
        """), {"cid": chatbot_id, "env": settings.ENVIRONMENT}).first()
        if pendiente:
            logger.warning(f"[{slug}] Ya hay un job pendiente/en curso (id {pendiente.id}); se omite.")
            return False
        job_id = conn.execute(text("""
            INSERT INTO pagina_web.ChatbotIndexJobs (chatbot_id, status, started_at, environment)
            OUTPUT INSERTED.id
            VALUES (:cid, 'running', SYSDATETIME(), :env)
        """), {"cid": chatbot_id, "env": settings.ENVIRONMENT}).first().id

    ejecutar_index_job(job_id, chatbot_id)

    with engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM pagina_web.ChatbotIndexJobs WHERE id = :jid"),
            {"jid": job_id},
        ).scalar()
    if status == "pending":
        # Gemini sin cupo: el job quedó en la cola y lo va a retomar el scheduler.
        logger.warning(f"[{slug}] Sin cupo de Gemini: el job {job_id} quedó encolado para reintentar.")
    return status == "completed"


def main() -> int:
    parser = argparse.ArgumentParser(description="Reindexa chatbots desde la BD.")
    parser.add_argument("slugs", nargs="+", help="Slugs de bots, o 'all' para todos los activos con docs.")
    args = parser.parse_args()

    if "all" in args.slugs:
        bots = _bots_activos_con_docs()
    else:
        bots = []
        for slug in args.slugs:
            bot = _bot_por_slug(slug)
            if bot is None:
                logger.error(f"No existe un chatbot con slug '{slug}'.")
                return 1
            bots.append(bot)

    if not bots:
        logger.warning("No hay bots para reindexar.")
        return 0

    logger.info(f"Reindexando {len(bots)} bots: {[b['slug'] for b in bots]}")
    rag_settings.configure_global_settings()

    fallidos = []
    for bot in bots:
        logger.info(f"===== {bot['slug']} =====")
        if not _encolar_y_procesar(bot["id"], bot["slug"]):
            fallidos.append(bot["slug"])

    if fallidos:
        logger.error(f"Fallaron: {fallidos}. Revisar pagina_web.ChatbotIndexJobs.error")
        return 1
    logger.info("Todos los bots reindexados correctamente. No hace falta reiniciar el backend.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
