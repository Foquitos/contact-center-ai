"""Conocimiento de referencia de una plantilla: documentos de los chatbots que la IA
lee antes de auditar.

EL PROBLEMA
-----------
Los atributos que dependen de saber cómo es la gestión —"Conocimiento del
producto", "Tipología de la ODS", "perjuicio económico por monto erróneo"— se
calificaban sin saber cuál es la respuesta correcta de la operación. El modelo solo
podía castigar titubeos evidentes: en la plantilla 21 (HIDRA Comercial), desde el
20/08, Conocimiento del producto dio 160 OK / 4 NO OK y Tipología ODS 164/164 OK.

Ese conocimiento ya existe, armado y mantenido por la operación, en los documentos
de los chatbots (`pagina_web.ChatbotDocMarkdown`). Acá se lo lleva al auditor.

LAS DECISIONES
--------------
* **Documento por documento, no el bot entero.** Parte del material de un bot
  explica cómo usar una herramienta (las pantallas de SAP, por ejemplo). El auditor
  escucha el llamado, no ve la pantalla: esos documentos solo suman tokens y el
  riesgo de castigar pasos que no se escuchan. Calidad elige en el editor de la
  plantilla (`calidad.PlantillaConocimiento`).
* **Entero en la parte fija del prompt, no búsqueda por llamado (RAG).** El auditor
  recibe audio: antes de que el modelo lo escuche no hay texto con qué buscar. Haría
  falta una pasada previa (transcribir → buscar → auditar), el doble de llamados y,
  en batch, dos lotes en serie. Los documentos de un bot entran holgados (HIDRA
  Comercial completo son ~30 mil tokens) y en la instrucción de sistema entran solos
  en la caché de contexto (ver AuditorIA/cache_plantillas.py).
* **Se lee de SQL, no de Qdrant.** El índice vectorial es por servidor; el markdown
  es el mismo para dev y prod.
* **Es parte de la versión de la plantilla.** Editar un documento cambia lo que la IA
  lee, así que cambia la versión (ver AuditorIA/versionado.py): si no, las auditorías
  de dos conocimientos distintos quedarían mezcladas en la misma métrica.

Nada de esto cambia a las plantillas sin documentos elegidos: su instrucción de
sistema, su caché y el hash de su versión quedan idénticos.

Migración: scripts/migrations/2026-09-21c_plantilla_conocimiento.sql
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from AuditorIA.cache_plantillas import CHARS_POR_TOKEN

logger = logging.getLogger(__name__)


class ConocimientoNoDisponible(RuntimeError):
    """Falta la migración 2026-09-21c: no hay dónde guardar la selección."""


# Las reglas de uso son lo que separa "sabe cómo es la gestión" de "castiga todo lo
# que no se escucha". Lo crítico es la regla 2: la mitad del material de un bot
# describe pasos en sistemas que ocurren en silencio.
INSTRUCCION_CONOCIMIENTO = (
    "\n\n--- CONOCIMIENTO DE REFERENCIA DE LA OPERACIÓN ---\n"
    "A continuación están los procedimientos, reglas y datos vigentes de la operación que "
    "atiende el operador. Usalos como referencia de cuál es la información y la gestión "
    "CORRECTA, con estas reglas:\n"
    "1. Verificá contra este conocimiento lo que el operador DICE o INFORMA en la "
    "interacción: montos, plazos, requisitos, trámites, qué orden o gestión corresponde "
    "generar y cuándo corresponde derivar. Si lo que informa lo contradice, es "
    "información errónea y así se evalúa en el atributo que corresponda.\n"
    "2. Los hechos salen solo de la interacción: el conocimiento no prueba que algo haya "
    "pasado. Muchos pasos se hacen en los sistemas sin decirse en voz alta (buscar, "
    "validar o cargar datos en pantallas): no penalices al operador por no mencionarlos.\n"
    "3. Si el conocimiento no cubre la situación o es ambiguo, no lo uses para bajar una "
    "calificación: aplicá el criterio de la plantilla.\n"
    "4. Las consignas de la plantilla y de cada atributo mandan sobre este conocimiento: "
    "el conocimiento dice qué es correcto en la operación; la plantilla dice cómo se "
    "califica.\n"
    "5. Si la plantilla pide feedback o comentarios y una calificación negativa (NO OK o "
    "error crítico) se apoya en este conocimiento, indicá ahí qué dice el procedimiento y "
    "el título del documento."
)

CIERRE_CONOCIMIENTO = "\n--- FIN DEL CONOCIMIENTO DE REFERENCIA ---"

# `![alt](/chatbots/imagenes/198)`: el auditor no ve la imagen y la ruta son tokens
# gastados en nada.
_RE_IMAGEN = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_RE_LINEAS_VACIAS = re.compile(r"\n{3,}")


def _tiene_tabla(conn) -> bool:
    """¿Está aplicada la migración? Si no, todo degrada a "sin conocimiento" en vez de
    romper la auditoría."""
    try:
        return conn.execute(text("""
            SELECT 1 FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = 'calidad' AND TABLE_NAME = 'PlantillaConocimiento'
        """)).first() is not None
    except Exception:
        return False


def _titulo(fila: Dict[str, Any]) -> str:
    return (fila.get("titulo") or "").strip() or f"Documento {fila.get('doc_id')}"


def limpiar_markdown(contenido: Optional[str]) -> str:
    """El markdown tal como lo lee el auditor: sin imágenes y sin renglones de más."""
    texto = _RE_IMAGEN.sub("", contenido or "")
    return _RE_LINEAS_VACIAS.sub("\n\n", texto).strip()


def estimar_tokens(caracteres: Optional[int]) -> int:
    """Estimado por caracteres, con la misma relación calibrada que usa la caché."""
    return int((caracteres or 0) / CHARS_POR_TOKEN)


def bloque_para_el_prompt(documentos: List[Dict[str, Any]]) -> str:
    """El bloque que se suma a la instrucción de sistema. Vacío si no hay documentos."""
    if not documentos:
        return ""
    partes = [INSTRUCCION_CONOCIMIENTO]
    for doc in documentos:
        titulo = _titulo(doc).replace('"', "'")
        partes.append(
            f'\n<documento titulo="{titulo}">\n{limpiar_markdown(doc.get("contenido_md"))}\n</documento>'
        )
    partes.append(CIERRE_CONOCIMIENTO)
    return "\n".join(partes)


# --------------------------------------------------------------------------- #
# Lo que lee la auditoría                                                      #
# --------------------------------------------------------------------------- #
# El orden importa: es parte de la clave de la caché y del hash de la versión. Por
# id de bot y no por nombre para que renombrar un bot no cambie nada.
_SQL_DOCUMENTOS_ACTIVOS = """
    SELECT m.id AS doc_id, m.titulo, m.contenido_md, m.chatbot_id
    FROM calidad.PlantillaConocimiento pc
    JOIN pagina_web.ChatbotDocMarkdown m ON m.id = pc.DocID
    WHERE pc.PlantillaID = :pid AND m.activo = 1
    ORDER BY m.chatbot_id, m.orden, m.id
"""


def _documentos_activos(conn, plantilla_id: int) -> List[Dict[str, Any]]:
    if not _tiene_tabla(conn):
        return []
    filas = conn.execute(text(_SQL_DOCUMENTOS_ACTIVOS), {"pid": plantilla_id}).mappings().all()
    return [dict(f) for f in filas]


def documentos_para_auditar(engine: Engine, plantilla_id: int) -> List[Dict[str, Any]]:
    """Los documentos que la IA lee al auditar con esta plantilla (solo los activos).

    Sin migración devuelve []. Un error de lectura con la tabla presente NO se traga:
    auditar sin el conocimiento sería calificar con otro criterio sin avisar.
    """
    with engine.connect() as conn:
        return _documentos_activos(conn, plantilla_id)


def documentos_por_id(engine: Engine, doc_ids: Iterable[Any], *,
                      solo_activos: bool = True) -> List[Dict[str, Any]]:
    """Documentos por id, en el mismo orden que usa la auditoría.

    Para probar una selección sin guardarla (scripts/probar_conocimiento_plantilla.py)
    y, con `solo_activos=False`, para medir cuánto leía una versión vieja de la
    plantilla aunque hoy alguno de sus documentos esté de baja.
    """
    ids = sorted({int(i) for i in doc_ids})
    if not ids:
        return []
    filtro_activo = "AND m.activo = 1" if solo_activos else ""
    with engine.connect() as conn:
        filas = conn.execute(text(f"""
            SELECT m.id AS doc_id, m.titulo, m.contenido_md, m.chatbot_id
            FROM pagina_web.ChatbotDocMarkdown m
            WHERE m.id IN :ids {filtro_activo}
            ORDER BY m.chatbot_id, m.orden, m.id
        """).bindparams(bindparam("ids", expanding=True)), {"ids": ids}).mappings().all()
    return [dict(f) for f in filas]


def huella(conn, plantilla_id: int) -> List[Dict[str, Any]]:
    """Identidad del conocimiento para el snapshot de la versión.

    Va el hash del contenido y no el contenido: 130 mil caracteres por versión
    inflarían el historial, y para saber SI cambió alcanza con el hash. El título va
    entero porque es lo que se muestra al comparar versiones.
    """
    return [
        {
            "doc_id": int(d["doc_id"]),
            "titulo": _titulo(d),
            "sha": hashlib.sha256((d.get("contenido_md") or "").encode("utf-8")).hexdigest()[:16],
        }
        for d in _documentos_activos(conn, plantilla_id)
    ]


# --------------------------------------------------------------------------- #
# Lo que usa el editor                                                         #
# --------------------------------------------------------------------------- #
def catalogo(engine: Engine) -> List[Dict[str, Any]]:
    """Los bots y sus documentos activos (propios y compartidos), para elegir.

    Un documento compartido aparece en cada bot que lo usa, igual que en el
    indexador: es el mismo id, así que elegirlo desde cualquiera es lo mismo.
    """
    with engine.connect() as conn:
        filas = conn.execute(text("""
            SELECT c.id AS chatbot_id, c.slug, c.nombre AS chatbot, c.activo AS chatbot_activo,
                   m.id AS doc_id, m.titulo, m.orden, LEN(m.contenido_md) AS caracteres,
                   m.updated_at,
                   CASE WHEN m.chatbot_id = c.id THEN NULL ELSE duenio.nombre END AS compartido_desde
            FROM pagina_web.Chatbots c
            JOIN pagina_web.ChatbotDocMarkdown m
              ON m.activo = 1
             AND (m.chatbot_id = c.id
                  OR EXISTS (SELECT 1 FROM pagina_web.ChatbotDocVinculo v
                             WHERE v.doc_id = m.id AND v.chatbot_id = c.id))
            JOIN pagina_web.Chatbots duenio ON duenio.id = m.chatbot_id
            ORDER BY c.nombre, m.orden, m.id
        """)).mappings().all()

    bots: Dict[int, Dict[str, Any]] = {}
    for f in filas:
        bot = bots.setdefault(int(f["chatbot_id"]), {
            "chatbot_id": int(f["chatbot_id"]),
            "slug": f["slug"],
            "nombre": f["chatbot"],
            "activo": bool(f["chatbot_activo"]),
            "documentos": [],
        })
        bot["documentos"].append({
            "doc_id": int(f["doc_id"]),
            "titulo": _titulo(f),
            "caracteres": int(f["caracteres"] or 0),
            "tokens": estimar_tokens(f["caracteres"]),
            "actualizado": f["updated_at"],
            "compartido_desde": f["compartido_desde"],
        })
    return list(bots.values())


def resumen_de_plantilla(engine: Engine, plantilla_id: int) -> Dict[str, Any]:
    """Qué documentos tiene elegidos la plantilla, incluidos los dados de baja en su
    bot (que ya no se usan, pero el editor tiene que poder mostrar que se cayeron)."""
    with engine.connect() as conn:
        if not _tiene_tabla(conn):
            return {"disponible": False, "documentos": [], "tokens": 0}
        filas = conn.execute(text("""
            SELECT m.id AS doc_id, m.titulo, m.activo, LEN(m.contenido_md) AS caracteres,
                   m.updated_at, c.id AS chatbot_id, c.nombre AS chatbot
            FROM calidad.PlantillaConocimiento pc
            JOIN pagina_web.ChatbotDocMarkdown m ON m.id = pc.DocID
            JOIN pagina_web.Chatbots c ON c.id = m.chatbot_id
            WHERE pc.PlantillaID = :pid
            ORDER BY m.chatbot_id, m.orden, m.id
        """), {"pid": plantilla_id}).mappings().all()

    documentos = [{
        "doc_id": int(f["doc_id"]),
        "titulo": _titulo(f),
        "activo": bool(f["activo"]),
        "caracteres": int(f["caracteres"] or 0),
        "tokens": estimar_tokens(f["caracteres"]),
        "actualizado": f["updated_at"],
        "chatbot_id": int(f["chatbot_id"]),
        "chatbot": f["chatbot"],
    } for f in filas]
    return {
        "disponible": True,
        "documentos": documentos,
        "tokens": sum(d["tokens"] for d in documentos if d["activo"]),
    }


def guardar_seleccion(
    engine: Engine,
    plantilla_id: int,
    doc_ids: Iterable[Any],
    *,
    user_id: Optional[int] = None,
    solo_activos: bool = True,
) -> None:
    """Reemplaza la selección de la plantilla por `doc_ids`.

    Los que ya estaban conservan su fecha de alta. `solo_activos=False` lo usa el
    restaurar de versiones: la versión vieja pudo tener un documento que hoy está de
    baja y restaurar tiene que dejarla exactamente como estaba (la auditoría lo va a
    ignorar igual mientras siga de baja).

    Levanta ConocimientoNoDisponible sin migración, LookupError si la plantilla no
    existe y ValueError si algún documento no existe (o está de baja, con
    `solo_activos`).
    """
    ids = sorted({int(i) for i in doc_ids})
    with engine.begin() as conn:
        if not _tiene_tabla(conn):
            raise ConocimientoNoDisponible(
                "El conocimiento de referencia todavía no está habilitado en esta base "
                "(falta aplicar la migración 2026-09-21c_plantilla_conocimiento.sql)."
            )
        existe = conn.execute(text("""
            SELECT 1 FROM calidad.Plantillas WHERE PlantillaID = :pid AND IsActive = 1
        """), {"pid": plantilla_id}).first()
        if existe is None:
            raise LookupError(f"No se encontró la plantilla {plantilla_id}.")

        if ids:
            filtro_activo = "AND activo = 1" if solo_activos else ""
            validos = {int(r[0]) for r in conn.execute(text(f"""
                SELECT id FROM pagina_web.ChatbotDocMarkdown WHERE id IN :ids {filtro_activo}
            """).bindparams(bindparam("ids", expanding=True)), {"ids": ids}).all()}
            faltan = [i for i in ids if i not in validos]
            if faltan:
                raise ValueError(
                    "Estos documentos no existen o están dados de baja en su chatbot: "
                    + ", ".join(str(i) for i in faltan)
                )

        actuales = {int(r[0]) for r in conn.execute(text("""
            SELECT DocID FROM calidad.PlantillaConocimiento WHERE PlantillaID = :pid
        """), {"pid": plantilla_id}).all()}

        a_quitar = sorted(actuales - set(ids))
        if a_quitar:
            conn.execute(text("""
                DELETE FROM calidad.PlantillaConocimiento
                WHERE PlantillaID = :pid AND DocID IN :ids
            """).bindparams(bindparam("ids", expanding=True)), {"pid": plantilla_id, "ids": a_quitar})
        for doc_id in sorted(set(ids) - actuales):
            conn.execute(text("""
                INSERT INTO calidad.PlantillaConocimiento (PlantillaID, DocID, CreadoPorUsuarioID)
                VALUES (:pid, :did, :uid)
            """), {"pid": plantilla_id, "did": doc_id, "uid": user_id})

    logger.info("Plantilla %s: conocimiento de referencia = %s (usuario %s)",
                plantilla_id, ids or "(ninguno)", user_id)


def copiar_seleccion(conn, origen_id: int, destino_id: int) -> None:
    """Al duplicar una plantilla, la copia audita con el mismo conocimiento. Corre en
    la transacción del duplicado; sin migración no hace nada."""
    if not _tiene_tabla(conn):
        return
    conn.execute(text("""
        INSERT INTO calidad.PlantillaConocimiento (PlantillaID, DocID, CreadoPorUsuarioID)
        SELECT :destino, DocID, CreadoPorUsuarioID
        FROM calidad.PlantillaConocimiento
        WHERE PlantillaID = :origen
    """), {"origen": origen_id, "destino": destino_id})
