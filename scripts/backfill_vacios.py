"""Backfill de los vacíos de conocimiento sobre el histórico ya registrado.

Sin esto la pantalla de Calidad arranca vacía y tarda semanas en tener algo útil.
Con esto, el día que se publica ya muestra los temas priorizados por frecuencia
sobre las ~9.000 consultas que hay en pagina_web.query_chatbots_logs.

Dos fases:

  1. MARCAR (gratis, sin IA): aplica la señal barata `evaluar_cobertura` a las
     filas históricas y deja `sin_cobertura` seteado. Como las filas viejas no
     tienen `score_max` (la columna es nueva), se decide solo por el texto de la
     respuesta — que es de todos modos la señal dominante.

  2. PROCESAR (consume tokens): drena la cola con exactamente el mismo código que
     usa el scheduler (`procesar_vacios_pendientes`), así el histórico y el
     tráfico nuevo quedan clasificados con el mismo criterio.

Uso (desde backend/, con el venv activo):
    python ../scripts/backfill_vacios.py --marcar                    # fase 1, en seco
    python ../scripts/backfill_vacios.py --marcar --apply            # fase 1
    python ../scripts/backfill_vacios.py --procesar --max 200        # fase 2, acotado
    python ../scripts/backfill_vacios.py --procesar                  # fase 2, hasta drenar

Correr la fase 2 en tandas y mirar la pantalla: si la agrupación quedó muy fina o
muy gruesa, se ajusta CHATBOT_VACIO_AGRUPAR_MIN_SCORE antes de seguir.
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import text  # noqa: E402

from app.logging_config import setup_logging  # noqa: E402
from app.config import settings  # noqa: E402
from app.vacios_conocimiento import evaluar_cobertura, procesar_vacios_pendientes  # noqa: E402

setup_logging()
logger = logging.getLogger("backfill_vacios")

# Solo campañas que existan como bot: las legacy ("csv no premium") no tienen a
# quién imputarles el vacío y el job las descartaría igual.
_SQL_CANDIDATAS = text("""
    SELECT l.id, l.response
    FROM pagina_web.query_chatbots_logs l
    WHERE l.sin_cobertura IS NULL
      AND EXISTS (SELECT 1 FROM pagina_web.Chatbots c WHERE c.slug = l.effective_campana)
""")


def marcar(apply: bool) -> None:
    from app.database import engine

    with engine.connect() as conn:
        filas = conn.execute(_SQL_CANDIDATAS).mappings().all()

    sin_cobertura = [f["id"] for f in filas if evaluar_cobertura(f["response"], None)]
    con_cobertura = [f["id"] for f in filas if f["id"] not in set(sin_cobertura)]

    print(f"Filas históricas sin evaluar : {len(filas)}")
    print(f"  -> sin cobertura (a analizar): {len(sin_cobertura)}")
    print(f"  -> respondidas               : {len(con_cobertura)}")

    if not apply:
        print("\n(en seco: no se escribió nada; agregá --apply)")
        return

    with engine.begin() as conn:
        # De a lotes: un IN con 2.000 ids revienta el límite de parámetros.
        for etiqueta, ids in (("1", sin_cobertura), ("0", con_cobertura)):
            for i in range(0, len(ids), 500):
                lote = ids[i:i + 500]
                conn.execute(text(f"""
                    UPDATE pagina_web.query_chatbots_logs
                    SET sin_cobertura = {etiqueta}
                    WHERE id IN ({",".join(str(int(x)) for x in lote)})
                """))
    print("\nListo. Ahora corré --procesar para clasificar y agrupar.")


def procesar(maximo: int | None) -> None:
    from app.database import engine

    with engine.connect() as conn:
        pendientes = conn.execute(text("""
            SELECT COUNT(*) FROM pagina_web.query_chatbots_logs
            WHERE sin_cobertura = 1 AND vacio_id IS NULL
        """)).scalar_one()

    print(f"Pendientes de clasificar: {pendientes}")
    if not pendientes:
        return

    objetivo = min(maximo, pendientes) if maximo else pendientes
    print(f"Se van a procesar hasta {objetivo} (lotes de {settings.CHATBOT_VACIOS_LOTE}).\n")

    total = 0
    while total < objetivo:
        procesadas = procesar_vacios_pendientes(limite=min(settings.CHATBOT_VACIOS_LOTE, objetivo - total))
        if procesadas == 0:
            # O se drenó la cola, o todas las del lote fallaron (error transitorio
            # del modelo): en ambos casos frenar es lo correcto, se reintenta luego.
            break
        total += procesadas
        print(f"  {total}/{objetivo}")

    with engine.connect() as conn:
        temas = conn.execute(text("SELECT COUNT(*) FROM pagina_web.ChatbotVacios")).scalar_one()
    print(f"\nProcesadas {total}. Temas agrupados en total: {temas}.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--marcar", action="store_true", help="fase 1: marcar sin_cobertura (sin IA)")
    parser.add_argument("--apply", action="store_true", help="escribir de verdad en la fase 1")
    parser.add_argument("--procesar", action="store_true", help="fase 2: clasificar y agrupar (consume tokens)")
    parser.add_argument("--max", type=int, help="tope de consultas a procesar en la fase 2")
    args = parser.parse_args()

    if not args.marcar and not args.procesar:
        parser.error("elegí --marcar y/o --procesar")

    if args.marcar:
        marcar(args.apply)
    if args.procesar:
        procesar(args.max)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
