"""Planificador — Lógica de insumos editables: reparto por skill, proyección intradía y atípicos.

Módulo de negocio desacoplado de los routers y de la pantalla.
Contiene las funciones puras de cálculo y validación, más las operaciones sobre BD
para la asignación y el seguimiento intradía.
"""

import logging
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app import planificador as pl
from app import planificador_datos as pdatos

logger = logging.getLogger(__name__)


# ============================================================================
# 1. REPARTO POR SKILL (planificacion.Asignacion)
# ============================================================================

def _llamadas_30d(conn: Connection, campana_id: int, hoy: date,
                  intervalo_min: int) -> Dict[int, int]:
    """Demanda del cliente por skill en 30 días: qué pesa cada cola antes de
    tocarle el porcentaje. Vacío si la campaña no pronostica la demanda total."""
    if not pdatos.hay_demanda_total(conn, campana_id):
        return {}
    volumen: Dict[int, float] = {}
    for (_, skill_id), v in pdatos.serie_demanda_total(
            conn, campana_id, hoy - timedelta(days=30), hoy, intervalo_min).items():
        volumen[skill_id] = volumen.get(skill_id, 0.0) + v["total"]
    return {k: int(round(v)) for k, v in volumen.items()}


def obtener_asignacion(conn: Connection, campana_id: int) -> dict:
    """Lee el reparto configurado para la campaña.

    Devuelve por skill:
    - tramo_vigente hoy (porcentaje, desde, hasta, nota)
    - historia: últimos 10 tramos
    - sin_tramo: skills activos con pool que no tienen tramo vigente hoy
    """
    cfg = pdatos.cargar_config(conn, campana_id)
    hoy = date.today()

    filas = conn.execute(text("""
        SELECT AsignacionID, SkillID, VigenteDesde, VigenteHasta, Porcentaje, Nota
        FROM planificacion.Asignacion
        WHERE CampanaID = :c
        ORDER BY VigenteDesde DESC, AsignacionID DESC
    """), {"c": campana_id}).mappings().all()
    tramos_todos = [dict(f) for f in filas]
    volumen = _llamadas_30d(conn, campana_id, hoy, cfg.intervalo_min)

    tramos_por_skill: Dict[Optional[int], List[dict]] = {}
    for t in tramos_todos:
        tramos_por_skill.setdefault(t["SkillID"], []).append(t)

    skills_salida = []
    sin_tramo = []

    for s in cfg.skills:
        if not s.activo or s.pool_id is None:
            continue
        tramos_s = tramos_por_skill.get(s.skill_id, [])
        vigentes = [
            t for t in tramos_s
            if t["VigenteDesde"] <= hoy
            and (t["VigenteHasta"] is None or t["VigenteHasta"] >= hoy)
        ]
        tramo_vigente = None
        if vigentes:
            vigentes.sort(key=lambda x: x["VigenteDesde"])
            v = vigentes[-1]
            tramo_vigente = {
                "asignacion_id": v["AsignacionID"],
                "porcentaje": float(v["Porcentaje"]),
                "desde": v["VigenteDesde"],
                "hasta": v["VigenteHasta"],
                "nota": v["Nota"],
            }

        historia = [
            {
                "asignacion_id": t["AsignacionID"],
                "porcentaje": float(t["Porcentaje"]),
                "desde": t["VigenteDesde"],
                "hasta": t["VigenteHasta"],
                "nota": t["Nota"],
            }
            for t in tramos_s[:10]
        ]

        skills_salida.append({
            "skill_id": s.skill_id,
            "nombre": s.nombre,
            "pool_id": s.pool_id,
            "tramo_vigente": tramo_vigente,
            "historia": historia,
            "llamadas_30d": volumen.get(s.skill_id) if volumen else None,
        })

        # Un skill sin tramo sólo importa si le llega demanda: si no, no hay nada
        # que dejar afuera del pronóstico. Sin la serie del cliente no se filtra.
        if tramo_vigente is None and (not volumen or volumen.get(s.skill_id, 0) > 0):
            sin_tramo.append({
                "skill_id": s.skill_id,
                "nombre": s.nombre,
                "pool_id": s.pool_id,
            })

    return {
        "campana_id": campana_id,
        "hoy": hoy,
        "skills": skills_salida,
        "sin_tramo": sin_tramo,
    }


def validar_nuevos_tramos(tramos_existentes: Sequence[dict],
                          nuevos: Sequence[dict]) -> None:
    """Valida la consistencia de los tramos antes de escribir en la base.

    Reglas:
    1. Porcentaje en [0, 1].
    2. No reescribir la historia: vigente_desde > VigenteDesde del último tramo del skill.
    3. Si el último tramo está cerrado con VigenteHasta, vigente_desde > VigenteHasta.
    4. Cada skill aparece como máximo una vez en la carga.
    """
    vistos = set()
    for t in nuevos:
        sid = t.get("skill_id")
        if sid in vistos:
            raise ValueError(f"El skill {sid} se repite en la misma carga de tramos.")
        vistos.add(sid)

        pct = float(t["porcentaje"])
        if not (0.0 <= pct <= 1.0):
            raise ValueError(f"El porcentaje de asignación debe estar entre 0 y 1: {pct}")

        desde = t["vigente_desde"]
        if isinstance(desde, str):
            desde = date.fromisoformat(desde)

        existentes_skill = [e for e in tramos_existentes if e.get("SkillID") == sid]
        if existentes_skill:
            ultimo = max(existentes_skill, key=lambda x: (x["VigenteDesde"], x.get("AsignacionID") or 0))
            u_desde = ultimo["VigenteDesde"]
            if isinstance(u_desde, str):
                u_desde = date.fromisoformat(u_desde)
            if desde <= u_desde:
                raise ValueError(
                    f"La fecha de vigencia desde ({desde}) debe ser posterior a la del último "
                    f"tramo ({u_desde}) del skill {sid}. No se reescribe la historia: se corrige con un tramo nuevo."
                )
            if ultimo.get("VigenteHasta") is not None:
                u_hasta = ultimo["VigenteHasta"]
                if isinstance(u_hasta, str):
                    u_hasta = date.fromisoformat(u_hasta)
                if desde <= u_hasta:
                    raise ValueError(
                        f"La fecha de vigencia desde ({desde}) debe ser posterior al fin del "
                        f"último tramo cerrado ({u_hasta}) del skill {sid}."
                    )


def guardar_asignacion_tramos(conn: Connection, campana_id: int,
                              nuevos: Sequence[dict],
                              usuario_id: Optional[int]) -> int:
    """Cierra los tramos abiertos y agrega los nuevos en una sola transacción."""
    if not nuevos:
        return 0

    filas = conn.execute(text("""
        SELECT AsignacionID, SkillID, VigenteDesde, VigenteHasta, Porcentaje
        FROM planificacion.Asignacion
        WHERE CampanaID = :c
        ORDER BY VigenteDesde, AsignacionID
    """), {"c": campana_id}).mappings().all()
    existentes = [dict(f) for f in filas]

    validar_nuevos_tramos(existentes, nuevos)

    insertados = 0
    for t in nuevos:
        sid = t.get("skill_id")
        desde = t["vigente_desde"]
        if isinstance(desde, str):
            desde = date.fromisoformat(desde)
        pct = float(t["porcentaje"])
        nota = t.get("nota")

        cierre = desde - timedelta(days=1)
        conn.execute(text("""
            UPDATE planificacion.Asignacion
            SET VigenteHasta = :cierre
            WHERE CampanaID = :c
              AND (SkillID = :s OR (:s IS NULL AND SkillID IS NULL))
              AND VigenteHasta IS NULL
        """), {"c": campana_id, "s": sid, "cierre": cierre})

        conn.execute(text("""
            INSERT INTO planificacion.Asignacion
                (CampanaID, SkillID, VigenteDesde, VigenteHasta, Porcentaje, Nota, CreadoPor)
            VALUES (:c, :s, :desde, NULL, :pct, :nota, :u)
        """), {"c": campana_id, "s": sid, "desde": desde, "pct": pct,
               "nota": nota, "u": usuario_id})
        insertados += 1

    return insertados


def aplicar_tramos_memoria(tramos_existentes: Sequence[dict],
                           nuevos: Sequence[dict]) -> List[dict]:
    """Simulador en memoria del cierre y alta de tramos para tests puros."""
    validar_nuevos_tramos(tramos_existentes, nuevos)
    salida = [dict(t) for t in tramos_existentes]

    for n in nuevos:
        sid = n.get("skill_id")
        desde = n["vigente_desde"]
        if isinstance(desde, str):
            desde = date.fromisoformat(desde)
        pct = float(n["porcentaje"])
        nota = n.get("nota")
        cierre = desde - timedelta(days=1)

        for t in salida:
            if t.get("SkillID") == sid and t.get("VigenteHasta") is None:
                t["VigenteHasta"] = cierre

        salida.append({
            "AsignacionID": len(salida) + 1,
            "SkillID": sid,
            "VigenteDesde": desde,
            "VigenteHasta": None,
            "Porcentaje": pct,
            "Nota": nota,
        })
    return salida


def proponer_reparto_desde_share(vigente: float, ratio: float) -> float:
    """Calcula el porcentaje propuesto: vigente * ratio, con 0 fijo y tope 1.0."""
    if vigente <= 0.0:
        return 0.0
    return min(1.0, round(vigente * ratio, 4))


# ============================================================================
# 2. SEGUIMIENTO INTRADÍA: PRÓXIMAS HORAS
# ============================================================================

def calcular_brecha_intradia(a_planificar_plan: int,
                             a_planificar_ajustado: int,
                             citados: Optional[int],
                             digital_con_turno: Optional[int]) -> dict:
    """Combina requerimiento ajustado con los citados y refuerzo de la malla.

    Función pura para cotejar personas a citar y palanca de refuerzo:
    - faltan: personas a citar que faltan contra la malla.
    - digital_con_turno: personas de Digital disponibles en ese intervalo.
    - faltan_tras_digital: lo que queda descubierto tras pasar Digital a la cola.
    """
    if citados is None:
        faltan = None
        faltan_tras_digital = None
    else:
        faltan = max(0, a_planificar_ajustado - citados)
        if digital_con_turno is not None:
            cubre = min(faltan, max(0, digital_con_turno))
            faltan_tras_digital = max(0, faltan - cubre)
        else:
            faltan_tras_digital = faltan

    return {
        "a_planificar_plan": a_planificar_plan,
        "a_planificar_ajustado": a_planificar_ajustado,
        "citados": citados,
        "faltan": faltan,
        "digital_con_turno": digital_con_turno,
        "faltan_tras_digital": faltan_tras_digital,
    }


def dimensionar_proximas_horas(cfg: pl.CampanaCfg,
                               pron_rows: Sequence[dict],
                               req_rows: Sequence[dict],
                               desvio: Optional[float],
                               en_curso: Optional[datetime],
                               dia: date,
                               horas: int = 4) -> List[dict]:
    """Intervalos que quedan de hoy (hasta `horas` horas adelante) con volumen ajustado.

    Usa exactamente el mismo camino de dimensionamiento que recalcular (pl.plan_de_pool).
    Si no hay desvío medido o no hay intervalo en curso, devuelve [].
    """
    if desvio is None or not en_curso:
        return []

    corte_max = en_curso + timedelta(hours=horas)
    momentos_interes = {
        f["Intervalo"] for f in pron_rows
        if f["Intervalo"].date() == dia and en_curso < f["Intervalo"] <= corte_max
    }
    if not momentos_interes:
        return []

    req_por_pool_momento = {
        (r["PoolID"], r["Intervalo"]): r for r in req_rows
        if r["Intervalo"].date() == dia
    }

    factor_ajuste = max(0.0, 1.0 + desvio)
    skill_a_pool = {s.skill_id: s.pool_id for s in cfg.skills if s.pool_id is not None}
    por_pool: Dict[int, Dict[datetime, List[pl.DemandaSkill]]] = {}

    for f in pron_rows:
        momento = f["Intervalo"]
        if momento not in momentos_interes:
            continue
        sid = f["SkillID"]
        pool_id = skill_a_pool.get(sid)
        if pool_id is None:
            continue
        tmo = float(f.get("TmoSeg") or 0)
        llamadas_base = float(f.get("LlamadasAcme") or 0)
        llamadas_ajustadas = llamadas_base * factor_ajuste

        por_pool.setdefault(pool_id, {}).setdefault(momento, []).append(
            pl.DemandaSkill(sid, llamadas_ajustadas, tmo)
        )

    resultados = []
    for pool_id, demanda_por_momento in por_pool.items():
        pool = cfg.pools.get(pool_id)
        if not pool:
            continue
        req_ajustados = pl.plan_de_pool(cfg, pool, demanda_por_momento)
        for r in req_ajustados:
            req_orig = req_por_pool_momento.get((pool_id, r.momento))
            a_plan_plan = (req_orig.get("OperadoresPlanificar")
                           if req_orig else r.operadores_a_planificar)
            citados = req_orig.get("OperadoresPlanificados") if req_orig else None
            # Igual que la brecha de la corrida: en equivalentes por antigüedad
            # cuando están guardados.
            if citados is not None and req_orig.get("CitadosEquivalentes") is not None:
                citados = int(float(req_orig["CitadosEquivalentes"]) + 0.5)
            digital = req_orig.get("RefuerzoDisponible") if req_orig else None

            brechas = calcular_brecha_intradia(
                a_planificar_plan=a_plan_plan,
                a_planificar_ajustado=r.operadores_a_planificar,
                citados=citados,
                digital_con_turno=digital
            )
            resultados.append({
                "intervalo": r.momento,
                "pool_id": pool_id,
                **brechas,
            })

    resultados.sort(key=lambda x: (x["intervalo"], x["pool_id"]))
    return resultados
