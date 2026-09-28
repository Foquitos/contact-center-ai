import logging
import pandas as pd
import numpy as np
from sqlalchemy import Engine, text, bindparam
from sqlalchemy.dialects.mssql import NVARCHAR

from AuditorIA import incidencias as inc
from AuditorIA import limites_texto
from AuditorIA import reauditoria
from AuditorIA.scoring import calcular_puntaje

logger = logging.getLogger(__name__)


def calcular_id_aplicativo(df: pd.DataFrame) -> pd.Series:
    """Calcula el IdAplicativo de cada fila de forma UNIFORME (misma prioridad y
    case-insensitive).

    Fuente ÚNICA de la clave: la usan tanto la auditoría (_estandarizar_columnas_sql)
    como la transcripción (guardar_transcripciones_sql). Si cada lado calculara la
    clave distinto (orden de prioridad / mayúsculas), la transcripción quedaría bajo
    otra clave y el SP no la asociaría a su auditoría -> auditorías "sin transcripción"
    que sí la tienen (bajo otra clave) y viceversa.
    """
    if df.empty:
        return pd.Series([], dtype=object)

    cols_lower_map = {col.lower(): col for col in df.columns}
    id_aliases = [
        'id_aplicativo', 'idaplicativo', 'idinteraccion', 'connid',
        'ucid_segmento', 'segmentid', 'id', 'caso_id', 'id del llamado'
    ]
    for alias in id_aliases:
        if alias in cols_lower_map:
            real_col = cols_lower_map[alias]
            # idinteraccion lleva el segmento concatenado si existe.
            if alias == 'idinteraccion' and 'segmento' in cols_lower_map:
                real_seg = cols_lower_map['segmento']
                return df[real_col].astype(str) + '_' + df[real_seg].astype(str)
            return df[real_col]

    return pd.Series([None] * len(df), index=df.index, dtype=object)


def _cargar_meta_atributos(connection, plantilla_id):
    """Devuelve {AtributoID: {"tipo": str, "ponderacion": float}} para una plantilla.

    Se usa para puntuar (ponderación + tipo critical_audit) al momento de guardar.
    Si la columna Ponderacion aún no existe (migración 002 no aplicada), degrada a 0.
    """
    if plantilla_id is None:
        return {}
    try:
        rows = connection.execute(
            text("""
                SELECT AtributoID, TipoDato, Ponderacion
                FROM calidad.Atributos
                WHERE PlantillaID = :pid AND IsActive = 1
            """),
            {"pid": int(plantilla_id)},
        ).fetchall()
    except Exception as e:
        # Fallback si la migración de Ponderacion no está aplicada todavía.
        logger.warning("No se pudo leer Ponderacion (¿migración 002 aplicada?): %s", e)
        rows = connection.execute(
            text("""
                SELECT AtributoID, TipoDato, 0 AS Ponderacion
                FROM calidad.Atributos
                WHERE PlantillaID = :pid AND IsActive = 1
            """),
            {"pid": int(plantilla_id)},
        ).fetchall()
    return {int(r[0]): {"tipo": r[1], "ponderacion": float(r[2] or 0)} for r in rows}

def tiene_columna_auditorias(engine: Engine, columna: str) -> bool:
    """¿Existe esa columna en calidad.Auditorias?

    Se chequea UNA vez por lote (no por fila) y antes de escribir: si la migración que
    la agrega no está aplicada, se guarda sin la columna en vez de que reviente cada
    INSERT de auditoría.
    """
    try:
        with engine.connect() as conn:
            return conn.execute(text("""
                SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = 'calidad' AND TABLE_NAME = 'Auditorias'
                  AND COLUMN_NAME = :columna
            """), {"columna": columna}).first() is not None
    except Exception as e:
        logger.warning("No se pudo verificar la columna %s: %s", columna, e)
        return False


def _tiene_columna_version(engine: Engine) -> bool:
    """¿Está aplicada la migración 2026-08-06d (Auditorias.PlantillaVersionID)?"""
    return tiene_columna_auditorias(engine, 'PlantillaVersionID')


def _puede_congelar_operador(engine: Engine) -> bool:
    """¿Está aplicada la migración 2026-08-25 (usuarios reciclados)?

    Hacen falta las DOS piezas: la columna donde congelar la persona y la función
    que la resuelve. Si falta alguna se guarda sin congelar y la auditoría queda
    como hasta ahora (la persona se infiere al leer, en el SP).
    """
    if not tiene_columna_auditorias(engine, 'OperadorNominaID'):
        return False
    try:
        with engine.connect() as conn:
            return conn.execute(text(
                "SELECT OBJECT_ID('calidad.fn_ResolverOperadorAuditoria', 'IF')"
            )).scalar() is not None
    except Exception as e:
        logger.warning("No se pudo verificar fn_ResolverOperadorAuditoria: %s", e)
        return False


def merge_dataframes_dict(dictionary_of_dfs):
    """
    Merges all dataframes in a dictionary into a single dataframe.
    
    Args:
        dictionary_of_dfs (dict): Dictionary where keys are names and values are pandas DataFrames
        
    Returns:
        pd.DataFrame: A single dataframe containing all rows from all dataframes
    """
    # Check if dictionary is empty
    if not dictionary_of_dfs:
        return pd.DataFrame()
    
    # Extract all dataframes from the dictionary
    dfs_list = list(dictionary_of_dfs.values())
    
    # Concatenate all dataframes into one
    merged_df = pd.concat(dfs_list, ignore_index=True)
    
    # Optionally add a column indicating the source/key
    # merged_df['source_key'] = [key for key, df in dictionary_of_dfs.items() for _ in range(len(df))]
    
    return merged_df

def clean_value(val):
    """Función auxiliar que limpia un único valor."""
    # Convierte tipos enteros de NumPy a int de Python
    if isinstance(val, np.integer):
        return int(val)
    # Convierte tipos flotantes de NumPy a float de Python
    if isinstance(val, np.floating):
        return float(val)
    # Convierte valores nulos (NaN, NaT, pd.NA) a None, que es el NULL de SQL
    if pd.isna(val):
        return None
    # Si no es ninguno de los anteriores, lo devuelve como está
    return val

def convert_types_for_sql(df: pd.DataFrame) -> pd.DataFrame:
    """
    Recorre cada celda de un DataFrame para convertir los tipos de datos de
    NumPy/Pandas a tipos nativos de Python compatibles con SQL.
    """
    # applymap aplica la función clean_value a cada celda del DataFrame
    return df.applymap(clean_value) # pyright: ignore[reportCallIssue]


def _auditoria_existente(connection, id_aplicativo, plantilla_id) -> "int | None":
    """¿Ya hay una auditoría de este llamado con esta plantilla?

    Se pregunta por (IdAplicativo, PlantillaID) y no solo por el llamado: el mismo
    audio auditado con DOS plantillas distintas son dos auditorías legítimas y
    separadas, no una reauditoría.
    """
    if id_aplicativo is None or plantilla_id is None:
        return None
    fila = connection.execute(text("""
        SELECT TOP 1 AuditoriaID FROM calidad.Auditorias
        WHERE IdAplicativo = :id_app AND PlantillaID = :plan_id
        ORDER BY AuditoriaID DESC
    """), {"id_app": str(id_aplicativo), "plan_id": plantilla_id}).scalar()
    return int(fila) if fila else None


def _sets_reemplazo(connection, guardar_version: bool, guardar_incidencia: bool) -> str:
    """El SET del UPDATE que reemplaza una auditoría por su corrida nueva.

    Solo las columnas que produce la corrida. El contexto del llamado (operador,
    fecha, tipificación, empresa) es el mismo audio y no se toca — pisarlo con lo
    que venga del pedido nuevo podría cambiarle el dueño a una auditoría vieja.
    """
    sets = [
        "PuntajeFinal = :puntaje_final", "EsErrorCritico = :es_ec",
        "FechaAuditoria = GETDATE()", "AuditorUsuarioID = :audit_id",
        "input_tokens = :input_tokens", "output_tokens = :output_tokens",
        "thoughts_tokens = :thoughts_tokens", "response_thoughts = :response_thoughts",
        "Extras = :extras",
    ]
    if guardar_version:
        sets.append("PlantillaVersionID = :plantilla_version_id")
    if guardar_incidencia:
        sets.append("Incidencia = :incidencia")
    return ", ".join(sets)


def auditoria_a_SQL(
    df: pd.DataFrame,
    engine: Engine,
    modo: str = "sync",
    # Modelo Gemini que produjo estas auditorías. Debe coincidir con el usado en
    # AuditorIA/gemini.py (hoy "gemini-3.8-flash" en sync y batch). Se registra para
    # el libro de consumo de IA (pagina_web.IA_Uso) / tablero de gastos.
    modelo: str = "gemini-3.8-flash",
):
    """
    Guarda un DataFrame de auditoría en la base de datos SQL de forma eficiente.

    Esta función itera sobre las filas del DataFrame de manera óptima y ejecuta
    una transacción por cada auditoría para insertar el registro principal y
    sus detalles asociados atómicamente.

    Args:
        df (pd.DataFrame): DataFrame que contiene los datos de auditoría.
                           Se espera que las columnas de detalles comiencen con "Detalle_".
        engine (Engine): Conexión SQLAlchemy a la base de datos (SQL Server).
        modo (str): "sync" o "batch" (batch factura -50%). Se registra en IA_Uso.
        modelo (str): nombre del modelo Gemini usado, para el libro de consumo de IA.
    """
    if df.empty:
        logging.info("El DataFrame de auditoría está vacío. No se realizarán inserciones.")
        return

    # Pre-calcular las columnas de detalles para no hacerlo en cada iteración.
    detalle_cols = [col for col in df.columns if col.startswith("Detalle_")]
    if not detalle_cols:
        logging.warning("No se encontraron columnas de detalles (que comiencen con 'Detalle_').")

    df = df.replace({np.nan: None})

    # Cache de metadata de atributos por plantilla (tipo + ponderación) para puntuar.
    meta_por_plantilla = {}

    # Consumos de Gemini a registrar (uno por auditoría insertada con éxito). Se
    # vuelca al libro pagina_web.IA_Uso al final, fuera del loop (best-effort).
    usos_ia = []

    # Versión de plantilla (Golden Set — Fase 2): la columna se resuelve una sola
    # vez para todo el lote y el INSERT se arma en consecuencia.
    guardar_version = _tiene_columna_version(engine)
    columna_version = ", PlantillaVersionID" if guardar_version else ""
    valor_version = ", :plantilla_version_id" if guardar_version else ""

    # Incidencia (migración 2026-08-18): motivo por el que NO hay que confiar en esta
    # auditoría (audio mudo, operador que no coincide...). Mismo criterio que arriba:
    # si la migración no está aplicada se guarda sin la columna.
    guardar_incidencia = tiene_columna_auditorias(engine, 'Incidencia')
    columna_incidencia = ", Incidencia" if guardar_incidencia else ""
    valor_incidencia = ", :incidencia" if guardar_incidencia else ""

    # Persona congelada (migración 2026-08-25 — usuarios reciclados). `operadorUsuario`
    # es el string de la plataforma, y esos usuarios se reciclan: si la persona se
    # resuelve recién al leer, la misma auditoría puede terminar a nombre de otro
    # cuando el usuario cambia de dueño. Se resuelve UNA vez, acá, con la fecha y la
    # empresa de la interacción, y queda fija. La resolución va dentro del INSERT (la
    # función devuelve a lo sumo una fila) para no pagar un round-trip por auditoría.
    congelar_operador = _puede_congelar_operador(engine)
    columna_operador = ", OperadorNominaID" if congelar_operador else ""
    valor_operador = (
        ", (SELECT NominaID FROM calidad.fn_ResolverOperadorAuditoria("
        ":operador_usuario, :emp_id, :fecha_interaccion))"
    ) if congelar_operador else ""

    # Usar itertuples() para un rendimiento óptimo.
    for row in df.itertuples(index=False):
        # Acceder a los datos por nombre de atributo de la tupla
        id_aplicativo = getattr(row, "id_aplicativo", None)
        operador_usuario = getattr(row, "operador_usuario", None)
        fecha_interaccion = getattr(row, "fecha_interaccion", None)
        sentido_interaccion = getattr(row, "sentido_interaccion", None)
        tipificacion_interaccion = getattr(row, "tipificacion_interaccion", None)
        duracion_segundos = getattr(row, "duracion_segundos", None)
        comentario_interaccion = getattr(row, "comentario_interaccion", None)
        campana_id = getattr(row, "campana_id", None)
        empresa_id = getattr(row, "empresa_id", None)
        plantilla_id = getattr(row, "plantilla_id", None)
        auditor_id = getattr(row, "auditor_id", None)
        input_tokens = getattr(row, "input_tokens", None)
        output_tokens = getattr(row, "output_tokens", None)
        thoughts_tokens = getattr(row, "thoughts_tokens", None)
        # Solo para el libro de uso de IA: calidad.Auditorias no tiene esta columna
        # (no hace falta, el costo se reporta desde pagina_web.IA_Uso).
        cached_tokens = getattr(row, "cached_tokens", None)
        response_thoughts = getattr(row, "response_thoughts", None)
        Extras = getattr(row, "Extras", None)
        incidencia = inc.limpiar_valor(getattr(row, "incidencia", None))

        # Construir la lista de detalles para la fila actual.
        # Con una incidencia bloqueante (audio mudo, operador que no coincide) el
        # resultado de la IA no es confiable: la auditoría se guarda como registro del
        # llamado pero SIN detalles, así no puntúa ni entra en el promedio del operador
        # —mismo criterio que un atributo opcional sin evidencia—.
        detalles_lista = []
        cols_a_guardar = [] if incidencia in inc.BLOQUEANTES else detalle_cols
        for i, col in enumerate(cols_a_guardar):
            atributo_id = int(col.split("_")[1])
            valor = getattr(row, col)

            # Red de contención del tope de texto libre (ver AuditorIA/limites_texto.py).
            # El max_length del response_schema ya se lo pide al modelo, pero si lo ignora
            # —o si la plantilla es vieja y tiene un atributo redactado como pedido de
            # transcripción— el texto se recorta acá, antes de que un llamado entero
            # transcripto entre a AuditoriaDetalles y salga por el export.
            valor = limites_texto.recortar_valor(valor, atributo_id=atributo_id, id_aplicativo=id_aplicativo)

            # Sin valor no se guarda detalle: es el caso de los atributos OPCIONALES
            # que la IA omitió por falta de evidencia (quedan sin auditar, sin puntuar
            # y sin penalizar). También cubre las respuestas en blanco.
            if valor is None:
                continue
            if isinstance(valor, str) and not valor.strip():
                continue
            if isinstance(valor, (list, tuple, np.ndarray)) and len(valor) == 0:
                continue

            detalles_lista.append({
                "atributo_id": atributo_id,
                "valor": valor,
                "orden": i + 1
            })

        sql_insert_auditoria = text(f"""
            INSERT INTO calidad.Auditorias (
                IdAplicativo, operadorUsuario, fecha_interaccion, sentido_interaccion, tipificacion_interaccion, duracion_segundos, comentario_interaccion, CampanaID, EmpresaID, PlantillaID, AuditorUsuarioID, input_tokens, output_tokens, thoughts_tokens, response_thoughts, Extras, PuntajeFinal, EsErrorCritico{columna_version}{columna_incidencia}{columna_operador}
            )
            OUTPUT INSERTED.AuditoriaID
            VALUES (
                :id_app, :operador_usuario, :fecha_interaccion, :sentido_interaccion, :tipificacion_interaccion, :duracion_segundos, :comentario_interaccion, :camp_id, :emp_id, :plan_id, :audit_id, :input_tokens, :output_tokens, :thoughts_tokens, :response_thoughts, :extras, :puntaje_final, :es_ec{valor_version}{valor_incidencia}{valor_operador}
            );
        """).bindparams(
            # Tipar explícitamente como texto Unicode sin límite (NVARCHAR(max)). Si se
            # deja el bind sin tipo, pyodbc adivina y declara el parámetro como
            # nvarchar(4000), truncando los pensamientos largos del modelo a 4000
            # caracteres (independientemente del largo de la columna).
            bindparam("response_thoughts", type_=NVARCHAR(None)),
            bindparam("extras", type_=NVARCHAR(None)),
        )

        try:
            # Usar una sola transacción para todas las operaciones
            with engine.begin() as connection:
                # Cargar (1 vez por plantilla) la metadata de atributos para puntuar.
                if plantilla_id not in meta_por_plantilla:
                    meta_por_plantilla[plantilla_id] = _cargar_meta_atributos(connection, plantilla_id)
                meta = meta_por_plantilla.get(plantilla_id, {})

                # --- Cálculo de puntaje ponderado + Error Crítico (snapshot) ---
                items_score = []
                for det in detalles_lista:
                    m = meta.get(det["atributo_id"], {})
                    det["ponderacion"] = m.get("ponderacion", 0)
                    items_score.append({
                        "id": det["atributo_id"],
                        "valor": det["valor"],
                        "ponderacion": det["ponderacion"],
                        "tipo": m.get("tipo"),
                    })
                resultado = calcular_puntaje(items_score)

                params_auditoria = {
                    "id_app": id_aplicativo,
                    "operador_usuario": operador_usuario,
                    "fecha_interaccion": fecha_interaccion,
                    "sentido_interaccion": sentido_interaccion,
                    "tipificacion_interaccion": tipificacion_interaccion,
                    "duracion_segundos": duracion_segundos,
                    "comentario_interaccion": comentario_interaccion,
                    "camp_id": campana_id,
                    "emp_id": empresa_id,
                    "plan_id": plantilla_id,
                    "audit_id": auditor_id,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "thoughts_tokens": thoughts_tokens,
                    "response_thoughts": response_thoughts,
                    "extras": Extras,
                    "puntaje_final": resultado.puntaje,
                    "es_ec": 1 if resultado.es_error_critico else 0,
                }
                if guardar_version:
                    params_auditoria["plantilla_version_id"] = getattr(row, "plantilla_version_id", None)
                if guardar_incidencia:
                    params_auditoria["incidencia"] = incidencia

                # ¿Este llamado YA estaba auditado con esta plantilla? Pasa cuando se
                # audita con `reauditar=True` o en batch de reauditoría.
                # Se reemplaza en su lugar y la corrida anterior se archiva: una sola fila
                # por llamado, siempre la última versión, y la evidencia de lo anterior no se pierde.
                auditoria_id_row = getattr(row, "auditoria_id", None)
                if auditoria_id_row is not None and not (isinstance(auditoria_id_row, float) and pd.isna(auditoria_id_row)):
                    try:
                        previa = int(auditoria_id_row)
                    except (ValueError, TypeError):
                        previa = _auditoria_existente(connection, id_aplicativo, plantilla_id)
                else:
                    previa = _auditoria_existente(connection, id_aplicativo, plantilla_id)

                if previa is not None:
                    # Incidencia bloqueante (audio mudo, etc.): la respuesta no es confiable.
                    # NO se pisa la corrida anterior con una inservible.
                    if incidencia in inc.BLOQUEANTES:
                        logging.warning(
                            "Auditoría %s: la nueva corrida detectó incidencia bloqueante (%s). "
                            "Se conserva la auditoría anterior.", previa, incidencia
                        )
                        continue

                    motivo_arch = (
                        getattr(row, "motivo_reauditoria", None)
                        or getattr(row, "motivo", None)
                        or "Reauditada desde Auditorías Realizadas"
                    )
                    reauditoria.archivar_version(
                        connection, previa,
                        usuario_id=auditor_id,
                        motivo=str(motivo_arch),
                    )
                    connection.execute(
                        text("DELETE FROM calidad.AuditoriaDetalles WHERE AuditoriaID = :aid"),
                        {"aid": previa},
                    )
                    connection.execute(
                        text(
                            "UPDATE calidad.Auditorias SET "
                            f"{_sets_reemplazo(connection, guardar_version, guardar_incidencia)} "
                            "WHERE AuditoriaID = :aid_upd"
                        ).bindparams(
                            # Mismo motivo que en el INSERT: sin tipar, pyodbc declara
                            # nvarchar(4000) y trunca los pensamientos largos.
                            bindparam("response_thoughts", type_=NVARCHAR(None)),
                            bindparam("extras", type_=NVARCHAR(None)),
                        ),
                        dict(params_auditoria, aid_upd=previa),
                    )
                    nueva_auditoria_id = previa
                else:
                    # Paso 1: Insertar la auditoría y obtener el ID en un solo paso
                    result = connection.execute(sql_insert_auditoria, params_auditoria)
                    nueva_auditoria_id = result.scalar() # Obtenemos el ID directamente del resultado del INSERT

                if not nueva_auditoria_id:
                    # Si esto falla, es casi seguro que el nombre de la columna en OUTPUT es incorrecto.
                    raise Exception("No se pudo obtener el ID de la auditoría. Verifica que 'INSERTED.ID' en la consulta SQL coincida con el nombre de tu columna de identidad.")

                # Paso 2: Preparar y ejecutar la inserción de detalles (con ponderación snapshot)
                if detalles_lista:
                    values_clause = ", ".join(
                        [f"(:auditoria_id, :atributo_id_{i}, :valor_{i}, :orden_{i}, :pond_{i})" for i in range(len(detalles_lista))]
                    )

                    sql_insert_detalles = text(f"""
                        INSERT INTO calidad.AuditoriaDetalles (AuditoriaID, AtributoID, ValorResultado, Orden, PonderacionAplicada)
                        VALUES {values_clause};
                    """)

                    detalle_params = {"auditoria_id": nueva_auditoria_id}
                    for i, detalle in enumerate(detalles_lista):
                        detalle_params[f"atributo_id_{i}"] = detalle["atributo_id"]
                        detalle_params[f"valor_{i}"] = str(detalle["valor"])
                        detalle_params[f"orden_{i}"] = detalle["orden"]
                        detalle_params[f"pond_{i}"] = detalle.get("ponderacion", 0)

                    connection.execute(sql_insert_detalles, detalle_params)

                logging.info(
                    "Auditoría %s. ID: %s (Campaña: %s, Puntaje: %s%s)",
                    "REEMPLAZADA (la anterior quedó archivada)" if previa is not None
                    else "agregada con éxito",
                    nueva_auditoria_id, campana_id, resultado.puntaje,
                    " EC" if resultado.es_error_critico else "",
                )

            # La auditoría quedó commiteada: anotamos su consumo de Gemini para el
            # libro pagina_web.IA_Uso (se registra en lote al final, best-effort).
            usos_ia.append({
                "feature": "auditoria",
                "modelo": modelo,
                "modo": modo,
                "user_id": auditor_id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "thoughts_tokens": thoughts_tokens,
                "cached_tokens": cached_tokens,
                "campana_id": campana_id,
                "empresa_id": empresa_id,
                "ref_id": f"auditoria:{nueva_auditoria_id}",
                "extras": {"id_aplicativo": id_aplicativo} if id_aplicativo is not None else None,
            })

        except Exception as e:
            logging.error(f"Error al insertar la auditoría para la campaña {campana_id}. Error: {e}", exc_info=True)

    # Registrar en el libro centralizado de consumo de IA (no debe romper el guardado).
    if usos_ia:
        try:
            from app.uso_ia import registrar_uso_ia_bulk
            registrar_uso_ia_bulk(usos_ia)
        except Exception:
            logging.exception("No se pudo registrar el consumo de IA de las auditorías.")

def verificar_calidad_en_SQL(engine: Engine, df: pd.DataFrame, plantilla_id: int) -> pd.DataFrame:
    """
    Verifica la calidad de los datos en SQL para cada fila del DataFrame de entrada.

    Esta función consulta una base de datos SQL para cada fila del DataFrame de entrada,
    buscando coincidencias basadas en 'idInteraccion' y 'Segmento'. Separa los resultados
    en dos DataFrames: uno para las filas con calidad verificada y otro para las que no.

    Args:
        engine: Conexión SQLAlchemy a la base de datos.
        df (pd.DataFrame): DataFrame de entrada con las columnas 'Campaña', 'idInteraccion' y 'Segmento'.

    Returns:
        tuple[pd.DataFrame, pd.DataFrame]: Una tupla conteniendo:
            - DataFrame con las filas sin calidad verificada.
            - DataFrame con las filas con calidad verificada.

    """

    if df.empty:
        return df

    def _calcular_id_aplicativo(row):
        """Detección dinámica de la columna ID según la campaña (mismo criterio
        que usa el resto del pipeline al guardar)."""
        # Caso 1: Mitrol estándar (idInteraccion + Segmento)
        if 'idInteraccion' in row and 'Segmento' in row and pd.notna(row['idInteraccion']):
            return str(row['idInteraccion']) + '_' + str(row['Segmento'])
        # Caso 2: Voltara (ConnID) / CSV (ucid_segmento) / ALARMIX-CXOne (segmentId) / Hidra Comercial-Hermes (ID)
        for col in ('ConnID', 'ucid_segmento', 'segmentId', 'ID'):
            if col in row and pd.notna(row[col]):
                return str(row[col])
        return None

    # 1. Calcular el IdAplicativo de cada fila una sola vez.
    ids_por_fila = df.apply(_calcular_id_aplicativo, axis=1)
    ids_a_verificar = list(dict.fromkeys(i for i in ids_por_fila if i is not None))

    # 2. Una sola consulta (por lotes) para saber cuáles ya fueron auditados,
    #    en lugar de un SELECT por fila. Traemos solo la columna ID, no SELECT *.
    #    Lotes de 1000 para no superar el tope de parámetros de SQL Server (2100).
    ids_existentes: set[str] = set()
    for inicio in range(0, len(ids_a_verificar), 1000):
        lote = ids_a_verificar[inicio:inicio + 1000]
        placeholders = ", ".join(["?"] * len(lote))
        query = f"""
            SELECT DISTINCT [IdAplicativo]
            FROM [calidad].[Auditorias]
            WHERE [PlantillaID] = ? AND [IdAplicativo] IN ({placeholders})
        """
        df_existentes = pd.read_sql(query, engine, params=(plantilla_id, *lote))
        if not df_existentes.empty:
            ids_existentes.update(df_existentes['IdAplicativo'].astype(str).tolist())

    # 3. Mantener filas sin ID verificable (no se pueden chequear, se auditan)
    #    o cuyo ID todavía no fue auditado para esta plantilla.
    mask_auditar = ids_por_fila.apply(lambda i: i is None or str(i) not in ids_existentes)

    return df[mask_auditar].reset_index(drop=True)
