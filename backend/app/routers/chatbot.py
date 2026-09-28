import logging
import re
import unicodedata
from typing import Optional, List, Dict
from urllib.parse import quote
from app.dependencies import RoleChecker, require_chatbot_user
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status, BackgroundTasks, Response, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.database import engine
from app.models import (
    CalificacionRequest, CalificacionResponse, User,
    ChatbotDisponible, ChatbotsDisponiblesResponse,
)
from app.security import get_current_active_user
from app.services import chatbot_registry, chatbot_sql
from app.chatbot_config import ChatbotConfig, slugify_campana, normalizar_pcrc
from app.config import settings
from app import chat_history, chatbot_adjuntos, chatbot_imagenes
from datetime import datetime
import json
router = APIRouter()
logger = logging.getLogger(__name__)

# --- Resolución de bots permitidos -----------------------------------------
# Cada bot tiene su permiso propio (chatbot:<slug>); los del grupo CSV comparten
# chatbot:csv y el bot concreto de un operador se resuelve por su PCRC vigente.

def get_allowed_bots(user: User) -> List[ChatbotConfig]:
    """Bots activos que el usuario puede usar según sus permisos.
    Super admin y chatbot:admin (calidad) pueden usar todos."""
    activos = chatbot_registry.list_active()
    if user.is_super_admin or "chatbot:admin" in user.permissions:
        return activos
    return [c for c in activos if c.permission_code in user.permissions]

# Consulta que devuelve el PCRC vigente de un operador a partir de su documento.
_PCRC_QUERY = text("""
    SELECT n.documento, np.PCRC
    FROM [Acme].[dbo].[CSV Historial Skill-PCRC] hsp
    JOIN [CSV Normalizador PCRC] np ON hsp.PCRC_ID = np.id
    JOIN CSV.vw_ausentismo_latest ns ON hsp.Skill_ID = ns.[Skill 4]
    -- TRY_CAST para transformar de forma segura el usuario a INT
    JOIN usuarios u ON TRY_CAST(u.usuario AS INT) = ns.[Identif. de conexión.1]
    JOIN nomina n ON n.id = u.nomina_id
    JOIN operadores o ON o.legajo_id = n.id AND o.estado = 1 AND o.fecha_hasta IS NULL
    WHERE hsp.fecha_hasta IS NULL AND Desconexion IS NULL
      AND n.documento = :documento
""")

_PCRC_MAP_QUERY = text("""
    SELECT c.slug
    FROM [Acme].[pagina_web].[ChatbotPcrc] m
    JOIN [Acme].[pagina_web].[Chatbots] c ON c.id = m.chatbot_id
    WHERE m.pcrc_normalizado = :pcrc AND c.activo = 1
""")

def resolver_slug_csv_por_pcrc(documento: int) -> Optional[str]:
    """Slug del bot CSV correspondiente al PCRC vigente del operador, o None si
    el operador no tiene PCRC vigente o el PCRC no está mapeado (en ese caso el
    caller decide: selector para calidad, error para operadores)."""
    try:
        with engine.connect() as conn:
            filas = conn.execute(_PCRC_QUERY, {"documento": documento}).mappings().all()
    except SQLAlchemyError as e:
        logger.error(f"Error consultando PCRC para el documento {documento}: {e}")
        return None

    if not filas:
        logger.info(f"No se encontró PCRC vigente para el operador {documento}.")
        return None

    pcrc_raw = filas[0]["PCRC"]
    if len(filas) > 1:
        pcrcs_distintos = {f["PCRC"] for f in filas}
        if len(pcrcs_distintos) > 1:
            logger.warning(f"El operador {documento} tiene múltiples PCRC vigentes {pcrcs_distintos}; se usa '{pcrc_raw}'.")

    try:
        with engine.connect() as conn:
            fila = conn.execute(_PCRC_MAP_QUERY, {"pcrc": normalizar_pcrc(pcrc_raw)}).mappings().first()
    except SQLAlchemyError as e:
        logger.error(f"Error consultando el mapeo PCRC->chatbot para '{pcrc_raw}': {e}")
        return None

    if not fila:
        logger.warning(f"El PCRC '{pcrc_raw}' del operador {documento} no tiene un chatbot asignado.")
        return None

    slug = fila["slug"]
    logger.info(f"Operador {documento} con PCRC '{pcrc_raw}' resuelto al chatbot '{slug}'.")
    return slug

def _acceso_es_solo_csv(user: User, allowed: List[ChatbotConfig]) -> bool:
    """True si todos los bots permitidos del usuario salen del permiso compartido
    del grupo CSV (candidato a resolución por PCRC)."""
    return bool(allowed) and all(c.es_csv for c in allowed)

def _resolver_slug_efectivo(user: User, form_campana: Optional[str]) -> str:
    """Determina el bot a usar: form (validado contra permisos) > único permitido
    > resolución PCRC (grupo CSV) > la campaña legacy del operador."""
    allowed = {c.slug: c for c in get_allowed_bots(user)}
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No tenés acceso a ningún chatbot.")

    if form_campana and form_campana.strip():
        slug = slugify_campana(form_campana)
        if slug not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"No tenés permiso para usar el chatbot '{slug}'.")
        return slug

    if len(allowed) == 1:
        return next(iter(allowed))

    # Operador CSV: el bot concreto sale de su PCRC/skill vigente.
    if _acceso_es_solo_csv(user, list(allowed.values())):
        slug = resolver_slug_csv_por_pcrc(user.usuario)
        if slug and slug in allowed:
            return slug

    # La campaña del operador como último default (ej. operador Voltara con más permisos).
    if user.campana:
        slug = slugify_campana(user.campana)
        if slug in allowed:
            return slug

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Tenés acceso a varios chatbots: seleccioná uno en el formulario ('campana').",
    )

@router.get("/chatbots/disponibles", response_model=ChatbotsDisponiblesResponse, tags=["ChatBot"])
def chatbots_disponibles(current_user: User = Depends(require_chatbot_user)):
    """Bots que el usuario puede usar, para armar el selector del frontend.
    default_slug preseleccionado: el resuelto por PCRC (operadores CSV) o el
    único bot permitido."""
    allowed = get_allowed_bots(current_user)
    if not allowed:
        return ChatbotsDisponiblesResponse(bots=[], default_slug=None)

    def _disponible(c: ChatbotConfig) -> ChatbotDisponible:
        return ChatbotDisponible(
            slug=c.slug, nombre=c.nombre, descripcion=c.descripcion,
            permite_adjuntos=c.permite_adjuntos,
        )

    # El permiso es del usuario; que además pueda adjuntar depende del bot elegido.
    puede_adjuntar = (
        current_user.is_super_admin
        or chatbot_adjuntos.PERMISO in current_user.permissions
    )

    default_slug: Optional[str] = None

    if len(allowed) == 1:
        default_slug = allowed[0].slug
    elif _acceso_es_solo_csv(current_user, allowed):
        # Operador CSV con PCRC vigente: va directo a su bot, sin selector.
        # Sin PCRC (ej. calidad con chatbot:csv): selector con todos los CSV.
        slug = resolver_slug_csv_por_pcrc(current_user.usuario)
        if slug and any(c.slug == slug for c in allowed):
            return ChatbotsDisponiblesResponse(
                bots=[_disponible(c) for c in allowed if c.slug == slug],
                default_slug=slug,
                adjuntos_habilitados=puede_adjuntar,
            )
    elif current_user.campana:
        # Preselección amable para usuarios multi-bot con campaña propia.
        slug = slugify_campana(current_user.campana)
        if any(c.slug == slug for c in allowed):
            default_slug = slug

    bots = sorted((_disponible(c) for c in allowed), key=lambda b: b.nombre.lower())
    return ChatbotsDisponiblesResponse(
        bots=bots,
        default_slug=default_slug,
        adjuntos_habilitados=puede_adjuntar,
    )


async def _leer_adjuntos(
    archivos: Optional[List[UploadFile]],
    user: User,
    config: ChatbotConfig,
) -> List:
    """Autoriza, lee y valida los archivos subidos ANTES de abrir el streaming.

    El orden importa dos veces:

    - la autorización va antes de leer los bytes, para no cargar en memoria archivos
      de alguien que no puede mandarlos;
    - todo esto va antes del `StreamingResponse`, porque una vez que empezó a salir
      el stream ya no se puede devolver un 4xx y el operador vería el error mezclado
      con la respuesta del bot.
    """
    if not archivos:
        return []

    try:
        chatbot_adjuntos.verificar_acceso(
            permisos=user.permissions,
            es_super_admin=user.is_super_admin,
            slug=config.slug,
            permite=config.permite_adjuntos,
        )
    except chatbot_adjuntos.AdjuntoNoAutorizado as e:
        logger.warning(
            f"Usuario {user.usuario} intentó adjuntar sin el permiso "
            f"{chatbot_adjuntos.PERMISO}."
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except chatbot_adjuntos.BotSinAdjuntos as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # La cantidad se chequea ANTES de leer: `preparar` lo valida igual, pero para
    # entonces ya habríamos cargado en memoria los 50 archivos que vamos a rechazar.
    if len(archivos) > settings.CHATBOT_ADJUNTOS_MAX_ARCHIVOS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Podés adjuntar hasta {settings.CHATBOT_ADJUNTOS_MAX_ARCHIVOS} "
                   f"archivos por consulta (mandaste {len(archivos)}).",
        )

    crudos = []
    for archivo in archivos:
        if not archivo or not archivo.filename:
            continue
        crudos.append((archivo.filename, await archivo.read()))
    try:
        return chatbot_adjuntos.preparar(crudos)
    except chatbot_adjuntos.AdjuntoInvalido as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.post("/consultar/stream/", tags=["ChatBot"])
async def consultar_stream(
    query: str = Form(...),
    campana: Optional[str] = Form(default=None),  # slug del bot (acepta nombres legacy "csv no premium")
    contexto: Optional[bool] = Form(False),
    adjuntos: Optional[List[UploadFile]] = File(default=None),
    current_user: User = Depends(require_chatbot_user)
):
    logger.info(f"Usuario {current_user.usuario} iniciando consulta en streaming: '{query[:50]}...'")

    effective_slug = _resolver_slug_efectivo(current_user, campana)

    chatbot_to_use = chatbot_registry.get(effective_slug)
    if chatbot_to_use is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"El chatbot '{effective_slug}' no está disponible en este momento (sin índice o desactivado).",
        )

    # Después de resolver el bot: si los adjuntos se validaran antes, no habría contra
    # qué chequear `permite_adjuntos` (el slug puede venir del form, del único bot
    # permitido o de la resolución por PCRC).
    adjuntos_preparados = await _leer_adjuntos(adjuntos, current_user, chatbot_to_use.config)

    task_id = f"consult_stream_{current_user.usuario}_{int(datetime.now().timestamp())}"
    async def response_generator_wrapper():
        # Llamamos al nuevo método async stream_query
        async for token in chatbot_to_use.stream_query(
            query,
            current_user.usuario,
            effective_slug,
            task_id,
            adjuntos=adjuntos_preparados,
        ):
            yield token

    return StreamingResponse(
        response_generator_wrapper(),
        media_type="text/event-stream",
        headers={"X-Task-ID": task_id}
    )

@router.post("/consultar/calificar/{task_id}", response_model=CalificacionResponse, tags=["ChatBot"])
def calificar_respuesta(
    task_id: str,
    calificacion_data: CalificacionRequest,
    current_user: User = Depends(require_chatbot_user)
):
    insert_query = text("""
        INSERT INTO [Acme].[pagina_web].[chatbot_calificaciones]
        (task_id, calificacion, comentario)
        VALUES
        (:task_id, :calificacion, :comentario)
    """)

    with engine.connect() as connection:
        connection.execute(
            insert_query,
            {
                "task_id": task_id,
                "calificacion": calificacion_data.calificacion,
                "comentario": calificacion_data.comentario,
            }
        )
        connection.commit()

    logger.info(f"Usuario {current_user.usuario} calificó la tarea '{task_id}' con un {calificacion_data.calificacion}.")
    return CalificacionResponse(task_id=task_id, calificacion_guardada=calificacion_data.calificacion, mensaje="Calificación recibida con éxito.")

@router.get("/consultar/historial", tags=["ChatBot"])
def obtener_historial(
    campana: Optional[str] = None,  # slug del bot; se resuelve igual que en /consultar/stream/
    current_user: User = Depends(require_chatbot_user),
):
    """Historial de conversación del usuario con un bot puntual (cada bot tiene su hilo)."""
    effective_slug = _resolver_slug_efectivo(current_user, campana)
    return chat_history.get_history(engine, current_user.usuario, effective_slug)

@router.delete("/consultar/historial", tags=["ChatBot"])
def borrar_historial(
    campana: Optional[str] = None,
    current_user: User = Depends(require_chatbot_user),
):
    """Borra la memoria del usuario con ese bot; los otros bots conservan su historial."""
    effective_slug = _resolver_slug_efectivo(current_user, campana)
    chat_history.clear_history(engine, current_user.usuario, effective_slug)
    return {"message": "Historial borrado correctamente"}

def _parsear_historial_sql(historial_raw: Optional[str]) -> Optional[List[Dict[str, str]]]:
    """Convierte el historial enviado por el frontend (JSON) en la lista que
    espera el orquestador. Cualquier formato inesperado se descarta en silencio."""
    if not historial_raw:
        return None
    try:
        historial = json.loads(historial_raw)
    except (TypeError, ValueError):
        logger.warning("Historial del chatbot SQL con formato inválido; se ignora.")
        return None
    if not isinstance(historial, list):
        return None
    limpio = [
        {"rol": str(m.get("rol", "user")), "texto": str(m.get("texto", ""))[:1000]}
        for m in historial if isinstance(m, dict) and m.get("texto")
    ]
    return limpio[-8:] or None


async def run_sql_analitica_task(task_id: str, query: str, historial: Optional[List[Dict[str, str]]], chatbot_sql_instance):
    """Ejecuta el pipeline del orquestador SQL en segundo plano y guarda el resultado."""
    try:
        logger.info(f"Iniciando tarea SQL Analítica en segundo plano: {task_id}")

        # Ejecutamos el servicio pesado de forma asíncrona
        resultado = await chatbot_sql_instance.consultar_con_analisis(pregunta=query, historial=historial)

        # Evaluamos si el servicio nos devolvió un error interno
        if "error" in resultado:
            estado = 'failed'
            error_msg = resultado["error"]
            result_json = None
        else:
            estado = 'completed'
            error_msg = None
            # Serializamos el diccionario completo (incluyendo config de gráfico y datos)
            result_json = json.dumps(resultado)

        # Guardar resultado en BBDD reutilizando la tabla de AuditorTasks
        update_query = text("""
            UPDATE [calidad].[AuditTasks]
            SET [status] = :status,
                [result_data] = :data,
                [error_message] = :error,
                [updated_at] = GETDATE()
            WHERE [task_id] = :task_id
        """)

        with engine.begin() as conn:
            conn.execute(update_query, {
                "status": estado,
                "data": result_json,
                "error": error_msg,
                "task_id": task_id
            })

        logger.info(f"Tarea SQL Analítica {task_id} finalizada con éxito.")

    except Exception as e:
        logger.error(f"Error crítico en tarea SQL Analítica {task_id}: {e}")
        # En caso de fallo total (ej. se cae la BBDD a medio camino)
        update_query = text("""
            UPDATE [calidad].[AuditTasks]
            SET [status] = 'failed', [error_message] = :error, [updated_at] = GETDATE()
            WHERE [task_id] = :task_id
        """)
        try:
            with engine.begin() as conn:
                conn.execute(update_query, {"error": str(e), "task_id": task_id})
        except Exception as db_err:
            logger.error(f"Error al guardar fallo de tarea SQL: {db_err}")

@router.post("/consultar/sql/", tags=["ChatBot SQL"], status_code=202, dependencies=[Depends(RoleChecker(["chatbot:sql"]))])
def consultar_analitica_sql(
    background_tasks: BackgroundTasks, # <--- Inyectamos BackgroundTasks
    query: str = Form(...),
    historial: Optional[str] = Form(None), # JSON: [{"rol": "user|bot", "texto": "..."}]
    current_user: User = Depends(get_current_active_user)
):
    logger.info(f"Usuario {current_user.usuario} iniciando consulta SQL Analítica: '{query[:50]}...'")

    if not chatbot_sql:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="El servicio de Chatbot SQL no está disponible en este momento.")

    # 1. Generamos un ID único para la tarea
    task_id = f"sql_{current_user.usuario}_{int(datetime.now().timestamp())}"

    # 2. Insertamos la tarea como "pending" en la base de datos
    try:
        insert_query = text("""
            INSERT INTO [calidad].[AuditTasks] (task_id, status, created_at, updated_at, Entorno)
            VALUES (:task_id, 'pending', GETDATE(), GETDATE(), :env)
        """)
        with engine.begin() as conn:
            conn.execute(insert_query, {"task_id": task_id, "env": settings.ENVIRONMENT})
    except Exception as e:
        logger.error(f"Error al insertar la tarea {task_id} en BBDD: {e}")
        raise HTTPException(status_code=500, detail=f"Error al registrar la tarea en la base de datos.")

    # 3. Enviamos el trabajo pesado al fondo
    background_tasks.add_task(run_sql_analitica_task, task_id, query, _parsear_historial_sql(historial), chatbot_sql)

    # 4. Devolvemos respuesta inmediata (Status 202 Accepted)
    return {"message": "La consulta SQL ha sido iniciada en segundo plano.", "task_id": task_id}


@router.get("/consultar/sql/status/{task_id}", tags=["ChatBot SQL"], dependencies=[Depends(RoleChecker(["chatbot:sql"]))])
def get_sql_status(task_id: str):
    """Endpoint para que el Frontend consulte si el bot ya terminó de pensar."""
    query = text("SELECT [status], [error_message] FROM [calidad].[AuditTasks] WHERE [task_id] = :task_id")

    try:
        with engine.connect() as conn:
            db_row = conn.execute(query, {"task_id": task_id}).mappings().first()
    except Exception as e:
        logger.error(f"Error al consultar estado SQL {task_id}: {e}")
        raise HTTPException(status_code=500, detail="Error de conexión al verificar el estado.")

    if not db_row:
        raise HTTPException(status_code=404, detail="ID de tarea no encontrado.")

    return {
        "task_id": task_id,
        "status": db_row["status"],
        "error": db_row["error_message"]
    }


@router.get("/consultar/sql/resultado/{task_id}", tags=["ChatBot SQL"], dependencies=[Depends(RoleChecker(["chatbot:sql"]))])
def get_sql_result(task_id: str, background_tasks: BackgroundTasks):
    """Endpoint para obtener el JSON final cuando el estado sea 'completed'."""

    def delete_task_from_db(task_id_to_delete: str):
        try:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM [calidad].[AuditTasks] WHERE [task_id] = :task_id"), {"task_id": task_id_to_delete})
        except Exception as e:
            logger.error(f"Error al limpiar tarea temporal SQL {task_id_to_delete}: {e}")

    query = text("SELECT [status], [error_message], [result_data] FROM [calidad].[AuditTasks] WHERE [task_id] = :task_id")

    with engine.connect() as conn:
        db_row = conn.execute(query, {"task_id": task_id}).mappings().first()

    if not db_row:
        raise HTTPException(status_code=404, detail="Tarea no encontrada.")

    status_db = db_row["status"]

    if status_db == "pending":
        raise HTTPException(status_code=202, detail="El bot de SQL aún está analizando los datos...")

    if status_db == "failed":
        background_tasks.add_task(delete_task_from_db, task_id)
        raise HTTPException(status_code=500, detail=f"La consulta falló: {db_row['error_message']}")

    if status_db == "completed":
        # Limpiamos la BBDD para no llenarla de JSONs basura a largo plazo
        background_tasks.add_task(delete_task_from_db, task_id)

        # Recuperamos el string JSON y lo volvemos un diccionario
        data_str = db_row["result_data"]
        if not data_str:
            raise HTTPException(status_code=500, detail="La tarea se completó pero no se guardó ningún JSON.")

        resultado_diccionario = json.loads(data_str)

        # Devolvemos exactamente lo mismo que devolvía tu endpoint original
        return resultado_diccionario


def _content_disposition(nombre: str) -> str:
    """Cabecera Content-Disposition que aguanta CUALQUIER nombre de archivo.

    Los headers HTTP se serializan en latin-1: un nombre con guion largo (—),
    comillas tipográficas o cualquier carácter fuera de ese juego hacía explotar
    la respuesta entera con UnicodeEncodeError, y el navegador solo veía la
    imagen rota. Como los nombres salen del archivo que subió el usuario (un
    "Instructivo — Alta de servicio.pptx" alcanza), va un fallback en ASCII puro
    y el nombre real codificado en filename* (RFC 5987/6266), que es el que los
    navegadores usan cuando está.
    """
    nombre = re.sub(r"[\r\n]+", " ", nombre or "").strip() or "imagen"
    ascii_fallback = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode("ascii")
    ascii_fallback = re.sub(r'[^A-Za-z0-9._ -]+', "_", ascii_fallback).strip(" _") or "imagen"
    return f"inline; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(nombre, safe='')}"


@router.get("/chatbots/imagenes/{imagen_id}", tags=["ChatBot"])
def obtener_imagen_chatbot(imagen_id: int, request: Request,
                           current_user: User = Depends(get_current_active_user)):
    """Sirve una imagen de chatbot almacenada en SQL Server con encabezados de caché.

    Pide sesión (aunque no un permiso de bot puntual): las capturas son material
    interno de los procedimientos y el id es un entero correlativo, así que sin
    login cualquiera que llegue a la API se las lleva enumerando ids.
    """
    img = chatbot_imagenes.obtener_imagen(imagen_id)
    if not img:
        raise HTTPException(status_code=404, detail="Imagen no encontrada.")

    etag = f'"{img.get("hash_sha256") or imagen_id}"'
    cache = "private, max-age=86400"
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": cache})

    return Response(
        content=img["datos"],
        media_type=img["mime"],
        headers={
            "ETag": etag,
            "Cache-Control": cache,
            "Content-Disposition": _content_disposition(img["nombre"]),
        },
    )
