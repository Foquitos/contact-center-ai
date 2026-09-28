/* ============================================================================
   La frase de "no encontré" pasa a invitar a repreguntar
   Fecha: 2026-09-04
   Autor: equipo Acme

   QUÉ PASÓ
   --------
   Cuando el bot no puede responder, los 14 prompts mandan una frase canónica:

       "No encontré información sobre ese tema específico en los manuales
        disponibles."

   Es una puerta cerrada. Le dice al operador —que tiene al cliente en línea—
   que el manual no tiene el tema, cuando en la enorme mayoría de los casos lo
   que pasó es que la consulta era de dos palabras y no alcanzó para elegir.
   Medido sobre agosto (log de pagina_web.query_chatbots_logs, sin el tráfico
   del smoke test): de los 382 "no encontré" de voltara, 89 salieron con el mejor
   fragmento por encima de 3,0 en el reranker, o sea sobre material que el propio
   sistema da por documentado. La frase no solo era falsa: además desalentaba el
   único movimiento que la arreglaba, que es volver a preguntar con más detalle.

   QUÉ CAMBIA
   ----------
   La frase nueva dice lo mismo sin cerrar la puerta: no afirma que el tema no
   exista (dice que no lo encontró ESCRITO ASÍ), y pide de forma concreta las dos
   cosas que de verdad mejoran la búsqueda — qué necesita resolver el operador y
   con qué palabras figura en el sistema.

   No cambia nada más del prompt: sigue siendo la frase de (b) de la regla
   EXCLUYENTE (2026-09-02c), sigue yendo SOLA y sigue empezando con "No
   encontré", que es lo que la detección de vacíos de conocimiento
   (app/vacios_conocimiento.py, _PATRONES_SIN_COBERTURA) usa para reconocerla.
   Mide 178 caracteres: bien por debajo de CHATBOT_VACIO_LARGO_RESPUESTA (400,
   el largo a partir del cual una negación "igual contestó") y de
   CHATBOT_DESAMB_NEGATIVA_MAX_CHARS (280, hasta donde se la puede reemplazar
   por la lista de temas). Si alguna vez se alarga, revisar esos dos números.

   En el mismo cambio va por deploy la desambiguación DESPUÉS de generar
   (app/chatbot_desambiguacion.py, `proponer_tras_negativa`): los casos con score
   alto ya no llegan a esta frase, se responden con la lista de temas.

   APLICAR: sin reindexar y sin reiniciar la API. El prompt se relee dentro del
   TTL del registry (~60s).

   ROLLBACK: el mismo REPLACE al revés (@nueva por @vieja).
   ============================================================================ */

USE [Acme];
GO

DECLARE @vieja NVARCHAR(MAX) = N'No encontré información sobre ese tema específico en los manuales disponibles.';

DECLARE @nueva NVARCHAR(MAX) = N'No encontré ese tema escrito así, pero puede estar documentado con otras palabras. Contame un poco más —qué necesitás resolver, o cómo aparece en el sistema— y lo busco de nuevo.';

UPDATE pagina_web.Chatbots
SET system_prompt = REPLACE(system_prompt, @vieja, @nueva),
    updated_at = SYSDATETIME()
WHERE CHARINDEX(@vieja, system_prompt) > 0;
GO

/* ============================================================================
   VERIFICACIÓN — `frase_nueva` tiene que dar OK en los 14 bots y `frase_vieja`
   tiene que quedar en 0. csv_isla_de_productos la repite 3 veces en su prompt
   propio: REPLACE las cambia todas.
   ============================================================================ */

SELECT slug,
       activo,
       CASE WHEN CHARINDEX(N'No encontré ese tema escrito así', system_prompt) > 0
            THEN 'OK' ELSE 'FALTA' END AS frase_nueva,
       (LEN(system_prompt) - LEN(REPLACE(system_prompt,
            N'No encontré información sobre ese tema específico en los manuales disponibles.', N''))) / 77
            AS frase_vieja,
       LEN(system_prompt) AS largo
FROM pagina_web.Chatbots
ORDER BY activo DESC, slug;
GO
