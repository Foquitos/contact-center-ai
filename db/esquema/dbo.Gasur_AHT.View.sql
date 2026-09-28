-- View [dbo].[Gasur_AHT]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[dbo].[Gasur_AHT]'))
EXEC dbo.sp_executesql @statement = N'CREATE view [dbo].[Gasur_AHT] as
SELECT   l.Id,
l.Fecha,
l.Cola,
l.Orígen,
l.Destino,
l.[Espera (seg.)],
l.[Duración (seg.)],
l.Tipo,
l.Estado,
l.Agente,
l.Localidad,
l.Intentos,
n.apellido + '' '' + n.nombre AS Asesor,
n.fecha_piso,
CAST(DATEADD(Hour, DATEPART(Hour, l.Fecha), ''00:00:00'') AS time) AS intervalo
FROM         dbo.[Gasur Llamadas] AS l 
INNER JOIN dbo.usuarios AS u ON u.usuario = SUBSTRING(l.Agente, CHARINDEX(''('', l.Agente) + 1, LEN(l.Agente) - CHARINDEX(''('', l.Agente) - 1) 
INNER JOIN dbo.nomina AS n ON n.id = u.nomina_id
INNER JOIN operadores o ON n.id = o.legajo_id AND l.Fecha>= o.fecha_desde AND (l.Fecha <= o.fecha_hasta OR o.fecha_hasta IS NULL)
WHERE     (CAST(l.Fecha AS date) = CAST(GETDATE() AS date)) and o.campana_id = 121 and o.estado = 1 and o.puesto_id in (2,3,4,5)' 
GO
