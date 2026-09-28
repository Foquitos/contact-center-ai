/* ============================================================================
   Limpieza — Baja de permisos muertos del catálogo RBAC
   Fecha: 2026-07-10
   Autor: equipo Acme

   QUÉ ELIMINA (ningún endpoint los chequea; auditado código + BD 2026-07-10)
   -------------------------------------------------------------------------
   1) users:read            — solo se exigía en GET /admin/users/{doc} en AND con
                              users:create, y los mismos 6 roles tenían ambos.
                              El endpoint queda protegido por users:create.
   2) users:assign_role     — nunca se chequeó; la asignación de roles queda
                              atada a users:create (decisión 2026-07-10).
   3) bandeja.annotate      — la feature de anotaciones no existe en el código.
   4) docs:refresh          — legacy, reemplazado por chatbot:admin
                              (migración 2026-07-08, DELETE diferido a limpieza).
   5) chatbot:selectcampaign— legacy del selector de campaña, expandido a
                              permisos por bot en la migración 2026-07-08.

   QUÉ NO TOCA
   -----------
   - template:<empresa> (x15): se conservan y pasan a ENFORZARSE como alcance
     por empresa (auditorías/dashboards/ejecución); ver la migración
     2026-07-10_permiso_templates_manage.sql y backend/app/rbac.py.
   - template:create y audit.bandeja_segurar: pasan a ENFORZARSE en código en
     este mismo deploy (creación/edición de plantillas y Dashboard Segurar
     respectivamente); ver backend/app/routers/planillas_prompts.py y bandeja.py.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme junto con 2026-07-10_rbac_jerarquia_delegacion.sql,
   ANTES de deployar el código. Idempotente.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

DECLARE @codigos TABLE (code VARCHAR(100));
INSERT INTO @codigos VALUES
    ('users:read'),
    ('users:assign_role'),
    ('bandeja.annotate'),
    ('docs:refresh'),
    ('chatbot:selectcampaign');

/* Reporte previo: qué roles pierden qué código (informativo, no cambia accesos:
   ningún endpoint valida estos permisos) */
SELECT p.code, r.name AS rol
FROM pagina_web.Permissions p
JOIN @codigos c ON c.code = p.code
LEFT JOIN pagina_web.RolePermissions rp ON rp.permission_id = p.id
LEFT JOIN pagina_web.Roles r ON r.id = rp.role_id
ORDER BY p.code, r.name;

DELETE rp
FROM pagina_web.RolePermissions rp
JOIN pagina_web.Permissions p ON p.id = rp.permission_id
JOIN @codigos c ON c.code = p.code;

DELETE p
FROM pagina_web.Permissions p
JOIN @codigos c ON c.code = p.code;
GO

/* Reporte final */
SELECT 'permisos restantes' AS objeto, COUNT(*) AS filas FROM pagina_web.Permissions
UNION ALL
SELECT 'muertos aun presentes (debe ser 0)', COUNT(*)
FROM pagina_web.Permissions
WHERE code IN ('users:read', 'users:assign_role', 'bandeja.annotate',
               'docs:refresh', 'chatbot:selectcampaign');
GO
