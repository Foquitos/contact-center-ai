"""
Smoke test EN VIVO del tope de texto libre: que la API de Gemini ACEPTE el `maxLength`
que `AuditorIA/gemini.py::prompt()` le pone a los atributos `string`/`array_string`.

POR QUÉ EXISTE
--------------
`maxLength` es parte del subconjunto de OpenAPI que declara la API, pero si algún modelo
lo rechazara, el error saldría recién al generar: con los 5 reintentos de
`apply_auditoria_threads` agotados, TODA auditoría de una plantilla con un atributo de
texto fallaría. Este test lo verifica con un llamado mínimo (texto, sin audio) por cada
modelo del catálogo, antes de deployar.

Además chequea que el modelo respete el tope en la práctica (se le pide a propósito un
texto largo). Si un día no lo respetara, sigue estando la red del recorte al guardar
(`sql_a_Claude`), así que eso se reporta pero no rompe el test.

CONSUME TOKENS (pocos: prompt corto y sin audio).
Ejecutar solo esta suite:   pytest tests/test_limites_texto_live.py -m tokens
Excluirla del resto:        pytest -m "not tokens"
"""
import json

import pytest

pytestmark = pytest.mark.tokens


# Plantilla mínima con los dos tipos de texto libre + un tipo acotado de control.
ATRIBUTOS = [
    {"name": "cumple_saludo", "type": "critical_audit", "constraints": {"enum": ["OK", "NO OK"]}, "id": 1},
    {"name": "feedback", "type": "string", "constraints": None, "id": 2},
    {"name": "objeciones", "type": "array_string", "constraints": None, "id": 3},
]

# Interacción de juguete. Se le pide EXPLÍCITAMENTE un texto largo para ver si el
# `maxLength` del esquema le pone freno.
INTERACCION = (
    "Transcripción del llamado:\n"
    "Agente: Buenas tardes, le habla Marcos de Servicios Acme, ¿hablo con el titular?\n"
    "Cliente: Sí, soy yo. Mire, hace tres días que no tengo luz y ya llamé dos veces.\n"
    "Agente: Entiendo, le pido disculpas. Ya tengo el reclamo acá, número 4471.\n"
    "Cliente: Y no me sirve el número, yo quiero saber cuándo viene el técnico.\n"
    "Agente: La cuadrilla está asignada para hoy antes de las 20. Le dejo el registro.\n"
    "Cliente: Bueno, gracias.\n"
    "Agente: Gracias a usted, buenas tardes.\n"
)


def _modelos():
    from AuditorIA.modelos_ia import MODELOS_IA

    return sorted(MODELOS_IA)


def pytest_generate_tests(metafunc):
    if "modelo" in metafunc.fixturenames:
        opcion = metafunc.config.getoption("-m") or ""
        if "not tokens" in opcion:
            metafunc.parametrize("modelo", [])
            return
        modelos = _modelos()
        metafunc.parametrize("modelo", modelos, ids=modelos)


@pytest.fixture
def prompt_info(monkeypatch):
    """El `prompt_info` real (con `max_length`), sin pegarle a la BD."""
    from AuditorIA import gemini
    from app.models import PlantillasIA

    monkeypatch.setattr(
        gemini.plantillas_manager_instance,
        "obtener_plantilla_para_IA",
        lambda plantilla_id: PlantillasIA(
            system_prompts="Sos un analista de calidad senior de un contact center.",
            text=(
                "Auditá la siguiente interacción. En 'feedback' escribí una devolución "
                "para el operador y en 'objeciones' listá las objeciones del cliente."
            ),
            response_schema=json.dumps(ATRIBUTOS, ensure_ascii=False),
            modelo="ignorado",
        ),
    )
    # `obtener_o_crear_version` toca la BD; acá no interesa qué versión es.
    monkeypatch.setattr(gemini, "obtener_o_crear_version", lambda engine, plantilla_id: None)
    return gemini.prompt(plantilla_id=1)


def test_la_api_acepta_max_length_en_el_response_schema(modelo, prompt_info):
    """Lo crítico: si la API rechazara `maxLength`, fallaría TODA auditoría con texto libre."""
    from google import genai
    from google.genai import types

    from AuditorIA import gemini, limites_texto
    from app.config import settings

    tope = limites_texto.max_caracteres()
    assert prompt_info["response_schema"].properties["feedback"].max_length == tope

    cliente = genai.Client(api_key=settings.GEMINI_AUDITORIA_API_KEY)
    config = gemini.obtener_configuracion_gemini(prompt_info, temperatura=0.4)

    respuesta = cliente.models.generate_content(
        model=modelo,
        contents=[types.Content(role="user", parts=[types.Part.from_text(
            text=INTERACCION + "\n\nEscribí el feedback lo más extenso y detallado que puedas."
        )])],
        config=config,
    )

    _, texto = gemini._extraer_texto_respuesta(respuesta)
    datos = json.loads(texto, strict=False)

    assert set(datos) >= {"cumple_saludo", "feedback", "objeciones"}
    assert isinstance(datos["feedback"], str) and datos["feedback"].strip()

    # Informativo: cuánto escribió realmente contra el tope pedido.
    largo = len(datos["feedback"])
    print(f"\n[{modelo}] feedback={largo} caracteres (tope {tope}), "
          f"objeciones={[len(o) for o in datos['objeciones']]}")
    if largo > tope:
        pytest.xfail(
            f"{modelo} ignoró maxLength ({largo} > {tope}). La API lo ACEPTA (que es lo que "
            "importa acá); el largo lo corta igual el recorte de sql_a_Claude."
        )


# El caso que motiva todo: una plantilla YA escrita como pedido de transcripción (el
# rechazo del alta no la alcanza, porque ya está guardada). Lo que tiene que pasar es
# que la capa 2 —max_length + instrucción— la neutralice sola, sin reescribirla.
ATRIBUTOS_LEGACY = [
    {"name": "Transcripcion", "type": "string", "constraints": None, "id": 9},
]


def test_neutraliza_una_plantilla_vieja_que_pide_la_transcripcion(monkeypatch):
    """Un solo modelo (el default) para no gastar de más: lo que se mide es la conducta."""
    from google import genai
    from google.genai import types

    from AuditorIA import gemini, limites_texto
    from AuditorIA.modelos_ia import MODELO_IA_DEFAULT
    from app.config import settings
    from app.models import PlantillasIA

    # Tope bajo a propósito: con 1500 el modelo ya escribe corto por su cuenta y no se
    # vería si el freno funciona.
    monkeypatch.setattr(limites_texto.settings, "ATRIBUTO_TEXTO_MAX_CARACTERES", 300, raising=False)
    monkeypatch.setattr(
        gemini.plantillas_manager_instance,
        "obtener_plantilla_para_IA",
        lambda plantilla_id: PlantillasIA(
            system_prompts="Sos un analista de calidad senior de un contact center.",
            text="Auditá la interacción y completá cada atributo del esquema.",
            response_schema=json.dumps(
                [{**ATRIBUTOS_LEGACY[0], "constraints": None}], ensure_ascii=False
            ),
            modelo="ignorado",
        ),
    )
    monkeypatch.setattr(gemini, "obtener_o_crear_version", lambda engine, plantilla_id: None)

    # El prompt del atributo (que en la BD vive por atributo) va en el texto general:
    # es exactamente lo que la plantilla vieja le pide al modelo.
    info = gemini.prompt(plantilla_id=1)
    info["text"] += (
        "\n\nEn el atributo 'Transcripcion' transcribí la conversación completa, "
        "palabra por palabra, indicando quién habla en cada turno."
    )

    cliente = genai.Client(api_key=settings.GEMINI_AUDITORIA_API_KEY)
    respuesta = cliente.models.generate_content(
        model=MODELO_IA_DEFAULT,
        contents=[types.Content(role="user", parts=[types.Part.from_text(text=INTERACCION)])],
        config=gemini.obtener_configuracion_gemini(info, temperatura=0.4),
    )

    _, texto = gemini._extraer_texto_respuesta(respuesta)
    devuelto = json.loads(texto, strict=False)["Transcripcion"]
    print(f"\n[{MODELO_IA_DEFAULT}] atributo-transcripción={len(devuelto)} caracteres "
          f"(tope 300, interacción original={len(INTERACCION)}):\n{devuelto!r}")

    # No tiene que devolver el llamado entero. El margen es por si el modelo se pasa un
    # poco: lo que importa es que NO sea una transcripción completa.
    assert len(devuelto) < len(INTERACCION), "devolvió el llamado transcripto igual"
