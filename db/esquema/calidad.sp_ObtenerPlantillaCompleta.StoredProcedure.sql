-- StoredProcedure [calidad].[sp_ObtenerPlantillaCompleta]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[sp_ObtenerPlantillaCompleta]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [calidad].[sp_ObtenerPlantillaCompleta] AS' 
END
GO

/* -- 6) sp_ObtenerPlantillaCompleta: suma nivel_razonamiento al JSON del editor */
/* Es el que alimenta el editor de plantillas del frontend. Cuerpo relevado de la
   BD el 2026-08-19 + el campo nuevo (incluye el `es_opcional` de 2026-08-05b y la
   `ponderacion` de EC). */
ALTER   PROCEDURE [calidad].[sp_ObtenerPlantillaCompleta]
    @TargetPlantillaID INT
AS
BEGIN
    SET NOCOUNT ON;

    IF NOT EXISTS (SELECT 1 FROM calidad.Plantillas WHERE PlantillaID = @TargetPlantillaID AND IsActive = 1)
    BEGIN
        RAISERROR ('La PlantillaID %d no existe o está inactiva.', 16, 1, @TargetPlantillaID);
        RETURN;
    END

    DECLARE @JsonOutput NVARCHAR(MAX);

    SET @JsonOutput = (
        SELECT
            p.PlantillaID AS 'id',
            p.Nombre AS 'nombre',
            p.Descripcion AS 'descripcion',
            p.CampanaID AS 'campana_id',
            REPLACE(p.SystemPrompt, '\', '\\') AS 'system',
            REPLACE(p.Recordatorio, '\', '\\') AS 'recordatorio',
            p.ModeloIA AS 'modelo_ia',
            -- <<< NUEVO: nivel de razonamiento, lo consume el editor de plantillas.
            -- NULL viaja como campo ausente y el editor lo muestra como el default.
            p.NivelRazonamiento AS 'nivel_razonamiento',
            (
                SELECT
                    a.AtributoID AS 'id',
                    REPLACE(a.NombreAtributo, '\', '\\') AS 'nombre',
                    REPLACE(a.PromptAdyacente, '\', '\\') AS 'prompt',
                    a.TipoDato AS 'tipo',
                    JSON_QUERY(a.Restricciones) AS 'restricciones',
                    a.Orden AS 'orden',
                    a.DarAviso,
                    a.FrasesAviso,
                    a.Ponderacion AS 'ponderacion',  -- <<< EC/PONDERACION
                    -- <<< OPCIONALES: lo consume el editor de plantillas (frontend).
                    CAST(ISNULL(a.EsOpcional, 0) AS BIT) AS 'es_opcional'
                FROM calidad.Atributos a
                WHERE a.PlantillaID = p.PlantillaID and a.IsActive = 1
                ORDER BY a.Orden
                FOR JSON PATH
            ) AS 'atributos'
        FROM
            calidad.Plantillas p
        WHERE
            p.PlantillaID = @TargetPlantillaID
        FOR JSON PATH, WITHOUT_ARRAY_WRAPPER
    );

    SELECT @JsonOutput AS PlantillaJson;
END
GO
