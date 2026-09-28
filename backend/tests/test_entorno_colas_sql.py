"""
Aislamiento dev/prod de las tablas de ejecución que se sumaron el 2026-09-22:
calidad.AuditSchedulers, calidad.BatchJobs y calidad.AuditTasks.

POR QUÉ EXISTE ESTE ARCHIVO
---------------------------
dev y prod comparten la base. Hasta el 2026-09-22 estas tres tablas no tenían Entorno: el
scheduler de cualquier servidor reclamaba las tareas programadas, procesaba los lotes de
Gemini (guardar, exportar, mandar el mail, borrar la carpeta del fileserver) y cerraba las
auditorías colgadas de los dos. Las demás colas (BatchPendientes, TranscripcionJobs,
ChatbotIndexJobs, ChatbotDocJobs) ya se particionaban por entorno.

Protege tres cosas:
  1. Cada lugar que reclama trabajo filtra por settings.ENVIRONMENT (offline).
  2. Todo INSERT en esas tablas escribe el Entorno: con el DEFAULT 'prod' de la columna, un
     INSERT que se lo olvide hace que una tarea creada en dev la ejecute prod, sin error.
  3. Los jobs fijos del scheduler que no son colas (fileserver, mails, tablas sin Entorno)
     se registran solo en prod, y cada job nuevo tiene que estar clasificado.
Y, cuando la migración ya está aplicada, que las consultas bindeen contra el esquema real.

No escribe ni ejecuta ninguna sentencia, no gasta tokens.
"""
import os
import re
from types import SimpleNamespace

import pytest

import Auditor as auditor_mod
from Auditor import AuditorIA
from AuditorIA import batch_cola
from app import tasks

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIGRACION = os.path.join(RAIZ, "scripts", "migrations", "2026-09-22_entorno_colas_ejecucion.sql")
TABLAS = ("AuditSchedulers", "BatchJobs", "AuditTasks")


# --------------------------------------------------------------------------- #
# Motor de mentira: registra cada sentencia con sus parámetros                 #
# --------------------------------------------------------------------------- #
class _Resultado:
    rowcount = 0

    def mappings(self):
        return self

    def all(self):
        return []

    def fetchall(self):
        return []


class _Conexion:
    def __init__(self, registro):
        self._registro = registro

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def begin(self):
        return SimpleNamespace(commit=lambda: None, rollback=lambda: None)

    def commit(self):
        pass

    def execute(self, sentencia, params=None):
        self._registro.append((str(sentencia), params or {}))
        return _Resultado()


class _Engine:
    def __init__(self):
        self.sentencias = []

    def begin(self):
        return _Conexion(self.sentencias)

    def connect(self):
        return _Conexion(self.sentencias)

    def la_que_nombra(self, fragmento):
        encontradas = [(sql, p) for sql, p in self.sentencias if fragmento in sql]
        assert encontradas, f"No se ejecutó ninguna sentencia con {fragmento!r}"
        return encontradas[0]


@pytest.fixture
def entorno_dev(monkeypatch):
    """Se prueba con 'dev' a propósito: es el valor que NO coincide con el DEFAULT de la
    columna, así que un parámetro olvidado no pasaría el test por casualidad."""
    monkeypatch.setattr(tasks.settings, "ENVIRONMENT", "dev")
    return "dev"


def _sentencia_de_reclamar_programadas(monkeypatch):
    engine = _Engine()
    monkeypatch.setattr(tasks, "engine", engine)
    tasks.run_pending_schedulers()
    return engine.la_que_nombra("AuditSchedulers")


def _sentencia_de_cerrar_huerfanas(monkeypatch):
    engine = _Engine()
    monkeypatch.setattr(tasks, "engine", engine)
    tasks.cerrar_auditorias_huerfanas()
    return engine.la_que_nombra("AuditTasks")


def _sentencia_de_revisar_lotes():
    engine = _Engine()
    fake = SimpleNamespace(engine=engine, MAX_INTENTOS_PROCESO=AuditorIA.MAX_INTENTOS_PROCESO)
    assert AuditorIA.check_batch_status(fake) is None
    return engine.la_que_nombra("BatchJobs")


def _sentencia_de_registrar_lote():
    engine = _Engine()
    batch_cola.registrar_lote_enviado(engine, batch_id="batches/x", filas_batch_data=[])
    return engine.la_que_nombra("INSERT INTO calidad.BatchJobs")


# --------------------------------------------------------------------------- #
# 1. Quien reclama trabajo, reclama solo el de su entorno                      #
# --------------------------------------------------------------------------- #
def test_el_scheduler_solo_reclama_las_tareas_programadas_de_su_entorno(monkeypatch, entorno_dev):
    sql, params = _sentencia_de_reclamar_programadas(monkeypatch)
    assert "Entorno = :env" in sql
    assert params["env"] == entorno_dev


def test_solo_cierra_las_auditorias_colgadas_de_su_entorno(monkeypatch, entorno_dev):
    sql, params = _sentencia_de_cerrar_huerfanas(monkeypatch)
    assert "Entorno = :env" in sql
    assert params["env"] == entorno_dev


def test_solo_revisa_los_lotes_de_gemini_de_su_entorno(entorno_dev):
    sql, params = _sentencia_de_revisar_lotes()
    # El filtro tiene que envolver TODO el WHERE: con un OR suelto, los SUCCEEDED sin
    # procesar del otro entorno se colarían igual.
    assert re.search(r"WHERE\s+Entorno\s*=\s*:env\s+AND\s*\(", sql), sql
    assert params["env"] == entorno_dev


def test_el_lote_enviado_queda_a_nombre_de_su_entorno(entorno_dev):
    sql, params = _sentencia_de_registrar_lote()
    assert "Entorno" in sql
    assert params["env"] == entorno_dev


def test_auditor_lee_el_mismo_settings_que_el_scheduler():
    """El monkeypatch de arriba cambia tasks.settings; si Auditor tuviera otra instancia,
    el test de los lotes estaría probando nada."""
    assert auditor_mod.settings is tasks.settings is batch_cola.settings


# --------------------------------------------------------------------------- #
# 2. Ningún INSERT se olvida del Entorno                                       #
# --------------------------------------------------------------------------- #
_INSERT = re.compile(
    r"INSERT\s+INTO\s+\[?calidad\]?\.\[?(" + "|".join(TABLAS) + r")\]?\s*\(([^)]*)\)",
    re.I,
)


def _inserts_en_el_codigo():
    encontrados = []
    for base in ("backend", "scripts"):
        for carpeta, subcarpetas, archivos in os.walk(os.path.join(RAIZ, base)):
            subcarpetas[:] = [d for d in subcarpetas if d not in ("tests", "__pycache__", "migrations")]
            for archivo in archivos:
                if not archivo.endswith(".py"):
                    continue
                ruta = os.path.join(carpeta, archivo)
                with open(ruta, encoding="utf-8") as f:
                    fuente = f.read()
                for m in _INSERT.finditer(fuente):
                    linea = fuente.count("\n", 0, m.start()) + 1
                    encontrados.append((f"{os.path.relpath(ruta, RAIZ)}:{linea}", m.group(1), m.group(2)))
    return encontrados


def test_todo_insert_en_las_tablas_de_ejecucion_escribe_el_entorno():
    inserts = _inserts_en_el_codigo()
    # 1 en AuditSchedulers, 3 en AuditTasks, 1 en BatchJobs. Si un refactor cambia la
    # forma de escribirlos, que se note acá y no que el test deje de mirar.
    assert len(inserts) >= 5, inserts
    sin_entorno = [f"{donde} ({tabla})" for donde, tabla, columnas in inserts
                   if not re.search(r"\bEntorno\b", columnas, re.I)]
    assert not sin_entorno, (
        "Estos INSERT no escriben Entorno: con el DEFAULT 'prod', lo que se cree en dev lo "
        f"ejecuta prod: {sin_entorno}"
    )


def test_la_migracion_agrega_la_columna_a_las_tres_tablas_y_es_idempotente():
    with open(MIGRACION, encoding="utf-8") as f:
        sql = f.read()
    for tabla in TABLAS:
        assert f"IF COL_LENGTH('calidad.{tabla}', 'Entorno') IS NULL" in sql, tabla
        assert re.search(
            rf"ALTER TABLE calidad\.{tabla} ADD Entorno NVARCHAR\(20\) NOT NULL\s+"
            rf"CONSTRAINT DF_{tabla}_Entorno DEFAULT 'prod' WITH VALUES",
            sql,
        ), tabla


# --------------------------------------------------------------------------- #
# 3. Jobs del scheduler: cola por entorno o trabajo fijo solo en prod          #
# --------------------------------------------------------------------------- #
# Las colas se aíslan solas (cada tick reclama lo de su entorno), así que corren en los
# dos servidores. Los trabajos fijos sobre algo compartido (fileserver, mails a Calidad,
# tablas sin Entorno) correrían dos veces: se registran solo con ENVIRONMENT=prod.
JOBS_POR_ENTORNO = {
    "check_batches_job", "auditorias_huerfanas_job", "transcripciones_cola_job",
    "batch_cola_job", "dynamic_schedulers_job", "chatbot_index_queue", "chatbot_doc_jobs_queue",
}
JOBS_SOLO_PROD = {
    "cuotas_conciliar_job", "auditoria_csv_diaria_job", "presupuesto_ia_job",
    "salud_plantillas_semanal", "chatbot_vacios_conocimiento",
}


def _jobs_registrados():
    """{id: solo_prod} de cada agregar_job de run_scheduler.py. Se lee el fuente y no se
    importa el módulo: importarlo configura el logging del scheduler para toda la sesión."""
    import ast
    with open(os.path.join(RAIZ, "backend", "run_scheduler.py"), encoding="utf-8") as f:
        arbol = ast.parse(f.read())
    jobs = {}
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Call) and getattr(nodo.func, "id", None) == "agregar_job":
            kw = {k.arg: k.value for k in nodo.keywords}
            solo_prod = kw.get("solo_prod")
            jobs[kw["id"].value] = bool(solo_prod is not None and solo_prod.value)
    return jobs


def test_cada_job_del_scheduler_esta_clasificado():
    """Un job nuevo tiene que decidir si es una cola por entorno o un trabajo fijo."""
    sin_clasificar = set(_jobs_registrados()) - JOBS_POR_ENTORNO - JOBS_SOLO_PROD
    assert not sin_clasificar, (
        f"Jobs nuevos sin clasificar: {sorted(sin_clasificar)}. Si reclaman trabajo por "
        "Entorno van en JOBS_POR_ENTORNO; si tocan algo compartido, solo_prod=True y a "
        "JOBS_SOLO_PROD."
    )


def test_los_trabajos_fijos_solo_se_registran_en_prod():
    jobs = _jobs_registrados()
    assert {j for j in JOBS_SOLO_PROD if not jobs.get(j)} == set()


def test_las_colas_por_entorno_corren_en_los_dos_servidores():
    """Si una cola quedara solo_prod, dev no podría procesar lo que encola él mismo."""
    jobs = _jobs_registrados()
    assert {j for j in JOBS_POR_ENTORNO if jobs.get(j, True)} == set()


# --------------------------------------------------------------------------- #
# 4. Binding contra la base (cuando la migración ya está aplicada)             #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def columnas_creadas(engine):
    """La columna la agrega Ignacio a mano. Hasta entonces estos tests no tienen contra qué
    validar: se saltean con el motivo a la vista en vez de fallar por algo que no pasó."""
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        faltan = [t for t in TABLAS if conn.execute(
            _text(f"SELECT COL_LENGTH('calidad.{t}', 'Entorno')")).scalar() is None]
    if faltan:
        pytest.skip(f"Falta Entorno en {faltan}: correr "
                    "scripts/migrations/2026-09-22_entorno_colas_ejecucion.sql")
    return True


def _concretar(sql: str) -> str:
    """describe no admite parámetros: se reemplazan por literales."""
    sql = sql.replace(":horas", "-12").replace(":max_intentos", "5")
    return re.sub(r":\w+", "'x'", sql)


def test_las_consultas_bindean_contra_el_esquema(columnas_creadas, validar_sql, monkeypatch):
    consultas = {
        "reclamar programadas": _sentencia_de_reclamar_programadas(monkeypatch)[0],
        "cerrar huérfanas": _sentencia_de_cerrar_huerfanas(monkeypatch)[0],
        "revisar lotes": _sentencia_de_revisar_lotes()[0],
        "registrar lote": _sentencia_de_registrar_lote()[0],
    }
    errores = {}
    for nombre, sql in consultas.items():
        resultado = validar_sql(_concretar(sql))
        if not resultado.ok:
            errores[nombre] = resultado.error
    assert not errores, errores
