"""Planificador — salud del plan: ¿los datos que alimentan el número están al día?

`GET /planificador/salud` contesta, antes de que alguien discuta un número, hasta
dónde llegó cada fuente (IVR del cliente, informe de skills, registro y malla de
RRHH, clima, forecast del cliente, cortes del ENRE, reparto) y qué migraciones del
planificador faltan correr en la base.

Existe porque las corridas repetían los mismos avisos durante días (medido el
2026-09-15: las últimas 25 traían los mismos dos) sin que se supiera si el problema
era del modelo o de un dato que no había llegado.

Cada chequeo corre en su PROPIA conexión y queda aislado: que una tabla falte o una
consulta falle marca esa fuente con estado "error" y no tumba el resto. Es la única
razón por la que acá sí se atrapan excepciones de SQL: la conexión se descarta, no
hay transacción de una corrida que proteger. Qué cuenta como "atrasado" vive en
funciones puras, testeables sin base.
"""

import logging
import time as reloj
from datetime import date, datetime, time, timedelta
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app import planificador_datos as pdatos

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ umbrales

# El informe de skills carga cada hora y viene con CEROS hasta el final del día
# (medido: a las 14:09 el MAX(Intervalo) era 20:30 y el último con llamadas 13:00),
# así que se mira el último intervalo con llamadas y sólo en horario de operación.
HORA_INICIO_SKILLS = 8
HORA_FIN_SKILLS = 23
MAX_HORAS_ATRASO_SKILLS = 2.0

# La malla publicada tiene que cubrir al menos el horizonte en que se piden refuerzos.
DIAS_HORIZONTE_MALLA = 14
# El pronóstico meteorológico llega a 16 días; con menos de 7 el plan de la semana
# sale sin corrección de clima.
DIAS_FUTUROS_CLIMA = 7
DIAS_HORIZONTE_FORECAST = 14
# El script de cortes corre cada hora.
MAX_HORAS_ATRASO_CORTES = 3.0

# La pantalla lo pide en cada carga de la configuración: no hace falta más fresco.
SEGUNDOS_CACHE = 300

Clasificacion = Tuple[str, Optional[str], str]   # (estado, esperado, detalle)


# ------------------------------------------------- clasificación (funciones puras)

def clasificar_demanda_total(ultimo_dia: Optional[date], hoy: date) -> Clasificacion:
    """IVR completo del cliente: carga nocturna, tiene que llegar al menos a ayer."""
    ayer = hoy - timedelta(days=1)
    if ultimo_dia is None:
        return "falta", str(ayer), "No hay llamadas cargadas."
    if ultimo_dia >= ayer:
        return "ok", str(ayer), "Al día."
    return "atrasado", str(ayer), f"Le faltan {(ayer - ultimo_dia).days} día(s): llega al {ultimo_dia}."


def clasificar_informe_skills(ultimo_momento: Optional[datetime], ahora: datetime) -> Clasificacion:
    """Informe de skills: carga horaria. Atrasado con más de 2 h, sólo de 8 a 23 h."""
    esperado = f"con llamadas hace menos de {MAX_HORAS_ATRASO_SKILLS:g} h (de 8 a 23 h)"
    if ultimo_momento is None:
        return "falta", esperado, "No hay intervalos con llamadas."
    if not HORA_INICIO_SKILLS <= ahora.hour < HORA_FIN_SKILLS:
        return "ok", esperado, "Fuera del horario en que se evalúa."
    horas = (ahora - ultimo_momento).total_seconds() / 3600
    if horas > MAX_HORAS_ATRASO_SKILLS:
        return "atrasado", esperado, f"El último intervalo con llamadas es de hace {horas:.1f} h."
    return "ok", esperado, "Al día."


def clasificar_registro_rrhh(ultimo_dia: Optional[date], hoy: date) -> Clasificacion:
    """Registro de RRHH (días cerrados): tiene que llegar a ayer."""
    ayer = hoy - timedelta(days=1)
    if ultimo_dia is None:
        return "falta", str(ayer), "No hay horas trabajadas registradas."
    if ultimo_dia >= ayer:
        return "ok", str(ayer), "Al día."
    return ("atrasado", str(ayer),
            f"Le faltan {(ayer - ultimo_dia).days} día(s): el cotejo de dotación no ve esos días.")


def clasificar_malla_publicada(ultimo_dia: Optional[date], hoy: date) -> Clasificacion:
    """Malla publicada: tiene que cubrir los próximos 14 días."""
    meta = hoy + timedelta(days=DIAS_HORIZONTE_MALLA)
    if ultimo_dia is None:
        return "falta", str(meta), "No hay turnos publicados."
    if ultimo_dia >= meta:
        return "ok", str(meta), "Cubre el horizonte."
    return ("atrasado", str(meta),
            f"Llega al {ultimo_dia}: después de esa fecha la brecha sale sin citados.")


def clasificar_clima(ultimo_obs: Optional[date], ultimo_pron: Optional[date],
                     hoy: date) -> Clasificacion:
    """Clima: observado hasta ayer y pronóstico al menos una semana adelante."""
    ayer = hoy - timedelta(days=1)
    meta = hoy + timedelta(days=DIAS_FUTUROS_CLIMA)
    esperado = f"observado ≥ {ayer}, pronóstico ≥ {meta}"
    if ultimo_obs is None and ultimo_pron is None:
        return "falta", esperado, "No hay clima cargado."
    fallas = []
    if ultimo_obs is None or ultimo_obs < ayer:
        fallas.append(f"el observado llega a {ultimo_obs or 'nunca'}")
    if ultimo_pron is None or ultimo_pron < meta:
        fallas.append(f"el pronóstico llega a {ultimo_pron or 'nunca'}")
    if fallas:
        return "atrasado", esperado, "; ".join(fallas).capitalize() + "."
    return "ok", esperado, "Al día."


def clasificar_forecast_cliente(ultimo_dia: Optional[date], hoy: date) -> Clasificacion:
    """Forecast que manda el cliente: al menos 14 días adelante."""
    meta = hoy + timedelta(days=DIAS_HORIZONTE_FORECAST)
    if ultimo_dia is None:
        return "falta", str(meta), "El cliente no mandó pronóstico."
    if ultimo_dia >= meta:
        return "ok", str(meta), "Cubre el horizonte."
    return "atrasado", str(meta), f"Llega al {ultimo_dia}."


def clasificar_cortes_enre(ultimo_momento: Optional[datetime], ahora: datetime) -> Clasificacion:
    """Cortes del ENRE: el script corre cada hora."""
    esperado = f"hace menos de {MAX_HORAS_ATRASO_CORTES:g} h"
    if ultimo_momento is None:
        return "falta", esperado, "No hay lecturas cargadas."
    horas = (ahora - ultimo_momento).total_seconds() / 3600
    if horas > MAX_HORAS_ATRASO_CORTES:
        return "atrasado", esperado, f"La última lectura es de hace {horas:.1f} h."
    return "ok", esperado, "Al día."


def clasificar_reparto(skills_con_demanda: Sequence[str],
                       skills_sin_tramo: Sequence[str]) -> Clasificacion:
    """Reparto: todo skill con demanda del cliente necesita tramo vigente (aunque sea 0%)."""
    esperado = "tramo vigente en cada skill con demanda"
    if skills_sin_tramo:
        return ("falta", esperado,
                "Sin tramo vigente (quedan fuera del pronóstico): " + ", ".join(sorted(skills_sin_tramo)))
    if not skills_con_demanda:
        return "ok", esperado, "Ningún skill tuvo demanda en 30 días."
    return "ok", esperado, "Todos los skills con demanda tienen tramo."


def resumen_de_salud(fuentes: Sequence[dict], migraciones: Sequence[dict]) -> dict:
    return {
        "atrasadas": sum(1 for f in fuentes if f["estado"] == "atrasado"),
        "faltan": sum(1 for f in fuentes if f["estado"] == "falta"),
        "errores": sum(1 for f in fuentes if f["estado"] == "error"),
        "migraciones_pendientes": sum(1 for m in migraciones if m["aplicada"] is False),
    }


# ------------------------------------------------------------------ fuentes

def _a_fecha(v) -> Optional[date]:
    if v is None:
        return None
    return v.date() if isinstance(v, datetime) else v


def _texto(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    return str(v)


def _sin_tabla(tabla: str) -> dict:
    return {"estado": "falta", "detalle": f"No existe {tabla} en la base."}


def _resultado(ultimo, clasificacion: Clasificacion) -> dict:
    estado, esperado, detalle = clasificacion
    return {"ultimo": ultimo, "estado": estado, "esperado": esperado, "detalle": detalle}


def _es_voltara(campana_id: int) -> bool:
    return campana_id == pdatos.CAMPANA_VOLTARA


def _demanda_total(conn: Connection, campana_id: int, ahora: datetime) -> Optional[dict]:
    if not _es_voltara(campana_id):
        return None
    if not pdatos._tiene_tabla(conn, "dbo.Voltara Enerval informe IVR"):
        return _sin_tabla("dbo.[Voltara Enerval informe IVR]")
    # CAST afuera del MAX: el índice clustered empieza por [Fecha de Inicio].
    ultimo = conn.execute(text(
        "SELECT CAST(MAX([Fecha de Inicio]) AS date) FROM dbo.[Voltara Enerval informe IVR]")).scalar()
    return _resultado(ultimo, clasificar_demanda_total(_a_fecha(ultimo), ahora.date()))


def _informe_skills(conn: Connection, campana_id: int, ahora: datetime) -> Optional[dict]:
    if not _es_voltara(campana_id):
        return None
    if not pdatos._tiene_tabla(conn, "dbo.Voltara Enerval informe skills"):
        return _sin_tabla("dbo.[Voltara Enerval informe skills]")
    ultimo = conn.execute(text("""
        SELECT MAX(Intervalo) FROM dbo.[Voltara Enerval informe skills]
        WHERE [Volumen de llamadas entrantes] > 0
    """)).scalar()
    return _resultado(ultimo, clasificar_informe_skills(ultimo, ahora))


def _acumuladores_mitrol(conn: Connection, campana_id: int, ahora: datetime) -> Optional[dict]:
    """Hidra Técnico: la serie, el TMO y los conectados salen de los acumuladores de
    campaña de Mitrol, que cargan intradía como el informe de skills de Voltara (y
    también traen filas en cero, así que se mira la última con llamadas)."""
    if campana_id != pdatos.CAMPANA_HIDRA_TECNICO:
        return None
    if not pdatos._tiene_tabla(conn, "dbo.Acumuladores_de_campana"):
        return _sin_tabla("dbo.Acumuladores_de_campana")
    # Acotado a los últimos días: la tabla tiene 24 M de filas y el índice es por
    # Intervalo, no por campaña.
    ultimo = conn.execute(text("""
        SELECT MAX(Intervalo) FROM dbo.Acumuladores_de_campana
        WHERE idCampania = 481 AND Ingresadas > 0 AND Intervalo >= :desde
    """), {"desde": ahora - timedelta(days=7)}).scalar()
    return _resultado(ultimo, clasificar_informe_skills(ultimo, ahora))



def _llamadas_gasur(conn: Connection, campana_id: int, ahora: datetime) -> Optional[dict]:
    """Gasur: volumen, TMO y espera salen del detalle de llamadas, que carga
    intradía. La tabla no tiene índices: se acota a la última semana."""
    if campana_id != pdatos.CAMPANA_GASUR:
        return None
    if not pdatos._tiene_tabla(conn, "dbo.Gasur Llamadas"):
        return _sin_tabla("dbo.[Gasur Llamadas]")
    ultimo = conn.execute(text("""
        SELECT MAX(Fecha) FROM dbo.[Gasur Llamadas]
        WHERE Tipo = 'Entrante' AND Fecha >= :desde
    """), {"desde": ahora - timedelta(days=7)}).scalar()
    return _resultado(ultimo, clasificar_informe_skills(ultimo, ahora))


def _actividad_gasur(conn: Connection, campana_id: int, ahora: datetime) -> Optional[dict]:
    """Gasur: los conectados salen de los logins y logouts, que cargan con el
    día cerrado (como el registro de RRHH)."""
    if campana_id != pdatos.CAMPANA_GASUR:
        return None
    if not pdatos._tiene_tabla(conn, "dbo.Gasur Actividad"):
        return _sin_tabla("dbo.[Gasur Actividad]")
    ultimo = conn.execute(text("SELECT MAX(Fecha) FROM dbo.[Gasur Actividad]")).scalar()
    return _resultado(ultimo, clasificar_registro_rrhh(_a_fecha(ultimo), ahora.date()))


def _registro_rrhh(conn: Connection, campana_id: int, ahora: datetime) -> Optional[dict]:
    if not pdatos._tiene_tabla(conn, "dbo.payroll"):
        return _sin_tabla("dbo.payroll")
    # Sólo días cerrados: el día en curso figura con todos ausentes hasta que cierra.
    ultimo = conn.execute(text("""
        SELECT MAX(fecha) FROM dbo.payroll WHERE fecha < :hoy AND horas_trabajadas > 0
    """), {"hoy": ahora.date()}).scalar()
    return _resultado(ultimo, clasificar_registro_rrhh(_a_fecha(ultimo), ahora.date()))


def _malla_publicada(conn: Connection, campana_id: int, ahora: datetime) -> Optional[dict]:
    if not pdatos._tiene_tabla(conn, "dbo.payroll_futuro"):
        return _sin_tabla("dbo.payroll_futuro")
    ultimo = conn.execute(text(
        "SELECT MAX(fecha) FROM dbo.payroll_futuro WHERE horas_programadas > 0")).scalar()
    return _resultado(ultimo, clasificar_malla_publicada(_a_fecha(ultimo), ahora.date()))


def _clima(conn: Connection, campana_id: int, ahora: datetime) -> Optional[dict]:
    if not pdatos._tiene_tabla(conn, "planificacion.Clima"):
        return _sin_tabla("planificacion.Clima (migración 2026-09-07d)")
    estado = pdatos.clima_de_la_campana(conn, campana_id)
    if not estado:
        return None
    # Apagado, el pronóstico no lo usa: que falte no es un problema del plan. En
    # Hidra nace apagado (no explica su volumen) y sin histórico cargado, y la
    # pantalla lo marcaba "falta" como si algo estuviera roto.
    if not estado.get("activo"):
        return {"estado": "no_aplica",
                "detalle": "El clima está apagado para esta campaña: el pronóstico no lo usa."}
    fila = conn.execute(text("""
        SELECT MAX(CASE WHEN EsPronostico = 0 THEN Fecha END) AS obs,
               MAX(CASE WHEN EsPronostico = 1 THEN Fecha END) AS pron
        FROM planificacion.Clima WHERE CampanaID = :c
    """), {"c": campana_id}).mappings().fetchone()
    obs, pron = _a_fecha(fila["obs"]), _a_fecha(fila["pron"])
    ultimo = f"observado {obs or '—'} · pronóstico {pron or '—'}"
    return _resultado(ultimo, clasificar_clima(obs, pron, ahora.date()))


def _forecast_cliente(conn: Connection, campana_id: int, ahora: datetime) -> Optional[dict]:
    nombre = pdatos.CAMPANA_EN_FORECAST.get(campana_id)
    if not nombre:
        return None
    if not pdatos._tiene_tabla(conn, "dbo.Forecast"):
        return _sin_tabla("dbo.Forecast")
    ultimo = conn.execute(text(
        "SELECT MAX(Fecha) FROM dbo.Forecast WHERE [Campaña] = :c"), {"c": nombre}).scalar()
    return _resultado(ultimo, clasificar_forecast_cliente(_a_fecha(ultimo), ahora.date()))


def _cortes_enre(conn: Connection, campana_id: int, ahora: datetime) -> Optional[dict]:
    if not _es_voltara(campana_id):
        return None
    if not pdatos._tiene_tabla(conn, "planificacion.CorteEnre"):
        return _sin_tabla("planificacion.CorteEnre (migración 2026-09-14c)")
    ultimo = conn.execute(text(
        "SELECT MAX(Momento) FROM planificacion.CorteEnre WHERE Distribuidora = 'VOLTARA'")).scalar()
    return _resultado(ultimo, clasificar_cortes_enre(ultimo, ahora))


def _cortes_enre_tipo(conn: Connection, campana_id: int, ahora: datetime) -> Optional[dict]:
    """La tabla por tipo (programados / imprevistos). La hora es la que informa el
    ENRE, no la de la carga: si el ENRE deja de actualizar, esto queda atrasado
    aunque el script corra bien."""
    if not _es_voltara(campana_id):
        return None
    if not pdatos._tiene_tabla(conn, "planificacion.CorteEnreTipo"):
        return _sin_tabla("planificacion.CorteEnreTipo (migración 2026-09-24c)")
    ultimo = conn.execute(text(
        "SELECT MAX(Momento) FROM planificacion.CorteEnreTipo WHERE Distribuidora = 'VOLTARA'")).scalar()
    return _resultado(ultimo, clasificar_cortes_enre(ultimo, ahora))


def _reparto(conn: Connection, campana_id: int, ahora: datetime) -> Optional[dict]:
    # El reparto sólo se aplica cuando se pronostica la demanda total del cliente.
    if not pdatos.hay_demanda_total(conn, campana_id):
        return None
    hoy = ahora.date()
    cfg = pdatos.cargar_config(conn, campana_id)
    tramos = pdatos.asignacion_configurada(conn, campana_id, hoy, hoy)
    volumen: Dict[int, float] = {}
    for (_, skill_id), v in pdatos.serie_demanda_total(
            conn, campana_id, hoy - timedelta(days=30), hoy, cfg.intervalo_min).items():
        volumen[skill_id] = volumen.get(skill_id, 0.0) + v["total"]

    medianoche = datetime.combine(hoy, time(0, 0))
    con_demanda, sin_tramo = [], []
    for sk in cfg.skills:
        if not sk.activo or sk.pool_id is None or volumen.get(sk.skill_id, 0) <= 0:
            continue
        con_demanda.append(sk.nombre)
        if pdatos.factor_de_asignacion(tramos, medianoche, sk.skill_id) is None:
            sin_tramo.append(sk.nombre)
    desdes = [t["VigenteDesde"] for t in tramos if t.get("VigenteDesde") is not None]
    ultimo = f"último tramo desde {max(desdes)}" if desdes else None
    return _resultado(ultimo, clasificar_reparto(con_demanda, sin_tramo))


FUENTES: Tuple[Tuple[str, str, Callable[[Connection, int, datetime], Optional[dict]]], ...] = (
    ("demanda_total", "Demanda total del cliente (IVR)", _demanda_total),
    ("informe_skills", "Informe de skills (intradía)", _informe_skills),
    ("acumuladores_mitrol", "Acumuladores de campaña de Mitrol (intradía)", _acumuladores_mitrol),
    ("llamadas_gasur", "Llamadas de Gasur (intradía)", _llamadas_gasur),
    ("actividad_gasur", "Logueos de Gasur (días cerrados)", _actividad_gasur),
    ("registro_rrhh", "Registro de RRHH (días cerrados)", _registro_rrhh),
    ("malla_publicada", "Malla publicada", _malla_publicada),
    ("clima", "Clima del área de concesión", _clima),
    ("forecast_cliente", "Forecast del cliente", _forecast_cliente),
    ("cortes_enre", "Cortes del ENRE", _cortes_enre),
    ("cortes_enre_tipo", "Cortes del ENRE por tipo (programados / imprevistos)", _cortes_enre_tipo),
    ("reparto", "Reparto por skill", _reparto),
)


def verificar_fuentes(engine: Engine, campana_id: int, ahora: datetime) -> List[dict]:
    salida = []
    for clave, nombre, chequeo in FUENTES:
        try:
            with engine.connect() as conn:
                r = chequeo(conn, campana_id, ahora)
        except Exception as e:
            logger.warning(f"Salud del planificador: no se pudo leer {clave}: {e}")
            r = {"estado": "error", "detalle": f"No se pudo leer: {e}"}
        if r is None:
            r = {"estado": "no_aplica", "detalle": "Esta campaña no usa esta fuente."}
        salida.append({
            "clave": clave, "nombre": nombre, "ultimo": _texto(r.get("ultimo")),
            "esperado": r.get("esperado"), "estado": r["estado"],
            "detalle": r.get("detalle", ""),
        })
    return salida


# --------------------------------------------------------------- migraciones

def _tabla(tabla: str) -> Callable[[Connection], Optional[bool]]:
    return lambda conn: pdatos._tiene_tabla(conn, tabla)


def _columna(tabla: str, columna: str) -> Callable[[Connection], Optional[bool]]:
    return lambda conn: pdatos._tiene_columna(conn, tabla, columna)


def _consulta(sql: str, esperado, requiere: Optional[str] = None
              ) -> Callable[[Connection], Optional[bool]]:
    """Para las migraciones que sólo mueven datos: una consulta cuyo resultado
    distingue antes de después. Si la tabla que lee no existe, no está aplicada."""
    def verificar(conn: Connection) -> Optional[bool]:
        if requiere and not pdatos._tiene_tabla(conn, requiere):
            return False
        return conn.execute(text(sql)).scalar() == esperado
    return verificar


# Una señal por migración. Un test compara esta lista contra los archivos de
# scripts/migrations: una migración nueva sin registrar lo rompe.
REGISTRO_MIGRACIONES: List[dict] = [
    {"archivo": "2026-09-03_planificador.sql",
     "senal": "tabla planificacion.Campana", "verificar": _tabla("planificacion.Campana")},
    {"archivo": "2026-09-03b_planificador_payroll_y_paciencia.sql",
     "senal": "tabla planificacion.PoolOrigen", "verificar": _tabla("planificacion.PoolOrigen")},
    {"archivo": "2026-09-07_planificador_demanda_total.sql",
     "senal": "CK_Plan_Asignacion_Pct admite 0",
     # La definición que guarda SQL Server es «[Porcentaje]>=(0) ...», sin espacios.
     "verificar": _consulta("SELECT COUNT(*) FROM sys.check_constraints "
                            "WHERE name = 'CK_Plan_Asignacion_Pct' AND definition LIKE '%>=(0)%'", 1)},
    {"archivo": "2026-09-07b_planificador_restricciones_planilla.sql",
     "senal": "columna Skill.MaxAsaSeg", "verificar": _columna("planificacion.Skill", "MaxAsaSeg")},
    {"archivo": "2026-09-07c_planificador_ventana_entrenamiento.sql",
     "senal": "columna Campana.SemanasBase", "verificar": _columna("planificacion.Campana", "SemanasBase")},
    {"archivo": "2026-09-07d_planificador_clima.sql",
     "senal": "tabla planificacion.Clima", "verificar": _tabla("planificacion.Clima")},
    {"archivo": "2026-09-08_planificador_nivel_por_tipo_de_dia.sql",
     "senal": "columna Campana.NivelPorTipoDeDia",
     "verificar": _columna("planificacion.Campana", "NivelPorTipoDeDia")},
    {"archivo": "2026-09-08b_planificador_deriva_reparto.sql",
     "senal": "columna Campana.RepartoDerivaDias",
     "verificar": _columna("planificacion.Campana", "RepartoDerivaDias")},
    {"archivo": "2026-09-09_planificador_reparto_cliente.sql",
     "senal": "columna Campana.CombinarCliente",
     "verificar": _columna("planificacion.Campana", "CombinarCliente")},
    {"archivo": "2026-09-09b_planificador_pool_unico.sql",
     "senal": "un solo pool activo en Voltara",
     "verificar": _consulta("SELECT COUNT(*) FROM planificacion.Pool "
                            "WHERE CampanaID = 20 AND Activo = 1", 1, requiere="planificacion.Pool")},
    {"archivo": "2026-09-09c_planificador_nivel_gbdt.sql",
     "senal": "columna Campana.NivelGbdt", "verificar": _columna("planificacion.Campana", "NivelGbdt")},
    {"archivo": "2026-09-09d_planificador_elasticidad_domingo.sql",
     "senal": "columna Campana.ClimaElasticidadTipoDia",
     "verificar": _columna("planificacion.Campana", "ClimaElasticidadTipoDia")},
    {"archivo": "2026-09-09e_planificador_puestos_malla.sql",
     "senal": "tabla planificacion.PuestoMalla", "verificar": _tabla("planificacion.PuestoMalla")},
    {"archivo": "2026-09-09f_planificador_pool_solo_telefonicas.sql",
     # 09f dejó sólo las telefónicas; 10b sumó Artefactos (64) y 14b T1-Consumo (177).
     # Después se sumaron Hidra (57, 2026-09-22) y Gasur (121, 2026-09-24).
     "senal": "PoolOrigen sin sub-campañas fuera de 54, 57, 64, 100, 106, 121, 177",
     "verificar": _consulta("SELECT COUNT(*) FROM planificacion.PoolOrigen "
                            "WHERE CampanaRRHHID NOT IN (54, 57, 64, 100, 106, 121, 177)", 0,
                            requiere="planificacion.PoolOrigen")},
    {"archivo": "2026-09-10_planificador_shrinkage_por_dia_y_break.sql",
     "senal": "columna Campana.BreakMinPorHora",
     "verificar": _columna("planificacion.Campana", "BreakMinPorHora")},
    {"archivo": "2026-09-10b_planificador_alinear_malla_con_tablero.sql",
     "senal": "Operador Capacitación (puesto 4) cuenta en la malla",
     "verificar": _consulta("SELECT EnMalla FROM planificacion.PuestoMalla WHERE PuestoID = 4", True,
                            requiere="planificacion.PuestoMalla")},
    {"archivo": "2026-09-10c_planificador_skill_prioritario.sql",
     "senal": "columna Skill.Prioridad", "verificar": _columna("planificacion.Skill", "Prioridad")},
    {"archivo": "2026-09-11_planificador_sin_minimo_pool.sql",
     "senal": "pool General de Voltara sin mínimo",
     "verificar": _consulta("SELECT MinOperadores FROM planificacion.Pool "
                            "WHERE CampanaID = 20 AND Nombre = 'General'", 0,
                            requiere="planificacion.Pool")},
    {"archivo": "2026-09-14_planificador_refuerzo_digital.sql",
     "senal": "tabla planificacion.PoolRefuerzo", "verificar": _tabla("planificacion.PoolRefuerzo")},
    {"archivo": "2026-09-14b_planificador_pool_con_t1_consumo.sql",
     "senal": "T1 - Consumo (177) en PoolOrigen, o ya reclasificada por la 2026-09-16b",
     # La 2026-09-16b la saca del pool a propósito: con la clasificación nueva
     # cargada, esta se da por aplicada (y superada).
     "verificar": _consulta(
         "SELECT CASE WHEN EXISTS (SELECT 1 FROM planificacion.PoolOrigen WHERE CampanaRRHHID = 177) "
         "OR OBJECT_ID('planificacion.PoolSubCampana', 'U') IS NOT NULL THEN 1 ELSE 0 END",
         1, requiere="planificacion.PoolOrigen")},
    {"archivo": "2026-09-14c_planificador_cortes_enre.sql",
     "senal": "tabla planificacion.CorteEnre", "verificar": _tabla("planificacion.CorteEnre")},
    {"archivo": "2026-09-15_planificador_refuerzo_pedido.sql",
     "senal": "tabla planificacion.RefuerzoPedido", "verificar": _tabla("planificacion.RefuerzoPedido")},
    {"archivo": "2026-09-15b_planificador_perfil_presencia.sql",
     "senal": "tabla planificacion.PerfilPresencia",
     "verificar": _tabla("planificacion.PerfilPresencia")},
    {"archivo": "2026-09-15c_planificador_antiguedad.sql",
     "senal": "tabla planificacion.CurvaAntiguedad",
     "verificar": _tabla("planificacion.CurvaAntiguedad")},
    {"archivo": "2026-09-16_planificador_subcampanas_parciales.sql",
     "senal": "tabla planificacion.PoolSubCampana",
     "verificar": _tabla("planificacion.PoolSubCampana")},
    {"archivo": "2026-09-16b_planificador_t1_consumo_dedicada.sql",
     "senal": "T1 - Consumo (177) como cola dedicada",
     "verificar": _consulta("SELECT COUNT(*) FROM planificacion.PoolSubCampana "
                            "WHERE CampanaRRHHID = 177 AND Clase = 'dedicada'",
                            1, requiere="planificacion.PoolSubCampana")},
    {"archivo": "2026-09-17_planificador_redondeo_nocturno.sql",
     "senal": "columna Campana.RedondeoAbajoDesde",
     "verificar": _columna("planificacion.Campana", "RedondeoAbajoDesde")},
    {"archivo": "2026-09-22_planificador_hidra_tecnico.sql",
     "senal": "Hidra Técnico (campaña 1) dada de alta",
     "verificar": _consulta("SELECT COUNT(*) FROM planificacion.Campana WHERE CampanaID = 1",
                            1, requiere="planificacion.Campana")},
    {"archivo": "2026-09-23_planificador_hidra_mejoras.sql",
     "senal": "Hidra Técnico con la persistencia prendida",
     "verificar": _consulta("SELECT COUNT(*) FROM planificacion.Campana "
                            "WHERE CampanaID = 1 AND PersistenciaPesoHoy > 0",
                            1, requiere="planificacion.Campana")},
    {"archivo": "2026-09-24_planificador_voltara_nivel_persistencia.sql",
     "senal": "Voltara con el GBDT del nivel y la persistencia prendidos",
     "verificar": _consulta("SELECT COUNT(*) FROM planificacion.Campana "
                            "WHERE CampanaID = 20 AND NivelGbdt = 1 "
                            "AND PersistenciaPesoHoy > 0",
                            1, requiere="planificacion.Campana")},
    {"archivo": "2026-09-24b_planificador_avisos_corte.sql",
     "senal": "tabla planificacion.AvisoCorte", "verificar": _tabla("planificacion.AvisoCorte")},
    {"archivo": "2026-09-24c_planificador_cortes_enre_tipo.sql",
     "senal": "tabla planificacion.CorteEnreTipo", "verificar": _tabla("planificacion.CorteEnreTipo")},
    {"archivo": "2026-09-24d_planificador_gasur.sql",
     "senal": "Gasur (campaña 30) dada de alta",
     "verificar": _consulta(f"SELECT COUNT(*) FROM planificacion.Campana "
                            f"WHERE CampanaID = {pdatos.CAMPANA_GASUR}",
                            1, requiere="planificacion.Campana")},
    {"archivo": "2026-09-24e_planificador_gasur_forma_paros.sql",
     "senal": "columna Campana.FormaDias",
     "verificar": _columna("planificacion.Campana", "FormaDias")},
]


def verificar_migraciones(engine: Engine) -> List[dict]:
    salida = []
    for m in REGISTRO_MIGRACIONES:
        try:
            with engine.connect() as conn:
                aplicada = m["verificar"](conn)
        except Exception as e:
            logger.warning(f"Salud del planificador: no se pudo verificar {m['archivo']}: {e}")
            aplicada = None
        salida.append({"archivo": m["archivo"], "senal": m["senal"],
                       "aplicada": None if aplicada is None else bool(aplicada)})
    return salida


# ------------------------------------------------------------------ entrada

_CACHE: Dict[int, Tuple[float, dict]] = {}


def diagnostico_salud(engine: Engine, campana_id: int, ahora: Optional[datetime] = None,
                      usar_cache: bool = True) -> dict:
    guardado = _CACHE.get(campana_id)
    if usar_cache and ahora is None and guardado and reloj.monotonic() - guardado[0] < SEGUNDOS_CACHE:
        return guardado[1]

    fuentes = verificar_fuentes(engine, campana_id, ahora or datetime.now())
    migraciones = verificar_migraciones(engine)
    resultado = {"fuentes": fuentes, "migraciones": migraciones,
                 "resumen": resumen_de_salud(fuentes, migraciones)}
    if ahora is None:
        _CACHE[campana_id] = (reloj.monotonic(), resultado)
    return resultado
