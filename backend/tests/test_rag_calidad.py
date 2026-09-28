"""Regresión de CALIDAD de recuperación del RAG (no solo "responde sin error").

test_chatbots_live.py comprueba que el pipeline no explota; esto comprueba que
recupera lo correcto. Se apoya en el mismo motor que scripts/eval_rag.py, sobre
un set dorado de preguntas reales de operadores (tests/data/rag_golden_*.json).

    pytest tests/test_rag_calidad.py -m tokens
    pytest tests/test_rag_calidad.py -m tokens -k voltara

CONSUME TOKENS: un embedding por pregunta del set, y solo la primera vez (se
cachean en {storage}/{slug}/eval/emb_cache.json). Los reruns salen gratis.

Los umbrales son deliberadamente bajos: marcan un piso de no-regresión, no un
objetivo. Para AFINAR la configuración usá el barrido del script, que compara
combinaciones; acá solo se defiende lo ya conseguido.
"""
import asyncio
import glob
import os

import pytest

pytestmark = pytest.mark.tokens

# Piso de no-regresión. Si una config nueva los baja, algo empeoró.
MIN_RECALL_BRUTO = 70.0   # el doc correcto entra en el top_k del híbrido
MIN_RECALL_FINAL = 55.0   # y sobrevive al reranker
MIN_TERMINOS = 75.0       # los términos clave llegan al contexto
MAX_CONTAMINACION = 10.0  # docs que no son de este bot (ej: cartas en el telefónico)

RUTA_SETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _slugs_con_set():
    patron = os.path.join(RUTA_SETS, "rag_golden_*.json")
    return sorted(
        os.path.basename(p)[len("rag_golden_"):-len(".json")] for p in glob.glob(patron)
    )


def pytest_generate_tests(metafunc):
    if "slug" in metafunc.fixturenames:
        opcion = metafunc.config.getoption("-m") or ""
        if "not tokens" in opcion:
            metafunc.parametrize("slug", [])
            return
        slugs = _slugs_con_set()
        metafunc.parametrize("slug", slugs, ids=slugs)


@pytest.fixture(scope="module")
def evaluador():
    """Importa el script de evaluación como módulo (vive fuera del paquete backend)."""
    import importlib.util

    ruta = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts", "eval_rag.py"
    )
    spec = importlib.util.spec_from_file_location("eval_rag", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)  # type: ignore[union-attr]
    return modulo


def _medir(evaluador, slug):
    from app import rag_settings
    from app.config import settings

    sd = evaluador.cargar_set(slug)

    # El índice vive en el Qdrant de CADA servidor; la BD es compartida. Si el local
    # quedó atrás del de prod (Calidad reindexa allá), esto no puede medir nada: el
    # set dorado está escrito contra el corpus de producción. Se saltea con el motivo
    # a la vista en vez de fallar, porque un rojo que no es una regresión real enseña
    # a ignorar el test — que es exactamente cómo estuvo ciego un mes.
    desfasaje = evaluador.desfasaje_con_prod(slug)
    if desfasaje:
        pytest.skip(f"Índice local desactualizado, no es comparable:\n{desfasaje}")

    cfg = evaluador.cargar_config(slug, verificar_desfasaje=False)
    docs = evaluador.mapa_documentos(cfg.id)

    rag_settings.configure_global_settings()
    stack = evaluador.StackRecuperacion(cfg)

    cache = evaluador.CacheEmbeddings(
        ruta=os.path.join(cfg.storage_dir, "eval", "emb_cache.json"),
        modelo=settings.DEFAULT_REMOTE_EMBED_MODEL,
    )

    async def correr():
        embeddings = await cache.obtener([c.pregunta for c in sd.casos])
        return await evaluador.evaluar(
            stack, sd, docs, embeddings,
            settings.CHATBOT_RETRIEVAL_TOP_K,
            settings.DEFAULT_RERANKER_TOP_N,
            settings.DEFAULT_RERANKER_MODEL,
        )

    return asyncio.run(correr())


def test_recuperacion_no_regresiona(evaluador, slug):
    r = _medir(evaluador, slug)

    fallidos = [c.pregunta for c in r.casos if not c.en_final]
    perdidos_por_reranker = [c.pregunta for c in r.casos if c.en_bruto and not c.en_final]
    contaminados = [c.pregunta for c in r.casos if c.contaminado]

    # Se reportan todas las métricas juntas: al fallar, el mensaje tiene que decir
    # QUÉ empeoró sin obligar a correr el script aparte.
    resumen = (
        f"\n[{slug}] recall bruto {r.recall_bruto:.1f}% | final {r.recall_final:.1f}% | "
        f"términos {r.terminos:.1f}% | ruido {r.ruido:.1f}% | contaminación {r.contaminacion:.1f}%"
        f"\n  sin recuperar ({len(fallidos)}): {fallidos[:6]}"
        f"\n  perdidos por el reranker ({len(perdidos_por_reranker)}): {perdidos_por_reranker[:6]}"
        f"\n  contaminados ({len(contaminados)}): {contaminados[:6]}"
    )

    assert r.recall_bruto >= MIN_RECALL_BRUTO, (
        f"El retriever híbrido no está trayendo el documento correcto (problema de "
        f"indexado/chunking, no de reranker).{resumen}"
    )
    assert r.recall_final >= MIN_RECALL_FINAL, (
        f"El reranker está descartando el documento correcto.{resumen}"
    )
    assert r.terminos >= MIN_TERMINOS, (
        f"Los términos clave no llegan al contexto del LLM.{resumen}"
    )
    assert r.contaminacion <= MAX_CONTAMINACION, (
        f"El bot está recuperando documentos que no le corresponden.{resumen}"
    )
