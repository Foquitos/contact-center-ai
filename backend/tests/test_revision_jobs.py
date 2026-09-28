"""
La revisión de plantillas corre en segundo plano (app/revision_jobs.py).

POR QUÉ IMPORTA
---------------
Resolver la revisión dentro de la request HTTP no funciona: encadena las consultas de
evidencia y un llamado a Gemini con razonamiento HIGH sobre la plantilla entera, y el
frontend corre bajo gunicorn, que **mata al worker a los 30 segundos**. Cuando eso pasa el
navegador no recibe un error del sistema sino la página de error del propio gunicorn —un
`Internal Server Error` en HTML que el JS ni siquiera puede parsear como JSON, así que el
editor mostraba un fallo mudo. Es el mismo problema que ya había resuelto la cola del
asistente de documentación.

Y hay un segundo motivo, que se descubrió en producción: el estado NO puede vivir en la
memoria del proceso. La API corre con varios workers, así que el POST creaba el trabajo en
el worker A y el polling caía en el B, que no lo conocía y contestaba `desconocido` —a
veces al primer intento, a veces al tercero—. Por eso ahora cada trabajo es un archivo en
un directorio compartido.

Lo que se prueba: que el POST devuelva al instante con un id, que el estado quede en disco
(y no en un diccionario del proceso), que se pueda seguir, que un error del trabajo llegue
como estado y no como excepción, y que el resultado —que trae los prompts completos de una
plantilla— solo lo vea quien lo pidió.

Test 100% offline: no toca DB, Gemini ni tokens.
Correr: pytest tests/test_revision_jobs.py -m "not tokens"
"""
import json
import os
import time

import pytest
from fastapi import HTTPException

from app import revision_jobs
from app.config import settings
from app.models import RevisarPlantillaRequest, RevisarAtributoRequest
from app.routers import planillas_prompts as router


class _Usuario:
    usuario = 42
    is_super_admin = True
    permissions = []


class _Otro:
    usuario = 99
    is_super_admin = True
    permissions = []


@pytest.fixture(autouse=True)
def directorio_de_trabajos(tmp_path, monkeypatch):
    """Los trabajos se guardan en disco: en los tests, en un directorio descartable."""
    monkeypatch.setattr(settings, "REVISION_JOBS_DIR", str(tmp_path))
    return tmp_path


def _esperar(job_id, timeout=5.0):
    """Espera a que el trabajo deje de estar en curso."""
    limite = time.time() + timeout
    while time.time() < limite:
        job = revision_jobs.obtener(job_id)
        if job and job["estado"] != revision_jobs.ESTADO_EN_CURSO:
            return job
        time.sleep(0.02)
    raise AssertionError("El trabajo no terminó a tiempo.")


# --------------------------------------------------------------------------- #
# El registro en sí                                                            #
# --------------------------------------------------------------------------- #
def test_el_trabajo_corre_aparte_y_deja_el_resultado():
    job_id = revision_jobs.lanzar("test", 1, 7, lambda: {"atributos": []})
    job = _esperar(job_id)
    assert job["estado"] == revision_jobs.ESTADO_LISTO
    assert job["resultado"] == {"atributos": []}
    assert revision_jobs.para_respuesta(job)["segundos"] >= 0


def test_un_error_queda_como_estado_y_no_tumba_nada():
    def _explota():
        raise RuntimeError("Gemini no contestó")
    job = _esperar(revision_jobs.lanzar("test", 1, 7, _explota))
    assert job["estado"] == revision_jobs.ESTADO_ERROR
    assert "Gemini no contestó" in job["detalle"]


def test_el_estado_se_comparte_por_disco_y_no_por_memoria(directorio_de_trabajos):
    """LA regresión: si alguien vuelve a guardarlos en un diccionario del proceso, el
    polling de otro worker deja de encontrarlos y el editor no puede mostrar nada."""
    job_id = revision_jobs.lanzar("test", 1, 7, lambda: {"plan": "listo"})
    _esperar(job_id)

    archivo = os.path.join(str(directorio_de_trabajos), f"{job_id}.json")
    assert os.path.exists(archivo), "El trabajo tiene que quedar en el directorio compartido."
    guardado = json.loads(open(archivo, encoding="utf-8").read())
    assert guardado["estado"] == revision_jobs.ESTADO_LISTO
    assert guardado["resultado"] == {"plan": "listo"}


def test_lee_un_trabajo_que_dejo_otro_worker(directorio_de_trabajos):
    """El caso real: el POST lo atendió otro proceso y el polling cae acá."""
    ajeno = {
        "id": "a" * 32, "tipo": "revisar_plantilla", "estado": revision_jobs.ESTADO_EN_CURSO,
        "usuario_id": 42, "plantilla_id": 3, "creado": time.time(),
        "terminado": None, "resultado": None, "detalle": None,
    }
    ruta = os.path.join(str(directorio_de_trabajos), f"{ajeno['id']}.json")
    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump(ajeno, archivo)

    job = revision_jobs.obtener(ajeno["id"])
    assert job is not None and job["estado"] == revision_jobs.ESTADO_EN_CURSO
    assert revision_jobs.para_respuesta(job)["job_id"] == ajeno["id"]


def test_un_id_desconocido_no_es_un_error():
    """Vencido, otro worker o la API reiniciada: el trabajo se perdió de verdad, y el
    editor tiene que poder decirlo en vez de dejar el spinner girando."""
    assert revision_jobs.obtener("no-existe") is None


def test_los_trabajos_viejos_se_descartan(monkeypatch):
    job_id = revision_jobs.lanzar("test", 1, 7, lambda: True)
    _esperar(job_id)
    monkeypatch.setattr(revision_jobs, "TTL_SEGUNDOS", -1)
    revision_jobs.lanzar("test", 1, 7, lambda: True)  # cualquier alta limpia lo vencido
    assert revision_jobs.obtener(job_id) is None


# --------------------------------------------------------------------------- #
# Los endpoints                                                                #
# --------------------------------------------------------------------------- #
@pytest.fixture
def revisar(monkeypatch):
    """Lanza la revisión con el modelo y la BD reemplazados."""
    def _correr(resultado=None, falla=None, usar_evidencia=True):
        monkeypatch.setattr(router, "_exigir_scope", lambda *a, **k: None)
        monkeypatch.setattr(router, "_plantilla_para_revision", lambda *a, **k: {"atributos": []})
        monkeypatch.setattr(router.evidencia_plantilla, "evidencia_de_plantilla",
                            lambda *a, **k: {"por_atributo": {}})

        def _revisar(*a, **k):
            if falla:
                raise falla
            return resultado or {"diagnostico": "ok", "atributos": []}
        monkeypatch.setattr(router.asistente_plantillas, "revisar_plantilla", _revisar)

        return router.ia_revisar_plantilla(
            plantilla_id=1,
            payload=RevisarPlantillaRequest(usar_evidencia=usar_evidencia),
            current_user=_Usuario(),
        )
    return _correr


def test_el_post_contesta_al_instante_con_el_id(revisar):
    respuesta = revisar()
    assert respuesta["estado"] == revision_jobs.ESTADO_EN_CURSO
    assert respuesta["job_id"]


def test_el_polling_devuelve_el_resultado_cuando_termina(revisar, monkeypatch):
    monkeypatch.setattr(router, "_exigir_scope", lambda *a, **k: None)
    job_id = revisar({"diagnostico": "La plantilla está bien.", "atributos": []})["job_id"]
    _esperar(job_id)
    estado = router.ia_estado_revision(job_id=job_id, current_user=_Usuario())
    assert estado["estado"] == "listo"
    assert estado["resultado"]["diagnostico"] == "La plantilla está bien."


def test_si_la_ia_falla_el_polling_lo_cuenta(revisar, monkeypatch):
    monkeypatch.setattr(router, "_exigir_scope", lambda *a, **k: None)
    job_id = revisar(falla=RuntimeError("La IA devolvió una respuesta vacía."))["job_id"]
    _esperar(job_id)
    estado = router.ia_estado_revision(job_id=job_id, current_user=_Usuario())
    assert estado["estado"] == "error"
    assert "vacía" in estado["detalle"]


def test_el_resultado_solo_lo_ve_quien_lo_pidio(revisar, monkeypatch):
    """Trae los prompts completos de la plantilla: un id ajeno no alcanza para leerlo."""
    monkeypatch.setattr(router, "_exigir_scope", lambda *a, **k: None)
    job_id = revisar()["job_id"]
    _esperar(job_id)
    with pytest.raises(HTTPException) as error:
        router.ia_estado_revision(job_id=job_id, current_user=_Otro())
    assert error.value.status_code == 403


def test_un_job_id_inventado_devuelve_desconocido():
    estado = router.ia_estado_revision(job_id="inventado", current_user=_Usuario())
    assert estado["estado"] == "desconocido"


def test_rehacer_un_cambio_sin_instruccion_se_rechaza_en_el_acto(monkeypatch):
    """Es un error del pedido: tiene que volver como 400 y no como un trabajo que
    arranca para fallar dos minutos después."""
    monkeypatch.setattr(router, "_exigir_scope", lambda *a, **k: None)
    with pytest.raises(HTTPException) as error:
        router.ia_revisar_atributo(
            plantilla_id=1,
            payload=RevisarAtributoRequest(instruccion_usuario="   ", atributo_id=3),
            current_user=_Usuario(),
        )
    assert error.value.status_code == 400
