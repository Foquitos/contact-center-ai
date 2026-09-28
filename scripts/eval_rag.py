"""Evaluador de recuperación (RAG) de los chatbots: compara configuraciones y dice
cuál recupera mejor, sobre preguntas reales de los operadores.

Por qué existe: la única señal de calidad que teníamos eran las calificaciones del
chat (35 en 9 meses) y el ojo. Con esto se mide, contra un set dorado, si el chunk
correcto llega efectivamente al LLM — que es donde se rompen las respuestas.

Métricas por configuración (todas sobre el set dorado):
  recall_bruto   % de casos donde el documento esperado entró en el top_k del
                 retriever híbrido (BM25 + vectorial). Si esto es bajo, el
                 problema es de indexado/chunking, no del reranker.
  recall_final   % de casos donde SOBREVIVIÓ al reranker y llegó al LLM. La
                 caída contra recall_bruto es el daño que hace el reranker.
  pos_media      posición media del documento esperado tras el reranking (1 = primero).
  terminos       % de términos de `debe_contener` presentes en el contexto final.
  ruido          % de chunks entregados al LLM con score de reranker negativo
                 (el cross-encoder los juzga NO relevantes y aun así se envían).
  contaminacion  % de casos que traen un documento de la lista `no_debe_traer`.

El embedding de cada pregunta se calcula UNA vez y se reutiliza en todas las
configuraciones (y entre corridas, vía caché en disco): un barrido completo cuesta
lo mismo que una sola pasada. Cambiar top_k/top_n/reranker no re-embebe nada.

Uso (desde backend/, con el venv activo y Qdrant corriendo):
    python ../scripts/eval_rag.py voltara
    python ../scripts/eval_rag.py voltara --barrido
    python ../scripts/eval_rag.py voltara --barrido --reranker cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
    python ../scripts/eval_rag.py voltara --detalle          # caso por caso
    python ../scripts/eval_rag.py voltara --json out.json    # para diffear entre corridas

CONSUME TOKENS: un embedding por pregunta nueva (la primera corrida del set).
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.chatbot_config import ChatbotConfig  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("eval_rag")

RUTA_SETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend", "tests", "data")

# Barrido por defecto. top_k es cuánto recupera el híbrido; top_n cuánto sobrevive
# al reranker y llega al LLM. La config de producción sale del .env y se agrega sola.
BARRIDO_DEFAULT = [(12, 3), (12, 5), (12, 8), (20, 5), (20, 8), (20, 12)]


# --------------------------------------------------------------------- set dorado

@dataclass
class Caso:
    pregunta: str
    doc_esperado: str
    debe_contener: List[str] = field(default_factory=list)
    nota: str = ""
    veces: int = 1


@dataclass
class SetDorado:
    bot: str
    casos: List[Caso]
    no_debe_traer: List[str] = field(default_factory=list)


def cargar_set(slug: str, ruta: Optional[str] = None) -> SetDorado:
    ruta = ruta or os.path.join(RUTA_SETS, f"rag_golden_{slug}.json")
    if not os.path.exists(ruta):
        raise SystemExit(
            f"No hay set dorado para '{slug}' (buscado en {ruta}).\n"
            f"Creá uno con el formato de rag_golden_voltara.json."
        )
    with open(ruta, encoding="utf-8") as f:
        data = json.load(f)
    casos = [
        Caso(
            pregunta=c["pregunta"],
            doc_esperado=c["doc_esperado"],
            debe_contener=c.get("debe_contener", []),
            nota=c.get("nota", ""),
            veces=c.get("veces", 1),
        )
        for c in data["casos"]
    ]
    return SetDorado(bot=data.get("bot", slug), casos=casos,
                     no_debe_traer=data.get("no_debe_traer", []))


def cargar_casos_desde_vacios(slug: str, docs: Dict[str, str]) -> List[Caso]:
    """Convierte los fallos de recuperación registrados en producción en casos de
    prueba.

    Un vacío clasificado 'recuperacion' ya trae todo lo que hace falta para ser un
    caso dorado: la pregunta REAL que hizo un operador y el documento que sí la
    responde (doc_sugerido, hallado por el sondeo del corpus). Son los casos más
    valiosos del set porque no son inventados: son consultas que el buscador
    falló de verdad, con el índice de producción.

    Así el ciclo se cierra solo: la pantalla de vacíos junta los fallos, el
    evaluador los usa para medir si un cambio de configuración los arregla.
    """
    from app.database import engine

    query = text("""
        SELECT v.tema, v.pregunta_ejemplo, v.doc_sugerido, v.ocurrencias
        FROM pagina_web.ChatbotVacios v
        JOIN pagina_web.Chatbots c ON c.id = v.chatbot_id
        WHERE c.slug = :slug
          AND v.clasificacion = 'recuperacion'
          AND v.doc_sugerido IS NOT NULL
          AND v.estado <> 'no_corresponde'
        ORDER BY v.ocurrencias DESC
    """)
    with engine.connect() as conn:
        filas = conn.execute(query, {"slug": slug}).mappings().all()

    # `doc_sugerido` guarda hoy el TÍTULO del documento, pero los vacíos viejos
    # guardaron el nombre interno del indexador ('04_dbdoc_19.md'); se aceptan los
    # dos. Sin esto el flag cargaba 0 casos en silencio desde que la pantalla pasó a
    # mostrar títulos: `docs` está indexado por nombre de archivo y ninguna búsqueda
    # acertaba.
    titulos = set(docs.values())
    casos = []
    for f in filas:
        sugerido = f["doc_sugerido"]
        titulo = sugerido if sugerido in titulos else docs.get(sugerido)
        if not titulo:
            # El documento se borró o se renumeró desde que se registró el vacío:
            # sin título no se puede evaluar el acierto.
            continue
        casos.append(Caso(
            pregunta=f["pregunta_ejemplo"] or f["tema"],
            doc_esperado=titulo,
            nota=f"vacío en producción ({f['ocurrencias']} consultas)",
            veces=f["ocurrencias"],
        ))
    return casos


# --------------------------------------------------------------- config del bot

def desfasaje_con_prod(slug: str) -> Optional[str]:
    """Mensaje si el índice de ESTE entorno es más viejo que el de prod; None si no.

    SQL Server es compartido entre dev y prod, pero **Qdrant no**: cada servidor
    tiene el suyo y los reindexa por su cuenta. Calidad actualiza la documentación
    y reindexa en prod, así que el índice local se queda atrás sin que nada avise, y
    una corrida del evaluador contra ese corpus viejo mide un corpus que no existe
    en ninguna parte. Pasó el 2026-08-05: dev servía una foto del 15/07 (anterior a
    la migración a markdown) contra un set dorado escrito para el corpus de prod;
    el recall dio 0% y parecía un problema del set dorado.

    Se compara `last_indexed_at`, NO `index_version`: los contadores de versión son
    independientes por entorno, así que dev podría estar en v10 y prod en v8 sin que
    eso diga nada sobre cuál es más nuevo.
    """
    from app.database import engine

    query = text("""
        SELECT s.environment, s.index_version, s.index_status, s.last_indexed_at
        FROM pagina_web.ChatbotIndexState s
        JOIN pagina_web.Chatbots c ON c.id = s.chatbot_id
        WHERE c.slug = :slug
    """)
    with engine.connect() as conn:
        filas = {f["environment"]: f for f in conn.execute(query, {"slug": slug}).mappings()}

    local, prod = filas.get(settings.ENVIRONMENT), filas.get("prod")
    if settings.ENVIRONMENT == "prod" or not prod or not local:
        return None
    if not prod["last_indexed_at"] or not local["last_indexed_at"]:
        return None
    if local["last_indexed_at"] >= prod["last_indexed_at"]:
        return None

    dias = (prod["last_indexed_at"] - local["last_indexed_at"]).days
    return (
        f"El índice local de '{slug}' está {dias} días ATRÁS del de producción.\n"
        f"  {settings.ENVIRONMENT:5}: v{local['index_version']} indexado {local['last_indexed_at']:%Y-%m-%d %H:%M}\n"
        f"  prod : v{prod['index_version']} indexado {prod['last_indexed_at']:%Y-%m-%d %H:%M}\n"
        f"Medir contra el corpus local no dice nada de lo que responde producción: "
        f"el set dorado está escrito para el corpus de prod.\n"
        f"Opciones: reindexar local (python reindex_all.py {slug}), correr el evaluador "
        f"en el server de prod, o --ignorar-desfasaje si de verdad querés medir el índice local."
    )


def cargar_config(slug: str, verificar_desfasaje: bool = True) -> ChatbotConfig:
    from app.database import engine

    # La versión vigente sale de ChatbotIndexState para ESTE entorno, no del escalar
    # de Chatbots (que está desactualizado): dev y prod numeran sus índices por
    # separado sobre la misma BD.
    query = text("""
        SELECT c.id, c.slug, c.nombre, c.descripcion, c.system_prompt, c.grupo,
               p.code AS permission_code, c.activo,
               COALESCE(s.index_version, 0) AS index_version,
               s.index_status, s.last_indexed_at, c.updated_at
        FROM pagina_web.Chatbots c
        JOIN pagina_web.Permissions p ON p.id = c.permission_id
        LEFT JOIN pagina_web.ChatbotIndexState s
               ON s.chatbot_id = c.id AND s.environment = :env
        WHERE c.slug = :slug
    """)
    with engine.connect() as conn:
        row = conn.execute(query, {"slug": slug, "env": settings.ENVIRONMENT}).mappings().first()
    if row is None:
        raise SystemExit(f"No existe el chatbot '{slug}' en pagina_web.Chatbots.")
    if not row["index_version"]:
        raise SystemExit(
            f"El chatbot '{slug}' no tiene índice en el entorno '{settings.ENVIRONMENT}': "
            f"no hay nada que evaluar.\nCorré primero:  python reindex_all.py {slug}"
        )
    if verificar_desfasaje:
        desfasaje = desfasaje_con_prod(slug)
        if desfasaje:
            raise SystemExit(desfasaje)
    return ChatbotConfig.from_row(row)


def mapa_documentos(chatbot_id: int) -> Dict[str, str]:
    """`{'07_dbdoc_22.md': 'Plantillas y Modelos...'}` para poder hablar de títulos
    y no de ids en el set dorado. Incluye los docs prestados (ChatbotDocVinculo)."""
    from app.database import engine

    query = text("""
        SELECT m.id, m.titulo, m.orden
        FROM pagina_web.ChatbotDocMarkdown m
        WHERE m.activo = 1
          AND (m.chatbot_id = :cid
               OR EXISTS (SELECT 1 FROM pagina_web.ChatbotDocVinculo v
                          WHERE v.doc_id = m.id AND v.chatbot_id = :cid))
        ORDER BY m.orden, m.id
    """)
    with engine.connect() as conn:
        filas = conn.execute(query, {"cid": chatbot_id}).mappings().all()
    return {f"{f['orden']:02d}_dbdoc_{f['id']}.md": f["titulo"] for f in filas}


# ------------------------------------------------------------- caché de embeddings

class CacheEmbeddings:
    """Los embeddings de las preguntas del set no cambian entre configuraciones ni
    entre corridas: cachearlos hace que barrer 6 configuraciones cueste 0 tokens
    extra. La clave incluye el modelo, así cambiar de modelo invalida el caché."""

    def __init__(self, ruta: str, modelo: str):
        self.ruta = ruta
        self.modelo = modelo
        self.datos: Dict[str, List[float]] = {}
        self.nuevos = 0
        if os.path.exists(ruta):
            try:
                with open(ruta, encoding="utf-8") as f:
                    guardado = json.load(f)
                if guardado.get("modelo") == modelo:
                    self.datos = guardado.get("embeddings", {})
            except Exception:
                logger.warning("Caché de embeddings ilegible, se regenera.")

    async def obtener(self, preguntas: Sequence[str]) -> Dict[str, List[float]]:
        from llama_index.core import Settings

        faltantes = [p for p in preguntas if p not in self.datos]
        for pregunta in faltantes:
            self.datos[pregunta] = await Settings.embed_model.aget_query_embedding(pregunta)
            self.nuevos += 1
        if faltantes:
            self.guardar()
        return {p: self.datos[p] for p in preguntas}

    def guardar(self) -> None:
        os.makedirs(os.path.dirname(self.ruta), exist_ok=True)
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump({"modelo": self.modelo, "embeddings": self.datos}, f)


# ----------------------------------------------------------------- stack de retrieval

def _versiones_locales(cfg: ChatbotConfig) -> List[int]:
    """Versiones de índice que existen EN ESTE equipo. index_version vive en la BD
    (compartida dev/prod) pero el índice es local a cada servidor, así que pueden
    no coincidir."""
    if not os.path.isdir(cfg.storage_dir):
        return []
    versiones = []
    for nombre in os.listdir(cfg.storage_dir):
        if nombre.startswith("v") and nombre[1:].isdigit():
            if os.path.exists(os.path.join(cfg.storage_dir, nombre, "docstore.json")):
                versiones.append(int(nombre[1:]))
    return sorted(versiones)


class StackRecuperacion:
    """El mismo pipeline de recuperación que usa ChatBot.stream_query (índice
    persistido + Qdrant por alias, híbrido BM25 + vectorial), pero sin LLM y con
    top_k/top_n/reranker parametrizables. Se carga UNA vez y se reusa en todo el
    barrido: cargar el índice y construir BM25 es lo caro."""

    def __init__(self, cfg: ChatbotConfig, version: Optional[int] = None):
        from llama_index.core import StorageContext, load_index_from_storage
        from llama_index.vector_stores.qdrant import QdrantVectorStore
        from llama_index.retrievers.bm25 import BM25Retriever
        from qdrant_client import QdrantClient
        from qdrant_client.async_qdrant_client import AsyncQdrantClient
        from chatBot import BM25_LANGUAGE, BM25_STEMMER

        self.cfg = cfg
        self.version = version or cfg.index_version
        # Con la versión vigente se usa el ALIAS (lo mismo que sirven los workers);
        # con --index-version hay que ir a la colección física, porque el alias
        # apunta a la versión publicada y no a la que se quiere medir.
        self.coleccion = (
            cfg.collection_alias if version is None else cfg.collection_fisica(version)
        )
        cliente = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY or None)
        acliente = AsyncQdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY or None)
        vector_store = QdrantVectorStore(
            collection_name=self.coleccion, client=cliente, aclient=acliente
        )

        persist_dir = cfg.persist_dir(self.version)
        if not os.path.exists(os.path.join(persist_dir, "docstore.json")):
            locales = _versiones_locales(cfg)
            raise SystemExit(
                f"No hay índice persistido en {persist_dir} (v{cfg.index_version} es la "
                f"vigente en el entorno '{settings.ENVIRONMENT}').\n"
                f"Versiones presentes en este equipo: {locales or '(ninguna)'}.\n"
                f"El índice es local a cada servidor: corré el evaluador donde viva el "
                f"índice, o usá --index-version para medir una versión local."
            )
        self.index = load_index_from_storage(
            storage_context=StorageContext.from_defaults(
                vector_store=vector_store, persist_dir=persist_dir
            ),
            show_progress=False,
        )
        self.n_chunks = len(self.index.docstore.docs)
        self.bm25_max = BM25Retriever.from_defaults(
            docstore=self.index.docstore,
            similarity_top_k=max(k for k, _ in BARRIDO_DEFAULT) if BARRIDO_DEFAULT else 20,
            stemmer=BM25_STEMMER,
            language=BM25_LANGUAGE,
        )

    async def recuperar(self, pregunta: str, embedding: List[float], top_k: int) -> List:
        """Devuelve los top_k nodos del híbrido, ANTES del reranker."""
        from llama_index.core.retrievers import QueryFusionRetriever
        from chatBot import PrecomputedQueryEmbeddingRetriever

        vectorial = self.index.as_retriever(similarity_top_k=top_k)
        self.bm25_max.similarity_top_k = top_k
        fusion = QueryFusionRetriever(
            [vectorial, self.bm25_max],
            similarity_top_k=top_k,
            num_queries=1,
            mode="reciprocal_rerank",  # pyright: ignore[reportArgumentType]
            use_async=True,
        )
        retriever = PrecomputedQueryEmbeddingRetriever(fusion, pregunta, embedding)
        return await retriever.aretrieve(pregunta)


_RERANKERS: Dict[str, object] = {}


def get_reranker(modelo: str, top_n: int):
    """Un reranker por modelo (cargarlo es caro); top_n se ajusta por corrida."""
    from llama_index.core.postprocessor import SentenceTransformerRerank

    if modelo not in _RERANKERS:
        print(f"  cargando reranker {modelo} ...", flush=True)
        _RERANKERS[modelo] = SentenceTransformerRerank(model=modelo, top_n=top_n)
    rr = _RERANKERS[modelo]
    rr.top_n = top_n  # type: ignore[attr-defined]
    return rr


# ------------------------------------------------------------------- evaluación

@dataclass
class ResultadoCaso:
    pregunta: str
    doc_esperado: str
    en_bruto: bool
    en_final: bool
    posicion: Optional[int]
    terminos_ok: int
    terminos_total: int
    chunks_negativos: int
    chunks_final: int
    contaminado: bool
    docs_final: List[str]


@dataclass
class Resultado:
    top_k: int
    top_n: int
    reranker: str
    casos: List[ResultadoCaso]

    @property
    def etiqueta(self) -> str:
        modelo_corto = self.reranker.split("/")[-1]
        return f"k={self.top_k:<3} n={self.top_n:<3} {modelo_corto}"

    def _pct(self, valores: Sequence[bool]) -> float:
        return 100.0 * sum(valores) / len(valores) if valores else 0.0

    @property
    def recall_bruto(self) -> float:
        return self._pct([c.en_bruto for c in self.casos])

    @property
    def recall_final(self) -> float:
        return self._pct([c.en_final for c in self.casos])

    @property
    def contaminacion(self) -> float:
        return self._pct([c.contaminado for c in self.casos])

    @property
    def pos_media(self) -> Optional[float]:
        posiciones = [c.posicion for c in self.casos if c.posicion is not None]
        return sum(posiciones) / len(posiciones) if posiciones else None

    @property
    def terminos(self) -> float:
        ok = sum(c.terminos_ok for c in self.casos)
        total = sum(c.terminos_total for c in self.casos)
        return 100.0 * ok / total if total else 0.0

    @property
    def ruido(self) -> float:
        neg = sum(c.chunks_negativos for c in self.casos)
        total = sum(c.chunks_final for c in self.casos)
        return 100.0 * neg / total if total else 0.0


def _norm(texto: str) -> str:
    import unicodedata

    sin_acentos = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return sin_acentos.lower()


def _titulo_de(nodo, docs: Dict[str, str]) -> str:
    archivo = nodo.metadata.get("file_name", "")
    return docs.get(archivo, archivo)


async def evaluar(stack: StackRecuperacion, sd: SetDorado, docs: Dict[str, str],
                  embeddings: Dict[str, List[float]], top_k: int, top_n: int,
                  modelo_reranker: str) -> Resultado:
    from llama_index.core.schema import MetadataMode, QueryBundle

    reranker = get_reranker(modelo_reranker, top_n)
    prohibidos = {_norm(t) for t in sd.no_debe_traer}
    resultados: List[ResultadoCaso] = []

    for caso in sd.casos:
        brutos = await stack.recuperar(caso.pregunta, embeddings[caso.pregunta], top_k)
        titulos_bruto = [_norm(_titulo_de(n.node, docs)) for n in brutos]
        esperado = _norm(caso.doc_esperado)

        finales = reranker.postprocess_nodes(brutos, QueryBundle(caso.pregunta))
        titulos_final = [_titulo_de(n.node, docs) for n in finales]
        titulos_final_norm = [_norm(t) for t in titulos_final]

        posicion = None
        for i, t in enumerate(titulos_final_norm, start=1):
            if t == esperado:
                posicion = i
                break

        contexto = _norm(
            "\n".join(n.node.get_content(metadata_mode=MetadataMode.NONE) for n in finales)
        )
        terminos_ok = sum(1 for t in caso.debe_contener if _norm(t) in contexto)

        resultados.append(ResultadoCaso(
            pregunta=caso.pregunta,
            doc_esperado=caso.doc_esperado,
            en_bruto=esperado in titulos_bruto,
            en_final=posicion is not None,
            posicion=posicion,
            terminos_ok=terminos_ok,
            terminos_total=len(caso.debe_contener),
            chunks_negativos=sum(1 for n in finales if (n.score or 0) < 0),
            chunks_final=len(finales),
            contaminado=any(t in prohibidos for t in titulos_final_norm),
            docs_final=titulos_final,
        ))

    return Resultado(top_k=top_k, top_n=top_n, reranker=modelo_reranker, casos=resultados)


# ---------------------------------------------------------------------- reporte

def imprimir_tabla(resultados: List[Resultado]) -> None:
    print()
    print(f"{'configuración':<46} {'recall':>7} {'recall':>7} {'pos':>5} {'térm':>6} {'ruido':>7} {'contam':>7}")
    print(f"{'':<46} {'bruto':>7} {'final':>7} {'med':>5} {'':>6} {'':>7} {'':>7}")
    print("-" * 92)
    mejor = max(resultados, key=lambda r: (r.recall_final, -r.contaminacion, r.terminos))
    for r in resultados:
        pos = f"{r.pos_media:.1f}" if r.pos_media is not None else "-"
        marca = "  <<<" if r is mejor else ""
        print(f"{r.etiqueta:<46} {r.recall_bruto:6.1f}% {r.recall_final:6.1f}% "
              f"{pos:>5} {r.terminos:5.1f}% {r.ruido:6.1f}% {r.contaminacion:6.1f}%{marca}")
    print("-" * 92)
    if all(r.recall_bruto == 0 for r in resultados):
        # Sin recall bruto en NINGUNA config el problema no es de tuning: el índice
        # no contiene los documentos que nombra el set dorado (típicamente se está
        # midiendo una versión vieja del índice, anterior al split en varios docs).
        print("AVISO: recall bruto 0% en todas las configuraciones — el índice medido no "
              "contiene\nlos documentos del set dorado. Revisá que la versión de índice "
              "sea la correcta;\nla columna 'térm' sigue siendo válida (mide si el "
              "CONTENIDO llega al LLM).")
        return
    print(f"Mejor configuración: {mejor.etiqueta}  "
          f"(recall final {mejor.recall_final:.1f}%, contaminación {mejor.contaminacion:.1f}%)")


def imprimir_detalle(resultado: Resultado) -> None:
    print(f"\n=== Detalle caso por caso — {resultado.etiqueta} ===")
    for c in sorted(resultado.casos, key=lambda x: (x.en_final, x.posicion or 99)):
        if c.en_final:
            estado = f"OK  (pos {c.posicion})"
        elif c.en_bruto:
            estado = "PERDIDO POR EL RERANKER"
        else:
            estado = "NO RECUPERADO"
        print(f"\n[{estado}] {c.pregunta}")
        print(f"    esperado : {c.doc_esperado}")
        print(f"    recibido : {' | '.join(c.docs_final) or '(nada)'}")
        if c.terminos_total:
            print(f"    términos : {c.terminos_ok}/{c.terminos_total}")
        if c.contaminado:
            print("    CONTAMINADO: trae un documento de la lista no_debe_traer")


async def main_async(args) -> int:
    from app import rag_settings

    sd = cargar_set(args.slug, args.set)
    cfg = cargar_config(args.slug, verificar_desfasaje=not args.ignorar_desfasaje)
    docs = mapa_documentos(cfg.id)

    if args.vacios:
        extra = cargar_casos_desde_vacios(args.slug, docs)
        # Sin deduplicar contra el set estático a propósito: si una pregunta está en
        # los dos lados es porque falló en producción pese a estar cubierta, y pesarla
        # doble es correcto.
        sd.casos.extend(extra)
        print(f"+{len(extra)} casos tomados de fallos reales de producción")

    print(f"Bot '{cfg.slug}' (índice v{cfg.index_version}) — {len(sd.casos)} casos, "
          f"{len(docs)} documentos indexados")

    rag_settings.configure_global_settings()
    stack = StackRecuperacion(cfg, version=args.index_version)
    print(f"Índice cargado: v{stack.version} ({stack.coleccion}), {stack.n_chunks} chunks")

    cache = CacheEmbeddings(
        ruta=os.path.join(cfg.storage_dir, "eval", "emb_cache.json"),
        modelo=settings.DEFAULT_REMOTE_EMBED_MODEL,
    )
    embeddings = await cache.obtener([c.pregunta for c in sd.casos])
    print(f"Embeddings: {cache.nuevos} nuevos, {len(embeddings) - cache.nuevos} desde caché")

    # La config de producción siempre se mide, para que el barrido sea comparativo.
    combos = [(settings.CHATBOT_RETRIEVAL_TOP_K, settings.DEFAULT_RERANKER_TOP_N)]
    if args.barrido:
        combos += [c for c in BARRIDO_DEFAULT if c not in combos]
    elif args.top_k or args.top_n:
        combos = [(args.top_k or settings.CHATBOT_RETRIEVAL_TOP_K,
                   args.top_n or settings.DEFAULT_RERANKER_TOP_N)]

    modelos = [settings.DEFAULT_RERANKER_MODEL] + [
        m for m in (args.reranker or []) if m != settings.DEFAULT_RERANKER_MODEL
    ]

    resultados: List[Resultado] = []
    for modelo in modelos:
        for top_k, top_n in combos:
            if top_n > top_k:
                continue
            resultados.append(
                await evaluar(stack, sd, docs, embeddings, top_k, top_n, modelo)
            )

    imprimir_tabla(resultados)

    if args.detalle:
        peor_config = resultados[0]  # la de producción
        imprimir_detalle(peor_config)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump([
                {
                    "top_k": r.top_k, "top_n": r.top_n, "reranker": r.reranker,
                    "recall_bruto": r.recall_bruto, "recall_final": r.recall_final,
                    "pos_media": r.pos_media, "terminos": r.terminos,
                    "ruido": r.ruido, "contaminacion": r.contaminacion,
                    "casos": [vars(c) for c in r.casos],
                }
                for r in resultados
            ], f, ensure_ascii=False, indent=2)
        print(f"\nResultados en {args.json}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("slug", help="slug del chatbot (ej: voltara)")
    parser.add_argument("--set", help="ruta del set dorado (default: tests/data/rag_golden_<slug>.json)")
    parser.add_argument("--barrido", action="store_true", help="probar varias combinaciones de top_k/top_n")
    parser.add_argument("--vacios", action="store_true",
                        help="sumar como casos los fallos de recuperación reales registrados "
                             "en la pantalla de Vacíos de conocimiento")
    parser.add_argument("--top-k", type=int, dest="top_k", help="cuántos chunks recupera el híbrido")
    parser.add_argument("--top-n", type=int, dest="top_n", help="cuántos sobreviven al reranker")
    parser.add_argument("--reranker", action="append",
                        help="modelo de reranker extra a comparar (se puede repetir)")
    parser.add_argument("--index-version", type=int, dest="index_version",
                        help="medir una versión de índice local distinta de la publicada")
    parser.add_argument("--detalle", action="store_true", help="imprimir caso por caso la config de producción")
    parser.add_argument("--ignorar-desfasaje", action="store_true", dest="ignorar_desfasaje",
                        help="medir el índice local aunque esté más viejo que el de prod "
                             "(por defecto se corta: el set dorado está escrito para el corpus de prod)")
    parser.add_argument("--json", help="volcar los resultados a un archivo JSON")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
