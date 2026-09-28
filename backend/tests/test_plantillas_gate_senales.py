"""
Gate de señales al guardar un atributo + señales de redacción del prompt.

POR QUÉ EXISTE ESTE GATE
------------------------
El asistente para mejorar plantillas se usó 24 veces en 7 semanas (5 personas de 39 con
permiso para editar plantillas): lo que hay que apretar a propósito no se usa. Lo que sí
funciona es lo que está en el camino del guardado, como el freno al "atributo
transcripción". Así que las señales que hasta ahora vivían en un modal ahora frenan el
guardado del atributo que las introduce.

LAS DOS REGLAS QUE HACEN QUE EL GATE NO MOLESTE
-----------------------------------------------
1. Solo frena la severidad ALTA (una instrucción rota), nunca las medias.
2. Solo frena lo que ESE guardado introduce: un atributo que ya venía con problemas se
   sigue pudiendo editar y reordenar. Sin esto, las plantillas viejas quedarían presas.

Test 100% offline: no toca DB, Gemini ni tokens.
Correr: pytest tests/test_plantillas_gate_senales.py -m "not tokens"
"""
import pytest

from AuditorIA import senales_prompt
from AuditorIA.Plantillas_prompts import plantillas_manager


def _claves(nombre, prompt, tipo="boolean"):
    return {c for c, _m, _s in senales_prompt.senales_de_texto(nombre, prompt, tipo)}


# --------------------------------------------------------------------------- #
# Señales de redacción                                                         #
# --------------------------------------------------------------------------- #
# Casos calcados de los errores que aparecen en las plantillas reales: fechas metidas en
# el prompt, plantillas duplicadas a las que les quedó un placeholder, y criterios que
# no dicen qué evaluar.
MAL_ESCRITOS = [
    ("prompt_con_fecha", "Promo",
     "Verificá si el operador ofreció la promoción vigente hasta el 31/12/2026 al cliente."),
    ("prompt_con_fecha", "Reclamos",
     "Analizá los llamados de agosto de 2026 y determiná si el operador siguió el protocolo."),
    ("prompt_incompleto", "Campaña",
     "Evaluá si el operador mencionó la campaña <nombre de la campaña> durante el llamado."),
    ("prompt_incompleto", "Nota",
     "Poné XXX si no aplica y evaluá el desempeño general del operador en la interacción."),
    ("prompt_repite_nombre", "Amabilidad", "Amabilidad"),
    ("prompt_sin_criterio", "Cierre", "El cierre del llamado."),
    ("multiples_criterios", "Protocolo",
     "¿Saludó correctamente? ¿Se identificó con nombre? ¿Ofreció ayuda antes de cerrar?"),
    ("pide_dato_externo", "Gestión",
     "Verificá en el CRM si el operador cargó la gestión correspondiente al reclamo."),
]

# La otra mitad del test, y la que importa: un patrón que marca prompts buenos enseña al
# analista que el semáforo miente, y a partir de ahí no lo mira más.
BIEN_ESCRITOS = [
    ("Saludo", "¿El operador saludó al cliente identificándose con nombre y empresa dentro "
               "de los primeros 15 segundos del llamado?"),
    ("Empatía", "El operador debe mostrar empatía frente al reclamo del cliente, usando "
                "frases de contención y sin interrumpirlo."),
    ("Feedback", "Escribí un feedback breve para el operador sobre su desempeño, "
                 "destacando lo positivo y lo mejorable."),
    ("Tipo de gestión", "Clasificá el motivo del llamado según el listado de "
                        "tipificaciones disponibles para la campaña."),
    # Mencionar un mes como parte del caso NO es fijar un período de evaluación.
    ("Factura", "Indicá si el cliente hace referencia a una factura de enero que todavía "
                "no fue acreditada en su cuenta corriente."),
]


@pytest.mark.parametrize("clave,nombre,prompt", MAL_ESCRITOS)
def test_detecta_los_errores_de_redaccion(clave, nombre, prompt):
    assert clave in _claves(nombre, prompt)


@pytest.mark.parametrize("nombre,prompt", BIEN_ESCRITOS)
def test_no_marca_los_prompts_bien_escritos(nombre, prompt):
    assert _claves(nombre, prompt) == set()


def test_un_placeholder_es_alta_y_una_fecha_no():
    """La severidad decide qué frena un guardado: un prompt a medio escribir es una
    instrucción rota; una fecha fija mide mal, pero mide."""
    severidades = {c: s for c, _m, s in senales_prompt.senales_de_texto(
        "Campaña", "Evaluá si mencionó la campaña <nombre> vigente hasta el 31/12/2026.", "boolean")}
    assert severidades["prompt_incompleto"] == senales_prompt.SEVERIDAD_ALTA
    assert severidades["prompt_con_fecha"] == senales_prompt.SEVERIDAD_MEDIA


def test_se_pueden_apagar_por_config(monkeypatch):
    monkeypatch.setattr(senales_prompt.settings, "PLANTILLA_SENALES_TEXTO", False, raising=False)
    assert senales_prompt.senales_de_texto("Amabilidad", "Amabilidad", "string") == []


# --------------------------------------------------------------------------- #
# Gate del guardado                                                            #
# --------------------------------------------------------------------------- #
GUARDADO = {
    "PlantillaID": 7, "NombreAtributo": "Motivo", "PromptAdyacente": "Clasificá el motivo del contacto.",
    "TipoDato": "enum", "Restricciones": '{"enum": ["A", "B"]}', "Orden": 0,
    "DarAviso": 0, "FrasesAviso": None, "Ponderacion": 0, "EsOpcional": 0,
}


class _Fila(tuple):
    """Fila indexable como las de SQLAlchemy."""


class _FakeResult:
    def __init__(self, filas):
        self._filas = filas

    def fetchall(self):
        return self._filas

    def fetchone(self):
        return self._filas[0] if self._filas else None

    def first(self):
        return self.fetchone()


class _FakeConnection:
    """Responde las tres lecturas del gate y registra lo que se ejecutó."""

    def __init__(self, hermanos=(), guardado=GUARDADO):
        self.hermanos = list(hermanos)
        self.guardado = guardado
        self.ejecutados = []

    def execute(self, statement, params=None):
        sql = " ".join(str(statement).split())
        self.ejecutados.append((sql, params or {}))
        if "INFORMATION_SCHEMA.COLUMNS" in sql:
            return _FakeResult([(1,)])
        if "SELECT AtributoID, NombreAtributo" in sql:
            return _FakeResult([_Fila(h) for h in self.hermanos])
        if "SELECT NombreAtributo, PromptAdyacente, TipoDato" in sql:
            g = self.guardado
            return _FakeResult([_Fila((g["NombreAtributo"], g["PromptAdyacente"], g["TipoDato"]))])
        if "SELECT PlantillaID, NombreAtributo" in sql:
            g = self.guardado
            return _FakeResult([_Fila((
                g["PlantillaID"], g["NombreAtributo"], g["PromptAdyacente"], g["TipoDato"],
                g["Restricciones"], g["Orden"], g["DarAviso"], g["FrasesAviso"],
                g["Ponderacion"], g["EsOpcional"],
            ))])
        return _FakeResult([])

    @property
    def escrituras(self):
        return [sql for sql, _ in self.ejecutados
                if sql.upper().startswith("EXEC") or sql.upper().startswith("UPDATE")]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self, connection):
        self._connection = connection

    def begin(self):
        return self._connection


def _manager(conn):
    return plantillas_manager(_FakeEngine(conn))


BUENO = {"nombre": "Saludo", "tipo": "boolean", "orden": 0, "es_opcional": True,
         "prompt": "¿El operador saludó identificándose con nombre y empresa al atender?"}
ROTO = {"nombre": "Motivo", "tipo": "enum", "orden": 1, "restricciones": {"enum": ["Reclamo", "Consulta"]},
        "prompt": "Clasificá el motivo del contacto según lo que plantea el cliente en el llamado."}


def test_el_alta_frena_el_atributo_que_introduce_un_problema_alto():
    """La lista sin salida segura obliga a la IA a elegir una opción aunque el caso real
    no esté: es el error que más ensucia las mediciones."""
    conn = _FakeConnection()
    with pytest.raises(senales_prompt.SenalesAltasError) as excinfo:
        _manager(conn).crear_atributos_plantilla(7, [BUENO, ROTO])

    assert [s["clave"] for s in excinfo.value.senales] == ["enum_sin_salida_segura"]
    # Y no queda media tanda creada: el gate corre sobre TODOS antes de escribir el primero.
    assert conn.escrituras == []


def test_el_alta_deja_pasar_los_atributos_sanos():
    conn = _FakeConnection()
    _manager(conn).crear_atributos_plantilla(7, [BUENO])
    assert conn.escrituras


def test_forzar_guarda_igual(caplog):
    """El editor ofrece 'guardar igual' con la lista a la vista. Queda logueado: el
    problema sigue en la plantilla y va a reaparecer en el semáforo y en el reporte."""
    conn = _FakeConnection()
    with caplog.at_level("WARNING"):
        _manager(conn).crear_atributos_plantilla(7, [ROTO], forzar=True, usuario="Ana (123)")

    assert conn.escrituras
    assert "Gate de plantillas salteado" in caplog.text and "Ana (123)" in caplog.text


def test_el_alta_frena_dos_atributos_con_el_mismo_nombre_en_la_misma_tanda():
    """Dos homónimos colapsan en la misma clave del JSON de la IA: uno queda sin auditar."""
    conn = _FakeConnection()
    with pytest.raises(senales_prompt.SenalesAltasError) as excinfo:
        _manager(conn).crear_atributos_plantilla(7, [BUENO, dict(BUENO, orden=1)])
    assert excinfo.value.senales[0]["clave"] == "nombre_duplicado"


def test_la_edicion_no_frena_por_un_problema_que_ya_estaba():
    """El atributo guardado ya es un enum sin salida segura. Cambiarle el prompt (o
    reordenarlo, que entra por el mismo PUT) no puede quedar preso de eso."""
    conn = _FakeConnection()
    _manager(conn).modificar_atributo(
        atributo_id=55,
        prompt="Clasificá el motivo del contacto según lo que plantea el cliente al llamar.",
    )
    assert conn.escrituras


def test_la_edicion_frena_lo_que_ella_introduce():
    """El atributo guardado es un enum sano; la edición le saca la salida segura."""
    conn = _FakeConnection(guardado=dict(GUARDADO, Restricciones='{"enum": ["A", "B", "Otros"]}'))
    with pytest.raises(senales_prompt.SenalesAltasError) as excinfo:
        _manager(conn).modificar_atributo(atributo_id=55, restricciones={"enum": ["A", "B"]})

    assert excinfo.value.senales[0]["clave"] == "enum_sin_salida_segura"
    assert conn.escrituras == []


def test_el_gate_se_puede_bajar_sin_apagar_el_diagnostico(monkeypatch):
    """Perilla para el día que el gate trabe más de lo que ayude: las señales se siguen
    calculando (semáforo, salud, reporte), pero no frenan a nadie."""
    monkeypatch.setattr(senales_prompt.settings, "PLANTILLA_BLOQUEAR_SENALES_ALTAS", False, raising=False)
    conn = _FakeConnection()
    _manager(conn).crear_atributos_plantilla(7, [ROTO])
    assert conn.escrituras
