"""Limpia las colecciones huérfanas que dejaron los reindexados fallidos en Qdrant.

CONTEXTO
--------
Entre el 2026-07-09 y el 2026-07-13 el reindexado nocturno falló en bloque porque
el contenedor de Qdrant corría con el nofile por defecto (1024 fds) y no podía
crear la colección nueva de cada bot:
    "RocksDB open error: IO error ... Too many open files (os error 24)"
Cada intento dejó un directorio de colección a medio crear (bot_{slug}_v{N+1},
sin registrar en Qdrant) más los segmentos de la caché. Este script los borra.

Trabaja sobre el FILESYSTEM, no por la API: los directorios huérfanos nunca se
llegaron a registrar como colecciones, así que un delete_collection no los ve.
Por eso Qdrant TIENE QUE ESTAR PARADO.

QUÉ CONSERVA (por cada bot activo, leyendo index_version de pagina_web.Chatbots):
  - bot_{slug}_v{N}    -> la colección viva, a la que apunta el alias bot_{slug}
  - bot_{slug}_v{N-1}  -> el colchón para workers que todavía no recargaron
    (mismo criterio que _limpiar_versiones_viejas en app/chatbot_indexer.py)
Todo lo demás sobra: versiones > N (basura de los intentos fallidos), versiones
< N-1, y las cache_* (son caché, se repueblan solas).

USO (en SRV01, desde backend/ y con el venv activo):
    docker compose -f ../docker-compose.qdrant.yml down
    python ../scripts/qdrant_limpiar_huerfanas.py            # dry-run: solo lista
    python ../scripts/qdrant_limpiar_huerfanas.py --apply    # borra
    docker compose -f ../docker-compose.qdrant.yml up -d --force-recreate

El dry-run es el default a propósito: mirá la lista antes de borrar nada.
"""
import argparse
import os
import re
import shutil
import socket
import sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import engine  # noqa: E402

# Path del volumen de Qdrant en el host (ver docker-compose.qdrant.yml).
QDRANT_STORAGE = "/var/lib/qdrant"
COLECCION_RE = re.compile(r"^bot_(?P<slug>[a-z0-9_]+)_v(?P<version>\d+)$")


def _qdrant_esta_corriendo() -> bool:
    url = urlparse(settings.QDRANT_URL)
    host, puerto = url.hostname or "127.0.0.1", url.port or 6333
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex((host, puerto)) == 0


def _versiones_vivas() -> dict[str, int]:
    with engine.connect() as conn:
        filas = conn.execute(text(
            "SELECT slug, index_version FROM pagina_web.Chatbots"
        )).mappings().all()
    return {f["slug"]: f["index_version"] for f in filas}


def _colecciones_a_conservar(vivas: dict[str, int]) -> set[str]:
    conservar = set()
    for slug, version in vivas.items():
        for v in (version, version - 1):
            if v >= 1:
                conservar.add(f"bot_{slug}_v{v}")
    return conservar


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Borra de verdad. Sin esto solo lista (dry-run).")
    args = parser.parse_args()

    if _qdrant_esta_corriendo():
        print(f"ERROR: Qdrant sigue respondiendo en {settings.QDRANT_URL}.")
        print("Pararlo primero:  docker compose -f docker-compose.qdrant.yml down")
        return 1

    dir_colecciones = os.path.join(QDRANT_STORAGE, "collections")
    if not os.path.isdir(dir_colecciones):
        print(f"ERROR: no existe {dir_colecciones}. ¿El volumen de Qdrant está en otro lado?")
        return 1

    vivas = _versiones_vivas()
    if not vivas:
        print("ERROR: pagina_web.Chatbots no devolvió ningún bot; no borro nada a ciegas.")
        return 1

    conservar = _colecciones_a_conservar(vivas)
    en_disco = sorted(d for d in os.listdir(dir_colecciones)
                      if os.path.isdir(os.path.join(dir_colecciones, d)))

    borrar, desconocidas = [], []
    for nombre in en_disco:
        if nombre in conservar:
            continue
        if nombre.startswith("cache_") or COLECCION_RE.match(nombre):
            borrar.append(nombre)
        else:
            # Algo que no sigue la convención bot_/cache_: no lo tocamos.
            desconocidas.append(nombre)

    print(f"Bots en la BD: {len(vivas)}  |  Colecciones en disco: {len(en_disco)}\n")

    print("CONSERVAR (viva + colchón):")
    for nombre in sorted(conservar & set(en_disco)):
        print(f"  = {nombre}")
    faltantes = sorted(conservar - set(en_disco))
    if faltantes:
        print("\n  OJO: estas deberían existir según index_version y NO están en disco.")
        print("  El bot va a fallar hasta que lo reindexes:")
        for nombre in faltantes:
            print(f"  ! {nombre}")

    print(f"\nBORRAR ({len(borrar)}):")
    for nombre in borrar:
        print(f"  - {nombre}")

    if desconocidas:
        print("\nSE IGNORAN (no siguen la convención, revisalas a mano):")
        for nombre in desconocidas:
            print(f"  ? {nombre}")

    if not borrar:
        print("\nNo hay nada que borrar.")
        return 0

    if not args.apply:
        print("\n[DRY-RUN] No se borró nada. Volvé a correr con --apply para aplicarlo.")
        return 0

    for nombre in borrar:
        shutil.rmtree(os.path.join(dir_colecciones, nombre), ignore_errors=True)
        print(f"  borrado: {nombre}")

    # Los persist_dir de LlamaIndex (docstore/index_store) siguen la misma
    # convención de versión bajo CHATBOT_STORAGE_ROOT/{slug}/v{N}.
    print("\nPersist dirs de LlamaIndex:")
    for slug, version in vivas.items():
        storage_dir = os.path.join(settings.CHATBOT_STORAGE_ROOT, slug)
        if not os.path.isdir(storage_dir):
            continue
        for entrada in sorted(os.listdir(storage_dir)):
            m = re.match(r"^v(\d+)$", entrada)
            if m and int(m.group(1)) not in (version, version - 1):
                shutil.rmtree(os.path.join(storage_dir, entrada), ignore_errors=True)
                print(f"  borrado: {slug}/{entrada}")

    print("\nListo. Ahora:  docker compose -f docker-compose.qdrant.yml up -d --force-recreate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
