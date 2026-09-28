/* ============================================================================
   Limpieza — Baja de los Google Docs como base de conocimiento de los chatbots
   Fecha: 2026-07-30
   Autor: equipo Acme

   QUÉ HACE
   --------
   Elimina pagina_web.ChatbotDocs, la tabla que guardaba los file_id de Google
   Drive que el indexador exportaba a markdown en cada reindexado.

   POR QUÉ
   -------
   El conocimiento de los chatbots se pasó a pagina_web.ChatbotDocMarkdown
   (documentos editables in-app con el asistente de documentación, prestables
   entre bots vía ChatbotDocVinculo). La migración de datos ya terminó: los 14
   chatbots tienen 0 filas en ChatbotDocs y todos los que están activos indexan
   desde markdown. La ruta de Drive quedó como código muerto y un pie de apoyo
   para volver a cargar conocimiento fuera del circuito de revisión.

   VERIFICADO ANTES DE ESCRIBIR ESTO
   ---------------------------------
   SELECT COUNT(*) FROM pagina_web.ChatbotDocs  ->  0

   La guardia de abajo lo vuelve a chequear: si apareciera alguna fila, aborta
   sin borrar nada (habría conocimiento vivo que rescatar a markdown primero).

   ORDEN RESPECTO DEL DEPLOY: correr DESPUÉS de deployar el código que ya no la
   consulta (backend/app/chatbot_indexer.py, backend/app/routers/chatbot_admin.py,
   backend/reindex_all.py). Si se corre antes, el panel de chatbots y el
   indexador tiran error hasta que entre el deploy.

   ROLLBACK: recrear la tabla con el DDL comentado al final. No hay datos que
   restaurar (estaba vacía).
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF OBJECT_ID('pagina_web.ChatbotDocs', 'U') IS NULL
BEGIN
    PRINT 'pagina_web.ChatbotDocs ya no existe: nada que hacer.';
END
ELSE
BEGIN
    DECLARE @filas INT = (SELECT COUNT(*) FROM pagina_web.ChatbotDocs);
    IF @filas > 0
        THROW 50002, 'pagina_web.ChatbotDocs tiene filas: migrar ese conocimiento a ChatbotDocMarkdown antes de borrar la tabla.', 1;

    DROP TABLE pagina_web.ChatbotDocs;
    PRINT 'pagina_web.ChatbotDocs eliminada.';
END
GO

/* --------------------------- VERIFICACIÓN --------------------------------- */
-- Debe devolver 0 filas:
SELECT TABLE_SCHEMA, TABLE_NAME
FROM INFORMATION_SCHEMA.TABLES
WHERE TABLE_SCHEMA = 'pagina_web' AND TABLE_NAME = 'ChatbotDocs';
GO

-- Todos los bots activos siguen teniendo conocimiento (propio o prestado):
SELECT c.slug, c.activo,
       (SELECT COUNT(*) FROM pagina_web.ChatbotDocMarkdown m
         WHERE m.activo = 1
           AND (m.chatbot_id = c.id
                OR EXISTS (SELECT 1 FROM pagina_web.ChatbotDocVinculo v
                           WHERE v.doc_id = m.id AND v.chatbot_id = c.id))) AS docs_que_indexa
FROM pagina_web.Chatbots c
WHERE c.activo = 1
ORDER BY c.slug;
GO


/* ============================================================================
   ROLLBACK (solo si hubiera que volver atrás el deploy)

CREATE TABLE pagina_web.ChatbotDocs (
    id            INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    chatbot_id    INT           NOT NULL,
    drive_file_id NVARCHAR(100) NOT NULL,
    titulo        NVARCHAR(200) NULL,
    orden         INT           NOT NULL DEFAULT 1,
    activo        BIT           NOT NULL DEFAULT 1,
    created_at    DATETIME2     NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT FK_ChatbotDocs_Chatbot FOREIGN KEY (chatbot_id)
        REFERENCES pagina_web.Chatbots(id)
);
   ============================================================================ */
