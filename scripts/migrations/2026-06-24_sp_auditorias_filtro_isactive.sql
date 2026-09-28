/* ============================================================================
   SP — sp_ObtenerAuditoriasFiltradas: filtrar por soft-delete (IsActive = 1)
   Fecha: 2026-06-24
   Autor: equipo Acme

   QUÉ CAMBIA
   ----------
   Se agregó calidad.Auditorias.IsActive (bit NOT NULL DEFAULT 1) para soft-delete.
   El SP ahora trae ÚNICAMENTE las auditorías activas (IsActive = 1) y NO devuelve
   la columna IsActive (sería siempre 1 en el resultado).

   Único cambio vs. la versión previa: el @WhereClause base pasa de
       N' WHERE 1 = 1 '
   a
       N' WHERE 1 = 1 AND a.IsActive = 1 '
   El alias `a` es calidad.Auditorias tanto en el cálculo de columnas pivote como
   en la consulta principal, así que el filtro aplica en ambos lugares.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario con DDL sobre el schema calidad.
   Idempotente: se recrea con CREATE OR ALTER.
   ============================================================================ */

SET XACT_ABORT ON;
GO

/* -- 2) SP que expone la columna a la bandeja y a /auditorias_realizadas ---- */
/* -- 2) SP que expone la columna a la bandeja y a /auditorias_realizadas ---- */
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
    @IncluirResponseThoughts BIT = 0
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @SQL NVARCHAR(MAX);
    DECLARE @PivotColumns NVARCHAR(MAX);
    DECLARE @PivotColumnsSelect NVARCHAR(MAX);
    DECLARE @WhereClause NVARCHAR(MAX) = N' WHERE 1 = 1 AND a.IsActive = 1 ';

    DECLARE @FechaDesde_Start DATETIME2 = CASE WHEN @FechaDesde IS NOT NULL THEN CAST(CAST(@FechaDesde AS DATE) AS DATETIME2) ELSE NULL END;
    DECLARE @FechaHasta_NextDay DATETIME2 = CASE WHEN @FechaHasta IS NOT NULL THEN DATEADD(DAY, 1, CAST(CAST(@FechaHasta AS DATE) AS DATETIME2)) ELSE NULL END;

    DECLARE @FechaInteraccionDesde_Start DATETIME2 = CASE WHEN @FechaInteraccionDesde IS NOT NULL THEN CAST(CAST(@FechaInteraccionDesde AS DATE) AS DATETIME2) ELSE NULL END;
    DECLARE @FechaInteraccionHasta_NextDay DATETIME2 = CASE WHEN @FechaInteraccionHasta IS NOT NULL THEN DATEADD(DAY, 1, CAST(CAST(@FechaInteraccionHasta AS DATE) AS DATETIME2)) ELSE NULL END;

    IF @AuditorUsuarioID IS NOT NULL     SET @WhereClause += N' AND a.AuditorUsuarioID = @AuditorUsuarioID_Param ';
    IF @CampanaID IS NOT NULL             SET @WhereClause += N' AND a.CampanaID = @CampanaID_Param ';
    IF @EmpresaID IS NOT NULL             SET @WhereClause += N' AND a.EmpresaID = @EmpresaID_Param ';
    IF @PlantillaID IS NOT NULL           SET @WhereClause += N' AND a.PlantillaID = @PlantillaID_Param ';
    IF @FechaDesde IS NOT NULL            SET @WhereClause += N' AND a.FechaAuditoria >= @FechaDesde_Param ';
    IF @FechaHasta IS NOT NULL            SET @WhereClause += N' AND a.FechaAuditoria < @FechaHasta_Param ';
    IF @FechaInteraccionDesde IS NOT NULL SET @WhereClause += N' AND a.fecha_interaccion >= @FechaInteraccionDesde_Param ';
    IF @FechaInteraccionHasta IS NOT NULL SET @WhereClause += N' AND a.fecha_interaccion < @FechaInteraccionHasta_Param ';
    IF @IdAplicativo IS NOT NULL          SET @WhereClause += N' AND a.IdAplicativo IN (SELECT value FROM STRING_SPLIT(@IdAplicativo_Param, '','')) ';

    -- Lote 1: Obtencion de columnas dinamicas
    DECLARE @GetColumnsSQL NVARCHAR(MAX) = N'
        SELECT
            @PivotColumns_Out = STRING_AGG(QUOTENAME(NombreAtributo), '','') WITHIN GROUP (ORDER BY MinOrden ASC),
            @PivotColumnsSelect_Out = STRING_AGG(''P.'' + QUOTENAME(NombreAtributo), '','') WITHIN GROUP (ORDER BY MinOrden ASC)
        FROM (
            SELECT
                att.NombreAtributo,
                MIN(ad.Orden) AS MinOrden
            FROM calidad.AuditoriaDetalles ad
            JOIN calidad.Auditorias a ON ad.AuditoriaID = a.AuditoriaID
            JOIN calidad.Atributos att ON ad.AtributoID = att.AtributoID
            ' + @WhereClause + N'
            GROUP BY att.NombreAtributo
        ) AS OrderedAttributes
        OPTION (RECOMPILE);';

    EXEC sp_executesql @GetColumnsSQL,
        N'@AuditorUsuarioID_Param INT, @CampanaID_Param INT, @EmpresaID_Param INT, @PlantillaID_Param INT, @FechaDesde_Param DATETIME2, @FechaHasta_Param DATETIME2, @FechaInteraccionDesde_Param DATETIME2, @FechaInteraccionHasta_Param DATETIME2, @IdAplicativo_Param VARCHAR(MAX), @PivotColumns_Out NVARCHAR(MAX) OUTPUT, @PivotColumnsSelect_Out NVARCHAR(MAX) OUTPUT',
        @AuditorUsuarioID, @CampanaID, @EmpresaID, @PlantillaID, @FechaDesde_Start, @FechaHasta_NextDay, @FechaInteraccionDesde_Start, @FechaInteraccionHasta_NextDay, @IdAplicativo,
        @PivotColumns OUTPUT, @PivotColumnsSelect OUTPUT;

    IF @PivotColumns IS NULL
    BEGIN
        SELECT
            CAST(NULL AS INT) AS AuditoriaID,
            CAST(NULL AS INT) AS AuditorUsuarioID,
            CAST(NULL AS VARCHAR(255)) AS IdAplicativo,
            CAST(NULL AS VARCHAR(255)) AS operadorUsuario,
            CAST(NULL AS VARCHAR(255)) AS Equipo,
            CAST(NULL AS VARCHAR(255)) AS Agente,
            CAST(NULL AS VARCHAR(50)) AS Legajo,
            CAST(NULL AS VARCHAR(255)) AS tipificacion,
            CAST(NULL AS VARCHAR(50)) AS sentido_interaccion,
            CAST(NULL AS INT) AS duracion_segundos,
            CAST(NULL AS NVARCHAR(MAX)) AS comentario_interaccion,
            CAST(NULL AS VARCHAR(20)) AS fecha_interaccion,
            CAST(NULL AS VARCHAR(20)) AS FechaAuditoria,
            CAST(NULL AS VARCHAR(255)) AS extras,
            CAST(NULL AS DECIMAL(5,2)) AS PuntajeFinal,
            CAST(0 AS BIT) AS EsErrorCritico,
            CAST(0 AS BIT) AS ExisteTranscripcion,
            CAST(0 AS BIT) AS ExisteResponseThoughts,
            CAST(NULL AS NVARCHAR(MAX)) AS TranscripcionJSON,
            CAST(NULL AS NVARCHAR(MAX)) AS ResponseThoughts
        WHERE 1 = 0;
        RETURN;
    END

    -- Lote 2: Consulta de Pivoteo Estrecho + JOIN Externo de campos LOB
    SET @SQL = N'
    WITH DatosPivot AS (
        SELECT
            AuditoriaID,
            ' + @PivotColumns + N'
        FROM (
            SELECT
                a.AuditoriaID,
                att.NombreAtributo,
                ad.ValorResultado
            FROM calidad.Auditorias a
            JOIN calidad.AuditoriaDetalles ad ON a.AuditoriaID = ad.AuditoriaID
            JOIN calidad.Atributos att ON ad.AtributoID = att.AtributoID
            ' + @WhereClause + N'
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
        e.nombre + '', '' + e.apellido as Equipo,
        ag.Nombre + '', '' + ag.Apellido as Agente,
        ag.LegajoNum as Legajo,
        A_Ext.sentido_interaccion,
        A_Ext.tipificacion_interaccion,
        A_Ext.duracion_segundos,
        A_Ext.comentario_interaccion,
        CONVERT(VARCHAR(19), A_Ext.[fecha_interaccion], 120) as [fecha_interaccion],
        CONVERT(VARCHAR(19), A_Ext.FechaAuditoria, 120) as FechaAuditoria,
        A_Ext.extras,
        A_Ext.PuntajeFinal,
        A_Ext.EsErrorCritico,
        ' + @PivotColumnsSelect + N',
        CASE WHEN T.ID IS NOT NULL THEN 1 ELSE 0 END AS ExisteTranscripcion,
        CASE WHEN A_Ext.response_thoughts IS NOT NULL THEN 1 ELSE 0 END AS ExisteResponseThoughts,
        CASE WHEN @IncluirTranscripcion_Param = 1 THEN T.segments ELSE NULL END AS TranscripcionJSON,
        CASE WHEN @IncluirResponseThoughts_Param = 1 THEN A_Ext.response_thoughts ELSE NULL END AS ResponseThoughts
    FROM DatosPivot P
    INNER JOIN calidad.Auditorias A_Ext ON P.AuditoriaID = A_Ext.AuditoriaID
    OUTER APPLY (
        SELECT TOP 1
            n.id        AS NominaID,
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
        ORDER BY
            -- 1) DESEMPATE POR EMPRESA: el mismo usuario de 5 digitos existe en varias
            --    empresas; priorizamos la persona cuyo operador pertenece a un cliente
            --    que el normalizador mapea a la EmpresaID de la auditoria.
            --    Se evalua sobre el historial completo de operadores (sin bound de fecha),
            --    porque la empresa es estable; el operador con bound de fecha de arriba
            --    se sigue usando solo para el Equipo vigente.
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
            -- 2) Luego, preferir un operador vigente al momento de la interaccion.
            CASE WHEN o.legajo_id IS NOT NULL THEN 0 ELSE 1 END,
            -- 3) Por ultimo, el operador mas reciente.
            o.fecha_desde DESC
    ) ag
    LEFT JOIN equipos e ON e.id = ag.EquipoID
    LEFT JOIN [Acme].[calidad].[transcripciones] T ON A_Ext.IdAplicativo = T.IdAplicativo
    ORDER BY A_Ext.FechaAuditoria DESC
    OPTION (RECOMPILE);';

    EXEC sp_executesql @SQL,
        N'@AuditorUsuarioID_Param INT, @CampanaID_Param INT, @EmpresaID_Param INT, @PlantillaID_Param INT, @FechaDesde_Param DATETIME2, @FechaHasta_Param DATETIME2, @FechaInteraccionDesde_Param DATETIME2, @FechaInteraccionHasta_Param DATETIME2, @IdAplicativo_Param VARCHAR(MAX), @IncluirTranscripcion_Param BIT, @IncluirResponseThoughts_Param BIT',
        @AuditorUsuarioID_Param = @AuditorUsuarioID,
        @CampanaID_Param = @CampanaID,
        @EmpresaID_Param = @EmpresaID,
        @PlantillaID_Param = @PlantillaID,
        @FechaDesde_Param = @FechaDesde_Start,
        @FechaHasta_Param = @FechaHasta_NextDay,
        @FechaInteraccionDesde_Param = @FechaInteraccionDesde_Start,
        @FechaInteraccionHasta_Param = @FechaInteraccionHasta_NextDay,
        @IdAplicativo_Param = @IdAplicativo,
        @IncluirTranscripcion_Param = @IncluirTranscripcion,
        @IncluirResponseThoughts_Param = @IncluirResponseThoughts;
END
GO
