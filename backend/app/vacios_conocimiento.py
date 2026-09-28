"""Vacíos de conocimiento: qué le preguntan los operadores al chatbot que la
documentación no responde, agrupado por tema para que Calidad lo triage.

El problema que resuelve: el 23% de las consultas a Voltara terminaban en "no
encontré esa información" y nadie se enteraba. La única señal de calidad eran las
calificaciones del chat (35 en 9 meses).

DOS SEÑALES, EN DOS MOMENTOS DISTINTOS
--------------------------------------
1. En línea, al loguear la consulta (`evaluar_cobertura`): frase canónica en la
   respuesta + score máximo del reranker. Cero tokens y cero latencia, corre
   dentro del logging que ya existía. Deja `sin_cobertura` marcado.

2. Diferido, en el scheduler (`procesar_vacios_pendientes`): sobre los candidatos
   marcados —y solo sobre ellos— corre el análisis caro. Nada de esto toca el
   camino de la consulta del operador, que es sensible al TTFT.

LO QUE HACE EL ANÁLISIS DIFERIDO
--------------------------------
a) Confirma con el LLM que realmente no se pudo responder y extrae un TEMA
   normalizado. Agrupar por la consulta cruda no funciona: los operadores
   escriben "cleinte", "porifs", "no lciente avellaneda".

b) Distingue HUECO de FALLO DE RECUPERACIÓN, que es la distinción que hace útil
   esta pantalla. Vuelve a buscar el tema contra el corpus con top_k alto y sin
   el recorte del reranker: si aparece un chunk claramente relevante, la
   información SÍ estaba y el problema es del buscador, no de Calidad. Mandarles
   esos casos haría que documenten cosas que ya existen.

c) Agrupa por similitud de embedding contra los temas ya registrados, para que
   "poda de arbol" / "poda de arboles" / "poda de árbol" sean UN tema con 18
   ocurrencias y no tres filas sueltas.
"""
import logging
import re
import unicodedata
import uuid
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text

from app.chatbot_desambiguacion import es_desambiguacion
from app.config import settings
from AuditorIA import razonamiento

logger = logging.getLogger(__name__)

# Colección de Qdrant con el embedding de cada tema ya registrado (el centroide es
# el embedding de la primera consulta que lo abrió). Vive aparte de los índices de
# los bots: no se toca en el reindexado.
COLECCION_VACIOS = "vacios_conocimiento"


# --------------------------------------------------------------- señal barata

# El system prompt de los bots manda una frase canónica ("No encontré información
# sobre ese tema específico en los manuales disponibles"), pero el modelo la
# parafrasea, así que se aceptan variantes. Es un PREFILTRO: los falsos positivos
# los descarta después el clasificador, y por eso conviene que peque de amplio.
_PATRONES_SIN_COBERTURA = [
    r"no encontr[eé]",
    r"no cuento con",
    r"no dispongo",
    r"no tengo informaci[oó]n",
    r"no hay informaci[oó]n",
    r"no se encuentra (?:informaci[oó]n|detallad)",
    r"no figura",
    r"no est[aá] disponible en (?:los|la)",
    r"no puedo responder",
    r"(?:informaci[oó]n|documentaci[oó]n|documentos) (?:proporcionad[ao]s?|disponibles?) no",
    r"no alcanza para responder",
    r"no (?:especifica|menciona|detalla) ",
]
_REGEX_SIN_COBERTURA = re.compile("|".join(_PATRONES_SIN_COBERTURA), re.IGNORECASE)


def parece_sin_cobertura(respuesta: Optional[str]) -> bool:
    """Prefiltro por texto. No decide nada solo: alimenta `evaluar_cobertura`."""
    if not respuesta:
        return False
    return bool(_REGEX_SIN_COBERTURA.search(respuesta))


def _contesta_igual(respuesta: str, score_max: Optional[float]) -> bool:
    """¿La respuesta dice "no encontré" pero después contesta igual?

    Caso real (31/07): con el prompt anclado, el modelo empezó a usar la frase como
    muletilla —"No encontré información... No obstante, la documentación menciona..."—
    sobre consultas que recuperaban perfecto ("artefactos dañados", score +5,6). Sin
    este filtro, temas bien documentados entran a la pantalla de Calidad como huecos.

    Se pide que se cumplan las DOS condiciones, porque cada una sola se equivoca:
    una respuesta larga puede ser un "no encontré" con sugerencias inútiles, y un
    score alto puede venir de un chunk del tema correcto que no trae el dato pedido.
    """
    if score_max is None or score_max < settings.CHATBOT_VACIO_SCORE_EXISTE:
        return False
    # Lo que sobra después de la frase de disculpa. Una negación pura ronda los
    # 100-200 caracteres (frase + recordatorio de verificar en los sistemas).
    return len(respuesta) > settings.CHATBOT_VACIO_LARGO_RESPUESTA


def evaluar_cobertura(respuesta: Optional[str], score_max: Optional[float],
                      con_adjuntos: bool = False) -> bool:
    """¿Esta consulta quedó sin responder por falta de información?

    Se apoya sobre todo en el texto de la respuesta. El score del reranker entra
    solo como refuerzo cuando es MUY malo: medido sobre el tráfico real, el score
    máximo separa flojo por sí solo (media 1,70 en las respondidas vs 1,02 en las
    fallidas), así que usarlo como criterio principal marcaría cualquier cosa.

    `con_adjuntos` (el operador mandó una captura o un PDF) apaga SOLO la rama del
    score. Ver el comentario de abajo: con un archivo adelante, un score malo dejó
    de significar lo que significaba.
    """
    # Lista de opciones: la pregunta era ambigua, no falta documentación — los temas
    # que se le ofrecieron al operador salieron del corpus. El gate solo dispara
    # dentro de una banda de score que empieza POR ENCIMA de CHATBOT_VACIO_SCORE_MIN
    # (ver app/chatbot_desambiguacion.py), así que un hueco real nunca llega acá
    # como desambiguación; este chequeo lo garantiza igual si algún día se
    # recalibran los umbrales para otro reranker.
    if es_desambiguacion(respuesta):
        return False

    if parece_sin_cobertura(respuesta):
        # ...salvo que haya contestado igual después de la frase. Esta rama vale
        # igual con adjuntos: si el bot dice "no encontré" es porque el tema no
        # está en los manuales, y que lo haya podido responder mirando la captura
        # no tapa el hueco — al contrario, lo confirma.
        return not _contesta_igual(respuesta or "", score_max)

    # Nada de lo recuperado tiene que ver con la pregunta: aunque el bot haya
    # improvisado una respuesta, no la sacó de la documentación.
    #
    # Esa deducción se cae cuando hay un adjunto: la respuesta salió del archivo,
    # que es EXACTAMENTE para lo que se adjunta. "¿Qué significa este error?" con
    # una captura recupera cualquier cosa (el error no está en ningún manual) y
    # puntúa pésimo, y el bot igual responde bien mirando la pantalla. Sin esta
    # excepción, cada consulta con adjunto entraría a la pantalla de Calidad como
    # un hueco de documentación inexistente, y encima gastando una clasificación
    # de IA por cada una.
    if con_adjuntos:
        return False

    if score_max is not None and score_max < settings.CHATBOT_VACIO_SCORE_MIN:
        return True
    return False


def score_maximo(source_nodes) -> Optional[float]:
    """Mejor score del reranker entre los chunks que se le mandaron al LLM.

    Devuelve un float NATIVO de Python a propósito: el score viene del
    cross-encoder, o sea numpy.float32, y pyodbc no sabe bindear ese tipo
    ("Invalid parameter type. param-type=numpy.float32"). pandas.to_sql lo
    convertía en silencio; al pasar a un INSERT explícito la conversión hay que
    hacerla acá, que es donde el valor entra al sistema.
    """
    scores = [n.score for n in (source_nodes or []) if n.score is not None]
    return float(max(scores)) if scores else None


# ----------------------------------------------------------- normalización

def _norm(texto: str) -> str:
    sin_acentos = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    return " ".join(sin_acentos.lower().split())


# --------------------------------------------------- clasificación con el LLM

_SCHEMA_CLASIFICACION = {
    "type": "OBJECT",
    "properties": {
        "sin_respuesta": {
            "type": "BOOLEAN",
            "description": "true si el asistente NO pudo responder por falta de información en su documentación",
        },
        "tema": {
            "type": "STRING",
            "description": "El tema consultado, normalizado, en 3 a 10 palabras, sin typos y en infinitivo o sustantivo (ej: 'poda de árboles en la vía pública')",
        },
        "fuera_de_alcance": {
            "type": "BOOLEAN",
            "description": "true si la consulta no es algo que le corresponda responder a un asistente de procedimientos (charla suelta, datos del cliente puntual, pedidos de redacción)",
        },
    },
    "required": ["sin_respuesta", "tema", "fuera_de_alcance"],
}

_PROMPT_CLASIFICACION = """Sos un analista de calidad de un call center. Te paso una consulta que un operador le hizo a un asistente interno de procedimientos, y la respuesta que recibió.

Decidí:
1. `sin_respuesta`: ¿el asistente dejó al operador SIN la información que pidió, porque no la tenía en su documentación? Si respondió aunque sea parcialmente lo que se le pidió, es false. Si contestó "no encontré información sobre X", es true.
2. `tema`: el tema de fondo consultado, normalizado y sin los errores de tipeo del operador. Tiene que servir para agrupar consultas parecidas: escribilo genérico, sin datos del cliente puntual (nada de números de cuenta, nombres ni fechas).
3. `fuera_de_alcance`: true si la consulta no es una pregunta de procedimiento (saludos, charla, pedidos de redactar un texto, o datos de un cliente concreto que el asistente no puede saber).

CONSULTA DEL OPERADOR:
{pregunta}

RESPUESTA DEL ASISTENTE:
{respuesta}
"""


_SCHEMA_RESUELVE = {
    "type": "OBJECT",
    "properties": {
        "resuelve": {
            "type": "BOOLEAN",
            "description": "true solo si algún fragmento contiene el dato concreto que se preguntó",
        },
        "documento": {
            "type": "STRING",
            "description": "nombre del documento del fragmento que contiene la respuesta; vacío si ninguno",
        },
    },
    "required": ["resuelve"],
}

_PROMPT_RESUELVE = """Sos un analista de calidad de un call center. Te paso lo que un operador consultó y fragmentos de la documentación interna.

Decidí `resuelve`: ¿ALGUNO de los fragmentos contiene la RESPUESTA concreta a lo que se preguntó? Si es así, poné en `documento` el nombre del que la contiene.

Criterio estricto, porque de esto depende a quién se le manda el trabajo:
- Que un fragmento hable del MISMO TEMA no alcanza. Si preguntan un teléfono y el texto describe cómo cargar el reclamo, `resuelve` es false.
- Si preguntan qué documentación se necesita y el texto solo dice "se revisa la documentación adjunta" sin listar cuál, `resuelve` es false.
- Al revés también: el dato puede estar bajo un título que no lo nombra. Un teléfono de emergencias listado dentro de "Canales de Atención" SÍ responde "número de emergencia". Fijate en el CONTENIDO, no en el título.
- Si preguntan un VALOR (un precio, un monto, una tarifa, un plazo, un teléfono) y el texto explica DÓNDE consultarlo, cómo tramitarlo o cómo registrar el reclamo, `resuelve` es false: el dato pedido no está. Remitir a un sitio web o a otro sector NO es tener la respuesta.
- Si el fragmento trata de OTRO procedimiento —aunque comparta palabras con la consulta—, `resuelve` es false. Preguntate "¿qué pregunta responde ESTE texto?"; si no es la que se hizo, es false.
- Si un fragmento responde aunque sea una parte concreta de lo consultado, `resuelve` es true.

CONSULTA DEL OPERADOR:
{tema}

FRAGMENTOS DE LA DOCUMENTACIÓN:
{texto}
"""


def respuesta_para_clasificar(respuesta: Optional[str]) -> str:
    """Lo que ve el clasificador cuando la negativa se cambió por la lista de temas.

    En esas filas `response` es la lista que vio el operador, pero lo que pasó es que
    el modelo NO respondió: dijo "no encontré" y el sistema le ofreció temas en su
    lugar (chatbot_desambiguacion.proponer_tras_negativa). Si el clasificador leyera
    la lista tal cual, la tomaría por una respuesta y descartaría la fila como falso
    positivo — justo la señal que la fila viene a traer.

    No aplica al otro camino (la lista que sale ANTES de generar): esas filas nunca
    se marcan `sin_cobertura`, así que no llegan hasta acá.
    """
    respuesta = respuesta or ""
    if not es_desambiguacion(respuesta):
        return respuesta
    return (
        "El asistente NO respondió la consulta: no encontró la información en su "
        "documentación y, en lugar de la respuesta, le ofreció al operador elegir "
        "entre estos temas parecidos.\n\n" + respuesta
    )


def _get_client():
    from google import genai

    api_key = settings.GEMINI_CHATBOT_API_KEY or settings.GEMINI_AUDITORIA_API_KEY
    return genai.Client(api_key=api_key)


def _juzgar_fragmentos(tema: str, fragmentos: List[Tuple[str, str]]) -> Tuple[bool, Optional[str]]:
    """¿Alguno de estos fragmentos responde el tema? Devuelve (resuelve, documento).

    Lo decide LEYENDO, no por score: el reranker mide parecido de redacción y se
    hunde con la paráfrasis (ver buscar_en_corpus). Se le pasan los fragmentos
    etiquetados con su documento para que diga en CUÁL está la respuesta — si no,
    se manda a Calidad al documento mejor rankeado, que puede no ser el que la tiene.

    Ante la duda devuelve False: es preferible pedirle a Calidad que documente algo
    que ya estaba, a decirle que el bot funciona mal cuando el dato no existía.
    """
    fragmentos = [(d, t) for d, t in (fragmentos or []) if (t or "").strip()]
    if not fragmentos:
        return False, None

    from google.genai import types

    texto = "\n\n".join(f"--- Documento: {doc} ---\n{cuerpo}" for doc, cuerpo in fragmentos)
    # Modelo y razonamiento propios: este juez decide a quién se le manda el trabajo y
    # el barato contestaba que sí demasiado fácil (ver CHATBOT_VACIOS_MODELO_JUEZ).
    modelo = settings.CHATBOT_VACIOS_MODELO_JUEZ
    try:
        client = _get_client()
        resp = client.models.generate_content(
            model=modelo,
            contents=_PROMPT_RESUELVE.format(tema=(tema or "")[:500], texto=texto[:12000]),
            config=types.GenerateContentConfig(
                # NO es una tarea mecánica: hay que comparar lo que se preguntó contra lo
                # que el texto realmente responde, y con MINIMAL el modelo se quedaba en
                # "habla del mismo tema, listo". Medido: 3/7 con MINIMAL contra 6/7 con
                # MEDIUM sobre los mismos casos.
                thinking_config=razonamiento.thinking_config(
                    razonamiento.NIVEL_RAZONAMIENTO_DEFAULT, modelo=modelo,
                    include_thoughts=False,
                ),
                response_mime_type="application/json",
                response_schema=_SCHEMA_RESUELVE,  # type: ignore[arg-type]
                temperature=0.0,
            ),
        )
        import json

        _registrar_consumo(resp, modelo)
        datos = json.loads(resp.text or "{}")
        if not datos.get("resuelve"):
            return False, None

        # Solo se acepta un documento que esté entre los que se le pasaron: el
        # modelo podría devolver un nombre inventado y terminaría en pantalla.
        nombrado = (datos.get("documento") or "").strip()
        conocidos = {doc for doc, _ in fragmentos}
        return True, (nombrado if nombrado in conocidos else fragmentos[0][0])
    except Exception:
        logger.warning("No se pudo evaluar si los fragmentos resuelven el tema.", exc_info=True)
        return False, None


def _registrar_consumo(resp, modelo: str) -> None:
    """El consumo va al libro central de IA para que aparezca en /uso-ia."""
    try:
        from app.uso_ia import registrar_uso_ia

        usage = getattr(resp, "usage_metadata", None)
        registrar_uso_ia(
            feature="vacios_conocimiento",
            modelo=modelo,
            modo="sync",
            input_tokens=getattr(usage, "prompt_token_count", None),
            output_tokens=getattr(usage, "candidates_token_count", None),
        )
    except Exception:
        logger.debug("No se pudo registrar el consumo del clasificador.", exc_info=True)


def clasificar(pregunta: str, respuesta: str) -> Optional[Dict]:
    """Una llamada corta al modelo barato. Devuelve None si falla (la fila queda
    pendiente y se reintenta en el próximo tick, no se pierde)."""
    from google.genai import types

    modelo = settings.CHATBOT_VACIOS_MODELO
    try:
        client = _get_client()
        resp = client.models.generate_content(
            model=modelo,
            contents=_PROMPT_CLASIFICACION.format(
                pregunta=(pregunta or "")[:2000],
                respuesta=(respuesta or "")[:4000],
            ),
            config=types.GenerateContentConfig(
                # Clasificar una respuesta ya generada: mecánico, no hay nada que
                # razonar. Sin thinking_config el modelo razonaba dinámico y sin tope.
                thinking_config=razonamiento.thinking_config(
                    razonamiento.NIVEL_RAZONAMIENTO_MECANICO, modelo=modelo,
                    include_thoughts=False,
                ),
                response_mime_type="application/json",
                response_schema=_SCHEMA_CLASIFICACION,  # type: ignore[arg-type]
                temperature=0.0,
            ),
        )
        import json

        datos = json.loads(resp.text or "{}")
        _registrar_consumo(resp, modelo)
        return datos
    except Exception:
        logger.warning("Falló la clasificación de un vacío; se reintenta luego.", exc_info=True)
        return None


# ------------------------------------------ ¿la info existe en la documentación?

def buscar_en_corpus(slug: str, tema: str) -> List[Tuple[str, float, str]]:
    """Sondea el corpus del bot. Devuelve [(documento, score, texto)] rerankeado.

    Top_k grande a propósito: la pregunta es si el contenido EXISTE, no si el
    pipeline de producción lo encuentra con su top_k acotado.

    NO se filtra por score, y esa es la corrección del 05/08. El reranker
    (ms-marco-MiniLM, entrenado en inglés sobre pasajes tipo prosa) se hunde
    cuando la consulta está redactada distinto que el documento, sobre todo en
    contenido de referencia como listas de teléfonos. Medido sobre el chunk que
    literalmente contiene "ENRE ...: 0800-000-0001":

        "ENRE"                        +5,28
        "número de contacto del ENRE" -3,46
        "teléfono del ENRE"           -2,62

    Con el umbral en 3,0 ese chunk se descartaba y el tema salía como 'hueco':
    le pedíamos a Calidad que documentara un teléfono que ya estaba escrito.
    Quien decide es _juzgar_fragmentos(), que LEE el texto.
    """
    try:
        import app.services as services

        bot = services.chatbot_registry.get(slug)
        if bot is None:
            return []

        from llama_index.core.schema import QueryBundle

        nodos = bot.index.as_retriever(
            similarity_top_k=settings.CHATBOT_VACIO_SONDEO_TOP_K
        ).retrieve(tema)
        if not nodos:
            return []

        rerankeados = bot.reranker.postprocess_nodes(nodos, QueryBundle(tema))

        import os

        salida = []
        for n in rerankeados[:settings.CHATBOT_VACIO_FRAGMENTOS_A_LEER]:
            doc = os.path.basename(n.node.metadata.get("file_name", "")) or "?"
            salida.append((doc, float(n.score if n.score is not None else -99.0),
                           (n.node.get_content() or "")[:1500]))
        return salida
    except Exception:
        logger.warning(f"No se pudo sondear el corpus de '{slug}'.", exc_info=True)
        return []


# ------------------------------------------- lectura del contexto ya guardado

# chatBot.py arma el contexto del log así:
#   Fuentes:
#   --- Archivo: 02_dbdoc_17.md (Score: 5.5792) ---
#   Contenido:
#   <texto del chunk>
_RE_FUENTE = re.compile(r"---\s*Archivo:\s*(.+?)\s*\(Score:\s*([\d.\-]+)\)\s*---", re.I)


def _archivo_top_del_contexto(contexto: Optional[str]) -> Optional[str]:
    """Nombre del archivo del primer fragmento (el mejor rankeado) que vio el bot."""
    if not contexto:
        return None
    m = _RE_FUENTE.search(contexto)
    return m.group(1).strip() if m else None


def _fragmentos_del_contexto(contexto: Optional[str]) -> List[Tuple[str, str]]:
    """[(documento, texto)] de los chunks que se le entregaron al bot.

    Se lee del log en vez de volver a recuperar: es EXACTAMENTE lo que el modelo
    tuvo delante cuando decidió que no sabía, que es lo único que permite separar
    "no lo usó" de "no lo tenía". Y no cuesta ni una llamada.
    """
    if not contexto:
        return []
    partes = _RE_FUENTE.split(contexto)
    # split con 2 grupos: [previo, archivo, score, texto, archivo, score, texto, ...]
    archivos = [a.strip() for a in partes[1::3]]
    textos = [p.replace("Contenido:", "", 1).strip() for p in partes[3::3]]
    return [(a, t) for a, t in zip(archivos, textos) if t]


def _titulo_documento(conn, nombre_archivo: Optional[str]) -> Optional[str]:
    """Convierte el nombre interno del indexador en el título que ve Calidad.

    En pantalla se leía "04_dbdoc_19.md": un nombre que no le dice nada a quien
    tiene que ir a corregir la documentación. Si el documento ya no existe o viene
    de otro origen (Drive), se deja el nombre tal cual: es mejor que nada.
    """
    if not nombre_archivo:
        return None
    from app.chatbot_config import titulos_por_archivo

    return titulos_por_archivo(conn, [nombre_archivo]).get(nombre_archivo, nombre_archivo)


# ---------------------------------------------------------------- agrupación

class AgrupadorVacios:
    """Agrupa temas por similitud de embedding sobre Qdrant.

    Mismo patrón que SemanticCache: se busca el vecino más cercano y si supera el
    umbral se reusa su grupo. La diferencia es que acá el payload apunta a una fila
    de SQL (el vacío) en vez de guardar una respuesta.
    """

    def __init__(self, client, min_score: Optional[float] = None):
        self.client = client
        self.min_score = (
            min_score if min_score is not None else settings.CHATBOT_VACIO_AGRUPAR_MIN_SCORE
        )

    def _ensure(self, vector_size: int) -> None:
        from qdrant_client import models as qmodels

        if not self.client.collection_exists(COLECCION_VACIOS):
            self.client.create_collection(
                collection_name=COLECCION_VACIOS,
                vectors_config=qmodels.VectorParams(
                    size=vector_size, distance=qmodels.Distance.COSINE
                ),
            )

    def buscar(self, slug: str, embedding: List[float]) -> Optional[int]:
        """id del vacío existente al que pertenece este tema, o None."""
        from qdrant_client import models as qmodels

        try:
            if not self.client.collection_exists(COLECCION_VACIOS):
                return None
            hits = self.client.query_points(
                collection_name=COLECCION_VACIOS,
                query=embedding,
                limit=1,
                score_threshold=self.min_score,
                # Los temas no se comparten entre bots: "medidor" en Voltara no es lo
                # mismo que en otra campaña.
                query_filter=qmodels.Filter(
                    must=[qmodels.FieldCondition(
                        key="slug", match=qmodels.MatchValue(value=slug)
                    )]
                ),
                with_payload=True,
            ).points
            if not hits:
                return None
            return (hits[0].payload or {}).get("vacio_id")
        except Exception:
            logger.warning("Falló la búsqueda de agrupación de vacíos.", exc_info=True)
            return None

    def olvidar(self, vacio_id: int) -> None:
        """Saca el centroide de un tema que ya no existe en SQL, para no volver a
        agrupar contra él en el próximo tick."""
        from qdrant_client import models as qmodels

        try:
            if not self.client.collection_exists(COLECCION_VACIOS):
                return
            self.client.delete(
                collection_name=COLECCION_VACIOS,
                points_selector=qmodels.FilterSelector(
                    filter=qmodels.Filter(must=[qmodels.FieldCondition(
                        key="vacio_id", match=qmodels.MatchValue(value=vacio_id)
                    )])
                ),
            )
        except Exception:
            logger.warning(f"No se pudo borrar el centroide huérfano {vacio_id}.", exc_info=True)

    def registrar(self, slug: str, vacio_id: int, embedding: List[float]) -> None:
        from qdrant_client import models as qmodels

        try:
            self._ensure(len(embedding))
            self.client.upsert(
                collection_name=COLECCION_VACIOS,
                points=[qmodels.PointStruct(
                    id=str(uuid.uuid4()),
                    vector=embedding,
                    payload={"slug": slug, "vacio_id": vacio_id},
                )],
            )
        except Exception:
            logger.warning("No se pudo registrar el centroide del vacío.", exc_info=True)


# ------------------------------------------------------------------ el job

_SQL_PENDIENTES = text("""
    SELECT TOP (:limite) l.id, l.[query], l.response, l.effective_campana, l.user_id,
           l.fecha, l.score_max, l.context
    FROM pagina_web.query_chatbots_logs l
    WHERE l.sin_cobertura = 1 AND l.vacio_id IS NULL
    ORDER BY l.fecha DESC
""")


def _clasificar_causa(slug: str, tema: str, datos: Dict, score_produccion: Optional[float],
                      contexto_produccion: Optional[str]):
    """De quién es el problema. Devuelve (clasificacion, doc_sugerido, score).

    Hay TRES causas distintas y confundirlas manda el trabajo a quien no puede
    resolverlo:

    - 'fuera_de_alcance': no era una pregunta de procedimiento (saludos, pedidos de
      redacción, datos de un cliente puntual). No es trabajo de nadie.

    - 'generacion': al bot le llegó el dato en el contexto y aun así dijo que no
      sabía. Es del prompt o del modelo, no del índice ni de la documentación.

    - 'recuperacion' vs 'hueco': el dato existe en el corpus pero la consulta real
      no lo trajo (del buscador), o directamente no existe (falta documentarlo).

    QUIÉN LO DECIDE (corregido el 05/08/2026)
    -----------------------------------------
    Antes alcanzaba con que el score del reranker superara CHATBOT_VACIO_SCORE_EXISTE.
    Eso estaba mal: ese score mide que el chunk hable del MISMO TEMA, no que tenga la
    respuesta. Medido sobre los casos reales de producción, los tres que se revisaron
    estaban mal clasificados como 'generacion':

        "numero de atencion de emergencia"      score 5,58 -> chunks sin ningún teléfono
        "cambio de medidor"                     score 5,02 -> hablaba de instalar VARIOS medidores
        "documentación para cambio de tarifa"   score 6,31 -> "se revisa la documentación
                                                               adjunta", sin listar cuál

    En los tres el bot hizo bien en no inventar, y Calidad recibía "el bot lo tenía y
    no lo usó" sobre información que no estaba. Ahora lo decide el LLM leyendo el
    texto: primero el contexto que realmente se le entregó al bot (ya guardado en el
    log, así que sale gratis), y solo si ese no resuelve se sondea el corpus.
    """
    if datos.get("fuera_de_alcance"):
        return "fuera_de_alcance", None, None

    # 1) ¿El bot tenía el dato servido y no lo usó?
    resuelve, doc = _juzgar_fragmentos(tema, _fragmentos_del_contexto(contexto_produccion))
    if resuelve:
        # El documento es el que el propio bot recibió: no hace falta re-sondear.
        return "generacion", doc or _archivo_top_del_contexto(contexto_produccion), score_produccion

    # 2) No lo tenía servido: ¿existe en algún lado del corpus?
    encontrados = buscar_en_corpus(slug, tema)
    resuelve, doc = _juzgar_fragmentos(tema, [(d, t) for d, _, t in encontrados])
    if resuelve:
        score = next((s for d, s, _ in encontrados if d == doc), None)
        return "recuperacion", doc, score

    return "hueco", None, None


def _slug_a_chatbot(conn) -> Dict[str, int]:
    filas = conn.execute(text("SELECT id, slug FROM pagina_web.Chatbots")).mappings().all()
    return {f["slug"]: f["id"] for f in filas}


def _upsert_vacio(conn, chatbot_id: int, tema: str, pregunta: str, clasificacion: str,
                  doc: Optional[str], score: Optional[float], fecha) -> int:
    """Crea el vacío o suma una ocurrencia al existente."""
    fila = conn.execute(text("""
        SELECT id FROM pagina_web.ChatbotVacios
        WHERE chatbot_id = :cid AND tema = :tema
    """), {"cid": chatbot_id, "tema": tema}).first()

    if fila:
        conn.execute(text("""
            UPDATE pagina_web.ChatbotVacios
            SET ocurrencias = ocurrencias + 1,
                ultima_vez = CASE WHEN :fecha > ultima_vez THEN :fecha ELSE ultima_vez END,
                primera_vez = CASE WHEN :fecha < primera_vez THEN :fecha ELSE primera_vez END,
                updated_at = SYSDATETIME()
            WHERE id = :id
        """), {"id": fila[0], "fecha": fecha})
        return fila[0]

    nuevo = conn.execute(text("""
        INSERT INTO pagina_web.ChatbotVacios
            (chatbot_id, tema, pregunta_ejemplo, clasificacion, doc_sugerido,
             score_sugerido, primera_vez, ultima_vez)
        OUTPUT INSERTED.id
        VALUES (:cid, :tema, :ej, :clas, :doc, :score, :fecha, :fecha)
    """), {
        "cid": chatbot_id, "tema": tema, "ej": (pregunta or "")[:1000],
        "clas": clasificacion, "doc": doc, "score": score, "fecha": fecha,
    }).scalar_one()
    return int(nuevo)


def _recontar_usuarios(conn, vacio_id: int) -> None:
    """`usuarios` (operadores distintos) es lo que distingue un tema que le pasa a
    todo el equipo de uno que le pasa a una sola persona."""
    conn.execute(text("""
        UPDATE v SET usuarios = ISNULL(c.n, 1), updated_at = SYSDATETIME()
        FROM pagina_web.ChatbotVacios v
        OUTER APPLY (
            SELECT COUNT(DISTINCT l.user_id) AS n
            FROM pagina_web.query_chatbots_logs l
            WHERE l.vacio_id = v.id
        ) c
        WHERE v.id = :id
    """), {"id": vacio_id})


def procesar_vacios_pendientes(limite: Optional[int] = None) -> int:
    """Tick del scheduler: clasifica y agrupa las consultas marcadas sin cobertura.

    Devuelve cuántas filas procesó. Es seguro correrlo en paralelo con el tráfico:
    solo escribe sobre filas que todavía no tienen vacio_id.
    """
    from app.database import engine
    from app.chatbot_indexer import _get_qdrant_client
    from llama_index.core import Settings
    from app import rag_settings

    limite = limite or settings.CHATBOT_VACIOS_LOTE

    with engine.connect() as conn:
        pendientes = conn.execute(_SQL_PENDIENTES, {"limite": limite}).mappings().all()
        if not pendientes:
            return 0
        slugs = _slug_a_chatbot(conn)

    rag_settings.configure_global_settings()
    agrupador = AgrupadorVacios(_get_qdrant_client())
    procesadas = 0

    for fila in pendientes:
        slug = (fila["effective_campana"] or "").strip()
        chatbot_id = slugs.get(slug)
        if chatbot_id is None:
            # Campaña legacy sin bot ("csv no premium"): no hay a quién imputarle el
            # vacío. Se desmarca para no reprocesarla en cada tick.
            with engine.begin() as conn:
                conn.execute(text(
                    "UPDATE pagina_web.query_chatbots_logs SET sin_cobertura = 0 WHERE id = :id"
                ), {"id": fila["id"]})
            continue

        datos = clasificar(fila["query"] or "", respuesta_para_clasificar(fila["response"]))
        if datos is None:
            continue  # error transitorio: se reintenta en el próximo tick

        if not datos.get("sin_respuesta"):
            # Falso positivo del prefiltro: el bot sí había respondido.
            with engine.begin() as conn:
                conn.execute(text(
                    "UPDATE pagina_web.query_chatbots_logs SET sin_cobertura = 0 WHERE id = :id"
                ), {"id": fila["id"]})
            procesadas += 1
            continue

        tema = (datos.get("tema") or "").strip()[:300]
        if not tema:
            continue

        clasificacion, doc, score = _clasificar_causa(
            slug, tema, datos, fila["score_max"], fila["context"]
        )

        embedding = Settings.embed_model.get_query_embedding(tema)
        vacio_existente = agrupador.buscar(slug, embedding)

        with engine.begin() as conn:
            vacio_id = None
            if vacio_existente:
                # El centroide puede apuntar a un tema ya borrado (purga con
                # scripts/limpiar_vacios.py corrida contra otro Qdrant, o limpieza
                # manual). Si el UPDATE no toca ninguna fila, la referencia está
                # muerta: se descarta el centroide y se sigue como tema nuevo. Sin
                # este chequeo el UPDATE del log violaría la FK y tumbaría el tick.
                afectadas = conn.execute(text("""
                    UPDATE pagina_web.ChatbotVacios
                    SET ocurrencias = ocurrencias + 1,
                        ultima_vez = CASE WHEN :fecha > ultima_vez THEN :fecha ELSE ultima_vez END,
                        updated_at = SYSDATETIME()
                    WHERE id = :id
                """), {"id": vacio_existente, "fecha": fila["fecha"]}).rowcount
                if afectadas:
                    vacio_id = vacio_existente
                else:
                    logger.info(
                        f"Centroide huérfano (vacio_id={vacio_existente} ya no existe): "
                        f"se descarta y se abre un tema nuevo."
                    )
                    agrupador.olvidar(vacio_existente)

            if vacio_id is None:
                vacio_id = _upsert_vacio(
                    conn, chatbot_id, tema, fila["query"], clasificacion,
                    _titulo_documento(conn, doc), score, fila["fecha"],
                )
                agrupador.registrar(slug, vacio_id, embedding)

            conn.execute(text(
                "UPDATE pagina_web.query_chatbots_logs SET vacio_id = :v WHERE id = :id"
            ), {"v": vacio_id, "id": fila["id"]})
            _recontar_usuarios(conn, vacio_id)

        procesadas += 1

    logger.info(f"Vacíos de conocimiento: {procesadas}/{len(pendientes)} consultas procesadas.")
    return procesadas
