"""
Conocimiento de referencia de las plantillas (documentos de los chatbots que la IA lee
antes de auditar). Ver AuditorIA/conocimiento_plantilla.py.

Lo que estos tests fijan:
- Una plantilla SIN documentos audita exactamente igual que antes: misma instrucción de
  sistema (misma caché) y mismo hash de versión (no nace una versión nueva de cada
  plantilla en la próxima corrida).
- Con documentos, el bloque llega a la instrucción de sistema, sin imágenes y con las
  reglas de uso.
- Cambiar los documentos o su contenido se ve en el diff de versiones.
- Sin la migración, nada se rompe.

Offline salvo la validación de sintaxis de la migración (PARSEONLY, no ejecuta nada).
Correr: pytest tests/test_conocimiento_plantilla.py -m "not tokens"
"""
import importlib.util
import json
import os

import pytest

from AuditorIA import conocimiento_plantilla as cp
from AuditorIA import gemini
from AuditorIA.versionado import diff_snapshots, hash_snapshot
from app.models import PlantillasIA

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRACION = os.path.join(REPO_ROOT, "scripts", "migrations", "2026-09-21c_plantilla_conocimiento.sql")
SCRIPT = os.path.join(REPO_ROOT, "scripts", "probar_conocimiento_plantilla.py")

DOCS = [
    {"doc_id": 88, "titulo": "Régimen Medido: fórmulas",
     "contenido_md": "# Régimen Medido\n\n![Portada](/chatbots/imagenes/198)\n\n\n\nEl cargo fijo es X."},
    {"doc_id": 101, "titulo": 'ODS "ZT27"', "contenido_md": "Inspección de medidor por alto consumo."},
]


# --------------------------------------------------------------------------- #
# El bloque                                                                    #
# --------------------------------------------------------------------------- #
def test_sin_documentos_no_hay_bloque():
    assert cp.bloque_para_el_prompt([]) == ""


def test_el_bloque_lleva_reglas_titulos_y_contenido_sin_imagenes():
    bloque = cp.bloque_para_el_prompt(DOCS)
    assert bloque.startswith(cp.INSTRUCCION_CONOCIMIENTO)
    assert bloque.endswith(cp.CIERRE_CONOCIMIENTO)
    assert '<documento titulo="Régimen Medido: fórmulas">' in bloque
    assert "El cargo fijo es X." in bloque
    # La imagen no la ve el auditor: la ruta son tokens tirados.
    assert "/chatbots/imagenes/" not in bloque
    assert "\n\n\n" not in cp.limpiar_markdown(DOCS[0]["contenido_md"])
    # Una comilla en el título no puede romper el atributo del tag.
    assert "<documento titulo=\"ODS 'ZT27'\">" in bloque


def test_las_reglas_protegen_lo_que_no_se_escucha():
    """La regla que evita llenar de NO OK falsos: los pasos en sistemas no se oyen."""
    assert "no penalices al operador por no mencionarlos" in cp.INSTRUCCION_CONOCIMIENTO
    assert "mandan sobre este conocimiento" in cp.INSTRUCCION_CONOCIMIENTO


def test_titulo_vacio_cae_al_id():
    bloque = cp.bloque_para_el_prompt([{"doc_id": 5, "titulo": None, "contenido_md": "x"}])
    assert '<documento titulo="Documento 5">' in bloque


# --------------------------------------------------------------------------- #
# Instrucción de sistema y prompt()                                            #
# --------------------------------------------------------------------------- #
def test_sin_conocimiento_la_instruccion_no_cambia():
    """Misma instrucción = misma caché de contexto: las plantillas que no eligen
    documentos no pueden pagar una caché nueva por este cambio."""
    base = {"system": "Sos un auditor."}
    assert gemini.instruccion_de_sistema({**base, "conocimiento": ""}) == gemini.instruccion_de_sistema(base)
    assert gemini.instruccion_de_sistema(base).endswith("con el esquema JSON definido.")


def test_con_conocimiento_va_al_final_de_la_instruccion():
    bloque = cp.bloque_para_el_prompt(DOCS)
    instruccion = gemini.instruccion_de_sistema({"system": "Sos un auditor.", "conocimiento": bloque})
    assert instruccion.startswith("Sos un auditor.")
    assert instruccion.endswith(bloque)


ATRIBUTOS = [{"name": "Conocimiento del producto", "type": "critical_audit",
              "constraints": {"enum": ["OK", "NO OK", "EC"]}, "id": 1}]


@pytest.fixture
def plantilla_en_memoria(monkeypatch):
    monkeypatch.setattr(
        gemini.plantillas_manager_instance, "obtener_plantilla_para_IA",
        lambda plantilla_id: PlantillasIA(
            system_prompts="Sos un auditor.", text="Auditá.",
            response_schema=json.dumps(ATRIBUTOS), modelo="gemini-fake", nivel_razonamiento=None,
        ),
    )
    monkeypatch.setattr(gemini, "obtener_o_crear_version", lambda engine, pid: 99)


def test_prompt_trae_los_documentos_de_la_plantilla(plantilla_en_memoria, monkeypatch):
    pedidos = []
    monkeypatch.setattr(cp, "documentos_para_auditar",
                        lambda engine, pid: pedidos.append(pid) or DOCS)
    info = gemini.prompt(21)
    assert pedidos == [21]
    assert info["conocimiento"] == cp.bloque_para_el_prompt(DOCS)
    assert info["version_id"] == 99


def test_prompt_sin_documentos_deja_el_conocimiento_vacio(plantilla_en_memoria, monkeypatch):
    monkeypatch.setattr(cp, "documentos_para_auditar", lambda engine, pid: [])
    assert gemini.prompt(21)["conocimiento"] == ""


def test_prompt_sin_versionar_no_registra_version(plantilla_en_memoria, monkeypatch):
    """El script de prueba no escribe en la base: tampoco una versión."""
    monkeypatch.setattr(cp, "documentos_para_auditar", lambda engine, pid: [])

    def _no(*a, **k):
        raise AssertionError("no debía versionar")

    monkeypatch.setattr(gemini, "obtener_o_crear_version", _no)
    assert gemini.prompt(21, versionar=False)["version_id"] is None


# --------------------------------------------------------------------------- #
# Sin migración                                                                #
# --------------------------------------------------------------------------- #
class _ConexionSinTabla:
    """Conexión cuyo chequeo de tabla falla: como una base sin la migración."""

    def execute(self, *a, **k):
        raise RuntimeError("Invalid object name 'calidad.PlantillaConocimiento'")


def test_sin_migracion_la_huella_es_vacia():
    assert cp.huella(_ConexionSinTabla(), 21) == []


def test_sin_migracion_duplicar_no_hace_nada():
    cp.copiar_seleccion(_ConexionSinTabla(), 21, 22)  # no levanta


# --------------------------------------------------------------------------- #
# Versionado                                                                   #
# --------------------------------------------------------------------------- #
def _snapshot(**cambios):
    base = {
        "plantilla_id": 21, "nombre": "HIDRA Comercial", "system_prompt": "Sos un analista.",
        "recordatorio": "", "modelo": None,
        "atributos": [{"atributo_id": 1, "nombre": "Conocimiento del producto",
                       "tipo": "critical_audit", "prompt": "¿Informa bien?", "restricciones": None,
                       "ponderacion": 10.0, "es_opcional": False}],
    }
    base.update(cambios)
    return base


def _huella(doc_id, titulo, sha):
    return {"doc_id": doc_id, "titulo": titulo, "sha": sha}


def test_elegir_documentos_cambia_el_hash_y_se_ve_en_el_diff():
    con = _snapshot(conocimiento=[_huella(88, "Régimen Medido", "aaa")])
    assert hash_snapshot(con) != hash_snapshot(_snapshot())
    diff = diff_snapshots(_snapshot(), con)
    assert diff["hay_cambios"]
    [cambio] = diff["cabecera"]
    assert cambio == {"campo": "conocimiento", "antes": None, "despues": "Régimen Medido"}
    # Cambió lo que la IA lee, no el criterio de un atributo puntual.
    assert diff["atributos_afectados"] == []


def test_editar_un_documento_se_marca_como_editado():
    antes = _snapshot(conocimiento=[_huella(88, "Régimen Medido", "aaa"), _huella(90, "Tarifas", "ccc")])
    despues = _snapshot(conocimiento=[_huella(88, "Régimen Medido", "bbb"), _huella(90, "Tarifas", "ccc")])
    [cambio] = diff_snapshots(antes, despues)["cabecera"]
    assert cambio["antes"] == "Régimen Medido (contenido editado)\nTarifas"
    assert cambio["despues"] == "Régimen Medido (contenido editado)\nTarifas"


def test_mismo_conocimiento_no_es_cambio():
    snap = _snapshot(conocimiento=[_huella(88, "Régimen Medido", "aaa")])
    assert diff_snapshots(snap, snap)["hay_cambios"] is False


# --------------------------------------------------------------------------- #
# Script de prueba (comparación pura)                                          #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("probar_conocimiento_plantilla", SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_normalizar_iguala_mayusculas_y_bool(script):
    assert script.normalizar("No Ok") == script.normalizar("NO OK")
    assert script.normalizar(True) == script.normalizar("True")
    assert script.normalizar(None) == script.normalizar(float("nan")) == ""


def test_resumir_cambios_cuenta_direcciones(script):
    resumen = script.resumir_cambios([("OK", "NO OK"), ("Ok", "OK"), ("OK", "NO OK"), ("NO OK", "OK")])
    assert resumen["cambian"] == 3
    assert resumen["total"] == 4
    assert resumen["direcciones"] == {"OK → NO OK": 2, "NO OK → OK": 1}


# --------------------------------------------------------------------------- #
# Migración (sintaxis, sin ejecutar)                                           #
# --------------------------------------------------------------------------- #
def _batches_de_la_migracion():
    if not os.path.exists(MIGRACION):
        return []
    batches, actual = [], []
    with open(MIGRACION, encoding="utf-8") as f:
        for linea in f.read().splitlines():
            if linea.strip().upper() == "GO":
                if "\n".join(actual).strip():
                    batches.append("\n".join(actual))
                actual = []
            else:
                actual.append(linea)
    return [(f"batch{i}", b) for i, b in enumerate(batches)
            if not b.strip().upper().startswith("USE ")]


BATCHES = _batches_de_la_migracion()


@pytest.mark.skipif(not BATCHES, reason="scripts/migrations no viaja por git: solo en SRV00")
@pytest.mark.parametrize("indice,batch", BATCHES, ids=[i for i, _ in BATCHES])
def test_sintaxis_de_la_migracion(engine, indice, batch):
    from conftest import _validar_parseonly

    resultado = _validar_parseonly(engine, batch)
    assert resultado.ok, f"Error de sintaxis en el {indice}: {resultado.error}"


@pytest.mark.skipif(not BATCHES, reason="scripts/migrations no viaja por git: solo en SRV00")
def test_la_migracion_es_idempotente_y_la_pk_no_acepta_nulos():
    with open(MIGRACION, encoding="utf-8") as f:
        sql = f.read()
    assert "IF OBJECT_ID('calidad.PlantillaConocimiento', 'U') IS NULL" in sql
    assert "PlantillaID        INT       NOT NULL" in sql
    assert "DocID              INT       NOT NULL" in sql
