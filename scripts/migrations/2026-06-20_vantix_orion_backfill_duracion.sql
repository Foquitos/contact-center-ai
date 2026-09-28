/* ============================================================================
   Backfill — duración (segundos) en auditorías históricas de Vantix / Orion
   Fecha: 2026-06-20
   Autor: equipo Acme

   CONTEXTO
   --------
   La feature de duración agregó calidad.Auditorias.duracion_segundos (ver
   migración 2026-06-20_auditorias_duracion_segundos.sql) y el guardado nuevo la
   puebla automáticamente. Las auditorías HISTÓRICAS quedaron en NULL.

   Para Vantix (Orion, EmpresaID 5) la duración del llamado es e.TiempoHablado
   (segundos hablados) en ContactCenter.dbo.EstadPorLlamada, lo mismo que mapea el
   código en vivo (columna [Duracion] del download). Este script la rellena por
   ID_Llamada vía linked server ORION_LINK.

   DEPENDENCIA
   -----------
   Requiere que la columna calidad.Auditorias.duracion_segundos YA exista (correr
   antes la migración de feature). El script aborta si no está.

   QUÉ HACE
   --------
   Rellena duracion_segundos = TiempoHablado para EmpresaID = 5.
   Total esperado ≈ 6627 filas (validado al 2026-06-20).

   PROPIEDADES
   -----------
   · Idempotente: sólo toca filas cuyo valor difiere del recalculado (NULL incluido);
     re-ejecutarla no cambia nada nuevo.
   · Acotada: pull remoto con OPENQUERY (filtro remoto), dos columnas. Ventana de
     -90 días cubre todas las auditorías afectadas (la más vieja es del 2026-06-01)
     y queda dentro de la retención de Orion. El JOIN por ID_Llamada restringe al
     conjunto auditado.
   · Transaccional con guarda: si se actualizan más filas de las esperadas, hace
     ROLLBACK en vez de COMMIT.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario con UPDATE sobre calidad.Auditorias y
   acceso al linked server ORION_LINK (p. ej. Bot). Revisar los PRINT; si dice
   "ABORT", NO commitea: revisar y reintentar.
   ============================================================================ */

SET XACT_ABORT ON;
SET NOCOUNT ON;

DECLARE @tope_filas INT = 9000;  -- guarda de seguridad (esperado ≈6627)

IF COL_LENGTH('calidad.Auditorias', 'duracion_segundos') IS NULL
BEGIN
    RAISERROR('Falta la columna calidad.Auditorias.duracion_segundos. Corré antes la migración de feature.', 16, 1);
    RETURN;
END

/* -- 1) Mapa ID_Llamada -> TiempoHablado (segundos), traído de Orion -------- */
IF OBJECT_ID('tempdb..#orion_dur') IS NOT NULL DROP TABLE #orion_dur;

SELECT IdLlamada, Seg
INTO #orion_dur
FROM OPENQUERY(ORION_LINK, '
    SELECT
        CAST(e.ID_Llamada AS VARCHAR(50)) AS IdLlamada,
        e.TiempoHablado AS Seg
    FROM ContactCenter.dbo.EstadPorLlamada e
    WHERE e.Fecha >= DATEADD(DAY, -90, GETDATE())
      AND e.TiempoHablado > 0
');

CREATE INDEX ix_tmp_dur_idllamada ON #orion_dur (IdLlamada);

/* -- 2) Diagnóstico previo ------------------------------------------------- */
DECLARE @a_llenar INT;
SELECT @a_llenar = COUNT(*)
FROM calidad.Auditorias a
JOIN #orion_dur o ON o.IdLlamada COLLATE DATABASE_DEFAULT = a.IdAplicativo
WHERE a.EmpresaID = 5 AND o.Seg IS NOT NULL
  AND (a.duracion_segundos IS NULL OR a.duracion_segundos <> o.Seg);
PRINT CONCAT('A rellenar/corregir duracion_segundos: ', ISNULL(@a_llenar, 0));

/* -- 3) Update transaccional con guarda ------------------------------------ */
BEGIN TRAN;

UPDATE a
SET a.duracion_segundos = o.Seg
FROM calidad.Auditorias a
JOIN #orion_dur o ON o.IdLlamada COLLATE DATABASE_DEFAULT = a.IdAplicativo
WHERE a.EmpresaID = 5
  AND o.Seg IS NOT NULL
  AND (a.duracion_segundos IS NULL OR a.duracion_segundos <> o.Seg);

DECLARE @afectadas INT = @@ROWCOUNT;
PRINT CONCAT('Filas actualizadas: ', @afectadas);

IF @afectadas > @tope_filas
BEGIN
    PRINT CONCAT('ABORT: se actualizaron ', @afectadas,
                 ' filas (tope ', @tope_filas, '). ROLLBACK. Revisar antes de commitear.');
    ROLLBACK TRAN;
END
ELSE
BEGIN
    COMMIT TRAN;
    PRINT 'OK: backfill commiteado.';
END

DROP TABLE #orion_dur;
