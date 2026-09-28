"""Planificador — Escenarios: qué pasaría con la dotación si cambia un supuesto.

La lógica vive en app/planificador_escenarios.py; acá van permisos, alcance por
empresa y armado de la respuesta, igual que en routers/planificador.py.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import planificador_datos as pdatos
from app import planificador_escenarios as pesc
from app.database import engine
from app.dependencies import RoleChecker
from app.models import User
from app.rbac import exigir_acceso_empresa

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/planificador", tags=["Planificador"])

require_view = RoleChecker(["planificador.view"])


class CambiosEscenarioRequest(BaseModel):
    factor_volumen: Optional[float] = None
    factor_tmo: Optional[float] = None
    max_ocupacion: Optional[float] = None
    umbral_seg: Optional[int] = None
    objetivo_nds: Optional[float] = None
    shrinkage: Optional[float] = None
    break_min_por_hora: Optional[float] = None


class EscenarioRequest(BaseModel):
    campana_id: int
    desde: Optional[date] = None
    hasta: Optional[date] = None
    cambios: Optional[CambiosEscenarioRequest] = Field(default_factory=CambiosEscenarioRequest)


def _verificar_acceso(campana_id: int, user: User) -> None:
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, user, campana_id=campana_id)


def _sin_migracion(e: pdatos.MigracionPendiente) -> HTTPException:
    return HTTPException(status_code=409, detail=str(e))


@router.post("/escenario")
def simular_escenario(req: EscenarioRequest,
                      current_user: User = Depends(require_view)) -> Dict[str, Any]:
    """Calcula la dotación con supuestos alternativos sin alterar el plan vigente."""
    _verificar_acceso(req.campana_id, current_user)
    cambios_dict = req.cambios.model_dump() if req.cambios else {}
    try:
        return pesc.calcular_escenario(
            engine,
            campana_id=req.campana_id,
            cambios=cambios_dict,
            desde=req.desde,
            hasta=req.hasta,
        )
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except ValueError as e:
        msg = str(e)
        if "no está dada de alta" in msg or "No hay una corrida vigente" in msg:
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    except Exception as e:
        logger.error(f"Error calculando escenario para campaña {req.campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
