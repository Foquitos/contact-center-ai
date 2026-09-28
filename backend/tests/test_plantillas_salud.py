"""
Chequeo de salud de las plantillas de una campaña (GET /plantillas/salud/{campana_id}).

Las mismas señales que la revisión le pasa masticadas al modelo sirven solas como
diagnóstico, sin llamar a Gemini: un enum sin salida segura, un atributo de calidad con
peso 0 o dos atributos con el mismo nombre son problemas verificables. Correrlas sobre
la campaña entera contesta "¿cuál de mis plantillas está rota?" sin gastar un token.

Lo que se prueba: que las plantillas queden ordenadas de peor a mejor (la lista se lee
de arriba hacia abajo), que la severidad se calcule por la peor señal y que una
plantilla sana no invente problemas.

Test 100% offline: no toca DB, Gemini ni tokens.
Correr: pytest tests/test_plantillas_salud.py -m "not tokens"
"""
import pytest

from app.routers import planillas_prompts as router


SANA = [
    {"id": 1, "nombre": "Cierre", "tipo": "critical_audit", "orden": 0, "ponderacion": 100,
     "restricciones": {"enum": ["OK", "NO OK", "EC", "N/A"]}, "es_opcional": False,
     "prompt": "Evaluá si el operador cerró la gestión dejando el próximo paso claro. " * 3},
]

ROTA = [
    # Lista sin salida segura (alta) + prompt corto (media).
    {"id": 2, "nombre": "Motivo", "tipo": "enum", "orden": 0, "ponderacion": 0,
     "restricciones": {"enum": ["A", "B"]}, "es_opcional": False, "prompt": "El motivo."},
    # Calidad ponderada con peso 0 (alta) y sin N/A (media).
    {"id": 3, "nombre": "Saludo", "tipo": "critical_audit", "orden": 1, "ponderacion": 0,
     "restricciones": {"enum": ["OK", "NO OK"]}, "es_opcional": False,
     "prompt": "Evaluá si el operador saludó identificándose con nombre y empresa. " * 3},
]

FLOJA = [
    # Solo señales medias: un Si/No que la IA no puede omitir.
    {"id": 4, "nombre": "Ofreció promo", "tipo": "boolean", "orden": 0, "ponderacion": 0,
     "restricciones": None, "es_opcional": False,
     "prompt": "Indicá si el operador ofreció la promoción vigente al cliente. " * 3},
]

PLANTILLAS = {10: "Sana", 11: "Rota", 12: "Floja"}
ATRIBUTOS = {10: SANA, 11: ROTA, 12: FLOJA}


class _ManagerFalso:
    def listar_plantillas(self, campana_id):
        return PLANTILLAS

    def obtener_plantilla(self, plantilla_id):
        return {"id": plantilla_id, "nombre": PLANTILLAS[plantilla_id],
                "atributos": ATRIBUTOS[plantilla_id]}


@pytest.fixture
def salud(monkeypatch):
    monkeypatch.setattr(router, "plantillas_manager_instance", _ManagerFalso())
    monkeypatch.setattr(router, "_exigir_scope", lambda *a, **k: None)
    return router.salud_plantillas_campana(campana_id=1, current_user=object())


def _por_nombre(salud, nombre):
    return next(p for p in salud["plantillas"] if p["nombre"] == nombre)


def test_la_peor_plantilla_va_primero(salud):
    assert [p["nombre"] for p in salud["plantillas"]] == ["Rota", "Floja", "Sana"]


def test_la_severidad_la_manda_la_peor_senal(salud):
    assert _por_nombre(salud, "Rota")["estado"] == "alta"
    assert _por_nombre(salud, "Floja")["estado"] == "media"
    assert _por_nombre(salud, "Sana")["estado"] == "ok"


def test_una_plantilla_sana_no_inventa_problemas(salud):
    sana = _por_nombre(salud, "Sana")
    assert sana["senales"] == [] and sana["altas"] == 0 and sana["medias"] == 0


def test_cada_senal_apunta_a_su_atributo(salud):
    rota = _por_nombre(salud, "Rota")
    claves = {(s["clave"], s["atributo_id"]) for s in rota["senales"]}
    assert ("enum_sin_salida_segura", 2) in claves
    assert ("critical_sin_peso", 3) in claves


def test_el_resumen_cuenta_las_que_hay_que_mirar(salud):
    assert salud["resumen"] == {"total": 3, "con_problemas": 2, "criticas": 1}


# --------------------------------------------------------------------------- #
# Señales de UNA plantilla (semáforo del editor)                               #
# --------------------------------------------------------------------------- #
# El mismo diagnóstico, pero agrupado por atributo y sobre la plantilla abierta: el
# analista tiene que ver el problema mientras la arma, no cuando alguien abre el chequeo
# de la campaña tres semanas después.
@pytest.fixture
def senales(monkeypatch):
    monkeypatch.setattr(router, "plantillas_manager_instance", _ManagerFalso())
    monkeypatch.setattr(router, "_exigir_scope", lambda *a, **k: None)
    return router.senales_de_plantilla(plantilla_id=11, current_user=object())


def test_las_senales_vienen_agrupadas_por_atributo(senales):
    """El editor pinta un punto por atributo: necesita poder buscar por id sin recorrer
    la lista entera ni volver a parsear el mensaje."""
    assert set(senales["por_atributo"]) == {"2", "3"}
    claves = {s["clave"] for s in senales["por_atributo"]["2"]}
    assert "enum_sin_salida_segura" in claves


def test_lo_que_no_es_de_ningun_atributo_va_aparte(senales):
    """Los pesos que no suman 100 son de la plantilla, no de un atributo: pintarlos en
    uno cualquiera sería mentir sobre dónde está el problema."""
    assert all(s["atributo_id"] is None for s in senales["generales"])


def test_el_resumen_separa_lo_que_frena_de_lo_que_solo_avisa(senales):
    assert senales["resumen"]["altas"] == 2
    assert senales["resumen"]["medias"] == len(senales["senales"]) - 2


def test_una_plantilla_sana_no_tiene_senales(monkeypatch):
    monkeypatch.setattr(router, "plantillas_manager_instance", _ManagerFalso())
    monkeypatch.setattr(router, "_exigir_scope", lambda *a, **k: None)
    limpia = router.senales_de_plantilla(plantilla_id=10, current_user=object())
    assert limpia["senales"] == [] and limpia["por_atributo"] == {}
