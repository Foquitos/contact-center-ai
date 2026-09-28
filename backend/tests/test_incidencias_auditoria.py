"""Incidencias de auditoría: el llamado que NO se puede auditar.

Cubre las tres vías por las que se marca una auditoría como no confiable
(ver backend/AuditorIA/incidencias.py):

  1. el gate de audio, que mide el archivo antes de gastar tokens;
  2. la propia IA, que ahora tiene un campo en el schema para declararla;
  3. los chequeos post-auditoría (operador que no coincide, duración que no cierra).

El caso real que motivó todo: un audio mudo de Voltara que Gemini "auditó" armando
una conversación entera con la metadata del prompt, y dos grabaciones intercambiadas
entre sí que quedaron auditadas cada una bajo el legajo del otro operador.

Test 100% offline: no toca DB, ni Gemini, ni tokens. El único binario que usa es
ffmpeg, y solo en los tests marcados con `ffmpeg` (se saltean si no está).
Correr: pytest tests/test_incidencias_auditoria.py -m "not tokens"
"""
import json
import os
import shutil
import subprocess

import pandas as pd
import pytest

from AuditorIA import audio_calidad, gemini_files, incidencias as inc


FFMPEG_DISPONIBLE = shutil.which('ffmpeg') is not None or os.path.exists('/usr/bin/ffmpeg')
necesita_ffmpeg = pytest.mark.skipif(not FFMPEG_DISPONIBLE, reason="ffmpeg no está instalado")


# --------------------------------------------------------------------------- #
# 1. Gate de audio                                                             #
# --------------------------------------------------------------------------- #

def _generar(tmp_path, nombre, filtro, segundos=8):
    """Genera un .ogg de prueba con ffmpeg (silencio o tono)."""
    destino = str(tmp_path / nombre)
    subprocess.run(
        ['/usr/bin/ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
         '-f', 'lavfi', '-i', filtro, '-t', str(segundos),
         '-c:a', 'libopus', '-b:a', '32k', destino],
        check=True,
    )
    return destino


@necesita_ffmpeg
def test_audio_mudo_se_detecta(tmp_path):
    """Un audio en silencio se marca como mudo y no llega nunca a la IA."""
    ruta = _generar(tmp_path, 'mudo.ogg', 'anullsrc=r=16000:cl=mono')
    motivo, analisis = audio_calidad.evaluar(ruta)
    assert motivo == inc.AUDIO_MUDO, f"análisis: {analisis.resumen() if analisis else None}"


@necesita_ffmpeg
def test_audio_con_voz_no_se_marca(tmp_path):
    """Un audio con señal continua se audita normalmente: el gate no lo toca.

    Es el chequeo que protege contra el falso positivo, que es el error caro:
    dejar sin auditar una llamada legítima.
    """
    ruta = _generar(tmp_path, 'tono.ogg', 'sine=frequency=440:sample_rate=16000')
    motivo, _ = audio_calidad.evaluar(ruta)
    assert motivo is None


@necesita_ffmpeg
def test_mide_la_duracion_real(tmp_path):
    ruta = _generar(tmp_path, 'diez.ogg', 'sine=frequency=440:sample_rate=16000', segundos=10)
    analisis = audio_calidad.analizar(ruta)
    assert analisis is not None and abs(analisis.duracion - 10) < 1


@necesita_ffmpeg
def test_medir_y_comprimir_son_una_sola_pasada(tmp_path):
    """Con `convertir=True` la misma pasada de ffmpeg mide Y deja el .clean.ogg.

    Antes el WAV se decodificaba dos veces: una para medirlo y otra para comprimirlo.
    Lo que se chequea acá es que fusionarlas no cambió ningún número (los filtros de
    medición pasan el audio sin tocarlo) y que el archivo comprimido queda listo para
    que lo reutilice `convertir_a_mp3_estandar` sin volver a codificar.
    """
    ruta = str(tmp_path / 'llamado.wav')
    subprocess.run(
        ['/usr/bin/ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
         '-f', 'lavfi', '-t', '6', '-i', 'sine=frequency=440:sample_rate=8000',
         '-f', 'lavfi', '-t', '6', '-i', 'anullsrc=r=8000:cl=mono',
         '-filter_complex', '[0:a][1:a]concat=n=2:v=0:a=1[out]', '-map', '[out]',
         '-ar', '8000', '-ac', '1', ruta],
        check=True,
    )

    solo_medir = audio_calidad.analizar(ruta)
    fusionada = audio_calidad.analizar(ruta, convertir=True)

    assert solo_medir is not None and fusionada is not None
    assert fusionada.duracion == pytest.approx(solo_medir.duracion, abs=0.1)
    assert fusionada.mean_db == pytest.approx(solo_medir.mean_db, abs=0.1)
    assert fusionada.segundos_silencio == pytest.approx(solo_medir.segundos_silencio, abs=0.1)

    comprimido = tmp_path / 'llamado.wav.clean.ogg'
    assert comprimido.exists() and comprimido.stat().st_size > 0
    assert comprimido.stat().st_size < os.path.getsize(ruta)

    # La conversión posterior reutiliza ese archivo en vez de codificar de nuevo.
    modificado = comprimido.stat().st_mtime
    assert gemini_files.convertir_a_mp3_estandar(ruta) == str(comprimido)
    assert comprimido.stat().st_mtime == modificado


@necesita_ffmpeg
def test_medir_por_defecto_no_escribe_nada(tmp_path):
    """Medir sin `convertir` no puede tener efectos sobre el disco.

    scripts/calibrar_audio_mudo.py corre `analizar` sobre TODO el store para calibrar
    los umbrales: si midiera dejando un .clean.ogg al lado de cada audio, duplicaría
    la carpeta y se comería el tope de 5 GB.
    """
    ruta = _generar(tmp_path, 'suelto.ogg', 'sine=frequency=440:sample_rate=16000')
    audio_calidad.analizar(ruta)
    assert sorted(p.name for p in tmp_path.iterdir()) == ['suelto.ogg']


def test_medir_algo_que_no_es_audio_no_rompe(tmp_path):
    """Medir es best-effort: un archivo inexistente o un chat (.json) no frena nada."""
    assert audio_calidad.analizar(str(tmp_path / 'no_existe.ogg')) is None
    chat = tmp_path / 'chat.json'
    chat.write_text('[]', encoding='utf-8')
    assert audio_calidad.analizar(str(chat)) is None
    assert audio_calidad.evaluar(None) == (None, None)


def test_silencio_abierto_al_final_se_cuenta():
    """Un tramo de silencio que llega hasta el final del archivo no siempre cierra
    con su silence_duration; si no se contara, un audio mudo mediría 0% de silencio."""
    salida = (
        "  Duration: 00:00:20.00, start: 0.000000, bitrate: 13 kb/s\n"
        "[Parsed_volumedetect_0 @ 0x1] mean_volume: -55.0 dB\n"
        "[Parsed_volumedetect_0 @ 0x1] max_volume: -40.0 dB\n"
        "[silencedetect @ 0x2] silence_start: 2\n"
    )
    analisis = audio_calidad._parsear_salida(salida)
    assert analisis.duracion == 20
    assert analisis.segundos_silencio == 18
    assert analisis.ratio_silencio == pytest.approx(0.9)


# --------------------------------------------------------------------------- #
# 2. Operador que no coincide                                                  #
# --------------------------------------------------------------------------- #

def _segments(texto_agente):
    return [{"speakerLabel": "Agente", "startTime": 0.0, "endTime": 6.0,
             "text": texto_agente.split()}]


@pytest.mark.parametrize("esperado,dicho,coincide", [
    ("RAMON DURAN", "Buen día se está comunicando con Voltara mi nombre es Ramón Durán", True),
    ("MARTA PEREYRA", "Voltara buenos días mi nombre es Pereyra Marta", True),   # orden invertido
    ("PAULA BEATRIZ SUAREZ", "Emergencias Voltara le habla Paula Suarez", True), # segundo nombre de más
    ("PAULA BEATRIZ SUAREZ", "Buen día mi nombre es Ramón Durán", False),         # el cruce real
    ("RAMON DURAN", "Voltara buen día mi nombre es Paula Suarez", False),        # su espejo
])
def test_deteccion_de_operador_cruzado(esperado, dicho, coincide):
    assert inc.operador_coincide(esperado, dicho) is coincide


def test_sin_autopresentacion_no_opina():
    """Si el agente nunca dice su nombre no hay evidencia: el chequeo se abstiene
    en vez de marcar a media campaña como sospechosa."""
    assert inc.operador_coincide("RAMON DURAN", "Buenos días, ¿me indica su DNI?") is None
    assert inc.operador_coincide(None, "mi nombre es Ramón Durán") is None


def test_verificar_operador_marca_solo_la_fila_cruzada():
    df = pd.DataFrame({
        'Nombre de Agente': ['RAMON DURAN', 'PAULA BEATRIZ SUAREZ'],
        'duracion_segundos': [253, 245],
    })
    trans = pd.DataFrame({'segments': [
        _segments("Buen día se está comunicando con Voltara mi nombre es Ramón Durán"),
        _segments("Buen día se está comunicando con Voltara mi nombre es Ramón Durán"),
    ]})

    assert inc.verificar_operador(df, trans) == 1
    assert df.at[0, inc.COLUMNA] is None or pd.isna(df.at[0, inc.COLUMNA])
    assert df.at[1, inc.COLUMNA] == inc.OPERADOR_NO_COINCIDE


def test_texto_de_segments_acepta_json_y_recorta_la_apertura():
    segs = [
        {"speakerLabel": "Agente", "startTime": 0.0, "text": ["hola", "soy", "leon"]},
        {"speakerLabel": "Cliente", "startTime": 90.0, "text": ["chau"]},
    ]
    texto = inc.texto_de_segments(json.dumps(segs), hasta_segundo=30)
    assert "leon" in texto and "chau" not in texto


# --------------------------------------------------------------------------- #
# 3. Duración que no cierra                                                    #
# --------------------------------------------------------------------------- #

def test_duracion_marca_el_cruce_pero_tolera_el_recorte_chico():
    df = pd.DataFrame({'duracion_segundos': [245, 253, 156]})
    # fila 0: el archivo dura la mitad (audios intercambiados);
    # fila 1: coincide; fila 2: 12s de diferencia sobre 156 -> dentro de tolerancia.
    reales = {0: 108.8, 1: 253.6, 2: 144.0}
    assert inc.verificar_duracion(df, reales) == 1
    assert df.at[0, inc.COLUMNA] == inc.DURACION_NO_COINCIDE
    assert pd.isna(df.at[1, inc.COLUMNA]) or df.at[1, inc.COLUMNA] is None


# --------------------------------------------------------------------------- #
# 4. Prioridad y forma final de la columna                                     #
# --------------------------------------------------------------------------- #

def test_el_gate_le_gana_a_la_ia():
    """ffmpeg midió el archivo; la IA opina sobre lo que cree haber escuchado."""
    df = pd.DataFrame({inc.COLUMNA: [inc.AUDIO_MUDO, None],
                       inc.COLUMNA_IA: ['ninguna', inc.AUDIO_INCOMPLETO]})
    df = inc.fusionar_declarada_por_ia(df)
    assert inc.COLUMNA_IA not in df.columns
    assert df.at[0, inc.COLUMNA] == inc.AUDIO_MUDO
    assert df.at[1, inc.COLUMNA] == inc.AUDIO_INCOMPLETO


def test_ninguna_se_guarda_como_nulo():
    """La columna tiene que significar siempre 'hay algo que mirar'."""
    assert inc.limpiar_valor('ninguna') is None
    assert inc.limpiar_valor('  ') is None
    assert inc.limpiar_valor(None) is None
    assert inc.limpiar_valor(' Audio_Mudo ') == inc.AUDIO_MUDO


def test_marcar_no_pisa_una_incidencia_previa():
    df = pd.DataFrame({'x': [1, 2]})
    inc.marcar(df, [0, 1], inc.AUDIO_MUDO)
    inc.marcar(df, [0, 1], inc.DURACION_NO_COINCIDE)
    assert list(df[inc.COLUMNA]) == [inc.AUDIO_MUDO, inc.AUDIO_MUDO]


# --------------------------------------------------------------------------- #
# 5. El campo en el schema de Gemini                                           #
# --------------------------------------------------------------------------- #
import json as _json  # noqa: E402  (sección aparte, para no ensuciar el encabezado)
from types import SimpleNamespace  # noqa: E402

from AuditorIA import gemini  # noqa: E402
from app.models import PlantillasIA  # noqa: E402

ATRIBUTOS = [
    {"name": "empatia", "type": "enum", "constraints": {"enum": ["Cumple", "No cumple"]}, "id": 7},
    {"name": "feedback", "type": "string", "constraints": None, "id": 8},
]


@pytest.fixture
def prompt_info(monkeypatch):
    """Plantilla en memoria: no toca la BD (mismo doble que test_atributos_opcionales)."""
    monkeypatch.setattr(
        gemini.plantillas_manager_instance, "obtener_plantilla_para_IA",
        lambda plantilla_id: PlantillasIA(
            system_prompts="Sos un auditor.", text="Auditá este llamado.",
            response_schema=_json.dumps(ATRIBUTOS, ensure_ascii=False), modelo="gemini-fake",
        ),
    )
    return gemini.prompt(plantilla_id=1)


def test_toda_plantilla_puede_declarar_incidencia(prompt_info):
    """Sin un lugar en el schema, el modelo no tiene forma de decir 'no puedo auditar
    esto' y completa igual: es exactamente lo que pasó con el audio mudo."""
    schema = prompt_info["response_schema"]
    assert gemini.CAMPO_INCIDENCIA in (schema.properties or {})
    assert gemini.CAMPO_INCIDENCIA in (schema.required or [])
    assert list(schema.properties[gemini.CAMPO_INCIDENCIA].enum) == list(inc.DECLARABLES_POR_IA)


def test_el_prompt_prohibe_reconstruir_el_llamado(prompt_info):
    """El prompt trae nombre del agente, motivo y documento del cliente: hay que
    decirle explícitamente que eso NO es lo que se dijo en el audio."""
    texto = prompt_info["text"]
    assert "INCIDENCIAS DEL AUDIO" in texto
    assert "NO reconstruyas" in texto


def _respuesta_falsa(payload):
    parte = SimpleNamespace(text=_json.dumps(payload, ensure_ascii=False), thought=False)
    contenido = SimpleNamespace(parts=[parte])
    return SimpleNamespace(
        candidates=[SimpleNamespace(content=contenido)],
        text=_json.dumps(payload, ensure_ascii=False),
        usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5,
                                       thoughts_token_count=1),
    )


def test_la_incidencia_no_se_confunde_con_un_atributo():
    """Va por su propia columna: si entrara como Detalle_ el guardado buscaría un
    AtributoID llamado 'Incidencia' y reventaría la corrida entera."""
    serie = gemini.procesar_respuesta(_respuesta_falsa({
        "empatia": "Cumple", "feedback": "ok", gemini.CAMPO_INCIDENCIA: "audio_mudo",
    }))
    assert serie[inc.COLUMNA_IA] == inc.AUDIO_MUDO
    assert f"Detalle_{gemini.CAMPO_INCIDENCIA}" not in serie.index
    assert serie["Detalle_empatia"] == "Cumple"


def test_incidencia_ninguna_llega_como_nulo():
    serie = gemini.procesar_respuesta(_respuesta_falsa({
        "empatia": "Cumple", gemini.CAMPO_INCIDENCIA: "ninguna",
    }))
    assert serie[inc.COLUMNA_IA] is None


def test_respuesta_combinada_separa_transcripcion_e_incidencia():
    serie_cal, serie_trans = gemini.procesar_respuesta_combinada(_respuesta_falsa({
        "empatia": "Cumple",
        gemini.CAMPO_INCIDENCIA: "operador_no_coincide",
        "Transcripcion": {"metadata": {"languageCode": "es-AR"}, "segments": [], "analytics": {}},
    }))
    assert serie_cal[inc.COLUMNA_IA] == inc.OPERADOR_NO_COINCIDE
    assert serie_trans is not None
    assert "Detalle_Transcripcion" not in serie_cal.index


# --------------------------------------------------------------------------- #
# 6. El gate sobre el DataFrame de la corrida                                  #
# --------------------------------------------------------------------------- #

@necesita_ffmpeg
def test_el_gate_aparta_el_mudo_y_deja_pasar_el_resto(tmp_path):
    """El corazón de la defensa: el audio sin voz nunca llega a Gemini."""
    mudo = _generar(tmp_path, 'g_mudo.ogg', 'anullsrc=r=16000:cl=mono')
    con_voz = _generar(tmp_path, 'g_voz.ogg', 'sine=frequency=440:sample_rate=16000')
    df = pd.DataFrame({'audio_dir': [con_voz, mudo], 'duracion_segundos': [8, 8]})

    auditables, no_auditables = audio_calidad.filtrar_no_auditables(df)

    assert list(auditables['audio_dir']) == [con_voz]
    assert list(no_auditables['audio_dir']) == [mudo]
    assert no_auditables.iloc[0][inc.COLUMNA] == inc.AUDIO_MUDO


@necesita_ffmpeg
def test_el_gate_se_puede_apagar(tmp_path, monkeypatch):
    """Interruptor por si un formato raro empieza a dar falsos positivos en prod."""
    from app.config import settings
    monkeypatch.setattr(settings, 'AUDIO_GATE_MUDO', False, raising=False)
    mudo = _generar(tmp_path, 'g_off.ogg', 'anullsrc=r=16000:cl=mono')
    df = pd.DataFrame({'audio_dir': [mudo]})

    auditables, no_auditables = audio_calidad.filtrar_no_auditables(df)
    assert len(auditables) == 1 and no_auditables.empty


def test_un_df_sin_audios_pasa_derecho():
    """Los chats no tienen audio: el gate no se mete con ellos."""
    df = pd.DataFrame({'chat': ['hola']})
    auditables, no_auditables = audio_calidad.filtrar_no_auditables(df)
    assert len(auditables) == 1 and no_auditables.empty


# --------------------------------------------------------------------------- #
# 7. El guardado: una auditoría marcada no puntúa                              #
# --------------------------------------------------------------------------- #
from contextlib import contextmanager  # noqa: E402

from AuditorIA import sql_a_Claude  # noqa: E402


class _ConnFalsa:
    """Conexión de mentira que registra las queries en vez de ejecutarlas."""

    def __init__(self, registro, columnas_existentes):
        self.registro = registro
        self.columnas = columnas_existentes

    def execute(self, query, params=None):
        sql = str(query)
        self.registro.append((sql, params))

        if 'INFORMATION_SCHEMA.COLUMNS' in sql:
            existe = (params or {}).get('columna') in self.columnas
            return SimpleNamespace(first=lambda: (1,) if existe else None)
        if 'FROM calidad.Atributos' in sql or 'Atributos' in sql and 'SELECT' in sql.upper():
            return SimpleNamespace(fetchall=lambda: [(1, 'enum', 1.0)])
        if 'INSERT INTO calidad.Auditorias' in sql:
            return SimpleNamespace(scalar=lambda: 12345)
        return SimpleNamespace(scalar=lambda: None, fetchall=lambda: [])


class _EngineFalso:
    def __init__(self, columnas_existentes=('Incidencia',)):
        self.registro = []
        self.columnas = set(columnas_existentes)

    @contextmanager
    def begin(self):
        yield _ConnFalsa(self.registro, self.columnas)

    @contextmanager
    def connect(self):
        yield _ConnFalsa(self.registro, self.columnas)


def _guardar(df):
    engine = _EngineFalso()
    sql_a_Claude.auditoria_a_SQL(df=df, engine=engine, modo="sync", modelo="gemini-fake")
    return engine.registro


def _fila(**extra):
    base = {
        'id_aplicativo': 'ABC123', 'operador_usuario': 'AR1', 'fecha_interaccion': None,
        'sentido_interaccion': 'Entrante', 'tipificacion_interaccion': None,
        'duracion_segundos': 100, 'comentario_interaccion': None, 'campana_id': 1,
        'empresa_id': 1, 'plantilla_id': 1, 'auditor_id': 1, 'input_tokens': 1,
        'output_tokens': 1, 'thoughts_tokens': 0, 'response_thoughts': None,
        'Extras': None, 'Detalle_1': 'Cumple',
    }
    base.update(extra)
    return pd.DataFrame([base])


def test_una_auditoria_con_audio_mudo_no_guarda_detalles_ni_puntaje():
    """El núcleo del estado 'no auditable': queda el registro del llamado, pero sin
    respuestas. Sin detalles no promedia ningún atributo, y con PuntajeFinal NULL
    tampoco entra en el promedio de puntaje: por eso no hubo que tocar los tableros."""
    registro = _guardar(_fila(incidencia=inc.AUDIO_MUDO))

    inserts_auditoria = [(s, p) for s, p in registro if 'INSERT INTO calidad.Auditorias' in s]
    inserts_detalle = [s for s, _ in registro if 'INSERT INTO calidad.AuditoriaDetalles' in s]

    assert len(inserts_auditoria) == 1, "el llamado tiene que quedar registrado igual"
    assert not inserts_detalle, "una auditoría marcada no puede guardar respuestas"
    assert inserts_auditoria[0][1]['puntaje_final'] is None
    assert inserts_auditoria[0][1]['incidencia'] == inc.AUDIO_MUDO


def test_una_auditoria_normal_sigue_guardando_todo():
    registro = _guardar(_fila())
    assert any('INSERT INTO calidad.AuditoriaDetalles' in s for s, _ in registro)
    insert = next(p for s, p in registro if 'INSERT INTO calidad.Auditorias' in s)
    assert insert['incidencia'] is None


def test_una_incidencia_no_bloqueante_conserva_las_respuestas():
    """'duración que no coincide' es un aviso para revisar, no un veto: la auditoría
    puede ser perfectamente válida (hay campañas con audios recortados)."""
    registro = _guardar(_fila(incidencia=inc.DURACION_NO_COINCIDE))
    assert any('INSERT INTO calidad.AuditoriaDetalles' in s for s, _ in registro)


def test_sin_la_migracion_aplicada_el_guardado_no_se_rompe():
    """Retrocompatible: si la columna no existe, se guarda sin ella (mismo criterio
    que PlantillaVersionID)."""
    engine = _EngineFalso(columnas_existentes=())
    sql_a_Claude.auditoria_a_SQL(df=_fila(incidencia=inc.AUDIO_MUDO), engine=engine,
                                 modo="sync", modelo="gemini-fake")
    insert = next(p for s, p in engine.registro if 'INSERT INTO calidad.Auditorias' in s)
    assert 'incidencia' not in insert
