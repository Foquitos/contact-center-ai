"""Parseo de lo que publica el ENRE (scripts/cortes_enre.py). Sin red y sin base."""
import importlib.util
import os
from datetime import datetime

import pytest

RUTA = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "cortes_enre.py"))


@pytest.fixture(scope="module")
def enre():
    spec = importlib.util.spec_from_file_location("cortes_enre", RUTA)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


UFS = """UFS = [
['23:50', 3445, 2926, '01d', 'Cielo Claro ', '9'],
['23:55', 3452, 2926, '01d', 'Cielo Claro ', '9'],
['00:00', 4208, 2900, '01d', 'Cielo Claro ', '9'],
['11:50', 1123, 865, '01d', 'Cielo Claro ', '12']
];
Valor_actual_EDS = '1123';
"""


def test_ufs_fecha_hacia_atras_cruzando_la_medianoche(enre):
    filas = enre.parsear_ufs(UFS, datetime(2026, 9, 14, 11, 59))
    voltara = {f["momento"]: f["usuarios"] for f in filas if f["distribuidora"] == "VOLTARA"}
    assert voltara == {
        datetime(2026, 9, 14, 11, 50): 1123,
        datetime(2026, 9, 14, 0, 0): 4208,
        datetime(2026, 9, 13, 23, 55): 3452,
        datetime(2026, 9, 13, 23, 50): 3445,
    }
    # la segunda columna es Norluz
    assert {f["usuarios"] for f in filas if f["distribuidora"] == "NORLUZ"} == {2926, 2900, 865}


def test_ufs_ultima_hora_posterior_a_ahora_es_de_ayer(enre):
    # Si el archivo se generó a las 23:55 y se lee a las 00:02, la última fila es de ayer.
    contenido = "['23:50', 10, 20, 'x'],\n['23:55', 11, 21, 'x']"
    filas = enre.parsear_ufs(contenido, datetime(2026, 9, 15, 0, 2))
    assert max(f["momento"] for f in filas) == datetime(2026, 9, 14, 23, 55)


def test_ufs_vacio_no_rompe(enre):
    assert enre.parsear_ufs("UFS = [];", datetime(2026, 9, 14)) == []


MAPA = ('var contents = "<h1>ENRE - Cortes</h1><h3>RESUMEN</h3><h3>NORLUZ</h3><p>     865 usuarios sin '
        'suministro electrico.</p><h3>VOLTARA</h3><p>              1134 usuarios sin suministro electrico.</p>'
        '<span>Informacion obtenida el dia lunes, 14 de Septiembre a las 12:09 hs </span>";'
        '[-34.67, -58.39, 11, "<b>CORTE DE MEDIA TENSION</b>, VOLTARA S.A., Partido: AVELLANEDA, '
        'Usuarios afectados: 137, Hora estimada de normalizacion: 2026-09-14 13:07"]'
        ',[-34.68, -58.35, 11, "<b>CORTE DE BAJA TENSION</b>, VOLTARA S.A., Partido: LANUS, '
        'Usuarios afectados: 12, Hora estimada de normalizacion: 2026-09-14 13:14"]'
        ',[-34.58, -58.59, 12, "<b>CORTE DE MEDIA TENSION</b>, NORLUZ S.A., Partido: PILAR, '
        'Usuarios afectados: 218, Hora estimada de normalizacion: Sin Datos"]')


def test_mapa_toma_la_hora_del_resumen_y_abre_por_tension(enre):
    filas = {f["distribuidora"]: f for f in enre.parsear_mapa(MAPA, datetime(2026, 9, 14, 12, 15))}
    assert filas["VOLTARA"]["momento"] == datetime(2026, 9, 14, 12, 9)
    assert filas["VOLTARA"]["usuarios"] == 1134          # el total publicado, no la suma
    assert (filas["VOLTARA"]["media"], filas["VOLTARA"]["baja"], filas["VOLTARA"]["cortes"]) == (137, 12, 2)
    assert filas["NORLUZ"]["media"] == 218


def test_mapa_de_diciembre_leido_en_enero_es_del_anio_anterior(enre):
    contenido = MAPA.replace("14 de Septiembre", "31 de Diciembre")
    filas = enre.parsear_mapa(contenido, datetime(2027, 1, 1, 0, 5))
    assert filas[0]["momento"].year == 2026


GITHUB = """latitud,longitud,nn,tipo,empresa,partido,localidad,subestacion,alimentador,afectados,normalizacion estimada,fecha_descarga,hora_descarga
-34.66,-58.36,11,media,Voltara,Avellaneda,Avellaneda,9 De Julio,R:1,392,2026-01-09 14:42,2026-01-09,12:49:28
-34.76,-58.40,11,baja,Voltara,Lomas,Lomas,Malvinas,R:2,8,Sin Datos,2026-01-09,12:49:28
-34.58,-58.59,12,media,Norluz,3 De Febrero,Coronado,S/D,R:3,218,Sin Datos,2026-01-09,12:49:28
-34.66,-58.36,11,media,Voltara,Avellaneda,Avellaneda,9 De Julio,R:1,100,Sin Datos,2026-01-09,02:10:00
"""


def test_github_agrega_por_foto_y_pasa_de_utc_a_hora_argentina(enre):
    filas = {(f["momento"], f["distribuidora"]): f for f in enre.parsear_github(GITHUB)}
    foto = filas[(datetime(2026, 1, 9, 9, 49), "VOLTARA")]
    assert (foto["usuarios"], foto["media"], foto["baja"], foto["cortes"]) == (400, 392, 8, 2)
    assert filas[(datetime(2026, 1, 9, 9, 49), "NORLUZ")]["usuarios"] == 218
    # 02:10 UTC es el día anterior en Argentina
    assert filas[(datetime(2026, 1, 8, 23, 10), "VOLTARA")]["usuarios"] == 100


# ---------------------------------------------------- tabla por tipo (data_EDS.js)

TABLA = """var data = {
	fuente : 'Web Service',
    empresa: 'VOLTARA',
    totalUsuariosSinSuministro: '6.262',
    totalUsuariosConSuministro: '2.794.841',
    ultimaActualizacion: '12:25',
    totalUsuariosAyer: '4.735',
    cortesPreventivos:  [],
    cortesProgramados:  [{partido: 'ALMIRANTE BROWN',localidad: 'MARMOL',subestacion_alimentador: '184-MONTE CHINGOLO / R:184-4-45',usuarios: '3.178',normalizacion: '2026-09-24 16:00'},{partido: 'LANUS',localidad: 'GERLI L',subestacion_alimentador: '186-GERLI',usuarios: '500',normalizacion: '2026-09-24 12:36'}],
    cortesServicioMedia:  [{partido: 'CAPITAL FEDERAL',localidad: 'VILLA RIACHUELO',subestacion_alimentador: '42-AUTODROMO',usuarios: '2045',normalizacion: '2026-09-24 19:05'},{partido: 'EZEIZA',localidad: 'TRISTAN SUAREZ',subestacion_alimentador: '83-ECHEVERRIA',usuarios: '378',normalizacion: '2026-09-24 17:53'}],
    cortesComunicados:  [{fecha: '2026-09-26 08:00', partido: 'LANUS', localidad: 'GERLI', subestacion_alimentador: '186-GERLI / R:1', usuarios: '1.050', normalizacion: '2026-09-26 13:00', calles: 'Av. Pavón [entre 1 y 2]'}],
    cortesServicioBaja:  [ {partido: 'BERAZATEGUI', localidad: 'GUTIERREZ', usuarios: '152'}, {partido: 'CAPITAL', localidad: 'CAPITAL', usuarios: '9'}]
  }
"""


def test_tabla_separa_programados_de_imprevistos(enre):
    fila, _ = enre.parsear_tabla(TABLA, "VOLTARA", datetime(2026, 9, 24, 12, 31))
    assert fila["momento"] == datetime(2026, 9, 24, 12, 25)
    assert fila["sin_suministro"] == 6262               # el punto es de miles
    assert fila["programados"] == 3678
    assert fila["media"] == 2423 and fila["cortes_media"] == 2
    assert fila["baja"] == 161 and fila["preventivos"] == 0
    assert fila["programados"] + fila["media"] + fila["baja"] == fila["sin_suministro"]
    assert fila["ayer"] == 4735
    assert fila["comunicados"] == 1050


def test_tabla_lee_los_comunicados_con_su_hora_de_inicio(enre):
    _, com = enre.parsear_tabla(TABLA, "VOLTARA", datetime(2026, 9, 24, 12, 31))
    assert len(com) == 1
    c = com[0]
    assert c["inicio"] == datetime(2026, 9, 26, 8, 0)
    assert c["normalizacion"] == datetime(2026, 9, 26, 13, 0)
    assert c["usuarios"] == 1050 and c["alimentador"] == "186-GERLI / R:1"
    assert c["calles"] == "Av. Pavón [entre 1 y 2]"      # los corchetes de adentro no cortan la lista
    assert c["visto"] == datetime(2026, 9, 24, 12, 25)


def test_tabla_actualizada_antes_de_medianoche_leida_despues_es_de_ayer(enre):
    contenido = TABLA.replace("'12:25'", "'23:58'")
    fila, _ = enre.parsear_tabla(contenido, "VOLTARA", datetime(2026, 9, 25, 0, 5))
    assert fila["momento"] == datetime(2026, 9, 24, 23, 58)


def test_tabla_sin_hora_no_se_guarda(enre):
    assert enre.parsear_tabla("var data = {};", "VOLTARA", datetime(2026, 9, 24)) == (None, [])
