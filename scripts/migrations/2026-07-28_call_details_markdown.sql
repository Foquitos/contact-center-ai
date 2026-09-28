/* ============================================================================
   Ajuste — El bloque de contexto de la interacción pasa de XML a Markdown
   Fecha: 2026-07-28
   Autor: equipo Acme

   QUÉ HACE
   --------
   Actualiza los system prompts de plantillas que describen el bloque de contexto
   como el tag XML `<Call_details>`, para que lo nombren como la sección Markdown
   `## Call_details` que ahora recibe el modelo.

   POR QUÉ
   -------
   `AuditorIA/gemini.py::prompt_details` arma los metadatos de la llamada/chat que
   viajan junto al audio (duración, tiempo en cola, tipificación, comentario del
   agente, etc.). Antes los mandaba como XML:

       <Call_details>
         <Duración>245</Duración>
         ...
       </Call_details>

   Ahora los manda como Markdown:

       ## Call_details
       - Duración: 245
       ...

   Motivo: el XML repetía el nombre de cada campo dos veces (apertura + cierre) y
   obligaba a escapar `<`, `>` y `&`; el Markdown es más barato en tokens y el modelo
   lo interpreta igual o mejor. Además ahora se omiten los campos vacíos, que antes
   viajaban como tags vacíos sin aportar nada.

   El NOMBRE del bloque (`Call_details`) se mantiene justamente para no invalidar los
   prompts que lo mencionan; lo único que cambia acá son los `<` `>` alrededor.

   ORDEN
   -----
   Se puede correr antes o después del deploy: solo cambia texto descriptivo del
   prompt. Es idempotente (si ya no queda ningún `<Call_details>`, no toca nada).
   ============================================================================ */

USE [Acme];
GO

UPDATE calidad.Plantillas
SET SystemPrompt = REPLACE(SystemPrompt, '<Call_details>', 'Call_details')
WHERE SystemPrompt LIKE '%<Call_details>%';
GO

-- Verificación: debe devolver 0 filas.
SELECT PlantillaID, Nombre
FROM calidad.Plantillas
WHERE SystemPrompt LIKE '%<Call_details>%';
GO
