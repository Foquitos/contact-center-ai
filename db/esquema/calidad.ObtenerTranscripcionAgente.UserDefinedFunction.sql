-- UserDefinedFunction [calidad].[ObtenerTranscripcionAgente]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[ObtenerTranscripcionAgente]') AND type in (N'FN', N'IF', N'TF', N'FS', N'FT'))
BEGIN
execute dbo.sp_executesql @statement = N'CREATE   FUNCTION [calidad].[ObtenerTranscripcionAgente] (@jsonSegments NVARCHAR(MAX))
RETURNS NVARCHAR(MAX)
AS
BEGIN
    DECLARE @resultado NVARCHAR(MAX);

    -- Esta consulta une todas las palabras de todos los segmentos que cumplen una condición.
    SELECT @resultado = STRING_AGG(CAST(palabra.value AS NVARCHAR(MAX)), '' '')
        -- Se asegura de que las palabras se unan en el orden correcto de la conversación.
        WITHIN GROUP (ORDER BY CAST(segmento.[key] AS INT) ASC, CAST(palabra.[key] AS INT) ASC)
    FROM 
        -- Abre el array principal de segmentos.
        OPENJSON(@jsonSegments) AS segmento
        -- Por cada segmento, abre el array de palabras que contiene.
        CROSS APPLY OPENJSON(segmento.value, ''$.text'') AS palabra
    WHERE 
        -- ¡Esta es la clave! Filtra para incluir solo los segmentos del Agente.
        JSON_VALUE(segmento.value, ''$.speakerLabel'') = ''Agente'';

    RETURN @resultado;
END;
' 
END
GO
