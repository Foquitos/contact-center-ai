"""Tutorial guiado (el recorrido que resalta controles sobre la pantalla real).

Cero tokens: es todo estático.

Qué se cuida acá, que es lo único que se puede romper en silencio: los pasos apuntan
a los controles por selector CSS (`#batchButton`, `.presets-bar`). Si alguien renombra
un id en el template, el tutorial no falla ni avisa —el motor descarta el paso y sigue
de largo—, así que la explicación de ese control simplemente desaparece. Este test
compara los selectores contra los templates de verdad.

También se chequea que las dos puntas estén: cada pantalla declarada en
`utils/tutorial_config.py` tiene sus pasos registrados en `tutorial_pasos.js` y al revés.
"""
import os
import re

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAIZ = os.path.dirname(BACKEND_DIR)
FRONT = os.path.join(RAIZ, "frontend", "app")
PASOS_JS = os.path.join(FRONT, "static", "js", "tutorial_pasos.js")
MOTOR_JS = os.path.join(FRONT, "static", "js", "tutorial.js")
TEMPLATES = os.path.join(FRONT, "templates")

# Qué template dibuja cada tutorial. Es el mismo mapeo que hace
# utils/tutorial_config.py con endpoints; acá se necesita el archivo.
TEMPLATE_DE = {
    "auditar": "Auditoria.html",
    "realizadas": "auditorias_realizadas.html",
    "dashboard": "bandeja.html",
    "plantillas": "plantillas.html",
}


def _texto(ruta):
    with open(ruta, encoding="utf-8") as f:
        return f.read()


def _tutoriales_registrados():
    """{id: bloque de texto de sus pasos} leyendo el JS como texto.

    No se ejecuta el JS (no hay Node en la suite): alcanza con el texto, porque lo
    que se quiere verificar son literales."""
    js = _texto(PASOS_JS)
    partes = re.split(r"T\.registrar\('([a-z_]+)',", js)
    # partes = [preámbulo, id1, cuerpo1, id2, cuerpo2, ...]
    return {partes[i]: partes[i + 1] for i in range(1, len(partes), 2)}


def _selectores(cuerpo):
    """Los selectores que el tutorial va a buscar en la pantalla.

    Incluye los que están adentro de los ayudantes (`conValor('#x')`) y los de
    `bloquear`: un botón que se quiso bloquear y ya no existe con ese id es tan
    silencioso como un paso que apunta a la nada, pero peor —el botón queda vivo."""
    return (re.findall(r"\bel:\s*'([^']+)'", cuerpo)
            + re.findall(r"\btrepar:\s*'([^']+)'", cuerpo)
            + re.findall(r"\bavanzarAlVer:\s*'([^']+)'", cuerpo)
            + re.findall(r"\bconValor\('([^']+)'\)", cuerpo)
            + re.findall(r"\bseVe\('([^']+)'\)", cuerpo)
            + re.findall(r"getElementById\('([^']+)'\)", cuerpo)
            + _bloqueados(cuerpo))


def _bloqueados(cuerpo):
    """Los botones que el tutorial anula mientras corre."""
    bloque = re.search(r"bloquear:\s*\[(.*?)\]", cuerpo, re.S)
    if not bloque:
        return []
    return re.findall(r"'([^']+)'", bloque.group(1))


# Controles que dibuja el JavaScript de la pantalla, no el template: no se pueden
# buscar en el HTML. Se listan a mano para que la lista sea corta y visible.
GENERADOS_POR_JS = {
    "#plantillas-list .list-group-item",   # las plantillas de la campaña elegida
}


def _cargar_tutorial_config():
    """Carga frontend/app/utils/tutorial_config.py por ruta.

    No se puede importar como `app.utils.tutorial_config`: en la suite `app` ya es el
    paquete del backend, que tiene su propio `utils` (mismo caso que
    test_asistente_manual.py con manual_texto.py). El módulo solo importa flask, así
    que cargarlo suelto es equivalente."""
    import importlib.util
    import sys
    ruta = os.path.join(FRONT, "utils", "tutorial_config.py")
    spec = importlib.util.spec_from_file_location("tutorial_config_frontend", ruta)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def test_las_dos_puntas_coinciden():
    """Cada pantalla con tutorial declarado tiene pasos, y cada paso tiene pantalla."""
    TUTORIALES = _cargar_tutorial_config().TUTORIALES

    declarados = {t["id"] for t in TUTORIALES}
    registrados = set(_tutoriales_registrados())
    assert declarados == registrados, (
        f"declarados en tutorial_config.py: {sorted(declarados)}; "
        f"con pasos en tutorial_pasos.js: {sorted(registrados)}")
    assert declarados == set(TEMPLATE_DE), "actualizá TEMPLATE_DE en este test"


@pytest.mark.parametrize("tutorial", sorted(TEMPLATE_DE))
def test_los_pasos_apuntan_a_controles_que_existen(tutorial):
    cuerpo = _tutoriales_registrados()[tutorial]
    html = _texto(os.path.join(TEMPLATES, TEMPLATE_DE[tutorial]))

    selectores = _selectores(cuerpo)
    assert selectores, f"el tutorial '{tutorial}' no resalta ningún control"

    for selector in selectores:
        if selector in GENERADOS_POR_JS:
            continue
        # Se valida el PRIMER token del selector: es el que ancla la búsqueda.
        # ('#panel-editor .alert-secondary' -> '#panel-editor')
        token = selector.split()[0]
        if not token.startswith(("#", ".")):
            token = "#" + token          # los de getElementById vienen sin el '#'
        if token.startswith("#"):
            aguja = f'id="{token[1:]}"'
        else:
            aguja = token.lstrip(".")
        assert aguja in html, (
            f"el tutorial '{tutorial}' apunta a '{selector}', que no está en "
            f"{TEMPLATE_DE[tutorial]} (¿se renombró el control?)")


def test_los_botones_que_lanzan_auditorias_estan_bloqueados():
    """El tutorial de Auditar tiene que anular los dos botones que auditan.

    Es lo único que separa "seguir el tutorial" de "lanzar una auditoría real que
    gasta tokens y cupo": la pantalla, a propósito, queda usable."""
    cuerpo = _tutoriales_registrados()["auditar"]
    bloqueados = set(_bloqueados(cuerpo))
    assert {"#batchButton", "#submitButton"} <= bloqueados, bloqueados
    # Y el Enter, que manda el formulario sin pasar por ningún botón.
    assert "bloquearSubmit: ['#auditForm']" in cuerpo


def test_ningun_paso_interactivo_apunta_a_un_boton_bloqueado():
    """Pedirle a alguien que toque un botón que el propio tutorial anuló."""
    for tutorial, cuerpo in _tutoriales_registrados().items():
        bloqueados = set(_bloqueados(cuerpo))
        for bloque in re.findall(r"\{[^{}]*interactivo:\s*true[^{}]*\}", cuerpo):
            for sel in re.findall(r"\bel:\s*'([^']+)'", bloque):
                assert sel not in bloqueados, (
                    f"el tutorial '{tutorial}' pide tocar {sel}, que él mismo bloquea")


def test_el_motor_anula_clic_y_submit_en_captura():
    """El candado tiene que correr ANTES que el handler de la pantalla.

    Si se registra sin `true` (fase de captura), auditoria.js ya procesó el submit y
    el bloqueo llega tarde: la auditoría sale igual."""
    motor = _texto(MOTOR_JS)
    assert "document.addEventListener('click', st.candadoClic, true);" in motor
    assert "document.addEventListener('submit', st.candadoSubmit, true);" in motor


def test_el_globo_nunca_se_pone_encima_del_control():
    """Un globo que tapa el control deja el paso interactivo imposible de hacer.

    Pasó con "Buscar Auditorías", pegado al borde izquierdo y abajo de todo: los
    cuatro lados daban "no entra" y el último se aceptaba a la fuerza, justo encima
    del botón que el tutorial pedía tocar."""
    motor = _texto(MOTOR_JS)
    # Un lado solo se acepta si entra Y no pisa al control...
    assert "if (c.entra && !pisa(c)) { elegido = c; break; }" in motor
    # ...y si no hay ninguno, el respaldo se vuelve a revisar antes de usarlo.
    assert "if (pisa(elegido)) {" in motor


def test_realizadas_amplia_el_periodo_antes_de_pedir_que_busque():
    """Las fechas vienen en HOY: buscando así lo normal es que no haya nada, y el
    tutorial se queda sin tabla, sin embudos y sin descargas justo cuando las va a
    explicar. El período se corre para atrás ANTES del paso de Buscar."""
    cuerpo = _tutoriales_registrados()["realizadas"]
    assert "ampliarPeriodo('#fecha_desde'" in cuerpo
    assert cuerpo.index("ampliarPeriodo('#fecha_desde'") < cuerpo.index("el: '#searchButton'")


def test_el_resaltado_respeta_los_contenedores_con_scroll_propio():
    """La tabla de auditorías tiene su PROPIO scroll (#results-table-container:
    max-height 600px, overflow-y auto). Una fila scrolleada fuera de esa caja sigue
    teniendo posición y tamaño —muy lejos, arriba o abajo del contenedor— y
    `getBoundingClientRect()` la devuelve igual. Sin recortar contra los ancestros que
    recortan, el resaltado se dibuja donde no hay nada y el globo, que se ubica
    respecto de ese rectángulo, se va de la pantalla al mover ese scrollbar."""
    motor = _texto(MOTOR_JS)
    assert "function ventanaVisible(el)" in motor
    assert "/(auto|scroll|hidden)/.test(cs.overflowY" in motor
    # Y el recorte se usa de verdad, con el elemento, no solo contra la ventana.
    assert "recorte(st.elActual.getBoundingClientRect(), st.elActual)" in motor


def test_el_globo_siempre_queda_dentro_de_la_pantalla():
    """Cada posición candidata se acota a la ventana antes de elegirla.

    `entra` dice si hizo falta acotar; acotar SIEMPRE es lo que garantiza que el
    globo no termine fuera de la vista con un control gigante (la tabla entera) o
    pegado a un borde."""
    motor = _texto(MOTOR_JS)
    cuerpo = motor.split("function candidato(lado)", 1)[1].split("return { lado:", 1)[0]
    assert cuerpo.count("top = acotar(top, maxTop);") == 2
    assert cuerpo.count("left = acotar(left, maxLeft);") == 2
    assert cuerpo.count("acotar(r.left + r.width / 2 - gw / 2, maxLeft)") == 2
    assert cuerpo.count("acotar(r.top + r.height / 2 - gh / 2, maxTop)") == 2


def test_la_fila_de_auditoria_no_resalta_la_tabla_entera():
    """El paso del audio/transcripción marca UNA fila, no el <tbody> completo: con
    100 filas el resaltado sería más alto que la pantalla y no señalaría nada."""
    cuerpo = _tutoriales_registrados()["realizadas"]
    bloque = cuerpo.split("Escuchar el llamado", 1)[0]
    paso = bloque[bloque.rindex("{"):]
    assert "el: '#results-table-body'" in paso
    assert "bajar: 'tr'" in paso


def test_la_pantalla_queda_usable():
    """Los paneles oscuros no pueden interceptar clics.

    Es lo que permite que la persona vaya eligiendo empresa/campaña/plantilla y que
    aparezcan los controles que solo existen con algo elegido. Si alguien vuelve a
    poner `pointer-events: auto`, el tutorial sigue "funcionando" pero se queda otra
    vez explicando una pantalla vacía."""
    css = _texto(os.path.join(FRONT, "static", "css", "tutorial.css"))
    bloque = css.split(".tour-panel {", 1)[1].split("}", 1)[0]
    assert "pointer-events: none" in bloque
