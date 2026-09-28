"""
Etiqueta [job/corrida] en los logs del scheduler.

El scheduler corre varios jobs a la vez sobre un pool de hilos y todos escriben al
mismo journal. La etiqueta es lo único que permite reconstruir qué línea fue de qué
corrida, así que lo que se verifica acá es justamente eso: que la etiqueta aparezca,
que sea distinta por corrida, que no se filtre de un hilo al siguiente (el pool
recicla hilos) y que un fallo deje una línea de ERROR con traceback.

Test 100% offline: no toca DB, Gemini ni tokens.
Correr: pytest tests/test_scheduler_logging.py -m "not tokens"
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.logging_config import FiltroContextoJob, FormatoJournal, LOG_FORMAT_JOB
from app.scheduler_logging import contexto, instrumentar

ETIQUETA = re.compile(r"\[([^\]]+)\]")


@pytest.fixture
def capturar(caplog):
    """caplog con el filtro de contexto puesto, como en el proceso del scheduler."""
    caplog.set_level(logging.DEBUG)
    caplog.handler.addFilter(FiltroContextoJob())
    caplog.handler.setFormatter(logging.Formatter(LOG_FORMAT_JOB))
    yield caplog
    caplog.handler.removeFilter(FiltroContextoJob())


def _etiquetas(caplog):
    return [ETIQUETA.search(linea).group(1) for linea in caplog.text.splitlines()
            if ETIQUETA.search(linea)]


def test_las_lineas_del_job_salen_etiquetadas(capturar):
    def job():
        logging.getLogger("app.doc_jobs").info("procesando")

    instrumentar(job, "chatbot_doc_jobs_queue")()

    etiquetas = _etiquetas(capturar)
    assert etiquetas, "ninguna línea salió etiquetada"
    assert all(e.startswith("chatbot_doc_jobs_queue/") for e in etiquetas)


def test_cada_corrida_tiene_su_propio_id(capturar):
    def job():
        logging.getLogger("app.doc_jobs").info("tick")

    envuelto = instrumentar(job, "chatbot_index_queue")
    envuelto()
    envuelto()

    ids = {e.split("/")[1] for e in _etiquetas(capturar)}
    assert len(ids) == 2, f"las dos corridas compartieron id: {ids}"


def test_la_etiqueta_no_sobrevive_al_hilo_reciclado(capturar):
    """El pool de APScheduler reutiliza hilos: la etiqueta de una corrida no puede
    quedar pegada a la siguiente que le toque a ese mismo hilo."""
    def job(n):
        logging.getLogger("app.tasks").info(f"trabajo {n}")

    with ThreadPoolExecutor(max_workers=1) as pool:
        for n in range(2):
            pool.submit(instrumentar(job, f"job_{n}"), n).result()

    logging.getLogger("app.tasks").info("fuera de toda corrida")

    etiquetas = _etiquetas(capturar)
    assert any(e.startswith("job_0/") for e in etiquetas)
    assert any(e.startswith("job_1/") for e in etiquetas)
    assert etiquetas[-1] == "-", "la última línea heredó la etiqueta de un job"


def test_contexto_anidado_vuelve_al_padre(capturar):
    """Las auditorías programadas abren su propio contexto dentro del job."""
    log = logging.getLogger("app.tasks")

    def job():
        with contexto("sched:Auditoria Voltara"):
            log.info("dentro de la tarea")
        log.info("de vuelta en el job")

    instrumentar(job, "dynamic_schedulers_job")()

    etiquetas = _etiquetas(capturar)
    assert any(e.startswith("sched:Auditoria Voltara/") for e in etiquetas)
    assert etiquetas[-1].startswith("dynamic_schedulers_job/"), "no volvió al contexto del job"


def test_el_fallo_deja_error_con_traceback_y_se_propaga(capturar):
    def job():
        raise ValueError("la BD dijo que no")

    with pytest.raises(ValueError):
        instrumentar(job, "presupuesto_ia_job")()

    errores = [r for r in capturar.records if r.levelno == logging.ERROR]
    assert len(errores) == 1
    assert "FALLÓ" in errores[0].getMessage()
    assert errores[0].exc_info is not None
    assert _etiquetas(capturar)[-1].startswith("presupuesto_ia_job/")


def test_prioridad_syslog_en_cada_linea_del_traceback():
    """journald parsea línea por línea: si el '<3>' fuera solo en la primera, el
    traceback entraría al journal como INFO y `journalctl -p err` lo escondería."""
    formato = FormatoJournal(LOG_FORMAT_JOB)
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        registro = logging.LogRecord(
            "app.tasks", logging.ERROR, __file__, 1, "explotó", None, sys.exc_info()
        )
    registro.job_ctx = "job/abcd"

    lineas = formato.format(registro).splitlines()
    assert len(lineas) > 1, "el traceback no quedó en el mensaje"
    assert all(linea.startswith("<3>") for linea in lineas)
