/* ============================================================================
   Feature — Permiso propio para auditar en modo SINCRÓNICO (audit:sync)
   Fecha: 2026-08-14
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   audit:sync — habilita el botón "Auditar ahora" de /Auditar y las
   programaciones del Scheduler con is_batch = 0. Es ADICIONAL a audit:execute:
   auditar sigue siendo audit:execute, pero hacerlo en el modo caro se asigna
   aparte.

   POR QUÉ
   -------
   El batch de Gemini sale a MITAD DE PRECIO que el sincrónico (ver
   backend/app/tasks.py). El sincrónico solo aporta ver el resultado en el acto,
   que casi nunca hace falta: los resultados quedan igual en "Auditorías
   Realizadas" y se avisan por correo. Sin embargo era el botón primario de la
   pantalla y quien no conocía la diferencia auditaba siempre así.

   Junto con esta migración, el código:
   - pone Batch como modo por defecto y botón primario en /Auditar (y deja el
     sincrónico como acción secundaria, visible solo con este permiso);
   - deja el Scheduler con "modo Batch" tildado por defecto;
   - rechaza en el backend (403) el POST /Auditar/ con batch=false y el
     alta/edición de schedulers con is_batch=false sin este permiso.

   DECISIÓN: nace SIN ASIGNAR (mismo criterio que audit:scheduler / uso_ia.chatbot
   en 2026-07-13). Hasta que se asigne rol por rol, solo el super admin puede
   auditar en sincrónico; TODOS los roles con audit:execute pasan a auditar en
   batch. Es el efecto buscado: el sincrónico se pide, no se hereda.

   Para asignarlo a un rol, descomentar el bloque del final con los nombres que
   correspondan (por ejemplo el rol de Calidad que prueba plantillas nuevas).

   CÓMO CORRER: contra la BD Acme, idempotente, ANTES del deploy del código.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'audit:sync')
BEGIN
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('audit:sync',
            'Auditar en modo sincrónico ("Auditar ahora", doble de costo que Batch); requiere además audit:execute');
END
GO

/* ---------------------------------------------------------------------------
   OPCIONAL — asignar el permiso a roles concretos.
   Descomentar y ajustar la lista de nombres de rol antes de correr.

DECLARE @roles TABLE (name NVARCHAR(100));
INSERT INTO @roles (name) VALUES ('Jefe Calidad');   -- ajustar

INSERT INTO pagina_web.RolePermissions (role_id, permission_id)
SELECT r.id, p.id
FROM pagina_web.Roles r
CROSS JOIN pagina_web.Permissions p
JOIN @roles rr ON rr.name = r.name
WHERE p.code = 'audit:sync'
  AND NOT EXISTS (SELECT 1 FROM pagina_web.RolePermissions rp
                  WHERE rp.role_id = r.id AND rp.permission_id = p.id);
--------------------------------------------------------------------------- */

SELECT code, description FROM pagina_web.Permissions
WHERE code IN ('audit:sync', 'audit:execute', 'audit:scheduler');
GO

/* Diagnóstico (no modifica nada): programaciones ACTIVAS que hoy corren en
   sincrónico. Siguen corriendo igual (el scheduler no pide permisos al ejecutar),
   pero son las candidatas obvias a pasar a Batch para bajar el gasto: alcanza con
   editarlas y tildar "Ejecutar en modo Batch". */
SELECT id,
       task_name,
       created_by,
       ISNULL(JSON_VALUE(parametros_json, '$.is_batch'), 'false') AS is_batch
FROM calidad.AuditSchedulers
WHERE is_active = 1
  AND ISNULL(JSON_VALUE(parametros_json, '$.is_batch'), 'false') <> 'true'
ORDER BY task_name;
GO
