"""Asistente de IA para el editor de plantillas de auditoría.

Provee dos capacidades, pensadas para usuarios de Calidad SIN conocimientos de
programación ni de "prompt engineering":

1. mejorar_prompt(): toma un texto de la plantilla (System Prompt, Recordatorio o
   el Prompt de un Atributo) y devuelve una versión mejorada + un resumen de los
   cambios en lenguaje claro. NO guarda nada: el endpoint devuelve la propuesta y
   el usuario decide si la aplica.

2. generar_plantilla(): a partir de una descripción simple del usuario, arma una
   plantilla completa (System Prompt + Recordatorio + Atributos con sus tipos y
   ponderaciones). Tampoco guarda nada: devuelve la propuesta para revisión.

3. revisar_plantilla(): mira la plantilla ENTERA y devuelve un plan de cambios —
   cabecera, y por cada atributo si se modifica (nombre, prompt, TIPO DE DATO,
   OPCIONES de la lista, marca de opcional, ponderación), si falta uno nuevo o si
   sobra uno. Existe porque mejorar_prompt() solo toca texto de a un campo: no podía
   arreglar un criterio de cumplimiento guardado como Si/No que debería puntuar, ni
   un enum sin salida segura, ni pesos que no cierran. Devuelve el diff campo por
   campo (con advertencias de impacto) para que el usuario acepte o descarte cada
   cambio por separado; el guardado real lo hace el endpoint de aplicar.

Las tres usan un modelo "Pro" de Gemini (configurable, ver settings.GEMINI_PLANTILLAS_MODEL)
porque es una tarea de bajo volumen y alto valor donde prima la calidad del texto.

Salida SIEMPRE estructurada (response_schema) para que el frontend pueda renderizar
el "antes/después" de forma confiable.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from app.config import settings
from AuditorIA import evidencia_plantilla
from AuditorIA import call_details
from AuditorIA import limites_texto
from AuditorIA import senales_prompt
from AuditorIA import razonamiento

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------- #
# Cliente Gemini (perezoso, reutiliza la API key de auditoría)                  #
# ---------------------------------------------------------------------------- #
_client: Optional[genai.Client] = None
_client_lock = threading.Lock()


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = genai.Client(api_key=settings.GEMINI_AUDITORIA_API_KEY)
    return _client


def _modelo() -> str:
    return getattr(settings, "GEMINI_PLANTILLAS_MODEL", "gemini-3.8-flash")


# Reintentos de un llamado que devuelve JSON cortado o falla por red. Van con presupuesto
# de tiempo además de tope de intentos: ver el comentario en _generar_json.
MAXIMO_INTENTOS = 3
PRESUPUESTO_REINTENTOS_SEG = 150


# Tipos de atributo soportados por el sistema (deben coincidir con el frontend y
# con AuditorIA/gemini.py::prompt()). Se usan para validar lo que genera la IA.
TIPOS_ATRIBUTO = [
    "string", "integer", "number", "boolean", "enum", "critical_audit",
    "array_string", "array_integer", "array_number", "array_boolean", "array_enum",
]
TIPOS_CON_OPCIONES = {"enum", "array_enum", "critical_audit"}


# ---------------------------------------------------------------------------- #
# Conocimiento de dominio compartido por las tres funciones                    #
# ---------------------------------------------------------------------------- #
_CONTEXTO_DOMINIO = """\
CONTEXTO DEL SISTEMA (leelo con atención, define cómo se usan estos textos):
- Estás ayudando a un área de CALIDAD de un Contact Center a auditar interacciones
  (llamadas telefónicas o chats) de operadores mediante una IA auditora.
- Una PLANTILLA de auditoría tiene tres partes:
  * System Prompt: define el rol y el comportamiento de la IA auditora (suele empezar
    con "Sos un analista de calidad senior...").
  * Recordatorio: instrucciones generales que aplican a TODOS los atributos y que se
    agregan al final del prompt (aclaraciones, definiciones, reglas transversales).
  * Atributos: cada atributo es una pregunta/criterio que la IA debe completar sobre
    la interacción. Cada uno tiene un TIPO de dato y un PROMPT que explica en detalle
    qué significa y qué debe responder la IA.
- Tipos de atributo disponibles:
  * string  -> texto libre.            * integer / number -> números.
  * boolean -> Sí/No.                  * enum -> elige UNA opción de una lista cerrada.
  * array_enum -> elige VARIAS opciones de una lista.   * array_* -> listas.
  * critical_audit -> atributo de calidad ponderado: la IA responde OK / NO OK / EC
    (Error Crítico) y opcionalmente N/A. OK suma el 100% de su peso, NO OK suma 0,
    EC deja el puntaje del llamado en 0, y N/A excluye el atributo del cálculo.

REGLAS DE ORO DE LA AUDITORÍA (aplicalas siempre):
1. Contemplá TODOS los desenlaces posibles del llamado, no solo el caso ideal. Muchas
   interacciones NO permiten evaluar todos los criterios. Ejemplos típicos:
   - El cliente pide ser recontactado y corta a los 30 segundos.
   - Número equivocado, contestador, llamada caída, cliente que no habla.
   - El motivo de la llamada hace que un criterio no tenga sentido.
   En esos casos el criterio NO debe penalizar al operador: debe poder responderse
   "N/A" (No Aplica). Por eso, en atributos de calidad (critical_audit) que puedan no
   aplicar, hay que habilitar la opción N/A; y en enums hay que prever una opción
   "No aplica" / "Otros" cuando ninguna de las opciones liste el caso real.
   Para los tipos que NO tienen lista de opciones (boolean, string, números y listas)
   existe la marca "es_opcional": con ella la IA auditora puede DEJAR SIN RESPONDER el
   atributo cuando no hay evidencia, en vez de verse obligada a elegir un Sí/No que
   castiga mal al operador. Marcá es_opcional=true en todo boolean/número/texto que
   dependa de que cierta situación haya ocurrido en el llamado.
2. Todo enum (selección de lista) DEBE tener una salida segura: si ninguna opción
   describe la situación, debe existir "Otros" o "No aplica". Nunca dejes a la IA
   obligada a elegir una opción incorrecta.
3. Sé concreto y dale ejemplos a la IA: explicá qué cuenta como OK y qué como NO OK,
   qué evidencia buscar, y cómo desempatar casos ambiguos.
4. La IA solo puede basarse en lo que realmente ocurre en la interacción: pedile que
   NO asuma ni invente datos que no se mencionan.
5. Escribí en español rioplatense, claro y profesional, como el resto del sistema.
6. Los atributos de texto libre ('string' / 'array_string') son para respuestas BREVES
   (un feedback, un resumen de dos líneas, una cita corta). NUNCA propongas un atributo
   que pida la transcripción del llamado, el diálogo completo o "todo lo que dijo el
   cliente": el sistema ya transcribe por otra vía (tilde "Transcripción" al auditar, o
   la cola de "Auditorías Realizadas") y RECHAZA esos atributos al guardarlos. Además
   todo texto libre se recorta al tope configurado, así que pedir textos largos solo
   encarece la auditoría sin agregar información.
"""


# ---------------------------------------------------------------------------- #
# Helpers de parsing de la respuesta de Gemini                                 #
# ---------------------------------------------------------------------------- #
def _texto_de_respuesta(response) -> str:
    """Ensambla el texto de la respuesta ignorando las partes de 'pensamiento'.

    Los modelos con thinking activado pueden dividir la salida en varias partes;
    algunas son 'thought'. Concatenamos solo las partes de respuesta real.
    """
    try:
        partes = response.candidates[0].content.parts or []
    except (AttributeError, IndexError, TypeError):
        return response.text or ""

    chunks = []
    for parte in partes:
        texto = getattr(parte, "text", None)
        if not texto:
            continue
        if getattr(parte, "thought", False):
            continue
        chunks.append(texto)
    if chunks:
        return "".join(chunks)
    return response.text or ""


def _registrar_consumo(response, user_id: Optional[int], ref_label: str, intento: int) -> None:
    """Registra el consumo de Gemini de este llamado en el libro pagina_web.IA_Uso.

    Cada intento del retry es un llamado facturable aparte, por eso se registra con un
    ref_id único. Best-effort: nunca rompe la generación."""
    try:
        from app.uso_ia import registrar_uso_ia
        registrar_uso_ia(
            feature="asistente_plantillas",
            modelo=_modelo(),
            modo="sync",
            user_id=user_id,
            usage=getattr(response, "usage_metadata", None),
            ref_id=f"asistente:{uuid.uuid4()}",
            extras={"accion": ref_label, "intento": intento},
        )
    except Exception:
        logger.exception("No se pudo registrar el consumo de IA del asistente.")


def _generar_json(system_instruction: str, user_text: str, response_schema: types.Schema,
                  temperatura: float = 0.6, user_id: Optional[int] = None,
                  ref_label: str = "asistente") -> Dict[str, Any]:
    """Llamado genérico a Gemini con salida JSON estructurada.

    Piensa al MÁXIMO (ver razonamiento.NIVEL_RAZONAMIENTO_ASISTENTES): este asistente
    redacta los prompts con los que después auditan miles de llamados, y se ejecuta
    unas pocas veces por día. Hasta el 2026-08-19 acá había un `thinking_budget=4096`
    que en Gemini 3.x no se respeta."""
    client = _get_client()
    config = types.GenerateContentConfig(
        temperature=temperatura,
        max_output_tokens=65535,
        response_mime_type="application/json",
        response_schema=response_schema,
        system_instruction=[types.Part.from_text(text=system_instruction)],
        thinking_config=razonamiento.thinking_config(
            razonamiento.NIVEL_RAZONAMIENTO_ASISTENTES, modelo=_modelo(),
            include_thoughts=True,
        ),
    )
    contents = [
        types.Content(role="user", parts=[types.Part.from_text(text=user_text)]),
    ]

    ultimo_error: Optional[Exception] = None
    arranque = time.perf_counter()
    for intento in range(MAXIMO_INTENTOS):
        # Reintentar un llamado que ya tardó un minuto cuesta otro minuto y otra factura.
        # Con razonamiento HIGH sobre una plantilla entera, tres intentos seguidos son
        # varios minutos de alguien mirando un spinner: si ya se pasó el presupuesto, se
        # corta y se avisa en vez de seguir insistiendo.
        transcurrido = time.perf_counter() - arranque
        if intento and transcurrido > PRESUPUESTO_REINTENTOS_SEG:
            logger.warning("%s: no se reintenta, ya van %.0fs.", ref_label, transcurrido)
            break
        try:
            response = client.models.generate_content(
                model=_modelo(), contents=contents, config=config,  # type: ignore
            )
            # El llamado ya se facturó: registrar su consumo (aunque luego falle el parseo).
            _registrar_consumo(response, user_id, ref_label, intento)
            if not response or not getattr(response, "candidates", None):
                raise RuntimeError("La IA no devolvió ninguna respuesta.")
            texto = _texto_de_respuesta(response)
            if not texto:
                raise RuntimeError("La IA devolvió una respuesta vacía.")
            datos = json.loads(texto, strict=False)
            logger.info("%s: respuesta OK en %.1fs (intento %s).", ref_label,
                        time.perf_counter() - arranque, intento + 1)
            return datos
        except json.JSONDecodeError as e:
            # Casi siempre es la salida cortada por el tope de tokens: se reintenta.
            ultimo_error = e
            logger.warning("%s: JSON inválido a los %.1fs (intento %s).", ref_label,
                           time.perf_counter() - arranque, intento + 1)
        except Exception as e:  # noqa: BLE001 - errores de API/red
            ultimo_error = e
            logger.warning("%s: falló a los %.1fs (intento %s): %s", ref_label,
                           time.perf_counter() - arranque, intento + 1, e)
    raise RuntimeError(f"No se pudo obtener una respuesta válida de la IA: {ultimo_error}")


# ---------------------------------------------------------------------------- #
# 1) Mejorar un prompt existente                                               #
# ---------------------------------------------------------------------------- #
_ETIQUETA_CAMPO = {
    "system_prompt": "el System Prompt (rol y comportamiento de la IA auditora)",
    "recordatorio": "el Recordatorio (instrucciones generales para todos los atributos)",
    "atributo": "el Prompt de un Atributo (criterio puntual que la IA debe evaluar)",
}

_SCHEMA_MEJORA = types.Schema(
    type=types.Type.OBJECT,
    required=["hay_cambios", "prompt_mejorado", "resumen_cambios"],
    properties={
        "hay_cambios": types.Schema(
            type=types.Type.BOOLEAN,
            description="false solo si el texto ya está muy bien y no amerita cambios.",
        ),
        "prompt_mejorado": types.Schema(
            type=types.Type.STRING,
            description="La versión mejorada del texto, lista para usar. Si hay_cambios es false, devolvé el texto original tal cual.",
        ),
        "resumen_cambios": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(type=types.Type.STRING),
            description="Lista de viñetas en español claro explicando qué se cambió y por qué. Pensado para alguien sin conocimientos técnicos.",
        ),
        "nota": types.Schema(
            type=types.Type.STRING,
            description="Aclaración o advertencia opcional (ej.: algo que el usuario debería revisar manualmente). Vacío si no aplica.",
        ),
    },
)


def mejorar_prompt(
    campo: str,
    texto_actual: str,
    instruccion_usuario: Optional[str] = None,
    contexto: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = None,
    campos_contexto: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Devuelve una versión mejorada de un texto de la plantilla, sin guardarla.

    Args:
        campo: "system_prompt" | "recordatorio" | "atributo".
        texto_actual: el texto a mejorar (puede venir vacío).
        instruccion_usuario: pedido opcional del usuario ("quiero agregar X / cambiar Y").
        contexto: info de la plantilla para que la IA entienda el conjunto (nombre,
            descripción, system_prompt, recordatorio, atributos; y, si campo == 'atributo',
            datos del atributo: nombre, tipo, opciones).

    Returns:
        {hay_cambios, prompt_mejorado, resumen_cambios, nota}
    """
    campo = (campo or "").strip()
    if campo not in _ETIQUETA_CAMPO:
        raise ValueError(f"Campo desconocido: {campo!r}. Use system_prompt, recordatorio o atributo.")
    if texto_actual is None:
        texto_actual = ""
    contexto = contexto or {}

    modo = "GUIADO" if (instruccion_usuario or "").strip() else "AUTOMÁTICO"

    system_instruction = (
        "Sos un ingeniero de prompts experto y, además, analista de calidad senior de "
        "contact center. Tu trabajo es mejorar los textos que un área de Calidad usa "
        "para auditar interacciones con una IA.\n\n"
        + _CONTEXTO_DOMINIO
        + call_details.bloque_para_prompt(campos_contexto)
        + "\n\nTAREA: Vas a mejorar " + _ETIQUETA_CAMPO[campo] + ".\n"
        "PRINCIPIOS AL MEJORAR:\n"
        "- Respetá SIEMPRE la intención original. No cambies el objetivo del texto.\n"
        "- Mejorá claridad, precisión y completitud. Eliminá ambigüedades.\n"
        "- Aplicá las REGLAS DE ORO: contemplá los desenlaces que no aplican (N/A), y si "
        "es un enum sin salida segura, sugerí agregar 'Otros'/'No aplica' (y dejalo escrito "
        "en el texto del prompt para que el usuario lo agregue como opción si corresponde).\n"
        "- Si el texto ya está muy bien, devolvé hay_cambios=false y no lo cambies.\n"
        "- NO inventes datos específicos de la campaña/empresa que no estén en el texto o "
        "el contexto. Si falta info, dejalo como un placeholder claro entre [corchetes].\n"
        "- El resumen_cambios debe ser entendible por alguien sin conocimientos técnicos.\n"
        + (
            "\nMODO GUIADO: el usuario te dio una instrucción explícita de qué quiere "
            "agregar o cambiar. Priorizá cumplir ese pedido, integrándolo de forma "
            "coherente con el resto del texto, sin romper lo demás."
            if modo == "GUIADO"
            else "\nMODO AUTOMÁTICO: no hay instrucción del usuario; mejorá lo que puedas "
            "por tu cuenta, de forma conservadora y útil."
        )
    )

    partes_usuario = [f"=== TEXTO ACTUAL A MEJORAR ({campo}) ===\n{texto_actual or '(vacío)'}"]

    if campo == "atributo":
        info_attr = {
            "nombre": contexto.get("atributo_nombre"),
            "tipo": contexto.get("atributo_tipo"),
            "opciones": contexto.get("atributo_opciones"),
        }
        partes_usuario.append(
            "=== DATOS DEL ATRIBUTO ===\n" + json.dumps(info_attr, ensure_ascii=False, indent=2)
        )

    # Contexto de la plantilla (acotado) para que la IA entienda el conjunto.
    plantilla_ctx = {
        "nombre": contexto.get("nombre"),
        "descripcion": contexto.get("descripcion"),
    }
    if campo != "system_prompt":
        plantilla_ctx["system_prompt"] = contexto.get("system_prompt")
    if campo != "recordatorio":
        plantilla_ctx["recordatorio"] = contexto.get("recordatorio")
    atributos_ctx = contexto.get("atributos")
    if atributos_ctx:
        plantilla_ctx["atributos"] = atributos_ctx
    partes_usuario.append(
        "=== CONTEXTO DE LA PLANTILLA (solo para entender el conjunto) ===\n"
        + json.dumps(plantilla_ctx, ensure_ascii=False, indent=2)
    )

    if modo == "GUIADO":
        partes_usuario.append(
            "=== INSTRUCCIÓN DEL USUARIO (qué quiere agregar/cambiar) ===\n"
            + instruccion_usuario.strip()  # type: ignore[union-attr]
        )

    user_text = "\n\n".join(partes_usuario)
    resultado = _generar_json(system_instruction, user_text, _SCHEMA_MEJORA, temperatura=0.5,
                              user_id=user_id, ref_label="mejorar_prompt")

    # Normalización defensiva.
    resultado.setdefault("hay_cambios", True)
    resultado.setdefault("prompt_mejorado", texto_actual)
    resultado.setdefault("resumen_cambios", [])
    resultado.setdefault("nota", "")
    if not isinstance(resultado.get("resumen_cambios"), list):
        resultado["resumen_cambios"] = [str(resultado["resumen_cambios"])]
    return resultado


# ---------------------------------------------------------------------------- #
# 2) Generar una plantilla completa                                            #
# ---------------------------------------------------------------------------- #
_SCHEMA_ATRIBUTO_GEN = types.Schema(
    type=types.Type.OBJECT,
    required=["nombre", "prompt", "tipo"],
    properties={
        "nombre": types.Schema(type=types.Type.STRING, description="Nombre corto del atributo (ej.: 'Saludo inicial')."),
        "prompt": types.Schema(type=types.Type.STRING, description="Explicación detallada para la IA: qué evaluar, qué es OK/NO OK, ejemplos y cuándo es N/A."),
        "tipo": types.Schema(type=types.Type.STRING, enum=TIPOS_ATRIBUTO, description="Tipo de dato del atributo."),
        "opciones": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(type=types.Type.STRING),
            description="Opciones de la lista para enum/array_enum/critical_audit. Para enum SIEMPRE incluí una salida segura ('Otros' o 'No aplica'). Para critical_audit usá OK, NO OK, EC y agregá N/A si el criterio puede no aplicar. Vacío para los demás tipos.",
        ),
        "ponderacion": types.Schema(type=types.Type.NUMBER, description="Peso del atributo para el puntaje (solo critical_audit). 0 para el resto."),
        "es_opcional": types.Schema(
            type=types.Type.BOOLEAN,
            description=(
                "true si la IA auditora puede dejar el atributo SIN responder cuando la "
                "interacción no da evidencia (el criterio no aplica, se cortó antes, el tema "
                "no se tocó). Usalo en boolean/número/texto que dependan de que algo haya "
                "pasado en el llamado. Para critical_audit usá la opción 'N/A' en vez de esto."
            ),
        ),
        "orden": types.Schema(type=types.Type.INTEGER, description="Orden de aparición, empezando en 0."),
    },
)

_SCHEMA_GENERAR = types.Schema(
    type=types.Type.OBJECT,
    required=["nombre", "system_prompt", "atributos"],
    properties={
        "nombre": types.Schema(type=types.Type.STRING, description="Nombre claro y corto de la plantilla."),
        "descripcion": types.Schema(type=types.Type.STRING, description="Descripción breve de para qué sirve."),
        "system_prompt": types.Schema(type=types.Type.STRING, description="System Prompt completo (rol y comportamiento de la IA auditora)."),
        "recordatorio": types.Schema(type=types.Type.STRING, description="Recordatorio con reglas transversales (incluí la regla de N/A para casos que no aplican)."),
        "atributos": types.Schema(type=types.Type.ARRAY, items=_SCHEMA_ATRIBUTO_GEN, description="Lista de atributos a evaluar."),
        "notas": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(type=types.Type.STRING),
            description="Supuestos que tomaste y cosas que el usuario debería revisar/ajustar. En lenguaje simple.",
        ),
    },
)


def generar_plantilla(
    descripcion_usuario: str,
    contexto: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = None,
    campos_contexto: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Genera una plantilla completa a partir de una descripción simple del usuario.

    NO guarda nada: devuelve la propuesta para que el usuario la revise y, si acepta,
    el frontend la cree con los endpoints existentes.

    Args:
        descripcion_usuario: lo que el usuario quiere auditar, en sus palabras.
        contexto: opcional (ej.: nombre de la campaña/empresa) para ambientar.

    Returns:
        {nombre, descripcion, system_prompt, recordatorio, atributos:[...], notas:[...]}
        Cada atributo trae además 'restricciones' listo para la BD.
    """
    if not (descripcion_usuario or "").strip():
        raise ValueError("Describí qué querés auditar para poder generar la plantilla.")
    contexto = contexto or {}

    system_instruction = (
        "Sos un ingeniero de prompts experto y analista de calidad senior de contact "
        "center. Tu trabajo es DISEÑAR una plantilla de auditoría completa a partir de "
        "una descripción simple que da un usuario de Calidad sin conocimientos técnicos.\n\n"
        + _CONTEXTO_DOMINIO
        + call_details.bloque_para_prompt(campos_contexto)
        + "\n\nCÓMO DISEÑAR LA PLANTILLA:\n"
        "- System Prompt: definí el rol de la IA auditora (analista de calidad senior), "
        "el tono objetivo y que se base solo en evidencia de la interacción.\n"
        "- Recordatorio: incluí reglas transversales, y SIEMPRE la regla de que si un "
        "criterio no aplica al llamado (cliente que pide recontacto y corta, número "
        "equivocado, contestador, etc.) se debe responder N/A en lugar de penalizar.\n"
        "- Atributos: proponé un set razonable (típicamente 4 a 10) que cubra el pedido. "
        "Para criterios de cumplimiento usá 'critical_audit' (OK/NO OK/EC, +N/A si puede "
        "no aplicar) con una ponderación coherente. Para clasificaciones usá 'enum' con "
        "una opción 'Otros'/'No aplica'. Usá 'string' para resúmenes/feedback, 'boolean' "
        "para sí/no simples. Cada prompt de atributo debe ser detallado y con ejemplos.\n"
        "- Para CADA atributo que pueda no aplicar, contemplá explícitamente el caso N/A "
        "en su prompt y, si es critical_audit, incluí 'N/A' en sus opciones; si es "
        "boolean/número/texto, marcalo con es_opcional=true para que la IA pueda dejarlo "
        "sin responder en vez de forzar un Sí/No que penalice mal.\n"
        "- En 'notas', dejá los supuestos que tomaste y qué debería revisar el usuario.\n"
        "- No inventes datos de la campaña que no te dieron: usá placeholders [entre corchetes]."
    )

    user_text = (
        "=== LO QUE EL USUARIO QUIERE AUDITAR ===\n"
        + descripcion_usuario.strip()
        + "\n\n=== CONTEXTO (opcional) ===\n"
        + json.dumps(
            {"empresa": contexto.get("empresa"), "campana": contexto.get("campana")},
            ensure_ascii=False,
        )
    )

    resultado = _generar_json(system_instruction, user_text, _SCHEMA_GENERAR, temperatura=0.7,
                              user_id=user_id, ref_label="generar_plantilla")

    # --- Normalización a la forma que esperan los endpoints existentes ---
    resultado.setdefault("nombre", "Plantilla generada por IA")
    resultado.setdefault("descripcion", "")
    resultado.setdefault("system_prompt", "")
    resultado.setdefault("recordatorio", "")
    resultado.setdefault("notas", [])
    atributos = resultado.get("atributos") or []
    atributos_norm: List[Dict[str, Any]] = []
    for i, attr in enumerate(atributos):
        tipo = (attr.get("tipo") or "string").strip()
        if tipo not in TIPOS_ATRIBUTO:
            tipo = "string"
        opciones = attr.get("opciones") or []
        if not isinstance(opciones, list):
            opciones = [str(opciones)]
        opciones = [str(o).strip() for o in opciones if str(o).strip()]

        restricciones = None
        if tipo in TIPOS_CON_OPCIONES:
            if tipo == "critical_audit" and not opciones:
                opciones = ["OK", "NO OK", "EC"]
            if opciones:
                restricciones = {"enum": opciones}

        ponderacion = attr.get("ponderacion")
        try:
            ponderacion = float(ponderacion) if ponderacion is not None else 0.0
        except (TypeError, ValueError):
            ponderacion = 0.0
        if tipo == "critical_audit" and ponderacion <= 0:
            ponderacion = 1.0  # peso por defecto razonable para que puntúe

        atributos_norm.append({
            "nombre": (attr.get("nombre") or f"Atributo {i + 1}").strip(),
            "prompt": (attr.get("prompt") or "").strip(),
            "tipo": tipo,
            "restricciones": restricciones,
            "opciones": opciones,  # útil para previsualizar en el front
            "orden": attr.get("orden") if isinstance(attr.get("orden"), int) else i,
            "DarAviso": False,
            "FrasesAviso": None,
            "ponderacion": ponderacion,
            # En critical_audit el "no aplica" se resuelve con la opción N/A (queda
            # registrada y renormaliza el puntaje), no dejando el campo sin responder.
            "es_opcional": bool(attr.get("es_opcional")) and tipo != "critical_audit",
        })
    resultado["atributos"] = atributos_norm
    return resultado


# ---------------------------------------------------------------------------- #
# 3) Revisar la plantilla ENTERA (mejora masiva)                               #
# ---------------------------------------------------------------------------- #
# mejorar_prompt() solo reescribe UN texto, así que no puede tocar la estructura. Y
# la mayoría de los problemas reales de una plantilla no son de redacción: son de
# estructura. El criterio de cumplimiento guardado como boolean que por eso no
# puntúa; el enum sin "Otros"/"No aplica" que obliga a la IA a elegir una opción
# incorrecta; el Si/No que debería ser opcional porque depende de que algo haya
# pasado en el llamado; los pesos que no cierran; el criterio que falta y el que
# está duplicado. revisar_plantilla() mira todo junto y devuelve un PLAN DE CAMBIOS
# con el diff campo por campo. No escribe nada: aplicar es otro endpoint, y aplica
# solo lo que el usuario dejó tildado.

# Sentinela de "este campo no cambia". Hace falta porque el modelo devuelve SOLO lo
# que toca (si devolviera la plantilla entera reescrita, una plantilla de 25
# atributos se comería la salida y volvería el JSON cortado): sin un valor
# explícito no se puede distinguir "no lo cambio" de "lo cambio a false / a 0" en
# los campos que no son texto.
_SIN_CAMBIO = "sin_cambio"

# Campos de un atributo que la revisión puede cambiar, en el orden en que se
# muestran (el prompt último: es el largo).
_ETIQUETA_CAMPO_ATRIBUTO = {
    "nombre": "Nombre",
    "tipo": "Tipo de dato",
    "opciones": "Opciones de la lista",
    "es_opcional": "Puede quedar sin responder",
    "ponderacion": "Ponderación (peso)",
    "prompt": "Prompt (qué evalúa la IA)",
}

# "Salida segura" de un enum: la opción donde cae la IA cuando ninguna otra describe
# el caso real. Sin una de estas, la lista obliga a responder mal (regla de oro 2).
# "otr[oa]s?" y no "otro": las listas reales dicen "OTRAS" y "Otras gestiones", que con
# el patrón viejo no contaban como salida y se reportaban como problema sin serlo.
_RE_SALIDA_SEGURA = re.compile(r"otr[oa]s?\b|no aplica|no corresponde|ninguna|^n/?a$", re.IGNORECASE)

# Escalas de cumplimiento: NO necesitan salida segura porque sus opciones ya cubren todo
# el universo por construcción. Es la mitad del catálogo real (115 de los 332 atributos
# activos al 2026-08-26 usan "Ok | No Ok | EC" o "Cumple | No cumple"), y marcarlas como
# problema convertía el chequeo en una pared de rojo que nadie iba a mirar: el reclamo
# legítimo —una lista de CLASIFICACIÓN donde el caso real puede no estar— quedaba
# enterrado entre 115 falsos positivos.
_VOCAB_CUMPLIMIENTO = {
    "ok", "no ok", "nook", "ec", "si", "no", "cumple", "no cumple", "cumple/no cumple",
    "n/a", "na", "no aplica", "aplica", "parcial", "parcialmente", "cumple parcialmente",
    "bueno", "regular", "malo", "verdadero", "falso", "true", "false",
}


def _es_escala_cerrada(opciones: List[str]) -> bool:
    """True si la lista se agota en sí misma y pedirle una salida segura no tiene sentido.

    Dos formas: el vocabulario de cumplimiento ("Ok | No Ok | EC") y el par X / No X
    ("Realiza speech" / "No realiza speech"). Lo que queda afuera —y sí se reporta— son
    las listas de clasificación: motivos, tipificaciones, compañías, productos. Ahí el
    caso real puede no estar en la lista, y sin salida segura la IA tiene que elegir algo
    igual.
    """
    normalizadas = [senales_prompt.normalizar(o) for o in opciones]
    if len(normalizadas) < 2:
        return False
    if all(o in _VOCAB_CUMPLIMIENTO for o in normalizadas):
        return True
    if len(normalizadas) == 2:
        corta, larga = sorted(normalizadas, key=len)
        if larga == f"no {corta}":
            return True
    return False

# Qué puede pedir el usuario que se mire con lupa. Se mandan como bloque aparte para
# que el modelo priorice sin perder el resto de la revisión.
FOCOS_REVISION = {
    "claridad": "Redacción de los prompts: ambigüedades, criterios que la IA no puede "
                "verificar con lo que se escucha, falta de ejemplos de OK / NO OK.",
    "no_aplica": "Desenlaces que no aplican: qué atributos necesitan la opción N/A "
                 "(critical_audit) o la marca de opcional (boolean/número/texto).",
    "tipos": "Tipos de dato mal elegidos: criterios de cumplimiento que deberían ser "
             "critical_audit para puntuar, clasificaciones escritas como texto libre "
             "que deberían ser enum, etc.",
    "opciones": "Listas de opciones (enum / array_enum / critical_audit): opciones que "
                "faltan, que se solapan, que están mal escritas o que no tienen salida segura.",
    "ponderacion": "Pesos del puntaje: atributos de calidad sin peso, pesos que no "
                   "reflejan la importancia del criterio, distribución despareja.",
    "cobertura": "Criterios que faltan para lo que la campaña quiere medir y criterios "
                 "duplicados o solapados que convendría unificar.",
}


def _opciones_de(atributo: Dict[str, Any]) -> List[str]:
    """Opciones (enum) de un atributo, venga en 'opciones' o dentro de 'restricciones'.

    Las restricciones viajan a veces como dict y a veces como string JSON según de
    dónde salga el atributo (BD, editor, propuesta de la IA).
    """
    opciones = atributo.get("opciones")
    if not opciones:
        restricciones = atributo.get("restricciones")
        if isinstance(restricciones, str):
            try:
                restricciones = json.loads(restricciones)
            except (ValueError, TypeError):
                restricciones = None
        if isinstance(restricciones, dict):
            opciones = restricciones.get("enum")
    if not opciones:
        return []
    if not isinstance(opciones, list):
        opciones = [opciones]
    return [str(o).strip() for o in opciones if str(o).strip()]


def _tiene_salida_segura(opciones: List[str]) -> bool:
    return any(_RE_SALIDA_SEGURA.search(str(o).strip()) for o in opciones)


def _a_float(valor: Any, defecto: float = 0.0) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return defecto


def _atributo_actual(atributo: Dict[str, Any], indice: int) -> Dict[str, Any]:
    """Forma canónica de un atributo tal como está hoy (para comparar contra la propuesta)."""
    tipo = (atributo.get("tipo") or "string").strip()
    orden = atributo.get("orden")
    return {
        "id": atributo.get("id"),
        "nombre": (atributo.get("nombre") or "").strip(),
        "prompt": (atributo.get("prompt") or "").strip(),
        "tipo": tipo,
        "opciones": _opciones_de(atributo),
        "es_opcional": bool(atributo.get("es_opcional")),
        "ponderacion": _a_float(atributo.get("ponderacion")),
        "orden": orden if isinstance(orden, int) else indice,
        "DarAviso": bool(atributo.get("DarAviso")),
        "FrasesAviso": atributo.get("FrasesAviso"),
    }


def normalizar_atributos(atributos: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Lleva los atributos a la forma canónica que usan las señales y el diff.

    Pública porque el chequeo de salud (que no llama a la IA) necesita exactamente la
    misma normalización que la revisión: si cada uno leyera las restricciones a su
    manera, un enum sin opciones se vería como un problema en un lado y no en el otro.
    """
    return [_atributo_actual(a, i) for i, a in enumerate(atributos or [])]


def _clave_nombre(nombre: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (nombre or "").strip().lower())


# Severidad de una señal. "alta" = rompe o distorsiona la medición y se arregla sí o
# sí; "media" = conviene revisarlo pero la plantilla funciona. Ordena el chequeo de
# salud, decide qué se muestra primero y —desde el gate del guardado— es lo que separa
# lo que frena un guardado de lo que solo se avisa. Se definen en senales_prompt (el
# módulo de más abajo en la cadena de imports) para que no haya dos fuentes.
SEVERIDAD_ALTA = senales_prompt.SEVERIDAD_ALTA
SEVERIDAD_MEDIA = senales_prompt.SEVERIDAD_MEDIA


def senales_de_atributo(atributo: Dict[str, Any],
                        hermanos: Optional[List[Dict[str, Any]]] = None,
                        campos_contexto: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Problemas de UN atributo: los de su estructura y los de la redacción del prompt.

    `hermanos` son los OTROS atributos de la plantilla (en forma canónica). Solo se usan
    para ver si el nombre está repetido: es lo único que no se puede juzgar mirando el
    atributo solo. Sin hermanos, ese chequeo no corre.

    `campos_contexto` son los datos del llamado que la IA recibe además del audio en esa
    campaña (bloque Call_details, ver AuditorIA/call_details.py). Sin ellos las señales
    de redacción son más ciegas: no pueden distinguir un "corroborá contra el CRM"
    imposible de uno que el prompt sí puede resolver porque el CRM viaja con el audio.

    Está separada de `senales_de_plantilla` porque el gate del guardado y el semáforo del
    editor razonan de a un atributo: el analista está editando uno, y frenarlo (o
    pintárselo en rojo) por un problema que está en otro sería incomprensible.

    `atributo` viene en la forma canónica de `_atributo_actual`.
    """
    nombre = atributo["nombre"] or "(sin nombre)"
    tipo = atributo["tipo"]
    opciones = atributo["opciones"]
    senales: List[Dict[str, Any]] = []

    def _senal(clave: str, mensaje: str, severidad: str):
        # El nombre se antepone acá y no en cada mensaje: las señales de redacción vienen
        # de senales_prompt, que no sabe de qué atributo se trata.
        senales.append({
            "clave": clave,
            "mensaje": f"'{nombre}': {mensaje}",
            "severidad": severidad,
            "atributo_id": atributo.get("id"),
            "atributo": atributo.get("nombre"),
        })

    clave_propia = _clave_nombre(nombre)
    if clave_propia and any(_clave_nombre(h.get("nombre")) == clave_propia for h in (hermanos or [])):
        # No es cosmético: el response_schema se arma con el NOMBRE como clave del
        # JSON (ver gemini.py::prompt), así que dos atributos homónimos colapsan en
        # uno y el segundo nunca recibe respuesta.
        _senal("nombre_duplicado",
               "hay otro atributo con el mismo nombre. Los dos comparten la misma clave "
               "en la respuesta de la IA, así que uno queda sin auditar.",
               SEVERIDAD_ALTA)

    if tipo in ("enum", "array_enum"):
        if not opciones:
            _senal("enum_sin_opciones", "es una lista pero no tiene opciones cargadas.",
                   SEVERIDAD_ALTA)
        elif not _tiene_salida_segura(opciones) and not _es_escala_cerrada(opciones):
            _senal("enum_sin_salida_segura",
                   "la lista no tiene salida segura ('Otros' / 'No aplica'), así que si el "
                   "caso real no está la IA igual tiene que elegir una opción.",
                   SEVERIDAD_ALTA)
    elif tipo == "critical_audit":
        if atributo["ponderacion"] <= 0:
            _senal("critical_sin_peso", "es de calidad ponderada pero tiene peso 0, no puntúa.",
                   SEVERIDAD_ALTA)
        if not any(str(o).strip().upper() in ("N/A", "NA") for o in opciones):
            _senal("critical_sin_na",
                   "no admite N/A, así que en los llamados donde el criterio no aplica la "
                   "IA tiene que responder OK o NO OK igual.",
                   SEVERIDAD_MEDIA)
    elif tipo in ("boolean", "integer", "number", "string") and not atributo["es_opcional"]:
        _senal("no_opcional",
               "no está marcado como opcional; si la interacción no da evidencia, la IA se "
               "ve obligada a responderlo igual.",
               SEVERIDAD_MEDIA)

    if tipo in limites_texto.TIPOS_TEXTO_LIBRE:
        motivo = limites_texto.pide_transcripcion(nombre, atributo["prompt"])
        if motivo:
            _senal("pide_transcripcion",
                   f"{motivo}. La transcripción no va como atributo (el sistema la hace por "
                   "otra vía y el guardado rechaza estos atributos).",
                   SEVERIDAD_ALTA)

    # Redacción del prompt (fechas fijas, placeholders sin reemplazar, criterios que no
    # se pueden evaluar). Ver AuditorIA/senales_prompt.py.
    for clave, mensaje, severidad in senales_prompt.senales_de_texto(
            nombre, atributo["prompt"], tipo, campos_contexto=campos_contexto):
        _senal(clave, mensaje, severidad)

    return senales


def senales_de_plantilla(atributos: List[Dict[str, Any]],
                         campos_contexto: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Problemas que se detectan SIN IA leyendo la plantilla entera.

    Tres usos: se le dan masticadas al modelo en la revisión (garantizan que lo obvio y
    verificable no dependa de que el modelo lo note), alimentan el chequeo de salud de
    todas las plantillas de una campaña —que no llama a Gemini y no cuesta un token— y
    pintan el semáforo de cada atributo en el editor.

    Devuelve dicts y no frases sueltas porque el chequeo necesita agrupar por severidad
    y por atributo; `frases_de_senales` las aplana cuando hace falta texto plano.

    `atributos` viene en la forma canónica de `_atributo_actual`.
    """
    senales: List[Dict[str, Any]] = []
    suma_pesos = 0.0
    hay_criticos = False

    for i, attr in enumerate(atributos):
        # Hermanos = los ANTERIORES: así el nombre repetido se reporta sobre la segunda
        # aparición (la que sobra) y no sobre las dos.
        senales.extend(senales_de_atributo(attr, hermanos=atributos[:i],
                                           campos_contexto=campos_contexto))
        if attr["tipo"] == "critical_audit":
            hay_criticos = True
            suma_pesos += attr["ponderacion"]

    if hay_criticos and abs(suma_pesos - 100.0) > 0.01:
        senales.append({
            "clave": "pesos_no_suman",
            "mensaje": (f"Los pesos de los atributos de calidad suman {suma_pesos:g} (no 100). "
                        "El puntaje se normaliza igual, pero conviene revisarlo si el número "
                        "tenía que ser 100."),
            "severidad": SEVERIDAD_MEDIA,
            "atributo_id": None,
            "atributo": None,
        })
    return senales


def frases_de_senales(senales: List[Dict[str, Any]]) -> List[str]:
    """Solo los mensajes, para meterlos en un prompt o en un log."""
    return [s["mensaje"] for s in senales or []]


_SCHEMA_ATRIBUTO_REVISION = types.Schema(
    type=types.Type.OBJECT,
    required=["accion"],
    properties={
        "accion": types.Schema(
            type=types.Type.STRING,
            enum=["modificar", "agregar", "eliminar", "sin_cambios"],
            description="Qué hacer con este atributo.",
        ),
        "id": types.Schema(
            type=types.Type.INTEGER,
            description="ID del atributo existente (copialo tal cual del listado). Omitilo si accion='agregar'.",
        ),
        "nombre_actual": types.Schema(
            type=types.Type.STRING,
            description="Nombre con el que aparece hoy el atributo. Sirve para identificarlo si el ID falla.",
        ),
        "nombre": types.Schema(
            type=types.Type.STRING,
            description="Nombre NUEVO. Dejalo vacío si el nombre no cambia. Obligatorio si accion='agregar'.",
        ),
        "prompt": types.Schema(
            type=types.Type.STRING,
            description="Prompt NUEVO completo (no un diff, no 'igual que antes'). Vacío si el prompt no cambia. Obligatorio si accion='agregar'.",
        ),
        "tipo": types.Schema(
            type=types.Type.STRING,
            enum=TIPOS_ATRIBUTO + [_SIN_CAMBIO],
            description=f"Tipo de dato NUEVO, o '{_SIN_CAMBIO}' si no cambia. Obligatorio si accion='agregar'.",
        ),
        "opciones": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(type=types.Type.STRING),
            description=(
                "Lista COMPLETA de opciones que queda (no solo las que agregás) para "
                "enum/array_enum/critical_audit. Vacío = las opciones no cambian. En enum "
                "incluí siempre una salida segura ('Otros' / 'No aplica'); en critical_audit "
                "usá OK, NO OK, EC y sumá N/A si el criterio puede no aplicar."
            ),
        ),
        "es_opcional": types.Schema(
            type=types.Type.STRING,
            enum=["si", "no", _SIN_CAMBIO],
            description=(
                "'si' = la IA auditora puede dejarlo SIN responder cuando no hay evidencia. "
                f"'{_SIN_CAMBIO}' si no cambia. En critical_audit no se usa (ahí va la opción N/A)."
            ),
        ),
        "ponderacion": types.Schema(
            type=types.Type.NUMBER,
            description="Peso NUEVO para el puntaje (solo critical_audit). Poné -1 si el peso no cambia.",
        ),
        "motivos": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(type=types.Type.STRING),
            description="Por qué proponés este cambio, en lenguaje simple, para alguien sin conocimientos técnicos.",
        ),
    },
)

_SCHEMA_REVISION = types.Schema(
    type=types.Type.OBJECT,
    required=["diagnostico", "atributos"],
    properties={
        "diagnostico": types.Schema(
            type=types.Type.STRING,
            description="2 a 4 frases sobre cómo está la plantilla hoy y qué le falta. Lenguaje simple.",
        ),
        "cabecera": types.Schema(
            type=types.Type.OBJECT,
            properties={
                "nombre": types.Schema(type=types.Type.STRING, description="Nombre nuevo de la plantilla. Vacío si no cambia."),
                "descripcion": types.Schema(type=types.Type.STRING, description="Descripción nueva. Vacío si no cambia."),
                "system_prompt": types.Schema(type=types.Type.STRING, description="System Prompt nuevo COMPLETO. Vacío si no cambia."),
                "recordatorio": types.Schema(type=types.Type.STRING, description="Recordatorio nuevo COMPLETO. Vacío si no cambia."),
                "motivos": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(type=types.Type.STRING),
                    description="Qué cambiaste de la cabecera y por qué.",
                ),
            },
        ),
        "atributos": types.Schema(
            type=types.Type.ARRAY,
            items=_SCHEMA_ATRIBUTO_REVISION,
            description="Un item por atributo que proponés tocar (más los nuevos). Los que están bien podés omitirlos.",
        ),
        "notas": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(type=types.Type.STRING),
            description="Supuestos que tomaste y cosas que el usuario debería decidir a mano. Lenguaje simple.",
        ),
    },
)


def _fusionar_propuesta(actual: Dict[str, Any], propuesto: Dict[str, Any],
                        es_nuevo: bool) -> Dict[str, Any]:
    """Aplica sobre el atributo actual solo los campos que el modelo dijo cambiar.

    Devuelve el atributo COMPLETO como quedaría. Los sentinelas (texto vacío, tipo
    '_SIN_CAMBIO', ponderación negativa, lista de opciones vacía) significan
    "dejalo como está".
    """
    resultado = dict(actual)

    nombre = (propuesto.get("nombre") or "").strip()
    if nombre:
        resultado["nombre"] = nombre
    prompt = (propuesto.get("prompt") or "").strip()
    if prompt:
        resultado["prompt"] = prompt

    tipo = (propuesto.get("tipo") or "").strip()
    if tipo and tipo != _SIN_CAMBIO and tipo in TIPOS_ATRIBUTO:
        resultado["tipo"] = tipo

    opciones = propuesto.get("opciones")
    if isinstance(opciones, list):
        opciones = [str(o).strip() for o in opciones if str(o).strip()]
        if opciones:
            resultado["opciones"] = opciones

    opcional = (propuesto.get("es_opcional") or "").strip().lower()
    if opcional in ("si", "sí", "true"):
        resultado["es_opcional"] = True
    elif opcional in ("no", "false"):
        resultado["es_opcional"] = False

    ponderacion = propuesto.get("ponderacion")
    if ponderacion is not None and _a_float(ponderacion, -1.0) >= 0:
        resultado["ponderacion"] = _a_float(ponderacion)

    if es_nuevo:
        resultado.setdefault("DarAviso", False)
        resultado.setdefault("FrasesAviso", None)

    return _coherencia_atributo(resultado)


def _coherencia_atributo(attr: Dict[str, Any]) -> Dict[str, Any]:
    """Reglas del sistema que la propuesta tiene que respetar sí o sí.

    Son las mismas que aplica el editor a mano (ver frontend/static/js/plantillas.js):
    los tipos sin lista no llevan opciones, critical_audit tiene su enum canónico y
    resuelve el "no aplica" con la opción N/A (no con la marca de opcional), y un
    atributo de calidad con peso 0 no puntuaría.
    """
    tipo = attr.get("tipo")
    opciones = [str(o).strip() for o in (attr.get("opciones") or []) if str(o).strip()]

    if tipo not in TIPOS_CON_OPCIONES:
        opciones = []
    elif tipo == "critical_audit":
        if not opciones:
            opciones = ["OK", "NO OK", "EC"]
        else:
            # OK / NO OK son obligatorias: sin ellas AuditorIA/scoring.py no puede puntuar.
            canonicas = {str(o).strip().upper() for o in opciones}
            for obligatoria in ("NO OK", "OK"):
                if obligatoria not in canonicas:
                    opciones.insert(0, obligatoria)
    attr["opciones"] = opciones

    if tipo == "critical_audit":
        # El "no aplica" acá es la opción N/A (deja registro y renormaliza el puntaje),
        # no la marca de opcional.
        attr["es_opcional"] = False
        if _a_float(attr.get("ponderacion")) <= 0:
            attr["ponderacion"] = 1.0
    return attr


def _diff_atributo(actual: Dict[str, Any], propuesta: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Qué campos cambian de verdad. Se calcula acá y no se le cree al modelo: si dice
    que cambió algo y devolvió lo mismo, el usuario no tiene que ver un cambio vacío."""
    cambios: List[Dict[str, Any]] = []
    for campo, etiqueta in _ETIQUETA_CAMPO_ATRIBUTO.items():
        antes, despues = actual.get(campo), propuesta.get(campo)
        if campo == "ponderacion":
            iguales = abs(_a_float(antes) - _a_float(despues)) < 1e-9
        elif campo == "opciones":
            iguales = list(antes or []) == list(despues or [])
        elif campo == "es_opcional":
            iguales = bool(antes) == bool(despues)
        else:  # nombre, tipo, prompt
            iguales = (antes or "").strip() == (despues or "").strip()
        if not iguales:
            cambios.append({"campo": campo, "etiqueta": etiqueta, "antes": antes, "despues": despues})
    return cambios


def _volumen_afectado(uso: Optional[Dict[str, Any]], ventana_dias: Optional[int]) -> Optional[str]:
    """Cuánto material afecta el cambio, en número.

    "Lo ya auditado queda como está" no le dice nada a nadie; "4.312 respuestas en los
    últimos 90 días" es lo que permite decidir si el cambio vale el corte de la serie.
    """
    if not uso:
        return None
    respuestas = uso.get("respuestas") or 0
    revisiones = uso.get("revisiones") or 0
    if not respuestas and not revisiones:
        return None
    partes = []
    if respuestas:
        ventana = f" en los últimos {ventana_dias} días" if ventana_dias else ""
        partes.append(f"{respuestas:,}".replace(",", ".") + f" respuestas{ventana}")
    if revisiones:
        partes.append(f"{revisiones} revisiones de auditores")
    return "Tamaño del impacto: este atributo acumula " + " y ".join(partes) + "."


def _advertencias_cambio(accion: str, actual: Dict[str, Any], propuesta: Dict[str, Any],
                         cambios: List[Dict[str, Any]], uso: Optional[Dict[str, Any]] = None,
                         ventana_dias: Optional[int] = None) -> List[str]:
    """Consecuencias que el usuario tiene que ver ANTES de aceptar.

    No son errores: son cambios legítimos que rompen la comparación con lo ya auditado
    o que apagan un criterio. La plantilla no avisa sola de esto en ningún otro lado.
    Cuando hay datos de uso (ver AuditorIA/evidencia_plantilla.py) se agrega el tamaño
    del impacto en números.
    """
    avisos: List[str] = []
    campos = {c["campo"] for c in cambios}
    volumen = _volumen_afectado(uso, ventana_dias)

    if accion == "eliminar":
        avisos.append(
            "El atributo deja de auditarse de acá en adelante. Las auditorías ya hechas "
            "conservan lo que respondió, pero no vuelve a aparecer en las nuevas."
        )
        if volumen:
            avisos.append(volumen)
        return avisos

    if "tipo" in campos:
        avisos.append(
            f"Cambia el tipo de dato ({actual.get('tipo')} → {propuesta.get('tipo')}): "
            "lo ya auditado queda como está, pero de acá en adelante la respuesta tiene "
            "otra forma y las dos partes no se comparan entre sí en el dashboard."
        )
        if actual.get("tipo") == "critical_audit" and propuesta.get("tipo") != "critical_audit":
            avisos.append("Al dejar de ser Calidad ponderada, el atributo ya no suma al puntaje del llamado.")
        elif propuesta.get("tipo") == "critical_audit":
            avisos.append("Al pasar a Calidad ponderada, el atributo empieza a pesar en el puntaje del llamado.")

    if "nombre" in campos:
        avisos.append(
            "Renombrarlo cambia el título de la columna en Auditorías Realizadas y en los "
            "dashboards; las plantillas de columnas guardadas que lo nombraban dejan de encontrarlo."
        )

    if "opciones" in campos:
        # Si las opciones desaparecieron porque el tipo nuevo no lleva lista, ya lo dice
        # el aviso del cambio de tipo: repetirlo acá solo suma ruido.
        quitadas = ([o for o in (actual.get("opciones") or []) if o not in (propuesta.get("opciones") or [])]
                    if propuesta.get("tipo") in TIPOS_CON_OPCIONES else [])
        if quitadas:
            avisos.append(
                "Se quitan opciones (" + ", ".join(quitadas) + "): las auditorías viejas que las "
                "usaron las conservan, pero la IA ya no va a poder elegirlas."
            )
        if propuesta.get("tipo") in ("enum", "array_enum") and not _tiene_salida_segura(propuesta.get("opciones") or []):
            avisos.append(
                "La lista queda sin salida segura ('Otros' / 'No aplica'): si el caso real no "
                "está en la lista, la IA va a tener que elegir una opción incorrecta."
            )

    if "ponderacion" in campos and propuesta.get("tipo") == "critical_audit":
        avisos.append(
            f"Cambia el peso ({_a_float(actual.get('ponderacion')):g} → {_a_float(propuesta.get('ponderacion')):g}): "
            "los puntajes nuevos no son comparables con los viejos."
        )

    # El tamaño del impacto solo importa si el cambio efectivamente parte la serie.
    if volumen and ({"tipo", "nombre", "opciones", "ponderacion"} & campos):
        avisos.append(volumen)

    return avisos


def _motivo_bloqueo(propuesta: Dict[str, Any]) -> Optional[str]:
    """Motivo por el que este cambio no se puede aplicar, o None.

    Se chequea acá para no ofrecer un cambio que el guardado va a rebotar (o que
    dejaría la plantilla en un estado inválido). El frontend lo muestra deshabilitado
    con el motivo, en vez de dejar que falle recién al aplicar."""
    tipo = propuesta.get("tipo")
    if tipo in ("enum", "array_enum") and not propuesta.get("opciones"):
        return "la lista quedaría sin opciones cargadas"
    if tipo in limites_texto.TIPOS_TEXTO_LIBRE:
        try:
            return limites_texto.pide_transcripcion(propuesta.get("nombre"), propuesta.get("prompt"))
        except Exception:  # noqa: BLE001 - nunca romper la revisión por el detector
            return None
    return None


def _uso_de(evidencia: Optional[Dict[str, Any]], atributo_id: Any) -> Optional[Dict[str, Any]]:
    """Datos de uso de un atributo. Las claves pueden venir como int o como str según
    de dónde salga la evidencia (BD o JSON de un test/endpoint)."""
    if not evidencia or atributo_id is None:
        return None
    por_atributo = evidencia.get("por_atributo") or {}
    return por_atributo.get(atributo_id) or por_atributo.get(str(atributo_id))


def _restricciones_de(propuesta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    opciones = propuesta.get("opciones") or []
    return {"enum": opciones} if opciones else None


def _item_revision(accion: str, actual: Optional[Dict[str, Any]], propuesta: Dict[str, Any],
                   cambios: List[Dict[str, Any]], motivos: Optional[List[Any]] = None,
                   evidencia: Optional[Dict[str, Any]] = None,
                   ventana_dias: Optional[int] = None) -> Dict[str, Any]:
    """Una tarjeta de la propuesta, tal como la consume el editor.

    Vive aparte porque la arman dos caminos: la revisión de la plantilla entera y el
    "pedir otra versión" de un cambio suelto. Si cada uno la armara por su lado, el
    segundo terminaría sin advertencias o sin el bloqueo, que es justo lo que evita que
    se aplique algo que el guardado va a rechazar.
    """
    propuesta["restricciones"] = _restricciones_de(propuesta)
    campos = {c["campo"] for c in cambios}
    impacto = "alto" if ({"tipo", "nombre"} & campos or accion == "agregar") else (
        "medio" if {"opciones", "ponderacion", "es_opcional"} & campos else "bajo")
    return {
        "accion": accion,
        "id": actual["id"] if actual else None,
        "nombre_actual": actual["nombre"] if actual else propuesta["nombre"],
        "cambios": cambios,
        "propuesta": propuesta,
        "motivos": [str(m) for m in (motivos or [])],
        "advertencias": _advertencias_cambio(accion, actual or {}, propuesta, cambios,
                                             uso=_uso_de(evidencia, (actual or {}).get("id")),
                                             ventana_dias=ventana_dias),
        "impacto": impacto,
        # Motivo por el que el guardado lo rechazaría: el frontend lo deshabilita.
        "bloqueado": _motivo_bloqueo(propuesta),
    }


def revisar_plantilla(
    plantilla: Dict[str, Any],
    instruccion_usuario: Optional[str] = None,
    foco: Optional[List[str]] = None,
    permitir_eliminar: bool = False,
    evidencia: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = None,
    campos_contexto: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Revisa la plantilla completa y devuelve un plan de cambios. NO guarda nada.

    Args:
        plantilla: estado actual {nombre, descripcion, system_prompt, recordatorio,
            atributos:[{id, nombre, prompt, tipo, opciones|restricciones, es_opcional,
            ponderacion, orden}]}. Es lo que se está viendo en el editor, así que
            incluye los cambios todavía no guardados de la cabecera.
        instruccion_usuario: pedido puntual ("sumá un criterio de mora", "sacale peso
            al saludo"). Vacío = revisión general.
        foco: claves de FOCOS_REVISION a priorizar.
        permitir_eliminar: si False, las bajas propuestas se descartan (el usuario no
            habilitó que la IA saque atributos).
        evidencia: cómo viene funcionando la plantilla (ver
            AuditorIA/evidencia_plantilla.py). Sin esto la revisión opina sobre el texto;
            con esto razona sobre lo que pasó: qué criterio no discrimina, qué opción no
            se elige nunca y dónde el auditor corrige a la IA.

    Returns:
        {diagnostico, senales, cabecera, atributos:[...], notas, resumen}
        Cada atributo trae `cambios` (diff campo por campo), `propuesta` (el atributo
        completo listo para guardar) y `advertencias`.
    """
    plantilla = plantilla or {}
    atributos_crudos = plantilla.get("atributos") or []
    if not isinstance(atributos_crudos, list):
        raise ValueError("La plantilla no trae una lista de atributos válida.")

    actuales = normalizar_atributos(atributos_crudos)
    por_id = {str(a["id"]): a for a in actuales if a.get("id") is not None}
    por_nombre = {_clave_nombre(a["nombre"]): a for a in actuales if a["nombre"]}
    orden_maximo = max([a["orden"] for a in actuales], default=-1)
    ventana_evidencia = (evidencia or {}).get("ventana_dias")
    senales = senales_de_plantilla(actuales, campos_contexto=campos_contexto)
    if evidencia and evidencia.get("por_atributo"):
        senales = senales + evidencia_plantilla.senales_de_evidencia(evidencia, actuales)

    # ---- Prompt --------------------------------------------------------------
    system_instruction = (
        "Sos un ingeniero de prompts experto y analista de calidad senior de contact "
        "center. Tu trabajo es AUDITAR UNA PLANTILLA DE AUDITORÍA COMPLETA y proponer "
        "cómo mejorarla, tanto en la redacción como en la ESTRUCTURA.\n\n"
        + _CONTEXTO_DOMINIO
        + call_details.bloque_para_prompt(campos_contexto)
        + "\n\nQUÉ PODÉS CAMBIAR DE CADA ATRIBUTO:\n"
        "- El prompt (qué evalúa y cómo) y el nombre.\n"
        "- El TIPO DE DATO. Es el cambio de más valor y el que nadie hace: un criterio de "
        "cumplimiento guardado como boolean no puntúa (debería ser critical_audit); una "
        "clasificación escrita como texto libre no se puede tabular (debería ser enum); un "
        "enum de dos opciones que en realidad es un Sí/No conviene que sea boolean.\n"
        "- Las OPCIONES de las listas (enum, array_enum y critical_audit): agregar la salida "
        "segura que falta, sumar N/A donde el criterio puede no aplicar, partir una opción "
        "ambigua en dos, sacar opciones que se solapan. Devolvé SIEMPRE la lista completa final.\n"
        "- La marca de OPCIONAL (que la IA pueda dejarlo sin responder si no hay evidencia).\n"
        "- La PONDERACIÓN de los atributos de calidad.\n"
        "- Podés AGREGAR atributos que falten para lo que la plantilla quiere medir.\n"
        + ("- Podés proponer ELIMINAR atributos duplicados, solapados o que no aportan.\n"
           if permitir_eliminar else
           "- NO propongas eliminar atributos: el usuario no lo habilitó. Si uno sobra o está "
           "duplicado, decilo en 'notas' y listo.\n")
        + "\nCÓMO RESPONDER:\n"
        "- Un item por atributo que tocás. Los que están bien, omitilos (o accion='sin_cambios').\n"
        "- Copiá el 'id' del atributo tal cual viene en el listado: es como se identifica.\n"
        "- Devolvé SOLO los campos que cambian; los que quedan igual, vacíos (o "
        f"'{_SIN_CAMBIO}' / -1 según el campo). Cuando cambiás el prompt devolvelo COMPLETO, "
        "no un fragmento ni 'igual que antes más esto'.\n"
        "- En 'motivos' explicá el porqué de cada cambio en lenguaje simple: el que decide si "
        "acepta es alguien de Calidad, no un técnico.\n"
        "\nSÉ CONSERVADOR Y CONCRETO:\n"
        "- No reescribas por reescribir: si un atributo está bien, dejalo. Una revisión de 4 "
        "cambios que valen la pena es mejor que una de 20 cosméticos.\n"
        "- No renombres atributos salvo que el nombre confunda de verdad: el nombre es el título "
        "de la columna en los tableros y en los reportes guardados.\n"
        "- Cambiá el tipo de dato solo cuando el actual impide medir lo que el atributo quiere "
        "medir, y explicá el porqué: la plantilla ya auditó llamados con el tipo viejo.\n"
        "- No inventes datos de la campaña que no estén en la plantilla: usá [placeholders]."
        + (
            "\n\nUSÁ LOS DATOS REALES QUE TE PASO (sección 'CÓMO VIENE FUNCIONANDO'):\n"
            "- Es lo que efectivamente respondió la IA auditando con esta plantilla, y en qué la "
            "corrigieron los auditores. Vale más que cualquier impresión que te dé leer el prompt.\n"
            "- Un criterio donde el 98% de las respuestas es el mismo valor no está midiendo nada: "
            "o el prompt lo empuja siempre para el mismo lado, o el criterio sobra.\n"
            "- Una opción que no se eligió NUNCA sobra o no está explicada en el prompt. Si en cambio "
            "'Otros'/'No aplica' se lleva una porción grande, a la lista le faltan opciones: "
            "proponelas concretas.\n"
            "- Donde el auditor corrige seguido a la IA, el prompt es ambiguo: reescribilo apuntando "
            "exactamente a ese desacuerdo y citá en 'motivos' la corrección que se repite.\n"
            "- Fundamentá con el número cuando lo tengas ('el 41% cae en Otros'), no con generalidades."
            if evidencia and evidencia.get("por_atributo") else ""
        )
    )

    plantilla_ctx = {
        "nombre": plantilla.get("nombre"),
        "descripcion": plantilla.get("descripcion"),
        "system_prompt": plantilla.get("system_prompt"),
        "recordatorio": plantilla.get("recordatorio"),
        "empresa": plantilla.get("empresa"),
        "campana": plantilla.get("campana"),
    }
    atributos_ctx = [
        {
            "id": a["id"], "nombre": a["nombre"], "tipo": a["tipo"], "opciones": a["opciones"],
            "es_opcional": a["es_opcional"], "ponderacion": a["ponderacion"], "prompt": a["prompt"],
        }
        for a in actuales
    ]

    partes = [
        "=== PLANTILLA ACTUAL (cabecera) ===\n" + json.dumps(plantilla_ctx, ensure_ascii=False, indent=2),
        f"=== ATRIBUTOS ACTUALES ({len(atributos_ctx)}) ===\n"
        + json.dumps(atributos_ctx, ensure_ascii=False, indent=2),
    ]
    if senales:
        partes.append(
            "=== SEÑALES DETECTADAS AUTOMÁTICAMENTE (verificalas y resolvé las que correspondan) ===\n"
            + "\n".join(f"- {s}" for s in frases_de_senales(senales))
        )
    bloque_evidencia = evidencia_plantilla.bloque_para_prompt(evidencia or {}, actuales) if evidencia else ""
    if bloque_evidencia:
        partes.append(
            "=== CÓMO VIENE FUNCIONANDO ESTA PLANTILLA (datos reales, no opinión) ===\n"
            + bloque_evidencia
        )
    focos_pedidos = [FOCOS_REVISION[f] for f in (foco or []) if f in FOCOS_REVISION]
    if focos_pedidos:
        partes.append(
            "=== EN QUÉ QUIERE QUE TE CONCENTRES (sin dejar de mirar el resto) ===\n"
            + "\n".join(f"- {f}" for f in focos_pedidos)
        )
    if (instruccion_usuario or "").strip():
        partes.append("=== PEDIDO EXPLÍCITO DEL USUARIO (tiene prioridad) ===\n" + instruccion_usuario.strip())

    resultado = _generar_json(system_instruction, "\n\n".join(partes), _SCHEMA_REVISION,
                              temperatura=0.4, user_id=user_id, ref_label="revisar_plantilla")

    # ---- Normalización: se compara contra lo actual y se arma el diff ---------
    cabecera_ia = resultado.get("cabecera") or {}
    cambios_cabecera = []
    valores_cabecera: Dict[str, Any] = {}
    for campo, etiqueta in (("nombre", "Nombre"), ("descripcion", "Descripción"),
                            ("system_prompt", "System Prompt"), ("recordatorio", "Recordatorio")):
        propuesto = (cabecera_ia.get(campo) or "").strip() if isinstance(cabecera_ia.get(campo), str) else ""
        actual_valor = (plantilla.get(campo) or "").strip()
        valores_cabecera[campo] = propuesto or actual_valor
        if propuesto and propuesto != actual_valor:
            cambios_cabecera.append({"campo": campo, "etiqueta": etiqueta,
                                     "antes": actual_valor, "despues": propuesto})

    atributos_salida: List[Dict[str, Any]] = []
    nuevos = 0
    ids_usados = set()
    for item in resultado.get("atributos") or []:
        if not isinstance(item, dict):
            continue
        accion = (item.get("accion") or "").strip().lower()
        if accion not in ("modificar", "agregar", "eliminar", "sin_cambios"):
            accion = "modificar"

        # Resolver a qué atributo existente se refiere: por id y, si el modelo lo
        # inventó o lo perdió, por nombre. Un id que no existe en ESTA plantilla no se
        # acepta nunca: sería tocar un atributo de otra.
        actual = None
        if item.get("id") is not None:
            actual = por_id.get(str(item.get("id")))
        if actual is None:
            for clave in (_clave_nombre(item.get("nombre_actual")), _clave_nombre(item.get("nombre"))):
                if clave and clave in por_nombre:
                    actual = por_nombre[clave]
                    break

        if accion == "eliminar":
            if actual is None or not permitir_eliminar:
                continue
            propuesta = dict(actual)
            item_baja = _item_revision("eliminar", actual, propuesta, [],
                                       motivos=item.get("motivos"), evidencia=evidencia,
                                       ventana_dias=ventana_evidencia)
            item_baja["impacto"] = "alto"
            item_baja["bloqueado"] = None
            atributos_salida.append(item_baja)
            ids_usados.add(str(actual["id"]))
            continue

        if actual is None:
            # No matcheó con ninguno: es un atributo nuevo (aunque el modelo dijera "modificar").
            accion = "agregar"
            nuevos += 1
            base = {
                "id": None, "nombre": "", "prompt": "", "tipo": "string", "opciones": [],
                "es_opcional": False, "ponderacion": 0.0, "orden": orden_maximo + nuevos,
                "DarAviso": False, "FrasesAviso": None,
            }
            propuesta = _fusionar_propuesta(base, item, es_nuevo=True)
            if not propuesta["nombre"] or not propuesta["prompt"]:
                continue  # atributo nuevo sin lo mínimo: no se ofrece
            cambios = [{"campo": "nuevo", "etiqueta": "Atributo nuevo", "antes": None,
                        "despues": propuesta["nombre"]}]
        else:
            if accion == "sin_cambios":
                continue
            accion = "modificar"
            propuesta = _fusionar_propuesta(actual, item, es_nuevo=False)
            cambios = _diff_atributo(actual, propuesta)
            if not cambios:
                continue  # el modelo dijo que cambiaba algo pero devolvió lo mismo
            ids_usados.add(str(actual["id"]))

        atributos_salida.append(_item_revision(
            accion, actual, propuesta, cambios,
            motivos=item.get("motivos"), evidencia=evidencia, ventana_dias=ventana_evidencia,
        ))

    resumen = {
        "modificar": sum(1 for a in atributos_salida if a["accion"] == "modificar"),
        "agregar": sum(1 for a in atributos_salida if a["accion"] == "agregar"),
        "eliminar": sum(1 for a in atributos_salida if a["accion"] == "eliminar"),
        "sin_cambios": len(actuales) - len(ids_usados),
        "cabecera": len(cambios_cabecera),
    }
    resumen["total"] = resumen["modificar"] + resumen["agregar"] + resumen["eliminar"] + resumen["cabecera"]

    return {
        "diagnostico": str(resultado.get("diagnostico") or "").strip(),
        "senales": senales,
        # Resumen de con qué datos se revisó (la UI necesita poder decir "esto sale de
        # 1.204 auditorías" o, si no hay, que la revisión fue solo sobre el texto).
        "evidencia": {
            "hay_datos": bool(evidencia and evidencia.get("por_atributo")),
            "ventana_dias": ventana_evidencia,
            "auditorias": (evidencia or {}).get("auditorias", 0),
            "desde": (evidencia or {}).get("desde"),
        },
        "cabecera": {
            "cambia": bool(cambios_cabecera),
            "cambios": cambios_cabecera,
            "valores": valores_cabecera,
            "motivos": [str(m) for m in (cabecera_ia.get("motivos") or [])],
        },
        "atributos": atributos_salida,
        "notas": [str(n) for n in (resultado.get("notas") or [])],
        "resumen": resumen,
    }


# ---------------------------------------------------------------------------- #
# 4) Re-pedir UN cambio de la revisión ("no me convence, hacelo así")           #
# ---------------------------------------------------------------------------- #
# Sin esto, la única salida cuando una tarjeta de la propuesta no convence era
# descartarla entera y volver a revisar la plantilla completa: otro llamado caro, y
# encima el resto de la propuesta (que sí gustaba) se perdía. Acá se re-pregunta por un
# solo atributo, con la propuesta anterior como punto de partida.
def revisar_atributo(
    plantilla: Dict[str, Any],
    atributo_id: Optional[int],
    instruccion_usuario: str,
    propuesta_previa: Optional[Dict[str, Any]] = None,
    accion: str = "modificar",
    evidencia: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = None,
    campos_contexto: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Devuelve OTRA versión de un cambio propuesto, con el pedido del usuario.

    Args:
        plantilla: igual que en revisar_plantilla (los atributos, de la BD).
        atributo_id: el atributo existente sobre el que se propone; None si el cambio
            era un alta.
        instruccion_usuario: qué quiere distinto ("que no cambie el nombre", "sumale la
            opción Promesa de pago", "más corto").
        propuesta_previa: la propuesta que se le mostró y no le gustó. Es el punto de
            partida: sin esto, el modelo empieza de cero y se pierde lo que sí servía.
        accion: "modificar" o "agregar" (la misma que traía la tarjeta).

    Returns:
        Un item con la MISMA forma que los de revisar_plantilla, listo para reemplazar
        la tarjeta en pantalla.
    """
    if not (instruccion_usuario or "").strip():
        raise ValueError("Decile qué querés distinto para que pueda proponer otra versión.")

    actuales = normalizar_atributos((plantilla or {}).get("atributos"))
    actual = next((a for a in actuales if str(a["id"]) == str(atributo_id)), None) if atributo_id is not None else None
    if actual is None and accion != "agregar":
        # El id no es de esta plantilla: se trata como alta en vez de tocar otro atributo.
        accion = "agregar"

    orden_maximo = max([a["orden"] for a in actuales], default=-1)
    base = dict(actual) if actual else {
        "id": None, "nombre": "", "prompt": "", "tipo": "string", "opciones": [],
        "es_opcional": False, "ponderacion": 0.0, "orden": orden_maximo + 1,
        "DarAviso": False, "FrasesAviso": None,
    }

    # La propuesta anterior es el punto de partida, no el estado guardado: si el usuario
    # solo pide "cambiale una opción", todo lo demás que ya había propuesto se conserva.
    partida = dict(base)
    if propuesta_previa:
        for campo in ("nombre", "prompt", "tipo", "opciones", "es_opcional", "ponderacion"):
            if campo in propuesta_previa and propuesta_previa[campo] not in (None, ""):
                partida[campo] = propuesta_previa[campo]
        partida = _coherencia_atributo(partida)

    system_instruction = (
        "Sos un ingeniero de prompts experto y analista de calidad senior de contact "
        "center. Ya propusiste un cambio sobre un atributo de una plantilla de auditoría "
        "y al usuario no lo convenció. Tenés que proponer OTRA versión de ESE cambio.\n\n"
        + _CONTEXTO_DOMINIO
        + call_details.bloque_para_prompt(campos_contexto)
        + "\n\nREGLAS DE ESTA RESPUESTA:\n"
        "- Devolvés UN solo atributo, el mismo del que se está hablando.\n"
        "- Partí de tu propuesta anterior y cambiá lo que el usuario te pide; no vuelvas a "
        "empezar de cero ni deshagas lo que no te pidió que cambies.\n"
        "- Si el usuario te pide que NO toques algo, devolvé ese campo vacío (o "
        f"'{_SIN_CAMBIO}' / -1 según el campo) para que quede como está.\n"
        "- Devolvé el prompt COMPLETO si lo cambiás, y la lista COMPLETA de opciones si "
        "cambian las opciones.\n"
        "- En 'motivos' explicá en lenguaje simple qué hiciste distinto esta vez."
    )

    contexto = {
        "plantilla": {"nombre": plantilla.get("nombre"), "descripcion": plantilla.get("descripcion"),
                      "recordatorio": plantilla.get("recordatorio")},
        "otros_atributos": [{"nombre": a["nombre"], "tipo": a["tipo"]} for a in actuales
                            if not actual or a["id"] != actual["id"]],
    }
    partes = [
        "=== CONTEXTO DE LA PLANTILLA ===\n" + json.dumps(contexto, ensure_ascii=False, indent=2),
        "=== ATRIBUTO COMO ESTÁ HOY ===\n" + json.dumps(
            {k: base[k] for k in ("nombre", "tipo", "opciones", "es_opcional", "ponderacion", "prompt")},
            ensure_ascii=False, indent=2),
        "=== LO QUE HABÍAS PROPUESTO (y no convenció) ===\n" + json.dumps(
            {k: partida[k] for k in ("nombre", "tipo", "opciones", "es_opcional", "ponderacion", "prompt")},
            ensure_ascii=False, indent=2),
        "=== QUÉ QUIERE EL USUARIO DISTINTO (tiene prioridad) ===\n" + instruccion_usuario.strip(),
    ]
    uso = _uso_de(evidencia, atributo_id)
    if uso:
        bloque = evidencia_plantilla.bloque_para_prompt(
            {"por_atributo": {atributo_id: uso},
             "ventana_dias": (evidencia or {}).get("ventana_dias"),
             "auditorias": (evidencia or {}).get("auditorias"),
             "desde": (evidencia or {}).get("desde")},
            [actual or base],
        )
        if bloque:
            partes.append("=== CÓMO VIENE FUNCIONANDO ESTE ATRIBUTO (datos reales) ===\n" + bloque)

    item = _generar_json(system_instruction, "\n\n".join(partes), _SCHEMA_ATRIBUTO_REVISION,
                         temperatura=0.5, user_id=user_id, ref_label="revisar_atributo")

    propuesta = _fusionar_propuesta(partida, item, es_nuevo=actual is None)
    if actual is None:
        cambios = [{"campo": "nuevo", "etiqueta": "Atributo nuevo", "antes": None,
                    "despues": propuesta["nombre"]}]
        accion_final = "agregar"
    else:
        cambios = _diff_atributo(actual, propuesta)
        accion_final = "modificar"

    resultado = _item_revision(accion_final, actual, propuesta, cambios,
                               motivos=item.get("motivos"), evidencia=evidencia,
                               ventana_dias=(evidencia or {}).get("ventana_dias"))
    # Sin cambios contra lo guardado: se avisa en vez de devolver una tarjeta vacía que
    # el editor no sabría dibujar.
    resultado["sin_cambios"] = not cambios
    return resultado
