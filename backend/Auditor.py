# --- START OF FILE Auditor.py ---
import os
import tempfile
import pandas as pd
import logging # Added for logging
import ast
import time  # <--- AGREGAR ESTO
import json
import re
import io
import uuid
from AuditorIA.downloads.CYT_comunicaciones import CYT_comunicaciones
from AuditorIA.downloads.Mitrol import Mitrol
from AuditorIA.downloads.Verint import Verint
from AuditorIA.downloads.CXOne import CXOne
from AuditorIA.downloads.Hermes import Hermes
from AuditorIA.downloads.AsterVoIP import AsterVoIP
from AuditorIA.downloads.Genesys import Genesys

from collections import Counter, defaultdict
from datetime import datetime, time as dt_time, timedelta
from typing import List, Optional, Union, Tuple, Dict, Any
from sqlalchemy import create_engine, text, exc as sqlalchemy_exc, Engine # Added specific exception
from google import genai

from AuditorIA.downloads.Hermes import Hermes
from app.config import settings
from AuditorIA.gemini import procesar_respuesta_combinada, apply_auditoria_threads, calidad_batch
from AuditorIA import batch_cola
from AuditorIA.sql_a_Claude import auditoria_a_SQL, verificar_calidad_en_SQL, calcular_id_aplicativo
from AuditorIA.execution_log import (
    iniciar_ejecucion, finalizar_ejecucion, obtener_contexto,
    corrida_terminada, marcar_envio_corrida,
    estimar_costo_usd, formatear_duracion, armar_detalle_html,
    continuar_o_iniciar_grupo, finalizar_grupo, calcular_desglose_muestreo,
    ALIAS_OPERADOR, ALIAS_TIPIFICACION,
)
from AuditorIA.Trancribir import (
    guardar_transcripciones_sql, ids_con_transcripcion, guardar_chats_como_transcripcion,
)
from AuditorIA import audio_calidad, audio_store, incidencias
from AuditorIA.downloads.CXOne import CXOne
from AuditorIA.modelos_ia import MODELO_IA_DEFAULT
from app.managers import plantillas_manager_instance

from AuditorIA.Descarga import descarga_aleatoria
from AuditorIA.avisos import AvisoUsuarioError
from AuditorIA.Cardnet import generar_df_llamados_csv, subida_interacciones_csv
from AuditorIA.Voltara import generar_df_llamados_voltara
from app.utils.mail import Calidad1_mail_manager, Envios_mail_manager
from app.utils.google_sheet import dataframe_a_sheet, preparar_export
# --- Setup Logger ---
# Get a logger instance specific to this module
logger = logging.getLogger(__name__)


def _idturno_a_texto(valor) -> Optional[str]:
    """El IdTurno del turnero de Dental, como texto exacto, o None si no se puede saber
    cuál era.

    En modo Batch el valor no viene del SQL original: viaja por la columna varchar de
    calidad.Batch_data y vuelve como string. Los lotes anteriores al fix de
    gemini.py::valor_para_batch_data lo guardaron en formato científico
    ('1.05276e+007'), porque pandas lo había promovido a float64 al haber alguna llamada
    sin turno asociado. Dos consecuencias:

      * `int(x)` crudo tiraba ValueError. Y como la estandarización de columnas corría
        fuera de todo try/except, la excepción abortaba la pasada ENTERA de
        procesar_batch: ningún lote se sellaba, ningún log se cerraba (quedaban EN_CURSO
        para siempre) y ni siquiera corría la red de MAX_INTENTOS_PROCESO.
      * El número quedó truncado a 6 dígitos significativos. Reconstruirlo
        (int(float(...)) = 10527600) daría el turno de OTRO paciente, así que en ese caso
        se devuelve None: es mejor una auditoría sin IdTurno que con uno ajeno.
    """
    if valor is None:
        return None
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass

    texto = str(valor).strip()
    if not texto or texto.lower() in ('none', 'nan', 'nat'):
        return None
    if texto.isdigit():
        return texto

    if 'e' in texto.lower():
        logger.warning(
            f"IdTurno '{texto}' vino en notación científica (lote previo al fix de "
            f"Batch_data): el número está truncado, se guarda NULL en vez de un IdTurno ajeno."
        )
        return None

    try:
        numero = float(texto)
    except ValueError:
        logger.warning(f"IdTurno '{texto}' no es numérico; se guarda NULL.")
        return None
    if not numero.is_integer():
        logger.warning(f"IdTurno '{texto}' no es entero; se guarda NULL.")
        return None
    return str(int(numero))


def _texto_error_gemini(error: Any) -> str:
    """Mensaje legible del error que Gemini devuelve POR REQUEST dentro de un lote.

    Un lote puede terminar en JOB_STATE_SUCCEEDED y aun así traer TODAS sus
    respuestas vacías: `inlined_responses[i].response` viene en None y el motivo
    real (cuota agotada, argumento inválido, modelo no habilitado) viaja en
    `.error`. Ese campo se ignoraba, y el síntoma que quedaba en el journal era
    un `'NoneType' object has no attribute 'candidates'` por cada interacción,
    que no dice nada de la causa (17/08/2026: 30 llamados de ALARMIX perdidos así)."""
    if error is None:
        return "Gemini no devolvió ni respuesta ni error para este request."
    if isinstance(error, dict):
        codigo, mensaje = error.get("code"), error.get("message") or error.get("status")
    else:
        codigo = getattr(error, "code", None)
        mensaje = getattr(error, "message", None) or getattr(error, "status", None)
    if mensaje:
        return f"[{codigo}] {mensaje}"[:300] if codigo is not None else str(mensaje)[:300]
    return str(error)[:300]


def _resumen_motivos(motivos: Optional[Counter]) -> str:
    """Los 3 motivos más frecuentes del lote, para pegarlos al error_message.

    Se resumen y no se listan uno por uno porque los 30 requests de un lote
    fallan casi siempre por lo mismo; lo que hace falta para arreglarlo es el
    mensaje, no repetirlo treinta veces."""
    if not motivos:
        return ""
    partes = [f"{motivo} (x{n})" for motivo, n in motivos.most_common(3)]
    if len(motivos) > 3:
        partes.append(f"… y {len(motivos) - 3} motivo(s) más")
    return " Motivo: " + " | ".join(partes)


class AuditorIA():
    def __init__(self,engine:Engine):
        """Initializes the AuditorIA class."""
        logger.info("Initializing AuditorIA instance...")
        self.engine = engine
        self.gemini = genai.Client(api_key=settings.GEMINI_AUDITORIA_API_KEY)
        self._tempfolder = tempfile.mkdtemp(prefix="auditoria_")
        
        logger.info("AuditorIA instance initialized.")

    @property
    def tempfolder(self) -> str:
        """Directorio temporal para descargas. Si el sistema operativo (p. ej. systemd-tmpfiles)
        lo eliminó mientras el servicio gunicorn seguía corriendo, se recrea automáticamente."""
        if not hasattr(self, '_tempfolder') or not self._tempfolder or not os.path.exists(self._tempfolder):
            self._tempfolder = tempfile.mkdtemp(prefix="auditoria_")
        return self._tempfolder

    @tempfolder.setter
    def tempfolder(self, value: str):
        self._tempfolder = value
        if value:
            os.makedirs(value, exist_ok=True)

    def __descarga_audios(self, cantidad:int=1, idInteraccion:Union[list[str], None]=None,
        Segmento:Union[list[int], None]=None, Fecha_desde:Union[datetime, None]=None, 
        Fecha_hasta:Union[datetime, None]=None, loginid:Union[list[str], None]=None, 
        empresa:Union[list[str], None]=None, campana:Union[list[str], None]=None, 
        duracion_min:Union[int, None]=None, duracion_max:Union[int, None]=None,
        tipificacion:Union[list[str], None]=None, sentido:Union[list[str], None]=None,
        comentario:Union[list[str], None]=None,
        Chunks:int=10, custom_audio_paths: Optional[List[str]] = None,
        ucids: Optional[str] = None, saved_files: Optional[str] = None,
        por_operador:bool=False, por_tipificacion:bool=False, omitir_limite:bool=False, reauditar:bool=False):
        """Descarga de los archivos de audio segun los filtros puestos de forma aleatoria en caso de multiples audios."""
        
        empresa = empresa or []
        
        if 'CSV' in empresa and custom_audio_paths and ucids and saved_files:
            df = generar_df_llamados_csv(
                carpeta_audios=os.path.dirname(custom_audio_paths[0]),
                ucid=ucids,
                savedFiles=saved_files
            )
            df = subida_interacciones_csv(df,engine=self.engine)
        elif 'Voltara' in empresa:
            # Voltara ya no baja audios de Verint: el usuario los sube junto con un Excel
            # (nombre de archivo -> ConnID). `custom_audio_paths` son los audios subidos y
            # `ucids` es la ruta del Excel de mapeo (ver app/routers/auditoria.py). El cruce
            # con el llamado (IVR) y el caso (Salesforce) vive en AuditorIA/Voltara.py.
            if custom_audio_paths and ucids:
                df = generar_df_llamados_voltara(
                    carpeta_audios=os.path.dirname(custom_audio_paths[0]),
                    ruta_excel=ucids,
                    engine=self.engine,
                )
            else:
                logger.warning("Auditoría de Voltara sin audios o sin Excel de mapeo; no hay nada para auditar.")
                df = pd.DataFrame()
        elif 'ALARMIX' in empresa:
            with CXOne(settings.CXONE_USER, settings.CXONE_PASS, settings.CXONE_TOPC, carpeta_descarga=self.tempfolder) as CXOneD:
                df = descarga_aleatoria(
                    engine=self.engine, cantidad=cantidad, api=CXOneD, num_chunks=Chunks,
                    idInteraccion=idInteraccion, Segmento=Segmento, Fecha_desde=Fecha_desde,
                    Fecha_hasta=Fecha_hasta, loginid=loginid, empresa=empresa, campana=campana,
                    duracion_min=duracion_min, duracion_max=duracion_max, tipificacion=tipificacion,
                    sentido=sentido, reauditar=reauditar,
                    por_operador=por_operador,
                    por_tipificacion=por_tipificacion, omitir_limite=omitir_limite
                )
        elif 'HIDRA Comercial' in empresa:
            with Hermes(
                    login=settings.HERMES_USER, 
                    password=settings.HERMES_PASS, 
                    station=settings.HERMES_STATION, 
                    default_folder=self.tempfolder
                ) as Hermes_instance:
                df = descarga_aleatoria(
                    engine=self.engine, cantidad=cantidad, api=Hermes_instance, num_chunks=Chunks,
                    idInteraccion=idInteraccion, Fecha_desde=Fecha_desde, Fecha_hasta=Fecha_hasta,
                    loginid=loginid, empresa=empresa, campana=campana, duracion_min=duracion_min,
                    duracion_max=duracion_max, por_operador=por_operador, por_tipificacion=por_tipificacion, omitir_limite=omitir_limite, reauditar=reauditar
                )
        elif 'Vitalis Salud' in empresa:
            df = descarga_aleatoria(
                engine=self.engine, cantidad=cantidad, api=None, num_chunks=Chunks,
                idInteraccion=idInteraccion, Fecha_desde=Fecha_desde, Fecha_hasta=Fecha_hasta,
                loginid=loginid, empresa=empresa, campana=campana, duracion_min=duracion_min,
                duracion_max=duracion_max, reauditar=reauditar,
                por_operador=por_operador,
                por_tipificacion=por_tipificacion, omitir_limite=omitir_limite
            )
        elif 'Farmalux' in empresa:
            with AsterVoIP(settings.ASTERVOIP_USER) as Aster:
                df = descarga_aleatoria(
                    engine=self.engine, cantidad=cantidad, api=Aster, num_chunks=Chunks,
                    idInteraccion=idInteraccion, Fecha_desde=Fecha_desde, Fecha_hasta=Fecha_hasta,
                    loginid=loginid, empresa=empresa, campana=campana, duracion_min=duracion_min,
                    duracion_max=duracion_max, comentario=comentario, reauditar=reauditar,
                    por_operador=por_operador,
                    por_tipificacion=por_tipificacion, omitir_limite=omitir_limite
                )
        elif 'Track On' in empresa or 'Trackon-Vantix' in empresa:
            with CYT_comunicaciones(settings.CYT_USER, settings.CYT_PASS) as CYT_api:
                df = descarga_aleatoria(
                    engine=self.engine, cantidad=cantidad, api=CYT_api, num_chunks=Chunks,
                    idInteraccion=idInteraccion, Segmento=Segmento, Fecha_desde=Fecha_desde,
                    Fecha_hasta=Fecha_hasta, loginid=loginid, empresa=empresa, campana=campana,
                    duracion_min=duracion_min, duracion_max=duracion_max, tipificacion=tipificacion,
                    sentido=sentido, comentario=comentario,
                    reauditar=reauditar,
                    por_operador=por_operador,
                    por_tipificacion=por_tipificacion, omitir_limite=omitir_limite
                )
        elif 'Benefix' in empresa:
            with Genesys(settings.GENESYS_USER, settings.GENESYS_PASS, settings.GENESYS_REGION) as genesys:
                df = descarga_aleatoria(
                    engine=self.engine, cantidad=cantidad, api=genesys, num_chunks=Chunks,
                    idInteraccion=idInteraccion, Fecha_desde=Fecha_desde, Fecha_hasta=Fecha_hasta,
                    loginid=loginid, empresa=empresa, campana=campana, duracion_min=duracion_min,
                    duracion_max=duracion_max, tipificacion=tipificacion, sentido=sentido,
                    comentario=comentario, reauditar=reauditar,
                    por_operador=por_operador,
                    por_tipificacion=por_tipificacion, omitir_limite=omitir_limite
                )
        else:
            mitrol = Mitrol(settings.MITROL_USER,settings.MITROL_PASS,self.tempfolder)
            df = descarga_aleatoria(
                engine=self.engine, cantidad=cantidad, api=mitrol, num_chunks=Chunks,
                idInteraccion=idInteraccion, Segmento=Segmento, Fecha_desde=Fecha_desde,
                Fecha_hasta=Fecha_hasta, loginid=loginid, empresa=empresa, campana=campana,
                duracion_min=duracion_min, duracion_max=duracion_max, tipificacion=tipificacion,
                sentido=sentido, comentario=comentario,
                por_operador=por_operador, por_tipificacion=por_tipificacion, omitir_limite=omitir_limite, reauditar=reauditar
            )
            del mitrol

        if isinstance(empresa, list) and len(empresa) > 0:
            df['Campaña'] = empresa[0]
        else:
            df['Campaña'] = empresa

        return df

    def __ejecutar_consulta_primera_col(self, query_str: str, params: Dict[str, Any]) -> List[str]:
        query = text(query_str)
        with self.engine.connect() as connection:
            return list(connection.execute(query, params).scalars().all())

    def __normalizar_empresa(self, empresa: int) -> list[str]:
        query_str = """
            SELECT
                CASE
                    WHEN em.nombre_mitrol IS NULL THEN e.Nombre
                    ELSE em.nombre_mitrol
                END AS Nombre
            FROM
                [Acme].[calidad].[Empresas] e
            LEFT JOIN
                calidad.Empresa_nombreMitrol em ON em.EmpresaID = e.EmpresaID
            WHERE e.EmpresaID = :empresa;
        """
        return self.__ejecutar_consulta_primera_col(query_str, {'empresa': empresa})

    def __normalizar_campana_skill(self, campana: int) -> list[str]:
        query_str = """
            SELECT 
                [Nombre]
            FROM [Acme].[calidad].[Skills]
            WHERE IsActive = 1
            AND campanaId = :campana;
        """
        return self.__ejecutar_consulta_primera_col(query_str, {'campana': campana})

    def _preparar_datos_auditoria(self, cantidad, duracion_min, duracion_max, idInteraccion, 
                                  Segmento, Fecha_desde, Fecha_hasta, loginid, empresa, campana, 
                                  tipificacion, sentido, custom_audio_paths, ucids, saved_files, 
                                  por_operador, por_tipificacion, omitir_limite, reauditar, plantilla_id,
                                  comentario=None) -> Tuple[pd.DataFrame, Any, Any]:
        """Método unificado para descargar audios y verificar calidad antes de procesar."""
        logger.info("Iniciando _preparar_datos_auditoria...")
        
        # --- Step 0: Normalizacion ---
        empresa_id = empresa
        campana_id = campana
        empresa_norm = self.__normalizar_empresa(empresa)
        campana_norm = self.__normalizar_campana_skill(campana)
        
        # --- Step 1: Descarga y Preprocesamiento ---
        logger.info("Llamando descarga de audios...")
        df = self.__descarga_audios(
            cantidad=cantidad, idInteraccion=idInteraccion, Segmento=Segmento,
            Fecha_desde=Fecha_desde, Fecha_hasta=Fecha_hasta, loginid=loginid,
            empresa=empresa_norm, campana=campana_norm, duracion_min=duracion_min,
            duracion_max=duracion_max, tipificacion=tipificacion, sentido=sentido,
            comentario=comentario,
            Chunks=min(cantidad, 10), custom_audio_paths=custom_audio_paths,
            ucids=ucids, saved_files=saved_files, por_operador=por_operador,
            por_tipificacion=por_tipificacion, omitir_limite=omitir_limite, reauditar=reauditar
        )
        
        df['empresa_id'] = empresa_id
        df['campana_id'] = campana_id

        if df.empty:
            logger.warning("La descarga de audios devolvió un DataFrame vacío.")
            return df, empresa_id, campana_id

        # --- Step 2: Verify Quality in SQL ---
        if not reauditar:
            logger.info("Llamando verificar_calidad_en_SQL...")
            df = verificar_calidad_en_SQL(df=df, engine=self.engine, plantilla_id=plantilla_id)
            if df.empty:
                logger.warning("Todas las interacciones obtenidas ya fueron auditadas previamente (df quedó vacío).")
                
        return df, empresa_id, campana_id

    def _guardar_no_auditables(self, df_no_auditable: pd.DataFrame, campana_id, empresa_id,
                               plantilla_id: int, user_id: int, modo: str = "sync") -> int:
        """Guarda los llamados que no se pudieron auditar, marcados y sin detalles.

        Sin esto un audio mudo desaparecería sin dejar rastro: el usuario pidió 20
        auditorías, recibe 19 y no hay dónde ver qué pasó con la que falta. Guardada
        con su incidencia, el llamado aparece en la bandeja explicando el motivo, y como
        no tiene detalles no puntúa ni ensucia el promedio del operador.

        Best-effort: si el guardado falla, la corrida sigue.
        """
        if df_no_auditable is None or df_no_auditable.empty:
            return 0
        try:
            df = self._estandarizar_columnas_sql(df_no_auditable.copy())
            self._finalizar_y_guardar_sql(
                df=df, campana_id=campana_id, empresa_id=empresa_id,
                plantilla_id=plantilla_id, user_id=user_id, modo=modo,
            )
            logger.info("%d llamado(s) guardados como no auditables.", len(df))
            return len(df)
        except Exception as e:
            logger.warning("No se pudieron guardar los llamados no auditables: %s", e)
            return 0

    def _estandarizar_columnas_sql(self, df: pd.DataFrame, solo_id: bool = False) -> pd.DataFrame:
        """
        Unifica la lógica de mapeo de columnas haciéndola case-insensitive, 
        preparando el DataFrame antes de subirlo a SQL.
        """
        if df.empty:
            return df

        # Creamos un mapa de las columnas en minúsculas hacia su nombre original
        # Ejemplo: {'loginid': 'LoginId', 'fecha': 'Fecha', 'idturno': 'IdTurno'}
        cols_lower_map = {col.lower(): col for col in df.columns}

        # --- 1. Mapeo de ID Aplicativo (lógica COMPARTIDA con la transcripción) ---
        df['id_aplicativo'] = calcular_id_aplicativo(df)

        if solo_id:
            return df
        
        # --- 2. Mapeo de Operador/Usuario (Case-Insensitive) ---
        df['operador_usuario'] = None
        # Misma lista que usa el desglose del muestreo (ver AuditorIA/execution_log.py):
        # en batch el desglose se calcula sobre el df todavía sin estandarizar.
        user_aliases = ALIAS_OPERADOR
        for alias in user_aliases:
            if alias in cols_lower_map:
                real_col = cols_lower_map[alias]
                df['operador_usuario'] = df[real_col]
                break
        
        # --- 3. Mapeo de Fecha ---
        df['fecha_interaccion'] = None
        fecha_aliases = ['fecha', 'date', 'inicio', 'calllocaltime', 'fecha_inicio','fecha y hora del llamado']
        for alias in fecha_aliases:
            if alias in cols_lower_map:
                real_col = cols_lower_map[alias]
                df['fecha_interaccion'] = pd.to_datetime(df[real_col], errors='coerce')
                break
        
        # --- 4. Mapeo de Sentido ---
        df['sentido_interaccion'] = None
        sentido_aliases = ['sentido', 'directiontype', 'es_entrante','sentido del llamado']
        for alias in sentido_aliases:
            if alias in cols_lower_map:
                real_col = cols_lower_map[alias]
                df['sentido_interaccion'] = df[real_col]
                break

        # --- 4b. Mapeo de Tipificación (tipificación del llamado) ---
        # La mayoría de las descargas exponen la columna ya como 'Tipificación'
        # (Mitrol/Hidra/Aurora Salud/Dental y los alias de ALARMIX/Vitalis). Vantix usa nombres propios.
        # Vantix (Orion) expone DOS columnas: la Subcategoría es la tipificación
        # real del llamado (p.ej. "No coordina turno") y la Categoría apenas su
        # bucket genérico (Turnos, Multiskill, Retenciones...). La subcategoría
        # tiene que ganar; con el orden inverso se guardaba la categoría como
        # tipificación. La Categoría queda sólo como fallback (de hecho nunca
        # aplica: el JOIN deriva cat de sub, así que sub NULL ⇒ cat NULL).
        # El orden vive en ALIAS_TIPIFICACION, compartido con el desglose del muestreo.
        df['tipificacion_interaccion'] = None
        tipificacion_aliases = ALIAS_TIPIFICACION
        for alias in tipificacion_aliases:
            if alias in cols_lower_map:
                real_col = cols_lower_map[alias]
                df['tipificacion_interaccion'] = df[real_col]
                break

        # --- 4c. Mapeo de Duración del llamado (en segundos) ---
        # Cada fuente trae la duración distinto: int (segundos, p.ej. Vantix
        # TiempoHablado o las vistas Mitrol), TIME, timedelta o string "HH:MM:SS".
        # La normalizamos a segundos (int) para que la bandeja / auditorías
        # muestren un valor homogéneo y comparable entre clientes.
        def _a_segundos(valor):
            if valor is None:
                return None
            try:
                if pd.isna(valor):
                    return None
            except (TypeError, ValueError):
                pass
            if isinstance(valor, bool):
                return None
            if isinstance(valor, (int, float)):
                return int(round(valor))
            if isinstance(valor, dt_time):
                return valor.hour * 3600 + valor.minute * 60 + valor.second
            if isinstance(valor, (timedelta, pd.Timedelta)):
                return int(valor.total_seconds())
            s = str(valor).strip()
            if not s:
                return None
            if ":" in s:  # "HH:MM:SS" / "MM:SS"
                try:
                    partes = [int(float(p)) for p in s.split(":")]
                except ValueError:
                    return None
                seg = 0
                for p in partes:
                    seg = seg * 60 + p
                return seg
            try:
                return int(round(float(s)))
            except ValueError:
                return None

        df['duracion_segundos'] = None
        for alias in ('duracion', 'duración'):
            if alias in cols_lower_map:
                df['duracion_segundos'] = df[cols_lower_map[alias]].map(_a_segundos)
                break

        # --- 4d. Mapeo de Comentario de la interacción ---
        # Campo de comentario/observación que carga el agente, con distinto nombre
        # por cliente. Se normaliza a una sola columna para que quede visible en la
        # auditoría. El primer alias presente en el df gana (cada df trae a lo sumo uno):
        #   Vantix            -> 'Comentario del Agente' (COMENT de DatosPorLlamada)
        #   Mitrol genérico  -> 'CRM'
        #   Hidra             -> 'Observacion'
        #   Aurora Salud/Dental -> 'Observaciones'
        #   Farmalux        -> 'Comentarios del caso'
        df['comentario_interaccion'] = None
        comentario_aliases = [
            'comentario del agente',
            'crm',
            'observacion',
            'observaciones',
            'comentarios del caso',
        ]
        for alias in comentario_aliases:
            if alias in cols_lower_map:
                df['comentario_interaccion'] = df[cols_lower_map[alias]]
                break

        # --- 5. Mapeo de Extras ---
        temp_df = pd.DataFrame()
        
        if 'extras' in cols_lower_map:
            temp_df['Extras'] = df[cols_lower_map['extras']]
            
        if 'idturno' in cols_lower_map:
            temp_df['IdTurno'] = df[cols_lower_map['idturno']].map(_idturno_a_texto)
            
        if 'motivo del fin del contacto' in cols_lower_map:
            temp_df['Motivo del fin del contacto'] = df[cols_lower_map['motivo del fin del contacto']]

        # Campos de gestión que van a Extras si existen en el df (visibles en
        # "Auditorías Realizadas"). Cubre SalesForce de Farmalux y el contexto de
        # Voltara (llamado en IVR + resumen de casos del agente en Salesforce; ver
        # SQL_query.py::get_filtered_data_Voltara -no es "el caso" de este llamado,
        # es contexto de lo que el agente gestionó esos días-).
        sf_fields = [
            'caso abierto',
            'caso cerrado',
            'comentarios del caso',
            'asunto',
            'nombre de la cuenta',
            'telefono contacto',
            'numero del caso',
            'voz del cliente',
            'motivo',
            'submotivo',
            # Voltara: contexto del llamado (IVR)
            'cola',
            'skill',
            'bpo',
            'documento',
            'suministro',
            # Voltara: resumen de casos del agente en la ventana (no el caso puntual)
            'cantidad de casos del agente en el periodo',
            'casos del agente en el periodo',
            'motivos de casos del agente en el periodo',
            # Vantix: quién cortó el llamado, derivado del EventCause del tramo del
            # agente en Orion (ver SQL_query.py::get_filtered_data_Vantix).
            'motivo de finalización',
            # Benefix: lo que el cliente marcó en el IVR de Genesys (a veces el número
            # de tarjeta). Va a Extras pero NO al prompt (gemini.COLUMNAS_OMITIDAS_EN_DETALLES).
            'externaltag',
        ]
        for sf in sf_fields:
            if sf in cols_lower_map:
                real_col = cols_lower_map[sf]
                temp_df[real_col] = df[real_col]

        def crear_json_desde_fila(fila):
            datos_validos = {clave: valor for clave, valor in fila.items() if pd.notna(valor) and valor != ''}
            return json.dumps(datos_validos, ensure_ascii=False) if datos_validos else None

        if not temp_df.empty:
            df['Extras'] = temp_df.apply(crear_json_desde_fila, axis=1)
        elif 'Extras' not in df.columns:
            df['Extras'] = None

        return df

    def _finalizar_y_guardar_sql(self, df: pd.DataFrame, campana_id: int, empresa_id: int, plantilla_id: int, user_id: int, modo: str = "sync", modelo: Optional[str] = None) -> Dict[str, Any]:
        """Asigna las columnas finales obligatorias, filtra los fallidos y guarda en SQL.

        `modo` ("sync"/"batch") se propaga al libro de consumo de IA (IA_Uso); batch
        factura -50%. `modelo` es el modelo de Gemini REAL con el que se generaron estas
        auditorías (viene de apply_auditoria_threads en sync, o de calidad.Batch_data en
        batch); si no se pasa, se asume el default global.

        Devuelve un resumen (filas guardadas/fallidas y tokens consumidos) para el
        log de ejecución (calidad.AuditExecutionLog)."""
        resumen: Dict[str, Any] = {
            "filas_auditadas": 0, "filas_error": 0,
            "tokens": {"input_tokens": 0, "output_tokens": 0, "thoughts_tokens": 0,
                       "cached_tokens": 0},
        }
        if df.empty:
            logger.warning("El DataFrame está vacío, no se guardará nada en SQL.")
            return resumen

        # 1. Asignar variables de contexto
        df = df.assign(
            campana_id=campana_id,
            empresa_id=empresa_id,
            plantilla_id=plantilla_id,
            auditor_id=user_id
        )

        # Versión de la plantilla usada (Golden Set — Fase 2). Se normaliza acá, en
        # un solo lugar, porque llega distinto según el camino: entera desde
        # apply_auditoria_threads (sync) y como texto desde calidad.Batch_data
        # (batch, donde todo viaja serializado). Si no vino, la columna no se toca
        # y la auditoría se guarda sin versión (histórico / migración sin aplicar).
        version_id = self._parsear_plantilla_version_id(df)
        if version_id is not None:
            df = df.assign(plantilla_version_id=version_id)

        # 2. Filtrar auditorías fallidas de forma segura
        if 'status_auditoria' in df.columns:
            df_exitoso = df[df['status_auditoria'] != 'FALLIDO']
        else:
            df_exitoso = df

        resumen["filas_auditadas"] = len(df_exitoso)
        resumen["filas_error"] = len(df) - len(df_exitoso)
        # cached_tokens: cuánto del input se cobró a precio de caché (ver
        # AuditorIA/cache_plantillas.py). Va al log y de ahí al costeo del mail.
        for col in ("input_tokens", "output_tokens", "thoughts_tokens", "cached_tokens"):
            if col in df_exitoso.columns:
                resumen["tokens"][col] = int(pd.to_numeric(df_exitoso[col], errors='coerce').fillna(0).sum())

        # 3. Guardar en SQL
        if not df_exitoso.empty:
            logger.info(f"Guardando {len(df_exitoso)} resultados exitosos en la base de datos.")
            auditoria_a_SQL(engine=self.engine, df=df_exitoso, modo=modo, modelo=modelo or MODELO_IA_DEFAULT)
        else:
            logger.warning("No hay filas exitosas para guardar en SQL (todas fallaron o df original vacío).")

        return resumen

    def _obtener_configuracion_avisos(self, plantilla_id: int) -> List[Dict[str, Any]]:
        """Busca en la BBDD qué atributos tienen DarAviso = 1 para la plantilla actual."""
        query = text("""
            SELECT NombreAtributo, FrasesAviso
            FROM calidad.Atributos
            WHERE PlantillaID = :plantilla_id AND DarAviso = 1 AND IsActive = 1
        """)
        try:
            with self.engine.connect() as conn:
                result = conn.execute(query, {"plantilla_id": plantilla_id}).fetchall()
                # Devolvemos un diccionario con el nombre del atributo y sus frases
                return [{"nombre": r[0], "frases": r[1]} for r in result if r[1]]
        except Exception as e:
            logger.error(f"Error al obtener configuración de avisos: {e}")
            return []

    def _enviar_alerta_calidad(self, row: pd.Series, atributo_nombre: str, valor_atributo: str, frases_encontradas: List[str]):
        """Construye y envía el correo con el audio adjunto al supervisor y a Calidad."""
        try:
            audio_path = row.get('audio_dir')
            operador = row.get('operador_usuario') or row.get('loginid') or "Desconocido"
            Campana = row.get('Campaña') or "Desconocido"
            id_interaccion = row.get('id_aplicativo') or row.get('idInteraccion') or "Desconocido"
            
            # Buscar el feedback general de la IA
            feedback = row.get('Feedback') or row.get('feedback') or 'No hay feedback registrado.'

            # 1. Intentar obtener el correo del supervisor
            # Ajusta esta query según tu estructura real en nomina_extendida para encontrar al supervisor
            #
            # Con usuarios reciclados (2026-08-25) `usuarios.usuario` puede ser de
            # varias personas, y el criterio viejo —cualquiera con asignación abierta—
            # elegía por azar: el aviso se le terminaba mandando al supervisor de otra
            # campaña. `calidad.fn_ResolverOperadorAuditoria` elige a la persona que
            # estaba en esa empresa el día del llamado y devuelve su equipo de ese
            # momento. Si la migración todavía no está aplicada, se cae al criterio
            # viejo antes que quedarse sin avisar.
            email_supervisor = None
            fecha_llamado = row.get('fecha_interaccion')
            if pd.isna(fecha_llamado):
                fecha_llamado = None
            empresa_llamado = row.get('empresa_id')
            if pd.isna(empresa_llamado):
                empresa_llamado = None

            query_sup = text("""
                SELECT TOP 1 ne.[EMAIL INTERNO]
                FROM calidad.fn_ResolverOperadorAuditoria(:operador, :empresa_id, :fecha) r
                JOIN equipos e ON e.id = r.EquipoID
                JOIN nomina_extendida ne ON ne.DOCUMENTO = e.id_nomina
            """)
            query_sup_legacy = text("""
                select ne.[EMAIL INTERNO] from usuarios u
                join operadores o on o.legajo_id = u.nomina_id and o.estado = 1 and o.fecha_hasta is null
                join equipos e on e.id = o.equipo_id
                join nomina_extendida ne on ne.DOCUMENTO = e.id_nomina
                where u.usuario = :operador
            """)
            with self.engine.connect() as conn:
                try:
                    res = conn.execute(query_sup, {
                        "operador": str(operador),
                        "empresa_id": empresa_llamado,
                        "fecha": fecha_llamado,
                    }).fetchone()
                except Exception:
                    logger.warning(
                        "fn_ResolverOperadorAuditoria no disponible (¿migración 2026-08-25 sin aplicar?): "
                        "el supervisor del aviso se resuelve con el criterio viejo."
                    )
                    # La transacción implícita queda invalidada tras el error: sin el
                    # rollback, el reintento revienta con PendingRollbackError.
                    conn.rollback()
                    res = conn.execute(query_sup_legacy, {"operador": str(operador)}).fetchone()
                if res and res[0]:
                    email_supervisor = res[0]

            # 2. Configurar destinatarios
            destinatarios = [settings.ADMIN_EMAIL,'calidad@acme.example']  # Área de Calidad por defecto
            if email_supervisor:
                destinatarios.append(email_supervisor)

            # 3. Construir Cuerpo del correo
            asunto = f"🚨 Alerta de Calidad Crítica - Operador {operador} - Campaña: {Campana}"
            cuerpo_mail = f"""
            <h2 style="color: #D32F2F;">Alerta de Calidad Detectada</h2>
            <p>Se ha detectado una coincidencia crítica en una auditoría reciente que requiere atención inmediata.</p>
            <ul>
                <li><strong>Operador:</strong> {operador}</li>
                <li><strong>ID Interacción:</strong> {id_interaccion}</li>
                <li><strong>Atributo Disparador:</strong> {atributo_nombre}</li>
                <li><strong>Palabra/Frase clave detectada:</strong> {', '.join(frases_encontradas)}</li>
            </ul>
            """
            if feedback:
                cuerpo_mail += f"""
                <h3>Análisis y Feedback de la IA:</h3>
                <div style="background-color: #f5f5f5; padding: 15px; border-left: 4px solid #D32F2F;">
                    <p>{feedback}</p>
                </div>"""
            cuerpo_mail += f"""
            <p>Se adjunta el audio de la llamada para su revisión.</p>
            """

            # 4. Adjuntar el audio si existe físicamente
            archivos_adjuntos = []
            if audio_path and os.path.exists(str(audio_path)):
                if str(audio_path).endswith('.json'):
                    # Si es un chat, adjuntamos el archivo JSON directamente
                    logger.info(f"Adjuntando archivo de chat: {audio_path}")
                    archivos_adjuntos.append(str(audio_path))
                else:
                    # Lógica para audios
                    directorio, nombre_archivo = os.path.split(str(audio_path))
                    nombre_limpio = nombre_archivo.replace('ñ', 'n').replace('Ñ', 'N')
                    nombre_limpio = re.sub(r'[^a-zA-Z0-9\.\-\_\ ]', '', nombre_limpio)
                    
                    ruta_comprimida_previa = os.path.join(directorio, nombre_limpio + ".clean.ogg")
                    
                    if os.path.exists(ruta_comprimida_previa):
                        logger.info(f"Reutilizando archivo de audio comprimido existente: {ruta_comprimida_previa}")
                        archivos_adjuntos.append(ruta_comprimida_previa)
                    else:
                        logger.warning(f"No se encontró el audio comprimido previo, se adjuntará el original: {audio_path}")
                        archivos_adjuntos.append(str(audio_path))

            # 5. Enviar el correo usando tu clase existente
            Calidad1_mail_manager.enviar_correo_base(
                destinatarios=destinatarios,
                asunto=asunto,
                mensaje_html=cuerpo_mail,
                archivos_adjuntos=archivos_adjuntos
            )
            logger.info(f"Alerta de calidad enviada por el atributo '{atributo_nombre}' en la interacción {id_interaccion}.")

        except Exception as e:
            logger.error(f"Error al intentar enviar la alerta de calidad: {e}", exc_info=True)

    def _evaluar_y_enviar_avisos(self, df: pd.DataFrame, plantilla_id: int):
        """Evalúa las respuestas de la IA contra las palabras configuradas y lanza la alerta."""
        if df.empty:
            return

        avisos_config = self._obtener_configuracion_avisos(plantilla_id)
        if not avisos_config:
            return

        for index, row in df.iterrows():
            if row.get('status_auditoria') == 'FALLIDO':
                continue

            for config in avisos_config:
                atributo_nombre = 'Detalle_' + config['nombre']
                frases_raw = config['frases']
                
                # Preparamos las frases en una lista limpia
                frases_lista = [f.strip().lower() for f in str(frases_raw).split(',') if f.strip()]
                
                if atributo_nombre in row and pd.notna(row[atributo_nombre]):
                    valor_atributo = str(row[atributo_nombre]).lower()
                    
                    # Chequear si ALGUNA frase prohibida está en la respuesta generada
                    frases_encontradas = [frase for frase in frases_lista if frase in valor_atributo]
                    
                    if frases_encontradas:
                        # Si hay coincidencias, despachamos el correo
                        self._enviar_alerta_calidad(row, config['nombre'], valor_atributo, frases_encontradas)

    def run(self, user_id:int, plantilla_id:int, cantidad:int=1, duracion_min:Union[int, None]=None,
            duracion_max:Union[int, None]=None, idInteraccion:Union[list[str], None]=None,
            Segmento:Union[list[int], None]=None, Fecha_desde:Union[datetime, None]=None,
            Fecha_hasta:Union[datetime, None]=None, loginid:Union[list[str], None]=None,
            empresa:Union[int, None]=None, campana:Union[int, None]=None, tipificacion:Union[list[str], None]=None,
            sentido:Union[list[str], None]=None, comentario:Union[list[str], None]=None, custom_audio_paths: Optional[List[str]] = None,
            ucids: Optional[str] = None, saved_files: Optional[str] = None, calidad: bool = True,
            transcribir: bool = False, por_operador:bool=False, por_tipificacion:bool=False, omitir_limite:bool=False, reauditar:bool=False,
            trigger_source: str = "manual", scheduler_id: Optional[int] = None,
            scheduler_name: Optional[str] = None, task_id: Optional[str] = None,
            upload_group_id: Optional[str] = None) -> pd.DataFrame:
        """
        Runs the main auditing process: fetch audio, transcribe, check quality, analyze with Claude (No-batch).
        """
        logger.info("AuditorIA run method started.")
        logger.info(f"Parameters: cantidad={cantidad}, duracion_min={duracion_min}, duracion_max={duracion_max}, "
                    f"idInteraccion={idInteraccion}, Segmento={Segmento}, Fecha_desde={Fecha_desde}, "
                    f"Fecha_hasta={Fecha_hasta}, loginid={loginid}, empresa={empresa}, "
                    f"campana={campana}, tipificacion={tipificacion}")

        # Fila de calidad.AuditExecutionLog para esta corrida (ver AuditorIA/execution_log.py).
        # Es puramente observabilidad: si falla, se loguea y la auditoría sigue igual.
        # `upload_group_id` (subida CSV en tandas de 10, ver auditoria.js::handleCSVUpload):
        # en vez de abrir una fila nueva por tanda, se acumula en una sola.
        campos_log = dict(
            trigger_source=trigger_source, modo="sync",
            scheduler_id=scheduler_id, scheduler_name=scheduler_name, task_id=task_id,
            empresa=empresa, campana=campana, plantilla_id=plantilla_id, user_id=user_id,
            fecha_desde=Fecha_desde, fecha_hasta=Fecha_hasta,
        )
        if upload_group_id:
            log_id = continuar_o_iniciar_grupo(
                self.engine, upload_group_id=upload_group_id, cantidad_solicitada=cantidad, **campos_log
            )
        else:
            log_id = iniciar_ejecucion(
                self.engine, cantidad_solicitada=cantidad,
                por_operador=por_operador, por_tipificacion=por_tipificacion, **campos_log
            )

        def _cerrar_log(status: str, **kwargs) -> None:
            if upload_group_id:
                finalizar_grupo(
                    self.engine, upload_group_id=upload_group_id, status=status,
                    filas_auditadas=kwargs.get("filas_auditadas"), filas_error=kwargs.get("filas_error"),
                    tokens=kwargs.get("tokens"), error_message=kwargs.get("error_message"),
                    modelo=kwargs.get("modelo"), nivel_razonamiento=kwargs.get("nivel_razonamiento"),
                )
            else:
                finalizar_ejecucion(self.engine, log_id=log_id, status=status, **kwargs)

        try:
            # Reutilizamos el nuevo método unificado
            df, empresa_id, campana_id = self._preparar_datos_auditoria(
                cantidad, duracion_min, duracion_max, idInteraccion, Segmento, Fecha_desde,
                Fecha_hasta, loginid, empresa, campana, tipificacion, sentido, custom_audio_paths,
                ucids, saved_files, por_operador, por_tipificacion, omitir_limite, reauditar, plantilla_id,
                comentario=comentario
            )

            if df.empty:
                _cerrar_log("SIN_DATOS", filas_auditadas=0, filas_error=0)
                return pd.DataFrame()

            # --- Step 2.5: Gate de audio ---
            # Los audios sin voz no llegan a la IA: se guardan marcados y no gastan
            # tokens (ver AuditorIA/audio_calidad.py::filtrar_no_auditables).
            df, df_no_auditable = audio_calidad.filtrar_no_auditables(df)
            if not df_no_auditable.empty:
                self._guardar_no_auditables(df_no_auditable, campana_id, empresa_id,
                                            plantilla_id, user_id, modo="sync")

            if df.empty:
                logger.warning("Ningún audio de la corrida tenía voz audible.")
                _cerrar_log("SIN_DATOS", filas_auditadas=0, filas_error=0)
                return pd.DataFrame()

            # --- Step 3: Análisis con LLM (una sola subida del audio por fila) ---
            # La auditoría de calidad SIEMPRE se ejecuta; la transcripción es opcional
            # y, cuando se pide, viaja en el MISMO llamado a Gemini (una sola lectura
            # del audio). El parámetro `calidad` se mantiene por compatibilidad de la
            # firma, pero ya no puede desactivar la auditoría.
            # La transcripción se pide a nivel de corrida; por fila, apply_auditoria_threads
            # solo transcribe los llamados SIN transcripción previa (mismo audio = misma
            # transcripción, no se rehace). No se ata a `reauditar`: una re-auditoría también
            # puede generar la transcripción si el llamado todavía no la tenía.
            do_transcribir = transcribir
            logger.info(f"Procesando auditoría (transcribir={do_transcribir})...")
            df, df_transcripciones, original_map, modelo_usado, nivel_usado = apply_auditoria_threads(
                df=df, plantilla_id=plantilla_id, gemini_api=self.gemini,
                transcribir=do_transcribir, engine=self.engine,
                max_workers=min(len(df), 15), concurrent_api_calls=5
            )

            # Guardar las transcripciones generadas (si las hay) en su tabla propia.
            if do_transcribir and df_transcripciones is not None and not df_transcripciones.empty:
                guardar_transcripciones_sql(df_transcripciones, engine=self.engine, user_id=user_id, modelo=modelo_usado)

            # Conservar el audio comprimido para poder escucharlo/descargarlo después
            # (ver AuditorIA/audio_store.py). Acá el df todavía tiene `audio_dir` (ruta o
            # BytesIO) y las columnas de id; los `.clean.ogg` físicos ya se crearon al
            # subir a Gemini. Best-effort: nunca corta la auditoría.
            try:
                audio_store.persistir_audios_df(self.engine, df)
            except Exception as e:
                logger.warning(f"No se pudieron conservar los audios de la corrida: {e}")

            # Los chats no tienen audio ni transcripción: se guarda la conversación
            # para poder leerla después en "Auditorías Realizadas" (misma vista que
            # la transcripción de un llamado). Best-effort.
            try:
                guardados = guardar_chats_como_transcripcion(self.engine, df, user_id=user_id)
                if guardados:
                    logger.info(f"Se conservaron {guardados} chats para su lectura.")
            except Exception as e:
                logger.warning(f"No se pudieron conservar los chats de la corrida: {e}")

            if 'Info_General_Clinica' in df.columns and 'Cronograma_Clinica' in df.columns:
                df.drop(columns=['Info_General_Clinica', 'Cronograma_Clinica'], inplace=True)

            #*Subir a SQL los resultados
            df = self._estandarizar_columnas_sql(df)
            self._evaluar_y_enviar_avisos(df, plantilla_id)

            df_calidad_return = df.copy(deep=True)

            # Muestreo por operador/tipificación: `cantidad` deja de ser el total y pasa
            # a ser "por cada grupo" (ver AuditorIA/SQL_query.py::construir_query_muestreo,
            # PARTITION BY). Acá, con el df ya estandarizado, se sabe cuántos grupos
            # distintos trajo la descarga y cuántas interacciones le tocaron a cada uno,
            # así el log muestra la cantidad final real (no el valor crudo del form).
            cantidad_solicitada_final = None
            desglose_muestreo = None
            if por_operador or por_tipificacion:
                cantidad_solicitada_final = len(df)
                desglose_muestreo = calcular_desglose_muestreo(
                    df, por_operador=por_operador, por_tipificacion=por_tipificacion
                )

            prompt_nombre_id_map = {f"Detalle_{k}": f"Detalle_{v}" for k, v in original_map.items()}
            df.rename(columns=prompt_nombre_id_map, inplace=True)
            resumen = self._finalizar_y_guardar_sql(
                df=df, campana_id=campana_id, empresa_id=empresa_id, plantilla_id=plantilla_id, user_id=user_id,
                modelo=modelo_usado
            )
            _cerrar_log(
                "EXITO" if resumen["filas_error"] == 0 else "PARCIAL",
                filas_auditadas=resumen["filas_auditadas"], filas_error=resumen["filas_error"],
                tokens=resumen["tokens"],
                cantidad_solicitada_final=cantidad_solicitada_final, desglose_muestreo=desglose_muestreo,
                modelo=modelo_usado, nivel_razonamiento=nivel_usado,
            )

            logger.info(f"AuditorIA run method finished successfully.")
            return df_calidad_return

        except AvisoUsuarioError as e:
            # Condiciones esperables con mensaje para el usuario (límite de muestreo
            # superado, sin acceso a Drive/Sheets, etc.): deben propagarse para que el
            # endpoint las registre como AVISO y el frontend las muestre, en lugar de
            # tragarse en el except genérico y terminar en un Excel vacío.
            _cerrar_log("ERROR", error_message=str(e))
            raise
        except sqlalchemy_exc.SQLAlchemyError as e:
             _cerrar_log("ERROR", error_message=str(e))
             logger.exception(f"Database error during AuditorIA run: {e}")
             return pd.DataFrame()
        except Exception as e:
            _cerrar_log("ERROR", error_message=str(e))
            logger.exception(f"An unexpected error occurred during AuditorIA run: {e}")
            return pd.DataFrame()

    def _actualizar_estado_task(self, task_id: Optional[str], status: str, error: Optional[str] = None, result_data: Optional[str] = None) -> None:
        """Actualiza el estado de una tarea batch en calidad.AuditTasks para que el
        frontend muestre el progreso. No-op si no hay task_id (ej. scheduler)."""
        if not task_id:
            return
        try:
            with self.engine.connect() as conn:
                conn.execute(text("""
                    UPDATE [calidad].[AuditTasks]
                    SET [status] = :status, [error_message] = :error,
                        [result_data] = COALESCE(:result_data, [result_data]), [updated_at] = GETDATE()
                    WHERE [task_id] = :task_id
                """), {"status": status, "error": error, "result_data": result_data, "task_id": task_id})
                conn.commit()
        except Exception as e:
            logger.error(f"No se pudo actualizar el estado de la tarea {task_id} a '{status}': {e}")

    def run_batch(self, user_id:int, plantilla_id:int, cantidad:int=1, duracion_min:Union[int, None]=None,
        duracion_max:Union[int, None]=None, idInteraccion:Union[list[str], None]=None,
        Segmento:Union[list[int], None]=None, Fecha_desde:Union[datetime, None]=None,
        Fecha_hasta:Union[datetime, None]=None, loginid:Union[list[str], None]=None,
        empresa:Union[int, None]=None, campana:Union[int, None]=None, tipificacion:Union[list[str], None]=None,
        sentido:Union[list[str], None]=None, comentario:Union[list[str], None]=None, custom_audio_paths: Optional[List[str]] = None,
        ucids: Optional[str] = None, saved_files: Optional[str] = None, calidad: bool = True,
        transcribir: bool = False, por_operador:bool=False, por_tipificacion:bool=False, omitir_limite:bool=False, reauditar: bool = False,
        gsheet_id: Optional[str] = None, gsheet_name: Optional[str] = None, task_id: Optional[str] = None,
        column_template_id: Optional[int] = None, trigger_source: str = "manual",
        scheduler_id: Optional[int] = None, scheduler_name: Optional[str] = None,
        upload_group_id: Optional[str] = None,
        email_destinatarios: Optional[str] = None) -> Optional[List[str]]:
        """Run a batch processing job for audio data.

        Devuelve los batch_id de Gemini encolados, distinguiendo tres finales
        (la mayoría de los llamadores ignora el retorno; lo usa quien tiene que
        decidir qué hacer con el origen de los audios, ver tasks.py::auditoria_csv_diaria):
          - lista con nombres -> el/los lote(s) quedaron encolados en Gemini,
          - []                -> no había nada para auditar (sin audios o ya auditados),
          - None              -> la corrida falló y no se encoló nada.

        `upload_group_id` (agrupado de tandas CSV, ver Auditor.py::run) se acepta
        acá solo para que /Auditar/ pueda pasar los mismos audit_params sin romper
        con batch=True; el agrupado de log NO está implementado para batch (cada
        job de Gemini sigue teniendo su propia fila, ver process_batch)."""
        logger.info("AuditorIA run_batch method started.")
        logger.info(f"Parameters: cantidad={cantidad}, duracion_min={duracion_min}, duracion_max={duracion_max}, "
                    f"idInteraccion={idInteraccion}, Segmento={Segmento}, Fecha_desde={Fecha_desde}, "
                    f"Fecha_hasta={Fecha_hasta}, loginid={loginid}, empresa={empresa}, "
                    f"campana={campana}, tipificacion={tipificacion}")

        # Contexto de la corrida que viaja hasta process_batch() para abrir la fila
        # de calidad.AuditExecutionLog de cada job de Gemini que se cree (ver
        # AuditorIA/execution_log.py). Se cierra horas después en procesar_batch().
        # `por_operador`/`por_tipificacion` viajan igual que el resto: sin ellos la
        # fila del batch quedaba siempre en 0 y la pantalla de logs mostraba el
        # muestreo por grupo solo en las corridas sincrónicas. El desglose (cuántas
        # interacciones le tocaron a cada grupo) lo calcula process_batch por lote,
        # que es el alcance real de cada fila del log.
        # `run_id` ata entre sí a los lotes de ESTA corrida: calidad_batch parte los
        # audios en lotes de 15 MB y cada uno abre su propia fila del log, así que sin
        # esto la pantalla de /uso-ia muestra una programada de 30 llamados como dos
        # corridas de 30 (parece que se ejecutó dos veces). Se genera acá, una sola vez,
        # antes de saber en cuántos lotes va a caer.
        contexto_ejecucion = {
            "trigger_source": trigger_source, "scheduler_id": scheduler_id,
            "scheduler_name": scheduler_name, "task_id": task_id,
            "fecha_desde": Fecha_desde, "fecha_hasta": Fecha_hasta,
            "cantidad_solicitada": cantidad,
            "por_operador": por_operador, "por_tipificacion": por_tipificacion,
            "run_id": str(uuid.uuid4()),
        }

        try:
            self._actualizar_estado_task(task_id, 'descargando_audios')
            df, empresa_id, campana_id = self._preparar_datos_auditoria(
                cantidad, duracion_min, duracion_max, idInteraccion, Segmento, Fecha_desde,
                Fecha_hasta, loginid, empresa, campana, tipificacion, sentido, custom_audio_paths,
                ucids, saved_files, por_operador, por_tipificacion, omitir_limite, reauditar, plantilla_id,
                comentario=comentario
            )

            if df.empty:
                self._actualizar_estado_task(
                    task_id, 'failed',
                    "AVISO:No se encontraron audios para auditar con los filtros seleccionados. "
                    "Puede que no haya llamadas en el período, que ya estén auditadas "
                    "(activá 'Reauditar' si querés repetirlas) o que fallara la descarga de audios."
                )
                return []

            # Gate de audio: lo mudo no se encola en Gemini (ver audio_calidad.py).
            # Estas filas se guardan ya mismo, no cuando vuelva el batch: no dependen
            # de la IA.
            df, df_no_auditable = audio_calidad.filtrar_no_auditables(df)
            if not df_no_auditable.empty:
                self._guardar_no_auditables(df_no_auditable, campana_id, empresa_id,
                                            plantilla_id, user_id, modo="batch")

            if df.empty:
                self._actualizar_estado_task(
                    task_id, 'failed',
                    "AVISO:Ninguno de los audios seleccionados tiene voz audible, así que no "
                    "se encoló nada. Quedaron registrados como no auditables."
                )
                return []

            batches: Optional[List[str]] = []
            if calidad:
                logger.info("Calling calidad_batch...")
                self._actualizar_estado_task(task_id, 'enviando_a_gemini')
                batches = calidad_batch(
                    engine=self.engine, df=df, gemini_api=self.gemini, user_id=user_id, plantilla_id=plantilla_id,
                    gsheet_id=gsheet_id, gsheet_name=gsheet_name, column_template_id=column_template_id,
                    contexto_ejecucion=contexto_ejecucion,
                    # La transcripción viaja anidada en el mismo request de calidad; la
                    # respuesta se desanida y se guarda en procesar_batch, cuando el job
                    # de Gemini termina (horas después).
                    transcribir=transcribir,
                    # A quién avisarle del resultado: viaja con el lote porque el mail
                    # sale horas después, cuando la tarea programada ya no está a mano.
                    email_destinatarios=email_destinatarios,
                )
            else:
                 logger.info("Skipping calidad_batch because calidad is False.")

            # Estado terminal de éxito para batch: el job quedó encolado en Gemini.
            # No es 'completed' (eso implica resultado descargable); los resultados
            # llegan asincrónicamente por correo cuando el batch termina.
            self._actualizar_estado_task(task_id, 'en_cola')

            # calidad_batch devuelve None cuando había audios pero ningún lote se pudo
            # encolar: eso es un fallo, no un "no había nada", y así lo propagamos.
            return batches

        except AvisoUsuarioError as e:
            # Igual que en run(): los avisos al usuario deben propagarse, no tragarse.
            # Antes de re-lanzar, dejamos el estado en AuditTasks para el frontend.
            if hasattr(e, 'cantidad_obtenida'):
                error_str = f"LIMITE_EXCEDIDO:{e.cantidad_obtenida}:{str(e)}"
            else:
                error_str = f"AVISO:{str(e)}"
            self._actualizar_estado_task(task_id, 'failed', error_str)
            raise
        except sqlalchemy_exc.SQLAlchemyError as e:
            self._actualizar_estado_task(task_id, 'failed', str(e))
            logger.exception(f"Database error during AuditorIA run_batch: {e}")
        except Exception as e:
            self._actualizar_estado_task(task_id, 'failed', str(e))
            logger.exception(f"An unexpected error occurred during AuditorIA run_batch: {e}")
    
    # Tope de pasadas de procesamiento por batch. Sin tope, un lote cuyas respuestas no se
    # pueden procesar se reintentaría cada 15 min para siempre y su metadata en Batch_data
    # nunca se podría limpiar.
    MAX_INTENTOS_PROCESO = 5

    def check_batch_status(self) -> Union[List[Tuple[str,str]], None]:
        """Devuelve los batches de Gemini con resultados listos para guardar.

        El estado de Gemini (BatchJobs.status) y el nuestro (BatchJobs.procesado_at) son
        dos cosas distintas, y hay que mantenerlas separadas: sp_LimpiarDatosDeBatchFinalizados
        borra calidad.Batch_data —los metadatos que atan cada segment_id a su interacción,
        plantilla y campaña— en cuanto damos el lote por cerrado. Si acá marcáramos el batch
        como SUCCEEDED antes de haber guardado sus resultados, un fallo posterior de
        procesar_batch lo dejaría fuera de este SELECT y sin metadata: las respuestas de
        Gemini quedan huérfanas y la auditoría se pierde de forma definitiva.

        Por eso un SUCCEEDED sin procesado_at se sigue devolviendo hasta que se procese
        (o hasta agotar MAX_INTENTOS_PROCESO).

        Solo los lotes de ESTE entorno (BatchJobs.Entorno): dev y prod comparten la tabla,
        y procesar el lote del otro sería guardarlo, exportarlo y mandar su mail con un
        código que no es el suyo."""
        # DEBUG: lo llama el scheduler cada 15 minutos aunque no haya ni un batch
        # abierto; los cambios de estado sí se loguean en INFO más abajo.
        logger.debug("Checking status for batch jobs")
        finished_batch: List[Tuple[str, str]] = []
        try:
            with self.engine.connect() as connection:
                query = text("""
                    SELECT name, status FROM calidad.[BatchJobs]
                    WHERE Entorno = :env
                      AND (status IN ('JOB_STATE_PENDING', 'JOB_STATE_RUNNING')
                           OR (status = 'JOB_STATE_SUCCEEDED'
                               AND procesado_at IS NULL
                               AND intentos_proceso < :max_intentos))
                """)
                results = connection.execute(
                    query, {"max_intentos": self.MAX_INTENTOS_PROCESO, "env": settings.ENVIRONMENT}
                ).fetchall()

                for name, status_db in results:
                    # Ya sabíamos que terminó bien: quedó pendiente de procesar de una
                    # pasada anterior. No hace falta volver a preguntarle a Gemini.
                    if status_db == 'JOB_STATE_SUCCEEDED':
                        finished_batch.append((name, status_db))
                        continue

                    batch_status = self.gemini.batches.get(name=name)
                    state = batch_status.state.name # type: ignore
                    logger.info(f"Estado del batch {name}: {state}")
                    if state not in ['JOB_STATE_SUCCEEDED', 'JOB_STATE_FAILED', 'JOB_STATE_CANCELLED', 'JOB_STATE_EXPIRED']:
                        continue

                    update_query = text("UPDATE calidad.[BatchJobs] SET status = :status WHERE name = :name")
                    connection.execute(update_query, {'status': state, 'name': name})
                    connection.commit()
                    logger.info(f"Batch job {name} completed with status: {state}")

                    if state == 'JOB_STATE_SUCCEEDED':
                        finished_batch.append((name, state))
                    else:
                        # Gemini no produjo resultados: no hay nada que guardar y no tiene
                        # sentido reintentar. Se cierra la corrida para que la tarea no
                        # quede girando en el frontend.
                        self._cerrar_batch_sin_resultado(name, state)

            if finished_batch:
                return finished_batch
            else:
                return None
        except sqlalchemy_exc.SQLAlchemyError as e:
            logger.exception(f"Database error while checking batch status: {e}")
            return None
        except Exception as e:
            logger.exception(f"An unexpected error occurred while checking batch status: {e}")
            return None

    def _tiene_metadata_batch(self, batch_id: str) -> bool:
        """¿Sobrevive la metadata del lote en calidad.Batch_data? Sin ella no se puede
        saber a qué interacción corresponde cada respuesta de Gemini."""
        try:
            with self.engine.connect() as conn:
                return conn.execute(
                    text("SELECT TOP 1 1 FROM calidad.Batch_data WHERE Batch_id = :bid"),
                    {"bid": batch_id},
                ).first() is not None
        except Exception as e:
            logger.error(f"No se pudo verificar la metadata del batch {batch_id}: {e}")
            return False

    def _registrar_intento_proceso(self, batch_id: str) -> int:
        """Suma un intento de procesamiento y devuelve el acumulado."""
        try:
            with self.engine.connect() as conn:
                row = conn.execute(text("""
                    UPDATE calidad.[BatchJobs]
                    SET intentos_proceso = intentos_proceso + 1
                    OUTPUT INSERTED.intentos_proceso
                    WHERE name = :name
                """), {"name": batch_id}).first()
                conn.commit()
                return row[0] if row else 0
        except Exception as e:
            logger.error(f"No se pudo registrar el intento de proceso del batch {batch_id}: {e}")
            return 0

    def _sellar_batch_procesado(self, batch_id: str) -> None:
        """Da el lote por terminado de nuestro lado. Recién a partir de acá
        sp_LimpiarDatosDeBatchFinalizados tiene permitido borrar su Batch_data."""
        try:
            with self.engine.connect() as conn:
                conn.execute(
                    text("UPDATE calidad.[BatchJobs] SET procesado_at = GETDATE() WHERE name = :name"),
                    {"name": batch_id},
                )
                conn.commit()
        except Exception as e:
            logger.error(f"No se pudo sellar el batch {batch_id} como procesado: {e}")

    def _cerrar_batch_sin_resultado(self, batch_id: str, state: str, motivo: Optional[str] = None,
                                    filas_error: Optional[int] = None) -> None:
        """Cierra la corrida de un lote que no va a producir auditorías. Sin esto, la fila
        de AuditTasks queda en 'en_cola' y su AuditExecutionLog en 'EN_CURSO' para siempre,
        y el frontend muestra la auditoría girando sin fin.

        `filas_error`: cuántas interacciones se perdieron. Sin esto la corrida quedaba en
        ERROR pero con 0 errores en la pantalla, que se lee como si no hubiera pasado nada."""
        motivo = motivo or f"El lote de Gemini terminó en estado {state}; no hay resultados para guardar."
        logger.warning(f"Batch {batch_id}: {motivo}")
        try:
            contexto = obtener_contexto(self.engine, batch_id=batch_id) or {}
            self._actualizar_estado_task(contexto.get('task_id'), 'failed', motivo)
            finalizar_ejecucion(self.engine, batch_id=batch_id, status="ERROR",
                                error_message=motivo, filas_error=filas_error)
        except Exception as e:
            logger.error(f"No se pudo cerrar la corrida del batch {batch_id}: {e}")
        # Un lote abandonado también puede ser el último de su corrida: si los otros
        # sí auditaron, el mail con lo que se salvó sale igual (y si no auditó nadie,
        # el log del admin avisa que la corrida terminó en la nada).
        self._notificar_corrida_si_termino(batch_id)
    
    def obtener_info_batch(self, batch_id: str, segment_id: int) -> pd.Series:
        """Obtiene la información original de una interacción específica."""
        logger.info(f"Obteniendo información para batch_id: {batch_id}, segment_id: {segment_id}")
        query = text("""
            SELECT columna, valor
            FROM calidad.Batch_data
            WHERE batch_id = :batch_id AND segment_id = :segment_id
        """)
        try:
            with self.engine.connect() as connection:
                results = connection.execute(query, {'batch_id': batch_id, 'segment_id': segment_id}).fetchall()
                if not results:
                    logger.warning(f"No se encontraron datos para batch_id: {batch_id}, segment_id: {segment_id}")
                    return pd.Series(dtype=object) 

                info_dict = dict(results) # type: ignore
                return pd.Series(info_dict)

        except sqlalchemy_exc.SQLAlchemyError as e:
            logger.exception(f"Error de base de datos al obtener info del batch: {e}")
            return pd.Series(dtype=object)
        except Exception as e:
            logger.exception(f"Error inesperado al obtener info del batch: {e}")
            return pd.Series(dtype=object)

    def borrar_archivo_gemini(self, archivo: str) -> None:
        """Borra un archivo de Gemini.

        Desde que el audio del batch viaja inline en el request (ver
        AuditorIA/gemini.py::process_batch) no queda archivo remoto que borrar y
        Batch_data guarda la columna vacía: ahí no hay nada que hacer. Los batches
        anteriores, todavía en vuelo, sí traen nombre y se borran igual que siempre.
        """
        if not archivo or not str(archivo).strip():
            return
        logger.info(f"Borrando archivo Gemini: {archivo}")
        try:
            self.gemini.files.delete(name=archivo)
        except Exception as e:
            logger.exception(f"Error al borrar archivo Gemini: {e}")

    def _parsear_column_template_id(self, df: pd.DataFrame) -> Optional[int]:
        """Lee el column_template_id que viajó en calidad.Batch_data (guardado como
        string) y lo devuelve como int, o None si no hay plantilla asignada."""
        if 'column_template_id' not in df.columns or df.empty:
            return None
        raw = df['column_template_id'].iloc[0]
        try:
            texto = str(raw).strip()
            if texto in ("", "None", "nan"):
                return None
            return int(float(texto))
        except (ValueError, TypeError):
            return None

    def _parsear_plantilla_version_id(self, df: pd.DataFrame) -> Optional[int]:
        """Lee la versión de plantilla que viajó en calidad.Batch_data (guardada como
        string por gemini.py::process_batch), o None si no viajó (batches previos a
        la Fase 2, o migración de versionado sin aplicar)."""
        if 'plantilla_version_id' not in df.columns or df.empty:
            return None
        raw = df['plantilla_version_id'].iloc[0]
        try:
            texto = str(raw).strip()
            if texto in ("", "None", "nan"):
                return None
            return int(float(texto))
        except (ValueError, TypeError):
            return None

    def _parsear_modelo_ia(self, df: pd.DataFrame) -> Optional[str]:
        """Lee el modelo de Gemini que viajó en calidad.Batch_data (guardado por
        gemini.py::process_batch junto a column_template_id/gsheet_id), o None si
        no viajó (batches viejos, previos a esta feature)."""
        if 'modelo_ia' not in df.columns or df.empty:
            return None
        raw = df['modelo_ia'].iloc[0]
        texto = str(raw).strip()
        return texto if texto and texto not in ("None", "nan") else None

    def _obtener_export_auditorias(self, df: pd.DataFrame, campana, empresa, plantilla_id,
                                   user_id, column_template_id: Optional[int] = None,
                                   fecha_desde: Optional[Any] = None) -> pd.DataFrame:
        """Reconsulta calidad.sp_ObtenerAuditoriasFiltradas para las interacciones recién
        guardadas y devuelve el mismo set de columnas filtradas/corregidas que muestra la
        pantalla "Auditorías Realizadas", aplicando la plantilla de columnas si la hay.

        `fecha_desde` acota la consulta del SP (por defecto, hoy). El mail de una corrida
        completa lo usa para arrancar en el día en que la corrida EMPEZÓ: un batch puede
        tardar horas y cruzar la medianoche, y con el filtro de un solo día las auditorías
        del lote que cerró ayer quedarían afuera del Excel.

        Devuelve un DataFrame vacío si no se puede reconstruir; el caller decide el
        fallback (no enviar nada a GSheets vs. adjuntar el DF crudo al correo)."""
        try:
            if 'id_aplicativo' not in df.columns:
                logger.warning("No hay 'id_aplicativo' en el DF del batch; no se puede reconsultar el SP.")
                return pd.DataFrame()

            ids = [str(x) for x in df['id_aplicativo'].tolist() if x is not None and str(x).strip()]
            if not ids:
                logger.warning("No se obtuvieron id_aplicativo del batch para reconsultar el SP.")
                return pd.DataFrame()

            hoy = datetime.now().date()
            desde = fecha_desde or hoy
            query = text("""
                EXEC calidad.sp_ObtenerAuditoriasFiltradas
                    @AuditorUsuarioID = :usuario,
                    @CampanaID = :campana,
                    @EmpresaID = :empresa,
                    @PlantillaID = :plantilla,
                    @FechaDesde = :fecha_desde,
                    @FechaHasta = :fecha_hasta,
                    @IdAplicativo = :id_aplicativo,
                    @IncluirTranscripcion = :incluir_transcripcion,
                    @IncluirResponseThoughts = :response_thoughts
            """)
            db_params = {
                "usuario": user_id,
                "campana": campana,
                "empresa": empresa,
                "plantilla": plantilla_id,
                "fecha_desde": desde,
                "fecha_hasta": hoy,
                "id_aplicativo": ",".join(ids),
                "incluir_transcripcion": True,
                "response_thoughts": True,
            }

            with self.engine.connect() as conn:
                result = conn.execute(query, db_params)
                columnas_sql = list(result.keys())
                rows = result.mappings().all()

            df_export = pd.DataFrame(rows, columns=columnas_sql)
            # Saca las columnas técnicas y las opt-in, y aplica la plantilla de columnas
            # si la hay. Lo mismo que hace el scheduler sincrónico (app/tasks.py), para
            # que ambos exports tengan exactamente la misma forma.
            df_export = preparar_export(self.engine, df_export, column_template_id)
            return df_export
        except Exception as e:
            logger.error(f"No se pudo reconstruir el export limpio del batch desde el SP: {e}", exc_info=True)
            return pd.DataFrame()

    def procesar_batch(self, lista_batches: List[Tuple[str, str]]) -> None:
        logger.info(f"Iniciando el procesamiento de {len(lista_batches)} batches completados.")
        lista_serie = []
        # Transcripciones que vinieron anidadas en la respuesta de calidad (solo si la
        # corrida las pidió, ver gemini.py::process_batch). Van a su propia tabla, no a
        # las columnas de la auditoría. Se agrupan por batch_id desde el principio: dos
        # batches pueden venir de orígenes con claves distintas (idInteraccion vs connid)
        # y, unidos en un solo DataFrame, las columnas del otro entran vacías y le roban
        # la prioridad a la clave real al calcular el IdAplicativo.
        trans_por_batch: Dict[str, List[pd.Series]] = {}
        # Un lote solo se sella (procesado_at) cuando sus resultados quedaron guardados; si
        # no, se reintenta en la próxima pasada del scheduler con la metadata todavía en
        # pie. 'cerrados' son los que se abandonaron explícitamente y no hay que reintentar.
        intentos: Dict[str, int] = {}
        cerrados: set = set()
        # Por qué falló cada lote, contado por motivo. Es lo único que queda cuando el
        # lote se abandona: sin esto, el error_message de la corrida era solo "no se
        # pudo procesar después de 5 intentos" y había que ir al journal del servidor
        # a reconstruir la causa (y encontrarla ahí antes de que rote).
        motivos: Dict[str, Counter] = defaultdict(Counter)
        for batch_id, _ in lista_batches:
            logger.info(f"Procesando batch con ID: {batch_id}")
            intentos[batch_id] = self._registrar_intento_proceso(batch_id)

            if not self._tiene_metadata_batch(batch_id):
                # Sin Batch_data no se puede saber a qué interacción, plantilla y campaña
                # pertenece cada respuesta: quedan huérfanas. Es terminal, reintentar no
                # las recupera.
                self._cerrar_batch_sin_resultado(
                    batch_id, "SIN_METADATA",
                    motivo=("Se perdió la metadata del lote (calidad.Batch_data no tiene filas para este "
                            "batch_id): las respuestas de Gemini ya no se pueden atribuir a sus interacciones."),
                )
                self._sellar_batch_procesado(batch_id)
                cerrados.add(batch_id)
                continue

            # Un fallo de red/API acá no puede propagarse: abortaría la pasada entera y se
            # llevaría puestos a los demás lotes de la misma ventana de polling. El lote
            # queda sin sellar, así que la próxima pasada lo vuelve a traer (y al agotar
            # MAX_INTENTOS_PROCESO se abandona explícitamente más abajo).
            try:
                batch_job = self.gemini.batches.get(name=batch_id)
            except Exception as e:
                motivos[batch_id][f"No se pudo recuperar el lote de Gemini: {e}"[:300]] += 1
                logger.error(
                    f"No se pudo recuperar el batch {batch_id} de Gemini: {e}. "
                    f"Se reintentará en la próxima pasada del scheduler."
                )
                continue

            # Las respuestas vuelven inline (lotes viejos, mandados como request) o en
            # un archivo (los mandados como JSONL, ver AuditorIA/batch_cola.py). El
            # lector devuelve las dos formas igual: [(segment_id, job), ...].
            try:
                respuestas = batch_cola.leer_respuestas(self.gemini, batch_job)
            except Exception as e:
                motivos[batch_id][f"No se pudo leer el resultado del lote: {e}"[:300]] += 1
                logger.error(
                    f"No se pudo leer el resultado del batch {batch_id}: {e}. "
                    f"Se reintentará en la próxima pasada del scheduler."
                )
                continue

            if not respuestas:
                motivos[batch_id]["El lote terminó sin respuestas (dest vacío en Gemini)."] += 1
                logger.warning(f"El batch {batch_id} no tiene respuestas o está en un estado inesperado. Saltando.")
                continue
            else:
                logger.info(f"El batch {batch_id} contiene {len(respuestas)} trabajos para procesar.")
                for i, job in respuestas:
                    try:
                        logger.debug(f"Procesando job {i} del batch {batch_id}")
                        serie_info = self.obtener_info_batch(batch_id=batch_id, segment_id=i)

                        if serie_info.empty:
                            logger.warning(f"No se encontró información para batch_id: {batch_id}, segment_id: {i}. Saltando.")
                            continue
                        else:
                            self.borrar_archivo_gemini(archivo=serie_info['gemini_file_name'])

                        # Gemini puede cerrar el LOTE en SUCCEEDED y aun así fallar
                        # request por request: ahí `response` viene en None y el motivo
                        # está en `error`. Sin este chequeo, el parseo explotaba con un
                        # 'NoneType' object has no attribute 'candidates' que ocultaba
                        # la causa real (ver _texto_error_gemini).
                        if job.response is None:
                            motivo = _texto_error_gemini(getattr(job, "error", None))
                            motivos[batch_id][motivo] += 1
                            logger.error(
                                f"Gemini no devolvió respuesta para el job {i} del batch "
                                f"{batch_id}. Detalle: {motivo}"
                            )
                            continue

                        # Si esto falla, el except lo atrapa y el bucle sigue con el siguiente 'i'.
                        # La respuesta trae la calidad y, si la corrida pidió transcribir,
                        # también la transcripción anidada; el parseo combinado la separa.
                        # Para los batches que no la pidieron (o previos a esta feature) la
                        # transcripción viene None y el resultado es idéntico al de antes.
                        procesed_response, serie_trans = procesar_respuesta_combinada(response=job.response)

                        serie = pd.concat([serie_info, procesed_response])
                        # Marca de qué job de Gemini viene cada fila: es la clave para
                        # no mezclar corridas distintas al guardar/exportar/mailear (ver
                        # el groupby más abajo).
                        serie['_batch_id_origen'] = batch_id
                        lista_serie.append(serie)

                        if serie_trans is not None:
                            # La transcripción necesita los datos originales de la
                            # interacción (serie_info) para resolver su IdAplicativo.
                            trans_por_batch.setdefault(batch_id, []).append(
                                pd.concat([serie_info, serie_trans])
                            )

                    except Exception as e:
                        motivos[batch_id][f"{type(e).__name__}: {e}"[:300]] += 1
                        logger.error(f"Error procesando el job {i} del batch {batch_id}. Se omitirá esta interacción. Detalle: {e}")
                        continue # Pasa al siguiente job sin romper el sistema

        logger.info(f"Creando DataFrame con {len(lista_serie)} resultados procesados.")
        df_total = pd.DataFrame(lista_serie)

        if df_total.empty:
            logger.warning("Ningún batch produjo resultados procesables.")

        procesados_ok: set = set()
        if not df_total.empty:
            # Cada batch_id de Gemini es una corrida independiente, con su propia
            # campaña/empresa/usuario y su propia fila de calidad.AuditExecutionLog.
            # ANTES se tomaba iloc[0] de TODO df_total para decidir campaña/empresa/
            # usuario de TODO el guardado/export/mail: si dos jobs de campañas o
            # usuarios distintos terminaban en la misma ventana de polling (15 min,
            # ver check_completed_batches), sus resultados se mezclaban y el mail
            # final se enviaba atribuido a un solo usuario/campaña. Procesamos cada
            # batch_id por separado para que un grupo nunca contamine a otro.
            for batch_id_origen, df_grupo in df_total.groupby('_batch_id_origen'):
                batch_id_origen = str(batch_id_origen)
                df_grupo = df_grupo.drop(columns=['_batch_id_origen']).reset_index(drop=True)
                # Un grupo del groupby arrastra TODAS las columnas de df_total, también
                # las que aportaron los otros lotes de la misma pasada (enteras en NaN
                # para este). No son inocuas: tanto la resolución de alias
                # (_estandarizar_columnas_sql) como la clave (calcular_id_aplicativo)
                # deciden por PRESENCIA de la columna y por orden de prioridad, no por si
                # tiene datos. Un lote de Farmalux (columna LoginId) en la misma ventana
                # le regalaba a Dental/ALARMIX una LoginId vacía que le ganaba a su propia
                # columna Operador -> operadorUsuario NULL; y un lote de Dental (columnas
                # idInteraccion + Segmento) le hacía calcular a ALARMIX un IdAplicativo
                # 'nan_nan' en vez de usar su segmentId. Una columna sin un solo dato no
                # es de este lote: se descarta antes de interpretar nada.
                df_grupo = df_grupo.dropna(axis=1, how='all')
                df_trans_grupo = pd.DataFrame(trans_por_batch.get(batch_id_origen, []))
                try:
                    # La estandarización de columnas va acá adentro, por lote, y no sobre
                    # el df_total de la pasada: es código que toca datos crudos de la
                    # descarga (parseos de fecha, duración, IdTurno...) y cualquier valor
                    # inesperado de UN cliente hacía estallar la pasada COMPLETA. El
                    # IdTurno científico de Dental dejó así 21 corridas en EN_CURSO para
                    # siempre —13 propias y 8 de otras empresas que solo tuvieron la mala
                    # suerte de caer en la misma ventana de polling—, porque la excepción
                    # se escapaba de procesar_batch antes de que nada se sellara, se
                    # cerrara o se abandonara. Adentro del try, el lote roto cierra en
                    # ERROR con su motivo y los demás se siguen guardando.
                    df_grupo = self._estandarizar_columnas_sql(df_grupo)
                    df_grupo = self._renombrar_detalles_a_atributo_id(df_grupo)
                    self._procesar_grupo_batch(batch_id_origen, df_grupo, df_trans_grupo,
                                               motivos_gemini=motivos.get(batch_id_origen))
                    # Los resultados ya están en SQL: recién ahora se puede soltar la
                    # metadata del lote.
                    self._sellar_batch_procesado(batch_id_origen)
                    procesados_ok.add(batch_id_origen)
                except Exception as e:
                    logger.error(f"Error procesando el grupo del batch {batch_id_origen}: {e}", exc_info=True)
                    # Los motivos por interacción (si los hubo) van con el error del grupo:
                    # el lote pudo haber perdido la mitad de sus llamados antes de romperse.
                    finalizar_ejecucion(
                        self.engine, batch_id=batch_id_origen, status="ERROR",
                        error_message=f"{e}{_resumen_motivos(motivos.get(batch_id_origen))}"[:4000],
                    )

        # Los que no llegaron a guardarse quedan sin sellar y con su Batch_data intacta, así
        # que el próximo check_batch_status los vuelve a traer. Al agotar los intentos se
        # abandonan de forma explícita, en vez de reintentar para siempre.
        for batch_id, n_intentos in intentos.items():
            if batch_id in procesados_ok or batch_id in cerrados:
                continue
            fallidas = sum(motivos.get(batch_id, Counter()).values())
            if n_intentos >= self.MAX_INTENTOS_PROCESO:
                self._cerrar_batch_sin_resultado(
                    batch_id, "ERROR_PROCESO",
                    motivo=(f"El lote no se pudo procesar después de {n_intentos} intentos; se abandona."
                            f"{_resumen_motivos(motivos.get(batch_id))}")[:4000],
                    filas_error=fallidas or None,
                )
                self._sellar_batch_procesado(batch_id)
            else:
                logger.warning(
                    f"Batch {batch_id}: intento {n_intentos} sin guardar resultados. "
                    f"Se reintentará en la próxima pasada del scheduler."
                    f"{_resumen_motivos(motivos.get(batch_id))}"
                )

        logger.info("Procesamiento de batches completado.")

    def _renombrar_detalles_a_atributo_id(self, df: pd.DataFrame) -> pd.DataFrame:
        """Pasa las columnas Detalle_<nombre_atributo> a Detalle_<AtributoID>, que es lo
        que espera guardar_auditoria_en_sql.

        El mapa se toma del propio batch y no del primero de la tanda: cuando dos batches
        de plantillas distintas terminan en la misma ventana de polling, df_total trae la
        unión de las columnas de ambos, y renombrar todo con el mapa del primero dejaba
        las del otro con su nombre de texto (Detalle_corte_abrupto). Al guardar, el
        int() sobre el sufijo reventaba y se caían las dos corridas.
        """
        if 'prompt_nombre_id_map' not in df.columns:
            return df

        mapa_crudo = df['prompt_nombre_id_map'].iloc[0]
        df = df.drop(columns=['prompt_nombre_id_map'])

        if isinstance(mapa_crudo, str):
            mapa_crudo = ast.literal_eval(mapa_crudo)
        df = df.rename(columns={f"Detalle_{k}": f"Detalle_{v}" for k, v in (mapa_crudo or {}).items()})

        # Lo que siga sin resolver a un AtributoID es de otra plantilla (columnas vacías
        # que arrastró la unión de batches): no son de esta corrida y no se guardan.
        ajenas = [c for c in df.columns if c.startswith("Detalle_") and not c.split("_", 1)[1].isdigit()]
        if ajenas:
            logger.warning(f"Batch con columnas Detalle_ de otra plantilla, se descartan: {ajenas}")
            df = df.drop(columns=ajenas)

        return df

    def _guardar_transcripciones_batch(self, df_trans: Optional[pd.DataFrame], user_id, modelo: Optional[str]) -> None:
        """Guarda en calidad.transcripciones las transcripciones que llegaron anidadas
        en la respuesta de un batch (ver gemini.py::process_batch).

        Se vuelve a deduplicar acá, y no solo al armar el request: entre el envío del
        batch y su resultado pasan horas, y en el medio otra corrida (sync o batch) pudo
        transcribir el mismo llamado.

        Nunca debe cortar el procesamiento del batch: la auditoría ya está guardada y la
        transcripción es un extra."""
        if df_trans is None or df_trans.empty:
            return
        try:
            ids = calcular_id_aplicativo(df_trans).astype(str)
            ya_transcriptos = ids_con_transcripcion(self.engine, ids)
            df_pendientes = df_trans[~ids.isin(ya_transcriptos)]
            if df_pendientes.empty:
                logger.info("Batch: las transcripciones recibidas ya estaban guardadas; no se duplican.")
                return
            guardar_transcripciones_sql(
                df_pendientes, engine=self.engine, user_id=user_id, modo="batch",
                modelo=modelo or MODELO_IA_DEFAULT,
            )
            logger.info(f"Batch: guardadas {len(df_pendientes)} transcripciones.")
        except Exception as e:
            logger.error(f"No se pudieron guardar las transcripciones del batch: {e}", exc_info=True)

    def _procesar_grupo_batch(self, batch_id: str, df: pd.DataFrame, df_trans: Optional[pd.DataFrame] = None,
                              motivos_gemini: Optional[Counter] = None) -> None:
        """Guarda, exporta y notifica los resultados de UN job de Gemini (un
        batch_id). Ver procesar_batch: cada batch_id es una corrida propia, no
        se mezcla con los resultados de otro batch_id.

        `motivos_gemini`: las interacciones del lote que Gemini devolvió sin respuesta
        (o que no se pudieron parsear), contadas por motivo. No llegan a `df`, así que
        sin esto un lote que perdió 24 de 30 cerraba como EXITO con 0 errores (Cortes
        abruptos - ALARMIX, 15/09/2026) y nadie se enteraba de que faltaban auditorías."""
        campana = df['campana_id'].iloc[0]
        empresa = df['empresa_id'].iloc[0]
        plantilla_id = df['plantilla_id'].iloc[0]
        user_id = df['user_id'].iloc[0]
        column_template_id = self._parsear_column_template_id(df)
        modelo_ia = self._parsear_modelo_ia(df)

        # Contexto de la corrida (guardado al momento de subir el batch a Gemini,
        # ver process_batch en gemini.py): trae origen, rango solicitado y
        # started_at, que ya no están disponibles acá.
        contexto = obtener_contexto(self.engine, batch_id=batch_id) or {}
        task_id = contexto.get('task_id')

        # Incidencias (ver AuditorIA/incidencias.py): mismo cierre que en sync, pero acá
        # recién ahora está la respuesta de la IA y la transcripción del lote. El gate de
        # audio ya corrió al encolar, y lo que haya marcado viaja en el propio df.
        df = incidencias.fusionar_declarada_por_ia(df)
        if df_trans is not None and not df_trans.empty:
            incidencias.verificar_operador(df, df_trans)
        incidencias.normalizar_columna(df)

        self._evaluar_y_enviar_avisos(df, plantilla_id)
        resumen = self._finalizar_y_guardar_sql(
            df=df, campana_id=campana, empresa_id=empresa, plantilla_id=plantilla_id, user_id=user_id, modo="batch",
            modelo=modelo_ia
        )
        logger.info(f"Batch {batch_id}: guardado en SQL completado ({resumen['filas_auditadas']} filas).")

        # Las transcripciones van a su propia tabla (calidad.transcripciones), asociadas
        # a la auditoría por IdAplicativo. Se guardan después de la auditoría para que el
        # SP del export ya las encuentre asociadas.
        self._guardar_transcripciones_batch(df_trans, user_id=user_id, modelo=modelo_ia)

        # Reconstruimos el export "limpio": misma información que muestra la grilla
        # "Auditorías Realizadas" (reconsultando el SP por los ids recién guardados y
        # aplicando la plantilla de columnas). Evita enviar a GSheets/correo el DF crudo
        # del batch, que arrastra columnas internas (nombre de archivo, ids, etc.).
        df_export = self._obtener_export_auditorias(
            df=df, campana=campana, empresa=empresa,
            plantilla_id=plantilla_id, user_id=user_id,
            column_template_id=column_template_id,
        )

        gsheet_enviado = False

        try:
            sheet_id = df['gsheet_id'].iloc[0] if 'gsheet_id' in df.columns and df['gsheet_id'].iloc[0] else None
            nombre_hoja_custom = df['gsheet_name'].iloc[0] if 'gsheet_name' in df.columns and df['gsheet_name'].iloc[0] else None
            if sheet_id and not df_export.empty:
                try:
                    # Sheets sí va por lote: dataframe_a_sheet AGREGA al final de la hoja
                    # si ya existe, así que los lotes de una corrida se acumulan solos en
                    # la misma pestaña. El mail, en cambio, no se puede "agregar": por eso
                    # ese sale una vez, al final (ver _notificar_corrida_si_termino).
                    nombre_hoja = nombre_hoja_custom if nombre_hoja_custom else f"Batch_{campana}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    resultado_gs = dataframe_a_sheet(
                        sheet_id=sheet_id,
                        df=df_export,
                        nombre_hoja=nombre_hoja
                    )
                    gsheet_enviado = True
                    logger.info(f"Resultados de batch subidos a Google Sheets: {resultado_gs}")
                except Exception as e:
                    logger.error(f"Error subiendo resultados del batch a Google Sheets: {e}")
            elif sheet_id:
                logger.warning(
                    "Se pidió enviar el batch a Google Sheets pero el SP no devolvió filas "
                    "para los ids auditados; no se sube nada (evitamos enviar datos crudos)."
                )
        except Exception as e:
            logger.error(f"Error exportando el batch {batch_id} a Google Sheets: {e}", exc_info=True)

        # Cierra el estado en AuditTasks para corridas manuales (antes quedaba
        # 'en_cola' para siempre: nada actualizaba esa tabla cuando el batch
        # terminaba horas después).
        result_json = None
        try:
            df_adjunto_json = df_export if not df_export.empty else df
            if not df_adjunto_json.empty:
                result_json = df_adjunto_json.to_json(orient='records')
        except Exception as e:
            logger.error(f"No se pudo serializar el resultado del batch {batch_id} para AuditTasks: {e}")
        self._actualizar_estado_task(task_id, 'completed', result_data=result_json)

        # `id_aplicativos`: qué auditó ESTE lote. El mail de la corrida se manda cuando
        # cierra el último, y para adjuntar la auditoría COMPLETA hay que reconsultar el
        # SP por los ids de todos los lotes — que pueden haber cerrado en pasadas (o
        # días) distintos, así que no alcanza con lo que hay en memoria.
        perdidas_en_gemini = sum(motivos_gemini.values()) if motivos_gemini else 0
        filas_error = resumen["filas_error"] + perdidas_en_gemini
        finalizar_ejecucion(
            self.engine, batch_id=batch_id,
            status="EXITO" if filas_error == 0 else "PARCIAL",
            filas_auditadas=resumen["filas_auditadas"], filas_error=filas_error,
            error_message=(
                f"{perdidas_en_gemini} interacción(es) sin respuesta de Gemini; no se auditaron."
                f"{_resumen_motivos(motivos_gemini)}"[:4000]
                if perdidas_en_gemini else None
            ),
            tokens=resumen["tokens"], gsheet_enviado=gsheet_enviado, modelo=modelo_ia,
            id_aplicativos=self._ids_auditados(df),
        )

        # Si este era el último lote en vuelo, recién ahora se notifica la corrida.
        self._notificar_corrida_si_termino(batch_id, column_template_id=column_template_id)

    @staticmethod
    def _ids_auditados(df: pd.DataFrame) -> Optional[str]:
        """Los IdAplicativo del lote, para guardarlos en el log de la corrida."""
        if 'id_aplicativo' not in df.columns:
            return None
        ids = [str(x) for x in df['id_aplicativo'].tolist() if x is not None and str(x).strip()]
        return ",".join(ids) if ids else None

    def _column_template_de_scheduler(self, scheduler_id) -> Optional[int]:
        """Plantilla de columnas configurada en la tarea programada.

        Fallback para cuando la corrida la cierra un lote que no tiene DataFrame
        (uno abandonado, ver _cerrar_batch_sin_resultado): sin esto, el Excel de una
        corrida así saldría con todas las columnas en vez de las configuradas."""
        if scheduler_id is None:
            return None
        try:
            with self.engine.connect() as conn:
                fila = conn.execute(
                    text("SELECT column_template_id FROM calidad.AuditSchedulers WHERE id = :id"),
                    {"id": scheduler_id},
                ).first()
            return fila[0] if fila else None
        except Exception as e:
            logger.error(f"No se pudo leer la plantilla de columnas del scheduler {scheduler_id}: {e}")
            return None

    def _destinatarios_de_la_corrida(self, resumen_corrida: Dict[str, Any], user_id) -> List[str]:
        """A quién se le manda el resultado.

        Primero los destinatarios configurados en la tarea programada (viajaron con el
        lote hasta el log, ver gemini.py::process_batch). Si no hay —auditoría manual,
        o tarea con el envío por mail apagado— se cae al mail interno de quien la
        disparó, que es el comportamiento que tenía este camino para todos los casos:
        de ahí venía que las tareas programadas notificaran SOLO a su creador."""
        crudos = resumen_corrida.get("mail_destinatarios") or ""
        destinatarios = [d.strip() for d in crudos.split(",") if d.strip()]
        if destinatarios:
            return destinatarios
        try:
            with self.engine.connect() as conn:
                fila = conn.execute(text("""
                    SELECT [EMAIL INTERNO]
                    FROM [Acme].[dbo].[nomina_extendida]
                    WHERE DOCUMENTO = :documento
                """), {"documento": str(user_id)}).fetchone()
            if fila and fila[0]:
                return [fila[0]]
            logger.warning(f"No se encontró [EMAIL INTERNO] para el documento {user_id}: la corrida no se notifica.")
        except Exception as e:
            logger.error(f"No se pudo resolver el mail interno del documento {user_id}: {e}")
        return []

    def _notificar_corrida_si_termino(self, batch_id: str, column_template_id: Optional[int] = None) -> None:
        """Manda UN mail con el resultado de la corrida completa, si ya cerraron todos
        sus lotes; si queda alguno en vuelo, no hace nada y espera al que cierre último.

        Antes esto vivía en `_procesar_grupo_batch`, o sea que salía un mail por cada
        job de Gemini: una corrida de Vantix partida en 14 lotes mandaba 14 mails, cada
        uno con una fracción de la auditoría y ninguno con el total. Acá se junta todo:
        un mail con las auditorías de la corrida entera adjuntas.

        Nunca puede interrumpir el guardado: las auditorías ya están en SQL cuando esto
        corre, así que todo va con try/except y a lo sumo se pierde la notificación."""
        try:
            resumen_corrida = corrida_terminada(self.engine, batch_id=batch_id)
            if not resumen_corrida:
                return
            if resumen_corrida.get("ya_notificada"):
                logger.info(f"Corrida {resumen_corrida['run_id']}: ya se había notificado; no se repite el mail.")
                return

            contexto = obtener_contexto(self.engine, batch_id=batch_id) or {}
            campana, empresa = contexto.get('campana'), contexto.get('empresa')
            plantilla_id, user_id = contexto.get('plantilla_id'), contexto.get('user_id')
            if column_template_id is None:
                column_template_id = self._column_template_de_scheduler(contexto.get('scheduler_id'))

            ids = resumen_corrida["id_aplicativos"]
            lotes = resumen_corrida["lotes"]
            resumen = {
                "filas_auditadas": resumen_corrida["filas_auditadas"],
                "filas_error": resumen_corrida["filas_error"],
                "tokens": resumen_corrida["tokens"],
            }

            mail_enviado = False
            destinatarios: List[str] = []
            if ids:
                # El adjunto se reconstruye con los ids de TODOS los lotes. El rango de
                # fechas arranca en el inicio de la corrida y no en "hoy": un batch puede
                # tardar horas y cruzar la medianoche, y con el filtro de un solo día las
                # auditorías del lote que cerró ayer no entrarían en el Excel.
                df_export = self._obtener_export_auditorias(
                    df=pd.DataFrame({"id_aplicativo": ids}),
                    campana=campana, empresa=empresa, plantilla_id=plantilla_id,
                    user_id=user_id, column_template_id=column_template_id,
                    fecha_desde=self._fecha_local(contexto.get('started_at')),
                )
                destinatarios = self._destinatarios_de_la_corrida(resumen_corrida, user_id)
                if destinatarios and not df_export.empty:
                    mail_enviado = self._enviar_mail_resultado_corrida(
                        destinatarios=destinatarios, campana=campana,
                        df_export=df_export, lotes=lotes,
                    )
                elif destinatarios:
                    logger.warning(
                        f"Corrida {resumen_corrida['run_id']}: no se pudo reconstruir el export "
                        f"({len(ids)} auditorías guardadas); no se manda el mail de resultado."
                    )
            else:
                logger.warning(
                    f"Corrida {resumen_corrida['run_id']}: ninguno de sus {lotes} lote(s) dejó "
                    f"auditorías ({resumen_corrida['estados']}); no hay resultado que mandar."
                )

            marcar_envio_corrida(
                self.engine, run_id=resumen_corrida["run_id"], mail_enviado=mail_enviado,
                mail_destinatarios=",".join(destinatarios) if destinatarios else None,
            )

            # El detalle interno (tokens, costo, duración) va SIEMPRE, aunque la corrida
            # no haya producido nada: es justamente cuando más hace falta enterarse.
            self._enviar_mail_log_admin(
                modo="batch", campana=campana, empresa=empresa, plantilla_id=plantilla_id,
                resumen=resumen, contexto=contexto, modelo=contexto.get('modelo'), lotes=lotes,
            )
        except Exception as e:
            logger.error(f"No se pudo notificar el cierre de la corrida del batch {batch_id}: {e}", exc_info=True)

    @staticmethod
    def _fecha_local(started_at) -> Optional[Any]:
        """started_at del log está en UTC; el SP filtra por fecha local (-3h)."""
        if started_at is None:
            return None
        try:
            return (started_at - timedelta(hours=3)).date()
        except Exception:
            return None

    def _enviar_mail_resultado_corrida(self, destinatarios: List[str], campana,
                                       df_export: pd.DataFrame, lotes: int) -> bool:
        """El mail de resultado: uno por corrida, con todas sus auditorías adjuntas."""
        try:
            stream = io.BytesIO()
            df_export.to_excel(stream, engine='openpyxl', index=False)
            adjunto = {
                "nombre": f"Resultados_Batch_Campana_{campana}.xlsx",
                "contenido": stream.getvalue(),
                "tipo": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            }
            # El detalle de en cuántos lotes se partió es ruido para el destinatario
            # (es un detalle del transporte a Gemini): solo se cuenta cuántas
            # auditorías tiene el adjunto.
            cuerpo = (
                f"Hola,<br><br>"
                f"Te informamos que el procesamiento de la auditoría en lote (Batch) "
                f"para la campaña <b>{campana}</b> ha finalizado con éxito.<br>"
                f"Se adjuntan las <b>{len(df_export)}</b> auditorías de esta corrida, "
                f"que ya quedaron guardadas en el sistema.<br><br>"
                f"Saludos,<br><b>Sistema de Calidad Acme Solutions</b>"
            )
            Envios_mail_manager.enviar_correo_base(
                destinatarios=destinatarios,
                asunto=f"Auditoría Batch Completada - Campaña {campana}",
                mensaje_html=cuerpo,
                archivos_adjuntos_memoria=[adjunto],
            )
            logger.info(
                f"Mail de la corrida enviado a {', '.join(destinatarios)} "
                f"({len(df_export)} auditorías de {lotes} lote(s))."
            )
            return True
        except Exception as e:
            logger.error(f"No se pudo enviar el mail de resultado de la corrida: {e}", exc_info=True)
            return False

    def _enviar_mail_log_admin(self, modo: str, campana, empresa, plantilla_id, resumen: Dict[str, Any],
                               contexto: Dict[str, Any], modelo: Optional[str] = None,
                               lotes: Optional[int] = None) -> None:
        """Manda el detalle de la corrida (modo, origen, rango/cantidad, duración,
        errores, tokens, costo estimado) SOLO a settings.ADMIN_EMAIL. Nunca al
        destinatario de la auditoría: es información interna de seguimiento, no
        el resultado de la auditoría en sí."""
        try:
            if not settings.ADMIN_EMAIL:
                return
            scheduler_name = contexto.get('scheduler_name')
            trigger_source = contexto.get('trigger_source')
            origen_txt = (
                f'Tarea programada "{scheduler_name}"' if trigger_source == "scheduler" and scheduler_name
                else "Ejecución manual"
            )
            duracion_txt = formatear_duracion(contexto.get('started_at'))
            # `modelo`: el modelo REAL usado para generar esta corrida (viene de
            # calidad.Batch_data en batch, ver _parsear_modelo_ia); si no vino
            # (batches previos a esta feature), se asume el default global.
            costo_usd = estimar_costo_usd(self.engine, modelo=modelo or MODELO_IA_DEFAULT, modo=modo, tokens=resumen["tokens"])

            detalle_html = armar_detalle_html(
                modo=modo, origen_txt=origen_txt,
                empresa=empresa, campana=campana, plantilla_id=plantilla_id,
                fecha_desde=contexto.get('fecha_desde'), fecha_hasta=contexto.get('fecha_hasta'),
                cantidad_solicitada=contexto.get('cantidad_solicitada'),
                filas_auditadas=resumen["filas_auditadas"], filas_error=resumen["filas_error"],
                tokens=resumen["tokens"], duracion_txt=duracion_txt, costo_usd=costo_usd,
                # `contexto` es un SELECT * de la fila del log, así que trae el nivel
                # con el que se creó el job (None en corridas previas a la feature).
                nivel_razonamiento=contexto.get('nivel_razonamiento'),
            )

            # En cuántos lotes de Gemini se partió la corrida: acá sí interesa (es
            # información de operación, y explica por qué una corrida puede tardar
            # mucho más que otra del mismo tamaño).
            if lotes and lotes > 1:
                detalle_html += f"<p>Enviada a Gemini en <b>{lotes}</b> lotes.</p>"

            Envios_mail_manager.enviar_correo_base(
                destinatarios=[settings.ADMIN_EMAIL],
                asunto=f"[Log Auditoría] Batch - Campaña {campana}",
                mensaje_html=detalle_html,
                archivos_adjuntos_memoria=[],
            )
        except Exception as e:
            logger.error(f"No se pudo enviar el mail de log a ADMIN_EMAIL: {e}")


def main():
    engine = create_engine(settings.connection_string)
    auditor = AuditorIA(engine=engine)
    batch_completos = auditor.check_batch_status()
    if batch_completos:
        auditor.procesar_batch(batch_completos)
    print('Hello')
    

if  __name__ == '__main__':
    main()