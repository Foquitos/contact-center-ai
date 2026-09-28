"""Planificador: cuánto hacerle caso al pronóstico que manda el cliente.

QUÉ ES `dbo.Forecast` Y QUÉ NO
------------------------------
Lo manda Voltara, pronostica NUESTRAS llamadas (ya trae el reparto adentro) y va
cargado hasta dos meses hacia adelante. No es un competidor: es información que
el cliente tiene y nosotros no —su calendario de campañas, su facturación, sus
cortes programados—. Por eso la pregunta correcta no es "cuál de los dos gana"
sino "cuánto peso le conviene a cada uno, y en qué días".

LO QUE DICE LA MEDICIÓN (464 días, 2025-06 a 2026-09, antelación 7)
--------------------------------------------------------------------
Error del total diario (MAPE):

    día              nuestro   cliente
    hábil              19,3%     37,5%
    sábado             36,8%     56,2%
    domingo+feriado    43,4%     49,8%

O sea que el nuestro gana en todos los tipos de día MIRANDO EL AÑO ENTERO, pero
no todos los meses: el del cliente ganó los fines de semana de octubre y
noviembre de 2025 y de julio, agosto y septiembre de 2026 —que son los meses
recientes, y por eso la impresión de la operación de que "el del cliente está más
cerca" es cierta para lo que se ve todos los días—. Su calidad salta muchísimo:
el ratio contra lo real fue 0,58× en octubre de 2025 y 1,76× en marzo de 2026.

De ahí sale el diseño: el peso NO se fija a mano ni de una vez, se MIDE contra lo
que pasó y se recalcula por tipo de día. Cuando el cliente viene bien, sube solo;
cuando se va a 1,76×, baja solo.

CÓMO SE COMBINA
---------------
En el logaritmo y sobre el NIVEL DEL DÍA, no intervalo por intervalo:

    combinado_del_día = nuestro_del_día ^ (1-w)  ×  cliente_del_día ^ w

y ese cociente se aplica como un factor a cada media hora del día. La curva
intradía y el reparto entre skills quedan siendo los nuestros. Es a propósito: el
error de FORMA de nuestro pronóstico ya es bajo (16-29% sabiendo el total del
día) y el que duele es el de NIVEL. Medido, mezclar intervalo por intervalo da lo
mismo (WAPE 42,8% contra 42,9% los fines de semana) y agrega la posibilidad de
importar la forma del cliente en las medias horas donde su reparto entre skills
está mal. Con el nivel alcanza.

POR QUÉ SÓLO LOS DÍAS NO HÁBILES (por defecto)
-----------------------------------------------
El peso óptimo de los días hábiles medido es ~0,03: el cliente no aporta y lo
poco que mueve, ensucia. Medido sobre los últimos seis meses, combinar también
los hábiles EMPEORA el error total (25,9% contra 26,6%) y no mejora los fines de
semana. Los no hábiles son los que valen: 41,8% -> 39,6%.

    variante                          todo   hábil   no hábil
    nuestro solo                      26,2%   19,3%     40,6%
    + reparto por tipo de día         25,3%   19,6%     37,2%
    + cliente en los dos tipos        25,2%   19,9%     36,0%
    + cliente sólo en no hábiles      24,9%   19,6%     36,0%   <-- ESTE

ACTUALIZACIÓN 2026-09-14: POR ACIERTO RECIENTE, SÓLO DOMINGOS Y FERIADOS
------------------------------------------------------------------------
Pedido de Ignacio: combinar según quién acertó últimamente. Re-medido sobre un
año (2025-11 a 2026-09) a 1-3 días de antelación —el horizonte con el que se
piden horas extra—, ya con el frío estacional y el arreglo de las medias horas
sin llamadas. MAPE del total diario de NUESTRAS llamadas:

    día        nuestro   cliente   LS acotado (el de arriba)   inverso del error reciente
    hábil       17,8%     38,5%        18,8%                        19,0%
    sábado      31,9%     60,8%        32,4%                        33,5% a 35,1%
    domingo     33,0%     40,5%        31,1% a 31,5%                28,4%   <-- ESTE
    feriado     39,1%     61,6%        37,7%                        34,3%   <-- ESTE

Dos cambios respecto de lo anterior, los dos medidos:
  - EL SÁBADO NO SE COMBINA. Con el reparto por tipo de día juntaba sábado,
    domingo y feriado, y el sábado empeora en todas las ventanas: el cliente no
    le aporta nada (60,8% de error propio).
  - EL PESO ES EL INVERSO DEL ERROR CUADRÁTICO (en logaritmo) de los últimos
    N_RECIENTES días del grupo. El de mínimos cuadrados, encogido y acotado,
    termina dándole al cliente ~0,10 y casi no mueve nada; el inverso del error
    le da ~0,33 los domingos y es el que baja el error. La ventana entre 10 y 20
    días da lo mismo (28,4%); con 4, 29,5%.
Queda un sesgo de -13% los domingos (nos quedamos cortos), que está del lado
que la operación prefiere: pasarse cuesta más que quedarse corto.

El módulo es lógica pura: no abre conexiones. Quién lee `dbo.Forecast` es
`planificador_datos.forecast_en_produccion`.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, Optional, Sequence, Tuple

from app import planificador as pl

logger = logging.getLogger(__name__)

# Cuántos días mira la estimación del peso. 120 y no la historia entera porque la
# calidad del pronóstico del cliente cambia de un mes al otro (ratio 0,58× a
# 1,76×): un peso ajustado sobre dos años promedia un cliente que ya no existe.
VENTANA_DEFECTO = 120

# Encogimiento hacia "no le hagas caso". Con pocos días el peso óptimo de la
# muestra es casi todo ruido, así que se lo multiplica por n/(n+K). Con K=5 hacen
# falta ~15 días del tipo para llegar al 75% del peso medido. Medido, entre K=0 y
# K=20 el error se mueve menos de una décima; está para que la primera semana no
# arranque con un peso inventado.
K_ENCOGIMIENTO = 5.0

# Tope del peso. No se le da al cliente más de la mitad del pronóstico ni cuando
# la ventana dice que se la merece: su calidad salta demasiado de un mes al otro
# como para quedar a merced de una ventana de 120 días.
PESO_MAXIMO = 0.5

# Mínimo de días de ese tipo para que el peso se calcule.
MIN_DIAS = 8

# Cuántos días del grupo mira el peso "por acierto reciente". Entre 10 y 20 da lo
# mismo; con 4 empeora. Ver el encabezado.
N_RECIENTES = 15

# Los grupos de día que SE COMBINAN. El sábado queda afuera: medido, empeora.
GRUPOS_COMBINABLES = ("no_habil",)


def grupo_de_dia(dia: date, feriados: set) -> str:
    """hábil / sábado / no hábil (domingo o feriado).

    El sábado va aparte porque no se combina: es 98,9% EMERGENCIAS como el
    domingo, pero el pronóstico del cliente no le agrega nada.
    """
    if dia in feriados:
        return "no_habil"
    n = dia.isoweekday()
    return "habil" if n <= 5 else ("sabado" if n == 6 else "no_habil")


@dataclass
class PesoCliente:
    """Cuánto pesa el pronóstico del cliente en un tipo de día, y por qué."""
    tipo: str                 # habil | no_habil
    peso: float               # 0 = sólo el nuestro, 1 = sólo el del cliente
    n: int                    # días con los que se midió
    mape_nuestro: float
    mape_cliente: float
    peso_crudo: float         # antes del encogimiento y del tope

    def como_dict(self) -> dict:
        return {
            "tipo": self.tipo,
            "peso": round(self.peso, 4),
            "n": self.n,
            "mape_nuestro": round(self.mape_nuestro, 4),
            "mape_cliente": round(self.mape_cliente, 4),
            "peso_crudo": round(self.peso_crudo, 4),
        }


def pesos_por_tipo(historia: Sequence[Tuple[date, float, float, float]],
                   feriados: Optional[Sequence[date]] = None,
                   ventana: int = VENTANA_DEFECTO,
                   k: float = K_ENCOGIMIENTO,
                   solo_no_habiles: bool = True,
                   maximo: float = PESO_MAXIMO) -> Dict[str, PesoCliente]:
    """El peso del cliente por tipo de día, medido sobre `historia`.

    `historia` son tuplas (día, real, nuestro, cliente) YA CERRADAS: días de los
    que se conoce lo que pasó. El que llama es el responsable de no meter ahí
    ningún día posterior a la fecha de corte; acá no hay forma de verificarlo y
    es el error que hace que un backtest se mienta.

    El peso sale de minimizar el residuo en el logaritmo. Con
    r_n = log(real/nuestro) y r_c = log(real/cliente), el w que minimiza
    Σ((1-w)·r_n + w·r_c)² es Σr_n(r_n-r_c) / Σ(r_n-r_c)², que es la fórmula de la
    combinación óptima de dos estimadores sesgados. Se acota a [0, `maximo`]: un
    peso negativo (apostar CONTRA el cliente) es la clase de cosa que anda en la
    ventana medida y explota fuera de ella.
    """
    fer = set(feriados or ())
    por_tipo: Dict[str, list] = {"habil": [], "no_habil": []}
    for dia, real, nuestro, cliente in sorted(historia):
        if real <= 0 or nuestro <= 0 or cliente <= 0:
            continue
        por_tipo[pl.tipo_de_dia(dia, fer)].append(
            (math.log(real / nuestro), math.log(real / cliente),
             abs(real - nuestro) / real, abs(real - cliente) / real))

    salida: Dict[str, PesoCliente] = {}
    for tipo, filas in por_tipo.items():
        filas = filas[-ventana:] if ventana else filas
        if len(filas) < MIN_DIAS:
            continue
        num = sum(rn * (rn - rc) for rn, rc, _, _ in filas)
        den = sum((rn - rc) ** 2 for rn, rc, _, _ in filas)
        crudo = (num / den) if den > 0 else 0.0
        peso = max(0.0, min(maximo, crudo)) * len(filas) / (len(filas) + k)
        if solo_no_habiles and tipo == "habil":
            peso = 0.0
        salida[tipo] = PesoCliente(
            tipo=tipo, peso=peso, n=len(filas), peso_crudo=crudo,
            mape_nuestro=sum(f[2] for f in filas) / len(filas),
            mape_cliente=sum(f[3] for f in filas) / len(filas))
    return salida


def pesos_recientes(historia: Sequence[Tuple[date, float, float, float]],
                    feriados: Optional[Sequence[date]] = None,
                    n: int = N_RECIENTES,
                    maximo: float = PESO_MAXIMO) -> Dict[str, PesoCliente]:
    """El peso del cliente por grupo de día, según quién acertó últimamente.

    Con e_n = log(real/nuestro) y e_c = log(real/cliente) sobre los últimos `n`
    días del grupo, el peso del cliente es (1/mse_c) / (1/mse_n + 1/mse_c): el
    que erró menos pesa más. Nunca es negativo y se acota a `maximo`.

    Los grupos que no se combinan (hábil y sábado) se devuelven igual, con peso
    0, para que la pantalla muestre por qué no se combinan.

    Como `pesos_por_tipo`, `historia` tiene que traer sólo días ya cerrados al
    corte: acá no hay forma de verificarlo.
    """
    fer = set(feriados or ())
    por_grupo: Dict[str, list] = {}
    for dia, real, nuestro, cliente in sorted(historia):
        if real <= 0 or nuestro <= 0 or cliente <= 0:
            continue
        por_grupo.setdefault(grupo_de_dia(dia, fer), []).append(
            (math.log(real / nuestro), math.log(real / cliente),
             abs(real - nuestro) / real, abs(real - cliente) / real))

    salida: Dict[str, PesoCliente] = {}
    for grupo, filas in por_grupo.items():
        filas = filas[-n:]
        if len(filas) < min(MIN_DIAS, n):
            continue
        mse_n = sum(f[0] ** 2 for f in filas) / len(filas) + 1e-6
        mse_c = sum(f[1] ** 2 for f in filas) / len(filas) + 1e-6
        crudo = (1 / mse_c) / (1 / mse_n + 1 / mse_c)
        peso = min(maximo, crudo) if grupo in GRUPOS_COMBINABLES else 0.0
        salida[grupo] = PesoCliente(
            tipo=grupo, peso=peso, n=len(filas), peso_crudo=crudo,
            mape_nuestro=sum(f[2] for f in filas) / len(filas),
            mape_cliente=sum(f[3] for f in filas) / len(filas))
    return salida


def factores_diarios(nuestro: Dict[date, float], cliente: Dict[date, float],
                     pesos: Dict[str, PesoCliente],
                     feriados: Optional[Sequence[date]] = None,
                     tope: float = 0.4) -> Dict[date, float]:
    """día -> por cuánto multiplicar NUESTRO pronóstico de ese día.

    `tope` acota el movimiento a ±40%. Es el mismo criterio que el resto del
    módulo: el cliente aporta información, pero un día suyo puede venir al doble
    de lo real y no tiene por qué arrastrar la dotación con él.
    """
    fer = set(feriados or ())
    salida: Dict[date, float] = {}
    for dia, mio in nuestro.items():
        suyo = cliente.get(dia)
        if not mio or not suyo or suyo <= 0:
            continue
        # El sábado no se combina nunca (ver GRUPOS_COMBINABLES), aunque haya un
        # peso de "no hábil" guardado de antes de que se separara.
        if grupo_de_dia(dia, fer) == "sabado":
            continue
        p = pesos.get(pl.tipo_de_dia(dia, fer))
        if p is None or p.peso <= 0:
            continue
        factor = math.exp(p.peso * math.log(suyo / mio))
        salida[dia] = min(1 + tope, max(1 - tope, factor))
    return salida


def simular(historia: Sequence[Tuple[date, float, float, float]],
            feriados: Optional[Sequence[date]] = None,
            antelacion: int = 7, ventana: int = VENTANA_DEFECTO,
            k: float = K_ENCOGIMIENTO, solo_no_habiles: bool = True,
            metodo: str = "reciente", n: int = N_RECIENTES
            ) -> Tuple[list, Dict[str, PesoCliente]]:
    """Qué habría dado la combinación día por día, midiendo el peso hacia atrás.

    Devuelve (filas, pesos_al_final). Cada fila trae el día, lo real, los dos
    pronósticos, el combinado y el peso que se usó.

    LA REGLA QUE HACE QUE ESTO NO SE MIENTA: el pronóstico del día D se hace con
    `antelacion` días de anticipación, así que el peso sólo puede mirar días
    cuyo resultado ya se conocía en esa fecha —los anteriores a D menos la
    antelación—. Sin la demora, el peso del domingo lo estaría eligiendo el
    sábado que ya pasó, y la simulación da un número que en producción no existe.
    """
    fer = set(feriados or ())
    conocidos: list = []
    pendientes: list = []
    filas = []
    pesos: Dict[str, PesoCliente] = {}
    for dia, real, nuestro, cliente in sorted(historia):
        corte = dia - timedelta(days=antelacion)
        while pendientes and pendientes[0][0] < corte:
            conocidos.append(pendientes.pop(0))
        pesos = (pesos_recientes(conocidos, fer, n=n) if metodo == "reciente"
                 else pesos_por_tipo(conocidos, fer, ventana=ventana, k=k,
                                     solo_no_habiles=solo_no_habiles))
        factor = factores_diarios({dia: nuestro}, {dia: cliente}, pesos,
                                  fer).get(dia, 1.0)
        p = pesos.get(grupo_de_dia(dia, fer)) if metodo == "reciente" else \
            pesos.get(pl.tipo_de_dia(dia, fer))
        filas.append({"dia": dia, "real": real, "nuestro": nuestro,
                      "cliente": cliente, "combinado": nuestro * factor,
                      "peso": round(p.peso, 4) if p else 0.0,
                      "tipo": (grupo_de_dia(dia, fer) if metodo == "reciente"
                               else pl.tipo_de_dia(dia, fer))})
        pendientes.append((dia, real, nuestro, cliente))
    return filas, pesos


def aplicar(demanda: Dict, filas: Sequence[dict],
            factores: Dict[date, float]) -> int:
    """Escala el pronóstico ya armado por el factor diario. Devuelve cuántos días tocó.

    Mueve las dos representaciones a la vez —la demanda que va al Erlang y las
    filas que se guardan— porque desacoplarlas es la forma de que la pantalla
    muestre un número y la dotación se calcule con otro. No agrega claves a las
    filas: van tal cual a un INSERT con nombre por columna.
    """
    if not factores:
        return 0
    for momento, skills in demanda.items():
        f = factores.get(momento.date())
        if f is None or f == 1.0:
            continue
        for d in skills:
            d.llamadas *= f
    tocados = set()
    for fila in filas:
        f = factores.get(fila["momento"].date())
        if f is None or f == 1.0:
            continue
        fila["acme"] = round(fila["acme"] * f, 2)
        tocados.add(fila["momento"].date())
    return len(tocados)
