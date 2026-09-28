/* ============================================================================
   Feature — Simulación de roles (permiso roles:impersonate)
   Fecha: 2026-07-13
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   El permiso roles:impersonate habilita el "modo simulación": ver el sistema
   con los permisos EFECTIVOS de un rol (menú, pantallas y APIs del backend,
   que valida un JWT especial en cada request) para verificar accesos antes de
   asignarlo a usuarios reales.
a
   Reglas (las valida el backend en cada request, ver app/security.py):
   - Solo se pueden simular roles NO super admin cuyos permisos efectivos sean
     subconjunto de los del simulador (misma anti-escalada de la gestión de
     roles) — el super admin puede simular cualquier rol no super admin.
   - Durante la simulación is_super_admin=False aunque el actor lo sea.
   - Cada inicio de simulación queda en pagina_web.RbacAuditLog.

   NO se asigna a ningún rol por default: dárselo junto con roles:manage a
   quienes gestionan roles (la pantalla de gestión es donde vive el botón).

   CÓMO CORRER: contra la BD Acme, idempotente, antes del deploy del código.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'roles:impersonate')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('roles:impersonate',
            'Simular un rol (ver el sistema con sus permisos efectivos) para verificar accesos antes de asignarlo');
END
GO

SELECT 'permiso roles:impersonate' AS objeto, COUNT(*) AS filas
FROM pagina_web.Permissions WHERE code = 'roles:impersonate';
GO
