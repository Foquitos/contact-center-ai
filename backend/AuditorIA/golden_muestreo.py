"""Qué llamados conviene mandar a revisar, para que la medición sirva.

EL PROBLEMA
-----------
Revisar es caro: un analista escucha el llamado entero y contesta atributo por
atributo. Con ese presupuesto, la muestra que se arma decide qué se puede medir
después — y una muestra elegida "al azar" o "los últimos 20" no permite medir casi
nada. En una campaña sana el 90% da OK, así que 20 llamados al azar traen 18 OK y
2 con algo; el kappa de la mayoría de los atributos queda en cero por falta de
casos del lado minoritario, no por culpa del prompt (ver
golden_metricas.confiabilidad_kappa).

POR QUÉ NO ALCANZA CON BALANCEAR EL LLAMADO
-------------------------------------------
La estratificación por resultado del llamado (con EC / con fallas / limpia) ya
mejora mucho, pero mide de a un llamado y el prompt se escribe de a un ATRIBUTO.
Un llamado "con fallas" puede fallar siempre en el mismo atributo: se juntan 30
casos de "Saluda correctamente" en NO OK y ni uno solo de "Verifica identidad",
que sigue sin poder medirse. Lo que hay que balancear es la grilla completa
(atributo × valor), no la columna del puntaje.

CÓMO SE ELIGE
-------------
Cobertura codiciosa (greedy set cover), que es lo que corresponde cuando el costo
está en la cantidad de llamados y no en la cantidad de atributos: cada llamado
"cubre" de una sentada una celda de cada atributo. En cada paso se elige el
llamado que llena más celdas todavía flojas, se descuenta lo que aportó y se
repite. Sale una muestra donde cada atributo tiene casos de sus dos lados con la
menor cantidad de escuchas posible.

Como todavía no sabemos la verdad humana de un llamado sin revisar (es
justamente lo que vamos a averiguar), se estratifica sobre la respuesta de la IA
como proxy. Es la práctica estándar y no sesga la métrica: la IA decide a quién
escuchamos, pero no qué se anota — el veredicto lo pone la persona.

Este módulo es PURO (sin DB, sin IO): quien trae las filas es
AuditorIA/golden_set.py. Se testea con `pytest tests/test_golden_muestreo.py`.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# Cuántos casos revisados queremos de CADA valor de CADA atributo. Es el mismo
# umbral con el que golden_metricas declara que un kappa no es concluyente: por
# debajo de esto la celda existe pero no sostiene ninguna conclusión.
OBJETIVO_POR_VALOR = 5

# Atributos con muchísimas opciones (tipologías largas, listas) no se persiguen
# celda por celda: exigirles 5 casos de cada valor se comería la muestra entera
# para medir algo que casi no mueve el puntaje. Se los cubre por volumen.
MAX_VALORES_POR_ATRIBUTO = 8

Celda = Tuple[int, str]


# --------------------------------------------------------------------------- #
# Déficit: qué le falta a la grilla                                            #
# --------------------------------------------------------------------------- #
def deficit_por_celda(
    cobertura: Dict[int, Dict[str, int]],
    *,
    objetivo: int = OBJETIVO_POR_VALOR,
    valores_esperados: Optional[Dict[int, Sequence[str]]] = None,
) -> Dict[Celda, int]:
    """Cuántas revisiones le faltan a cada (atributo, valor) para llegar al objetivo.

    `cobertura` es {atributo_id: {valor: revisiones_que_ya_hay}}.
    `valores_esperados` agrega las celdas que TODAVÍA NO APARECIERON nunca (las
    opciones declaradas en la plantilla): son las más importantes de todas —una
    celda en cero es un criterio del que no sabemos absolutamente nada— y sin
    esto serían invisibles, porque no hay revisión que las nombre.
    """
    faltan: Dict[Celda, int] = {}

    for atributo_id, valores in (cobertura or {}).items():
        if len(valores) > MAX_VALORES_POR_ATRIBUTO:
            continue
        for valor, cantidad in valores.items():
            resto = objetivo - int(cantidad or 0)
            if resto > 0:
                faltan[(int(atributo_id), valor)] = resto

    for atributo_id, esperados in (valores_esperados or {}).items():
        esperados = list(esperados or [])
        if not esperados or len(esperados) > MAX_VALORES_POR_ATRIBUTO:
            continue
        vistos = (cobertura or {}).get(int(atributo_id), {})
        for valor in esperados:
            celda = (int(atributo_id), valor)
            if celda in faltan:
                continue
            resto = objetivo - int(vistos.get(valor, 0) or 0)
            if resto > 0:
                faltan[celda] = resto

    return faltan


def celdas_flojas(
    faltan: Dict[Celda, int],
    nombres: Optional[Dict[int, str]] = None,
    *,
    objetivo: int = OBJETIVO_POR_VALOR,
) -> List[Dict[str, Any]]:
    """El déficit ordenado de peor a mejor, para mostrarlo en pantalla."""
    nombres = nombres or {}
    filas = [
        {
            "atributo_id": aid,
            "nombre": nombres.get(aid, str(aid)),
            "valor": valor,
            "faltan": resto,
            "revisados": max(0, objetivo - resto),
        }
        for (aid, valor), resto in faltan.items()
    ]
    filas.sort(key=lambda f: (-f["faltan"], f["nombre"], f["valor"]))
    return filas


# --------------------------------------------------------------------------- #
# Elección codiciosa                                                           #
# --------------------------------------------------------------------------- #
def elegir_balanceado(
    candidatas: Iterable[Dict[str, Any]],
    faltan: Dict[Celda, int],
    cantidad: int,
    *,
    nombres: Optional[Dict[int, str]] = None,
    max_motivos: int = 3,
) -> List[Dict[str, Any]]:
    """Elige `cantidad` llamados que llenen lo más posible de la grilla.

    Cada candidata es un dict con al menos `valores` = {atributo_id: valor_ia}.
    Se devuelve la candidata enriquecida con:
      - `aporte`: cuántas celdas flojas llena (0 = no aporta nada nuevo)
      - `motivos`: las celdas concretas, para que en pantalla se lea POR QUÉ está
        recomendada ("aporta el primer «NO OK» de Verifica identidad") y no salga
        como una lista opaca de IDs.

    Las que no aportan nada NO se descartan: se ordenan detrás y completan el
    cupo. Cuando la grilla ya está cubierta, más volumen sigue siendo útil, y
    devolver menos llamados de los pedidos sería confuso.
    """
    nombres = nombres or {}
    restante = dict(faltan)
    disponibles = list(candidatas)
    elegidas: List[Dict[str, Any]] = []

    while disponibles and len(elegidas) < max(0, cantidad):
        mejor_idx = -1
        mejor_ganancia = 0
        mejor_celdas: List[Celda] = []

        for idx, cand in enumerate(disponibles):
            cubre = [
                celda for celda in _celdas_de(cand)
                if restante.get(celda, 0) > 0
            ]
            if not cubre:
                continue
            # Se pondera por lo que falta: una celda que necesita 5 casos urge más
            # que una a la que le falta 1. Así se atacan primero los criterios de
            # los que no sabemos nada.
            ganancia = sum(restante[celda] for celda in cubre)
            if ganancia > mejor_ganancia:
                mejor_idx, mejor_ganancia, mejor_celdas = idx, ganancia, cubre

        if mejor_idx < 0:
            break  # ninguna aporta: el resto entra por volumen, más abajo

        cand = disponibles.pop(mejor_idx)
        mejor_celdas.sort(key=lambda c: -restante[c])
        cand = dict(cand)
        cand["aporte"] = len(mejor_celdas)
        cand["motivos"] = [
            {
                "atributo_id": aid,
                "nombre": nombres.get(aid, str(aid)),
                "valor": valor,
                "faltaban": restante[(aid, valor)],
            }
            for aid, valor in mejor_celdas[:max_motivos]
        ]
        for celda in mejor_celdas:
            restante[celda] -= 1
        elegidas.append(cand)

    # Relleno: se respeta el orden en que llegaron (el SQL ya las sorteó
    # estratificadas, así que el relleno tampoco queda sesgado).
    for cand in disponibles:
        if len(elegidas) >= max(0, cantidad):
            break
        cand = dict(cand)
        cand["aporte"] = 0
        cand["motivos"] = []
        elegidas.append(cand)

    return elegidas


def _celdas_de(candidata: Dict[str, Any]) -> List[Celda]:
    valores = candidata.get("valores") or {}
    return [(int(aid), valor) for aid, valor in valores.items() if valor is not None]


def resumen_aporte(elegidas: Sequence[Dict[str, Any]], faltan: Dict[Celda, int]) -> Dict[str, Any]:
    """Qué tan bien queda la grilla si se revisa lo recomendado. Es el número que
    justifica el pedido a Calidad: «con estos 20 llamados pasás de 12 criterios sin
    medir a 3»."""
    restante = dict(faltan)
    for cand in elegidas:
        for celda in _celdas_de(cand):
            if restante.get(celda, 0) > 0:
                restante[celda] -= 1
    return {
        "celdas_flojas_antes": len(faltan),
        "celdas_flojas_despues": sum(1 for v in restante.values() if v > 0),
        "revisiones_faltantes_antes": sum(faltan.values()),
        "revisiones_faltantes_despues": sum(v for v in restante.values() if v > 0),
    }
