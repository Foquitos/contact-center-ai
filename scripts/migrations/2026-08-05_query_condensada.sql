/* ============================================================================
   Guardar la consulta REESCRITA con la que el chatbot buscó en el índice
   Fecha: 2026-08-05
   Autor: equipo Acme

   POR QUÉ
   -------
   El bot usa CondensePlusContextChatEngine: antes de buscar, reescribe la
   pregunta del operador junto con el historial para dejar una consulta
   auto-contenida. Buscar en el índice usa ESA consulta, no la que se tipeó.

   Cuando el operador escribe una repregunta ("por donde se hace", "tiene costo
   adicional?", "que documentacion necesita?"), lo que se guardaba en el log era
   solo el texto suelto, y al revisar las solicitudes era imposible saber contra
   qué se buscó realmente — ni por qué el resultado no tenía nada que ver. Esas
   repreguntas son varias de las que hoy aparecen en Vacíos de conocimiento.

   Guardarla también deja medible cuántas veces la condensación arrastra el tema
   equivocado, que es una causa de fallo de recuperación que hoy no se ve.

   NOTAS
   -----
   - NVARCHAR: la consulta reescrita la produce el LLM y puede traer acentos y
     símbolos; en VARCHAR se degradan en el bind (mismo problema que ya arruinó
     los emoji de `response`).
   - NULL para todo lo histórico y para el primer mensaje de cada conversación,
     donde no hay historial que condensar y el motor devuelve la consulta tal cual.

   CÓMO CORRER
   -----------
   Contra la BD Acme. Idempotente. Aplicar ANTES de deployar: _log_query_details
   escribe la columna nueva.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('pagina_web.query_chatbots_logs')
      AND name = 'query_condensada'
)
BEGIN
    ALTER TABLE pagina_web.query_chatbots_logs
        ADD query_condensada NVARCHAR(2000) NULL;
    PRINT 'Columna query_condensada agregada.';
END
ELSE
    PRINT 'query_condensada ya existía; no se toca.';
GO

/* ------------------------------------------------------------ verificación */

SELECT c.name AS columna, t.name AS tipo, c.max_length, c.is_nullable
FROM sys.columns c
JOIN sys.types t ON t.user_type_id = c.user_type_id
WHERE c.object_id = OBJECT_ID('pagina_web.query_chatbots_logs')
  AND c.name IN ('query', 'query_condensada', 'response')
ORDER BY c.column_id;
GO
