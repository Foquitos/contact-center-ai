"""
Endpoint `/auditorias_realizadas/` — paginado, filtros por columna y caché del SP.

La pantalla "Auditorías Realizadas" ya no se trae el set completo: pide una página
filtrada y el backend resuelve el recorte sobre el resultado del SP (que además
queda cacheado unos minutos para que refiltrar o pasar de página no vuelva a
pegarle a SQL Server).

Acá se llama a la función del router directamente con un engine falso: no hay DB
ni IA de por medio, y lo que se valida es el contrato que consume el JS
(`columns`, `data`, `total`, `total_sin_filtros`, `facetas`) y que el SP se
ejecute una sola vez.
"""
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.routers.auditoria as router_auditoria
from app.utils.tabla_filtros import CacheConsultas


COLUMNAS_SP = ["AuditoriaID", "IdAplicativo", "Agente", "Equipo", "duracion_segundos",
               "fecha_interaccion", "ExisteTranscripcion", "ExisteResponseThoughts"]

EQUIPOS = ["Ventas", "Cobranzas", "Soporte"]
FILAS_SP = [
    {
        "AuditoriaID": i,
        "IdAplicativo": f"2601211015421{i:02d}",
        "Agente": "Ana Pérez" if i % 2 else "Juan Gómez",
        "Equipo": EQUIPOS[i % 3],
        "duracion_segundos": 30 + i,
        "fecha_interaccion": datetime(2026, 8, 1 + (i % 10), 10, 0, 0),
        "ExisteTranscripcion": 1,
        "ExisteResponseThoughts": 0,
    }
    for i in range(250)
]


class _FakeResult:
    def __init__(self, columnas, filas):
        self._columnas = columnas
        self._filas = filas

    def keys(self):
        return list(self._columnas)

    def mappings(self):
        return self

    def all(self):
        return [dict(f) for f in self._filas]


class _FakeConn:
    """Emula al SP: si le mandan @Offset/@Fetch devuelve solo esa página más la
    columna técnica [__Total] con el total del set (igual que la migración
    2026-08-14b). Si no, devuelve todo, como el SP de siempre."""

    def __init__(self, contador, sp_viejo=False):
        self._contador = contador
        self._sp_viejo = sp_viejo

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=None):
        self._contador["ejecuciones"] += 1
        params = params or {}
        if "fetch" in params:
            if self._sp_viejo:
                raise RuntimeError(
                    "Procedure or function sp_ObtenerAuditoriasFiltradas has too many "
                    "arguments specified.")
            self._contador["paginadas"] += 1
            desde = params.get("offset") or 0
            hasta = desde + params["fetch"]
            filas = [dict(f, __Total=len(FILAS_SP)) for f in FILAS_SP[desde:hasta]]
            return _FakeResult(COLUMNAS_SP + ["__Total"], filas)
        self._contador["completas"] += 1
        return _FakeResult(COLUMNAS_SP, FILAS_SP)


class _FakeEngine:
    def __init__(self, sp_viejo=False):
        self.contador = {"ejecuciones": 0, "paginadas": 0, "completas": 0}
        self._sp_viejo = sp_viejo

    def connect(self):
        return _FakeConn(self.contador, self._sp_viejo)


def _preparar(monkeypatch, sp_viejo=False):
    engine = _FakeEngine(sp_viejo=sp_viejo)
    monkeypatch.setattr(router_auditoria, "engine", engine)
    monkeypatch.setattr(router_auditoria, "empresas_permitidas", lambda conn, user: None)
    monkeypatch.setattr(router_auditoria, "exigir_acceso_empresa",
                        lambda conn, user, **kwargs: None)
    monkeypatch.setattr(router_auditoria, "_cache_auditorias",
                        CacheConsultas(ttl_segundos=300, max_entradas=4))
    # Flag global: un test puede haberlo apagado al simular el SP viejo.
    monkeypatch.setattr(router_auditoria, "_sp_paginado_disponible", True)
    return engine


@pytest.fixture
def engine_falso(monkeypatch):
    """Reemplaza el engine, el RBAC y la caché (fresca por test)."""
    return _preparar(monkeypatch)


def consultar(**kwargs):
    """Llama al endpoint con los defaults de la pantalla, pisando lo que se pase."""
    parametros = dict(
        fecha_desde=date(2026, 8, 1),
        fecha_hasta=date(2026, 8, 14),
        usuario=None,
        empresa=5,
        campana=8,
        plantilla=33,
        id_aplicativo=None,
        base_fecha="auditoria",
        incluir_transcripcion=False,
        response_thoughts=False,
        columnas=None,
        filtros=None,
        page=1,
        page_size=100,
        incluir_facetas=False,
        refrescar=False,
        revision=None,
        golden_set_id=None,
        current_user=SimpleNamespace(usuario=1234),
    )
    parametros.update(kwargs)
    return router_auditoria.auditorias_realizadas(**parametros)


# --------------------------------------------------------------------------- #
# Paginado                                                                     #
# --------------------------------------------------------------------------- #
def test_devuelve_solo_la_pagina_pedida(engine_falso):
    out = consultar(page=1, page_size=100)
    assert len(out["data"]) == 100
    assert out["total"] == 250
    assert out["total_sin_filtros"] == 250
    assert out["data"][0]["AuditoriaID"] == 0


def test_sin_filtros_la_pagina_la_recorta_el_sp(engine_falso):
    """Es la mejora que trae la migración 2026-08-14b: el SP ordena, pagina y recién
    ahí resuelve Agente/Equipo y transcripción (lo caro es por fila)."""
    consultar(page=2, page_size=100)
    assert engine_falso.contador["paginadas"] == 1
    assert engine_falso.contador["completas"] == 0


def test_el_total_tecnico_del_sp_no_sale_hacia_el_front(engine_falso):
    """[__Total] es cómo el SP informa el total al paginar; no es una columna de la
    tabla y no puede colarse ni en `columns` ni en las filas."""
    out = consultar(page=1, page_size=50)
    assert "__Total" not in out["columns"]
    assert all("__Total" not in fila for fila in out["data"])
    assert out["total"] == 250


def test_pagina_fuera_de_rango_vuelve_a_la_primera(engine_falso):
    """Si los datos se achicaron, una página vacía dejaría al front sin el total
    (viaja en las filas): se reconsulta la primera."""
    out = consultar(page=9, page_size=100)
    assert out["page"] == 1
    assert len(out["data"]) == 100
    assert out["total"] == 250


def test_con_filtros_no_se_pagina_en_sql(engine_falso):
    """Los filtros por columna se aplican sobre el pivot y sobre columnas derivadas
    (Equipo/Agente): si el SP paginara primero, la página saldría mal."""
    consultar(filtros='[{"col": "Equipo", "op": "en", "val": ["Ventas"]}]')
    assert engine_falso.contador["paginadas"] == 0
    assert engine_falso.contador["completas"] == 1


def test_pedir_facetas_fuerza_la_consulta_completa(engine_falso):
    """Las facetas son los valores distintos de TODO el set: no se pueden calcular
    con una página."""
    consultar(incluir_facetas=True)
    assert engine_falso.contador["paginadas"] == 0
    assert engine_falso.contador["completas"] == 1


def test_si_el_sp_todavia_no_tiene_paginado_cae_al_camino_completo(monkeypatch):
    """El deploy del código no puede depender de que la migración ya esté aplicada:
    ante el SP viejo se sigue por el camino de siempre."""
    engine = _preparar(monkeypatch, sp_viejo=True)
    out = consultar(page=1, page_size=100)
    assert len(out["data"]) == 100     # paginado en memoria
    assert out["total"] == 250
    assert engine.contador["completas"] == 1
    assert router_auditoria._sp_paginado_disponible is False


def test_ultima_pagina_incompleta(engine_falso):
    out = consultar(page=3, page_size=100)
    assert len(out["data"]) == 50
    assert out["data"][0]["AuditoriaID"] == 200


def test_page_size_cero_trae_todo(engine_falso):
    """Es lo que usa la descarga CSV/Excel: sin paginar, pero con los filtros puestos."""
    out = consultar(page_size=0)
    assert len(out["data"]) == 250


# --------------------------------------------------------------------------- #
# Filtros por columna                                                          #
# --------------------------------------------------------------------------- #
def test_filtro_por_valores_recorta_el_total(engine_falso):
    out = consultar(filtros='[{"col": "Equipo", "op": "en", "val": ["Ventas"]}]')
    assert out["total"] == len([f for f in FILAS_SP if f["Equipo"] == "Ventas"])
    assert out["total_sin_filtros"] == 250
    assert all(f["Equipo"] == "Ventas" for f in out["data"])


def test_filtros_se_combinan(engine_falso):
    out = consultar(filtros='[{"col": "Equipo", "op": "en", "val": ["Ventas"]},'
                            ' {"col": "duracion_segundos", "op": "entre", "val": [100, 150]}]')
    esperadas = [f for f in FILAS_SP
                 if f["Equipo"] == "Ventas" and 100 <= f["duracion_segundos"] <= 150]
    assert out["total"] == len(esperadas)


def test_filtro_por_columna_invalida_es_400(engine_falso):
    with pytest.raises(HTTPException) as e:
        consultar(filtros='[{"col": "NoExiste", "op": "contiene", "val": "x"}]')
    assert e.value.status_code == 400


def test_filtro_sigue_valiendo_aunque_la_columna_no_se_muestre(engine_falso):
    """Los filtros se aplican sobre las filas crudas del SP: si el usuario apagó
    la columna Equipo pero dejó su filtro, el recorte tiene que seguir aplicando."""
    out = consultar(columnas="Agente,duracion_segundos",
                    filtros='[{"col": "Equipo", "op": "en", "val": ["Ventas"]}]')
    assert out["total"] == len([f for f in FILAS_SP if f["Equipo"] == "Ventas"])
    assert "Equipo" not in out["data"][0]


# --------------------------------------------------------------------------- #
# Facetas y selección de columnas                                              #
# --------------------------------------------------------------------------- #
def test_facetas_solo_cuando_se_piden(engine_falso):
    assert consultar()["facetas"] == {}
    out = consultar(incluir_facetas=True)
    assert [f["valor"] for f in out["facetas"]["Equipo"]] == ["Cobranzas", "Soporte", "Ventas"]
    assert out["tipos"]["duracion_segundos"] == "numero"
    assert out["tipos"]["fecha_interaccion"] == "fecha"


def test_las_facetas_no_se_achican_con_los_filtros_puestos(engine_falso):
    """Las opciones del multiselect salen del set SIN filtros de columna: si se
    achicaran, el usuario no podría sumar un segundo valor al filtro."""
    out = consultar(incluir_facetas=True,
                    filtros='[{"col": "Equipo", "op": "en", "val": ["Ventas"]}]')
    assert len(out["facetas"]["Equipo"]) == 3


def test_columnas_tecnicas_no_se_ofrecen_como_filtro(engine_falso):
    out = consultar(incluir_facetas=True)
    assert "ExisteTranscripcion" not in out["facetas"]
    assert "ExisteTranscripcion" not in out["tipos"]


def test_seleccion_de_columnas_preserva_las_de_acciones(engine_falso):
    out = consultar(columnas="Agente,Equipo")
    assert out["columns"][:2] == ["Agente", "Equipo"]
    # AuditoriaID / IdAplicativo / flags viajan igual: son la clave de los botones.
    assert "AuditoriaID" in out["data"][0]
    assert "ExisteTranscripcion" in out["data"][0]


# --------------------------------------------------------------------------- #
# Caché                                                                        #
# --------------------------------------------------------------------------- #
def test_refiltrar_y_paginar_no_vuelven_a_ejecutar_el_sp(engine_falso):
    # "Buscar Auditorías" pide las facetas: trae el set completo y lo cachea.
    consultar(refrescar=True, incluir_facetas=True)
    consultar(page=2)                               # cambio de página
    consultar(filtros='[{"col": "Equipo", "op": "en", "val": ["Ventas"]}]')
    assert engine_falso.contador["ejecuciones"] == 1


def test_buscar_de_nuevo_ignora_la_cache(engine_falso):
    consultar()
    consultar(refrescar=True)
    assert engine_falso.contador["ejecuciones"] == 2


def test_otra_consulta_no_reusa_la_cache(engine_falso):
    consultar()
    consultar(fecha_hasta=date(2026, 8, 15))
    assert engine_falso.contador["ejecuciones"] == 2


def test_las_descargas_pesadas_no_se_cachean(engine_falso):
    """El modo "Completo" trae transcripciones y CoT: son decenas de MB de un solo
    uso, no tienen que quedar ocupando RAM."""
    consultar(incluir_transcripcion=True, page_size=0)
    consultar(incluir_transcripcion=True, page_size=0)
    assert engine_falso.contador["ejecuciones"] == 2


# --------------------------------------------------------------------------- #
# Filtro de revisión (Golden Set / reauditar / recomendadas)                    #
# --------------------------------------------------------------------------- #
# Quien revisa vive en esta pantalla y no en la de Golden Set, así que el trabajo
# de revisión tiene que poder pedirse desde acá. El SP no sabe nada de revisiones:
# el conjunto de AuditoriaID se resuelve aparte y las filas se filtran contra él.
def _filtro_falso(monkeypatch, ids, **extra):
    datos = {
        "auditoria_ids": list(ids),
        "total": len(ids),
        "desde_auditoria": datetime(2026, 3, 2, 9, 0, 0),
        "hasta_auditoria": datetime(2026, 8, 9, 18, 0, 0),
        "desde_interaccion": datetime(2026, 3, 1, 9, 0, 0),
        "hasta_interaccion": datetime(2026, 8, 8, 18, 0, 0),
        "motivos": {},
    }
    datos.update(extra)
    llamadas = {}

    def falso(engine, **kwargs):
        llamadas.update(kwargs)
        return datos

    monkeypatch.setattr(router_auditoria.golden_set, "ids_para_filtro", falso)
    return llamadas


def test_el_filtro_de_golden_set_devuelve_solo_esos_llamados(engine_falso, monkeypatch):
    _filtro_falso(monkeypatch, [3, 7, 11])
    out = consultar(revision="golden_set")
    assert [f["AuditoriaID"] for f in out["data"]] == [3, 7, 11]
    assert out["total"] == 3
    assert out["revision"]["filtro"] == "golden_set"
    assert out["revision"]["total"] == 3


def test_el_rango_de_fechas_del_usuario_se_reemplaza_por_el_del_conjunto(engine_falso, monkeypatch):
    """Un Golden Set está armado a propósito para cubrir períodos distintos: si se
    respetara el rango que el usuario tenía puesto (dos semanas), se comería la
    mitad del conjunto sin avisar."""
    _filtro_falso(monkeypatch, [3, 7])
    consultar(revision="golden_set", fecha_desde=date(2026, 8, 1), fecha_hasta=date(2026, 8, 14))
    # El SP se ejecutó con el rango derivado (marzo→agosto), no con el pedido.
    assert engine_falso.contador["completas"] == 1


def test_sin_llamados_no_se_le_pega_al_sp(engine_falso, monkeypatch):
    """Traer la plantilla entera para después descartarla toda sería el peor de
    los dos mundos: lento y vacío igual."""
    _filtro_falso(monkeypatch, [])
    out = consultar(revision="golden_set")
    assert out["data"] == []
    assert out["total"] == 0
    assert engine_falso.contador["ejecuciones"] == 0


def test_el_filtro_de_revision_no_usa_el_paginado_en_sql(engine_falso, monkeypatch):
    """El camino rápido pagina dentro del SP, que no conoce el conjunto: paginaría
    sobre filas que después se descartan y devolvería páginas incompletas."""
    _filtro_falso(monkeypatch, list(range(0, 250, 2)))
    out = consultar(revision="golden_set", page=1, page_size=10)
    assert engine_falso.contador["paginadas"] == 0
    assert len(out["data"]) == 10
    assert out["total"] == 125


def test_los_motivos_viajan_aparte_y_no_como_columna(engine_falso, monkeypatch):
    """Por qué conviene revisar/reauditar un llamado es información de la revisión,
    no de la auditoría: mezclarlo en las columnas rompería las plantillas de
    columnas guardadas por los usuarios."""
    _filtro_falso(monkeypatch, [5], motivos={5: {"aporte": 2, "motivos": []}})
    out = consultar(revision="recomendadas")
    assert out["revision"]["motivos"] == {"5": {"aporte": 2, "motivos": []}}
    assert "motivo" not in out["columns"]


def test_filtrar_por_revision_exige_plantilla(engine_falso, monkeypatch):
    _filtro_falso(monkeypatch, [1])
    with pytest.raises(HTTPException) as e:
        consultar(revision="golden_set", plantilla=None)
    assert e.value.status_code == 400
    assert "plantilla" in e.value.detail


def test_un_filtro_desconocido_no_devuelve_todo_por_las_dudas(engine_falso, monkeypatch):
    def explota(engine, **kwargs):
        raise ValueError("Filtro de revisión desconocido: 'cualquiera'")
    monkeypatch.setattr(router_auditoria.golden_set, "ids_para_filtro", explota)
    with pytest.raises(HTTPException) as e:
        consultar(revision="cualquiera")
    assert e.value.status_code == 400


def test_las_facetas_se_calculan_sobre_el_conjunto_filtrado(engine_falso, monkeypatch):
    """Si las facetas salieran del set completo, el multiselect ofrecería equipos
    que no están en ninguna de las filas que se ven."""
    _filtro_falso(monkeypatch, [0, 3])   # Equipos: Ventas y Ventas (i % 3)
    out = consultar(revision="golden_set", incluir_facetas=True, page_size=0)
    assert {f["valor"] for f in out["facetas"]["Equipo"]} == {"Ventas"}


def test_dos_filtros_distintos_no_comparten_cache(engine_falso, monkeypatch):
    _filtro_falso(monkeypatch, [1, 2])
    consultar(revision="golden_set")
    _filtro_falso(monkeypatch, [3, 4])
    out = consultar(revision="para_reauditar")
    assert [f["AuditoriaID"] for f in out["data"]] == [3, 4]
    assert engine_falso.contador["completas"] == 2
