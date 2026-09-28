/* ============================================================================
   Costeo de los tokens cacheados (context caching de Gemini)
   Fecha: 2026-09-01
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   1. pagina_web.IA_Precios.cached_usd_mtok  — precio del token que Gemini cobra
      como cacheado, por millón, con la misma vigencia por fecha que el resto.
   2. calidad.AuditExecutionLog.cached_tokens — cuántos tokens de la corrida se
      cobraron a ese precio.
   3. pagina_web.vw_IA_Uso_Costos — la fórmula del costo pasa a descontarlos.

   POR QUÉ
   -------
   Desde el 2026-09-01 las auditorías mandan el bloque fijo de la plantilla
   (instrucción de sistema + consignas de los atributos) UNA vez a la caché de
   contexto en vez de repetirlo en cada llamado (ver AuditorIA/cache_plantillas.py).
   Eso es el 47% del input de auditorías y Gemini lo cobra ~10 veces más barato.

   El problema es que `cached_tokens` ya se venía guardando en pagina_web.IA_Uso
   pero NINGUNA fórmula de costo lo miraba: la vista cobra TODO el input a
   `input_usd_mtok`. Es decir que el ahorro se hace en la factura de Google pero
   la pantalla de gastos sigue mostrando el número viejo, y encima el costo
   reportado queda POR ENCIMA del real justo por lo que se ahorró (~US$0,80 por
   día con el volumen del 2026-09-01, ~US$25/mes).

   No es un problema nuevo del todo: `asistente_docs` cachea solo (implícito) desde
   hace meses y acumuló 5,1M de tokens cacheados en 30 días que la vista viene
   cobrando a precio lleno. Esta migración corrige las dos cosas de una.

   POR QUÉ TAMBIÉN EN AuditExecutionLog
   ------------------------------------
   La vista costea desde pagina_web.IA_Uso, pero el mail por corrida, el listado
   de corridas de /uso-ia y los cupos por campaña (app/cuotas.py) costean desde
   calidad.AuditExecutionLog, que solo tenía input/output/thoughts. Sin esta
   columna esos tres seguirían cobrando el input entero — y los cupos, que
   reservan plata contra un presupuesto, se comerían el cupo más rápido que el
   gasto real.

   OJO CON UNA TRAMPA DE LA API
   ----------------------------
   `prompt_token_count` de Gemini INCLUYE los tokens cacheados, así que
   input_tokens NO baja cuando la caché pega. Por eso la fórmula resta:
   (input - cached) a precio de input, + cached a precio de caché. Si llega una
   fila con cached > input, el CASE deja la primera parte en 0 en vez de restar
   plata, pero los cacheados se cobran igual: se consumieron de verdad.

   Ese caso NO es teórico. Hay filas de `asistente_docs` con cached_tokens
   cargado y input_tokens en NULL, que hoy se costean en cero. Con esta migración
   pasan a pagar sus cacheados, así que el costo reportado de ese uso sube unos
   centavos (US$27,26 -> US$27,38 en los últimos 30 días) mientras que el de los
   demás baja. Es la dirección correcta en los dos casos.

   AL APLICARLA CAMBIA EL HISTÓRICO
   --------------------------------
   La vista costea al vuelo, no guarda el costo: en cuanto se aplique, todo el
   histórico de /uso-ia se recalcula con la fórmula nueva. Es lo que se quiere
   (los tokens cacheados nunca costaron lo que la vista venía diciendo), pero
   conviene saberlo antes de comparar contra un reporte viejo.

   PRECIOS CARGADOS
   ----------------
   Los 3.x Flash cobran el token cacheado al 10% del input (US$0,075/M contra
   US$0,75/M mientras dure el precio promocional; US$0,15 contra US$1,50 desde
   2027-01-01). Los modelos viejos se cargan con la misma proporción para que el
   histórico quede consistente. Los modos batch y flex siguen valiendo la mitad de
   todo, igual que antes.

   IDEMPOTENTE: se puede correr más de una vez.
   ============================================================================ */

SET NOCOUNT ON;
GO

/* --- 1. Precio del token cacheado ---------------------------------------- */
IF COL_LENGTH('pagina_web.IA_Precios', 'cached_usd_mtok') IS NULL
BEGIN
    ALTER TABLE pagina_web.IA_Precios
        ADD cached_usd_mtok DECIMAL(10, 4) NULL;
    PRINT 'IA_Precios.cached_usd_mtok agregada.';
END
ELSE
    PRINT 'IA_Precios.cached_usd_mtok ya existía.';
GO

/* El 10% del input es la relación que publica Google para toda la familia Flash.
   Se aplica solo donde todavía no hay valor, para no pisar un precio corregido
   a mano. */
UPDATE pagina_web.IA_Precios
SET cached_usd_mtok = ROUND(input_usd_mtok * 0.10, 4)
WHERE cached_usd_mtok IS NULL;
GO

/* --- 2. Tokens cacheados por corrida ------------------------------------- */
IF COL_LENGTH('calidad.AuditExecutionLog', 'cached_tokens') IS NULL
BEGIN
    ALTER TABLE calidad.AuditExecutionLog
        ADD cached_tokens INT NULL;
    PRINT 'AuditExecutionLog.cached_tokens agregada.';
END
ELSE
    PRINT 'AuditExecutionLog.cached_tokens ya existía.';
GO

/* --- 3. La fórmula del costo -------------------------------------------- */
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
    -- Cuántos de esos input_tokens se cobraron a precio de caché. Se expone para
    -- poder medir cuánto está pegando el caché sin volver a IA_Uso.
    COALESCE(u.cached_tokens, 0)                    AS cached_tokens,
    COALESCE(u.input_tokens, 0)
      + COALESCE(u.output_tokens, 0)
      + COALESCE(u.thoughts_tokens, 0)              AS total_tokens,
    p.modelo                                        AS modelo_tarifa,
    p.fecha_desde                                   AS tarifa_desde,
    CAST(
        (
            -- prompt_token_count de Gemini YA incluye los cacheados: se separan las
            -- dos porciones en vez de sumar una de más.
            CASE WHEN COALESCE(u.input_tokens, 0) > COALESCE(u.cached_tokens, 0)
                 THEN COALESCE(u.input_tokens, 0) - COALESCE(u.cached_tokens, 0)
                 ELSE 0
            END / 1000000.0 * p.input_usd_mtok
          + COALESCE(u.cached_tokens, 0) / 1000000.0
              -- Si la tarifa del modelo todavía no tiene precio de caché cargado,
              -- se usa el 10% del input, que es la relación de la familia Flash.
              * COALESCE(p.cached_usd_mtok, p.input_usd_mtok * 0.10)
          + (COALESCE(u.output_tokens, 0) + COALESCE(u.thoughts_tokens, 0)) / 1000000.0 * p.output_usd_mtok
        )
        -- batch (asincrónico) y flex (sincrónico best-effort) valen lo mismo: la mitad
        -- del precio estándar.
        * CASE WHEN u.modo IN ('batch', 'flex') THEN 0.5 ELSE 1.0 END
    AS DECIMAL(18,6))                               AS costo_usd
FROM pagina_web.IA_Uso u
OUTER APPLY (
    SELECT TOP 1 pr.modelo, pr.fecha_desde, pr.input_usd_mtok, pr.output_usd_mtok, pr.cached_usd_mtok
    FROM pagina_web.IA_Precios pr
    WHERE pr.modelo = u.modelo
      AND pr.fecha_desde <= CAST(u.fecha AS DATE)
    ORDER BY pr.fecha_desde DESC
) p;
GO

PRINT 'vw_IA_Uso_Costos actualizada: los tokens cacheados se costean aparte.';
GO
