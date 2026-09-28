"""Datos de la base de demostración. Todo es ficticio.

- `cargar_catalogos`: empresas, campañas, plataformas, permisos, roles y precios de IA. Van ANTES de las
  migraciones, porque varias verifican que exista lo que modifican (una campaña, un permiso, un precio).
- `cargar`: usuarios de prueba y datos de operación inventados (nómina, plantillas, auditorías…), DESPUÉS
  de las migraciones.
"""
from __future__ import annotations

import pyodbc

# ---------------------------------------------------------------------------------------------------------
# Catálogos
# ---------------------------------------------------------------------------------------------------------

PLATAFORMAS = [
    (1, "Mitrol"), (2, "Avaya"), (3, "Genesys"), (4, "CXOne"), (5, "Otro"),
    (6, "Hermes"), (7, "Wize"), (8, "Orion"), (9, "AsterVoIP"),
]

# (EmpresaID, Nombre, activa, RequiredPermissionID)
EMPRESAS = [
    (1, "HIDRA", 1, 14), (2, "Brandsync", 0, 15), (3, "AutoOne", 0, 16), (4, "Odonto Plus", 1, 17),
    (5, "Vantix", 1, 18), (6, "Aurora Salud", 0, 19), (7, "Tecnomax", 1, 20), (8, "Movilink", 0, 21),
    (9, "Osrural", 0, 22), (10, "CSV", 1, 23), (11, "Voltara", 1, 24), (12, "ALARMIX", 1, 26),
    (13, "RRHH", 0, 13), (14, "HIDRA Comercial", 1, 14), (15, "Vitalis Salud", 1, 27), (16, "Farmalux", 1, 28),
    (17, "Benefix", 1, 66), (18, "Gasur", 1, 68),
]

# (CampanaID, EmpresaID, Nombre, PlataformaID, activa)
CAMPANAS = [
    (1, 1, "HidraIN", 1, 1), (2, 2, "Brandsync", 1, 0), (5, 3, "Compras", 1, 0), (6, 4, "Dental", 1, 1),
    (7, 4, "PlanSenior", 1, 1), (8, 5, "Turnos", 8, 1), (9, 5, "Ventas", 8, 1), (10, 5, "Soporte", 8, 1),
    (11, 5, "Retencion", 8, 1), (12, 5, "Winback", 8, 1), (13, 6, "Turnos", 1, 0), (14, 6, "Imagenes", 1, 0),
    (15, 7, "Digix", 1, 1), (16, 7, "Ferbo", 1, 1), (17, 8, "Movilink", 1, 0), (18, 9, "Osrural", 1, 0),
    (19, 10, "CSV", 2, 1), (20, 11, "Voltara", 3, 1), (21, 12, "ALARMIX", 4, 1), (23, 13, "Análisis_CV", 5, 0),
    (24, 14, "Hidra Comercial", 6, 1), (25, 15, "Vitalis", 7, 1), (26, 4, "Dental chat", 1, 1),
    (27, 16, "Farmalux", 9, 1), (28, 10, "PAYGO", 2, 1), (29, 17, "Benefix", 3, 1), (30, 18, "Gasur", 5, 1),
]

EMPRESA_NOMBRE_MITROL = [
    (1, "HIDRA"), (2, "Brandsync"), (3, "AutoOne"), (4, "Odonto Plus"), (4, "Facebook"), (5, "Track On"),
    (5, "Trackon-Vantix"), (6, "AuroraSalud"), (7, "Tecnomax"), (8, "Movilink"), (9, "Osrural"),
]

# (id, code, description, activo)
PERMISOS = [
    (2, "users:create", "Crear nuevos usuarios", 1),
    (4, "audit:execute", "Ejecutar auditorias", 1),
    (5, "rrhh:analyze", "Analizar CVs", 1),
    (6, "template:read", "Ver lista de planillas", 1),
    (7, "template:create", "Permite la generacion de plantillas", 1),
    (8, "csv:read", "Ver la lista de csv", 1),
    (10, "chatbot:voltara", "Poder consultar al chatbot de voltara", 1),
    (11, "users:bulkcreate", "Permite la creacion de multiples usuarios en simultaneo", 1),
    (13, "template:rrhh", "Acceso a auditorías, dashboards y plantillas de RRHH", 0),
    (14, "template:hidra", "Acceso a auditorías, dashboards y plantillas de HIDRA / HIDRA Comercial", 1),
    (15, "template:brandsync", "Acceso a auditorías, dashboards y plantillas de Brandsync", 0),
    (16, "template:autoone", "Acceso a auditorías, dashboards y plantillas de AutoOne", 0),
    (17, "template:odontoplus", "Acceso a auditorías, dashboards y plantillas de Odonto Plus", 1),
    (18, "template:vantix", "Acceso a auditorías, dashboards y plantillas de Vantix", 1),
    (19, "template:aurorasalud", "Acceso a auditorías, dashboards y plantillas de Aurora Salud", 0),
    (20, "template:tecnomax", "Acceso a auditorías, dashboards y plantillas de Tecnomax", 1),
    (21, "template:movilink", "Acceso a auditorías, dashboards y plantillas de Movilink", 0),
    (22, "template:osrural", "Acceso a auditorías, dashboards y plantillas de Osrural", 0),
    (23, "template:csv", "Acceso a auditorías, dashboards y plantillas de CSV", 1),
    (24, "template:voltara", "Acceso a auditorías, dashboards y plantillas de Voltara", 1),
    (26, "template:alarmix", "Acceso a auditorías, dashboards y plantillas de ALARMIX", 1),
    (27, "template:vitalis", "Acceso a auditorías, dashboards y plantillas de Vitalis Salud", 1),
    (28, "template:farmalux", "Acceso a auditorías, dashboards y plantillas de Farmalux", 1),
    (29, "bandeja.view", "Ver la Bandeja del Supervisor (casos + anotaciones)", 1),
    (31, "bandeja.ver_todo", "Ver casos de todos los auditores (gerencia / QA cross-team)", 1),
    (34, "chatbot:sql", "Usar el agente de consultas SQL", 1),
    (35, "audit.bandeja_segurar", "Permite la visualizacion personalizada de Vantix Segurar", 1),
    (36, "uso_ia.view", "Ver el tablero de gastos/consumo de IA", 1),
    (37, "template:modelo_ia", "Ver y modificar qué modelo de Gemini usa cada plantilla (incluye precios por modelo)", 1),
    (38, "chatbot:paygo", "Usar el chatbot de PayGo", 1),
    (39, "chatbot:benefix", "Usar el chatbot de Benefix", 1),
    (40, "chatbot:vantix", "Usar el chatbot de Vantix", 1),
    (41, "chatbot:csv", "Usar los chatbots del grupo CSV (el bot concreto se resuelve por PCRC o selector)", 1),
    (42, "chatbot:admin", "Administrar chatbots: crear/editar bots, docs, prompts, reindexado y mapeo PCRC", 1),
    (43, "roles:manage", "Gestión delegada de roles: crear/editar/borrar roles con permisos que el usuario ya posee", 1),
    (44, "templates:manage", "Acceso a TODAS las empresas en auditorías, dashboards y plantillas (sin necesidad de permisos por empresa)", 1),
    (45, "roles:impersonate", "Simular un rol (ver el sistema con sus permisos efectivos) para verificar accesos antes de asignarlo", 1),
    (46, "uso_ia.chatbot", "Ver el tablero de gastos/consumo de IA de los chatbots (Gastos Chatbot)", 1),
    (47, "audit:scheduler", "Programar auditorías recurrentes (Scheduler); requiere además audit:execute", 1),
    (48, "users:reset_password", "Blanquear la contraseña de un usuario (queda igual al documento, fuerza cambio en el próximo ingreso)", 1),
    (49, "users:delete", "Eliminar el acceso de un usuario (borra su contraseña y roles; no toca nómina)", 1),
    (50, "logs:login", "Ver el log de inicios/cierres de sesión y el análisis de uso (Logs de Ingreso)", 1),
    (51, "chatbot.solicitudes", "Ver las consultas de los operadores a los chatbots (Solicitudes), sin datos de costos", 1),
    (53, "chatbot:voltara_digital", "Usar el chatbot de Voltara Digital (generación de cartas de respuesta)", 1),
    (54, "chatbot.vacios", "Ver y triagear los vacíos de conocimiento de los chatbots (qué no pudo responder el bot)", 1),
    (55, "bandeja.config", "Editar la configuración de visualización del Dashboard de Auditorías (gráficos, colores/polaridad, metas y umbrales por atributo)", 1),
    (56, "audit:review", "Revisar y corregir las respuestas de la IA en Auditorías Realizadas (verdad humana); requiere además audit:execute", 1),
    (57, "goldenset:manage", "Administrar los Golden Sets de evaluación de plantillas (crear sets, agregar/quitar interacciones)", 1),
    (58, "chatbot.adjuntos", "Adjuntar capturas de pantalla y PDF en las consultas al chatbot RAG", 1),
    (59, "audit:sync", "Auditar en modo sincrónico (\"Auditar ahora\", doble de costo que Batch); requiere además audit:execute", 1),
    (60, "audit:reauditar", "Volver a auditar un llamado ya auditado con el prompt vigente (reemplaza la corrida anterior, que queda archivada); requiere además audit:execute", 1),
    (61, "audit:cuotas", "Gestionar el cupo mensual de auditorías por campaña: ver consumo, costo por auditoría y gasto máximo proyectado, y subir/bajar el cupo", 1),
    (62, "audit:cuota_exento", "Exento del cupo mensual de auditorías: no consume cupo de ninguna campaña ni se le bloquea auditar (Calidad)", 1),
    (63, "planificador.view", "Ver el Planificador: pronóstico de llamadas por intervalo y operadores necesarios por pool", 1),
    (64, "planificador.edit", "Configurar el Planificador y recalcular: objetivos por skill, pools, porcentaje de asignación del cliente, shrinkage, calendario de eventos y ajustes manuales del pronóstico", 1),
    (65, "chatbot:hidra_comercial", "Usar el chatbot de Hidra Comercial", 1),
    (66, "template:benefix", "Acceso a auditorías, dashboards y plantillas de Benefix", 1),
    (67, "tips:manage", "Crear, editar y asignar grupos de tips del día", 1),
    (68, "template:gasur", "Acceso a Gasur (planificador, auditorías y plantillas)", 1),
]

# (id, name, description, is_super_admin, parent_role_id)
ROLES = [
    (1, "Super Admin", "Acceso total al sistema", 1, None),
    (2, "Analista RRHH", "Perfil para analistas de Recursos humanos", 0, None),
    (3, "Operador CSV", "", 0, None),
    (4, "Operador Voltara", "", 0, None),
    (5, "Analista Calidad", "", 0, None),
    (6, "Jefe Calidad", "", 0, 5),
    (7, "Jefe RRHH", "", 0, 2),
    (8, "Coordinador Vantix", "", 0, 20),
    (9, "Coordinador Voltara", "Jefatura de voltara capaz de otorgar usuarios", 0, 21),
    (10, "Jefe Pagonet", "", 0, 23),
    (11, "Supervisor Hidra", "", 0, 16),
    (12, "Supervisor Dental", "Rol de Odonto Plus para poder realizar auditorias", 0, 16),
    (13, "Supervisor Farmalux", "", 0, 16),
    (14, "Operador Benefix", "", 0, None),
    (15, "Operador Vantix", "", 0, None),
    (16, "Supervisor padre", "Rol de supervisor con los permisos que tienen todos los supervisores", 0, None),
    (17, "Gerente Operaciones", "Rol para el Gerente de operaciones, orientado a tener todas las herramientas de operaciones", 0, 16),
    (18, "Supervisor ALARMIX", "", 0, 16),
    (19, "Supervisor Benefix", "", 0, 16),
    (20, "Supervisor Vantix", "", 0, 16),
    (21, "Supervisor Voltara", "", 0, 16),
    (22, "Supervisor CSV", "", 0, 16),
    (23, "Coordinador CSV", "", 0, 22),
    (24, "Coordinador Mix", "", 0, 13),
    (25, "Coordinador Hidra", "", 0, 11),
    (26, "Coordinador ALARMIX", "", 0, 18),
    (27, "Jefe ALARMIX/Voltara/Vantix", "", 0, 8),
    (28, "Supervisor Aurora Salud", "", 0, 16),
    (29, "Supervisor PlanSenior", "", 0, 16),
    (30, "Supervisor Vitalis Salud", "", 0, 16),
    (31, "Cliente Vantix", "", 0, None),
    (32, "GTR", "", 0, None),
]

ROLE_PERMISSIONS = (
    "2:5,2:6,2:7,2:13,3:8,3:41,4:10,4:53,4:58,5:4,5:6,5:7,5:8,5:10,5:14,5:15,5:16,5:17,5:18,5:19,5:20,5:21,"
    "5:22,5:23,5:24,5:26,5:27,5:28,5:29,5:31,5:38,5:39,5:40,5:41,5:42,5:51,5:53,5:54,5:55,5:56,5:57,5:58,5:59,"
    "5:60,5:62,5:65,5:66,5:67,6:2,6:4,6:6,6:7,6:8,6:10,6:11,6:14,6:15,6:16,6:17,6:18,6:19,6:20,6:21,6:22,6:23,"
    "6:24,6:26,6:27,6:28,6:29,6:31,6:34,6:35,6:36,6:37,6:38,6:39,6:40,6:41,6:42,6:46,6:47,6:48,6:49,6:53,6:57,"
    "7:2,7:5,7:6,7:7,7:13,7:48,7:49,8:2,8:4,8:6,8:7,8:11,8:18,8:29,8:31,8:35,8:47,8:48,8:49,8:55,9:2,9:4,9:6,"
    "9:7,9:10,9:24,9:48,9:49,9:53,10:2,10:4,10:6,10:7,10:8,10:11,10:23,10:38,10:41,10:48,10:49,10:51,11:4,"
    "11:14,12:4,12:6,12:7,12:17,13:4,13:6,13:7,13:28,13:29,14:39,15:40,16:29,16:31,17:2,17:4,17:6,17:7,17:8,"
    "17:10,17:11,17:14,17:15,17:16,17:17,17:18,17:19,17:20,17:21,17:22,17:23,17:24,17:26,17:27,17:28,17:29,"
    "17:31,17:34,17:35,17:36,17:37,17:38,17:39,17:40,17:41,17:43,17:46,17:47,17:48,17:49,17:51,17:53,17:55,"
    "17:57,17:58,17:61,17:62,18:26,19:39,20:4,20:6,20:7,20:18,20:35,20:40,20:55,20:56,20:57,21:10,21:24,21:29,"
    "21:31,21:53,22:41,22:51,23:2,23:11,23:23,23:48,23:49,24:2,24:11,24:16,24:17,24:19,24:27,24:39,24:48,24:49,"
    "26:56,27:10,27:24,27:26,27:53,28:19,30:27,31:18,31:29,31:40,31:51,32:24,32:63,32:64,32:68"
)

# Puestos de la nómina (dbo.puestos). 2 y 3 atienden el teléfono: son los que cuentan en la malla.
PUESTOS = [(1, "Supervisor"), (2, "Operador 2"), (3, "Operador Telefonico"), (4, "Operador Capacitacion"),
           (5, "Back Office"), (6, "Team Leader"), (7, "Analista"), (8, "Operador Postcurso")]

# (id, modelo, fecha_desde, input, output, cached, notas) — precios públicos de lista de Gemini
IA_PRECIOS = [
    (1, "gemini-3-flash", "2025-10-01", 0.50, 3.00, 0.050, "Auditoría/transcripción hasta ~2026-05-19"),
    (2, "gemini-3.5-flash", "2026-05-19", 1.50, 9.00, 0.150, "Auditoría/transcripción desde ~2026-05-19 hasta ~2026-07-21"),
    (3, "gemini-3.1-flash-lite", "2025-10-01", 0.25, 1.50, 0.025, "Chatbot RAG hasta ~2026-07-21"),
    (4, "gemini-3.1-pro-preview", "2025-10-01", 2.00, 12.00, 0.200, "Asistente de plantillas hasta ~2026-08-14"),
    (5, "gemini-3-flash-preview", "2026-07-03", 1.00, 3.00, 0.100, "Opción \"Económica\" para plantillas"),
    (8, "gemini-3.6-flash", "2026-07-21", 1.50, 7.50, 0.150, "Auditoría/transcripción desde ~2026-07-21 hasta ~2026-08-14"),
    (10, "gemini-3.5-flash-lite", "2026-07-21", 0.30, 2.50, 0.030, "Chatbot RAG"),
    (11, "gemini-3.7-flash", "2026-08-14", 0.75, 3.75, 0.075, "Auditoría/transcripción + asistentes desde ~2026-08-14 hasta ~2026-09-02"),
    (12, "gemini-3.7-flash", "2027-01-01", 1.50, 7.50, 0.150, "Precio de lista desde 2027-01-01"),
    (13, "gemini-3.6-flash", "2026-08-14", 0.75, 3.75, 0.075, "Precio promocional"),
    (14, "gemini-3.6-flash", "2027-01-01", 1.50, 7.50, 0.150, "Precio de lista desde 2027-01-01"),
    (15, "gemini-3.8-flash", "2026-09-02", 0.75, 3.75, 0.075, "Auditoría/transcripción + asistentes desde 2026-09-02 (precio promocional)"),
    (16, "gemini-3.8-flash", "2027-01-01", 1.50, 7.50, 0.150, "Precio de lista desde 2027-01-01"),
]


def _insertar_con_id(cur: pyodbc.Cursor, tabla: str, columnas: list[str], filas: list[tuple]) -> None:
    """INSERT con IDENTITY_INSERT, salteando las filas cuya clave (primera columna) ya existe."""
    clave = columnas[0]
    lista = ", ".join(f"[{c}]" for c in columnas)
    marcas = ", ".join("?" for _ in columnas)
    cur.execute(f"SET IDENTITY_INSERT {tabla} ON")
    try:
        for fila in filas:
            cur.execute(f"IF NOT EXISTS (SELECT 1 FROM {tabla} WHERE [{clave}] = ?) "
                        f"INSERT INTO {tabla} ({lista}) VALUES ({marcas})", fila[0], *fila)
    finally:
        cur.execute(f"SET IDENTITY_INSERT {tabla} OFF")


def cargar_catalogos(cn: pyodbc.Connection) -> None:
    cur = cn.cursor()
    _insertar_con_id(cur, "pagina_web.Permissions", ["id", "code", "description", "activo"], PERMISOS)
    _insertar_con_id(cur, "calidad.Plataformas", ["PlataformaID", "nombre", "IsActive"],
                     [(i, n, 1) for i, n in PLATAFORMAS])
    _insertar_con_id(cur, "calidad.Empresas", ["EmpresaID", "Nombre", "IsActive", "RequiredPermissionID"], EMPRESAS)
    _insertar_con_id(cur, "calidad.Campanas", ["CampanaID", "EmpresaID", "Nombre", "PlataformaID", "IsActive"], CAMPANAS)
    for empresa_id, nombre in EMPRESA_NOMBRE_MITROL:
        cur.execute("IF NOT EXISTS (SELECT 1 FROM calidad.Empresa_nombreMitrol WHERE EmpresaID = ? AND nombre_mitrol = ?) "
                    "INSERT INTO calidad.Empresa_nombreMitrol (EmpresaID, nombre_mitrol) VALUES (?, ?)",
                    empresa_id, nombre, empresa_id, nombre)
    # Roles: primero sin padre (el padre puede tener un id mayor) y después se enlaza la jerarquía.
    _insertar_con_id(cur, "pagina_web.Roles", ["id", "name", "description", "is_super_admin"],
                     [r[:4] for r in ROLES])
    for rol_id, *_, padre in ROLES:
        if padre:
            cur.execute("UPDATE pagina_web.Roles SET parent_role_id = ? WHERE id = ?", padre, rol_id)
    for par in ROLE_PERMISSIONS.split(","):
        rol_id, permiso_id = (int(x) for x in par.split(":"))
        cur.execute("IF NOT EXISTS (SELECT 1 FROM pagina_web.RolePermissions WHERE role_id = ? AND permission_id = ?) "
                    "INSERT INTO pagina_web.RolePermissions (role_id, permission_id) VALUES (?, ?)",
                    rol_id, permiso_id, rol_id, permiso_id)
    _insertar_con_id(cur, "puestos", ["id", "puesto"], PUESTOS)
    _insertar_con_id(cur, "pagina_web.IA_Precios",
                     ["id", "modelo", "fecha_desde", "input_usd_mtok", "output_usd_mtok", "cached_usd_mtok", "notas"],
                     IA_PRECIOS)
    print("Catálogos cargados.")


# ---------------------------------------------------------------------------------------------------------
# Datos de operación (inventados)
# ---------------------------------------------------------------------------------------------------------

CLAVE_DEMO = "demo1234"

# (id, cliente, campaña, sub-campaña)
CAMPANAS_RRHH = [
    (1, "Acme", "Staff", "Staff"), (2, "Acme", "Calidad", "Calidad"), (54, "Voltara", "Voltara", "Voltara"),
    (57, "Hidra", "HidraIN", "Hidra Canal Telefónico"), (121, "Gasur", "Gasur", "Gasur"),
    (135, "Gasur", "Despacho", "Despacho"), (200, "Benefix", "Benefix", "Benefix"),
]

# (documento, nombre, apellido, rol, campaña legacy) — los usuarios con los que se entra a la demo.
USUARIOS_DEMO = [
    (11111111, "Ada", "Administradora", 1, "Staff"),        # Super Admin
    (22222222, "Carla", "Calidad", 6, "Calidad"),           # Jefe Calidad
    (33333333, "Sergio", "Supervisor", 21, "Voltara"),       # Supervisor Voltara
    (44444444, "Olga", "Operadora", 4, "Voltara"),          # Operador Voltara (solo chatbot)
    (55555555, "Pablo", "Planificador", 32, "Staff"),        # GTR (planificador)
]

NOMBRES = ["Lucía", "Martín", "Sofía", "Julián", "Valentina", "Tomás", "Camila", "Mateo", "Florencia",
           "Nicolás", "Agustina", "Facundo", "Micaela", "Joaquín", "Rocío", "Federico", "Brenda", "Lautaro",
           "Daniela", "Ezequiel", "Milagros", "Gonzalo", "Antonella", "Ramiro"]
APELLIDOS = ["Acosta", "Benítez", "Cabrera", "Domínguez", "Espinoza", "Ferreyra", "Giménez", "Herrera",
             "Ibarra", "Juárez", "Ledesma", "Molina", "Navarro", "Ortiz", "Paz", "Quiroga", "Ríos",
             "Sosa", "Toledo", "Vera", "Zárate", "Luna", "Medina", "Ponce"]

# Campañas con datos de auditoría: (CampanaID, EmpresaID, cliente legacy, campaña legacy, prefijo de usuario)
CAMPANAS_DEMO = [
    (20, 11, "Voltara", "Voltara", "vo"),
    (1, 1, "Hidra", "HidraIN", "hi"),
    (29, 17, "Benefix", "Benefix", "bx"),
]

_CRITICO = '{"enum": ["OK", "NO OK", "EC", "N/A"]}'

# Plantillas: (CampanaID, nombre, descripción, [atributos]); atributo = (nombre, tipo, prompt, restricciones, ponderación)
PLANTILLAS = [
    (20, "Atención telefónica Voltara", "Auditoría de la línea comercial y de reclamos de la distribuidora eléctrica.", [
        ("Saludo y presentación", "critical_audit", "¿El operador saluda, dice su nombre y el de la empresa en los primeros segundos?", _CRITICO, 10),
        ("Validación de identidad", "critical_audit", "¿Valida el número de cliente o el titular antes de dar información de la cuenta? No validar y dar datos de la cuenta es EC.", _CRITICO, 15),
        ("Escucha activa", "critical_audit", "¿Deja hablar al cliente, no lo interrumpe y retoma lo que dijo?", _CRITICO, 10),
        ("Indagación de la necesidad", "critical_audit", "¿Hace las preguntas necesarias para entender el motivo real del llamado?", _CRITICO, 15),
        ("Resolución correcta", "critical_audit", "¿La respuesta o gestión es la que corresponde según el procedimiento?", _CRITICO, 25),
        ("Información de plazos", "critical_audit", "Si hay un reclamo, ¿informa el número de reclamo y el plazo estimado? N/A si no hubo reclamo.", _CRITICO, 10),
        ("Cierre y despedida", "critical_audit", "¿Pregunta si hay algo más en que ayudar y se despide con la fórmula de la empresa?", _CRITICO, 5),
        ("Trato cordial", "critical_audit", "¿Mantiene un tono amable y profesional durante todo el llamado?", _CRITICO, 10),
        ("Motivo del llamado", "enum", "Motivo principal por el que llama el cliente.", '{"enum": ["Falta de suministro", "Consulta de factura", "Reclamo técnico", "Alta o baja de servicio", "Otros"]}', 0),
        ("Ofreció canal digital", "boolean", "¿Ofreció la factura digital o la oficina virtual?", None, 0),
        ("Observaciones", "texto", "Resumen breve (máximo dos oraciones) de lo más importante del llamado.", None, 0),
    ]),
    (1, "Línea técnica Hidra", "Auditoría de la línea técnica de agua y cloacas.", [
        ("Saludo y presentación", "critical_audit", "¿Saluda con la fórmula de la empresa y se presenta?", _CRITICO, 10),
        ("Toma de datos del domicilio", "critical_audit", "¿Registra calle, altura y entre calles del problema? Sin domicilio no se puede generar la orden: es EC.", _CRITICO, 20),
        ("Diagnóstico del problema", "critical_audit", "¿Hace las preguntas del árbol de diagnóstico (falta de agua, baja presión, pérdida, desborde)?", _CRITICO, 25),
        ("Consulta de cortes programados", "critical_audit", "¿Verifica si hay un corte programado en la zona antes de generar un reclamo?", _CRITICO, 15),
        ("Número de reclamo", "critical_audit", "¿Informa el número de reclamo generado? N/A si no se generó.", _CRITICO, 15),
        ("Trato cordial", "critical_audit", "¿Mantiene un tono amable y profesional?", _CRITICO, 15),
        ("Tipo de problema", "enum", "Problema que reporta el usuario.", '{"enum": ["Falta de agua", "Baja presión", "Pérdida en vereda", "Desborde cloacal", "Calidad del agua", "Otros"]}', 0),
        ("Observaciones", "texto", "Resumen breve del llamado.", None, 0),
    ]),
    (29, "Comercial Benefix", "Auditoría de la atención a empresas clientes de tarjetas de beneficios.", [
        ("Saludo y presentación", "critical_audit", "¿Saluda y se presenta?", _CRITICO, 10),
        ("Identificación de la empresa cliente", "critical_audit", "¿Identifica la razón social y el número de cliente antes de gestionar?", _CRITICO, 20),
        ("Resolución de la consulta", "critical_audit", "¿Resuelve la consulta (saldo, pedido de tarjetas, facturación) según el procedimiento?", _CRITICO, 30),
        ("Ofrecimiento comercial", "critical_audit", "Si corresponde, ¿ofrece un producto adicional? N/A si el contexto no lo permite.", _CRITICO, 15),
        ("Cierre", "critical_audit", "¿Resume lo gestionado y se despide?", _CRITICO, 10),
        ("Trato cordial", "critical_audit", "¿Mantiene un tono amable y profesional?", _CRITICO, 15),
        ("Tema de la consulta", "enum", "Tema principal.", '{"enum": ["Saldo y movimientos", "Pedido de tarjetas", "Facturación", "Baja", "Otros"]}', 0),
        ("Observaciones", "texto", "Resumen breve del llamado.", None, 0),
    ]),
]

SYSTEM_PROMPT = (
    "Sos auditor de calidad de un contact center. Vas a escuchar un llamado entre un operador y un cliente "
    "y a evaluarlo con los atributos de la plantilla. Respondé solo con lo que se escucha en el audio: si "
    "algo no ocurrió, no lo inventes. Para cada atributo de tipo OK / NO OK / EC / N/A justificá brevemente."
)

TIPIFICACIONES = {
    20: ["Falta de suministro", "Consulta de factura", "Reclamo técnico", "Alta o baja de servicio", "Otros"],
    1: ["Falta de agua", "Baja presión", "Pérdida en vereda", "Desborde cloacal", "Calidad del agua", "Otros"],
    29: ["Saldo y movimientos", "Pedido de tarjetas", "Facturación", "Baja", "Otros"],
}

DIALOGOS = {
    20: [("Agente", "Buen día, se comunicó con Voltara, mi nombre es {op}. ¿En qué puedo ayudarlo?"),
         ("Cliente", "Hola, estoy sin luz desde anoche en mi casa."),
         ("Agente", "Lamento la situación. ¿Me indica el número de cliente que figura en la factura?"),
         ("Cliente", "Sí, es el cero cero uno dos tres cuatro cinco seis."),
         ("Agente", "Gracias. Veo que en su zona hay un corte registrado y ya hay una cuadrilla trabajando."),
         ("Cliente", "¿Y para cuándo estaría?"),
         ("Agente", "El plazo estimado de normalización es de cuatro horas. Le dejo el número de reclamo por si lo necesita."),
         ("Cliente", "Perfecto, muchas gracias."),
         ("Agente", "¿Hay algo más en que pueda ayudarlo? Gracias por comunicarse con Voltara, que tenga buen día.")],
    1: [("Agente", "Hidra buenas tardes, le habla {op}."),
        ("Cliente", "Hola, no tengo agua desde la mañana."),
        ("Agente", "¿Me dice la calle, la altura y entre qué calles está el domicilio?"),
        ("Cliente", "Avenida Siempre Viva setecientos cuarenta y dos, entre Olmos y Tilos."),
        ("Agente", "Estoy viendo que hay un trabajo programado en la zona hasta las dieciocho horas."),
        ("Cliente", "Ah, no sabía. ¿Tengo que hacer algo?"),
        ("Agente", "No, se normaliza sola. Si a las veinte sigue sin agua vuelva a llamar y generamos el reclamo."),
        ("Cliente", "Bueno, gracias."),
        ("Agente", "A usted. Buenas tardes.")],
    29: [("Agente", "Benefix, buen día, mi nombre es {op}."),
         ("Cliente", "Hola, llamo de una empresa cliente, necesito pedir tarjetas para empleados nuevos."),
         ("Agente", "¿Me indica la razón social y el número de cliente?"),
         ("Cliente", "Distribuidora del Oeste, cliente cuatro cinco seis siete."),
         ("Agente", "Gracias. ¿Cuántas tarjetas necesita y a qué sucursal las enviamos?"),
         ("Cliente", "Doce tarjetas, a la oficina central."),
         ("Agente", "Listo, quedó cargado el pedido. Llegan en cinco días hábiles."),
         ("Cliente", "Perfecto."),
         ("Agente", "Le resumo: doce tarjetas a la oficina central. ¿Algo más? Que tenga buen día.")],
}

OBSERVACIONES = [
    "Llamado resuelto en el primer contacto.",
    "El operador tardó en identificar el motivo; luego lo resolvió.",
    "El cliente estaba molesto; el operador mantuvo la calma.",
    "Faltó informar el plazo estimado.",
    "Buena gestión y cierre prolijo.",
]


def _hash(clave: str) -> str:
    from passlib.context import CryptContext  # misma configuración que backend/app/security.py
    return CryptContext(schemes=["bcrypt"], deprecated="auto").hash(clave)


def _segmentos(campana_id: int, operador: str, duracion: int) -> list[dict]:
    turnos = DIALOGOS[campana_id]
    paso = duracion / len(turnos)
    segs = []
    for i, (quien, texto) in enumerate(turnos):
        texto = texto.format(op=operador)
        segs.append({"speakerLabel": quien, "startTime": round(i * paso, 1), "endTime": round((i + 1) * paso - 0.4, 1),
                     "confidence": 0.93, "text": texto.split()})
    return segs


def cargar(cn: pyodbc.Connection) -> None:
    import json
    import random
    from datetime import date, datetime, timedelta

    cur = cn.cursor()
    cur.execute("SELECT COUNT(*) FROM nomina WHERE documento = ?", USUARIOS_DEMO[0][0])
    if cur.fetchone()[0]:
        print("Datos de demostración: ya estaban cargados.")
        return

    rnd = random.Random(2026)
    hoy = date.today()
    clave = _hash(CLAVE_DEMO)
    cur.fast_executemany = False

    # Campañas de RRHH (dbo.campanas), las de la nómina y los turnos. Los ids son los que esperan las
    # migraciones del planificador (planificacion.PoolOrigen: 54 Voltara, 57 Hidra, 121 Gasur).
    _insertar_con_id(cur, "campanas", ["id", "cliente", "campana", "sub_campana"], CAMPANAS_RRHH)
    legacy = {c[2]: c[0] for c in CAMPANAS_RRHH}

    # Nómina: usuarios de la demo + operadores de cada campaña.
    nomina_id = 0
    alta = hoy - timedelta(days=900)

    def alta_nomina(documento: int, nombre: str, apellido: str, fecha_alta: date) -> int:
        nonlocal nomina_id
        nomina_id += 1
        cur.execute("INSERT INTO nomina (id, legajo, documento, nombre, apellido, fecha_alta, fecha_piso) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    nomina_id, f"L{nomina_id:05d}", documento, nombre, apellido, fecha_alta, fecha_alta + timedelta(days=15))
        return nomina_id

    staff: dict[int, int] = {}
    for documento, nombre, apellido, rol, camp in USUARIOS_DEMO:
        nid = alta_nomina(documento, nombre, apellido, alta)
        staff[documento] = nid
        cur.execute("INSERT INTO pagina_web.passwords (nomina_id, password, must_change_password) VALUES (?, ?, 0)", nid, clave)
        cur.execute("INSERT INTO pagina_web.UserRoles (nomina_id, role_id) VALUES (?, ?)", nid, rol)
        cur.execute("INSERT INTO operadores (legajo_id, estado, campana_id, fecha_desde) VALUES (?, 1, ?, ?)",
                    nid, legacy[camp], datetime.combine(alta, datetime.min.time()))

    operadores: dict[int, list[tuple[int, str, str, float]]] = {}
    for campana_id, _, _, camp_legacy, prefijo in CAMPANAS_DEMO:
        equipos = []
        for n in range(2):
            sup_nombre, sup_apellido = rnd.choice(NOMBRES), rnd.choice(APELLIDOS)
            sup = alta_nomina(30000000 + campana_id * 100 + n, sup_nombre, sup_apellido, alta)
            cur.execute("INSERT INTO equipos (nombre, apellido, campana_id, id_nomina, fecha_desde) "
                        "OUTPUT INSERTED.id VALUES (?, ?, ?, ?, ?)",
                        sup_nombre, sup_apellido, legacy[camp_legacy], sup, datetime.combine(alta, datetime.min.time()))
            equipos.append(cur.fetchone()[0])
        operadores[campana_id] = []
        for n in range(8):
            nombre, apellido = rnd.choice(NOMBRES), rnd.choice(APELLIDOS)
            ingreso = hoy - timedelta(days=rnd.randint(60, 800))
            nid = alta_nomina(40000000 + campana_id * 100 + n, nombre, apellido, ingreso)
            cur.execute("INSERT INTO operadores (legajo_id, estado, campana_id, equipo_id, fecha_desde, hora_ingreso, "
                        "hora_salida, puesto_id) VALUES (?, 1, ?, ?, ?, ?, ?, 3)",
                        nid, legacy[camp_legacy], equipos[n % 2], datetime.combine(ingreso, datetime.min.time()),
                        "08:00" if n % 2 else "14:00", "14:00" if n % 2 else "20:00")
            calidad = rnd.uniform(0.62, 0.96)  # probabilidad de OK por atributo: cada operador tiene su nivel
            operadores[campana_id].append((nid, f"{prefijo}{n + 1:03d}", f"{nombre} {apellido}", calidad))

    # Plantillas y atributos.
    atributos: dict[int, list[tuple[int, str, str, str | None, float]]] = {}
    plantilla_de: dict[int, int] = {}
    for campana_id, nombre, descripcion, attrs in PLANTILLAS:
        cur.execute("INSERT INTO calidad.Plantillas (Nombre, SystemPrompt, descripcion, CampanaID, IsActive) "
                    "OUTPUT INSERTED.PlantillaID VALUES (?, ?, ?, ?, 1)", nombre, SYSTEM_PROMPT, descripcion, campana_id)
        pid = cur.fetchone()[0]
        plantilla_de[campana_id] = pid
        atributos[pid] = []
        for orden, (anom, tipo, prompt, restr, pond) in enumerate(attrs, start=1):
            cur.execute("INSERT INTO calidad.Atributos (PlantillaID, NombreAtributo, PromptAdyacente, TipoDato, Restricciones, "
                        "Orden, Ponderacion) OUTPUT INSERTED.AtributoID VALUES (?, ?, ?, ?, ?, ?, ?)",
                        pid, anom, prompt, tipo, restr, orden, pond)
            atributos[pid].append((cur.fetchone()[0], anom, tipo, restr, pond))

    # Auditorías de los últimos 75 días, con transcripción y consumo de IA.
    auditor = USUARIOS_DEMO[1][0]
    total = 0
    for campana_id, empresa_id, _, _, _ in CAMPANAS_DEMO:
        pid = plantilla_de[campana_id]
        for nid, usuario, nombre_op, calidad in operadores[campana_id]:
            for dia in range(75, 0, -1):
                fecha = hoy - timedelta(days=dia)
                if fecha.weekday() == 6 or rnd.random() > 0.45:
                    continue
                total += 1
                momento = datetime.combine(fecha, datetime.min.time()) + timedelta(minutes=rnd.randint(8 * 60, 20 * 60))
                duracion = rnd.randint(90, 780)
                id_app = f"DEMO-{campana_id:02d}-{fecha:%Y%m%d}-{total:05d}"
                valores, ok, pond_total, ec = [], 0.0, 0.0, False
                for aid, anom, tipo, restr, pond in atributos[pid]:
                    if tipo == "critical_audit":
                        r = rnd.random()
                        if "plazos" in anom.lower() or "reclamo" in anom.lower() or "comercial" in anom.lower():
                            v = "N/A" if r < 0.35 else ("OK" if r < 0.35 + 0.65 * calidad else "NO OK")
                        elif r < 0.012 and pond >= 15:
                            v = "EC"
                        else:
                            v = "OK" if r < calidad + (0.05 if pond <= 10 else 0) else "NO OK"
                        if v == "EC":
                            ec = True
                        if v != "N/A":
                            pond_total += pond
                            ok += pond if v == "OK" else 0
                    elif tipo == "enum":
                        v = rnd.choice(json.loads(restr)["enum"])
                    elif tipo == "boolean":
                        v = "Sí" if rnd.random() < calidad else "No"
                    else:
                        v = rnd.choice(OBSERVACIONES)
                    valores.append((aid, v, pond))
                puntaje = 0.0 if ec else round(100 * ok / pond_total, 2) if pond_total else None
                tokens_in, tokens_out, tokens_th = rnd.randint(9000, 21000), rnd.randint(500, 1300), rnd.randint(250, 900)
                cur.execute(
                    "INSERT INTO calidad.Auditorias (IdAplicativo, operadorUsuario, CampanaID, EmpresaID, PlantillaID, "
                    "FechaAuditoria, AuditorUsuarioID, sentido_interaccion, input_tokens, output_tokens, thoughts_tokens, "
                    "response_thoughts, fecha_interaccion, PuntajeFinal, EsErrorCritico, tipificacion_interaccion, "
                    "duracion_segundos, OperadorNominaID) OUTPUT INSERTED.AuditoriaID "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'Entrante', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    id_app, usuario, campana_id, empresa_id, pid, momento + timedelta(hours=rnd.randint(12, 40)), auditor,
                    tokens_in, tokens_out, tokens_th,
                    "Evalué cada atributo con lo que se escucha en el audio." if rnd.random() < 0.5 else None,
                    momento, puntaje, 1 if ec else 0, rnd.choice(TIPIFICACIONES[campana_id]), duracion, nid)
                auditoria_id = cur.fetchone()[0]
                cur.executemany("INSERT INTO calidad.AuditoriaDetalles (AuditoriaID, AtributoID, ValorResultado, Orden, "
                                "PonderacionAplicada) VALUES (?, ?, ?, ?, ?)",
                                [(auditoria_id, aid, v, i + 1, pond) for i, (aid, v, pond) in enumerate(valores)])
                segs = _segmentos(campana_id, nombre_op.split()[0], duracion)
                cur.execute("INSERT INTO calidad.transcripciones (IdAplicativo, metadata, segments, analytics, fecha_subida, user_id) "
                            "VALUES (?, ?, ?, ?, ?, ?)", id_app,
                            json.dumps({"overallConfidence": 0.93, "languageCode": "es-AR", "audioDurationSeconds": duracion}),
                            json.dumps(segs, ensure_ascii=False), json.dumps({"keywords": [], "entities": []}),
                            momento + timedelta(hours=13), auditor)
                cur.execute("INSERT INTO pagina_web.IA_Uso (fecha, feature, modo, modelo, user_id, input_tokens, output_tokens, "
                            "thoughts_tokens, cached_tokens, campana_id, empresa_id, ref_id, status) "
                            "VALUES (?, 'auditoria', 'batch', 'gemini-3.8-flash', ?, ?, ?, ?, ?, ?, ?, ?, 'ok')",
                            momento + timedelta(hours=13), auditor, tokens_in, tokens_out, tokens_th,
                            rnd.randint(0, 6000), campana_id, empresa_id, f"auditoria:{auditoria_id}")

    # Corridas de auditoría (una por plantilla y semana) para "Log de auditorías".
    for campana_id, empresa_id, cliente, _, _ in CAMPANAS_DEMO:
        for semana in range(10, 0, -1):
            inicio = datetime.combine(hoy - timedelta(days=7 * semana), datetime.min.time()) + timedelta(hours=9)
            n = rnd.randint(20, 45)
            cur.execute("INSERT INTO calidad.AuditExecutionLog (trigger_source, modo, empresa, campana, plantilla_id, user_id, "
                        "fecha_desde, fecha_hasta, cantidad_solicitada, filas_auditadas, filas_error, input_tokens, "
                        "output_tokens, thoughts_tokens, status, started_at, finished_at) "
                        "VALUES (?, 'batch', ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 'EXITO', ?, ?)",
                        "scheduler" if semana % 2 else "manual", cliente, cliente, plantilla_de[campana_id], str(auditor),
                        (inicio - timedelta(days=7)).date(), inicio.date(), n, n, n * 15000, n * 900, n * 500,
                        inicio, inicio + timedelta(minutes=rnd.randint(20, 90)))

    # Consultas al chatbot de Voltara (para "Uso de IA" y "Solicitudes").
    preguntas = [
        ("¿Cuál es el plazo para normalizar un corte por falla en media tensión?",
         "Según el procedimiento de reclamos técnicos, el plazo estimado es de 4 a 8 horas; hay que informar el número de reclamo."),
        ("¿Cómo adhiere un cliente a la factura digital?",
         "Desde la oficina virtual, en la sección Facturación > Factura digital, con el número de cliente y un correo válido."),
        ("¿Qué hago si el cliente pide un reintegro por artefactos dañados?",
         "Se toma el reclamo por artefactos dañados con los datos del artefacto y se le informa que tiene 30 días para presentar el presupuesto."),
        ("¿Se puede dar de baja un servicio por teléfono?",
         "No: la baja requiere la presentación del titular con DNI en una oficina comercial o por la oficina virtual."),
    ]
    operador = staff[USUARIOS_DEMO[3][0]]
    for dia in range(30, 0, -1):
        for _ in range(rnd.randint(2, 9)):
            q, r = rnd.choice(preguntas)
            momento = datetime.combine(hoy - timedelta(days=dia), datetime.min.time()) + timedelta(minutes=rnd.randint(480, 1200))
            ti, to, te = rnd.randint(3000, 8000), rnd.randint(150, 450), rnd.randint(20, 60)
            cur.execute("INSERT INTO pagina_web.query_chatbots_logs (user_id, effective_campana, query, response, fecha, "
                        "input_tokens, output_tokens, embedding_tokens, active, sin_cobertura) VALUES (?, 'voltara', ?, ?, ?, ?, ?, ?, 1, 0)",
                        USUARIOS_DEMO[3][0], q, r, momento, ti, to, te)
            cur.execute("INSERT INTO pagina_web.IA_Uso (fecha, feature, modo, modelo, user_id, input_tokens, output_tokens, "
                        "embedding_tokens, ref_id, status) VALUES (?, 'chatbot', 'sync', 'gemini-3.5-flash-lite', ?, ?, ?, ?, ?, 'ok')",
                        momento, USUARIOS_DEMO[3][0], ti, to, te, f"chatbot:demo-{dia}-{rnd.randint(0, 10**9)}")

    # Un documento de conocimiento para el bot de Voltara (se indexa desde "Administrar chatbots").
    cur.execute("SELECT id FROM pagina_web.Chatbots WHERE slug = 'voltara'")
    fila = cur.fetchone()
    if fila:
        cur.execute("INSERT INTO pagina_web.ChatbotDocMarkdown (chatbot_id, titulo, orden, contenido_md, activo, created_at, updated_at) "
                    "VALUES (?, ?, 1, ?, 1, SYSUTCDATETIME(), SYSUTCDATETIME())", fila[0], "Procedimientos de reclamos (demo)",
                    "# Reclamos técnicos\n\n## Falta de suministro\n\nValidar el número de cliente, consultar si hay un corte "
                    "registrado en la zona e informar el plazo estimado (4 a 8 horas).\n\n## Artefactos dañados\n\nTomar el "
                    "reclamo con marca y modelo del artefacto. El cliente tiene 30 días para presentar el presupuesto.\n\n"
                    "# Trámites comerciales\n\n## Factura digital\n\nSe adhiere desde la oficina virtual con el número de "
                    "cliente y un correo válido.\n\n## Baja del servicio\n\nRequiere al titular con DNI; no se hace por teléfono.\n")

    llamadas = _cargar_planificador_gasur(cur, rnd, hoy, alta_nomina, legacy["Gasur"])

    print(f"Datos de demostración: {len(USUARIOS_DEMO)} usuarios, {nomina_id} personas en nómina, "
          f"{len(PLANTILLAS)} plantillas, {total} auditorías y {llamadas} llamadas de Gasur para el planificador.")


# ---------------------------------------------------------------------------------------------------------
# Planificador: historia de llamadas de Gasur (la campaña con la fuente más simple: una fila por llamada)
# ---------------------------------------------------------------------------------------------------------

DIAS_HISTORIA = 440
LOGINS_GASUR = list(range(500, 522))
# Peso de cada media hora entre las 08:00 y las 22:30: pico de mañana y otro al anochecer (calefacción).
PERFIL_INTRADIA = [1, 2, 3, 4, 5, 5, 5, 4.5, 4, 3.5, 3.2, 3, 3.2, 3.5, 3.8, 4, 4.5, 5, 5.5, 5.8, 5.5, 5,
                   4.4, 3.8, 3.2, 2.6, 2, 1.6, 1.2, 0.8]
FACTOR_DIA_SEMANA = [1.15, 1.05, 1.0, 1.0, 0.95, 0.7, 0.55]
TURNOS = [("08:00", "15:00"), ("12:00", "19:00"), ("16:00", "23:00")]


def _temperatura_media(dia) -> float:
    """Clima de Montevideo, más o menos: 23° en enero, 11° en julio."""
    import math
    return 17 + 6 * math.cos(2 * math.pi * (dia.timetuple().tm_yday - 20) / 365)


def _insertar_lotes(cur, sql_base: str, columnas: int, filas: list[tuple]) -> None:
    """INSERT de varias filas por sentencia (el límite de SQL Server es 2.100 parámetros)."""
    por_lote = max(1, 2000 // columnas)
    marcas = "(" + ", ".join("?" * columnas) + ")"
    for i in range(0, len(filas), por_lote):
        lote = filas[i:i + por_lote]
        cur.execute(sql_base + " VALUES " + ", ".join([marcas] * len(lote)), [v for f in lote for v in f])


def _cargar_planificador_gasur(cur, rnd, hoy, alta_nomina, campana_rrhh: int) -> int:
    import math
    from datetime import datetime, time, timedelta

    ahora = datetime.now()
    # Personas: un login por operador (en Gasur el login es el número de la central).
    operadores = {}
    for login in LOGINS_GASUR:
        ingreso = hoy - timedelta(days=rnd.randint(40, 700))
        documento = 50000000 + login
        nid = alta_nomina(documento, rnd.choice(NOMBRES), rnd.choice(APELLIDOS), ingreso)
        cur.execute("INSERT INTO operadores (legajo_id, estado, campana_id, fecha_desde, puesto_id) OUTPUT INSERTED.id "
                    "VALUES (?, 1, ?, ?, 3)", nid, campana_rrhh, datetime.combine(ingreso, time()))
        operadores[login] = cur.fetchone()[0]
        cur.execute("INSERT INTO usuarios (nomina_id, usuario) VALUES (?, ?)", documento, str(login))

    # Clima: observado hasta ayer y pronóstico a una semana.
    temperatura = {}
    desvio = 0.0
    for k in range(DIAS_HISTORIA, -8, -1):
        dia = hoy - timedelta(days=k)
        desvio = 0.7 * desvio + rnd.gauss(0, 1.8)
        temperatura[dia] = _temperatura_media(dia) + desvio
    cur.execute("DELETE FROM planificacion.Clima WHERE CampanaID = 30")
    _insertar_lotes(cur, "INSERT INTO planificacion.Clima (CampanaID, Fecha, TempMax, TempMin, TempMedia, AparenteMax, "
                    "AparenteMin, LluviaMm, VientoKmh, RafagaKmh, HumedadPct, EsPronostico, Origen, ActualizadoEn)", 14,
                    [(30, d, round(tm + 5, 1), round(tm - 5, 1), round(tm, 1), round(tm + 4, 1), round(tm - 7, 1),
                      round(max(0.0, rnd.gauss(-1, 6)), 1), round(rnd.uniform(8, 30), 1), round(rnd.uniform(20, 55), 1),
                      round(rnd.uniform(55, 90), 1), 1 if d >= hoy else 0, "demo", ahora)
                     for d, tm in temperatura.items()])

    def volumen_esperado(dia) -> float:
        return 260 * math.exp(0.11 * (18 - temperatura[dia])) * FACTOR_DIA_SEMANA[dia.weekday()]

    peso_total = sum(PERFIL_INTRADIA)
    llamadas, actividad, auxiliares, payroll = [], [], [], []
    for k in range(DIAS_HISTORIA, -1, -1):
        dia = hoy - timedelta(days=k)
        volumen = volumen_esperado(dia) * math.exp(rnd.gauss(0, 0.08))
        # Dotación del día: tres turnos que cubren la curva con ~80 % de ocupación.
        carga = [volumen * w / peso_total * 210 / 1800 for w in PERFIL_INTRADIA]  # agentes-hora por media hora
        necesarios = [math.ceil(c / 0.8) + 1 for c in carga]
        n_man = max(necesarios[:8])
        n_noche = max(necesarios[22:])
        n_medio = max(0, max(necesarios[8:22]) - (n_man + n_noche) // 2)
        disponibles = LOGINS_GASUR[:]
        rnd.shuffle(disponibles)
        en_turno: dict[int, list[int]] = {s: [] for s in range(30)}
        asignados = []
        for (ini, fin), n in zip(TURNOS, (n_man, n_medio, n_noche)):
            for _ in range(min(n, len(disponibles))):
                login = disponibles.pop()
                asignados.append((login, ini, fin))
        for login, ini, fin in asignados:
            h_ini = datetime.combine(dia, time.fromisoformat(ini))
            h_fin = datetime.combine(dia, time.fromisoformat(fin))
            falto = rnd.random() < 0.05
            payroll.append((operadores[login], dia, h_ini, h_fin, "ABS" if falto else "P", 7.0, 0.0 if falto else 7.0))
            if falto or dia == hoy:
                continue
            actividad.append((f"calla{login} ({login})", dia, h_ini.time(), (h_fin + timedelta(minutes=rnd.randint(0, 6))).time()))
            auxiliares.append((f"calla{login} ({login})", rnd.randint(300, 900), rnd.randint(100, 500), 1800, 600, dia))
            for s in range(30):
                if h_ini <= datetime.combine(dia, time(8)) + timedelta(minutes=30 * s) < h_fin:
                    en_turno[s].append(login)
        for s, w in enumerate(PERFIL_INTRADIA):
            inicio = datetime.combine(dia, time(8)) + timedelta(minutes=30 * s)
            if inicio >= ahora:
                break
            n = int(rnd.gauss(volumen * w / peso_total, math.sqrt(max(1.0, volumen * w / peso_total))))
            gente = en_turno[s] or LOGINS_GASUR[:3]
            ocupacion = min(0.97, carga[s] / max(1, len(gente)))
            for _ in range(max(0, n)):
                momento = inicio + timedelta(seconds=rnd.randint(0, 1799))
                espera = int(rnd.expovariate(1 / (4 + 60 * ocupacion ** 6)))
                abandona = espera > rnd.expovariate(1 / 208)
                login = rnd.choice(gente)
                llamadas.append((float(rnd.randint(10**9, 10**10)), momento, "C100C", "09" + str(rnd.randint(1000000, 9999999)),
                                 "C100C", min(espera, 32000), 0 if abandona else min(32000, int(rnd.lognormvariate(5.25, 0.5))),
                                 "Entrante", "Abandonada" if abandona else "Completada",
                                 None if abandona else f"calla{login} ({login})", "Montevideo"))

    _insertar_lotes(cur, "INSERT INTO dbo.[Gasur Llamadas] (Id, Fecha, Cola, [Orígen], Destino, [Espera (seg.)], "
                    "[Duración (seg.)], Tipo, Estado, Agente, Localidad)", 11, llamadas)
    _insertar_lotes(cur, "INSERT INTO dbo.[Gasur Actividad] (Agente, Fecha, Login, Logout)", 4, actividad)
    _insertar_lotes(cur, "INSERT INTO dbo.[Gasur Auxiliares] (Agente, Tareas, Varios, [30 minutos], [10 minutos], Fecha)", 6, auxiliares)
    _insertar_lotes(cur, "INSERT INTO dbo.payroll (id_operadores, fecha, inicio, final, codigo, horas_programadas, "
                    "horas_trabajadas)", 7, payroll)

    # Malla publicada (turnos del próximo mes) y forecast del cliente (por hora, sin skill).
    futuro, forecast = [], []
    for k in range(0, 32):
        dia = hoy + timedelta(days=k)
        if dia not in temperatura:
            temperatura[dia] = _temperatura_media(dia)
        volumen = volumen_esperado(dia)
        carga_pico = volumen * max(PERFIL_INTRADIA) / peso_total * 210 / 1800
        por_turno = math.ceil(carga_pico / 0.8)
        logins = LOGINS_GASUR[:]
        rnd.shuffle(logins)
        for (ini, fin) in TURNOS:
            for _ in range(min(por_turno, len(logins))):
                futuro.append((operadores[logins.pop()], dia, time.fromisoformat(ini), time.fromisoformat(fin), "P", 7.0))
    for k in range(-60, 17):
        dia = hoy + timedelta(days=k)
        volumen = volumen_esperado(dia) if dia in temperatura else 400
        for h in range(8, 23):
            w = PERFIL_INTRADIA[(h - 8) * 2] + PERFIL_INTRADIA[(h - 8) * 2 + 1]
            forecast.append((dia, time(h), int(volumen * w / peso_total * math.exp(rnd.gauss(0, 0.12))), "Gasur", None))
    _insertar_lotes(cur, "INSERT INTO dbo.payroll_futuro (id_operadores, fecha, inicio, final, codigo, horas_programadas)", 6, futuro)
    _insertar_lotes(cur, "INSERT INTO dbo.Forecast (Fecha, Intervalo, Forecast, [Campaña], [Skill ID])", 5, forecast)

    # Las otras dos campañas del planificador no tienen historia inventada: se apagan en la demo.
    cur.execute("UPDATE planificacion.Campana SET Activa = 0 WHERE CampanaID IN (1, 20)")
    return len(llamadas)
