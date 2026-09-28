"""
Valida las vistas definidas en scripts/migrations/*.sql contra SQL Server
SIN ejecutarlas ni aplicar DDL (cero tokens, cero escritura):

- El cuerpo (SELECT) de cada CREATE OR ALTER VIEW se valida con
  `sys.dm_exec_describe_first_result_set` (sintaxis + binding de columnas y
  tablas reales) vía la fixture `validar_sql` de conftest.

Sirve para verificar una migración ANTES de aplicarla a la base.
"""
import glob
import os
import re

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRATIONS_GLOB = os.path.join(REPO_ROOT, "scripts", "migrations", "*.sql")

_RE_VIEW = re.compile(
    r"CREATE\s+OR\s+ALTER\s+VIEW\s+(?P<nombre>[\w\.\[\]]+)\s+AS\s+(?P<cuerpo>.+)",
    re.IGNORECASE | re.DOTALL,
)


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


def _vistas_en_migraciones():
    """[(archivo, nombre_vista, cuerpo_select)] de todos los scripts de migración."""
    vistas = []
    for path in sorted(glob.glob(MIGRATIONS_GLOB)):
        with open(path, encoding="utf-8") as f:
            contenido = f.read()
        for batch in _batches(contenido):
            m = _RE_VIEW.search(batch)
            if m:
                cuerpo = m.group("cuerpo").strip().rstrip(";")
                vistas.append((os.path.basename(path), m.group("nombre"), cuerpo))
    return vistas


VISTAS = _vistas_en_migraciones()


def _columna_agregada_en_migracion(archivo: str, columna: str) -> bool:
    """True si la misma migración agrega esa columna (ALTER TABLE ... ADD)."""
    path = os.path.join(REPO_ROOT, "scripts", "migrations", archivo)
    with open(path, encoding="utf-8") as f:
        contenido = f.read()
    patron = re.compile(
        r"ALTER\s+TABLE\s+[\w\.\[\]]+\s+ADD\s+\[?" + re.escape(columna) + r"\]?\b",
        re.IGNORECASE,
    )
    return bool(patron.search(contenido))


@pytest.mark.skipif(not VISTAS, reason="No hay vistas en scripts/migrations/*.sql")
@pytest.mark.parametrize(
    "archivo,nombre,cuerpo", VISTAS, ids=[f"{a}::{n}" for a, n, _ in VISTAS]
)
def test_cuerpo_de_vista_valido(archivo, nombre, cuerpo, validar_sql):
    """El SELECT de cada vista debe compilar contra la base real (sin ejecutarse)."""
    resultado = validar_sql(cuerpo)
    if not resultado.ok:
        # Huevo-y-gallina: la vista puede usar una columna que la MISMA migración
        # crea. Hasta aplicarla el binding contra la BD falla; xfail para no
        # romper la suite pre-migración (aplicada la migración, vuelve a validar).
        m = re.search(r"Invalid column name '(\w+)'", str(resultado.error))
        if m and _columna_agregada_en_migracion(archivo, m.group(1)):
            pytest.xfail(f"la columna '{m.group(1)}' la crea esta misma migración (pendiente de aplicar)")
        # La base de demostración no tiene los linked servers de la instalación original.
        m = re.search(r"Could not find server '(\w+)'", str(resultado.error))
        if m:
            pytest.skip(f"el linked server {m.group(1)} no existe en esta base")
    assert resultado.ok, f"{archivo} -> {nombre}: {resultado.error}"
