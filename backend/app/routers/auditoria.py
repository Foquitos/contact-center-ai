import io
import logging
import os
from functools import lru_cache
from app.dependencies import RoleChecker
from app.tasks import calcular_next_run
from sqlalchemy import text
import shutil
import tempfile
import pandas as pd
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile, status, Query
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from pydantic import BaseModel
from app.models import (
    AuditStatusResponse,
    User,
    SchedulerCreateRequest,
    SchedulerResponse,
    ColumnTemplateCreateRequest,
    ColumnTemplateUpdateRequest,
    ColumnTemplateResponse,
)
from app import cuotas
from app.rbac import empresas_permitidas, exigir_acceso_empresa, exigir_modo_sincronico
from app.security import get_current_active_user
from app.services import Auditor
from app.database import engine
from app.config import settings
from app.managers import plantillas_manager_instance
import json # <--- Importar json
from app.utils.mail import Envios_mail_manager
from app.utils.tabla_filtros import (
    CacheConsultas,
    FiltroInvalido,
    aplicar_filtros,
    calcular_facetas,
    inferir_tipos,
    paginar,
    parsear_filtros,
    validar_columnas,
)
from AuditorIA.SQL_query import AuditLimitExceededError
from AuditorIA.sql_a_Claude import tiene_columna_auditorias
from AuditorIA.avisos import AvisoUsuarioError
from AuditorIA import (
    audio_store,
    golden_metricas,
    golden_set,
    reauditoria,
    revision,
    transcripcion_cola,
    versionado,
)

router = APIRouter(
    dependencies=[Depends(RoleChecker(["audit:execute"]))]
)
logger = logging.getLogger(__name__)

# Scheduler de auditorías: permiso ADICIONAL al audit:execute del router
# (programar corridas recurrentes es más sensible que auditar a demanda).
require_scheduler = RoleChecker(["audit:scheduler"])


def _notificar_inicio_auditoria(subject: str, body: str):
    """Envía el mail de aviso de inicio de auditoría. Pensado para correr como
    background task: el envío SMTP es bloqueante y no debe demorar la respuesta."""
    try:
        Envios_mail_manager.enviar_correo_base(
            destinatarios=[settings.ADMIN_EMAIL],
            asunto=subject,
            mensaje_html=body
        )
    except Exception as e:
        logger.error(f"Error al enviar el correo de notificación de auditoría: {e}")


def run_audit_task(task_id: str, auditor_instance, params: dict):
    try:
        logger.info(f"Iniciando tarea de auditoría en segundo plano: {task_id}")
        response_df = auditor_instance.run(**params, trigger_source="manual", task_id=task_id)

        if response_df.empty:
            # run() devuelve df vacío tanto cuando no hay audios que cumplan los
            # filtros como cuando tragó un error inesperado (queda en los logs).
            # En ambos casos el usuario debe enterarse, no recibir un Excel vacío.
            raise AvisoUsuarioError(
                "No se encontraron audios para auditar con los filtros seleccionados. "
                "Puede que no haya llamadas en el período, que ya estén auditadas "
                "(activá 'Reauditar' si querés repetirlas) o que fallara la descarga de audios."
            )

        # Serializar el DataFrame a un string JSON
        result_json = response_df.to_json(orient='records')
        
        # Guardar resultado en BBDD
        update_query = text("""
            UPDATE [calidad].[AuditTasks]
            SET [status] = 'completed', [result_data] = :data, [updated_at] = GETDATE()
            WHERE [task_id] = :task_id
        """)
        with engine.connect() as conn:
            conn.execute(update_query, {"data": result_json, "task_id": task_id})
            conn.commit()
            
        logger.info(f"Tarea de auditoría {task_id} completada exitosamente.")
        
    except AuditLimitExceededError as e:
        logger.warning(f"Tarea {task_id} excedió el límite de auditorías: {e}")
        # Prefijo LIMITE_EXCEDIDO para que el frontend lo muestre como aviso (no error rojo)
        error_str = f"LIMITE_EXCEDIDO:{e.cantidad_obtenida}:{str(e)}"

        update_query = text("""
            UPDATE [calidad].[AuditTasks]
            SET [status] = 'failed', [error_message] = :error, [updated_at] = GETDATE()
            WHERE [task_id] = :task_id
        """)
        try:
            with engine.connect() as conn:
                conn.execute(update_query, {"error": error_str, "task_id": task_id})
                conn.commit()
        except Exception as db_e:
            logger.error(f"Error al guardar el aviso de límite de la tarea {task_id} en BBDD: {db_e}")

    except AvisoUsuarioError as e:
        logger.warning(f"Tarea {task_id} finalizó con aviso al usuario: {e}")
        # Prefijo AVISO para que el frontend lo muestre como advertencia (no error rojo)
        error_str = f"AVISO:{str(e)}"

        update_query = text("""
            UPDATE [calidad].[AuditTasks]
            SET [status] = 'failed', [error_message] = :error, [updated_at] = GETDATE()
            WHERE [task_id] = :task_id
        """)
        try:
            with engine.connect() as conn:
                conn.execute(update_query, {"error": error_str, "task_id": task_id})
                conn.commit()
        except Exception as db_e:
            logger.error(f"Error al guardar el aviso de la tarea {task_id} en BBDD: {db_e}")

    except Exception as e:
        logger.error(f"Error en la tarea de auditoría {task_id}: {e}")
        error_str = str(e)

        # Guardar error en BBDD
        update_query = text("""
            UPDATE [calidad].[AuditTasks]
            SET [status] = 'failed', [error_message] = :error, [updated_at] = GETDATE()
            WHERE [task_id] = :task_id
        """)
        try:
            with engine.connect() as conn:
                conn.execute(update_query, {"error": error_str, "task_id": task_id})
                conn.commit()
        except Exception as db_e:
            logger.error(f"Error al *guardar el error* de la tarea {task_id} en BBDD: {db_e}")

@router.post("/Auditar/", tags=["AuditorIA"], status_code=202)
def Auditar(
    background_tasks: BackgroundTasks,
    plantilla_id: int = Form(...), 
    formato_salida: Optional[str] = Form("html"),
    carpeta_audios: Optional[List[UploadFile]] = File(None),
    ucid_file: Optional[UploadFile] = File(None),
    saved_files_txt: Optional[UploadFile] = File(None),
    cantidad: Optional[int] = Form(1),
    duracion_min: Optional[int] = Form(None),
    duracion_max: Optional[int] = Form(None),
    idInteraccion: Optional[List[str]] = Form(None),
    Segmento: Optional[List[str]] = Form(None), 
    Fecha_desde: Optional[str] = Form(None),
    Fecha_hasta: Optional[str] = Form(None),
    loginid: Optional[List[str]] = Form(None),
    empresa: Optional[str] = Form(None),
    campana: Optional[str] = Form(None), 
    tipificacion: Optional[List[str]] = Form(None),
    sentido: Optional[List[str]] = Form(None),
    comentario: Optional[List[str]] = Form(None),
    calidad: Optional[bool] = Form(True),
    transcribir: Optional[bool] = Form(False),
    por_operador: Optional[bool] = Form(False),
    por_tipificacion: Optional[bool] = Form(False),
    omitir_limite: Optional[bool] = Form(False),
    reauditar: Optional[bool] = Form(False),
    current_user: User = Depends(get_current_active_user),
    batch: Optional[bool] = Form(False),
    gsheet_id: Optional[str] = Form(None),
    gsheet_name: Optional[str] = Form(None),
    upload_group_id: Optional[str] = Form(None),
):
    if Auditor is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Auditing service is currently unavailable.")

    # Modo sincrónico ("Auditar ahora") = permiso audit:sync, adicional al
    # audit:execute del router. Se valida acá y no en el front porque el POST
    # a /Auditar/ se puede hacer a mano; el default de la pantalla es Batch.
    if not batch:
        exigir_modo_sincronico(current_user)

    # Alcance por empresa (permisos template:<empresa>): validar ANTES de
    # encolar. empresa/campana llegan como strings del form; la plantilla
    # resuelve la empresa aunque los otros dos no vengan.
    with engine.connect() as conn:
        exigir_acceso_empresa(
            conn, current_user,
            empresa_id=int(empresa) if empresa and str(empresa).isdigit() else None,
            campana_id=int(campana) if campana and str(campana).isdigit() else None,
            plantilla_id=plantilla_id,
        )
        # Cupo mensual (ver app/cuotas.py): a quién se le imputa esta corrida.
        # Se resuelve una sola vez acá y viaja hasta la reserva de más abajo.
        alcanzado_por_cupo = not cuotas.esta_exento(current_user)
        campana_cupo = (cuotas.resolver_campana(conn, campana, plantilla_id)
                        if alcanzado_por_cupo else None)
        campana_con_tope = alcanzado_por_cupo and cuotas.tiene_tope(conn, campana_cupo)

    # Quien está alcanzado por un cupo audita SOLO en Batch: el sincrónico sale el
    # doble en Gemini, así que el mismo cupo rendiría la mitad. No reemplaza a
    # audit:sync (que ya corta más arriba), lo refuerza para el caso de alguien
    # que tenga los dos permisos y además cupo.
    if not batch and campana_con_tope:
        raise HTTPException(
            status_code=403,
            detail=("Las campañas con cupo mensual se auditan en modo Batch: cuesta la "
                    "mitad y el cupo te rinde el doble. Usá \"Enviar a la Cola (Batch)\"."),
        )

    # Tampoco pueden saltarse el tope de 200 del muestreo por grupo: es el techo
    # con el que se reserva el cupo (ver cuotas.cantidad_a_reservar).
    if campana_con_tope and omitir_limite:
        omitir_limite = False

    # Avisa por mail que se inició una auditoría. Se manda en segundo plano: el
    # envío SMTP es bloqueante y, hecho inline, demoraba el 202 (y por ende el
    # "en proceso" del frontend) hasta que terminaba de mandarse el correo.
    if empresa not in ('10', '11'): #Evitar enviar mails por cada tanda de subida (CSV / Voltara)
        subject = "Nueva auditoría iniciada"
        body = f"El usuario {current_user.usuario} ha iniciado una auditoría con los siguientes parámetros:<br><br>" \
            f"Plantilla ID: {plantilla_id}<br>" \
            f"Cantidad: {cantidad}<br>" \
            f"Duración Mínima: {duracion_min}<br>" \
            f"Duración Máxima: {duracion_max}<br>" \
            f"ID de Interacción: {idInteraccion}<br>" \
            f"Segmento: {Segmento}<br>" \
            f"Fecha Desde: {Fecha_desde}<br>" \
            f"Fecha Hasta: {Fecha_hasta}<br>" \
            f"Login ID: {loginid}<br>" \
            f"Empresa: {empresa}<br>" \
            f"Campaña: {campana}<br>" \
            f"Tipificación: {tipificacion}<br>" \
            f"Sentido: {sentido}<br>" \
            f"Calidad: {calidad}<br>" \
            f"Transcribir: {transcribir}<br>" \
            f"Por operador: {por_operador}<br>" \
            f"Por tipificación: {por_tipificacion}<br>" \
            f"Omitir límite (>200 confirmado): {omitir_limite}<br>" \
            f"Reauditar: {reauditar}<br>"
        background_tasks.add_task(_notificar_inicio_auditoria, subject, body)


    task_id = f"audit_{current_user.usuario}_{int(datetime.now().timestamp())}"
    temp_dir, audio_paths, ucid_path, saved_files_txt_path = None, [], None, None

    # Campañas por subida de archivos: CSV (empresa 10 / campaña 19) y Voltara (empresa
    # 11). Ambas suben los audios en `carpeta_audios` + un archivo de mapeo en `ucid_file`
    # (UCID en CSV, Excel nombre->ConnID en Voltara). `saved_files_txt` es solo de CSV.
    if campana == '19' or empresa == '11':
        temp_dir = tempfile.mkdtemp()
        if carpeta_audios:
            for audio_file in carpeta_audios:
                filename = audio_file.filename or f"audio_{len(audio_paths)}"
                file_path = os.path.join(temp_dir, filename)
                with open(file_path, "wb") as buffer:
                    shutil.copyfileobj(audio_file.file, buffer)
                audio_paths.append(file_path)
        if ucid_file:
            filename = ucid_file.filename or "ucid_file"
            file_path = os.path.join(temp_dir, filename)
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(ucid_file.file, buffer)
            ucid_path = file_path
        if saved_files_txt:
            filename = saved_files_txt.filename or "saved_files.txt"
            file_path = os.path.join(temp_dir, filename)
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(saved_files_txt.file, buffer)
            saved_files_txt_path = file_path

        # CSV no usa `cantidad` para nada en la descarga (audita exactamente los
        # audios adjuntos); lo pisamos con la cantidad real de audios de ESTA
        # tanda para que el log (calidad.AuditExecutionLog) muestre "solicitadas"
        # real en vez del valor crudo del form (típicamente 1, sin relación con
        # los archivos subidos). El frontend sube en tandas de 10
        # (auditoria.js::handleCSVUpload); ver upload_group_id más abajo.
        if audio_paths:
            cantidad = len(audio_paths)

    # Reserva del cupo mensual de la campaña. Va acá y no más arriba porque en las
    # subidas por archivo la cantidad real recién se conoce con los audios ya
    # copiados. Se descuenta lo PEDIDO; el barrido cuotas.conciliar() lo ajusta a
    # las filas realmente auditadas cuando la corrida cierra.
    if alcanzado_por_cupo:
        referencia_cupo = cuotas.referencia_auditoria(
            task_id=task_id, upload_group_id=upload_group_id)
        try:
            cuotas.reservar(
                engine,
                campana_id=campana_cupo,
                documento=current_user.usuario,
                cantidad=cuotas.cantidad_a_reservar(
                    cantidad, por_operador=por_operador, por_tipificacion=por_tipificacion),
                tipo=cuotas.TIPO_AUDITORIA,
                referencia=referencia_cupo,
            )
        except cuotas.CuotaExcedidaError as e:
            # Los audios ya copiados no sirven para nada si el pedido no entra.
            if temp_dir:
                shutil.rmtree(temp_dir, ignore_errors=True)
            raise HTTPException(status_code=403, detail=str(e))

    # Expansión por prefijo (Mitrol/Wize) + variantes de separador.
    # Centralizado en utils para que el scheduler ejecute la misma transformación.
    from app.utils.tipificaciones import preparar_tipificacion_para_consulta
    tipificacion = preparar_tipificacion_para_consulta(
        engine=engine,
        tipificacion=tipificacion,
        campana=campana,
    )

    audit_params = {
        "plantilla_id": plantilla_id,
        'user_id': current_user.usuario, "cantidad": cantidad, "duracion_min": duracion_min,
        "duracion_max": duracion_max, "idInteraccion": idInteraccion, "Segmento": Segmento,
        "Fecha_desde": datetime.strptime(Fecha_desde, "%Y-%m-%d") if Fecha_desde else None,
        "Fecha_hasta": datetime.strptime(Fecha_hasta, "%Y-%m-%d") if Fecha_hasta else None,
        "loginid": loginid,
        "empresa": empresa,
        "campana": campana,
        "tipificacion": tipificacion,
        "sentido": sentido,
        "comentario": comentario,
        "custom_audio_paths": audio_paths,
        "ucids": ucid_path,
        "saved_files": saved_files_txt_path,
        "calidad": calidad,
        "transcribir": transcribir,
        "por_operador": por_operador,
        "por_tipificacion": por_tipificacion,
        "omitir_limite": omitir_limite,
        "reauditar": reauditar,
        "upload_group_id": upload_group_id,
    }

    if not batch:
        # --- Lógica NO-BATCH (Auditoría individual con polling) ---
        logger.info(f"Registrando tarea de auditoría NO-BATCH {task_id} en la BBDD.")
        
        # --- NUEVO: Insertar estado 'pending' en la BBDD ---
        try:
            insert_query = text("""
                INSERT INTO [calidad].[AuditTasks] (task_id, status, created_at, updated_at, Entorno)
                VALUES (:task_id, 'pending', GETDATE(), GETDATE(), :env)
            """)
            with engine.connect() as conn:
                conn.execute(insert_query, {"task_id": task_id, "env": settings.ENVIRONMENT})
                conn.commit()
        except Exception as e:
            logger.error(f"Error al insertar la tarea {task_id} en BBDD: {e}")
            raise HTTPException(status_code=500, detail=f"Error al registrar la tarea en la base de datos: {e}")
        # --- FIN NUEVO ---

        background_tasks.add_task(run_audit_task, task_id, Auditor, audit_params)
        # audit_results[task_id] = {"status": "pending"} # <-- ELIMINADO
        
        return {"message": "La auditoría ha sido iniciada.", "task_id": task_id}
    
    else:
        audit_params.update({
            "gsheet_id": gsheet_id, "gsheet_name": gsheet_name, "task_id": task_id,
            "trigger_source": "manual",
        })
        # --- Lógica BATCH ---
        # Registramos la tarea en AuditTasks para que el frontend pueda mostrar el
        # progreso por etapas (descargando audios → enviando a Gemini → en cola).
        # run_batch va actualizando ese estado a medida que avanza.
        logger.info(f"Añadiendo tarea de auditoría BATCH {task_id} al fondo.")
        if Auditor is None:
             raise HTTPException(status_code=503, detail="El servicio de Auditoría no está disponible.")

        try:
            insert_query = text("""
                INSERT INTO [calidad].[AuditTasks] (task_id, status, created_at, updated_at, Entorno)
                VALUES (:task_id, 'processing', GETDATE(), GETDATE(), :env)
            """)
            with engine.connect() as conn:
                conn.execute(insert_query, {"task_id": task_id, "env": settings.ENVIRONMENT})
                conn.commit()
        except Exception as e:
            logger.error(f"Error al registrar la tarea batch {task_id} en BBDD: {e}")
            raise HTTPException(status_code=500, detail=f"Error al registrar la tarea en la base de datos: {e}")

        background_tasks.add_task(Auditor.run_batch, **audit_params)

        return {"message": "La auditoría se ha puesto en cola.", "task_id": task_id}

@router.get("/Auditar/status/{task_id}", response_model=AuditStatusResponse, tags=["AuditorIA"])
def get_audit_status(task_id: str):
    query = text("SELECT [status], [error_message] FROM [calidad].[AuditTasks] WHERE [task_id] = :task_id")
    result_db = None
    try:
        with engine.connect() as conn:
            db_row = conn.execute(query, {"task_id": task_id}).mappings().first()
        if db_row:
            result_db = {"status": db_row["status"], "error": db_row["error_message"]}
    except Exception as e:
        logger.error(f"Error al consultar estado de tarea {task_id} en BBDD: {e}")
        raise HTTPException(status_code=500, detail="Error al consultar estado de la tarea.")

    if not result_db:
        raise HTTPException(status_code=404, detail="ID de tarea no encontrado.")
    
    # 1. Obtenemos el estado. Usamos .get() por seguridad, aunque sabemos que existe.
    status_from_db = result_db.get("status")
    
    # 2. Validamos que sea un string.
    if not isinstance(status_from_db, str):
        logger.error(f"La tarea {task_id} tiene un estado inválido en la BBDD: {status_from_db}")
        raise HTTPException(status_code=500, detail="Se encontró un estado de tarea inválido.")

    # 3. Pasamos los valores validados al modelo de respuesta.
    return AuditStatusResponse(
        task_id=task_id,
        status=status_from_db,        
        error=result_db.get("error")  
    )

@router.get("/Auditar/resultado/{task_id}", tags=["AuditorIA"])
def get_audit_result(
    task_id: str,
    background_tasks: BackgroundTasks, # <-- AÑADIDO
    formato_salida: str = "html"
):
    
    # --- Función helper para borrar la tarea en segundo plano ---
    def delete_task_from_db(task_id_to_delete: str):
        try:
            with engine.connect() as conn:
                delete_query = text("DELETE FROM [calidad].[AuditTasks] WHERE [task_id] = :task_id")
                conn.execute(delete_query, {"task_id": task_id_to_delete})
                conn.commit()
            logger.info(f"Registro de tarea temporal {task_id_to_delete} eliminado de AuditTasks.")
        except Exception as e:
            # Loguea el error pero no detiene la respuesta al usuario
            logger.error(f"Error al eliminar la tarea {task_id_to_delete} de AuditTasks: {e}")

    query = text("SELECT [status], [error_message], [result_data] FROM [calidad].[AuditTasks] WHERE [task_id] = :task_id")
    result_db = None
    try:
        with engine.connect() as conn:
            db_row = conn.execute(query, {"task_id": task_id}).mappings().first()
        if db_row:
            result_db = {
                "status": db_row["status"], 
                "error": db_row["error_message"], 
                "data_json": db_row["result_data"]
            }
    except Exception as e:
        logger.error(f"Error al obtener resultado de tarea {task_id} en BBDD: {e}")
        raise HTTPException(status_code=500, detail="Error al obtener resultado de la tarea.")

    if not result_db:
        raise HTTPException(status_code=404, detail="ID de tarea no encontrado.")

    if result_db.get("status") == "pending":
        raise HTTPException(status_code=202, detail="La tarea aún está pendiente.")

    if result_db.get("status") == "failed":
        background_tasks.add_task(delete_task_from_db, task_id) 
        raise HTTPException(status_code=500, detail=f"La tarea falló: {result_db.get('error')}")

    if result_db.get("status") == "completed":
        data_json_str = result_db.get("data_json")
        if not data_json_str:
            background_tasks.add_task(delete_task_from_db, task_id) 
            logger.error(f"La tarea {task_id} está 'completed' pero no tiene result_data.")
            raise HTTPException(status_code=500, detail="La tarea completó sin datos de resultado.")
        
        try:
            response_df = pd.read_json(data_json_str, orient='records')
        except Exception as e:
            background_tasks.add_task(delete_task_from_db, task_id)
            logger.error(f"Error al deserializar el DataFrame de la BBDD para {task_id}: {e}")
            raise HTTPException(status_code=500, detail="Error al procesar el resultado guardado.")
        
        background_tasks.add_task(delete_task_from_db, task_id)

        if formato_salida == 'csv':
            stream = io.StringIO()
            response_df.to_csv(stream, index=False)
            return StreamingResponse(iter([stream.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f"attachment;filename=auditoria_{task_id}.csv"})
        elif formato_salida == 'xlsx':
            stream = io.BytesIO()
            response_df.to_excel(stream, engine='openpyxl', index=False)
            stream.seek(0)
            return StreamingResponse(stream, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment;filename=auditoria_{task_id}.xlsx"})
        else:
            # El frontend interpreta "html" como "ya se subió a BBDD", 
            # así que devolvemos la respuesta que confirma el éxito.
            return HTMLResponse(content=response_df.to_html(index=False))

    raise HTTPException(status_code=400, detail="Estado de la tarea desconocido.")

@router.get("/Auditoria/transcripcion/{id_interaccion}", tags=["AuditorIA"])
def obtener_transcripcion(id_interaccion: str):
    """
    Obtiene el JSON completo de la transcripción para visualizar en el modal.
    """
    # Usamos una consulta directa simple para obtener el JSON del chat
    query = text("""
        SELECT TOP 1 segments, metadata 
        FROM [Acme].[calidad].[transcripciones] 
        WHERE IdAplicativo = :id
    """)
    
    try:
        with engine.connect() as conn:
            result = conn.execute(query, {"id": id_interaccion}).mappings().first()
            
        if not result:
            raise HTTPException(status_code=404, detail="Transcripción no encontrada")
            
        return {
            "segments": result["segments"], # Esto debería ser un string JSON
            "metadata": result["metadata"]
        }
    except Exception as e:
        logger.error(f"Error al obtener transcripción {id_interaccion}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/Auditoria/audio/{id_aplicativo}", tags=["AuditorIA"])
def obtener_audio(id_aplicativo: str, descargar: bool = Query(False)):
    """Sirve el audio conservado de una interacción (Opus/Ogg).

    Usa FileResponse, que soporta Range/206 (seeking del reproductor). `descargar=1`
    fuerza la descarga como adjunto; por defecto se sirve inline para reproducir. El
    audio se filtra por entorno dentro de audio_store (ver AudioAuditoria.Entorno)."""
    resuelto = audio_store.resolver_audio(engine, id_aplicativo)
    if not resuelto:
        raise HTTPException(status_code=404, detail="Audio no disponible")
    ruta, mime, nombre = resuelto
    if descargar:
        return FileResponse(ruta, media_type=mime, filename=nombre)
    return FileResponse(ruta, media_type=mime, headers={"Content-Disposition": "inline"})


class AudiosExistentesRequest(BaseModel):
    ids: List[str] = []


@router.post("/Auditoria/audios/existentes", tags=["AuditorIA"])
def audios_existentes(payload: AudiosExistentesRequest):
    """Dado un lote de IdAplicativo, devuelve el subconjunto que tiene audio conservado
    (para pintar el botón "Escuchar" solo en las filas que sí lo tienen)."""
    disponibles = audio_store.ids_con_audio(engine, payload.ids)
    return {"ids": sorted(disponibles)}


class TranscripcionesRequest(BaseModel):
    ids: List[str] = []


# Tope por pedido: encolar es barato para el que aprieta el botón pero cada llamado se
# paga en Gemini. Un tope evita el "seleccionar todo" de 5000 filas de un clic.
MAX_TRANSCRIPCIONES_POR_PEDIDO = 500


@router.post("/Auditoria/transcripciones/encolar", tags=["AuditorIA"])
def encolar_transcripciones(payload: TranscripcionesRequest,
                            background_tasks: BackgroundTasks,
                            current_user: User = Depends(get_current_active_user)):
    """Encola la transcripción de llamados YA auditados que no la tienen.

    No transcribe DENTRO de la request: deja el pedido en calidad.TranscripcionJobs y
    responde al instante, usando el audio ya conservado (no se vuelve a descargar del
    grabador ni se re-audita).

    Qué pasa después depende del tamaño del pedido (ver transcripcion_cola.elegir_motor):
      - Pedido chico (el auditor está escuchando ese llamado): motor FLEX, y el despacho
        arranca acá mismo como background task. Flex es sincrónico y cuesta lo mismo que
        batch, así que la transcripción suele estar en minutos; la pantalla la va a
        buscar sola con `/transcripciones/estado`.
      - Pedido grande: motor BATCH, lo toma el tick del scheduler y tarda horas.

    El tick del scheduler barre igual las dos colas, así que si la API se reinicia justo
    después de responder, el pedido no se pierde."""
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No se recibió ninguna interacción.")
    if len(payload.ids) > MAX_TRANSCRIPCIONES_POR_PEDIDO:
        raise HTTPException(
            status_code=400,
            detail=(f"Se pueden encolar hasta {MAX_TRANSCRIPCIONES_POR_PEDIDO} transcripciones "
                    f"por vez (llegaron {len(payload.ids)})."),
        )

    # Cupo mensual: una transcripción pedida a demanda se paga por llamado igual
    # que una auditoría, así que descuenta 1 por llamado. Se reserva ANTES de
    # encolar (el chequeo de saldo tiene que ser atómico) y se confirma abajo con
    # lo que REALMENTE entró en la cola: los que ya estaban transcriptos, ya en
    # cola o sin audio no cuestan nada y no deben descontar.
    reservas_cupo: dict = {}
    if not cuotas.esta_exento(current_user):
        with engine.connect() as conn:
            campanas = cuotas.campanas_de_interacciones(conn, payload.ids)
        por_campana: dict = {}
        for id_aplicativo in payload.ids:
            campana_id = campanas.get(str(id_aplicativo))
            if campana_id is not None:
                por_campana.setdefault(campana_id, []).append(str(id_aplicativo))
        try:
            for campana_id, ids_campana in por_campana.items():
                referencia = cuotas.referencia_transcripcion()
                cuotas.reservar(engine, campana_id=campana_id,
                                documento=current_user.usuario, cantidad=len(ids_campana),
                                tipo=cuotas.TIPO_TRANSCRIPCION, referencia=referencia)
                reservas_cupo[campana_id] = (referencia, set(ids_campana))
        except cuotas.CuotaExcedidaError as e:
            # Nada quedó encolado todavía: se devuelven las reservas ya tomadas de
            # las otras campañas del mismo pedido y se corta entero.
            for referencia, _ in reservas_cupo.values():
                cuotas.liberar(engine, referencia, "El pedido se rechazó por cupo.")
            raise HTTPException(status_code=403, detail=str(e))

    resultado = transcripcion_cola.encolar(engine, payload.ids, user_id=current_user.usuario)
    estados = transcripcion_cola.estados(engine, payload.ids)

    # Ajuste de la reserva a lo efectivamente encolado (se cierra en el acto: a
    # diferencia de una auditoría, acá ya se sabe el número final).
    encoladas = {str(i) for i in (resultado.get("encoladas") or [])}
    for referencia, ids_campana in reservas_cupo.values():
        cuotas.confirmar(engine, referencia, len(ids_campana & encoladas))

    # Los flex se mandan YA (después de responder, no antes: el llamado a Gemini puede
    # tardar minutos y la request tiene que volver en el acto). Si Auditor no levantó,
    # el cliente de Gemini no existe y quedan para el tick del scheduler.
    hay_flex = any((e or {}).get("motor") == transcripcion_cola.MOTOR_GEMINI_FLEX
                   and (e or {}).get("estado") == transcripcion_cola.PENDIENTE
                   for e in estados.values())
    if hay_flex and Auditor is not None:
        background_tasks.add_task(transcripcion_cola.despachar_flex_ahora, engine, Auditor.gemini)

    return {
        **resultado,
        "motor": transcripcion_cola.elegir_motor(len(resultado.get("encoladas") or [])),
        "estados": estados,
    }


@router.post("/Auditoria/transcripciones/estado", tags=["AuditorIA"])
def estado_transcripciones(payload: TranscripcionesRequest):
    """Estado del último pedido de transcripción de cada id (los que tengan alguno).

    La grilla lo usa para mostrar "en cola"/"procesando" en vez del botón de transcribir.
    """
    return {"estados": transcripcion_cola.estados(engine, payload.ids)}


# Columnas técnicas que NUNCA deben verse en UI o descargas planas.
# Se incluyen en el set retornado por el SP (flags / blobs), pero no son útiles para el analista.
COLUMNAS_TECNICAS_OCULTAS = {
    "TranscripcionJSON",
    "ExisteTranscripcion",
    "ResponseThoughts",
    "ExisteResponseThoughts",
}

# Columnas "core" que siempre deben estar disponibles para acciones (Ver transcripción, etc.).
# AuditoriaID viaja siempre aunque el usuario no la elija: es la clave del botón de
# revisión humana (Golden Set). Si el usuario no la pidió, el front la usa para la
# acción pero no la muestra como columna.
COLUMNAS_CORE_ACCIONES = {"AuditoriaID", "IdAplicativo", "idinteraccion",
                          "ExisteTranscripcion", "ExisteResponseThoughts"}

# Caché del resultado CRUDO del SP. El SP es lo caro de la pantalla: mientras el
# usuario ajusta filtros de columna o pasa de página, la consulta subyacente
# (fechas + empresa/campaña/plantilla) es la misma, así que se reutiliza lo ya
# traído. El botón "Buscar" manda `refrescar=1` y siempre va a la base.
_cache_auditorias = CacheConsultas(ttl_segundos=300, max_entradas=6)

PAGE_SIZE_DEFECTO = 100
PAGE_SIZE_MAX = 2000

# Columna técnica con la que el SP informa el total de filas cuando se le pide una
# página (ver scripts/migrations/2026-08-14b_*). Nunca sale hacia el front.
COL_TOTAL_SP = "__Total"

# El paginado en SQL depende de que esté aplicada la migración 2026-08-14b. Si el
# SP todavía es el viejo, la primera llamada con @Offset/@Fetch falla y a partir de
# ahí se usa el camino completo (traer todo y paginar en memoria), que funciona con
# las dos versiones. Así el deploy del código no depende del orden con la migración.
_sp_paginado_disponible = True


def _sp_soporta_paginado() -> bool:
    return _sp_paginado_disponible


def _seleccionar_columnas(columns_order: List[str], columnas: Optional[str],
                          incluir_transcripcion: bool, response_thoughts: bool):
    """Aplica la selección de columnas del cliente sobre el orden del SP.

    Devuelve (orden_final, permitidas | None). Si el cliente manda un subset se
    respeta el orden que eligió (drag-and-drop o plantilla de columnas guardada) y
    se preservan al final las columnas core (flags de acciones, IDs) y las técnicas
    pedidas explícitamente."""
    if not columnas:
        return columns_order, None

    user_order = [c.strip() for c in columnas.split(",") if c.strip()]
    user_set = set(user_order)
    permitidas = user_set | COLUMNAS_CORE_ACCIONES
    if incluir_transcripcion:
        permitidas.add("TranscripcionJSON")
    if response_thoughts:
        permitidas.add("ResponseThoughts")

    cols_sp = set(columns_order)
    ordenadas_usuario = [c for c in user_order if c in cols_sp]
    extras = [c for c in columns_order if c in permitidas and c not in user_set]
    return ordenadas_usuario + extras, permitidas


_QUERY_SP_PAGINADO = text("""
    EXEC calidad.sp_ObtenerAuditoriasFiltradas
        @AuditorUsuarioID = :usuario,
        @CampanaID = :campana,
        @EmpresaID = :empresa,
        @PlantillaID = :plantilla,
        @FechaDesde = :fecha_desde,
        @FechaHasta = :fecha_hasta,
        @FechaInteraccionDesde = :fi_desde,
        @FechaInteraccionHasta = :fi_hasta,
        @IdAplicativo = :id_aplicativo,
        @IncluirTranscripcion = :incluir_transcripcion,
        @IncluirResponseThoughts = :response_thoughts,
        @Offset = :offset,
        @Fetch = :fetch
""")


def _consultar_sp_paginado(params: dict, page: int, page_size: int):
    """Pide UNA página al SP, que ordena, pagina y recién ahí resuelve
    Agente/Equipo y la transcripción (lo caro es por fila).

    Devuelve (columns_order, filas, total, page) o None si el SP todavía no tiene
    el paginado (migración sin aplicar), para que el caller siga por el camino
    completo."""
    global _sp_paginado_disponible

    def _ejecutar(pagina_pedida: int):
        p = dict(params)
        p["offset"] = (pagina_pedida - 1) * page_size
        p["fetch"] = page_size
        with engine.connect() as conn:
            result = conn.execute(_QUERY_SP_PAGINADO, p)
            return list(result.keys()), [dict(r) for r in result.mappings().all()]

    try:
        columns_order, filas = _ejecutar(page)
    except Exception as e:
        # "Procedure ... has too many arguments specified" = SP viejo.
        mensaje = str(e).lower()
        if "too many arguments" in mensaje or "@offset" in mensaje:
            logger.warning("El SP no acepta paginado (¿migración 2026-08-14b sin aplicar?); "
                           "se usa el camino completo.")
            _sp_paginado_disponible = False
            return None
        raise

    # Si la página quedó vacía por estar más allá del final (los datos cambiaron
    # bajo los pies del usuario), volvemos a la primera: sin filas no viaja el
    # total y no habría con qué recalcular el paginado.
    if not filas and page > 1:
        page = 1
        columns_order, filas = _ejecutar(page)

    total = int(filas[0].get(COL_TOTAL_SP) or 0) if filas else 0
    columns_order = [c for c in columns_order if c != COL_TOTAL_SP]
    for fila in filas:
        fila.pop(COL_TOTAL_SP, None)

    return columns_order, filas, total, page


@router.get("/auditorias_realizadas/", tags=["AuditorIA"])
def auditorias_realizadas(
    fecha_desde: date,
    fecha_hasta: date,
    usuario: Optional[int] = None,
    empresa: Optional[int] = None,
    campana: Optional[int] = None,
    plantilla: Optional[int] = None,
    id_aplicativo: Optional[str] = None,
    base_fecha: str = Query("auditoria", description="Sobre qué fecha aplica el rango: 'auditoria' o 'interaccion' (fecha del llamado)."),
    incluir_transcripcion: Optional[bool] = Query(False, description="Si es True, devuelve el JSON completo de la transcripción en la descarga"),
    response_thoughts: Optional[bool] = Query(False, description="Si es True, incluye las 'response_thoughts' en el resultado"),
    columnas: Optional[str] = Query(None, description="CSV de columnas que el cliente quiere ver. Si llega, se filtra el set retornado (se preservan flags de acciones)."),
    filtros: Optional[str] = Query(None, description='JSON con filtros por columna: [{"col":"Agente","op":"en","val":["Ana"]}]. Ops: contiene/no_contiene/empieza/termina/igual/distinto/en/no_en/mayor/mayor_igual/menor/menor_igual/entre/vacio/no_vacio.'),
    page: int = Query(1, ge=1, description="Página a devolver (1-based)."),
    page_size: int = Query(PAGE_SIZE_DEFECTO, ge=0, le=PAGE_SIZE_MAX, description="Filas por página. 0 = todas (descargas)."),
    incluir_facetas: bool = Query(False, description="Si es True, agrega tipos por columna y valores distintos (para armar los controles de filtro)."),
    refrescar: bool = Query(False, description="Ignora la caché y vuelve a ejecutar el SP."),
    revision: Optional[str] = Query(None, description="Filtro de revisión: golden_set | para_reauditar | recomendadas | revisadas | sin_revisar. Ignora el rango de fechas."),
    golden_set_id: Optional[int] = Query(None, description="Acota 'golden_set' y 'para_reauditar' a un set puntual."),
    current_user: User = Depends(get_current_active_user),
):
    # Alcance por empresa: la salida del SP no trae EmpresaID (no se puede
    # post-filtrar), así que para usuarios acotados el filtro `empresa` es
    # obligatorio y se valida junto con campana/plantilla si vienen. El JS de
    # la página siempre manda los tres.
    with engine.connect() as conn:
        if empresa is None and empresas_permitidas(conn, current_user) is not None:
            raise HTTPException(
                status_code=403,
                detail="Tu acceso está acotado por empresa: indicá el filtro de empresa."
            )
        exigir_acceso_empresa(conn, current_user, empresa_id=empresa,
                              campana_id=campana, plantilla_id=plantilla)

    # El rango aplica sobre la fecha de auditoría (default) o la de interacción.
    # Se envían ambos pares de parámetros; el no usado va NULL (sin filtro).
    por_interaccion = (base_fecha or "auditoria").lower() == "interaccion"

    # --- Filtro de revisión (Golden Set / reauditar / recomendadas) ----------
    # Quien revisa vive en esta pantalla, no en la de Golden Set. El SP no sabe
    # nada de revisiones, así que el conjunto de AuditoriaID se resuelve aparte y
    # las filas se filtran contra él.
    #
    # El rango de fechas se DESCARTA a propósito: un Golden Set está armado para
    # cubrir períodos distintos, así que cualquier rango que el usuario tuviera
    # puesto se comería la mitad del conjunto sin avisar. En su lugar se usa el
    # rango que efectivamente cubren los llamados encontrados, que además mantiene
    # acotada la consulta al SP.
    ids_revision = None
    info_revision = None
    if revision:
        if plantilla is None:
            raise HTTPException(
                status_code=400,
                detail="Para filtrar por revisión hay que elegir una plantilla: "
                       "el Golden Set y la cobertura son de una plantilla puntual."
            )
        try:
            info_revision = golden_set.ids_para_filtro(
                engine, plantilla_id=plantilla, filtro=revision, golden_set_id=golden_set_id
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        ids_revision = set(info_revision["auditoria_ids"])
        if not ids_revision:
            # Sin llamados no hay nada que pedirle al SP: se contesta vacío en vez
            # de traer la plantilla entera para después descartarla toda.
            return {
                "columns": [], "data": [], "total": 0, "total_sin_filtros": 0,
                "page": 1, "page_size": page_size, "tipos": {}, "facetas": {},
                "desde_cache": False,
                "revision": {"filtro": revision, "total": 0, "motivos": {}},
            }

        if por_interaccion:
            fecha_desde = info_revision["desde_interaccion"] or fecha_desde
            fecha_hasta = info_revision["hasta_interaccion"] or fecha_hasta
        else:
            fecha_desde = info_revision["desde_auditoria"] or fecha_desde
            fecha_hasta = info_revision["hasta_auditoria"] or fecha_hasta
        fecha_desde = getattr(fecha_desde, "date", lambda: fecha_desde)()
        fecha_hasta = getattr(fecha_hasta, "date", lambda: fecha_hasta)()
        # Un día de margen a cada lado: el rango derivado es de datetimes y no se
        # sabe si el SP compara contra el inicio o el fin del día. Como después se
        # filtra por AuditoriaID exacto, traer de más no cambia el resultado —
        # traer de menos perdería llamados del conjunto sin que nadie se entere.
        fecha_desde = fecha_desde - timedelta(days=1)
        fecha_hasta = fecha_hasta + timedelta(days=1)

    query = text("""
        EXEC calidad.sp_ObtenerAuditoriasFiltradas
            @AuditorUsuarioID = :usuario,
            @CampanaID = :campana,
            @EmpresaID = :empresa,
            @PlantillaID = :plantilla,
            @FechaDesde = :fecha_desde,
            @FechaHasta = :fecha_hasta,
            @FechaInteraccionDesde = :fi_desde,
            @FechaInteraccionHasta = :fi_hasta,
            @IdAplicativo = :id_aplicativo,
            @IncluirTranscripcion = :incluir_transcripcion,
            @IncluirResponseThoughts = :response_thoughts
    """)

    params = {
        "usuario": usuario,
        "campana": campana,
        "empresa": empresa,
        "plantilla": plantilla,
        "fecha_desde": None if por_interaccion else fecha_desde,
        "fecha_hasta": None if por_interaccion else fecha_hasta,
        "fi_desde": fecha_desde if por_interaccion else None,
        "fi_hasta": fecha_hasta if por_interaccion else None,
        "id_aplicativo": id_aplicativo,
        "incluir_transcripcion": incluir_transcripcion,
        "response_thoughts": response_thoughts
    }

    # Clave de caché: el resultado del SP depende solo de estos parámetros. Se
    # incluye al usuario para no compartir filas entre cuentas con distinto alcance.
    clave_cache = CacheConsultas.clave(
        uid=current_user.usuario, usuario=usuario, empresa=empresa, campana=campana,
        plantilla=plantilla, fecha_desde=fecha_desde, fecha_hasta=fecha_hasta,
        base_fecha=("interaccion" if por_interaccion else "auditoria"),
        id_aplicativo=id_aplicativo, transcripcion=bool(incluir_transcripcion),
        thoughts=bool(response_thoughts),
        revision=revision or "", golden_set_id=golden_set_id or 0,
    )
    # Los sets "pesados" (descarga Completa con transcripciones y CoT) no se
    # cachean: son de un solo uso y ocuparían decenas de MB.
    cacheable = not (incluir_transcripcion or response_thoughts)

    # Los filtros se parsean antes de consultar: saber si hay alguno decide si se
    # puede paginar en SQL. Los nombres de columna se validan después, contra el
    # result set real.
    try:
        filtros_parseados = parsear_filtros(filtros)
    except FiltroInvalido as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        cacheado = None if (refrescar or not cacheable) else _cache_auditorias.obtener(clave_cache)

        # Camino rápido: sin filtros de columna, sin facetas y sin caché, el SP
        # ordena, pagina y enriquece SOLO la página pedida (la resolución de
        # Agente/Equipo y la transcripción son por fila: hacerlas sobre 100 filas
        # en vez de miles es de donde sale la mejora). Con la caché en memoria ya
        # cargada conviene el otro camino, que ni siquiera toca la base.
        if (cacheado is None and page_size > 0 and not filtros_parseados
                and not incluir_facetas and ids_revision is None
                and _sp_soporta_paginado()):
            paginado = _consultar_sp_paginado(params, page, page_size)
            if paginado is not None:
                columns_order, pagina, total, page = paginado
                columns_order, permitidas = _seleccionar_columnas(
                    columns_order, columnas, incluir_transcripcion, response_thoughts)
                if permitidas is not None:
                    pagina = [{k: v for k, v in fila.items() if k in permitidas} for fila in pagina]
                return {
                    "columns": columns_order,
                    "data": pagina,
                    "total": total,
                    "total_sin_filtros": total,   # sin filtros de columna, es el mismo
                    "page": page,
                    "page_size": page_size,
                    "tipos": {},
                    "facetas": {},
                    "desde_cache": False,
                }

        if cacheado is not None:
            columns_order, rows = cacheado
            desde_cache = True
        else:
            with engine.connect() as conn:
                result = conn.execute(query, params)
                columns_order = list(result.keys())
                rows = [dict(r) for r in result.mappings().all()]
            desde_cache = False
            if cacheable:
                _cache_auditorias.guardar(clave_cache, columns_order, rows)

        # El conjunto de revisión se aplica ANTES que todo lo demás: así los
        # totales, las facetas y los filtros por columna hablan del subconjunto
        # que el usuario está mirando y no del set completo de la plantilla.
        if ids_revision is not None:
            rows = [f for f in rows if f.get("AuditoriaID") in ids_revision]

        columns_order, permitidas = _seleccionar_columnas(
            columns_order, columnas, incluir_transcripcion, response_thoughts)

        # --- Filtros por columna ---------------------------------------------
        # Se aplican sobre las filas crudas (todas las columnas del SP), así un
        # filtro sigue valiendo aunque su columna no esté entre las visibles.
        try:
            validar_columnas(filtros_parseados, rows[0].keys() if rows else columns_order)
        except FiltroInvalido as e:
            raise HTTPException(status_code=400, detail=str(e))

        filas_filtradas = aplicar_filtros(rows, filtros_parseados)

        # --- Metadata para los controles de filtro ----------------------------
        # Tipos y valores distintos se calculan sobre el set SIN filtros de columna:
        # así las opciones del multiselect no se achican al tildar una y el usuario
        # puede sumar valores sin tener que limpiar lo anterior.
        columnas_visibles = [c for c in columns_order if c not in COLUMNAS_TECNICAS_OCULTAS]
        tipos: Dict[str, str] = {}
        facetas: Dict[str, list] = {}
        if incluir_facetas and rows:
            tipos = inferir_tipos(rows, columnas_visibles)
            facetas = calcular_facetas(rows, columnas_visibles)

        # --- Paginado ---------------------------------------------------------
        # La proyección de columnas se hace DESPUÉS de paginar: se arman 100 dicts
        # en lugar de miles.
        total = len(filas_filtradas)
        pagina = paginar(filas_filtradas, page, page_size)
        if permitidas is not None:
            pagina = [{k: v for k, v in fila.items() if k in permitidas} for fila in pagina]

        return {
            "columns": columns_order,
            "data": pagina,
            "total": total,                 # filas que pasan los filtros de columna
            "total_sin_filtros": len(rows),  # filas que devolvió el SP
            "page": page if page_size > 0 else 1,
            "page_size": page_size,
            "tipos": tipos,
            "facetas": facetas,
            "desde_cache": desde_cache,
            # Los motivos viajan por separado (no como columna) porque son de la
            # revisión, no de la auditoría: qué criterio destraba este llamado, o
            # qué atributos cambiaron desde que se lo auditó.
            "revision": None if info_revision is None else {
                "filtro": revision,
                "total": info_revision["total"],
                "truncado": info_revision.get("truncado", False),
                "motivos": {str(k): v for k, v in info_revision["motivos"].items()},
                "desde": info_revision["desde_interaccion" if por_interaccion else "desde_auditoria"],
                "hasta": info_revision["hasta_interaccion" if por_interaccion else "hasta_auditoria"],
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en auditorias_realizadas: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Columnas fijas que devuelve sp_ObtenerAuditoriasFiltradas (set superset).
# El SP puede no incluir algunas según la plantilla (p.ej. PuntajeFinal/EsErrorCritico solo cuando
# la plantilla tiene atributos de tipo 'calidad'); por eso este listado es solo para los toggles
# de la UI — el filtrado real se hace contra `columns_order` recibido del cursor.
COLUMNAS_FIJAS_AUDITORIAS = [
    "AuditoriaID",
    "AuditorUsuarioID",
    "IdAplicativo",
    "operadorUsuario",
    "Equipo",
    "Agente",
    "Legajo",
    "sentido_interaccion",
    "tipificacion_interaccion",
    "duracion_segundos",
    "comentario_interaccion",
    "fecha_interaccion",
    "FechaAuditoria",
    "extras",
    "PuntajeFinal",
    "EsErrorCritico",
]


@lru_cache(maxsize=1)
def _hay_columna_incidencia() -> bool:
    """¿Está aplicada la migración 2026-08-18b (calidad.Auditorias.Incidencia)?

    Si no lo está, el SP no devuelve esa columna y ofrecerla en el panel sería un chip
    que no hace nada. Se resuelve UNA vez por proceso (igual criterio que
    `_sp_paginado_disponible`): si la migración se aplica con el servicio arriba, la
    columna aparece recién al reiniciar.
    """
    return tiene_columna_auditorias(engine, "Incidencia")


@router.get("/Auditoria/auditorias/columnas", tags=["AuditorIA"])
def descubrir_columnas_auditoria(
    plantilla: int = Query(..., description="ID de plantilla — define las columnas dinámicas (atributos) que retornará el SP."),
    current_user: User = Depends(get_current_active_user),
):
    """
    Devuelve la lista de columnas que retornará el SP `sp_ObtenerAuditoriasFiltradas`
    para esta plantilla, sin ejecutar la consulta sobre las auditorías reales.

    Se compone de:
      - Columnas fijas (AuditoriaID, IdAplicativo, ...) en el mismo orden del SELECT final del SP.
      - Atributos dinámicos de la plantilla, ordenados por su campo `orden`.

    Usa la definición de la plantilla (sp_ObtenerPlantillaCompleta) en lugar del SP de
    auditorías, para no depender de que existan auditorías cargadas con esa plantilla.
    """
    if plantillas_manager_instance is None:
        raise HTTPException(status_code=503, detail="Plantillas Manager no disponible.")

    with engine.connect() as conn:
        exigir_acceso_empresa(conn, current_user, plantilla_id=plantilla)

    try:
        plantilla_data = plantillas_manager_instance.obtener_plantilla(plantilla)
    except Exception as e:
        logger.error(f"Error obteniendo plantilla {plantilla} para discovery: {e}")
        raise HTTPException(status_code=500, detail=f"Error consultando la plantilla: {e}")

    if not plantilla_data:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada.")

    atributos = plantilla_data.get("atributos") or []
    atributos_ordenados = sorted(
        atributos,
        key=lambda a: (a.get("orden") if a.get("orden") is not None else 9999),
    )
    nombres_atributos = [a.get("nombre") for a in atributos_ordenados if a.get("nombre")]

    # Columnas fijas que siempre devuelve el SP
    base_fijas = [
        "AuditoriaID", "AuditorUsuarioID", "IdAplicativo",
        "operadorUsuario", "Equipo", "Agente", "Legajo",
        "sentido_interaccion", "tipificacion_interaccion", "duracion_segundos", "comentario_interaccion", "fecha_interaccion", "FechaAuditoria", "extras",
    ]

    # Columnas de resultado. El SP las devuelve SIEMPRE, tenga o no la plantilla
    # atributos de "Calidad ponderada" (`critical_audit`): en una plantilla sin ellos,
    # PuntajeFinal viene NULL y EsErrorCritico en falso, pero las columnas están.
    #
    # Antes se ofrecían solo cuando la plantilla tenía atributos ponderados, y el efecto
    # era el peor de los dos mundos: en las demás plantillas (Voltara, por ejemplo) esas
    # columnas aparecían igual en la grilla y en las descargas, pero al no estar en el
    # descubrimiento no había forma de apagarlas ni de moverlas — y encima desaparecían
    # sin querer apenas el usuario apagaba cualquier otra columna, porque la selección
    # que viajaba no las incluía. Ofrecerlas siempre las vuelve configurables como el
    # resto, sin cambiar lo que se ve por defecto (el panel arranca con todo activo).
    columnas_resultado = ["PuntajeFinal"]
    if _hay_columna_incidencia():
        columnas_resultado.append("Incidencia")
    columnas_resultado.append("EsErrorCritico")

    columnas = base_fijas + nombres_atributos + columnas_resultado
    return {"columns": columnas}


# =========================================================
#   PLANTILLAS DE COLUMNAS (privadas por usuario)
# =========================================================

@router.get("/Auditoria/column-templates/", tags=["AuditorIA"], response_model=List[ColumnTemplateResponse])
def listar_column_templates(
    empresa: Optional[int] = None,
    campana: Optional[int] = None,
    plantilla: Optional[int] = None,
    current_user: User = Depends(get_current_active_user),
):
    """
    Lista las plantillas de columnas del usuario.
    Si se pasa empresa/campana/plantilla, filtra a las que matcheen ese contexto
    (más las "globales" del usuario que no tengan contexto seteado).
    """
    base_sql = """
        SELECT id, name, columns_json, empresa, campana, plantilla_id, updated_at
        FROM [calidad].[AuditColumnTemplates]
        WHERE user_id = :uid
    """
    params = {"uid": current_user.usuario}

    if empresa is not None and campana is not None and plantilla is not None:
        base_sql += " AND (empresa IS NULL OR empresa = :empresa)"
        base_sql += " AND (campana IS NULL OR campana = :campana)"
        base_sql += " AND (plantilla_id IS NULL OR plantilla_id = :plantilla)"
        params.update({"empresa": empresa, "campana": campana, "plantilla": plantilla})

    base_sql += " ORDER BY name ASC"

    try:
        with engine.connect() as conn:
            rows = conn.execute(text(base_sql), params).mappings().all()
    except Exception as e:
        logger.error(f"Error listando column templates: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    out: List[ColumnTemplateResponse] = []
    for r in rows:
        try:
            cols = json.loads(r["columns_json"]) if r["columns_json"] else []
        except json.JSONDecodeError:
            cols = []
        out.append(ColumnTemplateResponse(
            id=r["id"],
            name=r["name"],
            columns=cols,
            empresa=r["empresa"],
            campana=r["campana"],
            plantilla_id=r["plantilla_id"],
            updated_at=r["updated_at"].strftime("%Y-%m-%d %H:%M") if r["updated_at"] else None,
        ))
    return out


@router.get("/Auditoria/column-templates/{template_id}", tags=["AuditorIA"], response_model=ColumnTemplateResponse)
def obtener_column_template(
    template_id: int,
    current_user: User = Depends(get_current_active_user),
):
    query = text("""
        SELECT id, name, columns_json, empresa, campana, plantilla_id, updated_at
        FROM [calidad].[AuditColumnTemplates]
        WHERE id = :id AND user_id = :uid
    """)
    try:
        with engine.connect() as conn:
            r = conn.execute(query, {"id": template_id, "uid": current_user.usuario}).mappings().first()
    except Exception as e:
        logger.error(f"Error obteniendo column template {template_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    if not r:
        raise HTTPException(status_code=404, detail="Plantilla de columnas no encontrada")

    try:
        cols = json.loads(r["columns_json"]) if r["columns_json"] else []
    except json.JSONDecodeError:
        cols = []

    return ColumnTemplateResponse(
        id=r["id"],
        name=r["name"],
        columns=cols,
        empresa=r["empresa"],
        campana=r["campana"],
        plantilla_id=r["plantilla_id"],
        updated_at=r["updated_at"].strftime("%Y-%m-%d %H:%M") if r["updated_at"] else None,
    )


@router.post("/Auditoria/column-templates/", tags=["AuditorIA"], status_code=201)
def crear_column_template(
    req: ColumnTemplateCreateRequest,
    current_user: User = Depends(get_current_active_user),
):
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="El nombre de la plantilla no puede estar vacío.")
    if not req.columns:
        raise HTTPException(status_code=400, detail="Debes incluir al menos una columna.")

    insert = text("""
        INSERT INTO [calidad].[AuditColumnTemplates]
            (user_id, name, columns_json, empresa, campana, plantilla_id)
        OUTPUT inserted.id
        VALUES (:uid, :name, :cols, :emp, :camp, :pid)
    """)
    params = {
        "uid": current_user.usuario,
        "name": req.name.strip(),
        "cols": json.dumps(req.columns),
        "emp": req.empresa,
        "camp": req.campana,
        "pid": req.plantilla_id,
    }
    try:
        with engine.begin() as conn:
            new_id = conn.execute(insert, params).scalar()
        return {"id": new_id, "message": "Plantilla creada."}
    except Exception as e:
        msg = str(e)
        if "UQ_AuditColTpl_UserName" in msg:
            raise HTTPException(status_code=409, detail="Ya tenés una plantilla con ese nombre.")
        logger.error(f"Error creando column template: {e}")
        raise HTTPException(status_code=500, detail=msg)


@router.put("/Auditoria/column-templates/{template_id}", tags=["AuditorIA"])
def actualizar_column_template(
    template_id: int,
    req: ColumnTemplateUpdateRequest,
    current_user: User = Depends(get_current_active_user),
):
    sets = []
    params: dict = {"id": template_id, "uid": current_user.usuario}

    if req.name is not None:
        if not req.name.strip():
            raise HTTPException(status_code=400, detail="El nombre no puede estar vacío.")
        sets.append("name = :name")
        params["name"] = req.name.strip()
    if req.columns is not None:
        if not req.columns:
            raise HTTPException(status_code=400, detail="Debes incluir al menos una columna.")
        sets.append("columns_json = :cols")
        params["cols"] = json.dumps(req.columns)

    if not sets:
        raise HTTPException(status_code=400, detail="No hay cambios para aplicar.")

    sets.append("updated_at = GETDATE()")
    query = text(f"""
        UPDATE [calidad].[AuditColumnTemplates]
        SET {", ".join(sets)}
        WHERE id = :id AND user_id = :uid
    """)
    try:
        with engine.begin() as conn:
            result = conn.execute(query, params)
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Plantilla no encontrada.")
        return {"message": "Plantilla actualizada."}
    except HTTPException:
        raise
    except Exception as e:
        msg = str(e)
        if "UQ_AuditColTpl_UserName" in msg:
            raise HTTPException(status_code=409, detail="Ya tenés una plantilla con ese nombre.")
        logger.error(f"Error actualizando column template {template_id}: {e}")
        raise HTTPException(status_code=500, detail=msg)


@router.delete("/Auditoria/column-templates/{template_id}", tags=["AuditorIA"])
def eliminar_column_template(
    template_id: int,
    current_user: User = Depends(get_current_active_user),
):
    query = text("""
        DELETE FROM [calidad].[AuditColumnTemplates]
        WHERE id = :id AND user_id = :uid
    """)
    try:
        with engine.begin() as conn:
            result = conn.execute(query, {"id": template_id, "uid": current_user.usuario})
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Plantilla no encontrada.")
        return {"message": "Plantilla eliminada."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error eliminando column template {template_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def _exigir_scope_scheduler(current_user: User, request: SchedulerCreateRequest) -> None:
    """Alcance por empresa al crear/editar un scheduler: empresa/campana vienen
    como strings; la plantilla resuelve la empresa aunque aquellos no parseen.
    (También se revalida al correr, en tasks.procesar_tarea_individual.)"""
    with engine.connect() as conn:
        exigir_acceso_empresa(
            conn, current_user,
            empresa_id=int(request.empresa) if request.empresa and request.empresa.isdigit() else None,
            campana_id=int(request.campana) if request.campana and request.campana.isdigit() else None,
            plantilla_id=request.plantilla_id,
        )


def _exigir_modo_scheduler(current_user: User, request: SchedulerCreateRequest) -> None:
    """Una programación sincrónica (is_batch=False en parametros_json) repite el
    gasto del modo caro en cada corrida, así que exige el mismo audit:sync que
    la auditoría manual."""
    if not (request.parametros_json or {}).get("is_batch"):
        exigir_modo_sincronico(current_user, contexto="programación de auditoría")


def _exigir_cupo_scheduler(current_user: User, request: SchedulerCreateRequest) -> None:
    """Una programación audita sola todos los días y no pasa por POST /Auditar/,
    así que NO descuenta cupo: sería la puerta de atrás del sistema de cupos.
    Por eso a quien está alcanzado por un cupo no se le deja programar sobre una
    campaña que lo tiene. (Programar ya requiere audit:scheduler, que es un
    permiso aparte y poco repartido; esto cierra el caso de quien tenga los dos.)"""
    if cuotas.esta_exento(current_user):
        return
    with engine.connect() as conn:
        campana_id = cuotas.resolver_campana(conn, request.campana, request.plantilla_id)
        if cuotas.tiene_tope(conn, campana_id):
            raise HTTPException(
                status_code=403,
                detail=("Esta campaña tiene cupo mensual de auditorías: las corridas "
                        "programadas no descuentan cupo, así que se configuran desde "
                        "Calidad. Auditá a demanda desde \"Auditar\" o pedile al gerente "
                        "de operaciones que la programación la cargue Calidad."),
            )


@router.post("/Auditoria/scheduler/", tags=["Scheduler"], dependencies=[Depends(require_scheduler)])
def create_scheduler(
    request: SchedulerCreateRequest,
    current_user: User = Depends(get_current_active_user)
):
    _exigir_scope_scheduler(current_user, request)
    _exigir_modo_scheduler(current_user, request)
    _exigir_cupo_scheduler(current_user, request)

    next_run = calcular_next_run(request.frecuencia, request.hora_ejecucion, request.dias)
    dias_str = ",".join(request.dias) if request.dias else None
    
    # Entorno: la tarea la ejecuta el scheduler del servidor donde se creó
    # (run_pending_schedulers filtra por él). Editarla no lo cambia.
    query = text("""
        INSERT INTO [calidad].[AuditSchedulers]
        (task_name, frecuencia, hora_ejecucion, dias_semana, empresa, campana, plantilla_id,
         cantidad, rango_dinamico, parametros_json, send_email, email_addresses,
         send_gsheets, gsheet_id, gsheet_name, next_run_time, created_by, column_template_id,
         Entorno)
        VALUES
        (:tname, :freq, :hora, :dias, :emp, :camp, :pid, :cant, :rango, :params,
         :s_email, :emails, :s_sheet, :sheet_id, :sheet_name, :next_run, :uid, :col_tpl,
         :env)
    """)

    params = {
        "tname": request.task_name, "freq": request.frecuencia, "hora": request.hora_ejecucion,
        "dias": dias_str, "emp": request.empresa, "camp": request.campana, "pid": request.plantilla_id,
        "cant": request.cantidad, "rango": request.rango_dinamico,
        "params": json.dumps(request.parametros_json),
        "s_email": 1 if request.send_email else 0, "emails": request.email_addresses,
        "s_sheet": 1 if request.send_gsheets else 0, "sheet_id": request.gsheet_id, "sheet_name": request.gsheet_name,
        "next_run": next_run, "uid": current_user.usuario,
        "col_tpl": request.column_template_id, "env": settings.ENVIRONMENT,
    }

    try:
        with engine.begin() as conn:
            conn.execute(query, params)
        return {"message": "Programación guardada exitosamente", "next_run": next_run.strftime("%Y-%m-%d %H:%M")}
    except Exception as e:
        logger.error(f"Error creando scheduler: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/Auditoria/scheduler/", tags=["Scheduler"], dependencies=[Depends(require_scheduler)])
def get_schedulers(current_user: User = Depends(get_current_active_user)):
    query = text("""
        SELECT s.id, s.task_name, s.frecuencia, s.hora_ejecucion, e.Nombre as empresa,
               s.is_active, s.next_run_time, s.send_email, s.send_gsheets,
               c.Nombre as campana_nombre, s.campana, s.Entorno
        FROM [calidad].[AuditSchedulers] s
        LEFT JOIN [calidad].[Campanas] c ON TRY_CAST(s.campana AS INT) = c.CampanaID
        LEFT JOIN [calidad].Empresas e ON TRY_CAST(s.empresa AS INT) = e.EmpresaID
        ORDER BY s.created_at DESC
    """)
    
    try:
        with engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
            
        result = []
        for r in rows:
            destinos = []
            if r['send_email']: destinos.append("Email")
            if r['send_gsheets']: destinos.append("Sheets")
            
            result.append({
                "id": r['id'],
                "task_name": r['task_name'],
                "frecuencia": f"{r['frecuencia'].capitalize()} ({r['hora_ejecucion']})",
                "hora_ejecucion": str(r['hora_ejecucion']),
                "empresa": r['empresa'],
                "campana_nombre": r['campana_nombre'] or r['campana'],
                "is_active": bool(r['is_active']),
                "next_run_time": r['next_run_time'].strftime("%Y-%m-%d %H:%M") if r['next_run_time'] else "No programado",
                "destinos": " + ".join(destinos) if destinos else "BBDD",
                # Se listan las de los dos entornos, pero cada scheduler corre solo las
                # suyas: la pantalla marca las que ejecuta el otro servidor.
                "entorno": r['Entorno'],
                "otro_entorno": r['Entorno'] != settings.ENVIRONMENT,
            })
        return result
    except Exception as e:
        logger.error(f"Error listando schedulers: {e}")
        raise HTTPException(status_code=500, detail="Error obteniendo programaciones")

@router.put("/Auditoria/scheduler/{scheduler_id}/toggle", tags=["Scheduler"], dependencies=[Depends(require_scheduler)])
def toggle_scheduler(scheduler_id: int, current_user: User = Depends(get_current_active_user)):
    query = text("""
        UPDATE [calidad].[AuditSchedulers] 
        SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END
        WHERE id = :id
    """)
    try:
        with engine.begin() as conn:
            conn.execute(query, {"id": scheduler_id})
        return {"message": "Estado modificado correctamente."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/Auditoria/scheduler/{scheduler_id}", tags=["Scheduler"], dependencies=[Depends(require_scheduler)])
def delete_scheduler(
    scheduler_id: int, 
    current_user: User = Depends(get_current_active_user)
):
    """
    Elimina una programación de auditoría (scheduler) existente.
    """
    query = text("""
        DELETE FROM [calidad].[AuditSchedulers]
        WHERE id = :id
    """)
    
    try:
        with engine.begin() as conn:
            result = conn.execute(query, {"id": scheduler_id})
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Programación no encontrada.")
                
        return {"message": "Programación eliminada exitosamente."}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error eliminando scheduler {scheduler_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al eliminar la programación: {str(e)}")


@router.put("/Auditoria/scheduler/{scheduler_id}", tags=["Scheduler"], dependencies=[Depends(require_scheduler)])
def update_scheduler(
    scheduler_id: int,
    request: SchedulerCreateRequest,
    current_user: User = Depends(get_current_active_user)
):
    """
    Edita una programación de auditoría (scheduler) existente.
    """
    _exigir_scope_scheduler(current_user, request)
    _exigir_modo_scheduler(current_user, request)
    _exigir_cupo_scheduler(current_user, request)

    # Reutilizamos la función que calcula la próxima ejecución (asumiendo que ya está importada en tu archivo)
    next_run = calcular_next_run(request.frecuencia, request.hora_ejecucion, request.dias)
    dias_str = ",".join(request.dias) if request.dias else None
    
    query = text("""
        UPDATE [calidad].[AuditSchedulers]
        SET task_name = :tname,
            frecuencia = :freq,
            hora_ejecucion = :hora,
            dias_semana = :dias,
            empresa = :emp,
            campana = :camp,
            plantilla_id = :pid,
            cantidad = :cant,
            rango_dinamico = :rango,
            parametros_json = :params,
            send_email = :s_email,
            email_addresses = :emails,
            send_gsheets = :s_sheet,
            gsheet_id = :sheet_id,
            gsheet_name = :sheet_name,
            next_run_time = :next_run,
            column_template_id = :col_tpl
        WHERE id = :id
    """)

    params = {
        "id": scheduler_id,
        "tname": request.task_name,
        "freq": request.frecuencia,
        "hora": request.hora_ejecucion,
        "dias": dias_str,
        "emp": request.empresa,
        "camp": request.campana,
        "pid": request.plantilla_id,
        "cant": request.cantidad,
        "rango": request.rango_dinamico,
        "params": json.dumps(request.parametros_json),
        "s_email": 1 if request.send_email else 0,
        "emails": request.email_addresses,
        "s_sheet": 1 if request.send_gsheets else 0,
        "sheet_id": request.gsheet_id,
        "sheet_name": request.gsheet_name,
        "next_run": next_run,
        "col_tpl": request.column_template_id,
    }
    
    try:
        with engine.begin() as conn:
            result = conn.execute(query, params)
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Programación no encontrada.")
                
        return {
            "message": "Programación actualizada exitosamente", 
            "next_run": next_run.strftime("%Y-%m-%d %H:%M")
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error editando scheduler {scheduler_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error al editar la programación: {str(e)}")

@router.get("/Auditoria/scheduler/{scheduler_id}", tags=["Scheduler"], dependencies=[Depends(require_scheduler)])
def get_scheduler(
    scheduler_id: int, 
    current_user: User = Depends(get_current_active_user)
):
    """
    Obtiene toda la información de una programación de auditoría (scheduler) específica.
    """
    query = text("""
        SELECT s.*, c.Nombre as campana_nombre, e.Nombre as empresa_nombre
        FROM [calidad].[AuditSchedulers] s
        LEFT JOIN [calidad].Empresas e ON TRY_CAST(s.empresa AS INT) = e.EmpresaID
        LEFT JOIN [calidad].[Campanas] c ON TRY_CAST(s.campana AS INT) = c.CampanaID
        WHERE s.id = :id
    """)
    
    try:
        with engine.connect() as conn:
            row = conn.execute(query, {"id": scheduler_id}).mappings().first()
            
        if not row:
            raise HTTPException(status_code=404, detail="Programación no encontrada.")
            
        # Convertimos el resultado a un diccionario
        result = dict(row)
        
        # Formateamos las fechas a strings legibles
        if result.get("next_run_time"):
            result["next_run_time"] = result["next_run_time"].strftime("%Y-%m-%d %H:%M")
        if result.get("created_at"):
            result["created_at"] = result["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        if result.get("updated_at"):
            result["updated_at"] = result["updated_at"].strftime("%Y-%m-%d %H:%M:%S")
            
        # El campo parametros_json suele guardarse como string en la DB, lo parseamos a diccionario
        if result.get("parametros_json") and isinstance(result["parametros_json"], str):
            try:
                result["parametros_json"] = json.loads(result["parametros_json"])
            except json.JSONDecodeError:
                pass # Si falla el parseo, se deja como string
                
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error obteniendo la información del scheduler {scheduler_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error obteniendo la programación: {str(e)}")

# ============================================================================ #
# Revisión humana de auditorías (Golden Set — Fase 0)                          #
# ============================================================================ #
# La IA audita, pero nadie sabía cuánto acierta: no había dónde registrar que se
# equivocó. Estos endpoints capturan esa verdad humana, que es el insumo del
# evaluador (scripts/eval_auditoria.py) y, más adelante, del proponente de
# prompts. Ver AuditorIA/revision.py y AuditorIA/golden_set.py.
require_review = RoleChecker(["audit:review"])
require_goldenset = RoleChecker(["goldenset:manage"])
# Leer la medición (sin poder tocar los sets) alcanza con cualquiera de los dos.
require_evaluacion = RoleChecker(["audit:review", "goldenset:manage"])
# Reauditar REEMPLAZA una auditoría publicada (la nota que el operador ya vio
# cambia) y gasta tokens: no alcanza con poder auditar llamados nuevos.
require_reauditar = RoleChecker(["audit:reauditar"])


class VeredictoAtributo(BaseModel):
    atributo_id: int
    # None significa "no correspondía responder este atributo" (el caso de los
    # atributos opcionales). Es distinto de no mandar el atributo: lo que no
    # viene en la lista, no se revisó.
    valor_humano: Optional[Any] = None
    motivo: Optional[str] = None


class RevisionRequest(BaseModel):
    veredictos: List[VeredictoAtributo] = []
    comentario: Optional[str] = None


class RevisionesExistentesRequest(BaseModel):
    auditoria_ids: List[int] = []
    solo_mias: bool = False


class ReauditarRequest(BaseModel):
    """Reauditar reemplaza auditorías YA PUBLICADAS: se pide la lista explícita de
    IDs, nunca un filtro. Un filtro puede traer más de lo que el usuario vio en
    pantalla, y acá cada fila de más es una nota de operador que cambia."""
    auditoria_ids: List[int]
    motivo: Optional[str] = None
    batch: bool = True


class AporteRequest(BaseModel):
    """Las auditorías visibles en el listado + la plantilla a la que pertenecen.
    La plantilla no se deduce de los IDs a propósito: el listado puede mezclar
    plantillas y la grilla de cobertura es de una sola."""
    plantilla_id: int
    auditoria_ids: List[int] = []


class GoldenSetCreateRequest(BaseModel):
    nombre: str
    plantilla_id: int
    descripcion: Optional[str] = None


class GoldenSetItemsRequest(BaseModel):
    auditoria_ids: List[int] = []
    split: Optional[str] = None   # None = reparto automático 60/40
    notas: Optional[str] = None


def _exigir_acceso_auditoria(auditoria_id: int, user: User) -> dict:
    """Valida el alcance por empresa de UNA auditoría y devuelve su contexto.

    La auditoría no lleva el alcance encima: hay que resolver su empresa/campaña
    antes de mostrarla o dejar que la corrijan, igual que hace el listado.
    """
    with engine.connect() as conn:
        fila = conn.execute(text("""
            SELECT TOP 1 AuditoriaID, EmpresaID, CampanaID, PlantillaID, IdAplicativo
            FROM calidad.Auditorias WHERE AuditoriaID = :aid
        """), {"aid": auditoria_id}).mappings().first()
        if fila is None:
            raise HTTPException(status_code=404, detail="Auditoría no encontrada.")
        exigir_acceso_empresa(conn, user, empresa_id=fila["EmpresaID"],
                              campana_id=fila["CampanaID"], plantilla_id=fila["PlantillaID"])
    return dict(fila)


@router.get("/Auditoria/revision/{auditoria_id}", tags=["GoldenSet"],
            dependencies=[Depends(require_review)])
def obtener_revision(auditoria_id: int, current_user: User = Depends(get_current_active_user)):
    """Atributos de una auditoría con lo que respondió la IA, para revisarlos.

    Trae también la revisión previa del usuario si ya la hizo (para editarla) y
    cuántas otras personas revisaron el mismo llamado.
    """
    _exigir_acceso_auditoria(auditoria_id, current_user)
    try:
        datos = revision.obtener_para_revision(engine, auditoria_id, revisor_id=current_user.usuario)
    except Exception as e:
        logger.error(f"Error al preparar la revisión de la auditoría {auditoria_id}: {e}")
        raise HTTPException(status_code=500, detail=f"No se pudo cargar la auditoría: {e}")

    if datos is None:
        raise HTTPException(status_code=404, detail="Auditoría no encontrada.")
    return datos


@router.post("/Auditoria/revision/{auditoria_id}", tags=["GoldenSet"],
             dependencies=[Depends(require_review)])
def guardar_revision_auditoria(
    auditoria_id: int,
    payload: RevisionRequest,
    current_user: User = Depends(get_current_active_user),
):
    """Guarda el veredicto humano sobre los atributos de una auditoría.

    No modifica la auditoría publicada: la corrección vive aparte (ver
    AuditorIA/revision.py). Re-enviar pisa la revisión anterior del mismo usuario.
    """
    _exigir_acceso_auditoria(auditoria_id, current_user)
    try:
        return revision.guardar_revision(
            engine,
            auditoria_id=auditoria_id,
            revisor_id=current_user.usuario,
            veredictos=[v.model_dump() for v in payload.veredictos],
            comentario=payload.comentario,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error al guardar la revisión de la auditoría {auditoria_id}: {e}")
        raise HTTPException(status_code=500, detail=f"No se pudo guardar la revisión: {e}")


@router.delete("/Auditoria/revision/{auditoria_id}", tags=["GoldenSet"],
               dependencies=[Depends(require_review)])
def borrar_revision_auditoria(
    auditoria_id: int, current_user: User = Depends(get_current_active_user)
):
    """Borra la revisión propia (no la de otros revisores)."""
    _exigir_acceso_auditoria(auditoria_id, current_user)
    borrada = revision.borrar_revision(engine, auditoria_id=auditoria_id,
                                       revisor_id=current_user.usuario)
    if not borrada:
        raise HTTPException(status_code=404, detail="No tenías una revisión guardada.")
    return {"borrada": True}


@router.post("/Auditoria/revisiones/existentes", tags=["GoldenSet"],
             dependencies=[Depends(require_review)])
def revisiones_existentes(
    payload: RevisionesExistentesRequest,
    current_user: User = Depends(get_current_active_user),
):
    """Dado un lote de AuditoriaID, cuáles ya tienen revisión (para marcarlas en
    el listado y no revisar dos veces lo mismo)."""
    revisor = current_user.usuario if payload.solo_mias else None
    return {"ids": sorted(revision.ids_revisados(engine, payload.auditoria_ids, revisor_id=revisor))}


# ---------------------------------------------------------------------------- #
# Golden Sets                                                                   #
# ---------------------------------------------------------------------------- #
# Listar es de solo lectura y lo necesita quien revisa desde Auditorías Realizadas
# (para acotar el filtro a un set puntual), que puede no administrar sets. Crear,
# borrar y tocar items siguen exigiendo `goldenset:manage`.
@router.get("/Auditoria/golden-sets/", tags=["GoldenSet"],
            dependencies=[Depends(require_evaluacion)])
def listar_golden_sets(
    plantilla: Optional[int] = None,
    current_user: User = Depends(get_current_active_user),
):
    if plantilla is not None:
        with engine.connect() as conn:
            exigir_acceso_empresa(conn, current_user, plantilla_id=plantilla)
    sets = golden_set.listar_sets(engine, plantilla_id=plantilla)

    # Sin filtro de plantilla hay que recortar por alcance: un set nombra la
    # plantilla de una empresa que el usuario puede no tener permitida.
    with engine.connect() as conn:
        permitidas = empresas_permitidas(conn, current_user)
        if permitidas is not None:
            visibles = []
            for s in sets:
                try:
                    exigir_acceso_empresa(conn, current_user, plantilla_id=s["PlantillaID"])
                    visibles.append(s)
                except HTTPException:
                    continue
            sets = visibles
    return {"sets": sets}


@router.post("/Auditoria/golden-sets/", tags=["GoldenSet"], status_code=201,
             dependencies=[Depends(require_goldenset)])
def crear_golden_set(
    payload: GoldenSetCreateRequest,
    current_user: User = Depends(get_current_active_user),
):
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, current_user, plantilla_id=payload.plantilla_id)
    try:
        set_id = golden_set.crear_set(
            engine, nombre=payload.nombre, plantilla_id=payload.plantilla_id,
            descripcion=payload.descripcion, user_id=current_user.usuario,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error al crear el golden set: {e}")
        raise HTTPException(status_code=500, detail=f"No se pudo crear el set: {e}")
    return {"golden_set_id": set_id}


def _exigir_acceso_golden_set(golden_set_id: int, user: User) -> dict:
    juego = golden_set.obtener_set(engine, golden_set_id)
    if juego is None:
        raise HTTPException(status_code=404, detail="Golden set no encontrado.")
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, user, plantilla_id=juego["PlantillaID"])
    return juego


@router.get("/Auditoria/golden-sets/{golden_set_id}/items", tags=["GoldenSet"],
            dependencies=[Depends(require_goldenset)])
def listar_golden_set_items(
    golden_set_id: int, current_user: User = Depends(get_current_active_user)
):
    juego = _exigir_acceso_golden_set(golden_set_id, current_user)
    return {"set": juego, "items": golden_set.listar_items(engine, golden_set_id)}


@router.post("/Auditoria/golden-sets/{golden_set_id}/items", tags=["GoldenSet"],
             dependencies=[Depends(require_goldenset)])
def agregar_golden_set_items(
    golden_set_id: int,
    payload: GoldenSetItemsRequest,
    current_user: User = Depends(get_current_active_user),
):
    """Suma auditorías al set y FIJA sus audios (quedan fuera del descarte FIFO
    del store, que si no borraría el set a las pocas semanas)."""
    _exigir_acceso_golden_set(golden_set_id, current_user)
    try:
        return golden_set.agregar_items(
            engine, golden_set_id=golden_set_id, auditoria_ids=payload.auditoria_ids,
            split=payload.split, user_id=current_user.usuario, notas=payload.notas,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error al agregar items al golden set {golden_set_id}: {e}")
        raise HTTPException(status_code=500, detail=f"No se pudieron agregar los llamados: {e}")


@router.delete("/Auditoria/golden-sets/items/{item_id}", tags=["GoldenSet"],
               dependencies=[Depends(require_goldenset)])
def quitar_golden_set_item(item_id: int, current_user: User = Depends(get_current_active_user)):
    with engine.connect() as conn:
        fila = conn.execute(text("""
            SELECT TOP 1 gs.PlantillaID FROM calidad.GoldenSetItems i
            JOIN calidad.GoldenSets gs ON gs.GoldenSetID = i.GoldenSetID
            WHERE i.ItemID = :iid
        """), {"iid": item_id}).mappings().first()
        if fila is None:
            raise HTTPException(status_code=404, detail="Item no encontrado.")
        exigir_acceso_empresa(conn, current_user, plantilla_id=fila["PlantillaID"])

    if not golden_set.quitar_item(engine, item_id):
        raise HTTPException(status_code=404, detail="Item no encontrado.")
    return {"quitado": True}


@router.delete("/Auditoria/golden-sets/{golden_set_id}", tags=["GoldenSet"],
               dependencies=[Depends(require_goldenset)])
def desactivar_golden_set(
    golden_set_id: int, current_user: User = Depends(get_current_active_user)
):
    _exigir_acceso_golden_set(golden_set_id, current_user)
    golden_set.desactivar_set(engine, golden_set_id)
    return {"desactivado": True}


@router.get("/Auditoria/golden-sets/candidatas", tags=["GoldenSet"],
            dependencies=[Depends(require_goldenset)])
def candidatas_golden_set(
    plantilla: int = Query(..., description="Plantilla de la que sacar candidatas."),
    cantidad: int = Query(21, ge=3, le=90),
    solo_con_audio: bool = Query(True),
    current_user: User = Depends(get_current_active_user),
):
    """Auditorías sin revisar sugeridas para armar un set.

    No son las últimas N: en una campaña sana casi todo da OK y un set así no
    tendría casi NO OK ni EC, que es donde el prompt se rompe. Y no alcanza con
    estratificar por resultado del llamado: se elige por COBERTURA DE ATRIBUTOS
    (ver AuditorIA/golden_muestreo.py), porque un llamado "con fallas" puede
    fallar siempre en el mismo atributo y dejar al resto sin poder medirse.

    Cada fila viene con `motivos`: qué celda (atributo × valor) llena. Es lo que
    permite explicarle a Calidad por qué justo esos llamados.
    """
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, current_user, plantilla_id=plantilla)

    recomendacion = golden_set.recomendar_para_revisar(
        engine, plantilla_id=plantilla, cantidad=cantidad, solo_con_audio=solo_con_audio
    )
    return {
        "cobertura": golden_set.cobertura(engine, plantilla),
        # `candidatas` se mantiene con el nombre viejo: es lo que consume la
        # pantalla del Golden Set, ahora ordenado por aporte a la grilla.
        "candidatas": recomendacion["recomendadas"],
        "cobertura_atributos": recomendacion["cobertura"],
        "aporte": recomendacion["aporte"],
    }


@router.get("/Auditoria/golden-sets/cobertura-atributos", tags=["GoldenSet"],
            dependencies=[Depends(require_evaluacion)])
def cobertura_atributos_golden_set(
    plantilla: int = Query(..., description="Plantilla a diagnosticar."),
    golden_set_id: Optional[int] = Query(None, description="Acotar a un set."),
    current_user: User = Depends(get_current_active_user),
):
    """De qué criterios sabemos algo y de cuáles no sabemos nada.

    `cobertura` cuenta llamados revisados; esto cuenta celdas (atributo × valor).
    Un atributo con 40 revisiones todas en OK aparece cubierto en la primera y
    sin medir en esta, que es la que decide si el prompt se puede corregir.
    """
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, current_user, plantilla_id=plantilla)
    datos = golden_set.cobertura_por_atributo(
        engine, plantilla, golden_set_id=golden_set_id
    )
    # Las claves con guion bajo son insumo interno del muestreo, no de la API.
    return {k: v for k, v in datos.items() if not k.startswith("_")}


@router.get("/Auditoria/golden-sets/reauditar", tags=["GoldenSet"],
            dependencies=[Depends(require_evaluacion)])
def candidatas_reauditar(
    plantilla: int = Query(..., description="Plantilla a diagnosticar."),
    golden_set_id: Optional[int] = Query(None, description="Acotar a un set."),
    cantidad: int = Query(200, ge=1, le=1000),
    current_user: User = Depends(get_current_active_user),
):
    """Llamados con verdad humana cuya respuesta de la IA quedó vieja.

    Al tocar el prompt de un atributo, las auditorías anteriores conservan lo que
    contestó la versión vieja: medir el prompt nuevo contra esos valores da un
    resultado falso. La revisión humana sigue valiendo — lo que hay que rehacer es
    la corrida de la IA sobre el mismo audio (por eso los audios del set se fijan).
    """
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, current_user, plantilla_id=plantilla)
    return golden_set.candidatas_para_reauditar(
        engine, plantilla_id=plantilla, golden_set_id=golden_set_id, cantidad=cantidad
    )


@router.post("/Auditoria/revisiones/aporte", tags=["GoldenSet"],
             dependencies=[Depends(require_review)])
def aporte_de_auditorias(
    payload: AporteRequest,
    current_user: User = Depends(get_current_active_user),
):
    """De las auditorías que hay en pantalla, cuáles conviene revisar y por qué.

    Es la contracara de `/golden-sets/candidatas` para el listado de Auditorías
    Realizadas: ahí el usuario elige a mano, así que se informa el aporte de cada
    llamado por separado a la grilla (atributo × valor) en vez de una selección
    codiciosa que dependería del orden en que aparecen.
    """
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, current_user, plantilla_id=payload.plantilla_id)
    datos = golden_set.evaluar_aporte(
        engine, plantilla_id=payload.plantilla_id, auditoria_ids=payload.auditoria_ids
    )
    versiones = reauditoria.versiones_por_auditoria(engine, payload.auditoria_ids)
    for auditoria_id, info in datos.items():
        info["versiones"] = versiones.get(auditoria_id, 1)
    return {"aportes": {str(k): v for k, v in datos.items()}}


# ---------------------------------------------------------------------------- #
# Reauditar                                                                     #
# ---------------------------------------------------------------------------- #
# Vuelve a pasar la IA sobre el MISMO audio con el prompt vigente y reemplaza la
# auditoría EN SU LUGAR: mismo AuditoriaID, una sola fila. La corrida anterior se
# archiva en calidad.AuditoriaVersiones. Así los dashboards, el listado, los
# exports y el promedio del operador siguen viendo siempre la última versión sin
# que nadie tenga que acordarse de filtrar, y la evidencia de lo anterior queda.
@router.post("/Auditoria/reauditar/preparar", tags=["Reauditar"],
             dependencies=[Depends(require_reauditar)])
def preparar_reauditoria(
    payload: ReauditarRequest,
    current_user: User = Depends(get_current_active_user),
):
    """Qué se puede reauditar y qué no, SIN gastar un token.

    La mitad de los rechazos son previsibles (sin audio conservado, de otra
    plantilla) y enterarse recién después de esperar dos minutos y pagar los tokens
    es la peor forma de enterarse.
    """
    max_batch = int(getattr(settings, "REAUDITAR_MAX_LLAMADOS_BATCH", 1000))
    if len(payload.auditoria_ids) > max_batch:
        raise HTTPException(
            status_code=400,
            detail=f"Reauditá de a {max_batch} llamados como máximo por corrida."
        )

    datos = reauditoria.preparar(engine, payload.auditoria_ids)
    if datos["plantillas"]:
        with engine.connect() as conn:
            for plantilla_id in datos["plantillas"]:
                exigir_acceso_empresa(conn, current_user, plantilla_id=plantilla_id)
    return {
        "puede": [
            {"auditoria_id": i["auditoria_id"], "id_aplicativo": i["IdAplicativo"],
             "operador": i["operadorUsuario"], "fecha_interaccion": i["fecha_interaccion"]}
            for i in datos["listas"]
        ],
        "rechazadas": datos["rechazadas"],
        "plantillas": datos["plantillas"],
        "versionado_listo": reauditoria.hay_versionado(engine),
    }


@router.post("/Auditoria/reauditar", tags=["Reauditar"],
             dependencies=[Depends(require_reauditar)])
def ejecutar_reauditoria(
    payload: ReauditarRequest,
    current_user: User = Depends(get_current_active_user),
):
    """Reaudita los llamados indicados con el prompt vigente. GASTA TOKENS.

    Por defecto se ejecuta mediante Gemini Batch API (modo batch) para procesar
    hasta 1.000 llamadas sin riesgo de timeout y con 50% de descuento en tokens.
    """
    if not payload.auditoria_ids:
        raise HTTPException(status_code=400, detail="No indicaste ningún llamado.")

    max_batch = int(getattr(settings, "REAUDITAR_MAX_LLAMADOS_BATCH", 1000))
    max_sync = int(getattr(settings, "REAUDITAR_MAX_LLAMADOS_SYNC", 10))

    if len(payload.auditoria_ids) > max_batch:
        raise HTTPException(
            status_code=400,
            detail=f"Reauditá de a {max_batch} llamados como máximo por corrida en modo Batch."
        )

    if not payload.batch and len(payload.auditoria_ids) > max_sync:
        raise HTTPException(
            status_code=400,
            detail=f"Para más de {max_sync} llamados se debe usar el modo Batch (batch=true)."
        )

    preparado = reauditoria.preparar(engine, payload.auditoria_ids)
    with engine.connect() as conn:
        for plantilla_id in preparado["plantillas"]:
            exigir_acceso_empresa(conn, current_user, plantilla_id=plantilla_id)

    try:
        modo = "batch" if payload.batch else "sync"
        return reauditoria.reauditar(
            engine,
            auditoria_ids=payload.auditoria_ids,
            usuario_id=current_user.usuario,
            motivo=payload.motivo,
            modo=modo,
        )
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Falló la reauditoría")
        raise HTTPException(status_code=500, detail=f"No se pudo reauditar: {e}")


@router.get("/Auditoria/auditoria/{auditoria_id}/versiones", tags=["Reauditar"],
            dependencies=[Depends(require_review)])
def versiones_de_auditoria(
    auditoria_id: int,
    current_user: User = Depends(get_current_active_user),
):
    """El historial de corridas de un llamado: qué contestó cada una y qué cambió.

    Lo puede ver quien revisa aunque no pueda reauditar: es la evidencia de por qué
    la nota es la que es, y sin poder consultarla el archivo no sirve de nada.
    """
    datos = reauditoria.historial(engine, auditoria_id)
    if datos["auditoria"] is None:
        raise HTTPException(status_code=404, detail="No existe esa auditoría.")
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, current_user,
                              plantilla_id=datos["auditoria"].get("PlantillaID"))
    return datos


@router.get("/Auditoria/golden-sets/vigencia", tags=["GoldenSet"],
            dependencies=[Depends(require_goldenset)])
def vigencia_golden_set(
    plantilla: int = Query(..., description="Plantilla a diagnosticar."),
    golden_set_id: Optional[int] = Query(None, description="Acotar a un set."),
    current_user: User = Depends(get_current_active_user),
):
    """¿La verdad humana sigue valiendo o la campaña cambió el criterio abajo?

    Compara la versión de plantilla con la que cada revisión sentó criterio contra
    la versión actual y marca SOLO los atributos que cambiaron en el medio: el set
    no se vence entero, se vencen los atributos que cambiaron.
    """
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, current_user, plantilla_id=plantilla)
    return golden_set.diagnostico_vigencia(
        engine, plantilla_id=plantilla, golden_set_id=golden_set_id
    )


# ---------------------------------------------------------------------------- #
# Evaluación de plantillas (Golden Set — Fase 1, para la pantalla)              #
# ---------------------------------------------------------------------------- #
# Todo lo que devuelve sale de SQL: no gasta un token y se puede recargar sin
# miedo. El replay (re-auditar con la plantilla actual) NO está acá a propósito:
# tarda minutos y cuesta plata, así que sigue siendo una corrida explícita por
# consola (scripts/eval_auditoria.py --replay).


@router.get("/Auditoria/evaluacion", tags=["GoldenSet"],
            dependencies=[Depends(require_evaluacion)])
def evaluacion_plantilla(
    plantilla: int = Query(..., description="Plantilla a evaluar."),
    golden_set_id: Optional[int] = Query(None, description="Acotar a un Golden Set."),
    split: Optional[str] = Query(None, description="'train' | 'test' (solo con golden_set_id)."),
    current_user: User = Depends(get_current_active_user),
):
    """Todo lo que necesita la pantalla de evaluación, en un solo llamado.

    Se devuelve junto y no en cinco endpoints porque son cinco lecturas sobre el
    mismo conjunto de revisiones: pedirlas por separado significaría recorrerlas
    cinco veces y dejar que la pantalla muestre partes inconsistentes entre sí.
    """
    with engine.connect() as conn:
        exigir_acceso_empresa(conn, current_user, plantilla_id=plantilla)

    if split is not None and split not in ("train", "test"):
        raise HTTPException(status_code=400, detail="split debe ser 'train' o 'test'.")

    try:
        casos = golden_set.cargar_casos(
            engine, golden_set_id=golden_set_id, plantilla_id=plantilla, split=split
        )
        resumen = golden_metricas.evaluar(casos)

        # Acuerdo humano-humano: el techo de la evaluación. Si dos analistas no se
        # ponen de acuerdo, esa parte de la brecha no se arregla tocando el prompt.
        por_revisor = golden_set.cargar_casos_por_revisor(
            engine, golden_set_id=golden_set_id, plantilla_id=plantilla
        )
        acuerdo = golden_metricas.acuerdo_entre_revisores(por_revisor)

        # Métricas separadas por versión de plantilla (Fase 2): el A/B histórico,
        # sin re-auditar nada.
        por_version = []
        agrupados: dict = {}
        for caso in casos:
            agrupados.setdefault(caso.version_id, []).append(caso)
        for version_id, grupo in sorted(agrupados.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)):
            detalle = versionado.obtener_version(engine, version_id) if version_id else None
            resumen_version = golden_metricas.evaluar(grupo)
            por_version.append({
                "version_id": version_id,
                "numero": detalle["Numero"] if detalle else None,
                "casos": resumen_version.casos,
                "accuracy": resumen_version.accuracy,
                "kappa": resumen_version.kappa_global,
                "mae": resumen_version.puntaje.mae,
                "falsos_ec": resumen_version.puntaje.ec_falsos,
            })

        return {
            "cobertura": golden_set.cobertura(engine, plantilla),
            "resumen": resumen.as_dict(),
            "acuerdo_humano": acuerdo,
            "vigencia": golden_set.diagnostico_vigencia(
                engine, plantilla_id=plantilla, golden_set_id=golden_set_id
            ),
            "por_version": por_version,
            # Umbral debajo del cual las métricas son ruido: un caso mueve varios
            # puntos de accuracy. La pantalla lo usa para avisar, no para ocultar.
            "minimo_recomendado": 30,
        }
    except Exception as e:
        logger.error(f"Error evaluando la plantilla {plantilla}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"No se pudo evaluar: {e}")
