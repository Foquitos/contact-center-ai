-- View [dbo].[Tecnomax_AHT]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[dbo].[Tecnomax_AHT]'))
EXEC dbo.sp_executesql @statement = N'create view [dbo].[Tecnomax_AHT] as

SELECT CONVERT(VARCHAR(8), DATEADD(SECOND, DURACIÓN, 0), 108) AS DURACIÓN, CONVERT(VARCHAR(8), DATEADD(SECOND, d.[Tiempo Tarifado], 0), 108) AS [TIEMPO TARIFADO], CONVERT(VARCHAR(8), DATEADD(SECOND, d.Preview, 
                  0), 108) AS PREVIEW, CONVERT(VARCHAR(8), DATEADD(SECOND, d.Dialing, 0), 108) AS DIALING, CONVERT(VARCHAR(8), DATEADD(SECOND, d.Ringing, 0), 108) AS RINGING, CONVERT(VARCHAR(8), DATEADD(SECOND, d.TalkingTime, 0), 
                  108) AS TALKINGTIME, CONVERT(VARCHAR(8), DATEADD(SECOND, d.Hold, 0), 108) AS Hold, CONVERT(VARCHAR(8), DATEADD(SECOND, d.ACW, 0), 108) AS ACW, d.Derivada, d.Atendidas, d.[Agente No Atendidas], d.Abandonada, 
                  e.apellido + '' '' + e.nombre AS Supervisor, n.nombre + '' '' + n.apellido AS [Nombre Agente], n.fecha_piso AS [Fecha ingreso], RIGHT(''0'' + CONVERT(VARCHAR(2), DATEPART(HOUR, d.Inicio)), 2) + '':00:00'' AS INTERVALO, CAST(d.Inicio AS time) 
                  AS Inicio2, CONVERT(VARCHAR(8), DATEADD(SECOND, d.Preview + d.Dialing + d.Ringing + d.TalkingTime + d.Hold, 0), 108) AS [Tiempo AHT sin ACW], 1 AS Llamadas
FROM     dbo.detalle_de_interacciones_por_agente AS d INNER JOIN
                  dbo.usuarios AS u ON d.LoginId = u.usuario INNER JOIN
                  dbo.nomina AS n ON n.id = u.nomina_id INNER JOIN
                  dbo.operadores AS o ON o.legajo_id = n.id AND d.Inicio >= o.fecha_desde AND (d.Inicio < o.fecha_hasta OR
                  o.fecha_hasta IS NULL) INNER JOIN
                  dbo.equipos AS e ON o.equipo_id = e.id
WHERE  (d.Empresa = ''Tecnomax'') AND (d.fecha_inicio = CAST(GETDATE() AS Date)) AND (d.Sentido = ''entrante'') AND (d.Agente <> ''Campos Daniela Estela'')' 
GO
