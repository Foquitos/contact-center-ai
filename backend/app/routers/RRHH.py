from app.dependencies import RoleChecker
from fastapi import APIRouter, Form, HTTPException, Depends
from app.security import get_current_active_user
from app.models import User, AceptarCandidatoRequest
import logging

# Importamos el nuevo servicio
from app.services import RRHH_instance # (Ajusta la ruta de importación según tu estructura)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/RRHH"
    ,dependencies=[Depends(RoleChecker(["rrhh:analyze"]))]
    )


@router.get("/listar_candidatos",
            tags=["RRHH"],
            summary="Lee los candidatos directamente desde Google Sheets",
            response_description="Un JSON con todos los candidatos activos")
def listar_candidatos(
    current_user: User = Depends(get_current_active_user)
):
    try:
        # Traemos todos los candidatos (el filtrado de eliminados ya se hace en el servicio)
        candidatos = RRHH_instance.obtener_candidatos_activos()
        return candidatos
    except Exception as e:
        logger.error(f"Error al listar candidatos desde Sheets: {e}")
        raise HTTPException(status_code=500, detail="Error al conectar con Google Sheets.")

@router.delete("/candidato/{dni}", tags=["RRHH"])
def delete_candidate(
    dni: str,
    current_user: User = Depends(get_current_active_user)
):
    """
    Desactiva un candidato actualizando su estado a 'ELIMINADO' en Google Sheets.
    """
    try:
        RRHH_instance.cambiar_estado_candidato(dni, "ELIMINADO")
        logger.info(f"Usuario {current_user.usuario} eliminó al candidato con DNI {dni}")
        return {"message": "Candidato eliminado correctamente"}
    except Exception as e:
        logger.error(f"Error al eliminar candidato DNI {dni}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/candidato/{dni}/rechazar_negocio", tags=["RRHH"])
def reject_candidate_business(
    dni: str,
    negocio: str = Form(...),
    current_user: User = Depends(get_current_active_user)
):
    """
    Actualiza el estado del candidato en Google Sheets indicando de qué negocio fue rechazado.
    """
    try:
        estado_rechazo = f"RECHAZADO - {negocio.upper()}"
        RRHH_instance.cambiar_estado_candidato(dni, estado_rechazo)
        logger.info(f"Usuario {current_user.usuario} rechazó al DNI {dni} para {negocio}")
        return {"message": f"Candidato marcado como {estado_rechazo}"}
    except Exception as e:
        logger.error(f"Error rechazando candidato DNI {dni}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/aceptar_candidato_completo", tags=["RRHH"])
def aceptar_candidato_completo(
    payload: AceptarCandidatoRequest,
    current_user: User = Depends(get_current_active_user)
):
    datos_entrevista = {
        "fecha": payload.fecha,
        "hora": payload.hora,
        "lugar": payload.lugar,
        "campana": payload.campana_asignada
    }

    # 1. ÚNICAMENTE PONEMOS ACEPTADO EN LA BD (Y banderas en 0)
    RRHH_instance.aceptar_candidato(payload.dni, payload.correo_candidato, datos_entrevista)
    
    return {
        "message": "Candidato guardado como ACEPTADO. El envío de correos y la sincronización se procesarán en breve."
    }