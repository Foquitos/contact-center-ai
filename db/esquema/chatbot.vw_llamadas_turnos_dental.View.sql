-- View [chatbot].[vw_llamadas_turnos_dental]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[chatbot].[vw_llamadas_turnos_dental]'))
EXEC dbo.sp_executesql @statement = N'
-- -----------------------------------------------------------------------------
-- 3. Cruce interacción -> turno agendado, con canal (conversión por canal).
-- -----------------------------------------------------------------------------
CREATE   VIEW [chatbot].[vw_llamadas_turnos_dental] AS
WITH LlamadasDental AS (
    SELECT
        d.idInteraccion, d.segmento, d.Empresa, d.Campaña, d.LoginId,
        d.Tipificación, d.inicio, d.Sentido, d.Duración,
        CASE
            WHEN d.Empresa = ''Facebook''         THEN ''Chat Facebook''
            WHEN d.Campaña LIKE ''%whatsapp%''    THEN ''Chat WhatsApp''
            WHEN d.Campaña LIKE ''%encuesta%''    THEN ''Encuesta Telefonica''
            ELSE ''Telefono''
        END AS canal,
        LEAD(d.inicio) OVER (PARTITION BY d.LoginId ORDER BY d.inicio) AS SiguienteInicio
    FROM [Acme].[dbo].[detalle_de_interacciones_por_campana_lote] d
    WHERE d.Empresa IN (''Odonto Plus'', ''Facebook'')
      AND d.Campaña NOT IN (''prueba_break_in'', ''test_break'')
      AND d.Campaña NOT LIKE ''%projub%''
)
SELECT
    l.idInteraccion,
    l.segmento,
    l.Empresa        AS empresa,
    l.Campaña        AS campana,
    l.canal          AS canal,
    l.LoginId        AS usuario_agente,
    n.nombre         AS nombre_agente,
    n.apellido       AS apellido_agente,
    l.inicio         AS fecha_hora_inicio,
    l.Sentido        AS sentido_llamada,
    l.Tipificación   AS tipificacion_arbol,
    l.Duración       AS duracion_segundos,
    Turno.IdTurno    AS id_turno,
    Turno.IdClinica  AS id_clinica,
    Turno.FechaHora  AS fecha_turno,
    Turno.esprimeravez AS es_primera_vez,
    Turno.FechaAlta  AS fecha_agendamiento,
    CASE WHEN Turno.IdTurno IS NOT NULL THEN 1 ELSE 0 END AS agendo_turno
FROM LlamadasDental l
LEFT JOIN [Acme].[dbo].[usuarios] u_tel ON l.LoginId = u_tel.usuario
LEFT JOIN [Acme].[dbo].[nomina]   n     ON u_tel.nomina_id = n.id
OUTER APPLY (
    SELECT TOP 1 dt.IdTurno, dt.IdClinica, dt.FechaHora, dt.esprimeravez, dt.FechaAlta
    FROM [Acme].[dbo].[Odonto_Plus_Turnero] dt
    INNER JOIN [Acme].[dbo].[usuarios] u_crm ON dt.operador = u_crm.usuario
    WHERE u_crm.nomina_id = u_tel.nomina_id
      AND dt.FechaAlta > DATEADD(SECOND, 30, l.inicio)
      AND (l.SiguienteInicio IS NULL OR dt.FechaAlta < DATEADD(SECOND, 15, l.SiguienteInicio))
    ORDER BY dt.FechaAlta ASC
) AS Turno;
' 
GO
