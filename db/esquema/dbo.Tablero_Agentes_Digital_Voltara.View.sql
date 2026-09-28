-- View [dbo].[Tablero_Agentes_Digital_Voltara]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[dbo].[Tablero_Agentes_Digital_Voltara]'))
EXEC dbo.sp_executesql @statement = N'CREATE VIEW [dbo].[Tablero_Agentes_Digital_Voltara]
AS
WITH Logueos AS (
    SELECT 
        l.Usuario, 
        CONVERT(date, l.INICIO) AS Fecha, 
        l.INICIO, 
        COALESCE(l.FIN, GETDATE()) AS FIN
    FROM [Acme].[dbo].[Voltara_Salesforce_logueos] l
    JOIN usuarios u ON u.usuario = l.Usuario
    JOIN nomina n ON n.id = u.nomina_id
    JOIN (
        SELECT o.legajo_id, o.fecha_desde, o.fecha_hasta
        FROM [Acme].[dbo].[operadores] o
        JOIN campanas c ON c.id = o.campana_id
        WHERE c.sub_campana in (''T2T3 - BO'',''Digital'',''Agrupadas - Digital'',''Backoffice RRSS'')
    ) o 
        ON n.id = o.legajo_id
       AND l.INICIO >= o.fecha_desde
       AND (l.INICIO < o.fecha_hasta OR o.fecha_hasta IS NULL)
),
Nums AS (
    SELECT TOP (1000)
        ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS n
    FROM sys.all_objects
),
MaxInicioPorDia AS (
    SELECT Fecha, MAX(INICIO) AS MaxInicio
    FROM Logueos
    GROUP BY Fecha
),
Intervalos AS (
    SELECT DISTINCT 
        L.Fecha,
        DATEADD(hour, v.n, CAST(L.Fecha AS datetime)) AS HoraInicio,
        DATEADD(hour, v.n + 1, CAST(L.Fecha AS datetime)) AS HoraFin
    FROM (SELECT DISTINCT Fecha FROM Logueos) L
    CROSS JOIN (
        SELECT n FROM Nums WHERE n BETWEEN 0 AND 23
    ) v
    JOIN MaxInicioPorDia m ON L.Fecha = m.Fecha
    WHERE DATEADD(hour, v.n, CAST(L.Fecha AS datetime)) <= m.MaxInicio
),
MinutosPorUsuario AS (
    SELECT 
        i.Fecha,
        i.HoraInicio,
        l.Usuario,
        SUM(
            CASE 
                WHEN l.FIN <= i.HoraInicio OR l.INICIO >= i.HoraFin THEN 0
                ELSE DATEDIFF(
                    MINUTE,
                    CASE WHEN l.INICIO < i.HoraInicio THEN i.HoraInicio ELSE l.INICIO END,
                    CASE WHEN l.FIN > i.HoraFin THEN i.HoraFin ELSE l.FIN END
                )
            END
        ) AS MinutesPerUser
    FROM Logueos l
    JOIN Intervalos i 
        ON l.Fecha = i.Fecha
       AND l.FIN > i.HoraInicio
       AND l.INICIO < i.HoraFin
    GROUP BY i.Fecha, i.HoraInicio, l.Usuario
),
MinutosPorUsuarioCapped AS (
    SELECT 
        Fecha,
        HoraInicio,
        Usuario,
        CASE WHEN MinutesPerUser > 60 THEN 60 ELSE MinutesPerUser END AS MinutesCapped
    FROM MinutosPorUsuario
)
SELECT 
    Fecha,
    CONVERT(VARCHAR(8), HoraInicio, 108) AS Intervalo,
    SUM(MinutesCapped) / 60.0 AS OperadoresConectados
FROM MinutosPorUsuarioCapped
GROUP BY Fecha, HoraInicio;


' 
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPane1' , N'SCHEMA',N'dbo', N'VIEW',N'Tablero_Agentes_Digital_Voltara', NULL,NULL))
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
' , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'Tablero_Agentes_Digital_Voltara'
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPaneCount' , N'SCHEMA',N'dbo', N'VIEW',N'Tablero_Agentes_Digital_Voltara', NULL,NULL))
	EXEC sys.sp_addextendedproperty @name=N'MS_DiagramPaneCount', @value=1 , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'Tablero_Agentes_Digital_Voltara'
GO
