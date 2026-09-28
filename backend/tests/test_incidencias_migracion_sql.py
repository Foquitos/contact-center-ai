"""Valida la sintaxis de la migración de incidencias ANTES de aplicarla.

La columna todavía no existe en la base (la migración la corre Ignacio a mano), así
que se valida con `SET PARSEONLY ON`: chequea la sintaxis T-SQL sin ejecutar ni
escribir nada. Sirve para no descubrir un paréntesis de más recién al correrla contra
producción.

Correr: pytest tests/test_incidencias_migracion_sql.py -m "not tokens"
"""
import os

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRACION = os.path.join(REPO_ROOT, "scripts", "migrations",
                         "2026-08-18b_incidencias_auditoria.sql")


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
    # nombres de columnas y fallaría por la columna que esta migración viene a crear.
    from conftest import _validar_parseonly

    resultado = _validar_parseonly(engine, batch)
    assert resultado.ok, f"Error de sintaxis en el {indice}: {resultado.error}"


def test_es_idempotente():
    """Todo objeto se crea bajo una guarda: el script se tiene que poder correr dos
    veces sin romper (y sin duplicar el índice ni re-alterar el SP)."""
    contenido = open(MIGRACION, encoding="utf-8").read()
    assert "IF NOT EXISTS" in contenido
    assert contenido.count("IF NOT EXISTS") >= 2
    # El ALTER del SP se saltea si ya devuelve la columna.
    assert "CHARINDEX('A_Ext.Incidencia', @def) > 0" in contenido


def test_no_toca_nada_si_el_sp_cambio():
    """La reescritura del SP se hace sobre su propia definición: si los patrones no
    aparecen la cantidad esperada de veces, el script falla SIN modificar nada."""
    contenido = open(MIGRACION, encoding="utf-8").read()
    assert "IF @vecesSel <> 1 OR @vecesVac <> 2" in contenido
    assert "RAISERROR" in contenido
