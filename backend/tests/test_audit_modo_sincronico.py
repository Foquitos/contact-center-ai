"""Tests del permiso audit:sync (modo sincrónico de auditoría).

Offline (sin BD ni tokens): la regla es pura sobre los permisos del usuario.
El sincrónico le cuesta a Gemini el doble que el batch, así que auditar "ahora"
es un permiso aparte de auditar (audit:execute); el default es Batch.

Se cubren los dos puntos de entrada que lo exigen: POST /Auditar/ (batch=False)
y el alta/edición de schedulers (parametros_json.is_batch falsy), este último a
través de _exigir_modo_scheduler.
"""
import pytest
from fastapi import HTTPException

from app.models import User
from app.rbac import exigir_modo_sincronico, puede_auditar_sincronico


def _user(permissions=(), is_super=False):
    return User(
        usuario=123,
        Nombre="Test",
        permissions=list(permissions),
        is_super_admin=is_super,
    )


@pytest.mark.parametrize("perms,is_super,ok", [
    (["audit:sync"], False, True),
    (["audit:execute", "audit:sync"], False, True),
    ([], True, True),                                  # super admin siempre
    (["audit:execute"], False, False),                 # auditar != auditar en sincrónico
    (["audit:execute", "audit:scheduler"], False, False),
    ([], False, False),
], ids=["con-permiso", "execute+sync", "super-admin", "solo-execute",
        "execute+scheduler", "sin-permisos"])
def test_puede_auditar_sincronico(perms, is_super, ok):
    assert puede_auditar_sincronico(_user(perms, is_super)) is ok


def test_exigir_modo_sincronico_deja_pasar_con_permiso():
    exigir_modo_sincronico(_user(["audit:sync"]))  # no levanta


def test_exigir_modo_sincronico_corta_sin_permiso():
    with pytest.raises(HTTPException) as exc:
        exigir_modo_sincronico(_user(["audit:execute"]))
    assert exc.value.status_code == 403
    # El mensaje tiene que empujar al batch: es la salida que el usuario sí tiene.
    assert "audit:sync" in exc.value.detail
    assert "Batch" in exc.value.detail


# ------------------------------------------------------- scheduler (is_batch)

def _scheduler_request(is_batch):
    from app.models import SchedulerCreateRequest

    return SchedulerCreateRequest(
        task_name="test",
        frecuencia="diaria",
        hora_ejecucion="08:00",
        empresa="1",
        campana="1",
        plantilla_id=1,
        cantidad=10,
        rango_dinamico="dia_habil_anterior",
        parametros_json={"is_batch": is_batch},
    )


@pytest.mark.parametrize("is_batch,perms,corta", [
    (True, ["audit:execute"], False),       # batch: no hace falta audit:sync
    (False, ["audit:sync"], False),
    (False, ["audit:execute"], True),       # programar el modo caro sin permiso
], ids=["batch-sin-permiso", "sync-con-permiso", "sync-sin-permiso"])
def test_exigir_modo_scheduler(is_batch, perms, corta):
    # Import adentro: el router arrastra app.services (Auditor) y encarece la
    # colección de pytest, igual que en test_chatbot_access.py.
    from app.routers.auditoria import _exigir_modo_scheduler

    user = _user(perms)
    request = _scheduler_request(is_batch)
    if corta:
        with pytest.raises(HTTPException) as exc:
            _exigir_modo_scheduler(user, request)
        assert exc.value.status_code == 403
    else:
        _exigir_modo_scheduler(user, request)


def test_exigir_modo_scheduler_sin_is_batch_es_sincronico():
    """Un scheduler viejo (sin la clave is_batch) corre en sincrónico: se trata
    como tal y exige el permiso."""
    from app.models import SchedulerCreateRequest
    from app.routers.auditoria import _exigir_modo_scheduler

    request = SchedulerCreateRequest(
        task_name="test", frecuencia="diaria", hora_ejecucion="08:00",
        empresa="1", campana="1", plantilla_id=1, cantidad=10,
        rango_dinamico="dia_habil_anterior", parametros_json={},
    )
    with pytest.raises(HTTPException) as exc:
        _exigir_modo_scheduler(_user(["audit:execute"]), request)
    assert exc.value.status_code == 403
