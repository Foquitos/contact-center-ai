"""
Regresión del reporte ECH (Yoizen.Reporte_EHC), la fuente de datos de las auditorías
de audios CSV.

Dos fallas reales que dejaron la auditoría del 31-07-2026 sin encolar ni una carpeta:

1. El informe de Verint trae segmentos SIN 'Segment UCID'. Los vacíos vuelven float
   toda la columna y se mandaban tokens 'nan' / '...592.0' en la lista de UCIDs. El
   ECH no ignora lo que no entiende: con UN solo 'nan' devuelve CERO llamadas para
   TODA la consulta, y el pipeline reventaba después con un KeyError ilegible.
2. El reporte pagina de a 200: pedir 239 UCIDs devolvía 200 y las otras 39 se perdían
   sin ningún aviso.

Test 100% offline: la sesión HTTP es un stub, no se toca la red ni Verint.
"""
import pandas as pd
import pytest

from AuditorIA.downloads.Yoizen import Yoizen


normalizar = Yoizen._normalizar_ucids


class SesionStub:
    """Reemplaza requests.Session: devuelve páginas de 'Calls' y registra los POST."""

    def __init__(self, ucids_disponibles, filas_por_pagina=Yoizen.FILAS_POR_PAGINA_ECH):
        self.ucids_disponibles = ucids_disponibles
        self.filas_por_pagina = filas_por_pagina
        self.pedidos = []

    def post(self, url, json=None):
        # Copia: Reporte_EHC reusa el mismo dict cambiándole 'currentPage' en cada página.
        self.pedidos.append(dict(json))
        pedidos = [u for u in json["ucid"].split(",") if u]
        # El ECH solo entiende UCIDs numéricos: si viene un token cualquiera (p. ej.
        # 'nan'), no devuelve nada para toda la consulta.
        if any(not u.isdigit() for u in pedidos):
            return RespuestaStub({"d": {"Calls": None}})

        encontrados = [u for u in pedidos if u in self.ucids_disponibles]
        desde = json["currentPage"] * self.filas_por_pagina
        pagina = encontrados[desde:desde + self.filas_por_pagina]
        # La última página devuelve 'Calls': null, no una lista vacía.
        calls = [{"UCID": int(u), "Duration": 10} for u in pagina] or None
        return RespuestaStub({"d": {"Calls": calls}})


class RespuestaStub:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def _api(ucids_disponibles, filas_por_pagina=Yoizen.FILAS_POR_PAGINA_ECH):
    """Yoizen sin __init__: construirlo haría el login HTTP contra Verint."""
    api = object.__new__(Yoizen)
    api.session = SesionStub(ucids_disponibles, filas_por_pagina)
    return api


# ---------------- normalización de la lista de UCIDs ----------------

def test_descarta_nan_y_saca_el_punto_cero():
    # Tal cual sale de pandas cuando la columna tiene vacíos: floats y un NaN.
    crudos = [1026971785196592.0, float("nan"), 1017061785196339.0, None, "  1001811785195945 "]
    assert normalizar(crudos) == [
        "1026971785196592",
        "1017061785196339",
        "1001811785195945",
    ]


def test_descarta_tokens_no_numericos():
    assert normalizar(["123", "nan", "", "  ", "N/A", "45.6"]) == ["123"]


def test_no_repite_ucids():
    assert normalizar([123, "123", 123.0]) == ["123"]


def test_un_nan_no_puede_vaciar_la_consulta():
    """La falla original: la lista traía un NaN y el reporte devolvía 0 llamadas."""
    disponibles = {str(1000000000000000 + i) for i in range(3)}
    api = _api(disponibles)

    df = api.Reporte_EHC([1000000000000000.0, float("nan"), 1000000000000001.0, 1000000000000002.0])

    assert df is not None, "un segmento sin UCID no debe tumbar la consulta entera"
    assert len(df) == 3


def test_sin_ucids_validos_devuelve_none_sin_llamar_al_reporte():
    api = _api(set())

    assert api.Reporte_EHC([float("nan"), None, "nan"]) is None
    assert api.session.pedidos == [], "no tiene sentido consultar sin un solo UCID válido"


# ---------------- paginado ----------------

def test_trae_todas_las_paginas():
    # 239 UCIDs = 2 páginas de 200. Antes se quedaba con las primeras 200.
    disponibles = {str(1000000000000000 + i) for i in range(239)}
    api = _api(disponibles)

    df = api.Reporte_EHC(sorted(disponibles))

    assert len(df) == 239
    assert df["UCID"].nunique() == 239
    assert [p["currentPage"] for p in api.session.pedidos] == [0, 1]


def test_una_sola_pagina_no_pide_de_mas():
    disponibles = {str(1000000000000000 + i) for i in range(10)}
    api = _api(disponibles)

    df = api.Reporte_EHC(sorted(disponibles))

    assert len(df) == 10
    assert len(api.session.pedidos) == 1, "una página incompleta ya es la última"


def test_pagina_exacta_confirma_que_no_hay_mas():
    # Con un múltiplo exacto del tamaño de página hay que preguntar una vez más.
    disponibles = {str(1000000000000000 + i) for i in range(4)}
    api = _api(disponibles, filas_por_pagina=2)
    api.session.filas_por_pagina = Yoizen.FILAS_POR_PAGINA_ECH  # el server usa el real

    df = api.Reporte_EHC(sorted(disponibles))

    assert len(df) == 4


def test_sin_resultados_devuelve_none():
    api = _api(set())

    assert api.Reporte_EHC(["1000000000000000"]) is None


def test_error_http_en_la_primera_pagina_devuelve_none():
    api = _api({"1000000000000000"})
    api.session.post = lambda url, json=None: RespuestaStub({}, status_code=500)

    assert api.Reporte_EHC(["1000000000000000"]) is None
