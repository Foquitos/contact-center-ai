import io
import os
import requests
import numpy as np
import pandas as pd
import json

from datetime import datetime
from typing import Union
from sqlalchemy import Engine
from AuditorIA.downloads.CYT_comunicaciones import CYT_comunicaciones
from AuditorIA.downloads.CXOne import CXOne
from AuditorIA.downloads.Hermes import Hermes
from AuditorIA.downloads.Mitrol import Mitrol
from AuditorIA.downloads.Verint import Verint
from AuditorIA.downloads.AsterVoIP import AsterVoIP
from AuditorIA.downloads.Genesys import Genesys
from AuditorIA.gemini_files import convertir_bytesio_a_opus
from app.config import settings

from concurrent.futures import ThreadPoolExecutor, as_completed

def get_audio_bytes_Mitrol(api: Mitrol, engine: Engine, row: dict):
    try:
        if isinstance(api, Mitrol):
            # Verificar si tenemos la columna [Chat] y si está activa
            if row.get('Chat') == 1 or row.get('Chat') is True:
                # En lugar de un WAV, generamos el chat histórico en JSON. El Segmento
                # identifica cuál de las gestiones de la conversación es la que se audita
                # (una misma conversación puede haber pasado por varios operadores).
                segmento = row.get('Segmento')
                segmento = int(segmento) if segmento is not None and not pd.isna(segmento) else None
                chat_data = api.Chat_historico(
                    engine=engine,
                    idInteraccion=row['idInteraccion'],
                    segmento=segmento,
                    dias_atras=14,
                )
                if chat_data:
                    if getattr(api, 'carpeta_descarga', None):
                        os.makedirs(api.carpeta_descarga, exist_ok=True)
                    save_path = os.path.join(api.carpeta_descarga, f"{row['idInteraccion']}_chat.json")
                    with open(save_path, 'w', encoding='utf-8') as f:
                        json.dump(chat_data, f, ensure_ascii=False, indent=4)
                    return save_path
                return None
            else:
                # Comportamiento normal por defecto (es Grabacion)
                file_path = row.get('FilePath')
                file_name = row.get('FileName')
                if not file_name or pd.isna(file_name) or not file_path or pd.isna(file_path):
                    return None
                audio_dir = api.Grabaciones(FilePath=str(file_path), FileName=str(file_name))
                return audio_dir
        else:
            return None
    except Exception as e:
        print(f"Error descargando audio/chat Mitrol: {e}")
        return None

def get_audio_bytes_Voltara(api:Verint, row):
    for i in range(3):
        try:
            audio_dir = api.descargar_audio(id=row['ID_llamado'])
            if audio_dir:
                return audio_dir  # Devuelve bytes reales, no BytesIO
        except:
            pass
    return None

def get_audio_bytes_CXOne(api:CXOne, df):
    if 'segmentId' not in df.columns or df.empty:
        # Retornamos el df original con la columna nueva vacía para mantener consistencia
        df['audio_dir'] = None
        return df

    lista_segmentId = df['segmentId'].tolist()
    
    if isinstance(api, CXOne):
        rutas_descargadas = api.descargar_audios(segmentIds=lista_segmentId)
    else:
        rutas_descargadas = []

    # Crear un DataFrame a partir de las rutas descargadas
    df_descargas = pd.DataFrame(rutas_descargadas, columns=['audio_dir'])
    
    # Extraer el segmentId del nombre del archivo
    df_descargas['segmentId'] = df_descargas['audio_dir'].apply(lambda x: os.path.splitext(os.path.basename(x))[0])

    # --- PASO CLAVE: Asegurar tipos de datos ---
    # Convertimos ambos a string para evitar errores si uno es int y el otro str
    df['segmentId'] = df['segmentId'].astype(str)
    df_descargas['segmentId'] = df_descargas['segmentId'].astype(str)

    # --- FUSIÓN (MERGE) ---
    # Usamos how='left' para mantener todas las filas de tu df original,
    # incluso si no se encontró audio (quedará como NaN/None en audio_dir).
    # Si solo quieres filas que SÍ tengan audio, cambia a how='inner'.
    df_final = pd.merge(df, df_descargas, on='segmentId', how='left')

    return df_final

def get_audio_bytes_Hermes(api:Hermes, row):
    try:
        if isinstance(api, Hermes):
            audio_dir = api.descarga_audio(filepath=row['Rec_Filename'])
        else:
            audio_dir = None
        if audio_dir:
            return audio_dir
    except Exception as e:
        print(f"Error descargando audio Hermes: {e}")
        return None
    return None

def get_audio_bytes_Vitalis_Salud(row):
    try:
        response = requests.get(row['url_grabacion'], timeout=30)
        if response.status_code == 200:
            audio_dir = io.BytesIO(response.content)
        else:
            audio_dir = None
        return audio_dir
    except Exception as e:
        print(f"Error descargando audio Vitalis_Salud: {e}")
        return None

def get_audio_bytes_Farmalux(api: AsterVoIP, row):
    try:
        fecha = row['fecha_inicio']
        if not isinstance(fecha, datetime):
            fecha = pd.to_datetime(fecha).to_pydatetime()
        return api.descargar_audio(
            fecha=fecha,
            Destino=str(row['Destino']),
            Origen=str(row['Origen']),
            sentido='entrantes'
        )
    except Exception as e:
        print(f"Error descargando audio Farmalux: {e}")
        return None

def get_audio_bytes_Genesys(api: Genesys, df: pd.DataFrame) -> pd.DataFrame:
    """Benefix: el audio se pide por conversationId (columna `ID` del builder). Vuelve
    WAV en memoria; lo comprime a Opus `_comprimir_audios_en_memoria`, como a Vantix."""
    audios = api.descargar_audios(df['ID'].astype(str).tolist(),
                                  workers=settings.GENESYS_DOWNLOAD_WORKERS)
    df['audio_dir'] = df['ID'].astype(str).map(audios)
    return df


def _descargar_shard_orion(ids_shard: list, api: Union[CYT_comunicaciones, None] = None) -> dict:
    """Descarga un subconjunto de audios de CYT. Si recibe un `api` ya logueado lo
    reutiliza; si no, abre (y cierra) su propio navegador CYT. Tolerante a fallos:
    si el navegador no arranca o falla, devuelve {} para ese shard en vez de romper
    toda la descarga (esos audios quedan en None y se filtran aguas abajo)."""
    try:
        if api is not None:
            return api.descargar_audios(ids_audio=ids_shard)
        with CYT_comunicaciones(settings.CYT_USER, settings.CYT_PASS) as cyt:
            return cyt.descargar_audios(ids_audio=ids_shard)
    except Exception as e:
        print(f"Error en shard de descarga Orion ({len(ids_shard)} ids): {e}")
        return {}


def get_audio_bytes_Orion(api:CYT_comunicaciones, df:pd.DataFrame):
    try:
        id_audio:list[str] = df['ID de la grabación'].astype(str).tolist()

        # CYT_DOWNLOAD_WORKERS navegadores en paralelo: cada uno baja un shard de IDs.
        # La navegación Selenium por audio (no la bajada de bytes) es el cuello de
        # botella, así que repartirla entre N navegadores escala ~linealmente.
        workers = max(1, getattr(settings, 'CYT_DOWNLOAD_WORKERS', 1))

        if workers <= 1 or len(id_audio) <= workers:
            # Pocos audios o paralelización desactivada: usamos el navegador ya logueado.
            audios = api.descargar_audios(ids_audio=id_audio)
        else:
            shards = [list(s) for s in np.array_split(id_audio, workers) if len(s) > 0]
            audios = {}
            with ThreadPoolExecutor(max_workers=len(shards)) as executor:
                # El primer shard reutiliza el navegador ya logueado (api); el resto
                # abren su propio navegador CYT.
                futuros = [
                    executor.submit(_descargar_shard_orion, shard, api if i == 0 else None)
                    for i, shard in enumerate(shards)
                ]
                for futuro in as_completed(futuros):
                    audios.update(futuro.result())

        #Audios es un diccionario con Key siendo el ID de la grabación y value el audio en bytes
        df['audio_dir'] = df['ID de la grabación'].astype(str).map(audios)

        return df
    except Exception as e:
        print(f"Error descargando audio Orion: {e}")
        df['audio_dir'] = None
        return df

def _comprimir_audios_en_memoria(df: pd.DataFrame) -> pd.DataFrame:
    """Comprime a Opus (en memoria) todos los audios de `audio_dir` que vinieron como
    BytesIO (Vantix/Orion, Vitalis Salud, Farmalux). Se hace acá, justo después de la
    descarga, para que el DataFrame no arrastre los WAV crudos durante toda la fase de
    batch/upload (ahorra RAM) y para que el split de lotes de 1.5GB use el tamaño ya
    comprimido. La compresión es CPU-bound (ffmpeg subprocess), así que se paraleliza.
    """
    if 'audio_dir' not in df.columns:
        return df

    indices = [i for i in df.index if isinstance(df.at[i, 'audio_dir'], io.BytesIO)]
    if not indices:
        return df

    max_workers = min(len(indices), (os.cpu_count() or 4))
    comprimidos = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Leemos los BytesIO en el hilo principal y los comprimimos en los workers;
        # la asignación al DataFrame se hace después (pandas no es thread-safe al escribir).
        futuros = {executor.submit(convertir_bytesio_a_opus, df.at[i, 'audio_dir']): i for i in indices}
        for futuro in as_completed(futuros):
            comprimidos[futuros[futuro]] = futuro.result()

    for i, audio_comprimido in comprimidos.items():
        df.at[i, 'audio_dir'] = audio_comprimido
    return df


def get_audio_bytes(df:pd.DataFrame, engine, api: Union[Mitrol, Verint, CXOne, Hermes, CYT_comunicaciones, AsterVoIP, None], num_chunks:int=10, empresa:Union[list[str], None]=None):
    """Descarga los audios por el canal de cada empresa y comprime a Opus los que
    vienen en memoria (ver _comprimir_audios_en_memoria)."""
    if df is None or getattr(df, 'empty', True):
        if df is not None and 'audio_dir' not in df.columns:
            df['audio_dir'] = None
        return df
    df = _descargar_audios_dispatch(df, engine, api, num_chunks, empresa)
    return _comprimir_audios_en_memoria(df)


def _descargar_audios_dispatch(df:pd.DataFrame, engine, api: Union[Mitrol, Verint, CXOne, Hermes, CYT_comunicaciones, AsterVoIP, None], num_chunks:int=10, empresa:Union[list[str], None]=None):
    empresa = empresa or []

    if isinstance(api, AsterVoIP):
        df['audio_dir'] = df.apply(lambda row: get_audio_bytes_Farmalux(api, row), axis=1)
        return df
    if isinstance(api, Verint):
        df['audio_dir'] = df.apply(lambda row: get_audio_bytes_Voltara(api, row), axis=1)
        return df
    elif isinstance(api, CXOne):
        df = get_audio_bytes_CXOne(api, df)
        # La función ahora devuelve un df con 'segmentId' y 'audio_dir'
        return df 
    elif isinstance(api, Hermes):
        # Hermes (Hidra Comercial) maneja un único buffer de transcodificación por sesión/estación
        # en OnnetSlave (Manager.asmx): las descargas DEBEN ser secuenciales para que peticiones
        # concurrentes no sobreescriban el buffer del servidor y mezclen/repitan los audios.
        df['audio_dir'] = df.apply(lambda row: get_audio_bytes_Hermes(api, row), axis=1)
        return df
    elif isinstance(api, CYT_comunicaciones):
        df = get_audio_bytes_Orion(api, df)
        return df
    elif isinstance(api, Genesys):
        return get_audio_bytes_Genesys(api, df)
    elif isinstance(api, Mitrol):
        mitrol_api: Mitrol = api
        # Dividir el DataFrame en chunks
        df_chunks = np.array_split(df, num_chunks)
        with ThreadPoolExecutor(max_workers=num_chunks) as executor:
            futures = {executor.submit(lambda chunk: chunk.apply(lambda row: get_audio_bytes_Mitrol(mitrol_api, engine, row), axis=1), chunk): chunk for chunk in df_chunks}
            
            for future in as_completed(futures):
                chunk = futures[future]
                try:
                    result = future.result()
                    chunk['audio_dir'] = result
                except Exception as exc:
                    print(f'Generated an exception: {exc}')
        
        # Unir los chunks en un único DataFrame
        df = pd.concat([pd.DataFrame(chunk) for chunk in df_chunks])
        return df
    elif api is None:
        df['audio_dir'] = df.apply(lambda row: get_audio_bytes_Vitalis_Salud(row), axis=1)
        return df

