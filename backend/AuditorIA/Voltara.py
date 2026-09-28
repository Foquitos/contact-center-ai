"""Pipeline de la auditoría de Voltara por subida de archivos.

A diferencia del resto de las campañas (que bajan el audio de la grabadora), Voltara
funciona como CSV: el usuario sube a la plataforma los audios + un Excel con dos
columnas (nombre del archivo y ConnID). Acá se matchea cada audio con su ConnID y se
enriquece con la info del llamado ([Voltara informe IVR]) y, a modo de contexto, un
resumen de los casos que el mismo agente gestionó en Salesforce
([Voltara_Salesforce_casos_cerrados]) en los días cercanos al llamado -ver el comentario
en SQL_query.get_filtered_data_Voltara para el porqué (el caso NO se abre en vivo
durante la llamada, así que no se puede asociar un caso puntual a cada interacción)-.

El resultado es un DataFrame con una fila por audio, la ruta del audio en `audio_dir`
y las columnas de contexto del llamado (+ resumen de casos) que después viajan a Gemini.
"""
import os
import logging

import pandas as pd
from sqlalchemy import Engine

from AuditorIA.SQL_query import get_filtered_data_Voltara

logger = logging.getLogger(__name__)

# Extensiones de audio aceptadas en la carpeta de subida.
EXTENSIONES_AUDIO = ('.wav', '.mp3', '.ogg', '.opus', '.m4a', '.flac', '.wma', '.aac')


def leer_mapa_voltara(ruta_excel: str) -> pd.DataFrame:
    """Lee el Excel de mapeo (dos columnas: nombre de archivo y ConnID).

    La detección de columnas es tolerante: se busca por nombre ("archivo"/"nombre"/
    "file" para el audio y "conn" para el ConnID) y, si no matchea, se usan las dos
    primeras columnas en orden. Devuelve un df normalizado ['nombre_archivo', 'ConnID'].
    """
    df = pd.read_excel(ruta_excel, dtype=str)
    if df.empty or df.shape[1] < 2:
        logger.warning("El Excel de mapeo de Voltara no tiene al menos 2 columnas.")
        return pd.DataFrame(columns=['nombre_archivo', 'ConnID'])

    cols = {str(c).lower().strip(): c for c in df.columns}

    def _buscar(*claves, default):
        for k, original in cols.items():
            if any(clave in k for clave in claves):
                return original
        return default

    col_archivo = _buscar('archivo', 'nombre', 'file', 'audio', default=df.columns[0])
    col_conn = _buscar('conn', default=df.columns[1])
    # Evitar que ambas apunten a la misma columna (si el nombre matchea las dos búsquedas).
    if col_conn == col_archivo:
        col_conn = next((c for c in df.columns if c != col_archivo), df.columns[1])

    out = df[[col_archivo, col_conn]].copy()
    out.columns = ['nombre_archivo', 'ConnID']
    out['nombre_archivo'] = out['nombre_archivo'].astype(str).str.strip()
    out['ConnID'] = out['ConnID'].astype(str).str.strip()
    out = out[
        (out['nombre_archivo'] != '') & (out['nombre_archivo'].str.lower() != 'nan')
        & (out['ConnID'] != '') & (out['ConnID'].str.lower() != 'nan')
    ]
    return out.drop_duplicates(subset='nombre_archivo').reset_index(drop=True)


def _indexar_audios(carpeta_audios: str) -> dict[str, str]:
    """Índice {clave_normalizada -> nombre_de_archivo_real} de los audios en la carpeta.

    Se indexa por nombre completo y por nombre sin extensión, en minúsculas, para
    matchear el Excel tenga o no la extensión y sin importar mayúsculas.
    """
    indice: dict[str, str] = {}
    try:
        archivos = os.listdir(carpeta_audios)
    except OSError as e:
        logger.error(f"No se pudo listar la carpeta de audios de Voltara ({carpeta_audios}): {e}")
        return indice
    for f in archivos:
        if not f.lower().endswith(EXTENSIONES_AUDIO):
            continue
        indice.setdefault(f.lower(), f)
        indice.setdefault(os.path.splitext(f)[0].lower(), f)
    return indice


def generar_df_llamados_voltara(carpeta_audios: str, ruta_excel: str, engine: Engine) -> pd.DataFrame:
    """Arma el DataFrame de auditoría de Voltara a partir de los archivos subidos.

    1. Lee el Excel (nombre de archivo -> ConnID).
    2. Matchea cada fila con un audio realmente presente en `carpeta_audios`.
    3. Enriquece los ConnID con el llamado (IVR) + caso (Salesforce) vía SQL.
    4. Devuelve una fila por audio, con `audio_dir` apuntando al archivo subido.

    Un audio del Excel sin ConnID en [Voltara informe IVR] se descarta (no hay
    metadatos del llamado para auditarlo).
    """
    mapa = leer_mapa_voltara(ruta_excel)
    if mapa.empty:
        logger.warning("El mapa de Voltara (Excel) quedó vacío tras leerlo.")
        return pd.DataFrame()

    indice_audios = _indexar_audios(carpeta_audios)
    if not indice_audios:
        logger.warning(f"No se encontraron audios en {carpeta_audios} para la subida de Voltara.")
        return pd.DataFrame()

    def _resolver(nombre: str):
        n = str(nombre).strip().lower()
        return indice_audios.get(n) or indice_audios.get(os.path.splitext(n)[0])

    mapa['archivo_real'] = mapa['nombre_archivo'].map(_resolver)
    faltantes = mapa.loc[mapa['archivo_real'].isna(), 'nombre_archivo'].tolist()
    if faltantes:
        logger.warning(
            "Subida de Voltara: %d archivo(s) del Excel no están entre los audios subidos "
            "y no se auditan (%s).", len(faltantes), ", ".join(faltantes[:20]),
        )
    mapa = mapa[mapa['archivo_real'].notna()]
    if mapa.empty:
        logger.warning("Ningún archivo del Excel de Voltara coincidió con los audios subidos.")
        return pd.DataFrame()

    mapa['audio_dir'] = mapa['archivo_real'].map(lambda f: os.path.join(carpeta_audios, f))

    # Un mismo ConnID repartido en varios archivos significa que el Excel está mal: dos
    # audios distintos se auditarían como el mismo llamado y el segundo pisaría al
    # primero. Se auditan igual los demás, pero estos NO pasan (ver validar_mapa).
    duplicados = mapa['ConnID'].duplicated(keep=False)
    if duplicados.any():
        detalle = ", ".join(sorted(mapa.loc[duplicados, 'ConnID'].unique()))
        logger.error(
            "Subida de Voltara: %d archivo(s) comparten ConnID y quedan afuera (%s). "
            "Revisar el Excel de mapeo: cada audio tiene que tener su propio ConnID.",
            int(duplicados.sum()), detalle,
        )
        mapa = mapa[~duplicados]
        if mapa.empty:
            return pd.DataFrame()

    # Info del llamado (IVR) + caso (Salesforce) para los ConnID de esta tanda.
    df_sql = get_filtered_data_Voltara(engine=engine, conn_ids=mapa['ConnID'].tolist())
    if df_sql.empty:
        logger.warning("Ningún ConnID de la subida de Voltara se encontró en [Voltara informe IVR].")
        return pd.DataFrame()

    df_sql['ConnID'] = df_sql['ConnID'].astype(str).str.strip()

    # ConnID del Excel que no existe en el informe IVR: sin metadata del llamado no se
    # puede auditar. Antes se descartaba en silencio y la tanda "perdía" audios sin
    # explicación.
    sin_ivr = set(mapa['ConnID']) - set(df_sql['ConnID'])
    if sin_ivr:
        logger.warning(
            "Subida de Voltara: %d ConnID del Excel no están en [Voltara informe IVR] y no se "
            "auditan (%s).", len(sin_ivr), ", ".join(sorted(sin_ivr)),
        )

    # El audio manda: inner join contra los ConnID que sí tienen llamado en IVR.
    df = pd.merge(mapa[['ConnID', 'audio_dir']], df_sql, on='ConnID', how='inner')
    logger.info("Subida de Voltara: %d audio(s) listos para auditar.", len(df))
    return df.reset_index(drop=True)
