/*
  2026-09-22 — Columna Entorno en las tres tablas de ejecución que todavía no la tenían:
  calidad.AuditSchedulers, calidad.BatchJobs y calidad.AuditTasks.

  PROBLEMA
  --------
  dev (SRV00) y prod (SRV01) comparten la base. Las colas nuevas ya se particionan por
  settings.ENVIRONMENT (BatchPendientes, TranscripcionJobs, AudioAuditoria con Entorno;
  ChatbotIndexJobs y ChatbotDocJobs con environment), pero estas tres no:
    - AuditSchedulers: run_pending_schedulers reclama CUALQUIER tarea programada vencida.
    - BatchJobs: check_batch_status procesa CUALQUIER lote de Gemini en vuelo (guarda los
      resultados, exporta a Sheets, manda el mail y borra la carpeta del fileserver).
    - AuditTasks: cerrar_auditorias_huerfanas cierra las 'pending' de los dos entornos.
  Hoy no explota solo porque SRV00 no corre el scheduler. El día que alguien lo levante en
  dev para probar algo, dev ejecuta las auditorías programadas de prod y procesa sus lotes
  con código que todavía no está en prod.

  SOLUCIÓN
  --------
  Mismo patrón que las otras colas: NVARCHAR(20) NOT NULL DEFAULT 'prod'. Todo lo que ya
  existe queda como 'prod' (es quien lo viene ejecutando), así que prod sigue igual. Desde
  el deploy cada proceso escribe su settings.ENVIRONMENT y solo reclama lo suyo. La
  pantalla del Scheduler sigue listando las tareas de los dos entornos, con una marca en
  las que ejecuta el otro servidor.

  Aditiva e idempotente. Las tres tablas son chicas (13, ~1.200 y ~3.600 filas al
  2026-09-22): agregar la columna con DEFAULT es instantáneo.

  Aplicar ANTES de deployar el código (los INSERT y los WHERE nuevos nombran Entorno).
*/

SET XACT_ABORT ON;
BEGIN TRANSACTION;

IF COL_LENGTH('calidad.AuditSchedulers', 'Entorno') IS NULL
    ALTER TABLE calidad.AuditSchedulers ADD Entorno NVARCHAR(20) NOT NULL
        CONSTRAINT DF_AuditSchedulers_Entorno DEFAULT 'prod' WITH VALUES;

IF COL_LENGTH('calidad.BatchJobs', 'Entorno') IS NULL
    ALTER TABLE calidad.BatchJobs ADD Entorno NVARCHAR(20) NOT NULL
        CONSTRAINT DF_BatchJobs_Entorno DEFAULT 'prod' WITH VALUES;

IF COL_LENGTH('calidad.AuditTasks', 'Entorno') IS NULL
    ALTER TABLE calidad.AuditTasks ADD Entorno NVARCHAR(20) NOT NULL
        CONSTRAINT DF_AuditTasks_Entorno DEFAULT 'prod' WITH VALUES;

COMMIT TRANSACTION;
GO

-- Verificación: las tres deberían devolver 'prod' en todas sus filas.
SELECT 'AuditSchedulers' AS tabla, Entorno, COUNT(*) AS filas FROM calidad.AuditSchedulers GROUP BY Entorno
UNION ALL
SELECT 'BatchJobs', Entorno, COUNT(*) FROM calidad.BatchJobs GROUP BY Entorno
UNION ALL
SELECT 'AuditTasks', Entorno, COUNT(*) FROM calidad.AuditTasks GROUP BY Entorno;
GO
