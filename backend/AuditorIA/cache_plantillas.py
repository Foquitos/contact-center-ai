"""Caché de contexto de Gemini para el bloque fijo de cada plantilla.

Qué se cachea
-------------
De todo lo que viaja en una auditoría, hay una parte que es IDÉNTICA en los miles
de llamados de una plantilla: la instrucción de sistema y el texto de la plantilla
(los atributos con su consigna, las instrucciones de opcionales, de texto breve y
de incidencias). Lo único propio de cada auditoría es el audio y los datos de la
interacción.

Medido el 2026-09-01 con `count_tokens`, ese bloque fijo va de 1.200 a 9.400 tokens
según la plantilla: el 47% de todo el input de auditorías del mes. Hasta ahora se
pagaba entero en cada llamado, porque el audio iba PRIMERO en el turno y cortaba
cualquier prefijo repetido (por eso `cached_tokens` venía NULL en las 23.000
auditorías del período).

Acá ese bloque se sube UNA vez como `CachedContent` y cada request lo referencia
por nombre. Gemini lo cobra ~10 veces más barato (US$0,075 contra US$0,75 por millón
a precio de lista) y a cambio cobra el almacenamiento por hora de vida.

Cuándo conviene
---------------
El almacenamiento cuesta US$0,50 por millón de tokens por hora y el ahorro es de
US$0,30 por millón por llamado (a precio batch). O sea: **una caché se paga sola si
recibe más de 1,7 auditorías por cada hora que vive**. De ahí salen las dos reglas:

* La caché se busca por contenido y se REUSA entre corridas (`_buscar_viva`). Una
  plantilla que audita todos los días mantiene una sola caché viva y amortiza el
  almacenamiento entre todas sus corridas: es donde está casi todo el ahorro.
* Crear una caché nueva para una corrida chica no se paga. Por eso hay un mínimo de
  llamados para CREARLA (`minimo_llamados`, derivado del TTL: 40 con TTL de 24 h);
  para reusar una que ya está viva no hay mínimo, porque el almacenamiento ya se
  está pagando igual. Medido sobre las auditorías del 2026-09-01: con un mínimo de
  10, cuatro de las seis plantillas del día daban plata en contra —la peor fue una
  de 10 llamados con 9.401 tokens de bloque fijo, que ahorró US$0,028 y pagó
  US$0,113 de almacenamiento— y el neto del día caía de US$0,66 a US$0,55.

Por qué el TTL es largo
-----------------------
Si la caché vence mientras el lote está en la cola de Gemini, los requests que la
referencian fallan y esas auditorías se pierden. El batch tiene 24 h de plazo, así
que el TTL por defecto las cubre enteras. Sale más caro en almacenamiento que un TTL
corto, pero un lote perdido cuesta muchísimo más que unos centavos de storage.

Nada de esto puede romper una auditoría: si crear, buscar o refrescar la caché falla,
se devuelve None y el llamado sale como salía antes, con el bloque fijo inline.
"""

from __future__ import annotations

import hashlib
import logging
import math
from typing import Any, Dict, List, Optional

from google.genai import types

from app.config import settings
from AuditorIA.execution_log import calcular_costo_usd

logger = logging.getLogger(__name__)

# Prefijo del display_name. Sirve para reconocer las cachés de auditoría entre las
# de cualquier otra cosa que en el futuro use la misma API key.
PREFIJO = "auditoria-plantilla"

# Relación caracteres/token del español en estos prompts. Calibrada con `count_tokens`
# contra las 11 plantillas activas al 2026-09-01: el promedio dio 4,18 y el rango 3,93 a
# 4,28, o sea que la estimación queda dentro del ±4% en todas. Se usa para descartar
# bloques chicos y para mostrar el tamaño en la pantalla de Auditar sin gastar una
# llamada a Gemini cada vez que se cambia de plantilla.
CHARS_POR_TOKEN = 4.18

# Llamados que hacen falta por cada HORA de vida de la caché para que se pague sola:
# el almacenamiento cuesta US$0,50 por millón de tokens por hora y cada llamado ahorra
# US$0,30 por millón (precio batch). De acá sale el mínimo para crear una caché nueva.
LLAMADOS_POR_HORA_PARA_PAGARSE = 0.50 / 0.30


def _clave(sistema: str, texto: str, modelo: str) -> str:
    """Identidad de la caché: su contenido exacto.

    Direccionada por contenido y no por PlantillaID+versión a propósito: si alguien
    edita la plantilla, el hash cambia solo y la corrida siguiente crea una caché
    nueva en vez de auditar con el texto viejo. La vieja se muere sola al vencer.
    """
    h = hashlib.sha1(f"{modelo}\x00{sistema}\x00{texto}".encode("utf-8")).hexdigest()
    return h[:16]


def nombre_visible(plantilla_id: Optional[int], sistema: str, texto: str, modelo: str) -> str:
    return f"{PREFIJO}-{plantilla_id or 's-id'}-{_clave(sistema, texto, modelo)}"


def _ttl_horas() -> int:
    return max(1, int(getattr(settings, "GEMINI_CACHE_TTL_HORAS", 24)))


def _ttl_segundos() -> int:
    return _ttl_horas() * 3600


def minimo_llamados() -> int:
    """Cuántos llamados justifican CREAR una caché nueva.

    Si no está seteado a mano se deriva del TTL, que es lo correcto: una caché que vive
    24 h necesita 40 llamados para pagar su almacenamiento, una que vive 6 h necesita 10.
    Un número fijo se desalinea en cuanto alguien toca el TTL.
    """
    configurado = getattr(settings, "GEMINI_CACHE_MIN_LLAMADOS", None)
    if configurado is not None:
        return int(configurado)
    return math.ceil(LLAMADOS_POR_HORA_PARA_PAGARSE * _ttl_horas())


def tokens_del_bloque_fijo(sistema: str, texto: str) -> int:
    """Cuántos tokens pesa el bloque que se cachea, estimados por caracteres.

    Estimado y no `count_tokens` a propósito: esto se muestra en pantalla mientras
    el usuario arma la auditoría y no puede costar un ida y vuelta a Gemini cada
    vez que cambia la plantilla. El error de la estimación es de un par de puntos
    (ver CHARS_POR_TOKEN, medido contra las plantillas reales).
    """
    return int((len(sistema or "") + len(texto or "")) / CHARS_POR_TOKEN)


def estimar_ahorro(
    sistema: str,
    texto: str,
    *,
    tarifa: Optional[tuple] = None,
    modo: str = "batch",
) -> Dict[str, Any]:
    """Qué ahorra la caché en una corrida de esta plantilla, para mostrarlo al usuario.

    Devuelve el ahorro POR AUDITORÍA y el mínimo de llamados que hace falta para que
    la caché se cree, que es lo que hace que auditar en cantidad convenga: el bloque
    fijo se paga una vez por corrida, así que cuantas más auditorías lo compartan,
    menos cuesta cada una.

    `tarifa` es la de `execution_log.obtener_tarifa` (input, output, cacheado). Sin
    tarifa se devuelve el ahorro en tokens y `ahorro_usd_por_auditoria` en None: la
    pantalla puede mostrar igual el resto.
    """
    tokens = tokens_del_bloque_fijo(sistema, texto)
    activo = bool(getattr(settings, "GEMINI_CACHE_PLANTILLAS", True))
    minimo = minimo_llamados()

    ahorro_usd = None
    if tarifa and len(tarifa) > 2:
        input_usd, cached_usd = tarifa[0], tarifa[2]
        ahorro_usd = tokens * (input_usd - cached_usd) / 1_000_000
        if modo in ("batch", "flex"):
            ahorro_usd *= 0.5

    return {
        "activo": activo and tokens >= getattr(settings, "GEMINI_CACHE_MIN_TOKENS", 1024),
        "tokens_bloque_fijo": tokens,
        "minimo_llamados": minimo,
        "ahorro_usd_por_auditoria": ahorro_usd,
        "modo": modo,
    }


def costo_por_auditoria(
    historial: List[Dict[str, Any]],
    *,
    tokens_conocimiento_actual: int = 0,
    tarifa: Optional[tuple] = None,
    modo: str = "batch",
) -> Optional[float]:
    """Lo que cuesta HOY una auditoría de la plantilla, sin caché: el denominador del
    "cada auditoría sale X% más barata" de la pantalla de Auditar.

    `historial` trae una fila por versión de la plantilla con auditorías recientes:
    sumas de `input_tokens`, `output_tokens`, `thoughts_tokens`, la cantidad de
    `auditorias` y los `tokens_conocimiento` que leía esa versión (ver
    AuditorIA/conocimiento_plantilla.py).

    Por qué no alcanza con promediar lo que vino costando: cada auditoría vieja pagó
    el bloque fijo de SU versión. Si la plantilla sumó documentos de referencia, el
    ahorro ya se calcula con el bloque nuevo (decenas de miles de tokens) pero el
    historial no los pagó, y el porcentaje salía inflado —pasaba del 100%—. Por eso se
    descuenta el conocimiento de cada versión y se suma el de hoy; el resto del input
    (audio, datos del llamado, consignas) y la salida salen del historial.

    Mismo modo que el ahorro y sin descontar caché, para que el porcentaje compare el
    mismo llamado con y sin caché. None si no hay historial o tarifa.
    """
    auditorias = sum(int(h.get("auditorias") or 0) for h in historial)
    if not auditorias or not tarifa:
        return None
    input_sin_conocimiento = sum(
        int(h.get("input_tokens") or 0) - int(h.get("auditorias") or 0) * int(h.get("tokens_conocimiento") or 0)
        for h in historial
    )
    tokens = {
        "input_tokens": max(input_sin_conocimiento / auditorias, 0) + tokens_conocimiento_actual,
        "output_tokens": sum(int(h.get("output_tokens") or 0) for h in historial) / auditorias,
        "thoughts_tokens": sum(int(h.get("thoughts_tokens") or 0) for h in historial) / auditorias,
    }
    return calcular_costo_usd(tokens, modo, tarifa)


def _buscar_viva(gemini_api, display_name: str) -> Optional[str]:
    """Nombre del recurso de una caché viva con ese display_name, o None.

    Las vencidas ya no aparecen en el listado, así que lo que vuelve de acá se puede
    usar. Igual el llamador tolera que muera entre esta consulta y el request: el
    error de ese request se maneja como cualquier otro fallo de auditoría.
    """
    try:
        for cache in gemini_api.caches.list():
            if getattr(cache, "display_name", None) == display_name:
                return cache.name
    except Exception:
        logger.warning("No se pudo listar las cachés de contexto de Gemini.", exc_info=True)
    return None


def _refrescar(gemini_api, nombre: str) -> None:
    """Corre el vencimiento hacia adelante. Es lo que hace que una plantilla que se
    audita todos los días mantenga UNA caché viva en vez de crear una por corrida."""
    try:
        gemini_api.caches.update(
            name=nombre,
            config=types.UpdateCachedContentConfig(ttl=f"{_ttl_segundos()}s"),
        )
    except Exception:
        # No es grave: la caché sigue viva con el vencimiento que tenía.
        logger.warning("No se pudo extender el TTL de la caché %s.", nombre, exc_info=True)


def obtener_o_crear(
    gemini_api,
    prompt_info: Dict[str, Any],
    *,
    sistema: str,
    modelo: str,
    llamados: int,
    plantilla_id: Optional[int] = None,
) -> Optional[str]:
    """Nombre de la caché a referenciar en los requests, o None para no usar caché.

    `llamados` es cuántas auditorías van a compartirla en esta corrida: decide si
    vale la pena CREARLA (ver el encabezado del módulo).
    """
    if not getattr(settings, "GEMINI_CACHE_PLANTILLAS", True):
        return None

    texto = prompt_info.get("text") or ""
    if not texto:
        return None

    # Bloque chico: el ahorro no paga ni la llamada de más. El corte va en tokens
    # estimados por caracteres para no gastar un count_tokens por corrida.
    tokens_aprox = (len(sistema) + len(texto)) / CHARS_POR_TOKEN
    if tokens_aprox < getattr(settings, "GEMINI_CACHE_MIN_TOKENS", 1024):
        return None

    display_name = nombre_visible(plantilla_id, sistema, texto, modelo)

    viva = _buscar_viva(gemini_api, display_name)
    if viva:
        # Ya está paga: se reusa sin mínimo de llamados y se le corre el vencimiento.
        _refrescar(gemini_api, viva)
        logger.info("Auditoría: reusando la caché de contexto %s (%s).", display_name, viva)
        return viva

    if llamados < minimo_llamados():
        return None

    try:
        cache = gemini_api.caches.create(
            model=modelo,
            config=types.CreateCachedContentConfig(
                display_name=display_name,
                system_instruction=sistema or None,
                contents=[types.Content(role="user", parts=[types.Part.from_text(text=texto)])],
                ttl=f"{_ttl_segundos()}s",
            ),
        )
    except Exception:
        # El caso típico es que el bloque no llegue al mínimo de tokens que pide el
        # modelo. No se reintenta ni se avisa fuerte: la corrida sigue sin caché.
        logger.warning(
            "No se pudo crear la caché de contexto de la plantilla %s; la corrida sigue sin caché.",
            plantilla_id, exc_info=True,
        )
        return None

    tokens = getattr(getattr(cache, "usage_metadata", None), "total_token_count", None)
    logger.info(
        "Auditoría: caché de contexto creada para la plantilla %s (%s tokens, %s llamados).",
        plantilla_id, tokens, llamados,
    )
    return cache.name
