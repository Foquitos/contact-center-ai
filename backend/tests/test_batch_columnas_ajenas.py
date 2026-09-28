"""
Regresión de la contaminación de columnas ENTRE lotes de la misma pasada de polling
(detectada el 2026-07-30, al reprocesar 21 lotes juntos).

procesar_batch arma un solo df_total con los resultados de todos los lotes que terminaron
en la misma ventana de 15 min y después los separa con un groupby. Pero un grupo del
groupby conserva TODAS las columnas de df_total: las que aportó otro lote quedan presentes
y enteras en NaN.

Eso no es inocuo, porque tanto la resolución de alias (_estandarizar_columnas_sql) como la
clave (calcular_id_aplicativo) deciden por PRESENCIA de la columna y por orden de
prioridad, sin mirar si tiene datos:

  * 'loginid' gana sobre 'operador' -> un lote de Farmalux (LoginId) en la misma ventana
    dejaba a Dental y ALARMIX con operadorUsuario NULL (521 auditorías del 30/07).
  * 'idinteraccion' + 'segmento' gana sobre 'segmentid' -> un lote de Dental dejaba a ALARMIX
    con IdAplicativo 'nan_nan' (362 auditorías, la más vieja del 18/07). Sin IdAplicativo
    válido la auditoría no se puede asociar a su interacción ni deduplicar contra
    re-auditorías.

El mismo bug ya se había detectado para las transcripciones (se agrupan por batch_id desde
el principio, ver procesar_batch); estos tests cubren el df principal.

Test 100% offline: no toca DB, Gemini ni tokens.
"""
from types import SimpleNamespace

import pandas as pd
import pytest

import Auditor as auditor_mod
from Auditor import AuditorIA


DENTAL = "batches/dental"      # clave idInteraccion + Segmento, operador en 'Operador'
ALARMIX = "batches/alarmix"            # clave segmentId,               operador en 'Operador'
FARMALUX = "batches/farmalux"  # clave connid,                operador en 'LoginId'

# Una fila de contexto por lote, tal como vuelve de calidad.Batch_data.
CONTEXTO = {
    DENTAL: {"idInteraccion": "260721093234145_CHT_23000", "Segmento": "3", "Operador": "cl12239"},
    ALARMIX: {"segmentId": "621f1219-2cb9-4115-96b7-84e4d07cdc88", "Operador": "Johanna Mercuri"},
    FARMALUX: {"connid": "0007123456", "LoginId": "fmed01"},
}


def _correr_pasada(monkeypatch, batches):
    """Corre procesar_batch con la estandarización REAL y devuelve el df que le llegó a
    cada lote."""
    recibidos = {}

    def _procesar_grupo(batch_id, df, df_trans=None, motivos_gemini=None):
        recibidos[batch_id] = df

    respuesta = SimpleNamespace(response="ok")
    batch_job = SimpleNamespace(dest=SimpleNamespace(inlined_responses=[respuesta]))

    fake = SimpleNamespace(
        MAX_INTENTOS_PROCESO=AuditorIA.MAX_INTENTOS_PROCESO,
        engine=None,
        gemini=SimpleNamespace(batches=SimpleNamespace(get=lambda name: batch_job)),
        _registrar_intento_proceso=lambda batch_id: 1,
        _tiene_metadata_batch=lambda batch_id: True,
        _sellar_batch_procesado=lambda batch_id: None,
        _cerrar_batch_sin_resultado=lambda batch_id, state, motivo=None: None,
        obtener_info_batch=lambda batch_id, segment_id: pd.Series(
            {**CONTEXTO[batch_id], "gemini_file_name": "files/xyz"}
        ),
        borrar_archivo_gemini=lambda archivo: None,
        # La de verdad: es justamente lo que interpretaba mal las columnas ajenas.
        _estandarizar_columnas_sql=lambda df: AuditorIA._estandarizar_columnas_sql(None, df),
        _renombrar_detalles_a_atributo_id=lambda df: df,
        _procesar_grupo_batch=_procesar_grupo,
    )

    monkeypatch.setattr(auditor_mod, "procesar_respuesta_combinada",
                        lambda response: (pd.Series({"Detalle_101": "OK"}), None))
    monkeypatch.setattr(auditor_mod, "finalizar_ejecucion", lambda *a, **k: None)

    AuditorIA.procesar_batch(fake, [(b, "JOB_STATE_SUCCEEDED") for b in batches])
    return recibidos


def test_loginid_ajena_no_borra_el_operador(monkeypatch):
    """El síntoma reportado: Dental (y ALARMIX) sin usuario porque un lote de Farmalux, en la
    misma ventana, aportó una columna LoginId vacía que gana por orden de alias."""
    recibidos = _correr_pasada(monkeypatch, [DENTAL, FARMALUX])

    assert recibidos[DENTAL]["operador_usuario"].tolist() == ["cl12239"]
    assert recibidos[FARMALUX]["operador_usuario"].tolist() == ["fmed01"]


def test_idinteraccion_ajena_no_rompe_la_clave(monkeypatch):
    """El daño silencioso: ALARMIX calculaba 'nan_nan' como IdAplicativo porque heredaba las
    columnas idInteraccion/Segmento (vacías) del lote de Dental."""
    recibidos = _correr_pasada(monkeypatch, [DENTAL, ALARMIX])

    assert recibidos[ALARMIX]["id_aplicativo"].tolist() == ["621f1219-2cb9-4115-96b7-84e4d07cdc88"]
    assert recibidos[DENTAL]["id_aplicativo"].tolist() == ["260721093234145_CHT_23000_3"]
    assert recibidos[ALARMIX]["operador_usuario"].tolist() == ["Johanna Mercuri"]


def test_tres_lotes_juntos_no_se_pisan(monkeypatch):
    """La pasada del rescate: tres orígenes con tres claves y dos nombres de operador
    distintos. Cada lote tiene que salir con lo suyo."""
    recibidos = _correr_pasada(monkeypatch, [DENTAL, ALARMIX, FARMALUX])

    assert set(recibidos) == {DENTAL, ALARMIX, FARMALUX}
    assert recibidos[DENTAL]["id_aplicativo"].tolist() == ["260721093234145_CHT_23000_3"]
    assert recibidos[ALARMIX]["id_aplicativo"].tolist() == ["621f1219-2cb9-4115-96b7-84e4d07cdc88"]
    assert recibidos[FARMALUX]["id_aplicativo"].tolist() == ["0007123456"]
    assert recibidos[DENTAL]["operador_usuario"].tolist() == ["cl12239"]
    assert recibidos[ALARMIX]["operador_usuario"].tolist() == ["Johanna Mercuri"]
    assert recibidos[FARMALUX]["operador_usuario"].tolist() == ["fmed01"]

    for batch_id, df in recibidos.items():
        ajenas = [c for c in df.columns if c in ("LoginId", "connid", "segmentId", "idInteraccion")
                  and c not in CONTEXTO[batch_id]]
        assert not ajenas, f"{batch_id} se quedó con columnas de otro lote: {ajenas}"


def test_un_lote_solo_conserva_sus_columnas(monkeypatch):
    """Control: sin vecinos en la ventana, nada cambia respecto del comportamiento previo."""
    recibidos = _correr_pasada(monkeypatch, [ALARMIX])

    assert recibidos[ALARMIX]["id_aplicativo"].tolist() == ["621f1219-2cb9-4115-96b7-84e4d07cdc88"]
    assert recibidos[ALARMIX]["operador_usuario"].tolist() == ["Johanna Mercuri"]
