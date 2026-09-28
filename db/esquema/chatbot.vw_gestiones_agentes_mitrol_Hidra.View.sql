-- View [chatbot].[vw_gestiones_agentes_mitrol_Hidra]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[chatbot].[vw_gestiones_agentes_mitrol_Hidra]'))
EXEC dbo.sp_executesql @statement = N'
-- VISTA 3: GESTIONES DE AGENTES Y TRANSFERENCIAS
CREATE   VIEW [chatbot].[vw_gestiones_agentes_mitrol_Hidra]
AS
SELECT *
FROM [chatbot].[vw_interacciones_mitrol_Hidra]
WHERE Segmento >= 2 OR operador_id IS NOT NULL;
' 
GO
