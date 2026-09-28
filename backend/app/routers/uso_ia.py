"""Tablero unificado de IA: gastos + log de corridas de auditoría.

Dos fuentes, un solo router (antes estaban repartidas entre este archivo y
app/routers/auditoria.py, con lógica de costeo duplicada):

- `pagina_web.vw_IA_Uso_Costos` (libro de consumo + tarifas vigentes): agregados
  de costo/tokens por mes, feature, modelo, modo, campaña y usuario, más el
  bloque de análisis (costo unitario por modelo/campaña, ahorro batch).
- `calidad.AuditExecutionLog`: listado corrida por corrida ("solicitudes"), con
  el costo estimado por fila usando el modelo REAL guardado en la corrida
  (columna `modelo`; para filas viejas cae al modelo configurado hoy en la
  plantilla) y las mismas funciones de tarifa que usan los mails de log
  (AuditorIA/execution_log.py::obtener_tarifa/calcular_costo_usd).

Permisos: SOLO `uso_ia.view` (pedido explícito 2026-07-02: los supervisores de
calidad ya no acceden — la información de gasto es de Finanzas/Gerencia).

El grupo `chatbot` alimenta su propia pantalla (es el único consumo no
relacionado con auditorías); `auditorias` agrupa el resto (auditoría,
transcripción y asistente de plantillas). Dentro de chatbot, cada chatbot se
identifica por `extras.effective_campana` de `pagina_web.IA_Uso` (ChatVoltara,
CSV, Paygo, y los que se agreguen a futuro: el desglose sale de los datos,
no de una lista fija).

También acá: presupuesto mensual con alertas por umbral de porcentaje
(GET/POST /uso-ia/presupuesto, lógica en app/presupuesto_ia.py).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text, bindparam

from app.chatbot_config import titulos_por_archivo
from app.database import engine
from app.dependencies import RoleChecker
from app.managers import plantillas_manager_instance
from app.models import PresupuestoIARequest, User
from app.presupuesto_ia import estado_presupuesto, guardar_config, parsear_umbrales
from AuditorIA.execution_log import calcular_costo_usd, obtener_tarifa, select_cached_tokens
from AuditorIA.modelos_ia import MODELO_IA_DEFAULT

logger = logging.getLogger(__name__)

PERM_VIEW = "uso_ia.view"          # Gastos y Logs de IA (grupo auditorias, logs, presupuesto)
PERM_CHATBOT = "uso_ia.chatbot"    # Gastos Chatbot (grupo chatbot, con costos) — desde 2026-07-13
PERM_CHATBOT_SOLIC = "chatbot.solicitudes"  # Solo las consultas del chatbot, SIN costos — desde 2026-07-24
require_view = RoleChecker([PERM_VIEW])
# Las Solicitudes del chatbot las ve Calidad (chatbot.solicitudes) o quien tiene
# el tablero completo (uso_ia.chatbot). Los costos/tokens se ocultan a quien no
# tiene uso_ia.chatbot (ver _puede_ver_costos).
require_chatbot_logs = RoleChecker([PERM_CHATBOT, PERM_CHATBOT_SOLIC])
# Entrada al dashboard compartido: cualquiera de los dos; el permiso concreto
# se valida por grupo dentro del endpoint (_exigir_grupo).
require_alguna_vista = RoleChecker([PERM_VIEW, PERM_CHATBOT])

# Zona horaria de Argentina (UTC-3, sin DST). Se usa para serializar fechas que
# ya vienen en hora local (query_chatbots_logs.fecha = getdate() del server ARG),
# adjuntándoles el offset explícito para que el navegador no las reinterprete.
_TZ_ART = timezone(timedelta(hours=-3))

router = APIRouter(prefix="/uso-ia", tags=["UsoIA"])


def _exigir_grupo(user: User, grupo) -> None:
    """El dashboard sirve a dos páginas con permisos distintos: grupo=chatbot
    exige uso_ia.chatbot, grupo=auditorias exige uso_ia.view, y sin grupo
    (devuelve TODO el consumo) exige ambos."""
    if user.is_super_admin:
        return
    requeridos = {"chatbot": {PERM_CHATBOT}, "auditorias": {PERM_VIEW}}.get(grupo, {PERM_VIEW, PERM_CHATBOT})
    faltan = requeridos - set(user.permissions)
    if faltan:
        raise HTTPException(status_code=403, detail=f"Falta el permiso {sorted(faltan)} para ese grupo de datos.")

# Filtro de fecha común a todas las consultas (hasta es inclusive => +1 día).
_FILTRO_FECHA = "v.fecha >= :desde AND v.fecha < :hasta_next"


def _rows(conn, sql: str, params: dict) -> list[dict]:
    return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]


def _filtro_grupo(grupo: Optional[str]) -> str:
    """Fragmento extra del WHERE según el grupo pedido. El chatbot es el único
    consumo no relacionado con auditorías, por eso tiene pantalla propia."""
    if grupo == "chatbot":
        return " AND v.feature = 'chatbot'"
    if grupo == "auditorias":
        return " AND v.feature <> 'chatbot'"
    return ""


# Qué chatbot es cada consulta (ChatVoltara, CSV, Paygo, ...): el nombre viaja
# como texto en extras.effective_campana de pagina_web.IA_Uso (no es un
# campana_id; ver chatBot.py::_log_query_details y el backfill 2026-06-25).
# Requiere JOIN con IA_Uso porque la vista de costos no expone extras.
_JOIN_IA_USO = "JOIN pagina_web.IA_Uso u ON u.id = v.id"
_EXPR_CHATBOT = ("COALESCE(CASE WHEN ISJSON(u.extras) = 1 "
                 "THEN JSON_VALUE(u.extras, '$.effective_campana') END, 'Sin identificar')")


def _buscar_documentos_por_nombre(termino: str) -> list[str]:
    """Documentos cuyos nombre+apellido en nómina contienen el término (para el
    filtro de usuario del listado de solicitudes). Best-effort: si la vista no
    está disponible, devuelve [] (el endpoint lo muestra como "sin resultados")."""
    try:
        with engine.connect() as conn:
            q = text("""
                SELECT TOP 200 documento
                FROM chatbot.vw_nomina
                WHERE LTRIM(RTRIM(nombre + ' ' + apellido)) LIKE :q
            """)
            return [str(r[0]) for r in conn.execute(q, {"q": f"%{termino}%"}).fetchall()]
    except Exception as e:
        logger.warning(f"No se pudo buscar '{termino}' en nómina: {e}")
        return []


def _cond_usuario(usuario: str, col: str) -> str:
    """Fragmento WHERE (sin el AND) para segmentar por usuario: documento exacto
    (numérico) o parte del nombre (se resuelven los documentos en nómina). Los
    documentos se validan como int y se inlinean en la SQL — es seguro (no hay
    inyección posible con enteros) y evita arrastrar bindparams `expanding` a las
    ~12 consultas del dashboard que comparten el mismo WHERE. Sin coincidencias
    por nombre => '1 = 0' (resultado vacío explícito, no "sin filtro")."""
    usuario = (usuario or "").strip()
    if not usuario:
        return "1 = 1"
    if usuario.isdigit():
        return f"{col} = '{int(usuario)}'"
    docs = [int(d) for d in _buscar_documentos_por_nombre(usuario) if str(d).isdigit()]
    if not docs:
        return "1 = 0"
    return f"{col} IN ({','.join(repr(str(d)) for d in docs)})"


def _con_tz_art(dt):
    """query_chatbots_logs.fecha se graba con getdate() (hora local ARG) y pyodbc
    la devuelve naive; se le pega el offset -03:00 para que el navegador no la
    reinterprete según SU zona horaria (mismo problema que _con_tz_utc pero la
    fuente ya está en local, no en UTC)."""
    if dt is None:
        return None
    return dt.replace(tzinfo=_TZ_ART) if dt.tzinfo is None else dt


# El contexto del chatbot se guarda como texto con una estructura fija (ver
# chatBot.py::stream_query paso 7 / query): "Q Original: ...\n\nFuentes:\n"
# seguido de bloques "--- Archivo: <archivo> (Score: <score>) ---\nContenido:\n<texto>".
# Este regex separa cada fuente para poder mostrarlas una por una en el detalle.
_CTX_FUENTE_RE = re.compile(
    r"--- Archivo:\s*(?P<archivo>.*?)\s*\(Score:\s*(?P<score>-?[\d.]+)\)\s*---\s*"
    r"(?:Contenido:\s*)?(?P<contenido>.*?)(?=\n--- Archivo:|\Z)",
    re.DOTALL,
)


def _parsear_contexto(context: Optional[str]) -> list[dict]:
    """Lista de fuentes {archivo, score, contenido} recuperadas por el RAG para
    esa consulta. Best-effort: si el texto no matchea el formato esperado, se
    devuelve [] y el detalle muestra el contexto crudo."""
    if not context:
        return []
    fuentes = []
    for m in _CTX_FUENTE_RE.finditer(context):
        try:
            score = float(m.group("score"))
        except (TypeError, ValueError):
            score = None
        fuentes.append({
            "archivo": (m.group("archivo") or "").strip(),
            "score": score,
            "contenido": (m.group("contenido") or "").strip(),
        })
    return fuentes


# Desglose sync/batch reusable en cualquier GROUP BY de la vista de costos: el
# batch cuesta exactamente la mitad por token, así que casi todo agregado de
# costo se lee mal si no se abre por modo (lo completa _promedios_por_modo).
_DESGLOSE_MODO = """
                       SUM(CASE WHEN v.modo = 'batch' THEN 1 ELSE 0 END)           AS llamadas_batch,
                       SUM(CASE WHEN v.modo = 'batch' THEN v.costo_usd ELSE 0 END) AS costo_batch_usd,
                       SUM(CASE WHEN v.modo <> 'batch' THEN 1 ELSE 0 END)          AS llamadas_sync,
                       SUM(CASE WHEN v.modo <> 'batch' THEN v.costo_usd ELSE 0 END) AS costo_sync_usd"""

# Tokens abiertos por tipo: entrada, salida y "razonamiento" (thoughts). El
# total es la suma de los tres, pero cada uno tiene tarifa propia y explica de
# dónde sale el gasto de cada uso (ver el detalle por uso del Resumen).
_DESGLOSE_TOKENS = """
                       COALESCE(SUM(v.input_tokens), 0)    AS input_tokens,
                       COALESCE(SUM(v.output_tokens), 0)   AS output_tokens,
                       COALESCE(SUM(v.thoughts_tokens), 0) AS thoughts_tokens"""


def _promedios_por_modo(rows: list[dict]) -> None:
    """Completa costo_prom_sync_usd / costo_prom_batch_usd sobre filas que traen
    llamadas_{modo} y costo_{modo}_usd. El promedio se calcula POR MODO porque el
    batch cuesta exactamente la mitad por token: mezclarlos da un número que no
    es el precio verdadero de ninguno. None cuando el modo no tiene llamadas."""
    for r in rows:
        for modo in ("sync", "batch"):
            llamadas = r.get(f"llamadas_{modo}") or 0
            costo = float(r.get(f"costo_{modo}_usd") or 0)
            r[f"costo_prom_{modo}_usd"] = (costo / llamadas) if llamadas else None


def _resolver_nombres_usuarios(conn, user_ids: set[str]) -> dict[str, str]:
    """documento -> 'Nombre Apellido' vía chatbot.vw_nomina (misma vista maestra
    de agentes que usa chatbot_sql_service.py). Best-effort: si la vista no está
    disponible en un ambiente, devuelve {} y las filas quedan con el documento."""
    if not user_ids:
        return {}
    try:
        q = text("""
            SELECT documento, LTRIM(RTRIM(nombre + ' ' + apellido)) AS nombre_completo
            FROM chatbot.vw_nomina WHERE documento IN :ids
        """).bindparams(bindparam("ids", expanding=True))
        return {str(doc): nombre for doc, nombre in conn.execute(q, {"ids": list(user_ids)}).fetchall()}
    except Exception as e:
        logger.warning(f"No se pudieron resolver nombres de usuario: {e}")
        return {}


# -- Resolución de nombres de dimensiones (empresa/campaña/plantilla) ----------
# AuditExecutionLog guarda IDs; estos helpers los traducen a nombre para mostrar.
# Los usan tanto el listado de solicitudes (_enriquecer_logs_ejecucion) como el
# tablero de actividad. Best-effort: si una tabla/vista no está en un ambiente,
# devuelven {} y las filas quedan con el ID (no rompe el endpoint).

def _nombres_empresas(conn, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    try:
        q = text("SELECT EmpresaID, Nombre FROM calidad.Empresas WHERE EmpresaID IN :ids") \
            .bindparams(bindparam("ids", expanding=True))
        return {str(eid): nombre for eid, nombre in conn.execute(q, {"ids": [int(x) for x in ids]}).fetchall()}
    except Exception as e:
        logger.warning(f"No se pudieron resolver nombres de empresa: {e}")
        return {}


def _nombres_campanas(conn, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    try:
        q = text("SELECT CampanaID, Nombre FROM calidad.Campanas WHERE CampanaID IN :ids") \
            .bindparams(bindparam("ids", expanding=True))
        return {str(cid): nombre for cid, nombre in conn.execute(q, {"ids": [int(x) for x in ids]}).fetchall()}
    except Exception as e:
        logger.warning(f"No se pudieron resolver nombres de campaña: {e}")
        return {}


def _nombres_plantillas(conn, ids: set) -> tuple[dict, dict]:
    """(nombres, modelos) por PlantillaID (clave int). Se lee calidad.Plantillas
    directo y SIN filtrar IsActive a propósito: los logs son históricos y muchas
    plantillas ya se dieron de baja; igual queremos mostrar su nombre. (No se usa
    obtener_plantilla()/sp_ObtenerPlantillaCompleta porque ese SP hace RAISERROR
    para plantillas inactivas, lo que rompía la resolución y llenaba el log.)"""
    nombres, modelos = {}, {}
    if not ids:
        return nombres, modelos
    try:
        q = text("SELECT PlantillaID, Nombre, ModeloIA FROM calidad.Plantillas WHERE PlantillaID IN :ids") \
            .bindparams(bindparam("ids", expanding=True))
        for pid, nombre, modelo in conn.execute(q, {"ids": [int(x) for x in ids]}).fetchall():
            nombres[int(pid)] = nombre
            modelos[int(pid)] = modelo or MODELO_IA_DEFAULT
    except Exception as e:
        logger.warning(f"No se pudieron resolver nombres de plantilla: {e}")
    return nombres, modelos


# =========================================================
#   DASHBOARD DE GASTOS (pagina_web.vw_IA_Uso_Costos)
# =========================================================

@router.get("/dashboard")
def dashboard(
    desde: Optional[date] = Query(None, description="Inicio del rango (default: hace 12 meses)."),
    hasta: Optional[date] = Query(None, description="Fin del rango, inclusive (default: hoy)."),
    grupo: Optional[str] = Query(None, pattern="^(auditorias|chatbot)$",
                                 description="auditorias = todo menos chatbot; chatbot = solo chatbot."),
    chatbot: Optional[str] = Query(None, max_length=100,
                                   description="Solo con grupo=chatbot: filtra a UN chatbot (effective_campana)."),
    empresa_id: Optional[int] = Query(None, description="Segmentador: filtra a una empresa (EmpresaID)."),
    campana_id: Optional[int] = Query(None, description="Segmentador: filtra a una campaña (CampanaID)."),
    modelo: Optional[str] = Query(None, max_length=80, description="Segmentador: filtra a un modelo de Gemini."),
    modo: Optional[str] = Query(None, pattern="^(sync|batch|flex)$", description="Segmentador: sync | batch | flex (batch y flex valen la mitad que sync)."),
    usuario: Optional[str] = Query(None, max_length=100, description="Segmentador: documento exacto o parte del nombre."),
    current_user: User = Depends(require_alguna_vista),
):
    """Agregados de costo (USD) y tokens del consumo de IA en el rango.

    Segmentadores (empresa/campaña/modelo/modo/usuario): condiciones opcionales
    que acotan TODAS las agregaciones (Resumen y Análisis) a un subconjunto."""
    _exigir_grupo(current_user, grupo)
    hasta = hasta or date.today()
    desde = desde or (hasta - timedelta(days=365))
    params = {"desde": desde, "hasta_next": hasta + timedelta(days=1)}
    filtro = _FILTRO_FECHA + _filtro_grupo(grupo)

    # Segmentadores: un mismo fragmento WHERE que se agrega a filtro (Resumen) y a
    # filtro_unit (Análisis) para que ambas vistas queden acotadas igual. Todas
    # son columnas de la vista; usuario se resuelve con _cond_usuario (doc/nombre).
    seg = ""
    if empresa_id is not None:
        seg += " AND v.empresa_id = :empresa_id"; params["empresa_id"] = empresa_id
    if campana_id is not None:
        seg += " AND v.campana_id = :campana_id"; params["campana_id"] = campana_id
    if modelo:
        seg += " AND v.modelo = :modelo"; params["modelo"] = modelo
    if modo:
        seg += " AND v.modo = :modo"; params["modo"] = modo
    if usuario:
        seg += " AND " + _cond_usuario(usuario, "v.user_id")
    filtro += seg

    # En el grupo chatbot todas las consultas llevan el JOIN con IA_Uso para
    # poder desglosar/filtrar por chatbot (extras.effective_campana).
    join_extra = ""
    if grupo == "chatbot":
        join_extra = _JOIN_IA_USO
        if chatbot:
            filtro += f" AND {_EXPR_CHATBOT} = :chatbot"
            params["chatbot"] = chatbot

    # "Costo unitario": qué cuesta cada análisis individual. Para auditorías, 1
    # llamada = 1 auditoría (feature 'auditoria' pura, sin mezclar transcripción/
    # asistente); para chatbot, 1 llamada = 1 consulta.
    feature_unitaria = "chatbot" if grupo == "chatbot" else "auditoria"
    params_unit = {**params, "feature_unitaria": feature_unitaria}
    filtro_unit = _FILTRO_FECHA + " AND v.feature = :feature_unitaria" + seg
    if grupo == "chatbot" and chatbot:
        filtro_unit += f" AND {_EXPR_CHATBOT} = :chatbot"

    try:
        with engine.connect() as conn:
            total = _rows(conn, f"""
                SELECT
                    COUNT(*)                  AS llamadas,
                    COALESCE(SUM(v.costo_usd), 0)    AS costo_usd,
                    COALESCE(SUM(v.total_tokens), 0) AS total_tokens
                FROM pagina_web.vw_IA_Uso_Costos v {join_extra}
                WHERE {filtro}
            """, params)

            por_mes = _rows(conn, f"""
                SELECT v.anio_mes,
                       COUNT(*) AS llamadas,
                       SUM(v.costo_usd) AS costo_usd,
                       SUM(v.total_tokens) AS total_tokens
                FROM pagina_web.vw_IA_Uso_Costos v {join_extra}
                WHERE {filtro}
                GROUP BY v.anio_mes
                ORDER BY v.anio_mes
            """, params)

            # Mes × modelo: alimenta el gráfico apilado que muestra cómo se
            # reparte el gasto mensual entre modelos (y el efecto de un cambio
            # de modelo/tarifa en el tiempo).
            por_mes_modelo = _rows(conn, f"""
                SELECT v.anio_mes, v.modelo,
                       COUNT(*) AS llamadas,
                       SUM(v.costo_usd) AS costo_usd
                FROM pagina_web.vw_IA_Uso_Costos v {join_extra}
                WHERE {filtro}
                GROUP BY v.anio_mes, v.modelo
                ORDER BY v.anio_mes, v.modelo
            """, params)

            # Costo por uso (feature): el desglose principal del Resumen. Va
            # completo — tokens abiertos por tipo y sync/batch — porque es la
            # tabla que responde "en qué se nos va la plata" (pedido 2026-08-20:
            # el doughnut solo no alcanzaba).
            por_feature = _rows(conn, f"""
                SELECT v.feature,
                       COUNT(*) AS llamadas,
                       SUM(v.costo_usd) AS costo_usd,
                       SUM(v.total_tokens) AS total_tokens,
                       {_DESGLOSE_TOKENS},
                       {_DESGLOSE_MODO}
                FROM pagina_web.vw_IA_Uso_Costos v {join_extra}
                WHERE {filtro}
                GROUP BY v.feature
                ORDER BY costo_usd DESC
            """, params)
            _promedios_por_modo(por_feature)

            # Uso × modelo: filas hijas desplegables del detalle por uso (qué
            # modelo consume cada uso; un mismo uso puede repartirse entre
            # varios cuando se cambió el modelo de una plantilla a mitad de mes).
            por_feature_modelo = _rows(conn, f"""
                SELECT v.feature, v.modelo,
                       COUNT(*) AS llamadas,
                       SUM(v.costo_usd) AS costo_usd,
                       SUM(v.total_tokens) AS total_tokens,
                       {_DESGLOSE_TOKENS},
                       {_DESGLOSE_MODO}
                FROM pagina_web.vw_IA_Uso_Costos v {join_extra}
                WHERE {filtro}
                GROUP BY v.feature, v.modelo
                ORDER BY costo_usd DESC
            """, params)
            _promedios_por_modo(por_feature_modelo)

            # Mes × uso: alimenta la vista "por uso" del gráfico mensual apilado
            # (la otra es por modelo), para ver la evolución de cada uso y no
            # solo su total del rango.
            por_mes_feature = _rows(conn, f"""
                SELECT v.anio_mes, v.feature,
                       COUNT(*) AS llamadas,
                       SUM(v.costo_usd) AS costo_usd
                FROM pagina_web.vw_IA_Uso_Costos v {join_extra}
                WHERE {filtro}
                GROUP BY v.anio_mes, v.feature
                ORDER BY v.anio_mes, v.feature
            """, params)

            por_modelo = _rows(conn, f"""
                SELECT v.modelo,
                       COUNT(*) AS llamadas,
                       SUM(v.costo_usd) AS costo_usd,
                       SUM(v.total_tokens) AS total_tokens
                FROM pagina_web.vw_IA_Uso_Costos v {join_extra}
                WHERE {filtro}
                GROUP BY v.modelo
                ORDER BY costo_usd DESC
            """, params)

            por_modo = _rows(conn, f"""
                SELECT v.modo,
                       COUNT(*) AS llamadas,
                       SUM(v.costo_usd) AS costo_usd
                FROM pagina_web.vw_IA_Uso_Costos v {join_extra}
                WHERE {filtro}
                GROUP BY v.modo
                ORDER BY costo_usd DESC
            """, params)

            # Top campañas por costo (auditoría/transcripción). Chatbot/asistente no tienen
            # campana_id => caen en 'Sin campaña'.
            por_campana = _rows(conn, f"""
                SELECT TOP 20
                       COALESCE(c.Nombre, 'Sin campaña') AS campana,
                       COUNT(*) AS llamadas,
                       SUM(v.costo_usd) AS costo_usd,
                       SUM(v.total_tokens) AS total_tokens
                FROM pagina_web.vw_IA_Uso_Costos v {join_extra}
                LEFT JOIN calidad.Campanas c ON v.campana_id = c.CampanaID
                WHERE {filtro}
                GROUP BY COALESCE(c.Nombre, 'Sin campaña')
                ORDER BY costo_usd DESC
            """, params)

            # Quién consume: top usuarios por costo (documento -> nombre de nómina).
            por_usuario = _rows(conn, f"""
                SELECT TOP 10
                       v.user_id,
                       COUNT(*) AS llamadas,
                       SUM(v.costo_usd) AS costo_usd,
                       SUM(v.total_tokens) AS total_tokens
                FROM pagina_web.vw_IA_Uso_Costos v {join_extra}
                WHERE {filtro} AND v.user_id IS NOT NULL
                GROUP BY v.user_id
                ORDER BY costo_usd DESC
            """, params)
            nombres = _resolver_nombres_usuarios(conn, {str(r["user_id"]) for r in por_usuario})
            for r in por_usuario:
                r["user_nombre"] = nombres.get(str(r["user_id"]))

            # Desglose por chatbot (solo grupo=chatbot): ChatVoltara, CSV, Paygo,
            # y cualquiera que se agregue a futuro — los nombres salen de los
            # datos (extras.effective_campana), no de una lista fija.
            por_chatbot = []
            if grupo == "chatbot":
                por_chatbot = _rows(conn, f"""
                    SELECT {_EXPR_CHATBOT} AS chatbot,
                           COUNT(*) AS llamadas,
                           SUM(v.costo_usd) AS costo_usd,
                           SUM(v.total_tokens) AS total_tokens
                    FROM pagina_web.vw_IA_Uso_Costos v {join_extra}
                    WHERE {filtro}
                    GROUP BY {_EXPR_CHATBOT}
                    ORDER BY costo_usd DESC
                """, params)

            # --- Análisis de costo unitario -------------------------------------
            # ¿Cuánto cuesta cada auditoría (o consulta de chatbot) con cada
            # modelo / en cada campaña? El promedio se desglosa POR MODO
            # (sync/batch): el batch cuesta exactamente la mitad por token, así
            # que un promedio mezclado no es el precio verdadero de ninguno de
            # los dos (pedido 2026-07-02). Los promedios los arma
            # _promedios_por_modo en Python (división segura cuando un modo no
            # tiene llamadas).
            unitario_modelo = _rows(conn, f"""
                SELECT v.modelo,
                       COUNT(*) AS llamadas,
                       SUM(v.costo_usd) AS costo_usd,
                       SUM(v.total_tokens) / COUNT(*)  AS tokens_prom,
                       {_DESGLOSE_MODO}
                FROM pagina_web.vw_IA_Uso_Costos v {join_extra}
                WHERE {filtro_unit}
                GROUP BY v.modelo
                ORDER BY costo_usd DESC
            """, params_unit)
            _promedios_por_modo(unitario_modelo)

            unitario_campana = _rows(conn, f"""
                SELECT TOP 20
                       COALESCE(c.Nombre, 'Sin campaña') AS campana,
                       COUNT(*) AS llamadas,
                       SUM(v.costo_usd) AS costo_usd,
                       SUM(v.total_tokens) / COUNT(*)  AS tokens_prom,
                       {_DESGLOSE_MODO}
                FROM pagina_web.vw_IA_Uso_Costos v {join_extra}
                LEFT JOIN calidad.Campanas c ON v.campana_id = c.CampanaID
                WHERE {filtro_unit}
                GROUP BY COALESCE(c.Nombre, 'Sin campaña')
                ORDER BY costo_usd DESC
            """, params_unit)
            _promedios_por_modo(unitario_campana)

            # Costo por auditoría según el NIVEL DE RAZONAMIENTO de la plantilla.
            # Solo tiene sentido para auditorías (el chatbot no usa niveles) y sale
            # de AuditExecutionLog, que es donde vive el nivel.
            unitario_nivel = []
            if grupo != "chatbot":
                unitario_nivel = _unitario_por_nivel(
                    conn, desde=desde, hasta_next=hasta + timedelta(days=1),
                    empresa_id=empresa_id, campana_id=campana_id, modelo=modelo, modo=modo,
                )

            # Ahorro por Batch API: la vista ya aplica el -50%, así que lo pagado
            # en batch ES el ahorro (a precio sync hubiera costado el doble).
            ahorro_batch = conn.execute(text(f"""
                SELECT COALESCE(SUM(v.costo_usd), 0)
                FROM pagina_web.vw_IA_Uso_Costos v {join_extra}
                WHERE {filtro} AND v.modo = 'batch'
            """), params).scalar()

            # --- Uso temporal (solo chatbot) -----------------------------------
            # El chatbot es consumo interactivo de operadores: interesa el patrón
            # de uso en el tiempo (diario, por hora y por día de semana), no solo
            # el costo. fecha es UTC => se corre -3h para que el día/hora sean los
            # de Argentina (sin DST). Para auditorías el detalle temporal sale de
            # calidad.AuditExecutionLog en /uso-ia/actividad (tiene volumen real
            # por corrida), así que acá se calcula solo para chatbot.
            _LOCAL = "DATEADD(HOUR, -3, v.fecha)"
            por_dia = por_hora = por_dia_semana = []
            if grupo == "chatbot":
                por_dia = _rows(conn, f"""
                    SELECT CONVERT(CHAR(10), {_LOCAL}, 23) AS dia,
                           COUNT(*) AS llamadas,
                           SUM(v.costo_usd) AS costo_usd,
                           SUM(v.total_tokens) AS total_tokens
                    FROM pagina_web.vw_IA_Uso_Costos v {join_extra}
                    WHERE {filtro}
                    GROUP BY CONVERT(CHAR(10), {_LOCAL}, 23)
                    ORDER BY dia
                """, params)
                por_hora = _rows(conn, f"""
                    SELECT DATEPART(HOUR, {_LOCAL}) AS hora, COUNT(*) AS llamadas
                    FROM pagina_web.vw_IA_Uso_Costos v {join_extra}
                    WHERE {filtro}
                    GROUP BY DATEPART(HOUR, {_LOCAL})
                    ORDER BY hora
                """, params)
                # 0=lunes .. 6=domingo, independiente del DATEFIRST/idioma del
                # server: se cuenta días desde 1900-01-01 (que fue lunes) mód 7.
                _DOW = f"(DATEDIFF(DAY, '19000101', CAST({_LOCAL} AS DATE)) % 7)"
                por_dia_semana = _rows(conn, f"""
                    SELECT {_DOW} AS dow, COUNT(*) AS llamadas
                    FROM pagina_web.vw_IA_Uso_Costos v {join_extra}
                    WHERE {filtro}
                    GROUP BY {_DOW}
                    ORDER BY dow
                """, params)

        return {
            "rango": {"desde": desde.isoformat(), "hasta": hasta.isoformat()},
            "grupo": grupo,
            "total": total[0] if total else {"llamadas": 0, "costo_usd": 0, "total_tokens": 0},
            "por_mes": por_mes,
            "por_mes_modelo": por_mes_modelo,
            "por_mes_feature": por_mes_feature,
            "por_feature": por_feature,
            "por_feature_modelo": por_feature_modelo,
            "por_modelo": por_modelo,
            "por_modo": por_modo,
            "por_campana": por_campana,
            "por_usuario": por_usuario,
            "por_chatbot": por_chatbot,
            "por_dia": por_dia,
            "por_hora": por_hora,
            "por_dia_semana": por_dia_semana,
            "analisis": {
                "feature_unitaria": feature_unitaria,
                "unitario_modelo": unitario_modelo,
                "unitario_nivel": unitario_nivel,
                "unitario_campana": unitario_campana,
                "ahorro_batch_usd": float(ahorro_batch or 0),
            },
        }

    except Exception as e:
        logger.exception("Error en /uso-ia/dashboard")
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================
#   ACTIVIDAD DE AUDITORÍAS (calidad.AuditExecutionLog)
# =========================================================
# El dashboard de arriba mira el gasto (tokens/USD del libro IA_Uso). Esta vista
# mira la OPERACIÓN: cuántas auditorías se corren, sobre qué campañas/empresas,
# quién las dispara, con qué plantilla, manual vs programada, tasa de éxito y el
# patrón temporal. La fuente es AuditExecutionLog (1 fila = 1 corrida): `cantidad`
# de una llamada a Gemini no sirve acá porque una corrida audita muchas
# interacciones (filas_auditadas). Solo aplica a auditorías (esta tabla es
# específica de AuditorIA), por eso vive detrás de uso_ia.view.

# started_at se guarda en UTC (naive); se corre -3h para agrupar por día/hora de
# Argentina (sin DST). Se usa en varias consultas de este endpoint.
_LOCAL_AUDIT = "DATEADD(HOUR, -3, started_at)"


@router.get("/actividad")
def actividad(
    desde: Optional[date] = Query(None, description="Inicio del rango por started_at (default: hace 12 meses)."),
    hasta: Optional[date] = Query(None, description="Fin del rango, inclusive (default: hoy)."),
    empresa: Optional[str] = Query(None, max_length=100, description="Segmentador: EmpresaID."),
    campana: Optional[str] = Query(None, max_length=100, description="Segmentador: CampanaID."),
    modelo: Optional[str] = Query(None, max_length=80, description="Segmentador: modelo de Gemini."),
    modo: Optional[str] = Query(None, pattern="^(sync|batch)$", description="Segmentador: sync | batch."),
    origen: Optional[str] = Query(None, pattern="^(manual|scheduler)$", description="Segmentador: manual | scheduler."),
    usuario: Optional[str] = Query(None, max_length=100, description="Segmentador: documento exacto o parte del nombre."),
    current_user: User = Depends(require_view),
):
    """Métricas operativas de las corridas de auditoría en el rango: volumen de
    auditorías por campaña/empresa/usuario/plantilla, origen (manual/programada),
    modo (sync/batch), estado, y actividad por día y por hora.

    Segmentadores opcionales (empresa/campaña/modelo/modo/origen/usuario) acotan
    TODAS las agregaciones al subconjunto pedido; empresa/campaña son el ID (la
    columna los guarda como texto, ej. "10")."""
    hasta = hasta or date.today()
    desde = desde or (hasta - timedelta(days=365))
    params = {"desde": desde, "hasta_next": hasta + timedelta(days=1)}
    where = "started_at >= :desde AND started_at < :hasta_next"
    if empresa:
        where += " AND empresa = :empresa"; params["empresa"] = empresa
    if campana:
        where += " AND campana = :campana"; params["campana"] = campana
    if modelo:
        where += " AND modelo = :modelo"; params["modelo"] = modelo
    if modo:
        where += " AND modo = :modo"; params["modo"] = modo
    if origen:
        where += " AND trigger_source = :origen"; params["origen"] = origen
    if usuario:
        where += " AND " + _cond_usuario(usuario, "user_id")

    # Todas las agregaciones corren sobre la vista de CORRIDAS y no sobre las filas
    # de la tabla: en batch una corrida son varios lotes (ver _SQL_CORRIDAS), y
    # contarlos por separado inflaba "corridas" y duplicaba las solicitadas de las
    # corridas sin muestreo por grupo. Los filtros del segmentador se aplican dentro
    # del agrupado, sobre columnas que son constantes dentro de una corrida.
    cte = "WITH corridas AS (" + _SQL_CORRIDAS.format(
        where=f"WHERE {where}", cached=select_cached_tokens(engine, agregado=True)) + ")"

    try:
        with engine.connect() as conn:
            total = _rows(conn, f"""{cte}
                SELECT
                    COUNT(*)                                AS corridas,
                    COALESCE(SUM(filas_auditadas), 0)       AS auditorias,
                    COALESCE(SUM(filas_error), 0)           AS errores,
                    COALESCE(SUM(cantidad_solicitada), 0)   AS solicitadas,
                    SUM(CASE WHEN status = 'EXITO' THEN 1 ELSE 0 END)   AS corridas_exito,
                    SUM(CASE WHEN status = 'ERROR' THEN 1 ELSE 0 END)   AS corridas_error,
                    AVG(CASE WHEN modo = 'sync' THEN CAST(duration_seconds AS FLOAT) END) AS duracion_prom_sync
                FROM corridas
            """, params)

            por_dia = _rows(conn, f"""{cte}
                SELECT CONVERT(CHAR(10), {_LOCAL_AUDIT}, 23) AS dia,
                       COUNT(*) AS corridas,
                       COALESCE(SUM(filas_auditadas), 0) AS auditorias
                FROM corridas
                GROUP BY CONVERT(CHAR(10), {_LOCAL_AUDIT}, 23)
                ORDER BY dia
            """, params)

            por_hora = _rows(conn, f"""{cte}
                SELECT DATEPART(HOUR, {_LOCAL_AUDIT}) AS hora,
                       COUNT(*) AS corridas,
                       COALESCE(SUM(filas_auditadas), 0) AS auditorias
                FROM corridas
                GROUP BY DATEPART(HOUR, {_LOCAL_AUDIT})
                ORDER BY hora
            """, params)

            # Volumen por dimensión (ordenado por cantidad de auditorías, no costo).
            # empresa/campana pueden ser un ID (dígitos) o un nombre ya suelto (ej.
            # "CSV"): se agrupa por el valor crudo y después se resuelven los que
            # son ID a su nombre.
            por_campana = _rows(conn, f"""{cte}
                SELECT TOP 25 campana AS campana,
                       COUNT(*) AS corridas,
                       COALESCE(SUM(filas_auditadas), 0) AS auditorias,
                       COALESCE(SUM(filas_error), 0) AS errores
                FROM corridas
                WHERE campana IS NOT NULL
                GROUP BY campana
                ORDER BY auditorias DESC, corridas DESC
            """, params)

            por_empresa = _rows(conn, f"""{cte}
                SELECT TOP 25 empresa AS empresa,
                       COUNT(*) AS corridas,
                       COALESCE(SUM(filas_auditadas), 0) AS auditorias
                FROM corridas
                WHERE empresa IS NOT NULL
                GROUP BY empresa
                ORDER BY auditorias DESC, corridas DESC
            """, params)

            # Quién audita más (documento -> nombre de nómina).
            por_usuario = _rows(conn, f"""{cte}
                SELECT TOP 20 user_id,
                       COUNT(*) AS corridas,
                       COALESCE(SUM(filas_auditadas), 0) AS auditorias
                FROM corridas
                WHERE user_id IS NOT NULL
                GROUP BY user_id
                ORDER BY auditorias DESC, corridas DESC
            """, params)

            por_plantilla = _rows(conn, f"""{cte}
                SELECT TOP 25 plantilla_id,
                       COUNT(*) AS corridas,
                       COALESCE(SUM(filas_auditadas), 0) AS auditorias
                FROM corridas
                WHERE plantilla_id IS NOT NULL
                GROUP BY plantilla_id
                ORDER BY auditorias DESC, corridas DESC
            """, params)

            por_origen = _rows(conn, f"""{cte}
                SELECT trigger_source AS origen,
                       COUNT(*) AS corridas,
                       COALESCE(SUM(filas_auditadas), 0) AS auditorias
                FROM corridas
                GROUP BY trigger_source
                ORDER BY auditorias DESC
            """, params)

            por_modo = _rows(conn, f"""{cte}
                SELECT modo,
                       COUNT(*) AS corridas,
                       COALESCE(SUM(filas_auditadas), 0) AS auditorias
                FROM corridas
                GROUP BY modo
                ORDER BY auditorias DESC
            """, params)

            por_estado = _rows(conn, f"""{cte}
                SELECT status, COUNT(*) AS corridas
                FROM corridas
                GROUP BY status
                ORDER BY corridas DESC
            """, params)

            # -- Resolución de nombres de las dimensiones ----------------------
            camp_ids = {str(r["campana"]) for r in por_campana if str(r["campana"]).isdigit()}
            emp_ids = {str(r["empresa"]) for r in por_empresa if str(r["empresa"]).isdigit()}
            camp_nombres = _nombres_campanas(conn, camp_ids)
            emp_nombres = _nombres_empresas(conn, emp_ids)
            plant_nombres, _ = _nombres_plantillas(conn, {r["plantilla_id"] for r in por_plantilla})
            user_nombres = _resolver_nombres_usuarios(conn, {str(r["user_id"]) for r in por_usuario})

        for r in por_campana:
            r["campana_nombre"] = camp_nombres.get(str(r["campana"])) or str(r["campana"])
        for r in por_empresa:
            r["empresa_nombre"] = emp_nombres.get(str(r["empresa"])) or str(r["empresa"])
        for r in por_plantilla:
            r["plantilla_nombre"] = plant_nombres.get(int(r["plantilla_id"])) or f"Plantilla {r['plantilla_id']}"
        for r in por_usuario:
            r["user_nombre"] = user_nombres.get(str(r["user_id"]))

        t = total[0] if total else {}
        corridas = int(t.get("corridas") or 0)
        return {
            "rango": {"desde": desde.isoformat(), "hasta": hasta.isoformat()},
            "total": {
                "corridas": corridas,
                "auditorias": int(t.get("auditorias") or 0),
                "errores": int(t.get("errores") or 0),
                "solicitadas": int(t.get("solicitadas") or 0),
                "corridas_exito": int(t.get("corridas_exito") or 0),
                "corridas_error": int(t.get("corridas_error") or 0),
                "tasa_exito": (int(t.get("corridas_exito") or 0) / corridas) if corridas else None,
                "duracion_prom_sync_seg": float(t["duracion_prom_sync"]) if t.get("duracion_prom_sync") is not None else None,
            },
            "por_dia": por_dia,
            "por_hora": por_hora,
            "por_campana": por_campana,
            "por_empresa": por_empresa,
            "por_usuario": por_usuario,
            "por_plantilla": por_plantilla,
            "por_origen": por_origen,
            "por_modo": por_modo,
            "por_estado": por_estado,
        }

    except Exception as e:
        logger.exception("Error en /uso-ia/actividad")
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================
#   SEGMENTOS (opciones de los segmentadores de auditorías)
# =========================================================

def _unitario_por_nivel(
    conn, *, desde: date, hasta_next: date, empresa_id: Optional[int] = None,
    campana_id: Optional[int] = None, modelo: Optional[str] = None, modo: Optional[str] = None,
) -> List[dict]:
    """Costo y consumo por auditoría, agrupado por NIVEL DE RAZONAMIENTO.

    Sale de calidad.AuditExecutionLog y no de pagina_web.vw_IA_Uso_Costos como el
    resto del Análisis: el nivel es un atributo de la CORRIDA y sólo el log lo
    guarda (el libro IA_Uso factura por llamada y no lo conoce). El costo se
    calcula acá con la misma fórmula que el listado de logs —tarifa vigente del
    modelo, thinking a tarifa de output, batch -50%—, así que los números son
    comparables con los de la tabla por modelo.

    La columna que importa es `thoughts_prom`: es la que hace visible para qué
    sirve el nivel (cuántos tokens de pensamiento se gastan por auditoría), que es
    exactamente lo que se descontroló cuando el tope por tokens dejó de aplicarse.

    Las corridas sin nivel (anteriores a la migración 2026-08-19b) se agrupan
    aparte bajo `null`: no se asumen MEDIUM porque corrieron sin nivel fijado y
    mezclarlas ensuciaría la comparación.
    """
    filtros = ["started_at >= :desde", "started_at < :hasta_next", "filas_auditadas > 0"]
    params: dict = {"desde": desde, "hasta_next": hasta_next}
    # empresa/campana se guardan como TEXTO con el id adentro (ver execution_log.py::_texto).
    if empresa_id is not None:
        filtros.append("empresa = :empresa_id"); params["empresa_id"] = str(empresa_id)
    if campana_id is not None:
        filtros.append("campana = :campana_id"); params["campana_id"] = str(campana_id)
    if modelo:
        filtros.append("modelo = :modelo"); params["modelo"] = modelo
    if modo:
        filtros.append("modo = :modo"); params["modo"] = modo

    filas = _rows(conn, f"""
        SELECT nivel_razonamiento, modelo, modo,
               SUM(filas_auditadas)  AS auditorias,
               SUM(input_tokens)     AS input_tokens,
               SUM(output_tokens)    AS output_tokens,
               SUM(thoughts_tokens)  AS thoughts_tokens,
               {select_cached_tokens(engine, agregado=True)}
        FROM calidad.AuditExecutionLog
        WHERE {' AND '.join(filtros)}
        GROUP BY nivel_razonamiento, modelo, modo
    """, params)

    tarifas = {m: obtener_tarifa(engine, modelo=m)
               for m in {f.get("modelo") or MODELO_IA_DEFAULT for f in filas}}

    acumulado: dict = {}
    for f in filas:
        nivel = f.get("nivel_razonamiento")
        tokens = {c: f.get(c) or 0
                  for c in ("input_tokens", "output_tokens", "thoughts_tokens", "cached_tokens")}
        costo = calcular_costo_usd(tokens, f.get("modo"), tarifas.get(f.get("modelo") or MODELO_IA_DEFAULT))
        acc = acumulado.setdefault(nivel, {
            "nivel_razonamiento": nivel, "auditorias": 0, "costo_usd": 0.0,
            "input_tokens": 0, "output_tokens": 0, "thoughts_tokens": 0,
        })
        acc["auditorias"] += int(f.get("auditorias") or 0)
        acc["costo_usd"] += float(costo or 0)
        for c in ("input_tokens", "output_tokens", "thoughts_tokens"):
            acc[c] += int(tokens[c])

    resultado = []
    for acc in acumulado.values():
        n = acc["auditorias"] or 1  # ya se filtró filas_auditadas > 0; defensa por las dudas
        acc["costo_usd"] = round(acc["costo_usd"], 4)
        acc["costo_unitario_usd"] = round(acc["costo_usd"] / n, 5)
        acc["tokens_prom"] = round((acc["input_tokens"] + acc["output_tokens"] + acc["thoughts_tokens"]) / n)
        acc["thoughts_prom"] = round(acc["thoughts_tokens"] / n)
        acc["output_prom"] = round(acc["output_tokens"] / n)
        resultado.append(acc)
    resultado.sort(key=lambda r: r["costo_usd"], reverse=True)
    return resultado


@router.get("/segmentos")
def segmentos(current_user: User = Depends(require_view)):
    """Valores disponibles para los dropdowns de segmentación de auditorías:
    campañas y empresas (id + nombre) y modelos que efectivamente tuvieron
    consumo de auditoría (feature <> chatbot). Modo/origen son fijos, no salen
    de acá; el nivel de razonamiento tampoco (son 3 valores fijos y el filtro los
    lista completos aunque todavía no tengan corridas). Best-effort: si algo
    falla, ese listado va vacío."""
    campanas, empresas, modelos = [], [], []
    try:
        with engine.connect() as conn:
            campanas = _rows(conn, """
                SELECT DISTINCT v.campana_id AS id, c.Nombre AS nombre
                FROM pagina_web.vw_IA_Uso_Costos v
                JOIN calidad.Campanas c ON v.campana_id = c.CampanaID
                WHERE v.feature <> 'chatbot' AND v.campana_id IS NOT NULL
                ORDER BY c.Nombre
            """, {})
            empresas = _rows(conn, """
                SELECT DISTINCT v.empresa_id AS id, e.Nombre AS nombre
                FROM pagina_web.vw_IA_Uso_Costos v
                JOIN calidad.Empresas e ON v.empresa_id = e.EmpresaID
                WHERE v.feature <> 'chatbot' AND v.empresa_id IS NOT NULL
                ORDER BY e.Nombre
            """, {})
            modelos = [r["modelo"] for r in _rows(conn, """
                SELECT DISTINCT modelo FROM pagina_web.vw_IA_Uso_Costos
                WHERE feature <> 'chatbot' AND modelo IS NOT NULL
                ORDER BY modelo
            """, {})]
    except Exception as e:
        logger.warning(f"No se pudieron cargar los segmentos de auditoría: {e}")
    return {"campanas": campanas, "empresas": empresas, "modelos": modelos}


# =========================================================
#   SOLICITUDES DEL CHATBOT (pagina_web.query_chatbots_logs)
# =========================================================
# Cada fila es una interacción: qué consultó el usuario (query), qué respondió el
# bot (response) y de dónde salió el contexto RAG (context, con archivo+score+
# texto de cada fuente). El listado da un preview; el detalle por id trae todo,
# con las fuentes separadas (_parsear_contexto). Detrás de uso_ia.chatbot.

# Columnas ordenables del listado (clave frontend -> expresión SQL, lista blanca).
_CHATLOGS_ORDEN = {
    "fecha": "fecha",
    "usuario": "user_id",
    "chatbot": "effective_campana",
    "tokens": "(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0))",
}


def _puede_ver_costos(user: User) -> bool:
    """True si el usuario puede ver costos/tokens del chatbot (uso_ia.chatbot).
    Calidad (solo chatbot.solicitudes) ve las consultas pero NO los tokens/gasto,
    así que a esos usuarios se les quitan los campos de tokens de la respuesta."""
    return user.is_super_admin or PERM_CHATBOT in (user.permissions or [])


_CAMPOS_TOKENS = ("input_tokens", "output_tokens", "embedding_tokens")

# El smoke test en vivo (tests/test_chatbots_live.py) le pega a TODOS los bots con
# USER_ID = 0, así que cada corrida mete una fila por bot sin usuario detrás — en
# pantalla salían como "—". Son consultas nuestras, no de operadores: no aportan
# nada a quien revisa interacciones reales y encima empujan las verdaderas fuera
# de la primera página. Se filtran acá (no se borran: los tokens que gastaron son
# reales y tienen que seguir contando en el tablero de costos).
_SOLO_USUARIOS_REALES = "ISNULL(user_id, 0) <> 0"

# Permiso comodín: calidad puede probar todos los bots, así que también ve todos
# los logs. Mismo código que usa get_allowed_bots.
_PERM_CHATBOT_ADMIN = "chatbot:admin"


def _slugs_visibles(user: User) -> Optional[set[str]]:
    """Bots cuyas consultas puede ver el usuario; None = todos, sin filtrar.

    El acceso a los LOGS de un bot se define con el mismo permiso que el acceso a
    USARLO (chatbot:<slug>, o chatbot:csv para todo el grupo CSV): un rol como
    "Cliente Vantix" tiene que ver las consultas de vantix y ninguna otra. Se reusa
    get_allowed_bots para no tener dos definiciones de acceso que se despeguen.

    Se importa acá adentro a propósito: app.routers.chatbot arrastra app.services
    (registry + stack de IA) y este módulo no lo necesita para nada más.
    """
    if user.is_super_admin or _PERM_CHATBOT_ADMIN in (user.permissions or []):
        return None

    from app.routers.chatbot import get_allowed_bots

    slugs = {c.slug for c in get_allowed_bots(user)}
    # effective_campana guarda la campaña con la que se consultó, y las filas
    # anteriores a la convención de slug traen el formato viejo con espacios
    # ("csv no premium" en vez de "csv_no_premium"; ver slugify_campana). Se
    # aceptan las dos formas para que el historial no desaparezca a medias.
    return slugs | {s.replace("_", " ") for s in slugs}


def _cond_chatbots_visibles(slugs: Optional[set[str]], partes: list, params: dict) -> None:
    """Agrega a la SQL el filtro por bots visibles. Sin bots permitidos el
    resultado es vacío explícito ('1 = 0'), nunca "sin filtro"."""
    if slugs is None:
        return
    if not slugs:
        partes.append("1 = 0")
        return
    partes.append("effective_campana IN :slugs_visibles")
    params["slugs_visibles"] = sorted(slugs)


def _con_bindparams(sql: str, params: dict):
    """text() con el bindparam expandible de la lista de bots, si hace falta."""
    query = text(sql)
    if "slugs_visibles" in params:
        query = query.bindparams(bindparam("slugs_visibles", expanding=True))
    return query


@router.get("/chatbot-logs")
def listar_chatbot_logs(
    fecha_desde: Optional[date] = Query(None, description="Filtra por fecha >= (hora ARG)."),
    fecha_hasta: Optional[date] = Query(None, description="Filtra por fecha <= (fin del día, hora ARG)."),
    chatbot: Optional[str] = Query(None, max_length=100, description="effective_campana (ej. 'voltara')."),
    usuario: Optional[str] = Query(None, max_length=100, description="Documento exacto o parte del nombre."),
    q: Optional[str] = Query(None, max_length=200, description="Busca el texto en la consulta o la respuesta."),
    orden: Optional[str] = Query(None, description=f"Columna de orden: {', '.join(_CHATLOGS_ORDEN)}."),
    dir: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_chatbot_logs),
):
    """Lista interacciones del chatbot (preview de consulta/respuesta) para
    analizarlas una por una. Tokens solo para quien ve costos (uso_ia.chatbot);
    Calidad (chatbot.solicitudes) ve las consultas sin gasto. El texto completo
    va por /chatbot-logs/{id}. Las corridas del smoke test (sin usuario) quedan
    fuera (ver _SOLO_USUARIOS_REALES) y cada uno ve solo los bots a los que tiene
    acceso (ver _slugs_visibles)."""
    visibles = _slugs_visibles(current_user)
    partes = [_SOLO_USUARIOS_REALES]
    params: dict = {"limit": limit, "offset": offset}
    _cond_chatbots_visibles(visibles, partes, params)
    if fecha_desde:
        partes.append("fecha >= :fecha_desde"); params["fecha_desde"] = fecha_desde
    if fecha_hasta:
        partes.append("fecha < DATEADD(DAY, 1, :fecha_hasta)"); params["fecha_hasta"] = fecha_hasta
    if chatbot:
        partes.append("effective_campana = :chatbot"); params["chatbot"] = chatbot
    if usuario:
        partes.append(_cond_usuario(usuario, "user_id"))
    if q:
        # También sobre la reescrita: una repregunta suelta ("tiene costo?") no
        # matchea por su texto, pero sí por el tema que el bot le reconstruyó.
        partes.append("(query LIKE :q OR query_condensada LIKE :q OR response LIKE :q)")
        params["q"] = f"%{q}%"
    where_clause = f"WHERE {' AND '.join(partes)}"

    orden_sql = "fecha DESC, id DESC"
    if orden and orden in _CHATLOGS_ORDEN:
        orden_sql = f"{_CHATLOGS_ORDEN[orden]} {'ASC' if dir == 'asc' else 'DESC'}, id DESC"

    # Preview recortado en SQL (no traer los varchar completos por fila); el
    # texto completo lo sirve el detalle. num_fuentes = cuántos '--- Archivo:'.
    query = _con_bindparams(f"""
        SELECT id, fecha, user_id, effective_campana,
               LEFT(query, 300)    AS query_preview,
               -- NULL cuando no hubo reescritura (primer mensaje de la charla).
               LEFT(query_condensada, 300) AS query_condensada_preview,
               LEFT(response, 300) AS response_preview,
               input_tokens, output_tokens, embedding_tokens,
               (LEN(context) - LEN(REPLACE(context, '--- Archivo:', ''))) / LEN('--- Archivo:') AS num_fuentes
        FROM pagina_web.query_chatbots_logs
        {where_clause}
        ORDER BY {orden_sql}
        OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
    """, params)
    count_query = _con_bindparams(
        f"SELECT COUNT(*) FROM pagina_web.query_chatbots_logs {where_clause}", params
    )
    ver_costos = _puede_ver_costos(current_user)

    try:
        with engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(query, params).mappings().all()]
            total = conn.execute(count_query, params).scalar()
            nombres = _resolver_nombres_usuarios(conn, {str(r["user_id"]) for r in rows if r.get("user_id")})
            # Lista de chatbots para el selector (distinct). Solo hace falta para
            # Calidad: los usuarios con costos ya pueblan el selector desde el
            # dashboard (IA_Uso), así que para ellos no se corre esta consulta.
            chatbots = []
            if not ver_costos:
                partes_sel = ["effective_campana IS NOT NULL", _SOLO_USUARIOS_REALES]
                params_sel: dict = {}
                _cond_chatbots_visibles(visibles, partes_sel, params_sel)
                sel = _con_bindparams(f"""
                    SELECT DISTINCT effective_campana FROM pagina_web.query_chatbots_logs
                    WHERE {' AND '.join(partes_sel)}
                    ORDER BY effective_campana
                """, params_sel)
                chatbots = [c for (c,) in conn.execute(sel, params_sel).all()]
        for r in rows:
            r["user_nombre"] = nombres.get(str(r.get("user_id")))
            r["fecha"] = _con_tz_art(r.get("fecha"))
            if not ver_costos:
                for campo in _CAMPOS_TOKENS:
                    r.pop(campo, None)
        return {"total": total, "data": rows, "chatbots": chatbots}
    except Exception as e:
        logger.error(f"Error listando solicitudes del chatbot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/chatbot-logs/{log_id}")
def detalle_chatbot_log(log_id: int, current_user: User = Depends(require_chatbot_logs)):
    """Detalle de una interacción: consulta y respuesta completas + las fuentes
    del contexto RAG (archivo, score, contenido) separadas una por una. Tokens
    solo para quien ve costos (uso_ia.chatbot)."""
    try:
        with engine.connect() as conn:
            row = conn.execute(text("""
                SELECT id, fecha, user_id, effective_campana, query, query_condensada,
                       response, context,
                       input_tokens, output_tokens, embedding_tokens, task_id, active
                FROM pagina_web.query_chatbots_logs
                WHERE id = :id
            """), {"id": log_id}).mappings().first()
            if not row:
                raise HTTPException(status_code=404, detail="Interacción no encontrada.")
            r = dict(row)
            # Sin esto el filtro del listado no protege nada: alcanza con pedir
            # ids a mano para leer las consultas de cualquier otro bot. Se
            # responde 404 (no 403) para no confirmar que el id existe.
            visibles = _slugs_visibles(current_user)
            if visibles is not None and r.get("effective_campana") not in visibles:
                raise HTTPException(status_code=404, detail="Interacción no encontrada.")
            nombres = _resolver_nombres_usuarios(conn, {str(r["user_id"])} if r.get("user_id") else set())
            # El nombre que queda en los metadatos es el materializado por el
            # indexador ("04_dbdoc_19.md"); en pantalla va el título con el que se
            # cargó el documento, que es lo que le sirve a quien lo tiene que buscar.
            r["fuentes"] = _parsear_contexto(r.get("context"))
            titulos = titulos_por_archivo(conn, {f["archivo"] for f in r["fuentes"]})
        for f in r["fuentes"]:
            f["titulo"] = titulos.get(f["archivo"])
        r["user_nombre"] = nombres.get(str(r.get("user_id")))
        r["fecha"] = _con_tz_art(r.get("fecha"))
        if not _puede_ver_costos(current_user):
            for campo in _CAMPOS_TOKENS:
                r.pop(campo, None)
        return r
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error en /uso-ia/chatbot-logs/{id}")
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================
#   PRESUPUESTO MENSUAL (pagina_web.IA_Presupuesto)
# =========================================================

@router.get("/presupuesto")
def presupuesto(current_user: User = Depends(require_view)):
    """Estado del presupuesto mensual: config, gasto del mes en curso, % y
    avisos ya enviados. Si la migración 2026-07-05 no está aplicada, devuelve
    configurado=false (el gasto del mes se calcula igual)."""
    try:
        return estado_presupuesto()
    except Exception as e:
        logger.exception("Error en GET /uso-ia/presupuesto")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/presupuesto")
def guardar_presupuesto(
    body: PresupuestoIARequest,
    current_user: User = Depends(require_view),
):
    """Crea/actualiza la config (fila única). Los umbrales se normalizan
    (enteros 1..1000, ordenados); el job horario de run_scheduler.py es quien
    manda los avisos (1 por umbral por mes)."""
    if body.monto_usd is None or body.monto_usd <= 0:
        raise HTTPException(status_code=400, detail="El monto mensual debe ser mayor a 0.")
    umbrales = parsear_umbrales(body.umbrales)
    if not umbrales:
        raise HTTPException(status_code=400, detail="Indicá al menos un umbral válido (porcentaje entero, ej. 50,75,90,100).")
    destinatarios = (body.destinatarios or "").strip() or None
    try:
        guardar_config(
            monto_usd=round(float(body.monto_usd), 2),
            umbrales=",".join(str(u) for u in umbrales),
            destinatarios=destinatarios,
            usuario=getattr(current_user, "usuario", None),
        )
        return estado_presupuesto()
    except Exception as e:
        logger.exception("Error guardando el presupuesto de IA")
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo guardar (¿está aplicada la migración 2026-07-05_ia_presupuesto.sql?): {e}",
        )


# =========================================================
#   LOG DE CORRIDAS (calidad.AuditExecutionLog)
# =========================================================

# Una CORRIDA no es una fila de la tabla: en modo batch, calidad_batch parte los
# audios en lotes (hoy de hasta 250 llamados) y cada lote abre su propia fila (su
# propio job de Gemini, que cierra por separado horas después). Todas las filas de una misma
# ejecución comparten `run_id` (Auditor.run_batch lo genera una vez por corrida);
# esta vista las colapsa para que la pantalla muestre una fila por corrida y no
# la misma programada repetida N veces, que se leía como "se ejecutó N veces".
#
# Reglas del agregado, por si no son obvias:
#   - `cantidad_solicitada`: SUM solo con muestreo por operador/tipificación,
#     donde cada lote guarda las filas que le tocaron; sin muestreo TODOS los
#     lotes guardan el total pedido de la corrida, así que sumarlos lo duplicaría
#     (ver gemini.py::process_batch).
#   - `finished_at`/`duration_seconds`: solo cuando cerraron TODOS los lotes; con
#     uno abierto la corrida no terminó y un "duró X" sería mentira.
#   - `status`: EN_CURSO gana sobre todo (la corrida sigue viva); si todos los
#     lotes coinciden, ese estado; si hay éxitos mezclados con fallas, PARCIAL.
_SQL_CORRIDAS = """
    SELECT
        run_id,
        COUNT(*)                                    AS lotes,
        MIN(id)                                     AS id,
        MIN(trigger_source)                         AS trigger_source,
        MIN(scheduler_id)                           AS scheduler_id,
        MIN(scheduler_name)                         AS scheduler_name,
        MIN(task_id)                                AS task_id,
        MIN(modo)                                   AS modo,
        MIN(empresa)                                AS empresa,
        MIN(campana)                                AS campana,
        MIN(plantilla_id)                           AS plantilla_id,
        MIN(user_id)                                AS user_id,
        MIN(fecha_desde)                            AS fecha_desde,
        MAX(fecha_hasta)                            AS fecha_hasta,
        MIN(upload_group_id)                        AS upload_group_id,
        MIN(modelo)                                 AS modelo,
        -- Nivel de razonamiento de la corrida (ver AuditorIA/razonamiento.py). MIN
        -- igual que `modelo`: todos los lotes de una corrida se crean con el mismo.
        MIN(nivel_razonamiento)                     AS nivel_razonamiento,
        MAX(CAST(por_operador AS TINYINT))          AS por_operador,
        MAX(CAST(por_tipificacion AS TINYINT))      AS por_tipificacion,
        CASE
            WHEN MAX(CAST(por_operador AS TINYINT)) = 1
              OR MAX(CAST(por_tipificacion AS TINYINT)) = 1
            THEN SUM(cantidad_solicitada)
            ELSE MAX(cantidad_solicitada)
        END                                         AS cantidad_solicitada,
        SUM(filas_auditadas)                        AS filas_auditadas,
        SUM(filas_error)                            AS filas_error,
        SUM(input_tokens)                           AS input_tokens,
        SUM(output_tokens)                          AS output_tokens,
        SUM(thoughts_tokens)                        AS thoughts_tokens,
        -- Cuántos de esos input_tokens se cobraron a precio de caché (migración
        -- 2026-09-01b). Sin la columna llega 0 y el costo sale como salía antes.
        {cached},
        MAX(CAST(mail_enviado AS TINYINT))          AS mail_enviado,
        MAX(CAST(gsheet_enviado AS TINYINT))        AS gsheet_enviado,
        MAX(mail_destinatarios)                     AS mail_destinatarios,
        MAX(error_message)                          AS error_message,
        MIN(started_at)                             AS started_at,
        CASE WHEN COUNT(*) = COUNT(finished_at) THEN MAX(finished_at) END AS finished_at,
        CASE WHEN COUNT(*) = COUNT(finished_at)
             THEN DATEDIFF(SECOND, MIN(started_at), MAX(finished_at)) END  AS duration_seconds,
        CASE
            WHEN SUM(CASE WHEN status = 'EN_CURSO' THEN 1 ELSE 0 END) > 0 THEN 'EN_CURSO'
            WHEN MIN(status) = MAX(status) THEN MIN(status)
            WHEN SUM(CASE WHEN status = 'EXITO' THEN 1 ELSE 0 END) > 0 THEN 'PARCIAL'
            ELSE 'ERROR'
        END                                         AS status
    FROM calidad.AuditExecutionLog
    {where}
    GROUP BY run_id
"""

# Columnas ordenables del listado (clave del frontend -> expresión SQL). Es una
# lista blanca: NUNCA se interpola el valor del querystring, solo estas
# expresiones fijas. Son nombres de la vista agregada (_SQL_CORRIDAS), no de la
# tabla. Costo no está: se calcula en Python por fila (tarifa según modelo), no
# existe en la tabla; Tokens es el proxy ordenable más cercano.
_LOGS_ORDEN = {
    "inicio": "started_at",
    "modo": "modo",
    "origen": "trigger_source",
    "usuario": "user_id",
    "contexto": "empresa",  # la columna compuesta Empresa/Campaña/Plantilla ordena por empresa
    "modelo": "modelo",
    "nivel": "nivel_razonamiento",
    "solicitadas": "cantidad_solicitada",
    "auditadas": "filas_auditadas",
    "errores": "filas_error",
    "duracion": "duration_seconds",
    "tokens": "(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0) + COALESCE(thoughts_tokens, 0))",
    "estado": "status",
}


@router.get("/logs")
def listar_logs_ejecucion(
    fecha_desde: Optional[date] = Query(None, description="Filtra por started_at >= esta fecha."),
    fecha_hasta: Optional[date] = Query(None, description="Filtra por started_at <= esta fecha (fin del día)."),
    trigger_source: Optional[str] = Query(None, description="manual | scheduler"),
    modo: Optional[str] = Query(None, description="sync | batch"),
    nivel_razonamiento: Optional[str] = Query(None, max_length=10, description="LOW | MEDIUM | HIGH"),
    status: Optional[str] = Query(None, description="EN_CURSO | EXITO | PARCIAL | ERROR | SIN_DATOS"),
    empresa: Optional[str] = None,
    campana: Optional[str] = None,
    usuario: Optional[str] = Query(None, max_length=100,
                                   description="Documento exacto, o parte del nombre (se resuelve contra nómina)."),
    scheduler_id: Optional[int] = None,
    orden: Optional[str] = Query(None, description=f"Columna de orden: {', '.join(_LOGS_ORDEN)}."),
    dir: str = Query("desc", pattern="^(asc|desc)$", description="Dirección del orden."),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_view),
):
    """Lista corridas de auditoría (calidad.AuditExecutionLog): fuente única con
    modo, origen, empresa/campaña/plantilla, rango y cantidad auditada, duración,
    errores/omitidos, tokens, modelo de Gemini y estado de envío (mail/Sheets) de
    cada corrida, sin importar si fue manual o por scheduler, sync o batch.

    Una fila = una CORRIDA, no un job de Gemini: los lotes de una misma ejecución
    se colapsan por `run_id` (ver _SQL_CORRIDAS) y viajan contados en `lotes`. El
    detalle de cada lote se pide aparte, en /logs/{run_id}/lotes."""
    filtros = []
    params: dict = {"limit": limit, "offset": offset}

    if fecha_desde:
        filtros.append("started_at >= :fecha_desde")
        params["fecha_desde"] = fecha_desde
    if fecha_hasta:
        filtros.append("started_at < DATEADD(DAY, 1, :fecha_hasta)")
        params["fecha_hasta"] = fecha_hasta
    if trigger_source:
        filtros.append("trigger_source = :trigger_source")
        params["trigger_source"] = trigger_source
    if modo:
        filtros.append("modo = :modo")
        params["modo"] = modo
    if nivel_razonamiento:
        filtros.append("nivel_razonamiento = :nivel_razonamiento")
        params["nivel_razonamiento"] = nivel_razonamiento.upper()
    if empresa:
        filtros.append("empresa = :empresa")
        params["empresa"] = empresa
    if campana:
        filtros.append("campana = :campana")
        params["campana"] = campana
    if scheduler_id is not None:
        filtros.append("scheduler_id = :scheduler_id")
        params["scheduler_id"] = scheduler_id

    # Usuario: la tabla guarda solo el documento. Si escriben un número se
    # filtra directo; si escriben texto, se buscan los documentos que matcheen
    # por nombre en nómina (misma vista que la resolución de nombres). Sin
    # coincidencias => resultado vacío explícito (no "sin filtro").
    usuario_ids: list[str] = []
    if usuario:
        usuario = usuario.strip()
        if usuario.isdigit():
            filtros.append("user_id = :usuario")
            params["usuario"] = usuario
        else:
            usuario_ids = _buscar_documentos_por_nombre(usuario)
            if usuario_ids:
                filtros.append("user_id IN :usuario_ids")
                params["usuario_ids"] = usuario_ids
            else:
                filtros.append("1 = 0")

    where_clause = f"WHERE {' AND '.join(filtros)}" if filtros else ""

    # El estado se filtra DESPUÉS de agrupar, sobre el estado de la corrida: si se
    # filtrara por fila, pedir "Error" traería la corrida completa recortada a sus
    # lotes fallidos (auditadas y tokens de menos), y una corrida PARCIAL —que como
    # fila no existe en la tabla— no se podría pedir nunca.
    where_corrida = ""
    if status:
        where_corrida = "WHERE status = :status"
        params["status"] = status

    # Orden: solo expresiones de la lista blanca _LOGS_ORDEN (nunca el valor del
    # querystring). started_at DESC de desempate mantiene estable la paginación.
    orden_sql = "started_at DESC"
    if orden and orden in _LOGS_ORDEN:
        orden_sql = f"{_LOGS_ORDEN[orden]} {'ASC' if dir == 'asc' else 'DESC'}, started_at DESC"

    corridas_cte = _SQL_CORRIDAS.format(
        where=where_clause, cached=select_cached_tokens(engine, agregado=True))
    query = text(f"""
        WITH corridas AS ({corridas_cte})
        SELECT * FROM corridas
        {where_corrida}
        ORDER BY {orden_sql}
        OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
    """)
    count_query = text(f"""
        WITH corridas AS ({corridas_cte})
        SELECT COUNT(*) FROM corridas {where_corrida}
    """)
    if usuario_ids:
        query = query.bindparams(bindparam("usuario_ids", expanding=True))
        count_query = count_query.bindparams(bindparam("usuario_ids", expanding=True))

    try:
        with engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(query, params).mappings().all()]
            total = conn.execute(count_query, params).scalar()
            _sumar_desglose_muestreo(conn, rows)
            _enriquecer_logs_ejecucion(conn, rows)
        return {"total": total, "data": rows}
    except Exception as e:
        logger.error(f"Error listando logs de ejecución: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/logs/{run_id}/lotes")
def detalle_lotes_corrida(run_id: str, current_user: User = Depends(require_view)):
    """Los lotes (jobs de Gemini) que componen una corrida del listado.

    Es lo que se despliega al abrir una fila: sirve para ver cuál de los lotes
    falló y con qué mensaje, que agregado en la fila de la corrida se pierde.
    Una corrida sincrónica devuelve un solo lote —su propia fila—, así que la
    pantalla no necesita distinguir modos."""
    query = text(f"""
        SELECT
            id, batch_id, modo, modelo, nivel_razonamiento, status, error_message,
            cantidad_solicitada, filas_auditadas, filas_error,
            input_tokens, output_tokens, thoughts_tokens,
            {select_cached_tokens(engine)},
            mail_enviado, gsheet_enviado,
            started_at, finished_at, duration_seconds
        FROM calidad.AuditExecutionLog
        WHERE run_id = :run_id
        ORDER BY started_at, id
    """)
    try:
        with engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(query, {"run_id": run_id}).mappings().all()]
            if not rows:
                raise HTTPException(status_code=404, detail="No existe esa corrida.")
            # Tarifa por modelo distinto de la corrida (normalmente uno solo), igual
            # que en el listado: el costo de cada lote es el de SUS tokens.
            tarifas = {
                modelo: obtener_tarifa(engine, modelo=modelo)
                for modelo in {r.get("modelo") or MODELO_IA_DEFAULT for r in rows}
            }
        for r in rows:
            r["started_at"] = _con_tz_utc(r.get("started_at"))
            r["finished_at"] = _con_tz_utc(r.get("finished_at"))
            r["costo_usd"] = calcular_costo_usd(
                {"input_tokens": r.get("input_tokens"), "output_tokens": r.get("output_tokens"),
                 "thoughts_tokens": r.get("thoughts_tokens"),
                 "cached_tokens": r.get("cached_tokens")},
                r.get("modo"), tarifas.get(r.get("modelo") or MODELO_IA_DEFAULT),
            )
        return {"run_id": run_id, "data": rows}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listando los lotes de la corrida {run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _sumar_desglose_muestreo(conn, rows: List[dict]) -> None:
    """Junta en la corrida los desgloses de muestreo de todos sus lotes.

    El desglose ({operador: cuántas le tocaron}) lo calcula cada lote sobre las
    filas que ESE lote mandó a Gemini (ver gemini.py::process_batch), así que el
    reparto real de la corrida es la suma de los parciales. No se puede resolver
    en el GROUP BY —es JSON en una NVARCHAR— y se hace acá, en una sola consulta
    por página. Best-effort: si falla, las corridas quedan sin tooltip."""
    run_ids = [r["run_id"] for r in rows if r.get("run_id")]
    if not run_ids:
        return
    for r in rows:
        r["desglose_muestreo"] = None
    try:
        query = text("""
            SELECT run_id, desglose_muestreo
            FROM calidad.AuditExecutionLog
            WHERE run_id IN :run_ids AND desglose_muestreo IS NOT NULL
        """).bindparams(bindparam("run_ids", expanding=True))
        parciales: dict = {}
        for fila in conn.execute(query, {"run_ids": [str(x) for x in run_ids]}).mappings():
            try:
                desglose = json.loads(fila["desglose_muestreo"])
            except Exception:
                continue
            acumulado = parciales.setdefault(str(fila["run_id"]), {})
            for criterio, conteos in (desglose or {}).items():
                destino = acumulado.setdefault(criterio, {})
                for grupo, cantidad in (conteos or {}).items():
                    destino[grupo] = destino.get(grupo, 0) + int(cantidad or 0)
        for r in rows:
            r["desglose_muestreo"] = parciales.get(str(r.get("run_id")))
    except Exception as e:
        logger.warning(f"No se pudo sumar el desglose de muestreo de las corridas: {e}")


def _con_tz_utc(dt):
    """AuditExecutionLog guarda started_at/finished_at con SYSUTCDATETIME() pero
    naive (sin tzinfo); si se serializan así, el front los interpreta como hora
    local del navegador y muestra UTC0 en vez de convertir a Argentina."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _enriquecer_logs_ejecucion(conn, rows: List[dict]) -> None:
    """AuditExecutionLog solo guarda IDs (empresa/campana/plantilla_id/user_id);
    resuelve nombres para mostrar en la pantalla de logs y normaliza fechas a UTC
    explícito (ver _con_tz_utc). Best-effort por grupo: si una resolución falla,
    esas filas quedan sin *_nombre pero no rompe el listado."""
    empresa_ids = {str(r["empresa"]) for r in rows if r.get("empresa") and str(r["empresa"]).isdigit()}
    campana_ids = {str(r["campana"]) for r in rows if r.get("campana") and str(r["campana"]).isdigit()}
    plantilla_ids = {r["plantilla_id"] for r in rows if r.get("plantilla_id")}
    user_ids = {str(r["user_id"]) for r in rows if r.get("user_id")}

    empresa_nombres = _nombres_empresas(conn, empresa_ids)
    campana_nombres = _nombres_campanas(conn, campana_ids)
    # Nombre + modelo de Gemini configurado HOY (fallback de costeo para filas
    # viejas sin `modelo`) en UNA sola consulta batcheada.
    plantilla_nombres, plantilla_modelos = _nombres_plantillas(conn, plantilla_ids)
    usuario_nombres = _resolver_nombres_usuarios(conn, user_ids)

    # Modelo por fila: el REAL guardado en la corrida si está (columna `modelo`,
    # poblada por Auditor/process_batch); si no (fila vieja), el configurado hoy
    # en la plantilla. Después, 1 sola consulta de tarifa POR MODELO distinto
    # presente en la página (no por fila).
    for r in rows:
        pid = r.get("plantilla_id")
        modelo_plantilla = plantilla_modelos.get(int(pid)) if pid is not None else None
        r["modelo"] = r.get("modelo") or modelo_plantilla or MODELO_IA_DEFAULT
    tarifas_por_modelo = {
        modelo: obtener_tarifa(engine, modelo=modelo)
        for modelo in {r["modelo"] for r in rows} or {MODELO_IA_DEFAULT}
    }

    for r in rows:
        pid = r.get("plantilla_id")
        # El driver puede devolver el UNIQUEIDENTIFIER como uuid.UUID; el frontend
        # lo usa como parte de la URL del detalle, así que viaja siempre como texto.
        if r.get("run_id") is not None:
            r["run_id"] = str(r["run_id"])
        r["empresa_nombre"] = empresa_nombres.get(str(r.get("empresa")))
        r["campana_nombre"] = campana_nombres.get(str(r.get("campana")))
        r["plantilla_nombre"] = plantilla_nombres.get(int(pid)) if pid is not None else None
        r["user_nombre"] = usuario_nombres.get(str(r.get("user_id")))
        r["started_at"] = _con_tz_utc(r.get("started_at"))
        r["finished_at"] = _con_tz_utc(r.get("finished_at"))
        r["costo_usd"] = calcular_costo_usd(
            {"input_tokens": r.get("input_tokens"), "output_tokens": r.get("output_tokens"),
             "thoughts_tokens": r.get("thoughts_tokens"), "cached_tokens": r.get("cached_tokens")},
            r.get("modo"), tarifas_por_modelo.get(r["modelo"]),
        )
        # En el listado agrupado ya llega sumado y parseado (_sumar_desglose_muestreo);
        # solo hay que parsearlo cuando viene crudo de la columna NVARCHAR.
        if isinstance(r.get("desglose_muestreo"), str):
            try:
                r["desglose_muestreo"] = json.loads(r["desglose_muestreo"])
            except Exception:
                r["desglose_muestreo"] = None
