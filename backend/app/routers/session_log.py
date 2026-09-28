"""Endpoints de "Logs de Ingreso" (pagina_web.LoginAudit).

- POST /session/logout    : lo llama el frontend Flask al cerrar sesión (el token
                            aún es válido) para dejar registrado el logout, que de
                            otro modo el backend nunca vería.
- GET  /session/activos   : quién está conectado ahora (presencia por heartbeat).
- GET  /session/logins    : historial paginado/filtrable de eventos de sesión.
- GET  /session/dashboard : KPIs + agregados para los gráficos de uso.

Permiso: logs:login (nace sin asignar → solo super admin hasta asignarlo).
created_at está en hora local Argentina (SYSDATETIME), se sirve tal cual.
"""
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import text

from app.database import engine
from app.dependencies import RoleChecker
from app.models import User
from app.security import get_current_active_user
from app.session_log import (
    PRESENCIA_MINUTOS,
    registrar_evento,
    resolver_nombres,
    extraer_ip,
    extraer_user_agent,
)

router = APIRouter(prefix="/session", tags=["Session Log"])
logger = logging.getLogger(__name__)

require_logs_login = RoleChecker(["logs:login"])


class ClientInfo(BaseModel):
    """Info del cliente que el frontend manda en el cuerpo (a prueba de proxies
    que descartan headers custom). Todos opcionales: si no vienen, se cae a los
    headers."""
    client_ip: Optional[str] = None
    client_user_agent: Optional[str] = None

# Whitelist de columnas ordenables (NUNCA interpolar el querystring crudo).
_LOGINS_ORDEN = {
    "fecha": "created_at",
    "event": "event",
    "documento": "documento",
    "ip": "ip_address",
}


@router.post("/logout")
def registrar_logout(
    request: Request,
    info: Optional[ClientInfo] = None,
    current_user: User = Depends(get_current_active_user),
):
    """Registra el cierre de sesión del usuario del token. Idempotente en la
    práctica: si el frontend lo llama de más, solo agrega otra fila 'logout'."""
    ip = (info.client_ip if info else None) or extraer_ip(request)
    ua = (info.client_user_agent if info else None) or extraer_user_agent(request)
    try:
        with engine.begin() as conn:
            registrar_evento(conn, current_user.usuario, "logout", ip, ua)
    except Exception as e:
        # No romper el logout del frontend por un problema de registro.
        logger.error(f"No se pudo registrar logout de {current_user.usuario}: {e}")
    return {"ok": True}


@router.get("/activos")
def listar_activos(current_user: User = Depends(require_logs_login)):
    """Sesiones activas ahora: por usuario, su último 'login' cuyo heartbeat
    (last_seen, o el login si nunca hubo) esté dentro de PRESENCIA_MINUTOS y sin
    'logout' posterior."""
    query = text(f"""
        SELECT l.documento,
               l.ip_address,
               l.user_agent,
               l.created_at AS login_at,
               COALESCE(l.last_seen, l.created_at) AS last_seen
          FROM [Acme].[pagina_web].[LoginAudit] l
         WHERE l.event = 'login'
           AND l.id = (
                SELECT TOP 1 l2.id
                  FROM [Acme].[pagina_web].[LoginAudit] l2
                 WHERE l2.documento = l.documento AND l2.event = 'login'
                 ORDER BY l2.created_at DESC)
           AND COALESCE(l.last_seen, l.created_at) >= DATEADD(MINUTE, -{PRESENCIA_MINUTOS}, SYSDATETIME())
           AND NOT EXISTS (
                SELECT 1 FROM [Acme].[pagina_web].[LoginAudit] lo
                 WHERE lo.documento = l.documento AND lo.event = 'logout'
                   AND lo.created_at > l.created_at)
         ORDER BY last_seen DESC
    """)
    try:
        with engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(query).mappings().all()]
            nombres = resolver_nombres(conn, {r["documento"] for r in rows})
        for r in rows:
            r["nombre"] = nombres.get(r["documento"])
    except Exception as e:
        logger.error(f"Error listando sesiones activas: {e}")
        raise HTTPException(status_code=500, detail="Error al listar sesiones activas.")
    return {"total": len(rows), "data": rows}


@router.get("/logins")
def listar_logins(
    event: Optional[str] = Query(None, description="login | logout | login_failed"),
    documento: Optional[int] = None,
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
    orden: str = Query("fecha", pattern="^(fecha|event|documento|ip)$"),
    dir: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_logs_login),
):
    """Historial de eventos de sesión, paginado/filtrable, enriquecido con el
    nombre del usuario."""
    filtros = []
    params: dict = {"limit": limit, "offset": offset}
    if event:
        filtros.append("event = :event")
        params["event"] = event
    if documento is not None:
        filtros.append("documento = :documento")
        params["documento"] = documento
    if fecha_desde:
        filtros.append("created_at >= :fecha_desde")
        params["fecha_desde"] = fecha_desde
    if fecha_hasta:
        filtros.append("created_at < DATEADD(DAY, 1, :fecha_hasta)")
        params["fecha_hasta"] = fecha_hasta

    where_clause = f"WHERE {' AND '.join(filtros)}" if filtros else ""
    orden_sql = f"{_LOGINS_ORDEN[orden]} {'ASC' if dir == 'asc' else 'DESC'}"

    query = text(f"""
        SELECT id, documento, event, ip_address, user_agent, created_at, last_seen
          FROM [Acme].[pagina_web].[LoginAudit]
          {where_clause}
         ORDER BY {orden_sql}, id DESC
        OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
    """)
    count_query = text(f"SELECT COUNT(*) FROM [Acme].[pagina_web].[LoginAudit] {where_clause}")

    try:
        with engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(query, params).mappings().all()]
            total = conn.execute(count_query, params).scalar()
            nombres = resolver_nombres(conn, {r["documento"] for r in rows})
        for r in rows:
            r["nombre"] = nombres.get(r["documento"])
    except Exception as e:
        logger.error(f"Error listando log de ingresos: {e}")
        raise HTTPException(status_code=500, detail="Error al listar el log de ingresos.")

    return {"total": total, "data": rows}


@router.get("/dashboard")
def dashboard(
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
    current_user: User = Depends(require_logs_login),
):
    """KPIs (relativos a HOY) + agregados en el rango para los gráficos."""
    # Rango con default defensivo (últimos 30 días) por si el frontend no lo manda.
    params: dict = {}
    rango = []
    if fecha_desde:
        rango.append("created_at >= :fecha_desde")
        params["fecha_desde"] = fecha_desde
    if fecha_hasta:
        rango.append("created_at < DATEADD(DAY, 1, :fecha_hasta)")
        params["fecha_hasta"] = fecha_hasta
    if not rango:
        rango.append("created_at >= DATEADD(DAY, -30, CAST(SYSDATETIME() AS DATE))")
    where_rango = " AND ".join(rango)

    # KPIs de HOY (independientes del rango) + conectados ahora.
    kpis_query = text(f"""
        SELECT
            (SELECT COUNT(*) FROM [Acme].[pagina_web].[LoginAudit]
              WHERE event = 'login' AND created_at >= CAST(SYSDATETIME() AS DATE)) AS logins_hoy,
            (SELECT COUNT(DISTINCT documento) FROM [Acme].[pagina_web].[LoginAudit]
              WHERE event = 'login' AND created_at >= CAST(SYSDATETIME() AS DATE)) AS usuarios_hoy,
            (SELECT COUNT(*) FROM [Acme].[pagina_web].[LoginAudit]
              WHERE event = 'login_failed' AND created_at >= CAST(SYSDATETIME() AS DATE)) AS fallidos_hoy,
            (SELECT COUNT(*) FROM (
                SELECT l.documento
                  FROM [Acme].[pagina_web].[LoginAudit] l
                 WHERE l.event = 'login'
                   AND l.id = (SELECT TOP 1 l2.id FROM [Acme].[pagina_web].[LoginAudit] l2
                                WHERE l2.documento = l.documento AND l2.event = 'login'
                                ORDER BY l2.created_at DESC)
                   AND COALESCE(l.last_seen, l.created_at) >= DATEADD(MINUTE, -{PRESENCIA_MINUTOS}, SYSDATETIME())
                   AND NOT EXISTS (SELECT 1 FROM [Acme].[pagina_web].[LoginAudit] lo
                                    WHERE lo.documento = l.documento AND lo.event = 'logout'
                                      AND lo.created_at > l.created_at)
            ) AS a) AS conectados_ahora
    """)

    por_dia_query = text(f"""
        SELECT CAST(created_at AS DATE) AS dia,
               COUNT(*) AS logins,
               COUNT(DISTINCT documento) AS usuarios_unicos
          FROM [Acme].[pagina_web].[LoginAudit]
         WHERE event = 'login' AND {where_rango}
         GROUP BY CAST(created_at AS DATE)
         ORDER BY dia
    """)
    por_hora_query = text(f"""
        SELECT DATEPART(HOUR, created_at) AS hora, COUNT(*) AS logins
          FROM [Acme].[pagina_web].[LoginAudit]
         WHERE event = 'login' AND {where_rango}
         GROUP BY DATEPART(HOUR, created_at)
         ORDER BY hora
    """)
    # dow ISO estable (1=Lunes..7=Domingo) independiente de SET DATEFIRST.
    por_dow_query = text(f"""
        SELECT (DATEPART(WEEKDAY, created_at) + @@DATEFIRST + 5) % 7 + 1 AS dow,
               COUNT(*) AS logins
          FROM [Acme].[pagina_web].[LoginAudit]
         WHERE event = 'login' AND {where_rango}
         GROUP BY (DATEPART(WEEKDAY, created_at) + @@DATEFIRST + 5) % 7 + 1
         ORDER BY dow
    """)
    top_query = text(f"""
        SELECT TOP 15 documento, COUNT(*) AS logins
          FROM [Acme].[pagina_web].[LoginAudit]
         WHERE event = 'login' AND {where_rango} AND documento IS NOT NULL
         GROUP BY documento
         ORDER BY logins DESC
    """)
    fallidos_query = text(f"""
        SELECT CAST(created_at AS DATE) AS dia, COUNT(*) AS fallidos
          FROM [Acme].[pagina_web].[LoginAudit]
         WHERE event = 'login_failed' AND {where_rango}
         GROUP BY CAST(created_at AS DATE)
         ORDER BY dia
    """)

    try:
        with engine.connect() as conn:
            kpis = dict(conn.execute(kpis_query).mappings().first() or {})
            por_dia = [dict(r) for r in conn.execute(por_dia_query, params).mappings().all()]
            por_hora = [dict(r) for r in conn.execute(por_hora_query, params).mappings().all()]
            por_dow = [dict(r) for r in conn.execute(por_dow_query, params).mappings().all()]
            top = [dict(r) for r in conn.execute(top_query, params).mappings().all()]
            fallidos = [dict(r) for r in conn.execute(fallidos_query, params).mappings().all()]
            nombres = resolver_nombres(conn, {r["documento"] for r in top})
    except Exception as e:
        logger.error(f"Error armando dashboard de ingresos: {e}")
        raise HTTPException(status_code=500, detail="Error al armar el dashboard de ingresos.")

    for r in top:
        r["nombre"] = nombres.get(r["documento"])

    # promedio diario = logins totales del rango / días con al menos un login.
    total_logins = sum(r["logins"] for r in por_dia)
    dias_con_datos = len(por_dia)
    kpis["promedio_diario"] = round(total_logins / dias_con_datos, 1) if dias_con_datos else 0

    return {
        "kpis": kpis,
        "por_dia": por_dia,
        "por_hora": por_hora,
        "por_dia_semana": por_dow,
        "top_usuarios": top,
        "fallidos_por_dia": fallidos,
    }
