# app/dependencies.py
from fastapi import Depends, HTTPException, status
from app.security import get_current_active_user
from app.models import User

class RoleChecker:
    def __init__(self, required_permissions: list[str]):
        self.required_permissions = required_permissions

    def __call__(self, user: User = Depends(get_current_active_user)):
        # 1. Si es Super Admin, pasa siempre
        if user.is_super_admin:
            return user
            
        # 2. Verificar si tiene TODOS los permisos requeridos (o uno de ellos, depende de tu lógica)
        # Aquí implemento lógica: Debe tener AL MENOS UNO de los permisos requeridos para entrar
        has_permission = any(perm in user.permissions for perm in self.required_permissions)
        
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"No tienes permisos suficientes. Se requiere: {self.required_permissions}"
            )
        return user


# Permisos con prefijo chatbot: que NO habilitan por sí solos el chatbot RAG:
# chatbot:sql es el Asistente de Datos (otro flujo). chatbot:admin es el panel,
# pero SÍ da acceso de uso (calidad necesita probar los bots). El legacy
# chatbot:selectcampaign se eliminó de la BD (migración 2026-07-10).
CHATBOT_META_PERMS = {"chatbot:sql"}


def require_chatbot_user(user: User = Depends(get_current_active_user)) -> User:
    """Acceso a los endpoints de uso del chatbot RAG: super admin, chatbot:admin,
    o algún permiso de bot concreto (chatbot:<slug> / chatbot:csv)."""
    if user.is_super_admin or "chatbot:admin" in user.permissions:
        return user
    if any(
        p.startswith("chatbot:") and p not in CHATBOT_META_PERMS and p != "chatbot:admin"
        for p in user.permissions
    ):
        return user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="No tenés acceso a ningún chatbot.",
    )