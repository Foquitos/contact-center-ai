"""Planificador: segunda opinión del NIVEL diario, con árboles de decisión.

POR QUÉ EXISTE
--------------
El error del pronóstico se parte en tres, y conviene tener los tres números a la
vista antes de tocar nada. Medido sobre 464 días (2025-06-01 a 2026-09-06),
antelación 7, WAPE por media hora sobre la demanda del cliente:

    pronóstico de producción                      22,7%
    con el NIVEL DIARIO REAL de cada cola         14,1%   <- techo del nivel
    con el nivel real y la mejor forma posible    13,9%
    piso de Poisson (ruido de llegada)             5,2%

O sea: lo único que puede ganar un modelo de nivel son 8,6 puntos, y la forma
intradía no da más de 0,2 — un estimador de forma que VE EL FUTURO (la mediana de
los cuatro mismos días de semana anteriores y los cuatro posteriores) mejora eso y
nada más. Por eso este módulo ataca el nivel del día y no toca la curva.

QUÉ HACE
--------
Un GBDT (`HistGradientBoostingRegressor`) que estima el mismo cociente que estima
el modelo de clima —cuántas llamadas tuvo el día contra lo que decía el perfil
estacional— pero sin imponerle forma funcional. No REEMPLAZA al modelo de clima:
se promedia con él en logaritmo, con peso `peso_nivel`.

Y ESO NO ES UN CAPRICHO: SOLO GANA MEZCLADO
--------------------------------------------
El GBDT por su cuenta PIERDE contra la regresión que ya está (25,6% contra 22,7%
de WAPE). Con 8.500 filas de entrenamiento y nueve series, un modelo de árboles
aprende ruido que el ridge de doce rasgos no puede aprender. Mezclado al 40%
gana, porque los dos se equivocan en lugares distintos:

    peso   WAPE     MAPE diario   sesgo
    0,00  22,69%      19,71%      +0,3%   (producción de hoy)
    0,25  21,81%      18,04%      +1,9%
    0,40  21,62%      17,54%      +2,6%   <- ELEGIDO
    0,50  21,62%      17,35%      +3,1%

Por tipo de día (MAPE diario): hábil 14,43 -> 13,30 · sábado 28,21 -> 23,41 ·
domingo 34,27 -> 29,59 · feriado 26,28 -> 24,88. Gana los seis trimestres del
período en las dos métricas y los cuatro tipos de día.

DECISIONES QUE COSTARON Y ESTÁN MEDIDAS
----------------------------------------
1. PÉRDIDA DE POISSON, no error cuadrático sobre el logaritmo. El cuadrático en
   log estima la MEDIANA condicional y al volver con la exponencial se queda
   corto (sesgo de Jensen): el modelo solo daba +7,8% de sesgo y la mezcla +2,6%.
   Con Poisson sobre el cociente, ponderado por el perfil —que es exactamente un
   Poisson sobre el conteo con offset—, se estima la MEDIA condicional. Mejora el
   error Y baja el sesgo a la vez: 21,99/18,03/+2,6% pasó a 21,93/17,89/+2,3%.
   El otro arreglo del sesgo, el factor de smearing de Duan, se probó y EMPEORA
   (22,37/18,77): saca el sesgo pero cuesta medio punto de error.
2. SIN RASGOS DE REZAGO. Meterle el nivel de los últimos 7 y 28 días parece
   obvio y es peor: 21,92% con ellos contra 21,62% sin ellos. La razón es que
   duplican lo que ya hace la corrección de nivel de `baseline_estacional`, y
   como la rama del GBDT no lleva corrección, la mezcla los contaba dos veces.
   Al revés también se probó: SOLO los rezagos da 33,9%, o sea que toda la señal
   está en el clima y el calendario.
3. SOLO NECESITA LA SERIE DIARIA. Con los rezagos afuera, la versión que se
   calcula sin abrir los intervalos da idéntico (21,62% contra 21,63%). Por eso
   este módulo recibe lo mismo que `planificador_clima` y usa su mismo helper de
   perfil, en vez de recorrer las medias horas.
4. NO ES "CORREGIR MENOS EL NIVEL". Como la rama del GBDT no lleva corrección de
   nivel, mezclar al 40% equivale a aplicar la corrección elevada a 0,6, así que
   había que descartar que la ganancia fuera esa y nada más. Medido: atenuar la
   corrección sola da 22,60% en el mejor caso (contra 22,69%), o sea 0,09 puntos.
   La ganancia es del modelo.
5. EL PRONÓSTICO QUE MANDA EL CLIENTE COMO RASGO: probado y descartado. Con el
   reparto fijo en 50% desde septiembre de 2026, `dbo.Forecast` dividido por el
   porcentaje es una estimación directa de la demanda del cliente, así que valía
   la pena probarlo. Da 22,08% contra 22,04% de la misma configuración sin él.

VERIFICADO DE PUNTA A PUNTA
----------------------------
Los números de arriba salen del banco de pruebas. Corriendo el código de este
módulo más `baseline_estacional` sobre los mismos 464 días, la comparación da:

    peso 0,00   WAPE 22,70%   MAPE diario 19,72%   sesgo +0,3%
    peso 0,40   WAPE 21,54%   MAPE diario 17,32%   sesgo +2,4%

Con peso 0 reproduce el pronóstico de producción de hoy hasta el segundo decimal,
que es la garantía de que prender esto es una decisión y no un efecto lateral.
Con 0,40 sale un poco MEJOR que en el banco (21,54 contra 21,62), y la razón está
identificada: acá cada fila de entrenamiento usa el perfil hasta el día mismo
—igual que `planificador_clima.ajustar`— y el banco lo tomaba hasta siete días
antes. Siete días más de historia por fila, y se nota sobre todo en los feriados
(22,5% contra 24,9%), que son pocos y agradecen cada muestra.

CUÁNDO NO SE APLICA
--------------------
Si falta scikit-learn, si no hay suficientes días o si la campaña lo tiene
apagado, `ajustar` devuelve None y el pronóstico queda exactamente como está hoy.
Es a propósito: la mezcla es una mejora medida, no una dependencia.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from . import planificador_clima as pclima

logger = logging.getLogger(__name__)

# UN SOLO HILO DE OpenMP PARA EL GBDT (medido el 2026-09-24, SRV00 y SRV01 igual)
# ---------------------------------------------------------------------------
# El recálculo desde la pantalla tardaba ~35 s y el frontend (gunicorn, 30 s) lo
# cortaba con un Error 500, aunque ajustar el mismo modelo desde un script tarda
# 1,4 s. La diferencia es DÓNDE corre: la API atiende cada pedido en un hilo de su
# pool, y cada hilo que usa OpenMP arma su propio equipo de 16 hilos que queda vivo.
# Con varios equipos vivos hay más hilos que núcleos, libgomp deja de esperar
# activamente y cada región paralela (el GBDT tiene miles, una por nodo) paga el
# despertar de los hilos. Mismo ajuste, con otros tres hilos que ya ajustaron antes:
#
#     límite de hilos      16      4      2      1
#     segundos            25-30   1,4    1,6    2,0
#
# Con 4 anda en la prueba, pero la API tiene decenas de hilos en su pool: con
# suficientes pedidos se vuelve a pasar de los núcleos. Con 1 no se arma ningún
# equipo y el costo es fijo: 2 s en vez de 1,4. Por eso 1, y sólo alrededor del
# ajuste y la predicción (threadpoolctl viene con scikit-learn).
HILOS_GBDT = 1
# Además: sklearn igual le pregunta a joblib cuántos núcleos FÍSICOS hay, y joblib
# lo averigua con `cat /proc/cpuinfo`. En SRV01 el servicio tiene el PATH recortado
# al venv, eso falla y deja un traceback en el log (inofensivo). Con este tope no
# pregunta.
os.environ.setdefault("LOKY_MAX_CPU_COUNT", "4")


def _un_hilo():
    from threadpoolctl import threadpool_limits
    return threadpool_limits(limits=HILOS_GBDT, user_api="openmp")

# Los mismos rasgos de día que usa el modelo de clima —los veinticinco, no los
# doce activos: el GBDT elige solo cuáles usar y no paga colinealidad— más los
# que dependen del skill y de la fecha.
RASGOS_DIA = tuple(
    pclima.GRUPOS["clima"] + pclima.GRUPOS["persistencia"]
    + pclima.GRUPOS["clima_extra"] + pclima.GRUPOS["anual"]
    + pclima.GRUPOS["calendario"])
RASGOS_PROPIOS = ("dow", "feriado", "skill", "log_perfil", "temporal", "trend")
COLUMNAS = RASGOS_DIA + RASGOS_PROPIOS
# `dow` y `skill` son categóricas: el día de semana no está ordenado (el domingo
# no es "más" que el sábado) y el número de cola menos todavía.
CATEGORICAS = tuple(c in ("dow", "skill") for c in COLUMNAS)

# Con menos filas que esto el modelo aprende ruido. Son ~9 colas x 45 días.
MIN_FILAS_PARA_AJUSTAR = 400
# Tope del factor, por las mismas razones que en el modelo de clima: un día con
# rasgos fuera de todo lo visto no puede multiplicar el pronóstico por diez.
FACTOR_MIN, FACTOR_MAX = 0.25, 4.0
# Umbral de temporal para el rasgo `temporal` (días desde el último), y su tope.
TEMPORAL_MM, TEMPORAL_RAFAGA, TEMPORAL_TOPE = 20.0, 60.0, 60

HIPERPARAMETROS = dict(
    loss="poisson",
    max_iter=300,
    learning_rate=0.05,
    max_leaf_nodes=31,
    min_samples_leaf=40,
    l2_regularization=1.0,
    # Sin parada temprana: sacaría una porción del final de la serie para
    # validar, que es justo la parte más informativa para pronosticar mañana.
    early_stopping=False,
    random_state=0,
)


@dataclass
class ModeloNivel:
    """Un modelo para TODAS las colas, con el skill como rasgo categórico.

    Uno por cola no alcanza los datos: las colas chicas tienen decenas de días
    útiles. Agrupadas, el árbol aprende la respuesta al clima una vez y la
    modula por cola, que es lo que hace falta (EMERGENCIAS se mueve con el
    tiempo y COMERCIAL no).
    """
    modelo: object
    columnas: Sequence[str]
    n: int
    entrenado_hasta: date
    skills: Sequence[int]
    epoca: date

    def factor(self, skill_id: int, dia: date, clima: Dict[date, dict],
               feriados: set, perfil: float) -> Optional[float]:
        return self.factores([(skill_id, dia, perfil)], clima, feriados)[0]

    def factores(self, pedidos: Sequence[Tuple[int, date, float]],
                 clima: Dict[date, dict], feriados: set) -> List[Optional[float]]:
        """El factor de cada (skill, día, perfil), con UN solo `predict`.

        De a una fila era la trampa: cada `predict` de sklearn cuesta varios
        milisegundos fijos (validación y arranque de los hilos de OpenMP), y el
        recálculo pide ~440 días x 13 colas. Medido el 2026-09-24 en SRV01: el
        recálculo de Voltara pasó de ~4 s a más de un minuto al prender el GBDT, y
        el frontend (gunicorn, 30 s) lo cortaba con un Error 500.
        """
        salida: List[Optional[float]] = [None] * len(pedidos)
        filas, posiciones = [], []
        for i, (skill_id, dia, perfil) in enumerate(pedidos):
            fila = _fila(skill_id, dia, clima, feriados, perfil, self.epoca)
            if fila is not None:
                filas.append(fila)
                posiciones.append(i)
        if not filas:
            return salida
        import numpy as np
        with _un_hilo():
            valores = self.modelo.predict(np.array(filas, dtype=float))
        for i, valor in zip(posiciones, valores):
            valor = float(valor)
            if math.isfinite(valor) and valor > 0:
                salida[i] = min(FACTOR_MAX, max(FACTOR_MIN, valor))
        return salida


def _temporal(dia: date, clima: Dict[date, dict]) -> float:
    """Días desde el último temporal. Un corte no se arregla el mismo día: los
    reclamos por daño siguen entrando la semana siguiente."""
    for i in range(1, TEMPORAL_TOPE + 1):
        c = clima.get(dia - timedelta(days=i))
        if not c:
            continue
        if (float(c.get("lluvia_mm") or 0) >= TEMPORAL_MM
                or float(c.get("rafaga_kmh") or 0) >= TEMPORAL_RAFAGA):
            return float(i)
    return float(TEMPORAL_TOPE)


def _fila(skill_id: int, dia: date, clima: Dict[date, dict], feriados: set,
          perfil: float, epoca: date) -> Optional[List[float]]:
    r = pclima.rasgos_del_dia(dia, clima, feriados)
    if r is None or perfil is None or perfil <= 0:
        return None
    return ([r[n] for n in RASGOS_DIA]
            + [float(pclima._dow(dia, feriados)),
               1.0 if dia in feriados else 0.0,
               float(skill_id),
               math.log(perfil),
               _temporal(dia, clima),
               float((dia - epoca).days)])


def ajustar(series: Dict[int, Dict[date, float]], clima: Dict[date, dict],
            hasta: date, semanas: int = 52,
            feriados: Optional[Sequence[date]] = None,
            excluidos: Optional[Sequence[date]] = None
            ) -> Optional[ModeloNivel]:
    """Ajusta con los días ESTRICTAMENTE anteriores a `hasta`.

    El corte duro es por lo mismo que en `planificador_clima.ajustar`: un modelo
    que vio los días que después se evalúan da un número espectacular y falso.
    """
    try:
        import numpy as np
        from sklearn.ensemble import HistGradientBoostingRegressor
    except ImportError:  # pragma: no cover - depende del entorno
        logger.info("Nivel: falta scikit-learn, el pronóstico queda sin la mezcla.")
        return None

    feriados, excluidos = set(feriados or ()), set(excluidos or ())
    dias_todos = [d for serie in series.values() for d in serie]
    if not dias_todos:
        return None
    epoca = min(dias_todos)

    filas, objetivo, peso = [], [], []
    for skill_id, serie in sorted(series.items()):
        indice = pclima._indice(serie, feriados)
        for dia in sorted(d for d in serie if d < hasta):
            if dia in excluidos or serie[dia] <= 0:
                continue
            base = pclima._perfil(indice, dia, semanas, excluidos,
                                  pclima._dow(dia, feriados))
            if not base or base <= 0:
                continue
            fila = _fila(skill_id, dia, clima, feriados, base, epoca)
            if fila is None:
                continue
            filas.append(fila)
            # Poisson sobre el COCIENTE ponderado por el perfil: equivale a un
            # Poisson sobre el conteo con offset, y es lo que hace que el modelo
            # estime la media condicional en vez de la mediana.
            objetivo.append(serie[dia] / base)
            peso.append(base)

    if len(filas) < MIN_FILAS_PARA_AJUSTAR:
        logger.info("Nivel: %d filas, hacen falta %d para ajustar.",
                    len(filas), MIN_FILAS_PARA_AJUSTAR)
        return None

    modelo = HistGradientBoostingRegressor(
        categorical_features=np.array(CATEGORICAS), **HIPERPARAMETROS)
    with _un_hilo():
        modelo.fit(np.array(filas, dtype=float), np.array(objetivo, dtype=float),
                   sample_weight=np.array(peso, dtype=float))
    return ModeloNivel(modelo=modelo, columnas=COLUMNAS, n=len(filas),
                       entrenado_hasta=hasta, skills=sorted(series),
                       epoca=epoca)


def niveles_por_skill(modelo: Optional[ModeloNivel],
                      series: Dict[int, Dict[date, float]],
                      dias: Sequence[date], clima: Dict[date, dict],
                      hasta: date, semanas: int = 52,
                      feriados: Optional[Sequence[date]] = None,
                      excluidos: Optional[Sequence[date]] = None
                      ) -> Dict[int, Dict[date, float]]:
    """skill -> {día: llamadas del día}. Es un NIVEL, no un factor.

    Se devuelve el nivel y no el cociente a propósito: el perfil con el que se
    entrenó (la mediana del total diario) no es el mismo que el que arma
    `baseline_estacional` (la suma de las medianas por intervalo), y difieren en
    un factor constante de ~0,77. Devolver el nivel deja esa diferencia adentro
    del módulo en vez de repartirla entre dos archivos.
    """
    if modelo is None:
        return {}
    feriados, excluidos = set(feriados or ()), set(excluidos or ())
    pedidos: List[Tuple[int, date, float]] = []
    for skill_id, serie in series.items():
        indice = pclima._indice(serie, feriados)
        for dia in dias:
            base = pclima._perfil(indice, min(dia, hasta), semanas, excluidos,
                                  pclima._dow(dia, feriados))
            if base and base > 0:
                pedidos.append((skill_id, dia, base))
    salida: Dict[int, Dict[date, float]] = {}
    for (skill_id, dia, base), f in zip(pedidos, modelo.factores(pedidos, clima, feriados)):
        if f is not None:
            salida.setdefault(skill_id, {})[dia] = base * f
    return salida
