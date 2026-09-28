"""
Validador de la configuración de visualización del dashboard (bandeja).

`_validar_viz_config` es la puerta de entrada de un JSON que escribe el frontend y
que después se guarda tal cual en calidad.BandejaDashboards: todo lo que no
reconozca tiene que descartarlo en vez de persistirlo.

Foco de este archivo: la categoría "Sin respuesta" (los llamados en los que el
atributo quedó sin responder, típico de los atributos opcionales), que se
configura a dos niveles — default del dashboard y excepción por atributo.

Test 100% offline: no toca DB ni IA.
"""
import pytest

from app.routers.bandeja import _validar_viz_config


def test_default_del_dashboard_se_guarda():
    out = _validar_viz_config({"sin_respuesta": True})
    assert out["sin_respuesta"] is True


def test_sin_la_clave_no_se_inventa_el_default():
    """Ausente = conducta previa (no se dibuja): no queremos que aparezca sola en
    los dashboards ya configurados."""
    out = _validar_viz_config({"atributos": {}})
    assert "sin_respuesta" not in out


def test_excepcion_por_atributo_en_los_dos_sentidos():
    out = _validar_viz_config({
        "sin_respuesta": True,
        "atributos": {
            "Ofreció promo": {"mostrar_sin_respuesta": False},
            "Verificó identidad": {"mostrar_sin_respuesta": True},
        },
    })
    assert out["atributos"]["Ofreció promo"]["mostrar_sin_respuesta"] is False
    assert out["atributos"]["Verificó identidad"]["mostrar_sin_respuesta"] is True


def test_atributo_sin_la_clave_hereda():
    """Sin la clave, el atributo sigue al dashboard: por eso no se guarda un default."""
    out = _validar_viz_config({"atributos": {"Saludo": {"alias": "Saludo inicial"}}})
    assert "mostrar_sin_respuesta" not in out["atributos"]["Saludo"]


@pytest.mark.parametrize("valor,esperado", [(1, True), (0, False), ("", False), ("x", True)])
def test_valores_no_booleanos_se_coercionan(valor, esperado):
    """El front manda checkboxes; cualquier cosa rara entra como bool, no rompe."""
    out = _validar_viz_config({"sin_respuesta": valor})
    assert out["sin_respuesta"] is esperado


def test_no_rompe_lo_que_ya_habia():
    """Regresión: la clave nueva no se come el resto de la config."""
    out = _validar_viz_config({
        "sin_respuesta": True,
        "kpis": ["total", "puntaje"],
        "orden_atributos": ["B", "A"],
        "atributos": {"A": {"visible": False, "valores_ocultos": ["N/A"], "mostrar_sin_respuesta": True}},
    })
    assert out["kpis"] == ["total", "puntaje"]
    assert out["orden_atributos"] == ["B", "A"]
    assert out["atributos"]["A"]["visible"] is False
    assert out["atributos"]["A"]["valores_ocultos"] == ["N/A"]
    assert out["atributos"]["A"]["mostrar_sin_respuesta"] is True
