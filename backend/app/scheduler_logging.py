"""Trazabilidad de las corridas del scheduler dentro del journal.

POR QUÉ EXISTE
--------------
El scheduler corre ~8 jobs con periodicidades distintas (uno cada 10 segundos)
sobre un pool de 20 hilos, y todos escriben al MISMO stdout que systemd manda al
journal. Cuando una auditoría programada falla, `journalctl -u <unidad>` mezcla
sus líneas con las de las otras cuatro que corrían en paralelo más las de los
ticks de las colas: reconstruir a ojo qué línea era de qué corrida es imposible.

QUÉ RESUELVE
------------
1. Estampa CADA línea con el job y un id corto de corrida:

       10:31:04 - [chatbot_doc_jobs_queue/7f3a] app.doc_jobs - INFO - ...

   `journalctl -u <unidad> | grep 7f3a` devuelve una sola corrida completa, y
   `grep chatbot_doc_jobs_queue` todas las de ese job.

2. Loguea un ERROR con el job, la duración y el traceback cuando una corrida
   falla, en vez de dejar que APScheduler tire el traceback pelado sin contexto.

3. Traduce el nivel de Python a prioridad de syslog (ver logging_config), así
   `journalctl -u <unidad> -p warning` filtra de verdad por severidad.

Los ticks que salen bien se loguean en DEBUG a propósito: con uno cada 10
segundos, un INFO por corrida sería justamente el ruido que este módulo trata
de sacar del medio.
"""
import contextlib
import contextvars
import functools
import logging
import time
import uuid
from typing import Optional

logger = logging.getLogger("SchedulerWorker")

# Cada corrida escribe acá su etiqueta y el filtro de logging_config la copia a
# todas las líneas que emita ese hilo mientras dure. Es un ContextVar y no un
# atributo de hilo porque los hilos del pool se reciclan entre jobs distintos:
# el reset del `finally` garantiza que la etiqueta no se filtre a la corrida
# siguiente que le toque a ese mismo hilo.
_ETIQUETA: contextvars.ContextVar[str] = contextvars.ContextVar(
    "scheduler_job_ctx", default=""
)

# Una corrida más lenta que esto deja una línea en INFO aunque haya salido bien.
# El objetivo es que los ticks rápidos no ensucien el journal pero que "esto
# viene tardando" sea visible sin tener que bajar el nivel de log.
SEGUNDOS_CORRIDA_LENTA = 30


def etiqueta_actual() -> str:
    """Etiqueta `job/run` de la corrida en curso ('' fuera de toda corrida)."""
    return _ETIQUETA.get()


@contextlib.contextmanager
def contexto(nombre: str, run_id: Optional[str] = None):
    """Estampa `nombre/xxxx` en cada log emitido dentro del bloque.

    El id corto es lo que hace grepeable una corrida concreta cuando hay varias
    del mismo job en vuelo. Se devuelve por si el llamador quiere nombrarlo en
    un mensaje ("seguí la corrida 7f3a").
    """
    run_id = run_id or uuid.uuid4().hex[:4]
    token = _ETIQUETA.set(f"{nombre}/{run_id}")
    try:
        yield run_id
    finally:
        _ETIQUETA.reset(token)


def formatear_duracion(segundos: float) -> str:
    if segundos < 60:
        return f"{segundos:.1f}s"
    minutos, resto = divmod(int(segundos), 60)
    return f"{minutos}m{resto:02d}s"


def instrumentar(func, nombre: str):
    """Envuelve un job para que su corrida se pueda seguir de punta a punta.

    Se usa desde run_scheduler.py al registrar cada job. La excepción se vuelve
    a levantar: APScheduler necesita verla para emitir EVENT_JOB_ERROR y para no
    dar por buena una corrida que se cayó.
    """
    @functools.wraps(func)
    def _corrida(*args, **kwargs):
        with contexto(nombre) as run_id:
            inicio = time.monotonic()
            logger.debug("▶ inicio")
            try:
                resultado = func(*args, **kwargs)
            except Exception:
                logger.error(
                    "✖ FALLÓ tras %s (corrida %s)",
                    formatear_duracion(time.monotonic() - inicio),
                    run_id,
                    exc_info=True,
                )
                raise
            duracion = time.monotonic() - inicio
            nivel = logging.INFO if duracion >= SEGUNDOS_CORRIDA_LENTA else logging.DEBUG
            logger.log(nivel, "✔ ok en %s", formatear_duracion(duracion))
            return resultado

    return _corrida


class _SinTracebackDuplicado(logging.Filter):
    """Descarta el 'Job X raised an exception' que APScheduler emite por su cuenta."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "raised an exception" not in str(record.msg)


def silenciar_traceback_duplicado():
    """Calla el traceback que APScheduler tira por su cuenta al fallar un job.

    `instrumentar` ya loguea ese mismo traceback pero con la etiqueta de la
    corrida y la duración; dejar los dos significa encontrar dos veces el mismo
    error en el journal, y la copia de APScheduler es la que NO se puede atar a
    una corrida.

    Se filtra por mensaje en vez de subirle el nivel al logger porque ese mismo
    logger emite los avisos de corrida atrasada ('Run time of job ... was
    missed'), que son justo los que hay que conservar: son la única señal de que
    un tick no llegó a correr, y desde adentro del job no se pueden generar.
    """
    logging.getLogger("apscheduler.executors.default").addFilter(_SinTracebackDuplicado())
