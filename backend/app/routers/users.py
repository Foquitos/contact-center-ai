import logging
from typing import Optional
from fastapi import APIRouter, Depends, Form, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.database import engine
from app.models import Message, User, UserDetail, BulkUserCreate
from app.rbac import permisos_efectivos_de_rol, permisos_faltantes, registrar_auditoria
from app.security import get_current_active_user, get_password_hash, get_user as get_user_security, verify_password

router = APIRouter()
logger = logging.getLogger(__name__)

_MIN_PASSWORD_LENGTH = 8


def _validate_password_strength(password: str) -> str:
    if len(password) < _MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"La contraseña debe tener al menos {_MIN_PASSWORD_LENGTH} caracteres."
        )
    return password


# --- Dependencias de permisos --------------------------------------------
# Antes todo (alta, edición de roles, cambio de clave y borrado) dependía de
# un único permiso (users:create). Blanquear una clave y borrar el acceso de
# alguien son acciones más sensibles que dar de alta un usuario o tocarle los
# roles, así que tienen su propio permiso (migración
# 2026-07-21_users_permisos_reset_delete.sql, que además se lo otorga a todo
# rol que ya tuviera users:create para no romper nada el día del deploy).

def users_create_required(user: User = Depends(get_current_active_user)) -> User:
    if not (user.is_super_admin or 'users:create' in user.permissions):
        raise HTTPException(status_code=403, detail="No tienes permiso para crear o editar usuarios.")
    return user


def users_reset_password_required(user: User = Depends(get_current_active_user)) -> User:
    if not (user.is_super_admin or 'users:reset_password' in user.permissions):
        raise HTTPException(status_code=403, detail="No tienes permiso para blanquear contraseñas.")
    return user


def users_delete_required(user: User = Depends(get_current_active_user)) -> User:
    if not (user.is_super_admin or 'users:delete' in user.permissions):
        raise HTTPException(status_code=403, detail="No tienes permiso para eliminar usuarios.")
    return user


def users_bulkcreate_required(user: User = Depends(get_current_active_user)) -> User:
    if not (user.is_super_admin or 'users:bulkcreate' in user.permissions):
        raise HTTPException(status_code=403, detail="No tienes permiso para el alta masiva de usuarios.")
    return user


_GESTION_USUARIOS = {'users:create', 'users:reset_password', 'users:delete'}


def users_view_required(user: User = Depends(get_current_active_user)) -> User:
    """Ver el listado/detalle de cuentas: alcanza con cualquiera de los tres
    permisos de gestión de usuarios (para poder buscar a quién editar/blanquear/
    borrar sin necesitar los tres a la vez)."""
    if not (user.is_super_admin or _GESTION_USUARIOS & set(user.permissions)):
        raise HTTPException(status_code=403, detail="No tienes permiso para ver usuarios.")
    return user


# Columnas ordenables del listado de cuentas (lista blanca, nunca se interpola
# el valor del querystring directo).
_USERS_ORDEN = {
    "documento": "c.documento",
    "nombre": "c.nombre",
}

_CUENTAS_CTE = """
    WITH cuentas AS (
        SELECT n.id AS internal_id, n.documento AS documento,
               n.nombre + ' ' + n.apellido AS nombre,
               camp.campana AS campana, p.must_change_password AS must_change_password
        FROM nomina n
        JOIN [Acme].[pagina_web].[passwords] p ON p.nomina_id = n.id
        LEFT JOIN operadores o ON n.id = o.legajo_id AND o.fecha_hasta IS NULL AND o.estado = 1
        LEFT JOIN campanas camp ON camp.id = o.campana_id
        UNION ALL
        SELECT n.documento AS internal_id, n.documento AS documento,
               n.nombre + ' ' + n.apellido AS nombre,
               n.campana AS campana, p.must_change_password AS must_change_password
        FROM [Acme].[pagina_web].[Usuarios_extra] n
        JOIN [Acme].[pagina_web].[passwords] p ON p.nomina_id = n.documento
    )
"""


@router.get("/admin/users/", tags=["User Management (Admin)"])
def list_users(
    q: Optional[str] = Query(None, max_length=100, description="Documento exacto o parte del nombre."),
    orden: str = Query("documento", pattern="^(documento|nombre)$"),
    dir: str = Query("asc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(users_view_required),
):
    """Lista las cuentas del sitio (documento, nombre, campaña, roles y si
    tienen un blanqueo pendiente por confirmar), buscable y paginada. Antes no
    existía forma de ver todos los usuarios: había que conocer el documento
    exacto y buscarlo uno por uno."""
    filtros = []
    params: dict = {"limit": limit, "offset": offset}

    if q:
        q = q.strip()
        if q.isdigit():
            filtros.append("c.documento = :q_doc")
            params["q_doc"] = int(q)
        elif q:
            filtros.append("c.nombre LIKE :q_nombre")
            params["q_nombre"] = f"%{q}%"

    where_clause = f"WHERE {' AND '.join(filtros)}" if filtros else ""
    orden_sql = f"{_USERS_ORDEN[orden]} {'DESC' if dir == 'desc' else 'ASC'}"

    query = text(f"""
        {_CUENTAS_CTE}
        SELECT c.documento, c.nombre, c.campana, c.must_change_password,
               STRING_AGG(r.name, ', ') WITHIN GROUP (ORDER BY r.name) AS roles
        FROM cuentas c
        LEFT JOIN [Acme].[pagina_web].[UserRoles] ur ON ur.nomina_id = c.documento
        LEFT JOIN [Acme].[pagina_web].[Roles] r ON r.id = ur.role_id
        {where_clause}
        GROUP BY c.documento, c.nombre, c.campana, c.must_change_password
        ORDER BY {orden_sql}
        OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
    """)
    count_query = text(f"{_CUENTAS_CTE} SELECT COUNT(*) FROM cuentas c {where_clause}")

    try:
        with engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(query, params).mappings().all()]
            total = conn.execute(count_query, params).scalar()
        return {"total": total, "data": rows}
    except SQLAlchemyError as e:
        logger.exception(f"Error listando usuarios: {e}")
        raise HTTPException(status_code=500, detail="Error al listar usuarios.")


@router.get("/admin/users/{documento}", response_model=UserDetail, tags=["User Management (Admin)"])
def get_user_details(documento: int, current_user: User = Depends(users_view_required)):
    """Obtiene detalles de un usuario para edición, con TODOS sus roles (multi-rol)."""
    with engine.connect() as conn:
        # UserRoles.nomina_id guarda el documento (en nomina id == documento)
        query = text("""
            SELECT
                n.documento as usuario,
                n.nombre + ' ' + n.apellido as Nombre,
                c.campana,
                ur.role_id
            FROM nomina n
            LEFT JOIN operadores o ON n.id = o.legajo_id AND o.fecha_hasta IS NULL AND o.estado = 1
            LEFT JOIN campanas c ON c.id = o.campana_id
            LEFT JOIN [Acme].[pagina_web].[UserRoles] ur ON n.documento = ur.nomina_id
            WHERE n.documento = :doc
            union
            select
                n.documento as usuario,
                n.nombre + ' ' + n.apellido as Nombre,
                n.campana,
                ur.role_id
            FROM pagina_web.Usuarios_extra n
            LEFT JOIN [Acme].[pagina_web].[UserRoles] ur ON n.documento = ur.nomina_id
            WHERE n.documento = :doc
        """)
        rows = conn.execute(query, {"doc": documento}).mappings().all()

        if not rows:
             raise HTTPException(status_code=404, detail="Usuario no encontrado")

        base = rows[0]
        return UserDetail(
            usuario=base["usuario"],
            Nombre=base["Nombre"],
            campana=base["campana"],
            role_ids=sorted({row["role_id"] for row in rows if row["role_id"] is not None}),
        )


@router.post("/admin/users/{documento}/reset_password", response_model=Message, tags=["User Management (Admin)"])
def admin_reset_password(
    documento: int,
    current_user: User = Depends(users_reset_password_required),
):
    """Blanquea la contraseña de un usuario: NO se puede elegir una clave a
    mano, siempre queda igual al documento y se fuerza a cambiarla en el
    próximo ingreso (must_change_password=1). Es un blanqueo, no un cambio de
    clave: el admin nunca ve ni define la contraseña resultante."""
    target_user = get_user_security(documento)
    if not target_user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    if target_user.password is None:
        raise HTTPException(
            status_code=400,
            detail="Este usuario todavía no tiene una cuenta creada. Usá 'Crear Usuario'."
        )
    if target_user.is_super_admin and not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="No puedes modificar a un Super Admin.")

    hashed_password = get_password_hash(str(documento))

    try:
        with engine.begin() as conn:
            result = conn.execute(
                text("""
                    UPDATE [Acme].[pagina_web].[passwords]
                    SET [password] = :pwd, [must_change_password] = 1
                    WHERE nomina_id = :doc
                """),
                {"pwd": hashed_password, "doc": documento}
            )
            if result.rowcount == 0:
                raise HTTPException(
                    status_code=500,
                    detail="No se encontró la cuenta a blanquear (inconsistencia de datos)."
                )
            registrar_auditoria(conn, current_user.usuario, "user_password_reset", "user", documento)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error blanqueando password de {documento}: {e}")
        raise HTTPException(status_code=500, detail="Error al blanquear la contraseña.")

    return {"message": "Contraseña blanqueada: quedó igual al documento y se pedirá cambiarla en el próximo ingreso."}


@router.post("/admin/create_user/", response_model=Message, tags=["User Management (Admin)"])
def create_user(
    documento: int = Form(...),
    current_user: User = Depends(users_create_required),
):
    """Alta de usuario: la contraseña inicial siempre es el documento (DNI),
    el admin no la elige (misma regla que Alta Masiva). Se fuerza cambio en el
    próximo ingreso vía el DEFAULT de must_change_password."""
    logger.info(f"Admin {current_user.usuario} attempting to create user {documento}.")

    user = get_user_security(documento)
    if not user:
        logger.warning(f"User creation failed: User {documento} not found in nomina.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"User with document {documento} not found in the system.")

    if user.password is not None:
        logger.warning(f"User creation failed: User {documento} already has a password set.")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already has a password set.")

    hashed_new_password = get_password_hash(str(documento))

    try:
        if engine is None:
            logger.error("Database engine not available, cannot create user.")
            raise HTTPException(status_code=503, detail="Database service is unavailable.")
        with engine.connect() as connection:
            with connection.begin():
                insert_password_query = text("""
                    INSERT INTO [Acme].[pagina_web].[passwords] ([nomina_id], [password])
                    VALUES (:documento, :hashed_password)
                """)
                connection.execute(insert_password_query, {"documento": documento, "hashed_password": hashed_new_password})
                registrar_auditoria(connection, current_user.usuario, "user_create", "user", documento, {"origen": "individual"})
                logger.info(f"User {documento} created successfully.")
            return {"message": "Usuario creado. La contraseña inicial es el documento; deberá cambiarla en el próximo ingreso."}

    except HTTPException:
        raise
    except SQLAlchemyError as e:
        logger.exception(f"Database SQLAlchemyError during user creation for {documento}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error during user creation.")
    except Exception as e:
        logger.exception(f"An unexpected error occurred during user creation for {documento}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred.")


@router.delete("/admin/delete_user/{documento}", response_model=Message, tags=["User Management (Admin)"])
def delete_user(
    documento: int,
    current_user: User = Depends(users_delete_required),
):
    logger.info(f"Admin {current_user.usuario} attempting to delete password and roles records for user {documento}.")
    try:
        if engine is None:
            logger.error("Database engine not available, cannot delete user.")
            raise HTTPException(status_code=503, detail="Database service is unavailable.")
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(
                    text("DELETE FROM [Acme].[pagina_web].[UserRoles] WHERE nomina_id = :documento"),
                    {"documento": documento}
                )

                # Tabla legacy previa al RBAC; se limpia por si quedan filas viejas
                delete_roles_query = text("""
                    DELETE FROM [Acme].[pagina_web].[chatbot_roles]
                    WHERE nomina_id = :documento
                """)
                connection.execute(delete_roles_query, {"documento": documento})

                delete_password_query = text("""
                    DELETE FROM [Acme].[pagina_web].[passwords]
                    WHERE nomina_id = :documento
                """)
                password_delete_result = connection.execute(delete_password_query, {"documento": documento})

                registrar_auditoria(connection, current_user.usuario, "user_delete", "user", documento)

            if password_delete_result.rowcount == 0:
                 return {"message": f"User records processed. Password or roles may not have existed for {documento}."}
            else:
                return {"message": f"User password and roles records deleted successfully for {documento}."}

    except SQLAlchemyError as e:
        logger.exception(f"Database error during user record deletion for {documento}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error during user record deletion.")
    except Exception as e:
        logger.exception(f"An unexpected error occurred during user record deletion for {documento}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred.")


@router.post("/update_password/", response_model=Message, tags=["User Management"])
def update_password(
    documento: int = Form(...),
    current_password: str = Form(...),
    new_password: str = Form(...)
):
    logger.info(f"Password update attempt for user {documento}.")
    user = get_user_security(documento)

    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"User with document {documento} not found.")

    if user.password is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User does not have a password set. Use the /admin/create_user endpoint to set the initial password."
        )

    if not verify_password(current_password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect current password"
        )

    # En el cambio forzado el usuario ingresa con su contraseña vieja; no tiene
    # sentido (y sería un no-op inseguro) que la "nueva" sea la misma.
    if new_password == current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La nueva contraseña debe ser distinta a la actual."
        )

    _validate_password_strength(new_password)
    hashed_new_password = get_password_hash(new_password)

    try:
        if engine is None:
            logger.error("Database engine not available, cannot update password.")
            raise HTTPException(status_code=503, detail="Database service is unavailable.")
        with engine.begin() as connection:
            # El usuario cambió su propia contraseña => se apaga el flag de
            # cambio forzado.
            update_query = text("""
                UPDATE [Acme].[pagina_web].[passwords]
                SET [password] = :hashed_password,
                    [must_change_password] = 0
                WHERE nomina_id = :documento
            """)
            result = connection.execute(update_query, {"hashed_password": hashed_new_password, "documento": documento})

            if result.rowcount == 0:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Password record not found for update (unexpected).")

        return {"message": "Password changed successfully"}

    except SQLAlchemyError as e:
        logger.exception(f"Database SQLAlchemyError during password update for {documento}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Database error during password update.")
    except Exception as e:
        logger.exception(f"An unexpected error occurred during password update for {documento}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An unexpected error occurred.")


@router.post("/admin/users/bulk_create", tags=["User Management (Admin)"])
def bulk_create_users(
    payload: BulkUserCreate,
    current_user: User = Depends(users_bulkcreate_required),
):
    """
    Alta masiva de usuarios.
    - Alta nueva: contraseña inicial = documento + rol seleccionado. Queda
      logueada individualmente (user_create) además del resumen agregado.
    - Usuario existente (ya tiene contraseña): NO se le toca la contraseña,
      solo se le reemplaza el rol; sin rol seleccionado queda sin cambios.
    """
    results = {"created": 0, "updated": 0, "skipped": 0, "failed": 0, "errors": []}

    # 1. Validación de Seguridad para Rol (Escalada de privilegios)
    if payload.role_id:
        with engine.connect() as conn:
            is_target_super = conn.execute(
                text("SELECT is_super_admin FROM [Acme].[pagina_web].[Roles] WHERE id = :rid"),
                {"rid": payload.role_id}
            ).scalar()

            if is_target_super and not current_user.is_super_admin:
                raise HTTPException(status_code=403, detail="Solo un Super Admin puede asignar el rol de Super Admin masivamente.")

            # Anti-escalada (igual que en assign_user): permisos EFECTIVOS del rol,
            # heredados de roles padre incluidos
            if not current_user.is_super_admin:
                faltan = permisos_faltantes(
                    permisos_efectivos_de_rol(conn, payload.role_id),
                    set(current_user.permissions)
                )
                if faltan:
                    raise HTTPException(status_code=403, detail=f"No puedes asignar un rol con permisos que no posees ({sorted(faltan)}).")

    # 2. Procesamiento Masivo
    with engine.begin() as conn: # Una sola transacción para todo el bloque (o por lotes)
        for doc in payload.documents:
            try:
                # A. Verificar si existe en nomina
                nomina_id = conn.execute(text("SELECT id FROM nomina WHERE documento = :doc"), {"doc": doc}).scalar()

                if not nomina_id:
                    results["failed"] += 1
                    results["errors"].append(f"{doc}: No existe en nómina.")
                    continue

                # B. ¿Ya es usuario de la página? (usuario = fila en passwords)
                exists_pwd = conn.execute(text("SELECT 1 FROM [Acme].[pagina_web].[passwords] WHERE nomina_id = :nid"), {"nid": nomina_id}).scalar()

                if not exists_pwd:
                    # Alta nueva: contraseña inicial = documento
                    conn.execute(
                        text("INSERT INTO [Acme].[pagina_web].[passwords] (nomina_id, password) VALUES (:nid, :pwd)"),
                        {"nid": nomina_id, "pwd": get_password_hash(str(doc))}
                    )
                    registrar_auditoria(conn, current_user.usuario, "user_create", "user", doc, {"origen": "bulk"})
                elif not payload.role_id:
                    # Existente y sin rol seleccionado: nada que hacer (la
                    # contraseña NUNCA se resetea desde el alta masiva)
                    results["skipped"] += 1
                    continue

                # C. Asignar Rol (si se seleccionó uno)
                if payload.role_id:
                    conn.execute(text("DELETE FROM [Acme].[pagina_web].[UserRoles] WHERE nomina_id = :nid"), {"nid": nomina_id})
                    conn.execute(
                        text("INSERT INTO [Acme].[pagina_web].[UserRoles] (nomina_id, role_id) VALUES (:nid, :rid)"),
                        {"nid": nomina_id, "rid": payload.role_id}
                    )

                results["updated" if exists_pwd else "created"] += 1

            except Exception as e:
                logger.error(f"Error procesando usuario {doc} en bulk: {e}")
                results["failed"] += 1
                results["errors"].append(f"{doc}: Error interno.")

        registrar_auditoria(conn, current_user.usuario, "bulk_role_assign", "role", payload.role_id, {
            "documentos": len(payload.documents),
            "creados": results["created"],
            "solo_rol": results["updated"],
            "sin_cambios": results["skipped"],
            "fallos": results["failed"],
        })

    return results
