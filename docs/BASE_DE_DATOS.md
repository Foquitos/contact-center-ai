# Base de datos

La base del sistema es **SQL Server `10.0.0.11`, base `Acme`**. Este documento explica cómo está organizada,
qué escribe el sistema y qué cargan otros, y cómo se hace un cambio de esquema sin romper producción.

Quién administra la base: el **equipo de Reporting del área de Operaciones**, que es también el DBA. Respalda la
base, tiene los permisos de escritura y DDL y mantiene las cargas externas.

---

## Reglas que no se ven en el código

1. **Dev y prod usan la misma base.** Lo que se escribe desde SRV00 (dev) lo ve producción. Por eso las
   tablas de trabajo llevan la columna `Entorno` (ver [más abajo](#la-columna-entorno)).
2. **La fuente de verdad del esquema es la base.** El repo tiene una foto del esquema al 23/09/2026 en
   `scripts/esquema/` (ver [Esquema versionado](#esquema-versionado)) y las migraciones en
   `scripts/migrations/`, las dos en git desde el traspaso. Si la foto y la base difieren, manda la base.
3. **No hay tabla de control de migraciones.** Para saber si una migración está aplicada hay que buscar lo que
   crea (una tabla, una columna, un dato).
4. **La aplicación nunca hace DDL.** Todo cambio de esquema se entrega como migración y se aplica **antes** del
   deploy del código que la usa.
5. **Consultar producción con cuidado.** Ventanas cortas (días, no meses) y sin repetir una consulta pesada
   ampliando el rango: un cruce de 365 días terminó una vez en un deadlock.

---

## Esquemas

| Esquema | Qué guarda | Tablas / vistas / SPs |
|---|---|---|
| `calidad` | Auditorías, plantillas, Golden Set, colas de ejecución, cupos, dashboard | 35 / 2 / 18 |
| `pagina_web` | Usuarios y permisos (RBAC), chatbots, uso y costo de IA, tips, login | 32 / 6 / 0 |
| `planificacion` | Planificador: configuración, corridas, pronósticos, insumos | 20 / 0 / 0 |
| `dbo` | Mayormente **datos que cargan otros** (nómina, payroll, informes de plataformas) y el IVR de Enerval | 169 / 23 / 13 |
| `orion` | Réplica de Orion/Vantix, cargada por SPs desde los linked servers | 14 / 1 / 5 |
| `Benefix` | Llamados de Benefix traídos de Genesys | 2 / 0 / 0 |
| `chatbot` | 18 vistas del chatbot SQL de gerencia. **En desuso**: el chatbot SQL se discontinuó | 0 / 18 / 0 |
| `Farmalux`, `ALARMIX`, `Hidra`, `CSV`, `RRHH` | Datos de cliente (réplicas) y RRHH (en desuso) | pocas |

---

## Qué escribe el sistema, por módulo

La columna **Entorno** indica si la tabla lleva `Entorno` (o `environment`).

### Auditorías

| Tabla | Para qué | La escribe | Entorno |
|---|---|---|---|
| `calidad.Auditorias` / `AuditoriaDetalles` | Una fila por auditoría y una por atributo auditado | `AuditorIA/sql_a_Claude.py`, `AuditorIA/reauditoria.py` | no |
| `calidad.AuditoriaVersiones` / `AuditoriaVersionDetalles` | Historial de reauditorías | `AuditorIA/reauditoria.py` | no |
| `calidad.transcripciones` | Transcripción de cada interacción | `AuditorIA/Trancribir.py`, `AuditorIA/transcripcion_cola.py` | no |
| `calidad.Empresas`, `Campanas`, `Plantillas`, `Atributos`, `Skills` | Catálogo de plantillas | SPs `calidad.sp_*` que llama `AuditorIA/Plantillas_prompts.py` | no |
| `calidad.PlantillaVersiones` | Versión del prompt con la que se hizo cada auditoría | `AuditorIA/versionado.py` | no |
| `calidad.PlantillaConocimiento` | Documentos de chatbot que lee la IA auditora | `AuditorIA/conocimiento_plantilla.py` | no |
| `calidad.AuditSchedulers` / `AuditSchedulerHistory` | Tareas programadas y cada ejecución | `routers/auditoria.py`, `app/tasks.py` | sí / no |
| `calidad.AuditTasks` | Auditorías interactivas en curso | `routers/auditoria.py`, `Auditor.py`, `app/tasks.py` | sí |
| `calidad.BatchJobs` | Lotes enviados a Gemini y su estado | `Auditor.py`, `AuditorIA/batch_cola.py` | sí |
| `calidad.BatchPendientes` | Lotes que esperan cupo en Gemini | `AuditorIA/batch_cola.py` | sí |
| `calidad.Batch_data` | Metadata de cada llamado de un lote. Se vacía cuando el lote se procesó (ver abajo) | `AuditorIA/batch_cola.py` | no |
| `calidad.TranscripcionJobs` | Cola de transcripciones a demanda | `AuditorIA/transcripcion_cola.py` | sí |
| `calidad.AudioAuditoria` | Índice de los audios conservados en disco | `AuditorIA/audio_store.py` | sí |
| `calidad.AuditExecutionLog` | Log unificado de corridas (cuántos, costo, errores) | `AuditorIA/execution_log.py` | no |
| `calidad.AuditColumnTemplates` | Columnas que se exportan a Sheets | `routers/auditoria.py` | no |
| `calidad.interacciones_CSV` | Interacciones de CSV (empresa 10) | `AuditorIA/Cardnet.py` | no |
| `calidad.CuotaCampana` / `CuotaConsumo` | Cupos de auditoría por campaña y su consumo | `app/cuotas.py` | no |
| `calidad.AuditoriaRevisiones` / `AuditoriaRevisionDetalles` | Corrección humana de una auditoría (no pisa la auditoría) | `AuditorIA/revision.py` | no |
| `calidad.GoldenSets` / `GoldenSetItems` | Muestras de referencia para medir la IA | `AuditorIA/golden_set.py` | no |
| `calidad.BandejaDashboard` | Configuración de los dashboards | `routers/bandeja.py` | no |
| `calidad.AsistenteConversaciones` / `AsistenteMensajes` | Historial del asistente del dashboard | `app/asistente_conversaciones.py` | no |

El listado de auditorías de todas las pantallas sale de **`calidad.sp_ObtenerAuditoriasFiltradas`**, y la
persona a la que se atribuye cada auditoría, de la función **`calidad.fn_ResolverOperadorAuditoria`** (ver
[DECISIONES.md](DECISIONES.md)).

### Chatbots

| Tabla | Para qué | La escribe | Entorno |
|---|---|---|---|
| `pagina_web.Chatbots`, `ChatbotPcrc` | Definición de cada bot y su ruteo por PCRC | `routers/chatbot_admin.py` | no |
| `pagina_web.ChatbotDocMarkdown`, `ChatbotDocVinculo`, `ChatbotDocMaterial` | Conocimiento en markdown, documentos compartidos entre bots, material pendiente | `routers/chatbot_admin.py`, `app/doc_material.py` | no |
| `pagina_web.ChatbotImagenes` | Imágenes de soporte visual | `app/chatbot_imagenes.py` | no |
| `pagina_web.ChatbotTabla` / `ChatbotTablaFila` | Tablas de datos de los bots | `app/chatbot_tablas_admin.py` | no |
| `pagina_web.ChatbotIndexJobs` | Cola de reindexado | `app/chatbot_indexer.py`, `reindex_all.py` | sí |
| `pagina_web.ChatbotDocJobs` | Cola del asistente de documentación | `app/doc_jobs.py` | sí |
| `pagina_web.ChatbotIndexState` | Versión de índice activa de cada bot **en cada servidor** (Qdrant es local) | `app/chatbot_indexer.py` | sí |
| `pagina_web.query_chatbots_logs` | Cada pregunta y respuesta | `chatBot.py`, `app/chat_history.py` | no |
| `pagina_web.ChatbotVacios` | Temas que el bot no pudo responder | `app/vacios_conocimiento.py`, `routers/vacios.py` | no |
| `pagina_web.chatbot_calificaciones` | Calificación de las respuestas | `routers/chatbot.py` | no |

### Usuarios, permisos y uso de IA

| Tabla | Para qué | La escribe |
|---|---|---|
| `pagina_web.passwords` | Contraseña (hash) de cada usuario y si debe cambiarla | `routers/users.py` |
| `pagina_web.Roles`, `RolePermissions`, `UserRoles`, `Permissions` | RBAC. La vista `RoleEffectivePermissions` resuelve la herencia de roles | `routers/roles.py`, `routers/users.py` |
| `pagina_web.RbacAuditLog` | Quién cambió qué rol o permiso (hora local) | `app/rbac.py` |
| `pagina_web.LoginAudit` | Ingresos, salidas e intentos fallidos | `app/session_log.py` |
| `pagina_web.Usuarios_extra` | Usuarios que no están en la nómina (ver [alta](#alta-de-un-usuario-que-no-está-en-la-nómina)) | **A mano por SQL** |
| `pagina_web.IA_Uso` | Una fila por llamada a la IA (tokens, modelo, modo) | `app/uso_ia.py` |
| `pagina_web.IA_Precios` | Precio de cada modelo con vigencia por fecha. La vista `vw_IA_Uso_Costos` cruza uso y precio | **Por migración** |
| `pagina_web.IA_Presupuesto` / `IA_Presupuesto_Alertas` | Presupuesto mensual, umbrales y avisos ya enviados | `app/presupuesto_ia.py` |
| `pagina_web.Tips`, `TipGroups`, `TipGroupRoles`, `TipFeedback` | Tips del Día por rol | `routers/tips.py` |

Quién puede ingresar al sistema: las personas de la nómina (`dbo.nomina`, carga externa) y las de
`pagina_web.Usuarios_extra`. La contraseña y los roles se asignan desde la pantalla de usuarios.

### Planificador

Todas las tablas `planificacion.*` las escribe el backend (`app/planificador_datos.py` y módulos vecinos) o los
crons del planificador. Ninguna lleva `Entorno`: el recálculo automático corre solo en SRV01. Detalle de cada
tabla en [PLANIFICADOR.md](PLANIFICADOR.md). `planificacion.PoolRefuerzo` y `PoolSubCampana` se cargan **por
migración**.

### Ingestas propias

| Tabla | Qué es | La escribe |
|---|---|---|
| `dbo.[Voltara Enerval informe IVR]` + `…cargas` | Informe IVR de Enerval por día | `scripts/enerval_informe_ivr.py` (borra el día e inserta) |
| `Benefix.Interacciones` + `InteraccionesCargas` | Llamados de Benefix desde Genesys | `scripts/benefix_genesys.py` (borra el día e inserta) |
| `planificacion.CorteEnre` | Usuarios sin luz según el ENRE | `scripts/cortes_enre.py` |
| `planificacion.CorteEnreTipo`, `CorteEnreComunicado` | Usuarios sin luz separados por tipo de corte, y los cortes programados anunciados | `scripts/cortes_enre.py` |
| `planificacion.AvisoCorte` | Avisos de corte de Hidra, lectura con IA y ajuste propuesto | `scripts/cortes_hidra.py` (la pantalla del planificador lo aplica o descarta) |
| `planificacion.Clima` | Clima observado y pronóstico | `scripts/clima_voltara.py` |

Las tablas `…Cargas` registran cada corrida (fecha, estado, filas): son el primer lugar donde mirar si una
ingesta falló.

---

## Qué cargan otros

El sistema lee estas tablas pero **ningún código del repo las escribe**. Si dejan de actualizarse, el sistema
sigue funcionando con datos viejos y no avisa (salvo la pantalla de Salud del planificador).

| Tabla | Qué es | Qué depende de ella | Responsable |
|---|---|---|---|
| `dbo.nomina`, `dbo.operadores`, `dbo.usuarios`, `dbo.campanas`, `dbo.puestos` | Personas, legajos, campañas | **Login**, atribución de auditorías, RRHH, planificador | Reporting |
| `dbo.payroll`, `dbo.payroll_futuro` | Turnos registrados y malla futura | Planificador | Reporting |
| `dbo.Feriados`, `dbo.Intervalos`, `dbo.Ausentismo` | Calendario y ausentismo | Planificador | Reporting |
| `dbo.[Voltara Enerval informe skills]`, `dbo.[Voltara Enerval informe agente]`, vistas `dbo.Tablero_Agentes_Voltara` y `dbo.TMO_Voltara` | Llamadas por skill y media hora, logueo por agente | Planificador de Voltara | Reporting |
| `dbo.Acumuladores_de_campana`, `dbo.detalle_de_interacciones_por_agente`, `dbo.acumuladores_de_agentes_por_skill`, `dbo.detalle_de_interacciones_por_campana_lote` | Mitrol (Hidra Técnico y otras) | Planificador de Hidra, selección de llamados de Mitrol | Reporting |
| `dbo.Forecast` | Pronóstico que manda el cliente. **Se carga a mano** | Planificador (domingos y feriados) | Reporting |
| `dbo.[ALARMIX Detalle interacciones]`, `ALARMIX.Grabaciones` | Interacciones y grabaciones de ALARMIX | Auditorías de ALARMIX | Reporting |
| `Farmalux.SalesForce_casos`, `Farmalux.Grabaciones` | Casos y grabaciones de Farmalux | Auditorías de Farmalux | Reporting |
| `dbo.Voltara_Salesforce_casos_cerrados`, `…_casos_calidad` | Casos de Salesforce de Voltara | Auditorías de Voltara | Reporting |
| `Hidra.interacciones`, `dbo.Sar_Ingresos` | Hidra | Auditorías de Hidra | Reporting |
| `dbo.Odonto_Plus_Turnero`, `dbo.[Aurora Salud Turnos]`, `dbo.Vitalis_Llamadas` | Turneros y llamadas de otros clientes | Auditorías de esas campañas | Reporting |
| `orion.*` | Réplica de Orion/Vantix | Auditorías de Vantix | Reporting (SPs `orion.USP_Cargar_Silver_*`, ver abajo) |
| Audios de CSV en el fileserver `10.0.1.30` | No es una tabla: carpetas de audios | Auditoría diaria de CSV | **Operaciones** |

Dónde corre cada carga (SQL Agent, otro servidor): **A confirmar con Reporting**.

### Jobs del SQL Agent que usa el sistema

Estos procedimientos no los llama el código: corren como jobs del SQL Agent. Los armó el desarrollador del
sistema y **quedan a cargo de Reporting (DBA)**. El usuario de solo lectura no ve los jobs (`msdb`), así que
horarios y frecuencia: **A confirmar con Reporting**.

| Procedimiento | Qué hace | Si deja de correr |
|---|---|---|
| `dbo.sp_LimpiarDatosDeBatchFinalizados` | Borra `calidad.Batch_data` de los lotes ya procesados, fallidos, cancelados o vencidos. Nunca borra la de un lote exitoso sin procesar | `Batch_data` crece sin límite. No rompe nada |
| `orion.USP_Cargar_Silver_*` (Encuestas, Llamadas_Detalle, Agente_Auxiliares, Agente_Skill_Logins, Llamadas_Trafico_Colas) | Copian datos de Orion (`ORION_LINK`) y del MySQL de omnicanalidad (`MYSQL_LINK`) a `orion.*` | Las auditorías de Vantix trabajan con datos viejos |

---

## Linked servers

| Nombre | Destino | Para qué |
|---|---|---|
| `ORION_LINK` | SQL Server `10.0.1.171`, base `ContactCenter` (Orion/Vantix) | Selección de llamados de Vantix, skill de cada agente, resúmenes del dashboard |
| `MYSQL_LINK` | MySQL por el DSN `MYSQL_DSN_ORION`, base `omnicanalidad` | Motivos de cierre de las llamadas de Vantix; vista `orion.vw_Llamadas_Rebotadas_Agente` |

- Consultarlos **siempre con `OPENQUERY`**: la consulta se ejecuta en el servidor remoto y vuelve solo el
  resultado. Un `JOIN` directo contra un linked server trae tablas enteras por la red.
- El texto de un `OPENQUERY` admite **como máximo 8.000 caracteres**. El código parte las listas largas de IDs
  en tandas de 7.000 (`AuditorIA/SQL_query.py`).
- Los linked servers los administra Reporting (DBA).

---

## La columna `Entorno`

Como dev y prod comparten la base, **toda tabla de trabajo en ejecución** (una cola, un job, una tarea
programada) lleva la columna `Entorno` (`environment` en `pagina_web`), con el valor `prod` o `dev`:

- El `INSERT` escribe `settings.ENVIRONMENT` (el `ENVIRONMENT` del `.env` de cada servidor).
- El proceso que toma trabajo (el *claim*) filtra por su propio entorno. Si no lo hiciera, el scheduler de
  SRV00 podría ejecutar una tarea de producción, con el código de desarrollo, y mandar sus mails.

Tablas con `Entorno`: `calidad.AuditSchedulers`, `calidad.AuditTasks`, `calidad.BatchJobs`,
`calidad.BatchPendientes`, `calidad.TranscripcionJobs`, `calidad.AudioAuditoria`,
`pagina_web.ChatbotIndexJobs`, `pagina_web.ChatbotDocJobs` y `pagina_web.ChatbotIndexState` (esta última es
estado y no cola: cada servidor tiene su propio Qdrant).

El claim es atómico: un `UPDATE … OUTPUT` que marca la fila como tomada y la devuelve en el mismo paso
(con `UPDLOCK, READPAST` en las colas), así dos procesos no toman el mismo trabajo.

**Contenido y configuración** (plantillas, chatbots, documentos, permisos) **no** llevan `Entorno`: son
compartidos a propósito. Un cambio de plantilla hecho en dev se ve en prod al instante.

**Al crear una tabla de ejecución nueva:** agregar `Entorno NVARCHAR(20) NOT NULL`, escribirlo en cada
`INSERT`, filtrarlo en cada claim y sumar la tabla a `backend/tests/test_entorno_colas_sql.py`. Ese test
también obliga a clasificar cada job nuevo del scheduler como "cola por entorno" o "solo prod". Por qué no se
usó Redis u otra cola: [DECISIONES.md](DECISIONES.md).

---

## Migraciones

### Cómo se escribe una

Archivo `scripts/migrations/AAAA-MM-DD[letra]_descripcion.sql` (la letra ordena varias del mismo día). Reglas:

- **Aditiva:** agrega tablas, columnas, índices o datos. No borra ni renombra: el código anterior tiene que
  seguir funcionando con la base nueva (es lo que permite deployar después y volver atrás).
- **Idempotente:** correrla dos veces no rompe nada. Cada paso va protegido:

  ```sql
  IF COL_LENGTH('calidad.BatchJobs', 'Entorno') IS NULL
      ALTER TABLE calidad.BatchJobs
          ADD Entorno NVARCHAR(20) NOT NULL
          CONSTRAINT DF_BatchJobs_Entorno DEFAULT 'prod' WITH VALUES;
  GO

  IF OBJECT_ID('calidad.BatchPendientes', 'U') IS NULL
  BEGIN
      CREATE TABLE calidad.BatchPendientes ( ... );
  END
  GO

  IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions WHERE code = 'audit:sync')
      INSERT INTO pagina_web.Permissions (code, ...) VALUES ('audit:sync', ...);
  ```

- **Encabezado** con qué agrega, por qué y cómo correrla (el formato de las migraciones existentes).
- `SET XACT_ABORT ON;` al principio, para que un error corte la transacción entera.
- Constraints **con nombre** (`DF_<Tabla>_<columna>`) y `NOT NULL` explícito.
- Las columnas de una `PRIMARY KEY` tienen que decir **`NOT NULL`** explícito: si no, SQL Server da el error
  8111 al crear la tabla, y `PARSEONLY` no lo detecta.
- **Los permisos nuevos nacen sin asignar**: se insertan en `pagina_web.Permissions` y el `INSERT` en
  `RolePermissions` queda comentado. Se asignan después desde la pantalla de roles.
- Vistas con `CREATE OR ALTER VIEW`.
- Una migración ya aplicada **no se edita**: se escribe otra.

### Trampa: `UPDATE … FROM cte`

Un `UPDATE` sobre un CTE solo puede escribir las columnas que el CTE expone. Si el `SELECT` del CTE no incluye
la columna que se actualiza, falla **en ejecución**, no al compilar. Esto tiró abajo producción el 25/08/2026.

### Cómo se valida antes de aplicarla

Hay dos herramientas, y no son equivalentes:

| Herramienta | Qué revisa | Cuándo usarla |
|---|---|---|
| Fixture `validar_sql` (`backend/conftest.py`) | Sintaxis **y** que las tablas y columnas existan (usa `sys.dm_exec_describe_first_result_set`, que describe el resultado sin ejecutar la consulta) | Toda consulta sobre tablas que ya existen |
| `SET PARSEONLY ON` | Solo sintaxis | DDL de tablas que todavía no existen, tablas temporales (`#tmp`) y consultas por `ORION_LINK` |

`validar_sql` necesita el `.env` y conexión a la base (no escribe nada). Cae solo a `PARSEONLY` cuando la
consulta tiene `#` u `ORION_LINK`. Los tests que lo usan siguen este patrón (ver
`backend/tests/test_batch_cola_sql.py`):

```python
def test_las_consultas_bindean(validar_sql):
    resultado = validar_sql("SELECT name, status FROM calidad.BatchJobs WHERE Entorno = 'prod'")
    assert resultado.ok, resultado.error
```

Tests que ya revisan migraciones:

- `backend/tests/test_migrations_vistas_sql.py`: valida el cuerpo de cada vista de `scripts/migrations/`.
- `backend/tests/test_planificador_migracion_sql.py`: sintaxis de cada bloque, `NOT NULL` en las columnas de
  PK e idempotencia de la siembra.
- `backend/tests/test_planificador_salud.py`: falla si hay una migración `*planificador*.sql` que no está
  registrada en `planificador_salud.REGISTRO_MIGRACIONES` (el chequeo que alimenta la pantalla de Salud del
  planificador).

Para correrlos: `scripts/correr_tests.sh tests/test_migrations_vistas_sql.py`. Si la carpeta de migraciones no
está en la máquina, se saltean.

### Cómo se aplica

Ver OPERACION.md → Aplicar una migración.

### Esquema versionado

`scripts/esquema/` tiene un archivo `.sql` por objeto de la base `Acme` (tablas, vistas, procedimientos,
funciones y esquemas), generado con SSMS el 23/09/2026. Sirve para reconstruir la base, para ver una tabla sin
consultar producción y para comparar con `git diff` qué cambió entre dos fotos.

No incluye: datos, usuarios y roles de la base (los archivos `*.User.sql` y `*.ApplicationRole.sql` quedan
fuera de git porque llevan nombres de personas), linked servers ni jobs del SQL Agent (son del servidor, los
administra Reporting). Una migración (`2026-09-02d_vantix_bases_terceras.sql`) también queda fuera de git
porque trae datos de contacto del personal de un cliente; está solo en SRV00.

**Cómo regenerarlo** (después de aplicar migraciones, para que la foto no quede vieja):

1. En SSMS, clic derecho sobre la base `Acme` → **Tasks → Generate Scripts…** → "Script entire database and
   all database objects".
2. **Advanced:** *Types of data to script* = Schema only; *Script Indexes*, *Triggers*, *Check Constraints*,
   *Foreign Keys*, *Primary Keys*, *Unique Keys* = True; *Script USE DATABASE* = False; *Script Logins* y
   *Script Object-Level Permissions* = False; *Include IF NOT EXISTS* = True.
3. **Output:** "One script file per object", sobre `scripts/esquema/`.
4. SSMS guarda en UTF-16; pasarlo a UTF-8 para que git muestre las diferencias:

   ```bash
   cd scripts/esquema
   for f in *.sql; do
     if file -b "$f" | grep -q UTF-16; then
       iconv -f UTF-16LE -t UTF-8 "$f" | sed '1s/^\xEF\xBB\xBF//; s/\r$//' > "$f.tmp" && mv "$f.tmp" "$f"
     fi
   done
   ```

5. Revisar con `git diff --stat scripts/esquema` que lo que cambió sea lo que se migró, y commitear.

### Migraciones pendientes al 23/09

Relevamiento del 23/09/2026 sobre las 127 migraciones de la carpeta (del 20/06 al 22/09): 116 aplicadas,
3 pendientes, 1 a revisar y 2 probables. La del 23/09 (`2026-09-23_planificador_hidra_mejoras.sql`) está
aplicada.

| Migración | Estado | Cómo verificarlo |
|---|---|---|
| `2026-07-10_idx_alarmix_grabaciones_segmentid.sql` | Pendiente: `ALARMIX.Grabaciones` no tiene índices | `SELECT name FROM sys.indexes WHERE object_id = OBJECT_ID('ALARMIX.Grabaciones') AND name IS NOT NULL;` |
| `2026-07-22_vantix_motivo_finalizacion_backfill.sql` | Pendiente (probable): 0 de 170 auditorías de Vantix del 15 al 21/07 tienen "Motivo de finalización" | Leer el archivo y buscar las auditorías que completa |
| `2026-09-17_discurso_dental_chat_ortografia_mayusculas.sql` | Pendiente: el atributo 285 (plantilla 29) sigue con el texto viejo | Comparar el prompt del atributo 285 con el del archivo |
| `2026-07-30b_reparar_operador_e_idaplicativo.sql` | Revisar: quedan 51 auditorías de ALARMIX de julio con `IdAplicativo = 'nan_nan'` | `SELECT COUNT(*) FROM calidad.Auditorias WHERE IdAplicativo = 'nan_nan';` |

La lista se mantiene en TRASPASO.md §6 junto al resto de los
pendientes.

---

## Tareas frecuentes

### Alta de un usuario que no está en la nómina

Las personas que no están en `dbo.nomina` (por ejemplo, usuarios de un cliente) se agregan a mano en
`pagina_web.Usuarios_extra`, con un usuario con permiso de escritura:

```sql
INSERT INTO pagina_web.Usuarios_extra (documento, nombre, apellido, campana)
VALUES (12345678, 'Nombre', 'Apellido', 'Voltara');
```

Después se le crea la contraseña y se le asignan roles desde *Gestionar Usuarios*, como a cualquier otro. Para
darlo de baja, quitarle los roles desde la pantalla y borrar la fila.

### Consultas útiles

```sql
-- Últimas corridas de auditoría (cualquier origen)
SELECT TOP 20 * FROM calidad.AuditExecutionLog ORDER BY 1 DESC;

-- Colas con trabajo pendiente en prod
SELECT 'BatchPendientes' cola, COUNT(*) FROM calidad.BatchPendientes WHERE Entorno = 'prod' AND Estado = 'PENDIENTE'
UNION ALL
SELECT 'TranscripcionJobs', COUNT(*) FROM calidad.TranscripcionJobs WHERE Entorno = 'prod' AND Estado = 'PENDIENTE'
UNION ALL
SELECT 'ChatbotIndexJobs', COUNT(*) FROM pagina_web.ChatbotIndexJobs WHERE environment = 'prod' AND status = 'pending';

-- Estado de las ingestas propias
SELECT TOP 10 * FROM Benefix.InteraccionesCargas ORDER BY 1 DESC;
```

### Consultar sin romper producción

- Filtrar siempre por fecha, con ventanas de días.
- `calidad.AuditoriaDetalles` tiene una fila por atributo y por auditoría: millones de filas. Nunca sin
  filtro.
- No usar `WITH (NOLOCK)` para decidir nada importante (puede leer datos a medio escribir); sí sirve para
  mirar.
- El usuario de solo lectura no tiene permiso sobre las vistas de estado del servidor (DMVs) ni sobre `msdb`.

### Datos personales

Algunas plantillas (Farmalux y Vantix Calidad Turnos) extraen DNI, teléfono, mail, documento y patente, que
quedan en `calidad.AuditoriaDetalles`. Tenerlo en cuenta antes de compartir exportaciones.
