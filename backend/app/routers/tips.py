"""Endpoints de Tips del Día y administración de grupos de tips por rol.

Permite:
1. Obtener el tip del día para el usuario autenticado (determinístico por día y
   por usuario, y aleatorio para Super Admin).
2. Respetar la jerarquía de roles RBAC: los tips asignados a un rol se heredan
   automáticamente a todos sus roles hijos y descendientes.
3. Administrar (CRUD) grupos de tips y tips individuales para usuarios con el
   permiso tips:manage o super admin.
"""
from datetime import date
import hashlib
import logging
import random
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import bindparam, text

from app.database import engine
from app.models import (
    TipCreate,
    TipFeedbackResponse,
    TipGroupCreate,
    TipGroupResponse,
    TipGroupUpdate,
    TipResponseItem,
    TipsResponse,
    TipUpdate,
    User,
)
from app.rbac import (
    PERMISO_TIPS_MANAGE,
    grupos_efectivos_de_roles,
    registrar_auditoria,
)
from app.security import get_current_active_user

router = APIRouter(tags=["Tips"])
logger = logging.getLogger(__name__)


def tips_manage_required(user: User = Depends(get_current_active_user)) -> User:
    """Verifica que el usuario tenga permiso para gestionar tips o sea Super Admin."""
    if not (user.is_super_admin or PERMISO_TIPS_MANAGE in (user.permissions or [])):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Se requiere permiso para gestionar tips del día (tips:manage).",
        )
    return user


def _obtener_roles_usuario(conn, documento: int) -> list[int]:
    """Obtiene los IDs de roles asignados directamente a un usuario."""
    rows = conn.execute(
        text("SELECT role_id FROM [Acme].[pagina_web].[UserRoles] WITH (NOLOCK) WHERE nomina_id = :doc"),
        {"doc": documento},
    ).scalars().all()
    return list(rows)


def _seleccionar_tip_deterministico(tips: list, user_id: int, fecha: Optional[date] = None):
    """Elige un tip determinístico para la jornada basado en la fecha y el usuario."""
    if not tips:
        return None
    if fecha is None:
        fecha = date.today()
    clave = f"{fecha.isoformat()}:{user_id}".encode("utf-8")
    idx = int(hashlib.md5(clave).hexdigest(), 16) % len(tips)
    return tips[idx]


# --------------------------------------------------------------------------- #
# Endpoint público (para usuarios autenticados)                               #
# --------------------------------------------------------------------------- #

@router.get("/tips", response_model=TipsResponse)
def get_tip_del_dia(
    aleatorio: bool = Query(False, description="Forzar rotación aleatoria bajo demanda"),
    current_user: User = Depends(get_current_active_user),
):
    """Devuelve el tip del día para el usuario autenticado.

    Criterio:
    - Si el usuario está simulando un rol, evalúa solo ese rol.
    - Para Super Admin (sin simulación) o si aleatorio=True: salta un tip al azar.
    - Para usuarios normales: determinístico durante la jornada (el mismo tip hoy,
      rota mañana), considerando los grupos asignados a sus roles y sus ancestros.
    - Respeta fechas de vigencia (fecha_desde / fecha_hasta) y estado activo.
    """
    if engine is None:
        logger.error("Database engine not available.")
        return TipsResponse(tips=None)

    with engine.connect() as conn:
        def _obtener_feedback(tid: int) -> tuple[int, bool]:
            likes = 0
            voted = False
            try:
                fb_row = conn.execute(text("""
                    SELECT COUNT(*) AS total,
                           SUM(CASE WHEN documento = :doc THEN 1 ELSE 0 END) AS user_voted
                    FROM [Acme].[pagina_web].[TipFeedback] WITH (NOLOCK)
                    WHERE tip_id = :tid
                """), {"tid": tid, "doc": current_user.usuario}).fetchone()
                if fb_row:
                    likes = fb_row.total or 0
                    voted = bool(fb_row.user_voted)
            except Exception:
                pass
            return likes, voted

        def _armar_respuesta(fila) -> TipsResponse:
            likes_count, user_voted = _obtener_feedback(fila.id)
            return TipsResponse(
                tips=fila.content,
                title=fila.title,
                group_name=fila.group_name,
                id=fila.id,
                tipo=fila.tipo,
                es_prioritario=bool(getattr(fila, "es_prioritario", False)),
                url_accion=getattr(fila, "url_accion", None),
                texto_accion=getattr(fila, "texto_accion", None),
                likes_count=likes_count,
                user_voted=user_voted,
            )

        # Caso Super Admin directo (no simulando rol)
        if current_user.is_super_admin and not current_user.simulando_rol_id:
            tips_rows = conn.execute(text("""
                SELECT t.id, t.title, t.content, ISNULL(t.tipo, 'info') AS tipo,
                       ISNULL(t.es_prioritario, 0) AS es_prioritario,
                       t.url_accion, t.texto_accion, tg.name AS group_name
                FROM [Acme].[pagina_web].[Tips] t WITH (NOLOCK)
                JOIN [Acme].[pagina_web].[TipGroups] tg WITH (NOLOCK) ON tg.id = t.group_id
                WHERE t.activo = 1 AND tg.activo = 1
                  AND (t.fecha_desde IS NULL OR t.fecha_desde <= CONVERT(DATE, GETDATE()))
                  AND (t.fecha_hasta IS NULL OR t.fecha_hasta >= CONVERT(DATE, GETDATE()))
            """)).fetchall()

            if not tips_rows:
                return TipsResponse(tips=None)

            prioritarios = [r for r in tips_rows if getattr(r, "es_prioritario", False)]
            candidatos = prioritarios if prioritarios else tips_rows
            elegido = random.choice(candidatos)
            return _armar_respuesta(elegido)

        # Determinar roles efectivos del usuario
        if current_user.simulando_rol_id:
            role_ids = [current_user.simulando_rol_id]
        else:
            role_ids = _obtener_roles_usuario(conn, current_user.usuario)

        if not role_ids:
            return TipsResponse(tips=None)

        # Resolver grupos asignados a estos roles o sus ancestros (herencia hacia hijos)
        group_ids = grupos_efectivos_de_roles(conn, role_ids)
        if not group_ids:
            return TipsResponse(tips=None)

        query = text("""
            SELECT t.id, t.title, t.content, ISNULL(t.tipo, 'info') AS tipo,
                   ISNULL(t.es_prioritario, 0) AS es_prioritario,
                   t.url_accion, t.texto_accion, tg.name AS group_name
            FROM [Acme].[pagina_web].[Tips] t WITH (NOLOCK)
            JOIN [Acme].[pagina_web].[TipGroups] tg WITH (NOLOCK) ON tg.id = t.group_id
            WHERE t.activo = 1 AND tg.activo = 1
              AND (t.fecha_desde IS NULL OR t.fecha_desde <= CONVERT(DATE, GETDATE()))
              AND (t.fecha_hasta IS NULL OR t.fecha_hasta >= CONVERT(DATE, GETDATE()))
              AND t.group_id IN :gids
            ORDER BY t.id
        """).bindparams(bindparam("gids", expanding=True))

        tips_rows = conn.execute(query, {"gids": list(group_ids)}).fetchall()
        if not tips_rows:
            return TipsResponse(tips=None)

        prioritarios = [r for r in tips_rows if getattr(r, "es_prioritario", False)]
        candidatos = prioritarios if prioritarios else tips_rows

        if aleatorio:
            elegido = random.choice(candidatos)
        else:
            elegido = _seleccionar_tip_deterministico(candidatos, current_user.usuario)

        return _armar_respuesta(elegido)



# --------------------------------------------------------------------------- #
# CRUD Grupos de Tips                                                         #
# --------------------------------------------------------------------------- #

@router.get("/tips/groups", response_model=List[TipGroupResponse])
def list_tip_groups(user: User = Depends(tips_manage_required)):
    """Lista todos los grupos de tips con sus roles y conteo de tips."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Database service is unavailable.")

    with engine.connect() as conn:
        groups = conn.execute(text("""
            SELECT tg.id, tg.name, tg.description, tg.activo,
                   CONVERT(VARCHAR(19), tg.created_at, 120) AS created_at,
                   CONVERT(VARCHAR(19), tg.updated_at, 120) AS updated_at,
                   (SELECT COUNT(*) FROM [Acme].[pagina_web].[Tips] t WHERE t.group_id = tg.id) AS tips_count
            FROM [Acme].[pagina_web].[TipGroups] tg
            ORDER BY tg.id DESC
        """)).fetchall()

        # Obtener mapeo de roles por grupo
        group_roles = conn.execute(text("""
            SELECT tgr.tip_group_id, r.id AS role_id, r.name AS role_name
            FROM [Acme].[pagina_web].[TipGroupRoles] tgr
            JOIN [Acme].[pagina_web].[Roles] r ON r.id = tgr.role_id
            ORDER BY r.name
        """)).fetchall()

        roles_by_group: dict[int, list[int]] = {}
        names_by_group: dict[int, list[str]] = {}
        for row in group_roles:
            roles_by_group.setdefault(row.tip_group_id, []).append(row.role_id)
            names_by_group.setdefault(row.tip_group_id, []).append(row.role_name)

        alcanzados_by_group: dict[int, int] = {}
        try:
            alc_rows = conn.execute(text("""
                SELECT retg.tip_group_id, COUNT(DISTINCT ur.nomina_id) AS cant
                FROM [Acme].[pagina_web].[RoleEffectiveTipGroups] retg
                JOIN [Acme].[pagina_web].[UserRoles] ur ON ur.role_id = retg.role_id
                GROUP BY retg.tip_group_id
            """)).fetchall()
            for r in alc_rows:
                alcanzados_by_group[r.tip_group_id] = r.cant
        except Exception:
            pass

        result = []
        for g in groups:
            result.append(TipGroupResponse(
                id=g.id,
                name=g.name,
                description=g.description,
                activo=bool(g.activo),
                role_ids=roles_by_group.get(g.id, []),
                roles_nombres=names_by_group.get(g.id, []),
                tips_count=g.tips_count or 0,
                usuarios_alcanzados=alcanzados_by_group.get(g.id, 0),
                created_at=g.created_at,
                updated_at=g.updated_at,
            ))
        return result


@router.post("/tips/groups", response_model=TipGroupResponse, status_code=status.HTTP_201_CREATED)
def create_tip_group(payload: TipGroupCreate, user: User = Depends(tips_manage_required)):
    """Crea un nuevo grupo de tips y asigna sus roles."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Database service is unavailable.")

    with engine.connect() as conn:
        with conn.begin():
            insert_q = text("""
                INSERT INTO [Acme].[pagina_web].[TipGroups] (name, description, activo)
                OUTPUT INSERTED.id
                VALUES (:name, :desc, :activo)
            """)
            group_id = conn.execute(insert_q, {
                "name": payload.name.strip(),
                "desc": payload.description.strip() if payload.description else None,
                "activo": 1 if payload.activo else 0,
            }).scalar()

            if payload.role_ids:
                for rid in set(payload.role_ids):
                    conn.execute(text("""
                        INSERT INTO [Acme].[pagina_web].[TipGroupRoles] (tip_group_id, role_id)
                        VALUES (:gid, :rid)
                    """), {"gid": group_id, "rid": rid})

            registrar_auditoria(
                conn,
                actor_documento=user.usuario,
                action="create",
                entity_type="tip_group",
                entity_id=group_id,
                detail={"name": payload.name, "role_ids": payload.role_ids},
            )

    return get_tip_group_by_id(group_id, user)


@router.get("/tips/groups/{group_id}", response_model=TipGroupResponse)
def get_tip_group_by_id(group_id: int, user: User = Depends(tips_manage_required)):
    """Obtiene el detalle de un grupo de tips."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Database service is unavailable.")

    with engine.connect() as conn:
        g = conn.execute(text("""
            SELECT tg.id, tg.name, tg.description, tg.activo,
                   CONVERT(VARCHAR(19), tg.created_at, 120) AS created_at,
                   CONVERT(VARCHAR(19), tg.updated_at, 120) AS updated_at,
                   (SELECT COUNT(*) FROM [Acme].[pagina_web].[Tips] t WHERE t.group_id = tg.id) AS tips_count
            FROM [Acme].[pagina_web].[TipGroups] tg
            WHERE tg.id = :gid
        """), {"gid": group_id}).fetchone()

        if not g:
            raise HTTPException(status_code=404, detail="Grupo de tips no encontrado.")

        roles = conn.execute(text("""
            SELECT r.id AS role_id, r.name AS role_name
            FROM [Acme].[pagina_web].[TipGroupRoles] tgr
            JOIN [Acme].[pagina_web].[Roles] r ON r.id = tgr.role_id
            WHERE tgr.tip_group_id = :gid
            ORDER BY r.name
        """), {"gid": group_id}).fetchall()

        usuarios_alcanzados = 0
        try:
            usuarios_alcanzados = conn.execute(text("""
                SELECT COUNT(DISTINCT ur.nomina_id)
                FROM [Acme].[pagina_web].[RoleEffectiveTipGroups] retg
                JOIN [Acme].[pagina_web].[UserRoles] ur ON ur.role_id = retg.role_id
                WHERE retg.tip_group_id = :gid
            """), {"gid": group_id}).scalar() or 0
        except Exception:
            pass

        return TipGroupResponse(
            id=g.id,
            name=g.name,
            description=g.description,
            activo=bool(g.activo),
            role_ids=[r.role_id for r in roles],
            roles_nombres=[r.role_name for r in roles],
            tips_count=g.tips_count or 0,
            usuarios_alcanzados=usuarios_alcanzados,
            created_at=g.created_at,
            updated_at=g.updated_at,
        )



@router.put("/tips/groups/{group_id}", response_model=TipGroupResponse)
def update_tip_group(group_id: int, payload: TipGroupUpdate, user: User = Depends(tips_manage_required)):
    """Actualiza un grupo de tips y reasigna roles."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Database service is unavailable.")

    with engine.connect() as conn:
        with conn.begin():
            exist = conn.execute(text(
                "SELECT id FROM [Acme].[pagina_web].[TipGroups] WHERE id = :gid"
            ), {"gid": group_id}).scalar()
            if not exist:
                raise HTTPException(status_code=404, detail="Grupo de tips no encontrado.")

            set_clauses = ["updated_at = GETDATE()"]
            params = {"gid": group_id}

            if payload.name is not None:
                set_clauses.append("name = :name")
                params["name"] = payload.name.strip()
            if payload.description is not None:
                set_clauses.append("description = :desc")
                params["desc"] = payload.description.strip() if payload.description else None
            if payload.activo is not None:
                set_clauses.append("activo = :activo")
                params["activo"] = 1 if payload.activo else 0

            conn.execute(text(f"""
                UPDATE [Acme].[pagina_web].[TipGroups]
                SET {', '.join(set_clauses)}
                WHERE id = :gid
            """), params)

            if payload.role_ids is not None:
                conn.execute(text(
                    "DELETE FROM [Acme].[pagina_web].[TipGroupRoles] WHERE tip_group_id = :gid"
                ), {"gid": group_id})
                for rid in set(payload.role_ids):
                    conn.execute(text("""
                        INSERT INTO [Acme].[pagina_web].[TipGroupRoles] (tip_group_id, role_id)
                        VALUES (:gid, :rid)
                    """), {"gid": group_id, "rid": rid})

            registrar_auditoria(
                conn,
                actor_documento=user.usuario,
                action="update",
                entity_type="tip_group",
                entity_id=group_id,
                detail=payload.model_dump(exclude_unset=True),
            )

    return get_tip_group_by_id(group_id, user)


@router.delete("/tips/groups/{group_id}")
def delete_tip_group(group_id: int, user: User = Depends(tips_manage_required)):
    """Elimina un grupo de tips (en cascada elimina sus asignaciones y tips asociados)."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Database service is unavailable.")

    with engine.connect() as conn:
        with conn.begin():
            exist = conn.execute(text(
                "SELECT name FROM [Acme].[pagina_web].[TipGroups] WHERE id = :gid"
            ), {"gid": group_id}).fetchone()
            if not exist:
                raise HTTPException(status_code=404, detail="Grupo de tips no encontrado.")

            conn.execute(text(
                "DELETE FROM [Acme].[pagina_web].[TipGroups] WHERE id = :gid"
            ), {"gid": group_id})

            registrar_auditoria(
                conn,
                actor_documento=user.usuario,
                action="delete",
                entity_type="tip_group",
                entity_id=group_id,
                detail={"name": exist.name},
            )

    return {"message": f"Grupo '{exist.name}' eliminado correctamente."}


# --------------------------------------------------------------------------- #
# CRUD Tips Individuales                                                      #
# --------------------------------------------------------------------------- #

@router.get("/tips/items", response_model=List[TipResponseItem])
def list_tip_items(
    group_id: Optional[int] = Query(None, description="Filtrar por ID de grupo"),
    user: User = Depends(tips_manage_required),
):
    """Lista todos los tips individuales, opcionalmente filtrados por grupo."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Database service is unavailable.")

    with engine.connect() as conn:
        filtro = "WHERE t.group_id = :gid" if group_id is not None else ""
        query = text(f"""
            SELECT t.id, t.group_id, tg.name AS group_name, t.title, t.content,
                   ISNULL(t.tipo, 'info') AS tipo,
                   ISNULL(t.es_prioritario, 0) AS es_prioritario,
                   t.url_accion, t.texto_accion,
                   CONVERT(VARCHAR(10), t.fecha_desde, 120) AS fecha_desde,
                   CONVERT(VARCHAR(10), t.fecha_hasta, 120) AS fecha_hasta,
                   t.activo,
                   CONVERT(VARCHAR(19), t.created_at, 120) AS created_at,
                   CONVERT(VARCHAR(19), t.updated_at, 120) AS updated_at
            FROM [Acme].[pagina_web].[Tips] t
            JOIN [Acme].[pagina_web].[TipGroups] tg ON tg.id = t.group_id
            {filtro}
            ORDER BY t.id DESC
        """)
        params = {"gid": group_id} if group_id is not None else {}
        rows = conn.execute(query, params).fetchall()

        feedback_by_tip: dict[int, int] = {}
        try:
            fb_rows = conn.execute(text("""
                SELECT tip_id, COUNT(*) AS total
                FROM [Acme].[pagina_web].[TipFeedback]
                GROUP BY tip_id
            """)).fetchall()
            for r in fb_rows:
                feedback_by_tip[r.tip_id] = r.total
        except Exception:
            pass

        return [
            TipResponseItem(
                id=r.id,
                group_id=r.group_id,
                group_name=r.group_name,
                title=r.title,
                content=r.content,
                tipo=r.tipo,
                fecha_desde=r.fecha_desde,
                fecha_hasta=r.fecha_hasta,
                es_prioritario=bool(r.es_prioritario),
                url_accion=r.url_accion,
                texto_accion=r.texto_accion,
                feedback_count=feedback_by_tip.get(r.id, 0),
                activo=bool(r.activo),
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in rows
        ]


@router.post("/tips/items", response_model=TipResponseItem, status_code=status.HTTP_201_CREATED)
def create_tip_item(payload: TipCreate, user: User = Depends(tips_manage_required)):
    """Crea un nuevo tip individual en un grupo."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Database service is unavailable.")

    with engine.connect() as conn:
        with conn.begin():
            group = conn.execute(text(
                "SELECT id, name FROM [Acme].[pagina_web].[TipGroups] WHERE id = :gid"
            ), {"gid": payload.group_id}).fetchone()
            if not group:
                raise HTTPException(status_code=404, detail="El grupo especificado no existe.")

            insert_q = text("""
                INSERT INTO [Acme].[pagina_web].[Tips]
                    (group_id, title, content, tipo, fecha_desde, fecha_hasta, es_prioritario, url_accion, texto_accion, activo)
                OUTPUT INSERTED.id
                VALUES (:gid, :title, :content, :tipo, :fdesde, :fhasta, :prio, :url, :txt, :activo)
            """)
            tip_id = conn.execute(insert_q, {
                "gid": payload.group_id,
                "title": payload.title.strip() if payload.title else None,
                "content": payload.content.strip(),
                "tipo": payload.tipo or "info",
                "fdesde": payload.fecha_desde or None,
                "fhasta": payload.fecha_hasta or None,
                "prio": 1 if payload.es_prioritario else 0,
                "url": payload.url_accion.strip() if payload.url_accion else None,
                "txt": payload.texto_accion.strip() if payload.texto_accion else None,
                "activo": 1 if payload.activo else 0,
            }).scalar()

            registrar_auditoria(
                conn,
                actor_documento=user.usuario,
                action="create",
                entity_type="tip_item",
                entity_id=tip_id,
                detail={"group_id": payload.group_id, "title": payload.title, "tipo": payload.tipo},
            )

    return get_tip_item_by_id(tip_id, user)


@router.get("/tips/items/{tip_id}", response_model=TipResponseItem)
def get_tip_item_by_id(tip_id: int, user: User = Depends(tips_manage_required)):
    """Obtiene un tip individual por su ID."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Database service is unavailable.")

    with engine.connect() as conn:
        r = conn.execute(text("""
            SELECT t.id, t.group_id, tg.name AS group_name, t.title, t.content,
                   ISNULL(t.tipo, 'info') AS tipo,
                   ISNULL(t.es_prioritario, 0) AS es_prioritario,
                   t.url_accion, t.texto_accion,
                   CONVERT(VARCHAR(10), t.fecha_desde, 120) AS fecha_desde,
                   CONVERT(VARCHAR(10), t.fecha_hasta, 120) AS fecha_hasta,
                   t.activo,
                   CONVERT(VARCHAR(19), t.created_at, 120) AS created_at,
                   CONVERT(VARCHAR(19), t.updated_at, 120) AS updated_at
            FROM [Acme].[pagina_web].[Tips] t
            JOIN [Acme].[pagina_web].[TipGroups] tg ON tg.id = t.group_id
            WHERE t.id = :tid
        """), {"tid": tip_id}).fetchone()

        if not r:
            raise HTTPException(status_code=404, detail="Tip no encontrado.")

        feedback_count = 0
        try:
            feedback_count = conn.execute(text(
                "SELECT COUNT(*) FROM [Acme].[pagina_web].[TipFeedback] WHERE tip_id = :tid"
            ), {"tid": tip_id}).scalar() or 0
        except Exception:
            pass

        return TipResponseItem(
            id=r.id,
            group_id=r.group_id,
            group_name=r.group_name,
            title=r.title,
            content=r.content,
            tipo=r.tipo,
            fecha_desde=r.fecha_desde,
            fecha_hasta=r.fecha_hasta,
            es_prioritario=bool(r.es_prioritario),
            url_accion=r.url_accion,
            texto_accion=r.texto_accion,
            feedback_count=feedback_count,
            activo=bool(r.activo),
            created_at=r.created_at,
            updated_at=r.updated_at,
        )


@router.put("/tips/items/{tip_id}", response_model=TipResponseItem)
def update_tip_item(tip_id: int, payload: TipUpdate, user: User = Depends(tips_manage_required)):
    """Actualiza un tip individual."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Database service is unavailable.")

    with engine.connect() as conn:
        with conn.begin():
            exist = conn.execute(text(
                "SELECT id FROM [Acme].[pagina_web].[Tips] WHERE id = :tid"
            ), {"tid": tip_id}).scalar()
            if not exist:
                raise HTTPException(status_code=404, detail="Tip no encontrado.")

            set_clauses = ["updated_at = GETDATE()"]
            params = {"tid": tip_id}

            if payload.group_id is not None:
                grp_exist = conn.execute(text(
                    "SELECT id FROM [Acme].[pagina_web].[TipGroups] WHERE id = :gid"
                ), {"gid": payload.group_id}).scalar()
                if not grp_exist:
                    raise HTTPException(status_code=404, detail="El grupo de destino no existe.")
                set_clauses.append("group_id = :gid")
                params["gid"] = payload.group_id

            if payload.title is not None:
                set_clauses.append("title = :title")
                params["title"] = payload.title.strip() if payload.title else None

            if payload.content is not None:
                set_clauses.append("content = :content")
                params["content"] = payload.content.strip()

            if payload.tipo is not None:
                set_clauses.append("tipo = :tipo")
                params["tipo"] = payload.tipo

            if payload.fecha_desde is not None:
                set_clauses.append("fecha_desde = :fdesde")
                params["fdesde"] = payload.fecha_desde or None

            if payload.fecha_hasta is not None:
                set_clauses.append("fecha_hasta = :fhasta")
                params["fhasta"] = payload.fecha_hasta or None

            if payload.es_prioritario is not None:
                set_clauses.append("es_prioritario = :prio")
                params["prio"] = 1 if payload.es_prioritario else 0

            if payload.url_accion is not None:
                set_clauses.append("url_accion = :url")
                params["url"] = payload.url_accion.strip() if payload.url_accion else None

            if payload.texto_accion is not None:
                set_clauses.append("texto_accion = :txt")
                params["txt"] = payload.texto_accion.strip() if payload.texto_accion else None

            if payload.activo is not None:
                set_clauses.append("activo = :activo")
                params["activo"] = 1 if payload.activo else 0

            conn.execute(text(f"""
                UPDATE [Acme].[pagina_web].[Tips]
                SET {', '.join(set_clauses)}
                WHERE id = :tid
            """), params)

            registrar_auditoria(
                conn,
                actor_documento=user.usuario,
                action="update",
                entity_type="tip_item",
                entity_id=tip_id,
                detail=payload.model_dump(exclude_unset=True),
            )

    return get_tip_item_by_id(tip_id, user)


@router.post("/tips/items/{tip_id}/feedback", response_model=TipFeedbackResponse)
def toggle_tip_feedback(
    tip_id: int,
    current_user: User = Depends(get_current_active_user),
):
    """Registra o cancela el acuse / feedback ('Me sirvió') de un usuario sobre un tip."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Database service is unavailable.")

    with engine.connect() as conn:
        with conn.begin():
            exist = conn.execute(text(
                "SELECT id FROM [Acme].[pagina_web].[Tips] WITH (NOLOCK) WHERE id = :tid"
            ), {"tid": tip_id}).scalar()
            if not exist:
                raise HTTPException(status_code=404, detail="Tip no encontrado.")

            voted_id = None
            try:
                voted_id = conn.execute(text("""
                    SELECT id FROM [Acme].[pagina_web].[TipFeedback]
                    WHERE tip_id = :tid AND documento = :doc
                """), {"tid": tip_id, "doc": current_user.usuario}).scalar()
            except Exception:
                pass

            if voted_id:
                conn.execute(text("""
                    DELETE FROM [Acme].[pagina_web].[TipFeedback] WHERE id = :fbid
                """), {"fbid": voted_id})
                user_voted = False
            else:
                try:
                    conn.execute(text("""
                        INSERT INTO [Acme].[pagina_web].[TipFeedback] (tip_id, documento)
                        VALUES (:tid, :doc)
                    """), {"tid": tip_id, "doc": current_user.usuario})
                    user_voted = True
                except Exception:
                    user_voted = True

            count = 0
            try:
                count = conn.execute(text("""
                    SELECT COUNT(*) FROM [Acme].[pagina_web].[TipFeedback] WITH (NOLOCK) WHERE tip_id = :tid
                """), {"tid": tip_id}).scalar() or 0
            except Exception:
                pass

            return TipFeedbackResponse(ok=True, likes_count=count, user_voted=user_voted)


@router.delete("/tips/items/{tip_id}")
def delete_tip_item(tip_id: int, user: User = Depends(tips_manage_required)):
    """Elimina un tip individual."""
    if engine is None:
        raise HTTPException(status_code=503, detail="Database service is unavailable.")

    with engine.connect() as conn:
        with conn.begin():
            exist = conn.execute(text(
                "SELECT id, title FROM [Acme].[pagina_web].[Tips] WHERE id = :tid"
            ), {"tid": tip_id}).fetchone()
            if not exist:
                raise HTTPException(status_code=404, detail="Tip no encontrado.")

            conn.execute(text(
                "DELETE FROM [Acme].[pagina_web].[Tips] WHERE id = :tid"
            ), {"tid": tip_id})

            registrar_auditoria(
                conn,
                actor_documento=user.usuario,
                action="delete",
                entity_type="tip_item",
                entity_id=tip_id,
                detail={"title": exist.title},
            )

    return {"message": "Tip eliminado correctamente."}

