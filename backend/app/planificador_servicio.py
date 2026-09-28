"""Orquestación del planificador: la corrida completa, de punta a punta.

Junta las tres piezas —datos (`planificador_datos`), lógica (`planificador`) y
colas (`planificador_erlang`)— en las dos operaciones que expone la pantalla:

    recalcular()  arma el pronóstico y el requerimiento y los persiste
    calibrar()    mide contra los datos la paciencia y la disponibilidad

QUÉ HACE UNA CORRIDA
--------------------
1. Lee la serie histórica de la campaña (que hoy es SOLO nuestra porción; ver el
   encabezado de `planificador_datos`).
2. Detecta días atípicos y los suma al calendario de eventos como candidatos, sin
   confirmar: el que decide si un pico fue un corte masivo o un martes raro es una
   persona. Los ya cargados se excluyen del entrenamiento.
3. Arma la línea de base estacional por skill y le aplica los ajustes manuales.
4. Le pone TMO a cada intervalo desde el perfil histórico (que no es plano: en
   Voltara va de 158s a la noche a 303s en el pico).
5. Dimensiona por pool y guarda todo bajo una corrida versionada.

Lo único que va a cambiar cuando esté la descarga del 100% de las llamadas es el
paso 3: el modelo de volumen. Todo lo demás queda igual, y por eso se construyó
primero.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app import planificador as pl
from app import planificador_clima as pclima
from app import planificador_combinacion as pcomb
from app import planificador_datos as pdatos
from app import planificador_extras as pextras
from app import planificador_nivel as pnivel
from app import planificador_pedidos as ppedidos
from app import planificador_turnos as pturnos

logger = logging.getLogger(__name__)

# Cuánta historia se lee para armar el perfil. Ocho semanas de línea de base más
# margen para que la detección de atípicos tenga con qué comparar.
DIAS_DE_HISTORIA = 180


def _ventana_de_lectura(cfg, estado_clima, forzar_clima: Optional[bool] = None) -> int:
    """Cuántos días de historia se leen. Dos ventanas distintas en una lectura.

    El perfil estacional usa `semanas_base`; el modelo de clima quiere mucho más,
    para haber visto varios veranos y varios inviernos. Se lee el máximo de las
    dos y cada uno recorta lo suyo: leer dos veces la tabla del IVR para eso
    costaría el doble y no cambiaría un número.
    """
    usar = (estado_clima or {}).get("activo") if forzar_clima is None else forzar_clima
    base = _dias_de_historia(cfg)
    return max(base, DIAS_DE_HISTORIA_CLIMA) if usar else base


def _clima_del_pronostico(conn, campana_id: int, cfg, desde_hist: date,
                          hasta: date, forzar: Optional[bool] = None):
    """Lee el clima y decide si se usa. Devuelve (observado, todo, usar).

    `observado` es con lo que se ENTRENA (sólo lo que efectivamente pasó);
    `todo` incluye el pronóstico meteorológico, que es lo único que hay sobre el
    futuro y por eso es con lo que se PREDICE. Mezclarlos al entrenar le enseñaría
    al modelo el error del meteorólogo además del propio.
    """
    estado = pdatos.clima_de_la_campana(conn, campana_id)
    usar = estado["activo"] if (forzar is None and estado) else bool(forzar)
    if not estado or not usar:
        return {}, {}, False
    fin = hasta + timedelta(days=1)
    return (pdatos.clima_por_dia(conn, campana_id, desde_hist, fin, solo_observado=True),
            pdatos.clima_por_dia(conn, campana_id, desde_hist, fin),
            True)


def _series_diarias_por_skill(serie, sobre_total: bool) -> Dict[int, Dict[date, float]]:
    """skill -> {día: llamadas}. Es la serie sobre la que entrena el clima.

    POR SKILL y no agregada: las colas de Voltara no responden igual al tiempo.
    EMERGENCIAS explica el 44% de su variación con el clima y COMERCIAL el 7%, y
    como COMERCIAL es el 40% de un día hábil, un modelo ajustado sobre el total
    sale diluido. Eso rompía los fines de semana, que son 98,9% EMERGENCIAS: el
    día más sensible al clima recibía el factor más promediado. Ver el encabezado
    de `ajustar_por_skill` en planificador_clima.
    """
    salida: Dict[int, Dict[date, float]] = {}
    for clave, v in serie.items():
        momento, skill_id = clave
        valor = v["total"] if sobre_total else v[0]
        salida.setdefault(skill_id, {})
        dia = momento.date()
        salida[skill_id][dia] = salida[skill_id].get(dia, 0.0) + valor
    # Un día con datos en la fuente y sin llamadas del skill es un CERO, no un
    # faltante: sin esto la mediana diaria de una cola rala sale sólo de los días
    # en que tuvo llamadas (ver `pl._rellenar_ceros`). Desde el primer día del
    # skill, para no inventarle ceros a una cola nueva.
    observados = _dias_observados(serie)
    for por_dia in salida.values():
        primero = min(por_dia)
        for dia in observados:
            if dia >= primero:
                por_dia.setdefault(dia, 0.0)
    return salida


def _dias_observados(serie) -> set:
    """Días en los que la fuente trae datos de algún skill."""
    return {momento.date() for momento, _ in serie}


def _dias_de_historia(cfg) -> int:
    """Cuánta historia hay que leer para que la ventana configurada exista.

    Si se piden 52 semanas de línea de base y se leen 180 días, la ventana real
    son 26 semanas y el parámetro es letra muerta —pasó, y por eso está acá el
    margen explícito—. El extra cubre la corrección de nivel y la detección de
    días atípicos, que miran hacia atrás desde el borde de la ventana.
    """
    return max(DIAS_DE_HISTORIA, cfg.semanas_base * 7 + cfg.dias_nivel + 14)


# Historia que se lee cuando el clima está activo. El perfil estacional no la
# quiere —una mediana sobre tres años deja de seguir al nivel actual— pero el
# modelo de clima SÍ: con un año de entrenamiento vio un solo verano, y como el
# efecto del calor es superlineal, extrapola justo en los días que importan.
# Medido: con 402 días el clima MEJORA junio (35,3% -> 23,9% de error diario) y
# EMPEORA enero (42,3% -> 44,5%), con el factor pegado contra el tope.
DIAS_DE_HISTORIA_CLIMA = 1100

# Qué rasgos usa el modelo de clima. Se elige acá y no en el módulo de clima
# porque es una decisión MEDIDA, no de diseño: cada grupo se evaluó con el
# backtest sobre cinco períodos y entró sólo el que ganó.
#
# ERROR ABSOLUTO MEDIO DEL TOTAL DIARIO, antelación 7 días:
#
#     combinación            ago-sep  junio  enero  octubre  marzo   PROM
#     sin clima                29,9%  35,3%  42,3%   34,5%   53,8%  39,1%
#     clima (8 rasgos)         31,0%  23,4%  43,1%   33,4%   29,9%  32,1%
#     + persistencia (12)      30,9%  22,5%  42,3%   30,1%   25,3%  30,2%  <-- ESTE
#     + clima_extra (11)       28,6%  27,8%  47,7%   33,9%   35,7%  34,7%
#     + anual (12)             26,1%  27,3%  46,1%   31,7%   29,5%  32,1%
#     + calendario (14)        29,2%  22,7%  43,0%   35,1%   28,8%  31,8%
#     + anual + calendario     24,9%  26,8%  45,2%   33,1%   27,6%  31,5%
#     TODO (25 rasgos)         21,7%  25,7%  47,4%   30,1%   26,5%  30,3%
#
# POR QUÉ CLIMA + PERSISTENCIA Y NO "TODO":
#   - Empatan en el promedio (30,2% contra 30,3%) con doce rasgos en vez de
#     veinticinco. Con ~1.100 días de entrenamiento, trece parámetros de más que
#     no compran nada son trece formas de aprender ruido.
#   - Y no es un empate parejo: "TODO" es el mejor en agosto-septiembre (21,7%)
#     y el PEOR de todos en enero (47,4%). Ese patrón —mejora cerca del final del
#     entrenamiento y empeora lejos— es la firma del sobreajuste.
#   - La persistencia es además la única combinación que arregla el verano:
#     el clima solo lo empeoraba (42,3% -> 43,1%) y con los acumulados vuelve a
#     42,3%. Tiene sentido físico: lo que rompe la red no es el día caluroso,
#     es el tercero seguido.
#
# LO QUE SE PROBÓ Y NO ENTRÓ:
#   - humedad, viento y amplitud térmica ("clima_extra"): es la PEOR combinación
#     de todas (34,7%). Vienen gratis en la misma llamada a la API, y aun así
#     empeoran: la humedad ya está adentro de la temperatura aparente y el resto
#     es ruido.
#   - estacionalidad anual y calendario (facturación, vísperas, vacaciones):
#     mejoran mucho un período y empeoran otro, y en promedio no mueven la aguja.
#     Quedan implementados y medibles para volver a probarlos cuando haya más
#     historia; hoy no se ganan el lugar.
#
# 2026-09-14: ENTRA "estacional" (el frío modulado por la época del año). Se midió
# distinto que la tabla de arriba —un año corrido, antelación 1 a 3 días, que es la
# que usa la operación para pedir horas extra— y ya con el arreglo de las medias
# horas sin llamadas (`pl._rellenar_ceros`). MAPE del total diario del cliente:
#
#                              hábil   sábado  domingo  feriado   todo   últ. 30 d
#     clima + persistencia     14,1%   26,7%   34,9%    29,2%    19,6%     33,7%
#     + estacional (18)        13,3%   24,8%   31,0%    31,8%    18,4%     19,6%  <-- ESTE
#     + estacional + calor     13,3%   24,1%   31,3%    31,9%    18,4%     19,6%
#     + carga CAMMESA          ver planificador_clima, GRUPOS["estacional"]
#     sin clima                19,9%   43,2%   42,9%    32,2%    27,1%     25,9%
RASGOS_ACTIVOS = (pclima.GRUPOS["clima"] + pclima.GRUPOS["persistencia"]
                  + pclima.GRUPOS["estacional"])

MODELO_BASE = "baseline-estacional-v1"


def recalcular(engine: Engine, campana_id: int, dias: int = 14,
               horizonte: str = "operativo", usuario: Optional[int] = None,
               hoy: Optional[date] = None,
               origen: str = "manual") -> Dict[str, object]:
    """Corre el planificador y persiste la corrida. Devuelve el resumen."""
    hoy = hoy or date.today()
    desde_pron = hoy
    hasta_pron = hoy + timedelta(days=dias)
    with engine.begin() as conn:
        cfg = pdatos.cargar_config(conn, campana_id)
        estado_clima = pdatos.clima_de_la_campana(conn, campana_id)
        desde_hist = hoy - timedelta(days=_ventana_de_lectura(cfg, estado_clima))

        # Con la descarga completa del cliente el pronóstico se hace sobre la
        # DEMANDA TOTAL —que es la que depende del clima, los feriados y los
        # cortes— y el reparto se aplica después. Sin ella sólo tenemos nuestra
        # porción, que mezcla las dos cosas y se rompe cuando cambia el contrato.
        sobre_total = pdatos.hay_demanda_total(conn, campana_id)
        if sobre_total:
            cruda = pdatos.serie_demanda_total(conn, campana_id, desde_hist, hoy,
                                               cfg.intervalo_min)
            serie = {k: (v["total"], v["tmo"]) for k, v in cruda.items()}
            asignacion = pdatos.asignacion_configurada(conn, campana_id,
                                                       desde_pron, hasta_pron)
            # Sin tramos cargados el pronóstico saldría VACÍO, que es el peor de
            # los desenlaces: aplicar la migración de la demanda total y olvidar
            # la de asignación dejaría la pantalla en blanco sin decir por qué.
            # Se cae al reparto medido del régimen vigente y se avisa fuerte.
            asignacion_medida = None
            if not asignacion:
                asignacion_medida = pdatos.medir_asignacion(
                    conn, campana_id, hoy - timedelta(days=60), hoy)
                asignacion = _tramos_desde_medicion(asignacion_medida)
            # Los tramos que rigieron HACIA ATRÁS, que son otros: la deriva y el
            # factor por tipo de día comparan lo que nos llegó contra lo que el
            # tramo de ESE día decía, y un tramo que terminó ayer no entra en la
            # lectura del horizonte futuro. Sin esta segunda lectura los días
            # previos a un cambio de régimen quedaban sin porcentaje y las dos
            # correcciones se apagaban solas justo después del escalón.
            asignacion_hist = asignacion if asignacion_medida else (
                pdatos.asignacion_configurada(
                    conn, campana_id,
                    hoy - timedelta(days=max(cfg.reparto_tipo_dia_dias,
                                             cfg.reparto_deriva_dias)),
                    hasta_pron))
        else:
            cruda = {}
            serie = pdatos.serie_por_skill(conn, campana_id, desde_hist, hoy)
            asignacion, asignacion_medida, asignacion_hist = [], None, []

        if not serie:
            raise ValueError(
                f"No hay serie histórica para la campaña {campana_id} "
                f"entre {desde_hist} y {hoy}.")

        fer = pdatos.feriados(conn, desde_hist, hasta_pron, campana_id)
        # El dimensionamiento necesita saber qué días son feriado: el shrinkage
        # de un feriado es otro (5,3% contra 8,9%). Sin esto un feriado se
        # dimensiona como el día de semana que le toque.
        cfg.feriados = frozenset(fer)
        nuevos = _sembrar_atipicos(conn, campana_id, serie, fer, cfg)
        excluidos = pdatos.dias_a_excluir(conn, campana_id, desde_hist, hoy)
        ajustes = pdatos.ajustes_vigentes(conn, campana_id, desde_pron, hasta_pron)

        clima_obs, clima_todo, usar_clima = _clima_del_pronostico(
            conn, campana_id, cfg, desde_hist, hasta_pron)
        # Una sola vez: la usan el modelo de clima y el de nivel.
        diarias = _series_diarias_por_skill(cruda if sobre_total else serie,
                                            sobre_total)
        modelos_clima = pclima.ajustar_por_skill(
            diarias, clima_obs, hoy, semanas=cfg.semanas_base, feriados=fer,
            excluidos=excluidos, rasgos=RASGOS_ACTIVOS) if usar_clima else {}

        perfil_tmo = pdatos.perfil_de_tmo(serie)
        dias_futuros = [desde_pron + timedelta(days=i) for i in range(dias)]

        # El factor de clima se calcula para TODOS los días que tocan el cálculo,
        # no sólo los futuros: la corrección de nivel mira hacia atrás y necesita
        # saber qué parte de esos días ya la explicaba el tiempo.
        dias_del_calculo = [desde_hist + timedelta(days=i)
                            for i in range((hasta_pron - desde_hist).days + 1)]
        factor_clima = pclima.factores_por_skill(
            modelos_clima, dias_del_calculo, clima_todo, fer)
        # La elasticidad va DESPUÉS de calcular los factores y ANTES de todo lo
        # demás: la corrección de nivel tiene que ver el factor ya atenuado, o
        # seguiría compensando un movimiento que dejó de aplicarse.
        elasticidad = (pclima.elasticidad_por_tipo_de_dia(
            diarias, factor_clima, hoy, semanas=cfg.semanas_base,
            feriados=fer, excluidos=excluidos)
            if (usar_clima and cfg.clima_elasticidad_tipo_dia) else {})
        factor_clima = pclima.aplicar_elasticidad(factor_clima, elasticidad, fer)

        # Segunda opinión del nivel diario. Sólo tiene sentido con el clima
        # prendido —sus rasgos son el tiempo y el calendario—, así que si el
        # clima está apagado esto también. Ver `planificador_nivel`.
        modelo_nivel = (pnivel.ajustar(
            diarias, clima_obs, hoy, semanas=cfg.semanas_base,
            feriados=fer, excluidos=excluidos)
            if (usar_clima and cfg.nivel_gbdt and cfg.nivel_gbdt_peso) else None)
        nivel_diario = pnivel.niveles_por_skill(
            modelo_nivel, diarias, dias_del_calculo, clima_todo, hoy,
            semanas=cfg.semanas_base, feriados=fer, excluidos=excluidos)

        # Deriva del reparto: el tramo dice el régimen, esto sigue el vaivén
        # reciente alrededor de ese número. Ver `pdatos.deriva_de_reparto`.
        deriva = (_deriva_del_reparto(cfg, cruda, asignacion_hist, hoy)
                  if sobre_total else {})
        # Sólo para los días cercanos al corte. Más allá de la ventana con la que
        # se midió, la deriva deja de ser información y empeora el pronóstico.
        hasta_deriva = hoy + timedelta(days=cfg.reparto_deriva_dias)
        # El reparto de un día no hábil. A diferencia de la deriva, este SÍ se
        # aplica a todo el horizonte: no depende de la frescura del dato sino de
        # qué día de la semana cae, que se sabe con un año de anticipación.
        factor_no_habil = (pdatos.factor_reparto_no_habil(
            asignacion_hist, pdatos.reparto_diario(cruda), hoy,
            cfg.reparto_tipo_dia_dias, fer, cfg.reparto_tipo_dia_tope)
            if sobre_total else None)

        pronostico, filas_pron, sin_asignacion = _pronosticar(
            cfg, serie, dias_futuros, fer, excluidos, ajustes, perfil_tmo, hoy,
            sobre_total=sobre_total, asignacion=asignacion,
            factor_clima=factor_clima, deriva_reparto=deriva,
            deriva_hasta=hasta_deriva, factor_no_habil=factor_no_habil,
            nivel_diario=nivel_diario,
            peso_nivel=cfg.nivel_gbdt_peso if modelo_nivel else 0.0)

        # La combinación con el pronóstico del cliente va al final, sobre el
        # pronóstico ya armado: mueve el NIVEL del día y deja la curva intradía y
        # el reparto entre skills como estaban. Ver planificador_combinacion.
        combinacion = _combinar_con_cliente(conn, cfg, campana_id, filas_pron,
                                            pronostico, dias_futuros, fer)
        # El total del mes que manda el cliente, para lo que queda del mes a
        # partir de unos días (Gasur). Va después de la combinación y antes del
        # intradía, que corrige sólo lo que queda de hoy.
        ancla = _anclar_al_mes_del_cliente(conn, cfg, campana_id, serie, filas_pron,
                                           pronostico, hoy)
        # Lo que queda de hoy, reescalado por cómo viene lo que ya entró. Sólo en
        # el recálculo del día (el de las 14:00 del cron o uno manual) y siempre
        # contra NUESTRAS llamadas, que cargan cada hora: la demanda total del
        # cliente de hoy recién está a la madrugada siguiente. Con el reparto
        # fijo, corregir nuestra porción corrige el total en la misma proporción.
        # Voltara, año 2025-09-24 a 2026-09-23 sobre el plan de la mañana con
        # GBDT y persistencia, error del resto del día por skill y media hora:
        # desde las 14, 35,9% -> 34,7% (con una hora de atraso de la carga) o
        # 33,8% (sin atraso); desde las 12 no gana y desde las 10 empeora.
        intradia = (_reescalar_hoy(conn, cfg, campana_id, pronostico, filas_pron)
                    if (cfg.intradia_desde_hora is not None
                        and hoy == date.today()) else None)

        modelo = MODELO_BASE + ("+clima" if modelos_clima else "")
        corrida_id = pdatos.crear_corrida(conn, campana_id, horizonte, desde_pron,
                                          hasta_pron, modelo, usuario)
        pdatos.guardar_pronostico(conn, corrida_id, filas_pron)

        requerimientos: List[pl.RequerimientoPool] = []
        sin_malla = []
        for pool in cfg.pools.values():
            del_pool = pl.plan_de_pool(cfg, pool, pronostico)
            # Comparación contra la malla real de RRHH. Es lo que convierte el
            # plan en algo accionable: sin esto el número es correcto pero nadie
            # sabe si hay que contratar, mover turnos o no hacer nada.
            citados = pdatos.dotacion_planificada(
                conn, pool.pool_id, desde_pron, hasta_pron, cfg.intervalo_min)
            if citados:
                # Con la curva de antigüedad medida, la brecha va contra los
                # citados en equivalentes: la gente en sus primeras semanas de
                # piso tarda más por llamada. Ver `planificador_antiguedad`.
                equivalentes = (pdatos.dotacion_equivalente(
                    conn, pool.pool_id, desde_pron, hasta_pron,
                    cfg.curva_antiguedad, cfg.intervalo_min)
                    if cfg.curva_antiguedad else {})
                for r in del_pool:
                    r.planificados = citados.get(r.momento, 0)
                    if cfg.curva_antiguedad:
                        r.planificados_equivalentes = equivalentes.get(r.momento, 0.0)
            elif pool.origen_rrhh:
                sin_malla.append(pool.nombre)
            # La palanca cuando no se llega: gente de sub-campañas sin SLA
            # (Digital) que se puede pasar a la línea. Sólo tiene sentido contra
            # una malla —cubre un faltante— y no mueve lo que hay que citar. Ver
            # `pl.RequerimientoPool.refuerzo_cubre`.
            if citados and pdatos.campanas_refuerzo_del_pool(conn, pool.pool_id):
                refuerzo = pdatos.refuerzo_planificado(
                    conn, pool.pool_id, desde_pron, hasta_pron, cfg.intervalo_min)
                for r in del_pool:
                    r.refuerzo_disponible = refuerzo.get(r.momento, 0)
            requerimientos.extend(del_pool)
        pdatos.guardar_requerimiento(conn, corrida_id, requerimientos)

        resumen = pl.resumen_de_plan(requerimientos, cfg.intervalo_min)
        resumen["atipicos_nuevos"] = nuevos
        resumen["brecha"] = _resumen_de_brecha(requerimientos)
        con_equivalentes = [r for r in requerimientos
                            if r.planificados is not None
                            and r.planificados_equivalentes is not None]
        if con_equivalentes:
            por_hora = 60 / cfg.intervalo_min
            resumen["antiguedad"] = {
                "horas_citadas": round(sum(r.planificados for r in con_equivalentes)
                                       / por_hora, 1),
                "horas_equivalentes": round(sum(r.planificados_equivalentes
                                                for r in con_equivalentes) / por_hora, 1),
                "medido_en": (cfg.curva_antiguedad_medido_en.isoformat()
                              if cfg.curva_antiguedad_medido_en else None),
            }

        def _agregar_aviso(texto: str, codigo: str, nivel: str = "alerta",
                           accion: Optional[dict] = None) -> None:
            resumen.setdefault("avisos", []).append(texto)
            resumen.setdefault("avisos_detalle", []).append({
                "codigo": codigo,
                "nivel": nivel,
                "texto": texto,
                "accion": accion,
            })

        for nombre in sin_malla:
            _agregar_aviso(
                f"El pool «{nombre}» no tiene malla cargada en payroll para el período: "
                f"se puede ver cuánta gente hace falta, pero no compararla con la citada.",
                codigo="sin_malla", nivel="alerta", accion=None)
        pools_sin_origen = [p.nombre for p in cfg.pools.values() if not p.origen_rrhh]
        if pools_sin_origen:
            _agregar_aviso(
                "Sin sub-campañas de RRHH asociadas, estos pools no se pueden comparar "
                "contra la malla ni medirles el ausentismo: " + ", ".join(pools_sin_origen),
                codigo="pools_sin_origen_rrhh", nivel="alerta", accion=None)
        if asignacion_medida and asignacion_medida.get("vigente"):
            v = asignacion_medida["vigente"]
            _agregar_aviso(
                f"No hay tramos de asignación cargados: se usó el reparto MEDIDO de "
                f"los últimos {v['dias']} días (desde el {v['desde']}, "
                f"{v['porcentaje']:.1%} en promedio). Cargalo en Configuración → "
                f"Asignación para poder proyectar un cambio de contrato.",
                codigo="asignacion_medida", nivel="config",
                accion={"pestana": "config", "ancla": "slot-config-reparto"})
        if sin_asignacion:
            _agregar_aviso(
                "Estos skills tienen demanda del cliente pero no tienen cargado qué "
                "porcentaje nos asignan, así que quedaron fuera del pronóstico: "
                + ", ".join(sin_asignacion),
                codigo="skills_sin_asignacion", nivel="config",
                accion={"pestana": "config", "ancla": "slot-config-reparto"})
        if modelos_clima:
            nombres_skill = {sk.skill_id: sk.nombre for sk in cfg.skills}
            resumen["clima"] = {
                "skills": {nombres_skill.get(k, str(k)): m.como_dict()
                           for k, m in modelos_clima.items()},
                "sin_factor": sorted(
                    nombres_skill.get(sk.skill_id, str(sk.skill_id))
                    for sk in cfg.skills
                    if sk.activo and sk.pool_id is not None
                    and sk.skill_id not in modelos_clima),
                "factores": {
                    nombres_skill.get(k, str(k)): {str(d): round(f, 3)
                                                   for d, f in sorted(v.items())
                                                   if d >= desde_pron}
                    for k, v in factor_clima.items()},
            }
        elif usar_clima:
            _agregar_aviso(
                "El clima está activado pero no alcanzó para ajustar el modelo: hacen "
                f"falta al menos {pclima.MIN_DIAS_PARA_AJUSTAR} días observados. "
                "Corré scripts/clima_voltara.py --desde 2024-01-01.",
                codigo="clima_insuficiente", nivel="alerta", accion=None)
        cubiertos = set().union(*factor_clima.values()) if factor_clima else set()
        faltan_clima = [d for d in dias_futuros if d not in cubiertos]
        if usar_clima and modelos_clima and faltan_clima:
            _agregar_aviso(
                f"{len(faltan_clima)} de los {len(dias_futuros)} días pronosticados no "
                "tienen clima cargado y salieron sin corrección: el pronóstico "
                "meteorológico llega hasta 16 días.",
                codigo="clima_horizonte", nivel="info", accion=None)
        if factor_no_habil:
            resumen["reparto_no_habil"] = round(factor_no_habil, 4)
            if abs(factor_no_habil - 1) >= 0.02:
                signo = "más" if factor_no_habil > 1 else "menos"
                _agregar_aviso(
                    f"Los sábados, domingos y feriados el reparto viene "
                    f"{abs(factor_no_habil - 1):.1%} {signo} alto que en los días "
                    f"hábiles (medido sobre los últimos {cfg.reparto_tipo_dia_dias} "
                    f"días), y el pronóstico de esos días lo corrige por ese factor.",
                    codigo="reparto_no_habil", nivel="alerta", accion=None)
        if combinacion:
            resumen["combinacion"] = combinacion
            if not combinacion["aplicado"] and combinacion.get("motivo"):
                _agregar_aviso(combinacion["motivo"], codigo="combinacion_motivo",
                               nivel="alerta", accion=None)
            elif combinacion.get("sin_cliente"):
                _agregar_aviso(
                    f"{len(combinacion['sin_cliente'])} de los {dias} días "
                    "pronosticados no tienen pronóstico del cliente cargado y salieron "
                    "sólo con el nuestro.",
                    codigo="combinacion_sin_cliente", nivel="alerta", accion=None)
        if ancla and ancla.get("aplicado"):
            resumen["ancla_mensual"] = ancla
            _agregar_aviso(
                "Desde el " + ancla["desde"] + ", lo que queda del mes se llevó hacia el "
                "total que mandó el cliente: " + ", ".join(
                    f"{m} x{f:.2f}" for m, f in ancla["factor_por_mes"].items()) + ".",
                codigo="ancla_mensual", nivel="info", accion=None)
        if intradia:
            resumen["intradia"] = intradia
            _agregar_aviso(
                f"Lo que queda de hoy se reescaló por cómo viene el día hasta las "
                f"{intradia['hasta']}: " + ", ".join(
                    f"{n} x{f:.2f}" for n, f in intradia["factores"].items()) + ".",
                codigo="intradia", nivel="info", accion=None)
        resumen["dias"] = dias
        resumen["modelo"] = modelo
        resumen["sobre_demanda_total"] = sobre_total
        resumen["origen"] = origen
        pdatos.cerrar_corrida(conn, corrida_id, campana_id, horizonte, resumen)

    resumen["corrida_id"] = corrida_id
    return resumen


def _combinar_con_cliente(conn, cfg: pl.CampanaCfg, campana_id: int,
                          filas: List[dict], demanda, dias_futuros,
                          feriados) -> Optional[dict]:
    """Mezcla el pronóstico con el que manda el cliente, si está configurado.

    Devuelve el detalle de lo que hizo —o de por qué no hizo nada— para que la
    pantalla lo pueda mostrar: un pronóstico que cambió de nivel sin decir por
    qué es peor que uno que no cambió.

    Los pesos NO se miden acá. Salen de la calibración (`calibrar_combinacion`),
    que corre el backtest y compara los dos pronósticos contra lo que realmente
    pasó. Medirlos en cada corrida obligaría a rehacer el backtest completo cada
    vez, y sobre todo tentaría a medirlos con los días que se están pronosticando.
    """
    if not cfg.combinar_cliente:
        return None
    pesos = {}
    if cfg.combinar_cliente_peso_habil:
        pesos["habil"] = pcomb.PesoCliente(
            "habil", float(cfg.combinar_cliente_peso_habil), 0, 0.0, 0.0,
            float(cfg.combinar_cliente_peso_habil))
    if cfg.combinar_cliente_peso_no_habil:
        pesos["no_habil"] = pcomb.PesoCliente(
            "no_habil", float(cfg.combinar_cliente_peso_no_habil), 0, 0.0, 0.0,
            float(cfg.combinar_cliente_peso_no_habil))
    if not pesos:
        return {"aplicado": False, "motivo": (
            "La combinación con el pronóstico del cliente está activada pero no hay "
            "pesos medidos. Corré la calibración en la pestaña Comparación.")}
    if not pdatos.hay_forecast_en_produccion(conn, campana_id):
        return {"aplicado": False, "motivo": (
            "La combinación está activada pero esta campaña no tiene pronóstico del "
            "cliente cargado en dbo.Forecast.")}

    desde, hasta = dias_futuros[0], dias_futuros[-1] + timedelta(days=1)
    cliente_crudo = pdatos.forecast_en_produccion(conn, campana_id, desde, hasta)
    if not cliente_crudo:
        return {"aplicado": False, "motivo": (
            f"El cliente no tiene pronóstico cargado entre el {desde} y el "
            f"{dias_futuros[-1]}: el pronóstico queda sólo con el nuestro.")}

    # Los dos totales se arman con los MISMOS skills. Comparar un total que
    # incluye colas que el cliente no pronostica contra uno que no las tiene
    # no mide el nivel: mide qué cubre cada uno.
    comunes = {f["skill"] for f in filas} & {sid for _, sid in cliente_crudo}
    if not comunes:
        return {"aplicado": False, "motivo": (
            "El pronóstico del cliente no cubre ninguno de los skills que "
            "pronosticamos nosotros.")}
    nuestro_dia: Dict[date, float] = {}
    for f in filas:
        if f["skill"] in comunes:
            d = f["momento"].date()
            nuestro_dia[d] = nuestro_dia.get(d, 0.0) + f["acme"]
    cliente_dia: Dict[date, float] = {}
    for (momento, sid), v in cliente_crudo.items():
        if sid in comunes:
            d = momento.date()
            cliente_dia[d] = cliente_dia.get(d, 0.0) + v

    factores = pcomb.factores_diarios(nuestro_dia, cliente_dia, pesos, feriados)
    dias = pcomb.aplicar(demanda, filas, factores)
    # Los días que el cliente no pronosticó, que NO es lo mismo que los días sin
    # factor: un día hábil no tiene factor porque su peso es cero, y eso es lo
    # esperado, no un agujero de datos.
    faltan = [d for d in dias_futuros if d not in cliente_dia]
    return {
        "aplicado": bool(dias),
        "dias": dias,
        "pesos": {k: v.como_dict() for k, v in pesos.items()},
        "medido_en": cfg.combinar_cliente_medido_en,
        "skills": sorted(comunes),
        "factores": {str(d): round(f, 3) for d, f in sorted(factores.items())},
        "sin_cliente": [str(d) for d in faltan],
    }


def _deriva_del_reparto(cfg: pl.CampanaCfg, cruda, asignacion, corte: date
                        ) -> Dict[int, float]:
    """skill -> cuánto corregir el tramo de asignación por deriva reciente.

    Sólo tiene sentido a corta antelación: el share del día siguiente
    correlaciona 0,46 con el de hoy y a siete días ya baja a 0,26. Medido sobre
    357 días, baja el error de 30,98% a 30,27% a un día y de 30,92% a 30,76% a
    siete. Por eso el que llama la aplica sólo a los días cercanos al corte
    (`deriva_hasta`): más lejos que su propia ventana, empeora.
    """
    if not cfg.reparto_deriva_dias or not cruda:
        return {}
    diario = pdatos.reparto_diario(cruda)
    salida = {}
    for skill in cfg.skills:
        if not skill.activo or skill.pool_id is None:
            continue
        d = pdatos.deriva_de_reparto(asignacion, diario, corte, skill.skill_id,
                                     cfg.reparto_deriva_dias,
                                     cfg.reparto_deriva_tope)
        if d is not None:
            salida[skill.skill_id] = d
    return salida


def _anclar_al_mes_del_cliente(conn, cfg: pl.CampanaCfg, campana_id: int, serie,
                               filas: List[dict], demanda, hoy: date) -> Optional[dict]:
    """Aplica `pl.factores_ancla_mensual` en el lugar. None si está apagado o el
    cliente no tiene cargado el total del mes en dbo.Forecast."""
    if not cfg.ancla_mensual_peso or not filas:
        return None
    if not pdatos.hay_forecast_en_produccion(conn, campana_id):
        return None
    dias = sorted({f["momento"].date() for f in filas})
    inicio = date(dias[0].year, dias[0].month, 1)
    fin = (date(dias[-1].year, dias[-1].month, 1) + timedelta(days=32)).replace(day=1)
    skills = {f["skill"] for f in filas}
    cliente_mes: Dict[Tuple[int, int], float] = {}
    for (momento, sid), v in pdatos.forecast_en_produccion(conn, campana_id, inicio,
                                                           fin).items():
        if sid in skills:
            clave = (momento.year, momento.month)
            cliente_mes[clave] = cliente_mes.get(clave, 0.0) + v
    real_del_mes: Dict[Tuple[int, int], float] = {}
    for (momento, sid), (llamadas, _) in serie.items():
        if sid in skills and inicio <= momento.date() < hoy:
            clave = (momento.year, momento.month)
            real_del_mes[clave] = real_del_mes.get(clave, 0.0) + llamadas
    nuestro_dia: Dict[date, float] = {}
    for f in filas:
        nuestro_dia[f["momento"].date()] = nuestro_dia.get(f["momento"].date(), 0.0) + f["acme"]
    factores = pl.factores_ancla_mensual(nuestro_dia, real_del_mes, cliente_mes, hoy,
                                         cfg.ancla_mensual_peso,
                                         cfg.ancla_mensual_desde_dias)
    if not factores:
        return {"aplicado": False, "motivo": (
            "Sin total del cliente para el mes o con menos de una semana de mes por delante.")}
    pcomb.aplicar(demanda, filas, factores)
    por_mes: Dict[str, float] = {}
    for d, f in factores.items():
        por_mes[d.strftime("%Y-%m")] = round(f, 3)
    return {"aplicado": True, "factor_por_mes": por_mes,
            "total_cliente": {f"{a}-{m:02d}": round(v) for (a, m), v in cliente_mes.items()},
            "desde": min(factores).isoformat()}


def _reescalar_hoy(conn, cfg: pl.CampanaCfg, campana_id: int, demanda, filas,
                   ahora: Optional[datetime] = None) -> Optional[dict]:
    """Aplica `pl.reescalar_intradia` a cada skill del pronóstico de hoy, en el
    lugar: toca las filas que se guardan y la demanda que se dimensiona. None si
    todavía es temprano o no entró nada."""
    ahora = ahora or datetime.now()
    if ahora.hour < (cfg.intradia_desde_hora or 0):
        return None
    hoy = ahora.date()
    real = pdatos.serie_por_skill(conn, campana_id, hoy, hoy + timedelta(days=1))
    # El informe de Voltara trae TODAS las medias horas del día desde temprano,
    # en cero hasta que cargan: un cero todavía no cargado se leería como una
    # media hora cerrada sin llamadas y hundiría el factor. Se corta en la última
    # media hora con llamadas de toda la campaña (reescalar_intradia descarta
    # después esa última, que viene a medias).
    ultimo = max((m for (m, _), (ll, _) in real.items() if ll > 0), default=None)
    if ultimo is None:
        return None
    real = {k: v for k, v in real.items() if k[0] <= ultimo}
    factores: Dict[int, float] = {}
    for sid in {f["skill"] for f in filas}:
        pron = {f["momento"]: f["acme"] for f in filas
                if f["skill"] == sid and f["momento"].date() == hoy}
        real_s = {m: ll for (m, s_), (ll, _) in real.items() if s_ == sid}
        g, nuevos = pl.reescalar_intradia(pron, real_s, ahora, cfg.intervalo_min)
        if g is None or not nuevos:
            continue
        factores[sid] = g
        for f in filas:
            if f["skill"] == sid and f["momento"] in nuevos:
                f["acme"] = round(nuevos[f["momento"]], 2)
                # Con la demanda del cliente, el total se corre igual: el
                # reparto de lo que queda del día no cambia.
                if f.get("total") is not None:
                    f["total"] = round(f["total"] * g, 2)
        for momento, llamadas in nuevos.items():
            for d in demanda.get(momento, []):
                if d.skill_id == sid:
                    d.llamadas = llamadas
    if not factores:
        return None
    nombres = {sk.skill_id: sk.nombre for sk in cfg.skills}
    return {"hasta": ahora.strftime("%H:%M"),
            "factores": {nombres.get(k, str(k)): round(v, 3) for k, v in factores.items()}}


def _calendario_y_persistencia(cfg: pl.CampanaCfg, desde: date, hasta: date) -> dict:
    """Lo que la línea de base necesita del calendario propio de la campaña y de
    la persistencia del desvío. Con la configuración por defecto (Voltara) no
    cambia nada: todo feriado es domingo y la persistencia está en cero."""
    return {
        "puentes": pdatos.puentes(desde, hasta, cfg.campana_id) if cfg.puente_factor is not None else (),
        "feriado_como": "sabado" if cfg.feriado_como_sabado else "domingo",
        "puente_factor": cfg.puente_factor,
        "persistencia": (cfg.persistencia_peso_hoy, cfg.persistencia_peso_resto,
                         cfg.persistencia_dias),
        "persistencia_saltea_eventos": cfg.persistencia_saltea_eventos,
        "forma_dias": cfg.forma_dias,
    }


def _pronosticar(cfg: pl.CampanaCfg, serie, dias_futuros, feriados, excluidos,
                 ajustes, perfil_tmo, hoy, sobre_total=False, asignacion=(),
                 factor_clima=None, deriva_reparto=None, deriva_hasta=None,
                 factor_no_habil=None, nivel_diario=None, peso_nivel=0.0):
    """Línea de base por skill, con reparto, ajustes manuales y TMO del perfil.

    Cuando `sobre_total` es True la línea de base pronostica la demanda del
    CLIENTE y hay que multiplicarla por el porcentaje que nos asignan. Un skill
    sin tramo de asignación cargado NO se pronostica en cero ni al 100%: se
    reporta, porque las dos suposiciones son formas distintas de equivocarse
    callado.
    """
    demanda: Dict[datetime, List[pl.DemandaSkill]] = {}
    filas: List[dict] = []
    sin_asignacion: List[str] = []
    fer = set(feriados or ())
    observados = _dias_observados(serie)
    calendario = _calendario_y_persistencia(
        cfg, min((m.date() for m, _ in serie), default=hoy), dias_futuros[-1])

    for skill in cfg.skills:
        if not skill.activo or skill.pool_id is None:
            continue
        historico = {momento: llamadas
                     for (momento, sid), (llamadas, _) in serie.items()
                     if sid == skill.skill_id}
        if not historico:
            continue

        base = pl.baseline_estacional(
            historico, dias_futuros, intervalo_min=cfg.intervalo_min,
            feriados=feriados, excluir_dias=excluidos, hoy=hoy,
            semanas_base=cfg.semanas_base, dias_nivel=cfg.dias_nivel,
            factor_diario=(factor_clima or {}).get(skill.skill_id),
            nivel_por_tipo_de_dia=cfg.nivel_por_tipo_de_dia,
            nivel_diario=(nivel_diario or {}).get(skill.skill_id),
            peso_nivel=peso_nivel, dias_observados=observados, **calendario)

        if sobre_total:
            reparto = pdatos.factor_de_asignacion(
                asignacion, datetime.combine(dias_futuros[0], time(0, 0)),
                skill.skill_id)
            if reparto is None:
                sin_asignacion.append(skill.nombre)
                continue

        for momento, llamadas in base.items():
            if sobre_total:
                reparto = pdatos.factor_de_asignacion(asignacion, momento,
                                                      skill.skill_id)
                if reparto is None:
                    continue
                if deriva_hasta is None or momento.date() <= deriva_hasta:
                    reparto *= (deriva_reparto or {}).get(skill.skill_id, 1.0)
                # El sábado, el domingo y el feriado no reparten como un hábil.
                # Va DESPUÉS de la deriva y no en vez de ella: la deriva sigue el
                # nivel del share con los últimos siete días —que son casi todos
                # hábiles— y esto corrige la diferencia entre tipos de día, que
                # se mide con un año. Ver `pdatos.factor_reparto_no_habil`.
                if (factor_no_habil
                        and pl.tipo_de_dia(momento.date(), fer) == "no_habil"):
                    reparto *= factor_no_habil
                nuestras = llamadas * reparto
            else:
                reparto, nuestras = None, llamadas

            factor = pdatos.factor_de_ajuste(ajustes, momento, skill.skill_id)
            ajustadas = nuestras * factor
            tmo = pdatos.tmo_para(perfil_tmo, skill.skill_id, momento)
            if tmo <= 0:
                continue
            demanda.setdefault(momento, []).append(
                pl.DemandaSkill(skill.skill_id, ajustadas, tmo))
            filas.append({
                "skill": skill.skill_id, "momento": momento,
                "total": round(llamadas, 2) if sobre_total else None,
                "asignacion": reparto,
                "acme": round(ajustadas, 2),
                "base": round(nuestras, 2),
                "tmo": round(tmo, 2),
            })

    # Los intervalos sin ningún skill con volumen igual tienen que existir: es
    # donde manda la cobertura mínima del pool (la madrugada).
    paso = timedelta(minutes=cfg.intervalo_min)
    for dia in dias_futuros:
        momento = datetime.combine(dia, time(0, 0))
        fin = momento + timedelta(days=1)
        while momento < fin:
            demanda.setdefault(momento, [])
            momento += paso

    return demanda, filas, sorted(set(sin_asignacion))


def _sembrar_atipicos(conn, campana_id: int, serie, feriados, cfg) -> int:
    """Deja como candidatos los días atípicos que todavía no estén cargados.

    Nacen con Confirmado = 0: el sistema puede ver que un día fue raro, pero no
    puede saber si fue un corte masivo, una tormenta o el vencimiento de la
    factura. Eso lo pone una persona, y hasta entonces el evento sirve igual para
    sacar el día del entrenamiento.
    """
    skills = [s.skill_id for s in cfg.skills if s.pool_id is not None]
    diaria = pdatos.serie_diaria(serie, skills)
    candidatos = pl.detectar_atipicos(diaria, feriados=feriados)
    if not candidatos:
        return 0

    ya_estan = set()
    for e in pdatos.eventos(conn, campana_id, min(diaria), max(diaria) + timedelta(days=1)):
        cur = e["Desde"].date()
        fin = e["Hasta"]
        while datetime.combine(cur, time(0, 0)) < fin:
            ya_estan.add(cur)
            cur += timedelta(days=1)

    nuevos = 0
    for a in candidatos:
        if a.dia in ya_estan:
            continue
        conn.execute(text("""
            INSERT INTO planificacion.Evento
                (CampanaID, Desde, Hasta, Tipo, Descripcion, Factor, Origen,
                 ExcluirDeEntrenamiento, Confirmado)
            VALUES (:c, :d, :h, 'otro', :desc, :f, 'auto', 1, 0)
        """), {
            "c": campana_id,
            "d": datetime.combine(a.dia, time(0, 0)),
            "h": datetime.combine(a.dia + timedelta(days=1), time(0, 0)),
            "desc": (f"Detectado automáticamente: {a.llamadas:.0f} llamadas contra "
                     f"{a.esperado:.0f} esperadas para un "
                     f"{_nombre_dia(a.dia)} ({a.factor:.2f}x)."),
            "f": a.factor,
        })
        nuevos += 1
    return nuevos


def _tramos_desde_medicion(medicion: Dict[str, object]) -> List[dict]:
    """Convierte el reparto medido en tramos con la misma forma que los cargados.

    Se usa sólo como respaldo, y con la vigencia abierta desde el día en que
    arrancó el escalón actual: promediar los últimos 60 días daría un número que
    no es ni el reparto viejo ni el nuevo, justo cuando más importa acertarlo.
    """
    vigente = (medicion or {}).get("vigente")
    if not vigente:
        return []
    desde = vigente["desde"]
    # `por_skill_vigente` y no `por_skill`: el segundo promedia todo el período
    # consultado, que después de un cambio de contrato mezcla los dos regímenes y
    # da un reparto que no rigió nunca.
    reparto = medicion.get("por_skill_vigente") or medicion.get("por_skill") or {}
    return [{"AsignacionID": None, "SkillID": skill_id, "VigenteDesde": desde,
             "VigenteHasta": None, "Porcentaje": pct, "Nota": "medido"}
            for skill_id, pct in reparto.items()]


def _resumen_de_brecha(reqs: List[pl.RequerimientoPool]) -> Optional[dict]:
    """Cuánta gente falta o sobra, sumada sobre los intervalos que tienen malla.

    Se informan las dos puntas por separado y no la suma neta: un día con dos
    horas de faltante y dos de sobrante no está equilibrado, está mal armado, y el
    neto en cero lo escondería.
    """
    con_malla = [r for r in reqs if r.brecha is not None]
    if not con_malla:
        return None
    faltan = [r for r in con_malla if r.brecha < 0]
    sobran = [r for r in con_malla if r.brecha > 0]
    salida = {
        "intervalos_con_malla": len(con_malla),
        "intervalos_en_falta": len(faltan),
        "intervalos_con_exceso": len(sobran),
        "operadores_faltantes_pico": -min((r.brecha for r in faltan), default=0),
        "horas_operador_faltantes": round(sum(-r.brecha for r in faltan) / 2, 1),
        "horas_operador_sobrantes": round(sum(r.brecha for r in sobran) / 2, 1),
    }
    # Con refuerzo configurado, el faltante se parte en lo que se tapa pasando
    # gente de Digital y lo que no se tapa ni así. El segundo es el que obliga a
    # mover la malla o pedir horas extra; el primero se resuelve en el día.
    con_refuerzo = [r for r in faltan if r.refuerzo_cubre is not None]
    if con_refuerzo:
        sin_cubrir = [r for r in con_refuerzo if r.faltante_neto > 0]
        salida.update({
            "con_refuerzo": True,
            "horas_operador_cubre_refuerzo": round(
                sum(r.refuerzo_cubre for r in con_refuerzo) / 2, 1),
            "horas_operador_faltantes_netas": round(
                sum(r.faltante_neto for r in con_refuerzo) / 2, 1),
            "intervalos_sin_cubrir": len(sin_cubrir),
            "operadores_faltantes_netos_pico": max(
                (r.faltante_neto for r in sin_cubrir), default=0),
        })
    return salida


_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


TIPOS_DE_DIA = ("habil", "sabado", "domingo", "feriado")


def _nombre_tipo_de_dia(dia: date, feriados) -> str:
    """Hábil / sábado / domingo / feriado.

    Es más fino que `pl.tipo_de_dia` (que sólo parte en hábil y no hábil) porque
    acá se usa para MEDIR, no para pronosticar: el sábado y el domingo tienen
    errores muy distintos —29% y 37% sobre la demanda del cliente— y promediarlos
    esconde justo lo que hay que mirar.
    """
    if dia in set(feriados or ()):
        return "feriado"
    return {6: "sabado", 7: "domingo"}.get(dia.isoweekday(), "habil")


def _error_por_tipo_de_dia(cerrados: List[dict]) -> List[dict]:
    """El error de cada pronóstico partido por tipo de día.

    Es la respuesta a "¿cuál de los dos está más cerca?", que no tiene una sola
    respuesta: sobre 464 días medidos, el nuestro gana los hábiles por lejos
    (19,3% contra 37,5%) y los fines de semana la diferencia se achica mucho y en
    varios meses se da vuelta. Sin partirlo, la discusión se queda en impresiones.
    """
    salida = []
    for tipo in TIPOS_DE_DIA:
        filas = [f for f in cerrados if f["tipo_dia"] == tipo]
        if not filas:
            continue
        fila = {"tipo": tipo, "n": len(filas),
                "real": round(sum(f["real"] for f in filas), 1)}
        for clave, campo in (("modelo", "pronosticado"), ("produccion", "produccion"),
                             ("combinado", "combinado"), ("ingenuo", "ingenuo")):
            pares = [(f["real"], f[campo]) for f in filas if f.get(campo) is not None]
            fila[clave] = pl.medir_error(pares).como_dict() if pares else None
        salida.append(fila)
    return salida


def _nombre_dia(dia: date) -> str:
    return _DIAS[dia.isoweekday() - 1]


# ------------------------------------------------------------- calibraciones

# Ventana de la paciencia, aparte de la del shrinkage y la disponibilidad.
#
# La paciencia sale SÓLO de los que abandonaron, y hay colas donde eso es un
# puñado: en 90 días Electrodependientes tuvo 14 abandonos, TOC 8 y
# Emergencias-Empresarial 3. En 365 días son 395, 223 y 91 — o sea que ocho de
# las nueve colas pasan a tener muestra suficiente.
#
# Y la ventana larga no sólo agrega muestra: estabiliza y va para el lado
# conservador. TOC pasa de 6.970s a 1.068s y Electrodependientes de 1.254s a
# 584s, y MENOS paciencia pide MÁS gente en Erlang A. Para una cola donde lo que
# se busca es que nadie abandone, equivocarse para ese lado es lo correcto.
#
# Se separa del resto porque miden cosas distintas: el ausentismo y la
# disponibilidad son del régimen actual —cambian con la operación— y la paciencia
# es del que llama, que no cambia porque nosotros atendamos mejor.
DIAS_PACIENCIA = 365


def calibrar(engine: Engine, campana_id: int, dias: int = 90,
             hoy: Optional[date] = None,
             dias_paciencia: int = DIAS_PACIENCIA) -> Dict[str, object]:
    """Mide contra los datos los dos parámetros que no se pueden suponer.

    No escribe nada: propone. Que la paciencia o la disponibilidad cambien altera
    toda la dotación, así que el número nuevo lo mira una persona antes de
    quedar. La pantalla muestra el vigente y el medido, uno al lado del otro.
    """
    hoy = hoy or date.today()
    desde = hoy - timedelta(days=dias)

    with engine.connect() as conn:
        cfg = pdatos.cargar_config(conn, campana_id)
        horizonte = cfg.paciencia_horizonte_seg or 60
        desde_paciencia = hoy - timedelta(days=max(dias_paciencia, dias))
        paciencia = pdatos.estimar_paciencia_km(conn, campana_id, desde_paciencia,
                                                hoy, horizonte_seg=horizonte)
        _resolver_skills(paciencia, cfg)
        disponibilidad = {
            pool.nombre: pdatos.estimar_disponibilidad(conn, cfg, pool.pool_id,
                                                       desde, hoy)
            for pool in cfg.pools.values()
        }
        # Los feriados van para poder partir el shrinkage por tipo de día. No
        # cambian el número general: habilitan el desglose que contesta si hace
        # falta un número por tipo o alcanza con uno solo.
        fer = pdatos.feriados(conn, desde, hoy, campana_id)
        shrinkage = {
            pool.nombre: pdatos.estimar_shrinkage_payroll(conn, [pool.pool_id],
                                                          desde, hoy, feriados=fer)
            for pool in cfg.pools.values()
        }
        # EL QUE SE PROPONE APLICAR, cuando está medido. Los códigos de RRHH dicen
        # quién faltó de la gente del pool, pero la disponibilidad —el escalón
        # anterior de la cadena— se mide contra todos los conectados, y otras
        # sub-campañas tapan buena parte de ese faltante: medido del 17/08 al
        # 15/09 en hábiles, las telefónicas estuvieron logueadas el 84,5% de su
        # turno y con las otras sub-campañas (sin Digital) la línea tuvo el 93,6%.
        # Ver `planificador_presencia.conectados_que_cubren`.
        presencia = pdatos.nivel_de_presencia(conn, campana_id)

    return {
        "desde": desde, "hasta": hoy,
        "paciencia": {
            "vigente_seg": cfg.paciencia_seg,
            "origen": cfg.paciencia_origen,
            "horizonte_seg": horizonte,
            # La ventana de la paciencia es más larga que la del resto y hay que
            # decirlo: si no, "medido entre X e Y" al pie de la tarjeta miente
            # sobre la mitad de los números.
            "desde": desde_paciencia,
            "dias": (hoy - desde_paciencia).days,
            "por_skill": paciencia,
            # Sugerencia para el pool grande: la del skill con más volumen, que es
            # el que manda en la congestión de la cola compartida.
            "sugerida_seg": _paciencia_sugerida(paciencia),
        },
        "disponibilidad": {
            "vigente": [
                {"dia_semana": f.dia_semana, "hora_desde": f.hora_desde,
                 "hora_hasta": f.hora_hasta, "factor": f.factor}
                for f in cfg.disponibilidad
            ],
            "medida": disponibilidad,
        },
        "shrinkage": {
            "vigente": cfg.shrinkage,
            "origen": cfg.shrinkage_origen,
            "ausentismo_vigente": cfg.shrinkage_ausentismo,
            "capacitacion_vigente": cfg.shrinkage_capacitacion,
            "medido_en": cfg.shrinkage_medido_en,
            "medido": shrinkage,
            "presencia": presencia,
        },
    }


# Abandonos mínimos para que la paciencia de un skill se pueda aplicar.
#
# La paciencia sale de los que ABANDONARON: son los únicos que dicen cuánto
# aguantaron. Medido sobre 90 días, TOC tuvo 5.138 llamadas y **8 abandonos**, y
# de esos 8 sale una paciencia de 6.970 segundos —casi dos horas—. No es que la
# gente de TOC espere dos horas: es que casi nadie abandona y el estimador se
# queda sin denominador.
#
# Y el error va en la dirección peligrosa: una paciencia enorme hace que Erlang A
# diga "van a esperar, no hace falta más gente", así que el ruido REBAJA la
# dotación. Con 30 abandonos el error del estimador ronda el 18%; con 8 es
# cualquier cosa. Por debajo del piso el skill cae en la paciencia de la campaña,
# que es un número medido sobre toda la cola.
MIN_ABANDONOS_PARA_PACIENCIA = 30


def _resolver_skills(por_skill: Dict[str, dict], cfg) -> None:
    """Le pega a cada skill medido el `skill_id` que le corresponde, y decide si
    la medición alcanza para aplicarla.

    LOS NOMBRES NO COINCIDEN LITERALMENTE y por eso hay que normalizar: el
    informe de IVR dice `Emergencias`, `Comercial`, `Reclamo-Dano`, y
    `planificacion.Skill` dice `EMERGENCIAS`, `COMERCIAL`, `RECLAMO-DANO`.
    Comparando tal cual, el único que cruzaba era TOC —que está en mayúsculas de
    los dos lados— así que la paciencia medida se aplicaba a ESE skill y a
    ninguno más. Es el mismo UPPER() con el que la demanda total cruza el skill
    de texto contra el id del normalizador.

    El que no resuelve queda con `skill_id` en None y la pantalla lo muestra sin
    poder aplicarlo, que es mejor que aplicárselo al que no era.
    """
    por_nombre = {s.nombre.strip().upper(): s.skill_id for s in cfg.skills}
    for nombre, datos in por_skill.items():
        datos["skill_id"] = por_nombre.get(str(nombre).strip().upper())
        abandonos = int(datos.get("abandonos") or 0)
        datos["abandonos_minimos"] = MIN_ABANDONOS_PARA_PACIENCIA
        datos["aplicable"] = bool(
            datos["skill_id"] and datos.get("paciencia_seg")
            and abandonos >= MIN_ABANDONOS_PARA_PACIENCIA)


def _paciencia_sugerida(por_skill: Dict[str, dict]) -> Optional[int]:
    """La paciencia del skill de mayor volumen: es el que define la congestión."""
    con_valor = [(d.get("llamadas", 0), d.get("paciencia_seg"))
                 for d in por_skill.values() if d.get("paciencia_seg")]
    if not con_valor:
        return None
    return max(con_valor)[1]


def aplicar_calibracion(engine: Engine, campana_id: int, paciencia_seg=None,
                        shrinkage_pool: Optional[str] = None,
                        dias: int = 90,
                        shrinkage: Optional[dict] = None,
                        paciencia_por_skill: Optional[Dict[int, int]] = None,
                        disponibilidad: Optional[dict] = None
                        ) -> Dict[str, object]:
    """Deja como vigentes los valores medidos. Paso explícito, nunca automático.

    NO VUELVE A MEDIR SI YA LE DAN LO MEDIDO
    ----------------------------------------
    Antes corría `calibrar` DOS veces —una para leer el número y otra para
    devolver el estado nuevo—, y cada corrida hace Kaplan-Meier sobre los
    abandonos, invierte el NDS intervalo por intervalo y recorre 90 días de
    payroll por pool. Para un botón cuyo trabajo es escribir cuatro números que
    ya están en pantalla, eso son decenas de segundos de nada.

    Ahora los valores llegan en el pedido: son los que la pantalla ya tiene de
    `GET /calibracion`. Se sigue aceptando el modo viejo —`shrinkage_pool` sin
    valores— para no romper a quien llame el endpoint sin ellos, y ahí sí mide.
    """
    if shrinkage is None and shrinkage_pool:
        medido = calibrar(engine, campana_id, dias=dias)
        shrinkage = medido["shrinkage"]["medido"].get(shrinkage_pool)

    with engine.begin() as conn:
        pisados = pdatos.aplicar_calibracion(conn, campana_id,
                                             paciencia_seg=paciencia_seg,
                                             shrinkage=shrinkage)
        # La paciencia por skill va acá y no en la de la campaña: el que se quedó
        # sin luz espera mucho más que un electrodependiente. Ver
        # `pdatos.guardar_paciencia_por_skill`.
        skills = len(paciencia_por_skill or {})
        if paciencia_por_skill:
            pdatos.guardar_paciencia_por_skill(conn, campana_id, paciencia_por_skill)
        cfg = pdatos.cargar_config(conn, campana_id)

        # La disponibilidad medida no se aplicaba nunca: medir mostraba 0,82 y
        # aplicar dejaba las franjas como estaban. Ahora se escriben, en franjas
        # de una hora y ya NETAS de break.
        franjas = 0
        if disponibilidad:
            nuevas = _franjas_desde_medicion(cfg, disponibilidad)
            if nuevas:
                pdatos.guardar_disponibilidad(conn, campana_id, nuevas)
                franjas = len(nuevas)

    # No se recalibra para devolver: la pantalla recarga la configuración, que es
    # lo único que cambió.
    return {"ok": True, "pisados": pisados, "skills_con_paciencia": skills,
            "franjas_disponibilidad": franjas}


def _franjas_desde_medicion(cfg: pl.CampanaCfg,
                            medido: Dict[str, object]) -> List[dict]:
    """Convierte la disponibilidad medida por hora en franjas para guardar.

    DOS DECISIONES
    --------------
    1. Se guarda el factor YA NETO DE BREAK (`factor_aplicable`). El break se
       descuenta aparte, así que el día que la operación cambie los 5 minutos por
       hora se toca esa sola perilla y esto sigue valiendo.
    2. La hora sin muestra suficiente NO se toca: conserva el factor que ya
       tuviera configurado. De madrugada hay nueve intervalos útiles en 90 días y
       ese número no es una medición, es una anécdota — pisar un valor puesto por
       una persona con eso sería peor que no medir.

    Las horas consecutivas con el mismo factor se juntan en una sola franja: 24
    filas de una hora son ilegibles y dicen lo mismo que cuatro.
    """
    # La hora sin `factor_aplicable` se descarta acá y no más abajo: es la que
    # midió una disponibilidad que no da para sostener el break declarado, y
    # aplicarla igual sería quitarle el descuento a la dotación en silencio.
    por_hora = {int(h["hora"]): h for h in (medido.get("por_hora") or [])
                if h.get("factor_aplicable") is not None}
    if not por_hora:
        return []

    # El factor vigente de cada hora, para las que no se midieron.
    vigente: Dict[int, float] = {}
    for hora in range(24):
        franja = [f for f in cfg.disponibilidad
                  if f.dia_semana == 0 and f.hora_desde <= hora < f.hora_hasta]
        vigente[hora] = franja[0].factor if franja else 1.0

    factores = [round(float(por_hora[h]["factor_aplicable"]), 3) if h in por_hora
                else round(vigente[h], 3) for h in range(24)]

    franjas: List[dict] = []
    inicio = 0
    for hora in range(1, 25):
        if hora == 24 or factores[hora] != factores[inicio]:
            franjas.append({"dia_semana": 0, "hora_desde": inicio,
                            "hora_hasta": hora, "factor": factores[inicio]})
            inicio = hora
    return franjas


# ----------------------------------------------------------------- lecturas

# Cache en memoria por (campaña, fecha de hoy) para no releer 8 semanas de payroll en
# cada carga. Al cambiar el día se vacía: las entradas viejas no se vuelven a pedir.
_CACHE_EXTRAS: Dict[tuple[int, date], dict] = {}


def _extras_del_plan(engine: Engine, campana_id: int, hoy: date) -> dict:
    """Calcula las extras habituales de las últimas 8 semanas por pool.

    Best-effort a propósito: es una referencia visual para no pedir refuerzos que
    la operación cubre sola con horas extra. Si el payroll no está disponible o
    falla la lectura, la pantalla muestra el plan igual.

    Usa una conexión independiente para que, si falla una consulta de SQL, no
    deje la transacción del plan principal abortada.
    """
    clave = (campana_id, hoy)
    if clave in _CACHE_EXTRAS:
        return _CACHE_EXTRAS[clave]
    for vieja in [k for k in _CACHE_EXTRAS if k[1] != hoy]:
        del _CACHE_EXTRAS[vieja]

    try:
        with engine.connect() as conn:
            if not pdatos.schema_disponible(conn):
                return {"motivo": "esquema no disponible"}
            cfg = pdatos.cargar_config(conn, campana_id)
            pools = list(cfg.pools.values())
            if not pools:
                return {"motivo": "la campaña no tiene pools configurados"}

            desde = hoy - timedelta(days=pextras.SEMANAS_EXTRAS * 7)
            fer = set(pdatos.feriados(conn, desde, hoy, campana_id))
            dias_eval = [desde + timedelta(days=i) for i in range(pextras.SEMANAS_EXTRAS * 7)]

            por_pool: Dict[int, list] = {}
            for pool in pools:
                reg = pdatos.dotacion_real(conn, pool.pool_id, desde, hoy, cfg.intervalo_min)
                malla = pdatos.dotacion_planificada(conn, pool.pool_id, desde, hoy, cfg.intervalo_min)
                por_pool[pool.pool_id] = pextras.extras_habituales(
                    reg, malla,
                    tipo_de_dia=lambda d: pextras.tipo_de_dia_extras(d, fer),
                    dias=dias_eval,
                    intervalo_min=cfg.intervalo_min,
                )

            res = {
                "semanas": pextras.SEMANAS_EXTRAS,
                "feriados": [f.isoformat() if hasattr(f, "isoformat") else str(f) for f in sorted(fer)],
                "por_pool": por_pool,
            }
            _CACHE_EXTRAS[clave] = res
            return res
    except Exception as e:
        logger.warning(
            f"No se pudieron calcular las extras habituales de la campaña {campana_id}: {e}")
        return {"motivo": str(e)}


def plan_vigente(engine: Engine, campana_id: int,
                 horizonte: str = "operativo") -> Dict[str, object]:
    """Lo que muestra la pantalla: la última corrida buena y su detalle.

    Van también las dos series contra las que se lee el plan —el pronóstico del
    cliente y lo que efectivamente entró—, porque la pregunta que se hace todos
    los días delante de esta pantalla no es "cuánto pronosticamos" sino "cuánto
    pronosticamos comparado con qué". Ver `_cotejo_del_plan`.
    """
    with engine.connect() as conn:
        if not pdatos.schema_disponible(conn):
            raise pdatos.MigracionPendiente(
                "Falta correr scripts/migrations/2026-09-03_planificador.sql")

        corrida = pdatos.corrida_vigente(conn, campana_id, horizonte)
        if not corrida:
            return {"corrida": None, "requerimiento": [], "pronostico": [],
                    "real": [], "cliente": [], "conectados": [],
                    "real_hasta": None, "extras_habituales": None}

        cid = corrida["CorridaID"]
        requerimiento = pdatos.leer_requerimiento(conn, cid)
        salida = {
            "corrida": corrida,
            "requerimiento": requerimiento,
            "pronostico": pdatos.leer_pronostico(conn, cid),
            "sobre_demanda_total": pdatos.hay_demanda_total(conn, campana_id),
            "extras_habituales": _extras_del_plan(engine, campana_id, date.today()),
            **_cotejo_del_plan(conn, campana_id,
                               corrida["Desde"], corrida["Hasta"],
                               pool_ids=sorted({f["PoolID"] for f in requerimiento
                                                if f.get("PoolID") is not None})),
        }
        # Al final: es lo único opcional de la respuesta, y si algo falla adentro
        # no tiene que llevarse puesta ninguna de las lecturas de arriba.
        _anotar_cadena(conn, campana_id, salida["requerimiento"],
                       corrida["Desde"], corrida["Hasta"])
        return salida


def _anotar_cadena(conn, campana_id: int, filas: List[dict],
                   desde, hasta) -> None:
    """Le agrega a cada intervalo del plan los tres descuentos que convierten «en
    línea» en «a citar»: disponibilidad, break y ausentismo del día.

    Es para poder mostrar la cuenta. La diferencia entre las dos curvas del
    gráfico se leía entera como ausentismo, y medido sobre 30 días es al revés:
    en hábiles la cadena multiplica por 1,27 y 1,15 de eso es disponibilidad y
    break —gente logueada que no está sobre la cola—.

    Van con la configuración de AHORA, no la de cuando se calculó la corrida: la
    pantalla compara la cuenta contra lo guardado y, si no da, avisa que hay que
    recalcular en vez de mostrar una cuenta que no es la del plan. Best-effort: si
    falla, las filas van sin anotar y la tabla sale igual.
    """
    try:
        cfg = pdatos.cargar_config(conn, campana_id)
        d0 = desde.date() if isinstance(desde, datetime) else desde
        d1 = hasta.date() if isinstance(hasta, datetime) else hasta
        cfg.feriados = frozenset(pdatos.feriados(conn, d0, d1 + timedelta(days=1), campana_id))
        br = cfg.factor_break() if cfg.break_min_por_hora > 0 else 0.0
        for f in filas:
            m = f.get("Intervalo")
            if not isinstance(m, datetime):
                continue
            # Sin redondear: la pantalla rehace la cuenta y la compara contra lo
            # guardado, y con 5 min/h redondeado a 0,0833 un intervalo que cae
            # justo en un entero puede saltar al siguiente.
            f["Disponibilidad"] = cfg.factor_disponibilidad(m)
            f["Ausentismo"] = cfg.shrinkage_del_dia(m)
            f["Break"] = br
            f["Redondeo"] = "abajo" if cfg.redondea_para_abajo(m) else "arriba"
    except Exception as e:                                  # noqa: BLE001
        logger.warning("No se pudo anotar la cadena del plan de la campaña %s: %s",
                       campana_id, e)


def _cotejo_del_plan(conn, campana_id: int, desde: date,
                     hasta: date, pool_ids: Optional[Sequence[int]] = None
                     ) -> Dict[str, object]:
    """Las dos referencias que van AL LADO del pronóstico en la pantalla del
    plan: lo que pronosticó el cliente y lo que efectivamente entró.

    Las tres series son NUESTRAS llamadas y por eso se pueden dibujar juntas: el
    `dbo.Forecast` del cliente ya trae el reparto adentro y la serie real es la
    que atendimos. Nunca se mezcla acá la demanda total del IVR, que está en otra
    escala (somos el 50% de eso) y en el mismo gráfico haría creer que el plan se
    quedó a la mitad.

    El último intervalo del día de hoy se descarta, por la misma razón que en el
    seguimiento intradía: está EN CURSO, siempre viene a medias, y en un gráfico
    aparece como un desplome al final de la curva justo donde alguien está
    mirando para decidir si refuerza el turno.

    Best-effort a propósito. Son referencias para leer el plan, no el plan: si el
    informe del cliente no está cargado o la serie del día falla, la pantalla
    tiene que mostrar el plan igual.

    Con `pool_ids`, cada fila de conectados trae además el reparto por origen
    (telefónicos, refuerzo, otras), logueados y EN LÍNEA sin pausas, igual que el
    cotejo de dotación de la pestaña Comparación. Sin eso, o si esa lectura falla,
    queda sólo el total.
    """
    real: List[dict] = []
    cliente: List[dict] = []
    conectados: List[dict] = []
    ultimo: Optional[datetime] = None
    try:
        serie = pdatos.serie_por_skill(conn, campana_id, desde, hasta)
        con_datos = sorted({m for (m, _), (ll, _) in serie.items() if ll > 0})
        # Sólo el de HOY está en curso. En una corrida vieja el último intervalo
        # con datos ya cerró hace días y sacarlo sería tirar un dato bueno.
        en_curso = (con_datos[-1] if con_datos
                    and con_datos[-1].date() >= date.today() else None)
        cerrados = [m for m in con_datos if m != en_curso]
        # Hasta dónde llega el real. Va aparte de las filas porque las filas sólo
        # traen los intervalos con llamadas —de madrugada hay medias horas en
        # cero— y sin este corte la pantalla no puede distinguir "entraron cero"
        # de "todavía no pasó". Con él, la curva real se dibuja completa y
        # termina exactamente donde termina el dato.
        ultimo = cerrados[-1] if cerrados else None
        real = [{"SkillID": sid, "Intervalo": m, "Llamadas": round(ll, 1)}
                for (m, sid), (ll, _) in sorted(serie.items())
                if ll > 0 and (ultimo is None or m <= ultimo)]
    except Exception as e:
        logger.warning(f"No se pudo leer el real del plan de {campana_id}: {e}")
    try:
        # Cuántos operadores hubo realmente conectados en cada intervalo. Es lo
        # que cierra el círculo del plan: al lado de "a planificar" y "citados",
        # lo que efectivamente estuvo. Ver `pdatos.agentes_conectados`.
        crudo_con = pdatos.agentes_conectados(conn, campana_id, desde, hasta)
        conectados = [{"Intervalo": m, "Operadores": v}
                      for m, v in sorted(crudo_con.items())
                      if v > 0 and (ultimo is None or m <= ultimo)]
    except Exception as e:
        logger.warning(
            f"No se pudieron leer los operadores conectados de {campana_id}: {e}")
    try:
        if pdatos.hay_forecast_en_produccion(conn, campana_id):
            crudo = pdatos.forecast_en_produccion(conn, campana_id, desde, hasta)
            cliente = [{"SkillID": sid, "Intervalo": m, "Llamadas": round(v, 1)}
                       for (m, sid), v in sorted(crudo.items()) if v > 0]
    except Exception as e:
        logger.warning(
            f"No se pudo leer el forecast del cliente de {campana_id}: {e}")
    # Al final: si esta lectura pesada falla, las de arriba ya están.
    if conectados and pool_ids:
        try:
            # Partidos como en Comparación: los del pool y los del refuerzo, en
            # línea (sin break ni pausas) y logueados. Es la lectura pesada (cruza
            # el informe por agente con el payroll de cada persona), pero sólo hay
            # filas para los días que ya pasaron del plan, o sea casi siempre hoy.
            origen = pdatos.conectados_por_origen(conn, campana_id, list(pool_ids),
                                                  desde, hasta, con_en_linea=True)
            for fila in conectados:
                og = origen.get(fila["Intervalo"])
                if not og:
                    continue
                fila.update({
                    "Telefonicos": round(og["pool"], 1),
                    "Refuerzo": round(og["refuerzo"], 1),
                    "Otras": round(og["otras"], 1),
                    "Digital": round(og.get("digital") or 0.0, 1),
                    "Dedicada": round(og.get("dedicada") or 0.0, 1),
                    "TelefonicosEnLinea": round(og.get("pool_en_linea") or 0.0, 1),
                    "RefuerzoEnLinea": round(og.get("refuerzo_en_linea") or 0.0, 1),
                    "OtrasEnLinea": round(og.get("otras_en_linea") or 0.0, 1),
                    "DigitalEnLinea": round(og.get("digital_en_linea") or 0.0, 1),
                    "DedicadaEnLinea": round(og.get("dedicada_en_linea") or 0.0, 1),
                })
        except Exception as e:                              # noqa: BLE001
            logger.warning("No se pudieron partir los conectados del plan de %s: %s",
                           campana_id, e)
    return {"real": real, "cliente": cliente, "conectados": conectados,
            "real_hasta": ultimo}


# =========================================================================
# SEGUIMIENTO INTRADÍA — cómo viene el día contra lo pronosticado
# =========================================================================

# Cuánto se puede desviar el acumulado antes de que valga la pena avisar. Por
# debajo de esto es ruido normal de un día cualquiera.
DESVIO_PARA_AVISAR = 0.10

# Intervalos completos mínimos para que el desvío signifique algo. A las 7 de la
# mañana el acumulado son cuatro llamadas y cualquier cosa da 200%.
MIN_INTERVALOS_PARA_PROYECTAR = 6


def seguimiento_intradia(engine: Engine, campana_id: int, dia: Optional[date] = None,
                         horizonte: str = "operativo") -> Dict[str, object]:
    """Cómo viene el día contra el pronóstico, y cómo va a terminar.

    Es la respuesta a "el pronóstico difiere mucho de lo que está pasando hoy":
    en vez de que alguien lo note mirando el tablero a media mañana, se mide el
    desvío del acumulado y se reproyecta el resto del día.

    La reproyección aplica el desvío observado al pronóstico que queda, en vez de
    volver a pronosticar de cero: si el día viene 20% arriba a las 12, lo más
    probable es que siga 20% arriba, porque la forma intradía (el perfil de
    horas) es mucho más estable que el nivel.

    El último intervalo con datos se descarta: está en curso y siempre viene a
    medias, así que incluirlo hunde el desvío justo en la lectura más reciente.
    """
    dia = dia or date.today()
    with engine.connect() as conn:
        corrida = pdatos.corrida_vigente(conn, campana_id, horizonte)
        if not corrida:
            return {"dia": dia, "hay_corrida": False}

        pron = pdatos.pronostico_del_dia(conn, corrida["CorridaID"], dia)
        serie = pdatos.serie_por_skill(conn, campana_id, dia, dia + timedelta(days=1))
        asignacion = pdatos.asignacion_configurada(conn, campana_id, dia, dia)
        cfg = pdatos.cargar_config(conn, campana_id)
        pron_rows = pdatos.leer_pronostico(conn, corrida["CorridaID"])
        req_rows = pdatos.leer_requerimiento(conn, corrida["CorridaID"])

    real: Dict[datetime, float] = {}
    for (momento, _), (llamadas, _) in serie.items():
        real[momento] = real.get(momento, 0.0) + llamadas

    metricas, cerrados = metricas_intradia(pron, real)

    from app import planificador_insumos as pinsumos
    con_datos = sorted(m for m, v in real.items() if v > 0)
    en_curso = con_datos[-1] if con_datos else None
    proximas_horas = pinsumos.dimensionar_proximas_horas(
        cfg=cfg,
        pron_rows=pron_rows,
        req_rows=req_rows,
        desvio=metricas.get("desvio"),
        en_curso=en_curso,
        dia=dia,
        horas=4,
    )

    return {
        "dia": dia,
        "hay_corrida": True,
        "corrida_id": corrida["CorridaID"],
        **metricas,
        "share": _share_implicito(pron, real, cerrados, asignacion),
        "proximas_horas": proximas_horas,
        "detalle": [
            {"momento": m,
             "real": round(real.get(m, 0.0), 1),
             "pronosticado": round(pron.get(m, {}).get("acme", 0.0), 1),
             "cerrado": m in cerrados}
            for m in sorted(set(pron) | set(real))
        ],
    }


def metricas_intradia(pron: Dict[datetime, dict], real: Dict[datetime, float]):
    """Desvío del acumulado y proyección al cierre. Sin base de por medio.

    Devuelve también los intervalos cerrados, porque el resto del cálculo (el
    share implícito, el detalle) tiene que usar exactamente los mismos.
    """
    con_datos = sorted(m for m, v in real.items() if v > 0)
    # El último intervalo con datos está EN CURSO y siempre viene a medias:
    # incluirlo hunde el desvío justo en la lectura más reciente, que es la que
    # alguien está mirando.
    en_curso = con_datos[-1] if con_datos else None
    cerrados = [m for m in con_datos if m != en_curso]

    acum_real = sum(real[m] for m in cerrados)
    acum_pron = sum(pron.get(m, {}).get("acme", 0.0) for m in cerrados)
    desvio = (acum_real / acum_pron - 1) if acum_pron > 0 else None

    resto = [m for m in sorted(pron) if not en_curso or m > en_curso]
    pron_resto = sum(pron[m]["acme"] for m in resto)
    pron_dia = sum(v["acme"] for v in pron.values())

    suficientes = len(cerrados) >= MIN_INTERVALOS_PARA_PROYECTAR
    proyectado = None
    if desvio is not None and suficientes:
        # Se aplica el desvío al pronóstico que QUEDA, en vez de volver a
        # pronosticar de cero: la forma intradía (el perfil de horas) es mucho
        # más estable que el nivel.
        proyectado = acum_real + real.get(en_curso, 0.0) + pron_resto * (1 + desvio)

    return {
        "intervalos_cerrados": len(cerrados),
        "ultimo_cerrado": cerrados[-1] if cerrados else None,
        "acumulado_real": round(acum_real, 1),
        "acumulado_pronosticado": round(acum_pron, 1),
        "desvio": round(desvio, 4) if desvio is not None else None,
        "pronostico_dia": round(pron_dia, 1),
        "proyeccion_cierre": round(proyectado, 1) if proyectado else None,
        "avisar": bool(desvio is not None and suficientes
                       and abs(desvio) >= DESVIO_PARA_AVISAR),
    }, cerrados


def _share_implicito(pron, real, cerrados, asignacion) -> Optional[dict]:
    """Qué porcentaje del cliente nos estaría llegando hoy, si su demanda total
    fuese la pronosticada.

    Es una lectura PROVISORIA y hay que decirlo: la demanda real del cliente
    recién se conoce cuando entra la descarga completa, de noche. Pero sirve para
    lo que importa a media mañana — distinguir "el cliente tiene más llamadas" de
    "nos están mandando una porción más grande", que son dos problemas distintos
    y se resuelven de maneras distintas.
    """
    if not cerrados or not asignacion:
        return None
    total_pron = sum((pron.get(m, {}).get("total") or 0) for m in cerrados)
    if total_pron <= 0:
        return None
    aplicado = sum(pron.get(m, {}).get("acme", 0.0) for m in cerrados) / total_pron
    return {
        "aplicado": round(aplicado, 4),
        "implicito_hoy": round(sum(real.get(m, 0.0) for m in cerrados) / total_pron, 4),
        "provisorio": True,
    }


# =========================================================================
# BACKTEST — correr el pronóstico sobre días que YA PASARON
# =========================================================================
# "¿El pronóstico está cerca de lo real?" no se contesta esperando a que pasen
# los días: se contesta corriendo el modelo hacia atrás. Para cada día evaluado
# se rehace la línea de base con la historia que había `antelacion` días antes y
# se la compara contra lo que efectivamente entró.
#
# TRES REGLAS PARA QUE LA MEDICIÓN NO SE MIENTA
# ----------------------------------------------
# 1. El corte es duro. `baseline_estacional` sólo mira días ESTRICTAMENTE
#    anteriores a `hoy`, así que pasarle la fecha de corte alcanza para que no se
#    filtre el futuro. Nada de lo que se compara entró al entrenamiento.
# 2. No se aplican los ajustes manuales. Un ajuste cargado después del día que se
#    está evaluando sabría la respuesta; y aunque no la supiera, acá se mide el
#    MODELO, no el criterio de la persona que lo corrigió.
# 3. No se escribe nada. Ni corrida, ni eventos, ni atípicos. Un backtest que
#    deja rastro en la tabla de eventos contamina la próxima corrida real.
#
# LO QUE SÍ HAY QUE MIRAR CON DESCONFIANZA
# -----------------------------------------
# El reparto (qué porcentaje de las llamadas del cliente nos toca) sale de los
# tramos configurados, y esos tramos se cargaron MIRANDO la historia. O sea que
# de ese lado el backtest sabe algo que en su momento no se sabía. Por eso el
# error se informa partido en dos: sobre la demanda total del cliente —que es lo
# que el modelo realmente pronostica— y sobre nuestras llamadas, que es total por
# reparto. Si el primero es bueno y el segundo malo, el problema no es el modelo.

# Tope de días evaluados. Más que un trimestre no agrega y la respuesta se vuelve
# pesada: cada día son 48 intervalos.
MAX_DIAS_BACKTEST = 92

# Antelación por defecto, en días. Siete, porque es cuando se arma la malla: el
# error que duele no es el de mañana, es el del día que ya tiene turnos
# publicados y no se puede tocar.
ANTELACION_DEFECTO = 7

# Piso de llamadas de un intervalo para que entre en el MAPE. De madrugada hay
# medias horas de dos llamadas donde errar por una da 50%, y eso no dice nada
# sobre la calidad del pronóstico ni cambia una sola silla.
MIN_LLAMADAS_PARA_MAPE = 5.0


# QUÉ RESTRICCIÓN FIJÓ EL NÚMERO, agrupada en familias.
#
# `dimensionar_intervalo` evalúa varias restricciones y se queda con la MÁS
# EXIGENTE: ésa es la que "manda". El motor informa el motivo con más detalle del
# que conviene leer en una tabla —"segundo nivel de servicio", "nivel de atención",
# "abandono de ELECTRODEPENDIENTES"— así que acá se agrupan en las cuatro familias
# que corresponden a decisiones distintas:
#
#   nivel de servicio   el compromiso con el cliente (80% en 20s). Es lo que se
#                       negocia y lo que se audita.
#   techo de ocupación  condiciones de trabajo: cuánto se le puede exigir a un
#                       operador. NO es un compromiso con el cliente, y es la
#                       perilla a mirar si hay que bajar dotación.
#   abandono            el techo de abandono de un skill, con SU paciencia. Hoy
#                       sólo ELECTRODEPENDIENTES lo tiene cargado.
#   cobertura mínima    el piso del pool. Manda en la madrugada, donde el tráfico
#                       no pide a nadie y hay que atender el teléfono igual.
#
# El que no entre en ninguna queda con su nombre, a la vista: una familia nueva
# tiene que verse, no diluirse en "otros".
_FAMILIA_MOTIVO = {
    "nivel de servicio": "nivel de servicio",
    "segundo nivel de servicio": "nivel de servicio",
    "tiempo medio de espera": "espera media",
    "techo de ocupación": "techo de ocupación",
    "techo de abandono": "abandono",
    "nivel de atención": "abandono",
    "cobertura mínima": "cobertura mínima",
    "sin llamadas": "cobertura mínima",
}


def _familia_de_motivo(motivo: str) -> str:
    """La familia del motivo. `abandono de X` llega con el nombre del skill
    pegado, que no sirve para agrupar pero sí para leer el intervalo suelto."""
    if motivo.startswith("abandono de "):
        return "abandono"
    return _FAMILIA_MOTIVO.get(motivo, motivo or "sin motivo")


# Con menos llamadas que esto en el día, el cociente de dotación no se informa:
# un día con 30 llamadas necesita la cobertura mínima del pool y no dice nada
# sobre si el dimensionamiento acierta.
MIN_LLAMADAS_PARA_DOTACION = 200


def _dotacion_del_backtest(engine: Engine, cfg: pl.CampanaCfg, campana_id: int,
                           pron_skill: Dict[datetime, Dict[int, float]],
                           real_serie, usados, dias_eval: Sequence[date],
                           hoy: date, feriados, perfil_tmo) -> Dict[str, object]:
    """Cuántos operadores habríamos pedido para días que ya pasaron, y cuántos
    hubo.

    POR QUÉ NO ALCANZA CON COMPARAR PRONÓSTICO CONTRA REAL
    -------------------------------------------------------
    El error de llamadas no se traduce uno a uno en error de dotación: Erlang no
    es lineal, y la cadena de descuentos (disponibilidad, shrinkage, break) mete
    su propio error encima. Así que la brecha de dotación se dimensiona DOS veces
    y la diferencia entre las dos es la que separa las dos causas:

      - `a_planificar_pron`: con el pronóstico que se habría tenido ese día. Es
        el número que la operación habría visto en pantalla, o sea el que decide.
      - `a_planificar_real`: con la demanda que efectivamente entró. Es lo que de
        verdad hacía falta, y compararlo contra lo que hubo mide la CADENA sin el
        error del pronóstico adentro.

    Con eso, la brecha contra la malla se parte en dos sumandos:

        (pron - citados) = (pron - real)  +  (real - citados)
                            error de         error de
                            pronóstico       dimensionamiento

    Medido sobre Voltara el 2026-09-10, horario de operación: el plan pedía +15,4%
    sobre los citados, pero con la demanda real pedía -10,6%. O sea que la brecha
    era del pronóstico entera, y la cadena estaba pidiendo de menos.

    DOS REFERENCIAS DE "CUÁNTOS HUBO" Y NO UNA
    -------------------------------------------
    `citados` es la malla de RRHH (gente con turno ese día) y `conectados` son
    operadores-equivalentes logueados. No son lo mismo y las dos hacen falta: la
    malla es con lo que se negocia la dotación, y los conectados son los que de
    verdad estuvieron sobre la cola. Medido, dan casi igual —el ausentismo lo
    devuelve el solape de los bordes de turno— pero cuando se despegan, esa
    distancia es el dato.

    Y AL LADO, EL SERVICIO
    -----------------------
    Una brecha sin el nivel de servicio al lado no se puede leer. Citar menos de
    lo que el plan pedía y cumplir el objetivo igual significa que el plan pide de
    más; citar menos y no cumplirlo significa que la operación se quedó corta. Son
    conclusiones opuestas y el número de dotación solo no las distingue.

    SE MIDE CON LA CONFIGURACIÓN DE HOY, a propósito: la pregunta es qué pediría
    el planificador tal como está ahora para días que ya se conocen, no qué pidió
    una versión vieja. Por eso sirve para calibrar.
    """
    pools = list(cfg.pools.values())
    if not pools:
        return {"motivo": "la campaña no tiene pools configurados"}

    cerrados = [d for d in dias_eval if d < hoy]
    if not cerrados:
        return {"motivo": "no hay días cerrados en el período"}
    primero, ultimo = min(cerrados), max(cerrados)
    fin = ultimo + timedelta(days=1)

    # La demanda REAL por skill e intervalo, con SU TMO: el que de verdad tuvo
    # ese intervalo, no el del perfil. Es lo que hacía falta atender.
    real_dem: Dict[datetime, List[pl.DemandaSkill]] = {}
    llamadas_reales: Dict[date, float] = {}
    for (momento, sid), (llamadas, tmo) in real_serie.items():
        if sid not in usados or momento.date() not in set(cerrados):
            continue
        llamadas_reales[momento.date()] = llamadas_reales.get(momento.date(), 0.0) + llamadas
        if llamadas <= 0 or tmo <= 0:
            continue
        real_dem.setdefault(momento, []).append(pl.DemandaSkill(sid, llamadas, tmo))

    skills_ids = [s.skill_id for s in cfg.skills if s.activo and s.pool_id is not None]
    # BEST-EFFORT, igual que `_cotejo_del_plan` y por el mismo motivo: esto son las
    # referencias que van AL LADO de la medición del pronóstico, no la medición. Si
    # la malla o la vista del tablero no están —migración sin correr, informe del
    # día sin cargar—, el backtest tiene que seguir midiendo el pronóstico y decir
    # por qué le falta la dotación, en vez de caerse entero.
    try:
        with engine.connect() as conn:
            # DOS LECTURAS DE LA MALLA, y la que manda es la del REGISTRO.
            # `payroll_futuro` es la malla y no tiene ninguna hora extra cargada
            # (90 días del pool telefónico: `dbo.payroll` informa 1.426,4 h de
            # extras y la malla informa cero), así que para la pregunta "cuántos
            # hubo" la fuente correcta es el registro. La malla se conserva al lado
            # porque es contra lo que se negocia la dotación, y porque la distancia
            # entre las dos es en sí misma un dato: 1 a 3% en el horario de
            # operación. Ver `pdatos._PAYROLL_TURNOS_REAL`.
            citados: Dict[datetime, int] = {}
            malla: Dict[datetime, int] = {}
            for pool in pools:
                for momento, n in pdatos.dotacion_real(
                        conn, pool.pool_id, primero, fin, cfg.intervalo_min).items():
                    citados[momento] = citados.get(momento, 0) + n
                for momento, n in pdatos.dotacion_planificada(
                        conn, pool.pool_id, primero, fin, cfg.intervalo_min).items():
                    malla[momento] = malla.get(momento, 0) + n
            # Si el registro no tiene nada para el período —carga atrasada— se cae
            # a la malla en vez de informar una brecha infinita.
            if not citados:
                citados = malla
            conectados = pdatos.agentes_conectados(conn, campana_id, primero, fin)
            servicio = pdatos.servicio_real(conn, campana_id, skills_ids, primero, fin)
            # Las dos lecturas de Digital van AL FINAL y cada una con su try: son
            # detalle que se agrega al cotejo, no el cotejo. Si la migración de la
            # palanca no está o la consulta falla, la comparación sale igual sin esas
            # columnas.
            #
            # La palanca: gente de Digital con turno ese día, contada igual que
            # los citados (registro, y la malla si el registro está vacío).
            refuerzo: Dict[datetime, int] = {}
            hay_refuerzo = False
            try:
                for pool in pools:
                    if not pdatos.campanas_refuerzo_del_pool(conn, pool.pool_id):
                        continue
                    hay_refuerzo = True
                    del_pool = (pdatos.refuerzo_real(conn, pool.pool_id, primero, fin,
                                                     cfg.intervalo_min)
                                or pdatos.refuerzo_planificado(
                                    conn, pool.pool_id, primero, fin,
                                    cfg.intervalo_min))
                    for momento, n in del_pool.items():
                        refuerzo[momento] = refuerzo.get(momento, 0) + n
            except Exception as e:                          # noqa: BLE001
                logger.warning("No se pudo leer el refuerzo (Digital) de la "
                               "campaña %s: %s", campana_id, e)
                refuerzo, hay_refuerzo = {}, False
            # De dónde era cada conectado. Es la consulta más pesada (cruza el
            # informe por agente con la nómina y el payroll de cada día).
            try:
                origen = pdatos.conectados_por_origen(
                    conn, campana_id, [p.pool_id for p in pools], primero, fin,
                    con_en_linea=True)
            except Exception as e:                          # noqa: BLE001
                logger.warning("No se pudieron partir los conectados por origen "
                               "de la campaña %s: %s", campana_id, e)
                origen = {}
            # Contingencia, Gestión SVP y BU - Anfitrión atienden la línea algunos
            # días: suman a «tenían turno» sólo en las medias horas de su turno en
            # que estuvieron logueados (migración 2026-09-16). Al final y con su
            # try, como el resto del detalle.
            parciales: Dict[datetime, int] = {}
            try:
                for pool in pools:
                    for momento, n in pdatos.dotacion_parcial_real(
                            conn, campana_id, pool.pool_id, primero, fin).items():
                        parciales[momento] = parciales.get(momento, 0) + n
            except Exception as e:                          # noqa: BLE001
                logger.warning("No se pudieron leer los turnos parciales de la "
                               "campaña %s: %s", campana_id, e)
                parciales = {}
    except Exception as e:                                  # noqa: BLE001
        logger.warning("No se pudo leer la dotación real para el cotejo del "
                       "backtest de la campaña %s: %s", campana_id, e)
        return {"motivo": f"no se pudo leer la dotación real: {e}"}

    # --------------------------------------------------------- por intervalo
    paso = timedelta(minutes=cfg.intervalo_min)
    por_dia: List[dict] = []
    # El mismo cotejo, intervalo por intervalo. El día agregado dice CUÁNTO se
    # erró; esto dice DÓNDE, que es lo que se necesita para leer un caso: el +19%
    # de un domingo puede ser toda la tarde un poco o dos medias horas mucho, y son
    # problemas distintos.
    por_intervalo: List[dict] = []
    hay_origen = bool(origen)
    # Sin pausas sólo si la lectura los trajo (ver `pdatos.conectados_por_origen`).
    hay_linea = hay_origen and any("pool_en_linea" in g for g in origen.values())
    # Los turnos parciales ya van adentro de «citados»; se informan aparte para
    # poder decir cuántos eran.
    if parciales:
        citados = dict(citados)        # puede ser la misma `malla` (registro vacío)
    for momento, n in parciales.items():
        citados[momento] = citados.get(momento, 0) + n
    hay_parciales = bool(parciales)
    for dia in cerrados:
        acum = {k: 0.0 for k in ("pron", "real", "citados", "malla", "conectados",
                                 "llamadas_pron",
                                 "linea_pron", "linea_real", "trafico",
                                 "en_cola_real", "techo_ocupacion",
                                 "entrantes", "hasta_umbral", "atendidas",
                                 "refuerzo", "faltante", "cubre",
                                 "con_pool", "con_refuerzo", "con_otras",
                                 "linea_pool", "linea_refuerzo", "linea_otras",
                                 "con_digital", "linea_digital", "parciales",
                                 "con_dedicada", "linea_dedicada")}
        momento = datetime.combine(dia, time(0, 0))
        limite = momento + timedelta(days=1)
        intervalos = 0
        por_motivo: Dict[str, Dict[str, int]] = {}
        while momento < limite:
            dem_pron = [pl.DemandaSkill(sid, ll, pdatos.tmo_para(perfil_tmo, sid, momento))
                        for sid, ll in (pron_skill.get(momento) or {}).items()
                        if ll > 0 and pdatos.tmo_para(perfil_tmo, sid, momento) > 0]
            dem_real = real_dem.get(momento, [])
            disp = cfg.factor_disponibilidad(momento)
            fila_int = {"pron": 0, "real": 0, "linea_pron": 0, "linea_real": 0,
                        "trafico": 0.0}
            # Con varios pools, el motivo del intervalo es el del pool que más
            # gente pide: es el que mueve el total. El detalle crudo va igual, para
            # no perder "abandono de ELECTRODEPENDIENTES" detrás de "abandono".
            mayor = None
            for pool in pools:
                rp = pl.dimensionar_intervalo(cfg, pool, momento, dem_pron)
                rr = pl.dimensionar_intervalo(cfg, pool, momento, dem_real)
                acum["pron"] += rp.operadores_a_planificar
                acum["real"] += rr.operadores_a_planificar
                acum["linea_pron"] += rp.operadores_en_linea
                acum["linea_real"] += rr.operadores_en_linea
                fila_int["pron"] += rp.operadores_a_planificar
                fila_int["real"] += rr.operadores_a_planificar
                fila_int["linea_pron"] += rp.operadores_en_linea
                fila_int["linea_real"] += rr.operadores_en_linea
                fila_int["trafico"] += rr.trafico
                if mayor is None or rr.operadores_a_planificar > mayor.operadores_a_planificar:
                    mayor = rr
                # La ocupación se arma ponderada por TRÁFICO y no promediando
                # porcentajes: el 10% de ocupación de las 3 de la mañana no pesa
                # igual que el 85% del pico, y promediarlos da un número que no
                # describe ningún intervalo.
                acum["trafico"] += rr.trafico
                # QUÉ RESTRICCIÓN MANDÓ, por familia y en intervalos-operador.
                # Es la respuesta a "si cumplimos el NDS, por qué pide más gente":
                # porque en esos intervalos no mandó el NDS.
                fam = _familia_de_motivo(rr.motivo)
                m = por_motivo.setdefault(fam, {"operadores": 0, "intervalos": 0})
                m["operadores"] += rr.operadores_a_planificar
                m["intervalos"] += 1
                if fam == "techo de ocupación":
                    acum["techo_ocupacion"] += rr.operadores_a_planificar
            # Los que de verdad estuvieron SOBRE LA COLA: los logueados por el
            # mismo factor de disponibilidad que usa el modelo. Va con ese factor
            # a propósito —no es independiente— porque así las dos ocupaciones se
            # calculan igual y la diferencia entre ellas es sólo la dotación.
            acum["en_cola_real"] += conectados.get(momento, 0.0) * disp
            acum["citados"] += citados.get(momento, 0)
            acum["malla"] += malla.get(momento, 0)
            acum["conectados"] += conectados.get(momento, 0.0)
            sv = servicio.get(momento)
            if sv:
                for k in ("entrantes", "hasta_umbral", "atendidas"):
                    acum[k] += sv[k]
            con = conectados.get(momento, 0.0)
            ent_i = sv["entrantes"] if sv else 0.0
            at_i = sv["atendidas"] if sv else 0.0
            # La cadena que convierte "en línea" en "hacía falta", para poder
            # mostrarla. Sin esto una madrugada con una llamada, NDS 100% y "hacía
            # falta 3" no se entiende: son 2 en línea (el piso del pool) divididos
            # por disponibilidad, shrinkage y break = 2,64, que redondea a 3.
            sh = cfg.shrinkage_del_dia(momento)
            br = cfg.factor_break() if cfg.break_min_por_hora > 0 else 0.0
            llam_pron_i = sum(d.llamadas for d in dem_pron)
            acum["llamadas_pron"] += llam_pron_i
            # Lo que faltó contra los que tenían turno, y cuánto de eso podía
            # tapar Digital con la gente que tenía turno ese intervalo. Por
            # intervalo y sólo la parte positiva: el sobrante de las 15 no tapa el
            # faltante de las 11.
            falta_i = max(fila_int["real"] - citados.get(momento, 0), 0)
            ref_i = refuerzo.get(momento, 0) if hay_refuerzo else None
            cubre_i = min(falta_i, ref_i) if ref_i is not None else None
            acum["faltante"] += falta_i
            acum["refuerzo"] += ref_i or 0
            acum["cubre"] += cubre_i or 0
            og = (origen.get(momento) or {"pool": 0.0, "refuerzo": 0.0, "otras": 0.0}
                  if hay_origen else None)
            acum["parciales"] += parciales.get(momento, 0)
            if og:
                acum["con_pool"] += og["pool"]
                acum["con_refuerzo"] += og["refuerzo"]
                acum["con_otras"] += og["otras"]
                acum["con_digital"] += og.get("digital") or 0.0
                acum["linea_digital"] += og.get("digital_en_linea") or 0.0
                acum["con_dedicada"] += og.get("dedicada") or 0.0
                acum["linea_dedicada"] += og.get("dedicada_en_linea") or 0.0
                # Los mismos, sin las pausas. `.get` porque una conexión falsa o
                # una fuente sin la columna puede no traerlos.
                acum["linea_pool"] += og.get("pool_en_linea") or 0.0
                acum["linea_refuerzo"] += og.get("refuerzo_en_linea") or 0.0
                acum["linea_otras"] += og.get("otras_en_linea") or 0.0
            por_intervalo.append({
                "momento": momento,
                "llamadas_pron": round(llam_pron_i, 1),
                "llamadas_real": round(sum(d.llamadas for d in dem_real), 1),
                "en_linea_pron": fila_int["linea_pron"],
                "en_linea_real": fila_int["linea_real"],
                "a_planificar_pron": fila_int["pron"],
                "a_planificar_real": fila_int["real"],
                "citados": citados.get(momento, 0),
                # De esos, cuántos eran de las sub-campañas que atienden a veces.
                "citados_parciales": parciales.get(momento, 0) if hay_parciales else None,
                "malla": malla.get(momento, 0),
                "conectados": round(con, 1),
                # Los conectados partidos por de dónde era cada persona ese día.
                # None = no se pudo partir (ver `pdatos.conectados_por_origen`).
                "conectados_pool": round(og["pool"], 1) if og else None,
                "conectados_refuerzo": round(og["refuerzo"], 1) if og else None,
                "conectados_otras": round(og["otras"], 1) if og else None,
                "conectados_digital": round(og.get("digital") or 0.0, 1) if og else None,
                "conectados_dedicada": round(og.get("dedicada") or 0.0, 1) if og else None,
                # Los mismos EN LÍNEA: sin el tiempo en break y demás pausas. Son
                # los que se comparan contra «hacía falta en línea».
                "conectados_pool_en_linea": (round(og.get("pool_en_linea") or 0.0, 1)
                                             if og and hay_linea else None),
                "conectados_refuerzo_en_linea": (
                    round(og.get("refuerzo_en_linea") or 0.0, 1)
                    if og and hay_linea else None),
                "conectados_otras_en_linea": (round(og.get("otras_en_linea") or 0.0, 1)
                                              if og and hay_linea else None),
                "conectados_digital_en_linea": (
                    round(og.get("digital_en_linea") or 0.0, 1)
                    if og and hay_linea else None),
                "conectados_dedicada_en_linea": (
                    round(og.get("dedicada_en_linea") or 0.0, 1)
                    if og and hay_linea else None),
                "refuerzo_turno": ref_i,
                "cubre_refuerzo": cubre_i,
                "faltante_neto": falta_i - cubre_i if cubre_i is not None else None,
                "disponibilidad": round(disp, 3),
                "shrinkage": round(sh, 4),
                "factor_break": round(br, 4),
                "a_planificar_bruto": (
                    round(fila_int["linea_real"] / disp / (1 - sh) / (1 - br), 2)
                    if fila_int["linea_real"] and disp else 0.0),
                # De madrugada se redondea para abajo (CampanaCfg.redondeo_abajo_*).
                "redondeo_abajo": cfg.redondea_para_abajo(momento),
                "entrantes": ent_i,
                "atendidas": at_i,
                # CPH: llamadas por operador por hora. Ver `_cph`.
                "cph_pron": _cph(llam_pron_i, fila_int["pron"], cfg.intervalo_min),
                "cph_real": _cph(at_i, con, cfg.intervalo_min),
                # Con pocas llamadas el NDS de media hora es una anécdota (una
                # llamada de dos es 50%). Se informa igual —esconderlo sería
                # peor— y la pantalla lo atenúa mirando `entrantes`.
                "nds_real": (round(sv["hasta_umbral"] / ent_i, 4)
                             if sv and ent_i else None),
                "abandono_real": (round(1 - sv["atendidas"] / ent_i, 4)
                                  if sv and ent_i else None),
                "ocupacion_modelo": (round(fila_int["trafico"] / fila_int["linea_real"], 4)
                                     if fila_int["linea_real"] and fila_int["trafico"]
                                     else None),
                "ocupacion_real": (round(fila_int["trafico"] / (con * disp), 4)
                                   if con and disp and fila_int["trafico"] else None),
                "motivo": _familia_de_motivo(mayor.motivo) if mayor else None,
                "motivo_detalle": mayor.motivo if mayor else None,
            })
            intervalos += 1
            momento += paso

        llam = llamadas_reales.get(dia, 0.0)
        ent = acum["entrantes"]
        por_dia.append({
            "dia": dia,
            "tipo_dia": _nombre_tipo_de_dia(dia, feriados),
            "llamadas": round(llam, 1),
            "intervalos": intervalos,
            # Todo en INTERVALOS-OPERADOR (la suma de los 48 intervalos del día),
            # que es la unidad en la que se compran horas. El pico va aparte.
            "a_planificar_pron": int(round(acum["pron"])),
            "a_planificar_real": int(round(acum["real"])),
            "en_linea_real": int(round(acum["linea_real"])),
            # Crudas, para poder reconstruir las ocupaciones del resumen sin
            # promediar cocientes.
            "trafico": round(acum["trafico"], 2),
            "en_cola_real": round(acum["en_cola_real"], 2),
            # `citados` sale del registro (incluye las jornadas extendidas) y
            # `malla` de `payroll_futuro` (lo que se había prometido). La diferencia
            # entre las dos es horas extras y cambios de último momento.
            "citados": int(round(acum["citados"])),
            "citados_parciales": int(round(acum["parciales"])) if hay_parciales else None,
            "malla": int(round(acum["malla"])),
            "conectados": int(round(acum["conectados"])),
            "conectados_pool": round(acum["con_pool"], 1) if hay_origen else None,
            "conectados_refuerzo": (round(acum["con_refuerzo"], 1)
                                    if hay_origen else None),
            "conectados_otras": round(acum["con_otras"], 1) if hay_origen else None,
            "conectados_pool_en_linea": (round(acum["linea_pool"], 1)
                                         if hay_linea else None),
            "conectados_refuerzo_en_linea": (round(acum["linea_refuerzo"], 1)
                                             if hay_linea else None),
            "conectados_otras_en_linea": (round(acum["linea_otras"], 1)
                                          if hay_linea else None),
            "conectados_digital": round(acum["con_digital"], 1) if hay_origen else None,
            "conectados_digital_en_linea": (round(acum["linea_digital"], 1)
                                            if hay_linea else None),
            "conectados_dedicada": round(acum["con_dedicada"], 1) if hay_origen else None,
            "conectados_dedicada_en_linea": (round(acum["linea_dedicada"], 1)
                                             if hay_linea else None),
            # Digital: cuánta gente tenía turno, qué parte de ese tiempo estuvo en
            # la línea, y cuánto del faltante podía tapar. El faltante es la suma
            # de lo que faltó intervalo por intervalo, no la resta del día.
            "refuerzo_turno": int(round(acum["refuerzo"])) if hay_refuerzo else None,
            "refuerzo_en_linea": (round(acum["con_refuerzo"] / acum["refuerzo"], 4)
                                  if hay_origen and hay_refuerzo and acum["refuerzo"]
                                  else None),
            "faltante": int(round(acum["faltante"])),
            "cubre_refuerzo": int(round(acum["cubre"])) if hay_refuerzo else None,
            "faltante_neto": (int(round(acum["faltante"] - acum["cubre"]))
                              if hay_refuerzo else None),
            # Los tres cocientes que importan, ya calculados: que la pantalla no
            # tenga que decidir qué divide a qué.
            "pron_vs_citados": (round(acum["pron"] / acum["citados"] - 1, 4)
                                if acum["citados"] else None),
            "real_vs_citados": (round(acum["real"] / acum["citados"] - 1, 4)
                                if acum["citados"] else None),
            # Los dos cocientes EN LÍNEA, sin la cadena de descuentos: pronóstico
            # contra lo que hacía falta, y lo que hacía falta contra los que
            # estuvieron. No se mezcla una columna a citar con una en línea: el
            # ausentismo y las pausas de una no están en la otra.
            "en_linea_pron": int(round(acum["linea_pron"])),
            "linea_pron_vs_real": (round(acum["linea_pron"] / acum["linea_real"] - 1, 4)
                                   if acum["linea_real"] else None),
            "linea_real_vs_conectados": (
                round(acum["linea_real"] / acum["conectados"] - 1, 4)
                if acum["conectados"] else None),
            # Cuánto de la brecha contra la malla es del pronóstico. Es la resta
            # de los dos dimensionamientos, en intervalos-operador.
            "de_mas_por_pronostico": int(round(acum["pron"] - acum["real"])),
            # Las dos ocupaciones, con la MISMA definición de Erlang: tráfico
            # ofrecido sobre operadores en la cola. La del tablero NO sirve para
            # esto —da 84% incluso a las 3 de la mañana, donde el tráfico sobre
            # conectados es 10%—, así que no se mezcla acá.
            "ocupacion_modelo": (round(acum["trafico"] / acum["linea_real"], 4)
                                 if acum["linea_real"] else None),
            "ocupacion_real": (round(acum["trafico"] / acum["en_cola_real"], 4)
                               if acum["en_cola_real"] else None),
            "max_ocupacion": cfg.max_ocupacion,
            # Qué proporción de la gente pedida la fija el techo de ocupación.
            "peso_techo_ocupacion": (round(acum["techo_ocupacion"] / acum["real"], 4)
                                     if acum["real"] else None),
            "motivos": _repartir_motivos(por_motivo),
            "llamadas_pron": round(acum["llamadas_pron"], 1),
            "atendidas": round(acum["atendidas"], 1),
            "cph_pron": _cph(acum["llamadas_pron"], acum["pron"], cfg.intervalo_min),
            "cph_real": _cph(acum["atendidas"], acum["conectados"], cfg.intervalo_min),
            "nds_real": round(acum["hasta_umbral"] / ent, 4) if ent else None,
            "abandono_real": (round(1 - acum["atendidas"] / ent, 4)
                              if ent else None),
            "poco_volumen": llam < MIN_LLAMADAS_PARA_DOTACION,
        })

    # ------------------------------------------------------------- agregados
    def _resumir(filas: Sequence[dict]) -> Optional[dict]:
        utiles = [f for f in filas if not f["poco_volumen"] and f["citados"]]
        if not utiles:
            return None
        t = {k: sum(f[k] for f in utiles) for k in
             ("a_planificar_pron", "a_planificar_real", "citados", "malla",
              "conectados", "llamadas", "llamadas_pron", "atendidas")}
        # Las ocupaciones se reconstruyen del tráfico y los operadores, nunca
        # promediando los cocientes diarios.
        traf = sum(f["trafico"] for f in utiles)
        linea = sum(f["en_linea_real"] for f in utiles)
        cola = sum(f["en_cola_real"] for f in utiles)
        techo = sum(f["a_planificar_real"] * (f["peso_techo_ocupacion"] or 0)
                    for f in utiles)
        motivos: Dict[str, Dict[str, int]] = {}
        for f in utiles:
            for m in f["motivos"]:
                a = motivos.setdefault(m["motivo"], {"operadores": 0, "intervalos": 0})
                a["operadores"] += m["operadores"]
                a["intervalos"] += m["intervalos"]

        def opcional(campo):
            # None si falta en algún día: sumar los que sí tienen daría un total
            # que parece del período y es de una parte.
            vals = [f.get(campo) for f in utiles]
            return None if any(v is None for v in vals) else sum(vals)

        ref_turno, con_ref = opcional("refuerzo_turno"), opcional("conectados_refuerzo")
        faltante, cubre = sum(f.get("faltante") or 0 for f in utiles), opcional("cubre_refuerzo")
        def opcional_1(campo):
            v = opcional(campo)
            return round(v, 1) if v is not None else None

        return {
            "conectados_pool_en_linea": opcional_1("conectados_pool_en_linea"),
            "conectados_refuerzo_en_linea": opcional_1("conectados_refuerzo_en_linea"),
            "conectados_otras_en_linea": opcional_1("conectados_otras_en_linea"),
            "conectados_digital": opcional_1("conectados_digital"),
            "conectados_digital_en_linea": opcional_1("conectados_digital_en_linea"),
            "conectados_dedicada": opcional_1("conectados_dedicada"),
            "conectados_dedicada_en_linea": opcional_1("conectados_dedicada_en_linea"),
            "citados_parciales": opcional("citados_parciales"),
            "conectados_pool": (round(opcional("conectados_pool"), 1)
                                if opcional("conectados_pool") is not None else None),
            "conectados_refuerzo": round(con_ref, 1) if con_ref is not None else None,
            "conectados_otras": (round(opcional("conectados_otras"), 1)
                                 if opcional("conectados_otras") is not None else None),
            "refuerzo_turno": ref_turno,
            "refuerzo_en_linea": (round(con_ref / ref_turno, 4)
                                  if con_ref is not None and ref_turno else None),
            "faltante": faltante,
            "cubre_refuerzo": cubre,
            "faltante_neto": faltante - cubre if cubre is not None else None,
            "parte_cubre_refuerzo": (round(cubre / faltante, 4)
                                     if cubre is not None and faltante else None),
            "dias": len(utiles),
            "llamadas": round(t["llamadas"], 1),
            "a_planificar_pron": t["a_planificar_pron"],
            "a_planificar_real": t["a_planificar_real"],
            "citados": t["citados"],
            "malla": t["malla"],
            "conectados": t["conectados"],
            # Cuánto de la presencia real no figuraba en la malla: horas extras y
            # cambios de último momento. Es lo que hace que muchas veces haya más
            # conectados que citados.
            "registro_vs_malla": (round(t["citados"] / t["malla"] - 1, 4)
                                  if t["malla"] else None),
            "conectados_vs_citados": (round(t["conectados"] / t["citados"] - 1, 4)
                                      if t["citados"] else None),
            "pron_vs_citados": round(t["a_planificar_pron"] / t["citados"] - 1, 4),
            "real_vs_citados": round(t["a_planificar_real"] / t["citados"] - 1, 4),
            "en_linea_pron": sum(f["en_linea_pron"] for f in utiles),
            "en_linea_real": linea,
            "linea_pron_vs_real": (round(sum(f["en_linea_pron"] for f in utiles) / linea - 1, 4)
                                   if linea else None),
            "linea_real_vs_conectados": (round(linea / t["conectados"] - 1, 4)
                                         if t["conectados"] else None),
            "de_mas_por_pronostico": t["a_planificar_pron"] - t["a_planificar_real"],
            # Ponderado por llamadas, no promedio de promedios: un domingo de 700
            # llamadas no pesa igual que un lunes de 6.000.
            "ocupacion_modelo": round(traf / linea, 4) if linea else None,
            "ocupacion_real": round(traf / cola, 4) if cola else None,
            # Reconstruido de las sumas, nunca promediando los CPH diarios.
            "cph_pron": _cph(t["llamadas_pron"], t["a_planificar_pron"], cfg.intervalo_min),
            "cph_real": _cph(t["atendidas"], t["conectados"], cfg.intervalo_min),
            "max_ocupacion": cfg.max_ocupacion,
            "peso_techo_ocupacion": (round(techo / t["a_planificar_real"], 4)
                                     if t["a_planificar_real"] else None),
            "motivos": _repartir_motivos(motivos),
            "nds_real": _pond(utiles, "nds_real"),
            "abandono_real": _pond(utiles, "abandono_real"),
        }

    tipos: Dict[str, List[dict]] = {}
    for f in por_dia:
        tipos.setdefault(f["tipo_dia"], []).append(f)

    return {
        "por_dia": por_dia,
        "por_intervalo": por_intervalo,
        "resumen": _resumir(por_dia),
        "por_tipo_de_dia": [{"tipo": t, **(_resumir(v) or {})}
                            for t, v in sorted(tipos.items()) if _resumir(v)],
        "minimo_llamadas": MIN_LLAMADAS_PARA_DOTACION,
        "objetivo_nds": max((s.objetivo_nds for s in cfg.skills
                             if s.activo and s.objetivo_nds), default=None),
        "umbral_seg": min((s.umbral_seg for s in cfg.skills
                           if s.activo and s.umbral_seg), default=None),
    }


def _cph(llamadas: float, intervalos_operador: float,
         intervalo_min: int) -> Optional[float]:
    """Llamadas por operador por hora.

    `intervalos_operador` es la suma de operadores de cada media hora, así que
    multiplicado por la duración del intervalo da HORAS-operador. Los dos lados se
    miden con su propia gente y su propio volumen, a propósito:

      - PRONOSTICADO: llamadas pronosticadas sobre las horas que PEDÍAMOS. Es la
        productividad que el plan daba por supuesta.
      - REAL: llamadas ATENDIDAS sobre las horas CONECTADAS (los conectados ya son
        equivalentes: tiempo logueado sobre la duración del intervalo). Es la
        productividad que efectivamente hubo. Atendidas y no entrantes, porque la
        que se cortó esperando no la atendió nadie.

    Se reconstruye siempre de las sumas: el CPH de un día es el total de llamadas
    sobre el total de horas, no el promedio de 48 CPH de media hora, que le daría el
    mismo peso a las 3 de la mañana que al pico.
    """
    horas = intervalos_operador * intervalo_min / 60.0
    return round(llamadas / horas, 2) if horas > 0 else None


def _repartir_motivos(por_motivo: Dict[str, Dict[str, int]]) -> List[dict]:
    """El reparto de quién mandó, ordenado por peso y con su proporción.

    El peso va en INTERVALOS-OPERADOR y no en cantidad de intervalos: que el techo
    de ocupación mande en 16 intervalos y el nivel de servicio en 26 no dice cuál
    pesa más, porque los 16 son el pico. Medido el 07/09: 985 intervalos-operador
    contra 285, o sea 75% contra 22%.
    """
    total = sum(m["operadores"] for m in por_motivo.values())
    if total <= 0:
        return []
    return sorted(
        ({"motivo": k, "operadores": v["operadores"], "intervalos": v["intervalos"],
          "parte": round(v["operadores"] / total, 4)}
         for k, v in por_motivo.items() if v["operadores"] > 0),
        key=lambda m: -m["operadores"])


def _pond(filas: Sequence[dict], campo: str) -> Optional[float]:
    """Promedio del campo ponderado por llamadas del día."""
    pares = [(f["llamadas"], f[campo]) for f in filas if f.get(campo) is not None]
    peso = sum(p for p, _ in pares)
    return round(sum(p * v for p, v in pares) / peso, 4) if peso > 0 else None


# Lo que el laboratorio puede probar sin guardar: los parámetros del modelo de
# pronóstico, con los mismos topes que el CHECK de la migración de cada uno. Un
# parámetro de dimensionamiento no va acá: eso es la pestaña Escenarios.
AJUSTES_DE_MODELO: Dict[str, Tuple[type, float, float]] = {
    "semanas_base": (int, 4, 156),
    "dias_nivel": (int, 7, 120),
    "nivel_por_tipo_de_dia": (bool, 0, 1),
    "reparto_deriva_dias": (int, 0, 60),
    "reparto_deriva_tope": (float, 0, 0.5),
    "reparto_tipo_dia_dias": (int, 0, 1100),
    "reparto_tipo_dia_tope": (float, 0, 0.5),
    # Calendario propio y persistencia del desvío (2026-09-22, Hidra).
    "feriado_como_sabado": (bool, 0, 1),
    "puente_factor": (float, 0.1, 1.5),
    "persistencia_peso_hoy": (float, 0, 1),
    "persistencia_peso_resto": (float, 0, 1),
    "persistencia_dias": (int, 0, 30),
    # Gasur (2026-09-24): forma reciente del día y persistencia sin eventos.
    "forma_dias": (int, 7, 91),
    "persistencia_saltea_eventos": (bool, 0, 1),
}


def validar_ajustes_de_modelo(ajustes: Optional[Dict[str, object]]) -> Dict[str, object]:
    """Los ajustes que vinieron con valor, con su tipo y dentro del rango."""
    salida: Dict[str, object] = {}
    for clave, valor in (ajustes or {}).items():
        if valor is None:
            continue
        if clave not in AJUSTES_DE_MODELO:
            raise ValueError(f"«{clave}» no es un parámetro del modelo que se pueda probar.")
        tipo, minimo, maximo = AJUSTES_DE_MODELO[clave]
        if tipo is bool:
            salida[clave] = bool(valor)
            continue
        try:
            convertido = tipo(valor)
        except (TypeError, ValueError):
            raise ValueError(f"«{clave}» tiene que ser un número.")
        if not (minimo <= convertido <= maximo):
            raise ValueError(f"«{clave}» tiene que estar entre {minimo} y {maximo}.")
        salida[clave] = convertido
    return salida


def backtest(engine: Engine, campana_id: int, desde: date, hasta: date,
             antelacion: int = ANTELACION_DEFECTO,
             hoy: Optional[date] = None,
             clima: Optional[bool] = None,
             nivel: Optional[bool] = None,
             elasticidad: Optional[bool] = None,
             ajustes: Optional[Dict[str, object]] = None) -> Dict[str, object]:
    """Qué habría pronosticado el planificador para días que ya pasaron.

    `desde` y `hasta` son inclusivos. `antelacion` es con cuántos días de
    anticipación se supone hecho el pronóstico: con 7, el del lunes se arma con
    la historia hasta el lunes anterior.

    `clima` fuerza el uso del modelo de clima (True) o lo apaga (False); en None
    manda lo que esté configurado. Sirve para medir la misma ventana con y sin
    clima antes de decidir si se activa, que es lo que hay que hacer antes de
    tocar un pronóstico que después mueve gente.

    El real sale de `[Voltara Enerval informe skills]`, que es el reporte de NUESTRAS
    llamadas y carga cada hora: es el que permite comparar el mismo día. La
    demanda total del cliente sale del informe IVR completo, que carga de noche,
    así que para el día de hoy suele no estar todavía.
    """
    hoy = hoy or date.today()
    if hasta < desde:
        raise ValueError("El período termina antes de empezar.")
    # Antelación 0 es el plan de la mañana: el del día se arma con la historia
    # hasta ayer, que es lo que hace el recálculo de las 06:00.
    if antelacion < 0:
        raise ValueError("La antelación no puede ser negativa.")
    # No se puede evaluar el futuro. El día de hoy sí entra, marcado como
    # parcial: sirve para mirarlo, no para promediar.
    hasta = min(hasta, hoy)
    if hasta < desde:
        raise ValueError(f"No hay días cerrados para evaluar antes del {desde}.")

    dias_eval = [desde + timedelta(days=i) for i in range((hasta - desde).days + 1)]
    if len(dias_eval) > MAX_DIAS_BACKTEST:
        raise ValueError(f"El backtest admite hasta {MAX_DIAS_BACKTEST} días por vez; "
                         f"se pidieron {len(dias_eval)}.")

    corte_min = desde - timedelta(days=antelacion)
    fin = hasta + timedelta(days=1)

    with engine.connect() as conn:
        if not pdatos.schema_disponible(conn):
            raise pdatos.MigracionPendiente(
                "Falta correr scripts/migrations/2026-09-03_planificador.sql")
        cfg = pdatos.cargar_config(conn, campana_id)
        # El laboratorio: la misma comparación con otro parámetro del modelo, sin
        # guardarlo. Va antes de la ventana de lectura porque las semanas de
        # historia la cambian.
        ajustes = validar_ajustes_de_modelo(ajustes)
        if ajustes:
            cfg = dataclasses.replace(cfg, **ajustes)
        estado_clima = pdatos.clima_de_la_campana(conn, campana_id)
        desde_hist = corte_min - timedelta(days=_ventana_de_lectura(
            cfg, estado_clima, forzar_clima=clima))
        sobre_total = pdatos.hay_demanda_total(conn, campana_id)

        # El real: nuestras llamadas, del reporte que carga cada hora.
        nuestra = pdatos.serie_por_skill(conn, campana_id, desde_hist, fin)
        cruda = (pdatos.serie_demanda_total(conn, campana_id, desde_hist, fin,
                                            cfg.intervalo_min)
                 if sobre_total else {})

        fer = pdatos.feriados(conn, desde_hist, fin, campana_id)
        excluidos = pdatos.dias_a_excluir(conn, campana_id, desde_hist, corte_min)
        marcados = _dias_con_evento(pdatos.eventos(conn, campana_id, desde, fin))

        # El pronóstico que la operación usa HOY, para tenerlo al lado.
        produccion_cruda = (pdatos.forecast_en_produccion(conn, campana_id, desde, fin)
                            if pdatos.hay_forecast_en_produccion(conn, campana_id) else {})

        clima_obs, clima_todo, usar_clima = _clima_del_pronostico(
            conn, campana_id, cfg, desde_hist, hasta, forzar=clima)

        asignacion = pdatos.asignacion_configurada(conn, campana_id,
                                                   corte_min, hasta) if sobre_total else []
        asignacion_medida = None
        if sobre_total and not asignacion:
            # Respaldo sin filtrar el futuro: se mide con lo que había ANTES del
            # primer día evaluado, no con el promedio del período que se evalúa.
            asignacion_medida = pdatos.medir_asignacion(
                conn, campana_id, corte_min - timedelta(days=60), corte_min)
            asignacion = _tramos_desde_medicion(asignacion_medida)
            for t in asignacion:
                t["VigenteDesde"] = min(t["VigenteDesde"], corte_min)
        # Los tramos que rigieron hacia atrás, para medir la deriva y el factor
        # del tipo de día. Ver el comentario equivalente en `recalcular`: un
        # tramo que terminó antes del primer corte no aparece en la lectura del
        # período evaluado, y sin él los días previos a un cambio de régimen
        # quedan sin porcentaje contra el cual comparar.
        asignacion_hist = asignacion if (asignacion_medida or not sobre_total) else (
            pdatos.asignacion_configurada(
                conn, campana_id,
                corte_min - timedelta(days=cfg.reparto_tipo_dia_dias), hasta))

    avisos: List[str] = []
    skills = [s for s in cfg.skills if s.activo and s.pool_id is not None]

    # Historia sobre la que entrena el modelo. Con la descarga del 100% es la
    # demanda del cliente; sin ella, lo que recibimos nosotros.
    historia: Dict[int, Dict[datetime, float]] = {}
    for s in skills:
        if sobre_total:
            historia[s.skill_id] = {m: v["total"] for (m, sid), v in cruda.items()
                                    if sid == s.skill_id}
        else:
            historia[s.skill_id] = {m: ll for (m, sid), (ll, _) in nuestra.items()
                                    if sid == s.skill_id}

    # --------------------------------------------------- pronóstico hacia atrás
    pron_acme: Dict[datetime, float] = {}
    pron_total: Dict[datetime, float] = {}
    # momento -> {skill_id: llamadas}. Lo necesita el cotejo de dotación, que
    # vuelve a dimensionar cada intervalo con este pronóstico.
    pron_skill: Dict[datetime, Dict[int, float]] = {}
    sin_asignacion: set = set()
    # Qué skills llegaron efectivamente a producir pronóstico. El real se arma
    # DESPUÉS y sólo con estos: comparar un real que incluye colas que el modelo
    # ni intentó pronosticar no mide al modelo, mide un recorte de alcance.
    usados: set = set()

    diarias = _series_diarias_por_skill(cruda if sobre_total else nuestra, sobre_total)
    observados = _dias_observados(cruda if sobre_total else nuestra)
    modelos: Dict[date, dict] = {}
    factores: Dict[date, float] = {}
    reparto_dia = pdatos.reparto_diario(cruda) if sobre_total else {}
    derivas: Dict[date, Dict[int, float]] = {}

    def _deriva_al_corte(corte: date) -> Dict[int, float]:
        """La deriva del reparto que se habría podido medir en esa fecha.

        Igual que el modelo de clima: cada día evaluado usa sólo lo que ya se
        sabía a su propia fecha de corte."""
        if corte not in derivas:
            derivas[corte] = {}
            if sobre_total and cfg.reparto_deriva_dias:
                for s_ in skills:
                    d = pdatos.deriva_de_reparto(
                        asignacion_hist, reparto_dia, corte, s_.skill_id,
                        cfg.reparto_deriva_dias, cfg.reparto_deriva_tope)
                    if d is not None:
                        derivas[corte][s_.skill_id] = d
        return derivas[corte]

    factores_no_habil: Dict[date, Optional[float]] = {}

    def _no_habil_al_corte(corte: date) -> Optional[float]:
        """El factor de reparto de los días no hábiles medido a esa fecha.

        Igual que la deriva y que el clima: cada día evaluado usa sólo la ventana
        que terminaba en su propio corte."""
        if corte not in factores_no_habil:
            factores_no_habil[corte] = (
                pdatos.factor_reparto_no_habil(
                    asignacion_hist, reparto_dia, corte, cfg.reparto_tipo_dia_dias,
                    fer, cfg.reparto_tipo_dia_tope)
                if sobre_total and cfg.reparto_tipo_dia_dias else None)
        return factores_no_habil[corte]

    modelos_nivel: Dict[date, object] = {}
    elasticidades: Dict[date, Dict[str, float]] = {}
    usar_elasticidad = (cfg.clima_elasticidad_tipo_dia if elasticidad is None
                        else elasticidad)
    usar_nivel = (cfg.nivel_gbdt and cfg.nivel_gbdt_peso > 0) if nivel is None else nivel
    peso_nivel = cfg.nivel_gbdt_peso if usar_nivel else 0.0

    def _niveles_al_corte(corte: date) -> Dict[int, Dict[date, float]]:
        """La segunda opinión del nivel que se habría podido calcular ese día.

        Mismo corte duro y mismo caché por fecha que el modelo de clima: uno por
        fecha de corte, y ninguno puede ver un día posterior al suyo. Cuesta
        alrededor de un segundo y medio por corte, así que un backtest de un mes
        agrega unos cuarenta segundos.
        """
        if not usar_nivel or not usar_clima:
            return {}
        if corte not in modelos_nivel:
            modelos_nivel[corte] = pnivel.ajustar(
                diarias, clima_obs, corte, semanas=cfg.semanas_base,
                feriados=fer, excluidos=excluidos)
        if modelos_nivel[corte] is None:
            return {}
        dias = ([corte - timedelta(days=i) for i in range(cfg.dias_nivel + 1, -1, -1)]
                + [d for d in dias_eval if d >= corte])
        return pnivel.niveles_por_skill(
            modelos_nivel[corte], diarias, dias, clima_todo, corte,
            semanas=cfg.semanas_base, feriados=fer, excluidos=excluidos)

    def _factores_al_corte(corte: date) -> Dict[int, Dict[date, float]]:
        """Los factores de clima que se habrían podido calcular en esa fecha.

        Se ajusta una vez por fecha de corte y se cachea: el modelo no puede ver
        nada posterior a su propio corte, así que cada día evaluado tiene el suyo.
        Y va uno POR SKILL, porque las colas no responden igual al tiempo.
        """
        if corte not in modelos:
            modelos[corte] = pclima.ajustar_por_skill(
                diarias, clima_obs, corte, semanas=cfg.semanas_base,
                feriados=fer, excluidos=excluidos,
                rasgos=RASGOS_ACTIVOS) if usar_clima else {}
        if not modelos[corte]:
            return {}
        # Todos los días que tocan el cálculo, no sólo el que se pronostica: la
        # corrección de nivel mira hacia atrás y necesita saber qué parte de esos
        # días ya la explicaba el tiempo.
        #
        # Con la elasticidad prendida hay que mirar MUCHO más atrás: se mide
        # regresando sobre los días ya cerrados y con una ventana de 30 días hay
        # cuatro domingos, o sea que no llega al mínimo y la corrección queda
        # muda sin avisar. Le pasó a la primera verificación de punta a punta.
        atras = max(cfg.dias_nivel + 1,
                    pclima.VENTANA_ELASTICIDAD if usar_elasticidad else 0)
        dias = ([corte - timedelta(days=i) for i in range(atras, -1, -1)]
                + [d for d in dias_eval if d >= corte])
        f = pclima.factores_por_skill(modelos[corte], dias, clima_todo, fer)
        if usar_elasticidad:
            if corte not in elasticidades:
                elasticidades[corte] = pclima.elasticidad_por_tipo_de_dia(
                    diarias, f, corte, semanas=cfg.semanas_base, feriados=fer,
                    excluidos=excluidos)
            f = pclima.aplicar_elasticidad(f, elasticidades[corte], fer)
        return f

    calendario = _calendario_y_persistencia(cfg, desde_hist, hasta)
    for dia_obj in dias_eval:
        corte = dia_obj - timedelta(days=antelacion)
        factor = _factores_al_corte(corte)
        nivel_dia = _niveles_al_corte(corte)
        deriva = (_deriva_al_corte(corte)
                  if antelacion <= cfg.reparto_deriva_dias else {})
        no_habil = _no_habil_al_corte(corte)
        # El factor del tipo de día no depende de la antelación —qué día de la
        # semana cae se sabe con un año— así que va a todo el horizonte, a
        # diferencia de la deriva.
        ajuste_tipo = (no_habil if (no_habil and pl.tipo_de_dia(dia_obj, set(fer))
                                    == "no_habil") else 1.0)
        # Para la tabla se informa el factor del skill de mayor volumen, que es
        # el que manda: informar un promedio de factores de colas distintas no
        # significaría nada.
        del_dia = [v[dia_obj] for v in factor.values() if dia_obj in v]
        if del_dia:
            factores[dia_obj] = max(del_dia, key=lambda f: abs(f - 1))
        for s in skills:
            hist = historia.get(s.skill_id)
            if not hist:
                continue
            base = pl.baseline_estacional(
                hist, [dia_obj], intervalo_min=cfg.intervalo_min,
                feriados=fer, excluir_dias=excluidos, hoy=corte,
                semanas_base=cfg.semanas_base, dias_nivel=cfg.dias_nivel,
                factor_diario=factor.get(s.skill_id),
                nivel_por_tipo_de_dia=cfg.nivel_por_tipo_de_dia,
                nivel_diario=nivel_dia.get(s.skill_id),
                peso_nivel=peso_nivel, dias_observados=observados, **calendario)
            if not base:
                # El modelo no tuvo con qué pronosticar esta cola ese día (cola
                # nueva, o sin observaciones suficientes en la ventana). No entra
                # a `usados`: si nunca produce nada, su volumen real se informa
                # aparte en vez de contarse como error de un pronóstico que no existió.
                continue
            if sobre_total:
                reparto = pdatos.factor_de_asignacion(
                    asignacion, datetime.combine(dia_obj, time(0, 0)), s.skill_id)
                if reparto is None:
                    sin_asignacion.add(s.nombre)
                    continue
            usados.add(s.skill_id)
            for momento, llamadas in base.items():
                if sobre_total:
                    pron_total[momento] = pron_total.get(momento, 0.0) + llamadas
                    reparto = pdatos.factor_de_asignacion(asignacion, momento,
                                                          s.skill_id)
                    if reparto is None:
                        continue
                    llamadas = (llamadas * reparto * ajuste_tipo
                                * deriva.get(s.skill_id, 1.0))
                pron_acme[momento] = pron_acme.get(momento, 0.0) + llamadas
                # Además del agregado, el reparto por skill: sin él no se puede
                # dimensionar: cada cola tiene su TMO y su paciencia, y el pool
                # junta el tráfico de todas. Ver `_dotacion_del_backtest`.
                pron_skill.setdefault(momento, {})[s.skill_id] = \
                    pron_skill.get(momento, {}).get(s.skill_id, 0.0) + llamadas

    real_acme = _por_intervalo(nuestra, usados)
    real_total = _sumar(cruda, "total", usados) if sobre_total else {}
    fuera = _volumen_fuera(nuestra, usados, desde, hasta)

    # Mismo recorte de skills que el resto: comparar dos pronósticos que cubren
    # colas distintas no compara nada.
    produccion: Dict[datetime, float] = {}
    for (momento, sid), v in produccion_cruda.items():
        if sid in usados:
            produccion[momento] = produccion.get(momento, 0.0) + v
    skills_produccion = {sid for (_, sid) in produccion_cruda} & usados

    # ------------------------------------------------------------- ingenuo
    # Referencia contra la que el modelo tiene que ganar: el mismo día de la
    # semana anterior más cercano que ya se conocía a la fecha de corte. Sin
    # esto un "WAPE 9%" no significa nada — puede ser buenísimo o puede ser lo
    # que sale de no hacer nada.
    semanas_atras = max(1, -(-antelacion // 7))
    atras = timedelta(weeks=semanas_atras)
    paso = timedelta(minutes=cfg.intervalo_min)
    ingenuo: Dict[datetime, float] = {}
    dias_con_ingenuo: set = set()
    for dia_obj in dias_eval:
        momento = datetime.combine(dia_obj, time(0, 0))
        finaliza = momento + timedelta(days=1)
        while momento < finaliza:
            if momento - atras in real_acme:
                ingenuo[momento] = real_acme[momento - atras]
                dias_con_ingenuo.add(dia_obj)
            momento += paso

    # --------------------------------------------- combinación con el cliente
    # Qué habría dado mezclar nuestro pronóstico con el que manda Voltara, con el
    # peso MEDIDO sobre los días que ya habían pasado a cada fecha de corte. El
    # peso arranca en cero y sube a medida que hay con qué medirlo, así que en
    # una ventana corta la serie combinada es casi la nuestra: es el precio de no
    # dejarla mirar el futuro. La calibración usa una ventana larga por eso.
    combinado: Dict[datetime, float] = {}
    factor_comb: Dict[date, float] = {}
    pesos_finales: Dict[str, pcomb.PesoCliente] = {}
    if produccion:
        historia_comb: List[tuple] = []
        pendientes: List[tuple] = []
        for dia_obj in dias_eval:
            corte = dia_obj - timedelta(days=antelacion)
            while pendientes and pendientes[0][0] < corte:
                historia_comb.append(pendientes.pop(0))
            pesos_finales = pcomb.pesos_recientes(historia_comb, fer)
            mio, suyo = _del_dia(pron_acme, dia_obj), _del_dia(produccion, dia_obj)
            f = pcomb.factores_diarios({dia_obj: mio}, {dia_obj: suyo},
                                       pesos_finales, fer).get(dia_obj, 1.0)
            if f != 1.0:
                factor_comb[dia_obj] = f
            momento = datetime.combine(dia_obj, time(0, 0))
            finaliza = momento + timedelta(days=1)
            while momento < finaliza:
                if momento in pron_acme:
                    combinado[momento] = pron_acme[momento] * f
                momento += paso
            pendientes.append((dia_obj, _del_dia(real_acme, dia_obj), mio, suyo))

    # ------------------------------------------------------------ agregados
    por_dia = []
    for dia_obj in dias_eval:
        r = _del_dia(real_acme, dia_obj)
        p = _del_dia(pron_acme, dia_obj)
        rt = _del_dia(real_total, dia_obj) if sobre_total else None
        pt = _del_dia(pron_total, dia_obj) if sobre_total else None
        # El del cliente con la misma cuenta que el nuestro (real / pronóstico − 1),
        # para que los dos desvíos se lean uno al lado del otro.
        pc = _del_dia(produccion, dia_obj) if produccion else None
        fila = {
            "dia": dia_obj,
            "real": round(r, 1),
            "pronosticado": round(p, 1),
            "desvio": round(r / p - 1, 4) if p > 0 else None,
            "ingenuo": (round(_del_dia(ingenuo, dia_obj), 1)
                        if dia_obj in dias_con_ingenuo else None),
            "produccion": round(pc, 1) if pc is not None else None,
            "desvio_produccion": round(r / pc - 1, 4) if pc else None,
            "combinado": (round(_del_dia(combinado, dia_obj), 1)
                          if combinado else None),
            "peso_cliente": round(factor_comb[dia_obj], 3) if dia_obj in factor_comb else None,
            "tipo_dia": _nombre_tipo_de_dia(dia_obj, fer),
            "factor_clima": (round(factores[dia_obj], 3)
                             if dia_obj in factores else None),
            "real_total": round(rt, 1) if rt else None,
            "pronosticado_total": round(pt, 1) if pt else None,
            "share_real": round(r / rt, 4) if rt else None,
            "share_aplicado": round(p / pt, 4) if pt else None,
            "evento": marcados.get(dia_obj),
            "parcial": dia_obj >= hoy,
        }
        por_dia.append(fila)

    cerrados = [f for f in por_dia if not f["parcial"]]
    momentos_cerrados = [m for m in sorted(set(real_acme) | set(pron_acme))
                         if desde <= m.date() <= hasta and m.date() < hoy]

    def _pares(cual: str):
        return [(real_acme.get(m, 0.0), (pron_acme if cual == "modelo" else ingenuo)
                 .get(m, 0.0)) for m in momentos_cerrados]

    intervalo_modelo = pl.medir_error(_pares("modelo"), MIN_LLAMADAS_PARA_MAPE)
    diario_modelo = pl.medir_error([(f["real"], f["pronosticado"]) for f in cerrados])

    con_produccion = [m for m in momentos_cerrados if m in produccion]
    intervalo_produccion = pl.medir_error(
        [(real_acme.get(m, 0.0), produccion[m]) for m in con_produccion],
        MIN_LLAMADAS_PARA_MAPE) if con_produccion else None
    diario_produccion = pl.medir_error(
        [(f["real"], _del_dia(produccion, f["dia"])) for f in cerrados]) \
        if con_produccion else None

    con_ingenuo = [m for m in momentos_cerrados if m in ingenuo]
    intervalo_ingenuo = pl.medir_error(
        [(real_acme.get(m, 0.0), ingenuo[m]) for m in con_ingenuo],
        MIN_LLAMADAS_PARA_MAPE)

    intervalo_combinado = pl.medir_error(
        [(real_acme.get(m, 0.0), combinado.get(m, 0.0)) for m in momentos_cerrados],
        MIN_LLAMADAS_PARA_MAPE) if combinado else None
    diario_combinado = pl.medir_error(
        [(f["real"], f["combinado"] or 0.0) for f in cerrados]) if combinado else None

    normales = [f for f in cerrados if not f["evento"]]
    diario_normales = pl.medir_error([(f["real"], f["pronosticado"]) for f in normales])

    # NIVEL contra FORMA. Si se supiera de antemano el total del día, ¿cuánto
    # error quedaría? Lo que queda es el error de la CURVA intradía; lo que
    # desaparece es el error de NIVEL. La distinción decide qué hay que arreglar:
    # el nivel se ataca con clima, feriados y eventos —eso es el modelo de
    # machine learning—, y la forma con el perfil de horas, que es mucho más
    # estable. Para la malla de turnos duele más la forma; para la dotación
    # total, el nivel.
    por_dia_momentos: Dict[date, List[datetime]] = {}
    for m in momentos_cerrados:
        por_dia_momentos.setdefault(m.date(), []).append(m)
    pares_forma = []
    for f in cerrados:
        if f["pronosticado"] <= 0 or f["real"] <= 0:
            continue
        escala = f["real"] / f["pronosticado"]
        for m in por_dia_momentos.get(f["dia"], ()):
            pares_forma.append((real_acme.get(m, 0.0),
                                pron_acme.get(m, 0.0) * escala))
    forma = pl.medir_error(pares_forma, MIN_LLAMADAS_PARA_MAPE)

    total_modelo = (pl.medir_error(
        [(f["real_total"] or 0.0, f["pronosticado_total"] or 0.0)
         for f in cerrados if f["real_total"] and f["pronosticado_total"]])
        if sobre_total else None)

    # --------------------------------------------------------------- avisos
    if sin_asignacion:
        avisos.append(
            "Estos skills quedaron fuera del pronóstico porque no tienen tramo de "
            "asignación vigente en el período evaluado: " + ", ".join(sorted(sin_asignacion)))
    if asignacion_medida:
        avisos.append(
            "No hay tramos de asignación cargados: se usó el reparto medido en los "
            f"60 días previos al {corte_min}. El error sobre 'nuestras llamadas' "
            "arrastra ese supuesto; el de la demanda total del cliente no.")
    elif sobre_total:
        avisos.append(
            "El reparto sale de los tramos configurados, que se cargaron mirando la "
            "historia. Para juzgar el modelo mirá el error sobre la demanda total "
            "del cliente: ese es el único que no sabe nada del futuro.")
    faltan = len(cerrados) - len(normales)
    if faltan:
        avisos.append(
            f"{faltan} de los {len(cerrados)} días cerrados están marcados como "
            "atípicos o feriados. Se muestran igual, y además se informa el error "
            "sin ellos: un corte masivo no dice nada sobre la calidad del modelo.")
    if fuera:
        nombres = {sk.skill_id: sk.nombre for sk in cfg.skills}
        detalle = ", ".join(f"{nombres.get(k, k)} ({v:.0f})"
                            for k, v in sorted(fuera.items(), key=lambda x: -x[1]))
        avisos.append(
            f"Entraron {sum(fuera.values()):.0f} llamadas de colas que el modelo no "
            f"pronosticó y que por eso NO están en el error de arriba: {detalle}.")
    if produccion and skills_produccion != usados:
        nombres = {sk.skill_id: sk.nombre for sk in cfg.skills}
        faltantes = sorted(nombres.get(k, str(k)) for k in usados - skills_produccion)
        if faltantes:
            avisos.append(
                "El pronóstico en producción (dbo.Forecast) no cubre estos skills, así "
                "que en esos intervalos arranca con desventaja: " + ", ".join(faltantes))
    cotejo = _cotejar_fuentes(nuestra, cruda, desde, hasta) if sobre_total else None
    if cotejo and cotejo["diferencia"] is not None and abs(cotejo["diferencia"]) > 0.03:
        avisos.append(
            f"Las dos fuentes no coinciden en cuántas llamadas recibimos: el informe "
            f"de skills dice {cotejo['skills']:.0f} y el IVR completo "
            f"{cotejo['ivr']:.0f} ({cotejo['diferencia']:+.1%}). Mientras eso no se "
            "explique, el share medido tiene ese margen de error.")

    return {
        "desde": desde, "hasta": hasta, "antelacion": antelacion,
        "ajustes": ajustes,
        "modelo": MODELO_BASE + ("+clima" if factores else ""),
        "clima": _resumen_de_clima(usar_clima, modelos, factores, len(dias_eval)),
        "nivel": _resumen_de_nivel(usar_nivel, usar_clima, peso_nivel, modelos_nivel),
        "sobre_demanda_total": sobre_total,
        "dias_evaluados": len(cerrados),
        "resumen": {
            "intervalo": intervalo_modelo.como_dict(),
            "diario": diario_modelo.como_dict(),
            "diario_sin_eventos": diario_normales.como_dict(),
            "forma": forma.como_dict(),
            "total_cliente": total_modelo.como_dict() if total_modelo else None,
            "ingenuo": intervalo_ingenuo.como_dict(),
            "mejora_vs_ingenuo": _mejora(intervalo_modelo.wape, intervalo_ingenuo.wape),
            # El que la operación usa hoy. Es LA referencia: si el planificador no
            # le gana, no hay motivo para cambiar de pronóstico.
            "produccion": intervalo_produccion.como_dict() if intervalo_produccion else None,
            "produccion_diario": diario_produccion.como_dict() if diario_produccion else None,
            "mejora_vs_produccion": (_mejora(intervalo_modelo.wape, intervalo_produccion.wape)
                                     if intervalo_produccion else None),
            # Los dos mezclados, con el peso medido día a día hacia atrás.
            "combinado": intervalo_combinado.como_dict() if intervalo_combinado else None,
            "combinado_diario": diario_combinado.como_dict() if diario_combinado else None,
            "mejora_por_combinar": (_mejora(intervalo_combinado.wape, intervalo_modelo.wape)
                                    if intervalo_combinado else None),
        },
        "por_tipo_de_dia": _error_por_tipo_de_dia(cerrados),
        "combinacion": {
            "pesos": {k: v.como_dict() for k, v in pesos_finales.items()},
            "dias_con_factor": len(factor_comb),
            "reparto_no_habil": next(
                (round(factores_no_habil[c], 4)
                 for c in sorted(factores_no_habil, reverse=True)
                 if factores_no_habil[c]), None),
        } if produccion or factores_no_habil else None,
        "peor_dia": _extremo(cerrados, peor=True),
        "mejor_dia": _extremo(cerrados, peor=False),
        # Cuántos operadores habríamos pedido y cuántos hubo. Va al lado del
        # cotejo de llamadas y no en otra pantalla: el error de pronóstico sólo
        # importa por lo que le hace a la dotación, y acá se ve cuánto de la
        # brecha es del pronóstico y cuánto del dimensionamiento.
        "dotacion": _dotacion_del_backtest(
            engine, cfg, campana_id, pron_skill, nuestra, usados, dias_eval,
            hoy, fer,
            # EL PERFIL DE TMO SE ARMA SÓLO CON LO ANTERIOR AL PRIMER CORTE. Con
            # el perfil de toda la ventana, el pronóstico de dotación dimensionaría
            # con el TMO que después resultó: el mismo futuro del que el resto del
            # backtest se cuida.
            pdatos.perfil_de_tmo({k: v for k, v in nuestra.items()
                                  if k[0].date() < corte_min})),
        "por_dia": por_dia,
        "por_intervalo": [
            {"momento": m,
             "real": round(real_acme.get(m, 0.0), 1),
             "pronosticado": round(pron_acme.get(m, 0.0), 1),
             "produccion": round(produccion[m], 1) if m in produccion else None,
             "combinado": round(combinado[m], 1) if m in combinado else None,
             "real_total": round(real_total[m], 1) if m in real_total else None,
             "pronosticado_total": round(pron_total[m], 1) if m in pron_total else None}
            for m in sorted(set(real_acme) | set(pron_acme))
            if desde <= m.date() <= hasta
        ],
        "cotejo_fuentes": cotejo,
        "fuera_del_pronostico": [
            {"skill_id": k, "nombre": next((sk.nombre for sk in cfg.skills
                                            if sk.skill_id == k), str(k)),
             "llamadas": round(v, 1)}
            for k, v in sorted(fuera.items(), key=lambda x: -x[1])
        ],
        "avisos": avisos,
    }


def _por_intervalo(serie, skills=None) -> Dict[datetime, float]:
    """(momento, skill) -> llamadas  ==>  momento -> llamadas."""
    salida: Dict[datetime, float] = {}
    for (momento, sid), (llamadas, _) in serie.items():
        if skills is not None and sid not in skills:
            continue
        salida[momento] = salida.get(momento, 0.0) + llamadas
    return salida


def _sumar(cruda, campo: str, skills=None) -> Dict[datetime, float]:
    salida: Dict[datetime, float] = {}
    for (momento, sid), v in cruda.items():
        if skills is not None and sid not in skills:
            continue
        salida[momento] = salida.get(momento, 0.0) + v[campo]
    return salida


def _volumen_fuera(serie, usados, desde: date, hasta: date) -> Dict[int, float]:
    """Llamadas reales de skills que el modelo no pronosticó.

    Se informan en vez de desaparecer: si esto es grande, el error de arriba está
    medido sobre menos cola de la que realmente entra, y el número se lee mejor
    de lo que es."""
    fuera: Dict[int, float] = {}
    for (momento, sid), (llamadas, _) in serie.items():
        if sid in usados or not (desde <= momento.date() <= hasta) or llamadas <= 0:
            continue
        fuera[sid] = fuera.get(sid, 0.0) + llamadas
    return fuera


def _del_dia(serie: Dict[datetime, float], dia: date) -> float:
    return sum(v for m, v in serie.items() if m.date() == dia)


def _dias_con_evento(eventos) -> Dict[date, str]:
    """Qué días del período tienen un evento cargado, y de qué tipo.

    Se muestran en la tabla para que un día muy malo se pueda leer como lo que
    fue —un corte, un feriado— en vez de como una falla del modelo.
    """
    salida: Dict[date, str] = {}
    for e in eventos:
        dia = e["Desde"].date()
        while dia <= e["Hasta"].date():
            etiqueta = e["Tipo"] or "evento"
            if not e["Confirmado"]:
                etiqueta += " (sin confirmar)"
            salida.setdefault(dia, etiqueta)
            dia += timedelta(days=1)
    return salida


def _mejora(wape_modelo, wape_referencia) -> Optional[float]:
    """Cuánto mejor es el modelo que repetir la semana pasada.

    Positivo = el modelo gana. Si esto da cero o negativo, el modelo no está
    aportando nada por encima de la estacionalidad semanal y hay que decirlo.
    """
    # `not 0.0` es True: con el chequeo ingenuo, un modelo PERFECTO devolvía
    # "no se puede comparar" en vez de "gana por todo".
    if wape_modelo is None or not wape_referencia:
        return None
    return round(1 - wape_modelo / wape_referencia, 4)


def _extremo(filas, peor: bool):
    candidatos = [f for f in filas if f["desvio"] is not None]
    if not candidatos:
        return None
    clave = (lambda f: abs(f["desvio"]))
    return max(candidatos, key=clave) if peor else min(candidatos, key=clave)


def _cotejar_fuentes(nuestra, cruda, desde: date, hasta: date):
    """Las dos fuentes tienen que decir lo mismo sobre NUESTRAS llamadas.

    El informe de skills carga cada hora y es el que se usa para comparar el
    mismo día; el IVR completo carga de noche y es el que trae al resto de los
    BPO. Si difieren, el share medido está mal en esa misma proporción, y más
    vale que se vea acá que descubrirlo negociando el contrato.
    """
    en_rango = lambda m: desde <= m.date() <= hasta
    skills = sum(ll for (m, _), (ll, _) in nuestra.items() if en_rango(m))
    ivr = sum(v["acme"] for (m, _), v in cruda.items() if en_rango(m))
    return {
        "skills": round(skills, 1),
        "ivr": round(ivr, 1),
        "diferencia": round(skills / ivr - 1, 4) if ivr > 0 else None,
    }


def _resumen_de_nivel(usar: bool, usar_clima: bool, peso: float,
                      modelos: Dict[date, object]) -> dict:
    """Qué hizo la segunda opinión del nivel en esta corrida.

    Igual que con el clima, se informa aunque no haya podido ajustar: "está
    prendida y no le alcanzaron los datos" y "está apagada" son dos cosas
    distintas y la pantalla tiene que poder decir cuál.
    """
    if not usar:
        return {"usado": False, "motivo": "está apagada para la campaña"}
    if not usar_clima:
        return {"usado": False,
                "motivo": ("necesita el modelo de clima prendido: sus rasgos son "
                           "el tiempo y el calendario")}
    ajustados = [m for m in modelos.values() if m is not None]
    if not ajustados:
        return {"usado": False,
                "motivo": (f"no llegó a {pnivel.MIN_FILAS_PARA_AJUSTAR} filas de "
                           "entrenamiento en ninguna fecha de corte")}
    return {
        "usado": True,
        "peso": round(peso, 3),
        "cortes": len(modelos),
        "cortes_con_modelo": len(ajustados),
        "filas": ajustados[-1].n,
        "skills": len(ajustados[-1].skills),
        "rasgos": len(pnivel.COLUMNAS),
    }


def _resumen_de_clima(usar: bool, modelos: Dict[date, object],
                      factores: Dict[date, float], dias: int):
    """Qué hizo el modelo de clima en esta corrida del backtest.

    Se informa aunque no haya podido ajustar: "está activado y no sirvió" y "está
    apagado" son dos situaciones distintas y la pantalla tiene que poder decir
    cuál de las dos es.
    """
    if not usar:
        return {"usado": False, "motivo": "apagado para la campaña"}
    cortes = [c for c, ms in modelos.items() if ms]
    if not cortes:
        return {"usado": False,
                "motivo": f"no hay {pclima.MIN_DIAS_PARA_AJUSTAR} días de clima "
                          f"observado, o ningún skill llega al R² mínimo de "
                          f"{pclima.R2_MINIMO:.0%}"}
    # El del corte más reciente: es el que mejor representa lo que el modelo sabe
    # hoy. Los anteriores son el mismo ajuste con menos historia.
    ultimos = modelos[max(cortes)]
    principal = max(ultimos.values(), key=lambda m: m.n * m.r2)
    return {
        "usado": True,
        "dias_con_factor": len(factores),
        "dias_evaluados": dias,
        "skills_con_factor": len(ultimos),
        "factor_min": round(min(factores.values()), 3) if factores else None,
        "factor_max": round(max(factores.values()), 3) if factores else None,
        "modelo": principal.como_dict(),
        "por_skill": {str(k): {"r2": round(m.r2, 3), "n": m.n}
                      for k, m in sorted(ultimos.items())},
    }


# =========================================================================
# NECESIDADES DE REFUERZO — cuántas horas extra pedir, para cuándo y cómo
# =========================================================================
# Es el paso que faltaba para que el planificador sirva para algo más que mirar.
# El requerimiento por intervalo dice cuánta gente hace falta; la malla dice
# cuánta hay; la resta dice cuánta falta. Esto último lo convierte en un pedido:
# bloques contiguos, con la antelación disponible y el canal que corresponde.
#
# EL ESCENARIO ALTO NO ES PESIMISMO, ES EL COSTO ASIMÉTRICO
# ----------------------------------------------------------
# Quedarse corto y quedarse largo no cuestan lo mismo. Largo cuesta las horas
# pagadas de más; corto cuesta el nivel de servicio, el abandono y —en
# Electrodependientes— un compromiso contractual de atención. Por eso cada tramo
# se informa con el volumen pronosticado Y con el volumen del escenario alto
# (pronóstico más el error típico a esa antelación, medido con el backtest). El
# número que se pide sale de esa comparación, y esa decisión es de negocio.

# Error típico del pronóstico según con cuántos días de antelación se hizo.
# MEDIDO con el backtest sobre la historia de Voltara; `medir_error_por_horizonte`
# los recalcula y el endpoint /error-horizonte lo expone. Son WAPE por intervalo,
# o sea el error ponderado por volumen en el lugar donde hay gente atendiendo.
#
# Medido sobre 357 días corridos (2025-09-15 a 2026-09-06), con la configuración
# que quedó: deriva del reparto 7 días (que sólo aplica hasta 7 días) y clima:
#
#     antelación   WAPE ½h
#          1        30,27%
#          3        30,67%
#          7        30,76%
#         14        30,97%
#         30        31,03%
#
# LA CURVA SUBE, y eso es nuevo. La medición anterior daba casi plano (35,7% a un
# día contra 34,6% a treinta), o sea PEOR con datos más frescos, que es imposible
# y era el síntoma de que el modelo no estaba usando la información reciente. Con
# la deriva del reparto —que es justamente información de los últimos días— la
# curva se ordena.
#
# Pero la pendiente es chica: 0,76 puntos entre un día y treinta. Sigue
# conviniendo resolver los faltantes con antelación, moviendo la malla, en vez de
# dejarlos para la hora extra o la convocatoria del día: esperar mejora el
# pronóstico menos de un punto y la malla no cuesta plata.
#
# Ojo con el valor de 30: el backtest le da al modelo el clima OBSERVADO del día
# pronosticado, y a 30 días no existe pronóstico meteorológico (Open-Meteo llega a
# 16). O sea que 31,03% es optimista y el error real del horizonte largo es peor.
# Ver la nota del mismo tema en el encabezado del backtest.
#
# El horizonte 0 es el intradía y no sale del backtest sino del seguimiento: a
# media mañana ya hay acumulado real del día y la reproyección es mucho más
# ajustada que cualquier pronóstico previo.
ERROR_POR_HORIZONTE = {0: 0.12, 1: 0.303, 3: 0.307, 7: 0.308, 14: 0.310, 30: 0.310}


def error_esperado(dias: int, tabla: Optional[Dict[int, float]] = None) -> float:
    """Error típico a esa antelación, interpolando entre los horizontes medidos."""
    tabla = tabla or ERROR_POR_HORIZONTE
    claves = sorted(tabla)
    if dias <= claves[0]:
        return tabla[claves[0]]
    if dias >= claves[-1]:
        return tabla[claves[-1]]
    for a, b in zip(claves, claves[1:]):
        if a <= dias <= b:
            peso = (dias - a) / (b - a)
            return tabla[a] + peso * (tabla[b] - tabla[a])
    return tabla[claves[-1]]


def necesidades(engine: Engine, campana_id: int, horizonte: str = "operativo",
                hoy: Optional[date] = None, solo_desde_hoy: bool = True,
                error_tabla: Optional[Dict[int, float]] = None) -> Dict[str, object]:
    """Los refuerzos que hacen falta sobre la corrida vigente.

    Devuelve tramos contiguos con faltante, cada uno con la antelación que queda
    y con el canal que corresponde (mover la malla, hora extra, convocatoria del
    día), más el mismo tramo dimensionado con el escenario alto.
    """
    hoy = hoy or date.today()
    with engine.connect() as conn:
        if not pdatos.schema_disponible(conn):
            raise pdatos.MigracionPendiente(
                "Falta correr scripts/migrations/2026-09-03_planificador.sql")
        corrida = pdatos.corrida_vigente(conn, campana_id, horizonte)
        if not corrida:
            return {"hay_corrida": False, "refuerzos": [], "resumen": {}}
        cfg = pdatos.cargar_config(conn, campana_id)
        req = pdatos.leer_requerimiento(conn, corrida["CorridaID"])
        pron = pdatos.leer_pronostico(conn, corrida["CorridaID"])

    # El escenario alto se re-dimensiona con el MISMO motor y la misma config:
    # inflar la dotación por un porcentaje sería otra cuenta, porque la relación
    # entre volumen y operadores no es lineal (la economía de escala de la cola
    # hace que 30% más de llamadas pida bastante menos de 30% más de gente).
    alto = _dimensionar_escenario_alto(cfg, pron, hoy, error_tabla)

    filas = []
    for r in req:
        if r["Brecha"] is None or r["Brecha"] >= 0:
            continue
        momento = r["Intervalo"]
        if solo_desde_hoy and momento.date() < hoy:
            continue
        citados = r["OperadoresPlanificados"]
        # La brecha guardada ya va en equivalentes por antigüedad; el escenario
        # alto se mide contra lo mismo o las dos columnas no se comparan.
        if citados is not None and r.get("CitadosEquivalentes") is not None:
            citados = int(float(r["CitadosEquivalentes"]) + 0.5)
        necesarios_alto = alto.get((r["PoolID"], momento))
        filas.append({
            "pool_id": r["PoolID"], "pool": r["Pool"], "momento": momento,
            "faltante": -int(r["Brecha"]),
            "faltante_alto": (max(0, necesarios_alto - citados)
                              if necesarios_alto is not None and citados is not None
                              else None),
            "llamadas": float(r["Llamadas"] or 0),
        })

    refuerzos = pl.agrupar_refuerzos(filas, hoy, cfg.intervalo_min)

    refuerzos_dicts = []
    for r in refuerzos:
        d = r.como_dict()
        faltantes_bloque = [
            (f["momento"], f["faltante"])
            for f in filas
            if f["pool_id"] == r.pool_id and r.desde <= f["momento"] < r.hasta
        ]
        d["turnos_sugeridos"] = pturnos.sugerir_turnos(faltantes_bloque, intervalo_min=cfg.intervalo_min)
        refuerzos_dicts.append(d)

    resumen = pl.resumen_de_refuerzos(refuerzos)

    # Seguimiento de pedidos de refuerzo (best-effort)
    seguimiento_disponible = False
    ultimos_14d_horas_pedidas = 0.0
    ultimos_14d_horas_cubiertas = 0.0

    try:
        with engine.connect() as conn:
            if ppedidos.tabla_disponible(conn):
                seguimiento_disponible = True
                try:
                    ppedidos.cerrar_pedidos_vencidos(engine, campana_id, hoy=hoy, intervalo_min=cfg.intervalo_min)
                except Exception as e:
                    logger.warning(f"No se pudo completar el cierre perezoso de pedidos en necesidades: {e}")

                pedidos = ppedidos.listar_pedidos(conn, campana_id)
                refuerzos_dicts = ppedidos.emparejar(refuerzos_dicts, pedidos)

                h14 = hoy - timedelta(days=14)
                pedidos_14d = [
                    p for p in pedidos
                    # Días cerrados: pedido y cubierto sobre los mismos días.
                    if p.get("Dia") and h14 <= p["Dia"] < hoy and p.get("Estado") != "descartado"
                ]
                ultimos_14d_horas_pedidas = round(
                    sum(float(p.get("HorasOperador") or 0.0) for p in pedidos_14d), 1
                )
                ultimos_14d_horas_cubiertas = round(
                    sum(float(p.get("HorasCubiertas") or 0.0) for p in pedidos_14d), 1
                )
    except Exception as e:
        logger.warning(f"Error consultando seguimiento de pedidos en necesidades: {e}")
        seguimiento_disponible = False

    if not seguimiento_disponible:
        for d in refuerzos_dicts:
            d["pedido"] = None

    resumen["seguimiento_disponible"] = seguimiento_disponible
    resumen["ultimos_14d_horas_pedidas"] = ultimos_14d_horas_pedidas
    resumen["ultimos_14d_horas_cubiertas"] = ultimos_14d_horas_cubiertas

    return {
        "hay_corrida": True,
        "corrida_id": corrida["CorridaID"],
        "desde": corrida["Desde"], "hasta": corrida["Hasta"],
        "hoy": hoy,
        "resumen": resumen,
        "error_por_horizonte": error_tabla or ERROR_POR_HORIZONTE,
        "refuerzos": refuerzos_dicts,
    }


def _dimensionar_escenario_alto(cfg, pron, hoy: date,
                                error_tabla) -> Dict[tuple, int]:
    """(pool, momento) -> operadores a planificar si el día viene en el escenario alto.

    Se vuelve a correr el dimensionamiento completo con el volumen inflado por el
    error típico de la antelación de cada día. No se infla la dotación
    directamente porque la relación no es lineal: la cola compartida tiene
    economía de escala y 30% más de llamadas pide bastante menos de 30% más de
    gente. Inflar el resultado sobredimensionaría.
    """
    por_pool: Dict[int, Dict[datetime, List[pl.DemandaSkill]]] = {}
    skill_a_pool = {s.skill_id: s.pool_id for s in cfg.skills if s.pool_id is not None}
    for f in pron:
        pool_id = skill_a_pool.get(f["SkillID"])
        if pool_id is None:
            continue
        momento = f["Intervalo"]
        factor = 1 + error_esperado((momento.date() - hoy).days, error_tabla)
        tmo = float(f["TmoSeg"] or 0)
        if tmo <= 0:
            continue
        por_pool.setdefault(pool_id, {}).setdefault(momento, []).append(
            pl.DemandaSkill(f["SkillID"], float(f["LlamadasAcme"] or 0) * factor, tmo))

    salida: Dict[tuple, int] = {}
    for pool_id, demanda in por_pool.items():
        pool = cfg.pools.get(pool_id)
        if not pool:
            continue
        for r in pl.plan_de_pool(cfg, pool, demanda):
            salida[(pool_id, r.momento)] = r.operadores_a_planificar
    return salida


def calibrar_combinacion(engine: Engine, campana_id: int, dias: int = 180,
                         antelacion: int = ANTELACION_DEFECTO,
                         hoy: Optional[date] = None) -> Dict[str, object]:
    """Cuánto peso merece el pronóstico del cliente, medido contra lo que pasó.

    Corre el backtest sobre los últimos `dias` (en tandas, porque una sola no
    puede pasar de `MAX_DIAS_BACKTEST`), junta la serie diaria de los dos
    pronósticos contra lo real y calcula el peso por tipo de día. Devuelve
    también qué error habría tenido la combinación, simulada hacia adelante con
    el peso que se conocía en cada fecha: sin eso el peso es una promesa.

    NO guarda nada. Aplicarlo es un paso aparte, igual que la calibración de la
    paciencia y la del shrinkage: el número medido se muestra al lado del
    vigente y alguien decide.
    """
    hoy = hoy or date.today()
    hasta = hoy - timedelta(days=1)
    desde = hasta - timedelta(days=max(dias, 30) - 1)

    historia: List[tuple] = []
    corte = desde
    while corte <= hasta:
        fin = min(corte + timedelta(days=MAX_DIAS_BACKTEST - 1), hasta)
        bt = backtest(engine, campana_id, corte, fin, antelacion=antelacion, hoy=hoy)
        for f in bt["por_dia"]:
            if f["parcial"] or not f["real"] or not f["pronosticado"] or not f["produccion"]:
                continue
            historia.append((f["dia"], f["real"], f["pronosticado"], f["produccion"]))
        corte = fin + timedelta(days=1)

    if not historia:
        raise ValueError(
            "No hay días con los dos pronósticos a la vez en el período pedido: "
            "sin eso no se puede medir cuánto pesa el del cliente.")

    with engine.connect() as conn:
        fer = pdatos.feriados(conn, desde, hasta + timedelta(days=1), campana_id)

    filas, pesos = pcomb.simular(historia, fer, antelacion=antelacion, ventana=0)
    por_tipo = []
    for tipo in ("habil", "sabado", "no_habil"):
        del_tipo = [f for f in filas if f["tipo"] == tipo]
        if not del_tipo:
            continue
        por_tipo.append({
            "tipo": tipo, "n": len(del_tipo),
            "nuestro": pl.medir_error([(f["real"], f["nuestro"]) for f in del_tipo]).como_dict(),
            "cliente": pl.medir_error([(f["real"], f["cliente"]) for f in del_tipo]).como_dict(),
            "combinado": pl.medir_error([(f["real"], f["combinado"]) for f in del_tipo]).como_dict(),
        })
    return {
        "desde": desde, "hasta": hasta, "antelacion": antelacion,
        "dias": len(filas),
        "pesos": {k: v.como_dict() for k, v in pesos.items()},
        "por_tipo_de_dia": por_tipo,
        "total": {
            "nuestro": pl.medir_error([(f["real"], f["nuestro"]) for f in filas]).como_dict(),
            "cliente": pl.medir_error([(f["real"], f["cliente"]) for f in filas]).como_dict(),
            "combinado": pl.medir_error([(f["real"], f["combinado"]) for f in filas]).como_dict(),
        },
    }


def medir_error_por_horizonte(engine: Engine, campana_id: int, desde: date,
                              hasta: date,
                              antelaciones: Sequence[int] = (1, 3, 7, 14, 30)
                              ) -> Dict[int, float]:
    """Corre el backtest a varias antelaciones y devuelve el error de cada una.

    Es lo que alimenta el escenario alto. Se mide y no se supone: el error a 30
    días no es "el doble" del de 1 día, y suponerlo llevaría a pedir horas extra
    por ruido o a no pedirlas cuando hacen falta.
    """
    salida: Dict[int, float] = {}
    for ant in antelaciones:
        bt = backtest(engine, campana_id, desde, hasta, antelacion=ant)
        wape = (bt.get("resumen") or {}).get("intervalo", {}).get("wape")
        if wape:
            salida[ant] = round(wape, 4)
    return salida
