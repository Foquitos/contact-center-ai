import random
import io
import logging
import pandas as pd
import json
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import os
import re
import subprocess
import threading
import traceback
from google import genai
from google.genai import types

from AuditorIA.sql_a_Claude import calcular_id_aplicativo

# --- Prompt Definition ---
prompt = {
    'user':"""Puedes transcribir este llamado telefónico entre un cliente y un operador telefónico?
Para esto arma un Json el cual tendrá la key Transcripcion que es lista, que internamente tendra cada parte de la transcripcion teniendo las key

Descripción del Esquema JSON para la Transcripción de Audio
A continuación se detalla la estructura y el contenido esperado para cada clave del objeto JSON de salida. El objetivo es generar una respuesta precisa y COMPLETA: transcribí el llamado de principio a fin, sin resumir ni omitir turnos ni partes de la conversación. La eficiencia de tokens es solo de FORMATO (no coloques indentacion, espacios en blanco, tabulaciones ni saltos de linea dentro del JSON), NUNCA a costa de recortar lo que se dice en el audio.

Objeto Raíz
El objeto principal contendrá tres claves de nivel superior: metadata, segments y analytics.

metadata (Objeto): Contiene información general sobre el proceso de transcripción.

segments (Array de Objetos): Es el núcleo de la transcripción. Contiene la conversación dividida por interlocutor y con marcas de tiempo.

analytics (Objeto): Contiene información analítica derivada del texto, como palabras clave y entidades.

1. Objeto metadata
Contiene los metadatos globales del audio procesado.

overallConfidence (Float): Un valor numérico entre 0.0 y 1.0 que representa la confianza promedio en la precisión de toda la transcripción.

languageCode (String): El código del idioma detectado en el audio, siguiendo el formato IETF BCP-47. Ejemplo: \"es-AR\", \"en-US\".

audioDurationSeconds (Float): La duración total del archivo de audio original, expresada en segundos. Puede incluir decimales para mayor precisión.

2. Array segments
Este es un array donde cada objeto representa un fragmento de discurso continuo de un único hablante.

speakerLabel (String): Un identificador consistente para cada hablante. Se recomienda usar etiquetas genéricas y normalizadas como \"Cliente\", \"Agente\".

startTime (Float): El tiempo exacto en segundos desde el inicio del audio en que comienza este segmento de discurso.

endTime (Float): El tiempo exacto en segundos desde el inicio del audio en que finaliza este segmento de discurso.

confidence (Float): El puntaje de confianza promedio para la transcripción de este segmento específico, en una escala de 0.0 a 1.0.

text (Array de strings): Una lista con cada palabra dicha en ese segmento transcrito

3. Objeto analytics
Contiene información de valor agregado extraída del contenido de la transcripción.

keywords (Array de Strings): Una lista de las palabras clave o temas más importantes detectados en la conversación. Deben ser palabras en minúscula que resuman el propósito de la llamada.

entities (Array de Objetos): Una lista de las \"entidades nombradas\" identificadas.

Objeto dentro del array entities
type (String): La categoría de la entidad, usando un conjunto predefinido de tipos en mayúsculas. Ejemplos: \"Auto\", \"Lugar\", \"Fecha\", \"Producto\", \"ORDER_NUMBER\", \"EMAIL\".

text (String): El texto exacto de la entidad tal como apareció en la conversación.


Hazlo sin dejar identacion ni saltos de linea""",
    'system':"""Eres un experto transcriptor de llamadas telefonicas de un call center el cual es capaz de escuchar atentamente cada llamado, a pesar de los posibles problemas de comunicacion para transcribir con la mayor calidad, entregando tus resultados en un Json con un formato definido el cual no cuenta con salto de linea ni espacios en blanco o tabulaciones
    Transcribí el llamado COMPLETO, de principio a fin, sin resumir ni omitir turnos. Incluí todo lo que se dice, incluidas muletillas y repeticiones normales de una conversación. Lo único que NO debés hacer es entrar en un bucle repitiendo la MISMA frase o palabra muchas veces cuando el audio no la contiene: si detectás que te estás repitiendo de forma anómala, continuá con la parte siguiente del audio.""",
    'response_schema':types.Schema(
            type = types.Type.OBJECT,
            required = ["metadata", "segments", "analytics"],
            properties = {
                "metadata": types.Schema(
                    type = types.Type.OBJECT,
                    required = ["overallConfidence", "languageCode", "audioDurationSeconds"],
                    properties = {
                        "overallConfidence": types.Schema(
                            type = types.Type.NUMBER,
                        ),
                        "languageCode": types.Schema(
                            type = types.Type.STRING,
                        ),
                        "audioDurationSeconds": types.Schema(
                            type = types.Type.NUMBER,
                        ),
                    },
                ),
                "segments": types.Schema(
                    type = types.Type.ARRAY,
                    items = types.Schema(
                        type = types.Type.OBJECT,
                        required = ["speakerLabel", "startTime", "endTime", "confidence", "text"],
                        properties = {
                            "speakerLabel": types.Schema(
                                type = types.Type.STRING,
                                description = "Indica quién está hablando.",
                                enum = ["Agente", "Cliente"],
                            ),
                            "startTime": types.Schema(
                                type = types.Type.NUMBER,
                                description = "Segundo exacto en el que comienza este turno de habla.",
                            ),
                            "endTime": types.Schema(
                                type = types.Type.NUMBER,
                                description = "Duración en segundos de este turno de habla.",
                            ),
                            "confidence": types.Schema(
                                type = types.Type.NUMBER,
                            ),
                            "text": types.Schema(
                                type = types.Type.ARRAY,
                                items = types.Schema(
                                    type = types.Type.STRING,
                                ),
                            ),
                        },
                    ),
                ),
                "analytics": types.Schema(
                    type = types.Type.OBJECT,
                    required = ["keywords", "entities"],
                    properties = {
                        "keywords": types.Schema(
                            type = types.Type.ARRAY,
                            items = types.Schema(
                                type = types.Type.STRING,
                            ),
                        ),
                        "entities": types.Schema(
                            type = types.Type.ARRAY,
                            items = types.Schema(
                                type = types.Type.OBJECT,
                                required = ["type", "text"],
                                properties = {
                                    "type": types.Schema(
                                        type = types.Type.STRING,
                                    ),
                                    "text": types.Schema(
                                        type = types.Type.STRING,
                                    ),
                                },
                            ),
                        ),
                    },
                ),
            },
        )
}

# --- Helper Functions ---

def clean_value(val):
    """
    Limpia un valor para que sea compatible con SQL.
    Maneja valores escalares y también listas/arrays convirtiéndolos a string.
    """
    if isinstance(val, (list, np.ndarray, pd.Series)):
        return str(val)

    if pd.isna(val):
        return None
    
    if isinstance(val, str):
        try:
            return int(val)
        except (ValueError, TypeError):
            try:
                return float(val)
            except (ValueError, TypeError):
                return val.strip()

    return val

def convert_types_for_sql(df):
    """Aplica la función de limpieza a cada celda del DataFrame."""
    return df.map(clean_value)

def _ajustar_tiempos_segments(metadata, segments, dur_real: float):
    """Ajusta los timestamps ESTIMADOS por Gemini a la duración REAL del audio.

    Gemini no mide los tiempos, los estima: en llamadas largas se desvían y a veces el
    `endTime` se pasa del final real. Con la duración real (ffprobe) se corrige el desvío
    proporcional (reescalado por real/estimado, si es material y no absurdo) y se recorta
    todo a [0, dur_real]. No arregla errores no-lineales (segmentos mal ordenados), pero sí
    el caso común de "el reloj del modelo corre rápido/lento" y de "tiempo de más".
    Devuelve (metadata, segments) mutados en el lugar."""
    if not dur_real or dur_real <= 0 or not isinstance(segments, list) or not segments:
        return metadata, segments

    max_end = 0.0
    for s in segments:
        if not isinstance(s, dict):
            continue
        for k in ('startTime', 'endTime'):
            try:
                max_end = max(max_end, float(s.get(k)))
            except (TypeError, ValueError):
                pass
    if max_end <= 0:
        return metadata, segments

    factor = dur_real / max_end
    # Solo reescalar si el desvío es material (>2%) y el factor no es absurdo (audio/estimación
    # coherentes). Si no, se deja el tiempo tal cual y solo se recorta al rango real.
    reescalar = (abs(factor - 1.0) > 0.02) and (0.2 <= factor <= 5.0)

    for s in segments:
        if not isinstance(s, dict):
            continue
        for k in ('startTime', 'endTime'):
            try:
                v = float(s.get(k))
            except (TypeError, ValueError):
                continue
            if reescalar:
                v = v * factor
            v = min(max(v, 0.0), dur_real)
            s[k] = round(v, 3)
        # endTime nunca antes que startTime.
        try:
            if float(s.get('endTime')) < float(s.get('startTime')):
                s['endTime'] = s['startTime']
        except (TypeError, ValueError):
            pass

    if isinstance(metadata, dict):
        metadata['audioDurationSeconds'] = round(dur_real, 3)
    return metadata, segments


def _reescalar_tiempos_transcripcion(df_sql: pd.DataFrame, engine):
    """Corrige, fila por fila, los tiempos de `segments` contra la duración real del audio.

    La duración se saca del audio de la fila (`audio_dir`, path o BytesIO — path sync) o,
    si no está, de calidad.AudioAuditoria (path batch, donde el df ya no trae el audio pero
    el store lo guardó con su duración). Best-effort: si algo falla, se deja la transcripción
    con los tiempos originales del modelo."""
    if 'segments' not in df_sql.columns:
        return
    try:
        from AuditorIA import audio_store
    except Exception:
        return
    try:
        ids = calcular_id_aplicativo(df_sql).astype(str)
    except Exception:
        ids = None

    for i in df_sql.index:
        try:
            segments = df_sql.at[i, 'segments']
            if not isinstance(segments, list) or not segments:
                continue
            dur_real = None
            if 'audio_dir' in df_sql.columns:
                audio_src = df_sql.at[i, 'audio_dir']
                if audio_src is not None:
                    dur_real = audio_store.duracion_segundos(audio_src)
            if not dur_real and ids is not None:
                dur_real = audio_store.duracion_de(engine, ids[i])
            if not dur_real:
                continue
            metadata = df_sql.at[i, 'metadata'] if 'metadata' in df_sql.columns else None
            metadata, segments = _ajustar_tiempos_segments(metadata, segments, dur_real)
            df_sql.at[i, 'segments'] = segments
            if 'metadata' in df_sql.columns:
                df_sql.at[i, 'metadata'] = metadata
        except Exception:
            continue


def guardar_transcripciones_sql(df: pd.DataFrame, engine, user_id: int, modo: str = "sync", modelo: str = "gemini-3.8-flash"):
    """Serializa y guarda las transcripciones de un DataFrame en calidad.transcripciones.

    `modo`/`modelo` se registran en el libro de consumo de IA (pagina_web.IA_Uso). En el
    flujo combinado (calidad+transcripción en un solo llamado) la transcripción ya viene
    con input/thoughts en 0 y sólo su porción de output, así que sumar ambas features NO
    doble-cuenta el costo."""
    df_sql = df.copy()

    # Corregir los timestamps estimados por Gemini contra la duración real del audio,
    # ANTES de serializar los segments a JSON (así queda bien en el reproductor, el modal
    # y las exportaciones). Best-effort.
    _reescalar_tiempos_transcripcion(df_sql, engine)

    columns_to_serialize = ['metadata', 'segments', 'analytics']

    def safe_serialize(x):
        # Si es lista o diccionario, serializamos
        if isinstance(x, (list, dict)):
            return json.dumps(x, ensure_ascii=False)
        # Si es un valor nulo de pandas/numpy o None, retornamos None
        if pd.isna(x) if not isinstance(x, (list, dict)) else False:
            return None
        return None

    for col in columns_to_serialize:
        if col in df_sql.columns:
            df_sql[col] = df_sql[col].apply(safe_serialize)

    # Misma clave que usa la auditoría (fuente única): garantiza que la transcripción
    # quede asociada a SU auditoría por IdAplicativo.
    df_sql['IdAplicativo'] = calcular_id_aplicativo(df_sql)

    base_sql = 'transcripciones'
    columnas_validas_sql = [
        'IdAplicativo',
        'metadata',
        'segments',
        'analytics',
        'input_tokens',
        'output_tokens',
        'thoughts_tokens',
        'user_id'
    ]
    columnas_a_eliminar = ['id','Nro. ODT','Motivo','Observacion','Telefonos','Prestacion','Servicio','Cantidad_turnos','Empleado','es_wav_valido','Campaña','Sentido','FechaSubida','Interrupciones','Hold','Tipificación','Empresa','Campaña','Correo Electronico','email','celular','FechaHora','Observaciones','Telefono','canales','audio_dir','FileName','FilePath','skill','holds','Finalizada_por_operador','destino']
    columnas_existentes_a_eliminar = [col for col in columnas_a_eliminar if col in df_sql.columns]
    if columnas_existentes_a_eliminar:
        df_sql = df_sql.drop(columns=columnas_existentes_a_eliminar)

    df_sql_compatible = convert_types_for_sql(df_sql)
    df_sql_compatible['user_id'] = user_id
    df_sql_compatible = df_sql_compatible.dropna(subset=['metadata'])  # Solo subimos si hay metadata válida

    columnas_existentes = [c for c in columnas_validas_sql if c in df_sql_compatible.columns]
    df_final = df_sql_compatible[columnas_existentes]

    if not df_final.empty:
        df_final.to_sql(base_sql, engine, if_exists='append', index=False, schema='calidad')
        # Registrar el consumo de Gemini de cada transcripción (best-effort).
        try:
            from app.uso_ia import registrar_uso_ia_bulk
            usos = [{
                "feature": "transcripcion",
                "modelo": modelo,
                "modo": modo,
                "user_id": user_id,
                "input_tokens": getattr(fila, "input_tokens", None),
                "output_tokens": getattr(fila, "output_tokens", None),
                "thoughts_tokens": getattr(fila, "thoughts_tokens", None),
                "ref_id": f"transcripcion:{getattr(fila, 'IdAplicativo', None)}",
            } for fila in df_final.itertuples(index=False) if getattr(fila, "IdAplicativo", None) is not None]
            if usos:
                registrar_uso_ia_bulk(usos)
        except Exception:
            logging.exception("No se pudo registrar el consumo de IA de las transcripciones.")
    else:
        print("No hay datos válidos para subir a SQL.")

    return df


def ids_con_transcripcion(engine, ids) -> set:
    """Devuelve el subconjunto de IdAplicativo que YA tienen transcripción guardada en
    calidad.transcripciones.

    Se usa para NO re-transcribir un llamado ya transcripto: como es el mismo audio, la
    transcripción no cambia y volver a hacerla es gasto inútil. Consulta por lotes para
    no superar el tope de parámetros de SQL Server.
    """
    ids_unicos = list(dict.fromkeys(
        str(i) for i in ids if i is not None and not (isinstance(i, float) and pd.isna(i))
    ))
    existentes: set = set()
    if not ids_unicos:
        return existentes

    for inicio in range(0, len(ids_unicos), 1000):
        lote = ids_unicos[inicio:inicio + 1000]
        placeholders = ", ".join(["?"] * len(lote))
        query = f"""
            SELECT DISTINCT [IdAplicativo]
            FROM [calidad].[transcripciones]
            WHERE [IdAplicativo] IN ({placeholders})
        """
        df_ex = pd.read_sql(query, engine, params=tuple(lote))
        if not df_ex.empty:
            existentes.update(df_ex['IdAplicativo'].astype(str).tolist())
    return existentes


# --------------------------------------------------------------------------- #
# Chats: no se transcriben (ya son texto), pero sí se guardan para poder leerlos #
# --------------------------------------------------------------------------- #
# Cómo se muestra cada rol del chat en el modal de "Auditorías Realizadas".
# `role` es lo que usa el front para elegir el estilo de la burbuja.
_ROLES_CHAT = {
    "OPERADOR": ("agente", "Agente"),
    "CLIENTE": ("cliente", "Cliente"),
    "BOT_IVR": ("bot", "Bot / IVR"),
    "INDETERMINADO": ("indeterminado", "Sin identificar"),
}


def _segmento_de_sistema(texto: str) -> dict:
    """Nota al medio de la conversación (transferencias, cierres, separadores)."""
    return {"role": "sistema", "speakerLabel": "Sistema", "hora": "", "text": [texto]}


def chat_a_segments(chat_json: dict) -> tuple:
    """Convierte el JSON del chat auditado al formato `segments` de las transcripciones.

    Se reusa el mismo formato que devuelve Gemini para los llamados para que el chat se
    lea en el mismo modal, sin tocar el SP ni el endpoint: cada mensaje es un segmento
    con su `speakerLabel` y su texto. Se incluyen también los chats previos del cliente
    que la IA tuvo como contexto (separados por una nota), porque parte de la evaluación
    depende de ellos —si el paciente ya había escrito, no corresponde la bienvenida
    institucional— y el auditor tiene que poder ver lo mismo que vio la IA.
    """
    gestion = chat_json.get("gestion_a_auditar") or {}
    operador_auditado = gestion.get("operador_a_auditar") or ""
    segments = []

    for conversacion in chat_json.get("conversaciones", []):
        inicio = conversacion.get("inicio") or ""
        cliente = conversacion.get("cliente") or ""
        if conversacion.get("es_la_conversacion_a_auditar"):
            encabezado = f"CHAT AUDITADO — {inicio}" + (f" — cliente: {cliente}" if cliente else "")
            if operador_auditado:
                encabezado += f" — se audita a {operador_auditado}"
            if gestion.get("tipificacion"):
                encabezado += f" (tipificación: {gestion['tipificacion']})"
        else:
            encabezado = f"CHAT PREVIO DEL MISMO CLIENTE — {inicio} (contexto, no auditado)"
        segments.append(_segmento_de_sistema(encabezado))

        for mensaje in conversacion.get("transcripcion", []):
            if mensaje.get("tipo") != "mensaje":
                segments.append(_segmento_de_sistema(mensaje.get("texto", "")))
                continue

            role, etiqueta = _ROLES_CHAT.get(mensaje.get("quien", ""), ("indeterminado", "Sin identificar"))
            nombre = mensaje.get("nombre") or ""
            if mensaje.get("es_operador_auditado"):
                etiqueta = "Agente auditado"
            segments.append({
                "role": role,
                "speakerLabel": f"{etiqueta} ({nombre})" if nombre else etiqueta,
                "hora": mensaje.get("hora", ""),
                "text": [mensaje.get("texto", "")],
            })

    metadata = {
        "origen": "chat",
        "languageCode": "es-AR",
        "overallConfidence": 1.0,       # es el texto real del chat, no una transcripción
        "audioDurationSeconds": 0.0,    # no hay audio
        "gestion_a_auditar": gestion,
        "conversaciones_incluidas": len(chat_json.get("conversaciones", [])),
        "dias_de_historial": (chat_json.get("ventana_historial") or {}).get("dias_hacia_atras"),
    }
    return metadata, segments


def guardar_chats_como_transcripcion(engine, df, user_id=None) -> int:
    """Deja legible en "Auditorías Realizadas" el chat de cada fila que sea un chat.

    Los chats no se transcriben —ya son texto— así que nunca llegaban a
    calidad.transcripciones y no había forma de leerlos desde la plataforma. Acá se
    guarda la conversación tal cual, con el mismo formato que una transcripción, para
    que el modal de detalle la muestre igual que la de un llamado. No consume tokens:
    los contadores van en 0 y no se registra uso de IA.

    Se llama desde los mismos puntos que conservan el audio (path sync y batch), donde
    el df todavía tiene `audio_dir`, que para los chats es la ruta del JSON descargado.
    """
    if df is None or getattr(df, "empty", True) or 'audio_dir' not in df.columns:
        return 0

    ids = calcular_id_aplicativo(df)
    pendientes = {}
    for idx in df.index:
        ruta = df.at[idx, 'audio_dir']
        if not isinstance(ruta, str) or not ruta.endswith('.json') or not os.path.exists(ruta):
            continue
        id_aplicativo = ids.get(idx) if hasattr(ids, "get") else ids[idx]
        if id_aplicativo is None or (isinstance(id_aplicativo, float) and pd.isna(id_aplicativo)):
            continue
        try:
            with open(ruta, 'r', encoding='utf-8') as f:
                chat_json = json.load(f)
        except (OSError, ValueError) as e:
            logging.warning("No se pudo leer el chat %s: %s", ruta, e)
            continue
        if not isinstance(chat_json, dict) or not chat_json.get("conversaciones"):
            continue
        pendientes[str(id_aplicativo)] = chat_a_segments(chat_json)

    if not pendientes:
        return 0

    # El chat ya guardado no se duplica (una re-auditoría reusa el que está).
    ya_guardados = ids_con_transcripcion(engine, list(pendientes.keys()))
    filas = [
        {
            'IdAplicativo': id_aplicativo,
            'metadata': json.dumps(metadata, ensure_ascii=False),
            'segments': json.dumps(segments, ensure_ascii=False),
            'analytics': None,
            'input_tokens': 0,
            'output_tokens': 0,
            'thoughts_tokens': 0,
            'user_id': user_id,
        }
        for id_aplicativo, (metadata, segments) in pendientes.items()
        if id_aplicativo not in ya_guardados
    ]
    if not filas:
        return 0

    pd.DataFrame(filas).to_sql(
        'transcripciones', engine, if_exists='append', index=False, schema='calidad'
    )
    return len(filas)
