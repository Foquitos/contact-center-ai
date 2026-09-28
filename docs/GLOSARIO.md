# Glosario

Términos del negocio y del sistema, una línea cada uno, con el documento donde se amplían.

## Negocio (contact center)

| Término | Qué es | Más |
|---|---|---|
| **Empresa** | El cliente (Voltara, Hidra, ALARMIX, Vantix, CSV…). En la base, `calidad.Empresas` | [BASE_DE_DATOS](BASE_DE_DATOS.md) |
| **Campaña** | Una operación de un cliente (por ejemplo Hidra Técnico y Hidra Comercial son dos campañas de Hidra) | [ARQUITECTURA](ARQUITECTURA.md) |
| **CSV** | La empresa de Cardnet (empresa 10), no el formato de archivo. Se audita a partir de carpetas de audios que deja Operaciones en el fileserver | INTEGRACIONES |
| **Operador** | La persona que atiende. En las plataformas figura con un usuario que puede ser reciclado entre personas | [DECISIONES](DECISIONES.md#la-persona-auditada-se-congela-al-auditar) |
| **Interacción / llamado** | Una llamada o un chat. Puede tener varios **segmentos** si pasó por varios operadores | [ARQUITECTURA](ARQUITECTURA.md#1-auditoría-de-calidad-auditoria) |
| **UCID** | Identificador de llamada de Avaya/Verint que usan las auditorías de CSV | INTEGRACIONES |
| **Tipificación** | Cómo el operador clasificó la gestión al cerrarla | [ARQUITECTURA](ARQUITECTURA.md) |
| **Skill** | Una cola de atención con su propio número de llamadas y objetivo (Emergencias, Reclamos…) | [PLANIFICADOR](PLANIFICADOR.md) |
| **PCRC** | Código de la campaña de un operador de CSV; sirve para elegirle el chatbot que corresponde | [ARQUITECTURA](ARQUITECTURA.md#2-chatbot-rag) |
| **NDS (nivel de servicio)** | Porcentaje de llamadas atendidas antes de un umbral de espera (Voltara: 80% en 30 s sobre las entrantes) | [ESTADISTICA](ESTADISTICA.md#nivel-de-servicio-nds-y-erlang-c) |
| **TMO** | Tiempo medio de operación: cuánto dura en promedio atender una llamada | [ESTADISTICA](ESTADISTICA.md#tráfico-erlangs) |
| **Abandono** | Llamada que corta antes de ser atendida | [ESTADISTICA](ESTADISTICA.md#abandono-paciencia-y-erlang-a) |
| **Paciencia** | Cuánto espera en promedio un cliente antes de cortar | [ESTADISTICA](ESTADISTICA.md#abandono-paciencia-y-erlang-a) |
| **Ocupación** | Fracción del tiempo logueado que un operador pasa atendiendo (techo: 85%) | [ESTADISTICA](ESTADISTICA.md#techo-de-ocupación) |
| **Erlang** | Unidad de tráfico: 1 erlang = un operador ocupado toda la media hora. También las fórmulas (Erlang B, C y A) para calcular dotación | [ESTADISTICA](ESTADISTICA.md#tráfico-erlangs) |
| **En línea** | Operadores que tienen que estar atendiendo en una media hora | [ESTADISTICA](ESTADISTICA.md#cadena-de-descuentos-de-en-línea-a-a-citar) |
| **A citar** | Operadores que hay que convocar para tener los "en línea", después de disponibilidad, shrinkage y break | [ESTADISTICA](ESTADISTICA.md#cadena-de-descuentos-de-en-línea-a-a-citar) |
| **Disponibilidad** | Fracción del tiempo en el puesto en que el operador está disponible para atender | [ESTADISTICA](ESTADISTICA.md#cadena-de-descuentos-de-en-línea-a-a-citar) |
| **Shrinkage** | Porcentaje de la gente citada que no está (ausencias, capacitación) | [ESTADISTICA](ESTADISTICA.md#cadena-de-descuentos-de-en-línea-a-a-citar) |
| **Break** | Minutos de descanso por hora | [ESTADISTICA](ESTADISTICA.md#cadena-de-descuentos-de-en-línea-a-a-citar) |
| **Malla** | La grilla de turnos publicada a futuro (`dbo.payroll_futuro`) | [PLANIFICADOR](PLANIFICADOR.md#fuentes-de-datos) |
| **Payroll** | El registro de turnos y asistencia (`dbo.payroll` para el pasado, `payroll_futuro` para la malla) | [PLANIFICADOR](PLANIFICADOR.md#fuentes-de-datos) |
| **Nómina** | El padrón de personas (`dbo.nomina`), que carga Reporting. Define quién puede ingresar al sistema | [BASE_DE_DATOS](BASE_DE_DATOS.md#qué-cargan-otros) |
| **Pool** | Grupo de skills que atiende la misma gente y se dimensiona junto | [PLANIFICADOR](PLANIFICADOR.md#tablas-planificacion) |
| **Sub-campaña** | Agrupación de RRHH de la gente (T1-Teléfono, Digital…); define qué gente cuenta en cada pool | [PLANIFICADOR](PLANIFICADOR.md#tablas-planificacion) |
| **Brecha** | Diferencia entre la gente a citar y la citada en la malla | [PLANIFICADOR](PLANIFICADOR.md) |
| **Refuerzo** | Gente extra para cubrir una brecha: mover la malla, horas extra, convocatoria o Digital, según la antelación | [PLANIFICADOR](PLANIFICADOR.md) |
| **BPO-04** | Cómo figura Acme en el informe IVR de Enerval, que trae a todos los proveedores de Voltara | [PLANIFICADOR](PLANIFICADOR.md#cómo-funciona) |
| **Reporting** | Equipo del área de Operaciones que administra SQL Server (DBA) y carga las tablas externas | [BASE_DE_DATOS](BASE_DE_DATOS.md) |

## Auditoría de calidad

| Término | Qué es | Más |
|---|---|---|
| **Plantilla** | El formulario de evaluación de una campaña: una lista de atributos con sus consignas para la IA | [ARQUITECTURA](ARQUITECTURA.md#plantillas-y-prompts-tagsplantillas) |
| **Atributo** | Una pregunta de la plantilla ("¿saludó?", "tipología"), con su tipo de respuesta | [ARQUITECTURA](ARQUITECTURA.md#plantillas-y-prompts-tagsplantillas) |
| **`critical_audit`** | Tipo de atributo que puntúa: la IA responde OK, NO OK, EC o N/A | [ESTADISTICA](ESTADISTICA.md#puntaje-error-crítico-y-falsos-ec) |
| **Ponderación** | El peso de un atributo en el puntaje | [ESTADISTICA](ESTADISTICA.md#puntaje-error-crítico-y-falsos-ec) |
| **Puntaje** | Nota de 0 a 100 del llamado, calculada con los pesos; se guarda al auditar | [ESTADISTICA](ESTADISTICA.md#puntaje-error-crítico-y-falsos-ec) |
| **Error crítico (EC)** | Falta grave: deja el puntaje del llamado en 0 y dispara una alerta de calidad | [ESTADISTICA](ESTADISTICA.md#puntaje-error-crítico-y-falsos-ec) |
| **N/A** | "No aplica": el atributo sale de la cuenta y los demás pesos se renormalizan | [ESTADISTICA](ESTADISTICA.md#puntaje-error-crítico-y-falsos-ec) |
| **Atributo opcional** | Atributo que la IA puede dejar sin responder | [ARQUITECTURA](ARQUITECTURA.md) |
| **IdAplicativo** | Clave única de la interacción que une auditoría, transcripción y deduplicación; se calcula solo en `calcular_id_aplicativo` | [DECISIONES](DECISIONES.md#idaplicativo-se-calcula-en-un-solo-lugar) |
| **Incidencia** | Llamado que no se pudo auditar (audio mudo, operador que no coincide): se guarda sin puntaje y no promedia | [DECISIONES](DECISIONES.md#incidencia-la-auditoría-que-no-se-puede-hacer) |
| **Modo sincrónico** | "Auditar ahora": resultado en el momento, al doble de precio. Exige `audit:sync` | [DECISIONES](DECISIONES.md#batch-por-defecto-sincrónico-con-permiso-propio) |
| **Lote / Batch** | Modo por defecto: los llamados viajan a Gemini en lotes de hasta 250 y vuelven en horas, a mitad de precio | [ARQUITECTURA](ARQUITECTURA.md#cómo-viaja-un-lote-y-qué-pasa-cuando-no-hay-cupo-2026-08-26) |
| **Corrida** | Una ejecución de auditoría (`run_id`); puede tener varios lotes. Queda en `calidad.AuditExecutionLog` | [ARQUITECTURA](ARQUITECTURA.md) |
| **Tarea programada** | Auditoría que se repite sola (`calidad.AuditSchedulers`) | OPERACION |
| **Cupo** | Tope mensual de auditorías por campaña | [DECISIONES](DECISIONES.md#cupo-mensual-por-campaña) |
| **Audio conservado** | Copia comprimida del audio auditado, guardada en el servidor para escucharlo y reusarlo | [DECISIONES](DECISIONES.md#audio-conservado-en-disco-índice-en-la-base) |
| **Transcripción a demanda** | Pedir solo la transcripción de un llamado ya auditado | [DECISIONES](DECISIONES.md#transcribir-después-flex-o-batch-según-el-tamaño-del-pedido) |
| **Reauditar** | Volver a auditar un llamado con la plantilla actual; la versión anterior se archiva | [ARQUITECTURA](ARQUITECTURA.md) |
| **Revisión** | Corrección humana de una auditoría, guardada aparte | [ESTADISTICA](ESTADISTICA.md#parte-1--cuánto-le-acierta-la-ia-a-un-auditor) |
| **Golden Set** | Muestra congelada de llamados revisados para medir una plantilla | [ESTADISTICA](ESTADISTICA.md#muestreo-por-celda-cuántos-llamados-revisar) |
| **Kappa (κ)** | Medida de acuerdo entre la IA y el auditor que descuenta el acuerdo por azar | [ESTADISTICA](ESTADISTICA.md#kappa-de-cohen-κ) |
| **Falso EC** | La IA marcó Error Crítico y el auditor no | [ESTADISTICA](ESTADISTICA.md#puntaje-error-crítico-y-falsos-ec) |
| **Semáforo de plantillas** | Señales por atributo del reporte semanal (no discrimina, desacuerdo humano…) | [ESTADISTICA](ESTADISTICA.md#semáforo-de-plantillas) |
| **Caché de contexto** | El bloque fijo de una plantilla guardado en Gemini para no mandarlo en cada llamado | [DECISIONES](DECISIONES.md#caché-de-contexto-por-contenido) |
| **Nivel de razonamiento** | Cuánto "piensa" el modelo antes de responder (LOW, MEDIUM, HIGH); se elige por plantilla | [DECISIONES](DECISIONES.md#nivel-de-razonamiento-en-vez-de-presupuesto) |

## Chatbots

| Término | Qué es | Más |
|---|---|---|
| **Chatbot RAG** | Bot que responde con la documentación de una campaña: busca los pedazos relevantes y el modelo redacta | [ARQUITECTURA](ARQUITECTURA.md#2-chatbot-rag) |
| **Slug** | Nombre corto de un bot (`voltara`, `vantix`); da nombre a su permiso `chatbot:<slug>` y a su colección | [ARQUITECTURA](ARQUITECTURA.md#2-chatbot-rag) |
| **Índice** | Las dos mitades que usa un bot para buscar: la colección de Qdrant y el `docstore.json` en disco | OPERACION |
| **Reindexar** | Reconstruir el índice de un bot después de cambiar su documentación. Es a demanda | [DECISIONES](DECISIONES.md#reindexado-a-demanda) |
| **Qdrant** | Base de datos vectorial local de cada servidor, donde viven los índices | INTEGRACIONES |
| **Reranker** | Modelo que reordena los pedazos encontrados según cuánto se parecen a la pregunta; su puntaje es el "score" | [CONFIGURACION](CONFIGURACION.md#chatbots-rag) |
| **Vacío de conocimiento** | Consulta que el bot no pudo responder; se agrupan para que Calidad complete la documentación | [ARQUITECTURA](ARQUITECTURA.md#2-chatbot-rag) |
| **Desambiguación** | Ofrecer temas para elegir en vez de responder "no encontré" | [DECISIONES](DECISIONES.md#desambiguación-en-vez-de-no-encontré) |
| **Tabla de datos** | Listado de entidades de un bot que se consulta por fila, fuera del RAG | [DECISIONES](DECISIONES.md#tablas-de-datos-fuera-del-rag) |
| **Material** | Contenido crudo que Calidad sube para que el asistente lo convierta en documentación | [DECISIONES](DECISIONES.md#material-en-tres-pasos) |
| **Coral** | La burbuja de ayuda que responde sobre el manual de la aplicación | [ARQUITECTURA](ARQUITECTURA.md#5-mero--asistente-del-manual-burbuja-de-ayuda) |

## Sistema

| Término | Qué es | Más |
|---|---|---|
| **SRV00 / SRV01** | Servidor de desarrollo (`10.0.0.13`) y de producción (`10.0.0.14`) | OPERACION |
| **Entorno** | `dev` o `prod`: columna de las tablas de trabajo para que cada servidor tome solo lo suyo | [BASE_DE_DATOS](BASE_DE_DATOS.md#la-columna-entorno) |
| **Solo prod** | Job del scheduler que se registra solo en producción | [DECISIONES](DECISIONES.md#jobs-fijos-del-scheduler-solo-en-prod) |
| **Scheduler** | Proceso aparte que corre las colas y las tareas periódicas | OPERACION |
| **Claim** | Tomar una fila de una cola marcándola como "en curso" en un solo paso, para que dos procesos no tomen la misma | [BASE_DE_DATOS](BASE_DE_DATOS.md#la-columna-entorno) |
| **Migración** | Script SQL aditivo e idempotente que cambia el esquema; se aplica antes del deploy | [BASE_DE_DATOS](BASE_DE_DATOS.md#migraciones) |
| **Idempotente** | Que se puede correr dos veces sin romper nada | [BASE_DE_DATOS](BASE_DE_DATOS.md#cómo-se-escribe-una) |
| **Linked server** | Conexión de SQL Server a otra base (`ORION_LINK`, `MYSQL_LINK`); se consulta con `OPENQUERY` | [BASE_DE_DATOS](BASE_DE_DATOS.md#linked-servers) |
| **RBAC** | Control de acceso por roles: usuarios → roles → permisos | [ARQUITECTURA](ARQUITECTURA.md#autenticación-y-autorización-rbac) |
| **Permiso** | Código de texto (`audit:execute`, `planificador.view`) que habilita una acción; nace sin asignar | [DECISIONES](DECISIONES.md#los-permisos-nuevos-nacen-sin-asignar) |
| **`template:<empresa>`** | Permiso que da alcance sobre una empresa en el módulo de auditorías | [DECISIONES](DECISIONES.md#alcance-por-empresa) |
| **Super admin** | Rol que saltea toda verificación de permisos | [ARQUITECTURA](ARQUITECTURA.md#autenticación-y-autorización-rbac) |
| **Proxy (Flask)** | Ruta del frontend que reenvía un pedido al backend; la de auditoría filtra campos con una lista blanca | [DECISIONES](DECISIONES.md#lista-blanca-en-el-proxy-de-auditoría) |
| **Deploy** | Push a `origin` + `git pull` en SRV01 + reinicio del servicio que cambió | OPERACION |

## Planificador y pronóstico

| Término | Qué es | Más |
|---|---|---|
| **Corrida (del planificador)** | Un recálculo del plan; la última es la **vigente** | [PLANIFICADOR](PLANIFICADOR.md#cómo-funciona) |
| **Línea de base** | Pronóstico a partir del mismo día y hora de las últimas 52 semanas | [ESTADISTICA](ESTADISTICA.md#cómo-se-pronostica) |
| **Backtest** | Rehacer el pronóstico de días pasados para medir cuánto se habría errado | [ESTADISTICA](ESTADISTICA.md#backtest) |
| **WAPE** | Error del pronóstico ponderado por volumen; la métrica principal | [ESTADISTICA](ESTADISTICA.md#wape-mape-y-sesgo) |
| **MAPE** | Error porcentual promedio por media hora | [ESTADISTICA](ESTADISTICA.md#wape-mape-y-sesgo) |
| **Sesgo** | Si el pronóstico tiende a quedarse corto (positivo) o a pasarse (negativo) | [ESTADISTICA](ESTADISTICA.md#wape-mape-y-sesgo) |
| **Antelación** | Con cuántos días de anticipación se hizo el pronóstico | [ESTADISTICA](ESTADISTICA.md#error-esperado-por-antelación) |
| **Salud (del planificador)** | Pantalla que dice qué fuente de datos está atrasada | [PLANIFICADOR](PLANIFICADOR.md#pantalla-de-salud) |
