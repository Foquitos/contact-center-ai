import os
import time
from app import asistente_jobs


def test_crear_y_obtener_trabajo(tmp_path, monkeypatch):
    monkeypatch.setattr(asistente_jobs, "_directorio", lambda: str(tmp_path))

    job_id = asistente_jobs.crear_trabajo(
        user_id="user_test",
        conversacion_id=42,
        pregunta="¿Cuál es el desvío principal?",
    )
    assert job_id is not None
    assert len(job_id) == 32

    job = asistente_jobs.obtener_trabajo(job_id, user_id="user_test")
    assert job is not None
    assert job["id"] == job_id
    assert job["usuario_id"] == "user_test"
    assert job["conversacion_id"] == 42
    assert job["estado"] == asistente_jobs.ESTADO_EN_CURSO
    assert job["fase"] == asistente_jobs.FASE_ENRIQUECIENDO
    assert job["texto"] == ""

    resp = asistente_jobs.para_respuesta(job)
    assert resp["job_id"] == job_id
    assert resp["estado"] == asistente_jobs.ESTADO_EN_CURSO
    assert resp["fase"] == asistente_jobs.FASE_ENRIQUECIENDO
    assert resp["conversacion_id"] == 42
    assert isinstance(resp["segundos"], float)


def test_actualizar_trabajo(tmp_path, monkeypatch):
    monkeypatch.setattr(asistente_jobs, "_directorio", lambda: str(tmp_path))

    job_id = asistente_jobs.crear_trabajo(user_id="u1")
    asistente_jobs.actualizar_trabajo(
        job_id,
        fase=asistente_jobs.FASE_ESCRIBIENDO,
        texto="Texto inicial...",
        fuentes={"transcripciones": 2, "comparativa": True, "filas": 150},
    )

    job = asistente_jobs.obtener_trabajo(job_id)
    assert job["fase"] == asistente_jobs.FASE_ESCRIBIENDO
    assert job["texto"] == "Texto inicial..."
    assert job["fuentes"]["transcripciones"] == 2

    # Completar
    asistente_jobs.actualizar_trabajo(
        job_id,
        estado=asistente_jobs.ESTADO_LISTO,
        texto="Texto final completo",
        terminado=time.time(),
    )
    job_listo = asistente_jobs.obtener_trabajo(job_id)
    assert job_listo["estado"] == asistente_jobs.ESTADO_LISTO
    assert job_listo["texto"] == "Texto final completo"


def test_aislamiento_usuario(tmp_path, monkeypatch):
    monkeypatch.setattr(asistente_jobs, "_directorio", lambda: str(tmp_path))

    job_id = asistente_jobs.crear_trabajo(user_id="user_original")

    # Mismo usuario: ok
    assert asistente_jobs.obtener_trabajo(job_id, user_id="user_original") is not None
    # Usuario diferente: None
    assert asistente_jobs.obtener_trabajo(job_id, user_id="otro_usuario") is None


def test_limpiar_vencidos(tmp_path, monkeypatch):
    monkeypatch.setattr(asistente_jobs, "_directorio", lambda: str(tmp_path))

    job_id = asistente_jobs.crear_trabajo(user_id="u1")
    archivo = asistente_jobs._ruta(job_id)
    assert os.path.exists(archivo)

    # Forzar fecha vieja (modificado hace 3600 segundos)
    hace_una_hora = time.time() - 3600
    os.utime(archivo, (hace_una_hora, hace_una_hora))

    # Limpiar con ttl de 1800s
    asistente_jobs.limpiar_vencidos(ttl_segundos=1800)
    assert not os.path.exists(archivo)
    assert asistente_jobs.obtener_trabajo(job_id) is None


def test_trabajo_inexistente(tmp_path, monkeypatch):
    monkeypatch.setattr(asistente_jobs, "_directorio", lambda: str(tmp_path))
    assert asistente_jobs.obtener_trabajo("inexistente123") is None


def test_router_trabajo_iniciar_y_consultar(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.models import User
    from app.routers import bandeja

    monkeypatch.setattr(asistente_jobs, "_directorio", lambda: str(tmp_path))

    test_app = FastAPI()
    test_app.include_router(bandeja.router)

    usuario_mock = User(usuario=12345678, rol="supervisor", permissions=["bandeja.view"])
    test_app.dependency_overrides[bandeja.require_view] = lambda: usuario_mock

    # Mock de background task y DB de conversaciones
    async def _dummy_ejecutar(*a, **kw):
        pass

    monkeypatch.setattr(bandeja.asistente_dashboard, "ejecutar_trabajo_asistente", _dummy_ejecutar)
    monkeypatch.setattr(bandeja.asistente_conversaciones, "crear", lambda **kw: 101)
    monkeypatch.setattr(bandeja.asistente_conversaciones, "pertenece", lambda uid, cid: True)
    monkeypatch.setattr(bandeja.asistente_conversaciones, "agregar_mensaje", lambda *a, **kw: None)
    monkeypatch.setattr(bandeja.asistente_conversaciones, "historial_para_modelo", lambda uid, cid: [])

    client = TestClient(test_app)
    payload = {
        "pregunta": "¿Cómo vienen los errores críticos?",
        "conversacion_id": 101,
        "contexto": {
            "filas": [{"IdAplicativo": "123", "Operador": "Juan Perez", "Puntaje": 80.0}],
            "kpis": {"total": 1},
        },
    }
    res_post = client.post("/bandeja/asistente/trabajo", json=payload)
    assert res_post.status_code == 202
    datos = res_post.json()
    assert "job_id" in datos
    assert datos["estado"] == "en_curso"

    job_id = datos["job_id"]
    res_get = client.get(f"/bandeja/asistente/trabajo/{job_id}")
    assert res_get.status_code == 200
    datos_get = res_get.json()
    assert datos_get["job_id"] == job_id
    assert datos_get["estado"] == "en_curso"

    # 404 para trabajo desconocido
    res_404 = client.get("/bandeja/asistente/trabajo/noexiste999")
    assert res_404.status_code == 404


