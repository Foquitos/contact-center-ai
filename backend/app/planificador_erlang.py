"""Motor de dimensionamiento: de llamadas pronosticadas a operadores necesarios.

Módulo puro: no toca la base ni la red, no sabe de campañas ni de skills. Recibe
volumen, TMO y objetivos, y devuelve cuánta gente hace falta. Todo lo que decide
qué números entran acá vive en `app/planificador.py`.

QUÉ MODELO USA
--------------
Dos, y a propósito:

- **Erlang C** (`nivel_servicio_c`) asume que nadie corta: el que espera, espera
  para siempre. Es el modelo con el que la industria discute los contratos y el
  que va a usar Voltara si audita el número, así que es el que manda para
  dimensionar. Como no contempla el abandono, sobredimensiona.

- **Erlang A** (`metricas_a`) agrega la paciencia del cliente: cada uno en cola
  corta con una paciencia exponencial de media `paciencia_seg`. Sirve para dos
  cosas que Erlang C no puede contestar: cuánto abandono vamos a tener, y —clave
  para Voltara— cuál va a ser el nivel de servicio **medido sobre las atendidas**.

POR QUÉ IMPORTA "SOBRE LAS ATENDIDAS"
--------------------------------------
El NDS de Voltara se calcula como `atendidas en <= 20s / atendidas`, no sobre las
entrantes (verificado contra `Contestadas Umbral` del informe por intervalo). O
sea que las llamadas que abandonan **no cuentan en contra**: paradójicamente, con
poca gente el indicador se sostiene solo porque los que más esperan se van antes
de ser atendidos. Erlang C no puede ver eso; Erlang A sí. Por eso el motor
reporta las dos lecturas y `dimensionar()` puede exigir además un techo de
abandono, que es la única forma de que el número no se degrade solo.

CÓMO SE BUSCA EL NÚMERO
-----------------------
La búsqueda del mínimo de operadores se hace con Erlang C, que es cerrado y
barato, y recién sobre el resultado se evalúa Erlang A (que es un orden de
magnitud más caro) para verificar el abandono. Si el techo de abandono no se
cumple, se sigue subiendo. Evaluar Erlang A en cada paso de la búsqueda haría
inviable recalcular un año entero de intervalos.

UNIDADES
--------
Todo en segundos y en llamadas por intervalo. El "tráfico" (o carga ofrecida) se
mide en Erlangs: `llamadas * TMO / duración del intervalo` = cuántos operadores
harían falta si no existieran ni las colas ni los picos.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

# Los reportes de Voltara, Hidra y Gasur vienen en intervalos de media hora.
SEGUNDOS_INTERVALO = 1800

# Techo duro de la búsqueda. Un intervalo que pida más operadores que esto es un
# error de datos (un pico mal pronosticado, un TMO en minutos en vez de segundos)
# y conviene que salte como tal en vez de colgar el recálculo.
MAX_OPERADORES = 2000

# Hasta cuántos en cola se suma la distribución de Erlang A. Con tráficos
# normales la cola de la serie es despreciable mucho antes; el tope está para
# que un intervalo saturado no se vaya a un bucle largo.
_MAX_COLA = 400


# --------------------------------------------------------------------- Erlang C

def erlang_b(operadores: int, trafico: float) -> float:
    """Probabilidad de bloqueo de Erlang B (sin cola: el que no entra, se pierde).

    Se calcula con la recursión sobre la inversa, que es la forma numéricamente
    estable: `1/B(n) = 1 + (n/a) * 1/B(n-1)`. La versión directa se va a cero o
    a infinito con tráficos altos.
    """
    if operadores <= 0:
        return 1.0
    if trafico <= 0:
        return 0.0
    inversa = 1.0
    for n in range(1, operadores + 1):
        inversa = 1.0 + inversa * n / trafico
    return 1.0 / inversa


def erlang_c(operadores: int, trafico: float) -> float:
    """Probabilidad de que una llamada tenga que esperar (Erlang C).

    Con `trafico >= operadores` la cola no es estable: no hay capacidad para
    atender lo que entra, así que espera todo el mundo.
    """
    if operadores <= 0:
        return 1.0
    if trafico <= 0:
        return 0.0
    if trafico >= operadores:
        return 1.0
    b = erlang_b(operadores, trafico)
    rho = trafico / operadores
    return b / (1.0 - rho * (1.0 - b))


def nivel_servicio_c(operadores: int, trafico: float, tmo_seg: float,
                     umbral_seg: float) -> float:
    """Proporción de llamadas atendidas antes de `umbral_seg`, según Erlang C.

    Como Erlang C no tiene abandono, atendidas == entrantes: las dos lecturas del
    NDS coinciden. Por eso es la conservadora.
    """
    if operadores <= 0 or tmo_seg <= 0:
        return 0.0
    if trafico <= 0:
        return 1.0
    if trafico >= operadores:
        return 0.0
    c = erlang_c(operadores, trafico)
    return 1.0 - c * math.exp(-(operadores - trafico) * umbral_seg / tmo_seg)


def asa_seg(operadores: int, trafico: float, tmo_seg: float) -> float:
    """Tiempo medio de espera de TODAS las llamadas (Erlang C), en segundos.

    ASA = C * TMO / (n - a). Es el "TME" que la planilla de Excel usaba como
    restricción y el que la operación mira en su tablero. Con la cola inestable
    (a >= n) no hay una espera media finita: se devuelve infinito, que es la
    respuesta honesta.
    """
    if operadores <= 0 or tmo_seg <= 0:
        return math.inf
    if trafico <= 0:
        return 0.0
    if trafico >= operadores:
        return math.inf
    return erlang_c(operadores, trafico) * tmo_seg / (operadores - trafico)


def nivel_atencion_b(operadores: int, trafico: float) -> float:
    """Nivel de atención según el criterio de la planilla vieja: 1 - Erlang B.

    OJO: Erlang B modela un sistema SIN cola, donde la llamada que llega y no
    encuentra operador libre se pierde. En un call center con cola la mayoría de
    esas llamadas espera y termina siendo atendida, así que este número es
    sistemáticamente PESIMISTA. Con la dotación de 80/20 sobre 195 llamadas de
    TMO 180s da 94,3%, contra 99,4% de Erlang A con la paciencia medida.

    Se conserva porque es el criterio con el que se dimensionó históricamente y
    con el que se discutieron los compromisos: comparar contra el número viejo
    exige poder calcularlo igual. Para decidir, la lectura de Erlang A es la que
    describe la operación real.
    """
    return 1.0 - erlang_b(operadores, trafico)


def trafico_erlangs(llamadas: float, tmo_seg: float,
                    intervalo_seg: int = SEGUNDOS_INTERVALO) -> float:
    """Carga ofrecida en Erlangs: el piso teórico de operadores del intervalo."""
    if llamadas <= 0 or tmo_seg <= 0 or intervalo_seg <= 0:
        return 0.0
    return llamadas * tmo_seg / intervalo_seg


# --------------------------------------------------------------------- Erlang A

@dataclass
class MetricasA:
    """Lo que se espera que pase en un intervalo con `operadores` en línea."""
    operadores: int
    trafico: float
    p_espera: float             # probabilidad de no ser atendido al instante
    p_abandono: float           # proporción de las ENTRANTES que corta
    nds_sobre_atendidas: float  # el NDS como lo mide Voltara
    nds_sobre_entrantes: float  # el NDS "honesto", contando las que se fueron
    ocupacion: float            # proporción del tiempo que el operador habla

    @property
    def nivel_atencion(self) -> float:
        """Atendidas / entrantes. Es el indicador que Electrodependientes exige
        en 100%."""
        return 1.0 - self.p_abandono


def _distribucion_cola(operadores: int, trafico: float, beta: float):
    """Distribución del largo de cola que ve una llamada que llega y espera.

    Devuelve `(p_espera, pesos)`, donde `pesos[k]` es la probabilidad de que
    encuentre k adelante en la cola, *condicionada* a que le toque esperar.

    La cadena de nacimiento y muerte de M/M/n+M se normaliza en el estado n (con
    los n operadores ocupados y nadie en cola) en vez de en el estado 0. Es lo que
    evita el desborde: `a^j/j!` explota con tráficos altos, mientras que el
    cociente contra el estado n se queda siempre en un rango sano.
    """
    if operadores <= 0:
        return 1.0, [1.0]
    if trafico <= 0:
        return 0.0, [1.0]

    # Estados con operadores libres (j < n), normalizados contra el estado n.
    libres = 0.0
    u = 1.0
    for j in range(operadores, 0, -1):
        u *= j / trafico
        libres += u

    # Estados con cola (n + k). Con beta = 0 (nadie corta nunca) esto es la serie
    # geométrica de razón a/n, o sea Erlang C.
    pesos: List[float] = []
    c = 1.0
    total_cola = 0.0
    for k in range(_MAX_COLA + 1):
        if k > 0:
            c *= trafico / (operadores + k * beta)
        pesos.append(c)
        total_cola += c
        # La serie ya no aporta: cortar. El `k > operadores` evita cortar en el
        # arranque cuando el tráfico supera a la dotación y los primeros términos
        # todavía crecen.
        if k > operadores and c < 1e-14 * max(total_cola, 1e-300):
            break

    if total_cola <= 0:
        return 0.0, [1.0]

    p_espera = total_cola / (libres + total_cola)
    return p_espera, [p / total_cola for p in pesos]


def _cdf_por_etapas(operadores: int, beta: float, tmo_seg: float,
                    umbral_seg: float, etapas: int) -> List[float]:
    """P(la espera ofrecida termina antes del umbral), para cada largo de cola.

    Una llamada que llega y encuentra `k` adelante avanza un lugar cada vez que
    se libera un operador (tasa `n*mu`) o que alguno de los k que tiene adelante
    corta (tasa `k*theta`). Condicionada a que ella misma no corte, la espera es
    la suma de exponenciales independientes de tasas `mu*(n + j*beta)` con
    j = 1..k+1. Devuelve la acumulada de esa suma evaluada en el umbral, para
    todos los k de una sola pasada.

    Se resuelve por uniformización (la cadena discreta subyacente con saltos de
    Poisson) y no con la fórmula cerrada de la hipoexponencial: la cerrada usa
    fracciones parciales que se cancelan catastróficamente apenas la cola pasa de
    unos pocos lugares.
    """
    m = etapas
    if m <= 0 or tmo_seg <= 0 or umbral_seg < 0:
        return []

    mu = 1.0 / tmo_seg
    tasas = [mu * (operadores + j * beta) for j in range(1, m + 1)]
    lam = max(tasas)
    if lam <= 0:
        return [0.0] * m

    p_avance = [t / lam for t in tasas]
    lt = lam * umbral_seg

    # Cuántos saltos de Poisson mirar: la media más varias desviaciones, que deja
    # la masa que queda afuera por debajo de 1e-12.
    n_max = int(lt + 10.0 * math.sqrt(lt) + 30)

    v = [0.0] * (m + 1)   # v[j] = prob. de estar todavía en la etapa j
    v[0] = 1.0
    acum = [0.0] * (m + 1)

    peso = math.exp(-lt)
    masa = 0.0
    for n in range(n_max + 1):
        for j in range(m + 1):
            if v[j]:
                acum[j] += peso * v[j]
        masa += peso
        if masa > 1.0 - 1e-12:
            break
        # Un salto: cada etapa avanza con probabilidad p_avance[j].
        nuevo = [0.0] * (m + 1)
        for j in range(m):
            if v[j]:
                avanza = v[j] * p_avance[j]
                nuevo[j] += v[j] - avanza
                nuevo[j + 1] += avanza
        nuevo[m] += v[m]
        v = nuevo
        peso *= lt / (n + 1)

    # F[k] = P(completó las k+1 etapas) = 1 - P(sigue en alguna etapa <= k).
    cdf: List[float] = []
    restante = 1.0
    for k in range(m):
        restante -= acum[k]
        cdf.append(max(0.0, min(1.0, restante)))
    return cdf


def metricas_a(operadores: int, trafico: float, tmo_seg: float,
               umbral_seg: float, paciencia_seg: Optional[float],
               prioritario: bool = False) -> MetricasA:
    """Erlang A: qué pasa con `operadores` en línea, contando que el cliente corta.

    `paciencia_seg` es la media de la paciencia del cliente. `None` o <= 0 la
    apaga, y entonces todo esto colapsa exactamente en Erlang C (nadie abandona y
    las dos lecturas del NDS coinciden). Esa equivalencia está cubierta por tests
    y es la forma de verificar que la implementación no se fue de tema.

    `prioritario` describe una cola que el ACD atiende PRIMERO (en Voltara,
    Electrodependientes). Lo único que cambia es cuánta gente tiene adelante:
    ninguna. La llamada prioritaria espera igual a que se libere un operador
    —eso depende del estado del sistema y no de su prioridad, así que `p_espera`
    es la misma— pero cuando se libera, es la que entra.

    Está justo donde el modelo lo puede expresar: `pesos[k]` es la probabilidad
    de encontrar k adelante en la cola, y para la prioritaria toda la masa está
    en k = 0. De ahí salen solos un abandono mucho más bajo y un nivel de
    servicio mucho más alto, sin inventar ninguna fórmula nueva.

    ES UNA APROXIMACIÓN, Y SE SABE CUÁL: ignora que una llamada prioritaria
    pueda encontrar a OTRA prioritaria adelante. Vale mientras la cola con
    prioridad sea chica contra el total; en Voltara, Electrodependientes es el
    0,6% de las llamadas. Con una cola prioritaria grande esto subestimaría su
    espera.
    """
    operadores = max(0, int(operadores))
    ocupacion = (trafico / operadores) if operadores > 0 else 1.0

    if paciencia_seg is None or paciencia_seg <= 0:
        nds = nivel_servicio_c(operadores, trafico, tmo_seg, umbral_seg)
        return MetricasA(
            operadores=operadores,
            trafico=trafico,
            p_espera=erlang_c(operadores, trafico),
            p_abandono=0.0,
            nds_sobre_atendidas=nds,
            nds_sobre_entrantes=nds,
            ocupacion=ocupacion,
        )

    if trafico <= 0:
        return MetricasA(operadores, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0)

    # beta = theta/mu = cuánto dura la paciencia comparada con la llamada.
    beta = tmo_seg / paciencia_seg
    p_espera, pesos = _distribucion_cola(operadores, trafico, beta)
    if prioritario:
        # Espera lo mismo —el sistema está igual de ocupado— pero no tiene a
        # nadie adelante. `p_espera` se conserva; la distribución del largo de
        # cola se colapsa en cero.
        pesos = [1.0]
    if p_espera <= 0:
        return MetricasA(operadores, trafico, 0.0, 0.0, 1.0, 1.0, ocupacion)

    # P(ser atendido | k adelante). Sale de un producto que telescopa:
    # prod_{i=0..k} (n + i*beta)/(n + (i+1)*beta) = n / (n + (k+1)*beta).
    exito = [operadores / (operadores + (k + 1) * beta) for k in range(len(pesos))]
    cdf = _cdf_por_etapas(operadores, beta, tmo_seg, umbral_seg, len(pesos))

    p_atendida_esperando = 0.0
    p_a_tiempo_esperando = 0.0
    for k, w in enumerate(pesos):
        if w <= 0:
            continue
        p_atendida_esperando += w * exito[k]
        p_a_tiempo_esperando += w * exito[k] * cdf[k]

    p_abandono = p_espera * (1.0 - p_atendida_esperando)
    p_atendida = 1.0 - p_abandono
    # Las que no esperan se atienden en el acto: entran enteras al numerador.
    p_a_tiempo = (1.0 - p_espera) + p_espera * p_a_tiempo_esperando

    return MetricasA(
        operadores=operadores,
        trafico=trafico,
        p_espera=p_espera,
        p_abandono=max(0.0, min(1.0, p_abandono)),
        nds_sobre_atendidas=(p_a_tiempo / p_atendida) if p_atendida > 1e-12 else 0.0,
        nds_sobre_entrantes=max(0.0, min(1.0, p_a_tiempo)),
        ocupacion=ocupacion,
    )


# ---------------------------------------------------------------- dimensionar

@dataclass
class Requerimiento:
    """Cuánta gente hace falta en un intervalo y por qué."""
    llamadas: float
    tmo_seg: float
    trafico: float
    operadores_en_linea: int      # los que tienen que estar atendiendo
    operadores_a_planificar: int  # los anteriores + shrinkage: los que hay que citar
    nds_c: float                  # nivel de servicio por Erlang C (el contractual)
    nds_sobre_atendidas: float    # el que va a reportar el sistema de Voltara
    abandono_esperado: float
    ocupacion: float
    asa_seg: float = 0.0            # tiempo medio de espera (el "TME" de la planilla)
    nivel_atencion_b: float = 1.0   # 1 - Erlang B, el criterio de la planilla vieja
    motivo: str = ""              # qué restricción terminó fijando el número
    avisos: List[str] = field(default_factory=list)


def dimensionar(llamadas: float,
                tmo_seg: float,
                objetivo_nds: float = 0.80,
                umbral_seg: float = 20.0,
                paciencia_seg: Optional[float] = None,
                max_abandono: Optional[float] = None,
                max_ocupacion: Optional[float] = 0.85,
                shrinkage: float = 0.0,
                minimo_operadores: int = 0,
                intervalo_seg: int = SEGUNDOS_INTERVALO,
                max_asa_seg: Optional[float] = None,
                objetivo_nds_2: Optional[float] = None,
                umbral_seg_2: Optional[float] = None,
                min_nivel_atencion_b: Optional[float] = None) -> Requerimiento:
    """Operadores necesarios en un intervalo.

    Sube de a uno desde el piso teórico hasta que se cumplen, todas juntas:

    1. el nivel de servicio objetivo por **Erlang C** (el contractual);
    2. el techo de ocupación, si se pide (una dotación que cumple el NDS con la
       gente al 95% de ocupación no se sostiene: la gente se quema y el TMO sube,
       que es justamente lo que el modelo tomó como dato);
    3. el techo de abandono por **Erlang A**, si se pide. Es la restricción que
       manda en Electrodependientes, donde el compromiso es 100% de atención y no
       un nivel de servicio.

    Los tres últimos parámetros reproducen las restricciones que tenía la planilla
    de Excel con la que se dimensionaba antes (función `asesores`), para que el
    número nuevo sea comparable con el viejo:

    - `max_asa_seg` (el "TME"): techo del tiempo medio de espera.
    - `objetivo_nds_2` / `umbral_seg_2`: un SEGUNDO nivel de servicio, típicamente
      más laxo y a un plazo más largo (ej. 95% en 60s además de 80% en 20s).
    - `min_nivel_atencion_b`: piso de nivel de atención medido como 1 - Erlang B,
      que es el criterio de la planilla. Es pesimista (ver `nivel_atencion_b`),
      así que suele ser la restricción que más gente agrega; se deja disponible
      para poder reproducir el dimensionamiento histórico tal cual.

    `shrinkage` es la proporción del tiempo pago que no está en línea (pausas,
    capacitación, ausentismo): 0.30 significa que para tener 10 atendiendo hay que
    planificar 15. `minimo_operadores` es el piso de cobertura, para que la
    madrugada no quede en cero cuando el pronóstico da menos de una llamada.
    """
    llamadas = max(0.0, float(llamadas or 0.0))
    tmo_seg = max(0.0, float(tmo_seg or 0.0))
    trafico = trafico_erlangs(llamadas, tmo_seg, intervalo_seg)
    avisos: List[str] = []

    if trafico <= 0:
        n = max(0, int(minimo_operadores))
        return Requerimiento(
            llamadas=llamadas, tmo_seg=tmo_seg, trafico=0.0,
            operadores_en_linea=n, operadores_a_planificar=_con_shrinkage(n, shrinkage),
            nds_c=1.0, nds_sobre_atendidas=1.0, abandono_esperado=0.0, ocupacion=0.0,
            asa_seg=0.0, nivel_atencion_b=1.0,
            motivo="sin llamadas" if n == 0 else "cobertura mínima",
        )

    # EL PISO DE COBERTURA VA AL FINAL, NO COMO PUNTO DE PARTIDA. Si la búsqueda
    # arranca en el mínimo, en la madrugada todas las restricciones se cumplen de
    # entrada, el motivo nunca se asigna y queda "nivel de servicio" por defecto:
    # con una llamada en la media hora el nivel de servicio pide 1 operador, y la
    # pantalla decía que era el nivel de servicio el que pedía los 2 del piso.
    # Las restricciones son todas monótonas en n (más gente nunca empeora ninguna),
    # así que arrancar más abajo y aplicar el piso después da exactamente el mismo
    # número: sólo cambia que el motivo dice la verdad.
    n = max(1, int(math.floor(trafico)) + 1)
    motivo = ""
    while n <= MAX_OPERADORES:
        nds = nivel_servicio_c(n, trafico, tmo_seg, umbral_seg)
        if nds < objetivo_nds:
            motivo = "nivel de servicio"
            n += 1
            continue

        ocupacion = trafico / n
        if max_ocupacion and ocupacion > max_ocupacion:
            motivo = "techo de ocupación"
            n += 1
            continue

        if max_asa_seg is not None and asa_seg(n, trafico, tmo_seg) > max_asa_seg:
            motivo = "tiempo medio de espera"
            n += 1
            continue

        if objetivo_nds_2 and umbral_seg_2:
            if nivel_servicio_c(n, trafico, tmo_seg, umbral_seg_2) < objetivo_nds_2:
                motivo = "segundo nivel de servicio"
                n += 1
                continue

        if min_nivel_atencion_b is not None:
            if nivel_atencion_b(n, trafico) < min_nivel_atencion_b:
                motivo = "nivel de atención"
                n += 1
                continue

        if max_abandono is not None and paciencia_seg:
            m = metricas_a(n, trafico, tmo_seg, umbral_seg, paciencia_seg)
            if m.p_abandono > max_abandono:
                motivo = "techo de abandono"
                n += 1
                continue

        motivo = motivo or "nivel de servicio"
        break
    else:
        avisos.append(
            f"El intervalo pide más de {MAX_OPERADORES} operadores "
            f"({llamadas:.0f} llamadas con TMO {tmo_seg:.0f}s). Revisar el dato."
        )
        n = MAX_OPERADORES

    if n < minimo_operadores:
        n, motivo = int(minimo_operadores), "cobertura mínima"

    final = metricas_a(n, trafico, tmo_seg, umbral_seg, paciencia_seg)
    return Requerimiento(
        llamadas=llamadas,
        tmo_seg=tmo_seg,
        trafico=trafico,
        operadores_en_linea=n,
        operadores_a_planificar=_con_shrinkage(n, shrinkage),
        nds_c=nivel_servicio_c(n, trafico, tmo_seg, umbral_seg),
        nds_sobre_atendidas=final.nds_sobre_atendidas,
        abandono_esperado=final.p_abandono,
        ocupacion=final.ocupacion,
        asa_seg=asa_seg(n, trafico, tmo_seg),
        nivel_atencion_b=nivel_atencion_b(n, trafico),
        motivo=motivo,
        avisos=avisos,
    )


def _sin_shrinkage(operadores: float, shrinkage: float) -> float:
    """Igual que `_con_shrinkage` pero SIN redondear.

    Existe para poder encadenar varios descuentos y redondear una sola vez al
    final. Redondear en cada paso inventa gente: ver el comentario de
    `planificador.dimensionar_intervalo`.
    """
    if operadores <= 0:
        return 0.0
    s = min(max(float(shrinkage or 0.0), 0.0), 0.95)   # 0.95 = tope de sanidad
    return operadores / (1.0 - s)


def _con_shrinkage(operadores: int, shrinkage: float) -> int:
    """Operadores a planificar para tener `operadores` efectivamente en línea."""
    if operadores <= 0:
        return 0
    return int(math.ceil(_sin_shrinkage(operadores, shrinkage)))
