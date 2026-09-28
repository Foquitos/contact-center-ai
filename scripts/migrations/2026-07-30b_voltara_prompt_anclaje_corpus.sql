/* ============================================================================
   Fix — Anclar la respuesta del bot telefónico de Voltara a la documentación
   Fecha: 2026-07-30
   Autor: equipo Acme

   QUÉ CAMBIA
   ----------
   Reemplaza dos partes del system_prompt de `voltara`:

   1. Saca la autorización a responder FUERA del corpus. Decía:
        "si te preguntan por un tema que puede estar relacionado con Voltara, o
         terminos que el operador desconoce, puedes responderle aunque no este en
         la informacion proporcionada"
      En un asistente de PROCEDIMIENTOS de call center eso es peligroso: el bot
      improvisa pasos de gestión que el operador le transmite al cliente como si
      fueran oficiales. Se acota a lo que sí es seguro: conceptos generales del
      rubro y cuentas matemáticas, dejando explícito que un procedimiento, un
      requisito, un canal o un dato de contacto SOLO puede salir de los manuales.

   2. Endurece la frase de "no tengo esa información". Decía "...siempre y cuando
      no sea una pregunta facil de responder", lo que dejaba al modelo decidir
      cuándo improvisar. Ahora la frase es obligatoria y textual.

   POR QUÉ IMPORTA MÁS ALLÁ DE LA CALIDAD DE RESPUESTA
   ---------------------------------------------------
   La pantalla "Vacíos de conocimiento" (migración 2026-07-30) detecta qué le
   falta a la documentación leyendo justamente esa frase. Mientras el bot tenga
   permiso de improvisar, un hueco real se ve como una respuesta normal y Calidad
   nunca se entera de que hay algo que documentar. Anclar el prompt es lo que
   hace que la señal exista.

   CÓMO CORRER
   -----------
   Contra la BD Acme. Idempotente (si el texto ya fue reemplazado no hace nada).
   NO requiere reindexar: el system_prompt se lee de la tabla en cada
   instanciación del bot y los workers lo recogen dentro del TTL del registry
   (CHATBOT_REGISTRY_TTL_SECONDS, 60s por defecto).
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

DECLARE @viejo_apertura NVARCHAR(MAX) = N', si te preguntan por un tema que puede estar relacionado con Voltara, o terminos que el operador desconoce, puuedes responderle aunque no este en la informacion proporcionada, al igual que cuentas matematicas.';

DECLARE @nuevo_apertura NVARCHAR(MAX) = N'.

**Regla de anclaje (la más importante):** todo PROCEDIMIENTO, requisito, documentación exigida, canal de atención, teléfono, correo, plazo o monto DEBE salir de la documentación proporcionada. Nunca los deduzcas, completes ni infieras por analogía con otra gestión parecida: el operador se los transmite al cliente como información oficial. Solo podés responder por fuera de la documentación cuando se trate de (a) el significado de un término general del rubro eléctrico o del call center, o (b) una cuenta matemática.';

DECLARE @viejo_frase NVARCHAR(MAX) = N'Si la información no está ahí, indica: "No encontré información sobre ese tema específico en los manuales disponibles." siempre y cuando no sea una pregunta facil de responder';

DECLARE @nuevo_frase NVARCHAR(MAX) = N'Si la información no está ahí, respondé EXACTAMENTE con la frase: "No encontré información sobre ese tema específico en los manuales disponibles." y, si el contexto trae algo relacionado aunque no sea lo pedido, aclaralo aparte después de esa frase. No reemplaces la frase por una respuesta propia ni por una aproximación: que el operador sepa que tiene que verificarlo es más útil que una respuesta inventada';

UPDATE pagina_web.Chatbots
SET system_prompt = REPLACE(system_prompt, @viejo_apertura, @nuevo_apertura),
    updated_at = SYSDATETIME()
WHERE slug = 'voltara' AND CHARINDEX(@viejo_apertura, system_prompt) > 0;

UPDATE pagina_web.Chatbots
SET system_prompt = REPLACE(system_prompt, @viejo_frase, @nuevo_frase),
    updated_at = SYSDATETIME()
WHERE slug = 'voltara' AND CHARINDEX(@viejo_frase, system_prompt) > 0;
GO

/* Verificación: no debe quedar rastro de la autorización vieja. */
SELECT slug,
       CASE WHEN CHARINDEX(N'aunque no este en la informacion proporcionada', system_prompt) > 0
            THEN 'FALTA APLICAR' ELSE 'OK' END AS anclaje,
       CASE WHEN CHARINDEX(N'siempre y cuando no sea una pregunta facil', system_prompt) > 0
            THEN 'FALTA APLICAR' ELSE 'OK' END AS frase_sin_cobertura,
       LEN(system_prompt) AS largo
FROM pagina_web.Chatbots
WHERE slug = 'voltara';
GO
