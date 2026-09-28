"""Cola de lotes de auditoría pendientes de enviar a Gemini (calidad.BatchPendientes).

POR QUÉ EXISTE
--------------
La Batch API admite 100 jobs en estado no terminal a la vez, y ese cupo lo comparten
TODAS las colas de la cuenta (las auditorías de acá y la de transcripciones). Un lote
entraba ~16 llamados, así que una corrida de 2000 audios pedía ~125 jobs de una
sentada; el 22/08/2026, sin nada raro, se llegó a ~91 en vuelo solo por apilarse las
corridas de la auditoría diaria CSV (una carpeta cada ~5 min, ~14 lotes cada una, y
cada job tarda 30-60 min en volver).

Cuando `batches.create` rebotaba por cupo, el lote se descartaba: sus audios no se
auditaban, no quedaba fila en calidad.AuditExecutionLog (se abre DESPUÉS de crear el
job), la tarea igual quedaba 'en_cola' y en el camino CSV la carpeta del fileserver se
borraba lo mismo. O sea: audios perdidos, en silencio.

Acá el lote no se descarta. Se guarda en disco el .jsonl —los audios ya comprimidos y
los prompts, listo para subir— y su metadata en la tabla; el tick del scheduler lo
manda apenas hay cupo. Para el usuario el estado sigue siendo "en cola": tarda más, no
se pierde.

POR QUÉ EL PAYLOAD VA A DISCO
-----------------------------
Un lote son megas de audio (Opus 32 kbps ~240 KB/min). En la tabla va solo la ruta del
.jsonl y la metadata que hace falta para escribir calidad.Batch_data y abrir la fila de
calidad.AuditExecutionLog, que recién se pueden escribir cuando el job existe y tiene
batch_id.

CICLO DE VIDA
-------------
    PENDIENTE --(tick, hay cupo)--> ENVIANDO --(job creado)--> ENVIADO
        ^                               |
        +---(falló el envío, reintenta)-+
        +--> ERROR (intentos agotados o el .jsonl ya no está en disco)

Desde ENVIADO el lote sigue el circuito de siempre: calidad.BatchJobs +
Auditor.check_batch_status + Auditor.procesar_batch.

Aislamiento dev/prod: el .jsonl vive en el disco de cada servidor, así que todo filtra
por settings.ENVIRONMENT (columna Entorno), igual que calidad.AudioAuditoria y
calidad.TranscripcionJobs.

Nada de acá debe romper una corrida: las funciones atrapan sus excepciones, loguean y
devuelven un valor neutro.
"""
import base64
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.config import settings
from AuditorIA.execution_log import iniciar_ejecucion

logger = logging.getLogger(__name__)

# Un lote que quedó esperando cupo NO tiene batch_id todavía, pero la corrida tiene que
# poder distinguir "quedó encolado" de "se perdió": process_batch devuelve
# "pendiente:<LoteID>" en vez del nombre del job. Para quien llama, las dos cosas son
# un lote a salvo; solo None significa que hay audios sin destino.
PREFIJO_PENDIENTE = "pendiente:"


def es_pendiente(resultado: Optional[str]) -> bool:
    """¿Este resultado de process_batch es un lote encolado (y no un job de Gemini)?"""
    return bool(resultado) and str(resultado).startswith(PREFIJO_PENDIENTE)


PENDIENTE = "PENDIENTE"
ENVIANDO = "ENVIANDO"
ENVIADO = "ENVIADO"
ERROR = "ERROR"
ESTADOS_ABIERTOS = (PENDIENTE, ENVIANDO)

# Reintentos de ENVÍO (no de procesamiento: eso es Auditor.MAX_INTENTOS_PROCESO). Un
# lote que no pudo salir 5 veces tiene un problema propio, no falta de cupo.
MAX_INTENTOS = 5

# Estados de un job de Gemini que ocupan cupo. El resto (SUCCEEDED/FAILED/CANCELLED/
# EXPIRED) ya lo liberó.
ESTADOS_EN_VUELO = {"JOB_STATE_PENDING", "JOB_STATE_RUNNING", "JOB_STATE_QUEUED"}

# Páginas de 100 que se leen de batches.list para contar el cupo. Los jobs no
# terminales son SIEMPRE los más recientes y el tope es 100, así que con 2 páginas
# alcanza de sobra; leer todo el historial son segundos por corrida al pedo.
PAGINAS_CUPO = 2


def _entorno() -> str:
    return getattr(settings, "ENVIRONMENT", "prod")


def _dir_pendientes() -> str:
    ruta = getattr(settings, "BATCH_PENDIENTES_DIR", None) or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "storage", "batch_pendientes"
    )
    os.makedirs(ruta, exist_ok=True)
    return ruta


# --------------------------------------------------------------------------- #
# Cupo de la Batch API                                                         #
# --------------------------------------------------------------------------- #
# batches.list tarda ~1,5 s por página, y calidad_batch pregunta por CADA lote: sin
# cache, una corrida de 8 lotes se comía 25 s solo en preguntar. El contador se
# refresca cada CACHE_SEGUNDOS y se incrementa a mano cuando somos nosotros los que
# creamos un job (así dos lotes seguidos no se creen ambos con el mismo conteo viejo).
_cupo_lock = threading.Lock()
_cupo_cache: Dict[str, Any] = {"ts": 0.0, "en_vuelo": None}
CACHE_SEGUNDOS = 60


def _contar_en_vuelo(gemini_api) -> Optional[int]:
    """Jobs de Gemini en estado no terminal, de TODA la cuenta. None si no se pudo."""
    try:
        en_vuelo = 0
        leidos = 0
        for job in gemini_api.batches.list(config={"page_size": 100}):
            estado = job.state.name if job.state else ""
            if estado in ESTADOS_EN_VUELO:
                en_vuelo += 1
            leidos += 1
            if leidos >= PAGINAS_CUPO * 100:
                break
        return en_vuelo
    except Exception as e:
        logger.warning("No se pudo consultar el cupo de batches en Gemini: %s", e)
        return None


def jobs_en_vuelo(gemini_api, forzar: bool = False) -> Optional[int]:
    """Cuántos jobs ocupan cupo ahora (cacheado ~1 min). None = no se pudo averiguar."""
    with _cupo_lock:
        fresco = (time.time() - _cupo_cache["ts"]) < CACHE_SEGUNDOS
        if not forzar and fresco and _cupo_cache["en_vuelo"] is not None:
            return _cupo_cache["en_vuelo"]
    valor = _contar_en_vuelo(gemini_api)
    with _cupo_lock:
        _cupo_cache["ts"] = time.time()
        _cupo_cache["en_vuelo"] = valor
    return valor


def _sumar_al_cupo(n: int = 1) -> None:
    """Anota que acabamos de ocupar cupo, sin volver a preguntarle a Google."""
    with _cupo_lock:
        if _cupo_cache["en_vuelo"] is not None:
            _cupo_cache["en_vuelo"] += n


def cupo_libre(gemini_api) -> Optional[int]:
    """Cuántos jobs más podemos crear antes de tocar el techo. None = no se pudo saber."""
    en_vuelo = jobs_en_vuelo(gemini_api)
    if en_vuelo is None:
        return None
    tope = int(getattr(settings, "BATCH_CUPO_MAX", 85))
    return max(0, tope - en_vuelo)


def hay_cupo(gemini_api) -> bool:
    """¿Se puede mandar un lote más ahora mismo?

    Si no se pudo consultar (None), se decide MANDAR: un problema para listar no tiene
    por qué frenar una corrida, y si el cupo estuviera lleno de verdad el create rebota
    y el lote cae igual en la cola.
    """
    libre = cupo_libre(gemini_api)
    return True if libre is None else libre > 0


# --------------------------------------------------------------------------- #
# Serialización del lote (JSONL de la Batch API)                               #
# --------------------------------------------------------------------------- #
def _a_json(objeto: Any) -> Any:
    """Deja el request listo para json.dumps: bytes -> base64, modelos -> dict.

    El converter del SDK devuelve la estructura REST pero con los bytes del audio
    crudos y los `types.Schema` como objetos (los serializa después su capa HTTP).
    """
    if isinstance(objeto, bytes):
        return base64.b64encode(objeto).decode("ascii")
    if isinstance(objeto, BaseModel):
        return _a_json(objeto.model_dump(mode="json", exclude_none=True, by_alias=True))
    if isinstance(objeto, dict):
        return {k: _a_json(v) for k, v in objeto.items()}
    if isinstance(objeto, (list, tuple)):
        return [_a_json(v) for v in objeto]
    return objeto


def _linea_jsonl(gemini_api, request: Dict[str, Any], modelo: str, clave: str) -> str:
    """Una línea del JSONL: {"key": ..., "request": <GenerateContentRequest>}.

    La conversión la hace el propio SDK (`_InlinedRequest_to_mldev`, el mismo camino
    que usa `batches.create(src=[requests])`) para que el request por archivo sea
    idéntico al inline: mismo `systemInstruction` a nivel request, mismo
    `generationConfig`, mismo `thinkingLevel`. Escribirlo a mano era garantía de que
    algún día un campo nuevo de la config se perdiera solo en este camino.
    """
    from google.genai.batches import _InlinedRequest_to_mldev

    convertido = _InlinedRequest_to_mldev(
        gemini_api._api_client, {**request, "model": modelo}
    )
    cuerpo = _a_json(convertido).get("request", {})
    return json.dumps({"key": clave, "request": cuerpo}, ensure_ascii=False)


def escribir_jsonl(gemini_api, requests: List[Dict[str, Any]], modelo: str,
                   ruta: Optional[str] = None) -> Optional[str]:
    """Escribe el lote como JSONL y devuelve la ruta (None si falló).

    Se escribe a un parcial y se renombra (os.replace, atómico): si el proceso muere a
    mitad, no queda un .jsonl truncado que el tick dé por bueno y mande a Gemini.
    """
    ruta = ruta or os.path.join(_dir_pendientes(), f"lote-{uuid.uuid4().hex}.jsonl")
    parcial = f"{ruta}.{os.getpid()}.parcial"
    try:
        with open(parcial, "w", encoding="utf-8") as f:
            for i, request in enumerate(requests):
                f.write(_linea_jsonl(gemini_api, request, modelo, f"req-{i}") + "\n")
        os.replace(parcial, ruta)
        return ruta
    except Exception:
        logger.exception("No se pudo escribir el JSONL del lote.")
        try:
            os.remove(parcial)
        except OSError:
            pass
        return None


# --------------------------------------------------------------------------- #
# Alta en la cola                                                              #
# --------------------------------------------------------------------------- #
def encolar(engine: Engine, *, archivo_jsonl: str, llamados: int, modelo: str,
            plantilla_id: Optional[int], user_id: Optional[int],
            filas_batch_data: List[Dict[str, Any]], kwargs_log: Optional[Dict[str, Any]],
            run_id: Optional[str], motivo: str = "") -> Optional[int]:
    """Deja el lote esperando cupo. Devuelve el LoteID (None si ni eso se pudo).

    `filas_batch_data` y `kwargs_log` son lo que se va a escribir en
    calidad.Batch_data y calidad.AuditExecutionLog cuando el lote finalmente salga:
    se calculan ahora, con el DataFrame a mano, y se guardan serializados porque el
    despacho ocurre en otro proceso (el scheduler) y horas después.
    """
    metadata = json.dumps(
        {"batch_data": filas_batch_data, "log": kwargs_log or {}},
        ensure_ascii=False, default=str,
    )
    try:
        with engine.begin() as conn:
            fila = conn.execute(text("""
                INSERT INTO calidad.BatchPendientes
                    (Entorno, Estado, RunID, ArchivoJsonl, Bytes, Llamados,
                     Modelo, PlantillaID, UserID, MetadataJson, Error)
                OUTPUT INSERTED.LoteID
                VALUES (:env, :estado, :run, :archivo, :bytes, :llamados,
                        :modelo, :plantilla, :usuario, :meta, :motivo)
            """), {
                "env": _entorno(), "estado": PENDIENTE, "run": run_id,
                "archivo": archivo_jsonl, "bytes": os.path.getsize(archivo_jsonl),
                "llamados": llamados, "modelo": modelo, "plantilla": plantilla_id,
                "usuario": user_id, "meta": metadata, "motivo": (motivo or None),
            }).first()
        lote_id = int(fila[0]) if fila else None
        logger.info(
            "Lote de %d llamados encolado (LoteID=%s) a la espera de cupo en Gemini. %s",
            llamados, lote_id, motivo,
        )
        return lote_id
    except Exception:
        logger.exception(
            "No se pudo encolar el lote pendiente; el JSONL queda en %s", archivo_jsonl
        )
        return None


def pendientes_de_corrida(engine: Engine, run_id: Optional[str]) -> int:
    """Lotes de esta corrida que todavía no salieron (para no darla por terminada)."""
    if not run_id:
        return 0
    try:
        with engine.connect() as conn:
            fila = conn.execute(text("""
                SELECT COUNT(*) FROM calidad.BatchPendientes
                WHERE RunID = :run AND Estado IN ('PENDIENTE', 'ENVIANDO')
            """), {"run": run_id}).first()
        return int(fila[0]) if fila else 0
    except Exception:
        logger.exception("No se pudo contar los lotes pendientes de la corrida %s.", run_id)
        return 0


# --------------------------------------------------------------------------- #
# Transiciones                                                                 #
# --------------------------------------------------------------------------- #
def _devolver_a_pendiente(engine: Engine, lote_id: int, motivo: str) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE calidad.BatchPendientes
                SET Estado = :estado, Error = :motivo
                WHERE LoteID = :id
            """), {"estado": PENDIENTE, "motivo": motivo[:1000], "id": lote_id})
    except Exception:
        logger.exception("No se pudo devolver a PENDIENTE el lote %s.", lote_id)


def _cerrar_con_error(engine: Engine, lote_id: int, motivo: str) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE calidad.BatchPendientes
                SET Estado = :estado, Error = :motivo, FechaFin = SYSUTCDATETIME()
                WHERE LoteID = :id
            """), {"estado": ERROR, "motivo": motivo[:1000], "id": lote_id})
    except Exception:
        logger.exception("No se pudo cerrar como ERROR el lote %s.", lote_id)


def marcar_huerfanos(engine: Engine) -> None:
    """Lotes que quedaron en ENVIANDO por un proceso caído: vuelven a la cola.

    Se llama al arrancar el scheduler. Un lote en ENVIANDO sin nadie que lo esté
    mandando no lo toma nadie más (el claim solo mira PENDIENTE) y sus audios se
    quedarían en disco para siempre.
    """
    try:
        with engine.begin() as conn:
            filas = conn.execute(text("""
                UPDATE calidad.BatchPendientes
                SET Estado = :pendiente, Error = 'El proceso se cortó mientras se enviaba.'
                WHERE Entorno = :env AND Estado = :enviando
            """), {"pendiente": PENDIENTE, "enviando": ENVIANDO, "env": _entorno()})
        if filas.rowcount:
            logger.warning(
                "Se devolvieron %d lote(s) colgados en ENVIANDO a la cola.", filas.rowcount
            )
    except Exception:
        logger.exception("No se pudieron recuperar los lotes colgados en ENVIANDO.")


# Claim atómico FIFO de la cola. Vive en una constante para que el test de SQL pueda
# validarlo contra el esquema real sin ejecutarlo (ver tests/test_batch_cola_sql.py).
#
# OJO al tocarlo: un UPDATE sobre un CTE SOLO puede escribir las columnas que el CTE
# expone en su SELECT. Agregar algo al SET sin agregarlo arriba compila igual y revienta
# recién en producción con "Invalid column name" (le pasó a la cola de transcripciones
# el 2026-08-25). PARSEONLY no lo detecta: es sintácticamente perfecto.
_SQL_RECLAMAR_PENDIENTES = """
    WITH siguientes AS (
        SELECT TOP (:limite) LoteID, Estado, Intentos, ArchivoJsonl,
               MetadataJson, Modelo, Llamados, RunID
        FROM calidad.BatchPendientes WITH (UPDLOCK, READPAST, ROWLOCK)
        WHERE Entorno = :env AND Estado = :pendiente
        ORDER BY LoteID
    )
    UPDATE siguientes
    SET Estado = :enviando, Intentos = Intentos + 1
    OUTPUT INSERTED.LoteID, INSERTED.Intentos, INSERTED.ArchivoJsonl,
           INSERTED.MetadataJson, INSERTED.Modelo, INSERTED.Llamados,
           INSERTED.RunID
"""


def _reclamar_pendientes(engine: Engine, limite: int) -> List[Dict[str, Any]]:
    """Toma hasta `limite` lotes y los marca ENVIANDO (claim atómico FIFO).

    El UPDATE ... OUTPUT sobre un CTE con READPAST/UPDLOCK es lo que evita que dos
    ticks (o el tick y un envío en caliente) reclamen el mismo lote y lo manden dos
    veces: eso serían dos jobs pagos con los mismos audios.
    """
    if limite <= 0:
        return []
    try:
        with engine.begin() as conn:
            filas = conn.execute(text(_SQL_RECLAMAR_PENDIENTES), {
                "limite": limite, "env": _entorno(),
                "pendiente": PENDIENTE, "enviando": ENVIANDO,
            }).fetchall()
        return [{
            "lote_id": int(f[0]), "intentos": int(f[1]), "archivo": f[2],
            "metadata": f[3], "modelo": f[4], "llamados": int(f[5] or 0), "run_id": f[6],
        } for f in filas]
    except Exception:
        logger.exception("No se pudieron reclamar lotes pendientes.")
        return []


# --------------------------------------------------------------------------- #
# Envío                                                                        #
# --------------------------------------------------------------------------- #
def crear_job(gemini_api, archivo_jsonl: str, modelo: str, etiqueta: str = "calidad-audio-batch"):
    """Sube el JSONL y crea el job de Gemini. Devuelve el BatchJob.

    Por archivo y no inline (`src=[requests]`) porque el request inline no puede pasar
    de 20 MB —eran ~16 llamados por job, o sea ~125 jobs para 2000 audios, contra un
    techo de 100 en vuelo—. Un archivo de entrada llega a 2 GB, así que el lote lo
    define el tamaño que queramos, no el transporte. El audio sigue viajando inline
    DENTRO del JSONL (base64): los modelos 3.x rechazan con 403 cualquier file_uri de
    la File API, y eso no cambió (ver gemini_files.py).

    El archivo subido se borra solo a las 48 h (expiración de la File API); no lo
    borramos nosotros porque el job lo lee mientras corre y la API no expone de qué
    archivo salió un job (`job.src` viene en None). La File API tiene 20 GB: un lote de
    250 llamados pesa ~330 MB en base64, así que hacen falta ~60 lotes vivos a la vez
    para llenarla (unas 50 veces el volumen diario de hoy). Si igual se llenara, el
    upload falla y el lote vuelve a la cola: no se pierde nada.
    """
    subido = gemini_api.files.upload(
        file=archivo_jsonl,
        config={"display_name": f"{etiqueta}-{int(time.time())}", "mime_type": "jsonl"},
    )
    job = gemini_api.batches.create(
        model=modelo,
        src=subido.name,
        config={"display_name": f"{etiqueta}-{int(time.time())}"},
    )
    _sumar_al_cupo(1)
    return job


def _registrar_envio(engine: Engine, lote_id: int, batch_id: str) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE calidad.BatchPendientes
                SET Estado = :estado, BatchID = :batch, FechaEnvio = SYSUTCDATETIME(),
                    FechaFin = SYSUTCDATETIME(), Error = NULL
                WHERE LoteID = :id
            """), {"estado": ENVIADO, "batch": batch_id, "id": lote_id})
    except Exception:
        logger.exception(
            "El lote %s salió a Gemini como %s pero no se pudo marcar ENVIADO.",
            lote_id, batch_id,
        )


def limpiar_huerfanos_en_disco(engine: Engine, horas: int = 24) -> int:
    """Borra los .jsonl que no le pertenecen a ningún lote vivo de la cola.

    Un lote deja el archivo huérfano cuando se serializó pero el INSERT en la tabla
    falló (ahí la corrida se reporta como fallida y los audios se reintentan desde el
    origen, pero el archivo queda). Solo se tocan los que tienen más de `horas`: uno
    recién escrito puede ser de un lote que todavía no alcanzó a insertar su fila.
    """
    try:
        directorio = _dir_pendientes()
        corte = time.time() - horas * 3600
        candidatos = [
            os.path.join(directorio, n) for n in os.listdir(directorio)
            if n.endswith(".jsonl") and os.path.getmtime(os.path.join(directorio, n)) < corte
        ]
        if not candidatos:
            return 0

        with engine.connect() as conn:
            vivos = {
                fila[0] for fila in conn.execute(text("""
                    SELECT ArchivoJsonl FROM calidad.BatchPendientes
                    WHERE Entorno = :env AND Estado IN ('PENDIENTE', 'ENVIANDO')
                """), {"env": _entorno()}).fetchall()
            }

        borrados = 0
        for ruta in candidatos:
            if ruta in vivos:
                continue
            try:
                os.remove(ruta)
                borrados += 1
            except OSError:
                pass
        if borrados:
            logger.warning("Se borraron %d archivo(s) de lote huérfanos en disco.", borrados)
        return borrados
    except Exception:
        logger.exception("No se pudieron limpiar los archivos de lote huérfanos.")
        return 0


def despachar_pendientes(engine: Engine, gemini_api, limite: Optional[int] = None) -> int:
    """Manda a Gemini los lotes que estaban esperando cupo. Devuelve cuántos salieron.

    Lo llama el tick del scheduler. El tope real es el cupo: si no hay, no se reclama
    nada y los lotes quedan intactos para el tick siguiente.
    """
    if gemini_api is None:
        return 0

    libre = cupo_libre(gemini_api)
    if libre is not None and libre <= 0:
        logger.info("Cola de lotes: sin cupo en Gemini (%s en vuelo), se espera al próximo tick.",
                    jobs_en_vuelo(gemini_api))
        return 0

    tope = int(limite or getattr(settings, "BATCH_COLA_MAX_POR_TICK", 20))
    if libre is not None:
        tope = min(tope, libre)

    reclamados = _reclamar_pendientes(engine, tope)
    if not reclamados:
        return 0

    enviados = 0
    for lote in reclamados:
        lote_id = lote["lote_id"]

        if lote["intentos"] > MAX_INTENTOS:
            _cerrar_con_error(
                engine, lote_id,
                f"No se pudo enviar a Gemini después de {MAX_INTENTOS} intentos.",
            )
            continue

        if not lote["archivo"] or not os.path.exists(lote["archivo"]):
            _cerrar_con_error(
                engine, lote_id,
                "El archivo del lote ya no está en disco: sus audios no se auditaron.",
            )
            continue

        try:
            job = crear_job(gemini_api, lote["archivo"], lote["modelo"])
        except Exception as e:
            _devolver_a_pendiente(engine, lote_id, f"Fallo al enviar: {e}")
            logger.warning("Lote %s: no se pudo crear el job en Gemini (%s).", lote_id, e)
            continue

        # El job ya existe y se va a facturar, pero sin Batch_data sus respuestas no se
        # pueden atar a ninguna interacción: sería un lote pago que no sirve para nada.
        # Se cancela y el lote vuelve a la cola con el .jsonl intacto, igual que hace el
        # envío directo en gemini.py::process_batch.
        try:
            metadata = json.loads(lote["metadata"] or "{}")
            registrar_lote_enviado(
                engine, batch_id=job.name,
                filas_batch_data=metadata.get("batch_data") or [],
                kwargs_log=metadata.get("log") or {},
            )
        except Exception as e:
            logger.exception(
                "El lote %s se creó en Gemini como %s pero no se pudo registrar su "
                "metadata; se cancela el job y el lote vuelve a la cola.", lote_id, job.name,
            )
            try:
                gemini_api.batches.cancel(name=job.name)
            except Exception as cancel_e:
                logger.error("No se pudo cancelar el job %s: %s", job.name, cancel_e)
            _devolver_a_pendiente(engine, lote_id, f"No se pudo registrar el lote enviado: {e}")
            continue

        _registrar_envio(engine, lote_id, job.name)
        try:
            os.remove(lote["archivo"])
        except OSError:
            pass

        enviados += 1
        logger.info(
            "Cola de lotes: lote %s enviado a Gemini como %s (%d llamados).",
            lote_id, job.name, lote["llamados"],
        )

    return enviados


# --------------------------------------------------------------------------- #
# Registro del lote ya enviado (lo comparten el envío directo y el diferido)   #
# --------------------------------------------------------------------------- #
def registrar_lote_enviado(engine: Engine, *, batch_id: str,
                           filas_batch_data: Iterable[Dict[str, Any]],
                           kwargs_log: Optional[Dict[str, Any]] = None) -> None:
    """Escribe calidad.BatchJobs + calidad.Batch_data y abre la fila del log.

    Es el punto en el que un job de Gemini pasa a existir para el sistema: sin
    Batch_data, las respuestas que vuelvan horas después no se pueden atar a ninguna
    interacción. Lo llaman los dos caminos —el envío inmediato (gemini.py) y el
    diferido (esta cola)— para que el lote quede igual haya salido cuando haya salido.

    El log de ejecución es observabilidad: si falla, se avisa pero no se corta.
    """
    filas = list(filas_batch_data)
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            # Entorno: el lote lo recoge y procesa el scheduler del servidor que lo mandó
            # (Auditor.check_batch_status filtra por él).
            conn.execute(
                text("INSERT INTO calidad.BatchJobs (name, status, created_at, Entorno) "
                     "VALUES (:name, :status, :created_at, :env)"),
                {"name": batch_id, "status": "JOB_STATE_PENDING", "created_at": datetime.now(),
                 "env": _entorno()},
            )
            if filas:
                conn.execute(
                    text("INSERT INTO calidad.Batch_data (batch_id, segment_id, columna, valor) "
                         "VALUES (:batch_id, :segment_id, :columna, :valor)"),
                    [{"batch_id": batch_id, "segment_id": f["segment_id"],
                      "columna": f["columna"], "valor": f["valor"]} for f in filas],
                )
            transaction.commit()
        except Exception:
            transaction.rollback()
            raise

    if kwargs_log:
        try:
            iniciar_ejecucion(engine, batch_id=batch_id, **kwargs_log)
        except Exception as e:
            logger.error(
                "No se pudo registrar el inicio del batch %s en AuditExecutionLog: %s",
                batch_id, e,
            )


# --------------------------------------------------------------------------- #
# Lectura del resultado                                                        #
# --------------------------------------------------------------------------- #
# Un lote mandado por archivo devuelve el resultado TAMBIÉN por archivo
# (`dest.file_name`), no en `dest.inlined_responses`. Estas clases envuelven cada
# línea del JSONL de salida con la misma superficie que usa el parseo de siempre
# (candidates[0].content.parts, .text, .usage_metadata), así Auditor.procesar_batch
# no distingue de dónde vino la respuesta.
#
# Por qué a mano y no `types.GenerateContentResponse.model_validate`: el modelo del
# SDK prohíbe campos desconocidos y la API ya devuelve algunos que esta versión no
# conoce (p. ej. `serviceTier`), así que validar rompía el lote entero por un campo
# que ni miramos.
class _Uso:
    def __init__(self, d: Dict[str, Any]):
        d = d or {}
        self.prompt_token_count = d.get("promptTokenCount") or d.get("prompt_token_count")
        self.candidates_token_count = d.get("candidatesTokenCount") or d.get("candidates_token_count")
        self.thoughts_token_count = d.get("thoughtsTokenCount") or d.get("thoughts_token_count")
        # Los tokens que Gemini cobró como cacheados (ver AuditorIA/cache_plantillas.py).
        # Sin esto el libro de uso de IA los guarda en NULL y no hay forma de ver si la
        # caché está pegando: es la única medición del ahorro que tenemos.
        self.cached_content_token_count = (
            d.get("cachedContentTokenCount") or d.get("cached_content_token_count")
        )


class _Parte:
    def __init__(self, d: Dict[str, Any]):
        self.text = d.get("text")
        self.thought = bool(d.get("thought"))


class _Contenido:
    def __init__(self, d: Dict[str, Any]):
        self.parts = [_Parte(p) for p in (d or {}).get("parts") or []]


class _Candidato:
    def __init__(self, d: Dict[str, Any]):
        self.content = _Contenido(d.get("content") or {})
        self.finish_reason = d.get("finishReason") or d.get("finish_reason")


class RespuestaArchivo:
    """Una respuesta del JSONL de salida, con la forma que espera el parseo."""

    def __init__(self, d: Dict[str, Any]):
        self._raw = d or {}
        self.candidates = [_Candidato(c) for c in self._raw.get("candidates") or []]
        self.usage_metadata = _Uso(self._raw.get("usageMetadata") or self._raw.get("usage_metadata") or {})

    @property
    def text(self) -> str:
        partes = self.candidates[0].content.parts if self.candidates else []
        return "".join(p.text for p in partes if p.text and not p.thought)


class _JobArchivo:
    """Equivalente de un `inlined_response`: trae `response` o `error`."""

    def __init__(self, d: Dict[str, Any]):
        self.response = RespuestaArchivo(d["response"]) if d.get("response") else None
        self.error = d.get("error")


def _segment_id_de_clave(clave: Any, orden: int) -> int:
    """'req-7' -> 7. El orden de las líneas del archivo NO está garantizado, así que
    la posición dentro del lote viaja en la `key` de cada request (ver escribir_jsonl).
    Si por lo que sea no viene, se cae al orden de lectura."""
    try:
        return int(str(clave).rsplit("-", 1)[1])
    except (AttributeError, IndexError, ValueError):
        return orden


def leer_respuestas(gemini_api, batch_job) -> List[Any]:
    """Respuestas de un lote terminado como [(segment_id, job), ...].

    Sirve para las dos formas: el lote viejo/inline (`dest.inlined_responses`, donde
    la posición ES el segment_id) y el mandado por archivo (`dest.file_name`).
    """
    dest = getattr(batch_job, "dest", None)
    if dest is None:
        return []

    if getattr(dest, "inlined_responses", None):
        return list(enumerate(dest.inlined_responses))

    nombre = getattr(dest, "file_name", None)
    if not nombre:
        return []

    contenido = gemini_api.files.download(file=nombre)
    if isinstance(contenido, bytes):
        contenido = contenido.decode("utf-8")

    respuestas = []
    for orden, linea in enumerate(contenido.splitlines()):
        linea = linea.strip()
        if not linea:
            continue
        try:
            d = json.loads(linea)
        except json.JSONDecodeError:
            logger.error("Línea ilegible en el resultado del lote %s (posición %d).",
                         getattr(batch_job, "name", "?"), orden)
            continue
        respuestas.append((_segment_id_de_clave(d.get("key"), orden), _JobArchivo(d)))
    return respuestas
