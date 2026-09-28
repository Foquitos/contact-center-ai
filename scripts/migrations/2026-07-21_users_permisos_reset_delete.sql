/* ============================================================================
   Feature — Separar permisos de gestión de usuarios: crear vs blanquear clave
   vs eliminar acceso
   Fecha: 2026-07-21
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   Hasta ahora un solo permiso (users:create) habilitaba TODO en "Gestionar
   Usuarios": alta, edición de roles, cambio de contraseña a mano (con
   cualquier clave que el admin quisiera escribir) y borrado de acceso. El
   cambio de código que acompaña esta migración separa el reseteo de clave y
   el borrado en dos permisos propios:

   1) users:reset_password — blanquear la contraseña de un usuario (la deja
      igual a su documento y fuerza cambio en el próximo ingreso). Ya NO se
      puede fijar una clave arbitraria: es solo blanqueo. Sensible porque
      quien lo ejecuta puede loguearse como el usuario hasta que cambie la
      clave, así que se separa de users:create para poder auditarlo/limitarlo
      aparte.
   2) users:delete — eliminar el acceso de un usuario (password + roles).

   DECISIÓN (2026-07-21, confirmada con Ignacio): a diferencia de
   uso_ia.chatbot/audit:scheduler (que nacieron SIN asignar), estos dos nacen
   asignados a los mismos roles que HOY tienen users:create, para no romper
   el flujo de nadie el día del deploy. Ignacio los reacomoda después desde
   "Gestionar Roles" si quiere separar quién puede blanquear/borrar de quién
   solo puede dar de alta.

   CÓMO CORRER: contra la BD Acme, idempotente, ANTES del deploy del código
   (el código nuevo exige estos permisos en los endpoints de reset/delete).
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'users:reset_password')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('users:reset_password',
            'Blanquear la contraseña de un usuario (queda igual al documento, fuerza cambio en el próximo ingreso)');
END
GO

IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'users:delete')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('users:delete',
            'Eliminar el acceso de un usuario (borra su contraseña y roles; no toca nómina)');
END
GO

/* Copiar el grant: todo rol que hoy tiene users:create recibe también los dos
   permisos nuevos (idempotente vía NOT EXISTS). */
INSERT INTO pagina_web.RolePermissions (role_id, permission_id)
SELECT rp.role_id, perm_new.id
FROM pagina_web.RolePermissions rp
JOIN pagina_web.Permissions perm_create ON perm_create.id = rp.permission_id
CROSS JOIN pagina_web.Permissions perm_new
WHERE perm_create.code = 'users:create'
  AND perm_new.code IN ('users:reset_password', 'users:delete')
  AND NOT EXISTS (
        SELECT 1 FROM pagina_web.RolePermissions ya
        WHERE ya.role_id = rp.role_id AND ya.permission_id = perm_new.id
      );
GO

SELECT r.name AS rol, p.code AS permiso
FROM pagina_web.RolePermissions rp
JOIN pagina_web.Roles r ON r.id = rp.role_id
JOIN pagina_web.Permissions p ON p.id = rp.permission_id
WHERE p.code IN ('users:create', 'users:reset_password', 'users:delete')
ORDER BY r.name, p.code;
GO
