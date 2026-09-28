"""Panel de administración de chatbots (equipo de calidad, permiso chatbot:admin).

CRUD sobre pagina_web.Chatbots/ChatbotPcrc y encolado de reindexados
(pagina_web.ChatbotIndexJobs, procesada por run_scheduler.py). Al crear un bot
standalone se crea su permiso chatbot:<slug> en la misma transacción; los bots
del grupo CSV comparten el permiso chatbot:csv y se resuelven por PCRC.
"""
import base64
import logging
import mimetypes
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, File, Form, UploadFile, Request
from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError

from app import chatbot_imagenes, chatbot_tablas_admin, doc_jobs, doc_material
from app.config import settings
from app.database import engine
from app.dependencies import RoleChecker
from app.models import (
    ChatbotAdminCreate, ChatbotAdminOut, ChatbotAdminUpdate, ChatbotImagenIn,
    ChatbotImagenOut, DocCompartirIn, DocFormatearRequest, DocJobActualOut, DocJobEstadoOut,
    DocJobOut, DocMarkdownBulkIn, DocMarkdownIn, DocMarkdownOut, DocMaterialIn,
    DocMaterialOut, DocMergeRequest, DocPropuestaOut, FuenteCrudaIn, IndexJobOut,
    ReindexResponse, TablaActualizarRequest, TablaAplicarIn, TablaConvertirIn,
    TablaFilaIn, TablaFilasOut, TablaGuardarIn, TablaMetadataIn, TablaOut, User,
    TablaDesdeMaterialRequest, TablaFilaIn, TablaFilasOut, TablaGuardarIn,
    TablaMetadataIn, TablaOut, User,
)
from app.chatbot_config import GRUPO_CSV, SLUG_REGEX, normalizar_pcrc, slugify_nombre
from app.security import get_current_active_user
from AuditorIA import office_a_texto

router = APIRouter(
    prefix="/chatbots/admin",
    tags=["ChatBot Admin"],
    dependencies=[Depends(RoleChecker(["chatbot:admin"]))],
)
logger = logging.getLogger(__name__)

PERMISO_GRUPO_CSV = "chatbot:csv"


# --------------------------------------------------------------------- helpers

def _validar_pcrcs(pcrcs: List[str]) -> List[str]:
    normalizados = []
    for p in pcrcs:
        norm = normalizar_pcrc(p)
        if norm and norm not in normalizados:
            normalizados.append(norm)
    return normalizados


def _insertar_pcrcs(conn, chatbot_id: int, pcrcs: List[str]) -> None:
    """Los PCRC son únicos globalmente (un PCRC apunta a UN bot); si alguno ya
    está mapeado a otro bot, 409 con el detalle."""
    for pcrc in pcrcs:
        ocupado = conn.execute(text("""
            SELECT c.slug FROM pagina_web.ChatbotPcrc m
            JOIN pagina_web.Chatbots c ON c.id = m.chatbot_id
            WHERE m.pcrc_normalizado = :pcrc AND m.chatbot_id <> :cid
        """), {"pcrc": pcrc, "cid": chatbot_id}).first()
        if ocupado:
            raise HTTPException(
                status_code=409,
                detail=f"El PCRC '{pcrc}' ya está asignado al chatbot '{ocupado.slug}'.",
            )
        conn.execute(text("""
            INSERT INTO pagina_web.ChatbotPcrc (pcrc_normalizado, chatbot_id)
            VALUES (:pcrc, :cid)
        """), {"pcrc": pcrc, "cid": chatbot_id})


def _encolar_job(conn, chatbot_id: int, requested_by: Optional[int]) -> Optional[int]:
    """Inserta un job de reindexado si no hay otro pendiente/corriendo del bot."""
    existente = conn.execute(text("""
        SELECT id FROM pagina_web.ChatbotIndexJobs
        WHERE chatbot_id = :cid AND status IN ('pending', 'running')
          AND environment = :env
    """), {"cid": chatbot_id, "env": settings.ENVIRONMENT}).first()
    if existente:
        return None
    row = conn.execute(text("""
        INSERT INTO pagina_web.ChatbotIndexJobs (chatbot_id, requested_by, environment)
        OUTPUT INSERTED.id
        VALUES (:cid, :req, :env)
    """), {"cid": chatbot_id, "req": requested_by, "env": settings.ENVIRONMENT}).first()
    return row.id if row else None


def _cargar_bots(chatbot_id: Optional[int] = None) -> List[ChatbotAdminOut]:
    filtro = "WHERE c.id = :cid" if chatbot_id is not None else ""
    # index_version/status/last_indexed del ENTORNO actual (ChatbotIndexState): el panel
    # de dev muestra el índice de dev, el de prod el de prod.
    query = text(f"""
        SELECT c.id, c.slug, c.nombre, c.descripcion, c.system_prompt, c.grupo,
               c.activo,
               COALESCE(s.index_version, 0) AS index_version,
               s.index_status, s.last_indexed_at,
               p.code AS permission_code
        FROM pagina_web.Chatbots c
        JOIN pagina_web.Permissions p ON p.id = c.permission_id
        LEFT JOIN pagina_web.ChatbotIndexState s
               ON s.chatbot_id = c.id AND s.environment = :env
        {filtro}
        ORDER BY c.nombre
    """)
    params = {"env": settings.ENVIRONMENT}
    if chatbot_id is not None:
        params["cid"] = chatbot_id

    with engine.connect() as conn:
        bots = conn.execute(query, params).mappings().all()
        pcrcs = conn.execute(text("""
            SELECT chatbot_id, pcrc_normalizado FROM pagina_web.ChatbotPcrc
            ORDER BY pcrc_normalizado
        """)).mappings().all()
        jobs = conn.execute(text("""
            SELECT chatbot_id, status FROM pagina_web.ChatbotIndexJobs
            WHERE status IN ('pending', 'running') AND environment = :env
        """), {"env": settings.ENVIRONMENT}).mappings().all()
        # Cuenta los documentos que el bot INDEXA: propios + prestados por otro bot.
        # `tocado_at` es el último cambio en ese conjunto (alta, edición o baja lógica,
        # todas mueven updated_at; compartir/dejar de compartir mueve el vínculo):
        # comparado contra last_indexed_at dice si el índice quedó viejo.
        docs_md = conn.execute(text("""
            SELECT c.id AS chatbot_id,
                   (SELECT COUNT(*) FROM pagina_web.ChatbotDocMarkdown m
                     WHERE m.activo = 1
                       AND (m.chatbot_id = c.id
                            OR EXISTS (SELECT 1 FROM pagina_web.ChatbotDocVinculo v
                                       WHERE v.doc_id = m.id AND v.chatbot_id = c.id))) AS n,
                   (SELECT MAX(m.updated_at) FROM pagina_web.ChatbotDocMarkdown m
                     WHERE m.chatbot_id = c.id
                        OR EXISTS (SELECT 1 FROM pagina_web.ChatbotDocVinculo v
                                   WHERE v.doc_id = m.id AND v.chatbot_id = c.id)) AS tocado_at,
                   (SELECT MAX(v.created_at) FROM pagina_web.ChatbotDocVinculo v
                     WHERE v.chatbot_id = c.id) AS vinculado_at,
                   (SELECT COUNT(*) FROM pagina_web.ChatbotDocMaterial mat
                     WHERE mat.chatbot_id = c.id) AS material
            FROM pagina_web.Chatbots c
        """)).mappings().all()

    docs_md_por_bot: Dict[int, int] = {d["chatbot_id"]: d["n"] for d in docs_md}
    material_por_bot: Dict[int, int] = {d["chatbot_id"]: d["material"] for d in docs_md}
    tocado_por_bot: Dict[int, Optional[datetime]] = {
        d["chatbot_id"]: max([t for t in (d["tocado_at"], d["vinculado_at"]) if t], default=None)
        for d in docs_md
    }
    pcrcs_por_bot: Dict[int, List[str]] = {}
    for m in pcrcs:
        pcrcs_por_bot.setdefault(m["chatbot_id"], []).append(m["pcrc_normalizado"])
    job_por_bot: Dict[int, str] = {j["chatbot_id"]: j["status"] for j in jobs}

    return [
        ChatbotAdminOut(
            id=b["id"], slug=b["slug"], nombre=b["nombre"], descripcion=b["descripcion"],
            es_csv=(b["grupo"] == GRUPO_CSV), activo=bool(b["activo"]),
            permission_code=b["permission_code"], system_prompt=b["system_prompt"],
            index_version=b["index_version"], index_status=b["index_status"],
            last_indexed_at=b["last_indexed_at"],
            docs_md_count=docs_md_por_bot.get(b["id"], 0),
            pcrcs=pcrcs_por_bot.get(b["id"], []),
            job_pendiente=job_por_bot.get(b["id"]),
            cambios_sin_indexar=_hay_cambios_sin_indexar(
                tocado_por_bot.get(b["id"]), b["last_indexed_at"],
                docs_md_por_bot.get(b["id"], 0)),
            material_pendiente=material_por_bot.get(b["id"], 0),
        )
        for b in bots
    ]


def _hay_cambios_sin_indexar(tocado_at: Optional[datetime], last_indexed_at: Optional[datetime],
                             docs_count: int) -> bool:
    """El índice que está respondiendo, ¿incluye el estado actual de los documentos?

    Un bot sin documentos no tiene nada pendiente (no hay qué indexar), y uno que
    nunca se indexó pero ya tiene documentos sí: el conocimiento cargado todavía no
    llegó al chatbot.
    """
    if not docs_count:
        return False
    if last_indexed_at is None:
        return True
    return tocado_at is not None and tocado_at > last_indexed_at


def _get_bot_o_404(chatbot_id: int) -> ChatbotAdminOut:
    bots = _cargar_bots(chatbot_id)
    if not bots:
        raise HTTPException(status_code=404, detail="Chatbot no encontrado.")
    return bots[0]


# ------------------------------------------------------------------- endpoints

@router.get("/", response_model=List[ChatbotAdminOut])
def listar_chatbots():
    """Todos los chatbots (incluye desactivados) con PCRCs y estado de índice."""
    return _cargar_bots()


@router.get("/jobs", response_model=List[IndexJobOut])
def listar_jobs(limit: int = 30):
    """Últimos jobs de reindexado, para la tabla de estado del panel."""
    limit = max(1, min(limit, 200))
    query = text("""
        SELECT TOP (:limit) j.id, c.slug AS chatbot_slug, j.status, j.requested_by,
               j.error, j.created_at, j.started_at, j.finished_at
        FROM pagina_web.ChatbotIndexJobs j
        JOIN pagina_web.Chatbots c ON c.id = j.chatbot_id
        WHERE j.environment = :env
        ORDER BY j.id DESC
    """)
    with engine.connect() as conn:
        rows = conn.execute(query, {"limit": limit, "env": settings.ENVIRONMENT}).mappings().all()
    return [IndexJobOut(**row) for row in rows]


@router.post("/", response_model=ChatbotAdminOut, status_code=status.HTTP_201_CREATED)
def crear_chatbot(payload: ChatbotAdminCreate, current_user: User = Depends(get_current_active_user)):
    slug = payload.slug.strip().lower() if payload.slug else slugify_nombre(payload.nombre)
    if not SLUG_REGEX.match(slug):
        raise HTTPException(
            status_code=422,
            detail=f"Slug inválido '{slug}': solo minúsculas, dígitos y guión bajo (2-50 caracteres).",
        )
    if not payload.system_prompt.strip():
        raise HTTPException(status_code=422, detail="El system_prompt no puede estar vacío.")

    pcrcs = _validar_pcrcs(payload.pcrcs) if payload.es_csv else []

    try:
        # Una sola transacción: permiso + bot + pcrcs.
        with engine.begin() as conn:
            existe = conn.execute(
                text("SELECT id FROM pagina_web.Chatbots WHERE slug = :slug"), {"slug": slug}
            ).first()
            if existe:
                raise HTTPException(status_code=409, detail=f"Ya existe un chatbot con slug '{slug}'.")

            if payload.es_csv:
                perm = conn.execute(
                    text("SELECT id FROM pagina_web.Permissions WHERE code = :code"),
                    {"code": PERMISO_GRUPO_CSV},
                ).first()
                if not perm:
                    raise HTTPException(status_code=500, detail=f"Falta el permiso '{PERMISO_GRUPO_CSV}' en la BD.")
                permission_id = perm.id
            else:
                perm_code = f"chatbot:{slug}"
                perm = conn.execute(
                    text("SELECT id FROM pagina_web.Permissions WHERE code = :code"),
                    {"code": perm_code},
                ).first()
                if perm:
                    raise HTTPException(status_code=409, detail=f"Ya existe el permiso '{perm_code}'.")
                permission_id = conn.execute(text("""
                    INSERT INTO pagina_web.Permissions (code, description)
                    OUTPUT INSERTED.id
                    VALUES (:code, :descr)
                """), {"code": perm_code, "descr": f"Usar el chatbot de {payload.nombre}"}).first().id

            chatbot_id = conn.execute(text("""
                INSERT INTO pagina_web.Chatbots (slug, nombre, descripcion, system_prompt, grupo, permission_id)
                OUTPUT INSERTED.id
                VALUES (:slug, :nombre, :descripcion, :prompt, :grupo, :perm_id)
            """), {
                "slug": slug, "nombre": payload.nombre.strip(),
                "descripcion": payload.descripcion,
                "prompt": payload.system_prompt,
                "grupo": GRUPO_CSV if payload.es_csv else None,
                "perm_id": permission_id,
            }).first().id

            _insertar_pcrcs(conn, chatbot_id, pcrcs)
            # Sin reindexado inicial: el bot nace sin conocimiento. Los documentos se
            # cargan después con el asistente de documentación, que ya ofrece reindexar.
    except IntegrityError as e:
        logger.error(f"Error de integridad creando chatbot '{slug}': {e}")
        raise HTTPException(status_code=409, detail="Conflicto de datos al crear el chatbot (slug/permiso/PCRC duplicado).")

    logger.info(f"Usuario {current_user.usuario} creó el chatbot '{slug}' (id {chatbot_id}).")
    return _get_bot_o_404(chatbot_id)


@router.put("/{chatbot_id}", response_model=ChatbotAdminOut)
def actualizar_chatbot(chatbot_id: int, payload: ChatbotAdminUpdate, current_user: User = Depends(get_current_active_user)):
    bot = _get_bot_o_404(chatbot_id)

    pcrcs = None
    if payload.pcrcs is not None:
        if not bot.es_csv and payload.pcrcs:
            raise HTTPException(status_code=422, detail="Solo los chatbots del grupo CSV llevan mapeo PCRC.")
        pcrcs = _validar_pcrcs(payload.pcrcs)
    if payload.system_prompt is not None and not payload.system_prompt.strip():
        raise HTTPException(status_code=422, detail="El system_prompt no puede estar vacío.")

    try:
        with engine.begin() as conn:
            # updated_at siempre se bumpea: dispara el hot-reload del registry.
            conn.execute(text("""
                UPDATE pagina_web.Chatbots
                SET nombre        = COALESCE(:nombre, nombre),
                    descripcion   = COALESCE(:descripcion, descripcion),
                    system_prompt = COALESCE(:prompt, system_prompt),
                    updated_at    = SYSDATETIME()
                WHERE id = :cid
            """), {
                "nombre": payload.nombre, "descripcion": payload.descripcion,
                "prompt": payload.system_prompt, "cid": chatbot_id,
            })

            if pcrcs is not None:
                # Reemplazo completo (mismo patrón que roles.py con RolePermissions).
                conn.execute(text("DELETE FROM pagina_web.ChatbotPcrc WHERE chatbot_id = :cid"),
                             {"cid": chatbot_id})
                _insertar_pcrcs(conn, chatbot_id, pcrcs)
    except IntegrityError as e:
        logger.error(f"Error de integridad actualizando chatbot {chatbot_id}: {e}")
        raise HTTPException(status_code=409, detail="Conflicto de datos al actualizar el chatbot (doc/PCRC duplicado).")

    logger.info(f"Usuario {current_user.usuario} actualizó el chatbot '{bot.slug}'.")
    return _get_bot_o_404(chatbot_id)


@router.post("/{chatbot_id}/activar", response_model=ChatbotAdminOut)
def activar_chatbot(chatbot_id: int, current_user: User = Depends(get_current_active_user)):
    return _set_activo(chatbot_id, True, current_user)


@router.post("/{chatbot_id}/desactivar", response_model=ChatbotAdminOut)
def desactivar_chatbot(chatbot_id: int, current_user: User = Depends(get_current_active_user)):
    """Desactiva el bot (deja de servirse y de aparecer en selectores). No borra
    el permiso, los docs ni las colecciones de Qdrant: reactivar es instantáneo."""
    return _set_activo(chatbot_id, False, current_user)


def _set_activo(chatbot_id: int, activo: bool, current_user: User) -> ChatbotAdminOut:
    bot = _get_bot_o_404(chatbot_id)
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE pagina_web.Chatbots
            SET activo = :activo, updated_at = SYSDATETIME()
            WHERE id = :cid
        """), {"activo": 1 if activo else 0, "cid": chatbot_id})
    logger.info(f"Usuario {current_user.usuario} {'activó' if activo else 'desactivó'} el chatbot '{bot.slug}'.")
    return _get_bot_o_404(chatbot_id)


@router.post("/{chatbot_id}/reindex", response_model=ReindexResponse, status_code=status.HTTP_202_ACCEPTED)
def reindexar_chatbot(chatbot_id: int, current_user: User = Depends(get_current_active_user)):
    """Encola un reindexado: materializa los documentos del bot y reconstruye el
    índice. Lo procesa el scheduler (en <=60s); mientras tanto el índice viejo
    sigue respondiendo."""
    bot = _get_bot_o_404(chatbot_id)
    if not bot.docs_md_count:
        raise HTTPException(status_code=422, detail="El chatbot no tiene documentos del conocimiento cargados.")

    with engine.begin() as conn:
        job_id = _encolar_job(conn, chatbot_id, current_user.usuario)
    if job_id is None:
        raise HTTPException(status_code=409, detail="Ya hay un reindexado pendiente o en curso para este chatbot.")

    logger.info(f"Usuario {current_user.usuario} encoló el reindexado del chatbot '{bot.slug}' (job {job_id}).")
    return ReindexResponse(job_id=job_id, status="pending")


# ------------------------------------------- Asistente de documentación RAG

# Tope por archivo crudo subido para formatear. La File API de Gemini admite más,
# pero acotamos para no bufferear archivos enormes en memoria del backend.
MAX_ARCHIVO_BYTES = 20 * 1024 * 1024

# El navegador no siempre sabe el tipo de un Office (llega vacío o como
# application/octet-stream): mimetypes tampoco los trae en todas las máquinas, así
# que los mapeamos nosotros.
MIME_POR_EXTENSION = {
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".pdf": "application/pdf",
    ".csv": "text/csv",
    ".md": "text/markdown",
    ".txt": "text/plain",
}


def _normalizar_fuentes(fuentes: List[FuenteCrudaIn], exigir_contenido: bool = True) -> List[Dict]:
    """Convierte las FuenteCrudaIn (texto o archivo base64) al formato que consume
    asistente_docs (los archivos se decodifican a bytes). 422 si algo viene mal.

    `exigir_contenido=False` cuando el material puede venir de la bandeja y no en la
    request: ahí la lista vacía es válida y el 422 lo tira quien junta las dos partes.
    """
    out: List[Dict] = []
    for f in fuentes:
        if f.tipo == "texto":
            if (f.texto or "").strip():
                out.append({"tipo": "texto", "texto": f.texto, "nombre": f.nombre})
        elif f.tipo == "archivo":
            if not f.datos_base64:
                raise HTTPException(status_code=422, detail=f"El archivo '{f.nombre or ''}' vino sin datos.")
            mime = (f.mime or "").strip().lower()
            if not mime or mime == "application/octet-stream":
                # Windows manda los Office sin tipo o como octet-stream según cómo
                # esté registrada la extensión: lo resolvemos por el nombre.
                nombre_lower = (f.nombre or "").lower()
                extension = "." + nombre_lower.rsplit(".", 1)[-1] if "." in nombre_lower else ""
                mime = MIME_POR_EXTENSION.get(extension) or (mimetypes.guess_type(f.nombre or "")[0] or "")
            if not mime:
                raise HTTPException(status_code=422, detail=f"El archivo '{f.nombre or ''}' vino sin tipo.")
            try:
                datos = base64.b64decode(f.datos_base64, validate=True)
            except Exception:
                raise HTTPException(status_code=422, detail=f"El archivo '{f.nombre or ''}' no está en base64 válido.")
            if len(datos) > MAX_ARCHIVO_BYTES:
                raise HTTPException(status_code=413, detail=f"El archivo '{f.nombre or ''}' supera el máximo de 20 MB.")
            # El Office viejo (binario) no lo puede leer nadie: ni la conversión propia
            # ni Gemini. Se rechaza acá, al subirlo, y no cuando corre el trabajo:
            # el usuario está mirando la pantalla y puede volver a guardarlo como .docx.
            if office_a_texto.es_office_legacy(datos):
                raise HTTPException(status_code=422, detail=(
                    f"'{f.nombre or 'El archivo'}' está guardado en el formato viejo de Office "
                    f"(.doc/.xls/.ppt), que no se puede leer. Abrilo en Office y guardalo como "
                    f".docx/.xlsx/.pptx, o exportalo a PDF."
                ))
            out.append({"tipo": "archivo", "datos": datos, "mime": mime, "nombre": f.nombre})
        else:
            raise HTTPException(status_code=422, detail=f"Tipo de fuente desconocido: {f.tipo}")
    if not out and exigir_contenido:
        raise HTTPException(status_code=422, detail="Pegá texto o subí al menos un archivo con contenido.")
    return out


# El asistente NO corre acá: encola en pagina_web.ChatbotDocJobs y lo procesa el
# scheduler. Son 45s a 3min de llamadas a Gemini (ver app/doc_jobs.py), muy por
# encima del timeout del worker. La request solo valida y devuelve el job_id.


def _material_de_la_corrida(chatbot_id: Optional[int], material_ids: Optional[List[int]],
                            fuentes_inline: List[Dict]) -> List[int]:
    """Congela qué ítems de la bandeja entran en esta corrida.

    `material_ids=None` significa "todo lo pendiente", pero se resuelve ACÁ a una
    lista fija: si alguien suma material mientras el job corre, ese material queda
    para la próxima en vez de procesarse a medias (o de borrarse sin haber entrado
    en la propuesta cuando el usuario la acepta).
    """
    pendientes = doc_material.ids_pendientes(chatbot_id) if chatbot_id else []
    if material_ids is None:
        ids = pendientes
    else:
        conocidos = set(pendientes)
        faltantes = [i for i in material_ids if i not in conocidos]
        if faltantes:
            raise HTTPException(
                status_code=404,
                detail="Parte del material seleccionado ya no está en la bandeja "
                       f"(ids {faltantes}). Actualizá la página.",
            )
        ids = list(material_ids)

    if not ids and not fuentes_inline:
        raise HTTPException(
            status_code=422,
            detail="No hay material para procesar: sumá contenido a la bandeja o pegá texto.",
        )
    return ids


def _encolar_doc_job(**kwargs) -> int:
    """Encola, o 409 si el bot ya tiene un trabajo abierto (ver doc_jobs.TrabajoEnCurso).

    El `job_id` del que ya está viaja en el detalle para que el panel se enganche a ese
    en vez de pagar el mismo procesamiento dos veces."""
    try:
        return doc_jobs.encolar(**kwargs)
    except doc_jobs.TrabajoEnCurso as e:
        raise HTTPException(status_code=409, detail={"mensaje": str(e), "job_id": e.job_id})


@router.post("/docs/formatear", response_model=DocJobOut, status_code=202)
def formatear_doc(payload: DocFormatearRequest, current_user: User = Depends(get_current_active_user)):
    """Encola la transformación de contenido crudo (texto/archivos) en markdown RAG.

    El material sale de la bandeja del bot (`material_ids`) y/o de lo que venga en
    `fuentes`. El resultado se consulta con GET /docs/jobs/{job_id}. Puede terminar
    en VARIOS documentos si el material mezcla temas independientes. Es una
    PROPUESTA: no guarda nada hasta que el usuario la acepta.
    """
    # Valida acá (mime, base64, tamaño) para que el error salga en el acto y no
    # dentro de un job que el usuario tiene que esperar para enterarse.
    fuentes = _normalizar_fuentes(payload.fuentes, exigir_contenido=False)
    material_ids = _material_de_la_corrida(payload.chatbot_id, payload.material_ids, fuentes)

    datos = payload.model_dump()
    datos["material_ids"] = material_ids
    job_id = _encolar_doc_job(
        tipo="formatear", chatbot_id=payload.chatbot_id,
        payload=datos, requested_by=current_user.usuario,
    )
    return DocJobOut(job_id=job_id, status="pending")


@router.post("/docs/merge", response_model=DocJobOut, status_code=202)
def merge_doc(payload: DocMergeRequest, current_user: User = Depends(get_current_active_user)):
    """Encola la integración de contenido nuevo en la documentación existente del bot.

    Al ejecutarse, la IA rutea cada bloque al documento que le corresponde (puede
    modificar VARIOS a la vez y crear uno nuevo si nada encaja), deduplica y marca
    conflictos. El resultado se consulta con GET /docs/jobs/{job_id}.
    """
    _get_bot_o_404(payload.chatbot_id)
    fuentes = _normalizar_fuentes(payload.fuentes, exigir_contenido=False)
    material_ids = _material_de_la_corrida(payload.chatbot_id, payload.material_ids, fuentes)

    datos = payload.model_dump()
    datos["material_ids"] = material_ids
    job_id = _encolar_doc_job(
        tipo="merge", chatbot_id=payload.chatbot_id,
        payload=datos, requested_by=current_user.usuario,
    )
    return DocJobOut(job_id=job_id, status="pending")


# ------------------------------------------- Bandeja de material pendiente

# El material se junta primero y se procesa UNA vez, en lugar de mandar cada archivo
# a la IA apenas se sube: cada corrida regenera el markdown completo de los
# documentos que toca, así que cargar de a poco multiplicaba el trabajo (y el costo)
# por la cantidad de veces que se agregó algo.


@router.get("/{chatbot_id}/docs-material", response_model=List[DocMaterialOut])
def listar_material(chatbot_id: int):
    """Material pendiente del bot (metadatos; los archivos no viajan)."""
    _get_bot_o_404(chatbot_id)
    return [DocMaterialOut(**m) for m in doc_material.listar(chatbot_id)]


@router.post("/{chatbot_id}/docs-material", response_model=List[DocMaterialOut],
             status_code=status.HTTP_201_CREATED)
def agregar_material(chatbot_id: int, payload: DocMaterialIn,
                     current_user: User = Depends(get_current_active_user)):
    """Guarda material para procesarlo después. NO llama a la IA."""
    bot = _get_bot_o_404(chatbot_id)
    fuentes = _normalizar_fuentes(payload.fuentes)

    nuevo = sum(len(f["datos"]) if f["tipo"] == "archivo" else len(f["texto"].encode("utf-8"))
                for f in fuentes)
    if doc_material.total_bytes(chatbot_id) + nuevo > doc_material.MAX_MATERIAL_BOT_BYTES:
        tope_mb = doc_material.MAX_MATERIAL_BOT_BYTES // (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=f"La bandeja de material de este chatbot no puede pasar de {tope_mb} MB. "
                   "Procesá lo que ya está cargado o quitá lo que no vaya.",
        )

    if payload.doc_id_destino is not None:
        _get_doc_md_o_404(chatbot_id, payload.doc_id_destino)  # 404 si no es de este bot

    doc_material.agregar(chatbot_id, fuentes, payload.nota, current_user.usuario,
                         doc_id_destino=payload.doc_id_destino,
                         es_actualizacion=payload.es_actualizacion)
    logger.info(f"Usuario {current_user.usuario} sumó {len(fuentes)} ítem(s) al material "
                f"pendiente del chatbot '{bot.slug}'.")
    return [DocMaterialOut(**m) for m in doc_material.listar(chatbot_id)]


@router.delete("/{chatbot_id}/docs-material/{material_id}", status_code=status.HTTP_204_NO_CONTENT)
def quitar_material(chatbot_id: int, material_id: int,
                    current_user: User = Depends(get_current_active_user)):
    """Saca un ítem de la bandeja sin procesarlo."""
    _get_bot_o_404(chatbot_id)
    if not doc_material.borrar(chatbot_id, material_id):
        raise HTTPException(status_code=404, detail="Ese material ya no está en la bandeja.")
    logger.info(f"Usuario {current_user.usuario} quitó el material {material_id} del chatbot {chatbot_id}.")


@router.delete("/{chatbot_id}/docs-material", status_code=status.HTTP_204_NO_CONTENT)
def vaciar_material(chatbot_id: int, current_user: User = Depends(get_current_active_user)):
    """Vacía la bandeja del bot (el material se descarta sin procesar)."""
    _get_bot_o_404(chatbot_id)
    borrados = doc_material.vaciar(chatbot_id)
    logger.info(f"Usuario {current_user.usuario} vació el material pendiente del chatbot "
                f"{chatbot_id} ({borrados} ítems).")


# --------------------------------------------- Imágenes (pagina_web.ChatbotImagenes)
@router.get("/{chatbot_id}/imagenes", response_model=List[ChatbotImagenOut])
def listar_imagenes_bot(chatbot_id: int, current_user: User = Depends(get_current_active_user)):
    """Lista las imágenes disponibles de un chatbot."""
    _get_bot_o_404(chatbot_id)
    return [ChatbotImagenOut(**img) for img in chatbot_imagenes.listar_imagenes(chatbot_id)]


@router.post("/{chatbot_id}/imagenes", response_model=ChatbotImagenOut, status_code=status.HTTP_201_CREATED)
async def subir_imagen_bot(
    chatbot_id: int,
    request: Request,
    current_user: User = Depends(get_current_active_user),
):
    """Sube una imagen manual para el chatbot. Acepta multipart/form-data o JSON base64."""
    _get_bot_o_404(chatbot_id)
    content_type = (request.headers.get("content-type") or "").lower()

    if "application/json" in content_type:
        payload = await request.json()
        nombre = payload.get("nombre") or "captura.png"
        mime = payload.get("mime")
        desc = payload.get("descripcion")
        b64 = payload.get("datos_base64")
        if not b64:
            raise HTTPException(status_code=400, detail="Faltan los datos base64 de la imagen.")
        try:
            datos = base64.b64decode(b64)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Base64 inválido: {e}")
    elif "multipart/form-data" in content_type:
        form = await request.form()
        archivo = form.get("archivo")
        if not archivo or not hasattr(archivo, "read"):
            raise HTTPException(status_code=400, detail="No se recibió ningún archivo de imagen.")
        nombre = getattr(archivo, "filename", None) or "captura.png"
        mime = getattr(archivo, "content_type", None)
        desc = form.get("descripcion")
        datos = await archivo.read()
    else:
        raise HTTPException(status_code=415, detail="Tipo de contenido no soportado (use multipart/form-data o application/json).")

    try:
        guardada = chatbot_imagenes.guardar_imagen(
            chatbot_id=chatbot_id,
            nombre=nombre,
            datos=datos,
            mime=mime,
            descripcion=desc,
            user_id=current_user.usuario,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Error al guardar imagen de chatbot")
        raise HTTPException(status_code=500, detail=f"Error interno al guardar imagen: {e}")

    return ChatbotImagenOut(**guardada)


@router.delete("/{chatbot_id}/imagenes/{imagen_id}", status_code=status.HTTP_204_NO_CONTENT)
def borrar_imagen_bot(chatbot_id: int, imagen_id: int, current_user: User = Depends(get_current_active_user)):
    """Elimina una imagen de la base de datos."""
    _get_bot_o_404(chatbot_id)
    if not chatbot_imagenes.borrar_imagen(chatbot_id, imagen_id):
        raise HTTPException(status_code=404, detail="Esa imagen no existe o no pertenece a este chatbot.")
    logger.info(f"Usuario {current_user.usuario} eliminó imagen {imagen_id} del chatbot {chatbot_id}.")


@router.get("/docs/jobs/{job_id}", response_model=DocJobEstadoOut)
def estado_doc_job(job_id: int, current_user: User = Depends(get_current_active_user)):
    """Estado del trabajo del asistente, para el polling del navegador.

    Con status='done' viaja la propuesta en `resultado`; con 'failed', el motivo en
    `error` (es el texto que se le muestra al usuario).
    """
    job = doc_jobs.obtener(job_id)
    if not job or job["environment"] != settings.ENVIRONMENT:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado.")

    return DocJobEstadoOut(
        job_id=job["id"], status=job["status"], tipo=job["tipo"], error=job["error"],
        resultado=DocPropuestaOut(**job["resultado"]) if job["resultado"] else None,
        cancelado=job["cancelado"], segundos=job["segundos"], adelante=job["adelante"],
        en_curso_bot=job["en_curso_bot"], en_curso_segundos=job["en_curso_segundos"],
    )


@router.post("/docs/jobs/{job_id}/cancelar", response_model=DocJobEstadoOut)
def cancelar_doc_job(job_id: int, current_user: User = Depends(get_current_active_user)):
    """Cancela un trabajo del asistente en cola o corriendo (409 si ya terminó).

    El material queda en la bandeja. Si estaba corriendo, el scheduler lo corta antes de
    la próxima llamada a Gemini (ver doc_jobs.cancelar)."""
    job = doc_jobs.obtener(job_id)
    if not job or job["environment"] != settings.ENVIRONMENT:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado.")
    previo = doc_jobs.cancelar(job_id)
    if previo is None:
        raise HTTPException(status_code=409, detail="El trabajo ya había terminado: no hay nada que cancelar.")
    logger.info(f"Usuario {current_user.usuario} canceló el job de documentación {job_id} (estaba {previo}).")
    return estado_doc_job(job_id, current_user)


@router.get("/{chatbot_id}/docs/jobs/actual", response_model=Optional[DocJobActualOut])
def trabajo_doc_actual(chatbot_id: int, current_user: User = Depends(get_current_active_user)):
    """Lo que el panel retoma al abrir el bot: su trabajo abierto, o la última propuesta
    que terminó y no se guardó (ver doc_jobs.trabajo_actual_del_bot). null si no hay nada."""
    _get_bot_o_404(chatbot_id)
    actual = doc_jobs.trabajo_actual_del_bot(chatbot_id)
    return DocJobActualOut(**actual) if actual else None


# ---------------------------------- CRUD del markdown in-app (ChatbotDocMarkdown)

def _get_doc_md_o_404(chatbot_id: int, doc_id: int) -> DocMarkdownOut:
    """Documento PROPIO del bot. Los compartidos se editan en el bot dueño, así que
    acá se exige la propiedad a propósito (404 si es prestado)."""
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT id, chatbot_id, titulo, orden, contenido_md, activo, updated_at, updated_by
            FROM pagina_web.ChatbotDocMarkdown
            WHERE id = :did AND chatbot_id = :cid AND activo = 1
        """), {"did": doc_id, "cid": chatbot_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Documento no encontrado.")
    return DocMarkdownOut(**row)


@router.get("/{chatbot_id}/docs-md", response_model=List[DocMarkdownOut])
def listar_docs_md(chatbot_id: int):
    """Documentos markdown que el bot indexa: los propios y los COMPARTIDOS por otro bot.

    Los prestados vienen marcados (`compartido`) con el slug de su dueño: se muestran para
    que se vea el conocimiento real del bot, pero se editan donde viven."""
    _get_bot_o_404(chatbot_id)
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT m.id, m.chatbot_id, m.titulo, m.orden, m.contenido_md, m.activo,
                   m.updated_at, m.updated_by,
                   CASE WHEN m.chatbot_id = :cid THEN 0 ELSE 1 END AS compartido,
                   duenio.slug AS propietario_slug
            FROM pagina_web.ChatbotDocMarkdown m
            JOIN pagina_web.Chatbots duenio ON duenio.id = m.chatbot_id
            WHERE m.activo = 1
              AND (m.chatbot_id = :cid
                   OR EXISTS (SELECT 1 FROM pagina_web.ChatbotDocVinculo v
                              WHERE v.doc_id = m.id AND v.chatbot_id = :cid))
            ORDER BY m.orden, m.id
        """), {"cid": chatbot_id}).mappings().all()
        # A qué bots se prestó cada documento propio (para mostrarlo en el panel).
        prestados: Dict[int, List[str]] = {}
        for v in conn.execute(text("""
            SELECT v.doc_id, c.slug FROM pagina_web.ChatbotDocVinculo v
            JOIN pagina_web.Chatbots c ON c.id = v.chatbot_id
            JOIN pagina_web.ChatbotDocMarkdown m ON m.id = v.doc_id
            WHERE m.chatbot_id = :cid
            ORDER BY c.slug
        """), {"cid": chatbot_id}).mappings().all():
            prestados.setdefault(v["doc_id"], []).append(v["slug"])

    salida = []
    for r in rows:
        d = dict(r)
        d["compartido"] = bool(d["compartido"])
        d["compartido_con"] = prestados.get(d["id"], [])
        salida.append(DocMarkdownOut(**d))
    return salida


@router.put("/{chatbot_id}/docs-md/{doc_id}/compartir", response_model=DocMarkdownOut)
def compartir_doc_md(chatbot_id: int, doc_id: int, payload: DocCompartirIn,
                     current_user: User = Depends(get_current_active_user)):
    """Define con qué otros bots se comparte un documento (reemplazo completo).

    Compartir evita duplicar el mismo conocimiento en dos bots, que es la forma segura de
    que terminen desincronizados. El documento se sigue editando en su bot dueño; los
    demás lo indexan. Hay que reindexar los bots afectados para que tome efecto."""
    _get_doc_md_o_404(chatbot_id, doc_id)  # 404 si no es propio del bot

    slugs = [s.strip().lower() for s in payload.slugs if s and s.strip()]
    with engine.begin() as conn:
        destinos: List[int] = []
        for slug in dict.fromkeys(slugs):  # dedup conservando el orden
            row = conn.execute(text("SELECT id FROM pagina_web.Chatbots WHERE slug = :s"),
                               {"s": slug}).first()
            if not row:
                raise HTTPException(status_code=404, detail=f"No existe el chatbot '{slug}'.")
            if row.id == chatbot_id:
                continue  # compartirlo con su propio dueño no significa nada
            destinos.append(row.id)

        conn.execute(text("DELETE FROM pagina_web.ChatbotDocVinculo WHERE doc_id = :did"),
                     {"did": doc_id})
        for cid_destino in destinos:
            conn.execute(text("""
                INSERT INTO pagina_web.ChatbotDocVinculo (doc_id, chatbot_id) VALUES (:did, :cid)
            """), {"did": doc_id, "cid": cid_destino})

    logger.info(f"Usuario {current_user.usuario} compartió el doc {doc_id} con {slugs or '(nadie)'}.")
    return next(d for d in listar_docs_md(chatbot_id) if d.id == doc_id)


@router.post("/{chatbot_id}/docs-md", response_model=DocMarkdownOut, status_code=status.HTTP_201_CREATED)
def crear_doc_md(chatbot_id: int, payload: DocMarkdownIn, current_user: User = Depends(get_current_active_user)):
    """Persiste un documento markdown aceptado por el usuario. Opcionalmente encola reindex."""
    bot = _get_bot_o_404(chatbot_id)
    if not (payload.contenido_md or "").strip():
        raise HTTPException(status_code=422, detail="El contenido del documento no puede estar vacío.")
    with engine.begin() as conn:
        doc_id = conn.execute(text("""
            INSERT INTO pagina_web.ChatbotDocMarkdown (chatbot_id, titulo, orden, contenido_md, updated_by)
            OUTPUT INSERTED.id
            VALUES (:cid, :titulo, :orden, :md, :uid)
        """), {"cid": chatbot_id, "titulo": payload.titulo, "orden": payload.orden,
               "md": payload.contenido_md, "uid": current_user.usuario}).first().id
        if payload.reindex:
            _encolar_job(conn, chatbot_id, current_user.usuario)
    logger.info(f"Usuario {current_user.usuario} creó el doc markdown {doc_id} del chatbot '{bot.slug}'.")
    return _get_doc_md_o_404(chatbot_id, doc_id)


@router.post("/{chatbot_id}/docs-md/bulk", response_model=List[DocMarkdownOut])
def guardar_docs_md_bulk(chatbot_id: int, payload: DocMarkdownBulkIn,
                         current_user: User = Depends(get_current_active_user)):
    """Guarda de una vez todos los documentos de una propuesta multi-documento: crea los
    que vienen sin id y actualiza los que lo traen. Todo en UNA transacción (o entra la
    propuesta completa, o no entra nada).

    En la misma transacción se consume el material de la bandeja que dio origen a la
    propuesta: si el guardado se cae, el material sigue pendiente y se reprocesa.
    El reindexado NO se encola solo (ver DocMarkdownBulkIn.reindex)."""
    bot = _get_bot_o_404(chatbot_id)
    if not payload.documentos:
        raise HTTPException(status_code=422, detail="No hay documentos para guardar.")
    for doc in payload.documentos:
        if not (doc.contenido_md or "").strip():
            raise HTTPException(status_code=422, detail=f"El documento '{doc.titulo or ''}' está vacío.")

    ids_afectados: List[int] = []
    with engine.begin() as conn:
        # Los documentos nuevos sin orden explícito se agregan al final.
        siguiente_orden = (conn.execute(text("""
            SELECT COALESCE(MAX(orden), 0) FROM pagina_web.ChatbotDocMarkdown
            WHERE chatbot_id = :cid AND activo = 1
        """), {"cid": chatbot_id}).scalar() or 0) + 1

        for doc in payload.documentos:
            if doc.id is None:
                orden = doc.orden if doc.orden is not None else siguiente_orden
                siguiente_orden = max(siguiente_orden, orden) + 1
                nuevo_id = conn.execute(text("""
                    INSERT INTO pagina_web.ChatbotDocMarkdown (chatbot_id, titulo, orden, contenido_md, updated_by)
                    OUTPUT INSERTED.id
                    VALUES (:cid, :titulo, :orden, :md, :uid)
                """), {"cid": chatbot_id, "titulo": doc.titulo, "orden": orden,
                       "md": doc.contenido_md, "uid": current_user.usuario}).first().id
                ids_afectados.append(nuevo_id)
            else:
                # El WHERE por chatbot_id evita editar un documento de otro bot.
                actualizado = conn.execute(text("""
                    UPDATE pagina_web.ChatbotDocMarkdown
                    SET titulo = COALESCE(:titulo, titulo),
                        orden = COALESCE(:orden, orden),
                        contenido_md = :md,
                        updated_by = :uid, updated_at = SYSDATETIME()
                    WHERE id = :did AND chatbot_id = :cid AND activo = 1
                """), {"titulo": doc.titulo, "orden": doc.orden, "md": doc.contenido_md,
                       "uid": current_user.usuario, "did": doc.id, "cid": chatbot_id})
                if not actualizado.rowcount:
                    raise HTTPException(status_code=404, detail=f"El documento {doc.id} no existe en este chatbot.")
                ids_afectados.append(doc.id)

        consumidos = doc_material.consumir(conn, chatbot_id, payload.material_ids)

        if payload.reindex:
            _encolar_job(conn, chatbot_id, current_user.usuario)

    logger.info(f"Usuario {current_user.usuario} guardó {len(ids_afectados)} doc(s) markdown "
                f"del chatbot '{bot.slug}' (bulk); {consumidos} ítem(s) de material consumidos.")
    return [_get_doc_md_o_404(chatbot_id, did) for did in ids_afectados]


@router.put("/{chatbot_id}/docs-md/{doc_id}", response_model=DocMarkdownOut)
def actualizar_doc_md(chatbot_id: int, doc_id: int, payload: DocMarkdownIn,
                      current_user: User = Depends(get_current_active_user)):
    """Edita un documento markdown in-app (contenido/título/orden). Opcionalmente reindex."""
    _get_doc_md_o_404(chatbot_id, doc_id)  # 404 si no existe
    if not (payload.contenido_md or "").strip():
        raise HTTPException(status_code=422, detail="El contenido del documento no puede estar vacío.")
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE pagina_web.ChatbotDocMarkdown
            SET titulo = :titulo, orden = :orden, contenido_md = :md,
                updated_by = :uid, updated_at = SYSDATETIME()
            WHERE id = :did AND chatbot_id = :cid
        """), {"titulo": payload.titulo, "orden": payload.orden, "md": payload.contenido_md,
               "uid": current_user.usuario, "did": doc_id, "cid": chatbot_id})
        if payload.reindex:
            _encolar_job(conn, chatbot_id, current_user.usuario)
    logger.info(f"Usuario {current_user.usuario} actualizó el doc markdown {doc_id} del chatbot {chatbot_id}.")
    return _get_doc_md_o_404(chatbot_id, doc_id)


@router.delete("/{chatbot_id}/docs-md/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def borrar_doc_md(chatbot_id: int, doc_id: int, reindex: bool = False,
                  current_user: User = Depends(get_current_active_user)):
    """Baja lógica de un documento markdown (activo=0). Con ?reindex=true encola el
    reindexado para que el conocimiento borrado salga del índice."""
    _get_doc_md_o_404(chatbot_id, doc_id)  # 404 si no existe
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE pagina_web.ChatbotDocMarkdown
            SET activo = 0, updated_by = :uid, updated_at = SYSDATETIME()
            WHERE id = :did AND chatbot_id = :cid
        """), {"uid": current_user.usuario, "did": doc_id, "cid": chatbot_id})
        if reindex:
            _encolar_job(conn, chatbot_id, current_user.usuario)
    logger.info(f"Usuario {current_user.usuario} borró (lógico) el doc markdown {doc_id} del chatbot {chatbot_id}.")


# ---------------------------------------------------- Tablas de datos del bot
#
# Una tabla es una fuente PARALELA a los documentos markdown: no se indexa en
# Qdrant y el bot la consulta antes del retrieval (ver app/chatbot_tablas.py).
# Por eso guardar o borrar una tabla NO encola un reindexado: no hay nada que
# reconstruir, y el cambio se ve en <= CHATBOT_TABLAS_TTL_SECONDS.


@router.get("/{chatbot_id}/tablas", response_model=List[TablaOut])
def listar_tablas(chatbot_id: int):
    """Definición de las tablas del bot (sin las filas)."""
    _get_bot_o_404(chatbot_id)
    return [TablaOut(**t) for t in chatbot_tablas_admin.listar(chatbot_id)]


@router.get("/{chatbot_id}/tablas/{tabla_id}/filas", response_model=TablaFilasOut)
def listar_filas(chatbot_id: int, tabla_id: int, limit: int = 50, offset: int = 0,
                 q: Optional[str] = None):
    """Filas paginadas, con búsqueda sobre las columnas clave.

    Es la forma de verificar una carga sin abrir la base: la cartera de cobranzas
    tiene miles de filas y lo único que se quiere mirar es si tal cliente está.
    """
    _get_bot_o_404(chatbot_id)
    return TablaFilasOut(**chatbot_tablas_admin.obtener_filas(
        chatbot_id, tabla_id, limit=limit, offset=offset, q=q
    ))


@router.post("/{chatbot_id}/tablas", response_model=TablaOut,
             status_code=status.HTTP_201_CREATED)
def crear_tabla(chatbot_id: int, payload: TablaGuardarIn,
                current_user: User = Depends(get_current_active_user)):
    """Acepta una propuesta de tabla y la guarda con sus filas.

    Si vino de convertir un documento, ese documento se desactiva en el mismo
    acto: dejar las dos fuentes vivas es garantizar que se desincronicen, y
    además el documento seguiría inundando el índice vectorial con las filas que
    justamente se sacaron de ahí.
    """
    _get_bot_o_404(chatbot_id)
    if not payload.columnas:
        raise HTTPException(status_code=422, detail="La tabla no tiene columnas.")
    if not payload.filas:
        raise HTTPException(status_code=422, detail="La tabla no tiene filas.")

    tabla_id = chatbot_tablas_admin.guardar(
        chatbot_id, payload.model_dump(), created_by=current_user.usuario
    )

    if payload.material_ids:
        # El material se consume DESPUÉS de que la tabla existe, no en la misma
        # transacción: si se cayera acá, el material sigue pendiente y a lo sumo se
        # reprocesa (se ve y se borra a mano). Al revés —material consumido y tabla
        # sin guardar— se perdería la fuente.
        with engine.begin() as conn:
            doc_material.consumir(conn, chatbot_id, payload.material_ids)

    if payload.doc_origen_ids and payload.desactivar_doc_origen:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE pagina_web.ChatbotDocMarkdown
                SET activo = 0, updated_by = :uid, updated_at = SYSDATETIME()
                WHERE id IN :ids AND chatbot_id = :cid
            """).bindparams(bindparam("ids", expanding=True)),
                {"uid": current_user.usuario, "ids": list(payload.doc_origen_ids),
                 "cid": chatbot_id})
            # Acá sí hace falta reindexar: los documentos salieron del corpus y sus
            # chunks tienen que dejar de competir en el retrieval.
            _encolar_job(conn, chatbot_id, current_user.usuario)
        logger.info(
            f"Doc(s) {payload.doc_origen_ids} del chatbot {chatbot_id} desactivados: "
            f"pasaron a ser la tabla {tabla_id}."
        )

    logger.info(f"Usuario {current_user.usuario} creó la tabla {tabla_id} "
                f"({len(payload.filas)} filas) del chatbot {chatbot_id}.")
    return _get_tabla_o_404(chatbot_id, tabla_id)


@router.put("/{chatbot_id}/tablas/{tabla_id}", response_model=TablaOut)
def actualizar_tabla(chatbot_id: int, tabla_id: int, payload: TablaMetadataIn,
                     current_user: User = Depends(get_current_active_user)):
    """Edita la definición: nombre, descripción, términos, columnas clave, nota y
    modo. Las filas se editan por /tablas/{id}/filas.

    Cambiar las CLAVES recalcula la columna `busqueda` de todas las filas: es el
    texto sobre el que se busca, y dejarlo viejo haría que la tabla dejara de
    encontrar sus propias filas sin ningún error visible."""
    _get_tabla_o_404(chatbot_id, tabla_id)
    try:
        chatbot_tablas_admin.actualizar_metadata(chatbot_id, tabla_id, payload.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    logger.info(f"Usuario {current_user.usuario} actualizó la tabla {tabla_id} del chatbot {chatbot_id}.")
    return _get_tabla_o_404(chatbot_id, tabla_id)


@router.delete("/{chatbot_id}/tablas/{tabla_id}", status_code=status.HTTP_204_NO_CONTENT)
def borrar_tabla(chatbot_id: int, tabla_id: int,
                 current_user: User = Depends(get_current_active_user)):
    _get_tabla_o_404(chatbot_id, tabla_id)
    chatbot_tablas_admin.borrar(chatbot_id, tabla_id)
    logger.info(f"Usuario {current_user.usuario} borró la tabla {tabla_id} del chatbot {chatbot_id}.")


@router.post("/{chatbot_id}/tablas/convertir", response_model=DocJobOut, status_code=202)
def convertir_doc_en_tabla(chatbot_id: int, payload: TablaConvertirIn,
                           current_user: User = Depends(get_current_active_user)):
    """Encola el análisis de uno o VARIOS documentos ya cargados para convertirlos
    en UNA tabla de datos.

    Es el camino de migración de lo que ya está escrito: no hay que volver a subir
    el material. Devuelve una propuesta (GET /docs/jobs/{job_id}); recién al
    aceptarla se crea la tabla y se desactivan los documentos de origen.

    Varios documentos dan UNA tabla, no una por documento. Es el caso de la cartera
    de cobranzas de benefix: 10 documentos, uno por gestor, con las columnas
    renombradas y reordenadas entre ellos. El gestor es un VALOR de columna, y los
    esquemas distintos los unifica la IA en el análisis.
    """
    _get_bot_o_404(chatbot_id)
    if not payload.doc_ids:
        raise HTTPException(status_code=422, detail="No se indicó ningún documento.")
    for doc_id in payload.doc_ids:
        _get_doc_md_o_404(chatbot_id, doc_id)   # 404 antes de encolar nada
    job_id = _encolar_doc_job(
        tipo="tabla", chatbot_id=chatbot_id,
        payload={"doc_ids": payload.doc_ids}, requested_by=current_user.usuario,
    )
    return DocJobOut(job_id=job_id, status="pending")


@router.post("/{chatbot_id}/tablas/desde-material", response_model=DocJobOut, status_code=202)
def crear_tabla_desde_material(chatbot_id: int, payload: TablaDesdeMaterialRequest,
                               current_user: User = Depends(get_current_active_user)):
    """Encola el análisis de una planilla o listado de la bandeja para crear una NUEVA tabla de datos.

    La IA define el esquema a partir de una muestra chica de filas (asistente_docs.analizar_tabla),
    y el parser carga las filas completas sin pasar por el modelo ni límite de tokens.
    Devuelve una propuesta (GET /docs/jobs/{job_id}); recién al aceptarla se crea la tabla.
    """
    _get_bot_o_404(chatbot_id)
    fuentes = _normalizar_fuentes(payload.fuentes, exigir_contenido=False)
    material_ids = _material_de_la_corrida(chatbot_id, payload.material_ids, fuentes)

    job_id = _encolar_doc_job(
        tipo="tabla", chatbot_id=chatbot_id,
        payload={
            "material_ids": material_ids,
            "fuentes": [f for f in (payload.model_dump().get("fuentes") or [])],
        },
        requested_by=current_user.usuario,
    )
    return DocJobOut(job_id=job_id, status="pending")



def _get_tabla_o_404(chatbot_id: int, tabla_id: int) -> TablaOut:
    for tabla in chatbot_tablas_admin.listar(chatbot_id):
        if tabla["id"] == tabla_id:
            return TablaOut(**tabla)
    raise HTTPException(status_code=404, detail="La tabla no existe para ese chatbot.")


@router.post("/{chatbot_id}/tablas/{tabla_id}/filas", status_code=status.HTTP_201_CREATED)
def agregar_fila(chatbot_id: int, tabla_id: int, payload: TablaFilaIn,
                 current_user: User = Depends(get_current_active_user)):
    """Suma una fila a mano (un cliente nuevo, una sucursal que abrió)."""
    _get_bot_o_404(chatbot_id)
    try:
        fila_id = chatbot_tablas_admin.guardar_fila(
            chatbot_id, tabla_id, None, payload.datos, current_user.usuario)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    logger.info(f"Usuario {current_user.usuario} agregó la fila {fila_id} a la tabla {tabla_id}.")
    return {"id": fila_id}


@router.put("/{chatbot_id}/tablas/{tabla_id}/filas/{fila_id}")
def editar_fila(chatbot_id: int, tabla_id: int, fila_id: int, payload: TablaFilaIn,
                current_user: User = Depends(get_current_active_user)):
    """Corrige una fila. Queda marcada como editada a mano, así una recarga masiva
    posterior puede avisar cuántas correcciones está por pisar."""
    _get_bot_o_404(chatbot_id)
    try:
        chatbot_tablas_admin.guardar_fila(
            chatbot_id, tabla_id, fila_id, payload.datos, current_user.usuario)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    logger.info(f"Usuario {current_user.usuario} editó la fila {fila_id} de la tabla {tabla_id}.")
    return {"id": fila_id}


@router.delete("/{chatbot_id}/tablas/{tabla_id}/filas/{fila_id}",
               status_code=status.HTTP_204_NO_CONTENT)
def borrar_fila(chatbot_id: int, tabla_id: int, fila_id: int,
                current_user: User = Depends(get_current_active_user)):
    """Da de baja una fila (un cliente que dejó de ser cliente)."""
    _get_bot_o_404(chatbot_id)
    try:
        chatbot_tablas_admin.borrar_fila(chatbot_id, tabla_id, fila_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    logger.info(f"Usuario {current_user.usuario} borró la fila {fila_id} de la tabla {tabla_id}.")


@router.post("/{chatbot_id}/tablas/{tabla_id}/actualizar", response_model=DocJobOut,
             status_code=202)
def actualizar_tabla_con_material(chatbot_id: int, tabla_id: int,
                                  payload: TablaActualizarRequest,
                                  current_user: User = Depends(get_current_active_user)):
    """Encola la comparación del material de la bandeja contra una tabla cargada.

    Devuelve un DIFF (altas / cambios / bajas) para revisar; no toca la tabla
    hasta que se aprueba con /aplicar. El material tiene que ser una planilla
    (Excel/CSV) o una tabla pegada: se parsea sin pasar por el modelo, porque
    hacerle reconstruir una grilla a un LLM es caro y pierde filas.
    """
    _get_bot_o_404(chatbot_id)
    _get_tabla_o_404(chatbot_id, tabla_id)
    if payload.modo_carga not in ("incremental", "reemplazo"):
        raise HTTPException(status_code=422, detail="Modo de carga desconocido.")

    fuentes = _normalizar_fuentes(payload.fuentes, exigir_contenido=False)
    material_ids = _material_de_la_corrida(chatbot_id, payload.material_ids, fuentes)

    job_id = _encolar_doc_job(
        tipo="tabla", chatbot_id=chatbot_id,
        payload={
            "tabla_id": tabla_id,
            "modo_carga": payload.modo_carga,
            "material_ids": material_ids,
            "fuentes": [f for f in (payload.model_dump().get("fuentes") or [])],
        },
        requested_by=current_user.usuario,
    )
    return DocJobOut(job_id=job_id, status="pending")


@router.post("/{chatbot_id}/tablas/{tabla_id}/aplicar")
def aplicar_diff_tabla(chatbot_id: int, tabla_id: int, payload: TablaAplicarIn,
                       current_user: User = Depends(get_current_active_user)):
    """Aplica lo que el usuario aprobó del diff, en una sola transacción."""
    _get_bot_o_404(chatbot_id)
    _get_tabla_o_404(chatbot_id, tabla_id)
    if not (payload.altas or payload.cambios or payload.bajas):
        raise HTTPException(status_code=422, detail="No se aprobó ningún cambio.")

    try:
        resumen = chatbot_tablas_admin.aplicar_diff(
            chatbot_id, tabla_id, payload.altas,
            [c.model_dump() for c in payload.cambios], payload.bajas,
            current_user.usuario,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    if payload.material_ids:
        with engine.begin() as conn:
            doc_material.consumir(conn, chatbot_id, payload.material_ids)

    logger.info(f"Usuario {current_user.usuario} aplicó cambios a la tabla {tabla_id}: {resumen}.")
    return resumen
