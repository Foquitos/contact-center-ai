-- =========================================================================================
-- Migración: Actualización del prompt de DISCURSO para Odonto Plus Chat (PlantillaID = 29)
-- Objetivo: Reforzar la detección estricta de faltas ortográficas, mayúsculas indebidas
--           (ej. tras comas) y signos de puntuación, eliminando la cláusula permisiva.
-- Fecha: 2026-09-17
-- =========================================================================================

SET NOCOUNT ON;

DECLARE @PlantillaID INT = 29;
DECLARE @AtributoID INT = 285; -- Atributo 'DISCURSO'

DECLARE @NuevoPrompt NVARCHAR(MAX) = N'Evaluá si el operador auditado (mensajes con ''es_operador_auditado'': true) mantuvo un discurso adecuado, claro, con correcta ortografía, puntuación y alineado a las pautas de comunicación institucional de Odonto Plus:

1. PAUTAS GENERALES DEL DISCURSO:
- Vocabulario y tono: Debe utilizar un lenguaje articulado, claro, profesional y cercano/empático con el paciente.
- Denominación institucional: Es obligatorio utilizar el término "Clínica" para referirse a las sedes de atención. Queda terminantemente prohibido usar la palabra "Sucursal".
- Prolijidad y dosificación: La información debe brindarse de forma dosificada y en mensajes separados/ordenados, facilitando la lectura en el chat y evitando saturar de golpe al paciente.

2. ORTOGRAFÍA, MAYÚSCULAS Y SIGNOS DE PUNTUACIÓN (REGLAS ESTRICTAS DE REDACCIÓN):
Revisá minuciosamente el texto escrito por el operador auditado en busca de faltas formales de escritura:
- Faltas ortográficas y acentuación:
  * Palabras mal escritas, errores gramaticales o de concordancia de género/número.
  * Omisión notoria de tildes en palabras de uso común o nombres propios (ej. "Martin", "Marquez", "odontologo", "clinica", "proximo").
- Uso de Mayúsculas y Minúsculas:
  * Mayúsculas indebidas: NO se debe colocar mayúscula después de una coma o punto y coma (ejemplo incorrecto: "¡Hola, Un gusto!", "Buenos días, Te comento...").
  * Mayúsculas en medio de oraciones sin justificación gramatical.
  * Omisión de mayúsculas: Los nombres propios de personas (paciente u odontólogo), localidades, calles y clínicas DEBEN iniciar con mayúscula (ejemplo incorrecto: "mariel marquez", "francisco beiro", "ramos mejia").
- Signos de puntuación:
  * Uso correcto de comas, puntos y signos de interrogación o exclamación.
  * No omitir comas vocativas ni signos de puntuación básicos necesarios para la claridad del mensaje.
  * No dejar comas o puntos aislados ni pegar signos al texto siguiente sin espacio.

3. FALTAS CRÍTICAS OPERATIVAS (SE CONSIDERA ''EC'' SI OCURRE CUALQUIERA DE ESTAS SITUACIONES):
- Envío de plantilla solicitando datos ya brindados: Enviar un formulario, plantilla o mensaje pidiendo datos personales que el paciente ya aportó con anterioridad en la conversación (incluso si los brindó al bot inicial o a un operador previo en el mismo chat).
- Mensajes excesivamente extensos y sobrecargados ("mensajes choclo"): Enviar bloques masivos de texto que agrupen múltiples informaciones heterogéneas juntas en un único envío (por ejemplo: direcciones de clínicas + speech de reintegros de Obra Social + propuesta de Plan de Socios todo en un solo bloque), haciendo que se pierda la claridad, prolijidad y efectividad del mensaje.

4. GESTIONES SIN INTERVENCIÓN DEL OPERADOR:
- Si el operador auditado no llegó a escribir mensajes en la interacción (por ejemplo, chats transferidos de inmediato o cerrados antes de su intervención), calificar ''Ok'' para no perjudicarlo injustamente ante la falta de opción ''N/A''.

5. CRITERIOS PARA ELEGIR LA OPCIÓN:
- Seleccionar ''Ok'':
  * Redacción prolija, con ortografía correcta, puntuación adecuada y uso correcto de mayúsculas/minúsculas.
  * Mantuvo un tono empático y profesional.
  * Se refirió a las sedes como "clínica".
  * Dosificó la información en mensajes ordenados.
  * No reiteró pedidos de datos ya informados.
  * O bien, la gestión no contó con mensajes del operador auditado.
- Seleccionar ''No Ok'':
  * Desvíos de comunicación, redacción o estilo que no configuren Error Crítico, tales como:
    - Faltas ortográficas o de acentuación/tildes en palabras o nombres propios.
    - Mayúsculas indebidas (mayúscula tras coma, mayúsculas arbitrarias) o minúsculas en nombres propios/direcciones.
    - Signos de puntuación ausentes, redundantes o mal colocados.
    - Referirse a las sedes como "sucursal" en lugar de "clínica".
    - Tono inadecuado (excesivamente frío, distante, cortante o informal en exceso, sin llegar al insulto o agresión).
- Seleccionar ''EC'':
  * Incurrió en cualquiera de las faltas críticas operativas detalladas en el punto 3 (reiterar pedido de datos ya aportados o enviar mensajes masivos sobrecargando múltiples temas heterogéneos juntos).';

UPDATE calidad.Atributos
SET PromptAdyacente = @NuevoPrompt
WHERE AtributoID = @AtributoID AND PlantillaID = @PlantillaID;

PRINT 'Atributo DISCURSO actualizado exitosamente para Plantilla 29.';
