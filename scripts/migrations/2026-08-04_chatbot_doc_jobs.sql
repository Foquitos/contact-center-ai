/* ============================================================================
   Cola de trabajos del asistente de documentación (formatear / agregar info)
   Fecha: 2026-08-04
   Autor: equipo Acme

   POR QUÉ
   -------
   "Agregar información" corría DENTRO de la request HTTP. Medido sobre un
   documento real de 17k caracteres (Combustible Benefix): 45s el camino corto
   —la verificación pasó en la primera ronda— y ~3 minutos el peor caso, porque
   son 1 llamada de merge + hasta CHATBOT_DOCS_VERIFY_ROUNDS rondas de
   verificar→reparar, y cada una regenera el markdown COMPLETO del documento.

   El timeout por defecto de gunicorn es 30s, así que al worker lo mataban antes
   de que pudiera responder y al usuario le llegaba la página de error HTML de
   gunicorn dentro de un alert(). Subir el timeout tapaba el síntoma pero dejaba
   un worker tomado 3 minutos por cada uso del asistente.

   Ahora la request solo ENCOLA y responde al instante; el trabajo lo hace el
   scheduler (run_scheduler.py) y el navegador consulta el estado por polling,
   igual que la cola de reindexado (pagina_web.ChatbotIndexJobs), de la que esta
   tabla es hermana.

   NOTAS DE DISEÑO
   ---------------
   - `payload` guarda la request entera en JSON (incluye los archivos en base64,
     que es lo único voluminoso). Se BORRA al terminar el job: no hace falta
     después y evita que la tabla crezca con adjuntos de 20 MB.
   - `payload`/`resultado` son NVARCHAR: el markdown lleva emojis (🗣️ ✉️ 🖥️) y en
     VARCHAR se pierden en el bind, como ya pasó en query_chatbots_logs.
   - `environment` separa dev de prod, que comparten la base: un scheduler no
     debe tomar los jobs del otro entorno.

   CÓMO CORRER
   -----------
   Contra la BD Acme. Idempotente. Aplicar ANTES de deployar el código.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF OBJECT_ID('pagina_web.ChatbotDocJobs', 'U') IS NULL
BEGIN
    CREATE TABLE pagina_web.ChatbotDocJobs (
        id            INT IDENTITY(1,1) NOT NULL,
        tipo          NVARCHAR(20)  NOT NULL,   -- 'formatear' | 'merge'
        -- NULL permitido: formatear puede pedirse sin bot (el chatbot_id solo
        -- ambienta el prompt con la empresa/campaña).
        chatbot_id    INT           NULL,
        status        NVARCHAR(40)  NOT NULL CONSTRAINT DF_ChatbotDocJobs_status DEFAULT ('pending'),
        payload       NVARCHAR(MAX) NULL,       -- JSON de la request; se limpia al terminar
        resultado     NVARCHAR(MAX) NULL,       -- JSON de la propuesta (DocPropuestaOut)
        error         NVARCHAR(MAX) NULL,
        requested_by  INT           NULL,
        environment   VARCHAR(20)   NOT NULL,
        created_at    DATETIME2(7)  NOT NULL CONSTRAINT DF_ChatbotDocJobs_created DEFAULT (SYSDATETIME()),
        started_at    DATETIME2(7)  NULL,
        finished_at   DATETIME2(7)  NULL,
        CONSTRAINT PK_ChatbotDocJobs PRIMARY KEY CLUSTERED (id),
        CONSTRAINT CK_ChatbotDocJobs_tipo   CHECK (tipo IN ('formatear', 'merge')),
        CONSTRAINT CK_ChatbotDocJobs_status CHECK (status IN ('pending', 'running', 'done', 'failed')),
        CONSTRAINT FK_ChatbotDocJobs_Chatbots FOREIGN KEY (chatbot_id)
            REFERENCES pagina_web.Chatbots (id)
    );

    PRINT 'Tabla pagina_web.ChatbotDocJobs creada.';
END
ELSE
    PRINT 'pagina_web.ChatbotDocJobs ya existía; no se toca.';
GO

/* El claim del scheduler filtra por status+environment y ordena por id (FIFO).
   Sin este índice, cada tick escanea la tabla entera. */
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_ChatbotDocJobs_cola'
                 AND object_id = OBJECT_ID('pagina_web.ChatbotDocJobs'))
BEGIN
    CREATE NONCLUSTERED INDEX IX_ChatbotDocJobs_cola
        ON pagina_web.ChatbotDocJobs (status, environment, id);
    PRINT 'Índice IX_ChatbotDocJobs_cola creado.';
END
GO

/* ------------------------------------------------------------ verificación */

SELECT COUNT(*) AS jobs_existentes FROM pagina_web.ChatbotDocJobs;

SELECT c.name AS columna, t.name AS tipo, c.max_length, c.is_nullable
FROM sys.columns c
JOIN sys.types t ON t.user_type_id = c.user_type_id
WHERE c.object_id = OBJECT_ID('pagina_web.ChatbotDocJobs')
ORDER BY c.column_id;
GO
