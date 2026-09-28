"""La forma del export a Google Sheets no puede cambiar entre corridas.

`dataframe_a_sheet` ANEXA las filas al final de la hoja cuando la hoja ya existe, sin
volver a escribir los encabezados, y el scheduler manda siempre al mismo `gsheet_name`.
Entonces cualquier columna que aparezca (o desaparezca) del SP desalinea todas las filas
nuevas contra los encabezados viejos del histórico.

Por eso la columna `Incidencia` (migración 2026-08-18b) es OPT-IN: existe en la pantalla
y se puede pedir desde una plantilla de columnas, pero no se agrega sola al export.

Test 100% offline: no toca Sheets, ni la BD, ni tokens.
Correr: pytest tests/test_export_columnas_sheets.py -m "not tokens"
"""
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pandas as pd
import pytest

from app.utils.google_sheet import (
    COLUMNAS_OPT_IN_EXPORT, COLUMNAS_TECNICAS_EXPORT, preparar_export,
)

# Lo que devuelve el SP después de la migración, en su orden real.
COLUMNAS_DEL_SP = [
    "AuditoriaID", "IdAplicativo", "operadorUsuario", "Equipo", "Agente",
    "fecha_interaccion", "FechaAuditoria", "extras", "PuntajeFinal",
    "Incidencia", "EsErrorCritico", "empatia",
    "ExisteTranscripcion", "ExisteResponseThoughts", "ResponseThoughts",
]

# Las que venía subiendo el scheduler ANTES de la migración.
COLUMNAS_HISTORICAS = [c for c in COLUMNAS_DEL_SP
                       if c not in COLUMNAS_TECNICAS_EXPORT + COLUMNAS_OPT_IN_EXPORT]


def _df_del_sp():
    return pd.DataFrame([{c: f"v_{c}" for c in COLUMNAS_DEL_SP}])


class _EngineConPlantilla:
    """Engine falso que devuelve una plantilla de columnas."""

    def __init__(self, columnas):
        self.columnas = columnas

    @contextmanager
    def connect(self):
        columnas = self.columnas

        class _Conn:
            def execute(self, query, params=None):
                fila = {"columns_json": json.dumps(columnas)}
                return SimpleNamespace(mappings=lambda: SimpleNamespace(first=lambda: fila))

        yield _Conn()


def test_sin_plantilla_el_export_conserva_su_forma_de_siempre():
    """El caso del scheduler: sube lo que devuelve el SP. Si la columna nueva se colara,
    las filas quedarían corridas contra los encabezados ya escritos en la hoja."""
    df = preparar_export(engine=None, df=_df_del_sp(), column_template_id=None)
    assert list(df.columns) == COLUMNAS_HISTORICAS
    assert "Incidencia" not in df.columns


def test_una_plantilla_puede_pedir_la_columna_nueva():
    """No queda escondida: quien la quiera en su export la agrega a su plantilla."""
    engine = _EngineConPlantilla(["AuditoriaID", "Incidencia", "PuntajeFinal"])
    df = preparar_export(engine=engine, df=_df_del_sp(), column_template_id=7)
    assert list(df.columns) == ["AuditoriaID", "Incidencia", "PuntajeFinal"]


def test_una_plantilla_que_no_la_pide_no_la_trae():
    engine = _EngineConPlantilla(["AuditoriaID", "PuntajeFinal"])
    df = preparar_export(engine=engine, df=_df_del_sp(), column_template_id=7)
    assert list(df.columns) == ["AuditoriaID", "PuntajeFinal"]


def test_las_tecnicas_nunca_salen():
    """Flags de UI y textos enormes: no van al export ni con plantilla ni sin ella."""
    df = preparar_export(engine=None, df=_df_del_sp(), column_template_id=None)
    for col in COLUMNAS_TECNICAS_EXPORT:
        assert col not in df.columns


def test_df_vacio_no_rompe():
    vacio = pd.DataFrame()
    assert preparar_export(engine=None, df=vacio, column_template_id=None).empty
    assert preparar_export(engine=None, df=None, column_template_id=None) is None


def test_un_sp_viejo_sin_la_columna_sigue_funcionando():
    """Retrocompatible: si la migración todavía no se aplicó, el SP no devuelve
    Incidencia y el drop no tiene que fallar."""
    df_viejo = _df_del_sp().drop(columns=["Incidencia"])
    df = preparar_export(engine=None, df=df_viejo, column_template_id=None)
    assert list(df.columns) == COLUMNAS_HISTORICAS
