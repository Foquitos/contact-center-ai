-- View [chatbot].[vw_operadores]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[chatbot].[vw_operadores]'))
EXEC dbo.sp_executesql @statement = N'create view [chatbot].[vw_operadores] as


SELECT op.id as operador_id
        ,[legajo_id] as nomina_id
      ,[estado] as Esta_activo
      ,[hora_ingreso]
      ,[hora_salida]
      ,ds1.dia AS franco_1
      ,ds2.dia AS franco_2
      ,p.puesto as Puesto
      ,c.cliente as Cliente_empresa
      ,c.campana as Campana_empresa
      ,c.sub_campana as Sub_campana_empresa
      ,[equipo_id]
      ,s.nombre as Sitio
      ,[fecha_ultima_capa]
  FROM [Acme].[dbo].[operadores] op
  join dias_sem ds1 on ds1.id = op.franco_1
  join dias_sem ds2 on ds2.id = op.franco_2
  join puestos p on p.id = op.puesto_id
  join campanas c on c.id = op.campana_id
  join sitios s on s.id = op.sitio_id
' 
GO
