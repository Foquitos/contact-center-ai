-- View [chatbot].[vw_resumen_llamadas_dental]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[chatbot].[vw_resumen_llamadas_dental]'))
EXEC dbo.sp_executesql @statement = N'

-- -----------------------------------------------------------------------------
-- 2. Resumen a nivel INTERACCIÓN ÚNICA, con canal.
--    Vista principal para CONTAR: llamadas = canal ''Telefono''; chats por canal.
-- -----------------------------------------------------------------------------
CREATE     VIEW [chatbot].[vw_resumen_llamadas_dental] AS
WITH Segmentos AS (
    SELECT
        d.idInteraccion, d.segmento, d.Empresa, d.Campaña, d.Sentido,
        d.inicio, d.Duración, d.Tipificación, d.LoginId,
        CASE
            WHEN d.Empresa = ''Facebook''         THEN ''Chat Facebook''
            WHEN d.Campaña LIKE ''%whatsapp%''    THEN ''Chat WhatsApp''
            WHEN d.Campaña LIKE ''%encuesta%''    THEN ''Encuesta Telefonica''
            ELSE ''Telefono''
        END AS canal,
        ROW_NUMBER() OVER (PARTITION BY d.idInteraccion ORDER BY d.segmento DESC) AS rn_ultimo
    FROM [Acme].[dbo].[detalle_de_interacciones_por_campana_lote] d
    WHERE d.Empresa IN (''Odonto Plus'', ''Facebook'')
      AND d.Campaña NOT IN (''prueba_break_in'', ''test_break'')
      AND d.Campaña NOT LIKE ''%projub%''
  AND d.Campaña NOT LIKE ''piloto%''
)
SELECT
    idInteraccion,
    MIN(Empresa)  AS empresa,
    MIN(Campaña)  AS campana,
    MIN(canal)    AS canal,
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
