-- StoredProcedure [calidad].[sp_DesactivarEmpresa]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[sp_DesactivarEmpresa]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [calidad].[sp_DesactivarEmpresa] AS' 
END
GO

/* ================ 3) Alta/baja de empresas ================================= */

ALTER   PROCEDURE [calidad].[sp_DesactivarEmpresa]
    @TargetEmpresaID INT
AS
BEGIN
    SET NOCOUNT ON;

    IF NOT EXISTS (SELECT 1 FROM calidad.Empresas WHERE EmpresaID = @TargetEmpresaID)
    BEGIN
        RAISERROR ('El EmpresaID %d no existe en calidad.Empresas.', 16, 1, @TargetEmpresaID);
        RETURN;
    END

    BEGIN TRANSACTION;
    BEGIN TRY
        UPDATE calidad.Empresas
           SET IsActive = 0
         WHERE EmpresaID = @TargetEmpresaID AND IsActive = 1;

        /* Cascada: campañas -> plantillas -> atributos, y skills. */
        UPDATE a
           SET a.IsActive = 0
          FROM calidad.Atributos a
          JOIN calidad.Plantillas pl ON pl.PlantillaID = a.PlantillaID
          JOIN calidad.Campanas   c  ON c.CampanaID    = pl.CampanaID
         WHERE c.EmpresaID = @TargetEmpresaID AND a.IsActive = 1;

        UPDATE pl
           SET pl.IsActive = 0
          FROM calidad.Plantillas pl
          JOIN calidad.Campanas c ON c.CampanaID = pl.CampanaID
         WHERE c.EmpresaID = @TargetEmpresaID AND pl.IsActive = 1;

        UPDATE s
           SET s.IsActive = 0
          FROM calidad.Skills s
          JOIN calidad.Campanas c ON c.CampanaID = s.CampanaID
         WHERE c.EmpresaID = @TargetEmpresaID AND s.IsActive = 1;

        UPDATE calidad.Campanas
           SET IsActive = 0
         WHERE EmpresaID = @TargetEmpresaID AND IsActive = 1;

        EXEC calidad.sp_SincronizarPermisosEmpresas;

        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH
END
GO
