-- View [chatbot].[vw_llamadas_plansenior]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[chatbot].[vw_llamadas_plansenior]'))
EXEC dbo.sp_executesql @statement = N'
-- -----------------------------------------------------------------------------
-- 4. PLANSENIOR: tramos de llamada con operador. Empresa propia que en la base
--    figura mal catalogada bajo Empresa ''Odonto Plus''; se identifica por el
--    patrón ''%projub%'' en Campaña. Todas sus campañas son telefónicas.
-- -----------------------------------------------------------------------------
CREATE   VIEW [chatbot].[vw_llamadas_plansenior] AS
SELECT
    d.idInteraccion,
    d.segmento,
    d.Campaña        AS campana,
    d.LoginId        AS usuario_agente,
    n.nombre         AS nombre_agente,
    n.apellido       AS apellido_agente,
    d.inicio         AS fecha_hora_inicio,
    d.Sentido        AS sentido_llamada,
    d.Tipificación   AS tipificacion_arbol,
    d.Duración       AS duracion_segundos
FROM [Acme].[dbo].[detalle_de_interacciones_por_campana_lote] d
LEFT JOIN [Acme].[dbo].[usuarios] u ON d.LoginId = u.usuario
LEFT JOIN [Acme].[dbo].[nomina]   n ON u.nomina_id = n.id
WHERE d.Empresa = ''Odonto Plus''
  AND d.Campaña LIKE ''%projub%'';
' 
GO
