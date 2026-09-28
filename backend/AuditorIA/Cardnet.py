import datetime
import io
import os
import numpy as np
import pandas as pd
from sqlalchemy import Engine

from AuditorIA.avisos import AvisoUsuarioError
from AuditorIA.downloads.Yoizen import Yoizen

from app.config import settings
from app.utils.google_sheet import sheet_a_dataframe
from cachetools import cached, TTLCache


# Columnas que el informe de UCIDs (export de Verint) tiene que traer sí o sí:
# 'Segment Start Time' es la que cruza cada segmento con el informe de audios
# (savedFiles) y 'Segment UCID' la que después cruza contra el ECH de Yoizen.
#
# Ojo con esto: el informe se puede exportar a nivel LLAMADA o a nivel SEGMENTO, y
# solo el de segmento sirve. El de llamada trae 'Complete Start Time' en lugar de
# 'Segment Start Time' y hace reventar todo el proceso con un KeyError ilegible a
# 300 líneas de acá; por eso se valida al leer y no cuando se arma el merge.
COLUMNAS_UCID_REQUERIDAS = ['Full Name', 'Segment Start Time', 'Segment UCID']

# Ídem para el informe de audios (savedFiles.txt): sin estas dos no hay con qué
# cruzar el archivo de audio contra el segmento del informe de UCIDs.
COLUMNAS_AUDIOS_REQUERIDAS = ['Agent name', 'Start time']


def leer_informe_audios(ubicacion:str)->pd.DataFrame:
    def read_custom_txt(file_path):
        with open(file_path, 'r',encoding='utf-8') as file:
            content = file.read()
        
        sections = content.split('File name:')
        sections = [section.strip() for section in sections if section.strip()]
        
        all_data = []
        
        for section in sections:
            lines = section.split('\n')
            file_name = lines[0].strip()
            created = lines[1].split(':', 1)[1].strip()
            
            table_start = next(i for i, line in enumerate(lines) if '-------' in line)
            header = lines[table_start - 1]
            data = lines[table_start + 1]
            
            df = pd.read_csv(io.StringIO(header + '\n' + data), sep='\t', skipinitialspace=True)
            
            df = df.loc[:, ~df.columns.str.contains('^Unnamed')]
            
            # Corregir nombres de columnas
            df.columns = df.columns.str.strip()
            
            df['File name'] = file_name
            df['Created'] = created
            
            all_data.append(df)
        
        result = pd.concat(all_data, ignore_index=True)
        
        # Corregir nombres de columnas en el DataFrame final
        result.columns = result.columns.str.strip()
        
        return result

    def Borrar_raiz(Ubicacion: str) -> str:
        return Ubicacion.split('\\')[-1]

    nombre = os.path.basename(ubicacion)
    try:
        df_audios = read_custom_txt(ubicacion)
    except Exception as e:
        # El parseo es a medida del savedFiles de Verint (secciones "File name:" y
        # una tabla separada por tabs). Cualquier otro formato explota con un
        # StopIteration/IndexError que no dice nada de lo que hay que corregir.
        raise AvisoUsuarioError(
            f"No se pudo leer el informe de audios '{nombre}'. Tiene que ser el .txt de "
            "savedFiles tal cual lo genera Verint, sin editar."
        ) from e

    faltantes = [c for c in COLUMNAS_AUDIOS_REQUERIDAS if c not in df_audios.columns]
    if faltantes:
        raise AvisoUsuarioError(
            f"Al informe de audios '{nombre}' le faltan columnas necesarias para cruzar los "
            f"audios con el informe de UCIDs: {', '.join(faltantes)}. "
            f"Columnas que trae: {', '.join(df_audios.columns)}."
        )

    df_audios['File name'] = df_audios['File name'].apply(Borrar_raiz)
    
    return df_audios


def leer_informe_ucid(ubicacion:str)->pd.DataFrame:
    nombre = os.path.basename(ubicacion)

    # Leer el archivo CSV
    try:
        df_UCID = pd.read_csv(ubicacion, sep=",", encoding="UTF-16 LE")
    except Exception as e:
        raise AvisoUsuarioError(
            f"No se pudo leer el informe de UCIDs '{nombre}'. Tiene que ser el CSV tal cual "
            "lo exporta Verint (codificación UTF-16, separado por comas), sin abrirlo ni "
            "volverlo a guardar desde Excel."
        ) from e

    # Los encabezados del export vienen con espacios de más según cómo se haya
    # generado; sin esto, una columna presente puede darse por faltante.
    df_UCID.columns = df_UCID.columns.str.strip()

    faltantes = [c for c in COLUMNAS_UCID_REQUERIDAS if c not in df_UCID.columns]
    if faltantes:
        pista = ""
        if 'Segment Start Time' in faltantes and 'Complete Start Time' in df_UCID.columns:
            pista = (
                " El informe parece exportado a nivel LLAMADA (trae 'Complete Start Time'): "
                "hay que exportarlo a nivel SEGMENTO para que traiga las columnas de segmento."
            )
        raise AvisoUsuarioError(
            f"Al informe de UCIDs '{nombre}' le faltan columnas necesarias para auditar: "
            f"{', '.join(faltantes)}.{pista} "
            f"Columnas que trae: {', '.join(df_UCID.columns)}."
        )

    # Aplicar strip() a las columnas 'Full Name' y 'Complete Start Time'
    df_UCID['Full Name'] = df_UCID['Full Name'].str.strip()
    df_UCID['Segment Start Time'] = df_UCID['Segment Start Time'].str.strip()
    
    return df_UCID

@cached(cache=TTLCache(maxsize=1, ttl=3600))
def sheet_internos()->pd.DataFrame:
    return sheet_a_dataframe(sheet_id=settings.SHEET_ID_INTERNOS,nombre_hoja='Internos (Avaya)')

def _parse_segundos(val) -> float:
    """Convierte cadenas de tiempo (HH:MM:SS, MM:SS) o datetime.time a segundos."""
    if pd.isna(val):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if hasattr(val, 'hour') and hasattr(val, 'minute') and hasattr(val, 'second'):
        return float(val.hour * 3600 + val.minute * 60 + val.second)
    parts = str(val).strip().split(':')
    try:
        if len(parts) == 3:
            return float(int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2]))
        elif len(parts) == 2:
            return float(int(parts[0]) * 60 + float(parts[1]))
        elif len(parts) == 1:
            return float(parts[0])
    except Exception:
        pass
    return 0.0

def generar_df_llamados_csv(
    ucid:str,
    carpeta_audios:str,
    savedFiles:str
    )-> pd.DataFrame:
    df_UCID = leer_informe_ucid(ucid)
    df_audios = leer_informe_audios(savedFiles)


    df = pd.merge(
        left=df_UCID,
        right=df_audios,
        how='inner',
        left_on=['Full Name', 'Segment Start Time'],
        right_on=['Agent name', 'Start time']
    )
    
    #Quedarme unicamente con los archivos que existen en la carpeta de audios
    df['audio_path'] = df['File name'].apply(lambda nombre_archivo: os.path.join(carpeta_audios, nombre_archivo))
    df = df[df['audio_path'].apply(os.path.exists)]
    df = df.drop(columns=['audio_path'])

    # Si hay múltiples coincidencias por archivo de audio en la unión inicial, nos quedamos con una
    df = df.drop_duplicates(subset=['File name'])

    # El informe de Verint trae segmentos sin 'Segment UCID' (celda vacía). Sin UCID no
    # hay con qué cruzar contra el ECH, y además los vacíos vuelven float toda la columna
    # ('1026971785196592.0'). Se descartan acá y el resto queda como entero.
    con_ucid = pd.to_numeric(df['Segment UCID'], errors='coerce')
    sin_ucid = int(con_ucid.isna().sum())
    if sin_ucid:
        print(f"Advertencia: {sin_ucid} segmento(s) del informe no traen UCID y no se pueden auditar. Se omiten.")
    df = df[con_ucid.notna()].copy()
    df['Segment UCID'] = con_ucid[con_ucid.notna()].astype('int64')

    if df.empty:
        raise AvisoUsuarioError(
            "Ningún segmento del informe quedó utilizable: revisá que el CSV de UCIDs, el "
            "informe de audios (savedFiles) y los audios de la carpeta correspondan a la misma descarga."
        )

    with Yoizen(usuario=settings.YOIZEN_USER, contraseña=settings.YOIZEN_PASS) as api:
        Reporte_EMC = api.Reporte_EHC(df['Segment UCID'].to_list())
        if Reporte_EMC is None:
            # Antes se devolvía un DataFrame vacío y el proceso seguía igual, hasta
            # reventar con un KeyError ilegible al seleccionar columnas más abajo.
            raise AvisoUsuarioError(
                "El reporte ECH no devolvió llamadas para los UCIDs del informe. "
                "Puede que el reporte no esté disponible o que esas llamadas no existan en Verint."
            )

        # Evitar duplicados por UCID en Reporte_EMC
        Reporte_EMC = Reporte_EMC.drop_duplicates(subset=['UCID'])

        df = pd.merge(
            left=df,
            right=Reporte_EMC,
            how='inner',
            left_on='Segment UCID',
            right_on='UCID'
        )
        Call_info_lista = []
        # Iteramos únicamente sobre pares únicos de (UCID, Agent ID) para evitar
        # llamadas redundantes a Yoizen y prevenir productos cartesianos en el merge.
        #
        # itertuples y no iterrows: iterrows arma cada fila como UNA Series con un solo
        # dtype, así que si 'Agent ID' es float —alcanza con un segmento del informe sin
        # agente— el UCID también pasa a float y viaja como '1044221789171087.0'. CallInfo
        # no encuentra esa llamada y devuelve la página sin la tabla de segmentos. Así se
        # cayó la carpeta ENTERA, todos los días, cada vez que le tocó una de esas
        # ("list index out of range", desde el 28/08/2026).
        pares_unicos = df[['UCID', 'Agent ID']].drop_duplicates()
        for ucid_val, agent_id in pares_unicos.itertuples(index=False, name=None):
            if pd.isna(agent_id):
                # Sin agente no hay con qué elegir el segmento de CallInfo: ese audio
                # se descarta abajo, junto con los que no tienen detalle.
                continue
            try:
                CallInfo = api.informacion_de_llamado(int(ucid_val))
            except Exception as e:
                # Una llamada con una página rara no puede tirar abajo las otras 249.
                print(f"Advertencia: falló CallInfo para el UCID {ucid_val}: {e}. Saltando.")
                continue
            if CallInfo is None:
                print(f"Advertencia: No se pudo obtener información de llamado para UCID {ucid_val}. Saltando.")
                continue
            CallInfo = CallInfo[CallInfo['Agente'] == agent_id] # type: ignore
            if CallInfo.empty:
                continue

            # Si una misma llamada tiene múltiples segmentos en Yoizen para el mismo agente (ej. transferencias,
            # cambios de skill), seleccionamos el segmento principal (mayor Talk Time / duración) para que
            # 1 archivo de audio físico corresponda exactamente a 1 registro a auditar.
            if len(CallInfo) > 1:
                col_tiempo = 'Talk Time' if 'Talk Time' in CallInfo.columns else ('Duración' if 'Duración' in CallInfo.columns else None)
                if col_tiempo:
                    CallInfo = CallInfo.copy()
                    CallInfo['_segundos_ord'] = CallInfo[col_tiempo].map(_parse_segundos)
                    CallInfo = CallInfo.sort_values(by='_segundos_ord', ascending=False).drop(columns=['_segundos_ord'])
                CallInfo = CallInfo.drop_duplicates(subset=['Agente'], keep='first').copy()

            CallInfo['UCID Original'] = ucid_val
            Call_info_lista.append(CallInfo)

        if not Call_info_lista:
            # pd.concat([]) explota con un ValueError que no dice nada del problema real.
            raise AvisoUsuarioError(
                "No se pudo obtener el detalle de ninguna de las llamadas del informe "
                "(CallInfo). Revisá la conexión con el servidor de reportes."
            )

        CallInfo = pd.concat(Call_info_lista, ignore_index=True)

    # Por llamada Y agente: CallInfo trae un segmento por (UCID, agente), y cruzar solo por
    # UCID le pegaba a cada audio de una llamada transferida los segmentos de TODOS sus
    # agentes. El drop_duplicates por archivo de más abajo se quedaba con el primero, así
    # que el audio del segundo agente salía con el UCID de segmento y el agente del primero.
    # El agente se compara como número: read_html deja 'Agente' en float (682265.0).
    df = pd.merge(
            left=df.assign(_agente=pd.to_numeric(df['Agent ID'], errors='coerce')),
            right=CallInfo.assign(_agente=pd.to_numeric(CallInfo['Agente'], errors='coerce')),
            how='left',
            left_on=['UCID', '_agente'],
            right_on=['UCID Original', '_agente']
        ).drop(columns=['_agente'])

    # Un audio sin detalle de CallInfo (llamada no encontrada, segmento sin agente o sin
    # segmento de ese agente) no tiene UCID de segmento, que es la clave con la que se
    # guarda la auditoría: seguir con él dejaba filas con clave 'nan'.
    sin_detalle = df['UCID Original'].isna()
    if sin_detalle.any():
        print(f"Advertencia: {int(sin_detalle.sum())} audio(s) sin detalle de la llamada en CallInfo; "
              f"no se pueden auditar y se omiten.")
        df = df[~sin_detalle].copy()

    if 'Type_y' in df.columns:
        df = df[df['Type_y'] != 'Interna']
    elif 'Type' in df.columns:
        df = df[df['Type'] != 'Interna']
        # Rename it to Type_y so the subsequent column selection doesn't break
        df = df.rename(columns={'Type': 'Type_y'})
    else:
        # If neither exists, print a warning and create a dummy column to prevent crashes below
        print("Advertencia: No se encontró la columna 'Type_y' ni 'Type'.")
        df['Type_y'] = 'Desconocido'

    # Deduplicación final: 1 fila por archivo de audio físico
    df = df.drop_duplicates(subset=['File name'], keep='first').reset_index(drop=True)

    df= df[['Segment Start Time','Segment Stop Time','Segment UCID','Segment Duration','Call ID','Agent ID','Extension','Dialed in number','File name','Type_y','ANI','DNIS','CollectedDigits_x','UUI_x','Segments','Abandoned','CustomerCode','Inicio','Duración','Origen','Destino','CollectedDigits_y','Skill','Agente','Tiempo en Cola','Tiempo de Ring','Talk Time','Tiempo en ACW','Tiempo en Hold','Cantidad de Holds','UCID_y','UUI_y','Finalizada por:']]

    internos = sheet_internos()
    internos = internos[['VDN','Nombre (tal cuál figura en Avaya)','IP con la que ingresa']].copy()
    internos['VDN_match'] = pd.to_numeric(internos['VDN'], errors='coerce')
    df['Destino_match'] = pd.to_numeric(df['Destino'], errors='coerce')

    df = pd.merge(
            left=df,
            right=internos,
            how='left',
            left_on='Destino_match',
            right_on='VDN_match'
        )
    df = df.drop(columns=['VDN_match', 'Destino_match'], errors='ignore')

    df['audio_dir'] = df['File name'].apply(lambda nombre_archivo: os.path.join(carpeta_audios, nombre_archivo))
    
    return df

def subida_interacciones_csv(df:pd.DataFrame,engine: Engine) -> pd.DataFrame:
    if df.empty:
        # Defensa: sin filas, el select de columnas de abajo tira un KeyError que no
        # explica nada. Quien llama ya avisó por qué no hay datos.
        return df

    df= df[['Segment Start Time','Segment Stop Time','Segment Duration','Segment UCID','Call ID','audio_dir','Agent ID','Extension','Dialed in number','Type_y','ANI','DNIS','CollectedDigits_x','UUI_x','Segments','Abandoned','CustomerCode','Inicio','Duración','Origen','Destino','CollectedDigits_y','Skill','Agente','Tiempo en Cola','Tiempo de Ring','Talk Time','Tiempo en ACW','Tiempo en Hold','Cantidad de Holds','UCID_y','UUI_y','Nombre (tal cuál figura en Avaya)','IP con la que ingresa','Finalizada por:']]
    df = df.rename(columns={
        'Segment Start Time':'inicio',
        'Segment Stop Time':'fin',
        'Segment Duration':'duracion',
        'Segment UCID':'ucid_llamado',
        'Call ID':'id_llamado',
        'Agent ID':'id_agente',
        'Extension':'extension',
        'Dialed in number':'numero_marcado',
        'Type_y':'entrante',
        'ANI':'ani',
        'DNIS':'dnis',
        'CollectedDigits_x':'digitos_seleccionados',
        'UUI_x':'uui',
        'Segments':'segmento',
        'Abandoned':'abandonado',
        'CustomerCode':'codigo_cliente',
        'Inicio':'inicio_llamado',
        'Duración':'duracion_llamado',
        'Origen':'origen',
        'Destino':'destino',
        'CollectedDigits_y':'digitos_seleccionados_segmento',
        'Skill':'skill',
        'Tiempo en Cola':'tiempo_en_cola_llamado',
        'Tiempo de Ring':'tiempo_de_ring_llamado',
        'Talk Time':'tiempo_hablado',
        'Tiempo en ACW':'tiempo_acw',
        'Tiempo en Hold':'tiempo_hold',
        'Cantidad de Holds':'holds',
        'UCID_y':'ucid_segmento',
        'UUI_y':'uui_segmento',
        'Nombre (tal cuál figura en Avaya)':'nombre_ingresante',
        'IP con la que ingresa':'ip',
        'Finalizada por:':'Finalizada_por_operador'
    })


    df_sql = pd.read_sql(
        '''
        SELECT 
            [inicio]
            ,[fin]
            ,[duracion]
            ,[ucid_llamado]
            ,[id_llamado]
            ,[id_agente]
            ,[extension]
            ,[numero_marcado]
            ,[entrante]
            ,[ani]
            ,[dnis]
            ,[digitos_seleccionados]
            ,[uui]
            ,[segmento]
            ,[abandonado]
            ,[codigo_cliente]
            ,[inicio_llamado]
            ,[duracion_llamado]
            ,[origen]
            ,[destino]
            ,[digitos_seleccionados_segmento]
            ,[skill]
            ,[tiempo_en_cola_llamado]
            ,[tiempo_de_ring_llamado]
            ,[tiempo_hablado]
            ,[tiempo_acw]
            ,[tiempo_hold]
            ,[holds]
            ,[ucid_segmento]
            ,[uui_segmento]
            ,[nombre_ingresante]
            ,[ip]
            ,[transcripcion]
            ,[Finalizada_por_operador]
        FROM [Acme].[calidad].[interacciones_CSV]
        ''', engine)

    # Filtra las llamadas entrantes y limpia 'ucid_segmento' en df
    df = df[df['entrante'] == 'Entrante']
    df['ucid_segmento'] = df['ucid_segmento'].astype(str).str.strip().str.replace(r'\.0$', '', regex=True)

    # Limpia 'ucid_segmento' en df_sql
    df_sql['ucid_segmento'] = df_sql['ucid_segmento'].astype(str).str.strip()

    # Filtra df para quedarse con las filas que no están en df_sql



    if not df.empty:
        df = df[[
            'inicio',
            'fin',
            'duracion',
            'audio_dir',
            'ucid_llamado',
            'id_llamado',
            'id_agente',
            'extension',
            'numero_marcado',
            'entrante',
            'ani',
            'dnis',
            'digitos_seleccionados',
            'uui',
            'segmento',
            'abandonado',
            'codigo_cliente',
            'inicio_llamado',
            'duracion_llamado',
            'origen',
            'destino',
            'digitos_seleccionados_segmento',
            'skill',
            'tiempo_en_cola_llamado',
            'tiempo_de_ring_llamado',
            'tiempo_hablado',
            'tiempo_acw',
            'tiempo_hold',
            'holds',
            'ucid_segmento',
            'uui_segmento',
            'nombre_ingresante',
            'ip',
            'Finalizada_por_operador'
        ]]


        df['inicio'] = pd.to_datetime(df['inicio'], format='%d/%m/%Y %H:%M:%S')
        df['fin'] = df['fin'].str.strip()
        df['fin'] = pd.to_datetime(df['fin'], format='%d/%m/%Y %H:%M:%S')
        df['entrante'] = df['entrante'].apply(lambda x: 1 if x == 'Entrante' else 0)
        df['Finalizada_por_operador'] = df['Finalizada_por_operador'].apply(lambda x: 1 if x != 'Cliente' else 0)
        df.replace('-', np.nan, inplace=True)

        df_para_sql = df[[
            'inicio',
            'fin',
            'duracion',
            'ucid_llamado',
            'id_llamado',
            'id_agente',
            'extension',
            'numero_marcado',
            'entrante',
            'ani',
            'dnis',
            'digitos_seleccionados',
            'uui',
            'segmento',
            'abandonado',
            'codigo_cliente',
            'inicio_llamado',
            'duracion_llamado',
            'origen',
            'destino',
            'digitos_seleccionados_segmento',
            'skill',
            'tiempo_en_cola_llamado',
            'tiempo_de_ring_llamado',
            'tiempo_hablado',
            'tiempo_acw',
            'tiempo_hold',
            'holds',
            'ucid_segmento',
            'uui_segmento',
            'nombre_ingresante',
            'ip',
            'Finalizada_por_operador'
        ]]
        df_para_sql = df_para_sql.drop_duplicates(subset=['ucid_segmento'], keep='first')
        df_para_sql = df_para_sql[~df_para_sql['ucid_segmento'].isin(df_sql['ucid_segmento'])]
        if not df_para_sql.empty:
            df_para_sql.to_sql('interacciones_CSV', engine, if_exists='append', index=False,schema='calidad')

    # Paso 8: Concatenar los registros ya existentes con los nuevos procesados en pandas (si es necesario)
    df = df[[
            'inicio',
            'fin',
            'duracion',
            'audio_dir',
            'ucid_llamado',
            'id_llamado',
            'id_agente',
            'extension',
            'numero_marcado',
            'entrante',
            'ani',
            'dnis',
            'digitos_seleccionados',
            'uui',
            'segmento',
            'abandonado',
            'codigo_cliente',
            'inicio_llamado',
            'duracion_llamado',
            'origen',
            'destino',
            'digitos_seleccionados_segmento',
            'skill',
            'tiempo_en_cola_llamado',
            'tiempo_de_ring_llamado',
            'tiempo_hablado',
            'tiempo_acw',
            'tiempo_hold',
            'holds',
            'ucid_segmento',
            'uui_segmento',
            'nombre_ingresante',
            'ip',
            'Finalizada_por_operador'
        ]]


    df = df[['ucid_segmento','inicio','skill','holds','audio_dir','Finalizada_por_operador','id_agente','destino']]
    
    return df

