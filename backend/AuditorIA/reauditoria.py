"""Volver a auditar un llamado con el prompt vigente, sin perder la corrida anterior.

EL PROBLEMA
-----------
Cuando se cambia el prompt de un atributo, las auditorías ya hechas siguen
guardando lo que contestó la versión vieja. Medir el prompt nuevo contra esas
respuestas da un resultado falso: el cambio no aparece por ningún lado. Hay que
volver a pasar la IA sobre el MISMO audio (por eso los audios del Golden Set se
fijan) — pero tirar la corrida anterior tampoco sirve: es la evidencia de qué se
le informó al operador ese día.

LA DECISIÓN QUE ORDENA TODO
---------------------------
`calidad.Auditorias` sigue teniendo UNA fila por llamado, con el MISMO
AuditoriaID, y siempre contiene la ÚLTIMA versión. Lo anterior se archiva en
`calidad.AuditoriaVersiones` (+ `AuditoriaVersionDetalles`).

Por qué así y no insertando una fila nueva por corrida:
  - el SP del listado, los dashboards, los exports a Sheets, el puntaje del
    operador y los reportes no se tocan y NUNCA ven duplicados. "Traer solo la
    última versión" no es un filtro que alguien se pueda olvidar de poner: es la
    única fila que existe;
  - las revisiones humanas, los items de Golden Set y el audio conservado siguen
    apuntando al mismo AuditoriaID, así que el veredicto humano no se pierde ni
    hay que reindexar nada.

QUÉ PASA CON LA REVISIÓN HUMANA
-------------------------------
No se toca. `AuditoriaRevisionDetalles.ValorIA` queda como estaba: es el registro
fiel de qué vio la persona cuando revisó. Lo que cambia es contra qué se compara
al medir — `golden_set.cargar_casos` detecta que hubo una reauditoría posterior a
la revisión y usa la respuesta NUEVA, que es justamente el punto del ejercicio.

Migración: scripts/migrations/2026-08-27_reauditoria_versionado.sql
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.mssql import NVARCHAR
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Columnas que NO entran al SnapshotJSON: o son la clave, o ya van tipadas aparte.
_FUERA_DEL_SNAPSHOT = {"AuditoriaID", "PuntajeFinal", "EsErrorCritico", "FechaAuditoria"}


def _existe_tabla(conn, nombre: str, esquema: str = "calidad") -> bool:
    try:
        return conn.execute(
            text("""SELECT 1 FROM INFORMATION_SCHEMA.TABLES
                    WHERE TABLE_SCHEMA = :e AND TABLE_NAME = :t"""),
            {"e": esquema, "t": nombre},
        ).first() is not None
    except Exception:
        return False


def hay_versionado(engine: Engine) -> bool:
    """¿Está aplicada la migración? Sin ella no se reaudita: reemplazar una
    auditoría sin poder archivar la anterior sería destruir evidencia."""
    try:
        with engine.connect() as conn:
            return _existe_tabla(conn, "AuditoriaVersiones")
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Archivado                                                                    #
# --------------------------------------------------------------------------- #
def _serializable(valor: Any) -> Any:
    if valor is None or isinstance(valor, (str, int, float, bool)):
        return valor
    if isinstance(valor, bytes):
        return valor.decode("utf-8", "replace")
    return str(valor)


def archivar_version(
    conn,
    auditoria_id: int,
    *,
    usuario_id: Optional[int] = None,
    motivo: Optional[str] = None,
) -> Optional[int]:
    """Copia el estado ACTUAL de una auditoría a la tabla de versiones.

    Se llama DENTRO de la transacción que va a reemplazar la auditoría: si el
    archivado falla, la corrida nueva no se guarda y no se pierde nada. Devuelve
    el VersionAuditoriaID, o None si la migración no está aplicada.
    """
    if not _existe_tabla(conn, "AuditoriaVersiones"):
        return None

    fila = conn.execute(
        text("SELECT * FROM calidad.Auditorias WHERE AuditoriaID = :aid"),
        {"aid": auditoria_id},
    ).mappings().first()
    if fila is None:
        return None
    fila = dict(fila)

    # El resto de la fila va tal cual: `calidad.Auditorias` gana columnas cada
    # tantas migraciones y duplicar su DDL en el archivo lo dejaría incompleto en
    # silencio la próxima vez.
    snapshot = {
        k: _serializable(v) for k, v in fila.items() if k not in _FUERA_DEL_SNAPSHOT
    }

    numero = int(conn.execute(
        text("SELECT COUNT(*) FROM calidad.AuditoriaVersiones WHERE AuditoriaID = :aid"),
        {"aid": auditoria_id},
    ).scalar() or 0) + 1

    version_id = conn.execute(
        text("""
            INSERT INTO calidad.AuditoriaVersiones
                (AuditoriaID, Numero, PlantillaVersionID, PuntajeFinal, EsErrorCritico,
                 FechaAuditoria, SnapshotJSON, ReauditadaPorUsuarioID, Motivo)
            OUTPUT INSERTED.VersionAuditoriaID
            VALUES (:aid, :numero, :version_plantilla, :puntaje, :ec,
                    :fecha, :snapshot, :uid, :motivo)
        """).bindparams(bindparam("snapshot", type_=NVARCHAR(None))),
        {
            "aid": auditoria_id,
            "numero": numero,
            "version_plantilla": fila.get("PlantillaVersionID"),
            "puntaje": fila.get("PuntajeFinal"),
            "ec": fila.get("EsErrorCritico"),
            "fecha": fila.get("FechaAuditoria"),
            "snapshot": json.dumps(snapshot, ensure_ascii=False, default=str),
            "uid": usuario_id,
            "motivo": (motivo or None),
        },
    ).scalar()

    # Los detalles se copian con un INSERT..SELECT: no hay que traerlos a Python y
    # el valor viaja sin pasar por ninguna conversión de tipos.
    conn.execute(
        text("""
            INSERT INTO calidad.AuditoriaVersionDetalles
                (VersionAuditoriaID, AtributoID, ValorResultado, Orden, PonderacionAplicada)
            SELECT :vid, d.AtributoID, d.ValorResultado, d.Orden, d.PonderacionAplicada
            FROM calidad.AuditoriaDetalles d
            WHERE d.AuditoriaID = :aid
        """),
        {"vid": version_id, "aid": auditoria_id},
    )
    return int(version_id)


def numero_de_version(conn, auditoria_id: int) -> int:
    """En qué número va la corrida VIGENTE (1 = nunca se reauditó)."""
    if not _existe_tabla(conn, "AuditoriaVersiones"):
        return 1
    archivadas = conn.execute(
        text("SELECT COUNT(*) FROM calidad.AuditoriaVersiones WHERE AuditoriaID = :aid"),
        {"aid": auditoria_id},
    ).scalar() or 0
    return int(archivadas) + 1


def versiones_por_auditoria(engine: Engine, auditoria_ids: Iterable[Any]) -> Dict[int, int]:
    """{auditoria_id: cuántas corridas tiene} para marcar el listado. Las que
    nunca se reauditaron no aparecen (implícitamente valen 1)."""
    ids = [int(a) for a in auditoria_ids if a is not None]
    if not ids:
        return {}
    try:
        with engine.connect() as conn:
            if not _existe_tabla(conn, "AuditoriaVersiones"):
                return {}
            filas = conn.execute(text("""
                SELECT AuditoriaID, COUNT(*) AS Archivadas
                FROM calidad.AuditoriaVersiones
                WHERE AuditoriaID IN :ids
                GROUP BY AuditoriaID
            """).bindparams(bindparam("ids", expanding=True)), {"ids": ids}).fetchall()
        return {int(a): int(n) + 1 for a, n in filas}
    except Exception as e:
        logger.warning("No se pudo leer el historial de reauditorías: %s", e)
        return {}


# --------------------------------------------------------------------------- #
# Comparación (pura)                                                           #
# --------------------------------------------------------------------------- #
def comparar_detalles(
    antes: Dict[int, Any],
    despues: Dict[int, Any],
    nombres: Optional[Dict[int, str]] = None,
) -> List[Dict[str, Any]]:
    """Qué cambió entre dos corridas, atributo por atributo.

    Es lo que le da sentido a reauditar: sin el "antes y después" el usuario solo
    ve que el puntaje se movió, sin saber por culpa de qué criterio. Se compara con
    la misma normalización que usa la medición, así un `['a','b']` y un `["b","a"]`
    no figuran como cambio.
    """
    from AuditorIA.golden_metricas import normalizar

    nombres = nombres or {}
    cambios: List[Dict[str, Any]] = []
    for atributo_id in sorted(set(antes) | set(despues)):
        viejo = antes.get(atributo_id)
        nuevo = despues.get(atributo_id)
        if normalizar(viejo) == normalizar(nuevo):
            continue
        cambios.append({
            "atributo_id": atributo_id,
            "nombre": nombres.get(atributo_id, str(atributo_id)),
            "antes": None if viejo is None else str(viejo),
            "despues": None if nuevo is None else str(nuevo),
        })
    return cambios


# --------------------------------------------------------------------------- #
# Historial de un llamado                                                      #
# --------------------------------------------------------------------------- #
def historial(engine: Engine, auditoria_id: int) -> Dict[str, Any]:
    """Todas las corridas de un llamado, de la más nueva a la más vieja, con lo
    que cambió entre cada una y la siguiente."""
    with engine.connect() as conn:
        actual = conn.execute(text("""
            SELECT a.AuditoriaID, a.IdAplicativo, a.operadorUsuario, a.PlantillaID,
                   a.PuntajeFinal, a.EsErrorCritico, a.FechaAuditoria, a.PlantillaVersionID
            FROM calidad.Auditorias a WHERE a.AuditoriaID = :aid
        """), {"aid": auditoria_id}).mappings().first()
        if actual is None:
            return {"auditoria": None, "versiones": [], "disponible": False}
        actual = dict(actual)

        nombres = {
            int(r[0]): r[1] for r in conn.execute(text("""
                SELECT AtributoID, NombreAtributo FROM calidad.Atributos
                WHERE PlantillaID = :pid
            """), {"pid": actual["PlantillaID"]}).fetchall()
        }

        detalles_actual = {
            int(r["AtributoID"]): r["ValorResultado"]
            for r in conn.execute(text("""
                SELECT AtributoID, ValorResultado FROM calidad.AuditoriaDetalles
                WHERE AuditoriaID = :aid
            """), {"aid": auditoria_id}).mappings().all()
        }

        if not _existe_tabla(conn, "AuditoriaVersiones"):
            return {"auditoria": actual, "versiones": [], "disponible": False}

        archivadas = conn.execute(text("""
            SELECT VersionAuditoriaID, Numero, PuntajeFinal, EsErrorCritico,
                   FechaAuditoria, PlantillaVersionID, ReauditadaPorUsuarioID,
                   Motivo, FechaArchivado
            FROM calidad.AuditoriaVersiones
            WHERE AuditoriaID = :aid ORDER BY Numero
        """), {"aid": auditoria_id}).mappings().all()

        detalles_por_version: Dict[int, Dict[int, Any]] = {}
        if archivadas:
            for fila in conn.execute(text("""
                SELECT VersionAuditoriaID, AtributoID, ValorResultado
                FROM calidad.AuditoriaVersionDetalles
                WHERE VersionAuditoriaID IN :ids
            """).bindparams(bindparam("ids", expanding=True)),
                    {"ids": [int(a["VersionAuditoriaID"]) for a in archivadas]}).mappings():
                detalles_por_version.setdefault(
                    int(fila["VersionAuditoriaID"]), {}
                )[int(fila["AtributoID"])] = fila["ValorResultado"]

    versiones: List[Dict[str, Any]] = []
    for fila in archivadas:
        versiones.append({
            **dict(fila),
            "vigente": False,
            "detalles": detalles_por_version.get(int(fila["VersionAuditoriaID"]), {}),
        })
    versiones.append({
        "VersionAuditoriaID": None,
        "Numero": len(archivadas) + 1,
        "PuntajeFinal": actual["PuntajeFinal"],
        "EsErrorCritico": actual["EsErrorCritico"],
        "FechaAuditoria": actual["FechaAuditoria"],
        "PlantillaVersionID": actual["PlantillaVersionID"],
        "vigente": True,
        "detalles": detalles_actual,
    })

    # El diff de cada versión contra la anterior: es lo que se lee de un vistazo.
    # Se calculan TODOS los diffs antes de tocar `detalles`: si se convirtiera a
    # lista en la misma pasada, la versión siguiente compararía contra una lista y
    # el historial mostraría "todo cambió" en cada corrida.
    for i, version in enumerate(versiones):
        anterior = versiones[i - 1]["detalles"] if i > 0 else {}
        version["cambios"] = comparar_detalles(anterior, version["detalles"], nombres) if i > 0 else []
    for version in versiones:
        version["detalles"] = [
            {"atributo_id": aid, "nombre": nombres.get(aid, str(aid)), "valor": val}
            for aid, val in sorted(version["detalles"].items())
        ]

    versiones.reverse()   # la vigente primero
    return {"auditoria": actual, "versiones": versiones, "disponible": True}


# --------------------------------------------------------------------------- #
# La corrida                                                                   #
# --------------------------------------------------------------------------- #
# Reauditar reemplaza una auditoría YA PUBLICADA: la nota que el operador vio
# cambia. Por eso está detrás de su propio permiso (`audit:reauditar`), avisa qué
# cambió y deja la corrida anterior archivada y consultable.
CAMPOS_CONTEXTO = (
    "IdAplicativo", "operadorUsuario", "fecha_interaccion", "sentido_interaccion",
    "tipificacion_interaccion", "duracion_segundos", "comentario_interaccion",
    "CampanaID", "EmpresaID", "PlantillaID",
)


def _contexto(engine: Engine, auditoria_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """Los metadatos del llamado, para reconstruir el bloque Call_details del prompt.

    La corrida nueva tiene que ver lo MISMO que vio la original salvo el prompt: si
    ahora le faltara la tipificación o el operador, el resultado cambiaría por un
    motivo que no es el que estamos midiendo.
    """
    if not auditoria_ids:
        return {}
    with engine.connect() as conn:
        filas = conn.execute(text(f"""
            SELECT AuditoriaID, {', '.join(CAMPOS_CONTEXTO)}
            FROM calidad.Auditorias
            WHERE AuditoriaID IN :ids
        """).bindparams(bindparam("ids", expanding=True)),
            {"ids": auditoria_ids}).mappings().all()
    return {int(f["AuditoriaID"]): dict(f) for f in filas}


def es_chat(id_aplicativo: Optional[str], engine: Optional[Engine] = None) -> bool:
    """Indica si una interacción corresponde a un chat o mensajería de texto."""
    if not id_aplicativo:
        return False
    id_str = str(id_aplicativo)
    if "_CHT_" in id_str or id_str.endswith("_chat"):
        return True
    if engine is not None:
        try:
            with engine.connect() as conn:
                trans = conn.execute(text("""
                    SELECT TOP 1 1 FROM calidad.transcripciones
                    WHERE IdAplicativo = :id AND metadata LIKE '%"origen": "chat"%'
                """), {"id": id_str}).first()
                if trans:
                    return True
                inter, _, seg = id_str.rpartition("_")
                seg_int = int(seg) if seg.isdigit() else None
                if seg_int is not None:
                    dg = conn.execute(text("""
                        SELECT TOP 1 1 FROM [detalle de grabaciones]
                        WHERE idInteraccion = :inter AND Segmento = :seg AND Chat = 1
                    """), {"inter": inter, "seg": seg_int}).first()
                    if dg:
                        return True
        except Exception as e:
            logger.debug("Error comprobando es_chat para %s: %s", id_str, e)
    return False


def resolver_chat(
    engine: Engine,
    id_aplicativo: str,
    destino_dir: Optional[str] = None,
) -> Optional[str]:
    """Obtiene o reconstruye el archivo JSON del chat para pasarlo a Gemini.

    1. Si el archivo ya existe en disco en el directorio temporal, lo reutiliza.
    2. Si no, consulta Mitrol.Chat_historico para obtener la estructura completa del chat
       (con historial de 14 días, operadores y gestión auditada).
    3. Si Mitrol falla o no está disponible, intenta reconstruirlo a partir de
       calidad.transcripciones (metadata y segments) como fallback resiliente.
    Devuelve la ruta absoluta al archivo .json, o None si no se pudo recuperar.
    """
    from app.config import settings

    if not id_aplicativo:
        return None

    directorio = (
        destino_dir
        or getattr(settings, "AUDIO_TEMP_DIR", None)
        or os.path.join(tempfile.gettempdir(), "chats_auditoria")
    )
    os.makedirs(directorio, exist_ok=True)
    ruta_esperada = os.path.join(directorio, f"{id_aplicativo}_chat.json")

    if os.path.exists(ruta_esperada) and os.path.getsize(ruta_esperada) > 0:
        return ruta_esperada

    # 1. Identificar idInteraccion y segmento
    id_interaccion = id_aplicativo
    segmento: Optional[int] = None
    if "_" in id_aplicativo:
        partes = id_aplicativo.rsplit("_", 1)
        if len(partes) == 2 and partes[1].isdigit():
            id_interaccion = partes[0]
            segmento = int(partes[1])

    # Si hay metadata en calidad.transcripciones con gestion_a_auditar, priorizarla
    row_trans = None
    try:
        with engine.connect() as conn:
            row_trans = conn.execute(text("""
                SELECT metadata, segments FROM calidad.transcripciones
                WHERE IdAplicativo = :id
            """), {"id": id_aplicativo}).mappings().first()
    except Exception as e:
        logger.warning("Error leyendo transcripción para chat %s: %s", id_aplicativo, e)

    if row_trans and row_trans.get("metadata"):
        try:
            m = json.loads(row_trans["metadata"])
            ga = m.get("gestion_a_auditar") or {}
            if ga.get("idInteraccion"):
                id_interaccion = str(ga["idInteraccion"])
            if ga.get("segmento") is not None:
                segmento = int(ga["segmento"])
        except Exception:
            pass

    # 2. Intentar descargar mediante Mitrol.Chat_historico
    chat_data = None
    try:
        from AuditorIA.downloads.Mitrol import Mitrol
        api_mitrol = Mitrol(
            usuario=settings.MITROL_USER,
            clave=settings.MITROL_PASS,
            carpeta_descarga=directorio,
        )
        chat_data = api_mitrol.Chat_historico(
            engine=engine,
            idInteraccion=id_interaccion,
            segmento=segmento,
            dias_atras=14,
        )
    except Exception as e:
        logger.warning("Error consultando Mitrol.Chat_historico para %s: %s", id_aplicativo, e)

    # 3. Fallback: Reconstruir desde calidad.transcripciones si Mitrol no dio resultado
    if not chat_data and row_trans and row_trans.get("segments"):
        try:
            meta_dict = json.loads(row_trans["metadata"]) if row_trans.get("metadata") else {}
            segments = json.loads(row_trans["segments"])
            transcripcion_msgs = []
            operador_auditado = (meta_dict.get("gestion_a_auditar") or {}).get("operador_a_auditar", "")

            for s in segments:
                role = s.get("role", "")
                if role == "cliente":
                    quien = "CLIENTE"
                elif role == "bot":
                    quien = "BOT_IVR"
                elif role in ("agente", "operador") or "auditado" in (s.get("speakerLabel") or "").lower():
                    quien = "OPERADOR"
                else:
                    quien = "INDETERMINADO"

                es_auditado = "auditado" in (s.get("speakerLabel") or "").lower() or (
                    quien == "OPERADOR" and bool(operador_auditado)
                )
                textos = s.get("text", [])
                texto_str = "\n".join(textos) if isinstance(textos, list) else str(textos)

                transcripcion_msgs.append({
                    "quien": quien,
                    "nombre": s.get("speakerLabel", ""),
                    "hora": s.get("hora", ""),
                    "texto": texto_str,
                    "tipo": "mensaje" if role != "sistema" else "sistema",
                    "es_operador_auditado": es_auditado,
                })

            conversaciones = [{
                "idInteraccion": id_interaccion,
                "es_la_conversacion_a_auditar": True,
                "inicio": (meta_dict.get("gestion_a_auditar") or {}).get("inicio", ""),
                "cliente": (meta_dict.get("gestion_a_auditar") or {}).get("cliente", ""),
                "gestiones": [meta_dict.get("gestion_a_auditar")] if meta_dict.get("gestion_a_auditar") else [],
                "transcripcion": transcripcion_msgs,
            }]

            chat_data = {
                "gestion_a_auditar": meta_dict.get("gestion_a_auditar"),
                "ventana_historial": {
                    "dias_hacia_atras": meta_dict.get("dias_de_historial", 14),
                    "conversaciones_incluidas": len(conversaciones),
                },
                "como_leer_este_archivo": [
                    "Se audita SOLO la gestión descripta en 'gestion_a_auditar'; el resto de las conversaciones son historial del mismo cliente y sirven de contexto.",
                    "En 'transcripcion', 'quien' indica quién escribió cada mensaje: OPERADOR, CLIENTE o BOT_IVR.",
                    "Los mensajes con 'es_operador_auditado': true son los del operador bajo auditoría; los de otros operadores no se le computan.",
                ],
                "conversaciones": conversaciones,
            }
            logger.info("Chat %s reconstruido exitosamente desde calidad.transcripciones como fallback", id_aplicativo)
        except Exception as e:
            logger.warning("Error reconstruyendo chat %s desde transcripciones: %s", id_aplicativo, e)

    if not chat_data:
        logger.error("No se pudo obtener el chat %s ni de Mitrol ni de transcripciones", id_aplicativo)
        return None

    try:
        with open(ruta_esperada, "w", encoding="utf-8") as f:
            json.dump(chat_data, f, ensure_ascii=False, indent=2)
        return ruta_esperada
    except Exception as e:
        logger.error("Error escribiendo archivo chat en %s: %s", ruta_esperada, e)
        return None


def preparar(engine: Engine, auditoria_ids: Iterable[Any]) -> Dict[str, Any]:
    """Qué se puede reauditar de lo pedido, y qué no (sin gastar un token).

    Se chequea antes de disparar nada porque la mitad de los rechazos son
    previsibles —sin audio conservado, de otra plantilla— y enterarse después de
    esperar dos minutos es la peor forma de enterarse.
    """
    from AuditorIA import audio_store

    ids = [int(a) for a in auditoria_ids if a is not None]
    contexto = _contexto(engine, ids)

    listas: List[Dict[str, Any]] = []
    rechazadas: List[Dict[str, Any]] = []
    for auditoria_id in ids:
        meta = contexto.get(auditoria_id)
        if meta is None:
            rechazadas.append({"auditoria_id": auditoria_id, "motivo": "No existe esa auditoría."})
            continue
        resuelto = audio_store.resolver_audio(engine, meta["IdAplicativo"])
        if not resuelto:
            if es_chat(meta["IdAplicativo"], engine=engine):
                listas.append({**meta, "auditoria_id": auditoria_id, "ruta_audio": None, "es_chat": True})
                continue
            rechazadas.append({
                "auditoria_id": auditoria_id,
                "id_aplicativo": meta["IdAplicativo"],
                "motivo": "No se conserva el audio: sin el audio original no se puede "
                          "volver a auditar el mismo llamado.",
            })
            continue
        listas.append({**meta, "auditoria_id": auditoria_id, "ruta_audio": resuelto[0], "es_chat": False})

    plantillas = {int(item["PlantillaID"]) for item in listas if item.get("PlantillaID")}
    return {"listas": listas, "rechazadas": rechazadas, "plantillas": sorted(plantillas)}


def reauditar_batch(
    engine: Engine,
    *,
    auditoria_ids: Iterable[Any],
    usuario_id: Optional[int] = None,
    motivo: Optional[str] = None,
) -> Dict[str, Any]:
    """Encola la reauditoría de los llamados mediante Gemini Batch API.

    Los llamados se dividen por PlantillaID (cada una con su prompt vigente) y
    se envían a Gemini Batch en lotes. Cuando los trabajos concluyen, el
    procesamiento de batches archiva la versión previa y reemplaza la fila en
    calidad.Auditorias.
    """
    import uuid
    from collections import defaultdict
    import pandas as pd
    from google import genai
    from app.config import settings
    from AuditorIA.gemini import calidad_batch

    if not hay_versionado(engine):
        raise RuntimeError(
            "Falta aplicar la migración 2026-08-27_reauditoria_versionado.sql: sin la "
            "tabla de versiones no se puede archivar la corrida anterior, y reauditar "
            "sin archivar sería destruir evidencia."
        )

    preparado = preparar(engine, auditoria_ids)
    listas, rechazadas = preparado["listas"], preparado["rechazadas"]
    if not listas:
        return {
            "status": "SIN_LLAMADOS",
            "batches": [],
            "rechazadas": rechazadas,
            "encoladas": 0,
            "modo": "batch",
            "mensaje": "No hay llamadas ni chats disponibles para reauditar.",
        }

    # Materializar los archivos de chat para los ítems que sean chat
    items_listos: List[Dict[str, Any]] = []
    for item in listas:
        if item.get("es_chat") and not item.get("ruta_audio"):
            ruta_chat = resolver_chat(engine, item["IdAplicativo"])
            if ruta_chat:
                item["ruta_audio"] = ruta_chat
                items_listos.append(item)
            else:
                rechazadas.append({
                    "auditoria_id": item["auditoria_id"],
                    "id_aplicativo": item["IdAplicativo"],
                    "motivo": "No se pudo recuperar el contenido del chat desde Mitrol ni desde transcripciones.",
                })
        else:
            items_listos.append(item)

    listas = items_listos
    if not listas:
        return {
            "status": "SIN_LLAMADOS",
            "batches": [],
            "rechazadas": rechazadas,
            "encoladas": 0,
            "modo": "batch",
            "mensaje": "No se pudo recuperar el material (audio o chat) de ninguna de las interacciones solicitadas.",
        }

    # Agrupar llamadas por PlantillaID para que si vienen de distintas plantillas
    # se encole un batch adecuado por cada prompt.
    por_plantilla: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for item in listas:
        pid = int(item["PlantillaID"])
        por_plantilla[pid].append(item)

    cliente = genai.Client(api_key=settings.GEMINI_AUDITORIA_API_KEY)
    todos_batches: List[str] = []
    run_id_compartido = str(uuid.uuid4())

    for pid, items in por_plantilla.items():
        df = pd.DataFrame([{
            "audio_dir": item["ruta_audio"],
            "id_aplicativo": item["IdAplicativo"],
            "operador": item["operadorUsuario"],
            "operador_usuario": item["operadorUsuario"],
            "fecha_interaccion": item["fecha_interaccion"],
            "sentido": item["sentido_interaccion"],
            "sentido_interaccion": item["sentido_interaccion"],
            "tipificacion": item["tipificacion_interaccion"],
            "tipificacion_interaccion": item["tipificacion_interaccion"],
            "duracion_segundos": item["duracion_segundos"],
            "comentario": item["comentario_interaccion"],
            "comentario_interaccion": item["comentario_interaccion"],
            "empresa_id": item.get("EmpresaID"),
            "campana_id": item.get("CampanaID"),
            "plantilla_id": pid,
            "auditoria_id": item["auditoria_id"],
            "es_reauditoria": 1,
            "motivo_reauditoria": motivo or "Reauditada desde Auditorías Realizadas",
        } for item in items])

        contexto_ejecucion = {
            "trigger_source": "reauditoria",
            "run_id": run_id_compartido,
            "cantidad_solicitada": len(df),
            "empresa_id": df["empresa_id"].iloc[0] if "empresa_id" in df.columns and not df.empty else None,
            "campana_id": df["campana_id"].iloc[0] if "campana_id" in df.columns and not df.empty else None,
        }

        batches_plantilla = calidad_batch(
            engine=engine,
            df=df,
            plantilla_id=pid,
            gemini_api=cliente,
            user_id=int(usuario_id) if usuario_id else 0,
            contexto_ejecucion=contexto_ejecucion,
            transcribir=False,
        )
        if batches_plantilla:
            todos_batches.extend(batches_plantilla)

    return {
        "status": "BATCH_ENCOLADO",
        "batches": todos_batches,
        "encoladas": len(listas),
        "rechazadas": rechazadas,
        "plantillas": list(por_plantilla.keys()),
        "modo": "batch",
        "run_id": run_id_compartido,
        "mensaje": f"Se encolaron {len(listas)} llamada(s) en {len(todos_batches)} lote(s) de Gemini Batch.",
    }


def reauditar(
    engine: Engine,
    *,
    auditoria_ids: Iterable[Any],
    usuario_id: Optional[int] = None,
    motivo: Optional[str] = None,
    max_workers: int = 4,
    modo: str = "batch",
) -> Dict[str, Any]:
    """Vuelve a auditar los llamados indicados con el prompt vigente.

    Si `modo == "batch"` (default), delega en `reauditar_batch`.
    Si `modo == "sync"`, corre sincrónicamente con `apply_auditoria_threads`.
    """
    if modo == "batch":
        return reauditar_batch(
            engine,
            auditoria_ids=auditoria_ids,
            usuario_id=usuario_id,
            motivo=motivo,
        )

    import pandas as pd
    from google import genai

    from app.config import settings
    from AuditorIA.gemini import apply_auditoria_threads
    from AuditorIA.scoring import calcular_puntaje
    from AuditorIA import incidencias as inc, limites_texto

    if not hay_versionado(engine):
        raise RuntimeError(
            "Falta aplicar la migración 2026-08-27_reauditoria_versionado.sql: sin la "
            "tabla de versiones no se puede archivar la corrida anterior, y reauditar "
            "sin archivar sería destruir evidencia."
        )

    preparado = preparar(engine, auditoria_ids)
    listas, rechazadas = preparado["listas"], preparado["rechazadas"]
    if not listas:
        return {"resultados": [], "rechazadas": rechazadas, "reauditadas": 0, "modo": "sync"}

    # Materializar los archivos de chat para los ítems que sean chat
    items_listos: List[Dict[str, Any]] = []
    for item in listas:
        if item.get("es_chat") and not item.get("ruta_audio"):
            ruta_chat = resolver_chat(engine, item["IdAplicativo"])
            if ruta_chat:
                item["ruta_audio"] = ruta_chat
                items_listos.append(item)
            else:
                rechazadas.append({
                    "auditoria_id": item["auditoria_id"],
                    "id_aplicativo": item["IdAplicativo"],
                    "motivo": "No se pudo recuperar el contenido del chat desde Mitrol ni desde transcripciones.",
                })
        else:
            items_listos.append(item)

    listas = items_listos
    if not listas:
        return {"resultados": [], "rechazadas": rechazadas, "reauditadas": 0, "modo": "sync"}

    if len(preparado["plantillas"]) > 1:
        raise ValueError(
            "Los llamados son de plantillas distintas y cada una tiene su prompt: "
            "reauditá de a una plantilla por vez en modo sincrónico, o usá modo batch."
        )
    plantilla_id = preparado["plantillas"][0]

    df = pd.DataFrame([{
        "audio_dir": item["ruta_audio"],
        "id_aplicativo": item["IdAplicativo"],
        "operador": item["operadorUsuario"],
        "operador_usuario": item["operadorUsuario"],
        "fecha_interaccion": item["fecha_interaccion"],
        "sentido": item["sentido_interaccion"],
        "sentido_interaccion": item["sentido_interaccion"],
        "tipificacion": item["tipificacion_interaccion"],
        "tipificacion_interaccion": item["tipificacion_interaccion"],
        "duracion_segundos": item["duracion_segundos"],
        "comentario": item["comentario_interaccion"],
        "comentario_interaccion": item["comentario_interaccion"],
    } for item in listas])

    cliente = genai.Client(api_key=settings.GEMINI_AUDITORIA_API_KEY)
    df_calidad, _, nombre_id_map, modelo, _ = apply_auditoria_threads(
        df, cliente, plantilla_id, transcribir=False, engine=engine,
        max_workers=max_workers, concurrent_api_calls=max(1, max_workers // 2),
    )
    id_por_nombre = {str(k): int(v) for k, v in (nombre_id_map or {}).items()}

    with engine.connect() as conn:
        meta_atributos = {
            int(r["AtributoID"]): dict(r)
            for r in conn.execute(text("""
                SELECT AtributoID, NombreAtributo, TipoDato, Ponderacion
                FROM calidad.Atributos WHERE PlantillaID = :pid AND IsActive = 1
            """), {"pid": plantilla_id}).mappings().all()
        }
    nombres = {aid: m["NombreAtributo"] for aid, m in meta_atributos.items()}

    resultados: List[Dict[str, Any]] = []
    usos_ia: List[Dict[str, Any]] = []

    for posicion, item in enumerate(listas):
        auditoria_id = item["auditoria_id"]
        if posicion >= len(df_calidad):
            rechazadas.append({"auditoria_id": auditoria_id,
                               "motivo": "La IA no devolvió resultado para este llamado."})
            continue
        fila = df_calidad.iloc[posicion]
        if str(fila.get("status_auditoria") or "") == "FALLIDO":
            rechazadas.append({
                "auditoria_id": auditoria_id, "id_aplicativo": item["IdAplicativo"],
                "motivo": "La IA falló al auditar este llamado; la corrida anterior "
                          "quedó intacta.",
            })
            continue

        incidencia = inc.limpiar_valor(fila.get(inc.COLUMNA_IA))
        # Incidencia bloqueante (audio mudo, operador que no coincide): la respuesta
        # no es confiable. NO se pisa la corrida anterior con una peor.
        if incidencia in inc.BLOQUEANTES:
            rechazadas.append({
                "auditoria_id": auditoria_id, "id_aplicativo": item["IdAplicativo"],
                "motivo": f"La corrida nueva detectó una incidencia ({incidencia}); se "
                          "conserva la auditoría anterior en vez de reemplazarla por una "
                          "que no se puede usar.",
            })
            continue

        nuevos_detalles: List[Dict[str, Any]] = []
        for columna in df_calidad.columns:
            if not str(columna).startswith("Detalle_"):
                continue
            atributo_id = id_por_nombre.get(str(columna)[len("Detalle_"):])
            if atributo_id is None:
                continue
            valor = limites_texto.recortar_valor(
                fila.get(columna), atributo_id=atributo_id,
                id_aplicativo=item["IdAplicativo"],
            )
            # Igual que en el alta: sin valor no se guarda detalle (es el caso de los
            # atributos opcionales que la IA omitió por falta de evidencia).
            if valor is None or (isinstance(valor, str) and not valor.strip()):
                continue
            if isinstance(valor, (list, tuple)) and len(valor) == 0:
                continue
            nuevos_detalles.append({
                "atributo_id": atributo_id,
                "valor": valor,
                "ponderacion": meta_atributos.get(atributo_id, {}).get("Ponderacion", 0),
                "tipo": meta_atributos.get(atributo_id, {}).get("TipoDato"),
            })

        puntaje = calcular_puntaje([
            {"id": d["atributo_id"], "valor": d["valor"],
             "ponderacion": d["ponderacion"], "tipo": d["tipo"]}
            for d in nuevos_detalles
        ])

        try:
            resultado = _reemplazar(
                engine, auditoria_id=auditoria_id, detalles=nuevos_detalles,
                puntaje=puntaje, fila=fila, incidencia=incidencia,
                usuario_id=usuario_id, motivo=motivo, nombres=nombres,
            )
        except Exception as e:
            logger.exception("No se pudo reemplazar la auditoría %s", auditoria_id)
            rechazadas.append({
                "auditoria_id": auditoria_id, "id_aplicativo": item["IdAplicativo"],
                "motivo": f"No se pudo guardar la corrida nueva: {e}. La anterior "
                          "quedó intacta.",
            })
            continue

        resultado["id_aplicativo"] = item["IdAplicativo"]
        resultados.append(resultado)
        usos_ia.append({
            "feature": "auditoria",
            "modelo": modelo,
            "modo": "sync",
            "user_id": usuario_id,
            "input_tokens": fila.get("input_tokens"),
            "output_tokens": fila.get("output_tokens"),
            "thoughts_tokens": fila.get("thoughts_tokens"),
            "campana_id": item.get("CampanaID"),
            "empresa_id": item.get("EmpresaID"),
            "ref_id": f"reauditoria:{auditoria_id}",
            "extras": {"id_aplicativo": item["IdAplicativo"]},
        })

    if usos_ia:
        try:
            from app.uso_ia import registrar_uso_ia_bulk
            registrar_uso_ia_bulk(usos_ia)
        except Exception:
            logger.exception("No se pudo registrar el consumo de IA de la reauditoría.")

    return {"resultados": resultados, "rechazadas": rechazadas,
            "reauditadas": len(resultados), "modelo": modelo}


def _reemplazar(engine: Engine, *, auditoria_id, detalles, puntaje, fila,
                incidencia, usuario_id, motivo, nombres) -> Dict[str, Any]:
    """Archiva lo viejo y escribe lo nuevo, todo en UNA transacción.

    Si algo falla en el medio se revierte entero: no puede quedar una auditoría con
    los detalles nuevos y el puntaje viejo, ni al revés.
    """
    from AuditorIA import incidencias as inc

    with engine.begin() as conn:
        antes = {
            int(r["AtributoID"]): r["ValorResultado"]
            for r in conn.execute(text("""
                SELECT AtributoID, ValorResultado FROM calidad.AuditoriaDetalles
                WHERE AuditoriaID = :aid
            """), {"aid": auditoria_id}).mappings().all()
        }
        cabecera = conn.execute(text("""
            SELECT PuntajeFinal, EsErrorCritico FROM calidad.Auditorias
            WHERE AuditoriaID = :aid
        """), {"aid": auditoria_id}).mappings().first() or {}

        version_id = archivar_version(conn, auditoria_id, usuario_id=usuario_id, motivo=motivo)
        if version_id is None:
            raise RuntimeError("No se pudo archivar la corrida anterior.")

        conn.execute(text("DELETE FROM calidad.AuditoriaDetalles WHERE AuditoriaID = :aid"),
                     {"aid": auditoria_id})
        for orden, det in enumerate(detalles, start=1):
            conn.execute(
                text("""
                    INSERT INTO calidad.AuditoriaDetalles
                        (AuditoriaID, AtributoID, ValorResultado, Orden, PonderacionAplicada)
                    VALUES (:aid, :atributo, :valor, :orden, :pond)
                """).bindparams(bindparam("valor", type_=NVARCHAR(None))),
                {"aid": auditoria_id, "atributo": det["atributo_id"],
                 "valor": str(det["valor"]), "orden": orden,
                 "pond": det.get("ponderacion") or 0},
            )

        # Solo las columnas que produce la corrida. El contexto del llamado
        # (operador, fecha, tipificación) es el mismo audio: no se toca.
        sets = ["PuntajeFinal = :puntaje", "EsErrorCritico = :ec", "FechaAuditoria = GETDATE()",
                "input_tokens = :in_tok", "output_tokens = :out_tok",
                "thoughts_tokens = :th_tok", "response_thoughts = :thoughts"]
        params = {
            "aid": auditoria_id,
            "puntaje": puntaje.puntaje,
            "ec": 1 if puntaje.es_error_critico else 0,
            "in_tok": _int_o_none(fila.get("input_tokens")),
            "out_tok": _int_o_none(fila.get("output_tokens")),
            "th_tok": _int_o_none(fila.get("thoughts_tokens")),
            "thoughts": fila.get("response_thoughts"),
        }
        if _tiene_columna(conn, "Auditorias", "PlantillaVersionID"):
            sets.append("PlantillaVersionID = :version_plantilla")
            params["version_plantilla"] = _version_plantilla(fila)
        if _tiene_columna(conn, "Auditorias", "Incidencia"):
            sets.append("Incidencia = :incidencia")
            params["incidencia"] = inc.limpiar_valor(incidencia)

        conn.execute(
            text(f"UPDATE calidad.Auditorias SET {', '.join(sets)} WHERE AuditoriaID = :aid")
            .bindparams(bindparam("thoughts", type_=NVARCHAR(None))),
            params,
        )
        numero = numero_de_version(conn, auditoria_id)

    despues = {d["atributo_id"]: d["valor"] for d in detalles}
    return {
        "auditoria_id": auditoria_id,
        "version_archivada": version_id,
        "version_vigente": numero,
        "puntaje_antes": cabecera.get("PuntajeFinal"),
        "puntaje_despues": puntaje.puntaje,
        "ec_antes": bool(cabecera.get("EsErrorCritico")),
        "ec_despues": bool(puntaje.es_error_critico),
        "cambios": comparar_detalles(antes, despues, nombres),
    }


def _int_o_none(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _version_plantilla(fila):
    valor = fila.get("plantilla_version_id") if hasattr(fila, "get") else None
    return _int_o_none(valor)


def _tiene_columna(conn, tabla: str, columna: str, esquema: str = "calidad") -> bool:
    try:
        return conn.execute(
            text("""SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = :e AND TABLE_NAME = :t AND COLUMN_NAME = :c"""),
            {"e": esquema, "t": tabla, "c": columna},
        ).first() is not None
    except Exception:
        return False
