"""
Expansión de las ramas del árbol de tipificaciones (Mitrol/Wize).

Reporte de Magali (2026-08-20, Odonto Plus): tildar la carpeta intermedia
"Agenda completa-Clinicas Dental" y pedir del 14 al 18/08 devolvía 0 llamados,
aunque en `detalle_de_interacciones_por_campana_lote` había 4 (Laferrere, Moreno,
Monte Grande, Munro). El árbol manda el nodo intermedio con la ruta unida por
' - ' mientras que la DB guarda ' -> ', así que la expansión por prefijo no pegaba
y el filtro terminaba buscando por IN un string que no existe.

100% offline: sólo ejercita las funciones puras de app/utils/tipificaciones.py.
"""
import pytest

from app.utils.tipificaciones import (
    _coincide_rama,
    aplicar_variantes_separador,
    expandir_seleccion_mitrol,
)


DENTAL = [
    "No obtuvo turno -> Agenda completa-Clinicas Dental -> Laferrere",
    "No obtuvo turno -> Agenda completa-Clinicas Dental -> Moreno",
    "No obtuvo turno -> Agenda completa-Clinicas Dental -> Monte Grande",
    "No obtuvo turno -> Agenda completa-Clinicas Dental -> Munro",
    "No obtuvo turno -> Sin interes",
    "Obtuvo turno -> Primera vez",
]


def _expandir(seleccion, universo=DENTAL):
    """Corre sólo el matching (sin DB) tal como lo hace expandir_seleccion_mitrol."""
    expandida = set()
    for item in seleccion:
        matches = [t for t in universo if _coincide_rama(t, item)]
        expandida.update(matches or [item])
    return expandida


def test_rama_intermedia_con_separadores_distintos_expande_a_las_hojas():
    # Lo que manda el árbol al tildar la carpeta de nivel 2.
    seleccion = ["No obtuvo turno - Agenda completa-Clinicas Dental"]
    assert _expandir(seleccion) == set(DENTAL[:4])


def test_rama_de_nivel_1_sigue_expandiendo():
    assert _expandir(["No obtuvo turno"]) == set(DENTAL[:5])


def test_hoja_exacta_se_mantiene():
    hoja = "No obtuvo turno -> Agenda completa-Clinicas Dental -> Munro"
    assert _expandir([hoja]) == {hoja}


def test_no_arrastra_hermanos_con_nombre_parecido():
    universo = ["Venta -> Alta", "Ventas -> Alta", "Ventas -> Baja"]
    assert _expandir(["Venta"], universo) == {"Venta -> Alta"}


def test_seleccion_sin_coincidencias_cae_al_literal():
    assert _expandir(["Tipificación vieja"]) == {"Tipificación vieja"}


def test_variantes_de_separador_incluyen_ambos_formatos():
    variantes = aplicar_variantes_separador(["No obtuvo turno - Agenda completa"])
    assert "No obtuvo turno -> Agenda completa" in variantes
    assert "No obtuvo turno - Agenda completa" in variantes


    assert expandir_seleccion_mitrol(None, "no-es-un-id", []) == []


def test_rama_con_prefijo_campana_hidra_expande_correctamente():
    hidra_universo = [
        "HIDRA -> Agua -> Falta de agua",
        "HIDRA -> Agua -> Falta de presion",
        "HIDRA -> Agua -> Escape calzada o vereda",
        "HIDRA -> Cloaca -> Taponamiento con desborde",
        "HIDRA -> Cloaca -> Taponamiento sin desborde",
        "HIDRA -> Consulta -> Consulta tecnica",
        "Agua",
        "Cloaca",
        "Falta de agua",
    ]
    # Seleccionando la sub-carpeta "Agua" (sin el prefijo "HIDRA")
    assert _expandir(["Agua"], hidra_universo) == {
        "HIDRA -> Agua -> Falta de agua",
        "HIDRA -> Agua -> Falta de presion",
        "HIDRA -> Agua -> Escape calzada o vereda",
        "Agua",
    }

    # Seleccionando la hoja "Falta de agua"
    assert _expandir(["Falta de agua"], hidra_universo) == {
        "HIDRA -> Agua -> Falta de agua",
        "Falta de agua",
    }

    # Seleccionando sub-ruta "Agua - Falta de agua"
    assert _expandir(["Agua - Falta de agua"], hidra_universo) == {
        "HIDRA -> Agua -> Falta de agua",
    }

    # Seleccionando lista de tipificaciones del caso real reportado
    seleccion_usuario = [
        "Agua", "Cloaca", "Escape calzada o vereda", "Falta de agua",
        "Falta de presion", "Taponamiento con desborde", "Taponamiento sin desborde",
    ]
    expandido = _expandir(seleccion_usuario, hidra_universo)
    assert "HIDRA -> Agua -> Falta de agua" in expandido
    assert "HIDRA -> Cloaca -> Taponamiento con desborde" in expandido
    assert "HIDRA -> Agua -> Escape calzada o vereda" in expandido
    assert "HIDRA -> Consulta -> Consulta tecnica" not in expandido



BENEFIX = [
    "MDF - Inconveniente con plataforma",
    "MDF- Solicitud de Evento",
    "MDF- Consulta, Modificación o Cancelación de Turno",
    "MOB - ABM de Comercios",
    "Llamada caída",
]


def test_benefix_grupo_incluye_los_wrapups_sin_espacio_antes_del_guion():
    """Genesys tiene códigos "MDF- ..." (sin espacio). El árbol los cuelga de "MDF", así
    que tildar el grupo tiene que traerlos junto con los "MDF - ..."."""
    assert _expandir(["MDF"], BENEFIX) == set(BENEFIX[:3])


def test_benefix_hoja_sin_espacio_se_mantiene_exacta():
    assert _expandir(["MDF- Solicitud de Evento"], BENEFIX) == {"MDF- Solicitud de Evento"}


def test_guion_pegado_no_es_separador():
    assert not _coincide_rama("Agenda completa-Clinicas Dental", "Agenda completa")
