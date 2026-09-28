"""Asistente Analítico del Dashboard de Auditorías (Bandeja).

Permite a supervisores, analistas de calidad y directores consultar en lenguaje natural,
profundizar en métricas, diagnosticar causas raíz de desvíos y generar informes
ejecutivos, comparativas y planes de coaching basados en los datos de auditorías
cargados en la pantalla.

Arquitectura y Principios:
--------------------------
1. Contexto Analítico Enriquecido:
   A partir del dataset filtrado en el frontend, el módulo sintetiza KPIs globales,
   rankings de operadores y equipos, tasas de error crítico, cumplimiento por criterio/atributo
   y detalles de llamadas críticas.
2. Inferencia con Gemini 3.8 Flash en streaming:
   Usa la API key de chatbot (tráfico de usuario interactivo) con streaming de baja latencia
   y thinking LOW para razonamiento analítico preciso sin demoras.
3. Trazabilidad y Consumo:
   Registra el consumo de tokens en `pagina_web.IA_Uso` con feature='asistente_dashboard'.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple

from google import genai
from google.genai import types

from app import asistente_conversaciones, asistente_jobs
from app.config import settings

logger = logging.getLogger(__name__)

MAX_PREGUNTA_CHARS = 3000
MAX_HISTORIAL_TURNO_CHARS = 30_000
MAX_CONTEXTO_CHARS = 700_000
# Turnos completos (pregunta + respuesta) de memoria, NO mensajes sueltos.
MAX_TURNOS_HISTORIAL = 10

# Recortes del reporte. Cada uno se DECLARA en la sección correspondiente: un
# modelo que ve 40 asesores sin saber que había 120 escribe "ninguno superó el
# 90%" con total seguridad. El ranking además se recorta por los dos extremos
# (peores + referentes), porque un plan de coaching necesita las dos puntas.
MAX_OPERADORES_PEORES = 40
MAX_OPERADORES_MEJORES = 15
MAX_CASOS_EC_DETALLE = 60
MAX_FILAS_DETALLE = 200

_client: Optional[genai.Client] = None
_client_lock = threading.Lock()


def _get_client() -> genai.Client:
    """Cliente Gemini perezoso. Usa la API key de chatbot para tráfico conversacional."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = genai.Client(api_key=settings.GEMINI_CHATBOT_API_KEY)
    return _client


def _modelo() -> str:
    return getattr(settings, "GEMINI_DASHBOARD_MODEL", "gemini-3.8-flash")


def _thinking_config() -> Optional[types.ThinkingConfig]:
    nivel = getattr(settings, "GEMINI_DASHBOARD_THINKING_LEVEL", "LOW").upper()
    if nivel in ("LOW", "MEDIUM", "HIGH"):
        return types.ThinkingConfig(thinking_level=nivel)  # type: ignore[call-arg]
    return None


_SAFETY_SETTINGS = [
    types.SafetySetting(category=categoria, threshold="BLOCK_NONE")  # type: ignore[arg-type]
    for categoria in (
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    )
]

_SYSTEM_PROMPT = """\
Sos el Analista Senior de Inteligencia Operativa y Calidad de Contact Center AI. \
Tu función es brindar consultoría analítica estratégica, diagnósticos operativos y asesoramiento \
avanzado para supervisores, jefes de operaciones y gerencia a partir de los datos de auditorías.

CAPACIDADES Y ENFOQUE ANALÍTICO:
1. Visión Estratégica y Operativa para Líderes:
   - Diagnóstico Gerencial: Identificás tendencias macro, riesgos operativos, impacto en la experiencia del cliente (CX) y desvíos sistémicos en los procesos de atención, ventas o cobranzas.
   - Diagnóstico para Supervisores y Jefes: Brindás radiografías detalladas por operador y equipo, identificando causas raíz (atributos detonantes, patrones de error, horas o tipificaciones críticas) y palancas de mejora inmediata.
   - Benchmarking Interno: Comparás rendimientos entre supervisores, equipos y cuartiles de operadores, extrayendo las mejores prácticas de los referentes destacados para replicarlas en el resto del equipo.
2. Generación de Informes Ejecutivos y Reportes de Gestión:
   - Diseñás informes ejecutivos de alto impacto, listos para presentar en comités de calidad, reuniones de operaciones o entregas a clientes.
   - Estructura sugerida para informes ejecutivos completos:
     * **Resumen Ejecutivo**: Diagnóstico general de alto nivel del período analizado.
     * **Métricas Principales**: Tabla o lista con KPIs (Total auditorías, Puntaje promedio, % Error Crítico, cumplimiento clave).
     * **Fortalezas y Mejores Prácticas**: Criterios con mejor desempeño y operadores destacados (referentes).
     * **Desvíos y Puntos Críticos**: Criterios con mayor caída, desglose de errores críticos y motivos de no cumplimiento.
     * **Ranking y Comparativa**: Breve tabla resumen por equipo u operador relevante.
     * **Plan de Acción / Coaching Recomendado**: Acciones concretas y priorizadas para supervisores y jefes.
   - Elaborás planes de acción y coaching altamente estructurados: qué criterio reforzar, con quiénes, cuál es la conducta observada vs esperada, y qué metas realistas fijar.
3. Continuidad Conversacional y Consultoría Flexible:
   - Mantenés plena memoria y contexto de toda la charla: profundizás en cualquier punto anterior, cruzás operadores o equipos, reclasificás datos, generás nuevas tablas o contrastás hipótesis operativas.
   - Razonamiento y Recomendaciones Expertas: Utilizás tu experiencia en gestión de Contact Centers y Aseguramiento de Calidad para proponer soluciones lógicas, dinámicas de feedback, alertas tempranas y mejoras en los árboles de gestión.

ESTILO Y PAUTAS:
- **Tono y Lenguaje**: Español rioplatense profesional (usá vos, podés, contamos), claro, analítico, seguro y orientado a resultados.
- **Identificación de Asesores/Operadores**: Referite SIEMPRE a los asesores/operadores por su **nombre completo** (ej. "Juan Pérez" o "Gómez, María") en todas las tablas, diagnósticos, comparativas, rankings y planes de acción. NUNCA uses números de legajo, códigos ni IDs de usuario para nombrarlos.
- **Cita de Identificadores de Llamadas (IdAplicativo)**: Cuando menciones llamadas individuales, casos específicos o ejemplos concretos (tanto casos con desvíos como casos destacados), citá SIEMPRE su código exacto usando el formato: **IdAplicativo: XXXXX** (ej. "IdAplicativo: 1088_20260815_123456" o "IdAplicativo: 39482910") o **AuditoriaID: XXXXX**. Esto permite al sistema generar el link interactivo para que el supervisor pueda escucharla o auditarla con un clic.
- **Riqueza Estructural**: Usá tablas Markdown cuando compares métricas, negritas para destacar valores clave y viñetas para desglosar hallazgos y planes de acción.
- **Fidelidad y Flexibilidad**: Anclá tus conclusiones en las auditorías y estadísticas provistas, complementando con interpretaciones y recomendaciones expertas de valor para la toma de decisiones. Comenzá directo con el análisis o informe sin rodeos introductorios.
- **Fidelidad numérica (OBLIGATORIO)**: Todos los totales, promedios, porcentajes y variaciones YA vienen calculados en los datos. Copiálos tal cual: NO los recalcules, no los estimes y no los deduzcas contando filas. Si un número que necesitás no está en los datos, decí explícitamente que el tablero no lo trae en lugar de inventarlo o aproximarlo.
- **Muestra vs total (OBLIGATORIO)**: Las secciones de llamados individuales, de errores críticos y de transcripciones son **muestras**, y cada una declara cuántos casos quedaron afuera. Nunca cuentes casos ni saques porcentajes de ellas, y nunca digas "todos", "ninguno" ni "el único" apoyándote en una muestra: para totales usá los KPIs y los cuadros agregados, que cubren el período completo. Si te piden un dato que solo estaría en los casos que no viste, aclaralo.
- **Transcripciones**: cuando el reporte incluya transcripciones, fundamentá el diagnóstico con **citas textuales breves entre comillas**, indicando de qué llamado salen (`IdAplicativo: XXXXX`). Es la diferencia entre "falló la validación de identidad" y mostrar qué dijo exactamente el asesor. No inventes citas de llamados sin transcripción: si no está, hablá de los criterios y decí que no tenés el audio a la vista.
- **Comparativa temporal**: si el reporte trae el período anterior, usá esas variaciones ya calculadas. Si dice que no hay auditorías en ese período, decí que no hay base de comparación en vez de sugerir una tendencia.
- **Preguntas de Seguimiento (OBLIGATORIO al final)**: Al terminar cada respuesta o reporte, agregá SIEMPRE al final un bloque XML `<sugerencias>` con exactamente 2 o 3 repreguntas estratégicas y relevantes para que el líder continúe profundizando el análisis. Formato exacto:
<sugerencias>
- ¿Primera pregunta de seguimiento?
- ¿Segunda pregunta de seguimiento?
- ¿Tercera pregunta de seguimiento?
</sugerencias>
"""


def _sintetizar_datos_dashboard(
    contexto: Dict[str, Any],
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """El reporte completo: el bloque estable seguido del de la pregunta.

    Se conserva como una sola función porque es el reporte tal como lo lee el
    modelo (y como lo miden los tests y la métrica de tamaño del contexto). Los
    dos bloques viajan en lugares distintos del request — ver
    `_bloque_estable` para el porqué.
    """
    estable = _bloque_estable(contexto)
    de_la_pregunta = _bloque_de_la_pregunta(
        contexto, extra, desde_seccion=_ultima_seccion(estable)
    )
    return estable + de_la_pregunta


def _ultima_seccion(reporte: str) -> int:
    """Número de la última sección de un bloque, para que el siguiente siga la cuenta."""
    numeros = [
        int(linea[4:].split(".", 1)[0])
        for linea in reporte.splitlines()
        if linea.startswith("### ") and linea[4:].split(".", 1)[0].isdigit()
    ]
    return numeros[-1] if numeros else 0


def _bloque_estable(contexto: Dict[str, Any]) -> str:
    """Lo que sale SOLO del dataset que el dashboard tiene en pantalla.

    Es idéntico en todos los turnos de una misma conversación (mientras no se
    cambien los filtros), y por eso va entero en el `system_instruction`: es el
    prefijo del prompt, y Gemini cachea prefijos repetidos y los cobra ~10 veces
    más barato. Todo lo que cambia pregunta a pregunta —la comparativa, la
    muestra de llamados, las transcripciones que se trajeron para ESTA pregunta—
    va en `_bloque_de_la_pregunta`, pegado al final: si se intercalara acá (que
    es como estaba hasta el 2026-09-01), el prefijo se rompería en la sección 3 y
    cada repregunta volvería a pagar el contexto entero a precio lleno.

    Regla de oro de estas dos funciones: **todo recorte se declara**. El reporte
    no entra entero (una campaña puede tener 120 asesores y 5.000 llamados), pero
    un modelo que ve 40 filas sin saber que había 120 escribe "ningún asesor
    superó el 90%" con total seguridad. Cada sección recortada dice cuántos casos
    quedaron afuera y con qué criterio se eligieron los que están.
    """
    partes: List[str] = []
    seccion = 0

    def titulo(texto: str) -> None:
        nonlocal seccion
        seccion += 1
        partes.append(f"\n### {seccion}. {texto}")

    # 1. Metadatos del Alcance
    empresa = contexto.get("empresa_nombre") or contexto.get("empresa") or "No especificada"
    campana = contexto.get("campana_nombre") or contexto.get("campana") or "No especificada"
    plantilla = contexto.get("plantilla_nombre") or contexto.get("plantilla") or "No especificada"
    desde = contexto.get("fecha_desde") or "Inicio"
    hasta = contexto.get("fecha_hasta") or "Fin"
    base_fecha = contexto.get("base_fecha") or "interaccion"
    filtros = contexto.get("filtros_activos") or {}

    titulo("ALCANCE DE LA CONSULTA")
    partes.append(f"- **Empresa**: {empresa}")
    partes.append(f"- **Campaña**: {campana}")
    partes.append(f"- **Plantilla**: {plantilla}")
    partes.append(f"- **Período**: {desde} al {hasta} (Fecha base: {base_fecha})")
    if filtros:
        filtros_desc = ", ".join(f"{k}: {v}" for k, v in filtros.items() if v)
        if filtros_desc:
            partes.append(f"- **Filtros/Segmentación aplicados**: {filtros_desc}")

    # 2. Resumen Estadístico / KPIs
    kpis = contexto.get("kpis") or {}
    total_filas = kpis.get("total") or len(contexto.get("filas") or [])
    puntaje_prom = kpis.get("puntaje_promedio")
    cant_equipos = kpis.get("cant_equipos")
    cant_operadores = kpis.get("cant_operadores")
    casos_ec = kpis.get("casos_ec")
    pct_ec = kpis.get("pct_ec")

    titulo("KPIs GENERALES DEL DASHBOARD")
    partes.append("_Estos valores están calculados sobre el período COMPLETO. Son la fuente de verdad para cualquier total o porcentaje._")
    partes.append(f"- **Total de auditorías evaluadas**: {total_filas}")
    if cant_equipos is not None:
        partes.append(f"- **Cantidad de equipos**: {cant_equipos}")
    if cant_operadores is not None:
        partes.append(f"- **Cantidad de operadores**: {cant_operadores}")
    if puntaje_prom is not None:
        partes.append(f"- **Puntaje promedio global**: {puntaje_prom}%")
    if casos_ec is not None:
        partes.append(f"- **Auditorías con Error Crítico (EC)**: {casos_ec} ({pct_ec if pct_ec is not None else '—'}%)")

    # 3. Metadatos de Atributos de la Plantilla
    atributos = contexto.get("atributos") or []
    if atributos:
        titulo("CRITERIOS / ATRIBUTOS DE LA PLANTILLA")
        for atr in atributos:
            nom = atr.get("nombre", "Sin nombre")
            tipo = atr.get("tipo", "general")
            polaridad = atr.get("polaridad", "mayor")
            meta = atr.get("meta")
            ayuda = atr.get("ayuda") or ""
            meta_str = f" | Meta: {meta}" if meta is not None else ""
            pol_str = " (Mayor es mejor)" if polaridad == "mayor" else " (Menor es mejor)" if polaridad == "menor" else ""
            desc = f" - {ayuda}" if ayuda else ""
            partes.append(f"- **{nom}** ({tipo}{pol_str}{meta_str}){desc}")

    # 4. Resumen por Operador — recorte por los DOS extremos, declarado
    resumen_operadores = contexto.get("resumen_operadores")
    if resumen_operadores and isinstance(resumen_operadores, list):
        elegidos, nota, corte = _recortar_operadores(resumen_operadores)
        titulo(f"DESEMPEÑO POR ASESOR / OPERADOR (Nombre Completo) — {nota}")
        partes.append("| Asesor / Operador (Nombre Completo) | Supervisor / Equipo | Casos | Puntaje Prom. | Con Error Crítico |")
        partes.append("|---|---|---|---|---|")
        for i, op in enumerate(elegidos):
            # Corte visible entre las dos puntas: el salto de la fila 40 a la 41
            # es un salto de decenas de asesores, y en una tabla corrida no se ve.
            if corte is not None and i == corte:
                faltan = len(resumen_operadores) - len(elegidos)
                partes.append(f"| *(… {faltan} asesores de desempeño intermedio, no listados …)* | | | | |")
            nom = op.get("operador") or op.get("agente") or op.get("nombre") or "Desconocido"
            eq = op.get("equipo") or "Sin equipo"
            casos = op.get("casos", 0)
            pts = f"{op.get('puntaje_promedio')}%" if op.get("puntaje_promedio") is not None else "—"
            ecs = f"{op.get('casos_ec', 0)} ({op.get('pct_ec', 0)}%)"
            partes.append(f"| {nom} | {eq} | {casos} | {pts} | {ecs} |")

    # 5. Resumen por Equipo (van todos: son pocos)
    resumen_equipos = contexto.get("resumen_equipos")
    if resumen_equipos and isinstance(resumen_equipos, list):
        titulo("DESEMPEÑO POR EQUIPO (Supervisor)")
        partes.append("| Equipo | Operadores | Casos | Puntaje Prom. | Con Error Crítico |")
        partes.append("|---|---|---|---|---|")
        for eq in resumen_equipos:
            nom = eq.get("equipo") or "Sin equipo"
            ops = eq.get("cant_operadores", "—")
            casos = eq.get("casos", 0)
            pts = f"{eq.get('puntaje_promedio')}%" if eq.get("puntaje_promedio") is not None else "—"
            ecs = f"{eq.get('casos_ec', 0)} ({eq.get('pct_ec', 0)}%)"
            partes.append(f"| {nom} | {ops} | {casos} | {pts} | {ecs} |")

    # 6. Desglose de cumplimiento por atributo
    distribucion_atributos = contexto.get("distribucion_atributos")
    if distribucion_atributos and isinstance(distribucion_atributos, dict):
        titulo("DISTRIBUCIÓN Y CUMPLIMIENTO POR ATRIBUTO")
        partes.append(f"_Calculado sobre las {total_filas} auditorías del período (no sobre la muestra de llamados individuales)._")
        for atr_nom, stats in distribucion_atributos.items():
            tipo = stats.get("tipo", "")
            valores = stats.get("valores", {})
            partes.append(f"- **{atr_nom}** ({tipo}):")
            if isinstance(valores, dict):
                vals_str = ", ".join(f"{k}: {v.get('count', v)} ({v.get('pct', '')}%)" if isinstance(v, dict) else f"{k}: {v}" for k, v in valores.items())
                partes.append(f"  * Respuestas: {vals_str}")
            if stats.get("promedio") is not None:
                partes.append(f"  * Promedio: {stats.get('promedio')} (Min: {stats.get('min')}, Max: {stats.get('max')})")

    # 7. Detalle de casos con Error Crítico
    casos_ec_detalle = contexto.get("casos_ec_detalle")
    if casos_ec_detalle and isinstance(casos_ec_detalle, list):
        # El total sale de los KPIs, no de len(): la lista que manda el navegador
        # ya puede venir recortada y declararía un total menor al real.
        total_ec = kpis.get("casos_ec") or len(casos_ec_detalle)
        mostrados = casos_ec_detalle[:MAX_CASOS_EC_DETALLE]
        nota = (
            f"listados {len(mostrados)} de {total_ec} — los {total_ec - len(mostrados)} restantes NO están en esta lista"
            if total_ec > len(mostrados) else f"los {total_ec} del período"
        )
        titulo(f"DETALLE DE LLAMADOS CON ERROR CRÍTICO ({nota})")
        for caso in mostrados:
            id_rec = caso.get("IdAplicativo") or caso.get("IdGrabacion") or caso.get("AuditoriaID") or "S/ID"
            op = caso.get("Operador") or "S/Operador"
            eq = caso.get("Equipo") or "S/Equipo"
            fec = caso.get("Fecha") or caso.get("FechaInteraccion") or ""
            motivos = caso.get("motivos_ec") or caso.get("atributos_fallados") or []
            mot_str = f" - Motivos/Fallos: {', '.join(motivos)}" if motivos else ""
            partes.append(f"- Llamada IdAplicativo: `{id_rec}` | {fec} | Asesor: **{op}** (Equipo: {eq}){mot_str}")

    return "\n".join(partes)


def _bloque_de_la_pregunta(
    contexto: Dict[str, Any],
    extra: Optional[Dict[str, Any]] = None,
    *,
    desde_seccion: int = 0,
) -> str:
    """Lo que se trajo de la base para ESTA pregunta (ver `asistente_contexto`).

    Va al final del prompt, pegado a la pregunta, no en el `system_instruction`:
    cambia en cada turno y adelante rompería el prefijo cacheado (ver
    `_bloque_estable`). `desde_seccion` continúa la numeración del bloque estable
    para que el reporte se lea como uno solo.
    """
    partes: List[str] = []
    extra = extra or {}
    seccion = desde_seccion

    def titulo(texto: str) -> None:
        nonlocal seccion
        seccion += 1
        partes.append(f"\n### {seccion}. {texto}")

    kpis = contexto.get("kpis") or {}
    total_filas = kpis.get("total") or len(contexto.get("filas") or [])

    # Comparativa contra el período anterior (si se pudo calcular)
    comparativa = extra.get("comparativa")
    if comparativa:
        titulo("COMPARATIVA CON EL PERÍODO ANTERIOR")
        partes.append(_bloque_comparativa(comparativa, kpis, contexto.get("atributos") or []))

    # Auditorías individuales — la muestra elegida para ESTA pregunta
    filas = extra.get("filas_seleccionadas")
    if filas is None:
        filas = (contexto.get("filas") or [])[:MAX_FILAS_DETALLE]
    if filas and isinstance(filas, list):
        enviadas = len(contexto.get("filas") or [])
        criterios = extra.get("criterios_muestra") or []
        detalle_criterio = f" Priorizadas: {'; '.join(criterios)}." if criterios else ""
        titulo(
            f"AUDITORÍAS INDIVIDUALES (muestra de {len(filas)} sobre {total_filas} del período)"
        )
        partes.append(
            f"_Es una MUESTRA, no el listado completo: el dashboard envió {enviadas} filas y acá "
            f"entran {len(filas)}.{detalle_criterio} No cuentes ni saques porcentajes de esta tabla; "
            f"para eso están los KPIs y los cuadros agregados._"
        )
        partes.append("| IdAplicativo | Asesor (Nombre Completo) | Supervisor / Equipo | Fecha | Puntaje | Error Crítico |")
        partes.append("|---|---|---|---|---|---|")
        for r in filas:
            id_ap = r.get("IdAplicativo") or r.get("IdGrabacion") or r.get("AuditoriaID") or "—"
            op = r.get("Operador") or r.get("Agente") or "—"
            eq = r.get("Equipo") or "—"
            fec = r.get("Fecha") or r.get("fecha_interaccion") or "—"
            pts = f"{r.get('Puntaje')}%" if r.get('Puntaje') is not None else (f"{r.get('PuntajeFinal')}%" if r.get('PuntajeFinal') is not None else "—")
            ec = r.get("EsEC") or r.get("ErrorCritico") or ("Sí" if r.get("EsErrorCritico") or r.get("__esEC") else "No")
            partes.append(f"| {id_ap} | {op} | {eq} | {fec} | {pts} | {ec} |")

    # Transcripciones de los llamados relevantes
    transcripciones = extra.get("transcripciones")
    if transcripciones:
        titulo(f"TRANSCRIPCIONES DE LLAMADOS ({len(transcripciones)})")
        partes.append(
            "_Lo que se dijo, palabra por palabra. Usalas para fundamentar el diagnóstico con citas "
            "textuales breves en vez de deducir el motivo del nombre del criterio._"
        )
        for t in transcripciones:
            cabecera = (
                f"\n#### Llamado IdAplicativo: `{t.get('id_aplicativo')}` — Asesor: **{t.get('operador')}** "
                f"(Equipo: {t.get('equipo')}) | {t.get('fecha') or 's/fecha'}"
            )
            if t.get("puntaje") is not None:
                cabecera += f" | Puntaje: {t['puntaje']}"
            if t.get("duracion_segundos"):
                cabecera += f" | Duración: {t['duracion_segundos']}s"
            partes.append(cabecera)
            partes.append("```")
            partes.append(t.get("texto") or "")
            if t.get("truncada"):
                partes.append("[... transcripción recortada por longitud ...]")
            partes.append("```")

    return "\n".join(partes)


def _recortar_operadores(
    resumen: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], str, Optional[int]]:
    """Recorta el ranking de asesores por los DOS extremos, no por los primeros N.

    La lista llega ordenada de peor a mejor. Quedarse con los primeros 40 dejaba
    afuera justo a los referentes, que son la mitad de lo que se pide (plan de
    coaching = a quién corregir Y de quién copiar). Devuelve (filas, nota) con la
    nota lista para el título de la sección y el índice donde arranca el bloque de
    los mejores (para dibujar el corte en la tabla).
    """
    total = len(resumen)
    if total <= MAX_OPERADORES_PEORES + MAX_OPERADORES_MEJORES:
        return resumen, f"los {total} del período, de menor a mayor puntaje", None

    peores = resumen[:MAX_OPERADORES_PEORES]
    mejores = resumen[-MAX_OPERADORES_MEJORES:]
    ocultos = total - len(peores) - len(mejores)
    nota = (
        f"{len(peores) + len(mejores)} de {total}: los {len(peores)} de MENOR puntaje y los "
        f"{len(mejores)} de MAYOR. Faltan {ocultos} asesores de desempeño intermedio, que NO "
        f"están en esta tabla"
    )
    return peores + mejores, nota, len(peores)


def _flecha(actual: Optional[float], previo: Optional[float], mayor_es_mejor: bool = True) -> str:
    """Variación explícita, ya calculada, para que el modelo no la estime."""
    if actual is None or previo is None:
        return "—"
    delta = round(actual - previo, 1)
    if delta == 0:
        return "=  (sin cambio)"
    mejora = (delta > 0) if mayor_es_mejor else (delta < 0)
    signo = "+" if delta > 0 else ""
    return f"{signo}{delta} pts ({'mejora' if mejora else 'empeora'})"


def _bloque_comparativa(
    prev: Dict[str, Any],
    kpis: Dict[str, Any],
    atributos: List[Dict[str, Any]],
) -> str:
    """Cuadro contra la ventana anterior de igual duración, con las variaciones
    ya calculadas (el modelo solo tiene que leerlas)."""
    lineas = [
        f"_Ventana anterior de la misma duración: **{prev.get('desde')} al {prev.get('hasta')}**._"
    ]
    if not prev.get("total"):
        lineas.append(
            "- **No hay auditorías cargadas en ese período**: no se puede afirmar que se haya "
            "mejorado ni empeorado. Decilo así si te preguntan por la evolución."
        )
        return "\n".join(lineas)

    lineas.append("| Métrica | Período anterior | Período actual | Variación |")
    lineas.append("|---|---|---|---|")
    lineas.append(f"| Auditorías | {prev.get('total')} | {kpis.get('total', '—')} | — |")
    lineas.append(
        f"| Puntaje promedio | {prev.get('puntaje_promedio', '—')}% | {kpis.get('puntaje_promedio', '—')}% | "
        f"{_flecha(kpis.get('puntaje_promedio'), prev.get('puntaje_promedio'))} |"
    )
    lineas.append(
        f"| % Error Crítico | {prev.get('pct_ec', '—')}% | {kpis.get('pct_ec', '—')}% | "
        f"{_flecha(kpis.get('pct_ec'), prev.get('pct_ec'), mayor_es_mejor=False)} |"
    )

    cumplimiento = prev.get("cumplimiento_atributos") or {}
    if cumplimiento:
        lineas.append("\n**Cumplimiento por criterio en el período anterior** (para contrastar con la sección de distribución):")
        for nombre, pct in cumplimiento.items():
            lineas.append(f"- {nombre}: {pct}%")

    equipos = prev.get("equipos") or {}
    if equipos:
        lineas.append("\n**Por equipo en el período anterior**:")
        for eq, d in equipos.items():
            lineas.append(
                f"- {eq}: {d.get('casos')} casos | Puntaje {d.get('puntaje_promedio', '—')}% | EC {d.get('pct_ec')}%"
            )
    return "\n".join(lineas)


def _instruccion_de_sistema(resumen_datos: str) -> str:
    return (
        f"{_SYSTEM_PROMPT}\n\n"
        "===== DATOS Y ESTADÍSTICAS DEL DASHBOARD DE AUDITORÍAS =====\n"
        f"{resumen_datos}\n"
        "===== FIN DE LOS DATOS =====\n"
    )


def _contenidos(
    pregunta: str,
    historial: Optional[List[dict]],
    bloque_de_la_pregunta: str = "",
) -> List[types.Content]:
    """Prepara el historial conversacional asegurando alternancia estricta user <-> model.

    `bloque_de_la_pregunta` (los datos que se trajeron para esta consulta) entra
    en el ÚLTIMO turno, junto a la pregunta. Así lo único que cambia respecto del
    turno anterior queda al final del prompt y todo lo de arriba —instrucción de
    sistema, dataset y turnos previos— es un prefijo idéntico que Gemini puede
    cachear y cobrar más barato.
    """
    contenidos: List[types.Content] = []
    ultimo_rol = None

    # Cada turno son DOS mensajes (pregunta + respuesta): sin el x2 la memoria
    # efectiva era la mitad de la declarada.
    for turno in (historial or [])[-(MAX_TURNOS_HISTORIAL * 2):]:
        texto = (turno.get("texto") or "").strip()
        if not texto:
            continue
        rol = "user" if turno.get("rol") == "user" else "model"

        # Evitar turnos consecutivos del mismo rol
        if rol == ultimo_rol:
            continue

        limite = MAX_PREGUNTA_CHARS if rol == "user" else MAX_HISTORIAL_TURNO_CHARS
        contenidos.append(
            types.Content(role=rol, parts=[types.Part.from_text(text=texto[:limite])])
        )
        ultimo_rol = rol

    # Si el último turno del historial fue 'user', lo retiramos para que la nueva pregunta sea el turno 'user'
    if contenidos and contenidos[-1].role == "user":
        contenidos.pop()

    texto_final = pregunta[:MAX_PREGUNTA_CHARS]
    if bloque_de_la_pregunta:
        texto_final = (
            "===== DATOS TRAÍDOS PARA ESTA PREGUNTA =====\n"
            f"{bloque_de_la_pregunta}\n"
            "===== FIN DE LOS DATOS DE ESTA PREGUNTA =====\n\n"
            f"{texto_final}"
        )
    contenidos.append(types.Content(role="user", parts=[types.Part.from_text(text=texto_final)]))
    return contenidos


def _registrar_consumo(
    usage: Any,
    user_id: Optional[int],
    campana_id: Optional[int] = None,
    conversacion_id: Optional[int] = None,
    extras: Optional[Dict[str, Any]] = None,
) -> None:
    """Registra el consumo de tokens en pagina_web.IA_Uso.

    El `ref_id` lleva la conversación adelante (`dash:<conv>:<rand>`) y `extras`
    guarda el tamaño real del contexto de ESE turno. Sin eso, el gasto del
    asistente es una bolsa de consumos sueltos y no se puede responder la única
    pregunta que importa cuando la factura sube: si lo caro es la cantidad de
    charlas o el peso del contexto que se remanda en cada repregunta.
    """
    try:
        from app.uso_ia import registrar_uso_ia

        sufijo = uuid.uuid4().hex[:12]
        ref = f"dash:{conversacion_id}:{sufijo}" if conversacion_id else f"dash:{sufijo}"
        registrar_uso_ia(
            feature="asistente_dashboard",
            modelo=_modelo(),
            modo="sync",
            user_id=user_id,
            usage=usage,
            ref_id=ref,
            campana_id=campana_id,
            extras=extras or None,
        )
    except Exception:
        logger.exception("No se pudo registrar el consumo de IA del asistente del dashboard.")


def validar(pregunta: str, contexto: Dict[str, Any]) -> Tuple[str, str]:
    """Valida la pregunta y genera la síntesis analítica del contexto."""
    pregunta = (pregunta or "").strip()
    if not pregunta:
        raise ValueError("Escribí una pregunta o pedido para el analista de auditorías.")
    if len(pregunta) > MAX_PREGUNTA_CHARS:
        raise ValueError(f"La consulta es demasiado larga (máximo {MAX_PREGUNTA_CHARS} caracteres).")
    if not isinstance(contexto, dict) or not (contexto.get("filas") or contexto.get("kpis")):
        raise ValueError("No hay datos de auditorías cargados en el dashboard para analizar.")

    resumen_datos = _sintetizar_datos_dashboard(contexto)
    if len(resumen_datos) > MAX_CONTEXTO_CHARS:
        resumen_datos = resumen_datos[:MAX_CONTEXTO_CHARS] + "\n\n... (datos truncados por límite de tamaño)"

    return pregunta, resumen_datos


async def responder_stream(
    pregunta: str,
    contexto: Dict[str, Any],
    user_id: Optional[int] = None,
    campana_id: Optional[int] = None,
    historial: Optional[List[dict]] = None,
    extra: Optional[Dict[str, Any]] = None,
    conversacion_id: Optional[int] = None,
) -> AsyncIterator[str]:
    """Generador asíncrono con streaming de respuesta para el frontend.

    `extra` es lo que el router pudo traer de la base para ESTA pregunta
    (transcripciones, período anterior, la muestra de filas ya elegida por
    relevancia). Ver `asistente_contexto`.
    """
    client = _get_client()
    # El reporte va partido a propósito: el dataset de la pantalla (idéntico en
    # toda la conversación) al system_instruction, y lo que se trajo para esta
    # pregunta al final, con la pregunta. Es lo que hace que la repregunta número
    # 5 no vuelva a pagar el contexto entero: Gemini reconoce el prefijo repetido
    # y lo cobra ~10 veces más barato (ver `_bloque_estable`).
    bloque_estable = _bloque_estable(contexto)
    bloque_pregunta = _bloque_de_la_pregunta(
        contexto, extra, desde_seccion=_ultima_seccion(bloque_estable)
    )

    config_kwargs: Dict[str, Any] = {
        "system_instruction": _instruccion_de_sistema(bloque_estable),
        "temperature": 0.2,
        "safety_settings": _SAFETY_SETTINGS,
    }

    t_config = _thinking_config()
    if t_config is not None:
        config_kwargs["thinking_config"] = t_config

    config = types.GenerateContentConfig(**config_kwargs)

    usage = None
    try:
        stream = await client.aio.models.generate_content_stream(
            model=_modelo(),
            contents=_contenidos(pregunta, historial, bloque_pregunta),
            config=config,
        )
        async for chunk in stream:
            if getattr(chunk, "usage_metadata", None):
                usage = chunk.usage_metadata
            texto = getattr(chunk, "text", None)
            if texto:
                yield texto
    except Exception:
        logger.exception("Error al consultar el Asistente Analítico del Dashboard.")
        yield (
            "\n\nOcurrió un error al procesar el análisis con el servicio de IA. "
            "Por favor probá formular la pregunta nuevamente en unos momentos."
        )
    finally:
        if usage is not None:
            extra = extra or {}
            _registrar_consumo(
                usage, user_id, campana_id, conversacion_id,
                extras={
                    "contexto_chars": len(bloque_estable) + len(bloque_pregunta),
                    # Abierto en dos: el estable es lo que se cachea entre turnos
                    # y el de la pregunta es lo que se paga entero siempre. Si el
                    # gasto sube, esto dice cuál de los dos creció.
                    "contexto_estable_chars": len(bloque_estable),
                    "contexto_pregunta_chars": len(bloque_pregunta),
                    "cacheados": getattr(usage, "cached_content_token_count", None),
                    "turnos_historial": len(historial or []),
                    "filas_muestra": len(extra.get("filas_seleccionadas") or []),
                    "transcripciones": len(extra.get("transcripciones") or []),
                    "comparativa": bool(extra.get("comparativa")),
                },
            )


async def ejecutar_trabajo_asistente(
    job_id: str,
    pregunta: str,
    contexto: Dict[str, Any],
    alcance: Optional[Dict[str, Any]],
    current_user: Any,
    conv_id: Optional[int],
    campana_id: Optional[int],
    historial: List[dict],
    enriquecer_fn: Callable[..., Dict[str, Any]],
) -> None:
    """Ejecuta el trabajo del asistente en segundo plano y actualiza el estado en disco."""
    try:
        asistente_jobs.actualizar_trabajo(job_id, fase=asistente_jobs.FASE_ENRIQUECIENDO)
        extra = await asyncio.to_thread(
            enriquecer_fn, pregunta, contexto, alcance, current_user
        )
        fuentes = {
            "transcripciones": len(extra.get("transcripciones") or []),
            "comparativa": bool(extra.get("comparativa")),
            "filas": len(extra.get("filas_seleccionadas") or []),
        }
        asistente_jobs.actualizar_trabajo(
            job_id,
            fase=asistente_jobs.FASE_PENSANDO,
            fuentes=fuentes,
        )

        partes: List[str] = []
        ultimo_guardado = time.time()
        primero = True

        async for chunk in responder_stream(
            pregunta=pregunta,
            contexto=contexto,
            user_id=current_user.usuario,
            campana_id=campana_id,
            historial=historial,
            extra=extra,
            conversacion_id=conv_id,
        ):
            if primero:
                asistente_jobs.actualizar_trabajo(job_id, fase=asistente_jobs.FASE_ESCRIBIENDO)
                primero = False
            partes.append(chunk)
            ahora = time.time()
            if ahora - ultimo_guardado >= 0.5:
                asistente_jobs.actualizar_trabajo(job_id, texto="".join(partes))
                ultimo_guardado = ahora

        texto_final = "".join(partes).strip()
        if conv_id and texto_final:
            asistente_conversaciones.agregar_mensaje(conv_id, "bot", texto_final)

        asistente_jobs.actualizar_trabajo(
            job_id,
            estado=asistente_jobs.ESTADO_LISTO,
            texto=texto_final,
            terminado=time.time(),
        )
        logger.info("Trabajo de asistente %s completado con éxito.", job_id[:8])
    except Exception as e:
        logger.exception("Error al ejecutar trabajo de asistente %s: %s", job_id[:8], e)
        asistente_jobs.actualizar_trabajo(
            job_id,
            estado=asistente_jobs.ESTADO_ERROR,
            error=str(e),
            terminado=time.time(),
        )

