import logging
from typing import Optional
from Auditor import AuditorIA
from app.database import engine, engine_chatbot
from app.chatbot_registry import ChatbotRegistry
from RRHH import RRHH_SQL
from chatbot_sql_service import ChatbotSQLService
from app.config import settings

logger = logging.getLogger(__name__)

# --- FUNCIÓN AUXILIAR PARA NO REPETIR CÓDIGO ---
def init_service(service_name: str, service_class, *args, **kwargs):
    try:
        instance = service_class(*args, **kwargs)
        logger.info(f"{service_name} initialized successfully.")
        return instance
    except Exception as e:
        logger.exception(f"Failed to initialize {service_name}: {e}. Service will be unavailable.")
        return None

# --- INICIALIZACIÓN DE SERVICIOS ORIGINALES (SIN ROMPER IMPORTS) ---
Auditor: Optional[AuditorIA] = init_service('Auditor', AuditorIA, engine=engine) if engine else None
RRHH_instance: Optional[RRHH_SQL] = init_service('RRHH', RRHH_SQL)
chatbot_sql: Optional[ChatbotSQLService] = init_service('ChatbotSQLService', ChatbotSQLService, db_engine=engine_chatbot, api_key=settings.GEMINI_CHATBOT_API_KEY)

# --- CHATBOTS RAG ---
# Antes había 13 instancias módulo-level (chat_Voltara, chat_CSV_*, ...) armadas
# desde el .env. Ahora los bots viven en pagina_web.Chatbots y el registry los
# instancia desde la BD, con hot-reload cuando el indexador publica una versión
# nueva del índice (sin reiniciar el backend).
chatbot_registry = ChatbotRegistry(engine=engine)
try:
    chatbot_registry.configure_global_settings()
    chatbot_registry.load_all()
except Exception as e:
    logger.exception(f"Fallo inicializando el ChatbotRegistry: {e}. Los chatbots RAG no estarán disponibles.")
