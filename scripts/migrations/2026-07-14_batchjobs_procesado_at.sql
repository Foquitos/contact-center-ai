/*
  2026-07-14 — Separar "Gemini terminó el lote" de "nosotros guardamos sus resultados".

  PROBLEMA
  --------
  Auditor.check_batch_status marcaba BatchJobs.status = 'JOB_STATE_SUCCEEDED' y commiteaba
  ANTES de que procesar_batch guardara nada. sp_LimpiarDatosDeBatchFinalizados tomaba ese
  SUCCEEDED como "ya está, se puede limpiar" y borraba calidad.Batch_data, que es lo único
  que ata cada segment_id de la respuesta de Gemini a su interacción, plantilla y campaña.

  Si procesar_batch fallaba, se reiniciaba el scheduler, o simplemente no llegaba a correr
  antes de la limpieza, el resultado era pérdida definitiva:
    - las respuestas quedaban en Gemini pero sin forma de atribuirlas,
    - el lote ya no volvía a aparecer (check_batch_status solo miraba PENDING/RUNNING),
    - la fila de AuditTasks quedaba en 'en_cola' y la de AuditExecutionLog en 'EN_CURSO',
      para siempre.

  Al 2026-07-14 esto ya se había comido 4 lotes (logs 36, 55, 57 y 68 = 220 auditorías
  pagadas a Gemini y nunca guardadas).

  SOLUCIÓN
  --------
  Un lote tiene ahora dos estados independientes:
    - status        : en qué terminó del lado de Gemini.
    - procesado_at  : cuándo terminamos NOSOTROS con él (guardado o abandonado).
  La limpieza de Batch_data pasa a mirar procesado_at, no status. Mientras un lote no esté
  sellado, su metadata sobrevive y check_batch_status lo sigue reintentando.

  Aplicar ANTES de deployar el cambio de Auditor.py (el código nuevo lee estas columnas).
*/

SET XACT_ABORT ON;
BEGIN TRANSACTION;

-- 1) Estado propio del lote -------------------------------------------------------------
IF COL_LENGTH('calidad.BatchJobs', 'procesado_at') IS NULL
    ALTER TABLE calidad.BatchJobs ADD procesado_at DATETIME NULL;

IF COL_LENGTH('calidad.BatchJobs', 'intentos_proceso') IS NULL
    ALTER TABLE calidad.BatchJobs ADD intentos_proceso INT NOT NULL
        CONSTRAINT DF_BatchJobs_intentos_proceso DEFAULT 0 WITH VALUES;

COMMIT TRANSACTION;
GO

-- 2) Backfill ---------------------------------------------------------------------------
-- Todo lo histórico se da por cerrado. Sin esto, el primer check_batch_status posterior al
-- deploy intentaría reprocesar los ~194 lotes viejos, cuya Batch_data ya no existe.
UPDATE calidad.BatchJobs
SET procesado_at = created_at
WHERE status = 'JOB_STATE_SUCCEEDED'
  AND procesado_at IS NULL;
GO

-- 3) La limpieza pasa a respetar procesado_at --------------------------------------------
CREATE OR ALTER PROCEDURE dbo.sp_LimpiarDatosDeBatchFinalizados
AS
BEGIN
    SET NOCOUNT ON;

    -- Se borra la metadata SOLO de lotes con los que ya terminamos:
    --   * fallidos/cancelados/expirados en Gemini -> no hay resultados que guardar;
    --   * exitosos YA PROCESADOS (procesado_at sellado por Auditor._sellar_batch_procesado).
    -- Un lote exitoso todavía sin procesar conserva su Batch_data: es lo que permite que
    -- check_batch_status lo reintente en la próxima pasada en vez de perderlo.
    DELETE bd
    FROM calidad.[Batch_data] AS bd
    JOIN calidad.BatchJobs AS bj
      ON bj.[name] = bd.Batch_id
    WHERE bj.status IN ('JOB_STATE_FAILED', 'JOB_STATE_CANCELLED', 'JOB_STATE_EXPIRED')
       OR (bj.status = 'JOB_STATE_SUCCEEDED' AND bj.procesado_at IS NOT NULL);
END
GO

-- 4) Cerrar las tareas que quedaron colgadas por el bug -----------------------------------
-- 'pending'  : auditorías interactivas que morían con el proceso de la API.
-- 'en_cola'  : lotes batch cuyo resultado se perdió como se describe arriba.
-- El umbral (48h) está muy por encima del máximo real de un batch de Gemini (24h), así que
-- no puede alcanzar a una corrida viva.
UPDATE calidad.AuditTasks
SET status        = 'failed',
    error_message = 'La corrida se interrumpió y no dejó resultados (limpieza 2026-07-14).',
    updated_at    = GETDATE()
WHERE status IN ('pending', 'en_cola')
  AND updated_at < DATEADD(HOUR, -48, GETDATE());
GO

UPDATE calidad.AuditExecutionLog
SET status        = 'ERROR',
    error_message = 'La corrida se interrumpió y no dejó resultados (limpieza 2026-07-14).',
    finished_at   = GETDATE()
WHERE status = 'EN_CURSO'
  AND started_at < DATEADD(HOUR, -48, GETDATE());
GO
