import json
import logging
import pandas as pd
import gspread
from functools import lru_cache
from typing import Optional, Union
from app.config import settings
from typing import List
from gspread.utils import ValueInputOption, a1_range_to_grid_range
from sqlalchemy import text
import numpy as np

from AuditorIA.avisos import AvisoUsuarioError

logger = logging.getLogger(__name__)

# 1. Cacheamos el cliente. 
# En un servidor, esto es vital para no leer el disco ni renegociar token SSL en cada request.
@lru_cache()
def get_gspread_client():
    """
    Inicializa el cliente de Google Sheets una sola vez y lo mantiene en memoria.
    Usa la ruta definida en tus settings.
    """
    # Asegúrate de que settings.CREDENTIALS_GOOGLE_SHEET_JSON sea la ruta al archivo .json
    return gspread.service_account(filename=settings.CREDENTIALS_GOOGLE_SHEET_JSON)

def sheet_a_dataframe(
    sheet_id: str, 
    nombre_hoja: Optional[str] = None, 
    id_hoja: Optional[int] = None,
    clean_headers: bool = False
) -> pd.DataFrame:
    """
    Convierte Google Sheet a DataFrame optimizado para backend.
    
    Args:
        sheet_id (str): ID del Spreadsheet.
        nombre_hoja (str, optional): Nombre de la pestaña.
        id_hoja (int, optional): GID de la pestaña.
        clean_headers (bool): Si es True, normaliza columnas (ej: "Precio Unit." -> "precio_unit")
    """
    if not nombre_hoja and id_hoja is None:
        raise ValueError("Se requiere nombre_hoja o id_hoja")

    try:
        # Obtenemos cliente de la caché
        client = get_gspread_client()
        
        # open_by_key es lo más rápido directo a la API
        sh = client.open_by_key(sheet_id)

        # Selección de hoja (priorizamos ID por ser inmutable ante cambios de nombre)
        if id_hoja is not None:
            worksheet = sh.get_worksheet_by_id(id_hoja)
        else:
            worksheet = sh.worksheet(nombre_hoja)

        if not worksheet:
             raise ValueError(f"Hoja no encontrada en Spreadsheet {sheet_id}")

        # Obtenemos datos
        data = worksheet.get_all_values()

        if not data:
            return pd.DataFrame()

        # Creación del DataFrame
        headers = data.pop(0)
        df = pd.DataFrame(data, columns=headers)

        # --- OPTIMIZACIONES DE DATOS ---
        
        # 1. Conversión automática de tipos (Vital para analítica)
        # Intenta convertir columnas numéricas que vienen como strings
        df = df.apply(pd.to_numeric, errors='ignore')

        # 2. Limpieza opcional de cabeceras para facilitar uso en JSON/API
        if clean_headers:
            df.columns = (
                df.columns
                .str.strip()
                .str.lower()
                .str.replace(' ', '_')
                .str.replace(r'[^\w]', '', regex=True) # Quita caracteres especiales
            )

        return df

    except gspread.exceptions.APIError as e:
        print(f"[ERROR GOOGLE API] {e}")
        # Aquí podrías loguear a Sentry o similar
        return pd.DataFrame()
        
    except Exception as e:
        print(f"[ERROR GENERAL] {e}")
        return pd.DataFrame()

def limpiar_df_para_gsheets(df: pd.DataFrame) -> List[List]:
    """
    Prepara el DataFrame para ser aceptado por la API de Google Sheets (JSON).
    - Reemplaza NaN/Inf por strings vacíos.
    - Convierte fechas a string.
    - Devuelve una lista de listas (matriz).
    """
    # Crear copia para no modificar el original del servidor
    df_clean = df.copy()

    # 1. Convertir fechas a string (formato YYYY-MM-DD HH:MM:SS suele funcionar bien)
    # Esto evita errores de serialización JSON
    for col in df_clean.select_dtypes(include=['datetime64', 'datetimetz']).columns:
        df_clean[col] = df_clean[col].astype(str)

    # 2. Reemplazar NaN, None e Infinitos por string vacío ""
    # Google Sheets odia los NaNs de numpy
    df_clean = df_clean.replace([np.inf, -np.inf, np.nan], "")
    df_clean = df_clean.fillna("")

    return df_clean.values.tolist()

def dataframe_a_sheet(
    sheet_id: str, 
    df: pd.DataFrame, 
    nombre_hoja: str,
    input_option: str = 'USER_ENTERED'
) -> dict:
    """
    Sube un DataFrame a Google Sheets.
    - Si la hoja NO existe: La crea y sube encabezados + datos.
    - Si la hoja SI existe: Agrega los datos al final sin repetir encabezados.
    - Aplica formato CLIP para evitar que las filas se expandan a lo alto con textos largos.
    """
    if df.empty:
        return {"status": "warning", "message": "DataFrame vacío, no se subió nada."}

    try:
        client = get_gspread_client()
        sh = client.open_by_key(sheet_id)
    except gspread.exceptions.SpreadsheetNotFound as e:
        raise AvisoUsuarioError(
            f"No se encontró el Google Sheet de destino (id {sheet_id}). "
            "Verifique que el ID sea correcto y que el documento exista."
        ) from e
    except gspread.exceptions.APIError as e:
        raise AvisoUsuarioError(
            f"Sin acceso al Google Sheet de destino (id {sheet_id}): "
            "compártalo como editor con la cuenta de servicio del sistema."
        ) from e

    # Limpiamos los datos (valores NaN -> "")
    datos_valores = limpiar_df_para_gsheets(df)

    try:
        try:
            # INTENTO 1: Buscar la hoja existente
            worksheet = sh.worksheet(nombre_hoja)
            existe = True
        except gspread.exceptions.WorksheetNotFound:
            # INTENTO 2: No existe, así que la creamos
            existe = False
            rows = len(datos_valores) + 50
            cols = len(df.columns)
            worksheet = sh.add_worksheet(title=nombre_hoja, rows=rows, cols=cols)

        if input_option not in ['RAW', 'USER_ENTERED']:
            input_option = 'USER_ENTERED'

        if input_option == 'RAW':
            input_option = ValueInputOption.raw
        else:
            input_option = ValueInputOption.user_entered


        if existe:
            # --- ESCENARIO: APPEND (Hoja existe) ---
            worksheet.append_rows(
                datos_valores,
                value_input_option=input_option
            )
            accion = "datos anexados"
        else:
            # --- ESCENARIO: CREACIÓN (Hoja nueva) ---
            encabezados = df.columns.tolist()
            payload = [encabezados] + datos_valores

            worksheet.update(
                values=payload,
                range_name='A1',
                value_input_option=input_option
            )
            accion = "hoja creada y datos subidos"
    except gspread.exceptions.APIError as e:
        # Caso típico: el sheet está compartido solo-lectura y la falla
        # recién aparece al intentar crear la hoja o escribir.
        raise AvisoUsuarioError(
            f"No se pudo escribir en el Google Sheet de destino (id {sheet_id}): "
            "verifique que la cuenta de servicio del sistema tenga permiso de editor."
        ) from e

    # --- NUEVO: PREVENIR EXPANSIÓN DE FILAS ---
    # Formateamos un rango amplio (ej: desde A hasta ZZ) para aplicar la regla a todas las columnas.
    # Si sabes que nunca pasarás de la columna Z, puedes usar "A:Z" para ser más eficiente.
    # try:
    #     worksheet.format("A:ZZ", {
    #         "wrapStrategy": "CLIP" 
    #     })
    # except Exception as e:
    #     print(f"Advertencia: No se pudo aplicar el formato CLIP. Error: {e}")

    return {"status": "ok", "action": accion, "rows_affected": len(datos_valores)}


# Columnas internas del SP que nunca salen a un export (flags de UI y textos enormes).
# Ojo: 'TrascripcionJSON' está escrito así, sin la 'n', desde el origen; el SP en realidad
# devuelve 'TranscripcionJSON'. Se deja tal cual A PROPÓSITO: corregirlo hoy sacaría una
# columna de los exports que ya se vienen appendeando a hojas existentes y desalinearía
# todo el histórico. Si algún día se corrige, hay que hacerlo con hoja nueva.
COLUMNAS_TECNICAS_EXPORT = [
    'ExisteTranscripcion', 'ExisteResponseThoughts', 'TrascripcionJSON', 'ResponseThoughts',
]

# Columnas del SP que son OPT-IN en los exports automatizados: existen en la pantalla y se
# pueden pedir desde una plantilla de columnas, pero no se agregan solas.
#
# Por qué: dataframe_a_sheet AGREGA las filas al final de la hoja si ya existe, sin volver
# a escribir los encabezados. El scheduler manda siempre al mismo `gsheet_name`, así que
# una columna nueva —y encima insertada en el medio, como Incidencia entre PuntajeFinal y
# EsErrorCritico— desalinearía todas las filas nuevas contra los encabezados viejos. Quien
# la quiera en el export la agrega a su plantilla de columnas y ahí sale.
COLUMNAS_OPT_IN_EXPORT = ['Incidencia']


def preparar_export(engine, df: pd.DataFrame, column_template_id: Optional[int]) -> pd.DataFrame:
    """Deja el DataFrame del SP listo para exportar (Sheets / adjunto de correo).

    Saca las columnas técnicas, saca las opt-in cuando no hay plantilla de columnas que
    las pida, y aplica la plantilla si la hay. Compartida entre el flujo sincrónico del
    scheduler (app.tasks) y el asíncrono de batches (Auditor.procesar_batch) para que
    ambos exporten exactamente el mismo set de columnas.
    """
    if df is None or df.empty:
        return df

    a_sacar = list(COLUMNAS_TECNICAS_EXPORT)
    if not column_template_id:
        # Sin plantilla se sube lo que devuelve el SP: ahí la forma tiene que quedar
        # igual que siempre o se rompe el append a la hoja existente.
        a_sacar += COLUMNAS_OPT_IN_EXPORT

    df = df.drop(columns=a_sacar, errors='ignore')
    return aplicar_column_template(engine, df, column_template_id)


def aplicar_column_template(engine, df: pd.DataFrame, column_template_id: Optional[int]) -> pd.DataFrame:
    """
    Si hay una plantilla de columnas asignada, deja en el DataFrame solo las columnas
    listadas (en el orden de la plantilla). Si no hay plantilla, no se encuentra o la
    consulta falla, devuelve el DataFrame sin tocar.

    Compartida entre el flujo sincrónico del scheduler (app.tasks) y el procesamiento
    asíncrono de batches (Auditor.procesar_batch) para que ambos exporten exactamente
    las mismas columnas que la pantalla "Auditorías Realizadas".
    """
    if not column_template_id or df is None or df.empty:
        return df

    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT columns_json FROM [calidad].[AuditColumnTemplates] WHERE id = :id"),
                {"id": column_template_id},
            ).mappings().first()
    except Exception as e:
        logger.warning(f"No se pudo cargar la plantilla de columnas {column_template_id}: {e}. Se envía el DF completo.")
        return df

    if not row or not row.get("columns_json"):
        logger.warning(f"Plantilla de columnas {column_template_id} no encontrada. Se envía el DF completo.")
        return df

    try:
        columnas = json.loads(row["columns_json"])
    except json.JSONDecodeError:
        logger.warning(f"columns_json corrupto en plantilla {column_template_id}. Se envía el DF completo.")
        return df

    # Solo las columnas existentes en el DF, preservando el orden de la plantilla.
    columnas_existentes = [c for c in columnas if c in df.columns]
    if not columnas_existentes:
        logger.warning(f"Ninguna columna de la plantilla {column_template_id} coincide con el DF. Se envía el DF completo.")
        return df
    return df[columnas_existentes]