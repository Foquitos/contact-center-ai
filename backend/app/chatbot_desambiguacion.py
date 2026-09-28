"""Desambiguación: cuando la pregunta no alcanza, ofrecer temas en vez de "no encontré".

EL PROBLEMA
-----------
El operador tiene al cliente en línea y escribe corto: "poda", "medidor", "cambio
de titular". Contra el corpus, esas consultas no recuperan UN fragmento que
responda: recuperan VARIOS pedazos parecidos de secciones distintas, todos a media
altura de score. El bot entonces contesta la frase de siempre —"No encontré
información sobre ese tema"— y el operador se queda sin nada, aunque el manual SÍ
tenga los tres temas documentados. No es un hueco de documentación: es una
pregunta que no alcanza para elegir cuál.

LO QUE HACE
-----------
Antes de generar, se mira lo recuperado. Si nada responde con claridad pero hay
varios TEMAS distintos compitiendo, se responde con la lista de esos temas para
que el operador elija (y el frontend los muestra como botones). No se llama al
LLM: la lista sale de los encabezados del propio markdown indexado, así que es
determinística, cuesta cero tokens y lo que se ofrece existe de verdad.

POR QUÉ NO ROMPE LOS VACÍOS DE CONOCIMIENTO
-------------------------------------------
La detección de vacíos (`app/vacios_conocimiento.py`) se alimenta justamente de
esas respuestas: si el bot deja de decir "no encontré", Calidad deja de enterarse
de lo que falta documentar. Por eso la desambiguación vive en una BANDA de score
acotada por los dos umbrales que esa detección ya tiene calibrados:

    score < CHATBOT_DESAMB_SCORE_MIN   -> no se toca. Nada de lo recuperado tiene
                                          que ver: es un hueco real, el bot dice
                                          "no encontré" y el vacío se registra
                                          igual que siempre.
    score >= CHATBOT_VACIO_SCORE_EXISTE -> no se toca. Algo responde de verdad:
                                          que conteste, no que pregunte.
    en el medio + >= 2 temas parejos    -> se ofrece elegir.

Y además hace falta que haya DOS temas peleando cabeza a cabeza (ver
CHATBOT_DESAMB_EMPATE_FRACCION): si uno domina, la pregunta no era ambigua —era
un dato que falta en ese tema— y esa respuesta tiene que seguir siendo un "no
encontré" para que llegue a la pantalla de Calidad.

El bloque que se emite queda marcado en el texto (INICIO/FIN), y
`evaluar_cobertura` lo reconoce para no contarlo como hueco aunque los umbrales
se recalibren para otro reranker.

Y CUANDO EL MODELO SE NIEGA IGUAL (`proponer_tras_negativa`)
------------------------------------------------------------
El gate de arriba corre ANTES de generar y le cede el turno al modelo apenas algo
puntúa por encima de CHATBOT_VACIO_SCORE_EXISTE: si algo responde de verdad, que
conteste. Esa apuesta sale mal más seguido de lo que parece. Medido sobre el
tráfico de agosto (sin el smoke test), **89 de los 382 "no encontré" de voltara
—el 23%— salieron con el mejor fragmento por encima de 3,0**, o sea sobre
material que el propio sistema da por bueno; en benefix fueron 12 de 45 y en
csv_isla_de_productos 8 de 15. El caso que lo destapó es la consulta de una palabra:
"Medidor" recupera Tapa de Medidor (3,59), Medidor Quemado (3,51), Traslado
(2,47), Inspección de Funcionamiento (2,20) e Inversión (2,07) —cinco temas
documentados, todos sobre medidores— y como el primero pasa 3,0 el gate no toca
nada... y el modelo contesta "no encontré información sobre ese tema". El
operador se queda sin nada teniendo el manual entero atrás.

Por eso hay una segunda oportunidad DESPUÉS de generar: si la respuesta terminó
siendo la frase de negación sola (ver `retener`) y el score decía que el tema
existe, se cambia por la lista de temas. Es el mismo remedio, aplicado donde
antes no se podía saber: recién con la respuesta en la mano se sabe que el modelo
no la iba a usar.

Y los vacíos NO se pierden, que es lo que hace que este camino sea seguro. Acá
—a diferencia del gate previo— el modelo sí se negó, así que la fila se sigue
marcando `sin_cobertura` (chatBot.py le pasa al log la negativa que el modelo
escribió, no la lista que se mostró) y el clasificador diferido decide leyendo el
contexto servido, que es mejor criterio que cualquier umbral de score. La cuenta
que lo justifica: de las negativas con score >= 3,0 que el clasificador alcanzó a
leer, **60 de 100 resultaron huecos de documentación de verdad**. Taparlas habría
sido cambiar un problema del operador por uno de Calidad.
"""
import re
import unicodedata
from typing import List, Optional, Sequence

from app.config import settings

# Marcadores del bloque de opciones. Son comentarios HTML a propósito: el frontend
# los usa para armar los botones, el backend para reconocer la respuesta, y si algo
# falla el operador ve una lista markdown común (los comentarios no se renderizan).
INICIO = "<!--opciones-->"
FIN = "<!--/opciones-->"

ENCABEZADO = (
    "Encontré varios temas que pueden ser el tuyo, pero necesito que me digas cuál. "
    "¿Sobre cuál de estos necesitás información?"
)
PIE = "Si no es ninguno, escribime el tema con un poco más de detalle."

# Con una sola opción el texto de arriba miente ("varios temas") y suena a que el
# bot no entendió. Pasa solo por `proponer_tras_negativa`: ahí el modelo YA se negó,
# así que ofrecer el único tema fuerte que había es mejor que la negación —"Medidor
# monofasico" recupera Traslado de Medidor (Monofásico/Trifásico) en 4,70 y nada más,
# y esa negación es puro desperdicio.
ENCABEZADO_UNICO = (
    "Lo más parecido que tengo documentado es esto. ¿Es lo que estabas buscando?"
)
PIE_UNICO = "Si no es eso, escribime el tema con un poco más de detalle."

# Arranques de la frase de "no encontré", para reconocerla MIENTRAS se genera (ver
# `retener`). Son PREFIJOS normalizados, no el regex de app/vacios_conocimiento.py
# (_PATRONES_SIN_COBERTURA): aquel busca la negación en una respuesta ya terminada y
# puede darse el lujo de ser amplio; acá hay que decidir con las primeras letras si
# se frena el stream, así que la lista es corta y arranca siempre en "no ". Si allá
# se agrega un patrón que empiece distinto, este no lo hereda (y no pasa nada: como
# mucho se pierde un reemplazo, nunca se rompe una respuesta).
ARRANQUES_NEGATIVA = (
    "no encontre",
    "no tengo informacion",
    "no cuento con",
    "no dispongo",
    "no hay informacion",
    "no puedo responder",
    "no se encuentra",
)

# Largo máximo de una opción. Es un tope de seguridad, no el comportamiento normal:
# con el corpus de Voltara (232 encabezados, el más largo de 81 caracteres) no llega a
# recortar. Importa que casi nunca corte porque la etiqueta es TAMBIÉN la consulta que
# se manda cuando el operador la elige, así que un recorte se paga dos veces: se lee
# mal y busca peor. Cuando corta, corta en un espacio y lo marca con "…".
#
# Estaba en 80 y con el prefijo de la sección padre se pasaban casi todas: 26 de las 48
# opciones distintas que se eligieron en el tráfico real de agosto llegaron cortadas a
# mitad de palabra ("... › ¿Cuándo no vamos a generar").
LARGO_MAX_ETIQUETA = 120

# Por debajo de este largo un título no se explica solo ("Carga en Sistema",
# "Definición y Medios", "Datos A Solicitar") y se le antepone la sección que lo
# contiene. Por encima ya dice de qué se trata y el prefijo solo agrega ruido — que era
# justamente lo que hacía largas a todas las opciones.
LARGO_MIN_SIN_CONTEXTO = 25

_RE_ENCABEZADO_MD = re.compile(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$")


def activo(slug: Optional[str]) -> bool:
    """¿Este bot desambigua? Se prende por slug (CHATBOT_DESAMBIGUACION_SLUGS)."""
    if not slug:
        return False
    return slug.strip().lower() in {
        s.strip().lower() for s in settings.CHATBOT_DESAMBIGUACION_SLUGS
    }


def es_desambiguacion(texto: Optional[str]) -> bool:
    """¿Esta respuesta es una lista de opciones nuestra (y no una respuesta del LLM)?"""
    return bool(texto) and INICIO in texto


def retener(acumulado: str) -> bool:
    """¿Lo que va generado todavía puede terminar siendo la frase de "no encontré"?

    Es el freno del stream: mientras devuelva True, el texto NO se le muestra al
    operador, porque todavía se puede reemplazar por la lista de temas
    (`proponer_tras_negativa`). Apenas devuelve False se suelta lo acumulado de una
    y sigue el streaming normal.

    La clave es que una respuesta común no pague nada: arranca con 🖥️, con un
    título o con la respuesta directa, así que a la primera palabra ya se soltó. Lo
    que se retiene es el puñado de tokens de una negación, que además es lo único
    que no se pierde nada por mostrar tarde.

    El tope de largo es lo que separa la negación pura de la muletilla ("No
    encontré... No obstante, la documentación menciona..."): pasado ese punto la
    respuesta trae contenido y se suelta, aunque haya arrancado negando. Es la misma
    distinción que hace `_contesta_igual` en app/vacios_conocimiento.py, con un
    número más estricto (ver CHATBOT_DESAMB_NEGATIVA_MAX_CHARS).
    """
    if len(acumulado) > settings.CHATBOT_DESAMB_NEGATIVA_MAX_CHARS:
        return False
    # El emoji/negrita del arranque no dice nada todavía: se saca y se compara la
    # primera palabra de verdad. Si todavía no llegó ninguna letra, se sigue reteniendo.
    texto = re.sub(r"^[^a-z]+", "", _normalizar(acumulado))
    if not texto:
        return True
    return any(a.startswith(texto) or texto.startswith(a) for a in ARRANQUES_NEGATIVA)


# ------------------------------------------------------------------ etiquetas

def _normalizar(texto: str) -> str:
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", sin_tildes).strip().lower()


def _ruta(nodo) -> List[str]:
    """Ruta de encabezados del fragmento: ["Manual Voltara", "Reclamos", "Poda"].

    `header_path` lo deja el MarkdownNodeParser del indexador y trae SOLO los
    ancestros; el encabezado propio del fragmento es la primera línea del texto.
    Desde 2026-09-14 el indexador repite el encabezado de la sección en cada pedazo
    en que el SentenceSplitter parte una sección larga (ver
    chatbot_indexer._nodos_desde_documentos). Los índices anteriores no lo tienen:
    ahí los pedazos que no son el primero devuelven la ruta del padre, y eso lo
    resuelve `_misma_rama`, no acá.
    """
    metadata = getattr(nodo, "metadata", None) or {}
    partes = _partir_ruta(metadata.get("header_path") or "")

    primera_linea = (nodo.get_content() or "").lstrip().split("\n", 1)[0]
    encabezado = _RE_ENCABEZADO_MD.match(primera_linea)
    propio = encabezado.group(1).strip() if encabezado else ""
    if propio and (not partes or _normalizar(partes[-1]) != _normalizar(propio)):
        partes.append(propio)
    return partes


def _partir_ruta(header_path: str) -> List[str]:
    """Parte el `header_path` del indexador en los encabezados que lo componen.

    El separador es "/" y varios títulos del manual LO TIENEN adentro: "Tapa de
    Medidor (Cambio / Instalación)", "Traslado de Medidor (Monofásico/Trifásico)",
    "Cambio de Ramales (Monofásicos/Trifásicos)". El split los partía en dos, y como
    el pedazo de atrás quedaba de sección padre, la opción que veía el operador era
    "Instalación) › Situaciones" — y eso mismo era lo que se buscaba al elegirla.

    El paréntesis abierto y sin cerrar delata el corte, así que se vuelve a pegar.
    Una barra FUERA de paréntesis ("FOTO / ESTADO DEL MEDIDOR") no se distingue de
    dos niveles reales y queda partida; solo afecta al prefijo de contexto, nunca al
    título propio del fragmento, que sale de la primera línea del texto y llega
    entero.
    """
    partes: List[str] = []
    for cruda in (header_path or "").split("/"):
        pedazo = cruda.strip()
        if not pedazo:
            continue
        if partes and partes[-1].count("(") > partes[-1].count(")"):
            partes[-1] = f"{partes[-1]} / {pedazo}"
        else:
            partes.append(pedazo)
    return partes


def _misma_rama(a: Sequence[str], b: Sequence[str]) -> bool:
    """¿Dos rutas son el mismo tema? Sí cuando una es ancestro de la otra.

    Es lo que evita que los pedazos de UNA sección larga se cuenten como temas
    distintos ("Reclamos" y "Reclamos › Poda" son el mismo tema) e inventen una
    ambigüedad que no existe — que es justo lo que taparía un vacío real.
    """
    corta, larga = (a, b) if len(a) <= len(b) else (b, a)
    return all(_normalizar(x) == _normalizar(y) for x, y in zip(corta, larga))


def _recortar(etiqueta: str) -> str:
    """Tope de largo, siempre en un espacio: nunca a mitad de palabra."""
    if len(etiqueta) <= LARGO_MAX_ETIQUETA:
        return etiqueta
    corte = etiqueta.rfind(" ", 0, LARGO_MAX_ETIQUETA)
    recortada = etiqueta[:corte] if corte > 0 else etiqueta[:LARGO_MAX_ETIQUETA]
    return recortada.rstrip(" ,;:.-") + "…"


def _etiqueta(ruta: Sequence[str]) -> str:
    """Lo que ve el operador — y lo que se busca si la elige: el título de la sección.

    Se muestra el título SOLO, sin la rama que lo contiene. La ruta completa era fiel a
    la estructura del manual pero le sumaba ~45 caracteres de prefijo a cada opción
    ("Medidores: Problemas Técnicos, Inspecciones y Delitos › Inspección de Funcionami")
    para no decir nada que el título no dijera ya, y encima empujaba el recorte.

    La sección padre se agrega solo cuando el título es demasiado corto para valerse
    solo: ahí sí es lo único que distingue un "Carga en Sistema" de otro.

    El H1 del documento ("Guía de Procedimientos VOLTARA") no cuenta como sección
    padre: es el mismo para todas las opciones del archivo, así que no distingue
    nada y solo suma 30 caracteres a la etiqueta —y a la consulta que se manda al
    elegirla—. Por eso el prefijo pide un ancestro de verdad (ruta de 3 o más).
    """
    hoja = (ruta[-1] or "").strip()
    if len(ruta) >= 3 and len(hoja) < LARGO_MIN_SIN_CONTEXTO:
        hoja = f"{(ruta[-2] or '').strip()} › {hoja}"
    return _recortar(hoja)


# ---------------------------------------------------------------------- gate

def _ya_ofrecimos(historial) -> bool:
    """¿El último turno del bot ya fue una lista de opciones?

    Sin esto, un operador que elige mal (o escribe algo igual de vago) se queda en
    un bucle de listas. A la segunda se responde como siempre, aunque sea con un
    "no encontré" — que además es la respuesta correcta para que Calidad lo vea.
    """
    for mensaje in reversed(list(historial or [])):
        rol = getattr(mensaje, "role", "")
        rol = str(getattr(rol, "value", rol)).lower()
        if rol == "assistant":
            return es_desambiguacion(mensaje.content or "")
    return False


def proponer(nodos, historial=None) -> List[str]:
    """Temas para ofrecerle al operador, o [] si esta consulta no se desambigua.

    Devolver [] es el caso normal y quiere decir "seguí de largo": el bot genera
    la respuesta como siempre.
    """
    if _ya_ofrecimos(historial):
        return []

    scores = [n.score for n in (nodos or []) if n.score is not None]
    if not scores:
        return []

    mejor = max(scores)
    # Algo responde de verdad -> que conteste.
    if mejor >= settings.CHATBOT_VACIO_SCORE_EXISTE:
        return []
    # Nada de lo recuperado tiene que ver -> es un vacío de conocimiento, no una
    # ambigüedad. Se deja pasar para que el bot diga "no encontré" y se registre.
    if mejor < settings.CHATBOT_DESAMB_SCORE_MIN:
        return []

    temas = _temas(nodos)
    if len(temas) < 2:
        return []

    # Que peleen cabeza a cabeza. Si el segundo está lejos, el primero no compite
    # con nadie: la pregunta no era ambigua y la respuesta correcta es la de
    # siempre (posiblemente un "no encontré", que es lo que Calidad necesita ver).
    margen = (
        settings.CHATBOT_VACIO_SCORE_EXISTE - settings.CHATBOT_DESAMB_SCORE_MIN
    ) * settings.CHATBOT_DESAMB_EMPATE_FRACCION
    if temas[1]["score"] < temas[0]["score"] - margen:
        return []

    etiquetas = _etiquetas(temas)
    return etiquetas if len(etiquetas) >= 2 else []


def proponer_tras_negativa(nodos, historial=None) -> List[str]:
    """Temas para ofrecer cuando el modelo ya contestó "no encontré" igual.

    Corre DESPUÉS de generar, sobre los mismos nodos, y solo cuando la respuesta
    terminó siendo la negación sola. Acá el gate es al revés que en `proponer`: se
    exige que el score sea ALTO (>= CHATBOT_VACIO_SCORE_EXISTE), porque lo que
    habilita el reemplazo es la contradicción — el sistema dice que el tema está
    documentado y el modelo dice que no lo encontró. Por debajo de ese umbral no se
    toca nada: ahí `proponer` ya tuvo su oportunidad antes de generar.

    Lo que se muestra cambia; lo que se registra, no. La fila se sigue marcando como
    sin cobertura sobre la negativa del modelo (ver el final del docstring del
    módulo), así que un hueco real llega igual a Calidad aunque el operador haya
    visto una lista de temas.

    Alcanza con UN tema, a diferencia de `proponer`. Allá un tema solo significaba
    "la pregunta se entendió y falta el dato"; acá el modelo ya se negó sobre
    material que puntúa alto, así que el único tema fuerte que había es justamente
    lo que el operador estaba buscando y nadie le mostró.
    """
    if _ya_ofrecimos(historial):
        return []

    scores = [n.score for n in (nodos or []) if n.score is not None]
    if not scores or max(scores) < settings.CHATBOT_VACIO_SCORE_EXISTE:
        return []

    return _etiquetas(_temas(nodos))


def _temas(nodos) -> List[dict]:
    """Un tema por rama de encabezados, con el mejor score de sus fragmentos, de
    mayor a menor. Se descarta lo que puntúa por debajo del piso: eso no tiene nada
    que ver con la consulta y ofrecerlo sería peor que no ofrecer nada."""
    temas: List[dict] = []
    for nodo in sorted(
        [n for n in nodos if n.score is not None and n.score >= settings.CHATBOT_DESAMB_SCORE_MIN],
        key=lambda n: n.score,
        reverse=True,
    ):
        ruta = _ruta(nodo)
        if not ruta:
            continue
        for tema in temas:
            if _misma_rama(tema["ruta"], ruta):
                # Se queda la ruta más específica; el score ya es el mayor (venimos
                # ordenados de mayor a menor).
                if len(ruta) > len(tema["ruta"]):
                    tema["ruta"] = ruta
                break
        else:
            temas.append({"ruta": ruta, "score": nodo.score})
    return temas


def _etiquetas(temas: Sequence[dict]) -> List[str]:
    """Las etiquetas de los mejores temas, sin repetir (dos ramas distintas pueden
    terminar en el mismo título)."""
    etiquetas: List[str] = []
    vistas = set()
    for tema in temas[: settings.CHATBOT_DESAMB_MAX_OPCIONES]:
        etiqueta = _etiqueta(tema["ruta"])
        if etiqueta and _normalizar(etiqueta) not in vistas:
            vistas.add(_normalizar(etiqueta))
            etiquetas.append(etiqueta)
    return etiquetas


def render(opciones: Sequence[str]) -> str:
    """El texto que se le manda al operador. Markdown legible por sí solo."""
    lineas = "\n".join(f"- **{opcion}**" for opcion in opciones)
    encabezado, pie = (
        (ENCABEZADO_UNICO, PIE_UNICO) if len(opciones) == 1 else (ENCABEZADO, PIE)
    )
    return f"{encabezado}\n\n{INICIO}\n{lineas}\n{FIN}\n\n{pie}"
