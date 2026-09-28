-- StoredProcedure [calidad].[sp_AgregarAtributosAPlantilla]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[sp_AgregarAtributosAPlantilla]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [calidad].[sp_AgregarAtributosAPlantilla] AS' 
END
GO
-- Migración 003 — Stored procedures para Ponderación dinámica + Errores Críticos (EC)
-- ============================================================================
-- Requiere haber aplicado 002 (columnas Ponderacion / PuntajeFinal /
-- EsErrorCritico / PonderacionAplicada).
--
-- Usa CREATE OR ALTER (SQL Server 2016 SP1+). Cada SP va en su propio batch (GO).
-- Cambios respecto a las versiones actuales marcados con  -- <<< EC/PONDERACION
-- ============================================================================


-- 1) sp_AgregarAtributosAPlantilla : persistir Ponderacion desde el JSON --------
ALTER   PROCEDURE [calidad].[sp_AgregarAtributosAPlantilla]
    @PlantillaID INT,
    @AtributosJSON NVARCHAR(MAX)
AS
BEGIN
    SET NOCOUNT ON;

    IF NOT EXISTS (SELECT 1 FROM calidad.Plantillas WHERE PlantillaID = @PlantillaID)
    BEGIN
        RAISERROR ('La PlantillaID %d no existe. No se agregaron atributos.', 16, 1, @PlantillaID);
        RETURN;
    END

    BEGIN TRANSACTION;
    BEGIN TRY
        INSERT INTO calidad.Atributos
            (PlantillaID, NombreAtributo, PromptAdyacente, TipoDato, Restricciones, Orden, Ponderacion)  -- <<< EC/PONDERACION
        SELECT
            @PlantillaID,
            j.NombreAtributo,
            j.PromptAdyacente,
            j.TipoDato,
            j.Restricciones,
            j.Orden,
            ISNULL(j.Ponderacion, 0)  -- <<< EC/PONDERACION
        FROM
            OPENJSON(@AtributosJSON)
            WITH (
                NombreAtributo NVARCHAR(255) '$.nombre',
                PromptAdyacente NVARCHAR(MAX) '$.prompt',
                TipoDato NVARCHAR(50) '$.tipo',
                Restricciones NVARCHAR(MAX) '$.restricciones' AS JSON,
                Orden INT '$.orden',
                Ponderacion DECIMAL(6,2) '$.ponderacion'  -- <<< EC/PONDERACION
            ) AS j;

        COMMIT TRANSACTION;
        PRINT 'Atributos agregados correctamente a la PlantillaID ' + CAST(@PlantillaID AS VARCHAR);
    END TRY
    BEGIN CATCH
        ROLLBACK TRANSACTION;
        THROW;
    END CATCH
END
GO
