"""Tests offline de la reestructuración de plantillas de cartas para RAG.

Lo que se prueba es la maquinaria DETERMINÍSTICA (partir el documento, detectar el
boilerplate repetido, extraer apertura/cierre y verificar que no se pierda contenido),
que es justamente la garantía del proceso: la IA solo hace el trabajo semántico y
después esta capa comprueba que no haya desaparecido nada.
"""
from unittest.mock import MagicMock

import pytest

from AuditorIA import asistente_cartas as ac


APERTURA = "Hola **nombre del cliente**,\n\nNos comunicamos por la solicitud que realizaste."
CIERRE = (
    "Por cualquier duda comunicate con nosotros:\n\n"
    "• WHATSAPP: mandanos un mensaje al número 1100000001.\n"
    "• Atención comercial: 0810-000-0000 de lunes a viernes de 8 a 17 horas.\n\n"
    "Sin más, se despide cordialmente,\n\n**Asesor Comercial.**"
)


def _carta(cuerpo):
    return f"{APERTURA}\n\n{cuerpo}\n\n{CIERRE}"


DOC = f"""# Plantillas y Modelos de Respuesta

Guía general de uso de las plantillas.

## Derivaciones

### Derivación a Tarifa 3 (T3)
{_carta("Verificamos que la cuenta corresponde a tarifa 3. Escribí a persona7@voltara.example")}

### Mesa de Entrada
{_carta("Canalizá la consulta por persona8@enerval.example para recibir una respuesta.")}

## Pagos

### Aviso de Pago No Acreditado
{_carta("Aguardá un plazo de 24 a 72 horas hábiles para que el pago se vea impactado.")}
"""


def test_partir_cartas_respeta_categorias_y_preambulo():
    cartas, preambulo = ac.partir_cartas(DOC)
    assert [c["titulo"] for c in cartas] == [
        "Derivación a Tarifa 3 (T3)", "Mesa de Entrada", "Aviso de Pago No Acreditado",
    ]
    assert [c["categoria"] for c in cartas] == ["Derivaciones", "Derivaciones", "Pagos"]
    assert "Guía general de uso" in preambulo
    # El encabezado de categoría no se cuela dentro del cuerpo de la carta anterior.
    assert "## Pagos" not in cartas[1]["texto"]


def test_detectar_boilerplate_encuentra_lo_repetido_y_no_lo_propio():
    cartas, _ = ac.partir_cartas(DOC)
    bp = ac.detectar_boilerplate(cartas)
    assert ac._normalizar("Hola **nombre del cliente**,") in bp
    assert ac._normalizar("Sin más, se despide cordialmente,") in bp
    assert ac._normalizar("• WHATSAPP: mandanos un mensaje al número 1100000001.") in bp
    # Lo que distingue a cada carta NO es boilerplate.
    assert ac._normalizar("Verificamos que la cuenta corresponde a tarifa 3.") not in bp


def test_lineas_de_contenido_deja_solo_lo_discriminante():
    cartas, _ = ac.partir_cartas(DOC)
    bp = ac.detectar_boilerplate(cartas)
    contenido = ac.lineas_de_contenido(cartas[0]["texto"], bp)
    assert any("tarifa 3" in l for l in contenido)
    assert not any("whatsapp" in l for l in contenido)


def test_extraer_apertura_y_cierre():
    cartas, _ = ac.partir_cartas(DOC)
    bp = ac.detectar_boilerplate(cartas)
    apertura, cierre = ac.extraer_apertura_cierre(cartas, bp)
    assert "Hola **nombre del cliente**," in apertura
    assert "se despide cordialmente" in cierre
    assert "tarifa 3" not in apertura and "tarifa 3" not in cierre


def test_cierre_comun_es_el_tipico_y_los_opcionales_van_aparte():
    """Algunas cartas suman bloques promocionales opcionales (y excluyentes entre sí). El
    cierre común tiene que ser el TÍPICO: si se tomara el más largo, el bot pegaría esos
    bloques en todas las cartas y mandaría textos contradictorios."""
    promo = "**¿Sabías que tenés la opción de adherirte a factura digital? Hacete digital acá.**"
    ya_adherido = "**Verificamos que ya estás adherido a factura digital, no podés tener ambas.**"
    cartas = []
    for i in range(8):
        extra = ""
        if i == 0:
            extra = promo + "\n\n"
        elif i == 1:
            extra = ya_adherido + "\n\n"
        cartas.append({"titulo": f"Carta {i}", "categoria": "",
                       "texto": f"{APERTURA}\n\nContenido propio y distintivo del caso {i}.\n\n{extra}{CIERRE}"})

    bp = ac.detectar_boilerplate(cartas)
    apertura, cierre = ac.extraer_apertura_cierre(cartas, bp)

    # El cierre típico no arrastra los bloques que solo tienen 1-2 cartas.
    assert "se despide cordialmente" in cierre
    assert "adherirte a factura digital" not in cierre
    assert "ya estás adherido" not in cierre
    assert "Hola **nombre del cliente**," in apertura


def test_verificar_detecta_carta_perdida_y_contenido_faltante():
    cartas, _ = ac.partir_cartas(DOC)
    bp = ac.detectar_boilerplate(cartas)
    analizadas = {
        # Completa: conserva su contenido propio.
        "Derivación a Tarifa 3 (T3)": {
            "titulo": "Derivación a Tarifa 3 (T3)",
            "cuerpo_variable": "Verificamos que la cuenta corresponde a tarifa 3. "
                               "Escribí a persona7@voltara.example",
        },
        # Mutilada: se perdió el dato propio.
        "Mesa de Entrada": {"titulo": "Mesa de Entrada", "cuerpo_variable": "Canalizá la consulta."},
        # 'Aviso de Pago No Acreditado' directamente no vino.
    }
    problemas = ac.verificar_sin_perdida(cartas, analizadas, bp)
    por_carta = {p["carta"]: p for p in problemas}
    assert "Derivación a Tarifa 3 (T3)" not in por_carta      # esta está bien
    assert "línea" in por_carta["Mesa de Entrada"]["problema"]
    assert "no devolvió" in por_carta["Aviso de Pago No Acreditado"]["problema"]


def test_verificar_ignora_texto_que_esta_en_otras_cartas():
    """Perder una línea que también vive en otras plantillas no es pérdida de conocimiento:
    reportarla solo tapa las pérdidas reales."""
    compartida = "Recordá que podés adherirte por nuestros canales digitales de siempre."
    cartas = [
        {"titulo": "A", "categoria": "", "texto": f"Dato exclusivo de la carta A sobre tarifa 3.\n{compartida}"},
        {"titulo": "B", "categoria": "", "texto": f"Dato exclusivo de la carta B sobre medidores.\n{compartida}"},
        {"titulo": "C", "categoria": "", "texto": f"Dato exclusivo de la carta C sobre subsidios.\n{compartida}"},
    ]
    # Ninguna conserva la línea compartida; A además pierde lo suyo.
    analizadas = {
        "A": {"titulo": "A", "cuerpo_variable": ""},
        "B": {"titulo": "B", "cuerpo_variable": "Dato exclusivo de la carta B sobre medidores."},
        "C": {"titulo": "C", "cuerpo_variable": "Dato exclusivo de la carta C sobre subsidios."},
    }
    problemas = {p["carta"] for p in ac.verificar_sin_perdida(cartas, analizadas, set())}
    assert problemas == {"A"}          # solo se reporta la pérdida real y exclusiva


def test_verificar_acepta_texto_reubicado_sin_su_etiqueta():
    """El texto de una 'Nota operativa:' se reubica en su campo propio y ahí ya no lleva
    la etiqueta: no debe contarse como perdido."""
    cartas = [{"titulo": "A", "categoria": "",
               "texto": "**Nota operativa:** No ofrecer Factura Digital en este caso puntual."}]
    analizadas = {"A": {"titulo": "A", "cuerpo_variable": "",
                        "notas_operativas": "No ofrecer Factura Digital en este caso puntual."}}
    assert ac.verificar_sin_perdida(cartas, analizadas, set()) == []


def test_emparejar_titulos_tolera_sufijos_y_variaciones():
    """La IA a veces devuelve el título con un paréntesis pegado o con acentos distintos;
    si no se empareja, la carta se da por perdida aunque haya venido completa."""
    originales = [
        {"titulo": "Denuncia de Conexión Clandestina / Enganchado a Medidor", "categoria": "", "texto": ""},
        {"titulo": "Retiro de Vallas de Seguridad", "categoria": "", "texto": ""},
        {"titulo": "Consulta sobre Cuadro Tarifario", "categoria": "", "texto": ""},
    ]
    devueltas = [
        # Con la categoría pegada al título (el bug real detectado con Voltara).
        {"titulo": "Denuncia de Conexión Clandestina / Enganchado a Medidor (categoría: Reclamos Técnicos)", "cuerpo_variable": "a"},
        {"titulo": "retiro de vallas de seguridad", "cuerpo_variable": "b"},   # minúsculas
        {"titulo": "Consulta sobre Cuadro Tarifarios", "cuerpo_variable": "c"},  # typo
    ]
    m = ac._emparejar_por_titulo(originales, devueltas)
    assert set(m) == {o["titulo"] for o in originales}
    assert m["Denuncia de Conexión Clandestina / Enganchado a Medidor"]["cuerpo_variable"] == "a"
    assert m["Consulta sobre Cuadro Tarifario"]["cuerpo_variable"] == "c"


def test_variantes_del_boilerplate_no_se_toman_por_contenido():
    """El mismo párrafo común aparece redactado distinto según la carta. Si no se toleran
    esas variantes, la verificación reporta pérdidas que no existen."""
    bp = {ac._normalizar("Nos comunicamos por la solicitud que realizaste.")}
    # Variante redactada distinto: sigue siendo boilerplate.
    assert ac._es_boilerplate(ac._normalizar("Nos comunicamos por la solicitud que realizaste hoy."), bp)
    # Contenido propio de una carta: NO es boilerplate.
    assert not ac._es_boilerplate(
        ac._normalizar("Verificamos que la cuenta corresponde a tarifa 3."), bp)


def test_armar_documento_incluye_catalogo_y_texto_comun_una_sola_vez():
    cartas = [
        {"titulo": "Derivación a Tarifa 3 (T3)", "categoria": "Derivaciones",
         "cuando_usarla": "La cuenta es tarifa 3 y hay que derivar a grandes clientes.",
         "disparadores": ["T3", "tarifa 3", "grandes clientes"],
         "campos_a_completar": ["nombre del cliente"],
         "cuerpo_variable": "Verificamos que la cuenta corresponde a tarifa 3."},
        {"titulo": "Mesa de Entrada", "categoria": "Derivaciones",
         "cuando_usarla": "La consulta no es un trámite habitual de clientes.",
         "disparadores": ["mesa de entrada"], "cuerpo_variable": "Canalizá la consulta por mail.",
         "notas_operativas": "No ofrecer Factura Digital."},
    ]
    doc = ac.armar_documento(cartas, APERTURA, CIERRE, "Plantillas", "")

    assert "## Catálogo de cartas disponibles" in doc
    assert "| Derivación a Tarifa 3 (T3) |" in doc          # fila del catálogo
    assert "T3, tarifa 3, grandes clientes" in doc          # disparadores para BM25
    assert "### Carta: Mesa de Entrada" in doc
    assert "**Nota operativa:** No ofrecer Factura Digital." in doc
    # El texto común aparece UNA sola vez (esa es toda la idea).
    assert doc.count("Sin más, se despide cordialmente,") == 1
    assert doc.count("Hola **nombre del cliente**,") == 1


def test_reestructurar_catalogo_end_to_end(monkeypatch):
    """Flujo completo con la IA mockeada: parte, analiza, verifica y arma."""
    def fake_generar_json(system, parts, schema, **kw):
        payload = parts[0].text
        cartas = []
        for titulo, cuerpo in [
            ("Derivación a Tarifa 3 (T3)", "Verificamos que la cuenta corresponde a tarifa 3. Escribí a persona7@voltara.example"),
            ("Mesa de Entrada", "Canalizá la consulta por persona8@enerval.example para recibir una respuesta."),
            ("Aviso de Pago No Acreditado", "Aguardá un plazo de 24 a 72 horas hábiles para que el pago se vea impactado."),
        ]:
            if f"=== CARTA: {titulo}" in payload:
                cartas.append({"titulo": titulo, "cuando_usarla": f"Caso {titulo}.",
                               "disparadores": [titulo.lower()], "cuerpo_variable": cuerpo})
        return {"cartas": cartas}

    monkeypatch.setattr(ac, "_generar_json", fake_generar_json)

    res = ac.reestructurar_catalogo(DOC, titulo_doc="Plantillas")

    assert res["total_cartas"] == 3
    assert res["problemas"] == []                      # nada se perdió
    assert "## Catálogo de cartas disponibles" in res["markdown"]
    assert res["markdown"].count("se despide cordialmente") == 1


def test_cada_carta_queda_sin_boilerplate(monkeypatch):
    """La propiedad que hace que el RAG vuelva a distinguirlas: el fragmento de cada carta
    pasa a ser casi todo contenido propio, en vez de 55% texto común a todas.
    (El tamaño total del documento es un efecto secundario y depende del volumen de cartas:
    con pocas cartas los metadatos del catálogo pesan más que el boilerplate ahorrado.)"""
    cuerpos = {f"Carta {i}": f"Instrucción específica y particular del caso número {i} para el cliente."
               for i in range(1, 21)}
    doc = "# Plantillas\n\n## Categoría\n\n" + "\n\n".join(
        f"### {t}\n{_carta(c)}" for t, c in cuerpos.items()
    )

    def fake(system, parts, schema, **kw):
        payload = parts[0].text
        return {"cartas": [
            {"titulo": t, "cuando_usarla": f"Caso {t}.", "disparadores": [t.lower()], "cuerpo_variable": c}
            for t, c in cuerpos.items() if f"=== CARTA: {t} " in payload
        ]}

    monkeypatch.setattr(ac, "_generar_json", fake)
    res = ac.reestructurar_catalogo(doc, titulo_doc="Plantillas")

    assert res["total_cartas"] == 20
    assert res["problemas"] == []
    # El texto común aparece UNA vez en todo el documento (antes: 20 veces).
    assert res["markdown"].count("se despide cordialmente") == 1
    assert res["markdown"].count("Hola **nombre del cliente**,") == 1
    # Y la sección de cada carta contiene lo suyo y nada del boilerplate.
    seccion = res["markdown"].split("### Carta: Carta 7")[1].split("### Carta:")[0]
    assert "caso número 7" in seccion
    assert "WHATSAPP" not in seccion and "se despide cordialmente" not in seccion


def test_reestructurar_sin_cartas_falla_claro():
    with pytest.raises(ValueError, match="No se encontraron cartas"):
        ac.reestructurar_catalogo("# Documento\n\nTexto sin plantillas.")
