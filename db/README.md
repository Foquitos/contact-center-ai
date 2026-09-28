# Base de demostración

Un SQL Server 2022 en Docker con el esquema completo de la aplicación y datos **inventados**, para
entrar a la plataforma y recorrerla sin la infraestructura original. No tiene ningún dato real: las
personas, los llamados, las auditorías y las llamadas del planificador los genera `datos_demo.py`.

## Levantarla

Requisitos: Docker (con `docker compose`), el driver ODBC 18 de SQL Server y el venv del proyecto
(`make install`).

```bash
cp .env.example .env      # ya apunta a esta base
make db-demo              # levanta el contenedor y crea la base Acme (2-3 minutos)
make dev                  # backend :8000 + frontend :7000
```

Entrar a http://localhost:7000. Todos los usuarios tienen la clave **`demo1234`**:

| Usuario (documento) | Persona | Rol | Qué ve |
|---|---|---|---|
| `11111111` | Ada Administradora | Super Admin | Todo |
| `22222222` | Carla Calidad | Jefe Calidad | Auditorías, dashboard, plantillas, usuarios, uso de IA |
| `33333333` | Sergio Supervisor | Supervisor Voltara | Dashboard y chatbot de su campaña |
| `44444444` | Olga Operadora | Operador Voltara | Solo el chatbot |
| `55555555` | Pablo Planificador | GTR | Planificador |

`make db-demo-reset` borra la base y la arma de nuevo. Los datos se generan con fechas relativas al día
en que se crea la base: para que el planificador vuelva a tener "hoy" con llamadas, se recrea.

## Qué trae

- **Esquema**: `db/esquema/` (un archivo por tabla, vista, función o procedimiento) más todas las
  migraciones de `scripts/migrations/`, en orden. `crear_base_demo.py` lista las pocas que se omiten
  (`MIGRACIONES_OMITIDAS`) y por qué: dependen de servidores vinculados de la instalación original.
- **Catálogos**: empresas, campañas, permisos, roles y precios de IA (con nombres ficticios).
- **Auditoría**: 3 plantillas (Voltara, Hidra Técnico, Benefix) y ~700 auditorías de los últimos 75
  días, con transcripción, puntaje y errores críticos. Alcanza para el Dashboard, Auditorías
  Realizadas y Uso de IA.
- **Chatbot**: los bots de las migraciones, consultas de ejemplo al bot de Voltara y un documento de
  conocimiento para indexar desde "Administrar chatbots".
- **Planificador**: 440 días de llamadas de Gasur (una fila por llamada), logueos, turnos, clima y el
  forecast del cliente, con la primera corrida ya calculada. Voltara e Hidra quedan inactivas: no
  tienen historia inventada.

## Qué no funciona sin más configuración

- **Todo lo que llama a Gemini** (auditar, chatbots, asistentes): hace falta una clave propia en
  `GEMINI_AUDITORIA_API_KEY` y `GEMINI_CHATBOT_API_KEY`. Para los chatbots, además, Qdrant
  (`docker compose -f docker-compose.qdrant.yml up -d`) y reindexar el bot.
- **Bajar llamados de las plataformas** (Mitrol, Genesys, CXone…): son conectores a sistemas externos.

## Configuración

| Variable | Default | Para qué |
|---|---|---|
| `DEMO_SQL_PORT` | `1433` | Puerto local del contenedor |
| `DEMO_SA_PASSWORD` | `Demo_ContactCenter_2026` | Clave de `sa` (solo para esta base local) |
| `DEMO_SQL_SERVER` | `127.0.0.1` | Dónde corre SQL Server |

Si cambiás el puerto o la clave, cambialos también en `CONNECTION_STRING` del `.env`.

Con la base levantada, la suite completa corre sin saltear nada: `scripts/correr_tests.sh -m "not tokens"`.
