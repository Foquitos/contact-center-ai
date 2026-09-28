"""
Verifica que todas las vistas de las que dependen los agentes del
ChatbotSQLService (hidra, odonto plus, plansenior, general) existan y sean
consultables en la base `chatbot`. No invoca al LLM (cero tokens): toma la
lista canónica de los contextos a nivel de módulo y consulta cada vista con
`SELECT TOP 0` (no devuelve filas).

Si fallan las vistas nuevas, probablemente falte aplicar las migraciones
scripts/migrations/2026-06-12_chatbot_vistas_gerencia.sql y
2026-06-12_dental_canales.sql.
"""
import pytest
from sqlalchemy import text

from chatbot_sql_service import (
    CONTEXTO_HIDRA,
    CONTEXTO_DENTAL,
    CONTEXTO_GENERAL,
    CONTEXTO_PLANSENIOR,
)

VISTAS = sorted({**CONTEXTO_GENERAL, **CONTEXTO_HIDRA, **CONTEXTO_DENTAL, **CONTEXTO_PLANSENIOR}.keys())


def test_hay_vistas_definidas():
    assert VISTAS, "El chatbot no tiene vistas/contexto definidos"


@pytest.mark.parametrize("vista", VISTAS)
def test_vista_existe_y_es_consultable(vista, engine_chatbot):
    """Cada vista referenciada por los agentes debe poder consultarse en schema chatbot."""
    with engine_chatbot.connect() as conn:
        # TOP 0: valida nombre/esquema y que la vista compile, sin traer datos.
        conn.execute(text(f"SELECT TOP 0 * FROM chatbot.[{vista}]"))
