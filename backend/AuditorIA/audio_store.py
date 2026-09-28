"""Conservación en disco del audio ya auditado (para reproducir/descargar después).

Tras auditar, el audio ya viene comprimido a Opus mono 16kHz (bitrate
settings.AUDIO_OPUS_BITRATE, default 32k ≈ 240 KB/min):
los físicos como `<algo>.clean.ogg` (creado por gemini_files.convertir_a_mp3_estandar
al subir a Gemini) y los de memoria como BytesIO Opus (get_audio_bytes._comprimir_audios_en_memoria).
Este módulo copia ESE audio comprimido a un store en disco, keyeado por IdAplicativo
—la misma clave que liga calidad.Auditorias y calidad.transcripciones—, y lleva un
índice de metadatos en calidad.AudioAuditoria para servirlo, chequear disponibilidad y
descartar los más viejos cuando se supera el tope (FIFO).

Aislamiento dev/prod: dev y prod comparten el SQL pero corren en servidores/discos
distintos, así que TODO filtra por settings.ENVIRONMENT (columna Entorno). Una fila de
'dev' apunta a un archivo que solo existe en el disco de dev.

Nunca debe interrumpir una auditoría real: toda función atrapa sus excepciones, loguea y
devuelve un valor neutro (False/None/set()/no-op) en vez de propagar.
"""
import hashlib
import io
import logging
import os
import subprocess
import tempfile
from typing import Iterable, Optional, Tuple, Union

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from app.config import settings
from AuditorIA.gemini_files import convertir_a_mp3_estandar
from AuditorIA.sql_a_Claude import calcular_id_aplicativo

logger = logging.getLogger(__name__)

# Extensión/mime del audio conservado (Opus en contenedor Ogg).
_EXT = ".ogg"
_MIME = "audio/ogg"


def _entorno() -> str:
    return getattr(settings, "ENVIRONMENT", "prod") or "prod"


def _dir_entorno() -> str:
    """Carpeta del store para el entorno actual, creada si no existe."""
    ruta = os.path.join(settings.AUDIO_STORE_DIR, _entorno())
    os.makedirs(ruta, exist_ok=True)
    return ruta


def _nombre_archivo(entorno: str, id_aplicativo: str) -> str:
    """Nombre en disco determinístico y filesystem-safe (hash de entorno+id)."""
    clave = f"{entorno}|{id_aplicativo}".encode("utf-8")
    return hashlib.sha1(clave).hexdigest() + _EXT


def duracion_segundos(source: Union[str, io.BytesIO]) -> Optional[float]:
    """Duración REAL del audio en segundos vía ffprobe (o None si no se pudo medir).

    Sirve como fuente de verdad para corregir los timestamps que Gemini ESTIMA en la
    transcripción (ver Trancribir._ajustar_tiempos_segments). La compresión a Opus no
    cambia la duración, así que probar el original o el comprimido da lo mismo. Acepta
    ruta física o BytesIO (por pipe)."""
    base = ['/usr/bin/ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1']
    tmp_path = None
    try:
        if isinstance(source, io.BytesIO):
            # ffprobe por pipe no puede leer la duración de Ogg/Opus (necesita seek al
            # final para el granule position), así que el BytesIO se vuelca a un temporal.
            source.seek(0)
            datos = source.read()
            source.seek(0)
            if not datos:
                return None
            fd, tmp_path = tempfile.mkstemp(suffix=_EXT)
            with os.fdopen(fd, 'wb') as f:
                f.write(datos)
            ruta = tmp_path
        else:
            if not isinstance(source, str) or not source or source.endswith('.json') or not os.path.exists(source):
                return None
            ruta = source

        proc = subprocess.run(base + ['-i', ruta],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        salida = proc.stdout.decode('utf-8', 'ignore').strip()
        if not salida:
            return None
        dur = float(salida)
        return dur if dur > 0 else None
    except Exception as e:
        logger.debug("No se pudo medir la duración con ffprobe: %s", e)
        return None
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def duracion_de(engine: Engine, id_aplicativo: str) -> Optional[float]:
    """Duración guardada del audio de una interacción (calidad.AudioAuditoria) en el
    entorno actual, o None. La usa el path batch, donde el df de transcripción ya no
    trae el audio pero el store lo guardó (con su duración) al subir a Gemini."""
    if not id_aplicativo:
        return None
    try:
        with engine.connect() as conn:
            fila = conn.execute(text("""
                SELECT TOP 1 DuracionSegundos
                FROM calidad.AudioAuditoria
                WHERE Entorno = :e AND IdAplicativo = :id
            """), {"e": _entorno(), "id": str(id_aplicativo)}).first()
        return float(fila[0]) if fila and fila[0] is not None else None
    except Exception as e:
        logger.debug("No se pudo leer la duración guardada de %s: %s", id_aplicativo, e)
        return None


def _bytes_comprimidos(source: Union[str, io.BytesIO]) -> Optional[bytes]:
    """Devuelve los bytes del audio YA comprimido a Opus, o None si no aplica.

    - BytesIO: ya viene Opus desde la descarga en memoria -> se usa tal cual.
    - Ruta física: se reutiliza/crea el `.clean.ogg` (idempotente; si ya existe de
      la subida a Gemini, no recodifica). Si la conversión falló (devuelve el
      original sin comprimir), se descarta para no reventar el tope con WAV crudos.
    - Chats `.json` o audio inexistente: None.
    """
    try:
        if isinstance(source, io.BytesIO):
            source.seek(0)
            datos = source.read()
            source.seek(0)
            return datos or None

        if not isinstance(source, str) or not source:
            return None
        if source.endswith(".json"):
            return None  # chat, no hay audio
        if not os.path.exists(source):
            return None

        ruta_comprimida = convertir_a_mp3_estandar(source)
        # convertir_a_mp3_estandar devuelve el `.clean.ogg` si comprimió, o el
        # original si algo falló. Solo conservamos el comprimido.
        if not ruta_comprimida or not ruta_comprimida.endswith(_EXT):
            logger.warning("Audio no comprimido (se omite del store): %s", source)
            return None
        with open(ruta_comprimida, "rb") as f:
            return f.read()
    except Exception as e:
        logger.warning("No se pudo obtener el audio comprimido de %r: %s", source, e)
        return None


def guardar_audio(engine: Engine, id_aplicativo: str, source: Union[str, io.BytesIO]) -> bool:
    """Conserva el audio comprimido de una interacción en disco + índice de metadatos.

    Idempotente por (Entorno, IdAplicativo): en re-auditoría reescribe el archivo y
    actualiza tamaño/fecha. Best-effort: cualquier fallo se loguea y devuelve False.
    """
    if id_aplicativo is None:
        return False
    id_aplicativo = str(id_aplicativo)

    datos = _bytes_comprimidos(source)
    if not datos:
        return False

    entorno = _entorno()
    nombre = _nombre_archivo(entorno, id_aplicativo)
    ruta_final = os.path.join(_dir_entorno(), nombre)
    ruta_tmp = ruta_final + ".tmp"

    try:
        # Escritura atómica: tmp + replace, para no servir un archivo a medio escribir.
        with open(ruta_tmp, "wb") as f:
            f.write(datos)
        os.replace(ruta_tmp, ruta_final)
    except Exception as e:
        logger.warning("No se pudo escribir el audio %s: %s", id_aplicativo, e)
        try:
            if os.path.exists(ruta_tmp):
                os.remove(ruta_tmp)
        except OSError:
            pass
        return False

    # Duración real (ffprobe) del archivo ya escrito: fuente de verdad para corregir los
    # timestamps estimados de la transcripción. Best-effort: si falla, queda NULL.
    dur = duracion_segundos(ruta_final)

    try:
        with engine.begin() as conn:
            conn.execute(text("""
                MERGE calidad.AudioAuditoria WITH (HOLDLOCK) AS destino
                USING (SELECT :entorno AS Entorno, :id AS IdAplicativo) AS origen
                    ON destino.Entorno = origen.Entorno
                   AND destino.IdAplicativo = origen.IdAplicativo
                WHEN MATCHED THEN
                    UPDATE SET NombreArchivo = :nombre, FormatoMime = :mime,
                               TamanioBytes = :tam, DuracionSegundos = :dur,
                               FechaCreacion = SYSUTCDATETIME()
                WHEN NOT MATCHED THEN
                    INSERT (Entorno, IdAplicativo, NombreArchivo, FormatoMime, TamanioBytes, DuracionSegundos)
                    VALUES (:entorno, :id, :nombre, :mime, :tam, :dur);
            """), {
                "entorno": entorno,
                "id": id_aplicativo,
                "nombre": nombre,
                "mime": _MIME,
                "tam": len(datos),
                "dur": dur,
            })
        return True
    except Exception as e:
        logger.warning("No se pudo registrar el audio %s en la BD: %s", id_aplicativo, e)
        # Evitar archivo huérfano (no quedaría indexado ni sería descartado por el tope).
        try:
            os.remove(ruta_final)
        except OSError:
            pass
        return False


def persistir_audios_df(engine: Engine, df) -> int:
    """Conserva el audio de cada fila de `df` que traiga audio válido en `audio_dir`.

    Punto único que usan el path sync (Auditor.run) y el batch (gemini.process_batch):
    ambos llegan con el df que todavía tiene `audio_dir` (ruta o BytesIO) y las columnas
    de id. Al final aplica el tope (descarte FIFO). Devuelve cuántos audios conservó.
    """
    try:
        if df is None or getattr(df, "empty", True) or "audio_dir" not in df.columns:
            return 0
        ids = calcular_id_aplicativo(df)
        guardados = 0
        for idx in df.index:
            id_aplicativo = ids.get(idx) if hasattr(ids, "get") else ids[idx]
            if id_aplicativo is None:
                continue
            # Solo se descarta NaN (float que no es igual a sí mismo); un id numérico
            # válido sí se conserva. Se usa str() sobre el MISMO valor que produce
            # calcular_id_aplicativo (la clave con la que se guarda la auditoría), así
            # el audio queda bajo la misma IdAplicativo y el lookup del front la encuentra.
            if isinstance(id_aplicativo, float) and id_aplicativo != id_aplicativo:
                continue
            source = df.at[idx, "audio_dir"]
            if source is None:
                continue
            if guardar_audio(engine, str(id_aplicativo), source):
                guardados += 1
    except Exception as e:
        logger.warning("Fallo conservando audios del df: %s", e)
        return 0

    try:
        aplicar_tope(engine)
    except Exception as e:
        logger.warning("Fallo aplicando el tope del store de audio: %s", e)
    return guardados


def _tiene_columna_fijado(conn) -> bool:
    """¿Está aplicada la migración 2026-08-06c (calidad.AudioAuditoria.Fijado)?

    Se chequea antes de filtrar por la columna: si no existe, el barrido sigue
    funcionando como antes (sin pin) en vez de fallar y dejar el store sin tope.
    """
    try:
        return conn.execute(text("""
            SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = 'calidad' AND TABLE_NAME = 'AudioAuditoria'
              AND COLUMN_NAME = 'Fijado'
        """)).first() is not None
    except Exception:
        return False


def aplicar_tope(engine: Engine) -> None:
    """Descarte FIFO: si el total del entorno supera el tope, borra los más antiguos
    (archivo + fila) hasta quedar por debajo. Best-effort.

    Los audios FIJADOS (los que están en un Golden Set, ver AuditorIA/golden_set.py)
    quedan afuera: ni se borran ni cuentan para el tope. Sin esto, a las pocas
    semanas el set dorado se quedaría sin audios y no se podría re-auditar —
    justamente lo que le da sentido a congelar un set.
    """
    tope = int(getattr(settings, "AUDIO_STORE_MAX_BYTES", 100 * 1024 ** 3))
    entorno = _entorno()

    with engine.connect() as conn:
        filtro_fijado = "AND Fijado = 0" if _tiene_columna_fijado(conn) else ""

        total = conn.execute(text(
            f"""SELECT COALESCE(SUM(TamanioBytes), 0) FROM calidad.AudioAuditoria
                WHERE Entorno = :e {filtro_fijado}"""
        ), {"e": entorno}).scalar() or 0
        if total <= tope:
            return

        # Traemos una tanda de los más antiguos y vamos borrando hasta bajar del tope.
        # Si no alcanza, se vuelve a barrer en la próxima corrida (o loop de seguridad).
        for _ in range(50):  # cota de seguridad
            candidatos = conn.execute(text(f"""
                SELECT TOP 200 AudioID, NombreArchivo, TamanioBytes
                FROM calidad.AudioAuditoria
                WHERE Entorno = :e {filtro_fijado}
                ORDER BY FechaCreacion ASC
            """), {"e": entorno}).fetchall()
            if not candidatos:
                return

            for audio_id, nombre, tam in candidatos:
                if total <= tope:
                    return
                try:
                    ruta = os.path.join(settings.AUDIO_STORE_DIR, entorno, nombre)
                    if os.path.exists(ruta):
                        os.remove(ruta)
                except OSError as e:
                    logger.warning("No se pudo borrar el archivo de audio %s: %s", nombre, e)
                try:
                    with engine.begin() as del_conn:
                        del_conn.execute(text(
                            "DELETE FROM calidad.AudioAuditoria WHERE AudioID = :id"
                        ), {"id": audio_id})
                    total -= int(tam or 0)
                except Exception as e:
                    logger.warning("No se pudo borrar la fila de audio %s: %s", audio_id, e)

            if total <= tope:
                return


def ids_con_audio(engine: Engine, ids: Iterable) -> set:
    """Subconjunto de `ids` que YA tienen audio conservado en el entorno actual.

    Espejo de Trancribir.ids_con_transcripcion: se usa para pintar el botón "Escuchar"
    solo en las filas que sí tienen audio. Consulta por lotes (tope de params SQL Server).
    """
    entorno = _entorno()
    ids_unicos = list(dict.fromkeys(
        str(i) for i in ids
        if i is not None and not (isinstance(i, float) and str(i) == "nan")
    ))
    existentes: set = set()
    if not ids_unicos:
        return existentes

    try:
        with engine.connect() as conn:
            for inicio in range(0, len(ids_unicos), 1000):
                lote = ids_unicos[inicio:inicio + 1000]
                q = text("""
                    SELECT DISTINCT IdAplicativo
                    FROM calidad.AudioAuditoria
                    WHERE Entorno = :entorno AND IdAplicativo IN :ids
                """).bindparams(bindparam("ids", expanding=True))
                filas = conn.execute(q, {"entorno": entorno, "ids": lote}).fetchall()
                existentes.update(str(f[0]) for f in filas)
    except Exception as e:
        logger.warning("No se pudo consultar ids_con_audio: %s", e)
    return existentes


def resolver_audio(engine: Engine, id_aplicativo: str) -> Optional[Tuple[str, str, str]]:
    """Devuelve (ruta_absoluta, mime, nombre_descarga) del audio de una interacción, o
    None si no existe en el entorno actual o el archivo se perdió del disco.

    De paso actualiza FechaUltimoAcceso (best-effort; habilita LRU futuro)."""
    if not id_aplicativo:
        return None
    entorno = _entorno()
    try:
        with engine.connect() as conn:
            fila = conn.execute(text("""
                SELECT TOP 1 NombreArchivo, FormatoMime
                FROM calidad.AudioAuditoria
                WHERE Entorno = :entorno AND IdAplicativo = :id
            """), {"entorno": entorno, "id": str(id_aplicativo)}).mappings().first()
    except Exception as e:
        logger.warning("No se pudo resolver el audio %s: %s", id_aplicativo, e)
        return None

    if not fila:
        return None

    ruta = os.path.join(settings.AUDIO_STORE_DIR, entorno, fila["NombreArchivo"])
    if not os.path.exists(ruta):
        return None

    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE calidad.AudioAuditoria
                SET FechaUltimoAcceso = SYSUTCDATETIME()
                WHERE Entorno = :entorno AND IdAplicativo = :id
            """), {"entorno": entorno, "id": str(id_aplicativo)})
    except Exception:
        pass  # el acceso no debe fallar por no poder sellar la fecha

    # Nombre de descarga legible (el archivo en disco es un hash).
    seguro = "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(id_aplicativo))
    return ruta, fila["FormatoMime"] or _MIME, f"audio_{seguro}{_EXT}"
