from app.utils.decorators import tiene_acceso_chatbot

# El bloque "Herramientas" (Asistente de Datos, Información CSV, Analizar CV) quedó
# sin uso (2026-08-13), así que se oculta en vez de borrarse: rutas, templates,
# blueprints y permisos siguen intactos. Para volver a mostrarlo alcanza con poner
# esto en False (afecta al menú, a la grilla de la home y al capítulo del manual,
# ver routes/docs.py). Si en el futuro se recupera solo una de las tres pantallas,
# poner el flag en False y marcar "hidden" en los otros dos hijos.
HERRAMIENTAS_OCULTAS = True

# Estructura del menú principal.
#
# Cada item de primer nivel es un link suelto o un dropdown (con "children").
# En los hijos, la clave "group" agrupa visualmente: el template dibuja un
# encabezado + separador cada vez que cambia el grupo. Se agrupa por grupo y no
# con items "divider" sueltos justamente porque los hijos se filtran por
# permiso: si el separador fuera un item propio, un usuario que no ve ningún
# hijo del bloque siguiente quedaría con una línea colgada.
#
# "desc" es la bajada que muestra la grilla de accesos de la home (index.html),
# que se arma con este mismo menú ya filtrado por permisos.
MENU_STRUCTURE = [
    {
        # Fuera del dropdown a propósito: es la pantalla más usada por el
        # operador y no conviene dejarla a dos clics.
        "label": "Consultar",
        "endpoint": "chatbot.query",
        "icon": "bi-chat-left-text",
        "desc": "Preguntale a la IA sobre la base de conocimiento de tu campaña.",
        # Visible con cualquier permiso de bot (chatbot:<slug>/chatbot:csv) o
        # chatbot:admin; chatbot:sql y el legacy selectcampaign no cuentan.
        "condition": lambda s: tiene_acceso_chatbot(s.get('permissions', [])),
    },
    {
        "label": "AuditorIA",
        "id": "audit-dropdown",
        "icon": "bi-headset",
        # NOTA: el dropdown NO filtra por permiso propio; inject_menu() solo evalua
        # el permiso de cada hijo y muestra el menu si al menos uno es visible.
        "children": [
            {
                "label": "Auditar",
                "endpoint": "audit.Auditar",
                "icon": "bi-play-circle",
                "desc": "Lanzá una auditoría de calidad sobre las llamadas.",
                "group": "Operación",
                "permission": "audit:execute"
            },
            {
                "label": "Auditorías Realizadas",
                "endpoint": "audit.auditorias_realizadas",
                "icon": "bi-clipboard-check",
                "desc": "Resultados, audios y transcripciones de lo auditado.",
                "group": "Operación",
                "permission": "audit:execute"
            },
            {
                "label": "Dashboard Auditorías",
                "endpoint": "audit.bandeja_view",
                "icon": "bi-speedometer2",
                "desc": "Indicadores y evolución de la calidad.",
                "group": "Análisis",
                "permission": "bandeja.view"
            },
            {
                # Golden Set (Fase 1): cuánto le acierta la IA a un humano. Se ve
                # con audit:review (quien corrige a la IA) o goldenset:manage
                # (quien administra los sets); ambos nacen sin asignar y son
                # ADICIONALES a audit:execute, que es lo que exige el router de
                # auditorías del backend (mismo criterio que audit:scheduler). Se
                # pide acá también para no mostrar un item que daría 403 al entrar.
                "label": "Evaluación de la IA",
                "endpoint": "audit.evaluacion_view",
                "icon": "bi-bullseye",
                "desc": "Medí cuánto acierta cada plantilla contra la corrección humana.",
                "group": "Análisis",
                "condition": lambda s: (
                    'audit:execute' in s.get('permissions', [])
                    and ('audit:review' in s.get('permissions', [])
                         or 'goldenset:manage' in s.get('permissions', []))
                ),
            },
            {
                "label": "Plantillas",
                "endpoint": "plantillas.plantillas",
                "icon": "bi-file-earmark-text",
                "desc": "Definí las preguntas y criterios de evaluación.",
                "group": "Configuración",
                # Quien audita puede VER la plantilla con la que audita (el router
                # del backend ya lo permite); sin template:create la pantalla abre
                # en modo solo lectura. Es una condición y no un "permission"
                # porque son dos permisos alternativos.
                "condition": lambda s: any(
                    p in s.get('permissions', []) for p in ('template:read', 'audit:execute')
                ),
            },
            {
                # Gerente de operaciones: cuántas auditorías por mes puede gastar
                # cada campaña y cuánto es eso en dólares. Permiso propio
                # (audit:cuotas), nace sin asignar como audit:scheduler.
                "label": "Cupos de Auditoría",
                "endpoint": "audit.cuotas_view",
                "icon": "bi-sliders",
                "desc": "Definí cuántas auditorías por mes puede consumir cada campaña.",
                "group": "Configuración",
                "permission": "audit:cuotas"
            },
            {
                # Permiso propio desde 2026-07-13 (adicional a audit:execute):
                # programar auditorías recurrentes es más sensible que auditar.
                "label": "Scheduler",
                "endpoint": "audit.scheduler_view",
                "icon": "bi-calendar-week",
                "desc": "Programá auditorías recurrentes.",
                "group": "Configuración",
                "permission": "audit:scheduler"
            },
        ]
    },
    {
        # Agrupa las herramientas que antes colgaban sueltas de la barra. Son
        # pocas y bastante distintas entre sí, así que no llevan "group".
        "label": "Herramientas",
        "id": "ia-dropdown",
        "icon": "bi-stars",
        # "hidden" se evalúa antes que cualquier permiso: ni el super admin lo ve.
        "hidden": HERRAMIENTAS_OCULTAS,
        "children": [
            {
                "label": "Asistente de Datos",
                "endpoint": "sql_agent.index",
                "icon": "bi-database",
                "desc": "Consultá la base en lenguaje natural, sin escribir SQL.",
                "permission": "chatbot:sql"
            },
            {
                "label": "Información CSV",
                "endpoint": "informacion.informacion_csv",
                "icon": "bi-filetype-csv",
                "desc": "Informador dinámico: filtrá la información por secciones.",
                "permission": "csv:read",
            },
            {
                "label": "Analizar CV",
                "endpoint": "rrhh.analizar_cv",
                "icon": "bi-person-vcard",
                "desc": "Analizá currículums con IA y agendá entrevistas.",
                "permission": "rrhh:analyze"
            },
        ]
    },
    {
        "label": "Administración",
        "id": "admin-dropdown",
        "icon": "bi-gear",
        "children": [
            {
                # Alcanza con cualquiera de los tres permisos de gestión de
                # usuarios: alguien con solo users:reset_password (sin
                # users:create) también necesita entrar para blanquear.
                "label": "Gestionar Usuarios",
                "endpoint": "admin.manage_users",
                "icon": "bi-people",
                "desc": "Altas, blanqueos, roles y baja de cuentas.",
                "group": "Usuarios y accesos",
                "condition": lambda s: (s.get('is_super_admin')
                                        or 'users:create' in s.get('permissions', [])
                                        or 'users:reset_password' in s.get('permissions', [])
                                        or 'users:delete' in s.get('permissions', [])),
            },
            {
                "label": "Alta Masiva",
                "endpoint": "admin.bulk_users",
                "icon": "bi-person-plus",
                "desc": "Creá muchos usuarios de una vez desde un archivo.",
                "group": "Usuarios y accesos",
                "permission": "users:bulkcreate"
            },
            {
                # Gestión delegada: super admin, roles:manage (crear/editar roles
                # con permisos que posea) o roles:impersonate (solo simular).
                "label": "Gestionar Roles",
                "endpoint": "admin.manage_roles",
                "icon": "bi-shield-lock",
                "desc": "Definí roles y qué permisos incluye cada uno.",
                "group": "Usuarios y accesos",
                "condition": lambda s: ('roles:manage' in s.get('permissions', [])
                                        or 'roles:impersonate' in s.get('permissions', [])),
            },
            {
                # Log de RbacAuditLog: altas/blanqueos/borrados de usuarios y
                # cambios de roles. Mismo criterio que gestionar usuarios/roles.
                "label": "Log de Usuarios y Roles",
                "endpoint": "admin.users_audit_log",
                "icon": "bi-clock-history",
                "desc": "Historial de cambios sobre cuentas y roles.",
                "group": "Usuarios y accesos",
                "condition": lambda s: (s.get('is_super_admin')
                                        or 'roles:manage' in s.get('permissions', [])
                                        or 'users:create' in s.get('permissions', [])
                                        or 'users:reset_password' in s.get('permissions', [])
                                        or 'users:delete' in s.get('permissions', [])),
            },
            {
                # Login/logout/intentos + quién está conectado y análisis de uso
                # (pagina_web.LoginAudit). Permiso propio logs:login, nace sin
                # asignar (solo super admin hasta asignarlo desde Gestionar Roles).
                "label": "Logs de Ingreso",
                "endpoint": "admin.login_log_view",
                "icon": "bi-box-arrow-in-right",
                "desc": "Ingresos, sesiones activas y evolución del uso.",
                "group": "Usuarios y accesos",
                "permission": "logs:login",
            },
            {
                # Panel de calidad: crear/editar chatbots, cargar los documentos
                # del conocimiento, editar prompts, mapear PCRCs y reindexar on-demand.
                "label": "Chatbots",
                "endpoint": "chatbot_admin.manage_chatbots",
                "icon": "bi-robot",
                "desc": "Configurá bots, prompts, fuentes y reindexado.",
                "group": "Configuración",
                "permission": "chatbot:admin"
            },
            {
                # Triage de Calidad: qué le preguntaron a los chatbots que la
                # documentación no responde, agrupado por tema y ordenado por
                # frecuencia. Permiso propio chatbot.vacios, nace sin asignar.
                "label": "Vacíos de conocimiento",
                "endpoint": "admin.vacios_view",
                "icon": "bi-patch-question",
                "desc": "Qué no pudo responder el bot y hace falta documentar.",
                "group": "Configuración",
                "permission": "chatbot.vacios"
            },
            {
                # Administración de Tips del Día y asignación de grupos a roles RBAC.
                "label": "Gestionar Tips",
                "endpoint": "admin.manage_tips",
                "icon": "bi-lightbulb",
                "desc": "Configurá los tips del día y sus grupos por rol.",
                "group": "Configuración",
                "permission": "tips:manage",
            },
            {
                # Unifica las viejas "Gastos de IA" y "Logs de Auditoría" en una sola
                # página con pestañas Resumen/Solicitudes/Análisis. Solo uso_ia.view
                # (pedido 2026-07-02: los supervisores de calidad no acceden).
                "label": "Gastos y Logs de IA",
                "endpoint": "costos.uso_ia_view",
                "icon": "bi-cash-coin",
                "desc": "Consumo, costo y logs de las auditorías.",
                "group": "Consumo de IA",
                "permission": "uso_ia.view"
            },
            {
                # El chatbot es el único consumo de IA no relacionado con auditorías:
                # tiene su propia sección, con desglose por chatbot (ChatVoltara, CSV,
                # Paygo y los que se agreguen). Permiso propio desde 2026-07-13.
                "label": "Gastos Chatbot (IA)",
                "endpoint": "costos.uso_ia_chatbot_view",
                "icon": "bi-chat-dots",
                "desc": "Consumo y costo de los chatbots, por bot.",
                "group": "Consumo de IA",
                "permission": "uso_ia.chatbot"
            },
            {
                # Calidad: solo las consultas de los operadores a los chatbots
                # (misma página, pero sin costos). Se muestra únicamente a quien
                # tiene chatbot.solicitudes y NO uso_ia.chatbot (ese ya ve la
                # página completa con "Gastos Chatbot"). Permiso propio 2026-07-24.
                "label": "Consultas al Chatbot",
                "endpoint": "costos.uso_ia_chatbot_view",
                "icon": "bi-chat-left-text",
                "desc": "Qué consultaron los operadores a los chatbots y qué respondieron.",
                "group": "Consumo de IA",
                # Acceso "solo consultas" para Calidad: se muestra a quien tiene
                # chatbot.solicitudes pero NO el tablero completo (uso_ia.chatbot).
                # eval_condition_for_super => el super admin NO lo ve por duplicado
                # (ya ve "Gastos Chatbot", que incluye las Solicitudes).
                "eval_condition_for_super": True,
                "condition": lambda s: ('chatbot.solicitudes' in s.get('permissions', [])
                                        and 'uso_ia.chatbot' not in s.get('permissions', [])),
            }
        ]
    },
    {
        # Planificación de la operación: cuántas llamadas vamos a recibir y con
        # cuánta gente hay que cubrirlas. Fuera del dropdown de AuditorIA a
        # propósito: no es calidad, es dimensionamiento, y lo usa otra área.
        # Permiso propio (planificador.view), nace sin asignar como audit:cuotas.
        "label": "Planificador",
        "endpoint": "planificador.planificador_view",
        "icon": "bi-graph-up-arrow",
        "desc": "Pronóstico de llamadas por intervalo y operadores necesarios.",
        "permission": "planificador.view",
    },
    {
        # Manual de uso. Sin "permission" ni "condition": lo ve cualquiera que esté
        # logueado, incluso si todavía no le habilitaron ninguna pantalla (así puede
        # leer qué hace el sistema y qué pedirle al administrador). Va último para
        # que no compita con las secciones de trabajo.
        "label": "Documentación",
        "endpoint": "docs.documentacion",
        "icon": "bi-book",
        "desc": "Manual paso a paso de cada pantalla, en criollo y sin tecnicismos.",
    },
]
