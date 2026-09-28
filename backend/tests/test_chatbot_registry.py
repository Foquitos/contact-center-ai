"""Tests offline del ChatbotRegistry: snapshot con TTL, hot-reload por
index_version/updated_at y guardias (bot sin índice, re-instanciación fallida).

No tocan la BD ni Qdrant: el engine es fake y _instantiate se monkeypatcha.
"""
from datetime import datetime

import pytest

from app.chatbot_registry import ChatbotRegistry
from app.config import settings


@pytest.fixture(autouse=True)
def _prod_reload_por_defecto(monkeypatch):
    """Los tests de hot-reload asumen semántica de PROD (el registry sigue el puntero
    index_version/updated_at de la BD). Se fija acá para que el módulo no dependa del
    .env de la máquina: en dev el backend corre con CHATBOT_FOLLOW_INDEX_RELOAD=False.
    Los tests del modo dev lo sobreescriben a False explícitamente."""
    monkeypatch.setattr(settings, "CHATBOT_FOLLOW_INDEX_RELOAD", True)


# ------------------------------------------------------------------ fakes

def _row(slug, index_version=1, updated_at=None, grupo=None):
    return {
        "id": hash(slug) % 1000,
        "slug": slug,
        "nombre": slug.title(),
        "descripcion": None,
        "system_prompt": "prompt",
        "grupo": grupo,
        "permission_code": "chatbot:csv" if grupo == "csv" else f"chatbot:{slug}",
        "activo": True,
        "index_version": index_version,
        "index_status": "ready" if index_version else "never_indexed",
        "last_indexed_at": None,
        "updated_at": updated_at or datetime(2026, 1, 1),
    }


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class FakeConn:
    def __init__(self, engine):
        self._engine = engine

    def execute(self, query, params=None):
        self._engine.query_count += 1
        return FakeResult(self._engine.rows)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeEngine:
    def __init__(self, rows):
        self.rows = rows
        self.query_count = 0

    def connect(self):
        return FakeConn(self)


def _registry(rows, ttl):
    return ChatbotRegistry(engine=FakeEngine(rows), ttl_seconds=ttl)


# ------------------------------------------------------------------ snapshot/TTL

def test_snapshot_no_reconsulta_dentro_del_ttl():
    reg = _registry([_row("voltara")], ttl=3600)
    reg.get_config("voltara")
    reg.get_config("voltara")
    reg.list_active()
    assert reg.engine.query_count == 1


def test_snapshot_reconsulta_con_ttl_vencido():
    reg = _registry([_row("voltara")], ttl=0)
    reg.get_config("voltara")
    reg.get_config("voltara")
    assert reg.engine.query_count == 2


def test_slug_desconocido_fuerza_refresh_inmediato():
    """Un bot creado post-startup aparece sin esperar el TTL."""
    reg = _registry([_row("voltara")], ttl=3600)
    reg.get_config("voltara")
    assert reg.engine.query_count == 1

    # Aparece un bot nuevo en la BD; el TTL largo taparía el cambio, pero el
    # slug desconocido dispara un refresh forzado.
    reg.engine.rows = [_row("voltara"), _row("nuevo_bot")]
    cfg = reg.get_config("nuevo_bot")
    assert cfg is not None and cfg.slug == "nuevo_bot"
    assert reg.engine.query_count == 2


def test_bot_desactivado_se_descarta_del_snapshot():
    reg = _registry([_row("voltara"), _row("paygo")], ttl=0)
    reg._instantiate = lambda cfg: object()
    assert reg.get("paygo") is not None

    reg.engine.rows = [_row("voltara")]  # paygo se desactivó
    assert reg.get("paygo") is None
    assert "paygo" not in reg._instances


# ------------------------------------------------------------------ get()/hot-reload

def test_bot_sin_indice_no_se_instancia(monkeypatch):
    reg = _registry([_row("voltara", index_version=0)], ttl=3600)
    monkeypatch.setattr(reg, "_local_index_version", lambda cfg: 0)  # tampoco hay índice local
    llamadas = []
    reg._instantiate = lambda cfg: llamadas.append(cfg.slug) or object()
    assert reg.get("voltara") is None
    assert llamadas == []


def test_get_reusa_la_instancia_si_no_cambio_nada():
    reg = _registry([_row("voltara")], ttl=0)
    creados = []

    def instanciar(cfg):
        creados.append(cfg.slug)
        return object()

    reg._instantiate = instanciar
    a = reg.get("voltara")
    b = reg.get("voltara")
    assert a is b
    assert creados == ["voltara"]


def test_bump_de_index_version_reinstancia():
    reg = _registry([_row("voltara", index_version=1)], ttl=0)
    creados = []
    reg._instantiate = lambda cfg: creados.append(cfg.index_version) or object()

    a = reg.get("voltara")
    reg.engine.rows = [_row("voltara", index_version=2)]
    b = reg.get("voltara")
    assert a is not b
    assert creados == [1, 2]


def test_bump_de_updated_at_reinstancia():
    """Editar el prompt desde el panel (sin reindexar) también recarga el bot."""
    reg = _registry([_row("voltara", updated_at=datetime(2026, 1, 1))], ttl=0)
    creados = []
    reg._instantiate = lambda cfg: creados.append(cfg.slug) or object()

    a = reg.get("voltara")
    reg.engine.rows = [_row("voltara", updated_at=datetime(2026, 6, 1))]
    b = reg.get("voltara")
    assert a is not b
    assert len(creados) == 2


def test_pin_congela_la_instancia_con_follow_false(monkeypatch):
    """CHATBOT_FOLLOW_INDEX_RELOAD=False fija la instancia del arranque y no recarga ante
    cambios (interruptor de pin/depuración). El index_version del row simula el estado del
    entorno (la query lo resuelve por ChatbotIndexState)."""
    monkeypatch.setattr(settings, "CHATBOT_FOLLOW_INDEX_RELOAD", False)
    reg = _registry([_row("voltara", index_version=1)], ttl=0)
    creados = []
    reg._instantiate = lambda cfg: creados.append(cfg.index_version) or object()

    a = reg.get("voltara")
    reg.engine.rows = [_row("voltara", index_version=2)]  # cambió el estado del entorno
    b = reg.get("voltara")
    assert a is b            # misma instancia: pin activo
    assert creados == [1]    # no re-instanció


def test_sin_version_en_entorno_usa_indice_local(monkeypatch):
    """Si el entorno no publicó versión (ChatbotIndexState ausente => 0) pero hay un índice
    en el filesystem local, se sirve ese. Cubre la transición de dev antes de su 1er reindex."""
    reg = _registry([_row("voltara", index_version=0)], ttl=0)  # sin estado en este entorno
    monkeypatch.setattr(reg, "_local_index_version", lambda cfg: 3)  # local hay v3
    visto = []
    reg._instantiate = lambda cfg: visto.append(cfg.index_version) or object()

    assert reg.get("voltara") is not None
    assert visto == [3]      # instanció la versión local, no 0


def test_reinstanciacion_fallida_conserva_la_instancia_vieja():
    """Si el reload falla (ej. Qdrant caído), mejor servir con el bot viejo que
    devolver None."""
    reg = _registry([_row("voltara", index_version=1)], ttl=0)
    instancia_vieja = object()
    reg._instantiate = lambda cfg: instancia_vieja
    assert reg.get("voltara") is instancia_vieja

    reg.engine.rows = [_row("voltara", index_version=2)]
    reg._instantiate = lambda cfg: None  # la re-instanciación falla
    assert reg.get("voltara") is instancia_vieja


def test_bd_caida_mantiene_snapshot_anterior():
    reg = _registry([_row("voltara")], ttl=0)
    reg._instantiate = lambda cfg: object()
    assert reg.get("voltara") is not None

    def conexion_rota():
        raise RuntimeError("BD caída")

    reg.engine.connect = conexion_rota
    assert reg.get("voltara") is not None  # sigue sirviendo con lo que tenía
