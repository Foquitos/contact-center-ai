"""Endpoints de "Vacíos de conocimiento": qué le preguntaron al chatbot que la
documentación no responde, agrupado por tema para que Calidad lo triage.

- GET  /vacios              : lista agrupada, filtrable y ordenable por frecuencia.
- GET  /vacios/resumen      : KPIs (pendientes, huecos, fallos de recuperación).
- GET  /vacios/{id}         : las consultas reales que cayeron en ese tema.
- PUT  /vacios/{id}         : el triage de Calidad (agregar / no_corresponde / ya_documentado).

Permiso: chatbot.vacios (nace sin asignar → solo super admin hasta asignarlo a
los roles de Calidad, que son quienes documentan).

La lógica de detección y agrupación NO está acá: vive en app/vacios_conocimiento.py
y la corre el scheduler. Este router solo lee y triage.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import text

from app.database import engine
from app.dependencies import RoleChecker
from app.models import User

logger = logging.getLogger(__name__)

PERM_VACIOS = "chatbot.vacios"
require_vacios = RoleChecker([PERM_VACIOS])

router = APIRouter(prefix="/vacios", tags=["Vacíos de conocimiento"])

ESTADOS = {"pendiente", "agregar", "no_corresponde", "ya_documentado"}
# Ver _clasificar_causa en app/vacios_conocimiento.py: cada una le toca a alguien
# distinto (hueco -> Calidad; recuperacion/generacion -> nosotros).
CLASIFICACIONES = {"hueco", "recuperacion", "generacion", "fuera_de_alcance"}
ORDENES = {
    "ocurrencias": "v.ocurrencias DESC",
    "usuarios": "v.usuarios DESC",
    "reciente": "v.ultima_vez DESC",
    "antiguo": "v.primera_vez ASC",
}


class VacioOut(BaseModel):
    id: int
    chatbot_id: int
    slug: Optional[str] = None
    bot: Optional[str] = None
    tema: str
    pregunta_ejemplo: Optional[str] = None
    clasificacion: str
    estado: str
    ocurrencias: int
    usuarios: int
    primera_vez: Optional[str] = None
    ultima_vez: Optional[str] = None
    doc_sugerido: Optional[str] = None
    score_sugerido: Optional[float] = None
    notas: Optional[str] = None


class ConsultaVacioOut(BaseModel):
    id: int
    query: Optional[str] = None
    response: Optional[str] = None
    fecha: Optional[str] = None
    user_id: Optional[int] = None
    score_max: Optional[float] = None


class VacioDetalleOut(BaseModel):
    vacio: VacioOut
    consultas: List[ConsultaVacioOut]


class TriageIn(BaseModel):
    estado: str
    notas: Optional[str] = None

    @field_validator("estado")
    @classmethod
    def _estado_valido(cls, v: str) -> str:
        if v not in ESTADOS:
            raise ValueError(f"estado inválido: {v}. Válidos: {sorted(ESTADOS)}")
        return v


def _iso(valor) -> Optional[str]:
    return valor.isoformat() if valor is not None else None


def _fila_a_vacio(f) -> VacioOut:
    return VacioOut(
        id=f["id"], chatbot_id=f["chatbot_id"], slug=f["slug"], bot=f["bot"],
        tema=f["tema"], pregunta_ejemplo=f["pregunta_ejemplo"],
        clasificacion=f["clasificacion"], estado=f["estado"],
        ocurrencias=f["ocurrencias"], usuarios=f["usuarios"],
        primera_vez=_iso(f["primera_vez"]), ultima_vez=_iso(f["ultima_vez"]),
        doc_sugerido=f["doc_sugerido"], score_sugerido=f["score_sugerido"],
        notas=f["notas"],
    )


@router.get("", response_model=List[VacioOut])
def listar_vacios(
    slug: Optional[str] = None,
    estado: Optional[str] = None,
    clasificacion: Optional[str] = None,
    orden: str = Query("ocurrencias", description=f"uno de {sorted(ORDENES)}"),
    limite: int = Query(200, le=1000),
    current_user: User = Depends(require_vacios),
):
    """Por defecto lo más pedido primero: es el orden en que conviene documentar."""
    where = ["1=1"]
    params = {"limite": limite}
    if slug:
        where.append("c.slug = :slug")
        params["slug"] = slug
    if estado:
        if estado not in ESTADOS:
            raise HTTPException(400, f"estado inválido: {estado}")
        where.append("v.estado = :estado")
        params["estado"] = estado
    if clasificacion:
        if clasificacion not in CLASIFICACIONES:
            raise HTTPException(400, f"clasificación inválida: {clasificacion}")
        where.append("v.clasificacion = :clas")
        params["clas"] = clasificacion

    order_by = ORDENES.get(orden, ORDENES["ocurrencias"])
    query = text(f"""
        SELECT TOP (:limite)
               v.id, v.chatbot_id, c.slug, c.nombre AS bot, v.tema, v.pregunta_ejemplo,
               v.clasificacion, v.estado, v.ocurrencias, v.usuarios,
               v.primera_vez, v.ultima_vez, v.doc_sugerido, v.score_sugerido, v.notas
        FROM pagina_web.ChatbotVacios v
        JOIN pagina_web.Chatbots c ON c.id = v.chatbot_id
        WHERE {" AND ".join(where)}
        ORDER BY {order_by}
    """)
    try:
        with engine.connect() as conn:
            filas = conn.execute(query, params).mappings().all()
        return [_fila_a_vacio(f) for f in filas]
    except Exception:
        logger.exception("Error listando vacíos de conocimiento")
        raise HTTPException(500, "No se pudieron obtener los vacíos de conocimiento")


@router.get("/resumen")
def resumen_vacios(slug: Optional[str] = None, current_user: User = Depends(require_vacios)):
    """KPIs de la cabecera. `consultas_afectadas` es lo que hace tangible el
    impacto: no es "hay 12 temas", es "12 temas se comieron 340 consultas"."""
    where = "WHERE 1=1" + (" AND c.slug = :slug" if slug else "")
    params = {"slug": slug} if slug else {}
    try:
        with engine.connect() as conn:
            fila = conn.execute(text(f"""
                SELECT
                    COUNT(*) AS temas,
                    SUM(CASE WHEN v.estado = 'pendiente' THEN 1 ELSE 0 END) AS pendientes,
                    SUM(CASE WHEN v.clasificacion = 'hueco' AND v.estado = 'pendiente'
                             THEN 1 ELSE 0 END) AS huecos_pendientes,
                    SUM(CASE WHEN v.clasificacion = 'recuperacion' THEN 1 ELSE 0 END) AS fallos_recuperacion,
                    SUM(CASE WHEN v.clasificacion = 'generacion' THEN 1 ELSE 0 END) AS fallos_generacion,
                    SUM(CASE WHEN v.clasificacion = 'fuera_de_alcance' THEN 1 ELSE 0 END) AS fuera_de_alcance,
                    SUM(v.ocurrencias) AS consultas_afectadas
                FROM pagina_web.ChatbotVacios v
                JOIN pagina_web.Chatbots c ON c.id = v.chatbot_id
                {where}
            """), params).mappings().first()

            # Sin clasificar todavía: las que el scheduler no drenó aún. Sirve para
            # que no parezca que no hay vacíos cuando en realidad falta procesar.
            pendiente_proceso = conn.execute(text("""
                SELECT COUNT(*) FROM pagina_web.query_chatbots_logs
                WHERE sin_cobertura = 1 AND vacio_id IS NULL
            """)).scalar_one()

        datos = dict(fila or {})
        datos["sin_procesar"] = pendiente_proceso
        return datos
    except Exception:
        logger.exception("Error en el resumen de vacíos")
        raise HTTPException(500, "No se pudo obtener el resumen")


@router.get("/{vacio_id}", response_model=VacioDetalleOut)
def detalle_vacio(vacio_id: int, current_user: User = Depends(require_vacios)):
    """El tema más las consultas reales que cayeron ahí: Calidad necesita ver cómo
    lo preguntaron los operadores para saber qué documentar."""
    try:
        with engine.connect() as conn:
            fila = conn.execute(text("""
                SELECT v.id, v.chatbot_id, c.slug, c.nombre AS bot, v.tema, v.pregunta_ejemplo,
                       v.clasificacion, v.estado, v.ocurrencias, v.usuarios,
                       v.primera_vez, v.ultima_vez, v.doc_sugerido, v.score_sugerido, v.notas
                FROM pagina_web.ChatbotVacios v
                JOIN pagina_web.Chatbots c ON c.id = v.chatbot_id
                WHERE v.id = :id
            """), {"id": vacio_id}).mappings().first()
            if fila is None:
                raise HTTPException(404, "Vacío no encontrado")

            consultas = conn.execute(text("""
                SELECT TOP (50) l.id, l.[query], l.response, l.fecha, l.user_id, l.score_max
                FROM pagina_web.query_chatbots_logs l
                WHERE l.vacio_id = :id
                ORDER BY l.fecha DESC
            """), {"id": vacio_id}).mappings().all()

        return VacioDetalleOut(
            vacio=_fila_a_vacio(fila),
            consultas=[
                ConsultaVacioOut(
                    id=c["id"], query=c["query"], response=c["response"],
                    fecha=_iso(c["fecha"]), user_id=c["user_id"], score_max=c["score_max"],
                )
                for c in consultas
            ],
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error obteniendo el detalle del vacío {vacio_id}")
        raise HTTPException(500, "No se pudo obtener el detalle")


@router.put("/{vacio_id}", response_model=VacioOut)
def triagear_vacio(vacio_id: int, body: TriageIn, current_user: User = Depends(require_vacios)):
    """El triage de Calidad. 'ya_documentado' es el que nos rebota el caso a
    nosotros: significa que la información existe y el buscador no la encontró."""
    try:
        with engine.begin() as conn:
            actualizadas = conn.execute(text("""
                UPDATE pagina_web.ChatbotVacios
                SET estado = :estado,
                    notas = :notas,
                    resuelto_por = :uid,
                    resuelto_at = CASE WHEN :estado = 'pendiente' THEN NULL ELSE SYSDATETIME() END,
                    updated_at = SYSDATETIME()
                WHERE id = :id
            """), {
                "id": vacio_id, "estado": body.estado, "notas": body.notas,
                "uid": current_user.usuario,
            }).rowcount
            if not actualizadas:
                raise HTTPException(404, "Vacío no encontrado")

            fila = conn.execute(text("""
                SELECT v.id, v.chatbot_id, c.slug, c.nombre AS bot, v.tema, v.pregunta_ejemplo,
                       v.clasificacion, v.estado, v.ocurrencias, v.usuarios,
                       v.primera_vez, v.ultima_vez, v.doc_sugerido, v.score_sugerido, v.notas
                FROM pagina_web.ChatbotVacios v
                JOIN pagina_web.Chatbots c ON c.id = v.chatbot_id
                WHERE v.id = :id
            """), {"id": vacio_id}).mappings().first()
        return _fila_a_vacio(fila)
    except HTTPException:
        raise
    except Exception:
        logger.exception(f"Error triageando el vacío {vacio_id}")
        raise HTTPException(500, "No se pudo guardar el triage")
