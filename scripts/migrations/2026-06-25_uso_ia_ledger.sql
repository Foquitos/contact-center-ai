/* ============================================================================
   Feature — Libro centralizado de consumo de la API de Gemini (auditoría de costos)
   Fecha: 2026-06-25
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   1) pagina_web.IA_Uso — 1 fila = 1 llamada a Gemini. Registra tokens por tipo,
      modelo, modo (sync/batch), feature ("para qué"), user_id ("quién") y las
      dimensiones campaña/empresa cuando aplican. Lo pueblan, de ahora en más:
        - auditoría     -> backend/AuditorIA/sql_a_Claude.py (sync y batch)
        - transcripción -> backend/AuditorIA/Trancribir.py
        - chatbot       -> backend/chatBot.py
        - asistente     -> backend/AuditorIA/asistente_plantillas.py
      (el histórico se carga con la migración de backfill, archivo aparte).
   2) pagina_web.IA_Precios — tarifas USD por 1M de tokens, con vigencia por fecha
      (los precios de Gemini cambiaron; ver seed). Permite corregir/actualizar el
      costo sin tocar el libro de hechos.
   3) pagina_web.vw_IA_Uso_Costos — única fuente de costo: cruza IA_Uso con la tarifa
      vigente a la fecha del consumo y calcula costo_usd (thinking se factura a tarifa
      OUTPUT; el modo batch aplica -50%).

   NOTAS
   -----
   · UNIQUE (feature, ref_id) hace idempotentes tanto el backfill como los inserts
     going-forward: una misma fila origen nunca se cuenta dos veces.
   · Las columnas de tokens son NULL-ables; la vista usa COALESCE(...,0) para el costo.
   · Embeddings: se registra la columna pero su costo queda fuera de la vista por ahora
     (monto inmaterial); se agrega una fila de IA_Precios para el modelo de embeddings
     cuando se quiera contemplar.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme con un usuario con DDL sobre el schema pagina_web.
   Idempotente: las tablas se crean si no existen, el seed usa MERGE y la vista usa
   CREATE OR ALTER. Correr ANTES del backfill.
   ============================================================================ */

SET XACT_ABORT ON;
GO

/* -- 1) Tabla-libro de consumo --------------------------------------------- */
IF OBJECT_ID('pagina_web.IA_Uso', 'U') IS NULL
BEGIN
    CREATE TABLE pagina_web.IA_Uso (
        id               BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_IA_Uso PRIMARY KEY,
        fecha            DATETIME2     NOT NULL,                 -- momento del consumo (UTC)
        feature          NVARCHAR(40)  NOT NULL,                 -- auditoria | transcripcion | chatbot | asistente_plantillas
        modo             NVARCHAR(10)  NOT NULL CONSTRAINT DF_IA_Uso_modo DEFAULT('sync'), -- sync | batch
        modelo           NVARCHAR(80)  NOT NULL,                 -- nombre del modelo Gemini efectivo
        user_id          INT           NULL,                    -- quién (auditor / user chat / usuario del editor)
        input_tokens     BIGINT        NULL,
        output_tokens    BIGINT        NULL,
        thoughts_tokens  BIGINT        NULL,                    -- thinking; se factura a tarifa OUTPUT
        cached_tokens    BIGINT        NULL,
        embedding_tokens BIGINT        NULL,
        campana_id       INT           NULL,
        empresa_id       INT           NULL,
        ref_id           NVARCHAR(120) NULL,                    -- clave 1:1 de la fila origen (dedup)
        status           NVARCHAR(20)  NULL CONSTRAINT DF_IA_Uso_status DEFAULT('ok'),
        extras           NVARCHAR(MAX) NULL,                    -- JSON libre (finish_reason, batch job, etc.)
        created_at       DATETIME2     NOT NULL CONSTRAINT DF_IA_Uso_created DEFAULT(SYSUTCDATETIME())
    );

    CREATE INDEX IX_IA_Uso_fecha          ON pagina_web.IA_Uso (fecha);
    CREATE INDEX IX_IA_Uso_feature_fecha  ON pagina_web.IA_Uso (feature, fecha);
    CREATE INDEX IX_IA_Uso_modelo_fecha   ON pagina_web.IA_Uso (modelo, fecha);
    -- Idempotencia/sin duplicados: filtrado para permitir varias filas con ref_id NULL.
    CREATE UNIQUE INDEX UX_IA_Uso_feature_ref ON pagina_web.IA_Uso (feature, ref_id)
        WHERE ref_id IS NOT NULL;
END
GO

/* -- 2) Tabla de precios (USD por 1M tokens) con vigencia por fecha --------- */
IF OBJECT_ID('pagina_web.IA_Precios', 'U') IS NULL
BEGIN
    CREATE TABLE pagina_web.IA_Precios (
        id              INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_IA_Precios PRIMARY KEY,
        modelo          NVARCHAR(80)  NOT NULL,
        fecha_desde     DATE          NOT NULL,            -- vigencia desde (inclusive)
        input_usd_mtok  DECIMAL(10,4) NOT NULL,
        output_usd_mtok DECIMAL(10,4) NOT NULL,            -- aplica también a thoughts_tokens
        notas           NVARCHAR(200) NULL,
        CONSTRAINT UX_IA_Precios_modelo_fecha UNIQUE (modelo, fecha_desde)
    );
END
GO

/* -- 2b) Seed de precios conocidos (idempotente via MERGE) ------------------ */
;WITH precios(modelo, fecha_desde, input_usd_mtok, output_usd_mtok, notas) AS (
    SELECT * FROM (VALUES
        ('gemini-3-flash',          '2025-10-01', 0.50, 3.00, N'Auditoría/transcripción hasta ~2026-05-19 (precio oficial Google)'),
        ('gemini-3.5-flash',        '2026-05-19', 1.50, 9.00, N'Auditoría/transcripción desde ~2026-05-19 (precio oficial Google)'),
        ('gemini-3.1-flash-lite',   '2025-10-01', 0.25, 1.50, N'Chatbot RAG (texto; audio input $0.50)'),
        ('gemini-3.1-pro-preview',  '2026-02-19', 2.00, 12.00, N'Asistente de plantillas. Tramo <=200K tokens (ai.google.dev/gemini-api/docs/pricing, jun-2026). >200K seria 4.00/18.00, no aplica por volumen.')
    ) AS v(modelo, fecha_desde, input_usd_mtok, output_usd_mtok, notas)
)
MERGE pagina_web.IA_Precios AS dst
USING precios AS src
   ON dst.modelo = src.modelo AND dst.fecha_desde = src.fecha_desde
WHEN NOT MATCHED BY TARGET THEN
    INSERT (modelo, fecha_desde, input_usd_mtok, output_usd_mtok, notas)
    VALUES (src.modelo, src.fecha_desde, src.input_usd_mtok, src.output_usd_mtok, src.notas);
GO

/* -- 3) Vista de costos: única fuente de verdad del gasto ------------------- */
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
        ) * CASE WHEN u.modo = 'batch' THEN 0.5 ELSE 1.0 END
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
