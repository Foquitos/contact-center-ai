"""Tests del módulo Escenarios del Planificador.

Verifica:
  - sin cambios: escenario == base;
  - más volumen: horas_operador >= base;
  - techo de ocupación más alto: horas_operador <= base;
  - la conexión falsa verifica que NUNCA se ejecuta un INSERT/UPDATE/DELETE;
  - validaciones de rangos y fechas devuelven 400 (HTTPException / ValueError).

Offline: no toca base de datos ni red.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Sequence

import pytest
from fastapi import HTTPException

from app import planificador as pl
from app import planificador_datos as pdatos
from app import planificador_escenarios as pesc
from app.models import User
from app.routers import planificador_escenarios as router_esc


class _ResultadoFalso:
    def __init__(self, filas: Sequence[dict] = ()):
        self._filas = list(filas)

    def mappings(self):
        return self

    def fetchone(self):
        return self._filas[0] if self._filas else None

    def all(self):
        return list(self._filas)

    def __iter__(self):
        return iter(self._filas)


class _ConnFalsa:
    """Conexión simulada que registra todas las sentencias y comprueba que NUNCA
    se intente ejecutar una sentencia de escritura."""

    def __init__(self):
        self.queries: List[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, stmt: Any, params: Optional[dict] = None):
        sql = str(stmt).strip().upper()
        self.queries.append(sql)

        for verbo in ("INSERT", "UPDATE", "DELETE", "MERGE", "DROP", "ALTER", "CREATE"):
            if sql.startswith(verbo) or f" {verbo} " in sql:
                raise AssertionError(f"Violación de sólo lectura en escenario: {sql}")

        return _ResultadoFalso([])


class _EngineFalso:
    def __init__(self, conn: Optional[_ConnFalsa] = None):
        self._conn = conn or _ConnFalsa()

    def connect(self):
        return self._conn


def _usuario_falso() -> User:
    return User(usuario=1, permissions=["planificador.view"])


def _crear_cfg_base() -> pl.CampanaCfg:
    cfg = pl.CampanaCfg(
        campana_id=20,
        intervalo_min=30,
        max_ocupacion=0.85,
        shrinkage=0.30,
        paciencia_seg=800,
        break_min_por_hora=0.0,
    )
    skill = pl.SkillCfg(
        skill_id=1,
        nombre="Emergencias",
        pool_id=10,
        objetivo_nds=0.80,
        umbral_seg=20,
        max_abandono=0.05,
        paciencia_seg=850,
        activo=True,
    )
    pool = pl.PoolCfg(
        pool_id=10,
        nombre="Pool Telefónico",
        min_operadores=0,
        activo=True,
        origen_rrhh=[1],
    )
    cfg.skills = [skill]
    cfg.pools = {10: pool}
    cfg.disponibilidad = [
        pl.FranjaDisponibilidad(dia_semana=0, hora_desde=0, hora_hasta=24, factor=1.0)
    ]
    return cfg


def _momentos_dias(desde_dia: date, dias: int = 3) -> List[datetime]:
    momentos = []
    for i in range(dias):
        dia_actual = desde_dia + timedelta(days=i)
        for hora in range(8, 12):
            for minuto in (0, 30):
                momentos.append(datetime.combine(dia_actual, time(hora, minuto)))
    return momentos


@pytest.fixture
def ambiente_escenarios(monkeypatch):
    """Configura el entorno de datos simulado para pruebas de escenarios."""
    conn_falsa = _ConnFalsa()
    engine_falso = _EngineFalso(conn_falsa)

    dia_inicio = date(2026, 9, 15)
    dia_fin = date(2026, 9, 17)
    momentos = _momentos_dias(dia_inicio, dias=3)

    corrida_info = {
        "CorridaID": 100,
        "CampanaID": 20,
        "Horizonte": "operativo",
        "Desde": dia_inicio,
        "Hasta": dia_fin,
        "Modelo": "baseline-estacional-v1",
        "Metricas": None,
    }

    filas_pron = [
        {
            "SkillID": 1,
            "Intervalo": m,
            "LlamadasTotal": 120.0,
            "Asignacion": 0.5,
            "LlamadasAcme": 60.0,
            "LlamadasBase": 60.0,
            "TmoSeg": 240.0,
        }
        for m in momentos
    ]

    cfg_base = _crear_cfg_base()
    # Dimensionamos una vez para tener los mismos valores guardados en la corrida
    demanda_inicial = {
        m: [pl.DemandaSkill(1, 60.0, 240.0)]
        for m in momentos
    }
    pool = cfg_base.pools[10]
    reqs_iniciales = pl.plan_de_pool(cfg_base, pool, demanda_inicial)

    filas_req = [
        {
            "PoolID": 10,
            "Intervalo": r.momento,
            "Llamadas": r.llamadas,
            "TmoSeg": r.tmo_seg,
            "OperadoresLinea": r.operadores_en_linea,
            "OperadoresPlanificar": r.operadores_a_planificar,
            "OperadoresPlanificados": r.operadores_a_planificar - 1,  # 1 faltante
            "RefuerzoDisponible": 0,
        }
        for r in reqs_iniciales
    ]

    monkeypatch.setattr(pdatos, "schema_disponible", lambda c: bool((c.execute("SELECT 1 FROM sys.schemas WHERE name = 'planificacion'"), True)[1]))
    monkeypatch.setattr(pdatos, "corrida_vigente", lambda c, camp, hor="operativo": (c.execute("SELECT * FROM planificacion.Corrida WHERE CampanaID = :c", {"c": camp}), dict(corrida_info))[1])
    monkeypatch.setattr(pdatos, "cargar_config", lambda c, camp: (c.execute("SELECT * FROM planificacion.Campana WHERE CampanaID = :c", {"c": camp}), _crear_cfg_base())[1])
    monkeypatch.setattr(pdatos, "feriados", lambda c, d, h, *_: (c.execute("SELECT * FROM dbo.feriados"), [])[1])
    monkeypatch.setattr(pdatos, "leer_pronostico", lambda c, cid: (c.execute("SELECT * FROM planificacion.Pronostico WHERE CorridaID = :id", {"id": cid}), list(filas_pron))[1])
    monkeypatch.setattr(pdatos, "leer_requerimiento", lambda c, cid: (c.execute("SELECT * FROM planificacion.Requerimiento WHERE CorridaID = :id", {"id": cid}), list(filas_req))[1])

    monkeypatch.setattr(router_esc, "engine", engine_falso)
    monkeypatch.setattr(router_esc, "_verificar_acceso", lambda camp, user: None)

    return {
        "conn": conn_falsa,
        "engine": engine_falso,
        "cfg_base": cfg_base,
        "filas_pron": filas_pron,
        "filas_req": filas_req,
        "momentos": momentos,
    }


# ============================================================================
# TESTS DE LÓGICA Y CONEXIÓN FALSA
# ============================================================================

def test_sin_cambios_escenario_igual_a_base(ambiente_escenarios):
    """Sin cambios, la corrida base y el escenario deben ser idénticos."""
    conn = ambiente_escenarios["conn"]
    resultado = pesc.calcular_escenario(conn, campana_id=20, cambios={})

    res = resultado["resumen"]
    base = res["base"]
    esc = res["escenario"]
    dif = res["diferencia"]

    assert base["horas_operador"] == esc["horas_operador"]
    assert base["pico_operadores"] == esc["pico_operadores"]
    assert dif["horas_operador"] == 0.0
    assert dif["pico_operadores"] == 0

    assert resultado["base_coincide_con_corrida"] >= 0.99
    assert len(resultado["por_dia"]) == 3
    for d in resultado["por_dia"]:
        assert d["horas_base"] == d["horas_escenario"]
        assert d["faltante_base"] == d["faltante_escenario"]


def test_mas_volumen_horas_mayor_o_igual_a_base(ambiente_escenarios):
    """Un incremento en el factor de volumen debe requerir horas >= base."""
    conn = ambiente_escenarios["conn"]
    resultado = pesc.calcular_escenario(
        conn,
        campana_id=20,
        cambios={"factor_volumen": 1.4},
    )

    res = resultado["resumen"]
    base = res["base"]
    esc = res["escenario"]
    dif = res["diferencia"]

    assert esc["horas_operador"] >= base["horas_operador"]
    assert esc["pico_operadores"] >= base["pico_operadores"]
    assert dif["horas_operador"] > 0
    assert dif["horas_operador_pct"] > 0

    for d in resultado["por_dia"]:
        assert d["horas_escenario"] >= d["horas_base"]


def test_techo_ocupacion_mas_alto_horas_menor_o_igual_a_base(ambiente_escenarios):
    """Un techo de ocupación más relajado (más alto) permite exigir menos dotación."""
    conn = ambiente_escenarios["conn"]
    resultado = pesc.calcular_escenario(
        conn,
        campana_id=20,
        cambios={"max_ocupacion": 0.95},
    )

    res = resultado["resumen"]
    base = res["base"]
    esc = res["escenario"]
    dif = res["diferencia"]

    assert esc["horas_operador"] <= base["horas_operador"]
    assert dif["horas_operador"] <= 0


def test_conexion_falsa_verifica_sin_escrituras(ambiente_escenarios):
    """Comprueba que calcular_escenario no ejecutó ningún INSERT/UPDATE/DELETE
    y que la conexión simulada intercepta y rechaza activamente cualquier mutación."""
    conn = ambiente_escenarios["conn"]
    pesc.calcular_escenario(
        conn,
        campana_id=20,
        cambios={"factor_volumen": 1.2, "max_ocupacion": 0.90},
    )

    # Debe haber ejecutado consultas de sólo lectura
    assert len(conn.queries) > 0
    for q in conn.queries:
        for verbo in ("INSERT", "UPDATE", "DELETE", "MERGE", "DROP", "ALTER"):
            assert verbo not in q

    # La conexión falsa debe levantar AssertionError ante cualquier intento de escritura
    with pytest.raises(AssertionError, match="Violación de sólo lectura"):
        conn.execute("INSERT INTO planificacion.Corrida (CampanaID) VALUES (20)")

    with pytest.raises(AssertionError, match="Violación de sólo lectura"):
        conn.execute("UPDATE planificacion.Requerimiento SET OperadoresPlanificar = 10")


def test_configuracion_original_no_se_muta(ambiente_escenarios):
    """dataclasses.replace no debe modificar la instancia de CampanaCfg ni sus skills."""
    conn = ambiente_escenarios["conn"]
    cfg_antes = pdatos.cargar_config(conn, 20)
    ocup_antes = cfg_antes.max_ocupacion
    umbral_antes = cfg_antes.skills[0].umbral_seg

    pesc.calcular_escenario(
        conn,
        campana_id=20,
        cambios={"max_ocupacion": 0.70, "umbral_seg": 45},
    )

    cfg_despues = pdatos.cargar_config(conn, 20)
    assert cfg_despues.max_ocupacion == ocup_antes
    assert cfg_despues.skills[0].umbral_seg == umbral_antes


def test_base_no_coincide_genera_aviso(ambiente_escenarios, monkeypatch):
    """Si la corrida guardada fue calculada con otra config, avisa que la base usa la de hoy."""
    # Modificamos los operadores planificados guardados en la corrida
    filas_req_distintas = [
        dict(f, OperadoresPlanificar=999)
        for f in ambiente_escenarios["filas_req"]
    ]
    monkeypatch.setattr(pdatos, "leer_requerimiento", lambda c, cid: filas_req_distintas)

    conn = ambiente_escenarios["conn"]
    resultado = pesc.calcular_escenario(conn, campana_id=20, cambios={})

    assert resultado["base_coincide_con_corrida"] < 0.99
    assert any("la configuración cambió desde que se calculó el plan" in a for a in resultado["avisos"])


# ============================================================================
# TESTS DE VALIDACIONES (RESPUESTA 400)
# ============================================================================

def test_validaciones_factores_volumen_y_tmo():
    with pytest.raises(ValueError):
        pesc.validar_cambios({"factor_volumen": 0})
    with pytest.raises(ValueError):
        pesc.validar_cambios({"factor_volumen": 3.5})
    with pytest.raises(ValueError):
        pesc.validar_cambios({"factor_tmo": 0})
    with pytest.raises(ValueError):
        pesc.validar_cambios({"factor_tmo": 4.0})


def test_validaciones_ocupacion_umbral_nds_shrinkage_break():
    with pytest.raises(ValueError):
        pesc.validar_cambios({"max_ocupacion": 0})
    with pytest.raises(ValueError):
        pesc.validar_cambios({"max_ocupacion": 1.1})

    with pytest.raises(ValueError):
        pesc.validar_cambios({"umbral_seg": 0})
    with pytest.raises(ValueError):
        pesc.validar_cambios({"umbral_seg": -5})

    with pytest.raises(ValueError):
        pesc.validar_cambios({"objetivo_nds": 0})
    with pytest.raises(ValueError):
        pesc.validar_cambios({"objetivo_nds": 1.0})

    with pytest.raises(ValueError):
        pesc.validar_cambios({"shrinkage": -0.1})
    with pytest.raises(ValueError):
        pesc.validar_cambios({"shrinkage": 0.95})

    with pytest.raises(ValueError):
        pesc.validar_cambios({"break_min_por_hora": -1})
    with pytest.raises(ValueError):
        pesc.validar_cambios({"break_min_por_hora": 16})


def test_validaciones_fechas(ambiente_escenarios):
    conn = ambiente_escenarios["conn"]
    # desde posterior a hasta
    with pytest.raises(ValueError, match="'desde' no puede ser posterior a 'hasta'"):
        pesc.calcular_escenario(
            conn, campana_id=20, cambios={},
            desde=date(2026, 9, 20), hasta=date(2026, 9, 10)
        )

    # más de 31 días
    with pytest.raises(ValueError, match="El período no puede superar los 31 días"):
        pesc.calcular_escenario(
            conn, campana_id=20, cambios={},
            desde=date(2026, 9, 1), hasta=date(2026, 10, 5)
        )


def test_endpoint_router_devuelve_400_en_errores_de_validacion(ambiente_escenarios):
    """Verifica que el router capture ValueError de validación y levante HTTPException con código 400."""
    user = _usuario_falso()

    # Factor volumen fuera de rango
    req_vol_invalido = router_esc.EscenarioRequest(
        campana_id=20,
        cambios=router_esc.CambiosEscenarioRequest(factor_volumen=5.0)
    )
    with pytest.raises(HTTPException) as exc_info:
        router_esc.simular_escenario(req_vol_invalido, current_user=user)
    assert exc_info.value.status_code == 400

    # Ocupación fuera de rango
    req_ocup_invalida = router_esc.EscenarioRequest(
        campana_id=20,
        cambios=router_esc.CambiosEscenarioRequest(max_ocupacion=1.5)
    )
    with pytest.raises(HTTPException) as exc_info:
        router_esc.simular_escenario(req_ocup_invalida, current_user=user)
    assert exc_info.value.status_code == 400

    # Shrinkage fuera de rango
    req_sh_invalido = router_esc.EscenarioRequest(
        campana_id=20,
        cambios=router_esc.CambiosEscenarioRequest(shrinkage=0.99)
    )
    with pytest.raises(HTTPException) as exc_info:
        router_esc.simular_escenario(req_sh_invalido, current_user=user)
    assert exc_info.value.status_code == 400

    # Período mayor a 31 días
    req_fechas_invalido = router_esc.EscenarioRequest(
        campana_id=20,
        desde=date(2026, 1, 1),
        hasta=date(2026, 2, 15),
        cambios=router_esc.CambiosEscenarioRequest()
    )
    with pytest.raises(HTTPException) as exc_info:
        router_esc.simular_escenario(req_fechas_invalido, current_user=user)
    assert exc_info.value.status_code == 400


def test_endpoint_router_corrida_inexistente_devuelve_404(ambiente_escenarios, monkeypatch):
    monkeypatch.setattr(pdatos, "corrida_vigente", lambda c, camp, hor="operativo": None)
    user = _usuario_falso()
    req = router_esc.EscenarioRequest(campana_id=999)
    with pytest.raises(HTTPException) as exc_info:
        router_esc.simular_escenario(req, current_user=user)
    assert exc_info.value.status_code == 404


def test_atajo_sin_margen_de_ausentismo_reduce_horas(ambiente_escenarios):
    """Sin margen de ausentismo (shrinkage=0) las horas-operador deben ser menores a la base (shrinkage=0.30)."""
    conn = ambiente_escenarios["conn"]
    resultado = pesc.calcular_escenario(
        conn,
        campana_id=20,
        cambios={"shrinkage": 0.0},
    )
    res = resultado["resumen"]
    assert res["escenario"]["horas_operador"] < res["base"]["horas_operador"]
    assert res["diferencia"]["horas_operador"] < 0


def test_atajo_tmo_mas_alto_aumenta_horas(ambiente_escenarios):
    """TMO +10% (factor_tmo=1.10) debe requerir más horas-operador que la base."""
    conn = ambiente_escenarios["conn"]
    resultado = pesc.calcular_escenario(
        conn,
        campana_id=20,
        cambios={"factor_tmo": 1.10},
    )
    res = resultado["resumen"]
    assert res["escenario"]["horas_operador"] > res["base"]["horas_operador"]
    assert res["diferencia"]["horas_operador"] > 0


def test_atajo_umbral_mas_laxo_reduce_o_mantiene_horas(ambiente_escenarios):
    """Umbral de 30 s (vs 20 s contractual base) relaja el requerimiento de operadores."""
    conn = ambiente_escenarios["conn"]
    resultado = pesc.calcular_escenario(
        conn,
        campana_id=20,
        cambios={"umbral_seg": 30},
    )
    res = resultado["resumen"]
    assert res["escenario"]["horas_operador"] <= res["base"]["horas_operador"]

