-- View [chatbot].[vw_turnos_operadores_futuros]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[chatbot].[vw_turnos_operadores_futuros]'))
EXEC dbo.sp_executesql @statement = N'create view [chatbot].[vw_turnos_operadores_futuros] as
SELECT 
      [id_operadores] as operador_id
      ,[fecha]
      ,[inicio] as Inicio_turno
      ,[final] as Fin_turno
      ,[horas_programadas]
  FROM [Acme].[dbo].[payroll_futuro]
  where horas_programadas > 0' 
GO
