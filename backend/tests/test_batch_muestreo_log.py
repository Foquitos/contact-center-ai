"""
Regresión del muestreo por operador/tipificación en el log de auditorías (modo Batch).

En la pantalla "Uso de IA > Solicitudes", cada corrida muestra si se auditó por operador
o por tipificación (con el desglose de cuántas interacciones le tocaron a cada grupo,
columnas por_operador / por_tipificacion / desglose_muestreo de calidad.AuditExecutionLog).
El dato aparecía solo en las corridas sincrónicas: run_batch recibía los flags pero no los
ponía en el `contexto_ejecucion` que viaja hasta process_batch, así que la fila del batch
se insertaba siempre con 0/NULL.

Las tres invariantes que protegen estos tests:
  1. run_batch propaga los flags de muestreo en el contexto de la corrida.
  2. process_batch los registra y calcula el desglose sobre el df TODAVÍA sin
     estandarizar (en batch la estandarización ocurre horas después, al procesar la
     respuesta), resolviendo las columnas crudas de cada cliente por alias.
  3. Una corrida sin muestreo por grupo se sigue registrando igual que siempre.

Test 100% offline: no toca DB, Gemini ni tokens.
"""
import io
import types as pytypes

import pandas as pd
import pytest

import Auditor as auditor_mod
from Auditor import AuditorIA
from google import genai

from AuditorIA import gemini
from AuditorIA import batch_cola
from AuditorIA.execution_log import calcular_desglose_muestreo


# --- 1. run_batch: los flags viajan en el contexto ---------------------------------

def _correr_run_batch(monkeypatch, **kwargs_muestreo):
    """Corre run_batch con la descarga y el envío a Gemini simulados, y devuelve el
    `contexto_ejecucion` con el que se llamó a calidad_batch."""
    df = pd.DataFrame([{"idInteraccion": "abc", "Segmento": 1, "audio_dir": "/audios/abc.wav"}])
    capturado = {}

    def _fake_calidad_batch(**kwargs):
        capturado.update(kwargs)
        return ["batches/fake-123"]

    monkeypatch.setattr(auditor_mod, "calidad_batch", _fake_calidad_batch)

    fake_self = pytypes.SimpleNamespace(
        engine=None,
        gemini=None,
        _actualizar_estado_task=lambda *a, **kw: None,
        _preparar_datos_auditoria=lambda *a, **kw: (df, 12, 21),
    )

    AuditorIA.run_batch(
        fake_self, user_id=99, plantilla_id=1, cantidad=3,
        empresa=12, campana=21, **kwargs_muestreo,
    )
    return capturado["contexto_ejecucion"]


def test_run_batch_propaga_el_muestreo_por_grupo(monkeypatch):
    """Lo que estaba roto: los flags se quedaban en run_batch y nunca llegaban al log."""
    contexto = _correr_run_batch(monkeypatch, por_operador=True, por_tipificacion=True)

    assert contexto["por_operador"] is True
    assert contexto["por_tipificacion"] is True


def test_run_batch_sin_muestreo_manda_los_flags_en_falso(monkeypatch):
    contexto = _correr_run_batch(monkeypatch)

    assert contexto["por_operador"] is False
    assert contexto["por_tipificacion"] is False


# --- 2. process_batch: qué fila se abre en AuditExecutionLog -----------------------

def _preparado_falso(audio_dir):
    """Sustituto de gemini_files.preparar_contenido: (file_io, mime, display, log).

    El audio del batch viaja inline (bytes) en el request, no por la File API.
    """
    nombre = str(audio_dir)
    return io.BytesIO(f"audio-de-{nombre}".encode()), "audio/mp3", nombre, nombre


class _FakeGemini:
    """Cliente completo: el lote se manda como ARCHIVO (se sube y después se crea el
    job), y antes se consulta el cupo de batches en vuelo. Ver AuditorIA/batch_cola.py."""

    def __init__(self):
        self.batches = pytypes.SimpleNamespace(
            create=lambda model, src, config: pytypes.SimpleNamespace(name="batches/fake-123"),
            list=lambda config=None: iter([]),
        )
        self.files = pytypes.SimpleNamespace(
            upload=lambda file, config=None: pytypes.SimpleNamespace(name="files/fake-1")
        )
        self._api_client = genai.Client(api_key="fake")._api_client


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
    "text": "Auditá este llamado.",
    "nombre_id_map": {},
    "modelo": "gemini-fake",
}


@pytest.fixture
def log_abierto(monkeypatch):
    """Captura los kwargs con los que process_batch abre la fila del log."""
    registrado = {}
    monkeypatch.setattr(gemini, "prompt", lambda plantilla_id: dict(PROMPT_INFO))
    monkeypatch.setattr(gemini, "obtener_configuracion_gemini", lambda *a, **kw: {})
    monkeypatch.setattr(gemini, "preparar_contenido", _preparado_falso)
    monkeypatch.setattr(batch_cola, "iniciar_ejecucion", lambda engine, **kw: registrado.update(kw))
    return registrado


# Un lote tal como sale de la descarga: columnas CRUDAS de Mitrol (LoginId / Tipificación),
# sin pasar por _estandarizar_columnas_sql (eso ocurre recién al procesar la respuesta).
LOTE_CRUDO = pd.DataFrame([
    {"idInteraccion": "a1", "Segmento": 1, "audio_dir": "/audios/a1.wav",
     "LoginId": 101, "Tipificación": "Reclamo", "empresa_id": 12, "campana_id": 21},
    {"idInteraccion": "a2", "Segmento": 1, "audio_dir": "/audios/a2.wav",
     "LoginId": 101, "Tipificación": "Consulta", "empresa_id": 12, "campana_id": 21},
    {"idInteraccion": "a3", "Segmento": 1, "audio_dir": "/audios/a3.wav",
     "LoginId": 202, "Tipificación": "Reclamo", "empresa_id": 12, "campana_id": 21},
])


def _process_batch(contexto):
    gemini.process_batch(
        engine=_FakeEngine(), batch_df=LOTE_CRUDO.copy(), plantilla_id=1,
        gemini_api=_FakeGemini(), user_id=99, contexto_ejecucion=contexto,
    )


def test_el_batch_registra_el_muestreo_y_su_desglose(log_abierto):
    _process_batch({
        "trigger_source": "manual", "cantidad_solicitada": 1,
        "por_operador": True, "por_tipificacion": True,
    })

    assert log_abierto["por_operador"] is True
    assert log_abierto["por_tipificacion"] is True
    assert log_abierto["desglose_muestreo"] == {
        "operadores": {"101": 2, "202": 1},
        "tipificaciones": {"Reclamo": 2, "Consulta": 1},
    }
    # Con muestreo por grupo, `cantidad` es "por cada grupo": el total real es el
    # que efectivamente se mandó a auditar, igual que en el path sync.
    assert log_abierto["cantidad_solicitada"] == 3


def test_el_batch_sin_muestreo_se_registra_como_siempre(log_abierto):
    _process_batch({"trigger_source": "manual", "cantidad_solicitada": 50})

    assert log_abierto["por_operador"] is False
    assert log_abierto["por_tipificacion"] is False
    assert log_abierto["desglose_muestreo"] is None
    assert log_abierto["cantidad_solicitada"] == 50


def test_solo_se_desglosa_el_criterio_pedido(log_abierto):
    _process_batch({
        "trigger_source": "manual", "cantidad_solicitada": 1, "por_operador": True,
    })

    assert log_abierto["desglose_muestreo"] == {"operadores": {"101": 2, "202": 1}}


# --- 3. El desglose sobre df crudos vs. estandarizados ----------------------------

def test_desglose_resuelve_las_columnas_de_cada_cliente():
    """Cada descarga nombra distinto al operador; el desglose no puede depender de
    que el df ya haya pasado por _estandarizar_columnas_sql."""
    crudo = calcular_desglose_muestreo(LOTE_CRUDO, por_operador=True)
    estandarizado = calcular_desglose_muestreo(
        pd.DataFrame({"operador_usuario": ["101", "101", "202"]}), por_operador=True
    )

    assert crudo == estandarizado == {"operadores": {"101": 2, "202": 1}}


def test_desglose_sin_columna_util_no_rompe():
    """Un df sin ninguna columna de operador (o vacío) no puede tumbar el registro."""
    assert calcular_desglose_muestreo(pd.DataFrame({"x": [1]}), por_operador=True) is None
    assert calcular_desglose_muestreo(pd.DataFrame(), por_operador=True) is None
    assert calcular_desglose_muestreo(LOTE_CRUDO) is None
