"""Valida la sintaxis de las migraciones del Golden Set ANTES de aplicarlas.

Las tablas todavía no existen en la base (la migración la corre Ignacio a mano),
así que acá NO se puede usar `describe` (que resuelve nombres de tablas): se valida
con `SET PARSEONLY ON`, que chequea la sintaxis T-SQL sin ejecutar ni escribir
nada. Sirve para no descubrir un paréntesis de más recién al correr el script
contra producción.

Correr: pytest tests/test_golden_set_migracion_sql.py -m "not tokens"
"""
import os

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRACIONES = [
    os.path.join(REPO_ROOT, "scripts", "migrations", "2026-08-06c_golden_set_fase0.sql"),
    os.path.join(REPO_ROOT, "scripts", "migrations", "2026-08-06d_plantilla_versiones.sql"),
    os.path.join(REPO_ROOT, "scripts", "migrations", "2026-08-27_reauditoria_versionado.sql"),
]
MIGRACION = MIGRACIONES[0]  # la de la Fase 0, sobre la que corren los chequeos de forma


def _batches(sql_text: str):
    """Divide un script T-SQL en batches usando líneas 'GO' (mismo criterio que
    test_migrations_vistas_sql)."""
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


def _batches_de_las_migraciones():
    """[(id_legible, batch)] de todos los scripts del Golden Set."""
    salida = []
    for path in MIGRACIONES:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            contenido = f.read()
        etiqueta = os.path.basename(path).split("_")[0]
        # El USE Acme inicial no aporta a la validación y ensucia el id del test.
        for i, batch in enumerate(_batches(contenido)):
            if batch.strip() and not batch.strip().upper().startswith("USE "):
                salida.append((f"{etiqueta}-batch{i}", batch))
    return salida


BATCHES = _batches_de_las_migraciones()


@pytest.mark.parametrize("path", MIGRACIONES, ids=[os.path.basename(p) for p in MIGRACIONES])
def test_la_migracion_existe(path):
    assert os.path.exists(path), f"Falta el script de migración: {path}"


@pytest.mark.parametrize("indice,batch", BATCHES, ids=[i for i, _ in BATCHES])
def test_sintaxis_de_cada_batch(engine, indice, batch):
    # PARSEONLY directo y no la fixture `validar_sql`: esa prefiere `describe`,
    # que resuelve nombres de tablas y fallaría por las tablas que esta misma
    # migración viene a crear.
    from conftest import _validar_parseonly

    resultado = _validar_parseonly(engine, batch)
    assert resultado.ok, f"Error de sintaxis en el batch {indice}: {resultado.error}"


def test_es_idempotente():
    """Todo objeto se crea bajo una guarda: el script se tiene que poder correr dos
    veces sin romper (es el criterio de todas las migraciones del proyecto)."""
    with open(MIGRACION, encoding="utf-8") as f:
        contenido = f.read().upper()

    creaciones = contenido.count("CREATE TABLE ")
    guardas = contenido.count("OBJECT_ID(")
    assert guardas >= creaciones, "Hay CREATE TABLE sin su guarda IF OBJECT_ID(...) IS NULL"

    assert "COL_LENGTH('CALIDAD.AUDIOAUDITORIA', 'FIJADO') IS NULL" in contenido, (
        "El ALTER TABLE que agrega Fijado tiene que estar guardado por COL_LENGTH"
    )
    # Los permisos nacen sin asignar (patrón del proyecto): solo se insertan en
    # Permissions, nunca en RolePermissions.
    assert "ROLEPERMISSIONS" not in contenido, (
        "La migración no debe asignar los permisos a ningún rol (nacen sin asignar)"
    )
