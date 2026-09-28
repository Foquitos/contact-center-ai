"""Valida la sintaxis de las migraciones del agrupado por corrida ANTES de aplicarlas.

La columna `calidad.AuditExecutionLog.run_id` todavía no existe en la base (la
migración la corre Ignacio a mano), así que acá NO se puede usar `describe` —que
resuelve nombres de columnas—: se valida con `SET PARSEONLY ON`, que chequea la
sintaxis T-SQL sin ejecutar ni escribir nada. El script tiene gaps-and-islands con
funciones de ventana y temp tables, que es justo donde un paréntesis de más no se
descubre hasta correrlo contra producción.

Correr: pytest tests/test_run_id_migracion_sql.py -m "not tokens"
"""
import os

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRACION = os.path.join(REPO_ROOT, "scripts", "migrations",
                         "2026-08-18_run_id_audit_execution_log.sql")
# El mail por corrida (2026-08-19) agrega la columna con la que el último lote junta
# las auditorías de todos los demás: va en la misma tanda y se valida igual.
MIGRACION_MAIL = os.path.join(REPO_ROOT, "scripts", "migrations",
                              "2026-08-19_mail_por_corrida.sql")


def _batches(sql_text: str):
    """Divide un script T-SQL en batches por líneas 'GO' (mismo criterio que
    test_golden_set_migracion_sql / test_migrations_vistas_sql)."""
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
    salida = []
    for path in (MIGRACION, MIGRACION_MAIL):
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            contenido = f.read()
        etiqueta = os.path.basename(path).split("_")[0]
        salida += [(f"{etiqueta}-batch{i}", b) for i, b in enumerate(_batches(contenido)) if b.strip()]
    return salida


BATCHES = _batches_de_la_migracion()


@pytest.mark.parametrize("path", [MIGRACION, MIGRACION_MAIL],
                         ids=[os.path.basename(MIGRACION), os.path.basename(MIGRACION_MAIL)])
def test_la_migracion_existe(path):
    assert os.path.exists(path), f"Falta el script de migración: {path}"


@pytest.mark.skipif(not BATCHES, reason="No está el script de migración")
@pytest.mark.parametrize("indice,batch", BATCHES, ids=[i for i, _ in BATCHES])
def test_sintaxis_de_cada_batch(engine, indice, batch):
    from conftest import _validar_parseonly

    resultado = _validar_parseonly(engine, batch)
    assert resultado.ok, f"Error de sintaxis en el {indice}: {resultado.error}"


@pytest.mark.skipif(not BATCHES, reason="No está el script de migración")
def test_es_idempotente():
    """Cada paso bajo su guarda: el script se tiene que poder correr dos veces sin
    romper (criterio de todas las migraciones del proyecto)."""
    with open(MIGRACION, encoding="utf-8") as f:
        contenido = f.read().upper()

    assert "COL_LENGTH('CALIDAD.AUDITEXECUTIONLOG', 'RUN_ID') IS NULL" in contenido, (
        "El ALTER TABLE que agrega run_id tiene que estar guardado por COL_LENGTH"
    )
    assert "WHERE RUN_ID IS NULL" in contenido, (
        "El backfill tiene que tocar solo las filas sin run_id (correrlo dos veces "
        "no puede reagrupar lo ya agrupado)"
    )
    assert "SYS.DEFAULT_CONSTRAINTS" in contenido, "El DEFAULT necesita su guarda"
    assert "SYS.INDEXES" in contenido, "El índice necesita su guarda"


@pytest.mark.skipif(not os.path.exists(MIGRACION_MAIL), reason="No está el script de migración")
def test_la_columna_de_ids_esta_guardada():
    """id_aplicativos es lo que deja cada lote para que el último arme el adjunto de
    la corrida completa; su ALTER tiene que ser idempotente como el resto."""
    with open(MIGRACION_MAIL, encoding="utf-8") as f:
        contenido = f.read().upper()

    assert "COL_LENGTH('CALIDAD.AUDITEXECUTIONLOG', 'ID_APLICATIVOS') IS NULL" in contenido
