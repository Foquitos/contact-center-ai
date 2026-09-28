-- View [chatbot].[vw_resumen_llamadas_plansenior]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[chatbot].[vw_resumen_llamadas_plansenior]'))
EXEC dbo.sp_executesql @statement = N'
-- -----------------------------------------------------------------------------
-- 5. PLANSENIOR: resumen a nivel LLAMADA ÚNICA (vista principal para contar).
-- -----------------------------------------------------------------------------
CREATE   VIEW [chatbot].[vw_resumen_llamadas_plansenior] AS
WITH Segmentos AS (
    SELECT
        d.idInteraccion, d.segmento, d.Campaña, d.Sentido,
        d.inicio, d.Duración, d.Tipificación, d.LoginId,
        ROW_NUMBER() OVER (PARTITION BY d.idInteraccion ORDER BY d.segmento DESC) AS rn_ultimo
    FROM [Acme].[dbo].[detalle_de_interacciones_por_campana_lote] d
    WHERE d.Empresa = ''Odonto Plus''
      AND d.Campaña LIKE ''%projub%''
)
SELECT
    idInteraccion,
    MIN(Campaña)  AS campana,
    MIN(Sentido)  AS sentido_llamada,
    MIN(inicio)   AS fecha_hora_inicio,
    SUM(Duración) AS duracion_total_segundos,
    COUNT(*)      AS cantidad_segmentos,
    MAX(CASE WHEN rn_ultimo = 1 THEN Tipificación END) AS tipificacion_final,
    MAX(CASE WHEN rn_ultimo = 1 THEN LoginId END)      AS usuario_agente_final
FROM Segmentos
GROUP BY idInteraccion;
' 
GO
