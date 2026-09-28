/* ============================================================================
   Feature — Permiso 'uso_ia.view' para el tablero de gastos de IA
   Fecha: 2026-06-25
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   1) Permiso pagina_web.Permissions ('uso_ia.view') que protege:
        - backend FastAPI  /uso-ia/*           (RoleChecker(['uso_ia.view']))
        - frontend Flask   /uso-ia y /api/uso-ia/*  (@permission_required('uso_ia.view'))
   2) Lo asigna a todos los roles que ya tienen 'bandeja.view' (gerencia/supervisión),
      como default razonable. Los super-admin ya ven todo por su flag, no necesitan grant.

   CÓMO DARLO A FINANZAS
   ---------------------
   Para un rol de Finanzas SIN acceso a auditorías: asignarle SOLO 'uso_ia.view' desde
   Administración > Gestionar Roles (o agregar el rol al INSERT de abajo). El permiso
   entra en la sesión en el próximo login (lo lee @permission_required).

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme. Idempotente: el permiso se inserta si no existe y las
   asignaciones usan NOT EXISTS.
   ============================================================================ */

SET XACT_ABORT ON;
GO

/* -- 1) Alta del permiso (idempotente) ------------------------------------- */
IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'uso_ia.view')
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('uso_ia.view', N'Ver el tablero de gastos/consumo de IA');
GO

/* -- 2) Asignar a los roles que hoy ven dashboards (tienen bandeja.view) ---- */
INSERT INTO pagina_web.RolePermissions (role_id, permission_id)
SELECT rp.role_id, pnew.id
FROM pagina_web.RolePermissions rp
JOIN pagina_web.Permissions pbandeja ON rp.permission_id = pbandeja.id AND pbandeja.code = 'bandeja.view'
CROSS JOIN pagina_web.Permissions pnew
WHERE pnew.code = 'uso_ia.view'
  AND NOT EXISTS (
        SELECT 1 FROM pagina_web.RolePermissions x
        WHERE x.role_id = rp.role_id AND x.permission_id = pnew.id
  );
GO
