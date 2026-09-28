"""Qué datos del llamado recibe la IA, además del audio, en cada auditoría.

QUÉ ES ESTO
-----------
Con cada audio, `gemini.py::prompt_details` le agrega al prompt un bloque `## Call_details`
con los metadatos de esa interacción: una línea `- Campo: valor` por cada columna del
DataFrame que armó el builder de la campaña (tipificación, sentido, duración, operador,
motivo del caso, observaciones del CRM…). Históricamente se lo llama "CallInfo", que es
como se llama la variable que trae esos datos en el flujo de CSV (`AuditorIA/Cardnet.py`).

POR QUÉ HACE FALTA UN CATÁLOGO
------------------------------
El asistente de plantillas y el chequeo de salud razonaban sobre el prompt como si la IA
solo tuviera el audio. No es cierto, y eso llevaba a las dos equivocaciones opuestas:

  * El asistente proponía criterios sin usar datos que ya están ahí ("no se puede saber
    la tipificación") o, peor, inventaba campos que no existen en esa campaña.
  * La señal `pide_dato_externo` marcaba como problema un "verificá contra el CRM" en una
    campaña donde el CRM **sí viaja** en el Call_details. Un falso positivo que le enseña
    al analista que el semáforo miente.

DE DÓNDE SALE CADA LISTA
------------------------
De la cláusula SELECT del builder de cada empresa (`AuditorIA/SQL_query.py`), más los
armados a mano de las subidas (`AuditorIA/Cardnet.py` para CSV y `AuditorIA/Voltara.py`), y
sacando las columnas internas que `gemini.COLUMNAS_OMITIDAS_EN_DETALLES` nunca manda
(rutas de archivo, ids técnicos). `tests/test_call_details.py` verifica CADA lista contra
el código que la produce —el `select_clause` de cada builder, el SELECT externo de Voltara
y el recorte del DataFrame de CSV en Cardnet.py—: si alguien agrega o saca una columna y no
la refleja acá, el test lo dice.

Es un catálogo declarado y no una lectura en vivo porque los builders arman SQL dinámico
con parámetros de la corrida: describir la query sin ejecutarla exigiría una conexión (y
una corrida armada) cada vez que alguien abre el editor de plantillas.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

# Columnas internas: son las mismas que gemini.COLUMNAS_OMITIDAS_EN_DETALLES. Se listan
# acá (y el test verifica que coincidan) para no importar gemini desde este módulo, que
# tiene que poder usarse desde el editor de plantillas sin levantar el cliente de IA.
COLUMNAS_INTERNAS = frozenset({
    'FileName', 'FilePath', 'audio_dir', 'campana_id', 'Campaña',
    'idInteraccion', 'Segmento', 'empresa_id', 'externalTag',
})

# Empresa -> campos que llegan al bloque Call_details. La CLAVE es el nombre de la
# empresa tal como lo evalúa el dispatch (Auditor.__descarga_audios y
# Descarga.descarga_aleatoria), que compara por pertenencia: 'HIDRA Comercial' tiene que
# quedar ANTES que 'HIDRA' o el prefijo se la come. Ese orden vive en ORDEN_EMPRESAS.
CAMPOS_POR_EMPRESA: Dict[str, List[str]] = {
    # Subida de audios + informe de Verint, cruzado con el reporte de Avaya
    # (Cardnet.generar_df_llamados_csv -> subida_interacciones_csv, ya renombrado).
    'CSV': [
        'inicio', 'fin', 'duracion', 'ucid_llamado', 'id_llamado', 'id_agente', 'Agente',
        'extension', 'numero_marcado', 'entrante', 'ani', 'dnis', 'digitos_seleccionados',
        'uui', 'segmento', 'abandonado', 'codigo_cliente', 'inicio_llamado',
        'duracion_llamado', 'origen', 'destino', 'digitos_seleccionados_segmento', 'skill',
        'tiempo_en_cola_llamado', 'tiempo_de_ring_llamado', 'tiempo_hablado', 'tiempo_acw',
        'tiempo_hold', 'holds', 'ucid_segmento', 'uui_segmento', 'nombre_ingresante', 'ip',
        'Finalizada_por_operador',
    ],
    # Subida de audios + Excel (nombre -> ConnID), cruzado con el informe IVR y un
    # resumen de los casos del agente en Salesforce (SQL_query.get_filtered_data_Voltara).
    'Voltara': [
        'ConnID', 'ANI', 'inicio', 'Sentido', 'Nombre de Agente', 'Agente', 'Cola', 'Skill',
        'BPO', 'Numero de caso', 'Documento', 'Tipo de Documento', 'Suministro', 'Duracion',
        'Cantidad de casos del agente en el periodo', 'Casos del agente en el periodo',
        'Motivos de casos del agente en el periodo',
    ],
    # SQL_query.get_filtered_data_ALARMIX (CXOne).
    'ALARMIX': [
        'segmentId', 'Operador', 'inicio', 'directionType', 'Tipificación',
        'Motivo del fin del contacto',
    ],
    # SQL_query.get_filtered_data_Aurora_salud.
    'AuroraSalud': [
        'Tipificación', 'Empresa', 'Operador', 'Sentido', 'inicio', 'Correo Electronico',
        'Telefonos', 'Prestacion', 'Servicio', 'Observaciones', 'Cantidad_turnos',
        'Grabacion', 'Chat',
    ],
    # SQL_query.get_filtered_data_Odonto_plus (llamados y chats + turno del CRM).
    'Odonto Plus': [
        'Tipificación', 'Operador', 'Sentido', 'Empresa', 'inicio', 'email', 'celular',
        'FechaHora', 'esprimeravez', 'Observaciones', 'IdClinica', 'IdTurno',
        'Grabacion', 'Chat',
    ],
    'Facebook': [
        'Tipificación', 'Operador', 'Sentido', 'Empresa', 'inicio', 'email', 'celular',
        'FechaHora', 'esprimeravez', 'Observaciones', 'IdClinica', 'IdTurno',
        'Grabacion', 'Chat',
    ],
    # SQL_query.get_filtered_data_Hidra_comercial.
    'HIDRA Comercial': ['ID', 'CallLocalTime', 'Duracion', 'Sentido', 'EndByAgent', 'Rec_Filename', 'Rec_AgentId'],
    # SQL_query.get_filtered_data_Hidra (Mitrol + ingreso de SAR).
    'HIDRA': [
        'LoginId', 'Sentido', 'inicio', 'Nro. ODT', 'Motivo', 'Observacion',
        'Tipificación', 'Telefono',
    ],
    # SQL_query.get_filtered_data_Vitalis_Salud.
    'Vitalis Salud': [
        'es_entrante', 'agente_usuario', 'Tipificación', 'caso_id', 'fecha_inicio',
        'estado_llamada', 'url_grabacion',
    ],
    # SQL_query.get_filtered_data_Farmalux (Mitrol + caso de Salesforce).
    'Farmalux': [
        'id', 'fecha_inicio', 'Origen', 'Destino', 'Duracion', 'Estado', 'Entidad',
        'Usuario', 'LoginId', 'DataFijos', 'DataPers', 'Caso Abierto', 'Caso Cerrado',
        'Comentarios del caso', 'Asunto', 'Nombre de la cuenta', 'Telefono Contacto',
        'Numero del caso', 'Voz del cliente', 'Motivo', 'Submotivo',
    ],
    # SQL_query.get_filtered_data_Benefix (Genesys Cloud, Benefix.Interacciones).
    'Benefix': [
        'ID', 'inicio', 'Sentido', 'LoginId', 'Cola', 'Skill', 'Tipificación', 'Duracion',
        'Segundos en espera', 'Cantidad de esperas', 'Transferido', 'Motivo de finalización',
        'Telefono', 'Observaciones',
    ],
    # SQL_query.get_filtered_data_Vantix (Orion vía OPENQUERY).
    'Vantix': [
        'ID del llamado', 'Fecha del llamado', 'hora del llamado', 'Fecha y hora del llamado',
        'Duracion', 'Cliente empresaria', 'Skill', 'Campaña dentro del cliente',
        'Duracion del llamado total', 'Tiempo en Cola', 'Sentido del llamado',
        'Tipificacion/Categoria', 'Subtipificacion/Subcategoria', 'Agente', 'LoginId',
        'Numero de telefono del cliente', 'Motivo de finalización', 'Comentario del Agente',
        'ID de la grabación',
    ],
}
# Alias del mismo builder de Vantix (así los evalúa el dispatch de Descarga).
CAMPOS_POR_EMPRESA['Track On'] = CAMPOS_POR_EMPRESA['Vantix']
CAMPOS_POR_EMPRESA['Trackon-Vantix'] = CAMPOS_POR_EMPRESA['Vantix']

# Precedencia del dispatch. Importa: 'HIDRA Comercial' antes que 'HIDRA', y 'Odonto Plus'
# antes que cualquier prefijo suyo. Copiada del orden de los `elif` reales.
ORDEN_EMPRESAS: List[str] = [
    'CSV', 'Voltara', 'ALARMIX', 'AuroraSalud', 'Odonto Plus', 'Facebook', 'HIDRA Comercial',
    'HIDRA', 'Vitalis Salud', 'Farmalux', 'Track On', 'Trackon-Vantix', 'Vantix', 'Benefix',
]

# Las empresas que no matchean ninguna de las anteriores caen en el builder genérico de
# Mitrol (SQL_query.get_filtered_data_mitrol_puro).
CAMPOS_POR_DEFECTO: List[str] = ['loginId', 'Tipificación', 'Sentido', 'CRM', 'inicio']


def campos_de_empresa(nombre_empresa: Optional[str]) -> List[str]:
    """Campos del Call_details de una empresa, resueltos como los resuelve el dispatch.

    El match es por pertenencia y en el orden de ORDEN_EMPRESAS, igual que la cadena de
    `elif` que elige el builder: así una empresa nueva llamada "HIDRA Norte" cae en el
    mismo builder (y por lo tanto en los mismos campos) que "HIDRA".
    """
    nombre = (nombre_empresa or "").strip()
    if not nombre:
        return list(CAMPOS_POR_DEFECTO)
    for clave in ORDEN_EMPRESAS:
        if clave.lower() in nombre.lower():
            return [c for c in CAMPOS_POR_EMPRESA[clave] if c not in COLUMNAS_INTERNAS]
    return list(CAMPOS_POR_DEFECTO)


def empresa_de_plantilla(engine, plantilla_id: int) -> Optional[str]:
    """Nombre de la empresa de una plantilla, que es lo que decide el builder.

    Best-effort: si no se puede resolver, quien llama trabaja con los campos por defecto.
    """
    try:
        with engine.connect() as conn:
            return conn.execute(text("""
                SELECT e.Nombre
                FROM calidad.Plantillas p
                JOIN calidad.Campanas c ON c.CampanaID = p.CampanaID
                JOIN calidad.Empresas e ON e.EmpresaID = c.EmpresaID
                WHERE p.PlantillaID = :pid
            """), {"pid": plantilla_id}).scalar()
    except Exception as e:  # noqa: BLE001
        logger.warning("No se pudo resolver la empresa de la plantilla %s: %s", plantilla_id, e)
        return None


def empresa_de_campana(engine, campana_id: int) -> Optional[str]:
    """Nombre de la empresa de una campaña. Lo usa la generación de plantillas nuevas,
    que todavía no tiene plantilla_id."""
    try:
        with engine.connect() as conn:
            return conn.execute(text("""
                SELECT e.Nombre
                FROM calidad.Campanas c
                JOIN calidad.Empresas e ON e.EmpresaID = c.EmpresaID
                WHERE c.CampanaID = :cid
            """), {"cid": campana_id}).scalar()
    except Exception as e:  # noqa: BLE001
        logger.warning("No se pudo resolver la empresa de la campaña %s: %s", campana_id, e)
        return None


def campos_de_plantilla(engine, plantilla_id: Optional[int]) -> List[str]:
    """Atajo: los campos del Call_details que le van a llegar a esta plantilla."""
    if not plantilla_id:
        return []
    return campos_de_empresa(empresa_de_plantilla(engine, plantilla_id))


def campos_de_campana(engine, campana_id: Optional[int]) -> List[str]:
    """Ídem para una plantilla que todavía no existe (generación desde cero)."""
    if not campana_id:
        return []
    return campos_de_empresa(empresa_de_campana(engine, campana_id))


def bloque_para_prompt(campos: Optional[List[str]]) -> str:
    """Bloque que se le agrega al asistente para que sepa con qué datos cuenta la IA.

    Va con las dos advertencias que hacen la diferencia entre usarlos bien y usarlos mal:
    los campos pueden venir vacíos (prompt_details omite los que no tienen dato), y NO son
    lo que se dijo en el audio — es la misma trampa que ya cubre INSTRUCCION_INCIDENCIA en
    gemini.py, donde el modelo reconstruía un llamado mudo a partir del contexto.
    """
    if not campos:
        return ""
    lista = ", ".join(f"'{c}'" for c in campos)
    return (
        "\n\n--- DATOS DEL LLAMADO QUE LA IA RECIBE ADEMÁS DEL AUDIO (bloque Call_details) ---\n"
        f"En cada auditoría, junto con el audio, la IA recibe estos datos de la interacción: {lista}.\n"
        "Al escribir los criterios:\n"
        "- Podés apoyarte en ellos (por ejemplo, condicionar un criterio a la tipificación, "
        "al sentido del llamado o a su duración) en vez de pedirle a la IA que los deduzca "
        "del audio.\n"
        "- NO inventes campos que no estén en esa lista: la IA no tiene forma de consultarlos "
        "y va a responder igual, adivinando.\n"
        "- Cualquiera de esos campos puede venir VACÍO en un llamado puntual (los campos sin "
        "dato no se mandan), así que un criterio que dependa de uno tiene que contemplarlo.\n"
        "- Son datos del SISTEMA, no son lo que se dijo en el audio: sirven de contexto o "
        "para contrastar, nunca como evidencia de que algo se dijo."
    )
