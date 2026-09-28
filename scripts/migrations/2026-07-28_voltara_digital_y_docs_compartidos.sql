/* ============================================================================
   Feature — Separación de Voltara telefónico / Voltara Digital + documentos compartidos
   Fecha: 2026-07-28
   Autor: equipo Acme

   EL PROBLEMA
   -----------
   Voltara tiene dos operaciones distintas contra un mismo chatbot:
     - Voltara (telefónico): atiende llamadas y consulta procedimientos.
     - Voltara Digital: responde por correo/Oficina Virtual/App ENVIANDO CARTAS.
   Al vivir el catálogo de 50 cartas en el mismo índice que los procedimientos, el
   buscador se lo ofrecía al operador telefónico todo el tiempo: le devolvía modelos de
   carta a alguien que está hablando por teléfono con el cliente.

   LA SOLUCIÓN
   -----------
   Dos chatbots con índices separados. El del teléfono NO contiene las cartas, así que
   el problema deja de ser improbable y pasa a ser imposible. Ambos comparten los
   procedimientos generales mediante la tabla nueva ChatbotDocVinculo: el documento vive
   en un solo lugar y lo indexan los dos bots (duplicarlo garantizaría que se
   desincronicen apenas calidad edite uno).

   Reparto resultante:
     voltara          -> 6 documentos de procedimientos (propios). Sin cartas.
     voltara_digital  -> esos mismos 6 (prestados) + el catálogo de cartas (propio).

   El operador elige el bot en el selector; para los usuarios de campaña VOLTARA el
   preseleccionado sigue siendo el telefónico (ver _resolver_slug_efectivo en
   app/routers/chatbot.py, que cae a la campaña del usuario).

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con el código nuevo ya deployado (el indexador tiene que
   entender ChatbotDocVinculo). Idempotente. DESPUÉS: reindexar AMBOS bots.

   Depende de: 2026-07-08_chatbots_en_bd.sql, 2026-07-24_chatbot_docs_markdown.sql,
   2026-07-27_voltara_prompt_cartas.sql (de la que mueve el addendum de cartas).
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

/* ==================== 1) TABLA DE DOCUMENTOS COMPARTIDOS ==================== */

IF OBJECT_ID('pagina_web.ChatbotDocVinculo', 'U') IS NULL
BEGIN
    CREATE TABLE pagina_web.ChatbotDocVinculo (
        doc_id     INT NOT NULL CONSTRAINT FK_ChatbotDocVinculo_Doc
                       REFERENCES pagina_web.ChatbotDocMarkdown(id),
        chatbot_id INT NOT NULL CONSTRAINT FK_ChatbotDocVinculo_Chatbot
                       REFERENCES pagina_web.Chatbots(id),
        created_at DATETIME2 NOT NULL CONSTRAINT DF_ChatbotDocVinculo_ca DEFAULT SYSDATETIME(),
        CONSTRAINT PK_ChatbotDocVinculo PRIMARY KEY (doc_id, chatbot_id)
    );
    -- El indexador filtra por bot para saber qué documentos prestados le tocan.
    CREATE INDEX IX_ChatbotDocVinculo_chatbot ON pagina_web.ChatbotDocVinculo(chatbot_id);
END
GO

/* ==================== 2) PERMISO Y BOT NUEVO =============================== */

INSERT INTO pagina_web.Permissions (code, description)
SELECT 'chatbot:voltara_digital', N'Usar el chatbot de Voltara Digital (generación de cartas de respuesta)'
WHERE NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'chatbot:voltara_digital');
GO

DECLARE @id_voltara   INT = (SELECT id FROM pagina_web.Chatbots WHERE slug = 'voltara');
DECLARE @perm_digital INT = (SELECT id FROM pagina_web.Permissions WHERE code = 'chatbot:voltara_digital');
DECLARE @marca NVARCHAR(100) = N'--- ARMADO DE CARTAS Y RESPUESTAS DIGITALES ---';

IF @id_voltara IS NULL THROW 50010, 'No existe el chatbot voltara.', 1;

/* El prompt del bot Digital se deriva del de Voltara para no duplicar los ajustes que
   calidad le haya hecho: se toma la parte BASE (sin el bloque de cartas), se cambia lo
   específico del canal telefónico y se le vuelve a pegar el bloque de cartas. */
DECLARE @prompt_voltara NVARCHAR(MAX) = (SELECT system_prompt FROM pagina_web.Chatbots WHERE id = @id_voltara);
DECLARE @pos INT = CHARINDEX(@marca, @prompt_voltara);
DECLARE @base    NVARCHAR(MAX) = CASE WHEN @pos > 0 THEN LEFT(@prompt_voltara, @pos - 1) ELSE @prompt_voltara END;
DECLARE @cartas  NVARCHAR(MAX) = CASE WHEN @pos > 0 THEN SUBSTRING(@prompt_voltara, @pos, LEN(@prompt_voltara)) ELSE N'' END;

IF @cartas = N''
    PRINT 'AVISO: el prompt de voltara no tiene el bloque de cartas (¿falta correr 2026-07-27_voltara_prompt_cartas.sql?). El bot Digital se crea sin él.';

DECLARE @base_digital NVARCHAR(MAX) = REPLACE(REPLACE(REPLACE(@base,
    N'asistir a los operadores telefónicos de Voltara durante sus llamadas',
    N'asistir a los operadores de Voltara Digital, que responden a los clientes por correo electrónico, Oficina Virtual y App'),
    N'El operador está al teléfono y necesita leer rápido',
    N'El operador está redactando una respuesta escrita al cliente'),
    N'Eres ChatVoltara', N'Eres ChatVoltara Digital');

IF NOT EXISTS (SELECT 1 FROM pagina_web.Chatbots WHERE slug = 'voltara_digital')
BEGIN
    INSERT INTO pagina_web.Chatbots (slug, nombre, descripcion, system_prompt, grupo, permission_id)
    VALUES ('voltara_digital', N'Voltara Digital (cartas)',
            N'Generación de cartas y respuestas escritas al cliente (mail, Oficina Virtual y App)',
            @base_digital + @cartas, NULL, @perm_digital);
    PRINT 'Chatbot voltara_digital creado.';
END
ELSE
    PRINT 'El chatbot voltara_digital ya existía; no se toca su prompt.';
GO

/* ==================== 3) ACCESO ============================================ */
/* Todo rol que hoy puede usar el chatbot de Voltara pasa a ver también el Digital, para
   que el operador elija en el selector. Si más adelante se quiere restringir Digital a
   su propio equipo, se le quita el permiso a los roles que no correspondan desde
   Gestionar Roles (no hace falta tocar la BD). */
INSERT INTO pagina_web.RolePermissions (role_id, permission_id)
SELECT rp.role_id, pnew.id
FROM pagina_web.RolePermissions rp
JOIN pagina_web.Permissions pold ON pold.id = rp.permission_id AND pold.code = 'chatbot:voltara'
CROSS JOIN pagina_web.Permissions pnew
WHERE pnew.code = 'chatbot:voltara_digital'
  AND NOT EXISTS (SELECT 1 FROM pagina_web.RolePermissions x
                  WHERE x.role_id = rp.role_id AND x.permission_id = pnew.id);
GO

/* ==================== 4) REPARTO DE DOCUMENTOS ============================= */

DECLARE @id_voltara  INT = (SELECT id FROM pagina_web.Chatbots WHERE slug = 'voltara');
DECLARE @id_digital INT = (SELECT id FROM pagina_web.Chatbots WHERE slug = 'voltara_digital');

/* 4a) El catálogo de cartas PASA A SER del bot Digital: es su conocimiento propio y,
   sobre todo, deja de estar en el índice del telefónico (que era el problema). Se
   detecta por contenido, no por id, para no depender de datos de un entorno. */
UPDATE pagina_web.ChatbotDocMarkdown
SET chatbot_id = @id_digital, updated_at = SYSDATETIME()
WHERE chatbot_id = @id_voltara
  AND activo = 1
  AND (contenido_md LIKE N'%Catálogo de cartas disponibles%' OR titulo LIKE N'%Plantilla%');

/* 4b) El resto de los documentos de Voltara (procedimientos) se COMPARTEN con Digital:
   siguen viviendo y editándose en el telefónico, y los dos bots los indexan. */
INSERT INTO pagina_web.ChatbotDocVinculo (doc_id, chatbot_id)
SELECT m.id, @id_digital
FROM pagina_web.ChatbotDocMarkdown m
WHERE m.chatbot_id = @id_voltara AND m.activo = 1
  AND NOT EXISTS (SELECT 1 FROM pagina_web.ChatbotDocVinculo v
                  WHERE v.doc_id = m.id AND v.chatbot_id = @id_digital);
GO

/* ==================== 5) PROMPT DEL TELEFÓNICO ============================= */
/* Se le saca el bloque de armado de cartas: ese bot ya no las tiene en su índice, así
   que dejarlo solo lo empujaría a ofrecer cartas que no puede ver. */

DECLARE @marca NVARCHAR(100) = N'--- ARMADO DE CARTAS Y RESPUESTAS DIGITALES ---';
DECLARE @p NVARCHAR(MAX) = (SELECT system_prompt FROM pagina_web.Chatbots WHERE slug = 'voltara');
DECLARE @pos INT = CHARINDEX(@marca, @p);

IF @pos > 0
BEGIN
    UPDATE pagina_web.Chatbots
    SET system_prompt = RTRIM(LEFT(@p, @pos - 1)),
        updated_at = SYSDATETIME()
    WHERE slug = 'voltara';
    PRINT 'Bloque de cartas removido del prompt de voltara (telefónico).';
END
ELSE
    PRINT 'El prompt de voltara no tenía el bloque de cartas; nada que remover.';
GO

/* ==================== 6) REPORTE =========================================== */
SELECT c.slug, c.nombre, p.code AS permiso,
       (SELECT COUNT(*) FROM pagina_web.ChatbotDocMarkdown m
         WHERE m.chatbot_id = c.id AND m.activo = 1) AS docs_propios,
       (SELECT COUNT(*) FROM pagina_web.ChatbotDocVinculo v
         JOIN pagina_web.ChatbotDocMarkdown m2 ON m2.id = v.doc_id AND m2.activo = 1
         WHERE v.chatbot_id = c.id) AS docs_prestados,
       CASE WHEN c.system_prompt LIKE N'%ARMADO DE CARTAS%' THEN 'sí' ELSE 'no' END AS prompt_cartas
FROM pagina_web.Chatbots c
JOIN pagina_web.Permissions p ON p.id = c.permission_id
WHERE c.slug IN ('voltara', 'voltara_digital');
GO

/* ==================== 7) DESPUÉS DE CORRER ================================
   Reindexar AMBOS bots para que los índices reflejen el reparto:
       python reindex_all.py voltara voltara_digital
   Hasta entonces, el telefónico sigue sirviendo su índice viejo (con las cartas).
   ========================================================================== */
