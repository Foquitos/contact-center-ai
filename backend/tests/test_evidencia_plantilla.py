"""
Evidencia real de una plantilla: señales y bloque para el prompt.

La revisión con IA dejó de opinar solo sobre el texto de la plantilla y pasó a mirar qué
respondió de verdad auditando y en qué la corrigieron los auditores
(`AuditorIA/evidencia_plantilla.py`). Lo que se prueba acá es la LECTURA de esos datos,
que es donde está el criterio:

- las señales tienen umbral: con 4 respuestas no se concluye nada y una señal falsa hace
  que se desconfíe de todas;
- se detecta lo que no se puede ver leyendo el prompt (el criterio que no discrimina, la
  opción que nadie elige, la lista que se queda corta, el texto que se recorta, el
  atributo que el auditor corrige una y otra vez);
- el bloque que se le manda al modelo lleva los NÚMEROS y los motivos que escribieron
  los auditores, que es lo que le permite fundamentar en vez de generalizar.

Test 100% offline: no toca DB, Gemini ni tokens.
Correr: pytest tests/test_evidencia_plantilla.py -m "not tokens"
"""
import pytest

from AuditorIA import asistente_plantillas as ap
from AuditorIA import evidencia_plantilla as ev


ATRIBUTOS = ap.normalizar_atributos([
    {"id": 1, "nombre": "Saludo", "tipo": "boolean", "prompt": "¿Saludó?" * 20,
     "orden": 0, "ponderacion": 0, "es_opcional": False},
    {"id": 2, "nombre": "Motivo", "tipo": "enum", "prompt": "Clasificá el motivo." * 10,
     "restricciones": {"enum": ["Reclamo", "Consulta", "Otros"]}, "orden": 1,
     "ponderacion": 0, "es_opcional": False},
    {"id": 3, "nombre": "Feedback", "tipo": "string", "prompt": "Escribí un feedback breve." * 5,
     "orden": 2, "ponderacion": 0, "es_opcional": True},
])


def _evidencia(por_atributo, auditorias=500):
    return {"ventana_dias": 90, "auditorias": auditorias, "desde": "2026-05-23",
            "por_atributo": por_atributo, "hay_datos": True}


def _claves(senales):
    return {s["clave"] for s in senales}


def test_criterio_que_no_discrimina(  ):
    evidencia = _evidencia({1: {"respuestas": 400, "valores": [
        {"valor": "True", "n": 396, "pct": 99.0}, {"valor": "False", "n": 4, "pct": 1.0}]}})
    senales = ev.senales_de_evidencia(evidencia, ATRIBUTOS)
    assert "no_discrimina" in _claves(senales)
    assert "99.0%" in next(s["mensaje"] for s in senales if s["clave"] == "no_discrimina")


def test_con_pocas_respuestas_no_concluye_nada():
    """El mismo 100% sobre 5 respuestas no es una señal, es ruido."""
    evidencia = _evidencia({1: {"respuestas": 5, "valores": [{"valor": "True", "n": 5, "pct": 100.0}]}},
                           auditorias=5)
    assert "no_discrimina" not in _claves(ev.senales_de_evidencia(evidencia, ATRIBUTOS))


def test_la_lista_se_queda_corta_cuando_otros_se_lleva_todo():
    evidencia = _evidencia({2: {"respuestas": 300, "valores": [
        {"valor": "Reclamo", "n": 120, "pct": 40.0},
        {"valor": "Otros", "n": 111, "pct": 37.0},
        {"valor": "Consulta", "n": 69, "pct": 23.0}]}})
    senales = ev.senales_de_evidencia(evidencia, ATRIBUTOS)
    assert "salida_segura_saturada" in _claves(senales)
    assert next(s for s in senales if s["clave"] == "salida_segura_saturada")["severidad"] == "alta"


def test_opcion_que_nunca_se_elige():
    evidencia = _evidencia({2: {"respuestas": 300, "valores": [
        {"valor": "Reclamo", "n": 250, "pct": 83.3},
        {"valor": "Otros", "n": 50, "pct": 16.7}]}})
    senales = ev.senales_de_evidencia(evidencia, ATRIBUTOS)
    mensaje = next(s["mensaje"] for s in senales if s["clave"] == "opciones_muertas")
    assert "Consulta" in mensaje


def test_valores_guardados_que_ya_no_estan_en_la_lista():
    """Pasa cuando alguien edita las opciones sin mirar el histórico: la grilla muestra
    valores que la IA ya no puede volver a elegir."""
    evidencia = _evidencia({2: {"respuestas": 300, "valores": [
        {"valor": "Reclamo", "n": 100, "pct": 33.3},
        {"valor": "Consulta", "n": 100, "pct": 33.3},
        {"valor": "Otros", "n": 50, "pct": 16.7},
        {"valor": "Gestión de mora", "n": 50, "pct": 16.7}]}})
    senales = ev.senales_de_evidencia(evidencia, ATRIBUTOS)
    assert "valores_fuera_de_lista" in _claves(senales)


def test_texto_que_se_recorta_seguido():
    evidencia = _evidencia({3: {"respuestas": 200, "recortados": 60, "valores": []}})
    senales = ev.senales_de_evidencia(evidencia, ATRIBUTOS)
    senal = next(s for s in senales if s["clave"] == "texto_recortado")
    assert senal["severidad"] == "alta" and "30.0%" in senal["mensaje"]


def test_opcional_que_casi_nunca_aplica():
    evidencia = _evidencia({3: {"respuestas": 50, "valores": []}}, auditorias=500)
    assert "casi_nunca_aplica" in _claves(ev.senales_de_evidencia(evidencia, ATRIBUTOS))


def test_atributo_que_no_se_usa():
    evidencia = _evidencia({1: {"respuestas": 0, "valores": []}}, auditorias=500)
    assert "sin_uso" in _claves(ev.senales_de_evidencia(evidencia, ATRIBUTOS))


def test_desacuerdo_con_los_auditores_es_la_senal_mas_valiosa():
    """Es el dato que hasta acá vivía solo en la pantalla de Evaluación y nunca volvía a
    la plantilla que lo causaba."""
    evidencia = _evidencia({1: {
        "respuestas": 400, "valores": [{"valor": "True", "n": 300, "pct": 75.0}],
        "revisiones": 40, "aciertos": 21, "accuracy": 52.5, "kappa": 0.28,
        "correcciones": [{"ia": "Sí", "humano": "No", "n": 14}],
    }})
    senal = next(s for s in ev.senales_de_evidencia(evidencia, ATRIBUTOS)
                 if s["clave"] == "desacuerdo_humano")
    assert senal["severidad"] == "alta"
    assert "kappa 0.28" in senal["mensaje"] and "14 veces" in senal["mensaje"]


def test_con_pocas_revisiones_no_se_habla_de_desacuerdo():
    evidencia = _evidencia({1: {"respuestas": 400, "valores": [], "revisiones": 3,
                                "accuracy": 33.0, "kappa": 0.1}})
    assert "desacuerdo_humano" not in _claves(ev.senales_de_evidencia(evidencia, ATRIBUTOS))


def test_falsos_error_critico():
    """Un EC deja el llamado en 0: que la IA lo marque de más es de lo más caro que hay."""
    evidencia = _evidencia({1: {"respuestas": 400, "valores": [], "falsos_ec": 5}})
    assert "falsos_ec" in _claves(ev.senales_de_evidencia(evidencia, ATRIBUTOS))


def test_el_bloque_del_prompt_lleva_numeros_y_los_motivos_del_auditor():
    evidencia = _evidencia({2: {
        "respuestas": 300,
        "valores": [{"valor": "Otros", "n": 111, "pct": 37.0}],
        "revisiones": 20, "accuracy": 60.0, "kappa": 0.35,
        "correcciones": [{"ia": "Consulta", "humano": "Reclamo", "n": 8}],
        "motivos": ["el cliente estaba reclamando, no consultando"],
    }})
    bloque = ev.bloque_para_prompt(evidencia, ATRIBUTOS)
    assert "300 respuestas" in bloque
    assert "Otros 37.0%" in bloque
    assert "kappa 0.35" in bloque
    assert "el cliente estaba reclamando" in bloque
    assert "últimos 90 días" in bloque


def test_sin_datos_el_bloque_queda_vacio():
    assert ev.bloque_para_prompt({"por_atributo": {}}, ATRIBUTOS) == ""


# --------------------------------------------------------------------------- #
# Integración con la revisión                                                  #
# --------------------------------------------------------------------------- #
PLANTILLA = {
    "nombre": "Calidad", "descripcion": "", "system_prompt": "s", "recordatorio": "r",
    "atributos": [
        {"id": 1, "nombre": "Saludo", "tipo": "boolean", "prompt": "¿Saludó?" * 20,
         "orden": 0, "ponderacion": 0, "es_opcional": False},
    ],
}


def test_la_revision_suma_las_senales_de_datos_a_las_de_estructura(monkeypatch):
    monkeypatch.setattr(ap, "_generar_json", lambda *a, **k: {"diagnostico": "", "atributos": []})
    evidencia = _evidencia({1: {"respuestas": 400, "valores": [
        {"valor": "True", "n": 396, "pct": 99.0}]}})
    resultado = ap.revisar_plantilla(PLANTILLA, evidencia=evidencia)
    claves = {s["clave"] for s in resultado["senales"]}
    assert "no_opcional" in claves        # estructura (sin datos)
    assert "no_discrimina" in claves      # datos
    assert resultado["evidencia"]["auditorias"] == 500


def test_las_advertencias_dicen_cuanto_material_afecta_el_cambio(monkeypatch):
    """"Lo ya auditado queda como está" no le dice nada a nadie; "4.312 respuestas" sí."""
    monkeypatch.setattr(ap, "_generar_json", lambda *a, **k: {
        "diagnostico": "",
        "atributos": [{"accion": "modificar", "id": 1, "tipo": "critical_audit", "ponderacion": 10}],
    })
    evidencia = _evidencia({1: {"respuestas": 4312, "valores": [], "revisiones": 12}})
    attr = ap.revisar_plantilla(PLANTILLA, evidencia=evidencia)["atributos"][0]
    volumen = next(a for a in attr["advertencias"] if "Tamaño del impacto" in a)
    assert "4.312 respuestas" in volumen and "90 días" in volumen
    assert "12 revisiones" in volumen


def test_sin_evidencia_la_revision_sigue_funcionando(monkeypatch):
    monkeypatch.setattr(ap, "_generar_json", lambda *a, **k: {
        "diagnostico": "", "atributos": [{"accion": "modificar", "id": 1, "es_opcional": "si"}]})
    resultado = ap.revisar_plantilla(PLANTILLA)
    assert resultado["evidencia"]["hay_datos"] is False
    assert not any("Tamaño del impacto" in a for a in resultado["atributos"][0]["advertencias"])
