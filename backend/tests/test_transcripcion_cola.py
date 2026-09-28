"""
Cola de transcripciones a demanda (AuditorIA/transcripcion_cola.py).

Lo que se cubre —las tres cosas que, si se rompen, cuestan plata o pierden trabajo:

  1. ENCOLAR: no se encola lo que ya tiene transcripción, lo que ya está en cola ni lo
     que no tiene audio conservado (cada pedido de más es un llamado pago de Gemini).
  2. RUTEO: un pedido chico va por FLEX (sincrónico, minutos) y uno grande por BATCH.
     Es lo que hace que el auditor que está escuchando UN llamado no espere horas.
  3. DESPACHO: el request pide SOLO transcripción (no arrastra el esquema de calidad),
     se corta en lotes por tamaño y cada pedido queda atado a su posición dentro del
     lote — el SegmentID es lo único que permite saber, horas después, de qué llamado
     es cada respuesta.
  4. FLEX: se pide el tier flex de verdad (si no, se paga el doble), y cuando Gemini
     dice "no hay capacidad" (429/503) el pedido se DEGRADA a batch en vez de fallarle
     al auditor.
  5. VUELTA: la respuesta se guarda bajo el IdAplicativo correcto, con los tiempos
     corregidos contra la duración real del audio, y una respuesta fallida cierra SOLO
     su pedido.

Test 100% offline: no toca DB, ni Gemini, ni tokens.
"""
import json
import types as pytypes

import pytest

from AuditorIA import transcripcion_cola as cola


# --- Dobles de prueba -------------------------------------------------------------

class _FakeResult:
    def __init__(self, filas=None):
        self._filas = filas or []
        self.rowcount = len(self._filas)

    def fetchall(self):
        return self._filas

    def first(self):
        return self._filas[0] if self._filas else None


class _FakeConn:
    def __init__(self, registro):
        self.registro = registro

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sentencia, params=None):
        self.registro.append((str(sentencia), params or {}))
        return _FakeResult()


class _FakeEngine:
    """Registra (sql, params) de todo lo que se ejecuta, sin base de datos."""

    def __init__(self):
        self.ejecutado = []

    def begin(self):
        return _FakeConn(self.ejecutado)

    def connect(self):
        return _FakeConn(self.ejecutado)

    def sentencias(self, fragmento):
        return [params for sql, params in self.ejecutado if fragmento in sql]


class _FakeBatches:
    def __init__(self):
        self.creados = []          # [(model, requests)]
        self.resultados = {}       # name -> objeto que devuelve get()

    def create(self, model, src, config):
        nombre = f"batches/fake-{len(self.creados)}"
        self.creados.append((model, src))
        return pytypes.SimpleNamespace(name=nombre)

    def get(self, name):
        return self.resultados[name]


class _FakeModels:
    """El camino sincrónico (flex). `respuestas` es lo que devuelve cada llamada: una
    respuesta ya armada o una excepción para simular 429/503."""

    def __init__(self, respuestas=None):
        self.respuestas = list(respuestas or [])
        self.llamadas = []

    def generate_content(self, model, contents, config):
        self.llamadas.append({"model": model, "config": config, "contents": contents})
        siguiente = self.respuestas.pop(0) if self.respuestas else None
        if isinstance(siguiente, Exception):
            raise siguiente
        return siguiente


class _SinCapacidad(Exception):
    """Lo que tira el SDK cuando el tier flex está lleno (APIError trae .code)."""

    def __init__(self, code=429):
        super().__init__(f"{code} RESOURCE_EXHAUSTED. Flex capacity unavailable.")
        self.code = code


class _FakeGemini:
    def __init__(self, respuestas=None):
        self.batches = _FakeBatches()
        self.models = _FakeModels(respuestas)


def _job(job_id, id_aplicativo, intentos=1):
    return {"job_id": job_id, "id_aplicativo": id_aplicativo,
            "user_id": 42, "intentos": intentos}


# --- 1. Encolar -------------------------------------------------------------------

def test_encolar_solo_lo_que_falta_transcribir(monkeypatch):
    """Cada id pedido cae en su categoría y solo se inserta el que corresponde."""
    engine = _FakeEngine()
    monkeypatch.setattr(cola.audio_store, "ids_con_audio",
                        lambda e, ids: {"nuevo", "ya-transcripta", "en-cola"})
    monkeypatch.setattr(cola, "ids_con_transcripcion", lambda e, ids: {"ya-transcripta"})
    monkeypatch.setattr(cola, "_ids_con_job_abierto", lambda e, ids: {"en-cola"})

    resultado = cola.encolar(
        engine, ["nuevo", "ya-transcripta", "en-cola", "sin-audio", "nuevo"], user_id=7,
    )

    assert resultado["encoladas"] == ["nuevo"]          # el duplicado del pedido no cuenta dos veces
    assert resultado["ya_transcriptas"] == ["ya-transcripta"]
    assert resultado["ya_en_cola"] == ["en-cola"]
    assert resultado["sin_audio"] == ["sin-audio"]

    inserts = engine.sentencias("INSERT INTO calidad.TranscripcionJobs")
    assert len(inserts) == 1
    assert inserts[0]["id"] == "nuevo"
    assert inserts[0]["user_id"] == 7
    assert inserts[0]["estado"] == cola.PENDIENTE


def test_encolar_sin_ids_no_toca_la_base():
    engine = _FakeEngine()
    assert cola.encolar(engine, [])["encoladas"] == []
    assert engine.ejecutado == []


def test_un_fallo_de_base_no_se_disfraza_de_duplicado(monkeypatch):
    """Si el INSERT explota (p. ej. la migración no aplicada) no se puede decir "ya estaba
    en cola": no quedó nada anotado y nadie lo va a transcribir."""
    class _EngineRoto(_FakeEngine):
        def begin(self):
            raise RuntimeError("Invalid object name 'calidad.TranscripcionJobs'.")

    monkeypatch.setattr(cola.audio_store, "ids_con_audio", lambda e, ids: {"nuevo"})
    monkeypatch.setattr(cola, "ids_con_transcripcion", lambda e, ids: set())
    monkeypatch.setattr(cola, "_ids_con_job_abierto", lambda e, ids: set())

    resultado = cola.encolar(_EngineRoto(), ["nuevo"])

    assert resultado["fallidas"] == ["nuevo"]
    assert resultado["encoladas"] == [] and resultado["ya_en_cola"] == []


# --- 2. Ruteo del pedido: flex vs batch --------------------------------------------

def test_un_pedido_chico_va_por_flex_y_uno_grande_por_batch(monkeypatch):
    """El criterio no es la plata (flex y batch cuestan lo mismo): es quién espera.
    Un llamado suelto sale del reproductor, con el auditor mirando la pantalla."""
    monkeypatch.setattr(cola.settings, "TRANSCRIPCION_FLEX_MAX_PEDIDO", 10)

    assert cola.elegir_motor(1) == cola.MOTOR_GEMINI_FLEX
    assert cola.elegir_motor(10) == cola.MOTOR_GEMINI_FLEX
    assert cola.elegir_motor(11) == cola.MOTOR_GEMINI_BATCH
    assert cola.elegir_motor(0) == cola.MOTOR_GEMINI_BATCH   # nada que apurar


def test_el_motor_configurado_manda_sobre_el_ruteo(monkeypatch):
    """Con fastwhisper (o flex forzado) por config, el tamaño del pedido no decide nada."""
    monkeypatch.setattr(cola.settings, "TRANSCRIPCION_MOTOR", cola.MOTOR_FASTWHISPER)
    assert cola.elegir_motor(1) == cola.MOTOR_FASTWHISPER
    assert cola.elegir_motor(500) == cola.MOTOR_FASTWHISPER


def test_encolar_un_solo_llamado_lo_marca_como_flex(monkeypatch):
    """El motor se decide sobre lo que REALMENTE se encola, y viaja en la fila: es lo que
    después mira el despacho (y la pantalla, para decir "minutos" en vez de "horas")."""
    engine = _FakeEngine()
    monkeypatch.setattr(cola.audio_store, "ids_con_audio", lambda e, ids: {"abc"})
    monkeypatch.setattr(cola, "ids_con_transcripcion", lambda e, ids: set())
    monkeypatch.setattr(cola, "_ids_con_job_abierto", lambda e, ids: set())

    cola.encolar(engine, ["abc"], user_id=7)

    inserts = engine.sentencias("INSERT INTO calidad.TranscripcionJobs")
    assert inserts[0]["motor"] == cola.MOTOR_GEMINI_FLEX


# --- 3. Despacho por lote (batch) --------------------------------------------------

@pytest.fixture
def audio_falso(monkeypatch):
    monkeypatch.setattr(cola, "_leer_audio", lambda engine, idap: (b"x" * 10, "audio/ogg"))


def test_el_request_pide_solo_transcripcion(audio_falso):
    """Si se colara el esquema de calidad, el modelo devolvería atributos de una
    plantilla que acá no existe (y se pagaría el razonamiento de una auditoría)."""
    engine, gemini = _FakeEngine(), _FakeGemini()

    cola._despachar_gemini_batch(engine, gemini, [_job(1, "abc")])

    _modelo, requests = gemini.batches.creados[0]
    propiedades = requests[0]["config"].response_schema.properties
    assert set(propiedades) == {"metadata", "segments", "analytics"}
    # El audio viaja inline (los modelos 3.x rechazan los file_uri con 403).
    assert requests[0]["contents"][0].parts[0].inline_data is not None


def test_cada_pedido_queda_atado_a_su_posicion_en_el_lote(audio_falso):
    engine, gemini = _FakeEngine(), _FakeGemini()

    enviados = cola._despachar_gemini_batch(engine, gemini, [_job(1, "abc"), _job(2, "xyz")])

    assert enviados == 2
    marcados = engine.sentencias("SET Estado = :estado, BatchID = :batch")
    assert [(m["jid"], m["segmento"]) for m in marcados] == [(1, 0), (2, 1)]
    assert {m["batch"] for m in marcados} == {"batches/fake-0"}
    assert all(m["estado"] == cola.ENVIADO for m in marcados)


def test_el_lote_se_corta_por_tamanio(monkeypatch, audio_falso):
    """Un request de batch no puede pasar de 20 MB: los pedidos se reparten en varios
    jobs, cada uno con su propia numeración de segmentos."""
    monkeypatch.setattr(cola, "MAX_BYTES_POR_LOTE", 15)  # dos audios de 10 bytes no entran
    engine, gemini = _FakeEngine(), _FakeGemini()

    cola._despachar_gemini_batch(engine, gemini, [_job(1, "abc"), _job(2, "xyz")])

    assert len(gemini.batches.creados) == 2
    marcados = engine.sentencias("SET Estado = :estado, BatchID = :batch")
    assert [(m["batch"], m["segmento"]) for m in marcados] == [
        ("batches/fake-0", 0), ("batches/fake-1", 0),
    ]


def test_sin_audio_conservado_el_pedido_se_cierra_en_error(monkeypatch):
    """El tope FIFO del store pudo haberse llevado el audio: no hay nada que transcribir
    y el pedido no puede quedar dando vueltas en la cola."""
    monkeypatch.setattr(cola, "_leer_audio", lambda engine, idap: None)
    engine, gemini = _FakeEngine(), _FakeGemini()

    assert cola._despachar_gemini_batch(engine, gemini, [_job(1, "abc")]) == 0
    assert gemini.batches.creados == []
    cierres = engine.sentencias("SET Estado = :estado, Error = :error, FechaFin")
    assert cierres and cierres[0]["estado"] == cola.ERROR


def test_los_intentos_agotados_se_abandonan(monkeypatch, audio_falso):
    """Un pedido que no se puede enviar no se reintenta para siempre."""
    engine, gemini = _FakeEngine(), _FakeGemini()
    monkeypatch.setattr(cola, "_reclamar_pendientes",
                        lambda e, limite, motor: ([_job(1, "abc", intentos=cola.MAX_INTENTOS + 1)]
                                                  if motor == cola.MOTOR_GEMINI_BATCH else []))

    assert cola.despachar_pendientes(engine, gemini) == 0
    assert gemini.batches.creados == []
    cierres = engine.sentencias("SET Estado = :estado, Error = :error, FechaFin")
    assert cierres and cierres[0]["estado"] == cola.ERROR


# --- 4. Vuelta del lote -----------------------------------------------------------

TRANSCRIPCION = {
    "metadata": {"overallConfidence": 0.9, "languageCode": "es-AR", "audioDurationSeconds": 20.0},
    "segments": [{"speakerLabel": "Agente", "startTime": 0.0, "endTime": 20.0,
                  "confidence": 0.9, "text": ["hola", "qué", "tal"]}],
    "analytics": {"keywords": ["saludo"], "entities": []},
}


def _respuesta_ok(payload):
    parte = pytypes.SimpleNamespace(text=json.dumps(payload), thought=False)
    contenido = pytypes.SimpleNamespace(parts=[parte])
    return pytypes.SimpleNamespace(
        candidates=[pytypes.SimpleNamespace(content=contenido, finish_reason="STOP")],
        usage_metadata=pytypes.SimpleNamespace(
            prompt_token_count=8000, candidates_token_count=900, thoughts_token_count=0,
        ),
        text=json.dumps(payload),
    )


def _lote_terminado(gemini, nombre, respuestas, estado="JOB_STATE_SUCCEEDED"):
    gemini.batches.resultados[nombre] = pytypes.SimpleNamespace(
        state=pytypes.SimpleNamespace(name=estado),
        dest=pytypes.SimpleNamespace(inlined_responses=respuestas),
    )


def test_la_respuesta_se_guarda_bajo_su_idaplicativo(monkeypatch):
    engine, gemini = _FakeEngine(), _FakeGemini()
    monkeypatch.setattr(cola, "_jobs_en_vuelo", lambda e: {"batches/l1": [
        {"job_id": 1, "segment_id": 0, "id_aplicativo": "abc", "user_id": 42, "modelo": "gemini-fake"},
        {"job_id": 2, "segment_id": 1, "id_aplicativo": "xyz", "user_id": 42, "modelo": "gemini-fake"},
    ]})
    _lote_terminado(gemini, "batches/l1", [
        pytypes.SimpleNamespace(response=_respuesta_ok(TRANSCRIPCION), error=None),
        pytypes.SimpleNamespace(response=_respuesta_ok(TRANSCRIPCION), error=None),
    ])

    guardadas = []
    monkeypatch.setattr(cola, "guardar_transcripcion",
                        lambda e, idap, datos, tokens, uid, modelo, **kw:
                        guardadas.append((idap, datos["metadata"]["languageCode"], tokens, modelo)) or True)
    cerrados = []
    monkeypatch.setattr(cola, "_cerrar",
                        lambda e, ids, estado, error=None, modelo=None: cerrados.append((list(ids), estado)))

    assert cola.procesar_terminados(engine, gemini) == 2
    assert [g[0] for g in guardadas] == ["abc", "xyz"]
    assert guardadas[0][2]["input_tokens"] == 8000      # el costo del lote se registra
    assert cerrados == [([1], cola.LISTO), ([2], cola.LISTO)]


def test_una_respuesta_fallida_no_arrastra_a_las_demas(monkeypatch):
    engine, gemini = _FakeEngine(), _FakeGemini()
    monkeypatch.setattr(cola, "_jobs_en_vuelo", lambda e: {"batches/l1": [
        {"job_id": 1, "segment_id": 0, "id_aplicativo": "roto", "user_id": 1, "modelo": "gemini-fake"},
        {"job_id": 2, "segment_id": 1, "id_aplicativo": "ok", "user_id": 1, "modelo": "gemini-fake"},
    ]})
    _lote_terminado(gemini, "batches/l1", [
        pytypes.SimpleNamespace(response=None, error="RESOURCE_EXHAUSTED"),
        pytypes.SimpleNamespace(response=_respuesta_ok(TRANSCRIPCION), error=None),
    ])
    monkeypatch.setattr(cola, "guardar_transcripcion",
                        lambda e, idap, datos, tokens, uid, modelo, **kw: True)
    cerrados = []
    monkeypatch.setattr(cola, "_cerrar",
                        lambda e, ids, estado, error=None, modelo=None: cerrados.append((list(ids), estado)))

    assert cola.procesar_terminados(engine, gemini) == 1
    assert cerrados == [([1], cola.ERROR), ([2], cola.LISTO)]


def test_un_lote_que_no_termino_se_deja_para_la_proxima(monkeypatch):
    engine, gemini = _FakeEngine(), _FakeGemini()
    monkeypatch.setattr(cola, "_jobs_en_vuelo", lambda e: {"batches/l1": [
        {"job_id": 1, "segment_id": 0, "id_aplicativo": "abc", "user_id": 1, "modelo": None},
    ]})
    _lote_terminado(gemini, "batches/l1", [], estado="JOB_STATE_RUNNING")
    monkeypatch.setattr(cola, "_cerrar",
                        lambda *a, **k: pytest.fail("un lote en curso no se puede cerrar"))

    assert cola.procesar_terminados(engine, gemini) == 0


# --- 5. Flex: transcribir en el momento -------------------------------------------

@pytest.fixture(autouse=True)
def sin_esperas(monkeypatch):
    """El backoff del flex es real (15s y subiendo): en los tests no se espera."""
    monkeypatch.setattr(cola.time, "sleep", lambda _s: None)


def test_flex_pide_el_tier_flex_y_guarda_la_transcripcion(monkeypatch, audio_falso):
    """Sin `service_tier=flex` el llamado sale al precio ESTÁNDAR: el doble, en silencio."""
    engine = _FakeEngine()
    gemini = _FakeGemini([_respuesta_ok(TRANSCRIPCION)])

    guardadas = []
    monkeypatch.setattr(cola, "guardar_transcripcion",
                        lambda e, idap, datos, tokens, uid, modelo, **kw:
                        guardadas.append((idap, kw.get("modo"))) or True)
    cerrados = []
    monkeypatch.setattr(cola, "_cerrar",
                        lambda e, ids, estado, error=None, modelo=None: cerrados.append((list(ids), estado)))

    assert cola._despachar_gemini_flex(engine, gemini, [_job(1, "abc")]) == 1

    config = gemini.models.llamadas[0]["config"]
    assert config.service_tier == "flex"
    # Timeout holgado: la doc de Flex avisa que el pedido puede quedar encolado del lado
    # de Google y recomienda 10 minutos o más.
    assert config.http_options.timeout >= 600_000
    # El consumo se registra como 'flex' (es lo que la vista de costos cobra al 50%).
    assert guardadas == [("abc", "flex")]
    assert cerrados == [([1], cola.LISTO)]


def test_flex_sin_capacidad_reintenta_y_degrada_a_batch(monkeypatch, audio_falso):
    """429/503 no es un error del pedido: es que flex está lleno. En vez de fallarle al
    auditor, el llamado se resuelve por lote (mismo precio, más tarde)."""
    engine = _FakeEngine()
    gemini = _FakeGemini([_SinCapacidad(429), _SinCapacidad(503), _SinCapacidad(429)])
    monkeypatch.setattr(cola.settings, "TRANSCRIPCION_FLEX_REINTENTOS", 3)
    monkeypatch.setattr(cola, "_cerrar",
                        lambda *a, **k: pytest.fail("un pedido sin capacidad no se cierra en ERROR"))

    assert cola._despachar_gemini_flex(engine, gemini, [_job(1, "abc")]) == 0

    assert len(gemini.models.llamadas) == 3          # reintentó antes de rendirse
    degradados = engine.sentencias("SET Motor = :batch, Estado = :pendiente")
    assert degradados and degradados[0]["batch"] == cola.MOTOR_GEMINI_BATCH
    assert degradados[0]["pendiente"] == cola.PENDIENTE


def test_flex_con_un_error_real_cierra_el_pedido(monkeypatch, audio_falso):
    """Un audio que el modelo no puede procesar no mejora por irse a batch: se cierra en
    ERROR y el auditor lo ve, con el motivo, para poder volver a pedirlo."""
    engine = _FakeEngine()
    gemini = _FakeGemini([ValueError("JSON truncado")] * 3)
    monkeypatch.setattr(cola.settings, "TRANSCRIPCION_FLEX_REINTENTOS", 3)
    cerrados = []
    monkeypatch.setattr(cola, "_cerrar",
                        lambda e, ids, estado, error=None, modelo=None: cerrados.append((list(ids), estado, error)))

    assert cola._despachar_gemini_flex(engine, gemini, [_job(1, "abc")]) == 0

    assert engine.sentencias("SET Motor = :batch, Estado = :pendiente") == []
    assert cerrados[0][1] == cola.ERROR
    assert "JSON truncado" in cerrados[0][2]


def test_si_falla_el_guardado_no_se_le_vuelve_a_pedir_a_gemini(monkeypatch, audio_falso):
    """La transcripción ya está paga: un error de la BASE no puede disparar un segundo
    llamado al modelo. El pedido vuelve a la cola para reintentar más tarde."""
    engine = _FakeEngine()
    gemini = _FakeGemini([_respuesta_ok(TRANSCRIPCION)])
    monkeypatch.setattr(cola.settings, "TRANSCRIPCION_FLEX_REINTENTOS", 3)

    def _guardado_roto(*a, **k):
        raise RuntimeError("timeout de la base")
    monkeypatch.setattr(cola, "guardar_transcripcion", _guardado_roto)
    monkeypatch.setattr(cola, "_cerrar",
                        lambda *a, **k: pytest.fail("no se cierra: la transcripción se puede recuperar"))

    assert cola._despachar_gemini_flex(engine, gemini, [_job(1, "abc")]) == 0

    assert len(gemini.models.llamadas) == 1          # UN solo llamado pago
    # _devolver_a_pendiente (no _cerrar: ese lleva FechaFin)
    devueltos = [p for sql, p in engine.ejecutado
                 if "SET Estado = :estado, Error = :error" in sql and "FechaFin" not in sql]
    assert devueltos and devueltos[0]["estado"] == cola.PENDIENTE


def test_el_despacho_manda_cada_pedido_al_motor_de_su_fila(monkeypatch, audio_falso):
    """Los dos motores conviven en la cola (un flex degradado queda como batch): el tick
    tiene que barrer los dos, no solo el configurado."""
    engine = _FakeEngine()
    gemini = _FakeGemini([_respuesta_ok(TRANSCRIPCION)])
    monkeypatch.setattr(cola, "guardar_transcripcion", lambda *a, **k: True)
    monkeypatch.setattr(cola, "_cerrar", lambda *a, **k: None)
    monkeypatch.setattr(cola, "_reclamar_pendientes", lambda e, limite, motor: {
        cola.MOTOR_GEMINI_FLEX: [_job(1, "flexi")],
        cola.MOTOR_GEMINI_BATCH: [_job(2, "loteado")],
    }.get(motor, []))

    assert cola.despachar_pendientes(engine, gemini) == 2

    assert len(gemini.models.llamadas) == 1          # el flex, sincrónico
    assert len(gemini.batches.creados) == 1          # el otro, por lote


# --- 6. Guardado ------------------------------------------------------------------

def test_guardar_corrige_los_tiempos_contra_la_duracion_real(monkeypatch):
    """Gemini ESTIMA los timestamps; el store midió el audio con ffprobe al conservarlo."""
    engine = _FakeEngine()
    monkeypatch.setattr(cola.audio_store, "duracion_de", lambda e, idap: 10.0)
    monkeypatch.setattr(cola, "ids_con_transcripcion", lambda e, ids: set())
    import app.uso_ia
    monkeypatch.setattr(app.uso_ia, "registrar_uso_ia_bulk", lambda filas: None)

    guardado = cola.guardar_transcripcion(
        engine, "abc", json.loads(json.dumps(TRANSCRIPCION)),
        {"input_tokens": 100, "output_tokens": 200, "thoughts_tokens": 0},
        user_id=5, modelo="gemini-fake",
    )

    assert guardado is True
    insert = engine.sentencias("INSERT INTO calidad.transcripciones")[0]
    assert insert["id"] == "abc"
    segmentos = json.loads(insert["segments"])
    assert segmentos[0]["endTime"] == 10.0      # 20s estimados -> 10s reales
    assert json.loads(insert["metadata"])["audioDurationSeconds"] == 10.0
    # El texto va en NVARCHAR y sin escapar: los acentos tienen que sobrevivir.
    assert "qué" in insert["segments"]


def test_no_se_duplica_una_transcripcion_ya_guardada(monkeypatch):
    """Entre el envío y la vuelta del lote pasan horas: alguien pudo re-auditar el mismo
    llamado y transcribirlo en el medio."""
    engine = _FakeEngine()
    monkeypatch.setattr(cola.audio_store, "duracion_de", lambda e, idap: None)
    monkeypatch.setattr(cola, "ids_con_transcripcion", lambda e, ids: {"abc"})

    assert cola.guardar_transcripcion(
        engine, "abc", TRANSCRIPCION, {}, user_id=1, modelo="gemini-fake") is False
    assert engine.sentencias("INSERT INTO calidad.transcripciones") == []
