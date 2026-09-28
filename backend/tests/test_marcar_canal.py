"""Marcado de canal en la documentación (scripts/marcar_canal_docs.py).

Lo crítico no es que etiquete bien —eso es criterio del modelo y se valida
leyendo— sino que NO PIERDA NI CAMBIE CONTENIDO: lo único que puede hacer es
insertar una línea debajo de un encabezado. La documentación es la fuente de
verdad del RAG; una pérdida silenciosa acá se propaga a todas las respuestas.
"""
import importlib.util
import os

import pytest

RUTA = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts", "marcar_canal_docs.py"
)


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("marcar_canal_docs", RUTA)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)  # type: ignore[union-attr]
    return m


DOC = """# Trámites Contractuales

Este documento detalla la normativa.

## Alta de Suministro

### Definición
El Alta de Suministro habilita el servicio por primera vez.
Corresponde cuando el inmueble nunca tuvo suministro activo.

### Medios de Atención
* **Oficina Virtual (OV):** Canal prioritario.
* **Email servicio:** Solo si no puede adjuntar.

## Baja de Tasa AP
Se gestiona exclusivamente por Canal Digital.
"""


def test_partir_conserva_todo_el_texto(mod):
    partes = mod.partir_secciones(DOC)
    rearmado = "\n".join(
        (f"{enc}\n{cuerpo}" if enc else cuerpo) for enc, cuerpo in partes
    )
    assert rearmado == DOC


def test_el_preambulo_no_se_marca(mod):
    """El título del documento y su intro van antes del primer encabezado."""
    partes = mod.partir_secciones(DOC)
    assert partes[0][0] == ""
    assert partes[0][1].startswith("# Trámites Contractuales")


def test_verificacion_acepta_solo_lineas_insertadas(mod):
    marcado = DOC.replace(
        "## Baja de Tasa AP\n", "## Baja de Tasa AP\n**Canal:** digital\n"
    )
    assert mod.verificar_sin_perdida(DOC, marcado)


def test_verificacion_detecta_una_palabra_cambiada(mod):
    manipulado = DOC.replace("Canal prioritario", "Canal secundario")
    assert not mod.verificar_sin_perdida(DOC, manipulado)


def test_verificacion_detecta_una_linea_borrada(mod):
    manipulado = DOC.replace("* **Email servicio:** Solo si no puede adjuntar.\n", "")
    assert not mod.verificar_sin_perdida(DOC, manipulado)


def test_marcar_no_toca_el_contenido(mod, monkeypatch):
    """El caso completo: con el clasificador mockeado, el markdown resultante menos
    las marcas tiene que ser idéntico al de entrada."""
    monkeypatch.setattr(mod, "MIN_CHARS_SECCION", 0)
    monkeypatch.setattr(mod, "clasificar_lote",
                        lambda secs: {n: "digital" for n, _, _ in secs})

    marcado, conteo = mod.marcar_documento(DOC)

    assert mod.verificar_sin_perdida(DOC, marcado)
    assert "**Canal:** digital" in marcado
    assert conteo["digital"] > 0


def test_ambos_no_lleva_marca(mod, monkeypatch):
    """La ausencia de marca significa "sirve a los dos canales": marcarlo sería
    inflar cada chunk con una línea que no aporta."""
    monkeypatch.setattr(mod, "MIN_CHARS_SECCION", 0)
    monkeypatch.setattr(mod, "clasificar_lote",
                        lambda secs: {n: "ambos" for n, _, _ in secs})

    marcado, conteo = mod.marcar_documento(DOC)

    assert mod.MARCA not in marcado
    assert marcado == DOC
    assert conteo["ambos"] > 0


def test_si_falla_la_ia_el_documento_queda_igual(mod, monkeypatch):
    """Un error del modelo no puede dejar el documento a medio marcar ni romperlo."""
    monkeypatch.setattr(mod, "clasificar_lote", lambda secs: {})

    marcado, conteo = mod.marcar_documento(DOC)

    assert marcado == DOC
    assert conteo["telefonico"] == 0 and conteo["digital"] == 0


def test_las_secciones_muy_cortas_no_se_marcan(mod, monkeypatch):
    """Encabezados sueltos y listas de dos líneas: marcarlas es ruido."""
    llamadas = []
    monkeypatch.setattr(mod, "clasificar_lote",
                        lambda secs: llamadas.append([e for _, e, _ in secs]) or {})

    mod.marcar_documento(DOC)

    enviadas = [e for lote in llamadas for e in lote]
    assert "### Definición" not in enviadas  # 108 chars, por debajo del mínimo
