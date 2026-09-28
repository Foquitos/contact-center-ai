-- View [pagina_web].[preguntas_no_encotradas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[pagina_web].[preguntas_no_encotradas]'))
EXEC dbo.sp_executesql @statement = N'

CREATE view [pagina_web].[preguntas_no_encotradas] as
SELECT [user_id] Usuario
      ,[effective_campana] Campaña
      ,[query] Pregunta
,context [Informacion usada]
      ,[fecha] Fecha
  FROM [Acme].[pagina_web].[query_chatbots_logs]
  where (response like ''La información para responder a esa consulta%'' or response like ''No encontré información sobre%'') and user_id != 10000001
' 
GO
