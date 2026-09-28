import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

class Config:
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', b'_5#y2L"F4Q8z\n\xec]/')
    FASTAPI_BASE_URL = os.environ.get('FASTAPI_URL', 'http://localhost:8000')

    # Tope duro del cuerpo de CUALQUIER request de la app. Sin esto, Flask acepta la
    # subida entera antes de que nadie la mire y un archivo enorme se come la memoria
    # del worker.
    #
    # OJO CON BAJARLO: este tope es global y la subida más pesada de la app NO es el
    # chatbot sino la auditoría por archivos (Voltara / CSV), que manda tandas de 10
    # audios en un mismo POST. Cuando este valor era 25 MB —puesto pensando solo en los
    # adjuntos del chatbot— toda subida de audios de Voltara moría con un 413, y como
    # Werkzeug contesta ese 413 con una página HTML, el navegador mostraba el críptico
    # "Unexpected token '<', "<!doctype "... is not valid JSON" en vez de un motivo.
    # El límite chico del chatbot vive ahora en CHATBOT_MAX_UPLOAD_MB (abajo), aplicado
    # solo en su propia ruta.
    # OJO: nginx tiene su propio client_max_body_size y manda antes que este.
    MAX_CONTENT_LENGTH = int(os.environ.get('MAX_UPLOAD_MB', 500)) * 1024 * 1024

    # Barrera temprana SOLO para los adjuntos del chatbot (app/routes/chatbot.py): deja
    # margen sobre el tope real que valida el backend
    # (CHATBOT_ADJUNTOS_MAX_MB_TOTAL = 15 MB) para que el rechazo llegue con un mensaje
    # explicado y no como una conexión cortada, sin que el worker tenga que tragarse
    # cientos de MB.
    CHATBOT_MAX_UPLOAD_MB = int(os.environ.get('CHATBOT_MAX_UPLOAD_MB', 25))

    # Flask-WTF vence el token CSRF a la HORA (WTF_CSRF_TIME_LIMIT=3600 por defecto).
    # Cargar documentación del conocimiento es trabajo lento —se abre la pantalla, se
    # junta el material, se pega, se revisa— y pasar de una hora con la pantalla
    # abierta es lo normal, no la excepción. Al guardar, el token ya estaba vencido:
    # Flask contestaba 400 con una página HTML y el navegador mostraba un error que
    # hablaba de timeout, cuando lo que había vencido era el token.
    #
    # None ata el token a la SESIÓN en vez de a un reloj propio. No baja la protección:
    # el token sigue siendo secreto y ligado a la sesión, que es la que gobierna el
    # acceso. Lo único que se pierde es el límite al replay de un token ya robado, y
    # quien tenga el token tiene también la cookie de sesión.
    WTF_CSRF_TIME_LIMIT = None
