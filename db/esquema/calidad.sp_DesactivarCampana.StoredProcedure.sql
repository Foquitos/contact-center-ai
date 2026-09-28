-- StoredProcedure [calidad].[sp_DesactivarCampana]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[sp_DesactivarCampana]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [calidad].[sp_DesactivarCampana] AS' 
END
GO

/* ================ 4) Enganche en el ciclo de vida de campañas ============== */
/* Idéntico al original + la sincronización del permiso al final: si esta era
   la última campaña activa de la empresa, el permiso deja de tener sentido. */

ALTER   PROCEDURE [calidad].[sp_DesactivarCampana]
    @TargetCampanaID INT
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE calidad.Campanas   SET IsActive = 0 WHERE CampanaID = @TargetCampanaID AND IsActive = 1;
    UPDATE calidad.Plantillas SET IsActive = 0 WHERE CampanaID = @TargetCampanaID AND IsActive = 1;
    UPDATE calidad.Skills     SET IsActive = 0 WHERE CampanaID = @TargetCampanaID AND IsActive = 1;

    EXEC calidad.sp_SincronizarPermisosEmpresas;
END
GO
