/* ============================================================================
   Almacenamiento de imágenes de chatbots RAG
   Fecha: 2026-09-03
   Autor: equipo Acme

   EL PROBLEMA
   -----------
   El chatbot RAG ya cuenta con instrucciones en el system prompt y renderizado
   en el chat para mostrar imágenes de apoyo visual (![alt](url)). Sin embargo,
   requería que los analistas de calidad subieran las imágenes a un gestor
   externo y pegaran el link a mano, flujo que no se usaba por ser engorroso.

   LA SOLUCIÓN
   -----------
   Esta tabla (pagina_web.ChatbotImagenes) almacena las imágenes en SQL Server:
   - Paridad dev/prod: SRV00 y SRV01 comparten la base de datos Acme, por lo que
     las imágenes subidas en un entorno están disponibles de inmediato en el otro
     sin sincronización de discos.
   - Normalización previa en Python (Pillow): EXIF corregido, reescalado si excede
     1600px para mantener tamaños reducidos (~80 KB - 400 KB por imagen).
   - Deduplicación por hash_sha256: subir o extraer la misma imagen múltiples veces
     reutiliza el registro existente en lugar de duplicar bytes en la base.
   - Extracción automática: al procesar material crudo (.docx, .pdf, imágenes),
     el backend extrae las imágenes y le indica a Gemini sus URLs para insertarlas
     en el markdown generado.

   CÓMO CORRER
   -----------
   Contra la BD Acme. Idempotente y REEJECUTABLE.
   Aplicar ANTES de deployar el código.
   Depende de: 2026-07-08_chatbots_en_bd.sql.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF OBJECT_ID('pagina_web.ChatbotImagenes', 'U') IS NULL
BEGIN
    CREATE TABLE pagina_web.ChatbotImagenes (
        id           INT IDENTITY(1,1) NOT NULL,
        chatbot_id   INT            NOT NULL,
        nombre       NVARCHAR(400)  NOT NULL,   -- nombre original del archivo o etiqueta
        mime         NVARCHAR(100)  NOT NULL,   -- image/png, image/jpeg, image/webp, etc.
        datos        VARBINARY(MAX) NOT NULL,   -- binario optimizado
        tamano_bytes INT            NOT NULL CONSTRAINT DF_ChatbotImagenes_tam DEFAULT (0),
        ancho        INT            NULL,       -- ancho en píxeles
        alto         INT            NULL,       -- alto en píxeles
        descripcion  NVARCHAR(1000) NULL,       -- descripción / alt text
        hash_sha256  VARCHAR(64)    NULL,       -- hash SHA-256 para deduplicación
        created_by   INT            NULL,       -- usuario que subió o disparó la extracción
        created_at   DATETIME2(7)   NOT NULL CONSTRAINT DF_ChatbotImagenes_ca DEFAULT (SYSDATETIME()),
        CONSTRAINT PK_ChatbotImagenes PRIMARY KEY CLUSTERED (id),
        CONSTRAINT FK_ChatbotImagenes_Chatbots FOREIGN KEY (chatbot_id)
            REFERENCES pagina_web.Chatbots (id) ON DELETE CASCADE
    );

    PRINT 'Tabla pagina_web.ChatbotImagenes creada.';
END
ELSE
    PRINT 'pagina_web.ChatbotImagenes ya existía; no se toca.';
GO

/* Índice para listar imágenes de un chatbot ordenadas por fecha/id (excluyendo el blob) */
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_ChatbotImagenes_bot'
                 AND object_id = OBJECT_ID('pagina_web.ChatbotImagenes'))
BEGIN
    CREATE NONCLUSTERED INDEX IX_ChatbotImagenes_bot
        ON pagina_web.ChatbotImagenes (chatbot_id, id DESC)
        INCLUDE (nombre, mime, tamano_bytes, ancho, alto, descripcion, hash_sha256, created_by, created_at);
    PRINT 'Índice IX_ChatbotImagenes_bot creado.';
END
GO

/* Índice para deduplicación rápida por hash */
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_ChatbotImagenes_hash'
                 AND object_id = OBJECT_ID('pagina_web.ChatbotImagenes'))
BEGIN
    CREATE NONCLUSTERED INDEX IX_ChatbotImagenes_hash
        ON pagina_web.ChatbotImagenes (chatbot_id, hash_sha256);
    PRINT 'Índice IX_ChatbotImagenes_hash creado.';
END
GO

/* ------------------------------------------------------------ verificación */

SELECT c.name AS columna, t.name AS tipo, c.max_length, c.is_nullable
FROM sys.columns c
JOIN sys.types t ON t.user_type_id = c.user_type_id
WHERE c.object_id = OBJECT_ID('pagina_web.ChatbotImagenes')
ORDER BY c.column_id;
GO
