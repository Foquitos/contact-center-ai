/* ============================================================================
   Feature — Vacíos de conocimiento del chatbot (triage para Calidad)
   Fecha: 2026-07-30
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   1. Columnas de COBERTURA en pagina_web.query_chatbots_logs: cada consulta queda
      marcada según si el bot pudo responderla con la documentación o no.
   2. Tabla nueva pagina_web.ChatbotVacios: los "temas" sin cobertura AGRUPADOS
      (consultas parecidas caen en el mismo tema) para que Calidad triage por
      frecuencia y no consulta por consulta.
   3. Permiso chatbot.vacios para la pantalla nueva.
   4. query/response/context pasan de VARCHAR(MAX) a NVARCHAR(MAX).

   POR QUÉ
   -------
   El 23% de las consultas a Voltara terminan en "no encontré esa información" y
   hoy no hay forma de que Calidad las vea: la única señal de calidad eran las
   calificaciones del chat (35 en 9 meses). Sin esto, un tema que 18 operadores
   distintos preguntaron (ej: "poda de árboles") no llega nunca a quien puede
   documentarlo.

   La distinción clave que resuelve esta tabla: NO todo "no encontré" es falta de
   información. Puede ser (a) un hueco real de documentación -> le toca a Calidad,
   o (b) un fallo de recuperación: la info SÍ está pero el buscador no la trajo
   -> nos toca a nosotros. Mandarle (b) a Calidad hace que carguen duplicados. Por
   eso ChatbotVacios.clasificacion separa ambos casos y la pantalla los muestra
   distinto.

   VARCHAR -> NVARCHAR
   -------------------
   Las columnas se crearon con pandas.to_sql (VARCHAR(MAX)): los emoji del system
   prompt (🖥️ / 🗣️) se guardaban como '?'. Eso no es solo cosmético: chatBot.py
   RELEE response para reconstruir el historial del operador, así que el bot
   recibía su propia respuesta degradada como contexto. Los datos ya guardados no
   se recuperan (el carácter se perdió al escribir), pero de acá en adelante sí.

   CÓMO CORRER
   -----------
   Contra la BD Acme, ANTES de deployar el código nuevo. Idempotente.
   El ALTER COLUMN sobre una tabla de ~9k filas es instantáneo; aun así conviene
   correrlo fuera del horario pico porque toma un lock de esquema.

   Depende de: la tabla pagina_web.query_chatbots_logs (creada por pandas) y
   pagina_web.Chatbots.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

/* ---------------------------------------------------------------- 1. NVARCHAR */
/* Se dropean primero los índices que toquen estas columnas (no hay, pero el
   patrón evita sorpresas si alguien agregó uno). */

IF EXISTS (
    SELECT 1 FROM sys.columns c
    JOIN sys.types t ON t.user_type_id = c.user_type_id
    WHERE c.object_id = OBJECT_ID('pagina_web.query_chatbots_logs')
      AND c.name = 'query' AND t.name = 'varchar'
)
BEGIN
    ALTER TABLE pagina_web.query_chatbots_logs ALTER COLUMN [query] NVARCHAR(MAX) NULL;
END
GO

IF EXISTS (
    SELECT 1 FROM sys.columns c
    JOIN sys.types t ON t.user_type_id = c.user_type_id
    WHERE c.object_id = OBJECT_ID('pagina_web.query_chatbots_logs')
      AND c.name = 'response' AND t.name = 'varchar'
)
BEGIN
    ALTER TABLE pagina_web.query_chatbots_logs ALTER COLUMN [response] NVARCHAR(MAX) NULL;
END
GO

IF EXISTS (
    SELECT 1 FROM sys.columns c
    JOIN sys.types t ON t.user_type_id = c.user_type_id
    WHERE c.object_id = OBJECT_ID('pagina_web.query_chatbots_logs')
      AND c.name = 'context' AND t.name = 'varchar'
)
BEGIN
    ALTER TABLE pagina_web.query_chatbots_logs ALTER COLUMN [context] NVARCHAR(MAX) NULL;
END
GO

IF EXISTS (
    SELECT 1 FROM sys.columns c
    JOIN sys.types t ON t.user_type_id = c.user_type_id
    WHERE c.object_id = OBJECT_ID('pagina_web.query_chatbots_logs')
      AND c.name = 'effective_campana' AND t.name = 'varchar'
)
BEGIN
    ALTER TABLE pagina_web.query_chatbots_logs ALTER COLUMN [effective_campana] NVARCHAR(200) NULL;
END
GO

/* ------------------------------------------------- 2. Columnas de cobertura */
/* score_max: mejor score del reranker entre los chunks que se le mandaron al LLM.
   Es la señal barata (cero tokens, cero latencia) de qué tan bien le fue a la
   recuperación. Negativo = el cross-encoder considera que NADA de lo recuperado
   responde la pregunta.
   sin_cobertura: NULL = todavía no se evaluó; 1 = el bot no pudo responder.
   vacio_id: a qué tema agrupado quedó asociada (FK a ChatbotVacios). */

IF COL_LENGTH('pagina_web.query_chatbots_logs', 'score_max') IS NULL
    ALTER TABLE pagina_web.query_chatbots_logs ADD score_max FLOAT NULL;
GO
IF COL_LENGTH('pagina_web.query_chatbots_logs', 'sin_cobertura') IS NULL
    ALTER TABLE pagina_web.query_chatbots_logs ADD sin_cobertura BIT NULL;
GO
IF COL_LENGTH('pagina_web.query_chatbots_logs', 'vacio_id') IS NULL
    ALTER TABLE pagina_web.query_chatbots_logs ADD vacio_id INT NULL;
GO

/* ------------------------------------------------------- 3. Tabla de vacíos */

IF OBJECT_ID('pagina_web.ChatbotVacios', 'U') IS NULL
BEGIN
    CREATE TABLE pagina_web.ChatbotVacios (
        id             INT IDENTITY(1,1) CONSTRAINT PK_ChatbotVacios PRIMARY KEY,
        chatbot_id     INT NOT NULL CONSTRAINT FK_ChatbotVacios_Chatbot
                           REFERENCES pagina_web.Chatbots(id),

        -- Tema normalizado que extrae el clasificador ("poda de árboles en la vía
        -- pública"), no la consulta cruda: los operadores escriben con typos
        -- ("cleinte", "porifs") y agrupar por texto crudo no junta nada.
        tema           NVARCHAR(300) NOT NULL,
        pregunta_ejemplo NVARCHAR(1000) NULL,   -- una consulta real representativa

        -- 'hueco'     : la documentación no lo cubre -> le toca a Calidad.
        -- 'recuperacion': el contenido SÍ existe pero el buscador no lo trajo
        --                 -> problema técnico, no se le pide nada a Calidad.
        -- 'fuera_de_alcance': no es algo que le corresponda al bot.
        clasificacion  VARCHAR(20) NOT NULL
                           CONSTRAINT DF_ChatbotVacios_clas DEFAULT 'hueco',

        -- Triage de Calidad: pendiente -> agregar | no_corresponde | ya_documentado.
        -- 'ya_documentado' es el que nos rebota el caso a nosotros.
        estado         VARCHAR(20) NOT NULL
                           CONSTRAINT DF_ChatbotVacios_estado DEFAULT 'pendiente',

        ocurrencias    INT NOT NULL CONSTRAINT DF_ChatbotVacios_ocur DEFAULT 1,
        usuarios       INT NOT NULL CONSTRAINT DF_ChatbotVacios_usr DEFAULT 1,
        primera_vez    DATETIME2 NOT NULL CONSTRAINT DF_ChatbotVacios_pv DEFAULT SYSDATETIME(),
        ultima_vez     DATETIME2 NOT NULL CONSTRAINT DF_ChatbotVacios_uv DEFAULT SYSDATETIME(),

        -- Cuando la clasificación es 'recuperacion': dónde estaba la respuesta.
        -- Le sirve a Calidad para confirmar que ya está y a nosotros para depurar.
        doc_sugerido   NVARCHAR(300) NULL,
        score_sugerido FLOAT NULL,

        notas          NVARCHAR(2000) NULL,     -- por qué Calidad decidió lo que decidió
        resuelto_por   INT NULL,                -- documento del usuario que triageó
        resuelto_at    DATETIME2 NULL,

        created_at     DATETIME2 NOT NULL CONSTRAINT DF_ChatbotVacios_ca DEFAULT SYSDATETIME(),
        updated_at     DATETIME2 NOT NULL CONSTRAINT DF_ChatbotVacios_ua DEFAULT SYSDATETIME()
    );

    -- La pantalla ordena por frecuencia dentro de un bot y filtra por estado.
    CREATE INDEX IX_ChatbotVacios_bot_estado
        ON pagina_web.ChatbotVacios (chatbot_id, estado, ocurrencias DESC);
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_query_chatbots_logs_vacio'
)
AND COL_LENGTH('pagina_web.query_chatbots_logs', 'vacio_id') IS NOT NULL
BEGIN
    ALTER TABLE pagina_web.query_chatbots_logs
        ADD CONSTRAINT FK_query_chatbots_logs_vacio
        FOREIGN KEY (vacio_id) REFERENCES pagina_web.ChatbotVacios(id);
END
GO

/* Buscar los pendientes de clasificar (lo que drena el job asíncrono) tiene que
   ser barato: son pocas filas sobre una tabla que crece. */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_query_logs_sin_clasificar')
BEGIN
    CREATE INDEX IX_query_logs_sin_clasificar
        ON pagina_web.query_chatbots_logs (sin_cobertura, vacio_id)
        INCLUDE (effective_campana, fecha);
END
GO

/* ------------------------------------------------------------- 4. Permiso */

IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'chatbot.vacios')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('chatbot.vacios',
            'Ver y triagear los vacíos de conocimiento de los chatbots (qué no pudo responder el bot)');
END
GO

/* DECISIÓN: nace SIN ASIGNAR (igual que chatbot.solicitudes / uso_ia.chatbot).
   Se asigna después a los roles de Calidad, que son quienes documentan. */

SELECT code, description FROM pagina_web.Permissions WHERE code = 'chatbot.vacios';
GO

SELECT c.name AS columna, t.name AS tipo
FROM sys.columns c JOIN sys.types t ON t.user_type_id = c.user_type_id
WHERE c.object_id = OBJECT_ID('pagina_web.query_chatbots_logs')
ORDER BY c.column_id;
GO
