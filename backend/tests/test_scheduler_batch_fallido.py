"""
Regresión de las tareas programadas Batch que morían sin encolar nada y quedaban
"finalizadas con éxito" (caso ALARMIX, todos los días desde por lo menos el 25/08/2026).

`Auditor.run_batch` atrapa sus propias excepciones (p. ej. "Login en CXOne falló
después de 3 intentos") y devuelve None. `_procesar_tarea_individual` ignoraba el
retorno: marcaba EXITO en AuditSchedulerHistory, corría next_run_time al día siguiente
y logueaba "Lote enviado: N filas". Esa corrida no se auditaba nunca y nadie se
enteraba.

Invariantes:
  1. None de run_batch es un ERROR de la corrida (historial en ERROR, next_run_time sin
     tocar: el scheduler la vuelve a tomar en la próxima pasada).
  2. [] (no había nada para auditar) y una lista de lotes siguen siendo EXITO.

Test 100% offline: engine falso que solo registra el SQL, Auditor stub.
"""
from types import SimpleNamespace

import pytest

from app import tasks


class _Conn:
    def __init__(self, sentencias):
        self.sentencias = sentencias

    def execute(self, sentencia, params=None):
        self.sentencias.append((" ".join(str(sentencia).split()), params or {}))
        return SimpleNamespace(scalar=lambda: 99)


class _Engine:
    def __init__(self):
        self.sentencias = []

    def begin(self):
        conn = _Conn(self.sentencias)

        class _Ctx:
            def __enter__(self_inner):
                return conn

            def __exit__(self_inner, *exc):
                return False

        return _Ctx()


TAREA = {
    "id": 13, "task_name": "Encuesta x Op", "rango_dinamico": "ayer",
    "parametros_json": '{"is_batch": true, "por_operador": true}',
    "plantilla_id": 16, "created_by": 10000002, "cantidad": 10, "empresa": "12", "campana": "21",
    "send_gsheets": True, "gsheet_id": "sheet", "gsheet_name": "BASE", "send_email": False,
    "email_addresses": "", "column_template_id": None, "frecuencia": "diaria",
    "hora_ejecucion": "08:00:00", "dias_semana": None, "retry_count": 0,
}


@pytest.fixture
def entorno(monkeypatch):
    engine = _Engine()
    mails = []
    monkeypatch.setattr(tasks, "engine", engine)
    monkeypatch.setattr(tasks, "_scheduler_fuera_de_alcance", lambda tarea: False)
    monkeypatch.setattr(tasks, "preparar_tipificacion_para_consulta", lambda **k: [])
    monkeypatch.setattr(tasks.Envios_mail_manager, "enviar_correo_base", lambda **k: mails.append(k))
    return engine


def _correr(monkeypatch, entorno, retorno):
    monkeypatch.setattr(tasks, "Auditor", SimpleNamespace(run_batch=lambda **k: retorno))
    tasks._procesar_tarea_individual(dict(TAREA))
    return [sql for sql, _ in entorno.sentencias]


def test_corrida_que_no_encola_queda_en_error_y_se_reintenta(monkeypatch, entorno):
    sentencias = _correr(monkeypatch, entorno, None)

    assert any("SET status = 'ERROR'" in s for s in sentencias)
    assert not any("SET status = 'EXITO'" in s for s in sentencias)
    # Sin next_run_time nuevo: la próxima pasada del scheduler la vuelve a tomar.
    assert not any("next_run_time" in s for s in sentencias)
    assert any("SET retry_count = :rc" in s for s in sentencias)


def test_sin_nada_para_auditar_sigue_siendo_exito(monkeypatch, entorno):
    sentencias = _correr(monkeypatch, entorno, [])

    assert any("SET status = 'EXITO'" in s for s in sentencias)


def test_con_lotes_encolados_sigue_siendo_exito(monkeypatch, entorno):
    sentencias = _correr(monkeypatch, entorno, ["batches/abc"])

    assert any("SET status = 'EXITO'" in s for s in sentencias)
    assert any("next_run_time = :nr" in s for s in sentencias)
