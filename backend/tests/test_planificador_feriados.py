"""Tests del calendario de feriados del planificador.

De dónde salen los feriados no es un detalle administrativo: el tipo de día
decide el perfil estacional, el factor de clima por tipo de día, los rasgos de
calendario del GBDT y el corte del error en el backtest. Un puente turístico
tratado como martes cualquiera hace que el pronóstico se pase como 40%.

Medido sobre las llamadas de Voltara, cociente contra el promedio del mismo día de
semana cercano:

    2025-05-02  0,36     2026-03-23  0,60
    2025-08-15  0,64     2026-07-10  0,58
    2025-11-21  0,94     (los feriados de la tabla van de 0,15 a 0,69)

Cuatro de los cinco caen de lleno en el rango de un feriado, y ninguno de los
cinco estaba en `dbo.Feriados`.

Offline: no toca ni BD ni red.

Correr: pytest tests/test_planificador_feriados.py -m "not tokens"
"""
from datetime import date

import pytest

from app import planificador_datos as pd


class _Resultado:
    def __init__(self, filas):
        self._filas = list(filas)

    def fetchall(self):
        return self._filas


class _Conn:
    """Devuelve los feriados que tenga cargados `dbo.Feriados`."""
    def __init__(self, dias=()):
        self.dias = list(dias)

    def execute(self, stmt, params=None):
        return _Resultado([(d,) for d in self.dias])


# --------------------------------------------------------------- la unión

def test_la_libreria_aporta_los_puentes_que_la_tabla_no_tiene():
    """La tabla se carga a mano y nadie se acuerda de los puentes, que en
    Argentina se fijan por decreto cada año. Sobre 2026: la tabla tiene 16 días y
    la librería 19, y los tres de diferencia son puentes."""
    pytest.importorskip("holidays")

    dias = pd.feriados(_Conn([]), date(2026, 1, 1), date(2026, 12, 31))

    assert date(2026, 3, 23) in dias      # puente del 24 de marzo
    assert date(2026, 7, 10) in dias      # puente del 9 de julio
    assert date(2026, 12, 7) in dias      # puente del 8 de diciembre


def test_los_feriados_de_siempre_siguen_estando():
    pytest.importorskip("holidays")

    dias = pd.feriados(_Conn([]), date(2026, 1, 1), date(2026, 12, 31))

    for d in (date(2026, 1, 1), date(2026, 5, 1), date(2026, 7, 9),
              date(2026, 12, 25)):
        assert d in dias


def test_no_se_pierde_lo_que_alguien_cargo_a_mano():
    """La tabla puede tener días que la librería no conoce —un feriado
    provincial, un corte programado que la operación quiso tratar como feriado—.
    Cambiar de fuente en vez de sumar sería un retroceso."""
    propio = date(2026, 9, 21)

    dias = pd.feriados(_Conn([propio]), date(2026, 1, 1), date(2026, 12, 31))

    assert propio in dias


def test_no_se_repiten_los_que_estan_en_las_dos_fuentes():
    pytest.importorskip("holidays")
    navidad = date(2026, 12, 25)

    dias = pd.feriados(_Conn([navidad]), date(2026, 1, 1), date(2026, 12, 31))

    assert dias.count(navidad) == 1
    assert dias == sorted(dias)


def test_solo_los_del_periodo_pedido():
    pytest.importorskip("holidays")

    dias = pd.feriados(_Conn([]), date(2026, 6, 1), date(2026, 6, 30))

    assert all(date(2026, 6, 1) <= d <= date(2026, 6, 30) for d in dias)
    assert date(2026, 6, 20) in dias      # Belgrano


# ------------------------------------------------------------ falla blanda

def test_sin_la_libreria_el_planificador_sigue_andando(monkeypatch):
    """Import perezoso y falla blanda a propósito: un ImportError en el arranque
    dejaría sin pronóstico a toda la campaña por un calendario."""
    import builtins
    real = builtins.__import__

    def _sin_holidays(nombre, *a, **k):
        if nombre == "holidays":
            raise ImportError("no está")
        return real(nombre, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _sin_holidays)
    propio = date(2026, 5, 1)

    dias = pd.feriados(_Conn([propio]), date(2026, 1, 1), date(2026, 12, 31))

    assert dias == [propio]


def test_un_calendario_roto_no_voltea_la_corrida(monkeypatch):
    def _explota(*a, **k):
        raise RuntimeError("país inválido")

    import holidays
    monkeypatch.setattr(holidays, "country_holidays", _explota)

    dias = pd.feriados(_Conn([date(2026, 5, 1)]), date(2026, 1, 1), date(2026, 12, 31))

    assert dias == [date(2026, 5, 1)]


def test_el_pais_esta_declarado_y_no_cableado():
    """El día que haya una campaña de otro país esto pasa a ser un campo de
    `planificacion.Campana`; mientras tanto, al menos tiene nombre."""
    assert pd.PAIS_FERIADOS == "AR"
