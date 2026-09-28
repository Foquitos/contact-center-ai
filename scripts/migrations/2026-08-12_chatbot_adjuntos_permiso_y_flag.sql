/* ============================================================================
   Feature — Adjuntos del chatbot: permiso RBAC + habilitación por bot
   Fecha: 2026-08-12
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   1. Permiso `chatbot.adjuntos` — habilita subir capturas de pantalla y PDF en
      el chatbot RAG (POST /consultar/stream/ con multipart).
   2. Columna `pagina_web.Chatbots.permite_adjuntos` (BIT NOT NULL DEFAULT 0) —
      qué bots aceptan archivos.

   POR QUÉ DOS COSAS Y NO UNA
   --------------------------
   Responden preguntas distintas y las dos tienen que darse:

   - el PERMISO dice QUIÉN puede mandar archivos (se otorga por rol);
   - el FLAG dice A QUÉ BOT se le pueden mandar.

   Con solo el permiso, un usuario de Calidad podría adjuntarle una factura al bot
   de procedimientos de otra campaña, que no gana nada con la imagen y para el que
   ese archivo es un dato de un cliente saliendo de la empresa sin motivo. Con solo
   el flag, cualquier operador del bot podría subir archivos.

   El flag es además la PERILLA OPERATIVA: para cortar el feature en producción
   alcanza con un UPDATE (toma efecto en <= 60s por el TTL del registry, sin
   reiniciar la API ni tocar el .env). Por eso el super admin tampoco lo saltea.

   POR QUÉ EL CÓDIGO LLEVA PUNTO Y NO DOS PUNTOS
   ---------------------------------------------
   `chatbot.adjuntos`, NO `chatbot:adjuntos`. `require_chatbot_user` (backend) y
   `tiene_acceso_chatbot` (frontend) tratan a CUALQUIER permiso con prefijo
   `chatbot:` como "este usuario puede usar un bot", así que un `chatbot:adjuntos`
   le abriría la pantalla del chatbot a quien solo debía poder adjuntar. Mismo
   criterio que `chatbot.solicitudes` (2026-07-24). De paso evita chocar con el
   permiso de un bot que algún día se llame "adjuntos" (`chatbot:<slug>`).

   DECISIÓN: nace SIN ASIGNAR, igual que uso_ia.chatbot / chatbot.solicitudes.
   Hasta asignarlo rol por rol, solo el super admin puede adjuntar. Se asigna a
   los roles de Calidad para la prueba (ver el bloque comentado al final).

   Y NINGÚN BOT ARRANCA HABILITADO: `permite_adjuntos` queda en 0 para todos. Hay
   que prender a mano los que se van a probar.

   CÓMO CORRER
   -----------
   Contra la BD Acme, ANTES del deploy del código (el registry hace SELECT de la
   columna nueva; sin ella el chatbot no levanta). Idempotente. No requiere
   reindexar.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

/* --------------------------------------------------------------- 1. Permiso */
IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'chatbot.adjuntos')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('chatbot.adjuntos',
            'Adjuntar capturas de pantalla y PDF en las consultas al chatbot RAG');
END
GO

/* ------------------------------------------------------- 2. Flag por chatbot */
IF COL_LENGTH('pagina_web.Chatbots', 'permite_adjuntos') IS NULL
BEGIN
    ALTER TABLE pagina_web.Chatbots
        ADD permite_adjuntos BIT NOT NULL CONSTRAINT DF_Chatbots_permite_adjuntos DEFAULT 0;
END
GO

/* ============================================================================
   PUESTA EN MARCHA DE LA PRUEBA

   a) EL PERMISO, desde `Administración > Gestionar Roles`: el permiso aparece
      solo en la lista apenas corre esta migración, y asignarlo desde el panel
      deja el rastro en RbacAuditLog (quién lo dio y cuándo), que por SQL se
      pierde. Toma efecto en el próximo login del usuario, o antes si el frontend
      refresca la sesión contra /me. RoleEffectivePermissions es una VISTA, así
      que no hay nada que recalcular.

      Si igual conviene hacerlo por SQL (ajustar los nombres de rol con el último
      SELECT de verificación):

      INSERT INTO pagina_web.RolePermissions (role_id, permission_id)
      SELECT r.id, p.id
      FROM pagina_web.Roles r
      CROSS JOIN pagina_web.Permissions p
      WHERE p.code = 'chatbot.adjuntos'
        AND r.name IN ('Calidad', 'Supervisor Calidad')     -- <== AJUSTAR
        AND NOT EXISTS (SELECT 1 FROM pagina_web.RolePermissions rp
                        WHERE rp.role_id = r.id AND rp.permission_id = p.id);

   b) LOS BOTS que aceptan archivos durante la prueba (esto sí va por SQL: el
      panel de chatbots todavía no edita este flag).
   --    updated_at SÍ hace falta: es lo que hace que los workers re-instancien el
   --    bot en caliente y vean el flag nuevo dentro del TTL (~60s).
   UPDATE pagina_web.Chatbots
   SET permite_adjuntos = 1, updated_at = SYSDATETIME()
   WHERE slug IN ('voltara')                              -- <== AJUSTAR
     AND permite_adjuntos = 0;

   PARA CORTAR EL FEATURE (sin deploy ni reinicio):
   UPDATE pagina_web.Chatbots
   SET permite_adjuntos = 0, updated_at = SYSDATETIME()
   WHERE permite_adjuntos = 1;
   ============================================================================ */

/* ----------------------------------------------------------- Verificación */
SELECT code, description FROM pagina_web.Permissions
WHERE code IN ('chatbot.adjuntos', 'chatbot.solicitudes');
GO

SELECT slug, nombre, permite_adjuntos, activo
FROM pagina_web.Chatbots
ORDER BY permite_adjuntos DESC, slug;
GO

/* Quién quedó habilitado para adjuntar (vacío = solo el super admin). */
SELECT r.name AS rol, p.code AS permiso
FROM pagina_web.RolePermissions rp
JOIN pagina_web.Roles r ON r.id = rp.role_id
JOIN pagina_web.Permissions p ON p.id = rp.permission_id
WHERE p.code = 'chatbot.adjuntos'
ORDER BY r.name;
GO
