-- View [chatbot].[vw_nomina_extendida]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[chatbot].[vw_nomina_extendida]'))
EXEC dbo.sp_executesql @statement = N'create view [chatbot].[vw_nomina_extendida] as

SELECT [Documento] as nomina_id
      ,[CODIGO LABORAL] as CUIL
      ,[ÁREA] as Area_dentro_empresa
      ,Coalesce([EMPLEADOR], ''Acme Solutions S.A.'') as Empleador
      ,[SEXO]
      ,[DIRECCIÓN]
      ,[CÓDIGO POSTAL]
      ,[TELÉFONO]
      ,[EMAIL PERSONAL]
      ,[ESTADO CIVIL]
      ,[HIJOS]
      ,[NACIONALIDAD]
      ,[MOTIVO BAJA]
      ,[FECHA BAJA]
      ,[FECHA NACIMIENTO]
  FROM [Acme].[dbo].[nomina_extendida]
' 
GO
