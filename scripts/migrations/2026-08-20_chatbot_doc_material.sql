/* ============================================================================
   Bandeja de material pendiente del asistente de documentación
   Fecha: 2026-08-20
   Autor: equipo Acme

   EL PROBLEMA
   -----------
   El asistente no tenía dónde dejar material "para después": subir un archivo o
   pegar un texto era, en un solo paso, mandarlo a la IA. Y como guardar la
   propuesta encolaba un reindexado (el checkbox venía tildado por defecto), cada
   cosita que se agregaba costaba una regeneración COMPLETA de los documentos
   tocados más una reconstrucción COMPLETA del índice.

   Medido en prod sobre Benefix (chatbot_id=3): index_version 84, con ráfagas como
   la del 2026-08-06 —6 reindexados entre las 15:04 y las 15:47, ~1 minuto de
   embeddings cada uno— que en realidad eran UNA sola sesión de carga de material.

   LA SOLUCIÓN
   -----------
   Esta tabla es la bandeja: el material (texto pegado o archivos) se guarda tal
   cual llega, SIN llamar a la IA. Cuando la persona terminó de cargar todo,
   aprieta "Procesar" una vez y el asistente ve todo el material junto en una sola
   corrida. Al aceptar la propuesta, el material consumido se BORRA.

   El reindexado, en la misma tanda de cambios, dejó de encolarse solo: el bot
   queda marcado con "cambios sin indexar" (se calcula comparando el updated_at de
   los documentos contra last_indexed_at, sin columnas nuevas) y se reindexa a mano
   cuando la carga terminó.

   NOTAS DE DISEÑO
   ---------------
   - Sin `environment`: el material es CONTENIDO, no ejecución. Va junto con
     ChatbotDocMarkdown/ChatbotDocVinculo, que también son compartidas dev/prod.
   - `datos` VARBINARY(MAX) guarda el archivo crudo. Es voluminoso pero transitorio
     (se borra al consumirlo) y evita que los adjuntos viajen en base64 dentro del
     payload de ChatbotDocJobs, que es lo que se hacía antes.
   - `nota` es la aclaración que escribe quien carga el material ("esto va al
     documento de reintegros"); viaja a la IA como contexto del bloque.
   - `doc_id_destino` guarda a qué documento apuntaba quien cargó el material (el
     botón "Sumar material" de la fila de un documento). No fuerza nada: se le dice
     a la IA en la corrida, que sigue siendo un único merge global. La FK va sin
     acción referencial porque los documentos se dan de BAJA LÓGICA (activo=0), no
     se borran; y una segunda cascada contra Chatbots sería un cascade path múltiple.
   - ON DELETE CASCADE contra Chatbots: borrar un bot no puede dejar material
     huérfano de un chatbot que ya no existe.

   CÓMO CORRER
   -----------
   Contra la BD Acme. Idempotente y REEJECUTABLE: sirve tanto en una base virgen como
   en una donde ya corrió la primera versión de este archivo (la que creaba la tabla
   sin `doc_id_destino`). Aplicar ANTES de deployar el código.

   Depende de: 2026-07-08_chatbots_en_bd.sql, 2026-07-24_chatbot_docs_markdown.sql.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF OBJECT_ID('pagina_web.ChatbotDocMaterial', 'U') IS NULL
BEGIN
    CREATE TABLE pagina_web.ChatbotDocMaterial (
        id           INT IDENTITY(1,1) NOT NULL,
        chatbot_id   INT            NOT NULL,
        tipo         NVARCHAR(20)   NOT NULL,   -- 'texto' | 'archivo'
        nombre       NVARCHAR(400)  NULL,       -- nombre de archivo o etiqueta
        mime         NVARCHAR(200)  NULL,       -- solo en 'archivo'
        texto        NVARCHAR(MAX)  NULL,       -- solo en 'texto'
        datos        VARBINARY(MAX) NULL,       -- solo en 'archivo'
        tamano_bytes INT            NOT NULL CONSTRAINT DF_ChatbotDocMaterial_tam DEFAULT (0),
        nota         NVARCHAR(1000) NULL,       -- aclaración de quien lo cargó
        doc_id_destino INT          NULL,       -- documento al que apuntaba (opcional)
        created_by   INT            NULL,
        created_at   DATETIME2(7)   NOT NULL CONSTRAINT DF_ChatbotDocMaterial_ca DEFAULT (SYSDATETIME()),
        CONSTRAINT PK_ChatbotDocMaterial PRIMARY KEY CLUSTERED (id),
        CONSTRAINT CK_ChatbotDocMaterial_tipo CHECK (tipo IN ('texto', 'archivo')),
        CONSTRAINT FK_ChatbotDocMaterial_Chatbots FOREIGN KEY (chatbot_id)
            REFERENCES pagina_web.Chatbots (id) ON DELETE CASCADE
        -- FK_ChatbotDocMaterial_Doc se crea más abajo, junto con la columna.
    );

    PRINT 'Tabla pagina_web.ChatbotDocMaterial creada.';
END
ELSE
    PRINT 'pagina_web.ChatbotDocMaterial ya existía; no se toca.';
GO

/* La tabla ya se creó (y se corrió esta migración) ANTES de que existiera
   `doc_id_destino`: el CREATE de arriba no la agrega en una base donde la tabla ya
   está, así que la columna se suma aparte. Sigue siendo idempotente y sirve tanto
   para una base virgen como para una que ya tenía la primera versión de la tabla. */
IF COL_LENGTH('pagina_web.ChatbotDocMaterial', 'doc_id_destino') IS NULL
BEGIN
    ALTER TABLE pagina_web.ChatbotDocMaterial ADD doc_id_destino INT NULL;
    PRINT 'Columna doc_id_destino agregada.';
END
ELSE
    PRINT 'doc_id_destino ya existía; no se toca.';
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_ChatbotDocMaterial_Doc')
BEGIN
    ALTER TABLE pagina_web.ChatbotDocMaterial
        ADD CONSTRAINT FK_ChatbotDocMaterial_Doc FOREIGN KEY (doc_id_destino)
            REFERENCES pagina_web.ChatbotDocMarkdown (id);
    PRINT 'FK FK_ChatbotDocMaterial_Doc creada.';
END
GO

/* Todas las consultas son "el material de este bot, en el orden en que se cargó".
   El índice deja fuera los blobs a propósito: listar la bandeja no baja archivos. */
/* Se recrea siempre: en una base donde esta migración ya corrió, el índice existe con
   el INCLUDE viejo (sin doc_id_destino) y un IF NOT EXISTS lo dejaría desactualizado.
   Son pocas filas (la bandeja es transitoria), así que rehacerlo no cuesta nada. */
IF EXISTS (SELECT 1 FROM sys.indexes
           WHERE name = 'IX_ChatbotDocMaterial_bot'
             AND object_id = OBJECT_ID('pagina_web.ChatbotDocMaterial'))
    DROP INDEX IX_ChatbotDocMaterial_bot ON pagina_web.ChatbotDocMaterial;
GO

CREATE NONCLUSTERED INDEX IX_ChatbotDocMaterial_bot
    ON pagina_web.ChatbotDocMaterial (chatbot_id, id)
    INCLUDE (tipo, nombre, mime, tamano_bytes, nota, doc_id_destino, created_by, created_at);
PRINT 'Índice IX_ChatbotDocMaterial_bot al día.';
GO

/* ------------------------------------------------------------ verificación */

SELECT COUNT(*) AS material_pendiente FROM pagina_web.ChatbotDocMaterial;

SELECT c.name AS columna, t.name AS tipo, c.max_length, c.is_nullable
FROM sys.columns c
JOIN sys.types t ON t.user_type_id = c.user_type_id
WHERE c.object_id = OBJECT_ID('pagina_web.ChatbotDocMaterial')
ORDER BY c.column_id;
GO
