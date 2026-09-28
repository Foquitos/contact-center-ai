-- View [dbo].[Gasur_tiempo_Logueo]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[dbo].[Gasur_tiempo_Logueo]'))
EXEC dbo.sp_executesql @statement = N'CREATE VIEW [dbo].[Gasur_tiempo_Logueo]
AS
WITH HourlyLogins AS (SELECT Fecha, DATEPART(HOUR, Login) AS LoginHour, CASE WHEN Logout IS NULL THEN DATEPART(HOUR, GETDATE()) ELSE DATEPART(HOUR, Logout) END AS LogoutHour, Login, CASE WHEN Logout IS NULL 
                                                                THEN GETDATE() ELSE Logout END AS LogoutTime
                                              FROM      dbo.[Gasur Actividad]
                                              WHERE   (Agente <> ''supapoyo5000 (5000)'')), HourSegments AS
    (SELECT Fecha, LoginHour AS Hora, DATEDIFF(SECOND, Login, LogoutTime) AS Tiempo
     FROM      HourlyLogins AS HourlyLogins_3
     WHERE   (LoginHour = LogoutHour)
     UNION ALL
     SELECT Fecha, LoginHour AS Hora, DATEDIFF(SECOND, Login, DATEADD(HOUR, 1, DATEADD(HOUR, DATEDIFF(HOUR, 0, Login), 0))) AS Tiempo
     FROM     HourlyLogins AS HourlyLogins_2
     WHERE  (LoginHour <> LogoutHour)
     UNION ALL
     SELECT HL.Fecha, H.hour AS Hora, 3600 AS Tiempo
     FROM     HourlyLogins AS HL INNER JOIN
                           (SELECT TOP (24) number AS hour
                            FROM      master.dbo.spt_values
                            WHERE   (type = ''P'') AND (number BETWEEN 0 AND 23)) AS H ON HL.LoginHour < HL.LogoutHour AND H.hour > HL.LoginHour AND H.hour < HL.LogoutHour OR HL.LoginHour > HL.LogoutHour AND (H.hour > HL.LoginHour OR
                       H.hour < HL.LogoutHour)
     WHERE  (HL.LoginHour <> HL.LogoutHour)
     UNION ALL
     SELECT CASE WHEN LoginHour > LogoutHour THEN DATEADD(DAY, 1, Fecha) ELSE Fecha END AS Fecha, LogoutHour AS Hora, DATEDIFF(SECOND, DATEADD(HOUR, DATEDIFF(HOUR, 0, LogoutTime), 0), LogoutTime) AS Tiempo
     FROM     HourlyLogins AS HourlyLogins_1
     WHERE  (LoginHour <> LogoutHour))
    SELECT Fecha, Hora, SUM(Tiempo) AS Tiempo_en_segundos
    FROM     HourSegments AS HourSegments_1
    GROUP BY Fecha, Hora
' 
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPane1' , N'SCHEMA',N'dbo', N'VIEW',N'Gasur_tiempo_Logueo', NULL,NULL))
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
         Begin Table = "HourSegments_1"
            Begin Extent = 
               Top = 7
               Left = 48
               Bottom = 148
               Right = 258
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
         Alias = 2100
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
' , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'Gasur_tiempo_Logueo'
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPaneCount' , N'SCHEMA',N'dbo', N'VIEW',N'Gasur_tiempo_Logueo', NULL,NULL))
	EXEC sys.sp_addextendedproperty @name=N'MS_DiagramPaneCount', @value=1 , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'Gasur_tiempo_Logueo'
GO
