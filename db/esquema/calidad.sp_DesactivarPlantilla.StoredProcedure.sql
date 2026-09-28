-- StoredProcedure [calidad].[sp_DesactivarPlantilla]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[sp_DesactivarPlantilla]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [calidad].[sp_DesactivarPlantilla] AS' 
END
GO
ALTER PROCEDURE [calidad].[sp_DesactivarPlantilla] @TargetPlantillaID INT AS BEGIN SET NOCOUNT ON;
    UPDATE calidad.Plantillas SET IsActive = 0 WHERE PlantillaID = @TargetPlantillaID AND IsActive = 1;
    -- Opcional: Desactivar Atributos en cascada?
    UPDATE calidad.Atributos SET IsActive = 0 WHERE PlantillaID = @TargetPlantillaID AND IsActive = 1;
    PRINT 'Plantilla ' + CAST(@TargetPlantillaID AS VARCHAR) + ' desactivada.';
END;
GO
