"""Tests de la disponibilidad medida, y sobre todo de su TOPE.

Por qué existe este archivo. La disponibilidad se estima invirtiendo el nivel de
servicio observado: para cada intervalo se busca la dotación mínima que explicaría
el NDS que efectivamente se logró, y se la divide por la gente que estuvo
logueada. El cociente es el factor.

Ese cociente puede pasar de 1, y pasa: medido sobre Voltara, 90 días, en el 26% de
los intervalos útiles. No significa "disponibilidad mejor que perfecta" —eso no
existe— sino que el modelo necesita más operadores de los que hubo para explicar
un servicio que igual se logró. Es el límite de Erlang: supone atenciones
exponenciales y llegadas de Poisson dentro del intervalo, y con pocos operadores
las dos cosas son pesimistas. El reparto lo confirma: 6-9% de intervalos sin
explicar entre las 9 y las 12, contra 50-70% de madrugada.

La invariante que se defiende acá: NINGÚN número que la pantalla presente como
disponibilidad puede pasar de 100%, y la proporción de intervalos donde el modelo
no llega se informa aparte, que es su única lectura honesta.

Offline: no toca ni BD ni red.

Correr: pytest tests/test_planificador_disponibilidad.py -m "not tokens"
"""
from datetime import date, datetime

import pytest

from app import planificador as pl
from app import planificador_datos as pd


class _Resultado:
    def __init__(self, filas):
        self._filas = list(filas)

    def mappings(self):
        return self

    def all(self):
        return self._filas


class _Conn:
    def __init__(self, filas=()):
        self.filas = list(filas)

    def execute(self, stmt, params=None):
        return _Resultado(self.filas)


def _cfg(break_min=0.0, paciencia=800):
    cfg = pl.CampanaCfg(campana_id=pd.CAMPANA_VOLTARA, intervalo_min=30,
                        paciencia_seg=paciencia)
    cfg.break_min_por_hora = break_min
    cfg.skills = [pl.SkillCfg(skill_id=1, nombre="CNR", pool_id=1)]
    cfg.pools = {1: pl.PoolCfg(pool_id=1, nombre="General")}
    return cfg


def _fila(hora, entrantes, atendidas, hasta_20s, tmo, agentes, dia=1):
    return {"momento": datetime(2026, 6, dia, hora, 0),
            "entrantes": entrantes, "atendidas": atendidas,
            "hasta_20s": hasta_20s, "tmo_x_llamadas": tmo * atendidas,
            "agentes": agentes}


# Intervalo holgado: mucha gente para el tráfico que hubo, y aun así un NDS
# mediocre. El modelo lo explica con menos gente de la que estuvo -> cociente < 1.
SOBRAN = _fila(10, 200, 200, 150, 300, 60)
# Intervalo apretado: poca gente y un NDS que el modelo no alcanza a explicar
# ni con todos ellos -> cociente > 1, que es el caso que se tapa.
FALTAN = _fila(11, 200, 195, 190, 300, 34)


def test_el_factor_nunca_pasa_de_uno():
    d = pd.estimar_disponibilidad(_Conn([FALTAN] * 5), _cfg(), 1,
                                  date(2026, 6, 1), date(2026, 9, 1))
    assert d["factor"] <= 1.0


def test_los_percentiles_tampoco_pasan_de_uno():
    """El p75 era lo que se veía en pantalla como 106%: un percentil del cociente
    crudo, sin topar. Un 106% de disponibilidad no quiere decir nada."""
    filas = [SOBRAN] + [FALTAN] * 9
    d = pd.estimar_disponibilidad(_Conn(filas), _cfg(), 1,
                                  date(2026, 6, 1), date(2026, 9, 1))
    assert d["p25"] <= 1.0
    assert d["p75"] <= 1.0


def test_informa_que_proporcion_de_intervalos_no_explica():
    """Topar sin decirlo esconde el problema: la proporción de intervalos donde el
    modelo no llega es el dato que permite descartar una hora a ojo."""
    filas = [SOBRAN] * 6 + [FALTAN] * 4
    d = pd.estimar_disponibilidad(_Conn(filas), _cfg(), 1,
                                  date(2026, 6, 1), date(2026, 9, 1))
    assert d["sin_explicar"] == pytest.approx(0.4, abs=0.001)


def test_sin_intervalos_sin_explicar_el_aviso_queda_en_cero():
    d = pd.estimar_disponibilidad(_Conn([SOBRAN] * 8), _cfg(), 1,
                                  date(2026, 6, 1), date(2026, 9, 1))
    assert d["sin_explicar"] == 0.0
    assert d["factor"] < 1.0


def test_cada_hora_dice_cuanto_no_explica(monkeypatch):
    """La hora con muchos intervalos sin explicar no es una hora con buena
    disponibilidad: es una hora donde este método no mide. Si se informa 100% sin
    decirlo, se carga una franja que parece medida y no lo está."""
    monkeypatch.setattr(pd, "MIN_MUESTRAS_POR_HORA", 4)
    filas = [SOBRAN] * 5 + [FALTAN] * 5
    d = pd.estimar_disponibilidad(_Conn(filas), _cfg(), 1,
                                  date(2026, 6, 1), date(2026, 9, 1))
    por_hora = {h["hora"]: h for h in d["por_hora"]}
    assert por_hora[10]["sin_explicar"] == 0.0
    assert por_hora[11]["sin_explicar"] == 1.0
    assert por_hora[11]["factor_medido"] <= 1.0
