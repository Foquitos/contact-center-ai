/* ============================================================================
   Feature — AuditExecutionLog: modelo de Gemini REAL usado en cada corrida
   Fecha: 2026-07-04
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   Columna `modelo` (NVARCHAR(80) NULL) a calidad.AuditExecutionLog (creada en
   2026-07-01_audit_execution_log.sql).

   Hasta ahora la pantalla de logs y el mail admin estimaban el costo resolviendo
   el modelo ACTUAL configurado en la plantilla (podría haber cambiado después de
   la corrida). Con esta columna, cada fila guarda el modelo con el que la corrida
   se generó de verdad:
     - sync:  Auditor.py::run() lo pasa al cerrar la fila (finalizar_ejecucion /
              finalizar_grupo), con el modelo que devolvió apply_auditoria_threads.
     - batch: gemini.py::process_batch() lo pasa al ABRIR la fila (ya lo resolvió
              para crear el job), y _procesar_grupo_batch lo reafirma al cerrar
              con el modelo que viajó en calidad.Batch_data.
   Para filas viejas (modelo NULL), el listado /uso-ia/logs sigue cayendo al
   modelo configurado hoy en la plantilla, como antes.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario con DDL sobre el schema calidad,
   DESPUÉS de 2026-07-01_audit_execution_log.sql (y junto con la de
   2026-07-02_audit_execution_log_muestreo_csv.sql si aún no se aplicó).
   Idempotente: la columna se agrega solo si no existe.
   ============================================================================ */

SET XACT_ABORT ON;
GO

IF COL_LENGTH('calidad.AuditExecutionLog', 'modelo') IS NULL
    ALTER TABLE calidad.AuditExecutionLog ADD modelo NVARCHAR(80) NULL;
GO
