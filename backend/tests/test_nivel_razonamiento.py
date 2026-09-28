"""
Nivel de razonamiento por plantilla (LOW / MEDIUM / HIGH).

Reemplaza al selector de modelo como perilla de costo/calidad y, sobre todo,
reemplaza al `thinking_budget=8192` que estaba fijo en gemini.py: en Gemini 3.x el
budget dejó de respetarse (medido el 2026-08-19: con budget=512 el modelo gastó
igual 1.150 tokens de pensamiento), y ese tope muerto es lo que dejó que el
razonamiento por auditoría pasara de ~2.000 a ~13.000 tokens.

Lo que estos tests fijan: que la config que sale hacia Gemini lleve `thinking_level`
y NO `thinking_budget` (si alguien lo vuelve a poner, vuelve a no significar nada),
y que el nivel de la plantilla llegue hasta ahí.

Test 100% offline: no toca DB, Gemini ni tokens.
Correr: pytest tests/test_nivel_razonamiento.py -m "not tokens"
"""
import json

import pytest

from AuditorIA import gemini, razonamiento
from AuditorIA.modelos_ia import MODELO_IA_DEFAULT
from app.models import PlantillasIA


ATRIBUTOS = [
    {"name": "cumple_saludo", "type": "critical_audit", "constraints": {"enum": ["OK", "NO OK", "EC"]}, "id": 1},
    {"name": "feedback", "type": "string", "constraints": None, "id": 2},
]


@pytest.fixture
def prompt_de_plantilla(monkeypatch):
    """Devuelve gemini.prompt() para una plantilla en memoria con el nivel pedido."""
    def _instalar(nivel_razonamiento):
        monkeypatch.setattr(
            gemini.plantillas_manager_instance,
            "obtener_plantilla_para_IA",
            lambda plantilla_id: PlantillasIA(
                system_prompts="Sos un auditor.",
                text="Auditá este llamado.",
                response_schema=json.dumps(ATRIBUTOS, ensure_ascii=False),
                modelo="gemini-fake",
                nivel_razonamiento=nivel_razonamiento,
            ),
        )
        return gemini.prompt(plantilla_id=1)
    return _instalar


# --------------------------------------------------------------------------- #
# El catálogo                                                                  #
# --------------------------------------------------------------------------- #
def test_el_default_es_uno_del_catalogo():
    assert razonamiento.NIVEL_RAZONAMIENTO_DEFAULT in razonamiento.NIVELES_RAZONAMIENTO


def test_minimal_no_se_ofrece_por_plantilla():
    """MINIMAL existe como valor interno (los modelos "lite" lo aceptan) pero las
    auditorías corren en el flash de turno, que no lo soporta: ofrecerlo en el
    editor sería ofrecer una plantilla que no puede auditar. Además el CHECK de la
    migración 2026-08-19b tampoco lo aceptaría."""
    assert "MINIMAL" not in [n["valor"] for n in razonamiento.catalogo_niveles_razonamiento()]
    assert razonamiento.nivel_valido("MINIMAL") is False


def test_una_plantilla_no_puede_guardar_minimal():
    """El endpoint valida con nivel_valido: si esto se afloja, alguien guarda MINIMAL
    en una plantilla y TODAS sus auditorías empiezan a fallar con 400."""
    assert all(razonamiento.nivel_valido(n["valor"])
               for n in razonamiento.catalogo_niveles_razonamiento())


def test_el_catalogo_expone_cual_es_el_default():
    """El editor marca el default con esta bandera; si ninguno la trae, el <select>
    de una plantilla sin nivel no sabría en qué posición pararse."""
    defaults = [n for n in razonamiento.catalogo_niveles_razonamiento() if n["es_default"]]
    assert len(defaults) == 1
    assert defaults[0]["valor"] == razonamiento.NIVEL_RAZONAMIENTO_DEFAULT


@pytest.mark.parametrize("entrada", [None, "", "TURBO", "medium ", 7])
def test_un_nivel_invalido_cae_al_default_en_vez_de_romper(entrada):
    """Una plantilla sin nivel (columna NULL, migración sin aplicar) o con basura
    tiene que auditar igual: el nivel es una optimización de costo, no un requisito."""
    assert razonamiento.normalizar(entrada) == razonamiento.NIVEL_RAZONAMIENTO_DEFAULT


@pytest.mark.parametrize("entrada,esperado", [("low", "LOW"), ("Medium", "MEDIUM"), ("HIGH", "HIGH")])
def test_el_nivel_se_normaliza_en_mayusculas(entrada, esperado):
    assert razonamiento.normalizar(entrada) == esperado


# --------------------------------------------------------------------------- #
# La config que viaja a Gemini                                                 #
# --------------------------------------------------------------------------- #
def test_la_config_manda_thinking_level_y_no_thinking_budget(prompt_de_plantilla):
    """El corazón de la feature. `thinking_budget` es letra muerta en Gemini 3.x:
    si vuelve a aparecer acá, el tope de pensamiento vuelve a ser una ilusión."""
    info = prompt_de_plantilla("HIGH")
    config = gemini.obtener_configuracion_gemini(info)

    assert config.thinking_config.thinking_level == "HIGH"
    assert config.thinking_config.thinking_budget is None


def test_el_nivel_de_la_plantilla_llega_a_la_config(prompt_de_plantilla):
    info = prompt_de_plantilla("LOW")
    assert info["nivel_razonamiento"] == "LOW"
    assert gemini.obtener_configuracion_gemini(info).thinking_config.thinking_level == "LOW"


def test_una_plantilla_sin_nivel_audita_con_el_default(prompt_de_plantilla):
    """El caso real del día del deploy: 33 de 40 plantillas tienen la columna NULL."""
    info = prompt_de_plantilla(None)
    config = gemini.obtener_configuracion_gemini(info)
    assert config.thinking_config.thinking_level == razonamiento.NIVEL_RAZONAMIENTO_DEFAULT


def test_los_pensamientos_siguen_incluidos(prompt_de_plantilla):
    """include_thoughts alimenta la columna thoughts_tokens del log; sin eso, el
    costo del razonamiento se factura pero no se puede ver en /uso-ia."""
    info = prompt_de_plantilla("MEDIUM")
    assert gemini.obtener_configuracion_gemini(info).thinking_config.include_thoughts is True


def test_el_nivel_no_depende_del_pedido_de_transcripcion(prompt_de_plantilla):
    """La rama de transcripción arma otro response_schema; el nivel no tiene que
    perderse en el camino."""
    info = prompt_de_plantilla("HIGH")
    config = gemini.obtener_configuracion_gemini(info, incluir_transcripcion=True)
    assert config.thinking_config.thinking_level == "HIGH"


# --------------------------------------------------------------------------- #
# MINIMAL y el degradado por modelo                                            #
# --------------------------------------------------------------------------- #
def test_minimal_se_degrada_a_low_en_un_modelo_que_no_lo_soporta():
    """El flash de las auditorías no está en la lista blanca (gemini-3.7-flash
    respondía 400 INVALID_ARGUMENT ante MINIMAL). Degradar es obligatorio: el modelo
    de cada consumidor sale de settings y puede cambiarse por .env sin tocar código,
    así que pedir MINIMAL a ciegas es un 400 esperando."""
    assert razonamiento.nivel_para_modelo("MINIMAL", MODELO_IA_DEFAULT) == "LOW"


def test_minimal_se_respeta_en_los_modelos_que_si_lo_soportan():
    assert razonamiento.nivel_para_modelo("MINIMAL", "gemini-3.5-flash-lite") == "MINIMAL"


@pytest.mark.parametrize("modelo", [None, "", "gemini-99-turbo-que-no-existe"])
def test_un_modelo_desconocido_degrada_en_vez_de_reventar(modelo):
    """Lista blanca, no negra: sale un modelo nuevo cada ~3 semanas y el que no está
    en la lista tiene que caer a LOW (que funciona en todos), no mandarse con MINIMAL."""
    assert razonamiento.nivel_para_modelo("MINIMAL", modelo) == "LOW"


def test_el_degradado_no_toca_los_otros_niveles():
    for nivel in ("LOW", "MEDIUM", "HIGH"):
        assert razonamiento.nivel_para_modelo(nivel, MODELO_IA_DEFAULT) == nivel


# --------------------------------------------------------------------------- #
# Política por consumidor                                                      #
# --------------------------------------------------------------------------- #
def test_los_asistentes_que_mejoran_el_sistema_piensan_al_maximo():
    """Redactar prompts de plantillas y armar el markdown del chatbot son pocas
    ejecuciones por día cuyo resultado reusan miles de auditorías/consultas."""
    assert razonamiento.NIVEL_RAZONAMIENTO_ASISTENTES == "HIGH"


def test_el_asistente_de_plantillas_pide_el_maximo():
    from AuditorIA import asistente_plantillas

    assert asistente_plantillas.razonamiento.NIVEL_RAZONAMIENTO_ASISTENTES == "HIGH"


def test_el_asistente_de_docs_pide_el_maximo_por_defecto():
    import inspect

    from AuditorIA import asistente_docs

    firma = inspect.signature(asistente_docs._generar_json)
    assert firma.parameters["nivel_razonamiento"].default == "HIGH"
    assert "thinking_budget" not in firma.parameters


def test_el_asistente_de_cartas_corre_en_un_lite_y_no_razona():
    """Cartas comparte el camino de asistente_docs pero NO su modelo: su tarea es
    mecánica (separar parte fija de variable en cartas ya escritas) y de lote. El
    modelo lite además ACEPTA MINIMAL, cosa que el flash de las auditorías no."""
    from AuditorIA import asistente_cartas

    modelo = asistente_cartas._modelo()
    assert razonamiento.soporta_minimal(modelo), (
        f"{modelo} no acepta MINIMAL: cartas volvería a degradar a LOW"
    )
    nivel = razonamiento.nivel_para_modelo(razonamiento.NIVEL_RAZONAMIENTO_MECANICO, modelo)
    assert nivel == "MINIMAL"


def test_el_modelo_de_cartas_se_registra_como_el_suyo(monkeypatch):
    """Si el consumo se loguea con el modelo de docs, las llamadas de cartas se
    costean ~5x más caras de lo que son en /uso-ia (tarifa del flash contra la
    de un lite)."""
    from AuditorIA import asistente_docs

    registrado = {}
    monkeypatch.setattr(asistente_docs, "_modelo", lambda: MODELO_IA_DEFAULT)

    def fake_registrar_uso_ia(**kwargs):
        registrado.update(kwargs)

    import app.uso_ia as uso_ia
    monkeypatch.setattr(uso_ia, "registrar_uso_ia", fake_registrar_uso_ia)
    asistente_docs._registrar_consumo(
        response=None, user_id=None, ref_label="cartas_analizar", intento=0,
        modelo="gemini-3.5-flash-lite",
    )
    assert registrado["modelo"] == "gemini-3.5-flash-lite"


def test_las_tareas_mecanicas_no_razonan():
    """Clasificar/extraer sobre texto ya escrito: no hay nada que razonar y el
    pensamiento se factura a tarifa de salida. (El nivel efectivo depende del modelo:
    ver test_minimal_se_degrada_a_low_en_un_modelo_que_no_lo_soporta.)"""
    assert razonamiento.NIVEL_RAZONAMIENTO_MECANICO == "MINIMAL"


# --------------------------------------------------------------------------- #
# Cola de transcripciones                                                      #
# --------------------------------------------------------------------------- #
def test_la_transcripcion_pide_nivel_y_no_budget():
    """Mismo agujero que la auditoría: el `thinking_budget=1024` que había acá no
    frenaba nada — un job real de 3.7-flash gastó 20.071 tokens de pensamiento
    (19,6x) contra 4.100 de salida."""
    from AuditorIA import transcripcion_cola

    config = transcripcion_cola._config_transcripcion()
    assert config.thinking_config.thinking_budget is None
    assert config.thinking_config.thinking_level == "LOW"


def test_la_transcripcion_no_pide_los_pensamientos_de_vuelta():
    """include_thoughts=False no ahorra por sí solo (lo que ahorra es el nivel), pero
    sí evita traer texto que nadie lee."""
    from AuditorIA import transcripcion_cola

    assert transcripcion_cola._config_transcripcion().thinking_config.include_thoughts is False


def test_la_transcripcion_cae_a_LOW_y_no_al_default_de_auditorias(monkeypatch):
    """El piso de la transcripción es propio: si el setting queda vacío no puede
    heredar el MEDIUM de las auditorías, que ahí sería razonamiento pagado al pedo."""
    from AuditorIA import transcripcion_cola

    monkeypatch.setattr(transcripcion_cola.settings, "TRANSCRIPCION_NIVEL_RAZONAMIENTO", "", raising=False)
    assert transcripcion_cola._config_transcripcion().thinking_config.thinking_level == "LOW"
    assert razonamiento.NIVEL_RAZONAMIENTO_DEFAULT != "LOW"  # si no, el test no prueba nada


def test_el_nivel_de_transcripcion_se_puede_subir_por_config(monkeypatch):
    from AuditorIA import transcripcion_cola

    monkeypatch.setattr(transcripcion_cola.settings, "TRANSCRIPCION_NIVEL_RAZONAMIENTO", "HIGH", raising=False)
    assert transcripcion_cola._config_transcripcion().thinking_config.thinking_level == "HIGH"


def test_un_nivel_de_transcripcion_invalido_cae_a_LOW(monkeypatch):
    from AuditorIA import transcripcion_cola

    monkeypatch.setattr(transcripcion_cola.settings, "TRANSCRIPCION_NIVEL_RAZONAMIENTO", "TURBO", raising=False)
    assert transcripcion_cola._config_transcripcion().thinking_config.thinking_level == "LOW"


# --------------------------------------------------------------------------- #
# Lo que se ve en el mail de la corrida                                        #
# --------------------------------------------------------------------------- #
def test_el_mail_de_la_corrida_muestra_el_nivel():
    from AuditorIA.execution_log import armar_detalle_html

    html = armar_detalle_html(
        modo="batch", origen_txt="Ejecución manual",
        tokens={"input_tokens": 1000, "output_tokens": 100, "thoughts_tokens": 9000},
        nivel_razonamiento="HIGH",
    )
    assert "razonamiento Alto" in html


def test_el_mail_de_una_corrida_vieja_no_inventa_nivel():
    """Las corridas anteriores a la migración no tienen nivel: no se las etiqueta
    con el default, porque justo son las que corrieron sin tope."""
    from AuditorIA.execution_log import armar_detalle_html

    html = armar_detalle_html(modo="sync", origen_txt="Ejecución manual", nivel_razonamiento=None)
    assert "razonamiento" not in html.split("<li>Modo:")[1].split("</li>")[0]
