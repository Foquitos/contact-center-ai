-- UserDefinedFunction [calidad].[FormatearTranscripcion]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[FormatearTranscripcion]') AND type in (N'FN', N'IF', N'TF', N'FS', N'FT'))
BEGIN
execute dbo.sp_executesql @statement = N'CREATE   FUNCTION [calidad].[FormatearTranscripcion] (@jsonSegments NVARCHAR(MAX))
RETURNS NVARCHAR(MAX)
AS
BEGIN
    DECLARE @resultado NVARCHAR(MAX);

    -- El truco es usar una subconsulta (tabla derivada ''Lineas'') para que 
    -- el STRING_AGG interno se resuelva ANTES que el externo.
    SELECT @resultado = STRING_AGG(Lineas.LineaDeConversacion, CHAR(13) + CHAR(10))
                         WITHIN GROUP (ORDER BY Lineas.OrdenSegmento ASC)
    FROM 
    (
        -- PASO 1: Esta subconsulta interna prepara cada línea individualmente.
        SELECT
            CAST(segmento.[key] AS INT) AS OrdenSegmento,
            CONCAT(
                ''['', JSON_VALUE(segmento.value, ''$.startTime''), '' - '', JSON_VALUE(segmento.value, ''$.endTime''), ''] '',
                JSON_VALUE(segmento.value, ''$.speakerLabel''), '': '',
                -- El STRING_AGG de las palabras se ejecuta aquí adentro.
                (SELECT STRING_AGG(CAST(palabra.value AS NVARCHAR(MAX)), '' '') FROM OPENJSON(segmento.value, ''$.text'') AS palabra)
            ) AS LineaDeConversacion
        FROM
            OPENJSON(@jsonSegments) AS segmento
    ) AS Lineas; -- Le damos un nombre a nuestra subconsulta.

    RETURN @resultado;
END;
' 
END
GO
