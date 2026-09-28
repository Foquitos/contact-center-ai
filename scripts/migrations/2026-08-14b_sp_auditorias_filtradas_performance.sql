/* ===========================================================================
   2026-08-14b — sp_ObtenerAuditoriasFiltradas: performance + paginado opcional
   ===========================================================================

   QUÉ CAMBIA Y POR QUÉ
   --------------------
   El SP es el motor de "Auditorías Realizadas", de la Bandeja, del scheduler
   (tasks.py) y del export de batch (Auditor.py). Su costo NO estaba en escanear
   (89k auditorías, 1,77M detalles son volúmenes chicos) sino en lo que hacía
   UNA VEZ POR FILA:

   1. El OUTER APPLY que resuelve Agente/Equipo corre por cada fila devuelta
      (6.796 en una búsqueda de 30 días) y adentro tiene un EXISTS que joinea
      operadores × campanas × Normalizador_calidad_omnia comparando strings con
      LTRIM/RTRIM + COLLATE (no usa índices). Ahora se resuelve UNA VEZ por cada
      pareja (operadorUsuario, EmpresaID) distinta — 315 en esos mismos 30 días.
      La resolución es EQUIVALENTE, no aproximada: cuando el criterio de empresa
      deja más de una persona candidata (mismo `usuario` en varias empresas), esas
      filas caen al APPLY original completo, que sigue textual acá abajo.

   2. Los detalles (calidad.AuditoriaDetalles) se leían DOS veces: una para
      descubrir las columnas del PIVOT y otra para pivotear. Ahora se materializan
      una sola vez en #Det.

   3. El WHERE se armaba concatenando strings y se pasaba dos veces a
      sp_executesql. Ahora el set base se materializa una sola vez en #Base con el
      patrón (@X IS NULL OR col = @X) + OPTION (RECOMPILE), que el optimizador
      resuelve igual de bien y deja el SP legible.

   4. NUEVO (opcional): @Offset/@Fetch para paginar en SQL. Si se piden, el PIVOT,
      la resolución de agente y la transcripción se hacen SOLO sobre las filas de
      esa página, y la respuesta suma la columna [__Total] con el total de filas
      del set completo. Las columnas del PIVOT se siguen calculando sobre el set
      completo para que NO cambien entre páginas.

   CORRECCIÓN DE UN BUG DE DUPLICACIÓN (ojo con esto)
   --------------------------------------------------
   El LEFT JOIN a calidad.transcripciones era 1:N: hay 128 IdAplicativo con más de
   una transcripción, y esas auditorías salían DUPLICADAS en el listado, en el
   Excel y en la Bandeja. Ahora se toma la más reciente con OUTER APPLY TOP 1, así
   que esas auditorías pasan a aparecer una sola vez (los conteos de la Bandeja
   pueden bajar levemente: es el número correcto).

   COMPATIBILIDAD
   --------------
   Los parámetros nuevos son opcionales y su ausencia deja el comportamiento igual
   que antes: mismas columnas, mismo orden y mismo contenido. tasks.py, Auditor.py
   y bandeja.py siguen llamándolo tal cual. La única diferencia de forma es
   [__Total], que solo aparece si se pide paginado (el backend lo saca antes de
   responderle al front). El desempate del ORDER BY suma AuditoriaID DESC para que
   paginar sea estable.

   ROLLBACK
   --------
   scripts/migrations/2026-08-14b_sp_auditorias_filtradas_ROLLBACK.sql tiene la
   definición anterior tal cual estaba. Es idempotente: se puede correr sin miedo.
   =========================================================================== */

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

/* ---------------------------------------------------------------------------
   1) Índice para el set base
   ---------------------------------------------------------------------------
   La pantalla siempre filtra por plantilla + rango de fechas, y el SP filtra
   SIEMPRE por IsActive = 1 — que no estaba en ningún índice, así que cada fila
   candidata se resolvía con un key lookup al clustered. Este índice cubre el
   paso de armado de #Base sin lookups. Son ~89k filas: pesa poco y se crea en
   segundos. */
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = 'IX_Auditorias_Base_Busqueda'
                 AND object_id = OBJECT_ID('calidad.Auditorias'))
BEGIN
    CREATE NONCLUSTERED INDEX IX_Auditorias_Base_Busqueda
        ON calidad.Auditorias (PlantillaID, IsActive, FechaAuditoria DESC)
        INCLUDE (CampanaID, EmpresaID, AuditorUsuarioID, IdAplicativo,
                 operadorUsuario, fecha_interaccion);
END
GO


/* ---------------------------------------------------------------------------
   2) El SP
   --------------------------------------------------------------------------- */
CREATE OR ALTER PROCEDURE [calidad].[sp_ObtenerAuditoriasFiltradas]
    @AuditorUsuarioID INT = NULL,
    @CampanaID INT = NULL,
    @EmpresaID INT = NULL,
    @PlantillaID INT = NULL,
    @FechaDesde DATETIME2 = NULL,
    @FechaHasta DATETIME2 = NULL,
    @FechaInteraccionDesde DATETIME2 = NULL,
    @FechaInteraccionHasta DATETIME2 = NULL,
    @IdAplicativo VARCHAR(MAX) = NULL,
    @IncluirTranscripcion BIT = 0,
    @IncluirResponseThoughts BIT = 0,
    -- Paginado opcional. Sin @Fetch, el SP devuelve el set completo como siempre.
    @Offset INT = NULL,
    @Fetch  INT = NULL
AS
BEGIN
    -- NOCOUNT ON no es cosmético: sin él, los INSERT de las tablas temporales
    -- emiten result sets de conteo que le llegan a pyodbc antes del verdadero.
    SET NOCOUNT ON;

    DECLARE @FechaDesde_Start DATETIME2 =
        CASE WHEN @FechaDesde IS NOT NULL THEN CAST(CAST(@FechaDesde AS DATE) AS DATETIME2) END;
    DECLARE @FechaHasta_NextDay DATETIME2 =
        CASE WHEN @FechaHasta IS NOT NULL THEN DATEADD(DAY, 1, CAST(CAST(@FechaHasta AS DATE) AS DATETIME2)) END;
    DECLARE @FechaInteraccionDesde_Start DATETIME2 =
        CASE WHEN @FechaInteraccionDesde IS NOT NULL THEN CAST(CAST(@FechaInteraccionDesde AS DATE) AS DATETIME2) END;
    DECLARE @FechaInteraccionHasta_NextDay DATETIME2 =
        CASE WHEN @FechaInteraccionHasta IS NOT NULL THEN DATEADD(DAY, 1, CAST(CAST(@FechaInteraccionHasta AS DATE) AS DATETIME2)) END;

    DECLARE @Paginado BIT = CASE WHEN @Fetch IS NOT NULL AND @Fetch > 0 THEN 1 ELSE 0 END;

    -- =====================================================================
    -- 1) Set base: una sola pasada sobre calidad.Auditorias.
    -- =====================================================================
    CREATE TABLE #Base (
        AuditoriaID     INT NOT NULL PRIMARY KEY,
        FechaAuditoria  DATETIME2 NOT NULL,
        operadorUsuario VARCHAR(255) NULL,
        EmpresaKey      INT NOT NULL          -- ISNULL(EmpresaID, -1): se usa para joins
    );

    -- El patrón (@X IS NULL OR col = @X) con OPTION (RECOMPILE) le permite al
    -- optimizador descartar en compilación los filtros que no vinieron, que es
    -- exactamente lo que antes se lograba concatenando el WHERE a mano.
    INSERT INTO #Base (AuditoriaID, FechaAuditoria, operadorUsuario, EmpresaKey)
    SELECT a.AuditoriaID, a.FechaAuditoria, a.operadorUsuario, ISNULL(a.EmpresaID, -1)
    FROM calidad.Auditorias a
    WHERE a.IsActive = 1
      AND (@AuditorUsuarioID IS NULL     OR a.AuditorUsuarioID = @AuditorUsuarioID)
      AND (@CampanaID IS NULL            OR a.CampanaID = @CampanaID)
      AND (@EmpresaID IS NULL            OR a.EmpresaID = @EmpresaID)
      AND (@PlantillaID IS NULL          OR a.PlantillaID = @PlantillaID)
      AND (@FechaDesde IS NULL           OR a.FechaAuditoria >= @FechaDesde_Start)
      AND (@FechaHasta IS NULL           OR a.FechaAuditoria <  @FechaHasta_NextDay)
      AND (@FechaInteraccionDesde IS NULL OR a.fecha_interaccion >= @FechaInteraccionDesde_Start)
      AND (@FechaInteraccionHasta IS NULL OR a.fecha_interaccion <  @FechaInteraccionHasta_NextDay)
      AND (@IdAplicativo IS NULL         OR a.IdAplicativo IN (SELECT value FROM STRING_SPLIT(@IdAplicativo, ',')))
    OPTION (RECOMPILE);

    DECLARE @Total INT = @@ROWCOUNT;

    -- =====================================================================
    -- 2) Página a devolver (o todo el set si no se pidió paginado).
    -- =====================================================================
    CREATE TABLE #Pagina (AuditoriaID INT NOT NULL PRIMARY KEY);

    IF @Paginado = 1
        INSERT INTO #Pagina (AuditoriaID)
        SELECT b.AuditoriaID
        FROM #Base b
        ORDER BY b.FechaAuditoria DESC, b.AuditoriaID DESC
        OFFSET ISNULL(@Offset, 0) ROWS FETCH NEXT @Fetch ROWS ONLY;
    ELSE
        INSERT INTO #Pagina (AuditoriaID)
        SELECT b.AuditoriaID FROM #Base b;

    -- =====================================================================
    -- 3) Detalles de las filas a devolver (única lectura de la tabla grande).
    -- =====================================================================
    SELECT ad.AuditoriaID,
           att.NombreAtributo,
           ad.ValorResultado,
           ad.Orden
    INTO #Det
    FROM #Pagina p
    JOIN calidad.AuditoriaDetalles ad ON ad.AuditoriaID = p.AuditoriaID
    JOIN calidad.Atributos att        ON att.AtributoID = ad.AtributoID;

    -- =====================================================================
    -- 4) Columnas dinámicas del PIVOT.
    --    Sin paginado salen de #Det (ya está en memoria). Con paginado hay que
    --    mirarlas sobre el set COMPLETO: si salieran de la página, la tabla
    --    cambiaría de columnas al pasar de página.
    -- =====================================================================
    DECLARE @PivotColumns NVARCHAR(MAX), @PivotColumnsSelect NVARCHAR(MAX);

    IF @Paginado = 1
        SELECT @PivotColumns       = STRING_AGG(CAST(QUOTENAME(NombreAtributo) AS NVARCHAR(MAX)), ',')
                                     WITHIN GROUP (ORDER BY MinOrden ASC),
               @PivotColumnsSelect = STRING_AGG(CAST('P.' + QUOTENAME(NombreAtributo) AS NVARCHAR(MAX)), ',')
                                     WITHIN GROUP (ORDER BY MinOrden ASC)
        FROM (
            SELECT att.NombreAtributo, MIN(ad.Orden) AS MinOrden
            FROM #Base b
            JOIN calidad.AuditoriaDetalles ad ON ad.AuditoriaID = b.AuditoriaID
            JOIN calidad.Atributos att        ON att.AtributoID = ad.AtributoID
            GROUP BY att.NombreAtributo
        ) AS OrderedAttributes;
    ELSE
        SELECT @PivotColumns       = STRING_AGG(CAST(QUOTENAME(NombreAtributo) AS NVARCHAR(MAX)), ',')
                                     WITHIN GROUP (ORDER BY MinOrden ASC),
               @PivotColumnsSelect = STRING_AGG(CAST('P.' + QUOTENAME(NombreAtributo) AS NVARCHAR(MAX)), ',')
                                     WITHIN GROUP (ORDER BY MinOrden ASC)
        FROM (
            SELECT NombreAtributo, MIN(Orden) AS MinOrden
            FROM #Det
            GROUP BY NombreAtributo
        ) AS OrderedAttributes;

    IF @PivotColumns IS NULL
    BEGIN
        -- Sin atributos no hay PIVOT posible: se devuelve el set vacío con la
        -- forma exacta del SELECT real (el front arma los encabezados con esto).
        -- [__Total] solo cuando se pidió paginado, para no cambiarle la forma a
        -- los llamadores de siempre (tasks.py / Auditor.py / bandeja.py).
        IF @Paginado = 1
        SELECT
            CAST(NULL AS INT) AS AuditoriaID,
            CAST(NULL AS INT) AS AuditorUsuarioID,
            CAST(NULL AS VARCHAR(255)) AS IdAplicativo,
            CAST(NULL AS VARCHAR(255)) AS operadorUsuario,
            CAST(NULL AS VARCHAR(255)) AS Equipo,
            CAST(NULL AS VARCHAR(255)) AS Agente,
            CAST(NULL AS VARCHAR(50)) AS Legajo,
            CAST(NULL AS VARCHAR(50)) AS sentido_interaccion,
            CAST(NULL AS NVARCHAR(255)) AS tipificacion_interaccion,
            CAST(NULL AS INT) AS duracion_segundos,
            CAST(NULL AS NVARCHAR(MAX)) AS comentario_interaccion,
            CAST(NULL AS VARCHAR(20)) AS fecha_interaccion,
            CAST(NULL AS VARCHAR(20)) AS FechaAuditoria,
            CAST(NULL AS VARCHAR(255)) AS extras,
            CAST(NULL AS DECIMAL(5,2)) AS PuntajeFinal,
            CAST(0 AS BIT) AS EsErrorCritico,
            CAST(0 AS INT) AS ExisteTranscripcion,
            CAST(0 AS INT) AS ExisteResponseThoughts,
            CAST(NULL AS NVARCHAR(MAX)) AS TranscripcionJSON,
            CAST(NULL AS NVARCHAR(MAX)) AS ResponseThoughts,
            CAST(@Total AS INT) AS [__Total]
        WHERE 1 = 0;
        ELSE
        SELECT
            CAST(NULL AS INT) AS AuditoriaID,
            CAST(NULL AS INT) AS AuditorUsuarioID,
            CAST(NULL AS VARCHAR(255)) AS IdAplicativo,
            CAST(NULL AS VARCHAR(255)) AS operadorUsuario,
            CAST(NULL AS VARCHAR(255)) AS Equipo,
            CAST(NULL AS VARCHAR(255)) AS Agente,
            CAST(NULL AS VARCHAR(50)) AS Legajo,
            CAST(NULL AS VARCHAR(50)) AS sentido_interaccion,
            CAST(NULL AS NVARCHAR(255)) AS tipificacion_interaccion,
            CAST(NULL AS INT) AS duracion_segundos,
            CAST(NULL AS NVARCHAR(MAX)) AS comentario_interaccion,
            CAST(NULL AS VARCHAR(20)) AS fecha_interaccion,
            CAST(NULL AS VARCHAR(20)) AS FechaAuditoria,
            CAST(NULL AS VARCHAR(255)) AS extras,
            CAST(NULL AS DECIMAL(5,2)) AS PuntajeFinal,
            CAST(0 AS BIT) AS EsErrorCritico,
            CAST(0 AS INT) AS ExisteTranscripcion,
            CAST(0 AS INT) AS ExisteResponseThoughts,
            CAST(NULL AS NVARCHAR(MAX)) AS TranscripcionJSON,
            CAST(NULL AS NVARCHAR(MAX)) AS ResponseThoughts
        WHERE 1 = 0;

        RETURN;
    END

    -- =====================================================================
    -- 5) Resolución de Agente/Equipo, memoizada por (operadorUsuario, EmpresaID)
    -- =====================================================================
    -- Parejas distintas de las filas a devolver: es el conjunto sobre el que hay
    -- que pagar el EXISTS caro (cientos, no miles).
    SELECT DISTINCT b.operadorUsuario, b.EmpresaKey
    INTO #Par
    FROM #Base b
    JOIN #Pagina p ON p.AuditoriaID = b.AuditoriaID
    WHERE b.operadorUsuario IS NOT NULL;

    -- Candidatos con el criterio 1 del ORDER BY original: preferir la persona
    -- cuyo operador pertenece a un cliente que el normalizador mapea a la
    -- EmpresaID de la auditoría. Se evalúa una vez por pareja.
    SELECT DISTINCT
           pr.operadorUsuario,
           pr.EmpresaKey,
           n.id       AS NominaID,
           n.nombre   AS Nombre,
           n.apellido AS Apellido,
           n.legajo   AS LegajoNum,
           CASE WHEN EXISTS (
                    SELECT 1
                    FROM operadores oe
                    JOIN dbo.campanas cmp ON cmp.id = oe.campana_id
                    JOIN calidad.Normalizador_calidad_omnia nz
                         ON LTRIM(RTRIM(nz.Cliente_omnia)) COLLATE DATABASE_DEFAULT
                          = LTRIM(RTRIM(cmp.cliente))      COLLATE DATABASE_DEFAULT
                    WHERE oe.legajo_id = n.id
                      AND nz.id_empresa_calidad = pr.EmpresaKey
                ) THEN 0 ELSE 1 END AS RankEmpresa
    INTO #Cand
    FROM #Par pr
    JOIN usuarios u ON u.usuario = pr.operadorUsuario
    JOIN nomina   n ON n.id = u.nomina_id;

    -- Nos quedamos con los candidatos del mejor rango. Si queda UNO solo, la
    -- persona está determinada sin mirar la fecha de la interacción y se puede
    -- reusar para todas sus filas. Si quedan varios, el criterio 2 del ORDER BY
    -- original (operador vigente a la fecha) puede elegir distinto FILA POR FILA:
    -- esas parejas se marcan y caen al APPLY original, sin atajos.
    SELECT operadorUsuario, EmpresaKey, NominaID, Nombre, Apellido, LegajoNum,
           COUNT(*) OVER (PARTITION BY operadorUsuario, EmpresaKey) AS CandidatosMejorRank
    INTO #Persona
    FROM (
        SELECT c.*,
               RANK() OVER (PARTITION BY c.operadorUsuario, c.EmpresaKey ORDER BY c.RankEmpresa) AS rk
        FROM #Cand c
    ) z
    WHERE z.rk = 1;

    CREATE NONCLUSTERED INDEX IX_Persona ON #Persona (operadorUsuario, EmpresaKey);

    -- =====================================================================
    -- 6) SELECT final (dinámico solo por las columnas del PIVOT)
    -- =====================================================================
    DECLARE @ColTotal NVARCHAR(200) =
        CASE WHEN @Paginado = 1 THEN N', CAST(@Total_Param AS INT) AS [__Total]' ELSE N'' END;

    DECLARE @SQL NVARCHAR(MAX) = N'
    WITH DatosPivot AS (
        SELECT AuditoriaID, ' + @PivotColumns + N'
        FROM (
            SELECT AuditoriaID, NombreAtributo, ValorResultado FROM #Det
        ) AS SourceTable
        PIVOT (
            MAX(ValorResultado)
            FOR NombreAtributo IN (' + @PivotColumns + N')
        ) AS PivotTable
    )
    SELECT
        P.AuditoriaID,
        A_Ext.AuditorUsuarioID,
        A_Ext.IdAplicativo,
        A_Ext.operadorUsuario,
        e.nombre + '', '' + e.apellido AS Equipo,
        ag.Nombre + '', '' + ag.Apellido AS Agente,
        ag.LegajoNum AS Legajo,
        A_Ext.sentido_interaccion,
        A_Ext.tipificacion_interaccion,
        A_Ext.duracion_segundos,
        A_Ext.comentario_interaccion,
        CONVERT(VARCHAR(19), A_Ext.[fecha_interaccion], 120) AS [fecha_interaccion],
        CONVERT(VARCHAR(19), A_Ext.FechaAuditoria, 120) AS FechaAuditoria,
        A_Ext.extras,
        A_Ext.PuntajeFinal,
        A_Ext.EsErrorCritico,
        ' + @PivotColumnsSelect + N',
        CASE WHEN tex.ID IS NOT NULL THEN 1 ELSE 0 END AS ExisteTranscripcion,
        CASE WHEN A_Ext.response_thoughts IS NOT NULL THEN 1 ELSE 0 END AS ExisteResponseThoughts,
        tseg.segments AS TranscripcionJSON,
        CASE WHEN @IncluirResponseThoughts_Param = 1 THEN A_Ext.response_thoughts ELSE NULL END AS ResponseThoughts'
        + @ColTotal + N'
    FROM DatosPivot P
    INNER JOIN calidad.Auditorias A_Ext ON A_Ext.AuditoriaID = P.AuditoriaID
    OUTER APPLY (
        SELECT TOP 1 z.Nombre, z.Apellido, z.LegajoNum, z.EquipoID
        FROM (
            /* (a) Camino memoizado: la pareja resuelve a una sola persona, así que
                   lo único que queda por fila es el equipo vigente a la fecha de la
                   interacción — un seek cubierto por IX_operadores_legajo_estado_fecha. */
            SELECT pm.Nombre, pm.Apellido, pm.LegajoNum, op.equipo_id AS EquipoID
            FROM #Persona pm
            OUTER APPLY (
                SELECT TOP 1 o.equipo_id
                FROM operadores o
                WHERE o.legajo_id = pm.NominaID
                  AND o.estado = 1
                  AND A_Ext.[fecha_interaccion] >= o.fecha_desde
                  AND (A_Ext.[fecha_interaccion] < o.fecha_hasta OR o.fecha_hasta IS NULL)
                ORDER BY o.fecha_desde DESC
            ) op
            WHERE pm.operadorUsuario = A_Ext.operadorUsuario
              AND pm.EmpresaKey = ISNULL(A_Ext.EmpresaID, -1)
              AND pm.CandidatosMejorRank = 1

            UNION ALL

            /* (b) Camino original, textual, para las parejas ambiguas (el mismo
                   usuario resuelve a varias personas empatadas en el criterio de
                   empresa). El EXISTS de arriba corta esta rama para el resto. */
            SELECT amb.Nombre, amb.Apellido, amb.LegajoNum, amb.EquipoID
            FROM (
                SELECT TOP 1
                    n.nombre    AS Nombre,
                    n.apellido  AS Apellido,
                    n.legajo    AS LegajoNum,
                    o.equipo_id AS EquipoID
                FROM usuarios u
                JOIN nomina n ON u.nomina_id = n.id
                LEFT JOIN operadores o ON o.legajo_id = n.id
                    AND o.estado = 1
                    AND A_Ext.[fecha_interaccion] >= o.fecha_desde
                    AND (A_Ext.[fecha_interaccion] < o.fecha_hasta OR o.fecha_hasta IS NULL)
                WHERE u.usuario = A_Ext.operadorUsuario
                  AND EXISTS (
                        SELECT 1 FROM #Persona pa
                        WHERE pa.operadorUsuario = A_Ext.operadorUsuario
                          AND pa.EmpresaKey = ISNULL(A_Ext.EmpresaID, -1)
                          AND pa.CandidatosMejorRank > 1
                  )
                ORDER BY
                    CASE WHEN EXISTS (
                            SELECT 1
                            FROM operadores oe
                            JOIN dbo.campanas cmp ON cmp.id = oe.campana_id
                            JOIN calidad.Normalizador_calidad_omnia nz
                                 ON LTRIM(RTRIM(nz.Cliente_omnia)) COLLATE DATABASE_DEFAULT
                                  = LTRIM(RTRIM(cmp.cliente))      COLLATE DATABASE_DEFAULT
                            WHERE oe.legajo_id = n.id
                              AND nz.id_empresa_calidad = A_Ext.EmpresaID
                         ) THEN 0 ELSE 1 END,
                    CASE WHEN o.legajo_id IS NOT NULL THEN 0 ELSE 1 END,
                    o.fecha_desde DESC
            ) amb
        ) z
    ) ag
    LEFT JOIN equipos e ON e.id = ag.EquipoID
    /* Existencia de transcripción: seek al índice por IdAplicativo, sin tocar el
       LOB. Antes era un LEFT JOIN 1:N que DUPLICABA la auditoría cuando el mismo
       IdAplicativo tenía más de una transcripción (128 casos hoy). */
    OUTER APPLY (
        SELECT TOP 1 T.ID
        FROM [Acme].[calidad].[transcripciones] T
        WHERE T.IdAplicativo = A_Ext.IdAplicativo
        ORDER BY T.ID DESC
    ) tex
    /* El texto de la transcripción solo se lee si lo pidieron: el filtro por
       parámetro deja la rama sin ejecutar en las búsquedas normales. */
    OUTER APPLY (
        SELECT TOP 1 T2.segments
        FROM [Acme].[calidad].[transcripciones] T2
        WHERE @IncluirTranscripcion_Param = 1
          AND T2.IdAplicativo = A_Ext.IdAplicativo
        ORDER BY T2.ID DESC
    ) tseg
    ORDER BY A_Ext.FechaAuditoria DESC, A_Ext.AuditoriaID DESC
    OPTION (RECOMPILE);';

    EXEC sp_executesql @SQL,
        N'@IncluirTranscripcion_Param BIT, @IncluirResponseThoughts_Param BIT, @Total_Param INT',
        @IncluirTranscripcion_Param = @IncluirTranscripcion,
        @IncluirResponseThoughts_Param = @IncluirResponseThoughts,
        @Total_Param = @Total;
END
GO
