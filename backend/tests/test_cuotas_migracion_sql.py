"""Valida la sintaxis de la migración de cupos de auditoría ANTES de aplicarla.

Las tablas todavía no existen en la base (la migración la corre Ignacio a mano),
así que se valida con `SET PARSEONLY ON`: chequea la sintaxis T-SQL sin ejecutar
ni escribir nada.

Correr: pytest tests/test_cuotas_migracion_sql.py -m "not tokens"
"""
import os

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRACION = os.path.join(REPO_ROOT, "scripts", "migrations",
                         "2026-08-31_cuotas_auditoria.sql")


def _batches(sql_text: str):
    """Divide un script T-SQL en batches usando líneas 'GO'."""
    batch, batches = [], []
    for linea in sql_text.splitlines():
        if linea.strip().upper() == "GO":
            if batch:
                batches.append("\n".join(batch))
                batch = []
        else:
            batch.append(linea)
    if batch and "\n".join(batch).strip():
        batches.append("\n".join(batch))
    return batches


def _batches_de_la_migracion():
    if not os.path.exists(MIGRACION):
        return []
    with open(MIGRACION, encoding="utf-8") as f:
        contenido = f.read()
    return [(f"batch{i}", b) for i, b in enumerate(_batches(contenido))
            if b.strip() and not b.strip().upper().startswith("USE ")]


BATCHES = _batches_de_la_migracion()


def test_la_migracion_existe():
    assert os.path.exists(MIGRACION), f"Falta el script de migración: {MIGRACION}"


@pytest.mark.parametrize("indice,batch", BATCHES, ids=[i for i, _ in BATCHES])
def test_sintaxis_de_cada_batch(engine, indice, batch):
    # PARSEONLY y no la fixture `validar_sql`: esa prefiere `describe`, que resuelve
    # nombres y fallaría por las tablas que esta migración viene a crear.
    from conftest import _validar_parseonly

    resultado = _validar_parseonly(engine, batch)
    assert resultado.ok, f"Error de sintaxis en el {indice}: {resultado.error}"


def test_es_idempotente():
    """Las dos tablas y los dos permisos se crean bajo guarda: el script se tiene
    que poder correr dos veces sin romper ni duplicar nada."""
    contenido = open(MIGRACION, encoding="utf-8").read()
    assert contenido.count("IF OBJECT_ID(") >= 2
    assert contenido.count("IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions") == 2


def test_los_permisos_nacen_sin_asignar():
    """Mismo criterio que audit:sync / audit:scheduler: el INSERT en
    RolePermissions queda comentado para que lo habilite quien corresponda."""
    contenido = open(MIGRACION, encoding="utf-8").read()
    activos = [linea for linea in contenido.splitlines()
               if "INSERT INTO pagina_web.RolePermissions" in linea]
    assert activos, "El bloque opcional de asignación tiene que seguir documentado."
    inicio = contenido.index("OPCIONAL — asignar los permisos")
    fin = contenido.index("--------------------------------------------------------------------------- */", inicio)
    for linea in activos:
        assert inicio < contenido.index(linea) < fin, (
            "El INSERT en RolePermissions tiene que estar dentro del bloque comentado."
        )
