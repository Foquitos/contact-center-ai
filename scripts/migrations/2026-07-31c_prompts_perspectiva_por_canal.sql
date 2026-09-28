/* ============================================================================
   Fix — Cada bot de Voltara responde desde la perspectiva de SU canal
   Fecha: 2026-07-31
   Autor: equipo Acme

   EL PROBLEMA
   -----------
   El operador telefónico pregunta "baja de suministro" y recibe una respuesta
   armada alrededor del circuito digital (qué se sube a la Oficina Virtual, qué
   valida el equipo que responde los mails), así que tiene que repreguntar "¿y yo
   qué hago en la llamada?". La información correcta estaba ahí; el problema es
   desde dónde está ordenada.

   POR QUÉ NO SE SEPARA EL CONTENIDO
   ---------------------------------
   Se midió sobre los 6 documentos compartidos (201 secciones, 127k chars):

       solo teléfono      8,5% de las secciones
       solo digital      16,9%
       sirve a los dos    7,5%
       sin marca canal   67,2%   <- requisitos, documentación, definiciones

   Solo el 25% del contenido es específico de un canal. Separarlo duplicaría el
   72% restante en dos bases que después divergen — justo lo que evita
   ChatbotDocVinculo. Y buena parte de lo "solo digital" es lo que el operador
   telefónico necesita para DERIVAR al cliente ("la Oficina Virtual es el canal
   prioritario"): sacárselo lo dejaría sin cómo cerrar la llamada.

   QUÉ CAMBIA
   ----------
   1. Bloque de PERSPECTIVA en cada bot: el telefónico ordena la respuesta por lo
      que hace el operador en la llamada y manda la derivación digital al final en
      una línea; el Digital ordena por lo que verifica y responde por escrito.
   2. voltara_digital arrastraba el permiso de responder fuera del corpus (nació
      antes de la migración 2026-07-30b) y la frase de "no encontré" condicionada.
      Se le aplican las mismas correcciones que ya tiene el telefónico, en su
      versión EXCLUYENTE (ver 2026-07-31, la de la muletilla).
   3. voltara_digital decía "si es algo que debe decir verbalmente, iniciá con 🗣️":
      instrucción de canal telefónico en el bot que responde por escrito.

   CÓMO CORRER
   -----------
   Contra la BD Acme. Idempotente. No requiere deploy ni reindexar: el prompt se
   relee de la tabla y los workers lo toman dentro del TTL del registry (~60s).
   Conviene correrla DESPUÉS de 2026-07-31_voltara_prompt_frase_excluyente.sql.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

/* ------------------------------------------------- 1. Perspectiva telefónica */

DECLARE @persp_tel NVARCHAR(MAX) = N'**PERSPECTIVA — asistís al operador TELEFÓNICO.**
El operador tiene al cliente en línea AHORA. Ordená SIEMPRE la respuesta por lo que tiene que hacer ÉL durante la llamada:
1. Qué preguntarle o verificar con el cliente.
2. Qué cargar en el sistema (motivo, submotivo, observaciones).
3. Qué decirle al cliente.
Muchos trámites se completan por canal digital (Oficina Virtual, App, email servicio). Esa información te sirve, pero como DERIVACIÓN: va al FINAL y en una línea ("Derivá al cliente a: ..."). Nunca armes la respuesta alrededor del circuito digital, ni le expliques al operador el trabajo que hace después el equipo Digital: él necesita resolver la llamada, no entender el back office.
Si un trámite es EXCLUSIVAMENTE digital, decilo en la PRIMERA línea y pasá directo a cómo derivarlo.

**Tus Objetivos Principales:**';

UPDATE pagina_web.Chatbots
SET system_prompt = REPLACE(system_prompt, N'**Tus Objetivos Principales:**', @persp_tel),
    updated_at = SYSDATETIME()
WHERE slug = 'voltara'
  AND CHARINDEX(N'PERSPECTIVA — asistís al operador TELEFÓNICO', system_prompt) = 0;
GO

/* ---------------------------------------------------- 2. Perspectiva digital */

DECLARE @persp_dig NVARCHAR(MAX) = N'**PERSPECTIVA — asistís al operador de CANAL DIGITAL.**
El operador responde por escrito (correo, Oficina Virtual, App). No tiene al cliente en línea, así que NO puede repreguntar en el momento: si falta un dato, hay que pedirlo en la misma respuesta. Ordená SIEMPRE la respuesta por lo que tiene que hacer ÉL:
1. Qué verificar en el sistema y en lo que el cliente ya envió.
2. Qué falta pedirle, si falta algo (todo junto, no de a una cosa por vez).
3. Qué responderle por escrito.
Lo que es propio del canal telefónico (indagaciones en vivo, protocolos de llamada, derivaciones a un 0800) es contexto y no la respuesta: mencionalo solo si el caso hay que derivarlo al canal telefónico.

**Tus Objetivos Principales:**';

UPDATE pagina_web.Chatbots
SET system_prompt = REPLACE(system_prompt, N'**Tus Objetivos Principales:**', @persp_dig),
    updated_at = SYSDATETIME()
WHERE slug = 'voltara_digital'
  AND CHARINDEX(N'PERSPECTIVA — asistís al operador de CANAL DIGITAL', system_prompt) = 0;
GO

/* --------------------------------- 3. Anclaje al corpus en el bot Digital */

DECLARE @viejo_ancla NVARCHAR(MAX) = N', si te preguntan por un tema que puede estar relacionado con Voltara, o terminos que el operador desconoce, puuedes responderle aunque no este en la informacion proporcionada, al igual que cuentas matematicas.';

DECLARE @nuevo_ancla NVARCHAR(MAX) = N'.

**Regla de anclaje (la más importante):** todo PROCEDIMIENTO, requisito, documentación exigida, canal de atención, teléfono, correo, plazo o monto DEBE salir de la documentación proporcionada. Nunca los deduzcas, completes ni infieras por analogía con otra gestión parecida: lo que escribís sale con la firma de la empresa. Solo podés responder por fuera de la documentación cuando se trate de (a) el significado de un término general del rubro eléctrico o del call center, o (b) una cuenta matemática.';

UPDATE pagina_web.Chatbots
SET system_prompt = REPLACE(system_prompt, @viejo_ancla, @nuevo_ancla),
    updated_at = SYSDATETIME()
WHERE slug = 'voltara_digital' AND CHARINDEX(@viejo_ancla, system_prompt) > 0;
GO

/* ------------------- 4. Frase de "no encontré" EXCLUYENTE en el bot Digital */

DECLARE @viejo_frase NVARCHAR(MAX) = N'Si la información no está ahí, indica: "No encontré información sobre ese tema específico en los manuales disponibles." siempre y cuando no sea una pregunta facil de responder';

DECLARE @nuevo_frase NVARCHAR(MAX) = N'Cuando el contexto NO alcanza, tenés dos opciones y son EXCLUYENTES entre sí:
  (a) Si en los documentos hay algo que sirva —aunque sea parcial— RESPONDÉ con eso y aclará al final qué parte puntual no figura. En este caso NO uses la frase de abajo: sería confundir al operador diciéndole que no encontraste algo que sí encontraste.
  (b) Solo si NO hay absolutamente nada aprovechable, respondé ÚNICAMENTE con esta frase y nada más: "No encontré información sobre ese tema específico en los manuales disponibles."
Nunca uses la frase de (b) como introducción de una respuesta: si vas a contestar, contestá';

UPDATE pagina_web.Chatbots
SET system_prompt = REPLACE(system_prompt, @viejo_frase, @nuevo_frase),
    updated_at = SYSDATETIME()
WHERE slug = 'voltara_digital' AND CHARINDEX(@viejo_frase, system_prompt) > 0;
GO

/* ------------- 5. El bot Digital no habla: escribe (el 🗣️ era de teléfono) */

UPDATE pagina_web.Chatbots
SET system_prompt = REPLACE(
        system_prompt,
        N'Si es algo que debe decir verbalmente, inicia con 🗣️.',
        N'Si es texto para enviarle al cliente por escrito, inicia con ✉️.'),
    updated_at = SYSDATETIME()
WHERE slug = 'voltara_digital'
  AND CHARINDEX(N'debe decir verbalmente', system_prompt) > 0;
GO

/* ------------------------------------------------------------ verificación */

SELECT slug,
       CASE WHEN CHARINDEX(N'PERSPECTIVA —', system_prompt) > 0 THEN 'OK' ELSE 'FALTA' END AS perspectiva,
       CASE WHEN CHARINDEX(N'aunque no este en la informacion proporcionada', system_prompt) > 0
            THEN 'FALTA' ELSE 'OK' END AS anclaje,
       CASE WHEN CHARINDEX(N'siempre y cuando no sea una pregunta facil', system_prompt) > 0
            THEN 'FALTA' ELSE 'OK' END AS frase_excluyente,
       CASE WHEN CHARINDEX(N'debe decir verbalmente', system_prompt) > 0
            THEN 'REVISAR' ELSE 'OK' END AS canal_correcto,
       LEN(system_prompt) AS largo
FROM pagina_web.Chatbots
WHERE slug IN ('voltara', 'voltara_digital')
ORDER BY slug;
GO
