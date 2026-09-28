/*
  2026-07-30 (b) — Reparación de auditorías que quedaron sin operador y/o con
  IdAplicativo 'nan_nan' por contaminación de columnas entre lotes de la misma pasada.

  QUÉ PASÓ
  --------
  procesar_batch arma un solo df_total con los resultados de todos los lotes que
  terminaron en la misma ventana de polling (15 min) y después los separa con un groupby.
  Pero un grupo del groupby conserva TODAS las columnas de df_total: las que aportó otro
  lote quedan presentes y enteras en NaN. Y tanto la resolución de alias
  (_estandarizar_columnas_sql) como la clave (calcular_id_aplicativo) deciden por
  PRESENCIA de la columna y por orden de prioridad, sin mirar si tiene datos:

    * 'loginid' gana sobre 'operador'  -> un lote de Farmalux/Vantix (columna LoginId) le
      dejaba a Dental y ALARMIX el operadorUsuario en NULL.
    * 'idinteraccion'+'segmento' ganan sobre 'segmentid' / 'id del llamado' -> un lote de
      Dental le hacía calcular a ALARMIX y a Vantix un IdAplicativo 'nan_nan' (y a Vantix,
      además, fecha_interaccion NULL, porque heredaba la columna 'inicio' vacía).

  El bug es viejo y latente: sólo se dispara cuando dos orígenes distintos coinciden en la
  misma ventana. Explotó de golpe el 30/07 al reprocesar los 21 lotes rescatados juntos.
  Arreglado en Auditor.py::procesar_batch (dropna de columnas sin un solo dato antes de
  interpretar el grupo).

  ALCANCE DEL DAÑO
  ----------------
    * Dental (empresa 4, campañas 6 y 26): 311 auditorías sin operadorUsuario.
    * ALARMIX (campaña 21): 362 con IdAplicativo 'nan_nan' y/o sin operador (18/07 en adelante).
    * Vantix (campaña 10): 20 con IdAplicativo 'nan_nan' y fecha_interaccion NULL.

  Un IdAplicativo inválido no es cosmético: es la clave con la que la auditoría se asocia
  a su interacción y con la que la descarga deduplica (NOT EXISTS) para no re-auditar lo
  mismo.

  DE DÓNDE SALEN LOS DATOS
  ------------------------
  De calidad.Batch_data, que conserva la metadata original de cada lote. Cada bloque usa
  la clave real de su origen y sólo toca filas rotas; si la metadata ya fue purgada por
  sp_LimpiarDatosDeBatchFinalizados, la fila no se repara (se reporta al final).

  Se puede correr más de una vez: los predicados exigen que la fila esté rota.
*/

SET XACT_ABORT ON;
BEGIN TRANSACTION;

-- Foto previa.
SELECT 'ANTES' AS momento, EmpresaID, CampanaID,
       SUM(CASE WHEN operadorUsuario IS NULL THEN 1 ELSE 0 END) AS sin_operador,
       SUM(CASE WHEN IdAplicativo LIKE 'nan%' THEN 1 ELSE 0 END) AS id_invalido
FROM calidad.Auditorias
WHERE operadorUsuario IS NULL OR IdAplicativo LIKE 'nan%'
GROUP BY EmpresaID, CampanaID;


/* -----------------------------------------------------------------------------------
   1) Dental (y cualquier origen con clave idInteraccion + Segmento): falta el operador.
      El IdAplicativo de estas filas quedó BIEN, así que sirve de clave de reparación.
   ----------------------------------------------------------------------------------- */
WITH op_por_idaplicativo AS (
    SELECT i.valor + '_' + s.valor AS IdAplicativo, MAX(o.valor) AS operador
    FROM calidad.Batch_data o
    JOIN calidad.Batch_data i
      ON i.batch_id = o.batch_id AND i.segment_id = o.segment_id AND i.columna = 'idInteraccion'
    JOIN calidad.Batch_data s
      ON s.batch_id = o.batch_id AND s.segment_id = o.segment_id AND s.columna = 'Segmento'
    WHERE o.columna = 'Operador' AND o.valor IS NOT NULL
      AND i.valor IS NOT NULL AND s.valor IS NOT NULL
    GROUP BY i.valor + '_' + s.valor
)
UPDATE a
SET operadorUsuario = op.operador
FROM calidad.Auditorias a
JOIN op_por_idaplicativo op ON op.IdAplicativo = a.IdAplicativo
WHERE a.operadorUsuario IS NULL;


/* -----------------------------------------------------------------------------------
   2) ALARMIX: IdAplicativo 'nan_nan' (su clave real es segmentId) y operador NULL.
      Como la clave está rota, el enlace se hace por fecha_interaccion, que sí se guardó
      bien y viene del mismo campo 'inicio' de la metadata. Verificado: dentro de los
      lotes de ALARMIX los 'inicio' son únicos (300 filas, 300 valores distintos).
      El CAST a datetime2 y de vuelta a datetime replica el redondeo de la columna
      destino; sin él, el string de 7 decimales no castea a datetime y no matchea nada.
   ----------------------------------------------------------------------------------- */
WITH alarmix AS (
    SELECT
        MAX(CASE WHEN columna = 'segmentId' THEN valor END) AS segid,
        MAX(CASE WHEN columna = 'Operador'  THEN valor END) AS operador,
        CAST(TRY_CAST(MAX(CASE WHEN columna = 'inicio' THEN valor END) AS datetime2) AS datetime) AS inicio
    FROM calidad.Batch_data
    WHERE batch_id IN (
        SELECT batch_id FROM calidad.AuditExecutionLog WHERE campana = '21' AND batch_id IS NOT NULL
    )
    GROUP BY batch_id, segment_id
)
UPDATE a
SET IdAplicativo   = COALESCE(x.segid, a.IdAplicativo),
    operadorUsuario = COALESCE(a.operadorUsuario, x.operador)
FROM calidad.Auditorias a
CROSS APPLY (
    SELECT TOP 1 segid, operador FROM alarmix
    WHERE alarmix.inicio = a.fecha_interaccion AND alarmix.segid IS NOT NULL
) x
WHERE a.CampanaID = 21
  AND (a.IdAplicativo LIKE 'nan%' OR a.operadorUsuario IS NULL);


/* -----------------------------------------------------------------------------------
   3) Vantix (campaña 10): IdAplicativo 'nan_nan' y fecha_interaccion NULL. Su clave real
      es 'ID del llamado'. Acá el operador SÍ quedó bien (LoginId es su propia columna),
      así que el enlace se hace por (operador, duración), verificado único dentro del lote.
   ----------------------------------------------------------------------------------- */
WITH vantix AS (
    SELECT
        MAX(CASE WHEN columna = 'LoginId'        THEN valor END) AS loginid,
        MAX(CASE WHEN columna = 'ID del llamado' THEN valor END) AS id_llamado,
        TRY_CAST(MAX(CASE WHEN columna = 'Duracion' THEN valor END) AS int) AS duracion,
        CAST(TRY_CAST(MAX(CASE WHEN columna = 'Fecha y hora del llamado' THEN valor END) AS datetime2) AS datetime) AS fecha_hora
    FROM calidad.Batch_data
    WHERE batch_id IN (
        SELECT batch_id FROM calidad.AuditExecutionLog WHERE campana = '10' AND batch_id IS NOT NULL
    )
    GROUP BY batch_id, segment_id
)
UPDATE a
SET IdAplicativo      = COALESCE(x.id_llamado, a.IdAplicativo),
    fecha_interaccion = COALESCE(a.fecha_interaccion, x.fecha_hora)
FROM calidad.Auditorias a
CROSS APPLY (
    SELECT TOP 1 id_llamado, fecha_hora FROM vantix
    WHERE vantix.loginid = a.operadorUsuario
      AND vantix.duracion = a.duracion_segundos
      AND vantix.id_llamado IS NOT NULL
) x
WHERE a.CampanaID = 10
  AND a.IdAplicativo LIKE 'nan%';


-- Foto posterior: lo que quede acá es lo que ya no tiene metadata viva en Batch_data
-- (lotes purgados por sp_LimpiarDatosDeBatchFinalizados) y no se puede reconstruir.
SELECT 'DESPUES' AS momento, EmpresaID, CampanaID,
       SUM(CASE WHEN operadorUsuario IS NULL THEN 1 ELSE 0 END) AS sin_operador,
       SUM(CASE WHEN IdAplicativo LIKE 'nan%' THEN 1 ELSE 0 END) AS id_invalido
FROM calidad.Auditorias
WHERE operadorUsuario IS NULL OR IdAplicativo LIKE 'nan%'
GROUP BY EmpresaID, CampanaID;

COMMIT TRANSACTION;
GO
