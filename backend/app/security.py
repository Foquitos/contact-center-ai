import logging
import time
from typing import Optional, List
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.database import engine
from app.models import User
from app.config import settings

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)
    return encoded_jwt

def get_user(documento: int) -> Optional[User]:
    # Consulta híbrida: Trae datos básicos + campaña (Legacy) + Roles/Permisos (Nuevo RBAC)
    # Los permisos inactivos (Permissions.activo = 0, p. ej. el template:<empresa>
    # de una empresa dada de baja) no se otorgan: la fila en RolePermissions se
    # conserva para que reactivar la empresa los devuelva sin reasignar nada.
    query = text("""
        SELECT 
            n.documento AS usuario,
            n.nombre + ' ' + n.apellido AS Nombre,
            p.[password],
            p.[must_change_password],
            c.campana,
            r.name as role_name,
            r.is_super_admin,
            perm.code as permission_code
        FROM nomina n WITH (NOLOCK)
        LEFT JOIN [Acme].[pagina_web].[passwords] p WITH (NOLOCK) ON n.id = p.nomina_id
        -- Joins para Campaña (Sistema anterior)
        LEFT JOIN operadores o WITH (NOLOCK) ON n.id = o.legajo_id AND o.fecha_hasta IS NULL AND o.estado = 1
        LEFT JOIN campanas c WITH (NOLOCK) ON c.id = o.campana_id
        -- Joins para RBAC (permisos efectivos: propios + heredados de roles padre)
        LEFT JOIN [Acme].[pagina_web].[UserRoles] ur WITH (NOLOCK) ON n.id = ur.nomina_id
        LEFT JOIN [Acme].[pagina_web].[Roles] r WITH (NOLOCK) ON ur.role_id = r.id
        LEFT JOIN [Acme].[pagina_web].[RoleEffectivePermissions] rep WITH (NOLOCK) ON r.id = rep.role_id
        LEFT JOIN [Acme].[pagina_web].[Permissions] perm WITH (NOLOCK) ON rep.permission_id = perm.id AND perm.activo = 1
        where n.documento = :documento
        union
        SELECT 
            n.documento AS usuario,
            n.nombre + ' ' + n.apellido AS Nombre,
            p.[password],
            p.[must_change_password],
            n.campana,
            r.name as role_name,
            r.is_super_admin,
            perm.code as permission_code
        FROM pagina_web.Usuarios_extra n WITH (NOLOCK)
        LEFT JOIN [Acme].[pagina_web].[passwords] p WITH (NOLOCK) ON n.documento = p.nomina_id
        -- Joins para RBAC (permisos efectivos: propios + heredados de roles padre)
        LEFT JOIN [Acme].[pagina_web].[UserRoles] ur WITH (NOLOCK) ON n.documento = ur.nomina_id
        LEFT JOIN [Acme].[pagina_web].[Roles] r WITH (NOLOCK) ON ur.role_id = r.id
        LEFT JOIN [Acme].[pagina_web].[RoleEffectivePermissions] rep WITH (NOLOCK) ON r.id = rep.role_id
        LEFT JOIN [Acme].[pagina_web].[Permissions] perm WITH (NOLOCK) ON rep.permission_id = perm.id AND perm.activo = 1
        where n.documento = :documento
    """)
    
    if engine is None:
        logger.error("Database engine not available.")
        return None

    results = None
    for intento in range(3):
        try:
            with engine.connect() as connection:
                results = connection.execute(query, {"documento": documento}).fetchall()
            break
        except SQLAlchemyError as e:
            err_msg = str(e)
            if ("1205" in err_msg or "40001" in err_msg) and intento < 2:
                logger.warning(
                    f"Deadlock en SQL Server al consultar usuario {documento}, reintentando ({intento + 1}/3)..."
                )
                time.sleep(0.05 * (intento + 1))
                continue
            logger.error(f"Database error getting user {documento}: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting user {documento}: {e}")
            return None

    if not results:
        logger.debug(f"User {documento} not found.")
        return None

    # Procesar resultados (agrupar filas porque un usuario puede tener múltiples permisos)
    base_row = results[0]
    roles_set = set()
    permissions_set = set()
    is_super_admin = False

    for row in results:
        if row.role_name:
            roles_set.add(row.role_name)
        if row.is_super_admin:
            is_super_admin = True
        if row.permission_code:
            permissions_set.add(row.permission_code)

    user_model = User(
        usuario=base_row.usuario,
        Nombre=base_row.Nombre,
        password=base_row.password,
        campana=base_row.campana,
        roles=list(roles_set),
        permissions=list(permissions_set),
        is_super_admin=is_super_admin,
        must_change_password=bool(base_row.must_change_password),
    )
    return user_model

def authenticate_user(username: int, password: str) -> Optional[User]:
    user = get_user(username)
    if not user:
        return None
    if not user.password:
        return None
    if not verify_password(password, user.password):
        return None
    return user

def _aplicar_simulacion(user: User, role_id: int) -> User:
    """Convierte al usuario real en su versión simulada: permisos efectivos del
    rol, is_super_admin=False. Revalida EN CADA REQUEST que el actor siga
    autorizado (si perdió roles:impersonate o el rol cambió y dejó de ser
    subconjunto, la simulación muere con 403)."""
    # Import local: security se importa desde rbac indirectamente vía routers;
    # evita cualquier ciclo en el arranque.
    from app.rbac import permisos_efectivos_de_rol, puede_simular_rol

    with engine.connect() as conn:
        rol = conn.execute(
            text("SELECT name, is_super_admin FROM [Acme].[pagina_web].[Roles] WITH (NOLOCK) WHERE id = :rid"),
            {"rid": role_id},
        ).mappings().first()
        if rol is None:
            raise HTTPException(status_code=403, detail="El rol simulado ya no existe. Salí de la simulación.")

        efectivos = permisos_efectivos_de_rol(conn, role_id)

    if not puede_simular_rol(user, efectivos, bool(rol["is_super_admin"])):
        raise HTTPException(
            status_code=403,
            detail="Ya no estás autorizado a simular ese rol. Salí de la simulación.",
        )

    return user.model_copy(update={
        "permissions": sorted(efectivos),
        "roles": [rol["name"]],
        "is_super_admin": False,
        "simulando_rol_id": role_id,
        "simulando_rol": rol["name"],
    })


async def get_current_active_user(token: str = Depends(oauth2_scheme)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        username_str: Optional[str] = payload.get("sub")
        if username_str is None:
            raise credentials_exception
        username_int = int(username_str)
    except (JWTError, ValueError):
        raise credentials_exception

    user = get_user(username_int)
    if user is None:
        raise credentials_exception

    # Token de simulación de rol (emitido por POST /roles/{id}/impersonate)
    imp_role = payload.get("imp_role")
    if imp_role is not None:
        user = _aplicar_simulacion(user, int(imp_role))

    return user