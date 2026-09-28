"""
Regresión de las auditorías de Odonto Plus que quedaban "En curso" para siempre
(2026-07-13 a 2026-07-29, 21 corridas / 493 auditorías).

Dental es la única descarga con IdTurno (OUTER APPLY contra Odonto_Plus_Turnero). Si en
la muestra había alguna llamada SIN turno, pandas promovía la columna a float64 por el NaN
y, al persistir la metadata del lote, ese float entraba como parámetro a la columna varchar
de calidad.Batch_data: SQL Server lo escribía en notación científica ('1.05276e+007').
Horas después, al procesar la respuesta de Gemini, `str(int(x))` sobre ese texto tiraba
ValueError.

Lo grave no era el ValueError sino DÓNDE ocurría: la estandarización de columnas corría
fuera de todo try/except, así que la excepción abortaba la pasada COMPLETA de
procesar_batch. Ningún lote se sellaba, ningún AuditExecutionLog se cerraba, y ni siquiera
llegaba a correr la red de MAX_INTENTOS_PROCESO que debía abandonarlos con un motivo. De
las 21 corridas trabadas, 8 eran de OTRAS empresas (ALARMIX, Farmalux): su único pecado fue
caer en la misma ventana de polling de 15 min que un lote de Dental.

Las tres invariantes que protegen estos tests:
  1. Batch_data nunca recibe un float (se serializa en decimal, sin exponente).
  2. Un IdTurno ilegible no revienta: se guarda NULL (y no un id reconstruido, que
     apuntaría al turno de otro paciente).
  3. Un lote que falla al estandarizar cierra en ERROR él solo; los demás de la misma
     pasada se guardan igual.

Test 100% offline: no toca DB, Gemini ni tokens.
"""
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import Auditor as auditor_mod
from Auditor import AuditorIA, _idturno_a_texto
from AuditorIA.gemini import valor_para_batch_data


# Valor real leído de calidad.Batch_data (lote fg8d54b2…, corrida 146 del 23/07).
IDTURNO_CIENTIFICO = "1.05276e+007"


# --- 1. Lado de escritura: lo que se guarda en Batch_data -------------------------------

def test_float_entero_se_guarda_en_decimal():
    """El corazón del bug: un IdTurno float NO puede llegar como float a una columna
    varchar, porque SQL Server lo convierte a '1.05276e+007' y le come los dígitos."""
    assert valor_para_batch_data(10527612.0) == "10527612"
    assert valor_para_batch_data(np.float64(10549964.0)) == "10549964"
    # Ida y vuelta completa: lo que se escribe hoy tiene que ser legible mañana.
    assert _idturno_a_texto(valor_para_batch_data(np.float64(10527612.0))) == "10527612"


def test_columna_con_huecos_no_pierde_precision():
    """El disparador real: una sola llamada sin turno promueve TODA la columna a float64."""
    serie = pd.Series([10527612, None, 10527613])  # -> float64 por el NaN
    assert serie.dtype == "float64"

    guardado = [valor_para_batch_data(v) for v in serie]

    assert guardado == ["10527612", None, "10527613"]


def test_valores_no_float_pasan_igual():
    """El resto de la metadata del lote no cambia de forma."""
    assert valor_para_batch_data("10527612") == "10527612"
    assert valor_para_batch_data(103) == 103
    assert valor_para_batch_data(None) is None
    assert valor_para_batch_data(np.nan) is None
    assert valor_para_batch_data(pd.NaT) is None
    assert valor_para_batch_data(b"audio crudo") is None, "el audio en memoria se persiste NULL"
    assert valor_para_batch_data(1.5) == "1.5", "un float no entero conserva su parte decimal"


# --- 2. Lado de lectura: IdTurno ilegible -> NULL, nunca una excepción ------------------

@pytest.mark.parametrize("crudo, esperado", [
    ("10527612", "10527612"),          # el caso sano (lote con IdTurno en todas las filas)
    ("10527612.0", "10527612"),        # float serializado por pandas
    (10527612, "10527612"),
    (np.int64(10527612), "10527612"),
    (10527612.0, "10527612"),
    (None, None),
    (np.nan, None),
    ("", None),
    ("nan", None),
    (IDTURNO_CIENTIFICO, None),        # truncado en origen: irrecuperable
    ("sin turno", None),               # basura: tampoco puede reventar
])
def test_idturno_a_texto(crudo, esperado):
    assert _idturno_a_texto(crudo) == esperado


def test_estandarizar_no_revienta_con_idturno_cientifico():
    """La regresión exacta: este df es lo que volvía de Batch_data y tumbaba la pasada."""
    df = pd.DataFrame([
        {"idInteraccion": "aaa", "Segmento": "1", "Operador": "u1", "IdTurno": IDTURNO_CIENTIFICO},
        {"idInteraccion": "bbb", "Segmento": "1", "Operador": "u2", "IdTurno": None},
        {"idInteraccion": "ccc", "Segmento": "2", "Operador": "u3", "IdTurno": "10527613"},
    ])

    # El método no usa `self` (solo el logger del módulo): se llama sin construir la clase,
    # que abriría conexión a la base y cliente de Gemini.
    resultado = AuditorIA._estandarizar_columnas_sql(None, df)

    assert resultado["id_aplicativo"].tolist() == ["aaa_1", "bbb_1", "ccc_2"]
    extras = resultado["Extras"].tolist()
    assert extras[0] is None, "un IdTurno truncado no se reconstruye: sería el turno de otro"
    assert extras[1] is None
    assert '"IdTurno": "10527613"' in extras[2], "el IdTurno legible sí se conserva"


# --- 3. Un lote roto no se lleva puestos a los demás ------------------------------------

SANO = "batches/sano"
ROTO = "batches/roto"


def _fake_self(monkeypatch, intentos=1):
    """Un AuditorIA de mentira: solo los colaboradores que toca procesar_batch.

    _estandarizar_columnas_sql explota para el lote ROTO, igual que hacía el IdTurno
    científico de Dental.
    """
    estado = SimpleNamespace(sellados=[], cerrados=[], grupos=[], errores=[])

    def _estandarizar(df):
        if (df["_marca"] == ROTO).any():
            raise ValueError(f"invalid literal for int() with base 10: '{IDTURNO_CIENTIFICO}'")
        return df

    respuesta = SimpleNamespace(response="ok")
    batch_job = SimpleNamespace(dest=SimpleNamespace(inlined_responses=[respuesta]))

    fake = SimpleNamespace(
        MAX_INTENTOS_PROCESO=AuditorIA.MAX_INTENTOS_PROCESO,
        engine=None,
        gemini=SimpleNamespace(batches=SimpleNamespace(get=lambda name: batch_job)),
        _registrar_intento_proceso=lambda batch_id: intentos,
        _tiene_metadata_batch=lambda batch_id: True,
        _sellar_batch_procesado=lambda batch_id: estado.sellados.append(batch_id),
        _cerrar_batch_sin_resultado=lambda batch_id, state, motivo=None, filas_error=None:
            estado.cerrados.append((batch_id, state)),
        # La marca deja rastro de a qué lote pertenece cada fila del df estandarizado.
        obtener_info_batch=lambda batch_id, segment_id: pd.Series(
            {"id_aplicativo": "abc_1", "gemini_file_name": "files/xyz", "_marca": batch_id}
        ),
        borrar_archivo_gemini=lambda archivo: None,
        _estandarizar_columnas_sql=_estandarizar,
        _renombrar_detalles_a_atributo_id=lambda df: df,
        _procesar_grupo_batch=lambda batch_id, df, df_trans=None, motivos_gemini=None: estado.grupos.append(batch_id),
    )

    monkeypatch.setattr(auditor_mod, "procesar_respuesta_combinada",
                        lambda response: (pd.Series({"Detalle_101": "OK"}), None))
    monkeypatch.setattr(
        auditor_mod, "finalizar_ejecucion",
        lambda engine, batch_id=None, status=None, error_message=None, **k:
            estado.errores.append((batch_id, status)),
    )

    return fake, estado


def test_un_lote_roto_no_tumba_a_los_demas(monkeypatch):
    """Antes, el ValueError del lote de Dental se escapaba de procesar_batch y se llevaba
    puestas a las corridas de las otras empresas de la misma ventana de polling."""
    fake, estado = _fake_self(monkeypatch)

    AuditorIA.procesar_batch(fake, [(ROTO, "JOB_STATE_SUCCEEDED"), (SANO, "JOB_STATE_SUCCEEDED")])

    assert estado.grupos == [SANO], "el lote sano tiene que guardarse igual"
    assert estado.sellados == [SANO], "y solo él puede sellarse"
    assert (ROTO, "ERROR") in estado.errores, "el lote roto cierra su corrida en ERROR, no en EN_CURSO"


def test_el_lote_roto_se_abandona_al_agotar_intentos(monkeypatch):
    """La red de contención: si nunca se puede procesar, se abandona con un motivo en vez
    de quedar EN_CURSO para siempre. La excepción de un lote no puede saltearla."""
    fake, estado = _fake_self(monkeypatch, intentos=AuditorIA.MAX_INTENTOS_PROCESO)

    AuditorIA.procesar_batch(fake, [(ROTO, "JOB_STATE_SUCCEEDED"), (SANO, "JOB_STATE_SUCCEEDED")])

    assert estado.cerrados == [(ROTO, "ERROR_PROCESO")]
    assert set(estado.sellados) == {SANO, ROTO}


def test_fallo_de_gemini_no_tumba_la_pasada(monkeypatch):
    """Mismo criterio para el GET a Gemini: un error de red en un lote deja a los demás en
    pie y el lote se reintenta (no se sella ni se cierra todavía)."""
    fake, estado = _fake_self(monkeypatch)

    def _get(name):
        if name == ROTO:
            raise ConnectionError("503 Service Unavailable")
        return SimpleNamespace(dest=SimpleNamespace(inlined_responses=[SimpleNamespace(response="ok")]))

    fake.gemini = SimpleNamespace(batches=SimpleNamespace(get=_get))

    AuditorIA.procesar_batch(fake, [(ROTO, "JOB_STATE_SUCCEEDED"), (SANO, "JOB_STATE_SUCCEEDED")])

    assert estado.grupos == [SANO]
    assert estado.sellados == [SANO]
    assert estado.cerrados == [], "todavía le quedan intentos: no se abandona"
