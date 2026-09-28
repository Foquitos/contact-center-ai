-- Estado de índice de chatbots POR ENTORNO.
--
-- Contexto: dev (SRV00) y prod (SRV01) comparten el MISMO SQL, pero tienen Qdrant y
-- filesystem SEPARADOS. Hasta ahora `pagina_web.Chatbots.index_version` era un escalar
-- único, así que reindexar en un entorno cambiaba la versión que el otro intentaba cargar.
-- Esta tabla independiza index_version/index_status/last_indexed_at por entorno: dev puede
-- reindexar sin tocar prod.
--
-- La DEFINICIÓN del bot (slug, prompt, docs, permiso, activo, updated_at) sigue en Chatbots
-- y es compartida. Las columnas index_* de Chatbots quedan por compatibilidad durante el
-- deploy (código viejo del otro entorno todavía las lee) y pasan a ser vestigiales; se
-- pueden dropear en una migración posterior cuando ambos entornos estén en el código nuevo.
--
-- Idempotente.

IF OBJECT_ID('pagina_web.ChatbotIndexState', 'U') IS NULL
BEGIN
    CREATE TABLE pagina_web.ChatbotIndexState (
        chatbot_id      INT          NOT NULL,
        environment     VARCHAR(20)  NOT NULL,
        index_version   INT          NOT NULL CONSTRAINT DF_ChatbotIndexState_version DEFAULT 0,
        index_status    VARCHAR(30)  NULL,
        last_indexed_at DATETIME2    NULL,
        updated_at      DATETIME2    NOT NULL CONSTRAINT DF_ChatbotIndexState_updated DEFAULT SYSDATETIME(),
        CONSTRAINT PK_ChatbotIndexState PRIMARY KEY (chatbot_id, environment),
        CONSTRAINT FK_ChatbotIndexState_Chatbots
            FOREIGN KEY (chatbot_id) REFERENCES pagina_web.Chatbots(id) ON DELETE CASCADE
    );
END
GO

-- Backfill 'prod' con el estado actual de Chatbots (prod es quien viene indexando y
-- sirviendo, así que sus columnas reflejan la realidad de prod). NO se backfillea 'dev':
-- sus filas se crean en el primer reindex de dev; hasta entonces el registry de dev sirve
-- la versión que tenga en su filesystem local (_local_index_version), sin quedar sin bots.
INSERT INTO pagina_web.ChatbotIndexState (chatbot_id, environment, index_version, index_status, last_indexed_at, updated_at)
SELECT c.id, 'prod', c.index_version, c.index_status, c.last_indexed_at, SYSDATETIME()
FROM pagina_web.Chatbots c
WHERE NOT EXISTS (
    SELECT 1 FROM pagina_web.ChatbotIndexState s
    WHERE s.chatbot_id = c.id AND s.environment = 'prod'
);
GO
