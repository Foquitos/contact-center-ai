"""
Verifica el ruteo de `descarga_sin_verificar`: cada nombre de empresa debe caer
en el builder de SQL correcto y los filtros deben mapearse bien (p. ej. en ALARMIX
`sentido`->`direccion`, `campana`->`skill`, `loginid`->`Empleado`).

Test 100% offline: se reemplazan los builders y la descarga de audios por stubs.
"""
import pandas as pd
import pytest

import AuditorIA.Descarga as D

# Voltara ya NO rutea por descarga_sin_verificar: audita por subida de archivos
# (audios + Excel nombre->ConnID) y arma su df en Auditor.__descarga_audios vía
# AuditorIA/Voltara.py, sin pasar por este dispatcher. Su SQL se valida aparte en
# test_auditoria_sql.py::test_sql_voltara_upload.
BUILDERS = [
    "get_filtered_data_Aurora_salud",
    "get_filtered_data_Odonto_plus",
    "get_filtered_data_Hidra_comercial",
    "get_filtered_data_Hidra",
    "get_filtered_data_ALARMIX",
    "get_filtered_data_Vitalis_Salud",
    "get_filtered_data_Farmalux",
    "get_filtered_data_Vantix",
    "get_filtered_data_Benefix",
    "get_filtered_data_mitrol_puro",
]

# empresa -> builder esperado
RUTEO = [
    ("AuroraSalud", "get_filtered_data_Aurora_salud"),
    ("Odonto Plus", "get_filtered_data_Odonto_plus"),
    ("Facebook", "get_filtered_data_Odonto_plus"),
    ("HIDRA Comercial", "get_filtered_data_Hidra_comercial"),
    ("HIDRA", "get_filtered_data_Hidra"),
    ("ALARMIX", "get_filtered_data_ALARMIX"),
    ("Vitalis Salud", "get_filtered_data_Vitalis_Salud"),
    ("Farmalux", "get_filtered_data_Farmalux"),
    ("Track On", "get_filtered_data_Vantix"),
    ("Trackon-Vantix", "get_filtered_data_Vantix"),
    ("Benefix", "get_filtered_data_Benefix"),
    ("CualquierOtra", "get_filtered_data_mitrol_puro"),  # rama else
]


@pytest.fixture
def grabador(monkeypatch):
    """Reemplaza todos los builders y la descarga de audios por stubs que registran llamadas."""
    llamadas = {}

    def make_stub(nombre):
        def stub(**kwargs):
            llamadas[nombre] = kwargs
            return pd.DataFrame()
        return stub

    for b in BUILDERS:
        monkeypatch.setattr(D, b, make_stub(b))
    # Evita cualquier descarga real de audio
    monkeypatch.setattr(D, "get_audio_bytes", lambda df, *a, **k: df)
    return llamadas


@pytest.mark.parametrize("empresa,builder_esperado", RUTEO, ids=[r[0] for r in RUTEO])
def test_ruteo_empresa_a_builder(empresa, builder_esperado, grabador):
    D.descarga_sin_verificar(
        engine=None, api=None, cantidad=1, empresa=[empresa],
        loginid=["op1"], campana=["c1"], tipificacion=["t1"], sentido=["Entrante"],
    )
    assert builder_esperado in grabador, (
        f"empresa '{empresa}' no llamó al builder esperado. Llamados: {list(grabador)}"
    )
    otros = [b for b in grabador if b != builder_esperado]
    assert not otros, f"empresa '{empresa}' llamó builders de más: {otros}"


def test_mapeo_filtros_ALARMIX(grabador):
    """ALARMIX renombra filtros: sentido->direccion, campana->skill, loginid->Empleado."""
    D.descarga_sin_verificar(
        engine=None, api=None, cantidad=1, empresa=["ALARMIX"],
        loginid=["op1"], campana=["skillX"], sentido=["Entrante"], tipificacion=["t1"],
    )
    kw = grabador["get_filtered_data_ALARMIX"]
    assert kw["direccion"] == ["Entrante"]
    assert kw["skill"] == ["skillX"]
    assert kw["Empleado"] == ["op1"]


def test_mapeo_filtros_Vitalis(grabador):
    """Vitalis Salud mapea sentido->direccion."""
    D.descarga_sin_verificar(
        engine=None, api=None, cantidad=1, empresa=["Vitalis Salud"], sentido=["Saliente"],
    )
    kw = grabador["get_filtered_data_Vitalis_Salud"]
    assert kw["direccion"] == ["Saliente"]


# Builders que aceptan el filtro de sentido, con el nombre del kwarg que usa cada uno.
# HIDRA Comercial y Farmalux quedan afuera a propósito: sus builders no lo soportan.
SENTIDO_POR_BUILDER = {
    "get_filtered_data_Aurora_salud": "sentido",
    "get_filtered_data_Odonto_plus": "sentido",
    "get_filtered_data_Hidra": "sentido",
    "get_filtered_data_ALARMIX": "direccion",
    "get_filtered_data_Vitalis_Salud": "direccion",
    "get_filtered_data_Vantix": "sentido",
    "get_filtered_data_Benefix": "sentido",
    "get_filtered_data_mitrol_puro": "sentido",
}

RUTEO_CON_SENTIDO = [(e, b) for e, b in RUTEO if b in SENTIDO_POR_BUILDER]


@pytest.mark.parametrize("empresa,builder", RUTEO_CON_SENTIDO,
                         ids=[r[0] for r in RUTEO_CON_SENTIDO])
def test_sentido_llega_al_builder(empresa, builder, grabador):
    """Regresión (2026-07-30): Vantix y la rama Mitrol recibían el filtro de sentido y no
    se lo pasaban a su builder, así que la auditoría filtrada por "Salientes" traía
    también entrantes. Un filtro que el usuario marca no puede perderse en el camino."""
    D.descarga_sin_verificar(
        engine=None, api=None, cantidad=1, empresa=[empresa], sentido=["Saliente"],
    )
    kw = grabador[builder]
    assert kw[SENTIDO_POR_BUILDER[builder]] == ["Saliente"], (
        f"'{empresa}' descartó el filtro de sentido antes de llegar a {builder}"
    )
