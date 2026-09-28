/* ============================================================================
   Feature — Un solo mail por corrida (y a los destinatarios que corresponde)
   Fecha: 2026-08-19
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   calidad.AuditExecutionLog.id_aplicativos — los IdAplicativo que auditó ESE lote,
   separados por coma.

   POR QUÉ
   -------
   El mail de resultado del modo Batch se mandaba en `_procesar_grupo_batch`, o sea
   UNO POR LOTE: una corrida de Vantix que se parte en 14 lotes mandaba 14 mails, cada
   uno con su pedacito de la auditoría. Ahora el mail se manda una sola vez, cuando
   cierra el último lote de la corrida (mismo `run_id`, ver la migración
   2026-08-18_run_id_audit_execution_log.sql), con TODAS las auditorías adjuntas.

   Para armar ese adjunto acumulado hay que reconsultar
   `calidad.sp_ObtenerAuditoriasFiltradas` por los IdAplicativo de la corrida entera,
   y los lotes pueden cerrar en pasadas distintas del scheduler (hasta en días
   distintos): no alcanza con lo que hay en memoria. Cada lote deja acá los suyos y el
   último los junta.

   `calidad.Batch_data` no sirve para esto: se borra en cuanto el lote se sella
   (sp_LimpiarDatosDeBatchFinalizados), que es exactamente el momento en que el
   siguiente lote todavía no terminó.

   NO HACE FALTA MIGRACIÓN PARA LOS DESTINATARIOS
   -----------------------------------------------
   El otro bug que se arregla en esta tanda —el mail iba solo al creador de la tarea y
   no a `AuditSchedulers.email_addresses`— se resuelve haciendo viajar los destinatarios
   con el lote en `calidad.Batch_data` (igual que gsheet_id / gsheet_name /
   column_template_id) y guardándolos en la columna `mail_destinatarios`, que ya existe.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario con DDL sobre el schema calidad.
   Idempotente. Aplicar DESPUÉS de 2026-08-18_run_id_audit_execution_log.sql y ANTES
   de deployar el backend (sin la columna, el cierre de corrida no puede juntar las
   auditorías y el mail saldría con el último lote solamente).
   ============================================================================ */

SET XACT_ABORT ON;
GO

IF COL_LENGTH('calidad.AuditExecutionLog', 'id_aplicativos') IS NULL
BEGIN
    ALTER TABLE calidad.AuditExecutionLog ADD id_aplicativos NVARCHAR(MAX) NULL;
    PRINT 'id_aplicativos agregada.';
END
ELSE
    PRINT 'id_aplicativos ya existía; no se toca.';
GO

/* ---------------------------------------------------------------------------
   Verificación (opcional): corridas de varios lotes y qué auditó cada uno.
   ---------------------------------------------------------------------------
SELECT run_id, COUNT(*) AS lotes, MIN(scheduler_name) AS tarea,
       SUM(filas_auditadas) AS auditadas, MAX(mail_destinatarios) AS destinatarios
FROM calidad.AuditExecutionLog
WHERE started_at >= DATEADD(DAY, -7, SYSUTCDATETIME())
GROUP BY run_id
HAVING COUNT(*) > 1
ORDER BY MIN(started_at) DESC;
   --------------------------------------------------------------------------- */
