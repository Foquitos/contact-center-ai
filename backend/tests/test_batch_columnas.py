"""
Regresión del mapeo de columnas Detalle_ al procesar batches de Gemini.

Cada plantilla tiene sus propios atributos, y las columnas Detalle_<nombre> tienen que
terminar como Detalle_<AtributoID> antes de guardarse. Cuando dos batches de plantillas
distintas terminaban en la misma ventana de polling, se renombraba TODO con el mapa del
primero: las columnas del otro quedaban con su nombre de texto (Detalle_corte_abrupto) y
al guardar reventaba con "invalid literal for int() with base 10: 'corte'", tumbando las
dos corridas.

Test 100% offline: no toca DB, Gemini ni tokens.
"""
import pandas as pd

from Auditor import AuditorIA


# El método no usa `self` (solo el logger del módulo): lo llamamos sin construir
# la clase, que abriría conexión a la base y cliente de Gemini.
renombrar = AuditorIA._renombrar_detalles_a_atributo_id


MAPA_ALARMIX = {"corte_abrupto": 101, "origen_del_corte": 102}
MAPA_DENTAL = {"obtuvo_turno": 201, "saludo_inicial": 202}


def _df_union_de_dos_batches():
    """Reproduce df_total: la unión de columnas de dos batches de plantillas distintas
    (cada fila trae vacías las columnas del otro batch, como hace pandas)."""
    fila_alarmix = {
        "id_aplicativo": "abc_1",
        "prompt_nombre_id_map": str(MAPA_ALARMIX),
        "Detalle_corte_abrupto": "false",
        "Detalle_origen_del_corte": "No hubo corte",
        "Detalle_obtuvo_turno": None,
        "Detalle_saludo_inicial": None,
    }
    fila_dental = {
        "id_aplicativo": "xyz_2",
        "prompt_nombre_id_map": str(MAPA_DENTAL),
        "Detalle_corte_abrupto": None,
        "Detalle_origen_del_corte": None,
        "Detalle_obtuvo_turno": "OK",
        "Detalle_saludo_inicial": "OK",
    }
    return pd.DataFrame([fila_alarmix]), pd.DataFrame([fila_dental])


def test_cada_batch_usa_su_propio_mapa():
    df_alarmix, df_dental = _df_union_de_dos_batches()

    alarmix = renombrar(None, df_alarmix)
    dental = renombrar(None, df_dental)

    # Cada grupo conserva SOLO sus atributos, ya resueltos a AtributoID.
    assert sorted(c for c in alarmix.columns if c.startswith("Detalle_")) == ["Detalle_101", "Detalle_102"]
    assert sorted(c for c in dental.columns if c.startswith("Detalle_")) == ["Detalle_201", "Detalle_202"]

    # Los valores viajan con su columna, no se cruzan entre plantillas.
    assert alarmix.loc[0, "Detalle_101"] == "false"
    assert dental.loc[0, "Detalle_201"] == "OK"


def test_no_queda_ninguna_columna_con_sufijo_no_numerico():
    """Lo que rompía era int(col.split('_')[1]) sobre un sufijo de texto."""
    df_alarmix, df_dental = _df_union_de_dos_batches()

    for df in (renombrar(None, df_alarmix), renombrar(None, df_dental)):
        assert "prompt_nombre_id_map" not in df.columns
        for col in df.columns:
            if col.startswith("Detalle_"):
                assert int(col.split("_")[1])  # no debe levantar ValueError


def test_sin_mapa_no_toca_el_dataframe():
    df = pd.DataFrame([{"id_aplicativo": "abc_1", "Detalle_101": "OK"}])
    assert list(renombrar(None, df).columns) == ["id_aplicativo", "Detalle_101"]
