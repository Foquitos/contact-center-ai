"""Gestión, extracción y almacenamiento de imágenes de chatbots RAG
(pagina_web.ChatbotImagenes).

POR QUÉ EXISTE
--------------
El chatbot RAG ya cuenta con instrucciones en el system prompt y soporte de
renderizado en el frontend para mostrar imágenes de apoyo visual (![alt](url)).
Sin embargo, dependía de que los analistas de calidad subieran las imágenes a un
gestor externo y pegaran el link a mano, flujo que no se usaba por ser engorroso.

Acá las imágenes se almacenan directamente en SQL Server (pagina_web.ChatbotImagenes):
- Paridad dev/prod: SRV00 y SRV01 comparten la base de datos Acme, por lo que
  las imágenes subidas en un entorno están disponibles de inmediato en el otro
  sin requerir sincronización de carpetas en disco.
- Normalización automática (Pillow): corrección de orientación EXIF y reescalado
  si el lado mayor supera 1600 px para evitar tamaños excesivos en la BD (~80-400 KB).
- Deduplicación por hash SHA-256: no se duplican bytes si una imagen ya existe
  para el mismo chatbot.
- Extracción automática: al procesar material crudo (.docx, .pptx, .xlsx, .pdf e
  imágenes sueltas), se extraen las imágenes relevantes y se le pasan a Gemini sus
  URLs para que las inserte en los procedimientos correspondientes. De un .pptx se
  guarda además en qué diapositiva estaba cada captura, y de un PDF en qué página:
  sin esa referencia la IA recibe una lista de "image7.png" sin saber dónde va.
"""
import hashlib
import io
import logging
import os
import re
import zipfile
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageOps
import pypdf
from sqlalchemy import bindparam, text
from sqlalchemy.types import LargeBinary, NVARCHAR, VARCHAR

logger = logging.getLogger(__name__)

# Lado mayor al que se reescala una imagen antes de guardarla si es más grande.
MAX_LADO_IMAGEN = 1600

# Filtros para descartar viñetas, íconos decorativos o separadores al extraer
MIN_BYTES_EXTRACCION = 3 * 1024       # 3 KB
MIN_DIMENSION_EXTRACCION = 80         # 80x80 px

_INSERT_IMAGEN = text("""
    INSERT INTO pagina_web.ChatbotImagenes
        (chatbot_id, nombre, mime, datos, tamano_bytes, ancho, alto, descripcion, hash_sha256, created_by)
    OUTPUT INSERTED.id
    VALUES (:cid, :nombre, :mime, :datos, :tam, :ancho, :alto, :desc, :hash, :uid)
""").bindparams(
    bindparam("nombre", type_=NVARCHAR),
    bindparam("mime", type_=VARCHAR),
    bindparam("datos", type_=LargeBinary),
    bindparam("desc", type_=NVARCHAR),
    bindparam("hash", type_=VARCHAR),
)

_BUSCAR_POR_HASH = text("""
    SELECT TOP (1) id, chatbot_id, nombre, mime, tamano_bytes, ancho, alto, descripcion, hash_sha256, created_at
    FROM pagina_web.ChatbotImagenes
    WHERE chatbot_id = :cid AND hash_sha256 = :hash
""")

_OBTENER_POR_ID = text("""
    SELECT id, chatbot_id, nombre, mime, datos, tamano_bytes, ancho, alto, descripcion, hash_sha256, created_at
    FROM pagina_web.ChatbotImagenes
    WHERE id = :id
""")

_LISTAR_POR_BOT = text("""
    SELECT id, chatbot_id, nombre, mime, tamano_bytes, ancho, alto, descripcion, hash_sha256, created_by, created_at
    FROM pagina_web.ChatbotImagenes
    WHERE chatbot_id = :cid
    ORDER BY id DESC
""")

_BORRAR_IMAGEN = text("""
    DELETE FROM pagina_web.ChatbotImagenes
    WHERE id = :id AND chatbot_id = :cid
""")


def _get_engine():
    from app.database import engine
    return engine


def normalizar_imagen_bytes(datos: bytes, nombre: str = "imagen") -> Tuple[bytes, str, int, int]:
    """Valida, corrige orientación EXIF y reescala una imagen si es necesario.

    Returns:
        (datos_optimizados, mime_detectado, ancho, alto)
    Raises:
        ValueError si el archivo no es una imagen válida o está dañado.
    """
    try:
        with Image.open(io.BytesIO(datos)) as img:
            img.load()
            # Corregir orientación según metadata EXIF (ej: fotos de celulares)
            corregida = ImageOps.exif_transpose(img) or img

            ancho, alto = corregida.size
            if ancho <= 0 or alto <= 0:
                raise ValueError(f"Dimensiones de imagen inválidas ({ancho}x{alto})")

            # Reescalar proporcionalmente si excede el lado mayor permitido
            necesita_reescalado = max(ancho, alto) > MAX_LADO_IMAGEN
            if necesita_reescalado:
                ratio = MAX_LADO_IMAGEN / float(max(ancho, alto))
                nuevo_ancho = max(1, int(ancho * ratio))
                nuevo_alto = max(1, int(alto * ratio))
                corregida = corregida.resize((nuevo_ancho, nuevo_alto), Image.Resampling.LANCZOS)
                ancho, alto = corregida.size

            formato = (img.format or "PNG").upper()
            if formato not in ("PNG", "JPEG", "JPG", "WEBP", "GIF"):
                formato = "PNG"

            mime = "image/jpeg" if formato in ("JPEG", "JPG") else f"image/{formato.lower()}"

            # Si se corrigió orientación o reescaló, re-exportamos
            buf = io.BytesIO()
            if formato in ("JPEG", "JPG"):
                if corregida.mode in ("RGBA", "P"):
                    corregida = corregida.convert("RGB")
                corregida.save(buf, format="JPEG", quality=85, optimize=True)
            elif formato == "WEBP":
                corregida.save(buf, format="WEBP", quality=85)
            else:
                corregida.save(buf, format="PNG", optimize=True)

            datos_optimizados = buf.getvalue()
            return datos_optimizados, mime, ancho, alto
    except Exception as e:
        logger.warning(f"Error procesando imagen '{nombre}': {e}")
        raise ValueError(f"No se pudo procesar la imagen '{nombre}'. ¿El archivo está dañado o en un formato no soportado?")


def guardar_imagen(
    chatbot_id: int,
    nombre: str,
    datos: bytes,
    mime: Optional[str] = None,
    descripcion: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Guarda una imagen en pagina_web.ChatbotImagenes con deduplicación por SHA-256.

    Devuelve dict con metadatos y URL lista para usar en markdown.
    """
    if not datos:
        raise ValueError("Los datos de la imagen no pueden estar vacíos.")

    datos_opt, mime_opt, ancho, alto = normalizar_imagen_bytes(datos, nombre=nombre)
    tamano_bytes = len(datos_opt)
    hash_sha256 = hashlib.sha256(datos_opt).hexdigest()

    nombre_limpio = (nombre or "imagen").strip()[:400]
    desc_limpia = (descripcion or "").strip()[:1000] or None

    engine = _get_engine()
    with engine.begin() as conn:
        # Verificar deduplicación para este chatbot
        existente = conn.execute(_BUSCAR_POR_HASH, {"cid": chatbot_id, "hash": hash_sha256}).mappings().first()
        if existente:
            logger.info(f"Chatbot {chatbot_id}: imagen '{nombre_limpio}' ya existía (id={existente['id']}), se reutiliza.")
            rec = dict(existente)
            rec["url"] = f"/chatbots/imagenes/{rec['id']}"
            rec["markdown"] = f"![{rec['descripcion'] or rec['nombre']}]({rec['url']})"
            return rec

        row = conn.execute(_INSERT_IMAGEN, {
            "cid": chatbot_id,
            "nombre": nombre_limpio,
            "mime": mime_opt,
            "datos": datos_opt,
            "tam": tamano_bytes,
            "ancho": ancho,
            "alto": alto,
            "desc": desc_limpia,
            "hash": hash_sha256,
            "uid": user_id,
        }).first()

    imagen_id = row.id
    logger.info(f"Chatbot {chatbot_id}: imagen guardada con id {imagen_id} ({ancho}x{alto}, {tamano_bytes} bytes).")

    return {
        "id": imagen_id,
        "chatbot_id": chatbot_id,
        "nombre": nombre_limpio,
        "mime": mime_opt,
        "tamano_bytes": tamano_bytes,
        "ancho": ancho,
        "alto": alto,
        "descripcion": desc_limpia,
        "hash_sha256": hash_sha256,
        "url": f"/chatbots/imagenes/{imagen_id}",
        "markdown": f"![{desc_limpia or nombre_limpio}](/chatbots/imagenes/{imagen_id})",
    }


def obtener_imagen(imagen_id: int) -> Optional[Dict[str, Any]]:
    """Recupera la imagen completa (incluyendo binario) para servir por HTTP."""
    engine = _get_engine()
    with engine.connect() as conn:
        row = conn.execute(_OBTENER_POR_ID, {"id": imagen_id}).mappings().first()
    return dict(row) if row else None


def listar_imagenes(chatbot_id: int) -> List[Dict[str, Any]]:
    """Metadatos de todas las imágenes de un chatbot (sin el campo de datos binarios)."""
    engine = _get_engine()
    with engine.connect() as conn:
        filas = conn.execute(_LISTAR_POR_BOT, {"cid": chatbot_id}).mappings().all()

    salida = []
    for f in filas:
        d = dict(f)
        d["url"] = f"/chatbots/imagenes/{d['id']}"
        d["markdown"] = f"![{d['descripcion'] or d['nombre']}](/chatbots/imagenes/{d['id']})"
        salida.append(d)
    return salida


def borrar_imagen(chatbot_id: int, imagen_id: int) -> bool:
    """Elimina una imagen de la base de datos."""
    engine = _get_engine()
    with engine.begin() as conn:
        res = conn.execute(_BORRAR_IMAGEN, {"id": imagen_id, "cid": chatbot_id})
    return bool(res.rowcount)


# ---------------------------------------------------------------------------- #
# Extracción automática desde material (.docx, .pptx, .xlsx, .pdf, imágenes)    #
# ---------------------------------------------------------------------------- #

def _orden_natural(ruta: str) -> tuple:
    """Ordena 'media/image10.png' DESPUÉS de 'media/image2.png'.

    Sin esto las imágenes le llegan a la IA en orden alfabético (image1, image10,
    image11, image2...), que no es el orden del documento, y las ubica mal."""
    base = os.path.basename(ruta)
    m = re.search(r"(\d+)", base)
    return (int(m.group(1)) if m else 0, base)


def _imagen_util(img_data: bytes) -> bool:
    """Descarta lo diminuto (viñetas, íconos, separadores) y lo que no es imagen
    (los Office también guardan WMF/EMF, audio o video en la carpeta media)."""
    if len(img_data) < MIN_BYTES_EXTRACCION:
        return False
    try:
        with Image.open(io.BytesIO(img_data)) as im:
            w, h = im.size
    except Exception:
        return False
    return w >= MIN_DIMENSION_EXTRACCION and h >= MIN_DIMENSION_EXTRACCION


def _extraer_de_zip_office(datos: bytes, nombre_origen: str, chatbot_id: int,
                           user_id: Optional[int], carpeta: str,
                           ubicaciones: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """Guarda las imágenes embebidas de un OOXML (.docx/.pptx/.xlsx son ZIPs).

    `ubicaciones` mapea la ruta interna a una referencia legible ("diapositiva 4"),
    que se guarda en el nombre y la descripción para que la IA sepa a qué parte del
    material pertenece cada captura.
    """
    guardadas = []
    try:
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            rutas = sorted((n for n in z.namelist() if n.startswith(carpeta)), key=_orden_natural)
            for item in rutas:
                img_data = z.read(item)
                if not _imagen_util(img_data):
                    continue

                ubicacion = (ubicaciones or {}).get(item)
                base_item = os.path.basename(item)
                nombre_img = f"{nombre_origen} ({ubicacion + ' - ' if ubicacion else ''}{base_item})"
                descripcion = (f"Imagen de la {ubicacion} de {nombre_origen}" if ubicacion
                               else f"Imagen extraída de {nombre_origen}")
                try:
                    rec = guardar_imagen(
                        chatbot_id=chatbot_id,
                        nombre=nombre_img,
                        datos=img_data,
                        descripcion=descripcion,
                        user_id=user_id,
                    )
                    rec["fuente_origen"] = nombre_origen
                    guardadas.append(rec)
                except Exception as e:
                    logger.warning(f"No se pudo guardar imagen embebida {item} de {nombre_origen}: {e}")
    except Exception as e:
        logger.warning(f"Error extrayendo imágenes de '{nombre_origen}': {e}")
    return guardadas


def _extraer_de_docx(datos: bytes, nombre_origen: str, chatbot_id: int, user_id: Optional[int]) -> List[Dict[str, Any]]:
    """Extrae imágenes embebidas dentro de un archivo .docx (carpeta word/media/)."""
    return _extraer_de_zip_office(datos, nombre_origen, chatbot_id, user_id, "word/media/")


def _extraer_de_pdf(datos: bytes, nombre_origen: str, chatbot_id: int, user_id: Optional[int]) -> List[Dict[str, Any]]:
    """Extrae imágenes de las páginas de un archivo PDF usando pypdf."""
    guardadas = []
    try:
        reader = pypdf.PdfReader(io.BytesIO(datos))
        for num_pag, page in enumerate(reader.pages, start=1):
            for img_idx, img_obj in enumerate(page.images, start=1):
                img_data = img_obj.data
                if not _imagen_util(img_data):
                    continue

                nombre_img = f"{nombre_origen} (pág. {num_pag} - img {img_idx})"
                try:
                    rec = guardar_imagen(
                        chatbot_id=chatbot_id,
                        nombre=nombre_img,
                        datos=img_data,
                        descripcion=f"Imagen de pág. {num_pag} de {nombre_origen}",
                        user_id=user_id,
                    )
                    rec["fuente_origen"] = nombre_origen
                    guardadas.append(rec)
                except Exception as e:
                    logger.warning(f"No se pudo guardar imagen pág {num_pag} de {nombre_origen}: {e}")
    except Exception as e:
        logger.warning(f"Error extrayendo imágenes de PDF '{nombre_origen}': {e}")
    return guardadas


def _extraer_de_pptx(datos: bytes, nombre_origen: str, chatbot_id: int, user_id: Optional[int]) -> List[Dict[str, Any]]:
    """Extrae imágenes embebidas dentro de un archivo .pptx (carpeta ppt/media/),
    anotando en qué diapositiva aparece cada una."""
    from AuditorIA import office_a_texto

    try:
        ubicaciones = {ruta: f"diapositiva {n}" for ruta, n in office_a_texto.media_por_diapositiva(datos).items()}
    except Exception:
        logger.warning(f"No se pudo mapear las imágenes por diapositiva de '{nombre_origen}'.")
        ubicaciones = {}
    return _extraer_de_zip_office(datos, nombre_origen, chatbot_id, user_id, "ppt/media/", ubicaciones)


def _extraer_de_xlsx(datos: bytes, nombre_origen: str, chatbot_id: int, user_id: Optional[int]) -> List[Dict[str, Any]]:
    """Extrae imágenes embebidas dentro de una planilla .xlsx (carpeta xl/media/)."""
    return _extraer_de_zip_office(datos, nombre_origen, chatbot_id, user_id, "xl/media/")


def _extraer_embebidas(datos: bytes, nombre: str, chatbot_id: int,
                       user_id: Optional[int]) -> List[Dict[str, Any]]:
    """Extrae las imágenes de un documento según lo que REALMENTE es.

    Se mira el contenido y no la extensión ni el mime: el navegador manda los
    Office como application/octet-stream (o vacío) más seguido de lo que parece, y
    un .pptx guardado como .ppt seguía siendo un ZIP que se podía abrir.
    """
    if datos.startswith(b"%PDF-"):
        return _extraer_de_pdf(datos, nombre, chatbot_id, user_id)
    if not datos.startswith(b"PK\x03\x04"):
        return []  # Office viejo (binario), texto plano, o cualquier otra cosa.

    try:
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            partes = z.namelist()
    except Exception as e:
        logger.warning(f"No se pudo abrir '{nombre}' como documento Office: {e}")
        return []

    if any(p.startswith("ppt/slides/slide") for p in partes):
        return _extraer_de_pptx(datos, nombre, chatbot_id, user_id)
    if "word/document.xml" in partes:
        return _extraer_de_docx(datos, nombre, chatbot_id, user_id)
    if "xl/workbook.xml" in partes:
        return _extraer_de_xlsx(datos, nombre, chatbot_id, user_id)
    # ZIP de Office al que no le reconocemos la parte principal: si trae imágenes
    # donde Office las guarda, se sacan igual antes de darlo por perdido.
    for carpeta in ("ppt/media/", "word/media/", "xl/media/"):
        if any(p.startswith(carpeta) for p in partes):
            return _extraer_de_zip_office(datos, nombre, chatbot_id, user_id, carpeta)
    return []


def extraer_imagenes_de_fuentes(
    fuentes: List[Dict[str, Any]],
    chatbot_id: int,
    user_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Recorre las fuentes crudas de una corrida (imágenes sueltas, Word .docx,
    PowerPoint .pptx, Excel .xlsx o PDF), extrae sus imágenes, las almacena en la base
    de datos y devuelve la lista de imágenes disponibles para que la IA las referencie
    en el markdown.
    """
    if not fuentes or not chatbot_id:
        return []

    todas_guardadas: List[Dict[str, Any]] = []
    ids_vistos = set()

    for f in fuentes:
        if f.get("tipo") != "archivo":
            continue

        datos = f.get("datos")
        if not datos:
            continue

        mime = (f.get("mime") or "").lower()
        nombre = (f.get("nombre") or "archivo").strip()
        ext = os.path.splitext(nombre)[1].lower()

        # 1. Imagen directa (PNG, JPG, WEBP, GIF, etc.)
        if mime.startswith("image/") or ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            try:
                rec = guardar_imagen(
                    chatbot_id=chatbot_id,
                    nombre=nombre,
                    datos=datos,
                    mime=mime,
                    descripcion=nombre,
                    user_id=user_id,
                )
                if rec["id"] not in ids_vistos:
                    ids_vistos.add(rec["id"])
                    rec["fuente_origen"] = nombre
                    todas_guardadas.append(rec)
            except Exception as e:
                logger.warning(f"No se pudo procesar archivo de imagen directo '{nombre}': {e}")

        # 2. Documento con imágenes adentro (Word, PowerPoint, Excel o PDF)
        else:
            for rec in _extraer_embebidas(datos, nombre, chatbot_id, user_id):
                if rec["id"] not in ids_vistos:
                    ids_vistos.add(rec["id"])
                    todas_guardadas.append(rec)

    if todas_guardadas:
        logger.info(f"Chatbot {chatbot_id}: {len(todas_guardadas)} imagen(es) listas para referenciar en el markdown.")

    return todas_guardadas
