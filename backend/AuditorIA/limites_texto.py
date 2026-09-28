"""Tope de largo de los atributos de texto libre (y freno al "atributo transcripción").

POR QUÉ EXISTE
--------------
Un atributo de tipo texto libre (`string` / `array_string`) no tenía ningún límite: lo
que devolviera la IA se guardaba tal cual en calidad.AuditoriaDetalles. Varias campañas
aprovecharon eso para pedir la TRANSCRIPCIÓN del llamado adentro de un atributo
("Transcribí la conversación completa", "detallá palabra por palabra lo que dijeron").

Es la peor forma posible de conseguir algo que el sistema ya hace mejor:

  * Se paga como tokens de SALIDA (los más caros) en cada auditoría, y para colmo se
    paga aunque el llamado se audite en modo sync.
  * La transcripción queda enterrada como texto plano en una celda de la grilla, sin
    hablantes, sin tiempos y sin poder seguirla contra el audio.
  * Cuanto más largo el JSON de salida, más chances de que el modelo lo corte a mitad
    ("Unterminated string") y se pierda la auditoría ENTERA, no solo ese atributo.
  * Ya existe la vía correcta: tildar "Transcripción" al lanzar la auditoría (una sola
    lectura del audio, ver gemini.py::procesar_respuesta_combinada) o encolarla después
    desde "Auditorías Realizadas" (AuditorIA/transcripcion_cola.py, en modo batch, a
    mitad de precio).

LAS TRES CAPAS DE LA CONTRAMEDIDA
----------------------------------
1. ALTA/EDICIÓN del atributo (`validar_atributo`): si el prompt pide derecho viejo la
   transcripción del llamado, no se guarda y el error explica cuál es el camino bueno.
   El patrón es a propósito estrecho —exige el pedido explícito sobre TODO el llamado—
   para no bloquear cosas legítimas como "transcribí la frase exacta del saludo".
2. GENERACIÓN (`instruccion_texto_breve` + `max_length` en el response_schema, ver
   gemini.py::prompt): al modelo se le dice el tope y se le pone en el esquema. Esto es
   lo que evita el gasto: el texto largo no llega a generarse.
3. GUARDADO (`recortar_valor`, ver sql_a_Claude.auditoria_a_SQL): red de contención. El
   modelo puede ignorar el tope del esquema, y las plantillas viejas siguen teniendo
   atributos redactados como pedido de transcripción. Lo que exceda se recorta y queda
   logueado.

Todo el módulo se apaga poniendo `ATRIBUTO_TEXTO_MAX_CARACTERES = 0` en la config
(desactiva tope, instrucción, `max_length` y recorte; la validación del alta se controla
aparte con `ATRIBUTO_BLOQUEAR_TRANSCRIPCION`).
"""
import logging
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)

# Tipos cuyo valor es texto que escribe la IA. Son los únicos que pueden crecer sin
# control: el resto (boolean, enum, números) ya está acotado por su propio tipo.
TIPOS_TEXTO_LIBRE = {"string", "array_string"}

# Marca que se deja cuando un valor se recorta al guardar, para que en la grilla se vea
# que el texto está incompleto y no parezca que la IA cortó la frase por su cuenta.
MARCA_RECORTE = "… [recortado]"


def max_caracteres() -> int:
    """Tope de caracteres por atributo de texto libre. 0 = sin tope (todo desactivado)."""
    try:
        return int(getattr(settings, "ATRIBUTO_TEXTO_MAX_CARACTERES", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _bloquea_transcripcion() -> bool:
    return bool(getattr(settings, "ATRIBUTO_BLOQUEAR_TRANSCRIPCION", True))


def _normalizar(texto: Optional[str]) -> str:
    """Minúsculas, sin acentos y con los espacios colapsados, para poder matchear."""
    if not texto:
        return ""
    plano = unicodedata.normalize("NFKD", str(texto))
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", plano.lower()).strip()


# El "objeto" del pedido: la interacción entera. Es la diferencia entre "transcribí el
# llamado" (lo que queremos frenar) y "transcribí la frase exacta del saludo" (legítimo,
# y corto).
_LLAMADO = (
    r"(?:el |la |los |las |todo el |toda la |ese |este )?"
    r"(?:llamad[oa]s?|audios?|conversacion(?:es)?|dialogos?|interaccion(?:es)?|"
    r"charlas?|grabacion(?:es)?|chats?|contactos?)"
)
_LITERAL = r"(?:completa?o?s?|integra?s?|entera?s?|literal(?:mente)?|textual(?:mente)?|exacta?s?|fiel(?:mente)?)"

# (patrón, motivo legible). El primero que matchea es el que se reporta.
_PATRONES_TRANSCRIPCION: List[Tuple[str, str]] = [
    # "transcribí el llamado", "transcribir la conversación", "transcribime el audio"
    (rf"transcrib\w*\b(?:\s+\w+){{0,3}}?\s+{_LLAMADO}\b", "pide transcribir la interacción"),
    # "la transcripción completa", "transcripción textual del llamado"
    (rf"transcripcion(?:es)?\b(?:\s+\w+){{0,3}}?\s+{_LITERAL}\b", "pide la transcripción completa"),
    # "escribí / devolvé / incluí ... la transcripción"
    (
        r"(?:escrib\w*|redact\w*|devolv\w*|devuelv\w*|inclu\w*|adjunt\w*|pega\w*|copia\w*|"
        r"colo[cq]\w*|complet\w*|dame|genera\w*|arma\w*|detalla\w*|transcrib\w*)"
        r"[^.;]{0,40}\btranscripcion(?:es)?\b",
        "pide devolver la transcripción",
    ),
    (r"palabra por palabra", "pide texto palabra por palabra"),
    (r"\bverbatim\b", "pide texto verbatim"),
    (r"al pie de la letra", "pide texto al pie de la letra"),
    # "todo lo que dijo el operador", "todo lo que se dijo en el llamado"
    (r"todo lo que (?:se )?(?:dij[oe]|dijeron|hablaron|conversaron|menciona\w*)", "pide reproducir todo lo dicho"),
    (rf"{_LLAMADO}\s+{_LITERAL}\s+en\s+texto", "pide la interacción completa en texto"),
]

_PATRONES_COMPILADOS = [(re.compile(p), motivo) for p, motivo in _PATRONES_TRANSCRIPCION]

# En el NOMBRE del atributo alcanza con la palabra: nadie llama "Transcripción" a un
# atributo que no lo sea... salvo que el prompt acote el alcance (ver abajo).
_NOMBRE_TRANSCRIPCION = re.compile(r"transcri(?:pcion|bir|pto|pta)")

# Pedidos ACOTADOS: citar un tramo corto del llamado es un criterio de auditoría
# legítimo y barato ("transcribí los últimos 20 segundos" para ver cómo se cortó la
# llamada; "la frase exacta del saludo" como evidencia). No es lo que este módulo viene
# a frenar, y bloquearlos rompería plantillas que hoy funcionan bien.
_ACOTA_ALCANCE = re.compile(
    r"\b(?:ultim|penultim|primer)\w*\s+\d+\s*(?:segundos?|minutos?|frases?|turnos?|intervenciones?|lineas?)"
    r"|\b(?:solo|solamente|unicamente)\s+(?:el|la|los|las)\s+"
    r"(?:parte|fragmento|tramo|frase|frases|momento|minuto|segundos)"
    r"|\b(?:fragmento|extracto|tramo|cita textual|la frase exacta|las frases exactas)\b"
)

# ...salvo que además pida el llamado entero: ahí la acotación no salva nada.
_PIDE_TODO = re.compile(
    r"\b(?:completa?o?s?|integra|entera|de principio a fin|palabra por palabra|verbatim|"
    r"al pie de la letra|de punta a punta)\b"
)


def pide_transcripcion(nombre: Optional[str], prompt: Optional[str]) -> Optional[str]:
    """Devuelve el motivo si el atributo pide la transcripción del llamado, o None.

    Solo tiene sentido llamarla para tipos de texto libre: en un boolean o un enum el
    valor ya está acotado y la palabra "transcripción" suele ser una referencia legítima
    ("según la transcripción, ¿saludó?").
    """
    texto = _normalizar(prompt)

    # Un pedido acotado a un tramo corto no es "la transcripción del llamado": se deja
    # pasar aunque el atributo se llame "Transcripción". Lo que sí se frena es el que
    # acota y a la vez pide todo ("transcribí completa la última parte... de principio a fin").
    if texto and _ACOTA_ALCANCE.search(texto) and not _PIDE_TODO.search(texto):
        return None

    if _NOMBRE_TRANSCRIPCION.search(_normalizar(nombre)):
        return "el nombre del atributo es una transcripción"
    if not texto:
        return None
    for patron, motivo in _PATRONES_COMPILADOS:
        if patron.search(texto):
            return motivo
    return None


def _mensaje_rechazo(nombre: str, motivo: str) -> str:
    tope = max_caracteres()
    limite = f" Un atributo de texto libre es para respuestas breves (tope {tope} caracteres, lo que exceda se recorta al guardar)." if tope else ""
    return (
        f"El atributo «{nombre}» {motivo}, y la transcripción no va como atributo. "
        "El sistema ya la hace, mejor y más barata: tildá «Transcripción» al lanzar la "
        "auditoría, o pedila después desde «Auditorías Realizadas» (botón Transcribir), "
        "que la resuelve en modo batch. Pedirla dentro de un atributo se paga como "
        "tokens de salida en cada llamado y encima arriesga que el JSON se corte y se "
        "pierda la auditoría entera." + limite
    )


def validar_atributo(atributo: Dict[str, Any]) -> None:
    """Valida un atributo antes de guardarlo. Lanza ValueError con el motivo.

    Se aplica en el alta y en la edición (Plantillas_prompts), así que cubre tanto el
    editor de plantillas como las plantillas que propone el asistente de IA.
    """
    if not _bloquea_transcripcion():
        return
    tipo = str(atributo.get("tipo") or "").strip().lower()
    if tipo not in TIPOS_TEXTO_LIBRE:
        return
    nombre = str(atributo.get("nombre") or "").strip()
    motivo = pide_transcripcion(nombre, atributo.get("prompt"))
    if motivo:
        raise ValueError(_mensaje_rechazo(nombre or "sin nombre", motivo))


def validar_atributos(atributos: Iterable[Dict[str, Any]]) -> None:
    for atributo in atributos or []:
        validar_atributo(atributo)


def instruccion_texto_breve(nombres: List[str]) -> str:
    """Bloque que se agrega al prompt cuando la plantilla tiene atributos de texto libre.

    El `max_length` del esquema es el tope duro, pero el modelo trabaja mucho mejor
    cuando además se le explica qué se espera: una respuesta breve y no un relato del
    llamado. Sin esto, un atributo llamado "Feedback" se llevaba media auditoría.
    """
    tope = max_caracteres()
    if not tope or not nombres:
        return ""
    lista = ", ".join(f"'{n}'" for n in nombres)
    return (
        "\n\n--- ATRIBUTOS DE TEXTO LIBRE ---\n"
        f"Estos atributos se responden con texto: {lista}.\n"
        f"Cada uno admite como MÁXIMO {tope} caracteres: escribí una respuesta breve y "
        "concreta (una o dos frases, o una cita corta si el criterio pide evidencia "
        "textual). Lo que exceda ese tope se recorta al guardar, así que no sirve de nada "
        "extenderse.\n"
        "NUNCA transcribas la interacción ni reproduzcas el diálogo completo dentro de un "
        "atributo, aunque el enunciado del atributo parezca pedirlo: la transcripción se "
        "obtiene por otra vía y no es tarea de la auditoría de calidad."
    )


def recortar_valor(valor: Any, atributo_id: Any = None, id_aplicativo: Any = None) -> Any:
    """Recorta un valor de texto que supere el tope. Red de contención del guardado.

    Acepta el valor tal como llega del DataFrame: texto, o lista/tupla (array_string),
    donde el tope aplica a CADA elemento. Cualquier otro tipo pasa intacto.
    """
    tope = max_caracteres()
    if not tope:
        return valor

    if isinstance(valor, (list, tuple)):
        recortados = [recortar_valor(v, atributo_id, id_aplicativo) for v in valor]
        return type(valor)(recortados) if isinstance(valor, tuple) else recortados

    if not isinstance(valor, str) or len(valor) <= tope:
        return valor

    logger.warning(
        "Atributo %s del llamado %s devolvió %s caracteres (tope %s): se recorta. "
        "Suele ser un atributo redactado como pedido de transcripción; revisá la plantilla.",
        atributo_id, id_aplicativo, len(valor), tope,
    )
    corte = max(tope - len(MARCA_RECORTE), 0)
    return valor[:corte].rstrip() + MARCA_RECORTE
