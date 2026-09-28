/* ============================================================================
   Feature — Alcance por empresa: permiso maestro templates:manage
   Fecha: 2026-07-10
   Autor: equipo Acme

   CONTEXTO
   --------
   Los permisos template:<empresa> (calidad.Empresas.RequiredPermissionID ->
   pagina_web.Permissions) pasan a ENFORZARSE en el backend: controlan el
   acceso a auditorías realizadas, dashboards (bandeja) y ejecución de
   auditorías de cada empresa. El código de empresas_disponibles() ya
   contemplaba un permiso maestro 'templates:manage' ("ve todas las empresas")
   que nunca existió en el catálogo: acá se crea.

   QUÉ HACE
   --------
   1) Crea el permiso templates:manage (NO se asigna a ningún rol: los roles
      de calidad ya tienen los 15 template:<empresa> individuales y el super
      admin bypassa; queda disponible para roles futuros "ve todo").
   2) Actualiza las descripciones de los template:<empresa> para reflejar su
      nuevo efecto real.

   CÓMO CORRER
   -----------
   Contra la BD Acme. Idempotente. Independiente del orden de deploy (el
   código nuevo funciona sin este permiso; solo lo hace usable).
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'templates:manage')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('templates:manage',
            'Acceso a TODAS las empresas en auditorías, dashboards y plantillas (sin necesidad de permisos por empresa)');
END
GO

/* Descripciones acordes al nuevo efecto (antes: "Permite ver la template de X").
   Un permiso puede cubrir varias empresas (template:hidra = HIDRA + HIDRA Comercial). */
UPDATE p
SET p.description = 'Acceso a auditorías, dashboards y plantillas de ' + x.nombres
FROM pagina_web.Permissions p
JOIN (
    SELECT RequiredPermissionID, STRING_AGG(Nombre, ' / ') WITHIN GROUP (ORDER BY Nombre) AS nombres
    FROM calidad.Empresas
    WHERE RequiredPermissionID IS NOT NULL
    GROUP BY RequiredPermissionID
) x ON x.RequiredPermissionID = p.id
WHERE p.code LIKE 'template:%'
  AND p.code NOT IN ('template:read', 'template:create', 'template:modelo_ia');
GO

/* Reporte: empresa -> permiso requerido */
SELECT e.EmpresaID, e.Nombre, e.IsActive, p.code AS permiso_requerido
FROM calidad.Empresas e
LEFT JOIN pagina_web.Permissions p ON p.id = e.RequiredPermissionID
ORDER BY e.EmpresaID;
GO
