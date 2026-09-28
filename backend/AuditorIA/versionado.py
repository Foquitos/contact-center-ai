"""Versionado de plantillas de auditoría: qué prompt produjo cada auditoría.

`sp_ModificarPlantilla` pisa el texto y no deja historial. Sin versiones no se
puede decir "la v3 subió el kappa de 0.51 a 0.68" (las auditorías de dos prompts
distintos quedan mezcladas en la misma métrica), no hay a dónde volver cuando un
cambio empeora las cosas, y la verdad humana envejece sin que nadie se entere.

CÓMO NACE UNA VERSIÓN
---------------------
Sola, por HASH del contenido. `obtener_o_crear_version` arma un snapshot canónico
de la plantilla (system prompt + recordatorio + modelo + atributos con su tipo,
prompt, restricciones y ponderación), lo hashea, y si ese hash ya existe devuelve
la versión existente; si no, crea la siguiente.

Se llama en dos momentos: al construir el prompt de una corrida
(`AuditorIA/gemini.py::prompt`) y al guardar una revisión humana
(`AuditorIA/revision.py`). Elegido así a propósito:

- No hay que enganchar los seis endpoints de edición de plantillas/atributos, ni
  DEPENDER de que el usuario "guarde una versión" (hay un botón para hacerlo a
  mano —ver más abajo—, pero nadie se va a acordar de apretarlo siempre).
- Editar y volver atrás NO genera una versión nueva: el hash vuelve a ser el de
  antes y se reusa esa fila. El historial refleja estados distintos, no clics.
- Una plantilla que se edita pero nunca se usa no genera versiones: no hay nada
  que trazar hasta que produce una auditoría.

El costo es que la numeración salta cuando hay ediciones intermedias que nunca se
usaron. Es el precio correcto: la v3 es "el prompt con el que se auditó", no "la
tercera vez que alguien tocó el editor".

Y A MANO, CUANDO EL USUARIO LO PIDE
-----------------------------------
`guardar_version_actual` es la misma alta pero disparada por el botón "Guardar en
el historial" del editor. Existe porque el alta automática deja un hueco real: una
plantilla recién editada no tiene punto de retorno hasta que audita, y auditar
cuesta plata. Es la MISMA función de hash, así que sigue valiendo todo lo de
arriba (guardar dos veces sin tocar nada no crea dos versiones); lo único que
cambia es que acá los fallos se cuentan —el usuario apretó un botón y merece un
error, no un silencio— en vez de degradar sin avisar.

Migración: scripts/migrations/2026-08-06d_plantilla_versiones.sql
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from AuditorIA import conocimiento_plantilla

logger = logging.getLogger(__name__)

# Campos del atributo que forman parte de la identidad de la versión. Cambiar
# cualquiera de estos cambia lo que la IA responde o cómo puntúa, así que define
# una versión nueva. Quedan afuera a propósito: DarAviso/FrasesAviso (son alertas
# por mail, no afectan la auditoría) y el Orden (reordenar no cambia el criterio).
CAMPOS_ATRIBUTO = ("nombre", "tipo", "prompt", "restricciones", "ponderacion", "es_opcional")


def _tiene_tabla_versiones(conn) -> bool:
    """¿Está aplicada la migración 2026-08-06d? Se chequea antes de escribir: si
    no está, todo el subsistema degrada a no versionar en vez de romper la
    auditoría, que es lo único que no puede fallar."""
    try:
        return conn.execute(text("""
            SELECT 1 FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = 'calidad' AND TABLE_NAME = 'PlantillaVersiones'
        """)).first() is not None
    except Exception:
        return False


def _tiene_columna(conn, tabla: str, columna: str) -> bool:
    try:
        return conn.execute(text("""
            SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = 'calidad' AND TABLE_NAME = :t AND COLUMN_NAME = :c
        """), {"t": tabla, "c": columna}).first() is not None
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Snapshot y hash (puros salvo la lectura)                                     #
# --------------------------------------------------------------------------- #
def construir_snapshot(conn, plantilla_id: int) -> Optional[Dict[str, Any]]:
    """Estado completo de la plantilla, en forma canónica y ordenada.

    Canónico = dos lecturas del mismo estado producen el MISMO dict, así el hash
    es estable. Por eso los atributos van ordenados por AtributoID (no por Orden,
    que el usuario puede cambiar sin cambiar el criterio) y las claves fijas.
    """
    cabecera = conn.execute(text("""
        SELECT TOP 1 PlantillaID, Nombre, SystemPrompt, Recordatorio, ModeloIA
        FROM calidad.Plantillas
        WHERE PlantillaID = :pid AND IsActive = 1
    """), {"pid": plantilla_id}).mappings().first()
    if cabecera is None:
        return None

    tiene_opcional = _tiene_columna(conn, "Atributos", "EsOpcional")
    col_opcional = "at.EsOpcional" if tiene_opcional else "CAST(0 AS BIT)"

    filas = conn.execute(text(f"""
        SELECT at.AtributoID, at.NombreAtributo, at.TipoDato, at.PromptAdyacente,
               at.Restricciones, at.Ponderacion, {col_opcional} AS EsOpcional
        FROM calidad.Atributos at
        WHERE at.PlantillaID = :pid AND at.IsActive = 1
        ORDER BY at.AtributoID
    """), {"pid": plantilla_id}).mappings().all()

    atributos = []
    for fila in filas:
        atributos.append({
            "atributo_id": int(fila["AtributoID"]),
            "nombre": fila["NombreAtributo"],
            "tipo": fila["TipoDato"],
            "prompt": fila["PromptAdyacente"],
            "restricciones": fila["Restricciones"],
            "ponderacion": float(fila["Ponderacion"] or 0),
            "es_opcional": bool(fila["EsOpcional"]),
        })

    snapshot = {
        "plantilla_id": int(cabecera["PlantillaID"]),
        "nombre": cabecera["Nombre"],
        "system_prompt": cabecera["SystemPrompt"],
        "recordatorio": cabecera["Recordatorio"],
        "modelo": cabecera["ModeloIA"],
        "atributos": atributos,
    }
    # Los documentos de referencia son parte de lo que la IA lee (ver
    # AuditorIA/conocimiento_plantilla.py): editar uno cambia la versión. La clave va
    # SOLO si hay documentos, para que el hash de las plantillas sin conocimiento sea
    # el mismo de siempre y no nazca una versión nueva de cada una en la próxima corrida.
    conocimiento = conocimiento_plantilla.huella(conn, plantilla_id)
    if conocimiento:
        snapshot["conocimiento"] = conocimiento
    return snapshot


def hash_snapshot(snapshot: Dict[str, Any]) -> str:
    """sha256 del snapshot serializado de forma determinística.

    `sort_keys` y separadores fijos: sin eso, dos dicts iguales podrían
    serializarse distinto y generar versiones fantasma en cada corrida.
    """
    canonico = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Alta y lectura de versiones                                                  #
# --------------------------------------------------------------------------- #
class VersionadoNoDisponible(RuntimeError):
    """No hay dónde guardar la versión: falta la migración 2026-08-06d.

    Solo la ve el alta MANUAL. El alta automática no puede frenar una corrida por
    esto, así que degrada en silencio (devuelve None); el botón, en cambio, tiene
    que decir por qué no guardó.
    """


def _obtener_o_crear(
    conn,
    plantilla_id: int,
    *,
    user_id: Optional[int] = None,
    motivo: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """La versión del estado ACTUAL, creándola si el hash es nuevo.

    Corre dentro de una transacción ya abierta y con la tabla ya verificada.
    Devuelve {"version_id", "numero", "creada"} —`creada` en False cuando el hash ya
    existía, que es lo que le permite al botón decir "esto ya estaba guardado como
    v3" en vez de fingir un alta— o None si la plantilla no existe o está de baja.
    """
    snapshot = construir_snapshot(conn, plantilla_id)
    if snapshot is None:
        return None
    firma = hash_snapshot(snapshot)

    existente = conn.execute(text("""
        SELECT TOP 1 VersionID, Numero FROM calidad.PlantillaVersiones
        WHERE PlantillaID = :pid AND Hash = :hash
    """), {"pid": plantilla_id, "hash": firma}).first()
    if existente:
        return {"version_id": int(existente[0]), "numero": int(existente[1]), "creada": False}

    # El lock serializa el "calculá el siguiente número e insertá" entre
    # corridas concurrentes: dos auditorías simultáneas de la misma
    # plantilla recién editada calcularían el mismo Numero y una
    # reventaría contra el UNIQUE.
    conn.execute(text("""
        EXEC sp_getapplock @Resource = :res, @LockMode = 'Exclusive',
                           @LockOwner = 'Transaction', @LockTimeout = 15000
    """), {"res": f"plantilla_version:{plantilla_id}"})

    # Relectura dentro del lock: otra corrida pudo crearla mientras tanto.
    existente = conn.execute(text("""
        SELECT TOP 1 VersionID, Numero FROM calidad.PlantillaVersiones
        WHERE PlantillaID = :pid AND Hash = :hash
    """), {"pid": plantilla_id, "hash": firma}).first()
    if existente:
        return {"version_id": int(existente[0]), "numero": int(existente[1]), "creada": False}

    siguiente = conn.execute(text("""
        SELECT COALESCE(MAX(Numero), 0) + 1 FROM calidad.PlantillaVersiones
        WHERE PlantillaID = :pid
    """), {"pid": plantilla_id}).scalar() or 1

    version_id = conn.execute(text("""
        INSERT INTO calidad.PlantillaVersiones
            (PlantillaID, Numero, Hash, SnapshotJSON, Motivo, CreadoPorUsuarioID)
        OUTPUT inserted.VersionID
        VALUES (:pid, :num, :hash, :snap, :motivo, :uid)
    """), {
        "pid": plantilla_id,
        "num": int(siguiente),
        "hash": firma,
        "snap": json.dumps(snapshot, ensure_ascii=False),
        "motivo": motivo,
        "uid": user_id,
    }).scalar()
    if not version_id:
        return None

    logger.info("Plantilla %s: nueva versión v%s (%s)", plantilla_id, siguiente, firma[:12])
    return {"version_id": int(version_id), "numero": int(siguiente), "creada": True}


def obtener_o_crear_version(
    engine: Engine,
    plantilla_id: int,
    *,
    user_id: Optional[int] = None,
    motivo: Optional[str] = None,
) -> Optional[int]:
    """VersionID de la plantilla TAL COMO ESTÁ AHORA. Crea la versión si es nueva.

    Best-effort: si la migración no está aplicada o algo falla, devuelve None y la
    auditoría sigue su curso sin versionar. Versionar es trazabilidad; no puede
    ser el motivo por el que una corrida se cae.
    """
    try:
        with engine.begin() as conn:
            if not _tiene_tabla_versiones(conn):
                return None
            datos = _obtener_o_crear(conn, plantilla_id, user_id=user_id, motivo=motivo)
        return datos["version_id"] if datos else None
    except Exception as e:
        logger.warning("No se pudo versionar la plantilla %s: %s", plantilla_id, e)
        return None


def guardar_version_actual(
    engine: Engine,
    plantilla_id: int,
    *,
    user_id: Optional[int] = None,
    motivo: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Alta MANUAL: guarda el estado actual en el historial sin tener que auditar.

    Es el mismo alta por hash (guardar dos veces sin tocar nada devuelve la versión
    que ya estaba, con `creada` en False), pero pedida a mano. La diferencia con
    `obtener_o_crear_version` es el manejo de errores: acá NO se tragan. El usuario
    apretó un botón y espera una respuesta; un None silencioso le haría creer que
    guardó cuando no guardó nada.

    Devuelve None solo si la plantilla no existe o está dada de baja; si falta la
    migración levanta VersionadoNoDisponible, y cualquier otro error de base sube
    tal cual para que el endpoint lo traduzca.
    """
    with engine.begin() as conn:
        if not _tiene_tabla_versiones(conn):
            raise VersionadoNoDisponible(
                "El historial de versiones todavía no está habilitado en esta base "
                "(falta aplicar la migración 2026-08-06d_plantilla_versiones.sql)."
            )
        return _obtener_o_crear(conn, plantilla_id, user_id=user_id, motivo=motivo)


def listar_versiones(engine: Engine, plantilla_id: int) -> List[Dict[str, Any]]:
    """Historial de la plantilla, con cuántas auditorías produjo cada versión.

    El conteo es lo que vuelve útil el historial: una versión con 3 auditorías no
    se puede comparar contra una con 800.
    """
    try:
        with engine.connect() as conn:
            if not _tiene_tabla_versiones(conn):
                return []
            tiene_col = _tiene_columna(conn, "Auditorias", "PlantillaVersionID")
            conteo = ("""(SELECT COUNT(*) FROM calidad.Auditorias a
                           WHERE a.PlantillaVersionID = v.VersionID)"""
                      if tiene_col else "0")
            filas = conn.execute(text(f"""
                SELECT v.VersionID, v.Numero, v.Hash, v.Motivo, v.CreadoPorUsuarioID,
                       v.FechaCreacion, {conteo} AS Auditorias
                FROM calidad.PlantillaVersiones v
                WHERE v.PlantillaID = :pid
                ORDER BY v.Numero DESC
            """), {"pid": plantilla_id}).mappings().all()
        return [dict(f) for f in filas]
    except Exception as e:
        logger.warning("No se pudo listar el historial de la plantilla %s: %s", plantilla_id, e)
        return []


def obtener_version(engine: Engine, version_id: int) -> Optional[Dict[str, Any]]:
    """Una versión con su snapshot ya parseado."""
    try:
        with engine.connect() as conn:
            if not _tiene_tabla_versiones(conn):
                return None
            fila = conn.execute(text("""
                SELECT TOP 1 VersionID, PlantillaID, Numero, Hash, SnapshotJSON,
                       Motivo, CreadoPorUsuarioID, FechaCreacion
                FROM calidad.PlantillaVersiones WHERE VersionID = :vid
            """), {"vid": version_id}).mappings().first()
        if fila is None:
            return None
        datos = dict(fila)
        try:
            datos["snapshot"] = json.loads(datos.pop("SnapshotJSON"))
        except (TypeError, ValueError):
            datos["snapshot"] = None
        return datos
    except Exception as e:
        logger.warning("No se pudo leer la versión %s: %s", version_id, e)
        return None


def version_actual(engine: Engine, plantilla_id: int) -> Optional[Dict[str, Any]]:
    """La versión que corresponde al estado ACTUAL de la plantilla, sin crearla.

    Sirve para preguntar "¿la verdad de este set se estableció con el prompt que
    está hoy en producción?" sin ensuciar el historial con una versión nueva.
    """
    try:
        with engine.connect() as conn:
            if not _tiene_tabla_versiones(conn):
                return None
            snapshot = construir_snapshot(conn, plantilla_id)
            if snapshot is None:
                return None
            fila = conn.execute(text("""
                SELECT TOP 1 VersionID, Numero, Hash, FechaCreacion
                FROM calidad.PlantillaVersiones
                WHERE PlantillaID = :pid AND Hash = :hash
            """), {"pid": plantilla_id, "hash": hash_snapshot(snapshot)}).mappings().first()
        return dict(fila) if fila else None
    except Exception as e:
        logger.warning("No se pudo resolver la versión actual de %s: %s", plantilla_id, e)
        return None


# --------------------------------------------------------------------------- #
# Diff entre versiones (puro: sin DB, testeable)                               #
# --------------------------------------------------------------------------- #
def _titulos_de_conocimiento(docs: List[Dict[str, Any]], otros: List[Dict[str, Any]]) -> Optional[str]:
    """Los títulos de una lista de documentos, uno por renglón, marcando los que
    están en las dos versiones con el contenido cambiado."""
    if not docs:
        return None
    sha_en_otros = {d.get("doc_id"): d.get("sha") for d in otros}
    renglones = []
    for d in docs:
        doc_id = d.get("doc_id")
        editado = doc_id in sha_en_otros and sha_en_otros[doc_id] != d.get("sha")
        renglones.append(f"{d.get('titulo')}{' (contenido editado)' if editado else ''}")
    return "\n".join(renglones)


def diff_snapshots(anterior: Optional[Dict[str, Any]],
                   posterior: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Qué cambió entre dos snapshots de plantilla.

    Devuelve {cabecera: [...], atributos: {agregados, quitados, modificados},
    atributos_afectados: [ids]}. `atributos_afectados` es la lista que importa
    para la vigencia del Golden Set: si entre la versión con la que se revisó y
    la actual cambió el atributo 7, la verdad humana del atributo 7 quedó vieja,
    pero la del resto sigue valiendo. Vencer el set entero sería tirar trabajo.
    """
    resultado: Dict[str, Any] = {
        "cabecera": [],
        "atributos": {"agregados": [], "quitados": [], "modificados": []},
        "atributos_afectados": [],
        "hay_cambios": False,
    }
    if anterior is None or posterior is None:
        return resultado

    for campo in ("system_prompt", "recordatorio", "modelo", "nombre"):
        if (anterior.get(campo) or "") != (posterior.get(campo) or ""):
            resultado["cabecera"].append({
                "campo": campo,
                "antes": anterior.get(campo),
                "despues": posterior.get(campo),
            })

    # Conocimiento de referencia: se compara por documento y contenido (el hash), pero
    # se muestra por título, que es lo que la persona reconoce.
    conocimiento_antes = anterior.get("conocimiento") or []
    conocimiento_despues = posterior.get("conocimiento") or []
    if conocimiento_antes != conocimiento_despues:
        resultado["cabecera"].append({
            "campo": "conocimiento",
            "antes": _titulos_de_conocimiento(conocimiento_antes, conocimiento_despues),
            "despues": _titulos_de_conocimiento(conocimiento_despues, conocimiento_antes),
        })

    por_id_antes = {a["atributo_id"]: a for a in (anterior.get("atributos") or [])}
    por_id_despues = {a["atributo_id"]: a for a in (posterior.get("atributos") or [])}

    for atributo_id, attr in por_id_despues.items():
        if atributo_id not in por_id_antes:
            resultado["atributos"]["agregados"].append(
                {"atributo_id": atributo_id, "nombre": attr.get("nombre")}
            )
            resultado["atributos_afectados"].append(atributo_id)

    for atributo_id, attr in por_id_antes.items():
        if atributo_id not in por_id_despues:
            resultado["atributos"]["quitados"].append(
                {"atributo_id": atributo_id, "nombre": attr.get("nombre")}
            )
            resultado["atributos_afectados"].append(atributo_id)
            continue

        nuevo = por_id_despues[atributo_id]
        campos_cambiados = [
            {"campo": campo, "antes": attr.get(campo), "despues": nuevo.get(campo)}
            for campo in CAMPOS_ATRIBUTO
            if attr.get(campo) != nuevo.get(campo)
        ]
        if campos_cambiados:
            resultado["atributos"]["modificados"].append({
                "atributo_id": atributo_id,
                "nombre": nuevo.get("nombre") or attr.get("nombre"),
                "campos": campos_cambiados,
            })
            resultado["atributos_afectados"].append(atributo_id)

    resultado["atributos_afectados"] = sorted(set(resultado["atributos_afectados"]))
    resultado["hay_cambios"] = bool(
        resultado["cabecera"]
        or resultado["atributos"]["agregados"]
        or resultado["atributos"]["quitados"]
        or resultado["atributos"]["modificados"]
    )
    return resultado


def diff_versiones(engine: Engine, version_a: int, version_b: int) -> Dict[str, Any]:
    """Como diff_snapshots pero resolviendo las versiones desde la BD."""
    a = obtener_version(engine, version_a)
    b = obtener_version(engine, version_b)
    diff = diff_snapshots(a.get("snapshot") if a else None, b.get("snapshot") if b else None)
    diff["version_a"] = {"version_id": version_a, "numero": a.get("Numero") if a else None}
    diff["version_b"] = {"version_id": version_b, "numero": b.get("Numero") if b else None}
    return diff
