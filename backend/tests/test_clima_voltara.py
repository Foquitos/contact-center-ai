"""scripts/clima_voltara.py: qué días quedan guardados como observados.

El archivo de Open-Meteo llega hasta ayer; el script le pedía hasta hace 5 días y
los días del medio quedaban como pronóstico, así que el modelo no entrenaba con
ellos y la salud del planificador marcaba el clima atrasado todos los días.
"""
import importlib.util
from datetime import date, timedelta
from pathlib import Path

RUTA = Path(__file__).resolve().parents[2] / "scripts" / "clima_voltara.py"


def _modulo():
    spec = importlib.util.spec_from_file_location("clima_voltara", RUTA)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


HOY = date(2026, 9, 15)


def _fila(dias_atras: int, es_pron: bool, t_max=20.0):
    return {"fecha": HOY - timedelta(days=dias_atras), "t_max": t_max, "es_pron": es_pron}


def test_el_observado_llega_a_ayer_aunque_el_archivo_venga_corto():
    clima = _modulo()
    archivo = [_fila(d, False) for d in (7, 6, 5)]
    pronostico = [_fila(d, True) for d in range(7, -8, -1)]   # 7 días atrás a 7 adelante
    cerrado = clima.cerrar_dias_pasados(archivo, pronostico, HOY)
    assert [f["fecha"] for f in cerrado] == [HOY - timedelta(days=d) for d in range(7, 0, -1)]
    assert all(f["es_pron"] is False for f in cerrado)
    assert max(f["fecha"] for f in cerrado) == HOY - timedelta(days=1)   # hoy no se cierra


def test_el_archivo_gana_y_sus_dias_vacios_no_pisan_nada():
    clima = _modulo()
    archivo = [_fila(2, False, t_max=18.0), _fila(1, False, t_max=None)]
    pronostico = [_fila(2, True, t_max=25.0), _fila(1, True, t_max=21.0)]
    cerrado = {f["fecha"]: f for f in clima.cerrar_dias_pasados(archivo, pronostico, HOY)}
    assert cerrado[HOY - timedelta(days=2)]["t_max"] == 18.0     # el dato del archivo
    assert cerrado[HOY - timedelta(days=1)]["t_max"] == 21.0     # vacío en el archivo: análisis
    assert cerrado[HOY - timedelta(days=1)]["es_pron"] is False


def test_no_modifica_las_filas_del_pronostico():
    clima = _modulo()
    pronostico = [_fila(1, True)]
    clima.cerrar_dias_pasados([], pronostico, HOY)
    assert pronostico[0]["es_pron"] is True
