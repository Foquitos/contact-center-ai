-- View [dbo].[Tablero_Voltara]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[dbo].[Tablero_Voltara]'))
EXEC dbo.sp_executesql @statement = N'CREATE VIEW [dbo].[Tablero_Voltara]
AS
SELECT e.Intervalo AS [Fecha/Horas], CAST(e.Intervalo AS date) AS Fecha, CAST(e.Intervalo AS time) AS Horas, e.[Skill ID], n.Skill, SUM(e.[Volumen de llamadas respondidas]) AS Completadas, SUM(e.[Llamadas transferidas]) AS Transferidas, 
                  SUM(e.[Volumen de llamadas abandonadas]) AS Abandonadas, SUM(e.[Volumen de llamadas entrantes]) AS Total, SUM(e.[Contestadas Umbral]) AS [Atendidas antes de Umbral], AVG(e.TMO) AS [TMO Promedio], 
                  SUM(CAST(e.[Volumen de llamadas respondidas] AS FLOAT) * CAST(e.TMO AS FLOAT) / 86400.0) AS [Tiempo Hablado], AVG(CAST(e.TME AS FLOAT) / 86400.0) AS ASA, SUM(f.Forecast) AS Forecast, SUM(ISNULL(ll.Cortas, 0)) AS Cortas, 
                  SUM(ISNULL(ll.Express, 0)) AS Express, SUM(ISNULL(ll.Normales, 0)) AS Normales, SUM(e.[Agentes Logueados]) AS Cant_op, SUM(CAST(e.[Tiempo Agentes Logueados] AS FLOAT) / 3600.0) AS Logueado, 
                  SUM(CAST(e.[Tiempo Agentes en pausa] AS FLOAT) / 3600.0) AS [Tiempo en pausa], ROUND(AVG(CAST(e.[% Ocupacion] AS FLOAT)), 2) AS [% Ocupacion], MAX(ns.Facturacion) AS TipoSkill, MAX(ns.CPH) AS CPH, SUM(ISNULL(tmo.Valor, 0)) 
                  AS [TMO objetivo]
FROM     dbo.[Voltara Enerval informe skills] AS e INNER JOIN
                  dbo.[Voltara normalizador por Skill] AS n ON e.[Skill ID] = n.[Skill ID] LEFT OUTER JOIN
                  dbo.Forecast AS f ON CAST(f.Fecha AS date) = CAST(e.Intervalo AS date) AND f.Intervalo = CAST(e.Intervalo AS time) AND f.Campaña = ''Voltara'' AND f.[Skill ID] = n.[Skill ID] LEFT OUTER JOIN
                  dbo.[Voltara Enerval normalizador cph skill] AS ns ON n.[Skill ID] = ns.[Skill ID] LEFT OUTER JOIN
                  dbo.[Voltara TMO por Skill] AS tmo ON tmo.[Skill ID] = e.[Skill ID] AND tmo.Fecha = CAST(e.Intervalo AS date) AND tmo.Hora = CAST(CAST(e.Intervalo AS time) AS time) LEFT OUTER JOIN
                      (SELECT CAST(ivr.[Fecha de Agente] AS date) AS Fecha, DATEPART(HOUR, ivr.[Fecha de Agente]) AS Hora, DATEPART(MINUTE, ivr.[Fecha de Agente]) / 30 * 30 AS Minuto, n.[Skill ID], 
                                         SUM(CASE WHEN ([Duración Ring] + [Duración Talk] + [Duración Hold] + [Duración ACW]) <= 30 THEN 1 ELSE 0 END) AS Cortas, SUM(CASE WHEN ((ns.Facturacion = ''Simple'' AND 
                                         ([Duración Ring] + [Duración Talk] + [Duración Hold] + [Duración ACW]) > 30 AND ([Duración Ring] + [Duración Talk] + [Duración Hold] + [Duración ACW]) <= 100) OR
                                         (ns.Facturacion = ''Compleja'' AND ([Duración Ring] + [Duración Talk] + [Duración Hold] + [Duración ACW]) > 30 AND ([Duración Ring] + [Duración Talk] + [Duración Hold] + [Duración ACW]) <= 200)) THEN 1 ELSE 0 END) AS Express, 
                                         SUM(CASE WHEN ((ns.Facturacion = ''Simple'' AND ([Duración Ring] + [Duración Talk] + [Duración Hold] + [Duración ACW]) > 100) OR
                                         (ns.Facturacion = ''Compleja'' AND ([Duración Ring] + [Duración Talk] + [Duración Hold] + [Duración ACW]) > 200)) THEN 1 ELSE 0 END) AS Normales
                       FROM      dbo.[Voltara informe IVR] AS ivr INNER JOIN
                                         dbo.usuarios AS u ON ivr.Agente = u.usuario INNER JOIN
                                         dbo.[Voltara normalizador por Skill] AS n ON ivr.Skill = n.Skill INNER JOIN
                                         dbo.[Voltara Enerval normalizador cph skill] AS ns ON n.[Skill ID] = ns.[Skill ID]
                       WHERE   (MONTH(ivr.[Fecha de Agente]) = MONTH(GETDATE())) AND (YEAR(ivr.[Fecha de Agente]) = YEAR(GETDATE()))
                       GROUP BY CAST(ivr.[Fecha de Agente] AS date), DATEPART(HOUR, ivr.[Fecha de Agente]), DATEPART(MINUTE, ivr.[Fecha de Agente]) / 30 * 30, n.[Skill ID]) AS ll ON CAST(e.Intervalo AS date) = ll.Fecha AND DATEPART(HOUR, 
                  e.Intervalo) = ll.Hora AND DATEPART(MINUTE, e.Intervalo) / 30 * 30 = ll.Minuto AND n.[Skill ID] = ll.[Skill ID]
WHERE  (e.Intervalo < DATEADD(MINUTE, DATEDIFF(MINUTE, 0, GETDATE()) / 30 * 30, 0))
GROUP BY e.Intervalo, CAST(e.Intervalo AS date), CAST(e.Intervalo AS time), e.[Skill ID], n.Skill
' 
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPane1' , N'SCHEMA',N'dbo', N'VIEW',N'Tablero_Voltara', NULL,NULL))
	EXEC sys.sp_addextendedproperty @name=N'MS_DiagramPane1', @value=N'[0E232FF0-B466-11cf-A24F-00AA00A3EFFF, 1.00]
Begin DesignProperties = 
   Begin PaneConfigurations = 
      Begin PaneConfiguration = 0
         NumPanes = 4
         Configuration = "(H (1[40] 4[20] 2[20] 3) )"
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
         Begin Table = "e"
            Begin Extent = 
               Top = 7
               Left = 48
               Bottom = 170
               Right = 381
            End
            DisplayFlags = 280
            TopColumn = 0
         End
         Begin Table = "n"
            Begin Extent = 
               Top = 7
               Left = 671
               Bottom = 126
               Right = 881
            End
            DisplayFlags = 280
            TopColumn = 0
         End
         Begin Table = "f"
            Begin Extent = 
               Top = 175
               Left = 48
               Bottom = 338
               Right = 242
            End
            DisplayFlags = 280
            TopColumn = 0
         End
         Begin Table = "ns"
            Begin Extent = 
               Top = 7
               Left = 429
               Bottom = 170
               Right = 623
            End
            DisplayFlags = 280
            TopColumn = 0
         End
         Begin Table = "tmo"
            Begin Extent = 
               Top = 7
               Left = 929
               Bottom = 170
               Right = 1139
            End
            DisplayFlags = 280
            TopColumn = 0
         End
         Begin Table = "ll"
            Begin Extent = 
               Top = 126
               Left = 671
               Bottom = 289
               Right = 881
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
    ' , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'Tablero_Voltara'
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPane2' , N'SCHEMA',N'dbo', N'VIEW',N'Tablero_Voltara', NULL,NULL))
	EXEC sys.sp_addextendedproperty @name=N'MS_DiagramPane2', @value=N'     Output = 720
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
' , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'Tablero_Voltara'
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPaneCount' , N'SCHEMA',N'dbo', N'VIEW',N'Tablero_Voltara', NULL,NULL))
	EXEC sys.sp_addextendedproperty @name=N'MS_DiagramPaneCount', @value=2 , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'Tablero_Voltara'
GO
