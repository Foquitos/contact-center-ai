"""Planificador: de la serie histórica al requerimiento de operadores por intervalo.

Este módulo es la lógica pura del planificador. No abre conexiones ni sabe de
FastAPI: recibe configuración y series como estructuras de Python y devuelve el
plan. Lo que lee y escribe la base vive en `app/planificador_datos.py`; el
cálculo de colas, en `app/planificador_erlang.py`.

LAS TRES ETAPAS
---------------
1. **Volumen** (`baseline_estacional`) — cuántas llamadas por skill y por
   intervalo. Hoy es una línea de base estacional: la mediana del mismo día de
   semana a la misma hora en las últimas semanas, corregida por el nivel
   reciente. NO es el modelo definitivo; es el piso contra el que hay que medir
   al de machine learning cuando esté la descarga del 100% de las llamadas de
   Voltara. Que exista ahora es lo que permite que todo lo de abajo funcione y se
   pueda probar de punta a punta.

2. **Dimensionamiento** (`dimensionar_intervalo`) — cuántos operadores hacen
   falta. Se calcula POR POOL, no por skill, porque los operadores son
   multiskill; ver el encabezado de la migración 2026-09-03_planificador.sql.

3. **Traducción a gente citada** — el número de Erlang son operadores efectivos
   sobre la cola. Para llegar a cuánta gente hay que tener en el turno se aplican
   dos factores distintos, y es importante no confundirlos:

     - `disponibilidad` (medida, ~0,82 de día en Voltara): de los que están
       presentes, qué proporción está realmente atendiendo esa cola en un momento
       dado. Cubre pausas cortas, ACW largo y tiempo en otras colas.
     - `shrinkage` de nómina: de los que están en la lista, qué proporción no
       viene ese día (ausentismo, vacaciones, capacitación).

   Aplicar las pausas en los dos lados descuenta dos veces lo mismo y
   sobredimensiona. La regla es: Erlang → dividir por disponibilidad → dividir
   por (1 - shrinkage).

POR QUÉ EL POOL Y NO EL SKILL
------------------------------
Cada operador de Voltara está logueado en 3 a 5 colas: la suma de agentes por
skill da ~355 en un intervalo donde hay 119 personas. Sumar el requerimiento de
nueve Erlang C independientes tira a la basura la economía de escala de la cola
compartida y pide el triple de gente. El pool junta el tráfico de todos sus
skills y dimensiona una sola vez.

QUÉ OBJETIVO SE LE EXIGE AL POOL
---------------------------------
Los objetivos de ESPERA sí se componen tomando el más estricto de los skills con
volumen en ese intervalo: el mayor objetivo de NDS y el menor umbral. El techo de
ABANDONO no, y es la única excepción: cuánto abandono produce una espera dada
depende de la paciencia de cada tipo de cliente, así que aplicarlo a nivel pool
obligaría a usar una paciencia promediada y le terminaría exigiendo a los
clientes de Emergencias (cuya paciencia equivale a 1.126s) el techo del skill más
estricto. Por eso se dimensiona primero por espera y después se verifica el
abandono skill por skill, cada uno con su propia paciencia. Es lo que permite que
Electrodependientes —cuyo compromiso es de nivel de atención y no de espera—
conviva en el pool grande sin obligar a dimensionar toda la cola como si fuera él.
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from app import planificador_erlang as erlang

logger = logging.getLogger(__name__)

# Cuántas semanas mira la línea de base para la mediana del día de semana.
# Es el respaldo de `CampanaCfg.semanas_base`; se dejó en 52 y no en las 8
# originales porque 8 semanas está medido y es malo (ver el comentario del
# dataclass): un evento de dos semanas se come la cuarta parte de la ventana.
SEMANAS_BASE = 52
# Con menos de esto para un (día de semana, hora) no hay mediana confiable.
MIN_OBSERVACIONES = 3
# Ventana para corregir el nivel reciente (el error que hoy tiene dbo.Forecast:
# el volumen bajó y el pronóstico se quedó arriba).
DIAS_NIVEL_RECIENTE = 28
# Cuánto puede corregir esa ventana. Sin tope, un evento de tres días arrastra el
# pronóstico del mes entero.
TOPE_CORRECCION_NIVEL = 0.35
# Cómo se cierra la corrección de nivel sobre su ventana: sumando todos los días
# ("suma"), con la mediana de los cocientes diarios ("mediana") o sumando después
# de sacar el día más alto y el más bajo ("recortada").
#
# VA "RECORTADA", y las tres están medidas sobre los mismos 464 días corridos
# (2025-06 a 2026-09, antelación 7). Error del total diario y WAPE por media hora:
#
#     cierre        todo   hábil   sábado   domingo   feriado   WAPE   findes Q3
#     suma         26,2%   19,3%   36,8%    47,1%     31,4%    22,6%    64,9%
#     mediana      25,4%   18,4%   34,9%    46,3%     36,8%    22,1%    63,0%
#     recortada    25,9%   18,9%   36,5%    46,6%     32,9%    22,4%    62,2%
#
# EL PROBLEMA QUE RESUELVE: la ventana son 28 días y, partida por tipo de día,
# deja OCHO muestras del lado no hábil. Con ocho, un solo día extremo se lleva la
# suma puesta: el sábado 15 de agosto de 2026 entraron 9.794 llamadas contra las
# ~3.000 de un sábado normal, y los tres fines de semana siguientes se
# pronosticaron al doble de lo que entró. Sacar el día más alto y el más bajo
# alcanza para que eso no pase.
#
# POR QUÉ NO LA MEDIANA, QUE MIDE MEJOR EN EL PROMEDIO: pierde la capacidad de
# seguir un cambio de régimen. Con un salto real de volumen (x10 de un día para el
# otro) la mediana no se mueve hasta que la mitad de la ventana quedó del lado
# nuevo, o sea catorce días; la suma reacciona al día siguiente y la recortada
# también. Hay un test que fija esa propiedad, y no es teórica: en Voltara el
# reparto ya saltó de golpe una vez (2026-09-01, de 32% a 49%).
# Y la mediana rompe los feriados (31,4% -> 36,8%, 12 de 20 individualmente),
# porque el cociente por suma pondera por volumen y un feriado es un día no hábil
# de volumen ALTO; la recortada conserva esa ponderación.
NIVEL_ROBUSTO = "recortada"


# --------------------------------------------------------------- configuración

@dataclass
class SkillCfg:
    skill_id: int
    nombre: str
    pool_id: Optional[int] = None
    objetivo_nds: Optional[float] = None
    umbral_seg: Optional[int] = None
    max_abandono: Optional[float] = None
    paciencia_seg: Optional[int] = None
    # Restricciones que traía la planilla de Excel con la que se dimensionaba
    # antes (función `asesores`). Se conservan para que el número nuevo sea
    # comparable con el viejo; ver planificador_erlang.dimensionar.
    max_asa_seg: Optional[int] = None          # el "TME" de la planilla
    objetivo_nds_2: Optional[float] = None     # segundo nivel de servicio
    umbral_seg_2: Optional[int] = None
    min_nivel_atencion_b: Optional[float] = None   # 1 - Erlang B (criterio viejo)
    # Cola que el ACD atiende PRIMERO. En Voltara es Electrodependientes: cuando
    # entra una de esas, es la que sale. Cambia sólo la verificación de SU techo
    # de abandono —una llamada prioritaria no tiene a nadie adelante en la cola—
    # y no el dimensionamiento del pool, que sigue siendo el agregado.
    prioridad: bool = False
    activo: bool = True


@dataclass
class PoolCfg:
    pool_id: int
    nombre: str
    min_operadores: int = 0
    activo: bool = True
    # Sub-campañas de RRHH (dbo.campanas.id) cuya gente atiende este pool. Es lo
    # que permite leer de payroll la dotación planificada y el ausentismo real.
    # Vacío = el pool no se puede comparar contra la malla.
    origen_rrhh: List[int] = field(default_factory=list)


@dataclass
class FranjaDisponibilidad:
    dia_semana: int      # 1 = lunes ... 7 = domingo; 0 = todos
    hora_desde: int
    hora_hasta: int      # exclusivo
    factor: float
    # 'medido' | 'manual' | None. Sólo informativo: la pantalla marca qué franjas
    # se cargaron a mano, que es lo primero a revisar cuando el número no cierra.
    origen: Optional[str] = None


@dataclass
class CampanaCfg:
    campana_id: int
    intervalo_min: int = 30
    max_ocupacion: Optional[float] = 0.85
    shrinkage: float = 0.30
    paciencia_seg: Optional[int] = None
    pools: Dict[int, PoolCfg] = field(default_factory=dict)
    skills: List[SkillCfg] = field(default_factory=list)
    disponibilidad: List[FranjaDisponibilidad] = field(default_factory=list)

    # El shrinkage no es un solo número. Los dos de abajo en None caen al general
    # —que es lo correcto para el fin de semana: medido sobre 180 días, sábado
    # 9,0% y domingo 10,2% contra 8,9% de un hábil, o sea lo mismo—. El que de
    # verdad se aparta es el FERIADO: 5,3%, y con explicación estructural, porque
    # el que no trabaja un feriado se carga con licencia y CERO horas
    # programadas: esa malla es de voluntarios y se cumple casi entera.
    shrinkage_no_habil: Optional[float] = None
    shrinkage_feriado: Optional[float] = None
    # Minutos de break por cada hora planificada del turno (5 min/hora = 8,33%).
    # NACE EN CERO a propósito: ese 8,33% ya está adentro del factor de
    # disponibilidad, que se estima invirtiendo el NDS observado contra los
    # agentes presentes —y el que está en su break está presente y no está sobre
    # la cola—. Ponerlo en 5 sin volver a medir la disponibilidad pide 9% más de
    # gente por una hora ya descontada. Ver la migración 2026-09-10.
    break_min_por_hora: float = 0.0
    # Franja horaria [desde, hasta) en la que la gente a citar se redondea PARA
    # ABAJO. De madrugada la cola pide 1 a 3 en línea y la cadena les suma menos
    # de una persona (2 → 2,5 → 3): para arriba es citar un 50% más por una
    # fracción. Para abajo nunca queda debajo de «en línea», porque la cadena sólo
    # divide por factores menores a 1. None = apagado. Desde > hasta cruza la
    # medianoche. Migración 2026-09-17 (Voltara, 0 a 8).
    redondeo_abajo_desde: Optional[int] = None
    redondeo_abajo_hasta: Optional[int] = None
    # Los feriados del horizonte. Los carga el servicio; sin ellos un feriado se
    # dimensiona como el día de semana que le toque.
    feriados: frozenset = frozenset()
    # Cuánto se aparta cada media hora de la presencia media de su tipo de día:
    # (tipo_dia, minuto del día) -> puntos que se suman al shrinkage. Lo mide
    # scripts/planificador_perfil_presencia.py y lo carga `cargar_config`. Vacío,
    # el shrinkage es parejo en toda la jornada. Ver `planificador_presencia`.
    perfil_presencia: Dict[Tuple[str, int], float] = field(default_factory=dict)
    perfil_presencia_medido_en: Optional[datetime] = None
    # Cuánto pesa en la malla una persona según los días que lleva en piso: día
    # desde del tramo -> factor. Lo mide scripts/planificador_antiguedad.py. Vacío,
    # cada citado cuenta 1. Ver `planificador_antiguedad`.
    curva_antiguedad: Dict[int, float] = field(default_factory=dict)
    curva_antiguedad_medido_en: Optional[datetime] = None

    # Cuánta historia mira la línea de base. NO es un detalle de implementación:
    # es el parámetro más sensible del pronóstico. Con 8 semanas, un evento de
    # dos —la ola de calor de agosto de 2026— es la cuarta parte de la ventana y
    # para un día de semana puntual llega a ser la mitad de las muestras, o sea
    # que se le mete adentro a la mediana. Medido con el backtest, pasar de 8 a
    # 52 semanas baja el error diario de 49,7% a 31,0% en agosto-septiembre.
    semanas_base: int = 52
    # Ventana de la corrección de nivel. 28 y no 14 por lo mismo que arriba: con
    # dos semanas la corrección ES el evento.
    #
    # Se probó 42 y NO entra, aunque a primera vista parecía que sí. Medido sobre
    # 357 días corridos (WAPE ½h):
    #
    #     antelación    28 días   42 días
    #          1         30,98%    30,68%
    #          7         30,92%    30,81%
    #         30         31,03%    31,42%
    #
    # Gana en el horizonte corto y pierde en el largo, y promediando los tres
    # queda 30,98% contra 30,97%: empate. Tiene sentido —a 30 días de antelación
    # la ventana de 42 alcanza 72 días hacia atrás en vez de 58, y ahí diluir el
    # dato reciente cuesta más de lo que el promedio más largo ahorra en ruido—,
    # pero no justifica un parámetro que dependa del horizonte.
    dias_nivel: int = 28
    # Corrección de nivel separada para días hábiles y no hábiles. Ver
    # `tipo_de_dia`: en Voltara son prácticamente dos campañas distintas, y medido
    # baja el error de fin de semana de 54,2% a 48,5%.
    nivel_por_tipo_de_dia: bool = True
    # Cuántos días mira la corrección de deriva del reparto, y cuánto puede
    # corregir. El tramo de asignación dice el régimen ("nos mandan el 49%") pero
    # el reparto real vibra alrededor de ese número: CV de 14,2% diario, de 0,298
    # a 0,441 entre percentil 5 y 95. Aun con la demanda del cliente perfecta,
    # ese vaivén deja 9,8% de error diario; seguirlo con 7 días lo baja a 8,2%.
    # En 0 queda apagado y el tramo se usa tal cual.
    # La deriva NO se extrapola más lejos que la ventana con la que se midió:
    # para un día a más de `reparto_deriva_dias` del corte se usa el tramo tal
    # cual. Está medido sobre 357 días: aplicándola a todo el horizonte, a 30
    # días de antelación EMPEORA (31,71% contra 31,42%). Tiene sentido —el share
    # de un día correlaciona 0,46 con el del anterior y 0,17 a 28 días—: pasada
    # la ventana ya no es información, es una apuesta.
    reparto_deriva_dias: int = 7
    reparto_deriva_tope: float = 0.25
    # Cuánta historia mira el factor de reparto de los días NO hábiles, y cuánto
    # puede corregir. Es un efecto DISTINTO de la deriva de arriba: la deriva
    # sigue el vaivén de los últimos días (que son casi todos hábiles) y esto
    # mide cuánto se corre el sábado, el domingo y el feriado respecto del hábil.
    #
    # NACE EN 0, O SEA APAGADO, Y ESO ES UN RESULTADO. El efecto EXISTE mirando el
    # agregado: sobre 464 días, el share de un sábado o un domingo es 6,6% menor
    # que el de los hábiles de la misma quincena y el de un feriado 12,8% mayor,
    # con una elasticidad al volumen del día de +0,140 (Voltara nos desborda cuando
    # tiene un día grande). Simulado sobre el agregado, aplicarlo bajaba el error
    # de fin de semana de 40,6% a 37,2%.
    #
    # Y MEDIDO SOBRE EL PRONÓSTICO DE VERDAD, EMPEORA:
    #
    #     464 días, antelación 7    todo   sábado   domingo   WAPE
    #     con el factor            25,9%    36,5%    46,6%   22,4%
    #     sin el factor            25,3%    34,6%    44,3%   22,2%
    #
    # POR QUÉ la simulación mentía: el reparto se aplica POR SKILL, y el fin de
    # semana tiene otra mezcla de colas (98,9% EMERGENCIAS contra 55% en un día
    # hábil). Como cada skill tiene su propio tramo —EMERGENCIAS 0,356, TOC 0,704,
    # RECLAMO-DAÑO 0,683—, el share agregado de un sábado ya baja solo por la
    # mezcla, sin que ningún tramo esté mal. O sea que lo que la medición agregada
    # leía como "efecto del día de la semana" era en buena parte el efecto de la
    # MEZCLA, y los tramos por skill ya lo estaban capturando. Aplicar el factor
    # encima lo cuenta dos veces.
    #
    # Queda implementado y en cero porque el mecanismo del desborde es real y está
    # medido: si algún día el reparto se vuelve a mover, esta es la perilla. Pero
    # se prende midiendo, y el número de arriba es el que hay que batir.
    reparto_tipo_dia_dias: int = 0
    reparto_tipo_dia_tope: float = 0.25

    # Combinación con el pronóstico que manda el cliente (dbo.Forecast). El peso
    # NO se elige a mano: sale de medir los dos contra lo real y se guarda acá
    # con la fecha en que se midió (ver servicio.calibrar_combinacion).
    combinar_cliente: bool = False
    combinar_cliente_peso_habil: Optional[float] = None
    combinar_cliente_peso_no_habil: Optional[float] = None
    combinar_cliente_medido_en: Optional[datetime] = None

    # Calendario propio de la campaña (migración 2026-09-22). En Voltara un feriado
    # se parece a un domingo (x1,11) y así queda. En Hidra NO: un feriado en día
    # hábil es x1,36 un domingo y x0,86 un sábado, y un PUENTE turístico es casi
    # un día hábil (x0,6 de su día; 10/07/2026: 2.139 llamadas contra 500
    # pronosticadas tratándolo como domingo). Ver `calendario_del_modelo`.
    feriado_como_sabado: bool = False
    puente_factor: Optional[float] = None    # None = el puente es un feriado más

    # Persistencia del desvío reciente (migración 2026-09-22). El residuo de un
    # día (log real / pronóstico) correlaciona 0,53 con el del día siguiente en
    # Hidra: los cortes y los eventos duran dos o tres días, y la corrección de
    # nivel de 28 días los ve tarde. Peso 0 = apagado, que es como queda Voltara
    # hasta medirlo ahí. Ver `factor_de_persistencia`.
    persistencia_peso_hoy: float = 0.0
    persistencia_peso_resto: float = 0.0
    persistencia_dias: int = 0
    # Desde qué hora el recálculo reescala lo que queda del día con lo que ya
    # entró. None = apagado. Ver `reescalar_intradia`.
    intradia_desde_hora: Optional[int] = None
    # Que la persistencia no arrastre el desvío de un día marcado como atípico
    # (evento que se saca del entrenamiento): se saltea como un feriado y se toma
    # el último día normal. Pensado para los paros del call center de Gasur, que
    # duran un día y nos mandan x3-x6. Nace apagado: en Hidra y Voltara un evento
    # (un corte, un temporal) suele durar varios días y ahí el arrastre sirve.
    persistencia_saltea_eventos: bool = False
    # Forma del día (cómo se reparte el total entre las medias horas) tomada de
    # los últimos N días del mismo tipo (hábil / sábado / domingo-feriado) en vez
    # de la mediana de `semanas_base` semanas. None = apagado. Ver
    # `_forma_reciente`. Es para Gasur, donde nos llega el DESBORDE de su call
    # center y la forma depende de a qué hora les falta gente a ellos.
    forma_dias: Optional[int] = None
    # Ancla al total del mes que manda el cliente (dbo.Forecast): a partir de
    # `ancla_mensual_desde_dias` días de antelación, lo que queda del mes se
    # escala hacia (total del cliente - lo ya entrado) con este peso. 0 = apagado.
    # Ver `factores_ancla_mensual`.
    ancla_mensual_peso: float = 0.0
    ancla_mensual_desde_dias: int = 7

    # De dónde salió cada parámetro. No cambia ningún cálculo: existe para que la
    # pantalla pueda decir "esto se midió sobre payroll el 3/9" en vez de mostrar
    # un 0,272 sin explicación, que es exactamente lo que no se entendía.
    paciencia_horizonte_seg: Optional[int] = None
    paciencia_origen: Optional[str] = None          # km | mle | manual
    shrinkage_ausentismo: Optional[float] = None
    shrinkage_capacitacion: Optional[float] = None
    shrinkage_origen: Optional[str] = None          # payroll | presencia | manual
    shrinkage_medido_en: Optional[datetime] = None

    # Segunda opinión del nivel diario con árboles de decisión. Ver
    # `planificador_nivel`: un GBDT con pérdida de Poisson que estima el mismo
    # cociente que el modelo de clima, promediado con él en logaritmo.
    #
    # NACE APAGADO aunque esté medido, por la regla de la casa: todo lo que
    # cambia el pronóstico se prende después de verlo en la pestaña Comparación
    # con los datos de la campaña. El peso por defecto es el medido sobre 464
    # días (2025-06-01 a 2026-09-06, antelación 7, demanda del cliente):
    #
    #     peso   WAPE ½h   MAPE diario   sesgo
    #     0,00    22,69%      19,71%     +0,3%
    #     0,25    21,81%      18,04%     +1,9%
    #     0,40    21,62%      17,54%     +2,6%   <- el default
    #     0,50    21,62%      17,35%     +3,1%
    #
    # Gana los seis trimestres del período en las dos métricas y los cuatro
    # tipos de día. En 0 el pronóstico queda idéntico al de hoy (hay test).
    nivel_gbdt: bool = False
    nivel_gbdt_peso: float = 0.40
    # Atenuar el factor de clima del DOMINGO por su elasticidad medida. Ver
    # `planificador_clima.elasticidad_por_tipo_de_dia`: un domingo es 98,9%
    # EMERGENCIAS, la cola más sensible al tiempo, así que recibe los factores
    # más grandes justo donde el modelo menos se sostiene, y la demanda no
    # acompaña (cumple el 58% de lo que se le pide, con correlación -0,34).
    #
    # NACE APAGADO porque sobre el año es neutro: lo que hace es arreglar el
    # régimen en el que estamos. MAPE diario del domingo, 464 días:
    #
    #     sin atenuar          año 29,65%   últimos 90 días 45,3%
    #     con elasticidad      año 29,06%   últimos 90 días 41,9%
    #
    # El alfa se mide solo sobre una ventana móvil, así que si el domingo vuelve
    # a responder al clima el factor vuelve a 1 sin que nadie toque nada.
    clima_elasticidad_tipo_dia: bool = False

    @property
    def intervalo_seg(self) -> int:
        return self.intervalo_min * 60

    def skills_del_pool(self, pool_id: int) -> List[SkillCfg]:
        return [s for s in self.skills if s.activo and s.pool_id == pool_id]

    def factor_disponibilidad(self, momento: datetime) -> float:
        """Qué proporción de los presentes está sobre la cola a esa hora.

        Gana la franja más específica: una definida para el día de semana
        concreto le gana a la genérica (dia_semana = 0). Sin ninguna, 1.0, que
        es el supuesto implícito de Erlang y el más optimista.

        LO QUE GUARDA LA TABLA ES LA DISPONIBILIDAD *REAL*, SIN EL BREAK
        ----------------------------------------------------------------
        El break se descuenta aparte, en su propio factor. Así, el día que la
        operación cambie los 5 minutos por hora por otro número, se toca esa sola
        perilla y la disponibilidad guardada sigue valiendo.

        La medición sale BRUTA —se estima invirtiendo el NDS observado contra los
        agentes presentes, y el que está en su break está presente y no está
        sobre la cola—, así que la conversión

            factor_real = factor_medido / (1 - break)

        se hace UNA vez, al aplicar la calibración, y no acá. Acá se lee lo
        guardado tal cual.
        """
        dia = momento.isoweekday()
        hora = momento.hour
        candidatas = [f for f in self.disponibilidad
                      if f.hora_desde <= hora < f.hora_hasta
                      and f.dia_semana in (0, dia)]
        if not candidatas:
            return 1.0
        especificas = [f for f in candidatas if f.dia_semana == dia]
        return (especificas or candidatas)[0].factor

    def shrinkage_del_dia(self, momento: datetime) -> float:
        """El shrinkage que corresponde a ese día.

        Tres escalones y no cuatro: hábil, no hábil (sábado y domingo) y feriado.
        Sábado y domingo no se separan porque medidos dan lo mismo que un hábil
        —lo que se ahorran en capacitación, que no se dicta el fin de semana, se
        les va en faltante dentro del turno— y partir un número por una
        diferencia que no supera su propio error estándar es fabricar ruido.

        Y a ese número del día se le suma el apartamiento de la media hora
        (`perfil_presencia`): el que entra a primera hora se conecta tarde y a la
        tarde se solapan los turnos, así que el mismo shrinkage de la jornada se
        descuenta más en unas medias horas que en otras.
        """
        dia = momento.date()
        if dia in self.feriados:
            base = (self.shrinkage_feriado if self.shrinkage_feriado is not None
                    else self.shrinkage)
        elif dia.isoweekday() >= 6 and self.shrinkage_no_habil is not None:
            base = self.shrinkage_no_habil
        else:
            base = self.shrinkage
        if not self.perfil_presencia:
            return base
        tipo = "no_habil" if (dia in self.feriados or dia.isoweekday() >= 6) else "habil"
        exceso = self.perfil_presencia.get((tipo, momento.hour * 60 + momento.minute), 0.0)
        # Piso en cero (una media hora con más conectados que gente con turno no
        # «devuelve» gente) y techo en 0,6 para que un dato raro no pida el doble.
        return min(max(base + exceso, 0.0), 0.6)

    def redondea_para_abajo(self, momento: datetime) -> bool:
        """Si la gente a citar de ese intervalo se redondea para abajo."""
        desde, hasta = self.redondeo_abajo_desde, self.redondeo_abajo_hasta
        if desde is None or hasta is None or desde == hasta:
            return False
        hora = momento.hour
        if desde < hasta:
            return desde <= hora < hasta
        return hora >= desde or hora < hasta

    def factor_break(self) -> float:
        """Qué proporción del turno se va en el break. 5 min/hora = 0,0833."""
        return min(max(self.break_min_por_hora, 0.0), 30.0) / 60.0


# ------------------------------------------------------------- días atípicos

@dataclass
class Atipico:
    dia: date
    llamadas: float
    esperado: float
    factor: float        # 2.5 = dos veces y media lo normal
    z: float


def detectar_atipicos(serie_diaria: Dict[date, float],
                      feriados: Optional[Sequence[date]] = None,
                      umbral_z: float = 3.5,
                      minimo_factor: float = 1.35) -> List[Atipico]:
    """Días cuyo volumen no se explica por el día de semana.

    Compara cada día contra la mediana de su mismo día de semana y mide la
    desviación en unidades de MAD (desviación absoluta mediana) en vez de sigmas:
    la media y el desvío estándar los arrastran los propios outliers que estamos
    buscando, y terminaríamos comparando los picos contra un promedio que los
    picos inflaron.

    Los feriados se excluyen: son atípicos, sí, pero conocidos y ya modelados por
    el calendario, así que no tienen por qué ensuciar la lista de eventos.

    Pide las dos condiciones a la vez —desviación estadística Y un salto de
    tamaño relevante— porque en las colas chicas el MAD es diminuto y cualquier
    variación normal da z gigante.
    """
    feriados = set(feriados or ())
    por_dia_semana: Dict[int, List[float]] = {}
    for dia, valor in serie_diaria.items():
        if dia in feriados:
            continue
        por_dia_semana.setdefault(dia.isoweekday(), []).append(valor)

    referencia: Dict[int, Tuple[float, float]] = {}
    for dow, valores in por_dia_semana.items():
        if len(valores) < MIN_OBSERVACIONES:
            continue
        mediana = statistics.median(valores)
        mad = statistics.median([abs(v - mediana) for v in valores])
        # 1.4826 lleva el MAD a la escala del desvío estándar de una normal.
        referencia[dow] = (mediana, mad * 1.4826)

    atipicos: List[Atipico] = []
    for dia, valor in sorted(serie_diaria.items()):
        if dia in feriados:
            continue
        ref = referencia.get(dia.isoweekday())
        if not ref:
            continue
        mediana, escala = ref
        if mediana <= 0:
            continue
        factor = valor / mediana
        if escala <= 0:
            # Día de semana sin dispersión: solo el salto de tamaño puede marcarlo.
            z = math.inf if abs(factor - 1) >= minimo_factor - 1 else 0.0
        else:
            z = abs(valor - mediana) / escala
        if z >= umbral_z and (factor >= minimo_factor or factor <= 1 / minimo_factor):
            atipicos.append(Atipico(dia=dia, llamadas=valor, esperado=mediana,
                                    factor=round(factor, 3),
                                    z=round(z, 2) if math.isfinite(z) else 999.0))
    return atipicos


# ------------------------------------------------------------- línea de base

def baseline_estacional(historico: Dict[datetime, float],
                        dias_a_pronosticar: Sequence[date],
                        intervalo_min: int = 30,
                        feriados: Optional[Sequence[date]] = None,
                        excluir_dias: Optional[Sequence[date]] = None,
                        hoy: Optional[date] = None,
                        semanas_base: Optional[int] = None,
                        dias_nivel: Optional[int] = None,
                        factor_diario: Optional[Dict[date, float]] = None,
                        nivel_por_tipo_de_dia: bool = False,
                        nivel_diario: Optional[Dict[date, float]] = None,
                        peso_nivel: float = 0.0,
                        dias_observados: Optional[Iterable[date]] = None,
                        puentes: Optional[Iterable[date]] = None,
                        feriado_como: str = "domingo",
                        puente_factor: Optional[float] = None,
                        persistencia: Optional[Tuple[float, float, int]] = None,
                        persistencia_saltea_eventos: bool = False,
                        forma_dias: Optional[int] = None
                        ) -> Dict[datetime, float]:
    """Línea de base estacional por (día de semana, hora del día).

    Para cada intervalo futuro toma la mediana de ese mismo día de semana a esa
    misma hora en las últimas `SEMANAS_BASE` semanas, y después corrige por el
    nivel de las últimas dos semanas. Esa corrección es la que le falta a lo que
    hay hoy en producción: `dbo.Forecast` no tiene forma de enterarse de que el
    volumen bajó, y por eso en la última semana de agosto de 2026 sobreestimaba
    entre 28% y 73% todos los días.

    Los feriados se pronostican con el perfil de los domingos, que es lo que
    empíricamente se les parece. `excluir_dias` saca del entrenamiento los días
    marcados como atípicos: un corte masivo no dice nada sobre un martes normal.

    `dias_observados` son los días en los que la fuente TIENE datos (de cualquier
    skill). Hace falta porque la serie sólo trae las medias horas con llamadas: sin
    saber qué días existieron, una media hora en cero no se distingue de un día sin
    descarga, y la mediana se calcula sólo sobre los días con llamadas. Ver
    `_rellenar_ceros`.

    Es explícitamente una LÍNEA DE BASE, no el modelo final: no usa clima, ni
    feriados puente, ni eventos futuros. Sirve para que el planificador funcione
    hoy y para tener contra qué comparar al modelo de machine learning.

    `puentes`, `feriado_como` y `puente_factor` son el calendario de la campaña
    (ver `calendario_del_modelo`); con los valores por defecto todo feriado es un
    domingo, como siempre. `persistencia` = (peso de hoy, peso del resto, días):
    corrige cada día por el desvío del último día cerrado (ver
    `factor_de_persistencia`); None o pesos en cero, apagada.
    `persistencia_saltea_eventos`: el día cerrado que está en `excluir_dias` no
    se arrastra (ver `CampanaCfg.persistencia_saltea_eventos`). `forma_dias`:
    reparte el total de cada día con la forma reciente (ver `_forma_reciente`).
    """
    if persistencia and (persistencia[0] or persistencia[1]) and persistencia[2] > 0:
        return _con_persistencia(
            historico, dias_a_pronosticar, persistencia,
            dict(intervalo_min=intervalo_min, feriados=feriados,
                 excluir_dias=excluir_dias, hoy=hoy, semanas_base=semanas_base,
                 dias_nivel=dias_nivel, factor_diario=factor_diario,
                 nivel_por_tipo_de_dia=nivel_por_tipo_de_dia,
                 nivel_diario=nivel_diario, peso_nivel=peso_nivel,
                 dias_observados=dias_observados, puentes=puentes,
                 feriado_como=feriado_como, puente_factor=puente_factor,
                 forma_dias=forma_dias),
            saltea_eventos=persistencia_saltea_eventos)

    feriados, dow_forzado, factor_puentes = calendario_del_modelo(
        feriados or (), puentes or (), feriado_como, puente_factor)
    if factor_puentes:
        factor_diario = {d: (factor_diario or {}).get(d, 1.0) * factor_puentes.get(d, 1.0)
                         for d in set(factor_diario or {}) | set(factor_puentes)}
    excluidos = set(excluir_dias or ())
    hoy = hoy or date.today()
    paso = timedelta(minutes=intervalo_min)
    semanas_base = semanas_base or SEMANAS_BASE
    desde = hoy - timedelta(weeks=semanas_base)

    # (día de semana efectivo, minuto del día) -> valores observados
    muestras: Dict[Tuple[int, int], List[float]] = {}
    for momento, valor in historico.items():
        dia = momento.date()
        if dia < desde or dia >= hoy or dia in excluidos:
            continue
        # La historia va con su día de semana de siempre: el calendario propio
        # decide con qué perfil se PRONOSTICA un feriado, no con qué perfil se
        # mezcla. Metiendo los feriados en el perfil del sábado, el sábado de Hidra
        # empeoraba de 20,1% a 22,0% (un feriado hábil trae x1,16 un sábado).
        clave = (_dow_efectivo(dia, feriados), momento.hour * 60 + momento.minute)
        muestras.setdefault(clave, []).append(valor)

    observados = _dias_observados_del_skill(historico, dias_observados, desde, hoy,
                                            excluidos)
    if observados is not None:
        _rellenar_ceros(muestras, observados, feriados)

    perfil = {clave: statistics.median(v) for clave, v in muestras.items()
              if len(v) >= MIN_OBSERVACIONES}
    if not perfil:
        return {}

    # El factor de clima va ANTES de la corrección de nivel y ésta se calcula
    # sobre lo que el clima NO explicó. Al revés, las dos estarían explicando lo
    # mismo dos veces: la corrección mira los últimos días, que es justo donde
    # vivió la ola de frío o el temporal que el factor ya está representando.
    clima = factor_diario or {}
    correccion = _correccion_de_nivel(historico, perfil, feriados, excluidos, hoy,
                                      dias_nivel=dias_nivel, factor_diario=clima,
                                      por_tipo_de_dia=nivel_por_tipo_de_dia,
                                      dias_observados=observados)

    # Nivel del día que predice el perfil crudo, por día de semana. Hace falta
    # para poder mezclar con la segunda opinión de `planificador_nivel`, que
    # habla en llamadas por día y no en factores.
    nivel_del_perfil: Dict[int, float] = {}
    for (d, _), v in perfil.items():
        nivel_del_perfil[d] = nivel_del_perfil.get(d, 0.0) + v

    pronostico: Dict[datetime, float] = {}
    for dia in dias_a_pronosticar:
        dow = _dow_efectivo(dia, feriados, dow_forzado)
        factor = (correccion[tipo_de_dia(dia, feriados)]
                  if isinstance(correccion, dict) else correccion)
        ajuste = factor * clima.get(dia, 1.0)
        ajuste = _mezclar_nivel(ajuste, nivel_del_perfil.get(dow),
                                (nivel_diario or {}).get(dia), peso_nivel)
        momento = datetime.combine(dia, time(0, 0))
        fin = momento + timedelta(days=1)
        while momento < fin:
            base = perfil.get((dow, momento.hour * 60 + momento.minute))
            if base is not None:
                pronostico[momento] = max(0.0, base * ajuste)
            momento += paso
    if forma_dias:
        pronostico = _forma_reciente(pronostico, historico, forma_dias, hoy, feriados,
                                     dow_forzado, excluidos, observados, paso)
    return pronostico


# ------------------------------------------------------ forma reciente del día

# Días del mismo tipo que hacen falta en la ventana para confiar en la forma
# reciente; con menos, el día se queda con la del perfil.
MIN_DIAS_FORMA = 3


def _tipo_de_forma(dow: int) -> str:
    """hábil / sábado / domingo, sobre el día de semana EFECTIVO (el feriado ya
    viene como 7, o como 6 si la campaña lo pronostica como sábado)."""
    return "sabado" if dow == 6 else "domingo" if dow == 7 else "habil"


def _forma_reciente(pronostico: Dict[datetime, float],
                    historico: Dict[datetime, float], dias: int, hoy: date,
                    feriados: set, dow_forzado: Dict[date, int], excluidos: set,
                    observados: Optional[set], paso: timedelta
                    ) -> Dict[datetime, float]:
    """Reparte el total pronosticado de cada día con la forma de los últimos
    `dias` días del mismo tipo. El TOTAL del día no cambia: sólo cómo se reparte.

    POR QUÉ (Gasur, 2026-09-24). A Gasur le llega el desborde del call center
    del cliente: la forma del día depende de a qué hora les falta gente a ellos
    (breaks, cambios de turno, horario), no de cuándo llama la gente, y cambia
    más rápido que lo que sigue una mediana de 26 semanas. Además la línea pasó a
    atender hasta las 24 en marzo de 2026 y la mediana larga tarda meses en ver
    esas horas. Medido con el total del día perfecto (15/07-20/09/2026), error
    por media hora: mediana de 26 semanas por día de semana ~32%, suma de los
    últimos 28 días del mismo tipo 27,2%; el piso por azar es 12,5%.

    Es la SUMA de las llamadas de la ventana por media hora sobre el total, y no
    la media de las proporciones de cada día: pondera por volumen, así un domingo
    de 200 llamadas no pesa lo mismo que un lunes de 1.800. Los días sin datos o
    marcados como atípicos no entran; un día observado sin llamadas en una media
    hora cuenta como cero porque la suma ya lo hace.
    """
    desde = hoy - timedelta(days=dias)
    sumas: Dict[str, Dict[int, float]] = {}
    dias_por_tipo: Dict[str, set] = {}
    for momento, valor in historico.items():
        dia = momento.date()
        if dia < desde or dia >= hoy or dia in excluidos:
            continue
        if observados is not None and dia not in observados:
            continue
        tipo = _tipo_de_forma(_dow_efectivo(dia, feriados))
        minuto = momento.hour * 60 + momento.minute
        sumas.setdefault(tipo, {})
        sumas[tipo][minuto] = sumas[tipo].get(minuto, 0.0) + valor
        dias_por_tipo.setdefault(tipo, set()).add(dia)
    formas: Dict[str, Dict[int, float]] = {}
    for tipo, por_minuto in sumas.items():
        total = sum(por_minuto.values())
        if total > 0 and len(dias_por_tipo.get(tipo, ())) >= MIN_DIAS_FORMA:
            formas[tipo] = {m: v / total for m, v in por_minuto.items()}

    totales: Dict[date, float] = {}
    for momento, valor in pronostico.items():
        totales[momento.date()] = totales.get(momento.date(), 0.0) + valor
    salida: Dict[datetime, float] = {}
    for dia, total in totales.items():
        forma = formas.get(_tipo_de_forma(_dow_efectivo(dia, feriados, dow_forzado)))
        if not forma or total <= 0:
            salida.update({m: v for m, v in pronostico.items() if m.date() == dia})
            continue
        inicio = datetime.combine(dia, time(0, 0))
        for minuto, parte in forma.items():
            salida[inicio + timedelta(minutes=minuto)] = total * parte
    return salida


# -------------------------------------------------- calendario de la campaña

# Los feriados que aun en Hidra se comportan como un domingo: Navidad (225
# llamadas el 25/12/2025, x0,63 un domingo) y Año Nuevo (x0,53 y x0,95). El resto
# de los feriados en día hábil de Hidra andan en x1,36 un domingo.
FERIADOS_COMO_DOMINGO = {(12, 25), (1, 1)}


def calendario_del_modelo(feriados: Iterable[date], puentes: Iterable[date],
                          feriado_como: str = "domingo",
                          puente_factor: Optional[float] = None
                          ) -> Tuple[set, Dict[date, int], Dict[date, float]]:
    """(feriados para el modelo, día de semana forzado, factor de los puentes).

    Con los valores por defecto devuelve los feriados tal cual y nada más: todo
    feriado es un domingo, que es lo medido en Voltara.

    `feriado_como='sabado'`: el feriado de lunes a viernes se pronostica con el
    perfil del sábado (salvo Navidad y Año Nuevo, que siguen siendo domingo).
    Sigue siendo día NO hábil para la corrección de nivel, y en la historia sigue
    contando como feriado: el perfil del sábado se arma sólo con sábados.

    `puente_factor`: el puente turístico deja de ser feriado —usa el perfil de su
    propio día de semana y la corrección de los hábiles— y se multiplica por el
    factor. Va por el mismo canal que el factor de clima, también para los días
    pasados: así la corrección de nivel no lee un puente de la historia como una
    caída del volumen. Medido en Hidra sobre 5 puentes: x0,6 de su día hábil.
    """
    feriados = set(feriados)
    puentes = set(puentes)
    factor: Dict[date, float] = {}
    if puente_factor is not None and puentes:
        feriados -= puentes
        factor = {d: float(puente_factor) for d in puentes}
    forzado: Dict[date, int] = {}
    if feriado_como == "sabado":
        # Sólo el feriado que cae de lunes a viernes: el de un domingo sigue
        # siendo domingo (12/10/2025: 247 llamadas; como sábado se pronosticaban
        # 536) y el de un sábado ya es sábado.
        forzado = {d: 6 for d in feriados
                   if d.weekday() < 5 and (d.month, d.day) not in FERIADOS_COMO_DOMINGO}
    return feriados, forzado, factor


# ------------------------------------------------------ persistencia del desvío

# Prior de la persistencia, en llamadas: un día de 10 llamadas contra 5
# pronosticadas no es un desvío de x2 que haya que arrastrar.
PRIOR_PERSISTENCIA = 20.0
# Tope del residuo que se arrastra (en log): x2 para arriba, x0,5 para abajo.
TOPE_PERSISTENCIA = 0.7
# Cuántos feriados seguidos se saltean buscando el último día normal (un fin de
# semana largo de cuatro días).
MAX_DIAS_ATRAS_PERSISTENCIA = 4


def factor_de_persistencia(residuo: float, horizonte: int, peso_hoy: float,
                           peso_resto: float, dias: int) -> float:
    """Cuánto corregir un día por el desvío del último día cerrado.

    `residuo` = log(real / pronóstico) de ayer; `horizonte` = días desde hoy (0 =
    hoy). Hoy pesa `peso_hoy` y los `dias - 1` siguientes `peso_resto`; más allá,
    nada. Son dos pesos y no un decaimiento porque así lo dicen los datos de Hidra
    (regresión del residuo de un día sobre el de k días antes, 347 días):

        k = 1: 0,53    k = 2: 0,36    k = 3: 0,36    k = 4: 0,37    k = 7: 0,30

    El salto del primer día es el evento que dura dos o tres días (cortes
    programados, feriados largos); lo que queda plano es el nivel que se está
    moviendo y la corrección de 28 días todavía no alcanzó.
    """
    if horizonte < 0 or horizonte >= dias:
        return 1.0
    r = max(-TOPE_PERSISTENCIA, min(TOPE_PERSISTENCIA, residuo))
    return math.exp((peso_hoy if horizonte == 0 else peso_resto) * r)


def _con_persistencia(historico: Dict[datetime, float],
                      dias_a_pronosticar: Sequence[date],
                      persistencia: Tuple[float, float, int],
                      kw: dict, saltea_eventos: bool = False) -> Dict[datetime, float]:
    """La línea de base corregida por el desvío del último día cerrado.

    El desvío se mide contra lo que el MISMO modelo habría dicho de ayer con lo
    que sabía hasta anteayer, no contra el plan guardado: así el backtest y el
    recálculo lo miden igual y no depende de a qué hora corrió cada corrida.
    """
    base = baseline_estacional(historico, dias_a_pronosticar, **kw)
    hoy = kw.get("hoy") or date.today()
    # EL ÚLTIMO DÍA CERRADO QUE NO SEA FERIADO. El desvío de un feriado es del
    # calendario (el feriado se pronostica peor), no del nivel: arrastrarlo al día
    # siguiente lo hundía —en Hidra el día hábil después de un feriado salía
    # +19% por debajo—. Se saltea y se toma el anterior, y como ese dato es más
    # viejo, el día de hoy recibe el peso de "los días siguientes".
    no_calendario = set(kw.get("feriados") or ()) | set(kw.get("puentes") or ())
    # Con `saltea_eventos`, el día atípico tampoco se arrastra: un paro de un día
    # no dice nada del día siguiente (ver `CampanaCfg.persistencia_saltea_eventos`).
    if saltea_eventos:
        no_calendario |= set(kw.get("excluir_dias") or ())
    ayer = hoy - timedelta(days=1)
    atraso = 0
    while ayer in no_calendario and atraso < MAX_DIAS_ATRAS_PERSISTENCIA:
        ayer -= timedelta(days=1)
        atraso += 1
    if ayer in no_calendario:
        return base
    real = sum(v for m, v in historico.items() if m.date() == ayer)
    observados = kw.get("dias_observados")
    if not real and (observados is None or ayer not in set(observados)):
        return base            # ayer no hay datos: no se sabe nada del desvío
    anterior = baseline_estacional(historico, [ayer], **dict(kw, hoy=ayer))
    pron = sum(anterior.values())
    if pron <= 0:
        return base
    residuo = math.log((real + PRIOR_PERSISTENCIA) / (pron + PRIOR_PERSISTENCIA))
    peso_hoy, peso_resto, dias = persistencia
    salida: Dict[datetime, float] = {}
    for momento, valor in base.items():
        f = factor_de_persistencia(residuo, (momento.date() - hoy).days + atraso,
                                   peso_hoy, peso_resto, dias)
        salida[momento] = valor * f
    return salida


# ------------------------------------------------ ancla al total del mes del cliente

# Con menos de esto por delante no se ancla: dos o tres días cargan con todo el
# desvío del mes y el factor se dispara.
MIN_DIAS_RESTO_ANCLA = 7
TOPE_ANCLA = (0.6, 1.8)


def factores_ancla_mensual(nuestro_dia: Dict[date, float],
                           real_del_mes: Dict[Tuple[int, int], float],
                           cliente_mes: Dict[Tuple[int, int], float],
                           hoy: date, peso: float, desde_dias: int
                           ) -> Dict[date, float]:
    """Factor por día que lleva lo que queda del mes hacia el total del cliente.

    POR QUÉ (Gasur, 2026-09-24). El cliente manda cada mes cuántas llamadas nos
    va a derivar —es el mínimo que paga— y lo acierta: de oct-2025 a ago-2026 el
    error de ese total fue 8,1% contra la realidad, porque el desborde lo decide
    su propia dotación. Nuestro pronóstico a 7 días, sumado por mes, erraba 21,6%
    y se quedaba corto en la rampa del invierno. Medido sobre un año, total
    diario a 7 días: MAPE 45,1% -> 40,6% y sesgo -13,5% -> -4,4% con peso 0,75.
    A 0 y 1 día EMPEORA (32,9% -> 36,1%): ahí manda lo que pasó ayer, y por eso
    sólo se aplica desde `desde_dias` de antelación.

    factor = ((total del cliente - lo ya entrado en el mes) / nuestro pronóstico
    del resto del mes) ^ peso, acotado a TOPE_ANCLA. Sólo para los meses cuyo
    resto está entero en el pronóstico y tiene al menos MIN_DIAS_RESTO_ANCLA días.
    """
    if peso <= 0 or not nuestro_dia:
        return {}
    salida: Dict[date, float] = {}
    for (anio, mes), total_cliente in cliente_mes.items():
        if not total_cliente:
            continue
        inicio = date(anio, mes, 1)
        fin = (inicio + timedelta(days=32)).replace(day=1)
        desde = max(inicio, hoy)
        resto = [desde + timedelta(days=i) for i in range((fin - desde).days)]
        if len(resto) < MIN_DIAS_RESTO_ANCLA or any(d not in nuestro_dia for d in resto):
            continue
        nuestro = sum(nuestro_dia[d] for d in resto)
        if nuestro <= 0:
            continue
        pendiente = total_cliente - real_del_mes.get((anio, mes), 0.0)
        f = max(TOPE_ANCLA[0], min(TOPE_ANCLA[1], pendiente / nuestro))
        for d in resto:
            if (d - hoy).days >= desde_dias:
                salida[d] = f ** peso
    return salida


# --------------------------------------------------------------- intradía

# Prior del reescalado intradía, en llamadas pronosticadas: con poco acumulado
# (la mañana de un domingo) el cociente se acerca a 1 en vez de saltar.
PRIOR_INTRADIA = 20.0
TOPE_INTRADIA = (0.5, 3.0)


def reescalar_intradia(pronostico: Dict[datetime, float],
                       real: Dict[datetime, float], ahora: datetime,
                       intervalo_min: int = 30
                       ) -> Tuple[Optional[float], Dict[datetime, float]]:
    """Reescala lo que queda del día por cómo viene lo que ya entró.

    (factor, momento -> llamadas para las medias horas que todavía no cerraron).
    Mide sólo los intervalos CERRADOS de hoy y descarta el último con datos, que
    puede venir a medias (mismo criterio que `seguimiento_intradia`). La forma
    del día se conserva: se corre el nivel.

    Medido en Hidra sobre un año, encima del plan de la mañana con persistencia
    (error del resto del día): desde las 12, 22,5% -> 20,9%; desde las 14,
    24,0% -> 21,4%. A las 10 empeora (21,2% -> 22,8%): con pocas horas de mañana
    el acumulado es ruido, y la persistencia ya se llevó buena parte del desvío.
    """
    paso = timedelta(minutes=intervalo_min)
    hoy = ahora.date()
    cerrados = sorted(m for m in real if m.date() == hoy and m + paso <= ahora)
    if cerrados:
        cerrados = cerrados[:-1]
    pron_c = sum(pronostico.get(m, 0.0) for m in cerrados)
    real_c = sum(real.get(m, 0.0) for m in cerrados)
    if pron_c <= 0:
        return None, {}
    g = (real_c + PRIOR_INTRADIA) / (pron_c + PRIOR_INTRADIA)
    g = max(TOPE_INTRADIA[0], min(TOPE_INTRADIA[1], g))
    return g, {m: v * g for m, v in pronostico.items()
               if m.date() == hoy and m + paso > ahora}


# LAS MEDIAS HORAS SIN LLAMADAS TAMBIÉN SON DATO (bug encontrado el 2026-09-14)
# ---------------------------------------------------------------------------
# La serie viene de un GROUP BY sobre llamadas, así que una media hora sin llamadas
# no es un cero: no existe. La mediana del perfil se tomaba sólo sobre los días que
# SÍ tuvieron llamadas, y en las colas ralas eso la infla sin límite. El caso que
# lo destapó, perfil del domingo de Voltara a 52 semanas (dow efectivo 7 = 49
# domingos + 19 feriados):
#
#     skill           perfil (sin ceros)   con ceros   real de un domingo
#     COMERCIAL             998                0              0
#     RECLAMO-DANO           62                0              0
#     ELECTRODEP.            58                3             27
#     EMERG-EMPRESARIAL      44                0              8
#     EMERGENCIAS         3.260            3.258          3.522
#
# COMERCIAL no atiende los domingos, pero los feriados que caen en día hábil sí
# tienen llamadas comerciales, y como eran las únicas muestras la mediana salía de
# ellos. ~1.200 llamadas fantasma sobre un domingo de ~3.300: es el +36% de sesgo
# de los domingos medido sobre un año a 1-3 días.
#
# Se rellena con ceros SÓLO desde el primer día con datos del skill dentro de la
# ventana: una cola nueva no tiene que heredar ceros de antes de existir.


def _dias_observados_del_skill(historico: Dict[datetime, float],
                               dias_observados: Optional[Iterable[date]],
                               desde: date, hoy: date, excluidos: set
                               ) -> Optional[set]:
    """Días de la ventana en los que el skill pudo tener llamadas. None = no se sabe."""
    if dias_observados is None:
        return None
    propios = [m.date() for m in historico if desde <= m.date() < hoy]
    if not propios:
        return set()
    primero = min(propios)
    return {d for d in dias_observados
            if primero <= d < hoy and d >= desde and d not in excluidos}


def _rellenar_ceros(muestras: Dict[Tuple[int, int], List[float]],
                    observados: set, feriados: set) -> None:
    """Completa cada (día de semana, media hora) con ceros hasta los días observados."""
    por_dow: Dict[int, int] = {}
    for d in observados:
        dow = _dow_efectivo(d, feriados)
        por_dow[dow] = por_dow.get(dow, 0) + 1
    for (dow, _), valores in muestras.items():
        faltan = por_dow.get(dow, 0) - len(valores)
        if faltan > 0:
            valores.extend([0.0] * faltan)


def _mezclar_nivel(ajuste: float, nivel_del_perfil: Optional[float],
                   nivel_modelo: Optional[float], peso: float) -> float:
    """Promedia en logaritmo el nivel del día de las dos fuentes.

    `ajuste` es lo que la línea de base le aplica al perfil (corrección de nivel
    por el factor de clima), así que `perfil x ajuste` es el nivel del día según
    el modelo de siempre. `nivel_modelo` es el mismo número según el GBDT de
    `planificador_nivel`. Se promedian en logaritmo —no en llamadas— porque el
    error del pronóstico es multiplicativo: equivocarse en 500 llamadas sobre
    1.000 no es lo mismo que sobre 10.000.

    La media geométrica queda por debajo de la aritmética, y eso es deliberado:
    con el peso medido (0,40) el sesgo del año pasa de +0,3% a +2,6%, y aun así
    el error baja de 22,69% a 21,62% de WAPE y de 19,71% a 17,54% de MAPE
    diario. Se probó sacarle el sesgo con una calibración de escala medida sobre
    los días ya cerrados y EMPEORA en todas las ventanas (60, 90 y 180 días),
    incluso aplicada al pronóstico de producción solo: 22,69% -> 22,92%.

    Peso 0 devuelve `ajuste` intacto, byte por byte. Es la garantía de que
    apagar la mezcla deja el pronóstico exactamente como estaba.
    """
    if not peso or nivel_modelo is None or nivel_modelo <= 0:
        return ajuste
    if not nivel_del_perfil or nivel_del_perfil <= 0 or ajuste <= 0:
        return ajuste
    nivel_base = nivel_del_perfil * ajuste
    mezclado = math.exp((1 - peso) * math.log(nivel_base)
                        + peso * math.log(nivel_modelo))
    return mezclado / nivel_del_perfil


def _dow_efectivo(dia: date, feriados: set,
                  dow_forzado: Optional[Dict[date, int]] = None) -> int:
    """Día de semana a efectos del perfil. Un feriado se comporta como domingo.

    MEDIDO, y es lo mejor que hay hoy: sobre 20 feriados, la demanda total del
    cliente fue la mediana de los domingos recientes por 1,11 (contra 0,39 si se
    los tomara como día hábil). O sea que el domingo es la referencia correcta.

    PERO ES EL PUNTO MÁS FLOJO DEL PRONÓSTICO, y conviene saberlo: la dispersión
    es enorme —de x0,64 (1 de mayo de 2026) a x1,96 (1 de enero de 2026)—, y
    encima el reparto de un feriado es ~13% MÁS alto que el de un hábil mientras
    que el de un fin de semana es ~7% más bajo, así que la corrección por tipo de
    día los empuja para el lado contrario. Resultado medido sobre nuestras
    llamadas: los feriados grandes se subpronostican fuerte (el 16 de junio de
    2025 entraron 3.775 y el modelo dijo 1.356). Separarlos del fin de semana en
    la corrección del reparto se probó y EMPEORA (33,6% contra 31,0%): con trece
    feriados en una ventana de un año, el ruido de estimarles un coeficiente
    propio es mayor que el sesgo que corrige. Hace falta más historia.

    `dow_forzado` es el calendario propio de la campaña (`calendario_del_modelo`):
    en Hidra un feriado se pronostica como sábado."""
    if dow_forzado and dia in dow_forzado:
        return dow_forzado[dia]
    return 7 if dia in feriados else dia.isoweekday()


def tipo_de_dia(dia: date, feriados: set) -> str:
    """Hábil o no hábil.

    Existe porque los dos se comportan como campañas distintas: un día hábil de
    Voltara es 55% EMERGENCIAS y 40% COMERCIAL, y un fin de semana es 98,9%
    EMERGENCIAS. O sea que COMERCIAL —que es estable y no depende del clima—
    ancla el nivel de los días hábiles y no ancla nada el sábado."""
    return "no_habil" if (dia in feriados or dia.isoweekday() >= 6) else "habil"


def _correccion_de_nivel(historico: Dict[datetime, float],
                         perfil: Dict[Tuple[int, int], float],
                         feriados: set, excluidos: set, hoy: date,
                         dias_nivel: Optional[int] = None,
                         factor_diario: Optional[Dict[date, float]] = None,
                         por_tipo_de_dia: bool = False,
                         dias_observados: Optional[set] = None):
    """Cuánto se corrió el volumen reciente respecto del perfil, acotado.

    Es el cociente entre lo que realmente pasó en las últimas dos semanas y lo
    que el perfil hubiera predicho para esos mismos intervalos. Se acota a
    ±TOPE_CORRECCION_NIVEL para que un evento de tres días no arrastre el
    pronóstico del mes entero.
    """
    # Se probó estirar la ventana para los días no hábiles (que en 28 días son
    # sólo 8 muestras contra 20): medido, EMPEORA — 48,5% de error de fin de
    # semana con la misma ventana contra 51,5% con el triple. La ventana larga
    # deja de seguir el nivel reciente, que es justamente para lo que está.
    desde = hoy - timedelta(days=dias_nivel or DIAS_NIVEL_RECIENTE)
    # Se acumula por DÍA y no sólo por tipo, para poder cerrar el cociente de las
    # dos maneras: sumando todo (que es lo que se usa) o con la mediana de los
    # cocientes diarios. Ver NIVEL_ROBUSTO.
    acum: Dict[str, Dict[date, List[float]]] = {}
    for momento, valor in historico.items():
        dia = momento.date()
        if dia < desde or dia >= hoy or dia in excluidos:
            continue
        base = perfil.get((_dow_efectivo(dia, feriados),
                           momento.hour * 60 + momento.minute))
        if base is None:
            continue
        clave = tipo_de_dia(dia, feriados) if por_tipo_de_dia else "todo"
        par = acum.setdefault(clave, {}).setdefault(dia, [0.0, 0.0])
        par[0] += valor
        par[1] += base * (factor_diario or {}).get(dia, 1.0)

    # Con los días observados, lo esperado de cada día es el perfil ENTERO del día
    # y no sólo el de las medias horas que tuvieron llamadas, y un día observado
    # sin ninguna llamada del skill cuenta como cero (ver `_rellenar_ceros`).
    if dias_observados is not None:
        nivel_por_dow: Dict[int, float] = {}
        for (d, _), v in perfil.items():
            nivel_por_dow[d] = nivel_por_dow.get(d, 0.0) + v
        for dia in dias_observados:
            if dia < desde or dia >= hoy or dia in excluidos:
                continue
            clave = tipo_de_dia(dia, feriados) if por_tipo_de_dia else "todo"
            par = acum.setdefault(clave, {}).setdefault(dia, [0.0, 0.0])
            par[1] = (nivel_por_dow.get(_dow_efectivo(dia, feriados), 0.0)
                      * (factor_diario or {}).get(dia, 1.0))

    def _acotado(v):
        return min(1 + TOPE_CORRECCION_NIVEL, max(1 - TOPE_CORRECCION_NIVEL, v))

    def _cociente(dias: Dict[date, List[float]], respaldo=1.0):
        if not dias:
            return respaldo
        usables = [(r, e) for r, e in dias.values() if e > 0]
        if not usables:
            return respaldo
        if NIVEL_ROBUSTO == "mediana":
            return _acotado(statistics.median(r / e for r, e in usables))
        if NIVEL_ROBUSTO == "recortada" and len(usables) >= 5:
            # Se sacan el día más alto y el más bajo del período y se suma el
            # resto. Mantiene la ponderación por volumen (que es la que le sirve
            # a los feriados) y saca el día suelto que se lleva la ventana puesta.
            usables.sort(key=lambda p: p[0] / p[1])
            usables = usables[1:-1]
        real = sum(r for r, _ in usables)
        esperado = sum(e for _, e in usables)
        return _acotado(real / esperado) if esperado > 0 else respaldo

    if not por_tipo_de_dia:
        return _cociente(acum.get("todo", {}))

    # Con una ventana corta puede no haber ningún fin de semana; el respaldo es
    # la corrección global, que es lo que se usaba antes.
    todos: Dict[date, List[float]] = {}
    for por_dia in acum.values():
        todos.update(por_dia)
    global_ = _cociente(todos)
    return {t: _cociente(acum.get(t, {}), respaldo=global_)
            for t in ("habil", "no_habil")}


# ---------------------------------------------------------- dimensionamiento

@dataclass
class DemandaSkill:
    """Lo que se espera de un skill en un intervalo."""
    skill_id: int
    llamadas: float
    tmo_seg: float


@dataclass
class RequerimientoPool:
    pool_id: int
    momento: datetime
    llamadas: float
    tmo_seg: float
    trafico: float
    operadores_en_linea: int       # efectivos sobre la cola (lo que da Erlang)
    operadores_presentes: int      # los que tienen que estar en el turno
    operadores_a_planificar: int   # los que hay que citar, con shrinkage de nómina
    nds_contractual: float
    nds_sobre_atendidas: float
    nds_sobre_entrantes: float
    abandono: float
    ocupacion: float
    asa_seg: float
    # Las dos lecturas del nivel de atención, a propósito. La de Erlang B es la de
    # la planilla vieja y es pesimista (supone que la llamada sin operador libre
    # se pierde, cuando en realidad espera); la de Erlang A usa la paciencia
    # medida. Mostrarlas juntas evita elegir una en silencio.
    nivel_atencion_b: float
    motivo: str
    disponibilidad: float
    # Lo que RRHH tiene citado para ese intervalo (de payroll / payroll_futuro).
    # None = todavía no hay malla cargada para ese día.
    planificados: Optional[int] = None
    # Gente de las sub-campañas que se pueden pasar a la línea (Digital) con turno
    # en el intervalo. None = el pool no tiene refuerzo configurado.
    refuerzo_disponible: Optional[int] = None
    # Los mismos citados contados con el peso de su antigüedad (la gente en sus
    # primeras semanas de piso rinde menos). None = sin curva medida: cada citado
    # cuenta 1. Ver `planificador_antiguedad`.
    planificados_equivalentes: Optional[float] = None
    avisos: List[str] = field(default_factory=list)

    @property
    def brecha(self) -> Optional[int]:
        """Citados menos necesarios. Negativo = falta gente para cumplir el
        objetivo; positivo = sobra. Es el número que se lleva a la reunión.

        Con la curva de antigüedad medida, los citados van en equivalentes
        redondeados: diez personas que recién pasaron a piso no cubren lo que
        cubren diez con meses."""
        if self.planificados is None:
            return None
        citados = (self.planificados if self.planificados_equivalentes is None
                   else int(self.planificados_equivalentes + 0.5))
        return citados - self.operadores_a_planificar

    @property
    def refuerzo_cubre(self) -> Optional[int]:
        """Cuánto del faltante contra la malla se puede tapar pasando gente del
        refuerzo a la línea: nunca más de lo que falta ni de los que hay.

        No toca `operadores_a_planificar`, a propósito: lo que pide la cola es lo
        mismo, lo que cambia es quién lo puede cubrir. Contar con Digital al citar
        dejaría a Digital sin gente todos los días, y la palanca es para cuando no
        se llega."""
        if self.brecha is None or self.refuerzo_disponible is None:
            return None
        return min(max(-self.brecha, 0), self.refuerzo_disponible)

    @property
    def faltante_neto(self) -> Optional[int]:
        """Lo que sigue faltando aun pasando a todo el refuerzo disponible."""
        cubre = self.refuerzo_cubre
        if cubre is None:
            return None
        return max(-self.brecha, 0) - cubre


def dimensionar_intervalo(cfg: CampanaCfg, pool: PoolCfg, momento: datetime,
                          demanda: Sequence[DemandaSkill]) -> RequerimientoPool:
    """Operadores necesarios en un pool para un intervalo.

    Junta el tráfico de todos los skills del pool (que es lo que corresponde: la
    cola es compartida), le exige el objetivo más estricto de los que tienen
    volumen, y después verifica skill por skill el techo de abandono con la
    paciencia propia de cada uno.
    """
    del_pool = {s.skill_id: s for s in cfg.skills_del_pool(pool.pool_id)}
    activas = [d for d in demanda
               if d.skill_id in del_pool and d.llamadas > 0 and d.tmo_seg > 0]

    llamadas = sum(d.llamadas for d in activas)
    trabajo = sum(d.llamadas * d.tmo_seg for d in activas)
    tmo_efectivo = (trabajo / llamadas) if llamadas > 0 else 0.0

    objetivo, umbral, paciencia, extras = _objetivos_del_pool(
        cfg, [del_pool[d.skill_id] for d in activas], activas)

    disponibilidad = cfg.factor_disponibilidad(momento)

    # El techo de abandono NO se aplica acá a propósito. A nivel pool habría que
    # usar la paciencia promediada, y eso le exigiría a los clientes de
    # Emergencias (que esperan 853s) el techo del skill más estricto. El abandono
    # se verifica después, skill por skill y cada uno con su propia paciencia.
    base = erlang.dimensionar(
        llamadas=llamadas,
        tmo_seg=tmo_efectivo,
        objetivo_nds=objetivo,
        umbral_seg=umbral,
        paciencia_seg=paciencia,
        max_abandono=None,
        max_ocupacion=cfg.max_ocupacion,
        shrinkage=0.0,                     # se aplica después, junto a disponibilidad
        minimo_operadores=pool.min_operadores,
        intervalo_seg=cfg.intervalo_seg,
        **extras,
    )

    n = base.operadores_en_linea
    motivo = base.motivo
    avisos = list(base.avisos)

    # Verificación por skill: cada uno con SU paciencia. Es lo que permite que
    # Electrodependientes (compromiso de nivel de atención) viva en el pool
    # grande sin obligar a dimensionar toda la cola como si fuera él.
    n, motivo, aviso_skill = _ajustar_por_skill(
        cfg, activas, del_pool, n, base.trafico, tmo_efectivo, motivo)
    avisos.extend(aviso_skill)

    final = erlang.metricas_a(n, base.trafico, tmo_efectivo, umbral, paciencia)
    presentes = int(math.ceil(n / disponibilidad)) if n > 0 else 0
    # Los tres descuentos van ENCADENADOS y nunca sumados: son proporciones de
    # cosas distintas —de los citados, cuántos llegan al piso; de los que están,
    # cuántos no están en su break; de los disponibles, cuántos están sobre esta
    # cola—. Sumarlos descontaría dos veces lo mismo.
    #
    # Y VAN EN FLOAT, CON UN SOLO REDONDEO AL FINAL. Redondear en cada paso
    # inventa gente, y con pocos operadores la inventa a lo grande: 2 en línea con
    # estos factores da ceil(2/0,918)=3 y después ceil(3/0,9167)=4, o sea el doble,
    # cuando la cuenta en float da 2,38 → 3. Medido sobre el pool telefónico, 16
    # días: redondear tres veces pide 13.937 intervalos-operador contra 13.109
    # redondeando una sola vez (+6,3%), y el exceso está casi todo en los
    # intervalos chicos:
    #
    #     en línea   intervalos   x3 ceil   x1 ceil   de más
    #       1-4          294       2,012     1,410     455
    #       5-9          204       1,366     1,303      86
    #      10-19          73       1,295     1,242      51
    #      20-39         129       1,310     1,270     157
    #      40+            68       1,318     1,293      79
    #
    # O sea que la madrugada y los fines de semana —que son casi todos intervalos
    # de uno a cuatro operadores— pedían un 43% de gente de más por redondeo, no
    # por dimensionamiento. Es lo que hacía que el cotejo de dotación mostrara un
    # domingo pidiendo +55% con el nivel de servicio cumplido.
    bruto = erlang._sin_shrinkage(n / disponibilidad if n > 0 else 0.0,
                                  cfg.shrinkage_del_dia(momento))
    if cfg.break_min_por_hora > 0:
        bruto = erlang._sin_shrinkage(bruto, cfg.factor_break())
    if bruto <= 0:
        a_planificar = 0
    elif cfg.redondea_para_abajo(momento):
        # De madrugada, para abajo (ver `CampanaCfg.redondeo_abajo_desde`). El
        # max con `n` es por las dudas: matemáticamente bruto >= n siempre, pero
        # un factor mal cargado mayor a 1 no puede dejar la línea sin gente.
        a_planificar = max(n, int(math.floor(bruto + 1e-9)))
    else:
        a_planificar = int(math.ceil(bruto))

    return RequerimientoPool(
        pool_id=pool.pool_id,
        momento=momento,
        llamadas=llamadas,
        tmo_seg=tmo_efectivo,
        trafico=base.trafico,
        operadores_en_linea=n,
        operadores_presentes=presentes,
        operadores_a_planificar=a_planificar,
        nds_contractual=erlang.nivel_servicio_c(n, base.trafico, tmo_efectivo, umbral)
        if tmo_efectivo > 0 else 1.0,
        nds_sobre_atendidas=final.nds_sobre_atendidas,
        nds_sobre_entrantes=final.nds_sobre_entrantes,
        abandono=final.p_abandono,
        ocupacion=final.ocupacion,
        asa_seg=erlang.asa_seg(n, base.trafico, tmo_efectivo),
        nivel_atencion_b=erlang.nivel_atencion_b(n, base.trafico),
        motivo=motivo,
        disponibilidad=disponibilidad,
        avisos=avisos,
    )


def _objetivos_del_pool(cfg: CampanaCfg, skills: Sequence[SkillCfg],
                        demanda: Sequence[DemandaSkill]):
    """El objetivo de espera más estricto entre los skills con volumen, y la
    paciencia ponderada por llamadas.

    No devuelve techo de abandono: ese es el único objetivo que NO se puede
    componer promediando, porque depende de la paciencia de cada tipo de cliente.
    Lo resuelve `_ajustar_por_skill`.
    """
    objetivos = [s.objetivo_nds for s in skills if s.objetivo_nds]
    umbrales = [s.umbral_seg for s in skills if s.umbral_seg]

    objetivo = max(objetivos) if objetivos else 0.80
    umbral = float(min(umbrales)) if umbrales else 20.0

    # Las restricciones de la planilla vieja SÍ se componen tomando la más
    # estricta, al revés que el techo de abandono: todas dependen únicamente de la
    # congestión de la cola (n y tráfico) y no de la paciencia de cada cliente.
    asas = [s.max_asa_seg for s in skills if s.max_asa_seg]
    nds2 = [s.objetivo_nds_2 for s in skills if s.objetivo_nds_2]
    umb2 = [s.umbral_seg_2 for s in skills if s.umbral_seg_2]
    nab = [s.min_nivel_atencion_b for s in skills if s.min_nivel_atencion_b]
    extras = {
        "max_asa_seg": float(min(asas)) if asas else None,
        "objetivo_nds_2": max(nds2) if nds2 else None,
        "umbral_seg_2": float(min(umb2)) if umb2 else None,
        "min_nivel_atencion_b": max(nab) if nab else None,
    }

    total = sum(d.llamadas for d in demanda)
    if total > 0:
        por_skill = {s.skill_id: s for s in skills}
        acumulado = 0.0
        for d in demanda:
            s = por_skill.get(d.skill_id)
            p = (s.paciencia_seg if s and s.paciencia_seg else cfg.paciencia_seg)
            if not p:
                acumulado = 0.0
                break
            acumulado += d.llamadas * p
        paciencia = (acumulado / total) if acumulado > 0 else cfg.paciencia_seg
    else:
        paciencia = cfg.paciencia_seg

    return objetivo, umbral, paciencia, extras


def _ajustar_por_skill(cfg: CampanaCfg, demanda: Sequence[DemandaSkill],
                       del_pool: Dict[int, SkillCfg], n: int, trafico: float,
                       tmo_efectivo: float, motivo: str):
    """Sube la dotación hasta que ningún skill viole su propio techo de abandono.

    La congestión la sufre toda la cola por igual (es compartida), pero cuánto se
    traduce eso en abandono depende de dos cosas de cada tipo de cliente: su
    PACIENCIA —un electrodependiente espera distinto que uno que llama por una
    duda de factura, así que el mismo tiempo de espera le produce distinto
    abandono— y su PRIORIDAD en el ACD, porque la cola prioritaria no espera
    detrás de las demás.
    """
    avisos: List[str] = []
    con_tope = [(d, del_pool[d.skill_id]) for d in demanda
                if del_pool[d.skill_id].max_abandono is not None]
    if not con_tope or n <= 0:
        return n, motivo, avisos

    for _ in range(erlang.MAX_OPERADORES):
        incumple = None
        for d, s in con_tope:
            paciencia = s.paciencia_seg or cfg.paciencia_seg
            if not paciencia:
                continue
            # La cola con prioridad se verifica como lo que es: la primera de la
            # fila. Sin esto, el techo de abandono de Electrodependientes se
            # evaluaba con la congestión que sufre el ÚLTIMO de la cola, y era la
            # restricción que más dotación pedía en el pool.
            m = erlang.metricas_a(n, trafico, tmo_efectivo,
                                  float(s.umbral_seg or 20), paciencia,
                                  prioritario=s.prioridad)
            if m.p_abandono > s.max_abandono:
                incumple = s
                break
        if incumple is None:
            return n, motivo, avisos
        n += 1
        motivo = f"abandono de {incumple.nombre}"
        if n >= erlang.MAX_OPERADORES:
            avisos.append(
                f"No se pudo cumplir el techo de abandono de {incumple.nombre} "
                f"({incumple.max_abandono:.2%}) ni con {erlang.MAX_OPERADORES} operadores."
            )
            return n, motivo, avisos
    return n, motivo, avisos


def plan_de_pool(cfg: CampanaCfg, pool: PoolCfg,
                 demanda: Dict[datetime, List[DemandaSkill]]) -> List[RequerimientoPool]:
    """Dimensiona todos los intervalos de un pool."""
    return [dimensionar_intervalo(cfg, pool, momento, demanda.get(momento, []))
            for momento in sorted(demanda)]


def resumen_de_plan(requerimientos: Iterable[RequerimientoPool],
                    intervalo_min: int = 30) -> Dict[str, float]:
    """Cifras de cabecera del plan: lo que va arriba de la pantalla.

    Las horas-operador son la traducción a plata: es el número con el que se
    discute una dotación, no el pico de un intervalo suelto.
    """
    reqs = list(requerimientos)
    if not reqs:
        return {}
    intervalos_por_hora = 60 / intervalo_min
    con_carga = [r for r in reqs if r.llamadas > 0]
    return {
        "intervalos": len(reqs),
        "llamadas": round(sum(r.llamadas for r in reqs), 1),
        "pico_operadores": max(r.operadores_a_planificar for r in reqs),
        "horas_operador": round(
            sum(r.operadores_a_planificar for r in reqs) / intervalos_por_hora, 1),
        "nds_promedio": round(
            sum(r.nds_contractual * r.llamadas for r in con_carga)
            / sum(r.llamadas for r in con_carga), 4) if con_carga else None,
        "abandono_promedio": round(
            sum(r.abandono * r.llamadas for r in con_carga)
            / sum(r.llamadas for r in con_carga), 4) if con_carga else None,
        "avisos": sorted({a for r in reqs for a in r.avisos}),
    }


# ============================================================================
# MEDICIÓN DEL ERROR — qué tan cerca estuvo el pronóstico de lo que pasó
# ============================================================================
# Se reportan dos medidas y no una, porque miden cosas distintas y cada una
# miente sola:
#
#   WAPE  = suma|error| / suma real.  Pondera por volumen, así que dice cuánto
#           erró el pronóstico DONDE HAY GENTE ATENDIENDO. Es la que hay que
#           mirar para dimensionar.
#   MAPE  = promedio de |error|/real.  Le da el mismo peso a un intervalo de 300
#           llamadas que a uno de 2, y de madrugada cualquier diferencia da
#           cientos por ciento. Sobre medias horas es directamente inservible;
#           sobre TOTALES DIARIOS sí significa algo, y es el número que la gente
#           está acostumbrada a citar.
#
# Por eso el servicio calcula el WAPE sobre intervalos y el MAPE sobre días.

@dataclass
class ErrorPronostico:
    """Cuánto erró un pronóstico contra lo que efectivamente pasó."""
    n: int
    real: float
    pronosticado: float
    # Positivo = el pronóstico se quedó CORTO (vino más real del previsto). Es
    # la misma convención que la tarjeta de seguimiento intradía, a propósito:
    # dos signos distintos para la misma idea en la misma pantalla es una trampa.
    sesgo: Optional[float]
    wape: Optional[float]
    mape: Optional[float]

    def como_dict(self) -> Dict[str, object]:
        return {
            "n": self.n,
            "real": round(self.real, 1),
            "pronosticado": round(self.pronosticado, 1),
            "sesgo": round(self.sesgo, 4) if self.sesgo is not None else None,
            "wape": round(self.wape, 4) if self.wape is not None else None,
            "mape": round(self.mape, 4) if self.mape is not None else None,
        }


def medir_error(pares: Sequence[Tuple[float, float]],
                minimo_para_mape: float = 0.0) -> ErrorPronostico:
    """Error de un pronóstico. `pares` son (real, pronosticado).

    `minimo_para_mape` saca del MAPE los puntos con muy poco volumen real, que
    son los que lo hacen explotar. No toca el WAPE ni el sesgo: esos ya están
    ponderados por volumen y no necesitan que nadie elija un umbral.
    """
    datos = [(float(r), float(p)) for r, p in pares]
    if not datos:
        return ErrorPronostico(0, 0.0, 0.0, None, None, None)

    real = sum(r for r, _ in datos)
    pron = sum(p for _, p in datos)
    abs_err = sum(abs(p - r) for r, p in datos)

    relativos = [abs(p - r) / r for r, p in datos if r > 0 and r >= minimo_para_mape]
    return ErrorPronostico(
        n=len(datos),
        real=real,
        pronosticado=pron,
        sesgo=(real / pron - 1) if pron > 0 else None,
        wape=(abs_err / real) if real > 0 else None,
        mape=(sum(relativos) / len(relativos)) if relativos else None,
    )


# ============================================================================
# REFUERZOS — de la brecha a "cuántas horas extra pido, para cuándo y cómo"
# ============================================================================
# El requerimiento por intervalo es correcto pero no es accionable: nadie pide
# "0,7 operadores para las 14:30". Lo que se pide es un BLOQUE — "tres personas
# de 14 a 18 el jueves"— y la forma de pedirlo depende de cuánto falta para ese
# día:
#
#     4 días o más   todavía se puede mover la malla, que no cuesta plata
#     1 a 3 días     hora extra programada, con la gente avisada
#     el mismo día   convocatoria o extensión de jornada, lo más caro y lo peor
#                    para el clima interno
#
# POR QUÉ HAY UN MARGEN POR ERROR DE PRONÓSTICO
# ----------------------------------------------
# Un pronóstico a 7 días erra en el orden del 30%. Pedir horas extra por una
# brecha de dos personas cuando el ruido del pronóstico es de diez es tirar
# plata; y no pedirlas cuando el día puede venir 30% arriba es quedarse corto.
# Por eso cada tramo se informa DOS VECES: con el volumen pronosticado y con el
# volumen del escenario alto. La decisión de cuál mirar depende de qué cuesta
# más en esa campaña, y ésa es una decisión de negocio, no del modelo.

# Cuántos días de antelación hacen falta para que todavía se pueda resolver
# moviendo turnos en vez de pagando horas extra.
DIAS_PARA_MOVER_MALLA = 4

# Faltante mínimo, en operadores, para que un tramo valga la pena informarse.
# Por debajo de esto es ruido de redondeo del propio Erlang.
FALTANTE_MINIMO = 1


@dataclass
class Refuerzo:
    """Un bloque contiguo de intervalos donde falta gente, en un pool y un día."""
    pool_id: int
    pool: str
    dia: date
    desde: datetime
    hasta: datetime               # exclusivo: el fin del último intervalo
    intervalos: int
    faltante_pico: int
    faltante_promedio: float
    horas_operador: float
    # El mismo tramo con el volumen del escenario alto (pronóstico + error
    # típico a esa antelación). Es el seguro, no la previsión.
    faltante_pico_alto: Optional[int] = None
    horas_operador_alto: Optional[float] = None
    antelacion_dias: int = 0
    accion: str = "malla"
    llamadas: float = 0.0

    def como_dict(self) -> Dict[str, object]:
        return {
            "pool_id": self.pool_id, "pool": self.pool, "dia": self.dia,
            "desde": self.desde, "hasta": self.hasta,
            "intervalos": self.intervalos,
            "faltante_pico": self.faltante_pico,
            "faltante_promedio": round(self.faltante_promedio, 1),
            "horas_operador": round(self.horas_operador, 1),
            "faltante_pico_alto": self.faltante_pico_alto,
            "horas_operador_alto": (round(self.horas_operador_alto, 1)
                                    if self.horas_operador_alto is not None else None),
            "antelacion_dias": self.antelacion_dias,
            "accion": self.accion,
            "llamadas": round(self.llamadas, 1),
        }


def accion_por_antelacion(dias: int) -> str:
    """Cómo se resuelve un faltante según cuánto falta para el día.

    No es cosmético: cada canal tiene un costo y un plazo distintos, y ponerle
    nombre es lo que convierte el número en una decisión."""
    if dias >= DIAS_PARA_MOVER_MALLA:
        return "malla"
    if dias >= 1:
        return "horas_extra"
    return "convocatoria"


def agrupar_refuerzos(filas: Sequence[dict], hoy: date, intervalo_min: int = 30,
                      faltante_minimo: int = FALTANTE_MINIMO) -> List[Refuerzo]:
    """Junta intervalos contiguos con faltante en tramos pedibles.

    `filas` son dicts con pool_id, pool, momento, faltante (positivo = falta),
    faltante_alto y llamadas. Se cortan los tramos cuando cambia el pool, cambia
    el día o hay un hueco: un faltante de 10 a 12 y otro de 17 a 19 son dos
    pedidos distintos, y unirlos pediría gente para el mediodía que no hace falta.
    """
    paso = timedelta(minutes=intervalo_min)
    ordenadas = sorted((f for f in filas if f.get("faltante", 0) >= faltante_minimo),
                       key=lambda f: (f["pool_id"], f["momento"]))

    tramos: List[Refuerzo] = []
    actual: List[dict] = []

    def cerrar():
        if not actual:
            return
        primero, ultimo = actual[0], actual[-1]
        faltantes = [f["faltante"] for f in actual]
        altos = [f.get("faltante_alto") for f in actual if f.get("faltante_alto")]
        dia = primero["momento"].date()
        antelacion = (dia - hoy).days
        por_hora = 60 / intervalo_min
        tramos.append(Refuerzo(
            pool_id=primero["pool_id"], pool=primero.get("pool", ""), dia=dia,
            desde=primero["momento"], hasta=ultimo["momento"] + paso,
            intervalos=len(actual),
            faltante_pico=max(faltantes),
            faltante_promedio=sum(faltantes) / len(faltantes),
            horas_operador=sum(faltantes) / por_hora,
            faltante_pico_alto=max(altos) if altos else None,
            horas_operador_alto=(sum(altos) / por_hora) if altos else None,
            antelacion_dias=antelacion,
            accion=accion_por_antelacion(antelacion),
            llamadas=sum(f.get("llamadas", 0.0) for f in actual),
        ))
        actual.clear()

    for f in ordenadas:
        if actual:
            previo = actual[-1]
            corta = (f["pool_id"] != previo["pool_id"]
                     or f["momento"].date() != previo["momento"].date()
                     or f["momento"] != previo["momento"] + paso)
            if corta:
                cerrar()
        actual.append(f)
    cerrar()
    return tramos


def resumen_de_refuerzos(refuerzos: Sequence[Refuerzo]) -> Dict[str, object]:
    """Cifras de cabecera: lo que se lleva a la reunión de dotación."""
    if not refuerzos:
        return {"tramos": 0, "horas_operador": 0.0, "dias": 0, "por_accion": {}}
    por_accion: Dict[str, Dict[str, float]] = {}
    for r in refuerzos:
        d = por_accion.setdefault(r.accion, {"tramos": 0, "horas_operador": 0.0})
        d["tramos"] += 1
        d["horas_operador"] += r.horas_operador
    for d in por_accion.values():
        d["horas_operador"] = round(d["horas_operador"], 1)
    return {
        "tramos": len(refuerzos),
        "dias": len({r.dia for r in refuerzos}),
        "horas_operador": round(sum(r.horas_operador for r in refuerzos), 1),
        "horas_operador_alto": round(
            sum(r.horas_operador_alto or r.horas_operador for r in refuerzos), 1),
        "faltante_pico": max(r.faltante_pico for r in refuerzos),
        "por_accion": por_accion,
    }
