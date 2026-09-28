"""
Cola de lotes de auditoría (AuditorIA/batch_cola.py).

La Batch API admite 100 jobs en estado no terminal en TODA la cuenta. Al tocar ese
techo, `batches.create` rebota; antes el lote se descartaba y sus audios no se
auditaban nunca (y en el camino CSV la carpeta se borraba igual). Lo que se cubre es
justamente eso: que un lote sin cupo NO se pierda, y que salga después sin duplicarse
ni quedar sin metadata.

  1. CUPO: se encola a partir del umbral, no del 100 pelado, y un fallo al consultar
     el cupo no frena la corrida.
  2. SERIALIZACIÓN: el JSONL lleva el audio y la config REAL del request (el mismo
     que manda el camino inline), y cada línea sabe a qué posición del lote pertenece.
  3. DESPACHO: se reclama como mucho lo que entra en el cupo, un lote sin archivo se
     cierra en ERROR (no se reintenta para siempre), un fallo de envío vuelve a la
     cola y un lote enviado que no se puede registrar CANCELA el job (un lote pago sin
     Batch_data no sirve para nada).
  4. RESULTADO: las respuestas por archivo se atan a su segment_id por la `key`, no
     por el orden de las líneas (que la API no garantiza).

Test 100% offline: no toca DB, ni Gemini, ni tokens.
"""
import json
import os
import types as pytypes

import pytest
from google import genai
from google.genai import types

from AuditorIA import batch_cola


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
    def __init__(self, engine):
        self.engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def begin(self):
        return pytypes.SimpleNamespace(commit=lambda: None, rollback=lambda: None)

    def execute(self, sentencia, params=None):
        sql = str(sentencia)
        self.engine.ejecutado.append((sql, params or {}))
        if self.engine.falla_en and self.engine.falla_en in sql:
            raise RuntimeError("falla simulada de la base")
        for fragmento, filas in self.engine.respuestas.items():
            if fragmento in sql:
                return _FakeResult(filas)
        return _FakeResult()


class _FakeEngine:
    def __init__(self, respuestas=None, falla_en=None):
        self.ejecutado = []
        self.respuestas = respuestas or {}
        self.falla_en = falla_en

    def begin(self):
        return _FakeConn(self)

    def connect(self):
        return _FakeConn(self)

    def sentencias(self, fragmento):
        return [params for sql, params in self.ejecutado if fragmento in sql]


class _FakeBatches:
    def __init__(self, en_vuelo=0, error_al_crear=None):
        self.jobs = [
            pytypes.SimpleNamespace(state=pytypes.SimpleNamespace(name="JOB_STATE_RUNNING"))
            for _ in range(en_vuelo)
        ]
        self.creados = []
        self.cancelados = []
        self.error_al_crear = error_al_crear

    def list(self, config=None):
        return iter(self.jobs)

    def create(self, model, src, config):
        if self.error_al_crear:
            raise self.error_al_crear
        self.creados.append((model, src))
        return pytypes.SimpleNamespace(name=f"batches/fake-{len(self.creados)}")

    def cancel(self, name):
        self.cancelados.append(name)


class _FakeFiles:
    def __init__(self):
        self.subidos = []

    def upload(self, file, config=None):
        self.subidos.append(file)
        return pytypes.SimpleNamespace(name=f"files/fake-{len(self.subidos)}")


class _FakeGemini:
    def __init__(self, en_vuelo=0, error_al_crear=None):
        self.batches = _FakeBatches(en_vuelo, error_al_crear)
        self.files = _FakeFiles()


@pytest.fixture(autouse=True)
def _cupo_limpio():
    """El conteo de cupo se cachea a nivel módulo: sin esto, un test se lleva el
    número del anterior."""
    batch_cola._cupo_cache.update({"ts": 0.0, "en_vuelo": None})
    yield
    batch_cola._cupo_cache.update({"ts": 0.0, "en_vuelo": None})


# --- 1. Cupo ----------------------------------------------------------------------

def test_hay_cupo_cuando_estamos_por_debajo_del_umbral(monkeypatch):
    monkeypatch.setattr(batch_cola.settings, "BATCH_CUPO_MAX", 85, raising=False)
    assert batch_cola.hay_cupo(_FakeGemini(en_vuelo=10)) is True


def test_sin_cupo_al_llegar_al_umbral(monkeypatch):
    """85 y no 100: el conteo se cachea un minuto y la cola de transcripciones crea
    jobs por su cuenta contra el mismo techo."""
    monkeypatch.setattr(batch_cola.settings, "BATCH_CUPO_MAX", 85, raising=False)
    assert batch_cola.hay_cupo(_FakeGemini(en_vuelo=85)) is False


def test_si_no_se_puede_consultar_el_cupo_se_manda_igual(monkeypatch):
    """Un problema para LISTAR no puede frenar una corrida: si el cupo estuviera
    lleno de verdad, el create rebota y el lote cae en la cola igual."""
    gemini = _FakeGemini()

    def _explota(config=None):
        raise RuntimeError("API caída")

    monkeypatch.setattr(gemini.batches, "list", _explota)
    assert batch_cola.jobs_en_vuelo(gemini) is None
    assert batch_cola.hay_cupo(gemini) is True


def test_los_jobs_terminados_no_ocupan_cupo(monkeypatch):
    monkeypatch.setattr(batch_cola.settings, "BATCH_CUPO_MAX", 5, raising=False)
    gemini = _FakeGemini()
    gemini.batches.jobs = [
        pytypes.SimpleNamespace(state=pytypes.SimpleNamespace(name=estado))
        for estado in ("JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED", "JOB_STATE_RUNNING",
                       "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED")
    ]
    assert batch_cola.jobs_en_vuelo(gemini) == 1


def test_crear_un_job_descuenta_del_cupo_sin_volver_a_preguntar(monkeypatch):
    """Dos lotes seguidos no pueden salir los dos con el mismo conteo viejo."""
    monkeypatch.setattr(batch_cola.settings, "BATCH_CUPO_MAX", 2, raising=False)
    gemini = _FakeGemini(en_vuelo=1)
    assert batch_cola.hay_cupo(gemini) is True
    batch_cola.crear_job(gemini, __file__, "gemini-3.8-flash")
    assert batch_cola.hay_cupo(gemini) is False


# --- 2. Serialización del lote ----------------------------------------------------

def _request_de_prueba(texto="auditá esto", audio=b"OGGDATA"):
    return {
        "contents": [types.Content(role="user", parts=[
            types.Part.from_bytes(data=audio, mime_type="audio/ogg"),
            types.Part.from_text(text=texto),
        ])],
        "config": types.GenerateContentConfig(
            temperature=0.8,
            response_mime_type="application/json",
            response_schema=types.Schema(
                type=types.Type.OBJECT, required=["Nota"],
                properties={"Nota": types.Schema(type=types.Type.STRING)},
            ),
            system_instruction=[types.Part.from_text(text="sos un auditor")],
            stop_sequences=["dice dice"],
        ),
    }


def test_el_jsonl_lleva_el_request_completo(tmp_path):
    """El audio va inline en base64 y la config viaja entera: si algo de esto se
    pierde, el lote se audita con otras reglas que el camino sincrónico."""
    cliente = genai.Client(api_key="fake")
    ruta = batch_cola.escribir_jsonl(
        cliente, [_request_de_prueba()], "gemini-3.8-flash",
        ruta=str(tmp_path / "lote.jsonl"),
    )
    linea = json.loads(open(ruta, encoding="utf-8").read().strip())

    assert linea["key"] == "req-0"
    pedido = linea["request"]
    parte_audio = pedido["contents"][0]["parts"][0]["inlineData"]
    assert parte_audio["mimeType"] == "audio/ogg"
    import base64
    assert base64.b64decode(parte_audio["data"]) == b"OGGDATA"

    # El system prompt va a nivel request (no adentro de generationConfig).
    assert pedido["systemInstruction"]["parts"][0]["text"] == "sos un auditor"
    config = pedido["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"]["required"] == ["Nota"]
    assert config["stopSequences"] == ["dice dice"]


def test_cada_linea_declara_su_posicion_en_el_lote(tmp_path):
    """El orden de las respuestas NO está garantizado: la posición viaja en la key."""
    cliente = genai.Client(api_key="fake")
    ruta = batch_cola.escribir_jsonl(
        cliente, [_request_de_prueba(f"audio {i}") for i in range(3)],
        "gemini-3.8-flash", ruta=str(tmp_path / "lote.jsonl"),
    )
    claves = [json.loads(l)["key"] for l in open(ruta, encoding="utf-8")]
    assert claves == ["req-0", "req-1", "req-2"]


def test_un_jsonl_a_medio_escribir_no_queda_como_bueno(tmp_path, monkeypatch):
    """Si el proceso muere a mitad, no puede quedar un archivo truncado que el tick
    dé por válido y mande a Gemini."""
    cliente = genai.Client(api_key="fake")
    destino = str(tmp_path / "lote.jsonl")

    def _explota(*args, **kwargs):
        raise RuntimeError("se cortó")

    monkeypatch.setattr(batch_cola, "_linea_jsonl", _explota)
    assert batch_cola.escribir_jsonl(cliente, [_request_de_prueba()], "m", ruta=destino) is None
    assert not os.path.exists(destino)
    assert not list(tmp_path.glob("*.parcial"))


# --- 3. Despacho ------------------------------------------------------------------

def _lote(lote_id=1, archivo=None, intentos=1, metadata=None):
    meta = json.dumps(metadata if metadata is not None else {
        "batch_data": [{"segment_id": 0, "columna": "id_aplicativo", "valor": "abc"}],
        "log": {"trigger_source": "manual", "modo": "batch"},
    })
    return (lote_id, intentos, archivo, meta, "gemini-3.8-flash", 3, "run-1")


def test_no_se_reclama_nada_sin_cupo(monkeypatch):
    monkeypatch.setattr(batch_cola.settings, "BATCH_CUPO_MAX", 5, raising=False)
    engine = _FakeEngine()
    assert batch_cola.despachar_pendientes(engine, _FakeGemini(en_vuelo=5)) == 0
    assert engine.sentencias("UPDATE siguientes") == []


def test_se_reclama_como_mucho_lo_que_entra_en_el_cupo(monkeypatch):
    """Reclamar de más dejaría lotes en ENVIANDO que este tick no puede mandar."""
    monkeypatch.setattr(batch_cola.settings, "BATCH_CUPO_MAX", 85, raising=False)
    monkeypatch.setattr(batch_cola.settings, "BATCH_COLA_MAX_POR_TICK", 20, raising=False)
    engine = _FakeEngine()
    batch_cola.despachar_pendientes(engine, _FakeGemini(en_vuelo=82))
    assert engine.sentencias("UPDATE siguientes")[0]["limite"] == 3


def test_un_lote_sin_archivo_se_cierra_en_error(monkeypatch, tmp_path):
    """Sin el JSONL no hay nada que mandar: reintentarlo para siempre solo esconde
    que esos audios no se auditaron."""
    monkeypatch.setattr(batch_cola.settings, "BATCH_CUPO_MAX", 85, raising=False)
    engine = _FakeEngine()
    monkeypatch.setattr(batch_cola, "_reclamar_pendientes",
                        lambda *a, **k: [dict(zip(
                            ("lote_id", "intentos", "archivo", "metadata", "modelo", "llamados", "run_id"),
                            _lote(archivo=str(tmp_path / "no-existe.jsonl"))))])
    gemini = _FakeGemini()
    assert batch_cola.despachar_pendientes(engine, gemini) == 0
    assert gemini.batches.creados == []
    cerrados = engine.sentencias("SET Estado = :estado, Error = :motivo, FechaFin")
    assert cerrados and cerrados[0]["estado"] == batch_cola.ERROR


def test_se_abandona_despues_de_agotar_los_intentos(monkeypatch, tmp_path):
    monkeypatch.setattr(batch_cola.settings, "BATCH_CUPO_MAX", 85, raising=False)
    archivo = tmp_path / "lote.jsonl"
    archivo.write_text("{}\n")
    engine = _FakeEngine()
    monkeypatch.setattr(batch_cola, "_reclamar_pendientes",
                        lambda *a, **k: [dict(zip(
                            ("lote_id", "intentos", "archivo", "metadata", "modelo", "llamados", "run_id"),
                            _lote(archivo=str(archivo), intentos=batch_cola.MAX_INTENTOS + 1)))])
    gemini = _FakeGemini()
    assert batch_cola.despachar_pendientes(engine, gemini) == 0
    assert gemini.batches.creados == []
    assert engine.sentencias("SET Estado = :estado, Error = :motivo, FechaFin")[0]["estado"] == batch_cola.ERROR


def test_un_fallo_de_envio_devuelve_el_lote_a_la_cola(monkeypatch, tmp_path):
    monkeypatch.setattr(batch_cola.settings, "BATCH_CUPO_MAX", 85, raising=False)
    archivo = tmp_path / "lote.jsonl"
    archivo.write_text("{}\n")
    engine = _FakeEngine()
    monkeypatch.setattr(batch_cola, "_reclamar_pendientes",
                        lambda *a, **k: [dict(zip(
                            ("lote_id", "intentos", "archivo", "metadata", "modelo", "llamados", "run_id"),
                            _lote(archivo=str(archivo))))])
    gemini = _FakeGemini(error_al_crear=RuntimeError("429 RESOURCE_EXHAUSTED"))
    assert batch_cola.despachar_pendientes(engine, gemini) == 0

    vueltos = engine.sentencias("SET Estado = :estado, Error = :motivo\n")
    assert vueltos and vueltos[0]["estado"] == batch_cola.PENDIENTE
    # El payload sigue en disco: es lo único que tiene los audios.
    assert archivo.exists()


def test_un_lote_enviado_queda_registrado_y_sale_de_la_cola(monkeypatch, tmp_path):
    monkeypatch.setattr(batch_cola.settings, "BATCH_CUPO_MAX", 85, raising=False)
    archivo = tmp_path / "lote.jsonl"
    archivo.write_text("{}\n")
    engine = _FakeEngine()
    monkeypatch.setattr(batch_cola, "_reclamar_pendientes",
                        lambda *a, **k: [dict(zip(
                            ("lote_id", "intentos", "archivo", "metadata", "modelo", "llamados", "run_id"),
                            _lote(archivo=str(archivo))))])
    gemini = _FakeGemini()

    assert batch_cola.despachar_pendientes(engine, gemini) == 1

    # El job se creó a partir del ARCHIVO subido, no de requests inline.
    assert gemini.files.subidos == [str(archivo)]
    assert gemini.batches.creados[0][1] == "files/fake-1"
    # Y quedó su metadata: sin Batch_data las respuestas no se pueden atribuir.
    assert engine.sentencias("INSERT INTO calidad.BatchJobs")
    assert engine.sentencias("INSERT INTO calidad.Batch_data")
    enviados = engine.sentencias("SET Estado = :estado, BatchID = :batch")
    assert enviados and enviados[0]["estado"] == batch_cola.ENVIADO
    # El JSONL ya no hace falta.
    assert not archivo.exists()


def test_si_no_se_puede_registrar_el_lote_se_cancela_el_job(monkeypatch, tmp_path):
    """Un job pago cuyas respuestas no se pueden atar a ninguna interacción no sirve
    para nada: se cancela y el lote vuelve a la cola con su JSONL intacto."""
    monkeypatch.setattr(batch_cola.settings, "BATCH_CUPO_MAX", 85, raising=False)
    archivo = tmp_path / "lote.jsonl"
    archivo.write_text("{}\n")
    engine = _FakeEngine(falla_en="INSERT INTO calidad.Batch_data")
    monkeypatch.setattr(batch_cola, "_reclamar_pendientes",
                        lambda *a, **k: [dict(zip(
                            ("lote_id", "intentos", "archivo", "metadata", "modelo", "llamados", "run_id"),
                            _lote(archivo=str(archivo))))])
    gemini = _FakeGemini()

    assert batch_cola.despachar_pendientes(engine, gemini) == 0
    assert gemini.batches.cancelados == ["batches/fake-1"]
    assert archivo.exists()
    vueltos = engine.sentencias("SET Estado = :estado, Error = :motivo\n")
    assert vueltos and vueltos[0]["estado"] == batch_cola.PENDIENTE


# --- 4. Lectura del resultado -----------------------------------------------------

def _linea_resultado(clave, texto, tokens=(100, 20, 5)):
    entrada, salida, pensamiento = tokens
    return json.dumps({
        "key": clave,
        "response": {
            "candidates": [{"content": {"parts": [{"text": texto}]}, "finishReason": "STOP"}],
            "usageMetadata": {
                "promptTokenCount": entrada, "candidatesTokenCount": salida,
                "thoughtsTokenCount": pensamiento,
                # Campo que el modelo del SDK todavía no conoce: no puede romper la
                # lectura del lote entero.
                "serviceTier": "SERVICE_TIER_STANDARD",
            },
        },
    })


class _GeminiConArchivo:
    def __init__(self, contenido):
        self.files = pytypes.SimpleNamespace(download=lambda file: contenido.encode())


def test_las_respuestas_por_archivo_se_atan_por_la_key_no_por_el_orden():
    """La API no garantiza el orden de las líneas; el segment_id sale de la key."""
    contenido = "\n".join([
        _linea_resultado("req-2", '{"Nota": "c"}'),
        _linea_resultado("req-0", '{"Nota": "a"}'),
        _linea_resultado("req-1", '{"Nota": "b"}'),
    ])
    job = pytypes.SimpleNamespace(
        name="batches/x",
        dest=pytypes.SimpleNamespace(inlined_responses=None, file_name="files/salida"),
    )
    respuestas = batch_cola.leer_respuestas(_GeminiConArchivo(contenido), job)

    assert [segment_id for segment_id, _ in respuestas] == [2, 0, 1]
    por_segmento = dict(respuestas)
    assert por_segmento[0].response.text == '{"Nota": "a"}'
    assert por_segmento[2].response.text == '{"Nota": "c"}'
    assert por_segmento[1].response.usage_metadata.prompt_token_count == 100
    assert por_segmento[1].response.usage_metadata.thoughts_token_count == 5


def test_las_partes_de_pensamiento_no_entran_en_el_texto():
    linea = json.dumps({
        "key": "req-0",
        "response": {"candidates": [{"content": {"parts": [
            {"text": "pensando...", "thought": True},
            {"text": '{"Nota":'}, {"text": ' "ok"}'},
        ]}}], "usageMetadata": {}},
    })
    job = pytypes.SimpleNamespace(
        name="batches/x",
        dest=pytypes.SimpleNamespace(inlined_responses=None, file_name="files/salida"),
    )
    (_, respuesta), = batch_cola.leer_respuestas(_GeminiConArchivo(linea), job)
    assert respuesta.response.text == '{"Nota": "ok"}'


def test_una_respuesta_fallida_del_archivo_llega_como_error_y_no_rompe_el_lote():
    contenido = "\n".join([
        json.dumps({"key": "req-0", "error": {"code": 3, "message": "audio inválido"}}),
        _linea_resultado("req-1", '{"Nota": "b"}'),
    ])
    job = pytypes.SimpleNamespace(
        name="batches/x",
        dest=pytypes.SimpleNamespace(inlined_responses=None, file_name="files/salida"),
    )
    respuestas = dict(batch_cola.leer_respuestas(_GeminiConArchivo(contenido), job))
    assert respuestas[0].response is None
    assert respuestas[0].error["message"] == "audio inválido"
    assert respuestas[1].response is not None


def test_los_lotes_viejos_inline_se_siguen_leyendo_igual():
    """Los batches en vuelo al momento del deploy devuelven inlined_responses: la
    posición en la lista ES el segment_id."""
    inline = [pytypes.SimpleNamespace(response="r0"), pytypes.SimpleNamespace(response="r1")]
    job = pytypes.SimpleNamespace(
        name="batches/x",
        dest=pytypes.SimpleNamespace(inlined_responses=inline, file_name=None),
    )
    assert batch_cola.leer_respuestas(None, job) == [(0, inline[0]), (1, inline[1])]


# --- 5. Contrato con la corrida ---------------------------------------------------

def test_un_lote_encolado_no_se_confunde_con_uno_perdido():
    """process_batch devuelve 'pendiente:<id>' para el encolado y None solo cuando
    los audios se quedaron sin destino: de eso depende que la carpeta CSV se borre."""
    assert batch_cola.es_pendiente("pendiente:42") is True
    assert batch_cola.es_pendiente("batches/abc") is False
    assert batch_cola.es_pendiente(None) is False


# --- 6. Limpieza de archivos huérfanos --------------------------------------------

def test_se_borran_los_jsonl_viejos_que_no_son_de_ningun_lote(monkeypatch, tmp_path):
    """Un lote que se serializó pero no llegó a insertar su fila deja megas de audio
    tirados en disco."""
    monkeypatch.setattr(batch_cola.settings, "BATCH_PENDIENTES_DIR", str(tmp_path), raising=False)
    huerfano = tmp_path / "lote-viejo.jsonl"
    vivo = tmp_path / "lote-vivo.jsonl"
    reciente = tmp_path / "lote-recien-escrito.jsonl"
    for archivo in (huerfano, vivo, reciente):
        archivo.write_text("{}\n")
    viejo = 1_700_000_000  # bien atrás en el tiempo
    os.utime(huerfano, (viejo, viejo))
    os.utime(vivo, (viejo, viejo))

    engine = _FakeEngine(respuestas={"SELECT ArchivoJsonl": [(str(vivo),)]})
    assert batch_cola.limpiar_huerfanos_en_disco(engine) == 1

    assert not huerfano.exists()
    assert vivo.exists()       # tiene una fila viva en la cola
    assert reciente.exists()   # puede ser de un lote que todavía no insertó su fila
