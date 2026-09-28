/* ============================================================================
   Feature — Log unificado de ejecuciones de AuditorIA
   Fecha: 2026-07-01
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   calidad.AuditExecutionLog — 1 fila = 1 corrida de auditoría completa (modo
   sync, disparada manual o por scheduler) o 1 job de Gemini (modo batch, desde
   que se sube hasta que Gemini devuelve resultado). Hoy esa información está
   repartida sin conexión entre calidad.AuditSchedulerHistory (solo scheduler,
   sin modo/tokens/duración), calidad.AuditTasks (solo manual, y nunca se
   cierra para batch) y pagina_web.IA_Uso (tokens por fila de consumo, no por
   corrida). Esta tabla es la fuente única para analizar corridas (SQL/Excel)
   y para armar el mail final con atributos que hoy no viajan: modo, origen
   (manual/scheduler y cuál), empresa/campaña/plantilla, rango de fechas y
   cantidad solicitada vs. auditada, duración, errores/omitidos dentro de la
   corrida y tokens consumidos.

   La pueblan (backend/AuditorIA/execution_log.py):
     - Auditor.py::run()                    -> 1 fila por corrida sync (abre y
                                                cierra en el mismo hilo).
     - AuditorIA/gemini.py::process_batch()  -> abre 1 fila por job de Gemini
                                                creado (modo batch), keyed por
                                                batch_id (cada chunk ≤1.5GB de
                                                una misma corrida es su propio
                                                job y su propia fila).
     - Auditor.py::procesar_batch()          -> cierra esa fila cuando Gemini
                                                devuelve resultado (horas
                                                después), agrupando por
                                                batch_id para no mezclar
                                                corridas de campañas/usuarios
                                                distintos que terminen en la
                                                misma ventana de polling.

   Nunca debe interrumpir una auditoría real: abrir/cerrar la fila está siempre
   en try/except silencioso (loguea y sigue) del lado Python.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario con DDL sobre el schema calidad.
   Idempotente: la tabla se crea solo si no existe.
   ============================================================================ */

SET XACT_ABORT ON;
GO

IF OBJECT_ID('calidad.AuditExecutionLog', 'U') IS NULL
BEGIN
    CREATE TABLE calidad.AuditExecutionLog (
        id                  BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_AuditExecutionLog PRIMARY KEY,
        trigger_source      NVARCHAR(20)  NOT NULL,             -- manual | scheduler
        scheduler_id        INT           NULL,                 -- calidad.AuditSchedulers.id (si trigger_source=scheduler)
        scheduler_name      NVARCHAR(200) NULL,                 -- desnormalizado: sobrevive si se borra/renombra el scheduler
        task_id             NVARCHAR(100) NULL,                 -- calidad.AuditTasks.task_id (si vino del endpoint manual)
        modo                NVARCHAR(10)  NOT NULL,             -- sync | batch
        batch_id            NVARCHAR(300) NULL,                 -- nombre del job de Gemini (solo modo batch); clave de cierre
        empresa             NVARCHAR(100) NULL,
        campana             NVARCHAR(100) NULL,
        plantilla_id        INT           NULL,
        user_id             NVARCHAR(50)  NULL,                 -- quién disparó la corrida (documento)
        fecha_desde         DATE          NULL,                 -- rango de fechas auditado (filtro de la corrida)
        fecha_hasta         DATE          NULL,
        cantidad_solicitada INT           NULL,
        filas_auditadas     INT           NULL,
        filas_error         INT           NULL,                 -- ítems que fallaron/se saltearon dentro de la corrida
        input_tokens        BIGINT        NULL,
        output_tokens       BIGINT        NULL,
        thoughts_tokens     BIGINT        NULL,
        status              NVARCHAR(20)  NOT NULL CONSTRAINT DF_AuditExecutionLog_status DEFAULT('EN_CURSO'),
                                                                 -- EN_CURSO | EXITO | PARCIAL | ERROR | SIN_DATOS
        error_message       NVARCHAR(MAX) NULL,
        mail_enviado        BIT           NOT NULL CONSTRAINT DF_AuditExecutionLog_mail DEFAULT(0),
        mail_destinatarios  NVARCHAR(500) NULL,
        gsheet_enviado      BIT           NOT NULL CONSTRAINT DF_AuditExecutionLog_gsheet DEFAULT(0),
        started_at          DATETIME2     NOT NULL CONSTRAINT DF_AuditExecutionLog_started DEFAULT(SYSUTCDATETIME()),
        finished_at         DATETIME2     NULL,
        duration_seconds    AS (DATEDIFF(SECOND, started_at, finished_at)), -- NULL mientras está EN_CURSO
        created_at          DATETIME2     NOT NULL CONSTRAINT DF_AuditExecutionLog_created DEFAULT(SYSUTCDATETIME())
    );

    CREATE INDEX IX_AuditExecutionLog_started   ON calidad.AuditExecutionLog (started_at);
    CREATE INDEX IX_AuditExecutionLog_scheduler ON calidad.AuditExecutionLog (scheduler_id, started_at);
    CREATE INDEX IX_AuditExecutionLog_status    ON calidad.AuditExecutionLog (status);
    -- Búsqueda por batch_id para cerrar la fila horas después (procesar_batch);
    -- único porque cada job de Gemini abre exactamente una fila.
    CREATE UNIQUE INDEX UX_AuditExecutionLog_batch_id ON calidad.AuditExecutionLog (batch_id)
        WHERE batch_id IS NOT NULL;
END
GO
