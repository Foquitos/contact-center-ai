"""
Alta de atributos de una plantilla (plantillas_manager.crear_atributos_plantilla).

Cubre el bug de la llamada al SP dentro de un for: sp_AgregarAtributosAPlantilla hace
un INSERT ... SELECT FROM OPENJSON con la lista COMPLETA, así que llamarlo una vez por
atributo (pasándole siempre la lista entera) insertaba N copias de cada uno. No saltaba
porque el editor crea los atributos de a uno; sí lo haría el alta de una plantilla
generada por IA si alguna vez manda el lote junto.

También cubre los campos que el SP de alta no persiste y completa el manager
(DarAviso/FrasesAviso y EsOpcional).

Test 100% offline: no toca DB, Gemini ni tokens.
"""
import json

import pytest

from AuditorIA.Plantillas_prompts import plantillas_manager


class _FakeResult:
    def __init__(self, filas):
        self._filas = filas

    def first(self):
        return self._filas[0] if self._filas else None

    def fetchall(self):
        return self._filas

    def fetchone(self):
        return self.first()


class _FakeConnection:
    """Registra (sql, params) de cada execute. Devuelve filas según lo que se consulte."""

    def __init__(self, tiene_es_opcional=True):
        self.ejecutados = []
        self.tiene_es_opcional = tiene_es_opcional

    def execute(self, statement, params=None):
        sql = str(statement)
        self.ejecutados.append((sql, params or {}))
        if "INFORMATION_SCHEMA.COLUMNS" in sql:
            return _FakeResult([(1,)] if self.tiene_es_opcional else [])
        return _FakeResult([])

    # Context manager de engine.begin()
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self, connection):
        self._connection = connection

    def begin(self):
        return self._connection


def _manager(tiene_es_opcional=True):
    conn = _FakeConnection(tiene_es_opcional=tiene_es_opcional)
    return plantillas_manager(_FakeEngine(conn)), conn


def _llamadas_al_sp(conn):
    return [(sql, p) for sql, p in conn.ejecutados if "sp_AgregarAtributosAPlantilla" in sql]


def _updates(conn):
    return [(sql, p) for sql, p in conn.ejecutados if sql.strip().upper().startswith("UPDATE")]


ATRIBUTOS = [
    {"nombre": "Saludo", "prompt": "¿Saludó?", "tipo": "boolean", "restricciones": None,
     "orden": 0, "DarAviso": False, "FrasesAviso": None, "ponderacion": 0, "es_opcional": True},
    {"nombre": "Cierre", "prompt": "¿Cerró bien?", "tipo": "critical_audit",
     "restricciones": {"enum": ["OK", "NO OK", "EC"]}, "orden": 1,
     "DarAviso": True, "FrasesAviso": "insulto, amenaza", "ponderacion": 30},
]


def test_el_sp_se_llama_una_sola_vez_con_la_lista_completa():
    """El bug: se llamaba una vez por atributo con el JSON entero -> N duplicados."""
    manager, conn = _manager()
    manager.crear_atributos_plantilla(7, ATRIBUTOS)

    llamadas = _llamadas_al_sp(conn)
    assert len(llamadas) == 1

    _, params = llamadas[0]
    assert params["plantilla_id"] == 7
    enviados = json.loads(params["atributos_json"])
    assert [a["nombre"] for a in enviados] == ["Saludo", "Cierre"]


def test_persiste_los_campos_que_el_sp_de_alta_ignora():
    """DarAviso/FrasesAviso no entran en el INSERT del SP: los completa el manager."""
    manager, conn = _manager()
    manager.crear_atributos_plantilla(7, ATRIBUTOS)

    updates = _updates(conn)
    sql_cierre = next(sql for sql, p in updates if p.get("n") == "Cierre")
    params_cierre = next(p for sql, p in updates if p.get("n") == "Cierre")

    assert "DarAviso = :av" in sql_cierre and params_cierre["av"] == 1
    assert "FrasesAviso = :fr" in sql_cierre and params_cierre["fr"] == "insulto, amenaza"
    assert "Ponderacion = :p" in sql_cierre and params_cierre["p"] == 30.0


def test_marca_es_opcional_solo_en_el_atributo_que_lo_pidio():
    manager, conn = _manager()
    manager.crear_atributos_plantilla(7, ATRIBUTOS)

    updates = _updates(conn)
    sql_saludo = next(sql for sql, p in updates if p.get("n") == "Saludo")
    sql_cierre = next(sql for sql, p in updates if p.get("n") == "Cierre")

    assert "EsOpcional = 1" in sql_saludo
    assert "EsOpcional" not in sql_cierre


def test_sin_la_migracion_no_se_escribe_es_opcional():
    """Degradación: si calidad.Atributos.EsOpcional no existe, el alta no rompe."""
    manager, conn = _manager(tiene_es_opcional=False)
    manager.crear_atributos_plantilla(7, ATRIBUTOS)

    assert len(_llamadas_al_sp(conn)) == 1
    assert all("EsOpcional" not in sql for sql, _ in _updates(conn))


def test_lista_vacia_no_toca_la_base():
    manager, conn = _manager()
    manager.crear_atributos_plantilla(7, [])

    assert conn.ejecutados == []
