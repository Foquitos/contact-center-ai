-- StoredProcedure [calidad].[sp_ListarSkillsPorCampana]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[sp_ListarSkillsPorCampana]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [calidad].[sp_ListarSkillsPorCampana] AS' 
END
GO
-- Creamos el SP para listar los nombres de los skills de una campaña.
ALTER PROCEDURE [calidad].[sp_ListarSkillsPorCampana]
    -- Parámetro obligatorio: el ID de la campaña
    @TargetCampanaID INT
AS
BEGIN
    SET NOCOUNT ON;

    -- 1. Validamos que la CampanaID exista.
    IF NOT EXISTS (SELECT 1 FROM calidad.Campanas WHERE CampanaID = @TargetCampanaID)
    BEGIN
        RAISERROR ('La CampanaID %d no existe.', 16, 1, @TargetCampanaID);
        RETURN;
    END

    -- 2. Seleccionamos los nombres de los skills para esa campaña.
    SELECT
        Nombre AS NombreSkill -- Devolvemos solo la columna 'Nombre'
    FROM
        calidad.Skills
    WHERE
        CampanaID = @TargetCampanaID
        and IsActive = 1
    ORDER BY
        Nombre; -- Opcional: ordenar alfabéticamente

END
GO
