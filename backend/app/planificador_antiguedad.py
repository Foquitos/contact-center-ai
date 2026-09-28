"""Planificador — rinde de la gente nueva (descuento por antigüedad).

La malla cuenta a todos igual, y una persona en su primera semana de piso no rinde
como una con meses. Medido en Voltara (2026-09-15) NO es que se conecte menos —en
sus primeras semanas está logueada más tiempo de su turno que el promedio—: es que
tarda más por llamada. Y se compara DENTRO DE CADA COLA, porque la gente nueva no
atiende la misma mezcla que la antigua y un TMO crudo mezclaría el aprendizaje con
la cola: el rinde de un tramo es cuánto les habría llevado a los antiguos atender
esas mismas llamadas, en esas mismas colas, sobre lo que efectivamente les llevó.

Cómo entra al plan
------------------
Los citados se cuentan en EQUIVALENTES: cada persona pesa según cuántos días hace
que pasó a piso, y la brecha se calcula sobre eso. El peso no es el rinde crudo:
se normaliza contra la mezcla de antigüedades de las llamadas con las que se midió
el TMO del pronóstico. Ese TMO ya trae adentro a la gente nueva de la historia, así
que descontar además a cada nuevo entero lo contaría dos veces. Normalizado, una
malla con la misma proporción de gente nueva que la historia vale lo mismo que
antes, una con más gente nueva vale menos y una más antigua vale un poco más.

Los días de capacitación no entran: esos turnos vienen con código de capacitación
y la malla ya no los cuenta.

La medición lee meses del detalle de llamadas y del informe por agente, así que no
corre en cada recálculo: la hace scripts/planificador_antiguedad.py y se guarda en
planificacion.CurvaAntiguedad. Sin medición el factor es 1 y todo queda como antes.
"""

from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# Semanas de llamadas con las que se mide el rinde. Tiene que haber varias camadas
# adentro: con una sola, el tramo es esa camada y no la antigüedad.
SEMANAS_RINDE = 12
# Días desde el pase a piso. Desde DIAS_ANTIGUO la persona es la referencia.
TRAMOS: Tuple[Tuple[int, int], ...] = ((0, 14), (14, 28), (28, 56))
DIAS_ANTIGUO = 56
# Con esta cantidad de llamadas el rinde medido se usa a la mitad.
LLAMADAS_ENCOGIMIENTO = 1000
RINDE_MINIMO, RINDE_MAXIMO = 0.3, 1.2
FACTOR_MINIMO, FACTOR_MAXIMO = 0.3, 1.5


def tramos_con_antiguos() -> List[int]:
    return [desde for desde, _ in TRAMOS] + [DIAS_ANTIGUO]


def tramo_de(dias: Optional[int]) -> int:
    """El tramo (día desde) de alguien con `dias` de piso. Sin fecha de piso cuenta
    como antiguo: no descontar es lo que se hacía siempre. Antes del pase a piso
    (atiende en práctica) cae en el primer tramo."""
    if dias is None or dias >= DIAS_ANTIGUO:
        return DIAS_ANTIGUO
    for desde, hasta in TRAMOS:
        if dias < hasta:
            return desde
    return DIAS_ANTIGUO


def medir_rinde(filas: Iterable[Mapping],
                llamadas_encogimiento: float = LLAMADAS_ENCOGIMIENTO) -> Dict[int, dict]:
    """Rinde de cada tramo contra los antiguos, cola por cola.

    `filas`: {"dias": días de piso (None = sin fecha), "skill_id", "llamadas",
    "segundos"}. Una cola sin antiguos no se puede comparar y no cuenta. Un tramo
    con pocas llamadas se encoge hacia 1.
    """
    antiguos: Dict[object, List[float]] = {}
    nuevos: Dict[int, Dict[object, List[float]]] = {}
    for f in filas:
        llamadas = float(f.get("llamadas") or 0)
        segundos = float(f.get("segundos") or 0)
        if llamadas <= 0 or segundos <= 0:
            continue
        tramo = tramo_de(f.get("dias"))
        destino = antiguos if tramo == DIAS_ANTIGUO else nuevos.setdefault(tramo, {})
        acum = destino.setdefault(f.get("skill_id"), [0.0, 0.0])
        acum[0] += llamadas
        acum[1] += segundos

    tmo_antiguo = {sid: seg / ll for sid, (ll, seg) in antiguos.items() if ll > 0}
    salida: Dict[int, dict] = {DIAS_ANTIGUO: {
        "rinde": 1.0, "crudo": 1.0,
        "llamadas": int(round(sum(ll for ll, _ in antiguos.values())))}}
    for desde, _ in TRAMOS:
        esperado = real = llamadas = 0.0
        for sid, (ll, seg) in nuevos.get(desde, {}).items():
            if sid not in tmo_antiguo:
                continue
            esperado += ll * tmo_antiguo[sid]
            real += seg
            llamadas += ll
        if real <= 0:
            continue
        crudo = esperado / real
        peso = llamadas / (llamadas + llamadas_encogimiento)
        rinde = min(max(1 + (crudo - 1) * peso, RINDE_MINIMO), RINDE_MAXIMO)
        salida[desde] = {"rinde": round(rinde, 4), "crudo": round(crudo, 4),
                         "llamadas": int(round(llamadas))}
    return salida


def llamadas_por_tramo(filas: Iterable[Mapping]) -> Dict[int, float]:
    """{"dias", "llamadas"} -> llamadas por tramo."""
    salida: Dict[int, float] = {}
    for f in filas:
        llamadas = float(f.get("llamadas") or 0)
        if llamadas <= 0:
            continue
        tramo = tramo_de(f.get("dias"))
        salida[tramo] = salida.get(tramo, 0.0) + llamadas
    return salida


def normalizar(rinde: Mapping[int, float], llamadas: Mapping[int, float]) -> Dict[int, float]:
    """Peso de cada tramo en la malla: rinde / R.

    R es el rinde que ya trae el TMO del pronóstico. El TMO promedio pondera por
    llamadas, así que R es la media ARMÓNICA del rinde ponderada por la
    participación de cada tramo en las llamadas de esa historia:
    1/R = Σ w_b / r_b. Con ese R, una malla con la misma mezcla de horas que la
    historia suma exactamente lo mismo en equivalentes que en personas.
    """
    total = sum(v for v in llamadas.values() if v > 0)
    tramos = tramos_con_antiguos()
    if not total:
        base = 1.0
    else:
        inversa = sum((max(llamadas.get(t, 0.0), 0.0) / total) / (rinde.get(t) or 1.0)
                      for t in tramos)
        base = 1.0 / inversa if inversa > 0 else 1.0
    return {t: min(max((rinde.get(t) or 1.0) / base, FACTOR_MINIMO), FACTOR_MAXIMO)
            for t in tramos}


def armar_curva(rinde_medido: Mapping[int, dict],
                llamadas_historia: Mapping[int, float]) -> List[dict]:
    """Las filas que se guardan: rinde medido, peso normalizado y de dónde salen."""
    rinde = {t: float(d["rinde"]) for t, d in rinde_medido.items()}
    factores = normalizar(rinde, llamadas_historia)
    total = sum(v for v in llamadas_historia.values() if v > 0)
    filas = []
    for desde, hasta in list(TRAMOS) + [(DIAS_ANTIGUO, None)]:
        medido = rinde_medido.get(desde) or {}
        filas.append({
            "dia_desde": desde, "dia_hasta": hasta,
            "rinde": round(rinde.get(desde, 1.0), 4),
            "factor": round(factores[desde], 4),
            "llamadas": int(medido.get("llamadas") or 0),
            "participacion": (round(max(llamadas_historia.get(desde, 0.0), 0.0) / total, 4)
                              if total else None),
        })
    return filas


def como_curva(filas: Iterable[Mapping]) -> Dict[int, float]:
    return {int(f["dia_desde"]): float(f["factor"]) for f in filas}


def _fecha(v) -> Optional[date]:
    if v is None:
        return None
    return v.date() if isinstance(v, datetime) else v


def factor_para(curva: Mapping[int, float], fecha_piso, dia: date) -> float:
    if not curva:
        return 1.0
    piso = _fecha(fecha_piso)
    dias = None if piso is None else (dia - piso).days
    return float(curva.get(tramo_de(dias), 1.0))


def contar_equivalentes(turnos: Iterable[Sequence], curva: Mapping[int, float],
                        intervalo_min: int) -> Dict[datetime, float]:
    """momento -> citados en equivalentes.

    `turnos`: (operador, inicio, final, fecha_piso). La misma regla que el conteo
    de personas de la malla —una persona cuenta una vez por intervalo aunque tenga
    filas que se pisen—, sólo que cada una suma su peso del día.
    """
    paso = timedelta(minutes=intervalo_min)
    presentes: Dict[datetime, Dict[object, float]] = {}
    for operador, inicio, final, fecha_piso in turnos:
        if not inicio or not final or final <= inicio:
            continue
        momento = inicio.replace(
            minute=(inicio.minute // intervalo_min) * intervalo_min,
            second=0, microsecond=0)
        while momento < final:
            presentes.setdefault(momento, {})[operador] = factor_para(
                curva, fecha_piso, momento.date())
            momento += paso
    return {m: round(sum(pesos.values()), 2) for m, pesos in presentes.items()}
