"""
Regresión del diagnóstico de lotes fallidos (caso ALARMIX, 17/08/2026).

Gemini cerró el lote en JOB_STATE_SUCCEEDED pero devolvió `response = None` en sus
30 requests (el motivo real venía en `.error`, que el código ignoraba). El parseo
explotaba con `'NoneType' object has no attribute 'candidates'` una vez por
interacción, y lo único que quedaba guardado en calidad.AuditExecutionLog era
"El lote no se pudo procesar después de 5 intentos; se abandona." — para saber qué
había pasado de verdad había que ir al journal de SRV01 y encontrarlo antes de que
rotara. Se perdieron 30 llamados sin saber por qué.

Las invariantes que protegen estos tests:
  1. Una respuesta vacía se detecta ANTES de parsearla y se registra con el error
     que mandó Gemini (no con el AttributeError que venía después).
  2. Al abandonar el lote, el motivo real viaja en el `error_message` de la corrida
     (y la cantidad de interacciones perdidas, en `filas_error`).
  3. El resumen de motivos es legible: agrupa por mensaje en vez de repetirlo N veces.

Test 100% offline: no toca DB, Gemini ni tokens.
"""
from collections import Counter
from types import SimpleNamespace

import pandas as pd
import pytest

import Auditor as auditor_mod
from Auditor import AuditorIA, _resumen_motivos, _texto_error_gemini


BATCH = "batches/alarmix-roto"


# --- 1. El mensaje que Gemini manda por request -----------------------------------

def test_error_de_gemini_como_objeto():
    error = SimpleNamespace(code=429, message="Resource exhausted", status="RESOURCE_EXHAUSTED")

    assert _texto_error_gemini(error) == "[429] Resource exhausted"


def test_error_de_gemini_como_dict():
    assert _texto_error_gemini({"code": 400, "message": "Invalid argument"}) == "[400] Invalid argument"


def test_sin_error_lo_dice_explicitamente():
    """El peor caso: ni respuesta ni error. Antes quedaba como un AttributeError."""
    assert "ni respuesta ni error" in _texto_error_gemini(None)


# --- 3. El resumen que se guarda en la corrida ------------------------------------

def test_el_resumen_agrupa_por_mensaje():
    motivos = Counter({"[429] Resource exhausted": 28, "[400] Invalid argument": 2})

    resumen = _resumen_motivos(motivos)

    assert "[429] Resource exhausted (x28)" in resumen
    assert "[400] Invalid argument (x2)" in resumen


def test_el_resumen_de_una_corrida_sana_es_vacio():
    assert _resumen_motivos(None) == ""
    assert _resumen_motivos(Counter()) == ""


def test_el_resumen_se_corta_en_tres_motivos():
    motivos = Counter({f"error {i}": 10 - i for i in range(6)})

    resumen = _resumen_motivos(motivos)

    assert resumen.count("|") == 3        # 3 motivos + la coletilla
    assert "y 3 motivo(s) más" in resumen


# --- 2. El caso completo: el lote se abandona CON el motivo ------------------------

def _fake_self(monkeypatch, respuestas, intentos=AuditorIA.MAX_INTENTOS_PROCESO):
    """AuditorIA de mentira con las respuestas que devuelve el lote de Gemini."""
    estado = SimpleNamespace(cerrados=[], sellados=[])
    batch_job = SimpleNamespace(dest=SimpleNamespace(inlined_responses=respuestas))

    def _cerrar(batch_id, state, motivo=None, filas_error=None):
        estado.cerrados.append({"batch_id": batch_id, "state": state,
                                "motivo": motivo, "filas_error": filas_error})

    fake = SimpleNamespace(
        MAX_INTENTOS_PROCESO=AuditorIA.MAX_INTENTOS_PROCESO,
        engine=None,
        gemini=SimpleNamespace(batches=SimpleNamespace(get=lambda name: batch_job)),
        _registrar_intento_proceso=lambda batch_id: intentos,
        _tiene_metadata_batch=lambda batch_id: True,
        _sellar_batch_procesado=lambda batch_id: estado.sellados.append(batch_id),
        _cerrar_batch_sin_resultado=_cerrar,
        obtener_info_batch=lambda batch_id, segment_id: pd.Series(
            {"id_aplicativo": f"abc_{segment_id}", "gemini_file_name": ""}
        ),
        borrar_archivo_gemini=lambda archivo: None,
        _estandarizar_columnas_sql=lambda df: df,
        _renombrar_detalles_a_atributo_id=lambda df: df,
        _procesar_grupo_batch=lambda batch_id, df, df_trans=None, motivos_gemini=None: None,
    )
    monkeypatch.setattr(auditor_mod, "procesar_respuesta_combinada",
                        lambda response: (pd.Series({"Detalle_101": "OK"}), None))
    monkeypatch.setattr(auditor_mod, "finalizar_ejecucion", lambda *a, **k: None)
    return fake, estado


def test_el_lote_vacio_se_abandona_con_el_motivo_de_gemini(monkeypatch):
    """El caso ALARMIX exacto: 3 requests, todos con response=None y el mismo error."""
    error = SimpleNamespace(code=429, message="Resource exhausted", status=None)
    respuestas = [SimpleNamespace(response=None, error=error) for _ in range(3)]
    fake, estado = _fake_self(monkeypatch, respuestas)

    AuditorIA.procesar_batch(fake, [(BATCH, "JOB_STATE_SUCCEEDED")])

    assert len(estado.cerrados) == 1
    cerrado = estado.cerrados[0]
    assert cerrado["state"] == "ERROR_PROCESO"
    # Lo que antes se perdía: el motivo real y cuántas interacciones se cayeron.
    assert "[429] Resource exhausted (x3)" in cerrado["motivo"]
    assert cerrado["filas_error"] == 3
    assert estado.sellados == [BATCH]


def test_una_respuesta_vacia_no_frena_a_las_sanas(monkeypatch):
    """Un request fallido se salta; los demás se auditan igual (y no se abandona nada)."""
    respuestas = [
        SimpleNamespace(response=None, error=SimpleNamespace(code=400, message="Invalid", status=None)),
        SimpleNamespace(response="ok", error=None),
    ]
    fake, estado = _fake_self(monkeypatch, respuestas)

    AuditorIA.procesar_batch(fake, [(BATCH, "JOB_STATE_SUCCEEDED")])

    assert estado.cerrados == []
    assert estado.sellados == [BATCH]


# --- 4. El lote PARCIAL no cierra como éxito ---------------------------------------
# Caso Cortes abruptos - ALARMIX, 15/09/2026: Gemini devolvió 6 respuestas y 24 errores
# `[13] Internal error`. Las 6 se guardaron y la corrida cerró EXITO con filas_error=0:
# en la pantalla parecía una corrida sana de 6 auditorías y nadie supo que faltaban 24.

def test_los_motivos_del_lote_llegan_al_cierre_del_grupo(monkeypatch):
    respuestas = [
        SimpleNamespace(response=None, error=SimpleNamespace(code=13, message="Internal error encountered.", status=None)),
        SimpleNamespace(response=None, error=SimpleNamespace(code=13, message="Internal error encountered.", status=None)),
        SimpleNamespace(response="ok", error=None),
    ]
    fake, estado = _fake_self(monkeypatch, respuestas)
    recibidos = {}
    fake._procesar_grupo_batch = lambda batch_id, df, df_trans=None, motivos_gemini=None: \
        recibidos.update({batch_id: motivos_gemini})

    AuditorIA.procesar_batch(fake, [(BATCH, "JOB_STATE_SUCCEEDED")])

    assert recibidos[BATCH] == Counter({"[13] Internal error encountered.": 2})


def _fake_grupo(monkeypatch, filas_error_al_guardar=0):
    """Lo mínimo para correr _procesar_grupo_batch sin DB, Sheets ni mail."""
    cierres = []
    monkeypatch.setattr(auditor_mod, "obtener_contexto", lambda *a, **k: {})
    monkeypatch.setattr(auditor_mod, "incidencias", SimpleNamespace(
        fusionar_declarada_por_ia=lambda df: df,
        verificar_operador=lambda df, df_trans: None,
        normalizar_columna=lambda df: None,
    ))
    monkeypatch.setattr(auditor_mod, "finalizar_ejecucion", lambda engine, **k: cierres.append(k))
    fake = SimpleNamespace(
        engine=None,
        _parsear_column_template_id=lambda df: None,
        _parsear_modelo_ia=lambda df: "gemini-3.8-flash",
        _evaluar_y_enviar_avisos=lambda df, plantilla_id: None,
        _finalizar_y_guardar_sql=lambda **k: {"filas_auditadas": 6, "filas_error": filas_error_al_guardar, "tokens": {}},
        _guardar_transcripciones_batch=lambda *a, **k: None,
        _obtener_export_auditorias=lambda **k: pd.DataFrame(),
        _actualizar_estado_task=lambda *a, **k: None,
        _ids_auditados=AuditorIA._ids_auditados,
        _notificar_corrida_si_termino=lambda *a, **k: None,
    )
    df = pd.DataFrame({"campana_id": [21], "empresa_id": [12], "plantilla_id": [23], "user_id": [1]})
    return fake, df, cierres


def test_lote_con_respuestas_perdidas_cierra_parcial_y_dice_por_que(monkeypatch):
    fake, df, cierres = _fake_grupo(monkeypatch)

    AuditorIA._procesar_grupo_batch(fake, BATCH, df,
                                    motivos_gemini=Counter({"[13] Internal error encountered.": 24}))

    assert cierres[0]["status"] == "PARCIAL"
    assert cierres[0]["filas_error"] == 24
    assert "24 interacción(es) sin respuesta de Gemini" in cierres[0]["error_message"]
    assert "[13] Internal error encountered. (x24)" in cierres[0]["error_message"]


def test_lote_completo_sigue_cerrando_exito_sin_mensaje(monkeypatch):
    fake, df, cierres = _fake_grupo(monkeypatch)

    AuditorIA._procesar_grupo_batch(fake, BATCH, df)

    assert cierres[0]["status"] == "EXITO"
    assert cierres[0]["filas_error"] == 0
    assert cierres[0]["error_message"] is None
