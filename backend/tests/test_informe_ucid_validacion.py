"""Validación de los informes que acompañan a los audios CSV (AuditorIA/Cardnet.py).

El informe de Verint se puede exportar a nivel LLAMADA o a nivel SEGMENTO, y solo el de
segmento sirve: 'Segment Start Time' es lo que cruza cada audio con su segmento y
'Segment UCID' lo que después cruza contra el ECH. Con el export equivocado el proceso
moría con un KeyError a 300 líneas de distancia, la carpeta quedaba trabada en el
fileserver y nadie sabía por qué (pasó entre el 02/08 y el 18/08 de 2026).

Se valida al leer, con un mensaje que diga qué falta y qué hay que exportar.
"""
import pytest

from AuditorIA.avisos import AvisoUsuarioError
from AuditorIA.Cardnet import leer_informe_ucid, leer_informe_audios


def _csv_verint(tmp_path, encabezados, nombre="informe.csv"):
    """Escribe un CSV con el formato que exporta Verint (UTF-16 LE, separado por comas)."""
    ruta = tmp_path / nombre
    filas = ",".join(f"v{i}" for i in range(len(encabezados)))
    ruta.write_text(",".join(encabezados) + "\n" + filas + "\n", encoding="UTF-16 LE")
    return str(ruta)


COLUMNAS_SEGMENTO = [
    "Type", "Flag", "Full Name", "Segment Start Time", "Segment UCID", "Score",
]
COLUMNAS_LLAMADA = [
    "Type", "Flag", "Full Name", "Complete Start Time", "Segment UCID", "Score",
]


def test_informe_a_nivel_segmento_se_lee(tmp_path):
    df = leer_informe_ucid(_csv_verint(tmp_path, COLUMNAS_SEGMENTO))
    assert list(df.columns) == COLUMNAS_SEGMENTO
    assert len(df) == 1


def test_informe_a_nivel_llamada_avisa_que_falta_el_segmento(tmp_path):
    with pytest.raises(AvisoUsuarioError) as e:
        leer_informe_ucid(_csv_verint(tmp_path, COLUMNAS_LLAMADA))

    mensaje = str(e.value)
    assert "Segment Start Time" in mensaje
    # La pista es lo que hace accionable el aviso para quien exporta el informe.
    assert "SEGMENTO" in mensaje
    assert "informe.csv" in mensaje


def test_avisa_todas_las_columnas_que_faltan(tmp_path):
    with pytest.raises(AvisoUsuarioError) as e:
        leer_informe_ucid(_csv_verint(tmp_path, ["Type", "Flag", "Full Name", "Complete Start Time"]))

    mensaje = str(e.value)
    assert "Segment Start Time" in mensaje and "Segment UCID" in mensaje


def test_encabezados_con_espacios_de_mas_no_cuentan_como_faltantes(tmp_path):
    """Según cómo se genere el export, los encabezados vienen con espacios: una columna
    presente no puede darse por faltante por eso."""
    df = leer_informe_ucid(_csv_verint(tmp_path, [f" {c} " for c in COLUMNAS_SEGMENTO]))
    assert "Segment Start Time" in df.columns


def test_csv_ilegible_avisa_en_castellano(tmp_path):
    """Un CSV reabierto y guardado desde Excel deja de ser UTF-16 y no se puede leer."""
    ruta = tmp_path / "guardado_con_excel.csv"
    ruta.write_text("Type,Flag,Full Name\na,b,c\n", encoding="utf-8")

    with pytest.raises(AvisoUsuarioError) as e:
        leer_informe_ucid(str(ruta))

    assert "guardado_con_excel.csv" in str(e.value)


def test_savedfiles_con_otro_formato_avisa(tmp_path):
    ruta = tmp_path / "savedFiles.txt"
    ruta.write_text("esto no es el informe de audios\n", encoding="utf-8")

    with pytest.raises(AvisoUsuarioError) as e:
        leer_informe_audios(str(ruta))

    assert "savedFiles.txt" in str(e.value)
