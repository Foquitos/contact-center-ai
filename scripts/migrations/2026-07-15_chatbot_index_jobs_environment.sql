-- Aísla la cola de reindexado de chatbots por entorno.
--
-- Contexto: dev (SRV00) y prod (SRV01) comparten el MISMO SQL Server, así que
-- comparten pagina_web.ChatbotIndexJobs. Sin discriminador, el scheduler de un
-- entorno reclama (o marca huérfano) jobs del otro, y un reindex disparado en dev
-- puede ejecutarse en prod. Con esta columna cada scheduler reclama/marca SOLO los
-- jobs de su ENVIRONMENT (setting nuevo en la app; ver app/config.py).
--
-- Default 'prod': las filas existentes y prod (sin necesidad de re-deploy inmediato)
-- mantienen el comportamiento actual. dev debe setear ENVIRONMENT=dev en su .env.
--
-- Idempotente: se puede correr más de una vez sin error.

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('pagina_web.ChatbotIndexJobs')
      AND name = 'environment'
)
BEGIN
    ALTER TABLE pagina_web.ChatbotIndexJobs
        ADD environment VARCHAR(20) NOT NULL
        CONSTRAINT DF_ChatbotIndexJobs_environment DEFAULT 'prod';
END
GO

-- El claim y el marcado de huérfanos filtran por (status, environment): un índice
-- que incluya environment mantiene barato el TOP(1) del claim con la cola grande.
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_ChatbotIndexJobs_env_status'
      AND object_id = OBJECT_ID('pagina_web.ChatbotIndexJobs')
)
BEGIN
    CREATE INDEX IX_ChatbotIndexJobs_env_status
        ON pagina_web.ChatbotIndexJobs (environment, status) INCLUDE (chatbot_id);
END
GO
