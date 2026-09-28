"""Trabajos en curso del asistente de plantillas (revisión integral y rehacer un cambio).

POR QUÉ EXISTE
--------------
Una revisión integral encadena las consultas de evidencia y un llamado a Gemini con
razonamiento **HIGH** sobre la plantilla entera: son decenas de segundos, y con una
plantilla grande pasa el minuto.

Eso NO entra en una request HTTP. El frontend Flask corre bajo **gunicorn, que mata al
worker a los 30 segundos**, y cuando eso pasa el navegador no recibe un error del sistema:
recibe la página de error del propio gunicorn —un `Internal Server Error` en HTML— que el
JS ni siquiera puede parsear como JSON. Es el mismo problema que ya había resuelto la cola
del asistente de documentación (ver `app/doc_jobs.py`, que lo documenta igual).

La solución es la misma: la request solo **lanza** el trabajo y responde al instante, y el
navegador consulta el estado por polling.

Lo usa también la comparación del planificador (`GET /planificador/backtest` con
`en_segundo_plano=true`, tipo `planificador_backtest`): con el GBDT del nivel prendido, un
mes de comparación pasa el minuto.

POR QUÉ EN DISCO Y NO EN MEMORIA
---------------------------------
La primera versión guardaba los trabajos en un diccionario del proceso, asumiendo un solo
worker. **Estaba mal**: la API también corre con varios workers, así que el POST creaba el
trabajo en el worker A y el polling caía en el B, que no lo conocía y contestaba
`desconocido` — a veces al primer intento, a veces al tercero, según a quién le tocara.

Ahora el estado vive en un archivo por trabajo, en un directorio compartido por todos los
workers de la máquina. Cada archivo lo escribe **un solo** proceso (el que corre ese
trabajo) y se reemplaza de forma atómica (`os.replace`), así que un lector nunca ve un
archivo a medio escribir. De paso, un trabajo sobrevive al reinicio de un worker.

POR QUÉ NO UNA TABLA, COMO doc_jobs
------------------------------------
`doc_jobs` vive en la BD porque lo encola la API y lo ejecuta el **scheduler**, que es otro
proceso y necesita verlo. Acá el trabajo lo corre el mismo proceso que lo lanzó: lo único
que hay que compartir es el estado, y para eso un archivo alcanza. Además así no hace falta
una migración —el arreglo se puede deployar solo— y no queda basura en la base: es un
resultado que se mira una vez y se descarta.

El límite es que asume que **todos los workers comparten disco**, o sea que corren en la
misma máquina (hoy es así). Si algún día la API se reparte entre servidores, esto pasa a
ser una tabla.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
import uuid
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Un resultado se mira una vez y se tira. Media hora es más que suficiente para que el
# navegador lo levante, incluso si la persona deja la pestaña abierta y vuelve después.
TTL_SEGUNDOS = 30 * 60

ESTADO_EN_CURSO = "en_curso"
ESTADO_LISTO = "listo"
ESTADO_ERROR = "error"

# Serializa el borrado de vencidos dentro de un proceso (entre procesos no hace falta:
# borrar dos veces el mismo archivo vencido es inofensivo).
_lock = threading.Lock()


def _directorio() -> str:
    from app.config import settings
    ruta = getattr(settings, "REVISION_JOBS_DIR", "") or os.path.join(
        tempfile.gettempdir(), "acme_revision_jobs")
    os.makedirs(ruta, exist_ok=True)
    return ruta


def _ruta(job_id: str) -> str:
    # El id lo genera uuid4, pero igual se corta cualquier cosa que no sea hexadecimal:
    # este valor llega por la URL del polling y termina en un nombre de archivo.
    seguro = "".join(c for c in job_id if c.isalnum())[:64]
    return os.path.join(_directorio(), f"{seguro}.json")


def _guardar(job: Dict[str, Any]) -> None:
    """Escritura atómica: se escribe al lado y se reemplaza. Un lector ve el archivo
    entero viejo o el entero nuevo, nunca uno a medias."""
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


def _limpiar() -> None:
    """Borra los trabajos vencidos. Best-effort: no puede impedir lanzar uno nuevo."""
    ahora = time.time()
    try:
        with _lock:
            for nombre in os.listdir(_directorio()):
                if not nombre.endswith(".json"):
                    continue
                ruta = os.path.join(_directorio(), nombre)
                try:
                    if ahora - os.path.getmtime(ruta) > TTL_SEGUNDOS:
                        os.unlink(ruta)
                except OSError:
                    continue
    except Exception as e:  # noqa: BLE001
        logger.debug("No se pudieron limpiar los trabajos viejos: %s", e)


def lanzar(tipo: str, usuario_id: Optional[int], plantilla_id: Optional[int],
           funcion: Callable[[], Any]) -> str:
    """Corre `funcion()` en segundo plano y devuelve el id para consultarla.

    `funcion` no recibe argumentos: se le pasa ya cerrada sobre lo que necesite (un
    `lambda` en el endpoint). Cualquier excepción queda guardada como estado 'error';
    nunca se propaga al hilo principal ni tumba nada.

    Si el trabajo no se puede registrar (permisos, disco), lanza: es preferible fallar
    en el acto y con un motivo a devolver un id que el polling nunca va a encontrar.
    """
    _limpiar()
    job = {
        "id": uuid.uuid4().hex, "tipo": tipo, "estado": ESTADO_EN_CURSO,
        "usuario_id": usuario_id, "plantilla_id": plantilla_id,
        "creado": time.time(), "terminado": None, "resultado": None, "detalle": None,
    }
    try:
        _guardar(job)
    except Exception as e:  # noqa: BLE001
        logger.exception("No se pudo registrar el trabajo en %s", _directorio())
        raise RuntimeError(f"No se pudo iniciar el pedido: {e}")

    hilo = threading.Thread(target=_correr, args=(job["id"], funcion),
                            name=f"revision-{tipo}-{job['id'][:8]}", daemon=True)
    hilo.start()
    logger.info("Trabajo %s lanzado (tipo=%s, plantilla=%s).", job["id"][:8], tipo, plantilla_id)
    return job["id"]


def _correr(job_id: str, funcion: Callable[[], Any]) -> None:
    inicio = time.time()
    try:
        resultado = funcion()
        _terminar(job_id, ESTADO_LISTO, resultado=resultado)
        logger.info("Trabajo %s terminado en %.1fs.", job_id[:8], time.time() - inicio)
    except Exception as e:  # noqa: BLE001 - el hilo no puede dejar escapar nada
        logger.exception("Trabajo %s falló a los %.1fs.", job_id[:8], time.time() - inicio)
        _terminar(job_id, ESTADO_ERROR, detalle=str(e))


def _terminar(job_id: str, estado: str, resultado: Any = None, detalle: Optional[str] = None) -> None:
    job = obtener(job_id)
    if job is None:  # venció mientras corría: el resultado ya no le importa a nadie
        logger.warning("El trabajo %s terminó pero su registro ya no estaba.", job_id[:8])
        return
    job.update({"estado": estado, "resultado": resultado, "detalle": detalle,
                "terminado": time.time()})
    try:
        _guardar(job)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo guardar el resultado del trabajo %s.", job_id[:8])


def obtener(job_id: str) -> Optional[Dict[str, Any]]:
    """Estado del trabajo, o None si no existe (vencido, id inventado, o el archivo se
    perdió). El polling lo traduce a `desconocido`."""
    try:
        with open(_ruta(job_id), encoding="utf-8") as archivo:
            return json.load(archivo)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except (ValueError, OSError) as e:
        logger.warning("No se pudo leer el trabajo %s: %s", job_id[:8], e)
        return None


def para_respuesta(job: Dict[str, Any]) -> Dict[str, Any]:
    """Lo que ve el navegador. `segundos` es lo que se muestra mientras espera."""
    fin = job.get("terminado") or time.time()
    return {
        "job_id": job["id"],
        "estado": job["estado"],
        "segundos": round(fin - job["creado"], 1),
        "resultado": job.get("resultado"),
        "detalle": job.get("detalle"),
    }
