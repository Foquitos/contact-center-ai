import logging
from typing import Optional
from app.dependencies import RoleChecker
from fastapi import APIRouter, Depends, Form, HTTPException
from sqlalchemy import text
from app.database import engine
from app.models import InformacionCSVResponse, Message, SeccionesCSVResponse, User
from app.security import get_current_active_user
from informacion_csv import _informacion_csv as InformacionCSV, _secciones_csv as SeccionesCSV, _refresh_csv as RefreshCSV

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/informacion_csv", tags=["Informacion CSV"], response_model=InformacionCSVResponse,dependencies=[Depends(RoleChecker(["csv:read"]))])
def informacion_csv(
    seccion_1: str = Form(...),
    seccion_2: Optional[str] = Form(None),
    seccion_3: Optional[str] = Form(None),
    seccion_4: Optional[str] = Form(None),
    user_id: int = Form(...)
):
    info = InformacionCSV(seccion_1=seccion_1, seccion_2=seccion_2, seccion_3=seccion_3, seccion_4=seccion_4)

    query = text("""
        INSERT INTO pagina_web.Informador_csv_logs
        (Documento, seccion_1, seccion_2, seccion_3, seccion_4)
        VALUES
        (:user_id, :s1, :s2, :s3, :s4)
    """)
    params = {"user_id": user_id, "s1": seccion_1, "s2": seccion_2, "s3": seccion_3, "s4": seccion_4}

    if engine is None:
        logger.error("Database engine not available.")
        raise HTTPException(status_code=503, detail="Database service is unavailable.")
    with engine.connect() as conn:
        conn.execute(query, params)
        conn.commit()

    return InformacionCSVResponse(texto=info)

@router.get("/secciones_csv", tags=["Informacion CSV"], response_model=SeccionesCSVResponse, dependencies=[Depends(RoleChecker(["csv:read"]))])
def secciones_csv():
    return SeccionesCSVResponse(secciones=SeccionesCSV())

@router.get("/refresh_sheets", tags=["Informacion CSV"], response_model=Message, dependencies=[Depends(RoleChecker(["csv:read"]))])
def refresh_sheets():
    RefreshCSV()
    return {"message": "Sheets refreshed successfully"}
