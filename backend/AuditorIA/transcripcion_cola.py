"""Cola de transcripciones a demanda (calidad.TranscripcionJobs).

POR QUÉ EXISTE
--------------
La transcripción solo se obtiene EN EL MOMENTO de auditar: viaja anidada en el mismo
llamado de calidad (ver gemini.py::process_batch y apply_auditoria_threads), sobre la
misma lectura del audio. Si la corrida no la pidió, ese llamado se queda sin
transcripción para siempre y la única forma de conseguirla era re-auditar, o sea pagar
de nuevo la auditoría entera.

Desde que el audio auditado se conserva en disco (AuditorIA/audio_store.py) eso ya no
hace falta: el audio está ahí, así que se puede transcribir después, solo. Esta cola es
el "después": el usuario encola desde "Auditorías Realizadas" (una fila desde el
reproductor, o varias seleccionadas), la request responde al instante y el scheduler
resuelve los pedidos contra el motor configurado.

MOTORES
-------
El motor se elige POR PEDIDO según cuántos llamados tenga (ver `elegir_motor`), porque
los dos casos de uso son distintos:

- `gemini_flex` (pedidos CHICOS: el auditor está escuchando un llamado y quiere leerlo):
  llamado SINCRÓNICO al tier Flex de Gemini. Cuesta lo mismo que batch —50% del precio
  estándar— pero responde en minutos (Google apunta a 1-15 min y no garantiza latencia)
  en vez de horas. A cambio es best-effort: si no hay capacidad devuelve 429/503 y NO
  sube solo a estándar (no puede encarecerse por sorpresa). Ante falta de capacidad se
  reintenta con backoff y, si no cede, el pedido se DEGRADA a `gemini_batch`: el auditor
  igual lo va a tener, más tarde y al mismo precio.
- `gemini_batch` (pedidos GRANDES: se tildaron muchas filas de la grilla): un job de
  Gemini en modo BATCH con el audio inline. Mismo precio que flex, resultado en horas,
  pero soporta cientos de llamados en un request sin pelear por capacidad.
- `fastwhisper` (FUTURO, cuando el servidor tenga GPU): transcripción local, sincrónica
  y sin costo por token. El punto de extensión es `_despachar_fastwhisper`: la cola, la
  UI, la tabla y el guardado quedan igual; cambia `settings.TRANSCRIPCION_MOTOR` y los
  jobs pasan de PENDIENTE a LISTO en el mismo tick, sin BatchID.

CICLO DE VIDA DE UN JOB
-----------------------
    flex:   PENDIENTE --(al encolar)--> ENVIANDO --(respuesta)--> LISTO
                                            |
                                            +--> PENDIENTE como batch (Flex sin capacidad)
    batch:  PENDIENTE --(tick)--> ENVIANDO --(lote creado)--> ENVIADO --(resultado)--> LISTO

    En cualquiera:  --> PENDIENTE (falló el envío, se reintenta)
                    --> ERROR (sin audio / intentos agotados / lote fallido)

El flex arranca en el acto: el endpoint que encola dispara el despacho en background
(no espera a que el tick del scheduler lo tome 5 minutos después). El tick igual barre
lo que haya quedado, así que un reinicio de la API no pierde el pedido.

Aislamiento dev/prod: el audio vive en el disco de cada servidor, así que TODO filtra
por settings.ENVIRONMENT (columna Entorno), igual que calidad.AudioAuditoria.

Nada de acá debe romper una pantalla ni el scheduler: las funciones atrapan sus
excepciones, loguean y devuelven un valor neutro.
"""
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from google.genai import types
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.types import NVARCHAR

from app.config import settings
from AuditorIA import audio_store
from AuditorIA.modelos_ia import MODELO_IA_DEFAULT
from AuditorIA import razonamiento
from AuditorIA.Trancribir import (
    _ajustar_tiempos_segments,
    ids_con_transcripcion,
    prompt as PROMPT_TRANSCRIPCION,
)

logger = logging.getLogger(__name__)

# --- Estados (ver el comentario de la migración 2026-08-14c) ---
PENDIENTE = "PENDIENTE"
ENVIANDO = "ENVIANDO"
ENVIADO = "ENVIADO"
LISTO = "LISTO"
ERROR = "ERROR"
ESTADOS_ABIERTOS = (PENDIENTE, ENVIANDO, ENVIADO)

# --- Motores ---
MOTOR_GEMINI_FLEX = "gemini_flex"
MOTOR_GEMINI_BATCH = "gemini_batch"
MOTOR_FASTWHISPER = "fastwhisper"

# Códigos con los que Gemini avisa que el tier Flex no tiene capacidad AHORA (la
# documentación lo dice explícito: "429 Too Many Requests" o "503 Service Unavailable",
# reintentar con backoff). No es un error del pedido: es que hay que esperar o irse a
# batch.
CODIGOS_SIN_CAPACIDAD = (429, 503)

# Un pedido flex que quedó en ENVIANDO más tiempo que esto es de un proceso que se
# murió a mitad del llamado (la API se reinició): vuelve a la cola. Holgado a propósito
# —Flex apunta a 1-15 min— para no re-mandar (y pagar dos veces) uno que todavía corre.
MINUTOS_ENVIO_COLGADO = 30

# Un envío fallido (red, cuota de Gemini) se reintenta en el próximo tick; pasado este
# número el job se abandona explícitamente en vez de girar para siempre. Mismo criterio
# que Auditor.MAX_INTENTOS_PROCESO.
MAX_INTENTOS = 5

# El audio viaja INLINE en el request del batch (los modelos 3.x rechazan los file_uri
# con 403, ver gemini_files.audio_parte_activa), y el request completo no puede superar
# los 20 MB. Mismo corte que usa calidad_batch para las auditorías.
MAX_BYTES_POR_LOTE = 15 * 1024 * 1024


def _entorno() -> str:
    return getattr(settings, "ENVIRONMENT", "prod") or "prod"


def motor_actual() -> str:
    """Motor con el que se despachan los pedidos nuevos (settings.TRANSCRIPCION_MOTOR)."""
    return getattr(settings, "TRANSCRIPCION_MOTOR", MOTOR_GEMINI_BATCH) or MOTOR_GEMINI_BATCH


def _modelo_actual() -> str:
    """Modelo de Gemini para transcribir. Vacío en config = el default del catálogo."""
    return getattr(settings, "TRANSCRIPCION_MODELO", "") or MODELO_IA_DEFAULT


def elegir_motor(cantidad: int) -> str:
    """Con qué motor resolver un pedido de `cantidad` llamados.

    Flex y batch cuestan lo MISMO (50% del precio estándar), así que el criterio no es
    la plata: es quién está esperando. Un pedido chico sale del reproductor de audio —el
    auditor está escuchando ese llamado ahora— y por flex lo tiene en minutos. Un pedido
    grande sale de tildar filas en la grilla: nadie mira la pantalla hasta que estén, y
    cientos de llamados sincrónicos se comerían la capacidad flex a fuerza de 429.
    """
    configurado = motor_actual()
    if configurado != MOTOR_GEMINI_BATCH:
        # fastwhisper local ya es inmediato; y si alguien fuerza flex por config, manda.
        return configurado
    tope = int(getattr(settings, "TRANSCRIPCION_FLEX_MAX_PEDIDO", 10))
    return MOTOR_GEMINI_FLEX if 0 < cantidad <= tope else MOTOR_GEMINI_BATCH


def _normalizar_ids(ids: Iterable) -> List[str]:
    """Ids únicos, como texto y sin vacíos/NaN (preserva el orden de entrada)."""
    limpios = []
    for i in ids or []:
        if i is None:
            continue
        texto = str(i).strip()
        if not texto or texto.lower() in ("nan", "none"):
            continue
        limpios.append(texto)
    return list(dict.fromkeys(limpios))


# --------------------------------------------------------------------------- #
# Encolar / consultar (lo que usa la pantalla)                                 #
# --------------------------------------------------------------------------- #

def _ids_con_job_abierto(engine: Engine, ids: List[str]) -> set:
    """Ids que ya tienen un pedido en curso (PENDIENTE/ENVIANDO/ENVIADO)."""
    abiertos: set = set()
    if not ids:
        return abiertos
    try:
        with engine.connect() as conn:
            for inicio in range(0, len(ids), 1000):
                lote = ids[inicio:inicio + 1000]
                q = text("""
                    SELECT DISTINCT IdAplicativo
                    FROM calidad.TranscripcionJobs
                    WHERE Entorno = :env AND Estado IN :estados AND IdAplicativo IN :ids
                """).bindparams(
                    bindparam("estados", expanding=True),
                    bindparam("ids", expanding=True),
                )
                filas = conn.execute(q, {
                    "env": _entorno(), "estados": list(ESTADOS_ABIERTOS), "ids": lote,
                }).fetchall()
                abiertos.update(str(f[0]) for f in filas)
    except Exception:
        logger.exception("No se pudo consultar qué transcripciones ya estaban encoladas.")
    return abiertos


def encolar(engine: Engine, ids: Iterable, user_id: Optional[int] = None,
            motor: Optional[str] = None) -> Dict[str, List[str]]:
    """Encola la transcripción de las interacciones que la necesitan y la pueden tener.

    Devuelve el destino de CADA id pedido, para que la pantalla explique qué pasó:
      - `encoladas`: quedaron en la cola.
      - `ya_en_cola`: ya tenían un pedido en curso (no se duplica el gasto).
      - `ya_transcriptas`: ya tienen transcripción guardada (mismo audio = misma
        transcripción; volver a pedirla es tirar plata).
      - `sin_audio`: no hay audio conservado para ese llamado (nunca se guardó, o lo
        descartó el tope FIFO del store), así que no hay nada que transcribir.
      - `fallidas`: no se pudieron encolar por un error de la base (p. ej. la migración
        2026-08-14c todavía no aplicada). Van aparte para que la pantalla no diga "ya
        estaban en cola" cuando en realidad no quedó nada anotado.

    El motor sale de `elegir_motor` sobre la cantidad REAL a encolar (flex para los
    pedidos chicos, batch para los grandes), salvo que el caller imponga uno.
    """
    pedidos = _normalizar_ids(ids)
    resultado: Dict[str, List[str]] = {
        "encoladas": [], "ya_en_cola": [], "ya_transcriptas": [], "sin_audio": [], "fallidas": [],
    }
    if not pedidos:
        return resultado

    con_audio = audio_store.ids_con_audio(engine, pedidos)
    try:
        ya_transcriptas = ids_con_transcripcion(engine, pedidos)
    except Exception:
        logger.exception("No se pudo verificar qué interacciones ya tienen transcripción.")
        ya_transcriptas = set()
    abiertos = _ids_con_job_abierto(engine, pedidos)

    a_encolar = []
    for id_aplicativo in pedidos:
        if id_aplicativo in ya_transcriptas:
            resultado["ya_transcriptas"].append(id_aplicativo)
        elif id_aplicativo in abiertos:
            resultado["ya_en_cola"].append(id_aplicativo)
        elif id_aplicativo not in con_audio:
            resultado["sin_audio"].append(id_aplicativo)
        else:
            a_encolar.append(id_aplicativo)

    if not a_encolar:
        return resultado

    insert = text("""
        INSERT INTO calidad.TranscripcionJobs (Entorno, IdAplicativo, Estado, Motor, SolicitadoPor)
        VALUES (:env, :id, :estado, :motor, :user_id)
    """)
    # El motor se decide con el TAMAÑO real del pedido (lo que se va a encolar), no con
    # lo que pidió la pantalla: si de 30 ids 29 ya estaban transcriptos, el que queda es
    # un pedido chico y merece flex.
    motor = motor or elegir_motor(len(a_encolar))
    for id_aplicativo in a_encolar:
        try:
            # De a uno y no en bloque: el índice único de "job abierto" puede rechazar
            # una fila (otro usuario encoló el mismo llamado un instante antes) y eso no
            # debe llevarse puesto el resto del pedido.
            with engine.begin() as conn:
                conn.execute(insert, {
                    "env": _entorno(), "id": id_aplicativo, "estado": PENDIENTE,
                    "motor": motor, "user_id": user_id,
                })
            resultado["encoladas"].append(id_aplicativo)
        except IntegrityError:
            # El índice único de job abierto: otro usuario (o el mismo, dos clics) lo
            # encoló un instante antes. No es un error, es el duplicado que se evita.
            resultado["ya_en_cola"].append(id_aplicativo)
        except Exception as e:
            logger.warning("No se pudo encolar la transcripción de %s: %s", id_aplicativo, e)
            resultado["fallidas"].append(id_aplicativo)

    if resultado["encoladas"]:
        logger.info(
            "Encoladas %d transcripciones [%s, motor=%s] pedidas por el usuario %s.",
            len(resultado["encoladas"]), _entorno(), motor, user_id,
        )
    return resultado


def estados(engine: Engine, ids: Iterable) -> Dict[str, Dict[str, Any]]:
    """Último pedido de cada id: `{IdAplicativo: {estado, motor, error}}`.

    Lo consume la grilla de "Auditorías Realizadas" (reloj de "en cola" en vez del
    botón) y el modal, que hace polling mientras espera una transcripción flex: por eso
    viaja también el motor —para saber si esperar minutos u horas— y el error, para
    poder decir POR QUÉ falló sin ir a los logs.
    """
    pedidos = _normalizar_ids(ids)
    salida: Dict[str, Dict[str, Any]] = {}
    if not pedidos:
        return salida
    try:
        with engine.connect() as conn:
            for inicio in range(0, len(pedidos), 1000):
                lote = pedidos[inicio:inicio + 1000]
                q = text("""
                    SELECT j.IdAplicativo, j.Estado, j.Motor, j.Error
                    FROM calidad.TranscripcionJobs AS j
                    JOIN (
                        SELECT IdAplicativo, MAX(JobID) AS JobID
                        FROM calidad.TranscripcionJobs
                        WHERE Entorno = :env AND IdAplicativo IN :ids
                        GROUP BY IdAplicativo
                    ) AS ultimo ON ultimo.JobID = j.JobID
                """).bindparams(bindparam("ids", expanding=True))
                for fila in conn.execute(q, {"env": _entorno(), "ids": lote}).fetchall():
                    salida[str(fila[0])] = {
                        "estado": str(fila[1]),
                        "motor": str(fila[2]) if fila[2] else None,
                        # El Error de un job LISTO es el de un intento anterior que se
                        # recuperó (p. ej. la degradación de flex a batch): no es una falla.
                        "error": str(fila[3]) if (fila[3] and str(fila[1]) == ERROR) else None,
                    }
    except Exception:
        logger.exception("No se pudo consultar el estado de las transcripciones encoladas.")
    return salida


# --------------------------------------------------------------------------- #
# Transiciones de estado                                                       #
# --------------------------------------------------------------------------- #

def _cerrar(engine: Engine, job_ids: Iterable[int], estado: str,
            error: Optional[str] = None, modelo: Optional[str] = None) -> None:
    """Deja los jobs en un estado final (LISTO/ERROR) con su fecha de fin."""
    ids = [int(j) for j in job_ids]
    if not ids:
        return
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE calidad.TranscripcionJobs
                SET Estado = :estado, Error = :error, FechaFin = SYSUTCDATETIME(),
                    Modelo = COALESCE(:modelo, Modelo)
                WHERE JobID IN :ids
            """).bindparams(bindparam("ids", expanding=True)),
                {"estado": estado, "error": error[:1000] if error else None,
                 "modelo": modelo, "ids": ids})
    except Exception:
        logger.exception("No se pudo cerrar como %s los jobs de transcripción %s.", estado, ids)


def _devolver_a_pendiente(engine: Engine, job_ids: Iterable[int], motivo: str) -> None:
    """El envío falló: el job vuelve a la cola para el próximo tick (ya sumó un intento)."""
    ids = [int(j) for j in job_ids]
    if not ids:
        return
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE calidad.TranscripcionJobs
                SET Estado = :estado, Error = :error
                WHERE JobID IN :ids
            """).bindparams(bindparam("ids", expanding=True)),
                {"estado": PENDIENTE, "error": motivo[:1000], "ids": ids})
    except Exception:
        logger.exception("No se pudo devolver a la cola los jobs de transcripción %s.", ids)


def _degradar_a_batch(engine: Engine, job_ids: Iterable[int], motivo: str) -> None:
    """Flex no tuvo capacidad: el pedido vuelve a la cola como batch.

    Es la red de seguridad del tier flex, que es best-effort por diseño: en vez de
    fallarle al auditor, la transcripción se resuelve por lote (mismo precio, más
    tarde). El motivo queda escrito para poder explicar la demora.
    """
    ids = [int(j) for j in job_ids]
    if not ids:
        return
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE calidad.TranscripcionJobs
                SET Motor = :batch, Estado = :pendiente, Error = :motivo,
                    BatchID = NULL, SegmentID = NULL
                WHERE JobID IN :ids
            """).bindparams(bindparam("ids", expanding=True)),
                {"batch": MOTOR_GEMINI_BATCH, "pendiente": PENDIENTE,
                 "motivo": motivo[:1000], "ids": ids})
        logger.warning("%d transcripciones flex degradadas a batch: %s", len(ids), motivo)
    except Exception:
        logger.exception("No se pudieron degradar a batch los jobs %s.", ids)


def recuperar_envios_colgados(engine: Engine) -> None:
    """Devuelve a la cola los pedidos que quedaron ENVIANDO de un proceso muerto.

    Espejo de `marcar_huerfanos` pero acotado por tiempo, para que corra en cada tick
    sin robarle el trabajo a un flex que todavía está esperando la respuesta de Gemini.
    """
    try:
        with engine.begin() as conn:
            resultado = conn.execute(text("""
                UPDATE calidad.TranscripcionJobs
                SET Estado = :pendiente,
                    Error = 'El envío quedó colgado (se reinició el proceso); se reintenta.'
                WHERE Estado = :enviando AND Entorno = :env
                  AND (FechaEnvio IS NULL
                       OR FechaEnvio < DATEADD(minute, :minutos, SYSUTCDATETIME()))
            """), {"pendiente": PENDIENTE, "enviando": ENVIANDO, "env": _entorno(),
                   "minutos": -MINUTOS_ENVIO_COLGADO})
        if resultado.rowcount:
            logger.warning("%d transcripciones colgadas devueltas a la cola.", resultado.rowcount)
    except Exception:
        logger.exception("No se pudieron recuperar las transcripciones colgadas.")


def marcar_huerfanos(engine: Engine) -> None:
    """Al arrancar el scheduler: los jobs que quedaron en ENVIANDO por un proceso caído
    vuelven a PENDIENTE.

    Solo los de ESTE entorno: dev y prod comparten la base y un scheduler no debe
    resucitar un job que el otro está mandando en este momento.
    """
    try:
        with engine.begin() as conn:
            resultado = conn.execute(text("""
                UPDATE calidad.TranscripcionJobs
                SET Estado = :pendiente,
                    Error = 'El proceso se reinició mientras se enviaba el lote; se reintenta.'
                WHERE Estado = :enviando AND Entorno = :env
            """), {"pendiente": PENDIENTE, "enviando": ENVIANDO, "env": _entorno()})
        if resultado.rowcount:
            logger.warning(
                "%d transcripciones huérfanas [%s] devueltas a la cola.",
                resultado.rowcount, _entorno(),
            )
    except Exception:
        logger.exception("No se pudieron recuperar las transcripciones huérfanas.")


# --------------------------------------------------------------------------- #
# Despacho (tick del scheduler)                                                #
# --------------------------------------------------------------------------- #

# Claim atómico FIFO de la cola.
#
# OJO con el CTE: un UPDATE sobre un CTE solo puede escribir las columnas que el CTE
# EXPONE en su SELECT. Si se agrega una columna al SET hay que agregarla también arriba,
# o SQL Server responde "Invalid column name" en tiempo de ejecución (pasó con FechaEnvio
# el 2026-08-25: frenó el despacho de TODOS los motores hasta el fix). `SET PARSEONLY ON`
# no lo detecta —es binding, no sintaxis—; la validación que sí lo ve es
# sys.dm_exec_describe_first_result_set (ver tests/test_transcripcion_cola_sql.py).
_SQL_RECLAMAR_PENDIENTES = """
                WITH siguientes AS (
                    SELECT TOP (:limite) JobID, Estado, Intentos, IdAplicativo,
                           SolicitadoPor, FechaEnvio
                    FROM calidad.TranscripcionJobs
                    WHERE Estado = :pendiente AND Entorno = :env AND Motor = :motor
                    ORDER BY JobID
                )
                UPDATE siguientes
                SET Estado = :enviando, Intentos = Intentos + 1,
                    FechaEnvio = SYSUTCDATETIME()
                OUTPUT INSERTED.JobID, INSERTED.IdAplicativo,
                       INSERTED.SolicitadoPor, INSERTED.Intentos
"""


def _reclamar_pendientes(engine: Engine, limite: int, motor: str) -> List[Dict[str, Any]]:
    """Toma hasta `limite` pedidos de la cola y los marca ENVIANDO (claim atómico FIFO).

    El UPDATE sobre el CTE evita que dos ticks solapados tomen el mismo job y se pague
    la transcripción dos veces. Se sella `FechaEnvio` en el momento del claim (y no
    recién al crear el lote) para poder detectar los envíos colgados: en flex el job se
    queda en ENVIANDO todo lo que dure el llamado sincrónico, así que sin ese sello no
    habría forma de distinguir "corriendo" de "el proceso se murió".
    """
    try:
        with engine.begin() as conn:
            filas = conn.execute(text(_SQL_RECLAMAR_PENDIENTES), {
                "limite": int(limite), "pendiente": PENDIENTE, "enviando": ENVIANDO,
                "env": _entorno(), "motor": motor,
            }).fetchall()
    except Exception:
        logger.exception("No se pudo reclamar la cola de transcripciones.")
        return []

    return [
        {"job_id": f[0], "id_aplicativo": str(f[1]), "user_id": f[2], "intentos": f[3]}
        for f in filas
    ]


def _nivel_razonamiento() -> str:
    """Nivel con el que se transcribe (settings.TRANSCRIPCION_NIVEL_RAZONAMIENTO)."""
    return getattr(settings, "TRANSCRIPCION_NIVEL_RAZONAMIENTO", "") or \
        razonamiento.NIVEL_RAZONAMIENTO_TRANSCRIPCION


def _config_transcripcion(flex: bool = False) -> types.GenerateContentConfig:
    """Config del request: transcripción sola (sin auditoría de calidad).

    Con `flex=True` se pide el tier Flex (`service_tier`), que es sincrónico y cuesta la
    mitad que el estándar. El timeout va holgado a propósito: la doc de Flex avisa que
    el pedido puede quedar EN COLA del lado de Google y recomienda 10 minutos o más —con
    el timeout por defecto del SDK, un pedido que igual se va a facturar se perdería.

    Sin razonamiento largo a propósito: transcribir es dictado, no análisis. Cada token
    de pensamiento se factura a tarifa de SALIDA y acá no compra nada; además, si el
    pensamiento crece se come el max_output_tokens y trunca el JSON (el problema
    documentado en gemini.obtener_configuracion_gemini).

    Hasta el 2026-08-19 esto se pedía con `thinking_budget=1024`, que en la generación
    3.x de Gemini NO se respeta: el único job real que corrió con gemini-3.7-flash gastó
    20.071 tokens de pensamiento —19,6x el supuesto tope, y más caro que la transcripción
    misma— contra 4.100 de salida. Ahora se pide por `thinking_level` (ver
    AuditorIA/razonamiento.py), que es el parámetro que el modelo sí obedece.

    `include_thoughts=False` no ahorra nada por sí solo: solo evita que los pensamientos
    vuelvan en la respuesta. Lo que ahorra es el nivel bajo.
    """
    extra: Dict[str, Any] = {}
    if flex:
        extra["service_tier"] = types.ServiceTier.FLEX
        extra["http_options"] = types.HttpOptions(
            timeout=int(getattr(settings, "TRANSCRIPCION_FLEX_TIMEOUT_MS", 900_000)),
        )

    return types.GenerateContentConfig(
        temperature=0.2,
        max_output_tokens=65535,
        response_mime_type="application/json",
        response_schema=PROMPT_TRANSCRIPCION['response_schema'],
        system_instruction=[types.Part.from_text(text=PROMPT_TRANSCRIPCION['system'])],
        # Mismos cortes de loop que la auditoría: sin esto, un modelo repitiendo una
        # frase hasta MAX_TOKENS quema el lote entero (ver gemini.py).
        stop_sequences=["dice dice", "cliente cliente", "uhm uhm uhm"],
        thinking_config=razonamiento.thinking_config(
            _nivel_razonamiento(), include_thoughts=False,
            default=razonamiento.NIVEL_RAZONAMIENTO_TRANSCRIPCION,
        ),
        **extra,
    )


def _leer_audio(engine: Engine, id_aplicativo: str) -> Optional[Tuple[bytes, str]]:
    """(bytes, mime) del audio conservado de la interacción, o None si ya no está."""
    resuelto = audio_store.resolver_audio(engine, id_aplicativo)
    if not resuelto:
        return None
    ruta, mime, _nombre = resuelto
    try:
        with open(ruta, "rb") as f:
            datos = f.read()
    except OSError as e:
        logger.warning("No se pudo leer el audio conservado de %s: %s", id_aplicativo, e)
        return None
    return (datos, mime) if datos else None


def despachar_pendientes(engine: Engine, gemini_api, limite: Optional[int] = None,
                         motores: Optional[Sequence[str]] = None) -> int:
    """Manda a su motor los pedidos que están en la cola. Devuelve cuántos salieron.

    Se recorre motor por motor —cada uno tiene su forma de despachar— y el flex va
    PRIMERO: es el que tiene a alguien esperando en pantalla. `motores` acota a cuáles
    mirar (lo usa `despachar_flex_ahora`, que corre en la request y no debe ponerse a
    armar lotes de batch).

    Es el tick del scheduler: nunca propaga.
    """
    limite = int(limite or getattr(settings, "TRANSCRIPCION_MAX_POR_TICK", 200))
    # Se barren los motores que pueden tener jobs encolados, no solo el configurado:
    # un pedido flex degradado a batch, o un cambio de config, dejan filas del otro.
    if motores is None:
        motores = [MOTOR_GEMINI_FLEX, MOTOR_GEMINI_BATCH, motor_actual()]
    salidos = 0

    for motor in dict.fromkeys(motores):
        reclamados = _reclamar_pendientes(engine, limite, motor)
        if not reclamados:
            continue

        # Los que ya agotaron los intentos no se mandan más: se abandonan con su motivo.
        agotados = [j for j in reclamados if j["intentos"] > MAX_INTENTOS]
        if agotados:
            _cerrar(engine, [j["job_id"] for j in agotados], ERROR,
                    error=f"No se pudo enviar a transcribir después de {MAX_INTENTOS} intentos.")
        reclamados = [j for j in reclamados if j["intentos"] <= MAX_INTENTOS]
        if not reclamados:
            continue

        if motor == MOTOR_FASTWHISPER:
            salidos += _despachar_fastwhisper(engine, reclamados)
        elif motor == MOTOR_GEMINI_FLEX:
            salidos += _despachar_gemini_flex(engine, gemini_api, reclamados)
        else:
            salidos += _despachar_gemini_batch(engine, gemini_api, reclamados)

    return salidos


def despachar_flex_ahora(engine: Engine, gemini_api) -> int:
    """Despacha SOLO los pedidos flex, en el acto.

    Lo dispara el endpoint que encola (como background task, ver
    app/routers/auditoria.py): sin esto, un pedido hecho desde el reproductor esperaría
    hasta 5 minutos a que lo tome el tick del scheduler — más que lo que tarda la
    transcripción misma. El tick igual los sigue barriendo, así que si la API se cae en
    el medio el pedido no se pierde.
    """
    # Acotado a propósito: esto corre DENTRO del proceso de la API (background task) y
    # cada llamado flex puede tardar minutos. Sin tope, una request podría quedarse con
    # un hilo del pool horas, drenando toda la cola. Lo que exceda lo toma el scheduler,
    # que también despacha flex.
    tope = 2 * int(getattr(settings, "TRANSCRIPCION_FLEX_MAX_PEDIDO", 10))
    try:
        return despachar_pendientes(engine, gemini_api, limite=tope,
                                    motores=[MOTOR_GEMINI_FLEX])
    except Exception:
        logger.exception("Falló el despacho inmediato de transcripciones flex.")
        return 0


def _es_falta_de_capacidad(error: Exception) -> bool:
    """¿El error es "flex no tiene capacidad ahora" (429/503) y no un problema del pedido?"""
    codigo = getattr(error, "code", None)
    if isinstance(codigo, int) and codigo in CODIGOS_SIN_CAPACIDAD:
        return True
    texto = str(error).upper()
    return any(marca in texto for marca in
               ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "429", "503", "OVERLOADED"))


def _despachar_gemini_flex(engine: Engine, gemini_api, reclamados: List[Dict[str, Any]]) -> int:
    """Transcribe los pedidos EN EL MOMENTO contra el tier Flex (sincrónico, 50% off).

    Corre en el hilo que lo llama (background task de la API o tick del scheduler), con
    unos pocos llamados en paralelo: son pedidos chicos por definición y lo que se busca
    acá es latencia, no throughput.
    """
    if gemini_api is None:
        _devolver_a_pendiente(engine, [j["job_id"] for j in reclamados],
                              "El cliente de Gemini no está disponible.")
        return 0

    if not reclamados:
        return 0

    modelo = _modelo_actual()
    workers = max(1, int(getattr(settings, "TRANSCRIPCION_FLEX_WORKERS", 3)))
    listas = 0
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(reclamados)))) as executor:
        futuros = {
            executor.submit(_transcribir_flex, engine, gemini_api, job, modelo): job
            for job in reclamados
        }
        for futuro in as_completed(futuros):
            job = futuros[futuro]
            try:
                listas += int(bool(futuro.result()))
            except Exception:
                # _transcribir_flex ya atrapa lo suyo; esto es la red de última instancia
                # para que un job nunca quede colgado en ENVIANDO por una excepción rara.
                logger.exception("Falló la transcripción flex de %s.", job["id_aplicativo"])
                _devolver_a_pendiente(engine, [job["job_id"]], "Error inesperado en el envío flex.")
    if listas:
        logger.info("Transcripciones flex: %d listas en esta pasada.", listas)
    return listas


def _transcribir_flex(engine: Engine, gemini_api, job: Dict[str, Any], modelo: str) -> bool:
    """UN pedido de punta a punta contra Flex. Nunca propaga: deja el job cerrado
    (LISTO/ERROR), devuelto a la cola o degradado a batch."""
    id_aplicativo = job["id_aplicativo"]

    audio = _leer_audio(engine, id_aplicativo)
    if audio is None:
        _cerrar(engine, [job["job_id"]], ERROR,
                error="El audio de esta interacción ya no está conservado en el servidor.")
        return False
    datos_audio, mime = audio

    contents = [types.Content(role="user", parts=[
        types.Part.from_bytes(data=datos_audio, mime_type=mime),
        types.Part.from_text(text=PROMPT_TRANSCRIPCION['user']),
    ])]
    config = _config_transcripcion(flex=True)

    intentos = max(1, int(getattr(settings, "TRANSCRIPCION_FLEX_REINTENTOS", 3)))
    espera = int(getattr(settings, "TRANSCRIPCION_FLEX_BACKOFF_SEG", 15))
    ultimo_error: Optional[Exception] = None

    # El llamado a Gemini y el guardado van SEPARADOS a propósito: si falla el INSERT
    # (la base, no el modelo), reintentar acá volvería a pedirle la transcripción a
    # Gemini y a pagarla de nuevo. Un fallo de guardado devuelve el pedido a la cola.
    resultado_modelo: Optional[Tuple[Dict[str, Any], Dict[str, int]]] = None

    for intento in range(1, intentos + 1):
        try:
            respuesta = gemini_api.models.generate_content(
                model=modelo, contents=contents, config=config,
            )
            resultado_modelo = _parsear_respuesta(respuesta)
            break
        except Exception as e:  # noqa: BLE001 - se clasifica abajo
            ultimo_error = e
            sin_capacidad = _es_falta_de_capacidad(e)
            if intento < intentos:
                # Falta de capacidad: backoff exponencial, que es lo que pide la doc de
                # Flex. Otros errores (JSON truncado, corte de red): reintento corto,
                # igual que hace la auditoría sincrónica.
                time.sleep(espera if sin_capacidad else 3)
                if sin_capacidad:
                    espera *= 3
                continue

            if sin_capacidad:
                _degradar_a_batch(
                    engine, [job["job_id"]],
                    "Flex no tuvo capacidad; la transcripción se resuelve por lote "
                    "(mismo precio, tarda más).")
            else:
                logger.error("Transcripción flex de %s fallida: %s", id_aplicativo, e)
                _cerrar(engine, [job["job_id"]], ERROR, error=str(e), modelo=modelo)
            return False

    if resultado_modelo is None:
        # Inalcanzable (el for cierra el job en el último intento), pero si algún día se
        # toca el flujo el job no puede quedar en ENVIANDO para siempre.
        _devolver_a_pendiente(engine, [job["job_id"]], str(ultimo_error or "Sin resultado."))
        return False

    datos, tokens = resultado_modelo
    try:
        guardar_transcripcion(engine, id_aplicativo, datos, tokens,
                              job["user_id"], modelo, modo="flex")
    except Exception as e:
        # La transcripción ya está paga; lo que falló es guardarla. Vuelve a la cola (con
        # su intento consumido) en vez de cerrarse en ERROR: el próximo tick la reintenta.
        logger.exception("No se pudo guardar la transcripción flex de %s.", id_aplicativo)
        _devolver_a_pendiente(engine, [job["job_id"]], f"No se pudo guardar: {e}")
        return False

    _cerrar(engine, [job["job_id"]], LISTO, modelo=modelo)
    return True


def _despachar_gemini_batch(engine: Engine, gemini_api, reclamados: List[Dict[str, Any]]) -> int:
    """Arma uno o más jobs de batch de Gemini (audio inline) con los pedidos reclamados."""
    if gemini_api is None:
        _devolver_a_pendiente(engine, [j["job_id"] for j in reclamados],
                              "El cliente de Gemini no está disponible.")
        return 0

    modelo = _modelo_actual()
    config = _config_transcripcion()

    # 1) Leer los audios y cortar en lotes por tamaño (el request no puede pasar de 20 MB).
    lotes: List[List[Dict[str, Any]]] = []
    lote_actual: List[Dict[str, Any]] = []
    bytes_lote = 0
    for job in reclamados:
        audio = _leer_audio(engine, job["id_aplicativo"])
        if audio is None:
            _cerrar(engine, [job["job_id"]], ERROR,
                    error="El audio de esta interacción ya no está conservado en el servidor.")
            continue
        datos, mime = audio
        if bytes_lote + len(datos) > MAX_BYTES_POR_LOTE and lote_actual:
            lotes.append(lote_actual)
            lote_actual, bytes_lote = [], 0
        lote_actual.append({**job, "datos": datos, "mime": mime})
        bytes_lote += len(datos)
    if lote_actual:
        lotes.append(lote_actual)

    if not lotes:
        return 0

    # 2) Un job de Gemini por lote. El SegmentID de cada pedido es su posición DENTRO
    #    del lote: así se ata cada respuesta a su interacción cuando vuelve, horas después.
    enviados = 0
    for lote in lotes:
        requests = [{
            "contents": [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_bytes(data=job["datos"], mime_type=job["mime"]),
                        types.Part.from_text(text=PROMPT_TRANSCRIPCION['user']),
                    ],
                ),
            ],
            "config": config,
        } for job in lote]

        try:
            batch_job = gemini_api.batches.create(
                model=modelo,
                src=requests,
                config={'display_name': f"transcripcion-cola-{int(time.time())}"},
            )
        except Exception as e:
            logger.error("No se pudo crear el lote de transcripción en Gemini: %s", e)
            _devolver_a_pendiente(engine, [j["job_id"] for j in lote], f"Fallo al enviar: {e}")
            continue

        try:
            with engine.begin() as conn:
                for segmento, job in enumerate(lote):
                    conn.execute(text("""
                        UPDATE calidad.TranscripcionJobs
                        SET Estado = :estado, BatchID = :batch, SegmentID = :segmento,
                            Modelo = :modelo, FechaEnvio = SYSUTCDATETIME(), Error = NULL
                        WHERE JobID = :jid
                    """), {
                        "estado": ENVIADO, "batch": batch_job.name, "segmento": segmento,
                        "modelo": modelo, "jid": job["job_id"],
                    })
            enviados += len(lote)
            logger.info(
                "Transcripciones: lote %s enviado a Gemini con %d llamados (modelo %s).",
                batch_job.name, len(lote), modelo,
            )
        except Exception:
            # El lote ya está en Gemini pero no quedó anotado: sin BatchID nadie va a ir a
            # buscar su resultado. Los pedidos vuelven a la cola (se re-mandarán) y este
            # lote queda huérfano; se avisa fuerte porque igual se va a facturar.
            logger.exception(
                "El lote de transcripción %s se creó en Gemini pero no se pudo registrar; "
                "sus pedidos vuelven a la cola.", getattr(batch_job, "name", "?"),
            )
            _devolver_a_pendiente(engine, [j["job_id"] for j in lote],
                                  "No se pudo registrar el lote enviado a Gemini.")

    return enviados


def _despachar_fastwhisper(engine: Engine, reclamados: List[Dict[str, Any]]) -> int:
    """PENDIENTE (a futuro): transcripción local con faster-whisper sobre GPU.

    Cuando el servidor tenga GPU, acá va el camino SINCRÓNICO: por cada pedido, cargar
    el audio de audio_store, correr el modelo local, armar el mismo dict
    {metadata, segments, analytics} que devuelve Gemini y llamar a
    `guardar_transcripcion(...)` con tokens en 0 (no hay costo por token) y
    modelo='faster-whisper-<tamaño>'. El job pasa de ENVIANDO a LISTO en el mismo tick,
    sin BatchID ni polling, y la pantalla no cambia en nada.

    Hasta entonces, si alguien pone TRANSCRIPCION_MOTOR=fastwhisper los pedidos vuelven
    a la cola en vez de perderse.
    """
    logger.error(
        "TRANSCRIPCION_MOTOR=%s todavía no está implementado (falta la GPU): "
        "%d pedidos vuelven a la cola.", MOTOR_FASTWHISPER, len(reclamados),
    )
    _devolver_a_pendiente(engine, [j["job_id"] for j in reclamados],
                          "El motor local de transcripción todavía no está disponible.")
    return 0


# --------------------------------------------------------------------------- #
# Vuelta del lote (tick del scheduler)                                         #
# --------------------------------------------------------------------------- #

ESTADOS_TERMINALES_GEMINI = (
    'JOB_STATE_SUCCEEDED', 'JOB_STATE_FAILED', 'JOB_STATE_CANCELLED', 'JOB_STATE_EXPIRED',
)

# Un batch de Gemini apunta a 24 h. Pasado este plazo el lote no va a volver (o Gemini ya
# ni lo conoce y `batches.get` tira error en cada pasada): el pedido se abandona en vez de
# quedar reintentándose para siempre y bloqueando —por el índice único de job abierto— que
# el usuario lo vuelva a encolar.
DIAS_ESPERA_LOTE = 3


def _cerrar_vencidos(engine: Engine) -> None:
    """Abandona los pedidos cuyo lote nunca devolvió resultado."""
    try:
        with engine.begin() as conn:
            resultado = conn.execute(text("""
                UPDATE calidad.TranscripcionJobs
                SET Estado = :error, FechaFin = SYSUTCDATETIME(),
                    Error = :motivo
                WHERE Estado = :enviado AND Entorno = :env
                  AND FechaEnvio < DATEADD(day, :dias, SYSUTCDATETIME())
            """), {
                "error": ERROR, "enviado": ENVIADO, "env": _entorno(),
                "dias": -DIAS_ESPERA_LOTE,
                "motivo": (f"El lote de Gemini no devolvió resultado en {DIAS_ESPERA_LOTE} días; "
                           f"se abandona. Se puede volver a encolar."),
            })
        if resultado.rowcount:
            logger.warning("%d transcripciones vencidas (lote sin resultado) abandonadas.",
                           resultado.rowcount)
    except Exception:
        logger.exception("No se pudieron cerrar las transcripciones vencidas.")


def _jobs_en_vuelo(engine: Engine) -> Dict[str, List[Dict[str, Any]]]:
    """Pedidos ENVIADOS agrupados por el lote de Gemini que los tiene."""
    por_batch: Dict[str, List[Dict[str, Any]]] = {}
    try:
        with engine.connect() as conn:
            filas = conn.execute(text("""
                SELECT JobID, BatchID, SegmentID, IdAplicativo, SolicitadoPor, Modelo
                FROM calidad.TranscripcionJobs
                WHERE Estado = :enviado AND Entorno = :env AND BatchID IS NOT NULL
                ORDER BY BatchID, SegmentID
            """), {"enviado": ENVIADO, "env": _entorno()}).fetchall()
    except Exception:
        logger.exception("No se pudieron leer las transcripciones en vuelo.")
        return por_batch

    for job_id, batch_id, segmento, id_aplicativo, user_id, modelo in filas:
        por_batch.setdefault(str(batch_id), []).append({
            "job_id": job_id, "segment_id": segmento, "id_aplicativo": str(id_aplicativo),
            "user_id": user_id, "modelo": modelo,
        })
    return por_batch


def _parsear_respuesta(respuesta) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """(datos de la transcripción, tokens) de una respuesta de Gemini.

    El ensamblado del texto se reusa de gemini.py (una respuesta larga viene partida en
    varias `parts` y leer una sola rompe el JSON). Import diferido: gemini.py arrastra el
    manager de plantillas y este módulo tiene que poder usarse sin eso.
    """
    from AuditorIA.gemini import _extraer_texto_respuesta

    _thoughts, texto = _extraer_texto_respuesta(respuesta)
    # strict=False: Gemini a veces mete \n/\t crudos dentro de los strings (ver
    # gemini.procesar_respuesta).
    datos = json.loads(texto, strict=False)

    uso = getattr(respuesta, "usage_metadata", None)
    tokens = {
        "input_tokens": getattr(uso, "prompt_token_count", None) or 0,
        "output_tokens": getattr(uso, "candidates_token_count", None) or 0,
        "thoughts_tokens": getattr(uso, "thoughts_token_count", None) or 0,
    }
    return datos, tokens


def guardar_transcripcion(engine: Engine, id_aplicativo: str, datos: Dict[str, Any],
                          tokens: Dict[str, int], user_id: Optional[int],
                          modelo: str, modo: str = "batch") -> bool:
    """Guarda una transcripción suelta en calidad.transcripciones y registra su consumo.

    Es el equivalente de Trancribir.guardar_transcripciones_sql para el camino de la
    cola: acá no hay DataFrame de la interacción del que deducir la clave —el pedido
    YA nació con su IdAplicativo—, así que la fila se arma directo.

    Se vuelve a deduplicar contra la tabla: entre que se encoló y volvió el lote pueden
    haber pasado horas, y en el medio una re-auditoría pudo transcribir el mismo llamado.
    """
    metadata = datos.get("metadata") if isinstance(datos, dict) else None
    segments = datos.get("segments") if isinstance(datos, dict) else None
    analytics = datos.get("analytics") if isinstance(datos, dict) else None
    if not segments:
        raise ValueError("La respuesta no trajo segmentos de transcripción.")

    # Los tiempos que devuelve el modelo son estimados: se corrigen contra la duración
    # real del audio, que el store ya midió con ffprobe al conservarlo.
    dur_real = audio_store.duracion_de(engine, id_aplicativo)
    if dur_real:
        metadata, segments = _ajustar_tiempos_segments(metadata, segments, dur_real)

    if str(id_aplicativo) in ids_con_transcripcion(engine, [id_aplicativo]):
        logger.info("La transcripción de %s ya existía; no se duplica.", id_aplicativo)
        return False

    insertar = text("""
        INSERT INTO calidad.transcripciones
            (IdAplicativo, metadata, segments, analytics,
             input_tokens, output_tokens, thoughts_tokens, user_id)
        VALUES (:id, :metadata, :segments, :analytics, :input, :output, :thoughts, :user_id)
    """).bindparams(
        # NVARCHAR explícito: la transcripción lleva acentos/ñ y en VARCHAR se pierden
        # en el bind (mismo problema que tuvo el log del chatbot).
        bindparam("metadata", type_=NVARCHAR),
        bindparam("segments", type_=NVARCHAR),
        bindparam("analytics", type_=NVARCHAR),
    )
    with engine.begin() as conn:
        conn.execute(insertar, {
            "id": str(id_aplicativo),
            "metadata": json.dumps(metadata, ensure_ascii=False) if metadata is not None else None,
            "segments": json.dumps(segments, ensure_ascii=False),
            "analytics": json.dumps(analytics, ensure_ascii=False) if analytics is not None else None,
            "input": tokens.get("input_tokens") or 0,
            "output": tokens.get("output_tokens") or 0,
            "thoughts": tokens.get("thoughts_tokens") or 0,
            "user_id": user_id,
        })

    # Libro de consumo de IA (pagina_web.IA_Uso). Best-effort: la transcripción ya está
    # guardada, no se pierde por no poder anotar el costo.
    try:
        from app.uso_ia import registrar_uso_ia_bulk
        registrar_uso_ia_bulk([{
            "feature": "transcripcion",
            "modelo": modelo,
            "modo": modo,
            "user_id": user_id,
            "input_tokens": tokens.get("input_tokens"),
            "output_tokens": tokens.get("output_tokens"),
            "thoughts_tokens": tokens.get("thoughts_tokens"),
            "ref_id": f"transcripcion:{id_aplicativo}",
        }])
    except Exception:
        logger.exception("No se pudo registrar el consumo de la transcripción %s.", id_aplicativo)
    return True


def procesar_terminados(engine: Engine, gemini_api) -> int:
    """Guarda las transcripciones de los lotes de Gemini que ya terminaron.

    Devuelve cuántas quedaron guardadas. Cada lote y cada respuesta van en su propio
    try: un lote roto no puede llevarse puestos a los demás.
    """
    if gemini_api is None:
        return 0
    _cerrar_vencidos(engine)
    en_vuelo = _jobs_en_vuelo(engine)
    if not en_vuelo:
        return 0

    guardadas = 0
    for batch_id, jobs in en_vuelo.items():
        try:
            batch_job = gemini_api.batches.get(name=batch_id)
        except Exception as e:
            logger.error("No se pudo consultar el lote de transcripción %s: %s", batch_id, e)
            continue

        estado_gemini = getattr(getattr(batch_job, "state", None), "name", None)
        if estado_gemini not in ESTADOS_TERMINALES_GEMINI:
            continue

        if estado_gemini != 'JOB_STATE_SUCCEEDED':
            logger.warning("El lote de transcripción %s terminó en %s.", batch_id, estado_gemini)
            _cerrar(engine, [j["job_id"] for j in jobs], ERROR,
                    error=f"El lote de Gemini terminó en estado {estado_gemini}.")
            continue

        destino = getattr(batch_job, "dest", None)
        respuestas = list(getattr(destino, "inlined_responses", None) or [])
        if not respuestas:
            logger.warning("El lote de transcripción %s terminó sin respuestas.", batch_id)
            _cerrar(engine, [j["job_id"] for j in jobs], ERROR,
                    error="El lote terminó sin respuestas.")
            continue

        for job in jobs:
            segmento = job["segment_id"]
            try:
                if segmento is None or segmento >= len(respuestas):
                    raise ValueError(f"El lote no trajo la respuesta {segmento}.")
                item = respuestas[segmento]
                respuesta = getattr(item, "response", None)
                if respuesta is None:
                    raise ValueError(f"Gemini devolvió un error para este audio: "
                                     f"{getattr(item, 'error', 'sin detalle')}")

                datos, tokens = _parsear_respuesta(respuesta)
                guardo = guardar_transcripcion(
                    engine, job["id_aplicativo"], datos, tokens,
                    job["user_id"], job["modelo"] or _modelo_actual(),
                )
                _cerrar(engine, [job["job_id"]], LISTO)
                guardadas += int(guardo)
            except Exception as e:
                logger.error("No se pudo guardar la transcripción de %s (lote %s): %s",
                             job["id_aplicativo"], batch_id, e)
                _cerrar(engine, [job["job_id"]], ERROR, error=str(e))

    if guardadas:
        logger.info("Transcripciones encoladas: %d guardadas en esta pasada.", guardadas)
    return guardadas


def procesar_cola(engine: Engine, gemini_api) -> None:
    """Tick único del scheduler: primero recoge lo que ya terminó, después manda lo nuevo.

    En ese orden a propósito: recoger libera pedidos que quizá se encolaron de nuevo, y
    manda recién después de haber guardado lo que ya estaba pago.
    """
    try:
        recuperar_envios_colgados(engine)
    except Exception:
        logger.exception("Falló la recuperación de envíos colgados.")
    try:
        procesar_terminados(engine, gemini_api)
    except Exception:
        logger.exception("Falló la recolección de transcripciones terminadas.")
    try:
        despachar_pendientes(engine, gemini_api)
    except Exception:
        logger.exception("Falló el despacho de transcripciones pendientes.")
