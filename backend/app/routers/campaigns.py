import logging
import pandas as pd
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.database import engine
from app.dependencies import RoleChecker
from app.models import CampaignListResponse, EmpresaCampanaItem, User
from app.rbac import empresas_permitidas
from app.security import get_current_active_user

router = APIRouter()
logger = logging.getLogger(__name__)

# DEPRECADO: el selector del chatbot ahora usa GET /chatbots/disponibles (los
# bots viven en pagina_web.Chatbots). Este endpoint y su lista fija se eliminan
# en la fase de limpieza, cuando ningún cliente viejo lo consuma.
def get_active_campaigns() -> List[str]:
    return ['Voltara','paygo','benefix','vantix','csv no premium','csv premium','csv commercial','csv denuncias','csv isla de marcas','csv vip','csv bancentro','csv pto a pto','csv tokenizacion']

@router.get("/campaigns/", response_model=CampaignListResponse, tags=["Campaigns"], deprecated=True)
def list_campaigns(current_user: User = Depends(get_current_active_user)):
    logger.info(f"User {current_user.usuario} requesting campaign list.")
    return CampaignListResponse(campaigns=get_active_campaigns())

@router.get("/lista_empresa_campana/", response_model=List[EmpresaCampanaItem], tags=["Helper"],
            dependencies=[Depends(RoleChecker(["audit:execute"]))])
def lista_empresa_campana(current_user: User = Depends(get_current_active_user)):
    """Mapa Empresa/Campaña/Tipificación (mundo Mitrol) para la página Auditar.

    Alcance por empresa best-effort: se ocultan las filas cuyo nombre Mitrol
    mapea (vía calidad.Empresa_nombreMitrol) a una empresa FUERA del alcance
    del usuario. Los nombres sin mapeo quedan visibles: el mapa no cubre todas
    las empresas y ocultarlos rompería CSV/Voltara/ALARMIX.
    """
    logger.info(f"User {current_user.usuario} requesting empresa/campaña list.")
    try:
        if not engine:
             raise HTTPException(status_code=503, detail="Database service is unavailable.")

        query = """
            SELECT DISTINCT Empresa, Campaña, Tipificación as Tipificacion
            FROM [Acme].[dbo].[detalle_de_interacciones_por_campana_lote]
            WHERE fecha_inicio > DATEADD(Day, -10, GETDATE())
            AND Empresa IS NOT NULL AND Campaña IS NOT NULL
            ORDER BY Empresa, Campaña, Tipificación;
        """
        df_empresa_campana = pd.read_sql(text(query), engine)
        result = df_empresa_campana.to_dict('records')

        with engine.connect() as conn:
            permitidas = empresas_permitidas(conn, current_user)
            if permitidas is not None:
                mapa = conn.execute(text(
                    "SELECT nombre_mitrol, EmpresaID FROM [Acme].[calidad].[Empresa_nombreMitrol]"
                )).fetchall()
                empresas_por_nombre: dict = {}
                for row in mapa:
                    empresas_por_nombre.setdefault(row.nombre_mitrol.strip().lower(), set()).add(row.EmpresaID)
                # Prohibido solo si NINGUNA de sus empresas mapeadas está permitida
                prohibidos = {
                    nombre for nombre, ids in empresas_por_nombre.items()
                    if not (ids & permitidas)
                }
                result = [
                    r for r in result
                    if str(r.get("Empresa", "")).strip().lower() not in prohibidos
                ]

        logger.info(f"Successfully fetched {len(result)} empresa/campaña/Tipificacion pairs for user {current_user.usuario}.")
        return result

    except SQLAlchemyError as e:
        logger.error(f"Database error fetching empresa/campaña list for user {current_user.usuario}: {e}")
        raise HTTPException(status_code=500, detail="Database error retrieving company/campaign list")
    except Exception as e:
        logger.error(f"Unexpected error fetching empresa/campaña list for user {current_user.usuario}: {e}")
        raise HTTPException(status_code=500, detail="Could not retrieve company/campaign list")
