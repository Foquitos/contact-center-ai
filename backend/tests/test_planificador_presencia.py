"""Tests del perfil de presencia por media hora (app/planificador_presencia.py) y de
cómo entra al shrinkage del dimensionamiento. Offline: sin base.

Fijan que el perfil REDISTRIBUYE el shrinkage de la jornada sin cambiarlo, que una
media hora con pocas muestras casi no se mueve, y que sin perfil el dimensionamiento
queda idéntico al de antes.
"""

from datetime import date, datetime, timedelta

from app import planificador as pl
from app import planificador_datos as pdatos
from app import planificador_presencia as pp

LUNES = date(2026, 9, 7)
SABADO = date(2026, 9, 12)


def _momento(dia, hh, mm=0):
    return datetime(dia.year, dia.month, dia.day, hh, mm)


def test_tipo_de_dia():
    assert pp.tipo_de_dia(LUNES) == "habil"
    assert pp.tipo_de_dia(SABADO) == "no_habil"
    assert pp.tipo_de_dia(LUNES, {LUNES}) == "no_habil"


def test_el_apartamiento_es_contra_la_presencia_media_del_tipo_de_dia():
    # A las 08:00 falta el 30% y a las 14:00 nadie: la media es 15%.
    turnos = {_momento(LUNES, 8): 1000, _momento(LUNES, 14): 1000}
    conectados = {_momento(LUNES, 8): 700, _momento(LUNES, 14): 1000}
    filas = {f["minuto"]: f for f in pp.medir_perfil(turnos, conectados, muestras_encogimiento=0)}
    assert filas[480]["faltante"] == 0.3 and filas[840]["faltante"] == 0.0
    assert filas[480]["exceso"] == 0.15 and filas[840]["exceso"] == -0.15


def test_la_jornada_descuenta_lo_mismo():
    """Ponderado por gente, el apartamiento de todo el día suma cero."""
    turnos, conectados = {}, {}
    for i, (personas, presentes) in enumerate([(10, 6), (40, 36), (60, 60), (50, 48), (20, 15)]):
        m = _momento(LUNES, 7) + timedelta(minutes=30 * i)
        turnos[m], conectados[m] = personas, presentes
    filas = pp.medir_perfil(turnos, conectados, muestras_encogimiento=0, tope=1)
    assert abs(sum(f["exceso"] * f["muestras"] for f in filas)) < 1e-2


def test_pocas_muestras_se_encogen_hacia_cero():
    turnos = {_momento(LUNES, 3): 20, _momento(LUNES, 14): 2000}
    conectados = {_momento(LUNES, 3): 10, _momento(LUNES, 14): 2000}
    filas = {f["minuto"]: f for f in pp.medir_perfil(turnos, conectados)}
    crudo = filas[180]["faltante"] - (1 - 2010 / 2020)
    assert 0 < filas[180]["exceso"] < crudo * 0.1


def test_tope_y_datos_que_no_cuentan():
    hoy = date(2026, 9, 15)
    turnos = {_momento(LUNES, 6): 1000, _momento(LUNES, 15): 1000,
              _momento(hoy, 6): 1000,            # hoy: el día no cerró
              _momento(LUNES, 9): 1000}          # sin dato de conexión
    conectados = {_momento(LUNES, 6): 0, _momento(LUNES, 15): 1000, _momento(hoy, 6): 0}
    filas = pp.medir_perfil(turnos, conectados, hoy=hoy, muestras_encogimiento=0)
    assert {f["minuto"] for f in filas} == {360, 900}
    assert max(f["exceso"] for f in filas) == pp.TOPE_EXCESO
    assert min(f["exceso"] for f in filas) == -pp.TOPE_EXCESO


def test_separa_habiles_de_no_habiles():
    turnos = {_momento(LUNES, 8): 100, _momento(SABADO, 8): 100}
    conectados = {_momento(LUNES, 8): 90, _momento(SABADO, 8): 50}
    tipos = {f["tipo_dia"] for f in pp.medir_perfil(turnos, conectados)}
    assert tipos == {"habil", "no_habil"}


# ------------------------------------------------------- en el dimensionamiento

def _cfg(**kw):
    return pl.CampanaCfg(campana_id=20, shrinkage=0.08, **kw)


def test_sin_perfil_el_shrinkage_es_el_de_siempre():
    cfg = _cfg(shrinkage_no_habil=0.10)
    assert cfg.shrinkage_del_dia(_momento(LUNES, 8)) == 0.08
    assert cfg.shrinkage_del_dia(_momento(SABADO, 8)) == 0.10


def test_el_perfil_suma_al_shrinkage_del_dia_por_media_hora():
    cfg = _cfg(shrinkage_no_habil=0.10, shrinkage_feriado=0.05,
               perfil_presencia={("habil", 360): 0.20, ("habil", 900): -0.05,
                                 ("no_habil", 360): 0.02})
    assert abs(cfg.shrinkage_del_dia(_momento(LUNES, 6)) - 0.28) < 1e-9
    assert abs(cfg.shrinkage_del_dia(_momento(LUNES, 15)) - 0.03) < 1e-9
    assert cfg.shrinkage_del_dia(_momento(LUNES, 11)) == 0.08          # sin dato: parejo
    assert abs(cfg.shrinkage_del_dia(_momento(SABADO, 6)) - 0.12) < 1e-9
    # Un feriado usa su shrinkage y el perfil de los no hábiles.
    cfg.feriados = frozenset({LUNES})
    assert abs(cfg.shrinkage_del_dia(_momento(LUNES, 6)) - 0.07) < 1e-9


def test_el_shrinkage_de_una_media_hora_queda_acotado():
    cfg = _cfg(perfil_presencia={("habil", 360): 0.9, ("habil", 900): -0.5})
    assert cfg.shrinkage_del_dia(_momento(LUNES, 6)) == 0.6
    assert cfg.shrinkage_del_dia(_momento(LUNES, 15)) == 0.0


def test_la_media_hora_con_mas_faltante_pide_mas_gente_a_citar():
    pool = pl.PoolCfg(pool_id=1, nombre="General", min_operadores=0)
    skill = pl.SkillCfg(6, "EMERGENCIAS", 1, 0.8, 20)
    base = dict(pools={1: pool}, skills=[skill])
    demanda = [pl.DemandaSkill(6, 120.0, 200.0)]
    parejo = pl.dimensionar_intervalo(_cfg(**base), pool, _momento(LUNES, 6), demanda)
    con_perfil = pl.dimensionar_intervalo(
        _cfg(perfil_presencia={("habil", 360): 0.2}, **base), pool, _momento(LUNES, 6), demanda)
    assert con_perfil.operadores_en_linea == parejo.operadores_en_linea
    assert con_perfil.operadores_a_planificar > parejo.operadores_a_planificar


def test_sin_la_migracion_el_perfil_viene_vacio(monkeypatch):
    monkeypatch.setattr(pdatos, "_tiene_tabla", lambda conn, t: False)
    assert pdatos.perfil_presencia(object(), 20) == ({}, None)


# ------------------------------------------------ quién cuenta como presente

def test_cubren_la_linea_el_pool_y_otras_pero_no_el_refuerzo():
    """La disponibilidad se mide contra todos los conectados, así que el faltante
    que tapa otra sub-campaña no se vuelve a citar. Digital no: es la palanca."""
    m = _momento(LUNES, 11)
    por_origen = {m: {"pool": 53.9, "refuerzo": 6.0, "otras": 2.2}}
    assert pp.conectados_que_cubren(por_origen) == {m: 53.9 + 2.2}


def test_las_digitales_que_no_son_palanca_siguen_cubriendo():
    """Clasificarlas como digitales no mueve la presencia: antes eran «otras»."""
    m = _momento(LUNES, 11)
    antes = pp.conectados_que_cubren({m: {"pool": 50.0, "refuerzo": 5.0, "otras": 3.0}})
    despues = pp.conectados_que_cubren(
        {m: {"pool": 50.0, "refuerzo": 5.0, "digital": 1.0, "otras": 2.0}})
    assert antes == despues == {m: 53.0}


def test_cubren_la_linea_tolera_grupos_vacios():
    m = _momento(LUNES, 3)
    assert pp.conectados_que_cubren({m: {"pool": None, "otras": 1.5}}) == {m: 1.5}


def test_el_nivel_se_pondera_por_muestras():
    """La madrugada, con pocas personas, no pesa lo mismo que el pico."""
    filas = [
        {"tipo_dia": "habil", "minuto": 180, "faltante": 0.40, "muestras": 10},
        {"tipo_dia": "habil", "minuto": 660, "faltante": 0.05, "muestras": 990},
        {"tipo_dia": "no_habil", "minuto": 660, "faltante": 0.12, "muestras": 50},
        {"tipo_dia": "no_habil", "minuto": 690, "faltante": None, "muestras": 50},
        {"tipo_dia": "no_habil", "minuto": 720, "faltante": 0.9, "muestras": 0},
    ]
    nivel = pp.nivel_por_tipo(filas)
    assert nivel["habil"] == {"faltante": 0.0535, "muestras": 1000}
    assert nivel["no_habil"] == {"faltante": 0.12, "muestras": 50}


def test_nivel_mas_perfil_devuelve_el_faltante_de_cada_media_hora():
    """Con la base igual al nivel medido, cada media hora descuenta su faltante
    (encogido): nivel y forma salen de la misma medición y no se suman dos veces."""
    turnos = {_momento(LUNES, 8): 1000, _momento(LUNES, 14): 3000}
    conectados = {_momento(LUNES, 8): 700, _momento(LUNES, 14): 2900}
    filas = pp.medir_perfil(turnos, conectados, muestras_encogimiento=0)
    nivel = pp.nivel_por_tipo(filas)["habil"]["faltante"]
    cfg = pl.CampanaCfg(campana_id=20, shrinkage=nivel, perfil_presencia=pp.como_perfil(filas))
    assert abs(cfg.shrinkage_del_dia(_momento(LUNES, 8)) - 0.30) < 1e-3
    assert abs(cfg.shrinkage_del_dia(_momento(LUNES, 14)) - 1 / 30) < 1e-3


def test_sin_la_migracion_no_hay_nivel_de_presencia(monkeypatch):
    monkeypatch.setattr(pdatos, "_tiene_tabla", lambda conn, t: False)
    assert pdatos.nivel_de_presencia(object(), 20) == {}


class _ConnPerfil:
    def __init__(self, filas):
        self.filas = filas

    def execute(self, *a, **k):
        filas = self.filas

        class _R:
            def mappings(self):
                return self

            def all(self):
                return filas
        return _R()


def _fila_guardada(tipo, faltante, muestras, medido_en):
    return {"TipoDia": tipo, "Faltante": faltante, "Muestras": muestras, "MedidoEn": medido_en}


def test_el_perfil_medido_solo_con_el_pool_no_se_propone_como_nivel(monkeypatch):
    """Antes del 16/09 el faltante era contra los conectados del pool (15,9% en
    hábiles): proponerlo le sumaría ocho puntos a la dotación."""
    monkeypatch.setattr(pdatos, "_tiene_tabla", lambda conn, t: True)
    viejo = datetime(2026, 9, 15, 16, 30)
    conn = _ConnPerfil([_fila_guardada("habil", 0.159, 100, viejo)])
    r = pdatos.nivel_de_presencia(conn, 20)
    assert "por_tipo" not in r and "motivo" in r


def test_el_perfil_nuevo_se_propone_como_nivel(monkeypatch):
    monkeypatch.setattr(pdatos, "_tiene_tabla", lambda conn, t: True)
    nuevo = datetime(2026, 9, 16, 16, 30)
    conn = _ConnPerfil([_fila_guardada("habil", 0.10, 300, nuevo),
                        _fila_guardada("habil", 0.02, 100, nuevo),
                        _fila_guardada("no_habil", 0.099, 50, nuevo)])
    r = pdatos.nivel_de_presencia(conn, 20)
    assert r["por_tipo"]["habil"]["faltante"] == 0.08
    assert r["por_tipo"]["no_habil"]["faltante"] == 0.099
    assert r["medido_en"] == nuevo
