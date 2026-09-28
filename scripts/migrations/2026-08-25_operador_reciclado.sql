/* ===========================================================================
   2026-08-25 — Usuarios reciclados: resolver bien de quién es la auditoría
   ===========================================================================

   EL PROBLEMA
   -----------
   `calidad.Auditorias` NO guarda a la persona: guarda el string de la plataforma
   (`operadorUsuario`, p.ej. el Agent ID de Avaya en CSV). La persona se resuelve
   RECIÉN AL LEER, en `sp_ObtenerAuditoriasFiltradas`, con
   `usuarios.usuario = operadorUsuario -> nomina`.

   Como los usuarios se RECICLAN (distintas plataformas, distintos clientes),
   `usuarios` tiene VARIAS filas con el mismo `usuario` apuntando a personas
   distintas — y sin fecha que diga desde cuándo es de cada una. Hoy hay 1.429
   usuarios reciclados y 18.603 auditorías activas colgando de un string ambiguo.

   El desempate actual (migración 2026-08-14b) es:
     1) ¿la persona estuvo ALGUNA VEZ en una campaña de la empresa auditada?
     2) ¿tiene asignación vigente a la fecha de la interacción?
     3) `o.fecha_desde DESC`
   El (1) no mira ni la fecha ni el `estado`, así que cualquiera que haya pasado
   por esa empresa empata; y el (3) premia al alta más NUEVA, que no tiene nada
   que ver con la campaña del llamado.

   Caso testigo (auditoría 98046, usuario '642409', empresa 10 = PAGONET,
   interacción del 2026-08-20):

     legajo 11276 SAYAGO   -> vigente en PAGONET / Retención Paygo  <- la correcta
     legajo  6470 PEREZ    -> vigente en VOLTARA / Ajustes-Lecturas  <- la que devuelve hoy
     legajo  7897 FUNES    -> de baja, pasó por PAGONET en 2024
     legajo  8206 CAVANAGH -> de baja, pasó por PAGONET en 2024

   Los 4 empatan en (1). En (2) quedan SAYAGO y PEREZ. En (3) gana PEREZ porque
   su alta en VOLTARA (2026-06-05) es más nueva que la de SAYAGO en PAGONET
   (2026-05-21). La campaña del llamado nunca entra en la decisión.

   QUÉ HACE ESTA MIGRACIÓN (aditiva: no toca el SP ni datos existentes)
   -------------------------------------------------------------------
   1. `calidad.fn_ResolverOperadorAuditoria` — una única fuente de verdad para la
      pregunta "quién era este usuario en esta empresa en esta fecha", con el
      criterio corregido. La usan el SP (2026-08-25c), el backfill (2026-08-25b)
      y el INSERT de auditorías (AuditorIA/sql_a_Claude.py).

   2. `calidad.Auditorias.OperadorNominaID` — la persona CONGELADA al auditar.
      Que la atribución dependa del estado de nómina "de hoy" es el defecto de
      fondo: la misma auditoría muestra una persona distinta según cuándo se la
      mire, y vuelve a romperse la próxima vez que se recicle el usuario. Con la
      columna, la auditoría queda atada a la persona que se resolvió en su
      momento y ya no se re-infiere nunca más.

   EL CRITERIO NUEVO, EN ORDEN
   ---------------------------
     1) Tenía asignación VIGENTE a la fecha de la interacción (`estado = 1`,
        entre `fecha_desde` y `fecha_hasta`) EN UNA CAMPAÑA DE LA EMPRESA
        AUDITADA. Esto es lo que resuelve el caso: es la persona que ese día
        estaba atendiendo esos llamados.
     2) Tenía asignación vigente a la fecha (cualquier campaña).
     3) Pasó alguna vez por esa empresa (el criterio viejo, como red de
        contención para nómina desfasada o gente ya desvinculada).
     4) La asignación más CERCANA en el tiempo a la interacción — preferendo las
        que empezaron antes del llamado. Reemplaza al `fecha_desde DESC`, que
        premiaba al alta más nueva aunque fuera de años después.
     5) `nomina.id`, solo para que el resultado sea determinístico.

   La columna `Criterio` que devuelve la función dice cuál de las 5 reglas ganó:
   sirve para auditar la atribución (1 = resuelto por vigencia+empresa, que es el
   caso confiable; 4 = pura proximidad, que es el que conviene mirar a ojo).

   MEDICIÓN PREVIA (ventana de 10 días, 2.006 auditorías ambiguas)
   --------------------------------------------------------------
   Cambian 10, todas en la misma dirección: llamados de PAGONET que hoy figuran a
   nombre de gente que ese día estaba en VOLTARA (usuarios '642409' y '605780').
   Ninguna corrección va en sentido contrario. El resto (1.996) queda igual.

   ORDEN DE APLICACIÓN
   -------------------
     2026-08-25   (este)  -> función + columna. No cambia nada de lo que se ve.
     2026-08-25b          -> backfill de las auditorías ya existentes.
     2026-08-25c          -> el SP pasa a usar la columna congelada.
   Es idempotente: se puede correr más de una vez.
   =========================================================================== */

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO

/* ---------------------------------------------------------------------------
   1) La persona congelada en la auditoría
   ---------------------------------------------------------------------------
   NULL = "todavía no resuelta" (auditoría vieja sin backfill, o resolución que
   no encontró a nadie). El SP de 2026-08-25c cae a la función en ese caso, así
   que la columna se puede agregar sin coordinar con el deploy del backend. */
IF NOT EXISTS (SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
               WHERE TABLE_SCHEMA = 'calidad' AND TABLE_NAME = 'Auditorias'
                 AND COLUMN_NAME = 'OperadorNominaID')
BEGIN
    ALTER TABLE calidad.Auditorias ADD OperadorNominaID INT NULL;
END
GO


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
CREATE OR ALTER FUNCTION calidad.fn_ResolverOperadorAuditoria
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
GO
