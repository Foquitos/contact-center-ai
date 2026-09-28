-- View [chatbot].[vw_sar_ingresos_Hidra]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[chatbot].[vw_sar_ingresos_Hidra]'))
EXEC dbo.sp_executesql @statement = N'
-- -----------------------------------------------------------------------------
-- 1. Hidra: ingresos SAR con datos del operador.
--    Un registro = un ingreso (ODT) cargado en SAR. Permite responder volumen
--    de ingresos, motivos más frecuentes, ingresos por operador, etc.
-- -----------------------------------------------------------------------------
CREATE   VIEW [chatbot].[vw_sar_ingresos_Hidra] AS
SELECT
    s.[Nro. ODT]            AS nro_odt,
    s.Legajo                AS legajo_sar,
    n.nombre                AS nombre_agente,
    n.apellido              AS apellido_agente,
    s.Motivo                AS motivo,
    s.Observacion           AS observacion,
    s.[Fecha de Ing.]       AS fecha_ingreso
FROM [Acme].[dbo].[Sar_Ingresos] s
LEFT JOIN [Acme].[dbo].[usuarios] u ON s.Legajo = u.usuario
LEFT JOIN [Acme].[dbo].[nomina]   n ON u.nomina_id = n.id;
' 
GO
