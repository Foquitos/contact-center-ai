"""Enriquecimiento del contexto del Asistente Analítico del Dashboard.

El asistente respondía SOLO con lo que el navegador podía calcular sobre las filas
que tenía en pantalla. Eso dejaba afuera tres cosas que son justo las que pregunta
un supervisor:

1. **Qué se dijo en la llamada.** El motivo de un error crítico se deducía del
   *nombre* del atributo que dio false ("No valida identidad"), nunca de lo que el
   asesor efectivamente dijo. Acá se trae la transcripción de los llamados que la
   pregunta señala (o de los peores casos, cuando la pregunta es de causa raíz).
2. **Cómo venía antes.** El dashboard es una foto de un período. "¿Mejoramos
   contra el mes pasado?" era incontestable. Acá se calculan los mismos KPIs sobre
   el período inmediatamente anterior, de igual duración.
3. **Las filas que importan.** La muestra que viajaba era "las primeras 150", sin
   relación con la pregunta: preguntabas por un asesor y podía no haber ni una
   llamada suya en la muestra. Acá se eligen las filas según lo que la pregunta
   nombra.

**Todo esto se decide sin llamar al modelo**: se detecta a qué se refiere la
pregunta cruzándola contra los nombres, equipos e IdAplicativo que YA están en el
contexto. Es determinístico, testeable sin tokens y no agrega latencia de IA.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import text

from app.database import engine

logger = logging.getLogger(__name__)

# Cuántos llamados se transcriben como máximo en un turno. La transcripción es
# larga (una llamada de 5 minutos son ~6.000 caracteres); con 4 alcanza para
# fundamentar un diagnóstico sin tapar el resto del contexto.
MAX_TRANSCRIPCIONES = 4
MAX_CHARS_TRANSCRIPCION = 14_000
# Filas individuales que se imprimen en el reporte, ya elegidas por relevancia.
MAX_FILAS_REPORTE = 200
# Vida del cache del período anterior: sirve para todas las repreguntas de una
# misma charla sin volver a golpear el SP, que es la parte cara.
TTL_COMPARATIVA = 900  # segundos
MAX_ENTRADAS_CACHE = 32

_STOPWORDS = {
    "de", "del", "la", "el", "los", "las", "y", "en", "con", "por", "para", "un",
    "una", "que", "sin", "sobre", "como", "mas", "sus", "sin",
}


def _normalizar(texto: Any) -> str:
    """Minúsculas sin acentos: 'Gómez, María' -> 'gomez, maria'."""
    s = str(texto or "").lower()
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _tokens(nombre: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", _normalizar(nombre)) if len(t) >= 3 and t not in _STOPWORDS]


# --------------------------------------------------------------------------- #
# 1. A qué se refiere la pregunta                                              #
# --------------------------------------------------------------------------- #

# Pide que le citen lo que se dijo, o busca la causa de un desvío.
_PATRONES_VERBATIM = (
    "transcrip", "que dijo", "que dice", "textual", "literal", "palabras",
    "escuchar", "audio", "guion", "libreto", "causa raiz", "causa-raiz",
    "por que fallo", "por que fallaron", "por que se cayo", "detalle de la llamada",
    "detalle del llamado", "ejemplo concreto", "que paso en", "evidencia",
    "fundamenta", "justifica", "verbatim",
)
# Pide comparar contra otro momento.
_PATRONES_TEMPORAL = (
    "mes pasado", "mes anterior", "periodo anterior", "semana pasada",
    "compar", "tendencia", "evoluc", "veniamos", "venia", "mejoro", "mejoramos",
    "empeoro", "empeoramos", "respecto a", "contra el", "historico", "antes",
    "ultimos meses", "vs ", "versus",
)
# Un informe ejecutivo sin la foto anterior es media respuesta.
_PATRONES_INFORME = ("informe", "resumen ejecutivo", "reporte", "comite", "gerencia")


@dataclass
class Foco:
    """Qué señala la pregunta, resuelto contra los datos que ya están en pantalla."""
    operadores: List[str] = field(default_factory=list)
    equipos: List[str] = field(default_factory=list)
    ids: List[str] = field(default_factory=list)
    atributos: List[str] = field(default_factory=list)
    quiere_verbatim: bool = False
    quiere_comparativa: bool = False

    @property
    def hay_foco(self) -> bool:
        return bool(self.operadores or self.equipos or self.ids or self.atributos)


def _nombres_del_contexto(contexto: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    operadores = [
        str(o.get("operador") or "")
        for o in (contexto.get("resumen_operadores") or [])
        if o.get("operador")
    ]
    equipos = [
        str(e.get("equipo") or "")
        for e in (contexto.get("resumen_equipos") or [])
        if e.get("equipo") and e.get("equipo") != "—"
    ]
    return operadores, equipos


def _nombres_mencionados(pregunta_norm: str, nombres: Sequence[str]) -> List[str]:
    """Nombres del dataset que aparecen en la pregunta.

    Se exige o el nombre completo, o dos partes distintas del nombre (evita que
    "¿cómo viene el equipo de mañana?" matchee a un asesor apellidado Mañana), o
    una sola parte larga que sea ÚNICA en la campaña (así "¿qué pasó con
    Etchevarría?" funciona, pero "Gómez" con tres Gómez no elige uno al azar).
    """
    encontrados: List[str] = []
    conteo_token: Dict[str, int] = {}
    for nombre in nombres:
        for t in set(_tokens(nombre)):
            conteo_token[t] = conteo_token.get(t, 0) + 1

    for nombre in nombres:
        norm = _normalizar(nombre)
        if norm and norm in pregunta_norm:
            encontrados.append(nombre)
            continue
        toks = [t for t in set(_tokens(nombre)) if t in pregunta_norm]
        if len(toks) >= 2 or any(len(t) >= 6 and conteo_token.get(t) == 1 for t in toks):
            encontrados.append(nombre)
    return encontrados


def _mencion_exacta(valor: str, texto: str) -> bool:
    """¿`valor` aparece en `texto` como token completo?

    Con `in` a secas, el id 1234 matchea dentro de 12345 y el asistente terminaba
    trayendo la transcripción de una llamada que nadie pidió (los AuditoriaID son
    enteros: convivir con un id que es prefijo de otro es lo normal, no el borde).
    """
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(valor)}(?![A-Za-z0-9_])", texto) is not None


def _ids_mencionados(pregunta: str, contexto: Dict[str, Any]) -> List[str]:
    """IdAplicativo/AuditoriaID que aparecen en la pregunta.

    Se buscan los ids REALES del dataset dentro del texto en vez de inventar una
    expresión regular de "esto parece un id": así un año ("2026") o un porcentaje
    no se confunden con un llamado."""
    candidatos: List[str] = []
    vistos = set()
    for coleccion in ("casos_ec_detalle", "filas"):
        for fila in (contexto.get(coleccion) or []):
            for clave in ("IdAplicativo", "AuditoriaID"):
                valor = fila.get(clave)
                if valor in (None, ""):
                    continue
                sval = str(valor)
                if len(sval) >= 4 and sval not in vistos:
                    vistos.add(sval)
                    candidatos.append(sval)
    return [c for c in candidatos if _mencion_exacta(c, pregunta)][:MAX_TRANSCRIPCIONES]


def analizar_pregunta(pregunta: str, contexto: Dict[str, Any]) -> Foco:
    """Resuelve la pregunta contra los datos en pantalla. Sin IA."""
    norm = _normalizar(pregunta)
    operadores, equipos = _nombres_del_contexto(contexto)
    atributos = [
        str(a.get("nombre"))
        for a in (contexto.get("atributos") or [])
        if a.get("nombre") and _normalizar(a["nombre"]) in norm
    ]
    ids = _ids_mencionados(pregunta, contexto)
    ops = _nombres_mencionados(norm, operadores)

    quiere_verbatim = bool(ids) or any(p in norm for p in _PATRONES_VERBATIM)
    quiere_comparativa = any(p in norm for p in _PATRONES_TEMPORAL) or any(
        p in norm for p in _PATRONES_INFORME
    )

    return Foco(
        operadores=ops,
        equipos=_nombres_mencionados(norm, equipos),
        ids=ids,
        atributos=atributos,
        quiere_verbatim=quiere_verbatim,
        quiere_comparativa=quiere_comparativa,
    )


# --------------------------------------------------------------------------- #
# 2. Qué filas mostrar                                                         #
# --------------------------------------------------------------------------- #

def _es_ec(fila: Dict[str, Any]) -> bool:
    return str(fila.get("EsEC") or "").lower() in ("sí", "si", "true", "1") or bool(
        fila.get("EsErrorCritico") or fila.get("TieneErrorCritico")
    )


def _puntaje(fila: Dict[str, Any]) -> float:
    for clave in ("Puntaje", "PuntajeFinal"):
        valor = fila.get(clave)
        if isinstance(valor, (int, float)):
            return float(valor)
    return 101.0  # sin puntaje: al final del orden "peores primero"


def seleccionar_filas(
    filas: List[Dict[str, Any]],
    foco: Foco,
    tope: int = MAX_FILAS_REPORTE,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Elige qué auditorías individuales entran en el reporte y por qué.

    Prioridad: los llamados que la pregunta nombra, después los del asesor/equipo
    por el que se pregunta (peor puntaje primero), después los errores críticos, y
    recién ahí se completa con el resto. Devuelve (filas, criterios) — los
    criterios se imprimen en el reporte para que el modelo sepa qué está mirando.
    """
    if not filas:
        return [], []

    ids_foco = set(foco.ids)
    ops_foco = {_normalizar(o) for o in foco.operadores}
    eqs_foco = {_normalizar(e) for e in foco.equipos}

    def _id_de(f: Dict[str, Any]) -> str:
        return str(f.get("IdAplicativo") or f.get("AuditoriaID") or "")

    nombradas, del_foco, criticas, resto = [], [], [], []
    for f in filas:
        if ids_foco and _id_de(f) in ids_foco:
            nombradas.append(f)
        elif ops_foco and _normalizar(f.get("Operador")) in ops_foco:
            del_foco.append(f)
        elif eqs_foco and _normalizar(f.get("Equipo")) in eqs_foco:
            del_foco.append(f)
        elif _es_ec(f):
            criticas.append(f)
        else:
            resto.append(f)

    del_foco.sort(key=_puntaje)

    criterios: List[str] = []
    if nombradas:
        criterios.append(f"los {len(nombradas)} llamados citados en la pregunta")
    if del_foco:
        quienes = ", ".join(foco.operadores + foco.equipos)
        criterios.append(f"los {len(del_foco)} de {quienes} (peor puntaje primero)")
    if criticas:
        criterios.append(f"{min(len(criticas), tope)} con error crítico")

    elegidas: List[Dict[str, Any]] = []
    for grupo in (nombradas, del_foco, criticas, resto):
        for f in grupo:
            if len(elegidas) >= tope:
                return elegidas, criterios
            elegidas.append(f)
    return elegidas, criterios


def ids_para_transcribir(contexto: Dict[str, Any], foco: Foco) -> List[str]:
    """Qué llamados vale la pena transcribir en este turno.

    Los que la pregunta cita; si no cita ninguno pero pide fundamentar, los peores
    casos del foco (o los errores críticos del período)."""
    if foco.ids:
        return foco.ids[:MAX_TRANSCRIPCIONES]
    if not foco.quiere_verbatim:
        return []

    ops_foco = {_normalizar(o) for o in foco.operadores}
    eqs_foco = {_normalizar(e) for e in foco.equipos}

    def _coincide(fila: Dict[str, Any]) -> bool:
        if not ops_foco and not eqs_foco:
            return True
        return (
            _normalizar(fila.get("Operador")) in ops_foco
            or _normalizar(fila.get("Equipo")) in eqs_foco
        )

    candidatos = [c for c in (contexto.get("casos_ec_detalle") or []) if _coincide(c)]
    if not candidatos:
        # Sin errores críticos: los llamados de peor puntaje del foco.
        candidatos = sorted(
            [f for f in (contexto.get("filas") or []) if _coincide(f)],
            key=_puntaje,
        )

    ids: List[str] = []
    for c in candidatos:
        valor = c.get("IdAplicativo") or c.get("AuditoriaID")
        if valor not in (None, "") and str(valor) not in ids:
            ids.append(str(valor))
        if len(ids) >= MAX_TRANSCRIPCIONES:
            break
    return ids


# --------------------------------------------------------------------------- #
# 3. Transcripciones (SP calidad.sp_ObtenerAuditoriasFiltradas)                 #
# --------------------------------------------------------------------------- #

_SP_TRANSCRIPCION = text("""
    EXEC calidad.sp_ObtenerAuditoriasFiltradas
        @AuditorUsuarioID = NULL,
        @CampanaID = :campana,
        @EmpresaID = :empresa,
        @PlantillaID = :plantilla,
        @FechaDesde = NULL,
        @FechaHasta = NULL,
        @FechaInteraccionDesde = NULL,
        @FechaInteraccionHasta = NULL,
        @IdAplicativo = :id_aplicativo,
        @IncluirTranscripcion = 1,
        @IncluirResponseThoughts = 0
""")


def _formatear_transcripcion(crudo: Any) -> str:
    """`TranscripcionJSON` es una lista de {speakerLabel, text}; se arma el diálogo."""
    if not crudo or crudo == "null":
        return ""
    try:
        segmentos = json.loads(crudo) if isinstance(crudo, str) else crudo
    except (ValueError, TypeError):
        return str(crudo)[:MAX_CHARS_TRANSCRIPCION]
    if not isinstance(segmentos, list):
        return str(crudo)[:MAX_CHARS_TRANSCRIPCION]

    lineas = []
    for seg in segmentos:
        if not isinstance(seg, dict):
            continue
        texto = seg.get("text")
        if isinstance(texto, list):
            texto = " ".join(str(t) for t in texto)
        texto = str(texto or "").strip()
        if not texto:
            continue
        lineas.append(f"[{seg.get('speakerLabel') or 'Hablante'}]: {texto}")
    return "\n".join(lineas)


def transcripciones(
    ids: Sequence[str],
    plantilla_id: Optional[int],
    empresa_id: Optional[int] = None,
    campana_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Trae la transcripción de cada llamado pedido.

    ⚠️ El alcance por empresa lo valida el ROUTER antes de llamar acá (la plantilla
    llega desde el navegador). Este módulo no decide permisos.
    """
    if not ids or not plantilla_id:
        return []

    salida: List[Dict[str, Any]] = []
    try:
        with engine.connect() as conn:
            for id_ap in list(ids)[:MAX_TRANSCRIPCIONES]:
                filas = conn.execute(_SP_TRANSCRIPCION, {
                    "plantilla": plantilla_id,
                    "empresa": empresa_id,
                    "campana": campana_id,
                    "id_aplicativo": id_ap,
                }).mappings().all()
                if not filas:
                    continue
                fila = filas[0]
                texto = _formatear_transcripcion(fila.get("TranscripcionJSON"))
                if not texto:
                    continue
                salida.append({
                    "id_aplicativo": str(fila.get("IdAplicativo") or id_ap),
                    "operador": fila.get("Agente") or fila.get("operadorUsuario") or "—",
                    "equipo": fila.get("Equipo") or "—",
                    "fecha": str(fila.get("fecha_interaccion") or "")[:19],
                    "duracion_segundos": fila.get("duracion_segundos"),
                    "puntaje": fila.get("PuntajeFinal"),
                    "texto": texto[:MAX_CHARS_TRANSCRIPCION],
                    "truncada": len(texto) > MAX_CHARS_TRANSCRIPCION,
                })
    except Exception:
        # Sin transcripción el asistente sigue contestando con los agregados; que
        # falle el SP no puede dejar al supervisor sin respuesta.
        logger.exception("No se pudieron traer transcripciones (plantilla=%s, ids=%s)", plantilla_id, list(ids))
        return salida
    return salida


# --------------------------------------------------------------------------- #
# 4. Período anterior                                                          #
# --------------------------------------------------------------------------- #

_SP_PERIODO = text("""
    EXEC calidad.sp_ObtenerAuditoriasFiltradas
        @AuditorUsuarioID = NULL,
        @CampanaID = :campana,
        @EmpresaID = :empresa,
        @PlantillaID = :plantilla,
        @FechaDesde = :fecha_desde,
        @FechaHasta = :fecha_hasta,
        @FechaInteraccionDesde = :fi_desde,
        @FechaInteraccionHasta = :fi_hasta,
        @IdAplicativo = NULL,
        @IncluirTranscripcion = 0,
        @IncluirResponseThoughts = 0
""")

_cache_comparativa: Dict[Tuple, Tuple[float, Optional[Dict[str, Any]]]] = {}
_cache_lock = threading.Lock()


def _leer_cache(clave: Tuple) -> Tuple[bool, Optional[Dict[str, Any]]]:
    with _cache_lock:
        entrada = _cache_comparativa.get(clave)
        if entrada and (time.time() - entrada[0]) < TTL_COMPARATIVA:
            return True, entrada[1]
        if entrada:
            _cache_comparativa.pop(clave, None)
    return False, None


def _guardar_cache(clave: Tuple, valor: Optional[Dict[str, Any]]) -> None:
    with _cache_lock:
        if len(_cache_comparativa) >= MAX_ENTRADAS_CACHE:
            mas_viejo = min(_cache_comparativa.items(), key=lambda kv: kv[1][0])[0]
            _cache_comparativa.pop(mas_viejo, None)
        _cache_comparativa[clave] = (time.time(), valor)


def periodo_anterior(desde: str, hasta: str) -> Optional[Tuple[str, str]]:
    """La ventana inmediatamente anterior, de la MISMA duración.

    Del 01/08 al 31/08 (31 días) devuelve del 01/07 al 31/07. Comparar contra una
    ventana de otro largo daría un "bajamos un 40%" que solo mide que el período
    es más corto."""
    from datetime import date, timedelta

    try:
        d = date.fromisoformat(str(desde)[:10])
        h = date.fromisoformat(str(hasta)[:10])
    except (ValueError, TypeError):
        return None
    if h < d:
        return None
    dias = (h - d).days + 1
    return ((d - timedelta(days=dias)).isoformat(), (d - timedelta(days=1)).isoformat())


def _kpis_de_filas(filas: List[Dict[str, Any]], atributos: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Los mismos KPIs que muestra el dashboard, calculados en el backend."""
    total = len(filas)
    puntajes = [f["PuntajeFinal"] for f in filas if isinstance(f.get("PuntajeFinal"), (int, float))]
    ec = sum(1 for f in filas if f.get("EsErrorCritico") in (True, 1, "1", "Sí"))

    cumplimiento: Dict[str, float] = {}
    for atr in atributos:
        nombre = atr.get("nombre")
        if not nombre or (atr.get("tipo") or "").lower() not in ("boolean", "critical_audit"):
            continue
        valores = [f.get(nombre) for f in filas if f.get(nombre) is not None]
        if not valores:
            continue
        ok = sum(1 for v in valores if v in (True, 1, "1", "Sí", "si", "sí"))
        cumplimiento[nombre] = round(ok * 100 / len(valores), 1)

    equipos: Dict[str, Dict[str, Any]] = {}
    for f in filas:
        eq = str(f.get("Equipo") or "—")
        d = equipos.setdefault(eq, {"casos": 0, "suma": 0.0, "con_puntaje": 0, "ec": 0})
        d["casos"] += 1
        if isinstance(f.get("PuntajeFinal"), (int, float)):
            d["suma"] += float(f["PuntajeFinal"])
            d["con_puntaje"] += 1
        if f.get("EsErrorCritico") in (True, 1, "1", "Sí"):
            d["ec"] += 1

    return {
        "total": total,
        "puntaje_promedio": round(sum(puntajes) / len(puntajes), 1) if puntajes else None,
        "casos_ec": ec,
        "pct_ec": round(ec * 100 / total, 1) if total else 0,
        "cumplimiento_atributos": cumplimiento,
        "equipos": {
            eq: {
                "casos": d["casos"],
                "puntaje_promedio": round(d["suma"] / d["con_puntaje"], 1) if d["con_puntaje"] else None,
                "pct_ec": round(d["ec"] * 100 / d["casos"], 1) if d["casos"] else 0,
            }
            for eq, d in equipos.items()
        },
    }


def comparativa_periodo_anterior(
    plantilla_id: Optional[int],
    desde: str,
    hasta: str,
    base_fecha: str = "interaccion",
    empresa_id: Optional[int] = None,
    campana_id: Optional[int] = None,
    atributos: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """KPIs del período inmediatamente anterior, de igual duración.

    Cacheado por (plantilla, ventana, base de fecha): la primera pregunta de la
    charla paga el SP y las repreguntas no. Devuelve None si no se pudo calcular.

    ⚠️ El alcance por empresa lo valida el ROUTER antes de llamar acá.
    """
    if not plantilla_id:
        return None
    ventana = periodo_anterior(desde, hasta)
    if not ventana:
        return None
    prev_desde, prev_hasta = ventana

    clave = (int(plantilla_id), prev_desde, prev_hasta, base_fecha)
    hubo, cacheado = _leer_cache(clave)
    if hubo:
        return cacheado

    por_interaccion = (base_fecha or "interaccion").lower() != "auditoria"
    try:
        with engine.connect() as conn:
            filas = [dict(r) for r in conn.execute(_SP_PERIODO, {
                "campana": campana_id,
                "empresa": empresa_id,
                "plantilla": plantilla_id,
                "fecha_desde": None if por_interaccion else prev_desde,
                "fecha_hasta": None if por_interaccion else prev_hasta,
                "fi_desde": prev_desde if por_interaccion else None,
                "fi_hasta": prev_hasta if por_interaccion else None,
            }).mappings().all()]
    except Exception:
        logger.exception("No se pudo leer el período anterior (plantilla=%s)", plantilla_id)
        _guardar_cache(clave, None)
        return None

    if not filas:
        # Que no haya datos ES un dato: evita que el modelo invente una tendencia.
        resultado = {"desde": prev_desde, "hasta": prev_hasta, "total": 0}
        _guardar_cache(clave, resultado)
        return resultado

    kpis = _kpis_de_filas(filas, atributos or [])
    kpis.update({"desde": prev_desde, "hasta": prev_hasta})
    _guardar_cache(clave, kpis)
    return kpis
