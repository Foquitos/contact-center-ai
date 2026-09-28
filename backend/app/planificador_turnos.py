"""Planificador — de la brecha de operadores a turnos pedibles a RRHH.

RRHH no convoca «intervalos-operador de media hora»: pide turnos completos o
extensiones de un turno que ya está. Traducir la brecha de cada bloque a una
propuesta concreta evita hacer la cuenta a ojo antes de mandar el pedido.

CÓMO SE ARMA
------------
Por CAPAS, como se lee un gráfico de faltantes: la capa k son las medias horas en
que faltan al menos k personas, y cada tramo contiguo de una capa es UNA persona
de punta a punta. Un pico se cubre así con piezas anidadas (una larga abajo, más
cortas arriba) en vez de con recortes sueltos. La primera versión asignaba cada
turno desde el primer faltante hacia adelante y, medido sobre el plan real del
2026-09-15, un bloque de 08:00 a 13:00 salía en nueve grupos con piezas de media
hora («2 extensiones 08:30–09:00»), que nadie puede pedir.

Cada tramo se cubre con:
  - una extensión si dura menos que el turno más corto y no más de
    EXTENSION_MAX_H;
  - si no, el turno más corto que lo cubra; un tramo más largo que el turno más
    largo se parte en turnos máximos y el resto se vuelve a clasificar.
Ninguna pieza dura menos de MIN_PIEZA_H: la media hora que sobra es la
sobrecobertura que se informa, no un pedido de 30 minutos.
"""

from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Sequence, Tuple, Union

# Supuestos a confirmar con RRHH.
LARGOS_TURNO_H: Tuple[int, ...] = (4, 6, 8)
# Más de 3 horas sobre un turno ya no es extensión: es otro turno (convenio y fatiga).
EXTENSION_MAX_H: int = 3
# Nadie se cita ni se queda por media hora.
MIN_PIEZA_H: float = 1.0

Pieza = Tuple[str, datetime, datetime, float]   # (tipo, desde, hasta, horas)


def _normalizar(faltantes) -> Tuple[List[datetime], List[int]]:
    tiempos: List[datetime] = []
    valores: List[int] = []
    for f in faltantes:
        if isinstance(f, (tuple, list)):
            t, v = f[0], f[1]
        elif isinstance(f, dict):
            t = f.get("inicio") or f.get("momento")
            v = f.get("faltante") if f.get("faltante") is not None else f.get("faltan", 0)
        else:
            t = getattr(f, "inicio", getattr(f, "momento", None))
            v = getattr(f, "faltante", getattr(f, "faltan", 0))
        if t is not None:
            tiempos.append(t)
            valores.append(max(0, int(v or 0)))
    orden = sorted(range(len(tiempos)), key=lambda i: tiempos[i])
    return [tiempos[i] for i in orden], [valores[i] for i in orden]


def _cubrir_tramo(inicio: datetime, duracion_h: float, largos_h: Sequence[int],
                  extension_max_h: float, min_pieza_h: float) -> List[Pieza]:
    """Una persona para un tramo contiguo: una o más piezas pedibles."""
    largo_min, largo_max = min(largos_h), max(largos_h)
    piezas: List[Pieza] = []
    while duracion_h > 1e-9:
        if duracion_h > largo_max:
            fin = inicio + timedelta(hours=largo_max)
            piezas.append(("turno", inicio, fin, float(largo_max)))
            inicio, duracion_h = fin, duracion_h - largo_max
            continue
        horas = max(duracion_h, min_pieza_h)
        if horas < largo_min and horas <= extension_max_h:
            tipo = "extension"
        else:
            tipo, horas = "turno", float(min(L for L in largos_h if L >= horas)
                                         if any(L >= horas for L in largos_h) else largo_max)
        piezas.append((tipo, inicio, inicio + timedelta(hours=horas), horas))
        break
    return piezas


def sugerir_turnos(
    faltantes: Sequence[Union[Tuple[datetime, int], List[Any], dict]],
    intervalo_min: int = 30,
    largos_h: Sequence[int] = LARGOS_TURNO_H,
    extension_max_h: float = EXTENSION_MAX_H,
    min_pieza_h: float = MIN_PIEZA_H,
) -> Dict[str, Any]:
    """Propuesta determinística de turnos y extensiones para UN bloque.

    `faltantes`: (inicio, faltan) por intervalo, o dicts con momento/faltante.
    Devuelve propuesta [{tipo, desde, hasta, horas, cantidad}], horas propuestas,
    horas faltantes, sobrecobertura y el texto para pantalla y Excel.
    """
    tiempos, valores = _normalizar(faltantes)
    horas_faltantes = round(sum(valores) * intervalo_min / 60.0, 2)
    if horas_faltantes == 0:
        return {"propuesta": [], "horas_propuestas": 0.0, "horas_faltantes": 0.0,
                "sobrecobertura_h": 0.0, "texto": "Sin refuerzos necesarios"}

    paso = timedelta(minutes=intervalo_min)
    piezas: List[Pieza] = []
    for capa in range(1, max(valores) + 1):
        i = 0
        while i < len(valores):
            if valores[i] < capa:
                i += 1
                continue
            j = i
            while (j + 1 < len(valores) and valores[j + 1] >= capa
                   and tiempos[j + 1] == tiempos[j] + paso):
                j += 1
            piezas.extend(_cubrir_tramo(tiempos[i], (j - i + 1) * intervalo_min / 60.0,
                                        largos_h, extension_max_h, min_pieza_h))
            i = j + 1

    conteo: Dict[Pieza, int] = OrderedDict()
    for p in sorted(piezas, key=lambda p: (p[1], p[0] != "turno", p[2])):
        conteo[p] = conteo.get(p, 0) + 1

    propuesta = [{"tipo": tipo, "desde": desde, "hasta": hasta, "horas": round(horas, 1),
                  "cantidad": cantidad}
                 for (tipo, desde, hasta, horas), cantidad in conteo.items()]
    horas_propuestas = round(sum(p["horas"] * p["cantidad"] for p in propuesta), 2)
    texto = formatear_propuesta(propuesta)
    return {
        "propuesta": propuesta,
        "horas_propuestas": horas_propuestas,
        "horas_faltantes": horas_faltantes,
        "sobrecobertura_h": round(max(0.0, horas_propuestas - horas_faltantes), 2),
        "texto": texto,
        "resumen": resumir_propuesta(propuesta, horas_propuestas) if len(propuesta) > 3 else texto,
    }


def resumir_propuesta(propuesta: Sequence[Dict[str, Any]], horas_propuestas: float) -> str:
    """Para el renglón de la pantalla cuando el detalle no entra: un pico de la
    mañana puede dar ocho grupos distintos. El detalle sigue en «texto»."""
    turnos = sum(p["cantidad"] for p in propuesta if p["tipo"] == "turno")
    extensiones = sum(p["cantidad"] for p in propuesta if p["tipo"] != "turno")
    partes = []
    if turnos:
        partes.append(f"{turnos} turno{'s' if turnos > 1 else ''}")
    if extensiones:
        partes.append(f"{extensiones} extensi{'ones' if extensiones > 1 else 'ón'}")
    return f"{' y '.join(partes)} · {horas_propuestas:g} h"


def formatear_propuesta(propuesta: Sequence[Dict[str, Any]]) -> str:
    """'3 turnos 09:00–15:00 · 2 extensiones 18:00–20:00'."""
    if not propuesta:
        return "Sin refuerzos necesarios"
    partes: List[str] = []
    for p in propuesta:
        plural = p["cantidad"] > 1
        nombre = ("turnos" if plural else "turno") if p["tipo"] == "turno" else \
                 ("extensiones" if plural else "extensión")
        desde, hasta = p["desde"], p["hasta"]
        d = desde.strftime("%H:%M") if hasattr(desde, "strftime") else str(desde)
        h = hasta.strftime("%H:%M") if hasattr(hasta, "strftime") else str(hasta)
        partes.append(f"{p['cantidad']} {nombre} {d}–{h}")
    return " · ".join(partes)
