# contact-center-ai

> **Proyecto de portfolio.** Es la plataforma que desarrollé para el contact center de una empresa de BPO, publicada
> con autorización de la empresa. Esta versión está **anonimizada**: la empresa, sus clientes y las personas tienen
> nombres ficticios (Acme, Voltara, Hidra, Benefix, Vantix…), y se sacaron IPs, dominios, identificadores de Google,
> credenciales y datos reales. También quedaron afuera los documentos operativos internos (runbook de producción,
> integraciones y traspaso) y la foto del esquema de la base: cuando el texto los menciona, no están en este repo.

Plataforma interna del contact center: **auditoría de calidad con IA** (audios y chats evaluados con Gemini
según plantillas), **chatbots** que responden con la documentación de cada campaña, **planificador** de
llamadas y dotación, y administración de usuarios y permisos.

Es un monorepo Python con dos servicios web y un proceso de tareas:

| Servicio | Tecnología | Puerto | Qué hace |
|---|---|---|---|
| `backend/` | FastAPI + Gunicorn | 8000 | API, lógica de negocio, IA; el único que habla con la base y los sistemas externos |
| `frontend/` | Flask + Gunicorn | 7000 | Interfaz web. **Nunca toca la base**: todo lo pide al backend |
| `backend/run_scheduler.py` | APScheduler | — | Colas, auditorías programadas, lotes de Gemini, alertas |

En la instalación original, producción y desarrollo corrían en dos servidores Linux (**SRV01** y **SRV00**) contra
la **misma base** SQL Server (base `Acme` en esta versión).

---

## Qué leer para cada cosa

| Si necesitás… | Leé |
|---|---|
| Entender cómo está armado y cómo viaja una auditoría | [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md) |
| Saber qué variable configura qué y dónde vive | [docs/CONFIGURACION.md](docs/CONFIGURACION.md) |
| Tablas, cargas externas, linked servers, cómo escribir una migración | [docs/BASE_DE_DATOS.md](docs/BASE_DE_DATOS.md) |
| El planificador (pronóstico y dotación) | [docs/PLANIFICADOR.md](docs/PLANIFICADOR.md) |
| Kappa, semáforo de plantillas, Erlang, WAPE: qué miden y qué no tocar | [docs/ESTADISTICA.md](docs/ESTADISTICA.md) |
| Por qué algo está hecho de una forma que parece rara | [docs/DECISIONES.md](docs/DECISIONES.md) |
| Qué significa un término | [docs/GLOSARIO.md](docs/GLOSARIO.md) |
| Los proyectos a futuro y las mejoras chicas ya estudiadas | [docs/DESARROLLOS_FUTUROS.md](docs/DESARROLLOS_FUTUROS.md) |

El manual para usuarios finales está dentro de la aplicación, en `/documentacion`.

---

## Módulos

- **AuditorIA** — baja interacciones de cada plataforma, las evalúa con Gemini según la plantilla de la campaña
  y guarda puntaje, errores críticos y transcripción. Modo Batch por defecto; tareas programadas; exporta a
  Google Sheets y mail. → [ARQUITECTURA](docs/ARQUITECTURA.md#1-auditoría-de-calidad-auditoria)
- **Golden Set y evaluación** — mide cuánto acierta la IA contra la revisión de un auditor (kappa por
  atributo). → [ESTADISTICA](docs/ESTADISTICA.md)
- **Dashboard de auditorías** — gráficos, tablas y tendencias por operador y atributo, con asistente de IA.
  → [ARQUITECTURA](docs/ARQUITECTURA.md#dashboard-de-auditorías-tagsbandeja)
- **Plantillas** — ABM de campañas, plantillas y atributos, con asistente de IA y controles de redacción.
  → [ARQUITECTURA](docs/ARQUITECTURA.md#plantillas-y-prompts-tagsplantillas)
- **Chatbots RAG** — responden a los operadores con la documentación de cada campaña (LlamaIndex + Qdrant +
  Gemini). → [ARQUITECTURA](docs/ARQUITECTURA.md#2-chatbot-rag)
- **Planificador** — pronóstico por media hora y operadores necesarios para Voltara y Hidra Técnico.
  → [PLANIFICADOR](docs/PLANIFICADOR.md)
- **Uso de IA** — consumo, costo y presupuesto mensual. → [ARQUITECTURA](docs/ARQUITECTURA.md)
- **Usuarios, roles y permisos** — RBAC con jerarquía de roles y alcance por empresa.
  → [ARQUITECTURA](docs/ARQUITECTURA.md#autenticación-y-autorización-rbac)
- **Coral y tutorial guiado** — ayuda en pantalla sobre el manual. → [ARQUITECTURA](docs/ARQUITECTURA.md)
- **ChatBot SQL** (discontinuado) y **RRHH** (en desuso): el código sigue en el repo.

---

## Instalación en desarrollo

Requisitos: **Python 3.11 o más** (numpy y scipy lo exigen), driver ODBC 18 de SQL Server, Google Chrome (para
los conectores con Selenium), ffmpeg y `make`.

```bash
git clone <url-del-repositorio> && cd contact-center-ai
make install              # crea .venv en la raíz e instala requirements.txt (un solo venv para todo)
cp .env.example .env      # completar los secretos (ver docs/CONFIGURACION.md)
```

En producción el venv se llamaba `venv` (sin punto) y se desplegaba con `git pull` en el servidor.

## Ejecución

```bash
make dev                  # backend (:8000) + frontend (:7000)
make backend              # solo backend  → http://localhost:8000/docs
make frontend             # solo frontend → http://localhost:7000

cd backend && ../.venv/bin/python run_scheduler.py   # scheduler, si hace falta en dev
```

`make backend` y `make frontend` corren `make install` antes de arrancar. En dev, el scheduler toma solo el
trabajo de `ENVIRONMENT=dev` y no registra los jobs de producción.

## Tests

```bash
scripts/correr_tests.sh -m "not tokens"               # suite offline (~2.400 tests, ~50 s, sin gastar IA)
scripts/correr_tests.sh tests/test_batch_cola.py -k claim   # un archivo o un caso
scripts/correr_tests.sh tests/test_chatbots_live.py -m tokens  # GASTA tokens de IA: solo a propósito
```

Los tests están en `backend/tests/` (configuración en `backend/conftest.py`). Varios validan SQL contra la base
**sin ejecutarlo** (fixture `validar_sql`): necesitan el `.env` y conexión a la base. Ver
[BASE_DE_DATOS.md → Cómo se valida](docs/BASE_DE_DATOS.md#cómo-se-valida-antes-de-aplicarla).

---

## Estructura

```
contact-center-ai/
├── backend/
│   ├── main.py              # app FastAPI (routers, CORS, /health/)
│   ├── run_scheduler.py     # scheduler (proceso aparte)
│   ├── Auditor.py           # orquestador de auditorías
│   ├── chatBot.py           # motor de los chatbots RAG
│   ├── reindex_all.py       # reindexado de chatbots por consola
│   ├── AuditorIA/           # auditoría: builders SQL, Gemini, lotes, descargas por plataforma (downloads/)
│   ├── app/                 # routers/, config.py, seguridad, RBAC y módulos de negocio (planificador_*, cuotas…)
│   ├── storage/             # audios conservados, lotes en espera, trabajos (fuera de git)
│   └── tests/
├── frontend/
│   ├── run.py
│   └── app/                 # routes/ (blueprints), templates/, static/, utils/ (api_client, menu_config)
├── scripts/                 # crons, recuperación de lotes, evaluación, utilitarios
│   └── migrations/          # SQL de las migraciones
├── docs/
├── docker-compose.qdrant.yml
├── requirements.txt         # un solo archivo para backend y frontend
├── Makefile
└── .env.example
```
