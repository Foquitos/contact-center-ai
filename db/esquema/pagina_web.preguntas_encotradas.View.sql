-- View [pagina_web].[preguntas_encotradas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[pagina_web].[preguntas_encotradas]'))
EXEC dbo.sp_executesql @statement = N'
CREATE view [pagina_web].[preguntas_encotradas] as
SELECT [user_id] Usuario
      ,[effective_campana] Campaña
      ,[query] Pregunta
	  ,response respuesta
,Context [Inforción usada]
      ,q.[fecha] Fecha
      ,cc.calificacion
      ,cc.comentario
  FROM [Acme].[pagina_web].[query_chatbots_logs] q
  left join pagina_web.chatbot_calificaciones cc on cc.task_id = q.task_id
  where (response not like ''La información para responder a esa consulta%'' and response not like ''No encontré información sobre%'') and user_id != 10000001
' 
GO
