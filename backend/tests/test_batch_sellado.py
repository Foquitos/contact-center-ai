"""
Regresión de la pérdida de resultados en el modo Batch (2026-07-14).

check_batch_status marcaba el lote como JOB_STATE_SUCCEEDED y commiteaba ANTES de que
procesar_batch guardara nada. sp_LimpiarDatosDeBatchFinalizados leía ese SUCCEEDED como
"ya está, se puede limpiar" y borraba calidad.Batch_data, la única tabla que ata cada
segment_id de la respuesta de Gemini a su interacción. Si procesar_batch fallaba o no
llegaba a correr antes de la limpieza, el lote quedaba fuera del SELECT (solo miraba
PENDING/RUNNING) y sin metadata: las respuestas quedaban huérfanas para siempre. Se comió
4 lotes = 220 auditorías ya pagadas a Gemini.

La invariante que protegen estos tests: un lote se sella (procesado_at) SOLO cuando sus
resultados quedaron guardados, o cuando se lo abandona explícitamente. Mientras no esté
sellado, su Batch_data sobrevive y el scheduler lo reintenta.

Test 100% offline: no toca DB, Gemini ni tokens.
"""
from types import SimpleNamespace

import pandas as pd
import pytest

import Auditor as auditor_mod
from Auditor import AuditorIA


BATCH = "batches/test123"


def _fake_self(monkeypatch, *, con_metadata=True, intentos=1, grupo_explota=False):
    """Un AuditorIA de mentira: solo los colaboradores que toca procesar_batch."""
    estado = SimpleNamespace(sellados=[], cerrados=[], grupos_procesados=[])

    def _procesar_grupo(batch_id, df, df_trans=None, motivos_gemini=None):
        if grupo_explota:
            raise RuntimeError("fallo al guardar en SQL")
        estado.grupos_procesados.append(batch_id)

    respuesta = SimpleNamespace(response="ok")
    batch_job = SimpleNamespace(dest=SimpleNamespace(inlined_responses=[respuesta]))

    fake = SimpleNamespace(
        MAX_INTENTOS_PROCESO=AuditorIA.MAX_INTENTOS_PROCESO,
        engine=None,
        gemini=SimpleNamespace(batches=SimpleNamespace(get=lambda name: batch_job)),
        _registrar_intento_proceso=lambda batch_id: intentos,
        _tiene_metadata_batch=lambda batch_id: con_metadata,
        _sellar_batch_procesado=lambda batch_id: estado.sellados.append(batch_id),
        _cerrar_batch_sin_resultado=lambda batch_id, state, motivo=None, filas_error=None:
            estado.cerrados.append((batch_id, state)),
        obtener_info_batch=lambda batch_id, segment_id: pd.Series(
            {"id_aplicativo": "abc_1", "gemini_file_name": "files/xyz"}
        ),
        borrar_archivo_gemini=lambda archivo: None,
        _estandarizar_columnas_sql=lambda df: df,
        _renombrar_detalles_a_atributo_id=lambda df: df,
        _procesar_grupo_batch=_procesar_grupo,
    )

    # La respuesta de Gemini no se parsea de verdad: no es lo que estamos probando.
    monkeypatch.setattr(auditor_mod, "procesar_respuesta_combinada",
                        lambda response: (pd.Series({"Detalle_101": "OK"}), None))
    monkeypatch.setattr(auditor_mod, "finalizar_ejecucion", lambda *a, **k: None)

    return fake, estado


def test_lote_guardado_se_sella(monkeypatch):
    """El camino feliz: se guardó en SQL, recién ahí se suelta la metadata."""
    fake, estado = _fake_self(monkeypatch)

    AuditorIA.procesar_batch(fake, [(BATCH, "JOB_STATE_SUCCEEDED")])

    assert estado.grupos_procesados == [BATCH]
    assert estado.sellados == [BATCH]
    assert estado.cerrados == []


def test_lote_que_falla_al_guardar_no_se_sella(monkeypatch):
    """El corazón del bug: si no se guardó, NO se sella. Así conserva su Batch_data y el
    próximo check_batch_status lo vuelve a traer, en vez de perderlo para siempre."""
    fake, estado = _fake_self(monkeypatch, grupo_explota=True, intentos=1)

    AuditorIA.procesar_batch(fake, [(BATCH, "JOB_STATE_SUCCEEDED")])

    assert estado.sellados == [], "un lote que no se guardó no puede darse por terminado"
    assert estado.cerrados == [], "todavía le quedan intentos: no se abandona"


def test_lote_sin_metadata_se_abandona_explicitamente(monkeypatch):
    """Sin Batch_data no hay forma de atribuir las respuestas. Reintentar no las recupera:
    se cierra la corrida (para que la tarea no quede girando) y se sella."""
    fake, estado = _fake_self(monkeypatch, con_metadata=False)

    AuditorIA.procesar_batch(fake, [(BATCH, "JOB_STATE_SUCCEEDED")])

    assert estado.grupos_procesados == [], "sin metadata no se procesa nada"
    assert estado.cerrados == [(BATCH, "SIN_METADATA")]
    assert estado.sellados == [BATCH], "es terminal: no tiene sentido reintentarlo"


def test_al_agotar_los_intentos_se_abandona(monkeypatch):
    """Un lote que nunca se puede procesar no debe reintentarse cada 15 min para siempre."""
    fake, estado = _fake_self(
        monkeypatch, grupo_explota=True, intentos=AuditorIA.MAX_INTENTOS_PROCESO
    )

    AuditorIA.procesar_batch(fake, [(BATCH, "JOB_STATE_SUCCEEDED")])

    assert estado.cerrados == [(BATCH, "ERROR_PROCESO")]
    assert estado.sellados == [BATCH]
