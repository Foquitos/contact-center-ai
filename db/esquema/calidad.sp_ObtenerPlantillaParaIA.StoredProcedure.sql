-- StoredProcedure [calidad].[sp_ObtenerPlantillaParaIA]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[sp_ObtenerPlantillaParaIA]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [calidad].[sp_ObtenerPlantillaParaIA] AS' 
END
GO

/* -- 3) sp_ObtenerPlantillaParaIA: suma NivelRazonamiento ------------------- */
ALTER   PROCEDURE [calidad].[sp_ObtenerPlantillaParaIA]
    @TargetPlantillaID INT
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        p.SystemPrompt,
        p.ModeloIA,
        -- <<< NUEVO: cuánto piensa el modelo antes de responder (LOW/MEDIUM/HIGH).
        -- NULL = el backend aplica su default (AuditorIA/razonamiento.py).
        p.NivelRazonamiento,
        STRING_AGG(
            CAST(a.NombreAtributo AS NVARCHAR(MAX)) + N': ' + a.PromptAdyacente,
            CHAR(13) + CHAR(10) + CHAR(13) + CHAR(10)
        ) WITHIN GROUP (ORDER BY a.Orden)
        +
        CASE
            WHEN p.Recordatorio IS NOT NULL AND p.Recordatorio <> ''
            THEN CHAR(13) + CHAR(10) + CHAR(13) + CHAR(10) + N'Recuerda: ' + p.Recordatorio
            ELSE ''
        END AS Prompt,
        (
            SELECT
                a_sub.NombreAtributo AS name,
                a_sub.TipoDato AS type,
                JSON_QUERY(a_sub.Restricciones) AS constraints,
                a_sub.AtributoID AS id,
                -- OPCIONALES: 1 = la IA puede omitir el campo (sale del `required`).
                CAST(ISNULL(a_sub.EsOpcional, 0) AS BIT) AS [optional]
            FROM calidad.Atributos a_sub
            WHERE a_sub.PlantillaID = p.PlantillaID and a_sub.IsActive = 1
            ORDER BY a_sub.Orden
            FOR JSON PATH
        ) AS ResponseSchema
    FROM calidad.Plantillas p
    JOIN calidad.Atributos a ON p.PlantillaID = a.PlantillaID
    WHERE p.PlantillaID = @TargetPlantillaID
    GROUP BY
        p.PlantillaID,
        p.SystemPrompt,
        p.ModeloIA,
        p.NivelRazonamiento,
        p.Recordatorio;

END
GO
