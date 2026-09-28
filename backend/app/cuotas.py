"""Cupo mensual de auditorías por campaña.

Una campaña tiene una BOLSA mensual de auditorías (calidad.CuotaCampana) que
comparten todos los usuarios alcanzados, más un sublímite opcional por usuario.
Cada pedido deja una fila en calidad.CuotaConsumo: reserva lo pedido al enviarse
y se ajusta a lo realmente auditado cuando la corrida cierra.

Reglas (acordadas 2026-08-31, ver scripts/migrations/2026-08-31_cuotas_auditoria.sql):

- **A quién limita**: a todos salvo super admin y `audit:cuota_exento` (Calidad).
  El exento ni consume ni se le bloquea nada.
- **Período**: mes calendario en hora Argentina. El mes del cupo tiene que ser el
  que el usuario ve en pantalla; con UTC el cupo se renovaría a las 21:00 del
  último día del mes.
- **Qué cuenta 1**: un llamado auditado y un llamado enviado a transcribir a
  demanda (los dos se pagan por llamado en Gemini).
- **Reserva y ajuste**: al enviar se descuenta lo pedido (si no, se pueden lanzar
  diez lotes de 100 antes de que cierre el primero, porque un batch tarda horas
  en cerrar su fila de log) y al cerrar la corrida se ajusta a
  `AuditExecutionLog.filas_auditadas`. El ajuste lo hace `conciliar()`.
- **Al excederse**: se bloquea el pedido entero informando el saldo. No se
  recorta al saldo: cuánto auditar de menos lo decide el usuario.
- **Sin cupo configurado = sin tope, pero se mide igual**: una campaña sin fila
  (o con Activo = 0, o LimiteMensual NULL) registra el consumo sin bloquear a
  nadie. Así el deploy no cambia nada para nadie y el gerente elige el primer
  cupo mirando el consumo real.

Todo lo que toca la BD es best-effort en el mismo sentido que
`AuditorIA/execution_log.py`: si la migración no está aplicada o la escritura
falla, se loguea y la auditoría sigue. La ÚNICA excepción es el bloqueo por cupo
agotado (`CuotaExcedidaError`), que sí tiene que cortar.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from app.models import User
from AuditorIA.execution_log import calcular_costo_usd, obtener_tarifa, select_cached_tokens
from AuditorIA.modelos_ia import MODELO_IA_DEFAULT

logger = logging.getLogger(__name__)

PERMISO_GESTION = "audit:cuotas"          # gerente de operaciones: ver/editar cupos
PERMISO_EXENTO = "audit:cuota_exento"     # fuera del sistema de cupos (Calidad)

TIPO_AUDITORIA = "auditoria"
TIPO_TRANSCRIPCION = "transcripcion"

ESTADO_RESERVADO = "RESERVADO"
ESTADO_CONFIRMADO = "CONFIRMADO"
ESTADO_LIBERADO = "LIBERADO"

# Zona horaria de Argentina (UTC-3, sin DST), igual que routers/uso_ia.py.
_TZ_ART = timezone(timedelta(hours=-3))

# Tope duro del muestreo por operador/tipificación (SQL_query.AuditLimitExceededError).
# Con muestreo por grupo, `cantidad` es POR GRUPO y el total real recién se sabe
# con el DataFrame descargado, así que la reserva tiene que ser el techo posible.
LIMITE_MUESTREO_GRUPO = 200

# Ventanas del barrido de conciliación (minutos / horas).
ESPERA_CONCILIACION_MIN = 15   # no tocar una reserva recién creada
# Las subidas por archivo (CSV/Voltara) llegan en tandas que suman sobre la MISMA
# reserva: se espera más antes de cerrarla, o una tanda tardía se encontraría con
# la reserva ya confirmada y no descontaría nada.
ESPERA_GRUPO_MIN = 60
ABANDONO_SIN_LOG_HORAS = 24    # reservó y nunca abrió fila de log -> se libera
ABANDONO_EN_CURSO_HORAS = 48   # lote colgado en Gemini -> se cierra con lo que haya

# Días de historia con los que se calcula el costo promedio por auditoría.
DIAS_COSTO_UNITARIO = 90


class CuotaExcedidaError(Exception):
    """El pedido no entra en el cupo del mes. Lleva el detalle para el mensaje."""

    def __init__(self, mensaje: str, estado: Dict[str, Any]):
        super().__init__(mensaje)
        self.estado = estado


# --------------------------------------------------------------- funciones puras

def mes_actual(momento: Optional[datetime] = None) -> str:
    """'YYYY-MM' del mes en curso en hora Argentina."""
    ahora = momento or datetime.now(_TZ_ART)
    if ahora.tzinfo is None:
        ahora = ahora.replace(tzinfo=_TZ_ART)
    return ahora.astimezone(_TZ_ART).strftime("%Y-%m")


def esta_exento(user: Optional[User]) -> bool:
    """True si el usuario queda fuera del sistema de cupos."""
    if user is None:
        return True
    return bool(user.is_super_admin) or PERMISO_EXENTO in (user.permissions or [])


def puede_gestionar(user: Optional[User]) -> bool:
    if user is None:
        return False
    return bool(user.is_super_admin) or PERMISO_GESTION in (user.permissions or [])


def cantidad_a_reservar(cantidad: Optional[int], *, por_operador: bool = False,
                        por_tipificacion: bool = False) -> int:
    """Cuántas auditorías descontar al ENVIAR el pedido.

    Con muestreo por grupo `cantidad` es "por cada operador/tipificación", así que
    el total puede ser cualquier número hasta el tope duro de 200: se reserva ese
    techo y `conciliar()` lo baja a lo realmente auditado cuando la corrida cierra.
    """
    if por_operador or por_tipificacion:
        return LIMITE_MUESTREO_GRUPO
    return max(int(cantidad or 0), 0)


def calcular_disponible(limite: Optional[int], consumido: int) -> Optional[int]:
    """Saldo restante, o None si no hay tope configurado."""
    if limite is None:
        return None
    return max(limite - consumido, 0)


def entra_en_cupo(estado: Dict[str, Any], cantidad: int) -> Optional[str]:
    """None si el pedido entra; si no, el motivo ('campana' | 'usuario')."""
    disponible = estado.get("disponible")
    if disponible is not None and cantidad > disponible:
        return "campana"
    disponible_usuario = estado.get("disponible_usuario")
    if disponible_usuario is not None and cantidad > disponible_usuario:
        return "usuario"
    return None


def mensaje_cupo_agotado(estado: Dict[str, Any], cantidad: int, motivo: str) -> str:
    """Mensaje para el usuario: qué pidió, qué le queda y a quién pedirle más."""
    if motivo == "usuario":
        cabeza = (f"Tu cupo personal de auditorías de esta campaña para {estado['anio_mes']} "
                  f"no alcanza: pediste {cantidad} y te quedan "
                  f"{estado.get('disponible_usuario')} de {estado.get('limite_usuario')}.")
    else:
        cabeza = (f"El cupo de auditorías de la campaña para {estado['anio_mes']} no alcanza: "
                  f"pediste {cantidad} y quedan {estado.get('disponible')} de "
                  f"{estado.get('limite')} (lo consumen todos los usuarios de la campaña).")
    return (f"{cabeza} Podés auditar menos llamados, esperar al mes que viene o pedirle "
            f"al gerente de operaciones que amplíe el cupo de la campaña.")


# ---------------------------------------------------------------- lectura en BD

def _fila_config(conn, campana_id: int) -> Optional[Dict[str, Any]]:
    """Config de la campaña, o None si no hay fila / la tabla no existe todavía."""
    try:
        row = conn.execute(text("""
            SELECT CampanaID, LimiteMensual, LimiteMensualUsuario, Activo, Nota,
                   ActualizadoPor, ActualizadoEn
            FROM calidad.CuotaCampana WHERE CampanaID = :cid
        """), {"cid": int(campana_id)}).mappings().first()
        return dict(row) if row else None
    except Exception as e:
        logger.warning(f"No se pudo leer calidad.CuotaCampana (¿migración sin aplicar?): {e}")
        return None


def _limites(config: Optional[Dict[str, Any]]) -> tuple[Optional[int], Optional[int]]:
    """(limite_campana, limite_usuario) efectivos: Activo = 0 apaga los dos."""
    if not config or not config.get("Activo"):
        return None, None
    limite = config.get("LimiteMensual")
    limite_usuario = config.get("LimiteMensualUsuario")
    return (int(limite) if limite is not None else None,
            int(limite_usuario) if limite_usuario is not None else None)


# Lo que ocupa cada fila: mientras está en vuelo pesa lo reservado; una vez
# cerrada, lo realmente consumido. Las liberadas no pesan.
_SQL_PESO = """
    SUM(CASE Estado
            WHEN 'RESERVADO'  THEN Reservado
            WHEN 'CONFIRMADO' THEN COALESCE(Consumido, Reservado)
            ELSE 0
        END)
"""


def _consumo(conn, campana_id: int, anio_mes: str, documento: Optional[int] = None) -> int:
    filtro_usuario = " AND Documento = :doc" if documento is not None else ""
    params: Dict[str, Any] = {"cid": int(campana_id), "mes": anio_mes}
    if documento is not None:
        params["doc"] = int(documento)
    try:
        valor = conn.execute(text(f"""
            SELECT {_SQL_PESO} FROM calidad.CuotaConsumo
            WHERE CampanaID = :cid AND AnioMes = :mes{filtro_usuario}
        """), params).scalar()
        return int(valor or 0)
    except Exception as e:
        logger.warning(f"No se pudo leer el consumo de cupo de la campaña {campana_id}: {e}")
        return 0


def estado_cuota(conn, campana_id: int, documento: Optional[int] = None,
                 anio_mes: Optional[str] = None) -> Dict[str, Any]:
    """Cupo, consumo y saldo de una campaña (y del usuario dentro de ella)."""
    anio_mes = anio_mes or mes_actual()
    config = _fila_config(conn, campana_id)
    limite, limite_usuario = _limites(config)
    consumido = _consumo(conn, campana_id, anio_mes)
    consumido_usuario = (_consumo(conn, campana_id, anio_mes, documento)
                         if documento is not None else None)
    return {
        "campana_id": int(campana_id),
        "anio_mes": anio_mes,
        "limite": limite,
        "limite_usuario": limite_usuario,
        "consumido": consumido,
        "consumido_usuario": consumido_usuario,
        "disponible": calcular_disponible(limite, consumido),
        "disponible_usuario": (calcular_disponible(limite_usuario, consumido_usuario or 0)
                               if limite_usuario is not None else None),
        "con_tope": limite is not None or limite_usuario is not None,
        "nota": (config or {}).get("Nota"),
    }


def tiene_tope(conn, campana_id: Optional[int]) -> bool:
    """True si la campaña tiene un cupo activo (con límite cargado)."""
    if campana_id is None:
        return False
    limite, limite_usuario = _limites(_fila_config(conn, campana_id))
    return limite is not None or limite_usuario is not None


# ------------------------------------------------------------ reserva y cierre

def referencia_auditoria(*, task_id: Optional[str] = None,
                         upload_group_id: Optional[str] = None) -> str:
    """Con qué clave se ata la reserva a la corrida.

    Las subidas por archivo (CSV/Voltara) llegan en tandas de 10 audios, cada una
    con su propio task_id pero TODAS con una sola fila de AuditExecutionLog
    (execution_log.continuar_o_iniciar_grupo). Si cada tanda reservara por su
    task_id, solo la primera encontraría su fila al conciliar y el resto se
    liberaría sin descontar nada. Por eso el grupo manda sobre el task_id.
    """
    if upload_group_id:
        return f"grupo:{upload_group_id}"
    return f"task:{task_id}"


def reservar(engine: Engine, *, campana_id: Optional[int], documento: Optional[int],
             cantidad: int, tipo: str, referencia: str,
             anio_mes: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Descuenta `cantidad` del cupo del mes. Lanza CuotaExcedidaError si no entra.

    Devuelve el estado del cupo ya con la reserva aplicada, o None si no había
    nada que reservar (campaña desconocida, cantidad 0) o si la tabla todavía no
    existe — en ese caso NO se bloquea: un cupo que no se puede leer no puede
    negar trabajo.
    """
    if campana_id is None or cantidad <= 0:
        return None
    anio_mes = anio_mes or mes_actual()

    try:
        with engine.begin() as conn:
            # LockOwner='Transaction': se libera al cerrar esta transacción. Serializa
            # el "leer saldo + reservar" entre requests (mismo patrón que
            # execution_log.continuar_o_iniciar_grupo); sin esto, dos envíos
            # simultáneos ven el mismo saldo y los dos entran.
            conn.execute(text("""
                EXEC sp_getapplock @Resource = :res, @LockMode = 'Exclusive',
                                   @LockOwner = 'Transaction', @LockTimeout = 15000
            """), {"res": f"cuota_auditoria:{campana_id}:{anio_mes}"})

            estado = estado_cuota(conn, campana_id, documento, anio_mes)
            motivo = entra_en_cupo(estado, cantidad)
            if motivo:
                raise CuotaExcedidaError(mensaje_cupo_agotado(estado, cantidad, motivo), estado)

            # Acumula sobre la reserva del grupo si ya existe (tandas CSV/Voltara) y
            # mueve ActualizadoEn, que es lo que mira el barrido para saber si la
            # subida todavía está llegando.
            actualizadas = conn.execute(text("""
                UPDATE calidad.CuotaConsumo
                SET Reservado = Reservado + :cant, ActualizadoEn = SYSUTCDATETIME()
                WHERE Referencia = :ref AND Estado = 'RESERVADO'
            """), {"cant": cantidad, "ref": referencia}).rowcount

            if not actualizadas:
                conn.execute(text("""
                    INSERT INTO calidad.CuotaConsumo
                        (CampanaID, AnioMes, Documento, Tipo, Referencia, Reservado, Estado)
                    VALUES (:cid, :mes, :doc, :tipo, :ref, :cant, 'RESERVADO')
                """), {
                    "cid": int(campana_id), "mes": anio_mes,
                    "doc": int(documento) if documento is not None else None,
                    "tipo": tipo, "ref": referencia, "cant": cantidad,
                })

            estado["consumido"] += cantidad
            estado["disponible"] = calcular_disponible(estado["limite"], estado["consumido"])
            if estado["consumido_usuario"] is not None:
                estado["consumido_usuario"] += cantidad
                estado["disponible_usuario"] = calcular_disponible(
                    estado["limite_usuario"], estado["consumido_usuario"])
            return estado
    except CuotaExcedidaError:
        raise
    except Exception as e:
        logger.warning(f"No se pudo reservar cupo de la campaña {campana_id} ({referencia}): {e}")
        return None


def confirmar(engine: Engine, referencia: str, consumido: int,
              motivo: Optional[str] = None) -> None:
    """Cierra la reserva con lo realmente consumido (0 = se libera entera)."""
    estado = ESTADO_CONFIRMADO if consumido > 0 else ESTADO_LIBERADO
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE calidad.CuotaConsumo
                SET Estado = :estado, Consumido = :cant, Motivo = :motivo,
                    CerradoEn = SYSUTCDATETIME(), ActualizadoEn = SYSUTCDATETIME()
                WHERE Referencia = :ref AND Estado = 'RESERVADO'
            """), {"estado": estado, "cant": max(int(consumido), 0),
                   "motivo": motivo, "ref": referencia})
    except Exception as e:
        logger.warning(f"No se pudo cerrar la reserva de cupo {referencia}: {e}")


def liberar(engine: Engine, referencia: str, motivo: str) -> None:
    """Devuelve el cupo reservado (el pedido no auditó nada)."""
    confirmar(engine, referencia, 0, motivo)


def referencia_transcripcion() -> str:
    """Clave de una tanda de transcripciones. No se ata a nada de la cola: se
    reserva antes de encolar (para que el chequeo de cupo sea atómico) y se
    confirma en el acto con lo que realmente entró en la cola, así que no hace
    falta reconciliarla después."""
    return f"transcripcion:{uuid.uuid4()}"


def campana_de_plantilla(conn, plantilla_id: Optional[int]) -> Optional[int]:
    """Campaña a la que se le imputa la corrida. El form de auditoría manda la
    campaña como texto, pero no siempre (las subidas por archivo y algunos flujos
    la omiten): la plantilla siempre está y cuelga de una campaña."""
    if plantilla_id is None:
        return None
    try:
        return conn.execute(
            text("SELECT CampanaID FROM calidad.Plantillas WHERE PlantillaID = :pid"),
            {"pid": int(plantilla_id)},
        ).scalar()
    except Exception as e:
        logger.warning(f"No se pudo resolver la campaña de la plantilla {plantilla_id}: {e}")
        return None


def resolver_campana(conn, campana: Any, plantilla_id: Optional[int] = None) -> Optional[int]:
    """El id de campaña del form si viene numérico; si no, el de la plantilla."""
    if campana is not None and str(campana).strip().isdigit():
        return int(str(campana).strip())
    return campana_de_plantilla(conn, plantilla_id)


def campanas_de_interacciones(conn, ids: List[str]) -> Dict[str, int]:
    """{IdAplicativo: CampanaID} de llamados ya auditados.

    La cola de transcripciones (calidad.TranscripcionJobs) no guarda campaña: se
    resuelve por la auditoría que dejó el llamado, vía su plantilla.
    """
    if not ids:
        return {}
    try:
        filas = conn.execute(text("""
            SELECT DISTINCT a.IdAplicativo, p.CampanaID
            FROM calidad.Auditorias a
            JOIN calidad.Plantillas p ON p.PlantillaID = a.PlantillaID
            WHERE a.IdAplicativo IN :ids
        """).bindparams(bindparam("ids", expanding=True)),
            {"ids": [str(i) for i in ids]}).mappings().all()
        return {str(f["IdAplicativo"]): int(f["CampanaID"]) for f in filas if f["CampanaID"]}
    except Exception as e:
        logger.warning(f"No se pudo resolver la campaña de las interacciones a transcribir: {e}")
        return {}


# ------------------------------------------------------- barrido de conciliación

_SQL_LOG_POR_CLAVE = """
    SELECT COUNT(*)                                              AS lotes,
           SUM(CASE WHEN status = 'EN_CURSO' THEN 1 ELSE 0 END)  AS abiertos,
           SUM(COALESCE(filas_auditadas, 0))                     AS auditadas
    FROM calidad.AuditExecutionLog
    WHERE {columna} = :clave
"""


def _clave_de_referencia(referencia: str) -> tuple[Optional[str], Optional[str]]:
    """('task:abc') -> ('task_id', 'abc'); ('grupo:xyz') -> ('upload_group_id', 'xyz')."""
    if referencia.startswith("task:"):
        return "task_id", referencia[len("task:"):]
    if referencia.startswith("grupo:"):
        return "upload_group_id", referencia[len("grupo:"):]
    return None, None


def _horas(desde: Optional[datetime]) -> float:
    if desde is None:
        return 0.0
    if desde.tzinfo is None:
        desde = desde.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - desde).total_seconds() / 3600


def decidir_conciliacion(*, lotes: int, abiertos: int, auditadas: int,
                         antiguedad_horas: float) -> tuple[str, Optional[int], Optional[str]]:
    """Qué hacer con una reserva, mirando las filas de log de su corrida.

    Devuelve ('esperar'|'confirmar'|'liberar', cantidad, motivo). Separado del
    barrido para poder probar las cuatro situaciones sin BD:
      - sin fila de log todavía -> esperar (el lote puede estar haciendo cola en
        calidad.BatchPendientes) y recién liberar pasado un día;
      - algún lote abierto -> esperar, salvo que lleve 48 h colgado;
      - todo cerrado -> confirmar lo auditado (0 auditadas = liberar).
    """
    if lotes == 0:
        if antiguedad_horas >= ABANDONO_SIN_LOG_HORAS:
            return "liberar", 0, "La corrida nunca llegó a ejecutarse."
        return "esperar", None, None

    if abiertos > 0 and antiguedad_horas < ABANDONO_EN_CURSO_HORAS:
        return "esperar", None, None

    motivo = ("Lote sin cerrar tras 48 h: se cuenta lo auditado hasta acá."
              if abiertos > 0 else None)
    if auditadas <= 0:
        return "liberar", 0, motivo or "La corrida no auditó ningún llamado."
    return "confirmar", auditadas, motivo


def conciliar(engine: Engine, limite_filas: int = 200) -> Dict[str, int]:
    """Ajusta las reservas de auditoría a lo que realmente se auditó.

    Una reserva se cierra cuando TODOS los lotes de su corrida cerraron (en batch
    una corrida son varias filas de log, ver [[audit-execution-log]]); ahí el cupo
    pasa a contar `filas_auditadas` en vez de lo pedido. Las que nunca abrieron
    una fila de log —la corrida se cayó antes de empezar— se liberan pasadas
    ABANDONO_SIN_LOG_HORAS, y las que quedaron con un lote colgado en Gemini se
    cierran con lo que haya pasadas ABANDONO_EN_CURSO_HORAS.

    Corre en el scheduler cada 15 min y también al abrir la pantalla de cupos,
    para que el gerente no mire saldos inflados por reservas ya terminadas.
    Best-effort: cualquier falla se loguea y devuelve lo hecho hasta ahí.
    """
    resultado = {"confirmadas": 0, "liberadas": 0, "en_vuelo": 0}
    try:
        with engine.connect() as conn:
            pendientes = conn.execute(text("""
                SELECT TOP (:tope) ConsumoID, Referencia, Reservado, CreadoEn, ActualizadoEn
                FROM calidad.CuotaConsumo
                WHERE Estado = 'RESERVADO' AND Tipo = 'auditoria'
                  AND ActualizadoEn < DATEADD(
                        minute,
                        -CASE WHEN Referencia LIKE 'grupo:%' THEN :espera_grupo ELSE :espera END,
                        SYSUTCDATETIME())
                ORDER BY ActualizadoEn
            """), {"tope": limite_filas, "espera": ESPERA_CONCILIACION_MIN,
                   "espera_grupo": ESPERA_GRUPO_MIN}).mappings().all()
    except Exception as e:
        logger.warning(f"No se pudo leer las reservas de cupo pendientes: {e}")
        return resultado

    for fila in pendientes:
        referencia = fila["Referencia"]
        columna, clave = _clave_de_referencia(referencia)
        if not columna:
            continue
        try:
            with engine.connect() as conn:
                log = conn.execute(
                    text(_SQL_LOG_POR_CLAVE.format(columna=columna)), {"clave": clave}
                ).mappings().first() or {}
        except Exception as e:
            logger.warning(f"No se pudo conciliar la reserva {referencia}: {e}")
            continue

        accion, cantidad, motivo = decidir_conciliacion(
            lotes=int(log.get("lotes") or 0),
            abiertos=int(log.get("abiertos") or 0),
            auditadas=int(log.get("auditadas") or 0),
            antiguedad_horas=_horas(fila["CreadoEn"]),
        )
        if accion == "esperar":
            resultado["en_vuelo"] += 1
            continue

        confirmar(engine, referencia, cantidad or 0, motivo)
        resultado["confirmadas" if accion == "confirmar" else "liberadas"] += 1

    if any(resultado.values()):
        logger.info("Conciliación de cupos: %s", resultado)
    return resultado



def conciliar_pendientes() -> Dict[str, int]:
    """Entrada del scheduler (run_scheduler.py, cada 15 min): conciliar() con el
    engine global. Import perezoso para no arrastrar la BD al importar el módulo."""
    from app.database import engine as engine_global
    return conciliar(engine_global)

# --------------------------------------------------- costo por auditoría (USD)

def costos_unitarios(conn, dias: int = DIAS_COSTO_UNITARIO) -> Dict[Optional[int], Dict[str, Any]]:
    """{CampanaID: {auditorias, costo_usd, unitario_usd}} de los últimos `dias`.

    Sale de calidad.AuditExecutionLog con la MISMA fórmula que el resto del
    tablero de gastos (tarifa vigente del modelo, thinking a precio de output,
    batch a mitad de precio; ver routers/uso_ia.py::_unitario_por_nivel), así que
    los números son comparables con los de /uso-ia.

    La clave None es el promedio general, que se usa como referencia para las
    campañas que todavía no auditaron nada.
    """
    try:
        filas = conn.execute(text(f"""
            SELECT campana, modelo, modo,
                   SUM(COALESCE(filas_auditadas, 0)) AS auditorias,
                   SUM(COALESCE(input_tokens, 0))    AS input_tokens,
                   SUM(COALESCE(output_tokens, 0))   AS output_tokens,
                   SUM(COALESCE(thoughts_tokens, 0)) AS thoughts_tokens,
                   {select_cached_tokens(conn.engine, agregado=True)}
            FROM calidad.AuditExecutionLog
            WHERE started_at >= DATEADD(day, -:dias, SYSUTCDATETIME())
              AND filas_auditadas > 0
            GROUP BY campana, modelo, modo
        """), {"dias": dias}).mappings().all()
    except Exception as e:
        logger.warning(f"No se pudo calcular el costo por auditoría: {e}")
        return {}

    modelos = {f.get("modelo") or MODELO_IA_DEFAULT for f in filas}
    tarifas = {m: obtener_tarifa(conn.engine, modelo=m) for m in modelos}

    acumulado: Dict[Optional[int], Dict[str, Any]] = {}

    def _acc(clave: Optional[int]) -> Dict[str, Any]:
        return acumulado.setdefault(clave, {"auditorias": 0, "costo_usd": 0.0})

    for f in filas:
        tokens = {c: int(f.get(c) or 0)
                  for c in ("input_tokens", "output_tokens", "thoughts_tokens", "cached_tokens")}
        costo = float(calcular_costo_usd(
            tokens, f.get("modo"), tarifas.get(f.get("modelo") or MODELO_IA_DEFAULT)) or 0)
        auditorias = int(f.get("auditorias") or 0)
        try:
            campana_id: Optional[int] = int(f.get("campana"))
        except (TypeError, ValueError):
            campana_id = None
        for clave in ({campana_id, None} if campana_id is not None else {None}):
            acc = _acc(clave)
            acc["auditorias"] += auditorias
            acc["costo_usd"] += costo

    for acc in acumulado.values():
        acc["costo_usd"] = round(acc["costo_usd"], 4)
        acc["unitario_usd"] = (round(acc["costo_usd"] / acc["auditorias"], 5)
                               if acc["auditorias"] else None)
    return acumulado


# ------------------------------------------------------- resumen para el gerente

def resumen_campanas(conn, anio_mes: Optional[str] = None,
                     empresas_permitidas: Optional[set] = None) -> Dict[str, Any]:
    """Una fila por campaña con cupo, consumo del mes, costo por auditoría y
    gasto máximo posible (cupo x costo unitario). Es la pantalla del gerente."""
    anio_mes = anio_mes or mes_actual()

    campanas = conn.execute(text("""
        SELECT c.CampanaID, c.Nombre AS Campana, e.EmpresaID, e.Nombre AS Empresa
        FROM calidad.Campanas c
        JOIN calidad.Empresas e ON e.EmpresaID = c.EmpresaID
        WHERE c.IsActive = 1 AND e.IsActive = 1
        ORDER BY e.Nombre, c.Nombre
    """)).mappings().all()

    try:
        configs = {int(f["CampanaID"]): dict(f) for f in conn.execute(text("""
            SELECT CampanaID, LimiteMensual, LimiteMensualUsuario, Activo, Nota,
                   ActualizadoPor, ActualizadoEn
            FROM calidad.CuotaCampana
        """)).mappings().all()}
    except Exception as e:
        logger.warning(f"No se pudo leer los cupos configurados: {e}")
        configs = {}

    try:
        consumos = {int(f["CampanaID"]): dict(f) for f in conn.execute(text(f"""
            SELECT CampanaID,
                   {_SQL_PESO}                                                AS Consumido,
                   SUM(CASE WHEN Tipo = 'transcripcion'
                            THEN CASE Estado
                                     WHEN 'RESERVADO'  THEN Reservado
                                     WHEN 'CONFIRMADO' THEN COALESCE(Consumido, Reservado)
                                     ELSE 0 END
                            ELSE 0 END)                                       AS Transcripciones,
                   SUM(CASE WHEN Estado = 'RESERVADO' THEN Reservado ELSE 0 END) AS EnVuelo,
                   COUNT(DISTINCT Documento)                                   AS Usuarios
            FROM calidad.CuotaConsumo
            WHERE AnioMes = :mes
            GROUP BY CampanaID
        """), {"mes": anio_mes}).mappings().all()}
    except Exception as e:
        logger.warning(f"No se pudo leer el consumo de cupos del mes: {e}")
        consumos = {}

    costos = costos_unitarios(conn)
    unitario_general = (costos.get(None) or {}).get("unitario_usd")

    filas: List[Dict[str, Any]] = []
    for c in campanas:
        empresa_id = int(c["EmpresaID"])
        if empresas_permitidas is not None and empresa_id not in empresas_permitidas:
            continue
        campana_id = int(c["CampanaID"])
        config = configs.get(campana_id)
        limite, limite_usuario = _limites(config)
        consumo = consumos.get(campana_id, {})
        consumido = int(consumo.get("Consumido") or 0)
        costo = costos.get(campana_id) or {}
        # Con historia propia se usa el costo real de la campaña; sin ella, el
        # promedio general (una campaña nueva no tiene con qué proyectar).
        unitario = costo.get("unitario_usd") or unitario_general
        filas.append({
            "campana_id": campana_id,
            "campana": c["Campana"],
            "empresa_id": empresa_id,
            "empresa": c["Empresa"],
            "limite": limite,
            "limite_usuario": limite_usuario,
            "activo": bool(config["Activo"]) if config else False,
            "nota": (config or {}).get("Nota"),
            "actualizado_en": (config or {}).get("ActualizadoEn"),
            "consumido": consumido,
            "transcripciones": int(consumo.get("Transcripciones") or 0),
            "en_vuelo": int(consumo.get("EnVuelo") or 0),
            "usuarios": int(consumo.get("Usuarios") or 0),
            "disponible": calcular_disponible(limite, consumido),
            "costo_unitario_usd": unitario,
            "costo_unitario_propio": costo.get("unitario_usd") is not None,
            "auditorias_historicas": int(costo.get("auditorias") or 0),
            # Lo que importa para el gerente: cuánto puede llegar a gastar esta
            # campaña en el mes si consume el cupo entero.
            "gasto_maximo_usd": round(limite * unitario, 2) if (limite and unitario) else None,
            "gasto_consumido_usd": round(consumido * unitario, 2) if unitario else None,
        })

    con_tope = [f for f in filas if f["limite"] is not None]
    return {
        "anio_mes": anio_mes,
        "campanas": filas,
        "costo_unitario_general_usd": unitario_general,
        "totales": {
            "campanas_con_cupo": len(con_tope),
            "cupo_total": sum(f["limite"] for f in con_tope) or 0,
            "consumido_total": sum(f["consumido"] for f in filas),
            "gasto_maximo_usd": round(sum(f["gasto_maximo_usd"] or 0 for f in con_tope), 2),
            "gasto_consumido_usd": round(sum(f["gasto_consumido_usd"] or 0 for f in filas), 2),
        },
    }


def consumo_por_usuario(conn, campana_id: int, anio_mes: Optional[str] = None) -> List[Dict[str, Any]]:
    """Quién consumió el cupo de una campaña en el mes (detalle de la pantalla)."""
    anio_mes = anio_mes or mes_actual()
    try:
        filas = conn.execute(text(f"""
            SELECT Documento,
                   {_SQL_PESO} AS Consumido,
                   SUM(CASE WHEN Tipo = 'transcripcion'
                            THEN CASE Estado
                                     WHEN 'RESERVADO'  THEN Reservado
                                     WHEN 'CONFIRMADO' THEN COALESCE(Consumido, Reservado)
                                     ELSE 0 END
                            ELSE 0 END) AS Transcripciones,
                   COUNT(*)             AS Pedidos,
                   MAX(ActualizadoEn)   AS Ultimo
            FROM calidad.CuotaConsumo
            WHERE CampanaID = :cid AND AnioMes = :mes
            GROUP BY Documento
            ORDER BY 2 DESC
        """), {"cid": int(campana_id), "mes": anio_mes}).mappings().all()
    except Exception as e:
        logger.warning(f"No se pudo leer el consumo por usuario de la campaña {campana_id}: {e}")
        return []
    return [{"documento": f["Documento"], "consumido": int(f["Consumido"] or 0),
             "transcripciones": int(f["Transcripciones"] or 0),
             "pedidos": int(f["Pedidos"] or 0), "ultimo": f["Ultimo"]} for f in filas]


def guardar_cupo(engine: Engine, *, campana_id: int, limite: Optional[int],
                 limite_usuario: Optional[int], activo: bool, nota: Optional[str],
                 documento: Optional[int]) -> None:
    """Alta/edición del cupo de una campaña (pantalla del gerente)."""
    with engine.begin() as conn:
        conn.execute(text("""
            MERGE calidad.CuotaCampana AS dst
            USING (SELECT :cid AS CampanaID) AS src ON dst.CampanaID = src.CampanaID
            WHEN MATCHED THEN UPDATE SET
                LimiteMensual = :limite, LimiteMensualUsuario = :limite_usuario,
                Activo = :activo, Nota = :nota,
                ActualizadoPor = :doc, ActualizadoEn = SYSUTCDATETIME()
            WHEN NOT MATCHED THEN
                INSERT (CampanaID, LimiteMensual, LimiteMensualUsuario, Activo, Nota, ActualizadoPor)
                VALUES (:cid, :limite, :limite_usuario, :activo, :nota, :doc);
        """), {"cid": int(campana_id), "limite": limite, "limite_usuario": limite_usuario,
               "activo": 1 if activo else 0, "nota": nota, "doc": documento})
