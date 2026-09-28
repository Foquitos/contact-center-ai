-- View [chatbot].[vw_ivr_solo_mitrol_Hidra]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[chatbot].[vw_ivr_solo_mitrol_Hidra]'))
EXEC dbo.sp_executesql @statement = N'
-- VISTA 2: SOLO IVR / SIN AGENTE
CREATE   VIEW [chatbot].[vw_ivr_solo_mitrol_Hidra]
AS
SELECT *
FROM [chatbot].[vw_interacciones_mitrol_Hidra]
WHERE Segmento = 1 AND operador_id IS NULL;
' 
GO
