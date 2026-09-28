"""Tests offline de la lógica RBAC compartida (app/rbac.py) y de RoleChecker.

Sin BD: las funciones puras reciben los mapas de padres/permisos como dicts y
los usuarios se construyen a mano, igual que en test_chatbot_access.py.
"""
import pytest
from fastapi import HTTPException

from app.dependencies import RoleChecker
from app.models import User
from app.rbac import (
    hay_ciclo,
    permisos_faltantes,
    puede_gestionar_rol,
    puede_simular_rol,
    resolver_empresas_permitidas,
    resolver_permisos_efectivos,
)


def _user(permissions=(), is_super=False):
    return User(
        usuario=123,
        Nombre="Test",
        permissions=list(permissions),
        is_super_admin=is_super,
    )


# ---------------------------------------------------------- permisos_faltantes

@pytest.mark.parametrize("rol,usuario,faltan", [
    ({"a", "b"}, {"a", "b", "c"}, set()),          # subconjunto estricto
    ({"a", "b"}, {"a", "b"}, set()),               # igualdad
    ({"a", "b", "z"}, {"a", "b"}, {"z"}),          # falta uno
    ({"x", "y"}, set(), {"x", "y"}),               # usuario sin permisos
    (set(), set(), set()),                         # rol vacío siempre asignable
], ids=["subconjunto", "igualdad", "falta-uno", "usuario-vacio", "rol-vacio"])
def test_permisos_faltantes(rol, usuario, faltan):
    assert permisos_faltantes(rol, usuario) == faltan


# ------------------------------------------------------------------- hay_ciclo

# Jerarquía base: 3 -> 2 -> 1 (3 hijo de 2, 2 hijo de 1)
PADRES = {1: None, 2: 1, 3: 2, 4: None}


@pytest.mark.parametrize("role_id,nuevo_padre,ciclo", [
    (4, 1, False),        # colgar un rol suelto de la raíz
    (4, 3, False),        # colgar de una hoja
    (4, None, False),     # quitar el padre nunca cicla
    (2, 2, True),         # auto-padre
    (1, 2, True),         # ciclo directo: 2 ya desciende de 1
    (1, 3, True),         # ciclo profundo: 3 desciende de 1 vía 2
    (3, 1, False),        # re-colgar una hoja más arriba
], ids=["raiz", "hoja", "sin-padre", "auto-padre", "ciclo-directo", "ciclo-profundo", "recolgar"])
def test_hay_ciclo(role_id, nuevo_padre, ciclo):
    assert hay_ciclo(role_id, nuevo_padre, PADRES) is ciclo


def test_hay_ciclo_corta_ante_ciclo_preexistente():
    # Mapa corrupto (A<->B): no debe colgarse en loop infinito
    padres = {10: 11, 11: 10}
    assert hay_ciclo(99, 10, padres) is True


# --------------------------------------------- resolver_permisos_efectivos

PERMISOS = {1: {"base:ver"}, 2: {"medio:editar"}, 3: {"hoja:borrar"}, 4: set()}


@pytest.mark.parametrize("role_ids,esperado", [
    ([1], {"base:ver"}),                                        # raíz: solo propios
    ([3], {"base:ver", "medio:editar", "hoja:borrar"}),         # hoja hereda cadena completa
    ([2], {"base:ver", "medio:editar"}),                        # nivel medio
    ([4], set()),                                               # rol sin permisos ni padre
    ([1, 3], {"base:ver", "medio:editar", "hoja:borrar"}),      # multi-rol: unión
    ([], set()),                                                # sin roles
], ids=["raiz", "hoja-cadena", "medio", "vacio", "multi-rol", "sin-roles"])
def test_resolver_permisos_efectivos(role_ids, esperado):
    assert resolver_permisos_efectivos(role_ids, PADRES, PERMISOS) == esperado


def test_resolver_no_se_cuelga_con_ciclo_preexistente():
    padres = {10: 11, 11: 10}
    permisos = {10: {"a"}, 11: {"b"}}
    assert resolver_permisos_efectivos([10], padres, permisos) == {"a", "b"}


# ---------------------------------------------------------- puede_gestionar_rol

@pytest.mark.parametrize("user_perms,is_super,rol_perms,rol_super,ok", [
    ([], True, {"lo", "que", "sea"}, False, True),      # super admin gestiona todo
    ([], True, set(), True, True),                      # incluso roles super admin
    (["a", "b"], False, {"a"}, False, True),            # delegado: subconjunto ok
    (["a", "b"], False, {"a", "b"}, False, True),       # igualdad ok
    (["a"], False, {"a", "b"}, False, False),           # superset denegado
    (["a"], False, set(), True, False),                 # rol super admin denegado
    (["a"], False, set(), False, True),                 # rol sin permisos ok
], ids=["super-todo", "super-rol-super", "subconjunto", "igualdad", "superset", "rol-super", "rol-vacio"])
def test_puede_gestionar_rol(user_perms, is_super, rol_perms, rol_super, ok):
    user = _user(user_perms, is_super=is_super)
    assert puede_gestionar_rol(user, rol_perms, rol_super) is ok


# ----------------------------------------------------------------- RoleChecker

@pytest.mark.parametrize("required,user_perms,ok", [
    (["a"], ["a"], True),
    (["a", "b"], ["b"], True),      # lógica OR: alcanza con uno
    (["a", "b"], ["c"], False),
    (["a"], [], False),
], ids=["exacto", "or-uno", "ninguno", "vacio"])
def test_role_checker(required, user_perms, ok):
    checker = RoleChecker(required)
    user = _user(user_perms)
    if ok:
        assert checker(user) is user
    else:
        with pytest.raises(HTTPException) as exc:
            checker(user)
        assert exc.value.status_code == 403


def test_role_checker_super_admin_bypass():
    user = _user([], is_super=True)
    assert RoleChecker(["cualquier:cosa"])(user) is user


# ------------------------------------------------------- puede_simular_rol

@pytest.mark.parametrize("user_perms,is_super,rol_perms,rol_super,ok", [
    ([], True, {"a", "b"}, False, True),                        # super admin simula cualquier rol
    ([], True, set(), True, False),                             # ...pero nunca uno super admin
    (["roles:impersonate", "a", "b"], False, {"a"}, False, True),   # subconjunto ok
    (["roles:impersonate", "a"], False, {"a", "b"}, False, False),  # superset denegado
    (["roles:impersonate", "a"], False, {"a"}, True, False),        # rol super admin denegado
    (["a", "b"], False, {"a"}, False, False),                   # sin el permiso de simular
    (["roles:impersonate"], False, set(), False, True),         # rol sin permisos, simulable
], ids=["super", "super-rol-super", "subconjunto", "superset", "rol-super", "sin-permiso", "rol-vacio"])
def test_puede_simular_rol(user_perms, is_super, rol_perms, rol_super, ok):
    user = _user(user_perms, is_super=is_super)
    assert puede_simular_rol(user, rol_perms, rol_super) is ok


# ------------------------------------------- resolver_empresas_permitidas
# Alcance por empresa: filas = [(empresa_id, required_code)], mismo criterio
# que empresas_disponibles() del plantillas_manager.

EMPRESAS = [
    (1, "template:hidra"),
    (11, "template:voltara"),
    (5, "template:vantix"),
    (99, None),  # empresa "pública" sin permiso requerido
]


@pytest.mark.parametrize("perms,is_super,esperado", [
    ([], True, None),                                        # super admin: sin restricción
    (["templates:manage"], False, None),                     # maestro: sin restricción
    (["template:voltara"], False, {11, 99}),                  # su empresa + públicas
    (["template:voltara", "template:hidra"], False, {1, 11, 99}),
    (["audit:execute"], False, {99}),                        # sin templates: solo públicas
    ([], False, {99}),
], ids=["super", "maestro", "una", "dos", "sin-templates", "vacio"])
def test_resolver_empresas_permitidas(perms, is_super, esperado):
    assert resolver_empresas_permitidas(EMPRESAS, set(perms), is_super) == esperado


def test_resolver_empresas_sin_publicas_da_set_vacio():
    filas = [(1, "template:hidra"), (5, "template:vantix")]
    assert resolver_empresas_permitidas(filas, {"audit:execute"}, False) == set()
