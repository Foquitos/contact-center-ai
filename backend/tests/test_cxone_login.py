"""
Regresión del login de CXOne que se caía todos los días a las 08:00 y a las 11:00.

Dos tareas programadas de ALARMIX arrancan juntas y cada una abre su propio login de
Microsoft con MFA por TOTP. Había dos problemas encadenados:

  1. Los dos logins pedían el código en la misma ventana de 30 s. Microsoft no acepta
     dos veces el mismo código, así que el segundo quedaba trabado en "Enter code".
  2. Los reintentos no servían: se reemplazaba `self.driver` pero `self.wait` seguía
     atado al Chrome cerrado, y el 2.º y 3.º intento fallaban en un segundo con
     "Connection refused" contra el puerto viejo.

Test 100% offline: no abre Chrome ni habla con Microsoft.
"""
from types import SimpleNamespace

import pyotp
import pytest

from AuditorIA.downloads import CXOne as cxone_mod
from AuditorIA.downloads.driver_selenium import driver as DriverBase

SECRETO = pyotp.random_base32()


@pytest.fixture
def reloj(monkeypatch, tmp_path):
    """Reloj controlado: `sleep` avanza el tiempo en vez de esperar."""
    estado = SimpleNamespace(ahora=1_000_000_005.0, esperas=[])

    def _sleep(segundos):
        estado.esperas.append(segundos)
        estado.ahora += segundos

    monkeypatch.setattr(cxone_mod, "_TOTP_ULTIMO_PASO", str(tmp_path / "totp"))
    monkeypatch.setattr(cxone_mod.time, "time", lambda: estado.ahora)
    monkeypatch.setattr(cxone_mod.time, "sleep", _sleep)
    return estado


def test_dos_logins_en_la_misma_ventana_no_repiten_codigo(reloj):
    primero = cxone_mod._codigo_totp_sin_repetir(SECRETO)
    segundo = cxone_mod._codigo_totp_sin_repetir(SECRETO)

    totp = pyotp.TOTP(SECRETO)
    paso_inicial = 1_000_000_005 // 30
    assert primero == totp.generate_otp(paso_inicial)
    # El segundo esperó a la ventana siguiente y usa su código.
    assert segundo == totp.generate_otp(paso_inicial + 1)
    assert primero != segundo
    assert len(reloj.esperas) == 1


def test_en_otra_ventana_no_espera(reloj):
    cxone_mod._codigo_totp_sin_repetir(SECRETO)
    reloj.ahora += 30

    cxone_mod._codigo_totp_sin_repetir(SECRETO)

    assert reloj.esperas == []


def test_estado_ilegible_no_bloquea_el_login(reloj, tmp_path):
    (tmp_path / "totp").write_text("basura")

    codigo = cxone_mod._codigo_totp_sin_repetir(SECRETO)

    assert len(codigo) == 6
    assert reloj.esperas == []


def test_reiniciar_driver_reata_las_esperas_al_chrome_nuevo():
    viejo = SimpleNamespace(quit=lambda: None, implicitly_wait=lambda s: None)
    nuevo = SimpleNamespace(quit=lambda: None, esperas=[], implicitly_wait=None)
    nuevo.implicitly_wait = lambda s: nuevo.esperas.append(s)

    fake = SimpleNamespace(
        driver=viejo, _wait_timeout=20, _implicit_wait=5, _captured_requests=["vieja"],
        _generar_driver=lambda pagina, incognito, headless: nuevo,
    )

    DriverBase.reiniciar_driver(fake, pagina="https://na1.nice-incontact.com", incognito=True, headless=True)

    assert fake.driver is nuevo
    assert fake.wait._driver is nuevo          # antes quedaba apuntando al viejo
    assert fake.wait._timeout == 20
    assert nuevo.esperas == [5]
    assert fake._captured_requests == []
