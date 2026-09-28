-- View [dbo].[Eficiencia_Tecnomax_Tablero]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[dbo].[Eficiencia_Tecnomax_Tablero]'))
EXEC dbo.sp_executesql @statement = N'CREATE VIEW [dbo].[Eficiencia_Tecnomax_Tablero]
AS
SELECT e.Fecha, SUM(e.EntrantesACola) AS [Entrantes A Cola], SUM(e.Entrantes) AS Entrantes, SUM(e.Abandonadas) AS Abandonadas, SUM(e.Atendidas) AS Atendidas, SUM(e.AgNoAtendio) AS [Agente No Atendidas], SUM(e.AtAntesUmbral) 
                  AS [Atendidas Antes del Umbral], SUM(CAST(e.ATT * e.Atendidas AS FLOAT) / 86400) AS [Tpo Total Atención], SUM(CAST(e.ASA * e.Atendidas AS FLOAT) / 86400) AS [Tpo Total ASA], SUM(CAST(e.AHT * e.Atendidas AS FLOAT) / 86400) 
                  AS [Tpo Total AHT], CASE WHEN camem.Campaña IN (''Diggit'', ''Ferbi'', ''Salientes no disponible'', ''Salientes Tecnomax'', ''Courier TDF inbound'') THEN ''Telefónico'' WHEN camem.Campaña IN (''Mail_IN_consultas'', ''Mail_IN_consultasferbi'', 
                  ''Mail_IN_consultasjblargentina'', ''Mail_OUT_consultasjblargentina'', ''Mail_OUT_consultasferbi'', ''Mail_OUT_consultas'', ''Mail_IN_QUIT_consultas'', ''Mail_IN_consutas_courier'') THEN ''Mail'' ELSE ''No encontrado'' END AS Canal
FROM     dbo.eficiencia_de_campana AS e INNER JOIN
                  dbo.campanas_empresa_mitrol AS camem ON camem.idCampania = e.idCampania
WHERE  (camem.Empresa = ''Tecnomax'') AND (DATEPART(YEAR, e.fecha_inicio) = DATEPART(YEAR, GETDATE())) AND (camem.Campaña IN (''Diggit'', ''Ferbi'', ''Courier TDF inbound'', ''Salientes no disponible'', ''Salientes Tecnomax'', ''Mail_IN_consultas'', 
                  ''Mail_IN_consultasferbi'', ''Mail_IN_consultasjblargentina'', ''Mail_IN_consutas_courier'', ''Mail_OUT_consultasjblargentina'', ''Mail_OUT_consultasferbi'', ''Mail_OUT_consultas'', ''Mail_IN_QUIT_consultas''))
GROUP BY e.Fecha, CASE WHEN camem.Campaña IN (''Diggit'', ''Ferbi'', ''Salientes no disponible'', ''Salientes Tecnomax'', ''Courier TDF inbound'') THEN ''Telefónico'' WHEN camem.Campaña IN (''Mail_IN_consultas'', ''Mail_IN_consultasferbi'', 
                  ''Mail_IN_consultasjblargentina'', ''Mail_OUT_consultasjblargentina'', ''Mail_OUT_consultasferbi'', ''Mail_OUT_consultas'', ''Mail_IN_QUIT_consultas'', ''Mail_IN_consutas_courier'') THEN ''Mail'' ELSE ''No encontrado'' END
' 
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPane1' , N'SCHEMA',N'dbo', N'VIEW',N'Eficiencia_Tecnomax_Tablero', NULL,NULL))
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
               Right = 283
            End
            DisplayFlags = 280
            TopColumn = 0
         End
         Begin Table = "camem"
            Begin Extent = 
               Top = 294
               Left = 48
               Bottom = 413
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
' , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'Eficiencia_Tecnomax_Tablero'
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPaneCount' , N'SCHEMA',N'dbo', N'VIEW',N'Eficiencia_Tecnomax_Tablero', NULL,NULL))
	EXEC sys.sp_addextendedproperty @name=N'MS_DiagramPaneCount', @value=1 , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'Eficiencia_Tecnomax_Tablero'
GO
