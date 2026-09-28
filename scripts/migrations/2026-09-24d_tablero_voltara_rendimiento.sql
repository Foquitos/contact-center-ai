/* ============================================================================
   Rendimiento — vista dbo.Tablero_Voltara (índices + reescritura)
   Fecha: 2026-09-24
   Autor: equipo Acme

   POR QUÉ TARDA
   -------------
   Ninguna de las tablas que lee la vista tiene un índice que sirva para el
   cruce, así que cada consulta las lee enteras:

     tabla                                   filas    índice útil antes
     dbo.[Voltara informe IVR]                5,6 M    ninguno ([Fecha de Agente] no está indexada)
     dbo.[Voltara Enerval informe skills]        303 k    ninguno (solo PK por id)
     dbo.Forecast                            294 k    ninguno (solo PK por id)
     dbo.[Voltara TMO por Skill]              271 k    ninguno (heap, sin PK)

   Lo más caro es la subconsulta de Cortas/Express/Normales: filtra el mes con
   MONTH(...) = MONTH(GETDATE()) AND YEAR(...) = YEAR(GETDATE()), que no puede
   usar un índice aunque exista, y termina recorriendo los 1,4 GB de la tabla
   del IVR (varchar(max) en casi todas las columnas) para quedarse con ~90 k
   filas del mes.

   QUÉ HACE
   --------
   1. Cuatro índices no agrupados, cubrientes para lo que la vista lee.
   2. Reescribe la vista (CREATE OR ALTER: conserva los permisos):
      - El mes corriente se filtra por rango ([Fecha de Agente] >= día 1 y
        < día 1 del mes siguiente): mismo resultado, pero busca en el índice.
      - Forecast y TMO objetivo se agregan antes de cruzar (ver "CAMBIA EL
        NÚMERO").
      - El cruce con dbo.usuarios pasa a EXISTS (ver "CAMBIA EL NÚMERO").
      Las columnas, sus nombres y tipos quedan iguales.

   CAMBIA EL NÚMERO (corrige dos conteos inflados)
   ----------------------------------------------
   a) dbo.usuarios tiene 1.455 usuarios repetidos. El INNER JOIN contaba dos
      veces cada llamada de esos operadores en Cortas, Express y Normales. Con
      EXISTS cuenta una. En Voltara los que pegan son operadores cargados dos
      veces con el mismo nomina_id (p. ej. AR10000005, ids 121675 y 135314):
      el 23/09/2026 fueron 4 operadores y 132 llamadas. Medido ese día:
      Cortas 46 -> 43, Express 925 -> 900, Normales 3.669 -> 3.565 (-2,8%).
      El resto de las columnas dio idéntico ese día (624 filas).
   b) Si Forecast o [Voltara TMO por Skill] tienen dos filas para el mismo
      intervalo y skill, el LEFT JOIN duplicaba la fila del informe y TODAS
      las sumas (Completadas, Total, Logueado...) salían al doble. Hoy pasa en
      1.152 intervalos del TMO (hasta 27/07/2026) y en 1.436 del Forecast de
      octubre 2026 (se iba a notar el 1/10). Ahora se suman antes de cruzar:
      Forecast y TMO objetivo valen lo mismo que antes; las demás columnas ya
      no se duplican.

   Queda sin tocar: 506 pares (Intervalo, Skill ID) repetidos en
   dbo.[Voltara Enerval informe skills] entre 24/10/2025 y 06/07/2026 (cargas
   repetidas del informe: es un problema del dato, no de la vista).

   CÓMO APLICARLO
   --------------
   SQL Server 2017 Standard: sin ONLINE, así que crear los índices bloquea la
   escritura de cada tabla mientras se construye (el del IVR es el largo, del
   orden de un minuto). Correrlo fuera del horario de carga del informe IVR.
   Idempotente: se puede correr dos veces.

   ROLLBACK
   --------
   Recrear la vista con scripts/esquema/dbo.Tablero_Voltara.View.sql (como
   ALTER VIEW) y DROP INDEX de los cuatro índices de abajo.
   ============================================================================ */

SET NOCOUNT ON;
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
SET ANSI_PADDING ON;
GO

/* ---- 1. Índices -------------------------------------------------------- */

-- Subconsulta del mes: rango sobre [Fecha de Agente] + lo que se suma y cruza.
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE object_id = OBJECT_ID(N'dbo.[Voltara informe IVR]')
                 AND name = N'IX_VoltaraInformeIVR_FechaAgente')
    CREATE NONCLUSTERED INDEX IX_VoltaraInformeIVR_FechaAgente
        ON dbo.[Voltara informe IVR] ([Fecha de Agente])
        INCLUDE (Skill, Agente, [Duración Ring], [Duración Talk], [Duración Hold], [Duración ACW])
        WITH (SORT_IN_TEMPDB = ON);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE object_id = OBJECT_ID(N'dbo.[Voltara Enerval informe skills]')
                 AND name = N'IX_VoltaraEnervalInformeSkills_Intervalo_Skill')
    CREATE NONCLUSTERED INDEX IX_VoltaraEnervalInformeSkills_Intervalo_Skill
        ON dbo.[Voltara Enerval informe skills] (Intervalo, [Skill ID])
        INCLUDE ([Volumen de llamadas respondidas], [Llamadas transferidas],
                 [Volumen de llamadas abandonadas], [Volumen de llamadas entrantes],
                 [Contestadas Umbral], TMO, TME, [Agentes Logueados],
                 [Tiempo Agentes Logueados], [Tiempo Agentes en pausa], [% Ocupacion])
        WITH (SORT_IN_TEMPDB = ON);
GO

-- Campaña es varchar(max): no puede ser clave, va incluida.
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE object_id = OBJECT_ID(N'dbo.Forecast')
                 AND name = N'IX_Forecast_Fecha_Intervalo_Skill')
    CREATE NONCLUSTERED INDEX IX_Forecast_Fecha_Intervalo_Skill
        ON dbo.Forecast (Fecha, Intervalo, [Skill ID])
        INCLUDE (Forecast, Campaña)
        WITH (SORT_IN_TEMPDB = ON);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE object_id = OBJECT_ID(N'dbo.[Voltara TMO por Skill]')
                 AND name = N'IX_VoltaraTMOporSkill_Fecha_Hora_Skill')
    CREATE NONCLUSTERED INDEX IX_VoltaraTMOporSkill_Fecha_Hora_Skill
        ON dbo.[Voltara TMO por Skill] (Fecha, Hora, [Skill ID])
        INCLUDE (Valor)
        WITH (SORT_IN_TEMPDB = ON);
GO

/* ---- 2. Vista ---------------------------------------------------------- */

CREATE OR ALTER VIEW dbo.Tablero_Voltara
AS
SELECT e.Intervalo AS [Fecha/Horas], CAST(e.Intervalo AS date) AS Fecha, CAST(e.Intervalo AS time) AS Horas, e.[Skill ID], n.Skill,
       SUM(e.[Volumen de llamadas respondidas]) AS Completadas,
       SUM(e.[Llamadas transferidas]) AS Transferidas,
       SUM(e.[Volumen de llamadas abandonadas]) AS Abandonadas,
       SUM(e.[Volumen de llamadas entrantes]) AS Total,
       SUM(e.[Contestadas Umbral]) AS [Atendidas antes de Umbral],
       AVG(e.TMO) AS [TMO Promedio],
       SUM(CAST(e.[Volumen de llamadas respondidas] AS FLOAT) * CAST(e.TMO AS FLOAT) / 86400.0) AS [Tiempo Hablado],
       AVG(CAST(e.TME AS FLOAT) / 86400.0) AS ASA,
       SUM(f.Forecast) AS Forecast,
       SUM(ISNULL(ll.Cortas, 0)) AS Cortas,
       SUM(ISNULL(ll.Express, 0)) AS Express,
       SUM(ISNULL(ll.Normales, 0)) AS Normales,
       SUM(e.[Agentes Logueados]) AS Cant_op,
       SUM(CAST(e.[Tiempo Agentes Logueados] AS FLOAT) / 3600.0) AS Logueado,
       SUM(CAST(e.[Tiempo Agentes en pausa] AS FLOAT) / 3600.0) AS [Tiempo en pausa],
       ROUND(AVG(CAST(e.[% Ocupacion] AS FLOAT)), 2) AS [% Ocupacion],
       MAX(ns.Facturacion) AS TipoSkill,
       MAX(ns.CPH) AS CPH,
       SUM(ISNULL(tmo.Valor, 0)) AS [TMO objetivo]
FROM dbo.[Voltara Enerval informe skills] AS e
INNER JOIN dbo.[Voltara normalizador por Skill] AS n ON e.[Skill ID] = n.[Skill ID]
-- Forecast y TMO se agregan antes de cruzar: una fila repetida en esas
-- tablas duplicaba la fila del informe y todas las sumas.
LEFT OUTER JOIN (
    SELECT Fecha, Intervalo, [Skill ID], SUM(Forecast) AS Forecast
    FROM dbo.Forecast
    WHERE Campaña = 'Voltara'
    GROUP BY Fecha, Intervalo, [Skill ID]
) AS f ON f.Fecha = CAST(e.Intervalo AS date) AND f.Intervalo = CAST(e.Intervalo AS time) AND f.[Skill ID] = n.[Skill ID]
LEFT OUTER JOIN dbo.[Voltara Enerval normalizador cph skill] AS ns ON n.[Skill ID] = ns.[Skill ID]
LEFT OUTER JOIN (
    SELECT Fecha, Hora, [Skill ID], SUM(Valor) AS Valor
    FROM dbo.[Voltara TMO por Skill]
    GROUP BY Fecha, Hora, [Skill ID]
) AS tmo ON tmo.[Skill ID] = e.[Skill ID] AND tmo.Fecha = CAST(e.Intervalo AS date) AND tmo.Hora = CAST(e.Intervalo AS time)
LEFT OUTER JOIN (
    -- Llamadas del mes corriente por media hora y skill, según duración total.
    SELECT CAST(ivr.[Fecha de Agente] AS date) AS Fecha, DATEPART(HOUR, ivr.[Fecha de Agente]) AS Hora,
           DATEPART(MINUTE, ivr.[Fecha de Agente]) / 30 * 30 AS Minuto, n.[Skill ID],
           SUM(CASE WHEN d.Duracion <= 30 THEN 1 ELSE 0 END) AS Cortas,
           SUM(CASE WHEN d.Duracion > 30 AND d.Duracion <= CASE ns.Facturacion WHEN 'Simple' THEN 100 WHEN 'Compleja' THEN 200 END
                    THEN 1 ELSE 0 END) AS Express,
           SUM(CASE WHEN d.Duracion > CASE ns.Facturacion WHEN 'Simple' THEN 100 WHEN 'Compleja' THEN 200 END
                    THEN 1 ELSE 0 END) AS Normales
    FROM dbo.[Voltara informe IVR] AS ivr
    CROSS APPLY (SELECT ivr.[Duración Ring] + ivr.[Duración Talk] + ivr.[Duración Hold] + ivr.[Duración ACW] AS Duracion) AS d
    INNER JOIN dbo.[Voltara normalizador por Skill] AS n ON ivr.Skill = n.Skill
    INNER JOIN dbo.[Voltara Enerval normalizador cph skill] AS ns ON n.[Skill ID] = ns.[Skill ID]
    -- Rango (no MONTH()/YEAR()) para que use IX_VoltaraInformeIVR_FechaAgente.
    WHERE ivr.[Fecha de Agente] >= DATEADD(MONTH, DATEDIFF(MONTH, 0, GETDATE()), 0)
      AND ivr.[Fecha de Agente] <  DATEADD(MONTH, DATEDIFF(MONTH, 0, GETDATE()) + 1, 0)
      -- EXISTS y no JOIN: dbo.usuarios repite usuarios y el JOIN contaba doble.
      AND EXISTS (SELECT 1 FROM dbo.usuarios AS u WHERE u.usuario = ivr.Agente)
    GROUP BY CAST(ivr.[Fecha de Agente] AS date), DATEPART(HOUR, ivr.[Fecha de Agente]),
             DATEPART(MINUTE, ivr.[Fecha de Agente]) / 30 * 30, n.[Skill ID]
) AS ll ON CAST(e.Intervalo AS date) = ll.Fecha AND DATEPART(HOUR, e.Intervalo) = ll.Hora
       AND DATEPART(MINUTE, e.Intervalo) / 30 * 30 = ll.Minuto AND n.[Skill ID] = ll.[Skill ID]
WHERE e.Intervalo < DATEADD(MINUTE, DATEDIFF(MINUTE, 0, GETDATE()) / 30 * 30, 0)
GROUP BY e.Intervalo, CAST(e.Intervalo AS date), CAST(e.Intervalo AS time), e.[Skill ID], n.Skill;
GO
