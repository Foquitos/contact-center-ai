"""
Regresión (2026-07-30): los filtros que marca el usuario tienen que sobrevivir el viaje
Auditor -> Descarga -> builder SQL.

Reporte de Lina (Vantix/Turnos): pidió 10 auditorías por operador filtrando por
"Salientes" y le vinieron llamadas entrantes. La causa: Auditor.__descarga_audios armaba
la llamada a descarga_aleatoria sin pasar `sentido` en la rama de Vantix (Track On) ni en
la rama Mitrol genérica, así que el builder lo recibía en None y no filtraba nada. El
resto de las ramas (ALARMIX, AuroraSalud, HIDRA...) sí lo pasaban: por eso el bug era invisible
salvo en esos dos clientes.

Test 100% offline: no toca DB, ni las APIs de grabación, ni Gemini.
"""
from types import SimpleNamespace

import pandas as pd
import pytest

import Auditor as auditor_mod
from Auditor import AuditorIA


class _ApiFalsa:
    """Reemplazo de CYT_comunicaciones/Mitrol: no se conecta a ningún lado."""

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def descargas(monkeypatch):
    """Captura los kwargs con los que Auditor llama a descarga_aleatoria."""
    llamadas = []

    def stub(**kwargs):
        llamadas.append(kwargs)
        return pd.DataFrame()

    monkeypatch.setattr(auditor_mod, "descarga_aleatoria", stub)
    monkeypatch.setattr(auditor_mod, "CYT_comunicaciones", _ApiFalsa)
    monkeypatch.setattr(auditor_mod, "Mitrol", _ApiFalsa)
    monkeypatch.setattr(auditor_mod, "Genesys", _ApiFalsa)
    return llamadas


@pytest.mark.parametrize("empresa", ["Trackon-Vantix", "Track On", "AuroraSalud", "Benefix", "CualquierOtra"],
                         ids=["vantix", "trackon", "aurorasalud", "benefix", "mitrol-generico"])
def test_sentido_llega_a_la_descarga(empresa, descargas):
    fake = SimpleNamespace(engine=None, tempfolder="/tmp")

    AuditorIA._AuditorIA__descarga_audios(
        fake, cantidad=10, empresa=[empresa], loginid=["1070", "1093"],
        tipificacion=["Coordina turno"], sentido=["Saliente"], por_operador=True,
    )

    assert len(descargas) == 1
    assert descargas[0].get("sentido") == ["Saliente"], (
        f"'{empresa}' perdió el filtro de sentido antes de llegar a la descarga"
    )
