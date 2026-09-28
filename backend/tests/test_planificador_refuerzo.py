"""Tests de Digital como PALANCA de la línea.

Por qué existe este archivo. Digital es back office sin SLA y su gente se puede
pasar al teléfono cuando la malla no alcanza (la operación, 2026-09-14: pasan
todas menos Agrupadas - Digital y Ajustes - Lecturas). Ya pasa: medido 17/08-10/09,
día hábil de 10 a 12 h hay 4,5 equivalentes de Digital en la línea de ~13 que
tienen turno.

Las invariantes que se defienden acá:

  - la palanca NO cambia lo que hay que citar: sólo dice cuánto del faltante se
    tapa y cuánto falta igual;
  - nunca cubre más de lo que falta ni de la gente que hay;
  - la gente de Digital se cuenta con la misma definición que los citados (sus
    propias sub-campañas, los mismos puestos, personas y no filas);
  - los conectados partidos por origen suman lo mismo que los conectados.

Offline: no toca ni BD ni red.

Correr: pytest tests/test_planificador_refuerzo.py -m "not tokens"
"""
from datetime import datetime, timedelta

import pytest

from app import planificador as pl
from app import planificador_datos as pd
from app import planificador_servicio as ps


# ------------------------------------------------------------------ andamiaje

class _Resultado:
    def __init__(self, filas):
        self._filas = list(filas)

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self._filas)

    def all(self):
        return self._filas

    def fetchall(self):
        return self._filas


class _Conn:
    def __init__(self, filas=()):
        self.filas = list(filas)
        self.sql = []
        self.params = []

    def execute(self, stmt, params=None):
        self.sql.append(str(stmt))
        self.params.append(params)
        return _Resultado(self.filas)


M = datetime(2026, 9, 9, 11, 0)


def _req(a_planificar=10, citados=None, refuerzo=None, momento=M):
    r = pl.RequerimientoPool(
        pool_id=1, momento=momento, llamadas=300.0, tmo_seg=300.0, trafico=50.0,
        operadores_en_linea=8, operadores_presentes=9,
        operadores_a_planificar=a_planificar, nds_contractual=0.8,
        nds_sobre_atendidas=0.8, nds_sobre_entrantes=0.78, abandono=0.02,
        ocupacion=0.8, asa_seg=15.0, nivel_atencion_b=0.98,
        motivo="nivel de servicio", disponibilidad=0.9)
    r.planificados = citados
    r.refuerzo_disponible = refuerzo
    return r


# ------------------------------------------------------------ la cuenta en sí

def test_digital_cubre_hasta_lo_que_tiene():
    r = _req(a_planificar=10, citados=6, refuerzo=3)

    assert r.brecha == -4
    assert r.refuerzo_cubre == 3
    assert r.faltante_neto == 1


def test_digital_no_cubre_mas_de_lo_que_falta():
    r = _req(a_planificar=10, citados=6, refuerzo=8)

    assert r.refuerzo_cubre == 4
    assert r.faltante_neto == 0


def test_si_sobra_gente_digital_no_cubre_nada():
    r = _req(a_planificar=10, citados=12, refuerzo=5)

    assert r.refuerzo_cubre == 0
    assert r.faltante_neto == 0


def test_la_palanca_no_cambia_lo_que_hay_que_citar():
    """Contar con Digital al citar dejaría a Digital sin gente todos los días: la
    palanca es para cuando no se llega, no un descuento de la malla."""
    sin = _req(a_planificar=10, citados=6)
    con = _req(a_planificar=10, citados=6, refuerzo=5)

    assert con.operadores_a_planificar == sin.operadores_a_planificar
    assert con.brecha == sin.brecha


@pytest.mark.parametrize("citados,refuerzo", [(None, 3), (6, None)])
def test_sin_malla_o_sin_refuerzo_no_hay_cuenta(citados, refuerzo):
    r = _req(citados=citados, refuerzo=refuerzo)

    assert r.refuerzo_cubre is None
    assert r.faltante_neto is None


def test_el_faltante_neto_viaja_ya_calculado_en_la_lectura():
    assert pd._con_faltante_neto({"Brecha": -5, "RefuerzoCubre": 3})["FaltanteNeto"] == 2
    assert pd._con_faltante_neto({"Brecha": 2, "RefuerzoCubre": 0})["FaltanteNeto"] == 0
    assert pd._con_faltante_neto({"Brecha": -5, "RefuerzoCubre": None})["FaltanteNeto"] is None


# ------------------------------------------------------ el resumen de la brecha

def test_el_resumen_parte_el_faltante_en_lo_que_tapa_digital_y_lo_que_no():
    paso = timedelta(minutes=30)
    reqs = [_req(10, 6, 3, M),               # faltan 4: cubre 3, faltan igual 1
            _req(10, 8, 5, M + paso),        # faltan 2: cubre 2
            _req(10, 11, 2, M + 2 * paso)]   # sobra 1

    b = ps._resumen_de_brecha(reqs)

    assert b["con_refuerzo"] is True
    assert b["horas_operador_faltantes"] == 3.0
    assert b["horas_operador_cubre_refuerzo"] == 2.5
    assert b["horas_operador_faltantes_netas"] == 0.5
    assert b["intervalos_sin_cubrir"] == 1
    assert b["operadores_faltantes_netos_pico"] == 1


def test_sin_refuerzo_el_resumen_queda_como_estaba():
    b = ps._resumen_de_brecha([_req(10, 6, None)])

    assert "con_refuerzo" not in b
    assert b["horas_operador_faltantes"] == 2.0


# ----------------------------------------------------------- lectura de la base

def test_sin_la_migracion_no_hay_refuerzo(monkeypatch):
    monkeypatch.setattr(pd, "_tiene_tabla", lambda c, t: t != "planificacion.PoolRefuerzo")
    conn = _Conn()

    assert pd.campanas_refuerzo_del_pool(conn, 1) == []
    assert conn.sql == []


def test_el_refuerzo_se_cuenta_sobre_sus_sub_campanas_y_no_las_del_pool(monkeypatch):
    """Misma definición que los citados pero con OTRA lista de sub-campañas."""
    monkeypatch.setattr(pd, "_tiene_tabla", lambda c, t: True)
    monkeypatch.setattr(pd, "campanas_rrhh_del_pool", lambda c, p: [100, 106])
    monkeypatch.setattr(pd, "campanas_refuerzo_del_pool", lambda c, p: [53, 132])
    conn = _Conn([(7, M, M + timedelta(hours=1)),
                  (8, M, M + timedelta(minutes=30))])

    n = pd.refuerzo_planificado(conn, 1, M.date(), M.date() + timedelta(days=1))

    assert conn.params[-1]["campanas"] == [53, 132]
    assert "payroll_futuro" in conn.sql[-1]
    assert n == {M: 2, M + timedelta(minutes=30): 1}


def test_el_refuerzo_real_sale_del_registro(monkeypatch):
    monkeypatch.setattr(pd, "_tiene_tabla", lambda c, t: True)
    monkeypatch.setattr(pd, "campanas_refuerzo_del_pool", lambda c, p: [53])
    conn = _Conn([(7, M, M + timedelta(minutes=30))])

    assert pd.refuerzo_real(conn, 1, M.date(), M.date() + timedelta(days=1)) == {M: 1}
    assert "dbo.payroll p" in conn.sql[-1]


def test_sin_sub_campanas_de_refuerzo_no_consulta_el_payroll(monkeypatch):
    monkeypatch.setattr(pd, "campanas_refuerzo_del_pool", lambda c, p: [])
    conn = _Conn()

    assert pd.refuerzo_planificado(conn, 1, M.date(), M.date()) == {}
    assert conn.sql == []


def test_los_conectados_se_parten_por_la_sub_campana_de_ese_dia(monkeypatch):
    monkeypatch.setattr(pd, "campanas_rrhh_del_pool", lambda c, p: [100, 106])
    monkeypatch.setattr(pd, "campanas_refuerzo_del_pool", lambda c, p: [53])
    monkeypatch.setattr(pd, "subcampanas_del_pool", lambda c, p, k: [])
    conn = _Conn([
        {"momento": M, "campana_id": 100, "agentes": 30.0},
        {"momento": M, "campana_id": 106, "agentes": 5.5},
        {"momento": M, "campana_id": 53, "agentes": 4.5},
        {"momento": M, "campana_id": 58, "agentes": 2.0},    # Backoffice RRSS
        {"momento": M, "campana_id": None, "agentes": 0.5},  # sin payroll ese día
    ])

    g = pd.conectados_por_origen(conn, pd.CAMPANA_VOLTARA, [1], M.date(), M.date())

    assert g[M] == {"pool": 35.5, "refuerzo": 4.5, "digital": 0.0, "dedicada": 0.0,
                    "otras": 2.5}
    assert sum(g[M].values()) == pytest.approx(42.5)


def test_en_linea_son_los_mismos_conectados_sin_las_pausas(monkeypatch):
    """El que está en break está logueado pero no atiende: sale de la línea. Por
    defecto no viene, para no cambiarle la cuenta a la presencia contra turnos."""
    monkeypatch.setattr(pd, "campanas_rrhh_del_pool", lambda c, p: [100])
    monkeypatch.setattr(pd, "campanas_refuerzo_del_pool", lambda c, p: [53])
    monkeypatch.setattr(pd, "subcampanas_del_pool", lambda c, p, k: [])
    filas = [
        {"momento": M, "campana_id": 100, "agentes": 30.0, "agentes_en_linea": 26.5},
        {"momento": M, "campana_id": 53, "agentes": 4.5, "agentes_en_linea": 4.0},
        {"momento": M, "campana_id": 58, "agentes": 2.0, "agentes_en_linea": None},
    ]

    g = pd.conectados_por_origen(_Conn(filas), pd.CAMPANA_VOLTARA, [1], M.date(), M.date(),
                                 con_en_linea=True)
    assert g[M] == {"pool": 30.0, "refuerzo": 4.5, "digital": 0.0, "dedicada": 0.0,
                    "otras": 2.0, "pool_en_linea": 26.5, "refuerzo_en_linea": 4.0,
                    "digital_en_linea": 0.0, "dedicada_en_linea": 0.0,
                    "otras_en_linea": 0.0}

    solo = pd.conectados_por_origen(_Conn(filas), pd.CAMPANA_VOLTARA, [1], M.date(), M.date())
    assert set(solo[M]) == {"pool", "refuerzo", "digital", "dedicada", "otras"}


def test_la_consulta_resta_la_pausa_del_logueo():
    sql = pd.FuenteVoltara.CONECTADOS_POR_CAMPANA
    assert "[Tiempo Agentes en pausa]" in sql
    assert "AS agentes_en_linea" in sql
    # Nunca negativo: una pausa mayor que el logueo cuenta cero, no resta.
    assert "ELSE 0 END" in sql


def test_la_consulta_de_origen_deduplica_por_persona_como_el_tablero():
    """El informe trae una fila por skill: sin el MAX por persona e intervalo la
    suma triplica, y dejaría de coincidir con `CONECTADOS`."""
    sql = pd.FuenteVoltara.CONECTADOS_POR_CAMPANA

    assert "MAX(CAST(a.[Tiempo Agentes Logueados] AS float))" in sql
    assert "GROUP BY a.Intervalo, u.nomina_id" in sql
    assert "/ 1800.0" in sql
    # La sub-campaña de ESE día, no la actual.
    assert "dbo.payroll p" in sql and "p.fecha IN" in sql


def test_las_telefonicas_parciales_cuentan_del_pool_y_las_digitales_aparte(monkeypatch):
    """Contingencia/SVP/Anfitrión conectados están atendiendo: son telefónicos. Las
    digitales que no son palanca van a su grupo, no al del refuerzo."""
    monkeypatch.setattr(pd, "campanas_rrhh_del_pool", lambda c, p: [100])
    monkeypatch.setattr(pd, "campanas_refuerzo_del_pool", lambda c, p: [53])
    clases = {"telefonica_parcial": [88, 146, 178], "digital": [131, 58], "dedicada": [177]}
    monkeypatch.setattr(pd, "subcampanas_del_pool", lambda c, p, k: clases[k])
    conn = _Conn([
        {"momento": M, "campana_id": 100, "agentes": 30.0},
        {"momento": M, "campana_id": 88, "agentes": 4.0},
        {"momento": M, "campana_id": 53, "agentes": 3.0},
        {"momento": M, "campana_id": 131, "agentes": 1.0},
        {"momento": M, "campana_id": 177, "agentes": 2.0},   # T1 - Consumo: dedicada
        {"momento": M, "campana_id": 999, "agentes": 0.5},
    ])

    g = pd.conectados_por_origen(conn, pd.CAMPANA_VOLTARA, [1], M.date(), M.date())

    assert g[M] == {"pool": 34.0, "refuerzo": 3.0, "digital": 1.0, "dedicada": 2.0,
                    "otras": 0.5}


def test_sin_la_migracion_no_hay_sub_campanas_clasificadas(monkeypatch):
    monkeypatch.setattr(pd, "_tiene_tabla", lambda c, t: False)
    assert pd.subcampanas_del_pool(_Conn(), 1, "telefonica_parcial") == []
    with pytest.raises(ValueError):
        pd.subcampanas_del_pool(_Conn(), 1, "inventada")


def test_turnos_parciales_cuentan_solo_con_turno_y_logueado(monkeypatch):
    """La consulta exige las dos cosas: turno en el registro y logueo en la línea."""
    sql = pd.FuenteVoltara.TURNOS_PARCIALES_EN_LINEA
    assert "[Tiempo Agentes Logueados] > 0" in sql
    assert "t.documento = l.nomina_id" in sql
    assert "l.Intervalo >= t.desde30 AND l.Intervalo < t.final" in sql
    monkeypatch.setattr(pd, "subcampanas_del_pool", lambda c, p, k: [])
    assert pd.dotacion_parcial_real(_Conn(), pd.CAMPANA_VOLTARA, 1, M.date(), M.date()) == {}


def test_una_sub_campana_en_los_dos_lados_cuenta_como_del_pool(monkeypatch):
    monkeypatch.setattr(pd, "campanas_rrhh_del_pool", lambda c, p: [100])
    monkeypatch.setattr(pd, "campanas_refuerzo_del_pool", lambda c, p: [100])
    monkeypatch.setattr(pd, "subcampanas_del_pool", lambda c, p, k: [])
    conn = _Conn([{"momento": M, "campana_id": 100, "agentes": 10.0}])

    g = pd.conectados_por_origen(conn, pd.CAMPANA_VOLTARA, [1], M.date(), M.date())

    assert g[M]["pool"] == 10.0 and g[M]["refuerzo"] == 0.0


# ------------------------------------------------------------ guardar la corrida

def test_la_corrida_guarda_con_que_refuerzo_contaba(monkeypatch):
    monkeypatch.setattr(pd, "_tiene_columna", lambda c, t, col: True)
    conn = _Conn()

    pd.guardar_requerimiento(conn, 64, [_req(10, 6, 3)])

    assert "RefuerzoDisponible, RefuerzoCubre" in conn.sql[-1]
    fila = conn.params[-1][0]
    assert fila["ref_disp"] == 3 and fila["ref_cubre"] == 3


def test_sin_la_migracion_la_corrida_se_guarda_igual(monkeypatch):
    monkeypatch.setattr(pd, "_tiene_columna",
                        lambda c, t, col: col != "RefuerzoDisponible")
    conn = _Conn()

    pd.guardar_requerimiento(conn, 64, [_req(10, 6, 3)])

    assert "Refuerzo" not in conn.sql[-1]
    assert "ref_disp" not in conn.params[-1][0]
