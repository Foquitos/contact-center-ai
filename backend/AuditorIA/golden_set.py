"""Golden Set: el conjunto congelado de llamados con verdad humana de una plantilla.

Es la capa de datos entre las revisiones humanas (AuditorIA/revision.py) y las
métricas puras (AuditorIA/golden_metricas.py). Acá vive todo lo que toca la BD:
armar los casos comparables, administrar los sets y sus items, y proteger los
audios del set contra el descarte automático.

QUÉ ES UN SET Y POR QUÉ NO ALCANZA CON "TODAS LAS REVISIONES"
------------------------------------------------------------
Las revisiones sueltas sirven para medir sobre la marcha, pero tienen sesgo de
selección: el analista revisa lo que le llamó la atención, que suele ser lo que
la IA hizo mal. Un set es una muestra elegida a propósito (estratificada por
resultado, duración y skill) y CONGELADA, para que dos corridas separadas por un
mes se puedan comparar entre sí. Por eso `cargar_casos` acepta las dos fuentes:
un set (`golden_set_id`) o todas las revisiones de una plantilla.

SPLIT train/test
----------------
Cada item cae en 'train' o 'test'. El proponente automático de prompts (Fase 3)
solo puede mirar 'train'; la validación corre contra 'test'. Sin esa separación
el prompt termina memorizando los casos del set: la métrica sube y la auditoría
real no mejora.

Migración: scripts/migrations/2026-08-06c_golden_set_fase0.sql
"""
from __future__ import annotations

import logging
import random
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from AuditorIA import versionado
from AuditorIA import golden_muestreo, reauditoria
from AuditorIA.golden_metricas import (
    CasoEvaluado,
    ComparacionAtributo,
    es_revisable,
    normalizar,
)
from AuditorIA.scoring import EC, NO_OK, OK, TIPO_CRITICAL

logger = logging.getLogger(__name__)

SPLITS = ("train", "test")


# --------------------------------------------------------------------------- #
# Sets                                                                         #
# --------------------------------------------------------------------------- #
def listar_sets(engine: Engine, plantilla_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Sets activos (opcionalmente los de una plantilla) con el conteo de items."""
    filtro = "AND gs.PlantillaID = :pid" if plantilla_id is not None else ""
    with engine.connect() as conn:
        filas = conn.execute(text(f"""
            SELECT gs.GoldenSetID, gs.Nombre, gs.PlantillaID, gs.Descripcion,
                   gs.FechaCreacion, gs.FechaRevisionRecomendada, gs.CreadoPorUsuarioID,
                   p.Nombre AS NombrePlantilla,
                   COUNT(CASE WHEN i.IsActive = 1 THEN 1 END) AS Items,
                   COUNT(CASE WHEN i.IsActive = 1 AND i.Split = 'train' THEN 1 END) AS Train,
                   COUNT(CASE WHEN i.IsActive = 1 AND i.Split = 'test' THEN 1 END) AS Test
            FROM calidad.GoldenSets gs
            LEFT JOIN calidad.GoldenSetItems i ON i.GoldenSetID = gs.GoldenSetID
            LEFT JOIN calidad.Plantillas p ON p.PlantillaID = gs.PlantillaID
            WHERE gs.IsActive = 1 {filtro}
            GROUP BY gs.GoldenSetID, gs.Nombre, gs.PlantillaID, gs.Descripcion,
                     gs.FechaCreacion, gs.FechaRevisionRecomendada, gs.CreadoPorUsuarioID, p.Nombre
            ORDER BY gs.Nombre
        """), {"pid": plantilla_id}).mappings().all()
    return [dict(f) for f in filas]


def obtener_set(engine: Engine, golden_set_id: int) -> Optional[Dict[str, Any]]:
    with engine.connect() as conn:
        fila = conn.execute(text("""
            SELECT TOP 1 GoldenSetID, Nombre, PlantillaID, Descripcion, IsActive,
                   FechaCreacion, FechaRevisionRecomendada
            FROM calidad.GoldenSets WHERE GoldenSetID = :sid
        """), {"sid": golden_set_id}).mappings().first()
    return dict(fila) if fila else None


def crear_set(
    engine: Engine,
    *,
    nombre: str,
    plantilla_id: int,
    descripcion: Optional[str] = None,
    user_id: Optional[int] = None,
) -> int:
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("El set necesita un nombre.")
    with engine.begin() as conn:
        return int(conn.execute(text("""
            INSERT INTO calidad.GoldenSets (Nombre, PlantillaID, Descripcion, CreadoPorUsuarioID)
            OUTPUT inserted.GoldenSetID
            VALUES (:nombre, :pid, :desc, :uid)
        """), {
            "nombre": nombre, "pid": plantilla_id,
            "desc": descripcion, "uid": user_id,
        }).scalar())


def desactivar_set(engine: Engine, golden_set_id: int) -> None:
    """Baja lógica. Los audios fijados se liberan: si el set no se usa más, no
    tiene sentido que sigan ocupando lugar del tope (ver audio_store.aplicar_tope)."""
    with engine.begin() as conn:
        ids = [f[0] for f in conn.execute(text("""
            SELECT IdAplicativo FROM calidad.GoldenSetItems
            WHERE GoldenSetID = :sid AND IsActive = 1
        """), {"sid": golden_set_id}).fetchall()]
        conn.execute(text("UPDATE calidad.GoldenSets SET IsActive = 0 WHERE GoldenSetID = :sid"),
                     {"sid": golden_set_id})
    _liberar_audios_sin_set(engine, ids)


# --------------------------------------------------------------------------- #
# Items                                                                        #
# --------------------------------------------------------------------------- #
def listar_items(engine: Engine, golden_set_id: int) -> List[Dict[str, Any]]:
    """Items del set con el estado de su verdad humana y de su audio.

    `Revisiones` = cuántos humanos revisaron esa auditoría. Un item en 0 está en
    el set pero todavía no aporta verdad: hay que revisarlo para que cuente.
    """
    with engine.connect() as conn:
        filas = conn.execute(text("""
            SELECT i.ItemID, i.IdAplicativo, i.AuditoriaID, i.Split, i.Notas,
                   i.FechaAgregado, i.IsActive,
                   a.operadorUsuario, a.fecha_interaccion, a.duracion_segundos,
                   a.PuntajeFinal, a.EsErrorCritico,
                   (SELECT COUNT(*) FROM calidad.AuditoriaRevisiones r
                     WHERE r.AuditoriaID = i.AuditoriaID) AS Revisiones,
                   CASE WHEN au.AudioID IS NOT NULL
                             OR EXISTS (SELECT 1 FROM calidad.transcripciones t
                                        WHERE t.IdAplicativo = i.IdAplicativo
                                          AND (t.metadata LIKE '%"origen": "chat"%' OR i.IdAplicativo LIKE '%_CHT_%' OR i.IdAplicativo LIKE '%_chat%'))
                             OR i.IdAplicativo LIKE '%_CHT_%'
                             OR i.IdAplicativo LIKE '%_chat%'
                        THEN 1 ELSE 0 END AS TieneAudio
            FROM calidad.GoldenSetItems i
            LEFT JOIN calidad.Auditorias a ON a.AuditoriaID = i.AuditoriaID
            LEFT JOIN calidad.AudioAuditoria au ON au.IdAplicativo = i.IdAplicativo
            WHERE i.GoldenSetID = :sid AND i.IsActive = 1
            ORDER BY i.FechaAgregado DESC
        """), {"sid": golden_set_id}).mappings().all()
    return [dict(f) for f in filas]


def agregar_items(
    engine: Engine,
    *,
    golden_set_id: int,
    auditoria_ids: Iterable[int],
    split: Optional[str] = None,
    user_id: Optional[int] = None,
    notas: Optional[str] = None,
) -> Dict[str, Any]:
    """Suma auditorías al set y fija sus audios.

    `split` en None reparte automático 60/40 respetando lo que ya tiene el set
    (elige el lado que esté más flojo), así el reparto no depende de en qué orden
    se cargaron los casos.

    Devuelve {agregados, ya_estaban, omitidas, audios_fijados}. `omitidas` son las
    auditorías que no existen o que son de otra plantilla.
    """
    ids = list(dict.fromkeys(int(i) for i in auditoria_ids if i is not None))
    if not ids:
        return {"agregados": 0, "ya_estaban": 0, "omitidas": [], "audios_fijados": 0}
    if split is not None and split not in SPLITS:
        raise ValueError(f"Split inválido: {split!r}. Usar 'train' o 'test'.")

    juego = obtener_set(engine, golden_set_id)
    if juego is None:
        raise ValueError(f"No existe el golden set {golden_set_id}.")

    agregados: List[str] = []
    ya_estaban = 0
    omitidas: List[int] = []

    with engine.begin() as conn:
        conteos = conn.execute(text("""
            SELECT Split, COUNT(*) FROM calidad.GoldenSetItems
            WHERE GoldenSetID = :sid AND IsActive = 1 GROUP BY Split
        """), {"sid": golden_set_id}).fetchall()
        por_split = {"train": 0, "test": 0}
        for nombre_split, cantidad in conteos:
            por_split[str(nombre_split)] = int(cantidad)

        filas = conn.execute(text("""
            SELECT AuditoriaID, IdAplicativo, PlantillaID
            FROM calidad.Auditorias WHERE AuditoriaID IN :ids
        """).bindparams(bindparam("ids", expanding=True)), {"ids": ids}).fetchall()
        info = {int(f[0]): {"id_aplicativo": f[1], "plantilla_id": f[2]} for f in filas}

        existentes = {
            str(f[0]) for f in conn.execute(text("""
                SELECT IdAplicativo FROM calidad.GoldenSetItems WHERE GoldenSetID = :sid
            """), {"sid": golden_set_id}).fetchall()
        }

        for auditoria_id in ids:
            datos = info.get(auditoria_id)
            if datos is None or not datos["id_aplicativo"]:
                omitidas.append(auditoria_id)
                continue
            # Un set evalúa UNA plantilla: mezclar plantillas haría que los
            # atributos de una contaminen las métricas de la otra.
            if datos["plantilla_id"] != juego["PlantillaID"]:
                omitidas.append(auditoria_id)
                continue

            id_aplicativo = str(datos["id_aplicativo"])
            if id_aplicativo in existentes:
                ya_estaban += 1
                continue

            if split is not None:
                destino = split
            else:
                # 60/40: el lado que esté más lejos de su cupo se lleva el caso.
                destino = "test" if por_split["test"] < round(
                    0.4 * (por_split["train"] + por_split["test"] + 1)
                ) else "train"
            por_split[destino] += 1

            conn.execute(text("""
                INSERT INTO calidad.GoldenSetItems
                    (GoldenSetID, IdAplicativo, AuditoriaID, Split, Notas, AgregadoPorUsuarioID)
                VALUES (:sid, :idapp, :aid, :split, :notas, :uid)
            """), {
                "sid": golden_set_id, "idapp": id_aplicativo, "aid": auditoria_id,
                "split": destino, "notas": notas, "uid": user_id,
            })
            existentes.add(id_aplicativo)
            agregados.append(id_aplicativo)

    fijados = fijar_audios(engine, agregados, fijado=True)
    return {
        "agregados": len(agregados),
        "ya_estaban": ya_estaban,
        "omitidas": omitidas,
        "audios_fijados": fijados,
    }


def quitar_item(engine: Engine, item_id: int) -> bool:
    """Saca un item del set (baja lógica) y libera su audio si no queda en otro set."""
    with engine.begin() as conn:
        fila = conn.execute(text("""
            SELECT TOP 1 IdAplicativo FROM calidad.GoldenSetItems WHERE ItemID = :iid
        """), {"iid": item_id}).first()
        if fila is None:
            return False
        conn.execute(text("UPDATE calidad.GoldenSetItems SET IsActive = 0 WHERE ItemID = :iid"),
                     {"iid": item_id})
    _liberar_audios_sin_set(engine, [fila[0]])
    return True


def repartir_split(engine: Engine, golden_set_id: int, proporcion_test: float = 0.4,
                   semilla: int = 42) -> Dict[str, int]:
    """Re-reparte TODOS los items del set entre train y test.

    Con semilla fija a propósito: el reparto tiene que ser reproducible, o dos
    corridas del evaluador sobre el mismo set no serían comparables.
    """
    if not 0.0 < proporcion_test < 1.0:
        raise ValueError("La proporción de test tiene que estar entre 0 y 1.")

    with engine.begin() as conn:
        ids = [int(f[0]) for f in conn.execute(text("""
            SELECT ItemID FROM calidad.GoldenSetItems
            WHERE GoldenSetID = :sid AND IsActive = 1 ORDER BY ItemID
        """), {"sid": golden_set_id}).fetchall()]

        random.Random(semilla).shuffle(ids)
        corte = int(round(len(ids) * proporcion_test))
        for posicion, item_id in enumerate(ids):
            conn.execute(text("UPDATE calidad.GoldenSetItems SET Split = :s WHERE ItemID = :iid"),
                         {"s": "test" if posicion < corte else "train", "iid": item_id})

    return {"test": corte, "train": len(ids) - corte}


# --------------------------------------------------------------------------- #
# Audios fijados (contra el descarte FIFO)                                     #
# --------------------------------------------------------------------------- #
def fijar_audios(engine: Engine, ids_aplicativo: Iterable, fijado: bool = True) -> int:
    """Marca/desmarca audios como fijados en TODOS los entornos.

    Sin entorno a propósito: dev y prod comparten el SQL pero tienen discos
    distintos (ver audio_store), y el set se arma una vez para los dos. Marcar
    solo el entorno actual dejaría el audio del otro expuesto al FIFO.
    Best-effort: no debe romper el alta de un item.
    """
    ids = [str(i) for i in ids_aplicativo if i is not None]
    if not ids:
        return 0
    try:
        with engine.begin() as conn:
            resultado = conn.execute(text("""
                UPDATE calidad.AudioAuditoria SET Fijado = :f WHERE IdAplicativo IN :ids
            """).bindparams(bindparam("ids", expanding=True)),
                {"f": 1 if fijado else 0, "ids": ids})
        return int(resultado.rowcount or 0)
    except Exception as e:
        logger.warning("No se pudieron %s los audios del golden set: %s",
                       "fijar" if fijado else "liberar", e)
        return 0


def _liberar_audios_sin_set(engine: Engine, ids_aplicativo: Iterable) -> None:
    """Libera solo los audios que ya no pertenecen a ningún set activo."""
    ids = [str(i) for i in ids_aplicativo if i is not None]
    if not ids:
        return
    try:
        with engine.connect() as conn:
            todavia_en_uso = {
                str(f[0]) for f in conn.execute(text("""
                    SELECT DISTINCT i.IdAplicativo
                    FROM calidad.GoldenSetItems i
                    JOIN calidad.GoldenSets gs ON gs.GoldenSetID = i.GoldenSetID AND gs.IsActive = 1
                    WHERE i.IsActive = 1 AND i.IdAplicativo IN :ids
                """).bindparams(bindparam("ids", expanding=True)), {"ids": ids}).fetchall()
            }
    except Exception as e:
        logger.warning("No se pudo verificar el uso de los audios: %s", e)
        return
    liberables = [i for i in ids if i not in todavia_en_uso]
    fijar_audios(engine, liberables, fijado=False)


# --------------------------------------------------------------------------- #
# Casos comparables (IA vs humano)                                             #
# --------------------------------------------------------------------------- #
def cargar_casos(
    engine: Engine,
    *,
    golden_set_id: Optional[int] = None,
    plantilla_id: Optional[int] = None,
    split: Optional[str] = None,
    desde: Optional[Any] = None,
    hasta: Optional[Any] = None,
) -> List[CasoEvaluado]:
    """Arma los casos IA-vs-humano desde las revisiones guardadas.

    Fuente: calidad.AuditoriaRevisionDetalles. El ValorIA sale del snapshot que
    se guardó al revisar, no de AuditoriaDetalles: si la auditoría se rehiciera,
    la comparación seguiría siendo contra lo que el humano efectivamente vio.

    Cuando una auditoría fue revisada por más de una persona se toma la revisión
    MÁS RECIENTE (una sola verdad por llamado). El acuerdo entre revisores se
    mide aparte, con `cargar_casos_por_revisor`.

    Tipo y ponderación salen de la plantilla ACTUAL, iguales para los dos lados:
    lo que se mide es el desacuerdo en las respuestas, no un cambio de pesos.
    """
    if golden_set_id is None and plantilla_id is None:
        raise ValueError("Indicá golden_set_id o plantilla_id.")

    condiciones = ["1 = 1"]
    params: Dict[str, Any] = {}
    if golden_set_id is not None:
        condiciones.append("""EXISTS (
            SELECT 1 FROM calidad.GoldenSetItems gi
            WHERE gi.GoldenSetID = :sid AND gi.IsActive = 1
              AND gi.AuditoriaID = r.AuditoriaID
              AND (:split IS NULL OR gi.Split = :split)
        )""")
        params["sid"] = golden_set_id
        params["split"] = split
    if plantilla_id is not None:
        condiciones.append("a.PlantillaID = :pid")
        params["pid"] = plantilla_id
    if desde is not None:
        condiciones.append("r.FechaRevision >= :desde")
        params["desde"] = desde
    if hasta is not None:
        condiciones.append("r.FechaRevision <= :hasta")
        params["hasta"] = hasta

    with engine.connect() as conn:
        # La versión solo se puede pedir si la migración 2026-08-06d está aplicada;
        # si no, va NULL y el evaluador agrupa todo como "sin versión".
        col_version = ("au.PlantillaVersionID"
                       if versionado._tiene_columna(conn, "Auditorias", "PlantillaVersionID")
                       else "CAST(NULL AS INT)")

        # REAUDITORÍA: `ValorIA` es el snapshot de lo que la persona TENÍA ENFRENTE
        # cuando revisó, y eso no se toca nunca (ver AuditorIA/reauditoria.py). Pero
        # si después de esa revisión el llamado se volvió a auditar con el prompt
        # nuevo, medir contra el snapshot viejo haría invisible justamente el cambio
        # que se quería medir. En ese caso —y solo en ese— se compara contra la
        # respuesta VIGENTE de AuditoriaDetalles, que es lo que dice el prompt de hoy.
        if reauditoria._existe_tabla(conn, "AuditoriaVersiones"):
            col_valor_ia = """
                CASE WHEN EXISTS (
                        SELECT 1 FROM calidad.AuditoriaVersiones v
                        WHERE v.AuditoriaID = u.AuditoriaID
                          AND v.FechaArchivado > u.FechaRevision
                     ) THEN ad.ValorResultado ELSE d.ValorIA END"""
            col_reauditado = """
                CASE WHEN EXISTS (
                        SELECT 1 FROM calidad.AuditoriaVersiones v
                        WHERE v.AuditoriaID = u.AuditoriaID
                          AND v.FechaArchivado > u.FechaRevision
                     ) THEN 1 ELSE 0 END"""
            join_detalles = """
        LEFT JOIN calidad.AuditoriaDetalles ad
               ON ad.AuditoriaID = u.AuditoriaID AND ad.AtributoID = d.AtributoID"""
        else:
            col_valor_ia = "d.ValorIA"
            col_reauditado = "CAST(0 AS BIT)"
            join_detalles = ""

        query = text(f"""
        WITH ultimas AS (
            SELECT r.RevisionID, r.AuditoriaID, r.RevisorUsuarioID, r.IdAplicativo,
                   COALESCE(r.FechaModificacion, r.FechaRevision) AS FechaRevision,
                   ROW_NUMBER() OVER (
                       PARTITION BY r.AuditoriaID
                       ORDER BY COALESCE(r.FechaModificacion, r.FechaRevision) DESC, r.RevisionID DESC
                   ) AS rn
            FROM calidad.AuditoriaRevisiones r
            JOIN calidad.Auditorias a ON a.AuditoriaID = r.AuditoriaID
            WHERE {' AND '.join(condiciones)}
        )
        SELECT u.AuditoriaID, u.RevisorUsuarioID, u.IdAplicativo,
               d.AtributoID, {col_valor_ia} AS ValorIA, d.ValorHumano, d.Motivo,
               {col_reauditado} AS Reauditado,
               at.NombreAtributo, at.TipoDato, at.Ponderacion,
               COALESCE(gi.Split, 'train') AS Split,
               {col_version} AS PlantillaVersionID
        FROM ultimas u
        JOIN calidad.AuditoriaRevisionDetalles d ON d.RevisionID = u.RevisionID
        JOIN calidad.Atributos at ON at.AtributoID = d.AtributoID
        JOIN calidad.Auditorias au ON au.AuditoriaID = u.AuditoriaID{join_detalles}
        LEFT JOIN calidad.GoldenSetItems gi
               ON gi.AuditoriaID = u.AuditoriaID AND gi.IsActive = 1
              AND gi.GoldenSetID = :sid_join
        WHERE u.rn = 1
        ORDER BY u.AuditoriaID, at.Orden, d.AtributoID
        """)
        params["sid_join"] = golden_set_id
        filas = conn.execute(query, params).mappings().all()

    return _agrupar_en_casos(filas)


def cargar_casos_por_revisor(
    engine: Engine,
    *,
    golden_set_id: Optional[int] = None,
    plantilla_id: Optional[int] = None,
) -> Dict[int, List[CasoEvaluado]]:
    """Igual que cargar_casos pero SIN colapsar revisores: cada uno con su lista.

    Es lo que alimenta el acuerdo humano-humano, el techo de la evaluación.
    """
    if golden_set_id is None and plantilla_id is None:
        raise ValueError("Indicá golden_set_id o plantilla_id.")

    condiciones = ["1 = 1"]
    params: Dict[str, Any] = {}
    if golden_set_id is not None:
        condiciones.append("""EXISTS (
            SELECT 1 FROM calidad.GoldenSetItems gi
            WHERE gi.GoldenSetID = :sid AND gi.IsActive = 1 AND gi.AuditoriaID = r.AuditoriaID
        )""")
        params["sid"] = golden_set_id
    if plantilla_id is not None:
        condiciones.append("a.PlantillaID = :pid")
        params["pid"] = plantilla_id

    with engine.connect() as conn:
        filas = conn.execute(text(f"""
            SELECT r.AuditoriaID, r.RevisorUsuarioID, r.IdAplicativo,
                   d.AtributoID, d.ValorIA, d.ValorHumano, d.Motivo,
                   at.NombreAtributo, at.TipoDato, at.Ponderacion,
                   'train' AS Split
            FROM calidad.AuditoriaRevisiones r
            JOIN calidad.Auditorias a ON a.AuditoriaID = r.AuditoriaID
            JOIN calidad.AuditoriaRevisionDetalles d ON d.RevisionID = r.RevisionID
            JOIN calidad.Atributos at ON at.AtributoID = d.AtributoID
            WHERE {' AND '.join(condiciones)}
            ORDER BY r.RevisorUsuarioID, r.AuditoriaID, at.Orden
        """), params).mappings().all()

    por_revisor: Dict[int, List[Dict[str, Any]]] = {}
    for fila in filas:
        por_revisor.setdefault(int(fila["RevisorUsuarioID"]), []).append(fila)
    return {rid: _agrupar_en_casos(fs) for rid, fs in por_revisor.items()}


def _agrupar_en_casos(filas: Iterable[Dict[str, Any]]) -> List[CasoEvaluado]:
    casos: Dict[Any, CasoEvaluado] = {}
    for fila in filas:
        clave = fila["AuditoriaID"]
        caso = casos.get(clave)
        if caso is None:
            version_id = fila.get("PlantillaVersionID")
            caso = CasoEvaluado(
                id_aplicativo=str(fila["IdAplicativo"] or ""),
                auditoria_id=int(clave) if clave is not None else None,
                split=str(fila["Split"] or "train"),
                revisor_id=int(fila["RevisorUsuarioID"]) if fila["RevisorUsuarioID"] else None,
                version_id=int(version_id) if version_id is not None else None,
                reauditado=bool(fila.get("Reauditado")),
            )
            casos[clave] = caso
        # Texto libre fuera de la medición (ver golden_metricas.TIPOS_NO_REVISABLES).
        # Se filtra al leer y no solo al escribir, para que las correcciones de
        # texto libre YA cargadas —antes de que se bloquearan— no arrastren el
        # kappa hacia abajo con desacuerdos que nunca fueron errores.
        if not es_revisable(fila["TipoDato"]):
            continue

        caso.atributos.append(ComparacionAtributo(
            atributo_id=int(fila["AtributoID"]),
            nombre=fila["NombreAtributo"],
            tipo=fila["TipoDato"],
            ponderacion=float(fila["Ponderacion"] or 0),
            valor_ia=fila["ValorIA"],
            valor_humano=fila["ValorHumano"],
            motivo=fila["Motivo"],
        ))
    return list(casos.values())


# --------------------------------------------------------------------------- #
# Cobertura                                                                    #
# --------------------------------------------------------------------------- #
def cobertura(engine: Engine, plantilla_id: int) -> Dict[str, Any]:
    """Cuánta verdad humana hay para una plantilla: sirve para decidir si ya se
    puede medir. Con menos de ~30 llamados revisados las métricas por atributo
    son ruido (un caso mueve varios puntos de accuracy)."""
    with engine.connect() as conn:
        fila = conn.execute(text("""
            SELECT COUNT(DISTINCT r.AuditoriaID) AS Auditorias,
                   COUNT(DISTINCT r.RevisorUsuarioID) AS Revisores,
                   MIN(r.FechaRevision) AS Primera,
                   MAX(r.FechaRevision) AS Ultima
            FROM calidad.AuditoriaRevisiones r
            JOIN calidad.Auditorias a ON a.AuditoriaID = r.AuditoriaID
            WHERE a.PlantillaID = :pid
        """), {"pid": plantilla_id}).mappings().first()

        total = conn.execute(text("""
            SELECT COUNT(*) FROM calidad.Auditorias WHERE PlantillaID = :pid
        """), {"pid": plantilla_id}).scalar() or 0

    datos = dict(fila) if fila else {}
    datos["AuditoriasTotales"] = int(total)
    revisadas = int(datos.get("Auditorias") or 0)
    datos["PorcentajeRevisado"] = round(100.0 * revisadas / total, 2) if total else 0.0
    return datos


def diagnostico_vigencia(
    engine: Engine, *, plantilla_id: int, golden_set_id: Optional[int] = None
) -> Dict[str, Any]:
    """¿La verdad humana sigue valiendo, o la campaña cambió el criterio abajo?

    El riesgo real de un set congelado no es que los audios sean viejos —una
    llamada de marzo sigue siendo una llamada válida— sino que el CRITERIO con el
    que se la juzgó haya cambiado. Si en mayo Calidad redefinió qué cuenta como
    "verificación de identidad", las revisiones hechas en marzo sobre ese atributo
    miden la política vieja y empujan el prompt en la dirección equivocada.

    Por eso no se vence el set entero: se compara la versión de plantilla con la
    que cada revisión sentó criterio contra la versión actual y, vía
    versionado.diff_snapshots, se marcan SOLO los atributos que cambiaron en el
    medio. Lo demás sigue siendo verdad perfectamente usable.

    Devuelve {version_actual, revisiones_por_version:[...], atributos_vencidos:[...],
    revisiones_vigentes, revisiones_vencidas}.
    """
    actual = versionado.version_actual(engine, plantilla_id)
    resultado: Dict[str, Any] = {
        "version_actual": actual,
        "revisiones_por_version": [],
        "atributos_vencidos": [],
        "revisiones_vigentes": 0,
        "revisiones_vencidas": 0,
        "sin_version": 0,
    }

    filtro_set = ""
    params: Dict[str, Any] = {"pid": plantilla_id}
    if golden_set_id is not None:
        filtro_set = """AND EXISTS (
            SELECT 1 FROM calidad.GoldenSetItems gi
            WHERE gi.GoldenSetID = :sid AND gi.IsActive = 1 AND gi.AuditoriaID = r.AuditoriaID
        )"""
        params["sid"] = golden_set_id

    try:
        with engine.connect() as conn:
            if not versionado._tiene_columna(conn, "AuditoriaRevisiones", "PlantillaVersionID"):
                return resultado
            filas = conn.execute(text(f"""
                SELECT r.PlantillaVersionID, COUNT(*) AS Revisiones,
                       MIN(r.FechaRevision) AS Primera, MAX(r.FechaRevision) AS Ultima
                FROM calidad.AuditoriaRevisiones r
                JOIN calidad.Auditorias a ON a.AuditoriaID = r.AuditoriaID
                WHERE a.PlantillaID = :pid {filtro_set}
                GROUP BY r.PlantillaVersionID
            """), params).mappings().all()
    except Exception as e:
        logger.warning("No se pudo diagnosticar la vigencia del set: %s", e)
        return resultado

    version_actual_id = actual["VersionID"] if actual else None
    snapshot_actual = None
    if version_actual_id is not None:
        detalle = versionado.obtener_version(engine, version_actual_id)
        snapshot_actual = detalle.get("snapshot") if detalle else None

    afectados_totales: Dict[int, Dict[str, Any]] = {}

    for fila in filas:
        version_id = fila["PlantillaVersionID"]
        cantidad = int(fila["Revisiones"])
        entrada: Dict[str, Any] = {
            "version_id": version_id,
            "revisiones": cantidad,
            "primera": fila["Primera"],
            "ultima": fila["Ultima"],
            "es_actual": version_id is not None and version_id == version_actual_id,
            "atributos_afectados": [],
        }

        if version_id is None:
            # Revisiones previas a la Fase 2: no se sabe con qué criterio se
            # hicieron. No se las declara vencidas (sería tirar trabajo real) pero
            # tampoco vigentes: se informan aparte para que alguien decida.
            resultado["sin_version"] += cantidad
        elif entrada["es_actual"]:
            resultado["revisiones_vigentes"] += cantidad
        else:
            vieja = versionado.obtener_version(engine, version_id)
            diff = versionado.diff_snapshots(
                vieja.get("snapshot") if vieja else None, snapshot_actual
            )
            entrada["numero"] = vieja.get("Numero") if vieja else None
            entrada["atributos_afectados"] = diff["atributos_afectados"]
            entrada["cambios_de_cabecera"] = [c["campo"] for c in diff["cabecera"]]
            if diff["atributos_afectados"] or diff["cabecera"]:
                resultado["revisiones_vencidas"] += cantidad
                for atributo_id in diff["atributos_afectados"]:
                    registro = afectados_totales.setdefault(
                        atributo_id, {"atributo_id": atributo_id, "revisiones": 0}
                    )
                    registro["revisiones"] += cantidad
            else:
                # Otra versión pero sin diferencias que afecten la auditoría
                # (p.ej. se renombró la plantilla): la verdad sigue vigente.
                resultado["revisiones_vigentes"] += cantidad

        resultado["revisiones_por_version"].append(entrada)

    # Nombre de los atributos vencidos, para que el reporte sea legible.
    if afectados_totales:
        try:
            with engine.connect() as conn:
                nombres = conn.execute(text("""
                    SELECT AtributoID, NombreAtributo FROM calidad.Atributos
                    WHERE AtributoID IN :ids
                """).bindparams(bindparam("ids", expanding=True)),
                    {"ids": list(afectados_totales)}).fetchall()
            for atributo_id, nombre in nombres:
                if int(atributo_id) in afectados_totales:
                    afectados_totales[int(atributo_id)]["nombre"] = nombre
        except Exception:
            pass

    resultado["atributos_vencidos"] = sorted(
        afectados_totales.values(), key=lambda a: a["revisiones"], reverse=True
    )
    resultado["revisiones_por_version"].sort(
        key=lambda e: (e["version_id"] is None, e["version_id"] or 0)
    )
    return resultado


def candidatas_para_revisar(
    engine: Engine, *, plantilla_id: int, cantidad: int = 20, solo_con_audio: bool = True,
    desde: Optional[Any] = None, hasta: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Auditorías sin revisar, estratificadas por resultado.

    No devuelve las últimas N: en una campaña sana casi todo da OK, y un set
    armado con las últimas 20 no tendría casi NO OK ni EC — justo los casos donde
    el prompt se rompe y donde equivocarse sale caro. Reparte el cupo en tres
    grupos (con EC, con algún NO OK, y limpias) y sortea dentro de cada uno.

    Sin `desde`/`hasta` **no hay límite temporal**: se sortea sobre toda la
    historia de la plantilla. Lo que en la práctica empuja la muestra hacia lo
    reciente es `solo_con_audio`: el store de audio es un FIFO de 5 GB, así que de
    los llamados viejos ya no queda audio. Con `solo_con_audio=False` se llega más
    atrás y se revisa contra la transcripción, que sí se conserva siempre.
    """
    cupo = max(1, cantidad // 3)
    filtro_audio = """
        AND (EXISTS (SELECT 1 FROM calidad.AudioAuditoria au
                     WHERE au.IdAplicativo = a.IdAplicativo)
             OR EXISTS (SELECT 1 FROM calidad.transcripciones t
                        WHERE t.IdAplicativo = a.IdAplicativo
                          AND (t.metadata LIKE '%"origen": "chat"%' OR a.IdAplicativo LIKE '%_CHT_%' OR a.IdAplicativo LIKE '%_chat%'))
             OR a.IdAplicativo LIKE '%_CHT_%'
             OR a.IdAplicativo LIKE '%_chat%')
    """ if solo_con_audio else ""
    filtro_fecha = ""
    if desde is not None:
        filtro_fecha += " AND a.fecha_interaccion >= :desde"
    if hasta is not None:
        filtro_fecha += " AND a.fecha_interaccion <= :hasta"
    filtro_audio += filtro_fecha

    query = text(f"""
        SELECT * FROM (
            SELECT TOP (:cupo) a.AuditoriaID, a.IdAplicativo, a.operadorUsuario,
                   a.fecha_interaccion, a.duracion_segundos, a.PuntajeFinal,
                   a.EsErrorCritico, 'EC' AS Grupo
            FROM calidad.Auditorias a
            WHERE a.PlantillaID = :pid AND a.EsErrorCritico = 1
              AND NOT EXISTS (SELECT 1 FROM calidad.AuditoriaRevisiones r
                               WHERE r.AuditoriaID = a.AuditoriaID)
              {filtro_audio}
            ORDER BY NEWID()
        ) ec
        UNION ALL
        SELECT * FROM (
            SELECT TOP (:cupo) a.AuditoriaID, a.IdAplicativo, a.operadorUsuario,
                   a.fecha_interaccion, a.duracion_segundos, a.PuntajeFinal,
                   a.EsErrorCritico, 'CON_FALLAS' AS Grupo
            FROM calidad.Auditorias a
            WHERE a.PlantillaID = :pid AND ISNULL(a.EsErrorCritico, 0) = 0
              AND a.PuntajeFinal IS NOT NULL AND a.PuntajeFinal < 100
              AND NOT EXISTS (SELECT 1 FROM calidad.AuditoriaRevisiones r
                               WHERE r.AuditoriaID = a.AuditoriaID)
              {filtro_audio}
            ORDER BY NEWID()
        ) fallas
        UNION ALL
        SELECT * FROM (
            SELECT TOP (:cupo) a.AuditoriaID, a.IdAplicativo, a.operadorUsuario,
                   a.fecha_interaccion, a.duracion_segundos, a.PuntajeFinal,
                   a.EsErrorCritico, 'LIMPIA' AS Grupo
            FROM calidad.Auditorias a
            WHERE a.PlantillaID = :pid AND ISNULL(a.EsErrorCritico, 0) = 0
              AND (a.PuntajeFinal IS NULL OR a.PuntajeFinal >= 100)
              AND NOT EXISTS (SELECT 1 FROM calidad.AuditoriaRevisiones r
                               WHERE r.AuditoriaID = a.AuditoriaID)
              {filtro_audio}
            ORDER BY NEWID()
        ) limpias
    """)

    params: Dict[str, Any] = {"pid": plantilla_id, "cupo": cupo}
    if desde is not None:
        params["desde"] = desde
    if hasta is not None:
        params["hasta"] = hasta

    with engine.connect() as conn:
        filas = conn.execute(query, params).mappings().all()
    return [dict(f) for f in filas]


# --------------------------------------------------------------------------- #
# Recomendación: qué revisar y qué reauditar                                   #
# --------------------------------------------------------------------------- #
def _atributos_medibles(conn, plantilla_id: int) -> Dict[int, Dict[str, Any]]:
    """Atributos de la plantilla que admiten veredicto humano, con sus opciones.

    Las opciones (el `enum` de Restricciones) importan para el muestreo: una
    opción que NUNCA apareció en ninguna revisión es una celda en cero, y sin
    leerlas de la plantilla sería invisible — no hay revisión que la nombre.
    """
    import json as _json

    filas = conn.execute(text("""
        SELECT AtributoID, NombreAtributo, TipoDato, Restricciones
        FROM calidad.Atributos
        WHERE PlantillaID = :pid AND IsActive = 1
        ORDER BY Orden, AtributoID
    """), {"pid": plantilla_id}).mappings().all()

    atributos: Dict[int, Dict[str, Any]] = {}
    for fila in filas:
        tipo = fila["TipoDato"]
        if not es_revisable(tipo):
            continue  # texto libre: no se mide, no se muestrea (ver golden_metricas)

        opciones: List[str] = []
        if (tipo or "").strip().lower() == TIPO_CRITICAL:
            # N/A queda afuera a propósito: `normalizar` lo unifica con "sin
            # responder" (None), así que no es una celda que se pueda llenar.
            opciones = [OK, NO_OK, EC]
        else:
            crudo = fila["Restricciones"]
            try:
                datos = _json.loads(crudo) if isinstance(crudo, str) else (crudo or {})
                if isinstance(datos, dict):
                    opciones = [str(v) for v in (datos.get("enum") or [])]
            except Exception:
                opciones = []

        atributos[int(fila["AtributoID"])] = {
            "atributo_id": int(fila["AtributoID"]),
            "nombre": fila["NombreAtributo"],
            "tipo": tipo,
            "opciones": [v for v in (normalizar(o, tipo) for o in opciones) if v],
        }
    return atributos


def cobertura_por_atributo(
    engine: Engine,
    plantilla_id: int,
    *,
    objetivo: int = golden_muestreo.OBJETIVO_POR_VALOR,
    golden_set_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Cuánta verdad humana hay POR ATRIBUTO Y POR VALOR, no por llamado.

    `cobertura` mide el volumen total y responde "¿ya se puede medir algo?". Esto
    responde la pregunta que de verdad decide si el prompt se puede corregir:
    "¿de qué criterios sabemos algo y de cuáles no sabemos nada?". Un atributo
    con 40 revisiones todas en OK sigue sin medirse.
    """
    filtro_set = ""
    params: Dict[str, Any] = {"pid": plantilla_id}
    if golden_set_id is not None:
        filtro_set = """AND EXISTS (
            SELECT 1 FROM calidad.GoldenSetItems gi
            WHERE gi.GoldenSetID = :sid AND gi.IsActive = 1 AND gi.AuditoriaID = r.AuditoriaID
        )"""
        params["sid"] = golden_set_id

    with engine.connect() as conn:
        atributos = _atributos_medibles(conn, plantilla_id)
        # Una sola verdad por llamado: si dos personas revisaron el mismo, vale la
        # más reciente (mismo criterio que cargar_casos).
        filas = conn.execute(text(f"""
            WITH ultimas AS (
                SELECT r.RevisionID, r.AuditoriaID,
                       ROW_NUMBER() OVER (
                           PARTITION BY r.AuditoriaID
                           ORDER BY COALESCE(r.FechaModificacion, r.FechaRevision) DESC,
                                    r.RevisionID DESC
                       ) AS rn
                FROM calidad.AuditoriaRevisiones r
                JOIN calidad.Auditorias a ON a.AuditoriaID = r.AuditoriaID
                WHERE a.PlantillaID = :pid {filtro_set}
            )
            SELECT d.AtributoID, d.ValorHumano, at.TipoDato
            FROM ultimas u
            JOIN calidad.AuditoriaRevisionDetalles d ON d.RevisionID = u.RevisionID
            JOIN calidad.Atributos at ON at.AtributoID = d.AtributoID
            WHERE u.rn = 1
        """), params).mappings().all()

    conteo: Dict[int, Dict[str, int]] = {}
    for fila in filas:
        atributo_id = int(fila["AtributoID"])
        if atributo_id not in atributos:
            continue
        valor = normalizar(fila["ValorHumano"], fila["TipoDato"])
        if valor is None:
            continue  # "sin responder" no es una celda a llenar
        celdas = conteo.setdefault(atributo_id, {})
        celdas[valor] = celdas.get(valor, 0) + 1

    # Las opciones declaradas que nunca aparecieron entran en cero, para que el
    # déficit las vea.
    for atributo_id, meta in atributos.items():
        celdas = conteo.setdefault(atributo_id, {})
        for opcion in meta["opciones"]:
            celdas.setdefault(opcion, 0)

    nombres = {aid: meta["nombre"] for aid, meta in atributos.items()}
    faltan = golden_muestreo.deficit_por_celda(conteo, objetivo=objetivo)

    detalle = []
    for atributo_id, meta in atributos.items():
        celdas = conteo.get(atributo_id, {})
        detalle.append({
            "atributo_id": atributo_id,
            "nombre": meta["nombre"],
            "tipo": meta["tipo"],
            "revisados": sum(celdas.values()),
            "valores": [
                {"valor": v, "revisados": n, "faltan": max(0, objetivo - n)}
                for v, n in sorted(celdas.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
            "celdas_flojas": sum(1 for v, n in celdas.items() if (atributo_id, v) in faltan),
        })
    detalle.sort(key=lambda a: (-a["celdas_flojas"], a["revisados"], a["nombre"]))

    return {
        "objetivo_por_valor": objetivo,
        "atributos": detalle,
        "celdas_flojas": golden_muestreo.celdas_flojas(faltan, nombres, objetivo=objetivo),
        "_conteo": conteo,
        "_nombres": nombres,
        "_faltan": faltan,
    }


def recomendar_para_revisar(
    engine: Engine,
    *,
    plantilla_id: int,
    cantidad: int = 20,
    solo_con_audio: bool = True,
    objetivo: int = golden_muestreo.OBJETIVO_POR_VALOR,
    pool: int = 300,
    desde: Optional[Any] = None,
    hasta: Optional[Any] = None,
) -> Dict[str, Any]:
    """Los N llamados que más le sirven a la medición, y por qué cada uno.

    Sobre el pool estratificado de `candidatas_para_revisar` (que ya garantiza
    que haya EC, fallas y limpias) corre la cobertura codiciosa de
    golden_muestreo: elige los llamados que llenan las celdas (atributo × valor)
    que hoy están flojas. El resultado trae `motivos` por fila, así Calidad no
    recibe una lista opaca sino "este aporta el primer NO OK de Verifica
    identidad".
    """
    cobertura_attr = cobertura_por_atributo(
        engine, plantilla_id, objetivo=objetivo
    )
    faltan = cobertura_attr.pop("_faltan")
    nombres = cobertura_attr.pop("_nombres")
    cobertura_attr.pop("_conteo", None)

    candidatas = candidatas_para_revisar(
        engine,
        plantilla_id=plantilla_id,
        cantidad=max(cantidad, min(pool, max(cantidad * 6, 60))),
        solo_con_audio=solo_con_audio,
        desde=desde,
        hasta=hasta,
    )
    if not candidatas:
        return {"recomendadas": [], "cobertura": cobertura_attr, "aporte": None}

    # Lo que respondió la IA en cada candidata: es el proxy sobre el que se
    # estratifica (la verdad humana todavía no existe, es lo que vamos a pedir).
    ids = [int(c["AuditoriaID"]) for c in candidatas]
    valores_por_auditoria: Dict[int, Dict[int, str]] = {}
    with engine.connect() as conn:
        filas = conn.execute(text("""
            SELECT ad.AuditoriaID, ad.AtributoID, ad.ValorResultado, at.TipoDato
            FROM calidad.AuditoriaDetalles ad
            JOIN calidad.Atributos at ON at.AtributoID = ad.AtributoID
            WHERE ad.AuditoriaID IN :ids
        """).bindparams(bindparam("ids", expanding=True)), {"ids": ids}).mappings().all()

    for fila in filas:
        valor = normalizar(fila["ValorResultado"], fila["TipoDato"])
        if valor is None:
            continue
        valores_por_auditoria.setdefault(
            int(fila["AuditoriaID"]), {}
        )[int(fila["AtributoID"])] = valor

    for cand in candidatas:
        cand["valores"] = valores_por_auditoria.get(int(cand["AuditoriaID"]), {})

    elegidas = golden_muestreo.elegir_balanceado(
        candidatas, faltan, cantidad, nombres=nombres
    )
    aporte = golden_muestreo.resumen_aporte(elegidas, faltan)

    # `valores` es insumo del algoritmo, no de la pantalla: se saca para no
    # mandar la grilla entera por la red en cada fila.
    for cand in elegidas:
        cand.pop("valores", None)

    return {"recomendadas": elegidas, "cobertura": cobertura_attr, "aporte": aporte}


def candidatas_para_reauditar(
    engine: Engine, *, plantilla_id: int, golden_set_id: Optional[int] = None,
    cantidad: int = 200,
) -> Dict[str, Any]:
    """Llamados con verdad humana cuya respuesta de la IA quedó vieja.

    Cuando se toca el prompt de un atributo, las auditorías anteriores siguen
    guardando lo que contestó la versión vieja. Medir el prompt nuevo contra esos
    ValorIA da un resultado falso: el cambio no aparece por ningún lado. La
    solución no es tirar la revisión —el veredicto humano sigue valiendo— sino
    volver a pasar la IA sobre el MISMO audio (por eso los audios del set se
    fijan) y comparar de nuevo.

    Devuelve solo los llamados donde el cambio de versión tocó de verdad algún
    atributo: si entre las dos versiones solo se renombró la plantilla, no hay
    nada que reauditar.
    """
    vacio = {"version_actual": None, "auditorias": [], "total": 0, "disponible": False}

    # OJO: se compara contra el ESTADO ACTUAL de la plantilla, no contra la última
    # versión registrada. Las versiones se crean al auditar (no al guardar en el
    # editor), así que justo después de tocar un prompt todavía no existe ninguna
    # versión con ese contenido — y es exactamente el momento en que Calidad
    # pregunta qué hay que rehacer. Con `version_actual` a secas, esa pregunta
    # devolvería "nada que reauditar", que es lo contrario de la verdad.
    try:
        with engine.connect() as conn:
            snapshot_actual = versionado.construir_snapshot(conn, plantilla_id)
    except Exception as e:
        logger.warning("No se pudo leer el estado actual de la plantilla %s: %s", plantilla_id, e)
        return vacio
    if snapshot_actual is None:
        return vacio
    hash_actual = versionado.hash_snapshot(snapshot_actual)
    actual = versionado.version_actual(engine, plantilla_id)

    filtro_set = ""
    params: Dict[str, Any] = {"pid": plantilla_id, "top": max(1, cantidad)}
    if golden_set_id is not None:
        filtro_set = """AND EXISTS (
            SELECT 1 FROM calidad.GoldenSetItems gi
            WHERE gi.GoldenSetID = :sid AND gi.IsActive = 1 AND gi.AuditoriaID = a.AuditoriaID
        )"""
        params["sid"] = golden_set_id

    try:
        with engine.connect() as conn:
            if not versionado._tiene_columna(conn, "Auditorias", "PlantillaVersionID"):
                return vacio
            filas = conn.execute(text(f"""
                SELECT TOP (:top) a.AuditoriaID, a.IdAplicativo, a.operadorUsuario,
                       a.fecha_interaccion, a.PuntajeFinal, a.EsErrorCritico,
                       a.PlantillaVersionID,
                       CASE WHEN EXISTS (SELECT 1 FROM calidad.AudioAuditoria au
                                          WHERE au.IdAplicativo = a.IdAplicativo)
                                 OR EXISTS (SELECT 1 FROM calidad.transcripciones t
                                            WHERE t.IdAplicativo = a.IdAplicativo
                                              AND (t.metadata LIKE '%"origen": "chat"%' OR a.IdAplicativo LIKE '%_CHT_%' OR a.IdAplicativo LIKE '%_chat%'))
                                 OR a.IdAplicativo LIKE '%_CHT_%'
                                 OR a.IdAplicativo LIKE '%_chat%'
                            THEN 1 ELSE 0 END AS TieneAudio
                FROM calidad.Auditorias a
                WHERE a.PlantillaID = :pid {filtro_set}
                  AND EXISTS (SELECT 1 FROM calidad.AuditoriaRevisiones r
                               WHERE r.AuditoriaID = a.AuditoriaID)
                ORDER BY a.fecha_interaccion DESC
            """), params).mappings().all()
    except Exception as e:
        logger.warning("No se pudieron listar las auditorías a reauditar: %s", e)
        return vacio

    # El diff se calcula UNA vez por versión vieja, no una por auditoría: en un
    # set de 200 llamados suele haber dos o tres versiones distintas.
    cache: Dict[Any, Dict[str, Any]] = {}

    resultado = []
    for fila in filas:
        version_id = fila["PlantillaVersionID"]
        if version_id not in cache:
            if version_id is None:
                # Auditoría anterior a la Fase 2: no se sabe con qué prompt salió.
                cache[version_id] = {"atributos": [], "sin_version": True, "vigente": False}
            else:
                vieja = versionado.obtener_version(engine, version_id)
                diff = versionado.diff_snapshots(
                    vieja.get("snapshot") if vieja else None, snapshot_actual
                )
                cache[version_id] = {
                    "atributos": diff["atributos_afectados"],
                    "numero": vieja.get("Numero") if vieja else None,
                    "sin_version": False,
                    "vigente": bool(vieja and vieja.get("Hash") == hash_actual),
                }
        info = cache[version_id]
        if info["vigente"]:
            continue  # se auditó con el prompt que está hoy en producción
        if not info["atributos"] and not info["sin_version"]:
            continue  # cambió la versión pero no lo que se le pide a la IA

        item = dict(fila)
        item["atributos_afectados"] = info["atributos"]
        item["version_numero"] = info.get("numero")
        item["motivo"] = (
            "Auditada antes de que existiera el versionado: no se sabe con qué "
            "prompt se generó."
            if info["sin_version"]
            else f"El prompt cambió en {len(info['atributos'])} atributo(s) desde la "
                 f"versión con la que se auditó."
        )
        resultado.append(item)

    nombres: Dict[int, str] = {}
    ids_attr = sorted({a for it in resultado for a in it["atributos_afectados"]})
    if ids_attr:
        try:
            with engine.connect() as conn:
                for aid, nombre in conn.execute(text("""
                    SELECT AtributoID, NombreAtributo FROM calidad.Atributos
                    WHERE AtributoID IN :ids
                """).bindparams(bindparam("ids", expanding=True)), {"ids": ids_attr}):
                    nombres[int(aid)] = nombre
        except Exception:
            pass
    for item in resultado:
        item["atributos_afectados_nombres"] = [
            nombres.get(a, str(a)) for a in item["atributos_afectados"]
        ]

    return {
        "version_actual": actual,
        "auditorias": resultado,
        "total": len(resultado),
        "disponible": True,
    }


def evaluar_aporte(
    engine: Engine,
    *,
    plantilla_id: int,
    auditoria_ids: Iterable[Any],
    objetivo: int = golden_muestreo.OBJETIVO_POR_VALOR,
) -> Dict[Any, Dict[str, Any]]:
    """De estas auditorías concretas, ¿cuáles le sirven a la medición y por qué?

    Es la versión "sobre lo que ya está en pantalla" de `recomendar_para_revisar`:
    en el listado de Auditorías Realizadas el usuario elige a mano, así que no
    corresponde una selección codiciosa secuencial (que depende del orden) sino el
    aporte de cada llamado por separado contra la grilla actual.

    Devuelve {auditoria_id: {aporte, motivos, revisada, desactualizada, ...}}.
    """
    ids = [int(a) for a in auditoria_ids if a is not None]
    if not ids:
        return {}

    cobertura_attr = cobertura_por_atributo(engine, plantilla_id, objetivo=objetivo)
    faltan = cobertura_attr["_faltan"]
    nombres = cobertura_attr["_nombres"]

    resultado: Dict[Any, Dict[str, Any]] = {}
    with engine.connect() as conn:
        tiene_version = versionado._tiene_columna(conn, "Auditorias", "PlantillaVersionID")
        col_version = "a.PlantillaVersionID" if tiene_version else "CAST(NULL AS INT)"
        filas = conn.execute(text(f"""
            SELECT a.AuditoriaID, {col_version} AS PlantillaVersionID,
                   CASE WHEN EXISTS (SELECT 1 FROM calidad.AuditoriaRevisiones r
                                      WHERE r.AuditoriaID = a.AuditoriaID)
                        THEN 1 ELSE 0 END AS Revisada
            FROM calidad.Auditorias a
            WHERE a.AuditoriaID IN :ids AND a.PlantillaID = :pid
        """).bindparams(bindparam("ids", expanding=True)),
            {"ids": ids, "pid": plantilla_id}).mappings().all()

        detalles = conn.execute(text("""
            SELECT ad.AuditoriaID, ad.AtributoID, ad.ValorResultado, at.TipoDato
            FROM calidad.AuditoriaDetalles ad
            JOIN calidad.Atributos at ON at.AtributoID = ad.AtributoID
            WHERE ad.AuditoriaID IN :ids
        """).bindparams(bindparam("ids", expanding=True)), {"ids": ids}).mappings().all()

        # Igual que en candidatas_para_reauditar: la referencia es el ESTADO ACTUAL
        # de la plantilla, no la última versión registrada. Si se editó el prompt y
        # todavía no se auditó nada con él, no existe versión con ese contenido y sin
        # embargo TODAS las auditorías previas quedaron viejas.
        try:
            snapshot_actual = versionado.construir_snapshot(conn, plantilla_id)
        except Exception:
            snapshot_actual = None

    hash_actual = versionado.hash_snapshot(snapshot_actual) if snapshot_actual else None
    hash_por_version: Dict[Any, Optional[str]] = {}

    def _vigente(version_id) -> bool:
        if hash_actual is None or version_id is None:
            # Sin versionado, o auditoría anterior a la Fase 2: no se sabe con qué
            # prompt salió, así que no se la marca. Poner el aviso en todas las
            # auditorías viejas de golpe sería ruido y nadie lo miraría. El listado
            # completo de esos casos está en la pestaña "Para reauditar".
            return True
        if version_id not in hash_por_version:
            fila_v = versionado.obtener_version(engine, version_id)
            hash_por_version[version_id] = fila_v.get("Hash") if fila_v else None
        return hash_por_version[version_id] == hash_actual

    valores: Dict[int, Dict[int, str]] = {}
    for fila in detalles:
        valor = normalizar(fila["ValorResultado"], fila["TipoDato"])
        if valor is None:
            continue
        valores.setdefault(int(fila["AuditoriaID"]), {})[int(fila["AtributoID"])] = valor

    for fila in filas:
        auditoria_id = int(fila["AuditoriaID"])
        revisada = bool(fila["Revisada"])
        celdas = [
            (aid, valor) for aid, valor in valores.get(auditoria_id, {}).items()
            if faltan.get((aid, valor), 0) > 0
        ]
        celdas.sort(key=lambda c: -faltan[c])
        # Una auditoría ya revisada no "aporta": su verdad ya está contada en la
        # grilla. Lo que puede pasarle es quedar desactualizada (ver más abajo).
        motivos = [] if revisada else [
            {
                "atributo_id": aid,
                "nombre": nombres.get(aid, str(aid)),
                "valor": valor,
                "faltaban": faltan[(aid, valor)],
            }
            for aid, valor in celdas[:3]
        ]
        desactualizada = bool(
            revisada and tiene_version and not _vigente(fila["PlantillaVersionID"])
        )
        resultado[auditoria_id] = {
            "auditoria_id": auditoria_id,
            "revisada": revisada,
            "aporte": 0 if revisada else len(celdas),
            "motivos": motivos,
            "desactualizada": desactualizada,
        }

    return resultado


# --------------------------------------------------------------------------- #
# Filtro del listado de Auditorías Realizadas                                  #
# --------------------------------------------------------------------------- #
# Quien revisa vive en "Auditorías Realizadas": es la pantalla donde escucha, ve
# la transcripción y corrige. La pantalla de Golden Set es de administración y casi
# no se abre. Por eso el trabajo de revisión (qué falta revisar, qué está en el
# set, qué quedó viejo) tiene que poder pedirse desde el listado, y no al revés.
#
# El listado sale de un stored procedure que solo filtra por empresa/campaña/
# plantilla/fecha/IdAplicativo. No se lo toca: se resuelve el conjunto de
# AuditoriaID acá y el router filtra las filas del SP contra ese conjunto.
FILTROS_REVISION = ("golden_set", "para_reauditar", "recomendadas", "revisadas", "sin_revisar")


def ids_para_filtro(
    engine: Engine,
    *,
    plantilla_id: int,
    filtro: str,
    golden_set_id: Optional[int] = None,
    limite: int = 500,
    solo_con_audio: bool = True,
) -> Dict[str, Any]:
    """Qué auditorías corresponden a un filtro de revisión, y en qué rango caen.

    El rango importa: el SP del listado necesita fechas, y quien busca "los
    llamados del Golden Set" no tiene por qué saber de cuándo son — de hecho el
    conjunto está armado a propósito para cubrir períodos distintos, así que
    cualquier rango que eligiera a mano se comería la mitad. Se devuelve el rango
    que efectivamente cubren los llamados encontrados, para que el router lo use en
    lugar del que puso el usuario.

    Devuelve {auditoria_ids, total, desde_auditoria, hasta_auditoria,
    desde_interaccion, hasta_interaccion, motivos}.
    """
    if filtro not in FILTROS_REVISION:
        raise ValueError(f"Filtro de revisión desconocido: {filtro!r}")

    motivos: Dict[int, Dict[str, Any]] = {}
    ids: List[int] = []

    if filtro == "golden_set":
        condicion_set = "AND gi.GoldenSetID = :sid" if golden_set_id is not None else ""
        params: Dict[str, Any] = {"pid": plantilla_id}
        if golden_set_id is not None:
            params["sid"] = golden_set_id
        with engine.connect() as conn:
            filas = conn.execute(text(f"""
                SELECT DISTINCT gi.AuditoriaID
                FROM calidad.GoldenSetItems gi
                JOIN calidad.GoldenSets gs ON gs.GoldenSetID = gi.GoldenSetID
                JOIN calidad.Auditorias a ON a.AuditoriaID = gi.AuditoriaID
                WHERE gi.IsActive = 1 AND gs.IsActive = 1
                  AND a.PlantillaID = :pid {condicion_set}
            """), params).fetchall()
        ids = [int(f[0]) for f in filas]

    elif filtro == "para_reauditar":
        datos = candidatas_para_reauditar(
            engine, plantilla_id=plantilla_id, golden_set_id=golden_set_id, cantidad=limite
        )
        for item in datos["auditorias"]:
            auditoria_id = int(item["AuditoriaID"])
            ids.append(auditoria_id)
            motivos[auditoria_id] = {
                "motivo": item["motivo"],
                "atributos": item.get("atributos_afectados_nombres") or [],
                "version_numero": item.get("version_numero"),
            }

    elif filtro == "recomendadas":
        datos = recomendar_para_revisar(
            engine, plantilla_id=plantilla_id, cantidad=limite,
            solo_con_audio=solo_con_audio,
        )
        for item in datos["recomendadas"]:
            auditoria_id = int(item["AuditoriaID"])
            ids.append(auditoria_id)
            motivos[auditoria_id] = {
                "aporte": item.get("aporte", 0),
                "motivos": item.get("motivos") or [],
            }

    else:  # revisadas | sin_revisar
        operador = "EXISTS" if filtro == "revisadas" else "NOT EXISTS"
        with engine.connect() as conn:
            filas = conn.execute(text(f"""
                SELECT TOP (:top) a.AuditoriaID
                FROM calidad.Auditorias a
                WHERE a.PlantillaID = :pid
                  AND {operador} (SELECT 1 FROM calidad.AuditoriaRevisiones r
                                   WHERE r.AuditoriaID = a.AuditoriaID)
                ORDER BY a.AuditoriaID DESC
            """), {"pid": plantilla_id, "top": max(1, limite)}).fetchall()
        ids = [int(f[0]) for f in filas]

    resultado: Dict[str, Any] = {
        "auditoria_ids": ids,
        "total": len(ids),
        # Los filtros de volumen (sin_revisar / revisadas) están topeados: sin el
        # aviso, "hay 500 sin revisar" se leería como el total real de la campaña.
        "truncado": len(ids) >= limite,
        "desde_auditoria": None, "hasta_auditoria": None,
        "desde_interaccion": None, "hasta_interaccion": None,
        "motivos": motivos,
    }
    if not ids:
        return resultado

    with engine.connect() as conn:
        rango = conn.execute(text("""
            SELECT MIN(a.FechaAuditoria) AS DesdeAud, MAX(a.FechaAuditoria) AS HastaAud,
                   MIN(a.fecha_interaccion) AS DesdeInt, MAX(a.fecha_interaccion) AS HastaInt
            FROM calidad.Auditorias a
            WHERE a.AuditoriaID IN :ids
        """).bindparams(bindparam("ids", expanding=True)), {"ids": ids}).mappings().first()

    if rango:
        resultado["desde_auditoria"] = rango["DesdeAud"]
        resultado["hasta_auditoria"] = rango["HastaAud"]
        resultado["desde_interaccion"] = rango["DesdeInt"]
        resultado["hasta_interaccion"] = rango["HastaInt"]
    return resultado
