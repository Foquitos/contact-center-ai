/* ============================================================================
   Feature — Encolar transcripciones desde "Auditorías Realizadas"
   Fecha: 2026-08-14
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   calidad.TranscripcionJobs — 1 fila = 1 pedido de transcripción para una
   interacción YA auditada que tiene el audio conservado (calidad.AudioAuditoria)
   pero NO tiene transcripción (calidad.transcripciones).

   POR QUÉ
   -------
   Hoy la transcripción solo se obtiene EN EL MOMENTO de auditar (viaja anidada
   en el mismo llamado de calidad, ver gemini.py::process_batch). Si la corrida no
   la pidió, el llamado queda sin transcripción para siempre: la única forma de
   conseguirla era re-auditar, o sea pagar de nuevo la auditoría completa.

   Con esta cola, desde el reproductor de audio (o seleccionando varias filas de
   "Auditorías Realizadas") se encola la transcripción y el scheduler la resuelve
   por Gemini en modo BATCH —la mitad de precio que el sync— sobre el audio que ya
   está en disco: no se vuelve a descargar del grabador ni se re-audita.

   MOTOR (por qué la columna existe)
   ---------------------------------
   'gemini_batch' es el motor de HOY. Cuando el servidor tenga GPU, el motor pasa
   a ser 'fastwhisper' (transcripción local, sincrónica y sin costo por token) y
   la misma cola lo despacha sin cambiar ni la UI ni esta tabla: solo cambia el
   valor de settings.TRANSCRIPCION_MOTOR. Las filas viejas conservan con qué motor
   se transcribieron.

   ESTADOS
   -------
     PENDIENTE -> encolado por el usuario, todavía no se mandó a ningún motor.
     ENVIANDO  -> el tick del scheduler lo tomó y está armando/mandando el lote.
                  Un proceso caído acá lo deja colgado: al arrancar, el scheduler
                  lo devuelve a PENDIENTE (transcripcion_cola.marcar_huerfanos).
     ENVIADO   -> vive en Gemini (BatchID + SegmentID); se espera el resultado.
     LISTO     -> la transcripción ya está en calidad.transcripciones.
     ERROR     -> se abandonó (sin audio, lote fallido o se agotaron los intentos).

   AISLAMIENTO dev/prod
   --------------------
   El audio conservado vive en el disco de CADA servidor (calidad.AudioAuditoria
   filtra por Entorno), así que la cola también: un job de dev apunta a un archivo
   que solo existe en el disco de dev. Mismo patrón que AudioAuditoria y
   pagina_web.ChatbotIndexJobs.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario con DDL sobre el schema calidad.
   Idempotente: la tabla se crea solo si no existe.
   ============================================================================ */

SET XACT_ABORT ON;
GO

IF OBJECT_ID('calidad.TranscripcionJobs', 'U') IS NULL
BEGIN
    CREATE TABLE calidad.TranscripcionJobs (
        JobID          BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_TranscripcionJobs PRIMARY KEY,
        Entorno        NVARCHAR(20)   NOT NULL CONSTRAINT DF_TranscripcionJobs_Entorno   DEFAULT('prod'),
        IdAplicativo   NVARCHAR(200)  NOT NULL,          -- misma clave que Auditorias/transcripciones/AudioAuditoria
        Estado         NVARCHAR(20)   NOT NULL CONSTRAINT DF_TranscripcionJobs_Estado    DEFAULT('PENDIENTE'),
        Motor          NVARCHAR(30)   NOT NULL CONSTRAINT DF_TranscripcionJobs_Motor     DEFAULT('gemini_batch'),
        BatchID        NVARCHAR(300)  NULL,              -- job de Gemini (batches/xxxx)
        SegmentID      INT            NULL,              -- índice de este pedido DENTRO del lote
        Modelo         NVARCHAR(100)  NULL,              -- modelo con el que se transcribió (costeo)
        SolicitadoPor  INT            NULL,              -- usuario que apretó "Transcribir"
        FechaSolicitud DATETIME2      NOT NULL CONSTRAINT DF_TranscripcionJobs_Solicitud DEFAULT(SYSUTCDATETIME()),
        FechaEnvio     DATETIME2      NULL,
        FechaFin       DATETIME2      NULL,
        Intentos       INT            NOT NULL CONSTRAINT DF_TranscripcionJobs_Intentos  DEFAULT(0),
        Error          NVARCHAR(1000) NULL
    );

    -- Una sola solicitud ABIERTA por interacción y entorno: encolar dos veces el
    -- mismo llamado (dos usuarios, o doble clic) no puede pagar dos transcripciones.
    -- Los jobs cerrados (LISTO/ERROR) quedan fuera del índice, así que un llamado
    -- que falló se puede volver a encolar.
    CREATE UNIQUE INDEX UQ_TranscripcionJobs_Abierto
        ON calidad.TranscripcionJobs (Entorno, IdAplicativo)
        WHERE Estado IN ('PENDIENTE', 'ENVIANDO', 'ENVIADO');

    -- Tick del scheduler: tomar los pendientes de ESTE entorno en orden FIFO.
    CREATE INDEX IX_TranscripcionJobs_Estado
        ON calidad.TranscripcionJobs (Entorno, Estado, JobID);

    -- Vuelta del lote: ubicar los jobs de un batch de Gemini que terminó.
    CREATE INDEX IX_TranscripcionJobs_Batch
        ON calidad.TranscripcionJobs (BatchID)
        WHERE BatchID IS NOT NULL;

    -- Pintar el estado en la grilla: se consulta por lote de IdAplicativo.
    CREATE INDEX IX_TranscripcionJobs_Id
        ON calidad.TranscripcionJobs (Entorno, IdAplicativo, JobID);
END
GO
