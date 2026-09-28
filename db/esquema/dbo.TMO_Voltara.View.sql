-- View [dbo].[TMO_Voltara]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[dbo].[TMO_Voltara]'))
EXEC dbo.sp_executesql @statement = N'CREATE VIEW [dbo].[TMO_Voltara]
AS
SELECT 
    e.[Fecha de Inicio],

    CASE 
        WHEN DATEDIFF(DAY, o.fecha_desde, e.[Fecha de Inicio]) > 15 
            THEN ''Experto''
        ELSE ''Post Curso''
    END AS Estado,

    e.Skill,
    NS.[Skill ID],
    e.[Nombre de Agente],
    e.[Duración Ring],
    e.[Duración Talk],
    e.[Duración Hold],
    e.[Duración ACW],

    ISNULL(e.[Duración Ring], 0) 
        + ISNULL(e.[Duración Talk], 0) 
        + ISNULL(e.[Duración Hold], 0) 
        + ISNULL(e.[Duración ACW], 0) AS [Tiempo Total],

    e.[Fecha de Agente],

    CONCAT(eq.apellido, '' '', eq.nombre) AS Equipo,

    CONVERT(
        TIME, 
        DATEADD(
            MINUTE, 
            DATEDIFF(MINUTE, 0, e.[Fecha de Inicio]) / 30 * 30, 
            0
        )
    ) AS Intervalo,

    TMO.Valor AS TMO_Objetivo

FROM dbo.[Voltara informe IVR] AS e

INNER JOIN dbo.usuarios AS u 
    ON e.Agente COLLATE SQL_Latin1_General_CP1_CS_AS 
       = u.usuario COLLATE SQL_Latin1_General_CP1_CS_AS

INNER JOIN dbo.nomina AS n 
    ON n.id = u.nomina_id

INNER JOIN
(
    SELECT 
        o.id,
        o.legajo_id,
        o.estado,
        o.hora_ingreso,
        o.hora_salida,
        o.franco_1,
        o.franco_2,
        o.puesto_id,
        o.campana_id,
        o.equipo_id,
        o.sitio_id,
        o.fecha_desde,
        o.fecha_hasta

    FROM dbo.operadores AS o

    INNER JOIN dbo.campanas AS c 
        ON c.id = o.campana_id

    WHERE c.cliente = ''VOLTARA''
      AND o.puesto_id IN (1, 2, 3, 4)

) AS o 
    ON n.id = o.legajo_id
    AND CONVERT(date, e.[Fecha de Inicio]) >= o.fecha_desde
    AND (
        CONVERT(date, e.[Fecha de Inicio]) < o.fecha_hasta
        OR o.fecha_hasta IS NULL
    )

INNER JOIN dbo.equipos AS eq 
    ON eq.id = o.equipo_id

INNER JOIN dbo.Normalizador_VOLTARA AS nor 
    ON nor.Skill = e.Skill

INNER JOIN dbo.[Voltara normalizador por Skill] AS NS 
    ON NS.Skill = e.Skill

LEFT JOIN dbo.[Voltara TMO por Skill] AS TMO 
    ON TMO.[Skill ID] = NS.[Skill ID]
    AND TMO.Fecha = CONVERT(date, e.[Fecha de Agente])
    AND TMO.Hora = CONVERT(
        TIME, 
        DATEADD(
            MINUTE, 
            DATEDIFF(MINUTE, 0, e.[Fecha de Agente]) / 30 * 30, 
            0
        )
    )


UNION ALL

SELECT

    t.[Inicio de transferencia] AS [Fecha de Inicio],

    CASE 
        WHEN DATEDIFF(DAY, o.fecha_desde, t.[Inicio de transferencia]) > 15 
            THEN ''Experto''
        ELSE ''Post Curso''
    END AS Estado,

    t.[Intención de Destino] AS Skill,

    NS.[Skill ID],

    CONCAT(n.apellido, '' '', n.nombre) AS [Nombre de Agente],

    t.[Duración del Ring] AS [Duración Ring],

    t.[Tiempo Hablado] AS [Duración Talk],

    t.[Duración del Hold] AS [Duración Hold],

    0 AS [Duración ACW],

    ISNULL(t.[Duración del Ring], 0)
        + ISNULL(t.[Tiempo Hablado], 0)
        + ISNULL(t.[Duración del Hold], 0)
        AS [Tiempo Total],

    t.[Inicio de transferencia] AS [Fecha de Agente],

    CONCAT(eq.apellido, '' '', eq.nombre) AS Equipo,

    CONVERT(
        TIME,
        DATEADD(
            MINUTE,
            DATEDIFF(
                MINUTE,
                0,
                t.[Inicio de transferencia]
            ) / 30 * 30,
            0
        )
    ) AS Intervalo,

    TMO.Valor AS TMO_Objetivo

FROM [Acme].[dbo].[Voltara Enerval transferencias internas] AS t

INNER JOIN dbo.usuarios AS u
    ON t.[Agente de Destino] COLLATE SQL_Latin1_General_CP1_CS_AS
       = u.usuario COLLATE SQL_Latin1_General_CP1_CS_AS

INNER JOIN dbo.nomina AS n
    ON n.id = u.nomina_id

INNER JOIN
(
    SELECT 
        o.id,
        o.legajo_id,
        o.estado,
        o.hora_ingreso,
        o.hora_salida,
        o.franco_1,
        o.franco_2,
        o.puesto_id,
        o.campana_id,
        o.equipo_id,
        o.sitio_id,
        o.fecha_desde,
        o.fecha_hasta

    FROM dbo.operadores AS o

    INNER JOIN dbo.campanas AS c
        ON c.id = o.campana_id

    WHERE c.cliente = ''VOLTARA''
      AND o.puesto_id IN (1, 2, 3, 4)

) AS o
    ON n.id = o.legajo_id
    AND CONVERT(date, t.[Inicio de transferencia]) >= o.fecha_desde
    AND (
        CONVERT(date, t.[Inicio de transferencia]) < o.fecha_hasta
        OR o.fecha_hasta IS NULL
    )

INNER JOIN dbo.equipos AS eq
    ON eq.id = o.equipo_id

INNER JOIN dbo.[Voltara normalizador por Skill] AS NS
    ON NS.Skill = t.[Intención de Destino]

LEFT JOIN dbo.[Voltara TMO por Skill] AS TMO
    ON TMO.[Skill ID] = NS.[Skill ID]
    AND TMO.Fecha = CONVERT(date, t.[Inicio de transferencia])
    AND TMO.Hora = CONVERT(
        TIME,
        DATEADD(
            MINUTE,
            DATEDIFF(
                MINUTE,
                0,
                t.[Inicio de transferencia]
            ) / 30 * 30,
            0
        )
    )

WHERE t.[Intención de Destino] = ''Comercial-Consumo''
' 
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPane1' , N'SCHEMA',N'dbo', N'VIEW',N'TMO_Voltara', NULL,NULL))
	EXEC sys.sp_addextendedproperty @name=N'MS_DiagramPane1', @value=N'[0E232FF0-B466-11cf-A24F-00AA00A3EFFF, 1.00]
Begin DesignProperties = 
   Begin PaneConfigurations = 
      Begin PaneConfiguration = 0
         NumPanes = 4
         Configuration = "(H (1[41] 4[20] 2[22] 3) )"
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
               Right = 314
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
         Begin Table = "o_1"
            Begin Extent = 
               Top = 490
               Left = 48
               Bottom = 653
               Right = 242
            End
            DisplayFlags = 280
            TopColumn = 0
         End
         Begin Table = "eq"
            Begin Extent = 
               Top = 658
               Left = 48
               Bottom = 821
               Right = 242
            End
            DisplayFlags = 280
            TopColumn = 0
         End
         Begin Table = "nor"
            Begin Extent = 
               Top = 826
               Left = 48
               Bottom = 945
               Right = 242
            End
            DisplayFlags = 280
            TopColumn = 0
         End
         Begin Table = "NS"
            Begin Extent = 
               Top = 945
               Left = 48
               Bottom = 1064
               Right = 242
            End
            DisplayFlags = 280
            TopColumn = 0
         ' , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'TMO_Voltara'
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPane2' , N'SCHEMA',N'dbo', N'VIEW',N'TMO_Voltara', NULL,NULL))
	EXEC sys.sp_addextendedproperty @name=N'MS_DiagramPane2', @value=N'End
         Begin Table = "TMO"
            Begin Extent = 
               Top = 1064
               Left = 48
               Bottom = 1227
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
' , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'TMO_Voltara'
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPaneCount' , N'SCHEMA',N'dbo', N'VIEW',N'TMO_Voltara', NULL,NULL))
	EXEC sys.sp_addextendedproperty @name=N'MS_DiagramPaneCount', @value=2 , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'TMO_Voltara'
GO
