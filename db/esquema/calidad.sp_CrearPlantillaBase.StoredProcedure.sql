-- StoredProcedure [calidad].[sp_CrearPlantillaBase]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[sp_CrearPlantillaBase]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [calidad].[sp_CrearPlantillaBase] AS' 
END
GO

/* -- 4) sp_CrearPlantillaBase: suma @NivelRazonamiento ---------------------- */
/* Cuerpo relevado de la BD el 2026-08-19 + el parámetro nuevo. NULL = la
   plantilla nace sin nivel y audita con el default del código. */
ALTER   PROCEDURE [calidad].[sp_CrearPlantillaBase]
    @NombrePlantilla NVARCHAR(255),
    @SystemPrompt NVARCHAR(MAX),
    @CampanaID INT,
    @Recordatorio NVARCHAR(MAX) = NULL,
    @Descripcion NVARCHAR(MAX),
    @ModeloIA NVARCHAR(80) = NULL,
    @NivelRazonamiento NVARCHAR(10) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    -- Validar que la CampanaID exista
    IF NOT EXISTS (SELECT 1 FROM calidad.Campanas WHERE CampanaID = @CampanaID)
    BEGIN
        RAISERROR ('La CampanaID %d no existe. No se puede crear la plantilla.', 16, 1, @CampanaID);
        RETURN;
    END

    BEGIN TRANSACTION;

    DECLARE @NuevaPlantillaID INT;

    BEGIN TRY
        -- 1. Insertamos la nueva plantilla con su CampanaID
        INSERT INTO calidad.Plantillas
            (Nombre, SystemPrompt, Recordatorio, CampanaID, descripcion, ModeloIA, NivelRazonamiento)
        VALUES
            (@NombrePlantilla, @SystemPrompt, @Recordatorio, @CampanaID, @Descripcion, @ModeloIA, @NivelRazonamiento);

        -- 2. Obtenemos el ID de la plantilla creada.
        SET @NuevaPlantillaID = SCOPE_IDENTITY();

        COMMIT TRANSACTION;

        -- 3. Devolvemos el ID de la nueva plantilla.
        SELECT @NuevaPlantillaID AS NuevaPlantillaID;

    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0
            ROLLBACK TRANSACTION;
        THROW;
    END CATCH
END
GO
