import json
import logging
from datetime import date, datetime, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import bindparam, text
from app.database import engine
from app.models import User, Role, RoleCreate, Permission, RoleDetail, RoleAssignment, RoleUser
from app.security import create_access_token, get_current_active_user
from app.rbac import (
    PERMISO_ROLES_IMPERSONATE,
    hay_ciclo,
    mapa_padres,
    permisos_efectivos_de_rol,
    permisos_faltantes,
    puede_gestionar_rol,
    puede_simular_rol,
    registrar_auditoria,
)

# Duración del token de simulación (corta a propósito: es una herramienta de
# verificación, no una sesión de trabajo)
SIMULACION_MINUTOS = 60

router = APIRouter(prefix="/roles", tags=["Roles Management"])
logger = logging.getLogger(__name__)


# Gestión de roles: super admin o delegados con roles:manage. Los delegados solo
# pueden tocar roles cuyos permisos efectivos sean subconjunto de los propios
# (lo valida cada endpoint con puede_gestionar_rol).
def roles_manage_required(user: User = Depends(get_current_active_user)):
    if not (user.is_super_admin or 'roles:manage' in user.permissions):
        raise HTTPException(status_code=403, detail="Se requiere permiso de gestión de roles.")
    return user


# Ver roles/usuarios-por-rol: también quienes asignan roles a usuarios o
# pueden simularlos (necesitan el listado para elegir qué simular).
def roles_view_required(user: User = Depends(get_current_active_user)):
    if not (user.is_super_admin or 'roles:manage' in user.permissions
            or 'users:create' in user.permissions
            or PERMISO_ROLES_IMPERSONATE in user.permissions):
        raise HTTPException(status_code=403, detail="No tienes permiso para ver los roles.")
    return user


# Ver el log de auditoría RBAC (altas/blanqueos/borrados de usuarios y cambios
# de roles): cualquiera de los permisos que puede generar filas en el log.
_PERMISOS_AUDIT_LOG = {'roles:manage', 'users:create', 'users:reset_password', 'users:delete'}


def rbac_audit_view_required(user: User = Depends(get_current_active_user)):
    if not (user.is_super_admin or _PERMISOS_AUDIT_LOG & set(user.permissions)):
        raise HTTPException(status_code=403, detail="No tienes permiso para ver el log de auditoría.")
    return user


def _cargar_rol(conn, role_id: int):
    row = conn.execute(
        text("SELECT id, name, description, is_super_admin, parent_role_id FROM [Acme].[pagina_web].[Roles] WHERE id = :rid"),
        {"rid": role_id}
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Rol no encontrado")
    return row


def _exigir_gestion(conn, user: User, role_row) -> None:
    """403 si el usuario no puede gestionar ese rol (regla de delegación)."""
    efectivos = permisos_efectivos_de_rol(conn, role_row["id"])
    if not puede_gestionar_rol(user, efectivos, bool(role_row["is_super_admin"])):
        raise HTTPException(
            status_code=403,
            detail="No puedes gestionar un rol con más privilegios que los tuyos."
        )


def _validar_permisos_solicitados(conn, user: User, permissions_ids: List[int]) -> List[str]:
    """Devuelve los codes de los permisos pedidos; 403 si un delegado pide
    permisos que no posee."""
    if not permissions_ids:
        return []
    rows = conn.execute(
        text("SELECT id, code FROM [Acme].[pagina_web].[Permissions] WHERE id IN :pids AND activo = 1")
        .bindparams(bindparam("pids", expanding=True)),
        {"pids": list(permissions_ids)}
    ).fetchall()
    encontrados = {row.id: row.code for row in rows}
    desconocidos = set(permissions_ids) - set(encontrados)
    if desconocidos:
        raise HTTPException(status_code=400, detail=f"Permisos inexistentes o inactivos: {sorted(desconocidos)}")
    if not user.is_super_admin:
        faltan = permisos_faltantes(set(encontrados.values()), set(user.permissions))
        if faltan:
            raise HTTPException(
                status_code=403,
                detail=f"No puedes asignar permisos que no posees: {sorted(faltan)}"
            )
    return list(encontrados.values())


def _validar_padre(conn, user: User, role_id, parent_role_id: int) -> None:
    """Valida un padre propuesto: existe, no es super admin, no genera ciclo y
    (para delegados) sus permisos efectivos son subconjunto de los propios.
    role_id es None al crear (no puede haber ciclo)."""
    padre = _cargar_rol(conn, parent_role_id)
    if padre["is_super_admin"]:
        raise HTTPException(status_code=400, detail="Un rol Super Admin no puede ser padre en la jerarquía.")
    if role_id is not None and hay_ciclo(role_id, parent_role_id, mapa_padres(conn)):
        raise HTTPException(status_code=400, detail="Ese padre generaría un ciclo en la jerarquía de roles.")
    if not user.is_super_admin:
        # El hijo hereda los permisos del padre: colgarse de un padre más
        # poderoso equivale a asignarse esos permisos.
        faltan = permisos_faltantes(permisos_efectivos_de_rol(conn, parent_role_id), set(user.permissions))
        if faltan:
            raise HTTPException(
                status_code=403,
                detail=f"No puedes usar ese rol padre: heredaría permisos que no posees ({sorted(faltan)})."
            )


@router.get("/permissions", response_model=List[Permission])
def list_permissions(current_user: User = Depends(roles_manage_required)):
    """Catálogo de permisos asignables. Los delegados solo ven los que ellos
    poseen (no pueden asignar otros, así que no tiene sentido mostrarlos).

    Los inactivos (activo = 0) no se listan: son los template:<empresa> de
    empresas dadas de baja, no otorgan nada y asignarlos no tendría efecto."""
    with engine.connect() as conn:
        result = conn.execute(text("SELECT id, code, description FROM [Acme].[pagina_web].[Permissions] WHERE activo = 1 ORDER BY code"))
        permisos = [Permission(id=row.id, code=row.code, description=row.description) for row in result]
    if not current_user.is_super_admin:
        propios = set(current_user.permissions)
        permisos = [p for p in permisos if p.code in propios]
    return permisos


@router.get("/list", response_model=List[Role])
def list_roles(current_user: User = Depends(roles_view_required)):
    """Lista los roles con padre, cantidad de usuarios y si el usuario actual
    puede editarlos (delegación)."""
    with engine.connect() as conn:
        roles_result = conn.execute(text("""
            SELECT r.id, r.name, r.description, r.is_super_admin, r.parent_role_id,
                   pr.name AS parent_name,
                   (SELECT COUNT(*) FROM [Acme].[pagina_web].[UserRoles] ur WHERE ur.role_id = r.id) AS users_count
            FROM [Acme].[pagina_web].[Roles] r
            LEFT JOIN [Acme].[pagina_web].[Roles] pr ON pr.id = r.parent_role_id
            ORDER BY r.name
        """)).fetchall()

        # Permisos efectivos por rol (para calcular editable de delegados).
        # Espejo de permisos_efectivos_de_rol(): los inactivos no cuentan.
        efectivos_rows = conn.execute(text("""
            SELECT rep.role_id, p.code
            FROM [Acme].[pagina_web].[RoleEffectivePermissions] rep
            JOIN [Acme].[pagina_web].[Permissions] p ON p.id = rep.permission_id
            WHERE p.activo = 1
        """)).fetchall()

    efectivos_por_rol: dict[int, set] = {}
    for row in efectivos_rows:
        efectivos_por_rol.setdefault(row.role_id, set()).add(row.code)

    return [
        Role(
            id=row.id,
            name=row.name,
            description=row.description,
            is_super_admin=bool(row.is_super_admin),
            parent_role_id=row.parent_role_id,
            parent_name=row.parent_name,
            users_count=row.users_count,
            editable=puede_gestionar_rol(current_user, efectivos_por_rol.get(row.id, set()), bool(row.is_super_admin)),
            simulable=puede_simular_rol(current_user, efectivos_por_rol.get(row.id, set()), bool(row.is_super_admin)),
        )
        for row in roles_result
    ]


@router.post("/create", status_code=201)
def create_role(role_data: RoleCreate, current_user: User = Depends(roles_manage_required)):
    """Crea un rol con permisos y padre opcional. Un delegado solo puede usar
    permisos (y padres) que él mismo posea."""
    logger.info(f"Usuario {current_user.usuario} creando rol {role_data.name}")
    try:
        with engine.begin() as conn:
            codes = _validar_permisos_solicitados(conn, current_user, role_data.permissions_ids)
            if role_data.parent_role_id is not None:
                _validar_padre(conn, current_user, None, role_data.parent_role_id)

            result = conn.execute(
                text("""INSERT INTO [Acme].[pagina_web].[Roles] (name, description, parent_role_id)
                        OUTPUT INSERTED.id VALUES (:name, :desc, :parent)"""),
                {"name": role_data.name, "desc": role_data.description, "parent": role_data.parent_role_id}
            )
            new_role_id = result.scalar()

            if role_data.permissions_ids:
                values = [{"rid": new_role_id, "pid": pid} for pid in role_data.permissions_ids]
                conn.execute(
                    text("INSERT INTO [Acme].[pagina_web].[RolePermissions] (role_id, permission_id) VALUES (:rid, :pid)"),
                    values
                )

            registrar_auditoria(conn, current_user.usuario, "role_create", "role", new_role_id, {
                "name": role_data.name,
                "parent_role_id": role_data.parent_role_id,
                "permissions": sorted(codes),
            })
        return {"message": f"Rol '{role_data.name}' creado exitosamente.", "role_id": new_role_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creando rol: {e}")
        raise HTTPException(status_code=500, detail=f"Error al crear el rol: {str(e)}")


@router.post("/assign_user/{documento}")
def assign_roles_to_user(documento: int, assignment: RoleAssignment, current_user: User = Depends(get_current_active_user)):
    """Reemplaza el set completo de roles de un usuario (multi-rol).
    SEGURIDAD: no se pueden asignar roles con permisos (efectivos, heredados
    incluidos) que el asignador no posee."""
    if not current_user.is_super_admin and 'users:create' not in current_user.permissions:
        raise HTTPException(status_code=403, detail="No tienes permiso para asignar roles.")

    try:
        with engine.begin() as conn:
            for role_id in assignment.role_ids:
                rol = _cargar_rol(conn, role_id)

                if rol["is_super_admin"] and not current_user.is_super_admin:
                    raise HTTPException(status_code=403, detail="Solo un Super Admin puede asignar el rol de Super Admin.")

                if not current_user.is_super_admin:
                    faltan = permisos_faltantes(permisos_efectivos_de_rol(conn, role_id), set(current_user.permissions))
                    if faltan:
                        logger.warning(f"Usuario {current_user.usuario} intentó asignar rol {role_id} con permisos que no posee: {sorted(faltan)}")
                        raise HTTPException(
                            status_code=403,
                            detail=f"No puedes asignar un rol que tiene más privilegios que tú (Faltan: {sorted(faltan)})."
                        )

            conn.execute(text("DELETE FROM [Acme].[pagina_web].[UserRoles] WHERE nomina_id = :nid"), {"nid": documento})
            if assignment.role_ids:
                conn.execute(
                    text("INSERT INTO [Acme].[pagina_web].[UserRoles] (nomina_id, role_id) VALUES (:nid, :rid)"),
                    [{"nid": documento, "rid": rid} for rid in set(assignment.role_ids)]
                )

            registrar_auditoria(conn, current_user.usuario, "user_roles_set", "user", documento, {
                "role_ids": sorted(set(assignment.role_ids)),
            })

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error asignando roles: {e}")
        raise HTTPException(status_code=500, detail=f"Error en base de datos: {str(e)}")

    return {"message": "Roles asignados correctamente."}


@router.post("/{role_id}/impersonate")
def impersonate_role(role_id: int, current_user: User = Depends(get_current_active_user)):
    """Emite un token de simulación: mismas credenciales del usuario pero con
    los permisos EFECTIVOS del rol (claim imp_role, revalidado por el backend
    en cada request). Permite verificar qué ve/puede un rol antes de asignarlo.
    SEGURIDAD: solo roles no super admin cuyos permisos sean subconjunto de los
    del simulador (o super admin simulando cualquier rol no super admin)."""
    if not (current_user.is_super_admin or PERMISO_ROLES_IMPERSONATE in current_user.permissions):
        raise HTTPException(status_code=403, detail="No tienes permiso para simular roles.")
    if current_user.simulando_rol_id is not None:
        raise HTTPException(status_code=409, detail="Ya estás simulando un rol: salí de la simulación primero.")

    with engine.begin() as conn:
        rol = _cargar_rol(conn, role_id)
        efectivos = permisos_efectivos_de_rol(conn, role_id)

        if not puede_simular_rol(current_user, efectivos, bool(rol["is_super_admin"])):
            raise HTTPException(
                status_code=403,
                detail="No puedes simular ese rol (es super admin o tiene permisos que no posees)."
            )

        registrar_auditoria(conn, current_user.usuario, "role_impersonate", "role", role_id, {
            "name": rol["name"],
        })

    expira = timedelta(minutes=SIMULACION_MINUTOS)
    token = create_access_token(
        data={
            "sub": str(current_user.usuario),
            "nombre": current_user.Nombre,
            "campana": current_user.campana,
            "imp_role": role_id,
        },
        expires_delta=expira,
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": (datetime.now() + expira).isoformat(),
        "role_id": role_id,
        "role_name": rol["name"],
        "permissions": sorted(efectivos),
        "is_super_admin": False,
    }


# --- Log de auditoría RBAC (pagina_web.RbacAuditLog) -------------------------
# Registra altas/blanqueos/borrados de usuarios (users.py) y creación/edición/
# borrado de roles y asignaciones (este router), vía registrar_auditoria().
# created_at usa SYSDATETIME() (hora local del servidor, ya en horario
# Argentina — verificado 2026-07-21) así que se sirve tal cual, sin el ajuste
# de UTC que necesitan otras tablas (ver AuditExecutionLog/_con_tz_utc).
#
# OJO: esta ruta estática DEBE declararse ANTES de las rutas paramétricas
# `/{role_id}` (get/put/delete). Si va después, FastAPI matchea /roles/audit_log
# contra /roles/{role_id} e intenta parsear "audit_log" como int → 422.

_AUDIT_LOG_ORDEN = {
    "fecha": "created_at",
    "action": "action",
    "entity_type": "entity_type",
    "actor": "actor_documento",
}


def _resolver_nombres_documentos(conn, docs: set) -> dict:
    """documento -> 'Nombre Apellido'. Cubre nómina (chatbot.vw_nomina, la misma
    vista maestra que usan uso_ia/chatbot_sql_service) Y los usuarios externos
    (pagina_web.Usuarios_extra), que NO están en la vista (verificado 2026-07-21:
    los 4 Usuarios_extra caen fuera de vw_nomina). Best-effort: si algo falla,
    esos documentos quedan sin nombre y el frontend muestra el número."""
    if not docs:
        return {}
    try:
        docs_int = [int(d) for d in docs]
    except (TypeError, ValueError):
        return {}
    try:
        q = text("""
            SELECT documento, LTRIM(RTRIM(nombre + ' ' + apellido)) AS nombre_completo
            FROM chatbot.vw_nomina WHERE documento IN :ids
            UNION
            SELECT documento, LTRIM(RTRIM(nombre + ' ' + apellido)) AS nombre_completo
            FROM [Acme].[pagina_web].[Usuarios_extra] WHERE documento IN :ids
        """).bindparams(bindparam("ids", expanding=True))
        return {str(doc): nombre for doc, nombre in conn.execute(q, {"ids": docs_int}).fetchall()}
    except Exception as e:
        logger.warning(f"No se pudieron resolver nombres de documentos para el audit log: {e}")
        return {}


def _resolver_nombres_roles(conn, role_ids: set) -> dict:
    """role_id -> nombre del rol (pagina_web.Roles). Best-effort."""
    if not role_ids:
        return {}
    try:
        ids = [int(x) for x in role_ids]
    except (TypeError, ValueError):
        return {}
    try:
        q = text("SELECT id, name FROM [Acme].[pagina_web].[Roles] WHERE id IN :ids") \
            .bindparams(bindparam("ids", expanding=True))
        return {int(rid): name for rid, name in conn.execute(q, {"ids": ids}).fetchall()}
    except Exception as e:
        logger.warning(f"No se pudieron resolver nombres de roles para el audit log: {e}")
        return {}


def _enriquecer_audit_log(conn, rows: list) -> None:
    """Parsea el detail JSON y resuelve nombres legibles: actor (documento ->
    nombre), entidad (rol -> nombre del rol / usuario -> nombre de la persona)
    y los roles referenciados dentro del detail (user_roles_set guarda role_ids).
    Todo best-effort: si una resolución falla, la fila queda con el número."""
    for r in rows:
        if r.get("detail"):
            try:
                r["detail"] = json.loads(r["detail"])
            except (TypeError, ValueError):
                pass

    # Documentos a resolver: el actor de TODA fila + la entidad cuando es usuario.
    docs = {str(r["actor_documento"]) for r in rows if r.get("actor_documento") is not None}
    role_ids = set()
    for r in rows:
        ent_id = r.get("entity_id")
        if ent_id is None:
            continue
        if r.get("entity_type") == "user":
            docs.add(str(ent_id))
        elif r.get("entity_type") == "role":
            role_ids.add(ent_id)
    # Roles referenciados dentro del detail (asignación de roles a un usuario).
    for r in rows:
        det = r.get("detail")
        if isinstance(det, dict) and isinstance(det.get("role_ids"), list):
            role_ids.update(det["role_ids"])

    nombres_doc = _resolver_nombres_documentos(conn, docs)
    nombres_rol = _resolver_nombres_roles(conn, role_ids)

    for r in rows:
        r["actor_nombre"] = nombres_doc.get(str(r.get("actor_documento")))
        ent_id = r.get("entity_id")
        if ent_id is None:
            r["entity_nombre"] = None
        elif r.get("entity_type") == "role":
            r["entity_nombre"] = nombres_rol.get(int(ent_id))
        elif r.get("entity_type") == "user":
            r["entity_nombre"] = nombres_doc.get(str(ent_id))
        else:
            r["entity_nombre"] = None
        # Nombres de los roles asignados (user_roles_set), para no mostrar IDs sueltos.
        det = r.get("detail")
        if isinstance(det, dict) and isinstance(det.get("role_ids"), list):
            det["roles_nombres"] = [nombres_rol.get(int(x), f"#{x}") for x in det["role_ids"]]


@router.get("/audit_log")
def listar_audit_log(
    action: Optional[str] = None,
    entity_type: Optional[str] = Query(None, description="role | user"),
    actor: Optional[int] = Query(None, description="Documento del admin que ejecutó la acción."),
    entity_id: Optional[int] = None,
    fecha_desde: Optional[date] = None,
    fecha_hasta: Optional[date] = None,
    orden: str = Query("fecha", pattern="^(fecha|action|entity_type|actor)$"),
    dir: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(rbac_audit_view_required),
):
    """Lista pagina_web.RbacAuditLog paginado/filtrable: quién hizo qué (alta,
    blanqueo, borrado de usuario; alta/edición/borrado de rol; asignación de
    roles) y cuándo. Pensado sobre todo para controlar los blanqueos de
    contraseña, que ahora quedan siempre registrados."""
    filtros = []
    params: dict = {"limit": limit, "offset": offset}

    if action:
        filtros.append("action = :action")
        params["action"] = action
    if entity_type:
        filtros.append("entity_type = :entity_type")
        params["entity_type"] = entity_type
    if actor is not None:
        filtros.append("actor_documento = :actor")
        params["actor"] = actor
    if entity_id is not None:
        filtros.append("entity_id = :entity_id")
        params["entity_id"] = entity_id
    if fecha_desde:
        filtros.append("created_at >= :fecha_desde")
        params["fecha_desde"] = fecha_desde
    if fecha_hasta:
        filtros.append("created_at < DATEADD(DAY, 1, :fecha_hasta)")
        params["fecha_hasta"] = fecha_hasta

    where_clause = f"WHERE {' AND '.join(filtros)}" if filtros else ""
    orden_sql = f"{_AUDIT_LOG_ORDEN[orden]} {'ASC' if dir == 'asc' else 'DESC'}"

    query = text(f"""
        SELECT id, actor_documento, action, entity_type, entity_id, detail, created_at
        FROM [Acme].[pagina_web].[RbacAuditLog]
        {where_clause}
        ORDER BY {orden_sql}
        OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
    """)
    count_query = text(f"SELECT COUNT(*) FROM [Acme].[pagina_web].[RbacAuditLog] {where_clause}")

    try:
        with engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(query, params).mappings().all()]
            total = conn.execute(count_query, params).scalar()
            _enriquecer_audit_log(conn, rows)
    except Exception as e:
        logger.error(f"Error listando audit log RBAC: {e}")
        raise HTTPException(status_code=500, detail="Error al listar el log de auditoría.")

    return {"total": total, "data": rows}


@router.get("/{role_id}/users", response_model=List[RoleUser])
def list_role_users(role_id: int, current_user: User = Depends(roles_view_required)):
    """Lista los usuarios que tienen asignado un rol."""
    with engine.connect() as conn:
        _cargar_rol(conn, role_id)
        rows = conn.execute(text("""
            SELECT n.documento, n.nombre + ' ' + n.apellido AS nombre, c.campana
            FROM [Acme].[pagina_web].[UserRoles] ur
            JOIN nomina n ON n.documento = ur.nomina_id
            LEFT JOIN operadores o ON n.id = o.legajo_id AND o.fecha_hasta IS NULL AND o.estado = 1
            LEFT JOIN campanas c ON c.id = o.campana_id
            WHERE ur.role_id = :rid
            UNION
            SELECT ux.documento, ux.nombre + ' ' + ux.apellido AS nombre, ux.campana
            FROM [Acme].[pagina_web].[UserRoles] ur
            JOIN [Acme].[pagina_web].[Usuarios_extra] ux ON ux.documento = ur.nomina_id
            WHERE ur.role_id = :rid
            ORDER BY nombre
        """), {"rid": role_id}).fetchall()

    return [RoleUser(documento=row.documento, nombre=row.nombre, campana=row.campana) for row in rows]


@router.get("/{role_id}", response_model=RoleDetail)
def get_role_details(role_id: int, current_user: User = Depends(roles_manage_required)):
    """Detalle de un rol para edición: permisos propios y heredados por separado."""
    with engine.connect() as conn:
        role_row = _cargar_rol(conn, role_id)
        _exigir_gestion(conn, current_user, role_row)

        # Solo permisos activos: los inactivos no aparecen en el catálogo, así
        # que devolverlos los mostraría como casillas fantasma. Sus filas en
        # RolePermissions quedan igual (update_role no las toca).
        propios = set(conn.execute(
            text("""SELECT rp.permission_id
                    FROM [Acme].[pagina_web].[RolePermissions] rp
                    JOIN [Acme].[pagina_web].[Permissions] p ON p.id = rp.permission_id
                    WHERE rp.role_id = :rid AND p.activo = 1"""),
            {"rid": role_id}
        ).scalars().all())

        efectivos = set(conn.execute(
            text("""SELECT rep.permission_id
                    FROM [Acme].[pagina_web].[RoleEffectivePermissions] rep
                    JOIN [Acme].[pagina_web].[Permissions] p ON p.id = rep.permission_id
                    WHERE rep.role_id = :rid AND p.activo = 1"""),
            {"rid": role_id}
        ).scalars().all())

        return RoleDetail(
            id=role_row["id"],
            name=role_row["name"],
            description=role_row["description"],
            is_super_admin=bool(role_row["is_super_admin"]),
            parent_role_id=role_row["parent_role_id"],
            permissions_ids=sorted(propios),
            inherited_permissions_ids=sorted(efectivos - propios),
        )


@router.put("/{role_id}")
def update_role(role_id: int, role_data: RoleCreate, current_user: User = Depends(roles_manage_required)):
    """Actualiza nombre, descripción, padre y permisos de un rol."""
    try:
        with engine.begin() as conn:
            role_row = _cargar_rol(conn, role_id)
            _exigir_gestion(conn, current_user, role_row)

            codes = _validar_permisos_solicitados(conn, current_user, role_data.permissions_ids)

            if role_data.parent_role_id is not None:
                if role_row["is_super_admin"]:
                    raise HTTPException(status_code=400, detail="Un rol Super Admin no participa de la jerarquía.")
                _validar_padre(conn, current_user, role_id, role_data.parent_role_id)

            permisos_antes = permisos_efectivos_de_rol(conn, role_id)

            conn.execute(
                text("""UPDATE [Acme].[pagina_web].[Roles]
                        SET name = :name, description = :desc, parent_role_id = :parent WHERE id = :rid"""),
                {"name": role_data.name, "desc": role_data.description,
                 "parent": role_data.parent_role_id, "rid": role_id}
            )

            # Permisos propios: borrar los activos y re-insertar los seleccionados.
            # Las asignaciones a permisos INACTIVOS se conservan: no se muestran
            # en el formulario, así que nunca vienen en permissions_ids y
            # borrarlas sería una pérdida silenciosa (reactivar la empresa no
            # devolvería el template:<empresa> a este rol).
            conn.execute(text("""
                DELETE rp
                FROM [Acme].[pagina_web].[RolePermissions] rp
                JOIN [Acme].[pagina_web].[Permissions] p ON p.id = rp.permission_id
                WHERE rp.role_id = :rid AND p.activo = 1
            """), {"rid": role_id})
            if role_data.permissions_ids:
                values = [{"rid": role_id, "pid": pid} for pid in role_data.permissions_ids]
                conn.execute(
                    text("INSERT INTO [Acme].[pagina_web].[RolePermissions] (role_id, permission_id) VALUES (:rid, :pid)"),
                    values
                )

            registrar_auditoria(conn, current_user.usuario, "role_update", "role", role_id, {
                "name": role_data.name,
                "parent_role_id": role_data.parent_role_id,
                "permisos_efectivos_antes": sorted(permisos_antes),
                "permisos_propios_despues": sorted(codes),
            })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error actualizando rol {role_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al actualizar: {str(e)}")

    return {"message": "Rol actualizado correctamente"}


@router.delete("/{role_id}")
def delete_role(role_id: int, current_user: User = Depends(roles_manage_required)):
    """Elimina un rol sin usuarios asignados ni roles hijos."""
    try:
        with engine.begin() as conn:
            role_row = _cargar_rol(conn, role_id)
            _exigir_gestion(conn, current_user, role_row)

            usuarios = conn.execute(
                text("SELECT COUNT(*) FROM [Acme].[pagina_web].[UserRoles] WHERE role_id = :rid"),
                {"rid": role_id}
            ).scalar()
            if usuarios:
                raise HTTPException(status_code=409, detail=f"El rol tiene {usuarios} usuario(s) asignado(s). Reasignalos antes de borrarlo.")

            hijos = conn.execute(
                text("SELECT COUNT(*) FROM [Acme].[pagina_web].[Roles] WHERE parent_role_id = :rid"),
                {"rid": role_id}
            ).scalar()
            if hijos:
                raise HTTPException(status_code=409, detail=f"El rol tiene {hijos} rol(es) hijo(s). Cambiales el padre antes de borrarlo.")

            conn.execute(text("DELETE FROM [Acme].[pagina_web].[RolePermissions] WHERE role_id = :rid"), {"rid": role_id})
            conn.execute(text("DELETE FROM [Acme].[pagina_web].[Roles] WHERE id = :rid"), {"rid": role_id})

            registrar_auditoria(conn, current_user.usuario, "role_delete", "role", role_id, {
                "name": role_row["name"],
            })
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error eliminando rol {role_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al eliminar: {str(e)}")

    return {"message": f"Rol '{role_row['name']}' eliminado."}
