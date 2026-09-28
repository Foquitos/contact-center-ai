"""Cola de trabajos del asistente de documentación (pagina_web.ChatbotDocJobs).

POR QUÉ EXISTE
--------------
Formatear y mergear encadenan varias llamadas a Gemini —una de merge más una ronda
de verificar→reparar por cada CHATBOT_DOCS_VERIFY_ROUNDS— y cada una regenera el
markdown COMPLETO del documento. Medido sobre un documento real de 17k caracteres:
45s el camino corto y ~3 minutos el peor caso.

Eso no entra en una request HTTP: gunicorn corta el worker a los 30s y el usuario
recibía la página de error del worker muerto. Así que la request solo ENCOLA y
responde al instante, el scheduler hace el trabajo y el navegador consulta el
estado por polling.

Es la hermana de la cola de reindexado (chatbot_indexer.procesar_cola_reindexado):
mismo claim atómico FIFO, mismo scoping por entorno, mismo marcado de huérfanos.
"""
import base64
import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import bindparam, text
from sqlalchemy.types import NVARCHAR

from app import doc_material
from app.config import settings

logger = logging.getLogger(__name__)

ESTADOS_ABIERTOS = ("pending", "running")


def _get_engine():
    from app.database import engine
    return engine


# ------------------------------------------------------------------ encolar

# payload/resultado van con bind NVARCHAR explícito: el markdown lleva emojis y en
# VARCHAR se pierden en el bind (mismo problema que tuvo query_chatbots_logs).
_INSERT_JOB = text("""
    INSERT INTO pagina_web.ChatbotDocJobs (tipo, chatbot_id, payload, requested_by, environment)
    OUTPUT INSERTED.id
    VALUES (:tipo, :cid, :payload, :req, :env)
""").bindparams(bindparam("payload", type_=NVARCHAR))


# El trabajo abierto (en cola o corriendo) de un chatbot. Ver TrabajoEnCurso.
_ABIERTO_DEL_BOT = text("""
    SELECT TOP (1) id, status
    FROM pagina_web.ChatbotDocJobs
    WHERE chatbot_id = :cid AND environment = :env AND status IN ('pending', 'running')
    ORDER BY id
""")


class TrabajoEnCurso(Exception):
    """El chatbot ya tiene un trabajo de la IA en cola o corriendo.

    Pasó el 2026-09-14 con el manual de Hidra: la pantalla dejaba de esperar a los 15
    minutos y dos personas volvieron a mandar el mismo PDF (jobs 183, 184 y 185, este
    último con el archivo subido de nuevo). La cola es de a uno para todo el sistema, así
    que cada copia sumaba horas de espera para los demás y el mismo gasto otra vez. Es
    por chatbot y no por id de material justamente por eso: volver a subir el archivo le
    da otro id.
    """

    def __init__(self, job_id: int):
        super().__init__(
            f"Ya hay un trabajo de la IA en curso para este chatbot (#{job_id}). "
            "Hasta que termine o se cancele no se puede encolar otro."
        )
        self.job_id = job_id


def encolar(tipo: str, chatbot_id: Optional[int], payload: Dict[str, Any],
            requested_by: Optional[int]) -> int:
    """Deja el trabajo listo para que lo tome el scheduler. Devuelve el job_id.

    Si el chatbot ya tiene uno abierto, TrabajoEnCurso con su id: el panel muestra ese
    en vez de encolar otro."""
    engine = _get_engine()
    with engine.begin() as conn:
        if chatbot_id is not None:
            abierto = conn.execute(_ABIERTO_DEL_BOT, {
                "cid": chatbot_id, "env": settings.ENVIRONMENT,
            }).first()
            if abierto:
                raise TrabajoEnCurso(abierto.id)
        row = conn.execute(_INSERT_JOB, {
            "tipo": tipo,
            "cid": chatbot_id,
            "payload": json.dumps(payload, ensure_ascii=False),
            "req": requested_by,
            "env": settings.ENVIRONMENT,
        }).first()
    job_id = row.id
    logger.info(f"Job de documentación {job_id} encolado (tipo={tipo}, chatbot={chatbot_id}).")
    return job_id


# Qué hay antes de un trabajo en cola. La cola es de a uno para todo el sistema, así
# que "En cola" solo no explica por qué no arranca: el 2026-09-14 un trabajo esperó casi
# una hora detrás de otro que llevaba tres.
_COLA = text("""
    SELECT
        (SELECT COUNT(*) FROM pagina_web.ChatbotDocJobs a
          WHERE a.environment = :env
            AND (a.status = 'running' OR (a.status = 'pending' AND a.id < :jid))) AS adelante,
        c.nombre AS en_curso_bot,
        DATEDIFF(second, r.started_at, SYSDATETIME()) AS en_curso_segundos
    FROM (SELECT 1 AS uno) base
    OUTER APPLY (SELECT TOP (1) j.chatbot_id, j.started_at
                 FROM pagina_web.ChatbotDocJobs j
                 WHERE j.status = 'running' AND j.environment = :env
                 ORDER BY j.id) r
    LEFT JOIN pagina_web.Chatbots c ON c.id = r.chatbot_id
""")


def obtener(job_id: int) -> Optional[Dict[str, Any]]:
    """Estado del job para el polling. `resultado` viene ya deserializado.

    `segundos` cuenta desde que arrancó (o desde que se encoló, si todavía espera) con el
    reloj de la base, no con el de la PC. En cola se suma cuántos trabajos hay adelante y
    cuánto lleva el que está corriendo."""
    engine = _get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT id, tipo, chatbot_id, status, resultado, error, environment,
                   created_at, started_at, finished_at,
                   DATEDIFF(second, COALESCE(started_at, created_at), SYSDATETIME()) AS segundos
            FROM pagina_web.ChatbotDocJobs
            WHERE id = :jid
        """), {"jid": job_id}).mappings().first()
        if not row:
            return None

        datos = dict(row)
        datos.update(adelante=None, en_curso_bot=None, en_curso_segundos=None)
        if datos["status"] == "pending":
            cola = conn.execute(_COLA, {"env": datos["environment"], "jid": job_id}).mappings().first()
            if cola:
                datos.update(adelante=cola["adelante"], en_curso_bot=cola["en_curso_bot"],
                             en_curso_segundos=cola["en_curso_segundos"])

    crudo = datos.pop("resultado", None)
    datos["resultado"] = json.loads(crudo) if crudo else None
    datos["cancelado"] = es_cancelacion(datos["status"], datos.get("error"))
    return datos


# --------------------------------------------------------------------- cola

# Claim atómico FIFO: el UPDATE sobre el CTE evita que dos ticks solapados tomen
# el mismo job. No se excluye por chatbot como en el reindexado porque la cola se
# drena de a un job por vez.
_CLAIM_QUERY = text("""
    WITH siguiente AS (
        SELECT TOP (1) *
        FROM pagina_web.ChatbotDocJobs
        WHERE status = 'pending' AND environment = :env
        ORDER BY id
    )
    UPDATE siguiente
    SET status = 'running', started_at = SYSDATETIME()
    OUTPUT INSERTED.id, INSERTED.tipo, INSERTED.chatbot_id,
           INSERTED.payload, INSERTED.requested_by
""")

# Los dos cierres exigen status='running': si lo cancelaron mientras corría, la fila
# ya la cerró quien canceló y no hay que pisarla con un resultado que nadie pidió.
_FINALIZAR_OK = text("""
    UPDATE pagina_web.ChatbotDocJobs
    SET status = 'done', resultado = :res, payload = NULL, finished_at = SYSDATETIME()
    WHERE id = :jid AND status = 'running'
""").bindparams(bindparam("res", type_=NVARCHAR))

_FINALIZAR_ERROR = text("""
    UPDATE pagina_web.ChatbotDocJobs
    SET status = 'failed', error = :err, payload = NULL, finished_at = SYSDATETIME()
    WHERE id = :jid AND status = 'running'
""").bindparams(bindparam("err", type_=NVARCHAR))


def procesar_cola_docs() -> None:
    """Tick del scheduler: drena la cola de a un job por vez.

    Serial a propósito: son llamadas a Gemini con salida larga y no tiene sentido
    pelearse por el rate limit para adelantar unos segundos.
    """
    engine = _get_engine()
    while True:
        try:
            with engine.begin() as conn:
                claimed = conn.execute(_CLAIM_QUERY, {"env": settings.ENVIRONMENT}).first()
        except Exception:
            logger.exception("No se pudo consultar la cola de documentación.")
            return

        if claimed is None:
            return

        ejecutar_doc_job(claimed.id, claimed.tipo, claimed.chatbot_id,
                         claimed.payload, claimed.requested_by)


def ejecutar_doc_job(job_id: int, tipo: str, chatbot_id: Optional[int],
                     payload_json: Optional[str], requested_by: Optional[int]) -> None:
    """Corre un job ya reclamado y deja el resultado (o el error) en la fila.

    Nunca propaga: si algo falla, el job queda 'failed' con el motivo, que es lo
    que el usuario ve en pantalla. Dejarlo 'running' lo colgaría para siempre.

    Si lo cancelan desde el panel, se corta antes de la próxima llamada a Gemini y no
    escribe nada: la fila ya la cerró quien canceló.
    """
    from AuditorIA import asistente_docs

    engine = _get_engine()
    try:
        payload = json.loads(payload_json or "{}")
        with asistente_docs.cancelable(lambda: _fue_cancelado(job_id)):
            if tipo == "formatear":
                resultado = _correr_formatear(payload, requested_by)
            elif tipo == "merge":
                resultado = _correr_merge(payload, chatbot_id, requested_by)
            elif tipo == "tabla":
                resultado = _correr_tabla(payload, chatbot_id, requested_by)
            else:
                raise ValueError(f"Tipo de trabajo desconocido: {tipo}")

        with engine.begin() as conn:
            conn.execute(_FINALIZAR_OK, {
                "jid": job_id, "res": json.dumps(resultado, ensure_ascii=False, default=str),
            })
        logger.info(f"Job de documentación {job_id} ({tipo}) terminado.")

    except asistente_docs.TrabajoCancelado:
        logger.info(f"Job de documentación {job_id} ({tipo}) cancelado desde el panel: "
                    "se corta sin escribir resultado.")

    except Exception as e:  # noqa: BLE001 - el error viaja a la pantalla, no al log solo
        logger.exception(f"Falló el job de documentación {job_id} ({tipo}).")
        try:
            with engine.begin() as conn:
                conn.execute(_FINALIZAR_ERROR, {"jid": job_id, "err": str(e)[:4000]})
        except Exception:
            logger.exception(f"Tampoco se pudo marcar como fallido el job {job_id}.")


# ---------------------------------------------------------------- cancelar

# No hay estado 'cancelled': el CHECK de la tabla solo admite pending/running/done/failed
# y sumarlo pedía una migración antes del deploy. Un cancelado es un 'failed' con este
# motivo, que es también lo que ve quien estaba esperando el trabajo.
_PREFIJO_CANCELADO = "Trabajo cancelado desde el panel."
MOTIVO_CANCELADO = (_PREFIJO_CANCELADO
                    + " El material sigue en la bandeja: se puede volver a procesar cuando se quiera.")

_CANCELAR = text("""
    UPDATE pagina_web.ChatbotDocJobs
    SET status = 'failed', error = :err, payload = NULL, finished_at = SYSDATETIME()
    OUTPUT DELETED.status
    WHERE id = :jid AND environment = :env AND status IN ('pending', 'running')
""").bindparams(bindparam("err", type_=NVARCHAR))


def es_cancelacion(status: Optional[str], error: Optional[str]) -> bool:
    return status == "failed" and (error or "").startswith(_PREFIJO_CANCELADO)


def cancelar(job_id: int) -> Optional[str]:
    """Cancela un trabajo en cola o corriendo. Devuelve el estado que tenía, o None si
    ya había terminado (o es de otro entorno).

    Uno en cola ya no se reclama. Uno que corre se entera antes de su próxima llamada a
    Gemini (ver _fue_cancelado): la que ya salió no se puede frenar, así que la cola puede
    tardar unos minutos en liberarse.
    """
    engine = _get_engine()
    with engine.begin() as conn:
        row = conn.execute(_CANCELAR, {
            "jid": job_id, "env": settings.ENVIRONMENT, "err": MOTIVO_CANCELADO,
        }).first()
    return row.status if row else None


def _fue_cancelado(job_id: int) -> bool:
    """¿El trabajo dejó de estar 'running' mientras corría? Pasa si lo cancelaron (o si
    otro proceso lo dio por huérfano): en los dos casos hay que dejar de gastar.

    Ante un error de base se sigue: cortar un trabajo de media hora por un corte de red
    de un segundo sería peor que la llamada de más."""
    try:
        with _get_engine().connect() as conn:
            row = conn.execute(text(
                "SELECT status FROM pagina_web.ChatbotDocJobs WHERE id = :jid"
            ), {"jid": job_id}).first()
    except Exception:  # noqa: BLE001
        logger.warning(f"No se pudo consultar si el job de documentación {job_id} fue cancelado.")
        return False
    return row is not None and row.status != "running"


# ------------------------------------------------------- retomar desde el panel

_ULTIMA_PROPUESTA = text("""
    SELECT TOP (1) id, JSON_QUERY(resultado, '$.material_ids') AS material_ids,
           DATEDIFF(second, finished_at, SYSDATETIME()) AS segundos
    FROM pagina_web.ChatbotDocJobs
    WHERE chatbot_id = :cid AND environment = :env AND status = 'done'
    ORDER BY id DESC
""")


def trabajo_actual_del_bot(chatbot_id: int) -> Optional[Dict[str, Any]]:
    """Lo que el panel tiene que retomar al abrir un bot, aunque sea otra pestaña.

    Primero, un trabajo abierto (lo haya lanzado quien sea). Si no hay, la última
    propuesta terminada cuyo material sigue TODO en la bandeja: el material se borra al
    guardar la propuesta, así que eso quiere decir que nadie la guardó. Una propuesta sin
    material de la bandeja (texto pegado en la request) no se ofrece: no hay forma de
    saber si ya se guardó.

    Existe porque el job 182 terminó a los 72 minutos con la pantalla ya rendida: la
    propuesta estaba en la base y no había forma de abrirla.
    """
    engine = _get_engine()
    params = {"cid": chatbot_id, "env": settings.ENVIRONMENT}
    with engine.connect() as conn:
        abierto = conn.execute(_ABIERTO_DEL_BOT, params).first()
        if abierto:
            return {"job_id": abierto.id, "status": abierto.status, "segundos": None}
        ultima = conn.execute(_ULTIMA_PROPUESTA, params).first()

    if not ultima or not ultima.material_ids:
        return None
    try:
        ids = {int(i) for i in json.loads(ultima.material_ids)}
    except (TypeError, ValueError):
        return None
    if not ids or not ids <= set(doc_material.ids_pendientes(chatbot_id)):
        return None
    return {"job_id": ultima.id, "status": "done", "segundos": ultima.segundos}


# ------------------------------------------------------------- ejecutores

def _fuentes_del_job(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Todo el material de la corrida: primero la bandeja, después lo que vino en la
    request.

    La bandeja (pagina_web.ChatbotDocMaterial) se lee ACÁ y no al encolar por lo
    mismo que los documentos del merge: los archivos son voluminosos y no tienen por
    qué viajar dos veces ni quedar duplicados en el payload del job. Los ids ya
    quedaron congelados en la request (ver _material_de_la_corrida).
    """
    fuentes = doc_material.cargar_fuentes(payload.get("material_ids") or [])
    fuentes += _fuentes_desde_payload(payload.get("fuentes"))
    if not fuentes:
        raise ValueError("El material a procesar ya no está disponible: "
                         "puede haberse quitado de la bandeja mientras el trabajo esperaba.")
    return fuentes


def _fuentes_desde_payload(fuentes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convierte las fuentes guardadas (archivos en base64) al formato que consume
    asistente_docs (bytes). El tamaño y el mime ya se validaron al encolar."""
    salida: List[Dict[str, Any]] = []
    for f in fuentes or []:
        if f.get("tipo") == "texto":
            if (f.get("texto") or "").strip():
                salida.append({"tipo": "texto", "texto": f["texto"], "nombre": f.get("nombre")})
        elif f.get("tipo") == "archivo":
            salida.append({
                "tipo": "archivo",
                "datos": base64.b64decode(f["datos_base64"]),
                "mime": f.get("mime"),
                "nombre": f.get("nombre"),
            })
    return salida


def _contexto_bot(chatbot_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """Nombre del bot para ambientar el prompt del asistente (no es fuente de datos).

    Solo `bot_nombre`/`empresa`/`campana` llegan al prompt (asistente_docs._contexto_texto);
    cualquier otra clave se descarta en silencio.
    """
    if not chatbot_id:
        return None
    engine = _get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT nombre FROM pagina_web.Chatbots WHERE id = :cid"), {"cid": chatbot_id}
        ).first()
    return {"bot_nombre": row.nombre} if row else None


def _correr_formatear(payload: Dict[str, Any], requested_by: Optional[int]) -> Dict[str, Any]:
    from AuditorIA import asistente_docs
    from app import chatbot_imagenes

    fuentes = _fuentes_del_job(payload)
    cid = payload.get("chatbot_id")
    imagenes = []
    if cid:
        try:
            imagenes = chatbot_imagenes.extraer_imagenes_de_fuentes(fuentes, cid, requested_by)
        except Exception:
            logger.exception(f"No se pudieron extraer imágenes automáticas para chatbot {cid}")

    contexto = _contexto_bot(cid)
    resultado = asistente_docs.formatear_documento(
        fuentes=fuentes,
        titulo=payload.get("titulo"),
        contexto=contexto,
        user_id=requested_by,
        permitir_separacion=payload.get("permitir_separacion", True),
        imagenes_disponibles=imagenes,
    )
    _separar_tablas(resultado, contexto, requested_by)
    # Viaja a la pantalla para que, al aceptar la propuesta, el guardado sepa qué
    # material de la bandeja ya quedó incorporado y pueda borrarlo.
    resultado["material_ids"] = payload.get("material_ids") or []
    return resultado


def _separar_tablas(resultado: Dict[str, Any], contexto: Optional[Dict[str, Any]],
                    requested_by: Optional[int]) -> None:
    """Saca de la propuesta los documentos que en realidad son tablas de datos.

    Corre DESPUÉS de formatear y no en su lugar: el material sigue pasando por el
    mismo pipeline de siempre (con su verificación de que no se pierde nada) y
    recién sobre el markdown ya limpio se decide qué es prosa y qué es tabla. Para
    Calidad no cambia nada: sigue siendo "sumar material" y "procesar", y la IA
    decide cómo acomodarlo.

    El filtro `parece_tabla` es determinístico y gratis: sin él, cada formateo
    pagaría una llamada extra por cada documento producido.
    """
    from AuditorIA import asistente_docs

    from app import chatbot_tablas_admin

    documentos = resultado.get("documentos") or []
    quedan: List[Dict[str, Any]] = []
    tablas: List[Dict[str, Any]] = []

    for doc in documentos:
        markdown = doc.get("markdown") or ""
        if not chatbot_tablas_admin.parece_tabla(markdown):
            quedan.append(doc)
            continue
        try:
            propuesta = asistente_docs.analizar_tabla(
                [{"titulo": doc.get("titulo") or "", "markdown": markdown}],
                contexto=contexto, user_id=requested_by,
            )
        except Exception:
            # Que falle la detección no puede tumbar el formateo: el documento
            # queda como documento, que es exactamente lo de antes.
            logger.exception("Falló el análisis de tabla de '%s'; queda como documento.",
                             doc.get("titulo"))
            quedan.append(doc)
            continue

        if not propuesta.get("es_tabla"):
            quedan.append(doc)
            continue

        propuesta["doc_titulo_origen"] = doc.get("titulo")
        tablas.append(propuesta)
        # Lo que del documento NO era la tabla (introducción, política,
        # procedimiento) sigue siendo documentación y no se pierde.
        resto = (propuesta.get("resto_markdown") or "").strip()
        if resto:
            quedan.append({**doc, "markdown": resto})

    resultado["documentos"] = quedan
    resultado["tablas"] = tablas


def _correr_tabla(payload: Dict[str, Any], chatbot_id: Optional[int],
                  requested_by: Optional[int]) -> Dict[str, Any]:
    """Convierte uno o VARIOS documentos ya cargados en UNA tabla de datos.

    Es el camino de migración de lo que ya está escrito (la cartera de cobranzas
    de benefix, las bases de vantix): no hace falta volver a subir el material.

    Que acepte varios documentos es lo que resuelve el caso de benefix: la cartera
    está repartida en 10 documentos, uno por gestor, con las columnas renombradas
    y reordenadas entre ellos (y dos con el encabezado roto). El gestor es un
    VALOR de columna, no una tabla aparte, así que los 10 tienen que terminar en
    una sola tabla — y los esquemas distintos los unifica la IA en el análisis.
    """
    from AuditorIA import asistente_docs

    if payload.get("tabla_id"):
        return _correr_tabla_actualizar(payload, chatbot_id, requested_by)

    doc_ids = payload.get("doc_ids") or ([payload["doc_id"]] if payload.get("doc_id") else [])
    if not doc_ids:
        if payload.get("material_ids") or payload.get("fuentes"):
            return _correr_tabla_crear_desde_material(payload, chatbot_id, requested_by)
        raise ValueError("Falta el documento o material a convertir.")

    engine = _get_engine()
    with engine.connect() as conn:
        filas = conn.execute(text("""
            SELECT id, titulo, contenido_md
            FROM pagina_web.ChatbotDocMarkdown
            WHERE id IN :ids AND chatbot_id = :cid AND activo = 1
            ORDER BY orden, id
        """).bindparams(bindparam("ids", expanding=True)),
            {"ids": list(doc_ids), "cid": chatbot_id}).mappings().all()
    if not filas:
        raise ValueError("Los documentos ya no existen.")
    if len(filas) != len(doc_ids):
        logger.warning("Conversión a tabla: se pidieron %d documentos y se encontraron %d.",
                       len(doc_ids), len(filas))

    propuesta = asistente_docs.analizar_tabla(
        [{"titulo": f["titulo"] or "", "markdown": f["contenido_md"] or "", "doc_id": f["id"]}
         for f in filas],
        contexto=_contexto_bot(chatbot_id), user_id=requested_by,
    )
    if not propuesta.get("es_tabla"):
        raise ValueError(
            "Ese material no es un listado de datos consultable, así que conviene "
            f"dejarlo como documentación. Motivo: {propuesta.get('motivo') or 'sin detalle'}"
        )

    # Todos los documentos de origen se dan de baja al aceptar: si quedara uno vivo,
    # sus filas seguirían compitiendo en el índice contra la tabla que las reemplaza.
    propuesta["doc_origen_ids"] = [f["id"] for f in filas]
    propuesta["doc_titulo_origen"] = ", ".join(f["titulo"] or f"#{f['id']}" for f in filas)
    return {"documentos": [], "tablas": [propuesta], "notas": propuesta.get("notas") or []}


def _correr_tabla_crear_desde_material(payload: Dict[str, Any], chatbot_id: Optional[int],
                                       requested_by: Optional[int]) -> Dict[str, Any]:
    """Crea una NUEVA tabla de datos a partir de una planilla o listado de la bandeja.

    La IA define el esquema a partir de una muestra chica de filas (asistente_docs.analizar_tabla),
    y el parser carga las filas completas sin pasar por el modelo ni límite de tokens.
    """
    from AuditorIA import asistente_docs, office_a_texto

    fuentes = _fuentes_del_job(payload)
    textos: List[Dict[str, str]] = []
    for f in fuentes:
        if f.get("tipo") == "texto":
            textos.append({"titulo": f.get("nombre") or "", "markdown": f.get("texto") or ""})
        elif f.get("tipo") == "archivo" and f.get("datos"):
            txt = office_a_texto.extraer_texto(f["datos"], f.get("nombre") or "archivo")
            textos.append({"titulo": f.get("nombre") or "", "markdown": txt})

    if not textos:
        raise ValueError("No se pudo extraer contenido para la tabla.")

    propuesta = asistente_docs.analizar_tabla(
        textos, contexto=_contexto_bot(chatbot_id), user_id=requested_by,
    )
    if not propuesta.get("es_tabla"):
        raise ValueError(
            "Ese material no es un listado de datos consultable, así que conviene "
            f"dejarlo como documentación. Motivo: {propuesta.get('motivo') or 'sin detalle'}"
        )

    propuesta["material_ids"] = payload.get("material_ids") or []
    return {
        "documentos": [],
        "tablas": [propuesta],
        "notas": propuesta.get("notas") or [],
        "material_ids": payload.get("material_ids") or [],
    }


def _correr_tabla_actualizar(payload: Dict[str, Any], chatbot_id: Optional[int],
                             requested_by: Optional[int]) -> Dict[str, Any]:
    """Compara el material de la bandeja contra una tabla ya cargada y devuelve el
    DIFF para aprobar (altas / cambios / bajas). NO aplica nada.

    El material se parsea acá mismo, sin pasar por el asistente: un Excel ya ES
    una tabla y hacerle reconstruir la grilla a un modelo es caro y lossy. El
    modelo interviene en una sola llamada chica, para decir a qué columna de la
    tabla destino corresponde cada columna del archivo.
    """
    from AuditorIA import asistente_docs

    from app import chatbot_tablas_admin

    tabla_id = payload["tabla_id"]
    modo_carga = payload.get("modo_carga") or "incremental"
    if modo_carga not in ("incremental", "reemplazo"):
        raise ValueError(f"Modo de carga desconocido: {modo_carga}")

    tablas = [t for t in chatbot_tablas_admin.listar(chatbot_id or 0) if t["id"] == tabla_id]
    if not tablas:
        raise ValueError("La tabla ya no existe.")
    tabla = tablas[0]

    grupos = chatbot_tablas_admin.grupos_desde_fuentes(_fuentes_del_job(payload))
    if not grupos:
        raise ValueError(
            "No se pudo leer ninguna grilla del material. Para actualizar una tabla el "
            "material tiene que ser una planilla (Excel/CSV) o una tabla pegada como texto: "
            "reconstruir la grilla desde un PDF o una imagen es justo el paso que rompe los "
            "datos."
        )

    mapeo = asistente_docs.mapear_material_a_tabla(
        tabla, grupos, contexto=_contexto_bot(chatbot_id), user_id=requested_by)
    unificado = chatbot_tablas_admin.unificar_grupos(
        grupos, [c["nombre"] for c in tabla["columnas"]], mapeo["mapeos"])

    if not unificado["filas"]:
        raise ValueError(
            "Ninguna fila del material pudo mapearse contra las columnas de la tabla. "
            + (" ".join(mapeo["advertencias"]) or
               "Revisá que el archivo corresponda a esta tabla.")
        )

    diff = chatbot_tablas_admin.diff_contra_tabla(
        chatbot_id or 0, tabla_id, unificado["filas"], modo_carga)
    diff["tabla_nombre"] = tabla["nombre"]
    diff["notas"] = (diff.get("notas") or []) + list(mapeo["advertencias"])
    if unificado["grupos_descartados"]:
        diff["notas"].append(
            "No se usaron las filas de " + ", ".join(unificado["grupos_descartados"]) +
            ": no se pudo determinar a qué columna corresponde cada una."
        )
    diff["material_ids"] = payload.get("material_ids") or []
    return {"documentos": [], "tablas": [], "diff": diff}


def _correr_merge(payload: Dict[str, Any], chatbot_id: Optional[int],
                  requested_by: Optional[int]) -> Dict[str, Any]:
    from AuditorIA import asistente_docs
    from app import chatbot_imagenes

    doc_ids = payload.get("doc_ids")
    engine = _get_engine()
    # Los documentos se leen ACÁ y no al encolar: entre que se encoló y se ejecuta
    # alguien pudo editarlos, y mergear contra una copia vieja pisaría ese cambio.
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, titulo, contenido_md
            FROM pagina_web.ChatbotDocMarkdown
            WHERE chatbot_id = :cid AND activo = 1
            ORDER BY orden, id
        """), {"cid": chatbot_id}).mappings().all()

    candidatos = [
        {"doc_id": r["id"], "titulo": r["titulo"], "markdown": r["contenido_md"]}
        for r in rows
        if doc_ids is None or r["id"] in set(doc_ids)
    ]
    if not candidatos:
        raise ValueError("Los documentos a los que se iba a agregar la información ya no existen.")

    fuentes = _fuentes_del_job(payload)
    imagenes = []
    if chatbot_id:
        try:
            imagenes = chatbot_imagenes.extraer_imagenes_de_fuentes(fuentes, chatbot_id, requested_by)
        except Exception:
            logger.exception(f"No se pudieron extraer imágenes automáticas para chatbot {chatbot_id}")

    resultado = asistente_docs.agregar_informacion(
        documentos_actuales=candidatos,
        fuentes_nuevas=fuentes,
        contexto=_contexto_bot(chatbot_id),
        user_id=requested_by,
        permitir_crear=payload.get("permitir_crear", True),
        imagenes_disponibles=imagenes,
    )
    resultado["material_ids"] = payload.get("material_ids") or []
    return resultado


# ---------------------------------------------------------------- limpieza

def marcar_doc_jobs_huerfanos() -> None:
    """Al arrancar el scheduler: los jobs que quedaron 'running' por un proceso
    caído pasan a 'failed'. Si no, el usuario ve "procesando" para siempre.

    Solo los de ESTE entorno: dev y prod comparten la base y un scheduler no debe
    dar por muerto un job que el otro tiene realmente en curso.
    """
    engine = _get_engine()
    try:
        with engine.begin() as conn:
            result = conn.execute(text("""
                UPDATE pagina_web.ChatbotDocJobs
                SET status = 'failed',
                    error = 'El proceso se reinició mientras se generaba la propuesta. Volvé a intentarlo.',
                    payload = NULL,
                    finished_at = SYSDATETIME()
                WHERE status = 'running' AND environment = :env
            """), {"env": settings.ENVIRONMENT})
        if result.rowcount:
            logger.warning(f"{result.rowcount} jobs de documentación huérfanos [{settings.ENVIRONMENT}] marcados como failed.")

        # Las propuestas viejas ya se aceptaron o se descartaron: no hay para qué
        # guardarlas (el payload ya se limpió al terminar, esto baja el resto).
        with engine.begin() as conn:
            purgados = conn.execute(text("""
                DELETE FROM pagina_web.ChatbotDocJobs
                WHERE status IN ('done', 'failed')
                  AND finished_at < DATEADD(day, -7, SYSDATETIME())
            """))
        if purgados.rowcount:
            logger.info(f"{purgados.rowcount} jobs de documentación viejos purgados.")
    except Exception:
        logger.exception("No se pudieron limpiar los jobs de documentación.")
