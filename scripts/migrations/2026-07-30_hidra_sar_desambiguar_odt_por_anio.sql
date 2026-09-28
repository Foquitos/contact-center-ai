/* ============================================================================
   Hidra — Desambiguar el enlace llamada -> ingreso SAR por año del ODT
   Fecha: 2026-07-30
   Autor: equipo Acme

   EL PROBLEMA
   -----------
   Calidad reportó auditorías de Hidra donde el audio y la tipificación (lado
   Mitrol) eran correctos, pero TODO el bloque de SAR (Nro. ODT, Motivo,
   Observación) correspondía a OTRO caso.

   Causa: el Nro. ODT tiene formato '<prefijo>-<año>-<dígitos>' y la numeración
   REARRANCA CADA AÑO, pero la columna CRM de
   detalle_de_interacciones_por_campana_lote guarda SOLO los dígitos (sin año).
   Entonces los dígitos solos son ambiguos:

       CRM = '298761'  ->  R-2024-298761 (02/07/2024, Escape Vereda)
                           R-2025-298761 (03/07/2025, Escape Calzada)
                           R-2026-298761 (10/07/2026, Taponamiento Sin Desborde)

   Medido sobre Sar_Ingresos: 1.266.171 filas con ODT 'R%', de las cuales
   1.261.622 caen en grupos de dígitos repetidos. Y como el enlace resolvía el
   empate con ROW_NUMBER() ... ORDER BY [Fecha de Ing.] (ascendente), SIEMPRE
   se quedaba con el ODT MÁS VIEJO, o sea con el caso de 2024/2025.

   Contraste sobre las 1.964 auditorías de Hidra que hoy tienen comentario
   (14/06/2026 -> 20/07/2026): 0 coincidían con el ODT correcto.

   LA CORRECCIÓN
   -------------
   Acotar el ingreso a la ventana del llamado:
     - nunca posterior a Inicio + 30 minutos (margen para el ODT que el
       operador carga durante el llamado o al cerrarlo; sobre datos reales
       ninguno se carga más tarde que eso), y
     - nunca más viejo que 180 días (cubre las reiteraciones —el 99,9% de los
       enlaces reales cae dentro de 90 días— y queda muy por debajo del año en
       que se repite la numeración, así que elimina la colisión).

   El mismo fix va en AuditorIA/SQL_query.py -> get_filtered_data_Hidra (código),
   y acá en la vista del chatbot, que replica esa lógica.

   NOTA APARTE (no se arregla acá): la ingesta de SAR está detenida.
   Sar_Ingresos tiene datos hasta 2026-07-12 23:58 y Sar_Consultas hasta
   2026-07-12 23:11. Los llamados del 13/07 en adelante no tienen su ODT
   cargado, así que con este fix quedan SIN datos de SAR (NULL) en vez de con
   los de otro caso. Hay que retomar la carga.
   ============================================================================ */

USE [Acme];
GO

CREATE OR ALTER VIEW chatbot.vw_llamadas_sar_Hidra AS
WITH LlamadasHidra AS (
    SELECT
        d.idInteraccion, d.segmento, d.LoginId, d.Tipificación, d.inicio,
        d.Sentido, d.Duración, d.CRM,
        LEAD(d.inicio) OVER (PARTITION BY d.LoginId ORDER BY d.inicio) AS SiguienteInicio
    FROM [Acme].[dbo].[detalle_de_interacciones_por_campana_lote] d
    WHERE d.Campaña = 'HidraIN'
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
            c.CRM IS NOT NULL AND LTRIM(RTRIM(c.CRM)) <> ''
            AND s.[Nro. ODT] LIKE 'R%'
            AND LTRIM(RTRIM(c.CRM)) = s.ODT_Digitos
            -- La numeración del ODT rearranca cada año y el CRM no trae el año:
            -- se desambigua acotando el ingreso a la ventana del llamado.
            AND s.[Fecha de Ing.] <= DATEADD(MINUTE, 30, c.inicio)
            AND s.[Fecha de Ing.] > DATEADD(DAY, -180, c.inicio)
        )
        OR
        (
            -- CRM vacío: enlace por operador dentro de la ventana del llamado
            (c.CRM IS NULL OR LTRIM(RTRIM(c.CRM)) = '')
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
GO
