"""Tests offline de los helpers del panel admin de chatbots: validación de
PCRCs, convención de slugs y normalización del material crudo que se sube.

No levantan la API ni tocan la BD (el CRUD real se verifica E2E post-deploy).
"""
import base64

import pytest

from fastapi import HTTPException

from app.chatbot_config import SLUG_REGEX
from app.models import FuenteCrudaIn
from app.routers.chatbot_admin import _normalizar_fuentes, _validar_pcrcs


# ------------------------------------------------------------ _validar_pcrcs

def test_validar_pcrcs_normaliza_y_deduplica():
    assert _validar_pcrcs(["no premium", "  NO   PREMIUM ", "Tokenización", ""]) == [
        "NO PREMIUM",
        "TOKENIZACION",
    ]


# ------------------------------------------------------------------- slugs

@pytest.mark.parametrize("slug,valido", [
    ("voltara", True),
    ("csv_no_premium", True),
    ("bot2", True),
    ("a", False),            # muy corto
    ("Con Mayusculas", False),
    ("con-guiones", False),
    ("con espacios", False),
    ("x" * 51, False),       # muy largo
])
def test_convencion_de_slug(slug, valido):
    assert bool(SLUG_REGEX.match(slug)) is valido


# --------------------------------------------------------------------------- #
# Normalización del material crudo que sube Calidad                            #
# --------------------------------------------------------------------------- #

def _fuente_archivo(nombre, datos, mime=None):
    return FuenteCrudaIn(tipo="archivo", nombre=nombre, mime=mime,
                         datos_base64=base64.b64encode(datos).decode())


def test_el_tipo_de_un_office_sin_mime_se_deduce_del_nombre():
    """Windows manda los .pptx/.docx sin tipo o como octet-stream según cómo esté
    registrada la extensión; sin esto el archivo se rechazaba por 'vino sin tipo'."""
    fuentes = _normalizar_fuentes([
        _fuente_archivo("instructivo.pptx", b"PK\x03\x04datos", mime=""),
        _fuente_archivo("manual.docx", b"PK\x03\x04datos", mime="application/octet-stream"),
    ])
    assert fuentes[0]["mime"] == "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    assert fuentes[1]["mime"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def test_el_office_viejo_se_rechaza_al_subirlo():
    """No lo lee ni la conversión propia ni Gemini. El 422 sale acá, con el usuario
    mirando la pantalla, en vez de un trabajo que falla dos minutos después."""
    with pytest.raises(HTTPException) as e:
        _normalizar_fuentes([_fuente_archivo(
            "presentacion.ppt", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1contenido")])
    assert e.value.status_code == 422
    assert "formato viejo de Office" in e.value.detail


def test_si_el_bot_ya_tiene_un_trabajo_abierto_responde_409_con_su_id(monkeypatch):
    """El panel usa el job_id del 409 para mostrar el avance de ese trabajo en vez de
    encolar otro."""
    from app.routers import chatbot_admin

    def encolar(**kwargs):
        raise chatbot_admin.doc_jobs.TrabajoEnCurso(183)

    monkeypatch.setattr(chatbot_admin.doc_jobs, "encolar", encolar)

    with pytest.raises(HTTPException) as e:
        chatbot_admin._encolar_doc_job(tipo="formatear", chatbot_id=16, payload={}, requested_by=1)

    assert e.value.status_code == 409
    assert e.value.detail["job_id"] == 183
    assert "trabajo de la IA en curso" in e.value.detail["mensaje"]
