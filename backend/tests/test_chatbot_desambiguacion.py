"""Desambiguación: ofrecer temas en vez de "no encontré información".

Sin tokens: el gate es determinístico (mira scores del reranker y encabezados del
markdown indexado) y el recorrido completo va con el LLM mockeado.

La mitad de estos tests son sobre lo que el feature NO tiene que hacer. La razón es
que la desambiguación le come el terreno a la detección de vacíos de conocimiento
(app/vacios_conocimiento.py): cada consulta que se responde con una lista de opciones
es una consulta que Calidad NO ve como hueco de documentación. Si el gate se pasa de
generoso, la pantalla de vacíos se apaga sola y nadie se entera.
"""
import asyncio
import types
from unittest.mock import MagicMock

from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.core.memory import ChatMemoryBuffer
from llama_index.core.schema import NodeWithScore, TextNode

import pytest

from app import chatbot_desambiguacion as desamb
from app import chatbot_tablas
from app.vacios_conocimiento import evaluar_cobertura, parece_sin_cobertura
from chatBot import ChatBot


@pytest.fixture(autouse=True)
def _sin_tablas_de_datos(monkeypatch):
    """Estos bots no tienen tablas de datos cargadas.

    stream_query pregunta primero si la consulta la responde una tabla (ver
    app/chatbot_tablas.py); acá se responde que no, sin ir a la base, para que los
    tests sigan midiendo lo que miden: la desambiguación."""
    monkeypatch.setattr(chatbot_tablas, "resolver", lambda *a, **k: None)


def _nodo(score, texto, header_path="/"):
    """Un fragmento tal como sale del índice: el encabezado propio es la primera
    línea del texto y `header_path` trae los ancestros (MarkdownNodeParser)."""
    return NodeWithScore(
        node=TextNode(text=texto, metadata={"header_path": header_path}), score=score
    )


def _memoria(*mensajes):
    memoria = ChatMemoryBuffer.from_defaults(token_limit=3000)
    for rol, texto in mensajes:
        memoria.put(ChatMessage(role=rol, content=texto))
    return memoria


# --------------------------------------------------------------- cuándo SÍ

def test_ofrece_los_temas_cuando_hay_varios_parecidos_y_ninguno_responde():
    """El caso real: el operador escribe "poda" con el cliente en línea. Nada
    puntúa como respuesta, pero el manual tiene tres secciones de poda."""
    opciones = desamb.proponer([
        _nodo(2.1, "## Poda de árboles a pedido del cliente\nSe genera reclamo...",
              header_path="/Manual Voltara/Reclamos/"),
        _nodo(1.8, "## Poda de árboles a pedido del municipio\nSe deriva...",
              header_path="/Manual Voltara/Reclamos/"),
        _nodo(0.9, "## Ramas de un árbol sobre las líneas aéreas\nEs urgencia...",
              header_path="/Manual Voltara/Urgencias/"),
    ])

    assert len(opciones) == 3
    # El título solo: la rama que lo contiene no agrega nada que el título no diga.
    assert opciones[0] == "Poda de árboles a pedido del cliente"
    assert opciones[2] == "Ramas de un árbol sobre las líneas aéreas"


def test_el_texto_que_se_emite_es_markdown_legible_y_trae_las_opciones():
    texto = desamb.render(["Poda de árboles", "Ramas sobre las líneas aéreas"])

    assert desamb.INICIO in texto and desamb.FIN in texto
    assert "- **Poda de árboles**" in texto
    # Y no arrastra ninguna de las frases con las que se detecta un vacío: si el
    # bloque las tuviera, cada desambiguación entraría a la pantalla de Calidad.
    assert not parece_sin_cobertura(texto)


def test_una_lista_de_opciones_no_es_un_vacio_de_conocimiento():
    """La garantía del feature: lo que se ofrece salió del corpus, así que no falta
    documentación. Vale incluso con un score que por sí solo marcaría hueco (los
    umbrales se recalibran por reranker; esto no puede depender de eso)."""
    texto = desamb.render(["Poda de árboles", "Ramas sobre las líneas aéreas"])

    assert not evaluar_cobertura(texto, score_max=1.5)
    assert not evaluar_cobertura(texto, score_max=-9.0)


def test_las_opciones_no_llegan_cortadas_a_mitad_de_palabra():
    """Regresión del tráfico real (agosto): 26 de las 48 opciones distintas que se
    eligieron llegaron con exactamente 80 caracteres, cortadas a mitad de palabra
    —"Reclamos de Red, Trabajos de Vereda e Instalaciones › ¿Cuándo no vamos a
    generar"—. Se pagaba dos veces: se leía mal y, como la etiqueta es también la
    consulta que se manda al elegirla, buscaba peor."""
    opciones = desamb.proponer([
        _nodo(2.1, "### Inspección de Funcionamiento del Medidor\ntexto",
              header_path="/Manual Voltara/Medidores: Problemas Técnicos, Inspecciones y Delitos/"),
        _nodo(1.9, "### ¿Cuándo no vamos a generar un reclamo de red?\ntexto",
              header_path="/Manual Voltara/Reclamos de Red, Trabajos de Vereda e Instalaciones/"),
    ])

    assert opciones == [
        "Inspección de Funcionamiento del Medidor",
        "¿Cuándo no vamos a generar un reclamo de red?",
    ]
    assert not any(o.endswith("…") for o in opciones)


def test_un_titulo_con_barra_no_se_parte_al_usarlo_de_contexto():
    """El separador del `header_path` es "/" y varios títulos lo tienen adentro
    ("Tapa de Medidor (Cambio / Instalación)"). Sin repararlo, la opción que veía el
    operador era "Instalación) › Situaciones" — y eso mismo se buscaba al elegirla."""
    opciones = desamb.proponer_tras_negativa([
        _nodo(3.6, "## Situaciones\ntexto",
              header_path="/Guía VOLTARA/Tapa de Medidor (Cambio / Instalación)/"),
        _nodo(3.5, "## Medidor Quemado\ntexto", header_path="/Guía VOLTARA/"),
    ])

    assert opciones[0] == "Tapa de Medidor (Cambio / Instalación) › Situaciones"


def test_un_titulo_corto_se_muestra_con_su_seccion():
    """"Carga en Sistema" o "Definición y Medios" (que en el manual de Voltara aparece
    tres veces bajo secciones distintas) no dicen nada solos: ahí el padre es lo único
    que distingue una opción de la otra, y también lo que hace buscable la consulta."""
    opciones = desamb.proponer([
        _nodo(2.1, "### Carga en Sistema\ntexto",
              header_path="/Manual/Denuncias por Conexiones Clandestinas y Fraude/"),
        _nodo(1.9, "### Datos A Solicitar\ntexto",
              header_path="/Manual/Peligro en la Vía Pública (SVP)/"),
    ])

    assert opciones == [
        "Denuncias por Conexiones Clandestinas y Fraude › Carga en Sistema",
        "Peligro en la Vía Pública (SVP) › Datos A Solicitar",
    ]


def test_si_hay_que_recortar_se_corta_en_un_espacio():
    """El tope es una red de contención (ningún encabezado del corpus llega), pero si
    alguna vez corta, que no parta una palabra: el "…" lo saca el frontend antes de
    mandar la consulta."""
    largo = "Procedimiento " * 20
    etiqueta = desamb._etiqueta([largo.strip()])

    assert len(etiqueta) <= desamb.LARGO_MAX_ETIQUETA + 1
    assert etiqueta.endswith("…")
    assert etiqueta[:-1].endswith("Procedimiento")


# --------------------------------------------------------------- cuándo NO

def test_no_interrumpe_cuando_algo_responde_de_verdad():
    """Con un fragmento por encima de CHATBOT_VACIO_SCORE_EXISTE el bot tiene la
    respuesta: preguntarle al operador cuál tema quiere sería hacerle perder el
    tiempo con el cliente en línea."""
    assert desamb.proponer([
        _nodo(5.2, "## Poda de árboles\nSe genera reclamo...", header_path="/Manual/Reclamos/"),
        _nodo(1.1, "## Ramas sobre la acometida\nEs urgencia...", header_path="/Manual/Urgencias/"),
    ]) == []


def test_no_tapa_un_vacio_real_cuando_nada_de_lo_recuperado_sirve():
    """EL test importante. Por debajo del piso lo recuperado no tiene nada que ver:
    esa consulta tiene que seguir terminando en "no encontré" para que la detección
    de vacíos la registre y Calidad documente lo que falta."""
    nodos = [
        _nodo(-7.1, "## Cambio de titularidad\nDNI frente y dorso...", header_path="/Manual/Trámites/"),
        _nodo(-9.4, "## Poda de árboles\nSe genera reclamo...", header_path="/Manual/Reclamos/"),
    ]

    assert desamb.proponer(nodos) == []
    # ...y el camino de siempre sigue marcando el hueco.
    assert evaluar_cobertura(
        "No encontré información sobre ese tema en los manuales disponibles.",
        score_max=-7.1,
    )


def test_no_ofrece_opciones_cuando_un_solo_tema_domina():
    """"¿Cuánto tarda la poda?" recupera la sección de poda y poco más: la pregunta
    se entendió perfecto, lo que falta es el dato. Eso es un vacío, no una
    ambigüedad, y tiene que llegar a la pantalla de Calidad."""
    assert desamb.proponer([
        _nodo(2.8, "## Poda de árboles\nSe genera reclamo...", header_path="/Manual/Reclamos/"),
        _nodo(0.1, "## Cambio de titularidad\nDNI...", header_path="/Manual/Trámites/"),
    ]) == []


def test_los_pedazos_de_una_misma_seccion_no_cuentan_como_temas_distintos():
    """El SentenceSplitter parte las secciones largas y los pedazos que no son el
    primero se quedan sin encabezado propio (solo heredan la ruta del padre). Sin
    juntarlos por rama, UNA sección se vería como dos temas e inventaría una
    ambigüedad que no existe — tapando un vacío real."""
    assert desamb.proponer([
        _nodo(2.0, "## Poda de árboles\nSe genera reclamo...", header_path="/Manual/Reclamos/"),
        _nodo(1.9, "...continuación del procedimiento de poda...", header_path="/Manual/Reclamos/"),
    ]) == []


def test_no_ofrece_opciones_dos_veces_seguidas():
    """El operador eligió mal o volvió a escribir algo vago: a la segunda se
    responde como siempre, aunque sea con un "no encontré". Sin este corte queda
    en un bucle de listas y la consulta nunca se registra como vacío."""
    nodos = [
        _nodo(2.1, "## Poda a pedido del cliente\ntexto", header_path="/Manual/Reclamos/"),
        _nodo(1.8, "## Poda a pedido del municipio\ntexto", header_path="/Manual/Reclamos/"),
    ]
    historial = _memoria(
        (MessageRole.USER, "poda"),
        (MessageRole.ASSISTANT, desamb.render(["Poda a pedido del cliente"])),
    ).get_all()

    assert desamb.proponer(nodos) != []
    assert desamb.proponer(nodos, historial) == []


def test_sin_encabezados_no_hay_opciones_que_ofrecer():
    """Un corpus sin títulos no da etiquetas mostrables; antes que inventar nombres
    de temas, se responde como siempre."""
    assert desamb.proponer([
        _nodo(2.1, "texto suelto sin encabezado"),
        _nodo(1.9, "otro texto suelto"),
    ]) == []


def test_solo_desambiguan_los_bots_configurados():
    assert desamb.activo("voltara")
    assert not desamb.activo("voltara_digital")
    assert not desamb.activo(None)


# ------------------------------------------------- el recorrido completo

def test_una_consulta_ambigua_responde_con_opciones_sin_llamar_al_modelo(monkeypatch):
    """Camino entero sin red: que la lista salga por el stream, que el LLM no se
    toque, que no se cachee (es una repregunta, no una respuesta) y que el log no
    le cargue tokens a un turno que no consumió ninguno."""
    import chatBot as modulo_chatbot

    bot = object.__new__(ChatBot)
    bot.slug = "voltara"
    bot.config = types.SimpleNamespace(id=1, slug="voltara")
    bot.system_prompt = "Sos el asistente."
    bot.bm25_retriever = None
    bot.token_counter = MagicMock()
    bot.cache = MagicMock()
    bot.cache.check.return_value = None
    bot.index = MagicMock()
    bot.index.as_retriever.return_value = MagicMock()
    bot.reranker = MagicMock()
    bot.llm = MagicMock()
    bot._log_query_details = MagicMock()

    async def _recuperar(retriever, pregunta):
        return [
            _nodo(2.1, "## Poda a pedido del cliente\ntexto", header_path="/Manual/Reclamos/"),
            _nodo(1.8, "## Poda a pedido del municipio\ntexto", header_path="/Manual/Reclamos/"),
        ]

    async def _memoria_vacia(user_id):
        return _memoria()

    async def _embedding(texto):
        return [0.1, 0.2, 0.3]

    bot._recuperar_nodos = _recuperar
    bot._get_memory_for_user_async = _memoria_vacia
    monkeypatch.setattr(
        modulo_chatbot, "Settings",
        types.SimpleNamespace(embed_model=MagicMock(aget_query_embedding=_embedding)),
    )

    async def consultar():
        return "".join([
            token async for token in bot.stream_query("poda de arboles", 42, "voltara", "task-1")
        ])

    respuesta = asyncio.run(consultar())

    assert desamb.INICIO in respuesta
    assert "Poda a pedido del municipio" in respuesta
    bot.llm.astream_chat.assert_not_called()
    bot.cache.save.assert_not_called()

    # Tokens en 0 (no hubo llamada al modelo) y el log no lo cuenta como hueco.
    assert bot._log_query_details.call_args[0][6] == 0  # input_tokens
    assert bot._log_query_details.call_args[0][7] == 0  # output_tokens
    assert not evaluar_cobertura(bot._log_query_details.call_args[0][1], score_max=2.1)


def test_un_bot_sin_desambiguacion_sigue_yendo_por_el_motor(monkeypatch):
    """El feature es opt-in por slug: para el resto de los bots el camino de la
    consulta tiene que quedar exactamente como estaba (motor de LlamaIndex)."""
    import chatBot as modulo_chatbot

    bot = object.__new__(ChatBot)
    bot.slug = "paygo"
    bot.config = types.SimpleNamespace(id=2, slug="paygo")
    bot.system_prompt = "Sos el asistente."
    bot.bm25_retriever = None
    bot.token_counter = MagicMock()
    bot.cache = MagicMock()
    bot.cache.check.return_value = None
    bot.index = MagicMock()
    bot.index.as_retriever.return_value = MagicMock()
    bot.reranker = MagicMock()
    bot.llm = MagicMock()
    bot._log_query_details = MagicMock()

    async def _memoria_vacia(user_id):
        return _memoria()

    async def _embedding(texto):
        return [0.1, 0.2, 0.3]

    bot._get_memory_for_user_async = _memoria_vacia
    bot._recuperar_nodos = MagicMock()

    class _Respuesta:
        sources = []
        source_nodes = []

        async def async_response_gen(self):
            for token in ("Respuesta ", "del motor."):
                yield token

    async def _astream_chat(query):
        return _Respuesta()

    motor = MagicMock()
    motor.from_defaults.return_value = types.SimpleNamespace(astream_chat=_astream_chat)
    monkeypatch.setattr(modulo_chatbot, "CondensePlusContextChatEngine", motor)
    monkeypatch.setattr(
        modulo_chatbot, "Settings",
        types.SimpleNamespace(embed_model=MagicMock(aget_query_embedding=_embedding)),
    )

    async def consultar():
        return "".join([
            token async for token in bot.stream_query("poda de arboles", 42, "paygo", "task-2")
        ])

    assert asyncio.run(consultar()) == "Respuesta del motor."
    motor.from_defaults.assert_called_once()
    bot._recuperar_nodos.assert_not_called()


# ------------------------- cuando el modelo se niega igual (después de generar)
#
# El gate de arriba le cede el turno al modelo apenas algo puntúa por encima de
# CHATBOT_VACIO_SCORE_EXISTE. Estos tests son sobre lo que pasa cuando esa apuesta
# sale mal: el modelo contesta "no encontré" sobre material que el propio sistema da
# por documentado (89 de los 382 "no encontré" de voltara en agosto).

NEGACION = "No encontré información sobre ese tema específico en los manuales disponibles."

# Lo que recupera de verdad "Medidor" contra el corpus de Voltara (medido el
# 2026-09-04). El primero pasa 3,0 —por eso el gate previo no toca nada— y detrás hay
# cinco temas documentados, todos sobre medidores, que es lo que el operador quería.
NODOS_MEDIDOR = [
    _nodo(3.59, "## Situaciones\ntexto", header_path="/Guía VOLTARA/Tapa de Medidor (Cambio - Instalación)/"),
    _nodo(3.51, "## Medidor Quemado\ntexto", header_path="/Guía VOLTARA/"),
    _nodo(2.47, "## Traslado de Medidor (Monofásico/Trifásico)\ntexto", header_path="/Guía VOLTARA/"),
    _nodo(2.20, "## Inspección de Funcionamiento de Medidor\ntexto", header_path="/Guía VOLTARA/"),
    _nodo(2.07, "## Inversión de Medidores\ntexto", header_path="/Guía VOLTARA/"),
]


def test_una_consulta_de_una_palabra_ya_no_termina_en_no_encontre():
    """El caso que trajo el operador: "medidor". El gate previo se abstiene (hay un
    fragmento por encima de 3,0), el modelo se niega igual, y ahí recién se le ofrecen
    los temas que estaban documentados todo el tiempo."""
    assert desamb.proponer(NODOS_MEDIDOR) == []

    opciones = desamb.proponer_tras_negativa(NODOS_MEDIDOR)
    assert opciones[:2] == [
        "Tapa de Medidor (Cambio - Instalación) › Situaciones",
        "Medidor Quemado",
    ]
    assert len(opciones) == 4  # CHATBOT_DESAMB_MAX_OPCIONES


def test_con_un_solo_tema_documentado_igual_se_ofrece():
    """"Medidor monofasico" recupera UN tema fuerte (Traslado de Medidor, 4,70) y
    ruido. Para el gate previo eso sería "se entendió y falta el dato", pero acá el
    modelo ya se negó sobre ese mismo material: ofrecerlo es mejor que la negación."""
    nodos = [
        _nodo(4.70, "## Traslado de Medidor (Monofásico/Trifásico)\ntexto", header_path="/Guía VOLTARA/"),
        _nodo(-3.3, "## Artefactos Dañados\ntexto", header_path="/Guía VOLTARA/"),
    ]

    opciones = desamb.proponer_tras_negativa(nodos)
    assert opciones == ["Traslado de Medidor (Monofásico/Trifásico)"]
    # Y el texto no puede decir "varios temas" cuando hay uno solo.
    texto = desamb.render(opciones)
    assert desamb.ENCABEZADO_UNICO in texto and desamb.ENCABEZADO not in texto


def test_no_se_reemplaza_la_negativa_cuando_el_score_no_dice_que_el_tema_exista():
    """EL test importante de este camino. Por debajo de CHATBOT_VACIO_SCORE_EXISTE no
    hay contradicción que resolver: el modelo dice que no está y el sistema tampoco
    afirma que esté. Esa negación tiene que llegar entera a la pantalla de Calidad."""
    nodos = [
        _nodo(2.9, "## Poda de árboles\ntexto", header_path="/Manual/Reclamos/"),
        _nodo(2.8, "## Ramas sobre la acometida\ntexto", header_path="/Manual/Urgencias/"),
    ]

    assert desamb.proponer_tras_negativa(nodos) == []
    assert evaluar_cobertura(NEGACION, score_max=2.9)


def test_no_se_reemplaza_la_negativa_dos_veces_seguidas():
    """Mismo corte que en el gate previo: si el turno anterior ya fue una lista, la
    negación va tal cual y la consulta se registra como hueco."""
    historial = _memoria(
        (MessageRole.USER, "medidor"),
        (MessageRole.ASSISTANT, desamb.render(["Medidor Quemado", "Tapa de Medidor"])),
    ).get_all()

    assert desamb.proponer_tras_negativa(NODOS_MEDIDOR) != []
    assert desamb.proponer_tras_negativa(NODOS_MEDIDOR, historial) == []


# --------------------------------------------------- el freno del stream (retener)

def test_una_respuesta_normal_se_suelta_en_la_primera_palabra():
    """Lo que se retiene se paga en TTFT, que es lo único que el operador siente. Una
    respuesta común tiene que soltarse apenas se ve que no es una negación."""
    assert desamb.retener("🖥️")           # todavía no hay letras: no se sabe
    assert not desamb.retener("🖥️ **Acciones para el operador")
    assert not desamb.retener("Para gestionar el cambio de titularidad")
    assert not desamb.retener("### 1. Qué verificar")


def test_se_retiene_la_negacion_mientras_se_escribe():
    """Token a token, tal como llega del modelo."""
    acumulado = ""
    for trozo in ("No", " encontré", " información sobre ese tema"):
        acumulado += trozo
        assert desamb.retener(acumulado), acumulado
    assert desamb.retener(NEGACION)


def test_una_negacion_con_respuesta_pegada_atras_se_suelta():
    """La muletilla "No encontré... No obstante, la documentación menciona...". Ahí hay
    contenido: reemplazarlo por una lista de temas sería borrar una respuesta."""
    muletilla = NEGACION + " No obstante, la documentación menciona el procedimiento " + "de carga. " * 30
    assert not desamb.retener(muletilla)


# ------------------------------------------------- el recorrido completo (fallback)

def _bot_voltara(monkeypatch, nodos, salida_del_modelo):
    """Un bot de voltara con el retrieval y el LLM mockeados."""
    import chatBot as modulo_chatbot

    bot = object.__new__(ChatBot)
    bot.slug = "voltara"
    bot.config = types.SimpleNamespace(id=1, slug="voltara")
    bot.system_prompt = "Sos el asistente."
    bot.bm25_retriever = None
    bot.token_counter = MagicMock()
    bot.cache = MagicMock()
    bot.cache.check.return_value = None
    bot.index = MagicMock()
    bot.index.as_retriever.return_value = MagicMock()
    bot.reranker = MagicMock()
    bot.llm = MagicMock()
    bot._log_query_details = MagicMock()

    async def _recuperar(retriever, pregunta):
        return nodos

    async def _memoria_vacia(user_id):
        return _memoria()

    async def _embedding(texto):
        return [0.1, 0.2, 0.3]

    async def _astream_chat(mensajes):
        async def _gen():
            for trozo in salida_del_modelo:
                yield types.SimpleNamespace(delta=trozo)
        return _gen()

    bot._recuperar_nodos = _recuperar
    bot._get_memory_for_user_async = _memoria_vacia
    bot.llm.astream_chat = _astream_chat
    monkeypatch.setattr(
        modulo_chatbot, "Settings",
        types.SimpleNamespace(embed_model=MagicMock(aget_query_embedding=_embedding)),
    )
    return bot


def _consultar(bot, texto):
    async def _correr():
        return "".join([t async for t in bot.stream_query(texto, 42, "voltara", "task-3")])

    return asyncio.run(_correr())


def test_la_negativa_del_modelo_no_llega_a_pantalla_y_sale_la_lista(monkeypatch):
    """Camino entero: el operador escribe "medidor", el modelo se niega y lo que ve es
    la lista de temas. La negación no se muestra ni un instante (se retuvo) y no se
    cachea, pero los tokens SÍ se cobran: esta vez el modelo se llamó."""
    import tiktoken

    bot = _bot_voltara(monkeypatch, NODOS_MEDIDOR, ["No encontré ", "información sobre ese tema."])
    respuesta = _consultar(bot, "medidor")

    assert desamb.INICIO in respuesta
    assert "no encontré" not in respuesta.lower()
    assert "Medidor Quemado" in respuesta
    bot.cache.save.assert_not_called()

    guardado = bot._log_query_details.call_args
    # Se cobra lo que generó el modelo (la negación), no la lista que se mostró.
    esperado = len(tiktoken.get_encoding("cl100k_base").encode(
        "No encontré información sobre ese tema."))
    assert guardado[0][7] == esperado


def test_el_reemplazo_no_le_esconde_el_hueco_a_calidad(monkeypatch):
    """EL test que hace seguro al reemplazo. El operador ve una lista de temas, pero
    el modelo se negó: la fila se sigue marcando sin cobertura sobre LO QUE EL MODELO
    ESCRIBIÓ, y el clasificador diferido decide. Sin esto, las 60 de cada 100
    negativas con score alto que son huecos de verdad dejarían de llegar a Calidad."""
    negativa = "No encontré información sobre ese tema."
    bot = _bot_voltara(monkeypatch, NODOS_MEDIDOR, [negativa])
    _consultar(bot, "medidor")

    guardado = bot._log_query_details.call_args
    assert guardado[1]["respuesta_modelo"] == negativa
    # Lo guardado es la lista (eso vio el operador)...
    assert desamb.es_desambiguacion(guardado[0][1])
    # ...y la cobertura se juzga sobre la negativa, que sí marca el candidato.
    assert evaluar_cobertura(guardado[1]["respuesta_modelo"], score_max=3.59)


def test_el_clasificador_lee_la_lista_como_lo_que_es(monkeypatch):
    """La fila llega al clasificador con la lista en `response`. Tal cual, la leería
    como una respuesta y la descartaría como falso positivo; se la encuadra."""
    from app.vacios_conocimiento import respuesta_para_clasificar

    lista = desamb.render(["Medidor Quemado", "Tapa de Medidor"])
    texto = respuesta_para_clasificar(lista)

    assert texto.startswith("El asistente NO respondió")
    assert "Medidor Quemado" in texto          # los temas ofrecidos siguen a la vista
    assert respuesta_para_clasificar("Se carga con motivo Comercial.") == (
        "Se carga con motivo Comercial.")      # una respuesta normal no se toca


def test_una_respuesta_de_verdad_pasa_entera_y_sin_tocar(monkeypatch):
    """El 97% de los turnos: el modelo responde y el freno no tiene que dejar nada en
    el camino ni cambiar una coma."""
    trozos = ["🖥️ **Acciones", " para el operador:**\n", "1. Verificá el medidor."]
    bot = _bot_voltara(monkeypatch, NODOS_MEDIDOR, trozos)

    assert _consultar(bot, "medidor quemado") == "".join(trozos)


def test_sin_temas_para_ofrecer_la_negativa_se_muestra_tal_cual(monkeypatch):
    """Si no hay de dónde sacar opciones (score bajo), el operador ve la frase y el
    hueco se registra: es exactamente el comportamiento anterior."""
    nodos = [_nodo(-6.0, "## Cambio de fase\ntexto", header_path="/Guía VOLTARA/")]
    bot = _bot_voltara(monkeypatch, nodos, [NEGACION])

    respuesta = _consultar(bot, "cambio de fase")
    assert respuesta == NEGACION
    assert evaluar_cobertura(respuesta, score_max=-6.0)
