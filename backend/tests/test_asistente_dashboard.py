import asyncio
import types
import pytest
from app import asistente_dashboard


def test_validar_pregunta_vacia():
    with pytest.raises(ValueError, match="Escribí una pregunta"):
        asistente_dashboard.validar("", {"kpis": {"total": 10}})


def test_validar_contexto_vacio():
    with pytest.raises(ValueError, match="No hay datos de auditorías"):
        asistente_dashboard.validar("¿Cómo fue el puntaje?", {})


def test_validar_pregunta_muy_larga():
    with pytest.raises(ValueError, match="demasiado larga"):
        asistente_dashboard.validar("a" * 3005, {"kpis": {"total": 10}})


def test_sintetizar_datos_dashboard():
    contexto = {
        "empresa_nombre": "Empresa Test",
        "campana_nombre": "Campaña Atención",
        "plantilla_nombre": "Plantilla Calidad",
        "fecha_desde": "2026-08-01",
        "fecha_hasta": "2026-08-28",
        "base_fecha": "interaccion",
        "kpis": {
            "total": 50,
            "puntaje_promedio": 88.5,
            "cant_equipos": 3,
            "cant_operadores": 12,
            "casos_ec": 2,
            "pct_ec": 4,
        },
        "atributos": [
            {"nombre": "Saluda", "tipo": "boolean", "polaridad": "mayor", "meta": 95},
            {"nombre": "Tipifica", "tipo": "enum", "polaridad": "mayor", "meta": 90},
        ],
        "resumen_operadores": [
            {"operador": "Juan Perez", "equipo": "Equipo A", "casos": 10, "puntaje_promedio": 92.0, "casos_ec": 0, "pct_ec": 0},
            {"operador": "Maria Gomez", "equipo": "Equipo B", "casos": 8, "puntaje_promedio": 75.0, "casos_ec": 1, "pct_ec": 13},
        ],
        "resumen_equipos": [
            {"equipo": "Equipo A", "cant_operadores": 5, "casos": 30, "puntaje_promedio": 90.0, "casos_ec": 1, "pct_ec": 3},
        ],
        "distribucion_atributos": {
            "Saluda": {"tipo": "boolean", "valores": {"Sí": {"count": 45, "pct": 90}, "No": {"count": 5, "pct": 10}}},
        },
        "casos_ec_detalle": [
            {"IdGrabacion": "12345", "Operador": "Maria Gomez", "Equipo": "Equipo B", "FechaInteraccion": "2026-08-15", "motivos_ec": ["No valida identidad"]},
        ],
        "filas": [
            {"IdGrabacion": "12345", "FechaInteraccion": "2026-08-15", "Operador": "Maria Gomez", "Equipo": "Equipo B", "PuntajeFinal": 75.0, "TieneErrorCritico": True, "Saluda": True},
        ],
    }

    resumen = asistente_dashboard._sintetizar_datos_dashboard(contexto)
    assert "Empresa Test" in resumen
    assert "Campaña Atención" in resumen
    assert "Juan Perez" in resumen
    assert "Maria Gomez" in resumen
    assert "Equipo A" in resumen
    assert "Saluda" in resumen
    assert "12345" in resumen
    assert "88.5%" in resumen


def test_contenidos_con_historial():
    historial = [
        {"rol": "user", "texto": "Hola"},
        {"rol": "bot", "texto": "Hola, ¿en qué te puedo ayudar?"},
    ]
    pregunta = "¿Cuál fue el promedio?"
    contenidos = asistente_dashboard._contenidos(pregunta, historial)
    assert len(contenidos) == 3
    assert contenidos[0].role == "user"
    assert contenidos[1].role == "model"
    assert contenidos[2].role == "user"


class _ChunkFalso:
    def __init__(self, text=None, usage=None):
        self.text = text
        self.usage_metadata = usage


def _cliente_falso(chunks, explota=False):
    async def _stream(**kwargs):
        async def _gen():
            for chunk in chunks:
                yield chunk
            if explota:
                raise RuntimeError("se cayó el servicio")
        return _gen()

    return types.SimpleNamespace(
        aio=types.SimpleNamespace(
            models=types.SimpleNamespace(generate_content_stream=_stream)
        )
    )


async def _juntar(generador):
    return "".join([texto async for texto in generador])


def test_responder_stream_devuelve_chunks_y_registra_uso(monkeypatch):
    usage_mock = object()
    monkeypatch.setattr(
        asistente_dashboard,
        "_get_client",
        lambda: _cliente_falso([
            _ChunkFalso("El puntaje promedio "),
            _ChunkFalso("fue de 88.5%.", usage_mock),
        ]),
    )

    registrados = []
    monkeypatch.setattr(
        asistente_dashboard,
        "_registrar_consumo",
        lambda u, uid, cid, conv=None, extras=None: registrados.append((u, uid, cid, conv, extras)),
    )

    contexto = {
        "kpis": {"total": 10, "puntaje_promedio": 88.5},
        "filas": [{"IdGrabacion": "1", "PuntajeFinal": 88.5}],
    }

    salida = asyncio.run(
        _juntar(
            asistente_dashboard.responder_stream(
                "¿Cuál fue el promedio?", contexto, user_id=42, campana_id=5,
                conversacion_id=7,
            )
        )
    )

    assert salida == "El puntaje promedio fue de 88.5%."
    assert len(registrados) == 1
    usage, uid, cid, conv, extras = registrados[0]
    assert (usage, uid, cid, conv) == (usage_mock, 42, 5, 7)
    # El consumo queda atribuido a la conversación y con el peso real del contexto
    # de ESE turno: sin esto no se puede saber si lo caro son las charlas o el
    # contexto que se remanda en cada repregunta.
    assert extras["contexto_chars"] > 0
    assert extras["transcripciones"] == 0
    assert extras["comparativa"] is False


def test_ref_id_del_consumo_lleva_la_conversacion(monkeypatch):
    """El ref_id tiene que permitir agrupar el gasto por conversación."""
    llamadas = {}
    import app.uso_ia as uso_ia

    monkeypatch.setattr(uso_ia, "registrar_uso_ia", lambda **kw: llamadas.update(kw))
    asistente_dashboard._registrar_consumo(None, 42, 5, conversacion_id=99, extras={"a": 1})
    assert llamadas["ref_id"].startswith("dash:99:")
    assert llamadas["extras"] == {"a": 1}

    asistente_dashboard._registrar_consumo(None, 42, 5)
    assert llamadas["ref_id"].startswith("dash:")
    assert not llamadas["ref_id"].startswith("dash:None")


def test_responder_stream_error_gracioso(monkeypatch):
    monkeypatch.setattr(
        asistente_dashboard,
        "_get_client",
        lambda: _cliente_falso([], explota=True),
    )

    contexto = {
        "kpis": {"total": 10},
        "filas": [{"IdGrabacion": "1"}],
    }

    salida = asyncio.run(
        _juntar(
            asistente_dashboard.responder_stream(
                "¿Cómo fue?", contexto
            )
        )
    )

    assert "Ocurrió un error al procesar el análisis" in salida


def test_contenidos_preserva_respuestas_largas_del_asistente():
    respuesta_larga = "Informe detallado: " + ("x" * 5000)
    historial = [
        {"rol": "user", "texto": "Generá un informe"},
        {"rol": "bot", "texto": respuesta_larga},
    ]
    pregunta = "¿Y qué pasó con Juan?"
    contenidos = asistente_dashboard._contenidos(pregunta, historial)
    assert len(contenidos) == 3
    # El turno del modelo no debe haber sido recortado a 2000 caracteres
    assert len(contenidos[1].parts[0].text) > 4000
    assert contenidos[1].role == "model"
    assert contenidos[2].role == "user"
    assert contenidos[2].parts[0].text == "¿Y qué pasó con Juan?"


def test_contenidos_alternancia_estricta_roles():
    historial = [
        {"rol": "user", "texto": "Pregunta 1"},
        {"rol": "user", "texto": "Pregunta 1 duplicada"},
        {"rol": "bot", "texto": "Respuesta 1"},
        {"rol": "user", "texto": "Pregunta 2 sin respuesta previa"},
    ]
    pregunta = "Pregunta 3"
    contenidos = asistente_dashboard._contenidos(pregunta, historial)
    # Debe eliminar el user duplicado inicial y el user colgante antes de la nueva pregunta
    assert len(contenidos) == 3
    assert contenidos[0].role == "user"
    assert contenidos[1].role == "model"
    assert contenidos[2].role == "user"
    assert contenidos[2].parts[0].text == "Pregunta 3"


# --------------------------------------------------------------------------- #
# Recortes declarados: lo que el modelo NO ve tiene que saber que no lo ve      #
# --------------------------------------------------------------------------- #
def _contexto_con_operadores(cantidad):
    """Ranking de peor a mejor, como lo manda el frontend."""
    return {
        "kpis": {"total": cantidad * 10, "casos_ec": 5},
        "resumen_operadores": [
            {"operador": f"Asesor {i:03d}", "equipo": "A", "casos": 10,
             "puntaje_promedio": 50 + i, "casos_ec": 0, "pct_ec": 0}
            for i in range(cantidad)
        ],
    }


def test_ranking_recortado_declara_cuantos_faltan():
    """El bug: con 120 asesores se imprimían 40 sin avisar, y el modelo escribía
    'ningún asesor superó el 90%' habiendo visto un tercio."""
    resumen = asistente_dashboard._sintetizar_datos_dashboard(_contexto_con_operadores(120))
    assert "55 de 120" in resumen
    assert "Faltan 65 asesores" in resumen


def test_ranking_recortado_conserva_las_dos_puntas():
    """Un plan de coaching necesita a quién corregir Y de quién copiar: quedarse
    con los primeros 40 dejaba afuera justo a los referentes."""
    resumen = asistente_dashboard._sintetizar_datos_dashboard(_contexto_con_operadores(120))
    assert "Asesor 000" in resumen   # el peor
    assert "Asesor 119" in resumen   # el mejor
    assert "Asesor 060" not in resumen  # intermedio: no está, y el reporte lo dice


def test_ranking_corto_no_inventa_un_recorte():
    resumen = asistente_dashboard._sintetizar_datos_dashboard(_contexto_con_operadores(12))
    assert "los 12 del período" in resumen
    assert "Faltan" not in resumen


def test_criticos_declaran_el_total_real_aunque_llegue_recortado():
    """El total sale de los KPIs: si el navegador manda una lista recortada,
    len() declararía un total menor al real."""
    contexto = {
        "kpis": {"total": 900, "casos_ec": 214},
        "casos_ec_detalle": [
            {"IdAplicativo": f"id_{i}", "Operador": "X", "motivos_ec": ["No valida"]}
            for i in range(80)
        ],
    }
    resumen = asistente_dashboard._sintetizar_datos_dashboard(contexto)
    assert "listados 60 de 214" in resumen
    assert "154 restantes NO están" in resumen


def test_la_muestra_de_filas_se_declara_como_muestra():
    contexto = {
        "kpis": {"total": 1240},
        "filas": [{"IdAplicativo": str(i), "Operador": "X"} for i in range(600)],
    }
    extra = {
        "filas_seleccionadas": [{"IdAplicativo": "1", "Operador": "María Gómez"}],
        "criterios_muestra": ["los 3 de María Gómez (peor puntaje primero)"],
    }
    resumen = asistente_dashboard._sintetizar_datos_dashboard(contexto, extra)
    assert "muestra de 1 sobre 1240" in resumen
    assert "el dashboard envió 600 filas" in resumen
    assert "María Gómez" in resumen
    assert "No cuentes ni saques porcentajes de esta tabla" in resumen


def test_sin_seleccion_previa_recorta_igual_que_antes():
    contexto = {"kpis": {"total": 900}, "filas": [{"IdAplicativo": str(i)} for i in range(500)]}
    resumen = asistente_dashboard._sintetizar_datos_dashboard(contexto)
    assert f"muestra de {asistente_dashboard.MAX_FILAS_DETALLE} sobre 900" in resumen


# --------------------------------------------------------------------------- #
# Transcripciones y período anterior                                           #
# --------------------------------------------------------------------------- #
def test_las_transcripciones_entran_al_reporte():
    extra = {"transcripciones": [{
        "id_aplicativo": "1088_9931", "operador": "María Gómez", "equipo": "A",
        "fecha": "2026-08-15", "puntaje": 40, "duracion_segundos": 320,
        "texto": "[Asesor]: Buenas tardes\n[Cliente]: Hola", "truncada": False,
    }]}
    resumen = asistente_dashboard._sintetizar_datos_dashboard({"kpis": {"total": 5}}, extra)
    assert "TRANSCRIPCIONES DE LLAMADOS (1)" in resumen
    assert "1088_9931" in resumen
    assert "[Asesor]: Buenas tardes" in resumen


def test_la_comparativa_trae_la_variacion_ya_calculada():
    """El modelo no tiene que restar: si lo hace, se equivoca y lo afirma igual."""
    contexto = {"kpis": {"total": 100, "puntaje_promedio": 85.0, "pct_ec": 4.0}}
    extra = {"comparativa": {
        "desde": "2026-07-01", "hasta": "2026-07-31", "total": 90,
        "puntaje_promedio": 80.0, "pct_ec": 7.0,
        "cumplimiento_atributos": {"Valida identidad": 72.0},
        "equipos": {"A": {"casos": 90, "puntaje_promedio": 80.0, "pct_ec": 7.0}},
    }}
    resumen = asistente_dashboard._sintetizar_datos_dashboard(contexto, extra)
    assert "2026-07-01 al 2026-07-31" in resumen
    assert "+5.0 pts (mejora)" in resumen          # puntaje: subir es mejorar
    assert "-3.0 pts (mejora)" in resumen          # % EC: bajar es mejorar
    assert "Valida identidad: 72.0%" in resumen


def test_periodo_anterior_sin_datos_lo_dice_explicito():
    extra = {"comparativa": {"desde": "2026-07-01", "hasta": "2026-07-31", "total": 0}}
    resumen = asistente_dashboard._sintetizar_datos_dashboard({"kpis": {"total": 5}}, extra)
    assert "No hay auditorías cargadas en ese período" in resumen


def test_las_secciones_se_numeran_solas():
    """Las secciones son opcionales: numerarlas a mano dejaba huecos (1, 2, 5...)."""
    resumen = asistente_dashboard._sintetizar_datos_dashboard({"kpis": {"total": 5}})
    numeros = [int(l.split(".")[0][4:]) for l in resumen.splitlines() if l.startswith("### ")]
    assert numeros == list(range(1, len(numeros) + 1))


# --------------------------------------------------------------------------- #
# Memoria conversacional                                                       #
# --------------------------------------------------------------------------- #
def test_la_memoria_es_de_turnos_completos_no_de_mensajes():
    """El slice era sobre mensajes: 'últimos 10 turnos' eran en realidad 5 idas y
    vueltas, y el asistente se olvidaba a mitad de un análisis largo."""
    historial = []
    for i in range(15):
        historial.append({"rol": "user", "texto": f"pregunta {i}"})
        historial.append({"rol": "bot", "texto": f"respuesta {i}"})

    contenidos = asistente_dashboard._contenidos("pregunta nueva", historial)
    textos = [c.parts[0].text for c in contenidos]
    assert "pregunta 5" in textos        # 10 turnos hacia atrás
    assert "pregunta 4" not in textos    # el 11º ya no
    assert textos[-1] == "pregunta nueva"


def test_el_corte_del_ranking_se_ve_en_la_tabla():
    """El salto de la fila 40 a la 41 son decenas de asesores: en una tabla
    corrida el modelo lee 55 filas seguidas y las trata como el universo."""
    resumen = asistente_dashboard._sintetizar_datos_dashboard(_contexto_con_operadores(120))
    assert "65 asesores de desempeño intermedio, no listados" in resumen


# --------------------------------------------------------------------------- #
# Prefijo cacheable: qué parte del contexto se repite y dónde va               #
# --------------------------------------------------------------------------- #
# Gemini cobra ~10 veces más barato lo que reconoce como prefijo repetido, pero solo
# si es EXACTAMENTE el mismo y está al principio. El dataset de la pantalla no cambia
# en toda la conversación; lo que se trae para cada pregunta, sí. Si lo variable se
# cuela en el medio del reporte (que es como estaba hasta el 2026-09-01), el prefijo
# se rompe en la sección 3 y cada repregunta vuelve a pagar el contexto entero.
def _contexto_de_dos_turnos():
    return {
        "kpis": {"total": 1240, "puntaje_promedio": 85.0, "casos_ec": 12, "pct_ec": 1.0},
        "resumen_operadores": [{"operador": "María Gómez", "casos": 10, "puntaje_promedio": 70.0}],
    }


def test_el_bloque_estable_no_cambia_con_lo_que_trae_cada_pregunta():
    contexto = _contexto_de_dos_turnos()
    primera = asistente_dashboard._bloque_estable(contexto)

    # Segundo turno: se trajeron transcripciones y el período anterior.
    extra = {
        "transcripciones": [{"id_aplicativo": "1088_1", "operador": "María Gómez",
                             "equipo": "A", "texto": "[Asesor]: Buenas"}],
        "comparativa": {"desde": "2026-07-01", "hasta": "2026-07-31", "total": 90,
                        "puntaje_promedio": 80.0},
    }
    segunda = asistente_dashboard._bloque_estable(contexto)

    assert primera == segunda
    # Y nada de lo que cambia por pregunta se coló en el bloque estable.
    de_la_pregunta = asistente_dashboard._bloque_de_la_pregunta(contexto, extra)
    assert "[Asesor]: Buenas" not in primera
    assert "PERÍODO ANTERIOR" not in primera
    assert "[Asesor]: Buenas" in de_la_pregunta
    assert "PERÍODO ANTERIOR" in de_la_pregunta


def test_los_datos_de_la_pregunta_van_en_el_ultimo_turno():
    """Al final del prompt, no adelante: todo lo de arriba tiene que quedar igual que
    en el turno anterior para que Gemini lo reconozca."""
    historial = [{"rol": "user", "texto": "Pregunta 1"}, {"rol": "bot", "texto": "Respuesta 1"}]
    contenidos = asistente_dashboard._contenidos(
        "¿Y María?", historial, "### 8. TRANSCRIPCIONES DE LLAMADOS (1)"
    )

    assert len(contenidos) == 3
    # Los turnos previos quedan intactos (son el prefijo que se reusa).
    assert contenidos[0].parts[0].text == "Pregunta 1"
    assert contenidos[1].parts[0].text == "Respuesta 1"
    ultimo = contenidos[2].parts[0].text
    assert "TRANSCRIPCIONES DE LLAMADOS" in ultimo
    assert ultimo.endswith("¿Y María?")


def test_el_reporte_completo_sigue_leyendose_como_uno_solo():
    """Partirlo es una decisión de facturación, no de contenido: el modelo tiene que
    seguir viendo un reporte con las secciones numeradas de corrido."""
    contexto = _contexto_de_dos_turnos()
    extra = {"comparativa": {"desde": "2026-07-01", "hasta": "2026-07-31", "total": 90}}
    completo = asistente_dashboard._sintetizar_datos_dashboard(contexto, extra)

    numeros = [int(l.split(".")[0][4:]) for l in completo.splitlines() if l.startswith("### ")]
    assert numeros == list(range(1, len(numeros) + 1))
