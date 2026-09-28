/* ============================================================================
   Feature — Permiso dedicado: ver las CONSULTAS al chatbot (sin costos)
   Fecha: 2026-07-24
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   chatbot.solicitudes — habilita la pestaña "Solicitudes" de la página de
   chatbot (/uso-ia/chatbot): las interacciones de los operadores con los bots
   (qué consultaron, qué respondió el bot y de dónde salió el contexto RAG),
   SIN NADA de costos/gasto (ni USD ni tokens).

   POR QUÉ
   -------
   Los roles de Calidad necesitan poder revisar las consultas que hicieron los
   operadores a los chatbots, pero NO deben ver información de costos (que es de
   Finanzas/Gerencia). Hoy toda la página vivía detrás de uso_ia.chatbot (que
   incluye el Resumen con costo/tokens). Este permiso separa el acceso:

   - uso_ia.chatbot  -> página completa (Resumen con costos + Solicitudes).
   - chatbot.solicitudes -> SOLO la pestaña Solicitudes; el backend ni siquiera
     devuelve tokens a quien no tiene uso_ia.chatbot, y el dashboard de costos
     (grupo=chatbot) le da 403.

   El endpoint /uso-ia/chatbot-logs[/{id}] acepta cualquiera de los dos permisos;
   /uso-ia/dashboard?grupo=chatbot sigue exigiendo uso_ia.chatbot.

   DECISIÓN: nace SIN ASIGNAR (estilo uso_ia.chatbot / audit:scheduler). Hasta
   asignarlo rol por rol, solo el super admin ve la pestaña. Se asignará a los
   roles de Calidad.

   CÓMO CORRER: contra la BD Acme, idempotente, ANTES del deploy del código.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'chatbot.solicitudes')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('chatbot.solicitudes',
            'Ver las consultas de los operadores a los chatbots (Solicitudes), sin datos de costos');
END
GO

SELECT code, description FROM pagina_web.Permissions
WHERE code IN ('chatbot.solicitudes', 'uso_ia.chatbot');
GO
