import os
import random
import re
import json
import time
import pandas as pd
import threading # Importamos threading para el semáforo
import subprocess
import io

from typing import Optional, Dict, Any, Tuple
from google import genai
from sqlalchemy import engine, text

from google.genai import types
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.managers import plantillas_manager_instance
from AuditorIA.gemini_files import preparar_contenido, audio_parte_activa
from AuditorIA.Trancribir import prompt as _transcripcion_prompt, ids_con_transcripcion
from AuditorIA.sql_a_Claude import calcular_id_aplicativo
from AuditorIA.execution_log import calcular_desglose_muestreo
from AuditorIA import incidencias as inc
from AuditorIA import limites_texto
from AuditorIA.modelos_ia import MODELO_IA_DEFAULT
from AuditorIA import razonamiento
from AuditorIA.versionado import obtener_o_crear_version
from AuditorIA import batch_cola
from AuditorIA import cache_plantillas
from AuditorIA import conocimiento_plantilla
from AuditorIA.batch_cola import PREFIJO_PENDIENTE
from app.config import settings

if not plantillas_manager_instance:
    raise Exception("El plantillas_manager_instance no está inicializado correctamente.")

def lista_a_string(row):
    if isinstance(row, list):
        return '\n'.join(row)
    return str(row)


def valor_para_batch_data(valor):
    """Normaliza una celda del DataFrame antes de persistirla en calidad.Batch_data.

    Batch_data.valor es varchar: es la metadata que ata cada segment_id de la respuesta
    de Gemini a su interacción horas después. Todo lo que no sea texto lo convierte el
    driver/SQL Server, y ahí hay dos trampas:

      * Audio en memoria (BytesIO/bytes: Vitalis Salud y Vantix/Orion vía CYT): no es
        serializable como parámetro SQL y, aunque lo fuera, el buffer no sobrevive hasta
        que el batch termina. Se persiste NULL.
      * NaN/NaT: tras el melt las celdas vacías quedan como NaN (float). SQL Server lo
        rechaza como parámetro de una columna de texto y rompe el stream TDS (p. ej.
        IdClinica sin valor en una fila de Dental). Se persiste NULL.
      * float: SQL Server lo convierte a varchar en formato CIENTÍFICO y con 6 dígitos
        significativos ('1.05276e+007'), o sea que un id entero de 8 dígitos vuelve
        MUTILADO. Le pasa a cualquier columna entera que pandas haya promovido a float64
        por tener algún NaN en la muestra: el IdTurno de Dental (turnero con OUTER APPLY)
        rompió así 13 corridas: `int('1.05276e+007')` tiraba ValueError al procesar el
        lote y no quedaba ninguna auditoría. Se serializa acá, en decimal y sin exponente.
    """
    if isinstance(valor, (io.BytesIO, bytes, bytearray)):
        return None

    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        # pd.isna sobre valores no escalares (listas/dicts) es ambiguo; en ese caso se
        # deja el valor tal cual.
        return valor

    if isinstance(valor, bool):
        return valor
    if pd.api.types.is_float(valor):
        f = float(valor)
        return str(int(f)) if f.is_integer() else repr(f)
    return valor

# Columnas internas del DataFrame que no son contexto de la interacción (rutas de
# archivo, ids técnicos): no se mandan al modelo. `externalTag` (Benefix) es lo que el
# cliente marcó en el IVR, a veces el número de tarjeta: se guarda en Extras y no viaja
# a Gemini.
COLUMNAS_OMITIDAS_EN_DETALLES = frozenset({
    'FileName', 'FilePath', 'audio_dir', 'campana_id', 'Campaña',
    'idInteraccion', 'Segmento', 'empresa_id', 'externalTag',
})

# Título del bloque de contexto. Se mantiene el nombre histórico (antes era el tag XML
# <Call_details>) porque hay system prompts de plantillas que lo nombran.
TITULO_CALL_DETAILS = "## Call_details"


def _valor_de_detalle(valor) -> str:
    """Normaliza el valor de una columna a texto plano; devuelve '' si está vacío.

    Maneja pd.NA / np.nan / None y también listas (que llegan de algunos builders).
    """
    if valor is None:
        return ""
    if isinstance(valor, (list, tuple, set)):
        valor = "\n".join(str(v) for v in valor if v is not None)
    else:
        try:
            if pd.isna(valor):
                return ""
        except (TypeError, ValueError):
            # pd.isna sobre valores no escalares es ambiguo: se toma tal cual.
            pass
    return str(valor).strip()


def prompt_details(row: pd.Series, prompt_text: str) -> str:
    """
    Arma el bloque de contexto de la interacción (metadatos de la llamada/chat) en
    Markdown, para añadirlo al prompt de la plantilla.

    Antes viajaba como XML (`<Call_details><Columna>valor</Columna>...`), lo que
    duplicaba el nombre de cada campo en tokens (apertura + cierre) y obligaba a
    escapar `<`, `>` y `&`. En Markdown cada campo es una línea `- Campo: valor`:
    el modelo lo interpreta igual de bien y cuesta bastante menos.

    Criterios:
    1. Se omiten las columnas internas (`COLUMNAS_OMITIDAS_EN_DETALLES`).
    2. Se omiten los campos vacíos (NA/None/""): un campo sin dato no aporta contexto
       y en XML igual se mandaba como tag vacío.
    3. Los valores multilínea van indentados debajo de la clave (estilo YAML) para
       que no se confundan con el campo siguiente.
    """
    lineas = []

    for nombre_columna, valor_columna in row.items():
        if nombre_columna in COLUMNAS_OMITIDAS_EN_DETALLES:
            continue

        texto = _valor_de_detalle(valor_columna)
        if not texto:
            continue

        if "\n" in texto:
            cuerpo = "\n".join(
                f"  {linea.strip()}" for linea in texto.splitlines() if linea.strip()
            )
            lineas.append(f"- {nombre_columna}:\n{cuerpo}")
        else:
            lineas.append(f"- {nombre_columna}: {texto}")

    if not lineas:
        lineas.append("- (sin datos de contexto)")

    details_string = TITULO_CALL_DETAILS + "\n" + "\n".join(lineas)

    return prompt_text + "\n\n" + details_string if prompt_text else details_string

def instruccion_atributos_opcionales(nombres: list) -> str:
    """Bloque que se agrega al prompt para los atributos marcados como opcionales.

    Sacarlos del `required` del esquema alcanza para que la API acepte la respuesta
    sin ellos, pero el modelo tiende igual a completar todo. Este texto le dice
    explícitamente que omitir es una salida válida y preferible a inventar: es
    justamente el caso que el atributo opcional viene a resolver (un Si/No que la
    interacción no permite responder terminaba respondido igual, y castigaba al
    operador por algo que nunca pasó).
    """
    lista = ", ".join(f"'{n}'" for n in nombres)
    return (
        "\n\n--- ATRIBUTOS OPCIONALES ---\n"
        f"Estos atributos son OPCIONALES: {lista}.\n"
        "Si la interacción no permite responderlos con evidencia (el criterio no aplica "
        "a este llamado, se cortó antes de llegar a esa instancia, o el tema nunca se "
        "tocó), OMITÍ por completo esa clave del JSON en lugar de elegir una respuesta "
        "para cumplir con el formato. Un atributo omitido queda sin auditar: no puntúa "
        "ni penaliza al operador. Solo respondelos cuando tengas evidencia real. "
        "El resto de los atributos sigue siendo obligatorio."
    )


# Clave del campo de incidencia en el JSON de respuesta. No lleva prefijo Detalle_ ni
# entra en nombre_id_map: se saca de la respuesta antes de mapear atributos.
CAMPO_INCIDENCIA = 'Incidencia'

INSTRUCCION_INCIDENCIA = (
    "\n\n--- INCIDENCIAS DEL AUDIO ---\n"
    "El campo 'Incidencia' es OBLIGATORIO y sirve para avisar que este llamado no se "
    "puede auditar de forma confiable. Valores posibles:\n"
    "- 'ninguna': el audio se escucha y la auditoría es válida. Es el caso normal.\n"
    "- 'audio_mudo': el audio está en silencio, es inaudible o no contiene una "
    "conversación. IMPORTANTE: en ese caso NO reconstruyas el llamado a partir de los "
    "datos que te pasamos (nombre del agente, motivo, documento del cliente). Esos datos "
    "son contexto del sistema, NO son lo que se dijo en el audio. Inventar una "
    "conversación verosímil es el peor error posible: una auditoría falsa que nadie "
    "puede distinguir de una real.\n"
    "- 'audio_incompleto': el audio está cortado, empieza empezado o termina de golpe, "
    "y falta parte de la conversación.\n"
    "- 'operador_no_coincide': el agente que se escucha NO es el que figura en los datos "
    "del llamado (por ejemplo, se presenta con otro nombre).\n"
    "Ante cualquiera de las tres últimas, informala igual aunque hayas podido completar "
    "algunos atributos: preferimos una auditoría marcada para revisión que una auditoría "
    "equivocada dada por buena."
)


def prompt(plantilla_id:int, *, versionar: bool = True) -> Dict[str, Any]:
    """Todo lo que define cómo audita una plantilla, resuelto UNA vez por corrida.

    `versionar=False` no registra la versión (queda en None): lo usa el script que
    prueba una selección de conocimiento sin guardarla, que no escribe en la base.
    """
    plantilla = plantillas_manager_instance.obtener_plantilla_para_IA(plantilla_id=plantilla_id)
    if not plantilla:
        raise ValueError(f"No se encontró la plantilla con ID {plantilla_id}")

    prompt_text = plantilla.text
    system_instruction = plantilla.system_prompts
    # El response_schema viene como un string JSON, necesitamos parsearlo.
    response_schema_data = json.loads(plantilla.response_schema)

    required_fields = []
    atributos_opcionales = []
    atributos_texto_libre = []
    properties_dict = {}
    nombre_id_map = {}
    # Tope de los atributos de texto libre (ver AuditorIA/limites_texto.py). 0 = sin tope.
    # Va al `max_length` del esquema para que el modelo NO gaste la salida escribiendo un
    # llamado transcripto adentro de un atributo.
    tope_texto = limites_texto.max_caracteres()

    for atributo in response_schema_data:
        atributo_nombre = atributo['name']
        atributo_tipo = atributo['type']
        atributo_id = atributo['id']
        # El JSON de entrada usa 'constraints', no 'restrictions'
        atributo_constraints = atributo.get('constraints', {})
        # 'optional' lo agrega sp_ObtenerPlantillaParaIA (migración 2026-08-05b). Si la
        # migración no está aplicada la clave no viene y todo queda obligatorio, que es
        # la conducta previa.
        es_opcional = bool(atributo.get('optional'))

        nombre_id_map[atributo_nombre] = atributo_id

        match atributo_tipo.lower():
            case 'string':
                tipo = types.Schema(type=types.Type.STRING, max_length=tope_texto or None)
                atributos_texto_libre.append(atributo_nombre)
            case 'integer':
                tipo = types.Schema(type=types.Type.INTEGER)
            case 'number':
                tipo = types.Schema(type=types.Type.NUMBER)
            case 'boolean':
                tipo = types.Schema(type=types.Type.BOOLEAN)
            case 'enum':
                atributo_constraints = atributo_constraints.get('enum', [])
                if not isinstance(atributo_constraints, list):
                    atributo_constraints = [atributo_constraints]
                tipo = types.Schema(type=types.Type.STRING, enum=atributo_constraints)
            case 'critical_audit':
                # Atributo ponderado para puntaje 0-100. Enum fijo OK / NO OK / EC.
                # Si la plantilla define sus propias opciones se respetan; si no,
                # se usan las tres canónicas (ver AuditorIA/scoring.py).
                opciones = atributo_constraints.get('enum') if isinstance(atributo_constraints, dict) else None
                if not opciones:
                    opciones = ['OK', 'NO OK', 'EC']
                tipo = types.Schema(type=types.Type.STRING, enum=opciones)
            case 'array_string':
                # El tope aplica a CADA elemento de la lista, igual que al guardar.
                tipo = types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(type=types.Type.STRING, max_length=tope_texto or None),
                )
                atributos_texto_libre.append(atributo_nombre)
            case 'array_integer':
                tipo = types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.INTEGER))
            case 'array_number':
                tipo = types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.NUMBER))
            case 'array_boolean':
                tipo = types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.BOOLEAN))
            case 'array_enum':
                atributo_constraints = atributo_constraints.get('enum', [])
                if not isinstance(atributo_constraints, list):
                    atributo_constraints = [atributo_constraints]
                tipo = types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING, enum=atributo_constraints))
            case _:
                raise ValueError(f"Tipo de atributo desconocido: {atributo_tipo}")
        properties_dict[atributo_nombre] = tipo
        # Los opcionales quedan en `properties` (la IA puede responderlos) pero fuera de
        # `required`: si no hay evidencia, el campo no viene y el atributo queda sin
        # auditar (ver sql_a_Claude.auditoria_a_SQL, que no guarda detalle sin valor).
        if es_opcional:
            atributos_opcionales.append(atributo_nombre)
        else:
            required_fields.append(atributo_nombre)

    if atributos_opcionales:
        prompt_text = (prompt_text or "") + instruccion_atributos_opcionales(atributos_opcionales)

    # Texto libre: el max_length del esquema es el tope duro, pero además hay que decirle
    # al modelo qué se espera (una respuesta breve) y que la transcripción NO va acá.
    # Hay plantillas cuyos atributos están redactados como pedido de transcripción y esta
    # instrucción es la que las neutraliza sin tener que reescribirlas una por una.
    if atributos_texto_libre:
        prompt_text = (prompt_text or "") + limites_texto.instruccion_texto_breve(atributos_texto_libre)

    # Campo fijo de INCIDENCIA (ver AuditorIA/incidencias.py). No es un atributo de la
    # plantilla: es la vía para que el modelo diga "no puedo auditar esto" en vez de
    # completar igual. Se agrega al schema de todas las plantillas.
    prompt_text = (prompt_text or "") + INSTRUCCION_INCIDENCIA
    properties_dict[CAMPO_INCIDENCIA] = types.Schema(
        type=types.Type.STRING, enum=list(inc.DECLARABLES_POR_IA)
    )
    required_fields = list(required_fields) + [CAMPO_INCIDENCIA]

    response_schema = types.Schema(
                type=types.Type.OBJECT,
                required=required_fields,
                properties=properties_dict
            )

    # Versión de la plantilla con la que se va a auditar (Golden Set — Fase 2).
    # Se resuelve acá, UNA vez por corrida, y no al guardar: acá es donde el prompt
    # queda congelado, así que es el momento exacto que hay que trazar. Si la
    # migración no está aplicada devuelve None y todo sigue igual, sin versionar.
    # (El engine sale del manager para no chocar con el `engine` de sqlalchemy que
    # este módulo importa arriba como tipo.)
    return {
        'text': prompt_text,
        'system': system_instruction,
        'response_schema': response_schema,
        'nombre_id_map': nombre_id_map,
        'modelo': plantilla.modelo,
        # Cuánto piensa Gemini antes de responder (ver AuditorIA/razonamiento.py).
        # Si la plantilla no lo tiene seteado —o la migración todavía no se aplicó—
        # `normalizar` cae al default y la auditoría sigue igual.
        'nivel_razonamiento': razonamiento.normalizar(
            getattr(plantilla, 'nivel_razonamiento', None)
        ),
        # Documentos de los chatbots que la plantilla eligió como conocimiento de
        # referencia (ver AuditorIA/conocimiento_plantilla.py). "" si no eligió
        # ninguno: la instrucción de sistema queda igual que antes.
        'conocimiento': conocimiento_plantilla.bloque_para_el_prompt(
            conocimiento_plantilla.documentos_para_auditar(plantillas_manager_instance.engine, plantilla_id)
        ),
        'version_id': (
            obtener_o_crear_version(plantillas_manager_instance.engine, plantilla_id)
            if versionar else None
        ),
    }

INSTRUCCION_TRANSCRIPCION = (
    "\n\n--- TRANSCRIPCIÓN ADICIONAL ---\n"
    "Además de la auditoría de calidad, completá el campo 'Transcripcion' del JSON con la "
    "transcripción del audio según su esquema: 'metadata' (overallConfidence, languageCode, "
    "audioDurationSeconds); 'segments' (lista de turnos, cada uno con speakerLabel 'Agente' o "
    "'Cliente', startTime, endTime, confidence y 'text' como lista de palabras); y 'analytics' "
    "(keywords y entities). Transcribí el audio COMPLETO de principio a fin, sin resumir ni omitir "
    "turnos. No pongas saltos de línea ni espacios innecesarios dentro de los textos (es solo formato, "
    "no recortes lo que se dice), y no inventes nada que no esté en el audio."
)


def instruccion_de_sistema(prompt_info: Dict[str, Any]) -> str:
    """La instrucción de sistema completa de una plantilla.

    Vive separada de `obtener_configuracion_gemini` porque es, junto con
    `prompt_info['text']`, lo que se sube a la caché de contexto: las dos vías
    tienen que armar exactamente el mismo texto o la caché no se reconoce y se
    paga todo dos veces (ver AuditorIA/cache_plantillas.py).

    No depende de si el llamado pide transcripción: esa consigna va en el turno
    del usuario (INSTRUCCION_TRANSCRIPCION), justamente para que la instrucción de
    sistema sea idéntica en los dos casos y una sola caché sirva para toda la
    corrida, transcriba o no.
    """
    return (
        (prompt_info.get('system') or "") +
        "\n\n--- INSTRUCCIONES ESTRICTAS DE AUDITORÍA Y RAZONAMIENTO ---\n"
        "Eres un auditor de calidad experto, objetivo y altamente analítico. Tu tarea es evaluar exhaustivamente "
        "toda la información proporcionada (el registro de la interacción, ya sea audio o historial de chat, y los detalles de contexto).\n"
        "1. Analiza activamente: Presta atención a las palabras exactas, las resoluciones y el contexto general de la interacción.\n"
        "2. Razona paso a paso: Antes de generar la respuesta JSON, estructura tu pensamiento, "
        "evalúa la evidencia encontrada en la interacción y justifica tu decisión internamente.\n"
        "3. Cero alucinaciones: Básate ÚNICAMENTE en los hechos observables en la interacción. Si un dato no se menciona o no está claro, no asumas su existencia.\n"
        "4. Precisión de formato: Tu salida final debe cumplir estrictamente con el esquema JSON definido."
        # El conocimiento de referencia va al final y solo si la plantilla eligió
        # documentos: así las plantillas sin conocimiento conservan su caché intacta.
        + (prompt_info.get('conocimiento') or "")
    )


def obtener_configuracion_gemini(
    prompt_info: Dict[str, Any],
    temperatura: float = 0.8,
    incluir_transcripcion: bool = False,
    cache_contexto: Optional[str] = None,
) -> types.GenerateContentConfig:
    """
    Genera la configuración unificada y mejorada para Gemini, asegurando que tanto
    las peticiones individuales como los batches usen exactamente las mismas reglas.

    Si `incluir_transcripcion` es True, agrega al esquema de respuesta un campo
    'Transcripcion' anidado para obtener calidad + transcripción en UN solo llamado.

    `cache_contexto` es el nombre de la caché que ya tiene la instrucción de sistema
    y el texto de la plantilla (ver AuditorIA/cache_plantillas.py). Cuando viene, la
    instrucción NO se repite en el request: está adentro de la caché, y mandarla dos
    veces además de pagarla dos veces es un error de la API.
    """
    response_schema = prompt_info['response_schema']
    if incluir_transcripcion:
        # Combinamos el esquema de calidad con el de transcripción (campo anidado),
        # para resolver ambas tareas en un único llamado (una sola lectura del audio).
        propiedades = dict(response_schema.properties or {})
        propiedades['Transcripcion'] = _transcripcion_prompt['response_schema']
        requeridos = list(response_schema.required or []) + ['Transcripcion']
        response_schema = types.Schema(
            type=types.Type.OBJECT,
            required=requeridos,
            properties=propiedades,
        )

    return types.GenerateContentConfig(
        temperature=temperatura,
        max_output_tokens=65535,
        response_mime_type="application/json",
        response_schema=response_schema,
        cached_content=cache_contexto,
        system_instruction=(
            None if cache_contexto else
            [types.Part.from_text(text=instruccion_de_sistema(prompt_info))]
        ),
        # stop_sequences para cortar los loops de repetición (Gemini repitiendo una
        # frase hasta MAX_TOKENS = minutos perdidos). Este modelo NO soporta
        # frequency_penalty, así que esta es la única vía.
        # OJO: quitamos "..." (puntos suspensivos) porque es comunísimo en
        # transcripciones/feedback y cortaba el JSON a mitad ("Unterminated string").
        # Los dobles de palabra casi no aparecen en el JSON legítimo (las palabras de
        # la transcripción van en elementos separados del array: "dice","dice", no
        # "dice dice") pero sí atajan el loop en los campos de texto libre.
        stop_sequences=[
            "dice dice",
            "cliente cliente",
            "uhm uhm uhm",
        ],
        # Cuánto piensa el modelo antes de responder: lo decide la PLANTILLA
        # (ver AuditorIA/razonamiento.py). Hasta el 2026-08-19 acá había un
        # `thinking_budget=8192` fijo; se sacó porque en Gemini 3.x el budget no
        # se respeta —medido: con budget=512 el modelo gastó 1.150 tokens de
        # pensamiento— y esa falsa sensación de tope fue lo que dejó pasar el
        # salto de ~2.000 a ~13.000 tokens de razonamiento por auditoría.
        thinking_config=razonamiento.thinking_config(prompt_info.get('nivel_razonamiento'))
    )

def texto_del_turno(
    row,
    prompt_info: Dict[str, Any],
    *,
    cacheado: bool = False,
    con_transcripcion: bool = False,
) -> str:
    """El texto que acompaña al audio en el turno del usuario.

    Con caché de contexto el texto de la plantilla ya viajó una vez y quedó del lado
    de Gemini: acá van SOLO los datos de esta interacción. Sin caché va todo junto,
    como antes (ver AuditorIA/cache_plantillas.py).
    """
    texto = prompt_details(row, "" if cacheado else prompt_info['text'])
    if con_transcripcion:
        texto += INSTRUCCION_TRANSCRIPCION
    return texto


def _generar_calidad(row, prompt, gemini_api, parte_audio, cache_contexto=None) -> pd.Series:
    """Genera la auditoría de calidad sobre un audio YA preparado (`types.Part`).

    Reintenta solo la GENERACIÓN (no la preparación del audio) variando la temperatura.
    """
    audio_id = row.get('audio_dir')
    for i in range(5):
        temperatura = 0.4 + i * 0.1
        try:
            prompt_with_details = texto_del_turno(row, prompt, cacheado=bool(cache_contexto))
            model = prompt.get('modelo', MODELO_IA_DEFAULT)
            contents = [
                types.Content(
                    role="user",
                    parts=[
                        parte_audio,
                        types.Part.from_text(text=prompt_with_details),
                    ],
                ),
            ]

            generate_content_config = obtener_configuracion_gemini(
                prompt, temperatura=temperatura, cache_contexto=cache_contexto
            )

            response = gemini_api.models.generate_content(
                model=model,
                contents=contents,  # type: ignore
                config=generate_content_config,
            )

            if not response.candidates or not response:
                print(f"WARN: Respuesta bloqueada para {audio_id}. Razón: {response.prompt_feedback}. Reintentando...")
                time.sleep(5)
                continue

            finish_reason = response.candidates[0].finish_reason
            if finish_reason != 'STOP':
                print(f"WARN: Generación detenida prematuramente. Razón: '{finish_reason}'.")
                if finish_reason == 'MAX_TOKENS':
                    print(f"ERROR: Límite de tokens alcanzado para {audio_id}. No se puede procesar.")
                    break
                time.sleep(5)
                continue

            if not response or not response.candidates or not response.candidates[0].content or not response.candidates[0].content.parts:
                print(f"ERROR: Invalid response structure for {audio_id}")
                continue
            return procesar_respuesta(response)

        except Exception as e:
            print('-' * 20)
            print(e)
            print(f"Reintentando {audio_id} (próx. temp {temperatura + 0.1:.1f})")
            print('-' * 20)
            # JSON malformado/truncado: reintento casi inmediato. Otros errores
            # (API/red): backoff más largo.
            time.sleep(1 if isinstance(e, json.JSONDecodeError) else 10)

    print(f"ERROR: Todos los 5 intentos fallaron para el archivo {audio_id}.")
    return procesar_respuesta_error("Fallo tras 5 intentos.")


def calidad_claude(
    row: pd.Series,
    prompt: Dict[str, Any],
    gemini_api: genai.Client,
    semaphore: threading.Semaphore
    ) -> Optional[pd.Series]:
    """Audita una sola fila: sube el audio (una vez), genera y borra. Wrapper fino."""
    _, serie_cal = _procesar_fila_auditoria(
        row, gemini_api, semaphore, prompt, do_transcribir=False
    )
    return serie_cal


def _extraer_texto_respuesta(response) -> Tuple[Optional[str], str]:
    """Devuelve (response_thoughts, response_text) ensamblando TODAS las partes.

    Gemini puede dividir una respuesta larga en varias `parts`; además, con thinking
    activado, algunas partes son de tipo 'thought'. Concatenamos el texto de las
    partes que NO son pensamiento para formar el JSON completo. Leer una sola parte
    por índice (partes[1]) rompía el JSON en respuestas largas (p. ej. calidad +
    transcripción), produciendo errores como "Unterminated string".
    """
    partes = response.candidates[0].content.parts or []
    thought_chunks = []
    answer_chunks = []
    for parte in partes:
        texto = getattr(parte, 'text', None)
        if not texto:
            continue
        if getattr(parte, 'thought', False):
            thought_chunks.append(texto)
        else:
            answer_chunks.append(texto)

    response_thoughts = "\n".join(thought_chunks) if thought_chunks else None
    response_text = "".join(answer_chunks) if answer_chunks else (response.text or "")
    return response_thoughts, response_text


def procesar_respuesta(response) -> pd.Series:
    response_thoughts, response_text = _extraer_texto_respuesta(response)
    # strict=False permite caracteres de control crudos (\n, \t reales sin escapar)
    # dentro de los strings, que Gemini a veces mete en campos de texto libre como
    # FEEDBACK y que con el default (strict=True) rompían con "Invalid control character".
    response_data = json.loads(response_text, strict=False)
    
    input_tokens = response.usage_metadata.prompt_token_count if response.usage_metadata and response.usage_metadata.prompt_token_count else 7000
    output_tokens = response.usage_metadata.candidates_token_count if response.usage_metadata and response.usage_metadata.candidates_token_count else 400
    thoughts_tokens = response.usage_metadata.thoughts_token_count if response.usage_metadata and response.usage_metadata.thoughts_token_count else 0
    # Los tokens que se cobraron a precio de caché (ver AuditorIA/cache_plantillas.py).
    # Van al libro de uso de IA, no a calidad.Auditorias: es donde se mide el ahorro.
    cached_tokens = getattr(response.usage_metadata, 'cached_content_token_count', None) or 0 if response.usage_metadata else 0

    # La incidencia no es un atributo de la plantilla: se saca ANTES de prefijar
    # Detalle_, para que no busque un AtributoID que no existe.
    incidencia = inc.limpiar_valor(response_data.pop(CAMPO_INCIDENCIA, None))

    df = pd.DataFrame([response_data])
    
    returned_list = [df[columna].iloc[0] for columna in df.columns] 
    returned_list.append(input_tokens)
    returned_list.append(output_tokens)
    returned_list.append(thoughts_tokens)
    returned_list.append(cached_tokens)
    returned_list.append(response_thoughts)
    returned_list.append(incidencia)

    # Mapear los nombres de los atributos a sus IDs
    columnas_finales = list(df.columns)

    #Agrega la palabra Detalle_ antes de cada columna
    columnas_finales = ['Detalle_' + str(columna) for columna in columnas_finales]

    columnas_finales.extend(['input_tokens', 'output_tokens','thoughts_tokens', 'cached_tokens',
                             'response_thoughts', inc.COLUMNA_IA])
    return pd.Series(returned_list, index=columnas_finales)

def procesar_respuesta_error(error_msg: str) -> pd.Series:
    """
    Genera una serie con la estructura esperada pero indicando error.
    Útil para no romper el DataFrame final.
    """
    # Como no sabemos las columnas exactas de la plantilla hasta runtime,
    # retornamos una serie con al menos una columna indicadora de error.
    # Al hacer pd.concat/join, las columnas que falten se llenarán con NaN.
    return pd.Series({
        'status_auditoria': 'FALLIDO',
        'error_detalle': error_msg,
        'input_tokens': 0,
        'output_tokens': 0,
        'thoughts_tokens': 0,
        'cached_tokens': 0,
        'response_thoughts': None
    })

def procesar_respuesta_combinada(response) -> Tuple[pd.Series, Optional[pd.Series]]:
    """Parsea una respuesta que trae la auditoría de calidad y, anidada en el campo
    'Transcripcion', la transcripción del audio.

    Devuelve (serie_calidad, serie_transcripcion | None). El costo (tokens) del único
    llamado se atribuye a la fila de calidad; la fila de transcripción se guarda con 0
    tokens porque NO hubo un llamado aparte.
    """
    response_thoughts, response_text = _extraer_texto_respuesta(response)
    # strict=False: ver nota en procesar_respuesta (acepta \n/\t crudos en strings).
    response_data = json.loads(response_text, strict=False)

    input_tokens = response.usage_metadata.prompt_token_count if response.usage_metadata and response.usage_metadata.prompt_token_count else 7000
    output_tokens = response.usage_metadata.candidates_token_count if response.usage_metadata and response.usage_metadata.candidates_token_count else 400
    thoughts_tokens = response.usage_metadata.thoughts_token_count if response.usage_metadata and response.usage_metadata.thoughts_token_count else 0
    # Los tokens que se cobraron a precio de caché (ver AuditorIA/cache_plantillas.py).
    # Van al libro de uso de IA, no a calidad.Auditorias: es donde se mide el ahorro.
    cached_tokens = getattr(response.usage_metadata, 'cached_content_token_count', None) or 0 if response.usage_metadata else 0

    # Extraer (y quitar) la transcripción y la incidencia para que NO entren como
    # atributos de calidad (ninguna de las dos tiene AtributoID).
    transcripcion = response_data.pop('Transcripcion', None)
    incidencia = inc.limpiar_valor(response_data.pop(CAMPO_INCIDENCIA, None))
    serie_transcripcion = None
    output_tokens_transcripcion = 0
    if isinstance(transcripcion, dict):
        # Repartimos output_tokens en forma aproximada según el tamaño del JSON de
        # cada parte (la API no da el desglose por campo). input/thoughts quedan en
        # calidad porque corresponden al audio y al razonamiento de la auditoría.
        len_trans = len(json.dumps(transcripcion, ensure_ascii=False))
        len_calidad = len(json.dumps(response_data, ensure_ascii=False))
        total = len_trans + len_calidad
        output_tokens_transcripcion = round(output_tokens * len_trans / total) if total > 0 else 0
        serie_transcripcion = pd.Series({
            'metadata': transcripcion.get('metadata'),
            'segments': transcripcion.get('segments'),
            'analytics': transcripcion.get('analytics'),
            'input_tokens': 0,
            'output_tokens': output_tokens_transcripcion,
            'thoughts_tokens': 0,
        })

    # Calidad: el resto de las claves, con el prefijo Detalle_. Le descontamos al
    # output la porción atribuida a la transcripción.
    output_tokens_calidad = output_tokens - output_tokens_transcripcion
    df = pd.DataFrame([response_data])
    returned_list = [df[columna].iloc[0] for columna in df.columns]
    returned_list.append(input_tokens)
    returned_list.append(output_tokens_calidad)
    returned_list.append(thoughts_tokens)
    returned_list.append(cached_tokens)
    returned_list.append(response_thoughts)
    returned_list.append(incidencia)
    columnas_finales = ['Detalle_' + str(columna) for columna in df.columns]
    columnas_finales.extend(['input_tokens', 'output_tokens', 'thoughts_tokens', 'cached_tokens',
                             'response_thoughts', inc.COLUMNA_IA])
    serie_calidad = pd.Series(returned_list, index=columnas_finales)

    return serie_calidad, serie_transcripcion


def _generar_calidad_y_transcripcion(row, prompt_info, gemini_api, parte_audio, cache_contexto=None) -> Tuple[pd.Series, Optional[pd.Series]]:
    """UN solo llamado a Gemini que produce la auditoría de calidad Y la transcripción
    sobre el mismo audio (una única lectura). Reintenta solo la generación.

    Devuelve (serie_calidad, serie_transcripcion | None).
    """
    audio_id = row.get('audio_dir')
    for i in range(5):
        temperatura = 0.4 + i * 0.1
        try:
            prompt_with_details = texto_del_turno(
                row, prompt_info, cacheado=bool(cache_contexto), con_transcripcion=True
            )
            model = prompt_info.get('modelo', MODELO_IA_DEFAULT)
            contents = [
                types.Content(
                    role="user",
                    parts=[
                        parte_audio,
                        types.Part.from_text(text=prompt_with_details),
                    ],
                ),
            ]

            generate_content_config = obtener_configuracion_gemini(
                prompt_info, temperatura=temperatura, incluir_transcripcion=True,
                cache_contexto=cache_contexto,
            )

            response = gemini_api.models.generate_content(
                model=model,
                contents=contents,  # type: ignore
                config=generate_content_config,
            )

            if not response.candidates or not response:
                print(f"WARN: Respuesta bloqueada para {audio_id}. Razón: {response.prompt_feedback}. Reintentando...")
                time.sleep(5)
                continue

            finish_reason = response.candidates[0].finish_reason
            if finish_reason != 'STOP':
                print(f"WARN: Generación detenida prematuramente. Razón: '{finish_reason}'.")
                if finish_reason == 'MAX_TOKENS':
                    print(f"ERROR: Límite de tokens alcanzado para {audio_id}. No se puede procesar.")
                    break
                time.sleep(5)
                continue

            if not response or not response.candidates or not response.candidates[0].content or not response.candidates[0].content.parts:
                print(f"ERROR: Invalid response structure for {audio_id}")
                continue
            return procesar_respuesta_combinada(response)

        except Exception as e:
            print('-' * 20)
            print(e)
            print(f"Reintentando {audio_id} (próx. temp {temperatura + 0.1:.1f})")
            print('-' * 20)
            # JSON malformado/truncado: reintento casi inmediato. Otros errores
            # (API/red): backoff más largo.
            time.sleep(1 if isinstance(e, json.JSONDecodeError) else 10)

    print(f"ERROR: Todos los 5 intentos (calidad+transcripción) fallaron para {audio_id}.")
    return procesar_respuesta_error("Fallo tras 5 intentos."), None


def _procesar_fila_auditoria(row, gemini_api, semaphore, prompt_info, do_transcribir, cache_contexto=None):
    """Sube el audio de UNA fila una sola vez y, sobre el mismo archivo, obtiene la
    auditoría de calidad y —si se pide y hay audio real— la transcripción, borrándolo
    al final.

    La calidad SIEMPRE se ejecuta; la transcripción es opcional. Cuando se piden ambas
    se hace UN solo llamado a Gemini (una sola lectura del audio). Los chats (.json) no
    se transcriben.

    Devuelve (serie_transcripcion | None, serie_calidad).
    """
    audio_dir = row.get('audio_dir')
    es_chat = isinstance(audio_dir, str) and audio_dir.endswith('.json')
    quiere_transcribir = do_transcribir and not es_chat

    # Sin audio: la calidad devuelve error formateado; no hay transcripción.
    if not audio_dir or (not isinstance(audio_dir, io.BytesIO) and pd.isna(audio_dir)):
        print("⚠️ Fila sin audio disponible.")
        return None, procesar_respuesta_error("Audio no disponible o descarga fallida.")

    with semaphore:
        print(f"Semáforo adquirido para {audio_dir}. Iniciando proceso...")
        serie_trans = None
        serie_cal = None
        try:
            with audio_parte_activa(gemini_api, audio_dir) as parte_audio:
                if parte_audio is None:
                    return None, procesar_respuesta_error("Fallo al preparar el audio para Gemini.")

                if quiere_transcribir:
                    # UN solo llamado: calidad + transcripción sobre la misma lectura.
                    serie_cal, serie_trans = _generar_calidad_y_transcripcion(
                        row, prompt_info, gemini_api, parte_audio, cache_contexto
                    )
                else:
                    serie_cal = _generar_calidad(
                        row, prompt_info, gemini_api, parte_audio, cache_contexto
                    )
        except Exception as e:
            print(f"Error procesando la fila ({audio_dir}): {e}")
            if serie_cal is None:
                serie_cal = procesar_respuesta_error(f"Excepción: {e}")
        return serie_trans, serie_cal

def apply_auditoria_threads(
    df: pd.DataFrame,
    gemini_api,
    plantilla_id: int,
    *,
    transcribir: bool = False,
    engine=None,
    max_workers: int = 10,
    concurrent_api_calls: int = 5,
    prompt_info: Optional[Dict[str, Any]] = None,
) -> Tuple[pd.DataFrame, Optional[pd.DataFrame], Dict[str, str], str]:
    """Pasada unificada: sube cada audio UNA sola vez y, sobre ese mismo archivo,
    ejecuta SIEMPRE la auditoría de calidad y —si `transcribir`— también la
    transcripción (en el mismo llamado, una sola lectura del audio).

    Si `transcribir` y se pasa `engine`, NO se re-transcriben los llamados que ya
    tienen transcripción (mismo audio = misma transcripción): esas filas van con la
    llamada calidad-sola y conservan su transcripción previa. Solo se transcriben los
    llamados sin transcripción previa.

    Devuelve (df_calidad, df_transcripciones, nombre_id_map, modelo):
      - df_calidad: df original unido con las columnas de calidad.
      - df_transcripciones: df con columnas de transcripción (o None si no aplica).
      - nombre_id_map: mapa nombre->id de atributos de la plantilla.
      - modelo: modelo de Gemini configurado en la plantilla (mismo para todas
        las filas de esta corrida), para el libro de consumo de IA.
    """
    # Índice único: el path reauditar puede traer índices duplicados (concat sin
    # reset), lo que rompería el join de resultados y el lookup por fila.
    df = df.reset_index(drop=True)

    semaphore = threading.Semaphore(concurrent_api_calls)
    # `prompt_info` armado afuera solo lo pasa el script que compara auditar con y sin
    # conocimiento de referencia (scripts/probar_conocimiento_plantilla.py).
    if prompt_info is None:
        prompt_info = prompt(plantilla_id=plantilla_id)

    # Por fila: transcribir solo si se pidió Y el llamado no fue transcripto antes.
    ids_aplicativo = calcular_id_aplicativo(df) if transcribir else None
    ya_transcriptos = (
        ids_con_transcripcion(engine, ids_aplicativo)
        if (transcribir and engine is not None and ids_aplicativo is not None) else set()
    )

    def _debe_transcribir(index) -> bool:
        if not transcribir or ids_aplicativo is None:
            return False
        idap = ids_aplicativo.get(index)
        if idap is None or (isinstance(idap, float) and pd.isna(idap)):
            return False  # sin id no se puede deduplicar ni guardar correctamente
        return str(idap) not in ya_transcriptos

    trans_results: Dict[Any, pd.Series] = {}
    cal_results: Dict[Any, pd.Series] = {}

    # El bloque fijo de la plantilla va UNA vez a la caché de contexto y todas las
    # filas de la corrida lo referencian (ver AuditorIA/cache_plantillas.py). Si no
    # se puede, `cache_contexto` queda en None y cada llamado lo manda inline.
    cache_contexto = cache_plantillas.obtener_o_crear(
        gemini_api, prompt_info,
        sistema=instruccion_de_sistema(prompt_info),
        modelo=prompt_info.get('modelo', MODELO_IA_DEFAULT),
        llamados=len(df),
        plantilla_id=plantilla_id,
    )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_procesar_fila_auditoria, row, gemini_api, semaphore, prompt_info, _debe_transcribir(index), cache_contexto): index
            for index, row in df.iterrows()
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                serie_trans, serie_cal = future.result(timeout=300)
            except TimeoutError:
                print(f'ERROR: La tarea para el índice {index} excedió el tiempo límite.')
                serie_trans, serie_cal = None, procesar_respuesta_error("Timeout de ejecución")
            except Exception as exc:
                print(f'La fila con índice {index} generó una excepción: {exc}')
                serie_trans, serie_cal = None, procesar_respuesta_error(f"Excepción: {str(exc)}")

            if serie_trans is not None:
                trans_results[index] = serie_trans
            if serie_cal is not None:
                cal_results[index] = serie_cal

    # --- DataFrame de calidad (siempre presente) ---
    nombre_id_map = prompt_info.get('nombre_id_map', {})
    if cal_results:
        df_calidad = df.join(pd.DataFrame.from_dict(cal_results, orient='index'))
    else:
        print("No se procesó ninguna fila de calidad con éxito.")
        df_calidad = df.assign(status_auditoria="FALLO_TOTAL")

    # Versión de la plantilla usada (Golden Set — Fase 2): viaja como columna hasta
    # auditoria_a_SQL, que la persiste en calidad.Auditorias. Sin esto, comparar dos
    # prompts sería imposible: sus auditorías quedarían mezcladas en la misma métrica.
    if prompt_info.get('version_id') is not None:
        df_calidad = df_calidad.assign(plantilla_version_id=prompt_info['version_id'])

    # --- DataFrame de transcripciones ---
    df_transcripciones = None
    if transcribir and trans_results:
        df_transcripciones = df.join(pd.DataFrame.from_dict(trans_results, orient='index'))

    # Incidencias (ver AuditorIA/incidencias.py): se consolida lo que declaró la IA con
    # lo que ya venía marcado por el gate de audio, y con la transcripción en mano se
    # verifica que el operador que se escucha sea el que dice el sistema.
    df_calidad = inc.fusionar_declarada_por_ia(df_calidad)
    if df_transcripciones is not None and not df_transcripciones.empty:
        inc.verificar_operador(df_calidad, df_transcripciones)
    inc.normalizar_columna(df_calidad)

    # El nivel de razonamiento viaja junto al modelo porque es la otra mitad de
    # "con qué se auditó esto": el modelo dice cuánto vale cada token y el nivel,
    # cuántos se gastan. Auditor.run los guarda a los dos en el log de la corrida.
    return (df_calidad, df_transcripciones, nombre_id_map,
            prompt_info.get('modelo', MODELO_IA_DEFAULT),
            razonamiento.normalizar(prompt_info.get('nivel_razonamiento')))


# --- Lógica Principal del Batch ---

def preparar_audios_en_paralelo(df: pd.DataFrame) -> Dict[Any, Tuple[bytes, str]]:
    """Comprime los audios del DataFrame en paralelo y devuelve {index: (datos, mime)}.

    Las filas que no se pueden leer/convertir quedan afuera del dict (se omiten del
    batch, igual que antes hacían las que fallaban la subida).

    Concurrencia propia del batch (más alta que el semáforo global de 5 del path
    interactivo): preparar 1345 audios de a 5 era el cuello de botella.
    """
    workers = max(1, getattr(settings, 'GEMINI_BATCH_UPLOAD_WORKERS', 5))
    preparados: Dict[Any, Tuple[bytes, str]] = {}

    def _preparar(audio_dir):
        resultado = preparar_contenido(audio_dir)
        if resultado is None:
            return None
        file_io, mime_type, _display_name, _log = resultado
        return file_io.getvalue(), mime_type

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_index = {
            executor.submit(_preparar, row['audio_dir']): index
            for index, row in df.iterrows()
        }
        for future in as_completed(future_to_index):
            index = future_to_index[future]
            try:
                resultado = future.result()
                if resultado and resultado[0]:
                    preparados[index] = resultado
                else:
                    print(f"Fallo al preparar el audio del índice {index}, se omitirá del batch request.")
            except Exception as e:
                print(f"Excepción al preparar el audio del índice {index}: {e}")

    return preparados


def process_batch(
    engine: engine.Engine,
    batch_df: pd.DataFrame,
    plantilla_id: int,
    gemini_api: genai.Client,
    user_id: int,
    gsheet_id: Optional[str] = None,
    gsheet_name: Optional[str] = None,
    column_template_id: Optional[int] = None,
    contexto_ejecucion: Optional[dict] = None,
    transcribir: bool = False,
    audios_preparados: Optional[Dict[Any, Tuple[bytes, str]]] = None,
    email_destinatarios: Optional[str] = None,
) -> Optional[str]:
    """
    Procesa un único lote de audios:
    1. Prepara (comprime) los audios en paralelo, salvo que ya vengan preparados.
    2. Crea y envía un trabajo de batch a Gemini con el audio inline.
    3. Prepara los datos para la BD.
    4. Guarda la información del trabajo y los datos en la BD.

    Si `transcribir`, la transcripción viaja ANIDADA en el mismo request de calidad
    (campo 'Transcripcion' del esquema, igual que en el path sync: una sola lectura
    del audio, sin un segundo job de batch). La respuesta la desanida
    Auditor.procesar_batch horas después, cuando Gemini devuelve el resultado.
    """

    prompt_info = prompt(plantilla_id=plantilla_id)
    model = prompt_info.get('modelo', MODELO_IA_DEFAULT)  # Modelo de la plantilla, definido una vez

    # --- 1. Preparar los audios (comprimir) en paralelo ---
    # Van INLINE en el request del batch, no por la File API: los modelos Gemini 3.x
    # rechazan con 403 PERMISSION_DENIED cualquier file_uri (ver gemini_files.
    # audio_parte_activa). En batch el síntoma era peor que un error: el job termina
    # SUCCEEDED y es cada respuesta la que trae code=7 "The caller does not have
    # permission", así que procesar_batch las descartaba fila por fila y la corrida
    # quedaba registrada como EXITO con menos auditorías de las pedidas.
    # calidad_batch corta los lotes con el audio ya comprimido a mano y pasa acá lo
    # preparado (`audios_preparados`) para no comprimir dos veces. El lote sale como
    # archivo JSONL (ver AuditorIA/batch_cola.py), así que el tope de 20 MB del request
    # inline ya no aplica: el corte es por cantidad de llamados.
    audios_preparados = audios_preparados or preparar_audios_en_paralelo(batch_df)

    if not audios_preparados:
        print("No se pudo preparar ningún audio para este lote.")
        return None

    # Conservar el audio comprimido de las filas que SÍ se prepararon, para poder
    # escucharlo/descargarlo después (ver AuditorIA/audio_store.py). En este punto los
    # `.clean.ogg` físicos ya se crearon al comprimir y batch_df todavía tiene
    # `audio_dir`. Best-effort: nunca corta el batch. Solo las filas preparadas (las
    # que fallaron no tienen audio útil).
    try:
        from AuditorIA import audio_store
        audio_store.persistir_audios_df(engine, batch_df.loc[list(audios_preparados.keys())])
    except Exception as e:
        print(f"No se pudieron conservar los audios del batch: {e}")

    # Los chats no tienen audio ni transcripción: se guarda la conversación para poder
    # leerla en "Auditorías Realizadas" igual que la transcripción de un llamado.
    try:
        from AuditorIA.Trancribir import guardar_chats_como_transcripcion
        guardar_chats_como_transcripcion(engine, batch_df.loc[list(audios_preparados.keys())])
    except Exception as e:
        print(f"No se pudieron conservar los chats del batch: {e}")

    # --- 2. Preparar y Enviar el Trabajo de Batch a Gemini ---
    # Mismo criterio de transcripción que el path sync (ver apply_auditoria_threads):
    # solo se transcriben los llamados SIN transcripción previa (mismo audio = misma
    # transcripción) y nunca los chats (.json). Se decide por fila, así que cada request
    # del lote lleva su propio config.
    ids_aplicativo = calcular_id_aplicativo(batch_df) if transcribir else None
    ya_transcriptos = (
        ids_con_transcripcion(engine, ids_aplicativo)
        if (transcribir and ids_aplicativo is not None) else set()
    )

    def _debe_transcribir(index, row) -> bool:
        if not transcribir or ids_aplicativo is None:
            return False
        audio = row.get('audio_dir')
        if isinstance(audio, str) and audio.endswith('.json'):
            return False  # chat, no hay audio que transcribir
        idap = ids_aplicativo.get(index)
        if idap is None or (isinstance(idap, float) and pd.isna(idap)):
            return False  # sin id no se puede deduplicar ni guardar correctamente
        return str(idap) not in ya_transcriptos

    # El cupo se consulta ACÁ, antes de armar los requests, porque de eso depende si el
    # lote puede usar la caché de contexto: el nombre de la caché queda escrito dentro
    # del JSONL, y un lote que se va a la cola puede salir muchas horas después. Si sale
    # ahora, la caché vive de sobra (TTL 24 h); si va a la cola, se arma sin caché y paga
    # el texto de la plantilla entero, que es lo que hacía siempre.
    # (`hay_cupo` cachea su listado, así que preguntar acá no agrega latencia al camino
    # de abajo.)
    sale_ahora = batch_cola.hay_cupo(gemini_api)
    cache_contexto = cache_plantillas.obtener_o_crear(
        gemini_api, prompt_info,
        sistema=instruccion_de_sistema(prompt_info),
        modelo=model,
        llamados=len(audios_preparados),
        plantilla_id=plantilla_id,
    ) if sale_ahora else None

    def _armar_requests(cache: Optional[str]) -> list:
        """Los requests del lote. `cache` None = el texto de la plantilla va inline."""
        armados = []
        con_transcripcion = 0
        # Usamos iterrows del DF original para mantener el orden/indices, pero verificamos
        # si el audio se pudo preparar
        for index, row in batch_df.iterrows():
            if index not in audios_preparados:
                continue  # Salta los audios que no se pudieron preparar

            incluir_transcripcion = _debe_transcribir(index, row)
            con_transcripcion += int(incluir_transcripcion)

            prompt_with_details = texto_del_turno(
                row, prompt_info,
                cacheado=bool(cache), con_transcripcion=incluir_transcripcion,
            )
            datos_audio, mime_audio = audios_preparados[index]

            armados.append({
                "contents": [
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_bytes(data=datos_audio, mime_type=mime_audio),
                            types.Part.from_text(text=prompt_with_details),
                        ],
                    ),
                ],
                "config": obtener_configuracion_gemini(
                    prompt_info, temperatura=0.8,
                    incluir_transcripcion=incluir_transcripcion,
                    cache_contexto=cache,
                )
            })

        if transcribir:
            print(f"Batch: {con_transcripcion}/{len(armados)} requests piden también la transcripción.")
        return armados

    requests = _armar_requests(cache_contexto)

    if not requests:
        print("No se generaron solicitudes para este lote (ningún audio subido fue válido).")
        return None

    # El lote se serializa a un JSONL en disco ANTES de decidir si sale ahora o
    # espera: es el mismo formato para los dos caminos y, si hay que encolarlo, los
    # audios ya comprimidos quedan a salvo en el archivo (ver AuditorIA/batch_cola.py).
    archivo_jsonl = batch_cola.escribir_jsonl(gemini_api, requests, model)
    if not archivo_jsonl:
        print("No se pudo serializar el lote: no se envía ni se encola.")
        return None

    # Metadata para calidad.AuditExecutionLog. Se arma acá —con el DataFrame a mano—
    # aunque el lote todavía no tenga batch_id: si se encola, viaja serializada y la
    # fila se abre recién cuando el lote sale. Puramente observabilidad: nunca debe
    # cortar el batch si falla.
    kwargs_log: Optional[dict] = None
    if contexto_ejecucion:
        try:
            empresa_val = batch_df['empresa_id'].iloc[0] if 'empresa_id' in batch_df.columns and not batch_df.empty else None
            campana_val = batch_df['campana_id'].iloc[0] if 'campana_id' in batch_df.columns and not batch_df.empty else None
            por_operador = bool(contexto_ejecucion.get("por_operador"))
            por_tipificacion = bool(contexto_ejecucion.get("por_tipificacion"))

            # Muestreo por operador/tipificación: igual que en el path sync (ver
            # Auditor.run), `cantidad` deja de ser el total y pasa a ser "por cada
            # grupo", así que el número real recién se sabe con los audios ya
            # descargados. Se mide sobre las filas que efectivamente se mandaron a
            # Gemini en ESTE lote (las que fallaron la subida no se auditan), que es
            # justo el alcance de esta fila del log. El df todavía no pasó por
            # _estandarizar_columnas_sql (eso ocurre al procesar la respuesta, horas
            # después): calcular_desglose_muestreo resuelve las columnas por alias.
            df_enviado = batch_df.loc[list(audios_preparados.keys())]
            desglose = calcular_desglose_muestreo(
                df_enviado, por_operador=por_operador, por_tipificacion=por_tipificacion
            )
            cantidad_solicitada = (
                len(df_enviado) if (por_operador or por_tipificacion)
                else contexto_ejecucion.get("cantidad_solicitada")
            )

            kwargs_log = dict(
                trigger_source=contexto_ejecucion.get("trigger_source", "manual"), modo="batch",
                scheduler_id=contexto_ejecucion.get("scheduler_id"), scheduler_name=contexto_ejecucion.get("scheduler_name"),
                task_id=contexto_ejecucion.get("task_id"),
                empresa=empresa_val, campana=campana_val, plantilla_id=plantilla_id, user_id=user_id,
                fecha_desde=contexto_ejecucion.get("fecha_desde"), fecha_hasta=contexto_ejecucion.get("fecha_hasta"),
                cantidad_solicitada=cantidad_solicitada,
                por_operador=por_operador, por_tipificacion=por_tipificacion,
                desglose_muestreo=desglose,
                modelo=model,
                # El nivel con el que se creó ESTE job. Se guarda al abrir (igual que
                # el modelo): al cerrar, horas después, la plantilla puede tener otro.
                nivel_razonamiento=prompt_info.get('nivel_razonamiento'),
                # Todos los lotes de la misma corrida comparten run_id: es lo que
                # deja agrupar en /uso-ia las N filas que genera un solo run_batch.
                run_id=contexto_ejecucion.get("run_id"),
                # A quién hay que avisarle cuando termine la CORRIDA (no este lote):
                # el que cierra último lee de acá, y puede ser un lote abandonado que
                # no tiene ni DataFrame ni Batch_data para consultar.
                mail_destinatarios=email_destinatarios,
            )
        except Exception as e:
            print(f"No se pudo armar el registro del lote para AuditExecutionLog: {e}")


    # --- 3. Preparar los datos para la BD ---
    # Se arman ANTES de que el lote exista en Gemini: si no hay cupo, viajan
    # serializados en la cola y se escriben cuando el lote salga.

    indices_procesados = [index for index, row in batch_df.iterrows() if index in audios_preparados]
    df_sql = batch_df.loc[indices_procesados].copy()

    # El audio ya no vive en la File API (viaja inline en el request), así que no queda
    # ningún archivo remoto que borrar al procesar la respuesta. La columna se conserva
    # —Batch_data la espera y los batches viejos, en vuelo, todavía traen nombre— pero
    # se guarda vacía: Auditor.borrar_archivo_gemini ignora los valores vacíos.
    df_sql['gemini_file_name'] = ""
    df_sql['user_id'] = user_id
    df_sql['plantilla_id'] = plantilla_id
    
    # NUEVO: Guardar la configuración de GSheets (Si no vienen, guardamos string vacío)
    df_sql['gsheet_id'] = gsheet_id if gsheet_id else ""
    df_sql['gsheet_name'] = gsheet_name if gsheet_name else ""
    # Destinatarios del mail de resultado (los `email_addresses` de la tarea
    # programada). Viajan con el lote como el resto de la configuración de envío:
    # cuando el batch termina, horas después, la tarea ya no está a mano y antes se
    # caía al único dato disponible ahí —el mail del usuario que la creó—, así que
    # el resultado le llegaba solo a esa persona y no a la lista configurada.
    df_sql['email_destinatarios'] = email_destinatarios if email_destinatarios else ""
    # Plantilla de columnas: viaja junto al batch para que, al finalizar (horas
    # después), procesar_batch exporte a GSheets/correo las mismas columnas que la
    # pantalla "Auditorías Realizadas".
    df_sql['column_template_id'] = str(column_template_id) if column_template_id else ""
    # Modelo de Gemini usado para ESTE batch (resuelto una vez arriba, de la
    # plantilla): viaja igual que column_template_id/gsheet_id para que, al
    # guardar/costear el resultado horas después (_procesar_grupo_batch), se
    # use el modelo REAL con el que se generó, no el que la plantilla tenga
    # configurado en ese momento (podría haber cambiado mientras tanto).
    df_sql['modelo_ia'] = model
    # Ídem la versión de la plantilla (Golden Set — Fase 2): el resultado del batch
    # se guarda horas después, y para entonces la plantilla puede haberse editado.
    # Lo que hay que registrar es la versión con la que se armó ESTE request.
    df_sql['plantilla_version_id'] = str(prompt_info.get('version_id') or "")
    # FIN NUEVO
    
    prompt_nombre_id_map = prompt_info.get('nombre_id_map', {})
    df_sql['prompt_nombre_id_map'] = str(prompt_nombre_id_map)
    
    df_sql = df_sql.reset_index(drop=True)
    
    # El melt va a convertir "gsheet_id" y "gsheet_name" en filas automáticamente
    # guardándolas en tu tabla calidad.Batch_data
    df_long = df_sql.reset_index().melt(
        id_vars=['index',],
        var_name='columna',
        value_name='valor'
    )
    df_long.rename(columns={'index': 'segment_id'}, inplace=True)

    # Las filas de calidad.Batch_data, ya serializadas: se escriben cuando el lote
    # tenga batch_id (ahora si sale ahora, o al despacharlo si queda en la cola).
    filas_batch_data = [
        {'segment_id': fila['segment_id'], 'columna': fila['columna'],
         'valor': valor_para_batch_data(fila['valor'])}
        for fila in df_long.to_dict('records')
    ]

    # --- 4. Mandarlo ahora o dejarlo en la cola ---
    # El techo son 100 jobs en estado no terminal en TODA la cuenta (auditorías +
    # transcripciones). Antes, al tocarlo, `batches.create` rebotaba y el lote se
    # descartaba en silencio: audios que nadie auditaba nunca. Ahora, sin cupo, el
    # lote queda con su JSONL en disco y sale solo cuando el scheduler ve lugar.
    if not sale_ahora:
        lote_id = batch_cola.encolar(
            engine, archivo_jsonl=archivo_jsonl, llamados=len(requests), modelo=model,
            plantilla_id=plantilla_id, user_id=user_id,
            filas_batch_data=filas_batch_data, kwargs_log=kwargs_log,
            run_id=(contexto_ejecucion or {}).get("run_id"),
            motivo="Sin cupo de batches en Gemini al momento de armar el lote.",
        )
        return f"{PREFIJO_PENDIENTE}{lote_id}" if lote_id else None

    try:
        batch_job = batch_cola.crear_job(gemini_api, archivo_jsonl, model)
        print(f"Trabajo de batch creado: {batch_job.name}")
    except Exception as e:
        # Puede ser cupo (el conteo tiene hasta un minuto de atraso) o un problema
        # pasajero de la API: en los dos casos el lote se guarda y se reintenta.
        print(f"Error al crear el trabajo de batch en Gemini: {e}. Se encola para reintentar.")
        if cache_contexto:
            # El JSONL apunta a una caché que puede no existir cuando el lote salga de
            # la cola (horas después), y un request que referencia una caché muerta
            # falla: esa auditoría se perdería. Se reescribe sin caché —el lote paga el
            # texto entero, que es lo que hacía antes— y queda a salvo de la espera.
            print("Reescribiendo el lote sin caché de contexto antes de encolarlo.")
            archivo_jsonl = batch_cola.escribir_jsonl(
                gemini_api, _armar_requests(None), model, ruta=archivo_jsonl
            ) or archivo_jsonl
        lote_id = batch_cola.encolar(
            engine, archivo_jsonl=archivo_jsonl, llamados=len(requests), modelo=model,
            plantilla_id=plantilla_id, user_id=user_id,
            filas_batch_data=filas_batch_data, kwargs_log=kwargs_log,
            run_id=(contexto_ejecucion or {}).get("run_id"),
            motivo=f"Falló el envío: {e}",
        )
        return f"{PREFIJO_PENDIENTE}{lote_id}" if lote_id else None

    # --- 5. Guardar en la Base de Datos ---
    try:
        batch_cola.registrar_lote_enviado(
            engine, batch_id=batch_job.name,
            filas_batch_data=filas_batch_data, kwargs_log=kwargs_log,
        )
        print(f"Datos del batch {batch_job.name} guardados en la base de datos.")
    except Exception as e:
        print(f"Error en la transacción de la base de datos: {e}")
        # ¡CRÍTICO! Si la BD falla, debemos cancelar el job que ya se envió: sin
        # Batch_data sus respuestas no se pueden atribuir a ninguna interacción.
        print(f"Cancelando trabajo de batch {batch_job.name} debido a fallo en BD...")
        try:
            gemini_api.batches.cancel(name=batch_job.name)  # type: ignore
            print(f"Trabajo {batch_job.name} cancelado exitosamente.")
        except Exception as cancel_e:
            print(f"ERROR: No se pudo cancelar el trabajo {batch_job.name}: {cancel_e}")
        return None  # La operación falló

    # El JSONL ya cumplió: el lote vive en Gemini.
    try:
        os.remove(archivo_jsonl)
    except OSError:
        pass

    return batch_job.name

def calidad_batch(
    engine:engine.Engine,
    df: pd.DataFrame,
    plantilla_id:int,
    gemini_api: genai.Client,
    user_id:int,
    gsheet_id: Optional[str] = None,
    gsheet_name: Optional[str] = None,
    column_template_id: Optional[int] = None,
    contexto_ejecucion: Optional[dict] = None,
    transcribir: bool = False,
    email_destinatarios: Optional[str] = None,
    ) -> Optional[list[str]]:
    """
    Función principal que divide un DataFrame en lotes y los procesa.

    Devuelve None si quedó algún audio SIN destino (no salió a Gemini ni quedó en la
    cola). Quien llama tiene que tratar eso como "hay que reintentar el origen": es la
    única señal de que se perdieron audios. Si devuelve lista, todos los lotes están a
    salvo, sea como job de Gemini o como pendiente en calidad.BatchPendientes.
    """
    # Índice único: el path reauditar puede traer índices duplicados (concat sin
    # reset). Con etiquetas repetidas, el .loc de abajo devolvería filas de más y
    # los dicts keyed por índice de process_batch (audios preparados, transcribir sí/no)
    # colisionarían entre filas. Mismo motivo que en apply_auditoria_threads.
    df = df.reset_index(drop=True)

    # Corte de los lotes. El lote ya no viaja como request inline (tope 20 MB, que
    # daban ~16 llamados por job) sino como archivo JSONL, que llega a 2 GB: el límite
    # real pasó a ser cuántos jobs podemos tener en vuelo (100 en toda la cuenta), así
    # que conviene un lote GRANDE. No se va al máximo a propósito: un job que falla se
    # lleva puesto todo lo que tiene adentro, y 250 llamados es el punto donde 2000
    # audios entran en 8 jobs (contra 125 antes) sin que un fallo cueste una corrida
    # entera. El tope de bytes queda como red de contención del tamaño del archivo.
    MAX_LLAMADOS_POR_LOTE = int(getattr(settings, "BATCH_MAX_LLAMADOS_POR_LOTE", 250))
    MAX_BYTES_POR_LOTE = int(getattr(settings, "BATCH_MAX_BYTES_POR_LOTE", 400 * 1024 * 1024))

    audios_preparados = preparar_audios_en_paralelo(df)
    if not audios_preparados:
        print("No se pudo preparar ningún audio para el batch.")
        return None

    batches = []
    current_batch_size = 0
    current_batch_indices = []

    for index, _row in df.iterrows():
        if index not in audios_preparados:
            continue  # No se pudo leer/convertir: ya se avisó al prepararlo.

        file_size = len(audios_preparados[index][0])

        if current_batch_indices and (
            len(current_batch_indices) >= MAX_LLAMADOS_POR_LOTE
            or current_batch_size + file_size > MAX_BYTES_POR_LOTE
        ):
            batches.append(df.loc[current_batch_indices])
            current_batch_indices = []
            current_batch_size = 0

        current_batch_indices.append(index)
        current_batch_size += file_size

    if current_batch_indices:
        batches.append(df.loc[current_batch_indices])

    print(f"Se procesarán {len(audios_preparados)} audios en {len(batches)} lote(s).")

    all_results = []
    lotes_perdidos = 0
    for i, batch_df in enumerate(batches):
        print(f"--- Procesando Lote {i+1}/{len(batches)} ({len(batch_df)} audios) ---")
        processed_batch = process_batch(
            engine=engine,
            batch_df=batch_df,
            audios_preparados={idx: audios_preparados[idx] for idx in batch_df.index},
            plantilla_id=plantilla_id,
            gemini_api=gemini_api,
            user_id=user_id,
            gsheet_id=gsheet_id,
            gsheet_name=gsheet_name,
            column_template_id=column_template_id,
            contexto_ejecucion=contexto_ejecucion,
            transcribir=transcribir,
            email_destinatarios=email_destinatarios,
        )
        if processed_batch:
            all_results.append(processed_batch)
        else:
            # Ni salió a Gemini ni se pudo encolar: estos audios se quedaron sin
            # destino. No alcanza con que los otros lotes hayan salido —quien llama
            # podría borrar el origen y perderlos—, así que la corrida entera se
            # reporta como fallida.
            lotes_perdidos += len(batch_df)

    if lotes_perdidos:
        print(f"ATENCIÓN: {lotes_perdidos} audio(s) no se pudieron ni enviar ni encolar.")
        return None

    if not all_results:
        return None

    encolados = sum(1 for r in all_results if batch_cola.es_pendiente(r))
    if encolados:
        print(f"{encolados} de {len(all_results)} lote(s) quedaron en cola esperando cupo en Gemini.")
    return all_results