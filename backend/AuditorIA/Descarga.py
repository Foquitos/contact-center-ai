import io
import os
import pandas as pd

from typing import Union
from datetime import datetime
from AuditorIA.downloads.CYT_comunicaciones import CYT_comunicaciones
from AuditorIA.downloads.CXOne import CXOne
from AuditorIA.downloads.Hermes import Hermes
from AuditorIA.downloads.Mitrol import Mitrol
from AuditorIA.downloads.Verint import Verint
from AuditorIA.downloads.AsterVoIP import AsterVoIP
from AuditorIA.SQL_query import *

from AuditorIA.get_audio_bytes import get_audio_bytes




def descarga_sin_verificar(
    engine,
    api: Union[Mitrol, Verint, CXOne, Hermes, CYT_comunicaciones, AsterVoIP, None],
    cantidad:int=1, 
    num_chunks:int=10,
    idInteraccion:Union[list[str], None]=None,
    Segmento:Union[list[int], None]=None,
    Fecha_desde:Union[datetime, None]=None, 
    Fecha_hasta:Union[datetime, None]=None, 
    loginid:Union[list[str], None]=None, 
    empresa:Union[list[str], None]=None, 
    campana:Union[list[str], None]=None, 
    duracion_min:Union[int, None]=None,
    duracion_max:Union[int, None]=None,
    tipificacion:Union[list[str], None]=None,
    sentido:Union[list[str], None]=None,
    comentario:Union[list[str], None]=None,
    por_operador:bool=False,
    por_tipificacion:bool=False,
    omitir_limite:bool=False,
    reauditar:bool=False
    )->pd.DataFrame:

    """Descarga de los audios segun los filtros dados, en caso de error en algun archivo, no devuelve la fila con dicho archivo

    Args:
        engine (_type_): _description_
        api (Type[Mitrol]): _description_
        cantidad (int, optional): Cantidad de audios a descargar. Defaults to 1.
        num_chunks (int, optional): Cantidad de hilos generados para aumentar la velocidad a cambio de mayor coste de red y procesador. Defaults to 10.
        idInteraccion (str, optional): _description_. Defaults to None.
        Segmento (int, optional): _description_. Defaults to None.
        Fecha_desde (str, optional): _description_. Defaults to None.
        Fecha_hasta (str, optional): _description_. Defaults to None.
        loginid (str, optional): _description_. Defaults to None.
        empresa (str, optional): _description_. Defaults to None.
        campana (str, optional): _description_. Defaults to None.
        duracion_min (int, optional): _description_. Defaults to None.

    Returns:
        _type_: Devuelve un df con la identificaion de cada audio y una columna con los audios en formato de bits
    """
    empresa = empresa or []
    
    if 'AuroraSalud' in empresa:
        df = get_filtered_data_Aurora_salud(
        cantidad=cantidad,
        engine= engine,
        idInteraccion=idInteraccion,
        Segmento=Segmento,
        Fecha_desde=Fecha_desde,
        Fecha_hasta=Fecha_hasta,
        loginid=loginid,
        duracion_min=duracion_min,
        duracion_max=duracion_max,
        tipificacion=tipificacion,
        sentido=sentido,
        comentario=comentario,
        reauditar=reauditar,
        por_operador=por_operador,
        por_tipificacion=por_tipificacion,
        omitir_limite=omitir_limite
        )
    elif 'Odonto Plus' in empresa or 'Facebook' in empresa:
        df = get_filtered_data_Odonto_plus(
        cantidad=cantidad,
        engine= engine,
        idInteraccion=idInteraccion,
        campana=campana,
        Segmento=Segmento,
        Fecha_desde=Fecha_desde,
        Fecha_hasta=Fecha_hasta,
        loginid=loginid,
        duracion_min=duracion_min,
        duracion_max=duracion_max,
        tipificacion=tipificacion,
        sentido=sentido,
        comentario=comentario,
        reauditar=reauditar,
        por_operador=por_operador,
        por_tipificacion=por_tipificacion,
        omitir_limite=omitir_limite
        )
    elif 'HIDRA Comercial' in empresa:
        df = get_filtered_data_Hidra_comercial(
        cantidad=cantidad,
        engine= engine,
        idInteraccion=idInteraccion,
        Fecha_desde=Fecha_desde,
        Fecha_hasta=Fecha_hasta,
        loginid=loginid,
        duracion_min=duracion_min,
        duracion_max=duracion_max,
        reauditar=reauditar,
        por_operador=por_operador,
        por_tipificacion=por_tipificacion,
        omitir_limite=omitir_limite
        )
    elif 'HIDRA' in empresa:
        df = get_filtered_data_Hidra(
        cantidad=cantidad,
        engine= engine,
        idInteraccion=idInteraccion,
        Segmento=Segmento,
        Fecha_desde=Fecha_desde,
        Fecha_hasta=Fecha_hasta,
        loginid=loginid,
        duracion_min=duracion_min,
        duracion_max=duracion_max,
        tipificacion=tipificacion,
        sentido=sentido,
        comentario=comentario,
        reauditar=reauditar,
        por_operador=por_operador,
        por_tipificacion=por_tipificacion,
        omitir_limite=omitir_limite
        )
    elif 'ALARMIX' in empresa:
        df = get_filtered_data_ALARMIX(
        cantidad=cantidad,
        engine= engine,
        idInteraccion=idInteraccion,
        Fecha_desde=Fecha_desde,
        Fecha_hasta=Fecha_hasta,
        direccion=sentido,
        Empleado=loginid,
        tipificacion=tipificacion,
        skill=campana,
        duracion_min=duracion_min,
        duracion_max=duracion_max,
        reauditar=reauditar,
        por_operador=por_operador,
        por_tipificacion=por_tipificacion,
        omitir_limite=omitir_limite
        )
    elif 'Vitalis Salud' in empresa:
        df = get_filtered_data_Vitalis_Salud(
        cantidad=cantidad,
        engine= engine,
        idInteraccion=idInteraccion,
        Fecha_desde=Fecha_desde,
        Fecha_hasta=Fecha_hasta,
        loginid=loginid,
        duracion_min=duracion_min,
        duracion_max=duracion_max,
        tipificacion=tipificacion,
        direccion=sentido,
        reauditar=reauditar,
        por_operador=por_operador,
        por_tipificacion=por_tipificacion,
        omitir_limite=omitir_limite
        )
    elif 'Farmalux' in empresa:
        df = get_filtered_data_Farmalux(
        cantidad=cantidad,
        engine= engine,
        idInteraccion=idInteraccion,
        Fecha_desde=Fecha_desde,
        Fecha_hasta=Fecha_hasta,
        loginid=loginid,
        duracion_min=duracion_min,
        duracion_max=duracion_max,
        campana=campana,
        comentario=comentario,
        reauditar=reauditar,
        por_operador=por_operador,
        por_tipificacion=por_tipificacion,
        omitir_limite=omitir_limite
        )
    elif 'Track On' in empresa or 'Trackon-Vantix' in empresa:
        df = get_filtered_data_Vantix(
        cantidad=cantidad,
        engine= engine,
        idInteraccion=idInteraccion,
        Fecha_desde=Fecha_desde,
        Fecha_hasta=Fecha_hasta,
        loginid=loginid,
        duracion_min=duracion_min,
        duracion_max=duracion_max,
        empresa=empresa,
        campana=campana,
        tipificacion=tipificacion,
        sentido=sentido,
        comentario=comentario,
        reauditar=reauditar,
        por_operador=por_operador,
        por_tipificacion=por_tipificacion,
        omitir_limite=omitir_limite
        )
    elif 'Benefix' in empresa:
        df = get_filtered_data_Benefix(
        cantidad=cantidad,
        engine= engine,
        idInteraccion=idInteraccion,
        Fecha_desde=Fecha_desde,
        Fecha_hasta=Fecha_hasta,
        loginid=loginid,
        duracion_min=duracion_min,
        duracion_max=duracion_max,
        campana=campana,
        tipificacion=tipificacion,
        sentido=sentido,
        comentario=comentario,
        reauditar=reauditar,
        por_operador=por_operador,
        por_tipificacion=por_tipificacion,
        omitir_limite=omitir_limite
        )
    else:
        df = get_filtered_data_mitrol_puro(
        cantidad=cantidad,
        engine= engine,
        idInteraccion=idInteraccion,
        Segmento=Segmento,
        Fecha_desde=Fecha_desde,
        Fecha_hasta=Fecha_hasta,
        loginid=loginid,
        empresa=empresa,
        campana=campana,
        duracion_min=duracion_min,
        duracion_max=duracion_max,
        tipificacion=tipificacion,
        sentido=sentido,
        comentario=comentario,
        reauditar=reauditar,
        por_operador=por_operador,
        por_tipificacion=por_tipificacion,
        omitir_limite=omitir_limite
        )

    num_chunks = min(cantidad, num_chunks, len(df)) if len(df) > 0 else 1
    
    df = get_audio_bytes(df, engine, api, num_chunks, empresa)

    # Filtra filas donde la descarga falló (audio_dir es nulo o vacío)
    # MODIFICACION: Solo borramos si NO se especificaron IDs. Si se pidieron IDs específicos,
    # queremos saber que fallaron.

    # Función para verificar si el audio es válido (tamaño > 100KB)
    def es_audio_valido(filepath):
        tamanio_minimo_bytes = 1 * 1024
        try:
            # 1. CASO VITALIS SALUD: El archivo está en memoria (io.BytesIO)
            if isinstance(filepath, io.BytesIO):
                if len(filepath.getvalue()) > tamanio_minimo_bytes:
                    return True
                else:
                    print("Audio en memoria inválido (tamaño <= 1KB o vacío). Se eliminará la fila.")
                    return False

            # 2. CASO RESTO: El archivo es una ruta física en el disco (str)
            if filepath and isinstance(filepath, str) and os.path.exists(filepath):
                # Manejo especial para CHATS guardados en JSON
                if filepath.endswith('.json'):
                    # Con que el JSON pese más de 0 bytes, asumimos que extrajo historial
                    if os.path.getsize(filepath) > 0:
                        return True
                    else:
                        print(f"Chat JSON inválido (vacío): {filepath}. Se eliminará la fila.")
                        os.remove(filepath)
                        return False
                # Manejo regular para audios (WAV/MP3)
                elif os.path.getsize(filepath) > tamanio_minimo_bytes:
                    return True
                else:
                    print(f"Audio inválido (tamaño <= 1KB): {filepath}. Se eliminará la fila.")
                    os.remove(filepath) 
                    return False
            
            return False
        
        except (TypeError, OSError) as e:
            print(f"Error al procesar el archivo {filepath}: {e}")
            return False

    if not df.empty:
        # Crea una máscara booleana aplicando la función de validación
        mascara_valida = df['audio_dir'].apply(es_audio_valido)

        # Si se pidieron IDs específicos, mantenemos las filas invalidas pero marcamos el error (implícitamente audio_dir=None o invalido)
        if idInteraccion:
             # Solo logueamos que fallaron algunos
             failed_count = (~mascara_valida).sum()
             if failed_count > 0:
                 print(f"Advertencia: {failed_count} audios fallaron la descarga o validación, pero se mantienen porque se solicitaron IDs específicos.")
                 # Opcional: Podríamos poner 'audio_dir' a None explícitamente para los inválidos
                 df.loc[~mascara_valida, 'audio_dir'] = None
        else:
            # Filtra el DataFrame para mantener solo las filas con audios válidos
            df = df[mascara_valida]

    return df



def descarga_aleatoria(
    engine,
    api: Union[Mitrol, Verint, CXOne, Hermes, CYT_comunicaciones, AsterVoIP, None],
    cantidad:int=1, 
    num_chunks:int=10,
    idInteraccion:Union[list[str], None]=None,
    Segmento:Union[list[int], None]=None,
    Fecha_desde:Union[datetime, None]=None, 
    Fecha_hasta:Union[datetime, None]=None, 
    loginid:Union[list[str], None]=None, 
    empresa:Union[list[str], None]=None, 
    campana:Union[list[str], None]=None, 
    duracion_min:Union[int, None]=None,
    duracion_max:Union[int, None]=None,
    tipificacion:Union[list[str], None]=None,
    sentido:Union[list[str], None]=None,
    comentario:Union[list[str], None]=None,
    por_operador:bool=False,
    por_tipificacion:bool=False,
    omitir_limite:bool=False,
    reauditar:bool=False
    ) -> pd.DataFrame:
    """Descarga de los audios segun los filtros dados, verificando que lka cantidad devuelta sea la misma cantidad que la solicitada

    Args:
        engine (_type_): _description_
        api (Type[Mitrol]): _description_
        cantidad (int, optional): Cantidad de audios a descargar. Defaults to 1.
        num_chunks (int, optional): Cantidad de hilos generados para aumentar la velocidad a cambio de mayor coste de red y procesador. Defaults to 10.
        idInteraccion (str, optional): _description_. Defaults to None.
        Segmento (int, optional): _description_. Defaults to None.
        Fecha_desde (str, optional): _description_. Defaults to None.
        Fecha_hasta (str, optional): _description_. Defaults to None.
        loginid (str, optional): _description_. Defaults to None.
        empresa (str, optional): _description_. Defaults to None.
        campana (str, optional): _description_. Defaults to None.
        duracion_min (int, optional): _description_. Defaults to None.

    Returns:
        _type_: Devuelve un df con la identificaion de cada audio y una columna con los audios en formato de bits
    """
    df = pd.DataFrame()
    contador = 0
    empresa = empresa or []
    
    # Si tenemos IDs específicos, no queremos un loop infinito tratando de encontrar otros
    # Si idInteraccion está presente, solo hacemos un intento (o los necesarios pero sabiendo que no encontraremos 'otros')
    es_busqueda_especifica = (idInteraccion is not None)

    while len(df) < cantidad:
        df_temp = descarga_sin_verificar(
        engine=engine,
        api=api,
        num_chunks=min(cantidad, num_chunks),
        cantidad=cantidad - len(df),
        idInteraccion=idInteraccion,
        Segmento=Segmento,
        Fecha_desde=Fecha_desde,
        Fecha_hasta=Fecha_hasta,
        loginid=loginid,
        empresa=empresa,
        campana=campana,
        duracion_min=duracion_min,
        duracion_max=duracion_max,
        tipificacion=tipificacion,
        sentido=sentido,
        comentario=comentario,
        por_operador=por_operador,
        por_tipificacion=por_tipificacion,
        omitir_limite=omitir_limite,
        reauditar=reauditar
        )


        df = pd.concat([df,df_temp])
        if 'ALARMIX' in empresa:
            df = df.drop_duplicates(subset='segmentId', keep='first')
        elif 'HIDRA Comercial' in empresa:
            df = df.drop_duplicates(subset='ID', keep='first')
        elif 'Vitalis Salud' in empresa:
            df = df.drop_duplicates(subset='caso_id', keep='first')
        elif 'Farmalux' in empresa:
            df = df.drop_duplicates(subset='id', keep='first')
        elif 'Track On' in empresa or 'Trackon-Vantix' in empresa:
            df = df.drop_duplicates(subset='ID del llamado', keep='first')
        elif 'Benefix' in empresa:
            df = df.drop_duplicates(subset='ID', keep='first')
        else:
            # Caso por defecto (Mitrol/Verint genérico)
            # Solo entra aquí si ninguna de las anteriores se cumplió
            df = df.drop_duplicates(subset='idInteraccion', keep='first')
        
        if (por_operador or por_tipificacion) and df.empty:
            print("No se encontraron audios para los operadores especificados en HIDRA. Terminando búsqueda.")
            break
        
        contador += 1

        if es_busqueda_especifica:
            # Si buscamos IDs específicos, lo que encontramos en la primera vuelta es lo que hay (o falló).
            # No tiene sentido pedir "dame más" porque solo pedimos esos IDs.
            break

        if contador >= 3:
            break
    
    return df