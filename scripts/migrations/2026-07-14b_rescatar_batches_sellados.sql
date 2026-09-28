/*
  2026-07-14 (b) — CORRECCIÓN de la migración 2026-07-14_batchjobs_procesado_at.sql.

  QUÉ SALIÓ MAL
  -------------
  El backfill de esa migración fue demasiado ancho:

      UPDATE calidad.BatchJobs SET procesado_at = created_at
      WHERE status = 'JOB_STATE_SUCCEEDED' AND procesado_at IS NULL;

  La intención era dar por cerrados los lotes históricos para que el primer
  check_batch_status posterior al deploy no intentara reprocesar los ~194 viejos (cuya
  Batch_data ya no existe). Pero el WHERE no distinguía entre "exitoso y ya guardado" y
  "exitoso y NUNCA procesado, con su metadata todavía viva": selló las dos cosas.

  Sellar un lote es justamente el permiso que sp_LimpiarDatosDeBatchFinalizados necesita
  para borrar su Batch_data. O sea que el backfill puso en la cola de destrucción a 5 lotes
  que todavía se podían salvar — el mismo daño que la migración venía a evitar.

  QUÉ RESCATA ESTO
  ----------------
  Lotes SUCCEEDED en Gemini, con Batch_data intacta, que nunca dejaron auditorías:

    * logs 79 y 80  -> murieron con "invalid literal for int() with base 10: 'corte'"
                       (colisión de plantillas en la misma ventana de polling). Ese bug se
                       arregló en c34e582, así que ahora sí se pueden procesar.
    * logs 81/91/92 -> nunca se procesaron y quedaron en EN_CURSO.

  Al desellarlos vuelven a entrar en el SELECT de check_batch_status y el scheduler los
  procesa en la próxima pasada (15 min), guardando las auditorías que ya se le pagaron a
  Gemini.

  EL PREDICADO
  ------------
  "Tiene Batch_data viva Y su corrida nunca terminó en EXITO" es una señal limpia: un lote
  realmente procesado deja su AuditExecutionLog en EXITO, y su metadata se purga después.

  APLICAR CUANTO ANTES: si sp_LimpiarDatosDeBatchFinalizados corre primero, la metadata se
  va y estos 5 lotes se pierden igual que los 4 anteriores.
  Requiere tener deployado el Auditor.py nuevo para que el reproceso ocurra.
*/

SET XACT_ABORT ON;
BEGIN TRANSACTION;

-- Foto de lo que se va a rescatar (queda en la salida del script, para control).
SELECT
    bj.[name]                         AS batch_id,
    l.id                              AS log_id,
    l.status                          AS estado_corrida,
    l.scheduler_name,
    COUNT(bd.id)                      AS filas_metadata
FROM calidad.BatchJobs bj
JOIN calidad.Batch_data bd            ON bd.Batch_id = bj.[name]
LEFT JOIN calidad.AuditExecutionLog l ON l.batch_id  = bj.[name]
WHERE bj.status = 'JOB_STATE_SUCCEEDED'
  AND bj.procesado_at IS NOT NULL
  AND NOT EXISTS (
        SELECT 1 FROM calidad.AuditExecutionLog ok
        WHERE ok.batch_id = bj.[name] AND ok.status = 'EXITO'
  )
GROUP BY bj.[name], l.id, l.status, l.scheduler_name;

-- Devolverlos a la cola de procesamiento.
UPDATE bj
SET procesado_at     = NULL,
    intentos_proceso = 0
FROM calidad.BatchJobs bj
WHERE bj.status = 'JOB_STATE_SUCCEEDED'
  AND bj.procesado_at IS NOT NULL
  AND EXISTS (
        SELECT 1 FROM calidad.Batch_data bd WHERE bd.Batch_id = bj.[name]
  )
  AND NOT EXISTS (
        SELECT 1 FROM calidad.AuditExecutionLog ok
        WHERE ok.batch_id = bj.[name] AND ok.status = 'EXITO'
  );

COMMIT TRANSACTION;
GO
