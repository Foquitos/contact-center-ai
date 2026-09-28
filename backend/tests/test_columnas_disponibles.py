"""Qué columnas ofrece el panel de "Auditorías Realizadas" (y las plantillas de columnas).

El panel deja prender/apagar y reordenar por drag las columnas que devuelve
`GET /Auditoria/auditorias/columnas`; lo que no está ahí, no se puede configurar ni
guardar en una plantilla de columnas.

`PuntajeFinal` y `EsErrorCritico` se ofrecían SOLO en plantillas con atributos de
"Calidad ponderada" (`critical_audit`), pero el SP las devuelve siempre. En una
plantilla sin ponderación (Voltara) eso daba el peor de los dos mundos: salían en la
grilla y en las descargas, y no había forma de sacarlas ni moverlas.

Test 100% offline: no toca DB, Gemini ni tokens.
Correr: pytest tests/test_columnas_disponibles.py -m "not tokens"
"""
from contextlib import contextmanager

import pytest

from app.routers import auditoria as router


# Plantilla real de Voltara: ocho atributos enum, ninguno ponderado.
ATRIBUTOS_VOLTARA = [
    {"nombre": "Empatía", "tipo": "enum", "orden": 0},
    {"nombre": "Amabilidad y cordialidad", "tipo": "enum", "orden": 1},
    {"nombre": "Bienvenida/Despedida", "tipo": "enum", "orden": 6},
    {"nombre": "Corte de la comunicación", "tipo": "enum", "orden": 7},
]

ATRIBUTOS_PONDERADOS = [
    {"nombre": "saludo", "tipo": "critical_audit", "orden": 0},
    {"nombre": "cierre", "tipo": "critical_audit", "orden": 1},
]


class _EngineFalso:
    @contextmanager
    def connect(self):
        yield None


@pytest.fixture
def columnas(monkeypatch):
    """Llama al endpoint con la plantilla y el estado de migración que se le pidan."""
    def _pedir(atributos, con_incidencia=True):
        monkeypatch.setattr(router, "engine", _EngineFalso())
        monkeypatch.setattr(router, "exigir_acceso_empresa", lambda *a, **k: None)
        monkeypatch.setattr(router, "_hay_columna_incidencia", lambda: con_incidencia)
        monkeypatch.setattr(
            router.plantillas_manager_instance, "obtener_plantilla",
            lambda plantilla_id: {"atributos": atributos},
        )
        return router.descubrir_columnas_auditoria(plantilla=1, current_user=None)["columns"]
    return _pedir


def test_una_plantilla_sin_ponderacion_igual_puede_configurar_el_puntaje(columnas):
    """La regresión: el SP devuelve PuntajeFinal siempre, así que siempre se tiene que
    poder apagar o mover. En Voltara viene NULL en todas las filas y hasta ahora no había
    manera de sacarla de la grilla ni del Excel."""
    cols = columnas(ATRIBUTOS_VOLTARA)
    assert "PuntajeFinal" in cols
    assert "EsErrorCritico" in cols


def test_la_incidencia_se_puede_prender_apagar_y_ordenar(columnas):
    cols = columnas(ATRIBUTOS_VOLTARA)
    assert "Incidencia" in cols


def test_sin_la_migracion_aplicada_no_se_ofrece_la_incidencia(columnas):
    """Si el SP todavía no devuelve la columna, ofrecerla sería un chip que no hace nada."""
    cols = columnas(ATRIBUTOS_VOLTARA, con_incidencia=False)
    assert "Incidencia" not in cols
    assert "PuntajeFinal" in cols


def test_las_de_resultado_van_juntas_y_al_final(columnas):
    """Orden por defecto del panel: identificación, atributos de la plantilla y recién
    después el bloque de resultado. El usuario lo reordena arrastrando."""
    cols = columnas(ATRIBUTOS_VOLTARA)
    assert cols[-3:] == ["PuntajeFinal", "Incidencia", "EsErrorCritico"]
    assert cols.index("Empatía") < cols.index("PuntajeFinal")


def test_los_atributos_respetan_el_orden_de_la_plantilla(columnas):
    cols = columnas(ATRIBUTOS_VOLTARA)
    assert cols.index("Empatía") < cols.index("Bienvenida/Despedida") < cols.index("Corte de la comunicación")


def test_una_plantilla_ponderada_sigue_ofreciendo_todo(columnas):
    """No se le saca nada a las plantillas que sí usan Calidad ponderada."""
    cols = columnas(ATRIBUTOS_PONDERADOS)
    for esperada in ("PuntajeFinal", "EsErrorCritico", "Incidencia", "saludo", "cierre"):
        assert esperada in cols


def test_las_columnas_base_siguen_estando(columnas):
    cols = columnas(ATRIBUTOS_VOLTARA)
    for esperada in ("AuditoriaID", "IdAplicativo", "operadorUsuario", "Agente", "FechaAuditoria"):
        assert esperada in cols
