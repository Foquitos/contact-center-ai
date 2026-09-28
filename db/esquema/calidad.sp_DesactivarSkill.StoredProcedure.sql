-- StoredProcedure [calidad].[sp_DesactivarSkill]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[sp_DesactivarSkill]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [calidad].[sp_DesactivarSkill] AS' 
END
GO
ALTER PROCEDURE [calidad].[sp_DesactivarSkill] @TargetCampanaID INT, @NombreSkill NVARCHAR(255) AS BEGIN SET NOCOUNT ON;
    DECLARE @TargetEmpresaID INT, @SkillID INT;
    SELECT @TargetEmpresaID = EmpresaID FROM calidad.Campanas WHERE CampanaID = @TargetCampanaID;
    IF @TargetEmpresaID IS NULL BEGIN RAISERROR('CampanaID %d no existe.', 16, 1, @TargetCampanaID); RETURN; END;
    SELECT @SkillID = s.SkillID FROM calidad.Skills s JOIN calidad.Campanas c ON s.CampanaID = c.CampanaID WHERE c.EmpresaID = @TargetEmpresaID AND s.Nombre = @NombreSkill AND s.IsActive = 1;
    IF @SkillID IS NOT NULL BEGIN UPDATE calidad.Skills SET IsActive = 0 WHERE SkillID = @SkillID; PRINT 'Skill ' + @NombreSkill + ' desactivado.'; END;
    ELSE BEGIN PRINT 'Skill ' + @NombreSkill + ' no encontrado o ya inactivo.'; END;
END;
GO
