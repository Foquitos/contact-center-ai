"""Avisos de corte de Hidra -> ajuste propuesto (app/planificador_cortes.py).

Sin red, sin Gemini y sin escribir la base: la lectura de la web se prueba con
HTML de muestra, la IA se inyecta y lo que toca tablas se reemplaza. La consulta
que mide lo que trajo cada corte se valida con `describe` contra el detalle real.

Correr: pytest tests/test_planificador_cortes.py -m "not tokens"
"""
import os
import re
from datetime import date, datetime, timedelta

import pytest

from app import planificador_cortes as pc
from app import planificador_datos as pd
from app import planificador_salud as salud

LISTADO = """
<a href="/usuarios/Novedades/2026/09/inspeccion_rios_subterraneos_oeste_sudoeste">x</a>
<a href="/usuarios/Novedades/2026/09/inspeccion_rios_subterraneos_oeste_sudoeste">repetido</a>
<a href="/usuarios/Novedades/2026/08/cambio_valvula_caballito">y</a>
<a href="/usuarios/Novedades">el listado mismo no</a>
"""

NOTA = """<html><head><title>Hidra - Inspeccion rios subterraneos oeste y sudoeste</title>
<script>var menu = "Atención Comercial";</script></head><body>
<nav>Usuarios Interrupción de servicio Novedades</nav>
<p>16 de septiembre de 2026</p><h1>Hidra continúa con su Programa</h1>
<p>Este jueves por la noche se hará la inspección del tramo &ldquo;Floresta &ndash; Matanza&rdquo;.</p>
<p>Atención Comercial 6333-AGUA</p></body></html>"""


def test_el_listado_trae_cada_nota_una_vez():
    assert pc.enlaces_de_novedades(LISTADO) == [
        "https://www.hidra.example/usuarios/Novedades/2026/09/inspeccion_rios_subterraneos_oeste_sudoeste",
        "https://www.hidra.example/usuarios/Novedades/2026/08/cambio_valvula_caballito",
    ]


def test_la_nota_arranca_en_la_fecha_y_corta_en_el_pie():
    publicado, titulo, texto = pc.leer_nota(NOTA)
    assert publicado == date(2026, 9, 16)
    assert titulo == "Inspeccion rios subterraneos oeste y sudoeste"
    assert texto.startswith("16 de septiembre de 2026")
    assert "Floresta – Matanza" in texto
    assert "6333" not in texto and "Novedades" not in texto


def test_sin_inicio_no_hay_corte_que_planificar():
    lec = pc.normalizar_lectura({"es_corte": True, "inicio": "", "resumen": "algo"})
    assert lec.es_corte is False
    assert pc.normalizar_lectura({}).es_corte is False


def test_la_lectura_se_valida():
    lec = pc.normalizar_lectura({
        "es_corte": True, "programado": True, "inicio": "2026-09-17T22:00",
        "fin": "2026-09-17T21:00",           # antes del inicio: se descarta
        "zonas": ["La Matanza", " ", "Liniers"], "afecta_caba": True,
        "usuarios_afectados": 0, "escala": "enorme", "instalaciones": "EE La Matanza",
        "resumen": "Inspección Floresta-Matanza"})
    assert lec.es_corte and lec.inicio == datetime(2026, 9, 17, 22)
    assert lec.fin is None
    assert lec.zonas == "La Matanza, Liniers"
    assert lec.usuarios_afectados is None     # cero no es un dato
    assert lec.escala == "rutina"             # una escala desconocida no pasa


def test_la_ventana_sigue_unas_horas_despues_del_fin():
    lec = pc.Lectura(es_corte=True, inicio=datetime(2026, 9, 17, 22),
                     fin=datetime(2026, 9, 18, 15))
    assert pc.ventana_del_ajuste(lec) == (datetime(2026, 9, 17, 22), datetime(2026, 9, 18, 19))
    sin_fin = pc.Lectura(es_corte=True, inicio=datetime(2026, 9, 17, 22))
    assert pc.ventana_del_ajuste(sin_fin)[1] == datetime(2026, 9, 17, 22) + pc.DURACION_POR_DEFECTO + pc.COLA_DESPUES_DEL_FIN


def test_la_base_es_la_mediana_de_las_mismas_medias_horas():
    corte = datetime(2026, 9, 17, 22)
    serie = {}
    for k, valor in zip(range(1, 5), (10, 20, 30, 1000)):     # una semana rara no mueve la mediana
        for m in (corte, corte + timedelta(minutes=30)):
            serie[(m - timedelta(weeks=k), 481)] = (valor, 300.0)
    base = pc.base_de_la_ventana(serie, corte, corte + timedelta(hours=1))
    assert base == pytest.approx(2 * 25)


def test_una_semana_sin_la_media_hora_cuenta_cero():
    corte = datetime(2026, 9, 17, 3)
    serie = {(corte - timedelta(weeks=1), 481): (8.0, 0.0)}
    assert pc.base_de_la_ventana(serie, corte, corte + timedelta(minutes=30)) == 0.0


def test_la_semilla_manda_hasta_que_hay_historia_propia():
    llamadas, origen = pc.llamadas_esperadas("grande", {"grande": [100, 200]})
    assert llamadas == pc.LLAMADAS_SEMILLA["grande"] and "semilla" in origen
    llamadas, origen = pc.llamadas_esperadas("rutina", {"rutina": [10, 20, 30, 40, 5000]})
    assert llamadas == 30 and "5" in origen


def test_el_factor_tiene_tope_y_necesita_base():
    assert pc.factor_propuesto(500, 1000) == 1.5
    assert pc.factor_propuesto(50_000, 1000) == 3.0
    assert pc.factor_propuesto(10, 0) is None


def test_el_exceso_se_mide_contra_la_mediana_previa():
    inicio = date(2026, 8, 13)
    por_dia = {inicio - timedelta(days=3 + i): 14 for i in range(pc.DIAS_BASE_MEDICION)}
    por_dia[inicio - timedelta(days=1)] = 500       # el día del aviso no entra en la base
    por_dia[inicio] = 96
    por_dia[inicio + timedelta(days=1)] = 1576
    assert pc.exceso_medido(por_dia, inicio, inicio + timedelta(days=1)) == (96 - 14) + (1576 - 14)


def _procesar(monkeypatch, crudo, ahora, medidas=None, base=1000.0):
    guardado = {}
    monkeypatch.setattr(pc, "guardar_aviso", lambda conn, *a, **k: guardado.update(
        estado=a[8], lectura=a[6], **k))
    monkeypatch.setattr(pc, "llamadas_medidas", lambda conn, cid: medidas or {})
    monkeypatch.setattr(pd, "serie_por_skill", lambda conn, cid, a, b: {})
    monkeypatch.setattr(pc, "base_de_la_ventana", lambda serie, d, h: base)
    estado = pc.procesar_aviso(None, 1, "web", "https://x", date(2026, 9, 16), "t", "texto",
                               ahora=ahora, extraer=lambda texto, publicado: (crudo, "modelo"))
    return estado, guardado


CRUDO = {"es_corte": True, "programado": True, "inicio": "2026-09-17T22:00",
         "fin": "2026-09-18T15:00", "zonas": ["La Matanza"], "afecta_caba": False,
         "usuarios_afectados": None, "escala": "grande", "instalaciones": "EE",
         "resumen": "r"}


def test_un_aviso_futuro_queda_pendiente_con_su_propuesta(monkeypatch):
    estado, g = _procesar(monkeypatch, CRUDO, datetime(2026, 9, 16, 12))
    assert estado == g["estado"] == "pendiente"
    assert g["extra"] == pc.LLAMADAS_SEMILLA["grande"]
    assert g["factor"] == pc.factor_propuesto(pc.LLAMADAS_SEMILLA["grande"], 1000.0)
    assert g["ventana"] == (datetime(2026, 9, 17, 22), datetime(2026, 9, 18, 19))


def test_un_aviso_de_algo_que_ya_paso_no_propone_nada(monkeypatch):
    estado, g = _procesar(monkeypatch, CRUDO, datetime(2026, 9, 20))
    assert estado == "vencido" and "factor" not in g


def test_una_nota_sin_corte(monkeypatch):
    estado, _ = _procesar(monkeypatch, {"es_corte": False, "resumen": "balance"},
                          datetime(2026, 9, 16))
    assert estado == "sin_corte"


def test_si_la_ia_falla_queda_en_error_para_reintentar(monkeypatch):
    guardado = {}
    monkeypatch.setattr(pc, "guardar_aviso", lambda conn, *a, **k: guardado.update(estado=a[8]))

    def falla(texto, publicado):
        raise RuntimeError("503")
    assert pc.procesar_aviso(None, 1, "web", "u", None, None, "t", extraer=falla) == "error"
    assert guardado["estado"] == "error"


def test_la_consulta_de_medicion_resuelve_nombres(validar_sql):
    sql = re.sub(r":desde\b", "'2026-09-10'", pc.LLAMADAS_CORTE_POR_DIA)
    sql = re.sub(r":hasta\b", "'2026-09-12'", sql)
    resultado = validar_sql(sql)
    assert resultado.ok, resultado.error


def test_solo_hidra_tiene_avisos():
    assert pc.CAMPANAS_CON_AVISOS == {pd.CAMPANA_HIDRA_TECNICO}


def test_la_migracion_esta_registrada_en_salud():
    archivos = [m["archivo"] for m in salud.REGISTRO_MIGRACIONES]
    assert "2026-09-24b_planificador_avisos_corte.sql" in archivos


MIGRACION = os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "migrations",
                         "2026-09-24b_planificador_avisos_corte.sql")


@pytest.mark.skipif(not os.path.exists(MIGRACION), reason="scripts/migrations no viaja")
def test_la_migracion_cubre_lo_que_escribe_el_modulo():
    sql = open(MIGRACION, encoding="utf-8").read()
    assert "IF OBJECT_ID('planificacion.AvisoCorte', 'U') IS NULL" in sql
    for estado in ("pendiente", "aplicado", "descartado", "sin_corte", "vencido", "error"):
        assert f"'{estado}'" in sql
    for escala in pc.ESCALAS:
        assert f"'{escala}'" in sql
    assert not re.search(r"\b(DELETE|DROP|TRUNCATE|UPDATE)\b", sql, re.I)
