import logging
from datetime import datetime
from sqlalchemy import bindparam, text
from app.database import engine
from app.utils.mail import RRHH_mail_manager
from typing import List, Dict, Any
from collections import defaultdict
import pandas as pd

# Importamos utilidades necesarias
from app.utils.google_sheet import dataframe_a_sheet
from app.config import settings
logger = logging.getLogger(__name__)

def _obtener_candidatos_pendientes() -> list:
    """Busca en SQL todos los aceptados que les falte el mail o el sheets."""
    query_get = text("""
        SELECT *
        FROM rrhh.Candidatos 
        WHERE Estado = 'ACEPTADO' 
          AND (ISNULL(Mail_Enviado, 0) = 0 OR ISNULL(Sincronizado_Ingresos, 0) = 0)
    """)
    with engine.connect() as conn:
        result = conn.execute(query_get).mappings().all()
        return [dict(row) for row in result]


# ==========================================
# 2. FUNCIÓN DE ENVÍO DE CORREOS
# ==========================================
def _procesar_envio_mails(pendientes_mail: list):
    """Agrupa candidatos, envía correo BCC general, ICS individual y actualiza la BD."""
    grupos = defaultdict(list)
    for c in pendientes_mail:
        clave = (str(c['Entrevista_Fecha']), str(c['Entrevista_Hora']), str(c['Entrevista_Lugar']), str(c['Entrevista_Campana']))
        grupos[clave].append(c)

    for clave, grupo in grupos.items():
        fecha_str_raw, hora_str_raw, lugar, campana = clave
        correos = [c['Email'] for c in grupo]

        # Parseo de hora/fecha
        hora_str = str(hora_str_raw)
        if len(hora_str.split(':')) >= 2:
            hora_str = ':'.join(hora_str.split(':')[:2])
        
        fecha_str = f"{fecha_str_raw} {hora_str}"
        try:
            fecha_inicio = datetime.strptime(fecha_str, "%Y-%m-%d %H:%M")
            fecha_formateada = fecha_inicio.strftime("%d/%m/%Y")
        except ValueError as e:
            logger.error(f"Error parseando fecha/hora para el grupo {clave}: {e}")
            continue
        
        ubicacion_es_virtual = "meet.google.com" in lugar or "http" in lugar
        texto_lugar = f"A traves de Google Meet: <a href='{lugar}'>{lugar}</a><br>" if ubicacion_es_virtual else f"Dirección: {lugar}<br><br>👉 Deberás traer impresa una copia de tu CV + DNI frente y dorso.<br>"

        asunto_principal = "Entrevista Laboral - Acme Solutions S.A. / LXJ Recursos Compartidos S.A"
        
        # --- A. Enviar BCC Masivo ---
        mensaje_html_principal = f"""
        Hola, buenos días 😊<br>
        Espero que estés muy bien.<br>
        <br>
        Nos comunicamos desde el área de Recursos Humanos de el grupo empresario conformado por Acme Solutions S.A. Y LxJ Recursos Compartidos S.A. Recibimos tu postulación para el puesto de Operador Telefónico en nuestro contact center y, luego de revisar tu perfil, queremos invitarte a una entrevista grupal.<br>
        <br>
        📅 <b>Día: {fecha_formateada}</b><br>
        ⏰ <b>Hora: {hora_str} hs</b><br>
        📍 <b>{texto_lugar}</b>
        <br>
        Te pedimos que, por favor, nos confirmes tu asistencia por esta via.<br>
        <br>
        ⚠️ En caso de necesitar reprogramar la entrevista, te solicitamos que lo informes vía WhatsApp al 1100000000 <br>
        <br>
        ¡Muchas gracias!<br>
        <br>
        Saludos.<br>
        Equipo de Reclutamiento & Selección<br>
        Acme Solutions
        """
        try:
            RRHH_mail_manager.enviar_correo(
                destinatarios=[], bcc=correos, asunto=asunto_principal, 
                mensaje=mensaje_html_principal, nombre_remitente_override='Acme Solutions'
            )
            logger.info(f"Correo grupal enviado a {len(correos)} candidatos para campaña {campana}.")
        except Exception as e:
            logger.error(f"Error enviando correo masivo BCC: {e}")
            continue # Si falla, aborta este grupo y lo reintenta la próxima vez

        # --- B. Enviar ICS Individuales y Actualizar BD ---
        mensaje_html_ics = "Hola de nuevo 😊,<br><br>Adjuntamos la invitación formal para agendar la entrevista en tu calendario.<br><br>¡Te esperamos!<br><br>Saludos."
        asunto_respuesta = f"RE: {asunto_principal}"
        for c in grupo:
            dni, correo = c['DNI'], c['Email']
            try:
                # ics_data = RRHH_mail_manager.crear_invitacion_ics(
                #     asunto="Entrevista Acme Solutions", descripcion=f"Campaña: {campana}",
                #     fecha_inicio=fecha_inicio, email_invitado=correo
                # )
                # RRHH_mail_manager.enviar_correo(
                #     destinatarios=[correo], asunto=asunto_respuesta, 
                #     mensaje=mensaje_html_ics, contenido_ics=ics_data
                # )
                
                # Actualizar DB individualmente
                with engine.begin() as conn:
                    conn.execute(text("UPDATE rrhh.Candidatos SET Mail_Enviado = 1 WHERE DNI = :dni"), {"dni": dni})
            except Exception as e:
                logger.error(f"Error enviando ICS al DNI {dni}: {e}")


# ==========================================
# 3. FUNCIÓN DE SINCRONIZACIÓN (Google Sheets)
# ==========================================
def formatear_fecha_ddmmaaaa(fecha_val) -> str:
    """Convierte fechas (date, datetime o string) a formato DD/MM/AAAA"""
    if not fecha_val:
        return ""
    try:
        # Si ya es un objeto datetime o date
        if hasattr(fecha_val, 'strftime'):
            return fecha_val.strftime("%d/%m/%Y")
        # Si es un string, lo parseamos
        if isinstance(fecha_val, str):
            # Asumimos que viene de SQL (YYYY-MM-DD)
            dt = pd.to_datetime(fecha_val)
            return dt.strftime("%d/%m/%Y")
    except Exception as e:
        logger.warning(f"No se pudo formatear la fecha {fecha_val}: {e}")
        pass
    return str(fecha_val)


# ==========================================
# 3. FUNCIÓN DE SINCRONIZACIÓN (Google Sheets)
# ==========================================
def _procesar_sincronizacion_sheets(candidatos_pendientes: list):
    """
    Formatea los datos, los separa por sede, los sube a las hojas 
    correspondientes de Google Sheets en bloque y actualiza la BD con IN clause.
    """
    sede_norte = []
    sede_sur = []
    
    for c in candidatos_pendientes:
        modalidad = "Virtual" if "meet" in str(c.get('Entrevista_Lugar', '')).lower() else "Presencial"
        
        # Aplicamos el formato de fechas
        fecha_entrevista = formatear_fecha_ddmmaaaa(c.get('Entrevista_Fecha'))
        fecha_postulacion = formatear_fecha_ddmmaaaa(c.get('FechaSubida'))
        
        fila = {
            'Fecha': fecha_entrevista,
            'Hora': c.get('Entrevista_Hora', ''),
            'Modalidad': modalidad,
            'Razon Social': '',
            'Campaña': c.get('Entrevista_Campana', ''),
            'Apellido/s': c.get('Apellido', ''),
            'Nombre/s': c.get('Nombre', ''),
            'Correo Electrónico': c.get('Correo_Candidato', c.get('Email', '')), # Fallback por seguridad
            'Celular': c.get('Telefono', ''),
            'Postulación': 'IA',
            'Cita enviada': 'Sí',
            'Confirmación': '', 
            'Presentismo': '', 
            'Comentarios': c.get('Comentarios', '')
        }
        
        sede = str(c.get('Sede', '')).upper()
        if 'SEDE NORTE' in sede:
            sede_norte.append(fila)
        elif 'SUR' in sede:
            sede_sur.append(fila)
        else:
            # Si un candidato no tiene sede, podemos mandarlo a Sede Norte por defecto 
            # o dejar un warning. Aquí lo mandamos a Sede Norte por defecto para no perderlo.
            logger.warning(f"DNI {c.get('DNI')} no tiene Sede válida ('{sede}'). Mandando a Sede Norte.")
            sede_norte.append(fila)

    try:
        # 2. Subir a Google Sheets EN BLOQUE (Una sola llamada a la API por pestaña)
        if sede_norte:
            df_sede_norte = pd.DataFrame(sede_norte)
            # Asegúrate de que settings.SHEET_ID_INGRESOS exista en app/config.py
            dataframe_a_sheet(settings.SHEET_ID_INGRESOS, df_sede_norte, 'SEDE NORTE')
            logger.info(f"Subidos {len(sede_norte)} candidatos a pestaña SEDE NORTE.")
        
        if sede_sur:
            df_sede_sur = pd.DataFrame(sede_sur)
            dataframe_a_sheet(settings.SHEET_ID_INGRESOS, df_sede_sur, 'SEDE SUR')
            logger.info(f"Subidos {len(sede_sur)} candidatos a pestaña SEDE SUR.")

        # 3. Marcar como sincronizados en SQL en bloque
        dnis_actualizados = [c['DNI'] for c in candidatos_pendientes]
        
        if dnis_actualizados:
            update_query = text("""
                UPDATE rrhh.Candidatos 
                SET Sincronizado_Ingresos = 1 
                WHERE DNI IN :dnis
            """)
            
            # Truco para pasar listas en SQLAlchemy
            update_query = update_query.bindparams(bindparam('dnis', expanding=True))
            
            with engine.begin() as conn:
                conn.execute(update_query, {"dnis": dnis_actualizados})
                
            logger.info(f"Lote finalizado. Se marcaron {len(dnis_actualizados)} candidatos como sincronizados en BD.")

    except Exception as e:
        logger.error(f"Error en la sincronización masiva a Google Sheets: {e}")


# ==========================================
# 4. FUNCIÓN ORQUESTADORA (Llamada por el Scheduler)
# ==========================================
def procesar_aceptados_15min():
    """
    Función maestra ejecutada cada 15 minutos. 
    Coordina la búsqueda, el envío de mails y la subida a sheets.
    """
    logger.info("Iniciando Scheduler: Procesando candidatos para Mails y Sheets...")
    try:
        # 1. Buscar candidatos
        candidatos = _obtener_candidatos_pendientes()

        if not candidatos:
            logger.info("No hay candidatos pendientes de procesar.")
            return

        # 2. Clasificar el trabajo a realizar
        pendientes_mail = [c for c in candidatos if not c.get('Mail_Enviado') and c.get('Email')]
        pendientes_sheet = [c for c in candidatos if not c.get('Sincronizado_Ingresos')]

        # 3. Ejecutar envío de mails
        if pendientes_mail:
            logger.info(f"Procesando mails para {len(pendientes_mail)} candidatos...")
            _procesar_envio_mails(pendientes_mail)

        # 4. Ejecutar sincronización de Google Sheets
        if pendientes_sheet:
            logger.info(f"Procesando subida a Sheets para {len(pendientes_sheet)} candidatos...")
            _procesar_sincronizacion_sheets(pendientes_sheet)
            
        logger.info("Finalizó la tarea programada de RRHH exitosamente.")

    except Exception as e:
        logger.error(f"Error general en procesar_aceptados_15min: {e}")


class RRHH_SQL:
    def __init__(self):
        """Inicializa la clase usando SQL Server como Base de Datos Principal."""
        logger.info("Inicializando instancia RRHH (Modo SQL Server DB)...")

    def obtener_candidatos_activos(self) -> List[Dict[str, Any]]:
        """
        Lee los candidatos activos (PENDIENTES) directamente de SQL.
        Es instantáneo y no consume cuota de la API de Google.
        """
        query = text("""SELECT * FROM rrhh.Candidatos 
        WHERE (Estado = 'PENDIENTE' OR Estado IS NULL) and FechaSubida >= DATEADD(DAY, -30, GETDATE())
        order by FechaSubida DESC
        """)
        try:
            with engine.connect() as conn:
                result = conn.execute(query).mappings().all()
                return [dict(row) for row in result]
        except Exception as e:
            logger.error(f"Error al obtener candidatos de SQL: {e}")
            return []
        
    

    def obtener_candidato_por_dni(self, dni: str) -> dict:
        """Busca y devuelve toda la información de un candidato por su DNI."""
        query = text("SELECT * FROM rrhh.Candidatos WHERE DNI = :dni")
        try:
            with engine.connect() as conn:
                result = conn.execute(query, {"dni": dni}).mappings().first()
                return dict(result) if result else {}
        except Exception as e:
            logger.error(f"Error al obtener candidato DNI {dni}: {e}")
            return {}

    def cambiar_estado_candidato(self, dni: str, nuevo_estado: str):
        """Actualiza la columna 'Estado' de un candidato en SQL."""
        logger.info(f"Cambiando estado de DNI {dni} a: {nuevo_estado} en SQL")
        query = text("UPDATE rrhh.Candidatos SET Estado = :estado WHERE DNI = :dni")
        try:
            with engine.begin() as conn:
                conn.execute(query, {"estado": nuevo_estado, "dni": dni})
            return True
        except Exception as e:
            logger.error(f"Error al cambiar estado en SQL para DNI {dni}: {e}")
            raise e


    def aceptar_candidato(self, dni: str, correo_candidato: str, datos_entrevista: dict):
        logger.info(f"Procesando aceptación en SQL para el DNI: {dni}")
        query = text("""
            UPDATE rrhh.Candidatos 
            SET Estado = 'ACEPTADO',
                Entrevista_Fecha = :fecha,
                Entrevista_Hora = :hora,
                Entrevista_Lugar = :lugar,
                Entrevista_Campana = :campana,
                Sincronizado_Ingresos = 0, 
                Mail_Enviado = 0 
            WHERE DNI = :dni
        """)
        try:
            with engine.begin() as conn:
                conn.execute(query, {
                    "fecha": datos_entrevista.get('fecha') if datos_entrevista.get('fecha') else None,
                    "hora": datos_entrevista.get('hora') if datos_entrevista.get('hora') else None,
                    "lugar": datos_entrevista.get('lugar', ''),
                    "campana": datos_entrevista.get('campana', ''),
                    "dni": dni
                })
            return True
        except Exception as e:
            logger.error(f"Error al procesar aceptación en SQL para DNI {dni}: {e}")
            raise e