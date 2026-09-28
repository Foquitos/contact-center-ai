import os
import sys
import logging
import logging.handlers
from app.config import settings
from app.scheduler_logging import etiqueta_actual

LOG_FORMAT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
# Igual al de arriba pero con la etiqueta de la corrida del scheduler, que es lo
# que permite aislar un job entre todos los que corren en paralelo.
LOG_FORMAT_JOB = '%(asctime)s - [%(job_ctx)s] %(name)s - %(levelname)s - %(message)s'
LOG_LEVEL = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
formatter = logging.Formatter(LOG_FORMAT)

# systemd traduce el prefijo '<N>' de cada línea de stdout a la PRIORITY del
# journal (SyslogLevelPrefix viene activado por defecto). Sin esto, TODO lo que
# escribe el scheduler entra al journal como 'info' y `journalctl -p err` no
# filtra nada.
NIVEL_A_SYSLOG = {
    logging.CRITICAL: 2,
    logging.ERROR: 3,
    logging.WARNING: 4,
    logging.INFO: 6,
    logging.DEBUG: 7,
}


class FiltroContextoJob(logging.Filter):
    """Copia la etiqueta `job/run` de la corrida en curso a cada registro."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.job_ctx = etiqueta_actual() or "-"
        return True


class FormatoJournal(logging.Formatter):
    """Formatter que antepone la prioridad de syslog a CADA línea del registro.

    El prefijo va línea por línea y no solo en la primera a propósito: journald
    parsea el stdout por líneas, así que si el '<3>' fuera únicamente al inicio,
    las líneas del traceback entrarían como 'info' y `journalctl -p err` mostraría
    el título del error sin el traceback que lo explica.
    """

    def format(self, record: logging.LogRecord) -> str:
        prioridad = NIVEL_A_SYSLOG.get(record.levelno, 6)
        texto = super().format(record)
        return "\n".join(f"<{prioridad}>{linea}" for linea in texto.splitlines())


def _bajo_journald() -> bool:
    """True si systemd conectó nuestra salida al journal (setea JOURNAL_STREAM).

    Corriendo a mano en una terminal no hay journald que interprete los '<3>', y
    el prefijo sería basura en pantalla.
    """
    return "JOURNAL_STREAM" in os.environ


def setup_logging(proceso: str = "api"):
    """Configura el logging del proceso.

    `proceso='scheduler'` agrega lo que solo tiene sentido en run_scheduler.py:
    etiqueta de corrida en cada línea, prioridades de syslog para el journal y un
    archivo aparte con warnings y errores.
    """
    try:
        os.makedirs(settings.DEFAULT_LOG_DIR, exist_ok=True)
    except OSError as e:
        print(f"Error creating log directory {settings.DEFAULT_LOG_DIR}: {e}", file=sys.stderr)


    root_logger = logging.getLogger()
    root_logger.setLevel(LOG_LEVEL)

    logging.getLogger('selenium').setLevel(logging.ERROR)
    logging.getLogger('urllib3').setLevel(logging.ERROR)
    logging.getLogger('hpack').setLevel(logging.ERROR)
    # El SDK de Gemini loguea "AFC is enabled with max remote calls: 10" en INFO
    # en CADA llamada a generate_content (AFC viene activado por defecto), lo que
    # inunda el log del backend. Solo nos interesan sus warnings/errores.
    logging.getLogger('google_genai').setLevel(logging.WARNING)

    es_scheduler = proceso == "scheduler"

    if not any(isinstance(h, logging.StreamHandler) for h in root_logger.handlers):
        console_handler = logging.StreamHandler(sys.stdout)
        if es_scheduler:
            clase = FormatoJournal if _bajo_journald() else logging.Formatter
            console_handler.setFormatter(clase(LOG_FORMAT_JOB))
            console_handler.addFilter(FiltroContextoJob())
        else:
            console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    setup_file_logger('Api', 'api.log')
    setup_file_logger('chatBot', 'chatbot.log')
    setup_file_logger('Auditor', 'auditor.log')

    if es_scheduler:
        setup_problem_logger()

    logger = logging.getLogger(__name__)
    logger.info("Logging configured with separate files.")

def setup_file_logger(logger_name, filename):
    """Helper para configurar un logger de archivo rotatorio."""
    logger = logging.getLogger(logger_name)
    log_path = os.path.join(settings.DEFAULT_LOG_DIR, filename)

    if not any(isinstance(h, logging.handlers.RotatingFileHandler) and os.path.abspath(h.baseFilename) == os.path.abspath(log_path) for h in logger.handlers):
        try:
            file_handler = logging.handlers.RotatingFileHandler(
                log_path, maxBytes=10*1024*1024, backupCount=3, encoding='utf-8'
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
            logger.propagate = True
            logging.getLogger().info(f"--- Logger '{logger_name}' FILE handler configured for {filename} ---")
        except Exception as e:
            logging.getLogger().error(f"Failed to set up file handler for '{logger_name}' at '{log_path}': {e}")


def setup_problem_logger(filename: str = 'scheduler_errors.log'):
    """Archivo aparte con SOLO los warnings y errores del scheduler.

    Es el atajo para el caso más común: saber si algo falló sin pelearse con el
    journal. Va sobre el root logger, así que junta los fallos de cualquier
    módulo que corra dentro del scheduler (tasks, Auditor, colas de chatbot).
    Como el proceso es exclusivo del scheduler, no se cuela nada de la API.
    """
    log_path = os.path.join(settings.DEFAULT_LOG_DIR, filename)
    root_logger = logging.getLogger()

    if any(isinstance(h, logging.handlers.RotatingFileHandler) and os.path.abspath(h.baseFilename) == os.path.abspath(log_path) for h in root_logger.handlers):
        return

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=10*1024*1024, backupCount=3, encoding='utf-8'
        )
        file_handler.setLevel(logging.WARNING)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT_JOB))
        file_handler.addFilter(FiltroContextoJob())
        root_logger.addHandler(file_handler)
        root_logger.info(f"--- Warnings y errores del scheduler duplicados en {filename} ---")
    except Exception as e:
        root_logger.error(f"Failed to set up problem handler at '{log_path}': {e}")
