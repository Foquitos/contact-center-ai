"""
El llamado a Gemini del asistente de plantillas (`_generar_json`): reintentos y logs.

POR QUÉ EXISTE
--------------
Todos los tests del asistente mockean `_generar_json` para no gastar tokens, así que su
código no lo ejecutaba NADIE. Y ahí se coló un error que llegó a producción: las líneas de
tiempo que se agregaron el 2026-08-21 usaban un `logger` que el módulo no definía. El
llamado a Gemini salía bien (200 OK, ya facturado) y la revisión moría justo después con un
`NameError` al intentar loguear que había salido bien.

Así que esto ejerce la función de verdad, con un cliente falso: sin red, sin tokens y sin
DB, pero corriendo todas sus ramas —incluidas las de log, que es donde estaba la bomba.

Correr: pytest tests/test_asistente_plantillas_llamado.py -m "not tokens"
"""
import json
import logging

import pytest

from AuditorIA import asistente_plantillas as ap


# --- Dobles de la respuesta del SDK ---------------------------------------- #
class _Parte:
    def __init__(self, text, thought=False):
        self.text = text
        self.thought = thought


class _Candidato:
    def __init__(self, partes):
        self.content = type("Contenido", (), {"parts": partes})()


class _Respuesta:
    def __init__(self, partes, texto=""):
        self.candidates = [_Candidato(partes)]
        self.text = texto
        self.usage_metadata = None


class _ClienteFalso:
    """Devuelve (o lanza) lo que se le cargue, y cuenta los llamados."""

    def __init__(self, guion):
        self._guion = list(guion)
        self.llamadas = 0
        self.models = self

    def generate_content(self, **kwargs):
        self.llamadas += 1
        siguiente = self._guion.pop(0)
        if isinstance(siguiente, Exception):
            raise siguiente
        return siguiente


@pytest.fixture
def llamar(monkeypatch):
    """Ejecuta _generar_json contra un cliente falso. Devuelve (resultado, cliente)."""
    def _correr(guion):
        cliente = _ClienteFalso(guion)
        monkeypatch.setattr(ap, "_get_client", lambda: cliente)
        # El consumo se registra en la BD: acá solo interesa que se llame una vez por
        # intento, porque cada intento es un llamado que YA se facturó.
        registrados = []
        monkeypatch.setattr(ap, "_registrar_consumo",
                            lambda resp, uid, ref, intento: registrados.append(intento))
        resultado = ap._generar_json("sos un asistente", "hola", ap._SCHEMA_REVISION,
                                     ref_label="test")
        return resultado, cliente, registrados
    return _correr


def _ok(datos):
    return _Respuesta([_Parte(json.dumps(datos))])


def test_devuelve_el_json_y_loguea_el_tiempo(llamar, caplog):
    """El caso que rompió producción: la respuesta era correcta y el log de "salió bien"
    tiraba NameError, así que se perdía un llamado ya pagado."""
    with caplog.at_level(logging.INFO, logger=ap.__name__):
        resultado, cliente, registrados = llamar([_ok({"diagnostico": "ok", "atributos": []})])

    assert resultado == {"diagnostico": "ok", "atributos": []}
    assert cliente.llamadas == 1
    assert registrados == [0]
    assert any("respuesta OK" in m for m in caplog.messages)


def test_ignora_las_partes_de_pensamiento(llamar):
    """Con thinking activado la salida viene partida y algunas partes son 'thought':
    si se concatenaran, el JSON no parsearía."""
    respuesta = _Respuesta([
        _Parte("pensando en voz alta...", thought=True),
        _Parte('{"diagnostico": "ok", "atributos": []}'),
    ])
    resultado, _, _ = llamar([respuesta])
    assert resultado["diagnostico"] == "ok"


def test_reintenta_cuando_el_json_viene_cortado(llamar, caplog):
    """Es lo que pasa cuando la salida choca contra el tope de tokens."""
    with caplog.at_level(logging.WARNING, logger=ap.__name__):
        resultado, cliente, registrados = llamar([
            _Respuesta([_Parte('{"diagnostico": "se corto')]),   # JSON inválido
            _ok({"diagnostico": "ok", "atributos": []}),
        ])
    assert resultado["diagnostico"] == "ok"
    assert cliente.llamadas == 2
    assert registrados == [0, 1], "Cada intento se factura aparte y se registra aparte."
    assert any("JSON inválido" in m for m in caplog.messages)


def test_una_respuesta_vacia_tambien_reintenta(llamar):
    resultado, cliente, _ = llamar([
        _Respuesta([]),                                  # sin partes
        _ok({"diagnostico": "ok", "atributos": []}),
    ])
    assert resultado["diagnostico"] == "ok"
    assert cliente.llamadas == 2


def test_si_fallan_todos_los_intentos_explica_por_que(llamar):
    with pytest.raises(RuntimeError) as error:
        llamar([RuntimeError("503 Service Unavailable")] * ap.MAXIMO_INTENTOS)
    assert "503" in str(error.value)


def test_no_reintenta_si_ya_se_paso_del_presupuesto(llamar, monkeypatch, caplog):
    """Reintentar un llamado que ya tardó un minuto cuesta otro minuto y otra factura.
    Con razonamiento HIGH sobre una plantilla entera, tres intentos seguidos son varios
    minutos de alguien mirando un spinner."""
    monkeypatch.setattr(ap, "PRESUPUESTO_REINTENTOS_SEG", 0)
    with caplog.at_level(logging.WARNING, logger=ap.__name__):
        with pytest.raises(RuntimeError):
            llamar([RuntimeError("timeout")] * ap.MAXIMO_INTENTOS)
    assert any("no se reintenta" in m for m in caplog.messages)


def test_el_presupuesto_no_impide_el_primer_intento(llamar, monkeypatch):
    """El corte es para los REINTENTOS: el intento inicial va siempre."""
    monkeypatch.setattr(ap, "PRESUPUESTO_REINTENTOS_SEG", 0)
    resultado, cliente, _ = llamar([_ok({"diagnostico": "ok", "atributos": []})])
    assert resultado["diagnostico"] == "ok" and cliente.llamadas == 1
