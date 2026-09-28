-- View [calidad].[v_SkillsPorEmpresa]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[calidad].[v_SkillsPorEmpresa]'))
EXEC dbo.sp_executesql @statement = N'-- Recrear la vista incluyendo IsActive
CREATE VIEW [calidad].[v_SkillsPorEmpresa]
WITH SCHEMABINDING
AS
SELECT
    s.Nombre,
    c.EmpresaID,
    s.IsActive -- Incluir IsActive
FROM
    calidad.Skills AS s
JOIN
    calidad.Campanas AS c ON s.CampanaID = c.CampanaID;
' 
GO
