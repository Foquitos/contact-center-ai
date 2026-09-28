"""
Filtros por columna, facetas, paginado y caché de "Auditorías Realizadas".

`app/utils/tabla_filtros.py` es lo que permite que la pantalla traiga una página
filtrada en lugar del set completo del SP. Todo lo que se prueba acá corre sobre
listas de dicts en memoria, así que el archivo es 100% offline: no toca DB ni IA.

Foco: que un filtro signifique lo mismo mirando la celda cruda (datetime, Decimal,
bool) que mirando el valor que el front recibió como opción del multiselect.
"""
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.utils.tabla_filtros import (
    CacheConsultas,
    FiltroInvalido,
    aplicar_filtros,
    calcular_facetas,
    clave_valor,
    inferir_tipos,
    paginar,
    parsear_filtros,
)


FILAS = [
    {"Agente": "Ana Pérez", "Equipo": "Ventas", "duracion_segundos": 120,
     "PuntajeFinal": Decimal("87.5"), "EsErrorCritico": False,
     "fecha_interaccion": datetime(2026, 8, 1, 10, 30, 0),
     "comentario_interaccion": "Cliente pidió hablar con un supervisor porque no le resolvieron "
                               "la gestión anterior de facturación", "extras": None},
    {"Agente": "Juan Gómez", "Equipo": "Cobranzas", "duracion_segundos": 45,
     "PuntajeFinal": Decimal("40"), "EsErrorCritico": True,
     "fecha_interaccion": datetime(2026, 8, 5, 9, 0, 0),
     "comentario_interaccion": "Reclamó dos veces", "extras": ""},
    {"Agente": "Ana Pérez", "Equipo": "Ventas", "duracion_segundos": 600,
     "PuntajeFinal": Decimal("100"), "EsErrorCritico": False,
     "fecha_interaccion": datetime(2026, 8, 12, 18, 45, 0),
     "comentario_interaccion": None, "extras": "campaña X"},
]

COLUMNAS = list(FILAS[0].keys())


def _filtrar(col, op, val=None):
    filtros = parsear_filtros(
        f'[{{"col": "{col}", "op": "{op}", "val": {val if val is not None else "null"}}}]',
        COLUMNAS,
    )
    return aplicar_filtros(FILAS, filtros)


# --------------------------------------------------------------------------- #
# Parseo                                                                       #
# --------------------------------------------------------------------------- #
def test_columna_inexistente_es_error():
    with pytest.raises(FiltroInvalido):
        parsear_filtros('[{"col": "NoExiste", "op": "contiene", "val": "x"}]', COLUMNAS)


def test_operador_desconocido_es_error():
    with pytest.raises(FiltroInvalido):
        parsear_filtros('[{"col": "Agente", "op": "regex", "val": "x"}]', COLUMNAS)


def test_filtro_sin_valor_se_ignora():
    """Un input de texto vacío en la UI no es un error: simplemente no filtra."""
    assert parsear_filtros('[{"col": "Agente", "op": "contiene", "val": ""}]', COLUMNAS) == []
    assert parsear_filtros('[{"col": "Equipo", "op": "en", "val": []}]', COLUMNAS) == []


def test_rango_a_medio_llenar_se_degrada():
    """Solo "desde" cargado tiene que valer como >=, no exigir el otro extremo."""
    filtros = parsear_filtros(
        '[{"col": "duracion_segundos", "op": "entre", "val": [100, null]}]', COLUMNAS
    )
    assert filtros[0]["op"] == "mayor_igual"
    assert len(aplicar_filtros(FILAS, filtros)) == 2


# --------------------------------------------------------------------------- #
# Texto                                                                        #
# --------------------------------------------------------------------------- #
def test_contiene_ignora_acentos_y_mayusculas():
    """Buscar "reclamo" tiene que encontrar "Reclamó"."""
    assert len(_filtrar("comentario_interaccion", "contiene", '"reclamo"')) == 1


def test_contiene_sobre_celda_nula_no_matchea_ni_explota():
    assert len(_filtrar("comentario_interaccion", "contiene", '"supervisor"')) == 1


def test_no_contiene_incluye_las_celdas_vacias():
    """Si la celda está vacía, no contiene el texto buscado: entra en el resultado."""
    assert len(_filtrar("comentario_interaccion", "no_contiene", '"reclamo"')) == 2


# --------------------------------------------------------------------------- #
# Listas (multiselect estilo Excel)                                            #
# --------------------------------------------------------------------------- #
def test_en_con_varios_valores():
    filas = _filtrar("Equipo", "en", '["Ventas", "Cobranzas"]')
    assert len(filas) == 3


def test_no_en_es_el_complemento_exacto_de_en():
    """El front invierte el filtro cuando el usuario tilda casi todos los valores
    (para no mandar una lista enorme por querystring): tiene que dar lo mismo."""
    con_en = _filtrar("Equipo", "en", '["Ventas", "Soporte"]')
    con_no_en = _filtrar("Equipo", "no_en", '["Cobranzas"]')
    assert [f["Agente"] for f in con_en] == [f["Agente"] for f in con_no_en]


def test_en_matchea_bool_por_su_clave_de_faceta():
    """El multiselect ofrece "1"/"0" para un BIT: filtrar por "1" debe traer los True."""
    filas = _filtrar("EsErrorCritico", "en", '["1"]')
    assert len(filas) == 1 and filas[0]["Agente"] == "Juan Gómez"


def test_en_con_valor_vacio_trae_las_celdas_sin_dato():
    """La opción "(vacío)" del multiselect viaja como "" y tiene que matchear
    tanto el NULL como el string vacío."""
    filas = _filtrar("extras", "en", '[""]')
    assert len(filas) == 2


# --------------------------------------------------------------------------- #
# Números y fechas                                                             #
# --------------------------------------------------------------------------- #
def test_rango_numerico_sobre_decimal():
    filas = _filtrar("PuntajeFinal", "entre", "[80, 100]")
    assert len(filas) == 2


def test_mayor_igual_sobre_entero():
    assert len(_filtrar("duracion_segundos", "mayor_igual", "120")) == 2


def test_rango_de_fechas_incluye_el_dia_completo_del_limite():
    """"hasta 2026-08-05" no puede cortar a las 00:00 y perder la llamada de las 9."""
    filas = _filtrar("fecha_interaccion", "entre", '["2026-08-01", "2026-08-05"]')
    assert len(filas) == 2


def test_vacio_y_no_vacio():
    assert len(_filtrar("comentario_interaccion", "vacio")) == 1
    assert len(_filtrar("comentario_interaccion", "no_vacio")) == 2


def test_varios_filtros_se_combinan_en_and():
    filtros = parsear_filtros(
        '[{"col": "Equipo", "op": "en", "val": ["Ventas"]},'
        ' {"col": "duracion_segundos", "op": "mayor", "val": 300}]',
        COLUMNAS,
    )
    filas = aplicar_filtros(FILAS, filtros)
    assert len(filas) == 1 and filas[0]["duracion_segundos"] == 600


# --------------------------------------------------------------------------- #
# Metadata para la UI                                                          #
# --------------------------------------------------------------------------- #
def test_tipos_inferidos():
    tipos = inferir_tipos(FILAS, COLUMNAS)
    assert tipos["duracion_segundos"] == "numero"
    assert tipos["PuntajeFinal"] == "numero"
    assert tipos["fecha_interaccion"] == "fecha"
    assert tipos["Agente"] == "texto"


def test_un_id_numerico_en_texto_no_se_toma_como_numero():
    """IdAplicativo es un string de dígitos: un rango min/max ahí no le sirve a
    nadie, queremos "contiene"."""
    filas = [{"IdAplicativo": "260121101542144"}, {"IdAplicativo": "260121101542145"}]
    assert inferir_tipos(filas, ["IdAplicativo"])["IdAplicativo"] == "texto"


def test_facetas_solo_para_columnas_de_baja_cardinalidad():
    facetas = calcular_facetas(FILAS, COLUMNAS)
    assert [f["valor"] for f in facetas["Equipo"]] == ["Cobranzas", "Ventas"]
    assert facetas["Equipo"][1]["n"] == 2
    # Los comentarios son textos largos: no se ofrecen como multiselect.
    assert "comentario_interaccion" not in facetas


def test_facetas_cortan_por_cantidad_de_valores():
    filas = [{"col": f"valor-{i}"} for i in range(80)]
    assert "col" not in calcular_facetas(filas, ["col"], max_valores=60)


def test_clave_valor_es_estable_para_los_tipos_de_sql():
    assert clave_valor(Decimal("40")) == "40"
    assert clave_valor(Decimal("87.5")) == "87.5"
    assert clave_valor(True) == "1"
    assert clave_valor(None) == ""
    assert clave_valor(date(2026, 8, 1)) == "2026-08-01"
    assert clave_valor(datetime(2026, 8, 1, 10, 30)) == "2026-08-01 10:30:00"


# --------------------------------------------------------------------------- #
# Paginado y caché                                                             #
# --------------------------------------------------------------------------- #
def test_paginado():
    filas = [{"i": i} for i in range(250)]
    assert [f["i"] for f in paginar(filas, 1, 100)][:2] == [0, 1]
    assert [f["i"] for f in paginar(filas, 3, 100)] == [200 + i for i in range(50)]
    assert len(paginar(filas, 1, 0)) == 250  # 0 = todas (descargas)


def test_cache_devuelve_lo_guardado_y_respeta_la_clave():
    cache = CacheConsultas(ttl_segundos=60, max_entradas=3)
    clave = CacheConsultas.clave(uid=1, plantilla=33)
    assert cache.obtener(clave) is None
    cache.guardar(clave, ["a"], [{"a": 1}])
    assert cache.obtener(clave) == (["a"], [{"a": 1}])
    assert cache.obtener(CacheConsultas.clave(uid=2, plantilla=33)) is None


def test_cache_vence_por_ttl():
    cache = CacheConsultas(ttl_segundos=0, max_entradas=3)
    clave = CacheConsultas.clave(uid=1)
    cache.guardar(clave, ["a"], [{"a": 1}])
    assert cache.obtener(clave) is None


def test_cache_expulsa_las_entradas_mas_viejas():
    cache = CacheConsultas(ttl_segundos=60, max_entradas=2)
    claves = [CacheConsultas.clave(uid=i) for i in range(3)]
    for c in claves:
        cache.guardar(c, ["a"], [{"a": 1}])
    assert cache.obtener(claves[0]) is None
    assert cache.obtener(claves[2]) is not None


def test_cache_no_guarda_sets_gigantes():
    cache = CacheConsultas(ttl_segundos=60, max_entradas=3, max_filas_total=10)
    clave = CacheConsultas.clave(uid=1)
    cache.guardar(clave, ["a"], [{"a": i} for i in range(50)])
    assert cache.obtener(clave) is None
