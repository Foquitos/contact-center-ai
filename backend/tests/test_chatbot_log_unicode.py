"""El log de consultas tiene que conservar los emoji.

Por qué importa más de lo que parece: el system prompt de los bots pide arrancar
las respuestas con 🖥️ (acción en el sistema) o 🗣️ (qué decirle al cliente), y
_get_memory_for_user RELEE la columna `response` para reconstruir el historial del
operador. Si el emoji se guarda como '?', el bot recibe su propia respuesta rota
como contexto de la conversación.

La causa era pandas.to_sql: con dtype `object` bindea el parámetro como VARCHAR y
el driver convierte el texto a su code page ANTES de insertarlo, así que migrar la
columna a NVARCHAR no alcanzaba (el daño ocurre en el bind). Verificado contra la
base: CAST(N'🖥️ Ingresá' AS VARCHAR) -> '??? Ingresá' (los acentos sobreviven,
los emoji no; por eso el problema pasó desapercibido).
"""
import re
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import mssql

import chatBot


TEXTOS = ("query", "response", "context", "effective_campana")


def test_los_textos_se_bindean_como_nvarchar():
    binds = chatBot._INSERT_LOG.compile(dialect=mssql.dialect()).binds
    for campo in TEXTOS:
        assert type(binds[campo].type).__name__ == "NVARCHAR", (
            f"'{campo}' no se bindea como NVARCHAR: los emoji se guardarían como '?'"
        )


def test_no_se_vuelve_a_usar_pandas_to_sql():
    """Guarda de regresión: to_sql es cómodo pero no sabe que las columnas son
    NVARCHAR y vuelve a romper los emoji en silencio."""
    fuente = open(chatBot.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    # La LLAMADA, no la palabra: los comentarios explican por qué no se usa.
    assert not re.search(r"\.to_sql\s*\(", fuente), (
        "Volvió pandas.to_sql al log del chatbot: bindea como VARCHAR y pierde los emoji."
    )


def test_los_numeros_de_numpy_se_convierten_a_nativos():
    """pyodbc no sabe bindear numpy.float32 y los scores del reranker lo son:
    'Invalid parameter type. param-index=9 param-type=numpy.float32'. pandas.to_sql
    lo convertía solo; con el INSERT explícito hay que hacerlo a mano."""
    import numpy as np

    assert type(chatBot._num(np.float32(-1.236), float)) is float
    assert type(chatBot._num(np.int64(1534), int)) is int


def test_num_tolera_none_y_basura():
    """El log es accesorio: nunca puede caerse por un valor raro."""
    assert chatBot._num(None, float) is None
    assert chatBot._num("no es un numero", int) is None


def test_score_maximo_devuelve_float_nativo():
    """La conversión se hace en el origen, donde el valor entra al sistema."""
    import numpy as np

    from app.vacios_conocimiento import score_maximo

    nodos = [MagicMock(score=np.float32(-3.5)), MagicMock(score=np.float32(1.25))]
    resultado = score_maximo(nodos)
    assert type(resultado) is float
    assert resultado == pytest.approx(1.25)


def test_el_insert_cubre_todas_las_columnas_que_arma_el_log():
    """Si alguien agrega una clave al diccionario del log y se olvida del INSERT,
    SQLAlchemy falla recién en runtime con un parámetro no consumido."""
    fuente = open(chatBot.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    bloque = fuente.split("diccionario = {", 1)[1].split("}", 1)[0]
    claves = set(re.findall(r"[\"'](\w+)[\"']\s*:", bloque))
    parametros = set(re.findall(r":(\w+)", str(chatBot._INSERT_LOG)))
    assert claves == parametros, (
        f"El diccionario del log y el INSERT no coinciden. "
        f"Sobran en el dict: {claves - parametros}. Faltan en el dict: {parametros - claves}."
    )
