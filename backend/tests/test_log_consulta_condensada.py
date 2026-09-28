"""Consulta reescrita y título del documento en el log de solicitudes.

Dos datos que faltaban para poder revisar una interacción y entenderla:

- La consulta REESCRITA. El bot usa CondensePlusContextChatEngine: busca en el
  índice con la pregunta condensada junto al historial, no con lo que se tipeó.
  En una repregunta ("tiene costo adicional?") las dos no se parecen en nada, y
  sin la reescrita no hay forma de saber contra qué se buscó.
- El TÍTULO del documento. En pantalla se leía "04_dbdoc_19.md", el nombre que
  materializa el indexador, que no le sirve a quien tiene que ir a buscarlo.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.chatbot_config import doc_id_de_archivo, titulos_por_archivo


def _respuesta(mensaje_condensado):
    """Doble de la respuesta del chat engine: la reescrita viaja en
    sources[0].raw_input['message'] (ver condense_plus_context._arun_c3)."""
    return SimpleNamespace(
        sources=[SimpleNamespace(raw_input={"message": mensaje_condensado})]
    )


# ------------------------------------------------------ consulta reescrita

def test_se_extrae_la_consulta_reescrita():
    from chatBot import consulta_condensada

    resp = _respuesta("¿Qué documentación se necesita para el cambio de tarifa?")

    assert consulta_condensada(resp, "que documentacion necesita?") == \
        "¿Qué documentación se necesita para el cambio de tarifa?"


def test_no_se_guarda_si_no_hubo_reescritura():
    """En el primer mensaje no hay historial que condensar y el motor devuelve la
    consulta tal cual: guardarla sería repetir la columna de al lado."""
    from chatBot import consulta_condensada

    assert consulta_condensada(_respuesta("cambio de titularidad"), "cambio de titularidad") is None


def test_los_espacios_no_cuentan_como_reescritura():
    from chatBot import consulta_condensada

    assert consulta_condensada(_respuesta("  cambio de medidor  "), "cambio de medidor") is None


def test_sin_fuentes_no_rompe():
    from chatBot import consulta_condensada

    assert consulta_condensada(SimpleNamespace(sources=[]), "x") is None
    assert consulta_condensada(SimpleNamespace(sources=None), "x") is None
    assert consulta_condensada(SimpleNamespace(), "x") is None


def test_un_raw_input_raro_no_rompe():
    """Si una versión de llama_index cambia la forma del ToolOutput, se pierde el
    dato pero NO se cae el logueo de la consulta."""
    from chatBot import consulta_condensada

    assert consulta_condensada(
        SimpleNamespace(sources=[SimpleNamespace(raw_input="no es un dict")]), "x") is None
    assert consulta_condensada(
        SimpleNamespace(sources=[SimpleNamespace(raw_input={})]), "x") is None
    assert consulta_condensada(
        SimpleNamespace(sources=[SimpleNamespace()]), "x") is None


def test_se_recorta_al_largo_de_la_columna():
    """La columna es NVARCHAR(2000); pasarse tumbaría el INSERT del log entero."""
    from chatBot import consulta_condensada

    largo = consulta_condensada(_respuesta("a" * 5000), "corta")

    assert len(largo) == 2000


# --------------------------------------------- nombre interno -> título real

def test_se_reconoce_el_id_del_documento():
    assert doc_id_de_archivo("04_dbdoc_19.md") == 19
    assert doc_id_de_archivo("/ruta/completa/01_dbdoc_16.md") == 16


def test_un_archivo_de_otro_origen_no_tiene_id():
    """Los documentos que vienen de Drive no siguen la convención del indexador."""
    assert doc_id_de_archivo("manual_drive.md") is None
    assert doc_id_de_archivo(None) is None
    assert doc_id_de_archivo("") is None


def test_los_titulos_se_resuelven_en_una_sola_consulta():
    """El detalle puede traer varias fuentes: una consulta por cada una sería ir a
    la base tres o cuatro veces para dibujar un modal."""
    conn = MagicMock()
    conn.execute.return_value.mappings.return_value.all.return_value = [
        {"id": 19, "titulo": "Facturación, Medios de Pago y Cobranzas"},
        {"id": 16, "titulo": "Trámites Contractuales"},
    ]

    titulos = titulos_por_archivo(conn, ["04_dbdoc_19.md", "01_dbdoc_16.md", "04_dbdoc_19.md"])

    assert titulos == {
        "04_dbdoc_19.md": "Facturación, Medios de Pago y Cobranzas",
        "01_dbdoc_16.md": "Trámites Contractuales",
    }
    assert conn.execute.call_count == 1


def test_un_documento_borrado_no_aparece_en_el_mapa():
    """El que llama deja el nombre de archivo como estaba: es mejor que nada."""
    conn = MagicMock()
    conn.execute.return_value.mappings.return_value.all.return_value = []

    assert titulos_por_archivo(conn, ["04_dbdoc_19.md"]) == {}


def test_sin_nombres_no_se_consulta_la_base():
    conn = MagicMock()

    assert titulos_por_archivo(conn, []) == {}
    assert titulos_por_archivo(conn, None) == {}
    assert titulos_por_archivo(conn, ["manual_drive.md"]) == {}
    assert not conn.execute.called
