-- StoredProcedure [calidad].[sp_CrearCampana]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[sp_CrearCampana]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [calidad].[sp_CrearCampana] AS' 
END
GO

/* Crear una campaña puede revivir el permiso de una empresa que se había
   quedado sin campañas activas. */
ALTER   PROCEDURE [calidad].[sp_CrearCampana]
    @NombreCampana NVARCHAR(255),
    @EmpresaID INT,
    @PlataformaID INT
AS
BEGIN
    SET NOCOUNT ON;

    -- 1. Validaciones Previas
    IF NOT EXISTS (SELECT 1 FROM calidad.Empresas WHERE EmpresaID = @EmpresaID)
    BEGIN
        RAISERROR ('El EmpresaID %d no existe en la tabla calidad.Empresas.', 16, 1, @EmpresaID);
        RETURN;
    END

    IF NOT EXISTS (SELECT 1 FROM calidad.Plataformas WHERE PlataformaID = @PlataformaID)
    BEGIN
        RAISERROR ('El PlataformaID %d no existe en la tabla calidad.Plataformas.', 16, 1, @PlataformaID);
        RETURN;
    END

    IF EXISTS (SELECT 1 FROM calidad.Campanas WHERE Nombre = @NombreCampana AND EmpresaID = @EmpresaID)
    BEGIN
        RAISERROR ('Ya existe una campaña con el nombre "%s" para la EmpresaID %d.', 16, 1, @NombreCampana, @EmpresaID);
        RETURN;
    END

    BEGIN TRANSACTION;

    DECLARE @NuevaCampanaID INT;

    BEGIN TRY
        INSERT INTO calidad.Campanas (Nombre, EmpresaID, PlataformaID)
        VALUES (@NombreCampana, @EmpresaID, @PlataformaID);

        SET @NuevaCampanaID = SCOPE_IDENTITY();

        EXEC calidad.sp_SincronizarPermisosEmpresas;

        COMMIT TRANSACTION;

        SELECT @NuevaCampanaID AS NuevaCampanaID;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0
            ROLLBACK TRANSACTION;
        THROW;
    END CATCH
END
GO
