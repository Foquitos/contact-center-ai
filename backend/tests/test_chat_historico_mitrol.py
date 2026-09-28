"""
Chats de Mitrol (Odonto Plus / Facebook / WhatsApp): armado del JSON que se le
adjunta a la IA para auditar un chat.

Lo que se valida acá es la atribución de cada mensaje, que es de donde salen las
notas: si el mensaje del operador se le adjudica al cliente (o al revés), la
auditoría califica a la persona equivocada. El HTML de GetChat.aspx no marca el
emisor por mensaje; hay que deducirlo del encabezado de cada segmento, que puede
venir con el operador de un lado o del otro.

No toca la red ni la base: el HTML es un fixture y `Chat`/`pd.read_sql` se
interceptan. La única prueba que necesita SQL Server valida la sintaxis de la
consulta del historial sin ejecutarla (ver conftest.py -> validar_sql).
"""
import json

import pandas as pd
import pytest

import AuditorIA.Trancribir as tr
from AuditorIA.downloads.Mitrol import Mitrol


# --------------------------------------------------------------------------- #
# Fixture de HTML: reproduce la estructura real de GetChat.aspx               #
# --------------------------------------------------------------------------- #
def _encabezado(segmento: str) -> str:
    return (
        '<tr style="background-color:#CCCCFF;"><td colspan="2">'
        f'{segmento}</td></tr>'
    )


def _partes(izquierda: str, derecha: str) -> str:
    return (
        f'<tr><td style="width:50%;background-color:#D8D8D8;">{izquierda}</td>'
        f'<td style="width:50%;background-color:#D62329;text-align:right;">{derecha}</td></tr>'
    )


def _evento(texto: str) -> str:
    return f'<tr><td colspan="2" style="text-align:center;color:#B0B0B0;">{texto}</td></tr>'


def _burbuja(texto: str, hora: str, derecha: bool) -> str:
    alineacion = 'right' if derecha else 'left'
    color = '#D62329' if derecha else '#D8D8D8'
    return (
        f'<tr style="text-align:{alineacion};"><td colspan="2">'
        f'<div style="display:inline-block;background-color:{color};border-radius: 15px;">'
        f'{texto}<span style="font-size:0.8em;"> {hora}</span></div></td></tr>'
    )


ID = "260804122328859_CHT_23000"

# Segmento 1: IVR a la izquierda, cliente a la derecha.
# Segmento 2: cliente a la izquierda, operador a la derecha.
# Segmento 3: operador a la izquierda y cliente a la derecha (Mitrol da vuelta los
#             lados de un segmento a otro: por eso no alcanza con la alineación).
# Segmento 4: el encabezado nombra a un operador distinto del que atendió según la
#             base; ahí Mitrol manda todas las burbujas al mismo lado y el emisor
#             real es indistinguible.
HTML = (
    "<html><body>"
    "<table>"
    + _encabezado(f"{ID}:1")
    + _partes("piloto_whatsappDental [ Normal / Ivr ]", "Daniela_5491100000002 [ Normal / Client ]")
    + _evento("Nuevo chat: piloto_whatsappDental 04/08/2026 12:23:28")
    + _burbuja("Turnos", "12:23:28", derecha=True)
    + _burbuja("¡Hola! ¿En qué te puedo ayudar?", "12:23:30", derecha=False)
    + "</table>"
    "<table>"
    + _encabezado(f"{ID}:2")
    + _partes("Daniela_5491100000002 [ Normal / Client ]", "Valeria Torres [ Normal / Agent ]")
    + _evento("Transferido a: Valeria Torres desde: piloto_whatsappDental 04/08/2026 13:34:38")
    + _burbuja("Necesito un turno", "13:35:00", derecha=False)
    + _burbuja("Te ayudo con eso", "13:35:20", derecha=True)
    + "</table>"
    "<table>"
    + _encabezado(f"{ID}:3")
    + _partes("Valeria Torres [ Normal / Agent ]", "Daniela_5491100000002 [ Normal / Client ]")
    + _burbuja("Dale para el 13/08", "15:34:55", derecha=True)
    + _burbuja("¡Genial! Te esperamos el 13/08", "15:35:10", derecha=False)
    + "</table>"
    "<table>"
    + _encabezado(f"{ID}:4")
    + _partes("Daniela_5491100000002 [ Normal / Client ]", "RODRIGO BLANCO [ Normal / Agent ]")
    + _burbuja("¿Te parece bien?", "15:40:00", derecha=False)
    + _burbuja("Si si", "15:40:30", derecha=False)
    + "</table>"
    "</body></html>"
)

# Operador real de cada segmento según [Nombre Agente] de la base.
OPERADORES = {2: "Valeria Torres", 3: "Valeria Torres", 4: "Lucia Vidal"}


@pytest.fixture(scope="module")
def api() -> Mitrol:
    """Instancia sin __init__: no hace login ni sale a la red."""
    return Mitrol.__new__(Mitrol)


@pytest.fixture(scope="module")
def chat(api) -> dict:
    return api._parse_chat_html(
        HTML,
        segmento_auditado=3,
        operador_auditado="VALERIA TORRES",  # la base lo escribe en mayúsculas
        operadores_por_segmento=OPERADORES,
    )


def _mensajes(chat: dict, segmento: int) -> list:
    return [m for m in chat["transcripcion"] if m["tipo"] == "mensaje" and m["segmento"] == segmento]


# --------------------------------------------------------------------------- #
# Atribución de los mensajes                                                   #
# --------------------------------------------------------------------------- #
def test_el_lado_del_operador_cambia_segun_el_segmento(chat):
    """El mismo lado (derecha) es el cliente en un segmento y el operador en otro."""
    seg2 = _mensajes(chat, 2)
    assert [(m["quien"], m["texto"]) for m in seg2] == [
        ("CLIENTE", "Necesito un turno"),
        ("OPERADOR", "Te ayudo con eso"),
    ]

    seg3 = _mensajes(chat, 3)
    assert [(m["quien"], m["texto"]) for m in seg3] == [
        ("CLIENTE", "Dale para el 13/08"),
        ("OPERADOR", "¡Genial! Te esperamos el 13/08"),
    ]


def test_el_bot_no_se_confunde_con_el_operador(chat):
    seg1 = _mensajes(chat, 1)
    assert [m["quien"] for m in seg1] == ["CLIENTE", "BOT_IVR"]
    assert chat["cliente"] == "Daniela"  # sin el id de la red social


def test_marca_los_mensajes_del_operador_auditado(chat):
    """Sin la marca, la IA no sabe cuál de los operadores del chat está evaluando."""
    auditados = [m for m in chat["transcripcion"] if m.get("es_operador_auditado")]
    assert [m["texto"] for m in auditados] == ["Te ayudo con eso", "¡Genial! Te esperamos el 13/08"]
    # Ningún mensaje del cliente puede quedar marcado como del operador.
    assert all(m["quien"] == "OPERADOR" for m in auditados)


def test_tramo_sin_emisor_confiable_queda_indeterminado(chat):
    """Encabezado con un operador que no es el que atendió: Mitrol manda todo al
    mismo lado y atribuirlo sería inventar."""
    seg4 = _mensajes(chat, 4)
    assert [m["quien"] for m in seg4] == ["INDETERMINADO", "INDETERMINADO"]
    assert all(not m["es_operador_auditado"] for m in seg4)
    assert chat["segmentos_con_emisor_indeterminado"] == [4]

    avisos = [m for m in chat["transcripcion"] if m["tipo"] == "aviso"]
    assert len(avisos) == 1 and avisos[0]["segmento"] == 4
    assert "Lucia Vidal" in avisos[0]["texto"]


def test_sin_datos_de_la_base_no_se_inventan_indeterminados(api):
    """Si no se sabe quién atendió cada segmento, se respeta el encabezado."""
    chat = api._parse_chat_html(HTML)
    assert chat["segmentos_con_emisor_indeterminado"] == []
    assert all(m["quien"] != "INDETERMINADO" for m in chat["transcripcion"] if m["tipo"] == "mensaje")
    assert not any(m.get("es_operador_auditado") for m in chat["transcripcion"] if m["tipo"] == "mensaje")


# --------------------------------------------------------------------------- #
# Armado del historial                                                         #
# --------------------------------------------------------------------------- #
GESTIONES = pd.DataFrame([
    # Chat viejo del mismo cliente (contexto).
    {"idInteraccion": "260803155658025_CHT_23000", "Segmento": 2, "Inicio": pd.Timestamp("2026-08-03 15:56:56"),
     "LoginId": "fg0003", "NombreAgente": "Fabian Gimenez", "Tipificacion": "No responde", "Cliente": "5491100000001_5491100000002"},
    # Chat auditado: tres gestiones, cada una con su operador y su tipificación.
    {"idInteraccion": ID, "Segmento": 2, "Inicio": pd.Timestamp("2026-08-04 13:34:36"),
     "LoginId": "vt0001", "NombreAgente": "Valeria Torres", "Tipificacion": "Transferido-", "Cliente": "5491100000001_5491100000002"},
    {"idInteraccion": ID, "Segmento": 3, "Inicio": pd.Timestamp("2026-08-04 15:19:55"),
     "LoginId": "vt0001", "NombreAgente": "Valeria Torres", "Tipificacion": "Obtuvo turno -> Primera vez", "Cliente": "5491100000001_5491100000002"},
    {"idInteraccion": ID, "Segmento": 4, "Inicio": pd.Timestamp("2026-08-04 15:30:00"),
     "LoginId": "lv0002", "NombreAgente": "Lucia Vidal", "Tipificacion": "No Disp.", "Cliente": "5491100000001_5491100000002"},
])


@pytest.fixture
def historial(api, monkeypatch) -> dict:
    """`Chat_historico` con la consulta y la descarga del HTML interceptadas."""
    monkeypatch.setattr(
        Mitrol, "_gestiones_del_historial",
        lambda self, engine, idInteraccion, dias_atras: GESTIONES.copy(),
    )
    monkeypatch.setattr(
        Mitrol, "Chat",
        lambda self, idInteraccion, **kw: self._parse_chat_html(HTML, **kw),
    )
    return api.Chat_historico(engine=None, idInteraccion=ID, segmento=3)


def test_identifica_la_gestion_que_se_audita(historial):
    """La ficha tiene que decir a quién se audita y con qué tipificación cerró."""
    assert historial["gestion_a_auditar"] == {
        "idInteraccion": ID,
        "segmento": 3,
        "operador_a_auditar": "Valeria Torres",
        "loginId": "vt0001",
        "tipificacion": "Obtuvo turno -> Primera vez",
        "cliente": "5491100000001_5491100000002",
        "inicio": "2026-08-04 15:19:55",
    }


def test_cada_gestion_lleva_su_operador_y_su_tipificacion(historial):
    auditada = [c for c in historial["conversaciones"] if c["es_la_conversacion_a_auditar"]][0]
    assert [(g["segmento"], g["operador"], g["tipificacion"], g["es_la_gestion_a_auditar"]) for g in auditada["gestiones"]] == [
        (2, "Valeria Torres", "Transferido-", False),
        (3, "Valeria Torres", "Obtuvo turno -> Primera vez", True),
        (4, "Lucia Vidal", "No Disp.", False),
    ]
    assert auditada["gestiones"][2]["emisor_de_los_mensajes"] == "indeterminado"


def test_el_historial_llega_en_orden_y_sin_repetir_conversaciones(historial):
    """Cada chat se baja una sola vez, aunque tenga varios segmentos en la base."""
    ids = [c["idInteraccion"] for c in historial["conversaciones"]]
    assert ids == ["260803155658025_CHT_23000", ID]
    assert historial["ventana_historial"]["dias_hacia_atras"] == 14
    assert historial["ventana_historial"]["conversaciones_incluidas"] == 2


def test_solo_se_marca_como_auditada_la_conversacion_correspondiente(historial):
    """En los chats de contexto no puede haber mensajes marcados como auditados."""
    contexto = [c for c in historial["conversaciones"] if not c["es_la_conversacion_a_auditar"]]
    assert contexto, "el historial tiene que traer chats previos del mismo cliente"
    for conversacion in contexto:
        assert not any(m.get("es_operador_auditado") for m in conversacion["transcripcion"])


# --------------------------------------------------------------------------- #
# Lectura del chat desde la plataforma (calidad.transcripciones)               #
# --------------------------------------------------------------------------- #
# Los chats no se transcriben, así que nunca llegaban a calidad.transcripciones y no
# se podían leer en "Auditorías Realizadas". Ahora se guardan con el mismo formato de
# `segments` que una transcripción, para que el modal los muestre igual.
def test_los_segments_del_chat_identifican_a_cada_hablante(historial):
    _, segments = tr.chat_a_segments(historial)

    roles = {(s["role"], s["speakerLabel"]) for s in segments}
    assert ("agente", "Agente auditado (Valeria Torres)") in roles
    assert ("cliente", "Cliente (Daniela)") in roles
    assert ("bot", "Bot / IVR (piloto_whatsappDental)") in roles
    assert ("indeterminado", "Sin identificar") in roles


def test_el_chat_auditado_se_distingue_del_historial(historial):
    metadata, segments = tr.chat_a_segments(historial)

    encabezados = [s["text"][0] for s in segments if s["role"] == "sistema"]
    assert any(t.startswith("CHAT AUDITADO") and "Valeria Torres" in t for t in encabezados)
    assert any(t.startswith("CHAT PREVIO DEL MISMO CLIENTE") for t in encabezados)
    assert metadata["origen"] == "chat"
    assert metadata["dias_de_historial"] == 14


def test_solo_se_guardan_las_filas_que_son_chats(api, monkeypatch, historial, tmp_path):
    """Un audio no tiene que terminar en la tabla de transcripciones por esta vía."""
    ruta_chat = tmp_path / "chat.json"
    ruta_chat.write_text(json.dumps(historial, ensure_ascii=False), encoding="utf-8")

    df = pd.DataFrame([
        {"idInteraccion": ID, "Segmento": 3, "audio_dir": str(ruta_chat)},
        {"idInteraccion": "260805100000000_CHT_23000", "Segmento": 2, "audio_dir": str(tmp_path / "audio.wav")},
    ])

    guardado = {}
    monkeypatch.setattr(tr, "ids_con_transcripcion", lambda engine, ids: set())
    monkeypatch.setattr(
        pd.DataFrame, "to_sql",
        lambda self, name, *a, **k: guardado.update({"tabla": name, "filas": self.to_dict("records")}),
    )

    assert tr.guardar_chats_como_transcripcion(engine=None, df=df, user_id=7) == 1
    assert guardado["tabla"] == "transcripciones"
    fila = guardado["filas"][0]
    assert fila["IdAplicativo"] == f"{ID}_3"
    assert fila["input_tokens"] == 0 and fila["output_tokens"] == 0  # leer un chat no gasta IA
    assert fila["user_id"] == 7
    assert json.loads(fila["segments"])[0]["role"] == "sistema"


def test_no_se_duplica_el_chat_ya_guardado(api, monkeypatch, historial, tmp_path):
    ruta_chat = tmp_path / "chat.json"
    ruta_chat.write_text(json.dumps(historial, ensure_ascii=False), encoding="utf-8")
    df = pd.DataFrame([{"idInteraccion": ID, "Segmento": 3, "audio_dir": str(ruta_chat)}])

    monkeypatch.setattr(tr, "ids_con_transcripcion", lambda engine, ids: {f"{ID}_3"})
    monkeypatch.setattr(
        pd.DataFrame, "to_sql",
        lambda *a, **k: pytest.fail("no debería insertar un chat ya guardado"),
    )

    assert tr.guardar_chats_como_transcripcion(engine=None, df=df) == 0


# --------------------------------------------------------------------------- #
# SQL del historial (no se ejecuta: solo se valida contra SQL Server)          #
# --------------------------------------------------------------------------- #
def test_sql_del_historial_es_valido(api, monkeypatch, validar_sql):
    class _Capturado(Exception):
        def __init__(self, sql):
            self.sql = sql

    monkeypatch.setattr(pd, "read_sql", lambda query, *a, **k: (_ for _ in ()).throw(_Capturado(query)))
    try:
        api._gestiones_del_historial(object(), ID, 14)
    except _Capturado as e:
        sql = e.sql
    else:
        raise AssertionError("_gestiones_del_historial no generó SQL")

    resultado = validar_sql(sql)
    assert resultado, f"SQL inválido: {resultado}"
