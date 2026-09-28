-- View [chatbot].[vw_interacciones_mitrol_Hidra]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[chatbot].[vw_interacciones_mitrol_Hidra]'))
EXEC dbo.sp_executesql @statement = N'


CREATE   VIEW [chatbot].[vw_interacciones_mitrol_Hidra]
AS
SELECT   di.idInteraccion, di.Segmento, op.id AS operador_id, di.Inicio AS fecha_hora_inicio, di.idInteraccion AS id_interaccion, di.[Tipo Contacto] AS tipo_contacto, di.Sentido AS sentido_llamada, 
                         di.Cliente AS telefono_cliente, di.DNIS AS numero_marcado_dnis, di.LoginId AS usuario_agente, di.[Nombre Agente] AS nombre_agente, nom.documento AS dni_agente, 
                         op.hora_ingreso AS turno_hora_ingreso, op.hora_salida AS turno_hora_salida, di.Duración AS duracion_total_segundos, di.[Tiempo Tarifado] AS tiempo_tarifado_segundos, 
                         di.Ringing AS tiempo_ringing_segundos, di.TalkingTime AS tiempo_hablando_segundos, di.Hold AS tiempo_espera_hold_segundos, di.ACW AS tiempo_trabajo_posterior_acw_segundos, 
                         di.EnCola AS tiempo_en_cola_segundos, di.Tipificación AS tipificacion_arbol, di.[Tipos Tipificación] AS categoria_tipificacion, di.CRM AS id_ticket_crm, 
                         di.[Origen Corte] AS origen_corte_llamada, di.[Causa Terminación] AS causa_terminacion, di.Atendidas AS fue_atendida, di.Abandonada AS fue_abandonada, 
                         di.TransferIn AS transferencia_recibida, di.TransferOut AS transferencia_realizada
FROM         dbo.detalle_de_interacciones_por_campana_lote AS di LEFT OUTER JOIN
                         dbo.usuarios AS us ON di.LoginId = us.usuario LEFT OUTER JOIN
                         dbo.nomina AS nom ON nom.id = us.nomina_id LEFT OUTER JOIN
                         dbo.operadores AS op ON op.legajo_id = nom.id AND di.Inicio >= op.fecha_desde AND (di.Inicio <= op.fecha_hasta OR op.fecha_hasta IS NULL)
WHERE     (di.Empresa = ''Hidra'')  and (di.Entrante = 1 OR di.Derivada = 1 OR di.Abandonada = 1)
' 
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPane1' , N'SCHEMA',N'chatbot', N'VIEW',N'vw_interacciones_mitrol_Hidra', NULL,NULL))
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
         Configuration = "(H (1[50] 2[25] 3) )"
      End
      Begin PaneConfiguration = 3
         NumPanes = 3
         Configuration = "(H (4[30] 2[40] 3) )"
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
         Begin Table = "di"
            Begin Extent = 
               Top = 7
               Left = 48
               Bottom = 170
               Right = 292
            End
            DisplayFlags = 280
            TopColumn = 0
         End
         Begin Table = "us"
            Begin Extent = 
               Top = 175
               Left = 48
               Bottom = 316
               Right = 292
            End
            DisplayFlags = 280
            TopColumn = 0
         End
         Begin Table = "nom"
            Begin Extent = 
               Top = 322
               Left = 48
               Bottom = 485
               Right = 292
            End
            DisplayFlags = 280
            TopColumn = 0
         End
         Begin Table = "op"
            Begin Extent = 
               Top = 490
               Left = 48
               Bottom = 653
               Right = 292
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
      Begin ColumnWidths = 9
         Width = 284
         Width = 1500
         Width = 1500
         Width = 1500
         Width = 1500
         Width = 1500
         Width = 1500
         Width = 1500
         Width = 1500
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
' , @level0type=N'SCHEMA',@level0name=N'chatbot', @level1type=N'VIEW',@level1name=N'vw_interacciones_mitrol_Hidra'
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPaneCount' , N'SCHEMA',N'chatbot', N'VIEW',N'vw_interacciones_mitrol_Hidra', NULL,NULL))
	EXEC sys.sp_addextendedproperty @name=N'MS_DiagramPaneCount', @value=1 , @level0type=N'SCHEMA',@level0name=N'chatbot', @level1type=N'VIEW',@level1name=N'vw_interacciones_mitrol_Hidra'
GO
