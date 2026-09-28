"""
Aplicación de la revisión integral (POST /plantillas/{id}/ia/aplicar-revision).

El endpoint recibe SOLO los cambios que el usuario dejó tildados en pantalla y los
guarda uno por uno. Lo que se prueba acá:

- un AtributoID que no es de esta plantilla NO se toca. El alcance por empresa se
  valida sobre la plantilla del path, así que sin este chequeo bastaba con colar un id
  ajeno en el body para editar (o dar de baja) un atributo de otra empresa;
- una operación que falla no se lleva puestas a las demás (son N guardados
  independientes: si el freno de transcripción rebota un atributo, el resto entra);
- el orden de ejecución (cabecera → ediciones → bajas → altas) y que los atributos
  nuevos queden al fondo de la lista.

Test 100% offline: no toca DB, Gemini ni tokens.
Correr: pytest tests/test_plantillas_revision_aplicar.py -m "not tokens"
"""
import json

import pytest

from app.routers import planillas_prompts as router
from app.models import AplicarRevisionRequest


class _Usuario:
    """Lo único que el endpoint necesita del usuario es su legajo, para versionar."""
    usuario = 42
    is_super_admin = True
    permissions = []


class _ManagerFalso:
    """Doble del plantillas_manager: registra llamadas en vez de tocar la BD."""

    def __init__(self, atributos, falla_en=None):
        self._atributos = atributos
        self._falla_en = falla_en or set()
        self.llamadas = []

    def atributos_activos(self, plantilla_id):
        return {k: v for k, v in self._atributos.items() if v.get("activo", True)}

    def atributos_de_plantilla(self, plantilla_id, incluir_inactivos=False):
        if incluir_inactivos:
            return self._atributos
        return self.atributos_activos(plantilla_id)

    def obtener_plantilla(self, plantilla_id):
        return {"id": plantilla_id, "nombre": "Calidad", "descripcion": "la de siempre"}

    def reactivar_atributo(self, atributo_id, plantilla_id):
        self.llamadas.append(("reactivar", atributo_id))
        self._atributos[atributo_id]["activo"] = True

    def modificar_plantilla(self, **kwargs):
        self.llamadas.append(("cabecera", kwargs))

    def modificar_atributo(self, **kwargs):
        if kwargs.get("nombre") in self._falla_en:
            raise ValueError("El atributo pide la transcripción del llamado.")
        self.llamadas.append(("modificar", kwargs))

    def desactivar_atributo(self, atributo_id):
        self.llamadas.append(("eliminar", atributo_id))

    def crear_atributos_plantilla(self, plantilla_id, atributos):
        if atributos[0].get("nombre") in self._falla_en:
            raise ValueError("El atributo pide la transcripción del llamado.")
        self.llamadas.append(("agregar", atributos[0]))


ATRIBUTOS = {
    10: {"nombre": "Saludo inicial", "orden": 0},
    11: {"nombre": "Motivo del contacto", "orden": 1},
}


@pytest.fixture
def aplicar(monkeypatch):
    """Llama al endpoint con el manager, el alcance y el versionado reemplazados."""
    def _correr(payload, falla_en=None):
        manager = _ManagerFalso({k: dict(v) for k, v in ATRIBUTOS.items()}, falla_en)
        monkeypatch.setattr(router, "plantillas_manager_instance", manager)
        monkeypatch.setattr(router, "_exigir_scope", lambda *a, **k: None)
        monkeypatch.setattr(router.versionado, "obtener_o_crear_version", lambda *a, **k: 77)
        resultado = router.ia_aplicar_revision(
            plantilla_id=1, payload=AplicarRevisionRequest(**payload), current_user=_Usuario()
        )
        return resultado, manager
    return _correr


def _attr(**kwargs):
    base = {"nombre": "Saludo inicial", "prompt": "Evaluá el saludo.", "tipo": "critical_audit"}
    base.update(kwargs)
    return base


def test_atributo_de_otra_plantilla_no_se_modifica(aplicar):
    resultado, manager = aplicar({"modificar": [_attr(id=99999, nombre="Ajeno")]})
    assert resultado["aplicados"] == 0
    assert manager.llamadas == []
    assert "no pertenece a esta plantilla" in resultado["errores"][0]["detalle"]


def test_atributo_de_otra_plantilla_no_se_da_de_baja(aplicar):
    resultado, manager = aplicar({"eliminar": [99999]})
    assert resultado["aplicados"] == 0
    assert manager.llamadas == []


def test_versiona_el_estado_previo_para_poder_deshacer(aplicar):
    """Una revisión toca varios atributos de una. Sin esta foto no habría a dónde
    volver: calidad.PlantillaVersiones solo se escribe cuando la plantilla AUDITA."""
    resultado, _ = aplicar({"modificar": [_attr(id=10)]})
    assert resultado["version_previa"] == 77


def test_aplica_cabecera_ediciones_bajas_y_altas_en_ese_orden(aplicar):
    resultado, manager = aplicar({
        "cabecera": {"nombre": "Calidad", "descripcion": "d", "system_prompt": "s", "recordatorio": "r"},
        "modificar": [_attr(id=10, ponderacion=20, restricciones={"enum": ["OK", "NO OK", "N/A"]})],
        "eliminar": [11],
        "agregar": [_attr(nombre="Empatía", tipo="boolean", es_opcional=True)],
    })
    assert (resultado["aplicados"], resultado["total"], resultado["errores"]) == (4, 4, [])
    assert [c[0] for c in manager.llamadas] == ["cabecera", "modificar", "eliminar", "agregar"]

    edicion = dict(manager.llamadas[1][1])
    assert edicion["atributo_id"] == 10 and edicion["ponderacion"] == 20
    assert edicion["restricciones"] == {"enum": ["OK", "NO OK", "N/A"]}

    alta = manager.llamadas[3][1]
    assert alta["es_opcional"] is True
    assert alta["orden"] == 2  # al fondo: los existentes ocupan 0 y 1


def test_un_cambio_rechazado_no_frena_a_los_demas(aplicar):
    """El freno al 'atributo transcripción' rebota un atributo con un 400. Los otros
    cambios que el usuario aceptó no tienen por qué perderse."""
    resultado, manager = aplicar({
        "modificar": [_attr(id=10), _attr(id=11, nombre="Motivo del contacto", tipo="enum")],
        "agregar": [_attr(nombre="Transcripción", tipo="string",
                          prompt="Transcribí el llamado completo.")],
    }, falla_en={"Saludo inicial", "Transcripción"})

    assert resultado["aplicados"] == 1
    assert [c[0] for c in manager.llamadas] == ["modificar"]
    assert manager.llamadas[0][1]["atributo_id"] == 11
    assert {e["ref"] for e in resultado["errores"]} == {"Saludo inicial", "Transcripción"}


def test_sin_cabecera_no_se_toca_la_plantilla(aplicar):
    resultado, manager = aplicar({"modificar": [_attr(id=10)]})
    assert [c[0] for c in manager.llamadas] == ["modificar"]
    assert resultado["total"] == 1


# --------------------------------------------------------------------------- #
# Restaurar una versión (el "deshacer" de una revisión)                        #
# --------------------------------------------------------------------------- #
SNAPSHOT = {
    "plantilla_id": 1,
    "nombre": "Calidad (antes)",
    "system_prompt": "Sos un analista.",
    "recordatorio": "Recordatorio viejo.",
    "atributos": [
        {"atributo_id": 10, "nombre": "Saludo inicial", "tipo": "boolean",
         "prompt": "Prompt viejo del saludo.", "restricciones": None,
         "ponderacion": 0.0, "es_opcional": False},
        # Estaba activo en la versión y hoy está dado de baja: restaurar tiene que
        # revivir ESA fila, no crear otra.
        {"atributo_id": 12, "nombre": "Despedida", "tipo": "enum",
         "prompt": "Prompt viejo de la despedida.",
         "restricciones": '{"enum": ["Sí", "No", "Otros"]}',
         "ponderacion": 0.0, "es_opcional": True},
    ],
}

ATRIBUTOS_HOY = {
    10: {"nombre": "Saludo institucional", "orden": 0, "DarAviso": True,
         "FrasesAviso": "insulto", "activo": True},
    11: {"nombre": "Motivo del contacto", "orden": 1, "DarAviso": False,
         "FrasesAviso": None, "activo": True},   # lo agregó la revisión: se da de baja
    12: {"nombre": "Despedida", "orden": 2, "DarAviso": False,
         "FrasesAviso": None, "activo": False},  # lo eliminó la revisión: revive
}


@pytest.fixture
def restaurar(monkeypatch):
    def _correr(snapshot=SNAPSHOT, plantilla_de_la_version=1):
        manager = _ManagerFalso({k: dict(v) for k, v in ATRIBUTOS_HOY.items()})
        monkeypatch.setattr(router, "plantillas_manager_instance", manager)
        monkeypatch.setattr(router, "_exigir_scope", lambda *a, **k: None)
        monkeypatch.setattr(router.versionado, "obtener_o_crear_version", lambda *a, **k: 88)
        monkeypatch.setattr(router.versionado, "obtener_version", lambda *a, **k: {
            "PlantillaID": plantilla_de_la_version, "Numero": 3, "snapshot": snapshot,
        })
        # La selección de conocimiento se escribe con el engine real: sin esto el test
        # tocaría la base productiva (dev y prod comparten SQL).
        monkeypatch.setattr(
            router.conocimiento_plantilla, "guardar_seleccion",
            lambda engine, plantilla_id, doc_ids, **k: manager.llamadas.append(
                ("conocimiento", {"plantilla_id": plantilla_id, "doc_ids": list(doc_ids), **k})),
        )
        resultado = router.restaurar_version_plantilla(
            plantilla_id=1, version_id=5, current_user=_Usuario()
        )
        return resultado, manager
    return _correr


def test_restaurar_devuelve_los_atributos_a_los_valores_de_la_version(restaurar):
    resultado, manager = restaurar()
    ediciones = {c[1]["atributo_id"]: c[1] for c in manager.llamadas if c[0] == "modificar"}

    assert ediciones[10]["nombre"] == "Saludo inicial"
    assert ediciones[10]["prompt"] == "Prompt viejo del saludo."
    assert ediciones[12]["restricciones"] == {"enum": ["Sí", "No", "Otros"]}
    assert ediciones[12]["es_opcional"] is True
    assert resultado["errores"] == []


def test_restaurar_revive_el_atributo_que_la_revision_habia_eliminado(restaurar):
    """Revive la MISMA fila: si se recreara, se perdería el enlace con las auditorías
    que ya lo respondieron."""
    resultado, manager = restaurar()
    assert ("reactivar", 12) in manager.llamadas
    assert resultado["revividos"] == 1
    assert not any(c[0] == "agregar" for c in manager.llamadas)


def test_restaurar_da_de_baja_lo_que_la_revision_habia_agregado(restaurar):
    resultado, manager = restaurar()
    assert ("eliminar", 11) in manager.llamadas
    assert resultado["dados_de_baja"] == 1


def test_restaurar_no_mueve_el_orden_ni_apaga_las_alertas(restaurar):
    """El snapshot no guarda orden ni alertas por mail (no cambian lo que la IA
    responde), así que se reenvían tal como están hoy para no tocarlos de rebote."""
    _, manager = restaurar()
    ediciones = {c[1]["atributo_id"]: c[1] for c in manager.llamadas if c[0] == "modificar"}
    assert ediciones[10]["orden"] == 0
    assert ediciones[10]["DarAviso"] is True and ediciones[10]["FrasesAviso"] == "insulto"


def test_restaurar_conserva_la_descripcion_actual(restaurar):
    """Tampoco está en el snapshot: si se mandara vacía, restaurar la borraría."""
    _, manager = restaurar()
    cabecera = next(c[1] for c in manager.llamadas if c[0] == "cabecera")
    assert cabecera["descripcion"] == "la de siempre"
    assert cabecera["system_prompt"] == "Sos un analista."


def test_restaurar_versiona_el_estado_previo(restaurar):
    resultado, _ = restaurar()
    assert resultado["version_previa"] == 88


def test_restaurar_una_version_sin_conocimiento_deja_la_plantilla_sin_documentos(restaurar):
    """Un snapshot sin la clave es una versión que no leía documentos: restaurarla
    tiene que dejarla igual, no conservar los de hoy."""
    _, manager = restaurar()
    [llamada] = [c[1] for c in manager.llamadas if c[0] == "conocimiento"]
    assert llamada["plantilla_id"] == 1
    assert llamada["doc_ids"] == []


def test_restaurar_vuelve_a_elegir_los_documentos_de_la_version(restaurar):
    """Incluso uno que hoy esté de baja en su bot (solo_activos=False): la auditoría
    lo va a ignorar igual mientras siga de baja."""
    snapshot = dict(SNAPSHOT, conocimiento=[
        {"doc_id": 88, "titulo": "Régimen Medido", "sha": "aaa"},
        {"doc_id": 101, "titulo": "ODS ZT27", "sha": "bbb"},
    ])
    _, manager = restaurar(snapshot=snapshot)
    [llamada] = [c[1] for c in manager.llamadas if c[0] == "conocimiento"]
    assert llamada["doc_ids"] == [88, 101]
    assert llamada["solo_activos"] is False


def test_no_se_puede_restaurar_una_version_de_otra_plantilla(restaurar):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        restaurar(plantilla_de_la_version=999)
    assert error.value.status_code == 400
