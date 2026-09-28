"""¿Qué cambiaría si las tablas de datos ya estuvieran andando?

Corre el ruteo de app/chatbot_tablas.py contra las consultas REALES que ya están
en pagina_web.query_chatbots_logs y muestra, una por una, cuáles pasarían a
responderse por una tabla y con qué filas. Sirve para dos momentos:

  ANTES de cargar una tabla — con `--simular <archivo.json>` se le pasa una tabla
  candidata sin guardarla en la base, y se ve si los términos y las claves
  elegidos alcanzan para atrapar el tráfico que hoy falla.

  DESPUÉS de cargarla — sin `--simular` usa las tablas reales del bot y confirma
  que el ruteo hace lo que se esperaba (y, sobre todo, que NO se lleva puestas
  las consultas de procedimiento, que es el riesgo real de este feature).

Es una foto del ruteo, no de la respuesta: no llama a ningún modelo, no consume
tokens y no escribe nada.

Uso (desde backend/, con el venv activo):
    python ../scripts/simular_tablas.py vantix
    python ../scripts/simular_tablas.py vantix --dias 120 --detalle
    python ../scripts/simular_tablas.py benefix --simular /tmp/cartera.json

El JSON de --simular tiene la forma de una propuesta de tabla:
    {"nombre": "...", "descripcion": "...", "terminos": ["..."],
     "columnas": [{"nombre": "Base", "clave": true}, ...],
     "modo": "auto", "filas": [{"Base": "...", "Zona": "..."}, ...]}
"""
import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import text  # noqa: E402

from app import chatbot_tablas  # noqa: E402
from app.chatbot_tablas import Columna, Fila, Tabla  # noqa: E402
from app.chatbot_tablas_admin import _busqueda_de  # noqa: E402


def _engine():
    from app.database import engine
    return engine


def tabla_desde_json(ruta: str, chatbot_id: int) -> Tabla:
    """Arma una Tabla en memoria desde una propuesta, sin tocar la base."""
    with open(ruta, encoding="utf-8") as fh:
        datos = json.load(fh)

    columnas = [
        Columna(nombre=c["nombre"], descripcion=c.get("descripcion", ""),
                clave=bool(c.get("clave")))
        for c in datos["columnas"]
    ]
    claves = [c.nombre for c in columnas if c.clave] or [columnas[0].nombre]
    filas = [
        Fila(orden=i, datos={k: str(v) for k, v in f.items()},
             busqueda=_busqueda_de(f, claves))
        for i, f in enumerate(datos.get("filas") or [])
    ]
    tabla = Tabla(
        id=0, chatbot_id=chatbot_id, nombre=datos.get("nombre", "Tabla"),
        descripcion=datos.get("descripcion", ""),
        terminos=[chatbot_tablas.normalizar(t) for t in datos.get("terminos") or []],
        columnas=columnas, nota=datos.get("nota", ""),
        modo_declarado=datos.get("modo", "auto"), filas=filas,
    )
    tabla.preparar()
    return tabla


def consultas_del_bot(slug: str, dias: int):
    """Las consultas reales del bot, con el score que sacaron por el camino RAG.

    El score es lo que permite leer el resultado: una consulta que hoy puntúa mal
    y pasa a ir por tabla es una ganancia; una que hoy puntúa BIEN y se la lleva
    la tabla es el riesgo que hay que mirar con lupa.
    """
    with _engine().connect() as conn:
        cid = conn.execute(text(
            "SELECT id FROM pagina_web.Chatbots WHERE slug = :slug"
        ), {"slug": slug}).scalar()
        if cid is None:
            raise SystemExit(f"No existe el chatbot '{slug}'.")
        filas = conn.execute(text("""
            SELECT [query], score_max, sin_cobertura, input_tokens
            FROM pagina_web.query_chatbots_logs
            WHERE effective_campana = :slug AND active = 1
              AND fecha >= DATEADD(day, -:dias, GETDATE())
            ORDER BY fecha DESC
        """), {"slug": slug, "dias": dias}).mappings().all()
    return cid, filas


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("slug", help="slug del chatbot (ej: vantix)")
    parser.add_argument("--dias", type=int, default=90, help="ventana de consultas (default 90)")
    parser.add_argument("--simular", help="JSON de una tabla candidata, en vez de las cargadas")
    parser.add_argument("--detalle", action="store_true", help="imprimir consulta por consulta")
    args = parser.parse_args()

    chatbot_id, consultas = consultas_del_bot(args.slug, args.dias)
    if not consultas:
        print(f"Sin consultas de '{args.slug}' en los últimos {args.dias} días.")
        return 0

    if args.simular:
        tabla = tabla_desde_json(args.simular, chatbot_id)
        chatbot_tablas.obtener_tablas = lambda cid: [tabla]  # type: ignore[assignment]
        print(f"Simulando con la tabla «{tabla.nombre}» ({len(tabla.filas)} filas, "
              f"modo efectivo: {tabla.modo}).\n")
    else:
        tablas = chatbot_tablas.obtener_tablas(chatbot_id)
        if not tablas:
            raise SystemExit(
                f"El bot '{args.slug}' no tiene tablas cargadas. Usá --simular para probar una."
            )
        print("Tablas cargadas: " + ", ".join(
            f"{t.nombre} ({len(t.filas)} filas, {t.modo})" for t in tablas) + "\n")

    ruteadas, por_motivo, por_tabla = [], Counter(), Counter()
    for fila in consultas:
        # Sin embedding: se mide el ruteo por CLAVE y por TÉRMINOS, que son las dos
        # señales determinísticas. La similitud con la descripción necesitaría un
        # embedding por consulta (y eso sí gasta), y es la señal de última
        # instancia: si el ruteo depende de ella, los términos están mal elegidos.
        consulta = chatbot_tablas.resolver(chatbot_id, fila["query"] or "")
        if consulta is None:
            continue
        ruteadas.append((fila, consulta))
        por_motivo[consulta.motivo] += 1
        por_tabla[consulta.tabla.nombre] += 1

    total = len(consultas)
    n = len(ruteadas)
    print(f"{n} de {total} consultas ({n * 100 // max(1, total)}%) pasarían a responderse por tabla.")
    print("  Por qué rutean: " + ", ".join(f"{k}={v}" for k, v in por_motivo.most_common()))
    if len(por_tabla) > 1:
        print("  Por tabla: " + ", ".join(f"{k}={v}" for k, v in por_tabla.most_common()))

    scores = [f["score_max"] for f, _ in ruteadas if f["score_max"] is not None]
    if scores:
        print(f"  Score RAG que tenían: promedio {sum(scores) / len(scores):+.2f}, "
              f"peor {min(scores):+.2f}, mejor {max(scores):+.2f}")
    tokens = [f["input_tokens"] for f, _ in ruteadas if f["input_tokens"]]
    if tokens:
        print(f"  Tokens de entrada que gastaban: promedio {sum(tokens) // len(tokens)}")

    # Lo importante de mirar: consultas que HOY funcionan bien y se las llevaría la
    # tabla. Si aparecen procedimientos acá, hay que afinar los términos.
    riesgo = [(f, c) for f, c in ruteadas
              if (f["score_max"] or 0) >= 4.0 and not f["sin_cobertura"]]
    if riesgo:
        print(f"\n⚠ {len(riesgo)} consulta(s) que hoy el RAG responde BIEN (score >= 4) "
              "pasarían por la tabla. Revisá que no sean de procedimiento:")
        for f, c in riesgo[:15]:
            print(f"    [{f['score_max']:+.2f}] {(f['query'] or '')[:80]}  ->  "
                  f"{c.tabla.nombre} ({c.motivo}/{c.forma})")

    if args.detalle:
        print("\n--- consulta por consulta ---")
        for f, c in ruteadas:
            claves = [
                " / ".join(fila.datos.get(col.nombre, "") for col in c.tabla.columnas_clave)
                for fila in c.filas[:3]
            ]
            print(f"[{(f['score_max'] if f['score_max'] is not None else 0):+.2f}] "
                  f"{(f['query'] or '')[:70]:<70} -> {c.forma:<9} {c.motivo:<12} "
                  + (f"filas: {', '.join(claves)}" if c.forma == "lookup" else ""))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
