"""
Regresión del mail de resultado del modo Batch (2026-08-19).

Dos bugs que se arreglan juntos porque son el mismo agujero: el mail salía desde
`_procesar_grupo_batch`, o sea UNA VEZ POR LOTE de Gemini, y a esa altura —horas
después de encolar— el único destinatario a mano era el mail interno del usuario que
creó la corrida.

  1. Una corrida de Vantix que se parte en 14 lotes mandaba 14 mails, cada uno con una
     fracción de la auditoría y ninguno con el total.
  2. Los `email_addresses` configurados en la tarea programada NO recibían nada: el
     resultado le llegaba solo a quien la había creado (reportado en Vantix).

Las invariantes que protegen estos tests:
  1. Mientras quede un lote en vuelo, no se manda nada.
  2. El último lote en cerrar manda UN mail con las auditorías de TODA la corrida.
  3. Los destinatarios son los configurados en la tarea; el creador es solo el fallback.
  4. Una corrida ya notificada no vuelve a mandar mail.
  5. Una corrida que no auditó nada no manda mail de resultado (pero sí el log interno).

Test 100% offline: no toca DB, Gemini, mail ni tokens.
"""
from types import SimpleNamespace

import pandas as pd
import pytest

import Auditor as auditor_mod
from Auditor import AuditorIA


BATCH = "batches/lote-2"
RUN = "8f6b1c2e-0000-4000-8000-000000000009"


@pytest.fixture
def entorno(monkeypatch):
    """Captura los mails enviados y las marcas que se dejan en el log."""
    estado = SimpleNamespace(mails=[], logs_admin=[], marcas=[])

    monkeypatch.setattr(auditor_mod.Envios_mail_manager, "enviar_correo_base",
                        lambda **kw: estado.mails.append(kw))
    monkeypatch.setattr(auditor_mod, "marcar_envio_corrida",
                        lambda engine, **kw: estado.marcas.append(kw))
    monkeypatch.setattr(auditor_mod, "obtener_contexto", lambda engine, **kw: {
        "campana": 21, "empresa": 12, "plantilla_id": 23, "user_id": "30111222",
        "scheduler_id": 7, "scheduler_name": "Cortes abruptos - ALARMIX",
        "trigger_source": "scheduler", "modelo": "gemini-fake", "started_at": None,
    })
    return estado


def _fake_self(estado, *, export=None):
    """AuditorIA de mentira: solo lo que toca _notificar_corrida_si_termino."""
    df_export = export if export is not None else pd.DataFrame({"IdAplicativo": [1, 2, 3]})
    fake = SimpleNamespace(
        engine=None,
        _obtener_export_auditorias=lambda **kw: df_export,
        _column_template_de_scheduler=lambda scheduler_id: None,
        _enviar_mail_resultado_corrida=lambda **kw: (
            estado.mails.append(kw) or True
        ),
        _enviar_mail_log_admin=lambda **kw: estado.logs_admin.append(kw),
        _fecha_local=AuditorIA._fecha_local,
    )
    # La resolución de destinatarios es justamente lo que se está probando: se usa la
    # real, atada a mano al fake (un SimpleNamespace no liga métodos como una clase).
    fake._destinatarios_de_la_corrida = (
        lambda resumen, user_id: AuditorIA._destinatarios_de_la_corrida(fake, resumen, user_id)
    )
    return fake


def _corrida(monkeypatch, **campos):
    """Lo que devuelve corrida_terminada: None = todavía hay lotes en vuelo."""
    base = {
        "run_id": RUN, "lotes": 2, "id_aplicativos": ["a1", "a2", "a3"],
        "filas_auditadas": 3, "filas_error": 0,
        "tokens": {"input_tokens": 10, "output_tokens": 5, "thoughts_tokens": 0},
        "mail_destinatarios": "calidad@acme.example, supervisor@acme.example",
        "ya_notificada": False, "estados": ["EXITO", "EXITO"],
    }
    base.update(campos)
    monkeypatch.setattr(auditor_mod, "corrida_terminada", lambda engine, **kw: base)
    return base


# --- 1. Mientras quede un lote abierto, silencio ----------------------------------

def test_no_manda_nada_si_quedan_lotes_en_vuelo(entorno, monkeypatch):
    """El bug original: cada lote notificaba lo suyo apenas terminaba."""
    monkeypatch.setattr(auditor_mod, "corrida_terminada", lambda engine, **kw: None)

    AuditorIA._notificar_corrida_si_termino(_fake_self(entorno), BATCH)

    assert entorno.mails == []
    assert entorno.logs_admin == []


# --- 2 y 3. El último cierra: un mail, con todo y a quien corresponde -------------

def test_el_ultimo_lote_manda_un_solo_mail_con_toda_la_corrida(entorno, monkeypatch):
    _corrida(monkeypatch)

    AuditorIA._notificar_corrida_si_termino(_fake_self(entorno), BATCH)

    assert len(entorno.mails) == 1, "un mail por corrida, no uno por lote"
    mail = entorno.mails[0]
    # El adjunto es el de la corrida entera (los ids de todos los lotes), no el del lote.
    assert len(mail["df_export"]) == 3
    assert mail["lotes"] == 2
    assert len(entorno.logs_admin) == 1


def test_manda_a_los_destinatarios_configurados_y_no_solo_al_creador(entorno, monkeypatch):
    """El bug de Vantix: la lista de la tarea programada no recibía nada."""
    _corrida(monkeypatch)

    AuditorIA._notificar_corrida_si_termino(_fake_self(entorno), BATCH)

    assert entorno.mails[0]["destinatarios"] == ["calidad@acme.example", "supervisor@acme.example"]
    assert entorno.marcas[0]["mail_enviado"] is True


def test_sin_destinatarios_configurados_cae_al_creador(entorno, monkeypatch):
    """Auditoría manual (o tarea con el mail apagado): sigue avisándole a quien la corrió."""
    _corrida(monkeypatch, mail_destinatarios=None)

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def execute(self, *a, **kw): return SimpleNamespace(fetchone=lambda: ("juan@acme.example",))

    fake = _fake_self(entorno)
    fake.engine = SimpleNamespace(connect=lambda: _Conn())

    AuditorIA._notificar_corrida_si_termino(fake, BATCH)

    assert entorno.mails[0]["destinatarios"] == ["juan@acme.example"]


# --- 4 y 5. Los bordes -----------------------------------------------------------

def test_una_corrida_ya_notificada_no_repite_el_mail(entorno, monkeypatch):
    _corrida(monkeypatch, ya_notificada=True)

    AuditorIA._notificar_corrida_si_termino(_fake_self(entorno), BATCH)

    assert entorno.mails == []


def test_corrida_sin_auditorias_no_manda_resultado_pero_si_el_log_interno(entorno, monkeypatch):
    """El caso ALARMIX: los dos lotes fallaron. No hay nada que adjuntar, pero el admin
    tiene que enterarse igual de que la corrida terminó en la nada."""
    _corrida(monkeypatch, id_aplicativos=[], filas_auditadas=0, filas_error=30,
             estados=["ERROR", "ERROR"])

    AuditorIA._notificar_corrida_si_termino(_fake_self(entorno), BATCH)

    assert entorno.mails == []
    assert len(entorno.logs_admin) == 1
    assert entorno.marcas[0]["mail_enviado"] is False


# --- Los helpers sueltos ---------------------------------------------------------

def test_ids_auditados_serializa_para_el_log():
    df = pd.DataFrame({"id_aplicativo": ["a1", None, "a3", ""]})

    assert AuditorIA._ids_auditados(df) == "a1,a3"


def test_ids_auditados_sin_columna_no_rompe():
    assert AuditorIA._ids_auditados(pd.DataFrame({"otra": [1]})) is None


# --- 6. Lotes que todavía esperan cupo en Gemini (2026-08-26) ----------------------
# Desde que un lote sin cupo queda en calidad.BatchPendientes (ver AuditorIA/batch_cola.py),
# "no quedan filas EN_CURSO" ya no alcanza para dar la corrida por terminada: los lotes
# encolados NO tienen fila en el log todavía (la abre el despacho, cuando el job existe).

class _ConnCorrida:
    """Responde las tres consultas de corrida_terminada en orden."""

    def __init__(self, abiertos, esperando, existe_cola=True):
        self.abiertos = abiertos
        self.esperando = esperando
        self.existe_cola = existe_cola
        self.consultas = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sentencia, params=None):
        sql = " ".join(str(sentencia).split())
        self.consultas.append(sql)
        if "SELECT TOP 1 run_id" in sql:
            return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: {"run_id": RUN}))
        if "AuditExecutionLog WHERE run_id" in sql and "EN_CURSO" in sql:
            return SimpleNamespace(scalar=lambda: self.abiertos)
        if "BatchPendientes" in sql:
            if not self.existe_cola:
                raise RuntimeError("Invalid object name 'calidad.BatchPendientes'.")
            return SimpleNamespace(scalar=lambda: self.esperando)
        raise AssertionError(f"consulta inesperada: {sql[:80]}")


class _EngineCorrida:
    def __init__(self, conn):
        self._conn = conn

    def connect(self):
        return self._conn


def test_no_se_notifica_si_quedan_lotes_esperando_cupo():
    """El caso nuevo: 0 lotes EN_CURSO pero 2 todavía en la cola. Sin este chequeo el
    mail salía con una fracción de las auditorías y los lotes que faltaban cerraban
    después contra una corrida ya notificada (o sea, sin que nadie viera su resultado)."""
    from AuditorIA.execution_log import corrida_terminada

    conn = _ConnCorrida(abiertos=0, esperando=2)
    assert corrida_terminada(_EngineCorrida(conn), batch_id=BATCH) is None
    assert any("BatchPendientes" in c for c in conn.consultas)


def test_si_la_tabla_de_la_cola_no_existe_la_corrida_cierra_igual():
    """La migración la corre Ignacio a mano: hasta entonces, el comportamiento tiene
    que ser exactamente el de antes y no romper la notificación."""
    from AuditorIA.execution_log import corrida_terminada

    conn = _ConnCorrida(abiertos=0, esperando=0, existe_cola=False)
    corrida_terminada(_EngineCorrida(conn), batch_id=BATCH)

    # Lo que importa: el error de la tabla inexistente no cortó la evaluación, siguió
    # de largo hasta las consultas del resumen de la corrida.
    assert any("id_aplicativos" in c for c in conn.consultas)
