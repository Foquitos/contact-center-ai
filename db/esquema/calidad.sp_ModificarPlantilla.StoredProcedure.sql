-- StoredProcedure [calidad].[sp_ModificarPlantilla]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[sp_ModificarPlantilla]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [calidad].[sp_ModificarPlantilla] AS' 
END
GO

/* -- 5) sp_ModificarPlantilla: suma @NivelRazonamiento ---------------------- */
/* Mismo criterio COALESCE que el resto de los campos: NULL = "no lo toques".
   Consecuencia: una plantilla que ya tiene nivel no se puede volver a dejar en
   NULL desde acá; para volver al default hay que elegir el nivel default de
   forma explícita en el editor. Es lo mismo que ya pasaba con @ModeloIA. */
ALTER   PROCEDURE [calidad].[sp_ModificarPlantilla]
    @PlantillaID INT,
    @NombrePlantilla NVARCHAR(255) = NULL,
    @Descripcion NVARCHAR(1000) = NULL,
    @SystemPrompt NVARCHAR(MAX) = NULL,
    @Recordatorio NVARCHAR(MAX) = NULL,
    @ModeloIA NVARCHAR(80) = NULL,
    @NivelRazonamiento NVARCHAR(10) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    -- Verificamos que la plantilla exista.
    IF NOT EXISTS (SELECT 1 FROM calidad.Plantillas WHERE PlantillaID = @PlantillaID)
    BEGIN
        RAISERROR ('La PlantillaID %d no existe. No se realizó ninguna modificación.', 16, 1, @PlantillaID);
        RETURN;
    END

    -- Usamos una transacción para la integridad de la operación.
    BEGIN TRANSACTION;

    BEGIN TRY
        -- Actualizamos la fila en la tabla de Plantillas.
        UPDATE calidad.Plantillas
        SET
            -- COALESCE elige el nuevo valor si no es NULL, o mantiene el valor antiguo.
            Nombre = COALESCE(@NombrePlantilla, Nombre),
            Descripcion = COALESCE(@Descripcion, Descripcion),
            SystemPrompt = COALESCE(@SystemPrompt, SystemPrompt),
            Recordatorio = COALESCE(@Recordatorio, Recordatorio),
            ModeloIA = COALESCE(@ModeloIA, ModeloIA),
            NivelRazonamiento = COALESCE(@NivelRazonamiento, NivelRazonamiento)
        WHERE
            PlantillaID = @PlantillaID;

        COMMIT TRANSACTION;
        PRINT 'Plantilla con ID ' + CAST(@PlantillaID AS VARCHAR) + ' modificada correctamente.';

    END TRY
    BEGIN CATCH
        ROLLBACK TRANSACTION;
        THROW; -- Re-lanza el error para que la aplicación lo reciba.
    END CATCH
END
GO
