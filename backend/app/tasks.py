from concurrent.futures import ThreadPoolExecutor
import datetime
import logging
from typing import Any, Dict, List, Optional
from app.services import Auditor
from app.config import settings
from app.utils.google_sheet import dataframe_a_sheet, sheet_a_dataframe, preparar_export
import pandas as pd
from app.database import engine
from app.utils.mail import Envios_mail_manager
import json
import io
import traceback
import pandas as pd
from sqlalchemy import text, bindparam
import os
import shutil
from app.utils.tipificaciones import preparar_tipificacion_para_consulta
from AuditorIA.execution_log import obtener_contexto, estimar_costo_usd, armar_detalle_html, formatear_duracion
from AuditorIA.modelos_ia import MODELO_IA_DEFAULT
from AuditorIA import transcripcion_cola
from AuditorIA import batch_cola
from AuditorIA.avisos import AvisoUsuarioError
from app.managers import plantillas_manager_instance
from fastapi import HTTPException
from app.rbac import exigir_acceso_empresa
from app.scheduler_logging import contexto as contexto_job
from app.security import get_user

logger = logging.getLogger(__name__)


def _scheduler_fuera_de_alcance(tarea) -> bool:
    """True si el creador del scheduler ya no tiene alcance (permisos
    template:<empresa>) sobre la empresa/campaña/plantilla programada. Los
    permisos se revalidan en cada corrida: los roles del creador pueden haber
    cambiado desde que programó la tarea. Ante errores de infraestructura no
    bloquea la corrida (solo bloquea un 403/404 concreto)."""
    try:
        creador = get_user(int(tarea['created_by']))
        if creador is None:
            logger.warning(f"Scheduler {tarea['id']}: creador {tarea['created_by']} inexistente.")
            return True
        empresa = str(tarea.get('empresa') or '')
        campana = str(tarea.get('campana') or '')
        with engine.connect() as conn:
            exigir_acceso_empresa(
                conn, creador,
                empresa_id=int(empresa) if empresa.isdigit() else None,
                campana_id=int(campana) if campana.isdigit() else None,
                plantilla_id=tarea['plantilla_id'],
            )
        return False
    except HTTPException:
        return True
    except Exception as e:
        logger.error(f"No se pudo revalidar el alcance del scheduler {tarea.get('id')}: {e}")
        return False

def procesar_cola_transcripciones():
    """Tick de la cola de transcripciones a demanda (calidad.TranscripcionJobs).

    Recoge los lotes de Gemini que ya terminaron y manda los pedidos nuevos que el
    usuario encoló desde "Auditorías Realizadas". Va por su propia tabla y su propio
    tick, aparte de check_completed_batches: esos lotes son AUDITORÍAS (tienen
    Batch_data, plantilla, campaña y un AuditExecutionLog que cerrar) y procesar_batch
    daría por huérfano cualquier lote que no traiga esa metadata."""
    if Auditor is None:
        return
    transcripcion_cola.procesar_cola(engine, Auditor.gemini)


def marcar_transcripciones_huerfanas():
    """Al arrancar el scheduler: los pedidos que quedaron a mitad de envío por un
    proceso caído vuelven a la cola (si no, quedan colgados para siempre)."""
    transcripcion_cola.marcar_huerfanos(engine)


def despachar_cola_lotes():
    """Tick de la cola de lotes de auditoría (calidad.BatchPendientes).

    Manda a Gemini los lotes que se quedaron esperando cupo (100 jobs en vuelo como
    máximo en toda la cuenta). Va aparte de check_completed_batches porque no mira
    resultados: solo empuja hacia afuera lo que ya está armado en disco."""
    if Auditor is None:
        return
    enviados = batch_cola.despachar_pendientes(engine, Auditor.gemini)
    if enviados:
        logger.info("Cola de lotes: %d lote(s) salieron a Gemini en este tick.", enviados)
    # Un lote que se serializó pero no llegó a insertar su fila deja el .jsonl tirado
    # (megas de audio). Se barre acá, con margen de 24 h para no pisar uno en curso.
    batch_cola.limpiar_huerfanos_en_disco(engine)


def marcar_lotes_huerfanos():
    """Al arrancar el scheduler: los lotes que quedaron a mitad de envío por un proceso
    caído vuelven a la cola (si no, se quedan en ENVIANDO y no los toma nadie)."""
    batch_cola.marcar_huerfanos(engine)


def check_completed_batches():
    # DEBUG y no INFO: el tick corre cada 15 minutos y la enorme mayoría de las
    # veces no hay ningún batch para mirar. Lo que importa (batch terminado, o
    # fallado) lo loguean check_batch_status y procesar_batch.
    logger.debug("Scheduler: Verificando trabajos de auditoría completados...")
    if Auditor is not None:
        batch_completos = Auditor.check_batch_status()
        if batch_completos:
            Auditor.procesar_batch(batch_completos)


# Una auditoría interactiva puede tardar bastante (descarga + transcripción + IA), pero
# nunca 12 horas. Pasado ese umbral, la tarea no está lenta: está muerta.
HORAS_AUDITORIA_HUERFANA = 12


def cerrar_auditorias_huerfanas():
    """Cierra las auditorías 'pending' que quedaron colgadas.

    Las auditorías interactivas corren con BackgroundTasks dentro del proceso de la API: si
    la API se reinicia con una en curso, el hilo muere pero la fila de AuditTasks queda en
    'pending' para siempre y el frontend la muestra girando sin fin.

    No toca los 'en_cola' (modo batch): esos viven en Gemini, sobreviven a los reinicios a
    propósito y los cierra check_batch_status/procesar_batch cuando terminan.

    Solo las de ESTE entorno: la que quedó colgada es la de la API de este servidor."""
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                UPDATE calidad.AuditTasks
                SET status = 'failed',
                    error_message = 'La auditoría se interrumpió porque el servidor se reinició mientras corría.',
                    updated_at = GETDATE()
                WHERE status = 'pending'
                  AND Entorno = :env
                  AND updated_at < DATEADD(HOUR, :horas, GETDATE())
            """), {"horas": -HORAS_AUDITORIA_HUERFANA, "env": settings.ENVIRONMENT})
            conn.commit()
        if result.rowcount:
            logger.warning(f"{result.rowcount} auditorías huérfanas marcadas como failed.")
    except Exception:
        logger.exception("No se pudieron cerrar las auditorías huérfanas.")

def auditoria_ALARMIX_diaria():
    """
    Tarea programada para auditar el día anterior y guardar el acumulativo en Google Sheets
    utilizando el mismo formato e información que la pantalla 'Mis Auditorías'.
    """
    logger.info("Scheduler: Iniciando auditoría diaria acumulativa ALARMIX...")
    if Auditor is None:
        logger.error("Servicio Auditor no disponible. Cancelando tarea.")
        return

    # Calcular la fecha del día anterior
    ayer = datetime.date.today() - datetime.timedelta(days=1)
    hoy = datetime.date.today()
    # Parámetros para procesar la auditoría
    audit_params = {
        "plantilla_id": 23,
        "user_id": "10000001",
        "cantidad": 50,
        "duracion_min": 10,
        "duracion_max": 180,
        "idInteraccion": None,
        "Segmento": None,
        "Fecha_desde": datetime.datetime.combine(ayer, datetime.datetime.min.time()),
        "Fecha_hasta": datetime.datetime.combine(ayer, datetime.datetime.min.time()),
        "loginid": None,
        "empresa": "12",
        "campana": "21",
        "tipificacion": None,
        "sentido": ['Entrante', 'Saliente'],
        "calidad": True,
        "transcribir": False,
        "por_operador": False,
        "por_tipificacion": False,
        "reauditar": False,
        "custom_audio_paths": [],
        "ucids": None,
        "saved_files": None
    }

    try:
        logger.info(f"Paso 1: Ejecutando auditoría automática para la fecha: {ayer}")
        # 1. Ejecutar la auditoría (esto procesa los audios y los guarda en la base de datos)
        Auditor.run(**audit_params)
        
        logger.info("Paso 2: Obteniendo resultados desde sp_ObtenerAuditoriasFiltradas...")
        # 2. Consultar la base de datos usando el mismo Stored Procedure que el endpoint
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

        # Parámetros exactos para el Stored Procedure
        db_params = {
            "usuario": "10000001",
            "campana": 21,
            "empresa": 12,
            "plantilla": 23,    
            "fecha_desde": hoy,  # Para obtener solo las auditorías del día anterior, usamos 'hoy' como fecha de inicio 
            "fecha_hasta": hoy,
            "id_aplicativo": None,
            "incluir_transcripcion": True, 
            "response_thoughts": True
        }

        with engine.connect() as conn:
            result = conn.execute(query, db_params)
            rows = result.mappings().all() # Extraemos las filas con formato diccionario

        # 3. Convertir el resultado de la Base de Datos a un DataFrame de Pandas
        df_resultado = pd.DataFrame(rows)
        
        df_resultado = df_resultado[['AuditoriaID', 'IdAplicativo', 'operadorUsuario', 'sentido_interaccion', 'fecha_interaccion', 'FechaAuditoria', 'extras', 'corte_abrupto', 'origen_del_corte', 'criticidad_del_corte', 'motivo_fin_llamada', 'aviso_retorno_llamada', 'contexto_previo_al_corte', 'transcripcion_final']]
        
        # 4. Subir a Google Sheets
        if not df_resultado.empty:
            # ⚠️ IMPORTANTE: Coloca el ID de tu Google Sheet
            sheet_id = "ID_DE_GOOGLE_A_CONFIGURAR" 
            nombre_hoja = "Dataset_Acumulativo" 
            
            resultado_gs = dataframe_a_sheet(
                sheet_id=sheet_id,
                df=df_resultado,
                nombre_hoja=nombre_hoja
            )
            logger.info(f"Auditoría diaria guardada en Google Sheets con éxito: {resultado_gs}")
        else:
            logger.warning("La auditoría finalizó pero el Stored Procedure no devolvió resultados para subir.")
            
    except Exception as e:
        logger.error(f"Error en la auditoría diaria acumulativa ALARMIX: {e}", exc_info=True)


def _enviar_mail_log_admin_sync(
    scheduler_id: int, task_name: str, empresa, campana, plantilla_id,
    fecha_desde, fecha_hasta, cantidad_solicitada: int, filas_auditadas: int,
) -> None:
    """Manda el detalle de una corrida sync de scheduler (modo, origen, duración,
    errores, tokens, costo estimado) SOLO a settings.ADMIN_EMAIL. Nunca a los
    destinatarios configurados en la tarea: esa info es interna, no el resultado
    de la auditoría (ese va en el mail normal, sin este detalle)."""
    try:
        if not settings.ADMIN_EMAIL:
            return
        contexto = obtener_contexto(engine, scheduler_id=scheduler_id) or {}
        tokens_corrida = {
            "input_tokens": contexto.get("input_tokens", 0),
            "output_tokens": contexto.get("output_tokens", 0),
            "thoughts_tokens": contexto.get("thoughts_tokens", 0),
        }
        # Modelo REAL de la corrida: Auditor.run() lo guardó al cerrar la fila del
        # log (columna `modelo`). Si no está (fila vieja, migración sin aplicar),
        # caemos al modelo configurado HOY en la plantilla, como antes.
        modelo_corrida = contexto.get("modelo")
        if not modelo_corrida:
            modelo_corrida = MODELO_IA_DEFAULT
            try:
                if plantillas_manager_instance:
                    datos_plantilla = plantillas_manager_instance.obtener_plantilla(int(plantilla_id))
                    if datos_plantilla:
                        modelo_corrida = datos_plantilla.get("modelo_ia") or MODELO_IA_DEFAULT
            except Exception as e:
                logger.warning(f"No se pudo resolver el modelo de la plantilla {plantilla_id} para el costo estimado: {e}")
        costo_usd = estimar_costo_usd(engine, modelo=modelo_corrida, modo="sync", tokens=tokens_corrida)
        detalle_html = armar_detalle_html(
            modo="sync", origen_txt=f'Tarea programada "{task_name}"',
            empresa=empresa, campana=campana, plantilla_id=plantilla_id,
            fecha_desde=fecha_desde, fecha_hasta=fecha_hasta, cantidad_solicitada=cantidad_solicitada,
            filas_auditadas=filas_auditadas, filas_error=contexto.get("filas_error", 0),
            tokens=tokens_corrida,
            duracion_txt=formatear_duracion(contexto.get("started_at"), contexto.get("finished_at")),
            costo_usd=costo_usd,
            nivel_razonamiento=contexto.get("nivel_razonamiento"),
        )
        Envios_mail_manager.enviar_correo_base(
            destinatarios=[settings.ADMIN_EMAIL],
            asunto=f"[Log Auditoría] Scheduler - {task_name}",
            mensaje_html=detalle_html,
            archivos_adjuntos_memoria=[],
        )
    except Exception as e:
        logger.error(f"No se pudo enviar el mail de log a ADMIN_EMAIL para el scheduler {task_name}: {e}")


# --- Auditoría diaria de audios CSV subidos al fileserver ---
# Cada noche una persona sube al fileserver una o más carpetas (una por "descarga"),
# cada una con sus audios .wav + 1 .csv (ucid) + 1 .txt (savedFiles). Esta tarea las
# manda a auditar en MODO BATCH bajo empresa 10 / campaña 19 / plantilla 12 y borra
# cada carpeta ya encolada.
#
# Los audios llegan por DOS ORÍGENES separados ("Audios generales" y "Audios VIP"), cada
# uno montado en su propia carpeta y con su propio Google Sheet de destino. Lo único que
# cambia entre uno y otro es la hoja: misma empresa, misma campaña, misma plantilla de
# auditoría y mismas columnas de export. Si una descarga cae en el mount equivocado, se
# audita igual pero termina en el Sheet del otro equipo, así que el origen se registra en
# el log y en el scheduler_name de cada corrida para poder distinguirlas después.
#
# Corre a las 04:00 y a nadie le urge el resultado, así que no tiene sentido pagar el
# precio del modo sincrónico: el batch de Gemini sale a mitad de precio a cambio de
# devolver el resultado horas después. Por eso acá NO se guarda ni se exporta nada:
# check_completed_batches (cada 15 min) detecta el lote terminado y Auditor.procesar_batch
# guarda en SQL, aplica la plantilla de columnas y sube a Google Sheets. El id de la hoja
# y la plantilla de columnas viajan con el lote (calidad.Batch_data) hasta ese momento.
CSV_AUDIOS_DIR = "/mnt/fileserver_audios_CSV"
CSV_AUDIOS_VIP_DIR = "/mnt/fileserver_audios_CSV_VIP"
CSV_AUDIT_SHEET_ID = "ID_DE_GOOGLE_A_CONFIGURAR_GENERAL"
CSV_AUDIT_SHEET_ID_VIP = "ID_DE_GOOGLE_A_CONFIGURAR_VIP"
CSV_AUDIT_SHEET_HOJA = "Hoja 1"
CSV_AUDIT_EMPRESA = "10"
CSV_AUDIT_CAMPANA = "19"
CSV_AUDIT_PLANTILLA_ID = 12
CSV_AUDIT_USER_ID = "10000001"
CSV_AUDIT_COLUMN_TEMPLATE_ID = 4


def _origenes_csv() -> List[Dict[str, str]]:
    """Los orígenes de audios CSV: de dónde se leen las descargas y a qué Sheet van.

    Se arma en cada corrida en vez de ser una constante de módulo para que las rutas
    sigan siendo reemplazables (los tests apuntan los mounts a carpetas temporales).
    """
    return [
        {
            "nombre": "Audios generales",
            "dir": CSV_AUDIOS_DIR,
            "gsheet_id": CSV_AUDIT_SHEET_ID,
        },
        {
            "nombre": "Audios VIP",
            "dir": CSV_AUDIOS_VIP_DIR,
            "gsheet_id": CSV_AUDIT_SHEET_ID_VIP,
        },
    ]


def _avisar_carpetas_csv_pendientes(problemas: List[Dict[str, Any]]) -> None:
    """Avisa a ADMIN_EMAIL qué carpetas de audios CSV quedaron sin auditar y por qué.

    Una carpeta que falla NO se borra (ver auditoria_csv_diaria: borrarla perdería los
    audios sin auditar), así que se reintenta cada madrugada y vuelve a fallar. Hasta
    ahora eso solo quedaba en el log del scheduler: una tanda con el informe mal
    exportado podía estar semanas ahí sin que nadie se enterara, y del lado de Calidad
    solo se veía que "faltaban auditorías".

    Sale un solo mail por corrida y únicamente si hubo algo que reportar: si todas las
    carpetas se encolaron bien, no llega nada.
    """
    if not problemas or not settings.ADMIN_EMAIL:
        return
    try:
        filas = "".join(
            f"<tr>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;'>{p['origen']}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;'>{p['carpeta']}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;text-align:right;'>{p['audios']}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;text-align:right;'>{p['espera']}</td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;'>{p['motivo']}</td>"
            f"</tr>"
            for p in problemas
        )
        total_audios = sum(p['audios'] for p in problemas)
        ubicaciones = "".join(
            f"<li><b>{o['nombre']}</b>: <code>{o['dir']}</code></li>" for o in _origenes_csv()
        )
        mensaje_html = f"""
        <p>La auditoría diaria de audios CSV dejó <b>{len(problemas)} carpeta(s) sin auditar</b>
        ({total_audios} audio(s) en total).</p>
        <p>Esas carpetas <b>no se borran</b> y se reintentan solas en la corrida de mañana:
        alcanza con corregir lo que dice el motivo y dejarlas donde están.</p>
        <ul style='font-family:Arial,sans-serif;font-size:13px;'>{ubicaciones}</ul>
        <table style='border-collapse:collapse;font-family:Arial,sans-serif;font-size:13px;'>
            <tr style='background:#f2f2f2;'>
                <th style='padding:6px 10px;border:1px solid #ddd;text-align:left;'>Origen</th>
                <th style='padding:6px 10px;border:1px solid #ddd;text-align:left;'>Carpeta</th>
                <th style='padding:6px 10px;border:1px solid #ddd;'>Audios</th>
                <th style='padding:6px 10px;border:1px solid #ddd;'>Días esperando</th>
                <th style='padding:6px 10px;border:1px solid #ddd;text-align:left;'>Motivo</th>
            </tr>
            {filas}
        </table>
        """
        Envios_mail_manager.enviar_correo_base(
            destinatarios=[settings.ADMIN_EMAIL],
            asunto=f"[Auditoría CSV] {len(problemas)} carpeta(s) sin auditar",
            mensaje_html=mensaje_html,
            archivos_adjuntos_memoria=[],
        )
    except Exception as e:
        # El aviso no puede tumbar la tarea: lo que importa ya se encoló.
        logger.error(f"No se pudo enviar el aviso de carpetas CSV pendientes: {e}")


def _dias_esperando(carpeta: str) -> int:
    """Hace cuántos días que la carpeta está en el fileserver (por su fecha de
    modificación). Sirve para distinguir de un vistazo la tanda de anoche de una
    carpeta que viene fallando hace dos semanas."""
    try:
        subida = datetime.datetime.fromtimestamp(os.path.getmtime(carpeta))
        return max((datetime.datetime.now() - subida).days, 0)
    except OSError:
        return 0


def _encolar_carpeta_csv(carpeta: str, origen: Dict[str, str], problemas: List[Dict[str, Any]]) -> bool:
    """Encola una carpeta ("descarga") como lote de Gemini, contra el Sheet de su origen.

    Devuelve True solo si quedó algo encolado. Cualquier salida sin encolar deja la
    carpeta intacta (los audios solo existen ahí) y suma una fila al aviso por mail.
    """
    nombre_carpeta = os.path.basename(carpeta)
    # Se inicializa acá para poder informar cuántos audios quedaron sin auditar
    # aunque la carpeta falle antes de listarlos.
    audio_paths: List[str] = []
    try:
        archivos = os.listdir(carpeta)
        csv_files = [f for f in archivos if f.lower().endswith('.csv')]
        txt_files = [f for f in archivos if f.lower().endswith('.txt')]
        audio_paths = [
            os.path.join(carpeta, f) for f in archivos
            if not f.lower().endswith(('.csv', '.txt'))
        ]

        # Solo procesamos carpetas completas. Si está a medio subir, la dejamos
        # intacta (sin borrar) para auditarla recién cuando esté completa.
        if len(csv_files) != 1 or len(txt_files) != 1 or not audio_paths:
            motivo = (
                f"La carpeta tiene que traer exactamente 1 CSV de UCIDs, 1 .txt de savedFiles "
                f"y los audios. Trae: {len(csv_files)} CSV, {len(txt_files)} .txt y "
                f"{len(audio_paths)} audio(s)."
            )
            logger.warning(
                f"[{origen['nombre']}] Carpeta '{nombre_carpeta}' incompleta o ambigua. "
                f"{motivo} Se omite y NO se borra."
            )
            problemas.append({
                "origen": origen["nombre"], "carpeta": nombre_carpeta, "audios": len(audio_paths),
                "espera": _dias_esperando(carpeta), "motivo": motivo,
            })
            return False

        ucid_path = os.path.join(carpeta, csv_files[0])
        saved_files_path = os.path.join(carpeta, txt_files[0])

        logger.info(
            f"[{origen['nombre']}] Enviando a batch la carpeta '{nombre_carpeta}' "
            f"({len(audio_paths)} audios)..."
        )

        audit_params = {
            "plantilla_id": CSV_AUDIT_PLANTILLA_ID,
            "user_id": CSV_AUDIT_USER_ID,
            "cantidad": len(audio_paths),
            "empresa": CSV_AUDIT_EMPRESA,
            "campana": CSV_AUDIT_CAMPANA,
            "custom_audio_paths": audio_paths,
            "ucids": ucid_path,
            "saved_files": saved_files_path,
            "calidad": True,
            "transcribir": False,
            "reauditar": False,
            # Para que la corrida quede atribuida al scheduler en calidad.AuditExecutionLog
            # y no como "manual" (el default de run_batch). El origen va en el nombre para
            # poder separar generales de VIP en el listado de corridas de /uso-ia.
            "trigger_source": "scheduler",
            "scheduler_name": f"Auditoría diaria CSV - {origen['nombre']}",
        }

        # El destino del export viaja con el lote (calidad.Batch_data) y lo usa
        # Auditor.procesar_batch cuando Gemini devuelve el resultado. Cada origen
        # tiene su propia hoja: generales y VIP no se mezclan.
        batches = Auditor.run_batch(
            **audit_params,
            gsheet_id=origen["gsheet_id"],
            gsheet_name=CSV_AUDIT_SHEET_HOJA,
            column_template_id=CSV_AUDIT_COLUMN_TEMPLATE_ID,
        )

        if batches is None:
            # Quedó algún audio sin destino: ni salió a Gemini ni entró en la cola de
            # lotes. La carpeta NO se toca, se reintenta en la corrida siguiente (si se
            # borrara, esos audios se perderían sin haberse auditado nunca).
            logger.error(
                f"[{origen['nombre']}] No se pudo encolar el batch de la carpeta '{nombre_carpeta}'. "
                f"Se conserva para reintentar en la próxima corrida."
            )
            problemas.append({
                "origen": origen["nombre"], "carpeta": nombre_carpeta, "audios": len(audio_paths),
                "espera": _dias_esperando(carpeta),
                "motivo": "Los audios no se pudieron enviar a Gemini. Se reintenta en la próxima corrida.",
            })
            return False

        if not batches:
            logger.warning(
                f"[{origen['nombre']}] La carpeta '{nombre_carpeta}' no tenía audios nuevos para auditar "
                f"(¿ya auditados o sin coincidencias?). Se borra igualmente para no reprocesar."
            )
            encolada = False
        else:
            encolada = True
            esperando = sum(1 for b in batches if batch_cola.es_pendiente(b))
            detalle = (
                f" ({esperando} esperando cupo en la cola de lotes)" if esperando else ""
            )
            logger.info(
                f"[{origen['nombre']}] Carpeta '{nombre_carpeta}' enviada a Gemini en {len(batches)} lote(s){detalle}. "
                f"Los resultados se guardarán y exportarán al finalizar el batch."
            )

        # Llegar acá significa que TODOS los lotes están a salvo: o viven en Gemini, o
        # están en la cola con su JSONL (los audios ya comprimidos adentro) en disco.
        # En los dos casos la carpeta local ya no hace falta y se borra para no
        # reencolarla mañana. Si algún lote hubiera quedado sin destino, calidad_batch
        # devuelve None y no se llega hasta acá.
        shutil.rmtree(carpeta)
        logger.info(f"[{origen['nombre']}] Carpeta '{nombre_carpeta}' eliminada tras encolar el batch.")
        return encolada

    except Exception as e:
        # No borramos la carpeta si falló: queda para reintentar/investigar al día siguiente.
        logger.error(
            f"[{origen['nombre']}] Error auditando la carpeta '{nombre_carpeta}': {e}", exc_info=True
        )
        # AvisoUsuarioError ya viene redactado para una persona (p. ej. "al informe le
        # falta la columna Segment Start Time"); el resto va con el tipo de excepción
        # para que se pueda buscar en el log.
        motivo = str(e) if isinstance(e, AvisoUsuarioError) else f"{type(e).__name__}: {e}"
        problemas.append({
            "origen": origen["nombre"], "carpeta": nombre_carpeta, "audios": len(audio_paths),
            "espera": _dias_esperando(carpeta), "motivo": motivo,
        })
        return False


def auditoria_csv_diaria():
    """
    Tarea programada (madrugada) que audita los audios CSV subidos al fileserver.

    Recorre los dos orígenes (ver _origenes_csv: "Audios generales" y "Audios VIP") y,
    dentro de cada uno, cada subcarpeta (una "descarga" con sus audios .wav + 1 .csv
    (ucid) + 1 .txt (savedFiles)) se encola como un lote de Gemini (modo Batch) bajo
    empresa 10 / campaña 19 / plantilla 12. Lo único que cambia entre los orígenes es
    el Google Sheet de destino.

    La carpeta se borra recién cuando el lote quedó encolado (los audios ya están
    subidos a Gemini en ese punto) para no reprocesarla al día siguiente. Si el
    envío falla, la carpeta queda intacta y se reintenta en la corrida siguiente.

    Toda carpeta que quede sin auditar (salteada por estar incompleta o caída con
    error) se informa por mail a ADMIN_EMAIL al terminar la corrida, con el motivo y
    hace cuántos días está esperando: si no, una tanda mal exportada se acumula en el
    fileserver sin que nadie se entere hasta que alguien nota que faltan auditorías.
    Un origen que no esté montado también sale por ese mail: si no, los audios VIP se
    juntarían en el fileserver sin que nadie note que el mount se cayó.

    El guardado en SQL, la plantilla de columnas "Auditorias CSV" y la subida a
    Google Sheets NO pasan por acá: los hace Auditor.procesar_batch cuando el lote
    termina, horas más tarde.
    """
    logger.info("Scheduler: Iniciando auditoría diaria de audios CSV...")
    if Auditor is None:
        logger.error("Servicio Auditor no disponible. Cancelando tarea de auditoría CSV.")
        return

    carpetas_encoladas = 0
    # Carpetas que quedaron sin auditar (salteadas o con error), de los dos orígenes.
    # Se juntan para mandar UN aviso al final, no uno por carpeta.
    problemas: List[Dict[str, Any]] = []

    for origen in _origenes_csv():
        directorio = origen["dir"]

        if not os.path.isdir(directorio):
            motivo = (
                f"La carpeta {directorio} no existe o no está montada en el servidor: "
                f"no se pudo auditar nada de este origen."
            )
            logger.error(f"[{origen['nombre']}] {motivo}")
            problemas.append({
                "origen": origen["nombre"], "carpeta": "(origen completo)", "audios": 0,
                "espera": 0, "motivo": motivo,
            })
            continue

        # Cada subcarpeta directa es una descarga independiente.
        subcarpetas = sorted(
            os.path.join(directorio, nombre)
            for nombre in os.listdir(directorio)
            if os.path.isdir(os.path.join(directorio, nombre))
        )

        if not subcarpetas:
            logger.info(f"[{origen['nombre']}] No se encontraron subcarpetas de audios CSV para auditar.")
            continue

        logger.info(
            f"[{origen['nombre']}] Se encontraron {len(subcarpetas)} carpeta(s) de audios CSV para auditar."
        )

        for carpeta in subcarpetas:
            # Cada carpeta es una descarga independiente: el fallo de una no frena a las
            # demás ni al otro origen.
            if _encolar_carpeta_csv(carpeta, origen, problemas):
                carpetas_encoladas += 1

    if carpetas_encoladas:
        logger.info(
            f"Auditoría CSV: {carpetas_encoladas} carpeta(s) encoladas en modo Batch. "
            f"check_completed_batches se encarga del guardado y del envío a Google Sheets."
        )
    else:
        logger.info("Auditoría CSV: no se encoló ningún batch en esta corrida.")

    if problemas:
        logger.warning(
            f"Auditoría CSV: {len(problemas)} carpeta(s) quedaron sin auditar. "
            f"Se avisa por mail a {settings.ADMIN_EMAIL}."
        )
        _avisar_carpetas_csv_pendientes(problemas)


def calcular_next_run(frecuencia: str, hora_str: str, dias_semana: Optional[list] = None, desde_fecha: Optional[datetime.datetime] = None) -> datetime.datetime:
    """Calcula la próxima ejecución basada en la frecuencia y hora."""
    if desde_fecha is None:
        desde_fecha = datetime.datetime.now()
    
    try:
        hora = datetime.datetime.strptime(hora_str, "%H:%M:%S").time()
    except ValueError:
        hora = datetime.datetime.strptime(hora_str, "%H:%M").time()
    candidata = datetime.datetime.combine(desde_fecha.date(), hora)
    
    if candidata <= desde_fecha:
        candidata += datetime.timedelta(days=1)
        
    if frecuencia == 'diaria':
        return candidata
    elif frecuencia == 'semanal' and dias_semana:
        map_dias = {'L': 0, 'M': 1, 'X': 2, 'J': 3, 'V': 4, 'S': 5, 'D': 6}
        dias_validos = [map_dias.get(d) for d in dias_semana if map_dias.get(d) is not None]
        
        while candidata.weekday() not in dias_validos:
            candidata += datetime.timedelta(days=1)
        return candidata
    elif frecuencia == 'mensual':
        # Salta al mismo día del próximo mes (simplificado a 30 días o inicio de mes)
        if candidata.month == 12:
            return candidata.replace(year=candidata.year + 1, month=1)
        else:
            return candidata.replace(month=candidata.month + 1)
    
    return candidata

def calcular_fechas_rango(rango_dinamico: str):
    """Convierte el rango dinámico en fechas de inicio y fin exactas."""
    hoy = datetime.datetime.now()
    
    if rango_dinamico == 'ayer':
        inicio = fin = hoy - datetime.timedelta(days=1)
    elif rango_dinamico == 'dia_habil_anterior':
        if hoy.weekday() == 0:  # Lunes -> Viernes
            inicio = fin = hoy - datetime.timedelta(days=3)
        elif hoy.weekday() == 6:  # Domingo -> Viernes
            inicio = fin = hoy - datetime.timedelta(days=2)
        else:
            inicio = fin = hoy - datetime.timedelta(days=1)
    elif rango_dinamico == 'ultimos_7':
        inicio = hoy - datetime.timedelta(days=7)
        fin = hoy
    elif rango_dinamico == 'mes_actual':
        inicio = hoy.replace(day=1)
        fin = hoy
    elif rango_dinamico == 'mes_anterior':
        fin = hoy.replace(day=1) - datetime.timedelta(days=1)
        inicio = fin.replace(day=1)
    else:
        inicio = fin = hoy # Default fallback
        
    return inicio.strftime("%Y-%m-%d"), fin.strftime("%Y-%m-%d")

def procesar_tarea_individual(tarea):
    """Corre una tarea programada con su propia etiqueta de log.

    El hilo lo lanza run_pending_schedulers, y pueden ser hasta 5 a la vez sobre
    auditorías largas: sin una etiqueta por tarea, las líneas de las cinco
    corridas quedan intercaladas en el journal y no se puede saber cuál falló.
    La etiqueta lleva el nombre del scheduler porque es el dato con el que uno
    llega al problema ("falló la auditoría de tal campaña").
    """
    with contexto_job(f"sched:{tarea['task_name']}"):
        _procesar_tarea_individual(tarea)


def _procesar_tarea_individual(tarea):
    """Esta función corre en su propio hilo. Maneja una sola tarea de principio a fin."""
    scheduler_id = tarea['id']
    task_name = tarea['task_name']
    historial_id = None
    
    try:
        # 1. REGISTRAR INICIO EN EL HISTORIAL
        with engine.begin() as conn:
            result = conn.execute(
                text("""
                    INSERT INTO [calidad].[AuditSchedulerHistory] (scheduler_id, status) 
                    OUTPUT inserted.id 
                    VALUES (:id, 'EN_CURSO')
                """), 
                {"id": scheduler_id}
            )
            historial_id = result.scalar()

        logger.info(f"Hilo iniciado para Scheduler: {task_name} (ID: {scheduler_id})")

        # 1.b REVALIDAR ALCANCE DEL CREADOR (permisos template:<empresa>).
        # Si perdió acceso a la empresa programada, se OMITE la corrida pero se
        # avanza next_run_time para no reintentar cada minuto.
        if _scheduler_fuera_de_alcance(tarea):
            logger.warning(
                f"Scheduler {task_name} (ID {scheduler_id}) omitido: el creador "
                f"{tarea['created_by']} ya no tiene alcance sobre la empresa/campaña programada."
            )
            next_run = calcular_next_run(
                tarea['frecuencia'], str(tarea['hora_ejecucion']),
                tarea['dias_semana'].split(',') if tarea['dias_semana'] else [],
                datetime.datetime.now()
            )
            with engine.begin() as conn:
                conn.execute(
                    text("UPDATE [calidad].[AuditSchedulers] SET next_run_time = :nr, is_running = 0 WHERE id = :id"),
                    {"nr": next_run, "id": scheduler_id}
                )
                conn.execute(
                    text("""UPDATE [calidad].[AuditSchedulerHistory]
                            SET status = 'ERROR', end_time = GETDATE(), error_message = :err
                            WHERE id = :hist_id"""),
                    {"err": "Corrida omitida: el creador ya no tiene permiso sobre la empresa/campaña programada (permisos template:<empresa>).",
                     "hist_id": historial_id}
                )
            return

        # 2. PREPARAR PARÁMETROS Y EJECUTAR
        fecha_desde, fecha_hasta = calcular_fechas_rango(tarea['rango_dinamico'])
        params_extra = json.loads(tarea['parametros_json'] or "{}")
        
        # NUEVO: Verificamos si la tarea está configurada para correr en Batch
        is_batch = params_extra.get('is_batch', False)
        
        # Replicamos la expansión + variantes que hace el endpoint /Auditar/
        # para que las selecciones de árbol (Mitrol/Wize) matcheen con la DB.
        tipificacion_lista = preparar_tipificacion_para_consulta(
            engine=engine,
            tipificacion=params_extra.get('tipificacion'),
            campana=tarea['campana'],
        )

        # Validamos y construimos el diccionario para Auditor.run() o run_batch()
        audit_params = {
            "plantilla_id": tarea['plantilla_id'],
            "user_id": tarea['created_by'],
            "cantidad": tarea['cantidad'],
            "empresa": tarea['empresa'],
            "campana": tarea['campana'],
            "Fecha_desde": datetime.datetime.strptime(fecha_desde, "%Y-%m-%d"),
            "Fecha_hasta": datetime.datetime.strptime(fecha_hasta, "%Y-%m-%d"),
            "loginid": params_extra.get('loginid'),
            "sentido": params_extra.get('sentido'),
            "tipificacion": tipificacion_lista,
            "duracion_min": params_extra.get('duracion_min'),
            "duracion_max": params_extra.get('duracion_max'),
            "reauditar": params_extra.get('reauditar', False),
            "por_operador": params_extra.get('por_operador', False),
            "por_tipificacion": params_extra.get('por_tipificacion', False),
            # El scheduler corre desatendido: no hay forma de confirmar en el frontend,
            # así que permitimos superar el límite de 200 cuando el muestreo es por
            # operador o por tipificación (solo tiene efecto en ese caso).
            "omitir_limite": True
        }

        if Auditor is None:
            logger.error("Servicio Auditor no disponible. No se puede ejecutar la tarea.")
            return

        # ---------------------------------------------------------
        # 3. EJECUTAR AUDITORÍA SEGÚN EL MODO (NORMAL O BATCH)
        # ---------------------------------------------------------
        if is_batch:
            logger.info(f"Ejecutando auditoría en MODO BATCH. Enviando a Gemini API...")
            
            # Pasamos los parámetros de Sheet por si el usuario los configuró
            gsheet_id_val = tarea['gsheet_id'] if tarea['send_gsheets'] else None
            gsheet_name_val = tarea['gsheet_name'] if tarea['send_gsheets'] else None
            
            # Los destinatarios viajan CON el lote (calidad.Batch_data): el mail de
            # resultado sale horas después, cuando el batch termina, y hasta ahora ese
            # camino no tenía forma de saber a quién escribirle — caía al mail del
            # usuario que creó la tarea, así que la lista configurada acá no recibía
            # nada (reportado en las tareas de Vantix, 2026-08-19).
            destinatarios_val = tarea['email_addresses'] if tarea['send_email'] else None

            lotes = Auditor.run_batch(
                **audit_params,
                gsheet_id=gsheet_id_val,
                gsheet_name=gsheet_name_val,
                column_template_id=tarea.get('column_template_id'),
                trigger_source="scheduler",
                scheduler_id=scheduler_id,
                scheduler_name=task_name,
                email_destinatarios=destinatarios_val,
            )
            # None = la corrida falló antes de encolar (run_batch se traga la excepción
            # y la deja en el log). Antes se ignoraba y la tarea quedaba "finalizada con
            # éxito": así se perdió todos los días desde agosto una de las dos tareas de
            # ALARMIX que arrancan juntas y mueren en el login de CXOne. Como error, entra al
            # circuito de reintentos (next_run_time no avanza: la próxima pasada del
            # scheduler la vuelve a correr); a los 3 fallos seguidos se desactiva y avisa.
            if lotes is None:
                raise RuntimeError(
                    "La corrida Batch no encoló nada: falló antes de llegar a Gemini "
                    "(el motivo está en el log de la tarea)."
                )

            # Estimación: se mandan a procesar esta 'cantidad' (el resultado real se verá al finalizar el batch)
            filas_auditadas = tarea['cantidad'] 
            
            # IMPORTANTE: En modo batch saltamos el envío inmediato de correos y sheets.
            # Eso lo hará la función 'check_completed_batches()' cuando Gemini responda (horas más tarde).
            logger.info("Batch enviado a Gemini. El correo y GSheets se procesarán asíncronamente al finalizar.")
            
        else:
            # Flujo original Sincrónico
            logger.info("Ejecutando auditoría en MODO NORMAL (Sincrónico)...")
            df_resultado = Auditor.run(
                **audit_params, trigger_source="scheduler", scheduler_id=scheduler_id, scheduler_name=task_name
            )
            filas_auditadas = len(df_resultado) if not df_resultado.empty else 0

            # Log detallado (modo, origen, duración, errores, tokens, costo estimado)
            # SOLO para ADMIN_EMAIL: independiente de si la tarea tiene mail/sheets
            # configurado, es visibilidad interna, no el resultado de la auditoría.
            if filas_auditadas > 0:
                _enviar_mail_log_admin_sync(
                    scheduler_id=scheduler_id, task_name=task_name, empresa=tarea['empresa'],
                    campana=tarea['campana'], plantilla_id=tarea['plantilla_id'],
                    fecha_desde=fecha_desde, fecha_hasta=fecha_hasta,
                    cantidad_solicitada=tarea['cantidad'], filas_auditadas=filas_auditadas,
                )

            if (tarea['send_email'] or tarea['send_gsheets']) and filas_auditadas > 0:
                logger.info(f"Auditoría ejecutada. Filas auditadas: {filas_auditadas}. Preparando envíos...")
                hoy = datetime.date.today()
                
                id_auditorias = Auditor._estandarizar_columnas_sql(df_resultado, solo_id=True)['id_aplicativo']
                lista_id_auditorias = id_auditorias.tolist() if not id_auditorias.empty else []
                lista_id_auditorias_str = ",".join(map(str, lista_id_auditorias)) if lista_id_auditorias else "0" 
                
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
                    "usuario": tarea['created_by'],
                    "campana": tarea['campana'],
                    "empresa": tarea['empresa'],
                    "plantilla": tarea['plantilla_id'],
                    "fecha_desde": hoy, 
                    "fecha_hasta": hoy,
                    "id_aplicativo": lista_id_auditorias_str,
                    "incluir_transcripcion": True, 
                    "response_thoughts": True
                }

                with engine.connect() as conn:
                    result = conn.execute(query, db_params)
                    columnas_sql = list(result.keys())
                    rows = result.mappings().all() 

                df_resultado = pd.DataFrame(rows, columns=columnas_sql)
                # Columnas técnicas fuera, opt-in fuera salvo que la plantilla de columnas
                # de la tarea las pida, y después la plantilla. Importa que la forma no
                # cambie entre corridas: la subida a Sheets ANEXA a la misma hoja sin
                # reescribir los encabezados (ver app/utils/google_sheet.py).
                df_resultado = preparar_export(engine, df_resultado, tarea.get('column_template_id'))

                # Enviar por Email
                if tarea['send_email'] and tarea['email_addresses'] and not df_resultado.empty:
                    destinatarios = [e.strip() for e in tarea['email_addresses'].split(',')]
                    
                    stream = io.BytesIO()
                    df_resultado.to_excel(stream, engine='openpyxl', index=False)
                    archivo_adjunto = {
                        "nombre": f"Resultado_{tarea['task_name']}.xlsx",
                        "contenido": stream.getvalue(),
                        "tipo": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    }
                    
                    Envios_mail_manager.enviar_correo_base(
                        destinatarios=destinatarios,
                        asunto=f"Auditoría Automática: {tarea['task_name']}",
                        mensaje_html=f"Adjunto encontrarás los resultados de la auditoría automática ejecutada para fechas {fecha_desde} al {fecha_hasta}.",
                        archivos_adjuntos_memoria=[archivo_adjunto]
                    )

                # Enviar a GSheets
                if tarea['send_gsheets'] and tarea['gsheet_id'] and not df_resultado.empty:
                    nombre_hoja = tarea['gsheet_name'] if tarea['gsheet_name'] else f"{tarea['task_name']}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    resultado_gs = dataframe_a_sheet(
                        sheet_id=tarea['gsheet_id'],
                        df=df_resultado,
                        nombre_hoja=nombre_hoja
                    )
                    logger.info(f"Resultados subidos a Google Sheets: {resultado_gs}")

        # ---------------------------------------------------------
        # 4. CALCULAR PRÓXIMA EJECUCIÓN Y ACTUALIZAR HISTORIAL
        # ---------------------------------------------------------
        next_run = calcular_next_run(
            tarea['frecuencia'],
            str(tarea['hora_ejecucion']),
            tarea['dias_semana'].split(',') if tarea['dias_semana'] else [],
            datetime.datetime.now()
        )
        
        with engine.begin() as conn:
            conn.execute(
                text("""
                    UPDATE [calidad].[AuditSchedulers] 
                    SET next_run_time = :nr, retry_count = 0, is_running = 0 
                    WHERE id = :id
                """), 
                {"nr": next_run, "id": scheduler_id}
            )
            
            conn.execute(
                text("""
                    UPDATE [calidad].[AuditSchedulerHistory] 
                    SET status = 'EXITO', end_time = GETDATE(), rows_audited = :rows 
                    WHERE id = :hist_id
                """), 
                {"rows": filas_auditadas, "hist_id": historial_id}
            )
            
        logger.info(f"Scheduler {task_name} finalizado con éxito. {'Lote enviado' if is_batch else 'Auditadas'}: {filas_auditadas} filas.")
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error en Scheduler {task_name}: {error_msg}")
        
        intentos_actuales = tarea.get('retry_count', 0) + 1
        max_reintentos = 3
        
        with engine.begin() as conn:
            if historial_id:
                conn.execute(
                    text("""
                        UPDATE [calidad].[AuditSchedulerHistory] 
                        SET status = 'ERROR', end_time = GETDATE(), error_message = :err 
                        WHERE id = :hist_id
                    """), 
                    {"err": traceback.format_exc(), "hist_id": historial_id}
                )

            if intentos_actuales >= max_reintentos:
                logger.warning(f"¡ALERTA! Desactivando '{task_name}' tras {intentos_actuales} fallos.")
                conn.execute(
                    text("UPDATE [calidad].[AuditSchedulers] SET is_active = 0, is_running = 0 WHERE id = :id"),
                    {"id": scheduler_id}
                )
                
                Envios_mail_manager.enviar_correo_base(
                    destinatarios=[settings.ADMIN_EMAIL],
                    asunto=f"URGENTE: Tarea Programada Desactivada - {task_name}",
                    mensaje_html=f"La tarea <b>{task_name}</b> falló {max_reintentos} veces seguidas y fue desactivada.<br><br><b>Último error:</b> {error_msg}",
                    archivos_adjuntos_memoria=[]
                )
            else:
                conn.execute(
                    text("UPDATE [calidad].[AuditSchedulers] SET retry_count = :rc, is_running = 0 WHERE id = :id"),
                    {"rc": intentos_actuales, "id": scheduler_id}
                )

def run_pending_schedulers():
    """Orquestador: Busca tareas y lanza los hilos."""
    # DEBUG: corre cada 5 minutos y casi siempre no hay nada pendiente. Cuando sí
    # hay, la línea de abajo lo dice con el número de tareas.
    logger.debug("Buscando Schedulers pendientes...")
    
    # CONSULTA ATÓMICA MAGISTRAL: 
    # Bloquea la tarea, registra la hora de inicio y devuelve los datos en 1 solo paso.
    # Además, incluye la lógica "Anti-Zombie" (Si lleva > 60 mins corriendo, la toma igual).
    # Solo las de ESTE entorno: dev y prod comparten la tabla, y una tarea programada la
    # ejecuta el scheduler del servidor donde se creó.
    query = text("""
        UPDATE [calidad].[AuditSchedulers]
        SET is_running = 1,
            last_started_at = GETDATE()
        OUTPUT inserted.*
        WHERE is_active = 1
            AND Entorno = :env
            AND next_run_time <= GETDATE()
            AND (
                is_running = 0
                OR DATEDIFF(minute, last_started_at, GETDATE()) > 60
            )
        """)

    with engine.begin() as conn:
        tareas_pendientes = conn.execute(query, {"env": settings.ENVIRONMENT}).mappings().all()

    if not tareas_pendientes:
        return

    logger.info(f"Se encontraron {len(tareas_pendientes)} tareas. Iniciando pool de hilos...")

    # Lanzar tareas en paralelo (Ajusta max_workers según la capacidad de tu servidor/BBDD)
    with ThreadPoolExecutor(max_workers=5) as executor:
        for tarea in tareas_pendientes:
            executor.submit(procesar_tarea_individual, tarea)
            
    logger.info("Todas las tareas han sido enviadas al Pool de ejecución.")

def sync_candidatos_sheet_to_sql_diario():
    """Descarga todo el Form de Google y actualiza la Base de Datos SQL (1 vez al día)"""
    logger.info("Scheduler: Iniciando descarga diaria de Google Sheets a SQL...")
    df = sheet_a_dataframe(settings.SHEET_ID_CANDIDATOS, 'Respuestas de formulario 1')
    
    if df.empty:
        return

    # Mapeo idéntico al que tenías
    col_dni = 'DNI (sin puntos ni guiones)'
    df[col_dni] = df[col_dni].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    df = df.drop_duplicates(subset=[col_dni], keep='last')
    
    rename_map = {
        'Marca temporal': 'FechaSubida',
        'Apellido/s': 'Apellido',
        'Nombre/s': 'Nombre',
        'Correo electrónico': 'Email',
        'Celular de contacto (WhatsApp)': 'Telefono',
        'DNI (sin puntos ni guiones)': 'DNI',
        'CUIL (sin puntos ni guiones)': 'CUIL',
        'En ACME selecciona, contamos con 2 sedes, dependiendo de donde vivas, selecciona la más cercana a tu domicilio': 'Sede',
        'Las entrevistas se realizan en modalidad grupal y tienen una duración máxima de 1 hora. ¿En qué turno te resultaría más cómodo participar?': 'Turno',
        'Zona de residencia (Localidad, barrio)': 'Zona',
        '¿Tenes experiencia en Atención al cliente?': 'Exp_Atencion_Cliente',
        '¿Tenes experiencia en Ventas telefónicas?': 'Exp_Ventas',
        '¿Tenes experiencia en gestión de cobranzas?': 'Exp_Cobranzas',
        'Comentarios importantes': 'Comentarios',
        '¿Tenes hijos?': 'Hijos',
        '¿Contas con el analítico?': 'Analitico',
        '¿Te encontrás estudiando?': 'Estudiando'
    }
    df.rename(columns=rename_map, inplace=True)
    
    df['FechaSubida'] = pd.to_datetime(df['FechaSubida'], dayfirst=True, errors='coerce').dt.strftime('%Y-%m-%d %H:%M:%S')
    df = df.where(pd.notnull(df), None) # Cambiar NaNs por None para SQL

    # Insertar o actualizar en SQL (UPSERT básico)
    upsert_query = text("""
        IF NOT EXISTS (SELECT 1 FROM rrhh.Candidatos WHERE DNI = :DNI)
        BEGIN
            INSERT INTO rrhh.Candidatos (
                DNI, CUIL, FechaSubida, Apellido, Nombre, Email, Telefono, 
                Sede, Turno, Zona, Exp_Atencion_Cliente, Exp_Ventas, 
                Exp_Cobranzas, Comentarios, Hijos, Analitico, Estudiando, Estado
            )
            VALUES (
                :DNI, :CUIL, :FechaSubida, :Apellido, :Nombre, :Email, :Telefono, 
                :Sede, :Turno, :Zona, :Exp_Atencion_Cliente, :Exp_Ventas, 
                :Exp_Cobranzas, :Comentarios, :Hijos, :Analitico, :Estudiando, 'PENDIENTE'
            )
        END
    """)
    
    with engine.begin() as conn:
        for record in df.to_dict('records'):
            try:
                params = {str(k): v for k, v in record.items()}
                conn.execute(upsert_query, params)
            except Exception as e:
                logger.error(f"Error insertando DNI {record.get('DNI')}: {e}")
                
    logger.info("Descarga diaria finalizada.")

def formatear_fecha_ddmmaaaa(valor_fecha):
    """
    Toma una fecha (string, datetime o date) y la devuelve en formato DD/MM/YYYY.
    """
    if not valor_fecha:
        return ""
    
    # Si ya es un objeto tipo fecha de Python (date o datetime)
    if isinstance(valor_fecha, (datetime, datetime.date)):
        return valor_fecha.strftime("%d/%m/%Y")
    
    # Si viene como string desde SQL (usualmente 'YYYY-MM-DD' o 'YYYY-MM-DD HH:MM:SS')
    try:
        # Nos quedamos solo con la parte de la fecha separando por espacio
        fecha_str = str(valor_fecha).split()[0] 
        return datetime.datetime.strptime(fecha_str, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return str(valor_fecha) # Si falla el parseo, lo devuelve como estaba para no romper nada
