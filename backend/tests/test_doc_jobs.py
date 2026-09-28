"""Cola del asistente de documentación (app/doc_jobs.py).

Lo que importa acá no es que Gemini escriba bien —eso es criterio del modelo— sino
que la cola NUNCA deje un job colgado en 'running': el usuario está mirando una
pantalla que dice "procesando" y su única señal de que algo salió mal es que el job
termine en 'failed' con un motivo legible.

Se cubre también que el merge lea los documentos frescos de la base y no una copia
guardada al encolar, y que el resultado viaje sin perder emojis (el markdown los
usa: 🗣️ ✉️ 🖥️).
"""
import base64
import json

import pytest

import app.doc_jobs as doc_jobs


# ------------------------------------------------------------------ fakes

class FakeConn:
    """Conexión que devuelve filas preprogramadas y registra lo ejecutado."""

    def __init__(self, log, filas):
        self._log = log
        self._filas = filas

    def execute(self, query, params=None):
        sql = " ".join(str(query).split())
        self._log.append((sql, params or {}))
        for fragmento, resultado in self._filas.items():
            if fragmento in sql:
                return resultado
        return FakeResult([])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeResult:
    def __init__(self, filas):
        self._filas = filas
        self.rowcount = len(filas)

    def first(self):
        return self._filas[0] if self._filas else None

    def mappings(self):
        return self

    def all(self):
        return self._filas


class FakeEngine:
    def __init__(self, filas=None):
        self.log = []
        self._filas = filas or {}

    def begin(self):
        return FakeConn(self.log, self._filas)

    def connect(self):
        return FakeConn(self.log, self._filas)

    def sql_con(self, fragmento):
        return [(s, p) for s, p in self.log if fragmento in s]


@pytest.fixture
def engine(monkeypatch):
    eng = FakeEngine()
    monkeypatch.setattr(doc_jobs, "_get_engine", lambda: eng)
    return eng


@pytest.fixture
def asistente(monkeypatch):
    """El asistente real, con las dos funciones caras reemplazadas."""
    import AuditorIA.asistente_docs as ad
    return ad


# --------------------------------------------------- ejecutar_doc_job: fallos

def test_un_tipo_desconocido_deja_el_job_failed_y_no_propaga(engine):
    """Nada puede escapar de ejecutar_doc_job: es el tick del scheduler."""
    doc_jobs.ejecutar_doc_job(7, "tipo_raro", 1, "{}", None)

    fallidos = engine.sql_con("status = 'failed'")
    assert len(fallidos) == 1
    assert fallidos[0][1]["jid"] == 7
    assert "tipo_raro" in fallidos[0][1]["err"]


def test_si_el_asistente_revienta_el_job_queda_failed_con_el_motivo(engine, asistente, monkeypatch):
    def explota(**kwargs):
        raise RuntimeError("La IA no devolvió ningún documento actualizado.")

    monkeypatch.setattr(asistente, "formatear_documento", explota)

    doc_jobs.ejecutar_doc_job(3, "formatear", None,
                              json.dumps({"fuentes": [{"tipo": "texto", "texto": "hola"}]}), None)

    fallidos = engine.sql_con("status = 'failed'")
    assert len(fallidos) == 1
    assert "no devolvió ningún documento" in fallidos[0][1]["err"]


def test_el_payload_se_limpia_tambien_cuando_falla(engine):
    """Un adjunto de 20 MB en base64 no puede quedar en la tabla para siempre."""
    doc_jobs.ejecutar_doc_job(9, "tipo_raro", None, "{}", None)

    sql = engine.sql_con("status = 'failed'")[0][0]
    assert "payload = NULL" in sql


def test_un_payload_corrupto_no_cuelga_el_job(engine):
    """JSON ilegible: falla limpio en vez de quedar 'running' para siempre."""
    doc_jobs.ejecutar_doc_job(11, "formatear", None, "{esto no es json", None)

    assert len(engine.sql_con("status = 'failed'")) == 1


# ---------------------------------------------------- ejecutar_doc_job: éxito

def test_el_resultado_se_guarda_y_el_payload_se_libera(engine, asistente, monkeypatch):
    monkeypatch.setattr(asistente, "formatear_documento",
                        lambda **kw: {"documentos": [{"titulo": "T", "markdown": "# T"}]})

    doc_jobs.ejecutar_doc_job(5, "formatear", None,
                              json.dumps({"fuentes": [{"tipo": "texto", "texto": "hola"}]}), None)

    ok = engine.sql_con("status = 'done'")
    assert len(ok) == 1
    assert "payload = NULL" in ok[0][0]
    assert json.loads(ok[0][1]["res"])["documentos"][0]["titulo"] == "T"


def test_el_resultado_conserva_los_emojis(engine, asistente, monkeypatch):
    """El markdown usa 🗣️/✉️/🖥️ y ya se perdieron una vez por serializar mal."""
    monkeypatch.setattr(asistente, "formatear_documento",
                        lambda **kw: {"documentos": [{"titulo": "T", "markdown": "✉️ Escribile al cliente"}]})

    doc_jobs.ejecutar_doc_job(6, "formatear", None,
                              json.dumps({"fuentes": [{"tipo": "texto", "texto": "x"}]}), None)

    guardado = engine.sql_con("status = 'done'")[0][1]["res"]
    assert "✉️" in guardado                      # ensure_ascii=False
    assert "✉️" in json.loads(guardado)["documentos"][0]["markdown"]


# ------------------------------------------------------------------ contrato

def test_la_propuesta_sobrevive_al_viaje_por_json(engine, asistente, monkeypatch):
    """Antes el asistente devolvía el dict directo al response_model; ahora pasa por
    la base como JSON. Si esa vuelta rompiera el shape, el polling devolvería 500
    justo cuando el trabajo ya salió bien (y se pagó)."""
    from app.models import DocJobEstadoOut

    real = {
        "documentos": [{"doc_id": 35, "titulo": "Combustible Benefix",
                        "markdown": "# Combustible\n✉️ texto", "accion": "actualizar",
                        "cambios": ["Se agregó el circuito de tarjetas virtuales"]}],
        "ruteo": ["Los pedidos de tarjetas van al documento de Combustible"],
        "notas": [],
        "faltantes_residuales": [{"dato": "plazo de impresión", "severidad": "baja"}],
        "invenciones_residuales": [],
        "conflictos": [],
        "rondas_verificacion": 1,
        "verificado_ok": True,
    }
    monkeypatch.setattr(asistente, "agregar_informacion", lambda **kw: real)
    monkeypatch.setattr(doc_jobs, "_correr_merge", lambda p, c, u: real)

    doc_jobs.ejecutar_doc_job(1, "merge", 3, json.dumps({"fuentes": []}), None)
    crudo = engine.sql_con("status = 'done'")[0][1]["res"]

    from app.models import DocPropuestaOut
    estado = DocJobEstadoOut(job_id=1, status="done", tipo="merge",
                             resultado=DocPropuestaOut(**json.loads(crudo)))

    assert estado.resultado.documentos[0].doc_id == 35
    assert "✉️" in estado.resultado.documentos[0].markdown
    assert estado.resultado.verificado_ok is True
    assert estado.resultado.rondas_verificacion == 1


# ------------------------------------------------------------------ fuentes

def test_los_archivos_se_decodifican_de_base64():
    fuentes = doc_jobs._fuentes_desde_payload([
        {"tipo": "archivo", "nombre": "a.pdf", "mime": "application/pdf",
         "datos_base64": base64.b64encode(b"%PDF-1.4 contenido").decode()},
    ])

    assert fuentes[0]["datos"] == b"%PDF-1.4 contenido"
    assert fuentes[0]["mime"] == "application/pdf"


def test_el_texto_vacio_se_descarta():
    fuentes = doc_jobs._fuentes_desde_payload([
        {"tipo": "texto", "texto": "   "},
        {"tipo": "texto", "texto": "esto sí"},
    ])

    assert len(fuentes) == 1
    assert fuentes[0]["texto"] == "esto sí"


# -------------------------------------------------------------------- merge

def _fila_doc(doc_id, titulo, md):
    return {"id": doc_id, "titulo": titulo, "contenido_md": md}


def test_el_merge_lee_los_documentos_frescos_de_la_base(monkeypatch, asistente):
    """Entre encolar y ejecutar alguien pudo editar el documento: mergear contra
    una copia vieja le pisaría el cambio."""
    eng = FakeEngine({
        "FROM pagina_web.ChatbotDocMarkdown": FakeResult([_fila_doc(35, "Combustible", "# nuevo contenido")]),
        "FROM pagina_web.Chatbots": FakeResult([]),
    })
    monkeypatch.setattr(doc_jobs, "_get_engine", lambda: eng)

    visto = {}
    monkeypatch.setattr(asistente, "agregar_informacion",
                        lambda **kw: visto.update(kw) or {"documentos": []})

    doc_jobs._correr_merge({"fuentes": [{"tipo": "texto", "texto": "x"}]}, 3, None)

    assert visto["documentos_actuales"] == [
        {"doc_id": 35, "titulo": "Combustible", "markdown": "# nuevo contenido"}
    ]


def test_doc_ids_acota_los_candidatos(monkeypatch, asistente):
    eng = FakeEngine({
        "FROM pagina_web.ChatbotDocMarkdown": FakeResult([
            _fila_doc(34, "P&B", "a"), _fila_doc(35, "Combustible", "b"),
        ]),
        "FROM pagina_web.Chatbots": FakeResult([]),
    })
    monkeypatch.setattr(doc_jobs, "_get_engine", lambda: eng)

    visto = {}
    monkeypatch.setattr(asistente, "agregar_informacion",
                        lambda **kw: visto.update(kw) or {"documentos": []})

    doc_jobs._correr_merge({"fuentes": [{"tipo": "texto", "texto": "x"}], "doc_ids": [35]}, 3, None)

    assert [d["doc_id"] for d in visto["documentos_actuales"]] == [35]


def test_si_borraron_los_documentos_el_error_es_legible(monkeypatch):
    eng = FakeEngine({"FROM pagina_web.ChatbotDocMarkdown": FakeResult([])})
    monkeypatch.setattr(doc_jobs, "_get_engine", lambda: eng)

    with pytest.raises(ValueError, match="ya no existen"):
        doc_jobs._correr_merge({"fuentes": [{"tipo": "texto", "texto": "x"}]}, 3, None)


# --------------------------------------------------------------------- cola

def test_la_cola_para_cuando_no_queda_nada(engine):
    """Sin esto el while True del tick giraría para siempre."""
    doc_jobs.procesar_cola_docs()

    assert engine.sql_con("UPDATE siguiente")


def test_un_error_consultando_la_cola_no_tumba_el_scheduler(monkeypatch):
    class EngineRoto:
        def begin(self):
            raise OSError("se cayó la base")

    monkeypatch.setattr(doc_jobs, "_get_engine", lambda: EngineRoto())

    doc_jobs.procesar_cola_docs()   # no propaga


def test_el_claim_filtra_por_entorno(engine):
    """dev y prod comparten la base: un scheduler no puede tomar jobs del otro."""
    doc_jobs.procesar_cola_docs()

    sql, params = engine.sql_con("UPDATE siguiente")[0]
    assert "environment = :env" in sql
    assert "env" in params


# ------------------------------------------------------------------ huérfanos

def test_los_huerfanos_se_marcan_solo_del_entorno_propio(engine):
    doc_jobs.marcar_doc_jobs_huerfanos()

    sql, params = engine.sql_con("SET status = 'failed'")[0]
    assert "status = 'running'" in sql
    assert "environment = :env" in sql
    assert params["env"]


def test_el_mensaje_del_huerfano_le_dice_al_usuario_que_reintente(engine):
    doc_jobs.marcar_doc_jobs_huerfanos()

    sql = engine.sql_con("SET status = 'failed'")[0][0]
    assert "Volvé a intentarlo" in sql


def test_los_jobs_viejos_se_purgan(engine):
    """El payload ya se limpió al terminar; esto baja el resto de la fila."""
    doc_jobs.marcar_doc_jobs_huerfanos()

    assert engine.sql_con("DELETE FROM pagina_web.ChatbotDocJobs")


# ------------------------------- un trabajo por bot, cancelar, cola y retomar

def test_no_se_encola_un_segundo_trabajo_para_el_mismo_bot(monkeypatch):
    """El 2026-09-14 el mismo manual se encoló tres veces (jobs 183, 184 y 185) y la cola
    es de a uno: cada copia sumaba horas de espera y el mismo gasto."""
    from types import SimpleNamespace
    eng = FakeEngine({"status IN ('pending', 'running')": FakeResult([SimpleNamespace(id=183, status="running")])})
    monkeypatch.setattr(doc_jobs, "_get_engine", lambda: eng)

    with pytest.raises(doc_jobs.TrabajoEnCurso) as e:
        doc_jobs.encolar("formatear", 16, {"material_ids": [129]}, 10000002)

    assert e.value.job_id == 183
    assert not eng.sql_con("INSERT INTO")
    _, params = eng.sql_con("status IN ('pending', 'running')")[0]
    assert params["cid"] == 16 and params["env"]


def test_sin_trabajo_abierto_el_bot_encola_normal(monkeypatch):
    from types import SimpleNamespace
    eng = FakeEngine({"OUTPUT INSERTED.id": FakeResult([SimpleNamespace(id=190)])})
    monkeypatch.setattr(doc_jobs, "_get_engine", lambda: eng)

    assert doc_jobs.encolar("formatear", 16, {"material_ids": [129]}, 10000002) == 190


def test_cancelar_cierra_el_trabajo_con_motivo_propio(monkeypatch):
    from types import SimpleNamespace
    eng = FakeEngine({"OUTPUT DELETED.status": FakeResult([SimpleNamespace(status="running")])})
    monkeypatch.setattr(doc_jobs, "_get_engine", lambda: eng)

    assert doc_jobs.cancelar(183) == "running"

    sql, params = eng.sql_con("OUTPUT DELETED.status")[0]
    assert "status IN ('pending', 'running')" in sql and "environment = :env" in sql
    assert doc_jobs.es_cancelacion("failed", params["err"])


def test_cancelar_un_trabajo_que_ya_termino_no_hace_nada(engine):
    assert doc_jobs.cancelar(182) is None


def test_el_cierre_de_un_trabajo_no_pisa_una_cancelacion():
    assert "status = 'running'" in str(doc_jobs._FINALIZAR_OK)
    assert "status = 'running'" in str(doc_jobs._FINALIZAR_ERROR)


def _job_que_llama_a_gemini(monkeypatch, asistente, status_en_la_base):
    """Corre un formatear cuyo 'asistente' hace lo mismo que _generar_json antes de cada
    llamada: preguntar si lo cancelaron. Devuelve (engine, llamadas hechas)."""
    from types import SimpleNamespace
    eng = FakeEngine({"SELECT status FROM pagina_web.ChatbotDocJobs":
                      FakeResult([SimpleNamespace(status=status_en_la_base)])})
    monkeypatch.setattr(doc_jobs, "_get_engine", lambda: eng)
    monkeypatch.setattr(doc_jobs, "_fuentes_del_job", lambda p: [{"tipo": "texto", "texto": "hola"}])
    llamadas = []

    def formatear(**kwargs):
        asistente._cortar_si_cancelaron()
        llamadas.append("gemini")
        return {"documentos": []}

    monkeypatch.setattr(asistente, "formatear_documento", formatear)
    doc_jobs.ejecutar_doc_job(185, "formatear", None, json.dumps({"material_ids": [129]}), 1)
    return eng, llamadas


def test_si_lo_cancelan_mientras_corre_se_corta_antes_de_llamar_a_gemini(monkeypatch, asistente):
    """La fila ya la cerró quien canceló: el trabajo solo tiene que dejar de gastar."""
    eng, llamadas = _job_que_llama_a_gemini(monkeypatch, asistente, status_en_la_base="failed")

    assert llamadas == []
    assert not eng.sql_con("SET status = 'done'")
    assert not eng.sql_con("SET status = 'failed'")


def test_si_no_lo_cancelaron_el_trabajo_sigue_y_se_guarda(monkeypatch, asistente):
    eng, llamadas = _job_que_llama_a_gemini(monkeypatch, asistente, status_en_la_base="running")

    assert llamadas == ["gemini"]
    assert eng.sql_con("SET status = 'done'")


def test_un_error_de_base_no_cancela_el_trabajo(monkeypatch):
    """Cortar media hora de trabajo por un corte de red de un segundo sería peor."""
    class EngineRoto:
        def connect(self):
            raise RuntimeError("sin conexión")

    monkeypatch.setattr(doc_jobs, "_get_engine", lambda: EngineRoto())
    assert doc_jobs._fue_cancelado(185) is False


def _fila_job(**campos):
    fila = {"id": 185, "tipo": "formatear", "chatbot_id": 16, "status": "pending", "resultado": None,
            "error": None, "environment": "prod", "created_at": None, "started_at": None,
            "finished_at": None, "segundos": 572}
    fila.update(campos)
    return fila


def test_en_cola_se_informa_cuantos_hay_adelante_y_cuanto_lleva_el_que_corre(monkeypatch):
    """"En cola" solo no explicaba nada: el job 185 esperó casi una hora detrás de uno que
    llevaba tres."""
    eng = FakeEngine({
        "resultado, error, environment": FakeResult([_fila_job()]),
        "AS adelante": FakeResult([{"adelante": 1, "en_curso_bot": "Hidra Comercial", "en_curso_segundos": 10200}]),
    })
    monkeypatch.setattr(doc_jobs, "_get_engine", lambda: eng)

    job = doc_jobs.obtener(185)

    assert (job["adelante"], job["en_curso_bot"], job["en_curso_segundos"]) == (1, "Hidra Comercial", 10200)
    assert job["segundos"] == 572 and job["cancelado"] is False


def test_un_trabajo_corriendo_no_consulta_la_cola(monkeypatch):
    eng = FakeEngine({"resultado, error, environment": FakeResult([_fila_job(status="running")])})
    monkeypatch.setattr(doc_jobs, "_get_engine", lambda: eng)

    assert doc_jobs.obtener(185)["adelante"] is None
    assert not eng.sql_con("AS adelante")


def test_un_cancelado_se_reconoce_como_tal(monkeypatch):
    eng = FakeEngine({"resultado, error, environment":
                      FakeResult([_fila_job(status="failed", error=doc_jobs.MOTIVO_CANCELADO)])})
    monkeypatch.setattr(doc_jobs, "_get_engine", lambda: eng)

    assert doc_jobs.obtener(185)["cancelado"] is True


def test_al_abrir_el_bot_se_retoma_su_trabajo_abierto(monkeypatch):
    from types import SimpleNamespace
    eng = FakeEngine({"status IN ('pending', 'running')": FakeResult([SimpleNamespace(id=185, status="running")])})
    monkeypatch.setattr(doc_jobs, "_get_engine", lambda: eng)

    assert doc_jobs.trabajo_actual_del_bot(16) == {"job_id": 185, "status": "running", "segundos": None}


def test_se_ofrece_la_propuesta_que_termino_sin_que_nadie_la_guardara(monkeypatch):
    """El job 182 terminó a los 72 minutos con la pantalla ya rendida: la propuesta estaba
    en la base y no había forma de abrirla."""
    from types import SimpleNamespace
    eng = FakeEngine({"status = 'done'": FakeResult([SimpleNamespace(id=185, material_ids="[129]", segundos=300)])})
    monkeypatch.setattr(doc_jobs, "_get_engine", lambda: eng)
    monkeypatch.setattr(doc_jobs.doc_material, "ids_pendientes", lambda cid: [129, 130])

    assert doc_jobs.trabajo_actual_del_bot(16) == {"job_id": 185, "status": "done", "segundos": 300}


def test_no_se_ofrece_una_propuesta_que_ya_se_guardo(monkeypatch):
    """Guardar la propuesta borra su material de la bandeja."""
    from types import SimpleNamespace
    eng = FakeEngine({"status = 'done'": FakeResult([SimpleNamespace(id=185, material_ids="[129]", segundos=300)])})
    monkeypatch.setattr(doc_jobs, "_get_engine", lambda: eng)
    monkeypatch.setattr(doc_jobs.doc_material, "ids_pendientes", lambda cid: [130])

    assert doc_jobs.trabajo_actual_del_bot(16) is None


def test_correr_tabla_desde_material_crea_propuesta_de_tabla(engine, asistente, monkeypatch):
    prop = {"es_tabla": True, "nombre": "Cartera de cobranzas", "columnas": [], "filas": []}
    monkeypatch.setattr(asistente, "analizar_tabla", lambda *a, **k: prop)
    monkeypatch.setattr(doc_jobs, "_fuentes_del_job", lambda p: [{"tipo": "texto", "texto": "| A | B |\n| 1 | 2 |", "nombre": "test.txt"}])

    doc_jobs.ejecutar_doc_job(
        99, "tabla", 3,
        json.dumps({"material_ids": [100]}), 10000003,
    )

    completados = engine.sql_con("status = 'done'")
    assert len(completados) == 1
    _, params = completados[0]
    res = json.loads(params["res"])
    assert res["tablas"][0]["nombre"] == "Cartera de cobranzas"
    assert res["material_ids"] == [100]

