"""Reestructura el documento de PLANTILLAS DE CARTAS de un chatbot para que el RAG las distinga.

CONTEXTO
--------
Las cartas de atención son casi idénticas entre sí (mismo saludo, mismas vías de contacto,
misma despedida). En Voltara, el 55% del texto de cada carta es común a las 50, así que el
buscador no puede distinguirlas: al pedir "una carta para derivar a T3" recupera cualquier
otra con la misma confianza. Este script reescribe el documento como catálogo + texto común
una sola vez + una sección por carta con solo su contenido propio.
Ver backend/AuditorIA/asistente_cartas.py.

Después de aplicarlo hay que:
  1. Poner la APERTURA y el CIERRE comunes en el system prompt del bot (el script los
     imprime), para que el bot pueda armar la carta completa sin depender de recuperarlos.
  2. Reindexar el bot.

USO (desde backend/, con el venv activo):
    python ../scripts/optimizar_cartas_rag.py voltara                    # dry-run: analiza y muestra
    python ../scripts/optimizar_cartas_rag.py voltara --apply            # reescribe el documento
    python ../scripts/optimizar_cartas_rag.py voltara --doc-id 22 --apply
    python ../scripts/optimizar_cartas_rag.py voltara --apply --salida /tmp/cartas.md

El dry-run es el default: SÍ llama a Gemini (necesita analizar para poder mostrar el
resultado), pero no escribe en la BD.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import engine  # noqa: E402


def _elegir_documento(slug, doc_id):
    """Documento de cartas del bot. Sin --doc-id, elige el que más plantillas ('### ') tenga."""
    with engine.connect() as conn:
        filas = conn.execute(text("""
            SELECT m.id, m.titulo, m.orden, m.contenido_md
            FROM pagina_web.ChatbotDocMarkdown m
            JOIN pagina_web.Chatbots c ON c.id = m.chatbot_id
            WHERE c.slug = :slug AND m.activo = 1
            ORDER BY m.orden, m.id
        """), {"slug": slug}).mappings().all()
    if not filas:
        raise SystemExit(f"El chatbot '{slug}' no tiene documentos in-app activos.")
    if doc_id:
        elegido = next((f for f in filas if f["id"] == doc_id), None)
        if not elegido:
            raise SystemExit(f"El documento {doc_id} no existe en '{slug}'.")
        return elegido
    elegido = max(filas, key=lambda f: f["contenido_md"].count("\n### "))
    if elegido["contenido_md"].count("\n### ") < 3:
        raise SystemExit(
            f"Ningún documento de '{slug}' parece un catálogo de cartas "
            "(se buscan encabezados '### '). Indicá uno con --doc-id."
        )
    return elegido


def main():
    ap = argparse.ArgumentParser(description="Optimiza el documento de plantillas de cartas para RAG.")
    ap.add_argument("slug", help="Slug del chatbot (ej.: voltara).")
    ap.add_argument("--doc-id", type=int, help="ID del documento a reestructurar (si no, se detecta).")
    ap.add_argument("--apply", action="store_true", help="Escribe el resultado en la BD (default: dry-run).")
    ap.add_argument("--salida", help="Guarda el markdown resultante en este archivo (para revisarlo).")
    ap.add_argument("--reindex", action="store_true", help="Con --apply, encola el reindexado del bot.")
    args = ap.parse_args()

    from AuditorIA import asistente_cartas

    doc = _elegir_documento(args.slug, args.doc_id)
    print(f"[{args.slug}] documento {doc['id']} — '{doc['titulo']}' ({len(doc['contenido_md'])} chars)")

    resultado = asistente_cartas.reestructurar_catalogo(
        doc["contenido_md"], titulo_doc=doc["titulo"] or "Plantillas de cartas",
    )

    print(f"\n{resultado['total_cartas']} cartas procesadas. "
          f"Documento: {len(doc['contenido_md'])} -> {len(resultado['markdown'])} chars "
          f"({resultado['reduccion_pct']}% menos).")

    print("\n=== APERTURA COMÚN (copiar al system prompt del bot) ===")
    print(resultado["apertura"])
    print("\n=== CIERRE COMÚN (copiar al system prompt del bot) ===")
    print(resultado["cierre"])

    print("\n=== CATÁLOGO (primeras 10) ===")
    for c in resultado["cartas"][:10]:
        print(f"  • {c['titulo']}")
        print(f"      cuándo: {(c.get('cuando_usarla') or '')[:110]}")
        print(f"      pide:   {', '.join(c.get('disparadores') or [])[:110]}")

    problemas = resultado["problemas"]
    if problemas:
        print(f"\n⚠️  {len(problemas)} carta(s) con contenido que NO se pudo verificar. Revisalas a mano:")
        for p in problemas:
            print(f"  • {p['carta']}: {p['problema']}")
            if p.get("detalle"):
                print(f"      {p['detalle']}")
    else:
        print("\n✅ Verificación OK: todas las líneas de contenido de las cartas originales "
              "están presentes en el resultado.")

    if args.salida:
        with open(args.salida, "w", encoding="utf-8") as fh:
            fh.write(resultado["markdown"])
        print(f"\nMarkdown guardado en {args.salida}")

    if not args.apply:
        print("\n(dry-run: no se escribió nada. Reejecutá con --apply para guardar.)")
        return

    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE pagina_web.ChatbotDocMarkdown
            SET contenido_md = :md, updated_at = SYSDATETIME()
            WHERE id = :did
        """), {"md": resultado["markdown"], "did": doc["id"]})
        if args.reindex:
            conn.execute(text("""
                INSERT INTO pagina_web.ChatbotIndexJobs (chatbot_id, requested_by, environment)
                SELECT c.id, NULL, :env FROM pagina_web.Chatbots c WHERE c.slug = :slug
                  AND NOT EXISTS (SELECT 1 FROM pagina_web.ChatbotIndexJobs j
                                  WHERE j.chatbot_id = c.id AND j.status IN ('pending','running')
                                    AND j.environment = :env)
            """), {"slug": args.slug, "env": settings.ENVIRONMENT})
    print(f"\n✅ Documento {doc['id']} actualizado.")
    print("Falta: poner la apertura y el cierre en el system prompt del bot"
          + ("" if args.reindex else ", y reindexar"))


if __name__ == "__main__":
    main()
