"""
Enriquecimiento del contexto del Asistente Analítico del Dashboard.

Todo lo que decide qué datos extra ve el modelo se resuelve SIN llamar a la IA:
cruzando la pregunta contra los nombres, equipos e IdAplicativo que ya están en
pantalla. Este archivo fija esa conducta, que es la que puede degradarse en
silencio (una pregunta por un asesor que no trae ninguna llamada suya, o una
transcripción que se pide de más y llena el prompt).

Test 100% offline: el engine está mockeado, no toca DB ni IA.
"""
import pytest

from app import asistente_contexto as ac


CONTEXTO = {
    "resumen_operadores": [
        {"operador": "María Gómez", "equipo": "Equipo A", "puntaje_promedio": 62.0},
        {"operador": "Juan Gómez", "equipo": "Equipo A", "puntaje_promedio": 71.0},
        {"operador": "Estefanía Etchevarría", "equipo": "Equipo B", "puntaje_promedio": 95.0},
    ],
    "resumen_equipos": [{"equipo": "Equipo A"}, {"equipo": "Equipo B"}],
    "atributos": [{"nombre": "Valida identidad"}, {"nombre": "Saludo inicial"}],
    "casos_ec_detalle": [
        {"IdAplicativo": "1088_20260815_9931", "Operador": "María Gómez", "Equipo": "Equipo A"},
        {"IdAplicativo": "1088_20260816_4412", "Operador": "Juan Gómez", "Equipo": "Equipo A"},
    ],
    "filas": [
        {"IdAplicativo": "1088_20260815_9931", "Operador": "María Gómez", "Equipo": "Equipo A", "Puntaje": 40, "EsEC": "Sí"},
        {"IdAplicativo": "1088_20260817_1000", "Operador": "María Gómez", "Equipo": "Equipo A", "Puntaje": 55, "EsEC": "No"},
        {"IdAplicativo": "1088_20260818_2000", "Operador": "Estefanía Etchevarría", "Equipo": "Equipo B", "Puntaje": 98, "EsEC": "No"},
    ],
}


# --------------------------------------------------------------------------- #
# A qué se refiere la pregunta                                                 #
# --------------------------------------------------------------------------- #
def test_detecta_al_asesor_por_nombre_completo():
    foco = ac.analizar_pregunta("¿Qué pasó con María Gómez este mes?", CONTEXTO)
    assert "María Gómez" in foco.operadores


def test_detecta_al_asesor_sin_acentos_ni_mayusculas():
    """Nadie escribe los acentos al preguntar."""
    foco = ac.analizar_pregunta("que paso con maria gomez", CONTEXTO)
    assert "María Gómez" in foco.operadores


def test_un_apellido_compartido_no_elige_uno_al_azar():
    """Hay dos Gómez: con solo el apellido no se puede saber de cuál habla, y
    adivinar sería peor que no enfocar (el informe saldría del asesor equivocado)."""
    foco = ac.analizar_pregunta("¿cómo viene Gómez?", CONTEXTO)
    assert foco.operadores == []


def test_un_apellido_unico_y_largo_alcanza():
    foco = ac.analizar_pregunta("contame de Etchevarría", CONTEXTO)
    assert foco.operadores == ["Estefanía Etchevarría"]


def test_no_confunde_una_palabra_comun_con_un_nombre():
    foco = ac.analizar_pregunta("¿cuál es el puntaje promedio del período?", CONTEXTO)
    assert foco.operadores == []
    assert foco.equipos == []


def test_detecta_el_equipo():
    foco = ac.analizar_pregunta("Comparame el Equipo A contra el resto", CONTEXTO)
    assert foco.equipos == ["Equipo A"]


def test_detecta_el_id_de_llamado_que_existe():
    foco = ac.analizar_pregunta("Analizá la llamada 1088_20260815_9931", CONTEXTO)
    assert foco.ids == ["1088_20260815_9931"]
    assert foco.quiere_verbatim is True  # citar un llamado implica querer el detalle


def test_un_anio_no_es_un_id_de_llamado():
    """Se buscan los ids REALES del dataset dentro del texto, en vez de una regex
    de 'esto parece un id': si no, '2026' o un porcentaje entrarían como llamado."""
    foco = ac.analizar_pregunta("¿Cómo venimos en 2026 con el 95% de cumplimiento?", CONTEXTO)
    assert foco.ids == []


def test_detecta_el_atributo_nombrado():
    foco = ac.analizar_pregunta("¿Por qué cae Valida identidad?", CONTEXTO)
    assert foco.atributos == ["Valida identidad"]


@pytest.mark.parametrize("pregunta", [
    "¿Qué dijo el asesor exactamente?",
    "Mostrame la transcripción de los peores casos",
    "Necesito la causa raíz de los errores críticos",
    "Fundamentá con evidencia lo que decís",
])
def test_detecta_que_le_piden_fundamentar(pregunta):
    assert ac.analizar_pregunta(pregunta, CONTEXTO).quiere_verbatim is True


@pytest.mark.parametrize("pregunta", [
    "¿Mejoramos contra el mes pasado?",
    "Mostrame la tendencia del período",
    "Armá un informe ejecutivo para el comité",
    "¿Cómo veníamos antes?",
])
def test_detecta_que_le_piden_comparar(pregunta):
    assert ac.analizar_pregunta(pregunta, CONTEXTO).quiere_comparativa is True


def test_una_pregunta_simple_no_dispara_nada_caro():
    """Sin señales, no se traen transcripciones ni se golpea el SP del período
    anterior: no toda pregunta justifica dos consultas extra."""
    foco = ac.analizar_pregunta("¿Cuántas auditorías hay?", CONTEXTO)
    assert (foco.quiere_verbatim, foco.quiere_comparativa) == (False, False)
    assert ac.ids_para_transcribir(CONTEXTO, foco) == []


# --------------------------------------------------------------------------- #
# Qué filas entran al reporte                                                  #
# --------------------------------------------------------------------------- #
def test_las_filas_del_asesor_preguntado_van_primero():
    """El problema que resuelve: preguntabas por un asesor y la muestra podía no
    tener ni una llamada suya."""
    filas = [{"IdAplicativo": f"otro_{i}", "Operador": "Tercero", "Puntaje": 90} for i in range(300)]
    filas += CONTEXTO["filas"]
    foco = ac.analizar_pregunta("¿Qué pasó con María Gómez?", CONTEXTO)
    elegidas, criterios = ac.seleccionar_filas(filas, foco, tope=10)
    assert [f["Operador"] for f in elegidas[:2]] == ["María Gómez", "María Gómez"]
    # Peor puntaje primero: es lo que se va a mirar.
    assert elegidas[0]["Puntaje"] == 40
    assert any("María Gómez" in c for c in criterios)


def test_el_llamado_citado_va_antes_que_todo():
    foco = ac.analizar_pregunta("Analizá 1088_20260818_2000", CONTEXTO)
    elegidas, criterios = ac.seleccionar_filas(CONTEXTO["filas"], foco, tope=3)
    assert elegidas[0]["IdAplicativo"] == "1088_20260818_2000"
    assert any("citados" in c for c in criterios)


def test_sin_foco_los_criticos_se_priorizan():
    foco = ac.analizar_pregunta("Dame un resumen", CONTEXTO)
    elegidas, criterios = ac.seleccionar_filas(CONTEXTO["filas"], foco, tope=1)
    assert elegidas[0]["EsEC"] == "Sí"
    assert any("error crítico" in c for c in criterios)


def test_el_tope_se_respeta():
    filas = [{"IdAplicativo": str(i), "Operador": "X"} for i in range(500)]
    elegidas, _ = ac.seleccionar_filas(filas, ac.Foco(), tope=200)
    assert len(elegidas) == 200


def test_sin_filas_no_rompe():
    assert ac.seleccionar_filas([], ac.Foco()) == ([], [])


# --------------------------------------------------------------------------- #
# Qué se transcribe                                                            #
# --------------------------------------------------------------------------- #
def test_transcribe_lo_que_la_pregunta_cita():
    foco = ac.analizar_pregunta("Analizá la llamada 1088_20260816_4412", CONTEXTO)
    assert ac.ids_para_transcribir(CONTEXTO, foco) == ["1088_20260816_4412"]


def test_para_causa_raiz_transcribe_los_criticos_del_asesor():
    foco = ac.analizar_pregunta("¿Cuál es la causa raíz de lo de María Gómez?", CONTEXTO)
    assert ac.ids_para_transcribir(CONTEXTO, foco) == ["1088_20260815_9931"]


def test_nunca_pide_mas_transcripciones_que_el_tope():
    contexto = dict(CONTEXTO, casos_ec_detalle=[
        {"IdAplicativo": f"id_{i}", "Operador": "María Gómez"} for i in range(20)
    ])
    foco = ac.analizar_pregunta("causa raíz de los errores críticos", contexto)
    assert len(ac.ids_para_transcribir(contexto, foco)) == ac.MAX_TRANSCRIPCIONES


def test_sin_criticos_cae_a_los_peores_puntajes():
    contexto = dict(CONTEXTO, casos_ec_detalle=[])
    foco = ac.analizar_pregunta("¿qué dijo Etchevarría?", contexto)
    assert ac.ids_para_transcribir(contexto, foco) == ["1088_20260818_2000"]


# --------------------------------------------------------------------------- #
# Formato de la transcripción                                                  #
# --------------------------------------------------------------------------- #
def test_transcripcion_se_arma_como_dialogo():
    crudo = '[{"speakerLabel": "Asesor", "text": "Buenas tardes"}, {"speakerLabel": "Cliente", "text": ["Hola", "sí"]}]'
    assert ac._formatear_transcripcion(crudo) == "[Asesor]: Buenas tardes\n[Cliente]: Hola sí"


@pytest.mark.parametrize("crudo", [None, "", "null", "[]"])
def test_transcripcion_vacia_no_rompe(crudo):
    assert ac._formatear_transcripcion(crudo) == ""


def test_transcripcion_ilegible_no_rompe():
    """Si el JSON viniera roto, se manda el crudo recortado antes que perder el dato."""
    assert ac._formatear_transcripcion("{no es json") == "{no es json"


# --------------------------------------------------------------------------- #
# Período anterior                                                             #
# --------------------------------------------------------------------------- #
def test_el_periodo_anterior_tiene_la_misma_duracion():
    """Comparar 31 días contra 90 daría un 'bajamos un 60%' que solo mide que la
    ventana es más corta."""
    assert ac.periodo_anterior("2026-08-01", "2026-08-31") == ("2026-07-01", "2026-07-31")
    assert ac.periodo_anterior("2026-08-15", "2026-08-21") == ("2026-08-08", "2026-08-14")


def test_un_solo_dia_compara_contra_el_dia_previo():
    assert ac.periodo_anterior("2026-08-10", "2026-08-10") == ("2026-08-09", "2026-08-09")


@pytest.mark.parametrize("desde,hasta", [("", ""), ("2026-08-31", "2026-08-01"), ("ayer", "hoy")])
def test_periodo_invalido_devuelve_none(desde, hasta):
    assert ac.periodo_anterior(desde, hasta) is None


def test_kpis_del_periodo_anterior():
    filas = [
        {"PuntajeFinal": 80.0, "EsErrorCritico": False, "Equipo": "A", "Valida identidad": True},
        {"PuntajeFinal": 60.0, "EsErrorCritico": True, "Equipo": "A", "Valida identidad": False},
        {"PuntajeFinal": None, "EsErrorCritico": False, "Equipo": "B", "Valida identidad": None},
    ]
    atributos = [{"nombre": "Valida identidad", "tipo": "boolean"}]
    kpis = ac._kpis_de_filas(filas, atributos)
    assert kpis["total"] == 3
    assert kpis["puntaje_promedio"] == 70.0  # los None no cuentan
    assert (kpis["casos_ec"], kpis["pct_ec"]) == (1, 33.3)
    assert kpis["cumplimiento_atributos"] == {"Valida identidad": 50.0}  # el None no cuenta
    assert kpis["equipos"]["A"]["casos"] == 2


def test_la_comparativa_se_cachea_entre_repreguntas(monkeypatch):
    """El SP del período anterior es la parte cara: la primera pregunta de la
    charla lo paga y las repreguntas no."""
    llamadas = []

    class _Conn:
        def execute(self, sql, params=None):
            llamadas.append(params)
            class _R:
                def mappings(self_inner): return self_inner
                def all(self_inner): return []
            return _R()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _Engine:
        def connect(self): return _Conn()

    monkeypatch.setattr(ac, "engine", _Engine())
    monkeypatch.setattr(ac, "_cache_comparativa", {})

    for _ in range(3):
        ac.comparativa_periodo_anterior(44, "2026-08-01", "2026-08-31")
    assert len(llamadas) == 1


def test_periodo_anterior_vacio_es_un_dato(monkeypatch):
    """Que no haya auditorías antes NO puede quedar como None silencioso: si el
    modelo no ve nada, inventa una tendencia."""
    class _Conn:
        def execute(self, *a, **k):
            class _R:
                def mappings(self_inner): return self_inner
                def all(self_inner): return []
            return _R()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(ac, "engine", type("E", (), {"connect": lambda self: _Conn()})())
    monkeypatch.setattr(ac, "_cache_comparativa", {})
    res = ac.comparativa_periodo_anterior(44, "2026-08-01", "2026-08-31")
    assert res == {"desde": "2026-07-01", "hasta": "2026-07-31", "total": 0}


def test_sin_plantilla_no_consulta(monkeypatch):
    monkeypatch.setattr(ac, "engine", None)  # explotaría si intentara conectarse
    assert ac.comparativa_periodo_anterior(None, "2026-08-01", "2026-08-31") is None
    assert ac.transcripciones(["x"], None) == []


# --------------------------------------------------------------------------- #
# El pegamento del router: permisos antes de leer nada                         #
# --------------------------------------------------------------------------- #
class _ConnFalsa:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _EngineFalso:
    def connect(self): return _ConnFalsa()


@pytest.fixture
def router(monkeypatch):
    from app.routers import bandeja

    monkeypatch.setattr(bandeja, "engine", _EngineFalso())
    monkeypatch.setattr(bandeja, "_atributos_de_plantilla", lambda p: [])
    return bandeja


class _Usuario:
    usuario = 42


ALCANCE = {
    "plantilla_id": "44", "empresa_id": "7", "campana_id": "9",
    "fecha_desde": "2026-08-01", "fecha_hasta": "2026-08-31", "base_fecha": "interaccion",
}


def test_sin_acceso_a_la_plantilla_no_se_lee_ninguna_transcripcion(router, monkeypatch):
    """La plantilla llega desde el navegador: si no se validara el alcance, bastaría
    con mandar otro id para hacer que el asistente lea llamadas de otra empresa."""
    def _denegar(*a, **k):
        raise PermissionError("fuera de alcance")

    leidas = []
    monkeypatch.setattr(router, "exigir_acceso_empresa", _denegar)
    monkeypatch.setattr(router.asistente_contexto, "transcripciones", lambda *a, **k: leidas.append(a))

    extra = router._enriquecer_contexto("analizá 1088_20260815_9931", CONTEXTO, ALCANCE, _Usuario())
    assert leidas == []
    assert "transcripciones" not in extra
    # Pero la respuesta sigue: con lo que ya mandó el dashboard.
    assert extra["filas_seleccionadas"]


def test_con_acceso_se_piden_las_transcripciones_de_esa_plantilla(router, monkeypatch):
    pedidos = {}
    monkeypatch.setattr(router, "exigir_acceso_empresa", lambda *a, **k: None)
    monkeypatch.setattr(
        router.asistente_contexto, "transcripciones",
        lambda ids, plantilla, empresa, campana: pedidos.update(
            ids=list(ids), plantilla=plantilla, empresa=empresa, campana=campana) or [{"id_aplicativo": ids[0]}],
    )

    extra = router._enriquecer_contexto("analizá 1088_20260815_9931", CONTEXTO, ALCANCE, _Usuario())
    assert pedidos["ids"] == ["1088_20260815_9931"]
    assert (pedidos["plantilla"], pedidos["empresa"], pedidos["campana"]) == (44, 7, 9)
    assert len(extra["transcripciones"]) == 1


def test_la_comparativa_solo_se_pide_cuando_la_pregunta_la_necesita(router, monkeypatch):
    llamadas = []
    monkeypatch.setattr(router, "exigir_acceso_empresa", lambda *a, **k: None)
    monkeypatch.setattr(router.asistente_contexto, "comparativa_periodo_anterior",
                        lambda **kw: llamadas.append(kw) or {"total": 1})

    router._enriquecer_contexto("¿cuántas auditorías hay?", CONTEXTO, ALCANCE, _Usuario())
    assert llamadas == []

    router._enriquecer_contexto("¿mejoramos contra el mes pasado?", CONTEXTO, ALCANCE, _Usuario())
    assert llamadas[0]["desde"] == "2026-08-01"
    assert llamadas[0]["plantilla_id"] == 44


def test_sin_alcance_no_se_consulta_la_base(router, monkeypatch):
    """Un cliente viejo que no manda `alcance` tiene que seguir funcionando."""
    monkeypatch.setattr(router, "exigir_acceso_empresa", lambda *a, **k: pytest.fail("no debería validar"))
    extra = router._enriquecer_contexto("analizá 1088_20260815_9931", CONTEXTO, None, _Usuario())
    assert set(extra) == {"filas_seleccionadas", "criterios_muestra"}


def test_un_id_que_es_prefijo_de_otro_no_se_arrastra():
    """Los AuditoriaID son enteros: que 1234 sea prefijo de 12345 es lo normal.
    Con `in` a secas, preguntar por el 12345 traía la transcripción de los dos."""
    contexto = {
        "filas": [
            {"IdAplicativo": "1234", "Operador": "A"},
            {"IdAplicativo": "12345", "Operador": "B"},
            {"IdAplicativo": "912345", "Operador": "C"},
        ],
    }
    foco = ac.analizar_pregunta("Analizá la llamada 12345", contexto)
    assert foco.ids == ["12345"]


def test_el_id_se_reconoce_entre_signos_de_puntuacion():
    contexto = {"filas": [{"IdAplicativo": "1088_20260815_9931"}]}
    for pregunta in ("¿qué pasó en 1088_20260815_9931?", "revisá (1088_20260815_9931)"):
        assert ac.analizar_pregunta(pregunta, contexto).ids == ["1088_20260815_9931"]
