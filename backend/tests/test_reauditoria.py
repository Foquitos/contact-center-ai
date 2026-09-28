"""Reauditar un llamado sin perder la corrida anterior.

Lo que se prueba acá es la parte pura y las reglas de negocio que NO dependen de
Gemini ni de SQL Server: qué se considera un cambio entre dos corridas, y las
salvaguardas que impiden reemplazar una auditoría publicada cuando no corresponde.

La decisión de diseño que sostiene todo el módulo —una sola fila por llamado en
`calidad.Auditorias`, siempre la última versión, y lo anterior archivado en
`calidad.AuditoriaVersiones`— es lo que hace que el SP del listado, los dashboards,
los exports y el promedio del operador no tengan que filtrar nada: no hay
duplicados que filtrar.

Correr: pytest tests/test_reauditoria.py -m "not tokens"
"""
import pytest

from AuditorIA import reauditoria


# --------------------------------------------------------------------------- #
# Qué cambió entre dos corridas                                                #
# --------------------------------------------------------------------------- #
def test_lista_los_atributos_que_cambiaron_con_su_nombre():
    """Sin el "antes y después" por atributo, el usuario solo ve que el puntaje se
    movió y no sabe por culpa de qué criterio: no puede decidir si el prompt nuevo
    está mejor o peor."""
    cambios = reauditoria.comparar_detalles(
        {1: "OK", 2: "NO OK"}, {1: "NO OK", 2: "NO OK"},
        {1: "Verifica identidad", 2: "Saluda"},
    )
    assert len(cambios) == 1
    assert cambios[0]["nombre"] == "Verifica identidad"
    assert cambios[0]["antes"] == "OK"
    assert cambios[0]["despues"] == "NO OK"


def test_no_marca_como_cambio_lo_que_solo_se_escribe_distinto():
    """Se usa la misma normalización que la medición: si un `['a','b']` y un
    `["b","a"]` figuraran como cambio, cada reauditoría mostraría diferencias
    inventadas y nadie volvería a mirar el listado."""
    assert reauditoria.comparar_detalles({1: "['a', 'b']"}, {1: '["b","a"]'}) == []
    assert reauditoria.comparar_detalles({1: "3"}, {1: "3.0"}) == []


def test_un_atributo_que_dejo_de_responderse_es_un_cambio():
    """La IA nueva omitió un atributo opcional que la vieja sí contestaba: cambia el
    puntaje, así que tiene que verse."""
    cambios = reauditoria.comparar_detalles({1: "OK"}, {}, {1: "Despide"})
    assert len(cambios) == 1
    assert cambios[0]["antes"] == "OK"
    assert cambios[0]["despues"] is None


def test_un_atributo_nuevo_tambien_es_un_cambio():
    cambios = reauditoria.comparar_detalles({}, {7: "NO OK"}, {7: "Ofrece alternativa"})
    assert [c["atributo_id"] for c in cambios] == [7]
    assert cambios[0]["antes"] is None


def test_dos_corridas_iguales_no_generan_ruido():
    assert reauditoria.comparar_detalles({1: "OK", 2: "OK"}, {1: "OK", 2: "OK"}) == []


def test_los_cambios_salen_ordenados_por_atributo():
    """Estable entre corridas: si el orden bailara, comparar dos reauditorías del
    mismo llamado sería imposible de leer."""
    cambios = reauditoria.comparar_detalles({3: "a", 1: "a"}, {3: "b", 1: "b"})
    assert [c["atributo_id"] for c in cambios] == [1, 3]


# --------------------------------------------------------------------------- #
# Salvaguardas                                                                 #
# --------------------------------------------------------------------------- #
class _EngineSinTablas:
    """Base donde la migración del historial NO está aplicada."""
    def connect(self):
        raise RuntimeError("sin base")


def test_sin_la_migracion_no_se_reaudita():
    """Reauditar sin poder archivar la corrida anterior sería destruir evidencia:
    tiene que fallar con un mensaje que diga qué falta, no reemplazar igual."""
    engine = _EngineSinTablas()
    assert reauditoria.hay_versionado(engine) is False
    with pytest.raises(RuntimeError) as e:
        reauditoria.reauditar(engine, auditoria_ids=[1], usuario_id=9)
    assert "2026-08-27" in str(e.value)


def test_el_historial_no_se_cae_si_la_base_no_responde():
    """Es información de contexto en un listado: si falla, la pantalla tiene que
    seguir funcionando sin el badge de versiones."""
    assert reauditoria.versiones_por_auditoria(_EngineSinTablas(), [1, 2]) == {}


def test_sin_ids_no_se_consulta_nada():
    assert reauditoria.versiones_por_auditoria(_EngineSinTablas(), []) == {}


# --------------------------------------------------------------------------- #
# Historial: el diff de cada corrida contra la anterior                        #
# --------------------------------------------------------------------------- #
def test_cada_corrida_se_compara_contra_la_anterior_y_no_contra_si_misma():
    """El armado del historial convierte los detalles a lista para la pantalla. Si
    esa conversión pasara en la misma pasada que el diff, la corrida siguiente
    compararía contra una lista en vez de contra un diccionario y el historial
    mostraría "todo cambió" en cada corrida."""
    versiones = [
        {"Numero": 1, "detalles": {1: "OK", 2: "OK"}},
        {"Numero": 2, "detalles": {1: "NO OK", 2: "OK"}},
        {"Numero": 3, "detalles": {1: "NO OK", 2: "NO OK"}},
    ]
    nombres = {1: "Verifica identidad", 2: "Saluda"}

    for i, version in enumerate(versiones):
        anterior = versiones[i - 1]["detalles"] if i > 0 else {}
        version["cambios"] = (
            reauditoria.comparar_detalles(anterior, version["detalles"], nombres) if i > 0 else []
        )

    assert versiones[0]["cambios"] == []
    assert [c["nombre"] for c in versiones[1]["cambios"]] == ["Verifica identidad"]
    assert [c["nombre"] for c in versiones[2]["cambios"]] == ["Saluda"]


# --------------------------------------------------------------------------- #
# Reauditoría en modo Batch                                                    #
# --------------------------------------------------------------------------- #
def test_reauditar_batch_sin_llamados_con_audio(monkeypatch):
    """Si ningún llamado tiene audio conservado, devuelve SIN_LLAMADOS sin fallar."""
    monkeypatch.setattr(reauditoria, "hay_versionado", lambda engine: True)
    monkeypatch.setattr(reauditoria, "preparar", lambda engine, ids: {
        "listas": [],
        "rechazadas": [{"auditoria_id": 1, "motivo": "No se conserva el audio"}],
        "plantillas": [],
    })
    resultado = reauditoria.reauditar_batch(_EngineSinTablas(), auditoria_ids=[1], usuario_id=9)
    assert resultado["status"] == "SIN_LLAMADOS"
    assert resultado["encoladas"] == 0
    assert len(resultado["rechazadas"]) == 1


def test_reauditar_batch_encola_por_plantilla(monkeypatch):
    """Agrupa por PlantillaID y llama a calidad_batch con los metadatos correctos."""
    monkeypatch.setattr(reauditoria, "hay_versionado", lambda engine: True)
    monkeypatch.setattr(reauditoria, "preparar", lambda engine, ids: {
        "listas": [
            {
                "auditoria_id": 101, "IdAplicativo": "app_1", "operadorUsuario": "Op1",
                "fecha_interaccion": "2026-08-30", "sentido_interaccion": "Entrante",
                "tipificacion_interaccion": "Consulta", "duracion_segundos": 120,
                "comentario_interaccion": None, "PlantillaID": 5, "EmpresaID": 1, "CampanaID": 2,
                "ruta_audio": "/tmp/fake1.ogg",
            },
            {
                "auditoria_id": 102, "IdAplicativo": "app_2", "operadorUsuario": "Op2",
                "fecha_interaccion": "2026-08-30", "sentido_interaccion": "Saliente",
                "tipificacion_interaccion": "Venta", "duracion_segundos": 180,
                "comentario_interaccion": None, "PlantillaID": 5, "EmpresaID": 1, "CampanaID": 2,
                "ruta_audio": "/tmp/fake2.ogg",
            },
        ],
        "rechazadas": [],
        "plantillas": [5],
    })

    llamadas_calidad_batch = []
    def fake_calidad_batch(engine, df, plantilla_id, gemini_api, user_id, contexto_ejecucion, transcribir=False):
        llamadas_calidad_batch.append({
            "plantilla_id": plantilla_id,
            "df": df,
            "contexto": contexto_ejecucion,
        })
        return ["batches/fake_batch_123"]

    from AuditorIA import gemini
    monkeypatch.setattr(gemini, "calidad_batch", fake_calidad_batch)

    resultado = reauditoria.reauditar_batch(
        _EngineSinTablas(), auditoria_ids=[101, 102], usuario_id=42, motivo="Test batch"
    )

    assert resultado["status"] == "BATCH_ENCOLADO"
    assert resultado["encoladas"] == 2
    assert resultado["batches"] == ["batches/fake_batch_123"]
    assert len(llamadas_calidad_batch) == 1
    assert llamadas_calidad_batch[0]["plantilla_id"] == 5
    assert len(llamadas_calidad_batch[0]["df"]) == 2
    assert "es_reauditoria" in llamadas_calidad_batch[0]["df"].columns
    assert list(llamadas_calidad_batch[0]["df"]["auditoria_id"]) == [101, 102]


class _EngineAtributosVacios:
    """Base que responde la lectura de atributos con cero filas (y nada más)."""
    class _Conexion:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            class _Resultado:
                def mappings(self):
                    return self

                def all(self):
                    return []
            return _Resultado()

    def connect(self):
        return self._Conexion()


def test_reauditar_sincronico_desempaqueta_lo_que_devuelve_gemini(monkeypatch):
    """apply_auditoria_threads devuelve 5 valores desde que suma el nivel de
    razonamiento (2026-08-19). Este camino desempaquetaba 4 y reventaba con
    ValueError antes de guardar nada: el modo sincrónico no funcionaba."""
    from AuditorIA import gemini

    monkeypatch.setattr(reauditoria, "hay_versionado", lambda engine: True)
    monkeypatch.setattr(reauditoria, "preparar", lambda engine, ids: {
        "listas": [{
            "auditoria_id": 101, "IdAplicativo": "app_1", "operadorUsuario": "Op1",
            "fecha_interaccion": "2026-08-30", "sentido_interaccion": "Entrante",
            "tipificacion_interaccion": "Consulta", "duracion_segundos": 120,
            "comentario_interaccion": None, "PlantillaID": 5, "EmpresaID": 1, "CampanaID": 2,
            "ruta_audio": "/tmp/fake1.ogg", "es_chat": False,
        }],
        "rechazadas": [],
        "plantillas": [5],
    })
    # FALLIDO: así no llega a _reemplazar (que escribiría en la base).
    monkeypatch.setattr(gemini, "apply_auditoria_threads", lambda df, *a, **k: (
        df.assign(status_auditoria="FALLIDO"), None, {}, "gemini-fake", "MEDIUM",
    ))

    resultado = reauditoria.reauditar(_EngineAtributosVacios(), auditoria_ids=[101], modo="sync")

    assert resultado["reauditadas"] == 0
    assert resultado["modelo"] == "gemini-fake"
    assert "falló al auditar" in resultado["rechazadas"][0]["motivo"]


def test_reauditar_delega_en_batch_por_defecto(monkeypatch):
    """reauditar() por defecto usa modo='batch'."""
    llamado_batch = {}
    def fake_reauditar_batch(engine, auditoria_ids, usuario_id=None, motivo=None):
        llamado_batch["ejecutado"] = True
        return {"status": "BATCH_ENCOLADO", "batches": ["batches/x"]}

    monkeypatch.setattr(reauditoria, "reauditar_batch", fake_reauditar_batch)

    resultado = reauditoria.reauditar(_EngineSinTablas(), auditoria_ids=[1, 2])
    assert llamado_batch.get("ejecutado") is True
    assert resultado["status"] == "BATCH_ENCOLADO"


# --------------------------------------------------------------------------- #
# Reauditoría de Chats (WhatsApp / Mitrol)                                    #
# --------------------------------------------------------------------------- #
def test_es_chat_detecta_por_patron():
    assert reauditoria.es_chat("260915145131519_CHT_23000_2") is True
    assert reauditoria.es_chat("123456_chat") is True
    assert reauditoria.es_chat("260915145131519_VOX_23000_2") is False
    assert reauditoria.es_chat(None) is False
    assert reauditoria.es_chat("") is False


def test_preparar_acepta_chats_sin_audio_en_disco(monkeypatch):
    """Los chats no tienen audio físico en AudioAuditoria: preparar() los
    debe marcar como elegibles (es_chat=True) en vez de rechazarlos."""
    from AuditorIA import audio_store

    class _FakeEngine:
        def connect(self):
            raise RuntimeError("sin DB")

    monkeypatch.setattr(reauditoria, "_contexto", lambda engine, ids: {
        201: {
            "AuditoriaID": 201, "IdAplicativo": "260915145131519_CHT_23000_2",
            "operadorUsuario": "Mateo", "fecha_interaccion": "2026-09-15",
            "PlantillaID": 29, "sentido_interaccion": "Entrante",
            "tipificacion_interaccion": "Turno", "duracion_segundos": None,
            "comentario_interaccion": None, "EmpresaID": 1, "CampanaID": 2,
        },
        202: {
            "AuditoriaID": 202, "IdAplicativo": "260915145131519_VOX_23000_1",
            "operadorUsuario": "Ana", "fecha_interaccion": "2026-09-15",
            "PlantillaID": 29, "sentido_interaccion": "Entrante",
            "tipificacion_interaccion": "Turno", "duracion_segundos": 60,
            "comentario_interaccion": None, "EmpresaID": 1, "CampanaID": 2,
        }
    })
    # Ninguno tiene audio en el store
    monkeypatch.setattr(audio_store, "resolver_audio", lambda engine, id_app: None)

    datos = reauditoria.preparar(_FakeEngine(), [201, 202])

    assert len(datos["listas"]) == 1
    assert datos["listas"][0]["auditoria_id"] == 201
    assert datos["listas"][0]["es_chat"] is True
    assert datos["listas"][0]["ruta_audio"] is None

    assert len(datos["rechazadas"]) == 1
    assert datos["rechazadas"][0]["auditoria_id"] == 202
    assert "No se conserva el audio" in datos["rechazadas"][0]["motivo"]


def test_reauditar_batch_materializa_chat_y_asigna_audio_dir(monkeypatch, tmp_path):
    """Al reauditar en batch, resolver_chat materializa el .json y el DataFrame
    enviado a calidad_batch lleva esa ruta como audio_dir."""
    monkeypatch.setattr(reauditoria, "hay_versionado", lambda engine: True)
    monkeypatch.setattr(reauditoria, "preparar", lambda engine, ids: {
        "listas": [
            {
                "auditoria_id": 301, "IdAplicativo": "chat_mitrol_1_2",
                "operadorUsuario": "OpChat", "fecha_interaccion": "2026-09-15",
                "sentido_interaccion": "Entrante", "tipificacion_interaccion": "Consulta",
                "duracion_segundos": None, "comentario_interaccion": None,
                "PlantillaID": 29, "EmpresaID": 1, "CampanaID": 2,
                "ruta_audio": None, "es_chat": True,
            }
        ],
        "rechazadas": [],
        "plantillas": [29],
    })

    fake_json_path = str(tmp_path / "chat_mitrol_1_2_chat.json")
    with open(fake_json_path, "w", encoding="utf-8") as f:
        f.write('{"conversaciones": []}')

    monkeypatch.setattr(reauditoria, "resolver_chat", lambda engine, id_ap, destino_dir=None: fake_json_path)

    llamadas_calidad_batch = []
    def fake_calidad_batch(engine, df, plantilla_id, gemini_api, user_id, contexto_ejecucion, transcribir=False):
        llamadas_calidad_batch.append(df.to_dict(orient="records"))
        return ["batches/chat_batch_001"]

    from AuditorIA import gemini
    monkeypatch.setattr(gemini, "calidad_batch", fake_calidad_batch)

    res = reauditoria.reauditar_batch(_EngineSinTablas(), auditoria_ids=[301])
    assert res["status"] == "BATCH_ENCOLADO"
    assert res["encoladas"] == 1
    assert len(llamadas_calidad_batch) == 1
    assert llamadas_calidad_batch[0][0]["audio_dir"] == fake_json_path
    assert llamadas_calidad_batch[0][0]["audio_dir"].endswith(".json")


