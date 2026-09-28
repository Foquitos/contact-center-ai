-- View [dbo].[Acumuladores_Tecnomax_Tablero]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[dbo].[Acumuladores_Tecnomax_Tablero]'))
EXEC dbo.sp_executesql @statement = N'CREATE VIEW [dbo].[Acumuladores_Tecnomax_Tablero]
AS
WITH FechasBase AS (
    SELECT DISTINCT CAST(a.Intervalo AS date) AS Fecha
    FROM dbo.Acumuladores_de_campana AS a
    INNER JOIN dbo.campanas_mitrol AS cam ON cam.id = a.idCampania
    WHERE cam.campana = ''Salientes Tecnomax'' 
      AND DATEPART(YEAR, a.fecha_inicio) = DATEPART(YEAR, GETDATE())
), 
MinutosBase AS (
    SELECT DATEADD(MINUTE, n * 30, CAST(''00:00:00'' AS time)) AS Hora
    FROM (SELECT TOP 48 ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS n
          FROM master..spt_values) AS x
), 
IntervalosBase AS (
    SELECT 
        DATEADD(MINUTE, DATEPART(MINUTE, h.Hora), DATEADD(HOUR, DATEPART(HOUR, h.Hora), CAST(f.Fecha AS datetime))) AS Intervalo,
        CAST(DATEADD(HOUR, DATEPART(HOUR, h.Hora), 0) AS time) AS Intervalo2
    FROM FechasBase f 
    CROSS JOIN MinutosBase h
), 
DatosConIntervalos AS (
    SELECT 
        a.Intervalo,
        CAST(DATEADD(HOUR, DATEPART(HOUR, a.Intervalo), 0) AS time) AS Intervalo2,
        SUM(a.AgentesAtendidas) AS [AGENTES ATENDIDAS],
        AVG(CAST(a.AuxiliarTotal AS float) / 86400) AS [AUXILIAR TOTAL], 
        AVG(CAST(a.TiempoRealLogueo AS float) / 86400) AS [TIEMPO REAL DE LOGUEO], 
        AVG(CAST(a.Login AS float) / 3600) AS [Cantidad Op], 
        CASE 
            WHEN SUM(a.Login) <> 0 THEN 
                SUM(CAST((a.Ring + a.ACW + a.Connect) AS float) / 3600) / 
                AVG(CAST(a.Login AS float) / 3600) 
            ELSE 0 
        END AS [Ocupación.], 
        CASE 
            WHEN SUM(a.Login) <> 0 THEN 
                SUM(CAST(a.AgentesAtendidas AS float)) / 
                AVG(CAST(a.Login AS float) / 3600) 
            ELSE 0 
        END AS [CPH.], 
        AVG(CAST(a.Login AS float) / 3600) AS [Tpo Log Formt], 
        AVG(i_2.Cant_op) AS [Op planificados], 
        SUM(CAST(a.AHT AS float) * a.AgentesAtendidas / 86400) AS [Tiempo total aht], 
        MIN(CAST(a.Intervalo AS date)) AS FECHA, 
        SUM(c_1.Cortas) AS Cortas, 
        MIN(a.Intervalo) AS [Fecha 2]
    FROM dbo.Acumuladores_de_campana AS a
    LEFT JOIN (
        SELECT i.Interval_Start, COUNT(*) AS Cant_op
        FROM dbo.payroll_futuro p
        JOIN dbo.operadores o ON p.id_operadores = o.id
        JOIN dbo.campanas c ON c.id = o.campana_id
        JOIN dbo.Intervalos i 
            ON CAST(i.Interval_Start AS date) = p.fecha 
            AND CAST(i.Interval_Start AS time) >= p.inicio 
            AND (
                (p.inicio < p.final AND CAST(i.Interval_Start AS time) < p.final) OR
                (p.inicio > p.final AND (
                    CAST(i.Interval_Start AS time) < ''23:59:59'' OR 
                    CAST(i.Interval_Start AS time) >= ''00:00:00''
                ))
            )
        WHERE c.campana = ''Tecnomax'' 
          AND p.horas_programadas > 0 
          AND DATEPART(MINUTE, i.Interval_Start) = 0
        GROUP BY i.Interval_Start
    ) AS i_2 ON i_2.Interval_Start = a.Intervalo
    LEFT JOIN (
        SELECT i_1.Fecha, COUNT(*) AS Cant_op
        FROM dbo.payroll_futuro p
        JOIN dbo.operadores o ON p.id_operadores = o.id
        JOIN dbo.campanas c ON c.id = o.campana_id
        JOIN (
            SELECT DISTINCT CAST(Interval_Start AS date) AS Fecha
            FROM dbo.Intervalos
        ) AS i_1 ON i_1.Fecha = p.fecha
        WHERE c.campana = ''Tecnomax'' 
          AND p.horas_programadas > 0
        GROUP BY i_1.Fecha
    ) AS i_fecha ON i_fecha.Fecha = CAST(a.Intervalo AS date)
    LEFT JOIN (
        SELECT CAST(FORMAT(Inicio, ''yyyy-MM-dd HH:00:00'') AS datetime) AS Fecha, COUNT(*) AS Cortas
        FROM dbo.detalle_de_interacciones_por_agente
        WHERE Empresa = ''Tecnomax'' 
          AND (Preview + Dialing + Ringing + TalkingTime + Hold + ACW) <= 10 
          AND Atendidas = 1
        GROUP BY CAST(FORMAT(Inicio, ''yyyy-MM-dd HH:00:00'') AS datetime)
    ) AS c_1 ON c_1.Fecha = a.Intervalo
    JOIN dbo.campanas_mitrol cam ON cam.id = a.idCampania
    WHERE cam.campana = ''Salientes Tecnomax'' 
      AND DATEPART(YEAR, a.fecha_inicio) = DATEPART(YEAR, GETDATE())
    GROUP BY a.Intervalo
)
SELECT 
    b.Intervalo,
    b.Intervalo2,
    ISNULL(d.[AGENTES ATENDIDAS], 0) AS [AGENTES ATENDIDAS],
    d.[AUXILIAR TOTAL],
    d.[TIEMPO REAL DE LOGUEO],
    d.[Cantidad Op],
    d.[Ocupación.],
    d.[CPH.],
    d.[Tpo Log Formt],
    d.[Op planificados],
    d.[Tiempo total aht],
    ISNULL(d.FECHA, CAST(b.Intervalo AS date)) AS FECHA,
    d.Cortas,
    ISNULL(d.[Fecha 2], b.Intervalo) AS [Fecha 2]
FROM IntervalosBase b
LEFT JOIN DatosConIntervalos d ON b.Intervalo = d.Intervalo
WHERE b.Intervalo <= DATEADD(MINUTE, -30, DATEADD(MINUTE, DATEDIFF(MINUTE, 0, GETDATE()) / 30 * 30, 0))
' 
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPane1' , N'SCHEMA',N'dbo', N'VIEW',N'Acumuladores_Tecnomax_Tablero', NULL,NULL))
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
         Top = -360
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
' , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'Acumuladores_Tecnomax_Tablero'
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPaneCount' , N'SCHEMA',N'dbo', N'VIEW',N'Acumuladores_Tecnomax_Tablero', NULL,NULL))
	EXEC sys.sp_addextendedproperty @name=N'MS_DiagramPaneCount', @value=1 , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'Acumuladores_Tecnomax_Tablero'
GO
