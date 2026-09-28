/* ============================================================================
   Llevar al resto de los chatbots las tres reglas que arreglaron a Voltara
   Fecha: 2026-09-02
   Autor: equipo Acme

   QUÉ PASÓ
   --------
   Entre el 30/07 y el 21/08 el system_prompt de `voltara` (y después el de
   `voltara_digital`) recibió tres correcciones —anclaje al corpus, perspectiva
   del operador telefónico y la frase de "no encontré" como EXCLUYENTE— que
   subieron notoriamente el nivel de las respuestas. Ver:

       2026-07-30b_voltara_prompt_anclaje_corpus.sql
       2026-07-31_voltara_prompt_frase_excluyente.sql
       2026-07-31c_prompts_perspectiva_por_canal.sql

   Los otros 11 bots nunca las recibieron: siguen con el prompt original, que
   dice "Basa tu respuesta 100% en el contexto proporcionado. Si la información
   no está ahí, indica: <frase>". Esa formulación es la MISMA que hacía que
   Voltara se declarara sin información sobre consultas que recuperaban perfecto.

   LA MEDICIÓN (log de pagina_web.query_chatbots_logs, sin el tráfico del smoke
   test, al 2026-09-02) — "falso no encontré" = el bot dijo que no tenía la
   información aunque el mejor chunk que le llegó puntuó >= 3,0 en el reranker,
   que es el umbral con el que el propio sistema decide que el tema SÍ existe
   (CHATBOT_VACIO_SCORE_EXISTE):

       bot                   consultas   "no encontré"   falso "no encontré"
       voltara                   10400         609           78  (13% de ellos)
       benefix                    238          37           12  (32%)
       csv_isla_de_productos          78          14            8  (57%)
       vantix                       99          20            1  ( 5%)

   Un tercio a la mitad de los "no encontré" de esos bots son consultas que el
   manual respondía. Los casos son del mismo tipo que los de Voltara en julio:

       benefix  "combustible en bidones"                       score +5,69
       benefix  "transacciones manuales, el conductor no
                 esta en la estacion"                          score +7,56
       benefix  "una tarjeta virtual empresarial puede hacer
                 extracciones por el cajero automatico?"       score +5,79

   Y el mismo patrón de consulta corta que el operador escribe con el cliente
   en línea ("nuevo usuario", "presentacion", "LISTA POSITIVA", "VIAJEROS
   BANCENTRO") terminando en la frase de negación.

   El daño es doble, igual que en julio: el operador descarta una respuesta que
   estaba bien, y la pantalla de Vacíos de conocimiento se llena de temas que
   sí están documentados.

   QUÉ CAMBIA
   ----------
   1. **Regla de anclaje.** Ningún procedimiento, requisito, canal, teléfono,
      plazo ni monto puede salir de otro lado que no sea la documentación; se
      permite explícitamente lo que sí es seguro (el significado de un término
      general del rubro o del call center, y una cuenta matemática). En el
      prompt viejo eso estaba como un "100% en el contexto" que el modelo leía
      como "ante la duda, negate".
   2. **Perspectiva del operador telefónico.** La respuesta se ordena por lo
      que hace ÉL en la llamada (qué pregunta, qué carga, qué dice) y lo que se
      resuelve por otro canal baja al final como derivación en una línea.
   3. **La frase de "no encontré" pasa a ser EXCLUYENTE** —(a) responder con lo
      parcial, o (b) la frase sola— con la aclaración de que (b) es el mensaje
      COMPLETO, más la regla de consultas cortas y con typos. Es el texto que
      hoy tiene `voltara`, con los ejemplos neutralizados para que sirva a
      cualquier campaña.
   4. El recordatorio de "verificar en los sistemas oficiales" dejaba de ser
      compatible con (b) ("nada antes y nada después"). Se acota a las
      respuestas que traen información, en vez de sacarlo: es una decisión de
      Calidad, no del prompt. Voltara no lo tiene.

   `csv_isla_de_productos` tiene un prompt propio de 24k caracteres escrito por la
   campaña y NO se lo reemplaza: se le tocan solo las dos reglas de negación
   absoluta, se le agrega la de consultas cortas y se le convierte en reglas la
   cola de texto que hoy son recomendaciones dirigidas a una persona ("Un
   cambio que recomiendo especialmente...", "También dejaría como
   comportamiento obligatorio...") — 900 caracteres de conversación pegados en
   la posición más visible del prompt, el final.

   `paygo` entra aunque esté inactivo y sin documentos: si algún día se
   prende, que no arranque con el prompt viejo.

   QUÉ NO CAMBIA
   -------------
   `voltara` y `voltara_digital` quedan afuera: ya tienen las tres reglas. Las
   temperaturas (0,15 en todos los de procedimientos) tampoco se tocan.

   CÓMO CORRER
   -----------
   Contra la BD Acme. Idempotente (cada UPDATE está guardado por su propio
   CHARINDEX). NO requiere reindexar ni deploy: el system_prompt se lee de la
   tabla en cada instanciación y los workers lo recogen dentro del TTL del
   registry (CHATBOT_REGISTRY_TTL_SECONDS, 60s por defecto).

   La otra mitad de este cambio SÍ es de código y va por deploy: habilitar la
   desambiguación (ofrecer temas en vez de "no encontré") para estos bots en
   CHATBOT_DESAMBIGUACION_SLUGS — hoy es una lista con `voltara` solo.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

/* ============================================================================
   PARTE 1 — Los 11 bots que comparten el prompt original
   ============================================================================ */

/* ------------------------------------------------------- 1.1 Anclaje al corpus */

DECLARE @base NVARCHAR(MAX) = N'Tu base de conocimiento son exclusivamente los manuales y procedimientos internos proporcionados.';

DECLARE @base_anclada NVARCHAR(MAX) = N'Tu base de conocimiento son exclusivamente los manuales y procedimientos internos proporcionados.

**Regla de anclaje (la más importante):** todo PROCEDIMIENTO, requisito, documentación exigida, canal de atención, teléfono, correo, plazo o monto DEBE salir de la documentación proporcionada. Nunca los deduzcas, completes ni infieras por analogía con otra gestión parecida: el operador se los transmite al cliente como información oficial. Solo podés responder por fuera de la documentación cuando se trate de (a) el significado de un término general del rubro o del call center, o (b) una cuenta matemática.';

UPDATE pagina_web.Chatbots
SET system_prompt = REPLACE(system_prompt, @base, @base_anclada),
    updated_at = SYSDATETIME()
WHERE slug IN ('csv_bancentro', 'csv_commercial', 'csv_denuncias', 'csv_no_premium',
               'csv_premium', 'csv_pto_a_pto', 'csv_tokenizacion', 'csv_vip',
               'benefix', 'vantix', 'paygo')
  AND CHARINDEX(N'Regla de anclaje', system_prompt) = 0
  AND CHARINDEX(@base, system_prompt) > 0;
GO

/* --------------------------------------------- 1.2 Perspectiva del telefónico */

DECLARE @persp NVARCHAR(MAX) = N'**PERSPECTIVA — asistís al operador TELEFÓNICO.**
El operador tiene al cliente en línea AHORA. Ordená SIEMPRE la respuesta por lo que tiene que hacer ÉL durante la llamada:
1. Qué preguntarle o verificar con el cliente.
2. Qué cargar o ejecutar en el sistema (motivo, submotivo, observaciones, tipificación).
3. Qué decirle al cliente.
Cuando el trámite se completa por otro canal (autogestión web, app, sucursal, otro sector), esa información te sirve pero como DERIVACIÓN: va al FINAL y en una línea ("Derivá al cliente a: ..."). Nunca armes la respuesta alrededor del circuito de otro canal, ni le expliques al operador el trabajo que hace después otro equipo: él necesita resolver la llamada, no entender el back office.
Si un trámite NO se puede resolver en la llamada, decilo en la PRIMERA línea y pasá directo a cómo derivarlo.

**Tus Objetivos Principales:**';

UPDATE pagina_web.Chatbots
SET system_prompt = REPLACE(system_prompt, N'**Tus Objetivos Principales:**', @persp),
    updated_at = SYSDATETIME()
WHERE slug IN ('csv_bancentro', 'csv_commercial', 'csv_denuncias', 'csv_no_premium',
               'csv_premium', 'csv_pto_a_pto', 'csv_tokenizacion', 'csv_vip',
               'benefix', 'vantix', 'paygo')
  AND CHARINDEX(N'PERSPECTIVA', system_prompt) = 0;
GO

/* ------------------------------- 1.3 "No encontré" excluyente + consultas cortas */

DECLARE @frase_vieja NVARCHAR(MAX) = N'* Basa tu respuesta **100% en el contexto** proporcionado. Si la información no está ahí, indica: "No encontré información sobre ese tema específico en los manuales disponibles."';

DECLARE @frase_nueva NVARCHAR(MAX) = N'* Basa tu respuesta **en el contexto** proporcionado. Cuando el contexto NO alcanza, tenés dos opciones y son EXCLUYENTES entre sí:
  (a) Si en los documentos hay algo que sirva —aunque sea parcial, o cubra solo una parte de lo preguntado— RESPONDÉ con eso y aclará al final qué parte puntual no figura. En este caso NO uses la frase de abajo: sería confundir al operador diciéndole que no encontraste algo que sí encontraste.
  (b) Solo si NO hay absolutamente nada aprovechable en el contexto, respondé ÚNICAMENTE con esta frase y nada más: "No encontré información sobre ese tema específico en los manuales disponibles."
La frase de (b) va SOLA: es el mensaje COMPLETO, nada antes y nada después. Ni notas, ni aclaraciones entre paréntesis, ni "información relacionada al tema", ni "de todos modos te comento". Si mientras la escribís te dan ganas de agregar algo que sí encontraste, eso PRUEBA que estabas en el caso (a): borrá la frase y contestá con eso, que es lo que el operador necesita. Decir "no encontré" arriba de una respuesta correcta es peor que no responder, porque el operador la descarta. Y nunca inventes un procedimiento para evitar decir (b)
* **Consultas cortas, sueltas o con errores de tipeo:** el operador escribe con el cliente en línea, así que muchas veces manda dos o tres palabras sueltas ("nuevo usuario", "carga en bidones", "lista positiva") o con typos ("estracion", "titluar"). Eso NO habilita la frase de (b). Interpretalo como "contame todo lo que el manual dice sobre esto" y respondé con el procedimiento, los datos de carga y las condiciones que correspondan. Si el término aparece en el contexto como parte de una clasificación (por ejemplo un motivo o submotivo de gestión), eso YA es la respuesta: decile cuál es y cómo se carga. Solo pedí una aclaración si el término puede significar dos cosas muy distintas que el manual trata por separado, y en ese caso ofrecé las dos opciones en vez de negarte.';

UPDATE pagina_web.Chatbots
SET system_prompt = REPLACE(system_prompt, @frase_vieja, @frase_nueva),
    updated_at = SYSDATETIME()
WHERE slug IN ('csv_bancentro', 'csv_commercial', 'csv_denuncias', 'csv_no_premium',
               'csv_premium', 'csv_pto_a_pto', 'csv_tokenizacion', 'csv_vip',
               'benefix', 'vantix', 'paygo')
  AND CHARINDEX(@frase_vieja, system_prompt) > 0;
GO

/* ------------------------- 1.4 El recordatorio no puede colgarse del "no encontré" */

UPDATE pagina_web.Chatbots
SET system_prompt = REPLACE(
        system_prompt,
        N'Al final de cada respuesta, añade la siguiente advertencia en una nueva línea:',
        N'Al final de cada respuesta que CONTENGA información, añade la siguiente advertencia en una nueva línea. Nunca la agregues después de la frase de (b): esa frase va sola.'),
    updated_at = SYSDATETIME()
WHERE slug IN ('csv_bancentro', 'csv_commercial', 'csv_denuncias', 'csv_no_premium',
               'csv_premium', 'csv_pto_a_pto', 'csv_tokenizacion', 'csv_vip',
               'benefix', 'paygo')
  AND CHARINDEX(N'Al final de cada respuesta, añade', system_prompt) > 0;
GO

/* ============================================================================
   PARTE 2 — csv_isla_de_productos (prompt propio de la campaña: se toca lo mínimo)
   ============================================================================ */

/* ------------------ 2.1 La negación absoluta del punto 1 pasa a ser excluyente */

DECLARE @isla_neg NVARCHAR(MAX) = N'Si la información solicitada no se encuentra en la documentación disponible, responde exactamente:';

DECLARE @isla_neg_nueva NVARCHAR(MAX) = N'Cuando la documentación no alcanza para responder, tenés dos opciones y son EXCLUYENTES entre sí:
(a) Si en la base hay algo que sirva —aunque sea parcial, o cubra solo una parte de lo preguntado— RESPONDÉ con eso y aclará al final qué parte puntual no figura. En este caso NO uses la frase de (b): sería confundir al asesor diciéndole que no encontraste algo que sí encontraste. Responder con lo que sí está documentado NO es inventar, y es lo que el asesor necesita con el cliente en línea.
(b) Solo si NO hay absolutamente nada aprovechable en la base, respondé ÚNICAMENTE con esta frase y nada más:';

UPDATE pagina_web.Chatbots
SET system_prompt = REPLACE(system_prompt, @isla_neg, @isla_neg_nueva),
    updated_at = SYSDATETIME()
WHERE slug = 'csv_isla_de_productos' AND CHARINDEX(@isla_neg, system_prompt) > 0;
GO

/* ---------- 2.2 "La frase va SOLA" + consultas cortas, antes del punto 2 */

DECLARE @isla_sola NVARCHAR(MAX) = N'La frase de (b) va SOLA: es el mensaje COMPLETO, nada antes y nada después. Ni notas, ni aclaraciones entre paréntesis, ni "de todos modos te comento". Si mientras la escribís te dan ganas de agregar algo que sí encontraste, eso PRUEBA que estabas en el caso (a): borrá la frase y contestá con eso. Decir "no encontré" arriba de una respuesta correcta es peor que no responder, porque el asesor la descarta.
Consultas cortas, sueltas o con errores de tipeo
El asesor escribe con el cliente en línea, así que muchas veces manda dos o tres palabras sueltas ("lista positiva", "viajeros bancentro", "aviso de viaje") o con typos. Eso NO habilita la frase de (b) ni obliga a repreguntar. Interpretalo como "contame todo lo que el manual dice sobre esto" y respondé con el procedimiento, las condiciones y la registración que correspondan. Si el término aparece en la base como parte de una clasificación (una FLAG, un WHISPER, un tipo de gestión), eso YA es la respuesta: decí cuál es y cómo se aplica. Preguntá un dato solo cuando ese dato CAMBIE el procedimiento y el manual trate los casos por separado; en ese caso ofrecé las opciones en vez de negarte.
';

DECLARE @isla_ancla NVARCHAR(MAX) =
    N'________________________________________' + NCHAR(13) + NCHAR(10) + N'2. OBJETIVO PRINCIPAL';

UPDATE pagina_web.Chatbots
SET system_prompt = REPLACE(system_prompt, @isla_ancla,
        @isla_sola + NCHAR(13) + NCHAR(10) + @isla_ancla),
    updated_at = SYSDATETIME()
WHERE slug = 'csv_isla_de_productos'
  AND CHARINDEX(N'La frase de (b) va SOLA', system_prompt) = 0
  AND CHARINDEX(@isla_ancla, system_prompt) > 0;
GO

/* ------------------- 2.3 La misma negación absoluta, repetida en el punto 31 */

UPDATE pagina_web.Chatbots
SET system_prompt = REPLACE(
        system_prompt,
        N'Si la información no existe en la base:',
        N'Si NO hay absolutamente nada aprovechable en la base (y solo en ese caso: si hay algo parcial, va el caso (a) del punto 1):'),
    updated_at = SYSDATETIME()
WHERE slug = 'csv_isla_de_productos'
  AND CHARINDEX(N'Si la información no existe en la base:', system_prompt) > 0;
GO

/* ---- 2.4 La cola del prompt son recomendaciones a una persona: pasan a reglas */

DECLARE @isla_cola NVARCHAR(MAX) = N'Un cambio que recomiendo especialmente';

DECLARE @isla_reglas NVARCHAR(MAX) = N'33. REGLAS PUNTUALES
•	IPB, IP OK, IPEX OK, SIN IP e IP NO OK NO son equivalentes entre sí. Algunos manuales anteriores usan IPB donde otros dicen IP OK: no los interpretes como sinónimos ni traslades el procedimiento de uno al otro. Si el manual del caso no aclara cuál corresponde, decí qué falta definir en lugar de elegir uno.
•	Consultas sobre FLAG. Si la pregunta es "¿qué FLAG aplico?", primero determiná primer llamado o recontacto y si posee FLAG activa. Si la pregunta es "¿cómo cargo la FLAG?", explicá LYNX. Si la pregunta es "¿qué hago con una FLAG que ya posee?", determiná FLAG activa + WHISPER actual + recontacto.
';

UPDATE pagina_web.Chatbots
SET system_prompt = RTRIM(LEFT(system_prompt, CHARINDEX(@isla_cola, system_prompt) - 1))
                    + NCHAR(13) + NCHAR(10) + @isla_reglas,
    updated_at = SYSDATETIME()
WHERE slug = 'csv_isla_de_productos'
  AND CHARINDEX(@isla_cola, system_prompt) > 0;
GO

/* ============================================================================
   VERIFICACIÓN — las 4 columnas tienen que dar OK en todos los bots activos.
   `n/a` es lo esperado donde la regla no aplica a ese prompt.
   ============================================================================ */

SELECT slug,
       activo,
       CASE WHEN CHARINDEX(N'Regla de anclaje', system_prompt) > 0
                 OR CHARINDEX(N'Está prohibido', system_prompt) > 0 THEN 'OK' ELSE 'FALTA' END AS anclaje,
       CASE WHEN CHARINDEX(N'PERSPECTIVA', system_prompt) > 0
                 OR CHARINDEX(N'FLUJO MAESTRO DE ATENCIÓN TELEFÓNICA', system_prompt) > 0 THEN 'OK' ELSE 'FALTA' END AS perspectiva,
       CASE WHEN CHARINDEX(N'EXCLUYENTES entre sí', system_prompt) > 0 THEN 'OK' ELSE 'FALTA' END AS frase_excluyente,
       CASE WHEN CHARINDEX(N'Consultas cortas', system_prompt) > 0 THEN 'OK' ELSE 'FALTA' END AS consultas_cortas,
       CASE WHEN CHARINDEX(N'Al final de cada respuesta, añade', system_prompt) > 0 THEN 'REVISAR' ELSE 'OK' END AS recordatorio,
       CASE WHEN CHARINDEX(N'Un cambio que recomiendo', system_prompt) > 0 THEN 'REVISAR' ELSE 'OK' END AS sin_cola_conversada,
       LEN(system_prompt) AS largo
FROM pagina_web.Chatbots
ORDER BY activo DESC, slug;
GO
