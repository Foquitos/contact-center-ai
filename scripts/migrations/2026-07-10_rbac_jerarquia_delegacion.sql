/* ============================================================================
   Feature — RBAC: jerarquía de roles, delegación de gestión y auditoría
   Fecha: 2026-07-10
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   1) Roles.parent_role_id (self-FK nullable): jerarquía padre/hijo. El HIJO
      hereda los permisos del padre (padre = rol base; permisos efectivos =
      propios + todos los ancestros). Los roles is_super_admin quedan fuera de
      la jerarquía (lo valida el backend).
   2) Vista pagina_web.RoleEffectivePermissions (role_id, permission_id):
      resuelve la herencia con CTE recursiva. Mientras ningún rol tenga padre
      devuelve exactamente lo mismo que RolePermissions (cambio inocuo).
   3) Índice único en UserRoles (nomina_id, role_id): habilita multi-rol sin
      filas duplicadas (hoy no hay duplicados: verificado 2026-07-10).
   4) Permiso nuevo roles:manage — habilita la gestión delegada de roles
      (crear/editar/borrar roles cuyos permisos efectivos sean subconjunto de
      los del delegado). NO se asigna a ningún rol por default.
   5) Tabla pagina_web.RbacAuditLog: log de cambios RBAC (quién creó/modificó/
      borró roles y asignaciones de usuarios).

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme ANTES de deployar el código nuevo (el código
   viejo no usa la vista ni las columnas nuevas). Idempotente.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

/* ================ 1) Roles.parent_role_id (jerarquía) ====================== */

IF COL_LENGTH('pagina_web.Roles', 'parent_role_id') IS NULL
BEGIN
    ALTER TABLE pagina_web.Roles ADD parent_role_id INT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Roles_Parent')
BEGIN
    ALTER TABLE pagina_web.Roles ADD CONSTRAINT FK_Roles_Parent
        FOREIGN KEY (parent_role_id) REFERENCES pagina_web.Roles(id);
END
GO

/* ================ 2) Vista de permisos efectivos =========================== */
/* Sube por parent_role_id acumulando los permisos de todos los ancestros.
   El backend previene ciclos al editar; si igual apareciera uno, la consulta
   cortaría en el límite de recursión default (100) con error, no en loop. */

CREATE OR ALTER VIEW pagina_web.RoleEffectivePermissions AS
WITH ancestros AS (
    SELECT r.id AS role_id, r.id AS ancestro_id, r.parent_role_id
    FROM pagina_web.Roles r
    UNION ALL
    SELECT a.role_id, p.id, p.parent_role_id
    FROM ancestros a
    JOIN pagina_web.Roles p ON p.id = a.parent_role_id
)
SELECT DISTINCT a.role_id, rp.permission_id
FROM ancestros a
JOIN pagina_web.RolePermissions rp ON rp.role_id = a.ancestro_id;
GO

/* ================ 3) Índice único UserRoles ================================ */

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'UQ_UserRoles_nomina_role'
      AND object_id = OBJECT_ID('pagina_web.UserRoles')
)
BEGIN
    CREATE UNIQUE INDEX UQ_UserRoles_nomina_role
        ON pagina_web.UserRoles (nomina_id, role_id);
END
GO

/* ================ 4) Permiso roles:manage ================================== */

IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'roles:manage')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('roles:manage',
            'Gestión delegada de roles: crear/editar/borrar roles con permisos que el usuario ya posee');
END
GO

/* ================ 5) Auditoría RBAC ======================================== */

IF OBJECT_ID('pagina_web.RbacAuditLog', 'U') IS NULL
BEGIN
    CREATE TABLE pagina_web.RbacAuditLog (
        id              INT IDENTITY(1,1) CONSTRAINT PK_RbacAuditLog PRIMARY KEY,
        actor_documento INT           NOT NULL,
        action          VARCHAR(40)   NOT NULL,  -- role_create | role_update | role_delete | user_roles_set | bulk_role_assign | user_delete
        entity_type     VARCHAR(20)   NOT NULL,  -- role | user
        entity_id       INT           NULL,      -- role_id o documento según entity_type
        detail          NVARCHAR(MAX) NULL,      -- JSON con el antes/después o los datos del cambio
        created_at      DATETIME2     NOT NULL CONSTRAINT DF_RbacAuditLog_ca DEFAULT SYSDATETIME()
    );
    CREATE INDEX IX_RbacAuditLog_created ON pagina_web.RbacAuditLog (created_at);
END
GO

/* ================ REPORTE ================================================== */

SELECT 'roles' AS objeto, COUNT(*) AS filas FROM pagina_web.Roles
UNION ALL
SELECT 'permisos_directos', COUNT(*) FROM pagina_web.RolePermissions
UNION ALL
SELECT 'permisos_efectivos (debe ser igual a directos hasta usar padres)', COUNT(*) FROM pagina_web.RoleEffectivePermissions
UNION ALL
SELECT 'permiso roles:manage', COUNT(*) FROM pagina_web.Permissions WHERE code = 'roles:manage';
GO
