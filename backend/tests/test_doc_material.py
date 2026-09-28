"""Bandeja de material pendiente del asistente de documentación.

Lo que se cuida acá es la regla que motivó la bandeja: el material se procesa UNA
vez, y no se borra hasta que la propuesta se acepta. Si se borrara antes (al
encolar, o al terminar el job), un trabajo fallido o una propuesta descartada se
llevaría puesto el material que alguien cargó a mano.

También se cubre el otro lado del cambio: guardar documentos dejó de encolar un
reindexado y el aviso de "cambios sin indexar" pasó a ser lo único que avisa que el
chatbot todavía responde con los documentos viejos.
"""
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

import app.doc_jobs as doc_jobs
import app.doc_material as doc_material
from app.routers.chatbot_admin import _hay_cambios_sin_indexar, _material_de_la_corrida


# ------------------------------------------------------------------ fakes

class FakeResult:
    def __init__(self, filas):
        self._filas = filas
        self.rowcount = len(filas)

    def first(self):
        return self._filas[0] if self._filas else None

    def scalar(self):
        return self._filas[0] if self._filas else None

    def mappings(self):
        return self

    def all(self):
        return self._filas


class FakeConn:
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


def _material(mid, tipo="texto", texto=None, datos=None, nota=None, nombre="x", destino=None, es_actualizacion=False):
    return {"id": mid, "tipo": tipo, "nombre": nombre, "mime": "application/pdf" if datos else None,
            "texto": texto, "datos": datos, "nota": nota, "destino_titulo": destino,
            "es_actualizacion": 1 if es_actualizacion else 0}


# ------------------------------------------------------------ cargar_fuentes

def test_el_material_se_convierte_en_fuentes_para_el_asistente(monkeypatch):
    eng = FakeEngine({"FROM pagina_web.ChatbotDocMaterial": FakeResult([
        _material(1, texto="procedimiento de altas"),
        _material(2, tipo="archivo", datos=b"%PDF-1.4", nombre="manual.pdf"),
    ])})
    monkeypatch.setattr(doc_material, "_get_engine", lambda: eng)

    fuentes = doc_material.cargar_fuentes([1, 2])

    assert fuentes[0] == {"tipo": "texto", "texto": "procedimiento de altas", "nombre": "x"}
    assert fuentes[1]["datos"] == b"%PDF-1.4"
    assert fuentes[1]["mime"] == "application/pdf"


def test_la_nota_de_quien_cargo_el_material_viaja_a_la_ia(monkeypatch):
    """Es la mitad del contexto: sin ella la IA no sabe que el material REEMPLAZA algo."""
    eng = FakeEngine({"FROM pagina_web.ChatbotDocMaterial": FakeResult([
        _material(1, texto="nuevo tope: 5 días", nota="reemplaza el punto 3"),
        _material(2, tipo="archivo", datos=b"x", nombre="tarifas.xlsx", nota="solo la hoja 2"),
    ])})
    monkeypatch.setattr(doc_material, "_get_engine", lambda: eng)

    fuentes = doc_material.cargar_fuentes([1, 2])

    assert "reemplaza el punto 3" in fuentes[0]["texto"]
    assert "nuevo tope: 5 días" in fuentes[0]["texto"]
    assert "solo la hoja 2" in fuentes[1]["nombre"]


def test_el_flag_es_actualizacion_se_indica_a_la_ia(monkeypatch):
    """Cuando el material se marca como actualización/reemplazo, se antepone la directiva explícita."""
    eng = FakeEngine({"FROM pagina_web.ChatbotDocMaterial": FakeResult([
        _material(1, texto="nuevas tarifas 2026", es_actualizacion=True),
    ])})
    monkeypatch.setattr(doc_material, "_get_engine", lambda: eng)

    fuentes = doc_material.cargar_fuentes([1])

    assert "ACTUALIZACIÓN / REEMPLAZO O BAJA VIGENTE" in fuentes[0]["texto"]
    assert "nuevas tarifas 2026" in fuentes[0]["texto"]


def test_agregar_guarda_flag_es_actualizacion(monkeypatch):
    import types
    eng = FakeEngine({"OUTPUT INSERTED.id": FakeResult([types.SimpleNamespace(id=100)])})
    monkeypatch.setattr(doc_material, "_get_engine", lambda: eng)

    ids = doc_material.agregar(chatbot_id=1, fuentes=[{"tipo": "texto", "texto": "abc"}],
                               nota="nota test", created_by=123, es_actualizacion=True)

    assert ids == [100]
    sql, params = eng.sql_con("INSERT INTO pagina_web.ChatbotDocMaterial")[0]
    assert params["es_act"] == 1


def test_el_documento_destino_se_le_dice_a_la_ia(monkeypatch):
    """Cargar material desde la fila de un documento no fuerza el ruteo (sería una
    llamada por documento, justo lo que se vino a evitar): se lo dice y la IA decide."""
    eng = FakeEngine({"FROM pagina_web.ChatbotDocMaterial": FakeResult([
        _material(1, texto="nuevo circuito", destino="Combustible Benefix"),
        _material(2, texto="otra cosa", destino="Combustible Benefix", nota="al final"),
    ])})
    monkeypatch.setattr(doc_material, "_get_engine", lambda: eng)

    fuentes = doc_material.cargar_fuentes([1, 2])

    assert "Combustible Benefix" in fuentes[0]["texto"]
    assert "nuevo circuito" in fuentes[0]["texto"]
    # Destino y nota conviven en la misma indicación.
    assert "Combustible Benefix" in fuentes[1]["texto"] and "al final" in fuentes[1]["texto"]


def test_sin_ids_no_se_consulta_la_base(monkeypatch):
    eng = FakeEngine()
    monkeypatch.setattr(doc_material, "_get_engine", lambda: eng)

    assert doc_material.cargar_fuentes([]) == []
    assert eng.log == []


def test_el_material_vacio_se_descarta(monkeypatch):
    eng = FakeEngine({"FROM pagina_web.ChatbotDocMaterial": FakeResult([
        _material(1, texto="   "),
        _material(2, tipo="archivo", datos=None),
        _material(3, texto="esto sí"),
    ])})
    monkeypatch.setattr(doc_material, "_get_engine", lambda: eng)

    fuentes = doc_material.cargar_fuentes([1, 2, 3])

    assert len(fuentes) == 1
    assert fuentes[0]["texto"] == "esto sí"


def test_consumir_borra_solo_el_material_del_bot_indicado():
    """El DELETE lleva el chatbot_id además del id: un id de otro bot no se toca."""
    eng = FakeEngine()
    conn = eng.begin()

    doc_material.consumir(conn, 3, [10, 11])

    sql, params = eng.sql_con("DELETE FROM pagina_web.ChatbotDocMaterial")[0]
    assert "chatbot_id = :cid" in sql
    assert params["cid"] == 3 and params["ids"] == [10, 11]


def test_consumir_sin_material_no_ejecuta_nada():
    eng = FakeEngine()
    assert doc_material.consumir(eng.begin(), 3, []) == 0
    assert eng.log == []


# ------------------------------------------------- material dentro del job

def test_el_job_procesa_la_bandeja_y_lo_que_vino_en_la_request(monkeypatch):
    """La bandeja va PRIMERO: es el material más viejo, el orden es cronológico."""
    monkeypatch.setattr(doc_material, "cargar_fuentes",
                        lambda ids: [{"tipo": "texto", "texto": "de la bandeja"}])

    fuentes = doc_jobs._fuentes_del_job({
        "material_ids": [1],
        "fuentes": [{"tipo": "texto", "texto": "pegado recién"}],
    })

    assert [f["texto"] for f in fuentes] == ["de la bandeja", "pegado recién"]


def test_si_el_material_ya_no_esta_el_job_falla_con_un_motivo_legible(monkeypatch):
    """Alguien vació la bandeja mientras el job esperaba en la cola: el usuario tiene
    que leer qué pasó, no un job 'done' con cero documentos."""
    eng = FakeEngine()
    monkeypatch.setattr(doc_jobs, "_get_engine", lambda: eng)
    monkeypatch.setattr(doc_material, "cargar_fuentes", lambda ids: [])

    doc_jobs.ejecutar_doc_job(4, "formatear", 3, '{"material_ids": [1, 2], "fuentes": []}', None)

    fallidos = eng.sql_con("status = 'failed'")
    assert len(fallidos) == 1
    assert "material a procesar ya no está disponible" in fallidos[0][1]["err"]


def test_el_resultado_dice_que_material_entro(monkeypatch):
    """Sin esos ids, el guardado no sabría qué borrar de la bandeja."""
    import AuditorIA.asistente_docs as ad
    eng = FakeEngine()
    monkeypatch.setattr(doc_jobs, "_get_engine", lambda: eng)
    monkeypatch.setattr(doc_material, "cargar_fuentes",
                        lambda ids: [{"tipo": "texto", "texto": "material"}])
    monkeypatch.setattr(ad, "formatear_documento",
                        lambda **kw: {"documentos": [{"titulo": "T", "markdown": "# T"}]})

    doc_jobs.ejecutar_doc_job(5, "formatear", 3, '{"material_ids": [7, 8]}', None)

    import json
    guardado = json.loads(eng.sql_con("status = 'done'")[0][1]["res"])
    assert guardado["material_ids"] == [7, 8]


# --------------------------------------------- congelado de la lista de ids

def test_material_ids_null_toma_todo_lo_pendiente(monkeypatch):
    monkeypatch.setattr(doc_material, "ids_pendientes", lambda cid: [1, 2, 3])

    assert _material_de_la_corrida(3, None, []) == [1, 2, 3]


def test_un_id_que_ya_no_esta_en_la_bandeja_es_404(monkeypatch):
    """La pantalla quedó vieja: mejor decirlo que procesar en silencio otra cosa."""
    monkeypatch.setattr(doc_material, "ids_pendientes", lambda cid: [1, 2])

    with pytest.raises(HTTPException) as exc:
        _material_de_la_corrida(3, [2, 99], [])

    assert exc.value.status_code == 404
    assert "99" in exc.value.detail


def test_sin_bandeja_ni_fuentes_no_se_encola_nada(monkeypatch):
    monkeypatch.setattr(doc_material, "ids_pendientes", lambda cid: [])

    with pytest.raises(HTTPException) as exc:
        _material_de_la_corrida(3, None, [])

    assert exc.value.status_code == 422


def test_con_fuentes_en_la_request_alcanza_aunque_la_bandeja_este_vacia(monkeypatch):
    monkeypatch.setattr(doc_material, "ids_pendientes", lambda cid: [])

    assert _material_de_la_corrida(3, None, [{"tipo": "texto", "texto": "x"}]) == []


# ------------------------------------------------------ cambios sin indexar

AYER = datetime(2026, 8, 19, 10, 0, 0)
HOY = AYER + timedelta(days=1)


@pytest.mark.parametrize("tocado,indexado,docs,esperado", [
    (HOY, AYER, 3, True),      # se editó después de indexar
    (AYER, HOY, 3, False),     # el índice es posterior al último cambio
    (AYER, None, 3, True),     # hay documentos y nunca se indexó
    (None, None, 0, False),    # bot sin documentos: no hay nada que indexar
    (None, AYER, 3, False),    # documentos prestados sin fecha propia: no inventamos pendiente
])
def test_cuando_el_indice_quedo_viejo(tocado, indexado, docs, esperado):
    assert _hay_cambios_sin_indexar(tocado, indexado, docs) is esperado
