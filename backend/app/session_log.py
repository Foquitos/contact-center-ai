"""Registro de eventos de sesión (login / logout / intento fallido) y presencia.

Fuente de la pantalla "Logs de Ingreso". Todo es DEFENSIVO: ninguna de estas
funciones debe romper el login/logout ni el /me si la tabla pagina_web.LoginAudit
o el permiso todavía no existen (misma filosofía que rbac.registrar_auditoria).
Loguean el problema y siguen.

created_at lo pone la BD con DEFAULT SYSDATETIME() (hora local Argentina, igual
que RbacAuditLog) → se sirve tal cual, sin ajuste de zona.
"""
import logging
from typing import Optional

from sqlalchemy import text, bindparam

from app.database import engine

logger = logging.getLogger(__name__)

# Un usuario se considera "conectado ahora" si el último heartbeat de su sesión
# (last_seen, o el login si nunca hubo heartbeat) es más reciente que esto. El
# frontend refresca /me cada ~5 min, así que 15 min tolera un par de ciclos
# perdidos sin marcar como desconectado a alguien que sigue navegando.
PRESENCIA_MINUTOS = 15

EVENTOS_VALIDOS = {"login", "logout", "login_failed"}


def registrar_evento(conn, documento: Optional[int], event: str,
                     ip: Optional[str] = None, user_agent: Optional[str] = None) -> None:
    """Inserta una fila en pagina_web.LoginAudit usando una conexión ya abierta
    (dentro de un engine.begin() del caller, que hace commit)."""
    if event not in EVENTOS_VALIDOS:
        logger.warning(f"Evento de sesión desconocido, no se registra: {event!r}")
        return
    try:
        conn.execute(
            text("""
                INSERT INTO [Acme].[pagina_web].[LoginAudit]
                    (documento, event, ip_address, user_agent)
                VALUES (:documento, :event, :ip, :ua)
            """),
            {
                "documento": documento,
                "event": event,
                "ip": (ip or None) and str(ip)[:64],
                "ua": (user_agent or None) and str(user_agent)[:400],
            },
        )
    except Exception as e:
        logger.error(f"No se pudo registrar evento de sesión ({event} doc={documento}): {e}")


def registrar_evento_autonomo(documento: Optional[int], event: str,
                              ip: Optional[str] = None, user_agent: Optional[str] = None) -> None:
    """Igual que registrar_evento pero abre y commitea su propia transacción.
    Para call sites que no tienen una conexión a mano (ej. el /token/)."""
    if engine is None:
        return
    try:
        with engine.begin() as conn:
            registrar_evento(conn, documento, event, ip, user_agent)
    except Exception as e:
        logger.error(f"No se pudo registrar evento de sesión autónomo ({event} doc={documento}): {e}")


def actualizar_last_seen(documento: int) -> None:
    """Heartbeat de presencia: marca last_seen=ahora en la última fila 'login' del
    usuario que no tenga un 'logout' posterior. Lo llama GET /me (cada ~5 min).
    Silencioso: nunca debe hacer fallar /me."""
    if engine is None or documento is None:
        return
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE la
                       SET la.last_seen = SYSDATETIME()
                      FROM [Acme].[pagina_web].[LoginAudit] la
                     WHERE la.id = (
                            SELECT TOP 1 l.id
                              FROM [Acme].[pagina_web].[LoginAudit] l
                             WHERE l.documento = :doc AND l.event = 'login'
                               AND NOT EXISTS (
                                    SELECT 1 FROM [Acme].[pagina_web].[LoginAudit] lo
                                     WHERE lo.documento = l.documento
                                       AND lo.event = 'logout'
                                       AND lo.created_at > l.created_at)
                             ORDER BY l.created_at DESC)
                """),
                {"doc": documento},
            )
    except Exception as e:
        # Nivel debug: es esperable que falle si la migración aún no se aplicó.
        logger.debug(f"No se pudo actualizar last_seen (doc={documento}): {e}")


def resolver_nombres(conn, docs: set) -> dict:
    """documento -> 'Nombre Apellido'. Cubre nómina (chatbot.vw_nomina) y usuarios
    externos (pagina_web.Usuarios_extra), igual criterio que
    roles.py::_resolver_nombres_documentos. Best-effort: si falla, el frontend
    muestra el número."""
    if not docs:
        return {}
    try:
        docs_int = [int(d) for d in docs if d is not None]
    except (TypeError, ValueError):
        return {}
    if not docs_int:
        return {}
    try:
        q = text("""
            SELECT documento, LTRIM(RTRIM(nombre + ' ' + apellido)) AS nombre_completo
            FROM chatbot.vw_nomina WHERE documento IN :ids
            UNION
            SELECT documento, LTRIM(RTRIM(nombre + ' ' + apellido)) AS nombre_completo
            FROM [Acme].[pagina_web].[Usuarios_extra] WHERE documento IN :ids
        """).bindparams(bindparam("ids", expanding=True))
        return {int(doc): nombre for doc, nombre in conn.execute(q, {"ids": docs_int}).fetchall()}
    except Exception as e:
        logger.warning(f"No se pudieron resolver nombres para el log de ingresos: {e}")
        return {}


def extraer_ip(request) -> Optional[str]:
    """IP real del cliente. El login/logout llega Flask -> FastAPI, así que
    FastAPI ve la IP de Flask; el frontend reenvía la IP real en X-Forwarded-For
    (primer hop). Fallback a la IP directa de la conexión."""
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


# UAs de librerías/servidor que NO son un dispositivo real: la llamada interna
# Flask -> FastAPI viaja por requests, cuyo User-Agent es "python-requests/x.y".
# Guardar eso haría que todas las filas figuren como un "dispositivo" genérico.
_UA_SERVIDOR = ("python-requests", "python-urllib", "curl/", "go-http", "okhttp", "java/", "aiohttp")


def _es_ua_servidor(ua: Optional[str]) -> bool:
    return bool(ua) and ua.strip().lower().startswith(_UA_SERVIDOR)


def extraer_user_agent(request) -> Optional[str]:
    """User-agent del navegador. El frontend lo reenvía en X-Client-User-Agent y,
    como respaldo a prueba de proxies que descartan headers custom, también en el
    cuerpo de la request (ver los routers). El User-Agent directo a FastAPI es el
    de python-requests (la llamada interna), así que se descarta: mejor NULL
    ("desconocido") que un dispositivo falso."""
    ua = request.headers.get("X-Client-User-Agent")
    if ua and not _es_ua_servidor(ua):
        return ua
    ua = request.headers.get("User-Agent")
    if ua and not _es_ua_servidor(ua):
        return ua
    return None
