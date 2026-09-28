/* ============================================================================
   Limpieza — Eliminar por completo el chatbot de prueba 'voltara_pruebas'
   Fecha: 2026-07-30
   Autor: equipo Acme

   QUÉ HACE
   --------
   Borra el bot que se había creado el 2026-07-24 para probar el asistente de
   docs en markdown (id 14, slug 'voltara_pruebas', permiso chatbot:voltara_pruebas).
   Ya está desactivado (activo = 0) y su contenido se consolidó en los bots
   'voltara' y 'voltara_digital', así que no queda nada que rescatar.

   Se borra, en este orden (las FK son NO ACTION, el orden importa):
     1. ChatbotDocVinculo   -> vínculos de docs compartidos (los que el bot 14
                               prestaba y los que le prestaban a él).
     2. ChatbotDocMarkdown  -> sus 4 docs markdown (ids 12, 13, 36, 37).
     3. ChatbotDocs         -> docs de Drive (no tiene ninguno).
     4. ChatbotPcrc         -> mapeo PCRC (no tiene: no es del grupo csv).
     5. ChatbotIndexJobs    -> 2 jobs de indexado (ambos 'completed', en prod).
     6. ChatbotIndexState   -> estado por entorno (prod, v2 'ready').
     7. ChatbotVacios       -> 3 vacíos de conocimiento detectados en las pruebas.
     8. Chatbots            -> la fila del bot.
     9. Permissions         -> chatbot:voltara_pruebas (id 52). Al borrarlo,
                               RolePermissions cae solo por CASCADE: hoy lo tiene
                               un único rol, 'Analista Calidad' (id 5).

   VERIFICADO ANTES DE ESCRIBIR ESTO
   ---------------------------------
   - Ningún otro bot usa los docs markdown del bot 14: los únicos vínculos vivos
     en ChatbotDocVinculo son docs del bot 1 (voltara) compartidos con el 15
     (voltara_digital). Borrar los del 14 no le saca conocimiento a nadie.
   - El slug 'voltara_pruebas' no aparece en ningún lado del repo (backend,
     frontend, scripts, tests): todo el bot vivía en la BD.

   QUÉ NO BORRA (a propósito)
   --------------------------
   El historial de uso/costo: 7 filas en query_chatbots_logs y sus 7 filas
   espejo en IA_Uso. Es contabilidad ya consolidada (alimenta /uso-ia y el
   presupuesto mensual); borrarla reescribe totales históricos y desincroniza
   ambas tablas si se borra una sola. Como los vacíos SÍ se borran, a esos logs
   se les pone vacio_id = NULL (la FK no permite dejarlos apuntando al vacío).

   Si igual se quiere borrar el historial, está el bloque OPCIONAL del final:
   descomentarlo y correrlo ANTES del paso 7 (los logs referencian los vacíos).

   CÓMO CORRER: contra la BD Acme. Idempotente (se puede correr dos veces).
   No requiere deploy ni reinicio: el ChatbotRegistry deja de ver el bot al
   siguiente refresh de snapshot (<= CHATBOT_REGISTRY_TTL_SECONDS).

   DESPUÉS DE CORRER ESTO, en SRV01 (prod) hay que limpiar Qdrant y el disco;
   ver el bloque "LIMPIEZA FUERA DE SQL" al final del archivo. En SRV00 (dev)
   no hay nada: el bot nunca se indexó ahí (sin colección ni storage local).
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

DECLARE @cid INT = (SELECT id FROM pagina_web.Chatbots WHERE slug = 'voltara_pruebas');
DECLARE @pid INT = (SELECT id FROM pagina_web.Permissions WHERE code = 'chatbot:voltara_pruebas');

IF @cid IS NULL AND @pid IS NULL
BEGIN
    PRINT 'El chatbot voltara_pruebas y su permiso ya no existen: nada que hacer.';
END
ELSE
BEGIN
    BEGIN TRANSACTION;

    IF @cid IS NOT NULL
    BEGIN
        /* Guardia: si algún OTRO bot quedó usando un doc markdown del bot 14,
           abortamos en vez de dejarlo sin conocimiento. Hoy no pasa. */
        IF EXISTS (
            SELECT 1
            FROM pagina_web.ChatbotDocVinculo v
            JOIN pagina_web.ChatbotDocMarkdown d ON d.id = v.doc_id
            WHERE d.chatbot_id = @cid AND v.chatbot_id <> @cid
        )
        BEGIN
            ROLLBACK TRANSACTION;
            THROW 50001, 'Hay docs markdown de voltara_pruebas compartidos con otro chatbot. Revisar ChatbotDocVinculo antes de borrar.', 1;
        END

        -- 1. Vínculos de docs compartidos (en ambos sentidos).
        DELETE FROM pagina_web.ChatbotDocVinculo WHERE chatbot_id = @cid;
        DELETE v
        FROM pagina_web.ChatbotDocVinculo v
        JOIN pagina_web.ChatbotDocMarkdown d ON d.id = v.doc_id
        WHERE d.chatbot_id = @cid;

        -- 2-6. Contenido y estado del bot.
        DELETE FROM pagina_web.ChatbotDocMarkdown WHERE chatbot_id = @cid;
        DELETE FROM pagina_web.ChatbotDocs       WHERE chatbot_id = @cid;
        DELETE FROM pagina_web.ChatbotPcrc       WHERE chatbot_id = @cid;
        DELETE FROM pagina_web.ChatbotIndexJobs  WHERE chatbot_id = @cid;
        DELETE FROM pagina_web.ChatbotIndexState WHERE chatbot_id = @cid;

        -- 7. Vacíos de conocimiento. Los logs que los referencian se sueltan
        --    (FK NO ACTION); el log queda, sin vacío asociado.
        UPDATE pagina_web.query_chatbots_logs
        SET vacio_id = NULL
        WHERE vacio_id IN (SELECT id FROM pagina_web.ChatbotVacios WHERE chatbot_id = @cid);

        DELETE FROM pagina_web.ChatbotVacios WHERE chatbot_id = @cid;

        -- 8. El bot.
        DELETE FROM pagina_web.Chatbots WHERE id = @cid;
    END

    -- 9. El permiso (RolePermissions cae por CASCADE).
    IF @pid IS NOT NULL
        DELETE FROM pagina_web.Permissions WHERE id = @pid;

    COMMIT TRANSACTION;
    PRINT 'Chatbot voltara_pruebas eliminado.';
END
GO

/* --------------------------- VERIFICACIÓN --------------------------------- */
SELECT 'Chatbots'           AS tabla, COUNT(*) AS filas_restantes FROM pagina_web.Chatbots           WHERE slug = 'voltara_pruebas'
UNION ALL SELECT 'Permissions',       COUNT(*) FROM pagina_web.Permissions       WHERE code = 'chatbot:voltara_pruebas'
UNION ALL SELECT 'ChatbotDocMarkdown', COUNT(*) FROM pagina_web.ChatbotDocMarkdown WHERE chatbot_id = 14
UNION ALL SELECT 'ChatbotIndexState',  COUNT(*) FROM pagina_web.ChatbotIndexState  WHERE chatbot_id = 14
UNION ALL SELECT 'ChatbotIndexJobs',   COUNT(*) FROM pagina_web.ChatbotIndexJobs   WHERE chatbot_id = 14
UNION ALL SELECT 'ChatbotVacios',      COUNT(*) FROM pagina_web.ChatbotVacios      WHERE chatbot_id = 14;
GO

-- Los otros bots siguen enteros (voltara_digital conserva sus 6 docs prestados):
SELECT c.slug, c.activo,
       (SELECT COUNT(*) FROM pagina_web.ChatbotDocMarkdown d WHERE d.chatbot_id = c.id) AS docs_propios,
       (SELECT COUNT(*) FROM pagina_web.ChatbotDocVinculo v WHERE v.chatbot_id = c.id)  AS docs_prestados
FROM pagina_web.Chatbots c
ORDER BY c.id;
GO


/* ============================================================================
   OPCIONAL — borrar también el historial de uso/costo (7 + 7 filas)
   Descomentar y correr ANTES del bloque principal (los logs referencian los
   vacíos con FK NO ACTION). Ojo: altera totales históricos de /uso-ia.

DELETE FROM pagina_web.IA_Uso
WHERE feature = 'chatbot'
  AND extras LIKE '%"effective_campana": "voltara_pruebas"%';

DELETE FROM pagina_web.query_chatbots_logs
WHERE effective_campana = 'voltara_pruebas';
GO
   ============================================================================ */


/* ============================================================================
   LIMPIEZA FUERA DE SQL — SOLO EN SRV01 (prod)
   ----------------------------------------------------------------------------
   El bot llegó a indexarse en prod (ChatbotIndexState: environment='prod',
   index_version=2, 'ready'), así que dejó colecciones en Qdrant y un persist_dir
   en disco. En SRV00 (dev) NO hay nada que borrar: se verificó que no existe ni
   la colección ni /var/www/chatcsva/storage/chatbots/voltara_pruebas.

   Qdrant (borrar la colección elimina también su alias):

       curl -s http://127.0.0.1:6333/collections | grep -o 'bot_voltara_pruebas_v[0-9]*'
       curl -X DELETE http://127.0.0.1:6333/collections/bot_voltara_pruebas_v2
       curl -X DELETE http://127.0.0.1:6333/collections/bot_voltara_pruebas_v1
       curl -X DELETE http://127.0.0.1:6333/collections/cache_voltara_pruebas

   Disco (docs, logs y persist_dirs v1/v2 del bot):

       rm -rf /var/www/chatcsva/storage/chatbots/voltara_pruebas

   Verificación final: el alias bot_voltara_pruebas ya no debe figurar.

       curl -s http://127.0.0.1:6333/aliases
   ============================================================================ */
