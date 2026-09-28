"""
Historial y multi-chat del Asistente Analítico del Dashboard.

Foco de este archivo: lo que se puede romper en silencio.
  1. El buscador del panel arma un LIKE con lo que escribe el usuario. En T-SQL
     los corchetes son una CLASE de caracteres: buscar "[Adjuntos" sin escapar
     devuelve medio historial. Ya nos pasó midiendo adopción del chatbot.
  2. Cada consulta tiene que filtrar por el dueño: los chats son privados.
  3. El título automático no puede cortar una palabra al medio ni pasarse del
     largo de la columna.

Test 100% offline: el engine está mockeado, no toca DB ni IA.
"""
import pytest

from app import asistente_conversaciones as ac


# --------------------------------------------------------------------------- #
# Engine falso: registra el SQL y los parámetros de cada execute              #
# --------------------------------------------------------------------------- #
class _FakeResult:
    def __init__(self, filas=None, scalar=None, rowcount=1):
        self._filas = filas or []
        self._scalar = scalar
        self.rowcount = rowcount

    def mappings(self):
        return self

    def all(self):
        return self._filas

    def first(self):
        return self._filas[0] if self._filas else None

    def scalar(self):
        return self._scalar


class _FakeConn:
    def __init__(self, registro, resultados):
        self.registro = registro
        self.resultados = list(resultados)

    def execute(self, sql, params=None):
        self.registro.append((str(sql), params or {}))
        return self.resultados.pop(0) if self.resultados else _FakeResult()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeEngine:
    def __init__(self, *resultados):
        self.registro = []
        self._resultados = resultados

    def connect(self):
        return _FakeConn(self.registro, self._resultados)

    begin = connect


@pytest.fixture
def engine_falso(monkeypatch):
    def _armar(*resultados):
        fake = _FakeEngine(*resultados)
        monkeypatch.setattr(ac, "engine", fake)
        return fake
    return _armar


# --------------------------------------------------------------------------- #
# Buscador                                                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("entrada,esperado", [
    ("[Adjuntos", r"%\[Adjuntos%"),
    ("100%", r"%100\%%"),
    ("guion_bajo", r"%guion\_bajo%"),
    ("a\\b", r"%a\\b%"),
])
def test_busqueda_escapa_comodines(engine_falso, entrada, esperado):
    """Sin escapar, '[' matchea una clase de caracteres y '%'/'_' son comodines:
    el buscador devolvería conversaciones que no tienen nada que ver."""
    fake = engine_falso(_FakeResult([]))
    ac.listar(user_id=123, q=entrada)
    sql, params = fake.registro[0]
    assert params["q"] == esperado
    assert "ESCAPE '\\'" in sql


def test_busqueda_mira_titulo_y_mensajes(engine_falso):
    """Uno se acuerda de lo que preguntó, no de cómo quedó titulado el chat."""
    fake = engine_falso(_FakeResult([]))
    ac.listar(user_id=123, q="error crítico")
    sql, _ = fake.registro[0]
    assert "c.Titulo LIKE :q" in sql
    assert "AsistenteMensajes" in sql


def test_sin_termino_no_arma_filtro(engine_falso):
    fake = engine_falso(_FakeResult([]))
    ac.listar(user_id=123, q="   ")
    sql, params = fake.registro[0]
    assert "q" not in params
    assert "LIKE" not in sql


# --------------------------------------------------------------------------- #
# Privacidad: todo filtra por dueño                                            #
# --------------------------------------------------------------------------- #
def test_listar_filtra_por_usuario(engine_falso):
    fake = engine_falso(_FakeResult([]))
    ac.listar(user_id=777)
    sql, params = fake.registro[0]
    assert "c.UsuarioId = :uid" in sql
    assert params["uid"] == 777


def test_obtener_ajeno_devuelve_none(engine_falso):
    """La cabecera no aparece para otro dueño => ni se leen los mensajes."""
    fake = engine_falso(_FakeResult([]))
    assert ac.obtener(user_id=777, conv_id=5) is None
    assert len(fake.registro) == 1  # no llegó a pedir los mensajes


def test_eliminar_es_soft_delete_del_dueno(engine_falso):
    fake = engine_falso(_FakeResult(rowcount=1))
    assert ac.eliminar(user_id=777, conv_id=5) is True
    sql, params = fake.registro[0]
    assert "SET Activo = 0" in sql
    assert "UsuarioId = :uid" in sql
    assert params == {"id": 5, "uid": 777}


def test_eliminar_ajeno_no_borra_nada(engine_falso):
    engine_falso(_FakeResult(rowcount=0))
    assert ac.eliminar(user_id=1, conv_id=5) is False


def test_historial_para_modelo_viene_en_orden_cronologico(engine_falso):
    """La base devuelve DESC (para agarrar los últimos N); el modelo los necesita
    en el orden en que se dijeron."""
    engine_falso(_FakeResult([
        {"Rol": "bot", "Texto": "respuesta 2"},
        {"Rol": "user", "Texto": "pregunta 2"},
        {"Rol": "bot", "Texto": "respuesta 1"},
        {"Rol": "user", "Texto": "pregunta 1"},
    ]))
    turnos = ac.historial_para_modelo(user_id=1, conv_id=9)
    assert [t["texto"] for t in turnos] == [
        "pregunta 1", "respuesta 1", "pregunta 2", "respuesta 2",
    ]


def test_historial_ajeno_sale_vacio(engine_falso):
    fake = engine_falso(_FakeResult([]))
    assert ac.historial_para_modelo(user_id=1, conv_id=9) == []
    sql, params = fake.registro[0]
    assert "c.UsuarioId = :uid" in sql
    assert params["uid"] == 1


# --------------------------------------------------------------------------- #
# Mensajes                                                                     #
# --------------------------------------------------------------------------- #
def test_agregar_mensaje_toca_la_marca_de_uso(engine_falso):
    """El panel ordena por ActualizadoEn: sin el UPDATE, un chat activo se
    hunde al fondo de la lista."""
    fake = engine_falso(_FakeResult(), _FakeResult())
    ac.agregar_mensaje(conv_id=3, rol="bot", texto="informe")
    assert "INSERT INTO calidad.AsistenteMensajes" in fake.registro[0][0]
    assert "ActualizadoEn = SYSUTCDATETIME()" in fake.registro[1][0]


@pytest.mark.parametrize("rol,texto", [("system", "hola"), ("user", "   "), ("user", "")])
def test_agregar_mensaje_descarta_basura(engine_falso, rol, texto):
    fake = engine_falso()
    ac.agregar_mensaje(conv_id=3, rol=rol, texto=texto)
    assert fake.registro == []


def test_guardar_mensaje_no_puede_tumbar_la_respuesta(engine_falso, monkeypatch):
    """Perder el guardado es molesto; que explote la respuesta que el usuario ya
    está leyendo en pantalla, no."""
    class _Roto:
        def begin(self):
            raise RuntimeError("SQL caído")
    monkeypatch.setattr(ac, "engine", _Roto())
    ac.agregar_mensaje(conv_id=3, rol="user", texto="pregunta")  # no levanta


# --------------------------------------------------------------------------- #
# Título automático y alcance                                                  #
# --------------------------------------------------------------------------- #
def test_titulo_corto_queda_igual():
    assert ac.titulo_desde_pregunta("¿Cuántos errores críticos hubo?") == "¿Cuántos errores críticos hubo?"


def test_titulo_largo_corta_en_palabra_entera():
    pregunta = (
        "Generá un resumen ejecutivo completo de las auditorías de este período "
        "destacando los hallazgos"
    )
    titulo = ac.titulo_desde_pregunta(pregunta)
    assert titulo.endswith("…")
    assert len(titulo) <= 71
    assert not titulo[:-1].endswith(" ")
    assert pregunta.startswith(titulo[:-1])


def test_titulo_normaliza_saltos_de_linea():
    assert ac.titulo_desde_pregunta("  hola\n\n  mundo ") == "hola mundo"


def test_titulo_vacio_tiene_fallback():
    assert ac.titulo_desde_pregunta("   ") == "Consulta sin título"


def test_titulo_respeta_el_largo_de_la_columna(engine_falso):
    fake = engine_falso(_FakeResult(scalar=1))
    ac.crear(user_id=1, titulo="x" * 500)
    assert len(fake.registro[0][1]["titulo"]) == ac.MAX_TITULO


def test_alcance_se_guarda_como_json_y_desnormaliza_ids(engine_falso):
    """PlantillaID/CampanaId salen del alcance para poder filtrar sin abrir el JSON."""
    fake = engine_falso(_FakeResult(rowcount=1))
    ac.actualizar(user_id=1, conv_id=2, alcance={"plantilla_id": "44", "campana_id": "7", "empresa_nombre": "Voltara"})
    _, params = fake.registro[0]
    assert params["plantilla"] == 44
    assert params["campana"] == 7
    assert '"empresa_nombre": "Voltara"' in params["alcance"]


def test_alcance_vacio_no_ensucia_la_columna(engine_falso):
    fake = engine_falso(_FakeResult(scalar=1))
    ac.crear(user_id=1, titulo="t", alcance={})
    assert fake.registro[0][1]["alcance"] is None


def test_actualizar_sin_cambios_no_escribe(engine_falso):
    """Un PUT vacío no puede pisar el título con None."""
    fake = engine_falso(_FakeResult(scalar=1))
    ac.actualizar(user_id=1, conv_id=2)
    assert "UPDATE" not in fake.registro[0][0]


# --------------------------------------------------------------------------- #
# Bind de texto                                                                #
# --------------------------------------------------------------------------- #
def test_los_textos_se_bindean_como_nvarchar():
    """Las respuestas del asistente traen flechas, guiones largos y a veces emoji.
    Bindeados como VARCHAR, el driver los pasa a la code page ANSI ANTES de
    insertar y quedan como '?': migrar la columna a NVARCHAR no alcanza porque el
    daño ocurre en el bind (mismo problema que tuvo query_chatbots_logs)."""
    from sqlalchemy.dialects import mssql

    binds = ac._INSERT_MENSAJE.compile(dialect=mssql.dialect()).binds
    assert type(binds["txt"].type).__name__ == "NVARCHAR"

    binds = ac._INSERT_CONVERSACION.compile(dialect=mssql.dialect()).binds
    for campo in ("titulo", "alcance"):
        assert type(binds[campo].type).__name__ == "NVARCHAR"
