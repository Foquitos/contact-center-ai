"""Marca en la documentación qué secciones son de canal telefónico y cuáles de canal digital.

POR QUÉ
-------
El operador telefónico preguntaba "baja de suministro" y recibía la respuesta
armada alrededor del circuito digital, así que tenía que repreguntar "¿y yo qué
hago en la llamada?".

Se descartó separar el contenido en dos bases: medido sobre los 6 documentos
compartidos, solo el 25% de las secciones es específico de un canal y el 72%
restante (requisitos, documentación, definiciones) es idéntico para los dos.
Duplicarlo garantizaba que las dos copias divergieran. Además, buena parte de lo
"solo digital" es justo lo que el telefónico necesita para DERIVAR al cliente.

QUÉ HACE
--------
Agrega UNA LÍNEA debajo del encabezado de las secciones que sí son de un canal:

    ### Baja de Tasa AP
    **Canal:** digital
    ...contenido intacto...

Con eso el chunk que se recupera dice explícitamente de qué canal es, así que lo
ven tanto BM25 como el modelo al redactar, y cada bot puede priorizar o derivar
sin adivinar. Las secciones que sirven a los dos canales NO se marcan: la ausencia
de marca significa "aplica a ambos", y así no se infla cada chunk con ruido.

GARANTÍA
--------
Lo ÚNICO que cambia son las líneas insertadas. Antes de guardar, se sacan esas
líneas del resultado y se compara carácter por carácter contra el original: si no
coinciden, el documento no se toca. La IA solo decide la etiqueta, nunca reescribe.

Uso (desde backend/, con el venv activo):
    python ../scripts/marcar_canal_docs.py voltara                 # en seco
    python ../scripts/marcar_canal_docs.py voltara --apply
    python ../scripts/marcar_canal_docs.py voltara --apply --reindex
    python ../scripts/marcar_canal_docs.py voltara --doc-id 16     # un documento

CONSUME TOKENS: una llamada cada SECCIONES_POR_LOTE secciones.
"""
import argparse
import json
import logging
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.logging_config import setup_logging  # noqa: E402

setup_logging()
logger = logging.getLogger("marcar_canal")

SECCIONES_POR_LOTE = 12
MARCA = "**Canal:**"
# Solo se marcan estas; "ambos" queda sin marca (ausencia = sirve a los dos).
ETIQUETAS = {"telefonico": "telefónico", "digital": "digital"}

# Secciones más cortas que esto son encabezados sueltos o listas de dos líneas:
# marcarlas es puro ruido en el chunk.
MIN_CHARS_SECCION = 200

_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "secciones": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "n": {"type": "INTEGER", "description": "número de sección tal como se la pasó"},
                    "canal": {
                        "type": "STRING",
                        "enum": ["telefonico", "digital", "ambos"],
                        "description": "a qué canal le sirve el contenido de la sección",
                    },
                },
                "required": ["n", "canal"],
            },
        }
    },
    "required": ["secciones"],
}

_PROMPT = """Trabajás en la mesa de ayuda de una distribuidora eléctrica. Hay DOS equipos de operadores que consultan estos manuales:

- TELEFÓNICO: atiende llamadas con el cliente en línea. Indaga, carga casos en el sistema, le dice cosas al cliente y lo deriva a otros canales.
- DIGITAL: responde por escrito (correo, Oficina Virtual, App). Verifica lo que el cliente ya mandó, pide lo que falta y redacta la respuesta.

Para cada sección decidí a quién le sirve el CONTENIDO:

- "telefonico": describe algo que solo hace el operador en la llamada (protocolos de indagación en vivo, qué decirle al cliente, atención de emergencias por teléfono).
- "digital": describe algo que solo hace el equipo que responde por escrito (validar documentación adjunta, circuitos que se resuelven exclusivamente por Oficina Virtual o correo, plantillas de respuesta escrita).
- "ambos": todo lo demás. Requisitos, documentación exigida, definiciones, normativa, plazos, montos y procedimientos que no dependen del canal.

REGLA IMPORTANTE: que una sección MENCIONE la Oficina Virtual o un correo NO la vuelve "digital". El operador telefónico necesita esos datos para derivar al cliente. Marcá "digital" solo si el TRABAJO que describe lo hace el equipo digital. Ante la duda, "ambos": es la opción segura, porque no le esconde nada a nadie.

SECCIONES:
{secciones}
"""


# ------------------------------------------------------------------- partido

def partir_secciones(md: str):
    """Devuelve [(encabezado, resto)] preservando el texto EXACTO.

    El primer trozo (título del documento e intro, antes del primer ##) se
    devuelve con encabezado vacío y nunca se marca.
    """
    partes = re.split(r"\n(?=#{2,3} )", md)
    salida = []
    for i, p in enumerate(partes):
        # El primer trozo es el título H1 del documento más su intro: el split solo
        # corta en ##/###, así que nunca es una sección marcable (salvo que el doc
        # arranque directamente con un ##).
        if i == 0 and not re.match(r"#{2,3} ", p):
            salida.append(("", p))
            continue
        lineas = p.split("\n", 1)
        salida.append((lineas[0], lineas[1] if len(lineas) > 1 else ""))
    return salida


def _quitar_marcas(md: str) -> str:
    """Saca las líneas de marca para poder comparar contra el original."""
    return "\n".join(l for l in md.split("\n") if not l.startswith(MARCA))


def verificar_sin_perdida(original: str, marcado: str) -> bool:
    """El resultado menos las marcas tiene que ser IDÉNTICO al original."""
    return _quitar_marcas(marcado) == _quitar_marcas(original)


# ---------------------------------------------------------------- clasificar

_CLIENTE = None


def _client():
    """Cliente único a nivel de módulo.

    Creado por llamada, nadie retenía la referencia y el recolector lo cerraba en
    medio del request ("Cannot send a request, as the client has been closed").
    """
    global _CLIENTE
    if _CLIENTE is None:
        from google import genai

        _CLIENTE = genai.Client(
            api_key=settings.GEMINI_CHATBOT_API_KEY or settings.GEMINI_AUDITORIA_API_KEY
        )
    return _CLIENTE


def clasificar_lote(secciones) -> dict:
    """{indice: canal}. Ante cualquier error devuelve {} y el lote queda sin marcar."""
    from google.genai import types

    payload = "\n\n".join(
        f"--- SECCIÓN {n} ---\n{enc}\n{cuerpo[:1200]}" for n, enc, cuerpo in secciones
    )
    try:
        resp = _client().models.generate_content(
            model=settings.CHATBOT_VACIOS_MODELO,
            contents=_PROMPT.format(secciones=payload),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_SCHEMA,  # type: ignore[arg-type]
                temperature=0.0,
            ),
        )
        datos = json.loads(resp.text or "{}")
        return {int(s["n"]): s["canal"] for s in datos.get("secciones", [])}
    except Exception:
        logger.warning("Falló la clasificación de un lote; esas secciones quedan sin marcar.",
                       exc_info=True)
        return {}


def marcar_documento(md: str):
    """Devuelve (markdown_marcado, conteo_por_canal)."""
    secciones = partir_secciones(md)

    # Solo se mandan a clasificar las que valen la pena.
    candidatas = [
        (i, enc, cuerpo) for i, (enc, cuerpo) in enumerate(secciones)
        if enc and len(cuerpo) >= MIN_CHARS_SECCION
    ]

    canales = {}
    for i in range(0, len(candidatas), SECCIONES_POR_LOTE):
        canales.update(clasificar_lote(candidatas[i:i + SECCIONES_POR_LOTE]))

    conteo = {"telefonico": 0, "digital": 0, "ambos": 0, "sin_clasificar": 0}
    salida = []
    for i, (enc, cuerpo) in enumerate(secciones):
        canal = canales.get(i)
        if not enc:
            salida.append(cuerpo)
            continue
        if canal in ETIQUETAS:
            conteo[canal] += 1
            salida.append(f"{enc}\n{MARCA} {ETIQUETAS[canal]}\n{cuerpo}")
        else:
            conteo["ambos" if canal == "ambos" else "sin_clasificar"] += 1
            salida.append(f"{enc}\n{cuerpo}")

    return "\n".join(salida), conteo


# --------------------------------------------------------------------- main

def _docs_del_bot(slug: str, doc_id):
    from app.database import engine

    filtro = "m.id = :doc" if doc_id else """
        (m.chatbot_id = c.id OR EXISTS (SELECT 1 FROM pagina_web.ChatbotDocVinculo v
                                        WHERE v.doc_id = m.id AND v.chatbot_id = c.id))
    """
    with engine.connect() as conn:
        return conn.execute(text(f"""
            SELECT m.id, m.titulo, m.contenido_md
            FROM pagina_web.ChatbotDocMarkdown m
            JOIN pagina_web.Chatbots c ON c.slug = :slug
            WHERE m.activo = 1 AND {filtro}
            ORDER BY m.orden, m.id
        """), {"slug": slug, "doc": doc_id}).mappings().all()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("slug", help="slug del chatbot dueño de los documentos")
    parser.add_argument("--doc-id", type=int, help="marcar un solo documento")
    parser.add_argument("--apply", action="store_true", help="guardar los cambios")
    parser.add_argument("--reindex", action="store_true", help="encolar reindexado al terminar")
    args = parser.parse_args()

    from app.database import engine

    docs = _docs_del_bot(args.slug, args.doc_id)
    if not docs:
        print(f"No hay documentos activos para '{args.slug}'.")
        return 1

    total = {"telefonico": 0, "digital": 0, "ambos": 0, "sin_clasificar": 0}
    a_guardar = []

    for d in docs:
        marcado, conteo = marcar_documento(d["contenido_md"])

        if not verificar_sin_perdida(d["contenido_md"], marcado):
            # No debería pasar nunca: solo se insertan líneas. Si pasa, el documento
            # no se toca — perder conocimiento es mucho peor que no marcarlo.
            print(f"  !! {d['titulo']}: la verificación falló, se OMITE este documento")
            continue

        for k, v in conteo.items():
            total[k] += v
        print(f"  {d['titulo'][:50]:<52} tel={conteo['telefonico']:<3} "
              f"dig={conteo['digital']:<3} ambos={conteo['ambos']:<3} "
              f"sin_clasificar={conteo['sin_clasificar']}")
        a_guardar.append((d["id"], marcado))

    print(f"\nTotal: {total['telefonico']} secciones de teléfono, {total['digital']} de digital, "
          f"{total['ambos']} para ambos (sin marcar), {total['sin_clasificar']} sin clasificar.")

    if not args.apply:
        print("\n(en seco: no se guardó nada; agregá --apply)")
        return 0

    with engine.begin() as conn:
        for doc_id, contenido in a_guardar:
            conn.execute(text("""
                UPDATE pagina_web.ChatbotDocMarkdown
                SET contenido_md = :md, updated_at = SYSDATETIME()
                WHERE id = :id
            """), {"md": contenido, "id": doc_id})
    print(f"\nGuardados {len(a_guardar)} documentos.")

    if args.reindex:
        # Los dos bots comparten estos documentos: hay que reindexar ambos.
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO pagina_web.ChatbotIndexJobs (chatbot_id, requested_by, environment)
                SELECT c.id, NULL, :env FROM pagina_web.Chatbots c
                WHERE c.slug IN ('voltara', 'voltara_digital') AND c.activo = 1
            """), {"env": settings.ENVIRONMENT})
        print("Reindexado encolado para voltara y voltara_digital.")
    else:
        print("Acordate de reindexar AMBOS bots: python reindex_all.py voltara voltara_digital")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
