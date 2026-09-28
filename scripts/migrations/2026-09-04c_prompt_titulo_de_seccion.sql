/* ============================================================================
   Un título del contexto es una consulta válida (no un "no encontré")
   Fecha: 2026-09-04
   Autor: equipo Acme

   QUÉ PASÓ
   --------
   Revisando los vacíos de conocimiento de voltara clasificados como 'generacion'
   —el bot TENÍA el dato servido en el contexto y aun así dijo que no lo
   encontró— aparecen casos donde la consulta es, letra por letra, el título de
   una sección que llegó PRIMERA en el contexto:

       consulta: "Aclaraciones Técnicas para la Categorización de SVP"
       contexto: "### Aclaraciones Técnicas para la Categorización de SVP
                  **Canal:** telefónico
                  1. Una caja ubicada en un poste se ingresa como Caja Toma..."   (score 3,85)
       respuesta: "No encontré información sobre ese tema específico..."

       consulta: "Definición y Casos de Aplicación"
       contexto: "### Definición y Casos de Aplicación
                  El Alta de Suministro es el trámite mediante el cual..."
       respuesta: "No encontré información sobre ese tema específico..."

   No es una consulta rara: es la que genera el PROPIO asistente. Cuando la
   pregunta no alcanza, el bot ofrece una lista de temas y el operador elige uno
   con un clic; lo que se manda como consulta es el título de esa sección
   (app/chatbot_desambiguacion.py). También llega tipeada, cuando el operador
   escribe el nombre del procedimiento como figura en el sistema.

   Sobre el tráfico de agosto (sin el smoke test) hay 57 consultas cuyo título
   coincide con un encabezado del contexto servido; 3 terminaron en la frase de
   negación. Es poco volumen, pero es el peor lugar donde fallar: el operador
   eligió de una lista que le armamos nosotros y le contestamos que eso no está.

   QUÉ CAMBIA
   ----------
   Una regla más en "Reglas de Respuesta", pegada a la de consultas cortas: si lo
   consultado coincide con el título de una sección del contexto, esa sección ES
   la respuesta. No inventa nada nuevo — es el caso más extremo de la regla (a),
   escrito aparte porque el modelo no lo estaba resolviendo solo.

   APLICAR: sin reindexar y sin reiniciar la API (TTL del registry, ~60s).
   ROLLBACK: REPLACE de @regla por '' en los mismos bots.
   ============================================================================ */

USE [Acme];
GO

/* ---------------------------------------------- 1. Los 13 con el prompt común */

DECLARE @ancla NVARCHAR(MAX) = N'ofrecé las dos opciones en vez de negarte.';

DECLARE @regla NVARCHAR(MAX) = N'ofrecé las dos opciones en vez de negarte.
* **Un título del contexto ES una consulta válida.** Si lo que te preguntan coincide (o casi) con el título de una sección de los documentos que recibiste, esa sección es la respuesta: desarrollala completa, con el procedimiento y los datos de carga. Suele llegar así porque el operador eligió un tema de una lista que le ofreció el propio asistente, o porque tipeó el nombre del trámite como figura en el sistema. Nunca respondas la frase de (b) cuando el título consultado está en el contexto.';

UPDATE pagina_web.Chatbots
SET system_prompt = REPLACE(system_prompt, @ancla, @regla),
    updated_at = SYSDATETIME()
WHERE CHARINDEX(@ancla, system_prompt) > 0
  AND CHARINDEX(N'Un título del contexto', system_prompt) = 0;
GO

/* ------------------------------- 2. csv_isla_de_productos (prompt propio, "asesor") */

DECLARE @ancla_isla NVARCHAR(MAX) = N'ofrecé las opciones en vez de negarte.';

DECLARE @regla_isla NVARCHAR(MAX) = N'ofrecé las opciones en vez de negarte.
Un título de la base ES una consulta válida. Si lo que te preguntan coincide (o casi) con el título de una sección de la documentación que recibiste, esa sección es la respuesta: desarrollala completa. Suele llegar así porque el asesor eligió un tema de una lista que le ofreció el propio asistente, o porque tipeó el nombre de la gestión como figura en el sistema. Nunca respondas la frase de (b) cuando el título consultado está en la documentación recibida.';

UPDATE pagina_web.Chatbots
SET system_prompt = REPLACE(system_prompt, @ancla_isla, @regla_isla),
    updated_at = SYSDATETIME()
WHERE slug = 'csv_isla_de_productos'
  AND CHARINDEX(@ancla_isla, system_prompt) > 0
  AND CHARINDEX(N'Un título de la base', system_prompt) = 0;
GO

/* ============================================================================
   VERIFICACIÓN — los 14 bots tienen que dar OK.
   ============================================================================ */

SELECT slug, activo,
       CASE WHEN CHARINDEX(N'Un título del contexto', system_prompt) > 0
                 OR CHARINDEX(N'Un título de la base', system_prompt) > 0
            THEN 'OK' ELSE 'FALTA' END AS regla_titulo,
       LEN(system_prompt) AS largo
FROM pagina_web.Chatbots
ORDER BY activo DESC, slug;
GO
