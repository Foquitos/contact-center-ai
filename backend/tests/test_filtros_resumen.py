from app.utils.filtros_resumen import _construir_arbol_tipificaciones, _escapar_sql_in
from app.routers.planillas_prompts import FiltrosResumenRequest


def test_escapar_sql_in():
    assert _escapar_sql_in(["a", "b'c", "d"]) == "'a', 'b''c', 'd'"
    assert _escapar_sql_in([]) == "''"
    assert _escapar_sql_in(["", None]) == "''"


def test_construir_arbol_tipificaciones_jerarquico():
    tipis = [
        {"tipificacion": "Ventas -> Alta", "cantidad": 10},
        {"tipificacion": "Ventas -> Baja", "cantidad": 5},
        {"tipificacion": "Consultas -> Saldo", "cantidad": 20},
    ]
    arbol = _construir_arbol_tipificaciones(tipis, [" -> "])
    assert len(arbol) == 2

    # Consultas y Ventas ordenados alfabéticamente
    ventas = next(n for n in arbol if n["label"] == "Ventas")
    assert ventas["cantidad"] == 15
    assert len(ventas["children"]) == 2

    alta = next(n for n in ventas["children"] if n["label"] == "Alta")
    assert alta["cantidad"] == 10
    assert alta["value"] == "Ventas -> Alta"

    consultas = next(n for n in arbol if n["label"] == "Consultas")
    assert consultas["cantidad"] == 20


def test_construir_arbol_tipificaciones_tres_niveles_y_multiples_separadores():
    tipis = [
        {"tipificacion": "No obtuvo turno -> Clinicas - Moreno", "cantidad": 8},
        {"tipificacion": "No obtuvo turno -> Clinicas - Laferrere", "cantidad": 4},
        {"tipificacion": "No obtuvo turno -> Sin interes", "cantidad": 3},
        {"tipificacion": "Obtuvo turno -> Primera vez", "cantidad": 12},
    ]
    arbol = _construir_arbol_tipificaciones(tipis, [" -> ", " - "])
    assert len(arbol) == 2

    no_obtuvo = next(n for n in arbol if n["label"] == "No obtuvo turno")
    assert no_obtuvo["cantidad"] == 15
    assert len(no_obtuvo["children"]) == 2

    clinicas = next(n for n in no_obtuvo["children"] if n["label"] == "Clinicas")
    assert clinicas["cantidad"] == 12
    assert len(clinicas["children"]) == 2

    laferrere = next(n for n in clinicas["children"] if n["label"] == "Laferrere")
    assert laferrere["cantidad"] == 4
    assert laferrere["value"] == "No obtuvo turno -> Clinicas - Laferrere"


def test_filtros_resumen_request_model():
    req = FiltrosResumenRequest(
        fecha_desde="2026-09-01",
        fecha_hasta="2026-09-22",
        duracion_min=60,
        duracion_max=600,
        sentido=["Entrante"],
        loginid=["101", "102"],
        tipificacion=["Ventas -> Alta"],
        reauditar=False,
        comentario=["DNI"]
    )
    assert req.fecha_desde == "2026-09-01"
    assert req.fecha_hasta == "2026-09-22"
    assert req.duracion_min == 60
    assert req.duracion_max == 600
    assert req.sentido == ["Entrante"]
    assert req.loginid == ["101", "102"]
    assert req.comentario == ["DNI"]


def test_expandir_tipificaciones():
    from app.utils.filtros_resumen import _expandir_tipificaciones

    disponibles = [
        "HIDRA -> Agua -> Escape calzada o vereda",
        "HIDRA -> Agua -> Falta de agua",
        "HIDRA -> Comercial -> Facturacion",
    ]

    # Carpeta intermedia "HIDRA - Agua" debe expandir a las dos hojas de Agua
    res = _expandir_tipificaciones(["HIDRA - Agua"], disponibles)
    assert any("Escape calzada o vereda" in x for x in res)
    assert any("Falta de agua" in x for x in res)
    assert not any("Facturacion" in x for x in res)
    # Debe incluir variantes ' -> ' y ' - '
    assert "HIDRA -> Agua -> Falta de agua" in res
    assert "HIDRA - Agua - Falta de agua" in res

    # Si seleccionan hoja directa
    res_hoja = _expandir_tipificaciones(["HIDRA -> Comercial -> Facturacion"], disponibles)
    assert any("Facturacion" in x for x in res_hoja)
    assert not any("Falta de agua" in x for x in res_hoja)


def test_construir_arbol_farmalux_motivo_submotivo():
    tipis = [
        {"tipificacion": "Mi pedido esta demorado - Demora en la recepcion en PUP", "cantidad": 10},
        {"tipificacion": "Mi pedido esta demorado - Correo no paso", "cantidad": 5},
        {"tipificacion": "Otro", "cantidad": 50},
    ]
    arbol = _construir_arbol_tipificaciones(tipis, [" - "])
    assert len(arbol) == 2

    demorado = next(n for n in arbol if n["label"] == "Mi pedido esta demorado")
    assert demorado["cantidad"] == 15
    assert len(demorado["children"]) == 2

    otro = next(n for n in arbol if n["label"] == "Otro")
    assert otro["cantidad"] == 50
    assert len(otro["children"]) == 0



