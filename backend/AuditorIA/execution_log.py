# --- START OF FILE execution_log.py ---
"""Registro unificado de ejecuciones de auditoría (calidad.AuditExecutionLog).

Una fila = una corrida completa en modo sync (abre y cierra en el mismo hilo),
o un job de Gemini en modo batch (abre en process_batch, cierra horas después
en procesar_batch, keyed por batch_id). Es la fuente única para analizar
corridas por SQL/Excel y para enriquecer el mail final con atributos que antes
no viajaban (modo, origen, rango/cantidad, duración, errores, tokens).

Nunca debe interrumpir una auditoría real: toda función acá atrapa sus propias
excepciones, loguea y devuelve None/no-op en vez de propagar.
"""
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional, Sequence, Tuple

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from AuditorIA import razonamiento

logger = logging.getLogger(__name__)

# Alias de las columnas de operador y de tipificación tal como vienen de cada
# descarga (cada cliente la nombra distinto). Son la MISMA lista que usa
# Auditor._estandarizar_columnas_sql para armar `operador_usuario` /
# `tipificacion_interaccion`: viven acá para que el desglose del muestreo se
# pueda calcular también sobre un df todavía sin estandarizar (modo batch, ver
# gemini.process_batch) sin que las dos listas se desincronicen.
ALIAS_OPERADOR: Tuple[str, ...] = (
    'operador_usuario', 'operadorusuario', 'loginid', 'operador', 'empleado',
    'id_agente', 'agentname_sort', 'rec_agentid', 'agente_usuario', 'agente',
)
ALIAS_TIPIFICACION: Tuple[str, ...] = (
    'tipificación', 'tipificacion',
    # Vantix (Orion): la Subcategoría es la tipificación real del llamado y tiene
    # que ganarle a la Categoría (apenas su bucket genérico).
    'subtipificacion/subcategoria',
    'tipificacion/categoria', 'tipificación/categoria',
)


# Columnas que agrega la migración 2026-09-01b. Se consultan UNA vez por proceso: son
# `ALTER TABLE` que no se van a deshacer, y preguntar por cada corrida es ir a
# INFORMATION_SCHEMA para nada.
_COLUMNAS_MIGRACION_CACHE: Dict[str, bool] = {}


def _tiene_columna(engine: Engine, esquema: str, tabla: str, columna: str) -> bool:
    """¿Existe la columna? Cachea el resultado por proceso.

    Si la migración 2026-09-01b todavía no se aplicó, el costeo y el log siguen
    andando sin los tokens cacheados (que es exactamente lo que hacían antes).
    """
    clave = f"{esquema}.{tabla}.{columna}"
    if clave in _COLUMNAS_MIGRACION_CACHE:
        return _COLUMNAS_MIGRACION_CACHE[clave]
    try:
        with engine.connect() as conn:
            existe = conn.execute(text("""
                SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = :esquema AND TABLE_NAME = :tabla
                  AND COLUMN_NAME = :columna
            """), {"esquema": esquema, "tabla": tabla, "columna": columna}).first() is not None
    except Exception as e:
        logger.warning("No se pudo verificar la columna %s: %s", clave, e)
        existe = False
    _COLUMNAS_MIGRACION_CACHE[clave] = existe
    return existe


def tiene_columna_cached_tokens(engine: Engine) -> bool:
    """¿Está aplicada la migración 2026-09-01b sobre calidad.AuditExecutionLog?"""
    return _tiene_columna(engine, 'calidad', 'AuditExecutionLog', 'cached_tokens')


def select_cached_tokens(engine: Engine, *, agregado: bool = False) -> str:
    """Expresión para leer `cached_tokens` del log en un SELECT.

    Todo lo que costea desde calidad.AuditExecutionLog (el mail por corrida, el
    listado de /uso-ia, los cupos por campaña) necesita el dato, y todo eso tiene
    que seguir andando si la migración 2026-09-01b no está aplicada: ahí devuelve
    0, que es lo mismo que decir "no se cacheó nada" y costea como antes.
    """
    if not tiene_columna_cached_tokens(engine):
        return "0 AS cached_tokens"
    return "SUM(COALESCE(cached_tokens, 0)) AS cached_tokens" if agregado else "cached_tokens"


def _tiene_precio_cacheado(engine: Engine) -> bool:
    """¿Está aplicada la migración 2026-09-01b sobre pagina_web.IA_Precios?"""
    return _tiene_columna(engine, 'pagina_web', 'IA_Precios', 'cached_usd_mtok')


def _texto(valor: Any) -> Optional[str]:
    return None if valor is None else str(valor)


def _desglose_a_json(desglose: Optional[Dict[str, Any]]) -> Optional[str]:
    """Serializa el desglose para la columna NVARCHAR. `default=str` es red de
    seguridad: si algún llamador arma el dict a mano con tipos de numpy, se
    guarda igual en vez de reventar el registro de la corrida."""
    if not desglose:
        return None
    try:
        return json.dumps(desglose, ensure_ascii=False, default=str)
    except Exception as e:
        logger.warning(f"No se pudo serializar el desglose del muestreo: {e}")
        return None


def _serie_de_grupo(
    df: pd.DataFrame, columna_estandar: str, aliases: Sequence[str]
) -> Optional[pd.Series]:
    """Devuelve la columna con la que agrupar: la estandarizada si ya existe y
    tiene datos (path sync, después de _estandarizar_columnas_sql) o, si no, el
    primer alias crudo presente en el df (path batch, df recién descargado)."""
    if columna_estandar in df.columns and df[columna_estandar].notna().any():
        return df[columna_estandar]
    cols_lower_map = {str(col).lower(): col for col in df.columns}
    for alias in aliases:
        if alias in cols_lower_map:
            return df[cols_lower_map[alias]]
    return None


def _conteo_por_grupo(serie: pd.Series) -> Dict[str, int]:
    """{grupo: cantidad} con claves/valores nativos: el desglose se serializa a
    JSON y los tipos de numpy (int64 como clave o valor) rompen json.dumps."""
    return {str(clave): int(cant) for clave, cant in serie.value_counts().items()}


def calcular_desglose_muestreo(
    df: Optional[pd.DataFrame], *, por_operador: bool = False, por_tipificacion: bool = False
) -> Optional[Dict[str, Dict[str, int]]]:
    """Cuántas interacciones le tocaron a cada operador/tipificación en esta
    corrida (columna desglose_muestreo del log, tooltip en la pantalla de logs).

    Solo tiene sentido con muestreo por grupo activo: ahí `cantidad` deja de ser
    el total y pasa a ser "por cada grupo" (ver SQL_query.construir_query_muestreo),
    así que el reparto real recién se conoce con el df ya descargado. Devuelve
    None si no hay muestreo por grupo, si el df está vacío o si no se encontró
    ninguna columna con la que agrupar."""
    if df is None or df.empty or not (por_operador or por_tipificacion):
        return None
    desglose: Dict[str, Dict[str, int]] = {}
    if por_operador:
        serie = _serie_de_grupo(df, 'operador_usuario', ALIAS_OPERADOR)
        if serie is not None:
            desglose['operadores'] = _conteo_por_grupo(serie)
    if por_tipificacion:
        serie = _serie_de_grupo(df, 'tipificacion_interaccion', ALIAS_TIPIFICACION)
        if serie is not None:
            desglose['tipificaciones'] = _conteo_por_grupo(serie)
    return desglose or None


def iniciar_ejecucion(
    engine: Engine,
    *,
    trigger_source: str,
    modo: str,
    scheduler_id: Optional[int] = None,
    scheduler_name: Optional[str] = None,
    task_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    empresa: Optional[Any] = None,
    campana: Optional[Any] = None,
    plantilla_id: Optional[Any] = None,
    user_id: Optional[Any] = None,
    fecha_desde: Optional[Any] = None,
    fecha_hasta: Optional[Any] = None,
    cantidad_solicitada: Optional[int] = None,
    por_operador: bool = False,
    por_tipificacion: bool = False,
    upload_group_id: Optional[str] = None,
    modelo: Optional[str] = None,
    nivel_razonamiento: Optional[str] = None,
    desglose_muestreo: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
    mail_destinatarios: Optional[str] = None,
) -> Optional[int]:
    """Inserta una fila EN_CURSO. Devuelve su id (None si falla).

    `run_id`: identificador de la CORRIDA, compartido por todas las filas que
    pertenecen a la misma ejecución. Solo el modo batch lo necesita explícito
    (una corrida se parte en varios lotes de Gemini, cada uno con su fila, ver
    gemini.py::calidad_batch); en sync una fila YA es una corrida y se deja en
    None: el NEWID() del INSERT le da el suyo y el agrupado de /uso-ia/logs se
    comporta igual que antes.

    `mail_destinatarios`: a quién va dirigido el resultado (los `email_addresses`
    de la tarea programada). Se guarda al ABRIR y no al cerrar porque el mail de la
    corrida lo manda el último lote que termina, que puede ser uno abandonado —sin
    DataFrame ni Batch_data— y necesita leer de acá a quién escribirle. Que se haya
    mandado o no lo dice `mail_enviado`.

    `modelo`: modelo de Gemini REAL de la corrida. En batch ya se conoce al abrir
    (process_batch lo resolvió para crear el job); en sync se conoce recién al
    cerrar, así que acá queda NULL y lo completa finalizar_ejecucion.

    `nivel_razonamiento`: cuánto pensó el modelo (LOW/MEDIUM/HIGH, ver
    AuditorIA/razonamiento.py). Mismo criterio que `modelo`: en batch se conoce al
    abrir, en sync al cerrar. Se guarda el nivel REAL de la corrida y no se lee
    después de la plantilla porque la plantilla puede cambiar de nivel más tarde y
    ahí la comparación de costo por nivel en /uso-ia dejaría de significar nada.

    `desglose_muestreo`: ídem, en batch el reparto por operador/tipificación se
    conoce al abrir (el df ya está descargado y repartido en lotes); en sync lo
    completa finalizar_ejecucion, cuando termina la auditoría."""
    try:
        with engine.begin() as conn:
            result = conn.execute(text("""
                INSERT INTO calidad.AuditExecutionLog
                    (trigger_source, scheduler_id, scheduler_name, task_id, batch_id,
                     modo, empresa, campana, plantilla_id, user_id,
                     fecha_desde, fecha_hasta, cantidad_solicitada,
                     por_operador, por_tipificacion, upload_group_id, modelo,
                     nivel_razonamiento, desglose_muestreo, run_id,
                     mail_destinatarios, status)
                OUTPUT inserted.id
                VALUES
                    (:trigger_source, :scheduler_id, :scheduler_name, :task_id, :batch_id,
                     :modo, :empresa, :campana, :plantilla_id, :user_id,
                     :fecha_desde, :fecha_hasta, :cantidad_solicitada,
                     :por_operador, :por_tipificacion, :upload_group_id, :modelo,
                     :nivel_razonamiento, :desglose_muestreo, COALESCE(:run_id, NEWID()),
                     :mail_destinatarios, 'EN_CURSO')
            """), {
                "trigger_source": trigger_source,
                "scheduler_id": scheduler_id,
                "scheduler_name": scheduler_name,
                "task_id": task_id,
                "batch_id": batch_id,
                "modo": modo,
                "empresa": _texto(empresa),
                "campana": _texto(campana),
                "plantilla_id": plantilla_id,
                "user_id": _texto(user_id),
                "fecha_desde": fecha_desde,
                "fecha_hasta": fecha_hasta,
                "cantidad_solicitada": cantidad_solicitada,
                "por_operador": por_operador,
                "por_tipificacion": por_tipificacion,
                "upload_group_id": upload_group_id,
                "modelo": modelo,
                "nivel_razonamiento": nivel_razonamiento,
                "desglose_muestreo": _desglose_a_json(desglose_muestreo),
                "run_id": run_id,
                "mail_destinatarios": mail_destinatarios,
            })
            return result.scalar()
    except Exception as e:
        logger.error(f"No se pudo registrar el inicio de ejecución en AuditExecutionLog: {e}")
        return None


def finalizar_ejecucion(
    engine: Engine,
    *,
    log_id: Optional[int] = None,
    batch_id: Optional[str] = None,
    status: str,
    filas_auditadas: Optional[int] = None,
    filas_error: Optional[int] = None,
    tokens: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    mail_enviado: bool = False,
    mail_destinatarios: Optional[str] = None,
    gsheet_enviado: bool = False,
    cantidad_solicitada_final: Optional[int] = None,
    desglose_muestreo: Optional[Dict[str, Any]] = None,
    modelo: Optional[str] = None,
    nivel_razonamiento: Optional[str] = None,
    id_aplicativos: Optional[str] = None,
) -> None:
    """Cierra una fila EN_CURSO, por id (sync, mismo hilo) o por batch_id (batch,
    se cierra en otro ciclo horas después). No-op silencioso sin clave.

    `cantidad_solicitada_final`/`desglose_muestreo` son para el muestreo por
    operador/tipificación (ver Auditor.py::run): `cantidad` deja de ser el total
    y pasa a ser "por cada grupo", así que el total real solo se sabe después de
    descargar. Si no se pasan, se conserva lo que ya tenía la fila."""
    if log_id is None and not batch_id:
        return
    tokens = tokens or {}
    desglose_json = _desglose_a_json(desglose_muestreo)
    where_clause = "id = :log_id" if log_id is not None else "batch_id = :batch_id"
    # La columna la agrega la migración 2026-09-01b. Sin ella la corrida se cierra
    # igual, solo que sin el dato de cuántos tokens se cobraron cacheados.
    columna_cached = (
        "\n                    cached_tokens = :cached_tokens,"
        if tiene_columna_cached_tokens(engine) else ""
    )
    try:
        with engine.begin() as conn:
            conn.execute(text(f"""
                UPDATE calidad.AuditExecutionLog
                SET status = :status,
                    filas_auditadas = :filas_auditadas,
                    filas_error = :filas_error,
                    input_tokens = :input_tokens,
                    output_tokens = :output_tokens,
                    thoughts_tokens = :thoughts_tokens,{columna_cached}
                    error_message = :error_message,
                    mail_enviado = :mail_enviado,
                    -- COALESCE y no asignación directa: los destinatarios se guardan
                    -- al ABRIR la fila (ver iniciar_ejecucion) y cerrar el lote no
                    -- puede borrarlos, que es de donde los lee el cierre de corrida.
                    mail_destinatarios = COALESCE(:mail_destinatarios, mail_destinatarios),
                    gsheet_enviado = :gsheet_enviado,
                    cantidad_solicitada = COALESCE(:cantidad_solicitada_final, cantidad_solicitada),
                    desglose_muestreo = COALESCE(:desglose_muestreo, desglose_muestreo),
                    modelo = COALESCE(:modelo, modelo),
                    nivel_razonamiento = COALESCE(:nivel_razonamiento, nivel_razonamiento),
                    id_aplicativos = COALESCE(:id_aplicativos, id_aplicativos),
                    finished_at = SYSUTCDATETIME()
                WHERE {where_clause} AND status = 'EN_CURSO'
            """), {
                "log_id": log_id,
                "batch_id": batch_id,
                "status": status,
                "filas_auditadas": filas_auditadas,
                "filas_error": filas_error,
                "input_tokens": tokens.get("input_tokens"),
                "output_tokens": tokens.get("output_tokens"),
                "thoughts_tokens": tokens.get("thoughts_tokens"),
                # Solo se bindea si la columna existe; ver `columna_cached` arriba.
                **({"cached_tokens": tokens.get("cached_tokens")} if columna_cached else {}),
                "error_message": error_message,
                "mail_enviado": mail_enviado,
                "mail_destinatarios": mail_destinatarios,
                "gsheet_enviado": gsheet_enviado,
                "cantidad_solicitada_final": cantidad_solicitada_final,
                "desglose_muestreo": desglose_json,
                "modelo": modelo,
                "nivel_razonamiento": nivel_razonamiento,
                "id_aplicativos": id_aplicativos,
            })
    except Exception as e:
        clave = f"id={log_id}" if log_id is not None else f"batch_id={batch_id}"
        logger.error(f"No se pudo cerrar la ejecución en AuditExecutionLog ({clave}): {e}")


def continuar_o_iniciar_grupo(
    engine: Engine,
    *,
    upload_group_id: str,
    cantidad_solicitada: Optional[int],
    **campos_iniciales: Any,
) -> Optional[int]:
    """CSV especial: el frontend sube los audios en tandas de 10 por request
    (ver handleCSVUpload en auditoria.js), cada una un POST /Auditar/ propio. El
    endpoint solo encola la auditoría (background_tasks) y responde enseguida
    (202) SIN esperar a que termine — el frontend manda la tanda siguiente apenas
    llega esa respuesta, no cuando la auditoría de la tanda anterior termina. Por
    eso dos tandas de la misma subida pueden ejecutarse casi en simultáneo, y sin
    lock ambas verían "no existe fila todavía" y crearían cada una la suya
    (exactamente lo que se quiere evitar). `sp_getapplock` (SQL Server) serializa
    el chequeo+creación/actualización entre procesos/hilos usando el propio
    upload_group_id como clave.

    Si ya existe una fila con este upload_group_id (tanda anterior de la misma
    subida), le suma la cantidad de esta tanda y la reabre; si no existe (primera
    tanda), la crea. `campos_iniciales` son los mismos kwargs de iniciar_ejecucion
    (trigger_source, modo, empresa, campana, etc.), usados solo al crear la fila."""
    try:
        with engine.begin() as conn:
            # LockOwner='Transaction': se libera solo al terminar esta transacción
            # (commit/rollback del `with engine.begin()`), nunca queda colgado.
            conn.execute(text("""
                EXEC sp_getapplock @Resource = :res, @LockMode = 'Exclusive',
                                    @LockOwner = 'Transaction', @LockTimeout = 30000
            """), {"res": f"audit_upload_group:{upload_group_id}"})

            row = conn.execute(text("""
                SELECT id FROM calidad.AuditExecutionLog WHERE upload_group_id = :gid
            """), {"gid": upload_group_id}).fetchone()
            if row:
                log_id = row[0]
                conn.execute(text("""
                    UPDATE calidad.AuditExecutionLog
                    SET status = 'EN_CURSO',
                        cantidad_solicitada = COALESCE(cantidad_solicitada, 0) + COALESCE(:cant, 0)
                    WHERE id = :id
                """), {"cant": cantidad_solicitada, "id": log_id})
                return log_id

            # Primera tanda del grupo: inserta acá mismo (dentro del lock), no vía
            # iniciar_ejecucion (abriría su propia transacción, fuera del lock).
            campos_iniciales.setdefault("por_operador", False)
            campos_iniciales.setdefault("por_tipificacion", False)
            result = conn.execute(text("""
                INSERT INTO calidad.AuditExecutionLog
                    (trigger_source, scheduler_id, scheduler_name, task_id, batch_id,
                     modo, empresa, campana, plantilla_id, user_id,
                     fecha_desde, fecha_hasta, cantidad_solicitada,
                     por_operador, por_tipificacion, upload_group_id, status)
                OUTPUT inserted.id
                VALUES
                    (:trigger_source, :scheduler_id, :scheduler_name, :task_id, :batch_id,
                     :modo, :empresa, :campana, :plantilla_id, :user_id,
                     :fecha_desde, :fecha_hasta, :cantidad_solicitada,
                     :por_operador, :por_tipificacion, :upload_group_id, 'EN_CURSO')
            """), {
                "trigger_source": campos_iniciales.get("trigger_source"),
                "scheduler_id": campos_iniciales.get("scheduler_id"),
                "scheduler_name": campos_iniciales.get("scheduler_name"),
                "task_id": campos_iniciales.get("task_id"),
                "batch_id": campos_iniciales.get("batch_id"),
                "modo": campos_iniciales.get("modo"),
                "empresa": _texto(campos_iniciales.get("empresa")),
                "campana": _texto(campos_iniciales.get("campana")),
                "plantilla_id": campos_iniciales.get("plantilla_id"),
                "user_id": _texto(campos_iniciales.get("user_id")),
                "fecha_desde": campos_iniciales.get("fecha_desde"),
                "fecha_hasta": campos_iniciales.get("fecha_hasta"),
                "cantidad_solicitada": cantidad_solicitada,
                "por_operador": campos_iniciales.get("por_operador"),
                "por_tipificacion": campos_iniciales.get("por_tipificacion"),
                "upload_group_id": upload_group_id,
            })
            return result.scalar()
    except Exception as e:
        logger.error(f"No se pudo continuar/iniciar el grupo de subida {upload_group_id}: {e}")
        return None


def finalizar_grupo(
    engine: Engine,
    *,
    upload_group_id: str,
    status: str,
    filas_auditadas: Optional[int] = None,
    filas_error: Optional[int] = None,
    tokens: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    modelo: Optional[str] = None,
    nivel_razonamiento: Optional[str] = None,
) -> None:
    """Cierra (ACUMULANDO, no sobreescribiendo) la fila de un grupo de subida CSV:
    puede haber más tandas después de esta, así que se suma a lo que ya tenía en
    vez de reemplazar. Ver continuar_o_iniciar_grupo."""
    if not upload_group_id:
        return
    tokens = tokens or {}
    # Ver `columna_cached` en finalizar_ejecucion: la columna la agrega la migración
    # 2026-09-01b y acá también se acumula, no se reemplaza.
    columna_cached = (
        "\n                    cached_tokens = COALESCE(cached_tokens, 0) + COALESCE(:cached_tokens, 0),"
        if tiene_columna_cached_tokens(engine) else ""
    )
    try:
        with engine.begin() as conn:
            conn.execute(text(f"""
                UPDATE calidad.AuditExecutionLog
                SET status = :status,
                    filas_auditadas = COALESCE(filas_auditadas, 0) + COALESCE(:filas_auditadas, 0),
                    filas_error = COALESCE(filas_error, 0) + COALESCE(:filas_error, 0),
                    input_tokens = COALESCE(input_tokens, 0) + COALESCE(:input_tokens, 0),
                    output_tokens = COALESCE(output_tokens, 0) + COALESCE(:output_tokens, 0),
                    thoughts_tokens = COALESCE(thoughts_tokens, 0) + COALESCE(:thoughts_tokens, 0),{columna_cached}
                    error_message = COALESCE(:error_message, error_message),
                    modelo = COALESCE(:modelo, modelo),
                    nivel_razonamiento = COALESCE(:nivel_razonamiento, nivel_razonamiento),
                    finished_at = SYSUTCDATETIME()
                WHERE upload_group_id = :gid
            """), {
                "status": status,
                "filas_auditadas": filas_auditadas,
                "filas_error": filas_error,
                "input_tokens": tokens.get("input_tokens"),
                "output_tokens": tokens.get("output_tokens"),
                "thoughts_tokens": tokens.get("thoughts_tokens"),
                **({"cached_tokens": tokens.get("cached_tokens")} if columna_cached else {}),
                "error_message": error_message,
                "modelo": modelo,
                "nivel_razonamiento": nivel_razonamiento,
                "gid": upload_group_id,
            })
    except Exception as e:
        logger.error(f"No se pudo cerrar el grupo de subida {upload_group_id}: {e}")


def corrida_terminada(engine: Engine, *, batch_id: str) -> Optional[Dict[str, Any]]:
    """Si el lote `batch_id` era el ÚLTIMO abierto de su corrida, devuelve el
    acumulado de la corrida entera; si todavía queda algún lote en vuelo, None.

    Es el disparador del cierre: el mail de resultado se manda una sola vez por
    corrida y no una por lote. Antes salía de `_procesar_grupo_batch`, o sea uno por
    cada job de Gemini — una corrida de Vantix partida en 14 lotes mandaba 14 mails,
    cada uno con una fracción de la auditoría.

    "Terminó" = ninguna fila del run_id sigue en EN_CURSO. Como los lotes cierran de
    a uno y `finalizar_ejecucion` solo toca filas EN_CURSO, exactamente uno de ellos
    ve el conjunto completo: no hay forma de que dos lotes disparen el mismo mail.

    Devuelve None también si la corrida no tiene lotes cerrados con resultados que
    reportar; el caller decide qué hacer con eso (ver `id_aplicativos` vacío)."""
    try:
        with engine.connect() as conn:
            fila = conn.execute(text("""
                SELECT TOP 1 run_id FROM calidad.AuditExecutionLog WHERE batch_id = :batch_id
            """), {"batch_id": batch_id}).mappings().first()
            if not fila or fila["run_id"] is None:
                return None
            run_id = str(fila["run_id"])

            abiertos = conn.execute(text("""
                SELECT COUNT(*) FROM calidad.AuditExecutionLog
                WHERE run_id = :run_id AND status = 'EN_CURSO'
            """), {"run_id": run_id}).scalar()
            if abiertos:
                logger.info(
                    f"Corrida {run_id}: quedan {abiertos} lote(s) en curso; "
                    f"el mail se manda cuando termine el último."
                )
                return None

            # Los lotes que esperan cupo (calidad.BatchPendientes) TODAVÍA no tienen
            # fila en este log: la abre el despacho, cuando el job existe. Sin este
            # chequeo, una corrida con 3 lotes enviados y 5 esperando mandaba el mail
            # al cerrar el tercero —con una fracción de las auditorías— y los otros
            # cinco cerraban después contra una corrida ya notificada, o sea que su
            # resultado no lo veía nadie. Va en su propio try: si la tabla todavía no
            # existe (migración sin correr), se comporta como antes.
            try:
                esperando = conn.execute(text("""
                    SELECT COUNT(*) FROM calidad.BatchPendientes
                    WHERE RunID = :run_id AND Estado IN ('PENDIENTE', 'ENVIANDO')
                """), {"run_id": run_id}).scalar()
            except Exception as e:
                logger.debug(f"No se pudo consultar la cola de lotes pendientes: {e}")
                esperando = 0
            if esperando:
                logger.info(
                    f"Corrida {run_id}: quedan {esperando} lote(s) esperando cupo en Gemini; "
                    f"el mail se manda cuando salgan y terminen."
                )
                return None

            # `cached_tokens` la agrega la migración 2026-09-01b; sin ella la corrida
            # se cierra igual y el mail costea como antes (todo el input a precio lleno).
            col_cached = "cached_tokens" if tiene_columna_cached_tokens(engine) else "0 AS cached_tokens"
            lotes = conn.execute(text(f"""
                SELECT id_aplicativos, filas_auditadas, filas_error,
                       input_tokens, output_tokens, thoughts_tokens, {col_cached},
                       mail_destinatarios, mail_enviado, status
                FROM calidad.AuditExecutionLog
                WHERE run_id = :run_id
                ORDER BY started_at, id
            """), {"run_id": run_id}).mappings().all()

        ids: list = []
        for lote in lotes:
            ids.extend(x for x in (lote["id_aplicativos"] or "").split(",") if x)
        tokens = {
            clave: sum(int(lote[clave] or 0) for lote in lotes)
            for clave in ("input_tokens", "output_tokens", "thoughts_tokens", "cached_tokens")
        }
        # Los destinatarios son los mismos en todos los lotes (viajan con la corrida);
        # se toma el primero que los tenga por si alguna fila vieja quedó sin ellos.
        destinatarios = next((lote["mail_destinatarios"] for lote in lotes if lote["mail_destinatarios"]), None)
        return {
            "run_id": run_id,
            "lotes": len(lotes),
            "id_aplicativos": ids,
            "filas_auditadas": sum(int(lote["filas_auditadas"] or 0) for lote in lotes),
            "filas_error": sum(int(lote["filas_error"] or 0) for lote in lotes),
            "tokens": tokens,
            "mail_destinatarios": destinatarios,
            "ya_notificada": any(lote["mail_enviado"] for lote in lotes),
            "estados": [lote["status"] for lote in lotes],
        }
    except Exception as e:
        logger.error(f"No se pudo evaluar el cierre de la corrida del batch {batch_id}: {e}")
        return None


def marcar_envio_corrida(
    engine: Engine, *, run_id: str, mail_enviado: bool, mail_destinatarios: Optional[str] = None
) -> None:
    """Deja registrado en TODOS los lotes de la corrida que el mail salió.

    Va en todas las filas y no solo en la que disparó el envío para que el listado
    agrupado de /uso-ia (MAX(mail_enviado)) muestre la corrida como notificada sin
    depender de cuál de sus lotes cerró último."""
    if not run_id:
        return
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE calidad.AuditExecutionLog
                SET mail_enviado = :mail_enviado,
                    mail_destinatarios = COALESCE(:mail_destinatarios, mail_destinatarios)
                WHERE run_id = :run_id
            """), {
                "mail_enviado": mail_enviado,
                "mail_destinatarios": mail_destinatarios,
                "run_id": run_id,
            })
    except Exception as e:
        logger.error(f"No se pudo marcar el envío de la corrida {run_id}: {e}")


def obtener_contexto(
    engine: Engine, *, batch_id: Optional[str] = None, scheduler_id: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """Lee la fila para enriquecer el mail: por batch_id (match exacto, modo
    batch) o la más reciente por scheduler_id (recién finalizada, modo sync)."""
    try:
        if batch_id:
            query = text("SELECT TOP 1 * FROM calidad.AuditExecutionLog WHERE batch_id = :batch_id")
            params: Dict[str, Any] = {"batch_id": batch_id}
        elif scheduler_id is not None:
            query = text("""
                SELECT TOP 1 * FROM calidad.AuditExecutionLog
                WHERE scheduler_id = :scheduler_id ORDER BY started_at DESC
            """)
            params = {"scheduler_id": scheduler_id}
        else:
            return None
        with engine.connect() as conn:
            row = conn.execute(query, params).mappings().first()
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"No se pudo leer el contexto de ejecución: {e}")
        return None


# Proporción del precio de input que cuesta un token cacheado cuando la tarifa no lo
# tiene cargado (migración 2026-09-01b sin aplicar, o un modelo nuevo sin precio). Es la
# relación que publica Google para toda la familia Flash: US$0,075 contra US$0,75.
PROPORCION_CACHED_POR_DEFECTO = 0.10


def obtener_tarifa(
    engine: Engine, *, modelo: str, fecha: Optional[datetime] = None
) -> Optional[Tuple[float, float, float]]:
    """Tarifa vigente (input, output, cacheado) en USD por millón, a esa fecha.

    1 sola consulta reutilizable para calcular el costo de muchas filas (ver
    calcular_costo_usd) sin ir a la BD por cada una — lo usa el listado de logs.

    `cached_usd_mtok` la agrega la migración 2026-09-01b. Si todavía no está, se
    deriva del input (ver PROPORCION_CACHED_POR_DEFECTO) en vez de romper el
    costeo: sin caché el término vale 0 igual, y con caché prefiero un número
    aproximado a uno que cobra el input entero.
    """
    fecha = fecha or datetime.utcnow()
    columna_cached = "cached_usd_mtok" if _tiene_precio_cacheado(engine) else "NULL AS cached_usd_mtok"
    try:
        with engine.connect() as conn:
            row = conn.execute(text(f"""
                SELECT TOP 1 input_usd_mtok, output_usd_mtok, {columna_cached}
                FROM pagina_web.IA_Precios
                WHERE modelo = :modelo AND fecha_desde <= :fecha
                ORDER BY fecha_desde DESC
            """), {"modelo": modelo, "fecha": fecha}).fetchone()
        if not row:
            return None
        input_usd = float(row[0])
        cached_usd = float(row[2]) if row[2] is not None else input_usd * PROPORCION_CACHED_POR_DEFECTO
        return (input_usd, float(row[1]), cached_usd)
    except Exception as e:
        logger.error(f"No se pudo obtener la tarifa vigente para {modelo}: {e}")
        return None


# Modos que Google factura al 50% del precio estándar: el batch asincrónico y el tier
# Flex (sincrónico, best-effort, "priced at 50% of the standard API"). Si se agrega otro
# modo con descuento, va acá Y en pagina_web.vw_IA_Uso_Costos: las dos fórmulas tienen
# que decir lo mismo o el mail de una corrida y la pantalla de gastos se contradicen.
MODOS_MITAD_DE_PRECIO = ("batch", "flex")


def calcular_costo_usd(tokens: Dict[str, Any], modo: str, tarifa: Optional[Tuple[float, ...]]) -> Optional[float]:
    """Aplica una tarifa ya obtenida (sin ir a la BD): misma fórmula que
    pagina_web.vw_IA_Uso_Costos (thinking a tarifa OUTPUT, batch y flex -50%).

    Los tokens cacheados salen ~10 veces más baratos y `prompt_token_count` de
    Gemini YA los incluye, así que se separan las dos porciones del input en vez
    de sumar una de más (ver AuditorIA/cache_plantillas.py). Una fila sin
    `cached_tokens` da exactamente lo mismo que antes.
    """
    if not tarifa:
        return None
    input_usd_mtok, output_usd_mtok = tarifa[0], tarifa[1]
    cached_usd_mtok = (
        tarifa[2] if len(tarifa) > 2 else input_usd_mtok * PROPORCION_CACHED_POR_DEFECTO
    )
    input_tokens = tokens.get("input_tokens") or 0
    cached_tokens = tokens.get("cached_tokens") or 0
    # Lo que se paga a precio lleno es lo que NO vino de la caché, con piso en cero:
    # así una fila rara (cacheados > input, o input sin registrar y cacheados sí —
    # pasa en asistente_docs) no resta plata pero tampoco regala los tokens que
    # realmente se consumieron. Idéntico al CASE de pagina_web.vw_IA_Uso_Costos.
    no_cacheados = max(input_tokens - cached_tokens, 0)
    output_tokens = (tokens.get("output_tokens") or 0) + (tokens.get("thoughts_tokens") or 0)
    costo = (
        no_cacheados / 1_000_000 * input_usd_mtok
        + cached_tokens / 1_000_000 * cached_usd_mtok
        + output_tokens / 1_000_000 * output_usd_mtok
    )
    if modo in MODOS_MITAD_DE_PRECIO:
        costo *= 0.5
    return round(costo, 4)


def estimar_costo_usd(
    engine: Engine, *, modelo: str, modo: str, tokens: Dict[str, Any], fecha: Optional[datetime] = None
) -> Optional[float]:
    """Conveniencia para estimar el costo de 1 sola corrida (mail de log):
    junta obtener_tarifa + calcular_costo_usd en una sola llamada."""
    return calcular_costo_usd(tokens, modo, obtener_tarifa(engine, modelo=modelo, fecha=fecha))


def formatear_duracion(inicio: Optional[datetime], fin: Optional[datetime] = None) -> str:
    if not inicio:
        return "N/D"
    fin = fin or datetime.utcnow()
    segundos = (fin - inicio).total_seconds()
    if segundos < 0:
        return "N/D"
    horas, resto = divmod(int(segundos), 3600)
    minutos = resto // 60
    return f"{horas}h {minutos}min" if horas else f"{minutos}min"


def armar_detalle_html(
    *,
    modo: str,
    origen_txt: str,
    empresa: Optional[Any] = None,
    campana: Optional[Any] = None,
    plantilla_id: Optional[Any] = None,
    fecha_desde: Optional[Any] = None,
    fecha_hasta: Optional[Any] = None,
    cantidad_solicitada: Optional[int] = None,
    filas_auditadas: int = 0,
    filas_error: int = 0,
    tokens: Optional[Dict[str, Any]] = None,
    duracion_txt: str = "N/D",
    costo_usd: Optional[float] = None,
    nivel_razonamiento: Optional[str] = None,
) -> str:
    """Bloque HTML con los atributos de la corrida (usado por el mail sync del
    scheduler y el mail de cierre de batch, para no duplicar el markup)."""
    tokens = tokens or {}
    rango_txt = f"{fecha_desde} a {fecha_hasta}" if fecha_desde and fecha_hasta else "N/D"
    costo_txt = f" (~${costo_usd:.4f} USD)" if costo_usd is not None else ""
    cacheados = tokens.get('cached_tokens', 0) or 0
    cacheados_txt = f" (de los cuales {cacheados} cacheados)" if cacheados else ""
    cantidad_txt = cantidad_solicitada if cantidad_solicitada is not None else "N/D"
    # El nivel explica el grueso de los tokens de razonamiento de la línea de abajo:
    # sin él, un salto de costo entre dos corridas de la misma plantilla no se entiende.
    nivel_txt = f" (razonamiento {razonamiento.label(nivel_razonamiento)})" if nivel_razonamiento else ""
    return (
        "<b>Detalle de la corrida:</b><br>"
        "<ul>"
        f"<li>Modo: {'Batch' if modo == 'batch' else 'Sincrónico'}{nivel_txt}</li>"
        f"<li>Empresa / Campaña / Plantilla: {empresa or 'N/D'} / {campana or 'N/D'} / {plantilla_id or 'N/D'}</li>"
        f"<li>Rango auditado: {rango_txt} — {cantidad_txt} solicitadas, {filas_auditadas} auditadas</li>"
        f"<li>Origen: {origen_txt}</li>"
        f"<li>Duración: {duracion_txt}</li>"
        f"<li>Ítems con error/omitidos: {filas_error}</li>"
        f"<li>Tokens consumidos: entrada {tokens.get('input_tokens', 0) or 0}"
        # Los cacheados ya están contados en la entrada (prompt_token_count los
        # incluye): se aclaran aparte porque salen ~10 veces más baratos y sin eso
        # el mail parece decir que la corrida costó lo mismo que antes del caché.
        f"{cacheados_txt}, "
        f"salida {tokens.get('output_tokens', 0) or 0}, "
        f"razonamiento {tokens.get('thoughts_tokens', 0) or 0}{costo_txt}</li>"
        "</ul>"
    )
# --- END OF FILE execution_log.py ---
