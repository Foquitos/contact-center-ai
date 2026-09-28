/* ============================================================================
   Limpieza — pagina_web.ChatbotIndexJobs: purga de jobs 'failed'
   Fecha: 2026-07-13
   Autor: equipo Acme

   POR QUÉ
   -------
   Entre el 2026-07-09 y el 2026-07-13 el reindexado nocturno falló en bloque
   (63 jobs). Causa: el contenedor de Qdrant corría con el nofile por defecto
   (1024 fds) y se quedó sin file descriptors, así que no podía crear la
   colección nueva bot_{slug}_v{N+1} de cada reindexado:
       "RocksDB open error: IO error ... Too many open files (os error 24)"
   Arreglado con `ulimits: nofile: 65535` en docker-compose.qdrant.yml.

   Estos jobs son HISTORIAL: borrarlos no reencola nada ni toca los índices
   (el reindexado nocturno los vuelve a encolar solo, ver run_scheduler.py →
   encolar_reindexado_todos, cron 00:00).

   OJO — no todos los 'failed' son del bug de Qdrant:
     - 7 son de paygo ("El parseo no produjo ningún nodo con contenido"): su
       Google Doc está VACÍO a propósito, la carga la hace otro sector. Paygo
       va a volver a fallar todas las noches hasta que el doc tenga contenido.
       Si molesta el ruido, desactivarlo (ver bloque OPCIONAL al final).
     - 1 es un timeout de descarga de Drive (benefix): transitorio.

   CÓMO CORRER: contra la BD Acme. Correr DESPUÉS de subir el ulimit de Qdrant
   en SRV01, para no borrar evidencia de un problema todavía vivo.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

-- Antes: qué se va a borrar, agrupado por causa.
SELECT status, COUNT(*) AS jobs, MIN(created_at) AS primero, MAX(created_at) AS ultimo
FROM pagina_web.ChatbotIndexJobs
GROUP BY status;
GO

DELETE FROM pagina_web.ChatbotIndexJobs
WHERE status = 'failed';
GO

-- Después: solo deberían quedar los 'completed' (y los 'pending'/'running' del día).
SELECT status, COUNT(*) AS jobs
FROM pagina_web.ChatbotIndexJobs
GROUP BY status;
GO

/* ---------------------------------------------------------------------------
   OPCIONAL — cortar el ruido nocturno de paygo hasta que carguen el doc.
   Desactivarlo lo saca de encolar_reindexado_todos Y del selector de bots.
   Reactivar con activo = 1 cuando el doc tenga contenido.

   UPDATE pagina_web.Chatbots SET activo = 0, updated_at = SYSDATETIME()
   WHERE slug = 'paygo';
   --------------------------------------------------------------------------- */
