"""Utilidades compartidas para mandarle audios/chats a Gemini.

Centraliza la conversión con ffmpeg y la preparación que antes estaban duplicadas en
`gemini.py` (calidad) y `Trancribir.py` (transcripción).

El audio va INLINE en el request (`audio_parte_activa` / `preparar_contenido`): desde
el 2026-08-14 los modelos Gemini 3.x rechazan con 403 PERMISSION_DENIED cualquier
`file_uri` de la File API. La File API queda como camino de excepción para lo que no
entra inline (`INLINE_MAX_BYTES`), vía `upload_file_with_retry` / `audio_subido_activo`.
"""
import io
import os
import random
import re
import subprocess
import threading
import time
from contextlib import contextmanager

from google.genai import types

from app.config import settings

# Límite de subidas simultáneas a la File API de Gemini. Lo igualamos al tope de
# llamadas concurrentes (concurrent_api_calls=5) porque la subida era el cuello
# de botella: con Semaphore(2) solo 2 audios subían a la vez aunque hubiera 5
# hilos activos esperando para analizar.
upload_semaphore = threading.Semaphore(5)

# Tope de bytes que viajan inline (Part.from_bytes) en el request de auditoría antes
# de caer a la File API. El request completo no puede superar 20 MB contando el
# prompt, así que dejamos margen. Ver audio_parte_activa.
INLINE_MAX_BYTES = 15 * 1024 * 1024

FFMPEG = '/usr/bin/ffmpeg'

# Tope de tiempo por conversión. Comprimir corre a ~45x tiempo real (un llamado de
# 16 min se codifica en 22 s), así que 300 s cubren un audio de ~3,5 hs con margen.
# Sin timeout, un archivo corrupto deja un ffmpeg colgado y se come un worker del
# pool para siempre.
FFMPEG_TIMEOUT_SEGUNDOS = 300


def _flags_opus() -> list:
    """Flags de compresión a Opus mono 16 kHz, comunes a las dos conversiones.

    - `-ac 1` / `-ar 16000`: voz telefónica. Forzar 16 kHz aunque la fuente sea de 8
      es a propósito: medido, pedirle a libopus `-ar 8000` sobre una fuente de 8 kHz
      sale ~47% MÁS pesado que dejarlo trabajar en banda ancha.
    - `-frame_duration 60`: frames de 60 ms en vez de los 20 ms por defecto. Menos
      overhead de paquete: ~4% menos bytes a la misma calidad. Un llamado no necesita
      la latencia baja de los frames cortos.
    - `-b:a`: configurable (settings.AUDIO_OPUS_BITRATE). VBR está prendido por
      defecto, así que es un techo, no un piso: el promedio real ronda los 21 kbps.
    - `-compression_level 7`: 30% más rápido que el 10 por defecto (46x → 60x tiempo
      real) con el MISMO tamaño (24,6 vs 24,7 kbps) y 0,7 dB de fidelidad, que en voz
      no se escucha. OJO con bajarlo más: en 6 hay un acantilado. libopus deja de hacer
      el análisis que le permite a VBR gastar menos y el archivo salta a 38 kbps (+54%)
      perdiendo además 5 dB — peor en los dos ejes a la vez. 7 es el piso útil.
    """
    bitrate = getattr(settings, 'AUDIO_OPUS_BITRATE', '32k')
    return [
        '-vn',
        '-c:a', 'libopus',   # Códec Opus
        '-ac', '1',          # Mono
        '-ar', '16000',      # 16000 Hz (cubre el ancho de banda telefónico)
        '-b:a', bitrate,     # Configurable: define calidad de IA + escucha (ver config)
        '-frame_duration', '60',
        '-compression_level', '7',
    ]


def _convertir_archivo(ruta_original: str, ruta_convertida: str, filtro: str = '') -> str:
    """Comprime un archivo a Opus y devuelve el stderr de ffmpeg.

    Escribe a un temporal y renombra al final (os.replace, atómico): si ffmpeg muere a
    mitad, no queda un .clean.ogg truncado que las corridas siguientes den por bueno.

    `filtro` permite colgar filtros de medición (volumedetect/silencedetect) de la
    MISMA pasada: pasan el audio sin tocarlo y escriben sus números en stderr, así el
    gate de audio no tiene que decodificar el archivo una segunda vez (ver
    AuditorIA/audio_calidad.py). Por eso el stderr se captura en vez de tirarlo.
    """
    # Nombre único por proceso/hilo: dos workers que conviertan el mismo audio a la
    # vez no se pisan el archivo a medio escribir.
    parcial = f'{ruta_convertida}.{os.getpid()}.{threading.get_ident()}.parcial.ogg'
    comando = [FFMPEG, '-y', '-nostdin', '-hide_banner', '-nostats', '-i', ruta_original]
    if filtro:
        comando += ['-af', filtro]
    comando += _flags_opus() + [parcial]
    try:
        proceso = subprocess.run(
            comando, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            check=True, timeout=FFMPEG_TIMEOUT_SEGUNDOS,
        )
        os.replace(parcial, ruta_convertida)
        return proceso.stderr.decode('utf-8', 'ignore')
    finally:
        if os.path.exists(parcial):
            try:
                os.remove(parcial)
            except OSError:
                pass


def convertir_y_medir(ruta_original: str, filtro: str = ''):
    """Convierte a Opus y, si se pasa `filtro`, mide en la misma pasada.

    Devuelve `(ruta, salida_ffmpeg | None)`. La salida es None cuando no hubo nada
    que medir: o porque se reutilizó una conversión previa, o porque falló (y ahí la
    ruta que vuelve es la original, sin comprimir).

    Existe para que el gate de audio no decodifique el archivo dos veces: antes hacía
    una pasada solo para medir y después otra para comprimir. Ver audio_calidad.
    """
    try:
        if not ruta_original or not os.path.exists(ruta_original):
            return ruta_original, None

        # 1. Directorio y nombre base
        directorio, nombre_archivo = os.path.split(ruta_original)

        # 2. Sanitizar el nombre (reemplazar 'ñ' y otros caracteres raros)
        nombre_limpio = nombre_archivo.replace('ñ', 'n').replace('Ñ', 'N')
        # Mantener solo alfanuméricos, puntos, guiones y espacios
        nombre_limpio = re.sub(r'[^a-zA-Z0-9\.\-\_\ ]', '', nombre_limpio)

        # 3. Ruta de salida segura
        ruta_convertida = os.path.join(directorio, nombre_limpio + ".clean.ogg")

        # Reutilizar la conversión previa si ya existe y es válida.
        if os.path.exists(ruta_convertida) and os.path.getsize(ruta_convertida) > 0:
            return ruta_convertida, None

        salida = _convertir_archivo(ruta_original, ruta_convertida, filtro)
        print(f"🔄 Audio convertido exitosamente: {os.path.basename(ruta_convertida)}")
        return ruta_convertida, salida

    except Exception as e:
        print(f"⚠️ Error intentando convertir audio: {e}")
        return ruta_original, None


def convertir_a_mp3_estandar(ruta_original: str) -> str:
    """Convierte un audio a Opus mono 16kHz (bitrate settings.AUDIO_OPUS_BITRATE).

    El mismo archivo comprimido se sube a Gemini (auditoría/transcripción) y se
    conserva para reproducirlo (audio_store): el bitrate define la calidad de las
    tres cosas. Reutiliza la conversión previa si ya existe y es válida, evitando
    re-codificar el mismo audio dos veces (p. ej. al correr transcripción + calidad,
    o porque el gate de audio ya lo convirtió al medirlo).
    """
    ruta, _salida = convertir_y_medir(ruta_original)
    return ruta


def convertir_bytesio_a_opus(file_io: io.BytesIO) -> io.BytesIO:
    """Convierte un audio en memoria (BytesIO) a Opus mono 16kHz (bitrate
    settings.AUDIO_OPUS_BITRATE) usando
    ffmpeg por pipes (stdin→stdout, sin tocar disco). Es el equivalente en memoria
    de `convertir_a_mp3_estandar` para los audios que vienen como BytesIO
    (Vantix/Orion vía CYT, Vitalis Salud), que antes se subían crudos (WAV pesado).

    Es defensiva: si ffmpeg falla, no produce salida o el resultado no achica el
    audio, devuelve el original (la subida nunca queda peor que antes)."""
    try:
        file_io.seek(0)
        datos_entrada = file_io.read()
        if not datos_entrada:
            file_io.seek(0)
            return file_io

        # Sin -nostdin: acá stdin ES la entrada de audio (pipe:0), no la terminal.
        comando = (
            [FFMPEG, '-y', '-hide_banner', '-nostats', '-i', 'pipe:0']
            + _flags_opus()
            + ['-f', 'ogg',      # Contenedor explícito (no hay extensión en el pipe)
               'pipe:1']
        )
        proceso = subprocess.run(
            comando, input=datos_entrada,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
            timeout=FFMPEG_TIMEOUT_SEGUNDOS,
        )
        datos_salida = proceso.stdout

        # Si no hubo salida o no achicó, nos quedamos con el original.
        if not datos_salida or len(datos_salida) >= len(datos_entrada):
            file_io.seek(0)
            return file_io

        print(f"🔄 Audio en memoria a Opus: {len(datos_entrada)} -> {len(datos_salida)} bytes")
        salida_io = io.BytesIO(datos_salida)
        salida_io.seek(0)
        return salida_io

    except Exception as e:
        print(f"⚠️ Error convirtiendo audio en memoria a Opus: {e}. Se sube el original.")
        file_io.seek(0)
        return file_io


def preparar_contenido(file_path):
    """Deja el audio/chat listo para mandar a Gemini, comprimido y con su mime.

    Devuelve `(file_io, mime_type, display_name, archivo_log)` o None si no se puede
    leer. Es el paso común a las dos formas de mandarlo: subirlo a la File API
    (`upload_file_with_retry`) o pegarlo inline en el request (`audio_parte_activa`).
    """
    # CASO 1: El archivo ya está en memoria (Vantix/Orion, Vitalis Salud / BytesIO).
    # Ya viene comprimido a Opus desde la descarga (_comprimir_audios_en_memoria),
    # así que acá solo se prepara. mime_type queda "audio/mp3" como en el path físico
    # (que manda Opus/Ogg con ese mismo mime y Gemini lo acepta porque sniffea).
    if isinstance(file_path, io.BytesIO):
        file_io = file_path
        file_io.seek(0)
        return file_io, "audio/mp3", "audio_en_memoria.ogg", "Audio en memoria (BytesIO)"

    # CASO 2: El archivo es una ruta física en disco
    if not file_path or not os.path.exists(file_path):
        print(f"Archivo no encontrado: {file_path}")
        return None

    # --- SOPORTE PARA CHATS ---
    if str(file_path).endswith('.json'):
        archivo_a_mandar = file_path
        mime_type = "application/json"
    else:
        # Solo convertimos si es un archivo físico y NO es un chat
        archivo_a_mandar = convertir_a_mp3_estandar(file_path)
        mime_type = "audio/mp3"

    display_name = os.path.basename(archivo_a_mandar)
    with open(archivo_a_mandar, "rb") as f:
        file_data = f.read()
    return io.BytesIO(file_data), mime_type, display_name, display_name


def upload_file_with_retry(gemini_api, file_path, max_retries=5, semaphore=None):
    """Sube un archivo a la File API de Gemini controlando la concurrencia y con
    reintentos. Soporta audio físico (se convierte), chats `.json` y `BytesIO`.

    `semaphore` permite usar un límite de concurrencia distinto al global (el batch
    pasa uno más grande para subir lotes grandes más rápido; el path interactivo usa
    el `upload_semaphore` global por defecto)."""
    sem = semaphore if semaphore is not None else upload_semaphore

    preparado = preparar_contenido(file_path)
    if preparado is None:
        return None
    file_io, mime_type, display_name, archivo_log = preparado

    for attempt in range(max_retries):
        try:
            print(f"Subiendo archivo: {archivo_log} (Intento {attempt + 1})")
            file_io.seek(0)

            config = types.UploadFileConfig(
                mime_type=mime_type,
                display_name=display_name
            )

            with sem:
                return gemini_api.files.upload(file=file_io, config=config)

        except Exception as e:
            tiempo_espera = 5 + random.uniform(1, 5)
            print(f"Error al subir {archivo_log}: {e}. Reintentando en {tiempo_espera:.1f} segundos...")
            time.sleep(tiempo_espera)

    print(f"No se pudo subir el archivo {archivo_log} después de {max_retries} intentos.")
    return None


def esperar_archivo_activo(gemini_api, uploaded_file, max_intentos: int = 60) -> bool:
    """Hace polling hasta que el archivo subido pase a estado ACTIVE.

    Devuelve True si quedó activo. Lanza ValueError si el procesamiento falló y
    TimeoutError si tardó demasiado.
    """
    print(f"Esperando a que el archivo {uploaded_file.name} esté activo...")
    intentos = 0
    while True:
        try:
            file_check = gemini_api.files.get(name=uploaded_file.name)
            estado = str(file_check.state)
            if estado == "FileState.ACTIVE":
                print(f"✅ Archivo {file_check.name} está activo y listo.")
                return True
            elif estado == "FileState.FAILED":
                raise ValueError(f"❌ El procesamiento del archivo falló: {estado}")
            print(f"Estado: {estado}. Esperando 2 segundos...")
        except (ValueError, TimeoutError):
            raise
        except Exception as e:
            # Error temporal (p. ej. 500 al consultar estado): seguimos esperando.
            print(f"⚠️ Error temporal verificando estado ({e}). Reintentando en 2s...")

        time.sleep(2)
        intentos += 1
        if intentos > max_intentos:
            raise TimeoutError(f"El archivo {uploaded_file.name} tardó demasiado en activarse.")


@contextmanager
def audio_subido_activo(gemini_api, audio_dir):
    """Sube el audio UNA sola vez, espera a que esté activo, lo cede para usarlo
    en una o varias llamadas y lo borra al salir.

    Cede `None` si la subida falla. Si la activación falla, propaga la excepción
    (igual borra el archivo en el `finally`).
    """
    uploaded_file = upload_file_with_retry(gemini_api, audio_dir)
    if uploaded_file is None or uploaded_file.uri is None:
        print(f"❌ Fallo crítico al subir archivo {audio_dir}.")
        yield None
        return

    try:
        esperar_archivo_activo(gemini_api, uploaded_file)
        yield uploaded_file
    finally:
        try:
            print(f"Eliminando archivo subido: {uploaded_file.name}...")
            gemini_api.files.delete(name=uploaded_file.name)
        except Exception as e:
            print(f"Error al intentar eliminar el archivo {uploaded_file.name}: {e}")


@contextmanager
def audio_parte_activa(gemini_api, audio_dir):
    """Cede el `types.Part` con el audio listo para mandar a Gemini (o None si falla).

    Por qué inline y no la File API: desde el 2026-08-14 los modelos Gemini 3.x
    (3.1-flash-lite, 3.5, 3.6, 3.7, flash-latest) rechazan con 403 PERMISSION_DENIED
    cualquier `file_uri` de la File API — el archivo sube y queda ACTIVE, pero el
    generate_content que lo referencia muere. Verificado contra la API REST cruda, así
    que no es el SDK. Andan 2.5-flash, 2.5-pro y 3-flash-preview. Mandar los bytes
    inline funciona con TODOS los modelos.

    El único techo del camino inline es el tamaño del request (20 MB contando bytes +
    prompt): por encima de INLINE_MAX_BYTES se cae a la File API, que hoy va a fallar
    con un modelo 3.x, pero es la forma de que un audio enorme siga teniendo una vía.
    Un audio ya comprimido a Opus pesa ~90 KB por minuto, así que 15 MB son ~2.5 hs de
    llamada: en la práctica no se toca.

    El batch NO usa este camino: `batches.create` sigue con la File API (ver
    gemini.py::_procesar_lote_batch), que ahí sí funciona y además es la única forma
    de mandar cientos de audios en un solo request.
    """
    preparado = preparar_contenido(audio_dir)
    if preparado is None:
        print(f"❌ No se pudo preparar el archivo {audio_dir}.")
        yield None
        return

    file_io, mime_type, _display_name, archivo_log = preparado
    datos = file_io.getvalue()

    if len(datos) <= INLINE_MAX_BYTES:
        yield types.Part.from_bytes(data=datos, mime_type=mime_type)
        return

    print(f"⚠️ {archivo_log} pesa {len(datos) / 1024 / 1024:.1f} MB: no entra inline, "
          f"se sube a la File API (los modelos Gemini 3.x la rechazan con 403).")
    with audio_subido_activo(gemini_api, audio_dir) as uploaded_file:
        if uploaded_file is None:
            yield None
        else:
            yield types.Part.from_uri(file_uri=uploaded_file.uri, mime_type=uploaded_file.mime_type)
