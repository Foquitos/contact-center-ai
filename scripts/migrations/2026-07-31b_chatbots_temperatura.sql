/* ============================================================================
   Feature — Temperatura del LLM por chatbot
   Fecha: 2026-07-31
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   Columna pagina_web.Chatbots.temperatura (FLOAT NULL). NULL => se usa el default
   global DEFAULT_LLM_TEMP_REMOTE del .env (hoy 0.4), que era el único valor
   posible hasta ahora: todos los bots compartían una sola instancia de Gemini.

   POR QUÉ
   -------
   Los bots no hacen el mismo trabajo:

   - Los bots RAG de PROCEDIMIENTOS (voltara, csv_*, vantix, benefix, paygo) tienen
     que repetir el manual, no reformularlo. Cuando el operador está al teléfono,
     una variación de redacción sobre un requisito o un plazo es un error: la
     temperatura alta le da al modelo margen para "mejorar" un texto que debe salir
     tal cual. 0.15 mantiene algo de fluidez sin habilitar la paráfrasis creativa.

   - voltara_digital REDACTA CARTAS al cliente a partir de plantillas. Ahí sí hace
     falta margen para adaptar la plantilla al caso concreto, encadenar párrafos y
     ajustar el tono. 0.65.

   CUIDADO CON SUBIR MÁS voltara_digital
   ------------------------------------
   La creatividad que se busca es de REDACCIÓN, no de contenido: la carta sale con
   la firma de la empresa. Por encima de ~0.7 el modelo empieza a completar datos
   de política (plazos, montos, requisitos) que no están en la plantilla. Si hace
   falta más variedad, conviene antes revisar las plantillas del catálogo que
   seguir subiendo este número. El anclaje al corpus del system prompt sigue siendo
   la defensa principal; la temperatura no la reemplaza.

   CÓMO CORRER
   -----------
   Contra la BD Acme, ANTES del deploy (el registry hace SELECT de la columna).
   Idempotente. No requiere reindexar: los workers re-instancian el bot al ver el
   bump de updated_at, dentro del TTL del registry (~60s).
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF COL_LENGTH('pagina_web.Chatbots', 'temperatura') IS NULL
BEGIN
    ALTER TABLE pagina_web.Chatbots ADD temperatura FLOAT NULL;
END
GO

/* Rango sano: fuera de [0,1] Gemini directamente rechaza el request, y conviene
   que un error de tipeo en el panel falle acá y no en medio de un llamado. */
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_Chatbots_temperatura')
BEGIN
    ALTER TABLE pagina_web.Chatbots
        ADD CONSTRAINT CK_Chatbots_temperatura
        CHECK (temperatura IS NULL OR (temperatura >= 0 AND temperatura <= 1));
END
GO

/* Bots de procedimientos: repetir el manual, no reformularlo. */
UPDATE pagina_web.Chatbots
SET temperatura = 0.15, updated_at = SYSDATETIME()
WHERE slug <> 'voltara_digital'
  AND (temperatura IS NULL OR temperatura <> 0.15);
GO

/* Redacción de cartas: necesita margen para adaptar la plantilla al caso. */
UPDATE pagina_web.Chatbots
SET temperatura = 0.65, updated_at = SYSDATETIME()
WHERE slug = 'voltara_digital'
  AND (temperatura IS NULL OR temperatura <> 0.65);
GO

SELECT slug, nombre, temperatura, updated_at
FROM pagina_web.Chatbots
ORDER BY temperatura DESC, slug;
GO
