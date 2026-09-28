# foquitos/chatcsva/Foquitos-ChatCSVa.../app/config.py
#
# CRITERIO DE DÓNDE VA CADA VALOR
# --------------------------------
# El `.env` NO viaja por git: vive en cada máquina (SRV00 dev / SRV01 prod) y hay que
# editarlo a mano en cada una. Por eso el `.env` guarda SOLO dos cosas:
#   1. secretos (claves de API, contraseñas, cadenas de conexión, SECRET_KEY);
#   2. lo que legítimamente difiere entre máquinas (ENVIRONMENT, rutas si no coinciden).
# Todo lo demás —modelos de IA, umbrales, URLs de los portales, IDs de Sheets, casillas
# de correo, parámetros del RAG— vive acá como valor por defecto, así viaja con el deploy
# y dev y prod no se desincronizan solos.
#
# Sigue siendo todo sobreescribible: si una variable está en el `.env`, gana sobre el
# default de este archivo. Eso es útil para probar algo en dev, pero OJO con dejarla
# pegada: una línea vieja en el `.env` silencia el cambio que se hizo acá (fue exactamente
# lo que pasó con GEMINI_PLANTILLAS_MODEL, que quedó pinneado a un modelo anterior).
import os
from pydantic_settings import BaseSettings
from typing import List, Optional

# Raíz del backend (backend/), derivada de este archivo (backend/app/config.py).
# Se usa para anclar rutas de almacenamiento por defecto sin depender del CWD.
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class Settings(BaseSettings):
    # Database (SECRETO: va en el .env)
    connection_string: str
    CONNECTION_STRING_chatbot: str

    # FastAPI/JWT Security. Solo la clave de firma es secreta; el algoritmo y la
    # duración del token son decisiones de diseño y viajan con el código.
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 360

    # CORS Origins (Se carga como string, se parsea en main.py).
    # Son los hosts desde los que se sirve el frontend: localhost para desarrollo local
    # y la IP del servidor para dev/prod (7000 = Flask, 5000 = alterno).
    CORS_ORIGINS: str = (
        "http://localhost:7000,http://127.0.0.1:7000,"
        "http://10.0.0.12:7000,http://10.0.0.12:5000,"
        "http://localhost:5000,http://127.0.0.1:5000"
    )

    # Entorno de esta instancia ("prod" | "dev"). dev y prod comparten el mismo SQL,
    # así que la cola de reindexado (pagina_web.ChatbotIndexJobs) se reclama y se marca
    # por entorno: cada scheduler toca SOLO sus propios jobs. Default "prod": sin setear,
    # el comportamiento es idéntico al actual. dev debe poner ENVIRONMENT=dev en su .env.
    ENVIRONMENT: str = "prod"
    # Si True (prod), el registry re-instancia el bot cuando cambia index_version/updated_at
    # en la BD compartida (hot-reload al publicar un reindex). En dev conviene False: fija la
    # instancia cargada al arrancar e ignora los bumps de version que dispara prod (evita el
    # thrashing de recargar un índice que dev no tiene). dev adopta su propio reindex con un
    # restart del backend. Ver ChatbotRegistry.get().
    CHATBOT_FOLLOW_INDEX_RELOAD: bool = True

    # Logging. La ruta es la misma en dev y prod (discos separados, misma estructura);
    # si alguna máquina la tuviera distinta, se sobreescribe por .env.
    DEFAULT_LOG_DIR: str = "/var/www/chatcsva/storage/main_logs"
    LOG_LEVEL: str = "INFO"

    # API Keys (SECRETO: van en el .env)
    GEMINI_AUDITORIA_API_KEY: str
    GEMINI_CHATBOT_API_KEY: str
    # Modelo de Gemini para el asistente de plantillas (mejorar/generar prompts).
    # Es una tarea de bajo volumen y alto valor (ingeniería de prompts), por eso usaba
    # un modelo "Pro" más capaz que el flash de las auditorías; desde 2026-08-14 usa el
    # mismo flash que ellas (hoy gemini-3.8-flash), que supera en capacidad al Pro
    # anterior. Se deja configurable por .env para poder cambiar el identificador
    # sin redeploy.
    GEMINI_PLANTILLAS_MODEL: str = "gemini-3.8-flash"
    # Modelo de Gemini para el asistente de documentación RAG (formatear contenido
    # crudo -> markdown estructurado + verificar completitud + merge incremental).
    # Ver AuditorIA/asistente_docs.py. Cambiar este setting alcanza para mover el
    # asistente a otro modelo, sin redeploy.
    GEMINI_DOCS_MODEL: str = "gemini-3.8-flash"
    # Modelo del asistente de cartas (analiza cartas ya escritas y separa la parte fija
    # de la variable). Comparte el camino de asistente_docs pero NO su modelo: acá la
    # tarea es mecánica y de lote, no ingeniería de prompts. Un "lite" alcanza, cuesta
    # ~5x menos por token y —a diferencia del flash de auditorías— acepta
    # thinking_level=MINIMAL, así que no gasta nada en razonamiento (ver
    # AuditorIA/razonamiento.py).
    GEMINI_CARTAS_MODEL: str = "gemini-3.5-flash-lite"
    # Modelo del asistente del manual (la burbuja de ayuda; ver app/asistente_manual.py).
    # Flash a propósito: recibe el manual entero (~14.5k tokens) en cada pregunta, así
    # que el costo lo domina la entrada y no la capacidad del modelo.
    GEMINI_MANUAL_MODEL: str = "gemini-3.8-flash"
    # Modelo del asistente analista del dashboard de auditorías (ver app/asistente_dashboard.py).
    GEMINI_DASHBOARD_MODEL: str = "gemini-3.8-flash"
    GEMINI_DASHBOARD_THINKING_LEVEL: str = "HIGH"
    # Lectura de los avisos de corte de Hidra para el planificador (ver
    # app/planificador_cortes.py). Unas decenas de avisos por año: el costo no pesa, y
    # resolver "este jueves" contra la fecha de publicación pide el flash y no el lite.
    PLANIFICADOR_CORTES_MODELO: str = "gemini-3.8-flash"
    # Rondas máximas del loop de verificación de completitud: cada ronda es una
    # llamada extra a Gemini que compara la fuente cruda contra el markdown generado
    # y lista lo que falta; si encuentra faltantes, una llamada de reparación los
    # integra y se re-verifica. 0 desactiva el loop (solo transforma).
    CHATBOT_DOCS_VERIFY_ROUNDS: int = 3
    # Tamaño de fuente (en caracteres) a partir del cual el formateo deja de hacerse en
    # UNA sola llamada y pasa al camino "plan -> un documento por llamada". Una respuesta
    # no puede superar max_output_tokens (65535, compartido con el thinking), así que un
    # material grande no entra entero: se trunca el JSON. Con el plan, cada llamada emite
    # un solo documento y el límite deja de ser un techo para el material total.
    CHATBOT_DOCS_MAX_CHARS_UNA_PASADA: int = 60000
    DEEPGRAM_API_KEY: str
    ANTHROPIC_API_KEY: str
    OPENROUTESERVICE_API_KEY: str

    # Service Credentials (SECRETO: usuarios y contraseñas van en el .env).
    # Las URLs de login de cada portal están más abajo, junto al resto de la config
    # no secreta: no son credenciales y cambian para todas las máquinas a la vez.
    VERINT_USER: str
    VERINT_PASS: str
    CXONE_USER: str
    CXONE_PASS: str
    CXONE_TOPC: str
    YOIZEN_USER: str
    YOIZEN_PASS: str
    MITROL_USER: str
    MITROL_PASS: str
    HERMES_USER: str
    HERMES_PASS: str
    # Número de puesto de trabajo con el que Hermes registra la sesión. No es un
    # secreto (no autentica por sí solo), pero acompaña al login de Hermes.
    HERMES_STATION: str = "89550"
    CYT_USER: str
    CYT_PASS: str
    # Cantidad de navegadores Selenium en paralelo para descargar audios de CYT/Orion.
    # 1 = comportamiento serial original. Subirlo acelera la descarga pero abre N
    # logins simultáneos al recorder CYT; bajar a 1 si el sistema no tolera la concurrencia.
    CYT_DOWNLOAD_WORKERS: int = 4
    # Audios que se comprimen en paralelo al armar un lote de batch (el audio viaja
    # inline en el request, ya no se sube a la File API: ver AuditorIA/gemini_files.py).
    # Es trabajo de ffmpeg local, así que el techo real es la CPU de la máquina.
    GEMINI_BATCH_UPLOAD_WORKERS: int = 15

    # AuditorIA - Caché de contexto de la plantilla (ver AuditorIA/cache_plantillas.py).
    # El bloque fijo de cada plantilla (instrucción de sistema + consignas de los
    # atributos) es el 47% del input de auditorías y hasta ahora se pagaba entero en
    # cada llamado. Cacheado sale ~10 veces más barato, a cambio de pagar
    # almacenamiento por hora.
    GEMINI_CACHE_PLANTILLAS: bool = True
    # Cuánto vive la caché desde el último uso. 24 h cubre el plazo completo del batch:
    # si venciera con el lote todavía en la cola de Gemini, esos requests fallarían y
    # las auditorías se perderían. Una plantilla que se usa todos los días mantiene UNA
    # caché viva (el TTL se corre en cada corrida) y ahí está casi todo el ahorro.
    GEMINI_CACHE_TTL_HORAS: int = 24
    # Mínimo de llamados para CREAR una caché nueva. None = se deriva del TTL, que es lo
    # correcto: el almacenamiento cuesta US$0,50/M-tokens-hora y cada llamado ahorra
    # US$0,30/M, así que una caché se paga con 1,67 llamados por cada hora que vive
    # (con TTL de 24 h, 40 llamados). Medido sobre las auditorías del 2026-09-01, bajarlo
    # a 10 daba plata en contra en 4 de las 6 plantillas del día: la peor fue una de 10
    # llamados con 9.401 tokens de bloque fijo, que ahorró US$0,028 y pagó US$0,113 de
    # almacenamiento. Reusar una caché que ya está viva no tiene mínimo (ya se paga igual).
    GEMINI_CACHE_MIN_LLAMADOS: Optional[int] = None
    # Bloques más chicos que esto no se cachean: el modelo tiene su propio mínimo y el
    # ahorro no justifica el ida y vuelta.
    GEMINI_CACHE_MIN_TOKENS: int = 1024

    # AuditorIA - Cola de lotes de auditoría (ver AuditorIA/batch_cola.py).
    # La Batch API admite 100 jobs en estado no terminal en TODA la cuenta (auditorías
    # + transcripciones). Al tocar ese techo, `batches.create` rebota; antes el lote se
    # descartaba en silencio y sus audios no se auditaban nunca. Ahora espera turno.
    #
    # BATCH_CUPO_MAX: a partir de cuántos jobs en vuelo dejamos de mandar y encolamos.
    # 85 y no 100 para dejar aire: el conteo se cachea ~1 min y la cola de
    # transcripciones crea jobs por su cuenta contra el mismo cupo.
    BATCH_CUPO_MAX: int = 85
    # Llamados por lote. Con el lote yendo como archivo JSONL (tope 2 GB) el transporte
    # ya no manda; el límite es cuántos jobs podemos tener en vuelo. 250 deja 2000
    # audios en 8 jobs sin que un job fallido se lleve puesta una corrida entera.
    BATCH_MAX_LLAMADOS_POR_LOTE: int = 250
    # Red de contención por peso del archivo (el audio va en base64, que infla ~33%).
    BATCH_MAX_BYTES_POR_LOTE: int = 400 * 1024 * 1024
    # Lotes que el tick del scheduler manda por pasada, además del tope que imponga el
    # cupo libre. Evita que un pico vacíe la cola de golpe y tape a las transcripciones.
    BATCH_COLA_MAX_POR_TICK: int = 20
    # Dónde esperan los lotes encolados (el JSONL con los audios ya comprimidos).
    # Igual que AUDIO_STORE_DIR: `storage/` está gitignored y es local a cada servidor.
    BATCH_PENDIENTES_DIR: str = os.path.join(_BACKEND_ROOT, 'storage', 'batch_pendientes')

    ASTERVOIP_USER: str
    ASTERVOIP_PASS: str

    # Genesys Cloud de Benefix (AuditorIA/downloads/Genesys.py y scripts/benefix_genesys.py).
    # Es un usuario de la consola web, no un cliente OAuth: la cuenta no tiene permiso
    # para crear uno. Con default vacío para que una máquina sin el secreto arranque igual
    # (solo falla la auditoría/carga de Benefix, con un error que lo dice).
    GENESYS_USER: str = ""
    GENESYS_PASS: str = ""
    # Región de la org (edenredargentina vive en sa-east-1).
    GENESYS_REGION: str = "sae1.pure.cloud"
    # Descargas de audio en paralelo. La API admite 300 pedidos/min por token y cada
    # audio son 2-3 pedidos (transcodificación + bajada del archivo firmado).
    GENESYS_DOWNLOAD_WORKERS: int = 4

    # AuditorIA - Audio conservado.
    # Tras auditar, el audio ya comprimido a Opus (~90 KB/min) se conserva en disco
    # para poder reproducirlo/descargarlo desde "Auditorías Realizadas" y seguir la
    # transcripción sincronizada. Ver AuditorIA/audio_store.py. `storage/` está
    # gitignored, así que el default queda fuera del repo por defecto.
    AUDIO_STORE_DIR: str = os.path.join(_BACKEND_ROOT, 'storage', 'audios_auditoria')
    # Tope de espacio en disco por entorno (dev/prod tienen discos separados). Al
    # superarlo se borran los audios más antiguos (FIFO). Default 100 GB.
    AUDIO_STORE_MAX_BYTES: int = 100 * 1024 ** 3
    # Bitrate Opus de la compresión de audio (subida a Gemini + audio conservado).
    # 12k era el mínimo de "voz inteligible"; 32k da voz nítida tanto para la IA (mejor
    # transcripción/auditoría) como para la escucha humana. Es un TECHO, no un piso:
    # VBR está prendido, así que lo medido sobre el store real a 32k son ~156 KB/min
    # (~21 kbps promedio) = ~11.200 h en 100 GB, bastante más de lo que se había estimado.
    # OJO: Gemini factura el audio por SEGUNDO, no por byte, así que subir el bitrate
    # no encarece la auditoría — solo pesa más en disco y tarda un poco más en subir.
    # Si se quiere conservar la misma ventana de horas, subir AUDIO_STORE_MAX_BYTES en
    # proporción. Bajar a "24k" junto con `-application voip` achica ~23% más (medido),
    # pero es un cambio audible: escuchar un par de llamados antes de dejarlo fijo.
    AUDIO_OPUS_BITRATE: str = "32k"

    # AuditorIA - Gate de audio mudo (ver AuditorIA/audio_calidad.py).
    # Un audio sin voz llega igual a Gemini, que lo completa con la metadata del
    # llamado que viaja en el prompt y devuelve una auditoría entera inventada. Medir
    # el audio antes de subirlo corta eso de raíz y ahorra los tokens.
    AUDIO_GATE_MUDO: bool = True
    # Qué se considera silencio y cuánto tiene que durar para contarlo (silencedetect).
    AUDIO_SILENCIO_NOISE_DB: float = -40.0
    AUDIO_SILENCIO_DUR_MIN: float = 1.0
    # Umbrales para declarar MUDO (no se audita). Conservadores a propósito: un falso
    # positivo deja sin auditar una llamada legítima. Alcanza con cumplir uno de los dos.
    AUDIO_MUDO_RATIO_SILENCIO: float = 0.98
    AUDIO_MUDO_MEAN_DB: float = -50.0
    # Casi todo silencio pero con algo de voz: se audita y queda marcado para revisar.
    AUDIO_SOSPECHOSO_RATIO_SILENCIO: float = 0.90

    # AuditorIA - Tope de los atributos de texto libre (ver AuditorIA/limites_texto.py).
    # Un atributo `string`/`array_string` no tenía límite, y varias campañas terminaron
    # pidiendo la transcripción del llamado adentro de un atributo: se paga como tokens
    # de SALIDA en cada auditoría, queda como texto plano en una celda y encima arriesga
    # que el JSON se corte a mitad y se pierda la auditoría entera. El tope viaja al
    # `max_length` del response_schema, se le explica al modelo en el prompt y se aplica
    # de nuevo al guardar (red de contención para las plantillas ya escritas así).
    # 1500 caracteres = un feedback largo o una cita textual; muy lejos de un llamado
    # transcripto (~4.000-8.000 caracteres cada 5 minutos de audio).
    # 0 = sin tope: apaga instrucción, max_length y recorte.
    ATRIBUTO_TEXTO_MAX_CARACTERES: int = 1500
    # Rechazar en el alta/edición los atributos de texto libre redactados como pedido de
    # transcripción ("transcribí la conversación completa"). El error le indica al
    # usuario la vía correcta (tilde "Transcripción" al auditar, o la cola de
    # "Auditorías Realizadas"). False = solo se avisa por log al guardar el resultado.
    ATRIBUTO_BLOQUEAR_TRANSCRIPCION: bool = True

    # AuditorIA - Señales de calidad de las plantillas (ver AuditorIA/senales_prompt.py y
    # asistente_plantillas.senales_de_atributo). Los problemas de una plantilla se
    # detectan sin IA y sin tokens: fechas fijas en el prompt, placeholders sin
    # reemplazar, criterios que no se pueden evaluar, un enum sin salida segura.
    # False = no se calculan las señales de redacción (las estructurales siguen).
    PLANTILLA_SENALES_TEXTO: bool = True
    # Que las señales de severidad ALTA frenen el guardado del atributo. Solo frenan lo
    # que ESE guardado introduce: una plantilla vieja con problemas se puede seguir
    # editando y reordenando. El editor ofrece "guardar igual", que queda logueado.
    # False = las señales se siguen mostrando (semáforo, chequeo de salud, reporte
    # semanal) pero no frenan a nadie.
    PLANTILLA_BLOQUEAR_SENALES_ALTAS: bool = True
    # Destinatarios del reporte SEMANAL de salud de las plantillas (separados por ';',
    # ver app/salud_plantillas.py). ADMIN_EMAIL se agrega siempre. Van en config y no en
    # una tabla porque es una lista corta y estable, y tenerla versionada deja asentado a
    # quién se le está avisando. Vacío = solo ADMIN_EMAIL.
    PLANTILLAS_SALUD_DESTINATARIOS: str = ""
    # Mandar el mail aunque no haya nada para revisar. True a propósito: la semana en
    # verde es la que hace que el reporte se lea, y es la que deja registro de que el
    # aviso efectivamente llega.
    PLANTILLAS_SALUD_ENVIAR_SIN_HALLAZGOS: bool = True

    # AuditorIA - Ventana de la evidencia con la que se revisa una plantilla
    # (ver AuditorIA/evidencia_plantilla.py). Cuántos días de auditorías se agregan para
    # saber qué respondió realmente cada atributo: qué criterio no discrimina, qué opción
    # no se elige nunca, qué texto se recorta. Son consultas agregadas contra la base
    # PRODUCTIVA que dispara alguien que está esperando en pantalla, así que la ventana
    # es acotada a propósito: 90 días alcanzan para que las proporciones sean estables y
    # dejan afuera el histórico completo, que sí sería caro de escanear.
    REVISION_EVIDENCIA_DIAS: int = 90
    # Tope de auditorías que se agregan para esa evidencia. Es lo que vuelve predecible el
    # costo: AuditoriaDetalles tiene una fila por atributo y por auditoría, así que una
    # plantilla muy usada puede tener millones de filas en la ventana. Con las últimas
    # 2.000 auditorías las proporciones ya son estables (±2 puntos) y la consulta no
    # depende de cuánto haya auditado la plantilla en su vida.
    REVISION_EVIDENCIA_MAX_AUDITORIAS: int = 2000
    # Timeout duro de cada consulta de evidencia. Si la base está cargada es preferible
    # quedarse sin datos (la revisión sigue, sobre el texto) que colgar al usuario.
    REVISION_EVIDENCIA_TIMEOUT_SEG: int = 20
    # Dónde se registran los trabajos en curso de la revisión con IA (uno por archivo,
    # ver app/revision_jobs.py). Tiene que ser un directorio compartido por TODOS los
    # workers de la API: el POST lanza el trabajo en un worker y el polling del navegador
    # cae en cualquier otro. `storage/` está gitignored, así que queda fuera del repo.
    REVISION_JOBS_DIR: str = os.path.join(_BACKEND_ROOT, 'storage', 'revision_jobs')
    # Directorio para los trabajos en curso del asistente analítico del dashboard (app/asistente_jobs.py).
    ASISTENTE_JOBS_DIR: str = os.path.join(_BACKEND_ROOT, 'storage', 'asistente_jobs')

    # AuditorIA - Reauditoría de llamadas (ver AuditorIA/reauditoria.py).
    # Límite máximo de llamadas permitidas por corrida en modo Batch y en modo Sincrónico.
    REAUDITAR_MAX_LLAMADOS_BATCH: int = 1000
    REAUDITAR_MAX_LLAMADOS_SYNC: int = 10

    # AuditorIA - Cola de transcripciones a demanda (calidad.TranscripcionJobs).
    # Motor BASE de la cola. Con 'gemini_batch' (el default) el motor real se elige por
    # pedido: los chicos van por FLEX (sincrónico, mismo precio que batch, minutos en vez
    # de horas) y los grandes por batch — ver transcripcion_cola.elegir_motor. Ponerlo en
    # otro valor lo fuerza para todos los pedidos nuevos. Cuando el servidor tenga GPU
    # pasa a 'fastwhisper' (transcripción local, sin costo por token): la cola y la
    # pantalla no cambian, ver transcripcion_cola._despachar_fastwhisper.
    TRANSCRIPCION_MOTOR: str = "gemini_batch"
    # Hasta cuántos llamados puede tener un pedido para resolverse por Flex. Por encima
    # de esto va a batch: Flex es best-effort y cientos de llamados sincrónicos se comen
    # su capacidad a fuerza de 429 (y no hay nadie mirando la pantalla en un pedido así).
    TRANSCRIPCION_FLEX_MAX_PEDIDO: int = 10
    # Llamados flex en paralelo. Pocos a propósito: acá se busca latencia (los pedidos
    # son chicos por definición), no throughput, y cada request extra compite por la
    # misma capacidad best-effort.
    TRANSCRIPCION_FLEX_WORKERS: int = 3
    # Timeout del llamado flex, en MILISEGUNDOS (lo que espera el SDK). 15 min: la doc de
    # Flex avisa que el pedido puede quedar encolado del lado de Google y recomienda
    # "10 minutos o más"; con el default del SDK se perdería una respuesta ya facturada.
    TRANSCRIPCION_FLEX_TIMEOUT_MS: int = 900_000
    # Intentos por pedido flex y espera inicial del backoff (se triplica en cada intento)
    # cuando Gemini contesta 429/503 por falta de capacidad. Agotados los intentos, el
    # pedido se degrada a batch en vez de fallar.
    TRANSCRIPCION_FLEX_REINTENTOS: int = 3
    TRANSCRIPCION_FLEX_BACKOFF_SEG: int = 15
    # Modelo de Gemini para transcribir. Vacío = el default del catálogo
    # (AuditorIA/modelos_ia.py::MODELO_IA_DEFAULT), que es lo que conviene: el audio pesa
    # en tokens de ENTRADA y ahí el estándar es el más barato del catálogo.
    TRANSCRIPCION_MODELO: str = ""
    # Tope de pedidos que se mandan por tick. El corte real lo da el tamaño (un job de
    # batch no puede superar los 20 MB de request, así que se parte en lotes de 15 MB);
    # esto solo acota cuánto trabajo hace un tick.
    TRANSCRIPCION_MAX_POR_TICK: int = 200
    # Cuánto piensa el modelo antes de transcribir (LOW/MEDIUM/HIGH, ver
    # AuditorIA/razonamiento.py). LOW a propósito: transcribir es dictado, no análisis
    # —medido el 2026-08-19, LOW devuelve 0 tokens de pensamiento y la transcripción
    # sale completa igual, mientras que MEDIUM gasta ~1.100 por llamado sin mejorar nada.
    # Acá había un `thinking_budget=1024` que NO se respetaba: un solo job real de
    # 3.7-flash gastó 20.071 tokens de pensamiento (19,6x el supuesto tope), más caro que
    # la transcripción misma.
    TRANSCRIPCION_NIVEL_RAZONAMIENTO: str = "LOW"

    # ChatBot - LlamaIndex Settings.
    # OJO con cambiar el modelo de embeddings: los vectores ya indexados en Qdrant son
    # de ESTE modelo. Cambiarlo obliga a reindexar todos los bots (y la dimensión del
    # vector tiene que coincidir, si no la colección deja de aceptar consultas).
    DEFAULT_REMOTE_EMBED_MODEL: str = "gemini-embedding-2-preview"
    # Textos por request de embeddings. 100 es el TOPE de la API (con 250 contesta
    # 400: "at most 100 requests can be in one batch", medido el 2026-09-03) y es
    # justo lo que hay que aprovechar: un bot de 800 nodos son 8 requests en vez de
    # 80, que es de dónde salían los 429 RESOURCE_EXHAUSTED al reindexar. Antes acá
    # había un `Settings.embed_batch_size = 2` que no hacía NADA (Settings no tiene
    # ese atributo: se seteaba una propiedad suelta y el modelo seguía en su default
    # de 10) — ver app/rag_settings.py.
    EMBED_BATCH_SIZE: int = 100
    # Reintentos con backoff exponencial cuando los embeddings contestan 429/503.
    # El default de la librería (3 intentos, ~6 segundos en total) no alcanza para
    # una ventana de cuota por minuto: se le da paciencia de minutos porque del otro
    # lado hay un job de fondo, no una persona esperando. La espera arranca en
    # BACKOFF_MIN y se duplica hasta el tope de BACKOFF_MAX.
    EMBED_REINTENTOS: int = 8
    EMBED_BACKOFF_MIN_SEG: float = 2.0
    EMBED_BACKOFF_MAX_SEG: float = 120.0
    # Veces que un job de reindexado se vuelve a encolar solo cuando lo que falló fue
    # la CUOTA de Gemini (y no el material). No es un error del bot: agotar la cuota
    # es esperar, así que el job vuelve a 'pending' en vez de figurar como fallido.
    INDEX_REINTENTOS_SIN_CUPO: int = 5
    # LLM que redacta la respuesta del chatbot RAG. Flash-lite a propósito: el trabajo
    # duro lo hace la recuperación, el modelo solo redacta sobre el contexto que recibe,
    # y acá el volumen de consultas manda sobre la capacidad del modelo.
    DEFAULT_REMOTE_LLM_MODEL: str = "gemini-3.5-flash-lite"
    DEFAULT_LLM_TEMP_REMOTE: float = 0.4
    # OJO (2026-08-14): al mover esta config al repo se descubrió que dev y prod venían
    # corriendo reranker DISTINTO. El .env de SRV01 define
    # "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1" (multilingüe) y pisa este default,
    # mientras que TODA la calibración —umbrales de vacíos, mediciones del set dorado,
    # scripts/bench_reranker.py— está hecha sobre ms-marco (ver RERANKER_MODELO_CALIBRADO).
    # Antes de sacar esa línea del .env de prod hay que DECIDIR cuál de los dos queda:
    # borrarla a ciegas le cambia el reranker a producción de un deploy para el otro.
    DEFAULT_RERANKER_MODEL: str = "cross-encoder/ms-marco-MiniLM-L12-v2"
    # Cuántos chunks sobreviven al reranker y llegan al LLM. Medido sobre el índice
    # de PRODUCCIÓN de voltara (v8, 33 preguntas reales, 2026-08-05): la cobertura de
    # términos clave en el contexto sube 82,4% (n=3) -> 94,1% (n=8) -> 97,1% (n=12).
    # Estaba en 3 y era demasiado poco: los chunks miden 535 chars de media, así que
    # el LLM recibía ~400 tokens de contexto y había casos donde llegaban 3 chunks
    # del documento CORRECTO sin el dato pedido (ver scripts/eval_rag.py --detalle).
    # A partir de n=8 el rendimiento decae: +50% de tokens por +3 puntos.
    DEFAULT_RERANKER_TOP_N: int = 8


    # ChatBot - Node/Chunk Settings. Definen cómo se parte el markdown al indexar, así
    # que cambiarlos solo tiene efecto sobre los índices que se generen después.
    CUSTOM_CHUNK_SIZE: int = 512
    CUSTOM_CHUNK_OVERLAP: int = 100

    # ChatBot - Qdrant + storage por bot.
    # La definición de cada chatbot (docs de Drive, prompt, permiso) vive en la BD
    # (pagina_web.Chatbots); acá solo queda la infraestructura compartida.
    QDRANT_URL: str = "http://127.0.0.1:6333"
    QDRANT_API_KEY: Optional[str] = None
    # Raíz bajo la cual cada bot deriva sus rutas: {ROOT}/{slug}/{docs,logs,v{N}}
    CHATBOT_STORAGE_ROOT: str = "/var/www/chatcsva/storage/chatbots"
    # Cada worker relee la tabla de chatbots como máximo una vez por TTL; define
    # cuánto tarda un bot nuevo/reindexado en estar visible sin reiniciar.
    CHATBOT_REGISTRY_TTL_SECONDS: int = 60
    # Similitud coseno mínima para un hit del caché semántico (Chroma usaba
    # distancia < 0.2; en Qdrant se compara score de similitud >= este valor).
    CHATBOT_CACHE_MIN_SCORE: float = 0.95
    # Palabras mínimas para considerar una pregunta auto-contenida y por lo tanto
    # cacheable. Las consultas cortas ("¿cuánto tarda?") dependen del hilo y su
    # embedding colisiona con la misma pregunta hecha sobre otro tema.
    CHATBOT_CACHE_MIN_PALABRAS: int = 6
    # Candidatos que recupera cada retriever (vector y BM25) antes del reranker.
    # Recuperar de más y dejar que el cross-encoder filtre mejora el recall (que el
    # chunk correcto entre); el costo es CPU de rerank, ~lineal en esta cifra.
    CHATBOT_RETRIEVAL_TOP_K: int = 12
    # Cada worker de la API calienta la conexión TLS al API de embeddings al arrancar
    # y la mantiene viva con un ping cada N segundos, para que el primer request real
    # no pague el handshake frío (~2.5s, el mayor componente del TTFT). 0 lo desactiva.
    CHATBOT_EMBED_KEEPALIVE_SECONDS: int = 90
    # Ventana de sesión de conversación (minutos). Un hueco temporal mayor a este valor
    # entre dos consultas del mismo operador y bot se interpreta como una NUEVA gestión
    # (nuevo llamado): la memoria de condensación arranca limpia y NO arrastra el contexto
    # de la gestión anterior. Así el operador no tiene que borrar el chat a mano en cada
    # llamado. Los datos de uso (últimos 30 días) muestran que los seguimientos reales se
    # concentran en <5 min y que ~44% de los huecos superan los 30 min (llamados distintos).
    # 0 lo desactiva (comportamiento anterior: todo el historial reciente es una sola sesión).
    CHATBOT_SESSION_GAP_MINUTES: int = 10

    # --- Adjuntos del chatbot (capturas de pantalla y PDF) ---
    # Quién puede adjuntar y a qué bot NO se configura acá sino en la BD: el permiso
    # `chatbot.adjuntos` (por rol) y `Chatbots.permite_adjuntos` (por bot). Los dos
    # se cambian con un UPDATE y toman efecto en <= 60s (TTL del registry), sin
    # reiniciar la API — que es lo que hace falta para poder cortar el feature en
    # producción sin un deploy.
    # Topes por consulta. Están calibrados para capturas de pantalla y documentos
    # cortos del cliente; todo esto entra inline en el request a Gemini (el límite
    # duro de la API es 20 MB por request).
    CHATBOT_ADJUNTOS_MAX_ARCHIVOS: int = 3
    CHATBOT_ADJUNTOS_MAX_MB: float = 8
    CHATBOT_ADJUNTOS_MAX_MB_TOTAL: float = 15
    CHATBOT_ADJUNTOS_MAX_PAGINAS_PDF: int = 20
    # Lado mayor al que se reescala una imagen antes de mandarla. Más resolución no
    # mejora la lectura del modelo y sí multiplica los mosaicos de 768px que se
    # pagan. 0 desactiva el reescalado (se manda tal cual entró).
    CHATBOT_ADJUNTOS_MAX_LADO_PX: int = 1568

    # Extracción del TEXTO del adjunto (fase 2). El modelo ya mira el archivo al
    # generar, pero Qdrant y BM25 solo indexan texto: sin esto la búsqueda se hace
    # únicamente con lo que escribió el operador, y "¿por qué paga tanto?" no
    # recupera nada útil aunque la factura adjunta diga "cargo fijo" e "impuestos".
    # El texto extraído NO se le manda de vuelta al modelo (ya tiene el archivo):
    # se usa para RECUPERAR y para que el turno siguiente recuerde de qué se hablaba.
    CHATBOT_ADJUNTOS_EXTRAER_TEXTO: bool = True
    # Modelo de la transcripción cuando no hay capa de texto (imagen o PDF escaneado).
    # Tarea mecánica de lectura: no hace falta el modelo del chat.
    CHATBOT_ADJUNTOS_MODELO_TEXTO: str = "gemini-3.5-flash-lite"
    # Un PDF con menos de esto por página se considera escaneado y se manda a
    # transcribir. Un PDF de texto real da cientos de caracteres por página; los
    # escaneados dan 0 o basura de pocos caracteres.
    CHATBOT_ADJUNTOS_MIN_CHARS_CAPA: int = 40
    # Cuánto texto de cada adjunto entra a la consulta de recuperación. Sobra: la
    # búsqueda necesita términos, no el documento entero, y un texto largo diluye
    # la pregunta del operador entre miles de palabras de la factura.
    CHATBOT_ADJUNTOS_TEXTO_MAX_CHARS: int = 1200
    # Cuánto queda guardado en `query` (y por lo tanto en la memoria del turno
    # siguiente). Corto a propósito: el buffer de memoria son 3000 tokens y lo
    # tiene que compartir con el resto de la conversación.
    CHATBOT_ADJUNTOS_TEXTO_HISTORIAL_CHARS: int = 500

    # --- Vacíos de conocimiento (qué no pudo responder el bot) ---
    # Modelo del clasificador diferido: es una tarea de etiquetado corta, no hace
    # falta el modelo del chat. Corre fuera del camino de la consulta.
    CHATBOT_VACIOS_MODELO: str = "gemini-3.5-flash-lite"
    # El JUEZ ("¿este texto contiene la respuesta?") va aparte y con más nafta que el
    # clasificador. No es etiquetado: es la decisión de a quién se le manda el trabajo,
    # y equivocarla archiva un hueco real como problema técnico, que es el error que
    # nadie arregla después. Medido el 2026-09-04 sobre 7 casos con el contexto real de
    # producción (4 donde el dato NO estaba + 3 respondidos bien), contando aciertos:
    #
    #     flash-lite + MINIMAL + prompt viejo      3/7   (4 falsos "sí resuelve")
    #     flash-lite + MINIMAL + prompt reforzado  4/7
    #     flash-lite + MEDIUM  + prompt viejo      4/7
    #     flash-lite + MEDIUM  + prompt reforzado  6/7
    #     flash 3.8  + MEDIUM  + prompt reforzado  7/7
    #
    # Los dos ejes suman y ninguno introdujo el error contrario (ningún falso "no
    # resuelve"). El volumen es de decenas de llamadas por día, así que el modelo caro
    # acá no mueve el presupuesto.
    CHATBOT_VACIOS_MODELO_JUEZ: str = "gemini-3.8-flash"
    # Consultas que clasifica por tick del scheduler. Acota el gasto si un día se
    # dispara el volumen; el resto espera al tick siguiente.
    CHATBOT_VACIOS_LOTE: int = 50
    # Score del reranker por debajo del cual se considera que NADA de lo recuperado
    # responde la pregunta, aunque el bot haya improvisado una respuesta.
    # Modelo con el que se calibraron los umbrales de score de abajo. Si
    # DEFAULT_RERANKER_MODEL no coincide, rag_settings avisa por log: cada modelo
    # tiene su escala (MS MARCO da logits -11..+10; BAAI/bge da 0..1) y un umbral
    # fijo heredado de otro modelo rompe la detección de vacíos sin dar error.
    # Recalibrar con scripts/bench_reranker.py.
    RERANKER_MODELO_CALIBRADO: str = "cross-encoder/ms-marco-MiniLM-L12-v2"

    CHATBOT_VACIO_SCORE_MIN: float = -5.0
    # Sondeo "¿esto existe en la documentación?": se recupera de más a propósito,
    # porque la pregunta es si el contenido está, no si el top_k de producción lo
    # encuentra. Esa diferencia es la que separa hueco de fallo de recuperación.
    CHATBOT_VACIO_SONDEO_TOP_K: int = 50
    # Cuántos fragmentos del sondeo lee el LLM para decidir si el corpus responde.
    # Ya no se filtra por score antes de leerlos (el reranker se hunde con la
    # paráfrasis), así que este número es el que acota lo que se manda a leer.
    CHATBOT_VACIO_FRAGMENTOS_A_LEER: int = 5
    # Score mínimo para dar por hecho que un chunk SÍ responde el tema. Medido sobre
    # pares reales: lo que de verdad responde puntúa +4/+5 y lo irrelevante ~-11,
    # pero entre 0 y 3 caen los chunks "del mismo tema que no traen el dato pedido".
    # Con 0.0 se colaban como "ya documentado" cosas que eran huecos reales (ej.
    # "cambio de fase", sondeo 1.12), y eso es el peor error posible: un hueco
    # archivado como problema técnico no lo arregla nadie. Ante la duda, que vaya a
    # Calidad: como mucho revisan un documento y dicen "ya está".
    CHATBOT_VACIO_SCORE_EXISTE: float = 3.0
    # Largo a partir del cual una respuesta que arranca con "no encontré" se
    # considera que igual contestó (el modelo usa la frase como muletilla y sigue).
    # Una negación pura ronda 100-200 caracteres: frase + recordatorio de verificar.
    CHATBOT_VACIO_LARGO_RESPUESTA: int = 400
    # Similitud coseno para considerar que dos temas son el mismo ("poda de arbol"
    # vs "poda de arboles"). Más bajo agrupa de más y mezcla temas distintos.
    CHATBOT_VACIO_AGRUPAR_MIN_SCORE: float = 0.88

    # --- Desambiguación (ofrecer temas en vez de "no encontré") ---
    # Ver app/chatbot_desambiguacion.py. Bots que, cuando la consulta no alcanza
    # para elegir un tema, responden con la lista de temas parecidos que SÍ están
    # documentados en vez de con la frase de "no encontré información".
    # Es por slug y sin migración: sacar el slug de esta lista apaga el feature.
    # `voltara_digital` queda afuera a propósito — el operador de canal digital no
    # tiene al cliente en línea para repreguntar, así que una lista de opciones le
    # cuesta un ida y vuelta que el telefónico resuelve en el momento.
    #
    # Están TODOS los bots telefónicos (2026-09-02). Arrancó solo con voltara para
    # ver si el gate se pasaba de generoso y tapaba vacíos reales; sobre ~3.000
    # consultas no lo hizo, y el patrón que el feature ataca —la consulta de dos
    # palabras que el operador escribe con el cliente en línea— es el mismo en el
    # resto: "nuevo usuario" y "presentacion" en benefix, "LISTA POSITIVA" y
    # "VIAJEROS BANCENTRO" en csv_isla_de_productos, "horario base ducasse" en vantix,
    # todas respondidas con "no encontré" teniendo el tema documentado.
    # Los csv_* de poco tráfico entran igual: el gate exige DOS temas parejos
    # dentro de una banda de score, así que sobre un corpus chico simplemente no
    # dispara. `paygo` no está porque no tiene índice.
    CHATBOT_DESAMBIGUACION_SLUGS: List[str] = [
        "voltara",
        "benefix",
        "vantix",
        "csv_bancentro",
        "csv_commercial",
        "csv_denuncias",
        "csv_isla_de_productos",
        "csv_no_premium",
        "csv_premium",
        "csv_pto_a_pto",
        "csv_tokenizacion",
        "csv_vip",
    ]
    # Piso de score para ofrecer opciones. Está DELIBERADAMENTE por encima de
    # CHATBOT_VACIO_SCORE_MIN (-5.0): entre -5 y 0 lo recuperado no tiene nada que
    # ver, y esa consulta tiene que seguir terminando en "no encontré" para que la
    # detección de vacíos la registre. De 0 a CHATBOT_VACIO_SCORE_EXISTE (3.0) caen
    # los chunks "del mismo tema que no traen el dato pedido", que es exactamente
    # el material con el que se arma una lista de opciones útil.
    # Escala del reranker: ver RERANKER_MODELO_CALIBRADO.
    CHATBOT_DESAMB_SCORE_MIN: float = 0.0
    # Cuán parejos tienen que estar los dos mejores temas, como fracción del ancho
    # de la banda (3.0 - 0.0 = 3 puntos => 1.5 con el default). Si el primero saca
    # más ventaja que eso, no hay ambigüedad que resolver: la pregunta se entendió
    # y lo que falta es el dato. Subirlo ofrece opciones más seguido (y tapa más
    # vacíos); bajarlo lo vuelve más conservador.
    CHATBOT_DESAMB_EMPATE_FRACCION: float = 0.5
    # Cuántas opciones se muestran. Más de 4 en pantalla, con el cliente en línea,
    # es peor que ninguna.
    CHATBOT_DESAMB_MAX_OPCIONES: int = 4
    # Hasta acá una respuesta que arranca con "no encontré" se considera la negación
    # SOLA, y entonces se puede cambiar por la lista de temas (proponer_tras_negativa).
    # Más larga que esto, la respuesta trae contenido —la muletilla "No encontré...
    # No obstante, la documentación menciona..."— y se muestra tal cual.
    # Medido sobre los "no encontré" de agosto con score >= 3,0 (los que este camino
    # puede reemplazar): 93 de 114 miden exactamente 78 caracteres (la frase canónica),
    # 111 quedan por debajo de 280 y los 3 que se pasan rondan los 400, que es
    # justamente donde CHATBOT_VACIO_LARGO_RESPUESTA ya decide "esto contestó igual".
    # Este número es más estricto a propósito: acá el error se paga borrando una
    # respuesta que existía, allá solo clasificando de más.
    CHATBOT_DESAMB_NEGATIVA_MAX_CHARS: int = 280

    # --- Tablas de datos de los chatbots (app/chatbot_tablas.py) ---------------
    # Conocimiento que no es prosa sino entidad -> atributos (las bases de vantix,
    # la cartera de cobranzas de benefix). Se consulta ANTES del retrieval y no
    # pasa por Qdrant. Ver el módulo para el porqué; acá solo los umbrales.
    #
    # Kill switch: en False el bot ignora las tablas y responde como siempre.
    CHATBOT_TABLAS_ACTIVO: bool = True
    # Cada cuánto se revalida la caché de tablas de un worker. Es corto a
    # propósito: editar un teléfono de la tabla tiene que verse enseguida, y la
    # revalidación es una query de una fila (COUNT + MAX(updated_at)), no la
    # recarga de las filas.
    CHATBOT_TABLAS_TTL_SECONDS: int = 60
    # Techo para que una tabla entre ENTERA en el prompt (modo 'auto'). Medido en
    # caracteres de los datos, no del markdown renderizado. 24k caracteres son
    # ~6k tokens: las ~40 bases de vantix entran cómodas (y hoy esas consultas ya
    # cuestan 2.451 tokens promedio por el camino RAG, así que no es más caro);
    # las ~2.500 filas de la cartera de benefix quedan afuera y van por lookup.
    CHATBOT_TABLA_MAX_CHARS_COMPLETA: int = 24000
    # Cuántas filas como máximo se le pasan al modelo en modo lookup. Si una
    # consulta identifica más que esto, casi seguro identificó mal.
    CHATBOT_TABLA_MAX_FILAS: int = 12
    # Puntaje mínimo (en unidades de IDF acumulada) para dar por identificada una
    # fila. Por debajo, la coincidencia es de palabras comunes y la consulta NO
    # rutea a la tabla: mejor que conteste el RAG a que el bot afirme un dato de
    # la fila equivocada.
    CHATBOT_TABLA_SCORE_MIN: float = 1.5
    # Empates: se conservan las filas que llegan a esta fracción del mejor
    # puntaje. Es lo que hace que las dos Solange de la cartera de benefix
    # lleguen las dos al modelo en lugar de que elija una.
    CHATBOT_TABLA_EMPATE_FRACCION: float = 0.6
    # Peso del match por frase completa ("...razon social ALFA - RED SA" contiene
    # el valor entero de la clave). Es evidencia mucho más fuerte que los tokens
    # sueltos, que podrían venir de filas distintas.
    CHATBOT_TABLA_BONUS_FRASE: float = 4.0
    # Largo mínimo de un valor de clave para buscarlo por contención. Sin esto,
    # una SubCta de un dígito ("7") matchearía dentro de cualquier consulta.
    CHATBOT_TABLA_MIN_CHARS_FRASE: int = 5
    # Similitud coseno mínima entre la consulta y la descripción de la tabla para
    # rutear por esa señal sola (la más blanda de las tres). Alta a propósito:
    # equivocarse acá manda al camino de tabla una consulta de procedimiento.
    CHATBOT_TABLA_SIMILITUD_MIN: float = 0.72
    # Cuántos valores distintos puede tener una columna para entrar en el resumen
    # de una tabla grande ("¿qué gestores de cobranza hay?"). Por encima de esto
    # la columna es un identificador, no una categoría, y listarla no ayuda.
    CHATBOT_TABLA_RESUMEN_MAX_VALORES: int = 30
    # Cuántas filas (o cuántas repeticiones de una etiqueta "Campo: valor") tiene
    # que haber para que valga la pena preguntarle a la IA si un documento es una
    # tabla de datos. Es un filtro determinístico y barato: sin él, cada formateo
    # pagaría una llamada extra por documento. Ver chatbot_tablas_admin.parece_tabla.
    CHATBOT_TABLA_MIN_FILAS_DETECCION: int = 8

    # Google Sheet IDs. El ID de una hoja no da acceso a nada por sí solo: el acceso lo
    # otorga la cuenta de servicio (CREDENTIALS_GOOGLE_SHEET_JSON, ese sí secreto y fuera
    # del repo). Son las mismas hojas para todas las máquinas.
    SHEET_ID_INFORMACION: str = "ID_DE_GOOGLE_A_CONFIGURAR"
    SHEET_ID_INTERNOS: str = "ID_DE_GOOGLE_A_CONFIGURAR"
    SHEET_ID_DENTAL_CRONOGRAMA_DE_ATENCION: str = "ID_DE_GOOGLE_A_CONFIGURAR"
    SHEET_ID_INGRESOS: str = "ID_DE_GOOGLE_A_CONFIGURAR"
    SHEET_ID_CANDIDATOS: str = "ID_DE_GOOGLE_A_CONFIGURAR"

    # Other Paths & URLs.
    # Nombre del archivo JSON de la cuenta de servicio de Google. El NOMBRE no es
    # secreto; el archivo sí, y está en .gitignore. Se puede apuntar a otra ruta con
    # la env GOOGLE_SERVICE_ACCOUNT_FILE (ver backend/descargar_drive.py).
    CREDENTIALS_GOOGLE_SHEET_JSON: str = "CREDENTIALS_GOOGLE_SHEET_JSON.json"
    CHROME_BINARY_LOCATION: str = "/usr/bin/google-chrome"
    # URLs de los portales que raspan los conectores de descarga. Son públicas (o
    # internas de la red), no credenciales: usuario y contraseña de cada una siguen
    # en el .env.
    MITROL_URL: str = "https://apps.acme-solutions.example/reportes/login.aspx"
    YOIZEN_LOGIN_URL: str = "http://10.0.2.70/Reports/Login.aspx"
    VERINT_LOGIN_URL: str = "https://10.0.2.99/"
    CXONE_LOGIN_URL: str = "https://na1.nice-incontact.com"
    CXONE_OUTLOOK_URL: str = "https://outlook.office365.com/mail/"

    # Correo. Las direcciones no son secretas; las contraseñas de aplicación sí y van
    # en el .env.
    RRHH_MAIL: str = "empleos@acme-solutions.example"
    RRHH_MAIL_PASS: str
    RRHH_PLANTILLA_ID_DEFAULT: int = 14 # ID de plantilla por defecto para tareas automáticas

    GMAIL_IMAP_SERVER: str = "imap.gmail.com" # Default para Gmail
    GMAIL_SMTP_SERVER: str = "smtp.gmail.com" # Default para Gmail

    ENVIOS_OPERACIONES_MAIL: str = "envios_operaciones@acme-solutions.example"
    ENVIOS_OPERACIONES_PASS: str

    CALIDAD1_MAIL: str = "calidad1@acme-solutions.example"
    CALIDAD1_PASS: str

    # Destinatario de los mails administrativos (detalle de ejecuciones, alertas).
    ADMIN_EMAIL: str = "admin@acme.example"

    class Config:
        # Anclado a este archivo (backend/app/ -> raíz del repo), no al CWD:
        # scripts como reindex_all.py deben poder correrse desde cualquier lado.
        env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '.env')
        env_file_encoding = 'utf-8'
        extra = 'ignore'

settings = Settings() # type: ignore