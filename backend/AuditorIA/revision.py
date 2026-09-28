"""Revisión humana de una auditoría: la verdad contra la que se mide la IA.

Un analista de Calidad abre una auditoría ya hecha, ve atributo por atributo lo
que respondió la IA y deja su veredicto. Eso se guarda en
calidad.AuditoriaRevisiones (cabecera, el "revisé esta auditoría entera") +
calidad.AuditoriaRevisionDetalles (un veredicto por atributo).

DOS DECISIONES QUE IMPORTAN
---------------------------
1. La respuesta de la IA NO se pisa. calidad.AuditoriaDetalles queda intacta y la
   corrección vive aparte. Si se corrigiera en el lugar, se perdería exactamente
   el dato que se quiere medir (qué contestó la IA) y la auditoría publicada
   cambiaría de valor a espaldas del operador.

2. Se guardan también los ACUERDOS, no solo las correcciones. Sin las
   coincidencias no hay denominador: se sabría cuántas veces se equivocó pero no
   sobre cuántos casos, y no habría accuracy ni kappa posibles.

Ver AuditorIA/golden_metricas.py (métricas) y AuditorIA/golden_set.py (sets).
Migración: scripts/migrations/2026-08-06c_golden_set_fase0.sql.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.mssql import NVARCHAR
from sqlalchemy.engine import Engine

from AuditorIA.golden_metricas import coinciden, es_revisable

logger = logging.getLogger(__name__)


def _tiene_columna(conn, tabla: str, columna: str, esquema: str = "calidad") -> bool:
    """¿Existe la columna? Se chequea antes de leerla en vez de atrapar el error:
    un statement fallido dentro de la transacción dejaría la operación a medias
    (mismo criterio que plantillas_manager._tiene_columna_es_opcional)."""
    try:
        return conn.execute(
            text("""SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = :e AND TABLE_NAME = :t AND COLUMN_NAME = :c"""),
            {"e": esquema, "t": tabla, "c": columna},
        ).first() is not None
    except Exception:
        return False


def _parsear_restricciones(valor: Any) -> Optional[Dict[str, Any]]:
    """Restricciones del atributo (el {"enum": [...]} que la UI usa para pintar las
    opciones). Se guardan como texto JSON; si vienen rotas se ignoran en vez de
    romper la pantalla."""
    if not valor:
        return None
    if isinstance(valor, dict):
        return valor
    try:
        parseado = json.loads(valor)
        return parseado if isinstance(parseado, dict) else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Lectura                                                                      #
# --------------------------------------------------------------------------- #
def obtener_para_revision(
    engine: Engine, auditoria_id: int, revisor_id: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """Todo lo que necesita la pantalla de revisión de UNA auditoría.

    Devuelve la auditoría (contexto + puntaje), la lista de atributos ACTIVOS de
    su plantilla con lo que respondió la IA, y la revisión previa de `revisor_id`
    si ya existe (para poder editarla). None si la auditoría no existe.

    Se listan los atributos de la plantilla, no las filas de AuditoriaDetalles:
    un atributo opcional que la IA omitió no tiene detalle (ver
    sql_a_Claude.auditoria_a_SQL) y es justamente uno de los casos que hay que
    poder corregir — el revisor tiene que ver el hueco.
    """
    with engine.connect() as conn:
        auditoria = conn.execute(text("""
            SELECT TOP 1 a.AuditoriaID, a.IdAplicativo, a.PlantillaID, a.CampanaID, a.EmpresaID,
                   a.operadorUsuario, a.fecha_interaccion, a.duracion_segundos,
                   a.tipificacion_interaccion, a.PuntajeFinal, a.EsErrorCritico,
                   p.Nombre AS NombrePlantilla,
                   ag.Nombre + ', ' + ag.Apellido AS Agente,
                   ag.LegajoNum AS Legajo,
                   e.nombre + ', ' + e.apellido AS Equipo
            FROM calidad.Auditorias a
            LEFT JOIN calidad.Plantillas p ON p.PlantillaID = a.PlantillaID
            OUTER APPLY (
                SELECT TOP 1 z.Nombre, z.Apellido, z.LegajoNum, z.EquipoID
                FROM (
                    SELECT n.nombre AS Nombre, n.apellido AS Apellido, n.legajo AS LegajoNum,
                           op.equipo_id AS EquipoID, 0 AS Prioridad
                    FROM nomina n
                    OUTER APPLY (
                        SELECT TOP 1 o.equipo_id
                        FROM operadores o
                        WHERE o.legajo_id = n.id
                          AND o.estado = 1
                          AND a.[fecha_interaccion] >= o.fecha_desde
                          AND (a.[fecha_interaccion] < o.fecha_hasta OR o.fecha_hasta IS NULL)
                        ORDER BY o.fecha_desde DESC
                    ) op
                    WHERE n.id = a.OperadorNominaID

                    UNION ALL

                    SELECT r.Nombre, r.Apellido, r.Legajo, r.EquipoID, 1
                    FROM calidad.fn_ResolverOperadorAuditoria(
                             a.operadorUsuario, a.EmpresaID, a.[fecha_interaccion]) r
                    WHERE a.OperadorNominaID IS NULL AND r.Criterio = 1
                ) z
                ORDER BY z.Prioridad
            ) ag
            LEFT JOIN equipos e ON e.id = ag.EquipoID
            WHERE a.AuditoriaID = :aid
        """), {"aid": auditoria_id}).mappings().first()

        if auditoria is None:
            return None

        tiene_opcional = _tiene_columna(conn, "Atributos", "EsOpcional")
        col_opcional = "at.EsOpcional" if tiene_opcional else "CAST(0 AS BIT)"

        filas = conn.execute(text(f"""
            SELECT at.AtributoID, at.NombreAtributo, at.TipoDato, at.Restricciones,
                   at.Orden, at.Ponderacion, {col_opcional} AS EsOpcional,
                   ad.ValorResultado
            FROM calidad.Atributos at
            LEFT JOIN calidad.AuditoriaDetalles ad
                   ON ad.AtributoID = at.AtributoID AND ad.AuditoriaID = :aid
            WHERE at.PlantillaID = :pid AND at.IsActive = 1
            ORDER BY at.Orden, at.AtributoID
        """), {"aid": auditoria_id, "pid": auditoria["PlantillaID"]}).mappings().all()

        revision = None
        detalles_previos: Dict[int, Dict[str, Any]] = {}
        if revisor_id is not None:
            revision = conn.execute(text("""
                SELECT TOP 1 RevisionID, Comentario, FechaRevision, FechaModificacion
                FROM calidad.AuditoriaRevisiones
                WHERE AuditoriaID = :aid AND RevisorUsuarioID = :uid
            """), {"aid": auditoria_id, "uid": revisor_id}).mappings().first()

            if revision:
                previos = conn.execute(text("""
                    SELECT AtributoID, ValorHumano, Coincide, Motivo
                    FROM calidad.AuditoriaRevisionDetalles
                    WHERE RevisionID = :rid
                """), {"rid": revision["RevisionID"]}).mappings().all()
                detalles_previos = {int(d["AtributoID"]): dict(d) for d in previos}

        # Cuántos revisaron esta auditoría (además del actual): habilita el
        # cálculo de acuerdo entre humanos y avisa en la UI que ya la miró otro.
        otros = conn.execute(text("""
            SELECT COUNT(*) FROM calidad.AuditoriaRevisiones
            WHERE AuditoriaID = :aid AND (:uid IS NULL OR RevisorUsuarioID <> :uid)
        """), {"aid": auditoria_id, "uid": revisor_id}).scalar() or 0

    atributos = []
    for fila in filas:
        atributo_id = int(fila["AtributoID"])
        previo = detalles_previos.get(atributo_id)
        atributos.append({
            "atributo_id": atributo_id,
            "nombre": fila["NombreAtributo"],
            "tipo": fila["TipoDato"],
            "restricciones": _parsear_restricciones(fila["Restricciones"]),
            "orden": fila["Orden"],
            "ponderacion": float(fila["Ponderacion"] or 0),
            "es_opcional": bool(fila["EsOpcional"]),
            # False en los atributos de texto libre: se muestran (dan contexto para
            # juzgar el resto del llamado) pero no se pueden corregir. Ver
            # golden_metricas.TIPOS_NO_REVISABLES.
            "revisable": es_revisable(fila["TipoDato"]),
            "valor_ia": fila["ValorResultado"],
            "valor_humano": previo["ValorHumano"] if previo else None,
            "motivo": previo["Motivo"] if previo else None,
            # Sin revisión previa, la UI arranca asumiendo que la IA acertó: en
            # una plantilla de 12 atributos el revisor toca 1 o 2, y obligarlo a
            # confirmar los 12 haría que nadie revise nada.
            "revisado": previo is not None,
            "coincide_previo": bool(previo["Coincide"]) if previo else None,
        })

    return {
        "auditoria": dict(auditoria),
        "atributos": atributos,
        "revision": dict(revision) if revision else None,
        "otros_revisores": int(otros),
    }


def ids_revisados(
    engine: Engine, auditoria_ids: Iterable, revisor_id: Optional[int] = None
) -> set:
    """Subconjunto de auditorías que YA tienen revisión (de `revisor_id` o de
    cualquiera). Sirve para marcar en el listado lo que ya se revisó y no
    revisarlo dos veces. Consulta por lotes (tope de params de SQL Server)."""
    ids = list(dict.fromkeys(int(i) for i in auditoria_ids if i is not None))
    revisados: set = set()
    if not ids:
        return revisados

    filtro_revisor = "AND RevisorUsuarioID = :uid" if revisor_id is not None else ""
    try:
        with engine.connect() as conn:
            for inicio in range(0, len(ids), 1000):
                lote = ids[inicio:inicio + 1000]
                query = text(f"""
                    SELECT DISTINCT AuditoriaID
                    FROM calidad.AuditoriaRevisiones
                    WHERE AuditoriaID IN :ids {filtro_revisor}
                """).bindparams(bindparam("ids", expanding=True))
                params: Dict[str, Any] = {"ids": lote}
                if revisor_id is not None:
                    params["uid"] = revisor_id
                revisados.update(int(f[0]) for f in conn.execute(query, params).fetchall())
    except Exception as e:
        logger.warning("No se pudo consultar ids_revisados: %s", e)
    return revisados


# --------------------------------------------------------------------------- #
# Escritura                                                                    #
# --------------------------------------------------------------------------- #
def _version_de_la_auditoria(engine: Engine, auditoria_id: int) -> Optional[int]:
    """Versión ACTUAL de la plantilla de esa auditoría (Golden Set — Fase 2).

    Es la versión con la que el revisor está viendo los criterios ahora mismo, que
    no tiene por qué ser aquella con la que se auditó el llamado meses atrás: lo
    que se quiere trazar acá es contra qué definición sentó criterio el humano.
    Best-effort: sin migración de versionado, None y la revisión se guarda igual.
    """
    try:
        from AuditorIA.versionado import obtener_o_crear_version

        with engine.connect() as conn:
            fila = conn.execute(text("""
                SELECT TOP 1 PlantillaID FROM calidad.Auditorias WHERE AuditoriaID = :aid
            """), {"aid": auditoria_id}).first()
        if fila is None or fila[0] is None:
            return None
        return obtener_o_crear_version(engine, int(fila[0]))
    except Exception as e:
        logger.warning("No se pudo resolver la versión de plantilla al revisar: %s", e)
        return None



def guardar_revision(
    engine: Engine,
    *,
    auditoria_id: int,
    revisor_id: int,
    veredictos: List[Dict[str, Any]],
    comentario: Optional[str] = None,
) -> Dict[str, Any]:
    """Guarda (o pisa) la revisión de un usuario sobre una auditoría.

    `veredictos`: [{atributo_id, valor_humano, motivo}]. `valor_humano` en None
    significa "este atributo no correspondía responderlo" — no es lo mismo que no
    mandarlo: lo que no viene en la lista, no se revisó y no se guarda.

    El ValorIA se lee acá, del detalle guardado, y no se acepta del cliente: es
    la mitad de la comparación y tiene que salir de la BD, no del navegador.

    Devuelve {revision_id, atributos_revisados, correcciones}.
    """
    if not veredictos:
        raise ValueError("La revisión no tiene ningún atributo revisado.")

    pedidos = {}
    for v in veredictos:
        try:
            pedidos[int(v["atributo_id"])] = v
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"Veredicto sin atributo_id válido: {v!r}")

    # Se resuelve ANTES de abrir la transacción de la revisión: obtener_o_crear_version
    # abre la suya y toma un applock, y anidarlas es pedir un deadlock. Devuelve None
    # si la migración de versionado no está aplicada; en ese caso la revisión se
    # guarda igual, sin versión.
    version_id = _version_de_la_auditoria(engine, auditoria_id)

    with engine.begin() as conn:
        # .first() en las lecturas: cierra el cursor al leer la fila. Sin MARS el
        # driver ODBC rechaza la sentencia siguiente si el result set quedó abierto.
        auditoria = conn.execute(text("""
            SELECT TOP 1 PlantillaID, IdAplicativo FROM calidad.Auditorias
            WHERE AuditoriaID = :aid
        """), {"aid": auditoria_id}).first()
        if auditoria is None:
            raise ValueError(f"No existe la auditoría {auditoria_id}.")

        # Tipo y valor de la IA de cada atributo pedido. El tipo hace falta para
        # normalizar la comparación (OK/ok, listas, N/A) con el mismo criterio
        # que después usan las métricas.
        filas = conn.execute(text("""
            SELECT at.AtributoID, at.TipoDato, ad.ValorResultado
            FROM calidad.Atributos at
            LEFT JOIN calidad.AuditoriaDetalles ad
                   ON ad.AtributoID = at.AtributoID AND ad.AuditoriaID = :aid
            WHERE at.PlantillaID = :pid AND at.IsActive = 1
        """), {"aid": auditoria_id, "pid": auditoria.PlantillaID}).fetchall()
        contexto = {int(f[0]): {"tipo": f[1], "valor_ia": f[2]} for f in filas}

        desconocidos = set(pedidos) - set(contexto)
        if desconocidos:
            raise ValueError(
                f"Atributos ajenos a la plantilla de esta auditoría: {sorted(desconocidos)}"
            )

        # Texto libre: se rechaza el veredicto en vez de guardarlo (ver
        # golden_metricas.TIPOS_NO_REVISABLES). Un resumen o un feedback no tienen
        # una única redacción correcta, así que compararlos por igualdad daría
        # desacuerdo siempre y hundiría el kappa sin que nadie se haya equivocado.
        # La pantalla ya no los ofrece; esto ataja al cliente viejo y a la API.
        no_revisables = sorted(
            aid for aid in pedidos if not es_revisable(contexto[aid]["tipo"])
        )
        if no_revisables:
            raise ValueError(
                "Los atributos de texto libre no se revisan porque no tienen una "
                f"respuesta correcta única (atributos {no_revisables})."
            )

        revision = conn.execute(text("""
            SELECT TOP 1 RevisionID FROM calidad.AuditoriaRevisiones
            WHERE AuditoriaID = :aid AND RevisorUsuarioID = :uid
        """), {"aid": auditoria_id, "uid": revisor_id}).first()

        if revision:
            revision_id = int(revision[0])
            # La versión se refresca al re-revisar: si el criterio de la plantilla
            # cambió desde la revisión anterior, esta opinión es sobre el criterio
            # NUEVO y hay que registrarla como tal.
            set_version = ", PlantillaVersionID = :vid" if version_id is not None else ""
            conn.execute(text(f"""
                UPDATE calidad.AuditoriaRevisiones
                SET Comentario = :com, FechaModificacion = SYSUTCDATETIME(){set_version}
                WHERE RevisionID = :rid
            """), {
                "com": comentario, "rid": revision_id,
                **({"vid": version_id} if version_id is not None else {}),
            })
            # Reemplazo completo: la revisión que llega es el estado final, y
            # dejar veredictos viejos de atributos que el revisor sacó de la
            # lista mezclaría dos opiniones distintas en una sola revisión.
            conn.execute(text("""
                DELETE FROM calidad.AuditoriaRevisionDetalles WHERE RevisionID = :rid
            """), {"rid": revision_id})
        else:
            # Con qué versión de la plantilla el humano sentó criterio (Fase 2).
            # Es lo que después permite decir "esta verdad se estableció con la v2
            # y hoy vamos por la v4, que cambió justo ese atributo": el set no se
            # vence entero, se vencen los atributos que cambiaron.
            columna_version = ", PlantillaVersionID" if version_id is not None else ""
            valor_version = ", :vid" if version_id is not None else ""
            revision_id = int(conn.execute(text(f"""
                INSERT INTO calidad.AuditoriaRevisiones
                    (AuditoriaID, RevisorUsuarioID, PlantillaID, IdAplicativo, Comentario{columna_version})
                OUTPUT inserted.RevisionID
                VALUES (:aid, :uid, :pid, :idapp, :com{valor_version})
            """), {
                "aid": auditoria_id,
                "uid": revisor_id,
                "pid": auditoria.PlantillaID,
                "idapp": auditoria.IdAplicativo,
                "com": comentario,
                **({"vid": version_id} if version_id is not None else {}),
            }).scalar())

        correcciones = 0
        for atributo_id, pedido in pedidos.items():
            info = contexto[atributo_id]
            valor_humano = pedido.get("valor_humano")
            if isinstance(valor_humano, (list, tuple)):
                valor_humano = json.dumps(list(valor_humano), ensure_ascii=False)
            elif valor_humano is not None and not isinstance(valor_humano, str):
                valor_humano = str(valor_humano)

            acierta = coinciden(info["valor_ia"], valor_humano, info["tipo"])
            if not acierta:
                correcciones += 1

            conn.execute(text("""
                INSERT INTO calidad.AuditoriaRevisionDetalles
                    (RevisionID, AtributoID, ValorIA, ValorHumano, Coincide, Motivo)
                VALUES (:rid, :atid, :via, :vh, :coincide, :motivo)
            """).bindparams(
                # Sin tipar, pyodbc declara el parámetro como nvarchar(4000) y
                # trunca los valores largos (mismo problema que response_thoughts).
                bindparam("via", type_=NVARCHAR(None)),
                bindparam("vh", type_=NVARCHAR(None)),
            ), {
                "rid": revision_id,
                "atid": atributo_id,
                "via": info["valor_ia"],
                "vh": valor_humano,
                "coincide": 1 if acierta else 0,
                "motivo": (pedido.get("motivo") or None),
            })

    return {
        "revision_id": revision_id,
        "atributos_revisados": len(pedidos),
        "correcciones": correcciones,
    }


def borrar_revision(engine: Engine, *, auditoria_id: int, revisor_id: int) -> bool:
    """Borra la revisión de un usuario (los detalles caen por ON DELETE CASCADE)."""
    with engine.begin() as conn:
        resultado = conn.execute(text("""
            DELETE FROM calidad.AuditoriaRevisiones
            WHERE AuditoriaID = :aid AND RevisorUsuarioID = :uid
        """), {"aid": auditoria_id, "uid": revisor_id})
    return bool(resultado.rowcount)
