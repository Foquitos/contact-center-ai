/* ============================================================================
   Feature — Permisos dedicados: Gastos Chatbot (IA) y Scheduler de auditorías
   Fecha: 2026-07-13
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   1) uso_ia.chatbot  — página "Gastos Chatbot (IA)" (/uso-ia/chatbot y el
      dashboard backend con grupo=chatbot). Antes viajaba con uso_ia.view;
      uso_ia.view queda EXCLUSIVO de "Gastos y Logs de IA" (grupo=auditorias,
      logs y presupuesto).
   2) audit:scheduler — página "Scheduler de auditorías" y sus endpoints CRUD
      (/Auditoria/scheduler/…). Antes viajaba con audit:execute. OJO: es un
      permiso ADICIONAL a audit:execute (el router de auditorías lo sigue
      exigiendo y la pantalla necesita la cascada de empresas/plantillas):
      scheduler = audit:execute + audit:scheduler.

   DECISIÓN (2026-07-13): nacen SIN ASIGNAR (estilo template:modelo_ia).
   Hasta que se asignen rol por rol, solo el super admin ve esas páginas:
   - Pierden "Gastos Chatbot" los roles con uso_ia.view (Gerente Operaciones,
     Jefe Calidad, Jefe Vantix, Supervisor Farmalux).
   - Pierden "Scheduler" los 10 roles con audit:execute.
   ("Gastos y Logs de IA" no cambia: sigue con uso_ia.view.)

   CÓMO CORRER: contra la BD Acme, idempotente, ANTES del deploy del código.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'uso_ia.chatbot')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('uso_ia.chatbot',
            'Ver el tablero de gastos/consumo de IA de los chatbots (Gastos Chatbot)');
END
GO

IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'audit:scheduler')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('audit:scheduler',
            'Programar auditorías recurrentes (Scheduler); requiere además audit:execute');
END
GO

SELECT code, description FROM pagina_web.Permissions
WHERE code IN ('uso_ia.chatbot', 'audit:scheduler', 'uso_ia.view');
GO
