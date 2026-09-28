"""Cómo viene funcionando una plantilla: evidencia real para revisarla.

POR QUÉ EXISTE
--------------
El asistente revisaba una plantilla **leyéndola**, como un corrector de estilo: veía el
texto de los prompts y los tipos de dato, pero no tenía idea de qué había pasado cuando
esa plantilla audita. Con eso no se puede detectar lo que más importa:

- El criterio que **no discrimina**: 1.200 llamados y el 99% dio OK. O no mide nada, o
  está mal redactado y la IA se va siempre para el mismo lado.
- La **opción muerta**: una opción del enum que nadie eligió nunca en 90 días.
- La lista que **se queda corta**: el 40% de las respuestas cae en "Otros".
- El atributo que **el auditor corrige a mano una y otra vez**: es el desacuerdo
  IA-humano del Golden Set, que hasta acá vivía solo en la pantalla de Evaluación y no
  volvía nunca a la plantilla que lo causó.
- El texto libre que se **recorta** seguido (el tope de `limites_texto`): el atributo
  pide más de lo que entra.

Este módulo junta esas dos fuentes —el uso real (`calidad.AuditoriaDetalles`) y la
verdad humana (`calidad.AuditoriaRevisionDetalles`, vía golden_set/golden_metricas)— y
las deja listas para dos cosas: meterlas en el prompt de la revisión y mostrarlas como
señales en pantalla.

CUIDADOS
--------
- Todo va contra la base **productiva**, así que la ventana es acotada (90 días por
  defecto, `REVISION_EVIDENCIA_DIAS`) y las agrupaciones nunca son sobre el texto
  completo: `ValorResultado` es un NVARCHAR(MAX) y agrupar por él es carísimo. Se
  agrupa por los primeros 200 caracteres y SOLO en los tipos de valor acotado.
- Es **best-effort**: si algo falla o la migración de turno no está, se devuelve lo que
  se pudo y la revisión sigue. La evidencia mejora la propuesta; no puede impedirla.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Engine

from AuditorIA import limites_texto

logger = logging.getLogger(__name__)

VENTANA_DIAS_DEFECTO = 90
# Tope de auditorías que se agregan. Es lo que vuelve PREDECIBLE el costo de la consulta:
# `calidad.AuditoriaDetalles` tiene una fila por atributo y por auditoría, así que una
# plantilla muy usada puede tener millones de filas en 90 días y la agregación completa
# se va a minutos —bloqueando la request y, de paso, la base productiva. Con las últimas
# 2.000 auditorías las proporciones ya son estables (el margen de error de un porcentaje
# sobre 2.000 casos es de ±2 puntos) y el trabajo queda acotado pase lo que pase.
MAXIMO_AUDITORIAS_DEFECTO = 2000
# Timeout duro de cada consulta. Si la base está cargada, es preferible quedarse sin
# evidencia (la revisión sigue, sobre el texto) que colgar al usuario esperando.
TIMEOUT_CONSULTA_SEG = 20

# Tipos cuyo valor se puede agrupar para sacar una distribución. El texto libre queda
# afuera a propósito (cada respuesta es distinta y el GROUP BY sería sobre un LOB).
TIPOS_ACOTADOS = ("enum", "array_enum", "critical_audit", "boolean", "integer", "number")

# Cuántos valores distintos se reportan por atributo (los más frecuentes).
TOPE_VALORES = 8
# Cuántas correcciones humanas y motivos se citan por atributo.
TOPE_CORRECCIONES = 4
TOPE_MOTIVOS = 3

# Umbrales de las señales. Todos piden un mínimo de casos: con 3 respuestas no se
# concluye nada y una señal falsa hace que se desconfíe de todas.
MINIMO_PARA_CONCLUIR = 30
PCT_NO_DISCRIMINA = 97.0
PCT_SALIDA_SEGURA_ALTA = 25.0
PCT_RECORTE_ALTO = 10.0
PCT_SIN_RESPONDER_ALTO = 60.0
MINIMO_REVISIONES = 8
KAPPA_BAJO = 0.4


def _ventana_dias(dias: Optional[int]) -> int:
    if dias:
        return max(1, int(dias))
    try:
        from app.config import settings
        return max(1, int(getattr(settings, "REVISION_EVIDENCIA_DIAS", VENTANA_DIAS_DEFECTO)))
    except Exception:  # noqa: BLE001
        return VENTANA_DIAS_DEFECTO


def _entero_de_config(nombre: str, defecto: int) -> int:
    try:
        from app.config import settings
        return max(1, int(getattr(settings, nombre, defecto)))
    except Exception:  # noqa: BLE001
        return defecto


def _poner_timeout(conn, segundos: int) -> None:
    """Timeout de consulta real, sobre la conexión de pyodbc.

    OJO: `conn.connection` es el proxy del pool de SQLAlchemy y asignarle un atributo NO
    llega al driver (se queda en el proxy), así que hay que bajar hasta la conexión DBAPI.
    Sin esto el timeout es decorativo — que es como estaba escrito acá al principio.
    """
    proxy = getattr(conn, "connection", None)
    destino = (getattr(proxy, "dbapi_connection", None)
               or getattr(proxy, "driver_connection", None)
               or proxy)
    try:
        destino.timeout = segundos
    except Exception:  # noqa: BLE001 - driver que no lo soporta: se sigue sin timeout
        logger.debug("El driver no acepta timeout de consulta; la evidencia corre sin tope.")


def _columna_fecha(conn) -> str:
    """`FechaAuditoria` es la fecha en que se auditó y nunca viene nula; se prefiere a
    `fecha_interaccion`, que la carga el origen y puede faltar."""
    try:
        existe = conn.execute(text("""
            SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = 'calidad' AND TABLE_NAME = 'Auditorias'
              AND COLUMN_NAME = 'FechaAuditoria'
        """)).first()
        return "FechaAuditoria" if existe else "fecha_interaccion"
    except Exception:  # noqa: BLE001
        return "fecha_interaccion"


# Las consultas van como constantes (y no embebidas) para poder validarlas contra SQL
# Server sin ejecutarlas: ver tests/test_evidencia_plantilla_sql.py, que chequea nombres
# de tablas y columnas con `sys.dm_exec_describe_first_result_set`. Es la única forma de
# verificar el esquema sin escanear la base productiva.
# La muestra: las últimas N auditorías de la plantilla dentro de la ventana. Todo lo
# demás se calcula SOBRE ESTA MUESTRA, así que los porcentajes cierran entre sí y el costo
# de la consulta no depende de cuánto haya auditado la plantilla en su vida.
SQL_MUESTRA = """
    SELECT TOP (:tope) AuditoriaID
    FROM calidad.Auditorias
    WHERE PlantillaID = :pid AND {col_fecha} >= :desde
    ORDER BY {col_fecha} DESC
"""

SQL_TOTAL_AUDITORIAS = """
    SELECT COUNT(*) FROM (
""" + SQL_MUESTRA + """
    ) muestra
"""

SQL_USO_POR_ATRIBUTO = """
    WITH muestra AS (
""" + SQL_MUESTRA + """
    )
    SELECT ad.AtributoID,
           COUNT(*) AS Respuestas,
           SUM(CASE WHEN RIGHT(ad.ValorResultado, :largo_marca) = :marca THEN 1 ELSE 0 END) AS Recortados,
           AVG(CAST(LEN(ad.ValorResultado) AS FLOAT)) AS LargoPromedio
    FROM calidad.AuditoriaDetalles ad
    JOIN muestra m ON m.AuditoriaID = ad.AuditoriaID
    GROUP BY ad.AtributoID
"""

# El LEFT(...) es lo que hace viable el GROUP BY: ValorResultado es NVARCHAR(MAX) y
# agrupar por el valor entero es carísimo (y revienta al ordenar filas grandes).
SQL_DISTRIBUCION = """
    WITH muestra AS (
""" + SQL_MUESTRA + """
    )
    SELECT ad.AtributoID,
           LEFT(CAST(ad.ValorResultado AS NVARCHAR(200)), 200) AS Valor,
           COUNT(*) AS N
    FROM calidad.AuditoriaDetalles ad
    JOIN muestra m ON m.AuditoriaID = ad.AuditoriaID
    JOIN calidad.Atributos at ON at.AtributoID = ad.AtributoID
    WHERE at.PlantillaID = :pid AND at.TipoDato IN ({tipos})
    GROUP BY ad.AtributoID, LEFT(CAST(ad.ValorResultado AS NVARCHAR(200)), 200)
"""


def _uso_real(engine: Engine, plantilla_id: int, dias: int) -> Dict[str, Any]:
    """Qué respondió la IA en las últimas auditorías de la plantilla, por atributo.

    Trabaja siempre sobre una MUESTRA acotada (las últimas `MAXIMO_AUDITORIAS` dentro de
    la ventana), no sobre todo el histórico: ver el comentario de esa constante.
    """
    desde = datetime.now() - timedelta(days=dias)
    tope = _entero_de_config("REVISION_EVIDENCIA_MAX_AUDITORIAS", MAXIMO_AUDITORIAS_DEFECTO)
    timeout = _entero_de_config("REVISION_EVIDENCIA_TIMEOUT_SEG", TIMEOUT_CONSULTA_SEG)
    datos: Dict[int, Dict[str, Any]] = {}

    with engine.connect() as conn:
        # Corta cuelgues: esto corre contra la base productiva mientras alguien espera.
        _poner_timeout(conn, timeout)

        col_fecha = _columna_fecha(conn)
        params = {"pid": plantilla_id, "desde": desde, "tope": tope}
        # Se compara el FINAL del valor y no un LIKE: la marca lleva corchetes y en
        # T-SQL `[recortado]` dentro de un LIKE es una clase de caracteres, no el texto.
        marca = limites_texto.MARCA_RECORTE

        inicio = time.perf_counter()
        auditorias = conn.execute(
            text(SQL_TOTAL_AUDITORIAS.format(col_fecha=col_fecha)), params).scalar() or 0

        filas = conn.execute(
            text(SQL_USO_POR_ATRIBUTO.format(col_fecha=col_fecha)),
            dict(params, marca=marca, largo_marca=len(marca)),
        ).mappings().all()

        for fila in filas:
            datos[int(fila["AtributoID"])] = {
                "respuestas": int(fila["Respuestas"] or 0),
                "recortados": int(fila["Recortados"] or 0),
                "largo_promedio": round(float(fila["LargoPromedio"]), 1) if fila["LargoPromedio"] else None,
                "valores": [],
            }

        # Distribución solo de los tipos de valor acotado (ver TIPOS_ACOTADOS): el texto
        # libre no se agrupa (cada respuesta es distinta y el GROUP BY sería sobre un LOB).
        tipos = ", ".join(f"'{t}'" for t in TIPOS_ACOTADOS)
        distribucion = conn.execute(
            text(SQL_DISTRIBUCION.format(col_fecha=col_fecha, tipos=tipos)), params
        ).mappings().all()
        tardanza = time.perf_counter() - inicio

    for fila in distribucion:
        entrada = datos.get(int(fila["AtributoID"]))
        if entrada is None:
            continue
        entrada["valores"].append({"valor": (fila["Valor"] or "").strip(), "n": int(fila["N"] or 0)})

    for entrada in datos.values():
        total = entrada["respuestas"] or 1
        entrada["valores"].sort(key=lambda v: v["n"], reverse=True)
        entrada["distintos"] = len(entrada["valores"])
        for valor in entrada["valores"]:
            valor["pct"] = round(100.0 * valor["n"] / total, 1)
        entrada["valores"] = entrada["valores"][:TOPE_VALORES]

    logger.info("Evidencia de la plantilla %s: %s auditorías de muestra, %s atributos, %.1fs.",
                plantilla_id, auditorias, len(datos), tardanza)
    return {"auditorias": int(auditorias), "por_atributo": datos, "desde": desde,
            "muestra_topada": int(auditorias) >= tope, "tope_muestra": tope}


def _acuerdo_humano(engine: Engine, plantilla_id: int) -> Dict[int, Dict[str, Any]]:
    """Desacuerdo IA-humano por atributo, desde las revisiones ya guardadas.

    Reusa el mismo cálculo que la pantalla de Evaluación (golden_metricas) para que la
    revisión y el tablero no digan números distintos sobre lo mismo. No se filtra por
    fecha: las revisiones humanas son el recurso escaso, no hay tantas como para acotar.
    """
    from AuditorIA import golden_metricas, golden_set

    casos = golden_set.cargar_casos(engine, plantilla_id=plantilla_id)
    if not casos:
        return {}

    resultado: Dict[int, Dict[str, Any]] = {}
    for metrica in golden_metricas.metricas_por_atributo(casos):
        # Correcciones más frecuentes: "la IA dijo X y el auditor puso Y". Es lo que le
        # muestra al modelo en qué se equivoca, no solo cuánto.
        pares: Dict[tuple, int] = {}
        motivos: List[str] = []
        for error in metrica.errores:
            clave = (str(error.get("valor_ia")), str(error.get("valor_humano")))
            pares[clave] = pares.get(clave, 0) + 1
            motivo = (error.get("motivo") or "").strip()
            if motivo and motivo not in motivos:
                motivos.append(motivo)

        correcciones = [
            {"ia": ia, "humano": humano, "n": n}
            for (ia, humano), n in sorted(pares.items(), key=lambda kv: kv[1], reverse=True)
        ][:TOPE_CORRECCIONES]

        resultado[int(metrica.atributo_id)] = {
            "revisiones": metrica.n,
            "aciertos": metrica.aciertos,
            "accuracy": metrica.accuracy,
            "kappa": metrica.kappa,
            "falsos_ec": metrica.falsos_ec,
            "ec_omitidos": metrica.ec_omitidos,
            "correcciones": correcciones,
            "motivos": motivos[:TOPE_MOTIVOS],
        }
    return resultado


def evidencia_de_plantilla(engine: Engine, plantilla_id: int,
                           dias: Optional[int] = None) -> Dict[str, Any]:
    """Uso real + acuerdo humano por atributo. Best-effort: nunca lanza."""
    dias = _ventana_dias(dias)
    evidencia: Dict[str, Any] = {
        "ventana_dias": dias, "auditorias": 0, "por_atributo": {},
        "hay_datos": False, "desde": None, "muestra_topada": False,
    }
    try:
        uso = _uso_real(engine, plantilla_id, dias)
        evidencia["auditorias"] = uso["auditorias"]
        evidencia["desde"] = uso["desde"].strftime("%Y-%m-%d")
        evidencia["por_atributo"] = uso["por_atributo"]
        evidencia["muestra_topada"] = uso["muestra_topada"]
    except Exception as e:  # noqa: BLE001
        logger.warning("No se pudo leer el uso real de la plantilla %s: %s", plantilla_id, e)

    try:
        inicio = time.perf_counter()
        acuerdo = _acuerdo_humano(engine, plantilla_id)
        logger.info("Acuerdo humano de la plantilla %s: %s atributos revisados, %.1fs.",
                    plantilla_id, len(acuerdo), time.perf_counter() - inicio)
        for atributo_id, metrica in acuerdo.items():
            evidencia["por_atributo"].setdefault(atributo_id, {"respuestas": 0, "valores": []})
            evidencia["por_atributo"][atributo_id].update(metrica)
    except Exception as e:  # noqa: BLE001
        logger.warning("No se pudo leer el acuerdo humano de la plantilla %s: %s", plantilla_id, e)

    evidencia["hay_datos"] = bool(evidencia["por_atributo"])
    return evidencia


# --------------------------------------------------------------------------- #
# Lectura de la evidencia: señales y bloque para el prompt                     #
# --------------------------------------------------------------------------- #
def _pct(parte: float, total: float) -> float:
    return round(100.0 * parte / total, 1) if total else 0.0


def senales_de_evidencia(evidencia: Dict[str, Any],
                         atributos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Problemas que solo se ven con los datos en la mano.

    Mismo formato que `asistente_plantillas.senales_de_plantilla` (que mira la
    estructura), para que en pantalla y en el prompt vayan todas juntas.
    `atributos` viene en la forma canónica del asistente.
    """
    senales: List[Dict[str, Any]] = []
    por_atributo = (evidencia or {}).get("por_atributo") or {}
    dias = (evidencia or {}).get("ventana_dias", VENTANA_DIAS_DEFECTO)
    auditorias = (evidencia or {}).get("auditorias", 0)

    def _senal(clave: str, mensaje: str, severidad: str, attr: Dict[str, Any]):
        senales.append({
            "clave": clave, "mensaje": mensaje, "severidad": severidad,
            "atributo_id": attr.get("id"), "atributo": attr.get("nombre"), "origen": "datos",
        })

    for attr in atributos:
        datos = por_atributo.get(attr.get("id")) or por_atributo.get(str(attr.get("id")))
        if not datos:
            continue
        nombre = attr.get("nombre") or "(sin nombre)"
        respuestas = datos.get("respuestas") or 0
        valores = datos.get("valores") or []
        opciones = [str(o).strip() for o in (attr.get("opciones") or [])]

        if auditorias >= MINIMO_PARA_CONCLUIR and respuestas == 0:
            _senal("sin_uso",
                   f"'{nombre}': no tiene ni una respuesta en los últimos {dias} días, con "
                   f"{auditorias} auditorías hechas. O la IA nunca lo contesta o el atributo no se usa.",
                   "media", attr)
            continue

        # No discrimina: casi siempre la misma respuesta. Un criterio que no varía no
        # aporta información (y suele significar que está mal redactado).
        if respuestas >= MINIMO_PARA_CONCLUIR and valores:
            dominante = valores[0]
            if dominante["pct"] >= PCT_NO_DISCRIMINA:
                _senal("no_discrimina",
                       f"'{nombre}': el {dominante['pct']}% de las {respuestas} respuestas fue "
                       f"«{dominante['valor']}». Prácticamente no distingue un llamado de otro.",
                       "media", attr)

        # La lista se queda corta: la salida segura se lleva demasiadas respuestas.
        for valor in valores:
            es_salida = valor["valor"].strip().lower() in ("otros", "otro", "no aplica", "n/a", "na")
            if es_salida and respuestas >= MINIMO_PARA_CONCLUIR and valor["pct"] >= PCT_SALIDA_SEGURA_ALTA:
                _senal("salida_segura_saturada",
                       f"'{nombre}': el {valor['pct']}% cae en «{valor['valor']}». A la lista le "
                       "faltan opciones para los casos reales.",
                       "alta", attr)

        # Opciones declaradas que nadie eligió nunca, y valores guardados que ya no
        # están en la lista (pasa cuando se editan las opciones sin mirar el histórico).
        if opciones and respuestas >= MINIMO_PARA_CONCLUIR:
            usados = {v["valor"].strip().lower() for v in valores}
            muertas = [o for o in opciones if o.strip().lower() not in usados]
            if muertas and len(muertas) < len(opciones):
                _senal("opciones_muertas",
                       f"'{nombre}': en {dias} días nunca se eligió " +
                       ", ".join(f"«{o}»" for o in muertas[:4]) +
                       ". O sobran, o el prompt no explica cuándo corresponden.",
                       "media", attr)
            declaradas = {o.strip().lower() for o in opciones}
            fuera = [v["valor"] for v in valores if v["valor"] and v["valor"].strip().lower() not in declaradas]
            if fuera:
                _senal("valores_fuera_de_lista",
                       f"'{nombre}': hay respuestas guardadas que ya no figuran en la lista (" +
                       ", ".join(f"«{v}»" for v in fuera[:4]) +
                       "). El histórico y la lista actual no coinciden.",
                       "media", attr)

        # Opcional que casi nunca aplica: o el criterio sobra, o el prompt lo hace
        # imposible de responder.
        if attr.get("es_opcional") and auditorias >= MINIMO_PARA_CONCLUIR:
            sin_responder = _pct(max(auditorias - respuestas, 0), auditorias)
            if sin_responder >= PCT_SIN_RESPONDER_ALTO:
                _senal("casi_nunca_aplica",
                       f"'{nombre}': quedó sin responder en el {sin_responder}% de las auditorías. "
                       "Revisá si el criterio aplica de verdad a esta campaña.",
                       "media", attr)

        # Texto libre que se recorta: el atributo pide más de lo que entra.
        recortados = datos.get("recortados") or 0
        if recortados and respuestas:
            pct = _pct(recortados, respuestas)
            if pct >= PCT_RECORTE_ALTO:
                _senal("texto_recortado",
                       f"'{nombre}': el {pct}% de las respuestas se recortó por largo "
                       f"(tope {limites_texto.max_caracteres()} caracteres). Pedile algo más breve.",
                       "alta", attr)

        # Lo más valioso: dónde el auditor corrige a la IA.
        revisiones = datos.get("revisiones") or 0
        kappa = datos.get("kappa")
        accuracy = datos.get("accuracy")
        if revisiones >= MINIMO_REVISIONES:
            malo = (kappa is not None and kappa < KAPPA_BAJO) or (kappa is None and (accuracy or 100) < 70)
            if malo:
                detalle = f"kappa {kappa:.2f}" if kappa is not None else f"acuerdo {accuracy}%"
                correccion = ""
                if datos.get("correcciones"):
                    peor = datos["correcciones"][0]
                    correccion = (f" Lo más repetido: la IA respondió «{peor['ia']}» y el auditor "
                                  f"puso «{peor['humano']}» ({peor['n']} veces).")
                _senal("desacuerdo_humano",
                       f"'{nombre}': los auditores lo corrigieron seguido en {revisiones} revisiones "
                       f"({detalle}).{correccion}",
                       "alta", attr)
        if (datos.get("falsos_ec") or 0) >= 3:
            _senal("falsos_ec",
                   f"'{nombre}': la IA marcó Error Crítico {datos['falsos_ec']} veces donde el "
                   "auditor no lo veía. Un EC pone el llamado en 0, así que el criterio de EC "
                   "está demasiado laxo.",
                   "alta", attr)

    return senales


def bloque_para_prompt(evidencia: Dict[str, Any], atributos: List[Dict[str, Any]]) -> str:
    """Resumen legible de la evidencia para meter en el pedido a la IA.

    Compacto a propósito: una línea por atributo con datos. Lo que se busca es que el
    modelo deje de opinar sobre el texto y empiece a razonar sobre lo que pasó.
    """
    por_atributo = (evidencia or {}).get("por_atributo") or {}
    if not por_atributo:
        return ""

    lineas: List[str] = []
    for attr in atributos:
        datos = por_atributo.get(attr.get("id")) or por_atributo.get(str(attr.get("id")))
        if not datos:
            continue
        partes = [f"- '{attr.get('nombre')}' ({attr.get('tipo')}): {datos.get('respuestas', 0)} respuestas"]
        valores = datos.get("valores") or []
        if valores:
            partes.append("distribución: " + ", ".join(f"{v['valor']} {v['pct']}%" for v in valores))
        if datos.get("recortados"):
            partes.append(f"{datos['recortados']} respuestas recortadas por largo")
        if datos.get("revisiones"):
            acuerdo = f"{datos.get('accuracy')}% de acuerdo"
            if datos.get("kappa") is not None:
                acuerdo += f" (kappa {datos['kappa']:.2f})"
            partes.append(f"revisado por auditores {datos['revisiones']} veces, {acuerdo}")
        for correccion in (datos.get("correcciones") or [])[:2]:
            partes.append(f"corrección frecuente: IA «{correccion['ia']}» → auditor «{correccion['humano']}» x{correccion['n']}")
        if datos.get("motivos"):
            partes.append("motivos que escribieron los auditores: " +
                          "; ".join(f'"{m}"' for m in datos["motivos"]))
        lineas.append(". ".join(partes) + ".")

    if not lineas:
        return ""
    alcance = ("las últimas " if evidencia.get("muestra_topada") else "")
    cabecera = (
        f"Datos de los últimos {evidencia.get('ventana_dias')} días "
        f"({alcance}{evidencia.get('auditorias', 0)} auditorías con esta plantilla, "
        f"desde {evidencia.get('desde')}):"
    )
    return cabecera + "\n" + "\n".join(lineas)
