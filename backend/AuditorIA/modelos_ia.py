"""Catálogo de modelos de Gemini seleccionables por plantilla.

Única fuente de verdad, del lado Python, de qué modelos puede elegir una
plantilla y su default. Los precios acá son solo para mostrarlos "lindo" en el
selector del editor de plantillas (nivel de inteligencia + $/1M tokens); el
costeo real de cada auditoría sigue viniendo de `pagina_web.IA_Precios` (ver
AuditorIA/execution_log.py::obtener_tarifa), que es la fuente de verdad para
reporting/facturación y admite vigencia por fecha.
"""

from __future__ import annotations

from typing import Any, Dict

MODELO_IA_DEFAULT = "gemini-3.8-flash"

MODELOS_IA: Dict[str, Dict[str, Any]] = {
    "gemini-3-flash-preview": {
        "label": "Económica",
        "nivel": 1,
        "icono": "bi-lightning-charge",
        "descripcion": "Menor costo, menor inteligencia. Ideal para auditorías simples de alto volumen.",
        "input_usd_mtok": 1.00,
        "output_usd_mtok": 3.00,
    },
    # 2026-09-02: gemini-3.8-flash reemplaza a "gemini-3.7-flash" en el nivel 2 (que a su
    # vez había absorbido a "gemini-3.6-flash" y a "gemini-3.1-pro-preview" el 2026-08-14).
    # Es el sucesor directo, al MISMO precio, así que el catálogo sigue en 2 opciones y el
    # costo por auditoría no se mueve.
    # Precio promocional hasta el 2026-12-31 (1.50/7.50 a partir del 2027-01-01); acordarse
    # de actualizarlo acá cuando cambie — el costeo real lo toma de pagina_web.IA_Precios,
    # que ya tiene ambas vigencias cargadas.
    "gemini-3.8-flash": {
        "label": "Estándar",
        "nivel": 2,
        "icono": "bi-gear",
        "descripcion": "Balance costo/calidad. Modelo por defecto de las plantillas existentes.",
        "input_usd_mtok": 0.75,
        "output_usd_mtok": 3.75,
    },
}


def catalogo_modelos_ia() -> list[Dict[str, Any]]:
    """Catálogo como lista (para exponer por API), con el `modelo` incluido en cada item."""
    return [{"modelo": modelo, **info} for modelo, info in MODELOS_IA.items()]


def modelo_valido(modelo: str) -> bool:
    return modelo in MODELOS_IA
