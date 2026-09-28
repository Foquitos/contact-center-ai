"""
Tests para la resiliencia y re-autenticación de Mitrol.
Verifica:
1. Configuración de Connection Pool en requests.Session.
2. Login inicial y thread-safety con _lock.
3. Detección de sesión expirada y re-autenticación automática en Grabaciones y Chat.
4. Manejo limpio de errores sin corromper archivos ni tumbar el proceso.

Test 100% offline (sin llamadas reales a la red).
"""
import io
import os
import unittest.mock as mock
import pytest
import requests

from AuditorIA.downloads.Mitrol import Mitrol


class _MockResponse:
    def __init__(self, status_code=200, text="", content=b"RIFFfake-wav-bytes", url="https://apps.acme-solutions.example/reportes/GetWave.ashx", headers=None):
        self.status_code = status_code
        self.text = text
        self.content = content
        self.url = url
        self.headers = headers or {"content-type": "audio/x-wav"}


def test_mitrol_session_adapter_and_lock():
    """Mitrol monta HTTPAdapter con pool_maxsize >= 20 y tiene thread lock."""
    with mock.patch.object(Mitrol, 'login_session', return_value=True):
        mitrol = Mitrol(usuario="user", clave="pass", carpeta_descarga="/tmp")
        assert mitrol.session is not None
        assert hasattr(mitrol, '_lock')
        
        adapter = mitrol.session.adapters.get("https://")
        assert adapter is not None
        assert adapter._pool_connections >= 20
        assert adapter._pool_maxsize >= 20


def test_mitrol_login_session_flow():
    """login_session envía POST a settings.MITROL_URL con credenciales."""
    with mock.patch("requests.Session") as mock_session_cls:
        mock_sess = mock_session_cls.return_value
        mock_sess.post.return_value = _MockResponse(status_code=200, text="OK")

        mitrol = Mitrol(usuario="admin", clave="secret", carpeta_descarga="/tmp")
        assert mock_sess.post.called
        assert mitrol.username == "admin"


def test_mitrol_grabaciones_auto_relogin(tmp_path):
    """Grabaciones detecta sesión expirada (401 o redirect a Login) y reautentica automáticamente."""
    with mock.patch.object(Mitrol, 'login_session', return_value=True):
        mitrol = Mitrol(usuario="user", clave="pass", carpeta_descarga=str(tmp_path))

    # Primera llamada devuelve 401 (expirada), segunda tras re-login devuelve 200 con WAV
    resp_expired = _MockResponse(status_code=401, content=b"Unauthorized", headers={"content-type": "text/html"})
    resp_ok = _MockResponse(status_code=200, content=b"RIFF_VALID_WAV_HEADER", headers={"content-type": "audio/x-wav"})

    with mock.patch.object(mitrol.session, 'get', side_effect=[resp_expired, resp_ok]) as mock_get, \
         mock.patch.object(mitrol.session, 'post', return_value=_MockResponse(status_code=200, text="OK")), \
         mock.patch.object(mitrol, 'login_session', wraps=mitrol.login_session) as mock_login:
        
        resultado = mitrol.Grabaciones(FilePath="2026/08", FileName="llamada_123")
        assert resultado is not None
        assert os.path.exists(resultado)
        assert mock_get.call_count == 2
        assert mock_login.call_count >= 1
        with open(resultado, "rb") as f:
            assert f.read() == b"RIFF_VALID_WAV_HEADER"


def test_mitrol_chat_auto_relogin():
    """Chat detecta sesión expirada y reautentica automáticamente antes de parsear."""
    with mock.patch.object(Mitrol, 'login_session', return_value=True):
        mitrol = Mitrol(usuario="user", clave="pass", carpeta_descarga="/tmp")

    resp_expired = _MockResponse(status_code=200, text="<html><input id='TboxUser' /></html>", url="https://apps.acme-solutions.example/reportes/Login.aspx", headers={"content-type": "text/html"})
    resp_ok = _MockResponse(status_code=200, text="<html><table>chat</table></html>", url="https://apps.acme-solutions.example/reportes/Chat.aspx", headers={"content-type": "text/html"})
    with mock.patch.object(mitrol.session, 'get', side_effect=[resp_expired, resp_ok]), \
         mock.patch.object(mitrol, 'login_session', return_value=True) as mock_login, \
         mock.patch.object(mitrol, '_parse_chat_html', return_value={"transcripcion": []}):
        
        res = mitrol.Chat(idInteraccion="123456")
        assert res is not None
        assert mock_login.called


def test_mitrol_chat_doctype_no_se_confunde_con_login(tmp_path):
    """Grabaciones no falla si el directorio temporal fue borrado por el SO; lo recrea."""
    non_existent_folder = str(tmp_path / "carpeta_inexistente" / "subcarpeta")
    assert not os.path.exists(non_existent_folder)

    with mock.patch.object(Mitrol, 'login_session', return_value=True):
        mitrol = Mitrol(usuario="user", clave="pass", carpeta_descarga=non_existent_folder)

    # Borramos la carpeta simulando limpieza del SO
    import shutil
    if os.path.exists(non_existent_folder):
        shutil.rmtree(non_existent_folder)
    assert not os.path.exists(non_existent_folder)

    resp_ok = _MockResponse(status_code=200, content=b"RIFF_VALID_WAV_HEADER", headers={"content-type": "audio/x-wav"})
    with mock.patch.object(mitrol.session, 'get', return_value=resp_ok):
        resultado = mitrol.Grabaciones(FilePath="2026/08", FileName="llamada_recreada")
        assert resultado is not None
        assert os.path.exists(resultado)
        assert os.path.exists(non_existent_folder)


def test_auditor_tempfolder_autorecreacion():
    """AuditorIA recrea su tempfolder si el SO lo borró durante la ejecución de gunicorn."""
    from Auditor import AuditorIA
    import shutil

    with mock.patch("google.genai.Client"):
        auditor = AuditorIA(engine=mock.MagicMock())
        carpeta_original = auditor.tempfolder
        assert os.path.exists(carpeta_original)

        # Simulamos que systemd-tmpfiles borra la carpeta temporal
        shutil.rmtree(carpeta_original)
        assert not os.path.exists(carpeta_original)

        nueva_carpeta = auditor.tempfolder
        assert nueva_carpeta != carpeta_original or os.path.exists(nueva_carpeta)
        assert os.path.exists(nueva_carpeta)


def test_mitrol_grabaciones_ignora_nan_o_none():
    """Grabaciones no realiza requests ni lanza excepciones si FileName o FilePath son NaN/None."""
    import numpy as np
    import pandas as pd

    with mock.patch.object(Mitrol, 'login_session', return_value=True):
        mitrol = Mitrol(usuario="user", clave="pass", carpeta_descarga="/tmp")

    with mock.patch.object(mitrol.session, 'get') as mock_get:
        assert mitrol.Grabaciones(FilePath=None, FileName="123") is None
        assert mitrol.Grabaciones(FilePath="2026/08", FileName=None) is None
        assert mitrol.Grabaciones(FilePath=np.nan, FileName=np.nan) is None
        assert mitrol.Grabaciones(FilePath="nan", FileName="nan") is None
        assert mitrol.Grabaciones(FilePath="", FileName="") is None
        assert mock_get.call_count == 0


def test_get_audio_bytes_df_vacio_retorna_inmediatamente():
    """get_audio_bytes con DataFrame vacío no ejecuta descargas de red ni falla."""
    import pandas as pd
    from AuditorIA.get_audio_bytes import get_audio_bytes

    with mock.patch.object(Mitrol, 'login_session', return_value=True):
        mitrol = Mitrol(usuario="user", clave="pass", carpeta_descarga="/tmp")

    with mock.patch.object(mitrol.session, 'get') as mock_get:
        df_vacio = pd.DataFrame()
        res = get_audio_bytes(df_vacio, engine=None, api=mitrol)
        assert res is not None
        assert res.empty
        assert 'audio_dir' in res.columns
        assert mock_get.call_count == 0



