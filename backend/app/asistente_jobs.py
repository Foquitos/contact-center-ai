"""Trabajos en curso del Asistente Analítico del Dashboard (Bandeja).

POR QUÉ EXISTE
--------------
Una consulta analítica compleja sobre el dashboard involucra enriquecimiento SQL
(búsqueda de transcripciones completas vía `calidad.sp_ObtenerAuditoriasFiltradas` y
cálculo de comparativa del período anterior) y un llamado a Gemini 3.8 Flash con
razonamiento HIGH sobre un contexto voluminoso (~50k tokens): son más de 30 segundos
antes de emitir el primer token de texto.

En producción, el frontend Flask corre bajo Gunicorn con workers sincrónicos y timeout
de 30s. Si la request mantiene la conexión abierta sin bytes, Gunicorn mata al worker
con WORKER TIMEOUT y el usuario ve "Error al comunicarse con el analista."

La solución es un patrón asíncrono de jobs con polling incremental:
- El POST inicial valida, crea el trabajo en disco y responde 202 Accepted en < 150ms.
- El trabajo corre en background, acumulando el texto a medida que llegan los chunks.
- El frontend consulta periódicamente (polling cada 1s) para obtener el texto
  acumulado y el estado/fase actual, logrando sensación de streaming interactivo sin
  mantener una request HTTP abierta que amenace los 30s de Gunicorn.

POR QUÉ EN DISCO Y NO EN BD O MEMORIA
-------------------------------------
Igual que `revision_jobs.py`:
- Los workers de la API pueden ser varios: en memoria no se comparte entre workers.
- En disco compartido (`backend/storage/asistente_jobs/`), la escritura es atómica
  (`mkstemp` + `os.replace`), rápida y no requiere migraciones DDL ni ensucia la base.
- Se limpian automáticamente los archivos con TTL de 30 minutos.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

TTL_SEGUNDOS = 30 * 60

ESTADO_EN_CURSO = "en_curso"
ESTADO_LISTO = "listo"
ESTADO_ERROR = "error"

FASE_ENRIQUECIENDO = "enriqueciendo"
FASE_PENSANDO = "pensando"
FASE_ESCRIBIENDO = "escribiendo"

_lock = threading.Lock()


def _directorio() -> str:
    from app.config import settings

    ruta = getattr(settings, "ASISTENTE_JOBS_DIR", "") or os.path.join(
        tempfile.gettempdir(), "acme_asistente_jobs"
    )
    os.makedirs(ruta, exist_ok=True)
    return ruta


def _ruta(job_id: str) -> str:
    seguro = "".join(c for c in job_id if c.isalnum())[:64]
    return os.path.join(_directorio(), f"{seguro}.json")


def _guardar(job: Dict[str, Any]) -> None:
    destino = _ruta(job["id"])
    fd, temporal = tempfile.mkstemp(dir=os.path.dirname(destino), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as archivo:
            json.dump(job, archivo, ensure_ascii=False, default=str)
        os.replace(temporal, destino)
    except Exception:
        try:
            os.unlink(temporal)
        except OSError:
            pass
        raise


def limpiar_vencidos(ttl_segundos: int = TTL_SEGUNDOS) -> None:
    """Borra los trabajos vencidos. Best-effort."""
    ahora = time.time()
    try:
        with _lock:
            for nombre in os.listdir(_directorio()):
                if not nombre.endswith(".json"):
                    continue
                ruta = os.path.join(_directorio(), nombre)
                try:
                    if ahora - os.path.getmtime(ruta) > ttl_segundos:
                        os.unlink(ruta)
                except OSError:
                    continue
    except Exception as e:
        logger.debug("No se pudieron limpiar los trabajos viejos de asistente: %s", e)


def crear_trabajo(
    user_id: str,
    conversacion_id: Optional[int] = None,
    pregunta: str = "",
) -> str:
    """Crea el registro de un nuevo trabajo del asistente en disco y devuelve su job_id."""
    limpiar_vencidos()
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "usuario_id": str(user_id),
        "conversacion_id": conversacion_id,
        "pregunta": pregunta[:300],
        "estado": ESTADO_EN_CURSO,
        "fase": FASE_ENRIQUECIENDO,
        "texto": "",
        "fuentes": None,
        "error": None,
        "creado": time.time(),
        "actualizado": time.time(),
        "terminado": None,
    }
    try:
        _guardar(job)
    except Exception as e:
        logger.exception("No se pudo registrar el trabajo de asistente en %s", _directorio())
        raise RuntimeError(f"No se pudo iniciar el pedido: {e}")

    logger.info("Trabajo de asistente %s creado (usuario=%s, conv_id=%s).", job_id[:8], user_id, conversacion_id)
    return job_id


def actualizar_trabajo(job_id: str, **kwargs: Any) -> None:
    """Actualiza de forma atómica uno o más campos de un trabajo existente."""
    job = obtener_trabajo(job_id)
    if job is None:
        logger.warning("Intento de actualizar trabajo de asistente inexistente o vencido: %s", job_id[:8])
        return
    job.update(kwargs)
    job["actualizado"] = time.time()
    try:
        _guardar(job)
    except Exception:
        logger.exception("No se pudo actualizar el trabajo de asistente %s.", job_id[:8])


def obtener_trabajo(job_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Lee el estado del trabajo, o None si no existe o pertenece a otro usuario."""
    try:
        with open(_ruta(job_id), encoding="utf-8") as archivo:
            datos = json.load(archivo)
            if user_id is not None and str(datos.get("usuario_id")) != str(user_id):
                return None
            return datos
    except (FileNotFoundError, NotADirectoryError):
        return None
    except (ValueError, OSError) as e:
        logger.warning("No se pudo leer el trabajo de asistente %s: %s", job_id[:8], e)
        return None


def para_respuesta(job: Dict[str, Any]) -> Dict[str, Any]:
    """Lo que ve el frontend durante el polling."""
    fin = job.get("terminado") or time.time()
    return {
        "job_id": job["id"],
        "estado": job["estado"],
        "fase": job.get("fase"),
        "texto": job.get("texto", ""),
        "fuentes": job.get("fuentes"),
        "conversacion_id": job.get("conversacion_id"),
        "segundos": round(fin - job["creado"], 1),
        "error": job.get("error"),
    }

