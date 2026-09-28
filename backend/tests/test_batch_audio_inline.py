"""
El audio del batch viaja INLINE en el request, no por la File API.

Desde el 2026-08-14 los modelos Gemini 3.x devuelven 403 PERMISSION_DENIED al
referenciar un `file_uri` de la File API. En el path sync eso explota de frente; en
batch es peor: el job termina SUCCEEDED y el error viene DENTRO de cada respuesta
(code=7), así que Auditor.procesar_batch las descartaba de a una y la corrida quedaba
registrada como EXITO con menos auditorías de las pedidas.

Dos invariantes:
  1. El request lleva los bytes del audio (inline_data), nunca un file_uri. Esto no
     cambió al pasar el lote a archivo: el audio sigue viajando inline, ahora en
     base64 DENTRO del JSONL (ver AuditorIA/batch_cola.py).
  2. Los lotes se cortan por cantidad de llamados y, como red, por tamaño. El techo
     dejó de ser el request de 20 MB (daba ~16 audios por lote, o sea ~125 jobs para
     una corrida de 2000) y pasó a ser cuántos jobs podemos tener en vuelo: 100 en
     toda la cuenta.

Test 100% offline: no toca DB, Gemini ni tokens.
"""
import io
import types as pytypes

import pandas as pd
import pytest

from AuditorIA import gemini
from AuditorIA import batch_cola


class _FakeGemini:
    """El lote sale como ARCHIVO: se sube el JSONL y con eso se crea el job."""

    def __init__(self):
        self.requests = None      # lo llena el espía de escribir_jsonl
        self.subidos = []
        self.batches = pytypes.SimpleNamespace(create=self._create, list=lambda config=None: iter([]))
        self.files = pytypes.SimpleNamespace(upload=self._upload)

    def _upload(self, file, config=None):
        self.subidos.append(file)
        return pytypes.SimpleNamespace(name="files/fake-1")

    def _create(self, model, src, config):
        return pytypes.SimpleNamespace(name="batches/fake-123")


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


PROMPT_INFO = {"text": "Auditá este llamado.", "nombre_id_map": {}, "modelo": "gemini-fake"}


@pytest.fixture
def gemini_falso(monkeypatch, tmp_path):
    monkeypatch.setattr(gemini, "prompt", lambda plantilla_id: dict(PROMPT_INFO))
    monkeypatch.setattr(gemini, "obtener_configuracion_gemini", lambda *a, **kw: {})
    falso = _FakeGemini()

    # El lote se serializa a un JSONL antes de decidir si sale o se encola, y el
    # archivo se borra en cuanto el job existe: espiamos ahí para poder mirar los
    # requests tal como se arman. La serialización en sí se cubre en test_batch_cola.py.
    def _espiar(gemini_api, requests, modelo, ruta=None):
        falso.requests = requests
        destino = ruta or str(tmp_path / f"lote-{len(falso.subidos)}-{id(requests)}.jsonl")
        with open(destino, "w") as f:
            f.write("{}\n")
        return destino

    monkeypatch.setattr(batch_cola, "escribir_jsonl", _espiar)
    return falso


def _preparado(datos: bytes):
    return lambda audio_dir: (io.BytesIO(datos), "audio/mp3", str(audio_dir), str(audio_dir))


def test_el_request_del_batch_lleva_el_audio_inline(monkeypatch, gemini_falso):
    """Ni un file_uri: los bytes del audio ya comprimido viajan en el request."""
    monkeypatch.setattr(gemini, "preparar_contenido", _preparado(b"audio-comprimido"))
    df = pd.DataFrame([{"idInteraccion": "a1", "Segmento": 1, "audio_dir": "/audios/a1.wav"}])

    gemini.process_batch(engine=_FakeEngine(), batch_df=df, plantilla_id=1,
                         gemini_api=gemini_falso, user_id=99)

    parte_audio = gemini_falso.requests[0]["contents"][0].parts[0]
    assert parte_audio.file_data is None
    assert parte_audio.inline_data.data == b"audio-comprimido"
    assert parte_audio.inline_data.mime_type == "audio/mp3"


def _cortar_en_lotes(monkeypatch, cantidad_audios, bytes_por_audio):
    """Corre calidad_batch con process_batch simulado y devuelve los lotes que armó."""
    monkeypatch.setattr(gemini, "preparar_contenido", _preparado(b"x" * bytes_por_audio))
    df = pd.DataFrame([
        {"idInteraccion": f"a{i}", "Segmento": 1, "audio_dir": f"/audios/a{i}.wav"}
        for i in range(cantidad_audios)
    ])

    lotes = []

    def _fake_process_batch(**kwargs):
        lotes.append(kwargs["audios_preparados"])
        return f"batches/fake-{len(lotes)}"

    monkeypatch.setattr(gemini, "process_batch", _fake_process_batch)
    ids = gemini.calidad_batch(engine=_FakeEngine(), df=df, plantilla_id=1,
                               gemini_api=_FakeGemini(), user_id=99)
    return lotes, ids


def test_los_lotes_se_cortan_por_cantidad_de_llamados(monkeypatch):
    """El corte principal: cuántos llamados entran en un job. Con el lote yendo como
    archivo (tope 2 GB) el transporte ya no manda, manda el cupo de 100 jobs."""
    monkeypatch.setattr(gemini.settings, "BATCH_MAX_LLAMADOS_POR_LOTE", 2, raising=False)
    lotes, ids = _cortar_en_lotes(monkeypatch, cantidad_audios=5, bytes_por_audio=1024)

    assert [len(lote) for lote in lotes] == [2, 2, 1]
    assert len(ids) == 3


def test_el_tamano_sigue_siendo_una_red_de_contencion(monkeypatch):
    """Aunque entren 250 llamados, un lote no puede pesar cualquier cosa: el audio
    viaja en base64 (infla ~33%) y el archivo tiene un tope."""
    monkeypatch.setattr(gemini.settings, "BATCH_MAX_LLAMADOS_POR_LOTE", 250, raising=False)
    monkeypatch.setattr(gemini.settings, "BATCH_MAX_BYTES_POR_LOTE", 12 * 1024 * 1024, raising=False)
    lotes, _ids = _cortar_en_lotes(monkeypatch, cantidad_audios=5, bytes_por_audio=6 * 1024 * 1024)

    assert [len(lote) for lote in lotes] == [2, 2, 1]
    for lote in lotes:
        assert sum(len(datos) for datos, _mime in lote.values()) <= 12 * 1024 * 1024


def test_una_corrida_grande_entra_en_pocos_jobs(monkeypatch):
    """La razón de ser del cambio: 2000 audios tienen que caber MUY por debajo de los
    100 jobs en vuelo que admite la cuenta (antes daban ~125)."""
    monkeypatch.setattr(gemini.settings, "BATCH_MAX_LLAMADOS_POR_LOTE", 250, raising=False)
    monkeypatch.setattr(gemini.settings, "BATCH_MAX_BYTES_POR_LOTE", 400 * 1024 * 1024, raising=False)
    # ~1 MB por audio, que es lo que mide un llamado de 4 min a 32 kbps.
    lotes, _ids = _cortar_en_lotes(monkeypatch, cantidad_audios=2000, bytes_por_audio=1024 * 1024)

    assert len(lotes) <= 10


def test_los_audios_que_no_se_pueden_preparar_se_omiten(monkeypatch, gemini_falso):
    """Antes se saltaban los que fallaban la subida; ahora, los que no se pueden leer."""
    def _preparar(audio_dir):
        if str(audio_dir).endswith("roto.wav"):
            return None
        return io.BytesIO(b"ok"), "audio/mp3", str(audio_dir), str(audio_dir)

    monkeypatch.setattr(gemini, "preparar_contenido", _preparar)
    df = pd.DataFrame([
        {"idInteraccion": "a1", "Segmento": 1, "audio_dir": "/audios/a1.wav"},
        {"idInteraccion": "a2", "Segmento": 2, "audio_dir": "/audios/roto.wav"},
    ])

    gemini.process_batch(engine=_FakeEngine(), batch_df=df, plantilla_id=1,
                         gemini_api=gemini_falso, user_id=99)

    assert len(gemini_falso.requests) == 1
