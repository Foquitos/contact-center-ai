-- View [pagina_web].[RoleEffectivePermissions]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[pagina_web].[RoleEffectivePermissions]'))
EXEC dbo.sp_executesql @statement = N'
/* ================ 2) Vista de permisos efectivos =========================== */
/* Sube por parent_role_id acumulando los permisos de todos los ancestros.
   El backend previene ciclos al editar; si igual apareciera uno, la consulta
   cortaría en el límite de recursión default (100) con error, no en loop. */

CREATE   VIEW [pagina_web].[RoleEffectivePermissions] AS
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
' 
GO
