"""Asistente del manual (la burbuja de ayuda de todas las pantallas). Cero tokens
salvo el test marcado `tokens` del final.

Qué se cuida acá:

1. La CONVERSIÓN del manual (frontend/app/utils/manual_texto.py). Es la pieza que
   puede romperse en silencio: si alguien reestructura documentacion.html y las
   secciones dejan de ser `<section class="doc-section" id="...">`, el asistente
   seguiría respondiendo... con un manual vacío ("eso no está en tu manual" para
   todo). Por eso hay además un test que mira el template real.
2. Los TOPES de entrada y el recorte del historial, que es lo único que llega del
   navegador.
3. Que el manual y la pantalla efectivamente entren en la instrucción de sistema:
   sin eso el modelo contestaría de memoria, que es exactamente lo que no se quiere.
"""
import importlib.util
import os
import sys
import types

import pytest

from app import asistente_manual

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAIZ = os.path.dirname(BACKEND_DIR)
TEMPLATE_MANUAL = os.path.join(RAIZ, "frontend", "app", "templates", "documentacion.html")


def _cargar_manual_texto():
    """Carga frontend/app/utils/manual_texto.py por ruta.

    No se puede importar como `app.utils.manual_texto`: en la suite, `app` ya es el
    paquete del backend (que tiene su propio `utils`). El módulo no importa nada del
    frontend, así que cargarlo suelto es equivalente a importarlo."""
    ruta = os.path.join(RAIZ, "frontend", "app", "utils", "manual_texto.py")
    spec = importlib.util.spec_from_file_location("manual_texto_frontend", ruta)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


manual_texto = _cargar_manual_texto()


# --------------------------------------------------------------------------- #
# Conversión del manual renderizado a texto                                    #
# --------------------------------------------------------------------------- #

HTML_MINIMO = """
<nav id="doc-toc"><a href="#plantillas">Plantillas</a></nav>
<main>
  <section class="doc-section" id="plantillas">
    <h2><i class="bi bi-list"></i> Plantillas</h2>
    <p>La plantilla es el <strong>formulario</strong> de evaluación.</p>
    <h3>Cómo crear una</h3>
    <ol>
      <li>Entrá a <strong>Plantillas</strong>.
        <ul><li>Elegí la empresa</li></ul>
      </li>
      <li>Tocá <strong>Guardar Cambios</strong>.</li>
    </ol>
    <table>
      <thead><tr><th>Opción</th><th>Qué hace</th></tr></thead>
      <tbody><tr><td>Calidad</td><td>Evalúa con la plantilla</td></tr></tbody>
    </table>
    <dl><dt>Atributo</dt><dd>Cada punto que evalúa la plantilla.</dd></dl>
  </section>
</main>
<script>const x = "esto no es contenido";</script>
"""


@pytest.fixture(scope="module")
def texto_minimo():
    return manual_texto.html_a_texto(HTML_MINIMO)


def test_conserva_el_ancla_de_cada_seccion(texto_minimo):
    """El ancla es lo que permite que la respuesta linkee al manual; si se pierde,
    el asistente responde bien pero sin dónde ampliar."""
    assert "## Plantillas (link: /documentacion#plantillas)" in texto_minimo


def test_respeta_la_jerarquia_de_titulos(texto_minimo):
    # h2 de la sección -> '##', h3 -> '###': el modelo usa la jerarquía para saber
    # qué es subtema de qué.
    assert "### Cómo crear una" in texto_minimo


def test_aplana_listas_anidadas_con_sangria(texto_minimo):
    assert "1. Entrá a Plantillas." in texto_minimo
    assert "  - Elegí la empresa" in texto_minimo
    assert "2. Tocá Guardar Cambios." in texto_minimo


def test_tabla_a_markdown(texto_minimo):
    assert "| Opción | Qué hace |" in texto_minimo
    assert "| Calidad | Evalúa con la plantilla |" in texto_minimo


def test_definiciones_a_pares(texto_minimo):
    assert "- **Atributo**: Cada punto que evalúa la plantilla." in texto_minimo


def test_descarta_navegacion_y_scripts(texto_minimo):
    """El índice lateral y el buscador son controles: si entraran al prompt, el
    modelo los explicaría como si fueran contenido del manual."""
    assert "esto no es contenido" not in texto_minimo
    assert texto_minimo.count("Plantillas (link:") == 1


def test_puntuacion_pegada_al_texto():
    """Separar los inline con espacio deja 'Guardar Cambios ,'. Es cosmético para el
    modelo, pero el texto también se lee en logs y sale caro de depurar después."""
    salida = manual_texto.html_a_texto(
        '<section class="doc-section" id="x"><h2>X</h2>'
        '<p>Tocá <strong>Guardar</strong>, y listo.</p></section>'
    )
    assert "Tocá Guardar, y listo." in salida


def test_solo_toma_secciones_del_manual():
    """Cualquier otra cosa del layout (navbar, footer, banners) queda afuera."""
    salida = manual_texto.html_a_texto(
        "<div class='navbar'>Cerrar sesión</div>"
        "<section class='doc-section' id='a'><h2>A</h2><p>Contenido.</p></section>"
    )
    assert "Cerrar sesión" not in salida
    assert "Contenido." in salida


def test_el_template_real_sigue_teniendo_secciones_con_ancla():
    """Guardia sobre documentacion.html: la conversión depende de que cada capítulo
    sea `<section class="doc-section" id="...">`. Si se reestructura el manual, este
    test avisa antes de que el asistente empiece a responder sobre un manual vacío."""
    with open(TEMPLATE_MANUAL, encoding="utf-8") as fh:
        html = fh.read()
    secciones = html.count('<section class="doc-section')
    assert secciones >= 10, "el manual perdió secciones o cambió de estructura"
    # Toda sección abierta declara su id en la misma línea (es el ancla del link).
    for linea in html.splitlines():
        if '<section class="doc-section' in linea:
            assert ' id="' in linea, f"sección sin ancla: {linea.strip()}"


# --------------------------------------------------------------------------- #
# Validación de entrada                                                        #
# --------------------------------------------------------------------------- #

def test_validar_normaliza_y_exige_pregunta():
    pregunta, manual = asistente_manual.validar("  ¿Cómo audito?  ", " # Manual ")
    assert pregunta == "¿Cómo audito?"
    assert manual == "# Manual"

    with pytest.raises(ValueError):
        asistente_manual.validar("   ", "# Manual")


def test_validar_rechaza_pregunta_gigante():
    """Sin tope, una pregunta de 200k caracteres se paga como entrada."""
    with pytest.raises(ValueError):
        asistente_manual.validar("a" * (asistente_manual.MAX_PREGUNTA_CHARS + 1), "# Manual")


def test_validar_rechaza_manual_vacio():
    """Si el frontend no pudo armar el manual, es preferible el error a que el modelo
    conteste de memoria sobre un sistema que no conoce."""
    with pytest.raises(ValueError):
        asistente_manual.validar("¿Cómo audito?", "")


# --------------------------------------------------------------------------- #
# Prompt e historial                                                           #
# --------------------------------------------------------------------------- #

def test_la_instruccion_lleva_el_manual_y_la_pantalla():
    instruccion = asistente_manual._instruccion_de_sistema("## Plantillas\nTexto.", "Plantillas (/plantillas)")
    assert "## Plantillas" in instruccion
    assert "Plantillas (/plantillas)" in instruccion
    assert "MANUAL DE USO" in instruccion


def test_sin_pantalla_no_se_inventa_contexto():
    instruccion = asistente_manual._instruccion_de_sistema("# M", None)
    assert "LA PERSONA ESTÁ AHORA EN LA PANTALLA" not in instruccion


def test_historial_se_recorta_y_mapea_roles():
    """El historial lo manda el navegador: se recorta a los últimos turnos y todo lo
    que no sea 'user' se trata como respuesta del modelo."""
    historial = [{"rol": "user" if i % 2 == 0 else "bot", "texto": f"t{i}"} for i in range(20)]
    contenidos = asistente_manual._contenidos("nueva", historial)

    assert len(contenidos) == asistente_manual.MAX_TURNOS_HISTORIAL + 1
    assert contenidos[-1].role == "user"
    assert contenidos[-1].parts[0].text == "nueva"
    assert {c.role for c in contenidos} == {"user", "model"}


def test_historial_descarta_turnos_vacios():
    contenidos = asistente_manual._contenidos("hola", [{"rol": "bot", "texto": "   "}])
    assert len(contenidos) == 1


# --------------------------------------------------------------------------- #
# Streaming                                                                    #
# --------------------------------------------------------------------------- #

class _ChunkFalso:
    def __init__(self, text=None, usage=None):
        self.text = text
        self.usage_metadata = usage


def _cliente_falso(chunks, explota=False):
    """Cliente Gemini de mentira con la misma forma que el real: el método async es
    una corrutina que devuelve un iterador asíncrono."""
    async def _stream(**kwargs):
        async def _gen():
            for chunk in chunks:
                yield chunk
            if explota:
                raise RuntimeError("se cayó el servicio")
        return _gen()

    return types.SimpleNamespace(aio=types.SimpleNamespace(models=types.SimpleNamespace(
        generate_content_stream=_stream)))


async def _juntar(generador):
    return "".join([texto async for texto in generador])


def test_stream_devuelve_los_pedazos_y_registra_el_consumo(monkeypatch):
    import asyncio

    usage = object()
    monkeypatch.setattr(asistente_manual, "_get_client",
                        lambda: _cliente_falso([_ChunkFalso("Entrá a "),
                                                _ChunkFalso("Plantillas.", usage)]))
    registrados = []
    monkeypatch.setattr(asistente_manual, "_registrar_consumo",
                        lambda u, uid, pantalla: registrados.append((u, uid, pantalla)))

    salida = asyncio.run(_juntar(asistente_manual.responder_stream(
        "¿Cómo creo una plantilla?", "# Manual", user_id=42, pantalla="Plantillas")))

    assert salida == "Entrá a Plantillas."
    assert registrados == [(usage, 42, "Plantillas")]


def test_un_fallo_a_mitad_de_stream_se_explica_en_el_texto(monkeypatch):
    """Cuando ya empezó a salir la respuesta no se puede devolver un 5xx: el motivo
    tiene que viajar como texto o el usuario ve la respuesta cortada sin explicación."""
    import asyncio

    monkeypatch.setattr(asistente_manual, "_get_client",
                        lambda: _cliente_falso([_ChunkFalso("Empiezo")], explota=True))
    monkeypatch.setattr(asistente_manual, "_registrar_consumo", lambda *a, **k: None)

    salida = asyncio.run(_juntar(asistente_manual.responder_stream("¿Y?", "# Manual")))

    assert salida.startswith("Empiezo")
    assert "No pude terminar de responder" in salida


# --------------------------------------------------------------------------- #
# En vivo (gasta tokens): que el contrato con el SDK de Gemini siga siendo real #
# --------------------------------------------------------------------------- #

@pytest.mark.tokens
def test_responde_solo_con_el_manual_en_vivo():
    """Dos cosas que los mocks no pueden probar: que la llamada real al SDK sea la
    correcta, y que el modelo se ate al manual en vez de improvisar."""
    import asyncio

    manual = (
        "## Plantillas (link: /documentacion#plantillas)\n"
        "Para crear una plantilla entrá a Plantillas y tocá Nueva Plantilla.\n"
    )

    respuesta = asyncio.run(_juntar(asistente_manual.responder_stream(
        "¿Cómo creo una plantilla?", manual, pantalla="Inicio (/)")))
    assert "Nueva Plantilla" in respuesta
    assert "/documentacion#plantillas" in respuesta

    # Algo que el manual NO dice: la respuesta correcta es admitirlo, no inventarlo.
    negativa = asyncio.run(_juntar(asistente_manual.responder_stream(
        "¿Cómo exporto las auditorías a Excel?", manual)))
    assert "manual" in negativa.lower()


# --------------------------------------------------------------------------- #
# Capítulo del gestor de chatbots                                              #
# --------------------------------------------------------------------------- #
# El manual es la única explicación que tiene Calidad de las tablas de datos, y
# además es lo que lee el asistente de ayuda: si el capítulo se cae o se muestra
# a quien no puede abrir la pantalla, no hay error visible en ningún lado.

def _renderizar_manual(acceso: dict) -> str:
    """Renderiza SOLO el bloque de contenido de documentacion.html.

    Sin Flask: se recorta el bloque, se neutralizan los `url_for` y se pasa un
    `acceso` falso. Alcanza para verificar el gateo, que es lo que importa."""
    import re

    from jinja2 import DictLoader, Environment

    with open(TEMPLATE_MANUAL, encoding="utf-8") as fh:
        html = fh.read()
    ini = html.index("{% block content %}") + len("{% block content %}")
    cuerpo = html[ini:html.index("{% block scripts %}")]
    cuerpo = cuerpo[:cuerpo.rindex("{% endblock %}")]
    cuerpo = re.sub(r"\{\{\s*url_for\([^}]*\)\s*\}\}", "#", cuerpo)

    class _Acceso(dict):
        def __getattr__(self, clave):
            return self.get(clave, False)

    salida = Environment(loader=DictLoader({"m": cuerpo})).get_template("m").render(
        acceso=_Acceso(acceso), chatbots=2)
    return " ".join(salida.split())


def test_el_manual_explica_el_gestor_de_chatbots():
    manual = _renderizar_manual({"gestor_chatbots": True})
    assert 'id="gestor-chatbots"' in manual
    # Los dos tipos de conocimiento y la diferencia entre ellos: es la decisión que
    # más veces se toma mal al cargar material.
    for concepto in ("Tablas de datos", "Documentos", "valor exacto"):
        assert concepto in manual, f"falta explicar «{concepto}»"


def test_el_manual_explica_como_mantener_una_tabla():
    manual = _renderizar_manual({"gestor_chatbots": True})
    for concepto in (
        "Novedades",                      # carga incremental
        "La tabla completa",              # carga con bajas
        "Convertir en una tabla de datos",
        "Regla de uso",
        "Palabras que llevan a esta tabla",
    ):
        assert concepto in manual, f"falta explicar «{concepto}»"


def test_el_capitulo_de_chatbots_esta_gateado():
    """Quien no puede abrir el gestor no lo lee: el manual muestra solo las
    pantallas que la persona puede usar."""
    manual = _renderizar_manual({"gestor_chatbots": False, "chatbot": True})
    assert 'id="gestor-chatbots"' not in manual
    assert "Gestor de Chatbots" not in manual
