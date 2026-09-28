-- View [pagina_web].[RoleEffectiveTipGroups]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[pagina_web].[RoleEffectiveTipGroups]'))
EXEC dbo.sp_executesql @statement = N'
CREATE VIEW [pagina_web].[RoleEffectiveTipGroups] AS
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
' 
GO
