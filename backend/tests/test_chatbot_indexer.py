"""Tests offline del indexador: swap atómico de alias, publicación del índice y
manejo de fallos (el índice viejo debe seguir sirviendo).

Qdrant y la BD se mockean; la construcción real del índice (construir_indice)
se cubre E2E con reindex_all.py en el cutover.
"""
from datetime import datetime
from unittest.mock import MagicMock

import pytest

import app.chatbot_indexer as indexer
from app.chatbot_config import ChatbotConfig


# ------------------------------------------------------------------ fakes

def _cfg(slug="voltara", index_version=1):
    return ChatbotConfig(
        id=1, slug=slug, nombre=slug.title(), descripcion=None,
        system_prompt="p", grupo=None, permission_code=f"chatbot:{slug}",
        activo=True, index_version=index_version,
        index_status="ready" if index_version else "never_indexed",
        last_indexed_at=None, updated_at=datetime(2026, 1, 1),
    )


class RecConn:
    def __init__(self, log):
        self._log = log

    def execute(self, query, params=None):
        self._log.append((" ".join(str(query).split()), params or {}))
        return MagicMock()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class RecEngine:
    """Engine fake que registra cada (sql, params) ejecutado."""
    def __init__(self):
        self.log = []

    def begin(self):
        return RecConn(self.log)

    def connect(self):
        return RecConn(self.log)

    def sql_con(self, fragmento):
        return [(s, p) for s, p in self.log if fragmento in s]


def _alias_mock(nombres):
    """get_aliases() de qdrant con los alias dados."""
    aliases = []
    for n in nombres:
        a = MagicMock()
        a.alias_name = n
        aliases.append(a)
    resp = MagicMock()
    resp.aliases = aliases
    return resp


# ------------------------------------------------------------------ _swap_alias

def test_swap_alias_existente_delete_y_create_en_una_llamada():
    client = MagicMock()
    client.get_aliases.return_value = _alias_mock(["bot_voltara", "bot_otro"])

    indexer._swap_alias(client, "bot_voltara", "bot_voltara_v2")

    # Atómico: UNA sola llamada con delete + create.
    assert client.update_collection_aliases.call_count == 1
    ops = client.update_collection_aliases.call_args.kwargs["change_aliases_operations"]
    assert len(ops) == 2
    assert ops[0].delete_alias.alias_name == "bot_voltara"
    assert ops[1].create_alias.collection_name == "bot_voltara_v2"
    assert ops[1].create_alias.alias_name == "bot_voltara"
    client.delete_collection.assert_not_called()


def test_swap_alias_primer_indexado_solo_create():
    client = MagicMock()
    client.get_aliases.return_value = _alias_mock([])
    client.collection_exists.return_value = False

    indexer._swap_alias(client, "bot_nuevo", "bot_nuevo_v1")

    ops = client.update_collection_aliases.call_args.kwargs["change_aliases_operations"]
    assert len(ops) == 1
    assert ops[0].create_alias.collection_name == "bot_nuevo_v1"
    client.delete_collection.assert_not_called()


def test_swap_alias_borra_coleccion_real_con_nombre_de_alias():
    """Si una instanciación incorrecta creó una colección REAL con el nombre del
    alias, hay que borrarla antes o el create_alias falla."""
    client = MagicMock()
    client.get_aliases.return_value = _alias_mock([])
    client.collection_exists.return_value = True

    indexer._swap_alias(client, "bot_voltara", "bot_voltara_v1")

    client.delete_collection.assert_called_once_with("bot_voltara")
    ops = client.update_collection_aliases.call_args.kwargs["change_aliases_operations"]
    assert len(ops) == 1


# ------------------------------------------------------------- ejecutar_index_job

@pytest.fixture()
def entorno(monkeypatch):
    """Mockea BD, Qdrant y los pasos pesados del job."""
    engine = RecEngine()
    qdrant = MagicMock()
    qdrant.collection_exists.return_value = False
    monkeypatch.setattr(indexer, "_get_engine", lambda: engine)
    monkeypatch.setattr(indexer, "_get_qdrant_client", lambda: qdrant)
    monkeypatch.setattr(indexer, "_preparar_docs", lambda cfg, docs_md: None)
    monkeypatch.setattr(indexer, "construir_indice", lambda cfg, v, c: 5)
    monkeypatch.setattr(indexer, "_swap_alias", MagicMock())
    monkeypatch.setattr(indexer, "_limpiar_versiones_viejas", lambda cfg, v, c: None)
    monkeypatch.setattr(indexer.SemanticCache, "clear", lambda self: None)
    return engine, qdrant


def test_job_exitoso_publica_version_nueva(entorno, monkeypatch):
    engine, _ = entorno
    cfg = _cfg(index_version=3)
    docs_md = [{"id": 1, "titulo": "Manual", "orden": 1, "contenido_md": "# Manual\n\nTexto."}]
    monkeypatch.setattr(indexer, "_cargar_config_y_docs", lambda cid: (cfg, docs_md))

    indexer.ejecutar_index_job(job_id=10, chatbot_id=1)

    # Publica en ChatbotIndexState (por entorno), no en Chatbots.
    publicaciones = engine.sql_con("UPDATE pagina_web.ChatbotIndexState")
    assert any(p[1].get("v") == 4 for p in publicaciones)  # v3 -> v4
    assert not engine.sql_con("UPDATE pagina_web.Chatbots")

    completados = engine.sql_con("status = 'completed'")
    assert len(completados) == 1 and completados[0][1]["jid"] == 10
    # Bot ya 'ready': no debe pasar por estado 'indexing'.
    assert not any(p[1].get("st") == "indexing" for p in publicaciones)


def test_job_fallido_no_toca_el_indice_vigente(entorno, monkeypatch):
    """Fallo con índice previo: el job queda failed con error legible y NO se
    actualiza index_version/index_status (el alias viejo sigue sirviendo)."""
    engine, _ = entorno
    cfg = _cfg(index_version=2)
    docs_md = [{"id": 1, "titulo": "Manual", "orden": 1, "contenido_md": "# Manual\n\nTexto."}]
    monkeypatch.setattr(indexer, "_cargar_config_y_docs", lambda cid: (cfg, docs_md))

    def preparado_roto(cfg, docs_md):
        raise RuntimeError("403: el disco rechazó la escritura")

    monkeypatch.setattr(indexer, "_preparar_docs", preparado_roto)

    indexer.ejecutar_index_job(job_id=11, chatbot_id=1)

    fallidos = engine.sql_con("status = 'failed'")
    assert len(fallidos) == 1
    assert "403" in fallidos[0][1]["error"]
    # Índice previo intacto: con index_version>0 no se toca el estado del entorno.
    assert not engine.sql_con("pagina_web.ChatbotIndexState")


def test_primer_indexado_fallido_marca_el_bot(entorno, monkeypatch):
    engine, _ = entorno
    cfg = _cfg(index_version=0)
    docs_md = [{"id": 1, "titulo": "Manual", "orden": 1, "contenido_md": "# Manual\n\nTexto."}]
    monkeypatch.setattr(indexer, "_cargar_config_y_docs", lambda cid: (cfg, docs_md))
    monkeypatch.setattr(
        indexer, "construir_indice",
        MagicMock(side_effect=RuntimeError("sin nodos")),
    )

    indexer.ejecutar_index_job(job_id=12, chatbot_id=1)

    # Primero pasó a 'indexing' (nunca sirvió) y al fallar quedó 'failed', ambos en el
    # estado por entorno; nunca publicó una versión.
    estados = engine.sql_con("pagina_web.ChatbotIndexState")
    sts = [p.get("st") for _, p in estados]
    assert "indexing" in sts
    assert "failed" in sts
    assert engine.sql_con("status = 'failed'")  # el job
    assert all(p.get("v") is None for _, p in estados)


def test_bot_sin_docs_falla_con_mensaje_claro(entorno, monkeypatch):
    engine, _ = entorno
    monkeypatch.setattr(indexer, "_cargar_config_y_docs", lambda cid: (_cfg(), []))

    indexer.ejecutar_index_job(job_id=13, chatbot_id=1)

    fallidos = engine.sql_con("status = 'failed'")
    assert len(fallidos) == 1
    assert "documentos" in fallidos[0][1]["error"]


def test_job_con_solo_docs_md_indexa(entorno, monkeypatch):
    """Un bot con documentos markdown activos debe indexar."""
    engine, _ = entorno
    cfg = _cfg(index_version=1)
    docs_md = [{"id": 7, "titulo": "Altas", "orden": 1, "contenido_md": "# Altas\n\nProcedimiento."}]
    monkeypatch.setattr(indexer, "_cargar_config_y_docs", lambda cid: (cfg, docs_md))

    indexer.ejecutar_index_job(job_id=14, chatbot_id=1)

    completados = engine.sql_con("status = 'completed'")
    assert len(completados) == 1 and completados[0][1]["jid"] == 14
    assert not engine.sql_con("status = 'failed'")


def test_carga_incluye_docs_compartidos_de_otro_bot(monkeypatch):
    """El SQL que arma el conocimiento del bot tiene que traer los documentos prestados
    (ChatbotDocVinculo), no solo los propios: es lo que permite que Voltara Digital indexe
    los procedimientos del telefónico sin duplicarlos."""
    engine = RecEngine()
    monkeypatch.setattr(indexer, "_get_engine", lambda: engine)
    indexer._cargar_config_y_docs(chatbot_id=7)

    consultas = engine.sql_con("ChatbotDocMarkdown")
    assert consultas, "no se consultaron los documentos markdown"
    sql = consultas[0][0]
    assert "ChatbotDocVinculo" in sql, "la consulta ignora los documentos compartidos"
    assert "m.chatbot_id = :cid" in sql, "debe seguir trayendo los propios"


def test_preparar_docs_escribe_markdown_de_bd(tmp_path, monkeypatch):
    """_preparar_docs materializa el markdown in-app a disco con nombre estable y,
    si el contenido no arranca con encabezado, antepone el título como H1."""
    cfg = _cfg()
    monkeypatch.setattr(ChatbotConfig, "docs_dir", property(lambda self: str(tmp_path)))
    indexer._preparar_docs(
        cfg,
        docs_md=[
            {"id": 3, "titulo": "Reintegros", "orden": 2, "contenido_md": "Texto sin encabezado."},
            {"id": 4, "titulo": "Con H1", "orden": 1, "contenido_md": "# Ya tiene\n\nCuerpo."},
        ],
    )
    escrito = {p.name: p.read_text(encoding="utf-8") for p in tmp_path.glob("*.md")}
    assert "02_dbdoc_3.md" in escrito and "01_dbdoc_4.md" in escrito
    assert escrito["02_dbdoc_3.md"].startswith("# Reintegros\n\n")   # se antepone el título
    assert escrito["01_dbdoc_4.md"].startswith("# Ya tiene")          # ya tenía encabezado, no se toca


# ------------------------------------------ cómo se parte el markdown en nodos

def _nodos(tmp_path, monkeypatch, contenido):
    """Los nodos que indexaría un bot con este único documento (sin Qdrant ni embeddings)."""
    monkeypatch.setattr(ChatbotConfig, "docs_dir", property(lambda self: str(tmp_path)))
    indexer._preparar_docs(_cfg(), [{"id": 1, "titulo": "Manual", "orden": 1, "contenido_md": contenido}])
    return indexer._nodos_desde_documentos(indexer._leer_documentos(str(tmp_path)))


_SECCION_LARGA = "### Flag 003 - Marca No Molestar\n\n" + "\n".join(
    f"Paso {i}: verificá el dato número {i} en la pantalla correspondiente del sistema."
    for i in range(80)
) + "\n\nÚltimos 8 dígitos de tarjetas de débito que pertenezcan a la RED BANELCO."


def test_cada_pedazo_de_una_seccion_larga_arranca_con_su_encabezado(tmp_path, monkeypatch):
    """El SentenceSplitter solo le deja el título al primer pedazo. Sin repetirlo, el
    pedazo con las preguntas de la Flag 003 no decía de qué tema era y no llegaba al bot."""
    contenido = f"# Manual\n\n{_SECCION_LARGA}\n\n### Caso Monitor\n\nVerificar consumos."
    pedazos = [n for n in _nodos(tmp_path, monkeypatch, contenido) if "Paso" in n.get_content()]

    assert len(pedazos) >= 3, "la sección tiene que partirse para que el test valga"
    for pedazo in pedazos:
        texto = pedazo.get_content()
        assert texto.startswith("### Flag 003 - Marca No Molestar\n")
        assert texto.count("### Flag 003 - Marca No Molestar") == 1  # el primero no lo duplica
    ultimo = next(p for p in pedazos if "RED BANELCO" in p.get_content())
    assert ultimo.get_content().startswith("### Flag 003")
    assert all(indexer._META_ENCABEZADO not in p.metadata for p in pedazos)


def test_desambiguacion_ve_un_solo_tema_en_los_pedazos_de_una_seccion(tmp_path, monkeypatch):
    """El encabezado se repite exacto: con "(continuación)" la desambiguación contaría
    dos temas donde hay uno y ofrecería elegir entre la misma sección."""
    from app import chatbot_desambiguacion as desamb

    pedazos = [n for n in _nodos(tmp_path, monkeypatch, f"# Manual\n\n{_SECCION_LARGA}")
               if "Paso" in n.get_content()]
    rutas = {tuple(desamb._ruta(p)) for p in pedazos}
    assert rutas == {("Manual", "Flag 003 - Marca No Molestar")}


def test_nodos_de_solo_encabezado_no_se_indexan(tmp_path, monkeypatch):
    """"# Manual" seguido de "## Capítulo" daba nodos sin cuerpo que ocupaban lugar entre
    los candidatos. El título no se pierde: viaja en el header_path de los hijos."""
    contenido = "# Manual\n\n## Capítulo\n\n---\n\n### Tema\n\nCuerpo del tema."
    nodos = _nodos(tmp_path, monkeypatch, contenido)

    assert [n.get_content() for n in nodos] == ["### Tema\n\nCuerpo del tema."]
    assert "Capítulo" in nodos[0].metadata["header_path"]


# ------------------------------------------- 429 de Gemini: se reintenta solo

class _ErrorDeCupo(RuntimeError):
    """Doble del ClientError del SDK: trae `code` como los errores de la API."""
    def __init__(self, mensaje, code=429):
        super().__init__(mensaje)
        self.code = code


class ConnConNota(RecConn):
    """RecConn que además devuelve la nota ya guardada en el job (el SELECT del
    contador de intentos)."""
    def __init__(self, log, nota):
        super().__init__(log)
        self._nota = nota

    def execute(self, query, params=None):
        resultado = super().execute(query, params)
        resultado.scalar.return_value = self._nota
        return resultado


class EngineConNota(RecEngine):
    def __init__(self, nota=None):
        super().__init__()
        self.nota = nota

    def begin(self):
        return ConnConNota(self.log, self.nota)

    def connect(self):
        return ConnConNota(self.log, self.nota)


@pytest.mark.parametrize("error, es_cupo", [
    (_ErrorDeCupo("429 RESOURCE_EXHAUSTED. Resource exhausted."), True),
    (RuntimeError("429 RESOURCE_EXHAUSTED. {'error': {'code': 429}}"), True),
    (RuntimeError("503 UNAVAILABLE. The model is overloaded."), True),
    (RuntimeError("No se materializó ningún documento markdown."), False),
    (RuntimeError("403: el disco rechazó la escritura"), False),
])
def test_reconoce_cuando_gemini_no_tiene_cupo(error, es_cupo):
    assert indexer._es_falta_de_cupo(error) is es_cupo


def test_sin_cupo_el_job_vuelve_a_la_cola_y_no_figura_como_fallido(entorno, monkeypatch):
    """Un 429 al generar los embeddings no es un problema del material: se arregla
    esperando. El job vuelve a 'pending' y el panel muestra 'pendiente', no 'falló'."""
    engine = EngineConNota(nota=None)
    monkeypatch.setattr(indexer, "_get_engine", lambda: engine)
    monkeypatch.setattr(indexer, "_cargar_config_y_docs",
                        lambda cid: (_cfg(index_version=2),
                                     [{"id": 1, "titulo": "M", "orden": 1, "contenido_md": "# M"}]))
    monkeypatch.setattr(indexer, "construir_indice", MagicMock(
        side_effect=_ErrorDeCupo("429 RESOURCE_EXHAUSTED. Resource exhausted.")))

    resultado = indexer.ejecutar_index_job(job_id=20, chatbot_id=1)

    assert resultado == indexer.RESULTADO_REENCOLADO
    assert not engine.sql_con("status = 'failed'")
    reencolados = engine.sql_con("status = 'pending'")
    assert len(reencolados) == 1
    assert "intento 1 de" in reencolados[0][1]["nota"]


def test_agotados_los_intentos_el_job_si_queda_fallido(entorno, monkeypatch):
    """Los reintentos son acotados: si la cuota no vuelve, alguien tiene que enterarse."""
    tope = indexer.settings.INDEX_REINTENTOS_SIN_CUPO
    engine = EngineConNota(nota=f"Gemini no tenía cupo ... (intento {tope} de {tope}).")
    monkeypatch.setattr(indexer, "_get_engine", lambda: engine)
    monkeypatch.setattr(indexer, "_cargar_config_y_docs",
                        lambda cid: (_cfg(index_version=2),
                                     [{"id": 1, "titulo": "M", "orden": 1, "contenido_md": "# M"}]))
    monkeypatch.setattr(indexer, "construir_indice", MagicMock(
        side_effect=_ErrorDeCupo("429 RESOURCE_EXHAUSTED. Resource exhausted.")))

    resultado = indexer.ejecutar_index_job(job_id=21, chatbot_id=1)

    assert resultado == indexer.RESULTADO_FALLIDO
    assert len(engine.sql_con("status = 'failed'")) == 1


def test_al_reencolar_se_corta_el_tick(monkeypatch):
    """Si el tick siguiera drenando la cola, tomaría el mismo job al instante y
    quemaría los reintentos en el mismo minuto."""
    engine = RecEngine()
    claim = MagicMock(id=30, chatbot_id=1)
    monkeypatch.setattr(indexer, "_get_engine", lambda: engine)
    monkeypatch.setattr(RecConn, "execute",
                        lambda self, q, p=None: MagicMock(first=lambda: claim))
    monkeypatch.setattr(indexer.rag_settings, "configure_global_settings", lambda: None)
    corridas = []
    monkeypatch.setattr(indexer, "ejecutar_index_job",
                        lambda jid, cid: corridas.append(jid) or indexer.RESULTADO_REENCOLADO)

    indexer.procesar_cola_reindexado()

    assert corridas == [30]


def test_el_job_completado_no_arrastra_la_nota_del_reintento(entorno, monkeypatch):
    """Si el reintento sale bien, el panel no puede seguir mostrando el aviso viejo."""
    engine, _ = entorno
    monkeypatch.setattr(indexer, "_cargar_config_y_docs",
                        lambda cid: (_cfg(index_version=2),
                                     [{"id": 1, "titulo": "M", "orden": 1, "contenido_md": "# M"}]))

    indexer.ejecutar_index_job(job_id=22, chatbot_id=1)

    completados = engine.sql_con("status = 'completed'")
    assert len(completados) == 1
    assert "error = NULL" in completados[0][0]
