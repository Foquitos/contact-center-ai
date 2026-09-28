/* ============================================================================
   Backfill — mover Comentario de Vantix desde Extras a comentario_interaccion
   Fecha: 2026-06-24
   Autor: equipo Acme

   CONTEXTO
   --------
   En un primer enfoque (ya revertido) el comentario del agente de Vantix se
   persistía en calidad.Auditorias.extras como {"Comentario del Agente": "<valor>"}
   y se backfilleó el histórico (migración 2026-06-20_vantix_orion_backfill_extras_
   comentario.sql, aplicada en prod: ~7350 filas).

   Se cambió el enfoque: el comentario ahora vive en su propia columna
   comentario_interaccion (feature 2026-06-24_auditorias_comentario_interaccion.sql).
   Esta migración MUEVE el comentario histórico de Extras a la nueva columna y deja
   Extras en NULL.

   DEPENDENCIA
   -----------
   Requiere que la columna comentario_interaccion YA exista (correr antes la
   migración de feature). El script aborta si no está.

   QUÉ HACE
   --------
   Para EmpresaID = 5 (Vantix), en las filas cuyo Extras es EXACTAMENTE el JSON del
   comentario (una sola clave "Comentario del Agente"):
     · comentario_interaccion = valor del comentario (JSON_VALUE)
     · extras = NULL
   Total esperado ≈ 7350 filas (validado al 2026-06-24).

   PROPIEDADES
   -----------
   · Self-contained: usa el dato ya guardado en Extras (no consulta Orion).
   · No destructivo: solo vacía Extras cuando tiene UNA sola clave (la del
     comentario); si hubiera otras claves, no se toca (guard OPENJSON COUNT = 1).
   · Idempotente: solo toca filas con comentario_interaccion NULL; re-ejecutarla
     no cambia nada nuevo.
   · Transaccional con guarda: si actualiza más filas de las esperadas, ROLLBACK.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario con UPDATE sobre calidad.Auditorias.
   Revisar los PRINT; si dice "ABORT", NO commitea: revisar y reintentar.
   ============================================================================ */

SET XACT_ABORT ON;
SET NOCOUNT ON;

DECLARE @tope_filas INT = 9000;  -- guarda de seguridad (esperado ≈7350)

IF COL_LENGTH('calidad.Auditorias', 'comentario_interaccion') IS NULL
BEGIN
    RAISERROR('Falta la columna calidad.Auditorias.comentario_interaccion. Corré antes la migración de feature.', 16, 1);
    RETURN;
END

/* -- 1) Diagnóstico previo ------------------------------------------------- */
DECLARE @a_mover INT;
SELECT @a_mover = COUNT(*)
FROM calidad.Auditorias a
WHERE a.EmpresaID = 5
  AND a.comentario_interaccion IS NULL
  AND a.extras IS NOT NULL
  AND ISJSON(a.extras) = 1
  AND JSON_VALUE(a.extras, '$."Comentario del Agente"') IS NOT NULL
  AND (SELECT COUNT(*) FROM OPENJSON(a.extras)) = 1;
PRINT CONCAT('Comentarios a mover (Extras -> comentario_interaccion): ', ISNULL(@a_mover, 0));

/* -- 2) Move transaccional con guarda -------------------------------------- */
BEGIN TRAN;

UPDATE a
SET a.comentario_interaccion = JSON_VALUE(a.extras, '$."Comentario del Agente"'),
    a.extras = NULL
FROM calidad.Auditorias a
WHERE a.EmpresaID = 5
  AND a.comentario_interaccion IS NULL
  AND a.extras IS NOT NULL
  AND ISJSON(a.extras) = 1
  AND JSON_VALUE(a.extras, '$."Comentario del Agente"') IS NOT NULL
  AND (SELECT COUNT(*) FROM OPENJSON(a.extras)) = 1;

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
