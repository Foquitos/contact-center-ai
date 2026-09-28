"""
Tests de regresión para el cruce de turnos en Odonto Plus (get_filtered_data_Odonto_plus).

Valida que:
1. La consulta SQL seleccione y propague el campo `Cliente` (número telefónico/identificador)
   a través de `#InteraccionesBase` y `#SeleccionAleatoria`.
2. El OUTER APPLY con `Odonto_Plus_Turnero` realice cruce prioritario por número de teléfono
   (celular / teléfono fijo contra Cliente).
3. El fallback por ventana temporal entre llamadas consecutivas aplique ÚNICAMENTE a
   llamadas telefónicas (`ISNULL(s.Chat, 0) = 0`), protegiendo a los chats de asignaciones cruzadas
   de turnos de otros pacientes atendidos en paralelo.
4. El filtro de comentarios sobre observaciones (`comentario_filter`) replique la misma lógica dual.
"""
from unittest.mock import MagicMock
import AuditorIA.SQL_query as q


def test_dental_query_contiene_cruce_por_telefono_y_proteccion_chat():
    """Verifica que la consulta generada incluya el campo Cliente y la lógica dual de matching."""
    captured_query = None

    def fake_read_sql(sql, engine):
        nonlocal captured_query
        captured_query = sql
        import pandas as pd
        return pd.DataFrame()

    mock_engine = MagicMock()

    # Interceptamos pd.read_sql
    orig_read_sql = q.pd.read_sql
    try:
        q.pd.read_sql = fake_read_sql
        q.get_filtered_data_Odonto_plus(
            engine=mock_engine,
            cantidad=5,
            idInteraccion=["260915120620794_CHT_23000"],
            comentario=["reprogramacion"],
        )
    finally:
        q.pd.read_sql = orig_read_sql

    assert captured_query is not None, "No se capturó la consulta SQL"

    # 1. d.Cliente en #InteraccionesBase
    assert "d.Cliente" in captured_query, "Falta d.Cliente en la selección de #InteraccionesBase"

    # 2. Cliente en #SeleccionAleatoria
    assert "Cliente" in captured_query, "Falta Cliente en #SeleccionAleatoria"

    # 3. Cruce telefónico en OUTER APPLY
    assert "s.Cliente LIKE '%' + RIGHT(dt.celular, 8) + '%'" in captured_query
    assert "s.Cliente LIKE '%' + RIGHT(dt.telefono, 8) + '%'" in captured_query

    # 4. Protección para chats: fallback de ventana solo con ISNULL(s.Chat, 0) = 0
    assert "ISNULL(s.Chat, 0) = 0" in captured_query
    assert "ISNULL(g.Chat, 0) = 0" in captured_query

    # 5. Priorización en ORDER BY (0 para match telefónico, 1 para fallback)
    assert "CASE" in captured_query
    assert "ABS(DATEDIFF(SECOND, s.inicio, dt.FechaAlta)) ASC" in captured_query
