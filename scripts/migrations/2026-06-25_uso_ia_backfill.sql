/* ============================================================================
   Feature — Backfill histórico del libro de consumo de IA (pagina_web.IA_Uso)
   Fecha: 2026-06-25
   Autor: equipo Acme

   QUÉ HACE
   --------
   Carga en pagina_web.IA_Uso el consumo ya registrado en las tablas por-feature, para
   que el tablero muestre el histórico (oct-2025 →) y no sólo lo nuevo:
     · calidad.Auditorias        -> feature='auditoria'      (~94% del costo)
     · calidad.transcripciones   -> feature='transcripcion'
     · pagina_web.query_chatbots_logs -> feature='chatbot'

   Reglas de mapeo (idénticas al criterio del reporte de gerencia):
     · modelo por fecha de corte: < 2026-05-19 -> 'gemini-3-flash', si no 'gemini-3.5-flash'
       (auditoría/transcripción). Chatbot: 'gemini-3.1-flash-lite' todo el período.
     · modo 'batch' si el IdAplicativo está en calidad.Batch_data con columna='ID del llamado';
       si no, 'sync'. (Batch factura -50%, lo aplica la vista.)
     · ref_id = clave 1:1 de la fila origen -> dedup con el UNIQUE (feature, ref_id):
       re-correr esta migración NO duplica, y NO choca con los inserts going-forward.

   >>> A CONFIRMAR ANTES DE CORRER <<<
   El cruce de batch usa calidad.Batch_data(columna, [valor]). El nombre de la columna que
   guarda el VALOR del 'ID del llamado' está asumido como [valor]: si en tu esquema se llama
   distinto (p.ej. [Valor], [dato], [contenido]), ajustá el identificador en la CTE batch_ids.
   Si preferís no derivar batch ahora, comentá la CTE y dejá todo como 'sync' (sobreestima
   ~3% del costo total, sólo el ~6% batch); se puede corregir luego con un UPDATE.

   CÓMO CORRER
   -----------
   Ejecutar DESPUÉS de 2026-06-25_uso_ia_ledger.sql. Idempotente (INSERT ... NOT EXISTS).
   ============================================================================ */

SET XACT_ABORT ON;
GO

DECLARE @corte DATE = '2026-05-19';   -- gemini-3-flash -> gemini-3.5-flash

/* -- IdAplicativo procesados en Batch (para marcar modo) -------------------- */
;WITH batch_ids AS (
    SELECT DISTINCT LTRIM(RTRIM([valor])) AS IdAplicativo   -- <<< confirmar nombre de columna
    FROM calidad.Batch_data
    WHERE columna = 'ID del llamado'
)

/* -- 1) Auditorías ---------------------------------------------------------- */
INSERT INTO pagina_web.IA_Uso
    (fecha, feature, modo, modelo, user_id, input_tokens, output_tokens, thoughts_tokens,
     campana_id, empresa_id, ref_id, status, extras)
SELECT
    a.FechaAuditoria,
    'auditoria',
    CASE WHEN EXISTS (SELECT 1 FROM batch_ids b WHERE b.IdAplicativo = a.IdAplicativo)
         THEN 'batch' ELSE 'sync' END,
    CASE WHEN a.FechaAuditoria < @corte THEN 'gemini-3-flash' ELSE 'gemini-3.5-flash' END,
    a.AuditorUsuarioID,
    a.input_tokens, a.output_tokens, a.thoughts_tokens,
    a.CampanaID, a.EmpresaID,
    CONCAT('auditoria:', a.AuditoriaID),
    'ok',
    CASE WHEN a.IdAplicativo IS NOT NULL
         THEN CONCAT('{"id_aplicativo":"', REPLACE(a.IdAplicativo, '"', ''), '"}') END
FROM calidad.Auditorias a
WHERE NOT EXISTS (
    SELECT 1 FROM pagina_web.IA_Uso u
    WHERE u.feature = 'auditoria' AND u.ref_id = CONCAT('auditoria:', a.AuditoriaID)
);
GO

/* -- 2) Transcripciones ----------------------------------------------------- */
;WITH batch_ids AS (
    SELECT DISTINCT LTRIM(RTRIM([valor])) AS IdAplicativo   -- <<< confirmar nombre de columna
    FROM calidad.Batch_data
    WHERE columna = 'ID del llamado'
)
INSERT INTO pagina_web.IA_Uso
    (fecha, feature, modo, modelo, user_id, input_tokens, output_tokens, thoughts_tokens,
     ref_id, status, extras)
SELECT
    t.fecha_subida,
    'transcripcion',
    CASE WHEN EXISTS (SELECT 1 FROM batch_ids b WHERE b.IdAplicativo = t.IdAplicativo)
         THEN 'batch' ELSE 'sync' END,
    CASE WHEN t.fecha_subida < '2026-05-19' THEN 'gemini-3-flash' ELSE 'gemini-3.5-flash' END,
    t.user_id,
    t.input_tokens, t.output_tokens, t.thoughts_tokens,
    CONCAT('transcripcion:', t.IdAplicativo),
    'ok',
    NULL
FROM calidad.transcripciones t
WHERE t.IdAplicativo IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM pagina_web.IA_Uso u
    WHERE u.feature = 'transcripcion' AND u.ref_id = CONCAT('transcripcion:', t.IdAplicativo)
);
GO

/* -- 3) Chatbot ------------------------------------------------------------- */
/* effective_campana es un texto (no un campana_id): va en extras, campana_id queda NULL. */
INSERT INTO pagina_web.IA_Uso
    (fecha, feature, modo, modelo, user_id, input_tokens, output_tokens, embedding_tokens,
     ref_id, status, extras)
SELECT
    q.fecha,
    'chatbot',
    'sync',
    'gemini-3.1-flash-lite',
    q.user_id,
    q.input_tokens, q.output_tokens, q.embedding_tokens,
    CONCAT('chatbot:', COALESCE(NULLIF(q.task_id, ''), CONCAT('rowid:', CAST(q.id AS NVARCHAR(20))))),
    'ok',
    CASE WHEN q.effective_campana IS NOT NULL
         THEN CONCAT('{"effective_campana":"', REPLACE(q.effective_campana, '"', ''), '"}') END
FROM pagina_web.query_chatbots_logs q
WHERE NOT EXISTS (
    SELECT 1 FROM pagina_web.IA_Uso u
    WHERE u.feature = 'chatbot'
      AND u.ref_id = CONCAT('chatbot:', COALESCE(NULLIF(q.task_id, ''), CONCAT('rowid:', CAST(q.id AS NVARCHAR(20)))))
);
GO
