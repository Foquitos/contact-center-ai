-- View [chatbot].[vw_llamadas_dental]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[chatbot].[vw_llamadas_dental]'))
EXEC dbo.sp_executesql @statement = N'

-- -----------------------------------------------------------------------------
-- 1. Tramos de llamada/chat con canal y operador.
-- -----------------------------------------------------------------------------
CREATE     VIEW [chatbot].[vw_llamadas_dental] AS
SELECT
    d.idInteraccion,
    d.segmento,
    d.Empresa        AS empresa,
    d.Campaña        AS campana,
    CASE
        WHEN d.Empresa = ''Facebook''         THEN ''Chat Facebook''
        WHEN d.Campaña LIKE ''%whatsapp%''    THEN ''Chat WhatsApp''
        WHEN d.Campaña LIKE ''%encuesta%''    THEN ''Encuesta Telefonica''
        ELSE ''Telefono''
    END              AS canal,
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
WHERE d.Empresa IN (''Odonto Plus'', ''Facebook'')
  AND d.Campaña NOT IN (''prueba_break_in'', ''test_break'')
  AND d.Campaña NOT LIKE ''%projub%''
  ;
' 
GO
