-- StoredProcedure [calidad].[sp_ListarPlantillasPorCampana]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[sp_ListarPlantillasPorCampana]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [calidad].[sp_ListarPlantillasPorCampana] AS' 
END
GO
-- Ejemplo: sp_ListarPlantillasPorCampana
ALTER PROCEDURE [calidad].[sp_ListarPlantillasPorCampana]
    @TargetCampanaID INT
AS
BEGIN
    SET NOCOUNT ON;
    -- Añadir filtro por IsActive
    SELECT p.PlantillaID, p.Nombre AS NombrePlantilla
    FROM calidad.Plantillas p
    WHERE p.CampanaID = @TargetCampanaID AND p.IsActive = 1; -- <-- Añadido AND p.IsActive = 1
END
GO
