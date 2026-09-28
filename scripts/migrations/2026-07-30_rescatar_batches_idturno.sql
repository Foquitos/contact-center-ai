/*
  2026-07-30 — Rescate de los 21 lotes que quedaron EN_CURSO por el IdTurno de Dental.

  QUÉ PASÓ
  --------
  La descarga de Odonto Plus es la única que trae IdTurno (OUTER APPLY contra
  Odonto_Plus_Turnero). Cuando en la muestra había al menos una llamada SIN turno
  asociado, pandas promovía la columna a float64 por el NaN, y al persistir la metadata
  del lote ese float entraba como parámetro a la columna varchar de calidad.Batch_data:
  SQL Server lo convertía a texto en formato científico ('1.05276e+007').

  Horas después, al procesar la respuesta de Gemini, la estandarización de columnas hacía
  `str(int(x))` sobre ese texto -> ValueError. Y como esa llamada estaba FUERA de todo
  try/except, la excepción abortaba la pasada entera de procesar_batch: ningún lote se
  sellaba, ningún AuditExecutionLog se cerraba y ni siquiera corría la red de
  MAX_INTENTOS_PROCESO que debía abandonarlos con un motivo. Resultado: AuditTasks en
  'en_cola' y AuditExecutionLog en 'EN_CURSO' para siempre (el spinner infinito en la
  bandeja).

  Daño: 21 corridas, 493 auditorías. 13 son de Dental (campaña 6 telefónica y 26 chat) y
  las otras 8 son colaterales: ALARMIX y una manual de Farmalux que solo tuvieron la mala
  suerte de terminar en la misma ventana de polling de 15 min que un lote de Dental.

  POR QUÉ DESELLAR Y NO CERRAR EN ERROR
  -------------------------------------
  Se verificaron los 21 lotes contra la API de Gemini el 2026-07-30: los 21 siguen en
  JOB_STATE_SUCCEEDED y con sus inlined_responses disponibles (incluso el del 13/07), y su
  Batch_data está intacta. O sea que las 493 auditorías ya pagadas se pueden guardar: no
  hay nada que dar por perdido.

  REQUISITO
  ---------
  Aplicar DESPUÉS de deployar el fix (Auditor.py::_idturno_a_texto + estandarización por
  lote dentro del try + gemini.py::valor_para_batch_data). Sin el fix, el primer reintento
  vuelve a estrellarse igual y consume los 5 intentos de nuevo.

  EFECTO AL APLICAR
  -----------------
  El próximo check_batch_status (cada 15 min) los toma a los 21 juntos y, por cada uno,
  guarda las auditorías + exporta a Google Sheets + manda el mail de resultado a su
  usuario. Son 21 mails de corridas viejas de una sola vez. Si preferís escalonarlo, está
  abajo la variante por fecha.

  NOTA sobre el IdTurno de esos lotes: el número guardado en Batch_data quedó truncado a 6
  dígitos significativos, así que es irrecuperable. _idturno_a_texto lo detecta y guarda
  NULL en vez de reconstruir un id que apuntaría al turno de otro paciente. Las auditorías
  se rescatan completas; lo único que les falta es ese campo en Extras.
*/

SET XACT_ABORT ON;
BEGIN TRANSACTION;

-- Foto de lo que se va a rescatar (queda en la salida del script, para control).
SELECT
    l.id                                  AS log_id,
    l.empresa,
    l.campana,
    l.plantilla_id,
    ISNULL(l.scheduler_name, l.trigger_source) AS origen,
    l.started_at,
    l.cantidad_solicitada,
    bj.intentos_proceso,
    COUNT(bd.id)                          AS filas_metadata
FROM calidad.AuditExecutionLog l
JOIN calidad.BatchJobs bj  ON bj.[name]   = l.batch_id
JOIN calidad.Batch_data bd ON bd.Batch_id = l.batch_id
WHERE l.status = 'EN_CURSO'
  AND l.batch_id IS NOT NULL
  AND bj.status = 'JOB_STATE_SUCCEEDED'
  AND bj.procesado_at IS NULL
GROUP BY l.id, l.empresa, l.campana, l.plantilla_id, l.scheduler_name, l.trigger_source,
         l.started_at, l.cantidad_solicitada, bj.intentos_proceso;

/*
  Devolverlos a la cola de procesamiento.

  El predicado es el de un lote trabado por este bug, y ninguna otra cosa:
    * su corrida quedó EN_CURSO (nunca se cerró, ni en EXITO ni en ERROR),
    * Gemini lo terminó bien (JOB_STATE_SUCCEEDED),
    * de nuestro lado nunca se selló (procesado_at IS NULL),
    * y su Batch_data sigue viva, así que las respuestas se pueden atribuir.

  Resetear intentos_proceso es lo que los vuelve a meter en el SELECT de
  check_batch_status, que filtra por intentos_proceso < MAX_INTENTOS_PROCESO (5). Los 21
  están justo en 5.
*/
UPDATE bj
SET intentos_proceso = 0
FROM calidad.BatchJobs bj
WHERE bj.status = 'JOB_STATE_SUCCEEDED'
  AND bj.procesado_at IS NULL
  AND bj.intentos_proceso > 0
  AND EXISTS (
        SELECT 1 FROM calidad.Batch_data bd WHERE bd.Batch_id = bj.[name]
  )
  AND EXISTS (
        SELECT 1 FROM calidad.AuditExecutionLog l
        WHERE l.batch_id = bj.[name] AND l.status = 'EN_CURSO'
  );

-- Control: cuántos quedaron listos para reprocesar (esperado: 21).
SELECT COUNT(*) AS lotes_devueltos_a_la_cola
FROM calidad.BatchJobs bj
WHERE bj.status = 'JOB_STATE_SUCCEEDED'
  AND bj.procesado_at IS NULL
  AND bj.intentos_proceso = 0
  AND EXISTS (SELECT 1 FROM calidad.Batch_data bd WHERE bd.Batch_id = bj.[name]);

COMMIT TRANSACTION;
GO

/*
  VARIANTE ESCALONADA (opcional)
  ------------------------------
  Si 21 mails de golpe es mucho, correr el UPDATE de arriba agregando un filtro por fecha
  y repetir en tandas:

      AND EXISTS (
            SELECT 1 FROM calidad.AuditExecutionLog l
            WHERE l.batch_id = bj.[name] AND l.status = 'EN_CURSO'
              AND l.started_at >= '2026-07-28'
      )
*/
