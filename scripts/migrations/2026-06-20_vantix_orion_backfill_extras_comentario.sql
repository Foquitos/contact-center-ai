/* ============================================================================
   Backfill — Comentario del agente (COMENT) en Extras de auditorías Vantix/Orion
   Fecha: 2026-06-20
   Autor: equipo Acme

   CONTEXTO
   --------
   Se sumó el "Comentario del Agente" (clave COMENT de ContactCenter.dbo.
   DatosPorLlamada — DNI/patente/observaciones que el agente carga en la llamada)
   al armado de la columna Extras del guardado (backend/Auditor.py). Las
   auditorías nuevas de Vantix ya lo persisten como:
       {"Comentario del Agente": "<valor del COMENT>"}
   Las auditorías HISTÓRICAS de Vantix quedaron con extras = NULL.

   QUÉ HACE
   --------
   Rellena calidad.Auditorias.extras para EmpresaID = 5, SOLO donde hoy está NULL,
   con el mismo JSON que produce el código en vivo, recalculando el COMENT por
   ID_Llamada vía linked server ORION_LINK.
   Total esperado ≈ 7340 filas (validado al 2026-06-20).

   FIDELIDAD DEL JSON
   ------------------
   El código usa json.dumps({"Comentario del Agente": valor}, ensure_ascii=False).
   Acá se reproduce con STRING_ESCAPE(...,'json'); como STRING_ESCAPE escapa '/'
   como '\/' (json.dumps de Python NO lo hace), se revierte con REPLACE('\/','/').
   Comparado 2000/2000 contra el output de Python: coincidencia exacta.

   PROPIEDADES
   -----------
   · Idempotente y no destructivo: sólo toca filas con extras NULL (no pisa nada);
     re-ejecutarla sólo completa los NULL que falten.
   · Sólo calls con COMENT no vacío (mismo criterio que el código: valor IS NOT NULL
     AND valor <> '').
   · Acotada: pull con OPENQUERY (filtro remoto), dos columnas. Ventana -90 días
     cubre todas las auditorías afectadas (la más vieja es del 2026-06-01) y queda
     dentro de la retención de Orion. El JOIN por ID_Llamada restringe al conjunto
     auditado. COLLATE DATABASE_DEFAULT para el linked server.
   · Transaccional con guarda: si se actualizan más filas de las esperadas, ROLLBACK.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario con UPDATE sobre calidad.Auditorias y
   acceso al linked server ORION_LINK (p. ej. Bot). Revisar los PRINT; si dice
   "ABORT", NO commitea: revisar y reintentar.
   ============================================================================ */

SET XACT_ABORT ON;
SET NOCOUNT ON;

DECLARE @tope_filas INT = 10000;  -- guarda de seguridad (esperado ≈7340)

/* -- 1) Mapa ID_Llamada -> COMENT (no vacío), traído de Orion --------------- */
IF OBJECT_ID('tempdb..#orion_com') IS NOT NULL DROP TABLE #orion_com;

SELECT IdLlamada, Comentario
INTO #orion_com
FROM OPENQUERY(ORION_LINK, '
    SELECT
        CAST(e.ID_Llamada AS VARCHAR(50)) AS IdLlamada,
        com.Valor AS Comentario
    FROM ContactCenter.dbo.EstadPorLlamada e
    JOIN ContactCenter.dbo.DatosPorLlamada com
        ON com.Call_Id = e.ID_Llamada AND com.Clave = ''COMENT''
    WHERE e.Fecha >= DATEADD(DAY, -90, GETDATE())
      AND com.Valor IS NOT NULL
      AND com.Valor <> ''''
');

CREATE INDEX ix_tmp_com_idllamada ON #orion_com (IdLlamada);

/* -- 2) Diagnóstico previo ------------------------------------------------- */
DECLARE @a_llenar INT;
SELECT @a_llenar = COUNT(*)
FROM calidad.Auditorias a
JOIN #orion_com o ON o.IdLlamada COLLATE DATABASE_DEFAULT = a.IdAplicativo
WHERE a.EmpresaID = 5 AND a.extras IS NULL;
PRINT CONCAT('Extras a rellenar: ', ISNULL(@a_llenar, 0));

/* -- 3) Update transaccional con guarda ------------------------------------ */
BEGIN TRAN;

UPDATE a
SET a.extras = '{"Comentario del Agente": "'
             + REPLACE(STRING_ESCAPE(o.Comentario, 'json'), '\/', '/')
             + '"}'
FROM calidad.Auditorias a
JOIN #orion_com o ON o.IdLlamada COLLATE DATABASE_DEFAULT = a.IdAplicativo
WHERE a.EmpresaID = 5
  AND a.extras IS NULL
  AND o.Comentario IS NOT NULL;

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

DROP TABLE #orion_com;
