"""Extras habituales: neto histórico entre el registro y la malla de RRHH.

POR QUÉ EXISTE ESTE MÓDULO
--------------------------
La grilla de turnos publicada por RRHH (`dbo.payroll_futuro`) es la malla contra
la que se planifica y se negocia la dotación, pero NO incluye horas extra cargadas
con anticipación. El registro del día cerrado (`dbo.payroll`), en cambio, sí tiene
los turnos como efectivamente ocurrieron, con refuerzos de último momento y horas
extra adentro.

Medido en producción sobre 8 semanas de la campaña telefónica de Voltara:
  - En días hábiles el neto (registro − malla) oscila alrededor de cero (+0,1 a
    -0,2 personas).
  - En noches de fin de semana (18–22 h), en cambio, aparecen sistemáticamente
    entre +1,0 y +2,0 personas por encima de la malla publicada (promedio +1,5
    sobre ~8 personas).

Como la pantalla del planificador contrasta el pronóstico contra la malla de
`payroll_futuro`, sin este dato la operación ve una brecha artificial en esas
franjas y sale a pedir refuerzos que después la operación real cubre sola con
horas extra habituales.

Este módulo calcula ese promedio histórico por tipo de día e intervalo y lo
expone para mostrarlo AL LADO de los citados, SIN alterar la brecha, los refuerzos
ni ningún cálculo de dimensionamiento del planificador.
"""
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Union


# Cuántas semanas hacia atrás se promedian para capturar el hábito reciente sin
# arrastrar estacionalidades de otra época del año.
SEMANAS_EXTRAS = 8

TIPOS_DE_DIA = ("habil", "sabado", "domingo_feriado")


def tipo_de_dia_extras(dia: date, feriados: Optional[Union[set, Sequence[date]]] = None) -> str:
    """Clasifica el día en 'habil', 'sabado' o 'domingo_feriado'.

    Domingos y feriados van juntos porque en Voltara comparten el comportamiento
    de dotación, los turnos reducidos y la dinámica de horas extra.
    """
    if feriados:
        fer_set = {f if isinstance(f, date) else date.fromisoformat(str(f)) for f in feriados}
        if dia in fer_set:
            return "domingo_feriado"
    w = dia.isoweekday()
    if w == 7:
        return "domingo_feriado"
    if w == 6:
        return "sabado"
    return "habil"


def _normalizar_tipo(tipo: str) -> str:
    """Asegura compatibilidad con clasificadores externos que devuelvan 'domingo',
    'feriado' o 'no_habil' por separado."""
    if tipo in ("domingo", "feriado", "domingo_feriado", "no_habil"):
        return "domingo_feriado"
    if tipo == "sabado":
        return "sabado"
    return "habil"


def extras_habituales(registro: Dict[datetime, Union[float, int]],
                      malla: Dict[datetime, Union[float, int]],
                      tipo_de_dia: Any,
                      dias: Optional[Sequence[date]] = None,
                      intervalo_min: int = 30) -> List[Dict[str, Any]]:
    """Promedio, por (tipo de día, hora "HH:MM"), de (registro − malla).

    Se calcula sobre los días cerrados de las últimas semanas. CADA DÍA DEL TIPO
    CUENTA: un día en el que no se cargaron horas extra o no hubo diferencia suma
    cero al numerador y uno al denominador. Si se promediara sólo sobre los
    intervalos con diferencia positiva, un día normal no bajaría el promedio y
    sobreestimaríamos las horas extra esperadas.

    Argumentos:
        registro: momento -> personas con turno según el registro cerrado (`dbo.payroll`).
        malla: momento -> personas con turno según la grilla publicada (`dbo.payroll_futuro`).
        tipo_de_dia: función `dia -> str`, set de feriados o mapping fecha -> tipo.
        dias: secuencia opcional de días cerrados a considerar. Si no se pasa,
              se toman todos los días presentes en los datos.
        intervalo_min: duración de cada intervalo en minutos (default 30).

    Devuelve:
        Lista de diccionarios con formato:
        [{"tipo_dia": "habil", "hora": "18:00", "personas": 0.1}, ...]
    """
    if dias is not None:
        dias_eval = sorted(set(dias))
    else:
        dias_eval = sorted(set(dt.date() for dt in registro.keys()) |
                           set(dt.date() for dt in malla.keys()))

    def _clasificar(d: date) -> str:
        if callable(tipo_de_dia):
            return _normalizar_tipo(str(tipo_de_dia(d)))
        if isinstance(tipo_de_dia, (set, frozenset, list, tuple)):
            return tipo_de_dia_extras(d, feriados=tipo_de_dia)
        if isinstance(tipo_de_dia, Mapping):
            return _normalizar_tipo(str(tipo_de_dia.get(d, "habil")))
        return "habil"

    # Agrupamos los días de la ventana por tipo.
    dias_por_tipo: Dict[str, List[date]] = {t: [] for t in TIPOS_DE_DIA}
    for d in dias_eval:
        t = _clasificar(d)
        if t in dias_por_tipo:
            dias_por_tipo[t].append(d)

    # Las medias horas del día: 00:00 a 23:30 para asegurar que la grilla
    # tenga cobertura completa en cualquier horario que mire la pantalla.
    paso = max(int(intervalo_min), 5)
    horas = [f"{h:02d}:{m:02d}" for h in range(24) for m in range(0, 60, paso)]

    salida: List[Dict[str, Any]] = []
    for t in TIPOS_DE_DIA:
        dias_tipo = dias_por_tipo[t]
        n_dias = len(dias_tipo)

        for h_str in horas:
            if n_dias == 0:
                salida.append({"tipo_dia": t, "hora": h_str, "personas": 0.0})
                continue

            h, m = (int(x) for x in h_str.split(":"))
            t_obj = time(h, m)
            suma_dif = 0.0

            for d in dias_tipo:
                dt = datetime.combine(d, t_obj)
                reg_val = float(registro.get(dt, 0) or 0)
                malla_val = float(malla.get(dt, 0) or 0)
                suma_dif += (reg_val - malla_val)

            promedio = round(suma_dif / n_dias, 1)
            salida.append({"tipo_dia": t, "hora": h_str, "personas": promedio})

    return salida
