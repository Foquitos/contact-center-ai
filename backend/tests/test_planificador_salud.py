"""Tests de la salud del plan (app/planificador_salud.py). Offline: sin base.

Fijan qué cuenta como atrasado en cada fuente, que un chequeo que falla no tumba a
los demás, y que toda migración del planificador tiene su señal registrada.
"""

import glob
import os
from datetime import date, datetime, timedelta

import pytest

from app import planificador_datos as pdatos
from app import planificador_salud as salud
from app.models import User
from app.routers import planificador_salud as router_salud

HOY = date(2026, 9, 15)
AHORA = datetime(2026, 9, 15, 14, 10)


# ------------------------------------------------------------ clasificación

def test_demanda_total_tiene_que_llegar_a_ayer():
    assert salud.clasificar_demanda_total(HOY, HOY)[0] == "ok"
    assert salud.clasificar_demanda_total(HOY - timedelta(days=1), HOY)[0] == "ok"
    estado, _, detalle = salud.clasificar_demanda_total(HOY - timedelta(days=4), HOY)
    assert estado == "atrasado" and "3 día" in detalle
    assert salud.clasificar_demanda_total(None, HOY)[0] == "falta"


def test_informe_skills_sin_llamadas_recientes_es_atraso_solo_en_horario():
    assert salud.clasificar_informe_skills(datetime(2026, 9, 15, 13, 0), AHORA)[0] == "ok"
    assert salud.clasificar_informe_skills(datetime(2026, 9, 15, 11, 30), AHORA)[0] == "atrasado"
    # A las 3 de la mañana una hora sin llamadas no dice nada.
    madrugada = datetime(2026, 9, 15, 3, 0)
    assert salud.clasificar_informe_skills(datetime(2026, 9, 14, 22, 0), madrugada)[0] == "ok"
    assert salud.clasificar_informe_skills(None, AHORA)[0] == "falta"


def test_registro_rrhh_malla_y_forecast():
    assert salud.clasificar_registro_rrhh(HOY - timedelta(days=1), HOY)[0] == "ok"
    assert salud.clasificar_registro_rrhh(HOY - timedelta(days=3), HOY)[0] == "atrasado"
    assert salud.clasificar_malla_publicada(HOY + timedelta(days=14), HOY)[0] == "ok"
    assert salud.clasificar_malla_publicada(HOY + timedelta(days=5), HOY)[0] == "atrasado"
    assert salud.clasificar_forecast_cliente(date(2026, 10, 31), HOY)[0] == "ok"
    assert salud.clasificar_forecast_cliente(HOY + timedelta(days=3), HOY)[0] == "atrasado"
    assert salud.clasificar_forecast_cliente(None, HOY)[0] == "falta"


def test_clima_pide_observado_y_pronostico():
    ayer, semana = HOY - timedelta(days=1), HOY + timedelta(days=10)
    assert salud.clasificar_clima(ayer, semana, HOY)[0] == "ok"
    estado, _, detalle = salud.clasificar_clima(HOY - timedelta(days=7), semana, HOY)
    assert estado == "atrasado" and "observado" in detalle.lower()
    estado, _, detalle = salud.clasificar_clima(ayer, HOY + timedelta(days=2), HOY)
    assert estado == "atrasado" and "pronóstico" in detalle
    assert salud.clasificar_clima(None, None, HOY)[0] == "falta"


def test_cortes_y_reparto():
    assert salud.clasificar_cortes_enre(datetime(2026, 9, 15, 14, 5), AHORA)[0] == "ok"
    assert salud.clasificar_cortes_enre(datetime(2026, 9, 15, 9, 0), AHORA)[0] == "atrasado"
    estado, _, detalle = salud.clasificar_reparto(["EMERGENCIAS", "CNR-EMPRESARIAL"], ["CNR-EMPRESARIAL"])
    assert estado == "falta" and "CNR-EMPRESARIAL" in detalle
    assert salud.clasificar_reparto(["EMERGENCIAS"], [])[0] == "ok"


def test_resumen_cuenta_cada_estado():
    fuentes = [{"estado": e} for e in ("ok", "atrasado", "falta", "falta", "error", "no_aplica")]
    migraciones = [{"aplicada": True}, {"aplicada": False}, {"aplicada": None}]
    assert salud.resumen_de_salud(fuentes, migraciones) == {
        "atrasadas": 1, "faltan": 2, "errores": 1, "migraciones_pendientes": 1}


# ------------------------------------------------------ aislamiento de errores

class _Conn:
    def __init__(self, escalar=None):
        self.escalar = escalar
        self.sql = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sql.append(str(sql))
        valor = self.escalar

        class _R:
            def scalar(self_):
                return valor
        return _R()


class _Engine:
    def __init__(self, escalar=None):
        self.conexiones = 0
        self.escalar = escalar

    def connect(self):
        self.conexiones += 1
        return _Conn(self.escalar)


def test_una_fuente_que_falla_no_tumba_a_las_demas(monkeypatch):
    def rota(conn, cid, ahora):
        raise RuntimeError("Invalid object name")

    monkeypatch.setattr(salud, "FUENTES", (
        ("rota", "Rota", rota),
        ("ajena", "Ajena", lambda conn, cid, ahora: None),
        ("sana", "Sana", lambda conn, cid, ahora: {
            "ultimo": datetime(2026, 9, 15, 13, 0), "estado": "ok", "esperado": "x", "detalle": ""}),
    ))
    engine = _Engine()
    fuentes = salud.verificar_fuentes(engine, 20, AHORA)

    assert [f["estado"] for f in fuentes] == ["error", "no_aplica", "ok"]
    assert "Invalid object name" in fuentes[0]["detalle"]
    assert fuentes[2]["ultimo"] == "2026-09-15 13:00"
    # Cada chequeo en su propia conexión: una consulta fallida no contamina a la siguiente.
    assert engine.conexiones == 3


def test_migracion_que_no_se_puede_verificar_queda_en_none(monkeypatch):
    def explota(conn):
        raise RuntimeError("sin permisos")

    monkeypatch.setattr(salud, "REGISTRO_MIGRACIONES", [
        {"archivo": "a.sql", "senal": "s", "verificar": lambda conn: True},
        {"archivo": "b.sql", "senal": "s", "verificar": lambda conn: False},
        {"archivo": "c.sql", "senal": "s", "verificar": explota},
    ])
    r = salud.verificar_migraciones(_Engine())
    assert [m["aplicada"] for m in r] == [True, False, None]


def test_consulta_de_datos_compara_contra_el_esperado(monkeypatch):
    monkeypatch.setattr(pdatos, "_tiene_tabla", lambda conn, t: True)
    assert salud._consulta("SELECT 1", 0)(_Conn(escalar=0)) is True
    assert salud._consulta("SELECT 1", 0)(_Conn(escalar=3)) is False
    monkeypatch.setattr(pdatos, "_tiene_tabla", lambda conn, t: False)
    assert salud._consulta("SELECT 1", 0, requiere="planificacion.Pool")(_Conn(escalar=0)) is False


def test_el_check_del_07_09_matchea_la_definicion_real_de_sql_server():
    # La definición guardada es «([Porcentaje]>=(0) AND [Porcentaje]<=(1))»: un
    # LIKE con espacio nunca la encontraba y la migración figuraba pendiente.
    m = next(x for x in salud.REGISTRO_MIGRACIONES
             if x["archivo"] == "2026-09-07_planificador_demanda_total.sql")
    conn = _Conn(escalar=1)
    assert m["verificar"](conn) is True
    assert "'%>=(0)%'" in conn.sql[0]


def test_todas_las_migraciones_del_planificador_estan_registradas():
    carpeta = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "migrations")
    archivos = {os.path.basename(p) for p in glob.glob(os.path.join(carpeta, "*planificador*.sql"))}
    if not archivos:
        pytest.skip("scripts/migrations no está en esta máquina (va por fuera de git)")
    registradas = {m["archivo"] for m in salud.REGISTRO_MIGRACIONES}
    assert archivos - registradas == set()


def test_diagnostico_cachea_y_se_puede_forzar(monkeypatch):
    llamadas = []
    monkeypatch.setattr(salud, "verificar_fuentes",
                        lambda eng, cid, ahora: llamadas.append(cid) or [{"estado": "ok"}])
    monkeypatch.setattr(salud, "verificar_migraciones", lambda eng: [{"aplicada": False}])
    salud._CACHE.clear()

    r1 = salud.diagnostico_salud(object(), 20)
    r2 = salud.diagnostico_salud(object(), 20)
    assert r1 is r2 and len(llamadas) == 1
    assert r1["resumen"]["migraciones_pendientes"] == 1
    salud.diagnostico_salud(object(), 20, usar_cache=False)
    assert len(llamadas) == 2
    # Con un «ahora» explícito (tests, backfill) no se usa ni se escribe el cache.
    salud.diagnostico_salud(object(), 20, ahora=AHORA)
    assert len(llamadas) == 3


def test_endpoint_devuelve_el_diagnostico(monkeypatch):
    monkeypatch.setattr(router_salud, "exigir_acceso_empresa", lambda *a, **k: None)
    monkeypatch.setattr(router_salud, "engine", _Engine())
    monkeypatch.setattr(router_salud.salud, "diagnostico_salud",
                        lambda eng, cid: {"fuentes": [], "migraciones": [], "resumen": {"campana": cid}})
    user = User(usuario=1, permissions=["planificador.view"])
    r = router_salud.obtener_salud(campana_id=20, current_user=user)
    assert r["resumen"]["campana"] == 20
