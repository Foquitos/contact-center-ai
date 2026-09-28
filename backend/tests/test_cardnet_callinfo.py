"""
Regresión de la auditoría CSV diaria que tiraba carpetas enteras con
"list index out of range" (todos los días desde el 28/08/2026; el 15/09 fueron
'Audios 07-09 TT', 'Audios 10-9' y 'Audios 11-09 TM').

La cadena, reproducida contra el Yoizen real el 16/09:
  1. En el informe de Verint alcanza con UN segmento sin 'Agent ID' para que esa
     columna quede float.
  2. `iterrows` arma cada fila (UCID, Agent ID) como una Series de un solo dtype: el
     UCID también pasa a float y viaja como '1044221789171087.0'.
  3. CallInfo.aspx no encuentra esa llamada y devuelve la página con la tabla de datos
     vacía y SIN la de segmentos; `tables[1]` explota en la primera llamada y se cae la
     carpeta completa, que queda reintentándose (y fallando) cada madrugada.

De paso se cuida el cruce por agente: en una llamada transferida con dos audios, cada
audio tiene que quedar con el segmento de SU agente, no el del primero.

Test 100% offline: Yoizen y la hoja de internos son stubs.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from AuditorIA.Cardnet import generar_df_llamados_csv
from AuditorIA.downloads.Yoizen import Yoizen

LLAMADA_TRANSFERIDA = 1144631787267908
LLAMADA_SIN_AGENTE = 1144631787267999


def _carpeta(tmp_path):
    audios = tmp_path / "audios"
    audios.mkdir()
    for nombre in ("a.wav", "b.wav", "c.wav"):
        (audios / nombre).write_bytes(b"RIFF audio")

    # El tercer segmento no trae Agent ID: eso vuelve float toda la columna.
    informe = tmp_path / "ucids.csv"
    informe.write_text(
        "Full Name,Segment Start Time,Segment Stop Time,Segment Duration,Call ID,Agent ID,Extension,Dialed in number,Segment UCID\n"
        f"GOMEZ JUAN,20/08/2026 20:21:51,20/08/2026 20:23:07,00:01:16,1,606507,600171,669200,{LLAMADA_TRANSFERIDA}\n"
        f"PEREZ ANA,20/08/2026 20:23:10,20/08/2026 20:25:00,00:01:50,1,606508,600172,669200,{LLAMADA_TRANSFERIDA}\n"
        f"LOPEZ LUIS,20/08/2026 20:30:00,20/08/2026 20:31:00,00:01:00,2,,600173,669200,{LLAMADA_SIN_AGENTE}\n",
        encoding="UTF-16 LE",
    )

    saved = tmp_path / "savedFiles.txt"
    saved.write_text(
        "".join(
            f"File name: C:\\audios\\{archivo}\nCreated: 20/08/2026 20:40:00\n"
            f"Agent name\tStart time\n--------------------------------------------------\n{agente}\t{inicio}\n"
            for archivo, agente, inicio in (
                ("a.wav", "GOMEZ JUAN", "20/08/2026 20:21:51"),
                ("b.wav", "PEREZ ANA", "20/08/2026 20:23:10"),
                ("c.wav", "LOPEZ LUIS", "20/08/2026 20:30:00"),
            )
        ),
        encoding="utf-8",
    )
    return str(informe), str(audios), str(saved)


def _segmento(agente, ucid_segmento, talk):
    return {
        "Agente": float(agente),            # read_html lo deja en float
        "UCID": ucid_segmento, "Skill": 451, "Talk Time": talk, "Inicio": "20:21:00",
        "Duración": talk, "Origen": "1", "Destino": "669200", "CollectedDigits_y": None,
        "Tiempo en Cola": None, "Tiempo de Ring": "00:00:04", "Tiempo en ACW": None,
        "Tiempo en Hold": None, "Cantidad de Holds": 0, "UUI_y": None,
        "Finalizada por:": "Cliente", "Type_y": "Entrante",
    }


def _yoizen_como_el_real(pedidos):
    """CallInfo solo encuentra la llamada si el UCID llega como entero en texto."""
    api = MagicMock()
    api.Reporte_EHC.return_value = pd.DataFrame([
        {"UCID": u, "Type": "Entrante", "ANI": "1", "DNIS": "2", "CollectedDigits_x": None,
         "UUI_x": None, "Segments": 2, "Abandoned": False, "CustomerCode": None}
        for u in (LLAMADA_TRANSFERIDA, LLAMADA_SIN_AGENTE)
    ])

    def _callinfo(ucid):
        pedidos.append(ucid)
        if str(ucid) != str(LLAMADA_TRANSFERIDA):
            # Lo que hacía el código viejo con la página sin segmentos.
            raise IndexError("list index out of range")
        return pd.DataFrame([
            _segmento(606507, "1000000000000001", "00:01:10"),
            _segmento(606508, "1000000000000002", "00:01:40"),
        ])

    api.informacion_de_llamado.side_effect = _callinfo
    return api


def _correr(tmp_path, api):
    informe, audios, saved = _carpeta(tmp_path)
    internos = pd.DataFrame([{"VDN": 669200, "Nombre (tal cuál figura en Avaya)": "IPE", "IP con la que ingresa": ""}])
    with patch("AuditorIA.Cardnet.Yoizen") as clase, \
         patch("AuditorIA.Cardnet.sheet_internos", return_value=internos):
        clase.return_value.__enter__.return_value = api
        return generar_df_llamados_csv(ucid=informe, carpeta_audios=audios, savedFiles=saved)


def test_un_segmento_sin_agente_no_tira_la_carpeta(tmp_path):
    pedidos = []

    df = _correr(tmp_path, _yoizen_como_el_real(pedidos))

    # El UCID viaja como entero, nunca como '...908.0'.
    assert pedidos and all(str(p) == str(int(p)) for p in pedidos)
    # El audio sin agente se descarta; los otros dos se auditan.
    assert sorted(df["File name"]) == ["a.wav", "b.wav"]


def test_cada_audio_de_una_llamada_transferida_queda_con_su_agente(tmp_path):
    df = _correr(tmp_path, _yoizen_como_el_real([])).set_index("File name")

    assert df.loc["a.wav", "UCID_y"] == "1000000000000001"
    assert df.loc["b.wav", "UCID_y"] == "1000000000000002"
    assert df.loc["a.wav", "Agente"] == 606507
    assert df.loc["b.wav", "Agente"] == 606508


def test_una_llamada_que_falla_no_frena_a_las_demas(tmp_path):
    pedidos = []
    api = _yoizen_como_el_real(pedidos)
    original = api.informacion_de_llamado.side_effect

    def _falla_la_transferida(ucid):
        if str(ucid) == str(LLAMADA_SIN_AGENTE):
            raise RuntimeError("timeout")
        return original(ucid)

    api.informacion_de_llamado.side_effect = _falla_la_transferida

    df = _correr(tmp_path, api)

    assert sorted(df["File name"]) == ["a.wav", "b.wav"]


# --- Yoizen.informacion_de_llamado ------------------------------------------------

PAGINA_ENCONTRADA = """
<table id="tableCallInfo"><tr><td>Fecha:</td><td>15/09/2026</td></tr><tr><td>UCID:</td><td>111</td></tr></table>
<table><tr><th>#</th><th>Agente</th><th>UCID</th></tr><tr><td>1</td><td>682265</td><td>222</td></tr></table>
"""
PAGINA_VACIA = """
<table id="tableCallInfo"><tr><td>Fecha:</td><td></td></tr><tr><td>UCID:</td><td></td></tr></table>
"""


def _api_callinfo(pedidos):
    api = Yoizen.__new__(Yoizen)    # sin __init__: haría el login HTTP

    def _get(url, params=None, cookies=None):
        pedidos.append(params["id"])
        html = PAGINA_ENCONTRADA if params["id"] == "1044221789171087" else PAGINA_VACIA
        return SimpleNamespace(status_code=200, text=html)

    api.session = SimpleNamespace(get=_get, cookies={})
    return api


def test_callinfo_normaliza_el_ucid_float():
    pedidos = []

    df = _api_callinfo(pedidos).informacion_de_llamado(1044221789171087.0)

    assert pedidos == ["1044221789171087"]
    assert df is not None and len(df) == 1


def test_callinfo_sin_tabla_de_segmentos_devuelve_none():
    assert _api_callinfo([]).informacion_de_llamado(999) is None
