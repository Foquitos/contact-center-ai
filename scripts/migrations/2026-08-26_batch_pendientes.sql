/* ============================================================================
   Feature — Cola de lotes de auditoría pendientes de enviar a Gemini
   Fecha: 2026-08-26
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   calidad.BatchPendientes — 1 fila = 1 lote de auditoría YA armado (audios
   comprimidos + prompts + metadata) que todavía no vive en Gemini.

   POR QUÉ
   -------
   La API de Gemini admite 100 batch jobs en estado no terminal a la vez (mismo
   cupo para las auditorías y para la cola de transcripciones). Hoy un lote entra
   ~16 llamados, así que una corrida de 2000 audios pide ~125 jobs de una sentada
   y el 22/08, sin nada raro, se llegó a ~91 en vuelo solo apilando corridas de
   la auditoría diaria CSV.

   Cuando `batches.create` rebota por cupo, el código de hoy imprime el error y
   descarta el lote (ver AuditorIA/gemini.py::process_batch): los audios de ese
   lote no se auditan, no queda fila en calidad.AuditExecutionLog, la tarea igual
   queda 'en_cola' y —en el camino CSV— la carpeta del fileserver se borra igual,
   así que esos audios se pierden para siempre.

   Con esta cola el lote no se descarta: se guarda en disco (el .jsonl con los
   audios ya comprimidos, listo para subir) y su metadata acá. El tick del
   scheduler lo manda apenas hay cupo. Para el usuario el estado sigue siendo
   "en cola": tarda más, no se pierde.

   POR QUÉ EL PAYLOAD VA A DISCO Y NO A ESTA TABLA
   ----------------------------------------------
   Un lote son megas de audio (Opus 32 kbps ~240 KB/min). El .jsonl vive en
   settings.BATCH_PENDIENTES_DIR y esta tabla solo guarda la ruta y la metadata
   que hace falta para armar calidad.Batch_data cuando el lote finalmente se
   crea y recién ahí tiene batch_id.

   ESTADOS
   -------
     PENDIENTE -> armado y esperando cupo. El .jsonl está en disco.
     ENVIANDO  -> el tick lo tomó y está subiendo/creando el job. Un proceso
                  caído acá lo deja colgado: al arrancar, el scheduler lo
                  devuelve a PENDIENTE (batch_cola.marcar_huerfanos).
     ENVIADO   -> vive en Gemini (BatchID). A partir de acá lo sigue el circuito
                  de siempre: calidad.BatchJobs + Auditor.check_batch_status.
     ERROR     -> se agotaron los intentos o el payload ya no está en disco. Sale
                  por el mail de problemas: son audios que NO se auditaron.

   AISLAMIENTO dev/prod
   --------------------
   El .jsonl vive en el disco de CADA servidor, así que la cola filtra por
   Entorno igual que calidad.AudioAuditoria y calidad.TranscripcionJobs.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario con DDL sobre el schema calidad.
   Idempotente: la tabla se crea solo si no existe. No toca datos existentes.
   ============================================================================ */

SET XACT_ABORT ON;
GO

IF OBJECT_ID('calidad.BatchPendientes', 'U') IS NULL
BEGIN
    CREATE TABLE calidad.BatchPendientes (
        LoteID        BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_BatchPendientes PRIMARY KEY,
        Entorno       NVARCHAR(20)   NOT NULL CONSTRAINT DF_BatchPendientes_Entorno  DEFAULT('prod'),
        Estado        NVARCHAR(20)   NOT NULL CONSTRAINT DF_BatchPendientes_Estado   DEFAULT('PENDIENTE'),

        -- Ata este lote a su CORRIDA: los N lotes de un run_batch comparten RunID,
        -- igual que en calidad.AuditExecutionLog. Es lo que permite avisar "la corrida
        -- terminó" recién cuando no queda ningún lote suyo pendiente.
        RunID         NVARCHAR(50)   NULL,

        -- Payload listo para subir (audios ya comprimidos + prompts, formato JSONL de
        -- la Batch API). Ruta absoluta en el disco de ESTE servidor.
        ArchivoJsonl  NVARCHAR(500)  NOT NULL,
        Bytes         BIGINT         NOT NULL CONSTRAINT DF_BatchPendientes_Bytes     DEFAULT(0),
        Llamados      INT            NOT NULL CONSTRAINT DF_BatchPendientes_Llamados  DEFAULT(0),

        -- Con qué se armó el lote. Se congela acá porque el envío puede ocurrir horas
        -- después y para entonces la plantilla puede tener otro modelo/nivel (mismo
        -- motivo por el que hoy viajan en calidad.Batch_data).
        Modelo        NVARCHAR(100)  NULL,
        PlantillaID   INT            NULL,
        UserID        INT            NULL,

        -- Todo lo que hoy se inserta en calidad.Batch_data (el melt del DataFrame) más
        -- el contexto de ejecución para abrir la fila de AuditExecutionLog. Se escribe
        -- en sus tablas definitivas recién al despachar, cuando existe el batch_id.
        MetadataJson  NVARCHAR(MAX)  NOT NULL,

        BatchID       NVARCHAR(300)  NULL,   -- job de Gemini (batches/xxxx), al despachar
        FechaAlta     DATETIME2      NOT NULL CONSTRAINT DF_BatchPendientes_Alta       DEFAULT(SYSUTCDATETIME()),
        FechaEnvio    DATETIME2      NULL,
        FechaFin      DATETIME2      NULL,
        Intentos      INT            NOT NULL CONSTRAINT DF_BatchPendientes_Intentos   DEFAULT(0),
        Error         NVARCHAR(1000) NULL
    );

    -- Tick del scheduler: tomar los pendientes de ESTE entorno en orden FIFO (un lote
    -- viejo no puede quedar atrás de uno nuevo: sus audios ya esperaron más).
    CREATE INDEX IX_BatchPendientes_Estado
        ON calidad.BatchPendientes (Entorno, Estado, LoteID);

    -- "¿Quedan lotes de esta corrida sin mandar?" — lo consulta el aviso de fin de
    -- corrida y la pantalla de estado de la tarea.
    CREATE INDEX IX_BatchPendientes_Run
        ON calidad.BatchPendientes (RunID)
        WHERE RunID IS NOT NULL;

    -- Vuelta del lote: ubicar el pendiente que se convirtió en un batch de Gemini.
    CREATE INDEX IX_BatchPendientes_Batch
        ON calidad.BatchPendientes (BatchID)
        WHERE BatchID IS NOT NULL;
END
GO
