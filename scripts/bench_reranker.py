"""Compara modelos de reranker sobre los fallos REALES de recuperación de un bot.

POR QUÉ
-------
El reranker por defecto (`cross-encoder/ms-marco-MiniLM-L12-v2`) está entrenado en
inglés sobre pasajes tipo prosa, y puntúa parecido de REDACCIÓN más que presencia
de la respuesta. Medido el 05/08/2026 sobre el chunk que literalmente contiene
`**ENRE (...):** 0800-000-0001`:

    "ENRE"                        +5,28
    "número de contacto del ENRE" -3,46      <- se descarta el chunk que lo tiene
    "teléfono del ENRE"           -2,62

El contenido de REFERENCIA (listas de teléfonos, canales, tablas) es el más
castigado, porque no se parece a un pasaje que responda una pregunta.

QUÉ MIDE
--------
Cada caso es (consulta real, chunk que SÍ responde, chunk que NO responde pero es
del mismo tema). El señuelo es, cuando se pudo, el que producción devolvió de
verdad. La métrica principal es cuántas veces el correcto le gana al señuelo:
es lo único comparable entre modelos, porque la ESCALA cambia con cada uno.

    ms-marco / mmarco   -> logits, aprox. -11 .. +10
    BAAI/bge-*          -> probabilidades, 0 .. 1

OJO — LA ESCALA ESTÁ ACOPLADA AL RESTO DEL SISTEMA
--------------------------------------------------
Cambiar de modelo invalida los umbrales absolutos que dependen del score:

    CHATBOT_VACIO_SCORE_MIN    (evaluar_cobertura: por debajo = sin cobertura)
    CHATBOT_VACIO_SCORE_EXISTE (_contesta_igual)
    el "ruido" de scripts/eval_rag.py (% de chunks con score negativo)
    los pisos de backend/tests/test_rag_calidad.py

Con un modelo tipo bge (0..1) NADA cae por debajo de -5 y la detección de vacíos
deja de marcar en silencio, que es el peor modo de falla. Por eso este script
imprime también el rango de scores: sirve para RECALIBRAR, no solo para elegir.

Uso (desde backend/, con el venv activo):
    python ../scripts/bench_reranker.py voltara
    python ../scripts/bench_reranker.py voltara --modelos cross-encoder/mmarco-mMiniLMv2-L12-H384-v1

NO consume tokens: los modelos corren local. La primera corrida descarga pesos.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402

MODELOS_POR_DEFECTO = [
    "cross-encoder/ms-marco-MiniLM-L12-v2",        # el actual (inglés)
    "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",  # mismo tamaño, multilingüe
    "BAAI/bge-reranker-base",                      # XLM-R base
    "BAAI/bge-reranker-v2-m3",                     # el más fuerte y el más lento
]

# top_k de producción: con esto se estima el costo por consulta.
DOCS_POR_CONSULTA = 12

# (consulta real del operador, título de la sección que responde, señuelo)
# Salidos de pagina_web.ChatbotVacios y de los contextos de query_chatbots_logs.
CASOS_VOLTARA = [
    ("numero de atencion de emergencia", (21, "### Canales Principales de Voltara"), (17, "### Inversión de Medidores")),
    ("números de emergencia",            (21, "### Canales Principales de Voltara"), (17, "### Inversión de Medidores")),
    ("numero de contacto del ENRE",      (21, "### Organismos Externos"),           (17, "### Inversión de Medidores")),
    ("adherir tarjeta de credito",       (19, "#### Adhesión al Débito Automático"), (19, "### Devolución de Saldo a Favor")),
    ("cliente quiere pagar la ultima factura con tarjeta de credito",
                                         (19, "#### Adhesión al Débito Automático"), (19, "### Devolución de Saldo a Favor")),
    ("robo de medidor",                  (17, "### Medidor Quemado"),               (17, "### Inversión de Medidores")),
    ("INDIVIDUALIZACION DE MEDIDORES",   (17, "### Cambio o Individualización de Acometida"), (19, "### Devolución de Saldo a Favor")),
    ("que documentacion necesito para cambio de titularidad",
                                         (16, "## Cambio de Titularidad"),          (19, "### Devolución de Saldo a Favor")),
]

LARGO_CHUNK = 900


def _cargar_secciones(slug: str):
    from app.database import engine

    with engine.connect() as conn:
        docs = {d["id"]: d["contenido_md"] for d in conn.execute(text("""
            SELECT m.id, m.contenido_md FROM pagina_web.ChatbotDocMarkdown m
            JOIN pagina_web.Chatbots b ON b.id = m.chatbot_id
            WHERE b.slug = :slug AND m.activo = 1
        """), {"slug": slug}).mappings()}

    casos, faltantes = [], []
    for consulta, (doc_ok, tit_ok), (doc_no, tit_no) in CASOS_VOLTARA:
        bueno = _seccion(docs.get(doc_ok), tit_ok)
        malo = _seccion(docs.get(doc_no), tit_no)
        if bueno and malo:
            casos.append((consulta, bueno, malo))
        else:
            faltantes.append(consulta)
    return casos, faltantes


def _seccion(md, titulo):
    """El texto que sigue a un encabezado. None si la documentación cambió y ya no está."""
    if not md:
        return None
    i = md.find(titulo)
    return md[i:i + LARGO_CHUNK] if i >= 0 else None


def evaluar(nombre: str, casos):
    from sentence_transformers import CrossEncoder

    t0 = time.monotonic()
    modelo = CrossEncoder(nombre, max_length=512)
    carga = time.monotonic() - t0

    pares = [(c, t) for c, bueno, malo in casos for t in (bueno, malo)]
    t0 = time.monotonic()
    scores = [float(s) for s in modelo.predict(pares, show_progress_bar=False)]
    por_doc = (time.monotonic() - t0) / len(pares)

    aciertos = 0
    print(f"\n{nombre}")
    for i, (consulta, _, _) in enumerate(casos):
        bueno, malo = scores[2 * i], scores[2 * i + 1]
        ok = bueno > malo
        aciertos += ok
        print(f"    {'ok ' if ok else 'MAL'}  {consulta[:46]:<48} "
              f"correcto {bueno:+8.2f}   señuelo {malo:+8.2f}")

    correctos = scores[0::2]
    print(f"    -> {aciertos}/{len(casos)} aciertos | "
          f"carga {carga:.1f}s | {por_doc * 1000:.0f} ms/doc | "
          f"~{por_doc * DOCS_POR_CONSULTA * 1000:.0f} ms por consulta")
    print(f"    -> escala: todos [{min(scores):+.2f} .. {max(scores):+.2f}]  "
          f"correctos [{min(correctos):+.2f} .. {max(correctos):+.2f}]"
          f"   <- RECALIBRAR CHATBOT_VACIO_SCORE_* con esto")
    return aciertos


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("slug", nargs="?", default="voltara",
                        help="bot del que se leen los documentos (los casos son de voltara)")
    parser.add_argument("--modelos", nargs="+", default=MODELOS_POR_DEFECTO)
    args = parser.parse_args()

    casos, faltantes = _cargar_secciones(args.slug)
    if not casos:
        print(f"No se pudo armar ningún caso con los documentos de '{args.slug}'.")
        return 1
    print(f"{len(casos)} casos reales de '{args.slug}' (actual: {settings.DEFAULT_RERANKER_MODEL})")
    if faltantes:
        # La documentación se reescribe seguido: si una sección se renombró, el caso
        # se saltea en vez de comparar contra texto equivocado.
        print(f"  ({len(faltantes)} casos salteados, esas secciones ya no existen)")

    for nombre in args.modelos:
        try:
            evaluar(nombre, casos)
        except Exception as e:  # noqa: BLE001
            print(f"\n{nombre}\n    NO SE PUDO EVALUAR: {type(e).__name__}: {str(e)[:150]}")

    print("\nPara cambiarlo: DEFAULT_RERANKER_MODEL=<modelo> en el .env del backend, "
          "y reiniciar.\nNo hace falta reindexar: el reranker corre sobre lo ya recuperado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
