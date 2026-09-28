"""
Tope de los atributos de texto libre y freno al "atributo transcripción".

Varias campañas pedían la transcripción del llamado adentro de un atributo `string`
("Transcribí la conversación completa"), que es la forma más cara de conseguir algo que
el sistema ya hace por otra vía: se paga como tokens de SALIDA en cada auditoría y, si
el JSON se corta, se pierde la auditoría entera. La contramedida tiene tres capas
(ver AuditorIA/limites_texto.py): rechazo en el alta, tope en el response_schema +
instrucción al modelo, y recorte al guardar.

Test 100% offline: no toca DB, Gemini ni tokens.
Correr: pytest tests/test_limites_texto_atributos.py -m "not tokens"
"""
import json

import pytest

from AuditorIA import gemini, limites_texto
from AuditorIA.Plantillas_prompts import plantillas_manager
from app.models import PlantillasIA


# --------------------------------------------------------------------------- #
# Detección del pedido de transcripción                                        #
# --------------------------------------------------------------------------- #
PIDEN_TRANSCRIPCION = [
    ("Transcripcion", "Completá este campo."),
    ("Transcripción del llamado", "Lo que corresponda."),
    ("Feedback", "Transcribí la conversación completa entre el operador y el cliente."),
    ("Detalle", "Transcribir el audio íntegro, indicando quién habla."),
    ("Resumen", "Detallá palabra por palabra lo que dijo el cliente."),
    ("Dialogo", "Incluí la transcripción del audio en este campo."),
    ("Texto", "Escribí la transcripción literal de la llamada."),
    # Acotar el alcance no sirve de excusa si igual pide el llamado entero (caso real:
    # ALARMIX · Hold y Silencios, 6.031 caracteres promedio por auditoría).
    ("Transcripción",
     "Proporciona la transcripción textual completa y exacta de la llamada de principio "
     "a fin. Identifica claramente los interlocutores e incluye las marcas de tiempo."),
    ("Reclamo", "Escribí todo lo que dijo el cliente sobre el reclamo."),
]

# Casos legítimos que NO se deben bloquear. Son los que hacen que el patrón tenga que
# ser estrecho: mencionar la transcripción como fuente, o pedir una cita corta, es
# exactamente lo que un buen prompt de atributo hace.
NO_PIDEN_TRANSCRIPCION = [
    ("Saludo", "Según la transcripción del llamado, indicá si el operador saludó."),
    ("Frase de cierre", "Transcribí la frase exacta que usó el operador para despedirse."),
    # Casos REALES de las plantillas de ALARMIX y Vantix (revisión del 2026-08-19): citar un
    # tramo corto es un criterio de auditoría legítimo —así se ve cómo se cortó la
    # llamada— y en la práctica devuelve 110-205 caracteres. El nombre dice
    # "transcripcion" pero el prompt acota el alcance, así que no se bloquea.
    ("transcripcion_final",
     "Transcribe los últimos 20 segundos de la conversación colocando correctamente "
     "quien esta hablando (Cliente/Operador)"),
    ("Transcripcion parcial", "Transcribí solo el fragmento donde se menciona el precio."),
    ("Feedback", "Escribí un feedback breve y accionable para el operador."),
    ("Motivo", "Resumí en dos líneas el motivo del contacto."),
    ("Objeciones", "Listá las objeciones que planteó el cliente."),
]


@pytest.mark.parametrize("nombre,prompt", PIDEN_TRANSCRIPCION)
def test_detecta_el_pedido_de_transcripcion(nombre, prompt):
    assert limites_texto.pide_transcripcion(nombre, prompt) is not None


@pytest.mark.parametrize("nombre,prompt", NO_PIDEN_TRANSCRIPCION)
def test_no_bloquea_prompts_legitimos(nombre, prompt):
    assert limites_texto.pide_transcripcion(nombre, prompt) is None


# --------------------------------------------------------------------------- #
# Capa 1: alta / edición del atributo                                          #
# --------------------------------------------------------------------------- #
def test_rechaza_el_atributo_de_texto_que_pide_la_transcripcion():
    with pytest.raises(ValueError) as excinfo:
        limites_texto.validar_atributo({
            "nombre": "Feedback", "tipo": "string",
            "prompt": "Transcribí la conversación completa.",
        })
    mensaje = str(excinfo.value)
    # El error tiene que enseñar el camino bueno, no solo negar.
    assert "Transcripción" in mensaje and "Auditorías Realizadas" in mensaje


def test_solo_aplica_a_los_tipos_de_texto_libre():
    """En un boolean/enum el valor ya está acotado: mencionar la transcripción es normal."""
    limites_texto.validar_atributo({
        "nombre": "Saludo", "tipo": "boolean",
        "prompt": "Transcribí la conversación completa y decí si saludó.",
    })  # no lanza


def test_tambien_frena_las_listas_de_texto():
    with pytest.raises(ValueError):
        limites_texto.validar_atributo({
            "nombre": "Frases", "tipo": "array_string",
            "prompt": "Devolvé la transcripción del llamado, una línea por intervención.",
        })


def test_se_puede_apagar_por_config(monkeypatch):
    monkeypatch.setattr(limites_texto.settings, "ATRIBUTO_BLOQUEAR_TRANSCRIPCION", False, raising=False)
    limites_texto.validar_atributo({
        "nombre": "Transcripcion", "tipo": "string", "prompt": "Transcribí el llamado.",
    })  # no lanza


class _FakeResult:
    def __init__(self, filas):
        self._filas = filas

    def first(self):
        return self._filas[0] if self._filas else None

    def fetchall(self):
        return self._filas

    def fetchone(self):
        return self.first()


class _FakeConnection:
    def __init__(self):
        self.ejecutados = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.ejecutados.append((sql, params or {}))
        if "INFORMATION_SCHEMA.COLUMNS" in sql:
            return _FakeResult([(1,)])
        return _FakeResult([])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self, connection):
        self._connection = connection

    def begin(self):
        return self._connection


def test_el_alta_no_toca_la_base_si_el_atributo_esta_rechazado():
    """La validación corre ANTES del SP: no queda medio atributo creado."""
    conn = _FakeConnection()
    manager = plantillas_manager(_FakeEngine(conn))

    with pytest.raises(ValueError):
        manager.crear_atributos_plantilla(7, [
            {"nombre": "Saludo", "prompt": "¿Saludó?", "tipo": "boolean", "orden": 0},
            {"nombre": "Transcripcion", "prompt": "El llamado entero.", "tipo": "string", "orden": 1},
        ])

    assert conn.ejecutados == []


# --------------------------------------------------------------------------- #
# Capa 2: response_schema + instrucción al modelo                              #
# --------------------------------------------------------------------------- #
ATRIBUTOS = [
    {"name": "cumple_saludo", "type": "critical_audit", "constraints": {"enum": ["OK", "NO OK"]}, "id": 1},
    {"name": "feedback", "type": "string", "constraints": None, "id": 2},
    {"name": "objeciones", "type": "array_string", "constraints": None, "id": 3},
]


@pytest.fixture
def plantilla_falsa(monkeypatch):
    def _instalar(atributos=ATRIBUTOS):
        monkeypatch.setattr(
            gemini.plantillas_manager_instance,
            "obtener_plantilla_para_IA",
            lambda plantilla_id: PlantillasIA(
                system_prompts="Sos un auditor.",
                text="Auditá este llamado.",
                response_schema=json.dumps(atributos, ensure_ascii=False),
                modelo="gemini-fake",
            ),
        )
        return gemini.prompt(plantilla_id=1)
    return _instalar


def test_el_schema_le_pone_tope_al_texto_libre(plantilla_falsa):
    """Lo que evita el gasto: el texto largo ni siquiera se genera."""
    tope = limites_texto.max_caracteres()
    propiedades = plantilla_falsa()["response_schema"].properties

    assert propiedades["feedback"].max_length == tope
    # En las listas el tope va en cada elemento.
    assert propiedades["objeciones"].items.max_length == tope
    # Los tipos acotados no se tocan.
    assert getattr(propiedades["cumple_saludo"], "max_length", None) is None


def test_el_prompt_le_explica_el_tope_a_la_ia(plantilla_falsa):
    texto = plantilla_falsa()["text"]

    assert "ATRIBUTOS DE TEXTO LIBRE" in texto
    assert "'feedback'" in texto and "'objeciones'" in texto
    assert str(limites_texto.max_caracteres()) in texto
    assert "NUNCA transcribas" in texto


def test_sin_atributos_de_texto_no_se_agrega_la_instruccion(plantilla_falsa):
    solo_criticos = [ATRIBUTOS[0]]
    assert "ATRIBUTOS DE TEXTO LIBRE" not in plantilla_falsa(solo_criticos)["text"]


# --------------------------------------------------------------------------- #
# Capa 3: recorte al guardar                                                   #
# --------------------------------------------------------------------------- #
def test_recorta_el_texto_que_supera_el_tope():
    tope = limites_texto.max_caracteres()
    largo = "a" * (tope + 500)

    recortado = limites_texto.recortar_valor(largo, atributo_id=3)

    assert len(recortado) <= tope
    assert recortado.endswith(limites_texto.MARCA_RECORTE)


def test_no_toca_lo_que_entra_en_el_tope():
    corto = "El operador no ofreció la promo vigente."
    assert limites_texto.recortar_valor(corto) == corto
    assert limites_texto.recortar_valor(True) is True
    assert limites_texto.recortar_valor(None) is None


def test_recorta_elemento_por_elemento_en_las_listas():
    tope = limites_texto.max_caracteres()
    valores = ["ok", "b" * (tope + 10)]

    recortado = limites_texto.recortar_valor(valores)

    assert recortado[0] == "ok"
    assert len(recortado[1]) <= tope


def test_tope_cero_desactiva_todo(monkeypatch):
    monkeypatch.setattr(limites_texto.settings, "ATRIBUTO_TEXTO_MAX_CARACTERES", 0, raising=False)
    largo = "a" * 50000

    assert limites_texto.recortar_valor(largo) == largo
    assert limites_texto.instruccion_texto_breve(["feedback"]) == ""
