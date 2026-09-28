"""Recupera auditorías batch que se perdieron, sin repetir las que sí se guardaron.

POR QUÉ
-------
Hay dos formas en que una auditoría programada o manual en Batch se pierde sin que
nadie la vuelva a pedir:

1. Gemini cierra el lote en SUCCEEDED pero con las respuestas en error. Pasó el
   2026-09-15: los 9 lotes creados ese día tardaron 10-17 h en volver y casi todas sus
   respuestas traían `[7] The caller does not have permission` (los que usaban caché de
   contexto) o `[13] Internal error encountered` (los que no). No hay nada que guardar:
   `Auditor.procesar_batch` relee el mismo resultado 5 veces y abandona el lote.
2. La corrida muere ANTES de llegar a Gemini (p. ej. el login de CXOne) y la tarea
   programada queda igual como "finalizada con éxito".

En el caso 1 lo único que se perdió es la respuesta. La metadata de cada interacción
sigue en `calidad.Batch_data` (se limpia días después) y el audio comprimido quedó en
el store de audio conservado, que se escribe al ENVIAR el lote y no al recibirlo (ver
`AuditorIA/audio_store.py`). Con eso se puede volver a armar el mismo pedido.

QUÉ HACE
--------
`lotes BATCH_ID...` — por cada lote:
  1. lee el resultado en Gemini y se queda con las interacciones que volvieron SIN
     respuesta (de un lote parcial, las que se guardaron no se tocan);
  2. rearma el DataFrame original desde calidad.Batch_data, en el orden de columnas
     original, sin las columnas que agrega process_batch (esas las vuelve a poner);
  3. descarta las que ya están auditadas con esa plantilla o viajando en otro lote
     todavía sin guardar (incluidos los que este mismo script mandó antes);
  4. consigue el audio: el archivo original si sigue en disco; si no, una copia del
     audio conservado;
  5. lo manda por el camino normal (`gemini.calidad_batch`) con los mismos destinos
     (Sheets, destinatarios del mail, plantilla de columnas, tarea). Cuando vuelve,
     procesar_batch lo guarda, exporta y avisa como a cualquier corrida.

`scheduler ID:AAAA-MM-DD[:AAAA-MM-DD]...` — vuelve a correr una tarea programada (en
Batch) para un día puntual. Arma los parámetros igual que
`app/tasks.py::_procesar_tarea_individual`, pero con la fecha fija y sin tocar
next_run_time ni AuditSchedulerHistory. Correr ESTE modo antes que `lotes` si los dos
apuntan a la misma plantilla y fecha: así el paso 3 de `lotes` ve lo que ya salió.

Sin `--enviar` solo muestra lo que haría (no gasta tokens ni escribe nada).

Por defecto los pedidos salen SIN caché de contexto: una corrida de recuperación es
chica y lo que importa es que salga (el 15/09 fallaron el 100% de los pedidos con
caché). `--con-cache` usa el comportamiento normal.

Correr en el servidor donde corrió el lote (el audio está en SU disco):
    cd backend && ../venv/bin/python ../scripts/recuperar_batches_fallidos.py scheduler 13:2026-09-14 --enviar
    cd backend && ../venv/bin/python ../scripts/recuperar_batches_fallidos.py lotes \
        batches/abc batches/def --transcribir batches/def --enviar
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

# Columnas que agrega gemini.process_batch al guardar el lote. No son de la interacción:
# si se dejan, entran al prompt como "Call_details" y además process_batch las pisa.
COLUMNAS_DEL_LOTE = {
    "gemini_file_name", "user_id", "plantilla_id", "gsheet_id", "gsheet_name",
    "email_destinatarios", "column_template_id", "modelo_ia", "plantilla_version_id",
    "prompt_nombre_id_map",
}

# Alias con los que calcular_id_aplicativo arma la clave (en minúsculas). Solo hace falta
# traer estas columnas de los lotes en vuelo para saber qué interacciones llevan.
COLUMNAS_CLAVE = (
    "id_aplicativo", "idaplicativo", "idinteraccion", "connid", "ucid_segmento",
    "segmentid", "id", "caso_id", "id del llamado", "segmento",
)


def _vacio_a_none(valor: Optional[str]) -> Optional[str]:
    return valor if valor not in (None, "") else None


def _dataframe_de_batch_data(conn, batch_id: str, columnas: Optional[tuple] = None):
    """Rearma el DataFrame del lote: una fila por segment_id, columnas en el orden en que
    se guardaron (el melt de process_batch las escribe columna por columna)."""
    import pandas as pd
    from sqlalchemy import bindparam, text

    sql = "SELECT id, segment_id, columna, valor FROM calidad.Batch_data WHERE Batch_id = :b"
    params: Dict[str, Any] = {"b": batch_id}
    if columnas:
        sql += " AND LOWER(columna) IN :cols"
    q = text(sql + " ORDER BY id")
    if columnas:
        q = q.bindparams(bindparam("cols", expanding=True))
        params["cols"] = list(columnas)

    filas: Dict[int, Dict[str, Any]] = {}
    orden_columnas: List[str] = []
    for _id, segmento, columna, valor in conn.execute(q, params).fetchall():
        if columna not in orden_columnas:
            orden_columnas.append(columna)
        filas.setdefault(int(segmento), {})[columna] = valor
    if not filas:
        return pd.DataFrame()
    df = pd.DataFrame.from_dict(filas, orient="index")
    return df[[c for c in orden_columnas if c in df.columns]].sort_index()


def _ids_ya_cubiertos(engine, plantilla_id: int, ids: List[str], excluir_batch: str) -> Dict[str, str]:
    """{IdAplicativo: motivo} de las interacciones que no hay que volver a mandar: ya
    auditadas con esta plantilla, o dentro de otro lote de la misma plantilla que
    todavía no se guardó (en vuelo, o terminado y sin procesar)."""
    from sqlalchemy import bindparam, text
    from AuditorIA.sql_a_Claude import calcular_id_aplicativo

    cubiertos: Dict[str, str] = {}
    if not ids:
        return cubiertos
    buscados = set(ids)
    with engine.connect() as conn:
        for i in range(0, len(ids), 1000):
            q = text("""
                SELECT DISTINCT IdAplicativo FROM calidad.Auditorias
                WHERE PlantillaID = :p AND IdAplicativo IN :ids
            """).bindparams(bindparam("ids", expanding=True))
            for (id_ap,) in conn.execute(q, {"p": plantilla_id, "ids": ids[i:i + 1000]}).fetchall():
                cubiertos[str(id_ap)] = "ya auditada"

        en_vuelo = conn.execute(text("""
            SELECT b.name FROM calidad.BatchJobs b
            WHERE b.procesado_at IS NULL
              AND b.status IN ('JOB_STATE_PENDING', 'JOB_STATE_RUNNING', 'JOB_STATE_SUCCEEDED')
              AND b.name <> :excluir
              AND EXISTS (SELECT 1 FROM calidad.Batch_data d
                          WHERE d.Batch_id = b.name AND d.columna = 'plantilla_id' AND d.valor = :p)
        """), {"excluir": excluir_batch, "p": str(plantilla_id)}).fetchall()
        for (otro,) in en_vuelo:
            df_otro = _dataframe_de_batch_data(conn, otro, COLUMNAS_CLAVE)
            if df_otro.empty:
                continue
            for id_ap in calcular_id_aplicativo(df_otro).astype(str):
                if id_ap in buscados and id_ap not in cubiertos:
                    cubiertos[id_ap] = f"viajando en {otro}"
    return cubiertos


def _lotes_en_cola(engine) -> int:
    """Lotes esperando cupo en calidad.BatchPendientes. Su metadata todavía no está en
    Batch_data, así que la deduplicación de este script no los ve."""
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            return int(conn.execute(text(
                "SELECT COUNT(*) FROM calidad.BatchPendientes WHERE Estado IN ('PENDIENTE', 'ENVIANDO')"
            )).scalar() or 0)
    except Exception:
        return 0


def recuperar_lote(auditor, batch_id: str, *, transcribir: bool, enviar: bool, carpeta_tmp: str) -> None:
    from sqlalchemy import text
    from AuditorIA import audio_store, batch_cola
    from AuditorIA.execution_log import obtener_contexto
    from AuditorIA.gemini import calidad_batch
    from AuditorIA.sql_a_Claude import calcular_id_aplicativo

    engine = auditor.engine
    print(f"\n=== {batch_id}")

    contexto = obtener_contexto(engine, batch_id=batch_id) or {}
    if contexto:
        print(f"    corrida original: {contexto.get('trigger_source')} "
              f"{contexto.get('scheduler_name') or contexto.get('task_id') or ''} | "
              f"estado {contexto.get('status')} | auditadas {contexto.get('filas_auditadas')}")

    # 1. Qué interacciones volvieron sin respuesta.
    job = auditor.gemini.batches.get(name=batch_id)
    estado = job.state.name if job.state else "?"
    if estado != "JOB_STATE_SUCCEEDED":
        print(f"    el lote está en {estado}: se recupera solo un lote terminado en SUCCEEDED.")
        return
    con_respuesta: Set[int] = {seg for seg, r in batch_cola.leer_respuestas(auditor.gemini, job)
                               if r.response is not None}

    with engine.connect() as conn:
        df = _dataframe_de_batch_data(conn, batch_id)
    if df.empty:
        print("    calidad.Batch_data ya no tiene la metadata del lote: no se puede recuperar.")
        return
    total = len(df)
    df = df[~df.index.isin(con_respuesta)]
    print(f"    {total} interacciones en el lote, {len(con_respuesta)} con respuesta, {len(df)} sin respuesta.")
    if df.empty:
        return

    # 2. Destinos y plantilla, tal como viajaron con el lote.
    primera = df.iloc[0]
    plantilla_id = int(primera["plantilla_id"])
    user_id = primera.get("user_id")
    user_id = int(user_id) if str(user_id).isdigit() else user_id
    gsheet_id = _vacio_a_none(primera.get("gsheet_id"))
    gsheet_name = _vacio_a_none(primera.get("gsheet_name"))
    email_destinatarios = _vacio_a_none(primera.get("email_destinatarios"))
    column_template_id = _vacio_a_none(primera.get("column_template_id"))
    column_template_id = int(column_template_id) if column_template_id else None
    df = df.drop(columns=[c for c in COLUMNAS_DEL_LOTE if c in df.columns]).reset_index(drop=True)

    # 3. Nada de duplicados.
    ids = calcular_id_aplicativo(df).astype(str)
    cubiertos = _ids_ya_cubiertos(engine, plantilla_id, list(dict.fromkeys(ids)), batch_id)
    repetidas = ids.duplicated()
    descartar = ids.isin(list(cubiertos)) | repetidas
    for motivo in sorted(set(cubiertos.values())):
        n = sum(1 for i in ids if cubiertos.get(i) == motivo)
        print(f"    se descartan {n}: {motivo}")
    if repetidas.any():
        print(f"    se descartan {int(repetidas.sum())}: repetidas dentro del mismo lote")
    df, ids = df[~descartar].reset_index(drop=True), ids[~descartar].reset_index(drop=True)

    # 4. Audio.
    origen = {"original": 0, "conservado": 0, "sin audio": 0}
    rutas = []
    for i, fila in df.iterrows():
        ruta = fila.get("audio_dir")
        if isinstance(ruta, str) and ruta and os.path.exists(ruta):
            origen["original"] += 1
            rutas.append(ruta)
            continue
        conservado = audio_store.resolver_audio(engine, ids[i])
        if conservado:
            # Copia: preparar_contenido deja un .clean.ogg al lado del archivo que
            # recibe, y no hay que ensuciar la carpeta del store con archivos sin índice.
            copia = os.path.join(carpeta_tmp, f"{uuid.uuid4().hex}.ogg")
            shutil.copyfile(conservado[0], copia)
            origen["conservado"] += 1
            rutas.append(copia)
        else:
            origen["sin audio"] += 1
            rutas.append(None)
    df["audio_dir"] = rutas
    sin_audio = df["audio_dir"].isna()
    print(f"    audio: {origen}")
    df = df[~sin_audio].reset_index(drop=True)

    print(f"    a reenviar: {len(df)} | plantilla {plantilla_id} | usuario {user_id} | "
          f"transcribir {transcribir} | sheet {gsheet_name or '-'} | mail {email_destinatarios or '-'} | "
          f"columnas {column_template_id or '-'}")
    if df.empty or not enviar:
        return

    contexto_ejecucion = {
        "trigger_source": contexto.get("trigger_source") or "manual",
        "scheduler_id": contexto.get("scheduler_id"),
        "scheduler_name": contexto.get("scheduler_name"),
        "task_id": contexto.get("task_id"),
        "fecha_desde": contexto.get("fecha_desde"),
        "fecha_hasta": contexto.get("fecha_hasta"),
        "cantidad_solicitada": len(df),
        "por_operador": bool(contexto.get("por_operador")),
        "por_tipificacion": bool(contexto.get("por_tipificacion")),
        "run_id": str(uuid.uuid4()),
    }
    resultado = calidad_batch(
        engine=engine, df=df, plantilla_id=plantilla_id, gemini_api=auditor.gemini,
        user_id=user_id, gsheet_id=gsheet_id, gsheet_name=gsheet_name,
        column_template_id=column_template_id, contexto_ejecucion=contexto_ejecucion,
        transcribir=transcribir, email_destinatarios=email_destinatarios,
    )
    if resultado:
        # La tarea manual vuelve a "en cola": cuando el lote nuevo se guarde,
        # _procesar_grupo_batch la pasa a completed como a cualquier otra.
        auditor._actualizar_estado_task(contexto.get("task_id"), "en_cola")
        print(f"    ENVIADO: {resultado} (run_id {contexto_ejecucion['run_id']})")
    else:
        print("    FALLÓ el envío: ningún lote salió ni quedó en cola.")


def correr_scheduler(auditor, spec: str, *, enviar: bool) -> None:
    from sqlalchemy import text
    from app.utils.tipificaciones import preparar_tipificacion_para_consulta

    engine = auditor.engine
    partes = spec.split(":")
    scheduler_id = int(partes[0])
    fecha_desde = datetime.strptime(partes[1], "%Y-%m-%d")
    fecha_hasta = datetime.strptime(partes[2], "%Y-%m-%d") if len(partes) > 2 else fecha_desde

    with engine.connect() as conn:
        tarea = conn.execute(text("SELECT * FROM calidad.AuditSchedulers WHERE id = :id"),
                             {"id": scheduler_id}).mappings().first()
    print(f"\n=== scheduler {spec}")
    if not tarea:
        print("    no existe.")
        return
    tarea = dict(tarea)
    params_extra = json.loads(tarea["parametros_json"] or "{}")
    if not params_extra.get("is_batch"):
        print(f"    '{tarea['task_name']}' no corre en Batch: este script solo recupera tareas Batch.")
        return

    # Mismo armado que app/tasks.py::_procesar_tarea_individual.
    audit_params = {
        "plantilla_id": tarea["plantilla_id"],
        "user_id": tarea["created_by"],
        "cantidad": tarea["cantidad"],
        "empresa": tarea["empresa"],
        "campana": tarea["campana"],
        "Fecha_desde": fecha_desde,
        "Fecha_hasta": fecha_hasta,
        "loginid": params_extra.get("loginid"),
        "sentido": params_extra.get("sentido"),
        "tipificacion": preparar_tipificacion_para_consulta(
            engine=engine, tipificacion=params_extra.get("tipificacion"), campana=tarea["campana"],
        ),
        "duracion_min": params_extra.get("duracion_min"),
        "duracion_max": params_extra.get("duracion_max"),
        "reauditar": params_extra.get("reauditar", False),
        "por_operador": params_extra.get("por_operador", False),
        "por_tipificacion": params_extra.get("por_tipificacion", False),
        "omitir_limite": True,
    }
    destinos = {
        "gsheet_id": tarea["gsheet_id"] if tarea["send_gsheets"] else None,
        "gsheet_name": tarea["gsheet_name"] if tarea["send_gsheets"] else None,
        "column_template_id": tarea.get("column_template_id"),
        "email_destinatarios": tarea["email_addresses"] if tarea["send_email"] else None,
    }
    print(f"    '{tarea['task_name']}' | plantilla {tarea['plantilla_id']} | cantidad {tarea['cantidad']} | "
          f"{fecha_desde:%Y-%m-%d} a {fecha_hasta:%Y-%m-%d} | sheet {destinos['gsheet_name'] or '-'}")
    if not enviar:
        return

    resultado = auditor.run_batch(
        **audit_params, **destinos,
        trigger_source="scheduler", scheduler_id=scheduler_id, scheduler_name=tarea["task_name"],
    )
    if resultado is None:
        print("    FALLÓ: la corrida no encoló nada (ver el log de arriba).")
    elif not resultado:
        print("    no había nada para auditar (sin llamados o ya auditados).")
    else:
        print(f"    ENVIADO: {resultado}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="modo", required=True)
    p_lotes = sub.add_parser("lotes", help="reenviar lo que volvió sin respuesta de lotes terminados")
    p_lotes.add_argument("batch_ids", nargs="+")
    p_lotes.add_argument("--transcribir", nargs="*", default=[],
                         help="lotes que habían pedido transcripción (no queda registrado en Batch_data)")
    p_sched = sub.add_parser("scheduler", help="correr tareas programadas Batch para una fecha fija")
    p_sched.add_argument("specs", nargs="+", help="ID:AAAA-MM-DD o ID:AAAA-MM-DD:AAAA-MM-DD")
    for p in (p_lotes, p_sched):
        p.add_argument("--enviar", action="store_true", help="enviar de verdad (sin esto, solo muestra)")
        p.add_argument("--con-cache", action="store_true", help="usar la caché de contexto de la plantilla")
    args = parser.parse_args()

    if not args.con_cache:
        # Antes de importar la config: una variable de entorno le gana al .env.
        os.environ["GEMINI_CACHE_PLANTILLAS"] = "false"

    from app.database import engine
    from Auditor import AuditorIA

    auditor = AuditorIA(engine=engine)
    if not args.enviar:
        print("(simulación: agregá --enviar para mandar de verdad)")

    if args.modo == "scheduler":
        for spec in args.specs:
            correr_scheduler(auditor, spec, enviar=args.enviar)
        return

    en_cola = _lotes_en_cola(engine)
    if en_cola:
        print(f"ATENCIÓN: hay {en_cola} lote(s) esperando cupo en calidad.BatchPendientes; "
              f"la deduplicación no los ve.")
    carpeta_tmp = tempfile.mkdtemp(prefix="recuperacion_batch_")
    try:
        for batch_id in args.batch_ids:
            try:
                recuperar_lote(auditor, batch_id, transcribir=batch_id in args.transcribir,
                               enviar=args.enviar, carpeta_tmp=carpeta_tmp)
            except Exception as e:
                print(f"    ERROR recuperando {batch_id}: {type(e).__name__}: {e}")
    finally:
        shutil.rmtree(carpeta_tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
