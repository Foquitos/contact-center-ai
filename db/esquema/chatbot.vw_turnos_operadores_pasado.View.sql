-- View [chatbot].[vw_turnos_operadores_pasado]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[chatbot].[vw_turnos_operadores_pasado]'))
EXEC dbo.sp_executesql @statement = N'create view [chatbot].[vw_turnos_operadores_pasado] as
SELECT
      [id_operadores] as operador_id
      ,[fecha]
      ,[inicio] as Inicio_turno
      ,[final] as Fin_turno
      ,[codigo]
      ,[conexion]
      ,[desconexion]
      ,[horas_programadas]
      ,[horas_trabajadas]
      ,case 
      when horas_programadas > 0 then 1
      else 0
      end as programado
      ,case 
      when horas_trabajadas > 0 then 1
      else 0
      end as trabajado
      ,case 
      when codigo like ''VA%'' then 1
      else 0
      end as vacaciones
  FROM [Acme].[dbo].[payroll]
' 
GO
