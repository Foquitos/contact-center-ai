"""Tests del ciclo de vida y evaluación de pedidos de refuerzo (planificador_pedidos).

Invariantes defendidas:
  - `emparejar`: asocia bloques a pedidos por (pool, día, superposición temporal);
    si hay varios, prioriza el más reciente no descartado.
  - `evaluar_cobertura`:
      * horas cubiertas = Σ MIN(faltante, MAX(0, registro - malla)) * intervalo_min / 60
      * estado = cubierto (>= 90%), cubierto_parcial (> 0%), no_cubierto (0%).
  - Guards de tabla: lanzan `MigracionPendiente` si falta la tabla.
  - Cierre perezoso: evalúa pedidos vencidos (Dia < hoy) contra el día cerrado.

Offline: andamiaje con conexiones falsas sin tocar BD.
"""

from datetime import date, datetime, timedelta
import pytest

from app import planificador_datos as pd
from app import planificador_pedidos as pp
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

    def scalar(self):
        if not self._filas:
            return None
        primera = self._filas[0]
        if isinstance(primera, dict):
            return next(iter(primera.values()))
        if isinstance(primera, (tuple, list)):
            return primera[0]
        return primera

    @property
    def rowcount(self):
        return len(self._filas)


class _Conn:
    def __init__(self, filas=(), respuestas=None):
        self.filas = list(filas)
        self.respuestas = list(respuestas or [])
        self.sql = []
        self.params = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, stmt, params=None):
        self.sql.append(str(stmt))
        self.params.append(params)
        if self.respuestas:
            return _Resultado(self.respuestas.pop(0))
        return _Resultado(self.filas)


# ----------------------------------------------------------- emparejar (pura)

def test_emparejar_sin_pedidos():
    bloque = {
        "pool_id": 1,
        "dia": date(2026, 9, 15),
        "desde": datetime(2026, 9, 15, 10, 0),
        "hasta": datetime(2026, 9, 15, 14, 0),
    }
    resultado = pp.emparejar([bloque], [])
    assert len(resultado) == 1
    assert resultado[0]["pedido"] is None


def test_emparejar_con_pedido_superpuesto():
    t0 = datetime(2026, 9, 15, 10, 0)
    t1 = datetime(2026, 9, 15, 14, 0)
    bloque = {"pool_id": 1, "dia": date(2026, 9, 15), "desde": t0, "hasta": t1}
    pedido = {
        "RefuerzoPedidoID": 101,
        "PoolID": 1,
        "Dia": date(2026, 9, 15),
        "Desde": t0,
        "Hasta": t1,
        "Estado": "pedido",
        "HorasCubiertas": None,
        "CreadoEn": datetime(2026, 9, 14, 18, 0),
    }

    resultado = pp.emparejar([bloque], [pedido])
    assert resultado[0]["pedido"] == {
        "id": 101,
        "estado": "pedido",
        "horas_cubiertas": None,
    }


def test_emparejar_no_mezcla_pools_ni_dias():
    t0 = datetime(2026, 9, 15, 10, 0)
    t1 = datetime(2026, 9, 15, 14, 0)
    bloque = {"pool_id": 1, "dia": date(2026, 9, 15), "desde": t0, "hasta": t1}
    otro_pool = {
        "RefuerzoPedidoID": 102, "PoolID": 2, "Dia": date(2026, 9, 15),
        "Desde": t0, "Hasta": t1, "Estado": "pedido", "CreadoEn": datetime(2026, 9, 14, 18, 0)
    }
    otro_dia = {
        "RefuerzoPedidoID": 103, "PoolID": 1, "Dia": date(2026, 9, 16),
        "Desde": t0 + timedelta(days=1), "Hasta": t1 + timedelta(days=1),
        "Estado": "pedido", "CreadoEn": datetime(2026, 9, 14, 18, 0)
    }

    assert pp.emparejar([bloque], [otro_pool])[0]["pedido"] is None
    assert pp.emparejar([bloque], [otro_dia])[0]["pedido"] is None


def test_emparejar_prioriza_el_mas_reciente_no_descartado():
    t0 = datetime(2026, 9, 15, 10, 0)
    t1 = datetime(2026, 9, 15, 14, 0)
    bloque = {"pool_id": 1, "dia": date(2026, 9, 15), "desde": t0, "hasta": t1}

    viejo_pedido = {
        "RefuerzoPedidoID": 10, "PoolID": 1, "Dia": date(2026, 9, 15),
        "Desde": t0, "Hasta": t1, "Estado": "pedido",
        "CreadoEn": datetime(2026, 9, 14, 10, 0)
    }
    nuevo_descartado = {
        "RefuerzoPedidoID": 11, "PoolID": 1, "Dia": date(2026, 9, 15),
        "Desde": t0, "Hasta": t1, "Estado": "descartado",
        "CreadoEn": datetime(2026, 9, 14, 12, 0)
    }
    nuevo_pedido = {
        "RefuerzoPedidoID": 12, "PoolID": 1, "Dia": date(2026, 9, 15),
        "Desde": t0, "Hasta": t1, "Estado": "pedido",
        "CreadoEn": datetime(2026, 9, 14, 15, 0)
    }

    resultado = pp.emparejar([bloque], [viejo_pedido, nuevo_descartado, nuevo_pedido])
    assert resultado[0]["pedido"]["id"] == 12


def test_emparejar_toma_descartado_si_es_el_unico():
    t0 = datetime(2026, 9, 15, 10, 0)
    t1 = datetime(2026, 9, 15, 14, 0)
    bloque = {"pool_id": 1, "dia": date(2026, 9, 15), "desde": t0, "hasta": t1}
    descartado = {
        "RefuerzoPedidoID": 15, "PoolID": 1, "Dia": date(2026, 9, 15),
        "Desde": t0, "Hasta": t1, "Estado": "descartado",
        "CreadoEn": datetime(2026, 9, 14, 12, 0)
    }

    resultado = pp.emparejar([bloque], [descartado])
    assert resultado[0]["pedido"]["id"] == 15
    assert resultado[0]["pedido"]["estado"] == "descartado"


# ---------------------------------------------------- evaluar_cobertura (pura)

def test_evaluar_cobertura_completa():
    """Pedido de 2 horas (4 intervalos de 30 min) con faltante de 2 operadores = 4.0 h.
    En registro hay 2 operadores más que en la malla en cada intervalo:
    4.0 h cubiertas = 100% de 4.0 h -> 'cubierto'.
    """
    t0 = datetime(2026, 9, 10, 10, 0)
    t1 = datetime(2026, 9, 10, 12, 0)
    paso = timedelta(minutes=30)
    pedido = {"Desde": t0, "Hasta": t1, "HorasOperador": 4.0}

    faltantes = {t0 + i * paso: 2 for i in range(4)}
    registro = {t0 + i * paso: 12 for i in range(4)}
    malla = {t0 + i * paso: 10 for i in range(4)}

    res = pp.evaluar_cobertura(pedido, faltantes, registro, malla, intervalo_min=30)
    assert res["horas_cubiertas"] == 4.0
    assert res["estado"] == "cubierto"


def test_evaluar_cobertura_al_noventa_por_ciento():
    """Pedido de 10 horas. Se cubren exactamente 9.0 h (90%): estado 'cubierto'."""
    t0 = datetime(2026, 9, 10, 8, 0)
    t1 = datetime(2026, 9, 10, 18, 0)  # 20 intervalos = 10 h
    paso = timedelta(minutes=30)
    pedido = {"Desde": t0, "Hasta": t1, "HorasOperador": 10.0}

    # Cubre en 18 de 20 intervalos -> 18 * 0.5 = 9.0 h
    faltantes = {t0 + i * paso: 1 for i in range(20)}
    registro = {t0 + i * paso: 11 if i < 18 else 10 for i in range(20)}
    malla = {t0 + i * paso: 10 for i in range(20)}

    res = pp.evaluar_cobertura(pedido, faltantes, registro, malla, intervalo_min=30)
    assert res["horas_cubiertas"] == 9.0
    assert res["estado"] == "cubierto"


def test_evaluar_cobertura_parcial():
    """Pedido de 10 horas. Se cubren 5.0 h (50%): estado 'cubierto_parcial'."""
    t0 = datetime(2026, 9, 10, 8, 0)
    t1 = datetime(2026, 9, 10, 18, 0)
    paso = timedelta(minutes=30)
    pedido = {"Desde": t0, "Hasta": t1, "HorasOperador": 10.0}

    faltantes = {t0 + i * paso: 1 for i in range(20)}
    registro = {t0 + i * paso: 11 if i < 10 else 10 for i in range(20)}
    malla = {t0 + i * paso: 10 for i in range(20)}

    res = pp.evaluar_cobertura(pedido, faltantes, registro, malla, intervalo_min=30)
    assert res["horas_cubiertas"] == 5.0
    assert res["estado"] == "cubierto_parcial"


def test_evaluar_cobertura_cero_no_cubierto():
    """No se agregaron personas sobre la malla (registro <= malla): estado 'no_cubierto'."""
    t0 = datetime(2026, 9, 10, 10, 0)
    t1 = datetime(2026, 9, 10, 12, 0)
    pedido = {"Desde": t0, "Hasta": t1, "HorasOperador": 2.0}

    faltantes = {t0: 1}
    registro = {t0: 8}
    malla = {t0: 10}  # Registro fue menor que la malla

    res = pp.evaluar_cobertura(pedido, faltantes, registro, malla, intervalo_min=30)
    assert res["horas_cubiertas"] == 0.0
    assert res["estado"] == "no_cubierto"


def test_evaluar_cobertura_topeada_al_faltante():
    """Si se pusieron 5 personas extra pero el faltante era de 2, sólo computa 2."""
    t0 = datetime(2026, 9, 10, 10, 0)
    t1 = datetime(2026, 9, 10, 11, 0)
    paso = timedelta(minutes=30)
    pedido = {"Desde": t0, "Hasta": t1, "HorasOperador": 2.0}

    faltantes = {t0: 2, t0 + paso: 2}
    registro = {t0: 15, t0 + paso: 15}
    malla = {t0: 10, t0 + paso: 10}  # extra = 5

    res = pp.evaluar_cobertura(pedido, faltantes, registro, malla, intervalo_min=30)
    # 2 intervalos * min(2, 5) = 2 + 2 = 4 intervalos-operador = 2.0 h
    assert res["horas_cubiertas"] == 2.0
    assert res["estado"] == "cubierto"


# ------------------------------------------------------------- guards y BD

def test_guard_sin_migracion(monkeypatch):
    monkeypatch.setattr(pd, "_tiene_tabla", lambda c, t: False)
    conn = _Conn()

    assert pp.tabla_disponible(conn) is False
    with pytest.raises(pd.MigracionPendiente):
        pp.exigir_tabla(conn)


def test_guard_con_migracion(monkeypatch):
    monkeypatch.setattr(pd, "_tiene_tabla", lambda c, t: True)
    conn = _Conn()

    assert pp.tabla_disponible(conn) is True
    pp.exigir_tabla(conn)  # No lanza excepción


def test_crear_pedido(monkeypatch):
    monkeypatch.setattr(pd, "_tiene_tabla", lambda c, t: True)
    conn = _Conn(filas=[{"RefuerzoPedidoID": 42}])

    pid = pp.crear_pedido(
        conn, campana_id=20, pool_id=1, dia=date(2026, 9, 15),
        desde=datetime(2026, 9, 15, 10, 0), hasta=datetime(2026, 9, 15, 14, 0),
        faltante_pico=4, horas_operador=8.0, accion="horas_extra", nota="Para el pico",
        usuario=5
    )

    assert pid == 42
    assert "INSERT INTO planificacion.RefuerzoPedido" in conn.sql[-1]
    assert conn.params[-1]["pico"] == 4
    assert conn.params[-1]["accion"] == "horas_extra"


def test_actualizar_pedido(monkeypatch):
    monkeypatch.setattr(pd, "_tiene_tabla", lambda c, t: True)
    conn = _Conn(filas=[{"RefuerzoPedidoID": 42}])

    ok = pp.actualizar_pedido(
        conn, pedido_id=42, campana_id=20, estado="descartado", nota="Cancelado por lluvia"
    )

    assert ok is True
    assert "UPDATE planificacion.RefuerzoPedido" in conn.sql[-1]
    assert conn.params[-1]["estado"] == "descartado"
    assert conn.params[-1]["nota"] == "Cancelado por lluvia"


def test_cierre_perezoso(monkeypatch):
    monkeypatch.setattr(pd, "_tiene_tabla", lambda c, t: True)
    t0 = datetime(2026, 9, 10, 10, 0)
    t1 = datetime(2026, 9, 10, 12, 0)

    # 1. conn.execute para buscar pedidos pendientes vencidos
    pendientes = [{
        "RefuerzoPedidoID": 88, "PoolID": 1, "Dia": date(2026, 9, 10),
        # 1 h pedida: 2 intervalos de faltante 1 cubiertos con 1 persona extra = 1,0 h (100%).
        "Desde": t0, "Hasta": t1, "HorasOperador": 1.0
    }]
    # 2. Requerimiento guardado
    req_filas = [
        {"Intervalo": t0, "Brecha": -1},
        {"Intervalo": t0 + timedelta(minutes=30), "Brecha": -1},
    ]

    monkeypatch.setattr(pd, "dotacion_real", lambda c, p, d1, d2, i: {t0: 11, t0 + timedelta(minutes=30): 11})
    monkeypatch.setattr(pd, "dotacion_planificada", lambda c, p, d1, d2, i: {t0: 10, t0 + timedelta(minutes=30): 10})

    conn = _Conn(respuestas=[pendientes, req_filas, [{"rowcount": 1}]])

    actualizados = pp.cerrar_pedidos_vencidos(conn, campana_id=20, hoy=date(2026, 9, 15))
    assert actualizados == 1
    assert "UPDATE planificacion.RefuerzoPedido" in conn.sql[-1]
    assert conn.params[-1]["estado"] == "cubierto"
    assert conn.params[-1]["horas_cubiertas"] == 1.0


# --------------------------------------------------- necesidades en servicio

class _MockEngine:
    def __init__(self, conn=None):
        self._conn = conn or _Conn()

    def connect(self):
        return self._conn

    def begin(self):
        return self._conn


def test_necesidades_sin_migracion(monkeypatch):
    """Sin la migración, necesidades responde igual que antes más los campos
    vacíos de seguimiento y con turnos_sugeridos en cada bloque."""
    hoy = date(2026, 9, 15)
    t0 = datetime(2026, 9, 16, 10, 0)
    t1 = datetime(2026, 9, 16, 12, 0)

    monkeypatch.setattr(pd, "schema_disponible", lambda c: True)
    monkeypatch.setattr(pd, "corrida_vigente", lambda c, i, h: {
        "CorridaID": 1, "Desde": hoy, "Hasta": hoy + timedelta(days=14)})
    monkeypatch.setattr(pd, "cargar_config", lambda c, i: type("Config", (), {
        "intervalo_min": 30, "skills": [], "pools": {1: type("Pool", (), {"pool_id": 1, "nombre": "General"})()}
    })())
    monkeypatch.setattr(pd, "leer_requerimiento", lambda c, i: [
        {"PoolID": 1, "Pool": "General", "Intervalo": t0, "Brecha": -2, "OperadoresPlanificados": 10, "Llamadas": 50},
        {"PoolID": 1, "Pool": "General", "Intervalo": t0 + timedelta(minutes=30), "Brecha": -2, "OperadoresPlanificados": 10, "Llamadas": 50},
    ])
    monkeypatch.setattr(pd, "leer_pronostico", lambda c, i: [])
    monkeypatch.setattr(ps, "_dimensionar_escenario_alto", lambda *a, **k: {})
    # Simular que NO está la migración 2026-09-15
    monkeypatch.setattr(pd, "_tiene_tabla", lambda c, t: t != "planificacion.RefuerzoPedido")

    conn = _Conn()
    engine = _MockEngine(conn)

    res = ps.necesidades(engine, 20, hoy=hoy)
    assert res["hay_corrida"] is True
    assert res["resumen"]["seguimiento_disponible"] is False
    assert res["resumen"]["ultimos_14d_horas_pedidas"] == 0.0
    assert res["resumen"]["ultimos_14d_horas_cubiertas"] == 0.0

    refuerzos = res["refuerzos"]
    assert len(refuerzos) == 1
    bloque = refuerzos[0]
    assert bloque["pedido"] is None
    assert "turnos_sugeridos" in bloque
    assert bloque["turnos_sugeridos"]["horas_faltantes"] == 2.0
    assert "extension" in bloque["turnos_sugeridos"]["texto"].lower()


def test_necesidades_con_migracion(monkeypatch):
    """Con la migración, necesidades empareja pedidos y calcula totales de 14 días."""
    hoy = date(2026, 9, 15)
    t0 = datetime(2026, 9, 16, 10, 0)
    t1 = datetime(2026, 9, 16, 12, 0)

    monkeypatch.setattr(pd, "schema_disponible", lambda c: True)
    monkeypatch.setattr(pd, "corrida_vigente", lambda c, i, h: {
        "CorridaID": 1, "Desde": hoy, "Hasta": hoy + timedelta(days=14)})
    monkeypatch.setattr(pd, "cargar_config", lambda c, i: type("Config", (), {
        "intervalo_min": 30, "skills": [], "pools": {1: type("Pool", (), {"pool_id": 1, "nombre": "General"})()}
    })())
    monkeypatch.setattr(pd, "leer_requerimiento", lambda c, i: [
        {"PoolID": 1, "Pool": "General", "Intervalo": t0, "Brecha": -2, "OperadoresPlanificados": 10, "Llamadas": 50},
        {"PoolID": 1, "Pool": "General", "Intervalo": t0 + timedelta(minutes=30), "Brecha": -2, "OperadoresPlanificados": 10, "Llamadas": 50},
    ])
    monkeypatch.setattr(pd, "leer_pronostico", lambda c, i: [])
    monkeypatch.setattr(ps, "_dimensionar_escenario_alto", lambda *a, **k: {})

    # Migración disponible
    monkeypatch.setattr(pd, "_tiene_tabla", lambda c, t: True)

    pedidos_simulados = [
        {
            "RefuerzoPedidoID": 77, "PoolID": 1, "Dia": date(2026, 9, 16),
            "Desde": t0, "Hasta": t1, "FaltantePico": 2, "HorasOperador": 2.0,
            "Accion": "horas_extra", "Estado": "pedido", "HorasCubiertas": None,
            "CreadoEn": datetime(2026, 9, 14, 12, 0)
        },
        {
            "RefuerzoPedidoID": 70, "PoolID": 1, "Dia": date(2026, 9, 10),
            "Desde": t0, "Hasta": t1, "FaltantePico": 2, "HorasOperador": 4.0,
            "Accion": "horas_extra", "Estado": "cubierto", "HorasCubiertas": 4.0,
            "CreadoEn": datetime(2026, 9, 9, 12, 0)
        }
    ]
    monkeypatch.setattr(pp, "listar_pedidos", lambda c, cid: list(pedidos_simulados))
    monkeypatch.setattr(pp, "cerrar_pedidos_vencidos", lambda *a, **k: 0)

    conn = _Conn()
    engine = _MockEngine(conn)

    res = ps.necesidades(engine, 20, hoy=hoy)
    assert res["resumen"]["seguimiento_disponible"] is True
    # Sólo días cerrados: el pedido del 16/09 todavía no se puede comparar contra
    # lo que pasó, y sumarlo haría que «pedido» y «cubierto» midan cosas distintas.
    assert res["resumen"]["ultimos_14d_horas_pedidas"] == 4.0
    assert res["resumen"]["ultimos_14d_horas_cubiertas"] == 4.0

    refuerzos = res["refuerzos"]
    assert len(refuerzos) == 1
    assert refuerzos[0]["pedido"]["id"] == 77
    assert refuerzos[0]["pedido"]["estado"] == "pedido"


# ---------------------------------------------------- router de refuerzos

def test_router_listar_pedidos(monkeypatch):
    from app.routers import planificador_refuerzos as pr

    monkeypatch.setattr(pr, "_verificar_acceso", lambda cid, u: None)
    monkeypatch.setattr(pp, "cerrar_pedidos_vencidos", lambda *a, **k: 0)
    monkeypatch.setattr(pp, "listar_pedidos", lambda c, cid, d, h: [{"RefuerzoPedidoID": 1, "Estado": "pedido"}])

    conn = _Conn()
    monkeypatch.setattr(pr, "engine", _MockEngine(conn))

    user = type("MockUser", (), {"usuario": 10})()
    res = pr.listar_pedidos_refuerzo(campana_id=20, current_user=user)
    assert len(res["pedidos"]) == 1
    assert res["pedidos"][0]["RefuerzoPedidoID"] == 1


def test_router_crear_pedido(monkeypatch):
    from app.routers import planificador_refuerzos as pr

    monkeypatch.setattr(pr, "_verificar_acceso", lambda cid, u: None)
    monkeypatch.setattr(pr, "registrar_auditoria", lambda *a, **k: None)
    monkeypatch.setattr(pp, "crear_pedido", lambda **k: 99)

    conn = _Conn()
    monkeypatch.setattr(pr, "engine", _MockEngine(conn))

    user = type("MockUser", (), {"usuario": 10})()
    req = pr.PedidoRefuerzoCreateRequest(
        pool_id=1,
        dia=date(2026, 9, 16),
        desde=datetime(2026, 9, 16, 10, 0),
        hasta=datetime(2026, 9, 16, 12, 0),
        faltante_pico=2,
        horas_operador=2.0,
        accion="horas_extra",
        nota="Pedido de prueba"
    )

    res = pr.crear_pedido_refuerzo(payload=req, campana_id=20, current_user=user)
    assert res["ok"] is True
    assert res["pedido_id"] == 99


def test_router_actualizar_pedido(monkeypatch):
    from app.routers import planificador_refuerzos as pr

    monkeypatch.setattr(pr, "_verificar_acceso", lambda cid, u: None)
    monkeypatch.setattr(pr, "registrar_auditoria", lambda *a, **k: None)
    monkeypatch.setattr(pp, "actualizar_pedido", lambda **k: True)

    conn = _Conn()
    monkeypatch.setattr(pr, "engine", _MockEngine(conn))

    user = type("MockUser", (), {"usuario": 10})()
    req = pr.PedidoRefuerzoUpdateRequest(estado="descartado", nota="Cancelado")

    res = pr.actualizar_pedido_refuerzo(pedido_id=99, payload=req, campana_id=20, current_user=user)
    assert res["ok"] is True

