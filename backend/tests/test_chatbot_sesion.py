"""
Verifica el recorte por ventana de sesión del historial del chatbot (cero tokens, cero DB).

Problema que resuelve: un operador de call center atiende un llamado y al siguiente vuelve
a consultar el bot. Sin recortar, la condensación arrastra el contexto de la gestión anterior
y el bot responde sobre el llamado equivocado. Una gestión nueva se detecta por un hueco
temporal (> CHATBOT_SESSION_GAP_MINUTES) entre consultas consecutivas.

_filtrar_sesion_actual recibe filas ordenadas por fecha DESC (más nueva primero), con
`now_sql` = reloj del SQL Server (mismo valor en todas las filas), y devuelve solo las de
la sesión actual.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.config import settings
from chatBot import ChatBot

NOW = datetime(2026, 7, 15, 12, 0, 0)


def _rows(*mins_ago):
    """Construye filas (DESC por fecha) con edades en minutos respecto de NOW."""
    return [
        SimpleNamespace(query=f"q{i}", response=f"r{i}", fecha=NOW - timedelta(minutes=m), now_sql=NOW)
        for i, m in enumerate(mins_ago)
    ]


def _filtrar(rows):
    # El método no usa self (solo lee settings y loguea); se invoca sin instanciar el bot.
    return ChatBot._filtrar_sesion_actual(None, rows)  # pyright: ignore[reportArgumentType]


@pytest.fixture(autouse=True)
def _ventana_10min(monkeypatch):
    monkeypatch.setattr(settings, "CHATBOT_SESSION_GAP_MINUTES", 10)


def test_sin_filas_devuelve_vacio():
    assert _filtrar([]) == []


def test_todas_dentro_de_la_ventana_se_conservan():
    rows = _rows(1, 3, 6)  # huecos: now->1min, 2min, 3min, todos < 10
    assert len(_filtrar(rows)) == 3


def test_ultima_consulta_vieja_arranca_sesion_nueva():
    """Si la última interacción quedó fuera de la ventana respecto de ahora, memoria limpia."""
    rows = _rows(15, 17)  # la más nueva es de hace 15 min > 10
    assert _filtrar(rows) == []


def test_corta_en_el_primer_hueco_grande():
    """Conserva el bloque contiguo más nuevo y descarta la gestión anterior."""
    rows = _rows(2, 4, 40, 50)  # 2 y 4 min = sesión actual; salto a 40 min = otra gestión
    filtradas = _filtrar(rows)
    assert [r.query for r in filtradas] == ["q0", "q1"]


def test_gap_cero_desactiva_la_ventana(monkeypatch):
    monkeypatch.setattr(settings, "CHATBOT_SESSION_GAP_MINUTES", 0)
    rows = _rows(2, 40, 100)  # con la ventana apagada, todo es una sola sesión
    assert len(_filtrar(rows)) == 3
