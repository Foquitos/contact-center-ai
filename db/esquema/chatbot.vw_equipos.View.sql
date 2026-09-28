-- View [chatbot].[vw_equipos]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[chatbot].[vw_equipos]'))
EXEC dbo.sp_executesql @statement = N'create view [chatbot].[vw_equipos] as

SELECT e.[id] as [equipo_id]
      ,[nombre] as nombre_equipo
      ,[apellido] as apellido_equipo
      ,c.cliente as cliente_equipo
      ,c.campana as campana_equipo
      ,c.sub_campana as sub_campana_equipo
      ,[jefatura] as equipo_id_jefatura
      ,[id_nomina] as nomina_id
  FROM [Acme].[dbo].[equipos] e
  join campanas c on c.id = e.campana_id
' 
GO
