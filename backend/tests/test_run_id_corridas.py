"""
Regresión del agrupado de lotes en el log de auditorías (calidad.AuditExecutionLog).

En modo Batch una corrida NO es una fila: calidad_batch parte los audios en lotes
de 15 MB (el request inline de Gemini no puede ser más grande) y cada lote crea su
propio job con su propia fila de log. La pantalla "Uso de IA > Solicitudes" mostraba
esas filas sueltas, así que una tarea programada de 30 llamados que caía en dos
lotes aparecía como dos corridas de 30 — se leía como que el scheduler se había
ejecutado dos veces, cuando calidad.AuditSchedulerHistory tenía una sola ejecución.

La corrida se identifica ahora con `run_id`, generado UNA vez por run_batch y
compartido por todos sus lotes. Las invariantes que protegen estos tests:
  1. run_batch genera un run_id y lo pone en el contexto de la corrida.
  2. Dos corridas distintas nunca comparten run_id.
  3. process_batch registra ESE run_id en cada lote (los N lotes de una corrida
     abren N filas, todas con el mismo run_id).
  4. El sincrónico sigue funcionando sin run_id explícito (una fila ya es una
     corrida; el INSERT le pone el suyo con NEWID()).

Test 100% offline: no toca DB, Gemini ni tokens.
"""
import io
import types as pytypes
import uuid

import pandas as pd
import pytest

import Auditor as auditor_mod
from Auditor import AuditorIA
from google import genai

from AuditorIA import gemini
from AuditorIA import batch_cola


# --- 1 y 2. run_batch genera el run_id de la corrida ------------------------------

def _correr_run_batch(monkeypatch):
    """run_batch con la descarga y el envío a Gemini simulados; devuelve el
    `contexto_ejecucion` con el que se llamó a calidad_batch."""
    df = pd.DataFrame([{"idInteraccion": "abc", "Segmento": 1, "audio_dir": "/audios/abc.wav"}])
    capturado = {}

    monkeypatch.setattr(auditor_mod, "calidad_batch", lambda **kw: capturado.update(kw) or ["batches/x"])

    fake_self = pytypes.SimpleNamespace(
        engine=None,
        gemini=None,
        _actualizar_estado_task=lambda *a, **kw: None,
        _preparar_datos_auditoria=lambda *a, **kw: (df, 12, 21),
    )
    AuditorIA.run_batch(fake_self, user_id=99, plantilla_id=1, cantidad=3, empresa=12, campana=21)
    return capturado["contexto_ejecucion"]


def test_run_batch_genera_un_run_id_para_la_corrida(monkeypatch):
    contexto = _correr_run_batch(monkeypatch)

    # Tiene que ser un UUID válido: viaja como clave a una columna UNIQUEIDENTIFIER.
    assert uuid.UUID(contexto["run_id"])


def test_dos_corridas_no_comparten_run_id(monkeypatch):
    primera = _correr_run_batch(monkeypatch)["run_id"]
    segunda = _correr_run_batch(monkeypatch)["run_id"]

    assert primera != segunda


# --- 3. process_batch: todos los lotes de la corrida escriben el mismo run_id ------

def _preparado_falso(audio_dir):
    """Sustituto de gemini_files.preparar_contenido (el audio viaja inline)."""
    nombre = str(audio_dir)
    return io.BytesIO(f"audio-de-{nombre}".encode()), "audio/mp3", nombre, nombre


class _FakeGemini:
    """Un `name` distinto por lote, como haría Gemini con dos jobs de una corrida.

    El lote se manda como ARCHIVO (upload + create) y antes se consulta el cupo de
    batches en vuelo: ver AuditorIA/batch_cola.py.
    """

    def __init__(self):
        self.creados = 0
        self.batches = pytypes.SimpleNamespace(
            create=self._create, list=lambda config=None: iter([])
        )
        self.files = pytypes.SimpleNamespace(
            upload=lambda file, config=None: pytypes.SimpleNamespace(name="files/fake-1")
        )
        self._api_client = genai.Client(api_key="fake")._api_client

    def _create(self, model, src, config):
        self.creados += 1
        return pytypes.SimpleNamespace(name=f"batches/fake-{self.creados}")


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


LOTE = pd.DataFrame([
    {"idInteraccion": "a1", "Segmento": 1, "audio_dir": "/audios/a1.wav",
     "empresa_id": 12, "campana_id": 21},
])


@pytest.fixture
def filas_del_log(monkeypatch):
    """Junta los kwargs de cada fila que process_batch abre en el log."""
    filas = []
    monkeypatch.setattr(gemini, "prompt", lambda plantilla_id: {
        "text": "Auditá este llamado.", "nombre_id_map": {}, "modelo": "gemini-fake",
    })
    monkeypatch.setattr(gemini, "obtener_configuracion_gemini", lambda *a, **kw: {})
    monkeypatch.setattr(gemini, "preparar_contenido", _preparado_falso)
    monkeypatch.setattr(batch_cola, "iniciar_ejecucion", lambda engine, **kw: filas.append(kw))
    return filas


def test_los_lotes_de_una_corrida_comparten_run_id(filas_del_log):
    """Dos lotes = dos filas de log = una sola corrida en la pantalla."""
    contexto = {"trigger_source": "scheduler", "cantidad_solicitada": 30,
                "run_id": "8f6b1c2e-0000-4000-8000-000000000001"}
    api = _FakeGemini()

    for _ in range(2):  # el corte por tamaño lo hace calidad_batch; acá simulamos dos lotes
        gemini.process_batch(
            engine=_FakeEngine(), batch_df=LOTE.copy(), plantilla_id=1,
            gemini_api=api, user_id=99, contexto_ejecucion=contexto,
        )

    assert len(filas_del_log) == 2
    assert {f["batch_id"] for f in filas_del_log} == {"batches/fake-1", "batches/fake-2"}
    assert {f["run_id"] for f in filas_del_log} == {"8f6b1c2e-0000-4000-8000-000000000001"}


def test_un_contexto_sin_run_id_no_rompe_el_registro(filas_del_log):
    """Retrocompatibilidad: un lote encolado por código que no lo manda (o un batch
    en vuelo de antes) se registra igual, con run_id None -> NEWID() en el INSERT."""
    gemini.process_batch(
        engine=_FakeEngine(), batch_df=LOTE.copy(), plantilla_id=1,
        gemini_api=_FakeGemini(), user_id=99,
        contexto_ejecucion={"trigger_source": "manual", "cantidad_solicitada": 1},
    )

    assert filas_del_log[0]["run_id"] is None
