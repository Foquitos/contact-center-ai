# Configuración

Qué valores configura el sistema, dónde vive cada uno y cómo saber cuál está activo en una máquina.
Acá figuran **solo nombres de variables, nunca valores secretos**. Cómo se reinicia un servicio después
de cambiar algo está en OPERACION.md.

## Dónde vive cada valor

Hay cuatro lugares:

| Lugar | Qué guarda | ¿Viaja con el deploy (git)? | Cómo se cambia |
|---|---|---|---|
| `.env` en la raíz del repo | **Secretos** (claves, contraseñas, cadenas de conexión) y lo propio de la máquina (`ENVIRONMENT`) | **No** (está en `.gitignore`) | Editando el archivo a mano en cada servidor |
| `backend/app/config.py` | Todo lo demás del backend: modelos de IA, umbrales, URLs de portales, IDs de planillas, casillas de correo, rutas | Sí | Con un commit y un deploy |
| Unidades systemd de SRV01 (`/etc/systemd/system/*.service`) | Variables del frontend en producción (`FASTAPI_URL`, `FLASK_SECRET_KEY`) y el `PATH`/`PYTHONPATH` de cada servicio | No | `sudo systemctl edit --full <servicio>` y reinicio |
| Tablas de la base | Configuración que se tiene que poder cambiar sin deploy: chatbots, plantillas, cupos, presupuesto de IA, parámetros del planificador, permisos | — (la base es compartida por dev y prod) | Desde las pantallas de administración o con una migración |

La regla: **si no es secreto ni propio de la máquina, va en `config.py`**. Un cambio en `config.py` llega a
dev y a prod con el mismo commit; un cambio en el `.env` hay que repetirlo a mano en cada servidor.

### Precedencia: el `.env` pisa a `config.py` sin avisar

`Settings` (Pydantic Settings) toma primero la variable del entorno o del `.env` y recién si no está usa el
default de `config.py`. Los nombres **no distinguen mayúsculas**: `CONNECTION_STRING` en el `.env` llena el
campo `connection_string`.

Una línea olvidada en el `.env` silencia cualquier cambio posterior del código y no da ningún error. Ya pasó
con `GEMINI_PLANTILLAS_MODEL`, que quedó fijo en un modelo viejo después de actualizarlo en `config.py`.

**Para saber qué valor está activo en una máquina** (desde la raíz del repo):

```bash
# SRV00 (dev)
cd backend && ../.venv/bin/python -c "from app.config import settings; print(settings.GEMINI_PLANTILLAS_MODEL)"
# SRV01 (prod)
cd ~/contact-center-ai/backend && ../venv/bin/python -c "from app.config import settings; print(settings.GEMINI_PLANTILLAS_MODEL)"
```

**Para ver qué variables define un `.env` sin mostrar los valores:**

```bash
grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' .env | tr -d = | sort
```

Al 23/09/2026 los `.env` de SRV00 y SRV01 tienen las **mismas 35 variables** y los mismos valores, salvo
`ENVIRONMENT`. Ninguno pisa un default de `config.py`.

### Armar un `.env` nuevo

```bash
cp .env.example .env
chmod 600 .env
# completar los valores (los entrega quien administra los secretos, nunca por mail ni chat)
```

`.env.example` está versionado y lista todas las variables que hay que definir. Los secretos no se copian a
docs, tickets ni mails.

---

## 1. Variables del `.env`

Las marcadas ✅ son **obligatorias**: sin ellas `Settings` no carga y ni el backend ni el scheduler arrancan.

### Entorno

| Variable | Oblig. | Descripción |
|---|:---:|---|
| `ENVIRONMENT` | — | `prod` (default) o `dev`. Dev y prod comparten la base, así que cada proceso solo toma el trabajo marcado con su entorno, y los jobs "solo prod" del scheduler no se registran en dev. Ver [BASE_DE_DATOS.md → Entorno](BASE_DE_DATOS.md#la-columna-entorno). **Un dev sin esta línea se comporta como prod** |

### Base de datos

| Variable | Oblig. | Descripción |
|---|:---:|---|
| `CONNECTION_STRING` | ✅ | Cadena SQLAlchemy de la base `Acme` (`mssql+pyodbc://…?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes`). La usan la API, el scheduler y los scripts |
| `CONNECTION_STRING_chatbot` | ✅ | Misma base, otro usuario (con permisos acotados al esquema `chatbot`). La usaba el chatbot SQL de gerencia, **hoy discontinuado**; sigue siendo obligatoria porque `config.py` la declara |
| `mssql_server`, `mssql_read_user`, `mssql_read_password` | — | Solo para `mssql_mcp.py`, el conector de solo lectura que usan los asistentes de IA de desarrollo. La aplicación no lo usa. La base (`Acme`) y el puerto (`1433`) tienen default en ese archivo |

### Seguridad

| Variable | Oblig. | Descripción |
|---|:---:|---|
| `SECRET_KEY` | ✅ | Firma de los tokens JWT del backend. **Rotarla invalida todas las sesiones abiertas**. Para generar una: `python3 -c "import secrets;print(secrets.token_urlsafe(64))"` |

La clave de sesión del frontend (`FLASK_SECRET_KEY`) no va en el `.env` en producción: ver
[Variables de las unidades systemd](#3-variables-de-las-unidades-systemd).

### Claves de IA y servicios

| Variable | Oblig. | Descripción |
|---|:---:|---|
| `GEMINI_AUDITORIA_API_KEY` | ✅ | Gemini para auditorías, transcripciones, asistentes de plantillas, docs, manual y dashboard |
| `GEMINI_CHATBOT_API_KEY` | ✅ | Gemini para los chatbots (respuestas y embeddings) |
| `QDRANT_API_KEY` | — | Vacía: el Qdrant local no tiene autenticación |
| `DEEPGRAM_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTESERVICE_API_KEY` | ✅ | **Legado sin uso**: `config.py` las declara obligatorias pero ningún código las usa. Hoy alcanza con cualquier valor no vacío. Ver INTEGRACIONES.md |

### Credenciales de plataformas de contact center

Las usan los conectores de descarga de audio (`backend/AuditorIA/downloads/`). Las URLs no son secretas:
algunas están en `config.py` y otras escritas en el propio conector. Qué hace cada plataforma y cómo falla: INTEGRACIONES.md.

| Variables | Plataforma | Oblig. |
|---|---|:---:|
| `VERINT_USER`, `VERINT_PASS` | Verint. **Sin uso real**: el conector ya no se instancia (Voltara audita por subida y CSV usa exports manuales de Verint) | ✅ |
| `CXONE_USER`, `CXONE_PASS`, `CXONE_TOPC` | NICE CXone (`CXONE_TOPC` es la semilla del segundo factor TOTP) | ✅ |
| `YOIZEN_USER`, `YOIZEN_PASS` | Yoizen | ✅ |
| `MITROL_USER`, `MITROL_PASS` | Mitrol | ✅ |
| `HERMES_USER`, `HERMES_PASS` | Hermes (Hidra Comercial) | ✅ |
| `CYT_USER`, `CYT_PASS` | Grabador CyT (Vantix/Orion) | ✅ |
| `ASTERVOIP_USER`, `ASTERVOIP_PASS` | AsterVoIP (Farmalux). El portal no pide login: el conector no las usa para autenticar | ✅ |
| `GENESYS_USER`, `GENESYS_PASS` | Genesys Cloud de Benefix (usuario de la consola web) | — (default vacío; sin ellas no se auditan ni se ingieren llamados de Benefix) |
| `USER_ENERVAL`, `PASS_ENERVAL` | Portal de informes de Enerval. **No pasan por `config.py`**: las lee solo `scripts/enerval_informe_ivr.py` | — (solo el cron del IVR) |

### Correo

Solo las contraseñas de aplicación; las casillas están en `config.py`.

| Variable | Oblig. | Casilla |
|---|:---:|---|
| `RRHH_MAIL_PASS` | ✅ | RRHH (`RRHH_MAIL`) |
| `ENVIOS_OPERACIONES_PASS` | ✅ | Envíos de resultados y alertas (`ENVIOS_OPERACIONES_MAIL`) |
| `CALIDAD1_PASS` | ✅ | Calidad (`CALIDAD1_MAIL`) |

### Power BI (opcional, no está en los servidores)

`powerbi_tenant_id`, `powerbi_client_id`, `powerbi_auth_mode`, `powerbi_client_secret`,
`powerbi_workspace_id` y `powerbi_allow_write` configuran `powerbi_mcp.py`, un conector para asistentes de IA
de desarrollo. La aplicación no lo usa y ningún servidor tiene estas variables. La explicación de cada una
está en `.env.example`.

### Overrides por máquina (hoy ninguno)

Solo si un servidor difiere del default de `config.py`: `DEFAULT_LOG_DIR`, `CHATBOT_STORAGE_ROOT`,
`CHROME_BINARY_LOCATION`, `CORS_ORIGINS`, `LOG_LEVEL`. Al 23/09 ningún servidor los define.

---

## 2. Variables que leen los scripts por fuera de `config.py`

| Variable | Quién la lee | Para qué |
|---|---|---|
| `USER_ENERVAL`, `PASS_ENERVAL`, `CONNECTION_STRING` | `scripts/enerval_informe_ivr.py` | Cargan el `.env` por su cuenta (`load_dotenv`) |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | `backend/descargar_drive.py` | Ruta alternativa al JSON de la cuenta de servicio de Google. Sin definirla se usa `backend/CREDENTIALS_GOOGLE_SHEET_JSON.json` |
| `GEMINI_CACHE_PLANTILLAS` | `scripts/recuperar_batches_fallidos.py` | El script la fuerza a `false` para su propia corrida |
| `SCHEDULER_UNIT` | `scripts/scheduler_logs.sh` | Nombre de la unidad systemd del scheduler; sin definirla se autodetecta |
| `JOURNAL_STREAM` | `backend/app/logging_config.py` | La pone systemd, no se configura. Si está, los logs salen con prioridad de syslog para `journalctl -p` |

---

## 3. Variables de las unidades systemd

En SRV01 cada servicio define variables en su unidad (`/etc/systemd/system/{backend,frontend,scheduler}.service`).
**No están en el `.env` ni en git**: si se rearma el servidor, hay que copiarlas a mano.

| Unidad | Variables | Nota |
|---|---|---|
| `frontend.service` | `FASTAPI_URL` | URL del backend que usa el `ApiClient`. Default en `frontend/config.py`: `http://localhost:8000` |
| `frontend.service` | `FLASK_SECRET_KEY` | **Secreto.** Firma la cookie de sesión de Flask. Rotarla corta las sesiones del front. Sin definirla, Flask usa una clave embebida en `frontend/config.py` que no sirve para producción (en SRV00, dev, no está definida) |
| las tres | `PATH`, `PYTHONPATH` | Apuntan al venv de prod (`~/contact-center-ai/venv`, **no** `.venv`) y al directorio del servicio |
| `scheduler.service` | `PYTHONUNBUFFERED` | Para que los logs lleguen al journal en el momento |

Para ver los nombres sin mostrar los valores:

```bash
grep -oE '^Environment="?[A-Za-z_]+' /etc/systemd/system/frontend.service
```

El frontend también lee `MAX_UPLOAD_MB` y `CHATBOT_MAX_UPLOAD_MB` del entorno (ver
[Topes de subida](#5-topes-de-subida-tres-capas)); hoy no están definidas en ningún lado y rigen los defaults.

---

## 4. Configuración versionada (`backend/app/config.py`)

Todo lo de esta sección se cambia **con un commit**. Sigue siendo sobreescribible por `.env`, pero eso
solo se justifica para una prueba puntual en dev.

### Entorno, seguridad y logs

| Variable | Default | Descripción |
|---|---|---|
| `algorithm` | `HS256` | Algoritmo de firma de los JWT |
| `access_token_expire_minutes` | `360` | Validez del token (6 h). Vencido, el front pide login de nuevo |
| `CORS_ORIGINS` | localhost y `10.0.0.12` (:7000 y :5000) | Orígenes permitidos, separados por coma. `10.0.0.12` es un servidor viejo que ya no se usa: se puede sacar. El front en producción se sirve por Nginx y habla con el backend desde el servidor, así que CORS casi no interviene |
| `DEFAULT_LOG_DIR` | `/var/www/chatcsva/storage/main_logs` | Logs de la aplicación. Ver OPERACION.md → Logs |
| `LOG_LEVEL` | `INFO` | Nivel de logging |
| `ADMIN_EMAIL` | casilla personal del desarrollador | Destinatario de las alertas operativas: log de cada tarea programada, carpetas CSV sin auditar, errores de corrida, reporte semanal de plantillas. **Pendiente: cambiarlo a una casilla de Sistemas** (ver OPERACION.md → Pendientes) |

### Modelos de IA

Qué modelo usa cada **plantilla de auditoría** no se configura acá: se elige por plantilla desde el catálogo
de `backend/AuditorIA/modelos_ia.py` (ver OPERACION.md → Salió un modelo nuevo de Gemini).

| Variable | Default | Descripción |
|---|---|---|
| `GEMINI_PLANTILLAS_MODEL` | `gemini-3.8-flash` | Asistente del editor de plantillas |
| `GEMINI_DOCS_MODEL` | `gemini-3.8-flash` | Asistente de documentación de los chatbots |
| `GEMINI_CARTAS_MODEL` | `gemini-3.5-flash-lite` | Asistente de cartas. Tarea mecánica: el lite cuesta ~5 veces menos y acepta nivel de razonamiento `MINIMAL` |
| `GEMINI_MANUAL_MODEL` | `gemini-3.8-flash` | Coral, la burbuja de ayuda (recibe el manual entero en cada pregunta) |
| `GEMINI_DASHBOARD_MODEL` | `gemini-3.8-flash` | Asistente analista del dashboard de auditorías |
| `GEMINI_DASHBOARD_THINKING_LEVEL` | `HIGH` | Nivel de razonamiento de ese asistente (`LOW`/`MEDIUM`/`HIGH`) |
| `PLANIFICADOR_CORTES_MODELO` | `gemini-3.8-flash` | Lectura de los avisos de corte de Hidra para el planificador (unas decenas por año) |
| `DEFAULT_REMOTE_LLM_MODEL` | `gemini-3.5-flash-lite` | Modelo que redacta las respuestas de los chatbots RAG |
| `DEFAULT_LLM_TEMP_REMOTE` | `0.4` | Temperatura por defecto de los chatbots (cada bot puede tener la suya en la base) |
| `DEFAULT_REMOTE_EMBED_MODEL` | `gemini-embedding-2-preview` | Embeddings de los chatbots. **Cambiarlo obliga a reindexar todos los bots**: los vectores guardados en Qdrant son de este modelo |

### Auditorías: lotes de Gemini, caché y reauditoría

| Variable | Default | Descripción |
|---|---|---|
| `BATCH_CUPO_MAX` | `85` | Lotes en vuelo en Gemini a partir de los cuales un lote nuevo se encola en `calidad.BatchPendientes` en vez de salir. El techo de la cuenta es 100, compartido con las transcripciones |
| `BATCH_MAX_LLAMADOS_POR_LOTE` | `250` | Llamados por lote de Gemini |
| `BATCH_MAX_BYTES_POR_LOTE` | 400 MB | Tope de peso del archivo del lote |
| `BATCH_COLA_MAX_POR_TICK` | `20` | Lotes que el scheduler despacha por pasada |
| `BATCH_PENDIENTES_DIR` | `backend/storage/batch_pendientes` | Dónde esperan los lotes encolados. **No borrar a mano** |
| `GEMINI_BATCH_UPLOAD_WORKERS` | `15` | Audios que se comprimen en paralelo al armar un lote (usa CPU) |
| `GEMINI_CACHE_PLANTILLAS` | `True` | Guarda el bloque fijo de la plantilla en la caché de contexto de Gemini en vez de mandarlo en cada llamado |
| `GEMINI_CACHE_TTL_HORAS` | `24` | Vida de la caché desde el último uso. Tiene que cubrir el plazo del batch (24 h) |
| `GEMINI_CACHE_MIN_LLAMADOS` | vacío | Mínimo de llamados para crear una caché nueva. Vacío = se deriva del TTL (40 con 24 h) |
| `GEMINI_CACHE_MIN_TOKENS` | `1024` | Bloques más chicos no se cachean |
| `REAUDITAR_MAX_LLAMADOS_BATCH` / `REAUDITAR_MAX_LLAMADOS_SYNC` | `1000` / `10` | Tope de llamados por reauditoría en lote y en modo sincrónico |

### Auditorías: audio

| Variable | Default | Descripción |
|---|---|---|
| `AUDIO_STORE_DIR` | `backend/storage/audios_auditoria` | Audios conservados después de auditar (para escucharlos y para el Golden Set) |
| `AUDIO_STORE_MAX_BYTES` | 100 GB | Tope por entorno. Al superarlo se borran los más viejos, salvo los fijados por un Golden Set |
| `AUDIO_OPUS_BITRATE` | `32k` | Calidad de la compresión Opus. Gemini cobra el audio por segundo, no por peso |
| `AUDIO_GATE_MUDO` | `true` | Mide cada audio con ffmpeg antes de mandarlo a la IA; lo que no tiene voz no se audita y queda como incidencia. `false` lo apaga |
| `AUDIO_SILENCIO_NOISE_DB` / `AUDIO_SILENCIO_DUR_MIN` | `-40.0` / `1.0` | Por debajo de ese volumen, durante al menos ese tiempo, un tramo cuenta como silencio |
| `AUDIO_MUDO_RATIO_SILENCIO` / `AUDIO_MUDO_MEAN_DB` | `0.98` / `-50.0` | Umbrales para declarar un audio mudo (alcanza con uno) |
| `AUDIO_SOSPECHOSO_RATIO_SILENCIO` | `0.90` | Casi todo silencio: se audita, pero marcado para revisión |

Antes de mover los umbrales de audio, calibrarlos con `scripts/calibrar_audio_mudo.py`.

### Auditorías: plantillas

| Variable | Default | Descripción |
|---|---|---|
| `ATRIBUTO_TEXTO_MAX_CARACTERES` | `1500` | Tope de los atributos de texto libre. `0` = sin tope |
| `ATRIBUTO_BLOQUEAR_TRANSCRIPCION` | `true` | Rechaza atributos redactados como "transcribí la conversación" |
| `PLANTILLA_SENALES_TEXTO` | `true` | Detecta problemas de redacción del prompt (fechas fijas, textos sin reemplazar) |
| `PLANTILLA_BLOQUEAR_SENALES_ALTAS` | `true` | Las señales de severidad alta frenan el guardado (se puede "guardar igual") |
| `PLANTILLAS_SALUD_DESTINATARIOS` | vacío | Destinatarios del reporte semanal de salud de plantillas, separados por `;`. `ADMIN_EMAIL` se suma siempre. **Hoy está vacío en los dos servidores**: el reporte solo le llega a `ADMIN_EMAIL` |
| `PLANTILLAS_SALUD_ENVIAR_SIN_HALLAZGOS` | `true` | Manda el reporte aunque no haya nada crítico |
| `REVISION_EVIDENCIA_DIAS` | `90` | Días de auditorías que mira la "revisión de plantilla con IA" |
| `REVISION_EVIDENCIA_MAX_AUDITORIAS` | `2000` | Tope de auditorías de esa evidencia (vuelve predecible el costo de la consulta) |
| `REVISION_EVIDENCIA_TIMEOUT_SEG` | `20` | Timeout de cada consulta de evidencia; si vence, la revisión sigue sin esos datos |
| `REVISION_JOBS_DIR` / `ASISTENTE_JOBS_DIR` | `backend/storage/revision_jobs` / `…/asistente_jobs` | Trabajos en curso de la revisión con IA y del asistente del dashboard. Tienen que ser directorios compartidos por todos los workers de la API |

### Transcripciones a demanda

| Variable | Default | Descripción |
|---|---|---|
| `TRANSCRIPCION_MOTOR` | `gemini_batch` | Con este valor, cada pedido se resuelve por Flex (chico, en minutos) o por lote (grande). `fastwhisper` (local, con GPU) no está implementado |
| `TRANSCRIPCION_FLEX_MAX_PEDIDO` | `10` | Hasta cuántos llamados por pedido van por Flex |
| `TRANSCRIPCION_FLEX_WORKERS` | `3` | Llamados Flex en paralelo |
| `TRANSCRIPCION_FLEX_TIMEOUT_MS` | `900000` | Timeout de un llamado Flex (15 min) |
| `TRANSCRIPCION_FLEX_REINTENTOS` / `TRANSCRIPCION_FLEX_BACKOFF_SEG` | `3` / `15` | Reintentos ante 429/503; agotados, el pedido pasa a lote |
| `TRANSCRIPCION_MODELO` | vacío | Vacío = el modelo por defecto del catálogo (`modelos_ia.MODELO_IA_DEFAULT`) |
| `TRANSCRIPCION_MAX_POR_TICK` | `200` | Pedidos que despacha cada pasada del scheduler |
| `TRANSCRIPCION_NIVEL_RAZONAMIENTO` | `LOW` | Transcribir es dictado: con `LOW` sale igual de completa y sin tokens de razonamiento |

### Chatbots RAG

Los chatbots (nombre, prompt, documentos, permisos, PCRC) **no se configuran acá**: viven en la base
(`pagina_web.Chatbots` y tablas asociadas) y se administran desde *Administración > Chatbots*. Los vectores
viven en Qdrant (colección `bot_{slug}_v{N}` y alias `bot_{slug}`) y el resto del índice en
`{CHATBOT_STORAGE_ROOT}/{slug}/v{N}/`.

| Variable | Default | Descripción |
|---|---|---|
| `QDRANT_URL` | `http://127.0.0.1:6333` | Qdrant local (contenedor Docker) |
| `CHATBOT_STORAGE_ROOT` | `/var/www/chatcsva/storage/chatbots` | Carpeta por bot: `{slug}/{docs,logs,v{N}}` |
| `CHATBOT_FOLLOW_INDEX_RELOAD` | `true` | El backend recarga un bot cuando cambia su versión de índice. `false` fija la instancia del arranque (depuración) |
| `CHATBOT_REGISTRY_TTL_SECONDS` | `60` | Cada cuánto cada worker relee la lista de bots. Un cambio en la base tarda hasta esto en verse |
| `CUSTOM_CHUNK_SIZE` / `CUSTOM_CHUNK_OVERLAP` | `512` / `100` | Tamaño de los pedazos del índice. Solo afecta reindexados nuevos |
| `CHATBOT_RETRIEVAL_TOP_K` | `12` | Candidatos por buscador (vectores y BM25) antes del reranker |
| `DEFAULT_RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L12-v2` | Reordena los candidatos. Todos los umbrales de vacíos y desambiguación están calibrados en su escala |
| `RERANKER_MODELO_CALIBRADO` | igual al anterior | Si no coincide con `DEFAULT_RERANKER_MODEL`, el backend avisa por log que los umbrales quedaron descalibrados |
| `DEFAULT_RERANKER_TOP_N` | `8` | Pedazos que llegan al modelo |
| `CHATBOT_CACHE_MIN_SCORE` / `CHATBOT_CACHE_MIN_PALABRAS` | `0.95` / `6` | Caché de respuestas: similitud mínima y largo mínimo de pregunta |
| `CHATBOT_EMBED_KEEPALIVE_SECONDS` | `90` | Ping para mantener caliente la conexión de embeddings. `0` lo apaga |
| `CHATBOT_SESSION_GAP_MINUTES` | `10` | Pasado este hueco, la memoria de la conversación arranca limpia. `0` lo apaga |
| `EMBED_BATCH_SIZE` | `100` | Textos por pedido de embeddings. **100 es el tope de la API** |
| `EMBED_REINTENTOS` | `8` | Reintentos ante 429/503 de los embeddings |
| `EMBED_BACKOFF_MIN_SEG` / `EMBED_BACKOFF_MAX_SEG` | `2` / `120` | Espera entre reintentos (se duplica) |
| `INDEX_REINTENTOS_SIN_CUPO` | `5` | Veces que un reindexado vuelve a la cola si falló por cuota de Gemini |
| `CHATBOT_DOCS_VERIFY_ROUNDS` | `3` | Rondas de verificación del asistente de documentación. `0` lo apaga |
| `CHATBOT_DOCS_MAX_CHARS_UNA_PASADA` | `60000` | A partir de este tamaño el asistente arma un documento por llamada |

**Vacíos de conocimiento** (consultas que el bot no pudo responder; ver [ESTADISTICA.md](ESTADISTICA.md)):

| Variable | Default | Descripción |
|---|---|---|
| `CHATBOT_VACIOS_MODELO` / `CHATBOT_VACIOS_MODELO_JUEZ` | `gemini-3.5-flash-lite` / `gemini-3.8-flash` | Clasificador y juez ("¿este texto contiene la respuesta?") |
| `CHATBOT_VACIOS_LOTE` | `50` | Consultas que clasifica por pasada del scheduler |
| `CHATBOT_VACIO_SCORE_MIN` | `-5.0` | Score del reranker por debajo del cual nada de lo recuperado sirve |
| `CHATBOT_VACIO_SCORE_EXISTE` | `3.0` | Score a partir del cual un tema se da por documentado |
| `CHATBOT_VACIO_SONDEO_TOP_K` / `CHATBOT_VACIO_FRAGMENTOS_A_LEER` | `50` / `5` | Búsqueda ampliada del sondeo y pedazos que lee el juez |
| `CHATBOT_VACIO_LARGO_RESPUESTA` | `400` | Un "no encontré" más largo que esto igual cuenta como respuesta |
| `CHATBOT_VACIO_AGRUPAR_MIN_SCORE` | `0.88` | Similitud para agrupar dos temas como el mismo |

**Desambiguación** (ofrecer temas para elegir en vez de "no encontré"). Sus umbrales están atados a los de
vacíos: si se cambia el reranker, se recalibran todos juntos.

| Variable | Default | Descripción |
|---|---|---|
| `CHATBOT_DESAMBIGUACION_SLUGS` | los 12 bots telefónicos | Bots que ofrecen temas. Lista vacía = apagado. `voltara_digital` queda afuera a propósito |
| `CHATBOT_DESAMB_SCORE_MIN` | `0.0` | Piso para ofrecer opciones |
| `CHATBOT_DESAMB_EMPATE_FRACCION` | `0.5` | Cuán parejos tienen que estar los mejores temas |
| `CHATBOT_DESAMB_MAX_OPCIONES` | `4` | Opciones que se muestran |
| `CHATBOT_DESAMB_NEGATIVA_MAX_CHARS` | `280` | Hasta este largo, un "no encontré" se puede reemplazar por la lista de temas |

**Adjuntos del chatbot** (capturas y PDF). Quién puede adjuntar (permiso `chatbot.adjuntos`) y a qué bot
(`pagina_web.Chatbots.permite_adjuntos`) se configura en la base, para poder cortarlo sin deploy.

| Variable | Default | Descripción |
|---|---|---|
| `CHATBOT_ADJUNTOS_MAX_ARCHIVOS` | `3` | Archivos por consulta |
| `CHATBOT_ADJUNTOS_MAX_MB` / `CHATBOT_ADJUNTOS_MAX_MB_TOTAL` | `8` / `15` | Tope por archivo y total por consulta (Gemini acepta hasta 20 MB por pedido) |
| `CHATBOT_ADJUNTOS_MAX_PAGINAS_PDF` | `20` | Páginas máximas de un PDF |
| `CHATBOT_ADJUNTOS_MAX_LADO_PX` | `1568` | Lado mayor al que se reescala una imagen. `0` no reescala |
| `CHATBOT_ADJUNTOS_EXTRAER_TEXTO` | `true` | Extrae el texto del adjunto para buscar en el índice |
| `CHATBOT_ADJUNTOS_MODELO_TEXTO` | `gemini-3.5-flash-lite` | Modelo que transcribe imágenes y PDF escaneados |
| `CHATBOT_ADJUNTOS_MIN_CHARS_CAPA` | `40` | Menos caracteres por página = PDF escaneado |
| `CHATBOT_ADJUNTOS_TEXTO_MAX_CHARS` / `CHATBOT_ADJUNTOS_TEXTO_HISTORIAL_CHARS` | `1200` / `500` | Texto del adjunto que entra a la búsqueda y que queda en el historial |

**Tablas de datos** (conocimiento entidad → atributos que no pasa por el RAG, como las bases de Vantix o la
cartera de Benefix):

| Variable | Default | Descripción |
|---|---|---|
| `CHATBOT_TABLAS_ACTIVO` | `true` | Interruptor general. `false` = los bots ignoran las tablas |
| `CHATBOT_TABLAS_TTL_SECONDS` | `60` | Cada cuánto se revalida la caché de tablas |
| `CHATBOT_TABLA_MAX_CHARS_COMPLETA` | `24000` | Hasta este tamaño la tabla entra entera al prompt; si no, se busca la fila |
| `CHATBOT_TABLA_MAX_FILAS` | `12` | Filas máximas que recibe el modelo en una búsqueda |
| `CHATBOT_TABLA_SCORE_MIN` | `1.5` | Puntaje mínimo para dar por encontrada una fila |
| `CHATBOT_TABLA_EMPATE_FRACCION` | `0.6` | Filas que se conservan por empate |
| `CHATBOT_TABLA_BONUS_FRASE` / `CHATBOT_TABLA_MIN_CHARS_FRASE` | `4.0` / `5` | Peso y largo mínimo de una coincidencia por frase completa |
| `CHATBOT_TABLA_SIMILITUD_MIN` | `0.72` | Similitud mínima con la descripción de la tabla para usarla |
| `CHATBOT_TABLA_RESUMEN_MAX_VALORES` | `30` | Valores distintos máximos para listar una columna en el resumen |
| `CHATBOT_TABLA_MIN_FILAS_DETECCION` | `8` | Filas mínimas para sospechar que un documento es una tabla |

### Plataformas y servicios externos (datos no secretos)

| Variable | Default | Descripción |
|---|---|---|
| `VERINT_LOGIN_URL` | `https://10.0.2.99/` | Verint (sin uso) |
| `CXONE_LOGIN_URL` / `CXONE_OUTLOOK_URL` | `https://na1.nice-incontact.com` / `https://outlook.office365.com/mail/` | CXone. **Sin uso**: las URLs reales están escritas en `CXOne.py` |
| `YOIZEN_LOGIN_URL` | `http://10.0.2.70/Reports/Login.aspx` | Yoizen |
| `MITROL_URL` | `https://apps.acme-solutions.example/reportes/login.aspx` | Login de Mitrol (las URLs de descarga están escritas en `Mitrol.py`) |
| `HERMES_STATION` | `89550` | Puesto con el que Hermes registra la sesión |
| `CYT_DOWNLOAD_WORKERS` | `4` | Navegadores en paralelo contra el grabador CyT. `1` si el grabador no tolera la concurrencia |
| `GENESYS_REGION` | `sae1.pure.cloud` | Región de la organización de Benefix en Genesys Cloud |
| `GENESYS_DOWNLOAD_WORKERS` | `4` | Audios de Genesys en paralelo |
| `CHROME_BINARY_LOCATION` | `/usr/bin/google-chrome` | Chrome que usa Selenium |
| `CREDENTIALS_GOOGLE_SHEET_JSON` | `CREDENTIALS_GOOGLE_SHEET_JSON.json` | Nombre del JSON de la cuenta de servicio de Google (el archivo es secreto, vive en `backend/` y está en `.gitignore`) |

### Google Sheets

Un ID de planilla no da acceso por sí solo: el acceso lo da la cuenta de servicio, que tiene que estar
compartida como editora en cada planilla. Los IDs de las planillas de resultados de cada tarea programada no
están acá sino en la base (en cada tarea).

| Variable | Planilla |
|---|---|
| `SHEET_ID_INFORMACION` | Información general |
| `SHEET_ID_INTERNOS` | Internos |
| `SHEET_ID_DENTAL_CRONOGRAMA_DE_ATENCION` | Cronograma de atención de Dental |
| `SHEET_ID_INGRESOS` | Ingresos |
| `SHEET_ID_CANDIDATOS` | Candidatos de RRHH (módulo en desuso) |

Los Tips del Día ya no usan Google Sheets: viven en `pagina_web.TipGroups` y `pagina_web.Tips` y se
administran desde `/admin/tips`.

### Correo

| Variable | Default | Descripción |
|---|---|---|
| `RRHH_MAIL` | casilla de empleos | Casilla de RRHH |
| `ENVIOS_OPERACIONES_MAIL` | casilla de envíos | Casilla desde la que salen resultados y alertas |
| `CALIDAD1_MAIL` | casilla de Calidad | Casilla de Calidad |
| `GMAIL_IMAP_SERVER` / `GMAIL_SMTP_SERVER` | `imap.gmail.com` / `smtp.gmail.com` | Servidores de correo |
| `RRHH_PLANTILLA_ID_DEFAULT` | `14` | Plantilla por defecto de las tareas automáticas de RRHH |

---

## 5. Topes de subida: tres capas

Una subida grande (la auditoría por archivos de Voltara y CSV manda tandas de 10 audios en un solo POST)
atraviesa tres topes. **Manda el más chico**, y el que corta primero es Nginx:

| Capa | Dónde | Valor | Qué se ve si se supera |
|---|---|---|---|
| Nginx | `client_max_body_size` en el sitio `contact-center.local` de SRV01 | 100 MB | 413 de Nginx |
| Flask (toda la app) | `MAX_UPLOAD_MB` en `frontend/config.py` | 500 MB | 413 de Flask. El navegador lo muestra como `Unexpected token '<', "<!doctype "... is not valid JSON` |
| Ruta del chatbot | `CHATBOT_MAX_UPLOAD_MB` en `frontend/config.py` | 25 MB | Mensaje del chatbot |
| Backend (adjuntos del chatbot) | `CHATBOT_ADJUNTOS_MAX_MB_TOTAL` | 15 MB | Mensaje del chatbot |

**No bajar `MAX_UPLOAD_MB` pensando en el chatbot**: cuando valía 25 MB, toda subida de audios moría con 413.
Si se suben los topes, hay que tocar las capas que correspondan.

---

## 6. Qué configuración NO está en ningún archivo

Vive en la base y se cambia desde la aplicación o con una migración:

- Chatbots, documentos, PCRC y permisos de adjuntos: `pagina_web.Chatbots` y tablas asociadas.
- Plantillas, atributos, modelo y nivel de razonamiento de cada plantilla: esquema `calidad`.
- Tareas programadas de auditoría (fechas, destinatarios, planillas): `calidad.AuditSchedulers`.
- Cupos de auditoría por campaña: ver [ARQUITECTURA.md](ARQUITECTURA.md).
- Presupuesto mensual de IA y umbrales de aviso: `pagina_web.IA_Presupuesto`.
- Precios de los modelos de IA con su vigencia: `pagina_web.IA_Precios`.
- Parámetros del planificador (objetivos de nivel de servicio, shrinkage, pools): esquema `planificacion`,
  ver [PLANIFICADOR.md](PLANIFICADOR.md).
- Roles y permisos: `pagina_web` (RBAC).

El detalle de tablas está en [BASE_DE_DATOS.md](BASE_DE_DATOS.md).
