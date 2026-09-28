/* ============================================================================
   Feature — Tips del Día por Rol con Herencia y Almacenamiento SQL
   Fecha: 2026-09-22
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   1) pagina_web.TipGroups — grupos temáticos de tips (ej: Calidad, Operadores,
      Supervisión).
   2) pagina_web.Tips — contenido de cada tip asociado a un grupo.
   3) pagina_web.TipGroupRoles — asignación N a M de grupos de tips a roles RBAC.
   4) pagina_web.RoleEffectiveTipGroups — vista recursiva (mismo patrón que
      RoleEffectivePermissions): si un grupo se asigna al rol padre, todos los
      roles hijos y descendientes heredan automáticamente el acceso a ese grupo.
   5) Permiso tips:manage — habilita la administración de grupos y tips en la
      plataforma. Nace SIN ASIGNAR (solo accesible por Super Admin hasta asignarlo).

   POR QUÉ
   -------
   Reemplaza la integración legacy con Google Sheets (SHEET_ID_TIPS), permitiendo
   que los tips se configuren directamente desde la interfaz web, queden
   almacenados en la base de datos SQL y se filtren de forma personalizada según
   el rol y la jerarquía de roles del usuario.

   CÓMO CORRER: contra la base de datos Acme, idempotente, ANTES del deploy del código.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

-- 1. Tabla de Grupos de Tips
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE object_id = OBJECT_ID('pagina_web.TipGroups'))
BEGIN
    CREATE TABLE pagina_web.TipGroups (
        id INT IDENTITY(1,1) NOT NULL,
        name VARCHAR(100) NOT NULL,
        description VARCHAR(255) NULL,
        activo BIT NOT NULL CONSTRAINT DF_TipGroups_activo DEFAULT 1,
        created_at DATETIME NOT NULL CONSTRAINT DF_TipGroups_created DEFAULT GETDATE(),
        updated_at DATETIME NOT NULL CONSTRAINT DF_TipGroups_updated DEFAULT GETDATE(),
        CONSTRAINT PK_TipGroups PRIMARY KEY CLUSTERED (id)
    );
END
GO

-- 2. Tabla de Tips individuales
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE object_id = OBJECT_ID('pagina_web.Tips'))
BEGIN
    CREATE TABLE pagina_web.Tips (
        id INT IDENTITY(1,1) NOT NULL,
        group_id INT NOT NULL,
        title VARCHAR(150) NULL,
        content NVARCHAR(MAX) NOT NULL,
        tipo VARCHAR(20) NOT NULL CONSTRAINT DF_Tips_tipo DEFAULT 'info',
        fecha_desde DATE NULL,
        fecha_hasta DATE NULL,
        es_prioritario BIT NOT NULL CONSTRAINT DF_Tips_es_prioritario DEFAULT 0,
        url_accion VARCHAR(500) NULL,
        texto_accion VARCHAR(100) NULL,
        activo BIT NOT NULL CONSTRAINT DF_Tips_activo DEFAULT 1,
        created_at DATETIME NOT NULL CONSTRAINT DF_Tips_created DEFAULT GETDATE(),
        updated_at DATETIME NOT NULL CONSTRAINT DF_Tips_updated DEFAULT GETDATE(),
        CONSTRAINT PK_Tips PRIMARY KEY CLUSTERED (id),
        CONSTRAINT FK_Tips_TipGroups FOREIGN KEY (group_id)
            REFERENCES pagina_web.TipGroups(id) ON DELETE CASCADE
    );

    CREATE NONCLUSTERED INDEX IX_Tips_group_id_activo ON pagina_web.Tips (group_id, activo);
END
ELSE
BEGIN
    -- Idempotencia para columnas agregadas
    IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('pagina_web.Tips') AND name = 'tipo')
        ALTER TABLE pagina_web.Tips ADD tipo VARCHAR(20) NOT NULL CONSTRAINT DF_Tips_tipo DEFAULT 'info';

    IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('pagina_web.Tips') AND name = 'fecha_desde')
        ALTER TABLE pagina_web.Tips ADD fecha_desde DATE NULL;

    IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('pagina_web.Tips') AND name = 'fecha_hasta')
        ALTER TABLE pagina_web.Tips ADD fecha_hasta DATE NULL;

    IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('pagina_web.Tips') AND name = 'es_prioritario')
        ALTER TABLE pagina_web.Tips ADD es_prioritario BIT NOT NULL CONSTRAINT DF_Tips_es_prioritario DEFAULT 0;

    IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('pagina_web.Tips') AND name = 'url_accion')
        ALTER TABLE pagina_web.Tips ADD url_accion VARCHAR(500) NULL;

    IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('pagina_web.Tips') AND name = 'texto_accion')
        ALTER TABLE pagina_web.Tips ADD texto_accion VARCHAR(100) NULL;
END
GO

-- 3. Tabla relacional Grupos <-> Roles (N a M)
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE object_id = OBJECT_ID('pagina_web.TipGroupRoles'))
BEGIN
    CREATE TABLE pagina_web.TipGroupRoles (
        tip_group_id INT NOT NULL,
        role_id INT NOT NULL,
        created_at DATETIME NOT NULL CONSTRAINT DF_TipGroupRoles_created DEFAULT GETDATE(),
        CONSTRAINT PK_TipGroupRoles PRIMARY KEY CLUSTERED (tip_group_id, role_id),
        CONSTRAINT FK_TipGroupRoles_TipGroups FOREIGN KEY (tip_group_id)
            REFERENCES pagina_web.TipGroups(id) ON DELETE CASCADE,
        CONSTRAINT FK_TipGroupRoles_Roles FOREIGN KEY (role_id)
            REFERENCES pagina_web.Roles(id) ON DELETE CASCADE
    );

    CREATE NONCLUSTERED INDEX IX_TipGroupRoles_role_id ON pagina_web.TipGroupRoles (role_id);
END
GO

-- 4. Vista de Grupos de Tips Efectivos por Rol (con herencia recursiva hacia los hijos)
-- Sube por parent_role_id: si el rol R o alguno de sus ancestros tiene asignado el grupo G,
-- entonces R tiene acceso a G.
IF OBJECT_ID('pagina_web.RoleEffectiveTipGroups', 'V') IS NULL
    EXEC('CREATE VIEW pagina_web.RoleEffectiveTipGroups AS SELECT 1 AS dummy');
GO

ALTER VIEW pagina_web.RoleEffectiveTipGroups AS
WITH ancestros AS (
    SELECT r.id AS role_id, r.id AS ancestro_id, r.parent_role_id
    FROM pagina_web.Roles r
    UNION ALL
    SELECT a.role_id, p.id, p.parent_role_id
    FROM ancestros a
    JOIN pagina_web.Roles p ON p.id = a.parent_role_id
)
SELECT DISTINCT a.role_id, tgr.tip_group_id
FROM ancestros a
JOIN pagina_web.TipGroupRoles tgr ON tgr.role_id = a.ancestro_id;
GO

-- 5. Permiso de administración de tips
IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'tips:manage')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('tips:manage', 'Crear, editar y asignar grupos de tips del día');
END
GO

-- 6. Tabla de Feedback / Acuse ("Me sirvió") por tip y usuario
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE object_id = OBJECT_ID('pagina_web.TipFeedback'))
BEGIN
    CREATE TABLE pagina_web.TipFeedback (
        id INT IDENTITY(1,1) NOT NULL,
        tip_id INT NOT NULL,
        documento INT NOT NULL,
        created_at DATETIME NOT NULL CONSTRAINT DF_TipFeedback_created DEFAULT GETDATE(),
        CONSTRAINT PK_TipFeedback PRIMARY KEY CLUSTERED (id),
        CONSTRAINT UQ_TipFeedback_tip_doc UNIQUE NONCLUSTERED (tip_id, documento),
        CONSTRAINT FK_TipFeedback_Tips FOREIGN KEY (tip_id)
            REFERENCES pagina_web.Tips(id) ON DELETE CASCADE
    );

    CREATE NONCLUSTERED INDEX IX_TipFeedback_tip_id ON pagina_web.TipFeedback (tip_id);
END
GO

-- 7. Verificación de permisos y objetos creados
SELECT id, code, description FROM pagina_web.Permissions WHERE code = 'tips:manage';
GO

