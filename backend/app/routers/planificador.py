"""Planificador: pronóstico de llamadas por intervalo y operadores necesarios.

`GET /planificador/plan` es la pantalla: la última corrida vigente con el
requerimiento por pool e intervalo. `POST /planificador/recalcular` la vuelve a
armar. El resto son la configuración y los dos insumos que carga una persona: el
calendario de eventos y los ajustes manuales del pronóstico.

Permisos: `planificador.view` para leer, `planificador.edit` para recalcular y
para tocar cualquier cosa que cambie el número. Los dos nacen sin asignar, igual
que audit:cuotas.

La lógica vive en app/planificador_servicio.py; acá van permisos, alcance por
empresa y armado de la respuesta.
"""

import logging
import unicodedata
from datetime import date, datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text

from app import planificador_datos as pdatos
from app import planificador_export as export
from app import planificador_servicio as servicio
from app import revision_jobs
from app.database import engine
from app.dependencies import RoleChecker
from app.models import User
from app.rbac import empresas_permitidas, exigir_acceso_empresa, registrar_auditoria

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/planificador", tags=["Planificador"])

require_view = RoleChecker(["planificador.view"])
require_edit = RoleChecker(["planificador.edit"])

# Tope de días de una corrida. Más que esto no es planificación operativa: es
# presupuesto, y va por el horizonte 'presupuesto' con agregación mensual.
MAX_DIAS_OPERATIVO = 90


class EventoRequest(BaseModel):
    desde: datetime
    hasta: datetime
    tipo: str = Field(max_length=40)
    descripcion: Optional[str] = Field(default=None, max_length=400)
    factor: Optional[float] = Field(default=None, gt=0)
    excluir_de_entrenamiento: bool = True
    confirmado: bool = True


class AjusteRequest(BaseModel):
    skill_id: Optional[int] = None
    desde: datetime
    hasta: datetime
    factor: float = Field(gt=0, le=10)
    # Obligatorio a propósito: un override del pronóstico sin motivo no se puede
    # auditar después, y es justo el dato que hace falta para saber si la
    # corrección manual estuvo bien o mal.
    motivo: str = Field(min_length=3, max_length=400)


def _verificar_acceso(campana_id: int, user: User) -> None:
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, user, campana_id=campana_id)


def _sin_migracion(e: pdatos.MigracionPendiente):
    # 409 y no 500: no es un error del servidor, es un paso pendiente del deploy,
    # y la pantalla lo muestra como un aviso accionable.
    return HTTPException(status_code=409, detail=str(e))


def _campanas_visibles(filas: List[dict], permitidas: Optional[set]) -> List[dict]:
    """Las campañas de empresas que el usuario puede ver; `None` = todas."""
    if permitidas is None:
        return list(filas)
    return [c for c in filas if c.get("empresa_id") in permitidas]


@router.get("/campanas")
def listar_campanas(current_user: User = Depends(require_view)):
    """Las campañas dadas de alta en el planificador, para el selector. Antes el
    selector tenía a Voltara escrita en el HTML; ahora sale de
    planificacion.Campana, con el mismo alcance por empresa que el resto."""
    try:
        with engine.connect() as conn:
            filas = pdatos.campanas_del_planificador(conn)
            permitidas = empresas_permitidas(conn, current_user)
        return {"campanas": _campanas_visibles(filas, permitidas)}
    except Exception as e:
        logger.error(f"Error listando las campañas del planificador: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/plan")
def obtener_plan(campana_id: int = Query(...),
                 horizonte: str = Query("operativo", pattern="^(operativo|presupuesto)$"),
                 current_user: User = Depends(require_view)):
    """La corrida vigente: requerimiento por pool e intervalo, el pronóstico que
    lo generó, y las dos series contra las que se lo lee —lo que pronosticó el
    cliente y lo que efectivamente entró—."""
    _verificar_acceso(campana_id, current_user)
    try:
        return servicio.plan_vigente(engine, campana_id, horizonte)
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error leyendo el plan de la campaña {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config")
def obtener_config(campana_id: int = Query(...),
                   current_user: User = Depends(require_view)):
    """Pools, skills, objetivos y factores. Es lo que la pantalla necesita para
    poder explicar de dónde sale cada número."""
    _verificar_acceso(campana_id, current_user)
    try:
        return _armar_config(campana_id)
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


def _armar_config(campana_id: int) -> dict:
    """La configuración tal como la consume la pantalla.

    Vive aparte del endpoint porque la exportación a Excel la necesita igual: el
    archivo lleva una hoja de parámetros para que la dotación se pueda auditar
    sin volver al sistema, y esa hoja tiene que decir exactamente lo mismo que la
    pantalla o son dos versiones de la verdad.
    """
    with engine.connect() as conn:
        cfg = pdatos.cargar_config(conn, campana_id)

    # Volumen reciente por skill: es lo que contesta, sin que nadie tenga que
    # investigar, por qué un pool aparece vacío en la pantalla. "Comercial
    # Consumo" no está roto: su único skill tuvo 189 llamadas en todo un mes.
    volumen = _volumen_por_skill(campana_id, dias=30)
    origen = _origen_rrhh(campana_id)

    def _skill(s):
        return {
            "skill_id": s.skill_id, "nombre": s.nombre,
            "objetivo_nds": s.objetivo_nds, "umbral_seg": s.umbral_seg,
            "max_abandono": s.max_abandono, "paciencia_seg": s.paciencia_seg,
            "max_asa_seg": s.max_asa_seg, "objetivo_nds_2": s.objetivo_nds_2,
            "umbral_seg_2": s.umbral_seg_2,
            "min_nivel_atencion_b": s.min_nivel_atencion_b,
            "prioridad": s.prioridad,
            "activo": s.activo,
            "llamadas_30d": volumen.get(s.skill_id, 0),
        }

    pools = []
    for p in cfg.pools.values():
        skills = [_skill(s) for s in cfg.skills_del_pool(p.pool_id)]
        con_volumen = [s for s in skills if s["llamadas_30d"] > 0]
        pools.append({
            "pool_id": p.pool_id, "nombre": p.nombre,
            "min_operadores": p.min_operadores,
            "skills": skills,
            "llamadas_30d": sum(s["llamadas_30d"] for s in skills),
            "origen_rrhh": origen.get(p.pool_id, []),
            # Por qué el pool puede verse vacío. Se responde acá y no en el JS
            # para que la explicación viva junto al dato que la sostiene.
            "motivo_vacio": (
                None if con_volumen else
                ("No tiene skills asignados." if not skills else
                 "Sus skills no registraron llamadas en los últimos 30 días.")
            ),
        })

    return {
        "campana_id": cfg.campana_id,
        **_contexto_de_campana(campana_id, cfg),
        "intervalo_min": cfg.intervalo_min,
        "max_ocupacion": cfg.max_ocupacion,
        "shrinkage": cfg.shrinkage,
        "shrinkage_no_habil": cfg.shrinkage_no_habil,
        "shrinkage_feriado": cfg.shrinkage_feriado,
        "break_min_por_hora": cfg.break_min_por_hora,
        "redondeo_abajo_desde": cfg.redondeo_abajo_desde,
        "redondeo_abajo_hasta": cfg.redondeo_abajo_hasta,
        "feriado_como_sabado": cfg.feriado_como_sabado,
        "puente_factor": cfg.puente_factor,
        "persistencia_peso_hoy": cfg.persistencia_peso_hoy,
        "persistencia_peso_resto": cfg.persistencia_peso_resto,
        "persistencia_dias": cfg.persistencia_dias,
        "intradia_desde_hora": cfg.intradia_desde_hora,
        "forma_dias": cfg.forma_dias,
        "persistencia_saltea_eventos": cfg.persistencia_saltea_eventos,
        "ancla_mensual_peso": cfg.ancla_mensual_peso,
        "ancla_mensual_desde_dias": cfg.ancla_mensual_desde_dias,
        "paciencia_seg": cfg.paciencia_seg,
        "semanas_base": cfg.semanas_base,
        "dias_nivel": cfg.dias_nivel,
        "nivel_por_tipo_de_dia": cfg.nivel_por_tipo_de_dia,
        "reparto_deriva_dias": cfg.reparto_deriva_dias,
        "reparto_deriva_tope": cfg.reparto_deriva_tope,
        "reparto_tipo_dia_dias": cfg.reparto_tipo_dia_dias,
        "reparto_tipo_dia_tope": cfg.reparto_tipo_dia_tope,
        "combinar_cliente": cfg.combinar_cliente,
        "combinar_cliente_peso_habil": cfg.combinar_cliente_peso_habil,
        "combinar_cliente_peso_no_habil": cfg.combinar_cliente_peso_no_habil,
        "combinar_cliente_medido_en": cfg.combinar_cliente_medido_en,
        "nivel_gbdt": cfg.nivel_gbdt,
        "nivel_gbdt_peso": cfg.nivel_gbdt_peso,
        "clima_elasticidad_tipo_dia": cfg.clima_elasticidad_tipo_dia,
        "clima": _estado_del_clima(campana_id),
        "puestos_malla": _puestos_malla(
            campana_id, pool_ids=[p.pool_id for p in cfg.pools.values()]),
        # De dónde salió cada parámetro, para que la pantalla lo pueda explicar.
        "procedencia": {
            "paciencia_origen": cfg.paciencia_origen,
            "paciencia_horizonte_seg": cfg.paciencia_horizonte_seg,
            "shrinkage_origen": cfg.shrinkage_origen,
            "shrinkage_ausentismo": cfg.shrinkage_ausentismo,
            "shrinkage_capacitacion": cfg.shrinkage_capacitacion,
            "shrinkage_medido_en": cfg.shrinkage_medido_en,
        },
        "perfil_presencia": _resumen_perfil_presencia(cfg),
        "pools": pools,
        "sin_pool": [{"skill_id": s.skill_id, "nombre": s.nombre,
                      "llamadas_30d": volumen.get(s.skill_id, 0)}
                     for s in cfg.skills if s.pool_id is None],
        "disponibilidad": [
            {"dia_semana": f.dia_semana, "hora_desde": f.hora_desde,
             "hora_hasta": f.hora_hasta, "factor": f.factor,
             "origen": getattr(f, "origen", None)}
            for f in cfg.disponibilidad
        ],
        "puede_comparar_con_malla": any(p["origen_rrhh"] for p in pools),
    }


def _resumen_perfil_presencia(cfg) -> Optional[dict]:
    """Dónde descuenta más y menos el shrinkage por media hora, de mayor a menor.
    None sin medición: el shrinkage es parejo y no hay nada que mostrar."""
    if not cfg.perfil_presencia:
        return None
    salida: dict = {"medido_en": cfg.perfil_presencia_medido_en}
    for tipo in ("habil", "no_habil"):
        filas = sorted(((m, e) for (t, m), e in cfg.perfil_presencia.items() if t == tipo),
                       key=lambda x: x[1], reverse=True)
        salida[tipo] = [{"hora": f"{m // 60:02d}:{m % 60:02d}", "exceso": e} for m, e in filas]
    return salida


def _contexto_de_campana(campana_id: int, cfg) -> dict:
    """Lo que la pantalla necesita para no tener nada de una campaña escrito a
    mano: cómo se llama, si hay de dónde leer sus llamadas, cómo se llama su
    refuerzo y cuánto rinde su gente nueva.

    Best-effort: sin esto la configuración se muestra igual."""
    salida = {
        "nombre": None,
        "con_fuente": campana_id in pdatos.FUENTES,
        "refuerzo": {"etiqueta": pdatos.ETIQUETA_REFUERZO.get(campana_id, "Refuerzo"),
                     "configurado": False},
        "antiguedad": None,
    }
    try:
        with engine.connect() as conn:
            fila = next((c for c in pdatos.campanas_del_planificador(conn)
                         if c["campana_id"] == campana_id), None)
            salida["nombre"] = fila["nombre"] if fila else None
            salida["refuerzo"]["configurado"] = any(
                pdatos.campanas_refuerzo_del_pool(conn, pool_id) for pool_id in cfg.pools)
            curva = pdatos.leer_curva_antiguedad(conn, campana_id)
        if curva:
            salida["antiguedad"] = {
                "medido_en": max(f["medido_en"] for f in curva),
                "tramos": [{k: v for k, v in f.items() if k != "medido_en"} for f in curva],
            }
    except Exception as e:
        logger.warning(f"No se pudo leer el contexto de la campaña {campana_id}: {e}")
    return salida


def _estado_del_clima(campana_id: int) -> dict:
    """Si el clima está activo, dónde se mide y cuántos días hay cargados.

    Best-effort: sin la migración la pantalla muestra la sección apagada con el
    motivo, en vez de romperse."""
    try:
        from datetime import timedelta
        hoy = date.today()
        with engine.connect() as conn:
            estado = pdatos.clima_de_la_campana(conn, campana_id)
            if not estado:
                return {"disponible": False,
                        "motivo": "falta correr la migración 2026-09-07d"}
            todo = pdatos.clima_por_dia(conn, campana_id, date(2020, 1, 1),
                                        hoy + timedelta(days=30))
            obs = [d for d, v in todo.items() if not v["es_pronostico"]]
        return {
            "disponible": True, **estado,
            "dias_cargados": len(todo), "dias_observados": len(obs),
            "desde": min(todo) if todo else None,
            "hasta": max(todo) if todo else None,
            "hasta_observado": max(obs) if obs else None,
            "minimo_para_ajustar": servicio.pclima.MIN_DIAS_PARA_AJUSTAR,
        }
    except Exception as e:
        logger.warning(f"No se pudo leer el estado del clima: {e}")
        return {"disponible": False, "motivo": str(e)}


def _volumen_por_skill(campana_id: int, dias: int = 30) -> dict:
    """Llamadas por skill en los últimos días. Best-effort: si falla, la pantalla
    pierde la explicación pero no la configuración."""
    try:
        from datetime import timedelta
        hoy = date.today()
        with engine.connect() as conn:
            serie = pdatos.serie_por_skill(conn, campana_id, hoy - timedelta(days=dias), hoy)
        acumulado: dict = {}
        for (_, skill_id), (llamadas, _) in serie.items():
            acumulado[skill_id] = acumulado.get(skill_id, 0.0) + llamadas
        return {k: int(v) for k, v in acumulado.items()}
    except Exception as e:
        logger.warning(f"No se pudo calcular el volumen por skill: {e}")
        return {}


def _puestos_malla(campana_id: int, dias: int = 90,
                   pool_ids: Optional[List[int]] = None) -> dict:
    """Quién aporta horas de piso y quién de esos cuenta como dotación telefónica.

    Best-effort: sin esto la pantalla pierde la explicación de por qué la malla
    cuenta lo que cuenta, pero la configuración se muestra igual."""
    try:
        from datetime import timedelta
        hoy = date.today()
        with engine.connect() as conn:
            pools = pool_ids if pool_ids is not None else [
                p.pool_id for p in pdatos.cargar_config(conn, campana_id).pools.values()]
            return pdatos.puestos_de_la_malla(
                conn, pools, hoy - timedelta(days=dias), hoy)
    except Exception as e:
        logger.warning(f"No se pudieron leer los puestos de la malla: {e}")
        return {"disponible": False, "puestos": [], "motivo": str(e)}


def _origen_rrhh(campana_id: int) -> dict:
    """Sub-campañas de RRHH por pool, con su nombre legible."""
    try:
        with engine.connect() as conn:
            catalogo = {c["id"]: c for c in pdatos.campanas_rrhh(conn)}
            salida: dict = {}
            for pool_id, ids in pdatos._origen_de_los_pools(conn, campana_id).items():
                salida[pool_id] = [
                    {"campana_rrhh_id": i,
                     "sub_campana": (catalogo.get(i) or {}).get("sub_campana"),
                     "cliente": (catalogo.get(i) or {}).get("cliente")}
                    for i in ids
                ]
            return salida
    except Exception as e:
        logger.warning(f"No se pudo leer el origen de los pools: {e}")
        return {}


@router.post("/recalcular")
def recalcular(campana_id: int = Query(...),
               dias: int = Query(14, ge=1, le=MAX_DIAS_OPERATIVO),
               horizonte: str = Query("operativo", pattern="^(operativo|presupuesto)$"),
               current_user: User = Depends(require_edit)):
    """Arma una corrida nueva y la deja vigente.

    Es sincrónico: con 14 días de horizonte son ~672 intervalos por pool y el
    cálculo es de milisegundos por intervalo. Si el horizonte de presupuesto (un
    año) llega a molestar, se pasa a tarea de fondo como los lotes de auditoría.
    """
    _verificar_acceso(campana_id, current_user)
    try:
        resumen = servicio.recalcular(engine, campana_id, dias=dias,
                                      horizonte=horizonte,
                                      usuario=getattr(current_user, "usuario", None))
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error recalculando la campaña {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    with engine.begin() as conn:
        registrar_auditoria(conn, getattr(current_user, "usuario", None),
                            "planificador.recalcular", "campana", campana_id,
                            {"dias": dias, "horizonte": horizonte,
                             "corrida": resumen.get("corrida_id")})
    return resumen


@router.get("/calibracion")
def calibracion(campana_id: int = Query(...),
                dias: int = Query(90, ge=14, le=365),
                current_user: User = Depends(require_view)):
    """Paciencia y disponibilidad medidas contra los datos, junto a las vigentes.

    No cambia nada: propone. Los dos parámetros mueven toda la dotación, así que
    el número nuevo lo aprueba una persona."""
    _verificar_acceso(campana_id, current_user)
    try:
        return servicio.calibrar(engine, campana_id, dias=dias)
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error calibrando la campaña {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/eventos")
def listar_eventos(campana_id: int = Query(...),
                   desde: Optional[date] = None, hasta: Optional[date] = None,
                   current_user: User = Depends(require_view)):
    """Calendario de días atípicos. Los de Origen 'auto' son los que detectó el
    sistema y esperan que alguien diga qué fueron."""
    _verificar_acceso(campana_id, current_user)
    desde = desde or date(2023, 1, 1)
    hasta = hasta or date(2030, 1, 1)
    try:
        with engine.connect() as conn:
            return {"eventos": pdatos.eventos(conn, campana_id, desde, hasta)}
    except Exception as e:
        logger.error(f"Error listando eventos de la campaña {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/eventos")
def crear_evento(payload: EventoRequest, campana_id: int = Query(...),
                 current_user: User = Depends(require_edit)):
    """Carga un evento, incluidos los FUTUROS: son los únicos que el sistema no
    puede descubrir solo y los que más valen."""
    _verificar_acceso(campana_id, current_user)
    if payload.hasta <= payload.desde:
        raise HTTPException(status_code=400, detail="El evento termina antes de empezar.")
    try:
        with engine.begin() as conn:
            evento_id = conn.execute(text("""
                INSERT INTO planificacion.Evento
                    (CampanaID, Desde, Hasta, Tipo, Descripcion, Factor, Origen,
                     ExcluirDeEntrenamiento, Confirmado, CreadoPor)
                OUTPUT INSERTED.EventoID
                VALUES (:c, :d, :h, :t, :desc, :f, 'manual', :ex, :conf, :u)
            """), {"c": campana_id, "d": payload.desde, "h": payload.hasta,
                   "t": payload.tipo, "desc": payload.descripcion,
                   "f": payload.factor, "ex": payload.excluir_de_entrenamiento,
                   "conf": payload.confirmado,
                   "u": getattr(current_user, "usuario", None)}).scalar()
            registrar_auditoria(conn, getattr(current_user, "usuario", None),
                                "planificador.evento_alta", "evento", evento_id,
                                payload.model_dump(mode="json"))
        return {"evento_id": int(evento_id)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creando evento en la campaña {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/eventos/{evento_id}")
def confirmar_evento(evento_id: int, payload: EventoRequest,
                     campana_id: int = Query(...),
                     current_user: User = Depends(require_edit)):
    """Confirma o corrige un evento detectado automáticamente."""
    _verificar_acceso(campana_id, current_user)
    try:
        with engine.begin() as conn:
            filas = conn.execute(text("""
                UPDATE planificacion.Evento
                SET Desde = :d, Hasta = :h, Tipo = :t, Descripcion = :desc,
                    Factor = :f, ExcluirDeEntrenamiento = :ex, Confirmado = :conf
                WHERE EventoID = :id AND (CampanaID = :c OR CampanaID IS NULL)
            """), {"id": evento_id, "c": campana_id, "d": payload.desde,
                   "h": payload.hasta, "t": payload.tipo,
                   "desc": payload.descripcion, "f": payload.factor,
                   "ex": payload.excluir_de_entrenamiento,
                   "conf": payload.confirmado}).rowcount
            if not filas:
                raise HTTPException(status_code=404, detail="Evento inexistente.")
            registrar_auditoria(conn, getattr(current_user, "usuario", None),
                                "planificador.evento_edicion", "evento", evento_id,
                                payload.model_dump(mode="json"))
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error editando el evento {evento_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ajustes")
def listar_ajustes(campana_id: int = Query(...),
                   desde: Optional[date] = None, hasta: Optional[date] = None,
                   current_user: User = Depends(require_view)):
    _verificar_acceso(campana_id, current_user)
    desde = desde or date.today()
    hasta = hasta or date(2030, 1, 1)
    try:
        with engine.connect() as conn:
            return {"ajustes": pdatos.ajustes_vigentes(conn, campana_id, desde, hasta)}
    except Exception as e:
        logger.error(f"Error listando ajustes de la campaña {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ajustes")
def crear_ajuste(payload: AjusteRequest, campana_id: int = Query(...),
                 current_user: User = Depends(require_edit)):
    """Override manual del pronóstico. Se aplica en la próxima corrida."""
    _verificar_acceso(campana_id, current_user)
    if payload.hasta <= payload.desde:
        raise HTTPException(status_code=400, detail="El ajuste termina antes de empezar.")
    try:
        with engine.begin() as conn:
            ajuste_id = conn.execute(text("""
                INSERT INTO planificacion.Ajuste
                    (CampanaID, SkillID, Desde, Hasta, Factor, Motivo, CreadoPor)
                OUTPUT INSERTED.AjusteID
                VALUES (:c, :s, :d, :h, :f, :m, :u)
            """), {"c": campana_id, "s": payload.skill_id, "d": payload.desde,
                   "h": payload.hasta, "f": payload.factor, "m": payload.motivo,
                   "u": getattr(current_user, "usuario", None)}).scalar()
            registrar_auditoria(conn, getattr(current_user, "usuario", None),
                                "planificador.ajuste_alta", "ajuste", ajuste_id,
                                payload.model_dump(mode="json"))
        return {"ajuste_id": int(ajuste_id)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creando ajuste en la campaña {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/ajustes/{ajuste_id}")
def baja_ajuste(ajuste_id: int, campana_id: int = Query(...),
                current_user: User = Depends(require_edit)):
    """Baja lógica: el ajuste deja de aplicar pero queda el registro de que
    alguien lo cargó y por qué."""
    _verificar_acceso(campana_id, current_user)
    try:
        with engine.begin() as conn:
            filas = conn.execute(text("""
                UPDATE planificacion.Ajuste SET Activo = 0
                WHERE AjusteID = :id AND CampanaID = :c
            """), {"id": ajuste_id, "c": campana_id}).rowcount
            if not filas:
                raise HTTPException(status_code=404, detail="Ajuste inexistente.")
            registrar_auditoria(conn, getattr(current_user, "usuario", None),
                                "planificador.ajuste_baja", "ajuste", ajuste_id, None)
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error dando de baja el ajuste {ajuste_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================================
# CONFIGURACIÓN — todo lo que se puede cambiar sin tocar código
# =========================================================================

class CampanaRequest(BaseModel):
    intervalo_min: Optional[int] = Field(default=None, ge=5, le=60)
    # None explícito = sacar el techo de ocupación. Por eso el modelo distingue
    # "no vino la clave" de "vino en null": el router mira `model_fields_set`.
    max_ocupacion: Optional[float] = Field(default=None, gt=0, le=1)
    shrinkage: Optional[float] = Field(default=None, ge=0, lt=0.95)
    # Shrinkage por tipo de día. En null caen al general, y ese null es un valor
    # con significado: el router mira `model_fields_set` para distinguir "no vino
    # la clave" de "vino en null".
    shrinkage_no_habil: Optional[float] = Field(default=None, ge=0, lt=0.9)
    shrinkage_feriado: Optional[float] = Field(default=None, ge=0, lt=0.9)
    # Minutos de break por hora planificada. Tope 15 como el CHECK. Ojo: ese
    # tiempo ya está adentro del factor de disponibilidad, así que subirlo sin
    # volver a medirla descuenta dos veces. Ver la migración 2026-09-10.
    break_min_por_hora: Optional[float] = Field(default=None, ge=0, le=15)
    # Franja [desde, hasta) en la que la gente a citar se redondea para abajo.
    # Las dos en null = apagado. Topes del CHECK de la migración 2026-09-17.
    redondeo_abajo_desde: Optional[int] = Field(default=None, ge=0, le=23)
    redondeo_abajo_hasta: Optional[int] = Field(default=None, ge=1, le=24)
    paciencia_seg: Optional[int] = Field(default=None, gt=0)
    paciencia_horizonte_seg: Optional[int] = Field(default=None, ge=10, le=600)
    # Ventana de entrenamiento de la línea de base. Los topes son los mismos que
    # el CHECK de la migración 2026-09-07c.
    semanas_base: Optional[int] = Field(default=None, ge=4, le=156)
    dias_nivel: Optional[int] = Field(default=None, ge=7, le=120)
    nivel_por_tipo_de_dia: Optional[bool] = None
    # Deriva del reparto. Los topes son los del CHECK de la migración 2026-09-08b:
    # menos de 3 días es ruido y más de 60 deja de ser "reciente"; el tope de
    # corrección no puede pasar de 0,50 o la deriva reescribiría el tramo.
    reparto_deriva_dias: Optional[int] = Field(default=None, ge=0, le=60)
    reparto_deriva_tope: Optional[float] = Field(default=None, ge=0, le=0.5)
    # Reparto por tipo de día. Ventana larga a propósito: en 28 días hay 8 días
    # no hábiles y el factor sale de ruido. Topes del CHECK de la 2026-09-09.
    reparto_tipo_dia_dias: Optional[int] = Field(default=None, ge=0, le=1100)
    reparto_tipo_dia_tope: Optional[float] = Field(default=None, ge=0, le=0.5)
    # Combinar con el pronóstico que manda el cliente. Los PESOS no se editan
    # acá: los mide la calibración (POST /combinacion/aplicar). Esto es sólo la
    # llave de luz.
    combinar_cliente: Optional[bool] = None
    # Segunda opinión del nivel diario. El tope de 0,6 es el del CHECK de la
    # migración 2026-09-09c: con peso 1 el pronóstico sería el modelo de árboles
    # solo, que mide PEOR que producción. Se prende a mano y después de verlo en
    # la pestaña Comparación con el selector de nivel.
    nivel_gbdt: Optional[bool] = None
    nivel_gbdt_peso: Optional[float] = Field(default=None, ge=0, le=0.6)
    # Atenuar el factor de clima del domingo por su elasticidad medida. No tiene
    # peso configurable a propósito: el alfa lo mide el modelo sobre una ventana
    # móvil y se encoge hacia 1 con pocos días. Es una llave de luz.
    clima_elasticidad_tipo_dia: Optional[bool] = None
    # Clima del área de concesión. Activarlo cambia el pronóstico, así que se
    # hace a mano y después de medirlo en la pestaña Comparación.
    # Calendario propio, persistencia del desvío y reescalado intradía
    # (migración 2026-09-22). Topes = los CHECK de la migración.
    feriado_como_sabado: Optional[bool] = None
    puente_factor: Optional[float] = Field(default=None, ge=0.1, le=1.5)
    persistencia_peso_hoy: Optional[float] = Field(default=None, ge=0, le=1)
    persistencia_peso_resto: Optional[float] = Field(default=None, ge=0, le=1)
    persistencia_dias: Optional[int] = Field(default=None, ge=0, le=30)
    intradia_desde_hora: Optional[int] = Field(default=None, ge=6, le=22)
    # Forma reciente del día y persistencia que saltea eventos (2026-09-24e).
    forma_dias: Optional[int] = Field(default=None, ge=7, le=91)
    persistencia_saltea_eventos: Optional[bool] = None
    ancla_mensual_peso: Optional[float] = Field(default=None, ge=0, le=1)
    ancla_mensual_desde_dias: Optional[int] = Field(default=None, ge=0, le=30)
    clima_activo: Optional[bool] = None
    clima_lat: Optional[float] = Field(default=None, ge=-90, le=90)
    clima_lon: Optional[float] = Field(default=None, ge=-180, le=180)
    nota: Optional[str] = Field(default=None, max_length=400)

    @model_validator(mode="after")
    def _franja_de_redondeo_completa(self):
        """Las dos horas juntas o ninguna, y distintas: es lo que exige el CHECK, y
        dicho acá el error llega como 422 legible en vez de un 500 de la base."""
        claves = {"redondeo_abajo_desde", "redondeo_abajo_hasta"}
        if claves & self.model_fields_set:
            desde, hasta = self.redondeo_abajo_desde, self.redondeo_abajo_hasta
            if (desde is None) != (hasta is None):
                raise ValueError("El redondeo para abajo necesita las dos horas, o ninguna para apagarlo.")
            if desde is not None and desde == hasta:
                raise ValueError("El redondeo para abajo no puede empezar y terminar a la misma hora.")
        return self


class PoolRequest(BaseModel):
    pool_id: Optional[int] = None
    nombre: str = Field(min_length=1, max_length=80)
    min_operadores: int = Field(default=0, ge=0)
    activo: bool = True
    nota: Optional[str] = Field(default=None, max_length=400)


class SkillRequest(BaseModel):
    pool_id: Optional[int] = None          # None = no se dimensiona
    objetivo_nds: Optional[float] = Field(default=None, gt=0, le=1)
    umbral_seg: Optional[int] = Field(default=None, gt=0, le=600)
    max_abandono: Optional[float] = Field(default=None, ge=0, le=1)
    paciencia_seg: Optional[int] = Field(default=None, gt=0)
    # Restricciones de la planilla de Excel con la que se dimensionaba antes.
    # Todas opcionales: en NULL no restringen, que es como venía funcionando.
    max_asa_seg: Optional[int] = Field(default=None, gt=0, le=3600)
    objetivo_nds_2: Optional[float] = Field(default=None, gt=0, le=1)
    umbral_seg_2: Optional[int] = Field(default=None, gt=0, le=3600)
    min_nivel_atencion_b: Optional[float] = Field(default=None, gt=0, le=1)
    # Cola que el ACD atiende primero. Cambia sólo cómo se verifica SU techo de
    # abandono: una llamada prioritaria no espera detrás de las demás.
    prioridad: bool = False
    activo: bool = True
    nota: Optional[str] = Field(default=None, max_length=400)


class FranjaRequest(BaseModel):
    dia_semana: int = Field(default=0, ge=0, le=7)
    hora_desde: int = Field(ge=0, le=23)
    hora_hasta: int = Field(ge=1, le=24)
    factor: float = Field(gt=0, le=1)


class OrigenRequest(BaseModel):
    campana_rrhh_id: int
    nota: Optional[str] = Field(default=None, max_length=200)


class ShrinkageMedido(BaseModel):
    """Lo que la pantalla ya tiene de `GET /calibracion`, para no volver a medir.

    El botón escribe cuatro números que están a la vista; medir de nuevo son
    decenas de segundos por nada. Los valores vienen del propio endpoint de
    medición, así que no son entrada del usuario en ningún sentido interesante:
    quien pueda mandar esto puede editar la config a mano igual."""
    shrinkage: float = Field(ge=0, lt=0.95)
    ausentismo: Optional[float] = Field(default=None, ge=0, lt=0.95)
    capacitacion: Optional[float] = Field(default=None, ge=0, lt=0.95)
    # Los dos por tipo de día. En null quedan en null, que significa
    # "usá el general".
    no_habil: Optional[float] = Field(default=None, ge=0, lt=0.9)
    feriado: Optional[float] = Field(default=None, ge=0, lt=0.9)
    # De qué medición salen: los códigos de RRHH o la presencia en la línea. Sin
    # declararlo acá Pydantic lo descarta y quedaría registrado como 'payroll'.
    origen: Optional[Literal["payroll", "presencia"]] = None


class CalibracionRequest(BaseModel):
    paciencia_seg: Optional[int] = Field(default=None, gt=0)
    # Nombre del pool cuyo shrinkage medido se quiere dejar vigente. Sólo se usa
    # cuando NO viene `shrinkage`: es el modo viejo, que vuelve a medir.
    shrinkage_pool: Optional[str] = None
    shrinkage: Optional[ShrinkageMedido] = None
    # Paciencia medida por skill: {skill_id: segundos}. Es lo que hace que un
    # electrodependiente y alguien sin luz dejen de compartir un solo número.
    paciencia_por_skill: dict[int, Optional[int]] = Field(default_factory=dict)
    # La disponibilidad medida del pool, tal como la devolvió `GET /calibracion`:
    # se guarda por hora y ya neta de break. Sin esto, medir mostraba 0,82 y
    # aplicar dejaba las franjas como estaban.
    disponibilidad: Optional[dict] = None
    dias: int = Field(default=90, ge=14, le=365)


class CombinacionRequest(BaseModel):
    """Los pesos medidos que se dejan vigentes. El tope es el mismo que aplica
    `planificador_combinacion.PESO_MAXIMO` y que el CHECK de la migración: el
    pronóstico del cliente no puede pasar de la mitad del combinado."""
    peso_habil: Optional[float] = Field(default=None, ge=0, le=0.5)
    peso_no_habil: Optional[float] = Field(default=None, ge=0, le=0.5)


def _auditar(conn, user: User, accion: str, entidad: str, entidad_id, detalle):
    registrar_auditoria(conn, getattr(user, "usuario", None), accion, entidad,
                        entidad_id, detalle)


@router.put("/config/campana")
def guardar_campana(payload: CampanaRequest, campana_id: int = Query(...),
                    current_user: User = Depends(require_edit)):
    """Parámetros generales. Poner la paciencia o el shrinkage a mano los marca
    como 'manual'; eso ya no bloquea a la calibración —el botón de aplicar pisa
    igual y avisa qué pisó— pero deja registrado de dónde salió el número."""
    _verificar_acceso(campana_id, current_user)
    # Solo lo que el cliente mandó explícitamente: así `max_ocupacion: null`
    # significa "sacale el techo" y la ausencia de la clave significa "no la toques".
    datos = payload.model_dump(include=payload.model_fields_set)
    try:
        with engine.begin() as conn:
            pdatos.guardar_campana(conn, campana_id, datos,
                                   getattr(current_user, "usuario", None))
            _auditar(conn, current_user, "planificador.config_campana",
                     "campana", campana_id, datos)
        return {"ok": True}
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except Exception as e:
        logger.error(f"Error guardando la config de la campaña {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/config/pools")
def guardar_pool(payload: PoolRequest, campana_id: int = Query(...),
                 current_user: User = Depends(require_edit)):
    """Alta o edición de un pool."""
    _verificar_acceso(campana_id, current_user)
    try:
        with engine.begin() as conn:
            pool_id = pdatos.guardar_pool(conn, campana_id, payload.model_dump())
            _auditar(conn, current_user, "planificador.config_pool", "pool",
                     pool_id, payload.model_dump())
        return {"pool_id": pool_id}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error guardando el pool en la campaña {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/config/skills/{skill_id}")
def guardar_skill(skill_id: int, payload: SkillRequest,
                  campana_id: int = Query(...),
                  current_user: User = Depends(require_edit)):
    """Objetivos de un skill y a qué pool pertenece.

    `pool_id = null` lo saca del dimensionamiento, que es lo correcto para las
    colas sin volumen: si no, aparecen pidiendo cobertura mínima para llamadas
    que no existen."""
    _verificar_acceso(campana_id, current_user)
    try:
        with engine.begin() as conn:
            pdatos.guardar_skill(conn, campana_id, skill_id, payload.model_dump())
            _auditar(conn, current_user, "planificador.config_skill", "skill",
                     skill_id, payload.model_dump())
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error guardando el skill {skill_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/config/disponibilidad")
def guardar_disponibilidad(payload: List[FranjaRequest],
                           campana_id: int = Query(...),
                           current_user: User = Depends(require_edit)):
    """Reemplaza todas las franjas de disponibilidad de la campaña.

    Se valida que no se solapen: dos franjas pisándose dejarían el factor a
    merced del orden de lectura."""
    _verificar_acceso(campana_id, current_user)
    try:
        with engine.begin() as conn:
            franjas = [f.model_dump() for f in payload]
            pdatos.guardar_disponibilidad(conn, campana_id, franjas)
            _auditar(conn, current_user, "planificador.config_disponibilidad",
                     "campana", campana_id, {"franjas": franjas})
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error guardando la disponibilidad de {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/config/pools/{pool_id}/origen")
def guardar_origen(pool_id: int, payload: List[OrigenRequest],
                   campana_id: int = Query(...),
                   current_user: User = Depends(require_edit)):
    """Qué sub-campañas de RRHH aportan gente a este pool.

    Es lo que ata el planificador con payroll: sin esto se puede calcular cuánta
    gente hace falta, pero no compararla con la citada ni medir el ausentismo."""
    _verificar_acceso(campana_id, current_user)
    try:
        with engine.begin() as conn:
            origenes = [o.model_dump() for o in payload]
            pdatos.guardar_pool_origen(conn, pool_id, origenes)
            _auditar(conn, current_user, "planificador.config_origen", "pool",
                     pool_id, {"origen": origenes})
        return {"ok": True}
    except Exception as e:
        logger.error(f"Error guardando el origen del pool {pool_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config/campanas-rrhh")
def listar_campanas_rrhh(cliente: Optional[str] = None,
                         current_user: User = Depends(require_view)):
    """Catálogo de sub-campañas de RRHH, para el selector de origen del pool."""
    try:
        with engine.connect() as conn:
            return {"campanas": pdatos.campanas_rrhh(conn, cliente)}
    except Exception as e:
        logger.error(f"Error listando campañas de RRHH: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config/codigos-payroll")
def listar_codigos_payroll(current_user: User = Depends(require_view)):
    """Cómo está clasificado cada código de RRHH. Es lo que hace auditable el
    shrinkage: si el número no cierra, se ve qué código lo está moviendo."""
    try:
        with engine.connect() as conn:
            return {"codigos": pdatos.codigos_payroll(conn)}
    except Exception as e:
        logger.error(f"Error listando códigos de payroll: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/config/codigos-payroll/{codigo}")
def clasificar_codigo_payroll(codigo: str, clase: str = Query(...),
                              descripcion: Optional[str] = None,
                              current_user: User = Depends(require_edit)):
    """Clasifica un código de RRHH (piso / ausente / capacitacion / licencia)."""
    try:
        with engine.begin() as conn:
            pdatos.guardar_codigo_payroll(conn, codigo, clase, descripcion)
            _auditar(conn, current_user, "planificador.config_codigo", "codigo",
                     None, {"codigo": codigo, "clase": clase})
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error clasificando el código {codigo}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config/puestos-malla")
def listar_puestos_malla(campana_id: int = Query(...), dias: int = Query(90, ge=7, le=365),
                         current_user: User = Depends(require_view)):
    """Qué puestos aportan horas de piso y cuáles cuentan como dotación citada.

    Es lo que hace auditable la malla: si "Citados" no cierra contra lo que la
    operación ve en el piso, acá se ve qué puesto lo está moviendo."""
    _verificar_acceso(campana_id, current_user)
    try:
        return _puestos_malla(campana_id, dias)
    except Exception as e:
        logger.error(f"Error listando los puestos de la malla: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/config/puestos-malla/{puesto_id}")
def clasificar_puesto_malla(puesto_id: int, en_malla: bool = Query(...),
                            nota: Optional[str] = None,
                            campana_id: int = Query(...),
                            current_user: User = Depends(require_edit)):
    """Dice si un puesto cuenta como dotación telefónica.

    Mueve la malla citada y el shrinkage medido, así que queda auditado."""
    _verificar_acceso(campana_id, current_user)
    try:
        with engine.begin() as conn:
            pdatos.guardar_puesto_malla(conn, puesto_id, en_malla, nota)
            _auditar(conn, current_user, "planificador.config_puesto", "puesto",
                     puesto_id, {"en_malla": en_malla})
        return {"ok": True}
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except Exception as e:
        logger.error(f"Error clasificando el puesto {puesto_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/calibracion/aplicar")
def aplicar_calibracion(payload: CalibracionRequest, campana_id: int = Query(...),
                        current_user: User = Depends(require_edit)):
    """Deja como vigentes los valores medidos.

    Es un paso explícito a propósito: la paciencia y el shrinkage mueven toda la
    dotación. PISA lo que esté puesto a mano y devuelve en `pisados` qué pisó."""
    _verificar_acceso(campana_id, current_user)
    try:
        resultado = servicio.aplicar_calibracion(
            engine, campana_id, paciencia_seg=payload.paciencia_seg,
            shrinkage_pool=payload.shrinkage_pool, dias=payload.dias,
            shrinkage=(payload.shrinkage.model_dump()
                       if payload.shrinkage else None),
            paciencia_por_skill=payload.paciencia_por_skill,
            disponibilidad=payload.disponibilidad)
        with engine.begin() as conn:
            _auditar(conn, current_user, "planificador.calibracion_aplicada",
                     "campana", campana_id, payload.model_dump())
        return resultado
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except Exception as e:
        logger.error(f"Error aplicando la calibración de {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/combinacion")
def combinacion(campana_id: int = Query(...),
                dias: int = Query(180, ge=60, le=365),
                # 2 y no 7: el pronóstico del fin de semana se usa con 1 a 3 días
                # de antelación, para pedir horas extra (Ignacio, 2026-09-14).
                antelacion: int = Query(2, ge=1, le=60),
                current_user: User = Depends(require_view)):
    """Cuánto peso merece el pronóstico que manda el cliente, medido.

    Corre el backtest sobre los últimos `dias` y compara los dos pronósticos
    contra lo que entró, partido por tipo de día. Devuelve el peso que saldría y
    qué error habría tenido la combinación, simulada hacia adelante.

    No cambia nada. TARDA: son varias corridas del backtest.
    """
    _verificar_acceso(campana_id, current_user)
    try:
        return servicio.calibrar_combinacion(engine, campana_id, dias=dias,
                                             antelacion=antelacion)
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error midiendo la combinación de {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/combinacion/aplicar")
def aplicar_combinacion(payload: CombinacionRequest, campana_id: int = Query(...),
                        current_user: User = Depends(require_edit)):
    """Deja vigentes los pesos medidos. No prende la combinación.

    Son dos decisiones distintas a propósito: medir el peso no tiene por qué
    cambiar el pronóstico que la operación está mirando. La llave es
    `combinar_cliente` en la configuración de la campaña.
    """
    _verificar_acceso(campana_id, current_user)
    try:
        with engine.begin() as conn:
            pdatos.guardar_pesos_combinacion(conn, campana_id,
                                             payload.peso_habil,
                                             payload.peso_no_habil)
            _auditar(conn, current_user, "planificador.combinacion_pesos",
                     "campana", campana_id, payload.model_dump())
        return {"ok": True}
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except Exception as e:
        logger.error(f"Error guardando los pesos de combinación de {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/seguimiento")
def seguimiento(campana_id: int = Query(...), dia: Optional[date] = None,
                horizonte: str = Query("operativo", pattern="^(operativo|presupuesto)$"),
                current_user: User = Depends(require_view)):
    """Cómo viene el día contra lo pronosticado, y cómo va a terminar.

    Contesta a media mañana lo que hoy se nota mirando el tablero: si el día va
    por arriba o por abajo, cuánto, y —cuando hay demanda total— si la causa es
    que el cliente tiene más llamadas o que nos están mandando una porción más
    grande. Son dos problemas distintos: uno se resuelve con más gente, el otro
    renegociando o recalibrando el reparto."""
    _verificar_acceso(campana_id, current_user)
    try:
        return servicio.seguimiento_intradia(engine, campana_id, dia, horizonte)
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except Exception as e:
        logger.error(f"Error en el seguimiento intradía de {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =========================================================================
# BACKTEST Y EXPORTACIÓN
# =========================================================================

@router.get("/backtest")
def obtener_backtest(campana_id: int = Query(...),
                     desde: date = Query(...),
                     hasta: date = Query(...),
                     antelacion: int = Query(servicio.ANTELACION_DEFECTO, ge=0, le=60),
                     clima: Optional[bool] = Query(
                         None, description="Fuerza el modelo de clima; None = lo configurado"),
                     nivel: Optional[bool] = Query(
                         None, description="Fuerza la segunda opinión del nivel; "
                                           "None = lo configurado"),
                     elasticidad: Optional[bool] = Query(
                         None, description="Fuerza la atenuación del clima del "
                                           "domingo; None = lo configurado"),
                     # El laboratorio: parámetros del modelo probados sin guardar.
                     # Mismos topes que PUT /config/campana.
                     semanas_base: Optional[int] = Query(None, ge=4, le=156),
                     dias_nivel: Optional[int] = Query(None, ge=7, le=120),
                     nivel_por_tipo_de_dia: Optional[bool] = Query(None),
                     reparto_deriva_dias: Optional[int] = Query(None, ge=0, le=60),
                     reparto_deriva_tope: Optional[float] = Query(None, ge=0, le=0.5),
                     reparto_tipo_dia_dias: Optional[int] = Query(None, ge=0, le=1100),
                     reparto_tipo_dia_tope: Optional[float] = Query(None, ge=0, le=0.5),
                     feriado_como_sabado: Optional[bool] = Query(None),
                     puente_factor: Optional[float] = Query(None, ge=0.1, le=1.5),
                     persistencia_peso_hoy: Optional[float] = Query(None, ge=0, le=1),
                     persistencia_peso_resto: Optional[float] = Query(None, ge=0, le=1),
                     persistencia_dias: Optional[int] = Query(None, ge=0, le=30),
                     forma_dias: Optional[int] = Query(None, ge=7, le=91),
                     persistencia_saltea_eventos: Optional[bool] = Query(None),
                     en_segundo_plano: bool = Query(
                         False, description="Lanza la comparación y devuelve un job_id "
                                            "para seguirla en /planificador/trabajos/{id}"),
                     current_user: User = Depends(require_view)):
    """Qué habría pronosticado el planificador para días que ya pasaron.

    Es la forma de contestar "¿el pronóstico está cerca de lo real?" sin esperar
    a que pasen los días: se rehace la línea de base con la historia que había
    `antelacion` días antes de cada jornada y se la compara contra lo que
    efectivamente entró, que sale del reporte de skills —el que carga cada hora y
    trae sólo nuestras llamadas—.

    No escribe nada: ni corrida, ni eventos. Por eso alcanza con `planificador.view`.
    """
    _verificar_acceso(campana_id, current_user)
    try:
        ajustes = {
            "semanas_base": semanas_base, "dias_nivel": dias_nivel,
            "nivel_por_tipo_de_dia": nivel_por_tipo_de_dia,
            "reparto_deriva_dias": reparto_deriva_dias,
            "reparto_deriva_tope": reparto_deriva_tope,
            "reparto_tipo_dia_dias": reparto_tipo_dia_dias,
            "reparto_tipo_dia_tope": reparto_tipo_dia_tope,
            "feriado_como_sabado": feriado_como_sabado,
            "puente_factor": puente_factor,
            "persistencia_peso_hoy": persistencia_peso_hoy,
            "persistencia_peso_resto": persistencia_peso_resto,
            "persistencia_dias": persistencia_dias,
            "forma_dias": forma_dias,
            "persistencia_saltea_eventos": persistencia_saltea_eventos,
        }
        def _correr():
            return servicio.backtest(engine, campana_id, desde, hasta,
                                     antelacion=antelacion, clima=clima, nivel=nivel,
                                     elasticidad=elasticidad, ajustes=ajustes)
        if en_segundo_plano:
            # Una comparación larga no entra en una request: el frontend corre bajo
            # gunicorn, que mata al worker a los 30 s y devuelve SU página de error
            # (el "Error 500" en HTML). Con el GBDT prendido cada día evaluado
            # reajusta el modelo (~3 s), así que un mes ya pasa el minuto. Mismo
            # patrón que la revisión de plantillas: se lanza y se consulta.
            # jsonable_encoder antes de guardar: el trabajo vive en un .json y las
            # fechas tienen que quedar como las serializa FastAPI (ISO con "T").
            job_id = revision_jobs.lanzar(
                "planificador_backtest", getattr(current_user, "usuario", None), None,
                lambda: jsonable_encoder(_correr()))
            return {"job_id": job_id, "estado": revision_jobs.ESTADO_EN_CURSO}
        return _correr()
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error en el backtest de la campaña {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trabajos/{job_id}")
def estado_de_trabajo(job_id: str, current_user: User = Depends(require_view)):
    """Estado de una comparación lanzada en segundo plano. Sólo la ve quien la lanzó."""
    job = revision_jobs.obtener(job_id)
    if job is None or job.get("tipo") != "planificador_backtest":
        return {"job_id": job_id, "estado": "desconocido"}
    if job.get("usuario_id") is not None and job["usuario_id"] != getattr(current_user, "usuario", None):
        raise HTTPException(status_code=403, detail="Ese trabajo es de otra persona.")
    return revision_jobs.para_respuesta(job)


def _adjunto(flujo, nombre: str) -> StreamingResponse:
    return StreamingResponse(
        flujo, media_type=export.MEDIA_XLSX,
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@router.get("/plan.xlsx")
def exportar_plan(campana_id: int = Query(...),
                  horizonte: str = Query("operativo", pattern="^(operativo|presupuesto)$"),
                  current_user: User = Depends(require_view)):
    """La corrida vigente en Excel: requerimiento, pronóstico y parámetros.

    Va la hoja de parámetros a propósito. Una dotación sin los supuestos con los
    que salió no se puede defender en una reunión dos semanas después, que es
    justamente cuando se discute."""
    _verificar_acceso(campana_id, current_user)
    try:
        plan = servicio.plan_vigente(engine, campana_id, horizonte)
        if not plan.get("corrida"):
            raise HTTPException(
                status_code=404,
                detail="Todavía no hay ninguna corrida para exportar. Recalculá primero.")
        config = _armar_config(campana_id)
        with engine.connect() as conn:
            campana = pdatos.nombre_de_campana(conn, campana_id)
        corrida = plan["corrida"]
        nombre = export.nombre_de_archivo(
            f"planificacion_{_slug(campana)}", corrida.get("Desde"), corrida.get("Hasta"))
        return _adjunto(export.libro_del_plan(plan, config, campana), nombre)
    except HTTPException:
        raise
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except Exception as e:
        logger.error(f"Error exportando el plan de la campaña {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/backtest.xlsx")
def exportar_backtest(campana_id: int = Query(...),
                      desde: date = Query(...),
                      hasta: date = Query(...),
                      antelacion: int = Query(servicio.ANTELACION_DEFECTO, ge=1, le=60),
                      clima: Optional[bool] = Query(None),
                      nivel: Optional[bool] = Query(None),
                      elasticidad: Optional[bool] = Query(None),
                      current_user: User = Depends(require_view)):
    """El backtest en Excel: resumen, día por día e intervalo por intervalo."""
    _verificar_acceso(campana_id, current_user)
    try:
        bt = servicio.backtest(engine, campana_id, desde, hasta,
                               antelacion=antelacion, clima=clima, nivel=nivel,
                               elasticidad=elasticidad)
        with engine.connect() as conn:
            campana = pdatos.nombre_de_campana(conn, campana_id)
        nombre = export.nombre_de_archivo(
            f"comparacion_{_slug(campana)}", bt["desde"], bt["hasta"])
        return _adjunto(export.libro_del_backtest(
            bt, campana, pdatos.ETIQUETA_REFUERZO.get(campana_id, "Refuerzo")), nombre)
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error exportando el backtest de la campaña {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _slug(texto: str) -> str:
    """Nombre de archivo sin acentos ni espacios: viaja por mail y por Windows."""
    limpio = unicodedata.normalize("NFKD", texto or "")
    limpio = "".join(c for c in limpio if not unicodedata.combining(c))
    return "".join(c if c.isalnum() else "-" for c in limpio).strip("-").lower() or "campana"


# =========================================================================
# NECESIDADES DE REFUERZO
# =========================================================================

@router.get("/necesidades")
def obtener_necesidades(campana_id: int = Query(...),
                        horizonte: str = Query("operativo",
                                               pattern="^(operativo|presupuesto)$"),
                        current_user: User = Depends(require_view)):
    """Qué refuerzos hacen falta, agrupados en bloques pedibles.

    Convierte la brecha por intervalo en algo que alguien puede pedir: "tres
    personas de 14 a 18 el jueves", con la antelación que queda y el canal que
    corresponde —mover la malla, hora extra programada o convocatoria del mismo
    día—, más el mismo tramo dimensionado con el escenario alto."""
    _verificar_acceso(campana_id, current_user)
    try:
        return servicio.necesidades(engine, campana_id, horizonte)
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except Exception as e:
        logger.error(f"Error calculando las necesidades de {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/necesidades.xlsx")
def exportar_necesidades(campana_id: int = Query(...),
                         horizonte: str = Query("operativo",
                                                pattern="^(operativo|presupuesto)$"),
                         current_user: User = Depends(require_view)):
    """El pedido de refuerzos en Excel, para mandarlo a RRHH."""
    _verificar_acceso(campana_id, current_user)
    try:
        datos = servicio.necesidades(engine, campana_id, horizonte)
        if not datos.get("hay_corrida"):
            raise HTTPException(
                status_code=404,
                detail="Todavía no hay ninguna corrida. Recalculá primero.")
        with engine.connect() as conn:
            campana = pdatos.nombre_de_campana(conn, campana_id)
        nombre = export.nombre_de_archivo(
            f"refuerzos_{_slug(campana)}", datos.get("desde"), datos.get("hasta"))
        return _adjunto(export.libro_de_refuerzos(datos, campana), nombre)
    except HTTPException:
        raise
    except pdatos.MigracionPendiente as e:
        raise _sin_migracion(e)
    except Exception as e:
        logger.error(f"Error exportando los refuerzos de {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/error-horizonte")
def obtener_error_por_horizonte(campana_id: int = Query(...),
                                desde: date = Query(...),
                                hasta: date = Query(...),
                                current_user: User = Depends(require_view)):
    """Cuánto erra el pronóstico según con cuánta antelación se haga.

    Es lo que alimenta el escenario alto de los refuerzos. Se mide y no se
    supone: el error a 30 días no es "el doble" del de un día, y suponerlo
    llevaría a pedir horas extra por ruido o a no pedirlas cuando hacen falta.
    Tarda: corre el backtest completo una vez por cada antelación."""
    _verificar_acceso(campana_id, current_user)
    try:
        return {"desde": desde, "hasta": hasta,
                "medido": servicio.medir_error_por_horizonte(
                    engine, campana_id, desde, hasta),
                "en_uso": servicio.ERROR_POR_HORIZONTE}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error midiendo el error por horizonte de {campana_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
