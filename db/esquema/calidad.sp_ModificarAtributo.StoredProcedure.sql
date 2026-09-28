-- StoredProcedure [calidad].[sp_ModificarAtributo]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[sp_ModificarAtributo]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [calidad].[sp_ModificarAtributo] AS' 
END
GO


-- 2) sp_ModificarAtributo : nuevo @Ponderacion + no borrar Restricciones de
--    critical_audit + persistir Ponderacion en todas las ramas --------------------
ALTER   PROCEDURE [calidad].[sp_ModificarAtributo]
    @AtributoID INT,
    @NombreAtributo NVARCHAR(255) = NULL,
    @PromptAdyacente NVARCHAR(MAX) = NULL,
    @TipoDato NVARCHAR(50) = NULL,
    @Restricciones NVARCHAR(MAX) = NULL,
    @Orden INT = NULL,
    @DarAviso BIT = NULL,
    @FrasesAviso NVARCHAR(MAX) = NULL,
    @Ponderacion DECIMAL(6,2) = NULL  -- <<< EC/PONDERACION
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @CurrentTipoDato NVARCHAR(50);
    SELECT @CurrentTipoDato = TipoDato FROM calidad.Atributos WHERE AtributoID = @AtributoID;

    IF @CurrentTipoDato IS NULL
    BEGIN
        RAISERROR ('El AtributoID %d no existe. No se realizó ninguna modificación.', 16, 1, @AtributoID);
        RETURN;
    END

    DECLARE @FinalTipoDato NVARCHAR(50) = COALESCE(@TipoDato, @CurrentTipoDato);

    DECLARE @FinalRestricciones NVARCHAR(MAX);
    -- <<< EC/PONDERACION: 'critical_audit' también conserva Restricciones (OK/NO OK/EC).
    IF @FinalTipoDato IN ('enum', 'array_enum', 'critical_audit')
        SET @FinalRestricciones = COALESCE(@Restricciones, (SELECT Restricciones FROM calidad.Atributos WHERE AtributoID = @AtributoID));
    ELSE
        SET @FinalRestricciones = NULL;

    -- Sin cambio de orden: update simple
    IF @Orden IS NULL
    BEGIN
        BEGIN TRY
            UPDATE calidad.Atributos
            SET
                NombreAtributo = COALESCE(@NombreAtributo, NombreAtributo),
                PromptAdyacente = COALESCE(@PromptAdyacente, PromptAdyacente),
                TipoDato = COALESCE(@TipoDato, TipoDato),
                Restricciones = @FinalRestricciones,
                DarAviso = COALESCE(@DarAviso, DarAviso),
                FrasesAviso = COALESCE(@FrasesAviso, FrasesAviso),
                Ponderacion = COALESCE(@Ponderacion, Ponderacion)  -- <<< EC/PONDERACION
            WHERE AtributoID = @AtributoID;
        END TRY
        BEGIN CATCH
            THROW;
        END CATCH
        RETURN;
    END

    -- Con cambio de orden: shuffle atómico
    BEGIN TRANSACTION;

    DECLARE @TargetPlantillaID INT;
    DECLARE @OldOrden INT;
    DECLARE @NewOrden INT = @Orden;

    BEGIN TRY
        SELECT
            @TargetPlantillaID = PlantillaID,
            @OldOrden = Orden
        FROM calidad.Atributos
        WHERE AtributoID = @AtributoID;

        -- Mismo orden: update sin shuffle
        IF @OldOrden = @NewOrden
        BEGIN
            UPDATE calidad.Atributos
            SET
                NombreAtributo = COALESCE(@NombreAtributo, NombreAtributo),
                PromptAdyacente = COALESCE(@PromptAdyacente, PromptAdyacente),
                TipoDato = COALESCE(@TipoDato, TipoDato),
                Restricciones = @FinalRestricciones,
                DarAviso = COALESCE(@DarAviso, DarAviso),
                FrasesAviso = COALESCE(@FrasesAviso, FrasesAviso),
                Ponderacion = COALESCE(@Ponderacion, Ponderacion)  -- <<< EC/PONDERACION
            WHERE AtributoID = @AtributoID;

            COMMIT TRANSACTION;
            RETURN;
        END

        UPDATE calidad.Atributos SET Orden = -1 WHERE AtributoID = @AtributoID;

        IF @NewOrden < @OldOrden
        BEGIN
            UPDATE calidad.Atributos
            SET Orden = Orden + 1
            WHERE PlantillaID = @TargetPlantillaID
              AND Orden >= @NewOrden
              AND Orden < @OldOrden
              AND IsActive = 1;
        END
        ELSE
        BEGIN
            UPDATE calidad.Atributos
            SET Orden = Orden - 1
            WHERE PlantillaID = @TargetPlantillaID
              AND Orden > @OldOrden
              AND Orden <= @NewOrden
              AND IsActive = 1;
        END

        UPDATE calidad.Atributos
        SET
            Orden = @NewOrden,
            NombreAtributo = COALESCE(@NombreAtributo, NombreAtributo),
            PromptAdyacente = COALESCE(@PromptAdyacente, PromptAdyacente),
            TipoDato = COALESCE(@TipoDato, TipoDato),
            Restricciones = @FinalRestricciones,
            DarAviso = COALESCE(@DarAviso, DarAviso),
            FrasesAviso = COALESCE(@FrasesAviso, FrasesAviso),
            Ponderacion = COALESCE(@Ponderacion, Ponderacion)  -- <<< EC/PONDERACION
        WHERE AtributoID = @AtributoID;

        COMMIT TRANSACTION;
    END TRY
    BEGIN CATCH
        IF @@TRANCOUNT > 0
            ROLLBACK TRANSACTION;
        THROW;
    END CATCH
END
GO
