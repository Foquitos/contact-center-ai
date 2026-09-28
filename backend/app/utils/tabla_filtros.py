"""
Filtrado por columna, facetas y caché de resultados para tablas grandes.

Nace para "Auditorías Realizadas": el SP `calidad.sp_ObtenerAuditoriasFiltradas`
devuelve TODAS las filas del rango (pueden ser miles, con ~20 columnas dinámicas
según la plantilla) y hasta ahora ese set entero viajaba al navegador y se
pintaba de una sola vez en el DOM. Acá vive la maquinaria para recortar del lado
del servidor:

  * `parsear_filtros` / `aplicar_filtros` — filtros por cualquier columna del
    resultado (texto, número, fecha, listas de valores, vacíos).
  * `inferir_tipos` / `calcular_facetas` — metadata para que el front sepa qué
    control mostrar por columna (multiselect de valores, rango numérico, rango
    de fechas o "contiene").
  * `CacheConsultas` — guarda el resultado CRUDO del SP unos minutos, así
    cambiar un filtro o pasar de página NO vuelve a pegarle a SQL Server.

Todo se resuelve en memoria sobre las filas ya traídas: el SP no se toca (lo
comparten `tasks.py`, `Auditor.py` y la Bandeja), y aun así el navegador recibe
una página filtrada en lugar del set completo.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import unicodedata
from collections import OrderedDict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Operadores soportados                                                        #
# --------------------------------------------------------------------------- #
OPS_TEXTO = {"contiene", "no_contiene", "empieza", "termina"}
OPS_IGUALDAD = {"igual", "distinto"}
OPS_LISTA = {"en", "no_en"}
OPS_ORDEN = {"mayor", "mayor_igual", "menor", "menor_igual", "entre"}
OPS_VACIO = {"vacio", "no_vacio"}
OPS_VALIDOS = OPS_TEXTO | OPS_IGUALDAD | OPS_LISTA | OPS_ORDEN | OPS_VACIO

# Topes defensivos: los filtros llegan por querystring desde el navegador.
MAX_FILTROS = 40
MAX_VALORES_LISTA = 1000


class FiltroInvalido(ValueError):
    """El JSON de filtros no se entiende o pide algo imposible."""


# --------------------------------------------------------------------------- #
# Normalización de valores                                                     #
# --------------------------------------------------------------------------- #
def clave_valor(valor: Any) -> str:
    """Representación estable de una celda, usada para facetas y comparaciones
    exactas. Es la MISMA función que arma las opciones del multiselect y la que
    compara contra lo que el usuario eligió, así que ida y vuelta siempre matchean."""
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "1" if valor else "0"
    if isinstance(valor, datetime):
        return valor.isoformat(sep=" ", timespec="seconds")
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        numero = float(valor)
        return str(int(numero)) if numero.is_integer() else str(numero)
    if isinstance(valor, float):
        return str(int(valor)) if valor.is_integer() else str(valor)
    return str(valor).strip()


def _sin_acentos(texto: str) -> str:
    descompuesto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


def texto_normalizado(valor: Any) -> str:
    """Minúsculas y sin acentos: buscar "reclamo" tiene que encontrar "Reclamó"."""
    return _sin_acentos(clave_valor(valor).lower())


def _a_numero(valor: Any) -> Optional[float]:
    if valor is None or isinstance(valor, (datetime, date)):
        return None
    if isinstance(valor, bool):
        return 1.0 if valor else 0.0
    if isinstance(valor, (int, float, Decimal)):
        return float(valor)
    if isinstance(valor, str):
        limpio = valor.strip().replace(",", ".")
        if not limpio:
            return None
        try:
            return float(limpio)
        except ValueError:
            return None
    return None


_FORMATOS_FECHA = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y")


def _a_fecha(valor: Any) -> Optional[datetime]:
    if isinstance(valor, datetime):
        return valor
    if isinstance(valor, date):
        return datetime(valor.year, valor.month, valor.day)
    if isinstance(valor, str):
        limpio = valor.strip().replace("T", " ")
        if not limpio:
            return None
        limpio = limpio.split(".")[0][:19]
        for formato in _FORMATOS_FECHA:
            try:
                return datetime.strptime(limpio, formato)
            except ValueError:
                continue
    return None


def _es_fecha_sola(valor: Any) -> bool:
    """True si el usuario escribió solo una fecha (sin hora): el límite superior
    de un rango tiene que incluir el día completo, no cortar a las 00:00."""
    return isinstance(valor, str) and len(valor.strip()) <= 10


def _vacia(valor: Any) -> bool:
    return valor is None or (isinstance(valor, str) and not valor.strip())


# --------------------------------------------------------------------------- #
# Parseo y compilación de filtros                                              #
# --------------------------------------------------------------------------- #
def parsear_filtros(crudo: Optional[str],
                    columnas_validas: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    """Convierte el parámetro `filtros` (JSON) en una lista normalizada.

    Formato esperado: `[{"col": "Agente", "op": "en", "val": ["Ana", "Juan"]}, ...]`
    Se ignoran en silencio los filtros sin valor (un input de texto vacío en la UI
    no es un error, simplemente no filtra).

    `columnas_validas` es opcional: sirve para parsear ANTES de tener el result set
    (hay que saber si hay filtros para decidir si se puede paginar en SQL). La
    validación de que la columna exista se hace después con `validar_columnas`."""
    if not crudo or not crudo.strip():
        return []

    try:
        datos = json.loads(crudo)
    except (json.JSONDecodeError, TypeError) as e:
        raise FiltroInvalido(f"El parámetro 'filtros' no es JSON válido: {e}")

    if isinstance(datos, dict):  # tolerancia: {"col": {"op":..., "val":...}}
        datos = [{"col": c, **(v if isinstance(v, dict) else {"op": "igual", "val": v})}
                 for c, v in datos.items()]
    if not isinstance(datos, list):
        raise FiltroInvalido("El parámetro 'filtros' debe ser una lista de filtros.")
    if len(datos) > MAX_FILTROS:
        raise FiltroInvalido(f"Demasiados filtros (máximo {MAX_FILTROS}).")

    validas = set(columnas_validas) if columnas_validas is not None else None
    salida: List[Dict[str, Any]] = []

    for item in datos:
        if not isinstance(item, dict):
            raise FiltroInvalido("Cada filtro debe ser un objeto {col, op, val}.")
        col = str(item.get("col") or "").strip()
        op = str(item.get("op") or "contiene").strip().lower()
        val = item.get("val")

        if not col:
            raise FiltroInvalido("Un filtro llegó sin columna.")
        if validas is not None and col not in validas:
            raise FiltroInvalido(f"La columna '{col}' no existe en el resultado.")
        if op not in OPS_VALIDOS:
            raise FiltroInvalido(f"Operador '{op}' no soportado.")

        if op in OPS_VACIO:
            salida.append({"col": col, "op": op, "val": None})
            continue

        if op in OPS_LISTA:
            valores = val if isinstance(val, list) else ([] if val is None else [val])
            # El string vacío SÍ es un valor elegible (la opción "(vacío)" del
            # multiselect); lo que se descarta es un None suelto.
            valores = [v for v in valores if v is not None]
            if len(valores) > MAX_VALORES_LISTA:
                raise FiltroInvalido(f"Demasiados valores en el filtro de '{col}'.")
            if not valores:
                continue  # sin valores elegidos no filtra nada
            salida.append({"col": col, "op": op, "val": valores})
            continue

        if op == "entre":
            valores = val if isinstance(val, (list, tuple)) else [val, None]
            desde = valores[0] if len(valores) > 0 else None
            hasta = valores[1] if len(valores) > 1 else None
            if _vacia(desde) and _vacia(hasta):
                continue
            # Un rango a medio llenar se degrada al operador simple que corresponda.
            if _vacia(hasta):
                salida.append({"col": col, "op": "mayor_igual", "val": desde})
            elif _vacia(desde):
                salida.append({"col": col, "op": "menor_igual", "val": hasta})
            else:
                salida.append({"col": col, "op": "entre", "val": [desde, hasta]})
            continue

        if _vacia(val):
            continue
        salida.append({"col": col, "op": op, "val": val})

    return salida


def validar_columnas(filtros: Sequence[Dict[str, Any]], columnas_validas: Iterable[str]) -> None:
    """Verifica que cada filtro apunte a una columna que exista en el result set.
    Se llama cuando ya se tienen las filas (el parseo puede ocurrir antes)."""
    validas = set(columnas_validas)
    for filtro in filtros:
        if filtro["col"] not in validas:
            raise FiltroInvalido(f"La columna '{filtro['col']}' no existe en el resultado.")


def _comparador_orden(op: str, referencia: Any) -> Callable[[Any], bool]:
    """Arma el predicado de mayor/menor. Decide UNA vez si compara números,
    fechas o texto (según lo que escribió el usuario), no por cada fila."""
    numero_ref = _a_numero(referencia)
    fecha_ref = None if numero_ref is not None else _a_fecha(referencia)

    if fecha_ref is not None and _es_fecha_sola(referencia) and op in ("menor_igual", "menor"):
        # "hasta el 2026-08-05" incluye todo el 5, no corta a las 00:00.
        fecha_ref = fecha_ref + timedelta(days=1) - timedelta(seconds=1)

    def comparar(valor: Any) -> bool:
        if numero_ref is not None:
            actual: Any = _a_numero(valor)
        elif fecha_ref is not None:
            actual = _a_fecha(valor)
        else:
            actual = texto_normalizado(valor)
        if actual is None or (isinstance(actual, str) and not actual):
            return False
        referencia_efectiva = (numero_ref if numero_ref is not None
                               else fecha_ref if fecha_ref is not None
                               else texto_normalizado(referencia))
        if op == "mayor":
            return actual > referencia_efectiva
        if op == "mayor_igual":
            return actual >= referencia_efectiva
        if op == "menor":
            return actual < referencia_efectiva
        return actual <= referencia_efectiva

    return comparar


def _compilar(filtro: Dict[str, Any]) -> Callable[[Any], bool]:
    """Devuelve el predicado de una celda para este filtro (ya con los valores
    de referencia normalizados, para no repetir el trabajo fila por fila)."""
    op = filtro["op"]
    val = filtro["val"]

    if op == "vacio":
        return _vacia
    if op == "no_vacio":
        return lambda v: not _vacia(v)

    if op in OPS_TEXTO:
        aguja = texto_normalizado(val)
        if op == "contiene":
            return lambda v: aguja in texto_normalizado(v)
        if op == "no_contiene":
            return lambda v: aguja not in texto_normalizado(v)
        if op == "empieza":
            return lambda v: texto_normalizado(v).startswith(aguja)
        return lambda v: texto_normalizado(v).endswith(aguja)

    if op in OPS_IGUALDAD:
        numero_ref = _a_numero(val)
        aguja = texto_normalizado(val)

        def iguales(v: Any) -> bool:
            if numero_ref is not None:
                actual = _a_numero(v)
                if actual is not None:
                    return actual == numero_ref
            return texto_normalizado(v) == aguja

        return iguales if op == "igual" else (lambda v: not iguales(v))

    if op in OPS_LISTA:
        agujas = {texto_normalizado(v) for v in val}
        if op == "en":
            return lambda v: texto_normalizado(v) in agujas
        return lambda v: texto_normalizado(v) not in agujas

    if op == "entre":
        desde, hasta = val
        pred_desde = _comparador_orden("mayor_igual", desde)
        pred_hasta = _comparador_orden("menor_igual", hasta)
        return lambda v: pred_desde(v) and pred_hasta(v)

    return _comparador_orden(op, val)


def aplicar_filtros(filas: Sequence[Dict[str, Any]],
                    filtros: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Aplica todos los filtros en AND. Sin filtros devuelve la lista tal cual
    (misma referencia: no copia miles de dicts al pedo)."""
    if not filtros:
        return list(filas)

    predicados = [(f["col"], _compilar(f)) for f in filtros]
    return [
        fila for fila in filas
        if all(pred(fila.get(col)) for col, pred in predicados)
    ]


# --------------------------------------------------------------------------- #
# Metadata para la UI: tipos y facetas                                         #
# --------------------------------------------------------------------------- #
def inferir_tipos(filas: Sequence[Dict[str, Any]],
                  columnas: Sequence[str],
                  muestra: int = 300) -> Dict[str, str]:
    """Clasifica cada columna en 'numero' | 'fecha' | 'texto' mirando una muestra
    de valores NO nulos.

    Solo se considera numérica una columna cuyo tipo Python ya es numérico: un
    IdAplicativo es un string de dígitos y un rango min/max sobre él no le sirve
    a nadie (ahí queremos "contiene")."""
    tipos: Dict[str, str] = {}
    for col in columnas:
        vistos = 0
        numericos = 0
        fechas = 0
        for fila in filas:
            valor = fila.get(col)
            if _vacia(valor):
                continue
            vistos += 1
            if isinstance(valor, bool) or isinstance(valor, (int, float, Decimal)):
                numericos += 1
            elif isinstance(valor, (datetime, date)) or (
                isinstance(valor, str) and _a_fecha(valor) is not None and len(valor.strip()) >= 8
            ):
                fechas += 1
            if vistos >= muestra:
                break
        if vistos and numericos == vistos:
            tipos[col] = "numero"
        elif vistos and fechas == vistos:
            tipos[col] = "fecha"
        else:
            tipos[col] = "texto"
    return tipos


def calcular_facetas(filas: Sequence[Dict[str, Any]],
                     columnas: Sequence[str],
                     max_valores: int = 60,
                     max_largo: int = 80) -> Dict[str, List[Dict[str, Any]]]:
    """Valores distintos (con su conteo) por columna, para armar multiselects
    tipo Excel.

    Solo devuelve las columnas de baja cardinalidad: apenas una supera
    `max_valores` distintos o trae un texto largo (comentarios, transcripciones)
    se descarta y el front cae al filtro de texto libre. Una sola pasada sobre
    las filas, salteando las columnas ya descartadas."""
    contadores: Dict[str, Dict[str, int]] = {col: {} for col in columnas}
    descartadas: set = set()
    activas = list(columnas)

    for fila in filas:
        if not activas:
            break
        for col in activas:
            clave = clave_valor(fila.get(col))
            if len(clave) > max_largo:
                descartadas.add(col)
                continue
            conteo = contadores[col]
            conteo[clave] = conteo.get(clave, 0) + 1
            if len(conteo) > max_valores:
                descartadas.add(col)
        if descartadas:
            activas = [c for c in activas if c not in descartadas]

    salida: Dict[str, List[Dict[str, Any]]] = {}
    for col in columnas:
        if col in descartadas:
            continue
        conteo = contadores[col]
        if not conteo:
            continue
        valores = sorted(conteo.items(), key=lambda kv: _orden_faceta(kv[0]))
        salida[col] = [{"valor": v, "n": n} for v, n in valores]
    return salida


def _orden_faceta(clave: str) -> Tuple[int, float, str]:
    """Vacíos primero, después numéricos por valor y el resto alfabético."""
    if clave == "":
        return (0, 0.0, "")
    numero = _a_numero(clave)
    if numero is not None:
        return (1, numero, "")
    return (2, 0.0, _sin_acentos(clave.lower()))


# --------------------------------------------------------------------------- #
# Caché de resultados crudos                                                   #
# --------------------------------------------------------------------------- #
class CacheConsultas:
    """Caché LRU con TTL para el resultado crudo de una consulta pesada.

    El SP es lo caro de la pantalla: mientras el usuario ajusta filtros de columna
    o pasa de página, la consulta subyacente es idéntica, así que se reutiliza lo
    ya traído y SQL Server no se entera. La clave incluye al usuario y a todos los
    parámetros de la consulta, y hay tope de entradas y de filas totales para que
    esto no se coma la RAM del backend (el proceso de uvicorn es uno solo).
    """

    def __init__(self, ttl_segundos: int = 300, max_entradas: int = 6,
                 max_filas_total: int = 60_000):
        self.ttl = ttl_segundos
        self.max_entradas = max_entradas
        self.max_filas_total = max_filas_total
        self._datos: "OrderedDict[str, Tuple[float, List[str], List[Dict[str, Any]]]]" = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def clave(**partes: Any) -> str:
        crudo = json.dumps(partes, sort_keys=True, default=str)
        return hashlib.sha1(crudo.encode("utf-8")).hexdigest()

    def obtener(self, clave: str) -> Optional[Tuple[List[str], List[Dict[str, Any]]]]:
        ahora = time.time()
        with self._lock:
            entrada = self._datos.get(clave)
            if not entrada:
                return None
            guardado_en, columnas, filas = entrada
            if ahora - guardado_en > self.ttl:
                self._datos.pop(clave, None)
                return None
            self._datos.move_to_end(clave)  # LRU
            return columnas, filas

    def guardar(self, clave: str, columnas: List[str], filas: List[Dict[str, Any]]) -> None:
        if len(filas) > self.max_filas_total:
            return  # un set gigante no entra: mejor re-consultar que inflar la RAM
        ahora = time.time()
        with self._lock:
            self._datos[clave] = (ahora, columnas, filas)
            self._datos.move_to_end(clave)
            self._podar(ahora)

    def invalidar(self, clave: str) -> None:
        with self._lock:
            self._datos.pop(clave, None)

    def _podar(self, ahora: float) -> None:
        """Saca vencidas, después las más viejas hasta respetar los topes.
        Se llama siempre con el lock tomado."""
        vencidas = [k for k, (t, _, _) in self._datos.items() if ahora - t > self.ttl]
        for k in vencidas:
            self._datos.pop(k, None)

        while len(self._datos) > self.max_entradas:
            self._datos.popitem(last=False)

        total = sum(len(f) for _, _, f in self._datos.values())
        while total > self.max_filas_total and len(self._datos) > 1:
            _, (_, _, filas) = self._datos.popitem(last=False)
            total -= len(filas)


def paginar(filas: Sequence[Dict[str, Any]], page: int, page_size: int) -> List[Dict[str, Any]]:
    """Corta la página pedida. `page_size <= 0` significa "todas" (descargas)."""
    if page_size <= 0:
        return list(filas)
    inicio = max(0, (max(1, page) - 1) * page_size)
    return list(filas[inicio:inicio + page_size])
