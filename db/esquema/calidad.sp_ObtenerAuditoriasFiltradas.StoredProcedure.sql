-- StoredProcedure [calidad].[sp_ObtenerAuditoriasFiltradas]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[sp_ObtenerAuditoriasFiltradas]') AND type in (N'P', N'PC'))
BEGIN
EXEC dbo.sp_executesql @statement = N'CREATE PROCEDURE [calidad].[sp_ObtenerAuditoriasFiltradas] AS' 
END
GO


/* ---------------------------------------------------------------------------
   2) El SP
   --------------------------------------------------------------------------- */
ALTER   PROCEDURE [calidad].[sp_ObtenerAuditoriasFiltradas]
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
    -- Ya no hace falta arrastrar operadorUsuario/EmpresaID: la persona no se
    -- resuelve más acá (ver el bloque de Agente/Equipo más abajo).
    CREATE TABLE #Base (
        AuditoriaID     INT NOT NULL PRIMARY KEY,
        FechaAuditoria  DATETIME2 NOT NULL
    );

    -- El patrón (@X IS NULL OR col = @X) con OPTION (RECOMPILE) le permite al
    -- optimizador descartar en compilación los filtros que no vinieron, que es
    -- exactamente lo que antes se lograba concatenando el WHERE a mano.
    INSERT INTO #Base (AuditoriaID, FechaAuditoria)
    SELECT a.AuditoriaID, a.FechaAuditoria
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
    -- 5) SELECT final (dinámico solo por las columnas del PIVOT)
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
    /* Agente/Equipo. La persona sale CONGELADA de la auditoría; el equipo se
       sigue resolviendo por fecha (es el supervisor que tenía ese día, y no
       cambia con el tiempo porque la fecha de la interacción es fija). */
    OUTER APPLY (
        SELECT TOP 1 z.Nombre, z.Apellido, z.LegajoNum, z.EquipoID
        FROM (
            /* (a) Camino normal: la auditoría ya sabe de quién es. Seek a nomina
                   por PK + seek al equipo vigente por
                   IX_operadores_legajo_estado_fecha. */
            SELECT n.nombre AS Nombre, n.apellido AS Apellido, n.legajo AS LegajoNum,
                   op.equipo_id AS EquipoID, 0 AS Prioridad
            FROM nomina n
            OUTER APPLY (
                SELECT TOP 1 o.equipo_id
                FROM operadores o
                WHERE o.legajo_id = n.id
                  AND o.estado = 1
                  AND A_Ext.[fecha_interaccion] >= o.fecha_desde
                  AND (A_Ext.[fecha_interaccion] < o.fecha_hasta OR o.fecha_hasta IS NULL)
                ORDER BY o.fecha_desde DESC
            ) op
            WHERE n.id = A_Ext.OperadorNominaID

            UNION ALL

            /* (b) Auditoría sin congelar (anterior al backfill, o guardada por una
                   versión del backend que todavía no escribe la columna): se
                   resuelve con la misma regla que usó el backfill. El filtro por
                   OperadorNominaID IS NULL deja esta rama sin ejecutar en el resto
                   de las filas. */
            SELECT r.Nombre, r.Apellido, r.Legajo, r.EquipoID, 1
            FROM calidad.fn_ResolverOperadorAuditoria(
                     A_Ext.operadorUsuario, A_Ext.EmpresaID, A_Ext.[fecha_interaccion]) r
            WHERE A_Ext.OperadorNominaID IS NULL
        ) z
        ORDER BY z.Prioridad
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
