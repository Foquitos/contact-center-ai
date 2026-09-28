"""Cupos mensuales de auditoría por campaña (pantalla del gerente de operaciones).

`GET /cuotas/` es el tablero: una fila por campaña con el cupo cargado, lo
consumido en el mes, el costo promedio por auditoría de esa campaña y —lo que
motivó la pantalla— cuánto puede llegar a gastar como máximo si consume el cupo
entero. `PUT /cuotas/{campana_id}` sube o baja ese cupo.

`GET /cuotas/mi-saldo` es la otra punta: lo consulta la pantalla de auditar para
mostrarle al supervisor cuánto le queda ANTES de mandar el pedido. Va con
audit:execute (no con audit:cuotas): ver el propio saldo no es gestionar cupos.

La lógica vive en app/cuotas.py; acá solo van permisos, alcance por empresa y
armado de la respuesta.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from app import cuotas
from app.database import engine
from app.dependencies import RoleChecker
from app.models import User
from app.rbac import empresas_permitidas, exigir_acceso_empresa, registrar_auditoria
from app.security import get_current_active_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cuotas", tags=["Cuotas"])

require_gestion = RoleChecker([cuotas.PERMISO_GESTION])
require_auditor = RoleChecker(["audit:execute"])


class CuotaCampanaRequest(BaseModel):
    # None = sin tope (la campaña se mide pero no se bloquea).
    limite: Optional[int] = Field(default=None, ge=0)
    limite_usuario: Optional[int] = Field(default=None, ge=0)
    activo: bool = True
    nota: Optional[str] = Field(default=None, max_length=400)


def _con_tz_utc(dt):
    """Las fechas se graban con SYSUTCDATETIME() y pyodbc las devuelve naive; sin
    el tzinfo el navegador las lee como hora local (ver [[audit-execution-log]])."""
    from datetime import timezone
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


@router.get("/")
def listar_cuotas(anio_mes: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$"),
                  current_user: User = Depends(require_gestion)):
    """Tablero de cupos del mes. Acota a las empresas del usuario (los
    template:<empresa> valen igual acá que en el resto de auditorías)."""
    # Antes de mostrar saldos, ajusta las reservas de corridas ya terminadas: si
    # no, el gerente ve consumido lo que se pidió y no lo que se auditó.
    cuotas.conciliar(engine)
    try:
        with engine.connect() as conn:
            permitidas = empresas_permitidas(conn, current_user)
            datos = cuotas.resumen_campanas(conn, anio_mes=anio_mes,
                                            empresas_permitidas=permitidas)
    except Exception as e:
        logger.error(f"Error armando el tablero de cupos: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    for fila in datos["campanas"]:
        fila["actualizado_en"] = _con_tz_utc(fila.get("actualizado_en"))
    return datos


@router.put("/{campana_id}")
def guardar_cuota(campana_id: int, payload: CuotaCampanaRequest,
                  current_user: User = Depends(require_gestion)):
    """Define el cupo mensual de una campaña. Queda registrado en el log de RBAC:
    subir un cupo es plata, y tiene que poder auditarse quién lo movió."""
    if (payload.limite is not None and payload.limite_usuario is not None
            and payload.limite_usuario > payload.limite):
        raise HTTPException(
            status_code=400,
            detail="El cupo por usuario no puede ser mayor que el de la campaña.",
        )
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, current_user, campana_id=campana_id)

    cuotas.guardar_cupo(engine, campana_id=campana_id, limite=payload.limite,
                        limite_usuario=payload.limite_usuario, activo=payload.activo,
                        nota=payload.nota, documento=current_user.usuario)

    with engine.begin() as conn:
        registrar_auditoria(
            conn, current_user.usuario, "cuota_auditoria_update", "campana", campana_id,
            {"limite": payload.limite, "limite_usuario": payload.limite_usuario,
             "activo": payload.activo, "nota": payload.nota},
        )
        estado = cuotas.estado_cuota(conn, campana_id)
    return {"ok": True, "estado": estado}


@router.get("/{campana_id}/consumo")
def consumo_campana(campana_id: int,
                    anio_mes: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$"),
                    current_user: User = Depends(require_gestion)):
    """Quién se llevó el cupo de la campaña este mes."""
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, current_user, campana_id=campana_id)
        filas = cuotas.consumo_por_usuario(conn, campana_id, anio_mes)
        nombres = _nombres_de_documentos(conn, {str(f["documento"]) for f in filas
                                                if f["documento"] is not None})
        estado = cuotas.estado_cuota(conn, campana_id, anio_mes=anio_mes)
    for f in filas:
        f["nombre"] = nombres.get(str(f["documento"]))
        f["ultimo"] = _con_tz_utc(f.get("ultimo"))
    return {"estado": estado, "usuarios": filas}


@router.get("/mi-saldo")
def mi_saldo(campana_id: int = Query(...), current_user: User = Depends(require_auditor)):
    """Saldo del usuario en una campaña, para mostrarlo en la pantalla de auditar.

    Un exento (Calidad / super admin) devuelve `exento: true` y la pantalla no
    muestra nada: no consume cupo ni se le bloquea nada.
    """
    if cuotas.esta_exento(current_user):
        return {"exento": True, "campana_id": campana_id}
    with engine.connect() as conn:
        estado = cuotas.estado_cuota(conn, campana_id, current_user.usuario)
    return {"exento": False, **estado}


def _nombres_de_documentos(conn, documentos: set) -> dict:
    """documento -> 'Nombre Apellido' (chatbot.vw_nomina). Best-effort: si la
    vista no está en el ambiente, las filas quedan con el documento pelado."""
    if not documentos:
        return {}
    try:
        from sqlalchemy import bindparam
        q = text("""
            SELECT documento, LTRIM(RTRIM(nombre + ' ' + apellido)) AS nombre_completo
            FROM chatbot.vw_nomina WHERE documento IN :ids
        """).bindparams(bindparam("ids", expanding=True))
        return {str(doc): nombre for doc, nombre in conn.execute(q, {"ids": list(documentos)}).fetchall()}
    except Exception as e:
        logger.warning(f"No se pudieron resolver los nombres de los usuarios: {e}")
        return {}
