"""Qué se ve y qué no en "Solicitudes del Chatbot" (pagina_web.query_chatbots_logs).

Dos recortes distintos sobre el mismo listado:

1. Las corridas del smoke test en vivo (test_chatbots_live.py) consultan los ~13
   bots con USER_ID = 0, así que cada corrida deja una fila por bot sin usuario
   detrás. Salían como "—" mezcladas con las consultas reales y, al ser todas del
   mismo instante, copaban la primera página.

2. Cada usuario ve únicamente los bots a los que tiene acceso. Importa para roles
   de cliente (hoy existe "Cliente Vantix", con chatbot.solicitudes y solo
   chatbot:vantix): sin el filtro, un cliente lee las consultas de los operadores
   de todas las demás campañas.

El filtro va en el SELECT, en el COUNT y en el detalle por id. Si falta en el
COUNT, la tabla muestra las filas correctas pero el total (y con él la
paginación) sigue contando las ocultas. Si falta en el detalle, el recorte del
listado no protege nada: alcanza con pedir ids a mano.

Test 100% offline: la BD se reemplaza por un doble que solo anota las SQL, y
app.routers.chatbot por otro que responde qué bots tiene permitidos el usuario
(el módulo real arrastra app.services y todo el stack de IA).
"""
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import uso_ia


class _Resultado:
    """Lo que devuelve conn.execute(): cubre las cuatro formas de consumo de los
    endpoints (filas del listado, escalar del COUNT, tuplas del selector y la
    fila única del detalle)."""

    def __init__(self, filas):
        self._filas = filas

    def mappings(self):
        return self

    def all(self):
        return self._filas

    def fetchall(self):
        return self._filas

    def first(self):
        return self._filas[0] if self._filas else None

    def scalar(self):
        return 0


class _Conexion:
    def __init__(self, ejecutadas, filas):
        self._ejecutadas = ejecutadas
        self._filas = filas

    def execute(self, query, params=None):
        # str() de un text() con bindparam expandible no muestra los valores
        # (renderiza __[POSTCOMPILE_...]), así que se guardan los params aparte:
        # son ellos los que dicen QUÉ bots se pidieron.
        sql = str(query)
        self._ejecutadas.append((sql, params or {}))
        # Solo el SELECT del detalle tiene que devolver una fila; el resto de las
        # consultas (nombres, títulos) van vacías.
        return _Resultado(self._filas if "WHERE id = :id" in sql else [])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def bd(monkeypatch):
    """Doble de la BD. `ejecutadas` acumula (sql, params); `filas` es lo que
    devuelve el SELECT del detalle."""
    estado = SimpleNamespace(ejecutadas=[], filas=[])
    monkeypatch.setattr(
        uso_ia, "engine",
        SimpleNamespace(connect=lambda: _Conexion(estado.ejecutadas, estado.filas)),
    )
    return estado


@pytest.fixture
def bots_permitidos(monkeypatch):
    """Reemplaza app.routers.chatbot por un doble: _slugs_visibles lo importa
    adentro de la función justamente para no traerse el stack de IA."""
    permitidos: list[str] = []
    modulo = SimpleNamespace(
        get_allowed_bots=lambda user: [SimpleNamespace(slug=s) for s in permitidos]
    )
    monkeypatch.setitem(sys.modules, "app.routers.chatbot", modulo)
    return permitidos


def _user(permisos=(), super_admin=False):
    return SimpleNamespace(
        usuario=123, is_super_admin=super_admin, permissions=list(permisos)
    )


def _calidad():
    """Perfil de Calidad: ve las solicitudes pero no los costos, y con
    chatbot:admin puede probar todos los bots. Es además el único que dispara la
    consulta del selector de chatbots (ver listar_chatbot_logs)."""
    return _user([uso_ia.PERM_CHATBOT_SOLIC, "chatbot:admin"])


def _cliente():
    """Rol de cliente: ve solicitudes, pero de un solo bot."""
    return _user([uso_ia.PERM_CHATBOT_SOLIC, "chatbot:vantix"])


def _listar(**kwargs):
    opciones = dict(
        fecha_desde=None, fecha_hasta=None, chatbot=None, usuario=None, q=None,
        orden=None, dir="desc", limit=25, offset=0, current_user=_calidad(),
    )
    opciones.update(kwargs)
    return uso_ia.listar_chatbot_logs(**opciones)


def _consultas_de_logs(bd):
    return [(s, p) for s, p in bd.ejecutadas if "query_chatbots_logs" in s]


def _listado(bd):
    return [(s, p) for s, p in _consultas_de_logs(bd) if "OFFSET" in s][0]


def _count(bd):
    return [(s, p) for s, p in _consultas_de_logs(bd) if "COUNT(*)" in s][0]


def _selector(bd):
    return [(s, p) for s, p in bd.ejecutadas if "DISTINCT effective_campana" in s][0]


def _exige_filtro_de_bots(sql, params, esperados):
    """El filtro tiene que estar en la SQL y llevar exactamente esos bots."""
    assert "effective_campana IN" in sql, (
        "sin este filtro se leen las consultas de las demás campañas:\n" + sql
    )
    assert set(params["slugs_visibles"]) == set(esperados)


# ------------------------------------------------ consultas del smoke test

def test_el_listado_y_el_total_excluyen_las_consultas_de_prueba(bd):
    _listar()

    assert _consultas_de_logs(bd), "el endpoint no consultó la tabla de logs"
    for sql, _ in (_listado(bd), _count(bd)):
        assert uso_ia._SOLO_USUARIOS_REALES in sql, (
            "esta consulta sigue trayendo las corridas del smoke test:\n" + sql
        )


def test_el_selector_de_chatbots_tambien_los_excluye(bd):
    """Si no, un bot que solo recibió consultas de prueba aparece en el
    desplegable y al elegirlo la tabla queda vacía."""
    _listar()

    assert uso_ia._SOLO_USUARIOS_REALES in _selector(bd)[0]


def test_los_filtros_del_usuario_se_suman_al_de_prueba(bd):
    """El filtro base no puede pisarse cuando el usuario filtra por su cuenta:
    los dos tienen que convivir con AND."""
    _listar(chatbot="voltara", q="tarifa")

    sql, _ = _listado(bd)
    assert uso_ia._SOLO_USUARIOS_REALES in sql
    assert "effective_campana = :chatbot" in sql
    assert "query LIKE :q" in sql


# ------------------------------------------------------ acceso por chatbot

def test_quien_ve_todos_los_bots_no_lleva_filtro(bd, bots_permitidos):
    """chatbot:admin (calidad) puede probar todos los bots, así que también ve
    todos los logs: filtrar acá le escondería trabajo suyo."""
    assert uso_ia._slugs_visibles(_calidad()) is None

    _listar(current_user=_calidad())
    sql, params = _listado(bd)
    assert "effective_campana IN" not in sql
    assert "slugs_visibles" not in params


def test_el_super_admin_no_lleva_filtro():
    assert uso_ia._slugs_visibles(_user(super_admin=True)) is None


def test_un_cliente_solo_ve_su_bot(bd, bots_permitidos):
    bots_permitidos.append("vantix")

    assert uso_ia._slugs_visibles(_cliente()) == {"vantix"}

    _listar(current_user=_cliente())
    for sql, params in (_listado(bd), _count(bd)):
        _exige_filtro_de_bots(sql, params, {"vantix"})


def test_el_selector_solo_ofrece_los_bots_visibles(bd, bots_permitidos):
    """Si no, el cliente ve en el desplegable los nombres de las campañas ajenas
    aunque no pueda abrir sus filas."""
    bots_permitidos.append("vantix")

    _listar(current_user=_cliente())

    _exige_filtro_de_bots(*_selector(bd), {"vantix"})


def test_se_aceptan_los_nombres_de_campana_viejos(bots_permitidos):
    """effective_campana guarda la campaña con la que se consultó y las filas
    previas a la convención de slug traen el formato con espacios ("csv no
    premium"). Sin las dos formas, el historial del grupo CSV se ve a medias."""
    bots_permitidos.extend(["csv_no_premium", "csv_vip"])

    visibles = uso_ia._slugs_visibles(_user([uso_ia.PERM_CHATBOT_SOLIC, "chatbot:csv"]))

    assert visibles == {"csv_no_premium", "csv_vip", "csv no premium", "csv vip"}


def test_sin_bots_permitidos_no_se_ve_nada(bd, bots_permitidos):
    """Un usuario con el permiso de la pantalla pero sin ningún bot asignado
    tiene que ver la tabla vacía, no la tabla entera."""
    assert uso_ia._slugs_visibles(_user([uso_ia.PERM_CHATBOT_SOLIC])) == set()

    _listar(current_user=_user([uso_ia.PERM_CHATBOT_SOLIC]))
    for sql, _ in (_listado(bd), _count(bd)):
        assert "1 = 0" in sql


def test_pedir_un_bot_ajeno_por_parametro_no_lo_destapa(bd, bots_permitidos):
    """El ?chatbot= del frontend no puede saltear el recorte: los dos filtros
    van con AND y el resultado queda vacío."""
    bots_permitidos.append("vantix")

    _listar(current_user=_cliente(), chatbot="voltara")

    sql, params = _listado(bd)
    _exige_filtro_de_bots(sql, params, {"vantix"})
    assert "effective_campana = :chatbot" in sql


# ------------------------------------------------------- detalle por id

def _fila(campana):
    return {
        "id": 1, "fecha": None, "user_id": 42, "effective_campana": campana,
        "query": "hola", "query_condensada": None, "response": "chau",
        "context": None, "input_tokens": 1, "output_tokens": 2,
        "embedding_tokens": 3, "task_id": "t", "active": True,
    }


def test_el_detalle_de_un_bot_ajeno_no_se_abre(bd, bots_permitidos):
    """El agujero real: con el listado filtrado pero el detalle abierto, alcanza
    con pedir ids a mano para leer las consultas de cualquier campaña."""
    bots_permitidos.append("vantix")
    bd.filas = [_fila("voltara")]

    with pytest.raises(HTTPException) as exc:
        uso_ia.detalle_chatbot_log(log_id=1, current_user=_cliente())

    # 404 y no 403: un 403 confirmaría que el id existe.
    assert exc.value.status_code == 404


def test_el_detalle_del_bot_propio_se_abre(bd, bots_permitidos):
    bots_permitidos.append("vantix")
    bd.filas = [_fila("vantix")]

    r = uso_ia.detalle_chatbot_log(log_id=1, current_user=_cliente())

    assert r["effective_campana"] == "vantix"


def test_el_detalle_no_se_filtra_para_quien_ve_todo(bd, bots_permitidos):
    bd.filas = [_fila("voltara")]

    r = uso_ia.detalle_chatbot_log(log_id=1, current_user=_calidad())

    assert r["effective_campana"] == "voltara"
