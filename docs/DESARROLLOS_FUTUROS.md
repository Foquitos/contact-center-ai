# Desarrollos futuros

Los proyectos que quedaron planteados para el sistema al 25/09/2026, y de qué se parte en cada uno. No es un
compromiso de fechas: es el punto de partida para no rediseñar de cero ni repetir lo que ya se midió.

Qué **no** va acá: la deuda y los pendientes de operación (OPERACION.md,
TRASPASO.md §6) ni los parámetros sin decidir de algo que ya
funciona ([DECISIONES.md](DECISIONES.md#abiertas)). Cuando algo de esta lista se hace, se borra de acá y se
documenta en el doc que corresponda.

Índice: [Proyectos](#proyectos) · [Mejoras chicas ya estudiadas](#mejoras-chicas-ya-estudiadas)

---

## Proyectos

| # | Proyecto | Área |
|---|---|---|
| 1 | [Gasur en el Auditor](#1-gasur-en-el-auditor) | Auditorías |
| 2 | [Descarga automática de audios de Voltara](#2-descarga-automática-de-audios-de-voltara) | Auditorías |
| 3 | [Golden sets para mejorar los prompts solos](#3-golden-sets-para-mejorar-los-prompts-solos) | Auditorías |
| 4 | [Avisos de demanda y extras inteligentes](#4-avisos-de-demanda-y-extras-inteligentes) | Planificador |
| 5 | [Archivos de Omnia desde el planificador](#5-archivos-de-omnia-desde-el-planificador) | Planificador |
| 6 | [CSV en el planificador: forecast por PCRC y balanceo](#6-csv-en-el-planificador-forecast-por-pcrc-y-balanceo) | Planificador |
| 7 | [Informe de llamadas de Voltara cada pocos minutos](#7-informe-de-llamadas-de-voltara-cada-pocos-minutos) | Planificador |
| 8 | [Chatbot sobre los Power BI de Reporting](#8-chatbot-sobre-los-power-bi-de-reporting) | Chatbots |
| 9 | [Chatbot RAG que consulta bases de clientes por API](#9-chatbot-rag-que-consulta-bases-de-clientes-por-api) | Chatbots |
| 10 | [Chatbots de CSV elegidos por PCRC](#10-chatbots-de-csv-elegidos-por-pcrc) | Chatbots |
| 11 | [Agente de toda la página](#11-agente-de-toda-la-página) | Transversal |

### 1. Gasur en el Auditor

Auditar con IA las llamadas de Gasur como las del resto de las campañas.

- **De qué se parte:** Gasur ya existe en el planificador (campaña 30, Uruguay) y en calidad como empresa
  con permiso `template:gasur`. Falta todo lo de auditoría.
- **Qué hay que resolver:** de dónde salen los audios y los datos de la llamada (plataforma, acceso, si hay
  API o export). Con eso: el conector en `AuditorIA/downloads/`, la rama en `Auditor.py`
  (`__descarga_audios`), el builder de `SQL_query.py` con su dedup y cómo se arma el `IdAplicativo`
  (siempre en `calcular_id_aplicativo`). Si termina siendo por subida de archivos, sumar la rama en el
  proxy `frontend/app/routes/audit.py`.
- **Ojo:** a Acme le llega solo el desborde de Gasur (y el 100% en sus paros): el volumen es irregular.

### 2. Descarga automática de audios de Voltara

Hoy Voltara se audita **por subida**: alguien sube audios + un Excel (nombre → ConnID) y el sistema cruza el
informe IVR y Salesforce. El proyecto es que los audios bajen solos y Voltara pueda ir a tareas programadas.

- **Estado:** pausado, esperando la API del cliente.
- **De qué se parte:** el cruce con el informe IVR y Salesforce ya está hecho para la subida; lo que cambia
  es solo el origen del audio. El conector viejo de Verint (`downloads/Verint.py`) quedó sin uso.
- **Cuando llegue la API:** conector nuevo en `AuditorIA/downloads/`, rama en `Auditor.py`, builder con
  `NOT EXISTS` (dedup antes de descargar) y el mismo `IdAplicativo` que genera hoy la subida, para que no se
  re-auditen llamadas ya auditadas por el camino manual.

### 3. Golden sets para mejorar los prompts solos

Que cada plantilla tenga un golden set (llamadas corregidas por una persona) y que el sistema use los
desacuerdos entre la IA y la corrección para proponer mejoras al prompt, medirlas y quedarse con la que acierta más.

- **De qué se parte:** ya existen los golden sets y la evaluación de plantillas
  ([ARQUITECTURA.md](ARQUITECTURA.md#golden-set--revisión-humana-y-evaluación-de-plantillas),
  [ESTADISTICA.md](ESTADISTICA.md)) y el asistente que reescribe prompts (`POST /plantillas/ia/mejorar-prompt`
  y la revisión integral).
- **Lo que falta:** cerrar el lazo: desacuerdos del golden set → propuesta de prompt → evaluación contra el
  mismo set (sin sobreajustar: separar casos para proponer y para medir) → aprobación humana.
- **El cuello de botella son las revisiones humanas, no el código.** Medido el 20/08/2026:
  `calidad.AuditoriaRevisiones` estaba vacía con `audit:review` asignado a 26 personas, y los golden sets que
  existían eran de prueba. Sin revisiones no hay contra qué medir (ver P7 en
  TRASPASO.md).

### 4. Avisos de demanda y extras inteligentes

Que el planificador avise cuando viene más o menos demanda de la planificada y proponga (o pida) horas extra
para las próximas horas, o cancele las que sobran, para seguir el caudal de llamadas.

- **De qué se parte:** el reescalado intradía ya corrige el plan del día con lo que va entrando; la brecha se
  agrupa en bloques pedibles (`pl.agrupar_refuerzos`) y se clasifica por antelación (malla / horas extra /
  convocatoria); los pedidos se cargan y siguen a mano (`planificador_pedidos.py`). Para Hidra ya hay avisos de
  corte programado que proponen un ajuste.
- **Lo que falta:** el disparador (cada cuánto se recalcula dentro del día y con qué umbral se avisa), el canal
  (mail u otro) y el circuito de pedido/cancelación con su aprobación.
- **Criterio ya decidido:** pasarse es peor que quedarse corto (horas ociosas); el horizonte que importa para
  extras es de 1 a 3 días, y para el intradía, las próximas horas.

### 5. Archivos de Omnia desde el planificador

Que el planificador genere el archivo para cargar en Omnia la asignación de operadores, para ubicar a la
gente lo más rápido posible.

- **De qué se parte:** el planificador ya calcula cuántos operadores hacen falta por media hora, por pool y por
  skill, y lo exporta (`planificador_export.py`).
- **Qué hay que resolver:** el formato que acepta Omnia y qué decide el archivo (qué operador va a qué
  skill/cola y en qué horario). No hay nada de Omnia en el repo todavía.

### 6. CSV en el planificador: forecast por PCRC y balanceo

Sumar CSV al planificador con un pronóstico **por PCRC**, y que los GTR hagan el balanceo entre PCRC (pasar
operadores de una cola a otra cuando hace falta) directamente desde la página, con una posible automatización
más adelante.

- **Qué cambia respecto de las otras campañas:** CSV se cobra **por hora de operador**, no por llamada
  atendida. El objetivo no es dimensionar al mínimo sino mantener todas las colas lo mejor atendidas posible con
  la gente que hay, con **prioridades entre colas** (unas pesan más que otras).
- **De qué se parte:**
  - El planificador ya pronostica y dimensiona por skill y por pool, y ya maneja prioridad entre skills (en
    Voltara, Electrodependientes tiene techo de abandono y prioridad en la cola).
  - La relación operador → PCRC vigente ya se resuelve para los chatbots (`CSV Historial Skill-PCRC`,
    `CSV Normalizador PCRC`, `resolver_slug_csv_por_pcrc` en `app/routers/chatbot.py`).
- **Qué hay que resolver:**
  - La fuente de llamadas por PCRC y por intervalo (como el informe IVR en Voltara) y su historia para el
    pronóstico.
  - El balanceo: con la dotación fija del día, repartir operadores entre PCRC según demanda y prioridad; la
    pantalla propone movimientos y el GTR los confirma. La automatización viene después, sobre lo que el GTR
    ya valida a mano.
  - Cómo se ejecuta el movimiento: el traspaso entre PCRC se hace en **Avaya**, un aplicativo del cliente que
    tiene scripts para eso. Hay que ver si esos scripts se pueden invocar desde el sistema (para que el botón
    del GTR mueva de verdad al operador) o si la página solo prepara el movimiento y el GTR lo ejecuta en Avaya.

### 7. Informe de llamadas de Voltara cada pocos minutos

Bajar el informe de llamadas del portal de Enerval, con los datos de **todo el cliente** (todos los proveedores,
no solo Acme), cada X minutos durante el día, para seguir el día en curso de cerca y replanificarlo mejor.

- **De qué se parte:** `scripts/enerval_informe_ivr.py` ya baja el informe IVR por HTTP puro, un día por pedido,
  con el avance registrado en `dbo.[Voltara Enerval informe IVR cargas]`. Hoy corre una vez por día (04:00, pide
  anteayer, ayer y hoy), así que durante el día el planificador de Voltara trabaja con el día anterior cerrado.
  El reescalado intradía (desde las 14) y la pantalla con el plan de hoy, que se relee cada 10 minutos, ya
  están: les falta el dato fresco.
- **La traba principal: el portal admite una sola sesión por cuenta**, y la cuenta actual la usan personas
  durante el día (por eso el histórico corre de noche). Bajar cada X minutos con esa cuenta los desloguea.
  Hace falta una **cuenta propia para el sistema** con alcance completo (con una cuenta acotada va
  `--alcance propio`, o se borra el resto de los datos).
- **Qué hay que resolver:**
  - El intervalo (X): cuánto tarda el portal en reflejar las llamadas y cuánto aguanta un pedido cada pocos
    minutos.
  - Que la carga de hoy reemplace el día parcial sin duplicar filas y sin tapar un día cerrado.
  - Qué partes del planificador pasan a leer el total fresco: `dbo.[Voltara Enerval informe IVR]` trae BPO y skill
    de todos los contact centers, pero hoy parte del seguimiento sale de `dbo.[Voltara Enerval informe skills]`
    (solo lo de Acme), que no carga este script.
  - Con dato fresco, volver a medir desde qué hora conviene prender el intradía (hoy desde las 14) y la
    persistencia de hoy. Es la base del [proyecto 4](#4-avisos-de-demanda-y-extras-inteligentes) en Voltara.

### 8. Chatbot sobre los Power BI de Reporting

Un chatbot que consulte los tableros de Power BI del equipo de Reporting y dé la información ya masticada, para
que gerencia y los jefes de campaña pregunten directamente.

- **Reemplaza al chatbot SQL de gerencia** (orquestador + agentes por campaña), que no se sigue desarrollando.
- **De qué se parte:** `powerbi_mcp.py` (raíz del repo): conexión a la API de Power BI con consultas DAX,
  solo lectura, con dos modos de autenticación (usuario por *device code*, necesario para workspaces Premium
  por usuario, o service principal).
- **Qué hay que resolver:** qué datasets y workspaces de Reporting entran, con qué cuenta se autentica el
  servicio y cómo se mapea cada jefe a sus campañas (permisos por campaña, como `template:<empresa>`).

### 9. Chatbot RAG que consulta bases de clientes por API

Que los bots RAG, además de la documentación, consulten en vivo las bases del cliente (estado de un reclamo,
datos de una cuenta) a través de su API.

- **De qué se parte:** las tablas de datos del chatbot ya separan lo que es un lookup de lo que es prosa (modo
  `lookup`: se buscan las filas por columnas clave y solo esas van al prompt;
  [ARQUITECTURA.md](ARQUITECTURA.md), "Tablas de datos"). Una API del cliente es el mismo caso con otra fuente.
- **Qué hay que resolver:** API por cliente (cuál, credenciales, límites), qué se le puede mostrar a cada
  operador, y que los datos personales no queden en los logs del chatbot más de lo necesario.

### 10. Chatbots de CSV elegidos por PCRC

Los operadores de CSV comparten el permiso `chatbot:csv` y el sistema les abre el bot de su PCRC vigente sin
que elijan. Está **pausado**: se retoma cuando Calidad termine de armar los bots de CSV.

- **De qué se parte:** el mecanismo está en el código (`resolver_slug_csv_por_pcrc` y
  `_resolver_slug_efectivo` en `app/routers/chatbot.py`; mapeo PCRC → bot en `pagina_web.ChatbotPcrc`). Sin
  PCRC vigente o sin mapeo, Calidad ve el selector con todos los bots de CSV y el operador recibe un error.
- **Para volver a prenderlo:** que cada PCRC activo tenga su bot con documentación cargada e indexada, cargar
  el mapeo en `ChatbotPcrc` (desde `Administración > Chatbots`), verificar que el PCRC vigente de los operadores
  esté al día y asignar `chatbot:csv` a sus roles.

### 11. Agente de toda la página

Un agente único que responda de todo según el rol de quien pregunta: lanzar auditorías, ver el planificador,
responder preguntas de gestión, analizar auditorías realizadas y revisar los logs. La unión de todo lo que hoy
está en pantallas separadas.

- **De qué se parte:** cada pieza ya existe como endpoint del backend con su permiso (`RoleChecker`), y hay
  asistentes por pantalla: el analista del dashboard y los chatbots RAG. Los proyectos 8 y 9 serían
  herramientas de este agente.
- **Diseño natural:** herramientas (function calling) que llaman a los endpoints existentes **con el token del
  usuario**, así el agente nunca puede más que la persona: el RBAC ya hecho es el control de acceso.
- **Qué hay que resolver:**
  - No hay precedente de function calling en el repo.
  - Las acciones que gastan o escriben (lanzar una auditoría, pedir extras) piden confirmación explícita.
  - El costo va a `IA_Uso` con feature propia.

---

## Mejoras chicas ya estudiadas

Trabajos acotados, con la medición o el diseño ya hechos.

| Mejora | Estado | Qué falta / qué lo destraba |
|---|---|---|
| **Adjuntos del chatbot, fase 3** (guardar los adjuntos con retención corta, verlos en `/uso-ia`, tokens reales) | Fases 1 y 2 en producción | Decidir si se sigue: la adopción medida el 20/08/2026 fue casi nula (2 usos). Contar usos con `CHARINDEX('[Adjuntos:', query) > 0`; **no** con `LIKE '%[Adjuntos:%'` (en T-SQL los corchetes son una clase de caracteres) |
| **faster-whisper local** para transcribir | Diseñado | Que el servidor tenga GPU. Implementar `_despachar_fastwhisper` en `backend/AuditorIA/transcripcion_cola.py` y poner `TRANSCRIPCION_MOTOR=fastwhisper`; la UI, la tabla y la cola no cambian |
| **Perfil de arranque de turno** (shrinkage por media hora) | Medido | El faltante se concentra en la media hora en punto (09:00 22,8% contra 09:30 1,6%). Al hacerlo, sacar `dentro_del_turno` del shrinkage plano para no contarlo dos veces |
| **Cortes del ENRE como señal del pronóstico** | Medido: el total empeora el plan | Volver a medir con los imprevistos solos y los programados por comunicado, con 4 a 6 semanas de datos separados (se guardan desde el 24/09/2026) |
| CAMMESA y SMN como señales | Descartado / bloqueado | CAMMESA casi no mejora (19,5% contra 19,6%); el SMN no responde desde SRV00 ni SRV01 |
