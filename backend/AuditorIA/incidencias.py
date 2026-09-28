"""Incidencias de una auditoría: por qué un llamado no se pudo auditar (o hay que mirarlo).

Hasta ahora una auditoría solo podía terminar en dos estados: guardada con todos sus
atributos respondidos, o FALLIDO por un error técnico. No había forma de expresar
"esto se auditó pero no hay que creerle", que es justamente lo que pasó con un audio
mudo que la IA completó de memoria y con dos grabaciones que llegaron cruzadas entre sí
(el audio de un operador guardado bajo el ConnID de otro).

Una incidencia es esa tercera salida. Viaja como columna `incidencia` del DataFrame
desde tres orígenes distintos y termina en calidad.Auditorias.Incidencia:

  1. El gate de audio, ANTES de llamar a la IA (AuditorIA/audio_calidad.py).
  2. La propia IA, que ahora tiene un campo en el schema para declararla en vez de
     dejarla enterrada en su razonamiento (AuditorIA/gemini.py).
  3. Los chequeos post-auditoría de este módulo (operador que no coincide, duración
     que no cierra contra el sistema de origen).

Una auditoría con incidencia se guarda SIN detalles: no puntúa, no promedia y no
penaliza al operador — el mismo criterio que los atributos opcionales sin evidencia.
"""
import json
import logging
import re
import unicodedata
from typing import Any, Iterable, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Columna que transporta la incidencia por el DataFrame hasta el guardado.
COLUMNA = 'incidencia'
# La que declara la IA llega por separado y recién después se fusiona con la anterior:
# si compartieran nombre, el join de resultados contra el df original chocaría.
COLUMNA_IA = 'incidencia_ia'

NINGUNA = 'ninguna'
AUDIO_MUDO = 'audio_mudo'
AUDIO_MAYORMENTE_SILENCIO = 'audio_mayormente_silencio'
AUDIO_INCOMPLETO = 'audio_incompleto'
OPERADOR_NO_COINCIDE = 'operador_no_coincide'
DURACION_NO_COINCIDE = 'duracion_no_coincide'

# Las que puede declarar la IA (van al enum del response_schema). `ninguna` es el caso
# normal y debe ser el primero: es lo que responde en la enorme mayoría de los llamados.
DECLARABLES_POR_IA = [NINGUNA, AUDIO_MUDO, AUDIO_INCOMPLETO, OPERADOR_NO_COINCIDE]

# Las que impiden confiar en el resultado: la auditoría se guarda marcada y sin detalles.
BLOQUEANTES = {AUDIO_MUDO, OPERADOR_NO_COINCIDE}

# Texto para el usuario (bandeja de "Auditorías Realizadas").
DESCRIPCIONES = {
    AUDIO_MUDO: "El audio no tiene voz audible: no se pudo auditar.",
    AUDIO_MAYORMENTE_SILENCIO: "El audio está casi todo en silencio: revisar antes de tomar el resultado.",
    AUDIO_INCOMPLETO: "El audio está cortado o incompleto.",
    OPERADOR_NO_COINCIDE: "El operador que se escucha no es el que figura en el sistema.",
    DURACION_NO_COINCIDE: "La duración del audio no coincide con la del llamado en el sistema.",
}

# Cómo se llama el agente según la campaña. Se busca en este orden.
_COLUMNAS_NOMBRE_AGENTE = [
    'Nombre de Agente', 'nombre_agente', 'Nombre Agente', 'Empleado', 'Agente', 'agente',
]

# Autopresentación del agente al abrir el llamado ("mi nombre es X", "le habla X").
# Se piden 1 o 2 palabras: alcanza para comparar contra el padrón sin arrastrar media
# oración.
_RE_PRESENTACION = re.compile(
    r'(?:mi\s+nombre\s+es|le\s+habla|te\s+habla|les\s+habla|habla|se\s+comunica\s+con)\s+'
    r'([a-zñáéíóúü]+(?:\s+[a-zñáéíóúü]+)?)',
    re.IGNORECASE,
)

# Palabras que siguen a la fórmula de presentación pero no son un nombre.
_RUIDO = {
    'con', 'de', 'del', 'la', 'el', 'usted', 'buenos', 'buenas', 'dias', 'días', 'tardes',
    'noches', 'voltara', 'para', 'que', 'como', 'le', 'te', 'su', 'un', 'una', 'por',
    'favor', 'gracias', 'hola', 'si', 'no', 'y', 'a', 'en',
}


def normalizar(texto: Any) -> str:
    """Minúsculas sin tildes ni puntuación, para comparar nombres."""
    if texto is None or (isinstance(texto, float) and pd.isna(texto)):
        return ''
    s = unicodedata.normalize('NFKD', str(texto))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r'[^a-zA-Z\s]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip().lower()


def _tokens_nombre(texto: Any) -> set:
    """Tokens comparables de un nombre (descarta partículas y palabras muy cortas)."""
    return {t for t in normalizar(texto).split() if len(t) >= 4 and t not in _RUIDO}


def texto_de_segments(segments: Any, hasta_segundo: Optional[float] = None) -> str:
    """Aplana los `segments` de una transcripción a texto plano.

    `hasta_segundo` limita a la apertura del llamado (donde el agente se presenta).
    Acepta la lista ya parseada o su JSON, que es como viaja según el camino.
    """
    if isinstance(segments, str):
        try:
            segments = json.loads(segments)
        except (ValueError, TypeError):
            return ''
    if not isinstance(segments, list):
        return ''

    partes = []
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        if hasta_segundo is not None:
            inicio = seg.get('startTime')
            if isinstance(inicio, (int, float)) and inicio > hasta_segundo:
                break
        texto = seg.get('text')
        if isinstance(texto, list):
            partes.append(' '.join(str(p) for p in texto))
        elif texto:
            partes.append(str(texto))
    return ' '.join(partes)


def nombre_declarado_en_audio(texto_apertura: str) -> Optional[str]:
    """Nombre con el que el agente se presenta, o None si no se presenta.

    Devolver None es lo normal en muchas campañas (el agente no dice su nombre): sin
    autopresentación no se puede afirmar nada y el chequeo se abstiene.
    """
    if not texto_apertura:
        return None
    m = _RE_PRESENTACION.search(normalizar(texto_apertura))
    if not m:
        return None
    candidato = ' '.join(p for p in m.group(1).split() if p not in _RUIDO)
    return candidato or None


def operador_coincide(nombre_esperado: Any, texto_apertura: str) -> Optional[bool]:
    """¿El agente que se escucha es el que figura en el sistema?

    True/False, o None cuando no hay evidencia suficiente para opinar (no hay nombre
    esperado, o el agente nunca se presenta). Comparar por tokens tolera el orden
    invertido ("Pereyra Marta") y que diga solo el nombre de pila.
    """
    esperados = _tokens_nombre(nombre_esperado)
    if not esperados:
        return None
    declarado = nombre_declarado_en_audio(texto_apertura)
    if not declarado:
        return None
    dichos = _tokens_nombre(declarado)
    if not dichos:
        return None
    return bool(esperados & dichos)


def columna_nombre_agente(df: pd.DataFrame) -> Optional[str]:
    """Primera columna del df que contiene el nombre del agente, si hay alguna."""
    for col in _COLUMNAS_NOMBRE_AGENTE:
        if col in df.columns:
            return col
    return None


def marcar(df: pd.DataFrame, indices: Iterable, motivo: str) -> pd.DataFrame:
    """Marca filas con una incidencia sin pisar una ya existente.

    La primera incidencia detectada manda: el gate de audio corre antes que la IA y
    que los chequeos post, y su diagnóstico es el más confiable de los tres (midió el
    archivo). Devuelve el mismo df, mutado.
    """
    indices = list(indices)
    if not indices:
        return df
    if COLUMNA not in df.columns:
        df[COLUMNA] = None
    for idx in indices:
        if idx not in df.index:
            continue
        actual = df.at[idx, COLUMNA]
        if actual is None or (isinstance(actual, float) and pd.isna(actual)) or actual in ('', NINGUNA):
            df.at[idx, COLUMNA] = motivo
    return df


def limpiar_valor(valor: Any) -> Optional[str]:
    """Forma final de una incidencia suelta: motivo real, o None si no hay ninguna.

    La IA responde `ninguna` cuando no ve problemas; eso se guarda como NULL para que
    la columna signifique siempre "hay algo que mirar" y se pueda filtrar con un
    IS NOT NULL.
    """
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    texto = str(valor).strip().lower()
    if not texto or texto == NINGUNA:
        return None
    return texto


def normalizar_columna(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica `limpiar_valor` a toda la columna `incidencia` del DataFrame."""
    if COLUMNA not in df.columns:
        return df
    df[COLUMNA] = df[COLUMNA].apply(limpiar_valor)
    return df


def fusionar_declarada_por_ia(df: pd.DataFrame) -> pd.DataFrame:
    """Vuelca la incidencia que declaró la IA a la columna final y limpia la auxiliar.

    Gana lo que midió el gate: ffmpeg leyó el archivo, la IA opina sobre lo que cree
    haber escuchado. Si el gate no dijo nada, vale lo que declara la IA.
    """
    if COLUMNA_IA not in df.columns:
        return df
    declaradas = df[COLUMNA_IA].apply(limpiar_valor)
    for motivo in declaradas.dropna().unique():
        marcar(df, declaradas[declaradas == motivo].index, motivo)
    return df.drop(columns=[COLUMNA_IA])


def verificar_operador(df: pd.DataFrame, df_transcripciones: Optional[pd.DataFrame],
                       segundos_apertura: float = 30.0) -> int:
    """Marca las filas donde el agente que se escucha no es el que figura en el sistema.

    Es la red que atrapa un audio guardado bajo el identificador de otro llamado: el
    caso real fueron dos grabaciones de Voltara intercambiadas entre sí, donde la IA
    incluso notó el conflicto y lo dejó escrito en su razonamiento, invisible para
    todos. Devuelve cuántas filas marcó.
    """
    if df is None or df.empty or df_transcripciones is None or df_transcripciones.empty:
        return 0
    if 'segments' not in df_transcripciones.columns:
        return 0

    col_nombre = columna_nombre_agente(df)
    if not col_nombre:
        return 0

    sospechosas = []
    for idx, segments in df_transcripciones['segments'].items():
        if idx not in df.index:
            continue
        apertura = texto_de_segments(segments, hasta_segundo=segundos_apertura)
        coincide = operador_coincide(df.at[idx, col_nombre], apertura)
        if coincide is False:
            sospechosas.append(idx)
            logger.warning(
                "Operador que no coincide (fila %s): el sistema dice '%s' y en el audio "
                "se presenta '%s'.",
                idx, df.at[idx, col_nombre], nombre_declarado_en_audio(apertura),
            )

    marcar(df, sospechosas, OPERADOR_NO_COINCIDE)
    return len(sospechosas)


def verificar_duracion(df: pd.DataFrame, duraciones_reales: dict,
                       tolerancia_segundos: float = 15.0,
                       tolerancia_ratio: float = 0.25) -> int:
    """Marca las filas donde el audio no dura lo que dice el sistema de origen.

    Sirve de aviso temprano de archivo cruzado o recortado, pero NO es prueba: hay
    campañas cuyos audios legítimamente incluyen espera o vienen recortados. Por eso
    marca (no bloquea) y exige superar las dos tolerancias, absoluta y relativa.
    Devuelve cuántas filas marcó.
    """
    if df is None or df.empty or not duraciones_reales:
        return 0
    if 'duracion_segundos' not in df.columns:
        return 0

    desfasadas = []
    for idx, esperada in df['duracion_segundos'].items():
        real = duraciones_reales.get(idx)
        try:
            esperada = float(esperada)
        except (TypeError, ValueError):
            continue
        if not real or esperada <= 0:
            continue
        delta = abs(real - esperada)
        if delta > tolerancia_segundos and delta / esperada > tolerancia_ratio:
            desfasadas.append(idx)
            logger.warning(
                "Duración que no coincide (fila %s): el sistema dice %.0fs y el archivo dura %.0fs.",
                idx, esperada, real,
            )

    marcar(df, desfasadas, DURACION_NO_COINCIDE)
    return len(desfasadas)
