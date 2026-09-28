/* ============================================================================
   Fix — El bot de Voltara usa "No encontré información" como muletilla
   Fecha: 2026-07-31
   Autor: equipo Acme

   QUÉ PASÓ
   --------
   La migración 2026-07-30b (anclaje al corpus) dejó esta instrucción:

     "Si la información no está ahí, respondé EXACTAMENTE con la frase: '...' y,
      si el contexto trae algo relacionado aunque no sea lo pedido, aclaralo
      aparte después de esa frase."

   El modelo la leyó como permiso para PREFIJAR cualquier respuesta imperfecta
   con la frase, y después contestar igual. Medido sobre el tráfico del 30-31/07:

     "artefactos dañados"                        score +5.63 -> "No encontré... No obstante, ..."
     "numero de atencion por artefactos dañados" score +5.83 -> "No encontré... No obstante, ..."
     "canales de comunicación cnr script"        score +5.63 -> "No encontré... A pesar de..."

   Esos scores de reranker son altísimos: el chunk correcto SÍ estaba llegando.
   "artefactos dañados" es de las consultas más frecuentes (24 veces) y nunca
   había fallado. La tasa de "no encontré" saltó de ~25% a 47-80%.

   DOBLE DAÑO
   ----------
   1. El operador lee "No encontré información" antes de la respuesta correcta,
      así que desconfía de algo que estaba bien.
   2. La pantalla de Vacíos de conocimiento se llena de FALSOS POSITIVOS: temas
      que sí están documentados entran como huecos. Es exactamente lo contrario
      de lo que se buscaba al anclar el prompt.

   QUÉ CAMBIA
   ----------
   La frase pasa a ser EXCLUYENTE: o se responde, o se dice la frase, nunca las
   dos cosas. Se explicita que información parcial o relacionada es una respuesta
   válida (y que en ese caso hay que decir qué parte no está cubierta), y que la
   frase se reserva para cuando NO hay absolutamente nada útil en el contexto.

   Se mantiene intacto el anclaje al corpus de la 2026-07-30b: la regla de no
   inventar procedimientos/teléfonos/plazos no se toca. Lo que se corrige es
   cuándo corresponde declararse sin información.

   CÓMO CORRER
   -----------
   Contra la BD Acme. Idempotente. No requiere reindexar ni deploy: el prompt se
   relee de la tabla y los workers lo toman dentro del TTL del registry (~60s).
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

DECLARE @viejo NVARCHAR(MAX) = N'Si la información no está ahí, respondé EXACTAMENTE con la frase: "No encontré información sobre ese tema específico en los manuales disponibles." y, si el contexto trae algo relacionado aunque no sea lo pedido, aclaralo aparte después de esa frase. No reemplaces la frase por una respuesta propia ni por una aproximación: que el operador sepa que tiene que verificarlo es más útil que una respuesta inventada';

DECLARE @nuevo NVARCHAR(MAX) = N'Cuando el contexto NO alcanza, tenés dos opciones y son EXCLUYENTES entre sí:
  (a) Si en los documentos hay algo que sirva —aunque sea parcial, o cubra solo una parte de lo preguntado— RESPONDÉ con eso y aclará al final qué parte puntual no figura. En este caso NO uses la frase de abajo: sería confundir al operador diciéndole que no encontraste algo que sí encontraste.
  (b) Solo si NO hay absolutamente nada aprovechable en el contexto, respondé ÚNICAMENTE con esta frase y nada más: "No encontré información sobre ese tema específico en los manuales disponibles."
Nunca uses la frase de (b) como introducción de una respuesta: si vas a contestar, contestá. Y nunca inventes un procedimiento para evitar decir (b)';

UPDATE pagina_web.Chatbots
SET system_prompt = REPLACE(system_prompt, @viejo, @nuevo),
    updated_at = SYSDATETIME()
WHERE slug = 'voltara' AND CHARINDEX(@viejo, system_prompt) > 0;
GO

SELECT slug,
       CASE WHEN CHARINDEX(N'son EXCLUYENTES entre sí', system_prompt) > 0
            THEN 'OK' ELSE 'FALTA APLICAR' END AS frase_excluyente,
       CASE WHEN CHARINDEX(N'aunque no este en la informacion proporcionada', system_prompt) > 0
            THEN 'REVISAR: volvió el texto viejo' ELSE 'OK' END AS anclaje_intacto,
       LEN(system_prompt) AS largo
FROM pagina_web.Chatbots
WHERE slug = 'voltara';
GO
