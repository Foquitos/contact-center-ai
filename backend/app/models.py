from datetime import datetime
from pydantic import BaseModel, Field, EmailStr
from typing import Any, Optional, Dict, List

# --- Modelos de Respuesta Generales ---
class CampaignListResponse(BaseModel):
    campaigns: List[str]

class Message(BaseModel):
    message: str

class ConsultarResponse(BaseModel):
    response: str
    context: Optional[str] = None
    user: int
    campana: Optional[str] = None

class AuditoriaResponse(BaseModel):
    id: Optional[str] = None
    nota: Optional[float] = None
    fecha: Optional[str] = None

class InformacionCSVResponse(BaseModel):
    texto: Optional[str] = None

class SeccionesCSVResponse(BaseModel):
    secciones: Optional[Dict[str,Dict[str,Dict[str,list[str]]]]] = None

class TipsResponse(BaseModel):
    tips: Optional[str] = None
    title: Optional[str] = None
    group_name: Optional[str] = None
    id: Optional[int] = None
    tipo: str = "info"
    es_prioritario: bool = False
    url_accion: Optional[str] = None
    texto_accion: Optional[str] = None
    likes_count: int = 0
    user_voted: bool = False

# Modelos para Tips del Día y Grupos de Tips
class TipGroupCreate(BaseModel):
    name: str = Field(..., max_length=100)
    description: Optional[str] = Field(None, max_length=255)
    activo: bool = True
    role_ids: List[int] = []

class TipGroupUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = Field(None, max_length=255)
    activo: Optional[bool] = None
    role_ids: Optional[List[int]] = None

class TipGroupResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    activo: bool = True
    role_ids: List[int] = []
    roles_nombres: List[str] = []
    tips_count: int = 0
    usuarios_alcanzados: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class TipCreate(BaseModel):
    group_id: int
    title: Optional[str] = Field(None, max_length=150)
    content: str
    tipo: str = Field("info", max_length=20)
    fecha_desde: Optional[str] = None
    fecha_hasta: Optional[str] = None
    es_prioritario: bool = False
    url_accion: Optional[str] = Field(None, max_length=500)
    texto_accion: Optional[str] = Field(None, max_length=100)
    activo: bool = True

class TipUpdate(BaseModel):
    group_id: Optional[int] = None
    title: Optional[str] = Field(None, max_length=150)
    content: Optional[str] = None
    tipo: Optional[str] = Field(None, max_length=20)
    fecha_desde: Optional[str] = None
    fecha_hasta: Optional[str] = None
    es_prioritario: Optional[bool] = None
    url_accion: Optional[str] = Field(None, max_length=500)
    texto_accion: Optional[str] = Field(None, max_length=100)
    activo: Optional[bool] = None

class TipResponseItem(BaseModel):
    id: int
    group_id: int
    group_name: Optional[str] = None
    title: Optional[str] = None
    content: str
    tipo: str = "info"
    fecha_desde: Optional[str] = None
    fecha_hasta: Optional[str] = None
    es_prioritario: bool = False
    url_accion: Optional[str] = None
    texto_accion: Optional[str] = None
    feedback_count: int = 0
    activo: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

class TipFeedbackResponse(BaseModel):
    ok: bool = True
    likes_count: int = 0
    user_voted: bool = True

class EmbeddingStatusResponse(BaseModel):
    quantity: Optional[int] = None
    campana: str
    user: int
    status: str

class CalificacionRequest(BaseModel):
    calificacion: int = Field(..., ge=1, le=10)
    comentario: Optional[str] = Field(None, max_length=500)

class CalificacionResponse(BaseModel):
    task_id: str
    calificacion_guardada: int
    mensaje: str

class EmpresaCampanaItem(BaseModel):
    Empresa: str
    Campaña: str
    Tipificacion: str

class AuditStatusResponse(BaseModel):
    task_id: str
    status: str
    error: Optional[str] = None

# --- Modelos de Plantillas ---
class Plantilla(BaseModel):
    nombre: str
    descripcion: Optional[str]
    system_prompt: str
    campanas_id: Optional[int] = None
    recordatorio: Optional[str] = None
    # Modelo de Gemini que usa la plantilla para auditar. Solo puede venir
    # seteado (y solo se persiste) si quien llama tiene el permiso especial
    # 'template:modelo_ia' (ver app/routers/planillas_prompts.py).
    # DESDE 2026-08-19 ya no se ofrece en la UI: todas las plantillas auditan con
    # el mismo modelo y lo que se elige es `nivel_razonamiento`. El campo sigue
    # existiendo para no romper el historial de costos ni las plantillas viejas.
    modelo_ia: Optional[str] = None
    # Cuánto piensa Gemini antes de responder: LOW / MEDIUM / HIGH (ver
    # AuditorIA/razonamiento.py). Reemplazó al selector de modelo como perilla de
    # costo/calidad y va detrás del MISMO permiso 'template:modelo_ia'.
    nivel_razonamiento: Optional[str] = None

class PlantillasIA(BaseModel):
    system_prompts: str
    text: str
    response_schema: str
    # Modelo de Gemini configurado en la plantilla (o el default global si la
    # plantilla no tiene uno seteado). Ver AuditorIA/modelos_ia.py.
    modelo: str
    # Nivel de razonamiento de la plantilla (ver AuditorIA/razonamiento.py). None
    # = la plantilla no lo tiene seteado y se usa el default; se normaliza en
    # gemini.prompt(), que es donde se arma la config del llamado.
    nivel_razonamiento: Optional[str] = None

class Atributo(BaseModel):
    nombre: str
    prompt: str
    tipo: str
    restricciones: Optional[Dict[str, Any]] = None
    orden: int
    DarAviso: Optional[bool] = False
    FrasesAviso: Optional[str] = None
    ponderacion: Optional[float] = 0  # peso relativo para puntaje ponderado (0 = no participa)
    # True = la IA puede omitir el atributo cuando la interacción no da evidencia para
    # responderlo (queda sin auditar en lugar de forzar una respuesta que penalice mal).
    # None = no se modifica (útil en el PUT parcial que hace el reordenamiento).
    es_opcional: Optional[bool] = None

class Atributos(BaseModel):
    atributos: List[Atributo]

# --- Asistente de IA para plantillas (mejorar / generar prompts) ---
class MejorarPromptRequest(BaseModel):
    # Qué texto se quiere mejorar: "system_prompt" | "recordatorio" | "atributo"
    campo: str
    texto_actual: str = ""
    # Pedido opcional del usuario ("quiero agregar X / cambiar Y"). Vacío = mejora automática.
    instruccion_usuario: Optional[str] = None
    # Contexto de la plantilla para que la IA entienda el conjunto (todo opcional).
    contexto: Optional[Dict[str, Any]] = None
    # Plantilla que se está editando. Solo se usa para resolver QUÉ datos del llamado
    # recibe la IA en esa campaña (bloque Call_details, ver AuditorIA/call_details.py):
    # sin esto el asistente propone criterios sobre campos que no existen, o ignora los
    # que sí están.
    plantilla_id: Optional[int] = None

class GenerarPlantillaRequest(BaseModel):
    descripcion_usuario: str
    contexto: Optional[Dict[str, Any]] = None
    # La plantilla todavía no existe: los campos del Call_details se resuelven por la
    # campaña, que es la que determina el builder.
    campana_id: Optional[int] = None

# --- Revisión integral de la plantilla (mejora masiva) ---
# Dos pasos separados a propósito: revisar NO escribe nada (devuelve un plan de
# cambios) y aplicar recibe solo lo que el usuario dejó tildado. Ver
# AuditorIA/asistente_plantillas.py::revisar_plantilla.
class RevisarPlantillaRequest(BaseModel):
    # Estado que el usuario está viendo en el editor (incluye lo que todavía no guardó).
    # Si no viene, el backend lee la plantilla de la BD.
    plantilla: Optional[Dict[str, Any]] = None
    # Pedido puntual ("sumá un criterio de mora"). Vacío = revisión general.
    instruccion_usuario: Optional[str] = None
    # Claves de asistente_plantillas.FOCOS_REVISION a priorizar.
    foco: Optional[List[str]] = None
    # False = la IA no propone bajas de atributos (es el default: eliminar es la única
    # acción de la revisión que apaga un criterio que hoy se está midiendo).
    permitir_eliminar: bool = False
    # True = se le pasan a la IA los datos de cómo viene funcionando la plantilla
    # (distribución de respuestas y correcciones de los auditores, ver
    # AuditorIA/evidencia_plantilla.py). Son un par de consultas agregadas contra la
    # base productiva; el flag permite apagarlas.
    usar_evidencia: bool = True

class RevisarAtributoRequest(BaseModel):
    """Re-pedido de UN cambio de la revisión ("no me convence, hacelo así")."""
    # Atributo existente sobre el que se propone. None = el cambio era un alta.
    atributo_id: Optional[int] = None
    accion: str = "modificar"
    instruccion_usuario: str
    # La propuesta que se le mostró y no le gustó: es el punto de partida del re-pedido.
    propuesta_previa: Optional[Dict[str, Any]] = None
    usar_evidencia: bool = True

class AtributoRevisado(BaseModel):
    """Un atributo tal como quedaría al aplicar la revisión.

    `id` viaja solo en los cambios sobre atributos existentes y SIEMPRE se valida
    contra la plantilla del path: sin eso, un id de otra plantilla (de otra empresa)
    entraría por el body y se modificaría igual."""
    id: Optional[int] = None
    nombre: str
    prompt: str
    tipo: str
    restricciones: Optional[Dict[str, Any]] = None
    orden: Optional[int] = None
    ponderacion: Optional[float] = None
    es_opcional: Optional[bool] = None
    DarAviso: Optional[bool] = None
    FrasesAviso: Optional[str] = None

class CabeceraRevisada(BaseModel):
    nombre: Optional[str] = None
    descripcion: Optional[str] = None
    system_prompt: Optional[str] = None
    recordatorio: Optional[str] = None

class AplicarRevisionRequest(BaseModel):
    cabecera: Optional[CabeceraRevisada] = None
    modificar: List[AtributoRevisado] = []
    agregar: List[AtributoRevisado] = []
    eliminar: List[int] = []

# --- Alta manual de una versión de plantilla ("Guardar en el historial") ---
# Una versión nace sola cuando la plantilla audita (ver AuditorIA/versionado.py); esto
# es el atajo para dejar un punto de retorno SIN gastar una corrida.
class GuardarVersionRequest(BaseModel):
    # Por qué se guarda ("antes de reescribir el saludo"). Opcional, pero es lo único
    # que distingue en la lista una versión guardada a mano de una que nació auditando.
    motivo: Optional[str] = None

class ConocimientoPlantillaRequest(BaseModel):
    # Documentos de los chatbots (pagina_web.ChatbotDocMarkdown.id) que la IA lee al
    # auditar con la plantilla. Reemplaza la selección entera; vacío = ninguno.
    doc_ids: List[int] = []

class DuplicarPlantillaRequest(BaseModel):
    # Nombre de la copia. Si no viene, el backend arma "<nombre> (copia)".
    nombre: Optional[str] = None
    # Campaña destino. Si no viene, la copia queda en la campaña del original.
    campana_id: Optional[int] = None

class Skilldelete(BaseModel):
    campana_id: int
    skills: List[str]

# --- NUEVOS MODELOS DE SEGURIDAD (RBAC) ---

class Permission(BaseModel):
    id: int
    code: str
    description: Optional[str] = None

class Role(BaseModel):
    id: int
    name: str
    is_super_admin: bool
    description: Optional[str] = None
    parent_role_id: Optional[int] = None
    parent_name: Optional[str] = None
    users_count: int = 0
    editable: bool = False  # si el usuario actual puede editarlo/borrarlo (delegación)
    simulable: bool = False  # si el usuario actual puede simularlo (roles:impersonate)
    permissions: List[Permission] = []

class RoleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    parent_role_id: Optional[int] = None
    permissions_ids: List[int] = []

class User(BaseModel):
    usuario: int
    Nombre: Optional[str] = None
    password: Optional[str] = None
    campana: Optional[str] = None
    # Campos RBAC
    roles: List[str] = []
    permissions: List[str] = []
    is_super_admin: bool = False
    # True => el usuario debe cambiar su contraseña antes de operar (primer
    # ingreso / contraseña temporal seteada por un admin). La contraseña vieja
    # sigue siendo válida para autenticarse; el frontend lo redirige a cambiarla.
    must_change_password: bool = False
    # Simulación de rol: seteados cuando el token trae el claim imp_role
    # (los permisos/roles de arriba ya son los del rol simulado)
    simulando_rol_id: Optional[int] = None
    simulando_rol: Optional[str] = None

class Token(BaseModel):
    access_token: str
    token_type: str
    # Estos campos deben coincidir con lo que devuelve auth.py
    user_display_name: str
    permissions: List[str]
    is_super_admin: bool
    campana: Optional[str] = None
    expires_at: Optional[str] = None
    # True => el frontend debe forzar el cambio de contraseña tras el login
    must_change_password: bool = False

class RoleDetail(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    is_super_admin: bool
    parent_role_id: Optional[int] = None
    permissions_ids: List[int] = [] # Permisos PROPIOS (editables en checkboxes)
    inherited_permissions_ids: List[int] = [] # Heredados de ancestros (solo lectura en la UI)

class UserDetail(BaseModel):
    usuario: int
    Nombre: Optional[str] = None
    campana: Optional[str] = None
    role_ids: List[int] = [] # Roles actuales para pre-marcar el multi-select

class RoleAssignment(BaseModel):
    role_ids: List[int] = [] # Set COMPLETO de roles del usuario (reemplaza los anteriores)

class RoleUser(BaseModel):
    documento: int
    nombre: Optional[str] = None
    campana: Optional[str] = None

class BulkUserCreate(BaseModel):
    documents: List[int]
    role_id: Optional[int] = None

# --- CHATBOTS EN BD (pagina_web.Chatbots) ---

class ChatbotDisponible(BaseModel):
    """Un bot que el usuario actual puede usar (para el selector del frontend)."""
    slug: str
    nombre: str
    descripcion: Optional[str] = None
    # Si ESTE bot acepta capturas/PDF (Chatbots.permite_adjuntos). El clip se
    # prende y apaga al cambiar de bot en el selector.
    permite_adjuntos: bool = False

class ChatbotsDisponiblesResponse(BaseModel):
    bots: List[ChatbotDisponible]
    # Bot preseleccionado: el resuelto por PCRC para operadores CSV, o el único
    # permitido. None => el usuario debe elegir en el selector.
    default_slug: Optional[str] = None
    # Si el USUARIO tiene el permiso para adjuntar (chatbot.adjuntos). Que además
    # pueda hacerlo depende del bot elegido (ChatbotDisponible.permite_adjuntos).
    # Viaja desde el backend, y no como config propia del frontend, para que el clip
    # de la UI y lo que el endpoint realmente acepta no queden desincronizados.
    adjuntos_habilitados: bool = False

class ChatbotAdminCreate(BaseModel):
    nombre: str
    slug: Optional[str] = None          # si falta se autogenera del nombre
    descripcion: Optional[str] = None
    es_csv: bool = False
    system_prompt: str
    pcrcs: List[str] = []               # solo aplica si es_csv

class ChatbotAdminUpdate(BaseModel):
    # Todos opcionales; slug y grupo NO son editables.
    nombre: Optional[str] = None
    descripcion: Optional[str] = None
    system_prompt: Optional[str] = None
    pcrcs: Optional[List[str]] = None           # reemplazo completo si viene

class ChatbotAdminOut(BaseModel):
    id: int
    slug: str
    nombre: str
    descripcion: Optional[str] = None
    es_csv: bool
    activo: bool
    permission_code: str
    system_prompt: str
    index_version: int
    # None cuando el entorno todavía no publicó índice (sin fila en ChatbotIndexState);
    # el panel lo muestra como "sin indexar".
    index_status: Optional[str] = None
    last_indexed_at: Optional[datetime] = None
    docs_md_count: int = 0                 # documentos del conocimiento (ChatbotDocMarkdown)
    pcrcs: List[str] = []
    job_pendiente: Optional[str] = None   # 'pending'/'running' si hay job en cola
    # Documentos tocados después de la última indexación de ESTE entorno: el índice
    # que está respondiendo no incluye esos cambios.
    cambios_sin_indexar: bool = False
    material_pendiente: int = 0           # ítems en la bandeja del asistente

class IndexJobOut(BaseModel):
    id: int
    chatbot_slug: str
    status: str
    requested_by: Optional[int] = None
    error: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

class ReindexResponse(BaseModel):
    job_id: int
    status: str

# --- Asistente de documentación RAG (formatear/mergear docs de chatbots) ---
class FuenteCrudaIn(BaseModel):
    """Una fuente de contenido crudo a formatear: texto pegado o un archivo (base64)."""
    tipo: str                              # 'texto' | 'archivo'
    texto: Optional[str] = None            # si tipo == 'texto'
    datos_base64: Optional[str] = None     # si tipo == 'archivo' (contenido en base64)
    mime: Optional[str] = None             # si tipo == 'archivo'
    nombre: Optional[str] = None           # etiqueta / nombre de archivo

class DocFormatearRequest(BaseModel):
    fuentes: List[FuenteCrudaIn] = []
    # Material ya guardado en la bandeja (pagina_web.ChatbotDocMaterial) a incluir en
    # esta corrida. None = todo lo que haya pendiente del bot al momento de encolar.
    material_ids: Optional[List[int]] = None
    titulo: Optional[str] = None
    chatbot_id: Optional[int] = None       # solo para ambientar el prompt (empresa/campaña)
    # Si es False, fuerza un único documento aunque el material mezcle temas.
    permitir_separacion: bool = True

class DocMergeRequest(BaseModel):
    """Agrega información nueva a la documentación existente de un bot.

    La IA ve los documentos candidatos y rutea cada bloque nuevo al que corresponda
    (puede tocar varios). Por defecto los candidatos son TODOS los del bot; con
    `doc_ids` se acota a algunos."""
    chatbot_id: int
    fuentes: List[FuenteCrudaIn] = []
    material_ids: Optional[List[int]] = None   # ver DocFormatearRequest
    doc_ids: Optional[List[int]] = None    # None = todos los documentos del bot
    permitir_crear: bool = True            # puede crear un documento nuevo si nada encaja

# --- Bandeja de material pendiente (pagina_web.ChatbotDocMaterial) ---
class DocMaterialIn(BaseModel):
    """Material que se guarda para procesar DESPUÉS, sin llamar a la IA todavía."""
    fuentes: List[FuenteCrudaIn]
    # Aclaración de quien lo carga ("esto reemplaza el punto 3"); viaja a la IA junto
    # con el material como contexto del bloque.
    nota: Optional[str] = None
    # Documento al que apuntaba quien lo cargó (botón de la fila). Es una indicación
    # para la IA, no una imposición: la corrida sigue siendo un merge global.
    doc_id_destino: Optional[int] = None
    # Marca si el material es una modificación/reemplazo de precios, procedimientos o baja de secciones.
    es_actualizacion: bool = False

class DocMaterialOut(BaseModel):
    id: int
    chatbot_id: int
    tipo: str                              # 'texto' | 'archivo'
    nombre: Optional[str] = None
    mime: Optional[str] = None
    tamano_bytes: int = 0
    nota: Optional[str] = None
    doc_id_destino: Optional[int] = None
    destino_titulo: Optional[str] = None    # título del documento destino, para la lista
    es_actualizacion: bool = False
    preview: Optional[str] = None          # primeros caracteres, solo en 'texto'
    created_by: Optional[int] = None
    created_at: Optional[datetime] = None

class DocPropuestoOut(BaseModel):
    """Un documento dentro de una propuesta (nuevo o modificado)."""
    doc_id: Optional[int] = None           # None = documento nuevo
    titulo: str
    markdown: str
    secciones: List[str] = []
    accion: str = "crear"                  # 'crear' | 'actualizar'
    cambios: List[str] = []

class DocPropuestaOut(BaseModel):
    """Propuesta devuelta por el asistente: no se persiste hasta que el usuario acepta.
    Puede traer VARIOS documentos (separados al formatear, o ruteados al agregar info)."""
    documentos: List[DocPropuestoOut] = []
    # Lo que del material resultó ser una TABLA de datos y no prosa. Sale del
    # mismo procesamiento: para quien carga no hay dos flujos, la IA decide.
    tablas: List["TablaPropuestaOut"] = []
    # Cuando el material se procesó CONTRA una tabla existente, qué le haría.
    diff: Optional["TablaDiffOut"] = None
    motivo_separacion: str = ""             # por qué se separó (al formatear)
    ruteo: List[str] = []                   # a qué documento fue cada bloque (al agregar info)
    notas: List[str] = []
    faltantes_residuales: List[Dict[str, Any]] = []
    invenciones_residuales: List[str] = []
    conflictos: List[Dict[str, Any]] = []
    rondas_verificacion: int = 0
    verificado_ok: bool = False
    # Material de la bandeja que entró en esta propuesta: se borra recién cuando el
    # usuario la acepta (si la descarta, sigue pendiente y se puede reprocesar).
    material_ids: List[int] = []

# --- Imágenes de un chatbot (pagina_web.ChatbotImagenes) ---
class ChatbotImagenOut(BaseModel):
    id: int
    chatbot_id: int
    nombre: str
    mime: str
    tamano_bytes: int = 0
    ancho: Optional[int] = None
    alto: Optional[int] = None
    descripcion: Optional[str] = None
    hash_sha256: Optional[str] = None
    created_by: Optional[int] = None
    created_at: Optional[datetime] = None
    url: str
    markdown: str

class ChatbotImagenIn(BaseModel):
    nombre: str
    mime: str
    datos_base64: str
    descripcion: Optional[str] = None

# --- Tablas de datos de un chatbot (pagina_web.ChatbotTabla) ---
# Conocimiento que no es prosa sino entidad -> atributos (las bases de vantix, la
# cartera de cobranzas de benefix). Ver app/chatbot_tablas.py.
class TablaColumna(BaseModel):
    nombre: str
    descripcion: str = ""
    # ¿Un operador podría nombrar una fila por este valor? Es sobre las columnas
    # clave que se busca en modo lookup.
    clave: bool = False

class TablaPropuestaOut(BaseModel):
    """Tabla detectada por la IA dentro del material. No se persiste hasta que el
    usuario acepta, igual que una propuesta de documento."""
    nombre: str
    descripcion: str = ""
    terminos: List[str] = []
    columnas: List[TablaColumna] = []
    nota: str = ""
    modo: str = "auto"
    # Las filas las parsea el backend del markdown, NO las escribe el modelo.
    filas: List[Dict[str, str]] = []
    filas_total: int = 0
    duplicadas: int = 0                     # filas repetidas que se descartaron
    sospechosas: List[str] = []             # claves que no aparecen en el origen
    motivo: str = ""                        # por qué la IA dice que es una tabla
    notas: List[str] = []
    doc_origen_ids: List[int] = []          # documentos de los que se convirtió
    doc_titulo_origen: Optional[str] = None

class TablaOut(BaseModel):
    id: int
    nombre: str
    descripcion: str = ""
    terminos: List[str] = []
    columnas: List[TablaColumna] = []
    nota: str = ""
    modo: str = "auto"
    # El modo REAL con el que se responde: con 'auto', saberlo es la diferencia
    # entre "la tabla entra entera en el prompt" y "se busca por clave".
    modo_efectivo: str = "completa"
    filas_total: int = 0
    doc_origen_id: Optional[int] = None
    tiene_vector: bool = False
    # Filas que un analista corrigió a mano desde la última carga masiva. Se
    # muestran para que se sepa qué se perdería al recargar la tabla entera.
    editadas: int = 0
    updated_at: Optional[datetime] = None

class TablaGuardarIn(BaseModel):
    """Acepta una propuesta (o reemplaza una tabla existente con filas nuevas)."""
    nombre: str
    descripcion: str = ""
    terminos: List[str] = []
    columnas: List[TablaColumna]
    nota: str = ""
    modo: str = "auto"
    filas: List[Dict[str, str]] = []
    doc_origen_ids: List[int] = []
    # Los documentos de los que salió se desactivan al guardar: dejar las dos
    # fuentes vivas es garantizar que se desincronicen, y además sus filas
    # seguirían compitiendo en el índice contra la tabla que las reemplaza.
    desactivar_doc_origen: bool = True
    # Material de la bandeja que originó esta tabla. Se consume al aceptar, igual
    # que con los documentos. Va acá porque una propuesta puede ser SOLO tablas
    # (una planilla, o la conversión de un documento) y entonces no hay ningún
    # guardado de documentos donde consumirlo.
    material_ids: List[int] = []

class TablaMetadataIn(BaseModel):
    """Edición de la definición, sin tocar las filas."""
    nombre: Optional[str] = None
    descripcion: str = ""
    terminos: List[str] = []
    claves: List[str] = []                  # nombres de las columnas clave
    nota: str = ""
    modo: str = "auto"

class TablaFilaOut(BaseModel):
    id: int
    datos: Dict[str, str] = {}
    # True = un analista la corrigió a mano después de la última carga masiva.
    # La pantalla lo usa para avisar qué se pierde al reemplazar la tabla entera.
    editada: bool = False

class TablaFilasOut(BaseModel):
    total: int
    filas: List[TablaFilaOut] = []

class TablaFilaIn(BaseModel):
    """Alta o corrección de UNA fila. Solo se guardan las columnas declaradas en
    la tabla; una clave vacía se rechaza (sería una fila que nadie puede encontrar)."""
    datos: Dict[str, str]

class TablaCambioOut(BaseModel):
    """Una fila que el material entrante modificaría."""
    fila_id: int
    clave: str                              # cómo se la nombra (columnas clave)
    antes: Dict[str, str] = {}
    despues: Dict[str, str] = {}
    columnas: List[str] = []                # solo las que realmente cambian
    # La fila la había corregido un analista: la carga la pisa. Es el conflicto
    # que hay que mostrar, no resolver solo.
    editada_a_mano: bool = False

class TablaBajaOut(BaseModel):
    fila_id: int
    clave: str
    datos: Dict[str, str] = {}

class TablaDiffOut(BaseModel):
    """Qué le haría a la tabla el material cargado. No se aplica hasta aprobarlo:
    una carga que borra 400 filas porque el archivo vino cortado es indistinguible
    de una baja masiva legítima si no se mira antes."""
    tabla_id: int
    tabla_nombre: str = ""
    modo_carga: str = "incremental"
    columnas: List[str] = []
    claves: List[str] = []
    altas: List[Dict[str, str]] = []
    cambios: List[TablaCambioOut] = []
    bajas: List[TablaBajaOut] = []
    sin_cambios: int = 0
    notas: List[str] = []
    material_ids: List[int] = []

class TablaActualizarRequest(BaseModel):
    """Encola la comparación del material de la bandeja contra una tabla."""
    material_ids: Optional[List[int]] = None   # None = todo lo pendiente del bot
    fuentes: List[FuenteCrudaIn] = []
    # 'incremental' = el material trae solo novedades (nunca da de baja).
    # 'reemplazo'   = el material es la tabla completa: lo que no viene se da de baja.
    # No hay default razonable: son resultados opuestos sobre los mismos datos,
    # así que lo elige quien carga.
    modo_carga: str = "incremental"

class TablaAplicarIn(BaseModel):
    """Lo que el usuario aprobó del diff. Solo viaja lo tildado."""
    altas: List[Dict[str, str]] = []
    cambios: List[TablaCambioOut] = []
    bajas: List[int] = []                   # ids de fila
    material_ids: List[int] = []

class TablaConvertirIn(BaseModel):
    """Convierte uno o varios documentos ya cargados en UNA tabla de datos.

    Varios documentos dan UNA tabla, no una por documento: la cartera de cobranzas
    de benefix está repartida en 10 documentos (uno por gestor) y el gestor es un
    VALOR de columna. Una tabla por gestor perdería las consultas cruzadas y
    obligaría a tocar dos tablas cada vez que un cliente cambia de responsable."""
    doc_ids: List[int]

class TablaDesdeMaterialRequest(BaseModel):
    """Encola el análisis de una planilla o listado para crear una NUEVA tabla de datos."""
    material_ids: Optional[List[int]] = None   # None = todo lo pendiente del bot
    fuentes: List[FuenteCrudaIn] = []

class DocJobOut(BaseModel):
    """Acuse de encolado del asistente: el trabajo real lo hace el scheduler."""
    job_id: int
    status: str

class DocJobEstadoOut(BaseModel):
    """Estado del trabajo, para el polling del navegador.

    `resultado` solo viene con status='done'; `error` solo con status='failed' (y es
    el texto que se le muestra al usuario, no un stacktrace)."""
    job_id: int
    status: str                             # pending | running | done | failed
    tipo: str                               # formatear | merge
    error: Optional[str] = None
    resultado: Optional[DocPropuestaOut] = None
    # Un cancelado es un 'failed' con motivo propio: la tabla no tiene estado 'cancelled'.
    cancelado: bool = False
    # Desde que arrancó, o desde que se encoló si todavía espera (reloj de la base).
    segundos: Optional[int] = None
    # Solo en cola: cuántos trabajos van antes y cuánto lleva el que está corriendo.
    adelante: Optional[int] = None
    en_curso_bot: Optional[str] = None
    en_curso_segundos: Optional[int] = None

class DocJobActualOut(BaseModel):
    """Lo que el panel retoma al abrir un bot: su trabajo abierto, o la última propuesta
    que terminó y no se guardó (status='done')."""
    job_id: int
    status: str
    segundos: Optional[int] = None          # solo en 'done': hace cuánto terminó

class DocMarkdownBulkItem(BaseModel):
    """Un documento a guardar: se crea si id es None, se actualiza si viene."""
    id: Optional[int] = None
    titulo: Optional[str] = None
    orden: Optional[int] = None            # None = se autoasigna al final
    contenido_md: str

class DocMarkdownBulkIn(BaseModel):
    documentos: List[DocMarkdownBulkItem]
    # El reindexado NO es automático: guardar deja el bot con "cambios sin indexar" y
    # se reindexa a mano cuando la carga terminó (antes cada guardado encolaba uno y
    # una sesión de trabajo costaba media docena de reconstrucciones del índice).
    reindex: bool = False
    material_ids: List[int] = []           # material de la bandeja a consumir al guardar

class DocMarkdownIn(BaseModel):
    titulo: Optional[str] = None
    orden: int = 1
    contenido_md: str
    reindex: bool = False                   # encolar reindexado tras guardar

class DocMarkdownOut(BaseModel):
    id: int
    chatbot_id: int
    titulo: Optional[str] = None
    orden: int
    contenido_md: str
    activo: bool
    updated_at: Optional[datetime] = None
    updated_by: Optional[int] = None
    # Documento prestado por otro bot (ChatbotDocVinculo): se indexa acá pero se edita
    # en el bot dueño, para que no se desincronicen.
    compartido: bool = False
    propietario_slug: Optional[str] = None
    # A qué otros bots se prestó este documento (solo en los propios).
    compartido_con: List[str] = []

class DocCompartirIn(BaseModel):
    """Bots con los que se comparte un documento (reemplazo completo)."""
    slugs: List[str] = []

class AceptarCandidatoRequest(BaseModel):
    dni: str                    # Identificador único en Google Sheets
    correo_candidato: str       # Necesario para enviarle la invitación
    fecha: str
    hora: str
    lugar: str
    campana_asignada: str

# Modelo para definir una imagen
class ImagenCorreo(BaseModel):
    ruta: str
    ancho: str = "100%"

# Modelo para el cuerpo de la solicitud (opcional, pero recomendado)
class SolicitudCorreo(BaseModel):
    destinatarios: List[EmailStr]
    asunto: str
    mensaje: str
    imagenes: List[ImagenCorreo] = []
    archivos_adjuntos: List[str] = []

# --- MODELOS PARA SCHEDULER DE AUDITORÍAS ---
class SchedulerCreateRequest(BaseModel):
    task_name: str
    frecuencia: str
    hora_ejecucion: str
    dias: Optional[List[str]] = []
    
    # Destinos
    send_email: bool = False
    email_addresses: Optional[str] = None
    send_gsheets: bool = False
    gsheet_id: Optional[str] = None
    gsheet_name: Optional[str] = None
    
    # Parámetros Base
    empresa: str
    campana: str
    plantilla_id: int
    cantidad: int
    rango_dinamico: str
    
    # Parámetros Extra (Filtros Opcionales)
    parametros_json: Optional[Dict[str, Any]] = {}

    # Plantilla de columnas (opcional). Si llega, en mail/Sheets se filtran las columnas.
    column_template_id: Optional[int] = None

class SchedulerResponse(BaseModel):
    id: int
    task_name: str
    frecuencia: str
    hora_ejecucion: str
    empresa: str
    campana_nombre: Optional[str] = None
    is_active: bool
    next_run_time: Optional[str] = None
    destinos: str


# --- PLANTILLAS DE COLUMNAS PARA AUDITORÍAS REALIZADAS ---
class ColumnTemplateCreateRequest(BaseModel):
    name: str
    columns: List[str]
    empresa: Optional[int] = None
    campana: Optional[int] = None
    plantilla_id: Optional[int] = None


class ColumnTemplateUpdateRequest(BaseModel):
    name: Optional[str] = None
    columns: Optional[List[str]] = None


class ColumnTemplateResponse(BaseModel):
    id: int
    name: str
    columns: List[str]
    empresa: Optional[int] = None
    campana: Optional[int] = None
    plantilla_id: Optional[int] = None
    updated_at: Optional[str] = None


class PresupuestoIARequest(BaseModel):
    """Configuración del presupuesto mensual de IA (POST /uso-ia/presupuesto)."""
    monto_usd: float
    umbrales: str = "50,75,90,100"       # porcentajes separados por coma
    destinatarios: Optional[str] = None  # emails ';'-separados; vacío = solo ADMIN_EMAIL


