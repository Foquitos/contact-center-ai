"""Caché de contexto de la plantilla: qué se cachea, cuándo y qué pasa si falla.

El bloque fijo de una plantilla (instrucción de sistema + consignas de los atributos)
es el 47% del input de auditorías y hasta el 2026-09-01 se pagaba entero en cada
llamado. Acá se verifica que:

  * con caché, el request NO repite ese bloque (si lo repitiera se pagaría dos veces
    y la API rechaza mandar system_instruction junto con cached_content);
  * sin caché, todo viaja inline exactamente como antes;
  * la caché se reusa entre corridas (que es donde está el ahorro) y no se crea para
    corridas chicas (donde el almacenamiento sale más caro que lo que ahorra);
  * cualquier fallo de la caché deja la auditoría andando.

Test 100% offline: no toca DB, Gemini ni tokens.
"""
import types as pytypes

import pandas as pd
import pytest
from google.genai import types

from AuditorIA import cache_plantillas, gemini


PROMPT_INFO = {
    "system": "Sos un auditor.",
    "text": "Auditá este llamado según los atributos.",
    "nombre_id_map": {},
    "modelo": "gemini-fake",
    "response_schema": types.Schema(
        type=types.Type.OBJECT,
        required=["Saludo"],
        properties={"Saludo": types.Schema(type=types.Type.STRING)},
    ),
}


class _CachesFalsas:
    def __init__(self, vivas=()):
        self.vivas = list(vivas)
        self.creadas = []
        self.refrescadas = []
        self.explota_al_crear = False

    def list(self):
        return iter(self.vivas)

    def create(self, model, config):
        if self.explota_al_crear:
            raise RuntimeError("mínimo de tokens no alcanzado")
        self.creadas.append(config)
        cache = pytypes.SimpleNamespace(
            name=f"cachedContents/{len(self.creadas)}",
            display_name=config.display_name,
            usage_metadata=pytypes.SimpleNamespace(total_token_count=5000),
        )
        self.vivas.append(cache)
        return cache

    def update(self, name, config):
        self.refrescadas.append(name)


class _GeminiFalso:
    def __init__(self, caches=None):
        self.caches = caches or _CachesFalsas()


@pytest.fixture(autouse=True)
def _config_por_defecto(monkeypatch):
    monkeypatch.setattr(cache_plantillas.settings, "GEMINI_CACHE_PLANTILLAS", True, raising=False)
    # None = el mínimo se deriva del TTL, que es el default real (40 con TTL de 24 h).
    monkeypatch.setattr(cache_plantillas.settings, "GEMINI_CACHE_MIN_LLAMADOS", None, raising=False)
    monkeypatch.setattr(cache_plantillas.settings, "GEMINI_CACHE_MIN_TOKENS", 1, raising=False)
    monkeypatch.setattr(cache_plantillas.settings, "GEMINI_CACHE_TTL_HORAS", 24, raising=False)


def _obtener(api, prompt_info=None, llamados=50):
    prompt_info = prompt_info or dict(PROMPT_INFO)
    return cache_plantillas.obtener_o_crear(
        api, prompt_info,
        sistema=gemini.instruccion_de_sistema(prompt_info),
        modelo="gemini-fake", llamados=llamados, plantilla_id=7,
    )


# --------------------------------------------------------------------------- #
# Ciclo de vida                                                                #
# --------------------------------------------------------------------------- #
def test_la_primera_corrida_crea_la_cache():
    api = _GeminiFalso()
    nombre = _obtener(api)
    assert nombre == "cachedContents/1"
    assert len(api.caches.creadas) == 1


def test_la_segunda_corrida_reusa_la_misma_cache():
    """Acá está el ahorro: una plantilla que audita todos los días paga el
    almacenamiento UNA vez y lo amortiza entre todas sus corridas."""
    api = _GeminiFalso()
    primera = _obtener(api)
    segunda = _obtener(api)

    assert primera == segunda
    assert len(api.caches.creadas) == 1
    assert api.caches.refrescadas == [primera]   # se le corre el vencimiento


def test_una_corrida_chica_reusa_pero_no_crea():
    """Crear una caché para 3 llamados cuesta más almacenamiento que lo que ahorra;
    reusar una que ya está paga no tiene mínimo."""
    api = _GeminiFalso()
    assert _obtener(api, llamados=3) is None
    assert api.caches.creadas == []

    _obtener(api, llamados=50)                    # ahora sí existe
    assert _obtener(api, llamados=3) == "cachedContents/1"


def test_editar_la_plantilla_no_reusa_la_cache_vieja():
    """La caché se identifica por su contenido: auditar con el texto viejo sería
    peor que no cachear."""
    api = _GeminiFalso()
    _obtener(api)
    otra = dict(PROMPT_INFO, text="Otra consigna distinta.")
    assert _obtener(api, otra) == "cachedContents/2"


def test_si_la_cache_falla_la_auditoria_sigue_sin_cache():
    api = _GeminiFalso()
    api.caches.explota_al_crear = True
    assert _obtener(api) is None


def test_se_puede_apagar_por_configuracion(monkeypatch):
    monkeypatch.setattr(cache_plantillas.settings, "GEMINI_CACHE_PLANTILLAS", False, raising=False)
    api = _GeminiFalso()
    assert _obtener(api) is None
    assert api.caches.creadas == []


def test_un_bloque_chico_no_se_cachea(monkeypatch):
    monkeypatch.setattr(cache_plantillas.settings, "GEMINI_CACHE_MIN_TOKENS", 100_000, raising=False)
    api = _GeminiFalso()
    assert _obtener(api) is None


# --------------------------------------------------------------------------- #
# Qué viaja en el request                                                      #
# --------------------------------------------------------------------------- #
def test_con_cache_el_request_no_repite_el_bloque_fijo():
    fila = pd.Series({"idInteraccion": "a1", "operador": "María Gómez"})
    texto = gemini.texto_del_turno(fila, PROMPT_INFO, cacheado=True)

    assert PROMPT_INFO["text"] not in texto      # ya está del lado de Gemini
    assert "María Gómez" in texto                # los datos de la interacción sí van

    config = gemini.obtener_configuracion_gemini(PROMPT_INFO, cache_contexto="cachedContents/1")
    assert config.cached_content == "cachedContents/1"
    # Mandar las dos cosas es un error de la API además de pagarlo dos veces.
    assert config.system_instruction is None


def test_sin_cache_viaja_todo_inline_como_antes():
    fila = pd.Series({"idInteraccion": "a1", "operador": "María Gómez"})
    texto = gemini.texto_del_turno(fila, PROMPT_INFO, cacheado=False)

    assert PROMPT_INFO["text"] in texto
    assert "María Gómez" in texto

    config = gemini.obtener_configuracion_gemini(PROMPT_INFO)
    assert config.cached_content is None
    assert "Sos un auditor." in config.system_instruction[0].text


def test_la_instruccion_de_sistema_no_depende_de_la_transcripcion():
    """Si cambiara, harían falta dos cachés por plantilla en vez de una: la consigna
    de transcribir va en el turno del usuario, no en la instrucción de sistema."""
    con = gemini.obtener_configuracion_gemini(PROMPT_INFO, incluir_transcripcion=True)
    sin = gemini.obtener_configuracion_gemini(PROMPT_INFO, incluir_transcripcion=False)
    assert con.system_instruction[0].text == sin.system_instruction[0].text

    fila = pd.Series({"idInteraccion": "a1"})
    texto = gemini.texto_del_turno(fila, PROMPT_INFO, cacheado=True, con_transcripcion=True)
    assert "TRANSCRIPCIÓN ADICIONAL" in texto


# --------------------------------------------------------------------------- #
# El lote de batch                                                             #
# --------------------------------------------------------------------------- #
class _GeminiBatchFalso(_GeminiFalso):
    """Lo mínimo para que `process_batch` llegue a armar y serializar el lote."""

    def __init__(self, caches=None):
        super().__init__(caches)
        self.subidos = []
        self.batches = pytypes.SimpleNamespace(
            create=lambda model, src, config: pytypes.SimpleNamespace(name="batches/fake-1"),
            list=lambda config=None: iter([]),
        )
        self.files = pytypes.SimpleNamespace(
            upload=lambda file, config=None: pytypes.SimpleNamespace(name="files/fake-1")
        )


class _ConexionFalsa:
    def __enter__(self): return self
    def __exit__(self, *exc): return False
    def begin(self): return pytypes.SimpleNamespace(commit=lambda: None, rollback=lambda: None)
    def execute(self, *a, **kw): return None


class _EngineFalso:
    def connect(self): return _ConexionFalsa()


@pytest.fixture
def lote(monkeypatch, tmp_path):
    """Corre process_batch y devuelve los requests tal como se serializaron."""
    monkeypatch.setattr(gemini, "prompt", lambda plantilla_id: dict(PROMPT_INFO))
    monkeypatch.setattr(
        gemini, "preparar_contenido",
        lambda audio_dir: (__import__("io").BytesIO(b"audio"), "audio/mp3", "a", "a"),
    )
    escritos = []

    def _espiar(gemini_api, requests, modelo, ruta=None):
        escritos.append(requests)
        destino = ruta or str(tmp_path / f"lote-{len(escritos)}.jsonl")
        open(destino, "w").write("{}\n")
        return destino

    monkeypatch.setattr(gemini.batch_cola, "escribir_jsonl", _espiar)

    def _correr(api):
        df = pd.DataFrame([{"idInteraccion": "a1", "Segmento": 1, "audio_dir": "/a1.wav",
                            "operador": "María Gómez"}])
        gemini.process_batch(engine=_EngineFalso(), batch_df=df, plantilla_id=7,
                             gemini_api=api, user_id=99)
        return escritos

    return _correr


def test_el_lote_que_sale_ahora_referencia_la_cache(lote, monkeypatch):
    monkeypatch.setattr(cache_plantillas.settings, "GEMINI_CACHE_MIN_LLAMADOS", 1, raising=False)
    api = _GeminiBatchFalso()

    requests = lote(api)[0]

    assert requests[0]["config"].cached_content == "cachedContents/1"
    texto = requests[0]["contents"][0].parts[1].text
    assert PROMPT_INFO["text"] not in texto      # el bloque fijo ya está en la caché
    assert "María Gómez" in texto                # los datos de la interacción, no


def test_el_lote_que_va_a_la_cola_no_referencia_ninguna_cache(lote, monkeypatch):
    """Un lote encolado puede salir muchas horas después: si apuntara a una caché que
    ya venció, esos requests fallarían y las auditorías se perderían."""
    monkeypatch.setattr(cache_plantillas.settings, "GEMINI_CACHE_MIN_LLAMADOS", 1, raising=False)
    monkeypatch.setattr(gemini.batch_cola, "hay_cupo", lambda api: False)
    monkeypatch.setattr(gemini.batch_cola, "encolar", lambda *a, **kw: 1)
    api = _GeminiBatchFalso()

    requests = lote(api)[0]

    assert requests[0]["config"].cached_content is None
    assert PROMPT_INFO["text"] in requests[0]["contents"][0].parts[1].text
    assert api.caches.creadas == []


def test_si_el_envio_falla_el_lote_se_reescribe_sin_cache(lote, monkeypatch):
    """Mismo motivo: al caer en la cola tiene que quedar autosuficiente."""
    monkeypatch.setattr(cache_plantillas.settings, "GEMINI_CACHE_MIN_LLAMADOS", 1, raising=False)
    monkeypatch.setattr(gemini.batch_cola, "encolar", lambda *a, **kw: 1)
    api = _GeminiBatchFalso()
    api.batches.create = lambda model, src, config: (_ for _ in ()).throw(RuntimeError("sin cupo"))

    escritos = lote(api)

    assert len(escritos) == 2                      # se serializó de nuevo
    assert escritos[0][0]["config"].cached_content == "cachedContents/1"
    assert escritos[1][0]["config"].cached_content is None
    assert PROMPT_INFO["text"] in escritos[1][0]["contents"][0].parts[1].text


# --------------------------------------------------------------------------- #
# El mínimo se deriva del TTL                                                  #
# --------------------------------------------------------------------------- #
def test_el_minimo_para_crear_sale_del_ttl(monkeypatch):
    """Una caché se paga con 1,67 llamados por hora de vida: si alguien acorta el TTL,
    el mínimo tiene que bajar solo o queda desalineado."""
    monkeypatch.setattr(cache_plantillas.settings, "GEMINI_CACHE_MIN_LLAMADOS", None, raising=False)

    monkeypatch.setattr(cache_plantillas.settings, "GEMINI_CACHE_TTL_HORAS", 24, raising=False)
    assert cache_plantillas.minimo_llamados() == 40

    monkeypatch.setattr(cache_plantillas.settings, "GEMINI_CACHE_TTL_HORAS", 6, raising=False)
    assert cache_plantillas.minimo_llamados() == 10


def test_una_corrida_por_debajo_del_minimo_derivado_no_crea_cache(monkeypatch):
    """El caso real del 2026-09-01: 10 llamados con 9.401 tokens de bloque fijo
    ahorraban US$0,028 y pagaban US$0,113 de almacenamiento."""
    monkeypatch.setattr(cache_plantillas.settings, "GEMINI_CACHE_MIN_LLAMADOS", None, raising=False)
    monkeypatch.setattr(cache_plantillas.settings, "GEMINI_CACHE_TTL_HORAS", 24, raising=False)
    api = _GeminiFalso()

    assert _obtener(api, llamados=10) is None
    assert _obtener(api, llamados=40) == "cachedContents/1"


# --------------------------------------------------------------------------- #
# Lo que ve el usuario en la pantalla de Auditar                               #
# --------------------------------------------------------------------------- #
# El aviso tiene que contestar dos cosas mientras se arma la corrida: cuánto pesa el
# bloque fijo de la plantilla y a partir de cuántas auditorías conviene. Es lo que
# empuja a auditar de a muchas: el bloque se paga UNA vez por corrida, así que cuantas
# más lo compartan, más barata sale cada una.
def test_la_estimacion_para_la_pantalla_no_llama_a_gemini():
    """Se calcula por caracteres a propósito: se recalcula cada vez que el usuario
    cambia de plantilla y no puede costar un ida y vuelta a la API."""
    info = dict(PROMPT_INFO, text="x" * 41_800)
    datos = cache_plantillas.estimar_ahorro(
        "sistema", info["text"], tarifa=(0.75, 3.75, 0.075), modo="batch")

    assert datos["tokens_bloque_fijo"] == pytest.approx(10_000, rel=0.01)
    assert datos["minimo_llamados"] == 40
    # 10.000 tokens x (0,75 - 0,075) por millón, y el batch vale la mitad.
    assert datos["ahorro_usd_por_auditoria"] == pytest.approx(10_000 * 0.675 / 2 / 1e6, rel=0.01)


def test_el_sincronico_ahorra_el_doble_por_auditoria():
    """No es una excepción: el sincrónico cuesta el doble, así que lo que se ahorra
    al no repetir el bloque también es el doble."""
    batch = cache_plantillas.estimar_ahorro("s"*1000, "t"*40_000, tarifa=(0.75, 3.75, 0.075), modo="batch")
    sync = cache_plantillas.estimar_ahorro("s"*1000, "t"*40_000, tarifa=(0.75, 3.75, 0.075), modo="sync")
    assert sync["ahorro_usd_por_auditoria"] == pytest.approx(
        2 * batch["ahorro_usd_por_auditoria"], rel=1e-6)


def test_sin_tarifa_igual_dice_cuantas_hacen_falta():
    """Si no hay precio cargado para el modelo, el aviso pierde el monto pero no el
    dato que más importa: cuántas auditorías hacen falta."""
    datos = cache_plantillas.estimar_ahorro("s"*1000, "t"*40_000, tarifa=None)
    assert datos["ahorro_usd_por_auditoria"] is None
    assert datos["minimo_llamados"] == 40
    assert datos["tokens_bloque_fijo"] > 0


def test_una_plantilla_chiquita_no_promete_un_ahorro_que_no_existe(monkeypatch):
    monkeypatch.setattr(cache_plantillas.settings, "GEMINI_CACHE_MIN_TOKENS", 1024, raising=False)
    datos = cache_plantillas.estimar_ahorro("hola", "chau", tarifa=(0.75, 3.75, 0.075))
    assert datos["activo"] is False


# --------------------------------------------------------------------------- #
# El porcentaje del aviso: contra qué se compara el ahorro                     #
# --------------------------------------------------------------------------- #
# El ahorro se calcula con el bloque fijo de HOY; el costo de una auditoría sale del
# historial. Si la plantilla sumó documentos de referencia, el historial no los pagó:
# sin corregirlo, el porcentaje salía inflado y podía pasar del 100%.
TARIFA = (0.75, 3.75, 0.075)


def _version(auditorias, input_por_aud, conocimiento=0, output=600, thoughts=1500):
    return {"auditorias": auditorias, "input_tokens": auditorias * input_por_aud,
            "output_tokens": auditorias * output, "thoughts_tokens": auditorias * thoughts,
            "tokens_conocimiento": conocimiento}


def test_sin_historial_o_sin_tarifa_no_hay_costo():
    assert cache_plantillas.costo_por_auditoria([], tarifa=TARIFA) is None
    assert cache_plantillas.costo_por_auditoria([_version(10, 10_000)], tarifa=None) is None


def test_el_costo_es_el_de_un_llamado_sin_cache_en_el_modo_pedido():
    batch = cache_plantillas.costo_por_auditoria([_version(10, 10_000)], tarifa=TARIFA, modo="batch")
    sync = cache_plantillas.costo_por_auditoria([_version(10, 10_000)], tarifa=TARIFA, modo="sync")
    assert batch == pytest.approx((10_000 * 0.75 + 2_100 * 3.75) / 1e6 / 2, abs=1e-4)
    assert sync == pytest.approx(2 * batch, abs=1e-4)


def test_el_conocimiento_nuevo_se_suma_al_historial_que_no_lo_pago():
    sin = cache_plantillas.costo_por_auditoria([_version(10, 10_000)], tarifa=TARIFA)
    con = cache_plantillas.costo_por_auditoria(
        [_version(10, 10_000)], tokens_conocimiento_actual=20_000, tarifa=TARIFA)
    assert con == pytest.approx(sin + 20_000 * 0.75 / 1e6 / 2, abs=1e-4)


def test_el_conocimiento_que_ya_se_pagaba_no_se_cuenta_dos_veces():
    """Una versión que ya leía los mismos documentos: el historial ya los incluye."""
    ya_pagado = cache_plantillas.costo_por_auditoria(
        [_version(10, 30_000, conocimiento=20_000)], tokens_conocimiento_actual=20_000, tarifa=TARIFA)
    nunca = cache_plantillas.costo_por_auditoria(
        [_version(10, 10_000)], tokens_conocimiento_actual=20_000, tarifa=TARIFA)
    assert ya_pagado == pytest.approx(nunca, abs=1e-4)


def test_con_documentos_nuevos_el_ahorro_no_supera_el_costo():
    """El caso que estaba roto: plantilla que audita con ~5.000 tokens de bloque y le
    suman 21.000 de documentos. Con el costo viejo daba ~110%."""
    bloque = 5_000 + 21_000
    ahorro = cache_plantillas.estimar_ahorro(
        "s" * int(bloque * cache_plantillas.CHARS_POR_TOKEN), "", tarifa=TARIFA)["ahorro_usd_por_auditoria"]
    costo = cache_plantillas.costo_por_auditoria(
        [_version(100, 10_500)], tokens_conocimiento_actual=21_000, tarifa=TARIFA)
    assert ahorro / costo < 1
