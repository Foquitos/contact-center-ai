-- View [chatbot].[vw_nomina]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[chatbot].[vw_nomina]'))
EXEC dbo.sp_executesql @statement = N'create view [chatbot].[vw_nomina] as

SELECT [id] as nomina_id
      ,[legajo]
      ,[documento]
      ,[nombre]
      ,[apellido]
      ,[fecha_alta]
      ,[fecha_piso]
  FROM [Acme].[dbo].[nomina]
' 
GO
