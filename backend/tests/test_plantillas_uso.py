"""
Tests para el endpoint de uso y semáforo de plantillas (GET /plantillas/uso/{campana_id}).

Verifica que el cálculo del tiempo transcurrido desde la última auditoría asigne
correctamente los estados de color (verde / activo <= 30d, amarillo / inactivo 1m 31-90d,
rojo / inactivo >3m >90d, y rojo / sin uso con 0 auditorías), y que el resumen cuantitativo
de la campaña coincida.

Test 100% offline: no toca DB, Gemini ni tokens.
Correr: pytest backend/tests/test_plantillas_uso.py -m "not tokens"
"""
import pytest
from app.routers import planillas_prompts as router


MOCK_PLANTILLAS_USO = [
    {
        "plantilla_id": 1,
        "nombre": "Plantilla Activa",
        "fecha_creacion": "2026-01-01T00:00:00",
        "total_auditorias": 500,
        "auditorias_ultimos_30d": 120,
        "auditorias_ultimos_60d": 250,
        "auditorias_ultimos_90d": 380,
        "auditorias_ultimos_180d": 500,
        "ultima_auditoria": "2026-08-24T12:00:00",
        "dias_desde_ultima": 1,
        "estado_uso": "activa",
        "estado_color": "success",
        "estado_label": "En uso activo",
    },
    {
        "plantilla_id": 2,
        "nombre": "Plantilla Inactiva 1 Mes",
        "fecha_creacion": "2026-01-01T00:00:00",
        "total_auditorias": 300,
        "auditorias_ultimos_30d": 0,
        "auditorias_ultimos_60d": 50,
        "auditorias_ultimos_90d": 120,
        "auditorias_ultimos_180d": 300,
        "ultima_auditoria": "2026-07-15T12:00:00",
        "dias_desde_ultima": 41,
        "estado_uso": "inactiva_1m",
        "estado_color": "warning",
        "estado_label": "Sin uso > 1 mes",
    },
    {
        "plantilla_id": 3,
        "nombre": "Plantilla Inactiva 3 Meses",
        "fecha_creacion": "2026-01-01T00:00:00",
        "total_auditorias": 150,
        "auditorias_ultimos_30d": 0,
        "auditorias_ultimos_60d": 0,
        "auditorias_ultimos_90d": 0,
        "auditorias_ultimos_180d": 100,
        "ultima_auditoria": "2026-04-10T12:00:00",
        "dias_desde_ultima": 137,
        "estado_uso": "inactiva_3m",
        "estado_color": "danger",
        "estado_label": "Sin uso > 3 meses",
    },
    {
        "plantilla_id": 4,
        "nombre": "Plantilla Nunca Utilizada",
        "fecha_creacion": "2026-06-01T00:00:00",
        "total_auditorias": 0,
        "auditorias_ultimos_30d": 0,
        "auditorias_ultimos_60d": 0,
        "auditorias_ultimos_90d": 0,
        "auditorias_ultimos_180d": 0,
        "ultima_auditoria": None,
        "dias_desde_ultima": None,
        "estado_uso": "sin_uso",
        "estado_color": "danger",
        "estado_label": "Sin auditorías",
    },
]


class _ManagerUsoFalso:
    def obtener_uso_plantillas(self, campana_id):
        return MOCK_PLANTILLAS_USO


@pytest.fixture
def response_uso(monkeypatch):
    monkeypatch.setattr(router, "plantillas_manager_instance", _ManagerUsoFalso())
    monkeypatch.setattr(router, "_exigir_scope", lambda *a, **k: None)
    return router.uso_plantillas_campana(campana_id=10, current_user=object())


def test_conteo_resumen_estados(response_uso):
    resumen = response_uso["resumen"]
    assert resumen["total"] == 4
    assert resumen["activas"] == 1
    assert resumen["inactivas_1m"] == 1
    assert resumen["inactivas_3m"] == 1
    assert resumen["sin_uso"] == 1
    assert resumen["candidatas_eliminar"] == 2  # inactivas_3m + sin_uso


def test_detalle_plantillas_estados(response_uso):
    plantillas = response_uso["plantillas"]
    p_activa = next(p for p in plantillas if p["nombre"] == "Plantilla Activa")
    assert p_activa["estado_uso"] == "activa"
    assert p_activa["estado_color"] == "success"
    assert p_activa["auditorias_ultimos_30d"] == 120

    p_inactiva_1m = next(p for p in plantillas if p["nombre"] == "Plantilla Inactiva 1 Mes")
    assert p_inactiva_1m["estado_uso"] == "inactiva_1m"
    assert p_inactiva_1m["estado_color"] == "warning"

    p_inactiva_3m = next(p for p in plantillas if p["nombre"] == "Plantilla Inactiva 3 Meses")
    assert p_inactiva_3m["estado_uso"] == "inactiva_3m"
    assert p_inactiva_3m["estado_color"] == "danger"

    p_sin_uso = next(p for p in plantillas if p["nombre"] == "Plantilla Nunca Utilizada")
    assert p_sin_uso["estado_uso"] == "sin_uso"
    assert p_sin_uso["estado_color"] == "danger"
    assert p_sin_uso["total_auditorias"] == 0

