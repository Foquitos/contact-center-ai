/* ============================================================================
   Feature — Golden Set (Fase 0): verdad humana sobre las auditorías de la IA
   Fecha: 2026-08-06
   Autor: equipo Acme

   POR QUÉ
   -------
   Hoy no hay forma de saber cuánto acierta la IA auditora: nadie registra
   cuándo se equivoca. La única señal es el ojo del analista, que no queda
   escrito en ningún lado. Sin verdad humana no se puede medir una plantilla ni,
   más adelante, mejorar su prompt con evidencia (Fase 3): un set dorado
   generado por la misma IA sería circular.

   Esta migración crea la CAPTURA. No mide nada por sí sola.

   QUÉ AGREGA
   ----------
   1) calidad.AuditoriaRevisiones — 1 fila = un humano revisó UNA auditoría
      entera. Es el DENOMINADOR: sin ella solo se sabrían los errores (numerador)
      y no sobre cuántos casos, con lo cual no hay accuracy posible.
      UNIQUE (AuditoriaID, RevisorUsuarioID): un revisor deja una sola revisión
      por auditoría (se pisa al reenviar), pero DOS revisores pueden revisar la
      misma auditoría — eso es lo que habilita medir el acuerdo entre humanos,
      que es el techo real de lo que se le puede exigir a la IA.

   2) calidad.AuditoriaRevisionDetalles — 1 fila = el veredicto humano sobre UN
      atributo de esa auditoría. Guarda ValorIA (snapshot de lo que respondió la
      IA en el momento de revisar) y ValorHumano. Se guardan TAMBIÉN los
      acuerdos (Coincide = 1), no solo las correcciones.
      ValorHumano NULL = "este atributo no correspondía responderlo" (el caso de
      los atributos opcionales, ver migración 2026-08-05b).
      NUNCA se toca calidad.AuditoriaDetalles: la respuesta de la IA queda
      intacta, la corrección vive aparte. Si se pisara, se perdería justamente el
      dato que se quiere medir.

   3) calidad.GoldenSets / calidad.GoldenSetItems — el set congelado de
      referencia de una plantilla. Un item apunta a la auditoría revisada de la
      que sale la verdad. Split 'train' | 'test': el proponente de prompts (Fase
      3) solo puede mirar 'train' y se valida contra 'test'; sin esa separación
      el prompt se sobreajusta al set y la métrica sube sin que la auditoría real
      mejore.

   4) calidad.AudioAuditoria.Fijado — pin contra el descarte FIFO. El store de
      audio tiene un tope de 5 GB por entorno y borra los más antiguos
      (audio_store.aplicar_tope): sin esto, a las pocas semanas el golden set se
      queda sin audios y deja de poder re-auditarse. Los audios fijados quedan
      fuera del barrido y no cuentan para el tope.

   5) Permisos audit:review y goldenset:manage.

   DECISIÓN: los permisos nacen SIN ASIGNAR (mismo criterio que template:modelo_ia
   y uso_ia.chatbot). Hasta que se asignen rol por rol, solo el super admin
   revisa auditorías y administra sets. audit:review es ADICIONAL a audit:execute
   (el router de auditorías lo sigue exigiendo).

   CÓMO CORRER
   -----------
   Contra la BD Acme, con un usuario con DDL sobre el schema calidad.
   Idempotente. Aplicar ANTES del deploy del código.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

/* --------------------------------------------------------------------------
   1) Revisión humana de una auditoría (cabecera)
   -------------------------------------------------------------------------- */
IF OBJECT_ID('calidad.AuditoriaRevisiones', 'U') IS NULL
BEGIN
    CREATE TABLE calidad.AuditoriaRevisiones (
        RevisionID        BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_AuditoriaRevisiones PRIMARY KEY,
        AuditoriaID       BIGINT        NOT NULL,
        RevisorUsuarioID  INT           NOT NULL,
        -- Snapshot de contexto: permite filtrar/agrupar sin joinear Auditorias
        -- (y sobrevive si la auditoría se borra por retención).
        PlantillaID       INT           NULL,
        IdAplicativo      NVARCHAR(200) NULL,
        Comentario        NVARCHAR(2000) NULL,     -- observación general del revisor
        FechaRevision     DATETIME2     NOT NULL
            CONSTRAINT DF_AuditoriaRevisiones_Fecha DEFAULT(SYSUTCDATETIME()),
        FechaModificacion DATETIME2     NULL       -- se sella al re-enviar la revisión
    );

    -- Un revisor, una revisión por auditoría (el re-envío actualiza, no duplica).
    ALTER TABLE calidad.AuditoriaRevisiones
        ADD CONSTRAINT UQ_AuditoriaRevisiones_Auditoria_Revisor
        UNIQUE (AuditoriaID, RevisorUsuarioID);

    -- Barridos del evaluador: "todas las revisiones de esta plantilla".
    CREATE INDEX IX_AuditoriaRevisiones_Plantilla_Fecha
        ON calidad.AuditoriaRevisiones (PlantillaID, FechaRevision);
END
GO

/* --------------------------------------------------------------------------
   2) Veredicto humano por atributo
   -------------------------------------------------------------------------- */
IF OBJECT_ID('calidad.AuditoriaRevisionDetalles', 'U') IS NULL
BEGIN
    CREATE TABLE calidad.AuditoriaRevisionDetalles (
        RevisionDetalleID BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_AuditoriaRevisionDetalles PRIMARY KEY,
        RevisionID        BIGINT         NOT NULL,
        AtributoID        INT            NOT NULL,
        -- Lo que respondió la IA cuando se revisó. NULL = no respondió el
        -- atributo (opcional omitido por falta de evidencia).
        ValorIA           NVARCHAR(MAX)  NULL,
        -- Veredicto del humano. NULL = "no correspondía responderlo".
        ValorHumano       NVARCHAR(MAX)  NULL,
        -- Se persiste en vez de calcularse al vuelo: la comparación textual la
        -- hace Python normalizando (OK/ok, listas, N/A), y esa normalización
        -- tiene que quedar congelada con la revisión.
        Coincide          BIT            NOT NULL
            CONSTRAINT DF_AuditoriaRevisionDetalles_Coincide DEFAULT(1),
        -- Por qué la IA se equivocó. Es el insumo del proponente de prompts
        -- (Fase 3): sin el motivo, el LLM solo ve dos valores distintos.
        Motivo            NVARCHAR(1000) NULL,

        CONSTRAINT FK_AuditoriaRevisionDetalles_Revision
            FOREIGN KEY (RevisionID) REFERENCES calidad.AuditoriaRevisiones (RevisionID)
            ON DELETE CASCADE
    );

    ALTER TABLE calidad.AuditoriaRevisionDetalles
        ADD CONSTRAINT UQ_AuditoriaRevisionDetalles_Revision_Atributo
        UNIQUE (RevisionID, AtributoID);

    -- "Todos los desacuerdos del atributo X" (métricas por atributo).
    CREATE INDEX IX_AuditoriaRevisionDetalles_Atributo_Coincide
        ON calidad.AuditoriaRevisionDetalles (AtributoID, Coincide);
END
GO

/* --------------------------------------------------------------------------
   3) Golden Sets
   -------------------------------------------------------------------------- */
IF OBJECT_ID('calidad.GoldenSets', 'U') IS NULL
BEGIN
    CREATE TABLE calidad.GoldenSets (
        GoldenSetID       INT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_GoldenSets PRIMARY KEY,
        Nombre            NVARCHAR(255) NOT NULL,
        PlantillaID       INT           NOT NULL,   -- un set evalúa UNA plantilla
        Descripcion       NVARCHAR(1000) NULL,
        -- Los criterios de una campaña cambian: un set viejo mide la política
        -- vieja y empuja el prompt en la dirección equivocada. Esta fecha es
        -- para que el evaluador pueda avisar cuando el set está vencido.
        FechaRevisionRecomendada DATE  NULL,
        CreadoPorUsuarioID INT          NULL,
        FechaCreacion     DATETIME2     NOT NULL
            CONSTRAINT DF_GoldenSets_Fecha DEFAULT(SYSUTCDATETIME()),
        IsActive          BIT           NOT NULL
            CONSTRAINT DF_GoldenSets_Activo DEFAULT(1)
    );

    CREATE INDEX IX_GoldenSets_Plantilla ON calidad.GoldenSets (PlantillaID, IsActive);
END
GO

IF OBJECT_ID('calidad.GoldenSetItems', 'U') IS NULL
BEGIN
    CREATE TABLE calidad.GoldenSetItems (
        ItemID            BIGINT IDENTITY(1,1) NOT NULL
            CONSTRAINT PK_GoldenSetItems PRIMARY KEY,
        GoldenSetID       INT           NOT NULL,
        IdAplicativo      NVARCHAR(200) NOT NULL,   -- clave de la interacción (audio/transcripción)
        -- Auditoría revisada de la que sale la verdad de este item.
        AuditoriaID       BIGINT        NULL,
        -- 'train' (el proponente de prompts la ve) | 'test' (validación ciega).
        Split             NVARCHAR(10)  NOT NULL
            CONSTRAINT DF_GoldenSetItems_Split DEFAULT('train'),
        Notas             NVARCHAR(1000) NULL,
        AgregadoPorUsuarioID INT        NULL,
        FechaAgregado     DATETIME2     NOT NULL
            CONSTRAINT DF_GoldenSetItems_Fecha DEFAULT(SYSUTCDATETIME()),
        IsActive          BIT           NOT NULL
            CONSTRAINT DF_GoldenSetItems_Activo DEFAULT(1),

        CONSTRAINT FK_GoldenSetItems_Set
            FOREIGN KEY (GoldenSetID) REFERENCES calidad.GoldenSets (GoldenSetID)
            ON DELETE CASCADE,
        CONSTRAINT CK_GoldenSetItems_Split CHECK (Split IN ('train', 'test'))
    );

    -- Una interacción no puede entrar dos veces al mismo set (sesgaría la métrica).
    ALTER TABLE calidad.GoldenSetItems
        ADD CONSTRAINT UQ_GoldenSetItems_Set_Id UNIQUE (GoldenSetID, IdAplicativo);
END
GO

/* --------------------------------------------------------------------------
   4) Pin de audio contra el descarte FIFO (calidad.AudioAuditoria)
   -------------------------------------------------------------------------- */
IF COL_LENGTH('calidad.AudioAuditoria', 'Fijado') IS NULL
BEGIN
    ALTER TABLE calidad.AudioAuditoria
        ADD Fijado BIT NOT NULL CONSTRAINT DF_AudioAuditoria_Fijado DEFAULT(0);
END
GO

-- El barrido FIFO recorre por (Entorno, FechaCreacion) salteando los fijados;
-- con el filtro en el índice no los lee siquiera.
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_AudioAuditoria_Entorno_Fecha_NoFijado'
                 AND object_id = OBJECT_ID('calidad.AudioAuditoria'))
BEGIN
    CREATE INDEX IX_AudioAuditoria_Entorno_Fecha_NoFijado
        ON calidad.AudioAuditoria (Entorno, FechaCreacion)
        WHERE Fijado = 0;
END
GO

/* --------------------------------------------------------------------------
   5) Permisos (nacen sin asignar: solo super admin hasta repartirlos)
   -------------------------------------------------------------------------- */
IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'audit:review')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('audit:review',
            'Revisar y corregir las respuestas de la IA en Auditorías Realizadas (verdad humana); requiere además audit:execute');
END
GO

IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'goldenset:manage')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('goldenset:manage',
            'Administrar Golden Sets y ver la pantalla Evaluación de la IA; requiere además audit:execute');
END
GO

/* --------------------------------------------------------------------------
   Verificación
   -------------------------------------------------------------------------- */
SELECT
    OBJECT_ID('calidad.AuditoriaRevisiones', 'U')       AS Revisiones,
    OBJECT_ID('calidad.AuditoriaRevisionDetalles', 'U') AS RevisionDetalles,
    OBJECT_ID('calidad.GoldenSets', 'U')                AS GoldenSets,
    OBJECT_ID('calidad.GoldenSetItems', 'U')            AS GoldenSetItems,
    COL_LENGTH('calidad.AudioAuditoria', 'Fijado')      AS ColumnaFijado;
GO

SELECT code, description FROM pagina_web.Permissions
WHERE code IN ('audit:review', 'goldenset:manage');
GO
