"""
El SQL de la cola de transcripciones, validado contra el esquema REAL.

POR QUÉ EXISTE ESTE ARCHIVO
---------------------------
El 2026-08-25 el despacho de la cola se cayó entero en producción con
"Invalid column name 'FechaEnvio'". La columna existía: el problema es que un UPDATE
sobre un CTE **solo puede escribir las columnas que el CTE expone en su SELECT**, y
`FechaEnvio` se agregó al SET sin agregarla arriba.

Lo que no lo atrapó: `SET PARSEONLY ON` valida SINTAXIS, no binding, y esa consulta es
sintácticamente perfecta. Lo que sí lo atrapa:

  1. `test_el_cte_del_claim_expone_todo_lo_que_escribe` — sin base, siempre corre.
  2. `test_las_consultas_del_modulo_bindean_contra_el_esquema` — con
     `sys.dm_exec_describe_first_result_set` (la fixture `validar_sql` de conftest), que
     resuelve nombres de columnas y tablas SIN ejecutar nada.

No escribe ni ejecuta ninguna sentencia, no gasta tokens.
"""
import os
import re

import pytest

from AuditorIA import transcripcion_cola as cola

MODULO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "AuditorIA", "transcripcion_cola.py")


def _columnas_del_set(sql: str) -> set:
    """Columnas que el UPDATE escribe (`SET a = ..., b = ...`)."""
    cuerpo = re.search(r"\bSET\b(.*?)\bOUTPUT\b", sql, re.S | re.I)
    assert cuerpo, "El claim tiene que llevar OUTPUT (es de donde salen los jobs tomados)"
    return {m.group(1) for m in re.finditer(r"(\w+)\s*=", cuerpo.group(1))}


def _columnas_del_cte(sql: str) -> set:
    """Columnas que el CTE expone (las únicas que el UPDATE puede tocar)."""
    seleccion = re.search(r"SELECT\s+TOP\s*\([^)]*\)(.*?)\bFROM\b", sql, re.S | re.I)
    assert seleccion, "El CTE del claim tiene que ser un SELECT TOP (...)"
    return {c.strip() for c in seleccion.group(1).split(",") if c.strip()}


def test_el_cte_del_claim_expone_todo_lo_que_escribe():
    """La regresión exacta: SET FechaEnvio con el CTE sin FechaEnvio = 207 en runtime."""
    sql = cola._SQL_RECLAMAR_PENDIENTES
    faltantes = _columnas_del_set(sql) - _columnas_del_cte(sql)
    assert not faltantes, (
        f"El UPDATE del CTE escribe {sorted(faltantes)} pero el SELECT del CTE no las "
        f"expone: SQL Server responde 'Invalid column name' al ejecutarlo."
    )


# --------------------------------------------------------------------------- #
# Binding real contra la base                                                  #
# --------------------------------------------------------------------------- #

# describe no admite parámetros: los binds se reemplazan por literales del tipo que
# corresponde (el objetivo es resolver nombres de columnas/tablas, no valores).
_LITERALES = {
    ":limite": "20", ":dias": "-3", ":minutos": "-30", ":segmento": "0",
    ":input": "1", ":output": "2", ":thoughts": "3", ":jid": "1", ":user_id": "1",
}


def _concretar(sql: str) -> str:
    sql = sql.replace("IN :ids", "IN (1,2)").replace("IN :estados", "IN ('PENDIENTE')")
    for bind, literal in _LITERALES.items():
        sql = sql.replace(bind, literal)
    return re.sub(r":\w+", "'x'", sql)   # el resto son strings


def _consultas_del_modulo():
    """Todas las sentencias `text(\"\"\"...\"\"\")` del módulo, más el claim (que vive en
    una constante). Recorrer el fuente y no una lista a mano es a propósito: una consulta
    nueva queda cubierta sin que nadie se acuerde de sumarla acá."""
    with open(MODULO, encoding="utf-8") as f:
        fuente = f.read()
    bloques = re.findall(r'text\("""(.*?)"""\)', fuente, re.S)
    bloques.append(cola._SQL_RECLAMAR_PENDIENTES)
    return [(" ".join(b.split())[:60], b) for b in bloques]


CONSULTAS = _consultas_del_modulo()


def test_se_encontraron_las_consultas_del_modulo():
    """Si un refactor cambia la forma de escribir el SQL, este archivo dejaría de validar
    nada en silencio."""
    assert len(CONSULTAS) >= 10


@pytest.mark.parametrize("resumen,sql", CONSULTAS, ids=[c[0] for c in CONSULTAS])
def test_las_consultas_del_modulo_bindean_contra_el_esquema(validar_sql, resumen, sql):
    resultado = validar_sql(_concretar(sql))
    assert resultado.ok, f"{resumen}: {resultado.error}"
