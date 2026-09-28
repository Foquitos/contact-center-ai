"""Asistente de IA para armar la documentación de los chatbots RAG.

Pensado para el equipo de Calidad, que hoy tiene que escribir a mano el markdown
que alimenta a los chatbots (pagina_web.ChatbotDocMarkdown) y no conoce cómo
estructurarlo para que el indexador lo chunkee bien (MarkdownNodeParser parte por
jerarquía de títulos; ver app/chatbot_indexer.py). Provee tres capacidades, todas
sin conocimientos técnicos:

1. formatear_documento(): toma contenido CRUDO (texto pegado y/o archivos:
   PDF/Word/Excel/imagen) y lo transforma en markdown bien estructurado para RAG.
   Devuelve UNO o VARIOS documentos: si el material cubre temas independientes, la
   IA lo separa (criterio en _CONVENCION_MARKDOWN). Separar mejora la recuperación
   porque el indexador trata cada documento como una unidad propia.

2. agregar_informacion(): integra contenido crudo NUEVO en la documentación ya
   existente. Ve TODOS los documentos del bot y rutea cada bloque nuevo al que le
   corresponde —puede modificar VARIOS de una sola pasada— o crear uno nuevo si no
   encaja en ninguno. Deduplica y marca conflictos en vez de decidir solo.

3. Loop de VERIFICACIÓN de completitud (corazón del pedido): tras generar/mergear,
   se hacen varias llamadas SEPARADAS a Gemini que comparan la fuente cruda contra
   el markdown generado y listan lo que falta o se alteró. Si hay faltantes, una
   llamada de reparación los integra y se re-verifica, hasta N rondas
   (settings.CHATBOT_DOCS_VERIFY_ROUNDS). Lo que no se pudo resolver se DEVUELVE
   siempre (nada se oculta) para que un humano lo revise.

Ninguna función guarda nada: devuelven una PROPUESTA que el usuario revisa y, si
acepta, persiste con los endpoints de chatbot_admin. Mismo patrón que
AuditorIA/asistente_plantillas.py, del que este módulo es hermano.
"""

from __future__ import annotations

import contextvars
import io
import json
import logging
import re
import threading
import unicodedata
import uuid
import zipfile
from contextlib import contextmanager
from typing import Any, Callable, Dict, List, Optional, Tuple

import pypdf
from google import genai
from google.genai import types

from app import chatbot_tablas_admin
from app.config import settings
from AuditorIA import office_a_texto, razonamiento

logger = logging.getLogger(__name__)


class RespuestaTruncada(RuntimeError):
    """La respuesta de Gemini se cortó por max_output_tokens y el JSON quedó a medias.

    No es un error de red ni un fallo transitorio: el material pedido no entra en una
    sola respuesta. Reintentarlo igual da lo mismo; hay que partir el trabajo (ver el
    camino del plan en formatear_documento).
    """


class TrabajoCancelado(BaseException):
    """Cancelaron el trabajo desde el panel mientras la IA lo procesaba.

    Hereda de BaseException y no de RuntimeError por lo mismo que asyncio.CancelledError:
    en el camino hay `except RuntimeError` / `except Exception` que siguen de largo a
    propósito (_reparar conserva el documento sin reparar, la extracción de imágenes se
    saltea), y cualquiera de ellos se tragaría la cancelación y seguiría pagando llamadas.
    """


# Cómo se entera el asistente de que lo cancelaron sin conocer la cola: doc_jobs fija
# acá una función (ver `cancelable`) y _generar_json la consulta antes de cada llamada a
# Gemini. ContextVar y no una global: el scheduler corre cada tarea en su propio hilo.
# Fuera de un trabajo de la cola (asistente de cartas, tests) queda en None.
_esta_cancelado: contextvars.ContextVar[Optional[Callable[[], bool]]] = contextvars.ContextVar(
    "asistente_docs_esta_cancelado", default=None
)


@contextmanager
def cancelable(esta_cancelado: Callable[[], bool]):
    """Dentro del bloque, cada llamada a Gemini se corta si `esta_cancelado()` da True."""
    token = _esta_cancelado.set(esta_cancelado)
    try:
        yield
    finally:
        _esta_cancelado.reset(token)


def _cortar_si_cancelaron() -> None:
    chequeo = _esta_cancelado.get()
    if chequeo is not None and chequeo():
        raise TrabajoCancelado("El trabajo se canceló desde el panel.")

# ---------------------------------------------------------------------------- #
# Cliente Gemini (perezoso, reutiliza la API key de auditoría como el asistente #
# de plantillas: es una tarea de generación vía genai.Client, no de llama_index)#
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
    return getattr(settings, "GEMINI_DOCS_MODEL", "gemini-3.8-flash")


def _rondas_verificacion() -> int:
    try:
        return max(0, int(getattr(settings, "CHATBOT_DOCS_VERIFY_ROUNDS", 3)))
    except (TypeError, ValueError):
        return 3


# Techo de markdown por documento. Cada documento se redacta en UNA respuesta, y una
# respuesta no puede pasar de max_output_tokens (65535, compartidos con el pensamiento).
# 25.000 caracteres son ~8.000 tokens: deja margen de sobra y es el número con el que se
# calcula cuántos documentos como mínimo tiene que planificar la IA.
MAX_CHARS_POR_DOCUMENTO = 25_000


def _max_chars_una_pasada() -> int:
    try:
        return max(1000, int(getattr(settings, "CHATBOT_DOCS_MAX_CHARS_UNA_PASADA", 60000)))
    except (TypeError, ValueError):
        return 60000


# MIME types aceptados como archivo crudo. Gemini los entiende de forma nativa
# (documentos e imágenes). El resto (audio/video) no tiene sentido acá.
MIME_ACEPTADOS = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/msword",  # .doc
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
    "application/vnd.ms-powerpoint",  # .ppt
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    "application/vnd.ms-excel",  # .xls
    "text/plain",
    "text/csv",
    "text/markdown",
    "image/png",
    "image/jpeg",
    "image/webp",
}


# ---------------------------------------------------------------------------- #
# Convención de markdown para RAG (compartida por transformar/reparar/mergear)  #
# ---------------------------------------------------------------------------- #
_CONVENCION_MARKDOWN = """\
CÓMO DEBE QUEDAR EL MARKDOWN (esto es clave: define cómo lo lee el buscador del chatbot):
- El sistema parte el documento por TÍTULOS de markdown para armar los fragmentos que
  el chatbot recupera. Por eso la ESTRUCTURA importa tanto como el contenido.
- Usá jerarquía de títulos con '#', '##', '###': un '#' para el tema del documento y
  '##'/'###' para cada subtema/procedimiento/pregunta. NO dejes texto "colgando" bajo
  el título principal sin un subtítulo que lo agrupe.
- Una IDEA por sección: cada '##'/'###' debe ser AUTOCONTENIDO y entenderse solo, sin
  depender de leer las secciones anteriores. Si algo se necesita en dos lados, repetilo.
- Secciones cortas y enfocadas. Si un procedimiento tiene pasos, usá listas numeradas.
  Para requisitos/condiciones, viñetas. Para datos comparables (planes, precios,
  coberturas), usá TABLAS de markdown (preservá las que ya vengan en la fuente).
- Cuando la fuente sea claramente pregunta/respuesta o FAQ, usá el patrón:
  '### <la pregunta>' seguido de la respuesta. Es lo que mejor recupera el chatbot.
- Preservá EXACTAMENTE los enlaces de imágenes en formato markdown ![alt](url): el
  chatbot los muestra al operador como apoyo visual.
- Escribí en español rioplatense, claro y profesional. No agregues relleno ni opiniones.

CUÁNDO SEPARAR EN VARIOS DOCUMENTOS (decidilo vos según el material):
- UN SOLO documento si todo el material trata de un mismo tema, procedimiento o circuito,
  aunque sea largo. Los títulos internos ya alcanzan para que el buscador lo parta bien.
- VARIOS documentos cuando el material mezcla temas claramente INDEPENDIENTES entre sí:
  procedimientos que no se usan juntos, productos/campañas distintas, un manual operativo
  frente a un listado de preguntas frecuentes, o normativa frente a instructivos de sistema.
  La prueba práctica: si un operador consultaría un tema sin necesitar nunca el otro, van
  separados.
- NUNCA partas un mismo procedimiento a la mitad: los pasos, requisitos y excepciones de un
  circuito viven juntos en el mismo documento.
- Ante la duda, MENOS documentos y más grandes. Separar de más deja fragmentos sin contexto.
- Cada documento lleva un título propio, claro y específico ("Altas de servicio", no
  "Documento 2"), y debe entenderse sin leer los otros: si dos comparten una definición o
  requisito, repetila en ambos.

REGLA DE ORO — CERO PÉRDIDA Y CERO INVENCIÓN:
- Tenés que incluir TODA la información de la fuente: datos, montos, plazos, requisitos,
  excepciones, notas al pie, contenido de tablas e imágenes. No resumas al punto de
  perder un dato. Ante la duda, incluilo.
- NO inventes NADA que no esté en la fuente. No completes con conocimiento propio ni
  supongas valores. Si algo está incompleto o ambiguo en la fuente, dejalo señalado con
  un texto entre [corchetes] (ej.: "[dato no especificado en la fuente]") en vez de
  inventarlo.
"""


# ---------------------------------------------------------------------------- #
# Archivos crudos (multimodal): inline primero, File API como último recurso    #
# ---------------------------------------------------------------------------- #
# Los modelos Gemini 3.x rechazan con 403 PERMISSION_DENIED ("The caller does not
# have permission") cualquier `file_uri` de la File API: el archivo sube y queda
# ACTIVE, pero el generate_content que lo referencia muere. Comprobado el
# 2026-08-14 contra ambas API keys: fallan 3.1-flash-lite, 3.5, 3.6, 3.7 y
# flash-latest; andan 2.5-flash, 2.5-pro y 3-flash-preview. Mandar los bytes
# inline (Part.from_bytes) funciona con TODOS los modelos, así que ese es el
# camino por defecto.
#
# El límite es el tamaño del request: la API corta en 20 MB por request contando
# TODO (bytes inline + prompt). Por eso se inlinea hasta 15 MB acumulados entre
# todas las fuentes y lo que exceda cae a la File API — que hoy, con un modelo
# 3.x, va a fallar; queda como camino para modelos que sí la aceptan y para no
# perder la funcionalidad si Google la rehabilita.
INLINE_MAX_BYTES = 15 * 1024 * 1024


def _subir_archivo(client: genai.Client, datos: bytes, mime: str, nombre: Optional[str]):
    """Sube un archivo crudo (PDF/Word/Excel/imagen) a la File API de Gemini y espera
    a que quede ACTIVE. Devuelve el objeto de archivo subido.

    El upload_file_with_retry de gemini_files es específico de audio (convierte a mp3),
    por eso acá subimos el archivo tal cual.
    """
    from AuditorIA.gemini_files import esperar_archivo_activo

    display_name = (nombre or "fuente")[:120]
    file_io = io.BytesIO(datos)
    file_io.seek(0)
    config = types.UploadFileConfig(mime_type=mime, display_name=display_name)
    subido = client.files.upload(file=file_io, config=config)
    esperar_archivo_activo(client, subido)
    return subido


@contextmanager
def _fuentes_activas(client: genai.Client, fuentes: List[Dict[str, Any]]):
    """Convierte la lista de fuentes crudas en `parts` de Gemini y las cede.

    Los archivos van inline (Part.from_bytes) mientras entren en INLINE_MAX_BYTES:
    los mismos parts se reutilizan en transformar + todas las rondas de verificación,
    así que se arman una sola vez. Lo que no entra se sube a la File API y se BORRA
    al salir. Los textos van como parts de texto.
    """
    partes: List[types.Part] = []
    subidos: List[Any] = []
    presupuesto_inline = INLINE_MAX_BYTES
    try:
        for i, f in enumerate(fuentes):
            tipo = f.get("tipo")
            nombre = f.get("nombre") or f"Fuente {i + 1}"
            if tipo == "texto":
                texto = (f.get("texto") or "").strip()
                if texto:
                    partes.append(types.Part.from_text(text=f"=== FUENTE (texto): {nombre} ===\n{texto}"))
            elif tipo == "archivo":
                datos = f.get("datos")
                mime = f.get("mime")
                if not datos or not mime:
                    continue
                # Word/PowerPoint/Excel viajan como TEXTO ya convertido: Gemini acepta
                # sus bytes inline sin protestar pero no los lee (ver office_a_texto).
                # El PDF y las imágenes sí los lee, y van inline como siempre.
                texto_office = f.get("texto_office")
                if texto_office is None and not f.get("office_evaluado"):
                    texto_office = office_a_texto.extraer_texto(datos, nombre)
                if texto_office is not None:
                    cuerpo = texto_office.strip() or (
                        "(El archivo no traía texto: probablemente sean capturas/imágenes, "
                        "que se extrajeron aparte y están listadas como imágenes disponibles.)"
                    )
                    partes.append(types.Part.from_text(
                        text=f"=== FUENTE (archivo Office convertido a texto): {nombre} ===\n{cuerpo}"))
                    continue
                partes.append(types.Part.from_text(text=f"=== FUENTE (archivo): {nombre} ==="))
                if len(datos) <= presupuesto_inline:
                    presupuesto_inline -= len(datos)
                    partes.append(types.Part.from_bytes(data=datos, mime_type=mime))
                else:
                    logger.warning(
                        "La fuente '%s' (%.1f MB) no entra en el presupuesto inline: va por la File API, "
                        "que los modelos Gemini 3.x rechazan con 403.", nombre, len(datos) / 1024 / 1024,
                    )
                    subido = _subir_archivo(client, datos, mime, nombre)
                    subidos.append(subido)
                    partes.append(types.Part.from_uri(file_uri=subido.uri, mime_type=subido.mime_type))
        if not partes:
            raise ValueError("No hay contenido crudo para procesar (todas las fuentes vinieron vacías).")
        yield partes
    finally:
        for subido in subidos:
            try:
                client.files.delete(name=subido.name)
            except Exception:
                logger.warning("No se pudo borrar el archivo temporal de Gemini %s.", getattr(subido, "name", "?"))


# ---------------------------------------------------------------------------- #
# Helpers de parsing / consumo (calcados de asistente_plantillas)              #
# ---------------------------------------------------------------------------- #
def _texto_de_respuesta(response) -> str:
    """Ensambla el texto de la respuesta ignorando las partes de 'pensamiento'."""
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
    return "".join(chunks) if chunks else (response.text or "")


def _registrar_consumo(response, user_id: Optional[int], ref_label: str, intento: int,
                       modelo: Optional[str] = None) -> None:
    """Registra el consumo de Gemini en pagina_web.IA_Uso (feature='asistente_docs').

    Cada llamada (transformar, cada ronda de verificación, reparación, merge) es
    facturable aparte, por eso lleva un ref_id único. Best-effort.

    `modelo`: el que se usó DE VERDAD. No alcanza con `_modelo()`: el asistente de
    cartas comparte este camino pero corre en otro modelo (ver GEMINI_CARTAS_MODEL),
    y costear sus llamadas a la tarifa del modelo de docs las mostraría ~5x más caras
    de lo que son en /uso-ia."""
    try:
        from app.uso_ia import registrar_uso_ia
        registrar_uso_ia(
            feature="asistente_docs",
            modelo=modelo or _modelo(),
            modo="sync",
            user_id=user_id,
            usage=getattr(response, "usage_metadata", None),
            ref_id=f"docs:{uuid.uuid4()}",
            extras={"accion": ref_label, "intento": intento},
        )
    except Exception:
        logger.exception("No se pudo registrar el consumo de IA del asistente de docs.")


def _generar_json(
    system_instruction: str,
    user_parts: List[types.Part],
    response_schema: types.Schema,
    *,
    temperatura: float = 0.4,
    nivel_razonamiento: str = razonamiento.NIVEL_RAZONAMIENTO_ASISTENTES,
    modelo: Optional[str] = None,
    user_id: Optional[int] = None,
    ref_label: str = "docs",
) -> Dict[str, Any]:
    """Llamado genérico a Gemini con salida JSON estructurada y multimodal.

    `nivel_razonamiento`: por defecto HIGH. Este asistente arma el markdown del
    conocimiento que después consultan miles de preguntas al chatbot, y corre unas
    pocas veces por día: es exactamente donde conviene que piense de más. Hasta el
    2026-08-19 acá había un `thinking_budget` (4096/2048 según el paso) que en
    Gemini 3.x no se respeta — ver AuditorIA/razonamiento.py.

    `modelo`: None = el del asistente de docs (GEMINI_DOCS_MODEL). El asistente de
    cartas reusa esta función con su propio modelo, más chico y más barato, porque su
    tarea es mecánica; el modelo elegido decide además si MINIMAL se puede pedir de
    verdad o degrada a LOW (ver razonamiento.nivel_para_modelo)."""
    modelo_usado = modelo or _modelo()
    client = _get_client()
    config = types.GenerateContentConfig(
        temperature=temperatura,
        max_output_tokens=65535,
        response_mime_type="application/json",
        response_schema=response_schema,
        system_instruction=[types.Part.from_text(text=system_instruction)],
        thinking_config=razonamiento.thinking_config(
            nivel_razonamiento, modelo=modelo_usado, include_thoughts=False,
        ),
    )
    contents = [types.Content(role="user", parts=user_parts)]

    ultimo_error: Optional[Exception] = None
    truncada = False
    for intento in range(3):
        # Fuera del try: el except de abajo reintenta cualquier error de la API.
        _cortar_si_cancelaron()
        try:
            response = client.models.generate_content(
                model=modelo_usado, contents=contents, config=config,  # type: ignore
            )
            _registrar_consumo(response, user_id, ref_label, intento, modelo=modelo_usado)
            if not response or not getattr(response, "candidates", None):
                raise RuntimeError("La IA no devolvió ninguna respuesta.")
            texto = _texto_de_respuesta(response)
            if not texto:
                raise RuntimeError("La IA devolvió una respuesta vacía.")
            try:
                return json.loads(texto, strict=False)
            except json.JSONDecodeError as e:
                # JSON cortado a la mitad = la respuesta no entró en max_output_tokens.
                # Reintentar el MISMO request no arregla nada (el material sigue siendo
                # el mismo), así que se corta acá y el llamador decide: formatear_documento
                # rehace el trabajo por el camino del plan, un documento por llamada.
                if str(getattr(response.candidates[0], "finish_reason", "")).endswith("MAX_TOKENS"):
                    raise RespuestaTruncada(
                        "La respuesta de la IA no entró en una sola llamada."
                    ) from e
                raise
        except RespuestaTruncada:
            raise
        except json.JSONDecodeError as e:
            ultimo_error = e  # JSON malformado por otro motivo: reintentar
            truncada = True
        except Exception as e:  # noqa: BLE001 - errores de API/red
            ultimo_error = e
    if truncada and isinstance(ultimo_error, json.JSONDecodeError):
        raise RespuestaTruncada(
            f"La IA devolvió un JSON incompleto tres veces seguidas: {ultimo_error}"
        )
    raise RuntimeError(f"No se pudo obtener una respuesta válida de la IA: {ultimo_error}")


# ---------------------------------------------------------------------------- #
# Esquemas de respuesta                                                         #
# ---------------------------------------------------------------------------- #
_SCHEMA_DOCUMENTO = types.Schema(
    type=types.Type.OBJECT,
    required=["titulo", "markdown"],
    properties={
        "titulo": types.Schema(
            type=types.Type.STRING,
            description="Título corto y descriptivo del documento (es el nombre con el que lo ve el equipo de calidad).",
        ),
        "markdown": types.Schema(
            type=types.Type.STRING,
            description="El documento markdown COMPLETO, estructurado para RAG según la convención.",
        ),
        "secciones": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(type=types.Type.STRING),
            description="Títulos de sección de ESTE documento (para un índice rápido).",
        ),
    },
)

_SCHEMA_DOCUMENTOS = types.Schema(
    type=types.Type.OBJECT,
    required=["documentos"],
    properties={
        "documentos": types.Schema(
            type=types.Type.ARRAY,
            items=_SCHEMA_DOCUMENTO,
            description="Uno o VARIOS documentos, según el criterio de separación. Cada uno autocontenido.",
        ),
        "motivo_separacion": types.Schema(
            type=types.Type.STRING,
            description="Si separaste en varios documentos, explicá en una frase por qué. Vacío si es uno solo.",
        ),
        "notas": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(type=types.Type.STRING),
            description="Supuestos que tomaste y cosas que el usuario debería revisar. En lenguaje simple. Vacío si no hay.",
        ),
    },
)

_SCHEMA_PLAN = types.Schema(
    type=types.Type.OBJECT,
    required=["documentos"],
    properties={
        "documentos": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                required=["titulo", "alcance"],
                properties={
                    "titulo": types.Schema(type=types.Type.STRING, description="Título del documento planificado."),
                    "alcance": types.Schema(
                        type=types.Type.STRING,
                        description="Qué partes/temas del material cubre ESTE documento. Sé específico: es lo que va a guiar la redacción y define qué NO va acá.",
                    ),
                },
            ),
            description="Los documentos en los que conviene repartir el material, en orden.",
        ),
        "motivo_separacion": types.Schema(type=types.Type.STRING, description="Por qué se reparte así, en una frase."),
    },
)

_SCHEMA_FALTANTE = types.Schema(
    type=types.Type.OBJECT,
    required=["dato", "severidad"],
    properties={
        "dato": types.Schema(type=types.Type.STRING, description="El dato de la FUENTE que falta, se alteró o quedó ambiguo en el markdown."),
        "documento": types.Schema(type=types.Type.STRING, description="Título EXACTO del documento del conjunto donde debería estar este dato."),
        "seccion_sugerida": types.Schema(type=types.Type.STRING, description="En qué sección de ese documento debería ir."),
        "severidad": types.Schema(type=types.Type.STRING, enum=["alta", "media", "baja"], description="alta=dato importante omitido; baja=matiz menor."),
    },
)

_SCHEMA_INVENCION = types.Schema(
    type=types.Type.OBJECT,
    required=["afirmacion"],
    properties={
        "afirmacion": types.Schema(type=types.Type.STRING, description="La afirmación del markdown que NO está respaldada por las fuentes."),
        "documento": types.Schema(type=types.Type.STRING, description="Título EXACTO del documento del conjunto donde aparece."),
    },
)

_SCHEMA_CONFLICTO = types.Schema(
    type=types.Type.OBJECT,
    required=["descripcion"],
    properties={
        "descripcion": types.Schema(type=types.Type.STRING, description="Contradicción detectada (ej.: la fuente nueva dice algo distinto a lo que ya estaba)."),
        "severidad": types.Schema(type=types.Type.STRING, enum=["alta", "media", "baja"]),
    },
)

_SCHEMA_VERIFICACION = types.Schema(
    type=types.Type.OBJECT,
    required=["completo", "faltantes"],
    properties={
        "completo": types.Schema(type=types.Type.BOOLEAN, description="true SOLO si el markdown contiene toda la información de la fuente, sin alteraciones ni invenciones."),
        "faltantes": types.Schema(type=types.Type.ARRAY, items=_SCHEMA_FALTANTE, description="Datos de la fuente que faltan/alterados en el markdown. Vacío si completo."),
        "invenciones": types.Schema(type=types.Type.ARRAY, items=_SCHEMA_INVENCION, description="Afirmaciones del markdown que NO están respaldadas por la fuente (alucinaciones). Vacío si no hay."),
        "conflictos": types.Schema(type=types.Type.ARRAY, items=_SCHEMA_CONFLICTO, description="Contradicciones internas detectadas. Vacío si no hay."),
    },
)

_SCHEMA_DOC_MERGE = types.Schema(
    type=types.Type.OBJECT,
    required=["titulo", "markdown", "accion"],
    properties={
        "doc_id": types.Schema(
            type=types.Type.INTEGER,
            description="ID del documento existente que estás modificando (el que figura en su encabezado). Usá 0 si es un documento NUEVO.",
        ),
        "titulo": types.Schema(type=types.Type.STRING, description="Título del documento (el existente, o uno nuevo si accion='crear')."),
        "markdown": types.Schema(type=types.Type.STRING, description="El markdown COMPLETO y actualizado de ESTE documento."),
        "accion": types.Schema(type=types.Type.STRING, enum=["actualizar", "crear"], description="'actualizar' si modificaste uno existente; 'crear' si es nuevo."),
        "cambios": types.Schema(
            type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING),
            description="Qué se agregó/cambió en ESTE documento, en lenguaje simple.",
        ),
    },
)

_SCHEMA_RUTEO_MERGE = types.Schema(
    type=types.Type.OBJECT,
    required=["asignaciones"],
    properties={
        "asignaciones": types.Schema(
            type=types.Type.ARRAY,
            description="Un ítem por documento EXISTENTE que hay que tocar. Los que no cambian NO se incluyen.",
            items=types.Schema(
                type=types.Type.OBJECT,
                required=["doc_id", "que_integrar"],
                properties={
                    "doc_id": types.Schema(type=types.Type.INTEGER, description="doc_id del documento existente."),
                    "titulo": types.Schema(type=types.Type.STRING),
                    "que_integrar": types.Schema(
                        type=types.Type.STRING,
                        description="Qué información nueva va a ESTE documento y en qué sección, con el detalle "
                                    "suficiente para integrarla después sin volver a decidir.",
                    ),
                },
            ),
        ),
        "nuevos": types.Schema(
            type=types.Type.ARRAY,
            description="Documentos a CREAR con lo que no encaja en ninguno existente. Vacío si todo encaja.",
            items=types.Schema(
                type=types.Type.OBJECT,
                required=["titulo", "alcance"],
                properties={
                    "titulo": types.Schema(type=types.Type.STRING),
                    "alcance": types.Schema(type=types.Type.STRING, description="Qué parte del material nuevo le corresponde."),
                },
            ),
        ),
        "ruteo": types.Schema(
            type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING),
            description="Una línea por cada bloque de información nueva explicando a qué documento fue y por qué.",
        ),
        "conflictos": types.Schema(type=types.Type.ARRAY, items=_SCHEMA_CONFLICTO),
        "notas": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
    },
)

_SCHEMA_MERGE = types.Schema(
    type=types.Type.OBJECT,
    required=["documentos"],
    properties={
        "documentos": types.Schema(
            type=types.Type.ARRAY, items=_SCHEMA_DOC_MERGE,
            description="SOLO los documentos que modificaste o creaste. Los que no cambian NO se incluyen.",
        ),
        "ruteo": types.Schema(
            type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING),
            description="Una línea por cada bloque de información nueva explicando a qué documento fue y por qué.",
        ),
        "conflictos": types.Schema(type=types.Type.ARRAY, items=_SCHEMA_CONFLICTO, description="Casos donde la información nueva contradice a la existente y hay que revisar a mano."),
        "notas": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING), description="Supuestos y cosas a revisar. Vacío si no hay."),
    },
)


# ---------------------------------------------------------------------------- #
# Loop de verificación de completitud (compartido)                              #
# ---------------------------------------------------------------------------- #
def _serializar_docs(documentos: List[Dict[str, Any]]) -> str:
    """Representa un CONJUNTO de documentos como un solo texto, para que la
    verificación evalúe la completitud sobre el conjunto (un dato puede estar en
    cualquiera de los documentos: lo que importa es que no se haya perdido)."""
    bloques = []
    for i, d in enumerate(documentos, start=1):
        cabecera = f"=== DOCUMENTO {i}: {d.get('titulo') or '(sin título)'}"
        if d.get("doc_id"):
            cabecera += f" [doc_id={d['doc_id']}]"
        bloques.append(f"{cabecera} ===\n{d.get('markdown') or ''}")
    return "\n\n".join(bloques)


def _normalizar_documentos(crudos: Any, titulo_fallback: Optional[str] = None) -> List[Dict[str, Any]]:
    """Normaliza la lista de documentos que devolvió la IA (descarta los vacíos)."""
    out: List[Dict[str, Any]] = []
    for i, d in enumerate(crudos or []):
        if not isinstance(d, dict):
            continue
        markdown = (d.get("markdown") or "").strip()
        if not markdown:
            continue
        titulo = (d.get("titulo") or "").strip()
        if not titulo:
            titulo = titulo_fallback if (titulo_fallback and i == 0) else f"Documento {i + 1}"
        doc = {"titulo": titulo, "markdown": markdown, "secciones": d.get("secciones") or []}
        for extra in ("doc_id", "accion", "cambios"):
            if d.get(extra) is not None:
                doc[extra] = d[extra]
        out.append(doc)
    return out


def _verificar(documentos: List[Dict[str, Any]], fuentes_parts: List[types.Part],
               user_id: Optional[int], con_docs_previos: bool = False) -> Dict[str, Any]:
    """Una ronda de verificación sobre el CONJUNTO de documentos: compara las fuentes
    crudas contra todos los documentos y lista faltantes/invenciones/conflictos.
    Contexto separado (solo ve fuentes + documentos)."""
    extra = (
        " Además del contenido de las fuentes, los documentos DEBEN preservar todo lo que ya "
        "traían las versiones previas (no perder información al integrar lo nuevo)."
        if con_docs_previos else ""
    )
    system = (
        "Sos un auditor meticuloso de documentación. Recibís unas FUENTES crudas y un CONJUNTO "
        "de DOCUMENTOS markdown que alguien generó a partir de ellas. Tu única tarea es detectar "
        "qué información de las fuentes NO quedó reflejada (o quedó alterada/ambigua) en NINGUNO "
        "de los documentos, y qué afirmaciones de los documentos NO están respaldadas por las "
        "fuentes (invenciones)." + extra + "\n"
        "IMPORTANTE: el material puede estar repartido entre varios documentos. Un dato NO falta "
        "si aparece en cualquiera de ellos; no reportes como faltante algo que está en otro "
        "documento del conjunto, ni la repetición deliberada de definiciones compartidas.\n"
        "Sé exhaustivo con datos concretos: montos, plazos, requisitos, excepciones, "
        "filas de tablas, notas. No reportes diferencias de estilo o de orden si el dato "
        "está presente. Si todo está, devolvé completo=true y listas vacías."
    )
    partes = list(fuentes_parts) + [
        types.Part.from_text(text=f"=== DOCUMENTOS GENERADOS A VERIFICAR ===\n{_serializar_docs(documentos)}")
    ]
    resultado = _generar_json(
        system, partes, _SCHEMA_VERIFICACION,
        temperatura=0.1, user_id=user_id, ref_label="verificar",
    )
    resultado.setdefault("completo", False)
    resultado.setdefault("faltantes", [])
    resultado.setdefault("invenciones", [])
    resultado.setdefault("conflictos", [])
    return resultado


def _agrupar_por_documento(items: List[Dict[str, Any]], documentos: List[Dict[str, Any]],
                           clave: str) -> Dict[int, List[Dict[str, Any]]]:
    """Reparte los hallazgos de la verificación entre los documentos a los que apuntan
    (por título). Lo que no matchea va al primer documento, que es el criterio menos
    malo: preferimos ubicar el dato en algún lado antes que descartarlo."""
    por_titulo = {(d.get("titulo") or "").strip().lower(): i for i, d in enumerate(documentos)}
    agrupados: Dict[int, List[Dict[str, Any]]] = {}
    for item in items or []:
        if not isinstance(item, dict):
            item = {clave: str(item)}
        destino = por_titulo.get((item.get("documento") or "").strip().lower(), 0)
        agrupados.setdefault(destino, []).append(item)
    return agrupados


def _reparar(documentos: List[Dict[str, Any]], fuentes_parts: List[types.Part],
             verificacion: Dict[str, Any], user_id: Optional[int]) -> List[Dict[str, Any]]:
    """Integra los faltantes y quita las invenciones, reparando DE A UN DOCUMENTO por
    llamada (solo los que tienen hallazgos).

    Reparar todo el conjunto en una sola respuesta no escala: con material grande el
    JSON se trunca contra max_output_tokens. Además así los documentos sin hallazgos
    ni se re-emiten (no hay riesgo de que se degraden al reescribirlos)."""
    faltantes_por_doc = _agrupar_por_documento(verificacion.get("faltantes"), documentos, "dato")
    invenciones_por_doc = _agrupar_por_documento(verificacion.get("invenciones"), documentos, "afirmacion")
    indices = sorted(set(faltantes_por_doc) | set(invenciones_por_doc))

    system = (
        "Sos un editor técnico. Recibís unas FUENTES, UN documento markdown y un REPORTE de lo "
        "que le falta o le sobra. Devolvé ESE documento CORREGIDO:\n"
        "- Agregá los datos faltantes en la sección que corresponda (creá la sección si hace falta).\n"
        "- Quitá o corregí las invenciones (lo que no esté respaldado por las fuentes).\n"
        "- NO reescribas ni borres lo que ya estaba bien. Cambios mínimos y quirúrgicos.\n"
        "- Devolvé exactamente UN documento, con su MISMO título.\n\n"
        + _CONVENCION_MARKDOWN
    )

    reparados = list(documentos)
    for i in indices:
        if i >= len(reparados):
            continue
        objetivo = reparados[i]
        reporte = json.dumps(
            {"faltantes": faltantes_por_doc.get(i, []), "invenciones": invenciones_por_doc.get(i, [])},
            ensure_ascii=False, indent=2,
        )
        partes = list(fuentes_parts) + [
            types.Part.from_text(
                text=f"=== DOCUMENTO A CORREGIR: {objetivo.get('titulo')} ===\n{objetivo.get('markdown')}"),
            types.Part.from_text(text=f"=== REPORTE DE FALTANTES / INVENCIONES DE ESTE DOCUMENTO ===\n{reporte}"),
        ]
        try:
            resultado = _generar_json(
                system, partes, _SCHEMA_DOCUMENTOS,
                temperatura=0.3, user_id=user_id, ref_label="reparar",
            )
        except RuntimeError:
            logger.warning("No se pudo reparar el documento '%s'; se conserva sin cambios.",
                           objetivo.get("titulo"))
            continue
        nuevos = _normalizar_documentos(resultado.get("documentos"), titulo_fallback=objetivo.get("titulo"))
        if not nuevos:
            continue
        # Se conserva el título y los metadatos del original (doc_id/accion del merge).
        reparado = dict(objetivo)
        reparado["markdown"] = nuevos[0]["markdown"]
        reparados[i] = reparado
    return reparados


def _loop_verificacion(documentos: List[Dict[str, Any]], fuentes_parts: List[types.Part],
                       user_id: Optional[int], con_docs_previos: bool = False) -> Dict[str, Any]:
    """Corre hasta N rondas de verificar→reparar sobre el conjunto. Devuelve los
    documentos finales y el reporte RESIDUAL (lo que quedó sin resolver tras la última
    ronda, que siempre se muestra)."""
    rondas = _rondas_verificacion()
    verificacion = {"completo": True, "faltantes": [], "invenciones": [], "conflictos": []}
    rondas_hechas = 0
    for _ in range(rondas):
        verificacion = _verificar(documentos, fuentes_parts, user_id, con_docs_previos)
        rondas_hechas += 1
        faltantes = verificacion.get("faltantes") or []
        invenciones = verificacion.get("invenciones") or []
        if verificacion.get("completo") and not faltantes and not invenciones:
            break
        documentos = _reparar(documentos, fuentes_parts, verificacion, user_id)
    return {
        "documentos": documentos,
        "faltantes_residuales": verificacion.get("faltantes") or [],
        # La verificación las devuelve como objetos {afirmacion, documento} para poder
        # dirigir la reparación; hacia afuera viajan como texto legible.
        "invenciones_residuales": [
            (f"{i.get('afirmacion', '')}" + (f" (en: {i['documento']})" if i.get("documento") else ""))
            if isinstance(i, dict) else str(i)
            for i in (verificacion.get("invenciones") or [])
        ],
        "conflictos": verificacion.get("conflictos") or [],
        "rondas_verificacion": rondas_hechas,
        "verificado_ok": bool(verificacion.get("completo")) and not (verificacion.get("faltantes") or verificacion.get("invenciones")),
    }


# ---------------------------------------------------------------------------- #
# Validación de la entrada                                                      #
# ---------------------------------------------------------------------------- #
def _validar_fuentes(fuentes: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    limpias: List[Dict[str, Any]] = []
    for f in (fuentes or []):
        tipo = f.get("tipo")
        if tipo == "texto" and (f.get("texto") or "").strip():
            limpias.append(f)
        elif tipo == "archivo" and f.get("datos"):
            mime = f.get("mime")
            if mime not in MIME_ACEPTADOS:
                raise ValueError(f"Tipo de archivo no soportado: {mime}. Aceptados: PDF, Word, PowerPoint, Excel, texto, CSV o imagen.")
            nombre = f.get("nombre") or "El archivo"
            # El Office viejo (binario) no lo lee nadie: ni nosotros ni Gemini. Se
            # frena acá, con un mensaje que dice qué hacer, en vez de procesar un
            # archivo vacío y devolver documentación inventada.
            if office_a_texto.es_office_legacy(f["datos"]):
                raise ValueError(
                    f"'{nombre}' está guardado en el formato viejo de Office (.doc/.xls/.ppt), que no se "
                    f"puede leer. Abrilo en Office y guardalo como .docx/.xlsx/.pptx, o exportalo a PDF."
                )
            # Los Office modernos se convierten a texto UNA sola vez acá: el resultado
            # lo usan tanto la estimación de tamaño como el armado de los parts.
            limpias.append({**f, "texto_office": office_a_texto.extraer_texto(f["datos"], nombre),
                            "office_evaluado": True})
    if not limpias:
        raise ValueError("Pegá texto o subí al menos un archivo con contenido para procesar.")
    return limpias


# Caracteres de contenido que se le asignan como mínimo a cada página de un PDF. El
# texto extraíble solo no alcanza: los manuales traen capturas, diagramas y tablas
# como imagen, que la IA sí lee y vuelca al markdown. Calibrado con el caso que lo
# destapó (Manual Comercial de Hidra, 2026-09-14): 202 páginas, 69.147 caracteres de
# texto extraíble y ~344.000 de markdown generado (~1.700 por página, inflado porque
# se lo obligó a partirse de más).
CHARS_POR_PAGINA_PDF = 1_500


def _tamano_pdf(datos: bytes) -> Optional[int]:
    """Caracteres que estimamos que trae un PDF, o None si no se puede leer.

    Se mide por su contenido (texto extraíble y cantidad de páginas), no por sus bytes:
    un PDF pesado suele serlo por las imágenes. Con bytes/10, ese manual de Hidra (20 MB)
    se estimaba en 2 millones de caracteres. La IA quedaba obligada a planificar al menos
    82 documentos y cada uno se redactaba en una llamada con el PDF entero: el primer
    intento tardó 72 minutos y devolvió 126 documentos, y entre ese y un segundo intento
    se fueron 234 llamadas y ~30 millones de tokens en un día, con la cola de
    documentación bloqueada para todos los demás.

    Equivocarse para abajo sale barato: si un documento del plan no entra en una
    respuesta, se subdivide solo (_redactar_subdividiendo). Para arriba multiplica las
    llamadas.
    """
    try:
        lector = pypdf.PdfReader(io.BytesIO(datos))
        paginas = len(lector.pages)
    except Exception:  # noqa: BLE001 - PDF roto o cifrado: se cae a la estimación por bytes
        return None
    texto = 0
    for pagina in lector.pages:
        try:
            texto += len(pagina.extract_text() or "")
        except Exception:  # noqa: BLE001 - una página ilegible no invalida la estimación
            continue
    return max(texto, paginas * CHARS_POR_PAGINA_PDF)


def _tamano_archivo(datos: bytes) -> int:
    """Caracteres de contenido que estimamos que trae un archivo.

    Es la estimación para lo que NO se convierte a texto antes de mandarlo (PDF e
    imágenes): los Office modernos se convierten en _validar_fuentes y se miden por su
    largo real. Los PDF se miden por páginas y texto extraíble (ver _tamano_pdf).

    Los bytes crudos MIENTEN en los dos sentidos. Para arriba en un PDF con imágenes, y
    para abajo en los formatos Office modernos (.xlsx/.docx/.pptx), que son
    ZIPs: un Excel de 200 KB puede traer 218.000 caracteres de datos. Con la cuenta de
    /10 caía en 20.166, entraba por debajo del umbral de una sola pasada y la respuesta
    se cortaba a la mitad (JSON incompleto). Para esos casos medimos el tamaño YA
    descomprimido y le aplicamos el factor de markup del XML (medido sobre planillas
    reales: ~5 bytes de XML por carácter de dato).
    """
    if b"%PDF-" in datos[:1024]:
        tamano_pdf = _tamano_pdf(datos)
        if tamano_pdf is not None:
            return tamano_pdf
    try:
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            descomprimido = sum(info.file_size for info in z.infolist())
        if descomprimido > len(datos):
            return descomprimido // 5
    except (zipfile.BadZipFile, OSError, ValueError):
        pass  # No es un ZIP (PDF, imagen, texto plano): se estima por bytes.
    return len(datos) // 10


def _tamano_estimado(fuentes: List[Dict[str, Any]]) -> int:
    """Tamaño aproximado del material crudo, en caracteres."""
    total = 0
    for f in fuentes:
        if f.get("tipo") == "texto":
            total += len(f.get("texto") or "")
        elif f.get("texto_office") is not None:
            # Un Office ya convertido no necesita estimación: sabemos exactamente
            # cuántos caracteres le van a llegar a la IA.
            total += len(f["texto_office"])
        elif f.get("datos"):
            total += _tamano_archivo(f["datos"])
    return total


def _planificar_documentos(fuentes_parts: List[types.Part], titulo: Optional[str],
                           contexto: Optional[Dict[str, Any]], user_id: Optional[int],
                           docs_minimos: int = 0,
                           alcance_padre: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Primera fase del camino para material grande: decide EN QUÉ documentos se reparte
    el material (títulos + alcance), sin redactar todavía. Respuesta corta, sin riesgo de
    truncado.

    `docs_minimos` es el piso que impone el límite de salida: cada documento se redacta en
    UNA llamada, así que el plan tiene que partir el material en suficientes pedazos como
    para que ninguno se pase de MAX_CHARS_POR_DOCUMENTO. Sin este piso, con una planilla
    de 1789 filas la IA planificaba 2 o 3 documentos "temáticos" y cada uno se truncaba
    igual que la pasada única.

    `alcance_padre` re-planifica SOLO un pedazo (cuando ese pedazo, ya redactándose, no
    entró en una respuesta).
    """
    system = (
        "Sos un documentalista experto. Vas a planificar (NO redactar todavía) cómo repartir "
        "un material grande en documentos markdown para el buscador de un chatbot de soporte.\n\n"
        + _CONVENCION_MARKDOWN
        + "\n\nDevolvé SOLO el plan: para cada documento, su título y su alcance (qué partes del "
        "material le corresponden). El alcance tiene que ser lo bastante preciso como para que, "
        "al redactar cada documento por separado, quede claro qué entra y qué no, y para que "
        "ENTRE TODOS los documentos quede cubierto el material COMPLETO, sin huecos ni "
        "superposiciones (más allá de definiciones compartidas que convenga repetir)."
        + (
            "\n\nLÍMITE DE TAMAÑO (es una restricción técnica, no una preferencia):\n"
            f"- Cada documento se redacta en UNA sola respuesta y no puede superar los "
            f"{MAX_CHARS_POR_DOCUMENTO:,} caracteres de markdown.\n"
            f"- Por el volumen del material, el plan necesita AL MENOS {docs_minimos} documentos. "
            "Podés hacer más, nunca menos.\n"
            "- Si el material es un listado o una tabla larga (clientes, contactos, códigos), "
            "NO alcanza con separar por tema: partilo en tramos explícitos y contiguos que "
            "cubran todas las filas (por ejemplo 'filas 1 a 200', 'filas 201 a 400', o por "
            "rangos alfabéticos), y decilo así en el alcance."
            if docs_minimos > 1 else ""
        )
    )
    instruccion = types.Part.from_text(text=(
        (
            "Re-planificá SOLO esta parte del material, que resultó demasiado grande para un "
            f"único documento:\nTítulo: {alcance_padre['titulo']}\nAlcance: {alcance_padre['alcance']}\n"
            "Ignorá todo lo que quede fuera de ese alcance: de eso se ocupan otros documentos.\n\n"
            if alcance_padre else
            "Planificá los documentos para el material de abajo."
        )
        + (f"\nTítulo sugerido por el usuario: {titulo.strip()}" if (titulo or "").strip() else "")
        + _contexto_texto(contexto)
    ))
    plan = _generar_json(
        system, [instruccion] + fuentes_parts, _SCHEMA_PLAN,
        temperatura=0.3, user_id=user_id, ref_label="planificar",
    )
    documentos = [
        {"titulo": (d.get("titulo") or f"Documento {i + 1}").strip(), "alcance": (d.get("alcance") or "").strip()}
        for i, d in enumerate(plan.get("documentos") or []) if isinstance(d, dict)
    ]
    if not documentos:
        raise RuntimeError("La IA no pudo planificar la separación del material.")
    return {"documentos": documentos, "motivo_separacion": plan.get("motivo_separacion") or ""}


def _redactar_documento_del_plan(plan: List[Dict[str, Any]], indice: int,
                                 fuentes_parts: List[types.Part], contexto: Optional[Dict[str, Any]],
                                 user_id: Optional[int],
                                 imagenes_disponibles: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Segunda fase: redacta UN documento del plan. Cada llamada emite un solo documento,
    así el material total puede superar de largo el límite de salida de una respuesta."""
    objetivo = plan[indice]
    indice_txt = "\n".join(
        f"{i + 1}. {d['titulo']}: {d['alcance']}" + ("   <-- ESTE es el que tenés que redactar" if i == indice else "")
        for i, d in enumerate(plan)
    )
    system = (
        "Sos un documentalista experto. Estás redactando UNO de los documentos de un plan ya "
        "definido, a partir del material crudo completo.\n\n"
        + _CONVENCION_MARKDOWN
        + "\n\nREGLAS DE ESTA TAREA:\n"
        "- Redactá ÚNICAMENTE el documento indicado, respetando su alcance.\n"
        "- Volcá TODO el contenido del material que caiga dentro de ese alcance, con su nivel de "
        "detalle completo (montos, plazos, requisitos, excepciones, tablas, imágenes).\n"
        "- Lo que corresponde a los otros documentos del plan NO va acá; no lo resumas ni lo "
        "menciones de pasada. La excepción son definiciones o requisitos compartidos que este "
        "documento necesita para entenderse solo: esos sí se repiten.\n"
        "- Devolvé exactamente UN documento en la lista."
    )
    instruccion = types.Part.from_text(text=(
        "=== PLAN COMPLETO DE DOCUMENTOS (para saber qué NO va acá) ===\n" + indice_txt
        + f"\n\n=== DOCUMENTO A REDACTAR AHORA ===\nTítulo: {objetivo['titulo']}\nAlcance: {objetivo['alcance']}"
        + _contexto_texto(contexto)
        + _formatear_bloque_imagenes(imagenes_disponibles)
    ))
    resultado = _generar_json(
        system, [instruccion] + fuentes_parts, _SCHEMA_DOCUMENTOS,
        temperatura=0.4, user_id=user_id, ref_label="redactar",
    )
    documentos = _normalizar_documentos(resultado.get("documentos"), titulo_fallback=objetivo["titulo"])
    if not documentos:
        raise RuntimeError(f"La IA no redactó el documento '{objetivo['titulo']}'.")
    if len(documentos) > 1:
        # Se fue de alcance y devolvió varios: se conserva el que corresponde al plan.
        documentos = [documentos[0]]
    documentos[0]["titulo"] = objetivo["titulo"]
    return documentos[0]


def _integrar_en_documento(previo: Dict[str, Any], que_integrar: str,
                           fuentes_parts: List[types.Part], contexto: Optional[Dict[str, Any]],
                           user_id: Optional[int],
                           imagenes_disponibles: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Segunda fase del merge: devuelve UN documento existente con lo nuevo ya integrado.

    Una llamada por documento (igual que _redactar_documento_del_plan): la respuesta
    lleva un solo markdown y no arrastra el de los demás documentos tocados.
    """
    system = (
        "Sos un documentalista experto. Recibís UN documento ya existente y las fuentes "
        "crudas con información nueva. Devolvé ESE MISMO documento con lo que le "
        "corresponde ya integrado.\n\n"
        + _CONVENCION_MARKDOWN
        + "\n\nREGLAS DE ESTA TAREA:\n"
        "- Preservá el contenido que la información nueva no toca (no resumas ni borres contenido no afectado).\n"
        "- ACTUALIZACIONES / REEMPLAZOS: Si la fuente o indicación señala una actualización o reemplazo (ej: nuevos precios, nuevos montos, cambio de pasos o de procedimientos), SOBRESCRIBÍ y actualizá los valores, pasos o párrafos anteriores con la información nueva.\n"
        "- BAJAS / ELIMINACIÓN DE SECCIONES: Si la fuente o indicación solicita dar de baja o eliminar una sección, procedimiento o título obsoleto, REMOVÉ esa sección del documento.\n"
        "- Integrá SOLO lo que se indica abajo; el resto del material nuevo va a otros "
        "documentos y no debe aparecer acá.\n"
        "- Ubicá cada dato en su sección temática; creá una sección nueva solo si no encaja "
        "en ninguna.\n"
        "- DEDUPLICÁ: si algo ya estaba exactamente igual y no es una actualización, no lo repitas.\n"
        "- Si lo nuevo CONTRADICE lo existente sin ser una actualización o reemplazo explícito, dejá lo existente y reportá el conflicto.\n"
        "- Devolvé el markdown COMPLETO del documento, no un fragmento ni un diff."
    )
    instruccion = types.Part.from_text(text=(
        f"=== DOCUMENTO A ACTUALIZAR: {previo.get('titulo')} ===\n{previo.get('markdown')}\n\n"
        f"=== QUÉ INTEGRARLE (de las fuentes de abajo) ===\n{que_integrar}"
        + _contexto_texto(contexto)
        + _formatear_bloque_imagenes(imagenes_disponibles)
    ))
    try:
        resultado = _generar_json(
            system, [instruccion] + fuentes_parts, _SCHEMA_DOCUMENTOS,
            temperatura=0.4, user_id=user_id, ref_label="integrar",
        )
    except RespuestaTruncada as e:
        raise RuntimeError(
            f"El documento '{previo.get('titulo')}' es demasiado grande para actualizarlo: "
            "la IA no puede devolverlo entero en una sola respuesta. Conviene partirlo en "
            "documentos más chicos antes de agregarle información."
        ) from e
    documentos = _normalizar_documentos(resultado.get("documentos"), titulo_fallback=previo.get("titulo"))
    if not documentos:
        raise RuntimeError(f"La IA no devolvió el documento '{previo.get('titulo')}' actualizado.")
    documento = documentos[0]
    documento["titulo"] = documento.get("titulo") or previo.get("titulo")
    return documento


def _redactar_subdividiendo(objetivo: Dict[str, Any], fuentes_parts: List[types.Part],
                            contexto: Optional[Dict[str, Any]], user_id: Optional[int],
                            partes: int = 4) -> List[Dict[str, Any]]:
    """Rescate para un documento del plan que no entró en una respuesta.

    Re-planifica SOLO su alcance en `partes` pedazos y redacta cada uno. Sin recursión:
    si un pedazo TAMBIÉN se trunca, se corta con un error claro en vez de seguir
    partiendo indefinidamente (a esa altura el material no es documentación, es una base
    de datos y conviene resolverlo por otro lado).
    """
    subplan = _planificar_documentos(fuentes_parts, None, contexto, user_id,
                                     docs_minimos=partes, alcance_padre=objetivo)
    documentos = []
    for i in range(len(subplan["documentos"])):
        try:
            documentos.append(_redactar_documento_del_plan(
                subplan["documentos"], i, fuentes_parts, contexto, user_id))
        except RespuestaTruncada as e:
            raise RuntimeError(
                f"El material de '{objetivo['titulo']}' no entra en un documento ni "
                "partiéndolo: es demasiado denso para el asistente. Subilo en archivos más "
                "chicos (por ejemplo, la planilla partida en varias)."
            ) from e
    return documentos


def _contexto_texto(contexto: Optional[Dict[str, Any]]) -> str:
    contexto = contexto or {}
    datos = {k: contexto.get(k) for k in ("bot_nombre", "empresa", "campana") if contexto.get(k)}
    if not datos:
        return ""
    return "\n\n=== CONTEXTO (para ambientar, no es fuente de datos) ===\n" + json.dumps(datos, ensure_ascii=False)


def _formatear_bloque_imagenes(imagenes: Optional[List[Dict[str, Any]]]) -> str:
    """Formatea la lista de imágenes extraídas del material disponibles en el servidor
    para que la IA las inserte en el markdown en el paso o pantalla correspondiente."""
    if not imagenes:
        return ""
    lineas = [
        "\n\n=== IMÁGENES EXTRAÍDAS DEL MATERIAL DISPONIBLES EN EL SERVIDOR ===",
        "El material traía estas imágenes/capturas de soporte visual que ya fueron extraídas y almacenadas en el servidor. "
        "Cuando un procedimiento, paso o sección explique lo que muestra una de estas imágenes, "
        "INSERTA la imagen correspondiente en el markdown usando el formato exacto: `![descripción de la captura](URL)`. "
        "Usá exclusivamente las URLs que figuran en este listado:\n",
    ]
    for img in imagenes:
        url = img.get("url") or f"/chatbots/imagenes/{img.get('id')}"
        nombre = img.get("nombre") or f"imagen_{img.get('id')}"
        desc = img.get("descripcion") or nombre
        fuente = f" (fuente: {img['fuente_origen']})" if img.get("fuente_origen") else ""
        lineas.append(f"- URL: `{url}` | Archivo: `{nombre}`{fuente} | Contexto: {desc}")
    return "\n" + "\n".join(lineas)


# ---------------------------------------------------------------------------- #
# 1) Formatear documentación nueva desde cero (1..N documentos)                 #
# ---------------------------------------------------------------------------- #
def formatear_documento(
    fuentes: List[Dict[str, Any]],
    titulo: Optional[str] = None,
    contexto: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = None,
    permitir_separacion: bool = True,
    imagenes_disponibles: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Transforma contenido crudo en markdown estructurado para RAG y verifica que no
    se pierda nada. NO guarda nada: devuelve una propuesta para revisión.

    Puede devolver UNO o VARIOS documentos: si el material cubre temas independientes,
    la IA lo separa (ver el criterio en _CONVENCION_MARKDOWN). Separar conviene porque
    cada documento se indexa por separado y acota mejor lo que recupera el chatbot.

    Args:
        fuentes: lista de dicts. Cada uno es
            {"tipo": "texto", "texto": str, "nombre"?: str} o
            {"tipo": "archivo", "datos": bytes, "mime": str, "nombre"?: str}.
        titulo: título sugerido (se respeta si sale un único documento).
        contexto: {bot_nombre, empresa, campana} opcional, solo para ambientar.
        permitir_separacion: si es False, fuerza un único documento.
        imagenes_disponibles: lista de imágenes extraídas del material disponibles en el servidor.

        {documentos: [{titulo, markdown, secciones}], motivo_separacion, notas,
         faltantes_residuales, invenciones_residuales, conflictos,
         rondas_verificacion, verificado_ok}
    """
    fuentes = _validar_fuentes(fuentes)
    client = _get_client()

    if permitir_separacion:
        regla = (
            "Separá el material en VARIOS documentos si corresponde según el criterio de "
            "separación, o dejalo en uno solo si es un tema único."
        )
    else:
        regla = "Devolvé UN SOLO documento con todo el material (no separes)."

    system = (
        "Sos un documentalista experto. Convertís material crudo y desordenado (texto "
        "pegado, PDFs, planillas, imágenes) en documentos markdown limpios y bien "
        "estructurados que van a alimentar el buscador de un chatbot de soporte para "
        "operadores de un contact center.\n\n"
        + _CONVENCION_MARKDOWN
        + (f"\n\nTítulo sugerido por el usuario (usalo si el resultado es un único documento): {titulo.strip()}"
           if (titulo or "").strip() else "")
    )
    bloque_imgs = _formatear_bloque_imagenes(imagenes_disponibles)
    instruccion = types.Part.from_text(text=(
        f"para RAG, siguiendo la convención. {regla} No pierdas información y no inventes "
        "nada." + _contexto_texto(contexto) + bloque_imgs
    ))

    # se planifica primero y se redacta un documento por llamada.
    # Una sola vez: con un PDF implica leerlo entero (unos segundos en uno de 200 páginas).
    tamano = _tamano_estimado(fuentes)
    usar_plan = permitir_separacion and tamano > _max_chars_una_pasada()

    def _por_el_plan():
        docs_minimos = max(1, -(-tamano // MAX_CHARS_POR_DOCUMENTO))
        plan = _planificar_documentos(fuentes_parts, titulo, contexto, user_id,
                                      docs_minimos=docs_minimos)
        logger.info("Asistente de docs: material grande (mínimo %d docs), plan de %d documento(s).",
                    docs_minimos, len(plan["documentos"]))
        docs = []
        for i in range(len(plan["documentos"])):
            try:
                docs.append(_redactar_documento_del_plan(
                    plan["documentos"], i, fuentes_parts, contexto, user_id,
                    imagenes_disponibles=imagenes_disponibles))
            except RespuestaTruncada:
                # Ese pedazo del plan seguía siendo demasiado grande: se re-planifica
                # SOLO ese alcance y se redacta en partes.
                logger.warning("Asistente de docs: '%s' no entró en una respuesta; se subdivide.",
                               plan["documentos"][i]["titulo"])
                docs.extend(_redactar_subdividiendo(
                    plan["documentos"][i], fuentes_parts, contexto, user_id))
        return docs, {"motivo_separacion": plan["motivo_separacion"], "notas": []}

    with _fuentes_activas(client, fuentes) as fuentes_parts:
        if usar_plan:
            documentos, base = _por_el_plan()
        else:
            try:
                base = _generar_json(
                    system, [instruccion] + fuentes_parts, _SCHEMA_DOCUMENTOS,
                    temperatura=0.4, user_id=user_id, ref_label="formatear",
                )
                documentos = _normalizar_documentos(base.get("documentos"), titulo_fallback=titulo)
            except RespuestaTruncada:
                # El material resultó más grande de lo que estimamos (_tamano_estimado
                # se queda corto para formatos que no anticipamos). En vez de fallar,
                # se rehace por el camino del plan, que no tiene ese techo.
                if not permitir_separacion:
                    raise RuntimeError(
                        "El material es demasiado grande para un único documento: la IA no "
                        "puede devolverlo entero en una sola respuesta. Permití que se separe "
                        "en varios documentos, o subilo en partes."
                    ) from None
                logger.warning("Asistente de docs: la pasada única se truncó; se rehace con plan.")
                documentos, base = _por_el_plan()
        if not documentos:
            raise RuntimeError("La IA no generó ningún documento a partir de las fuentes.")
        if not permitir_separacion and len(documentos) > 1:
            # Salvaguarda: si igual separó, se unifica respetando los títulos como H1.
            unido = "\n\n".join(f"# {d['titulo']}\n\n{d['markdown']}" for d in documentos)
            documentos = [{"titulo": (titulo or documentos[0]["titulo"]), "markdown": unido, "secciones": []}]

        resultado_loop = _loop_verificacion(documentos, fuentes_parts, user_id)

    return {
        "documentos": resultado_loop["documentos"],
        "motivo_separacion": base.get("motivo_separacion") or "",
        "notas": base.get("notas") or [],
        "faltantes_residuales": resultado_loop["faltantes_residuales"],
        "invenciones_residuales": resultado_loop["invenciones_residuales"],
        "conflictos": resultado_loop["conflictos"],
        "rondas_verificacion": resultado_loop["rondas_verificacion"],
        "verificado_ok": resultado_loop["verificado_ok"],
    }


# ---------------------------------------------------------------------------- #
# 2) Agregar información a la documentación existente (rutea a 1..N documentos) #
# ---------------------------------------------------------------------------- #
def agregar_informacion(
    documentos_actuales: List[Dict[str, Any]],
    fuentes_nuevas: List[Dict[str, Any]],
    contexto: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = None,
    permitir_crear: bool = True,
    imagenes_disponibles: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Integra contenido crudo NUEVO en una documentación existente de VARIOS documentos.
    NO guarda nada: devuelve una propuesta para revisión.
    La IA decide a qué documento va cada bloque de información nueva — puede tocar
    VARIOS a la vez — y, si nada encaja, crear uno nuevo (si `permitir_crear`).
    Deduplica y marca conflictos en lugar de decidir por su cuenta.

    Args:
        documentos_actuales: [{doc_id, titulo, markdown}] de los documentos candidatos.
        fuentes_nuevas: mismas formas que en formatear_documento().
        imagenes_disponibles: lista de imágenes extraídas del material disponibles en el servidor.

    Returns:
        {documentos: [{doc_id, titulo, markdown, accion, cambios}], ruteo, notas,
         rondas_verificacion, verificado_ok}

    Solo se devuelven los documentos MODIFICADOS o CREADOS: los que no cambian no
    viajan (y por lo tanto la verificación tampoco los reprocesa).
    """
    actuales = [
        {"doc_id": d.get("doc_id"), "titulo": d.get("titulo") or "(sin título)",
         "markdown": (d.get("markdown") or "").strip()}
        for d in (documentos_actuales or []) if (d.get("markdown") or "").strip()
    ]
    if not actuales:
        # Sin documentación previa, es exactamente un formateo desde cero.
        return formatear_documento(fuentes_nuevas, contexto=contexto, user_id=user_id,
                                   imagenes_disponibles=imagenes_disponibles)
    fuentes_nuevas = _validar_fuentes(fuentes_nuevas)
    client = _get_client()
    regla_crear = (
        "- Si un bloque de información nueva no encaja temáticamente en NINGUNO de los "
        "documentos existentes, agregalo a 'nuevos' con un título claro y su alcance.\n"
        if permitir_crear else
        "- NO propongas documentos nuevos ('nuevos' vacío): ubicá toda la información en los "
        "documentos existentes.\n"
    )
    system = (
        "Sos un documentalista experto. Recibís la DOCUMENTACIÓN existente de un chatbot "
        "(uno o varios documentos markdown, cada uno con su doc_id) y FUENTES crudas con "
        "información NUEVA. Tu tarea es RUTEAR, no redactar: decidir qué información nueva "
        "va a qué documento. El texto de cada documento se redacta después, aparte.\n"
        "- Para CADA bloque de información nueva, elegí el documento cuyo tema le corresponde. "
        "Distintos bloques pueden ir a DISTINTOS documentos: está bien y es lo esperado "
        "tocar VARIOS documentos en una misma pasada.\n"
        + regla_crear +
        "- En 'que_integrar' describí con precisión QUÉ información va a ese documento y en qué "
        "sección, con el detalle suficiente para que otro la integre sin volver a decidir.\n"
        "- Listá SOLO los documentos que hay que tocar: los que no cambian no se incluyen.\n"
        "- DEDUPLICÁ: si algo ya estaba en la documentación y no es una actualización ni cambio, no lo mandes a integrar de nuevo.\n"
        "- ACTUALIZACIONES / REEMPLAZOS / BAJAS: Si una fuente o indicación señala una ACTUALIZACIÓN / REEMPLAZO "
        "(ej: nuevos precios, nuevo procedimiento, cambio de pasos, o solicitud de dar de baja/eliminar una sección obsoleta), "
        "RUTÉALO al documento correspondiente describiendo en 'que_integrar' los datos que deben sobrescribirse, reemplazarse o eliminarse.\n"
        "- Si la información nueva CONTRADICE a la existente pero NO es una actualización explícita, NO elijas por tu cuenta: reportá "
        "el conflicto en 'conflictos' para que un humano decida.\n"
        "- NO devuelvas markdown de los documentos: solo el ruteo.\n"
        "- Explicá en 'ruteo' a qué documento fue cada bloque y por qué.\n\n"
        + _CONVENCION_MARKDOWN
    )
    bloque_imgs = _formatear_bloque_imagenes(imagenes_disponibles)
    instruccion = types.Part.from_text(text=(
        "Decidí a qué documento va cada bloque de información nueva de las fuentes."
        + _contexto_texto(contexto)
        + bloque_imgs
    ))
    partes_previo = [types.Part.from_text(
        text=f"=== DOCUMENTACIÓN EXISTENTE (preservar todo) ===\n{_serializar_docs(actuales)}"
    )]
    previos_por_id = {d["doc_id"]: d for d in actuales if d.get("doc_id")}

    with _fuentes_activas(client, fuentes_nuevas) as fuentes_parts:
        # FASE 1 — Rutear: decidir a qué documento va cada bloque, SIN redactar. La
        # respuesta es corta (no lleva markdown), así que no se trunca. Antes esta
        # única llamada tenía que devolver el markdown COMPLETO de todos los documentos
        # tocados y se cortaba a la mitad en cuanto eran varios o grandes.
        base = _generar_json(
            system, [instruccion] + partes_previo + fuentes_parts, _SCHEMA_RUTEO_MERGE,
            temperatura=0.4, user_id=user_id, ref_label="merge",
        )
        asignaciones = [
            a for a in (base.get("asignaciones") or [])
            if isinstance(a, dict) and a.get("doc_id") in previos_por_id
        ]
        nuevos = [
            n for n in (base.get("nuevos") or [])
            if isinstance(n, dict) and (n.get("titulo") or "").strip()
        ] if permitir_crear else []
        if not asignaciones and not nuevos:
            return {
                "documentos": [],
                "ruteo": base.get("ruteo") or [
                    "La información proporcionada ya se encuentra totalmente integrada en la documentación existente o no requirió modificaciones."
                ],
                "notas": base.get("notas") or ["No se detectaron modificaciones ni contenido nuevo para incorporar."],
                "faltantes_residuales": [],
                "invenciones_residuales": [],
                "conflictos": base.get("conflictos") or [],
                "rondas_verificacion": 0,
                "verificado_ok": True,
            }

        # FASE 2 — Redactar: un documento por llamada, con su markdown completo.
        afectados = []
        for asignacion in asignaciones:
            previo = previos_por_id[asignacion["doc_id"]]
            doc = _integrar_en_documento(previo, asignacion.get("que_integrar") or "",
                                         fuentes_parts, contexto, user_id,
                                         imagenes_disponibles=imagenes_disponibles)
            doc["doc_id"], doc["accion"] = previo["doc_id"], "actualizar"
            afectados.append(doc)

        for nuevo in nuevos:
            plan = [{"titulo": nuevo["titulo"].strip(), "alcance": (nuevo.get("alcance") or "").strip()}]
            try:
                doc = _redactar_documento_del_plan(plan, 0, fuentes_parts, contexto, user_id,
                                                   imagenes_disponibles=imagenes_disponibles)
            except RespuestaTruncada:
                docs_partidos = _redactar_subdividiendo(plan[0], fuentes_parts, contexto, user_id)
                for parcial in docs_partidos:
                    parcial["doc_id"], parcial["accion"] = None, "crear"
                afectados.extend(docs_partidos)
                continue
            doc["doc_id"], doc["accion"] = None, "crear"
            afectados.append(doc)

        # Verificación acotada a lo que se tocó: se preserva lo que traían las versiones
        # previas de esos documentos y se integró lo nuevo. Los documentos intactos no
        # entran (no se reprocesan ni se re-verifican: no cambiaron).
        previos_afectados = [previos_por_id[d["doc_id"]] for d in afectados if d.get("doc_id") in previos_por_id]
        fuentes_verif = ([types.Part.from_text(
            text=f"=== VERSIÓN PREVIA DE LOS DOCUMENTOS MODIFICADOS (preservar todo) ===\n{_serializar_docs(previos_afectados)}"
        )] if previos_afectados else []) + fuentes_parts
        resultado_loop = _loop_verificacion(afectados, fuentes_verif, user_id, con_docs_previos=True)

    return {
        "documentos": resultado_loop["documentos"],
        "ruteo": base.get("ruteo") or [],
        "notas": base.get("notas") or [],
        "faltantes_residuales": resultado_loop["faltantes_residuales"],
        "invenciones_residuales": resultado_loop["invenciones_residuales"],
        # Los conflictos de merge (nuevo vs viejo) son los más útiles de mostrar.
        "conflictos": (base.get("conflictos") or []) + resultado_loop["conflictos"],
        "rondas_verificacion": resultado_loop["rondas_verificacion"],
        "verificado_ok": resultado_loop["verificado_ok"],
    }


# ---------------------------------------------------------------------------- #
# 3) Detectar y proponer TABLAS DE DATOS                                        #
# ---------------------------------------------------------------------------- #
# Hay conocimiento que no es prosa: es entidad -> atributos, y la pregunta del
# operador es un lookup o un filtro. Las bases de instalación de vantix y la
# cartera de cobranzas de benefix son eso, y por el camino RAG salen mal y caras
# (ver el módulo app/chatbot_tablas.py, que documenta la medición).
#
# LA REGLA: el modelo define el ESQUEMA, un parser determinístico mueve los DATOS.
# La IA nombra la tabla, elige qué columnas identifican una fila y con qué
# palabras se la reconoce; las filas se parsean del markdown. Con 2.500 filas, un
# modelo que las transcribe se saltea algunas y no hay forma de notarlo hasta que
# un operador pregunta justo por la que faltaba.
#
# Corolario práctico: el modelo NUNCA ve la tabla completa, solo los encabezados y
# una muestra, así que analizar un documento de 43k caracteres cuesta una llamada
# chica.

# Cuánto puede achicarse una tabla respecto de las entidades detectadas en el
# material sin que se considere una pérdida. No es 1.0 porque el conteo por
# etiqueta es aproximado: un bloque puede repetir una etiqueta dos veces, o una
# entidad puede no tenerla. Por debajo de esto sí hay algo para mirar.
UMBRAL_ENTIDADES = 0.9

_SCHEMA_TABLA = types.Schema(
    type=types.Type.OBJECT,
    required=["es_tabla", "motivo"],
    properties={
        "es_tabla": types.Schema(
            type=types.Type.BOOLEAN,
            description=(
                "true SOLO si el documento es principalmente un LISTADO de entidades con "
                "los mismos atributos, que se consulta buscando una entidad concreta "
                "(clientes y su gestor, sucursales y su dirección, códigos y su "
                "significado). false para procedimientos, instructivos, FAQ o normativa, "
                "aunque incluyan alguna tabla suelta."
            ),
        ),
        "motivo": types.Schema(
            type=types.Type.STRING,
            description="En una frase, por qué es (o no es) una tabla de datos consultable.",
        ),
        "nombre": types.Schema(
            type=types.Type.STRING,
            description="Nombre corto de la tabla, como lo vería el equipo de calidad.",
        ),
        "descripcion": types.Schema(
            type=types.Type.STRING,
            description=(
                "Una o dos frases: qué se puede averiguar con esta tabla y con qué dato se "
                "la consulta. La lee el chatbot para decidir si una pregunta va a esta "
                "tabla, así que tiene que nombrar las cosas como las nombra el operador."
            ),
        ),
        "terminos": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(type=types.Type.STRING),
            description=(
                "Palabras que, si aparecen en la pregunta de un operador, indican que la "
                "respuesta está en esta tabla ('sucursal', 'taller', 'gestor de "
                "cobranzas'). Incluí los sinónimos que usa el operador aunque no estén en "
                "el documento. NO incluyas verbos genéricos del negocio ('instalar', "
                "'cobrar', 'gestionar') ni palabras que también aparecen en los "
                "procedimientos: mandarían a esta tabla preguntas que no son de datos."
            ),
        ),
        "columnas": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                required=["nombre"],
                properties={
                    "nombre": types.Schema(
                        type=types.Type.STRING,
                        description="Nombre EXACTO de la columna, tal cual el encabezado de la tabla markdown.",
                    ),
                    "descripcion": types.Schema(
                        type=types.Type.STRING,
                        description="Qué contiene, si el nombre no se explica solo. Vacío si es obvio.",
                    ),
                    "clave": types.Schema(
                        type=types.Type.BOOLEAN,
                        description=(
                            "true si un operador podría nombrar una fila POR ESTE VALOR "
                            "(razón social, número de cliente, nombre de la sucursal). "
                            "Puede haber varias. false para los atributos que se "
                            "responden (dirección, teléfono, gestor asignado)."
                        ),
                    ),
                },
            ),
        ),
        "nota": types.Schema(
            type=types.Type.STRING,
            description=(
                "Regla de USO que el chatbot tiene que respetar siempre al responder con "
                "esta tabla, si el documento la trae (ej.: 'no informar estos teléfonos de "
                "forma proactiva'). Vacío si el documento no impone ninguna."
            ),
        ),
        "mapeo_grupos": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                required=["grupo", "columnas"],
                properties={
                    "grupo": types.Schema(
                        type=types.Type.INTEGER,
                        description="Número del grupo, tal como te lo presenté (1, 2, 3...).",
                    ),
                    "columnas": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(type=types.Type.STRING),
                        description=(
                            "Para CADA columna de ese grupo, EN ORDEN, el nombre de la columna "
                            "final que le corresponde (de la lista `columnas`). Tiene que haber "
                            "exactamente un nombre por columna del grupo. Usá cadena vacía para "
                            "una columna del grupo que haya que ignorar. Es lo que permite unir "
                            "en una sola tabla listados que traen las mismas columnas con otro "
                            "nombre u otro orden."
                        ),
                    ),
                },
            ),
            description=(
                "OBLIGATORIO cuando te presenté más de un grupo, o cuando un grupo vino sin "
                "encabezado utilizable: sin esto no se pueden usar sus filas."
            ),
        ),
        "tabla_markdown": types.Schema(
            type=types.Type.STRING,
            description=(
                "SOLO si el documento NO trae ya una tabla markdown y hay que armarla a "
                "partir del texto: la tabla markdown completa, con TODAS las entidades del "
                "material, una fila por entidad. Si el documento ya tiene la tabla, dejá "
                "este campo VACÍO: las filas se leen de ahí y no hay que reescribirlas."
            ),
        ),
        "resto_markdown": types.Schema(
            type=types.Type.STRING,
            description=(
                "Las partes del documento que NO son la tabla y que hay que conservar como "
                "documentación normal (introducciones, políticas, procedimientos). Vacío si "
                "el documento entero es la tabla."
            ),
        ),
    },
)


def _instruccion_tabla(titulo: str, markdown: str, grupos: List[Dict[str, Any]]) -> str:
    """El prompt del análisis: encabezados + muestra, nunca la tabla entera."""
    if grupos:
        partes = [
            "El material YA trae tabla(s) markdown. Estos son los grupos de columnas que "
            "encontré y una muestra de filas de cada uno (el total de filas es mucho mayor; "
            "no las necesitás para esta tarea):"
        ]
        for i, g in enumerate(grupos, 1):
            muestra = chatbot_tablas_admin.muestra_para_la_ia(g["filas_crudas"])
            if g.get("columnas"):
                cabecera = f"Columnas: {', '.join(g['columnas'])}"
            else:
                # Pasa de verdad: los documentos 81/82 de benefix tienen el
                # encabezado truncado sobre filas de 6 celdas. Las filas son
                # perfectamente utilizables si alguien nombra las columnas.
                cabecera = (
                    f"SIN ENCABEZADO UTILIZABLE — son {g['ancho']} columnas y hay que "
                    "deducir qué es cada una mirando los valores."
                )
            partes.append(
                f"\n--- Grupo {i} ({len(g['filas_crudas'])} filas) ---\n{cabecera}\n"
                + "\n".join(" | ".join(celdas) for celdas in muestra)
            )
        partes.append(
            "\nNO reescribas las filas: dejá `tabla_markdown` vacío. Lo que necesito es el "
            "esquema (nombre, descripción, términos, qué columnas son clave) y, en "
            "`mapeo_grupos`, a qué columna final corresponde cada columna de cada grupo.\n"
            "IMPORTANTE: si los grupos son el MISMO listado con las columnas renombradas, "
            "reordenadas o sin encabezado (por ejemplo, una cartera de clientes repartida en "
            "varios documentos, uno por responsable), tienen que terminar en UNA SOLA tabla: "
            "definí un único juego de `columnas` y mapeá todos los grupos contra él. Un valor "
            "que distingue a un grupo de otro (el responsable, la sucursal, el período) es una "
            "COLUMNA de esa tabla, no una tabla aparte."
        )
        contexto_txt = _texto_sin_tablas(markdown)
        if contexto_txt.strip():
            partes.append(
                "\n=== TEXTO QUE NO ES TABLA (puede traer la regla de uso) ===\n"
                + contexto_txt[:8000]
            )
    else:
        partes = [
            "El material NO trae ninguna tabla markdown. Si es un listado de entidades con "
            "los mismos atributos, armá vos la tabla en `tabla_markdown`, con UNA FILA POR "
            "ENTIDAD y todas las entidades del material — no omitas ninguna. Si el material "
            "distingue subgrupos (por ejemplo bases propias y de terceros), no hagas dos "
            "tablas: agregá una columna que los distinga.\n\n"
            "=== MATERIAL ===\n" + markdown[:60000]
        ]
    return f"Título: {titulo}\n\n" + "\n".join(partes)


def _texto_sin_tablas(markdown: str) -> str:
    """El documento sin sus filas de tabla. Se le pasa al modelo para que encuentre
    la regla de uso, que casi nunca está adentro de la tabla."""
    return "\n".join(
        linea for linea in (markdown or "").splitlines()
        if not linea.lstrip().startswith("|")
    )


def analizar_tabla(
    documentos: List[Dict[str, str]],
    contexto: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """¿Este material es en realidad una tabla de datos? Devuelve la propuesta.

    Recibe UNO o VARIOS documentos ({titulo, markdown, doc_id?}) y devuelve UNA
    sola tabla. Que acepte varios no es un detalle: la cartera de cobranzas de
    benefix está repartida en 10 documentos, uno por gestor, y tiene que terminar
    en una única tabla — el gestor es un VALOR de columna, no una tabla aparte.
    Con una tabla por gestor se pierden las consultas cruzadas ("¿qué gestores
    hay?", "¿cuántos clientes tiene Ferrer?"), el ruteo se degrada (diez
    descripciones casi idénticas) y un cliente que cambia de gestor obliga a
    tocar dos tablas.

    NO guarda nada. Las filas salen de un parser, no del modelo (ver la regla del
    módulo chatbot_tablas_admin); el modelo aporta el esquema, la unificación de
    los esquemas distintos y la regla de uso.

    Returns:
        {es_tabla, motivo, nombre, descripcion, terminos, columnas, nota,
         filas, filas_total, duplicadas, sospechosas, resto_markdown, notas}
    """
    documentos = [
        {"titulo": (d.get("titulo") or "").strip(),
         "markdown": (d.get("markdown") or "").strip(),
         "doc_id": d.get("doc_id")}
        for d in (documentos or []) if (d.get("markdown") or "").strip()
    ]
    if not documentos:
        return {"es_tabla": False, "motivo": "No hay material para analizar."}

    # Todo el material junto: los grupos de columnas se calculan sobre el conjunto,
    # que es lo que permite ver que diez documentos son la misma tabla.
    markdown = "\n\n".join(
        (f"# {d['titulo']}\n\n{d['markdown']}" if d["titulo"] else d["markdown"])
        for d in documentos
    )
    titulo = (documentos[0]["titulo"] if len(documentos) == 1
              else f"{len(documentos)} documentos: " +
                   ", ".join(d["titulo"] for d in documentos if d["titulo"])[:300])

    grupos = chatbot_tablas_admin.agrupar_tablas(chatbot_tablas_admin.extraer_tablas(markdown))
    system = (
        "Sos un documentalista experto. Tenés que decidir si un material del conocimiento "
        "de un chatbot es en realidad una TABLA DE DATOS —un listado de entidades con los "
        "mismos atributos, que se consulta buscando una entidad concreta— y, si lo es, "
        "definir su esquema.\n\n"
        "POR QUÉ IMPORTA: las tablas de datos no se buscan por similitud de texto sino por "
        "el valor exacto de sus columnas clave. Marcar como tabla un procedimiento haría "
        "que el chatbot lo responda con un listado en vez de explicar el circuito; no "
        "marcar una tabla real deja al operador sin poder encontrar una fila entre miles.\n\n"
        "CRITERIO: es tabla si un operador la consulta preguntando POR UNA ENTIDAD "
        "('¿quién gestiona a tal cliente?', '¿dónde queda tal sucursal?'). No es tabla si "
        "la consulta es sobre CÓMO se hace algo, aunque el material incluya cuadros."
        + _contexto_texto(contexto)
    )
    instruccion = types.Part.from_text(text=_instruccion_tabla(titulo, markdown, grupos))

    resultado = _generar_json(
        system, [instruccion], _SCHEMA_TABLA,
        temperatura=0.2, user_id=user_id, ref_label="tabla",
    )

    if not resultado.get("es_tabla"):
        return {
            "es_tabla": False,
            "motivo": resultado.get("motivo") or "No es un listado de entidades consultable.",
        }

    columnas_ia = [c for c in (resultado.get("columnas") or []) if (c.get("nombre") or "").strip()]
    if not columnas_ia:
        return {"es_tabla": False,
                "motivo": "La IA no pudo definir las columnas de la tabla."}
    canonicas = [c["nombre"].strip() for c in columnas_ia]

    # --- Las filas: del parser, nunca del modelo -----------------------------
    notas: List[str] = []
    if grupos:
        mapeos = _mapeos_por_grupo(resultado.get("mapeo_grupos"), grupos, canonicas)
        unificado = chatbot_tablas_admin.unificar_grupos(grupos, canonicas, mapeos)
        filas = unificado["filas"]
        if unificado["grupos_descartados"]:
            notas.append(
                "No se pudieron usar las filas de " +
                ", ".join(unificado["grupos_descartados"]) +
                ": la IA no dijo a qué columna corresponde cada una. Revisá esos documentos "
                "(suele ser un encabezado roto en el markdown de origen)."
            )
        if unificado["descartadas"]:
            notas.append(
                f"{unificado['descartadas']} fila(s) del material tenían una cantidad de "
                "celdas distinta al resto y se descartaron: están rotas en el documento de origen."
            )
        if len(grupos) > 1:
            notas.append(
                f"El material traía {len(grupos)} listados con columnas distintas; se "
                "unificaron en una sola tabla."
            )
    else:
        # Camino de PROSA: el material no traía ninguna grilla, así que las filas
        # las tuvo que escribir el modelo. Es el único caso en el que se le pide
        # que transcriba datos, y por eso el único que puede perder entidades.
        tabla_md = resultado.get("tabla_markdown") or ""
        parseadas = chatbot_tablas_admin.agrupar_tablas(
            chatbot_tablas_admin.extraer_tablas(tabla_md)
        )
        if not parseadas or not parseadas[0].get("columnas"):
            return {
                "es_tabla": False,
                "motivo": ("La IA reconoció un listado pero no pudo armar la tabla. "
                           "Probá cargando el material como planilla."),
            }
        canonicas = parseadas[0]["columnas"]
        filas = parseadas[0]["filas"]

        # Guarda contra OMISIONES, que es lo que la verificación de invenciones no
        # ve. Pasó de verdad: las bases de vantix estaban en prosa, el modelo
        # reescribió el listado y se comió las bases terceras — 28 bloques en el
        # material contra 17 filas en la tabla, sin que nada avisara. Contar una
        # etiqueta repetida ('Dirección:', 'Entrecalles:') es determinístico y
        # gratis, y alcanza para detectar el faltante aunque no diga cuál falta.
        esperadas, etiqueta = chatbot_tablas_admin.entidades_estimadas(markdown)
        if esperadas and len(filas) < esperadas * UMBRAL_ENTIDADES:
            notas.append(
                f"⚠ El material parece tener unas {esperadas} entidades (contando las veces "
                f"que se repite «{etiqueta}») y la tabla quedó con {len(filas)} filas. "
                "Revisá qué faltó ANTES de guardar: es un listado escrito en prosa, así que "
                "las filas las reescribió la IA y puede haberse salteado un bloque entero."
            )

    if not filas:
        return {"es_tabla": False,
                "motivo": "No se pudo recuperar ninguna fila del material. " +
                          (notas[0] if notas else "")}

    # El esquema del modelo manda para el orden, la descripción y las claves; los
    # NOMBRES de columna que existen en las filas son los canónicos, así que se
    # respetan tal cual: una columna renombrada quedaría vacía para siempre.
    propuestas = {_norm_col(c["nombre"]): c for c in columnas_ia}
    columnas = []
    for nombre in canonicas:
        propuesta = propuestas.get(_norm_col(nombre), {})
        columnas.append({
            "nombre": nombre,
            "descripcion": str(propuesta.get("descripcion") or "").strip(),
            "clave": bool(propuesta.get("clave")),
        })
    if not any(c["clave"] for c in columnas):
        # Sin clave no se puede buscar nada: se toma la primera columna, que en un
        # listado es casi siempre el identificador, y se avisa.
        columnas[0]["clave"] = True
        notas.append(
            f"La IA no marcó ninguna columna como identificadora; se usó «{columnas[0]['nombre']}». "
            "Si el operador nombra las filas de otra forma, cambialo antes de guardar."
        )

    claves = [c["nombre"] for c in columnas if c["clave"]]
    filas, duplicadas = chatbot_tablas_admin.deduplicar(filas, claves)
    if duplicadas:
        notas.append(
            f"Se descartaron {duplicadas} filas repetidas (la misma entidad estaba cargada "
            "más de una vez, normalmente por venir la tabla volcada en dos ordenamientos)."
        )

    sospechosas = chatbot_tablas_admin.filas_no_verificadas(filas, claves, markdown)
    if sospechosas:
        notas.append(
            f"{len(sospechosas)} fila(s) tienen una clave que NO aparece en el material de "
            "origen. Revisalas: pueden ser un error de transcripción."
        )

    return {
        "es_tabla": True,
        "motivo": resultado.get("motivo") or "",
        "nombre": (resultado.get("nombre") or titulo or "Tabla de datos").strip(),
        "descripcion": (resultado.get("descripcion") or "").strip(),
        "terminos": [t.strip() for t in (resultado.get("terminos") or []) if t and t.strip()],
        "columnas": columnas,
        "nota": (resultado.get("nota") or "").strip(),
        "filas": filas,
        "filas_total": len(filas),
        "duplicadas": duplicadas,
        "sospechosas": sospechosas,
        # Lo que no es tabla sigue siendo documentación: si el material traía una
        # introducción o un procedimiento, no se pierde por convertir el listado.
        "resto_markdown": (resultado.get("resto_markdown") or "").strip(),
        "notas": notas,
    }


def _mapeos_por_grupo(crudo: Any, grupos: List[Dict[str, Any]],
                      canonicas: List[str]) -> Dict[int, List[str]]:
    """Normaliza el `mapeo_grupos` de la IA a {índice de grupo: [columna canónica]}.

    Con un solo grupo que ya tiene encabezado propio no hace falta que la IA diga
    nada: sus columnas son las canónicas. El mapeo explícito es imprescindible
    recién cuando hay varios esquemas o cuando el encabezado del origen está roto.
    """
    mapeos: Dict[int, List[str]] = {}
    por_canonica = {_norm_col(c): c for c in canonicas}

    for item in (crudo or []):
        if not isinstance(item, dict):
            continue
        try:
            indice = int(item.get("grupo", 0)) - 1
        except (TypeError, ValueError):
            continue
        if not (0 <= indice < len(grupos)):
            continue
        # Se resuelve cada nombre contra las canónicas reales: la IA suele
        # devolverlos con otra capitalización o sin el acento.
        mapeos[indice] = [
            por_canonica.get(_norm_col(n), "") if (n or "").strip() else ""
            for n in (item.get("columnas") or [])
        ]

    for i, grupo in enumerate(grupos):
        if i in mapeos or not grupo.get("columnas"):
            continue
        mapeos[i] = [por_canonica.get(_norm_col(n), "") for n in grupo["columnas"]]
    return mapeos


def _norm_col(nombre: str) -> str:
    """Nombres de columna comparables (el modelo suele devolverlos sin el énfasis
    markdown o con otra capitalización)."""
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", str(nombre))
                  .encode("ascii", "ignore").decode("ascii").lower())


# ---------------------------------------------------------------------------- #
# 4) Mapear el material entrante contra una tabla que YA existe                 #
# ---------------------------------------------------------------------------- #
# Cuando Cobranzas manda la cartera nueva, el archivo no tiene por qué traer las
# columnas con el mismo nombre ni en el mismo orden que la tabla cargada (los 10
# documentos originales ya no coincidían entre sí). Decidir qué columna del
# archivo es cuál es una decisión de esquema, así que la toma el modelo; los
# datos los mueve el parser, como siempre.
#
# Es una llamada chica: ve los nombres de las columnas y una muestra de filas,
# nunca el archivo entero.

_SCHEMA_MAPEO = types.Schema(
    type=types.Type.OBJECT,
    required=["mapeo_grupos"],
    properties={
        "mapeo_grupos": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                required=["grupo", "columnas"],
                properties={
                    "grupo": types.Schema(type=types.Type.INTEGER),
                    "columnas": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(type=types.Type.STRING),
                        description=(
                            "Para CADA columna del grupo, EN ORDEN, el nombre de la columna "
                            "de la tabla destino que le corresponde. Exactamente un nombre "
                            "por columna del grupo. Cadena vacía para una columna del "
                            "material que no vaya a la tabla."
                        ),
                    ),
                },
            ),
        ),
        "advertencias": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(type=types.Type.STRING),
            description=(
                "Lo que quien carga tiene que revisar: columnas de la tabla que el material "
                "no trae, columnas del material que se ignoran, o cualquier indicio de que "
                "este archivo no es de esta tabla. Vacío si está todo claro."
            ),
        ),
    },
)


def mapear_material_a_tabla(
    tabla: Dict[str, Any],
    grupos: List[Dict[str, Any]],
    contexto: Optional[Dict[str, Any]] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Dice a qué columna de una tabla existente corresponde cada columna del
    material entrante.

    Args:
        tabla: {"nombre", "descripcion", "columnas": [{nombre, clave}]}
        grupos: los listados parseados del material (chatbot_tablas_admin).

    Returns:
        {"mapeos": {indice_grupo: [columna_destino, ...]}, "advertencias": [...]}
    """
    if not grupos:
        return {"mapeos": {}, "advertencias": []}

    destino = [c["nombre"] for c in tabla.get("columnas") or []]
    claves = [c["nombre"] for c in (tabla.get("columnas") or []) if c.get("clave")]

    partes = [
        f"TABLA DESTINO: {tabla.get('nombre')}",
        f"Para qué sirve: {tabla.get('descripcion') or '(sin descripción)'}",
        f"Columnas de la tabla: {', '.join(destino)}",
        f"Columnas por las que se identifica una fila: {', '.join(claves) or '(ninguna)'}",
        "",
        "MATERIAL NUEVO. Estos son los listados que traía y una muestra de filas:",
    ]
    for i, g in enumerate(grupos, 1):
        muestra = chatbot_tablas_admin.muestra_para_la_ia(g["filas_crudas"])
        cabecera = (f"Columnas: {', '.join(g['columnas'])}" if g.get("columnas")
                    else f"SIN ENCABEZADO UTILIZABLE — {g['ancho']} columnas, deducilas de los valores.")
        partes.append(
            f"\n--- Grupo {i} ({len(g['filas_crudas'])} filas) ---\n{cabecera}\n"
            + "\n".join(" | ".join(celdas) for celdas in muestra)
        )

    system = (
        "Sos un documentalista experto. Tenés una tabla de datos ya cargada y llega material "
        "nuevo para actualizarla. Tu única tarea es decir a qué columna de la tabla destino "
        "corresponde cada columna del material.\n\n"
        "REGLAS:\n"
        "- Guiate por los VALORES y no solo por el nombre del encabezado: el material suele "
        "traer las columnas con otro nombre, en otro orden, o sin encabezado.\n"
        "- Si una columna del material no tiene lugar en la tabla destino, mapeala a cadena "
        "vacía. NO inventes columnas nuevas: la tabla ya tiene su esquema.\n"
        "- Si el material claramente no corresponde a esta tabla (los valores no se parecen "
        "en nada a lo que la tabla guarda), decilo en `advertencias` y mapeá todo vacío. Es "
        "preferible que no entre nada a que entren miles de filas mal."
        + _contexto_texto(contexto)
    )

    resultado = _generar_json(
        system, [types.Part.from_text(text="\n".join(partes))], _SCHEMA_MAPEO,
        temperatura=0.1, user_id=user_id, ref_label="tabla-mapeo",
    )

    por_destino = {_norm_col(c): c for c in destino}
    mapeos: Dict[int, List[str]] = {}
    for item in (resultado.get("mapeo_grupos") or []):
        if not isinstance(item, dict):
            continue
        try:
            indice = int(item.get("grupo", 0)) - 1
        except (TypeError, ValueError):
            continue
        if not (0 <= indice < len(grupos)):
            continue
        mapeos[indice] = [
            por_destino.get(_norm_col(n), "") if (n or "").strip() else ""
            for n in (item.get("columnas") or [])
        ]

    return {"mapeos": mapeos, "advertencias": resultado.get("advertencias") or []}
