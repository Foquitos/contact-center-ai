"""Guardar una versión de plantilla A MANO ("Guardar en el historial").

El historial se escribe solo cuando la plantilla AUDITA (AuditorIA/versionado.py).
Eso deja un hueco: entre editar una plantilla y la próxima corrida no hay punto de
retorno, y lanzar una auditoría solo para tenerlo cuesta plata. El botón del editor
llena ese hueco, y lo que se prueba acá es que no mienta:

- guardar dos veces seguidas NO crea dos versiones (el alta sigue siendo por hash);
  la segunda vez avisa cuál es la versión que ya estaba;
- si falta la migración, el pedido MANUAL falla con un mensaje (el usuario apretó un
  botón), mientras que el alta automática sigue degradando en silencio: una corrida
  de auditoría no puede caerse por no poder versionar;
- el motivo que escribe el usuario llega a la base recortado a lo que entra en la
  columna, y si no escribe nada queda uno por defecto (si no, en la lista no hay con
  qué distinguir una versión guardada a mano de una que nació auditando).

Test 100% offline: no toca DB, Gemini ni tokens.
Correr: pytest tests/test_plantilla_version_manual.py -m "not tokens"
"""
import json

import pytest
from fastapi import HTTPException

from AuditorIA import versionado
from app.models import GuardarVersionRequest
from app.routers import planillas_prompts as router


# --------------------------------------------------------------------------- #
# Doble de la base                                                             #
# --------------------------------------------------------------------------- #
class _Resultado:
    def __init__(self, filas):
        self._filas = filas

    def first(self):
        return self._filas[0] if self._filas else None

    def scalar(self):
        fila = self.first()
        if fila is None:
            return None
        return fila[0] if isinstance(fila, (tuple, list)) else fila

    def mappings(self):
        return self

    def all(self):
        return self._filas


class _BaseFalsa:
    """Responde las consultas de versionado.py y GUARDA lo insertado.

    Guardar de verdad (y no solo registrar la llamada) es lo que hace que el test de
    idempotencia valga: la segunda pasada encuentra el hash que dejó la primera.
    """

    def __init__(self, *, con_tabla=True, con_plantilla=True):
        self.con_tabla = con_tabla
        self.con_plantilla = con_plantilla
        self.versiones = []
        self.sql = []

    # -- helpers de test
    def hubo(self, fragmento):
        return [(s, p) for s, p in self.sql if fragmento in s]

    # -- interfaz de conexión
    def execute(self, query, params=None):
        sql = " ".join(str(query).split())
        params = params or {}
        self.sql.append((sql, params))

        if "INFORMATION_SCHEMA.TABLES" in sql:
            return _Resultado([(1,)] if self.con_tabla else [])
        if "INFORMATION_SCHEMA.COLUMNS" in sql:
            return _Resultado([(1,)])
        if "sp_getapplock" in sql:
            return _Resultado([])

        if "SELECT TOP 1 VersionID, Numero FROM calidad.PlantillaVersiones" in sql:
            fila = next((v for v in self.versiones if v["hash"] == params["hash"]), None)
            return _Resultado([(fila["version_id"], fila["numero"])] if fila else [])
        if "COALESCE(MAX(Numero), 0) + 1" in sql:
            return _Resultado([(max([v["numero"] for v in self.versiones], default=0) + 1,)])
        if "INSERT INTO calidad.PlantillaVersiones" in sql:
            nueva = dict(params, version_id=500 + len(self.versiones), numero=params["num"])
            self.versiones.append(nueva)
            return _Resultado([(nueva["version_id"],)])

        if "FROM calidad.Plantillas" in sql:
            if not self.con_plantilla:
                return _Resultado([])
            return _Resultado([{
                "PlantillaID": 7, "Nombre": "Calidad Cobranzas",
                "SystemPrompt": "Sos un analista de calidad senior.",
                "Recordatorio": "Si el criterio no aplica, respondé N/A.",
                "ModeloIA": "gemini-3.8-flash",
            }])
        if "FROM calidad.Atributos" in sql:
            return _Resultado([{
                "AtributoID": 1, "NombreAtributo": "Saludo", "TipoDato": "critical_audit",
                "PromptAdyacente": "¿Saludó con el protocolo?",
                "Restricciones": '{"enum":["OK","NO OK"]}', "Ponderacion": 30.0,
                "EsOpcional": False,
            }])
        return _Resultado([])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _EngineFalso:
    def __init__(self, base):
        self.base = base

    def begin(self):
        return self.base

    def connect(self):
        return self.base


@pytest.fixture
def base():
    return _BaseFalsa()


# --------------------------------------------------------------------------- #
# versionado.guardar_version_actual                                            #
# --------------------------------------------------------------------------- #
def test_guarda_el_estado_actual_como_version_nueva(base):
    datos = versionado.guardar_version_actual(
        _EngineFalso(base), 7, user_id=42, motivo="Antes de reescribir el saludo")

    assert (datos["numero"], datos["creada"]) == (1, True)
    insert = base.hubo("INSERT INTO calidad.PlantillaVersiones")
    assert len(insert) == 1
    params = insert[0][1]
    assert params["motivo"] == "Antes de reescribir el saludo"
    assert params["uid"] == 42
    # El snapshot es el estado completo: sin él la versión no se podría restaurar.
    assert json.loads(params["snap"])["atributos"][0]["nombre"] == "Saludo"


def test_guardar_dos_veces_sin_tocar_nada_no_duplica_la_version(base):
    """El botón se puede apretar dos veces (o apretarlo después de auditar). Si cada
    clic creara una versión, el historial dejaría de ser la lista de estados distintos
    y pasaría a ser la lista de clics."""
    engine = _EngineFalso(base)
    primera = versionado.guardar_version_actual(engine, 7, user_id=42, motivo="La primera")
    segunda = versionado.guardar_version_actual(engine, 7, user_id=42, motivo="La segunda")

    assert primera["creada"] is True
    assert segunda["creada"] is False
    assert segunda["numero"] == primera["numero"] == 1
    assert len(base.hubo("INSERT INTO calidad.PlantillaVersiones")) == 1


def test_sin_migracion_el_pedido_manual_avisa_en_vez_de_fingir_que_guardo():
    base = _BaseFalsa(con_tabla=False)
    with pytest.raises(versionado.VersionadoNoDisponible):
        versionado.guardar_version_actual(_EngineFalso(base), 7, user_id=42, motivo="x")


def test_sin_migracion_el_alta_automatica_sigue_sin_romper():
    """La contracara: versionar es trazabilidad y no puede ser el motivo por el que
    una corrida de auditoría se cae."""
    base = _BaseFalsa(con_tabla=False)
    assert versionado.obtener_o_crear_version(_EngineFalso(base), 7) is None


def test_el_alta_automatica_sigue_devolviendo_el_version_id(base):
    """El refactor que compartió el cuerpo con el alta manual no puede cambiar lo que
    ve gemini.py: un VersionID (o None), no un dict."""
    engine = _EngineFalso(base)
    version_id = versionado.obtener_o_crear_version(engine, 7, user_id=42)
    assert isinstance(version_id, int)
    # Y el botón, sobre ese mismo estado, reconoce la versión que dejó la corrida.
    assert versionado.guardar_version_actual(engine, 7, user_id=42, motivo="a mano") == {
        "version_id": version_id, "numero": 1, "creada": False,
    }


def test_plantilla_inexistente_no_crea_version():
    base = _BaseFalsa(con_plantilla=False)
    assert versionado.guardar_version_actual(_EngineFalso(base), 999, user_id=42) is None
    assert base.hubo("INSERT INTO calidad.PlantillaVersiones") == []


# --------------------------------------------------------------------------- #
# El endpoint                                                                  #
# --------------------------------------------------------------------------- #
class _Usuario:
    usuario = 42
    is_super_admin = True
    permissions = []


@pytest.fixture
def llamar(monkeypatch):
    """Llama al endpoint con el alcance por empresa y el versionado reemplazados."""
    registro = {}

    def _correr(motivo=None, resultado=None, explota=None):
        def _fake(engine, plantilla_id, **kwargs):
            registro.update(kwargs, plantilla_id=plantilla_id)
            if explota:
                raise explota
            return resultado

        monkeypatch.setattr(router, "_exigir_scope", lambda *a, **k: None)
        monkeypatch.setattr(router.versionado, "guardar_version_actual", _fake)
        return router.guardar_version_plantilla(
            plantilla_id=7, payload=GuardarVersionRequest(motivo=motivo),
            current_user=_Usuario(),
        )

    _correr.registro = registro
    return _correr


def test_devuelve_si_creo_la_version_o_si_ya_estaba(llamar):
    datos = {"version_id": 500, "numero": 4, "creada": False}
    assert llamar(motivo="a mano", resultado=datos) == datos


def test_sin_motivo_queda_uno_por_defecto(llamar):
    """En la lista de versiones el motivo es la única columna que explica de dónde
    salió la fila: en blanco no se distingue de una versión que nació auditando."""
    llamar(motivo="   ", resultado={"version_id": 1, "numero": 1, "creada": True})
    assert llamar.registro["motivo"] == router.MOTIVO_VERSION_DEFAULT


def test_el_motivo_se_recorta_a_lo_que_entra_en_la_columna(llamar):
    llamar(motivo="x" * 5000, resultado={"version_id": 1, "numero": 1, "creada": True})
    assert llamar.registro["motivo"] == "x" * router.MOTIVO_VERSION_MAX


def test_la_version_queda_a_nombre_de_quien_apreto_el_boton(llamar):
    llamar(motivo="a mano", resultado={"version_id": 1, "numero": 1, "creada": True})
    assert llamar.registro["user_id"] == 42


def test_sin_migracion_responde_409_con_el_motivo_a_la_vista(llamar):
    with pytest.raises(HTTPException) as e:
        llamar(explota=versionado.VersionadoNoDisponible("falta la migración X"))
    assert e.value.status_code == 409
    assert "falta la migración X" in e.value.detail


def test_plantilla_inexistente_responde_404(llamar):
    with pytest.raises(HTTPException) as e:
        llamar(resultado=None)
    assert e.value.status_code == 404
