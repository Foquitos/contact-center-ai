"""
Configuración compartida de la suite de tests.

Objetivo: validar que las auditorías de cada campaña, sus filtros, las consultas
SQL y los chatbots estén funcionando, gastando la menor cantidad posible de
tokens de IA. Por eso:

- Los tests de SQL de auditoría NO ejecutan las queries (no escanean datos ni
  gastan tokens): generan el SQL y lo validan contra SQL Server con
  `sys.dm_exec_describe_first_result_set` (verifica sintaxis + nombres de
  columnas/tablas) o, cuando no es viable (tablas temporales / linked server),
  con `SET PARSEONLY ON` (solo sintaxis).
- Los tests de chatbots en vivo (que sí gastan tokens) viven en
  `tests/test_chatbots_live.py` y están marcados con `@pytest.mark.tokens`
  para poder excluirlos con `-m "not tokens"`.
"""
import os
import sys

import pytest
from sqlalchemy import create_engine, text

# Garantiza que el backend esté en sys.path (imports tipo `app`, `AuditorIA`).
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.config import settings  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "tokens: tests que invocan modelos de IA y consumen tokens (se excluyen con -m 'not tokens')",
    )


# --------------------------------------------------------------------------- #
# Engines reales (las validaciones de SQL no escanean datos ni gastan tokens)  #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def engine():
    """Engine a la base principal `Acme` (auditorías)."""
    eng = create_engine(settings.connection_string)
    yield eng
    eng.dispose()


@pytest.fixture(scope="session")
def engine_chatbot():
    """Engine a la base del chatbot (schema `chatbot`)."""
    eng = create_engine(settings.CONNECTION_STRING_chatbot)
    yield eng
    eng.dispose()


# --------------------------------------------------------------------------- #
# Validador de SQL sin ejecución                                              #
# --------------------------------------------------------------------------- #
class SQLValidationResult:
    def __init__(self, ok: bool, method: str, error: str = ""):
        self.ok = ok
        self.method = method  # "describe" | "parseonly"
        self.error = error

    def __bool__(self):
        return self.ok

    def __repr__(self):
        estado = "OK" if self.ok else "FAIL"
        return f"<SQL {estado} via {self.method}{': ' + self.error if self.error else ''}>"


def _validar_parseonly(eng, query: str) -> SQLValidationResult:
    """
    Valida SOLO la sintaxis (no compila binding ni ejecuta). Soporta temp tables
    y multi-statement. PARSEONLY debe activarse en un batch separado del query,
    de lo contrario no surte efecto y el query terminaría ejecutándose.
    """
    raw = eng.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("SET PARSEONLY ON")   # batch propio: recién así aplica al siguiente
        try:
            cur.execute(query)            # solo se parsea; no se ejecuta ni escanea datos
            return SQLValidationResult(True, "parseonly")
        except Exception as ex:           # pyodbc.ProgrammingError -> error de sintaxis
            return SQLValidationResult(False, "parseonly", str(ex).splitlines()[0][:300])
        finally:
            try:
                cur.execute("SET PARSEONLY OFF")
            except Exception:
                pass
    finally:
        raw.close()


def _validar_describe(eng, query: str, reintentos: int = 2) -> SQLValidationResult:
    """
    Valida sintaxis + binding (nombres de columnas/tablas) SIN ejecutar la query
    (la query viaja como parámetro al DMV). No soporta tablas temporales ni, de
    forma fiable, linked servers. Reintenta ante errores transitorios (timeouts).
    """
    sql = (
        "SELECT error_message "
        "FROM sys.dm_exec_describe_first_result_set(:q, NULL, 0) "
        "WHERE error_message IS NOT NULL"
    )
    ultimo_error = None
    for intento in range(reintentos + 1):
        try:
            with eng.connect() as conn:
                conn.connection.timeout = 30  # corta hangs transitorios
                rows = conn.execute(text(sql), {"q": query}).fetchall()
            if rows:
                # error_message poblado = error determinístico de binding/sintaxis
                return SQLValidationResult(False, "describe", str(rows[0][0])[:300])
            return SQLValidationResult(True, "describe")
        except Exception as ex:
            ultimo_error = str(ex).splitlines()[0][:300]
    # describe no pudo evaluarse (no es un veredicto de la query en sí)
    return SQLValidationResult(False, "describe-error", ultimo_error or "describe falló")


def _solo_sintaxis(query: str) -> bool:
    """Temp tables o linked server: describe no aplica, validamos solo sintaxis."""
    upper = query.upper()
    return "#" in query or "ORION_LINK" in upper


@pytest.fixture(scope="session")
def validar_sql(engine):
    """
    Devuelve una función validar_sql(query) -> SQLValidationResult.

    - Queries normales: `describe` (sintaxis + nombres de columnas/tablas), sin ejecutar.
    - Queries con temp tables / linked server: `PARSEONLY` (solo sintaxis), sin ejecutar.
    Si `describe` no logra evaluar (error transitorio/permisos), cae a PARSEONLY
    para al menos verificar la sintaxis y no reportar un falso positivo.
    """
    def _validar(query: str) -> SQLValidationResult:
        if _solo_sintaxis(query):
            return _validar_parseonly(engine, query)
        res = _validar_describe(engine, query)
        if res.method == "describe-error":
            # describe no concluyó: confirmamos al menos la sintaxis.
            return _validar_parseonly(engine, query)
        return res

    return _validar
