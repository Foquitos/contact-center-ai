"""Lógica compartida de RBAC: anti-escalada, jerarquía de roles y auditoría.

La jerarquía es hijo-hereda-del-padre: el padre es un rol base y los permisos
efectivos de un rol son los propios más los de todos sus ancestros. En BD la
resuelve la vista pagina_web.RoleEffectivePermissions; acá viven las versiones
puras (testeables sin BD) y los helpers que consultan la vista.
"""
import json
import logging
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import bindparam, text

from app.models import User

logger = logging.getLogger(__name__)

VISTA_EFECTIVOS = "[Acme].[pagina_web].[RoleEffectivePermissions]"


# ------------------------------------------------------------ funciones puras

def permisos_faltantes(permisos_rol: set[str], permisos_usuario: set[str]) -> set[str]:
    """Permisos del rol que el usuario NO posee (vacío = puede asignarlo/gestionarlo)."""
    return set(permisos_rol) - set(permisos_usuario)


def hay_ciclo(role_id: int, nuevo_padre_id: Optional[int], padres: dict[int, Optional[int]]) -> bool:
    """True si poner nuevo_padre_id como padre de role_id crearía un ciclo.

    `padres` es el mapa {role_id: parent_role_id} actual de la BD. Camina los
    ancestros del padre propuesto; si aparece role_id (incluye auto-padre) hay
    ciclo. Corta también ante ciclos preexistentes en el mapa.
    """
    actual = nuevo_padre_id
    visitados = set()
    while actual is not None:
        if actual == role_id:
            return True
        if actual in visitados:
            return True
        visitados.add(actual)
        actual = padres.get(actual)
    return False


def resolver_permisos_efectivos(
    role_ids: list[int],
    padres: dict[int, Optional[int]],
    permisos_por_rol: dict[int, set[str]],
) -> set[str]:
    """Versión pura de la vista RoleEffectivePermissions: unión de los permisos
    propios y de todos los ancestros de cada rol de la lista."""
    efectivos: set[str] = set()
    for rid in role_ids:
        actual: Optional[int] = rid
        visitados = set()
        while actual is not None and actual not in visitados:
            visitados.add(actual)
            efectivos |= permisos_por_rol.get(actual, set())
            actual = padres.get(actual)
    return efectivos


def resolver_grupos_efectivos(
    role_ids: list[int],
    padres: dict[int, Optional[int]],
    grupos_por_rol: dict[int, set[int]],
) -> set[int]:
    """Resuelve los grupos de tips aplicables a una lista de roles.

    Un grupo de tips asignado a un rol R aplica a R y a todos sus descendientes
    (hijos). Por lo tanto, para cada rol del usuario, se recorren sus ancestros:
    si el rol o cualquiera de sus ancestros tiene asignado el grupo, se incluye.
    """
    grupos: set[int] = set()
    for rid in role_ids:
        actual: Optional[int] = rid
        visitados = set()
        while actual is not None and actual not in visitados:
            visitados.add(actual)
            grupos |= grupos_por_rol.get(actual, set())
            actual = padres.get(actual)
    return grupos


def puede_gestionar_rol(user: User, permisos_efectivos_rol: set[str], rol_es_super_admin: bool) -> bool:
    """Regla de delegación: super admin gestiona todo; un delegado solo roles no
    super admin cuyos permisos efectivos sean subconjunto de los suyos."""
    if user.is_super_admin:
        return True
    if rol_es_super_admin:
        return False
    return not permisos_faltantes(permisos_efectivos_rol, set(user.permissions))


PERMISO_ROLES_IMPERSONATE = "roles:impersonate"


def puede_simular_rol(user: User, permisos_efectivos_rol: set[str], rol_es_super_admin: bool) -> bool:
    """Simulación de roles: nadie simula un rol super admin (ni el super admin:
    no hay nada que previsualizar, ve todo). El super admin simula cualquier
    otro rol; un usuario con roles:impersonate solo roles cuyos permisos
    efectivos sean subconjunto de los suyos (la simulación nunca otorga
    permisos que el simulador no tenga)."""
    if rol_es_super_admin:
        return False
    if user.is_super_admin:
        return True
    if PERMISO_ROLES_IMPERSONATE not in user.permissions:
        return False
    return not permisos_faltantes(permisos_efectivos_rol, set(user.permissions))


# ------------------------------------------------------------- helpers con BD

def permisos_efectivos_de_rol(conn, role_id: int) -> set[str]:
    """Codes de permisos efectivos (propios + heredados) de un rol, desde la vista.

    Ignora los permisos inactivos (pagina_web.Permissions.activo = 0, ver
    calidad.sp_SincronizarPermisosEmpresas): no otorgan nada, así que tampoco
    cuentan para la regla de delegación. Tiene que quedar espejado con el
    cálculo de permisos del usuario en security.py, si no un delegado vería
    roles "no gestionables" por permisos que en los hechos nadie tiene.
    """
    rows = conn.execute(
        text(f"""
            SELECT p.code
            FROM {VISTA_EFECTIVOS} rep WITH (NOLOCK)
            JOIN [Acme].[pagina_web].[Permissions] p WITH (NOLOCK) ON p.id = rep.permission_id
            WHERE rep.role_id = :rid AND p.activo = 1
        """),
        {"rid": role_id},
    ).scalars().all()
    return set(rows)


def mapa_padres(conn) -> dict[int, Optional[int]]:
    """Mapa {role_id: parent_role_id} de todos los roles."""
    rows = conn.execute(
        text("SELECT id, parent_role_id FROM [Acme].[pagina_web].[Roles] WITH (NOLOCK)")
    ).fetchall()
    return {row.id: row.parent_role_id for row in rows}


PERMISO_TIPS_MANAGE = "tips:manage"


def grupos_efectivos_de_roles(conn, role_ids: list[int]) -> set[int]:
    """Obtiene los IDs de grupos de tips efectivos para una lista de roles.

    Usa la vista pagina_web.RoleEffectiveTipGroups (o fallback a CTE recursiva).
    """
    if not role_ids:
        return set()

    try:
        query = text("""
            SELECT DISTINCT tip_group_id
            FROM [Acme].[pagina_web].[RoleEffectiveTipGroups] WITH (NOLOCK)
            WHERE role_id IN :rids
        """).bindparams(bindparam("rids", expanding=True))
        rows = conn.execute(query, {"rids": list(role_ids)}).scalars().all()
        return set(rows)
    except Exception as e:
        logger.warning(
            f"Fallo al consultar RoleEffectiveTipGroups, usando fallback CTE inline: {e}"
        )
        query_fallback = text("""
            WITH ancestros AS (
                SELECT r.id AS role_id, r.id AS ancestro_id, r.parent_role_id
                FROM [Acme].[pagina_web].[Roles] r
                UNION ALL
                SELECT a.role_id, p.id, p.parent_role_id
                FROM ancestros a
                JOIN [Acme].[pagina_web].[Roles] p ON p.id = a.parent_role_id
            )
            SELECT DISTINCT tgr.tip_group_id
            FROM ancestros a
            JOIN [Acme].[pagina_web].[TipGroupRoles] tgr ON tgr.role_id = a.ancestro_id
            WHERE a.role_id IN :rids
        """).bindparams(bindparam("rids", expanding=True))
        rows = conn.execute(query_fallback, {"rids": list(role_ids)}).scalars().all()
        return set(rows)


# ------------------------------------------------- alcance por empresa
# Los permisos template:<empresa> (calidad.Empresas.RequiredPermissionID ->
# pagina_web.Permissions) acotan qué empresas puede ver/auditar un usuario en
# auditorías realizadas, dashboards y ejecución. Mismo criterio que
# empresas_disponibles() en AuditorIA/Plantillas_prompts.py (mantener espejados).
# Las empresas con IsActive = 0 (soft delete) quedan fuera del alcance de todos
# salvo super admin / templates:manage, que no tienen restricción por empresa.

PERMISO_TEMPLATES_MANAGE = "templates:manage"


def resolver_empresas_permitidas(
    filas: list,
    permissions: set[str],
    is_super: bool,
) -> Optional[set[int]]:
    """Versión pura del cálculo de alcance. `filas` = [(empresa_id, required_code)].

    Devuelve None si el usuario no tiene restricción (super admin o
    templates:manage); si no, el set de EmpresaIDs accesibles: las empresas sin
    permiso requerido (públicas) más aquellas cuyo code está en sus permisos.
    """
    if is_super or PERMISO_TEMPLATES_MANAGE in permissions:
        return None
    return {
        empresa_id
        for empresa_id, required_code in filas
        if required_code is None or required_code in permissions
    }


def empresas_permitidas(conn, user: User) -> Optional[set[int]]:
    """Set de EmpresaIDs accesibles para el usuario, o None si ve todas.

    El LEFT JOIN a Permissions NO filtra por activo a propósito: code NULL
    significa "empresa pública" (la ve cualquiera), así que descartar el
    permiso inactivo convertiría en pública a una empresa restringida.
    """
    if user.is_super_admin or PERMISO_TEMPLATES_MANAGE in user.permissions:
        return None
    filas = conn.execute(text("""
        SELECT e.EmpresaID, p.code
        FROM [Acme].[calidad].[Empresas] e
        LEFT JOIN [Acme].[pagina_web].[Permissions] p ON p.id = e.RequiredPermissionID
        WHERE e.IsActive = 1
    """)).fetchall()
    return resolver_empresas_permitidas(
        [(row.EmpresaID, row.code) for row in filas],
        set(user.permissions),
        user.is_super_admin,
    )


def empresa_de_campana(conn, campana_id: int) -> Optional[int]:
    return conn.execute(
        text("SELECT EmpresaID FROM [Acme].[calidad].[Campanas] WHERE CampanaID = :cid"),
        {"cid": campana_id},
    ).scalar()


def empresa_de_plantilla(conn, plantilla_id: int) -> Optional[int]:
    return conn.execute(
        text("""
            SELECT c.EmpresaID
            FROM [Acme].[calidad].[Plantillas] p
            JOIN [Acme].[calidad].[Campanas] c ON c.CampanaID = p.CampanaID
            WHERE p.PlantillaID = :pid
        """),
        {"pid": plantilla_id},
    ).scalar()


def plantilla_de_atributo(conn, atributo_id: int) -> Optional[int]:
    return conn.execute(
        text("SELECT PlantillaID FROM [Acme].[calidad].[Atributos] WHERE AtributoID = :aid"),
        {"aid": atributo_id},
    ).scalar()


def exigir_acceso_empresa(conn, user: User, empresa_id=None, campana_id=None,
                          plantilla_id=None, atributo_id=None) -> None:
    """403 si algún identificador provisto cae fuera del alcance del usuario;
    404 si no se puede resolver a una empresa existente. Valida TODOS los
    identificadores que vengan (no solo el más específico)."""
    permitidas = empresas_permitidas(conn, user)
    if permitidas is None:
        return

    a_validar: list[Optional[int]] = []
    if empresa_id is not None:
        a_validar.append(int(empresa_id))
    if campana_id is not None:
        a_validar.append(empresa_de_campana(conn, int(campana_id)))
    if plantilla_id is not None:
        a_validar.append(empresa_de_plantilla(conn, int(plantilla_id)))
    if atributo_id is not None:
        pid = plantilla_de_atributo(conn, int(atributo_id))
        a_validar.append(empresa_de_plantilla(conn, pid) if pid is not None else None)

    for empresa in a_validar:
        if empresa is None:
            raise HTTPException(status_code=404, detail="Empresa/campaña/plantilla inexistente.")
        if empresa not in permitidas:
            logger.warning(
                f"Usuario {user.usuario} intentó acceder a la empresa {empresa} sin permiso."
            )
            raise HTTPException(
                status_code=403,
                detail="No tenés permiso sobre esa empresa (falta el permiso template de la empresa).",
            )


PERMISO_SINCRONICO = "audit:sync"

MENSAJE_SIN_SINCRONICO = (
    "El modo sincrónico (\"Auditar ahora\") requiere el permiso audit:sync. "
    "Usá el modo Batch: cuesta la mitad y los resultados llegan por correo y a "
    "\"Auditorías Realizadas\" cuando el lote termina."
)


def puede_auditar_sincronico(user: User) -> bool:
    """True si el usuario puede correr auditorías en modo sincrónico."""
    return bool(user.is_super_admin) or PERMISO_SINCRONICO in (user.permissions or [])


def exigir_modo_sincronico(user: User, contexto: str = "auditoría") -> None:
    """403 si el usuario no tiene audit:sync.

    El sincrónico le sale a Gemini el DOBLE que el batch (ver tasks.py) y es el
    modo que elige por default quien no conoce la diferencia, así que auditar
    "ahora" es un permiso aparte de auditar (audit:execute). Vale para la
    pantalla de auditoría y para programar corridas sincrónicas en el scheduler.
    """
    if puede_auditar_sincronico(user):
        return
    logger.warning(
        f"Usuario {user.usuario} intentó una {contexto} en modo sincrónico sin {PERMISO_SINCRONICO}."
    )
    raise HTTPException(status_code=403, detail=MENSAJE_SIN_SINCRONICO)


def registrar_auditoria(conn, actor_documento: int, action: str, entity_type: str,
                        entity_id: Optional[int], detail: Optional[dict] = None) -> None:
    """Inserta una fila en pagina_web.RbacAuditLog. No debe romper la operación
    principal: loguea y sigue si falla."""
    try:
        conn.execute(
            text("""
                INSERT INTO [Acme].[pagina_web].[RbacAuditLog]
                    (actor_documento, action, entity_type, entity_id, detail)
                VALUES (:actor, :action, :etype, :eid, :detail)
            """),
            {
                "actor": actor_documento,
                "action": action,
                "etype": entity_type,
                "eid": entity_id,
                "detail": json.dumps(detail, ensure_ascii=False, default=str) if detail else None,
            },
        )
    except Exception as e:
        logger.error(f"No se pudo registrar auditoría RBAC ({action} {entity_type} {entity_id}): {e}")
