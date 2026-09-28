# Decisiones de diseño

Decisiones que no se deducen leyendo el código: por qué algo está hecho así y no de la forma "obvia". Antes de
"arreglar" algo que parece raro, buscarlo acá. Cada entrada tiene contexto, decisión, consecuencia y fecha.
Solo están las que siguen vigentes al 23/09/2026; al final hay una sección de [decisiones abiertas](#abiertas).

Índice: [Infraestructura](#infraestructura) · [Auditorías](#auditorías) · [IA y costos](#ia-y-costos) ·
[Chatbots](#chatbots) · [Usuarios y permisos](#usuarios-y-permisos) · [Integraciones](#integraciones) ·
[Planificador](#planificador) · [Abiertas](#abiertas)

---

## Infraestructura

### Dev y prod comparten la base
- **Contexto:** hay un solo SQL Server (`10.0.0.11`, base `Acme`) y las tablas de datos las carga Reporting
  una sola vez.
- **Decisión:** SRV00 (dev) y SRV01 (prod) usan la misma base. Lo que se separa por servidor es el
  filesystem (`storage/`, audios, índices) y Qdrant.
- **Consecuencia:** un error en dev impacta en producción. De acá salen la columna `Entorno`, los jobs "solo
  prod", la regla de consultar con ventanas cortas y la prohibición de escribir en la base desde herramientas
  de desarrollo. Contenido y configuración (plantillas, chatbots, permisos) son compartidos a propósito.

### Columna `Entorno` en las colas, sin Redis
- **Contexto:** con la base compartida, un scheduler levantado en dev ejecutaba tareas programadas de prod y
  procesaba sus lotes (guardaba, mandaba mails y borraba carpetas del fileserver) con código no deployado. Se
  evaluó usar Redis para las colas.
- **Decisión:** no usar Redis. Toda tabla de ejecución lleva `Entorno` y cada proceso toma solo lo suyo con un
  claim atómico (`UPDATE … OUTPUT`). Con el volumen actual (decenas de tareas por semana, cientos de
  transcripciones) SQL Server sobra, y el estado de la cola es dato de negocio que se cruza con otras tablas
  en la misma transacción; una cola en memoria arriesga perder lotes.
- **Consecuencia:** una tarea programada creada desde dev no la corre prod. Toda tabla de ejecución nueva
  tiene que llevar `Entorno` (lo verifica `tests/test_entorno_colas_sql.py`). Reconsiderar Redis solo si la API
  pasa a varios servidores o el volumen crece en órdenes de magnitud.
- **Fecha:** 2026-07-15 (chatbots), 2026-09-22 (tareas, lotes y auditorías).

### Jobs fijos del scheduler solo en prod
- **Contexto:** los jobs que no son colas (auditoría diaria de CSV, alertas por mail, conciliación de cupos,
  vacíos de conocimiento) harían el trabajo dos veces si corrieran en los dos servidores.
- **Decisión:** se registran solo con `ENVIRONMENT=prod` (`agregar_job(..., solo_prod=True)`).
- **Consecuencia:** en dev se prueban llamando a la función a mano. Todo job nuevo se clasifica como cola por
  entorno o solo prod.
- **Fecha:** 2026-09-22.

### Crons solo en SRV01
- **Contexto:** los crons escriben en la base compartida y el portal de Enerval admite una sola sesión.
- **Decisión:** todo el crontab vive en SRV01; SRV00 no tiene crontab.
- **Consecuencia:** no reinstalar los crons en dev "para probar".
- **Fecha:** 2026-09-21.

### `.env` solo para secretos
- **Contexto:** el `.env` no viaja por git y un valor olvidado ahí pisa el de `config.py` sin avisar (pasó con
  el modelo de las plantillas).
- **Decisión:** el `.env` guarda solo secretos y `ENVIRONMENT`; todo lo demás es default en `config.py`.
- **Consecuencia:** un cambio de configuración es un commit y llega a los dos servidores. Ver
  [CONFIGURACION.md](CONFIGURACION.md).
- **Fecha:** 2026-08-14.

### Migraciones a mano, esquema versionado como foto
- **Contexto:** el esquema nació en la base y hasta el traspaso ni el esquema ni las migraciones estaban en git.
- **Decisión:** los cambios se entregan como migraciones aditivas e idempotentes en `scripts/migrations/`, que
  se aplican antes del deploy. Desde el 23/09/2026 las migraciones y una foto del esquema (`db/esquema/`)
  están en git.
- **Consecuencia:** no hay forma automática de saber qué migración está aplicada; hay que verificar lo que
  crea. La foto del esquema hay que regenerarla después de cada migración. Ver
  [BASE_DE_DATOS.md](BASE_DE_DATOS.md#migraciones).

---

## Auditorías

### Lista blanca en el proxy de auditoría
- **Contexto:** el formulario de `/Auditar` pasa por una ruta Flask (`frontend/app/routes/audit.py`) antes de
  llegar al backend.
- **Decisión:** el proxy arma el pedido con una lista blanca de campos (`payload_cleaned`), no reenvía todo lo
  que llega.
- **Consecuencia:** un parámetro nuevo que no se agregue ahí **se descarta sin error**. Las campañas por subida
  de archivos (CSV = empresa 10, Voltara = empresa 11) tienen rama propia en ese proxy.

### Batch por defecto; sincrónico con permiso propio
- **Contexto:** el modo sincrónico cuesta el doble y era el botón principal: se elegía por costumbre.
- **Decisión:** Batch es el default; "Auditar ahora" exige `audit:sync`, además de `audit:execute`.
- **Consecuencia:** los resultados llegan en horas, con mail. Las programaciones sincrónicas viejas siguen
  corriendo hasta que alguien las edite.
- **Fecha:** 2026-08-14.

### Lotes por archivo y cola de espera
- **Contexto:** Gemini admite 100 lotes en vuelo por proyecto. Al tocar el techo, el lote se descartaba en
  silencio y la carpeta de CSV se borraba igual.
- **Decisión:** el lote viaja como archivo JSONL de hasta 250 llamados; si hay 85 o más en vuelo, espera en
  `calidad.BatchPendientes` y sale cuando hay lugar. El umbral es 85 y no 100 porque el conteo se cachea un
  minuto y las transcripciones comparten el techo.
- **Consecuencia:** los lotes pueden tardar más en salir, pero no se pierden. No borrar
  `backend/storage/batch_pendientes/`.
- **Fecha:** 2026-08-26.

### Incidencia: la auditoría que no se puede hacer
- **Contexto:** un audio mudo fue "auditado" por la IA, que inventó la conversación a partir de los datos del
  llamado; otros llegaban cruzados entre operadores.
- **Decisión:** existe una tercera salida además de OK y fallida: **incidencia**. Un control con ffmpeg corta
  lo mudo antes de mandarlo a la IA, y la IA puede marcar operador o duración que no coinciden. Una incidencia
  bloqueante se guarda **sin detalles**, así queda con puntaje nulo y fuera de todo promedio sin tocar tableros
  ni procedimientos.
- **Consecuencia:** los umbrales del control de audio son conservadores: el error caro es descartar una
  llamada real. Se calibran con `scripts/calibrar_audio_mudo.py`.
- **Fecha:** 2026-08-18.

### La persona auditada se congela al auditar
- **Contexto:** la auditoría guarda el usuario de la plataforma, no la persona, y los usuarios se reciclan
  (medido: 19% de las auditorías colgaban de un usuario con más de un dueño). La persona se resolvía al leer,
  con la nómina de hoy.
- **Decisión:** `calidad.Auditorias.OperadorNominaID` guarda la persona resuelta al auditar, y
  `calidad.fn_ResolverOperadorAuditoria(usuario, empresa, fecha)` es la única regla de resolución (primero
  quien estaba vigente a la fecha y en la campaña; después criterios más débiles, informados en `Criterio`).
- **Consecuencia:** la atribución no cambia cuando cambia la nómina. Un `Criterio` 4 o 5 es una atribución por
  proximidad: mirar a mano.
- **Fecha:** 2026-08-25.

### `IdAplicativo` se calcula en un solo lugar
- **Contexto:** es la clave que une auditoría, transcripción y deduplicación.
- **Decisión:** se calcula solo en `calcular_id_aplicativo` (`AuditorIA/sql_a_Claude.py`).
- **Consecuencia:** el `NOT EXISTS` de cada builder de `SQL_query.py` lo arma inline y puede divergir: al
  tocar un builder, revisarlo contra esa función.

### Revisión humana guardada aparte
- **Contexto:** para medir la IA hace falta comparar su respuesta con la de un auditor.
- **Decisión:** la corrección del auditor no pisa la auditoría: va a `calidad.AuditoriaRevisiones`. Cada
  auditoría y revisión queda atada a la versión del prompt que la produjo (`calidad.PlantillaVersiones`).
- **Consecuencia:** se puede medir la IA, comparar prompts sin gastar tokens y volver atrás una plantilla.

### Cupo mensual por campaña
- **Contexto:** "Auditar", "Auditorías Realizadas" y el Dashboard se abrieron a todos los supervisores, y cada
  llamado auditado se paga.
- **Decisión:** bolsa mensual por campaña con tope opcional por usuario; mes calendario en hora de Argentina;
  se reserva al enviar y se ajusta al cerrar; al excederse **se bloquea** el pedido entero (no se recorta).
  Calidad y el super admin quedan fuera (`audit:cuota_exento`).
- **Consecuencia:** una campaña sin cupo cargado no bloquea a nadie pero se mide. Antes de cargar el primer
  cupo hay que darle `audit:cuota_exento` a Calidad.
- **Fecha:** 2026-08-31.

### Gate de plantillas: solo lo grave y solo lo nuevo
- **Contexto:** Calidad armaba plantillas con errores de redacción que después había que corregir a mano.
- **Decisión:** al guardar, solo frenan las señales de severidad **alta** y solo las que introduce ese
  guardado (con opción de "guardar igual", que queda registrada). El resto va al reporte semanal.
- **Consecuencia:** nadie queda bloqueado por problemas viejos de una plantilla.
- **Fecha:** 2026-08-26.

### Conocimiento de referencia de la plantilla: documento por documento, entero
- **Contexto:** en la plantilla de Hidra Comercial los atributos que dependen de saber la respuesta correcta no
  discriminaban.
- **Decisión:** la IA auditora puede leer documentos de los chatbots, elegidos **uno por uno** desde el editor
  (no el bot entero), y van **enteros** en la instrucción de sistema, no por búsqueda: el auditor recibe audio
  y no hay texto con qué buscar antes.
- **Consecuencia:** entran en la caché de contexto; solo cambian el hash de versión de las plantillas que los
  usan.
- **Fecha:** 2026-09-21.

### Audio conservado en disco, índice en la base
- **Contexto:** para escuchar lo auditado, transcribir después y armar golden sets hace falta el audio.
- **Decisión:** el audio ya comprimido se guarda en el disco de cada servidor
  (`backend/storage/audios_auditoria/`), con índice en `calidad.AudioAuditoria` y un tope de 100 GB por
  entorno que borra los más viejos (salvo los fijados por un golden set).
- **Consecuencia:** un audio auditado en prod no está en dev. Los servidores no tienen backup: perder el disco
  es perder los audios.
- **Fecha:** 2026-07-17.

### Transcribir después: Flex o Batch según el tamaño del pedido
- **Contexto:** una auditoría sin transcripción solo se podía transcribir re-auditándola entera.
- **Decisión:** la transcripción a demanda usa el audio conservado. Los pedidos chicos van por Flex (minutos)
  y los grandes por Batch (horas); cuestan lo mismo, así que el criterio es quién está esperando.
- **Consecuencia:** el motor está preparado para pasar a faster-whisper local si el servidor tiene GPU
  (`TRANSCRIPCION_MOTOR`); hoy no está implementado.
- **Fecha:** 2026-08-25.

---

## IA y costos

### Audio y archivos siempre inline a Gemini
- **Contexto:** desde el 14/08/2026 los modelos 3.x respondían 403 a los archivos subidos por la File API, y en
  batch el error venía **dentro** de un lote que figuraba como exitoso.
- **Decisión:** todo se manda inline (hasta 15 MB por pedido, `INLINE_MAX_BYTES`). La File API queda solo como
  excepción para lo que no entra.
- **Consecuencia:** ante un 403 raro, sospechar primero de un `file_uri`. Con `gemini-3.8-flash` la File API
  volvió a andar el 14/09, pero la decisión se mantiene.
- **Fecha:** 2026-08-14.

### Office convertido a texto antes de mandarlo
- **Contexto:** Gemini recibe un `.docx`, `.pptx` o `.xlsx` inline sin dar error, pero lo lee mal o inventa el
  contenido (medido con 3.8-flash).
- **Decisión:** el backend convierte los formatos modernos de Office a markdown con la librería estándar
  (`AuditorIA/office_a_texto.py`) y rechaza el Office viejo (`.doc`, `.ppt`, `.xls`) al subirlo.
- **Consecuencia:** sin dependencias nuevas; las notas del orador de un PowerPoint se conservan.
- **Fecha:** 2026-09-03.

### Nivel de razonamiento en vez de presupuesto
- **Contexto:** en Gemini 3.x el `thinking_budget` se acepta y se ignora; el razonamiento por auditoría se
  multiplicó y el costo casi se duplicó sin cambiar las plantillas.
- **Decisión:** cada plantilla elige un **nivel** (`LOW` / `MEDIUM` / `HIGH`, default `MEDIUM`) y el código
  manda `thinking_level`. `MINIMAL` solo para modelos medidos (`MODELOS_CON_MINIMAL`).
- **Consecuencia:** el nivel real de cada corrida queda en `AuditExecutionLog` para comparar costos.
- **Fecha:** 2026-08-19.

### Catálogo corto de modelos y precios con vigencia
- **Contexto:** el modelo "Pro" dejó de ser mejor que el flash vigente.
- **Decisión:** el catálogo de plantillas tiene dos opciones (Económica y Estándar). Los precios viven en
  `pagina_web.IA_Precios` con fecha de vigencia y no se borran al cambiar de modelo.
- **Consecuencia:** el histórico se sigue costeando con la tarifa de su momento. Checklist de cambio de
  modelo en OPERACION.md.
- **Fecha:** 2026-08-14.

### Caché de contexto por contenido
- **Contexto:** el bloque fijo de cada plantilla era casi la mitad de lo que se mandaba a la IA.
- **Decisión:** se sube una vez a la caché de Gemini, identificado por su contenido (no por plantilla), se
  reusa entre corridas y solo se crea si la corrida tiene suficientes llamados para pagar el almacenamiento.
  Un lote que espera cupo no referencia caché.
- **Consecuencia:** si la caché falla, la auditoría sale igual sin ella. El costo reportado descuenta los
  tokens cacheados en dos lugares que hay que mantener iguales (vista y `execution_log`).
- **Fecha:** 2026-09-01.

### Mail por corrida; el costo solo al administrador
- **Contexto:** un batch de varios lotes mandaba un mail por lote.
- **Decisión:** un solo mail por corrida (`run_id`) a los destinatarios de la tarea; el detalle técnico y de
  costo va aparte a `ADMIN_EMAIL`.
- **Fecha:** 2026-08-19.

---

## Chatbots

### Los bots viven en la base
- **Contexto:** los bots estaban definidos en `.env` y en Google Docs.
- **Decisión:** cada bot (prompt, documentos en markdown, permiso, PCRC, temperatura) vive en
  `pagina_web.Chatbots` y tablas asociadas y se administra desde la pantalla.
- **Consecuencia:** crear o cambiar un bot no requiere deploy. El índice son dos mitades locales a cada servidor
  (colección de Qdrant y `docstore.json`).
- **Fecha:** 2026-07-08.

### Estado de índice por entorno
- **Contexto:** dev y prod comparten la definición del bot pero cada uno tiene su propio Qdrant.
- **Decisión:** `pagina_web.ChatbotIndexState` guarda la versión de índice de cada bot por entorno.
- **Consecuencia:** dev puede reindexar sin tocar prod.
- **Fecha:** 2026-07-15.

### Reindexado a demanda
- **Contexto:** el reindexado nocturno reconstruía los 13 bots cada noche aunque no hubieran cambiado, y cada
  guardado de documento encolaba otro.
- **Decisión:** se sacó el nocturno y guardar no reindexa: el bot queda marcado "con cambios sin indexar" y se
  reindexa a mano cuando termina la carga.
- **Consecuencia:** después de editar documentos hay que acordarse de reindexar.
- **Fecha:** 2026-07-13 (nocturno), 2026-08-20 (guardado).

### Material en tres pasos
- **Contexto:** cada corrida del asistente de documentación regenera enteros los documentos que toca; cargar de a
  un archivo multiplicaba el costo.
- **Decisión:** sumar material (sin IA), procesar todo junto con IA (un paso, con confirmación) y reindexar.
  Un solo trabajo por bot a la vez, cancelable.
- **Fecha:** 2026-08-20, 2026-09-14.

### Cada pedazo del índice repite el título de su sección
- **Contexto:** las secciones largas se parten en pedazos y solo el primero decía de qué tema era: el bot
  "traía la mitad".
- **Decisión:** cada pedazo arranca con el encabezado de su sección y los nodos de solo título no se indexan.
  Los encabezados con sangría **no** se normalizan (medido: empeora).
- **Fecha:** 2026-09-14.

### Desambiguación en vez de "no encontré"
- **Contexto:** los operadores escriben corto ("poda", "medidor") y el bot contestaba "no encontré" aunque el
  manual tuviera varios temas parecidos.
- **Decisión:** en una banda de score calibrada, el bot ofrece los títulos de las secciones para elegir, sin
  llamar al modelo; y si el modelo igual se niega sobre material bien puntuado, la negación se reemplaza por
  la lista. La etiqueta es el título de la sección, no la ruta entera. Lo que se registra como vacío de
  conocimiento no cambia.
- **Consecuencia:** los umbrales están atados al reranker ms-marco; cambiar de reranker obliga a recalibrar.
- **Fecha:** 2026-08-24, 2026-09-02, 2026-09-04.

### Reglas comunes de los prompts de los bots
- **Contexto:** medido sobre el tráfico real, los bots improvisaban procedimientos o se negaban teniendo la
  respuesta.
- **Decisión:** todos los prompts comparten cuatro reglas: anclaje a la documentación, respuesta ordenada según
  el canal del operador, "no encontré" excluyente (o se responde o se dice la frase sola) y "un título del
  contexto es una consulta válida".
- **Consecuencia:** el texto para bots nuevos está en `DEFAULT_PROMPT_TEMPLATE`
  (`frontend/app/routes/chatbot_admin.py`) y tiene que moverse junto con las migraciones de prompts.
- **Fecha:** 2026-07-30 a 2026-09-04.

### No separar el contenido por canal
- **Contexto:** se pidió que el bot telefónico tuviera solo información de llamadas y el digital solo de mail.
- **Decisión:** no se separa el contenido (solo una parte chica es específica de un canal); el prompt ordena la
  respuesta según el canal. Sí se separaron en dos bots Voltara telefónico y Voltara Digital, porque el catálogo
  de cartas contaminaba al telefónico.
- **Fecha:** 2026-07-28, 2026-07-31.

### Tablas de datos fuera del RAG
- **Contexto:** listados de entidades (bases de instalación, carteras de clientes) se recuperaban mal por
  búsqueda semántica.
- **Decisión:** se guardan como tablas en SQL y se consultan antes del RAG, entera o buscando la fila. El modelo
  propone el esquema; las filas las copia un parser, nunca el modelo. Una respuesta de tabla nunca se cachea.
- **Fecha:** 2026-09-02.

### Imágenes en SQL Server
- **Contexto:** dev y prod tienen discos separados pero comparten la base.
- **Decisión:** las imágenes de los bots se guardan en `pagina_web.ChatbotImagenes`, deduplicadas por hash, y se
  sirven con sesión iniciada.
- **Fecha:** 2026-09-03.

### Adjuntos: el archivo es dato, no instrucción
- **Contexto:** los operadores necesitaban mandar la captura del error.
- **Decisión:** los adjuntos van inline y no se guardan; el contenido se le presenta al modelo como dato (contra
  instrucciones escondidas en un documento); la búsqueda sigue siendo por texto; quién puede adjuntar y en qué
  bot se decide en la base, para poder cortarlo sin deploy.
- **Fecha:** 2026-08-11, 2026-08-26.

---

## Usuarios y permisos

### Los permisos nuevos nacen sin asignar
- **Decisión:** toda migración que crea un permiso lo deja sin asignar a ningún rol; solo el super admin lo
  tiene hasta que se reparte.
- **Consecuencia:** aplicar una migración nunca amplía accesos por sorpresa. Después de un deploy con permiso
  nuevo, hay que asignarlo.
- **Fecha:** 2026-07-10.

### Jerarquía de roles y delegación sin escalada
- **Decisión:** un rol hijo hereda los permisos del padre. Quien gestiona roles sin ser super admin solo puede
  dar lo que él mismo tiene.
- **Fecha:** 2026-07-10.

### Alcance por empresa
- **Decisión:** los permisos `template:<empresa>` limitan qué empresas ve cada usuario en todo el módulo de
  auditorías, y se revalidan en cada corrida de una tarea programada contra su creador.
- **Consecuencia:** si el creador de una tarea pierde el permiso o se da de baja, la tarea se omite.
- **Fecha:** 2026-07-10.

### Blanqueo al documento
- **Contexto:** un administrador podía fijarle a mano la contraseña a otra persona.
- **Decisión:** el blanqueo vuelve la contraseña al número de documento y obliga a cambiarla al entrar; alta,
  blanqueo y baja son permisos separados y quedan registrados.
- **Fecha:** 2026-07-21.

### Presencia por latido
- **Decisión:** las sesiones activas se calculan por el refresco periódico de permisos (`GET /me`), sin tabla
  de sesiones.
- **Fecha:** 2026-07-23.

---

## Integraciones

### Genesys con usuario de consola
- **Contexto:** la cuenta de Benefix no tiene permiso para crear un cliente OAuth.
- **Decisión:** el sistema se loguea con un usuario de la consola web. Es la solución definitiva.
- **Consecuencia:** si alguien le cambia la clave o le activa segundo factor a esa cuenta, se corta la ingesta
  y la descarga de audios de Benefix.
- **Fecha:** 2026-09-21.

### Portal de Enerval sin navegador
- **Decisión:** el informe IVR se baja por HTTP puro, un día por pedido, en una tabla nueva que convive con la
  vieja.
- **Consecuencia:** sin Chrome en ese proceso; la carga es reanudable por día.
- **Fecha:** 2026-09-04.

---

## Planificador

Detalle del módulo en [PLANIFICADOR.md](PLANIFICADOR.md); el criterio estadístico en
[ESTADISTICA.md](ESTADISTICA.md).

| Fecha | Decisión | Por qué |
|---|---|---|
| 2026-09-07 | Línea de base con 52 semanas (eran 8) y nivel sobre 28 días (eran 14) | Con 8 semanas, una ola de calor de dos semanas era la mitad de las muestras de un día de semana y el pronóstico se disparaba |
| 2026-09-08 | Clima con persistencia y factor por día | El volumen de Voltara responde al frío y al calor |
| 2026-09-09 | Un solo pool en Voltara | Los operadores atienden varios skills; con tres pools separados cada uno pagaba su propio mínimo (medido: 17% menos horas-operador con uno) |
| 2026-09-10 | Pool formado solo por sub-campañas telefónicas; malla desde una sola tabla | Contar gente que no atiende el teléfono inflaba la cobertura |
| 2026-09-10 | NDS medido sobre las llamadas **entrantes** | La llamada que se cortó esperando también incumplió; así lo mide la operación |
| 2026-09-10 | Electrodependientes con techo de abandono y prioridad | Clientes críticos |
| 2026-09-10 | Un solo redondeo al final de la cadena de descuentos | Redondear en cada paso sumaba operadores fantasma |
| 2026-09-11 | Sin piso mínimo de operadores en el pool | El mínimo de 1 operador por media hora, inflado por la cadena de descuentos, pedía horas para colas sin llamadas |
| 2026-09-14 | Medias horas sin llamadas se rellenan con ceros | La serie solo traía las medias horas con llamadas: los domingos aparecían ~1.200 llamadas fantasma |
| 2026-09-14 | Combinar con el pronóstico del cliente solo domingos y feriados, con peso según el acierto reciente (tope 0,5) | Es donde nuestro pronóstico fallaba más; hábiles y sábados no se combinan |
| 2026-09-14 | Amplitud del clima 1,0 (fue 1,2) | Medido: 1,2 sobrerreaccionaba |
| 2026-09-14 | Digital como palanca de refuerzo, sin bajar "hacía falta" | La necesidad se informa real; Digital es una forma de cubrirla |
| 2026-09-14 | En fines de semana, pasarse es peor que quedarse corto; horizonte de decisión de 1 a 3 días | Costo de citar de más vs. error del pronóstico a más días |
| 2026-09-15 | Recálculo automático dos veces por día; perfil de presencia; curva de antigüedad; pedidos de refuerzo; escenarios | Plan siempre al día y medido contra la realidad |
| 2026-09-16 | Ausentismo medido como presencia en la línea; sub-campañas parciales; T1-Consumo como dedicada | Reflejar quién atiende de verdad |
| 2026-09-17 | Redondeo para abajo de 0 a 8 h en Voltara | De madrugada redondear para arriba agregaba un operador en cada media hora |
| 2026-09-22 | Hidra Técnico: feriado como sábado, días puente, persistencia e intradía | Su demanda se comporta distinto que la de Voltara |
| 2026-09-24 | Gasur: alta en Calidad con id fijo (30), feriados de Uruguay, NDS a 10 s, clima + GBDT + persistencia y 26 semanas de base | Es gas para calefacción (clima) y su horario se extendió en marzo de 2026 |
| 2026-09-24 | Gasur: forma del día de los últimos 28 días, persistencia que saltea los días atípicos y ancla al total mensual del cliente desde 7 días | Nos llega el desborde de su call center: la forma depende de su dotación, los paros duran un día y el total del mes lo fija el cliente |
| 2026-09-24 | El `Hasta` de un evento es exclusivo también al excluir del entrenamiento | Cada atípico sacaba además el día siguiente (todas las campañas) |

---

## Abiertas

Decisiones que quedaron sin tomar. El sistema funciona con el valor indicado.

| Tema | Estado actual | Qué falta decidir |
|---|---|---|
| Shrinkage medido contra el usado en el planificador | Se usa el cargado en `planificacion.Campana` (Voltara 7,3%) | Si se recalibra con la medición de presencia |
| Piso de cobertura por pool | Sin piso (`MinOperadores = 0`) | Si hace falta un mínimo operativo de madrugada |
| Horas extra habituales | Se miden (`planificador_extras.py`) pero no descuentan de la brecha | Si se incorporan al plan |
| Elasticidad del domingo | Implementada y apagada | Si se prende; hoy no mejora lo medido. (El GBDT del nivel diario se prendió en Voltara el 24/09/2026: ver PLANIFICADOR.md) |
| Umbral de nivel de servicio de Hidra Técnico | 80% en 30 s cargado | Confirmarlo con el cliente |
| Objetivo de nivel de servicio de Gasur | 80% en 10 s cargado (el umbral sale de `Gasur Eficiencia`) | Confirmar el 80% con el cliente |
| Fuente de clima | Open-Meteo con plan gratuito, de uso no comercial | Plan pago u otra fuente |
| Adjuntos del chatbot, fase 3 | Fases 1 y 2 hechas | Si se sigue: la adopción medida fue casi nula |
