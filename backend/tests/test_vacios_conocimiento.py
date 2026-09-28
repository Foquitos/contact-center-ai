"""Vacíos de conocimiento: detección, agrupación y triage.

Sin tokens: la señal barata (`evaluar_cobertura`) es determinística y el resto se
prueba con el cliente de Gemini y Qdrant mockeados. Lo que NO se prueba acá es si
el modelo clasifica bien — eso es criterio humano y se valida mirando la pantalla.
"""
import contextlib
from unittest.mock import MagicMock, patch

import pytest

from app.vacios_conocimiento import (
    AgrupadorVacios,
    evaluar_cobertura,
    parece_sin_cobertura,
    score_maximo,
)


# ------------------------------------------------------------ señal barata

@pytest.mark.parametrize("respuesta", [
    "No encontré información sobre ese tema específico en los manuales disponibles.",
    "No encontré información específica sobre el \"trámite caja nh\".",
    "La documentación proporcionada no especifica qué cartas se deben enviar.",
    "No cuento con esa información en los manuales.",
    "Los documentos proporcionados no contienen ese procedimiento.",
    "No dispongo de datos sobre la poda de árboles.",
    "no hay información sobre el monto límite",
])
def test_detecta_las_variantes_reales_de_sin_cobertura(respuesta):
    """Las frases salen del tráfico real: el modelo parafrasea la frase canónica
    del system prompt, así que el prefiltro tiene que aceptar variantes."""
    assert parece_sin_cobertura(respuesta)


@pytest.mark.parametrize("respuesta", [
    "Para el Cambio de Titularidad el cliente debe presentar DNI frente y dorso.",
    "El subsidio está asociado al cliente (titular) y no a la dirección.",
    "",
    None,
])
def test_no_marca_respuestas_buenas(respuesta):
    assert not parece_sin_cobertura(respuesta)


def test_score_muy_malo_marca_aunque_el_texto_parezca_una_respuesta():
    """El bot puede improvisar en vez de decir 'no encontré'. Si NADA de lo
    recuperado era relevante, igual es una consulta sin cobertura documental."""
    respuesta = "Para gestionar eso el operador debe seguir el procedimiento habitual."
    assert not parece_sin_cobertura(respuesta)
    assert evaluar_cobertura(respuesta, score_max=-9.8)
    assert not evaluar_cobertura(respuesta, score_max=4.2)


def test_no_marca_cuando_dice_no_encontre_pero_contesta_igual():
    """Regresión del 31/07: con el prompt anclado el modelo empezó a usar la frase
    como muletilla sobre consultas que recuperaban perfecto ("artefactos dañados",
    score +5,6) y después contestaba igual. Sin este filtro, temas bien documentados
    entraban a la pantalla de Calidad como huecos."""
    respuesta = (
        "No encontré información sobre ese tema específico en los manuales disponibles. "
        "No obstante, la documentación menciona procedimientos relacionados con la "
        "gestión de artefactos dañados: " + "detalle del procedimiento. " * 20
    )
    assert len(respuesta) > 400
    assert not evaluar_cobertura(respuesta, score_max=5.6)


def test_si_contesta_largo_pero_no_recuperó_nada_sigue_siendo_vacío():
    """Una respuesta larga con score malo es puro relleno: el bot divagó sin
    documentación, y eso sí es un vacío."""
    respuesta = "No encontré información sobre ese tema. " + "texto de relleno. " * 30
    assert evaluar_cobertura(respuesta, score_max=-6.5)


def test_negacion_pura_con_buen_score_sigue_siendo_vacío():
    """El chunk podía ser del tema correcto pero no traer el dato puntual pedido."""
    respuesta = "No encontré información sobre ese tema específico en los manuales disponibles."
    assert evaluar_cobertura(respuesta, score_max=4.2)


def test_sin_score_decide_solo_el_texto():
    """Las filas históricas no tienen score_max (la columna es nueva)."""
    assert evaluar_cobertura("No encontré información sobre eso.", None)
    assert not evaluar_cobertura("El plazo es de 15 días hábiles.", None)


# ------------------------------------------------- consultas con adjunto
# Una consulta que llega con una captura recupera mal por diseño: el error de
# pantalla que el operador está mirando no está en ningún manual, así que el
# reranker puntúa pésimo y el bot igual responde bien leyendo la imagen. Sin la
# excepción, TODAS esas consultas entraban a la pantalla de Calidad como huecos
# de documentación inventados, y encima gastando una clasificación de IA por cada
# una. Lo que se apaga es solo la rama del score, no la del texto.

def test_con_adjunto_un_score_malo_ya_no_alcanza_para_marcar():
    respuesta = "En la captura se ve el error 0x8007, que corresponde a la sesión vencida."
    assert evaluar_cobertura(respuesta, score_max=-9.8) is True
    assert evaluar_cobertura(respuesta, score_max=-9.8, con_adjuntos=True) is False


def test_con_adjunto_un_no_encontre_sigue_siendo_un_vacio_real():
    """Si el bot dice que el tema no está en los manuales, el hueco existe igual: que
    lo haya podido responder mirando la captura no lo tapa, lo confirma."""
    respuesta = "No encontré información sobre ese procedimiento en los manuales disponibles."
    assert evaluar_cobertura(respuesta, score_max=-9.8, con_adjuntos=True) is True


def test_sin_adjunto_no_cambia_nada():
    """El parámetro es opt-in: el tráfico normal se sigue evaluando igual que antes."""
    respuesta = "Para gestionar eso el operador debe seguir el procedimiento habitual."
    assert evaluar_cobertura(respuesta, score_max=-9.8) == evaluar_cobertura(
        respuesta, score_max=-9.8, con_adjuntos=False
    )


def test_score_maximo_toma_el_mejor_e_ignora_los_nulos():
    nodos = [MagicMock(score=-3.5), MagicMock(score=None), MagicMock(score=1.2)]
    assert score_maximo(nodos) == 1.2
    assert score_maximo([]) is None
    assert score_maximo(None) is None


# -------------------------------------------------------------- agrupación

def _agrupador_con_hit(vacio_id, score_ok=True):
    client = MagicMock()
    client.collection_exists.return_value = True
    punto = MagicMock()
    punto.payload = {"vacio_id": vacio_id, "slug": "voltara"}
    client.query_points.return_value.points = [punto] if score_ok else []
    return AgrupadorVacios(client, min_score=0.88), client


def test_agrupa_con_un_tema_existente():
    agrupador, _ = _agrupador_con_hit(7)
    assert agrupador.buscar("voltara", [0.1, 0.2]) == 7


def test_sin_vecino_cercano_abre_un_tema_nuevo():
    agrupador, _ = _agrupador_con_hit(7, score_ok=False)
    assert agrupador.buscar("voltara", [0.1, 0.2]) is None


def test_la_busqueda_filtra_por_bot():
    """"medidor" en Voltara no es el mismo tema que en otra campaña: sin el filtro
    los vacíos de bots distintos se mezclarían en un solo grupo."""
    agrupador, client = _agrupador_con_hit(7)
    agrupador.buscar("voltara", [0.1, 0.2])
    kwargs = client.query_points.call_args.kwargs
    assert kwargs["query_filter"] is not None
    assert kwargs["score_threshold"] == 0.88


def test_olvidar_borra_el_centroide_huerfano():
    """Tras purgar temas en SQL, el centroide tiene que irse también: si no, un
    vacío nuevo agrupa contra un id inexistente y el UPDATE del log viola la FK."""
    agrupador, client = _agrupador_con_hit(7)
    agrupador.olvidar(7)
    assert client.delete.called
    assert client.delete.call_args.kwargs["collection_name"] == "vacios_conocimiento"


def test_olvidar_no_rompe_si_no_hay_coleccion():
    client = MagicMock()
    client.collection_exists.return_value = False
    AgrupadorVacios(client).olvidar(7)
    assert not client.delete.called


def test_qdrant_caido_no_rompe_la_agrupacion():
    """Es un job de fondo: que falle Qdrant no puede tumbar el scheduler."""
    client = MagicMock()
    client.collection_exists.side_effect = RuntimeError("qdrant caído")
    assert AgrupadorVacios(client).buscar("voltara", [0.1]) is None


# ------------------------------------------------------ causa del vacío

_CTX_REAL = """Q Original: numero de atencion de emergencia


Fuentes:
--- Archivo: 02_dbdoc_17.md (Score: 5.5792) ---
Contenido:
### Inversión de Medidores
Se aplica cuando el cliente informa que al realizar la prueba de corte en su domicilio.

--- Archivo: 02_dbdoc_17.md (Score: 5.2027) ---
Contenido:
#### Situación 2: Cliente se comunica por un SVP que ya está en proceso
1. Ingresar al reclamo existente mediante el N° de caso.
"""


def test_score_alto_sin_la_respuesta_NO_es_generacion():
    """LA REGRESIÓN. Caso real del 05/08: "numero de atencion de emergencia" recuperó
    con score 5,58, pero los chunks eran sobre inversión de medidores y reclamos Sin
    Luz — ni un teléfono. El bot hizo bien en no inventarlo.

    Clasificarlo como 'generacion' le decía a Calidad "el bot lo tenía y no lo usó"
    sobre información que no existía, y mandaba a arreglar un prompt que estaba bien.
    """
    from app.vacios_conocimiento import _clasificar_causa

    with patch("app.vacios_conocimiento._juzgar_fragmentos", return_value=(False, None)), \
         patch("app.vacios_conocimiento.buscar_en_corpus", return_value=[]):
        clas, doc, score = _clasificar_causa(
            "voltara", "numero de atencion de emergencia", {},
            score_produccion=5.58, contexto_produccion=_CTX_REAL,
        )

    assert clas == "hueco"          # antes daba 'generacion' solo por el score
    assert doc is None


def test_es_generacion_cuando_el_contexto_SI_traia_la_respuesta():
    """El otro lado: si el dato estaba servido y el bot dijo que no sabía, el
    problema es del prompt y hay que mandarlo a quien toca los prompts."""
    from app.vacios_conocimiento import _clasificar_causa

    with patch("app.vacios_conocimiento._juzgar_fragmentos",
               return_value=(True, "02_dbdoc_17.md")):
        clas, doc, score = _clasificar_causa(
            "voltara", "numero de emergencia", {},
            score_produccion=5.58, contexto_produccion=_CTX_REAL,
        )

    assert clas == "generacion"
    assert doc == "02_dbdoc_17.md"   # el documento que el bot ya tenía delante
    assert score == 5.58


def test_generacion_no_vuelve_a_sondear_el_corpus():
    """Si el dato ya estaba en el contexto entregado, re-buscarlo es gastar de más."""
    from app.vacios_conocimiento import _clasificar_causa

    with patch("app.vacios_conocimiento._juzgar_fragmentos", return_value=(True, "x.md")), \
         patch("app.vacios_conocimiento.buscar_en_corpus") as sondeo:
        _clasificar_causa("voltara", "tema", {}, 5.5, _CTX_REAL)

    assert not sondeo.called


def test_fuera_de_alcance_gana_sobre_todo():
    from app.vacios_conocimiento import _clasificar_causa

    clas, doc, score = _clasificar_causa(
        "voltara", "saludo", {"fuera_de_alcance": True},
        score_produccion=8.0, contexto_produccion=_CTX_REAL,
    )
    assert clas == "fuera_de_alcance"


def test_el_corpus_lo_tiene_pero_no_se_lo_entregaron_es_recuperacion():
    from app.vacios_conocimiento import _clasificar_causa

    with patch("app.vacios_conocimiento._juzgar_fragmentos",
               side_effect=[(False, None), (True, "04_dbdoc_19.md")]), \
         patch("app.vacios_conocimiento.buscar_en_corpus",
               return_value=[("04_dbdoc_19.md", 5.0, "texto que sí responde")]) as sondeo:
        clas, doc, score = _clasificar_causa("voltara", "poda", {}, -5.9, _CTX_REAL)

    assert sondeo.called
    assert clas == "recuperacion" and doc == "04_dbdoc_19.md"
    assert score == 5.0          # el score del documento que resolvió, no el mejor


def test_el_score_reportado_es_el_del_documento_que_resuelve():
    """El mejor rankeado puede NO ser el que tiene la respuesta: si se informara su
    score, Calidad vería un número que no corresponde al documento que se le señala."""
    from app.vacios_conocimiento import _clasificar_causa

    with patch("app.vacios_conocimiento._juzgar_fragmentos",
               side_effect=[(False, None), (True, "05_dbdoc_20.md")]), \
         patch("app.vacios_conocimiento.buscar_en_corpus", return_value=[
             ("04_dbdoc_19.md", 6.0, "el mejor rankeado, que no responde"),
             ("05_dbdoc_20.md", 1.2, "el que sí tiene el dato"),
         ]):
        clas, doc, score = _clasificar_causa("voltara", "tema", {}, -5.9, _CTX_REAL)

    assert clas == "recuperacion"
    assert doc == "05_dbdoc_20.md" and score == 1.2


def test_el_corpus_habla_del_tema_pero_no_responde_es_hueco():
    """El sondeo puede traer algo con score alto que igual no conteste: sin esto,
    volvíamos a decir "es del buscador" sobre documentación que falta."""
    from app.vacios_conocimiento import _clasificar_causa

    with patch("app.vacios_conocimiento._juzgar_fragmentos", return_value=(False, None)), \
         patch("app.vacios_conocimiento.buscar_en_corpus",
               return_value=[("04_dbdoc_19.md", 6.0, "habla del tema pero no contesta")]):
        clas, doc, score = _clasificar_causa("voltara", "poda", {}, -5.9, _CTX_REAL)

    assert clas == "hueco"
    assert doc is None and score is None


def test_sin_contexto_guardado_va_directo_al_sondeo():
    """Las filas históricas no tienen context: no se las puede juzgar por ahí."""
    from app.vacios_conocimiento import _clasificar_causa

    with patch("app.vacios_conocimiento.buscar_en_corpus", return_value=[]):
        clas, _, _ = _clasificar_causa("voltara", "poda", {}, None, None)

    assert clas == "hueco"


# --------------------------------------------- lectura del contexto guardado

def test_se_extrae_el_archivo_mejor_rankeado():
    from app.vacios_conocimiento import _archivo_top_del_contexto

    assert _archivo_top_del_contexto(_CTX_REAL) == "02_dbdoc_17.md"
    assert _archivo_top_del_contexto(None) is None
    assert _archivo_top_del_contexto("sin fuentes") is None


def test_los_fragmentos_salen_etiquetados_con_su_documento():
    """El juez tiene que poder decir en CUÁL documento está la respuesta, así que
    cada fragmento viaja con su archivo. Y sin los encabezados "(Score: ...)", que
    son ruido y números sin significado para el modelo."""
    from app.vacios_conocimiento import _fragmentos_del_contexto

    frags = _fragmentos_del_contexto(_CTX_REAL)

    assert len(frags) == 2                    # trae TODOS los chunks, no solo el primero
    assert frags[0][0] == "02_dbdoc_17.md"
    assert "Inversión de Medidores" in frags[0][1]
    assert "Situación 2" in frags[1][1]
    unido = " ".join(t for _, t in frags)
    assert "Score:" not in unido and "Contenido:" not in unido


def test_sin_contexto_los_fragmentos_son_vacios():
    from app.vacios_conocimiento import _fragmentos_del_contexto

    assert _fragmentos_del_contexto(None) == []
    assert _fragmentos_del_contexto("") == []


def test_el_juez_no_llama_al_modelo_sin_fragmentos():
    from app.vacios_conocimiento import _juzgar_fragmentos

    with patch("app.vacios_conocimiento._get_client") as cliente:
        assert _juzgar_fragmentos("tema", []) == (False, None)
        assert _juzgar_fragmentos("tema", [("a.md", "   ")]) == (False, None)

    assert not cliente.called


def test_si_el_juez_falla_no_inventa_un_generacion():
    """Ante un error de la API conviene decir "falta documentarlo" y no "el bot
    funciona mal": lo primero se verifica leyendo, lo segundo manda a tocar un prompt."""
    from app.vacios_conocimiento import _juzgar_fragmentos

    with patch("app.vacios_conocimiento._get_client", side_effect=RuntimeError("api caída")):
        assert _juzgar_fragmentos("tema", [("a.md", "un texto cualquiera")]) == (False, None)


def test_el_juez_no_acepta_un_documento_inventado():
    """El modelo podría devolver un nombre que no le pasamos y terminaría en pantalla
    mandando a Calidad a un documento que no existe."""
    from app.vacios_conocimiento import _juzgar_fragmentos

    resp = MagicMock()
    resp.text = '{"resuelve": true, "documento": "documento_inventado.md"}'
    with patch("app.vacios_conocimiento._get_client") as cliente:
        cliente.return_value.models.generate_content.return_value = resp
        resuelve, doc = _juzgar_fragmentos("tema", [("04_dbdoc_19.md", "texto")])

    assert resuelve is True
    assert doc == "04_dbdoc_19.md"      # cae al que sí se le pasó


def test_el_juez_devuelve_el_documento_que_nombro():
    from app.vacios_conocimiento import _juzgar_fragmentos

    resp = MagicMock()
    resp.text = '{"resuelve": true, "documento": "05_dbdoc_20.md"}'
    with patch("app.vacios_conocimiento._get_client") as cliente:
        cliente.return_value.models.generate_content.return_value = resp
        resuelve, doc = _juzgar_fragmentos(
            "tema", [("04_dbdoc_19.md", "no"), ("05_dbdoc_20.md", "sí")])

    assert (resuelve, doc) == (True, "05_dbdoc_20.md")


# ----------------------------------------- nombre interno -> título legible

def _conn_con_docs(filas):
    conn = MagicMock()
    conn.execute.return_value.mappings.return_value.all.return_value = filas
    return conn


def test_el_documento_se_muestra_con_su_titulo():
    """En pantalla se leía "04_dbdoc_19.md", que no le dice nada a quien tiene que ir
    a corregir la documentación."""
    from app.vacios_conocimiento import _titulo_documento

    conn = _conn_con_docs([{"id": 19, "titulo": "Facturación, Medios de Pago y Cobranzas"}])

    assert _titulo_documento(conn, "04_dbdoc_19.md") == "Facturación, Medios de Pago y Cobranzas"


def test_si_el_documento_ya_no_existe_se_deja_el_nombre():
    from app.vacios_conocimiento import _titulo_documento

    assert _titulo_documento(_conn_con_docs([]), "04_dbdoc_19.md") == "04_dbdoc_19.md"


def test_un_documento_de_otro_origen_no_se_toca():
    from app.vacios_conocimiento import _titulo_documento

    conn = MagicMock()
    assert _titulo_documento(conn, "manual_drive.md") == "manual_drive.md"
    assert _titulo_documento(conn, None) is None
    assert not conn.execute.called


# ------------------------------------------- ¿la info existe en el corpus?

@contextlib.contextmanager
def _servicios_mockeados(bot):
    """Reemplaza app.services por un doble.

    Hay que parchear el atributo del paquete ADEMÁS de sys.modules: si otro test
    de la suite ya importó app.services de verdad, `import app.services as x`
    resuelve por getattr(app, "services") y esquiva el parche de sys.modules —
    el test pasaba aislado y fallaba en la suite completa.
    """
    import app

    # Se precarga ANTES de entrar al patch: patch.dict restaura sys.modules entero
    # al salir, así que se lleva puesto lo que se haya importado adentro. Cuando eso
    # pasaba con llama_index, el siguiente import re-ejecutaba sqlalchemy y reventaba
    # con "Type <class 'object'> is already registered" en un test que no tenía nada
    # que ver. Pasaba solo al correr el archivo entero, nunca aislado.
    import llama_index.core.schema  # noqa: F401

    servicios = MagicMock()
    servicios.chatbot_registry.get.return_value = bot
    with patch.object(app, "services", servicios, create=True), \
         patch.dict("sys.modules", {"app.services": servicios}):
        yield servicios

def _nodo(score, archivo, texto="contenido del chunk"):
    nodo = MagicMock()
    nodo.score = score
    nodo.node.metadata = {"file_name": archivo}
    nodo.node.get_content.return_value = texto
    return nodo


def _bot_con(nodos):
    bot = MagicMock()
    bot.index.as_retriever.return_value.retrieve.return_value = nodos
    bot.reranker.postprocess_nodes.return_value = nodos
    return bot


def test_el_sondeo_devuelve_los_textos_etiquetados():
    """Sin el texto, quien clasifica no puede verificar si la respuesta está."""
    from app.vacios_conocimiento import buscar_en_corpus

    bot = _bot_con([_nodo(5.2, "/docs/04_dbdoc_19.md", "La poda se solicita al 0800-000-0002.")])

    with _servicios_mockeados(bot):
        encontrados = buscar_en_corpus("voltara", "poda de árboles")

    assert encontrados == [("04_dbdoc_19.md", 5.2, "La poda se solicita al 0800-000-0002.")]


def test_el_sondeo_devuelve_varios_chunks():
    """Con uno solo se pierde la respuesta cuando está en el segundo."""
    from app.vacios_conocimiento import buscar_en_corpus

    bot = _bot_con([_nodo(5.2, "a.md", "primero"), _nodo(4.9, "b.md", "segundo"),
                    _nodo(4.1, "c.md", "tercero")])

    with _servicios_mockeados(bot):
        textos = [t for _, _, t in buscar_en_corpus("voltara", "poda")]

    assert textos == ["primero", "segundo", "tercero"]


def test_el_sondeo_NO_descarta_por_score_bajo():
    """LA REGRESIÓN del 05/08. Medido sobre el chunk que literalmente contiene
    "ENRE ...: 0800-000-0001": la consulta "número de contacto del ENRE" lo puntúa
    -3,46 (y "ENRE" a secas, +5,28). Con el umbral en 3,0 ese chunk se tiraba y el
    tema salía como 'hueco' — le pedíamos a Calidad documentar un teléfono que ya
    estaba escrito. Ahora se lo pasa al juez, que lo LEE."""
    from app.vacios_conocimiento import buscar_en_corpus

    bot = _bot_con([_nodo(-3.46, "06_dbdoc_21.md", "ENRE: 0800-000-0001 / 0800-000-0003")])

    with _servicios_mockeados(bot):
        encontrados = buscar_en_corpus("voltara", "número de contacto del ENRE")

    assert len(encontrados) == 1
    assert "0800-000-0001" in encontrados[0][2]


def test_el_sondeo_acota_cuantos_fragmentos_manda_a_leer(monkeypatch):
    """Sin filtro de score, el tope de fragmentos es lo único que acota lo que se
    manda al LLM: top_k=50 entero sería pagar de más en cada vacío."""
    from app.config import settings
    from app.vacios_conocimiento import buscar_en_corpus

    monkeypatch.setattr(settings, "CHATBOT_VACIO_FRAGMENTOS_A_LEER", 2)
    bot = _bot_con([_nodo(5.0 - i, f"d{i}.md", f"t{i}") for i in range(10)])

    with _servicios_mockeados(bot):
        assert len(buscar_en_corpus("voltara", "poda")) == 2


def test_bot_no_disponible_no_rompe():
    from app.vacios_conocimiento import buscar_en_corpus

    with _servicios_mockeados(bot=None):
        assert buscar_en_corpus("inexistente", "tema") == []
