"""
El SQL de la cola de lotes de auditoría, validado contra el esquema REAL.

POR QUÉ EXISTE ESTE ARCHIVO
---------------------------
Mismo cuidado que tests/test_transcripcion_cola_sql.py, por la misma caída: el
2026-08-25 el despacho de la cola de transcripciones se cayó en producción con
"Invalid column name 'FechaEnvio'" porque un UPDATE sobre un CTE **solo puede escribir
las columnas que el CTE expone en su SELECT**. `SET PARSEONLY ON` no lo atrapa: la
consulta es sintácticamente perfecta.

Además, calidad.BatchPendientes es una tabla nueva que vive solo en la base (el repo
tiene la migración, no el esquema), así que acá se chequea que el código y la migración
hablen de las mismas columnas — si alguien agrega una columna al INSERT y se olvida de
la migración, se ve en el test y no en la primera corrida sin cupo.

No escribe ni ejecuta ninguna sentencia, no gasta tokens.
"""
import os
import re

import pytest

from AuditorIA import batch_cola

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODULO = os.path.join(RAIZ, "backend", "AuditorIA", "batch_cola.py")
MIGRACION = os.path.join(RAIZ, "scripts", "migrations", "2026-08-26_batch_pendientes.sql")


# --------------------------------------------------------------------------- #
# 1. El claim: el CTE tiene que exponer todo lo que el UPDATE escribe          #
# --------------------------------------------------------------------------- #
def _columnas_del_set(sql: str) -> set:
    cuerpo = re.search(r"\bSET\b(.*?)\bOUTPUT\b", sql, re.S | re.I)
    assert cuerpo, "El claim tiene que llevar OUTPUT (es de donde salen los lotes tomados)"
    return {m.group(1) for m in re.finditer(r"(\w+)\s*=", cuerpo.group(1))}


def _columnas_del_cte(sql: str) -> set:
    seleccion = re.search(r"SELECT\s+TOP\s*\([^)]*\)(.*?)\bFROM\b", sql, re.S | re.I)
    assert seleccion, "El CTE del claim tiene que ser un SELECT TOP (...)"
    return {c.strip() for c in seleccion.group(1).split(",") if c.strip()}


def test_el_cte_del_claim_expone_todo_lo_que_escribe():
    faltantes = _columnas_del_set(batch_cola._SQL_RECLAMAR_PENDIENTES) - _columnas_del_cte(
        batch_cola._SQL_RECLAMAR_PENDIENTES
    )
    assert not faltantes, (
        f"El UPDATE del CTE escribe {sorted(faltantes)} pero el SELECT del CTE no las "
        f"expone: SQL Server responde 'Invalid column name' al ejecutarlo."
    )


def test_el_claim_devuelve_todo_lo_que_el_despacho_necesita():
    """El OUTPUT es lo único que se lee del lote reclamado: si falta una columna, el
    tick manda el lote sin saber qué archivo subir o con qué modelo."""
    output = re.search(r"\bOUTPUT\b(.*)$", batch_cola._SQL_RECLAMAR_PENDIENTES, re.S | re.I)
    devueltas = {m.group(1) for m in re.finditer(r"INSERTED\.(\w+)", output.group(1))}
    assert {"LoteID", "Intentos", "ArchivoJsonl", "MetadataJson", "Modelo"} <= devueltas


# --------------------------------------------------------------------------- #
# 2. Código y migración tienen que hablar de las mismas columnas               #
# --------------------------------------------------------------------------- #
def _columnas_de_la_migracion() -> set:
    with open(MIGRACION, encoding="utf-8") as f:
        sql = f.read()
    cuerpo = re.search(r"CREATE TABLE calidad\.BatchPendientes\s*\((.*?)\n    \);", sql, re.S)
    assert cuerpo, "No se encontró el CREATE TABLE de calidad.BatchPendientes"
    columnas = set()
    for linea in cuerpo.group(1).splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("--"):
            continue
        m = re.match(r"(\w+)\s+\w", linea)
        if m:
            columnas.add(m.group(1))
    return columnas


def _columnas_usadas_por_el_codigo() -> set:
    """Columnas de BatchPendientes nombradas en el módulo (SELECT/SET/INSERT/WHERE)."""
    with open(MODULO, encoding="utf-8") as f:
        fuente = f.read()
    declaradas = _columnas_de_la_migracion()
    # Se buscan por nombre exacto dentro de los bloques SQL: alcanza para detectar el
    # caso que importa (usar una columna que la migración no crea) sin inventar un
    # parser de SQL.
    usadas = set()
    for bloque in re.findall(r'"""(.*?)"""', fuente, re.S) + [batch_cola._SQL_RECLAMAR_PENDIENTES]:
        if "BatchPendientes" not in bloque and "INSERTED." not in bloque:
            continue
        for palabra in re.findall(r"\b(\w+)\b", bloque):
            if palabra in declaradas:
                usadas.add(palabra)
    return usadas


def test_la_migracion_crea_todas_las_columnas_que_el_codigo_usa():
    usadas = _columnas_usadas_por_el_codigo()
    assert usadas, "No se detectó ninguna columna: el test dejó de validar algo"
    faltantes = usadas - _columnas_de_la_migracion()
    assert not faltantes, f"El código usa columnas que la migración no crea: {sorted(faltantes)}"


def test_la_migracion_es_idempotente():
    """Ignacio la corre a mano; correrla dos veces no puede romper nada."""
    with open(MIGRACION, encoding="utf-8") as f:
        sql = f.read()
    assert "IF OBJECT_ID('calidad.BatchPendientes', 'U') IS NULL" in sql


# --------------------------------------------------------------------------- #
# 3. Binding contra la base                                                    #
# --------------------------------------------------------------------------- #
# describe no admite parámetros: los binds se reemplazan por literales del tipo que
# corresponde (el objetivo es resolver nombres de columnas/tablas, no valores).
_LITERALES = {":limite": "20", ":id": "1", ":bytes": "0", ":llamados": "0",
              ":plantilla": "1", ":usuario": "1"}


def _concretar(sql: str) -> str:
    for bind, literal in _LITERALES.items():
        sql = sql.replace(bind, literal)
    return re.sub(r":\w+", "'x'", sql)


def _consultas_del_modulo():
    with open(MODULO, encoding="utf-8") as f:
        fuente = f.read()
    bloques = [b for b in re.findall(r'text\("""(.*?)"""\)', fuente, re.S)]
    bloques.append(batch_cola._SQL_RECLAMAR_PENDIENTES)
    return [(" ".join(b.split())[:60], b) for b in bloques]


CONSULTAS = _consultas_del_modulo()


def test_se_encontraron_las_consultas_del_modulo():
    """Si un refactor cambia la forma de escribir el SQL, este archivo dejaría de
    validar nada en silencio."""
    assert len(CONSULTAS) >= 6


@pytest.fixture(scope="session")
def tabla_creada(engine):
    """La tabla vive solo en la base y la migración la corre Ignacio a mano. Hasta que
    la corra, estos tests no pueden validar binding contra nada: se saltean con el
    motivo a la vista en vez de fallar en rojo por algo que todavía no pasó."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        existe = conn.execute(_text("SELECT OBJECT_ID('calidad.BatchPendientes', 'U')")).scalar()
    if existe is None:
        pytest.skip(
            "calidad.BatchPendientes no existe todavía: falta correr "
            "scripts/migrations/2026-08-26_batch_pendientes.sql"
        )
    return True


@pytest.mark.parametrize("resumen,sql", CONSULTAS, ids=[c[0] for c in CONSULTAS])
def test_las_consultas_del_modulo_bindean_contra_el_esquema(tabla_creada, validar_sql, resumen, sql):
    resultado = validar_sql(_concretar(sql))
    assert resultado.ok, f"{resumen}: {resultado.error}"
