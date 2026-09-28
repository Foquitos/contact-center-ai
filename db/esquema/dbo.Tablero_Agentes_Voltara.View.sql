-- View [dbo].[Tablero_Agentes_Voltara]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[dbo].[Tablero_Agentes_Voltara]'))
EXEC dbo.sp_executesql @statement = N'CREATE VIEW [dbo].[Tablero_Agentes_Voltara]
AS
WITH AgentesUnicos AS (SELECT a.Intervalo, CAST(a.Intervalo AS date) AS Fecha, CAST(a.Intervalo AS time) AS Horas, u.nomina_id, MAX(CAST(a.[Tiempo Agentes Logueados] AS FLOAT)) AS Tiempo_Logueado, 
                                                                    MAX(CAST(a.[Tiempo Agentes en pausa] AS FLOAT)) AS Tiempo_Pausa, MAX(CAST(a.[% Ocupacion] AS FLOAT)) AS Ocupacion
                                                  FROM      dbo.[Voltara Enerval informe agente] AS a LEFT OUTER JOIN
                                                                    dbo.usuarios AS u ON a.Login COLLATE SQL_Latin1_General_CP1_CS_AS = u.usuario COLLATE SQL_Latin1_General_CP1_CS_AS
                                                  WHERE   (a.Intervalo >= ''2025-05-01'') AND (u.usuario IS NOT NULL)
                                                  GROUP BY a.Intervalo, u.nomina_id)
    SELECT a.Intervalo AS [Fecha/Horas], a.Fecha, a.Horas, SUM(a.Tiempo_Logueado / 1800.0) AS Cant_op, SUM(a.Tiempo_Logueado) / 3600.0 AS Logueado, SUM(a.Tiempo_Pausa) / 3600.0 AS [Tiempo Agentes en pausa], ROUND(AVG(a.Ocupacion), 
                      2) AS [% Ocupacion], AVG(i_intervalo.Cant_op) AS [Op planificados (intervalo)]
    FROM     AgentesUnicos AS a LEFT OUTER JOIN
                          (SELECT i.Interval_Start, COUNT(*) AS Cant_op
                           FROM      dbo.payroll_futuro AS p INNER JOIN
                                             dbo.operadores AS o ON p.id_operadores = o.id INNER JOIN
                                             dbo.campanas AS c ON c.id = o.campana_id INNER JOIN
                                             dbo.Intervalos AS i ON CAST(i.Interval_Start AS DATE) = p.fecha AND CAST(i.Interval_Start AS TIME) >= p.inicio AND (p.inicio < p.final AND CAST(i.Interval_Start AS TIME) < p.final OR
                                             p.inicio > p.final AND (CAST(i.Interval_Start AS TIME) < ''23:59:59'' OR
                                             CAST(i.Interval_Start AS TIME) >= ''00:00:00'')) INNER JOIN
                                             dbo.puestos AS pu ON pu.id = o.puesto_id
                           WHERE   (c.campana = ''Voltara'') AND (c.sub_campana IN (''T1 - Teléfono'', ''T1 - Emergencias'', ''Artefactos Dañados'', ''T2T3 - Teléfono'')) AND (p.horas_programadas > 0) AND (DATEPART(MINUTE, i.Interval_Start) IN (0, 30)) AND 
                                             (pu.puesto IN (''Operador 2'', ''Operador Telefónico'', ''Operador Capacitación'')) AND (p.codigo IS NULL OR
                                             p.codigo = ''ABS'')
                           GROUP BY i.Interval_Start) AS i_intervalo ON i_intervalo.Interval_Start = a.Intervalo
    GROUP BY a.Intervalo, a.Fecha, a.Horas
' 
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPane1' , N'SCHEMA',N'dbo', N'VIEW',N'Tablero_Agentes_Voltara', NULL,NULL))
	EXEC sys.sp_addextendedproperty @name=N'MS_DiagramPane1', @value=N'[0E232FF0-B466-11cf-A24F-00AA00A3EFFF, 1.00]
Begin DesignProperties = 
   Begin PaneConfigurations = 
      Begin PaneConfiguration = 0
         NumPanes = 4
         Configuration = "(H (1[41] 4[20] 2[24] 3) )"
      End
      Begin PaneConfiguration = 1
         NumPanes = 3
         Configuration = "(H (1 [50] 4 [25] 3))"
      End
      Begin PaneConfiguration = 2
         NumPanes = 3
         Configuration = "(H (1 [50] 2 [25] 3))"
      End
      Begin PaneConfiguration = 3
         NumPanes = 3
         Configuration = "(H (4 [30] 2 [40] 3))"
      End
      Begin PaneConfiguration = 4
         NumPanes = 2
         Configuration = "(H (1 [56] 3))"
      End
      Begin PaneConfiguration = 5
         NumPanes = 2
         Configuration = "(H (2 [66] 3))"
      End
      Begin PaneConfiguration = 6
         NumPanes = 2
         Configuration = "(H (4 [50] 3))"
      End
      Begin PaneConfiguration = 7
         NumPanes = 1
         Configuration = "(V (3))"
      End
      Begin PaneConfiguration = 8
         NumPanes = 3
         Configuration = "(H (1[56] 4[18] 2) )"
      End
      Begin PaneConfiguration = 9
         NumPanes = 2
         Configuration = "(H (1 [75] 4))"
      End
      Begin PaneConfiguration = 10
         NumPanes = 2
         Configuration = "(H (1[66] 2) )"
      End
      Begin PaneConfiguration = 11
         NumPanes = 2
         Configuration = "(H (4 [60] 2))"
      End
      Begin PaneConfiguration = 12
         NumPanes = 1
         Configuration = "(H (1) )"
      End
      Begin PaneConfiguration = 13
         NumPanes = 1
         Configuration = "(V (4))"
      End
      Begin PaneConfiguration = 14
         NumPanes = 1
         Configuration = "(V (2))"
      End
      ActivePaneConfig = 0
   End
   Begin DiagramPane = 
      Begin Origin = 
         Top = 0
         Left = 0
      End
      Begin Tables = 
         Begin Table = "a"
            Begin Extent = 
               Top = 7
               Left = 48
               Bottom = 170
               Right = 283
            End
            DisplayFlags = 280
            TopColumn = 0
         End
         Begin Table = "i_intervalo"
            Begin Extent = 
               Top = 7
               Left = 331
               Bottom = 126
               Right = 541
            End
            DisplayFlags = 280
            TopColumn = 0
         End
      End
   End
   Begin SQLPane = 
   End
   Begin DataPane = 
      Begin ParameterDefaults = ""
      End
   End
   Begin CriteriaPane = 
      Begin ColumnWidths = 12
         Column = 1440
         Alias = 900
         Table = 1176
         Output = 720
         Append = 1400
         NewValue = 1170
         SortType = 1356
         SortOrder = 1416
         GroupBy = 1350
         Filter = 1356
         Or = 1350
         Or = 1350
         Or = 1350
      End
   End
End
' , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'Tablero_Agentes_Voltara'
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPaneCount' , N'SCHEMA',N'dbo', N'VIEW',N'Tablero_Agentes_Voltara', NULL,NULL))
	EXEC sys.sp_addextendedproperty @name=N'MS_DiagramPaneCount', @value=1 , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'Tablero_Agentes_Voltara'
GO
