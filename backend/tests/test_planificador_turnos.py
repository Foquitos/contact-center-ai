"""Tests exhaustivos del dimensionamiento de turnos sugeridos (planificador_turnos).

Invariantes defendidas:
  - Todo faltante queda cubierto (la simulación de los turnos resta toda la brecha).
  - Determinismo estricto: misma entrada produce siempre idéntica salida.
  - Agrupación correcta: turnos con idéntico tipo, inicio y fin se acumulan en `cantidad`.
  - Casos de borde: entrada vacía, ceros, bloque de 1 intervalo (30 min), bloque mayor a 8 h.
  - Sobrecobertura acotada y explicable.

Offline: no toca base de datos ni red.
"""

from datetime import datetime, timedelta

import pytest

from app.planificador_turnos import (
    EXTENSION_MAX_H,
    LARGOS_TURNO_H,
    formatear_propuesta,
    sugerir_turnos,
)


def _generar_faltantes(inicio: datetime, cant_intervalos: int, valor: int = 1, intervalo_min: int = 30):
    paso = timedelta(minutes=intervalo_min)
    return [(inicio + i * paso, valor) for i in range(cant_intervalos)]


def test_entrada_vacia():
    res = sugerir_turnos([])
    assert res["propuesta"] == []
    assert res["horas_propuestas"] == 0.0
    assert res["horas_faltantes"] == 0.0
    assert res["sobrecobertura_h"] == 0.0
    assert res["texto"] == "Sin refuerzos necesarios"


def test_entrada_con_todos_ceros():
    t0 = datetime(2026, 9, 15, 9, 0)
    faltantes = [(t0, 0), (t0 + timedelta(minutes=30), 0)]
    res = sugerir_turnos(faltantes)
    assert res["propuesta"] == []
    assert res["horas_propuestas"] == 0.0
    assert res["horas_faltantes"] == 0.0
    assert res["sobrecobertura_h"] == 0.0


def test_bloque_de_un_intervalo():
    """Media hora de faltante no se pide como media hora: sale la pieza mínima
    (1 h) y la media hora de más queda como sobrecobertura."""
    t0 = datetime(2026, 9, 15, 10, 0)
    faltantes = [(t0, 1)]
    res = sugerir_turnos(faltantes, intervalo_min=30)

    assert len(res["propuesta"]) == 1
    p = res["propuesta"][0]
    assert p["tipo"] == "extension"
    assert p["desde"] == t0
    assert p["hasta"] == t0 + timedelta(hours=1)
    assert p["horas"] == 1.0
    assert p["cantidad"] == 1
    assert res["horas_faltantes"] == 0.5
    assert res["horas_propuestas"] == 1.0
    assert res["sobrecobertura_h"] == 0.5
    assert res["texto"] == "1 extensión 10:00–11:00"


def test_un_pico_se_cubre_por_capas_y_no_con_medias_horas_sueltas():
    """Perfil 1-2-3-3-2-1 (3 h): una persona de punta a punta, otra en el medio y
    otra en el centro. Ninguna pieza de media hora."""
    t0 = datetime(2026, 9, 15, 8, 0)
    paso = timedelta(minutes=30)
    faltantes = [(t0 + i * paso, v) for i, v in enumerate([1, 2, 3, 3, 2, 1])]
    res = sugerir_turnos(faltantes)

    assert all(p["horas"] >= 1.0 for p in res["propuesta"])
    assert res["texto"] == ("1 extensión 08:00–11:00 · 1 extensión 08:30–10:30 · "
                            "1 extensión 09:00–10:00")
    assert res["sobrecobertura_h"] == 0.0


def test_bloque_extension_corta():
    """Corrida de 2 horas (4 intervalos) con faltante 1: extensión de 2.0 h."""
    t0 = datetime(2026, 9, 15, 14, 0)
    faltantes = _generar_faltantes(t0, 4, valor=1, intervalo_min=30)
    res = sugerir_turnos(faltantes)

    assert len(res["propuesta"]) == 1
    p = res["propuesta"][0]
    assert p["tipo"] == "extension"
    assert p["desde"] == t0
    assert p["hasta"] == t0 + timedelta(hours=2)
    assert p["horas"] == 2.0
    assert p["cantidad"] == 1
    assert res["horas_faltantes"] == 2.0
    assert res["horas_propuestas"] == 2.0
    assert res["sobrecobertura_h"] == 0.0


def test_bloque_que_supera_extension_pero_menor_a_min_turno():
    """Corrida de 3.5 horas (7 intervalos): supera EXTENSION_MAX_H (3 h),
    por lo que debe proponer un turno estándar de 4 horas (el menor largo que cubra 3.5 h).
    """
    t0 = datetime(2026, 9, 15, 8, 0)
    faltantes = _generar_faltantes(t0, 7, valor=1, intervalo_min=30)
    res = sugerir_turnos(faltantes)

    assert len(res["propuesta"]) == 1
    p = res["propuesta"][0]
    assert p["tipo"] == "turno"
    assert p["desde"] == t0
    assert p["hasta"] == t0 + timedelta(hours=4)
    assert p["horas"] == 4.0
    assert p["cantidad"] == 1
    assert res["horas_faltantes"] == 3.5
    assert res["horas_propuestas"] == 4.0
    assert res["sobrecobertura_h"] == 0.5
    assert res["texto"] == "1 turno 08:00–12:00"


def test_bloque_turno_estandar():
    """Corrida de 5.5 horas (11 intervalos): entre 4 y 6 horas, debe sugerir turno de 6 h."""
    t0 = datetime(2026, 9, 15, 9, 0)
    faltantes = _generar_faltantes(t0, 11, valor=1, intervalo_min=30)
    res = sugerir_turnos(faltantes)

    assert len(res["propuesta"]) == 1
    p = res["propuesta"][0]
    assert p["tipo"] == "turno"
    assert p["desde"] == t0
    assert p["hasta"] == t0 + timedelta(hours=6)
    assert p["horas"] == 6.0
    assert p["cantidad"] == 1
    assert res["horas_faltantes"] == 5.5
    assert res["horas_propuestas"] == 6.0
    assert res["sobrecobertura_h"] == 0.5


def test_bloque_mas_largo_que_turno_maximo():
    """Corrida de 10 horas (20 intervalos): debe sugerir un turno de 8 h y una extensión de 2 h."""
    t0 = datetime(2026, 9, 15, 8, 0)
    faltantes = _generar_faltantes(t0, 20, valor=1, intervalo_min=30)
    res = sugerir_turnos(faltantes)

    assert len(res["propuesta"]) == 2
    t8, ext2 = res["propuesta"]

    assert t8["tipo"] == "turno"
    assert t8["desde"] == t0
    assert t8["hasta"] == t0 + timedelta(hours=8)
    assert t8["horas"] == 8.0
    assert t8["cantidad"] == 1

    assert ext2["tipo"] == "extension"
    assert ext2["desde"] == t0 + timedelta(hours=8)
    assert ext2["hasta"] == t0 + timedelta(hours=10)
    assert ext2["horas"] == 2.0
    assert ext2["cantidad"] == 1

    assert res["horas_faltantes"] == 10.0
    assert res["horas_propuestas"] == 10.0
    assert res["sobrecobertura_h"] == 0.0
    assert "1 turno 08:00–16:00 · 1 extensión 16:00–18:00" in res["texto"]


def test_agrupacion_multiples_operadores():
    """Faltan 3 operadores en los mismos 4 intervalos (2 h):
    deben agruparse en 3 extensiones del mismo horario.
    """
    t0 = datetime(2026, 9, 15, 10, 0)
    faltantes = _generar_faltantes(t0, 4, valor=3, intervalo_min=30)
    res = sugerir_turnos(faltantes)

    assert len(res["propuesta"]) == 1
    p = res["propuesta"][0]
    assert p["tipo"] == "extension"
    assert p["desde"] == t0
    assert p["hasta"] == t0 + timedelta(hours=2)
    assert p["horas"] == 2.0
    assert p["cantidad"] == 3
    assert res["horas_faltantes"] == 6.0
    assert res["horas_propuestas"] == 6.0
    assert res["sobrecobertura_h"] == 0.0
    assert res["texto"] == "3 extensiones 10:00–12:00"


def test_todo_faltante_queda_cubierto():
    """Verifica formalmente que ningún faltante quede sin cubrir en perfiles irregulares."""
    t0 = datetime(2026, 9, 15, 8, 0)
    # Perfil irregular con pico central: 1, 2, 4, 4, 3, 2, 1
    perfil = [1, 2, 4, 4, 3, 2, 1]
    paso = timedelta(minutes=30)
    faltantes = [(t0 + i * paso, v) for i, v in enumerate(perfil)]

    res = sugerir_turnos(faltantes)
    propuesta = res["propuesta"]

    # Simular la aplicación de la propuesta sobre el perfil original
    remanente = {t: v for t, v in faltantes}
    for p in propuesta:
        for _ in range(p["cantidad"]):
            desde, hasta = p["desde"], p["hasta"]
            for t in remanente:
                if desde <= t < hasta:
                    remanente[t] = max(0, remanente[t] - 1)

    assert all(v == 0 for v in remanente.values()), f"Quedó faltante sin cubrir: {remanente}"
    assert res["horas_propuestas"] >= res["horas_faltantes"]


def test_determinismo():
    """Correr 10 veces la función con el mismo input da exactamente el mismo resultado."""
    t0 = datetime(2026, 9, 15, 9, 0)
    perfil = [2, 3, 5, 4, 2, 1, 1, 2]
    paso = timedelta(minutes=30)
    faltantes = [(t0 + i * paso, v) for i, v in enumerate(perfil)]

    primer_res = sugerir_turnos(faltantes)
    for _ in range(9):
        res = sugerir_turnos(faltantes)
        assert res == primer_res


def test_sobrecobertura_acotada():
    """Un faltante de 1 solo intervalo con 1 operador cubierto por turno mínimo de 4 h
    no debe sobrecubrir más de 3.5 h.
    """
    t0 = datetime(2026, 9, 15, 10, 0)
    faltantes = [(t0, 1)]
    # Forzamos a no admitir extensiones (extension_max_h=0) para exigir turno
    res = sugerir_turnos(faltantes, extension_max_h=0)

    assert len(res["propuesta"]) == 1
    p = res["propuesta"][0]
    assert p["tipo"] == "turno"
    assert p["horas"] == 4.0
    assert res["horas_faltantes"] == 0.5
    assert res["horas_propuestas"] == 4.0
    assert res["sobrecobertura_h"] == 3.5


def test_formatear_propuesta():
    t0 = datetime(2026, 9, 15, 9, 0)
    prop = [
        {"tipo": "turno", "desde": t0, "hasta": t0 + timedelta(hours=6), "cantidad": 3},
        {"tipo": "extension", "desde": t0 + timedelta(hours=9), "hasta": t0 + timedelta(hours=11), "cantidad": 2},
    ]
    txt = formatear_propuesta(prop)
    assert txt == "3 turnos 09:00–15:00 · 2 extensiones 18:00–20:00"


def test_con_muchos_grupos_el_resumen_cuenta_y_el_texto_detalla():
    """Un pico escalonado da más de tres grupos: el renglón cuenta turnos y
    extensiones, y el detalle completo sigue en «texto» (va al Excel)."""
    t0 = datetime(2026, 9, 15, 8, 0)
    paso = timedelta(minutes=30)
    # Pico en punta: capas de 6 h, 4,5 h, 3,5 h, 2,5 h, 1,5 h y media hora.
    perfil = [1, 2, 3, 4, 5, 6, 5, 4, 3, 2, 1, 1]
    res = sugerir_turnos([(t0 + i * paso, v) for i, v in enumerate(perfil)])

    assert len(res["propuesta"]) > 3
    total_turnos = sum(p["cantidad"] for p in res["propuesta"] if p["tipo"] == "turno")
    total_ext = sum(p["cantidad"] for p in res["propuesta"] if p["tipo"] == "extension")
    assert res["resumen"].startswith(f"{total_turnos} turno")
    assert f"{total_ext} extensi" in res["resumen"]
    assert res["resumen"].endswith(f"{res['horas_propuestas']:g} h")
    assert res["texto"].count("·") == len(res["propuesta"]) - 1


def test_con_pocos_grupos_el_resumen_es_el_detalle():
    t0 = datetime(2026, 9, 15, 10, 0)
    res = sugerir_turnos(_generar_faltantes(t0, 4, valor=3))
    assert res["resumen"] == res["texto"] == "3 extensiones 10:00–12:00"
