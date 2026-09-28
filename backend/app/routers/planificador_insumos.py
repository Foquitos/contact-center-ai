"""Planificador — Insumos editables: reparto por skill, eventos, ajustes, avisos de
corte de Hidra y seguimiento del día.

La lógica vive en app/planificador_insumos.py; acá van permisos y armado de la respuesta,
igual que en routers/planificador.py.
"""

import logging
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app import planificador_cortes as pcortes
from app import planificador_datos as pdatos
from app import planificador_insumos as pinsumos
from app.database import engine
from app.dependencies import RoleChecker
from app.models import User
from app.rbac import exigir_acceso_empresa, registrar_auditoria

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/planificador", tags=["Planificador"])

require_view = RoleChecker(["planificador.view"])
require_edit = RoleChecker(["planificador.edit"])


class TramoAsignacionItem(BaseModel):
    skill_id: Optional[int] = None
    vigente_desde: date
    porcentaje: float = Field(ge=0.0, le=1.0)
    nota: Optional[str] = Field(default=None, max_length=400)


class GuardarAsignacionRequest(BaseModel):
    campana_id: int
    tramos: List[TramoAsignacionItem]


def _verificar_acceso(campana_id: int, user: User) -> None:
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, user, campana_id=campana_id)


def _sin_migracion(e: pdatos.MigracionPendiente):
    return HTTPException(status_code=409, detail=str(e))


@router.get("/asignacion")
def obtener_asignacion(campana_id: int = Query(...),
                       current_user: User = Depends(require_view)):
    """Tramos de asignación por skill: vigente hoy, últimos 10 de historia y skills sin tramo."""
    _verificar_acceso(campana_id, current_user)
    try:
        with engine.connect() as conn:
            return pinsumos.obtener_asignacion(conn, campana_id)
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error consultando asignación de la campaña {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/asignacion")
def guardar_asignacion(payload: GuardarAsignacionRequest,
                       current_user: User = Depends(require_edit)):
    """Carga tramos nuevos de asignación cerrando los vigentes el día anterior.

    En una sola transacción: no reescribe historia y exige porcentaje entre 0 y 1.
    La campaña viaja sólo en el cuerpo: con dos lugares posibles, el permiso se
    podía verificar contra una y escribir en la otra.
    """
    cid = payload.campana_id
    _verificar_acceso(cid, current_user)
    try:
        with engine.begin() as conn:
            tramos_dict = [t.model_dump(mode="json") for t in payload.tramos]
            guardados = pinsumos.guardar_asignacion_tramos(
                conn, cid, tramos_dict, getattr(current_user, "usuario", None)
            )
            registrar_auditoria(
                conn, getattr(current_user, "usuario", None),
                "planificador.asignacion_edicion", "asignacion", cid,
                payload.model_dump(mode="json")
            )
        return {"ok": True, "guardados": guardados}
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error guardando asignación de la campaña {cid}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ------------------------------------------------------- avisos de corte de Hidra

class AplicarAvisoRequest(BaseModel):
    # Todo opcional: sin nada se aplica la propuesta tal cual.
    factor: Optional[float] = Field(default=None, gt=0, le=10)
    desde: Optional[datetime] = None
    hasta: Optional[datetime] = None


@router.get("/avisos-corte")
def listar_avisos_corte(campana_id: int = Query(...), dias: int = Query(30, ge=1, le=400),
                        current_user: User = Depends(require_view)):
    """Avisos de corte leídos de la web de Hidra (o cargados de un mail), con la
    propuesta de ajuste. `aplica` = False si la campaña no tiene avisos: la
    pantalla no muestra la tarjeta."""
    _verificar_acceso(campana_id, current_user)
    if campana_id not in pcortes.CAMPANAS_CON_AVISOS:
        return {"aplica": False, "avisos": []}
    try:
        with engine.connect() as conn:
            if not pcortes.hay_tabla(conn):
                return {"aplica": True, "sin_tabla": True, "avisos": [],
                        "detalle": "Falta la migración 2026-09-24b_planificador_avisos_corte.sql"}
            medidas = pcortes.llamadas_medidas(conn, campana_id)
            return {
                "aplica": True,
                "avisos": pcortes.avisos(conn, campana_id, date.today() - timedelta(days=dias)),
                "estimacion": {e: dict(zip(("llamadas", "origen"),
                                           pcortes.llamadas_esperadas(e, medidas)))
                               for e in pcortes.ESCALAS},
            }
    except Exception as e:
        logger.error(f"Error listando avisos de corte de la campaña {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/avisos-corte/{aviso_id}/aplicar")
def aplicar_aviso_corte(aviso_id: int, payload: AplicarAvisoRequest,
                        campana_id: int = Query(...),
                        current_user: User = Depends(require_edit)):
    """Convierte la propuesta en un ajuste del pronóstico (y en un evento si el
    día deja de ser normal). Se ve en el próximo recálculo."""
    _verificar_acceso(campana_id, current_user)
    usuario = getattr(current_user, "usuario", None)
    try:
        with engine.begin() as conn:
            r = pcortes.aplicar(conn, campana_id, aviso_id, usuario, payload.factor,
                                payload.desde, payload.hasta)
            registrar_auditoria(conn, usuario, "planificador.aviso_corte_aplicado",
                                "aviso_corte", aviso_id,
                                {**payload.model_dump(mode="json"), **r})
        return r
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error(f"Error aplicando el aviso de corte {aviso_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/avisos-corte/{aviso_id}/descartar")
def descartar_aviso_corte(aviso_id: int, campana_id: int = Query(...),
                          current_user: User = Depends(require_edit)):
    _verificar_acceso(campana_id, current_user)
    usuario = getattr(current_user, "usuario", None)
    try:
        with engine.begin() as conn:
            pcortes.descartar(conn, campana_id, aviso_id, usuario)
            registrar_auditoria(conn, usuario, "planificador.aviso_corte_descartado",
                                "aviso_corte", aviso_id, None)
        return {"ok": True}
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error descartando el aviso de corte {aviso_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
