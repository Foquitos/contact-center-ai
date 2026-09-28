/* ============================================================================
   Feature — Permiso 'template:modelo_ia' para ver/editar el modelo de Gemini
   de una plantilla (incluye precios por 1M de tokens)
   Fecha: 2026-07-03
   Autor: equipo Acme

   QUÉ AGREGA
   ----------
   Permiso pagina_web.Permissions ('template:modelo_ia') que protege:
     - backend FastAPI  GET /Auditoria/plantillas/modelos-ia (catálogo con precios)
       y el campo modelo_ia dentro de GET/POST/PUT /Auditoria/plantillas/*
       (RoleChecker(['template:modelo_ia']), ver app/routers/planillas_prompts.py)
     - frontend Flask   el selector de modelo en /plantillas
       (session['permissions'], ver templates/plantillas.html)

   A diferencia de 'uso_ia.view' (2026-06-25_uso_ia_permiso.sql), este permiso
   NO se asigna a ningún rol por default: el pedido explícito es que sea
   "especial" (revela precios internos por modelo). Asignarlo manualmente desde
   Administración > Gestionar Roles a los roles que corresponda (ej. Calidad/
   gerencia), o agregar un INSERT puntual acá si se prefiere automatizarlo.

   CÓMO CORRER
   -----------
   Ejecutar contra la BD Acme. Idempotente: el permiso se inserta si no existe.
   ============================================================================ */

SET XACT_ABORT ON;
GO

IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'template:modelo_ia')
    INSERT INTO pagina_web.Permissions (code, description)
    VALUES ('template:modelo_ia', N'Ver y modificar qué modelo de Gemini usa cada plantilla (incluye precios por modelo)');
GO
