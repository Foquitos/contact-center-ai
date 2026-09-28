/* ============================================================================
   Feature — Agrupar los lotes de una misma corrida en el log de auditorías
   Fecha: 2026-08-18
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   calidad.AuditExecutionLog.run_id — identificador de la CORRIDA. Todas las
   filas que pertenecen a la misma ejecución comparten el mismo run_id.

   POR QUÉ
   -------
   En modo Batch una corrida no es una fila: gemini.py::calidad_batch parte los
   audios en lotes de 15 MB (el request inline de Gemini no puede ser más grande)
   y CADA lote crea su propio job y su propia fila de log. Una programada de 30
   llamados que entra en dos lotes se ve en /uso-ia como dos corridas de 30 —
   parece que la tarea se ejecutó dos veces cuando calidad.AuditSchedulerHistory
   muestra una sola ejecución.

   Con run_id, la pantalla agrupa: una fila por corrida, con el detalle por lote
   adentro. La columna `cantidad_solicitada` NO cambia de significado (sigue
   siendo por fila) — el agregado la resuelve según el muestreo, ver
   uso_ia.py::_SQL_CORRIDAS.

   BACKFILL
   --------
   Las filas viejas no tienen forma exacta de reagruparse (el run_id no existía),
   así que se reconstruye por proximidad: los lotes de modo 'batch' que comparten
   origen/scheduler/task/plantilla/empresa/campaña/usuario y arrancaron a menos de
   120 segundos uno del otro son la misma corrida (los lotes reales salen con 5-10
   segundos de diferencia; una corrida distinta de la misma programada está a un
   día). Todo lo demás —sync, y cualquier batch que quede suelto— recibe un run_id
   propio, que es exactamente la semántica anterior: una fila = una corrida.

   La única imprecisión posible es un usuario que haya disparado DOS auditorías
   manuales en batch de la misma campaña y plantilla con menos de 2 minutos de
   diferencia: quedarían agrupadas como una. Solo afecta al histórico; a partir de
   esta migración el run_id lo genera Auditor.run_batch y es exacto.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario con DDL sobre el schema calidad.
   Idempotente: cada paso se saltea si ya está aplicado.

   ORDEN: aplicar ANTES de deployar el backend (el listado de /uso-ia/logs
   agrupa por run_id y sin la columna devuelve 500).
   ============================================================================ */

SET XACT_ABORT ON;
GO

/* ---------------------------------------------------------------------------
   1. Columna
   --------------------------------------------------------------------------- */
IF COL_LENGTH('calidad.AuditExecutionLog', 'run_id') IS NULL
BEGIN
    ALTER TABLE calidad.AuditExecutionLog ADD run_id UNIQUEIDENTIFIER NULL;
    PRINT 'run_id agregada.';
END
ELSE
    PRINT 'run_id ya existía; no se toca.';
GO

/* ---------------------------------------------------------------------------
   2. Backfill de las filas históricas (ver BACKFILL arriba)
   --------------------------------------------------------------------------- */
IF EXISTS (SELECT 1 FROM calidad.AuditExecutionLog WHERE run_id IS NULL)
BEGIN
    -- Clave de agrupación: lo que es constante dentro de una corrida. batch_id
    -- queda afuera a propósito (es justo lo que cambia entre lotes).
    SELECT
        id,
        started_at,
        grp = CONCAT(trigger_source, '|',
                     ISNULL(CAST(scheduler_id AS NVARCHAR(20)), ''), '|',
                     ISNULL(task_id, ''), '|',
                     ISNULL(CAST(plantilla_id AS NVARCHAR(20)), ''), '|',
                     ISNULL(empresa, ''), '|',
                     ISNULL(campana, ''), '|',
                     ISNULL(user_id, ''))
    INTO #lotes
    FROM calidad.AuditExecutionLog
    WHERE modo = 'batch' AND run_id IS NULL;

    -- Gaps and islands: una isla nueva empieza cuando el lote anterior del mismo
    -- grupo arrancó hace más de 120 segundos (o no hay anterior: LAG NULL ->
    -- DATEDIFF NULL -> la comparación no se cumple -> salto = 1).
    SELECT
        id,
        grp,
        isla = SUM(salto) OVER (PARTITION BY grp ORDER BY started_at ROWS UNBOUNDED PRECEDING)
    INTO #islas
    FROM (
        SELECT
            id, grp, started_at,
            salto = CASE
                WHEN DATEDIFF(SECOND,
                              LAG(started_at) OVER (PARTITION BY grp ORDER BY started_at),
                              started_at) <= 120
                THEN 0 ELSE 1
            END
        FROM #lotes
    ) AS x;

    -- Un GUID por isla (NEWID() en un SELECT agrupado se evalúa una vez por grupo).
    SELECT grp, isla, gid = NEWID()
    INTO #runs
    FROM #islas
    GROUP BY grp, isla;

    UPDATE l
    SET run_id = r.gid
    FROM calidad.AuditExecutionLog AS l
    INNER JOIN #islas AS i ON i.id = l.id
    INNER JOIN #runs  AS r ON r.grp = i.grp AND r.isla = i.isla;

    -- CONCAT no admite subconsultas: los conteos se resuelven antes, en variables.
    DECLARE @filas INT = (SELECT COUNT(*) FROM #islas);
    DECLARE @corridas INT = (SELECT COUNT(*) FROM #runs);
    PRINT CONCAT('Lotes de batch agrupados: ', @filas, ' filas en ', @corridas, ' corridas.');

    DROP TABLE #runs, #islas, #lotes;

    -- Todo lo que no es batch (sync, subidas CSV agrupadas por upload_group_id):
    -- una fila = una corrida. NEWID() en un UPDATE se evalúa por fila.
    UPDATE calidad.AuditExecutionLog SET run_id = NEWID() WHERE run_id IS NULL;
END
ELSE
    PRINT 'No hay filas sin run_id; backfill omitido.';
GO

/* ---------------------------------------------------------------------------
   3. NOT NULL + default
   El default es la red de seguridad: cualquier INSERT que no nombre la columna
   (código viejo, un fix manual) recibe su propio run_id en vez de NULL. Un NULL
   sería peor que un duplicado — todas las filas con run_id NULL colapsarían en
   una sola "corrida" gigante en el listado agrupado.
   --------------------------------------------------------------------------- */
IF EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('calidad.AuditExecutionLog')
      AND name = 'run_id' AND is_nullable = 1
)
BEGIN
    ALTER TABLE calidad.AuditExecutionLog ALTER COLUMN run_id UNIQUEIDENTIFIER NOT NULL;
    PRINT 'run_id pasada a NOT NULL.';
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name = 'DF_AuditExecutionLog_run_id')
BEGIN
    ALTER TABLE calidad.AuditExecutionLog
        ADD CONSTRAINT DF_AuditExecutionLog_run_id DEFAULT NEWID() FOR run_id;
    PRINT 'Default NEWID() agregado.';
END
GO

/* ---------------------------------------------------------------------------
   4. Índice — el listado agrupa por run_id y el detalle de una corrida lo busca
   --------------------------------------------------------------------------- */
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID('calidad.AuditExecutionLog') AND name = 'IX_AuditExecutionLog_run_id'
)
BEGIN
    CREATE INDEX IX_AuditExecutionLog_run_id
        ON calidad.AuditExecutionLog (run_id) INCLUDE (started_at);
    PRINT 'IX_AuditExecutionLog_run_id creado.';
END
GO

/* ---------------------------------------------------------------------------
   Verificación (opcional)
   ---------------------------------------------------------------------------
SELECT TOP 20 run_id, COUNT(*) AS lotes, MIN(started_at) AS inicio,
       MIN(scheduler_name) AS tarea, SUM(filas_auditadas) AS auditadas
FROM calidad.AuditExecutionLog
GROUP BY run_id
HAVING COUNT(*) > 1
ORDER BY inicio DESC;
   --------------------------------------------------------------------------- */
