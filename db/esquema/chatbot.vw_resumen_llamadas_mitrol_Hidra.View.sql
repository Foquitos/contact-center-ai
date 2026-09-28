-- View [chatbot].[vw_resumen_llamadas_mitrol_Hidra]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[chatbot].[vw_resumen_llamadas_mitrol_Hidra]'))
EXEC dbo.sp_executesql @statement = N'CREATE   VIEW [chatbot].[vw_resumen_llamadas_mitrol_Hidra]
AS
SELECT 
    idInteraccion,
    MIN(Inicio) AS fecha_hora_inicio,
    MAX([Tipo Contacto]) AS tipo_contacto,
    MAX(Sentido) AS sentido_llamada,
    MAX(Cliente) AS telefono_cliente,
    MAX(DNIS) AS numero_marcado_dnis,
    -- Métricas sumadas
    SUM(Duración) AS duracion_total_llamada_segundos,
    SUM([Tiempo Tarifado]) AS tiempo_tarifado_total_segundos,
    SUM(Ringing) AS tiempo_ringing_total_segundos,
    SUM(TalkingTime) AS tiempo_hablando_total_segundos,
    SUM(Hold) AS tiempo_espera_hold_total_segundos,
    SUM(EnCola) AS tiempo_en_cola_total_segundos,
    -- Banderas
    MAX(CAST(Atendidas AS INT)) AS llamada_fue_atendida,
    MAX(CAST(Abandonada AS INT)) AS llamada_fue_abandonada,
    MAX(CAST(TransferOut AS INT)) AS llamada_tuvo_transferencia
FROM dbo.detalle_de_interacciones_por_campana_lote
WHERE Empresa = ''Hidra'' and (Entrante = 1 or Derivada = 1 or Abandonada = 1)
GROUP BY idInteraccion;
' 
GO
