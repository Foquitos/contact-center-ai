"""El detalle de "Costo por uso" del Resumen (GET /uso-ia/dashboard).

Hasta 2026-08-20 el costo por uso se veía solo en un doughnut chico: cuánto se
llevaba cada uso y nada más. Ahora la página muestra una tabla con la apertura
completa, y eso apoya en tres agregados del dashboard:

- `por_feature` con los tokens abiertos por tipo (entrada / salida /
  razonamiento) y el reparto sync-batch, porque el Batch cuesta la mitad por
  token y un promedio mezclado no es el precio de ninguno de los dos modos.
- `por_feature_modelo`, las filas hijas desplegables (qué modelo consume cada uso).
- `por_mes_feature`, la vista "por uso" del gráfico mensual apilado.

Lo que más fácil se rompe es el filtrado: los agregados nuevos tienen que llevar
el MISMO WHERE que el resto del Resumen (rango + grupo + segmentadores). Si a
uno se le escapa el segmentador, la página queda mostrando una tabla de toda la
empresa al lado de KPIs de una campaña, y no hay nada en pantalla que lo delate.

Test 100% offline: la BD se reemplaza por un doble que anota las SQL y responde
filas canned según el GROUP BY.
"""
from types import SimpleNamespace

import pytest

from app.routers import uso_ia


# --- Doble de BD -----------------------------------------------------------

class _Resultado:
    def __init__(self, filas):
        self._filas = filas

    def mappings(self):
        return self

    def all(self):
        return self._filas

    def fetchall(self):
        return self._filas

    def scalar(self):
        return 0


# Filas por uso: auditoría con los dos modos (el caso que importa) y asistente
# de documentación, sync puro.
FILAS_FEATURE = [
    {"feature": "auditoria", "llamadas": 30, "costo_usd": 300.0, "total_tokens": 3000,
     "input_tokens": 2000, "output_tokens": 400, "thoughts_tokens": 600,
     "llamadas_batch": 10, "costo_batch_usd": 50.0, "llamadas_sync": 20, "costo_sync_usd": 250.0},
    {"feature": "asistente_docs", "llamadas": 4, "costo_usd": 20.0, "total_tokens": 500,
     "input_tokens": 300, "output_tokens": 150, "thoughts_tokens": 50,
     "llamadas_batch": 0, "costo_batch_usd": 0.0, "llamadas_sync": 4, "costo_sync_usd": 20.0},
]

FILAS_FEATURE_MODELO = [
    {"feature": "auditoria", "modelo": "gemini-3.5-flash", "llamadas": 25, "costo_usd": 280.0,
     "total_tokens": 2500, "input_tokens": 1700, "output_tokens": 300, "thoughts_tokens": 500,
     "llamadas_batch": 10, "costo_batch_usd": 50.0, "llamadas_sync": 15, "costo_sync_usd": 230.0},
    {"feature": "auditoria", "modelo": "gemini-3-flash", "llamadas": 5, "costo_usd": 20.0,
     "total_tokens": 500, "input_tokens": 300, "output_tokens": 100, "thoughts_tokens": 100,
     "llamadas_batch": 0, "costo_batch_usd": 0.0, "llamadas_sync": 5, "costo_sync_usd": 20.0},
]

FILAS_MES_FEATURE = [
    {"anio_mes": "2026-08", "feature": "auditoria", "llamadas": 30, "costo_usd": 300.0},
    {"anio_mes": "2026-08", "feature": "asistente_docs", "llamadas": 4, "costo_usd": 20.0},
]


def _canned(sql: str):
    """Qué devuelve cada consulta, reconocida por su GROUP BY."""
    if "GROUP BY v.feature, v.modelo" in sql:
        return FILAS_FEATURE_MODELO
    if "GROUP BY v.feature" in sql:
        return FILAS_FEATURE
    if "GROUP BY v.anio_mes, v.feature" in sql:
        return FILAS_MES_FEATURE
    return []


class _Conexion:
    def __init__(self, ejecutadas):
        self._ejecutadas = ejecutadas

    def execute(self, query, params=None):
        sql = str(query)
        self._ejecutadas.append((sql, params or {}))
        return _Resultado(_canned(sql))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def bd(monkeypatch):
    ejecutadas: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        uso_ia, "engine", SimpleNamespace(connect=lambda: _Conexion(ejecutadas))
    )
    return ejecutadas


def _dashboard(**kwargs):
    opciones = dict(
        desde=None, hasta=None, grupo="auditorias", chatbot=None,
        empresa_id=None, campana_id=None, modelo=None, modo=None, usuario=None,
        current_user=SimpleNamespace(usuario=1, is_super_admin=False,
                                     permissions=[uso_ia.PERM_VIEW]),
    )
    opciones.update(kwargs)
    return uso_ia.dashboard(**opciones)


def _consultas(bd, fragmento):
    return [sql for sql, _ in bd if fragmento in sql]


# --- Tests -----------------------------------------------------------------

def test_por_feature_trae_tokens_abiertos_y_promedio_por_modo(bd):
    """La tabla necesita saber en qué se gastan los tokens de cada uso y cuánto
    sale una llamada en cada modo (el Batch es la mitad)."""
    d = _dashboard()

    auditoria = next(r for r in d["por_feature"] if r["feature"] == "auditoria")
    assert (auditoria["input_tokens"], auditoria["output_tokens"], auditoria["thoughts_tokens"]) == (2000, 400, 600)
    # 250 USD en 20 llamadas sync y 50 en 10 batch: el batch sale la mitad.
    assert auditoria["costo_prom_sync_usd"] == pytest.approx(12.5)
    assert auditoria["costo_prom_batch_usd"] == pytest.approx(5.0)

    # Un uso sin batch no promedia un modo que no existe (None != 0).
    docs = next(r for r in d["por_feature"] if r["feature"] == "asistente_docs")
    assert docs["costo_prom_batch_usd"] is None


def test_apertura_por_modelo_y_evolucion_mensual_por_uso(bd):
    """Filas hijas de la tabla + vista "por uso" del gráfico mensual."""
    d = _dashboard()

    modelos = [r["modelo"] for r in d["por_feature_modelo"] if r["feature"] == "auditoria"]
    assert modelos == ["gemini-3.5-flash", "gemini-3-flash"]
    assert d["por_feature_modelo"][0]["costo_prom_batch_usd"] == pytest.approx(5.0)

    assert {r["feature"] for r in d["por_mes_feature"]} == {"auditoria", "asistente_docs"}


def test_los_agregados_por_uso_respetan_los_segmentadores(bd):
    """Mismo WHERE que el resto del Resumen: rango + grupo + segmentadores. Sin
    esto la tabla de detalle contradice a los KPIs de arriba."""
    _dashboard(campana_id=7, modelo="gemini-3.5-flash", modo="batch")

    nuevas = (_consultas(bd, "GROUP BY v.feature")
              + _consultas(bd, "GROUP BY v.anio_mes, v.feature"))
    assert len(nuevas) == 3, "faltan las consultas por uso / uso×modelo / mes×uso"
    for sql in nuevas:
        assert "v.fecha >= :desde" in sql
        assert "v.feature <> 'chatbot'" in sql, "grupo=auditorias no puede incluir el chatbot"
        assert "v.campana_id = :campana_id" in sql
        assert "v.modelo = :modelo" in sql
        assert "v.modo = :modo" in sql
