"""Tests del módulo de insumos editables (app/planificador_insumos.py y routers/planificador_insumos.py).

Pruebas offline: sin base ni red.
Verifica:
1. Semántica de vigencia y CHECK de porcentaje al editar tramos de asignación (escritura con nuestra lógica,
   lectura con la de pdatos.factor_de_asignacion / asignacion_configurada).
2. Rechazo de reescritura de historia (vigente_desde <= VigenteDesde del último tramo) y porcentaje fuera de [0, 1].
3. Propuesta de reparto desde el share implícito del seguimiento (proponer_reparto_desde_share).
4. Días atípicos: confirmados y descartados no se vuelven a crear como "detectado" en un recálculo, y sólo los
   confirmados se excluyen del entrenamiento. Eventos multirango cubren todos sus días.
5. Función pura calcular_brecha_intradia para el requerimiento ajustado, citados y palanca de refuerzo Digital.
6. Proyección intradía de próximas horas: dimensionamiento idéntico al requerimiento sólo con desvío medido.
7. Endpoints GET y POST /planificador/asignacion con conexión y motor falso.

Correr: pytest tests/test_planificador_insumos.py -m "not tokens"
"""

from datetime import date, datetime, time, timedelta
from typing import List, Optional

import pytest
from pydantic import ValidationError

from app import planificador as pl
from app import planificador_datos as pdatos
from app import planificador_insumos as pinsumos
from app import planificador_servicio as servicio
from app.models import User
from app.routers import planificador_insumos as router_insumos


# ---------------------------------------------------------------------------
# Fakes para simular la conexión a base de datos
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, items=None, rowcount=1, scalar=None):
        self._items = items or []
        self.rowcount = rowcount
        self._scalar = scalar

    def mappings(self):
        return self

    def all(self):
        return self._items

    def fetchone(self):
        return self._items[0] if self._items else None

    def scalar(self):
        return self._scalar


class _FakeConn:
    def __init__(self, respuestas=None):
        self.respuestas = list(respuestas or [])
        self.queries = []

    def execute(self, statement, parameters=None):
        sql = str(statement.text if hasattr(statement, "text") else statement)
        self.queries.append((sql, parameters))
        if self.respuestas:
            return self.respuestas.pop(0)
        return _FakeResult()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _FakeEngine:
    def __init__(self, *respuestas):
        self.conn = _FakeConn(respuestas)

    def connect(self):
        return self.conn

    def begin(self):
        return self.conn


# ===========================================================================
# 1. SEMÁNTICA DE VIGENCIA Y CHECK DE REPARTO
# ===========================================================================

def test_semantica_asignacion_cierre_y_lectura_pdatos():
    """Escribe con nuestra lógica de cierre y lee con factor_de_asignacion de pdatos.

    Verifica:
    - Tramo existente: VigenteDesde 2026-09-01, VigenteHasta None, Porcentaje 0.492.
    - Se agrega nuevo tramo: vigente_desde 2026-10-01, porcentaje 0.70.
    - El tramo previo se cierra el día anterior: 2026-09-30.
    - factor_de_asignacion devuelve 0.492 hasta el 2026-09-30 inclusive,
      y 0.70 a partir del 2026-10-01 inclusive. Sin solapamientos ni huecos.
    """
    skill_id = 6
    existentes = [
        {
            "AsignacionID": 1,
            "SkillID": skill_id,
            "VigenteDesde": date(2026, 9, 1),
            "VigenteHasta": None,
            "Porcentaje": 0.492,
            "Nota": "Escalón septiembre",
        }
    ]

    nuevos = [
        {
            "skill_id": skill_id,
            "vigente_desde": date(2026, 10, 1),
            "porcentaje": 0.70,
            "nota": "Paso al 70%",
        }
    ]

    actualizados = pinsumos.aplicar_tramos_memoria(existentes, nuevos)

    # Verificamos estructura resultante
    assert len(actualizados) == 2
    viejo, nuevo = actualizados[0], actualizados[1]
    assert viejo["VigenteHasta"] == date(2026, 9, 30)
    assert nuevo["VigenteDesde"] == date(2026, 10, 1)
    assert nuevo["VigenteHasta"] is None
    assert nuevo["Porcentaje"] == 0.70

    # Lectura con la semántica oficial de pdatos.factor_de_asignacion
    # Antes del cambio
    assert pdatos.factor_de_asignacion(actualizados, date(2026, 9, 15), skill_id) == pytest.approx(0.492)
    # En el último día del tramo anterior (límite inclusivo)
    assert pdatos.factor_de_asignacion(actualizados, date(2026, 9, 30), skill_id) == pytest.approx(0.492)
    # En el primer día del tramo nuevo (límite inclusivo)
    assert pdatos.factor_de_asignacion(actualizados, date(2026, 10, 1), skill_id) == pytest.approx(0.70)
    # Días posteriores
    assert pdatos.factor_de_asignacion(actualizados, date(2026, 10, 15), skill_id) == pytest.approx(0.70)


def test_reparto_rechaza_reescribir_historia():
    """Un tramo con vigente_desde <= al VigenteDesde del último tramo debe rechazarse."""
    existentes = [
        {
            "AsignacionID": 1,
            "SkillID": 4,
            "VigenteDesde": date(2026, 9, 1),
            "VigenteHasta": None,
            "Porcentaje": 0.50,
        }
    ]

    # Fecha anterior al último tramo
    with pytest.raises(ValueError, match="No se reescribe la historia"):
        pinsumos.validar_nuevos_tramos(existentes, [{
            "skill_id": 4,
            "vigente_desde": date(2026, 8, 15),
            "porcentaje": 0.60,
        }])

    # Misma fecha que el último tramo
    with pytest.raises(ValueError, match="No se reescribe la historia"):
        pinsumos.validar_nuevos_tramos(existentes, [{
            "skill_id": 4,
            "vigente_desde": date(2026, 9, 1),
            "porcentaje": 0.60,
        }])


def test_reparto_valida_rango_porcentaje_y_admite_cero():
    """El porcentaje debe estar en [0, 1]. El 0 es un valor contractual explícito (CNR, BPO-05)."""
    existentes = [
        {
            "AsignacionID": 1,
            "SkillID": 5,
            "VigenteDesde": date(2026, 9, 1),
            "VigenteHasta": None,
            "Porcentaje": 0.50,
        }
    ]

    # Porcentaje mayor a 1
    with pytest.raises(ValueError, match="El porcentaje de asignación debe estar entre 0 y 1"):
        pinsumos.validar_nuevos_tramos(existentes, [{
            "skill_id": 5,
            "vigente_desde": date(2026, 10, 1),
            "porcentaje": 1.05,
        }])

    # Porcentaje menor a 0
    with pytest.raises(ValueError, match="El porcentaje de asignación debe estar entre 0 y 1"):
        pinsumos.validar_nuevos_tramos(existentes, [{
            "skill_id": 5,
            "vigente_desde": date(2026, 10, 1),
            "porcentaje": -0.10,
        }])

    # Porcentaje 0 es válido y legítimo
    pinsumos.validar_nuevos_tramos(existentes, [{
        "skill_id": 5,
        "vigente_desde": date(2026, 10, 1),
        "porcentaje": 0.0,
    }])


def test_pydantic_schema_tramo_valida_porcentaje():
    """El esquema Pydantic valida que porcentaje esté entre 0.0 y 1.0."""
    with pytest.raises(ValidationError):
        router_insumos.TramoAsignacionItem(
            skill_id=5,
            vigente_desde=date(2026, 10, 1),
            porcentaje=1.5,
        )


def test_proponer_reparto_desde_share():
    """Proponer reparto = vigente * ratio, con 0 fijo y tope 1.0."""
    # Suba del share: 50% * 1.2 = 60%
    assert pinsumos.proponer_reparto_desde_share(0.50, 1.20) == pytest.approx(0.60)
    # Desborde topeado en 1.0: 70% * 1.5 = 100%
    assert pinsumos.proponer_reparto_desde_share(0.70, 1.50) == 1.0
    # Cola en 0% (ej. CNR) permanece en 0%
    assert pinsumos.proponer_reparto_desde_share(0.0, 1.30) == 0.0


# ===========================================================================
# 2. DÍAS ATÍPICOS: CONFIRMAR Y DESCARTAR EN RECÁLCULO
# ===========================================================================

def test_atipicos_confirmar_y_descartar_no_recrea_en_recalculo(monkeypatch):
    """Verifica que un día confirmado o descartado no vuelva a crearse como 'detectado' en un recálculo.

    - Confirmado (Confirmado=1, ExcluirDeEntrenamiento=1): ya está en planificacion.Evento,
      _sembrar_atipicos no lo vuelve a insertar y dias_a_excluir lo excluye del entrenamiento.
    - Descartado (Confirmado=1, ExcluirDeEntrenamiento=0): permanece en planificacion.Evento,
      _sembrar_atipicos no lo vuelve a insertar y dias_a_excluir NO lo excluye del entrenamiento.
    - Evento multirango cubre todos sus días.
    """
    dia_confirmado = date(2026, 7, 10)
    dia_descartado = date(2026, 7, 15)
    dia_multirango_inicio = date(2026, 7, 20)
    dia_multirango_fin = date(2026, 7, 23)  # Cubre 20, 21, 22

    eventos_guardados = [
        {
            "EventoID": 101,
            "CampanaID": 20,
            "Desde": datetime.combine(dia_confirmado, time(0, 0)),
            "Hasta": datetime.combine(dia_confirmado + timedelta(days=1), time(0, 0)),
            "Tipo": "corte",
            "Descripcion": "Corte de luz masivo",
            "Factor": 2.5,
            "Origen": "manual",
            "ExcluirDeEntrenamiento": 1,
            "Confirmado": 1,
        },
        {
            "EventoID": 102,
            "CampanaID": 20,
            "Desde": datetime.combine(dia_descartado, time(0, 0)),
            "Hasta": datetime.combine(dia_descartado + timedelta(days=1), time(0, 0)),
            "Tipo": "normal",
            "Descripcion": "Descartado: día normal",
            "Factor": 1.4,
            "Origen": "auto",
            "ExcluirDeEntrenamiento": 0,
            "Confirmado": 1,
        },
        {
            "EventoID": 103,
            "CampanaID": 20,
            "Desde": datetime.combine(dia_multirango_inicio, time(0, 0)),
            "Hasta": datetime.combine(dia_multirango_fin, time(0, 0)),
            "Tipo": "clima",
            "Descripcion": "Ola de frío polar",
            "Factor": 2.0,
            "Origen": "manual",
            "ExcluirDeEntrenamiento": 1,
            "Confirmado": 1,
        },
    ]

    # Monkeypatch de pdatos.eventos para devolver la lista fija
    monkeypatch.setattr(pdatos, "eventos", lambda conn, cid, d, h: eventos_guardados)

    # 1. dias_a_excluir debe incluir dia_confirmado y los días del multirango, pero NO dia_descartado
    excluidos = pdatos.dias_a_excluir(_FakeConn(), 20, date(2026, 7, 1), date(2026, 8, 1))
    assert dia_confirmado in excluidos
    assert dia_descartado not in excluidos
    assert date(2026, 7, 20) in excluidos
    assert date(2026, 7, 21) in excluidos
    assert date(2026, 7, 22) in excluidos
    # Hasta es exclusivo: ni el 23 ni el día después del confirmado.
    assert date(2026, 7, 23) not in excluidos
    assert dia_confirmado + timedelta(days=1) not in excluidos

    # 2. _sembrar_atipicos: simular candidatos que incluyen el día confirmado, el descartado,
    # el día del medio del multirango (2026-07-21) y un día realmente nuevo (2026-07-28)
    dia_nuevo = date(2026, 7, 28)
    candidatos_detectados = [
        pl.Atipico(dia_confirmado, 5000, 2000, 2.5, 4.0),
        pl.Atipico(dia_descartado, 3000, 2000, 1.5, 3.1),
        pl.Atipico(date(2026, 7, 21), 4000, 2000, 2.0, 3.5),
        pl.Atipico(dia_nuevo, 6000, 2000, 3.0, 5.0),
    ]

    monkeypatch.setattr(pl, "detectar_atipicos", lambda diaria, feriados=None: candidatos_detectados)
    monkeypatch.setattr(pdatos, "serie_diaria", lambda serie, skills: {
        date(2026, 7, 1): 2000.0, date(2026, 7, 30): 2000.0
    })

    conn_fake = _FakeConn()
    cfg = pl.CampanaCfg(campana_id=20, skills=[pl.SkillCfg(6, "EMERGENCIAS", 1, 0.8, 20)])
    nuevos = servicio._sembrar_atipicos(conn_fake, 20, {}, set(), cfg)

    # Sólo se inserta el día nuevo; los confirmados, descartados y multirango NO se vuelven a crear
    assert nuevos == 1
    assert len(conn_fake.queries) == 1
    _, params = conn_fake.queries[0]
    assert params["d"] == datetime.combine(dia_nuevo, time(0, 0))


# ===========================================================================
# 3. FUNCIÓN PURA calcular_brecha_intradia
# ===========================================================================

def test_calcular_brecha_intradia_con_faltante_y_refuerzo():
    """Faltan 4 personas y Digital tiene 6 con turno -> faltan_tras_digital = 0."""
    res = pinsumos.calcular_brecha_intradia(
        a_planificar_plan=10,
        a_planificar_ajustado=14,
        citados=10,
        digital_con_turno=6,
    )
    assert res["a_planificar_plan"] == 10
    assert res["a_planificar_ajustado"] == 14
    assert res["citados"] == 10
    assert res["faltan"] == 4
    assert res["digital_con_turno"] == 6
    assert res["faltan_tras_digital"] == 0


def test_calcular_brecha_intradia_cuando_refuerzo_no_alcanza():
    """Faltan 8 personas y Digital tiene 6 -> tras Digital faltan 2."""
    res = pinsumos.calcular_brecha_intradia(
        a_planificar_plan=10,
        a_planificar_ajustado=18,
        citados=10,
        digital_con_turno=6,
    )
    assert res["faltan"] == 8
    assert res["faltan_tras_digital"] == 2


def test_calcular_brecha_intradia_sobra_gente():
    """Con citados de más, faltan = 0 y tras digital = 0."""
    res = pinsumos.calcular_brecha_intradia(
        a_planificar_plan=10,
        a_planificar_ajustado=10,
        citados=12,
        digital_con_turno=4,
    )
    assert res["faltan"] == 0
    assert res["faltan_tras_digital"] == 0


def test_calcular_brecha_intradia_sin_malla():
    """Si no hay citados cargados (None), faltan es None."""
    res = pinsumos.calcular_brecha_intradia(
        a_planificar_plan=10,
        a_planificar_ajustado=12,
        citados=None,
        digital_con_turno=None,
    )
    assert res["faltan"] is None
    assert res["faltan_tras_digital"] is None


# ===========================================================================
# 4. DIMENSIONAMIENTO DE PRÓXIMAS HORAS (INTRADÍA)
# ===========================================================================

def test_dimensionar_proximas_horas_sin_desvio():
    """Sin desvío medido devuelve lista vacía."""
    cfg = pl.CampanaCfg(campana_id=20)
    res = pinsumos.dimensionar_proximas_horas(
        cfg=cfg,
        pron_rows=[],
        req_rows=[],
        desvio=None,
        en_curso=datetime(2026, 9, 15, 14, 0),
        dia=date(2026, 9, 15),
    )
    assert res == []


def test_dimensionar_proximas_horas_con_desvio_y_ventana():
    """Con desvío medido, dimensiona los intervalos de hoy dentro de las 4 horas siguientes."""
    cfg = pl.CampanaCfg(
        campana_id=20,
        intervalo_min=30,
        max_ocupacion=0.85,
        shrinkage=0.0,
        paciencia_seg=850,
        pools={1: pl.PoolCfg(pool_id=1, nombre="General", min_operadores=0)},
        skills=[pl.SkillCfg(6, "EMERGENCIAS", 1, 0.80, 20)],
    )

    dia_hoy = date(2026, 9, 15)
    en_curso = datetime(2026, 9, 15, 14, 0)
    int_1 = datetime(2026, 9, 15, 14, 30)
    int_2 = datetime(2026, 9, 15, 15, 0)
    int_lejano = datetime(2026, 9, 15, 19, 0)  # > 4 horas después de 14:00

    pron_rows = [
        {"SkillID": 6, "Intervalo": int_1, "LlamadasAcme": 100.0, "TmoSeg": 200.0},
        {"SkillID": 6, "Intervalo": int_2, "LlamadasAcme": 120.0, "TmoSeg": 200.0},
        {"SkillID": 6, "Intervalo": int_lejano, "LlamadasAcme": 80.0, "TmoSeg": 200.0},
    ]

    req_rows = [
        {"PoolID": 1, "Intervalo": int_1, "OperadoresPlanificar": 15,
         "OperadoresPlanificados": 14, "RefuerzoDisponible": 2},
        {"PoolID": 1, "Intervalo": int_2, "OperadoresPlanificar": 18,
         "OperadoresPlanificados": 16, "RefuerzoDisponible": 2},
        {"PoolID": 1, "Intervalo": int_lejano, "OperadoresPlanificar": 12,
         "OperadoresPlanificados": 12, "RefuerzoDisponible": 0},
    ]

    # Desvío +20%
    desvio = 0.20
    res = pinsumos.dimensionar_proximas_horas(
        cfg=cfg,
        pron_rows=pron_rows,
        req_rows=req_rows,
        desvio=desvio,
        en_curso=en_curso,
        dia=dia_hoy,
        horas=4,
    )

    # int_lejano quedó fuera de las 4 horas (14:00 a 18:00)
    assert len(res) == 2
    assert res[0]["intervalo"] == int_1
    assert res[1]["intervalo"] == int_2

    # Verificamos que las claves pedidas estén presentes
    for item in res:
        for k in ("intervalo", "pool_id", "a_planificar_plan", "a_planificar_ajustado",
                  "citados", "faltan", "digital_con_turno", "faltan_tras_digital"):
            assert k in item

    # Con llamadas infladas al 1.2, a_planificar_ajustado dimensiona más operadores
    assert res[0]["a_planificar_ajustado"] >= 15


# ===========================================================================
# 5. ENDPOINTS GET Y POST /planificador/asignacion
# ===========================================================================

def test_endpoint_get_asignacion(monkeypatch):
    """GET /planificador/asignacion devuelve vigente, historia y sin_tramo."""
    user = User(usuario=1, permissions=["planificador.view"])
    monkeypatch.setattr(router_insumos, "exigir_acceso_empresa", lambda *a, **k: None)

    cfg = pl.CampanaCfg(
        campana_id=20,
        skills=[
            pl.SkillCfg(6, "EMERGENCIAS", 1, 0.8, 20),
            pl.SkillCfg(4, "CNR", 1, 0.8, 20),
        ]
    )
    monkeypatch.setattr(pdatos, "cargar_config", lambda conn, cid: cfg)

    # Fila de base: EMERGENCIAS tiene tramo vigente; CNR no tiene tramo
    filas_bd = [
        {
            "AsignacionID": 1,
            "SkillID": 6,
            "VigenteDesde": date(2026, 9, 1),
            "VigenteHasta": None,
            "Porcentaje": 0.492,
            "Nota": "Escalón septiembre",
            "CreadoPor": 1,
            "CreadoEn": datetime(2026, 9, 1, 10, 0),
        }
    ]

    fake_engine = _FakeEngine(_FakeResult(filas_bd))
    monkeypatch.setattr(router_insumos, "engine", fake_engine)

    resp = router_insumos.obtener_asignacion(campana_id=20, current_user=user)
    assert resp["campana_id"] == 20
    assert len(resp["skills"]) == 2

    emergencias = next(s for s in resp["skills"] if s["skill_id"] == 6)
    assert emergencias["tramo_vigente"] is not None
    assert emergencias["tramo_vigente"]["porcentaje"] == pytest.approx(0.492)
    assert len(emergencias["historia"]) == 1

    # CNR no tiene tramo y aparece en sin_tramo
    cnr = next(s for s in resp["skills"] if s["skill_id"] == 4)
    assert cnr["tramo_vigente"] is None
    assert any(st["skill_id"] == 4 for st in resp["sin_tramo"])


def test_endpoint_post_asignacion_valida_y_guarda(monkeypatch):
    """POST /planificador/asignacion actualiza tramo previo e inserta nuevo en una transacción."""
    user = User(usuario=42, permissions=["planificador.edit"])
    monkeypatch.setattr(router_insumos, "exigir_acceso_empresa", lambda *a, **k: None)

    filas_bd = [
        {
            "AsignacionID": 1,
            "SkillID": 6,
            "VigenteDesde": date(2026, 9, 1),
            "VigenteHasta": None,
            "Porcentaje": 0.492,
        }
    ]

    fake_engine = _FakeEngine(_FakeResult(filas_bd), _FakeResult(rowcount=1), _FakeResult(rowcount=1))
    monkeypatch.setattr(router_insumos, "engine", fake_engine)

    auditorias = []
    monkeypatch.setattr(router_insumos, "registrar_auditoria",
                        lambda conn, u, acc, ent, eid, det: auditorias.append((acc, ent, eid, det)))

    payload = router_insumos.GuardarAsignacionRequest(
        campana_id=20,
        tramos=[
            router_insumos.TramoAsignacionItem(
                skill_id=6,
                vigente_desde=date(2026, 10, 1),
                porcentaje=0.70,
                nota="Paso al 70%",
            )
        ]
    )

    resp = router_insumos.guardar_asignacion(payload=payload, current_user=user)
    assert resp["ok"] is True
    assert resp["guardados"] == 1

    # Verificamos que se ejecutó UPDATE para cerrar el tramo y luego INSERT
    conn = fake_engine.conn
    update_q = next(q for q in conn.queries if "UPDATE planificacion.Asignacion" in q[0])
    insert_q = next(q for q in conn.queries if "INSERT INTO planificacion.Asignacion" in q[0])

    assert update_q[1]["cierre"] == date(2026, 9, 30)
    assert insert_q[1]["pct"] == 0.70
    assert insert_q[1]["u"] == 42

    # Verificamos auditoría
    assert len(auditorias) == 1
    assert auditorias[0][0] == "planificador.asignacion_edicion"
    assert auditorias[0][1] == "asignacion"
    assert auditorias[0][2] == 20
