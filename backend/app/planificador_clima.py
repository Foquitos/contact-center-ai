"""Planificador: cuánto mueve el clima la demanda de una distribuidora eléctrica.

POR QUÉ EXISTE
--------------
El backtest dejó claro que el error del pronóstico es casi todo de NIVEL —cuántas
llamadas entran— y no de forma: sabiendo el total del día, el error por media hora
baja de 44% a 21%. Y el nivel de Voltara no lo explica el calendario: lo explica el
tiempo. Es una distribuidora de luz, así que la demanda de energía y las fallas de
red se mueven con la temperatura, y los reclamos atrás.

NO ES "OLA DE CALOR": ES UNA U
-------------------------------
La primera hipótesis era el calor, y estaba mal. Medido sobre dos años de demanda
total del cliente contra el clima real del área de concesión, los quince días de
mayor demanda se parten en dos grupos:

    2026-01-13   máxima 31,5 °C   32.723 llamadas     calor
    2025-12-31   máxima 39,2 °C   28.276              calor extremo
    2025-07-02   máxima  7,9 °C   25.231              FRÍO
    2026-07-06   máxima 11,0 °C   25.116              FRÍO

Los dos extremos disparan la demanda y el medio no. El pico de agosto de 2026 que
rompía el pronóstico —el que motivó todo esto— no fue calor: fueron máximas de 10
a 14 °C, o sea una ola de FRÍO, más dos temporales (38 mm y ráfagas de 64 km/h el
6 de agosto). Por eso se modela con grados-día de refrigeración Y de calefacción,
y no con la temperatura a secas: una regresión lineal sobre la temperatura le pone
signo a la U y no ve ninguno de los dos picos.

QUÉ MODELA Y QUÉ NO
--------------------
Un factor MULTIPLICATIVO por día sobre el perfil estacional. No toca la curva
intradía —que ya funciona— ni el reparto entre BPOs. Es deliberadamente chico:
ocho rasgos y una regresión lineal sobre el logaritmo del cociente. Con ~700 días
de historia, un modelo con más parámetros aprende el ruido de dos veranos.

CÓMO SE ENCADENA CON LA CORRECCIÓN DE NIVEL
--------------------------------------------
El factor va ANTES de la corrección de nivel, no después:

    pronóstico = perfil_estacional  x  factor_clima  x  corrección_de_nivel

y la corrección se calcula sobre lo que queda DESPUÉS del clima. Si fuera al
revés, las dos estarían explicando lo mismo dos veces: la corrección de nivel mira
los últimos 28 días, que es justo donde vivió la ola de frío. Encadenado así, el
sesgo constante del modelo de clima lo absorbe la corrección y lo único que
termina importando es cuánto VARÍA el factor de un día a otro, que es lo que se
quiere.
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Umbral de confort para refrigeración, en grados de temperatura APARENTE (que ya
# incluye humedad y viento; en Buenos Aires la diferencia con la de bulbo seco
# llega a 8 °C en enero y es justo la que decide si se prende el aire).
BASE_FRIO = 24.0
# Umbral de calefacción, sobre la mínima aparente del día: es la noche la que
# prende las estufas.
BASE_CALOR = 14.0

# Cuánto se estira la respuesta al clima. La regresión ajusta minimizando el
# error cuadrático sobre el nivel diario, y eso ATENÚA la respuesta: los valores
# ajustados tienen menos varianza que la realidad (con R² 0,44, bastante menos).
# El resultado es que el modelo se queda corto justo cuando el clima aprieta, que
# es cuando importa. Medido sobre 357 días corridos:
#
#     estiramiento   WAPE      sesgo del año   sesgo mayo-agosto
#         x1,0       30,76%        +3,9%            +10,1%
#         x1,2       30,67%        +2,7%             +7,6%   <-- ESTE
#         x1,4       31,02%        +1,4%             +5,1%
#
# El x1,2 mejora las dos cosas a la vez; el x1,4 sigue bajando el sesgo pero ya
# lo paga con error. La mejora de WAPE es chica y podría ser ruido; la del sesgo
# es monótona en las tres dosis, que es la señal en la que se puede confiar. Y un
# sesgo positivo significa sub-dotar sistemáticamente, que es peor que errar para
# los dos lados: en junio de 2026 el pronóstico se quedaba 14,5% corto.
#
# Es una constante y no un parámetro de pantalla a propósito: no es una decisión
# de operación sino una corrección medida del método.
#
# 2026-09-14: VUELVE A 1,0. El estiramiento compensaba justamente lo que faltaba:
# el frío no pesa igual en julio que en septiembre, y sin saberlo el modelo se
# quedaba corto en la rampa de invierno. Con el grupo "estacional" adentro (ver
# GRUPOS) ya no hace falta. Re-medido sobre un año, antelación 1 a 3 días, MAPE
# del total diario del cliente:
#
#     amplitud   hábil   sábado  domingo  feriado   todo   últimos 90 d   últimos 30 d
#       x1,2     13,3%   24,8%   31,0%    31,8%    18,4%      20,3%          19,6%
#       x1,0     13,1%   25,2%   29,8%    30,8%    18,1%      19,4%          17,9%   <-- ESTE
#
# Probado en la misma tanda y DESCARTADO: ventana de nivel de 21 días (19,1%),
# elasticidad del domingo (18,4%), tope de la corrección de nivel en 0,5 (18,5%)
# y factor máximo 1,8 (18,8%).
AMPLITUD = 1.0
# A partir de estas ráfagas empiezan a caer ramas sobre la línea de media tensión.
RAFAGA_UMBRAL = 40.0

# FRENO DE LOS FACTORES GRANDES
# ------------------------------
# El estiramiento de arriba arregla el sesgo del rango medio y EMPEORA el de los
# días extremos. Medido sobre 464 días corridos (antelación 7), agrupando por
# cuánto corrigió el factor y mirando el cociente entre la demanda real del
# cliente y la pronosticada:
#
#     factor de clima     n     real/pronosticado    idem sólo no hábiles
#         <= 0,80        180          0,99                  0,99
#         <= 0,95         47          0,93                  0,92
#         <= 1,50         94          1,01                  1,08
#         1,50 - 1,80     55          0,90                  0,84
#         1,80 - 2,19     25          0,88                  0,76
#         >= 2,20         63          0,95                  0,87
#
# Hasta 1,5 el factor no tiene sesgo. Pasado eso se pasa, y en los días no
# hábiles se pasa mucho más: cuando dice x1,8-2,2 un sábado, entran 24% menos
# llamadas de las pronosticadas. En julio-septiembre de 2026 ese tramo llegó a
# 0,63, o sea 59% de sobrepronóstico. Es el mecanismo por el cual los domingos de
# agosto de 2026 salieron 88% de error diario CON clima y 47% sin él.
#
# POR QUÉ PASA: el ajuste es lineal sobre el logaritmo con términos cuadráticos
# (hdd², cdd²), y la respuesta real satura —al tercer día de frío ya está toda la
# gente que iba a llamar—. Una parábola no satura: sigue subiendo. Con mínima
# aparente de -1,8 °C (el 2026-09-06) hdd² vale 25 y el factor se va al tope.
#
# SE PROBÓ FRENARLO Y NO ENTRA. QUEDA APAGADO (exponente 1 = sin efecto).
# ----------------------------------------------------------------------
# El freno comprime el exceso del factor por encima de FRENO_DESDE:
#     factor' = FRENO_DESDE x (factor / FRENO_DESDE) ^ FRENO_EXPONENTE
# Medido sobre los mismos 464 días, EMPEORA en todas las dosis y en todos los
# tipos de día (MAPE del total diario / WAPE por media hora):
#
#     exponente      todo   hábil   sábado   domingo   WAPE
#       1,00 (off)   26,2%   19,3%    36,8%    47,1%   22,6%   <-- ESTE
#       0,70         26,8%   19,3%    38,9%    49,0%   22,8%
#       0,50         27,0%   19,5%    39,6%    49,0%   23,0%
#       0,35         27,3%   19,6%    40,3%    49,4%   23,3%
#
# POR QUÉ LA TABLA DE ARRIBA NO ERA LO QUE PARECÍA: agrupar por el valor
# PRONOSTICADO y mirar el sesgo de cada grupo es condicionar sobre la predicción,
# y eso produce reversión a la media incluso con un modelo perfectamente
# calibrado. Los días de factor alto en los que la demanda SÍ saltó son los que
# el freno arruina, y son los que importan: son los días de dotación de más.
# Con el freno en 0,5 el sesgo del tramo 1,80-2,19 pasa de 0,88 a 0,96 —o sea que
# el sesgo se corrige, tal como decía la tabla— y el error total SUBE. Corregir un
# sesgo condicionado a la predicción no es corregir un error.
#
# Queda implementado y apagado por dos razones: el número está medido y no hay que
# volver a medirlo, y si algún día el modelo se descalibra de verdad el freno es
# la perilla —pero hay que mostrar la tabla de arriba antes de moverla.
FRENO_DESDE = 1.5
FRENO_EXPONENTE = 1.0

# Nombres de los rasgos, en orden. El orden importa: es el de los coeficientes.
# Los rasgos, agrupados. El backtest mide cada grupo por separado: agregar
# variables "porque sí" sobre 1.100 días es la forma más rápida de aprender el
# ruido de tres veranos y creer que se mejoró.
GRUPOS = {
    # Lo que ya estaba: la U de temperatura y el temporal del día.
    "clima": ("cdd", "cdd2", "hdd", "hdd2", "cdd_ayer", "hdd_ayer",
              "lluvia", "rafaga"),
    # Persistencia. Una ola no es un día suelto: la tercera jornada de calor
    # rompe transformadores que la primera aguantó, y el frío sostenido deja las
    # estufas prendidas todo el día. Se acumulan tres y siete días.
    "persistencia": ("cdd_3d", "hdd_3d", "cdd_7d", "hdd_7d"),
    # El frío MODULADO POR LA ÉPOCA DEL AÑO. El mismo frío no rompe lo mismo en
    # julio que en septiembre, y un grado-día fijo no lo puede ver. EMERGENCIAS
    # con mínima aparente bajo 3 °C, relativo a la mediana de su día de semana
    # (2023-2026):
    #
    #     mes        mayo   junio   julio   agosto   septiembre
    #     hábil      x1,51  x1,76   x2,11   x1,21    x1,05
    #     finde      x1,61  x1,65   x1,97   x1,18    x0,81
    #
    # Sin esto el modelo le aplicaba a un domingo frío de septiembre la respuesta
    # de julio y el factor se iba al tope (x2,2): findes del 23/08 al 13/09/2026
    # pronosticados entre +60% y +110%. Los grados-día de frío (del día, de tres y
    # de siete días) se multiplican por el seno y el coseno del día del año, así
    # que la regresión elige sola la amplitud y la FASE de la modulación.
    #
    # Medido con backtest sobre un año (2025-09-15 a 2026-09-13), antelación 1 a 3
    # días, MAPE del total diario del cliente:
    #
    #                  hábil   sábado  domingo  feriado   todo   últimos 30 días
    #     sin esto     14,1%   26,7%   34,9%    29,2%    19,6%       33,7%
    #     con esto     13,3%   24,8%   31,0%    31,8%    18,4%       19,6%
    #
    # La misma modulación sobre el CALOR ("calor estacional", cdd × sen/cos) se
    # probó y no suma (18,4% igual, peor en feriados): queda afuera. Tampoco
    # suma la carga eléctrica de GBA de CAMMESA como rasgo.
    "estacional": ("hdd_s", "hdd_c", "hdd_3d_s", "hdd_3d_c", "hdd_7d_s", "hdd_7d_c"),
    # Más señal del MISMO pronóstico meteorológico, que ya se baja y hasta ahora
    # se tiraba: humedad (que agrava el calor), viento sostenido y amplitud
    # térmica.
    "clima_extra": ("humedad", "viento", "amplitud"),
    # Estacionalidad anual. NO es redundante con la temperatura: el perfil
    # estacional es una mediana de 52 semanas por día de semana, o sea que no
    # tiene ninguna estacionalidad de mes propia, y toda la amplitud del año la
    # tiene que cargar la corrección de nivel, que está acotada a ±35%. Es la
    # sospecha principal de por qué el verano fallaba.
    "anual": ("anual_sin", "anual_cos", "anual_sin2", "anual_cos2"),
    # Calendario. La facturación mueve COMERCIAL —el skill de mayor TMO— y no
    # tiene nada que ver con el clima; las vísperas y los fines de semana largos
    # corren llamadas de un día a otro.
    "calendario": ("dia_mes_sin", "dia_mes_cos", "vispera", "post_feriado",
                   "finde_largo", "vacaciones"),
}

RASGOS = GRUPOS["clima"]
RASGOS_COMPLETOS = tuple(n for g in GRUPOS.values() for n in g)

# R² mínimo para que el factor de un skill se aplique. Por debajo de esto el
# modelo no está explicando nada y multiplicar por él sólo mete ruido: COMERCIAL
# da 0,072 —es una cola de facturación y trámites, el clima no la mueve— y
# GRANDES-CUENTAS 0,019. EMERGENCIAS, en cambio, da 0,440.
R2_MINIMO = 0.10

# Mínimo de días para que valga la pena ajustar. Con menos de un año no se vio un
# verano y un invierno completos, y el modelo extrapola a ciegas justo en los
# extremos, que son los días que importan.
MIN_DIAS_PARA_AJUSTAR = 300

# El factor se acota. Un modelo lineal sobre el logaritmo puede escupir cualquier
# cosa si le llega un día fuera de todo lo visto (45 °C, 200 mm), y multiplicar la
# dotación por 6 por una extrapolación no es un plan, es un accidente.
FACTOR_MIN, FACTOR_MAX = 0.55, 2.20


def frenar(factor: float, desde: Optional[float] = None,
           exponente: Optional[float] = None) -> float:
    """Comprime el exceso del factor por encima de `desde`. Ver FRENO_DESDE.

    Con exponente 1 no hace nada, así que apagar el freno es poner
    FRENO_EXPONENTE = 1 y no hay una rama de código distinta para el caso.
    """
    desde = FRENO_DESDE if desde is None else desde
    exponente = FRENO_EXPONENTE if exponente is None else exponente
    if factor <= desde or desde <= 0:
        return factor
    return desde * (factor / desde) ** exponente


@dataclass
class ModeloClima:
    """Los coeficientes ajustados y cuánto explican."""
    coeficientes: List[float]          # [intercepto] + uno por rasgo
    n: int
    r2: float
    reduccion_residuo: float           # cuánto baja el desvío del residuo
    entrenado_hasta: date
    # Rango de cada rasgo en el entrenamiento. Fuera de ahí el modelo no sabe
    # nada: una regresión con término cuadrático extrapola con una parábola, y
    # con un día más caluroso que todo lo visto devuelve un factor absurdo. Se
    # recorta al borde en vez de creerle.
    rango: Dict[str, tuple] = field(default_factory=dict)
    # Qué rasgos usa este ajuste. Viaja con el modelo porque los grupos se
    # pueden activar y desactivar, y un vector de coeficientes sin la lista de
    # nombres es una bomba de tiempo: el día que cambie el orden de RASGOS, el
    # modelo guardado empieza a multiplicar la humedad por el coeficiente de la
    # lluvia y nadie se entera.
    rasgos: tuple = RASGOS

    def factor(self, dia: date, clima: Dict[date, dict],
               feriados: Optional[set] = None) -> Optional[float]:
        """Cuánto se aparta ese día del perfil estacional, por el clima.

        Devuelve None cuando no hay clima para el día: el que llama tiene que
        seguir sin corrección y no suponer 1,0 en silencio, porque un agujero en
        la descarga del pronóstico meteorológico se vería igual que un día normal.
        """
        r = rasgos_del_dia(dia, clima, feriados)
        if r is None:
            return None
        v = self.coeficientes[0] + sum(c * self._acotado(n, r[n]) for c, n in
                                       zip(self.coeficientes[1:], self.rasgos))
        # El intercepto NO se estira: mueve el nivel medio, y de eso ya se ocupa
        # la corrección de nivel. Lo que se estira es el apartamiento.
        v = self.coeficientes[0] + (v - self.coeficientes[0]) * AMPLITUD
        return min(FACTOR_MAX, max(FACTOR_MIN, frenar(math.exp(v))))

    def _acotado(self, nombre: str, valor: float) -> float:
        lo, hi = self.rango.get(nombre, (valor, valor))
        return min(hi, max(lo, valor))

    def como_dict(self) -> Dict[str, object]:
        return {
            "n": self.n,
            "r2": round(self.r2, 4),
            "reduccion_residuo": round(self.reduccion_residuo, 4),
            "entrenado_hasta": self.entrenado_hasta,
            "coeficientes": {nombre: round(c, 5) for nombre, c in
                             zip(("intercepto",) + tuple(self.rasgos),
                                 self.coeficientes)},
        }


def rasgos_del_dia(dia: date, clima: Dict[date, dict],
                  feriados: Optional[set] = None) -> Optional[Dict[str, float]]:
    """Todos los rasgos del día. None si falta el clima del día o del anterior.

    Devuelve el diccionario completo aunque el modelo use sólo algunos: cuál se
    usa lo decide `ModeloClima.rasgos`, para poder medir grupo por grupo sin
    tocar esta función.

    El día anterior es obligatorio porque el efecto se ACUMULA: la segunda
    jornada de una ola de calor rompe transformadores que la primera aguantó, y
    el frío del día previo deja las estufas prendidas desde temprano. Los
    acumulados de tres y siete días se degradan solos: si falta alguno de esos
    días, se promedia sobre los que hay en vez de descartar la fila entera.
    """
    hoy, ayer = clima.get(dia), clima.get(dia - timedelta(days=1))
    if not hoy or not ayer:
        return None
    feriados = feriados or set()
    cdd = max(0.0, _num(hoy.get("t_aparente_max")) - BASE_FRIO)
    hdd = max(0.0, BASE_CALOR - _num(hoy.get("t_aparente_min")))
    doy = dia.timetuple().tm_yday
    ang = 2 * math.pi * doy / 365.25

    r = {
        "cdd": cdd,
        # Al cuadrado porque el efecto no es lineal: de 30 a 34 °C se rompe mucho
        # más que de 26 a 30. Dividido por 10 para que los coeficientes queden en
        # una escala parecida y la regresión no dependa del condicionamiento.
        "cdd2": cdd * cdd / 10,
        "hdd": hdd,
        "hdd2": hdd * hdd / 10,
        "cdd_ayer": max(0.0, _num(ayer.get("t_aparente_max")) - BASE_FRIO),
        "hdd_ayer": max(0.0, BASE_CALOR - _num(ayer.get("t_aparente_min"))),
        # Logaritmo: la diferencia entre 0 y 10 mm importa mucho más que entre
        # 40 y 50, que ya es "temporal" en los dos casos.
        "lluvia": math.log1p(max(0.0, _num(hoy.get("lluvia_mm")))),
        "rafaga": max(0.0, _num(hoy.get("rafaga_kmh")) - RAFAGA_UMBRAL) / 10,

        # --- persistencia
        "cdd_3d": _acumulado(dia, clima, 3, frio=True),
        "hdd_3d": _acumulado(dia, clima, 3, frio=False),
        "cdd_7d": _acumulado(dia, clima, 7, frio=True),
        "hdd_7d": _acumulado(dia, clima, 7, frio=False),

        # --- clima que ya se baja y no se usaba
        # Centrada en 60% y dividida por 10 para que quede en la misma escala que
        # los grados-día: sin eso la regresión queda mal condicionada.
        "humedad": (_num(hoy.get("humedad_pct")) - 60.0) / 10,
        "viento": _num(hoy.get("viento_kmh")) / 10,
        # Amplitud térmica: un día que va de 8 a 30 grados prende la estufa a la
        # mañana y el aire a la tarde. La suma de grados-día no lo distingue de
        # uno estable a 19.
        "amplitud": (_num(hoy.get("t_aparente_max"))
                     - _num(hoy.get("t_aparente_min"))) / 10,

        # --- estacionalidad anual, en armónicos (sin saltos entre diciembre y
        # enero, a diferencia de una variable de mes)
        "anual_sin": math.sin(ang), "anual_cos": math.cos(ang),
        "anual_sin2": math.sin(2 * ang), "anual_cos2": math.cos(2 * ang),

        # --- calendario
        # El ciclo de facturación también en armónicos: Voltara reparte los grupos
        # de facturación a lo largo del mes, así que el efecto es una onda y no
        # un día puntual.
        "dia_mes_sin": math.sin(2 * math.pi * dia.day / 30.44),
        "dia_mes_cos": math.cos(2 * math.pi * dia.day / 30.44),
        "vispera": 1.0 if (dia + timedelta(days=1)) in feriados else 0.0,
        "post_feriado": 1.0 if (dia - timedelta(days=1)) in feriados else 0.0,
        # Fin de semana largo: un feriado pegado al fin de semana no se comporta
        # como un feriado suelto, corre el volumen a los días de alrededor.
        "finde_largo": _finde_largo(dia, feriados),
        # Enero y las vacaciones de invierno: menos actividad comercial, y el
        # perfil de 52 semanas no tiene forma de saberlo.
        "vacaciones": 1.0 if (dia.month == 1 or (dia.month == 7 and 10 <= dia.day <= 31)) else 0.0,
    }
    # --- frío modulado por la época del año (ver GRUPOS["estacional"])
    s, c = math.sin(ang), math.cos(ang)
    for nombre in ("hdd", "hdd_3d", "hdd_7d"):
        r[nombre + "_s"] = r[nombre] * s
        r[nombre + "_c"] = r[nombre] * c
    return r


def _acumulado(dia: date, clima: Dict[date, dict], dias: int, frio: bool) -> float:
    """Grados-día promedio de los últimos `dias` días, incluido el de hoy.

    Promedio y no suma: así el rasgo no cambia de escala cuando faltan días de
    clima, que es lo que pasa en el borde de la descarga."""
    valores = []
    for i in range(dias):
        d = clima.get(dia - timedelta(days=i))
        if not d:
            continue
        valores.append(max(0.0, _num(d.get("t_aparente_max")) - BASE_FRIO) if frio
                       else max(0.0, BASE_CALOR - _num(d.get("t_aparente_min"))))
    return sum(valores) / len(valores) if valores else 0.0


def _finde_largo(dia: date, feriados: set) -> float:
    """1 si el día cae en un bloque de tres días o más sin actividad laboral."""
    def libre(d):
        return d in feriados or d.isoweekday() >= 6
    if not libre(dia):
        return 0.0
    largo = 1
    d = dia - timedelta(days=1)
    while libre(d):
        largo += 1
        d -= timedelta(days=1)
    d = dia + timedelta(days=1)
    while libre(d):
        largo += 1
        d += timedelta(days=1)
    return 1.0 if largo >= 3 else 0.0


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def perfil_diario(real: Dict[date, float], dia: date, semanas: int,
                  feriados: set, excluidos: set) -> Optional[float]:
    """Mediana del mismo día de semana en las últimas `semanas` semanas.

    Es el análogo diario del perfil por intervalo. No lleva corrección de nivel a
    propósito: acá se quiere el residuo LIMPIO contra la estacionalidad, y la
    corrección se aplica después, sobre lo que el clima no explicó.
    """
    return _perfil(_indice(real, feriados), dia, semanas, excluidos)


def _indice(real: Dict[date, float], feriados: set) -> Dict[int, List[tuple]]:
    """Agrupa la serie por día de semana. El ajuste recorre un año de días y para
    cada uno busca su ventana: sin el índice es cuadrático sobre la serie entera,
    y el backtest lo repite una vez por fecha de corte."""
    por_dow: Dict[int, List[tuple]] = {}
    for d, v in real.items():
        por_dow.setdefault(_dow(d, feriados), []).append((d, v))
    for v in por_dow.values():
        v.sort()
    return por_dow


def _perfil(indice, dia: date, semanas: int, excluidos: set,
            dow: Optional[int] = None) -> Optional[float]:
    desde = dia - timedelta(weeks=semanas)
    muestras = [v for d, v in indice.get(dow if dow is not None else dia.isoweekday(), ())
                if desde <= d < dia and d not in excluidos]
    if len(muestras) < 3:
        return None
    return statistics.median(muestras)


def _dow(dia: date, feriados: set) -> int:
    return 7 if dia in feriados else dia.isoweekday()


def ajustar(real: Dict[date, float], clima: Dict[date, dict], hasta: date,
            semanas: int = 52, feriados: Optional[Sequence[date]] = None,
            excluidos: Optional[Sequence[date]] = None,
            rasgos: Optional[Sequence[str]] = None) -> Optional[ModeloClima]:
    """Ajusta el factor de clima con los días ESTRICTAMENTE anteriores a `hasta`.

    El corte es duro por la misma razón que en el backtest: un modelo entrenado
    con los días que después se van a evaluar da un resultado espectacular y
    falso. `hasta` tiene que ser la fecha en la que se hace el pronóstico.
    """
    rasgos = tuple(rasgos or RASGOS)
    feriados, excluidos = set(feriados or ()), set(excluidos or ())
    indice = _indice(real, feriados)
    filas, objetivo = [], []
    for dia in sorted(d for d in real if d < hasta):
        if dia in excluidos or real[dia] <= 0:
            continue
        base = _perfil(indice, dia, semanas, excluidos, _dow(dia, feriados))
        if not base or base <= 0:
            continue
        r = rasgos_del_dia(dia, clima, feriados)
        if r is None:
            continue
        filas.append([1.0] + [r[n] for n in rasgos])
        objetivo.append(math.log(real[dia] / base))

    if len(filas) < MIN_DIAS_PARA_AJUSTAR:
        logger.info("Clima: %d días con datos, hacen falta %d para ajustar.",
                    len(filas), MIN_DIAS_PARA_AJUSTAR)
        return None

    beta = _minimos_cuadrados(filas, objetivo)
    if beta is None:
        return None

    pred = [sum(b * x for b, x in zip(beta, fila)) for fila in filas]
    media = sum(objetivo) / len(objetivo)
    ss_res = sum((y - p) ** 2 for y, p in zip(objetivo, pred))
    ss_tot = sum((y - media) ** 2 for y in objetivo)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    desvio_sin = math.sqrt(ss_tot / len(objetivo))
    desvio_con = math.sqrt(ss_res / len(objetivo))
    return ModeloClima(
        coeficientes=list(beta), n=len(filas), r2=r2,
        reduccion_residuo=(1 - desvio_con / desvio_sin) if desvio_sin > 0 else 0.0,
        entrenado_hasta=hasta,
        rasgos=rasgos,
        rango={n: (min(f[i + 1] for f in filas), max(f[i + 1] for f in filas))
               for i, n in enumerate(rasgos)})


# Penalización de cresta (ridge). Con veinticinco rasgos, muchos correlacionados
# entre sí —los grados-día de hoy con los de tres días, los armónicos anuales con
# la temperatura—, mínimos cuadrados puro reparte el crédito entre ellos de forma
# arbitraria y da coeficientes enormes de signos opuestos que se cancelan. Anda
# igual en el entrenamiento y se descalabra con un día nuevo. El ridge no cambia
# el ajuste cuando las columnas son independientes y lo estabiliza cuando no.
RIDGE = 1e-3


def _minimos_cuadrados(filas, objetivo, ridge: float = RIDGE) -> Optional[List[float]]:
    """OLS con penalización de cresta, por ecuaciones normales con pivoteo.

    Sin numpy a propósito: son veintiséis columnas y mil filas, y el módulo se
    mantiene importable desde cualquier lado sin arrastrar dependencias.
    """
    k = len(filas[0])
    # X'X aumentada con X'y. Se acumula sólo el triángulo superior y después se
    # refleja: X'X es simétrica y calcular las dos mitades duplica el trabajo,
    # que con veintiséis columnas y mil filas se nota (el backtest reajusta el
    # modelo una vez por cada fecha de corte).
    m = [[0.0] * (k + 1) for _ in range(k)]
    for fila, y in zip(filas, objetivo):
        for i in range(k):
            fi = fila[i]
            if fi == 0.0:
                continue
            mi = m[i]
            for j in range(i, k):
                mi[j] += fi * fila[j]
            mi[k] += fi * y
    for i in range(k):
        for j in range(i):
            m[i][j] = m[j][i]
    # El intercepto NO se penaliza: si no, el modelo se ve empujado hacia un
    # factor de 1 y deja de poder representar un nivel distinto.
    for i in range(1, k):
        m[i][i] += ridge * len(filas)

    for col in range(k):
        piv = max(range(col, k), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            # Columna degenerada: un rasgo constante en toda la ventana (por
            # ejemplo cero días de calor si se entrena sólo con invierno).
            logger.warning("Clima: la matriz es singular en la columna %d.", col)
            return None
        m[col], m[piv] = m[piv], m[col]
        for r in range(k):
            if r == col:
                continue
            f = m[r][col] / m[col][col]
            for c in range(col, k + 1):
                m[r][c] -= f * m[col][c]
    return [m[i][k] / m[i][i] for i in range(k)]


def factores(modelo: Optional[ModeloClima], dias: Sequence[date],
             clima: Dict[date, dict],
             feriados: Optional[Sequence[date]] = None) -> Dict[date, float]:
    """El factor de cada día. Los días sin clima quedan afuera del diccionario."""
    if modelo is None:
        return {}
    feriados = set(feriados or ())
    salida = {}
    for dia in dias:
        f = modelo.factor(dia, clima, feriados)
        if f is not None:
            salida[dia] = f
    return salida


# =========================================================================
# ELASTICIDAD POR TIPO DE DÍA: el domingo no responde al clima como un martes
# =========================================================================
# EL PROBLEMA, MEDIDO
# --------------------
# El modelo de clima se ajusta POR SKILL, y eso es correcto (EMERGENCIAS tiene
# R² 0,44 y COMERCIAL 0,07). Pero tiene un efecto lateral en el fin de semana:
# un domingo de Voltara es 98,9% EMERGENCIAS —la cola más sensible al tiempo—
# mientras que un día hábil es 55% EMERGENCIAS y 40% COMERCIAL, que no se mueve.
# Resultado: el domingo recibe los factores MÁS GRANDES justo donde el modelo
# menos se sostiene. Medido sobre los últimos 60 días, el factor medio es x1,12
# en días hábiles y x1,26 en fines de semana, con máximos de 1,48 y 1,99.
#
# Y la demanda del domingo no acompaña. Regresando log(real/pronosticado) contra
# log(factor) sobre 464 días:
#
#     tipo       n     pendiente   elasticidad   correlación
#     hábil    312       -0,039        0,96         -0,04
#     sábado    65       -0,099        0,90         -0,12
#     domingo   66       -0,417        0,58         -0,34
#     feriado   20       -0,186        0,81         -0,24
#
# En días hábiles el factor está bien (pendiente ~0). El domingo cumple sólo el
# 58% de lo que el modelo le pide, y con correlación -0,34: es sistemático.
# Casos concretos: el 23/08/2026 el factor pidió x1,99 y entraron 3.426 llamadas
# contra las 7.516 pronosticadas; el 06/09 pidió x1,87 contra 3.407 reales.
#
# EL ARREGLO: f elevado a alfa, con alfa MEDIDO Y NO FIJO
# -------------------------------------------------------
# El alfa NO se escribe a mano: se mide sobre los domingos ya cerrados y se
# encoge hacia 1 —o sea, hacia no corregir— mientras haya pocos. Si el domingo
# empieza a responder al clima, el alfa sube solo y la corrección se apaga sin
# que nadie toque nada. Hoy converge a 0,711.
#
# SÓLO EL DOMINGO, Y ESO ESTÁ MEDIDO
# -----------------------------------
# El sábado converge a alfa ~0,95, o sea que no necesita corrección, y
# aplicársela igual le mete el ruido del ajuste: 23,44% -> 24,32% de MAPE
# diario. Restringido al domingo, el sábado queda intacto.
#
# CUÁNTO DA (medido con ESTE código, 464 días, antelación 7, MAPE diario)
# ------------------------------------------------------------------------
#                    464 días        últimos 180      últimos 90
#     hábil        13,3 -> 13,3     13,2 -> 13,2     14,2 -> 14,2
#     sábado       23,1 -> 23,4     23,7 -> 24,1     30,3 -> 30,8
#     domingo      29,4 -> 28,8     33,7 -> 32,2     45,0 -> 41,4
#     feriado      22,8 -> 22,4     19,0 -> 18,2     17,5 -> 16,3
#
# El efecto CRECE al acercarse al presente, que es la firma de que corrige algo
# que está pasando ahora. El sábado empeora unas décimas aunque no se lo toque:
# comparte la corrección de nivel con el domingo (la ventana está partida en
# hábil / no hábil), así que atenuar uno mueve al otro.
#
# Y hay que decir hasta dónde llega. Los seis fines de semana que motivaron
# todo esto siguen mal, sólo que menos:
#
#     2026-08-23   real 3426    +96% -> +81%
#     2026-09-06   real 3407    +68% -> +52%
#     2026-08-30   real 2254    +55% -> +56%
#
# Sobre el año el total no se mueve (WAPE 21,5%, MAPE 17,3%). Por eso nace
# APAGADO y se decide con el selector de la pestaña Comparación.
#
# PROBADO Y DESCARTADO para el mismo problema (no reintentar sin medir):
#   - topear el factor en los días no hábiles: EMPEORA (1,9 -> 29,80%;
#     1,5 -> 29,98%; 1,3 -> 30,15%, contra 29,65% sin tope).
#   - sacarle el clima al domingo del todo: 32,14%, mucho peor. La señal existe,
#     está mal escalada.
#   - partir la corrección de nivel en sábado / domingo+feriado (3 buckets) o en
#     los cuatro tipos: 30,62% y 30,18%. En 28 días hay cuatro domingos.
#   - subir el peso del GBDT de `planificador_nivel` en los fines de semana: el
#     peso óptimo no sobrevive la validación cruzada (el sábado pide 0,80 en una
#     mitad del período y 0,25 en la otra, y cruzado empeora en las dos).
#   - combinar con el pronóstico del cliente por día de semana: 29,7% -> 29,0%
#     sobre el año. Es la alternativa viva, pero da menos que esto.


# Cuántos días mira la ventana con la que se mide la elasticidad.
#
# 365 y no 180, y eso está medido. Lo que se corrige es ESTRUCTURAL —un domingo
# es 98,9% EMERGENCIAS y eso no cambia de mes a mes— así que la ventana corta no
# aporta adaptabilidad, aporta ruido. Con 180 días el alfa oscila alrededor del
# umbral y la corrección se prende y se apaga sola de una semana a la otra:
#
#     corte        ventana 180   ventana 365
#     2026-08-30      0,816         0,711
#     2026-08-16      0,929         0,711
#     2026-08-02      0,885         0,712
#
# Con 365 el valor es estable, y coincide con el que da la ventana completa
# (0,711), que es la firma de un parámetro estructural y no de un régimen.
VENTANA_ELASTICIDAD = 365
# Encogimiento hacia 1 (sin corrección). Con n domingos, alfa se mueve n/(n+k)
# de lo medido: con pocos días el ajuste es ruido y conviene no corregir.
ENCOGIMIENTO_ELASTICIDAD = 15
# Mínimo de días del tipo para siquiera intentarlo.
MIN_DIAS_ELASTICIDAD = 12
# Los tipos de día que se corrigen. Sólo el domingo: ver arriba.
TIPOS_ELASTICIDAD = ("domingo",)
# Banda muerta: si la elasticidad medida no baja de acá, no se corrige nada.
#
# NO ES UN NÚMERO DE ADORNO. El estimador está sesgado hacia abajo por error en
# la variable explicativa: el factor de clima no es un dato, es una estimación
# con ruido, y regresar contra una variable con ruido atenúa la pendiente hacia
# cero. Medido sobre series sintéticas donde el domingo responde EXACTAMENTE
# igual que un día hábil —o sea, donde la respuesta correcta es 1,00— el
# estimador devuelve 0,855. Sin banda muerta, el modelo atenuaría el clima del
# domingo aunque no hiciera falta.
#
# El umbral tiene que quedar ENTRE el sesgo del estimador (0,855) y el valor
# real medido en Voltara (0,685). Con 0,80 el caso real entra con margen y el caso
# "no hay nada que corregir" queda afuera.
UMBRAL_ELASTICIDAD = 0.80
# La elasticidad no puede salirse de esto. El 0 sería "el clima no existe el
# domingo", que está medido y es peor.
ELASTICIDAD_MIN, ELASTICIDAD_MAX = 0.3, 1.0


def tipo_de_dia_fino(dia: date, feriados: set) -> str:
    """Cuatro tipos y no dos. El sábado y el domingo NO son la misma población:
    su elasticidad al clima es 0,90 y 0,58."""
    if dia in feriados:
        return "feriado"
    n = dia.isoweekday()
    return "habil" if n <= 5 else ("sabado" if n == 6 else "domingo")


def elasticidad_por_tipo_de_dia(
        series: Dict[int, Dict[date, float]],
        factores: Dict[int, Dict[date, float]], hasta: date,
        semanas: int = 52, feriados: Optional[Sequence[date]] = None,
        excluidos: Optional[Sequence[date]] = None,
        ventana: int = VENTANA_ELASTICIDAD,
        tipos: Sequence[str] = TIPOS_ELASTICIDAD) -> Dict[str, float]:
    """Cuánto del movimiento del factor de clima se cumple, por tipo de día.

    Se mide sobre el AGREGADO de las colas que tienen modelo de clima, no cola
    por cola: lo que se corrige es la mezcla de skills del día, que es de dónde
    viene el problema.

    Sólo días ESTRICTAMENTE anteriores a `hasta`, como todo lo demás acá.
    """
    feriados, excluidos = set(feriados or ()), set(excluidos or ())
    desde = hasta - timedelta(days=ventana) if ventana else date.min
    indices = {s: _indice(serie, feriados) for s, serie in series.items()}

    # día -> (real, perfil, perfil x factor)
    por_dia: Dict[date, List[float]] = {}
    for skill_id, serie in series.items():
        if skill_id not in factores:
            continue
        for dia, valor in serie.items():
            if not (desde <= dia < hasta) or dia in excluidos or valor <= 0:
                continue
            f = factores[skill_id].get(dia)
            if f is None or f <= 0:
                continue
            base = _perfil(indices[skill_id], dia, semanas, excluidos,
                           _dow(dia, feriados))
            if not base or base <= 0:
                continue
            acum = por_dia.setdefault(dia, [0.0, 0.0, 0.0])
            acum[0] += valor
            acum[1] += base
            acum[2] += base * f

    salida: Dict[str, float] = {}
    for tipo in tipos:
        filas = [(math.log(a[2] / a[1]), math.log(a[0] / a[1]))
                 for d, a in por_dia.items()
                 if tipo_de_dia_fino(d, feriados) == tipo and a[1] > 0 and a[0] > 0]
        if len(filas) < MIN_DIAS_ELASTICIDAD:
            continue
        n = len(filas)
        mx = sum(x for x, _ in filas) / n
        my = sum(y for _, y in filas) / n
        sxx = sum((x - mx) ** 2 for x, _ in filas)
        if sxx <= 1e-9:
            continue
        beta = sum((x - mx) * (y - my) for x, y in filas) / sxx
        # Encogido hacia 1: con pocos días, no corregir.
        alfa = 1.0 + (beta - 1.0) * n / (n + ENCOGIMIENTO_ELASTICIDAD)
        alfa = min(ELASTICIDAD_MAX, max(ELASTICIDAD_MIN, alfa))
        if alfa <= UMBRAL_ELASTICIDAD:
            salida[tipo] = alfa
            logger.info("Clima: la elasticidad del %s es %.2f sobre %d días.",
                        tipo, alfa, n)
    return salida


def aplicar_elasticidad(factores: Dict[int, Dict[date, float]],
                        alfas: Dict[str, float],
                        feriados: Optional[Sequence[date]] = None
                        ) -> Dict[int, Dict[date, float]]:
    """`f` elevado a alfa según el tipo de día. Sin alfas, devuelve lo mismo."""
    if not alfas:
        return factores
    fer = set(feriados or ())
    return {s: {d: (f ** alfas[tipo_de_dia_fino(d, fer)]
                    if tipo_de_dia_fino(d, fer) in alfas and f > 0 else f)
                for d, f in por_dia.items()}
            for s, por_dia in factores.items()}


# =========================================================================
# UN MODELO POR SKILL
# =========================================================================
# POR QUÉ NO ALCANZA CON UNO SOLO SOBRE EL TOTAL
# -----------------------------------------------
# Las colas de Voltara no responden igual al tiempo, y no es un matiz:
#
#     serie          R²      templado   calor 36°   frío 2°
#     TOTAL         0,404      x0,65      x1,83      x1,38
#     EMERGENCIAS   0,440      x0,56      x2,20      x1,51
#     COMERCIAL     0,072      x0,95      x1,04      x1,12
#     GRANDES-CTAS  0,019      x1,01      x1,10      x1,10
#
# COMERCIAL es facturación y trámites: no la mueve el clima. Y en un día hábil es
# el 40% del volumen, así que un modelo ajustado sobre el TOTAL sale diluido —
# promedia la sensibilidad real de EMERGENCIAS con la nula de COMERCIAL.
#
# Eso es exactamente lo que rompía los fines de semana. Los sábados y domingos
# son 98,9% EMERGENCIAS (contra 54,9% en un día hábil): el día más sensible al
# clima de la semana estaba recibiendo el factor más diluido. Con calor de 36° el
# modelo del total dice x1,83 y el de EMERGENCIAS x2,20; medido contra un día
# templado, la diferencia es de 2,8x a 3,9x.
#
# Los skills que no llegan al R² mínimo NO reciben factor. Aplicarles uno que no
# explica nada les mete el ruido del ajuste sin comprarles nada.


def ajustar_por_skill(series: Dict[int, Dict[date, float]],
                      clima: Dict[date, dict], hasta: date,
                      semanas: int = 52,
                      feriados: Optional[Sequence[date]] = None,
                      excluidos: Optional[Sequence[date]] = None,
                      rasgos: Optional[Sequence[str]] = None,
                      r2_minimo: float = R2_MINIMO
                      ) -> Dict[int, ModeloClima]:
    """Un modelo de clima por skill. Sólo devuelve los que explican algo.

    Un skill que no llega al R² mínimo queda AFUERA del diccionario, y el que
    llama tiene que interpretarlo como "este skill no se corrige por clima" — que
    es distinto de "el factor es 1" sólo en apariencia, pero muy distinto en el
    código: obliga a decidirlo explícitamente en vez de heredarlo por descuido.
    """
    salida: Dict[int, ModeloClima] = {}
    for skill_id, serie in series.items():
        modelo = ajustar(serie, clima, hasta, semanas=semanas, feriados=feriados,
                         excluidos=excluidos, rasgos=rasgos)
        if modelo is None:
            continue
        if modelo.r2 < r2_minimo:
            logger.info("Clima: el skill %s explica sólo %.1f%% y queda sin factor.",
                        skill_id, modelo.r2 * 100)
            continue
        salida[skill_id] = modelo
    return salida


def factores_por_skill(modelos: Dict[int, ModeloClima], dias: Sequence[date],
                       clima: Dict[date, dict],
                       feriados: Optional[Sequence[date]] = None
                       ) -> Dict[int, Dict[date, float]]:
    """skill -> {día: factor}. Los skills sin modelo no aparecen."""
    return {skill_id: factores(modelo, dias, clima, feriados)
            for skill_id, modelo in modelos.items()}
