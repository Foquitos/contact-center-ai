import logging
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.executors.pool import ThreadPoolExecutor
from app.logging_config import setup_logging
# Importamos la nueva función desde tasks.py
from app.tasks import (
    check_completed_batches, auditoria_ALARMIX_diaria, auditoria_csv_diaria,
    run_pending_schedulers, sync_candidatos_sheet_to_sql_diario, cerrar_auditorias_huerfanas,
    procesar_cola_transcripciones, marcar_transcripciones_huerfanas,
    despachar_cola_lotes, marcar_lotes_huerfanos,
)
from app.chatbot_indexer import procesar_cola_reindexado, encolar_reindexado_todos, marcar_jobs_huerfanos
from app.doc_jobs import procesar_cola_docs, marcar_doc_jobs_huerfanos
from app.vacios_conocimiento import procesar_vacios_pendientes
from app.presupuesto_ia import verificar_presupuesto_ia
from app.cuotas import conciliar_pendientes as conciliar_cuotas
from app.salud_plantillas import reporte_semanal_plantillas
from app.config import settings
from app.scheduler_logging import instrumentar, silenciar_traceback_duplicado
from RRHH import procesar_aceptados_15min

setup_logging("scheduler")
silenciar_traceback_duplicado()
logger = logging.getLogger("SchedulerWorker")

executors = {
    'default': ThreadPoolExecutor(20) 
}

job_defaults = {
    'coalesce': False,             
    'max_instances': 3,            
    'misfire_grace_time': 3600     
}

def agregar_job(scheduler, func, *args, id, solo_prod: bool = False, **kwargs) -> None:
    """Registra un job envuelto para que sus logs lleven la etiqueta de la corrida.

    Todos los jobs se dan de alta por acá: uno registrado con add_job directo
    escribiría sin etiqueta y volvería a ser imposible de aislar en el journal
    cuando hay varias tareas corriendo a la vez.

    solo_prod: el job no es una cola particionada por entorno sino un trabajo fijo
    sobre algo compartido (el fileserver, mails a Calidad, tablas sin Entorno). Si
    corriera también en dev, un scheduler levantado ahí para probar algo lo haría
    dos veces. Se registra solo con ENVIRONMENT=prod; en dev se prueba llamando a
    la función a mano.
    """
    if solo_prod and settings.ENVIRONMENT != "prod":
        logger.info(f"Job NO registrado: {id} (corre solo en prod; ENVIRONMENT={settings.ENVIRONMENT})")
        return
    scheduler.add_job(instrumentar(func, id), *args, id=id, **kwargs)


def run():
    logger.info(f"--- Iniciando Proceso de Scheduler Dedicado [ENVIRONMENT={settings.ENVIRONMENT}] ---")

    # Jobs de reindexado que quedaron 'running' de un proceso anterior caído
    # bloquearían para siempre el claim de su chatbot.
    marcar_jobs_huerfanos()

    # Ídem para el asistente de documentación: si no, el usuario ve "procesando"
    # para siempre en una propuesta que ya nadie está generando.
    marcar_doc_jobs_huerfanos()

    # Mismo problema del lado de las auditorías interactivas: las que corrían en la API
    # cuando se reinició quedan en 'pending' y el frontend las muestra girando sin fin.
    cerrar_auditorias_huerfanas()

    # Y del lado de la cola de transcripciones: un pedido que se estaba mandando cuando
    # murió el proceso queda en ENVIANDO y nadie lo vuelve a tomar.
    marcar_transcripciones_huerfanas()

    # Ídem los lotes de auditoría que quedaron a mitad de envío (calidad.BatchPendientes):
    # sin esto se quedan en ENVIANDO y no los toma ningún tick.
    marcar_lotes_huerfanos()

    scheduler = BlockingScheduler(
        executors=executors,
        job_defaults=job_defaults,
        timezone="America/Argentina/Buenos_Aires"
    )

    # 1. Tarea de lotes (cada 5 min)
    agregar_job(scheduler, check_completed_batches, 'interval', minutes=15, id="check_batches_job")

    # 1.b Barrido de auditorías interactivas huérfanas (la API pudo reiniciarse mucho
    # después del arranque del scheduler, así que no alcanza con hacerlo solo al inicio).
    agregar_job(scheduler, cerrar_auditorias_huerfanas, 'interval', hours=1, id="auditorias_huerfanas_job")

    # 1.c Cola de transcripciones a demanda (calidad.TranscripcionJobs): manda a Gemini
    # los llamados que el usuario encoló desde "Auditorías Realizadas" y guarda los que
    # ya volvieron. Cada 5 min y no cada 15 como los batches de auditoría porque el
    # despacho es lo único que el usuario está esperando ver ("en cola" -> "procesando");
    # el resultado en sí tarda horas igual. max_instances=1: nunca dos ticks reclamando
    # los mismos pedidos.
    agregar_job(
        scheduler,
        procesar_cola_transcripciones,
        'interval',
        minutes=5,
        id="transcripciones_cola_job",
        max_instances=1,
        coalesce=True,
    )

    # 1.d Cola de lotes de auditoría (calidad.BatchPendientes): manda a Gemini los lotes
    # que se quedaron esperando cupo (100 jobs en vuelo como máximo en toda la cuenta,
    # compartidos con las transcripciones). Cada 5 min y no cada 15: cuanto antes salga
    # un lote, antes vuelve su resultado, y el cupo se libera de a poco.
    # max_instances=1: nunca dos ticks reclamando los mismos lotes.
    agregar_job(
        scheduler,
        despachar_cola_lotes,
        'interval',
        minutes=5,
        id="batch_cola_job",
        max_instances=1,
        coalesce=True,
    )

    # 1.e Conciliación de cupos de auditoría (calidad.CuotaConsumo): baja lo
    # RESERVADO al enviar el pedido a lo realmente auditado cuando la corrida
    # cerró, y devuelve el cupo de las que nunca llegaron a correr. Sin esto, una
    # campaña con cupo se quedaría sin saldo por corridas que auditaron de menos.
    # Cada 15 min, alineado con el polling de batches (que es lo que las cierra).
    agregar_job(
        scheduler,
        conciliar_cuotas,
        'interval',
        minutes=15,
        id="cuotas_conciliar_job",
        max_instances=1,
        coalesce=True,
        solo_prod=True,
    )

    # 2. Tarea de Auditoría Diaria Acumulativa
    # Ejecuta en la madrugada (06:00 AM) de Martes a Sábado (tue-sat)
    # scheduler.add_job(
    #     auditoria_ALARMIX_diaria, 
    #     'cron', 
    #     day_of_week='tue-sat', 
    #     hour=6, 
    #     minute=0, 
    #     id="auditoria_ALARMIX_diaria_job"
    # )

    # 3. Auditoría diaria de audios CSV subidos al fileserver (todos los días 04:00 AM)
    # Audita las carpetas de los dos orígenes (/mnt/fileserver_audios_CSV = "Audios
    # generales" y /mnt/fileserver_audios_CSV_VIP = "Audios VIP") bajo empresa 10 /
    # campaña 19 / plantilla 12, sube el resultado al Google Sheet que le corresponde a
    # cada origen y borra las carpetas ya auditadas.
    agregar_job(
        scheduler,
        auditoria_csv_diaria,
        'cron',
        hour=4,
        minute=0,
        id="auditoria_csv_diaria_job",
        solo_prod=True,
    )

    # Revisará cada 1 minuto si hay alguna tarea atrasada/pendiente en la BBDD
    agregar_job(scheduler, run_pending_schedulers, 'interval', minutes=5, id="dynamic_schedulers_job")

    # Presupuesto mensual de IA: compara el gasto del mes (vw_IA_Uso_Costos)
    # contra los umbrales configurados y avisa por mail (1 vez por umbral/mes,
    # dedup por tabla IA_Presupuesto_Alertas — correr seguido no duplica avisos).
    agregar_job(scheduler, verificar_presupuesto_ia, 'interval', hours=1, id="presupuesto_ia_job",
                solo_prod=True)

    # Salud de las plantillas de auditoría: relevamiento semanal de TODAS las plantillas
    # activas (estructura + redacción de los prompts) y mail a las jefaturas de Calidad
    # con lo que hay para revisar, ordenado por gravedad y por uso real.
    # Lunes 08:00: el chequeo ya existía como botón dentro del editor y no lo abría nadie
    # (ver app/salud_plantillas.py). No usa IA ni consume tokens.
    agregar_job(
        scheduler,
        reporte_semanal_plantillas,
        'cron',
        day_of_week='mon',
        hour=8,
        minute=0,
        id="salud_plantillas_semanal",
        max_instances=1,
        solo_prod=True,
    )

    # scheduler.add_job(
    #     sync_candidatos_sheet_to_sql_diario, 
    #     'cron', 
    #     hour=5, 
    #     minute=0, 
    #     id="sync_candidatos_diario"
    # )

    # # Procesar Aceptados cada 15 Minutos (Exporta a Sheets en bloque)
    # scheduler.add_job(
    #     procesar_aceptados_15min, 
    #     'interval', 
    #     minutes=15, 
    #     id="procesar_aceptados_bloque"
    # )
    
    # Cola de reindexado de chatbots (pagina_web.ChatbotIndexJobs): reemplaza el
    # flujo viejo de actualizar_rag.flag + reinicio del backend a medianoche.
    # Con Qdrant en modo servidor el índice nuevo se publica con un swap de alias
    # y los workers lo recargan solos, sin reinicio.
    # max_instances=1 + coalesce: nunca dos ticks drenando la cola a la vez.
    agregar_job(
        scheduler,
        procesar_cola_reindexado,
        'interval',
        seconds=60,
        id="chatbot_index_queue",
        max_instances=1,
        coalesce=True,
    )

    # Cola del asistente de documentación (pagina_web.ChatbotDocJobs): formatear y
    # agregar información tardan de 45s a 3min, así que no pueden correr dentro de
    # la request. Mucho más seguido que el reindexado (60s) porque acá hay alguien
    # esperando en pantalla; no más seguido que esto porque el tick se queda tomado
    # mientras corre el job, y cada tick salteado deja un warning de APScheduler.
    # max_instances=1 + coalesce: nunca dos ticks drenando la cola a la vez.
    agregar_job(
        scheduler,
        procesar_cola_docs,
        'interval',
        seconds=10,
        id="chatbot_doc_jobs_queue",
        max_instances=1,
        coalesce=True,
    )

    # Vacíos de conocimiento: clasifica y agrupa las consultas que el bot no pudo
    # responder, para que Calidad las triage. Corre acá y no en la request porque
    # incluye una llamada al LLM y un sondeo del corpus: nada de eso puede pagarlo
    # el operador esperando su respuesta. max_instances=1 evita que dos ticks tomen
    # las mismas filas pendientes.
    agregar_job(
        scheduler,
        procesar_vacios_pendientes,
        'interval',
        minutes=10,
        id="chatbot_vacios_conocimiento",
        max_instances=1,
        coalesce=True,
        solo_prod=True,
    )

    # DESACTIVADO (2026-07-13): el refresco nocturno reindexaba los 13 bots todas
    # las noches aunque los documentos no hubieran cambiado — trabajo (y tokens de
    # embedding) al pedo en la enorme mayoría de las corridas. El reindexado ahora
    # es a demanda: botón del panel de chatbots, o `python reindex_all.py all`.
    # La cola de arriba los sigue procesando igual.
    # scheduler.add_job(
    #     encolar_reindexado_todos,
    #     'cron',
    #     hour=0,
    #     minute=0,
    #     id="chatbot_reindex_nocturno"
    # )

    try:
        # print_jobs() escribe directo a stdout: en el journal esas líneas entran
        # sin timestamp ni prioridad y sueltas de todo lo demás. Logueadas, el
        # arranque queda como cualquier otro evento y se puede ver con -p info.
        for job in scheduler.get_jobs():
            logger.info(f"Job registrado: {job.id} ({job.trigger})")
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("--- Deteniendo Scheduler ---")

if __name__ == "__main__":
    run()
    
    # procesar_aceptados_15min()