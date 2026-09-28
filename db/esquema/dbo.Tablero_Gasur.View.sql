-- View [dbo].[Tablero_Gasur]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[dbo].[Tablero_Gasur]'))
EXEC dbo.sp_executesql @statement = N'CREATE VIEW [dbo].[Tablero_Gasur]
AS
WITH Intervalos AS (SELECT Fecha, Horas
                                        FROM      dbo.[Gasur Eficiencia] AS E
                                        UNION
                                        SELECT Fecha, CAST(DATEADD(HOUR, Hora, CAST(Fecha AS datetime)) AS time) AS Horas
                                        FROM     dbo.Gasur_tiempo_Logueo AS L)
    SELECT TOP (100) PERCENT CAST(I.Fecha AS datetime) + CAST(I.Horas AS datetime) AS [Fecha/Horas], I.Fecha, I.Horas, E.Completadas, E.Transferidas, E.Abandonadas, E.Promedio, E.Total, 
                      E.Completadas * E.Promedio / 86400 AS [Tiempo Hablado], f.Forecast, CASE WHEN c_1.Cortas IS NULL THEN 0 ELSE c_1.Cortas END AS Cortas, CASE WHEN Sl.[Atendidas antes de Umbral] IS NULL 
                      THEN 0 ELSE Sl.[Atendidas antes de Umbral] END AS [Atendidas antes de Umbral], CASE WHEN ASA.ASA IS NULL THEN 0 ELSE ASA.ASA END AS ASA, i_1.Cant_op, l_1.Logueado
    FROM     Intervalos AS I LEFT OUTER JOIN
                      dbo.[Gasur Eficiencia] AS E ON E.Fecha = I.Fecha AND E.Horas = I.Horas LEFT OUTER JOIN
                      dbo.Forecast AS f ON f.Intervalo = I.Horas AND f.Fecha = I.Fecha AND f.Campaña = ''Gasur'' LEFT OUTER JOIN
                          (SELECT CAST(FORMAT(Fecha, ''yyyy-MM-dd HH:00:00'') AS datetime) AS Fecha, COUNT(*) AS Cortas
                           FROM      dbo.[Gasur Llamadas]
                           WHERE   ([Duración (seg.)] <= 10) AND (Estado <> ''Abandonada'') AND (Tipo = ''Entrante'')
                           GROUP BY CAST(FORMAT(Fecha, ''yyyy-MM-dd HH:00:00'') AS datetime)) AS c_1 ON c_1.Fecha = CAST(I.Fecha AS datetime) + CAST(I.Horas AS datetime) LEFT OUTER JOIN
                          (SELECT CAST(FORMAT(Fecha, ''yyyy-MM-dd HH:00:00'') AS datetime) AS Fecha, COUNT(*) AS [Atendidas antes de Umbral]
                           FROM      dbo.[Gasur Llamadas] AS [Gasur Llamadas_2]
                           WHERE   ([Espera (seg.)] <= 10) AND (Estado <> ''Abandonada'') AND (Tipo = ''Entrante'')
                           GROUP BY CAST(FORMAT(Fecha, ''yyyy-MM-dd HH:00:00'') AS datetime)) AS Sl ON Sl.Fecha = CAST(I.Fecha AS datetime) + CAST(I.Horas AS datetime) LEFT OUTER JOIN
                          (SELECT CAST(FORMAT(Fecha, ''yyyy-MM-dd HH:00:00'') AS datetime) AS Fecha, AVG(CAST([Espera (seg.)] AS float) / 86400) AS ASA
                           FROM      dbo.[Gasur Llamadas] AS [Gasur Llamadas_1]
                           WHERE   (Estado <> ''Abandonada'') AND (Tipo = ''Entrante'')
                           GROUP BY CAST(FORMAT(Fecha, ''yyyy-MM-dd HH:00:00'') AS datetime)) AS ASA ON ASA.Fecha = CAST(I.Fecha AS datetime) + CAST(I.Horas AS datetime) LEFT OUTER JOIN
                          (SELECT i.Interval_Start, COUNT(*) AS Cant_op
                           FROM      dbo.payroll_futuro AS p INNER JOIN
                                             dbo.operadores AS o ON p.id_operadores = o.id INNER JOIN
                                             dbo.campanas AS c ON c.id = o.campana_id INNER JOIN
                                             dbo.puestos AS pu ON pu.id = o.puesto_id INNER JOIN
                                             dbo.Intervalos AS i ON CAST(i.Interval_Start AS DATE) = p.fecha AND CAST(i.Interval_Start AS TIME) >= p.inicio AND (p.inicio < p.final AND CAST(i.Interval_Start AS TIME) < p.final OR
                                             p.inicio > p.final AND (CAST(i.Interval_Start AS TIME) < ''23:59:59'' OR
                                             CAST(i.Interval_Start AS TIME) >= ''00:00:00''))
                           WHERE   (c.sub_campana = ''Gasur'') AND (p.horas_programadas > 0) AND (DATEPART(MINUTE, i.Interval_Start) = 0) AND (pu.puesto IN (''Operador 2'', ''Operador Telefónico'', ''Operador Capacitación''))
                           GROUP BY i.Interval_Start) AS i_1 ON i_1.Interval_Start = CAST(I.Fecha AS datetime) + CAST(I.Horas AS datetime) LEFT OUTER JOIN
                          (SELECT SUM(CAST(Tiempo_en_segundos AS float) / 3600) AS Logueado, Fecha, Hora
                           FROM      dbo.Gasur_tiempo_Logueo
                           GROUP BY Fecha, Hora) AS l_1 ON l_1.Fecha = I.Fecha AND DATEPART(HOUR, I.Horas) = l_1.Hora AND DATEPART(MINUTE, I.Horas) = 0
    WHERE  (I.Fecha <> CAST(GETDATE() AS date)) OR
                      (DATEPART(HOUR, I.Horas) <> DATEPART(HOUR, GETDATE()))
' 
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPane1' , N'SCHEMA',N'dbo', N'VIEW',N'Tablero_Gasur', NULL,NULL))
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
         Begin Table = "E"
            Begin Extent = 
               Top = 7
               Left = 48
               Bottom = 170
               Right = 270
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
         Begin Table = "l_1"
            Begin Extent = 
               Top = 819
               Left = 48
               Bottom = 938
               Right = 242
            End
            DisplayFlags = 280
            TopColumn = 0
         End
         Begin Table = "I"
            Begin Extent = 
               Top = 7
               Left = 318
               Bottom = 126
               Right = 512
            End
            DisplayFlags = 280
            TopColumn = 0
         End
         Begin Table = "c_1"
            Begin Extent = 
               Top = 7
               Left = 560
               Bottom = 126
               Right = 754
            End
            DisplayFlags = 280
            TopColumn = 0
         End
         Begin Table = "Sl"
            Begin Extent = 
               Top = 7
               Left = 802
               Bottom = 126
               Right = 1077
            End
            DisplayFlags = 280
            TopColumn = 0
         End
         Begin Table = "ASA"
            Begin Extent = 
               Top = 7
               Left = 1125
               Bottom = 126
               Right = 1319
            End
            DisplayFlags = 280
            TopColumn = 0
         E' , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'Tablero_Gasur'
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPane2' , N'SCHEMA',N'dbo', N'VIEW',N'Tablero_Gasur', NULL,NULL))
	EXEC sys.sp_addextendedproperty @name=N'MS_DiagramPane2', @value=N'nd
         Begin Table = "i_1"
            Begin Extent = 
               Top = 126
               Left = 318
               Bottom = 245
               Right = 512
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
' , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'Tablero_Gasur'
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPaneCount' , N'SCHEMA',N'dbo', N'VIEW',N'Tablero_Gasur', NULL,NULL))
	EXEC sys.sp_addextendedproperty @name=N'MS_DiagramPaneCount', @value=2 , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'Tablero_Gasur'
GO
