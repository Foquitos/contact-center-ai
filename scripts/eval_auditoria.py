"""Evaluador de la IA auditora: cuánto le acierta a un humano, atributo por atributo.

Por qué existe: la IA audita miles de llamados y nadie sabía si acierta. La única
señal era el ojo del analista, que no quedaba escrito. Con la verdad humana que
captura la pantalla de revisión (Auditorías Realizadas → botón de revisión, ver
AuditorIA/revision.py), este script mide la plantilla y dice qué atributo está
roto y cuánto cuesta.

DOS MODOS
---------
  --medir   (default, NO gasta tokens) Compara lo que la IA YA respondió contra la
            verdad humana. Es la foto de la plantilla como está hoy en producción.
            Sale de SQL puro: se puede correr todos los días sin costo.

  --replay  (GASTA TOKENS) Vuelve a auditar los mismos llamados con la plantilla
            ACTUAL y compara ESA respuesta contra la verdad humana. Es lo que hay
            que usar después de tocar un prompt: dice si el cambio mejoró o
            empeoró, sobre los mismos casos. Re-usa el audio conservado del store
            (calidad.AudioAuditoria), así que no vuelve a descargar nada; los
            audios de un Golden Set están fijados y no los borra el FIFO.
            NO guarda las auditorías del replay: son de prueba, no van a la
            bandeja del operador. Sí registra su consumo en pagina_web.IA_Uso
            (feature='golden_set') para que el gasto no quede invisible.

MÉTRICAS (ver AuditorIA/golden_metricas.py para el detalle)
  kappa        acuerdo descontando el azar. LA métrica: con 90% de OK en una
               campaña sana, un prompt que responda siempre OK saca 90% de
               accuracy y no sirve para nada. Kappa lo desenmascara.
  falsos EC    la IA marcó Error Crítico donde el humano no lo vio: pone el
               llamado en 0 y castiga a un operador que no se equivocó. Es el
               error más caro del sistema.
  sin responder  la IA se escapó por la salida de emergencia donde sí había
               evidencia: N/A en Calidad ponderada o el campo vacío en un
               atributo opcional (es el mismo error, escrito de dos formas).
  MAE puntaje  cuántos puntos (0-100) se desvía el puntaje publicado.

Uso (desde backend/, con el venv activo):
    python ../scripts/eval_auditoria.py --plantilla 12
    python ../scripts/eval_auditoria.py --set 3 --split test
    python ../scripts/eval_auditoria.py --plantilla 12 --acuerdo      # techo humano
    python ../scripts/eval_auditoria.py --plantilla 12 --detalle
    python ../scripts/eval_auditoria.py --set 3 --replay --json antes.json
    python ../scripts/eval_auditoria.py --cobertura --plantilla 12    # ¿ya se puede medir?
"""
import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from sqlalchemy import bindparam, create_engine, text  # noqa: E402

from app.config import settings  # noqa: E402
from AuditorIA import audio_store, golden_set  # noqa: E402
from AuditorIA.golden_metricas import (  # noqa: E402
    CasoEvaluado,
    ComparacionAtributo,
    Resumen,
    acuerdo_entre_revisores,
    confiabilidad_kappa,
    evaluar,
    interpretar_kappa,
)

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("eval_auditoria")

# Debajo de esto las métricas por atributo son ruido: un caso mueve varios puntos
# de accuracy y el kappa salta con cualquier cosa.
MINIMO_RECOMENDADO = 30


# --------------------------------------------------------------------------- #
# Salida por consola                                                           #
# --------------------------------------------------------------------------- #
def _fmt(valor: Optional[float], sufijo: str = "") -> str:
    return "  n/d" if valor is None else f"{valor:5.1f}{sufijo}"


def imprimir_resumen(resumen: Resumen, titulo: str, detalle: bool = False) -> None:
    print()
    print("=" * 78)
    print(titulo)
    print("=" * 78)

    if resumen.casos == 0:
        print("\n  No hay llamados con verdad humana para esta selección.")
        print("  Revisá auditorías desde 'Auditorías Realizadas' (botón de revisión)")
        print("  y volvé a correr esto.\n")
        return

    print(f"\n  Llamados evaluados : {resumen.casos}")
    print(f"  Comparaciones      : {resumen.comparaciones} (atributo × llamado)")
    print(f"  Acierto global     : {_fmt(resumen.accuracy, '%')}")
    print(f"  Kappa global       : {resumen.kappa_global if resumen.kappa_global is not None else 'n/d'}"
          f"  ({interpretar_kappa(resumen.kappa_global)})")

    if resumen.casos < MINIMO_RECOMENDADO:
        print(f"\n  ⚠ Con {resumen.casos} llamados los números todavía son inestables "
              f"(recomendado: {MINIMO_RECOMENDADO}+).")

    puntaje = resumen.puntaje
    print("\n  --- Puntaje final del llamado ---")
    print(f"  Desvío medio (MAE) : {_fmt(puntaje.mae, ' pts')}  sobre {puntaje.n} llamados")
    print(f"  Peor desvío        : {_fmt(puntaje.max_error, ' pts')}")
    print(f"  Veredicto EC       : {puntaje.ec_discrepantes} discrepancias "
          f"({puntaje.ec_falsos} falsos EC, {puntaje.ec_omitidos} EC omitidos)")
    if puntaje.ec_falsos:
        print(f"    ⚠ {puntaje.ec_falsos} llamado(s) puntuados 0 por la IA que el humano NO reprobó.")

    print("\n  --- Por atributo (peor primero) ---")
    print(f"  {'atributo':<34} {'n':>4} {'acierto':>8} {'kappa':>7}  {'fEC':>4} {'ECom':>5} {'s/r':>4}")
    print(f"  {'-' * 34} {'-' * 4} {'-' * 8} {'-' * 7}  {'-' * 4} {'-' * 5} {'-' * 4}")
    for m in resumen.por_atributo:
        nombre = (m.nombre or "")[:34]
        kappa_txt = "  n/d" if m.kappa is None else f"{m.kappa:5.2f}"
        # El asterisco avisa que ese kappa lo define la muestra y no el prompt.
        if not confiabilidad_kappa(m)["concluyente"]:
            kappa_txt = kappa_txt.rstrip() + "*"
        print(f"  {nombre:<34} {m.n:>4} {_fmt(m.accuracy, '%'):>8} {kappa_txt:>7}  "
              f"{m.falsos_ec:>4} {m.ec_omitidos:>5} {m.sin_responder:>4}")

    print("\n  fEC = falsos Error Crítico · ECom = EC omitidos · s/r = la IA no evaluó (N/A o vacío)")

    # Un kappa 0.00 puede significar "el prompt no distingue nada" o "la muestra no
    # tenía con qué medir". Sin esta aclaración se leen igual y se corrige el prompt
    # equivocado.
    no_concluyentes = [(m, confiabilidad_kappa(m)) for m in resumen.por_atributo]
    no_concluyentes = [(m, c) for m, c in no_concluyentes if not c["concluyente"]]
    if no_concluyentes:
        print("\n  --- (*) Kappa no concluyente ---")
        for metrica, conf in no_concluyentes:
            print(f"  [{metrica.nombre}] {conf['motivo']}")
        print(f"  → {no_concluyentes[0][1]['falta']}")

    # El diagnóstico en castellano: el kappa dice QUE algo anda mal, esto dice QUÉ.
    diagnosticos = [(m, m.as_dict()["diagnostico"]) for m in resumen.por_atributo]
    diagnosticos = [(m, d) for m, d in diagnosticos if d]
    if diagnosticos:
        print("\n  --- Qué le pasa a cada atributo ---")
        for metrica, dx in diagnosticos:
            marca = " (indicio)" if dx["es_indicio"] else ""
            print(f"\n  [{metrica.nombre}] {dx['titulo']}{marca}")
            print(f"    {dx['detalle']}")
            print(f"    → {dx['sugerencia']}")

    if detalle:
        print("\n  --- Desacuerdos caso por caso ---")
        for m in resumen.por_atributo:
            if not m.errores:
                continue
            print(f"\n  [{m.nombre}]  (kappa {m.kappa}, {len(m.errores)} desacuerdos)")
            for e in m.errores[:15]:
                motivo = f"  — {e['motivo']}" if e.get("motivo") else ""
                print(f"    {e['id_aplicativo']:<24} IA: {e['valor_ia']:<14} humano: {e['valor_humano']:<14}{motivo}")
            if len(m.errores) > 15:
                print(f"    ... y {len(m.errores) - 15} más")

        if resumen.puntaje.peores:
            print("\n  --- Llamados con mayor desvío de puntaje ---")
            for p in resumen.puntaje.peores[:10]:
                print(f"    {p['id_aplicativo']:<24} IA: {p['puntaje_ia']:>6}  humano: {p['puntaje_humano']:>6}"
                      f"  (Δ {p['diferencia']})")
    print()


def imprimir_por_version(engine, casos: List[CasoEvaluado]) -> List[Dict[str, Any]]:
    """Kappa y acierto de cada VERSIÓN de la plantilla sobre la misma verdad humana.

    Es la comparación A/B histórica: no re-audita nada, simplemente separa las
    auditorías según con qué prompt se hicieron. Si la v3 tiene mejor kappa que la
    v2 sobre los mismos criterios humanos, el cambio sirvió.
    """
    from AuditorIA import versionado

    por_version: Dict[Any, List[CasoEvaluado]] = {}
    for caso in casos:
        por_version.setdefault(caso.version_id, []).append(caso)

    print()
    print("=" * 78)
    print("POR VERSIÓN DE PLANTILLA — misma verdad humana, distintos prompts")
    print("=" * 78)

    if len(por_version) == 1 and next(iter(por_version)) is None:
        print("\n  Ninguna auditoría tiene versión registrada.")
        print("  Las versiones se empiezan a registrar al auditar, después de aplicar")
        print("  la migración 2026-08-06d. Las auditorías previas quedan sin versión.\n")
        return []

    print(f"\n  {'versión':<12} {'llamados':>9} {'comparac.':>10} {'acierto':>9} {'kappa':>7} {'MAE':>7} {'fEC':>5}")
    print(f"  {'-' * 12} {'-' * 9} {'-' * 10} {'-' * 9} {'-' * 7} {'-' * 7} {'-' * 5}")

    filas = []
    for version_id in sorted(por_version, key=lambda v: (v is None, v or 0)):
        grupo = por_version[version_id]
        resumen = evaluar(grupo)
        if version_id is None:
            etiqueta = "sin versión"
        else:
            detalle = versionado.obtener_version(engine, version_id)
            etiqueta = f"v{detalle['Numero']}" if detalle else f"#{version_id}"
        kappa_txt = "  n/d" if resumen.kappa_global is None else f"{resumen.kappa_global:5.2f}"
        mae_txt = "  n/d" if resumen.puntaje.mae is None else f"{resumen.puntaje.mae:5.1f}"
        print(f"  {etiqueta:<12} {resumen.casos:>9} {resumen.comparaciones:>10} "
              f"{_fmt(resumen.accuracy, '%'):>9} {kappa_txt:>7} {mae_txt:>7} "
              f"{resumen.puntaje.ec_falsos:>5}")
        filas.append({
            "version_id": version_id, "etiqueta": etiqueta,
            "resumen": resumen.as_dict(),
        })

    chicas = [f for f in filas if f["resumen"]["casos"] < 15]
    if chicas:
        print(f"\n  ⚠ {len(chicas)} versión(es) con menos de 15 llamados: su número no distingue")
        print("    una mejora real del ruido. Sumá revisiones antes de decidir.")
    print()
    return filas


def imprimir_vigencia(vigencia: Dict[str, Any]) -> None:
    """Aviso de que el criterio de la campaña se movió abajo de la verdad humana."""
    print()
    print("=" * 78)
    print("VIGENCIA DE LA VERDAD HUMANA")
    print("=" * 78)

    actual = vigencia.get("version_actual")
    if not actual:
        print("\n  La plantilla actual todavía no tiene versión registrada (se crea al")
        print("  auditar o al revisar, después de aplicar la migración 2026-08-06d).\n")
        return

    print(f"\n  Versión actual de la plantilla : v{actual.get('Numero')} ({actual.get('Hash', '')[:12]})")
    print(f"  Revisiones vigentes            : {vigencia.get('revisiones_vigentes', 0)}")
    print(f"  Revisiones con criterio viejo  : {vigencia.get('revisiones_vencidas', 0)}")
    if vigencia.get("sin_version"):
        print(f"  Revisiones sin versión         : {vigencia['sin_version']}  (previas a la Fase 2)")

    vencidos = vigencia.get("atributos_vencidos") or []
    if vencidos:
        print("\n  Atributos cuyo criterio cambió DESPUÉS de que se revisaran:")
        for attr in vencidos:
            nombre = attr.get("nombre") or f"atributo {attr['atributo_id']}"
            print(f"    - {nombre:<40} {attr['revisiones']} revisión(es) afectadas")
        print("\n  Para esos atributos la verdad guardada mide la política vieja: conviene")
        print("  re-revisar esos llamados (el audio sigue siendo válido, lo que cambió es")
        print("  el criterio). El resto de los atributos sigue midiendo bien.")
    else:
        print("\n  Ningún criterio cambió desde que se revisó: la verdad está al día.")
    print()


def imprimir_comparacion(antes: Resumen, despues: Resumen) -> None:
    """Producción vs. replay: qué mejoró y qué empeoró con la plantilla actual."""
    print()
    print("=" * 78)
    print("COMPARACIÓN — lo que está guardado vs. la plantilla actual (replay)")
    print("=" * 78)
    print(f"\n  {'métrica':<28} {'guardado':>12} {'replay':>12} {'cambio':>10}")
    print(f"  {'-' * 28} {'-' * 12} {'-' * 12} {'-' * 10}")

    def linea(nombre, a, b, sufijo="", mejor_es_alto=True):
        if a is None or b is None:
            print(f"  {nombre:<28} {'n/d':>12} {'n/d':>12} {'':>10}")
            return
        delta = b - a
        flecha = "=" if abs(delta) < 1e-9 else ("↑" if delta > 0 else "↓")
        bueno = (delta > 0) == mejor_es_alto
        marca = "" if abs(delta) < 1e-9 else ("  ✓" if bueno else "  ✗")
        print(f"  {nombre:<28} {a:>11.2f}{sufijo} {b:>11.2f}{sufijo} {flecha}{abs(delta):>8.2f}{marca}")

    linea("Acierto global (%)", antes.accuracy, despues.accuracy)
    linea("Kappa global", antes.kappa_global, despues.kappa_global)
    linea("MAE puntaje (pts)", antes.puntaje.mae, despues.puntaje.mae, mejor_es_alto=False)
    linea("Falsos EC", float(antes.puntaje.ec_falsos), float(despues.puntaje.ec_falsos), mejor_es_alto=False)
    linea("EC omitidos", float(antes.puntaje.ec_omitidos), float(despues.puntaje.ec_omitidos), mejor_es_alto=False)

    print("\n  --- Kappa por atributo ---")
    kappas_antes = {m.atributo_id: m.kappa for m in antes.por_atributo}
    for m in despues.por_atributo:
        previo = kappas_antes.get(m.atributo_id)
        if previo is None or m.kappa is None:
            print(f"  {(m.nombre or '')[:34]:<34} {'n/d':>8}")
            continue
        delta = m.kappa - previo
        flecha = "=" if abs(delta) < 1e-9 else ("↑" if delta > 0 else "↓")
        print(f"  {(m.nombre or '')[:34]:<34} {previo:>6.2f} → {m.kappa:>6.2f}  {flecha}{abs(delta):>5.2f}")
    print()


# --------------------------------------------------------------------------- #
# Replay: re-auditar el set con la plantilla actual                            #
# --------------------------------------------------------------------------- #
def _contexto_de_llamados(engine, auditoria_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """Metadatos de cada llamado, para reconstruir el bloque Call_details del prompt.

    El replay tiene que ver lo MISMO que vio la auditoría original: si el prompt
    llevaba la tipificación y el operador, sacarlos ahora cambiaría el resultado
    por un motivo que no es el prompt.
    """
    ids = [int(i) for i in auditoria_ids if i is not None]
    if not ids:
        return {}
    with engine.connect() as conn:
        filas = conn.execute(text("""
            SELECT AuditoriaID, IdAplicativo, operadorUsuario, fecha_interaccion,
                   sentido_interaccion, tipificacion_interaccion, duracion_segundos,
                   comentario_interaccion, CampanaID, EmpresaID
            FROM calidad.Auditorias
            WHERE AuditoriaID IN :ids
        """).bindparams(bindparam("ids", expanding=True)), {"ids": ids}).mappings().all()
    return {int(f["AuditoriaID"]): dict(f) for f in filas}


def replay(engine, casos: List[CasoEvaluado], plantilla_id: int,
           max_workers: int = 6) -> List[CasoEvaluado]:
    """Re-audita los llamados del set y devuelve casos con el valor_ia NUEVO.

    La verdad humana (valor_humano) se conserva tal cual: lo único que cambia es
    contra qué se la compara.
    """
    import pandas as pd
    from google import genai

    from AuditorIA.gemini import apply_auditoria_threads

    contexto = _contexto_de_llamados(engine, [c.auditoria_id for c in casos if c.auditoria_id])

    filas = []
    casos_con_audio = []
    sin_audio = []
    for caso in casos:
        resuelto = audio_store.resolver_audio(engine, caso.id_aplicativo)
        if not resuelto:
            sin_audio.append(caso.id_aplicativo)
            continue
        ruta, _, _ = resuelto
        meta = contexto.get(caso.auditoria_id or -1, {})
        filas.append({
            "audio_dir": ruta,
            "id_aplicativo": caso.id_aplicativo,
            "operador": meta.get("operadorUsuario"),
            "fecha_interaccion": meta.get("fecha_interaccion"),
            "sentido": meta.get("sentido_interaccion"),
            "tipificacion": meta.get("tipificacion_interaccion"),
            "duracion_segundos": meta.get("duracion_segundos"),
            "comentario": meta.get("comentario_interaccion"),
        })
        casos_con_audio.append(caso)

    if sin_audio:
        print(f"\n  ⚠ {len(sin_audio)} llamado(s) sin audio conservado: no se pueden re-auditar.")
        print("    (Si son de un Golden Set, agregalos de nuevo para que queden fijados;")
        print("     si el FIFO ya los borró, no hay forma de recuperarlos.)")
    if not filas:
        print("\n  No quedó ningún llamado con audio para el replay.\n")
        return []

    print(f"\n  Re-auditando {len(filas)} llamado(s) con la plantilla {plantilla_id}... "
          f"(esto GASTA TOKENS)")

    cliente = genai.Client(api_key=settings.GEMINI_AUDITORIA_API_KEY)
    df = pd.DataFrame(filas)
    df_calidad, _, nombre_id_map, modelo, _ = apply_auditoria_threads(
        df, cliente, plantilla_id, transcribir=False, engine=engine,
        max_workers=max_workers, concurrent_api_calls=max(1, max_workers // 2),
    )

    _registrar_consumo(df_calidad, modelo, plantilla_id)

    # Las respuestas llegan como Detalle_<nombre de atributo>; la verdad humana
    # está indexada por AtributoID. El mapa lo devuelve el propio prompt.
    id_por_nombre = {str(k): int(v) for k, v in (nombre_id_map or {}).items()}
    nuevos: List[CasoEvaluado] = []
    fallidos = 0

    for posicion, caso in enumerate(casos_con_audio):
        if posicion >= len(df_calidad):
            break
        fila = df_calidad.iloc[posicion]
        if str(fila.get("status_auditoria") or "") == "FALLIDO":
            fallidos += 1
            continue

        respuestas: Dict[int, Any] = {}
        for columna in df_calidad.columns:
            if not str(columna).startswith("Detalle_"):
                continue
            nombre = str(columna)[len("Detalle_"):]
            atributo_id = id_por_nombre.get(nombre)
            if atributo_id is not None:
                respuestas[atributo_id] = fila.get(columna)

        nuevos.append(CasoEvaluado(
            id_aplicativo=caso.id_aplicativo,
            auditoria_id=caso.auditoria_id,
            split=caso.split,
            revisor_id=caso.revisor_id,
            atributos=[
                ComparacionAtributo(
                    atributo_id=attr.atributo_id,
                    nombre=attr.nombre,
                    tipo=attr.tipo,
                    ponderacion=attr.ponderacion,
                    # El atributo que no vino en la respuesta quedó sin responder
                    # (el caso de los opcionales): eso también se mide.
                    valor_ia=respuestas.get(attr.atributo_id),
                    valor_humano=attr.valor_humano,
                    motivo=attr.motivo,
                )
                for attr in caso.atributos
            ],
        ))

    if fallidos:
        print(f"  ⚠ {fallidos} llamado(s) fallaron en el replay y no entran en las métricas.")
    return nuevos


def _registrar_consumo(df_calidad, modelo: str, plantilla_id: int) -> None:
    """Anota el gasto del replay en pagina_web.IA_Uso con feature='golden_set'.

    Sin esto, evaluar sale gratis en los tableros y descuadra el presupuesto
    mensual de IA: son llamados a Gemini como cualquier otro.
    """
    try:
        import uuid

        from app.uso_ia import registrar_uso_ia

        def total(columna: str) -> int:
            return int(df_calidad[columna].fillna(0).sum()) if columna in df_calidad else 0

        registrar_uso_ia(
            feature="golden_set",
            modelo=modelo,
            modo="sync",
            input_tokens=total("input_tokens"),
            output_tokens=total("output_tokens"),
            thoughts_tokens=total("thoughts_tokens"),
            ref_id=f"golden_set:{uuid.uuid4()}",
            extras={"plantilla_id": plantilla_id, "llamados": int(len(df_calidad))},
        )
    except Exception:
        logger.exception("No se pudo registrar el consumo del replay en IA_Uso.")


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mide cuánto acierta la IA auditora contra la verdad humana.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    origen = parser.add_mutually_exclusive_group(required=True)
    origen.add_argument("--plantilla", type=int, help="Evaluar TODAS las revisiones de una plantilla.")
    origen.add_argument("--set", type=int, dest="golden_set_id", help="Evaluar un Golden Set congelado.")

    parser.add_argument("--split", choices=("train", "test"), help="Solo un lado del set.")
    parser.add_argument("--replay", action="store_true",
                        help="Re-auditar con la plantilla actual. GASTA TOKENS.")
    parser.add_argument("--acuerdo", action="store_true",
                        help="Medir el acuerdo entre revisores humanos (el techo de la IA).")
    parser.add_argument("--cobertura", action="store_true",
                        help="Solo informar cuánta verdad humana hay cargada.")
    parser.add_argument("--por-version", action="store_true", dest="por_version",
                        help="Comparar las versiones de la plantilla entre sí (A/B histórico, sin tokens).")
    parser.add_argument("--vigencia", action="store_true",
                        help="Ver si la campaña cambió algún criterio desde que se revisó.")
    parser.add_argument("--detalle", action="store_true", help="Listar los desacuerdos caso por caso.")
    parser.add_argument("--json", dest="salida_json", help="Guardar el resultado para diffear entre corridas.")
    parser.add_argument("--workers", type=int, default=6, help="Hilos del replay (default 6).")
    args = parser.parse_args()

    engine = create_engine(settings.connection_string)

    plantilla_id = args.plantilla
    if args.golden_set_id is not None:
        juego = golden_set.obtener_set(engine, args.golden_set_id)
        if juego is None:
            print(f"No existe el Golden Set {args.golden_set_id}.")
            return 1
        plantilla_id = int(juego["PlantillaID"])
        etiqueta = f"Golden Set «{juego['Nombre']}» (plantilla {plantilla_id})"
    else:
        etiqueta = f"Plantilla {plantilla_id} — todas las revisiones"

    if args.split:
        etiqueta += f" · split {args.split}"

    if args.cobertura:
        datos = golden_set.cobertura(engine, plantilla_id)
        print(f"\nCobertura de verdad humana — plantilla {plantilla_id}")
        print(f"  Auditorías totales   : {datos.get('AuditoriasTotales')}")
        print(f"  Revisadas por humanos: {datos.get('Auditorias')} ({datos.get('PorcentajeRevisado')}%)")
        print(f"  Revisores distintos  : {datos.get('Revisores')}")
        print(f"  Primera / última     : {datos.get('Primera')} / {datos.get('Ultima')}")
        revisadas = int(datos.get("Auditorias") or 0)
        if revisadas < MINIMO_RECOMENDADO:
            faltan = MINIMO_RECOMENDADO - revisadas
            print(f"\n  Faltan ~{faltan} revisiones para que las métricas sean estables.\n")
        else:
            print("\n  Ya hay suficiente para medir.\n")
        return 0

    if args.acuerdo:
        por_revisor = golden_set.cargar_casos_por_revisor(
            engine, golden_set_id=args.golden_set_id, plantilla_id=args.plantilla
        )
        resultado = acuerdo_entre_revisores(por_revisor)
        print(f"\nAcuerdo entre revisores humanos — {etiqueta}")
        if resultado is None:
            print("\n  Ningún llamado fue revisado por más de una persona.")
            print("  Para medir el techo, hacé que dos analistas revisen los mismos 20-30 llamados.\n")
            return 0
        print(f"\n  Llamados revisados por 2+ personas : {resultado['llamados_solapados']}")
        print(f"  Comparaciones                      : {resultado['comparaciones']}")
        print(f"  Acuerdo                            : {resultado['acuerdo']}%")
        print(f"  Kappa                              : {resultado['kappa']} "
              f"({interpretar_kappa(resultado['kappa'])})")
        print("\n  Este es el TECHO: lo que dos humanos no logran acordar, no se lo puede")
        print("  exigir a la IA. Si la brecha con el kappa de la IA es chica, el problema")
        print("  ya no es el prompt sino la definición del criterio.\n")
        return 0

    casos = golden_set.cargar_casos(
        engine, golden_set_id=args.golden_set_id, plantilla_id=args.plantilla, split=args.split
    )
    resumen_guardado = evaluar(casos)
    imprimir_resumen(resumen_guardado, f"GUARDADO — {etiqueta}", detalle=args.detalle)

    salida: Dict[str, Any] = {
        "plantilla_id": plantilla_id,
        "golden_set_id": args.golden_set_id,
        "split": args.split,
        "guardado": resumen_guardado.as_dict(),
    }

    # La vigencia se informa SIEMPRE que haya algo vencido, aunque no la pidan: un
    # número calculado sobre criterios viejos es peor que no tener número, porque
    # se lo cree igual.
    vigencia = golden_set.diagnostico_vigencia(
        engine, plantilla_id=plantilla_id, golden_set_id=args.golden_set_id
    )
    if args.vigencia or vigencia.get("revisiones_vencidas"):
        imprimir_vigencia(vigencia)
        salida["vigencia"] = vigencia

    if args.por_version and casos:
        salida["por_version"] = imprimir_por_version(engine, casos)

    if args.replay and casos:
        casos_replay = replay(engine, casos, plantilla_id, max_workers=args.workers)
        if casos_replay:
            resumen_replay = evaluar(casos_replay)
            imprimir_resumen(resumen_replay, f"REPLAY (plantilla actual) — {etiqueta}",
                             detalle=args.detalle)
            imprimir_comparacion(resumen_guardado, resumen_replay)
            salida["replay"] = resumen_replay.as_dict()

    if args.salida_json:
        with open(args.salida_json, "w", encoding="utf-8") as f:
            json.dump(salida, f, ensure_ascii=False, indent=2, default=str)
        print(f"  Resultado guardado en {args.salida_json}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
