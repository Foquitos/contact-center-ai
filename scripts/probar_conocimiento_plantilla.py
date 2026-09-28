"""Prueba el conocimiento de referencia de una plantilla ANTES de activarlo.

POR QUÉ
-------
Sumarle a la plantilla los documentos de un chatbot (ver
AuditorIA/conocimiento_plantilla.py) cambia lo que la IA sabe de la gestión, y con
eso las calificaciones. Puede corregir OK regalados ("Conocimiento del producto" 160
OK / 4 NO OK en la plantilla 21) o inventar NO OK por pasos que no se escuchan. La
plantilla 21 no tiene revisiones humanas ni Golden Set, así que no hay contra qué
medirla: este script arma la comparación y Calidad revisa SOLO lo que cambió.

QUÉ HACE
--------
1. Toma los llamados más recientes de la plantilla que tengan el audio conservado
   (calidad.AudioAuditoria): no vuelve a descargar nada.
2. Los audita DOS veces, ahora y con el mismo prompt: una SIN conocimiento y otra CON
   los documentos elegidos. Las dos corridas son necesarias: la IA no responde siempre
   igual, y lo que cambia entre la auditoría guardada y la corrida "sin" es el ruido
   normal. Solo lo que cambia entre "sin" y "con" se le puede atribuir al conocimiento.
3. Escribe un CSV con un renglón por llamado y atributo (original / sin / con) y
   muestra el resumen por atributo.

NO toca las auditorías ni la plantilla: los resultados quedan solo en el CSV. Lo único
que escribe es el consumo en pagina_web.IA_Uso (feature='golden_set', igual que el
replay de eval_auditoria.py), para que la prueba no salga gratis en el tablero.

Sin `--ejecutar` solo muestra la muestra y el tamaño del bloque (no gasta tokens).

El audio conservado es POR SERVIDOR: correr donde auditó la plantilla (las de HIDRA
Comercial auditan en prod; en dev hay pocas).

Uso (desde backend/, con el venv activo):
    python ../scripts/probar_conocimiento_plantilla.py --plantilla 21 --chatbot hidra_comercial \\
        --excluir 92,93,95,97 --muestra 30
    python ../scripts/probar_conocimiento_plantilla.py --plantilla 21 --docs 88,89,90,91 --ejecutar
    python ../scripts/probar_conocimiento_plantilla.py --plantilla 21 --seleccion-guardada --ejecutar
"""
import argparse
import csv
import json
import logging
import math
import os
import sys
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import bindparam, create_engine, text  # noqa: E402

from app.config import settings  # noqa: E402
from AuditorIA import audio_store, conocimiento_plantilla  # noqa: E402

logger = logging.getLogger("probar_conocimiento")

TIPOS_TEXTO = {"string", "array_string"}


# --------------------------------------------------------------------------- #
# Comparación (pura)                                                           #
# --------------------------------------------------------------------------- #
def normalizar(valor: Any) -> str:
    """Forma comparable de una respuesta: "No Ok" y "NO OK" son lo mismo, y lo
    guardado en SQL es texto ("True") aunque la IA lo devuelva como bool."""
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return ""
    if isinstance(valor, (list, tuple)):
        return json.dumps(list(valor), ensure_ascii=False).upper()
    return str(valor).strip().upper()


def resumir_cambios(pares: List[Tuple[Any, Any]]) -> Dict[str, Any]:
    """Cuántos pares cambian y en qué dirección ("OK → NO OK": 3)."""
    direcciones: Dict[str, int] = {}
    for antes, despues in pares:
        a, d = normalizar(antes), normalizar(despues)
        if a != d:
            clave = f"{a or '(vacío)'} → {d or '(vacío)'}"
            direcciones[clave] = direcciones.get(clave, 0) + 1
    return {
        "cambian": sum(direcciones.values()),
        "total": len(pares),
        "direcciones": dict(sorted(direcciones.items(), key=lambda kv: -kv[1])),
    }


# --------------------------------------------------------------------------- #
# Datos                                                                        #
# --------------------------------------------------------------------------- #
def resolver_documentos(engine, args) -> List[Dict[str, Any]]:
    if args.docs:
        ids = [int(x) for x in args.docs.split(",") if x.strip()]
        return conocimiento_plantilla.documentos_por_id(engine, ids)
    if args.chatbot:
        bots = [b for b in conocimiento_plantilla.catalogo(engine) if b["slug"] == args.chatbot]
        if not bots:
            raise SystemExit(f"No hay un chatbot '{args.chatbot}' con documentos activos.")
        excluir = {int(x) for x in (args.excluir or "").split(",") if x.strip()}
        ids = [d["doc_id"] for d in bots[0]["documentos"] if d["doc_id"] not in excluir]
        return conocimiento_plantilla.documentos_por_id(engine, ids)
    return conocimiento_plantilla.documentos_para_auditar(engine, args.plantilla)


def muestra_con_audio(engine, plantilla_id: int, cantidad: int, dias: int) -> List[Dict[str, Any]]:
    """Las auditorías más recientes de la plantilla cuyo audio sigue conservado en
    ESTE servidor. Ventana corta a propósito: la base es la productiva."""
    with engine.connect() as conn:
        filas = conn.execute(text("""
            SELECT TOP (:tope) AuditoriaID, IdAplicativo, operadorUsuario, fecha_interaccion,
                   sentido_interaccion, tipificacion_interaccion, duracion_segundos,
                   comentario_interaccion, PuntajeFinal
            FROM calidad.Auditorias
            WHERE PlantillaID = :pid AND IsActive = 1
              AND FechaAuditoria >= DATEADD(day, -:dias, SYSDATETIME())
            ORDER BY FechaAuditoria DESC
        """), {"tope": cantidad * 4, "pid": plantilla_id, "dias": dias}).mappings().all()

    elegidas = []
    for fila in filas:
        resuelto = audio_store.resolver_audio(engine, fila["IdAplicativo"])
        if not resuelto:
            continue
        elegidas.append({**dict(fila), "ruta_audio": resuelto[0]})
        if len(elegidas) >= cantidad:
            break
    return elegidas


def atributos_de_plantilla(engine, plantilla_id: int) -> Dict[int, Dict[str, Any]]:
    with engine.connect() as conn:
        filas = conn.execute(text("""
            SELECT AtributoID, NombreAtributo, TipoDato, Ponderacion, Orden
            FROM calidad.Atributos WHERE PlantillaID = :pid AND IsActive = 1
            ORDER BY Orden, AtributoID
        """), {"pid": plantilla_id}).mappings().all()
    return {int(f["AtributoID"]): dict(f) for f in filas}


def respuestas_guardadas(engine, auditoria_ids: List[int]) -> Dict[int, Dict[int, Any]]:
    with engine.connect() as conn:
        filas = conn.execute(text("""
            SELECT AuditoriaID, AtributoID, ValorResultado
            FROM calidad.AuditoriaDetalles WHERE AuditoriaID IN :ids
        """).bindparams(bindparam("ids", expanding=True)), {"ids": auditoria_ids}).all()
    guardadas: Dict[int, Dict[int, Any]] = {}
    for auditoria_id, atributo_id, valor in filas:
        guardadas.setdefault(int(auditoria_id), {})[int(atributo_id)] = valor
    return guardadas


# --------------------------------------------------------------------------- #
# Corridas                                                                     #
# --------------------------------------------------------------------------- #
def auditar(cliente, plantilla_id: int, llamados: List[Dict[str, Any]], prompt_info: Dict[str, Any],
            workers: int) -> Tuple[List[Optional[Dict[int, Any]]], Dict[str, int], str]:
    """Una corrida sincrónica sobre la muestra. Devuelve, por llamado, {AtributoID:
    valor} (None si falló), los tokens totales y el modelo."""
    import pandas as pd

    from AuditorIA.gemini import apply_auditoria_threads

    df = pd.DataFrame([{
        "audio_dir": ll["ruta_audio"],
        "id_aplicativo": ll["IdAplicativo"],
        "operador": ll["operadorUsuario"],
        "fecha_interaccion": ll["fecha_interaccion"],
        "sentido": ll["sentido_interaccion"],
        "tipificacion": ll["tipificacion_interaccion"],
        "duracion_segundos": ll["duracion_segundos"],
        "comentario": ll["comentario_interaccion"],
    } for ll in llamados])

    df_calidad, _, nombre_id_map, modelo, _ = apply_auditoria_threads(
        df, cliente, plantilla_id, transcribir=False, engine=None,
        max_workers=workers, concurrent_api_calls=max(1, workers // 2),
        prompt_info=prompt_info,
    )
    id_por_nombre = {str(k): int(v) for k, v in (nombre_id_map or {}).items()}

    resultados: List[Optional[Dict[int, Any]]] = []
    for posicion in range(len(llamados)):
        if posicion >= len(df_calidad):
            resultados.append(None)
            continue
        fila = df_calidad.iloc[posicion]
        if str(fila.get("status_auditoria") or "") in ("FALLIDO", "FALLO_TOTAL"):
            resultados.append(None)
            continue
        respuestas = {}
        for columna in df_calidad.columns:
            if str(columna).startswith("Detalle_"):
                atributo_id = id_por_nombre.get(str(columna)[len("Detalle_"):])
                if atributo_id is not None:
                    respuestas[atributo_id] = fila.get(columna)
        resultados.append(respuestas)

    def total(columna: str) -> int:
        return int(df_calidad[columna].fillna(0).sum()) if columna in df_calidad else 0

    tokens = {c: total(c) for c in ("input_tokens", "output_tokens", "thoughts_tokens", "cached_tokens")}
    return resultados, tokens, modelo


def puntaje(respuestas: Optional[Dict[int, Any]], atributos: Dict[int, Dict[str, Any]]) -> Optional[float]:
    from AuditorIA.scoring import calcular_puntaje

    if respuestas is None:
        return None
    return calcular_puntaje([
        {"id": aid, "valor": valor, "ponderacion": float(atributos[aid]["Ponderacion"] or 0),
         "tipo": atributos[aid]["TipoDato"]}
        for aid, valor in respuestas.items() if aid in atributos
    ]).puntaje


def registrar_consumo(tokens: Dict[str, int], modelo: str, plantilla_id: int, brazo: str,
                      llamados: int) -> None:
    from app.uso_ia import registrar_uso_ia

    registrar_uso_ia(
        feature="golden_set", modelo=modelo, modo="sync",
        input_tokens=tokens["input_tokens"], output_tokens=tokens["output_tokens"],
        thoughts_tokens=tokens["thoughts_tokens"],
        ref_id=f"probar_conocimiento:{uuid.uuid4()}",
        extras={"plantilla_id": plantilla_id, "llamados": llamados, "brazo": brazo},
    )


def _promedio(valores: List[Optional[float]]) -> Optional[float]:
    validos = [float(v) for v in valores if v is not None]
    return sum(validos) / len(validos) if validos else None


def _fmt(valor: Optional[float]) -> str:
    return "—" if valor is None else f"{valor:.1f}"


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plantilla", type=int, required=True)
    origen = parser.add_mutually_exclusive_group(required=True)
    origen.add_argument("--docs", help="ids de documentos separados por coma")
    origen.add_argument("--chatbot", help="slug del bot: todos sus documentos activos")
    origen.add_argument("--seleccion-guardada", action="store_true",
                        help="los documentos que la plantilla ya tiene elegidos en el editor")
    parser.add_argument("--excluir", help="con --chatbot: ids de documentos a dejar afuera")
    parser.add_argument("--muestra", type=int, default=30)
    parser.add_argument("--dias", type=int, default=30, help="ventana de auditorías a muestrear")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--salida", help="CSV de salida (default: en el directorio actual)")
    parser.add_argument("--ejecutar", action="store_true", help="auditar de verdad (GASTA TOKENS)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    engine = create_engine(settings.connection_string)

    documentos = resolver_documentos(engine, args)
    if not documentos:
        print("No hay documentos para probar (¿ids dados de baja, o la plantilla no tiene selección?).")
        return 1
    bloque = conocimiento_plantilla.bloque_para_el_prompt(documentos)
    tokens_bloque = conocimiento_plantilla.estimar_tokens(len(bloque))

    print(f"\nPlantilla {args.plantilla} · {len(documentos)} documento(s) · ~{tokens_bloque:,} tokens por llamado")
    for doc in documentos:
        print(f"  [{doc['doc_id']}] {conocimiento_plantilla._titulo(doc)}")

    llamados = muestra_con_audio(engine, args.plantilla, args.muestra, args.dias)
    print(f"\nMuestra: {len(llamados)} llamado(s) con audio conservado en este servidor "
          f"(entorno '{settings.ENVIRONMENT}', últimos {args.dias} días).")
    if not llamados:
        return 1
    if not args.ejecutar:
        print("\nSin --ejecutar no se audita nada. Agregalo para correr las dos pasadas (GASTA TOKENS).\n")
        return 0

    from google import genai

    from AuditorIA import gemini
    from AuditorIA.execution_log import calcular_costo_usd, obtener_tarifa

    base = gemini.prompt(args.plantilla, versionar=False)
    cliente = genai.Client(api_key=settings.GEMINI_AUDITORIA_API_KEY)

    print(f"\nAuditando {len(llamados)} llamado(s) SIN conocimiento...")
    sin, tokens_sin, modelo = auditar(cliente, args.plantilla, llamados, {**base, "conocimiento": ""}, args.workers)
    registrar_consumo(tokens_sin, modelo, args.plantilla, "sin", len(llamados))
    print(f"Auditando {len(llamados)} llamado(s) CON conocimiento...")
    con, tokens_con, _ = auditar(cliente, args.plantilla, llamados, {**base, "conocimiento": bloque}, args.workers)
    registrar_consumo(tokens_con, modelo, args.plantilla, "con", len(llamados))

    atributos = atributos_de_plantilla(engine, args.plantilla)
    guardadas = respuestas_guardadas(engine, [int(ll["AuditoriaID"]) for ll in llamados])

    # Solo entran los llamados que salieron bien en las DOS pasadas.
    validos = [i for i in range(len(llamados)) if sin[i] is not None and con[i] is not None]
    fallidos = len(llamados) - len(validos)

    salida = args.salida or f"probar_conocimiento_p{args.plantilla}_{datetime.now():%Y%m%d_%H%M}.csv"
    with open(salida, "w", newline="", encoding="utf-8-sig") as archivo:
        escritor = csv.writer(archivo)
        escritor.writerow([
            "auditoria_id", "id_aplicativo", "fecha_interaccion", "operador", "atributo", "tipo",
            "original", "sin_conocimiento", "con_conocimiento", "cambia_con_conocimiento",
            "puntaje_original", "puntaje_sin", "puntaje_con",
        ])
        for i in validos:
            ll = llamados[i]
            original = guardadas.get(int(ll["AuditoriaID"]), {})
            p_sin, p_con = puntaje(sin[i], atributos), puntaje(con[i], atributos)
            for atributo_id, meta in atributos.items():
                es_texto = meta["TipoDato"] in TIPOS_TEXTO
                valor_sin, valor_con = sin[i].get(atributo_id), con[i].get(atributo_id)
                cambia = "" if es_texto else ("SI" if normalizar(valor_sin) != normalizar(valor_con) else "NO")
                escritor.writerow([
                    ll["AuditoriaID"], ll["IdAplicativo"], ll["fecha_interaccion"], ll["operadorUsuario"],
                    meta["NombreAtributo"], meta["TipoDato"],
                    original.get(atributo_id), valor_sin, valor_con, cambia,
                    ll["PuntajeFinal"], p_sin, p_con,
                ])

    print(f"\n{len(validos)} llamado(s) comparados" + (f" ({fallidos} fallaron en alguna pasada)" if fallidos else ""))
    print("\nPor atributo — cambio atribuible al conocimiento (sin → con) y ruido normal (guardada → sin):")
    for atributo_id, meta in atributos.items():
        if meta["TipoDato"] in TIPOS_TEXTO:
            continue
        efecto = resumir_cambios([(sin[i].get(atributo_id), con[i].get(atributo_id)) for i in validos])
        ruido = resumir_cambios([
            (guardadas.get(int(llamados[i]["AuditoriaID"]), {}).get(atributo_id), sin[i].get(atributo_id))
            for i in validos
        ])
        if not efecto["cambian"] and not ruido["cambian"]:
            continue
        detalle = ", ".join(f"{k}: {v}" for k, v in efecto["direcciones"].items())
        print(f"  {meta['NombreAtributo'][:60]:<60} {efecto['cambian']:>3}/{efecto['total']:<3}"
              f" ruido {ruido['cambian']:>3}/{ruido['total']:<3} {detalle}")

    print("\nPuntaje promedio: guardado {} · sin {} · con {}".format(
        _fmt(_promedio([llamados[i]["PuntajeFinal"] for i in validos])),
        _fmt(_promedio([puntaje(sin[i], atributos) for i in validos])),
        _fmt(_promedio([puntaje(con[i], atributos) for i in validos])),
    ))

    tarifa = obtener_tarifa(engine, modelo=modelo)
    costo_sin = calcular_costo_usd(tokens_sin, "sync", tarifa)
    costo_con = calcular_costo_usd(tokens_con, "sync", tarifa)
    if costo_sin is not None and costo_con is not None:
        print(f"Costo: sin US${costo_sin:.3f} · con US${costo_con:.3f} "
              f"(input {tokens_sin['input_tokens']:,} → {tokens_con['input_tokens']:,} tokens)")
    print(f"\nDetalle por llamado y atributo: {salida}")
    print("Para Calidad: filtrar cambia_con_conocimiento = SI y escuchar esos llamados.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
