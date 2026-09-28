-- View [chatbot].[vw_turnos_dental]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[chatbot].[vw_turnos_dental]'))
EXEC dbo.sp_executesql @statement = N'
-- -----------------------------------------------------------------------------
-- 5. Odonto Plus: turnos del turnero con datos del operador que lo agendó.
--    Un registro = un turno. fecha_agendamiento (FechaAlta) es cuándo se cargó;
--    fecha_turno (FechaHora) es cuándo se atiende al paciente.
-- -----------------------------------------------------------------------------
CREATE   VIEW [chatbot].[vw_turnos_dental] AS
SELECT
    dt.IdTurno        AS id_turno,
    dt.IdClinica      AS id_clinica,
    dt.FechaAlta      AS fecha_agendamiento,
    dt.FechaHora      AS fecha_turno,
    dt.esprimeravez   AS es_primera_vez,
    dt.email          AS email,
    dt.celular        AS celular,
    dt.Observaciones  AS observaciones,
    dt.operador       AS usuario_crm,
    n.nombre          AS nombre_agente,
    n.apellido        AS apellido_agente
FROM [Acme].[dbo].[Odonto_Plus_Turnero] dt
LEFT JOIN [Acme].[dbo].[usuarios] u ON dt.operador = u.usuario
LEFT JOIN [Acme].[dbo].[nomina]   n ON u.nomina_id = n.id;
' 
GO
