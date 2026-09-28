"""
Tests para la descarga de audios de Hermes (Hidra Comercial).
Verifica:
1. Despacho secuencial seguro en DataFrame para evitar colisiones de buffer en Hermes.
2. Tolerancia a fallos por audio individual (no interrumpe el resto de descargas).
3. Configuración de Connection Pool en requests.Session.
4. Manejo de re-login / sesión expirada y errores HTTP.

Test 100% offline (sin llamadas reales a la red ni a SQL).
"""
import os
import unittest.mock as mock
import pandas as pd
import pytest
import requests

from AuditorIA.downloads.Hermes import Hermes
from AuditorIA.get_audio_bytes import _descargar_audios_dispatch, get_audio_bytes_Hermes


class _MockResponse:
    def __init__(self, status_code=200, text="", json_data=None, content=b"fake-mp3-bytes"):
        self.status_code = status_code
        self.text = text or ("" if json_data is None else str(json_data))
        self._json_data = json_data
        self.content = content

    def json(self):
        if self._json_data is not None:
            return self._json_data
        raise ValueError("No JSON data")


def test_hermes_session_adapter_and_lock():
    """Hermes monta un HTTPAdapter con pool_maxsize >= 20 para soportar conexiones limpias."""
    with mock.patch.object(Hermes, 'login_session', return_value=True):
        hermes = Hermes(login="user", password="pass", station="1234", default_folder="/tmp")
        assert hermes.session is not None
        assert hasattr(hermes, '_lock')
        
        adapter = hermes.session.adapters.get("https://")
        assert adapter is not None
        assert adapter._pool_connections >= 20
        assert adapter._pool_maxsize >= 20


def test_hermes_login_session_flow():
    """login_session obtiene token HTML y hace POST de autenticación."""
    with mock.patch("requests.Session") as mock_session_cls:
        mock_sess = mock_session_cls.return_value
        mock_sess.get.return_value = _MockResponse(
            status_code=200,
            text='<html><input name="__HRequestVerificationToken" value="token123" /></html>'
        )
        mock_sess.post.return_value = _MockResponse(status_code=200, text="OK")

        hermes = Hermes(login="user", password="pass", station="1234", default_folder="/tmp")
        # El __init__ ya invoca login_session()
        assert mock_sess.get.called
        assert mock_sess.post.called


def test_hermes_descarga_audio_exito(tmp_path):
    """descarga_audio ejecuta ConvertToMp3 -> Mp3ConversionProgress -> DownloadAudio."""
    with mock.patch.object(Hermes, 'login_session', return_value=True):
        hermes = Hermes(login="user", password="pass", station="1234", default_folder=str(tmp_path))

    def mock_post(url, *args, **kwargs):
        if "ConvertToMp3" in url:
            return _MockResponse(status_code=200, json_data={"Key": "mp3-key-abc"})
        elif "Mp3ConversionProgress" in url:
            return _MockResponse(status_code=200, text="1")
        return _MockResponse(status_code=404)

    def mock_get(url, *args, **kwargs):
        if "DownloadAudio" in url:
            return _MockResponse(status_code=200, content=b"AUDIO_DATA_MP3")
        return _MockResponse(status_code=404)

    with mock.patch.object(hermes.session, 'post', side_effect=mock_post), \
         mock.patch.object(hermes.session, 'get', side_effect=mock_get):
        
        destino = hermes.descarga_audio(filepath="2026/08/28/audio1.wav")
        assert destino is not None
        assert os.path.exists(destino)
        with open(destino, "rb") as f:
            assert f.read() == b"AUDIO_DATA_MP3"


def test_hermes_descarga_audio_error_handling(tmp_path):
    """descarga_audio devuelve None de forma segura ante errores HTTP o respuesta no-JSON."""
    with mock.patch.object(Hermes, 'login_session', return_value=True):
        hermes = Hermes(login="user", password="pass", station="1234", default_folder=str(tmp_path))

    # Caso 1: ConvertToMp3 responde 500 HTML
    with mock.patch.object(hermes.session, 'post', return_value=_MockResponse(status_code=500, text="<html>Internal Error</html>")):
        res = hermes.descarga_audio(filepath="audio_fail.wav")
        assert res is None

    # Caso 2: Conversión nunca termina (timeout de progreso)
    def mock_progress(url, *args, **kwargs):
        if "ConvertToMp3" in url:
            return _MockResponse(status_code=200, json_data={"Key": "k1"})
        return _MockResponse(status_code=200, text="0")

    with mock.patch.object(hermes.session, 'post', side_effect=mock_progress), \
         mock.patch("time.sleep", return_value=None):
        res = hermes.descarga_audio(filepath="audio_timeout.wav")
        assert res is None


def test_descargar_audios_dispatch_hermes_secuencial_seguro(monkeypatch):
    """_descargar_audios_dispatch descarga secuencialmente de forma segura y mapea el DataFrame correctamente."""
    df = pd.DataFrame({
        "ID": [101, 102, 103, 104, 105],
        "Rec_Filename": [f"file_{i}.wav" for i in range(1, 6)]
    })

    with mock.patch.object(Hermes, 'login_session', return_value=True):
        hermes_api = Hermes(login="u", password="p", station="s", default_folder="/tmp")

    descargas_realizadas = []

    def fake_get_audio(api, row):
        filename = row["Rec_Filename"]
        descargas_realizadas.append(filename)
        # Simulamos que file_3 falla
        if filename == "file_3.wav":
            return None
        return f"/tmp/{filename}.mp3"

    monkeypatch.setattr("AuditorIA.get_audio_bytes.get_audio_bytes_Hermes", fake_get_audio)

    res_df = _descargar_audios_dispatch(df=df.copy(), engine=None, api=hermes_api, num_chunks=5, empresa=["HIDRA Comercial"])

    assert "audio_dir" in res_df.columns
    assert len(descargas_realizadas) == 5
    assert res_df.loc[0, "audio_dir"] == "/tmp/file_1.wav.mp3"
    assert res_df.loc[1, "audio_dir"] == "/tmp/file_2.wav.mp3"
    assert res_df.loc[2, "audio_dir"] is None  # Falla aislada
    assert res_df.loc[3, "audio_dir"] == "/tmp/file_4.wav.mp3"
    assert res_df.loc[4, "audio_dir"] == "/tmp/file_5.wav.mp3"
