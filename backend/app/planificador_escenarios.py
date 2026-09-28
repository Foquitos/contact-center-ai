"""Planificador — Escenarios: qué pasaría con la dotación si cambia un supuesto.

Permite simular cambios de volumen, TMO, nivel de servicio, ocupación,
ausentismo (shrinkage) y descansos sin tocar la corrida vigente ni persistir
nada en la base de datos.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.engine import Connection, Engine

from app import planificador as pl
from app import planificador_datos as pdatos

logger = logging.getLogger(__name__)


def validar_cambios(cambios: Dict[str, Any]) -> None:
    """Valida los rangos de los parámetros del escenario según las reglas de negocio."""
    if not cambios:
        return

    fv = cambios.get("factor_volumen")
    if fv is not None:
        try:
            fv_val = float(fv)
        except (ValueError, TypeError):
            raise ValueError("El factor de volumen debe ser numérico.")
        if not (0 < fv_val <= 3):
            raise ValueError("El factor de volumen debe ser mayor a 0 y menor o igual a 3.")

    ft = cambios.get("factor_tmo")
    if ft is not None:
        try:
            ft_val = float(ft)
        except (ValueError, TypeError):
            raise ValueError("El factor de TMO debe ser numérico.")
        if not (0 < ft_val <= 3):
            raise ValueError("El factor de TMO debe ser mayor a 0 y menor o igual a 3.")

    mo = cambios.get("max_ocupacion")
    if mo is not None:
        try:
            mo_val = float(mo)
        except (ValueError, TypeError):
            raise ValueError("El techo de ocupación debe ser numérico.")
        if not (0 < mo_val <= 1):
            raise ValueError("El techo de ocupación debe estar en el intervalo (0, 1].")

    us = cambios.get("umbral_seg")
    if us is not None:
        try:
            us_val = int(us)
        except (ValueError, TypeError):
            raise ValueError("El umbral de nivel de servicio en segundos debe ser un entero.")
        if us_val <= 0:
            raise ValueError("El umbral de nivel de servicio en segundos debe ser mayor a 0.")

    nds = cambios.get("objetivo_nds")
    if nds is not None:
        try:
            nds_val = float(nds)
        except (ValueError, TypeError):
            raise ValueError("El objetivo de nivel de servicio debe ser numérico.")
        if not (0 < nds_val < 1):
            raise ValueError("El objetivo de nivel de servicio debe estar en el intervalo (0, 1).")

    sh = cambios.get("shrinkage")
    if sh is not None:
        try:
            sh_val = float(sh)
        except (ValueError, TypeError):
            raise ValueError("El shrinkage debe ser numérico.")
        if not (0 <= sh_val < 0.9):
            raise ValueError("El shrinkage debe estar en el intervalo [0, 0.9).")

    br = cambios.get("break_min_por_hora")
    if br is not None:
        try:
            br_val = float(br)
        except (ValueError, TypeError):
            raise ValueError("Los minutos de break por hora deben ser numéricos.")
        if not (0 <= br_val <= 15):
            raise ValueError("Los minutos de break por hora deben estar entre 0 y 15.")


def _resumen_metricas(reqs: Sequence[pl.RequerimientoPool],
                      intervalos_por_hora: float) -> Dict[str, Any]:
    """Cálculo de métricas globales de un conjunto de requerimientos."""
    if not reqs:
        return {
            "intervalos": 0,
            "llamadas": 0.0,
            "horas_operador": 0.0,
            "pico_operadores": 0,
            "horas_faltantes": None,
            "nds_promedio": None,
            "abandono_promedio": None,
        }

    horas_operador = round(sum(r.operadores_a_planificar for r in reqs) / intervalos_por_hora, 1)
    pico_operadores = max((r.operadores_a_planificar for r in reqs), default=0)

    con_malla = [r for r in reqs if r.planificados is not None]
    horas_faltantes = (
        round(sum(max(-r.brecha, 0) for r in con_malla) / intervalos_por_hora, 1)
        if con_malla else None
    )

    con_carga = [r for r in reqs if r.llamadas > 0]
    suma_llamadas = sum(r.llamadas for r in con_carga)

    nds_promedio = (
        round(sum(r.nds_contractual * r.llamadas for r in con_carga) / suma_llamadas, 4)
        if con_carga and suma_llamadas > 0 else None
    )
    abandono_promedio = (
        round(sum(r.abandono * r.llamadas for r in con_carga) / suma_llamadas, 4)
        if con_carga and suma_llamadas > 0 else None
    )

    return {
        "intervalos": len(reqs),
        "llamadas": round(sum(r.llamadas for r in reqs), 1),
        "horas_operador": horas_operador,
        "pico_operadores": pico_operadores,
        "horas_faltantes": horas_faltantes,
        "nds_promedio": nds_promedio,
        "abandono_promedio": abandono_promedio,
    }


def calcular_escenario(engine_or_conn: Engine | Connection,
                       campana_id: int,
                       cambios: Dict[str, Any],
                       desde: Optional[date] = None,
                       hasta: Optional[date] = None) -> Dict[str, Any]:
    """Calcula la dotación base contra un escenario hipotético sobre la corrida vigente.

    Reutiliza el camino exacto pronóstico -> requerimiento por pool e intervalo
    (`pl.plan_de_pool`, que encapsula Erlang, la verificación de restricciones
    por skill, prioridad de ACD y la cadena disponibilidad/shrinkage/break con
    un solo redondeo al final).

    NO guarda nada en la base de datos.
    """
    validar_cambios(cambios)

    if hasattr(engine_or_conn, "connect"):
        with engine_or_conn.connect() as conn:
            return _calcular_con_conexion(conn, campana_id, cambios, desde, hasta)
    else:
        return _calcular_con_conexion(engine_or_conn, campana_id, cambios, desde, hasta)


def _calcular_con_conexion(conn: Connection,
                           campana_id: int,
                           cambios: Dict[str, Any],
                           desde: Optional[date] = None,
                           hasta: Optional[date] = None) -> Dict[str, Any]:
    if not pdatos.schema_disponible(conn):
        raise pdatos.MigracionPendiente(
            "Falta correr scripts/migrations/2026-09-03_planificador.sql")

    corrida = pdatos.corrida_vigente(conn, campana_id, "operativo")
    if not corrida:
        raise ValueError(f"No hay una corrida vigente para la campaña {campana_id}.")

    cid = corrida["CorridaID"]
    corrida_desde = corrida["Desde"].date() if isinstance(corrida["Desde"], datetime) else corrida["Desde"]
    corrida_hasta = corrida["Hasta"].date() if isinstance(corrida["Hasta"], datetime) else corrida["Hasta"]

    d_desde = desde or corrida_desde
    d_hasta = hasta or corrida_hasta

    if d_desde > d_hasta:
        raise ValueError("'desde' no puede ser posterior a 'hasta'.")

    dias_periodo = (d_hasta - d_desde).days + 1
    if dias_periodo > 31:
        raise ValueError("El período no puede superar los 31 días.")

    cfg = pdatos.cargar_config(conn, campana_id)
    fer = pdatos.feriados(conn, d_desde, d_hasta + timedelta(days=1), campana_id)
    cfg.feriados = frozenset(fer)

    filas_pron = pdatos.leer_pronostico(conn, cid)
    if not filas_pron:
        raise ValueError(f"La corrida vigente {cid} no tiene pronóstico guardado.")

    req_guardados = pdatos.leer_requerimiento(conn, cid)
    malla_dict = {(r["PoolID"], r["Intervalo"]): r.get("OperadoresPlanificados") for r in req_guardados}
    # Los citados en equivalentes por antigüedad, si la corrida los guardó: la
    # brecha del escenario se mide contra lo mismo que la de la corrida.
    equivalentes_dict = {(r["PoolID"], r["Intervalo"]): (float(r["CitadosEquivalentes"])
                         if r.get("CitadosEquivalentes") is not None else None)
                         for r in req_guardados}
    refuerzo_dict = {(r["PoolID"], r["Intervalo"]): r.get("RefuerzoDisponible") for r in req_guardados}
    guardado_planificar = {(r["PoolID"], r["Intervalo"]): r.get("OperadoresPlanificar") for r in req_guardados}

    # 1. Configuración y demanda Base
    cfg_base = cfg

    demanda_base: Dict[datetime, List[pl.DemandaSkill]] = {}
    for f in filas_pron:
        momento = f["Intervalo"]
        dia_m = momento.date() if isinstance(momento, datetime) else momento
        if dia_m < d_desde or dia_m > d_hasta:
            continue
        demanda_base.setdefault(momento, [])
        sid = f.get("SkillID")
        llamadas = float(f.get("LlamadasAcme") or 0)
        tmo = float(f.get("TmoSeg") or 0)
        if sid is not None and tmo > 0:
            demanda_base[momento].append(pl.DemandaSkill(sid, llamadas, tmo))

    # 2. Configuración y demanda Escenario
    # Copia de campaña pisando parámetros si vienen en cambios
    kws_campana = {}
    if cambios.get("max_ocupacion") is not None:
        kws_campana["max_ocupacion"] = float(cambios["max_ocupacion"])
    if cambios.get("shrinkage") is not None:
        kws_campana["shrinkage"] = float(cambios["shrinkage"])
        kws_campana["shrinkage_no_habil"] = None
        kws_campana["shrinkage_feriado"] = None
    if cambios.get("break_min_por_hora") is not None:
        kws_campana["break_min_por_hora"] = float(cambios["break_min_por_hora"])

    cfg_escenario = dataclasses.replace(cfg, **kws_campana)

    # Copia de skills si umbral_seg u objetivo_nds vienen en cambios
    skills_escenario = []
    for s in cfg.skills:
        kws_s = {}
        if cambios.get("umbral_seg") is not None:
            kws_s["umbral_seg"] = int(cambios["umbral_seg"])
        if cambios.get("objetivo_nds") is not None:
            kws_s["objetivo_nds"] = float(cambios["objetivo_nds"])
        skills_escenario.append(dataclasses.replace(s, **kws_s) if kws_s else s)
    cfg_escenario = dataclasses.replace(cfg_escenario, skills=skills_escenario)

    f_vol = float(cambios.get("factor_volumen", 1.0)) if cambios.get("factor_volumen") is not None else 1.0
    f_tmo = float(cambios.get("factor_tmo", 1.0)) if cambios.get("factor_tmo") is not None else 1.0

    demanda_escenario: Dict[datetime, List[pl.DemandaSkill]] = {}
    for f in filas_pron:
        momento = f["Intervalo"]
        dia_m = momento.date() if isinstance(momento, datetime) else momento
        if dia_m < d_desde or dia_m > d_hasta:
            continue
        demanda_escenario.setdefault(momento, [])
        sid = f.get("SkillID")
        llamadas = float(f.get("LlamadasAcme") or 0) * f_vol
        tmo = float(f.get("TmoSeg") or 0) * f_tmo
        if sid is not None and tmo > 0:
            demanda_escenario[momento].append(pl.DemandaSkill(sid, llamadas, tmo))

    # 3. Dimensionamiento por pool (reutilizando exactamente pl.plan_de_pool)
    reqs_base: List[pl.RequerimientoPool] = []
    for pool in cfg_base.pools.values():
        del_pool = pl.plan_de_pool(cfg_base, pool, demanda_base)
        for r in del_pool:
            r.planificados = malla_dict.get((pool.pool_id, r.momento))
            r.planificados_equivalentes = equivalentes_dict.get((pool.pool_id, r.momento))
            r.refuerzo_disponible = refuerzo_dict.get((pool.pool_id, r.momento))
        reqs_base.extend(del_pool)

    reqs_esc: List[pl.RequerimientoPool] = []
    for pool in cfg_escenario.pools.values():
        del_pool = pl.plan_de_pool(cfg_escenario, pool, demanda_escenario)
        for r in del_pool:
            r.planificados = malla_dict.get((pool.pool_id, r.momento))
            r.planificados_equivalentes = equivalentes_dict.get((pool.pool_id, r.momento))
            r.refuerzo_disponible = refuerzo_dict.get((pool.pool_id, r.momento))
        reqs_esc.extend(del_pool)

    # 4. Cotejo con la corrida guardada
    coincidencias = 0
    total_comparados = 0
    for r in reqs_base:
        k = (r.pool_id, r.momento)
        if k in guardado_planificar:
            total_comparados += 1
            if r.operadores_a_planificar == guardado_planificar[k]:
                coincidencias += 1
    base_coincide = (coincidencias / total_comparados) if total_comparados > 0 else 1.0

    avisos: List[str] = []
    if base_coincide < 0.99:
        avisos.append("la configuración cambió desde que se calculó el plan: la base usa la configuración de hoy")

    # Avisos propios de dimensionamiento (ej. topes de Erlang no alcanzados)
    for r in reqs_esc:
        for a in r.avisos:
            if a not in avisos:
                avisos.append(a)

    # 5. Resúmenes
    intervalos_por_hora = 60.0 / cfg.intervalo_min

    res_base = _resumen_metricas(reqs_base, intervalos_por_hora)
    res_esc = _resumen_metricas(reqs_esc, intervalos_por_hora)

    h_dif = round(res_esc["horas_operador"] - res_base["horas_operador"], 1)
    h_dif_pct = round(h_dif / res_base["horas_operador"], 4) if res_base["horas_operador"] > 0 else 0.0
    pico_dif = res_esc["pico_operadores"] - res_base["pico_operadores"]

    falta_dif = (
        round(res_esc["horas_faltantes"] - res_base["horas_faltantes"], 1)
        if res_esc["horas_faltantes"] is not None and res_base["horas_faltantes"] is not None
        else None
    )
    nds_dif = (
        round(res_esc["nds_promedio"] - res_base["nds_promedio"], 4)
        if res_esc["nds_promedio"] is not None and res_base["nds_promedio"] is not None
        else None
    )
    abandono_dif = (
        round(res_esc["abandono_promedio"] - res_base["abandono_promedio"], 4)
        if res_esc["abandono_promedio"] is not None and res_base["abandono_promedio"] is not None
        else None
    )

    res_diferencia = {
        "horas_operador": h_dif,
        "horas_operador_pct": h_dif_pct,
        "pico_operadores": pico_dif,
        "horas_faltantes": falta_dif,
        "nds_promedio": nds_dif,
        "abandono_promedio": abandono_dif,
    }

    # 6. Agrupación por día
    dias_lista = [d_desde + timedelta(days=i) for i in range(dias_periodo)]
    base_por_dia: Dict[date, List[pl.RequerimientoPool]] = {}
    for r in reqs_base:
        base_por_dia.setdefault(r.momento.date(), []).append(r)

    esc_por_dia: Dict[date, List[pl.RequerimientoPool]] = {}
    for r in reqs_esc:
        esc_por_dia.setdefault(r.momento.date(), []).append(r)

    por_dia = []
    for d in dias_lista:
        rb = base_por_dia.get(d, [])
        re = esc_por_dia.get(d, [])

        h_base = round(sum(r.operadores_a_planificar for r in rb) / intervalos_por_hora, 1)
        h_esc = round(sum(r.operadores_a_planificar for r in re) / intervalos_por_hora, 1)

        mb = [r for r in rb if r.planificados is not None]
        f_base = (
            round(sum(max(-r.brecha, 0) for r in mb) / intervalos_por_hora, 1)
            if mb else None
        )

        me = [r for r in re if r.planificados is not None]
        f_esc = (
            round(sum(max(-r.brecha, 0) for r in me) / intervalos_por_hora, 1)
            if me else None
        )

        por_dia.append({
            "dia": str(d),
            "horas_base": h_base,
            "horas_escenario": h_esc,
            "faltante_base": f_base,
            "faltante_escenario": f_esc,
        })

    return {
        "resumen": {
            "base": res_base,
            "escenario": res_esc,
            "diferencia": res_diferencia,
        },
        "por_dia": por_dia,
        "base_coincide_con_corrida": round(base_coincide, 4),
        "avisos": avisos,
    }
