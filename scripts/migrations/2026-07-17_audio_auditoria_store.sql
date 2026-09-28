/* ============================================================================
   Feature — Conservar el audio auditado para escucharlo/descargarlo después
   Fecha: 2026-07-17
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   calidad.AudioAuditoria — 1 fila = 1 audio comprimido (Opus/Ogg) conservado
   en disco tras auditar, para poder reproducirlo y descargarlo desde la
   pantalla "Auditorías Realizadas" (y seguir la transcripción sincronizada si
   existe). Hoy el audio se descarga, se comprime a Opus mono 16kHz/12kbps para
   subirlo a Gemini y luego se pierde (los físicos quedan en un tempdir efímero;
   los de memoria son BytesIO que se descartan).

   El archivo en sí NO vive en la BD: vive en disco bajo
   settings.AUDIO_STORE_DIR/<entorno>/<hash>.ogg. Esta tabla es solo el índice
   de metadatos (para chequear disponibilidad, servir y hacer el descarte FIFO).

   La clave IdAplicativo es la MISMA que liga calidad.Auditorias y
   calidad.transcripciones (ver AuditorIA/sql_a_Claude.py::calcular_id_aplicativo),
   así que el audio queda asociado a su auditoría y a su transcripción sin tocar
   sp_ObtenerAuditoriasFiltradas.

   AISLAMIENTO dev/prod
   --------------------
   dev y prod comparten el mismo SQL pero corren en servidores distintos (discos
   distintos). Por eso la tabla lleva Entorno ('prod' | 'dev') y todo el
   subsistema (guardar, servir, descartar) filtra por settings.ENVIRONMENT: una
   fila de dev apunta a un archivo que solo existe en el disco de dev. Mismo
   patrón que pagina_web.ChatbotIndexState / ChatbotIndexJobs.

   La pueblan (backend/AuditorIA/audio_store.py):
     - Auditor.py::run()                    -> guarda el audio de cada fila
                                               auditada (modo sync), justo tras
                                               generar la auditoría.
     - AuditorIA/gemini.py::process_batch()  -> guarda el audio de cada fila que
                                               se subió a Gemini (modo batch).

   Nunca debe interrumpir una auditoría real: guardar/descartar está siempre en
   try/except silencioso (loguea y sigue) del lado Python.

   DESCARTE (tope de 5 GB por entorno)
   -----------------------------------
   settings.AUDIO_STORE_MAX_BYTES (default 5 GB). Al superarlo se borran los
   audios MÁS ANTIGUOS (FechaCreacion ASC) —archivo + fila— hasta quedar bajo el
   tope. FechaUltimoAcceso se guarda para poder cambiar a LRU en el futuro sin
   otra migración.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario con DDL sobre el schema calidad.
   Idempotente: la tabla se crea solo si no existe.
   ============================================================================ */

SET XACT_ABORT ON;
GO

IF OBJECT_ID('calidad.AudioAuditoria', 'U') IS NULL
BEGIN
    CREATE TABLE calidad.AudioAuditoria (
        AudioID           BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_AudioAuditoria PRIMARY KEY,
        Entorno           NVARCHAR(20)  NOT NULL CONSTRAINT DF_AudioAuditoria_Entorno DEFAULT('prod'),
        IdAplicativo      NVARCHAR(200) NOT NULL,             -- misma clave que Auditorias/transcripciones
        NombreArchivo     NVARCHAR(400) NOT NULL,             -- nombre en disco (sha1(entorno|id).ogg)
        FormatoMime       NVARCHAR(60)  NOT NULL CONSTRAINT DF_AudioAuditoria_Mime DEFAULT('audio/ogg'),
        TamanioBytes      BIGINT        NOT NULL,
        DuracionSegundos  FLOAT         NULL,                 -- opcional (no requerido para reproducir)
        FechaCreacion     DATETIME2     NOT NULL CONSTRAINT DF_AudioAuditoria_Creacion DEFAULT(SYSUTCDATETIME()),
        FechaUltimoAcceso DATETIME2     NULL                  -- se actualiza al servir; futuro LRU
    );

    -- Un audio por interacción y por entorno (upsert en re-auditoría).
    ALTER TABLE calidad.AudioAuditoria
        ADD CONSTRAINT UQ_AudioAuditoria_Entorno_Id UNIQUE (Entorno, IdAplicativo);

    -- Descarte FIFO: barrer los más antiguos del entorno actual.
    CREATE INDEX IX_AudioAuditoria_Entorno_Fecha
        ON calidad.AudioAuditoria (Entorno, FechaCreacion);
END
GO
