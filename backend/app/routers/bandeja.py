"""
Bandeja del Supervisor — dashboard analítico sobre auditorías.

Reutiliza el SP `calidad.sp_ObtenerAuditoriasFiltradas` (mismo origen que
`/auditorias_realizadas/`) y combina la respuesta con la metadata de la
plantilla (atributos + tipos) para que el frontend pueda decidir qué tipo
de gráfico mostrar por atributo (boolean → pie, enum → pie/bar, numérico
→ histograma; los `string` libres se omiten).

RBAC:
  - `bandeja.view`          — acceso al dashboard general.

El alcance NO es por auditor sino por empresa: quien entra al dashboard ve todas
las auditorías de las campañas que le corresponden (`exigir_acceso_empresa` sobre
los permisos `template:<empresa>`), las haya hecho quien las haya hecho. Un
supervisor necesita ver a su equipo completo, no solo lo que auditó él mismo.
El parámetro `usuario` queda como filtro opcional para quien quiera mirar a un
auditor puntual. Hasta 2026-07-28 esto estaba gateado por `bandeja.ver_todo`, que
forzaba `@AuditorUsuarioID = current_user` a todo el que no lo tuviera.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import bindparam, text

from app import asistente_contexto, asistente_conversaciones, asistente_dashboard, asistente_jobs
from app.database import engine
from app.dependencies import RoleChecker
from app.managers import plantillas_manager_instance
from app.models import User
from app.rbac import exigir_acceso_empresa

router = APIRouter(prefix="/bandeja", tags=["Bandeja"])
logger = logging.getLogger(__name__)


PERM_VIEW = "bandeja.view"
PERM_CONFIG = "bandeja.config"

require_view = RoleChecker([PERM_VIEW])
# Solo quien tenga bandeja.config puede EDITAR la configuración de visualización
# (los demás la ven aplicada pero no pueden cambiarla).
require_config = RoleChecker([PERM_CONFIG])


def _atributos_de_plantilla(plantilla_id: int) -> List[Dict[str, Any]]:
    """Devuelve [{nombre, tipo, restricciones}, ...] o []. No falla si la plantilla
    no tiene atributos cargados — el dashboard mostrará solo los KPIs base."""
    if not plantillas_manager_instance:
        return []
    try:
        plantilla = plantillas_manager_instance.obtener_plantilla(plantilla_id)
    except Exception as e:
        logger.warning("No se pudo obtener plantilla %s: %s", plantilla_id, e)
        return []
    if not plantilla or "atributos" not in plantilla:
        return []
    out = []
    for atr in plantilla["atributos"]:
        out.append({
            "nombre": atr.get("nombre"),
            "tipo": (atr.get("tipo") or "").lower(),
            "restricciones": atr.get("restricciones") or {},
            "orden": atr.get("orden"),
            "dar_aviso": bool(atr.get("DarAviso", False) or atr.get("dar_aviso", False)),
        })
    # Orden estable por el campo `orden` cuando esté presente.
    out.sort(key=lambda a: (a.get("orden") is None, a.get("orden") or 0))
    return out


# ---------------------------------------------------------------------------
# Enriquecido de Operador/Equipo para campañas de origen ALARMIX (Genesys)
# ---------------------------------------------------------------------------
# En ALARMIX la auditoría guarda el NOMBRE del agente en `operadorUsuario`, que nunca
# matchea `usuarios.usuario`. Por eso el SP no resuelve `Equipo` ni `Agente`
# (quedan NULL) y el dashboard no puede agrupar por equipo ni por operador.
#
# Recuperamos el operador real cruzando `IdAplicativo` (= segmentId de la
# grabación) contra `ALARMIX.Grabaciones`, de donde sacamos `agentIds` (que SÍ es
# `usuarios.usuario`) y resolvemos nomina -> operadores -> equipos = el EQUIPO
# REAL (el supervisor de Acme). OJO: NO se usa `teamName_sort` de la grabación,
# que es un "equipo" ficticio de la plataforma, no el supervisor.
#
# Cubre ~84% de las auditorías ALARMIX (equipo) y ~98% (nombre del agente); el resto
# no tiene grabación asociada o el agente no está registrado en nómina/operadores.
# Es campaña-agnóstico y barato: solo procesa filas SIN Equipo que traen
# IdAplicativo, y el JOIN a ALARMIX.Grabaciones únicamente matchea segmentIds de ALARMIX
# (para el resto de campañas devuelve vacío). Requiere el índice
# IX_ALARMIX_Grabaciones_segmentId (ver scripts/migrations/2026-07-10_*).
#
# OJO con la FECHA (2026-08-25): los usuarios se reciclan, así que `agentIds` puede
# apuntar a varias personas y una misma persona pasa por varios equipos. Antes esto
# tomaba la asignación MÁS NUEVA (`o.fecha_desde DESC`), o sea el equipo de HOY para
# un llamado de hace meses. Ahora se busca la asignación vigente A LA FECHA DE LA
# GRABACIÓN (`segmentContactStartTime`), con la más cercana en el tiempo como
# fallback para no perder cobertura. Es el mismo criterio que
# `calidad.fn_ResolverOperadorAuditoria`, escrito acá a mano para que la Bandeja no
# dependa de que la migración 2026-08-25 esté aplicada.
_RESOLVER_ALARMIX_SQL = text(
    """
    WITH ranked AS (
        SELECT
            g.segmentId,
            e.nombre + ', ' + e.apellido AS equipo_real,
            g.agentName_sort            AS agent_name,
            ROW_NUMBER() OVER (
                PARTITION BY g.segmentId
                -- Primero se elige la PERSONA (la que estaba en actividad el día de
                -- la grabación) y recién después se mira si tiene equipo. Al revés
                -- —como estaba— se llenaba el Equipo con el de otra persona.
                ORDER BY ISNULL(op.vigente, 0) DESC,
                         ISNULL(op.dist, 2147483647),
                         CASE WHEN e.id IS NOT NULL THEN 0 ELSE 1 END
            ) AS rn
        FROM [Acme].[ALARMIX].[Grabaciones] g
        CROSS APPLY (
            SELECT ISNULL(g.segmentContactStartTime, g.endTime) AS f
        ) fec
        LEFT JOIN usuarios u ON u.usuario = CAST(g.agentIds AS VARCHAR(50))
        LEFT JOIN nomina   n ON n.id = u.nomina_id
        OUTER APPLY (
            SELECT TOP 1
                o.equipo_id,
                CASE WHEN fec.f >= o.fecha_desde
                      AND (fec.f < o.fecha_hasta OR o.fecha_hasta IS NULL)
                     THEN 1 ELSE 0 END AS vigente,
                ABS(DATEDIFF(DAY, o.fecha_desde, fec.f)) AS dist
            FROM operadores o
            WHERE o.legajo_id = n.id
              AND o.estado = 1
            ORDER BY CASE WHEN fec.f >= o.fecha_desde
                           AND (fec.f < o.fecha_hasta OR o.fecha_hasta IS NULL)
                          THEN 0 ELSE 1 END,
                     ABS(DATEDIFF(DAY, o.fecha_desde, fec.f))
        ) op
        LEFT JOIN equipos e ON e.id = op.equipo_id
        WHERE g.segmentId IN :ids
    )
    SELECT segmentId, equipo_real, agent_name
    FROM ranked
    WHERE rn = 1
    """
).bindparams(bindparam("ids", expanding=True))


def _enriquecer_operador_equipo_alarmix(rows: List[Dict[str, Any]]) -> None:
    """Completa in-place `Equipo`/`Agente` de las filas ALARMIX que el SP no resolvió.

    No hace nada si ninguna fila tiene Equipo vacío con IdAplicativo (caso normal
    del resto de campañas). Nunca pisa un Equipo/Agente ya resuelto por el SP."""
    pendientes = {
        (r.get("IdAplicativo") or "").strip()
        for r in rows
        if not (r.get("Equipo") or "").strip() and (r.get("IdAplicativo") or "").strip()
    }
    pendientes.discard("")
    if not pendientes:
        return

    resol: Dict[str, Dict[str, Any]] = {}
    ids = list(pendientes)
    try:
        with engine.connect() as conn:
            # Chunk defensivo por si el rango trae muchísimas auditorías.
            for i in range(0, len(ids), 1500):
                chunk = ids[i : i + 1500]
                for row in conn.execute(_RESOLVER_ALARMIX_SQL, {"ids": chunk}).mappings().all():
                    resol[row["segmentId"]] = {
                        "equipo": row["equipo_real"],
                        "agente": row["agent_name"],
                    }
    except Exception as e:
        # El enriquecido es best-effort: si falla, el dashboard sigue con lo del SP.
        logger.warning("No se pudo enriquecer Operador/Equipo ALARMIX: %s", e)
        return

    for r in rows:
        info = resol.get((r.get("IdAplicativo") or "").strip())
        if not info:
            continue
        if not (r.get("Equipo") or "").strip() and info["equipo"]:
            r["Equipo"] = info["equipo"]
        if not (r.get("Agente") or "").strip() and info["agente"]:
            r["Agente"] = info["agente"]


# ---------------------------------------------------------------------------
# Configuración de visualización — perfiles de dashboard (calidad.BandejaDashboard)
#
# Una plantilla puede tener VARIOS perfiles ("Cliente", "Operaciones", …), cada
# uno con un JSON que define, por atributo, cómo se grafica/colorea. Los ve todo
# el que entra al dashboard; solo los edita quien tenga `bandeja.config`. El
# frontend los aplica; el backend persiste y VALIDA la forma (whitelist de
# claves/valores) para no guardar basura ni exponer al front a claves inesperadas.
# ---------------------------------------------------------------------------
_CHARTS_VALIDOS = {"auto", "pie", "bar", "line", "hist", "none"}
_POLARIDADES_VALIDAS = {"mayor", "menor", "neutral"}
_METRICAS_VALIDAS = {"prom", "med", "min", "max", "tendencia"}
_KPIS_VALIDOS = {"total", "equipos", "operadores", "puntaje", "ec", "atributos"}
_RE_COLOR = re.compile(r"^#[0-9a-fA-F]{3,8}$")


def _es_color(v: str) -> bool:
    return bool(_RE_COLOR.match(v.strip())) if isinstance(v, str) else False


def _puede_configurar(user: User) -> bool:
    """¿El usuario puede EDITAR la config de visualización? El super admin siempre
    (RoleChecker ya lo deja pasar); el resto necesita el permiso `bandeja.config`,
    que nace sin asignar."""
    return bool(getattr(user, "is_super_admin", False)) or PERM_CONFIG in (user.permissions or [])


def _num_o_none(v):
    """Coerción laxa a float; None/'' -> None; texto no numérico -> None."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _validar_config_atributo(raw: Any) -> Dict[str, Any]:
    """Normaliza la config de UN atributo quedándose solo con claves conocidas
    y valores válidos. Todo lo que no cuadre se descarta silenciosamente."""
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}

    if "visible" in raw:
        out["visible"] = bool(raw.get("visible"))

    chart = str(raw.get("chart") or "").lower()
    if chart in _CHARTS_VALIDOS:
        out["chart"] = chart

    pol = str(raw.get("polaridad") or "").lower()
    if pol in _POLARIDADES_VALIDAS:
        out["polaridad"] = pol

    for k in ("meta", "umbral_verde", "umbral_amarillo"):
        if k in raw:
            n = _num_o_none(raw.get(k))
            if n is not None:
                out[k] = n

    alias = raw.get("alias")
    if isinstance(alias, str) and alias.strip():
        out["alias"] = alias.strip()[:200]

    # Valores (categorías de enum/boolean) a OCULTAR como columna en las tablas.
    vo = raw.get("valores_ocultos")
    if isinstance(vo, list):
        limpio = [str(x)[:200] for x in vo
                  if isinstance(x, (str, int, float, bool)) and str(x).strip() != ""]
        if limpio:
            out["valores_ocultos"] = limpio[:100]

    # ¿Los valores ocultos se quitan también del TOTAL (denominador)? True => las
    # columnas visibles suman 100%; False => suman menos (el resto queda afuera).
    if "ocultas_afectan_total" in raw:
        out["ocultas_afectan_total"] = bool(raw.get("ocultas_afectan_total"))

    # ¿Se muestra la categoría "Sin respuesta" (los llamados donde este atributo
    # quedó sin responder, típicamente por ser opcional)? Si la clave NO está, el
    # atributo hereda el default del dashboard (`sin_respuesta` en la raíz).
    if "mostrar_sin_respuesta" in raw:
        out["mostrar_sin_respuesta"] = bool(raw.get("mostrar_sin_respuesta"))

    # Color por valor de enum (mapa valor -> #hex).
    vc = raw.get("valores_color")
    if isinstance(vc, dict):
        limpio_vc = {str(k)[:200]: v.strip() for k, v in vc.items()
                     if isinstance(k, str) and isinstance(v, str) and _es_color(v)}
        if limpio_vc:
            out["valores_color"] = limpio_vc

    # Columnas de métrica (numérico) a ocultar en tablas.
    mo = raw.get("metricas_ocultas")
    if isinstance(mo, list):
        limpio_mo = sorted({str(x).lower() for x in mo if str(x).lower() in _METRICAS_VALIDAS})
        if limpio_mo:
            out["metricas_ocultas"] = limpio_mo

    # Ayuda/descripción para el lector (tooltip en el dashboard).
    ayuda = raw.get("ayuda")
    if isinstance(ayuda, str) and ayuda.strip():
        out["ayuda"] = ayuda.strip()[:500]

    # Enum avanzado: alias / orden / agrupamiento por valor.
    va = raw.get("valores_alias")
    if isinstance(va, dict):
        limpio_va = {str(k)[:200]: str(v).strip()[:120] for k, v in va.items()
                     if isinstance(k, str) and isinstance(v, str) and v.strip()}
        if limpio_va:
            out["valores_alias"] = limpio_va

    vo_ord = _lista_str(raw.get("valores_orden"))
    if vo_ord:
        out["valores_orden"] = vo_ord

    vg = raw.get("valores_grupo")
    if isinstance(vg, dict):
        limpio_vg = {str(k)[:200]: str(v).strip()[:120] for k, v in vg.items()
                     if isinstance(k, str) and isinstance(v, str) and v.strip()}
        if limpio_vg:
            out["valores_grupo"] = limpio_vg

    # Enum: sobre qué valor aplica la meta/umbral (su % objetivo).
    vobj = raw.get("valor_objetivo")
    if isinstance(vobj, str) and vobj.strip():
        out["valor_objetivo"] = vobj.strip()[:200]

    # Color de la línea de meta.
    if _es_color(raw.get("meta_color") or ""):
        out["meta_color"] = str(raw.get("meta_color")).strip()

    return out


def _lista_str(v, cap: int = 200, n: int = 200) -> List[str]:
    """Lista de strings no vacías, deduplicada preservando orden, con topes."""
    if not isinstance(v, list):
        return []
    out: List[str] = []
    for x in v:
        if isinstance(x, (str, int, float, bool)) and str(x).strip():
            s = str(x)[:cap]
            if s not in out:
                out.append(s)
    return out[:n]


def _validar_viz_config(payload: Any) -> Dict[str, Any]:
    """Normaliza el JSON completo de config de un perfil de dashboard. Además de
    `atributos`, admite config a nivel dashboard: orden de atributos, secciones y
    qué KPIs mostrar. Devuelve solo lo válido."""
    if not isinstance(payload, dict):
        payload = {}
    atributos_in = payload.get("atributos") or {}
    if not isinstance(atributos_in, dict):
        atributos_in = {}

    atributos_out: Dict[str, Any] = {}
    for nombre, cfg in atributos_in.items():
        if not isinstance(nombre, str) or not nombre.strip():
            continue
        limpio = _validar_config_atributo(cfg)
        if limpio:  # no guardamos entradas vacías
            atributos_out[nombre.strip()[:200]] = limpio

    out: Dict[str, Any] = {"version": 1, "atributos": atributos_out}

    orden = _lista_str(payload.get("orden_atributos"))
    if orden:
        out["orden_atributos"] = orden

    secciones_in = payload.get("secciones")
    if isinstance(secciones_in, list):
        secciones_out = []
        for s in secciones_in[:30]:
            if not isinstance(s, dict):
                continue
            nombre = str(s.get("nombre") or "").strip()[:120]
            atrs = _lista_str(s.get("atributos"))
            if nombre:
                secciones_out.append({"nombre": nombre, "atributos": atrs})
        if secciones_out:
            out["secciones"] = secciones_out

    kpis = [k for k in _lista_str(payload.get("kpis"), n=10) if k in _KPIS_VALIDOS]
    # kpis presente (aunque vacío) = "mostrar solo estos"; ausente = default (todos).
    if isinstance(payload.get("kpis"), list):
        out["kpis"] = kpis

    # Default del dashboard para la categoría "Sin respuesta": si se muestra o no en
    # gráficos y tablas. Cada atributo puede pisarlo con su `mostrar_sin_respuesta`, y
    # el lector puede alternarlo en el momento con el botón de la barra (sin guardar).
    # Ausente = False (conducta previa: los llamados sin respuesta no se dibujan).
    if "sin_respuesta" in payload:
        out["sin_respuesta"] = bool(payload.get("sin_respuesta"))

    return out


_CONFIG_VACIA = {"version": 1, "atributos": {}}


def _parse_config(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return dict(_CONFIG_VACIA)
    try:
        return _validar_viz_config(json.loads(raw))
    except (ValueError, TypeError):
        return dict(_CONFIG_VACIA)


_NOMBRES_SQL = text("""
    SELECT documento, MAX(nombre) AS nombre FROM (
        SELECT documento, nombre + ' ' + apellido AS nombre FROM nomina WHERE documento IN :ids
        UNION ALL
        SELECT documento, nombre + ' ' + apellido AS nombre FROM pagina_web.Usuarios_extra WHERE documento IN :ids
    ) x
    GROUP BY documento
""").bindparams(bindparam("ids", expanding=True))


def _nombres_usuarios(conn, ids: List[int]) -> Dict[int, str]:
    """Resuelve id (documento) -> 'Nombre Apellido' para el detalle de 'quién editó'."""
    limpio = [i for i in {i for i in ids if i is not None}]
    if not limpio:
        return {}
    try:
        rows = conn.execute(_NOMBRES_SQL, {"ids": limpio}).all()
        return {r[0]: r[1] for r in rows if r[1]}
    except Exception as e:
        logger.warning("No se pudieron resolver nombres de usuarios: %s", e)
        return {}


def _leer_perfiles(plantilla_id: int) -> List[Dict[str, Any]]:
    """Perfiles de dashboard de una plantilla (metadata + config), ordenados. El
    default primero. Lista vacía si no hay ninguno todavía."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT Id, Nombre, EsDefault, Orden, Config, ActualizadoPor, ActualizadoEn
                    FROM calidad.BandejaDashboard
                    WHERE PlantillaID = :p
                    ORDER BY EsDefault DESC, Orden, Nombre
                """),
                {"p": plantilla_id},
            ).mappings().all()
            nombres = _nombres_usuarios(conn, [r["ActualizadoPor"] for r in rows])
    except Exception as e:
        logger.warning("No se pudieron leer perfiles de dashboard(%s): %s", plantilla_id, e)
        return []
    return [
        {
            "id": r["Id"],
            "nombre": r["Nombre"],
            "es_default": bool(r["EsDefault"]),
            "orden": r["Orden"],
            "viz_config": _parse_config(r["Config"]),
            "actualizado_por": r["ActualizadoPor"],
            "actualizado_por_nombre": nombres.get(r["ActualizadoPor"]),
            "actualizado_en": r["ActualizadoEn"].isoformat() if r["ActualizadoEn"] else None,
        }
        for r in rows
    ]


def _resolver_perfil_activo(perfiles: List[Dict[str, Any]], dashboard_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """Elige el perfil a aplicar: el pedido (si existe en la plantilla), si no el
    default, si no el primero. None si no hay perfiles."""
    if not perfiles:
        return None
    if dashboard_id is not None:
        for p in perfiles:
            if p["id"] == dashboard_id:
                return p
    for p in perfiles:
        if p["es_default"]:
            return p
    return perfiles[0]


def _perfil_de_plantilla(conn, dashboard_id: int) -> Optional[int]:
    """Devuelve el PlantillaID dueño de un perfil, o None si no existe."""
    row = conn.execute(
        text("SELECT PlantillaID FROM calidad.BandejaDashboard WHERE Id = :id"),
        {"id": dashboard_id},
    ).first()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# Lookups para la cascada Empresa → Campaña → Plantilla
# Reusamos el plantillas_manager pero exponemos los endpoints bajo /bandeja/*
# para que la única dependencia de permiso del módulo sea `bandeja.view`.
# ---------------------------------------------------------------------------
@router.get("/empresas")
def listar_empresas(current_user: User = Depends(require_view)):
    if not plantillas_manager_instance:
        raise HTTPException(status_code=503, detail="Plantillas manager no inicializado.")
    return plantillas_manager_instance.empresas_disponibles(
        user_permissions=current_user.permissions,
        is_super_admin=current_user.is_super_admin,
    )


@router.get("/campanas/{empresa_id}")
def listar_campanas(empresa_id: int, current_user: User = Depends(require_view)):
    if not plantillas_manager_instance:
        raise HTTPException(status_code=503, detail="Plantillas manager no inicializado.")
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, current_user, empresa_id=empresa_id)
    return plantillas_manager_instance.campanas_disponibles(empresa_id)


@router.get("/plantillas/{campana_id}")
def listar_plantillas(campana_id: int, current_user: User = Depends(require_view)):
    if not plantillas_manager_instance:
        raise HTTPException(status_code=503, detail="Plantillas manager no inicializado.")
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, current_user, campana_id=campana_id)
    return plantillas_manager_instance.listar_plantillas(campana_id)


@router.get("/dashboard")
def dashboard(
    fecha_desde: date,
    fecha_hasta: date,
    plantilla: int = Query(..., description="ID de plantilla (obligatorio: define los atributos a graficar)"),
    empresa: Optional[int] = None,
    campana: Optional[int] = None,
    id_aplicativo: Optional[str] = None,
    base_fecha: str = Query(
        "interaccion",
        description="Sobre qué fecha aplica el rango: 'interaccion' (fecha del llamado) o 'auditoria'.",
    ),
    usuario: Optional[int] = Query(None, description="Filtro opcional por auditor; sin valor, trae los de toda la campaña"),
    dashboard_id: Optional[int] = Query(None, description="Perfil de dashboard a aplicar; sin valor usa el default de la plantilla"),
    current_user: User = Depends(require_view),
):
    """Devuelve metadata de atributos + filas para alimentar el dashboard.

    El rango de fechas se aplica sobre la fecha de interacción (por defecto) o sobre
    la fecha de auditoría, según `base_fecha`. El SP recibe ambos pares de parámetros;
    se envía sólo el que corresponde y el otro va NULL (sin filtro).

    El recorte por auditor es opt-in (`usuario`): por defecto se devuelve la campaña
    completa dentro del alcance por empresa del caller.
    """
    # Alcance por empresa (permisos template:<empresa>): la plantilla es
    # obligatoria y resuelve la empresa; empresa/campana se validan si vienen.
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, current_user, empresa_id=empresa,
                              campana_id=campana, plantilla_id=plantilla)

    effective_usuario = usuario
    filtrado_por_user = effective_usuario is not None

    por_interaccion = (base_fecha or "interaccion").lower() != "auditoria"

    sp = text("""
        EXEC calidad.sp_ObtenerAuditoriasFiltradas
            @AuditorUsuarioID = :usuario,
            @CampanaID = :campana,
            @EmpresaID = :empresa,
            @PlantillaID = :plantilla,
            @FechaDesde = :fecha_desde,
            @FechaHasta = :fecha_hasta,
            @FechaInteraccionDesde = :fi_desde,
            @FechaInteraccionHasta = :fi_hasta,
            @IdAplicativo = :id_aplicativo,
            @IncluirTranscripcion = 0,
            @IncluirResponseThoughts = 0
    """)

    params = {
        "usuario": effective_usuario,
        "campana": campana,
        "empresa": empresa,
        "plantilla": plantilla,
        "fecha_desde": None if por_interaccion else fecha_desde,
        "fecha_hasta": None if por_interaccion else fecha_hasta,
        "fi_desde": fecha_desde if por_interaccion else None,
        "fi_hasta": fecha_hasta if por_interaccion else None,
        "id_aplicativo": id_aplicativo,
    }

    try:
        with engine.connect() as conn:
            result = conn.execute(sp, params)
            columns_order = list(result.keys())
            rows = [dict(r) for r in result.mappings().all()]

        # ALARMIX guarda el nombre del agente como operadorUsuario y el SP no resuelve
        # Equipo/Agente: los completamos desde la grabación (ver función).
        _enriquecer_operador_equipo_alarmix(rows)

        atributos = _atributos_de_plantilla(plantilla)
        perfiles = _leer_perfiles(plantilla)
        activo = _resolver_perfil_activo(perfiles, dashboard_id)

        return {
            "atributos": atributos,
            "columns": columns_order,
            "data": rows,
            "filtrado_por_user": filtrado_por_user,
            # Perfiles de dashboard de la plantilla (con su config, para cambiar de
            # perfil sin re-consultar) + el activo (su config va también en viz_config).
            "dashboards": perfiles,
            "dashboard_id": activo["id"] if activo else None,
            "viz_config": activo["viz_config"] if activo else dict(_CONFIG_VACIA),
            "puede_configurar": _puede_configurar(current_user),
        }

    except Exception as e:
        logger.exception("Error en /bandeja/dashboard")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Perfiles de dashboard — listar / crear / editar / borrar / duplicar
# ---------------------------------------------------------------------------
_NOMBRE_MAX = 120


def _nombre_perfil(payload: Dict[str, Any], por_defecto: str = "Nuevo dashboard") -> str:
    nombre = str(payload.get("nombre") or "").strip()
    return (nombre or por_defecto)[:_NOMBRE_MAX]


@router.get("/dashboards/{plantilla}")
def listar_dashboards(plantilla: int, current_user: User = Depends(require_view)):
    """Perfiles de dashboard de una plantilla (con su config). Cualquiera que
    acceda al dashboard los ve; el flag dice si puede editarlos."""
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, current_user, plantilla_id=plantilla)
    return {
        "dashboards": _leer_perfiles(plantilla),
        "puede_configurar": _puede_configurar(current_user),
    }


@router.post("/dashboards/{plantilla}")
def crear_dashboard(
    plantilla: int,
    payload: Dict[str, Any] = Body(...),
    current_user: User = Depends(require_config),
):
    """Crea un perfil nuevo para la plantilla. Si es el primero, queda default."""
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, current_user, plantilla_id=plantilla)

    nombre = _nombre_perfil(payload)
    limpio = _validar_viz_config(payload.get("viz_config", {}))
    config_json = json.dumps(limpio, ensure_ascii=False)
    try:
        with engine.begin() as conn:
            existe = conn.execute(
                text("SELECT COUNT(*) FROM calidad.BandejaDashboard WHERE PlantillaID = :p"),
                {"p": plantilla},
            ).scalar() or 0
            es_default = 1 if existe == 0 else (1 if payload.get("es_default") else 0)
            if es_default:
                conn.execute(
                    text("UPDATE calidad.BandejaDashboard SET EsDefault = 0 WHERE PlantillaID = :p"),
                    {"p": plantilla},
                )
            nuevo_id = conn.execute(
                text("""
                    INSERT INTO calidad.BandejaDashboard (PlantillaID, Nombre, EsDefault, Orden, Config, ActualizadoPor)
                    OUTPUT INSERTED.Id
                    VALUES (:p, :nombre, :def, :orden, :cfg, :uid)
                """),
                {"p": plantilla, "nombre": nombre, "def": es_default, "orden": existe,
                 "cfg": config_json, "uid": current_user.usuario},
            ).scalar()
    except Exception as e:
        logger.exception("Error creando perfil de dashboard(%s)", plantilla)
        raise HTTPException(status_code=500, detail=str(e))
    return {"id": nuevo_id, "dashboards": _leer_perfiles(plantilla)}


@router.put("/dashboards/perfil/{dashboard_id}")
def editar_dashboard(
    dashboard_id: int,
    payload: Dict[str, Any] = Body(...),
    current_user: User = Depends(require_config),
):
    """Actualiza un perfil: su config y/o nombre y/o marcarlo como default. Solo
    se tocan las claves presentes en el payload."""
    with engine.begin() as conn:
        plantilla = _perfil_de_plantilla(conn, dashboard_id)
        if plantilla is None:
            raise HTTPException(status_code=404, detail="Perfil de dashboard inexistente.")
        exigir_acceso_empresa(conn, current_user, plantilla_id=plantilla)

        sets = ["ActualizadoPor = :uid", "ActualizadoEn = SYSUTCDATETIME()"]
        vals: Dict[str, Any] = {"id": dashboard_id, "uid": current_user.usuario}
        if "viz_config" in payload:
            vals["cfg"] = json.dumps(_validar_viz_config(payload.get("viz_config") or {}), ensure_ascii=False)
            sets.append("Config = :cfg")
        if "nombre" in payload:
            vals["nombre"] = _nombre_perfil(payload)
            sets.append("Nombre = :nombre")
        if payload.get("es_default"):
            conn.execute(
                text("UPDATE calidad.BandejaDashboard SET EsDefault = 0 WHERE PlantillaID = :p"),
                {"p": plantilla},
            )
            sets.append("EsDefault = 1")
        try:
            conn.execute(
                text(f"UPDATE calidad.BandejaDashboard SET {', '.join(sets)} WHERE Id = :id"),
                vals,
            )
        except Exception as e:
            logger.exception("Error editando perfil de dashboard(%s)", dashboard_id)
            raise HTTPException(status_code=500, detail=str(e))
    return {"dashboards": _leer_perfiles(plantilla)}


@router.post("/dashboards/perfil/{dashboard_id}/duplicar")
def duplicar_dashboard(
    dashboard_id: int,
    payload: Dict[str, Any] = Body(default={}),
    current_user: User = Depends(require_config),
):
    """Duplica un perfil (misma config, nombre nuevo). Cubre 'copiar config'."""
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT PlantillaID, Nombre, Config FROM calidad.BandejaDashboard WHERE Id = :id"),
            {"id": dashboard_id},
        ).first()
        if not row:
            raise HTTPException(status_code=404, detail="Perfil de dashboard inexistente.")
        plantilla, nombre_orig, config = row[0], row[1], row[2]
        exigir_acceso_empresa(conn, current_user, plantilla_id=plantilla)
        nombre = _nombre_perfil(payload, f"{nombre_orig} (copia)")
        orden = conn.execute(
            text("SELECT COUNT(*) FROM calidad.BandejaDashboard WHERE PlantillaID = :p"),
            {"p": plantilla},
        ).scalar() or 0
        try:
            nuevo_id = conn.execute(
                text("""
                    INSERT INTO calidad.BandejaDashboard (PlantillaID, Nombre, EsDefault, Orden, Config, ActualizadoPor)
                    OUTPUT INSERTED.Id
                    VALUES (:p, :nombre, 0, :orden, :cfg, :uid)
                """),
                {"p": plantilla, "nombre": nombre, "orden": orden, "cfg": config, "uid": current_user.usuario},
            ).scalar()
        except Exception as e:
            logger.exception("Error duplicando perfil de dashboard(%s)", dashboard_id)
            raise HTTPException(status_code=500, detail=str(e))
    return {"id": nuevo_id, "dashboards": _leer_perfiles(plantilla)}


@router.delete("/dashboards/perfil/{dashboard_id}")
def borrar_dashboard(dashboard_id: int, current_user: User = Depends(require_config)):
    """Borra un perfil. Si era el default y quedan otros, asciende al primero."""
    with engine.begin() as conn:
        plantilla = _perfil_de_plantilla(conn, dashboard_id)
        if plantilla is None:
            raise HTTPException(status_code=404, detail="Perfil de dashboard inexistente.")
        exigir_acceso_empresa(conn, current_user, plantilla_id=plantilla)
        era_default = conn.execute(
            text("SELECT EsDefault FROM calidad.BandejaDashboard WHERE Id = :id"),
            {"id": dashboard_id},
        ).scalar()
        conn.execute(text("DELETE FROM calidad.BandejaDashboard WHERE Id = :id"), {"id": dashboard_id})
        if era_default:
            siguiente = conn.execute(
                text("SELECT TOP 1 Id FROM calidad.BandejaDashboard WHERE PlantillaID = :p ORDER BY Orden, Nombre"),
                {"p": plantilla},
            ).scalar()
            if siguiente:
                conn.execute(
                    text("UPDATE calidad.BandejaDashboard SET EsDefault = 1 WHERE Id = :id"),
                    {"id": siguiente},
                )
    return {"dashboards": _leer_perfiles(plantilla)}


# ---------------------------------------------------------------------------
# Comparativa de duración por tipificación (Coordina turno vs No coordina turno)
#
# El SP de auditorías NO trae la duración del llamado. Para responder "¿cuánto
# dura una llamada que coordina turno vs una que no?" vamos directo a Orion
# (ContactCenter, vía ORION_LINK) y agregamos el tiempo hablado agrupado por la
# tipificación. Es una población DISTINTA de la muestra auditada: son todas las
# llamadas del/los skill(s) de la campaña en el rango (coordinen o no), no sólo
# las 200 auditadas. Por eso vive en su propio endpoint y no en /dashboard.
# ---------------------------------------------------------------------------
def _sql_str(valor) -> str:
    """Escapa un literal para incrustarlo en SQL (comilla simple -> doble)."""
    return "'" + str(valor).replace("'", "''") + "'"


def _sql_in(valores) -> str:
    return ",".join(_sql_str(v) for v in valores)


def _clasificar_tipificacion(categoria: Optional[str], subcategoria: Optional[str]) -> str:
    """Mapea la tipificación de Orion a 3 baldes: coordina / no coordina / otra.

    El cliente define la conversión por la tipificación 'Coordina turno' /
    'No coordina turno'. Se chequea 'no coordina' primero porque contiene
    'coordina' como subcadena."""
    texto = " ".join(filter(None, [categoria, subcategoria])).lower()
    # Normalizar saltos/espacios sobrantes que Orion mete en las descripciones.
    texto = " ".join(texto.replace("\r", " ").replace("\n", " ").split())
    if "no coordina" in texto:
        return "No coordina turno"
    if "coordina" in texto and "turno" in texto:
        return "Coordina turno"
    return "Otra"


@router.get("/duracion_tipificacion")
def duracion_tipificacion(
    fecha_desde: date,
    fecha_hasta: date,
    campana: int = Query(..., description="ID de campaña (resuelve los skills de Orion a consultar)"),
    current_user: User = Depends(require_view),
):
    """Duración promedio de las llamadas de la campaña, separada por si la
    tipificación indica que se coordinó turno o no.

    Devuelve { buckets: [{label, cantidad, duracion_prom_seg, dur_min, dur_max}],
    detalle: [{categoria, subcategoria, cantidad, duracion_prom_seg, bucket}],
    skills: [...] }. Si el linked server de Orion no responde, devuelve 200 con
    `error` para que el resto del dashboard no se rompa."""
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, current_user, campana_id=campana)
    try:
        # 1) Skills (Habilidades de Orion) asignados a la campaña de auditoría.
        with engine.connect() as conn:
            skills = [
                r[0]
                for r in conn.execute(
                    text("SELECT Nombre FROM calidad.Skills WHERE CampanaID = :c AND IsActive = 1"),
                    {"c": campana},
                ).fetchall()
            ]

        if not skills:
            return {"buckets": [], "detalle": [], "skills": [],
                    "error": "La campaña no tiene skills de Orion asignados."}

        fd = fecha_desde.strftime("%Y-%m-%d")
        fh = fecha_hasta.strftime("%Y-%m-%d")

        # 2) Query remota agregada (corre en Orion vía OPENQUERY). Las comillas
        #    simples se doblan UNA vez al envolver en OPENQUERY('...').
        remota = f"""
            SELECT
                cat.Descripcion AS Categoria,
                sub.Descripcion AS Subcategoria,
                COUNT(*) AS Cantidad,
                AVG(CAST(e.TiempoHablado AS FLOAT)) AS DurProm,
                MIN(e.TiempoHablado) AS DurMin,
                MAX(e.TiempoHablado) AS DurMax
            FROM ContactCenter.dbo.EstadPorLlamada e
            LEFT JOIN ContactCenter.dbo.DatosPorLlamada dps
                ON dps.Call_Id = e.ID_Llamada AND dps.Clave = 'CODSUBCAT'
            LEFT JOIN ContactCenter.dbo.SubCategorias sub
                ON sub.CodSubcategoria = COALESCE(TRY_CAST(dps.Valor AS INT), NULLIF(e.Codificacion, 0))
            LEFT JOIN ContactCenter.dbo.Categorias cat ON cat.CodCategoria = sub.CodCategoria
            WHERE e.Agente <> 0 AND e.TiempoHablado > 0
              AND e.Fecha >= {_sql_str(fd)} AND e.Fecha < DATEADD(DAY, 1, {_sql_str(fh)})
              AND EXISTS (
                  SELECT 1
                  FROM ContactCenter.dbo.HabilidadesPorAgenteLog hl
                  JOIN ContactCenter.dbo.Habilidades h ON h.ID = hl.HabilidadID
                  WHERE hl.AgenteID = e.Agente
                    AND hl.FechaInicio <= e.Fecha
                    AND (hl.FechaFin IS NULL OR hl.FechaFin >= e.Fecha)
                    AND h.Nombre IN ({_sql_in(skills)})
              )
            GROUP BY cat.Descripcion, sub.Descripcion
        """

        query = text(f"SELECT * FROM OPENQUERY(ORION_LINK, '{remota.replace(chr(39), chr(39) * 2)}') AS oq")

        with engine.connect() as conn:
            filas = [dict(r) for r in conn.execute(query).mappings().all()]

        # 3) Clasificar y agregar (promedio ponderado por cantidad).
        buckets: Dict[str, Dict[str, Any]] = {}
        detalle: List[Dict[str, Any]] = []
        for f in filas:
            cat = (f.get("Categoria") or "").strip()
            sub = (f.get("Subcategoria") or "").strip()
            cantidad = int(f.get("Cantidad") or 0)
            dur_prom = float(f.get("DurProm") or 0.0)
            if cantidad <= 0:
                continue
            label = _clasificar_tipificacion(cat, sub)
            b = buckets.setdefault(label, {"cantidad": 0, "suma": 0.0, "dur_min": None, "dur_max": None})
            b["cantidad"] += cantidad
            b["suma"] += cantidad * dur_prom
            dmin = f.get("DurMin")
            dmax = f.get("DurMax")
            if dmin is not None:
                b["dur_min"] = dmin if b["dur_min"] is None else min(b["dur_min"], dmin)
            if dmax is not None:
                b["dur_max"] = dmax if b["dur_max"] is None else max(b["dur_max"], dmax)
            detalle.append({
                "categoria": cat, "subcategoria": sub, "cantidad": cantidad,
                "duracion_prom_seg": round(dur_prom, 1), "bucket": label,
            })

        orden = {"Coordina turno": 0, "No coordina turno": 1, "Otra": 2}
        salida = [
            {
                "label": label,
                "cantidad": b["cantidad"],
                "duracion_prom_seg": round(b["suma"] / b["cantidad"], 1) if b["cantidad"] else None,
                "dur_min": b["dur_min"],
                "dur_max": b["dur_max"],
            }
            for label, b in sorted(buckets.items(), key=lambda kv: orden.get(kv[0], 9))
        ]
        detalle.sort(key=lambda d: (orden.get(d["bucket"], 9), -d["cantidad"]))

        return {"buckets": salida, "detalle": detalle, "skills": skills}

    except Exception as e:
        # Orion es un linked server externo: si falla, no tiramos abajo el dashboard.
        logger.warning("No se pudo calcular duracion_tipificacion: %s", e)
        return {"buckets": [], "detalle": [], "skills": [], "error": str(e)}


# ---------------------------------------------------------------------------
# Asistente Analítico del Dashboard (Chatbot de Auditorías)
# ---------------------------------------------------------------------------

class TurnoAsistenteDashboard(BaseModel):
    rol: str  # 'user' | 'model' | 'bot'
    texto: str


class ConsultaAsistenteDashboard(BaseModel):
    pregunta: str
    contexto: Dict[str, Any]
    # Fallback para consultas sueltas (sin hilo): cuando viene `conversacion_id`
    # el historial se rearma desde la base y esto se ignora.
    historial: Optional[List[TurnoAsistenteDashboard]] = None
    # Hilo al que pertenece la consulta. Si no viene, se crea uno y su id vuelve
    # en el header X-Conversacion-Id.
    conversacion_id: Optional[int] = None
    # Foto de los filtros del dashboard, para poder reabrir el chat sabiendo
    # sobre qué datos se conversó. Solo se guarda al crear el hilo.
    alcance: Optional[Dict[str, Any]] = None


def _enriquecer_contexto(
    pregunta: str,
    contexto: Dict[str, Any],
    alcance: Optional[Dict[str, Any]],
    current_user: User,
) -> Dict[str, Any]:
    """Junta para ESTA pregunta lo que el navegador no puede saber.

    Tres cosas, todas decididas sin llamar al modelo (ver `asistente_contexto`):
    la muestra de llamados elegida por relevancia, las transcripciones de los
    llamados que la pregunta señala y los KPIs del período anterior.

    **Acá se valida el alcance por empresa**: la plantilla llega desde el
    navegador (dentro de `alcance`), así que antes de leer una sola transcripción
    se comprueba que el usuario tenga permiso sobre ella. Si no lo tiene, o si el
    alcance vino incompleto, se responde igual con lo que ya mandó el dashboard:
    degradar la respuesta es preferible a no contestar, y no se filtra nada.
    """
    alcance = alcance if isinstance(alcance, dict) else {}
    foco = asistente_contexto.analizar_pregunta(pregunta, contexto)
    filas, criterios = asistente_contexto.seleccionar_filas(contexto.get("filas") or [], foco)
    extra: Dict[str, Any] = {"filas_seleccionadas": filas, "criterios_muestra": criterios}

    def _id(clave: str) -> Optional[int]:
        try:
            return int(alcance.get(clave))
        except (TypeError, ValueError):
            return None

    plantilla_id = _id("plantilla_id")
    if not plantilla_id:
        return extra

    try:
        with engine.connect() as conn:
            exigir_acceso_empresa(conn, current_user, plantilla_id=plantilla_id)
    except Exception as e:
        logger.warning(
            "Asistente Dashboard: sin acceso a la plantilla %s para %s (%s); se responde solo con el contexto del navegador.",
            plantilla_id, current_user.usuario, e,
        )
        return extra

    empresa_id, campana_id = _id("empresa_id"), _id("campana_id")

    ids = asistente_contexto.ids_para_transcribir(contexto, foco)
    if ids:
        extra["transcripciones"] = asistente_contexto.transcripciones(
            ids, plantilla_id, empresa_id, campana_id
        )

    if foco.quiere_comparativa and alcance.get("fecha_desde") and alcance.get("fecha_hasta"):
        extra["comparativa"] = asistente_contexto.comparativa_periodo_anterior(
            plantilla_id=plantilla_id,
            desde=alcance["fecha_desde"],
            hasta=alcance["fecha_hasta"],
            base_fecha=alcance.get("base_fecha") or "interaccion",
            empresa_id=empresa_id,
            campana_id=campana_id,
            atributos=_atributos_de_plantilla(plantilla_id),
        )
    return extra


async def _stream_guardando(generador, conv_id: Optional[int]):
    """Reenvía el stream al navegador y, al terminar, guarda la respuesta.

    El `finally` también corre cuando el usuario cancela a mitad de camino: en
    ese caso se guarda lo que alcanzó a leer, que es exactamente lo que quedó en
    pantalla."""
    partes: List[str] = []
    try:
        async for chunk in generador:
            partes.append(chunk)
            yield chunk
    finally:
        if conv_id:
            texto = "".join(partes).strip()
            if texto:
                asistente_conversaciones.agregar_mensaje(conv_id, "bot", texto)


@router.post("/asistente/stream")
async def asistente_dashboard_stream(
    consulta: ConsultaAsistenteDashboard,
    current_user: User = Depends(require_view),
):
    """Responde consultas analíticas en streaming sobre las auditorías cargadas."""
    try:
        pregunta, _ = asistente_dashboard.validar(consulta.pregunta, consulta.contexto)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info(
        "Asistente Dashboard: usuario %s pregunta '%s...'",
        current_user.usuario, pregunta[:60],
    )

    raw_campana = consulta.contexto.get("campana_id") or consulta.contexto.get("campana")
    campana_id: Optional[int] = None
    if raw_campana is not None:
        try:
            campana_id = int(raw_campana)
        except (ValueError, TypeError):
            campana_id = None

    # --- Hilo de conversación -------------------------------------------------
    conv_id = consulta.conversacion_id
    if conv_id is not None and not asistente_conversaciones.pertenece(current_user.usuario, conv_id):
        raise HTTPException(status_code=404, detail="La conversación no existe o no es tuya.")

    if conv_id is None:
        # Primer turno: el hilo nace titulado con la pregunta y con la foto de
        # los filtros. Si la base falla, la consulta sigue igual: no guardar el
        # historial es molesto, quedarse sin respuesta es peor.
        try:
            alcance = consulta.alcance if isinstance(consulta.alcance, dict) else None
            conv_id = asistente_conversaciones.crear(
                user_id=current_user.usuario,
                titulo=asistente_conversaciones.titulo_desde_pregunta(pregunta),
                alcance=alcance,
                plantilla_id=(alcance or {}).get("plantilla_id"),
                campana_id=(alcance or {}).get("campana_id") or campana_id,
            )
        except RuntimeError:
            conv_id = None

    if conv_id:
        historial = asistente_conversaciones.historial_para_modelo(current_user.usuario, conv_id)
        asistente_conversaciones.agregar_mensaje(conv_id, "user", pregunta)
    else:
        historial = [t.model_dump() for t in (consulta.historial or [])]

    # Las consultas del enriquecimiento son SQL sincrónico (el SP de auditorías
    # puede tardar segundos sobre un período largo): van a un hilo para no frenar
    # el event loop mientras otros usuarios están recibiendo su stream.
    extra = await asyncio.to_thread(
        _enriquecer_contexto, pregunta, consulta.contexto, consulta.alcance, current_user
    )

    headers = {"X-Accel-Buffering": "no", "Cache-Control": "no-cache"}
    if conv_id:
        headers["X-Conversacion-Id"] = str(conv_id)
    # Qué datos extra se usaron para responder. El frontend lo muestra debajo de
    # la respuesta: si el asistente escuchó dos llamados o miró el mes anterior,
    # el supervisor tiene que saberlo (y así se entera de que puede pedirlo).
    headers["X-Asistente-Contexto"] = json.dumps({
        "transcripciones": len(extra.get("transcripciones") or []),
        "comparativa": bool(extra.get("comparativa")),
        "filas": len(extra.get("filas_seleccionadas") or []),
    })

    return StreamingResponse(
        _stream_guardando(
            asistente_dashboard.responder_stream(
                pregunta=pregunta,
                contexto=consulta.contexto,
                user_id=current_user.usuario,
                campana_id=campana_id,
                historial=historial,
                extra=extra,
                conversacion_id=conv_id,
            ),
            conv_id,
        ),
        media_type="text/plain; charset=utf-8",
        headers=headers,
    )


@router.post("/asistente/trabajo", status_code=status.HTTP_202_ACCEPTED)
async def asistente_dashboard_iniciar_trabajo(
    consulta: ConsultaAsistenteDashboard,
    current_user: User = Depends(require_view),
):
    """Inicia una consulta analítica en segundo plano y devuelve job_id para polling."""
    try:
        pregunta, _ = asistente_dashboard.validar(consulta.pregunta, consulta.contexto)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info(
        "Asistente Dashboard (Job): usuario %s pregunta '%s...'",
        current_user.usuario, pregunta[:60],
    )

    raw_campana = consulta.contexto.get("campana_id") or consulta.contexto.get("campana")
    campana_id: Optional[int] = None
    if raw_campana is not None:
        try:
            campana_id = int(raw_campana)
        except (ValueError, TypeError):
            campana_id = None

    # Hilo de conversación
    conv_id = consulta.conversacion_id
    if conv_id is not None and not asistente_conversaciones.pertenece(current_user.usuario, conv_id):
        raise HTTPException(status_code=404, detail="La conversación no existe o no es tuya.")

    if conv_id is None:
        try:
            alcance = consulta.alcance if isinstance(consulta.alcance, dict) else None
            conv_id = asistente_conversaciones.crear(
                user_id=current_user.usuario,
                titulo=asistente_conversaciones.titulo_desde_pregunta(pregunta),
                alcance=alcance,
                plantilla_id=(alcance or {}).get("plantilla_id"),
                campana_id=(alcance or {}).get("campana_id") or campana_id,
            )
        except RuntimeError:
            conv_id = None

    if conv_id:
        historial = asistente_conversaciones.historial_para_modelo(current_user.usuario, conv_id)
        asistente_conversaciones.agregar_mensaje(conv_id, "user", pregunta)
    else:
        historial = [t.model_dump() for t in (consulta.historial or [])]

    job_id = asistente_jobs.crear_trabajo(
        user_id=str(current_user.usuario),
        conversacion_id=conv_id,
        pregunta=pregunta,
    )

    asyncio.create_task(
        asistente_dashboard.ejecutar_trabajo_asistente(
            job_id=job_id,
            pregunta=pregunta,
            contexto=consulta.contexto,
            alcance=consulta.alcance if isinstance(consulta.alcance, dict) else None,
            current_user=current_user,
            conv_id=conv_id,
            campana_id=campana_id,
            historial=historial,
            enriquecer_fn=_enriquecer_contexto,
        )
    )

    return {
        "job_id": job_id,
        "conversacion_id": conv_id,
        "estado": asistente_jobs.ESTADO_EN_CURSO,
        "fase": asistente_jobs.FASE_ENRIQUECIENDO,
    }


@router.get("/asistente/trabajo/{job_id}")
async def asistente_dashboard_estado_trabajo(
    job_id: str,
    current_user: User = Depends(require_view),
):
    """Consulta el estado y texto acumulado de un trabajo en segundo plano."""
    job = asistente_jobs.obtener_trabajo(job_id, user_id=str(current_user.usuario))
    if job is None:
        raise HTTPException(status_code=404, detail="El trabajo no existe o ha expirado.")
    return asistente_jobs.para_respuesta(job)


# --- Historial de conversaciones del asistente ------------------------------
# Todo el CRUD es sobre los hilos del usuario logueado: no hay forma de leer ni
# tocar el chat de otro (ver asistente_conversaciones).

@router.get("/asistente/conversaciones")
def listar_conversaciones_asistente(
    q: Optional[str] = Query(None, description="Busca en el título y en el texto de los mensajes"),
    limit: int = Query(asistente_conversaciones.MAX_LISTADO, ge=1, le=asistente_conversaciones.MAX_LISTADO),
    current_user: User = Depends(require_view),
):
    try:
        return {"conversaciones": asistente_conversaciones.listar(current_user.usuario, q=q, limit=limit)}
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/asistente/conversaciones/{conv_id}")
def obtener_conversacion_asistente(
    conv_id: int,
    current_user: User = Depends(require_view),
):
    try:
        conv = asistente_conversaciones.obtener(current_user.usuario, conv_id)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not conv:
        raise HTTPException(status_code=404, detail="La conversación no existe o no es tuya.")
    return conv


@router.put("/asistente/conversaciones/{conv_id}")
def editar_conversacion_asistente(
    conv_id: int,
    payload: Dict[str, Any] = Body(...),
    current_user: User = Depends(require_view),
):
    """Renombra el hilo y/o actualiza su alcance."""
    titulo = payload.get("titulo") if "titulo" in payload else None
    alcance = payload.get("alcance") if isinstance(payload.get("alcance"), dict) else None
    try:
        ok = asistente_conversaciones.actualizar(
            current_user.usuario, conv_id, titulo=titulo, alcance=alcance
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="La conversación no existe o no es tuya.")
    return {"ok": True}


@router.delete("/asistente/conversaciones/{conv_id}")
def borrar_conversacion_asistente(
    conv_id: int,
    current_user: User = Depends(require_view),
):
    try:
        ok = asistente_conversaciones.eliminar(current_user.usuario, conv_id)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="La conversación no existe o no es tuya.")
    return {"ok": True}
