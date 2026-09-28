"""Tests del módulo de imágenes de chatbots (chatbot_imagenes).

Cubre:
- Normalización, reescalado y validación de imágenes con Pillow.
- Deduplicación por hash SHA-256.
- Extracción automática desde archivos Word (.docx) y PDF con filtrado de íconos/logos chicos (< 3KB o < 80x80).
- Métodos CRUD (guardar, obtener, listar, borrar).
- Endpoints de FastAPI para servir imágenes y administración.
"""
import io
import os
import zipfile
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import chatbot_imagenes
from app.routers import chatbot as chatbot_router
from app.security import get_current_active_user
from main import app


def _crear_imagen_bytes(ancho=200, alto=200, formato="PNG", con_ruido=True) -> bytes:
    """Crea una imagen sintética en memoria con Pillow.
    Si con_ruido=True, genera bytes aleatorios para superar el umbral de 3 KB con compresión.
    """
    buf = io.BytesIO()
    if con_ruido:
        img = Image.frombytes("RGB", (ancho, alto), os.urandom(ancho * alto * 3))
    else:
        img = Image.new("RGB", (ancho, alto), color="red")
    img.save(buf, format=formato)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# 1) Normalización y validación con Pillow                                     #
# --------------------------------------------------------------------------- #

def test_normalizar_imagen_valida():
    raw = _crear_imagen_bytes(ancho=300, alto=150, formato="PNG", con_ruido=False)
    norm, mime, ancho, alto = chatbot_imagenes.normalizar_imagen_bytes(raw, "captura.png")
    assert ancho == 300
    assert alto == 150
    assert mime == "image/png"
    assert isinstance(norm, bytes)
    assert len(norm) > 0


def test_normalizar_imagen_reescala_grandes():
    # Imagen de 2400 x 1200 (debe reescalarse a max 1600 en su lado mayor)
    raw = _crear_imagen_bytes(ancho=2400, alto=1200, formato="JPEG", con_ruido=False)
    norm, mime, ancho, alto = chatbot_imagenes.normalizar_imagen_bytes(raw, "foto_grande.jpg")
    assert ancho == 1600
    assert alto == 800
    assert mime == "image/jpeg"


def test_normalizar_bytes_corruptos_lanza_error():
    with pytest.raises(ValueError, match="No se pudo procesar la imagen"):
        chatbot_imagenes.normalizar_imagen_bytes(b"esto_no_es_una_imagen", "test.png")


def test_normalizar_extension_mime_fallback():
    raw = _crear_imagen_bytes(ancho=100, alto=100, formato="PNG", con_ruido=False)
    _, mime, _, _ = chatbot_imagenes.normalizar_imagen_bytes(raw, "imagen_sin_ext")
    assert mime == "image/png"


# --------------------------------------------------------------------------- #
# 2) Extracción automática desde .docx                                        #
# --------------------------------------------------------------------------- #

def test_extraer_de_docx_filtra_chicas_y_guarda_grandes():
    docx_buf = io.BytesIO()
    with zipfile.ZipFile(docx_buf, "w") as zf:
        # Imagen grande válida (200x200 con ruido: pesa > 3KB)
        img_valida = _crear_imagen_bytes(ancho=200, alto=200, formato="PNG", con_ruido=True)
        assert len(img_valida) > chatbot_imagenes.MIN_BYTES_EXTRACCION
        zf.writestr("word/media/image1.png", img_valida)

        # Ícono diminuto (20x20, debe ser descartado por dimensiones < 80x80)
        img_chica = _crear_imagen_bytes(ancho=20, alto=20, formato="PNG", con_ruido=False)
        zf.writestr("word/media/image2.png", img_chica)

        # Archivo que no es imagen dentro de media
        zf.writestr("word/media/dummy.txt", b"no es imagen")

    docx_bytes = docx_buf.getvalue()

    with patch("app.chatbot_imagenes.guardar_imagen") as mock_guardar:
        mock_guardar.return_value = {
            "id": 42,
            "chatbot_id": 1,
            "nombre": "image1.png",
            "hash_sha256": "abc",
            "url": "/chatbots/imagenes/42",
            "ancho": 200,
            "alto": 200,
            "fuente_origen": "manual.docx",
        }

        extraidas = chatbot_imagenes._extraer_de_docx(docx_bytes, "manual.docx", chatbot_id=1, user_id=10)

        # Debe haber guardado exactamente 1 imagen (la grande) y filtrado la de 20x20
        assert len(extraidas) == 1
        assert extraidas[0]["id"] == 42
        assert mock_guardar.call_count == 1
        call_kwargs = mock_guardar.call_args[1]
        assert call_kwargs["chatbot_id"] == 1
        assert "image1.png" in call_kwargs["nombre"]


def test_extraer_de_pptx_filtra_chicas_y_guarda_grandes():
    pptx_buf = io.BytesIO()
    with zipfile.ZipFile(pptx_buf, "w") as zf:
        # Imagen grande válida (200x200 con ruido: pesa > 3KB)
        img_valida = _crear_imagen_bytes(ancho=200, alto=200, formato="PNG", con_ruido=True)
        assert len(img_valida) > chatbot_imagenes.MIN_BYTES_EXTRACCION
        zf.writestr("ppt/media/image1.png", img_valida)

        # Ícono diminuto (20x20, debe ser descartado por dimensiones < 80x80)
        img_chica = _crear_imagen_bytes(ancho=20, alto=20, formato="PNG", con_ruido=False)
        zf.writestr("ppt/media/image2.png", img_chica)

        # Archivo que no es imagen dentro de media
        zf.writestr("ppt/media/dummy.txt", b"no es imagen")

    pptx_bytes = pptx_buf.getvalue()

    with patch("app.chatbot_imagenes.guardar_imagen") as mock_guardar:
        mock_guardar.return_value = {
            "id": 43,
            "chatbot_id": 2,
            "nombre": "image1.png",
            "hash_sha256": "def",
            "url": "/chatbots/imagenes/43",
            "ancho": 200,
            "alto": 200,
            "fuente_origen": "presentacion.pptx",
        }

        extraidas = chatbot_imagenes._extraer_de_pptx(pptx_bytes, "presentacion.pptx", chatbot_id=2, user_id=10)

        assert len(extraidas) == 1
        assert extraidas[0]["id"] == 43
        assert mock_guardar.call_count == 1
        call_kwargs = mock_guardar.call_args[1]
        assert call_kwargs["chatbot_id"] == 2
        assert "image1.png" in call_kwargs["nombre"]


def test_una_captura_de_pptx_dice_de_que_diapositiva_salio():
    """Sin la diapositiva, la IA recibe una lista de 'image7.png' y no sabe en qué
    paso del procedimiento va cada captura."""
    imagen = _crear_imagen_bytes(ancho=200, alto=200, formato="PNG", con_ruido=True)
    pptx_buf = io.BytesIO()
    with zipfile.ZipFile(pptx_buf, "w") as zf:
        for n in (1, 2):
            zf.writestr(f"ppt/slides/slide{n}.xml", '<p:sld xmlns:p="p"><p:cSld><p:spTree/></p:cSld></p:sld>')
        zf.writestr("ppt/slides/_rels/slide2.xml.rels",
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Target="../media/image1.png" '
                    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"/>'
                    '</Relationships>')
        zf.writestr("ppt/media/image1.png", imagen)

    with patch("app.chatbot_imagenes.guardar_imagen") as mock_guardar:
        mock_guardar.return_value = {"id": 51, "url": "/chatbots/imagenes/51"}
        chatbot_imagenes._extraer_de_pptx(pptx_buf.getvalue(), "manual.pptx", chatbot_id=2, user_id=10)

    llamada = mock_guardar.call_args[1]
    assert "diapositiva 2" in llamada["nombre"]
    assert "diapositiva 2" in llamada["descripcion"]


def test_una_planilla_tambien_entrega_sus_capturas():
    """Las imágenes pegadas en un Excel viven en xl/media/ y antes se perdían."""
    imagen = _crear_imagen_bytes(ancho=200, alto=200, formato="PNG", con_ruido=True)
    xlsx_buf = io.BytesIO()
    with zipfile.ZipFile(xlsx_buf, "w") as zf:
        zf.writestr("xl/workbook.xml", "<workbook/>")
        zf.writestr("xl/media/image1.png", imagen)

    with patch("app.chatbot_imagenes.guardar_imagen") as mock_guardar:
        mock_guardar.return_value = {"id": 52, "url": "/chatbots/imagenes/52"}
        extraidas = chatbot_imagenes._extraer_embebidas(
            xlsx_buf.getvalue(), "cartera.xlsx", chatbot_id=2, user_id=10)

    assert len(extraidas) == 1
    assert mock_guardar.call_count == 1


def test_el_tipo_de_documento_se_decide_por_el_contenido():
    """El navegador manda los Office como octet-stream y la gente renombra .pptx a
    .ppt: si se confía en el nombre, no se extrae ninguna imagen."""
    imagen = _crear_imagen_bytes(ancho=200, alto=200, formato="PNG", con_ruido=True)
    pptx_buf = io.BytesIO()
    with zipfile.ZipFile(pptx_buf, "w") as zf:
        zf.writestr("ppt/slides/slide1.xml", '<p:sld xmlns:p="p"><p:cSld><p:spTree/></p:cSld></p:sld>')
        zf.writestr("ppt/media/image1.png", imagen)

    fuentes = [{"tipo": "archivo", "nombre": "presentacion_sin_extension",
                "datos": pptx_buf.getvalue(), "mime": "application/octet-stream"}]

    with patch("app.chatbot_imagenes.guardar_imagen") as mock_guardar:
        mock_guardar.return_value = {"id": 53, "url": "/chatbots/imagenes/53"}
        extraidas = chatbot_imagenes.extraer_imagenes_de_fuentes(fuentes, chatbot_id=2, user_id=10)

    assert len(extraidas) == 1


# --------------------------------------------------------------------------- #
# 3) Extracción desde fuentes heterogéneas                                     #
# --------------------------------------------------------------------------- #

def test_extraer_imagenes_de_fuentes():
    img_bytes = _crear_imagen_bytes(ancho=200, alto=200, formato="PNG", con_ruido=False)

    pptx_buf = io.BytesIO()
    with zipfile.ZipFile(pptx_buf, "w") as zf:
        img_pptx = _crear_imagen_bytes(ancho=200, alto=200, formato="PNG", con_ruido=True)
        zf.writestr("ppt/media/slide_img.png", img_pptx)
    pptx_bytes = pptx_buf.getvalue()

    fuentes = [
        {"tipo": "texto", "texto": "procedimiento de prueba"},
        {"tipo": "archivo", "nombre": "captura_pantalla.png", "datos": img_bytes, "mime": "image/png"},
        {"tipo": "archivo", "nombre": "presentacion.pptx", "datos": pptx_bytes,
         "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation"},
        {"tipo": "archivo", "nombre": "tabla.csv", "datos": b"a,b\n1,2", "mime": "text/csv"},
    ]

    with patch("app.chatbot_imagenes.guardar_imagen") as mock_guardar:
        mock_guardar.side_effect = [
            {
                "id": 99,
                "chatbot_id": 5,
                "nombre": "captura_pantalla.png",
                "url": "/chatbots/imagenes/99",
                "ancho": 200,
                "alto": 200,
            },
            {
                "id": 100,
                "chatbot_id": 5,
                "nombre": "slide_img.png",
                "url": "/chatbots/imagenes/100",
                "ancho": 200,
                "alto": 200,
            },
        ]

        resultado = chatbot_imagenes.extraer_imagenes_de_fuentes(fuentes, chatbot_id=5, user_id=1)
        assert len(resultado) == 2
        assert resultado[0]["id"] == 99
        assert resultado[1]["id"] == 100
        assert mock_guardar.call_count == 2


# --------------------------------------------------------------------------- #
# 4) CRUD en base de datos (con mock de conexión SQL Server)                   #
# --------------------------------------------------------------------------- #

def test_guardar_imagen_deduplicacion():
    img_bytes = _crear_imagen_bytes(ancho=150, alto=150, formato="PNG", con_ruido=False)

    mock_conn = MagicMock()
    mock_row_dict = {
        "id": 7,
        "chatbot_id": 2,
        "nombre": "existente.png",
        "hash_sha256": "dummy_hash",
        "mime": "image/png",
        "ancho": 150,
        "alto": 150,
        "tamano_bytes": len(img_bytes),
        "descripcion": None,
        "fuente_origen": None,
        "creado_en": None,
    }

    mock_conn.execute.return_value.mappings.return_value.first.return_value = mock_row_dict

    with patch("app.chatbot_imagenes._get_engine") as mock_engine:
        mock_engine.return_value.begin.return_value.__enter__.return_value = mock_conn

        res = chatbot_imagenes.guardar_imagen(
            chatbot_id=2,
            nombre="nueva.png",
            datos=img_bytes,
        )
        assert res["id"] == 7
        assert res["url"] == "/chatbots/imagenes/7"
        # Al existir, no debió hacer INSERT
        assert "INSERT" not in str(mock_conn.execute.call_args[0][0])


def test_obtener_imagen_no_encontrada():
    mock_conn = MagicMock()
    mock_conn.execute.return_value.mappings.return_value.first.return_value = None

    with patch("app.chatbot_imagenes._get_engine") as mock_engine:
        mock_engine.return_value.connect.return_value.__enter__.return_value = mock_conn
        img = chatbot_imagenes.obtener_imagen(999999)
        assert img is None


# --------------------------------------------------------------------------- #
# 5) Endpoints FastAPI                                                         #
# --------------------------------------------------------------------------- #

@pytest.fixture
def client():
    """Cliente con la sesión ya resuelta: servir una imagen pide usuario logueado."""
    app.dependency_overrides[get_current_active_user] = lambda: MagicMock(usuario=1)
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_endpoint_servir_imagen_200_y_304(client):
    img_bytes = _crear_imagen_bytes(ancho=100, alto=100, formato="PNG", con_ruido=False)
    mock_data = {
        "id": 10,
        "chatbot_id": 1,
        "nombre": "prueba.png",
        "datos": img_bytes,
        "hash_sha256": "abc123hash",
        "mime": "image/png",
    }

    with patch("app.chatbot_imagenes.obtener_imagen", return_value=mock_data):
        # 1. Primera petición: 200 OK con headers
        resp = client.get("/chatbots/imagenes/10")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert resp.headers["etag"] == '"abc123hash"'
        assert "max-age=" in resp.headers["cache-control"]
        assert resp.content == img_bytes

        # 2. Segunda petición con ETag: 304 Not Modified
        resp_cached = client.get("/chatbots/imagenes/10", headers={"If-None-Match": '"abc123hash"'})
        assert resp_cached.status_code == 304


def test_endpoint_servir_imagen_404(client):
    with patch("app.chatbot_imagenes.obtener_imagen", return_value=None):
        resp = client.get("/chatbots/imagenes/9999")
        assert resp.status_code == 404


def test_endpoint_servir_imagen_sin_sesion_no_sirve_los_bytes():
    """El id es un entero correlativo: sin login, cualquiera se lleva las capturas
    internas enumerando ids."""
    with patch("app.chatbot_imagenes.obtener_imagen", return_value={"id": 1}):
        assert TestClient(app).get("/chatbots/imagenes/1").status_code == 401


def test_endpoint_servir_imagen_con_nombre_no_ascii(client):
    """El bug del 2026-09-03: un nombre con guion largo (—) reventaba la respuesta
    entera con UnicodeEncodeError, porque los headers HTTP se serializan en latin-1
    y el nombre del archivo subido va en el Content-Disposition."""
    img_bytes = _crear_imagen_bytes(ancho=80, alto=80, formato="PNG", con_ruido=False)
    mock_data = {
        "id": 11,
        "chatbot_id": 1,
        "nombre": "Instructivo — Alta de servicio (diapositiva 3).png",
        "datos": img_bytes,
        "hash_sha256": "hash_unicode",
        "mime": "image/png",
    }

    with patch("app.chatbot_imagenes.obtener_imagen", return_value=mock_data):
        resp = client.get("/chatbots/imagenes/11")

    assert resp.status_code == 200
    assert resp.content == img_bytes
    disposition = resp.headers["content-disposition"]
    disposition.encode("latin-1")                      # es lo que hace el servidor al responder
    assert "Instructivo" in disposition                 # fallback ASCII para clientes viejos
    assert "%E2%80%94" in disposition                   # el nombre real, en filename*=UTF-8


def test_content_disposition_no_rompe_con_comillas_ni_saltos():
    """Un nombre con comillas partía la cabecera en dos; con saltos de línea era
    directamente una inyección de headers."""
    cabecera = chatbot_router._content_disposition('foto "rara"\nX-Malo: 1.png')
    cabecera.encode("latin-1")
    assert "\n" not in cabecera and "\r" not in cabecera
    assert cabecera.count('"') == 2                     # solo las que abren y cierran el filename
