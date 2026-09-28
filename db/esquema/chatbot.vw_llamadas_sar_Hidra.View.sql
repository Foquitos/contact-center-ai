-- View [chatbot].[vw_llamadas_sar_Hidra]
SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
IF NOT EXISTS (SELECT * FROM sys.views WHERE object_id = OBJECT_ID(N'[chatbot].[vw_llamadas_sar_Hidra]'))
EXEC dbo.sp_executesql @statement = N'
CREATE   VIEW [chatbot].[vw_llamadas_sar_Hidra] AS
WITH LlamadasHidra AS (
    SELECT
        d.idInteraccion, d.segmento, d.LoginId, d.Tipificación, d.inicio,
        d.Sentido, d.Duración, d.CRM,
        LEAD(d.inicio) OVER (PARTITION BY d.LoginId ORDER BY d.inicio) AS SiguienteInicio
    FROM [Acme].[dbo].[detalle_de_interacciones_por_campana_lote] d
    WHERE d.Campaña = ''HidraIN''
),
ConOperador AS (
    SELECT
        l.*,
        n.nombre, n.apellido,
        sar_user.usuario AS LegajoSar
    FROM LlamadasHidra l
    LEFT JOIN [Acme].[dbo].[usuarios] u1 ON l.LoginId = u1.usuario
    LEFT JOIN [Acme].[dbo].[nomina]   n  ON u1.nomina_id = n.id
    OUTER APPLY (
        SELECT TOP 1 u2.usuario
        FROM [Acme].[dbo].[usuarios] u2
        WHERE u2.nomina_id = n.id
          AND u2.usuario IN (SELECT DISTINCT Legajo FROM [Acme].[dbo].[Sar_Ingresos])
    ) sar_user
),
IngresosRelacionados AS (
    SELECT
        c.*,
        s.[Nro. ODT]      AS NroODT,
        s.Motivo          AS MotivoSar,
        s.Observacion     AS ObservacionSar,
        s.[Fecha de Ing.] AS FechaIngresoSar,
        ROW_NUMBER() OVER (PARTITION BY c.idInteraccion, c.segmento
                           ORDER BY s.[Fecha de Ing.]) AS RowNum
    FROM ConOperador c
    LEFT JOIN [Acme].[dbo].[Sar_Ingresos] s ON
        (
            -- CRM presente: enlace por Nro. ODT (dígitos tras el 2° guion)
            c.CRM IS NOT NULL AND LTRIM(RTRIM(c.CRM)) <> ''''
            AND s.[Nro. ODT] LIKE ''R%''
            AND LTRIM(RTRIM(c.CRM)) = s.ODT_Digitos
            -- La numeración del ODT rearranca cada año y el CRM no trae el año:
            -- se desambigua acotando el ingreso a la ventana del llamado.
            AND s.[Fecha de Ing.] <= DATEADD(MINUTE, 30, c.inicio)
            AND s.[Fecha de Ing.] > DATEADD(DAY, -180, c.inicio)
        )
        OR
        (
            -- CRM vacío: enlace por operador dentro de la ventana del llamado
            (c.CRM IS NULL OR LTRIM(RTRIM(c.CRM)) = '''')
            AND c.LegajoSar = s.Legajo
            AND s.[Fecha de Ing.] > DATEADD(SECOND, 30, c.inicio)
            AND s.[Fecha de Ing.] < CASE WHEN c.SiguienteInicio IS NOT NULL
                                         THEN DATEADD(SECOND, 15, c.SiguienteInicio)
                                         ELSE DATEADD(MINUTE, 30, c.inicio) END
        )
)
SELECT
    idInteraccion,
    segmento,
    LoginId          AS usuario_agente,
    nombre           AS nombre_agente,
    apellido         AS apellido_agente,
    inicio           AS fecha_hora_inicio,
    Sentido          AS sentido_llamada,
    Tipificación     AS tipificacion_arbol,
    Duración         AS duracion_segundos,
    NroODT           AS nro_odt,
    MotivoSar        AS motivo_sar,
    ObservacionSar   AS observacion_sar,
    FechaIngresoSar  AS fecha_ingreso_sar,
    CASE WHEN NroODT IS NOT NULL THEN 1 ELSE 0 END AS genero_odt
FROM IngresosRelacionados
WHERE RowNum = 1;
' 
GO
