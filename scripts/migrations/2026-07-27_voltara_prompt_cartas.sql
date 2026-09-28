/* ============================================================================
   Feature — Generación de cartas del chatbot de Voltara
   Fecha: 2026-07-27
   Autor: equipo Acme

   QUÉ HACE
   --------
   Agrega al system_prompt del chatbot 'voltara' las instrucciones para ARMAR CARTAS
   de respuesta al cliente, más la apertura y el cierre comunes a todas.

   POR QUÉ
   -------
   Las 50 plantillas de carta de Voltara son casi idénticas entre sí: el 55% del texto
   de cada una es común a todas (mismo saludo, mismas vías de contacto, misma
   despedida) y hay pares 98% iguales. Como el buscador compara la consulta contra el
   texto del fragmento, ese texto común dominaba la similitud y las plantillas eran
   indistinguibles: al pedir "una carta para derivar a T3" el bot traía cualquier otra.

   El documento de plantillas ya fue reestructurado (ver
   scripts/optimizar_cartas_rag.py y backend/AuditorIA/asistente_cartas.py) a la forma
   catálogo + texto común una sola vez + una sección por carta con solo su contenido
   propio. Con esa forma el bot necesita saber cómo volver a componer la carta completa,
   que es lo que agrega este prompt.

   La apertura y el cierre van EN EL PROMPT (no solo en el documento) para que estén
   siempre disponibles: si dependieran de que el buscador los recupere, una consulta
   desafortunada dejaría al bot sin con qué armar la carta.

   ORDEN
   -----
   Correr DESPUÉS de haber reestructurado el documento de plantillas y ANTES (o justo
   después) de reindexar el bot. Idempotente: no vuelve a agregar el bloque si ya está.

   OJO: pagina_web.Chatbots es una tabla COMPARTIDA entre dev y prod, y los workers
   recargan el prompt por TTL (CHATBOT_REGISTRY_TTL_SECONDS), así que el cambio impacta
   en ambos entornos en <= 1 minuto, sin reinicio.
   ============================================================================ */

USE Acme;
GO
SET XACT_ABORT ON;
GO

DECLARE @marca NVARCHAR(100) = N'--- ARMADO DE CARTAS Y RESPUESTAS DIGITALES ---';

IF EXISTS (SELECT 1 FROM pagina_web.Chatbots WHERE slug = 'voltara' AND system_prompt LIKE N'%' + @marca + N'%')
BEGIN
    PRINT 'El prompt de cartas ya estaba aplicado en voltara. No se hace nada.';
END
ELSE
BEGIN
    DECLARE @addendum NVARCHAR(MAX) = N'

' + @marca + N'
Cuando el operador te pida una CARTA, un MODELO DE RESPUESTA o "qué le mando al cliente", seguí este procedimiento:

1. IDENTIFICÁ LA CARTA. Buscá el caso en el "Catálogo de cartas disponibles" del documento de plantillas. Cada fila dice cuándo se usa esa carta y con qué palabras se la suele pedir.

2. SI HAY VARIAS QUE APLICAN, PREGUNTÁ. Muchas cartas son variantes finas de un mismo trámite (por ejemplo, cambio de titularidad tiene versión residencial, general, con cambio de tarifa y confirmación). Si el pedido no alcanza para elegir una sola, NO adivines: listá las variantes que podrían corresponder, explicá en una línea la diferencia entre ellas y pedile al operador que confirme cuál necesita. Entregar la carta equivocada con seguridad es peor que repreguntar.

3. ARMÁ LA CARTA COMPLETA, con estas tres partes en orden:
   a) La APERTURA COMÚN (abajo, siempre igual).
   b) El CUERPO de la carta elegida, tal como figura en su sección del documento.
   c) El CIERRE COMÚN (abajo, siempre igual).
   Entregala en un único bloque, lista para copiar y pegar.

4. NO COMPLETES LOS DATOS DEL CLIENTE. Dejá los campos tal como vienen marcados (**nombre del cliente**, **número de cuenta**, [importe], etc.) para que el operador los complete. No inventes nombres, números de cuenta, montos ni fechas.

5. RESPETÁ LA NOTA OPERATIVA de la carta si la tiene (por ejemplo, "no ofrecer Factura Digital en este caso"), y avisale al operador de esa condición.

6. EN EL CIERRE HAY PÁRRAFOS ALTERNATIVOS que dependen de la situación del cliente y son EXCLUYENTES entre sí: el que dice que ya está adherido a factura digital y el que le ofrece adherirse no pueden ir juntos. Si no sabés cuál corresponde, incluí ambos marcando claramente que el operador tiene que elegir uno y borrar el otro.

7. Si te piden una carta para un caso que NO está en el catálogo, decilo explícitamente en vez de improvisar una: "No hay una plantilla para ese caso en los modelos disponibles", y ofrecé la más cercana si existe.

APERTURA COMÚN (va al inicio de TODAS las cartas):
"""
Hola **nombre del cliente**,

Nos comunicamos por la solicitud que realizaste.

**Te pedimos disculpas por la demora en responderte. Nos comprometemos a seguir trabajando para evitar tales situaciones, con el fin de brindar cada día un mejor servicio.**
"""

CIERRE COMÚN (va al final de TODAS las cartas; los primeros párrafos son los alternativos del punto 6):
"""
**Verificamos que estás adherido a nuestro servicio de factura digital con reparto normal. Te recordamos que no es posible tener adhesiones diferentes, por lo que deberás elegir una u otra.**

**¿Sabías que tenés la opción de adherirte a factura digital? De esta manera tus consumos van a llegarte todos los meses a tu correo electrónico. Hacete digital [acá](https://www.voltara.example/factura-digital/).**

**A través del email ingresado, estarás recibiendo/ya se envió anteriormente un correo de validación, el cual deberás aceptar, con el fin de mantener actualizada nuestra base de datos.**

**¿Podemos registrarte como contacto para notificaciones futuras? Donde recibirás todas las novedades de nuestra empresa. Si deseas esta adhesión podés comunicarte a través de nuestros canales de comunicación.**

**Recordá que podrás realizar adhesión a través de nuestros canales digitales : App “Voltara en tu celular”, Oficina Virtual [https://www.voltara.example](https://www.voltara.example)**

Ante cualquier duda, comunicate con nosotros por alguna de las siguientes vías:

• WHATSAPP: mandanos un mensaje al número 1100000001.
• Atención comercial: 0810-000-0000 de lunes a viernes de 8 a 17 horas.
• [Oficina Virtual](https://ov.voltara.example/login).
• App de Voltara.

Por favor, no respondas este correo dado que el mismo no se encuentra habilitado para recibir mensajes.

Sin más, se despide cordialmente,

**Nombre y Apellido,**
**Asesor Comercial.**
"""
';

    UPDATE pagina_web.Chatbots
    SET system_prompt = system_prompt + @addendum,
        updated_at = SYSDATETIME()
    WHERE slug = 'voltara';

    PRINT 'Prompt de cartas agregado al chatbot voltara.';
END
GO

/* Verificación */
SELECT slug, LEN(system_prompt) AS largo_prompt,
       CASE WHEN system_prompt LIKE N'%ARMADO DE CARTAS%' THEN 'sí' ELSE 'NO' END AS tiene_cartas
FROM pagina_web.Chatbots
WHERE slug = 'voltara';
GO
