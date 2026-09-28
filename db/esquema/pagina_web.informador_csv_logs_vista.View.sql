-- View [pagina_web].[informador_csv_logs_vista]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[pagina_web].[informador_csv_logs_vista]'))
EXEC dbo.sp_executesql @statement = N'create view [pagina_web].[informador_csv_logs_vista] as

SELECT [id]
      ,[Documento]
      ,[seccion_1]
      ,[seccion_2]
      ,[seccion_3]
      ,[seccion_4]
      ,[timestamp]
  FROM [Acme].[pagina_web].[Informador_csv_logs]
where Documento != 10000001' 
GO
