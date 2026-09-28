"""Benefix (Genesys Cloud): carga de llamados, login, audios y builder de auditorías.

Todo offline salvo la validación del SQL del builder, que usa la fixture `validar_sql`
(describe sin ejecutar). Mientras la migración 2026-09-21 no esté aplicada, el SQL se
valida contra una tabla de reemplazo con las mismas columnas y tipos.
"""
import datetime as dt
import importlib.util
import io
import json
import os
from datetime import datetime

import pandas as pd
import pytest

import AuditorIA.SQL_query as q
from AuditorIA.downloads import Genesys as G

RUTA = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "benefix_genesys.py"))


@pytest.fixture(scope="module")
def carga():
    spec = importlib.util.spec_from_file_location("benefix_genesys", RUTA)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# --------------------------------------------------------------------------- #
# Conversaciones de ejemplo (forma real de analytics, ids inventados)         #
# --------------------------------------------------------------------------- #
CATALOGOS = {
    "usuarios": {"u-acme": {"name": "ACME - Ana Perez", "email": "ana.perez@consultores.benefix.example"},
                 "u-benefix": {"name": "Lara Alonso", "email": "persona6@benefix.example"}},
    "colas": {"q-in": "Benefix", "q-out": "Saliente_Acme"},
    "tipificaciones": {"w-saldo": "P&B - Consulta de Saldo"},
    "skills": {"sk-1": "sk_Otras consultas"},
}


def _entrante(cid="c-1", inicio="2026-09-20T12:00:00.000Z"):
    return {
        "conversationId": cid,
        "conversationStart": inicio,
        "conversationEnd": "2026-09-20T12:06:00.000Z",
        "originatingDirection": "inbound",
        "externalTag": "1482000",
        "participants": [
            {"participantId": "p-cli", "purpose": "customer", "sessions": [{
                "mediaType": "voice", "direction": "inbound", "recording": True,
                "ani": "tel:+541140000000", "dnis": "tel:+541149091405", "segments": []}]},
            {"participantId": "p-ivr", "purpose": "ivr", "sessions": [{"mediaType": "voice", "segments": []}]},
            {"participantId": "p-ag", "purpose": "agent", "userId": "u-acme", "sessions": [{
                "mediaType": "voice", "direction": "inbound",
                "ani": "tel:+541140000000", "dnis": "tel:+541149091405",
                "metrics": [{"name": "tTalk", "value": 100}],
                "segments": [
                    {"segmentType": "alert", "queueId": "q-in", "requestedRoutingSkillIds": ["sk-1"],
                     "segmentStart": "2026-09-20T12:01:00.000Z", "segmentEnd": "2026-09-20T12:01:03.000Z"},
                    {"segmentType": "interact", "queueId": "q-in",
                     "segmentStart": "2026-09-20T12:01:03.000Z", "segmentEnd": "2026-09-20T12:03:03.000Z"},
                    {"segmentType": "hold", "queueId": "q-in",
                     "segmentStart": "2026-09-20T12:03:03.000Z", "segmentEnd": "2026-09-20T12:04:03.000Z"},
                    {"segmentType": "interact", "queueId": "q-in", "disconnectType": "peer",
                     "segmentStart": "2026-09-20T12:04:03.000Z", "segmentEnd": "2026-09-20T12:05:33.000Z"},
                    {"segmentType": "wrapup", "queueId": "q-in", "wrapUpCode": "w-saldo",
                     "wrapUpNote": "pidió saldo",
                     "segmentStart": "2026-09-20T12:05:33.000Z", "segmentEnd": "2026-09-20T12:05:43.000Z"},
                ]}]},
        ],
    }


def test_arma_un_tramo_por_agente_con_tiempos_y_catalogos(carga):
    filas = carga.filas_del_dia([_entrante()], dt.date(2026, 9, 20), CATALOGOS)
    assert len(filas) == 1
    f = dict(zip(carga.NOMBRES, filas[0]))
    # 12:00 UTC = 09:00 en Buenos Aires
    assert f["InicioConversacion"] == dt.datetime(2026, 9, 20, 9, 0, 0)
    assert f["InicioAgente"] == dt.datetime(2026, 9, 20, 9, 1, 0)
    assert f["Operador"] == "ACME - Ana Perez"
    assert f["Cola"] == "Benefix"
    assert f["Skills"] == "sk_Otras consultas"
    assert f["Tipificacion"] == "P&B - Consulta de Saldo"
    assert f["NotaWrapUp"] == "pidió saldo"
    assert (f["SegundosAlerta"], f["SegundosHablados"], f["SegundosEspera"], f["SegundosACW"]) == (3, 210, 60, 10)
    assert f["CantidadEsperas"] == 1
    assert f["SegundosManejo"] == 280
    assert f["DesconexionAgente"] == "peer"
    assert f["Sentido"] == "Entrante"
    assert f["TelefonoCliente"] == "+541140000000"
    assert f["Grabada"] is True and f["Transferido"] is False
    assert f["AgentesEnConversacion"] == 1
    assert f["ExternalTag"] == "1482000"


def test_asigna_la_conversacion_al_dia_local_de_su_inicio(carga):
    """Analytics devuelve también las que cruzan el borde del intervalo: una que empezó
    el día anterior (en hora local) no se carga en este."""
    anterior = _entrante("c-ayer", "2026-09-20T02:59:00.000Z")  # 23:59 del 19 en BA
    filas = carga.filas_del_dia([anterior, _entrante()], dt.date(2026, 9, 20), CATALOGOS)
    assert [f[0] for f in filas] == ["c-1"]


def test_sin_agente_no_hay_fila(carga):
    solo_ivr = _entrante("c-ivr")
    solo_ivr["participants"] = [p for p in solo_ivr["participants"] if p["purpose"] != "agent"]
    assert carga.filas_del_dia([solo_ivr], dt.date(2026, 9, 20), CATALOGOS) == []


def test_saliente_toma_el_telefono_del_dnis(carga):
    c = _entrante("c-out")
    ag = c["participants"][2]["sessions"][0]
    ag["direction"] = "outbound"
    ag["ani"], ag["dnis"] = "sip:agente@localhost", "tel:+541155555555"
    for sg in ag["segments"]:
        sg["queueId"] = "q-out"
    f = dict(zip(carga.NOMBRES, carga.filas_del_dia([c], dt.date(2026, 9, 20), CATALOGOS)[0]))
    assert f["Sentido"] == "Saliente"
    assert f["TelefonoCliente"] == "+541155555555"
    assert f["Cola"] == "Saliente_Acme"


def test_los_textos_se_recortan_al_largo_de_la_columna(carga):
    catalogos = dict(CATALOGOS, tipificaciones={"w-saldo": "x" * 500})
    f = dict(zip(carga.NOMBRES, carga.filas_del_dia([_entrante()], dt.date(2026, 9, 20), catalogos)[0]))
    assert len(f["Tipificacion"]) == 300


def test_el_dia_se_pide_en_hora_local(carga):
    desde, hasta = carga.intervalo_del_dia(dt.date(2026, 9, 20))
    assert desde.astimezone(dt.timezone.utc) == dt.datetime(2026, 9, 20, 3, 0, tzinfo=dt.timezone.utc)
    assert hasta - desde == dt.timedelta(days=1)


# --------------------------------------------------------------------------- #
# Login y audios (sin red: se reemplazan los pedidos HTTP)                     #
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, status, texto="", headers=None, js=None, content=b""):
        self.status_code, self.text, self.headers = status, texto, headers or {}
        self._js, self.content = js, content or (json.dumps(js).encode() if js is not None else texto.encode())

    def json(self):
        return self._js


class _SesionFalsa:
    def __init__(self, respuestas):
        self.respuestas, self.pedidos, self.headers = list(respuestas), [], {}

    def _siguiente(self, metodo, url, **kw):
        self.pedidos.append((metodo, url, kw))
        return self.respuestas.pop(0)

    def get(self, url, **kw):
        return self._siguiente("GET", url, **kw)

    def post(self, url, **kw):
        return self._siguiente("POST", url, **kw)

    def put(self, url, **kw):
        return self._siguiente("PUT", url, **kw)

    def close(self):
        pass


PAGINA_LOGIN = '<meta name="csrf" content="CSRF1">\n<meta name="requestId" content="RID1">'


def test_login_web_sigue_los_tres_pasos(monkeypatch):
    sesion = _SesionFalsa([
        _Resp(302, headers={"location": "/?rid=RID1#/"}),
        _Resp(200, PAGINA_LOGIN),
        _Resp(200, js={"userId": "u", "orgName": "edenredargentina"}),
        _Resp(200, js={"status": "approved",
                       "redirect": "https://apps.sae1.pure.cloud/directory/#access_token=TOK&expires_in=691199"}),
    ])
    monkeypatch.setattr(G.requests, "Session", lambda: sesion)
    token, vence = G.Genesys("u@x", "clave")._login_web()
    assert token == "TOK"
    assert vence > datetime.now(dt.timezone.utc) + dt.timedelta(days=7)
    metodo, url, kw = sesion.pedidos[2]
    assert url.endswith("/login")
    # el campo es `username`: con `email` Genesys contesta 401 como si la clave fuera mala
    assert json.loads(kw["data"])["username"] == "u@x"
    assert kw["headers"]["ININ-CSRF-TOKEN"] == "CSRF1"
    assert sesion.pedidos[3][1].endswith("/request/RID1")


def test_login_con_clave_mala_lo_dice(monkeypatch):
    sesion = _SesionFalsa([
        _Resp(302, headers={"location": "/?rid=RID1#/"}),
        _Resp(200, PAGINA_LOGIN),
        _Resp(401),
    ])
    monkeypatch.setattr(G.requests, "Session", lambda: sesion)
    with pytest.raises(G.ErrorGenesys, match="incorrectos"):
        G.Genesys("u@x", "mala")._login_web()


def test_sin_credenciales_no_arranca():
    with pytest.raises(G.ErrorGenesys, match="GENESYS_USER"):
        G.Genesys("", "")


def test_descargar_audio_espera_la_transcodificacion(monkeypatch):
    cli = G.Genesys("u@x", "clave")
    respuestas = [
        _Resp(202),
        _Resp(200, js=[
            {"media": "audio", "startTime": "2026-09-20T12:00:00Z", "endTime": "2026-09-20T12:00:10Z",
             "mediaUris": {"0": {"mediaUri": "https://corta"}}},
            {"media": "audio", "startTime": "2026-09-20T12:00:00Z", "endTime": "2026-09-20T12:05:00Z",
             "mediaUris": {"0": {"mediaUri": "https://larga"}}},
        ]),
    ]
    monkeypatch.setattr(cli, "_pedir", lambda *a, **k: respuestas.pop(0))
    monkeypatch.setattr(G.time, "sleep", lambda s: None)
    bajadas = []
    monkeypatch.setattr(G.requests, "get",
                        lambda url, **k: bajadas.append(url) or _Resp(200, content=b"RIFF" + b"0" * 2000))
    audio = cli.descargar_audio("c-1")
    assert isinstance(audio, io.BytesIO)
    assert bajadas == ["https://larga"]  # se queda con la grabación más larga


def test_descargar_audio_sin_grabacion_devuelve_none(monkeypatch):
    cli = G.Genesys("u@x", "clave")
    monkeypatch.setattr(cli, "_pedir", lambda *a, **k: _Resp(404))
    assert cli.descargar_audio("c-1") is None


# --------------------------------------------------------------------------- #
# Builder de auditorías                                                        #
# --------------------------------------------------------------------------- #
def test_lista_sql_escapa_apostrofos():
    assert q._lista_sql(["D'Alessandro", "Ana"]) == "N'D''Alessandro', N'Ana'"


class _SQLCapturado(Exception):
    def __init__(self, sql):
        self.sql = sql


def _capturar(monkeypatch, **kwargs) -> str:
    def fake_read_sql(query, *a, **k):
        raise _SQLCapturado(query)
    monkeypatch.setattr(pd, "read_sql", fake_read_sql)
    with pytest.raises(_SQLCapturado) as e:
        q.get_filtered_data_Benefix(engine=object(), **kwargs)
    return e.value.sql


# Espejo de Benefix.Interacciones (migración 2026-09-21) para validar el binding del SQL
# mientras la tabla no exista en la base.
TABLA_REEMPLAZO = """(SELECT
    CAST(NULL AS varchar(36)) AS ConversationId, CAST(NULL AS varchar(36)) AS ParticipantId,
    CAST(NULL AS date) AS Fecha, CAST(NULL AS datetime2(0)) AS InicioConversacion,
    CAST(NULL AS datetime2(0)) AS FinConversacion, CAST(NULL AS datetime2(0)) AS InicioAgente,
    CAST(NULL AS datetime2(0)) AS FinAgente, CAST(NULL AS varchar(20)) AS Canal,
    CAST(NULL AS varchar(10)) AS Sentido, CAST(NULL AS varchar(36)) AS UserId,
    CAST(NULL AS nvarchar(200)) AS Operador, CAST(NULL AS nvarchar(200)) AS Email,
    CAST(NULL AS varchar(36)) AS QueueId, CAST(NULL AS nvarchar(200)) AS Cola,
    CAST(NULL AS nvarchar(1000)) AS Skills, CAST(NULL AS varchar(36)) AS WrapUpCodeId,
    CAST(NULL AS nvarchar(300)) AS Tipificacion, CAST(NULL AS nvarchar(4000)) AS NotaWrapUp,
    CAST(NULL AS varchar(64)) AS TelefonoCliente, CAST(NULL AS varchar(200)) AS ANI,
    CAST(NULL AS varchar(200)) AS DNIS, CAST(NULL AS int) AS SegundosAlerta,
    CAST(NULL AS int) AS SegundosHablados, CAST(NULL AS int) AS SegundosEspera,
    CAST(NULL AS int) AS CantidadEsperas, CAST(NULL AS int) AS SegundosACW,
    CAST(NULL AS int) AS SegundosManejo, CAST(NULL AS bit) AS Transferido,
    CAST(NULL AS varchar(30)) AS DesconexionAgente, CAST(NULL AS bit) AS Grabada,
    CAST(NULL AS tinyint) AS AgentesEnConversacion, CAST(NULL AS datetime2(0)) AS CargadoEn,
    CAST(NULL AS varchar(64)) AS ExternalTag)"""


def test_la_tabla_de_reemplazo_tiene_las_columnas_que_carga_el_script(carga):
    for columna in carga.NOMBRES:
        assert f" AS {columna}," in TABLA_REEMPLAZO or f" AS {columna})" in TABLA_REEMPLAZO, columna


_DIR_MIGRACIONES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "migrations"))
MIGRACION = os.path.join(_DIR_MIGRACIONES, "2026-09-21_benefix_genesys.sql")
# Columnas que se agregaron después con ALTER TABLE ... ADD.
MIGRACIONES_ALTER = [os.path.join(_DIR_MIGRACIONES, "2026-09-21b_benefix_external_tag.sql")]


@pytest.mark.skipif(not all(os.path.exists(m) for m in [MIGRACION] + MIGRACIONES_ALTER),
                    reason="las migraciones no están en esta copia local")
def test_la_migracion_crea_las_columnas_que_carga_el_script(carga):
    """El INSERT del cargador y las migraciones tienen que nombrar las mismas columnas
    (con largos compatibles): si no, la primera corrida en prod revienta."""
    import re
    texto = open(MIGRACION, encoding="utf-8").read()
    create = texto[texto.index("CREATE TABLE Benefix.Interacciones"):texto.index("CONSTRAINT PK_BenefixInteracciones")]
    alters = "\n".join(open(m, encoding="utf-8").read() for m in MIGRACIONES_ALTER)
    for columna, largo in carga.COLUMNAS:
        m = (re.search(rf"^\s+{columna}\s+(\w+)(?:\((\d+)\))?", create, re.MULTILINE)
             or re.search(rf"\bADD\s+{columna}\s+(\w+)(?:\((\d+)\))?", alters))
        assert m, f"{columna} no está en la migración"
        if largo:
            assert m.group(2) and int(m.group(2)) == largo, f"{columna}: largo {m.group(2)} vs {largo}"


@pytest.fixture(scope="module")
def tabla_benefix(engine, carga):
    """True si la tabla real ya tiene todas las columnas que carga el script (o sea, si
    corrieron todas las migraciones); si no, el SQL se valida contra el reemplazo."""
    from sqlalchemy import text
    with engine.connect() as conn:
        existentes = {r[0] for r in conn.execute(text(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = 'Benefix' AND TABLE_NAME = 'Interacciones'")).fetchall()}
    return set(carga.NOMBRES) <= existentes


ESCENARIOS = {
    "minimo": dict(cantidad=1),
    "todos_los_filtros": dict(
        cantidad=5, Fecha_desde=datetime(2026, 9, 14), Fecha_hasta=datetime(2026, 9, 20),
        idInteraccion=["f0917fa7-7810-4d78-8ce5-fe5ef81dd67f"], loginid=["ACME - Ana D'Alessandro"],
        campana=["Benefix", "Saliente_Acme"], tipificacion=["P&B - Consulta de Saldo"],
        sentido=["Entrante", "Saliente", "Interno"], duracion_min=30, duracion_max=900,
        comentario=["saldo"]),
    "fecha_unica": dict(cantidad=1, Fecha_desde=datetime(2026, 9, 20)),
    "por_operador": dict(cantidad=3, por_operador=True),
    "por_tipificacion": dict(cantidad=3, por_tipificacion=True),
    "por_operador+tipificacion": dict(cantidad=3, por_operador=True, por_tipificacion=True),
    "reauditar": dict(cantidad=2, reauditar=True, loginid=["ACME - Ana Perez"]),
}


@pytest.mark.parametrize("nombre", list(ESCENARIOS))
def test_sql_del_builder_es_valido(nombre, monkeypatch, validar_sql, tabla_benefix):
    sql = _capturar(monkeypatch, **ESCENARIOS[nombre])
    assert sql.count("(") == sql.count(")")
    if not tabla_benefix:
        sql = sql.replace("[Acme].[Benefix].[Interacciones]", TABLA_REEMPLAZO)
    resultado = validar_sql(sql)
    assert resultado.ok, f"[{nombre}] {resultado.method}: {resultado.error}\n{sql}"


def test_el_builder_filtra_voz_grabada_de_acme_y_sin_auditar(monkeypatch):
    sql = _capturar(monkeypatch, cantidad=1)
    assert "i.[Canal] = 'voice'" in sql and "i.[Grabada] = 1" in sql
    assert q.BENEFIX_OPERADOR_ACME in sql
    assert "NOT EXISTS" in sql
    assert "NOT EXISTS" not in _capturar(monkeypatch, cantidad=1, reauditar=True)


def test_solo_interno_no_trae_nada(monkeypatch):
    """En Genesys no hay llamados internos: tildar solo "Interno" no puede ignorar el
    filtro y traer todo."""
    sql = _capturar(monkeypatch, cantidad=1, sentido=["Interno"])
    assert "i.[Sentido] IN" not in sql
    assert " AND 1 = 0" in sql


def test_interno_junto_a_otro_sentido_no_suma_nada(monkeypatch):
    sql = _capturar(monkeypatch, cantidad=1, sentido=["Entrante", "Interno"])
    assert "i.[Sentido] IN (N'Entrante')" in sql
    assert " AND 1 = 0" not in sql


def test_comentario_busca_en_lo_marcado_en_el_ivr(monkeypatch):
    sql = _capturar(monkeypatch, cantidad=1, comentario=["5117"])
    assert "CONCAT(i.[ExternalTag], N' ', i.[NotaWrapUp]) LIKE '%5117%'" in sql


# --------------------------------------------------------------------------- #
# externalTag: va a Extras, no al prompt                                       #
# --------------------------------------------------------------------------- #
def _fila_benefix() -> pd.DataFrame:
    return pd.DataFrame([{
        "ID": "c-1", "inicio": datetime(2026, 9, 20, 9, 0), "Sentido": "Entrante",
        "LoginId": "ACME - Ana Perez", "Cola": "Benefix", "Skill": "sk_Otras consultas",
        "Tipificación": "P&B - Consulta de Saldo", "Duracion": 210,
        "Motivo de finalización": "Cortó el cliente", "externalTag": "5117000000000000",
    }])


def test_external_tag_queda_en_extras():
    from Auditor import AuditorIA
    df = AuditorIA._estandarizar_columnas_sql(None, _fila_benefix())
    extras = json.loads(df.loc[0, "Extras"])
    assert extras["externalTag"] == "5117000000000000"
    assert extras["Cola"] == "Benefix"
    assert df.loc[0, "id_aplicativo"] == "c-1"
    assert df.loc[0, "operador_usuario"] == "ACME - Ana Perez"


def test_external_tag_no_viaja_a_gemini():
    """Puede ser el número de tarjeta: queda guardado en Extras pero no se le manda al modelo."""
    from AuditorIA.gemini import prompt_details
    bloque = prompt_details(_fila_benefix().iloc[0], "")
    assert "5117000000000000" not in bloque
    assert "externalTag" not in bloque
    assert "P&B - Consulta de Saldo" in bloque
