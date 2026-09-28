import sys
import logging
from sqlalchemy import create_engine, text, Engine
from sqlalchemy.exc import SQLAlchemyError
from app.config import settings

logger = logging.getLogger(__name__)

try:
    logger.info("Attempting to connect to database.")
    engine: Engine = create_engine(settings.connection_string,
        pool_size=10,         # Conexiones simultáneas permitidas
        max_overflow=20,      # Conexiones extra si el pool se llena
        pool_timeout=30,      # Segundos a esperar si no hay conexiones libres
        pool_recycle=1800     # Reciclar conexiones cada 30 min para evitar desconexiones silenciosas
    )
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    engine.dispose() # Limpia el pool heredado. Los workers crearán conexiones nuevas.
    logger.info("Database connection successful.")
except SQLAlchemyError as e:
    logger.exception(f"Database connection failed: {e}")
    sys.exit(f"FATAL: Database connection failed: {e}")
except Exception as e:
    logger.exception(f"An unexpected error occurred during database setup: {e}")
    sys.exit(f"FATAL: An unexpected error occurred during database setup: {e}")

try:
    logger.info("Attempting to connect to database chatbot.")
    engine_chatbot: Engine = create_engine(settings.CONNECTION_STRING_chatbot,
        pool_size=10,         
        max_overflow=20,      
        pool_timeout=30,      
        pool_recycle=1800     
    )
    with engine_chatbot.connect() as connection:
        connection.execute(text("SELECT 1"))
        
    engine_chatbot.dispose() # Limpia el pool heredado. Los workers crearán conexiones nuevas.
    
    logger.info("Database connection successful.")
except SQLAlchemyError as e:
    logger.exception(f"Database connection failed: {e}")
    sys.exit(f"FATAL: Database connection failed: {e}")
except Exception as e:
    logger.exception(f"An unexpected error occurred during database setup: {e}")
    sys.exit(f"FATAL: An unexpected error occurred during database setup: {e}")