"""Tests del control de acceso por chatbot: require_chatbot_user y la resolución
de bots permitidos/efectivos del router.

Los del router importan app.routers.chatbot (que arrastra app.services y la
config global de LlamaIndex), por eso se importan DENTRO de los tests, igual que
en test_chatbots_live.py, para no encarecer la colección de pytest.
"""
import pytest
from fastapi import HTTPException

from app.dependencies import require_chatbot_user
from app.models import User


def _user(permissions=(), is_super=False, campana=None):
    return User(
        usuario=123,
        Nombre="Test",
        campana=campana,
        permissions=list(permissions),
        is_super_admin=is_super,
    )


# ------------------------------------------------------- require_chatbot_user

@pytest.mark.parametrize("perms,ok", [
    (["chatbot:voltara"], True),
    (["chatbot:csv"], True),
    (["chatbot:admin"], True),                    # calidad puede probar los bots
    (["chatbot:un_bot_nuevo"], True),             # permisos creados por el panel
    (["chatbot:sql"], False),                     # Asistente de Datos: otro flujo
    (["audit:execute", "users:create"], False),
    ([], False),
], ids=["voltara", "csv", "admin", "bot-nuevo", "solo-sql", "sin-chatbot", "vacio"])
def test_require_chatbot_user(perms, ok):
    user = _user(perms)
    if ok:
        assert require_chatbot_user(user) is user
    else:
        with pytest.raises(HTTPException) as exc:
            require_chatbot_user(user)
        assert exc.value.status_code == 403


def test_require_chatbot_user_super_admin_pasa_sin_permisos():
    user = _user([], is_super=True)
    assert require_chatbot_user(user) is user


# ------------------------------------------------- resolución de bots (router)

def _fake_configs(monkeypatch, slugs_grupos):
    """Reemplaza el snapshot del registry por bots sintéticos [(slug, grupo)]."""
    from datetime import datetime
    import app.routers.chatbot as router
    from app.chatbot_config import ChatbotConfig

    configs = [
        ChatbotConfig(
            id=i, slug=slug, nombre=slug.title(), descripcion=None,
            system_prompt="p", grupo=grupo,
            permission_code="chatbot:csv" if grupo == "csv" else f"chatbot:{slug}",
            activo=True, index_version=1, index_status="ready",
            last_indexed_at=None, updated_at=datetime(2026, 1, 1),
        )
        for i, (slug, grupo) in enumerate(slugs_grupos, start=1)
    ]
    monkeypatch.setattr(router.chatbot_registry, "list_active", lambda: configs)
    return router


BOTS = [("voltara", None), ("paygo", None), ("csv_premium", "csv"), ("csv_vip", "csv")]


def test_get_allowed_bots_filtra_por_permiso(monkeypatch):
    router = _fake_configs(monkeypatch, BOTS)
    user = _user(["chatbot:voltara", "chatbot:csv"])
    slugs = {c.slug for c in router.get_allowed_bots(user)}
    assert slugs == {"voltara", "csv_premium", "csv_vip"}


def test_get_allowed_bots_admin_ve_todos(monkeypatch):
    router = _fake_configs(monkeypatch, BOTS)
    user = _user(["chatbot:admin"])
    assert len(router.get_allowed_bots(user)) == len(BOTS)


def test_resolver_form_no_permitido_da_403(monkeypatch):
    router = _fake_configs(monkeypatch, BOTS)
    user = _user(["chatbot:voltara"])
    with pytest.raises(HTTPException) as exc:
        router._resolver_slug_efectivo(user, "paygo")
    assert exc.value.status_code == 403


def test_resolver_acepta_nombre_legacy_de_campana(monkeypatch):
    """El form viejo mandaba 'csv premium'; se normaliza al slug csv_premium."""
    router = _fake_configs(monkeypatch, BOTS)
    user = _user(["chatbot:csv"])
    monkeypatch.setattr(router, "resolver_slug_csv_por_pcrc", lambda doc: None)
    assert router._resolver_slug_efectivo(user, "CSV Premium") == "csv_premium"


def test_resolver_unico_bot_va_directo(monkeypatch):
    router = _fake_configs(monkeypatch, BOTS)
    user = _user(["chatbot:voltara"])
    assert router._resolver_slug_efectivo(user, None) == "voltara"


def test_resolver_csv_usa_pcrc(monkeypatch):
    router = _fake_configs(monkeypatch, BOTS)
    user = _user(["chatbot:csv"])
    monkeypatch.setattr(router, "resolver_slug_csv_por_pcrc", lambda doc: "csv_vip")
    assert router._resolver_slug_efectivo(user, None) == "csv_vip"


def test_resolver_csv_sin_pcrc_y_sin_form_pide_seleccion(monkeypatch):
    """Calidad con chatbot:csv y sin PCRC vigente: debe elegir en el selector."""
    router = _fake_configs(monkeypatch, BOTS)
    user = _user(["chatbot:csv"])
    monkeypatch.setattr(router, "resolver_slug_csv_por_pcrc", lambda doc: None)
    with pytest.raises(HTTPException) as exc:
        router._resolver_slug_efectivo(user, None)
    assert exc.value.status_code == 400


def test_resolver_multibot_cae_a_la_campana_del_operador(monkeypatch):
    router = _fake_configs(monkeypatch, BOTS)
    user = _user(["chatbot:voltara", "chatbot:paygo"], campana="Paygo")
    assert router._resolver_slug_efectivo(user, None) == "paygo"


def test_resolver_sin_permisos_da_403(monkeypatch):
    router = _fake_configs(monkeypatch, BOTS)
    user = _user([])
    with pytest.raises(HTTPException) as exc:
        router._resolver_slug_efectivo(user, None)
    assert exc.value.status_code == 403


# ------------------------------------------- adjuntos: permiso + flag del bot

def _config(slug="voltara", permite_adjuntos=False):
    from datetime import datetime
    from app.chatbot_config import ChatbotConfig

    return ChatbotConfig(
        id=1, slug=slug, nombre=slug.title(), descripcion=None,
        system_prompt="p", grupo=None, permission_code=f"chatbot:{slug}",
        activo=True, index_version=1, index_status="ready",
        last_indexed_at=None, updated_at=datetime(2026, 1, 1),
        permite_adjuntos=permite_adjuntos,
    )


def _archivo(nombre="captura.png"):
    """UploadFile mínimo: lo que el endpoint necesita es filename y read().

    Con un PNG DE VERDAD: la validación abre la imagen, así que unos magic bytes
    sueltos rebotarían por inválidos y taparían lo que se quiere probar acá, que es
    el control de acceso."""
    import io
    from fastapi import UploadFile
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(buffer, format="PNG")
    buffer.seek(0)
    return UploadFile(filename=nombre, file=buffer)


def _leer(archivos, user, config):
    import asyncio
    import app.routers.chatbot as router

    return asyncio.run(router._leer_adjuntos(archivos, user, config))


def test_una_consulta_sin_archivos_no_pasa_por_el_control_de_adjuntos():
    """La regresión que importa: el tráfico normal (sin adjuntos) tiene que seguir
    funcionando igual para todos, tengan o no el permiso nuevo."""
    user = _user(["chatbot:voltara"])
    assert _leer(None, user, _config()) == []
    assert _leer([], user, _config()) == []


def test_adjuntar_sin_el_permiso_da_403():
    from app import chatbot_adjuntos

    user = _user(["chatbot:voltara"])
    with pytest.raises(HTTPException) as exc:
        _leer([_archivo()], user, _config(permite_adjuntos=True))
    assert exc.value.status_code == 403
    assert chatbot_adjuntos.PERMISO not in user.permissions


def test_adjuntar_a_un_bot_sin_el_flag_da_400():
    """Distinto del 403: el problema no es quién pregunta sino a qué bot."""
    from app import chatbot_adjuntos

    user = _user(["chatbot:voltara", chatbot_adjuntos.PERMISO])
    with pytest.raises(HTTPException) as exc:
        _leer([_archivo()], user, _config(permite_adjuntos=False))
    assert exc.value.status_code == 400
    assert "no acepta archivos" in exc.value.detail


def test_con_permiso_y_bot_habilitado_el_archivo_entra():
    from app import chatbot_adjuntos

    user = _user(["chatbot:voltara", chatbot_adjuntos.PERMISO])
    preparados = _leer([_archivo()], user, _config(permite_adjuntos=True))
    assert [a.nombre for a in preparados] == ["captura.png"]
