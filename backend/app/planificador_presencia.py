"""Planificador — perfil de presencia por media hora (el retraso al arrancar el turno).

El shrinkage de nómina dice qué parte de la gente con turno no llega a la cola en
el día, pero no CUÁNDO. Medido media hora por media hora (gente del pool con turno
en el registro contra los conectados que cubren la línea, ver `conectados_que_cubren`),
el faltante no es parejo: en hábiles se concentra a primera hora de la mañana —el que
entra se conecta tarde— y a la tarde, donde se solapan los turnos, hay más conectados
que el promedio. Repartirlo parejo pedía gente de más a la tarde y de menos cuando
arranca el día.

Acá se mide cuánto se aparta cada media hora de la presencia MEDIA de su tipo de
día. El dimensionamiento suma ese apartamiento al shrinkage del día
(`CampanaCfg.shrinkage_del_dia`): la jornada descuenta lo mismo que antes y sólo
cambia en qué media hora. La media de cada tipo de día es además el NIVEL que
propone la calibración (`planificador_datos.nivel_de_presencia`): forma y nivel
salen de la misma medición, sobre la misma gente.

Dos resguardos. Una media hora con pocas persona-intervalos detrás se encoge hacia
cero —la madrugada de un domingo son un puñado de personas y cualquier ausencia
parece un patrón—. Y ningún apartamiento pasa del tope.

La medición lee semanas del registro y de los conectados (vistas pesadas, cerca de
un minuto), así que no corre en cada recálculo: la hace
scripts/planificador_perfil_presencia.py y se guarda en planificacion.PerfilPresencia.
"""

from datetime import date, datetime
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

SEMANAS = 8
# Con esta cantidad de persona-intervalos el apartamiento se usa a la mitad.
MUESTRAS_ENCOGIMIENTO = 200
TOPE_EXCESO = 0.35


def conectados_que_cubren(por_origen: Mapping[datetime, Mapping[str, float]]
                          ) -> Dict[datetime, float]:
    """Los conectados contra los que se mide la presencia: los del pool MÁS los de
    otras sub-campañas, SIN los del refuerzo (Digital).

    POR QUÉ NO SÓLO LOS DEL POOL. La disponibilidad —el escalón anterior de la
    cadena— se mide contra TODOS los conectados (`FuenteVoltara.CALIBRACION` lee el
    tablero entero). Si la presencia se mide sólo con la gente del pool, la cadena
    supone que las otras sub-campañas no ayudan, cuando la disponibilidad ya contó
    sus horas logueadas como parte de los presentes. Medido del 17/08 al 15/09, en
    hábiles, contra la gente con turno de las telefónicas:

        conectados del pool                      84,5%
        + otras (Gestión SVP, Anfitrión,         93,6%
          BackOffice, RRSS, Contingencia…)
        + Digital                                98,4%

    Sobre 8 semanas (al 16/09) la presencia media da 92,7% en hábiles y 90,1% en no
    hábiles: 7,3% y 9,9% de faltante, casi lo mismo que ya daban los códigos de RRHH.
    La forma por media hora casi no cambia (la noche hábil sigue faltando más). Lo
    que cambia es que nivel y forma describen a la misma gente que la disponibilidad.

    Las digitales que NO son palanca (grupo "digital", migración 2026-09-16) siguen
    adentro, igual que antes de clasificarlas: son ayuda que no se pide. Así la
    clasificación no mueve la presencia medida. Las de colas dedicadas (grupo
    "dedicada", T1 - Consumo) quedan afuera: atienden otra cola, no ésta.

    POR QUÉ SIN DIGITAL. Digital es la palanca (`planificacion.PoolRefuerzo`): la
    operación decidió (2026-09-14) no citar menos telefónicos contando con ella, y
    el plan ya la muestra aparte como «lo cubre Digital». Contarla también acá la
    usaría dos veces. Decisión de Ignacio, 2026-09-16.
    """
    return {m: float(g.get("pool") or 0.0) + float(g.get("otras") or 0.0)
            + float(g.get("digital") or 0.0)
            for m, g in por_origen.items()}


def tipo_de_dia(dia: date, feriados: Iterable[date] = ()) -> str:
    """Dos tipos y no más: con semanas de historia, partir el fin de semana en
    sábado y domingo deja muy pocas muestras por media hora."""
    return "no_habil" if (dia.isoweekday() >= 6 or dia in feriados) else "habil"


def medir_perfil(turnos: Mapping[datetime, float], conectados: Mapping[datetime, float],
                 feriados: Iterable[date] = (), hoy: Optional[date] = None,
                 muestras_encogimiento: float = MUESTRAS_ENCOGIMIENTO,
                 tope: float = TOPE_EXCESO) -> List[dict]:
    """Por (tipo de día, media hora): faltante medido, apartamiento de la media.

    `turnos`: momento -> personas con turno (registro del día cerrado).
    `conectados`: momento -> operadores-equivalentes que cubren la línea
    (`conectados_que_cubren`).
    Un momento sin dato de conexión no se cuenta: no es ausencia, es falta de dato.
    """
    fer = set(feriados)
    con_turno: Dict[Tuple[str, int], float] = {}
    conectada: Dict[Tuple[str, int], float] = {}
    for momento, personas in turnos.items():
        if not personas or personas <= 0 or momento not in conectados:
            continue
        if hoy is not None and momento.date() >= hoy:
            continue
        clave = (tipo_de_dia(momento.date(), fer), momento.hour * 60 + momento.minute)
        con_turno[clave] = con_turno.get(clave, 0.0) + personas
        conectada[clave] = conectada.get(clave, 0.0) + float(conectados[momento] or 0.0)

    filas: List[dict] = []
    for tipo in ("habil", "no_habil"):
        claves = sorted((c for c in con_turno if c[0] == tipo), key=lambda c: c[1])
        total = sum(con_turno[c] for c in claves)
        if not total:
            continue
        # Media ponderada por gente: así el apartamiento de toda la jornada suma
        # cero y el shrinkage del día no cambia, sólo se redistribuye.
        medio = 1 - sum(conectada[c] for c in claves) / total
        for c in claves:
            faltante = 1 - conectada[c] / con_turno[c]
            peso = con_turno[c] / (con_turno[c] + muestras_encogimiento)
            exceso = max(-tope, min(tope, (faltante - medio) * peso))
            filas.append({"tipo_dia": tipo, "minuto": c[1], "exceso": round(exceso, 4),
                          "faltante": round(faltante, 4),
                          "muestras": int(round(con_turno[c]))})
    return filas


def nivel_por_tipo(filas: Iterable[Mapping]) -> Dict[str, dict]:
    """El faltante medio de cada tipo de día: el NIVEL que propone la calibración.

    Ponderado por muestras (persona-intervalos con turno), que reproduce la media
    con la que `medir_perfil` calcula el apartamiento: así base + apartamiento de
    cada media hora es su faltante medido (encogido y topado), sin contar nada dos
    veces. Promediar los faltantes sin ponderar le daría a la madrugada el mismo
    peso que al pico.
    """
    acum: Dict[str, List[float]] = {}
    for f in filas:
        n = float(f["muestras"] or 0)
        if n <= 0 or f["faltante"] is None:
            continue
        a = acum.setdefault(f["tipo_dia"], [0.0, 0.0])
        a[0] += float(f["faltante"]) * n
        a[1] += n
    return {t: {"faltante": round(total / n, 4), "muestras": int(round(n))}
            for t, (total, n) in acum.items() if n > 0}


def como_perfil(filas: Iterable[Mapping]) -> Dict[Tuple[str, int], float]:
    return {(f["tipo_dia"], int(f["minuto"])): float(f["exceso"]) for f in filas}
