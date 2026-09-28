"""Planificador — Seguimiento y ciclo de vida de pedidos de refuerzo a RRHH.

POR QUÉ ESTO EXISTE
-------------------
Cada bloque con brecha negativa que calcula la pestaña Refuerzos representa un
faltante a gestionar con RRHH (malla, horas extra o convocatoria). Sin este
módulo, un bloque pedido se ve idéntico a uno sin pedir en cada recálculo, y
nunca se sabe si el refuerzo efectivamente cubrió la brecha tras el cierre del
día.

ESTADOS DEL PEDIDO
------------------
  - pedido: solicitado a RRHH, a la espera del cierre del día para verificar.
  - cubierto: el registro de RRHH sumó al menos el 90% de las horas pedidas.
  - cubierto_parcial: se sumó gente por encima de la malla, pero menos del 90%.
  - no_cubierto: el día cerró sin gente extra en ese tramo (0 h cubiertas).
  - descartado: decisión operativa de no pedir refuerzo para ese bloque.

EVALUACIÓN DE COBERTURA
-----------------------
Para cada intervalo del pedido, las horas cubiertas se miden como:
    MIN(faltante_del_plan, MAX(0, dotacion_real - dotacion_malla)) * (intervalo_min / 60)
Es decir: sólo cuenta el personal extra efectivamente agregado sobre la malla
publicada, hasta el límite del faltante pronosticado.
"""

from datetime import date, datetime, timedelta
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app import planificador_datos as pdatos

logger = logging.getLogger(__name__)

# Umbral para considerar que un pedido fue cubierto satisfactoriamente (90%)
UMBRAL_CUBIERTO: float = 0.90

ESTADOS_VALIDOS: tuple = (
    "pedido",
    "cubierto",
    "cubierto_parcial",
    "no_cubierto",
    "descartado",
)

TABLA_REFUERZO_PEDIDO: str = "planificacion.RefuerzoPedido"


# --------------------------------------------------------------------- guards

def tabla_disponible(conn: Connection) -> bool:
    """Verifica si la tabla planificacion.RefuerzoPedido existe en la base."""
    return pdatos._tiene_tabla(conn, TABLA_REFUERZO_PEDIDO)


def exigir_tabla(conn: Connection) -> None:
    """Lanza MigracionPendiente si la tabla aún no fue creada."""
    if not tabla_disponible(conn):
        raise pdatos.MigracionPendiente(
            "Falta correr scripts/migrations/2026-09-15_planificador_refuerzo_pedido.sql"
        )


# ------------------------------------------------------------- lógica pura

def emparejar(
    bloques: Sequence[Union[dict, Any]],
    pedidos: Sequence[dict],
) -> List[dict]:
    """Asigna a cada bloque de refuerzo su pedido correspondiente.

    Un bloque (pool, día, [desde, hasta)) toma el pedido del mismo pool y día
    que se superpone en tiempo. Si hay varios que se superponen, toma el más
    reciente no descartado; si todos están descartados, toma el descartado más
    reciente. Si no hay ninguno, el bloque queda sin pedido (estado 'pendiente').
    """
    salida: List[dict] = []

    for b in bloques:
        # Extraemos campos tolerando objetos o dicts
        b_dict = dict(b) if isinstance(b, dict) else b.como_dict() if hasattr(b, "como_dict") else vars(b).copy()
        pool_id = b_dict.get("pool_id")
        dia_b = b_dict.get("dia")
        desde_b = b_dict.get("desde")
        hasta_b = b_dict.get("hasta")

        candidatos = []
        for p in pedidos:
            p_pool = p.get("PoolID") if p.get("PoolID") is not None else p.get("pool_id")
            p_dia = p.get("Dia") if p.get("Dia") is not None else p.get("dia")
            p_desde = p.get("Desde") if p.get("Desde") is not None else p.get("desde")
            p_hasta = p.get("Hasta") if p.get("Hasta") is not None else p.get("hasta")

            if p_pool == pool_id and p_dia == dia_b:
                # Superposición temporal entre [desde_b, hasta_b) y [p_desde, p_hasta)
                if max(desde_b, p_desde) < min(hasta_b, p_hasta):
                    candidatos.append(p)

        elegido = None
        if candidatos:
            no_descartados = [
                c for c in candidatos
                if (c.get("Estado") or c.get("estado")) != "descartado"
            ]
            criterio = lambda c: (
                c.get("CreadoEn") or datetime.min,
                c.get("RefuerzoPedidoID") or c.get("id") or 0,
            )
            if no_descartados:
                elegido = max(no_descartados, key=criterio)
            else:
                elegido = max(candidatos, key=criterio)

        if elegido:
            pid = elegido.get("RefuerzoPedidoID") or elegido.get("id")
            p_est = elegido.get("Estado") or elegido.get("estado")
            p_cub = elegido.get("HorasCubiertas") if elegido.get("HorasCubiertas") is not None else elegido.get("horas_cubiertas")
            b_dict["pedido"] = {
                "id": int(pid) if pid is not None else None,
                "estado": p_est,
                "horas_cubiertas": float(p_cub) if p_cub is not None else None,
            }
        else:
            b_dict["pedido"] = None

        salida.append(b_dict)

    return salida


def evaluar_cobertura(
    pedido: dict,
    faltantes_por_intervalo: Dict[datetime, int],
    registro_por_intervalo: Dict[datetime, int],
    malla_por_intervalo: Dict[datetime, int],
    intervalo_min: int = 30,
) -> Dict[str, Any]:
    """Calcula las horas cubiertas por la operación para un pedido cerrado.

    horas cubiertas = Σ sobre los intervalos del pedido de
        MIN(faltante, MAX(0, registro − malla)) * intervalo_min / 60

    Estado:
      - cubierto si horas_cubiertas >= 90% de HorasOperador
      - cubierto_parcial si horas_cubiertas > 0
      - no_cubierto si 0
    """
    desde = pedido.get("Desde") or pedido.get("desde")
    hasta = pedido.get("Hasta") or pedido.get("hasta")
    paso = timedelta(minutes=intervalo_min)

    suma_cubierto = 0
    momento = desde
    while momento < hasta:
        faltante = max(0, int(faltantes_por_intervalo.get(momento, 0)))
        registro = max(0, int(registro_por_intervalo.get(momento, 0)))
        malla = max(0, int(malla_por_intervalo.get(momento, 0)))

        extra = max(0, registro - malla)
        cubierto = min(faltante, extra)
        suma_cubierto += cubierto
        momento += paso

    horas_cubiertas = round(suma_cubierto * (intervalo_min / 60.0), 2)
    horas_operador = float(
        pedido.get("HorasOperador") if pedido.get("HorasOperador") is not None
        else pedido.get("horas_operador", 0.0)
    )

    if horas_operador > 0:
        ratio = horas_cubiertas / horas_operador
        if ratio >= UMBRAL_CUBIERTO:
            nuevo_estado = "cubierto"
        elif horas_cubiertas > 0:
            nuevo_estado = "cubierto_parcial"
        else:
            nuevo_estado = "no_cubierto"
    else:
        nuevo_estado = "cubierto" if horas_cubiertas > 0 else "no_cubierto"

    return {
        "estado": nuevo_estado,
        "horas_cubiertas": horas_cubiertas,
    }


# -------------------------------------------------------- lecturas y escrituras

def listar_pedidos(
    conn: Connection,
    campana_id: int,
    desde: Optional[date] = None,
    hasta: Optional[date] = None,
) -> List[dict]:
    """Lista los pedidos registrados para la campaña, con filtros opcionales de fecha."""
    exigir_tabla(conn)

    condiciones = ["CampanaID = :c"]
    params: Dict[str, Any] = {"c": campana_id}

    if desde is not None:
        condiciones.append("Dia >= :desde")
        params["desde"] = desde
    if hasta is not None:
        condiciones.append("Dia <= :hasta")
        params["hasta"] = hasta

    where_sql = " AND ".join(condiciones)

    filas = conn.execute(text(f"""
        SELECT RefuerzoPedidoID, CampanaID, PoolID, Dia, Desde, Hasta,
               FaltantePico, HorasOperador, Accion, Estado, HorasCubiertas,
               Nota, CreadoPor, CreadoEn, ActualizadoPor, ActualizadoEn
        FROM planificacion.RefuerzoPedido
        WHERE {where_sql}
        ORDER BY Dia, Desde, RefuerzoPedidoID
    """), params).mappings().all()

    return [dict(f) for f in filas]


def crear_pedido(
    conn: Connection,
    campana_id: int,
    pool_id: int,
    dia: date,
    desde: datetime,
    hasta: datetime,
    faltante_pico: int,
    horas_operador: float,
    accion: str,
    nota: Optional[str] = None,
    estado: str = "pedido",
    usuario: Optional[int] = None,
) -> int:
    """Inserta un nuevo pedido de refuerzo con su foto inicial."""
    exigir_tabla(conn)

    if estado not in ESTADOS_VALIDOS:
        raise ValueError(f"Estado inválido: {estado}. Debe ser uno de {ESTADOS_VALIDOS}")

    fila = conn.execute(text("""
        INSERT INTO planificacion.RefuerzoPedido
            (CampanaID, PoolID, Dia, Desde, Hasta, FaltantePico, HorasOperador,
             Accion, Estado, Nota, CreadoPor, ActualizadoPor)
        OUTPUT INSERTED.RefuerzoPedidoID
        VALUES (:c, :p, :dia, :desde, :hasta, :pico, :horas, :accion, :estado, :nota, :u, :u)
    """), {
        "c": campana_id,
        "p": pool_id,
        "dia": dia,
        "desde": desde,
        "hasta": hasta,
        "pico": faltante_pico,
        "horas": horas_operador,
        "accion": accion,
        "estado": estado,
        "nota": nota,
        "u": usuario,
    }).scalar()

    return int(fila)


def actualizar_pedido(
    conn: Connection,
    pedido_id: int,
    campana_id: int,
    estado: Optional[str] = None,
    nota: Optional[str] = None,
    horas_cubiertas: Optional[float] = None,
    usuario: Optional[int] = None,
) -> bool:
    """Modifica el estado, nota o cobertura de un pedido existente."""
    exigir_tabla(conn)

    sets = ["ActualizadoPor = :u", "ActualizadoEn = SYSUTCDATETIME()"]
    params: Dict[str, Any] = {"id": pedido_id, "c": campana_id, "u": usuario}

    if estado is not None:
        if estado not in ESTADOS_VALIDOS:
            raise ValueError(f"Estado inválido: {estado}. Debe ser uno de {ESTADOS_VALIDOS}")
        sets.append("Estado = :estado")
        params["estado"] = estado

    if nota is not None:
        sets.append("Nota = :nota")
        params["nota"] = nota

    if horas_cubiertas is not None:
        sets.append("HorasCubiertas = :horas_cubiertas")
        params["horas_cubiertas"] = horas_cubiertas

    filas = conn.execute(text(f"""
        UPDATE planificacion.RefuerzoPedido
        SET {', '.join(sets)}
        WHERE RefuerzoPedidoID = :id AND CampanaID = :c
    """), params).rowcount

    return filas > 0


# ------------------------------------------------------------- cierre perezoso

def cerrar_pedidos_vencidos(
    engine_o_conn: Union[Engine, Connection],
    campana_id: int,
    hoy: Optional[date] = None,
    intervalo_min: int = 30,
) -> int:
    """Cierre perezoso de pedidos cuyo día ya cerró (Dia < hoy) y siguen en 'pedido'.

    Evalúa cada pedido contra el registro real (dbo.payroll / dotacion_real)
    y la malla (dbo.payroll_futuro / dotacion_planificada), cruzándolo con el
    faltante por intervalo del requerimiento guardado.
    La actualización se realiza en una transacción separada de la lectura.
    """
    hoy = hoy or date.today()

    # Si recibimos un Engine, abrimos conexión de lectura
    if hasattr(engine_o_conn, "connect"):
        with engine_o_conn.connect() as conn_read:
            return _procesar_cierre_perezoso(conn_read, engine_o_conn, campana_id, hoy, intervalo_min)
    else:
        return _procesar_cierre_perezoso(engine_o_conn, engine_o_conn, campana_id, hoy, intervalo_min)


def _procesar_cierre_perezoso(
    conn_read: Connection,
    engine_o_conn_write: Union[Engine, Connection],
    campana_id: int,
    hoy: date,
    intervalo_min: int,
) -> int:
    if not tabla_disponible(conn_read):
        return 0

    pendientes = conn_read.execute(text("""
        SELECT RefuerzoPedidoID, PoolID, Dia, Desde, Hasta, HorasOperador
        FROM planificacion.RefuerzoPedido
        WHERE CampanaID = :c AND Dia < :hoy AND Estado = 'pedido'
    """), {"c": campana_id, "hoy": hoy}).mappings().all()

    if not pendientes:
        return 0

    actualizaciones: List[Tuple[int, str, float]] = []

    for p in pendientes:
        pid = p["RefuerzoPedidoID"]
        pool_id = p["PoolID"]
        dia = p["Dia"]
        desde = p["Desde"]
        hasta = p["Hasta"]

        try:
            # 1. Faltante por intervalo, de la ÚLTIMA corrida que cubría el bloque.
            #    No de la vigente: la vigente arranca hoy y un pedido vencido es de
            #    un día que ya no contiene. Y de UNA sola corrida: leer todas las
            #    que lo cubren mezcla faltantes de planes distintos.
            req_filas = conn_read.execute(text("""
                SELECT r.Intervalo, r.Brecha
                FROM planificacion.Requerimiento r
                WHERE r.PoolID = :p
                  AND r.Intervalo >= :desde AND r.Intervalo < :hasta
                  AND r.CorridaID = (
                      SELECT MAX(r2.CorridaID)
                      FROM planificacion.Requerimiento r2
                      JOIN planificacion.Corrida c2 ON c2.CorridaID = r2.CorridaID
                      WHERE c2.CampanaID = :c AND c2.Estado = 'OK'
                        AND r2.PoolID = :p
                        AND r2.Intervalo >= :desde AND r2.Intervalo < :hasta)
                ORDER BY r.Intervalo
            """), {"c": campana_id, "p": pool_id, "desde": desde, "hasta": hasta}).mappings().all()

            faltantes: Dict[datetime, int] = {}
            for r in req_filas:
                brecha = r["Brecha"]
                if brecha is not None and brecha < 0:
                    faltantes[r["Intervalo"]] = -int(brecha)

            # 2. Personas por intervalo con turno en el registro real (dbo.payroll)
            registro = pdatos.dotacion_real(
                conn_read, pool_id, dia, dia + timedelta(days=1), intervalo_min
            )

            # 3. Personas en la malla publicada (dbo.payroll_futuro)
            malla = pdatos.dotacion_planificada(
                conn_read, pool_id, dia, dia + timedelta(days=1), intervalo_min
            )

            # 4. Evaluación pura
            resultado = evaluar_cobertura(dict(p), faltantes, registro, malla, intervalo_min)
            actualizaciones.append((pid, resultado["estado"], resultado["horas_cubiertas"]))
        except Exception as e:
            logger.warning(f"Error evaluando cobertura perezosa para pedido {pid}: {e}")
            continue

    if not actualizaciones:
        return 0

    # 5. Escritura en transacción propia, separada de la lectura
    if hasattr(engine_o_conn_write, "begin"):
        with engine_o_conn_write.begin() as conn_tx:
            _ejecutar_actualizaciones_cierre(conn_tx, campana_id, actualizaciones)
    else:
        _ejecutar_actualizaciones_cierre(engine_o_conn_write, campana_id, actualizaciones)

    return len(actualizaciones)


def _ejecutar_actualizaciones_cierre(
    conn: Connection,
    campana_id: int,
    actualizaciones: Sequence[Tuple[int, str, float]],
) -> None:
    for pid, estado, horas_cubiertas in actualizaciones:
        conn.execute(text("""
            UPDATE planificacion.RefuerzoPedido
            SET Estado = :estado,
                HorasCubiertas = :horas_cubiertas,
                ActualizadoEn = SYSUTCDATETIME()
            WHERE RefuerzoPedidoID = :id AND CampanaID = :c
        """), {"id": pid, "c": campana_id, "estado": estado, "horas_cubiertas": horas_cubiertas})
