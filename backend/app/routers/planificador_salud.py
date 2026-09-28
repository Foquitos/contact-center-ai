"""Planificador — Salud del plan: frescura de los datos, migraciones pendientes y diagnóstico.

La lógica vive en app/planificador_salud.py; acá van permisos y armado de la respuesta,
igual que en routers/planificador.py.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app import planificador_datos as pdatos
from app import planificador_salud as salud
from app.database import engine
from app.dependencies import RoleChecker
from app.models import User
from app.rbac import exigir_acceso_empresa

router = APIRouter(prefix="/planificador", tags=["Planificador"])

require_view = RoleChecker(["planificador.view"])


def _verificar_acceso(campana_id: int, user: User) -> None:
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, user, campana_id=campana_id)


def _sin_migracion(e: pdatos.MigracionPendiente):
    return HTTPException(status_code=409, detail=str(e))


@router.get("/salud")
def obtener_salud(campana_id: int = Query(..., description="ID de la campaña"),
                  current_user: User = Depends(require_view)):
    """Diagnóstico de salud del planificador: frescura de fuentes y estado de migraciones."""
    _verificar_acceso(campana_id, current_user)
    try:
        return salud.diagnostico_salud(engine, campana_id)
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
