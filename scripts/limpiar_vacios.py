"""Purga temas de la pantalla de Vacíos de conocimiento, de forma consistente.

CASO DE USO ORIGINAL (2026-07-30): el backfill del histórico clasificó 515 temas
como 'recuperacion' (la información SÍ existe en el corpus, el buscador no la
trajo). Casi todos son consecuencia de problemas ya resueltos —la reestructuración
de los documentos y la separación del catálogo de cartas—, así que reflejan un
índice que ya no existe y solo tapan los 166 huecos reales que Calidad tiene que
documentar.

OJO: esto NO significa que 'recuperacion' sea una categoría inútil. De acá en
adelante es nuestra cola de trabajo (fallos de recuperación con el índice ACTUAL).
Lo que se purga es el lote histórico, medido contra un índice viejo.

POR QUÉ UN SCRIPT Y NO UN DELETE
--------------------------------
Borrar la fila sola deja el sistema inconsistente en dos lugares:

1. query_chatbots_logs.vacio_id tiene una FK: hay que soltarla primero. Y además
   hay que apagar sin_cobertura en esas consultas, porque si no el scheduler las
   vuelve a tomar como pendientes y recrea exactamente los mismos temas.

2. Los CENTROIDES en Qdrant (colección vacios_conocimiento) apuntan al vacio_id.
   Si quedan huérfanos, un vacío nuevo que se parezca agrupa contra un id que ya
   no existe -> el UPDATE no encuentra la fila y el UPDATE del log viola la FK,
   tumbando el tick entero del scheduler.

SEGUNDO CASO DE USO (2026-09-04): sacar los temas que el bot YA responde. Un vacío
es una foto del día que se registró; el corpus y el prompt siguen cambiando, así que
parte de la pantalla envejece sola. Con `--ids` se borra una lista puntual —la que
sale de re-preguntarle al bot cada `pregunta_ejemplo` y quedarse con las que hoy
contestan— sin tocar el resto. Ojo con el criterio: vale solo si el bot devuelve una
RESPUESTA. Una lista de temas para elegir (desambiguación) no es una respuesta y ese
vacío se queda donde está.

Uso (desde backend/, con el venv activo y Qdrant corriendo):
    python ../scripts/limpiar_vacios.py --clasificacion recuperacion
    python ../scripts/limpiar_vacios.py --clasificacion recuperacion --apply
    python ../scripts/limpiar_vacios.py --clasificacion fuera_de_alcance --apply
    python ../scripts/limpiar_vacios.py --clasificacion recuperacion --slug voltara --apply

    # Tras cambiar la lógica de clasificación: en vez de descartar las consultas,
    # las deja marcadas para que el scheduler las vuelva a clasificar.
    python ../scripts/limpiar_vacios.py --clasificacion recuperacion --reprocesar --apply

    # Temas puntuales (los que el bot ya responde), por id o desde un archivo:
    python ../scripts/limpiar_vacios.py --ids 412,517,533
    python ../scripts/limpiar_vacios.py --ids-archivo ya_responde.txt --apply
"""
import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import text  # noqa: E402

from app.logging_config import setup_logging  # noqa: E402
from app.vacios_conocimiento import COLECCION_VACIOS  # noqa: E402

setup_logging()
logger = logging.getLogger("limpiar_vacios")

CLASIFICACIONES = ("hueco", "recuperacion", "generacion", "fuera_de_alcance")


def _seleccionar(conn, clasificacion: str, slug: str | None):
    filtro = "v.clasificacion = :clas"
    params = {"clas": clasificacion}
    if slug:
        filtro += " AND c.slug = :slug"
        params["slug"] = slug
    return conn.execute(text(f"""
        SELECT v.id, v.tema, v.estado, v.ocurrencias, c.slug
        FROM pagina_web.ChatbotVacios v
        JOIN pagina_web.Chatbots c ON c.id = v.chatbot_id
        WHERE {filtro}
        ORDER BY v.ocurrencias DESC
    """), params).mappings().all()


def _seleccionar_ids(conn, ids: list[int]):
    """Los temas de una lista puntual. Se devuelven en el mismo formato que
    `_seleccionar` para que el resto del script no distinga de dónde salieron."""
    lote = ",".join(str(int(x)) for x in ids)
    return conn.execute(text(f"""
        SELECT v.id, v.tema, v.estado, v.ocurrencias, c.slug
        FROM pagina_web.ChatbotVacios v
        JOIN pagina_web.Chatbots c ON c.id = v.chatbot_id
        WHERE v.id IN ({lote})
        ORDER BY v.ocurrencias DESC
    """)).mappings().all()


def _borrar_centroides(ids: list[int]) -> None:
    """Saca de Qdrant los centroides de los temas borrados. Sin esto la agrupación
    de nuevos vacíos apuntaría a filas inexistentes."""
    from qdrant_client import models as qmodels
    from app.chatbot_indexer import _get_qdrant_client

    client = _get_qdrant_client()
    if not client.collection_exists(COLECCION_VACIOS):
        print("  (no hay colección de centroides todavía)")
        return

    # De a lotes: un MatchAny con 500 ids es un filtro enorme.
    for i in range(0, len(ids), 200):
        lote = ids[i:i + 200]
        client.delete(
            collection_name=COLECCION_VACIOS,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(must=[qmodels.FieldCondition(
                    key="vacio_id", match=qmodels.MatchAny(any=lote)
                )])
            ),
        )
    print(f"  centroides borrados de Qdrant: {len(ids)}")


def limpiar(clasificacion: str | None, slug: str | None, apply: bool,
            reprocesar: bool = False, ids: list[int] | None = None) -> None:
    from app.database import engine

    with engine.connect() as conn:
        filas = _seleccionar_ids(conn, ids) if ids else _seleccionar(conn, clasificacion, slug)

    if not filas:
        if ids:
            print("Ninguno de esos ids existe en la pantalla (¿ya se borraron?).")
        else:
            print(f"No hay temas con clasificación '{clasificacion}'"
                  + (f" en '{slug}'." if slug else "."))
        return

    if ids and len(filas) != len(set(ids)):
        faltan = sorted(set(ids) - {f["id"] for f in filas})
        print(f"OJO: {len(faltan)} de los ids pedidos ya no están: {faltan}\n")

    ids = [f["id"] for f in filas]
    consultas = sum(f["ocurrencias"] for f in filas)
    triageados = [f for f in filas if f["estado"] != "pendiente"]

    print(f"Temas a borrar          : {len(ids)}")
    print(f"Consultas que agrupaban : {consultas}")
    if triageados:
        # Borrarlos descarta una decisión humana: se avisa explícitamente.
        print(f"\nOJO: {len(triageados)} ya fueron triageados por alguien y también se borran:")
        for f in triageados[:10]:
            print(f"  - [{f['estado']}] {f['tema']} ({f['ocurrencias']} consultas)")

    print("\nLos más frecuentes:")
    for f in filas[:10]:
        print(f"  {f['ocurrencias']:>4}  {f['slug']:<16} {f['tema'][:70]}")

    if reprocesar:
        print("\nMODO REPROCESAR: las consultas quedan marcadas y el scheduler las vuelve\na clasificar con la lógica actual (no se pierden).")

    if not apply:
        print("\n(en seco: no se borró nada; agregá --apply)")
        return

    with engine.begin() as conn:
        # 1. Soltar la FK. Con --reprocesar se deja sin_cobertura=1 para que el
        #    scheduler las vuelva a clasificar (útil cuando cambió la lógica de
        #    clasificación); sin la opción se apaga, porque si no el próximo tick
        #    recrearía exactamente los mismos temas que se acaban de borrar.
        marca = "sin_cobertura" if reprocesar else "0"
        for i in range(0, len(ids), 500):
            lote = ",".join(str(int(x)) for x in ids[i:i + 500])
            afectadas = conn.execute(text(f"""
                UPDATE pagina_web.query_chatbots_logs
                SET vacio_id = NULL, sin_cobertura = {marca}
                WHERE vacio_id IN ({lote})
            """)).rowcount
            print(f"  consultas {'a reprocesar' if reprocesar else 'desvinculadas'}: {afectadas}")

        # 2. Ahora sí, las filas.
        for i in range(0, len(ids), 500):
            lote = ",".join(str(int(x)) for x in ids[i:i + 500])
            conn.execute(text(f"DELETE FROM pagina_web.ChatbotVacios WHERE id IN ({lote})"))

    # 3. Qdrant fuera de la transacción de SQL: si falla, quedan centroides
    #    huérfanos y hay que volver a correr el script, pero la BD ya está sana.
    _borrar_centroides(ids)

    with engine.connect() as conn:
        resto = conn.execute(text("""
            SELECT clasificacion, COUNT(*) AS temas, SUM(ocurrencias) AS consultas
            FROM pagina_web.ChatbotVacios GROUP BY clasificacion
        """)).mappings().all()
    print("\nQueda en la pantalla:")
    for r in resto:
        print(f"  {r['clasificacion']:<18} {r['temas']:>4} temas / {r['consultas']} consultas")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--clasificacion", choices=CLASIFICACIONES,
                        help="qué categoría purgar (o usá --ids para temas puntuales)")
    parser.add_argument("--ids", help="ids de temas separados por coma (los que el bot ya responde)")
    parser.add_argument("--ids-archivo", dest="ids_archivo",
                        help="archivo con un id por línea (se ignora lo que siga a un '#')")
    parser.add_argument("--slug", help="acotar a un chatbot")
    parser.add_argument("--apply", action="store_true", help="borrar de verdad")
    parser.add_argument("--reprocesar", action="store_true",
                        help="en vez de descartarlas, dejar las consultas para que el scheduler\n                              las vuelva a clasificar (usar tras cambiar la lógica de clasificación)")
    args = parser.parse_args()

    ids: list[int] = []
    if args.ids:
        ids += [int(x) for x in args.ids.replace(" ", "").split(",") if x]
    if args.ids_archivo:
        with open(args.ids_archivo, encoding="utf-8") as f:
            for linea in f:
                linea = linea.split("#", 1)[0].strip()
                if linea:
                    ids.append(int(linea))

    if bool(ids) == bool(args.clasificacion):
        parser.error("elegí UNA cosa: --clasificacion para purgar una categoría entera, "
                     "o --ids/--ids-archivo para temas puntuales.")

    limpiar(args.clasificacion, args.slug, args.apply, args.reprocesar, ids or None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
