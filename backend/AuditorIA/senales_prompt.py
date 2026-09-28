"""Problemas detectables leyendo el TEXTO del prompt de un atributo, sin IA y sin tokens.

POR QUÉ EXISTE
--------------
`asistente_plantillas.senales_de_plantilla` ya detectaba lo que se ve en la ESTRUCTURA
de un atributo (un enum sin salida segura, un peso en 0, dos nombres iguales). Pero los
errores que más aparecen en las plantillas reales están en la redacción del prompt, y
ninguno de esos chequeos los veía:

  * Fechas fijas ("los llamados de agosto de 2026", "hasta el 31/12"). Un prompt no se
    actualiza solo: pasada esa fecha la IA sigue evaluando contra ella y la plantilla
    empieza a medir cualquier cosa sin que nadie se entere.
  * Prompts a medio escribir: un placeholder que quedó sin reemplazar (`XXX`,
    `<nombre de la campaña>`, "a completar") es una instrucción incompleta que la IA
    igual va a intentar responder.
  * Prompts que no plantean ningún criterio: el nombre del atributo repetido, o un
    enunciado que no dice qué hay que evaluar.
  * Un solo atributo Sí/No que en realidad pregunta tres cosas: la IA tiene que resumir
    tres criterios en una única respuesta y el resultado no se puede accionar.
  * Prompts que mandan a consultar un sistema que la IA no ve (el CRM, la planilla, el
    legajo). Solo tiene el audio: lo que le pidan de afuera lo va a completar igual.

Todo devuelve señales en el mismo formato que las estructurales (clave, mensaje,
severidad) para que el chequeo de salud, el gate del guardado, el semáforo del editor y
la revisión con IA vean exactamente la misma lista.

CRITERIO DE SEVERIDAD
---------------------
`alta` = la IA va a recibir una instrucción rota y hay que arreglarla sí o sí (es lo que
frena el guardado). `media` = la plantilla funciona pero está midiendo peor de lo que
podría. La mayoría de lo que vive acá es `media` a propósito: son problemas de redacción,
y un gate que frena por una redacción discutible se termina salteando siempre.

Los patrones son deliberadamente ESTRECHOS. Un falso positivo acá se paga caro: el
analista aprende que el semáforo miente y deja de mirarlo. Ante la duda, no se marca.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings

# Severidades: mismos valores que asistente_plantillas (que las importa de acá para que
# no haya dos definiciones). Viven en este módulo porque es el de más abajo en la cadena
# de imports: limites_texto -> senales_prompt -> asistente_plantillas.
SEVERIDAD_ALTA = "alta"
SEVERIDAD_MEDIA = "media"

# Una señal en crudo, antes de que el llamador le pegue el atributo al que pertenece.
Senal = Tuple[str, str, str]  # (clave, mensaje, severidad)

# Largo mínimo de un prompt para que se lo considere un criterio escrito. Por debajo de
# esto no hay lugar para decir qué evaluar y con qué evidencia.
LARGO_MINIMO_PROMPT = 80


def normalizar(texto: Optional[str]) -> str:
    """Minúsculas, sin acentos, espacios colapsados. Mismo criterio que limites_texto.

    Pública porque las señales estructurales (asistente_plantillas) comparan opciones de
    enum contra vocabularios fijos y tienen que normalizar igual que los detectores de
    texto: si cada uno lo hiciera a su manera, "No Ok" y "NO OK" no serían lo mismo.
    """
    if not texto:
        return ""
    plano = unicodedata.normalize("NFKD", str(texto))
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", plano.lower()).strip()


_normalizar = normalizar  # alias interno (el resto del módulo lo usa así)


# --------------------------------------------------------------------------- #
# 1. Fechas fijas                                                             #
# --------------------------------------------------------------------------- #
_MESES = (r"(?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|"
          r"octubre|noviembre|diciembre)")

# Preposiciones que convierten un mes suelto en un recorte temporal del criterio. Sin
# esto marcaríamos "si el cliente reclama la factura de enero", que es legítimo.
_ANTES_DE_MES = r"(?:hasta|desde|durante|a partir de|antes de|despues de|entre|en|el mes de|vigencia|vigente)"

_PATRONES_FECHA: List[Tuple[str, str]] = [
    (r"\b\d{1,2}\s*[/-]\s*\d{1,2}\s*[/-]\s*\d{2,4}\b", "una fecha con día y mes"),
    (r"\b\d{4}-\d{2}-\d{2}\b", "una fecha con día y mes"),
    (rf"\b\d{{1,2}}\s+de\s+{_MESES}\b", "una fecha con día y mes"),
    (rf"\b{_MESES}\s+(?:de[l]?\s+)?20\d{{2}}\b", "un mes y un año concretos"),
    (rf"\b{_ANTES_DE_MES}\s+(?:el\s+mes\s+de\s+)?{_MESES}\b", "un mes concreto"),
    (r"\b(?:hasta|desde|durante|a partir de|antes de|despues de|en)\s+(?:el\s+)?(?:ano\s+)?20\d{2}\b",
     "un año concreto"),
    (r"\b(?:este|el proximo|el pasado)\s+(?:mes|trimestre|semestre|ano)\b",
     "un período relativo al momento en que se escribió"),
]
_FECHA_COMPILADOS = [(re.compile(p), q) for p, q in _PATRONES_FECHA]


def _senal_fecha(texto: str) -> Optional[Senal]:
    for patron, que in _FECHA_COMPILADOS:
        hallazgo = patron.search(texto)
        if hallazgo:
            return (
                "prompt_con_fecha",
                f"el prompt fija {que} («{hallazgo.group(0).strip()}»). El prompt no se "
                "actualiza solo: cuando esa fecha pase, la IA va a seguir evaluando contra "
                "ella. Si el criterio depende del período, sacá la fecha y describí la "
                "condición («si el cliente menciona una factura vencida»).",
                SEVERIDAD_MEDIA,
            )
    return None


# --------------------------------------------------------------------------- #
# 2. Prompt a medio escribir                                                   #
# --------------------------------------------------------------------------- #
# Solo marcadores inequívocos: nadie escribe "xxx" ni "<campaña>" a propósito en un
# criterio de auditoría. Los corchetes NO entran (se usan de verdad para citar).
_PATRONES_INCOMPLETO: List[Tuple[str, str]] = [
    (r"\bx{3,}\b", "quedó un «XXX» sin reemplazar"),
    (r"<[^>\n]{1,40}>", "quedó un marcador «<...>» sin reemplazar"),
    (r"\{\{[^}\n]{0,40}\}\}", "quedó un marcador «{{...}}» sin reemplazar"),
    (r"\b(?:a completar|completar aca|completar aqui|falta completar|por completar)\b",
     "dice que falta completarlo"),
    (r"\b(?:pendiente de definir|a definir|por definir|tbd)\b", "dice que falta definirlo"),
    (r"\blorem ipsum\b", "quedó texto de relleno"),
]
_INCOMPLETO_COMPILADOS = [(re.compile(p), q) for p, q in _PATRONES_INCOMPLETO]


def _senal_incompleto(texto: str) -> Optional[Senal]:
    for patron, que in _INCOMPLETO_COMPILADOS:
        if patron.search(texto):
            return (
                "prompt_incompleto",
                f"el prompt está a medio escribir: {que}. La IA lo va a responder igual, "
                "adivinando qué iba en ese lugar.",
                SEVERIDAD_ALTA,
            )
    return None


# --------------------------------------------------------------------------- #
# 3. Prompt sin criterio / que repite el nombre                                #
# --------------------------------------------------------------------------- #
# Verbos con los que se le pide algo a la IA. La lista cubre voseo, imperativo de tú y
# el infinitivo, que es como escribe la mitad de la gente ("verificar si saludó").
_VERBOS_EVALUACION = re.compile(
    r"\b(?:evalu|verific|indic|determin|analiz|revis|detect|identific|marc|chequ|control|"
    r"corrobor|describ|clasific|calific|valor|puntu|respond|consign|registr|constat|"
    r"observ|coteja|menciona|senal|escrib|redact|resum|detall|sintetiz|suger|sugier|"
    r"recomend|argument|justific|explic|coment|complet|transcrib|extra|"
    # Los que usa Calidad para pedir un dato ("colocá el motivo", "generá un feedback",
    # "cargá el documento"). Sin ellos, prompts perfectamente evaluables se reportaban
    # como si no plantearan nada.
    r"coloc|gener|carg|anot|agreg|selec|eleg|escog|brind|aport|asign|list|pon)\w*"
)
# Un condicional también plantea un criterio, aunque no haya verbo imperativo:
# "si el operador no se identifica, es NO OK".
_CONDICIONAL = re.compile(r"\bs[ií]\s+(?:el|la|los|las|no|se|hay|hubo|existe)\b")
# Y una regla enunciada como deber es la otra forma habitual de escribir un criterio:
# "el operador debe mostrar empatía frente al reclamo".
# "debe", "debés", "deberá", "debían": todas las conjugaciones del deber enuncian una
# regla. El stem va acotado a esas formas para no comerse "debido a".
_REGLA = re.compile(r"\bdeb(?:e|es|en|era|eras|eran|eria|erias|erian|ia|ias|ian)\b"
                    r"|\b(?:tiene que|tienen que|corresponde|se espera que|es obligatorio|"
                    r"esta prohibido|no puede|nunca)\b")


def _sin_puntuacion(texto: str) -> str:
    return re.sub(r"[^\w\s]", "", texto).strip()


def _senal_sin_criterio(nombre: str, prompt: str, texto: str) -> Optional[Senal]:
    """Prompt que no plantea nada que se pueda evaluar.

    Devuelve UNA sola señal (la más específica que aplique) para no apilar tres avisos
    sobre el mismo problema: un prompt vacío de criterio suele ser además corto y suele
    ser además el nombre repetido.
    """
    plano = _sin_puntuacion(prompt)
    nombre_plano = _sin_puntuacion(nombre)

    if nombre_plano and plano and (plano == nombre_plano or plano in nombre_plano):
        return (
            "prompt_repite_nombre",
            "el prompt repite el nombre del atributo y no agrega ningún criterio: la IA "
            "no sabe con qué evidencia decidir. Escribí qué tiene que buscar en el "
            "llamado y qué cuenta como cumplido.",
            SEVERIDAD_MEDIA,
        )

    # Los verbos se buscan contra el texto NORMALIZADO: el prompt real viene con
    # mayúscula inicial y acentos ("Clasificá", "Verificá"), y contra el original no
    # matchearía ninguno de los patrones.
    tiene_pregunta = "?" in prompt or "¿" in prompt
    if not tiene_pregunta and not any(r.search(texto) for r in (_VERBOS_EVALUACION, _CONDICIONAL, _REGLA)):
        return (
            "prompt_sin_criterio",
            "el prompt no plantea qué evaluar: no hay una pregunta, ni una instrucción "
            "(«verificá que…»), ni una condición («si el operador…»). Tal como está, la "
            "IA decide sola qué medir.",
            SEVERIDAD_MEDIA,
        )

    if len(prompt) < LARGO_MINIMO_PROMPT:
        return (
            "prompt_corto",
            f"el prompt es muy corto ({len(prompt)} caracteres) para que la IA sepa qué "
            "evaluar y con qué evidencia del llamado.",
            SEVERIDAD_MEDIA,
        )
    return None


# --------------------------------------------------------------------------- #
# 4. Varios criterios en una sola respuesta                                    #
# --------------------------------------------------------------------------- #
# Tipos cuya respuesta es UNA sola: si el prompt pregunta tres cosas, las tres colapsan
# en un Sí/No o en una única opción y el resultado deja de ser accionable.
TIPOS_RESPUESTA_UNICA = {"boolean", "enum", "critical_audit"}

_SUMA_CRITERIO = re.compile(
    rf"\b(?:y ademas|ademas|tambien|asimismo|por otro lado)\b[^.;?]{{0,60}}?{_VERBOS_EVALUACION.pattern}"
)


def _senal_multiples_criterios(prompt: str, texto: str, tipo: str) -> Optional[Senal]:
    if tipo not in TIPOS_RESPUESTA_UNICA:
        return None
    preguntas = prompt.count("?")
    if preguntas >= 2 or _SUMA_CRITERIO.search(texto):
        return (
            "multiples_criterios",
            "el prompt evalúa más de una cosa, pero el atributo admite una sola "
            "respuesta: los criterios se mezclan y no se puede saber cuál falló. "
            "Conviene partirlo en un atributo por criterio.",
            SEVERIDAD_MEDIA,
        )
    return None


# --------------------------------------------------------------------------- #
# 5. Datos que la IA no tiene                                                  #
# --------------------------------------------------------------------------- #
# La auditoría solo ve el audio (o la transcripción) del llamado. Todo lo que esté en un
# sistema es invisible: pedirlo no devuelve "no sé", devuelve una respuesta inventada.
_FUENTES_EXTERNAS = (r"(?:crm|salesforce|mitrol|orion|sap|zendesk|sistema|sistemas|"
                     r"base de datos|planilla|excel|ficha del cliente|legajo|"
                     r"historial del cliente|gestion cargada|ticket)")
# El artículo va aparte: "verificá en el CRM" y "verificá en CRM" tienen que matchear igual.
_ARTICULO = r"(?:el|la|los|las|un|una)\s+"
_PIDE_EXTERNO = re.compile(
    rf"\b(?:consult\w*|verific\w*|corrobor\w*|fijate|revis\w*|chequ\w*|compar\w*|valid\w*|"
    rf"contrast\w*|busc\w*|ingres\w*)\b[^.;?]{{0,40}}?\b(?:en|contra|con)\s+(?:{_ARTICULO})?"
    rf"(?P<fuente>{_FUENTES_EXTERNAS})\b"
)

# Cuántos campos se nombran en el mensaje. La lista completa puede tener 30 y el mensaje
# tiene que entrar en un tooltip: con los primeros alcanza para que se entienda la idea.
MAX_CAMPOS_EN_MENSAJE = 8


def _esta_en_el_contexto(fuente: str, campos_contexto: Optional[List[str]]) -> bool:
    """True si eso que el prompt manda a consultar YA viaja con el audio.

    Es lo que evita el falso positivo más caro de esta señal: hay campañas cuyo
    Call_details incluye el CRM, el número de caso o los comentarios de Salesforce (ver
    AuditorIA/call_details.py). Ahí "corroborá contra el CRM" no es un pedido imposible:
    el dato está en el prompt.
    """
    if not campos_contexto:
        return False
    fuente = _normalizar(fuente)
    return any(fuente in _normalizar(campo) or _normalizar(campo) in fuente
               for campo in campos_contexto if campo)


def _senal_dato_externo(texto: str, campos_contexto: Optional[List[str]] = None) -> Optional[Senal]:
    hallazgo = _PIDE_EXTERNO.search(texto)
    if not hallazgo:
        return None
    if _esta_en_el_contexto(hallazgo.group("fuente"), campos_contexto):
        return None

    if campos_contexto:
        visibles = ", ".join(f"'{c}'" for c in campos_contexto[:MAX_CAMPOS_EN_MENSAJE])
        resto = len(campos_contexto) - MAX_CAMPOS_EN_MENSAJE
        tiene = (f"La auditoría recibe el audio y estos datos del llamado: {visibles}"
                 + (f" y {resto} más" if resto > 0 else "") + ".")
    else:
        tiene = "La auditoría recibe el audio del llamado y sus datos de contexto."
    return (
        "pide_dato_externo",
        f"el prompt manda a consultar un sistema que la IA no puede abrir. {tiene} "
        "Reformulalo sobre esos datos o sobre lo que se escucha («si el operador dice "
        "que cargó el caso…»): pedirle algo que no tiene no devuelve «no sé», devuelve "
        "una respuesta inventada.",
        SEVERIDAD_MEDIA,
    )


class SenalesAltasError(ValueError):
    """El atributo que se está guardando introduce problemas de severidad alta.

    Hereda de ValueError porque los routers de plantillas ya traducen ValueError a un
    400 con el mensaje para el usuario: si esto pasara sin que nadie lo atrape, el
    guardado igual se frena con un error legible en vez de reventar en un 500.

    Lleva las señales adentro para que el editor pueda pintarlas una por una y ofrecer
    "guardar igual" con la lista a la vista, en vez de un cartel de error suelto.
    """

    def __init__(self, senales: List[Dict[str, Any]]):
        self.senales = senales or []
        detalle = " ".join(s.get("mensaje", "") for s in self.senales)
        super().__init__(detalle or "El atributo tiene problemas que hay que revisar antes de guardarlo.")


def bloquea_altas() -> bool:
    """Si las señales de severidad alta frenan el guardado del atributo.

    False = el editor las muestra igual (semáforo y chequeo de salud siguen andando)
    pero no frenan a nadie. Es la perilla para bajar el gate sin desactivar el
    diagnóstico, si en algún momento traba más de lo que ayuda.
    """
    return bool(getattr(settings, "PLANTILLA_BLOQUEAR_SENALES_ALTAS", True))


# --------------------------------------------------------------------------- #
# API pública                                                                  #
# --------------------------------------------------------------------------- #
def activo() -> bool:
    """Permite apagar TODAS las señales de texto desde la config, sin tocar código.

    Existe por el mismo motivo que ATRIBUTO_TEXTO_MAX_CARACTERES=0: si un patrón resulta
    ruidoso en producción, se apaga y se corrige con calma en vez de convivir con un
    semáforo que marca cosas que no son.
    """
    return bool(getattr(settings, "PLANTILLA_SENALES_TEXTO", True))


def senales_de_texto(nombre: Optional[str], prompt: Optional[str], tipo: Optional[str],
                    campos_contexto: Optional[List[str]] = None) -> List[Senal]:
    """Señales que salen de leer el nombre y el prompt de UN atributo.

    `campos_contexto` son los datos del llamado que la IA recibe además del audio en esa
    campaña (bloque Call_details, ver AuditorIA/call_details.py). Sin ellos las señales
    siguen andando, pero `pide_dato_externo` no puede distinguir "consultá el CRM" en una
    campaña donde el CRM viaja de una donde no.

    Devuelve tuplas (clave, mensaje, severidad) sin el nombre del atributo adelante: se
    lo agrega el llamador, que es el que sabe cómo se muestra cada señal.
    """
    if not activo():
        return []

    prompt = (prompt or "").strip()
    nombre = (nombre or "").strip()
    tipo = (tipo or "").strip().lower()
    texto = _normalizar(prompt)

    senales: List[Senal] = []

    incompleto = _senal_incompleto(texto)
    if incompleto:
        senales.append(incompleto)

    # Un prompt vacío ya se reporta como "sin criterio": no hace falta pasarle los otros
    # detectores, que dirían lo mismo con otras palabras.
    sin_criterio = _senal_sin_criterio(nombre, prompt, texto)
    if sin_criterio:
        senales.append(sin_criterio)

    if texto:
        fecha = _senal_fecha(texto)
        if fecha:
            senales.append(fecha)

        # Estas dos solo tienen sentido sobre un prompt que sí plantea algo: sobre uno
        # vacío o que repite el nombre serían ruido encima del problema real. Que sea
        # CORTO no las suprime: un prompt breve puede igual mandar a consultar el CRM.
        sin_ningun_criterio = bool(sin_criterio) and sin_criterio[0] in (
            "prompt_sin_criterio", "prompt_repite_nombre")
        if not sin_ningun_criterio:
            multiples = _senal_multiples_criterios(prompt, texto, tipo)
            if multiples:
                senales.append(multiples)
            externo = _senal_dato_externo(texto, campos_contexto)
            if externo:
                senales.append(externo)

    return senales
