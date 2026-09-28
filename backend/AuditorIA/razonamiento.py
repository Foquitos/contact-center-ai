"""Nivel de razonamiento (thinking) con el que audita cada plantilla.

Reemplaza al catálogo de modelos como perilla de costo/calidad: todas las
plantillas auditan con el MISMO modelo (`modelos_ia.MODELO_IA_DEFAULT`) y lo que
cambia de una a otra es cuánto piensa Gemini antes de responder.

POR QUÉ POR NIVEL Y NO POR CANTIDAD DE TOKENS
---------------------------------------------
Hasta Gemini 3.5 el techo del pensamiento se fijaba con `thinking_budget` (un
número de tokens). En la generación 3.x ese parámetro dejó de respetarse: la API
lo acepta sin error, pero el modelo lo ignora. Verificado en vivo el 2026-08-19
contra gemini-3.7-flash, con una tarea de razonamiento pesado:

    thinking_budget=512    ->  1.150 tokens de pensamiento  (2,2x el "tope")
    thinking_budget=2048   ->  1.091
    thinking_budget=8192   ->  1.487 / 1.566
    sin thinking_config    ->  1.676
    thinking_level=LOW     ->  1.161
    thinking_level=MEDIUM  ->  2.061
    thinking_level=HIGH    ->  4.608

El budget no frena (512 y 8192 dan lo mismo); los niveles escalan de forma
monótona. Ese budget muerto es lo que dejó que el razonamiento se disparara de
~2.000 a ~13.000 tokens por auditoría entre el 2026-08-13 y el 2026-08-19 y
multiplicara por 1,8 el costo por auditoría, aun con la tarifa de gemini-3.7-flash
a mitad de precio que la de 3.6-flash.

MINIMAL existe en el SDK (`types.ThinkingLevel`) pero NO lo aceptan todos los
modelos: gemini-3.7-flash lo rechazaba con 400 INVALID_ARGUMENT, mientras que los
"lite" sí lo toman y devuelven 0 tokens de pensamiento (medido, ver
MODELOS_CON_MINIMAL). Por eso MINIMAL existe como valor interno pero queda FUERA
del catálogo que se ofrece por plantilla: las auditorías corren en el flash de
turno (hoy gemini-3.8-flash, sin probar contra MINIMAL) y ofrecerlo ahí sería
ofrecer una plantilla que puede no poder auditar.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from google.genai import types

# MEDIUM y no LOW: LOW es lo más barato pero deja el razonamiento al ras del que
# tenía gemini-3.6-flash antes del salto, sin margen en llamados complejos (muchos
# atributos críticos, audios largos). MEDIUM cuesta ~2x en la porción de
# pensamiento —que es una fracción del costo, no el total— y da aire.
NIVEL_RAZONAMIENTO_DEFAULT = "MEDIUM"

NIVELES_RAZONAMIENTO: Dict[str, Dict[str, Any]] = {
    # `seleccionable` = se ofrece en el editor de plantillas. MINIMAL no: las
    # auditorías corren en el flash de turno (hoy gemini-3.8-flash), que no está
    # en la lista blanca de abajo. Existe igual porque los consumidores internos
    # que corren en modelos "lite" (chatbot RAG, detección de vacíos) sí lo pueden
    # pedir.
    "MINIMAL": {
        "label": "Mínimo",
        "nivel": 0,
        "icono": "bi-dash-circle",
        "descripcion": (
            "No piensa antes de responder. Solo para tareas mecánicas en modelos "
            "que lo soportan (los 'lite'); el flash de las auditorías no."
        ),
        "thoughts_referencia": 0,
        "seleccionable": False,
    },
    "LOW": {
        "label": "Bajo",
        "nivel": 1,
        "icono": "bi-lightning-charge",
        "descripcion": (
            "Piensa lo mínimo antes de responder. El más barato. Para plantillas "
            "simples y de alto volumen: pocos atributos, criterios objetivos "
            "(¿saludó?, ¿se identificó?)."
        ),
        # Referencia medida el 2026-08-19 sobre una tarea de razonamiento pesado.
        # Es orientativo para mostrar en el selector, NO un tope garantizado.
        "thoughts_referencia": 1200,
        "seleccionable": True,
    },
    "MEDIUM": {
        "label": "Medio",
        "nivel": 2,
        "icono": "bi-gear",
        "descripcion": (
            "Balance costo/calidad. Nivel por defecto: alcanza para auditorías de "
            "calidad con atributos ponderados y criterios que piden interpretar el "
            "contexto del llamado."
        ),
        "thoughts_referencia": 2100,
        "seleccionable": True,
    },
    "HIGH": {
        "label": "Alto",
        "nivel": 3,
        "icono": "bi-gem",
        "descripcion": (
            "Razona en profundidad antes de decidir. Aproximadamente 4x el costo de "
            "pensamiento del nivel Bajo. Reservalo para plantillas con muchos "
            "atributos críticos o criterios que dependen de matices de la conversación."
        ),
        "thoughts_referencia": 4600,
        "seleccionable": True,
    },
}


def catalogo_niveles_razonamiento() -> list[Dict[str, Any]]:
    """Catálogo como lista (para exponer por API), con el `nivel` en cada item y
    una marca de cuál es el default."""
    return [
        {
            "valor": valor,
            "es_default": valor == NIVEL_RAZONAMIENTO_DEFAULT,
            **info,
        }
        for valor, info in NIVELES_RAZONAMIENTO.items()
        if info.get("seleccionable")
    ]


def nivel_valido(nivel: Optional[str]) -> bool:
    """¿Es un nivel que una PLANTILLA puede elegir? MINIMAL da False a propósito:
    es un valor interno y guardarlo en una plantilla rompería la auditoría con un
    400 del modelo (además de violar el CHECK de la migración 2026-08-19b)."""
    if not nivel:
        return False
    info = NIVELES_RAZONAMIENTO.get(str(nivel).upper())
    return bool(info and info.get("seleccionable"))


def normalizar(nivel: Optional[str], *, default: Optional[str] = None) -> str:
    """Nivel utilizable a partir de lo que haya configurado.

    Tolerante a propósito: una plantilla sin nivel (columna NULL, o la migración
    todavía sin aplicar) y un valor basura caen los dos al default en vez de
    romper la auditoría.

    `default` permite que un consumidor tenga su propio piso: la cola de
    transcripciones usa LOW, porque transcribir es dictado y no análisis, y ahí
    caer al MEDIUM de las auditorías sería pagar razonamiento al pedo."""
    if nivel and str(nivel).upper() in NIVELES_RAZONAMIENTO:
        return str(nivel).upper()
    if default and str(default).upper() in NIVELES_RAZONAMIENTO:
        return str(default).upper()
    return NIVEL_RAZONAMIENTO_DEFAULT


# Modelos que aceptan thinking_level=MINIMAL. Medido el 2026-08-19 (probe contra la
# API real): los "lite" y el flash-preview lo toman y devuelven 0 tokens de
# pensamiento; gemini-3.7-flash respondía 400 INVALID_ARGUMENT ("Thinking level
# MINIMAL is not supported for this model"). gemini-3.8-flash (2026-09-02) todavía
# no se probó, así que queda fuera de la lista y degrada a LOW.
#
# Es una lista blanca y no una negra a propósito: sale un modelo nuevo cada ~3
# semanas y un modelo desconocido tiene que degradar a LOW —que funciona en todos—
# y no reventar la request. El costo de equivocarse hacia el lado seguro son unos
# pocos tokens de pensamiento; hacia el otro lado es un 400 en producción.
MODELOS_CON_MINIMAL = frozenset({
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
})


def soporta_minimal(modelo: Optional[str]) -> bool:
    return bool(modelo) and str(modelo).strip() in MODELOS_CON_MINIMAL


def nivel_para_modelo(nivel: Optional[str], modelo: Optional[str]) -> str:
    """El nivel pedido, degradado a LOW si el modelo no acepta MINIMAL.

    Deja que cada consumidor exprese lo que QUIERE (MINIMAL en tareas mecánicas)
    sin tener que saber en qué modelo va a terminar corriendo: el modelo sale de
    settings y puede cambiar por .env sin tocar el código (ver GEMINI_DOCS_MODEL /
    DEFAULT_REMOTE_LLM_MODEL). Si mañana ese modelo pasa a ser uno que sí soporta
    MINIMAL, el pedido se cumple solo."""
    resuelto = normalizar(nivel)
    if resuelto == "MINIMAL" and not soporta_minimal(modelo):
        return "LOW"
    return resuelto


def thinking_config(
    nivel: Optional[str], *, modelo: Optional[str] = None,
    include_thoughts: bool = True, default: Optional[str] = None
) -> types.ThinkingConfig:
    """ThinkingConfig para el nivel pedido.

    Se manda `thinking_level` y NO `thinking_budget`: el budget es letra muerta en
    esta generación de modelos (ver el docstring del módulo). Mandar los dos no
    tiene sentido y, peor, deja la impresión de que hay un tope que no existe.

    `include_thoughts=False` (transcripciones) no apaga el razonamiento, solo evita
    que los thoughts vuelvan en la respuesta: quien quiera gastar poco tiene que
    pedir un nivel bajo, no esconder los pensamientos.

    `modelo`: con qué modelo se va a mandar. Solo se usa para degradar MINIMAL a LOW
    cuando ese modelo no lo soporta (ver nivel_para_modelo); pasarlo siempre que se
    lo tenga a mano evita un 400 en producción."""
    resuelto = normalizar(nivel, default=default)
    return types.ThinkingConfig(
        include_thoughts=include_thoughts,
        thinking_level=nivel_para_modelo(resuelto, modelo),
    )


def label(nivel: Optional[str]) -> str:
    """Etiqueta legible ('Medio'), para mails y pantallas."""
    return NIVELES_RAZONAMIENTO[normalizar(nivel)]["label"]


# --------------------------------------------------------------------------- #
# Política por consumidor interno                                              #
# --------------------------------------------------------------------------- #
# Los asistentes que MEJORAN el resto del sistema (redactan prompts de plantillas,
# arman el markdown del conocimiento del chatbot) piensan al máximo: son de bajo
# volumen —unas pocas ejecuciones por día— y su salida la reusan después miles de
# auditorías o consultas. Ahorrar razonamiento acá es ahorrar en el lugar exacto
# donde no conviene (decisión de Ignacio, 2026-08-19).
NIVEL_RAZONAMIENTO_ASISTENTES = "HIGH"

# Tareas mecánicas de alto volumen: clasificar/extraer sobre texto que YA está
# escrito. No hay nada que razonar y cada token de pensamiento se factura a tarifa
# de SALIDA. MINIMAL donde el modelo lo acepte (los "lite"); si no, degrada a LOW
# solo (ver nivel_para_modelo).
NIVEL_RAZONAMIENTO_MECANICO = "MINIMAL"

# Piso de la cola de transcripciones (AuditorIA/transcripcion_cola.py). Transcribir es
# dictado, no análisis: medido el 2026-08-19, LOW devuelve 0 tokens de pensamiento y la
# transcripción sale completa igual, mientras que MEDIUM gasta ~1.100 por llamado sin
# mejorar nada. Configurable por settings.TRANSCRIPCION_NIVEL_RAZONAMIENTO.
NIVEL_RAZONAMIENTO_TRANSCRIPCION = "LOW"
