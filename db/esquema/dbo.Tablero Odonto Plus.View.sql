-- View [dbo].[Tablero Odonto Plus]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[dbo].[Tablero Odonto Plus]'))
EXEC dbo.sp_executesql @statement = N'CREATE VIEW [dbo].[Tablero Odonto Plus]
AS
WITH CampañasEmpresa AS (
SELECT *
FROM (VALUES
(''DentalPrioridad'',''TELÉFONO''),
(''DentalNoPrioridad'',''TELÉFONO''),
(''Chat IN Facebook'',''Facebook''),
(''IN Facebook'',''Facebook''),
(''Transferencias Facebook'',''Facebook''),
(''WhatsappIn'',''Whatsapp''),
(''Transferencias_inbound_WhatsApp'',''Odonto Plus Leadaki'')
) t(Campaña,Empresa)
),

SLA AS (
SELECT
CAST(e.Fecha AS DATE) Fecha,
DATEADD(HOUR,DATEDIFF(HOUR,0,e.Fecha),0) Intervalo,
ce.Empresa,
SUM(e.Entrantes) Entrantes,
SUM(e.Atendidas) Atendidas,
SUM(e.AtAntesUmbral) Atendidas_Antes_Umbral
FROM eficiencia_de_campana e
JOIN campanas_empresa_mitrol camem
 ON camem.idCampania=e.idCampania
JOIN CampañasEmpresa ce
 ON ce.Campaña=camem.Campaña
WHERE e.Fecha>=''2026-02-01''
AND e.Fecha<DATEADD(DAY,1,CAST(GETDATE() AS DATE))
AND (
 ce.Empresa<>''Whatsapp''
 OR camem.idCampania=441
)
GROUP BY
CAST(e.Fecha AS DATE),
DATEADD(HOUR,DATEDIFF(HOUR,0,e.Fecha),0),
ce.Empresa
),

PlanOperadores AS (
SELECT
CAST(i.Interval_Start AS DATE) Fecha,
DATEADD(HOUR,DATEDIFF(HOUR,0,i.Interval_Start),0) Intervalo,
c.sub_campana Empresa,
COUNT(DISTINCT p.id_operadores) Agentes_Planificados
FROM payroll_futuro p
JOIN operadores o
 ON p.id_operadores=o.id
JOIN campanas c
 ON c.id=o.campana_id
JOIN Intervalos i
 ON CAST(i.Interval_Start AS DATE)=p.fecha
AND CAST(i.Interval_Start AS TIME)>=p.inicio
AND CAST(i.Interval_Start AS TIME)<p.final
WHERE p.fecha>=''2026-02-01''
AND p.horas_programadas>0
AND DATEPART(MINUTE,i.Interval_Start)=0
GROUP BY
CAST(i.Interval_Start AS DATE),
DATEADD(HOUR,DATEDIFF(HOUR,0,i.Interval_Start),0),
c.sub_campana
),

CORTAS AS (
SELECT
CAST(d.Inicio AS DATE) Fecha,
DATEADD(HOUR,DATEDIFF(HOUR,0,d.Inicio),0) Intervalo,
ce.Empresa,
COUNT(*) Cortas
FROM detalle_de_interacciones_por_agente d
JOIN campanas_empresa_mitrol camem
 ON camem.idCampania=d.idCampania
JOIN CampañasEmpresa ce
 ON ce.Campaña=camem.Campaña
WHERE Preview+Dialing+Ringing+TalkingTime+Hold+ACW<=10
AND Atendidas=1
AND d.Inicio>=''2026-02-01''
GROUP BY
CAST(d.Inicio AS DATE),
DATEADD(HOUR,DATEDIFF(HOUR,0,d.Inicio),0),
ce.Empresa
),

TiempoAtencion AS (
SELECT
CAST(d.Inicio AS DATE) Fecha,
DATEADD(HOUR,DATEDIFF(HOUR,0,d.Inicio),0) Intervalo,
ce.Empresa,
SUM(
ISNULL(PREVIEW,0)+
ISNULL(DIALING,0)+
ISNULL(RINGING,0)+
ISNULL(TALKINGTIME,0)+
ISNULL(HOLD,0)+
ISNULL(ACW,0)
) TiempoAtencion_Segundos
FROM detalle_de_interacciones_por_agente d
JOIN campanas_empresa_mitrol camem
 ON camem.idCampania=d.idCampania
JOIN CampañasEmpresa ce
 ON ce.Campaña=camem.Campaña
WHERE d.Atendidas=1
AND d.Sentido=''Entrante''
AND d.Inicio>=''2026-02-01''
GROUP BY
CAST(d.Inicio AS DATE),
DATEADD(HOUR,DATEDIFF(HOUR,0,d.Inicio),0),
ce.Empresa
),

TiempoEnCola AS (
SELECT
CAST(d.Inicio AS DATE) Fecha,
DATEADD(HOUR,DATEDIFF(HOUR,0,d.Inicio),0) Intervalo,
ce.Empresa,
SUM(ISNULL(d.EnCola,0)) TiempoEnCola_Segundos
FROM detalle_de_interacciones_por_agente d
JOIN campanas_empresa_mitrol camem
 ON camem.idCampania=d.idCampania
JOIN CampañasEmpresa ce
 ON ce.Campaña=camem.Campaña
WHERE d.Atendidas=1
AND d.Sentido=''Entrante''
AND d.Inicio>=''2026-02-01''
GROUP BY
CAST(d.Inicio AS DATE),
DATEADD(HOUR,DATEDIFF(HOUR,0,d.Inicio),0),
ce.Empresa
),

Conectados AS (
SELECT
CAST(Intervalo AS DATE) Fecha,
DATEADD(HOUR,DATEDIFF(HOUR,0,Intervalo),0) Intervalo,

CASE
WHEN NombreCampania=''Requerido Facebook'' THEN ''Facebook''
WHEN NombreCampania=''Requerido Redes'' THEN ''Whatsapp''
WHEN NombreCampania=''Requerido Telefono'' THEN ''TELÉFONO''
WHEN NombreCampania=''Requerido Leadaki'' THEN ''Odonto Plus Leadaki''
WHEN NombreCampania IN (
''Requerido Eventualidades'',
''Requerido Confirmación'',
''Requerido Reprogramación''
) THEN ''Odonto Plus RRSS''
END Empresa,

SUM(Login) TpoLogueo_Segundos,
SUM(CAST(Login AS FLOAT)/3600) OperadoresConectados

FROM Acumuladores_de_campana
WHERE Intervalo >= ''2026-02-01''
GROUP BY
CAST(Intervalo AS DATE),
DATEADD(HOUR,DATEDIFF(HOUR,0,Intervalo),0),

CASE
WHEN NombreCampania=''Requerido Facebook'' THEN ''Facebook''
WHEN NombreCampania=''Requerido Redes'' THEN ''Whatsapp''
WHEN NombreCampania=''Requerido Telefono'' THEN ''TELÉFONO''
WHEN NombreCampania=''Requerido Leadaki'' THEN ''Odonto Plus Leadaki''
WHEN NombreCampania IN (
''Requerido Eventualidades'',
''Requerido Confirmación'',
''Requerido Reprogramación''
) THEN ''Odonto Plus RRSS''
END
),

ForecastCTE AS (
SELECT
CAST(f.Fecha AS DATE) Fecha,
DATEADD(HOUR,DATEDIFF(HOUR,0,
CAST(f.Fecha AS DATETIME)+CAST(f.Intervalo AS DATETIME)
),0) Intervalo,

CASE
WHEN [Skill ID]=1 THEN ''Whatsapp''
WHEN [Skill ID]=2 THEN ''TELÉFONO''
END Empresa,

SUM(f.Forecast) Forecast

FROM Acme.dbo.Forecast f
WHERE f.Campaña=''Odonto plus''
AND f.Fecha>=''2026-02-01''
AND [Skill ID] IN (1,2)

GROUP BY
CAST(f.Fecha AS DATE),
DATEADD(HOUR,DATEDIFF(HOUR,0,
CAST(f.Fecha AS DATETIME)+CAST(f.Intervalo AS DATETIME)
),0),

CASE
WHEN [Skill ID]=1 THEN ''Whatsapp''
WHEN [Skill ID]=2 THEN ''TELÉFONO''
END
)

SELECT

s.Fecha,
s.Intervalo,
s.Empresa,

ISNULL(p.Agentes_Planificados,0) Agentes_Planificados,
ISNULL(con.OperadoresConectados,0) OperadoresConectados,

s.Entrantes,
s.Atendidas,

CASE
WHEN s.Empresa=''Whatsapp'' THEN 0
WHEN s.Entrantes-s.Atendidas<0 THEN 0
ELSE s.Entrantes-s.Atendidas
END Abandonadas,

s.Atendidas_Antes_Umbral,

ISNULL(c.Cortas,0) Cortas,
ISNULL(fc.Forecast,0) Forecast,
ISNULL(con.TpoLogueo_Segundos,0) TpoLogueo_Segundos,
ISNULL(tec.TiempoEnCola_Segundos,0) TiempoEnCola_Segundos,
ISNULL(ta.TiempoAtencion_Segundos,0) TiempoAtencion_Segundos

FROM SLA s

LEFT JOIN PlanOperadores p
ON s.Empresa=p.Empresa
AND s.Fecha=p.Fecha
AND s.Intervalo=p.Intervalo

LEFT JOIN CORTAS c
ON s.Empresa=c.Empresa
AND s.Fecha=c.Fecha
AND s.Intervalo=c.Intervalo

LEFT JOIN Conectados con
ON s.Empresa=con.Empresa
AND s.Fecha=con.Fecha
AND s.Intervalo=con.Intervalo

LEFT JOIN TiempoAtencion ta
ON s.Empresa=ta.Empresa
AND s.Fecha=ta.Fecha
AND s.Intervalo=ta.Intervalo

LEFT JOIN TiempoEnCola tec
ON s.Empresa=tec.Empresa
AND s.Fecha=tec.Fecha
AND s.Intervalo=tec.Intervalo

LEFT JOIN ForecastCTE fc
ON s.Empresa=fc.Empresa
AND s.Fecha=fc.Fecha
AND s.Intervalo=fc.Intervalo



' 
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPane1' , N'SCHEMA',N'dbo', N'VIEW',N'Tablero Odonto Plus', NULL,NULL))
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
' , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'Tablero Odonto Plus'
GO
IF NOT EXISTS (SELECT * FROM sys.fn_listextendedproperty(N'MS_DiagramPaneCount' , N'SCHEMA',N'dbo', N'VIEW',N'Tablero Odonto Plus', NULL,NULL))
	EXEC sys.sp_addextendedproperty @name=N'MS_DiagramPaneCount', @value=1 , @level0type=N'SCHEMA',@level0name=N'dbo', @level1type=N'VIEW',@level1name=N'Tablero Odonto Plus'
GO
