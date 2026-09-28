import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from typing import Optional, Union
from app.config import settings
from app.utils.google_sheet import sheet_a_dataframe
from cachetools import cached, TTLCache

@cached(cache=TTLCache(maxsize=1, ttl=3600))
def sheet_info_csv()->pd.DataFrame:
    return sheet_a_dataframe(sheet_id=settings.SHEET_ID_INFORMACION,nombre_hoja='Informacion')

def _refresh_csv():
    """
    Limpia la caché de la función sheet_a_dataframe y la vuelve a llamar
    para obtener los datos más recientes de Google Sheets.
    """
    sheet_a_dataframe.cache_clear()
    sheet_info_csv.cache_clear()


def _informacion_csv(
    seccion_1:str,
    seccion_2:Optional[str]=None,
    seccion_3:Optional[str]=None,
    seccion_4:Optional[str]=None,
):
    df = sheet_info_csv()
    
    if seccion_1 is not None:
        df = df[df['Seccion 1'] == seccion_1]
    if seccion_2 is not None:
        df = df[df['Seccion 2'] == seccion_2]
    if seccion_3 is not None:
        df = df[df['Seccion 3'] == seccion_3]
    if seccion_4 is not None:
        df = df[df['Seccion 4'] == seccion_4]
    
    if df.empty:
        return "Información no encontrada."

    info = df.iloc[0]['Informacion']
    
    return info

def _secciones_csv():
    """
    Convierte un DataFrame con tres columnas jerárquicas en un diccionario anidado.

    Returns:
        dict: Un diccionario anidado con la estructura deseada.
    """
    df = sheet_info_csv()
    # 1. Creamos el diccionario vacío que contendrá el resultado final.
    resultado = {}

    # 2. Iteramos sobre cada fila del DataFrame.
    # 'iterrows()' nos da el índice y la fila (como una Serie de pandas).
    for _, fila in df.iterrows():
        # Obtenemos los valores de cada celda en la fila actual.
        s1 = fila.get('Seccion 1')
        s2 = fila.get('Seccion 2')
        s3 = fila.get('Seccion 3')
        s4 = fila.get('Seccion 4')
        
        if pd.isna(s1): continue # Saltar filas vacías

        # 3. Usamos setdefault para construir la estructura anidada.
        #    - Si la clave `s1` no existe en `resultado`, la crea y le asigna un diccionario vacío {}.
        #    - Luego, dentro de ese diccionario, si la clave `s2` no existe, la crea y
        #      le asigna una lista vacía [].
        diccionario_interno = resultado.setdefault(s1, {})
        diccionario_interno = diccionario_interno.setdefault(s2, {})
        lista_final = diccionario_interno.setdefault(s3, [])


        # 4. Agregamos el valor de `s4` a la lista correspondiente.
        if pd.notna(s4):
            lista_final.append(s4)

    return resultado