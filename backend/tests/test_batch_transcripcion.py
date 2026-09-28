"""
Regresión de la transcripción en modo Batch.

El modo batch aceptaba `transcribir` pero lo descartaba: run_batch nunca se lo pasaba a
calidad_batch, así que el request a Gemini se armaba con el esquema de calidad sola y la
transcripción no existía. Acá se cubren las dos mitades del arreglo:

  1. IDA: process_batch pide la transcripción ANIDADA en el request de calidad (una sola
     lectura del audio), y solo para los llamados que corresponde (no chats, no
     ya transcriptos).
  2. VUELTA: procesar_respuesta_combinada desanida la transcripción de la respuesta del
     batch sin ensuciar las columnas de la auditoría.

Test 100% offline: no toca DB, Gemini ni tokens.
"""
import io
import json
import types as pytypes

import pandas as pd
import pytest
from google.genai import types

from AuditorIA import gemini
from AuditorIA import batch_cola


# --- Dobles de prueba -------------------------------------------------------------

def _preparado_falso(audio_dir):
    """Sustituto de gemini_files.preparar_contenido: (file_io, mime, display, log).

    El audio del batch viaja inline (bytes) en el request, no por la File API.
    """
    nombre = str(audio_dir)
    mime = "application/json" if nombre.endswith('.json') else "audio/mp3"
    return io.BytesIO(f"audio-de-{nombre}".encode()), mime, nombre, nombre


class _FakeBatches:
    def __init__(self):
        self.requests = None      # lo llena el espía de escribir_jsonl

    def create(self, model, src, config):
        return pytypes.SimpleNamespace(name="batches/fake-123")

    def list(self, config=None):
        return iter([])


class _FakeGemini:
    """El lote sale como ARCHIVO: se sube el JSONL y con eso se crea el job."""

    def __init__(self):
        self.batches = _FakeBatches()
        self.files = pytypes.SimpleNamespace(
            upload=lambda file, config=None: pytypes.SimpleNamespace(name="files/fake-1")
        )


class _FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def begin(self):
        return pytypes.SimpleNamespace(commit=lambda: None, rollback=lambda: None)

    def execute(self, *args, **kwargs):
        return None


class _FakeEngine:
    def connect(self):
        return _FakeConnection()


PROMPT_INFO = {
    'text': 'Auditá este llamado.',
    'system': 'Sos un auditor.',
    'response_schema': types.Schema(
        type=types.Type.OBJECT,
        required=['saludo_inicial'],
        properties={'saludo_inicial': types.Schema(type=types.Type.STRING)},
    ),
    'nombre_id_map': {'saludo_inicial': 202},
    'modelo': 'gemini-fake',
}


@pytest.fixture
def batch_df():
    """Tres interacciones: una ya transcripta, una nueva, y un chat (sin audio)."""
    return pd.DataFrame([
        {'idInteraccion': 'abc', 'Segmento': 1, 'audio_dir': '/audios/abc.wav'},
        {'idInteraccion': 'xyz', 'Segmento': 2, 'audio_dir': '/audios/xyz.wav'},
        {'idInteraccion': 'chat', 'Segmento': 3, 'audio_dir': '/chats/chat.json'},
    ])


@pytest.fixture
def gemini_falso(monkeypatch, tmp_path):
    monkeypatch.setattr(gemini, 'prompt', lambda plantilla_id: dict(PROMPT_INFO))
    monkeypatch.setattr(gemini, 'preparar_contenido', _preparado_falso)
    # 'abc_1' ya tiene transcripción guardada; 'xyz_2' no.
    monkeypatch.setattr(gemini, 'ids_con_transcripcion', lambda engine, ids: {'abc_1'})
    falso = _FakeGemini()

    # El lote se serializa a un JSONL antes de salir (y el archivo se borra en cuanto
    # el job existe): se espía ahí para mirar los requests tal como se arman.
    def _espiar(gemini_api, requests, modelo, ruta=None):
        falso.batches.requests = requests
        destino = ruta or str(tmp_path / "lote.jsonl")
        with open(destino, "w") as f:
            f.write("{}\n")
        return destino

    monkeypatch.setattr(batch_cola, 'escribir_jsonl', _espiar)
    return falso


def _correr_process_batch(gemini_falso, batch_df, transcribir):
    gemini.process_batch(
        engine=_FakeEngine(), batch_df=batch_df, plantilla_id=1,
        gemini_api=gemini_falso, user_id=99, transcribir=transcribir,
    )
    return gemini_falso.batches.requests


def _pide_transcripcion(request) -> bool:
    return 'Transcripcion' in (request['config'].response_schema.properties or {})


def _texto_del_prompt(request) -> str:
    return request['contents'][0].parts[1].text


# --- IDA: cómo se arma el request del batch ---------------------------------------

def test_batch_pide_la_transcripcion_anidada_en_el_request_de_calidad(gemini_falso, batch_df):
    """Lo que estaba roto: con transcribir=True el request salía igual que sin él."""
    requests = _correr_process_batch(gemini_falso, batch_df, transcribir=True)

    # El llamado nuevo pide calidad + transcripción en UN solo request (no hay un
    # segundo job de batch solo para transcribir).
    assert len(requests) == 3
    assert _pide_transcripcion(requests[1])
    assert 'TRANSCRIPCIÓN ADICIONAL' in _texto_del_prompt(requests[1])

    # Y la calidad sigue intacta en ese mismo request.
    assert 'saludo_inicial' in requests[1]['config'].response_schema.properties


def test_no_se_retranscribe_lo_ya_transcripto_ni_los_chats(gemini_falso, batch_df):
    """Mismo audio = misma transcripción: no se paga dos veces. Los chats no tienen audio."""
    requests = _correr_process_batch(gemini_falso, batch_df, transcribir=True)

    assert not _pide_transcripcion(requests[0])  # 'abc_1' ya transcripto
    assert not _pide_transcripcion(requests[2])  # chat (.json)
    for request in (requests[0], requests[2]):
        assert 'TRANSCRIPCIÓN ADICIONAL' not in _texto_del_prompt(request)


def test_sin_transcribir_el_request_no_cambia(gemini_falso, batch_df):
    """El batch de solo calidad (el caso de siempre) no debe verse afectado."""
    requests = _correr_process_batch(gemini_falso, batch_df, transcribir=False)

    assert len(requests) == 3
    assert not any(_pide_transcripcion(request) for request in requests)


# --- VUELTA: cómo se lee la respuesta del batch -----------------------------------

def _respuesta_falsa(payload: dict):
    parte = pytypes.SimpleNamespace(text=json.dumps(payload), thought=False)
    contenido = pytypes.SimpleNamespace(parts=[parte])
    return pytypes.SimpleNamespace(
        candidates=[pytypes.SimpleNamespace(content=contenido, finish_reason='STOP')],
        usage_metadata=pytypes.SimpleNamespace(
            prompt_token_count=1000, candidates_token_count=500, thoughts_token_count=100,
        ),
        text=json.dumps(payload),
    )


TRANSCRIPCION = {
    'metadata': {'overallConfidence': 0.9, 'languageCode': 'es-AR', 'audioDurationSeconds': 12.5},
    'segments': [{'speakerLabel': 'Agente', 'startTime': 0.0, 'endTime': 2.0,
                  'confidence': 0.9, 'text': ['hola', 'buenos', 'días']}],
    'analytics': {'keywords': ['saludo'], 'entities': []},
}


def test_la_respuesta_del_batch_separa_calidad_y_transcripcion():
    serie_cal, serie_trans = gemini.procesar_respuesta_combinada(
        _respuesta_falsa({'saludo_inicial': 'OK', 'Transcripcion': TRANSCRIPCION})
    )

    # La transcripción NO puede quedar como un atributo de calidad: 'Detalle_Transcripcion'
    # no resuelve a ningún AtributoID y la fila entera se descartaría al guardar.
    assert 'Detalle_Transcripcion' not in serie_cal.index
    assert serie_cal['Detalle_saludo_inicial'] == 'OK'

    assert serie_trans is not None
    assert serie_trans['metadata']['languageCode'] == 'es-AR'
    assert serie_trans['segments'][0]['speakerLabel'] == 'Agente'

    # El costo del único llamado no se cuenta dos veces: el input/thinking queda en
    # calidad y el output se reparte entre ambas.
    assert serie_trans['input_tokens'] == 0
    assert serie_trans['thoughts_tokens'] == 0
    assert serie_cal['output_tokens'] + serie_trans['output_tokens'] == 500


def test_batch_sin_transcripcion_se_lee_igual_que_antes():
    """Batches de solo calidad (y los enviados antes de esta feature) siguen andando."""
    serie_cal, serie_trans = gemini.procesar_respuesta_combinada(
        _respuesta_falsa({'saludo_inicial': 'OK'})
    )

    assert serie_trans is None
    assert serie_cal['Detalle_saludo_inicial'] == 'OK'
    assert serie_cal['output_tokens'] == 500  # nada que descontar
