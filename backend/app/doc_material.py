"""Bandeja de material pendiente del asistente de documentación
(pagina_web.ChatbotDocMaterial).

POR QUÉ EXISTE
--------------
Antes, subir un archivo era en un mismo gesto mandarlo a la IA: no había dónde
dejarlo "para cuando termine de juntar todo". Como cada corrida del asistente
regenera el markdown COMPLETO de los documentos que toca, cargar el material de a
poco significaba pagar esa regeneración una vez por cosita agregada (y, con el
reindexado automático al guardar, una reconstrucción entera del índice también:
Benefix llegó a la versión 84).

Acá el material se guarda tal cual llega —sin llamar a la IA— y se procesa TODO
JUNTO en una sola corrida cuando la persona dice que terminó. Al aceptar la
propuesta, el material consumido se borra (`consumir`).

El material es contenido, no ejecución: como ChatbotDocMarkdown, la tabla es
compartida entre dev y prod y no lleva `environment`.
"""
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import bindparam, text
from sqlalchemy.types import LargeBinary, NVARCHAR

logger = logging.getLogger(__name__)

# Tope de la bandeja por bot. Los archivos viven en la base hasta que se procesan,
# así que hay un techo: si alguien deja 40 PDF olvidados, que lo diga la pantalla y
# no el disco del server.
MAX_MATERIAL_BOT_BYTES = 100 * 1024 * 1024

# Cuánto texto se muestra en la lista de la bandeja (el contenido entero puede ser
# de cientos de miles de caracteres y no tiene sentido mandarlo al navegador).
PREVIEW_CHARS = 300


def _get_engine():
    from app.database import engine
    return engine


# El texto va con bind NVARCHAR explícito: el material pegado trae acentos y emojis
# y en VARCHAR se pierden en el bind (mismo problema que ya tuvo doc_jobs). El archivo
# va con bind LargeBinary por lo mismo del otro lado: sin tipo declarado, el driver
# infiere el del primer valor y un PDF de 20 MB no entra donde infiera varbinary corto.
_INSERT = text("""
    INSERT INTO pagina_web.ChatbotDocMaterial
        (chatbot_id, tipo, nombre, mime, texto, datos, tamano_bytes, nota, doc_id_destino, es_actualizacion, created_by)
    OUTPUT INSERTED.id
    VALUES (:cid, :tipo, :nombre, :mime, :texto, :datos, :tam, :nota, :destino, :es_act, :uid)
""").bindparams(bindparam("texto", type_=NVARCHAR), bindparam("nota", type_=NVARCHAR),
                bindparam("datos", type_=LargeBinary))

_LISTAR = text("""
    SELECT m.id, m.chatbot_id, m.tipo, m.nombre, m.mime, m.tamano_bytes, m.nota,
           m.doc_id_destino, d.titulo AS destino_titulo,
           COALESCE(m.es_actualizacion, 0) AS es_actualizacion,
           m.created_by, m.created_at,
           LEFT(m.texto, :prev) AS preview
    FROM pagina_web.ChatbotDocMaterial m
    LEFT JOIN pagina_web.ChatbotDocMarkdown d ON d.id = m.doc_id_destino
    WHERE m.chatbot_id = :cid
    ORDER BY m.id
""")


def listar(chatbot_id: int) -> List[Dict[str, Any]]:
    """Metadatos de la bandeja (sin los blobs: listar no baja los archivos)."""
    with _get_engine().connect() as conn:
        filas = conn.execute(_LISTAR, {"cid": chatbot_id, "prev": PREVIEW_CHARS}).mappings().all()
    return [dict(f) for f in filas]


def total_bytes(chatbot_id: int) -> int:
    with _get_engine().connect() as conn:
        return conn.execute(text("""
            SELECT COALESCE(SUM(CAST(tamano_bytes AS BIGINT)), 0)
            FROM pagina_web.ChatbotDocMaterial WHERE chatbot_id = :cid
        """), {"cid": chatbot_id}).scalar() or 0


def agregar(chatbot_id: int, fuentes: List[Dict[str, Any]], nota: Optional[str],
            created_by: Optional[int], doc_id_destino: Optional[int] = None,
            es_actualizacion: bool = False) -> List[int]:
    """Guarda fuentes ya validadas (el formato es el de _normalizar_fuentes).

    Devuelve los ids creados. No llama a la IA: para eso está el botón de procesar.
    """
    ids: List[int] = []
    with _get_engine().begin() as conn:
        for f in fuentes:
            if f.get("tipo") == "texto":
                texto = f.get("texto") or ""
                params = {"tipo": "texto", "nombre": f.get("nombre") or "Texto pegado",
                          "mime": None, "texto": texto, "datos": None,
                          "tam": len(texto.encode("utf-8"))}
            else:
                datos = f.get("datos") or b""
                params = {"tipo": "archivo", "nombre": f.get("nombre"), "mime": f.get("mime"),
                          "texto": None, "datos": datos, "tam": len(datos)}
            params.update({"cid": chatbot_id, "nota": (nota or None),
                           "destino": doc_id_destino, "es_act": 1 if es_actualizacion else 0,
                           "uid": created_by})
            ids.append(conn.execute(_INSERT, params).first().id)

    logger.info(f"Chatbot {chatbot_id}: {len(ids)} ítem(s) sumados al material pendiente.")
    return ids


def ids_pendientes(chatbot_id: int) -> List[int]:
    """Ids de toda la bandeja del bot, para congelarlos en el payload del job.

    El job trabaja sobre una lista FIJA de ids y no sobre "todo lo que haya": si
    alguien carga material nuevo mientras corre, no se procesa a medias ni se borra
    al aceptar la propuesta.
    """
    with _get_engine().connect() as conn:
        filas = conn.execute(text("""
            SELECT id FROM pagina_web.ChatbotDocMaterial WHERE chatbot_id = :cid ORDER BY id
        """), {"cid": chatbot_id}).all()
    return [f.id for f in filas]


def _indicacion(fila) -> str:
    """Lo que dijo quien cargó el material: si es una actualización/reemplazo, la nota
    y, si lo cargó desde la fila de un documento, a qué documento apuntaba.

    El destino es una INDICACIÓN, no una imposición: la corrida sigue siendo un único
    merge global y la IA rutea. Forzar `doc_ids` por ítem partiría la corrida en una
    llamada por documento, que es exactamente lo que se vino a evitar.
    """
    partes = []
    if fila.get("es_actualizacion"):
        partes.append(
            "ACTUALIZACIÓN / REEMPLAZO O BAJA VIGENTE: Esta información modifica/actualiza precios, "
            "pasos o procedimientos anteriores, o solicita dar de baja/eliminar una sección obsoleta. "
            "Si contradice información previa o pide eliminar un procedimiento/sección, SOBRESCRIBIR "
            "o REMOVER la información anterior en el documento correspondiente"
        )
    if (fila.get("destino_titulo") or "").strip():
        partes.append(f"va al documento «{fila['destino_titulo'].strip()}»")
    if (fila.get("nota") or "").strip():
        partes.append(fila["nota"].strip())
    return "; ".join(partes)


def cargar_fuentes(material_ids: List[int]) -> List[Dict[str, Any]]:
    """Levanta el material y lo devuelve en el formato que consume asistente_docs.

    La indicación de quien lo cargó (nota + documento destino) se antepone al
    contenido: a la IA le sirve tanto como el material.
    """
    if not material_ids:
        return []

    consulta = text("""
        SELECT m.id, m.tipo, m.nombre, m.mime, m.texto, m.datos, m.nota,
               COALESCE(m.es_actualizacion, 0) AS es_actualizacion,
               d.titulo AS destino_titulo
        FROM pagina_web.ChatbotDocMaterial m
        LEFT JOIN pagina_web.ChatbotDocMarkdown d
               ON d.id = m.doc_id_destino AND d.activo = 1
        WHERE m.id IN :ids
        ORDER BY m.id
    """).bindparams(bindparam("ids", expanding=True))

    with _get_engine().connect() as conn:
        filas = conn.execute(consulta, {"ids": list(material_ids)}).mappings().all()

    salida: List[Dict[str, Any]] = []
    for f in filas:
        nota = _indicacion(f)
        if f["tipo"] == "texto":
            texto = (f["texto"] or "").strip()
            if not texto:
                continue
            if nota:
                texto = f"[Indicación de quien cargó el material: {nota}]\n\n{texto}"
            salida.append({"tipo": "texto", "texto": texto, "nombre": f["nombre"]})
        else:
            datos = f["datos"]
            if not datos:
                continue
            nombre = f["nombre"]
            if nota:
                nombre = f"{nombre or 'archivo'} — {nota}"
            salida.append({"tipo": "archivo", "datos": bytes(datos),
                           "mime": f["mime"], "nombre": nombre})
    return salida


def borrar(chatbot_id: int, material_id: int) -> bool:
    with _get_engine().begin() as conn:
        result = conn.execute(text("""
            DELETE FROM pagina_web.ChatbotDocMaterial WHERE id = :mid AND chatbot_id = :cid
        """), {"mid": material_id, "cid": chatbot_id})
    return bool(result.rowcount)


def vaciar(chatbot_id: int) -> int:
    with _get_engine().begin() as conn:
        result = conn.execute(text("""
            DELETE FROM pagina_web.ChatbotDocMaterial WHERE chatbot_id = :cid
        """), {"cid": chatbot_id})
    return result.rowcount


def consumir(conn, chatbot_id: int, material_ids: List[int]) -> int:
    """Borra el material que entró en la propuesta que se acaba de aceptar.

    Recibe la conexión de afuera a propósito: corre en la MISMA transacción que el
    guardado de los documentos. Si el guardado se cae, el material sigue en la
    bandeja y se puede volver a procesar.
    """
    if not material_ids:
        return 0
    consulta = text("""
        DELETE FROM pagina_web.ChatbotDocMaterial
        WHERE chatbot_id = :cid AND id IN :ids
    """).bindparams(bindparam("ids", expanding=True))
    result = conn.execute(consulta, {"cid": chatbot_id, "ids": list(material_ids)})
    return result.rowcount
