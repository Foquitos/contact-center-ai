"""Planificador — Seguimiento de los pedidos de refuerzo y turnos sugeridos.

Permisos:
  - `planificador.view`: listar pedidos de refuerzo.
  - `planificador.edit`: crear pedido ('Marcar pedido') o actualizar ('Descartar', nota).
    Registra auditoría en cada cambio.
"""

from datetime import date, datetime
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app import planificador_datos as pdatos
from app import planificador_pedidos as pedidos_lib
from app.database import engine
from app.dependencies import RoleChecker
from app.models import User
from app.rbac import exigir_acceso_empresa, registrar_auditoria

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/planificador", tags=["Planificador"])

require_view = RoleChecker(["planificador.view"])
require_edit = RoleChecker(["planificador.edit"])


def _verificar_acceso(campana_id: int, user: User) -> None:
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, user, campana_id=campana_id)


def _sin_migracion(e: pdatos.MigracionPendiente):
    return HTTPException(status_code=409, detail=str(e))


class PedidoRefuerzoCreateRequest(BaseModel):
    campana_id: Optional[int] = None
    pool_id: int
    dia: date
    desde: datetime
    hasta: datetime
    faltante_pico: int = Field(ge=0)
    horas_operador: float = Field(ge=0)
    accion: str = Field(max_length=20)
    nota: Optional[str] = Field(default=None, max_length=400)
    estado: Optional[str] = Field(default="pedido")


class PedidoRefuerzoUpdateRequest(BaseModel):
    estado: Optional[str] = None
    nota: Optional[str] = Field(default=None, max_length=400)
    horas_cubiertas: Optional[float] = Field(default=None, ge=0)


@router.get("/refuerzos/pedidos")
def listar_pedidos_refuerzo(
    campana_id: int = Query(...),
    desde: Optional[date] = None,
    hasta: Optional[date] = None,
    current_user: User = Depends(require_view),
):
    """Lista los pedidos de refuerzo de la campaña.

    Ejecuta el cierre perezoso de los pedidos vencidos (Dia < hoy) antes de leer.
    """
    _verificar_acceso(campana_id, current_user)
    try:
        # Cierre perezoso de pedidos anteriores a hoy
        try:
            pedidos_lib.cerrar_pedidos_vencidos(engine, campana_id)
        except Exception as e:
            logger.warning(f"No se pudo completar el cierre perezoso de pedidos: {e}")

        with engine.connect() as conn:
            filas = pedidos_lib.listar_pedidos(conn, campana_id, desde, hasta)
            return {"pedidos": filas}
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except Exception as e:
        logger.error(f"Error listando pedidos de refuerzo para campaña {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/refuerzos/pedidos")
def crear_pedido_refuerzo(
    payload: PedidoRefuerzoCreateRequest,
    campana_id: Optional[int] = Query(None),
    current_user: User = Depends(require_edit),
):
    """Registra un nuevo pedido de refuerzo enviado a RRHH (o descartado)."""
    cid = campana_id or payload.campana_id
    if cid is None:
        raise HTTPException(status_code=400, detail="Debe especificar campana_id.")
    _verificar_acceso(cid, current_user)

    if payload.hasta <= payload.desde:
        raise HTTPException(status_code=400, detail="El horario hasta debe ser posterior a desde.")

    estado = payload.estado or "pedido"
    if estado not in pedidos_lib.ESTADOS_VALIDOS:
        raise HTTPException(
            status_code=400,
            detail=f"Estado '{estado}' inválido. Válidos: {pedidos_lib.ESTADOS_VALIDOS}",
        )

    try:
        with engine.begin() as conn:
            usuario = getattr(current_user, "usuario", None)
            pid = pedidos_lib.crear_pedido(
                conn=conn,
                campana_id=cid,
                pool_id=payload.pool_id,
                dia=payload.dia,
                desde=payload.desde,
                hasta=payload.hasta,
                faltante_pico=payload.faltante_pico,
                horas_operador=payload.horas_operador,
                accion=payload.accion,
                nota=payload.nota,
                estado=estado,
                usuario=usuario,
            )
            registrar_auditoria(
                conn,
                usuario,
                "planificador.refuerzo_pedido_alta",
                "refuerzo_pedido",
                pid,
                payload.model_dump(mode="json"),
            )
        return {"ok": True, "pedido_id": pid}
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creando pedido de refuerzo para campaña {cid}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/refuerzos/pedidos/{pedido_id}")
def actualizar_pedido_refuerzo(
    pedido_id: int,
    payload: PedidoRefuerzoUpdateRequest,
    campana_id: int = Query(...),
    current_user: User = Depends(require_edit),
):
    """Actualiza estado ('descartado', 'cubierto', etc.), horas cubiertas o nota del pedido."""
    _verificar_acceso(campana_id, current_user)

    if payload.estado is not None and payload.estado not in pedidos_lib.ESTADOS_VALIDOS:
        raise HTTPException(
            status_code=400,
            detail=f"Estado '{payload.estado}' inválido. Válidos: {pedidos_lib.ESTADOS_VALIDOS}",
        )

    try:
        with engine.begin() as conn:
            usuario = getattr(current_user, "usuario", None)
            modificado = pedidos_lib.actualizar_pedido(
                conn=conn,
                pedido_id=pedido_id,
                campana_id=campana_id,
                estado=payload.estado,
                nota=payload.nota,
                horas_cubiertas=payload.horas_cubiertas,
                usuario=usuario,
            )
            if not modificado:
                raise HTTPException(status_code=404, detail="Pedido de refuerzo inexistente.")

            registrar_auditoria(
                conn,
                usuario,
                "planificador.refuerzo_pedido_edicion",
                "refuerzo_pedido",
                pedido_id,
                payload.model_dump(mode="json"),
            )
        return {"ok": True}
    except HTTPException:
        raise
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error actualizando pedido de refuerzo {pedido_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
