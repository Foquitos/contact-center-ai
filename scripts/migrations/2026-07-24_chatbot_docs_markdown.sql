/* ============================================================================
   Feature — Documentos de conocimiento RAG in-app (asistente de documentación)
   Fecha: 2026-07-24
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   Tabla nueva pagina_web.ChatbotDocMarkdown: el markdown del conocimiento de cada
   chatbot, armado desde el panel de calidad con el asistente de IA (formatear
   contenido crudo -> markdown estructurado + verificación de completitud + merge
   incremental; ver backend/AuditorIA/asistente_docs.py). Es la fuente de verdad
   que el indexador materializa a disco antes de chunquear (app/chatbot_indexer.py),
   en reemplazo de los Google Docs (pagina_web.ChatbotDocs), que se conservan y
   siguen funcionando durante la migración (el indexador soporta ambas fuentes).

   No agrega permisos: el CRUD y el asistente van bajo el permiso existente
   chatbot:admin (mismo que el resto del panel de administración de chatbots).

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme ANTES de deployar el código nuevo (el indexador nuevo
   lee esta tabla; el CRUD del panel la escribe). Idempotente.

   Depende de: 2026-07-08_chatbots_en_bd.sql (tabla pagina_web.Chatbots).
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF OBJECT_ID('pagina_web.ChatbotDocMarkdown', 'U') IS NULL
BEGIN
    CREATE TABLE pagina_web.ChatbotDocMarkdown (
        id            INT IDENTITY(1,1) CONSTRAINT PK_ChatbotDocMarkdown PRIMARY KEY,
        chatbot_id    INT NOT NULL CONSTRAINT FK_ChatbotDocMarkdown_Chatbot
                          REFERENCES pagina_web.Chatbots(id),
        titulo        NVARCHAR(200) NULL,
        orden         INT NOT NULL CONSTRAINT DF_ChatbotDocMarkdown_orden DEFAULT 1,
        contenido_md  NVARCHAR(MAX) NOT NULL,   -- markdown RAG (lo que se indexa)
        updated_by    INT NULL,                 -- documento del usuario que editó (NULL = desconocido)
        activo        BIT NOT NULL CONSTRAINT DF_ChatbotDocMarkdown_activo DEFAULT 1,
        created_at    DATETIME2 NOT NULL CONSTRAINT DF_ChatbotDocMarkdown_ca DEFAULT SYSDATETIME(),
        updated_at    DATETIME2 NOT NULL CONSTRAINT DF_ChatbotDocMarkdown_ua DEFAULT SYSDATETIME()
    );
    -- El indexador y el panel filtran por chatbot_id + activo.
    CREATE INDEX IX_ChatbotDocMarkdown_chatbot
        ON pagina_web.ChatbotDocMarkdown(chatbot_id, activo) INCLUDE (orden);
END
GO
