-- UserDefinedFunction [calidad].[fn_ResolverOperadorAuditoria]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[calidad].[fn_ResolverOperadorAuditoria]') AND type in (N'FN', N'IF', N'TF', N'FS', N'FT'))
BEGIN
execute dbo.sp_executesql @statement = N'

/* ---------------------------------------------------------------------------
   2) La resolución, en un solo lugar
   ---------------------------------------------------------------------------
   Es una función INLINE (RETURNS TABLE): el optimizador la expande dentro de la
   query que la llama, así que no paga el costo de una función escalar ni de una
   multi-statement. Por eso tampoco puede tener DECLARE — de ahí el CROSS APPLY
   que materializa la fecha efectiva.

   @Fecha NULL cae a la fecha de hoy: sirve para los llamadores que no tienen
   fecha de interacción (el aviso de calidad de Auditor.py), pero devuelve la
   asignación de HOY, no la del llamado. Pasar la fecha siempre que se la tenga. */
CREATE   FUNCTION [calidad].[fn_ResolverOperadorAuditoria]
(
    @usuario   VARCHAR(255),
    @EmpresaID INT,
    @Fecha     DATETIME
)
RETURNS TABLE
AS
RETURN
(
    SELECT TOP 1
        n.id         AS NominaID,
        n.nombre     AS Nombre,
        n.apellido   AS Apellido,
        n.legajo     AS Legajo,
        ov.equipo_id AS EquipoID,
        CASE WHEN ov.EnLaEmpresa = 1            THEN 1   -- vigente a la fecha + empresa auditada
             WHEN ov.Vigente = 1                THEN 2   -- vigente a la fecha, otra campaña
             WHEN hist.EstuvoEnLaEmpresa = 1    THEN 3   -- pasó por la empresa alguna vez
             WHEN cerca.Dist IS NOT NULL        THEN 4   -- solo proximidad temporal
             ELSE 5 END AS Criterio
    FROM usuarios u
    JOIN nomina n ON n.id = u.nomina_id
    /* La fecha efectiva, una sola vez (una inline TVF no admite DECLARE). */
    CROSS APPLY (SELECT ISNULL(@Fecha, CAST(SYSDATETIME() AS DATETIME)) AS f) p
    /* Asignación VIGENTE a la fecha de la interacción. Si esa fecha la agarra con
       más de una abierta, gana la de la empresa auditada. */
    OUTER APPLY (
        SELECT TOP 1
            1 AS Vigente,
            o.equipo_id,
            CASE WHEN nz.id_empresa_calidad IS NOT NULL THEN 1 ELSE 0 END AS EnLaEmpresa
        FROM operadores o
        LEFT JOIN dbo.campanas cmp
               ON cmp.id = o.campana_id
        LEFT JOIN calidad.Normalizador_calidad_omnia nz
               ON LTRIM(RTRIM(nz.Cliente_omnia)) COLLATE DATABASE_DEFAULT
                = LTRIM(RTRIM(cmp.cliente))      COLLATE DATABASE_DEFAULT
              AND nz.id_empresa_calidad = @EmpresaID
        WHERE o.legajo_id = n.id
          AND o.estado = 1
          AND p.f >= o.fecha_desde
          AND (p.f < o.fecha_hasta OR o.fecha_hasta IS NULL)
        ORDER BY CASE WHEN nz.id_empresa_calidad IS NOT NULL THEN 0 ELSE 1 END,
                 o.fecha_desde DESC
    ) ov
    /* Criterio viejo, degradado a red de contención: ¿pasó alguna vez por la
       empresa? Sin fecha ni estado, por eso empataba a todo el mundo. */
    OUTER APPLY (
        SELECT TOP 1 1 AS EstuvoEnLaEmpresa
        FROM operadores oe
        JOIN dbo.campanas cmp ON cmp.id = oe.campana_id
        JOIN calidad.Normalizador_calidad_omnia nz
             ON LTRIM(RTRIM(nz.Cliente_omnia)) COLLATE DATABASE_DEFAULT
              = LTRIM(RTRIM(cmp.cliente))      COLLATE DATABASE_DEFAULT
        WHERE oe.legajo_id = n.id
          AND nz.id_empresa_calidad = @EmpresaID
    ) hist
    /* Distancia de la asignación más cercana al llamado, prefiriendo las que
       empezaron ANTES (una asignación posterior no explica un llamado previo). */
    OUTER APPLY (
        SELECT TOP 1 ABS(DATEDIFF(DAY, o.fecha_desde, p.f)) AS Dist
        FROM operadores o
        WHERE o.legajo_id = n.id
        ORDER BY CASE WHEN o.fecha_desde <= p.f THEN 0 ELSE 1 END,
                 ABS(DATEDIFF(DAY, o.fecha_desde, p.f))
    ) cerca
    WHERE u.usuario = @usuario
    ORDER BY
        CASE WHEN ov.EnLaEmpresa = 1         THEN 0 ELSE 1 END,
        CASE WHEN ov.Vigente = 1             THEN 0 ELSE 1 END,
        CASE WHEN hist.EstuvoEnLaEmpresa = 1 THEN 0 ELSE 1 END,
        ISNULL(cerca.Dist, 2147483647),
        n.id
);
' 
END
GO
