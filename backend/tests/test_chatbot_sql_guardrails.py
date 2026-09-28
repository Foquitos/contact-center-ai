"""
Tests offline (cero tokens, cero DB) de los guardarraíles del chatbot SQL:
la validación del SQL generado por el LLM, el formateo del historial y la
conversión de los modelos de salida estructurada al schema de Gemini.
"""
import pytest

from chatbot_sql_service import (
    AnalisisRespuesta,
    ConfiguracionGrafico,
    ConsultaSQLGenerada,
    PlanOrquestador,
    SubConsulta,
    validar_sql_seguro,
    _formatear_historial,
)


# ------------------------------ SQL aceptado ------------------------------ #

@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM chatbot.vw_equipos",
    "select top (10) motivo from chatbot.vw_sar_ingresos_Hidra",
    "WITH x AS (SELECT 1 AS a) SELECT * FROM x",
])
def test_acepta_select_validos(sql):
    assert validar_sql_seguro(sql)


def test_limpia_fences_markdown_y_punto_y_coma_final():
    sql = "```sql\nSELECT 1 AS a;\n```"
    assert validar_sql_seguro(sql) == "SELECT 1 AS a"


# ------------------------------ SQL rechazado ----------------------------- #

@pytest.mark.parametrize("sql", [
    "",                                                   # vacío
    "DELETE FROM chatbot.vw_equipos",                     # no es SELECT
    "SELECT 1; DROP TABLE usuarios",                      # multi-sentencia
    "SELECT * FROM chatbot.vw_equipos; --",               # ';' intermedio
    "INSERT INTO x VALUES (1)",
    "UPDATE x SET a = 1",
    "EXEC sp_who",
    "SELECT * INTO y FROM chatbot.vw_equipos WHERE EXEC('x') = 1",
    "SELECT * FROM OPENQUERY(ORION_LINK, 'select 1')",    # linked server prohibido
    "SELECT * FROM chatbot.vw_equipos LIMIT 10",          # LIMIT no es T-SQL
])
def test_rechaza_sql_peligroso_o_invalido(sql):
    with pytest.raises(ValueError):
        validar_sql_seguro(sql)


# -------------------------------- Historial ------------------------------- #

def test_historial_vacio_devuelve_string_vacio():
    assert _formatear_historial(None) == ""
    assert _formatear_historial([]) == ""


def test_historial_formatea_roles_y_limita_mensajes():
    historial = [{"rol": "user", "texto": f"pregunta {i}"} for i in range(20)]
    historial.append({"rol": "bot", "texto": "respuesta final"})
    texto = _formatear_historial(historial)

    assert "HISTORIAL" in texto
    assert "Usuario: pregunta 19" in texto
    assert "Asistente: respuesta final" in texto
    # Solo viajan los últimos 8 mensajes.
    assert "pregunta 12" not in texto


# ------------------------- Schemas de salida -> Gemini --------------------- #

@pytest.mark.parametrize("modelo", [
    ConsultaSQLGenerada, SubConsulta, PlanOrquestador, ConfiguracionGrafico, AnalisisRespuesta,
])
def test_modelos_estructurados_convierten_a_schema_gemini(modelo):
    """astructured_predict manda estos modelos a Gemini como `response_schema`
    (salida estructurada nativa del SDK google-genai; antes era function calling
    con el protobuf de google-generativeai). Validamos la conversión completa de
    forma local, sin llamar a la API ni gastar tokens: un modelo que no sepa
    convertirse revienta recién en producción."""
    from google.genai import _transformers, types
    from google.genai._api_client import BaseApiClient

    schema = _transformers.t_schema(BaseApiClient(api_key="dummy"), modelo)
    assert isinstance(schema, types.Schema)
    assert schema.properties, f"{modelo.__name__} se convirtió a un schema sin propiedades"
