/* ============================================================================
   Feature — Chatbots administrables en BD (permiso por bot + panel de calidad)
   Fecha: 2026-07-08
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   1) Tablas nuevas en pagina_web:
        - Chatbots        : catálogo de bots RAG (slug, prompt, permiso, versión de índice).
        - ChatbotDocs     : N Google Docs (drive_file_id) por bot.
        - ChatbotPcrc     : mapeo PCRC vigente -> bot del grupo CSV (antes hardcodeado
                            en backend/app/routers/chatbot.py::PCRC_A_CAMPANA).
        - ChatbotIndexJobs: cola de reindexado procesada por run_scheduler.py
                            (reemplaza el flag actualizar_rag.flag + restart nocturno).
   2) Permisos nuevos: chatbot:paygo / chatbot:benefix / chatbot:vantix (uno por bot
      standalone; chatbot:voltara ya existía), chatbot:csv (compartido por el grupo CSV)
      y chatbot:admin (panel de administración de chatbots para calidad).
   3) Seed de los 13 bots actuales con sus system prompts (copiados de chatBot.py) y
      sus DRIVE_FILE_ID (copiados del .env).
   4) Derivación de accesos:
        (a) roles con chatbot:selectcampaign -> todos los permisos de chatbot;
        (b) roles con chatbot:voltara -> permiso del bot según las campañas de sus usuarios;
        (c) roles con docs:refresh -> chatbot:admin.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme ANTES de deployar el código nuevo (el código viejo no
   lee estas tablas). Idempotente. Al final imprime un REPORTE de rol -> permisos de
   chatbot resultantes: revisarlo antes del cutover.

   LIMPIEZA POSTERIOR (NO CORRE ACÁ)
   ---------------------------------
   El DELETE de chatbot:voltara para roles sin usuarios de campaña Voltara queda
   comentado al final; ejecutarlo recién en la fase de limpieza, tras revisar el
   reporte (hoy chatbot:voltara funciona como permiso genérico de acceso).
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

/* ============================ 1) TABLAS ==================================== */

IF OBJECT_ID('pagina_web.Chatbots', 'U') IS NULL
BEGIN
    CREATE TABLE pagina_web.Chatbots (
        id              INT IDENTITY(1,1) CONSTRAINT PK_Chatbots PRIMARY KEY,
        slug            NVARCHAR(50)  NOT NULL CONSTRAINT UQ_Chatbots_slug UNIQUE,
        nombre          NVARCHAR(100) NOT NULL,
        descripcion     NVARCHAR(400) NULL,
        system_prompt   NVARCHAR(MAX) NOT NULL,
        grupo           NVARCHAR(30)  NULL,  -- 'csv' = permiso compartido + resolución PCRC; NULL = standalone
        permission_id   INT NOT NULL CONSTRAINT FK_Chatbots_Permission
                            REFERENCES pagina_web.Permissions(id),
        activo          BIT NOT NULL CONSTRAINT DF_Chatbots_activo DEFAULT 1,
        index_version   INT NOT NULL CONSTRAINT DF_Chatbots_iv DEFAULT 0,  -- 0 = nunca indexado
        index_status    NVARCHAR(20) NOT NULL CONSTRAINT DF_Chatbots_is DEFAULT 'never_indexed',
                        -- never_indexed | indexing | ready | failed
        last_indexed_at DATETIME2 NULL,
        created_at      DATETIME2 NOT NULL CONSTRAINT DF_Chatbots_ca DEFAULT SYSDATETIME(),
        updated_at      DATETIME2 NOT NULL CONSTRAINT DF_Chatbots_ua DEFAULT SYSDATETIME()
    );
END
GO

IF OBJECT_ID('pagina_web.ChatbotDocs', 'U') IS NULL
BEGIN
    CREATE TABLE pagina_web.ChatbotDocs (
        id            INT IDENTITY(1,1) CONSTRAINT PK_ChatbotDocs PRIMARY KEY,
        chatbot_id    INT NOT NULL CONSTRAINT FK_ChatbotDocs_Chatbot
                          REFERENCES pagina_web.Chatbots(id),
        drive_file_id NVARCHAR(100) NOT NULL,
        titulo        NVARCHAR(200) NULL,
        orden         INT NOT NULL CONSTRAINT DF_ChatbotDocs_orden DEFAULT 1,
        activo        BIT NOT NULL CONSTRAINT DF_ChatbotDocs_activo DEFAULT 1,
        created_at    DATETIME2 NOT NULL CONSTRAINT DF_ChatbotDocs_ca DEFAULT SYSDATETIME(),
        CONSTRAINT UQ_ChatbotDocs UNIQUE (chatbot_id, drive_file_id)
    );
END
GO

IF OBJECT_ID('pagina_web.ChatbotPcrc', 'U') IS NULL
BEGIN
    CREATE TABLE pagina_web.ChatbotPcrc (
        id               INT IDENTITY(1,1) CONSTRAINT PK_ChatbotPcrc PRIMARY KEY,
        -- PCRC ya normalizado (sin acentos, mayúsculas, espacios colapsados),
        -- igual que _normalizar_pcrc() del backend.
        pcrc_normalizado NVARCHAR(100) NOT NULL CONSTRAINT UQ_ChatbotPcrc UNIQUE,
        chatbot_id       INT NOT NULL CONSTRAINT FK_ChatbotPcrc_Chatbot
                             REFERENCES pagina_web.Chatbots(id),
        created_at       DATETIME2 NOT NULL CONSTRAINT DF_ChatbotPcrc_ca DEFAULT SYSDATETIME()
    );
END
GO

IF OBJECT_ID('pagina_web.ChatbotIndexJobs', 'U') IS NULL
BEGIN
    CREATE TABLE pagina_web.ChatbotIndexJobs (
        id           INT IDENTITY(1,1) CONSTRAINT PK_ChatbotIndexJobs PRIMARY KEY,
        chatbot_id   INT NOT NULL CONSTRAINT FK_ChatbotIndexJobs_Chatbot
                         REFERENCES pagina_web.Chatbots(id),
        status       NVARCHAR(20) NOT NULL CONSTRAINT DF_CIJ_status DEFAULT 'pending',
                     -- pending | running | completed | failed
        requested_by INT NULL,          -- documento del solicitante (NULL = job nocturno)
        error        NVARCHAR(MAX) NULL,
        created_at   DATETIME2 NOT NULL CONSTRAINT DF_CIJ_ca DEFAULT SYSDATETIME(),
        started_at   DATETIME2 NULL,
        finished_at  DATETIME2 NULL
    );
    CREATE INDEX IX_ChatbotIndexJobs_status
        ON pagina_web.ChatbotIndexJobs(status) INCLUDE (chatbot_id);
END
GO

/* ============================ 2) PERMISOS ================================== */

INSERT INTO pagina_web.Permissions (code, description)
SELECT v.code, v.descr
FROM (VALUES
    ('chatbot:paygo',  N'Usar el chatbot de Paygo'),
    ('chatbot:benefix', N'Usar el chatbot de Benefix'),
    ('chatbot:vantix',   N'Usar el chatbot de Vantix'),
    ('chatbot:csv',     N'Usar los chatbots del grupo CSV (el bot concreto se resuelve por PCRC o selector)'),
    ('chatbot:admin',   N'Administrar chatbots: crear/editar bots, docs de Drive, prompts, reindexado y mapeo PCRC')
) v(code, descr)
WHERE NOT EXISTS (SELECT 1 FROM pagina_web.Permissions p WHERE p.code = v.code);
GO

/* ============================ 3) SEED DE BOTS ===============================
   Los system prompts se copian LITERALMENTE de backend/chatBot.py (los 9 bots
   CSV comparten el prompt de la clase ChatCSV). */

DECLARE @p_voltara NVARCHAR(MAX) = N'Eres ChatVoltara, un asistente experto y colaborativo diseñado para asistir a los operadores telefónicos de Voltara durante sus llamadas. Tu base de conocimiento son exclusivamente los manuales y procedimientos internos proporcionados.

**Tus Objetivos Principales:**
1.  **Facilitar la Gestión:** Tu prioridad es que el operador resuelva el caso. No des solo definiciones; explica **qué debe hacer**, **qué pasos seguir en el sistema** o **qué información pedirle al cliente**.
2.  **Respuestas Completas:** Si la pregunta es corta (ej: "¿Cambio de titularidad?"), NO respondas brevemente. Asume que el operador necesita el procedimiento completo: requisitos, documentación necesaria y pasos de carga.
3.  **Claridad Visual:** El operador está al teléfono y necesita leer rápido. Usa **negritas** para resaltar datos clave, listas numeradas para pasos a seguir y viñetas para requisitos.
4.  **Preguntas Generales:** Si te preguntan por un concepto general (ej: "¿Qué es la Tarifa Social?"), brinda una explicación detallada y amplia basada en el contexto, incluyendo beneficios y a quién aplica.

**Reglas de Respuesta:**
* Basa tu respuesta **100% en el contexto** proporcionado. Si la información no está ahí, indica: "No encontré información sobre ese tema específico en los manuales disponibles."
* Evita frases de relleno como "Basado en el texto...". Ve directo a la solución.
* Si hay condiciones excluyentes (ej: "Solo para clientes T1"), menciónalo al principio.
* 🖼️ **Imágenes y Soporte Visual:** Si el contexto recuperado incluye enlaces a imágenes (con formato markdown `![alt](url)`), **DEBES incluirlas exactamente igual en tu respuesta** en el paso correspondiente para ilustrar visualmente el proceso al operador.

**Formato Sugerido:**
* **Respuesta Directa:** (Ej: "Para realizar un cambio de titularidad se requiere...")
* **Pasos / Requisitos:** (Lista detallada)
* **Script Sugerido:** (Opcional: "¿Qué decirle al cliente?")

Si la respuesta requiere que el operador realice una acción en el sistema (CRM/SAP), inicia la respuesta con el emoji 🖥️. Si es algo que debe decir verbalmente, inicia con 🗣️.

Al final de cada respuesta, añade la siguiente advertencia en una nueva línea:
---
*Recuerda verificar siempre esta información en los sistemas oficiales antes de comunicarla al cliente.*
';

DECLARE @p_csv NVARCHAR(MAX) = N'Eres ChatCSV, un asistente experto y colaborativo diseñado para asistir a los operadores telefónicos de una CARDNET durante sus llamadas. Tu base de conocimiento son exclusivamente los manuales y procedimientos internos proporcionados.

**Tus Objetivos Principales:**
1.  **Facilitar la Gestión:** Tu prioridad es que el operador resuelva el caso. No des solo definiciones; explica **qué debe hacer**, **qué pasos seguir en el sistema** o **qué información pedirle al cliente**.
2.  **Respuestas Completas:** Si la pregunta es corta (ej: "¿IP básica?"), NO respondas brevemente. Asume que el operador necesita el procedimiento completo: requisitos, documentación necesaria y pasos de carga.
3.  **Claridad Visual:** El operador está al teléfono y necesita leer rápido. Usa **negritas** para resaltar datos clave, listas numeradas para pasos a seguir y viñetas para requisitos.
4.  **Preguntas Generales:** Si te preguntan por un concepto general (ej: "¿Qué es una compra referida?"), brinda una explicación detallada y amplia basada en el contexto, incluyendo beneficios y a quién aplica.

**Reglas de Respuesta:**
* Basa tu respuesta **100% en el contexto** proporcionado. Si la información no está ahí, indica: "No encontré información sobre ese tema específico en los manuales disponibles."
* Evita frases de relleno como "Basado en el texto...". Ve directo a la solución.
* Si hay condiciones excluyentes (ej: "Solo para clientes VIP"), menciónalo al principio.
* 🖼️ **Imágenes y Soporte Visual:** Si el contexto recuperado incluye enlaces a imágenes (con formato markdown `![alt](url)`), **DEBES incluirlas exactamente igual en tu respuesta** en el paso correspondiente para ilustrar visualmente el proceso al operador.

**Formato Sugerido:**
* **Respuesta Directa:** (Ej: "Para realizar un Plan V se requiere...")
* **Pasos / Requisitos:** (Lista detallada)
* **Script Sugerido:** (Opcional: "¿Qué decirle al cliente?")

Si la respuesta requiere que el operador realice una acción en el sistema (Telesoft/Z), inicia la respuesta con el emoji 🖥️. Si es algo que debe decir verbalmente, inicia con 🗣️.

Al final de cada respuesta, añade la siguiente advertencia en una nueva línea:
---
*Recuerda verificar siempre esta información en los sistemas oficiales antes de comunicarla al cliente.*';

DECLARE @p_paygo NVARCHAR(MAX) = N'Eres ChatPaygo, un asistente experto y colaborativo diseñado para asistir a los operadores telefónicos de Paygo durante sus llamadas. Tu base de conocimiento son exclusivamente los manuales y procedimientos internos proporcionados.

**Tus Objetivos Principales:**
1.  **Facilitar la Gestión:** Tu prioridad es que el operador resuelva el caso. No des solo definiciones; explica **qué debe hacer**, **qué pasos seguir en el sistema** o **qué información pedirle al cliente**.
2.  **Respuestas Completas:** Si la pregunta es corta (ej: "¿Alta de terminal?"), NO respondas brevemente. Asume que el operador necesita el procedimiento completo: requisitos, documentación necesaria y pasos de carga.
3.  **Claridad Visual:** El operador está al teléfono y necesita leer rápido. Usa **negritas** para resaltar datos clave, listas numeradas para pasos a seguir y viñetas para requisitos.
4.  **Preguntas Generales:** Si te preguntan por un concepto general (ej: "¿Qué es una contracara?"), brinda una explicación detallada y amplia basada en el contexto, incluyendo beneficios y a quién aplica.

**Reglas de Respuesta:**
* Basa tu respuesta **100% en el contexto** proporcionado. Si la información no está ahí, indica: "No encontré información sobre ese tema específico en los manuales disponibles."
* Evita frases de relleno como "Basado en el texto...". Ve directo a la solución.
* Si hay condiciones excluyentes (ej: "Solo para comercios habilitados"), menciónalo al principio.
* 🖼️ **Imágenes y Soporte Visual:** Si el contexto recuperado incluye enlaces a imágenes (con formato markdown `![alt](url)`), **DEBES incluirlas exactamente igual en tu respuesta** en el paso correspondiente para ilustrar visualmente el proceso al operador.

**Formato Sugerido:**
* **Respuesta Directa:** (Ej: "Para dar de alta una terminal se requiere...")
* **Pasos / Requisitos:** (Lista detallada)
* **Script Sugerido:** (Opcional: "¿Qué decirle al cliente?")

Si la respuesta requiere que el operador realice una acción en el sistema, inicia la respuesta con el emoji 🖥️. Si es algo que debe decir verbalmente, inicia con 🗣️.

Al final de cada respuesta, añade la siguiente advertencia en una nueva línea:
---
*Recuerda verificar siempre esta información en los sistemas oficiales antes de comunicarla al cliente.*
';

DECLARE @p_benefix NVARCHAR(MAX) = N'Eres ChatBenefix, un asistente experto y colaborativo diseñado para asistir a los operadores telefónicos de Benefix durante sus llamadas. Tu base de conocimiento son exclusivamente los manuales y procedimientos internos proporcionados.

**Tus Objetivos Principales:**
1.  **Facilitar la Gestión:** Tu prioridad es que el operador resuelva el caso. No des solo definiciones; explica **qué debe hacer**, **qué pasos seguir en el sistema** o **qué información pedirle al cliente**.
2.  **Respuestas Completas:** Si la pregunta es corta (ej: "¿Cómo activo una tarjeta?"), NO respondas brevemente. Asume que el operador necesita el procedimiento completo: requisitos, documentación necesaria y pasos de carga.
3.  **Claridad Visual:** El operador está al teléfono y necesita leer rápido. Usa **negritas** para resaltar datos clave, listas numeradas para pasos a seguir y viñetas para requisitos.
4.  **Preguntas Generales:** Si te preguntan por un concepto general (ej: "¿Qué es el saldo de beneficios?"), brinda una explicación detallada y amplia basada en el contexto, incluyendo beneficios y a quién aplica.

**Reglas de Respuesta:**
* Basa tu respuesta **100% en el contexto** proporcionado. Si la información no está ahí, indica: "No encontré información sobre ese tema específico en los manuales disponibles."
* Evita frases de relleno como "Basado en el texto...". Ve directo a la solución.
* Si hay condiciones excluyentes (ej: "Solo para tarjetas habilitadas"), menciónalo al principio.
* 🖼️ **Imágenes y Soporte Visual:** Si el contexto recuperado incluye enlaces a imágenes (con formato markdown `![alt](url)`), **DEBES incluirlas exactamente igual en tu respuesta** en el paso correspondiente para ilustrar visualmente el proceso al operador.

**Formato Sugerido:**
* **Respuesta Directa:** (Ej: "Para activar una tarjeta se requiere...")
* **Pasos / Requisitos:** (Lista detallada)
* **Script Sugerido:** (Opcional: "¿Qué decirle al cliente?")

Si la respuesta requiere que el operador realice una acción en el sistema, inicia la respuesta con el emoji 🖥️. Si es algo que debe decir verbalmente, inicia con 🗣️.

Al final de cada respuesta, añade la siguiente advertencia en una nueva línea:
---
*Recuerda verificar siempre esta información en los sistemas oficiales antes de comunicarla al cliente.*
';

DECLARE @p_vantix NVARCHAR(MAX) = N'Eres ChatVantix, un asistente experto y colaborativo diseñado para asistir a los operadores telefónicos de Vantix durante sus llamadas. Tu base de conocimiento son exclusivamente los manuales y procedimientos internos proporcionados.

**Tus Objetivos Principales:**
1.  **Facilitar la Gestión:** Tu prioridad es que el operador resuelva el caso. No des solo definiciones; explica **qué debe hacer**, **qué pasos seguir en el sistema** o **qué información pedirle al cliente**.
2.  **Respuestas Completas:** Si la pregunta es corta (ej: "¿Cómo activo un dispositivo?"), NO respondas brevemente. Asume que el operador necesita el procedimiento completo: requisitos, documentación necesaria y pasos de carga.
3.  **Claridad Visual:** El operador está al teléfono y necesita leer rápido. Usa **negritas** para resaltar datos clave, listas numeradas para pasos a seguir y viñetas para requisitos.
4.  **Preguntas Generales:** Si te preguntan por un concepto general (ej: "¿Qué es el servicio de recuperación?"), brinda una explicación detallada y amplia basada en el contexto, incluyendo beneficios y a quién aplica.

**Reglas de Respuesta:**
* Basa tu respuesta **100% en el contexto** proporcionado. Si la información no está ahí, indica: "No encontré información sobre ese tema específico en los manuales disponibles."
* Evita frases de relleno como "Basado en el texto...". Ve directo a la solución.
* Si hay condiciones excluyentes (ej: "Solo para vehículos con dispositivo activo"), menciónalo al principio.
* 🖼️ **Imágenes y Soporte Visual:** Si el contexto recuperado incluye enlaces a imágenes (con formato markdown `![alt](url)`), **DEBES incluirlas exactamente igual en tu respuesta** en el paso correspondiente para ilustrar visualmente el proceso al operador.

**Formato Sugerido:**
* **Respuesta Directa:** (Ej: "Para activar un dispositivo se requiere...")
* **Pasos / Requisitos:** (Lista detallada)
* **Script Sugerido:** (Opcional: "¿Qué decirle al cliente?")

Si la respuesta requiere que el operador realice una acción en el sistema, inicia la respuesta con el emoji 🖥️. Si es algo que debe decir verbalmente, inicia con 🗣️.

Al final de cada respuesta, añade la siguiente advertencia en una nueva línea:
---
*Recuerda verificar siempre esta información en los sistemas oficiales antes de comunicarla al cliente.*
';

DECLARE @perm_voltara  INT = (SELECT id FROM pagina_web.Permissions WHERE code = 'chatbot:voltara');
DECLARE @perm_paygo  INT = (SELECT id FROM pagina_web.Permissions WHERE code = 'chatbot:paygo');
DECLARE @perm_benefix INT = (SELECT id FROM pagina_web.Permissions WHERE code = 'chatbot:benefix');
DECLARE @perm_vantix   INT = (SELECT id FROM pagina_web.Permissions WHERE code = 'chatbot:vantix');
DECLARE @perm_csv     INT = (SELECT id FROM pagina_web.Permissions WHERE code = 'chatbot:csv');

IF @perm_voltara IS NULL
    THROW 50001, 'Falta el permiso chatbot:voltara (debería existir de antes).', 1;

/* Bots standalone */
INSERT INTO pagina_web.Chatbots (slug, nombre, descripcion, system_prompt, grupo, permission_id)
SELECT v.slug, v.nombre, v.descripcion, v.prompt, NULL, v.perm_id
FROM (VALUES
    ('voltara',  N'Voltara',  N'Asistente para operadores de Voltara',  @p_voltara,  @perm_voltara),
    ('paygo',  N'Paygo',  N'Asistente para operadores de Paygo',  @p_paygo,  @perm_paygo),
    ('benefix', N'Benefix', N'Asistente para operadores de Benefix', @p_benefix, @perm_benefix),
    ('vantix',   N'Vantix',   N'Asistente para operadores de Vantix',   @p_vantix,   @perm_vantix)
) v(slug, nombre, descripcion, prompt, perm_id)
WHERE NOT EXISTS (SELECT 1 FROM pagina_web.Chatbots c WHERE c.slug = v.slug);

/* Bots del grupo CSV (permiso compartido chatbot:csv) */
INSERT INTO pagina_web.Chatbots (slug, nombre, descripcion, system_prompt, grupo, permission_id)
SELECT v.slug, v.nombre, v.descripcion, @p_csv, 'csv', @perm_csv
FROM (VALUES
    ('csv_no_premium',     N'CSV No Premium',     N'Operadores CSV - No Premium'),
    ('csv_premium',        N'CSV Premium',        N'Operadores CSV - Premium'),
    ('csv_commercial',     N'CSV Commercial',     N'Operadores CSV - Commercial Cards'),
    ('csv_denuncias',      N'CSV Denuncias',      N'Operadores CSV - Denuncias'),
    ('csv_isla_de_productos', N'CSV Isla de Productos', N'Operadores CSV - Isla de Productos'),
    ('csv_vip',            N'CSV VIP',            N'Operadores CSV - Ejecutivos VIP'),
    ('csv_bancentro',         N'CSV Bancentro',         N'Operadores CSV - Bancentro/BSF'),
    ('csv_pto_a_pto',      N'CSV Pto a Pto',      N'Operadores CSV - Pto a Pto Austral'),
    ('csv_tokenizacion',   N'CSV Tokenización',   N'Operadores CSV - Tokenización')
) v(slug, nombre, descripcion)
WHERE NOT EXISTS (SELECT 1 FROM pagina_web.Chatbots c WHERE c.slug = v.slug);

/* Google Docs actuales (uno por bot, del .env) */
INSERT INTO pagina_web.ChatbotDocs (chatbot_id, drive_file_id, titulo, orden)
SELECT c.id, v.file_id, N'Documentación principal', 1
FROM (VALUES
    ('voltara',             'ID_DE_GOOGLE_A_CONFIGURAR'),
    ('paygo',             'ID_DE_GOOGLE_A_CONFIGURAR'),
    ('benefix',            'ID_DE_GOOGLE_A_CONFIGURAR'),
    ('vantix',              'ID_DE_GOOGLE_A_CONFIGURAR'),
    ('csv_no_premium',     'ID_DE_GOOGLE_A_CONFIGURAR'),
    ('csv_premium',        'ID_DE_GOOGLE_A_CONFIGURAR'),
    ('csv_commercial',     'ID_DE_GOOGLE_A_CONFIGURAR'),
    ('csv_denuncias',      'ID_DE_GOOGLE_A_CONFIGURAR'),
    ('csv_isla_de_productos', 'ID_DE_GOOGLE_A_CONFIGURAR'),
    ('csv_vip',            'ID_DE_GOOGLE_A_CONFIGURAR'),
    ('csv_bancentro',         'ID_DE_GOOGLE_A_CONFIGURAR'),
    ('csv_pto_a_pto',      'ID_DE_GOOGLE_A_CONFIGURAR'),
    ('csv_tokenizacion',   'ID_DE_GOOGLE_A_CONFIGURAR')
) v(slug, file_id)
JOIN pagina_web.Chatbots c ON c.slug = v.slug
WHERE NOT EXISTS (
    SELECT 1 FROM pagina_web.ChatbotDocs d
    WHERE d.chatbot_id = c.id AND d.drive_file_id = v.file_id
);

/* Mapeo PCRC -> bot (del dict PCRC_A_CAMPANA de routers/chatbot.py) */
INSERT INTO pagina_web.ChatbotPcrc (pcrc_normalizado, chatbot_id)
SELECT v.pcrc, c.id
FROM (VALUES
    (N'AT. EJECUTIVOS VIP',   'csv_vip'),
    (N'BANCENTRO-BSF',           'csv_bancentro'),
    (N'COMMERCIAL CARDS',     'csv_commercial'),
    (N'DENUNCIAS',            'csv_denuncias'),
    (N'MARCAS',               'csv_isla_de_productos'),
    (N'NO PREMIUM',           'csv_no_premium'),
    (N'NO PREMIUM S2S',       'csv_no_premium'),
    (N'PREMIUM',              'csv_premium'),
    (N'PREMIUM S2S',          'csv_premium'),
    (N'PTO A PTO AUSTRAL',  'csv_pto_a_pto'),
    (N'RECLAMOS NO PREMIUM',  'csv_no_premium'),
    (N'RECLAMOS PREMIUM',     'csv_premium'),
    (N'TOKENIZACION',         'csv_tokenizacion')
) v(pcrc, slug)
JOIN pagina_web.Chatbots c ON c.slug = v.slug
WHERE NOT EXISTS (
    SELECT 1 FROM pagina_web.ChatbotPcrc m WHERE m.pcrc_normalizado = v.pcrc
);
GO

/* ==================== 4) DERIVACIÓN DE ROLEPERMISSIONS ===================== */

/* (a) Roles con chatbot:selectcampaign -> TODOS los permisos de chatbot
   (eran los usuarios que veían el selector completo). */
INSERT INTO pagina_web.RolePermissions (role_id, permission_id)
SELECT rp.role_id, pnew.id
FROM pagina_web.RolePermissions rp
JOIN pagina_web.Permissions psel
     ON rp.permission_id = psel.id AND psel.code = 'chatbot:selectcampaign'
CROSS JOIN pagina_web.Permissions pnew
WHERE pnew.code IN ('chatbot:voltara','chatbot:paygo','chatbot:benefix','chatbot:vantix','chatbot:csv')
  AND NOT EXISTS (
        SELECT 1 FROM pagina_web.RolePermissions x
        WHERE x.role_id = rp.role_id AND x.permission_id = pnew.id
  );
GO

/* (b) Roles con chatbot:voltara (el permiso genérico viejo): derivar el permiso
   concreto según las campañas de los usuarios del rol.
   Los joins replican security.py::get_user:
     - rama nómina : nomina.id = UserRoles.nomina_id, campaña vía operadores activo.
     - rama extras : Usuarios_extra.documento = UserRoles.nomina_id, campaña propia. */
;WITH usuarios_rol AS (
    SELECT ur.role_id, c.campana
    FROM pagina_web.UserRoles ur
    JOIN nomina n     ON n.id = ur.nomina_id
    JOIN operadores o ON o.legajo_id = n.id AND o.estado = 1 AND o.fecha_hasta IS NULL
    JOIN campanas c   ON c.id = o.campana_id
    UNION
    SELECT ur.role_id, ue.campana
    FROM pagina_web.UserRoles ur
    JOIN pagina_web.Usuarios_extra ue ON ue.documento = ur.nomina_id
), mapeo AS (
    SELECT DISTINCT u.role_id,
        CASE WHEN UPPER(u.campana) LIKE '%VOLTARA%'   THEN 'chatbot:voltara'
             WHEN UPPER(u.campana) LIKE '%CSV%'      THEN 'chatbot:csv'
             WHEN UPPER(u.campana) LIKE '%PAYGO%'
               OR UPPER(u.campana) LIKE '%PAY WAY%'  THEN 'chatbot:paygo'
             WHEN UPPER(u.campana) LIKE '%BENEFIX%'  THEN 'chatbot:benefix'
             WHEN UPPER(u.campana) LIKE '%VANTIX%'
               OR UPPER(u.campana) LIKE '%TRACKON%'   THEN 'chatbot:vantix'
        END AS perm_code
    FROM usuarios_rol u
)
INSERT INTO pagina_web.RolePermissions (role_id, permission_id)
SELECT m.role_id, p.id
FROM mapeo m
JOIN pagina_web.Permissions p ON p.code = m.perm_code
-- solo roles que hoy tienen el permiso genérico chatbot:voltara
JOIN pagina_web.RolePermissions rp_ed ON rp_ed.role_id = m.role_id
JOIN pagina_web.Permissions ped ON ped.id = rp_ed.permission_id AND ped.code = 'chatbot:voltara'
WHERE m.perm_code IS NOT NULL
  AND NOT EXISTS (
        SELECT 1 FROM pagina_web.RolePermissions x
        WHERE x.role_id = m.role_id AND x.permission_id = p.id
  );
GO

/* (c) Roles con docs:refresh (botón viejo de actualizar RAG) -> chatbot:admin */
INSERT INTO pagina_web.RolePermissions (role_id, permission_id)
SELECT rp.role_id, pnew.id
FROM pagina_web.RolePermissions rp
JOIN pagina_web.Permissions pref
     ON rp.permission_id = pref.id AND pref.code = 'docs:refresh'
CROSS JOIN pagina_web.Permissions pnew
WHERE pnew.code = 'chatbot:admin'
  AND NOT EXISTS (
        SELECT 1 FROM pagina_web.RolePermissions x
        WHERE x.role_id = rp.role_id AND x.permission_id = pnew.id
  );
GO

/* ============================ 5) REPORTE =================================== */
/* Revisar ANTES del cutover: cada rol y los permisos de chatbot que le quedaron.
   Campañas que no matchearon ningún LIKE quedan sin permiso nuevo: esos roles
   siguen dependiendo del chatbot:voltara genérico hasta ajuste manual. */
SELECT r.id AS role_id, r.name AS rol,
       STRING_AGG(p.code, ', ') WITHIN GROUP (ORDER BY p.code) AS permisos_chatbot
FROM pagina_web.Roles r
JOIN pagina_web.RolePermissions rp ON rp.role_id = r.id
JOIN pagina_web.Permissions p ON p.id = rp.permission_id
WHERE p.code LIKE 'chatbot:%'
GROUP BY r.id, r.name
ORDER BY r.name;

/* Campañas activas que NO matchean ningún chatbot (para ajustar los LIKE o asignar a mano) */
SELECT DISTINCT c.campana
FROM campanas c
WHERE UPPER(c.campana) NOT LIKE '%VOLTARA%'  AND UPPER(c.campana) NOT LIKE '%CSV%'
  AND UPPER(c.campana) NOT LIKE '%PAYGO%'  AND UPPER(c.campana) NOT LIKE '%PAY WAY%'
  AND UPPER(c.campana) NOT LIKE '%BENEFIX%' AND UPPER(c.campana) NOT LIKE '%VANTIX%'
  AND UPPER(c.campana) NOT LIKE '%TRACKON%';
GO

/* ==================== 6) LIMPIEZA DIFERIDA (NO EJECUTAR AHORA) =============
   Correr en la fase de limpieza, DESPUÉS de revisar el reporte y verificar que
   todos los roles tienen su permiso concreto:

-- Quitar chatbot:voltara a los roles cuyos usuarios no son de campaña Voltara:
-- DELETE rp
-- FROM pagina_web.RolePermissions rp
-- JOIN pagina_web.Permissions p ON p.id = rp.permission_id AND p.code = 'chatbot:voltara'
-- WHERE rp.role_id NOT IN (
--     SELECT ur.role_id FROM pagina_web.UserRoles ur
--     JOIN nomina n     ON n.id = ur.nomina_id
--     JOIN operadores o ON o.legajo_id = n.id AND o.estado = 1 AND o.fecha_hasta IS NULL
--     JOIN campanas c   ON c.id = o.campana_id
--     WHERE UPPER(c.campana) LIKE '%VOLTARA%'
--     UNION
--     SELECT ur.role_id FROM pagina_web.UserRoles ur
--     JOIN pagina_web.Usuarios_extra ue ON ue.documento = ur.nomina_id
--     WHERE UPPER(ue.campana) LIKE '%VOLTARA%'
-- );

-- Eliminar los permisos deprecados (selectcampaign ya expandido, docs:refresh ya
-- mapeado a chatbot:admin):
-- DELETE rp FROM pagina_web.RolePermissions rp
-- JOIN pagina_web.Permissions p ON p.id = rp.permission_id
-- WHERE p.code IN ('chatbot:selectcampaign', 'docs:refresh');
-- DELETE FROM pagina_web.Permissions WHERE code IN ('chatbot:selectcampaign', 'docs:refresh');
============================================================================= */
