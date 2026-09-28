"""Detección de audio inaudible ANTES de gastar una auditoría en él.

El caso que motiva el módulo: una grabación de 82 segundos prácticamente muda llegó a
Gemini con toda la metadata del llamado en el prompt (nombre del agente, skill, DNI del
titular, motivo). El modelo devolvió una conversación completa y verosímil —construida
con esos datos— y una auditoría con todos los atributos en "Cumple". Nadie podía
distinguirla de una auditoría real: el audio existía, duraba lo que decía el informe y
la transcripción se veía normal.

La defensa es medir el audio, no confiar en que la IA avise. Una pasada de ffmpeg
(`volumedetect` + `silencedetect` encadenados, sin re-codificar) alcanza para saber si
hay voz. Si el audio está mudo, el llamado NO se manda a la IA: no se gastan tokens y
queda registrado como no auditable (ver AuditorIA/incidencias.py).

Todo es best-effort: si ffmpeg no está, falla o el formato no se puede medir, se
devuelve None y el llamado sigue su curso normal. Un error de medición nunca puede
frenar una auditoría legítima.
"""
import io
import logging
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Union

import pandas as pd

from app.config import settings
from AuditorIA import gemini_files, incidencias as inc

logger = logging.getLogger(__name__)

FFMPEG = '/usr/bin/ffmpeg'

# Timeout por audio. Medir no decodifica a archivo (salida a /dev/null) pero igual
# recorre el stream completo; una llamada de 30 min tarda pocos segundos.
_TIMEOUT_SEGUNDOS = 120

_RE_MEAN = re.compile(r'mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB')
_RE_MAX = re.compile(r'max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB')
_RE_SIL_DUR = re.compile(r'silence_duration:\s*(\d+(?:\.\d+)?)')
_RE_SIL_START = re.compile(r'silence_start:\s*(-?\d+(?:\.\d+)?)')
_RE_SIL_END = re.compile(r'silence_end:\s*(-?\d+(?:\.\d+)?)')
_RE_DURACION = re.compile(r'Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)')


@dataclass
class Analisis:
    """Medición de energía y silencio de un audio."""
    duracion: Optional[float]
    mean_db: Optional[float]
    max_db: Optional[float]
    segundos_silencio: float

    @property
    def ratio_silencio(self) -> Optional[float]:
        """Proporción del audio que es silencio (0..1), o None si no se pudo medir."""
        if not self.duracion or self.duracion <= 0:
            return None
        return min(self.segundos_silencio / self.duracion, 1.0)

    def resumen(self) -> str:
        """Texto corto para logs y para el motivo de la incidencia."""
        ratio = self.ratio_silencio
        partes = []
        if self.mean_db is not None:
            partes.append(f"volumen medio {self.mean_db:.1f} dB")
        if ratio is not None:
            partes.append(f"{ratio * 100:.0f}% en silencio")
        if self.duracion:
            partes.append(f"{self.duracion:.0f}s")
        return ", ".join(partes) if partes else "sin datos"


def _a_ruta_temporal(datos: bytes) -> Optional[str]:
    """Vuelca bytes a un archivo temporal (ffmpeg por pipe no puede hacer seek)."""
    if not datos:
        return None
    fd, ruta = tempfile.mkstemp(suffix='.ogg')
    with os.fdopen(fd, 'wb') as f:
        f.write(datos)
    return ruta


def _parsear_salida(salida: str) -> Analisis:
    """Extrae duración, volumen y silencio acumulado del stderr de ffmpeg."""
    duracion = None
    m = _RE_DURACION.search(salida)
    if m:
        horas, minutos, segundos = m.groups()
        duracion = int(horas) * 3600 + int(minutos) * 60 + float(segundos)

    mean_db = float(_RE_MEAN.search(salida).group(1)) if _RE_MEAN.search(salida) else None
    max_db = float(_RE_MAX.search(salida).group(1)) if _RE_MAX.search(salida) else None

    segundos_silencio = sum(float(d) for d in _RE_SIL_DUR.findall(salida))

    # Un tramo de silencio que llega hasta el final del archivo no siempre cierra con
    # su `silence_end`/`silence_duration`: si quedó abierto, se cuenta a mano. Sin esto
    # un audio que arranca mudo y nunca "termina" el silencio mide 0% de silencio.
    starts = _RE_SIL_START.findall(salida)
    ends = _RE_SIL_END.findall(salida)
    if duracion and len(starts) > len(ends):
        ultimo_inicio = float(starts[-1])
        if 0 <= ultimo_inicio <= duracion:
            segundos_silencio += duracion - ultimo_inicio

    return Analisis(duracion=duracion, mean_db=mean_db, max_db=max_db,
                    segundos_silencio=segundos_silencio)


def _filtro_medicion() -> str:
    """Cadena de filtros que mide sin tocar el audio (pasa el stream tal cual)."""
    noise = getattr(settings, 'AUDIO_SILENCIO_NOISE_DB', -40.0)
    dur_min = getattr(settings, 'AUDIO_SILENCIO_DUR_MIN', 1.0)
    return f'volumedetect,silencedetect=noise={noise}dB:d={dur_min}'


def _medir(ruta: str) -> str:
    """Pasada de ffmpeg que SOLO mide (salida al muxer null, no escribe nada)."""
    comando = [
        FFMPEG, '-hide_banner', '-nostats', '-nostdin',
        '-i', ruta,
        '-af', _filtro_medicion(),
        '-f', 'null', '-',
    ]
    proc = subprocess.run(comando, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                          timeout=_TIMEOUT_SEGUNDOS)
    return proc.stderr.decode('utf-8', 'ignore')


def analizar(source: Union[str, io.BytesIO, bytes, None],
             convertir: bool = False) -> Optional[Analisis]:
    """Mide energía y silencio de un audio en UNA pasada de ffmpeg.

    Acepta ruta física, BytesIO o bytes (el pipeline maneja las tres formas según la
    campaña). Devuelve None si no se pudo medir: el llamado sigue su curso.

    Con `convertir=True` esa única pasada además comprime el archivo a Opus y deja el
    `.clean.ogg` listo, que es el que después se le manda a Gemini y se conserva en el
    store. Antes eran dos decodificaciones completas del mismo WAV (una para medir,
    otra para comprimir); ahora los filtros de medición viajan colgados de la pasada
    de compresión. Es opcional a propósito: medir tiene que poder ser un acto sin
    efectos sobre el disco (lo usa scripts/calibrar_audio_mudo.py sobre el store, y
    ahí un .clean.ogg por audio duplicaría la carpeta).
    """
    tmp_path = None
    try:
        if isinstance(source, (io.BytesIO, bytes, bytearray)):
            datos = source.getvalue() if isinstance(source, io.BytesIO) else bytes(source)
            tmp_path = _a_ruta_temporal(datos)
            ruta = tmp_path
            # Lo que viene en memoria ya se comprimió al descargarlo
            # (get_audio_bytes._comprimir_audios_en_memoria): no hay nada que convertir.
            convertir = False
        else:
            ruta = source
        if not isinstance(ruta, str) or not ruta or not os.path.exists(ruta):
            return None
        # Los chats se pasean por el pipeline con un .json en `audio_dir`: no hay audio
        # que medir y ffmpeg escupiría un error por cada uno.
        if ruta.endswith('.json'):
            return None

        salida = ''
        if convertir:
            # gemini_files devuelve la salida de ffmpeg solo si realmente convirtió;
            # si reutilizó un .clean.ogg previo (re-auditoría) no hay nada medido y
            # caemos a la pasada de medición.
            _ruta_convertida, salida_conversion = gemini_files.convertir_y_medir(
                ruta, _filtro_medicion())
            salida = salida_conversion or ''
        if not salida:
            salida = _medir(ruta)
        if not salida:
            return None
        analisis = _parsear_salida(salida)
        # Sin ninguna de las dos mediciones no hay nada que evaluar.
        if analisis.mean_db is None and analisis.ratio_silencio is None:
            return None
        return analisis
    except subprocess.TimeoutExpired:
        logger.warning("Timeout midiendo el audio %s; se audita igual.", source)
        return None
    except Exception as e:
        logger.debug("No se pudo medir el audio (%s); se audita igual.", e)
        return None
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def evaluar(source: Union[str, io.BytesIO, bytes, None],
            convertir: bool = False) -> tuple[Optional[str], Optional[Analisis]]:
    """Clasifica un audio: (motivo | None, análisis | None).

    - `audio_mudo`: no hay voz auditable. El llamado NO se manda a la IA.
    - `audio_mayormente_silencio`: hay algo de voz pero el audio está casi vacío. Se
      audita igual (puede ser una llamada corta con mucha espera) pero queda marcado
      para revisión humana.

    Los umbrales son deliberadamente conservadores: un falso positivo acá deja sin
    auditar una llamada legítima, que es peor que auditar una muda de más. Se calibran
    en config.py y hay un script para medirlos contra el store real
    (scripts/calibrar_audio_mudo.py).
    """
    if not getattr(settings, 'AUDIO_GATE_MUDO', True):
        return None, None

    analisis = analizar(source, convertir=convertir)
    if analisis is None:
        return None, None

    ratio = analisis.ratio_silencio
    mean_db = analisis.mean_db

    umbral_mudo = getattr(settings, 'AUDIO_MUDO_RATIO_SILENCIO', 0.98)
    umbral_mean = getattr(settings, 'AUDIO_MUDO_MEAN_DB', -50.0)
    umbral_sospecha = getattr(settings, 'AUDIO_SOSPECHOSO_RATIO_SILENCIO', 0.90)

    if (mean_db is not None and mean_db <= umbral_mean) or (ratio is not None and ratio >= umbral_mudo):
        return 'audio_mudo', analisis
    if ratio is not None and ratio >= umbral_sospecha:
        return 'audio_mayormente_silencio', analisis
    return None, analisis


def filtrar_no_auditables(df: 'pd.DataFrame') -> Tuple['pd.DataFrame', 'pd.DataFrame']:
    """Mide los audios de un DataFrame y aparta los que no tienen voz.

    Es el gate que corre ANTES de llamar a la IA, en sync y en batch. Un audio mudo
    no da error: Gemini lo completa con la metadata del llamado que viaja en el prompt
    (nombre del agente, skill, documento del titular) y devuelve una conversación
    entera inventada con todos los atributos en "Cumple". Medir el archivo es la única
    forma de distinguirlo, y sale gratis: la medición viaja colgada de la pasada de
    ffmpeg que igual había que hacer para comprimir el audio a Opus (`convertir=True`),
    así que el archivo se decodifica una sola vez para las dos cosas.

    De paso aprovecha la medición para comparar la duración real contra la que declara
    el sistema de origen, que es el aviso temprano de un archivo cruzado o recortado.

    Devuelve (df_auditable, df_no_auditable). El segundo se guarda igual, marcado con
    su incidencia y sin detalles, para que el llamado no desaparezca sin explicación.
    """
    vacio = df.iloc[0:0]
    if df is None or df.empty or 'audio_dir' not in df.columns:
        return df, vacio
    if not getattr(settings, 'AUDIO_GATE_MUDO', True):
        return df, vacio

    resultados: Dict[Any, Tuple[Optional[str], Optional[Analisis]]] = {}
    # Un worker por núcleo: acá no se mide nomás, se comprime (`convertir=True`), que
    # es CPU pura y escala casi lineal (medido: 8 workers hacen 354 min de audio por
    # minuto de reloj, 16 hacen 605). El 8 fijo que había venía de cuando esta etapa
    # solo medía; en una máquina de 16 núcleos dejaba media máquina sin usar.
    with ThreadPoolExecutor(max_workers=min(len(df), os.cpu_count() or 8)) as executor:
        futures = {executor.submit(evaluar, audio, True): idx
                   for idx, audio in df['audio_dir'].items()}
        for future in as_completed(futures):
            idx = futures[future]
            try:
                resultados[idx] = future.result()
            except Exception as e:
                # Medir es best-effort: si falla, el llamado se audita igual.
                logger.debug("No se pudo evaluar el audio de la fila %s: %s", idx, e)

    mudos, sospechosos, duraciones = [], [], {}
    for idx, (motivo, analisis) in resultados.items():
        if analisis is not None and analisis.duracion:
            duraciones[idx] = analisis.duracion
        if motivo == inc.AUDIO_MUDO:
            mudos.append(idx)
            logger.warning("Audio sin voz audible (fila %s): %s. No se manda a la IA.",
                           idx, analisis.resumen() if analisis else 'sin datos')
        elif motivo == inc.AUDIO_MAYORMENTE_SILENCIO:
            sospechosos.append(idx)

    inc.marcar(df, mudos, inc.AUDIO_MUDO)
    inc.marcar(df, sospechosos, inc.AUDIO_MAYORMENTE_SILENCIO)
    inc.verificar_duracion(df, duraciones)

    if not mudos:
        return df, vacio

    logger.warning("%d de %d audios quedaron fuera de la auditoría por no tener voz audible.",
                   len(mudos), len(df))
    return df.drop(index=mudos), df.loc[mudos].copy()
