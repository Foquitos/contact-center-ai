-- View [dbo].[Hidra_AHT]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[dbo].[Hidra_AHT]'))
EXEC dbo.sp_executesql @statement = N'CREATE VIEW [dbo].[Hidra_AHT]
AS
SELECT CONVERT(VARCHAR(8), DATEADD(SECOND, DURACIÓN, 0), 108) AS DURACIÓN, CONVERT(VARCHAR(8), DATEADD(SECOND, d.[Tiempo Tarifado], 0), 108) AS [TIEMPO TARIFADO], CONVERT(VARCHAR(8), DATEADD(SECOND, d.Preview, 
                  0), 108) AS PREVIEW, CONVERT(VARCHAR(8), DATEADD(SECOND, d.Dialing, 0), 108) AS DIALING, CONVERT(VARCHAR(8), DATEADD(SECOND, d.Ringing, 0), 108) AS RINGING, CONVERT(VARCHAR(8), DATEADD(SECOND, d.TalkingTime, 0), 
                  108) AS TALKINGTIME, CONVERT(VARCHAR(8), DATEADD(SECOND, d.Hold, 0), 108) AS Hold, CONVERT(VARCHAR(8), DATEADD(SECOND, d.ACW, 0), 108) AS ACW, d.Derivada, d.Atendidas, d.[Agente No Atendidas], d.Abandonada, 
                  e.apellido + '' '' + e.nombre AS Supervisor, n.nombre + '' '' + n.apellido AS [Nombre Agente], n.fecha_piso AS [Fecha ingreso], RIGHT(''0'' + CONVERT(VARCHAR(2), DATEPART(HOUR, d.Inicio)), 2) + '':00:00'' AS INTERVALO, CAST(d.Inicio AS time) 
                  AS Inicio2, CONVERT(VARCHAR(8), DATEADD(SECOND, d.Preview + d.Dialing + d.Ringing + d.TalkingTime, 0), 108) AS [Tiempo AHT sin ACW], 1 AS Llamadas
FROM     dbo.detalle_de_interacciones_por_agente AS d INNER JOIN
                  dbo.usuarios AS u ON d.LoginId = u.usuario INNER JOIN
                  dbo.nomina AS n ON n.id = u.nomina_id INNER JOIN
                  dbo.operadores AS o ON o.legajo_id = n.id AND d.Inicio >= o.fecha_desde AND (d.Inicio < o.fecha_hasta OR
                  o.fecha_hasta IS NULL) INNER JOIN
                  dbo.equipos AS e ON o.equipo_id = e.id
WHERE  (d.Campaña = ''HIDRAIN'') AND (d.fecha_inicio = CAST(GETDATE() AS Date)) AND (d.Sentido = ''entrante'') AND (d.Agente <> ''Campos Daniela Estela'')
' 
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPane1' , N'SCHEMA',N'dbo', N'VIEW',N'Hidra_AHT', NULL,NULL))
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
         Begin Table = "d"
            Begin Extent = 
               Top = 7
               Left = 48
               Bottom = 170
               Right = 297
            End
            DisplayFlags = 280
            TopColumn = 0
         End
         Begin Table = "u"
            Begin Extent = 
               Top = 175
               Left = 48
               Bottom = 316
               Right = 242
            End
            DisplayFlags = 280
            TopColumn = 0
         End
         Begin Table = "n"
            Begin Extent = 
               Top = 322
               Left = 48
               Bottom = 485
               Right = 242
            End
            DisplayFlags = 280
            TopColumn = 0
         End
         Begin Table = "o"
            Begin Extent = 
               Top = 490
               Left = 48
               Bottom = 653
               Right = 265
            End
            DisplayFlags = 280
            TopColumn = 0
         End
         Begin Table = "e"
            Begin Extent = 
               Top = 658
               Left = 48
               Bottom = 821
               Right = 242
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
      Begin ColumnWidths = 11
         Column = 1440
         Alias = 900
         Table = 1170
         Output = 720
         Append = 1400
         NewValue = 1170
         SortType = 1350
         SortOrder = 1410
         GroupBy = 1350
         Filter = 1350
         Or = 1350
         Or = 1350
         Or = 1350
      End
   End
End
' , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'Hidra_AHT'
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPaneCount' , N'SCHEMA',N'dbo', N'VIEW',N'Hidra_AHT', NULL,NULL))
	EXEC sys.sp_addextendedproperty @name=N'MS_DiagramPaneCount', @value=1 , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'Hidra_AHT'
GO
