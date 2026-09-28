import logging
from typing import Optional
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from app.models import Token, User
from app.security import authenticate_user, create_access_token, get_current_active_user
from app.config import settings
from app.session_log import (
    registrar_evento_autonomo,
    actualizar_last_seen,
    extraer_ip,
    extraer_user_agent,
)

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/token/", response_model=Token, tags=["Authentication"])
def login_for_access_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    client_ip: Optional[str] = Form(None),
    client_user_agent: Optional[str] = Form(None),
):
    logger.info(f"Login attempt for username: {form_data.username}")
    # IP/UA reales del cliente. El frontend los manda en el cuerpo del form
    # (a prueba de proxies que descartan headers custom); si no vinieran, se cae
    # a los headers (X-Forwarded-For / X-Client-User-Agent). Ver session_log.
    ip = client_ip or extraer_ip(request)
    ua = client_user_agent or extraer_user_agent(request)
    try:
        username_int = int(form_data.username)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid input: Username must be an integer."
        )

    user = authenticate_user(username_int, form_data.password)
    if not user:
        # Intento fallido (usuario inexistente o contraseña incorrecta): queda
        # registrado para el análisis de seguridad.
        registrar_evento_autonomo(username_int, "login_failed", ip, ua)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    registrar_evento_autonomo(user.usuario, "login", ip, ua)

    access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
    
    # En el token JWT guardamos info mínima
    token_payload = {
        "sub": str(user.usuario),
        "nombre": user.Nombre,
        "campana": user.campana
    }
    
    access_token = create_access_token(data=token_payload, expires_delta=access_token_expires)
    expires_at = datetime.now() + access_token_expires

    # CORRECCIÓN: Mapeo de respuesta al nuevo modelo Token
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_display_name": user.Nombre if user.Nombre else str(user.usuario),
        "permissions": user.permissions,  # Lista de strings
        "is_super_admin": user.is_super_admin, # Booleano
        "campana": user.campana,
        "expires_at": expires_at.isoformat(),
        "must_change_password": user.must_change_password,
    }


@router.get("/me", tags=["Authentication"])
def read_me(user: User = Depends(get_current_active_user)):
    """Permisos vigentes del usuario del token. El frontend lo usa para
    refrescar la sesión sin re-login cuando cambian roles/permisos."""
    # Heartbeat de presencia: el frontend llama a /me cada ~5 min mientras el
    # usuario navega, así que sirve para saber quién está conectado ahora.
    # Silencioso: nunca debe hacer fallar /me.
    actualizar_last_seen(user.usuario)
    return {
        "usuario": user.usuario,
        "user_display_name": user.Nombre if user.Nombre else str(user.usuario),
        "permissions": user.permissions,
        "is_super_admin": user.is_super_admin,
        "campana": user.campana,
        "must_change_password": user.must_change_password,
        # Si el token es de simulación, los permissions de arriba ya son los
        # del rol simulado
        "simulando_rol_id": user.simulando_rol_id,
        "simulando_rol": user.simulando_rol,
    }