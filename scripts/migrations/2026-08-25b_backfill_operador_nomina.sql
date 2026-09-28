/* ===========================================================================
   2026-08-25b — Backfill de calidad.Auditorias.OperadorNominaID
   ===========================================================================

   Congela, en las auditorías YA EXISTENTES, la persona que corresponde según el
   criterio corregido de `calidad.fn_ResolverOperadorAuditoria` (ver la migración
   2026-08-25, que hay que aplicar antes que esta).

   POR QUÉ ES SEGURO CORRERLA
   --------------------------
   * No borra ni pisa nada: escribe una columna que hoy está entera en NULL, y
     solo donde sigue en NULL (se puede cortar y retomar).
   * No cambia lo que se ve hasta que se aplique el SP de 2026-08-25c: hasta
     entonces el SP sigue resolviendo como siempre e ignora la columna.
   * Va por lotes de 5.000 con su propia transacción, para no tomarse la tabla
     entera (~96k filas activas) en una sola escritura.

   QUÉ ESPERAR
   -----------
   Sobre la medición de 10 días: de 2.006 auditorías con usuario ambiguo cambian
   10 de persona; el resto queda igual. El paso 1 imprime el "antes" y el paso 4
   el "después" con el detalle de las que cambiaron, para revisarlas a ojo.

   Es idempotente: correrla de nuevo solo completa lo que haya quedado en NULL.
   =========================================================================== */

SET NOCOUNT ON;
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

IF OBJECT_ID('calidad.fn_ResolverOperadorAuditoria') IS NULL
BEGIN
    RAISERROR('Falta calidad.fn_ResolverOperadorAuditoria: aplicar primero 2026-08-25_operador_reciclado.sql', 16, 1);
END
GO

/* ---------------------------------------------------------------------------
   1) Antes: cuántas auditorías cuelgan de un usuario reciclado
   --------------------------------------------------------------------------- */
IF OBJECT_ID('tempdb..#Ambiguos') IS NOT NULL DROP TABLE #Ambiguos;

SELECT u.usuario, COUNT(DISTINCT u.nomina_id) AS personas
INTO #Ambiguos
FROM usuarios u
WHERE u.usuario IS NOT NULL
GROUP BY u.usuario
HAVING COUNT(DISTINCT u.nomina_id) > 1;

CREATE CLUSTERED INDEX IX_Ambiguos ON #Ambiguos (usuario);

SELECT 'ANTES' AS Etapa,
       COUNT(*) AS auditorias_activas,
       SUM(CASE WHEN amb.usuario IS NOT NULL THEN 1 ELSE 0 END) AS con_usuario_reciclado,
       SUM(CASE WHEN a.OperadorNominaID IS NOT NULL THEN 1 ELSE 0 END) AS ya_congeladas
FROM calidad.Auditorias a
LEFT JOIN #Ambiguos amb ON amb.usuario = a.operadorUsuario
WHERE a.IsActive = 1;
GO

/* ---------------------------------------------------------------------------
   2) Foto de la atribución vigente, para poder comparar después
   ---------------------------------------------------------------------------
   Es la resolución ACTUAL del SP (criterio viejo), calculada solo sobre las
   auditorías con usuario reciclado: son las únicas que pueden cambiar. */
IF OBJECT_ID('tempdb..#Antes') IS NOT NULL DROP TABLE #Antes;

SELECT a.AuditoriaID, viejo.NominaID
INTO #Antes
FROM calidad.Auditorias a
JOIN #Ambiguos amb ON amb.usuario = a.operadorUsuario
OUTER APPLY (
    SELECT TOP 1 n.id AS NominaID
    FROM usuarios u
    JOIN nomina n ON n.id = u.nomina_id
    LEFT JOIN operadores o
           ON o.legajo_id = n.id
          AND o.estado = 1
          AND a.fecha_interaccion >= o.fecha_desde
          AND (a.fecha_interaccion < o.fecha_hasta OR o.fecha_hasta IS NULL)
    WHERE u.usuario = a.operadorUsuario
    ORDER BY CASE WHEN EXISTS (
                     SELECT 1
                     FROM operadores oe
                     JOIN dbo.campanas cmp ON cmp.id = oe.campana_id
                     JOIN calidad.Normalizador_calidad_omnia nz
                          ON LTRIM(RTRIM(nz.Cliente_omnia)) COLLATE DATABASE_DEFAULT
                           = LTRIM(RTRIM(cmp.cliente))      COLLATE DATABASE_DEFAULT
                     WHERE oe.legajo_id = n.id
                       AND nz.id_empresa_calidad = a.EmpresaID
                 ) THEN 0 ELSE 1 END,
             CASE WHEN o.legajo_id IS NOT NULL THEN 0 ELSE 1 END,
             o.fecha_desde DESC
) viejo
WHERE a.IsActive = 1;

CREATE CLUSTERED INDEX IX_Antes ON #Antes (AuditoriaID);
GO

/* ---------------------------------------------------------------------------
   3) Backfill por lotes
   ---------------------------------------------------------------------------
   El UPDATE escribe directo sobre la tabla con CROSS APPLY a la función: NO se
   usa `UPDATE ... FROM cte`, que solo puede escribir las columnas que el CTE
   expone en su SELECT (eso tiró producción abajo el 2026-08-25 con la cola de
   transcripciones).

   El corte del WHILE es el vaciado de #Pendientes, no el @@ROWCOUNT del UPDATE:
   un usuario que no resuelve a nadie no devuelve fila en el CROSS APPLY y, si el
   loop dependiera de las filas escritas, esas auditorías lo dejarían girando. */
IF OBJECT_ID('tempdb..#Pendientes') IS NOT NULL DROP TABLE #Pendientes;
IF OBJECT_ID('tempdb..#Lote') IS NOT NULL DROP TABLE #Lote;

SELECT a.AuditoriaID
INTO #Pendientes
FROM calidad.Auditorias a
WHERE a.OperadorNominaID IS NULL
  AND a.operadorUsuario IS NOT NULL;

CREATE CLUSTERED INDEX IX_Pendientes ON #Pendientes (AuditoriaID);

CREATE TABLE #Lote (AuditoriaID INT NOT NULL PRIMARY KEY);

DECLARE @Lote INT = 5000, @Escritas INT = 0, @Hechas INT = 0;

WHILE EXISTS (SELECT 1 FROM #Pendientes)
BEGIN
    DELETE FROM #Lote;

    INSERT INTO #Lote (AuditoriaID)
    SELECT TOP (@Lote) AuditoriaID FROM #Pendientes ORDER BY AuditoriaID;

    BEGIN TRANSACTION;

        UPDATE a
        SET a.OperadorNominaID = r.NominaID
        FROM calidad.Auditorias a
        JOIN #Lote l ON l.AuditoriaID = a.AuditoriaID
        CROSS APPLY calidad.fn_ResolverOperadorAuditoria(
            a.operadorUsuario, a.EmpresaID, a.fecha_interaccion) r;

        SET @Escritas = @@ROWCOUNT;

        DELETE p FROM #Pendientes p JOIN #Lote l ON l.AuditoriaID = p.AuditoriaID;

    COMMIT TRANSACTION;

    SET @Hechas += @Escritas;
END

SELECT 'BACKFILL' AS Etapa, @Hechas AS filas_escritas;
GO

/* ---------------------------------------------------------------------------
   4) Después: qué cambió realmente
   --------------------------------------------------------------------------- */
SELECT 'DESPUES' AS Etapa,
       COUNT(*) AS auditorias_activas,
       SUM(CASE WHEN a.OperadorNominaID IS NOT NULL THEN 1 ELSE 0 END) AS congeladas,
       SUM(CASE WHEN a.OperadorNominaID IS NULL AND a.operadorUsuario IS NOT NULL THEN 1 ELSE 0 END) AS sin_resolver
FROM calidad.Auditorias a
WHERE a.IsActive = 1;

SELECT TOP 200
       a.AuditoriaID, a.operadorUsuario, a.EmpresaID, a.fecha_interaccion,
       vieja.legajo + ' ' + vieja.apellido AS figuraba,
       nueva.legajo + ' ' + nueva.apellido AS pasa_a_figurar
FROM #Antes t
JOIN calidad.Auditorias a ON a.AuditoriaID = t.AuditoriaID
LEFT JOIN nomina vieja ON vieja.id = t.NominaID
LEFT JOIN nomina nueva ON nueva.id = a.OperadorNominaID
WHERE ISNULL(t.NominaID, -1) <> ISNULL(a.OperadorNominaID, -1)
ORDER BY a.AuditoriaID DESC;

DROP TABLE #Antes;
DROP TABLE #Ambiguos;
DROP TABLE #Pendientes;
DROP TABLE #Lote;
GO
