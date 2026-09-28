/* ============================================================================
   Backfill — tipificación real (Subcategoría) en auditorías de Vantix / Orion
   Fecha: 2026-06-20
   Autor: equipo Acme

   CONTEXTO
   --------
   Las descargas de Vantix (Orion, EmpresaID 5) exponen DOS columnas de
   tipificación:
     · [Tipificacion/Categoria]      -> Categoría: bucket genérico
                                         (Turnos, Multiskill, Retenciones, ...)
     · [Subtipificacion/Subcategoria]-> Subcategoría: la tipificación REAL del
                                         llamado (No coordina turno, Volver a
                                         llamar, Contestador automático, ...)

   El mapeo a calidad.Auditorias.tipificacion_interaccion (backend/Auditor.py)
   recorría una lista de alias cortando en el primer match y tenía
   'tipificacion/categoria' ANTES que 'subtipificacion/subcategoria', así que
   para Vantix se guardaba la Categoría en lugar de la Subcategoría.

   FIX DE CÓDIGO (ya aplicado): backend/Auditor.py — se reordenaron los alias
   para que gane 'subtipificacion/subcategoria'. Desde ese deploy las
   auditorías nuevas guardan la subcategoría correcta. Esta migración corrige
   las auditorías HISTÓRICAS ya guardadas.

   QUÉ HACE
   --------
   Recalcula la Subcategoría real desde Orion (ContactCenter, vía linked server
   ORION_LINK) con el MISMO join que usa el código, y actualiza
   tipificacion_interaccion para EmpresaID = 5 cuando:
     · hoy guarda la Categoría genérica  (≈5187 filas), o
     · hoy está en NULL                  (≈1437 filas).
   Total esperado ≈ 6624 filas (validado al 2026-06-20).

   PROPIEDADES
   -----------
   · Idempotente: sólo toca filas cuyo valor guardado difiere de la subcategoría
     recalculada; re-ejecutarla no cambia nada nuevo.
   · Acotada: el pull remoto corre con OPENQUERY (filtros remotos) y trae sólo
     dos columnas. La ventana de -90 días cubre todas las auditorías afectadas
     (la más vieja es del 2026-06-01) y se mantiene dentro de la retención de
     Orion. El JOIN por ID_Llamada restringe al conjunto auditado.
   · Transaccional con guarda: si se actualizan más filas de las esperadas,
     hace ROLLBACK en vez de COMMIT (protege ante un error de lógica/collation).

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario que tenga UPDATE sobre
   calidad.Auditorias y acceso al linked server ORION_LINK (p. ej. Bot).
   Revisar los PRINT de salida; si dice "ABORT", NO commitea: revisar y reintentar.
   ============================================================================ */

SET XACT_ABORT ON;
SET NOCOUNT ON;

DECLARE @tope_filas INT = 9000;  -- guarda de seguridad (esperado ≈6624)

/* -- 1) Mapa ID_Llamada -> Subcategoría real, traído de Orion ------------- */
IF OBJECT_ID('tempdb..#orion_subcat') IS NOT NULL DROP TABLE #orion_subcat;

SELECT IdLlamada, Subcat
INTO #orion_subcat
FROM OPENQUERY(ORION_LINK, '
    SELECT
        CAST(e.ID_Llamada AS VARCHAR(50)) AS IdLlamada,
        LTRIM(RTRIM(REPLACE(REPLACE(sub.Descripcion, CHAR(13), ''''), CHAR(10), ''''))) AS Subcat
    FROM ContactCenter.dbo.EstadPorLlamada e
    LEFT JOIN ContactCenter.dbo.DatosPorLlamada dps
        ON dps.Call_Id = e.ID_Llamada AND dps.Clave = ''CODSUBCAT''
    LEFT JOIN ContactCenter.dbo.SubCategorias sub
        ON sub.CodSubcategoria = COALESCE(TRY_CAST(dps.Valor AS INT), NULLIF(e.Codificacion, 0))
    WHERE e.Fecha >= DATEADD(DAY, -90, GETDATE())
      AND sub.Descripcion IS NOT NULL
');

CREATE INDEX ix_tmp_idllamada ON #orion_subcat (IdLlamada);

/* -- 2) Diagnóstico previo ------------------------------------------------- */
DECLARE @categoria INT, @nulos INT;

SELECT
    @categoria = SUM(CASE WHEN a.tipificacion_interaccion IS NOT NULL
                           AND a.tipificacion_interaccion COLLATE DATABASE_DEFAULT <> s.Subcat
                          THEN 1 ELSE 0 END),
    @nulos     = SUM(CASE WHEN a.tipificacion_interaccion IS NULL THEN 1 ELSE 0 END)
FROM calidad.Auditorias a
JOIN #orion_subcat s ON s.IdLlamada COLLATE DATABASE_DEFAULT = a.IdAplicativo
WHERE a.EmpresaID = 5 AND s.Subcat IS NOT NULL;

PRINT CONCAT('A corregir (Categoría -> Subcategoría): ', ISNULL(@categoria, 0));
PRINT CONCAT('A rellenar (NULL -> Subcategoría):      ', ISNULL(@nulos, 0));

/* -- 3) Update transaccional con guarda ------------------------------------ */
BEGIN TRAN;

UPDATE a
SET a.tipificacion_interaccion = s.Subcat
FROM calidad.Auditorias a
JOIN #orion_subcat s ON s.IdLlamada COLLATE DATABASE_DEFAULT = a.IdAplicativo
WHERE a.EmpresaID = 5
  AND s.Subcat IS NOT NULL
  AND (a.tipificacion_interaccion IS NULL
       OR a.tipificacion_interaccion COLLATE DATABASE_DEFAULT <> s.Subcat);

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

DROP TABLE #orion_subcat;
