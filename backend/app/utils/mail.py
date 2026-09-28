import smtplib
import imaplib
import email
import os
import uuid
import logging
import datetime
import unicodedata
import re
from email.header import decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.base import MIMEBase
from email import encoders
from email.utils import formataddr
from typing import List, Optional, Union, Dict
from icalendar import Calendar, Event, vText, vCalAddress
from datetime import timedelta

# Importamos settings solo para usarlos en la clase hija
from app.config import settings
from app.models import ImagenCorreo

logger = logging.getLogger(__name__)

# ==========================================
# CLASE PADRE: GENÉRICA (BaseMailManager)
# ==========================================
class BaseMailManager:
    """
    Clase genérica para manejo de correos. 
    No contiene lógica de negocio específica (ni RRHH, ni Ventas).
    Solo funcionalidad técnica.
    """
    def __init__(
        self, 
        email_user: str, 
        email_pass: str, 
        imap_server: str = settings.GMAIL_IMAP_SERVER,
        smtp_server: str = settings.GMAIL_SMTP_SERVER,
        smtp_port: int = 465,
        default_sender_name: str = "Sistema de Correos"
    ):
        self.email_user = email_user
        self.email_pass = email_pass
        self.imap_server = imap_server
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.default_sender_name = default_sender_name

    def _sanitize_filename(self, filename: str) -> str:
        """Utilidad técnica para limpiar nombres de archivo."""
        filename = unicodedata.normalize('NFKD', filename).encode('ascii', 'ignore').decode('ascii')
        filename = re.sub(r'[^\w\.-]', '_', filename)
        return filename

    def generar_ics_bytes(
        self,
        asunto: str,
        descripcion: str,
        fecha_inicio: datetime.datetime,
        email_invitado: str,
        duracion_minutos: int = 60,
        ubicacion: str = "",
        email_organizador: str = None,
        nombre_organizador: str = None
    ) -> bytes:
        """
        Genera los bytes de una invitación .ics estándar.
        """
        email_organizador = email_organizador or self.email_user
        nombre_organizador = nombre_organizador or self.default_sender_name

        cal = Calendar()
        cal.add('prodid', '-//Acme Solutions//Mail Manager//ES')
        cal.add('version', '2.0')
        cal.add('method', 'REQUEST')

        evento = Event()
        evento.add('summary', asunto)
        evento.add('description', descripcion)
        evento.add('dtstart', fecha_inicio)
        evento.add('dtend', fecha_inicio + timedelta(minutes=duracion_minutos))
        evento.add('dtstamp', datetime.datetime.now())
        evento.add('location', vText(ubicacion))
        # UID único para evitar colisiones en calendarios
        evento.add('uid', f'{uuid.uuid4()}@{self.email_user.split("@")[-1]}')
        
        organizador = vCalAddress(f'mailto:{email_organizador}')
        organizador.params['cn'] = vText(nombre_organizador)
        evento.add('organizer', organizador)
        
        asistente = vCalAddress(f'mailto:{email_invitado}')
        asistente.params['cn'] = vText('Invitado')
        asistente.params['ROLE'] = vText('REQ-PARTICIPANT')
        asistente.params['RSVP'] = vText('TRUE')
        evento.add('attendee', asistente, encode=0)

        evento.add('status', 'CONFIRMED')
        cal.add_component(evento)
        return cal.to_ical()

    def enviar_correo_base(
        self,
        destinatarios: List[str], 
        asunto: str, 
        mensaje_html: str,
        imagenes: List[ImagenCorreo] = [],
        archivos_adjuntos: List[str] = [],
        contenido_ics: Optional[bytes] = None,
        nombre_remitente_override: Optional[str] = None,
        archivos_adjuntos_memoria: List[Dict[str, Union[str, bytes]]] = [],
        bcc: Optional[List[str]] = None
    ):
        """Lógica pura de envío SMTP."""
        remitente_display = nombre_remitente_override or self.default_sender_name
        
        try:
            msg = MIMEMultipart()
            msg['From'] = formataddr((remitente_display, self.email_user))
            # Si se envía solo por BCC, evitamos que el "To" quede vacío para que el servidor SMTP no falle
            msg['To'] = ", ".join(destinatarios) if destinatarios else self.email_user 
            
            if bcc:
                msg['Bcc'] = ", ".join(bcc)
            msg['Subject'] = asunto

            # Contenido HTML con soporte para imágenes CID
            body_content = f'<html><body>{mensaje_html}'
            for i, img in enumerate(imagenes):
                body_content += f'<img src="cid:imagen_{i}" style="max-width: {img.ancho}; height: auto;"><br>\n'
            body_content += '</body></html>'
            
            msg.attach(MIMEText(body_content, 'html'))

            # Adjuntar ICS si existe
            if contenido_ics:
                part = MIMEBase('text', 'calendar', method='REQUEST', name='invite.ics')
                part.set_payload(contenido_ics)
                encoders.encode_base64(part)
                part.add_header('Content-Description', 'Invitación')
                part.add_header('Content-class', 'urn:content-classes:calendarmessage')
                part.add_header('Filename', 'invite.ics')
                part.add_header('Path', 'invite.ics')
                msg.attach(part)

            # Adjuntar Imágenes Inline
            for i, img in enumerate(imagenes):
                if os.path.exists(img.ruta):
                    with open(img.ruta, 'rb') as f:
                        mime_img = MIMEImage(f.read())
                        mime_img.add_header('Content-ID', f'<imagen_{i}>')
                        mime_img.add_header('Content-Disposition', 'inline', filename=os.path.basename(img.ruta))
                        msg.attach(mime_img)

            # Adjuntar Archivos
            for archivo in archivos_adjuntos:
                if archivo and os.path.exists(archivo):
                    with open(archivo, 'rb') as f:
                        adjunto = MIMEBase('application', 'octet-stream')
                        adjunto.set_payload(f.read())
                        encoders.encode_base64(adjunto)
                        adjunto.add_header('Content-Disposition', 'attachment', filename=os.path.basename(archivo))
                        msg.attach(adjunto)

            # Adjuntar Archivos desde memoria
            for archivo_mem in archivos_adjuntos_memoria:
                if archivo_mem and 'contenido' in archivo_mem and 'nombre' in archivo_mem:
                    contenido = archivo_mem['contenido']
                    nombre = archivo_mem['nombre']
                    # El tipo es opcional, default a octet-stream
                    tipo = archivo_mem.get('tipo', 'application/octet-stream')
                    maintype, subtype = str(tipo).split('/', 1)

                    adjunto = MIMEBase(maintype, subtype)
                    adjunto.set_payload(contenido) # type: ignore
                    encoders.encode_base64(adjunto)
                    adjunto.add_header('Content-Disposition', 'attachment', filename=str(nombre))
                    msg.attach(adjunto)


            with smtplib.SMTP_SSL(self.smtp_server, self.smtp_port) as server:
                server.login(self.email_user, self.email_pass)
                server.send_message(msg)
                
            logger.info(f"✅ Correo enviado a: {destinatarios} desde {self.email_user}")

        except Exception as e:
            logger.error(f"❌ Error crítico enviando correo: {str(e)}")

    def descargar_adjuntos_generico(
        self, 
        download_folder: str, 
        fecha_filtro: datetime.date,
        extensiones_permitidas: List[str],
        palabras_ignoradas: List[str]
    ) -> List[str]:
        """
        Lógica pura de IMAP para descargar adjuntos.
        """
        downloaded_files = []
        date_format = fecha_filtro.strftime("%d-%b-%Y")
        extensiones_permitidas = [e.lower() for e in extensiones_permitidas]

        try:
            mail = imaplib.IMAP4_SSL(self.imap_server)
            mail.login(self.email_user, self.email_pass)
            mail.select("inbox")

            status, messages = mail.search(None, f'(SINCE "{date_format}")')
            if status != "OK":
                return []

            email_ids = messages[0].split()
            
            for e_id in email_ids:
                try:
                    _, msg_data = mail.fetch(e_id, "(RFC822)")
                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1])
                            
                            # --- NUEVO FILTRO DE ASUNTO ---
                            subject = msg.get("Subject", "")
                            if subject:
                                decoded_subject_parts = decode_header(subject)
                                subject_str = ""
                                for part, encoding in decoded_subject_parts:
                                    if isinstance(part, bytes):
                                        try:
                                            subject_str += part.decode(encoding or 'utf-8', errors='ignore')
                                        except LookupError:
                                            subject_str += part.decode('utf-8', errors='ignore')
                                    else:
                                        subject_str += part
                                
                                # Si contiene "interno" (case-insensitive), saltamos este correo
                                if "interno" in subject_str.lower():
                                    logger.info(f"Correo ignorado por contener 'interno' en el asunto: {subject_str}")
                                    continue
                            # Validación estricta de fecha
                            email_date_str = msg.get("Date")
                            if email_date_str:
                                email_dt = email.utils.parsedate_to_datetime(email_date_str)
                                if email_dt.date() != fecha_filtro:
                                    continue

                            for part in msg.walk():
                                if part.get_content_maintype() == "multipart" or part.get("Content-Disposition") is None:
                                    continue

                                filename = part.get_filename()
                                if filename:
                                    decoded_filename = decode_header(filename)[0][0]
                                    if isinstance(decoded_filename, bytes):
                                        decoded_filename = decoded_filename.decode()
                                    filename = decoded_filename
                                    
                                    # Filtro de palabras prohibidas
                                    if any(ign.lower() in filename.lower() for ign in palabras_ignoradas):
                                        logger.info(f"Archivo ignorado por filtro: {filename}")
                                        continue

                                    ext = os.path.splitext(filename)[1].lower()
                                    if ext in extensiones_permitidas:
                                        safe_filename = self._sanitize_filename(filename)
                                        prefix = fecha_filtro.strftime("%Y-%m-%d")
                                        filepath = os.path.join(download_folder, f"{prefix}_{safe_filename}")
                                        
                                        with open(filepath, "wb") as f:
                                            f.write(part.get_payload(decode=True))
                                        downloaded_files.append(filepath)

                except Exception as e:
                    logger.error(f"Error parseando email {e_id}: {e}")
                    continue

            mail.close()
            mail.logout()

        except Exception as e:
            logger.error(f"Error IMAP: {e}")

        return downloaded_files


# ==========================================
# CLASE HIJA: RRHH (RRHHMailManager)
# ==========================================
class RRHHMailManager(BaseMailManager):
    """
    Clase específica para Recursos Humanos.
    - Carga credenciales desde settings.RRHH_...
    - Define reglas de negocio como 'blacklist de cuil'.
    - Adapta métodos para entrevistas.
    """
    def __init__(self):
        # Inicializa la clase padre con las credenciales ESPECÍFICAS de RRHH
        super().__init__(
            email_user=settings.RRHH_MAIL,
            email_pass=settings.RRHH_MAIL_PASS,
            default_sender_name="Acme Solutions" # O "Acme Solutions RRHH"
        )
        # Definición de reglas de negocio propias de RRHH
        self.blacklist_archivos = ["cuil"]
        self.extensiones_validas = ['.csv', '.pdf', '.docx', '.doc', '.jpg', '.jpeg', '.png', '.webp']

    def crear_invitacion_ics(
        self, 
        asunto: str, 
        descripcion: str, 
        fecha_inicio: datetime.datetime, 
        email_invitado: str
    ) -> bytes:
        """
        Wrapper específico para entrevistas de RRHH.
        Usa el generador del padre pero inyecta los defaults de RRHH.
        """
        return self.generar_ics_bytes(
            asunto=asunto,
            descripcion=descripcion,
            fecha_inicio=fecha_inicio,
            email_invitado=email_invitado,
            nombre_organizador="Acme Solutions RRHH", # Nombre específico para invites
            duracion_minutos=60 # Default para entrevistas
        )

    def enviar_correo(self, destinatarios, asunto, mensaje, bcc=None, **kwargs):
        """
        Wrapper simple para mantener el nombre del método que usabas antes.
        """
        # Envuelve el mensaje en un párrafo con estilo como le gusta a RRHH
        mensaje_formateado = f'<p style="font-size: 15px;">{mensaje}</p>'
        
        self.enviar_correo_base(
            destinatarios=destinatarios,
            asunto=asunto,
            mensaje_html=mensaje_formateado,
            bcc=bcc, # <-- SE PASA EL PARÁMETRO
            **kwargs
        )

    def descargar_adjuntos_ayer(self, download_folder: str) -> List[str]:
        """
        Implementación de regla de negocio: "Bajar todo lo de ayer que no sea CUIL".
        """
        ayer = datetime.date.today() - datetime.timedelta(days=1)
        
        logger.info("Iniciando descarga de adjuntos RRHH (Regla: Ayer, Sin CUIL)...")
        
        return self.descargar_adjuntos_generico(
            download_folder=download_folder,
            fecha_filtro=ayer,
            extensiones_permitidas=self.extensiones_validas,
            palabras_ignoradas=self.blacklist_archivos
        )

# ==========================================
# INSTANCIA GLOBAL (RETROCOMPATIBILIDAD)
# ==========================================
# Esto asegura que el resto de tu código que importa 'RRHH_mail_manager'
# siga funcionando usando la lógica de RRHH por defecto.
RRHH_mail_manager = RRHHMailManager()

Envios_mail_manager = BaseMailManager(
    email_user=settings.ENVIOS_OPERACIONES_MAIL,
    email_pass=settings.ENVIOS_OPERACIONES_PASS,
    default_sender_name="Envios Operaciones"
)

Calidad1_mail_manager = BaseMailManager(
    email_user=settings.CALIDAD1_MAIL,
    email_pass=settings.CALIDAD1_PASS,
    default_sender_name="Calidad Acme"
)