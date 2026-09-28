/* ============================================================================
   Feature — Transcripciones por el tier FLEX de Gemini (modo 'flex' en el libro de IA)
   Fecha: 2026-08-25
   Autor: equipo Acme

   QUÉ CAMBIA
   ----------
   pagina_web.vw_IA_Uso_Costos — la única fuente de costo — aplicaba el descuento del
   50% SOLO al modo 'batch'. Desde hoy las transcripciones que se piden de a una desde
   el reproductor de audio se resuelven con el tier **Flex** de Gemini (sincrónico,
   respuesta en minutos), que Google factura exactamente igual que batch: "Flex
   inference is priced at 50% of the standard API".

   Sin este cambio, esas filas (modo = 'flex') se costearían al precio ESTÁNDAR y la
   pantalla de Gastos de IA mostraría el doble de lo que realmente se paga.

   La fórmula gemela del lado Python vive en
   AuditorIA/execution_log.py::MODOS_MITAD_DE_PRECIO — si se agrega otro modo con
   descuento hay que tocar los dos lugares o se contradicen entre sí.

   NO cambia nada de la tabla pagina_web.IA_Uso: `modo` ya es NVARCHAR(10) y 'flex'
   entra sin tocar el esquema. Las filas viejas (sync/batch) se costean igual que antes.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario con permiso de ALTER sobre las vistas del
   schema pagina_web. Idempotente (CREATE OR ALTER VIEW) y reversible: para volver
   atrás, correr la misma vista sin el WHEN de 'flex'.
   ============================================================================ */

SET XACT_ABORT ON;
GO

CREATE OR ALTER VIEW pagina_web.vw_IA_Uso_Costos
AS
SELECT
    u.id,
    u.fecha,
    YEAR(u.fecha)                                   AS anio,
    MONTH(u.fecha)                                  AS mes,
    CONVERT(CHAR(7), u.fecha, 126)                  AS anio_mes,   -- 'YYYY-MM'
    u.feature,
    u.modo,
    u.modelo,
    u.user_id,
    u.campana_id,
    u.empresa_id,
    COALESCE(u.input_tokens, 0)                     AS input_tokens,
    COALESCE(u.output_tokens, 0)                    AS output_tokens,
    COALESCE(u.thoughts_tokens, 0)                  AS thoughts_tokens,
    COALESCE(u.input_tokens, 0)
      + COALESCE(u.output_tokens, 0)
      + COALESCE(u.thoughts_tokens, 0)              AS total_tokens,
    p.modelo                                        AS modelo_tarifa,
    p.fecha_desde                                   AS tarifa_desde,
    CAST(
        (
            COALESCE(u.input_tokens, 0)   / 1000000.0 * p.input_usd_mtok
          + (COALESCE(u.output_tokens, 0) + COALESCE(u.thoughts_tokens, 0)) / 1000000.0 * p.output_usd_mtok
        )
        -- batch (asincrónico) y flex (sincrónico best-effort) valen lo mismo: la mitad
        -- del precio estándar.
        * CASE WHEN u.modo IN ('batch', 'flex') THEN 0.5 ELSE 1.0 END
    AS DECIMAL(18,6))                               AS costo_usd
FROM pagina_web.IA_Uso u
OUTER APPLY (
    SELECT TOP 1 pr.modelo, pr.fecha_desde, pr.input_usd_mtok, pr.output_usd_mtok
    FROM pagina_web.IA_Precios pr
    WHERE pr.modelo = u.modelo
      AND pr.fecha_desde <= CAST(u.fecha AS DATE)
    ORDER BY pr.fecha_desde DESC
) p;
GO
