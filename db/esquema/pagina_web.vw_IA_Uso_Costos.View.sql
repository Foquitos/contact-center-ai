-- View [pagina_web].[vw_IA_Uso_Costos]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[pagina_web].[vw_IA_Uso_Costos]'))
EXEC dbo.sp_executesql @statement = N'
/* --- 3. La fórmula del costo -------------------------------------------- */
CREATE   VIEW [pagina_web].[vw_IA_Uso_Costos]
AS
SELECT
    u.id,
    u.fecha,
    YEAR(u.fecha)                                   AS anio,
    MONTH(u.fecha)                                  AS mes,
    CONVERT(CHAR(7), u.fecha, 126)                  AS anio_mes,   -- ''YYYY-MM''
    u.feature,
    u.modo,
    u.modelo,
    u.user_id,
    u.campana_id,
    u.empresa_id,
    COALESCE(u.input_tokens, 0)                     AS input_tokens,
    COALESCE(u.output_tokens, 0)                    AS output_tokens,
    COALESCE(u.thoughts_tokens, 0)                  AS thoughts_tokens,
    -- Cuántos de esos input_tokens se cobraron a precio de caché. Se expone para
    -- poder medir cuánto está pegando el caché sin volver a IA_Uso.
    COALESCE(u.cached_tokens, 0)                    AS cached_tokens,
    COALESCE(u.input_tokens, 0)
      + COALESCE(u.output_tokens, 0)
      + COALESCE(u.thoughts_tokens, 0)              AS total_tokens,
    p.modelo                                        AS modelo_tarifa,
    p.fecha_desde                                   AS tarifa_desde,
    CAST(
        (
            -- prompt_token_count de Gemini YA incluye los cacheados: se separan las
            -- dos porciones en vez de sumar una de más.
            CASE WHEN COALESCE(u.input_tokens, 0) > COALESCE(u.cached_tokens, 0)
                 THEN COALESCE(u.input_tokens, 0) - COALESCE(u.cached_tokens, 0)
                 ELSE 0
            END / 1000000.0 * p.input_usd_mtok
          + COALESCE(u.cached_tokens, 0) / 1000000.0
              -- Si la tarifa del modelo todavía no tiene precio de caché cargado,
              -- se usa el 10% del input, que es la relación de la familia Flash.
              * COALESCE(p.cached_usd_mtok, p.input_usd_mtok * 0.10)
          + (COALESCE(u.output_tokens, 0) + COALESCE(u.thoughts_tokens, 0)) / 1000000.0 * p.output_usd_mtok
        )
        -- batch (asincrónico) y flex (sincrónico best-effort) valen lo mismo: la mitad
        -- del precio estándar.
        * CASE WHEN u.modo IN (''batch'', ''flex'') THEN 0.5 ELSE 1.0 END
    AS DECIMAL(18,6))                               AS costo_usd
FROM pagina_web.IA_Uso u
OUTER APPLY (
    SELECT TOP 1 pr.modelo, pr.fecha_desde, pr.input_usd_mtok, pr.output_usd_mtok, pr.cached_usd_mtok
    FROM pagina_web.IA_Precios pr
    WHERE pr.modelo = u.modelo
      AND pr.fecha_desde <= CAST(u.fecha AS DATE)
    ORDER BY pr.fecha_desde DESC
) p;
' 
GO
