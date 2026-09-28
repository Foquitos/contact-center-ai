/* ============================================================================
   Feature — AuditExecutionLog: muestreo por operador/tipificación + agrupado de CSV
   Fecha: 2026-07-02
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   Cuatro columnas nuevas a calidad.AuditExecutionLog (creada en
   2026-07-01_audit_execution_log.sql):

   1) por_operador / por_tipificacion (BIT) + desglose_muestreo (NVARCHAR(MAX),
      JSON libre {"operadores": {...}, "tipificaciones": {...}}): cuando la
      corrida usa muestreo por operador y/o tipificación, `cantidad` deja de ser
      el total y pasa a ser "cantidad POR CADA grupo" (ver
      AuditorIA/SQL_query.py::construir_query_muestreo, PARTITION BY). Sin esto,
      `cantidad_solicitada` mostraba el valor crudo del form (p.ej. 5) en vez del
      total real (5 × N operadores/tipificaciones encontrados). Lo puebla
      Auditor.py::run() al finalizar, con el desglose real por grupo.

   2) upload_group_id (NVARCHAR(100)): agrupa las tandas de una misma subida CSV.
      El frontend (Auditoria.html, empresa "CSV") sube los audios en tandas de 10
      por request (backend/frontend/app/static/js/auditoria.js::handleCSVUpload);
      cada tanda es un POST /Auditar/ independiente y, antes de este cambio,
      generaba su propia fila de log (cantidad_solicitada y filas_auditadas por
      tanda, sin relación visible entre sí). Con upload_group_id, todas las tandas
      de una misma subida (mismo ID generado 1 vez en el navegador antes de
      trocear) acumulan en UNA sola fila (ver
      AuditorIA/execution_log.py::continuar_o_iniciar_grupo/finalizar_grupo).

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario con DDL sobre el schema calidad.
   Idempotente: cada columna se agrega solo si no existe.
   ============================================================================ */

SET XACT_ABORT ON;
GO

IF COL_LENGTH('calidad.AuditExecutionLog', 'por_operador') IS NULL
    ALTER TABLE calidad.AuditExecutionLog ADD por_operador BIT NOT NULL CONSTRAINT DF_AuditExecutionLog_por_operador DEFAULT(0);
GO

IF COL_LENGTH('calidad.AuditExecutionLog', 'por_tipificacion') IS NULL
    ALTER TABLE calidad.AuditExecutionLog ADD por_tipificacion BIT NOT NULL CONSTRAINT DF_AuditExecutionLog_por_tipificacion DEFAULT(0);
GO

IF COL_LENGTH('calidad.AuditExecutionLog', 'desglose_muestreo') IS NULL
    ALTER TABLE calidad.AuditExecutionLog ADD desglose_muestreo NVARCHAR(MAX) NULL;
GO

IF COL_LENGTH('calidad.AuditExecutionLog', 'upload_group_id') IS NULL
    ALTER TABLE calidad.AuditExecutionLog ADD upload_group_id NVARCHAR(100) NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_AuditExecutionLog_upload_group')
    CREATE INDEX IX_AuditExecutionLog_upload_group ON calidad.AuditExecutionLog (upload_group_id)
        WHERE upload_group_id IS NOT NULL;
GO
