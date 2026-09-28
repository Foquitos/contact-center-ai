import io
import os
import json
import logging
import threading
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

# Rutas absolutas ancladas al directorio de este archivo,
# independientemente del directorio de trabajo desde donde se invoque.
_HERE = os.path.dirname(os.path.abspath(__file__))
_TOKEN_PATH = os.path.join(_HERE, 'token.json')
_CREDENTIALS_PATH = os.path.join(_HERE, 'credentials.json')

# Cuenta de servicio (opcional pero preferida). Si existe el JSON, se usa en
# lugar del flujo OAuth de usuario: no expira (se auto-renueva firmando un JWT),
# no requiere navegador ni reescribe ningún archivo. Es el modo recomendado en
# servidores headless porque elimina de raíz las carreras sobre token.json y la
# regeneración manual del token. Hay que compartir cada Sheet/Drive con el email
# de la cuenta de servicio.
#
# Reutiliza el MISMO JSON que ya usa el resto del sistema vía gspread
# (settings.CREDENTIALS_GOOGLE_SHEET_JSON). Se puede sobreescribir con la env
# GOOGLE_SERVICE_ACCOUNT_FILE.
def _service_account_path() -> str:
    env = os.environ.get('GOOGLE_SERVICE_ACCOUNT_FILE')
    if env:
        return env
    try:
        from app.config import settings
        path = settings.CREDENTIALS_GOOGLE_SHEET_JSON
    except Exception:
        path = 'service_account.json'
    # El JSON vive junto a este módulo; anclamos rutas relativas a _HERE para
    # no depender del directorio de trabajo desde donde se invoque.
    if path and not os.path.isabs(path):
        path = os.path.join(_HERE, path)
    return path

# El scheduler corre con muchos hilos (ThreadPoolExecutor 20 + 5). Sin este lock,
# dos hilos pueden leer/refrescar/escribir token.json a la vez: uno trunca el
# archivo con open('w') mientras otro lo lee vacío, provocando el clásico
# "JSONDecodeError: Expecting value: line 1 column 1 (char 0)".
_token_lock = threading.Lock()


def _load_credentials():
    """Lee token.json tolerando que esté ausente, vacío o corrupto."""
    if not os.path.exists(_TOKEN_PATH):
        return None
    try:
        return Credentials.from_authorized_user_file(_TOKEN_PATH, SCOPES)
    except (ValueError, json.JSONDecodeError) as e:
        # Archivo vacío o corrupto (p. ej. por una escritura interrumpida).
        # Lo tratamos como "sin credenciales" para regenerarlo en lugar de
        # tumbar toda la auditoría.
        logger.warning("token.json ilegible (%s); se intentará regenerar.", e)
        return None


def _save_credentials(creds):
    """Escritura atómica del token: archivo temporal + os.replace (rename atómico).

    Así un lector nunca ve un token.json a medio escribir ni vacío.
    """
    tmp_path = f"{_TOKEN_PATH}.{os.getpid()}.tmp"
    try:
        with open(tmp_path, 'w') as token:
            token.write(creds.to_json())
        os.replace(tmp_path, _TOKEN_PATH)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def _get_credentials():
    """Devuelve credenciales válidas para la API de Google.

    Prefiere una cuenta de servicio si está configurada (ideal para servidores
    headless). Si no, cae al flujo OAuth de usuario sobre token.json, cuya
    sección crítica corre bajo lock para serializar el acceso entre los hilos
    del scheduler.
    """
    sa_path = _service_account_path()
    if sa_path and os.path.exists(sa_path):
        return ServiceAccountCredentials.from_service_account_file(
            sa_path, scopes=SCOPES
        )

    with _token_lock:
        creds = _load_credentials()
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(_CREDENTIALS_PATH, SCOPES)
                creds = flow.run_local_server(port=0)
            _save_credentials(creds)
        return creds


def descarga_drive_memoria(file_id: str) -> io.BytesIO:
    # AvisoUsuarioError viaja hasta el frontend como advertencia legible; se
    # importa acá (y no a nivel módulo) para conservar el uso standalone de
    # este archivo sin depender del paquete AuditorIA.
    from AuditorIA.avisos import AvisoUsuarioError

    try:
        creds = _get_credentials()
    except Exception as e:
        raise AvisoUsuarioError(
            "No se pudo autenticar con Google Drive: las credenciales del servidor "
            "no son válidas o están vencidas. Contacte al administrador."
        ) from e

    service = build('drive', 'v3', credentials=creds)

    # Forzamos la exportación al formato Excel (.xlsx) ya que pandas lo requiere
    mime_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    request = service.files().export_media(fileId=file_id, mimeType=mime_type)

    # Creamos un buffer en memoria en lugar de un archivo en disco
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    try:
        while done is False:
            status, done = downloader.next_chunk()
            print(f"Descarga {int(status.progress() * 100)}%.")
    except HttpError as e:
        if e.resp.status in (403, 404):
            raise AvisoUsuarioError(
                f"Sin acceso al archivo de Google Drive (id {file_id}): verifique que "
                "exista y esté compartido con la cuenta del sistema."
            ) from e
        raise AvisoUsuarioError(
            f"Google Drive devolvió un error al descargar el archivo (HTTP {e.resp.status}). "
            "Intente nuevamente más tarde."
        ) from e

    print("✅ ¡Archivo exportado con éxito en memoria!")
    
    # IMPORTANTÍSIMO: Regresar el "cursor" al principio del archivo en memoria 
    # para que pandas pueda leerlo desde el principio.
    fh.seek(0) 
    return fh