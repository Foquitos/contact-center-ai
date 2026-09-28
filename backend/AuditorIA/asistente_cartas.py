"""Reestructura un documento de PLANTILLAS DE CARTAS para que el RAG las distinga.

EL PROBLEMA
-----------
Las cartas de un mismo equipo son casi idénticas entre sí: mismo saludo, mismo bloque
de vías de contacto, misma despedida. En Voltara, el 55% del texto de cada carta es
boilerplate repetido en las 50, y hay pares 98% iguales. Como el buscador compara la
consulta contra el texto del fragmento, ese 55% común domina la similitud y las
plantillas se vuelven indistinguibles: al pedir "una carta para derivar a T3" el bot
recupera cualquier otra carta con la misma confianza.

LA SOLUCIÓN
-----------
Reescribir el documento de modo que cada carta quede representada por lo que la hace
ÚNICA, y el texto común aparezca una sola vez:

  1. Un CATÁLOGO al principio: una fila por carta con cuándo usarla y con qué palabras
     la pide un operador. Es el fragmento que ancla la desambiguación.
  2. La APERTURA y el CIERRE comunes, definidos UNA vez (además van en el system prompt
     del bot, para que estén garantizados sin depender de que el buscador los recupere).
  3. Una sección por carta con: cuándo usarla, disparadores, campos a completar y SOLO
     el cuerpo variable.

Así el fragmento de cada carta pasa a ser casi todo contenido discriminante.

GARANTÍA DE NO PÉRDIDA
----------------------
La IA solo hace el trabajo semántico (separar cuerpo variable del boilerplate, redactar
cuándo usarla y los disparadores). Después se verifica de forma DETERMINÍSTICA que toda
línea de contenido del original —las que no son boilerplate— siga presente en el
resultado. Lo que no se pueda verificar se reporta; no se descarta en silencio.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from google.genai import types

from AuditorIA.asistente_docs import _generar_json
from AuditorIA import razonamiento
from app.config import settings


def _modelo() -> str:
    """Modelo del asistente de cartas. Configurable por .env sin redeploy, igual que
    el resto de los asistentes."""
    return getattr(settings, "GEMINI_CARTAS_MODEL", "gemini-3.5-flash-lite")

logger = logging.getLogger(__name__)

# Cartas por lote en el análisis semántico. Cada lote es una llamada: más chicos =
# más llamadas pero respuestas más cortas (y menos riesgo de truncado).
CARTAS_POR_LOTE = 8

# Una línea (contando sus variantes) se considera boilerplate si aparece en al menos esta
# fracción de las cartas. Bajo a propósito: los bloques promocionales y las fórmulas de
# cortesía no están en TODAS las cartas, pero tampoco distinguen una de otra. Contenido
# propio de un caso nunca se repite en ~1/3 de las plantillas.
UMBRAL_BOILERPLATE = 0.3


# --------------------------------------------------------------------------- #
# Partido del documento en cartas (determinístico)                            #
# --------------------------------------------------------------------------- #
def partir_cartas(markdown: str) -> Tuple[List[Dict[str, str]], str]:
    """Parte el documento en cartas (una por encabezado '### '), conservando la
    categoría ('## ') a la que pertenece cada una.

    Devuelve (cartas, preambulo). El preámbulo es todo lo anterior a la primera carta
    (título del documento y guía general), que se conserva tal cual.
    """
    lineas = markdown.split("\n")
    cartas: List[Dict[str, str]] = []
    preambulo: List[str] = []
    categoria = ""
    actual: Optional[Dict[str, Any]] = None

    for linea in lineas:
        m3 = re.match(r"^###\s+(.*)$", linea)
        m2 = re.match(r"^##\s+(.*)$", linea)
        if m3:
            if actual:
                cartas.append({"categoria": actual["categoria"], "titulo": actual["titulo"],
                               "texto": "\n".join(actual["cuerpo"]).strip()})
            actual = {"titulo": m3.group(1).strip(), "categoria": categoria, "cuerpo": []}
            continue
        if m2:
            if actual:
                cartas.append({"categoria": actual["categoria"], "titulo": actual["titulo"],
                               "texto": "\n".join(actual["cuerpo"]).strip()})
                actual = None
            categoria = m2.group(1).strip()
            continue
        if actual is not None:
            actual["cuerpo"].append(linea)
        else:
            preambulo.append(linea)

    if actual:
        cartas.append({"categoria": actual["categoria"], "titulo": actual["titulo"],
                       "texto": "\n".join(actual["cuerpo"]).strip()})
    return cartas, "\n".join(preambulo).strip()


def _normalizar(linea: str) -> str:
    """Normaliza una línea para comparar: sin acentos, sin marcas de markdown, minúsculas."""
    t = unicodedata.normalize("NFKD", linea).encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[*_>#`\[\]()]", "", t)
    return " ".join(t.lower().split())


def detectar_boilerplate(cartas: List[Dict[str, str]]) -> set:
    """Líneas que se repiten en al menos UMBRAL_BOILERPLATE de las cartas: son el texto
    común (saludo, vías de contacto, despedida) que no distingue una carta de otra.

    El conteo agrupa VARIANTES: el mismo párrafo suele estar redactado de formas apenas
    distintas según la carta ("nos comunicamos por la solicitud que realizaste" /
    "nos comunicamos en respuesta a tu solicitud"). Contando cada redacción por separado,
    ninguna llega al umbral y todas terminan tomadas por contenido propio.
    """
    if not cartas:
        return set()
    conteo: Counter = Counter()
    for c in cartas:
        vistas = {_normalizar(l) for l in c["texto"].split("\n") if _normalizar(l)}
        conteo.update(vistas)

    minimo = max(2, int(len(cartas) * UMBRAL_BOILERPLATE))
    # Solo las líneas repetidas pueden ser boilerplate; agrupar sobre ese subconjunto
    # mantiene la comparación por similitud acotada.
    candidatas = [l for l, n in conteo.items() if n >= 2 and len(l) >= 12]
    boilerplate = set()
    for linea in candidatas:
        total = 0
        for otra in candidatas:
            if abs(len(otra) - len(linea)) > max(len(otra), len(linea)) * 0.4:
                continue
            if otra == linea or SequenceMatcher(None, linea, otra).ratio() >= 0.8:
                total += conteo[otra]
        if total >= minimo:
            boilerplate.add(linea)
    return boilerplate


def _es_boilerplate(norm: str, boilerplate: set, umbral: float = 0.82) -> bool:
    """¿Esta línea es texto común? Además del match exacto, se aceptan VARIANTES: el mismo
    párrafo aparece redactado de formas apenas distintas según la carta ("nos comunicamos
    por la solicitud que realizaste" vs "nos comunicamos en respuesta a tu solicitud"), y
    sin esta tolerancia se los toma por contenido propio y la verificación da falsas
    alarmas de pérdida."""
    if norm in boilerplate:
        return True
    for bp in boilerplate:
        # El chequeo de longitud descarta comparaciones obviamente inútiles y es mucho
        # más barato que calcular la similitud completa.
        if abs(len(bp) - len(norm)) > max(len(bp), len(norm)) * 0.35:
            continue
        if SequenceMatcher(None, norm, bp).ratio() >= umbral:
            return True
    return False


def lineas_de_contenido(texto: str, boilerplate: set) -> List[str]:
    """Líneas con carga informativa: las que NO son boilerplate ni ruido de formato.
    Es la unidad con la que se verifica que no se haya perdido nada."""
    out = []
    for linea in texto.split("\n"):
        norm = _normalizar(linea)
        if not norm or len(norm) < 12:  # separadores, viñetas sueltas, restos de formato
            continue
        if _es_boilerplate(norm, boilerplate):
            continue
        out.append(norm)
    return out


# --------------------------------------------------------------------------- #
# Análisis semántico de cada carta (IA, por lotes)                            #
# --------------------------------------------------------------------------- #
_SCHEMA_CARTA = types.Schema(
    type=types.Type.OBJECT,
    required=["titulo", "cuando_usarla", "disparadores", "cuerpo_variable"],
    properties={
        "titulo": types.Schema(type=types.Type.STRING, description="El título EXACTO de la carta, tal como vino."),
        "cuando_usarla": types.Schema(
            type=types.Type.STRING,
            description="En qué situación concreta se manda esta carta, en una o dos frases. Tiene que permitir distinguirla de las demás del catálogo.",
        ),
        "disparadores": types.Schema(
            type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING),
            description="Palabras y frases con las que un operador pediría esta carta, incluyendo sinónimos y siglas (ej.: 'T3', 'tarifa 3', 'grandes clientes'). Entre 3 y 8.",
        ),
        "campos_a_completar": types.Schema(
            type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING),
            description="Datos que el operador debe completar antes de enviarla (ej.: 'nombre del cliente', 'número de cuenta'), tal como aparecen marcados en el texto.",
        ),
        "cuerpo_variable": types.Schema(
            type=types.Type.STRING,
            description=(
                "SOLO la parte de la carta que es propia de este caso, en markdown y TEXTUAL (copiada tal cual, "
                "sin reescribir). Excluí el saludo inicial, la disculpa por la demora, el bloque de vías de "
                "contacto, el 'no respondas este correo' y la despedida, que son comunes a todas."
            ),
        ),
        "notas_operativas": types.Schema(
            type=types.Type.STRING,
            description="Advertencias o instrucciones internas para el operador que traiga la carta (ej.: 'no ofrecer Factura Digital'). Vacío si no hay.",
        ),
    },
)

_SCHEMA_LOTE = types.Schema(
    type=types.Type.OBJECT,
    required=["cartas"],
    properties={"cartas": types.Schema(type=types.Type.ARRAY, items=_SCHEMA_CARTA)},
)

_SYSTEM_ANALISIS = """\
Sos un analista de documentación de un contact center. Recibís varias PLANTILLAS DE CARTA
que el equipo usa para responder a clientes. Todas comparten el mismo formato: saludo,
disculpa por la demora, el texto propio del caso, un bloque de vías de contacto, el aviso
de "no respondas este correo" y la despedida.

Para CADA carta tenés que devolver:
- cuerpo_variable: SOLO el texto propio de ese caso, copiado TEXTUALMENTE del original
  (no lo reescribas, no lo resumas, no lo mejores). Sacá el saludo, la disculpa, el bloque
  de vías de contacto, el aviso de no responder y la despedida: son iguales en todas.
  Conservá los campos a completar tal como están marcados (**nombre del cliente**,
  [número de cuenta], etc.), los links, los mails, los teléfonos y las tablas.
- cuando_usarla: la situación concreta en que se manda. Tiene que servir para elegirla
  entre cartas parecidas, así que sé específico y marcá qué la diferencia de sus variantes.
- disparadores: cómo la pediría un operador, con sinónimos y siglas.
- campos_a_completar y notas_operativas cuando correspondan.

REGLA CRÍTICA: no pierdas información del cuerpo. Si dudás de si una línea es del caso o
es texto común, INCLUILA en cuerpo_variable. Devolvé una entrada por cada carta recibida,
con su título exacto.
"""


def _analizar_lote(cartas: List[Dict[str, str]], user_id: Optional[int]) -> List[Dict[str, Any]]:
    # La categoría va en su PROPIA línea: si se la pone junto al título, el modelo la
    # copia como parte del título y después no se puede reasociar con la carta original.
    payload = "\n\n".join(
        f"=== CARTA: {c['titulo']} ===\nCategoría: {c['categoria'] or 'sin categoría'}\n\n{c['texto']}"
        for c in cartas
    )
    resultado = _generar_json(
        _SYSTEM_ANALISIS,
        [types.Part.from_text(text=payload)],
        _SCHEMA_LOTE,
        # Tarea mecánica: separar qué parte de una carta ya escrita es fija y qué
        # parte es variable. No hay nada que razonar, y son lotes grandes; por eso va
        # en su propio modelo "lite" (GEMINI_CARTAS_MODEL) y con MINIMAL, que ese
        # modelo sí acepta —el flash de las auditorías no— y deja el
        # pensamiento en cero. Ver AuditorIA/razonamiento.py::MODELOS_CON_MINIMAL.
        temperatura=0.2, nivel_razonamiento=razonamiento.NIVEL_RAZONAMIENTO_MECANICO,
        modelo=_modelo(), user_id=user_id, ref_label="cartas_analizar",
    )
    return [c for c in (resultado.get("cartas") or []) if isinstance(c, dict)]


def _emparejar_por_titulo(originales: List[Dict[str, str]],
                          devueltas: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Asocia cada carta devuelta por la IA con su carta original por título.

    El título que vuelve no siempre es idéntico: puede traer sufijos, acentos distintos o
    variaciones de mayúsculas. Se empareja por título normalizado y, si no hay match, por
    el más parecido (con un piso de similitud para no asignar cualquier cosa)."""
    por_norm = {_normalizar(o["titulo"]): o["titulo"] for o in originales}
    resultado: Dict[str, Dict[str, Any]] = {}
    sin_asignar: List[Dict[str, Any]] = []

    for c in devueltas:
        titulo = (c.get("titulo") or "").strip()
        if not titulo:
            continue
        norm = _normalizar(titulo)
        # El modelo a veces agrega un paréntesis aclaratorio al título; se descarta.
        norm_base = re.sub(r"\s*\(categoria:.*$", "", norm).strip()
        destino = por_norm.get(norm) or por_norm.get(norm_base)
        if destino:
            resultado.setdefault(destino, c)
        else:
            sin_asignar.append(c)

    # Segunda vuelta: por parecido, solo contra las que quedaron sin cubrir.
    for c in sin_asignar:
        norm = _normalizar(c.get("titulo") or "")
        pendientes = [o["titulo"] for o in originales if o["titulo"] not in resultado]
        if not pendientes:
            break
        mejor = max(pendientes, key=lambda t: SequenceMatcher(None, norm, _normalizar(t)).ratio())
        if SequenceMatcher(None, norm, _normalizar(mejor)).ratio() >= 0.7:
            resultado[mejor] = c
            logger.info("Título emparejado por similitud: %r -> %r", c.get("titulo"), mejor)
    return resultado


# --------------------------------------------------------------------------- #
# Armado del documento nuevo                                                  #
# --------------------------------------------------------------------------- #
def _fila_catalogo(c: Dict[str, Any]) -> str:
    disparadores = ", ".join(c.get("disparadores") or [])
    cuando = (c.get("cuando_usarla") or "").replace("|", "/").replace("\n", " ")
    return f"| {c['titulo']} | {cuando} | {disparadores.replace('|', '/')} |"


def _seccion_carta(c: Dict[str, Any]) -> str:
    partes = [f"### Carta: {c['titulo']}"]
    if c.get("categoria"):
        partes.append(f"**Categoría:** {c['categoria']}")
    partes.append(f"**Cuándo usarla:** {c.get('cuando_usarla') or ''}")
    if c.get("disparadores"):
        partes.append("**El operador la pide diciendo:** " + ", ".join(c["disparadores"]))
    if c.get("campos_a_completar"):
        partes.append("**Campos a completar antes de enviar:** " + ", ".join(c["campos_a_completar"]))
    if c.get("notas_operativas"):
        partes.append(f"**Nota operativa:** {c['notas_operativas']}")
    partes.append("**Cuerpo de esta carta** (va entre la apertura y el cierre comunes):\n")
    partes.append(c.get("cuerpo_variable") or "")
    return "\n\n".join(partes)


def armar_documento(cartas: List[Dict[str, Any]], apertura: str, cierre: str,
                    titulo_doc: str, preambulo: str, opcionales: Optional[List[str]] = None) -> str:
    """Ensambla el documento reestructurado: catálogo + texto común + una sección por carta."""
    bloques = [f"# {titulo_doc}"]
    if preambulo:
        # El preámbulo original ya trae el título; se conserva solo su cuerpo.
        cuerpo_preambulo = "\n".join(
            l for l in preambulo.split("\n") if not l.startswith("# ")
        ).strip()
        if cuerpo_preambulo:
            bloques.append(cuerpo_preambulo)

    bloques.append(
        "## Cómo se arma una carta\n\n"
        "Toda carta que se envía al cliente tiene tres partes en este orden:\n\n"
        "1. La **apertura común** (igual en todas, ver más abajo).\n"
        "2. El **cuerpo** de la carta que corresponda al caso (ver la sección de cada carta).\n"
        "3. El **cierre común** (igual en todas, ver más abajo).\n\n"
        "Para elegir la carta correcta, buscá el caso en el catálogo. Si hay varias variantes "
        "parecidas, hay que confirmar con el operador cuál corresponde antes de enviarla.\n\n"
        "Los campos entre **negritas** o [corchetes] se completan antes de enviar."
    )

    filas = "\n".join(_fila_catalogo(c) for c in cartas)
    bloques.append(
        "## Catálogo de cartas disponibles\n\n"
        "Cada fila es una carta distinta. Elegí la que coincida con el caso.\n\n"
        "| Carta | Cuándo usarla | El operador la pide diciendo |\n|---|---|---|\n" + filas
    )
    bloques.append("## Apertura común (va al inicio de TODAS las cartas)\n\n" + apertura)
    bloques.append("## Cierre común (va al final de TODAS las cartas)\n\n" + cierre)
    if opcionales:
        bloques.append(
            "## Bloques opcionales del cierre\n\n"
            "Estos párrafos NO van siempre: se agregan al cierre solo cuando la situación del "
            "cliente lo justifica, y varios son EXCLUYENTES entre sí (por ejemplo, no se puede "
            "ofrecer la adhesión a factura digital a alguien que ya está adherido). Ante la duda, "
            "no agregarlos.\n\n"
            + "\n\n".join(f"- {b}" for b in opcionales)
        )
    bloques.append("## Cartas")
    bloques.extend(_seccion_carta(c) for c in cartas)
    return "\n\n".join(bloques).strip() + "\n"


# --------------------------------------------------------------------------- #
# Verificación determinística de no pérdida                                   #
# --------------------------------------------------------------------------- #
_PREFIJO_ETIQUETA = re.compile(r"^[a-z ]{3,20}:\s*")


def _presente_en(linea: str, destino_norm: str) -> bool:
    """¿La línea sobrevivió en el resultado? Se prueba también sin su etiqueta inicial
    ('nota operativa:', 'importante:'), porque el texto suele reubicarse en un campo
    propio y ahí la etiqueta ya no viaja con él."""
    if linea in destino_norm:
        return True
    sin_etiqueta = _PREFIJO_ETIQUETA.sub("", linea)
    return bool(sin_etiqueta) and sin_etiqueta != linea and sin_etiqueta in destino_norm


def verificar_sin_perdida(cartas_originales: List[Dict[str, str]],
                          analizadas: Dict[str, Dict[str, Any]],
                          boilerplate: set) -> List[Dict[str, str]]:
    """Comprueba, sin IA, que no se haya perdido CONOCIMIENTO al reestructurar.

    Solo se reporta la pérdida de líneas prácticamente ÚNICAS de esa carta (presentes en
    2 cartas o menos). Una línea que aparece en varias plantillas es, por definición,
    texto compartido: aunque no quede en el cuerpo de ESTA carta, sigue estando en el
    documento. Sin ese criterio el informe se llena de falsas alarmas por las variantes
    de redacción de las fórmulas de cortesía, y las pérdidas reales se pierden de vista.
    """
    frecuencia: Counter = Counter()
    for c in cartas_originales:
        frecuencia.update({l for l in lineas_de_contenido(c["texto"], boilerplate)})

    problemas: List[Dict[str, str]] = []
    for original in cartas_originales:
        titulo = original["titulo"]
        analizada = analizadas.get(titulo)
        if analizada is None:
            problemas.append({"carta": titulo, "problema": "La IA no devolvió esta carta."})
            continue
        destino_norm = _normalizar(" \n".join([
            analizada.get("cuerpo_variable") or "",
            analizada.get("notas_operativas") or "",
            " ".join(analizada.get("campos_a_completar") or []),
        ]))
        perdidas = [
            l for l in lineas_de_contenido(original["texto"], boilerplate)
            if frecuencia[l] <= 2 and not _presente_en(l, destino_norm)
        ]
        if perdidas:
            problemas.append({
                "carta": titulo,
                "problema": f"{len(perdidas)} línea(s) propias de esta carta no aparecen en el resultado.",
                "detalle": " | ".join(p[:110] for p in perdidas[:3]),
            })
    return problemas


# --------------------------------------------------------------------------- #
# Punto de entrada                                                            #
# --------------------------------------------------------------------------- #
def reestructurar_catalogo(markdown: str, titulo_doc: str = "Plantillas de cartas",
                           user_id: Optional[int] = None) -> Dict[str, Any]:
    """Convierte un documento de plantillas de cartas en el formato optimizado para RAG.

    Returns:
        {markdown, apertura, cierre, cartas, problemas, total_cartas, reduccion_pct}
        `problemas` viene de la verificación determinística: si no está vacío, hay que
        revisar esas cartas a mano antes de publicar.
    """
    cartas, preambulo = partir_cartas(markdown)
    if not cartas:
        raise ValueError("No se encontraron cartas (encabezados '### ') en el documento.")
    boilerplate = detectar_boilerplate(cartas)
    logger.info("Cartas: %d | líneas de boilerplate detectadas: %d", len(cartas), len(boilerplate))

    devueltas: List[Dict[str, Any]] = []
    for i in range(0, len(cartas), CARTAS_POR_LOTE):
        lote = cartas[i:i + CARTAS_POR_LOTE]
        logger.info("Analizando cartas %d-%d de %d...", i + 1, i + len(lote), len(cartas))
        devueltas.extend(_analizar_lote(lote, user_id))

    analizadas = _emparejar_por_titulo(cartas, devueltas)

    # Reasociar categoría y conservar el ORDEN original del documento.
    ordenadas: List[Dict[str, Any]] = []
    for original in cartas:
        a = analizadas.get(original["titulo"])
        if a is None:
            # No se pudo analizar: se conserva la carta entera como cuerpo, para no perderla.
            a = {"titulo": original["titulo"], "cuando_usarla": "", "disparadores": [],
                 "cuerpo_variable": original["texto"]}
        a = dict(a)
        a["categoria"] = original["categoria"]
        ordenadas.append(a)

    apertura, cierre = extraer_apertura_cierre(cartas, boilerplate)
    opcionales = bloques_opcionales(cartas, boilerplate, apertura, cierre)
    problemas = verificar_sin_perdida(cartas, analizadas, boilerplate)
    nuevo = armar_documento(ordenadas, apertura, cierre, titulo_doc, preambulo, opcionales)

    return {
        "markdown": nuevo,
        "apertura": apertura,
        "cierre": cierre,
        "opcionales": opcionales,
        "cartas": ordenadas,
        "problemas": problemas,
        "total_cartas": len(cartas),
        "reduccion_pct": round(100 * (1 - len(nuevo) / max(1, len(markdown)))),
    }


def _extremos_por_carta(cartas: List[Dict[str, str]], boilerplate: set) -> List[Tuple[str, str]]:
    """(apertura, cierre) de cada carta: el texto común que rodea a su contenido propio."""
    out = []
    for c in cartas:
        lineas = c["texto"].split("\n")
        indices = [
            i for i, l in enumerate(lineas)
            if _normalizar(l) and not _es_boilerplate(_normalizar(l), boilerplate)
            and len(_normalizar(l)) >= 12
        ]
        if not indices:
            continue
        apertura = "\n".join(lineas[:indices[0]]).strip()
        cierre = re.sub(r"(\n\s*-{3,}\s*)+$", "", "\n".join(lineas[indices[-1] + 1:])).strip()
        out.append((apertura, cierre))
    return out


def extraer_apertura_cierre(cartas: List[Dict[str, str]], boilerplate: set) -> Tuple[str, str]:
    """Apertura y cierre TÍPICOS: los que aparecen en más cartas (la moda).

    No se elige el más largo a propósito. Algunas cartas suman bloques promocionales
    opcionales —y hasta mutuamente excluyentes, como "verificamos que ya estás adherido a
    factura digital" frente a "¿sabías que podés adherirte?"—. Tomar el más completo
    metería todos esos bloques en TODAS las cartas y el bot terminaría mandando textos
    contradictorios. Los opcionales se conservan aparte (ver bloques_opcionales).
    """
    extremos = _extremos_por_carta(cartas, boilerplate)
    if not extremos:
        return "", ""
    aperturas = Counter(a for a, _ in extremos if a.strip())
    cierres = Counter(c for _, c in extremos if c.strip())
    apertura = aperturas.most_common(1)[0][0] if aperturas else ""
    cierre = cierres.most_common(1)[0][0] if cierres else ""
    return apertura, cierre


def bloques_opcionales(cartas: List[Dict[str, str]], boilerplate: set,
                       apertura: str, cierre: str) -> List[str]:
    """Párrafos comunes que NO entran en la apertura/cierre típicos: bloques que solo
    algunas cartas suman (ofrecimiento de factura digital, adhesión a débito, etc.).

    Se conservan en una sección propia para que no desaparezcan al no formar parte del
    cierre estándar, y para que el bot pueda agregarlos cuando la carta lo indique."""
    fijo = _normalizar(apertura + "\n" + cierre)
    frecuencia: Counter = Counter()
    for c in cartas:
        for linea in {l.strip() for l in c["texto"].split("\n") if l.strip()}:
            norm = _normalizar(linea)
            if len(norm) < 40 or norm in fijo:
                continue
            if _es_boilerplate(norm, boilerplate):
                frecuencia[linea.strip()] += 1
    minimo = max(3, int(len(cartas) * 0.1))
    return [linea for linea, n in frecuencia.most_common() if n >= minimo]
