/* ============================================================================
   Fix — Dos formas de negarse a usar información que SÍ tenía delante
   Fecha: 2026-08-10
   Autor: equipo Acme

   DE DÓNDE SALE
   -------------
   De los 16 temas en Vacíos de conocimiento clasificados 'generacion' (el bot
   recibió el dato en el contexto y aun así dijo que no sabía). Se leyeron los
   contextos reales de query_chatbots_logs y hay DOS defectos distintos.

   (A) LA MULETILLA VOLVIÓ DISFRAZADA DE "NOTA RELACIONADA"
   --------------------------------------------------------
   Caso real (log 9286, repetido 3 veces). Consulta: "si ya tiene aviso de corte
   cuantos dias le quedan con luz". Respuesta:

     "No encontré información sobre ese tema específico en los manuales
      disponibles.  *(Nota relacionada al tema encontrada en los documentos:*
      Para los Grandes Clientes - Tarifas T2 y T3, el suministro queda disponible
      para corte comercial a los 5 días corridos posteriores al vencimiento de la
      factura y se emite un aviso de corte con un plazo máximo de 24 horas de
      anticipación...)"

   O sea: CONTESTÓ la pregunta —5 días y 24 horas— y la envolvió en un "no
   encontré". El operador lee la negación primero y descarta una respuesta buena.

   La regla vigente (2026-07-31) dice "nunca uses la frase como INTRODUCCIÓN de
   una respuesta". El modelo cumple la letra: no la usa de introducción, la usa
   de encabezado y agrega la respuesta después, entre paréntesis. El agujero es
   que la regla no prohibía AGREGAR nada después de la frase.

   (B) LAS CONSULTAS CORTAS SE RECHAZAN DE PLANO
   ----------------------------------------------
   11 de las 14 respuestas de 'generacion' miden exactamente 78 caracteres: la
   frase de rechazo sola. Las consultas son fragmentos, porque el operador
   escribe con el cliente en línea: "alta tension", "mail sdervicios",
   "diferencia 50$ comprobantes comercios", "reclamo alta tension".

   Caso real (log 9683). Consulta: "alta tension". El contexto entregado traía
   "Con Suministro - Habituales CS: Submotivos: `Baja Tensión`, `Oscilaciones` y
   `Sobretensión`" MÁS el procedimiento completo de carga del reclamo en
   Salesforce. Eso es exactamente lo que el operador necesitaba, y el bot
   respondió que no encontró información.

   El prompt no dice qué hacer con una consulta ambigua, así que el modelo elige
   lo más seguro: negarse. Para el operador, negarse es el peor resultado.

   CÓMO CORRER
   -----------
   Contra la BD Acme. Idempotente. Aplica a los dos bots de Voltara. No requiere
   deploy ni reindexar: el prompt se relee de la tabla dentro del TTL del
   registry (~60s).
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

DECLARE @nl NVARCHAR(2) = CHAR(13) + CHAR(10);

/* ------------- (A) La frase de rechazo es el mensaje COMPLETO, no un prefijo */

DECLARE @viejo_frase NVARCHAR(MAX) =
    N'Nunca uses la frase de (b) como introducción de una respuesta: si vas a contestar, contestá';

DECLARE @nuevo_frase NVARCHAR(MAX) =
    N'La frase de (b) va SOLA: es el mensaje COMPLETO, nada antes y nada después. ' +
    N'Ni notas, ni aclaraciones entre paréntesis, ni "información relacionada al tema", ni "de todos modos te comento". ' +
    N'Si mientras la escribís te dan ganas de agregar algo que sí encontraste, eso PRUEBA que estabas en el caso (a): ' +
    N'borrá la frase y contestá con eso, que es lo que el operador necesita. ' +
    N'Decir "no encontré" arriba de una respuesta correcta es peor que no responder, porque el operador la descarta';

UPDATE pagina_web.Chatbots
SET system_prompt = REPLACE(system_prompt, @viejo_frase, @nuevo_frase),
    updated_at = SYSDATETIME()
WHERE slug IN ('voltara', 'voltara_digital')
  AND CHARINDEX(@viejo_frase, system_prompt) > 0;
GO

/* --------------------- (B) Una consulta corta no es motivo para no responder */

DECLARE @nl2 NVARCHAR(2) = CHAR(13) + CHAR(10);

DECLARE @ancla NVARCHAR(MAX) =
    N'* Evita frases de relleno como "Basado en el texto...". Ve directo a la solución.';

DECLARE @regla_cortas NVARCHAR(MAX) =
    N'* **Consultas cortas, sueltas o con errores de tipeo:** el operador escribe con el cliente en línea, ' +
    N'así que muchas veces manda dos palabras ("alta tension", "mail servicios") o con typos ("mail sdervicios"). ' +
    N'Eso NO habilita la frase de (b). Interpretalo como "contame todo lo que el manual dice sobre esto" y ' +
    N'respondé con el procedimiento, los motivos/submotivos de carga y los datos que correspondan. ' +
    N'Si el término aparece en el contexto como parte de una clasificación (por ejemplo un submotivo de reclamo), ' +
    N'eso YA es la respuesta: decile cuál es y cómo se carga. ' +
    N'Solo pedí una aclaración si el término puede significar dos cosas muy distintas que el manual trata por separado, ' +
    N'y en ese caso ofrecé las dos opciones en vez de negarte.';

UPDATE pagina_web.Chatbots
SET system_prompt = REPLACE(system_prompt, @ancla, @regla_cortas + @nl2 + @ancla),
    updated_at = SYSDATETIME()
WHERE slug IN ('voltara', 'voltara_digital')
  AND CHARINDEX(@ancla, system_prompt) > 0
  AND CHARINDEX(N'Consultas cortas, sueltas o con errores de tipeo', system_prompt) = 0;
GO

/* ------------------------------------------------------------ verificación */

SELECT slug,
       CASE WHEN CHARINDEX(N'es el mensaje COMPLETO', system_prompt) > 0
            THEN 'OK' ELSE 'FALTA' END AS frase_va_sola,
       CASE WHEN CHARINDEX(N'Consultas cortas, sueltas o con errores de tipeo', system_prompt) > 0
            THEN 'OK' ELSE 'FALTA' END AS regla_consultas_cortas,
       CASE WHEN CHARINDEX(N'como introducción de una respuesta', system_prompt) > 0
            THEN 'REVISAR (quedó la regla vieja)' ELSE 'OK' END AS regla_vieja,
       LEN(system_prompt) AS largo
FROM pagina_web.Chatbots
WHERE slug IN ('voltara', 'voltara_digital')
ORDER BY slug;
GO
