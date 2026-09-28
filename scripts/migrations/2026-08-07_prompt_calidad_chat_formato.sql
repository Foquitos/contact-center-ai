/* ============================================================================
   Fix — La plantilla CALIDAD CHAT no sabía leer el archivo que recibe
   Fecha: 2026-08-07
   Autor: equipo Acme

   EL PROBLEMA
   -----------
   La plantilla 29 (CALIDAD CHAT, Odonto Plus / Facebook / WhatsApp) describe
   muy bien QUÉ evaluar, pero nunca dice CÓMO viene la conversación. El adjunto
   no es una transcripción de audio: es un JSON armado por
   AuditorIA/downloads/Mitrol.py::Chat_historico, que trae la gestión auditada,
   los chats previos del mismo cliente de los últimos 14 días y cada mensaje
   etiquetado con quién lo escribió.

   Sin decírselo, el modelo tiene que adivinar tres cosas de las que dependen
   varios criterios de la propia plantilla:

   1. A QUIÉN audita. Una conversación puede pasar por tres operadores; en el
      archivo eso viene marcado (`es_operador_auditado`), pero si no lo mira le
      carga a uno lo que hizo otro.
   2. QUÉ ES CONTEXTO. "Paciente Primera Vez (sin historial)" y "si hay chats
      previos en el mismo día no requiere volver a presentarse" solo se pueden
      resolver mirando las conversaciones previas, que están en el archivo pero
      NO se auditan.
   3. QUÉ TIPIFICACIÓN mirar en REGISTRACIÓN: cada gestión de la conversación
      tiene la suya y son de operadores distintos.

   Además hay tramos donde Mitrol no informa quién escribió cada mensaje: ahora
   llegan como INDETERMINADO en vez de atribuidos a la fuerza, y el prompt tiene
   que decir qué hacer con ellos (no marcar error sobre una duda).

   QUÉ CAMBIA
   ----------
   Se inserta un bloque [CÓMO LEER EL ARCHIVO DEL CHAT] antes de los criterios
   de evaluación. No se toca ningún criterio ni el formato de salida.

   CÓMO CORRER
   -----------
   Contra la BD Acme. Idempotente (no reinserta el bloque si ya está). No
   requiere deploy: la plantilla se lee de la tabla en cada corrida. La versión
   de plantilla se crea sola por hash en la primera auditoría posterior (ver
   AuditorIA/versionado.py), así que las auditorías viejas quedan trazadas a la
   versión anterior.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

/* El ancla es el título de la sección siguiente (aparece una sola vez en el
   prompt); el bloque nuevo se cuela arriba y lo deja intacto. Se evita anclar en
   la línea de guiones + salto de línea para no depender de si el archivo se
   guarda con LF o CRLF. */
DECLARE @bloque NVARCHAR(MAX) = N'[CÓMO LEER EL ARCHIVO DEL CHAT]

El archivo adjunto es un JSON con la conversación, no una transcripción de audio.
Leelo así:

- "gestion_a_auditar": la ÚNICA gestión que se evalúa. Indica el operador a
  auditar, su usuario, la tipificación con la que cerró y el cliente.
- "conversaciones": todos los chats de ese mismo cliente de los últimos 14 días,
  en orden cronológico. El que tiene "es_la_conversacion_a_auditar": true es el
  que se audita; los demás son CONTEXTO (historial del paciente) y NO se
  califican.
- "gestiones": una conversación puede pasar por varios operadores. Cada gestión
  tiene su operador y su tipificación; la marcada con "es_la_gestion_a_auditar":
  true es la de esta auditoría.
- "transcripcion": los mensajes en orden. "quien" dice quién escribió cada uno:
  OPERADOR, CLIENTE, BOT_IVR (respuesta automática del bot) o INDETERMINADO.
- "es_operador_auditado": true marca los mensajes del operador que estás
  evaluando. Calificá SOLO esos: lo que escribieron otros operadores o el
  BOT_IVR no se le computa, ni a favor ni en contra.
- "tipo": "evento" son hechos del sistema (transferencias, cierre del chat);
  "tipo": "aviso" son advertencias sobre la calidad de los datos.
- INDETERMINADO significa que la plataforma no informó quién escribió ese tramo.
  Deducilo por el contenido y, si te queda alguna duda, NO lo uses para marcar
  NO OK ni Error Crítico: la duda favorece al operador.

Consecuencias directas sobre los criterios de arriba:
- Paciente Primera Vez vs. Registrado, y "si hay chats previos en el mismo día no
  requiere volver a presentarse", se resuelven mirando las conversaciones de
  contexto y su fecha de inicio, no solo la auditada.
- La tipificación que se evalúa en REGISTRACIÓN es la de "gestion_a_auditar"; las
  de las otras gestiones son de otros operadores.
- Si el operador auditado no escribió ningún mensaje (por ejemplo, la
  conversación se transfirió antes de que interviniera), no inventes
  incumplimientos: usá N/A donde corresponda y aclaralo en el feedback.

--------------------------------------------------------------------------------
[CRITERIOS DE EVALUACIÓN OPERACIONAL]';

UPDATE calidad.Plantillas
SET SystemPrompt = REPLACE(SystemPrompt, N'[CRITERIOS DE EVALUACIÓN OPERACIONAL]', @bloque)
WHERE PlantillaID = 29
  AND CHARINDEX(N'[CÓMO LEER EL ARCHIVO DEL CHAT]', SystemPrompt) = 0
  AND CHARINDEX(N'[CRITERIOS DE EVALUACIÓN OPERACIONAL]', SystemPrompt) > 0;
GO

/* Verificación */
SELECT PlantillaID,
       Nombre,
       CASE WHEN CHARINDEX(N'[CÓMO LEER EL ARCHIVO DEL CHAT]', SystemPrompt) > 0
            THEN 'OK - bloque aplicado' ELSE 'FALTA' END AS Estado,
       LEN(SystemPrompt) AS LargoPrompt
FROM calidad.Plantillas
WHERE PlantillaID = 29;
GO
