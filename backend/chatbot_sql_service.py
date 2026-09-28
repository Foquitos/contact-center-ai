"""
Servicio Text-to-SQL para gerencia.

Arquitectura (un chatbot por campaña + orquestador):

- ``AgenteSQLCampana``: agente NL->SQL acotado a un conjunto de vistas del
  schema ``chatbot`` (una campaña, o los datos generales de la empresa).
  Genera la consulta con Gemini (salida estructurada), la valida (solo
  SELECT, una única sentencia) y la ejecuta con reintentos informando el
  error de la base al modelo para que se corrija.

- ``ChatbotSQLService`` (orquestador): decide con el LLM qué agente(s)
  deben responder la pregunta (uno solo si es de una campaña; varios si se
  pide comparar campañas), ejecuta las sub-consultas en paralelo y sintetiza
  una única respuesta con análisis de negocio y recomendación de gráfico.

Las vistas referenciadas viven en el schema ``chatbot`` de la base ``Acme``
(ver scripts/migrations/2026-06-12_chatbot_vistas_gerencia.sql para las más
recientes). El engine debe usar un usuario de SOLO LECTURA.
"""
import re
import logging
import asyncio
import pandas as pd
from sqlalchemy import Engine
from pydantic import BaseModel, Field
from typing import Literal, Optional, Dict, Any, List

from google.genai import types
from llama_index.llms.google_genai import GoogleGenAI
from llama_index.core import SQLDatabase
from llama_index.core.prompts import PromptTemplate

logger = logging.getLogger(__name__)

# Máximo de filas que viajan al frontend por resultado (las agregaciones
# normales devuelven muchísimo menos; esto solo acota listados de detalle).
MAX_FILAS_RESPUESTA = 500
# Máximo de filas de cada resultado que se le muestran al LLM para el análisis.
MAX_FILAS_ANALISIS = 40
MAX_REINTENTOS_SQL = 3

# --------------------------------------------------------------------------- #
# Contextos por dominio: vista -> descripción de negocio.                      #
# A nivel de módulo para que los tests puedan validar que cada vista exista    #
# sin instanciar el servicio (ver tests/test_chatbot_data_layer.py).           #
# --------------------------------------------------------------------------- #

CONTEXTO_GENERAL: Dict[str, str] = {
    'vw_equipos': (
        'Equipos de trabajo, incluye el Supervisor a cargo (nombre_equipo, apellido_equipo, id_nomina) '
        'y a qué campaña_id pertenecen.'
    ),
    'vw_nomina': 'Maestro de agentes. Contiene legajo, documento, nombre, apellido, fecha_alta y fecha_piso.',
    'vw_nomina_extendida': 'Versión extendida del maestro de agentes: Empleador, Sexo, Direccion, Hijos, etc.',
    'vw_operadores': (
        'Operadores de telefonía y su asignación vigente (puede cambiar en el tiempo). '
        'operador_id es el identificador único; cruza con vw_nomina (nomina_id) y vw_equipos (equipo_id). '
        'Incluye cliente/campaña y puesto del operador.'
    ),
    'vw_turnos_operadores_pasado': (
        'PAYROLL HISTÓRICO: turnos pasados de los operadores y horas trabajadas. Se actualiza '
        'constantemente, por lo que refleja cambios de horario cargados después del día trabajado. '
        'Usar para horas trabajadas, ausentismo pasado y dotación real.'
    ),
    'vw_turnos_operadores_futuros': (
        'PAYROLL FUTURO: turnos programados SIEMPRE de mañana en adelante. Como los turnos todavía '
        'no ocurrieron, TODOS los operadores figuran como ausentes/sin trabajar: NUNCA usar esta vista '
        'para medir ausentismo ni horas trabajadas, solo para dotación/programación futura.'
    ),
}

CONTEXTO_HIDRA: Dict[str, str] = {
    'vw_resumen_llamadas_mitrol_Hidra': (
        'VISTA PRINCIPAL PARA CONTAR LLAMADAS de Hidra. Nivel: LLAMADA ÚNICA. '
        'Un registro = una llamada completa.'
    ),
    'vw_ivr_solo_mitrol_Hidra': (
        'VISTA DE AUTOMATIZACIÓN Hidra. Solo los tramos donde el cliente navegó por el IVR o estuvo en cola.'
    ),
    'vw_gestiones_agentes_mitrol_Hidra': (
        'VISTA DE PRODUCTIVIDAD Y GESTIÓN Hidra. Solo los tramos donde intervino un operador humano.'
    ),
    'vw_sar_ingresos_Hidra': (
        'Ingresos (reclamos/órdenes ODT) cargados en SAR, el CRM del cliente Hidra. Un registro = un ODT, '
        'con motivo, observación, fecha_ingreso y el agente que lo cargó. Usar para volumen de reclamos '
        'registrados y motivos más frecuentes.'
    ),
    'vw_llamadas_sar_Hidra': (
        'CRUCE LLAMADA->SAR: cada tramo de llamada del canal telefónico de Hidra con el ODT que generó '
        '(nro_odt, motivo_sar, fecha_ingreso_sar; NULL si no generó). El flag genero_odt (1/0) permite medir '
        'la tasa de registración de reclamos por llamada, por operador o por tipificación.'
    ),
}

CONTEXTO_DENTAL: Dict[str, str] = {
    'vw_resumen_llamadas_dental': (
        'VISTA PRINCIPAL PARA CONTAR INTERACCIONES de Odonto Plus. Nivel: INTERACCIÓN ÚNICA. '
        "Un registro = una llamada o chat completo. La columna canal separa los 3 canales de la "
        "campaña: 'Telefono', 'Chat Facebook' y 'Chat WhatsApp' (más 'Encuesta Telefonica')."
    ),
    'vw_llamadas_dental': (
        'Tramos (segmentos) de llamadas/chats de Odonto Plus con el operador que los gestionó y la '
        'columna canal. Para contar interacciones únicas usar vw_resumen_llamadas_dental o '
        'COUNT(DISTINCT idInteraccion).'
    ),
    'vw_turnos_dental': (
        'Turnos odontológicos agendados en el turnero. Un registro = un turno. fecha_agendamiento es '
        'cuándo el operador lo cargó; fecha_turno es cuándo se atiende al paciente. Incluye id_clinica '
        'y es_primera_vez (pacientes nuevos).'
    ),
    'vw_llamadas_turnos_dental': (
        'CRUCE INTERACCIÓN->TURNO: cada llamada/chat de Odonto Plus (con su canal) y el turno '
        'agendado durante esa gestión (NULL si no agendó). El flag agendo_turno (1/0) permite medir '
        'la conversión por canal, operador, campaña o período.'
    ),
}

CONTEXTO_PLANSENIOR: Dict[str, str] = {
    'vw_resumen_llamadas_plansenior': (
        'VISTA PRINCIPAL PARA CONTAR LLAMADAS de Plansenior. Nivel: LLAMADA ÚNICA. '
        'Un registro = una llamada completa (todas las campañas de Plansenior son telefónicas).'
    ),
    'vw_llamadas_plansenior': (
        'Tramos (segmentos) de llamadas de Plansenior con el operador que los gestionó. '
        'Para contar llamadas únicas usar vw_resumen_llamadas_plansenior o COUNT(DISTINCT idInteraccion).'
    ),
}

# --------------------------------------------------------------------------- #
# Instrucciones y ejemplos por agente                                          #
# --------------------------------------------------------------------------- #

INSTRUCCIONES_TSQL = (
    "REGLAS OBLIGATORIAS:\n"
    "- Generá UNA sola sentencia SELECT en T-SQL (Microsoft SQL Server). NUNCA INSERT/UPDATE/DELETE/"
    "DROP/ALTER/EXEC ni múltiples sentencias.\n"
    "- Todas las vistas pertenecen al schema 'chatbot': usá SIEMPRE el prefijo chatbot.nombre_vista.\n"
    "- Dialecto T-SQL: usá TOP (N) (NUNCA LIMIT), GETDATE(), DATEADD/DATEDIFF, CAST(col AS DATE), "
    "FORMAT(fecha, 'yyyy-MM') para agrupar por mes.\n"
    "- Si preguntan por un rango temporal sin especificar año, usá el año actual.\n"
    "- Si piden un listado/detalle sin fechas, filtrá por los últimos 7 días y agregá TOP (500). "
    "NO apliques TOP a los COUNT ni a las agrupaciones.\n"
    "- Los nombres de personas compáralos con LIKE '%...%' (pueden venir incompletos o en otro orden).\n"
    "- Usá alias de columnas claros, en español y sin acentos (se usan como etiquetas de gráficos).\n"
    "- No confundas empresas ni campañas: limitate a las vistas listadas en el esquema."
)

EJEMPLOS_HIDRA = (
    "EJEMPLOS DE CONSULTAS CORRECTAS:\n"
    "Q: ¿Cuántas llamadas de Hidra entraron ayer?\n"
    "A: SELECT COUNT(*) AS llamadas FROM chatbot.vw_resumen_llamadas_mitrol_Hidra "
    "WHERE CAST(fecha_hora_inicio AS DATE) = CAST(GETDATE()-1 AS DATE);\n"
    "\nQ: Dame el detalle de la productividad de los agentes del equipo de 'Fernanda Ruiz'.\n"
    "A: SELECT n.nombre + ' ' + n.apellido AS nombre_completo, COUNT(g.idInteraccion) AS gestiones "
    "FROM chatbot.vw_gestiones_agentes_mitrol_Hidra g "
    "JOIN chatbot.vw_operadores o ON g.operador_id = o.operador_id "
    "JOIN chatbot.vw_nomina n ON o.nomina_id = n.nomina_id "
    "JOIN chatbot.vw_equipos e ON o.equipo_id = e.equipo_id "
    "WHERE e.nombre_equipo LIKE '%FLORENCIA%' AND e.apellido_equipo LIKE '%REY%' "
    "GROUP BY n.nombre, n.apellido;\n"
    "\nQ: ¿Programamos correctamente la cantidad de operadores por la cantidad de llamadas entrantes "
    "que tuvimos en el mes de abril de este año?\n"
    "A: SELECT Fecha, COUNT(DISTINCT tof.operador_id) AS total_operadores, "
    "COUNT(DISTINCT gam.idInteraccion) AS llamadas_entrantes, "
    "(COUNT(DISTINCT gam.idInteraccion) / COUNT(DISTINCT tof.operador_id)) AS llamadas_por_operador "
    "FROM chatbot.vw_turnos_operadores_futuros tof "
    "JOIN chatbot.vw_gestiones_agentes_mitrol_Hidra gam ON tof.operador_id = gam.operador_id "
    "AND CAST(tof.fecha AS DATE) = CAST(gam.fecha_hora_inicio AS DATE) "
    "WHERE MONTH(tof.fecha) = 4 AND YEAR(tof.fecha) = YEAR(GETDATE()) GROUP BY Fecha;\n"
    "\nQ: ¿Qué porcentaje de las llamadas de la última semana generó un ODT en SAR?\n"
    "A: SELECT CAST(SUM(CAST(genero_odt AS FLOAT)) * 100.0 / COUNT(*) AS DECIMAL(5,2)) AS porcentaje_con_odt "
    "FROM chatbot.vw_llamadas_sar_Hidra WHERE fecha_hora_inicio >= DATEADD(DAY, -7, GETDATE());\n"
    "\nQ: ¿Cuáles fueron los motivos de reclamo más frecuentes ayer?\n"
    "A: SELECT TOP (10) motivo, COUNT(*) AS cantidad FROM chatbot.vw_sar_ingresos_Hidra "
    "WHERE CAST(fecha_ingreso AS DATE) = CAST(GETDATE()-1 AS DATE) "
    "GROUP BY motivo ORDER BY cantidad DESC;"
)

INSTRUCCIONES_CANAL_DENTAL = (
    "SOBRE LOS CANALES DE ODONTO PLUS (columna canal):\n"
    "- La campaña tiene 3 canales: 'Telefono', 'Chat Facebook' y 'Chat WhatsApp'. NUNCA los sumes "
    "mezclados salvo que pidan explícitamente el total de interacciones o una comparación.\n"
    "- 'llamadas' = canal = 'Telefono'. 'chats' = canal IN ('Chat Facebook', 'Chat WhatsApp') "
    "(o el canal puntual si lo especifican).\n"
    "- 'Encuesta Telefonica' son encuestas post-llamada: excluilas de los conteos salvo que las pidan."
)

EJEMPLOS_DENTAL = (
    "EJEMPLOS DE CONSULTAS CORRECTAS:\n"
    "Q: ¿Cuántas llamadas recibió Odonto Plus ayer?\n"
    "A: SELECT COUNT(*) AS llamadas FROM chatbot.vw_resumen_llamadas_dental "
    "WHERE canal = 'Telefono' AND CAST(fecha_hora_inicio AS DATE) = CAST(GETDATE()-1 AS DATE);\n"
    "\nQ: ¿Cuántos chats de WhatsApp hubo la semana pasada?\n"
    "A: SELECT COUNT(*) AS chats_whatsapp FROM chatbot.vw_resumen_llamadas_dental "
    "WHERE canal = 'Chat WhatsApp' AND fecha_hora_inicio >= DATEADD(DAY, -7, CAST(GETDATE() AS DATE));\n"
    "\nQ: Compará el volumen de los canales de Odonto Plus este mes.\n"
    "A: SELECT canal, COUNT(*) AS interacciones FROM chatbot.vw_resumen_llamadas_dental "
    "WHERE MONTH(fecha_hora_inicio) = MONTH(GETDATE()) AND YEAR(fecha_hora_inicio) = YEAR(GETDATE()) "
    "GROUP BY canal ORDER BY interacciones DESC;\n"
    "\nQ: ¿Cuántos turnos agendó Odonto Plus ayer?\n"
    "A: SELECT COUNT(*) AS turnos_agendados FROM chatbot.vw_turnos_dental "
    "WHERE CAST(fecha_agendamiento AS DATE) = CAST(GETDATE()-1 AS DATE);\n"
    "\nQ: ¿Cuál fue la tasa de conversión de llamadas a turnos por operador este mes?\n"
    "A: SELECT nombre_agente + ' ' + apellido_agente AS operador, COUNT(*) AS llamadas, "
    "SUM(agendo_turno) AS turnos, CAST(SUM(agendo_turno) * 100.0 / COUNT(*) AS DECIMAL(5,2)) AS tasa_conversion "
    "FROM chatbot.vw_llamadas_turnos_dental "
    "WHERE canal = 'Telefono' AND MONTH(fecha_hora_inicio) = MONTH(GETDATE()) AND YEAR(fecha_hora_inicio) = YEAR(GETDATE()) "
    "AND usuario_agente IS NOT NULL "
    "GROUP BY nombre_agente, apellido_agente ORDER BY tasa_conversion DESC;"
)

EJEMPLOS_PLANSENIOR = (
    "EJEMPLOS DE CONSULTAS CORRECTAS:\n"
    "Q: ¿Cuántas llamadas tuvo Plansenior la semana pasada?\n"
    "A: SELECT COUNT(*) AS llamadas FROM chatbot.vw_resumen_llamadas_plansenior "
    "WHERE fecha_hora_inicio >= DATEADD(DAY, -7, CAST(GETDATE() AS DATE));\n"
    "\nQ: ¿Cómo se reparten las llamadas de Plansenior entre entrantes y salientes este mes?\n"
    "A: SELECT sentido_llamada, COUNT(*) AS llamadas FROM chatbot.vw_resumen_llamadas_plansenior "
    "WHERE MONTH(fecha_hora_inicio) = MONTH(GETDATE()) AND YEAR(fecha_hora_inicio) = YEAR(GETDATE()) "
    "GROUP BY sentido_llamada;"
)

EJEMPLOS_GENERAL = (
    "EJEMPLOS DE CONSULTAS CORRECTAS:\n"
    "Q: ¿Cuántos equipos de trabajo hay registrados?\n"
    "A: SELECT COUNT(*) AS equipos FROM chatbot.vw_equipos;\n"
    "\nQ: ¿Cuántos operadores tiene el equipo de Fernanda Ruiz?\n"
    "A: SELECT COUNT(*) AS operadores FROM chatbot.vw_operadores o "
    "JOIN chatbot.vw_equipos e ON o.equipo_id = e.equipo_id "
    "WHERE e.nombre_equipo LIKE '%FLORENCIA%' AND e.apellido_equipo LIKE '%REY%';"
)

INSTRUCCIONES_PAYROLL = (
    "SOBRE LOS TURNOS (PAYROLL):\n"
    "- vw_turnos_operadores_pasado = lo que efectivamente pasó (se actualiza si corrigen horarios).\n"
    "- vw_turnos_operadores_futuros = programación de mañana en adelante; todos figuran ausentes "
    "porque aún no ocurrió: jamás usarla para ausentismo.\n"
    "- Llegadas tarde: ese dato todavía no está disponible para el chatbot (requiere payroll_ayer); "
    "si lo piden, respondé que aún no está disponible."
)

# --------------------------------------------------------------------------- #
# Modelos Pydantic de salida estructurada                                      #
# --------------------------------------------------------------------------- #

class ConsultaSQLGenerada(BaseModel):
    sql: str = Field(description="La sentencia SELECT en T-SQL, sin formato markdown.")


class SubConsulta(BaseModel):
    agente: str = Field(description="Agente que debe responder: 'hidra', 'odonto plus' o 'general'.")
    pregunta: str = Field(description="Sub-pregunta auto-contenida (con fechas y filtros explícitos).")


class PlanOrquestador(BaseModel):
    consultas: List[SubConsulta] = Field(
        description="1 a 3 sub-consultas. Una sola si la pregunta es de una campaña; "
                    "una por campaña si piden comparar."
    )


class ConfiguracionGrafico(BaseModel):
    # Sin defaults en los Fields. El schema viaja a Gemini como response_schema
    # (salida estructurada del SDK google-genai). El SDK nuevo ya tolera la clave
    # "default" —el protobuf viejo de google-generativeai la rechazaba—, pero
    # mantenemos la convención: un default acá lo completa el modelo sin avisar en
    # vez de fallar, y no está verificado contra la API real
    # (ver tests/test_chatbot_sql_guardrails.py).
    eje_x: str = Field(description="Nombre EXACTO de la columna para el eje X")
    eje_y: str = Field(description="Nombre EXACTO de la columna para el eje Y (numérica)")
    titulo: str = Field(description="Título sugerido para el gráfico")
    fuente_datos: int = Field(description="Índice (desde 0) del resultado que alimenta el gráfico. Usar 0 si hay un solo resultado.")


class AnalisisRespuesta(BaseModel):
    respuesta_texto: str = Field(description="Respuesta natural y directa con las cifras solicitadas.")
    analisis_negocio: str = Field(description="Breve insight, conclusión o interpretación de los datos.")
    grafico_recomendado: Literal["bar", "line", "pie", "none"] = Field(
        description="Tipo de gráfico recomendado según los datos devueltos."
    )
    configuracion_grafico: Optional[ConfiguracionGrafico] = Field(
        default=None, description="Configuración del gráfico si aplica, omitir si es 'none'."
    )


# --------------------------------------------------------------------------- #
# Validación de SQL generado (defensa adicional al usuario read-only)          #
# --------------------------------------------------------------------------- #

_RE_FENCE = re.compile(r"^```(?:sql)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
_RE_PROHIBIDO = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|MERGE|EXEC|EXECUTE|GRANT|REVOKE|CREATE|"
    r"OPENQUERY|OPENROWSET|sp_\w+|xp_\w+)\b",
    re.IGNORECASE,
)


def validar_sql_seguro(sql: str) -> str:
    """Limpia y valida la consulta generada. Devuelve el SQL listo para ejecutar
    o lanza ValueError (el mensaje se le devuelve al LLM para que se corrija)."""
    limpio = _RE_FENCE.sub("", sql or "").strip().rstrip(";").strip()
    if not limpio:
        raise ValueError("La consulta generada está vacía.")
    if ";" in limpio:
        raise ValueError("Solo se permite UNA sentencia SQL (sin ';' intermedios).")
    if not re.match(r"^(SELECT|WITH)\b", limpio, re.IGNORECASE):
        raise ValueError("La consulta debe comenzar con SELECT o WITH (solo lectura).")
    encontrado = _RE_PROHIBIDO.search(limpio)
    if encontrado:
        raise ValueError(f"Palabra clave no permitida en la consulta: {encontrado.group(0)}.")
    if re.search(r"\bLIMIT\s+\d+", limpio, re.IGNORECASE):
        raise ValueError("LIMIT no existe en T-SQL: usá SELECT TOP (N).")
    return limpio


def _formatear_historial(historial: Optional[List[Dict[str, str]]]) -> str:
    if not historial:
        return ""
    lineas = []
    for msg in historial[-8:]:
        rol = "Usuario" if str(msg.get("rol", "")).lower() in ("user", "usuario") else "Asistente"
        texto = str(msg.get("texto", ""))[:1000]
        if texto:
            lineas.append(f"{rol}: {texto}")
    if not lineas:
        return ""
    return "HISTORIAL DE LA CONVERSACIÓN (para resolver referencias):\n" + "\n".join(lineas) + "\n\n"


# --------------------------------------------------------------------------- #
# Agente NL->SQL por campaña                                                   #
# --------------------------------------------------------------------------- #

class ResultadoAgente:
    """Resultado de una sub-consulta resuelta por un agente de campaña."""

    def __init__(self, agente: str, pregunta: str, sql: str, df: pd.DataFrame, truncado: bool):
        self.agente = agente
        self.pregunta = pregunta
        self.sql = sql
        self.df = df
        self.truncado = truncado

    def datos(self) -> List[Dict[str, Any]]:
        return self.df.astype(str).to_dict(orient="records")

    def tabla_markdown(self, max_filas: int = MAX_FILAS_ANALISIS) -> str:
        tabla = self.df.head(max_filas).to_markdown(index=False)
        if len(self.df) > max_filas:
            tabla += f"\n... ({len(self.df)} filas en total, se muestran {max_filas})"
        return tabla


class ConsultaSQLError(Exception):
    """El agente no logró generar una consulta válida tras los reintentos."""


class AgenteSQLCampana:
    """Agente Text-to-SQL acotado a las vistas de una campaña/dominio."""

    def __init__(self, nombre: str, descripcion: str, llm: GoogleGenAI, db_engine: Engine,
                 contexto_vistas: Dict[str, str], ejemplos: str = "", instrucciones_extra: str = ""):
        self.nombre = nombre
        self.descripcion = descripcion
        self.llm = llm
        self.engine = db_engine
        self.contexto_vistas = contexto_vistas
        self.ejemplos = ejemplos
        self.instrucciones_extra = instrucciones_extra

        # Refleja las vistas reales: si alguna no existe en la BD, esto lanza y
        # el agente queda fuera de servicio (lo captura el orquestador).
        sql_database = SQLDatabase(
            db_engine,
            include_tables=list(contexto_vistas.keys()),
            view_support=True,
            schema="chatbot",
        )
        self.esquema = self._construir_esquema(sql_database)

    def _construir_esquema(self, sql_database: SQLDatabase) -> str:
        bloques = []
        for vista, descripcion in self.contexto_vistas.items():
            info = sql_database.get_single_table_info(vista)
            bloques.append(f"{info}\nDescripción: {descripcion}")
        return "\n\n".join(bloques)

    def _prompt_generacion(self, pregunta: str, feedback_error: str) -> str:
        partes = [
            "Eres un experto en T-SQL (Microsoft SQL Server) para análisis de un contact center.",
            f"Dominio de datos: {self.descripcion}",
            "",
            "ESQUEMA DISPONIBLE (únicas vistas permitidas):",
            self.esquema,
            "",
            INSTRUCCIONES_TSQL,
        ]
        if self.instrucciones_extra:
            partes += ["", self.instrucciones_extra]
        if self.ejemplos:
            partes += ["", self.ejemplos]
        partes += ["", f"Pregunta de negocio: {pregunta}"]
        if feedback_error:
            partes += ["", feedback_error]
        partes.append("Generá la consulta SQL.")
        return "\n".join(partes)

    async def consultar(self, pregunta: str) -> ResultadoAgente:
        """Genera y ejecuta la consulta con reintentos; el error de cada intento
        fallido se inyecta en el siguiente para que el modelo se corrija."""
        feedback_error = ""
        ultimo_error = ""
        sql_limpio = ""

        for intento in range(MAX_REINTENTOS_SQL):
            prompt = self._prompt_generacion(pregunta, feedback_error)
            try:
                generada: ConsultaSQLGenerada = await self.llm.astructured_predict(
                    ConsultaSQLGenerada, PromptTemplate("{contenido}"), contenido=prompt
                )
                sql_limpio = validar_sql_seguro(generada.sql)
                logger.info(f"[{self.nombre}] SQL (intento {intento + 1}): {sql_limpio}")
                df = await asyncio.to_thread(pd.read_sql, sql_limpio, self.engine)

                truncado = len(df) > MAX_FILAS_RESPUESTA
                if truncado:
                    df = df.head(MAX_FILAS_RESPUESTA)
                return ResultadoAgente(self.nombre, pregunta, sql_limpio, df, truncado)

            except Exception as err:
                ultimo_error = str(err)
                logger.warning(f"[{self.nombre}] intento {intento + 1} falló: {ultimo_error}")
                feedback_error = (
                    f"Tu intento anterior fue:\n{sql_limpio or '(sin SQL válido)'}\n"
                    f"Y falló con este error:\n{ultimo_error}\n"
                    "Corregí la sintaxis o los nombres de columnas/vistas y generá una nueva consulta válida."
                )

        raise ConsultaSQLError(
            f"El agente '{self.nombre}' no logró una consulta válida tras "
            f"{MAX_REINTENTOS_SQL} intentos. Último error: {ultimo_error}"
        )


# --------------------------------------------------------------------------- #
# Orquestador                                                                  #
# --------------------------------------------------------------------------- #

class ChatbotSQLService:
    """Orquestador del chatbot SQL de gerencia.

    IMPORTANTE: para prevenir escrituras, el ``db_engine`` debe instanciarse
    con un usuario de base de datos de SOLO LECTURA.
    """

    def __init__(self, db_engine: Engine, api_key: str):
        self.engine = db_engine
        self.api_key = api_key
        self._configurar_llm()
        self._crear_agentes()

    def _configurar_llm(self):
        safety_settings = [
            types.SafetySetting(category=categoria, threshold="BLOCK_NONE")  # type: ignore[arg-type]
            for categoria in (
                "HARM_CATEGORY_HARASSMENT",
                "HARM_CATEGORY_HATE_SPEECH",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "HARM_CATEGORY_DANGEROUS_CONTENT",
            )
        ]

        def _construir(temperatura: float) -> GoogleGenAI:
            # La temperatura va DENTRO de generation_config: GoogleGenAI ignora el
            # argumento suelto `temperature` cuando recibe un config propio (ver
            # el mismo cuidado en app/rag_settings.py).
            return GoogleGenAI(
                model="gemini-3.8-flash",
                api_key=self.api_key,
                generation_config=types.GenerateContentConfig(
                    temperature=temperatura,
                    safety_settings=safety_settings,
                ),
            )

        # Generación de SQL: temperatura baja (precisión); análisis: más natural.
        self.llm_sql = _construir(0.1)
        self.llm_analisis = _construir(0.4)

    def _crear_agentes(self):
        # Los agentes de campaña incluyen las vistas generales para poder cruzar
        # llamadas/turnos con operadores, equipos y payroll.
        especificaciones = [
            (
                "hidra",
                "Campaña Hidra (Aguas Argentinas), canal telefónico sobre Mitrol: llamadas, IVR, "
                "gestiones de agentes y reclamos/ODT registrados en SAR (el CRM del cliente). "
                "Incluye además los datos generales de la empresa (nómina, equipos, operadores, turnos).",
                {**CONTEXTO_HIDRA, **CONTEXTO_GENERAL},
                EJEMPLOS_HIDRA,
                INSTRUCCIONES_PAYROLL,
            ),
            (
                "odonto plus",
                "Campaña Odonto Plus (clínicas odontológicas) con 3 canales separados: llamadas "
                "telefónicas, chats de Facebook y chats de WhatsApp. También turnos agendados en el "
                "turnero y conversión interacción->turno. NO incluye Plansenior (es otra empresa con "
                "agente propio). Incluye además los datos generales de la empresa (nómina, equipos, "
                "operadores, turnos).",
                {**CONTEXTO_DENTAL, **CONTEXTO_GENERAL},
                EJEMPLOS_DENTAL,
                INSTRUCCIONES_CANAL_DENTAL + "\n\n" + INSTRUCCIONES_PAYROLL,
            ),
            (
                "plansenior",
                "Empresa Plansenior: llamadas telefónicas entrantes y salientes (campañas Inbound "
                "Plansenior, PlanseniorSaliente, piloto_PROJUB). Es una empresa distinta de Dental "
                "Total aunque en la base figure bajo esa empresa. Incluye además los datos generales "
                "de la empresa (nómina, equipos, operadores, turnos).",
                {**CONTEXTO_PLANSENIOR, **CONTEXTO_GENERAL},
                EJEMPLOS_PLANSENIOR,
                INSTRUCCIONES_PAYROLL,
            ),
            (
                "general",
                "Datos generales de la empresa, sin campaña específica: nómina de agentes, equipos y "
                "supervisores, operadores de telefonía y turnos programados/trabajados (payroll).",
                CONTEXTO_GENERAL,
                EJEMPLOS_GENERAL,
                INSTRUCCIONES_PAYROLL,
            ),
        ]

        self.agentes: Dict[str, AgenteSQLCampana] = {}
        for nombre, descripcion, contexto, ejemplos, extra in especificaciones:
            try:
                self.agentes[nombre] = AgenteSQLCampana(
                    nombre, descripcion, self.llm_sql, self.engine, contexto, ejemplos, extra
                )
                logger.info(f"Agente SQL '{nombre}' inicializado ({len(contexto)} vistas).")
            except Exception as e:
                # Típicamente: vistas de la migración aún no aplicadas en la BD.
                logger.error(f"Agente SQL '{nombre}' no disponible: {e}")

        if not self.agentes:
            raise RuntimeError("Ningún agente SQL pudo inicializarse (¿faltan las vistas del schema chatbot?).")

    # ----------------------------- Planificación ----------------------------- #

    def _prompt_plan(self, pregunta: str, historial_txt: str) -> str:
        descripciones = "\n".join(
            f"- '{nombre}': {agente.descripcion}" for nombre, agente in self.agentes.items()
        )
        return (
            "Sos el orquestador de un sistema de chatbots Text-to-SQL de un contact center. "
            "Tu única tarea es decidir qué agente(s) deben responder la pregunta de gerencia.\n\n"
            f"AGENTES DISPONIBLES:\n{descripciones}\n\n"
            "REGLAS:\n"
            "- Usá UNA sola sub-consulta siempre que la pregunta sea de una campaña o de datos generales.\n"
            "- Si piden comparar campañas, generá una sub-consulta por campaña (máximo 3 en total).\n"
            "- Preguntas de dotación, nómina, equipos o turnos sin campaña específica van al agente 'general'.\n"
            "- Cada sub-pregunta debe ser AUTO-CONTENIDA: incluí fechas, filtros y unidades explícitas, "
            "resolviendo cualquier referencia al historial.\n"
            "- No inventes agentes que no estén en la lista.\n\n"
            f"{historial_txt}"
            f"Pregunta del usuario: {pregunta}"
        )

    async def _planificar(self, pregunta: str, historial_txt: str) -> List[SubConsulta]:
        plan: PlanOrquestador = await self.llm_sql.astructured_predict(
            PlanOrquestador, PromptTemplate("{contenido}"),
            contenido=self._prompt_plan(pregunta, historial_txt),
        )
        consultas = []
        for sub in plan.consultas[:3]:
            nombre = sub.agente.strip().lower()
            if nombre not in self.agentes:
                # Agente inexistente o no inicializado: derivamos a la mejor opción disponible.
                nombre = "hidra" if "hidra" in self.agentes else next(iter(self.agentes))
                logger.warning(f"Plan pidió agente '{sub.agente}', se deriva a '{nombre}'.")
            consultas.append(SubConsulta(agente=nombre, pregunta=sub.pregunta))
        if not consultas:
            primero = "hidra" if "hidra" in self.agentes else next(iter(self.agentes))
            consultas = [SubConsulta(agente=primero, pregunta=pregunta)]
        return consultas

    # ------------------------------- Síntesis -------------------------------- #

    def _prompt_sintesis(self, pregunta: str, resultados: List[ResultadoAgente],
                         fallidos: List[Dict[str, str]]) -> str:
        bloques = []
        for idx, r in enumerate(resultados):
            bloques.append(
                f"RESULTADO {idx} (agente '{r.agente}', sub-pregunta: {r.pregunta})\n"
                f"SQL ejecutado: {r.sql}\n"
                f"Datos:\n{r.tabla_markdown()}"
            )
        if fallidos:
            for f in fallidos:
                bloques.append(
                    f"AGENTE '{f['agente']}' FALLÓ: {f['error']} (mencioná en la respuesta que esos "
                    "datos no pudieron obtenerse)."
                )
        cuerpo = "\n\n".join(bloques)
        return (
            "Eres un experto analista de datos de contact center.\n"
            f"Pregunta original de gerencia: '{pregunta}'\n\n"
            f"{cuerpo}\n\n"
            "Con esos datos, respondé la pregunta y hacé un breve análisis de negocio. "
            "Si recomendás un gráfico, eje_x y eje_y deben ser nombres EXACTOS de columnas del "
            "resultado elegido y fuente_datos su índice. Si hay un solo valor escalar, no recomiendes gráfico. "
            "Respondé estrictamente según el esquema solicitado."
        )

    # ------------------------------ Entrada API ------------------------------ #

    async def consultar_con_analisis(self, pregunta: str,
                                     historial: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """Pipeline completo: planifica, consulta a los agentes en paralelo y sintetiza."""
        logger.info(f"Procesando pregunta de gerencia: {pregunta}")
        historial_txt = _formatear_historial(historial)

        try:
            plan = await self._planificar(pregunta, historial_txt)
        except Exception as e:
            logger.error(f"Error planificando la consulta: {e}", exc_info=True)
            return {"error": "No se pudo interpretar la pregunta.", "detalle": str(e)}

        logger.info("Plan: " + "; ".join(f"{c.agente} <- {c.pregunta}" for c in plan))

        tareas = [self.agentes[c.agente].consultar(c.pregunta) for c in plan]
        crudos = await asyncio.gather(*tareas, return_exceptions=True)

        resultados: List[ResultadoAgente] = []
        fallidos: List[Dict[str, str]] = []
        for sub, res in zip(plan, crudos):
            if isinstance(res, BaseException):
                fallidos.append({"agente": sub.agente, "pregunta": sub.pregunta, "error": str(res)})
            else:
                resultados.append(res)

        if not resultados:
            detalle = "; ".join(f"{f['agente']}: {f['error']}" for f in fallidos)
            return {"error": "No se pudo formular una consulta válida tras varios intentos.", "detalle": detalle}

        respuesta_base = self._serializar_resultados(resultados, fallidos)

        if all(r.df.empty for r in resultados):
            return {
                **respuesta_base,
                "respuesta_texto": "No se encontraron datos para esa consulta con los filtros actuales.",
                "analisis_negocio": "La base de datos no devolvió registros. Revisá el período o los filtros.",
                "grafico_recomendado": "none",
                "configuracion_grafico": None,
            }

        try:
            analisis: AnalisisRespuesta = await self.llm_analisis.astructured_predict(
                AnalisisRespuesta, PromptTemplate("{contenido}"),
                contenido=self._prompt_sintesis(pregunta, resultados, fallidos),
            )
            final = analisis.model_dump()
            config = final.get("configuracion_grafico")
            if config and not (0 <= config.get("fuente_datos", 0) < len(resultados)):
                config["fuente_datos"] = 0
            return {**respuesta_base, **final}
        except Exception as e:
            logger.error("Error generando el análisis de negocio", exc_info=True)
            return {
                **respuesta_base,
                "error": "Error al generar el análisis de negocio.",
                "detalle": str(e),
            }

    def _serializar_resultados(self, resultados: List[ResultadoAgente],
                               fallidos: List[Dict[str, str]]) -> Dict[str, Any]:
        serializados = [
            {
                "agente": r.agente,
                "pregunta": r.pregunta,
                "query_sql": r.sql,
                "datos": r.datos(),
                "truncado": r.truncado,
            }
            for r in resultados
        ]
        serializados += [
            {"agente": f["agente"], "pregunta": f["pregunta"], "error": f["error"]}
            for f in fallidos
        ]
        return {
            "resultados": serializados,
            # Retro-compatibilidad con el frontend/consumidores previos:
            "datos_crudos": resultados[0].datos(),
            "query_generada": resultados[0].sql,
        }

    # Alias retro-compatible (entrada histórica orientada solo a Hidra).
    async def consultar_hidra_con_analisis(self, pregunta: str) -> Dict[str, Any]:
        return await self.consultar_con_analisis(pregunta)
