"""Tablas de datos de los chatbots: conocimiento que NO es prosa y no debe pasar
por búsqueda vectorial.

EL PROBLEMA
-----------
Parte del material que carga Calidad no es un procedimiento: es una tabla de
entidad -> atributos, y la pregunta del operador es un lookup o un filtro sobre
esa tabla. Dos casos reales:

  - vantix: las bases/talleres de instalación. 48 de las 113 consultas históricas
    del bot (42%) preguntan por una sucursal, su dirección, su mail o qué
    vehículos acepta. Son las que peor puntúan ("sucursal de palermo" -8,43,
    "sucursal boedo" -6,51, "en que sucursal entran motos" -2,22) y las más
    caras (2.451 tokens de entrada contra 1.639 del resto).

  - benefix: la cartera de cobranzas, ~2.500 filas de "razón social / nº cliente
    -> gestor" repartidas en 10 documentos y 217k caracteres. En 197 consultas
    el retriever trajo un documento de cartera 3 veces.

La causa es la misma: MarkdownNodeParser + SentenceSplitter parten la tabla y
las filas quedan sin encabezado, el nombre de la entidad no está en el título de
la sección (que es lo que domina la señal recuperable) y el reranker castiga el
contenido de referencia. Ninguna de esas tres cosas se arregla tuneando el
retriever, porque la pregunta no es semántica: "¿quién gestiona a ALFA - RED SA?"
tiene UNA respuesta exacta en UNA fila.

LO QUE HACE
-----------
Una tabla es una fuente paralela a ChatbotDocMarkdown: vive en SQL
(pagina_web.ChatbotTabla + ChatbotTablaFila), NO se indexa en Qdrant y se
consulta antes del retrieval. Si la consulta no rutea a ninguna tabla, el bot
responde por el camino de siempre y no cambia nada.

Hay dos modos, y que existan los dos es lo que hace que el mismo diseño sirva
para los dos casos de arriba:

  completa  La tabla ENTERA entra en el prompt, sin búsqueda. Es lo correcto
            cuando entra: con las ~40 bases de vantix cuesta lo mismo que hoy
            pero con recall 100%, resuelve filtros de varias columnas ("moto en
            zona norte") y deja que el modelo razone geografía ("un cliente de
            Benavídez" -> la base de Tigre), que es justo lo que ningún
            retriever puede hacer, porque ese dato no está escrito en ningún
            lado.

  lookup    Se buscan las filas por sus columnas clave y solo esas van al
            prompt. Es lo único que escala a 2.500 filas: de 3.606 tokens a
            ~150, y sin depender de qué rango alfabético cayó en el chunk.

'auto' (el default) elige por tamaño, así una tabla que hoy entra entera y
mañana crece pasa sola a lookup sin que nadie tenga que acordarse.

CÓMO SE DECIDE QUE UNA CONSULTA VA A UNA TABLA
----------------------------------------------
Tres señales, ninguna de las cuales cuesta una llamada a un modelo:

  1. CLAVE. La consulta contiene el valor de una columna clave ("ALFA - RED SA",
     "33815.1", "daytona tigre"). Es la señal más fuerte: no hay ambigüedad
     posible, el dato está literalmente escrito.
  2. TÉRMINOS. La consulta usa alguna de las palabras declaradas de la tabla
     ("sucursal", "taller", "gestor de cobranzas"). Las propone la IA al crear
     la tabla y Calidad las edita.
  3. DESCRIPCIÓN. Similitud entre el embedding de la consulta —que stream_query
     YA calculó para el retrieval, así que sale gratis— y el de la descripción
     de la tabla. Es la red para la consulta que no usa ninguna palabra
     declarada. Si la tabla no tiene vector guardado, esta señal simplemente no
     participa.

En modo lookup, una consulta que rutea por términos/descripción pero SIN clave
no puede buscar nada. En vez de devolver 2.500 filas o mandarla al RAG (donde el
documento ya no está, porque se convirtió en tabla), se responde con el resumen
de la tabla: cuántas filas tiene y los valores distintos de las columnas de baja
cardinalidad. Eso alcanza para "¿qué gestores de cobranza hay?" y para que el
bot pida el dato que le falta ("necesito la razón social o el número de
cliente") en lugar de decir que no sabe.

POR QUÉ NO SE CACHEA UNA RESPUESTA DE TABLA
-------------------------------------------
El caché semántico compara embeddings con umbral 0.95. "el gestor del cliente
33815.1" y "el gestor del cliente 33816.1" son dos consultas distintas con
respuestas distintas, y su similitud está muy por encima de ese umbral: cachear
esto sería servir el gestor equivocado con total seguridad. Ver
`stream_query`, que apaga el caché cuando la consulta rutea a una tabla.
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy import text

from app.config import settings

logger = logging.getLogger(__name__)


# --------------------------------------------------------------- normalización

# Un token es una corrida alfanumérica, que puede llevar adentro . - / cuando
# separan alfanuméricos: así "33815.1" queda ENTERO (es la clave Cl2 de benefix)
# y no se parte en "33815" y "1". Las partes se indexan además por separado.
_RE_TOKEN = re.compile(r"[a-z0-9]+(?:[./-][a-z0-9]+)*")

# Palabras que no aportan a la identificación de una fila. Sin esto, "el cliente
# de la razón social X" puntúa contra cualquier fila que tenga "cliente" en el
# nombre.
_STOPWORDS = frozenset("""
a al algo alguna algunas alguno algunos ante antes como con contra cual cuales cuando
cuanto de del desde donde dos e el ella ellas ellos en entre era eran es esa esas ese
eso esos esta estan estas este esto estos ha hace hacer hasta hay la las le les lo los
mas me mi mis mucho muy ni no nos nuestra nuestro o os otra otras otro otros para pero
poco por porque que quien quienes se ser si sin sobre solo son su sus tambien tan tanto
te tiene tienen todo todos tu tus un una uno unos y ya
necesito dame decime pasame quiero saber consulta consultar buscar busco cual es cuales
son informacion dato datos favor gracias hola
""".split())


def normalizar(texto: Optional[str]) -> str:
    """Minúsculas, sin acentos, espacios colapsados. Es la forma canónica con la
    que se comparan consultas, claves y términos."""
    if not texto:
        return ""
    sin_acentos = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode("ascii")
    return " ".join(sin_acentos.lower().split())


def tokenizar(texto: Optional[str]) -> List[str]:
    """Tokens normalizados de un texto, con las partes de los compuestos.

    "ALFA - RED S.A. (33815.1)" -> one, net, s.a., s, a, 33815.1, 33815, 1
    Las partes se agregan para que "33815" sola también encuentre la fila cuyo
    Cl2 es "33815.1"."""
    base = normalizar(texto)
    tokens: List[str] = []
    for bruto in _RE_TOKEN.findall(base):
        tokens.append(bruto)
        if any(sep in bruto for sep in "./-"):
            tokens.extend(p for p in re.split(r"[./-]", bruto) if p)
    return tokens


def _tokens_utiles(texto: Optional[str]) -> List[str]:
    """Tokens de una CONSULTA, sin las palabras de relleno."""
    return [t for t in tokenizar(texto) if t not in _STOPWORDS]


# Tolerancia de plural para los TÉRMINOS declarados (no para las claves, que son
# nombres propios y se comparan exactos). Sin esto, un término "gestor" no
# reconoce "¿qué gestores hay?" y "sucursal" no reconoce "sucursales zona sur",
# que son consultas textuales del log. No es un stemmer: alcanza con permitir un
# sufijo corto sobre una raíz de largo razonable ("base"/"bases",
# "taller"/"talleres"), que no confunde palabras distintas.
_MIN_CHARS_RAIZ = 4
_MAX_SUFIJO = 3


def _misma_raiz(a: str, b: str) -> bool:
    if a == b:
        return True
    corto, largo = (a, b) if len(a) <= len(b) else (b, a)
    return (
        len(corto) >= _MIN_CHARS_RAIZ
        and largo.startswith(corto)
        and len(largo) - len(corto) <= _MAX_SUFIJO
    )


# ------------------------------------------------------------------- modelo

@dataclass(frozen=True)
class Columna:
    nombre: str
    descripcion: str = ""
    clave: bool = False


@dataclass
class Fila:
    orden: int
    datos: Dict[str, str]
    # Texto normalizado de las columnas clave (viene precomputado de la BD).
    busqueda: str = ""
    _tokens: frozenset = field(default_factory=frozenset, repr=False)
    # Valores de clave normalizados y largos, para el match por contención.
    _valores: Tuple[str, ...] = field(default=(), repr=False)

    def texto(self, columnas: Sequence[Columna]) -> str:
        """La fila como una línea legible para el modelo."""
        partes = []
        for col in columnas:
            valor = (self.datos.get(col.nombre) or "").strip()
            if valor:
                partes.append(f"{col.nombre}: {valor}")
        return " | ".join(partes)


@dataclass
class Tabla:
    id: int
    chatbot_id: int
    nombre: str
    descripcion: str
    terminos: List[str]
    columnas: List[Columna]
    nota: str
    modo_declarado: str
    filas: List[Fila]
    vector: Optional[List[float]] = None
    # Calculados al cargar (una vez por refresco, no por consulta).
    _idf: Dict[str, float] = field(default_factory=dict, repr=False)
    _frases: List[str] = field(default_factory=list, repr=False)

    # ---------------------------------------------------------------- modo

    @property
    def columnas_clave(self) -> List[Columna]:
        claves = [c for c in self.columnas if c.clave]
        # Sin claves declaradas, la primera columna oficia de identificador: una
        # tabla sin forma de nombrar a sus filas no se puede consultar.
        return claves or self.columnas[:1]

    @property
    def modo(self) -> str:
        """'completa' o 'lookup'. Resuelve 'auto' por tamaño."""
        if self.modo_declarado in ("completa", "lookup"):
            return self.modo_declarado
        return "completa" if self._chars_total() <= settings.CHATBOT_TABLA_MAX_CHARS_COMPLETA else "lookup"

    def _chars_total(self) -> int:
        return sum(len(f.busqueda) + sum(len(v or "") for v in f.datos.values()) for f in self.filas)

    # ------------------------------------------------------------ búsqueda

    def preparar(self) -> None:
        """Precalcula lo que se reutiliza en cada consulta: tokens por fila, IDF
        y las frases de clave lo bastante largas como para buscarlas por
        contención. Corre UNA vez por refresco de la tabla."""
        df: Dict[str, int] = {}
        for fila in self.filas:
            tokens = frozenset(tokenizar(fila.busqueda))
            fila._tokens = tokens
            valores = tuple(
                v for v in (
                    normalizar(fila.datos.get(c.nombre)) for c in self.columnas_clave
                ) if len(v) >= settings.CHATBOT_TABLA_MIN_CHARS_FRASE
            )
            fila._valores = valores
            for t in tokens:
                df[t] = df.get(t, 0) + 1

        total = max(1, len(self.filas))
        # IDF clásico: un token que está en casi todas las filas ("sa", "srl",
        # "curcio") pesa ~0; uno que está en una sola pesa mucho. Sin esto, "S.A."
        # empata con la razón social entera.
        self._idf = {t: math.log(1.0 + total / n) for t, n in df.items()}
        self._frases = sorted({v for f in self.filas for v in f._valores}, key=len, reverse=True)

    def buscar(self, consulta: str) -> List[Fila]:
        """Filas que la consulta identifica, de la más probable a la menos.

        Devuelve vacío cuando nada matchea con confianza: en modo lookup eso es
        justamente lo que hace que la consulta NO rutee a esta tabla."""
        if not self.filas:
            return []

        consulta_norm = normalizar(consulta)
        tokens = set(_tokens_utiles(consulta))
        if not tokens and not consulta_norm:
            return []

        puntajes: List[Tuple[float, Fila]] = []
        for fila in self.filas:
            score = sum(self._idf.get(t, 0.0) for t in tokens if t in fila._tokens)
            # El valor completo de una clave escrito dentro de la consulta es la
            # evidencia más fuerte que hay ("...de la razon social ALFA - RED SA"):
            # vale más que la suma de sus tokens sueltos, que podrían venir de
            # filas distintas.
            for valor in fila._valores:
                if valor and valor in consulta_norm:
                    score += settings.CHATBOT_TABLA_BONUS_FRASE * (1 + len(valor.split()))
            if score > 0:
                puntajes.append((score, fila))

        if not puntajes:
            return []

        puntajes.sort(key=lambda p: (-p[0], p[1].orden))
        mejor = puntajes[0][0]
        if mejor < settings.CHATBOT_TABLA_SCORE_MIN:
            return []
        # Se conservan los empates cerca del mejor: dos personas que se llaman
        # Solange, o una razón social con varias subcuentas, tienen que llegar
        # las dos al modelo para que responda por las dos en vez de elegir una.
        corte = mejor * settings.CHATBOT_TABLA_EMPATE_FRACCION
        return [f for s, f in puntajes if s >= corte][: settings.CHATBOT_TABLA_MAX_FILAS]

    # -------------------------------------------------------------- ruteo

    def menciona_termino(self, consulta_norm: str) -> bool:
        """¿La consulta usa alguna de las palabras declaradas de esta tabla?

        Compara por palabra completa (no substring): "base" no tiene que
        disparar dentro de "basedatos". Un término de varias palabras ("gestor de
        cobranzas") se busca tal cual, y uno de una sola tolera el plural (ver
        _misma_raiz)."""
        tokens = set(tokenizar(consulta_norm))
        for termino in self.terminos:
            if not termino:
                continue
            if " " in termino:
                if termino in consulta_norm:
                    return True
            elif any(_misma_raiz(t, termino) for t in tokens):
                return True
        return False

    def similitud(self, embedding: Optional[Sequence[float]]) -> float:
        """Coseno entre la consulta y la descripción de la tabla. 0 si la tabla
        no tiene vector guardado (o quedó de otro modelo de embeddings)."""
        if not embedding or not self.vector or len(self.vector) != len(embedding):
            return 0.0
        num = sum(a * b for a, b in zip(self.vector, embedding))
        na = math.sqrt(sum(a * a for a in self.vector))
        nb = math.sqrt(sum(b * b for b in embedding))
        if na <= 0 or nb <= 0:
            return 0.0
        return num / (na * nb)

    # ----------------------------------------------------------- contexto

    def _cabecera(self) -> str:
        cols = ", ".join(
            c.nombre + (f" ({c.descripcion})" if c.descripcion else "") for c in self.columnas
        )
        partes = [f"TABLA DE DATOS: {self.nombre}", f"Para qué sirve: {self.descripcion}",
                  f"Columnas: {cols}", f"Filas totales: {len(self.filas)}"]
        if self.nota:
            partes.append(f"CÓMO USARLA (obligatorio): {self.nota}")
        return "\n".join(partes)

    def como_markdown(self, filas: Optional[Sequence[Fila]] = None) -> str:
        """La tabla (o un subconjunto de filas) como tabla markdown."""
        filas = list(filas if filas is not None else self.filas)
        encabezado = "| " + " | ".join(c.nombre for c in self.columnas) + " |"
        separador = "| " + " | ".join("---" for _ in self.columnas) + " |"
        cuerpo = [
            "| " + " | ".join((f.datos.get(c.nombre) or "").replace("|", "\\|").strip()
                              for c in self.columnas) + " |"
            for f in filas
        ]
        return "\n".join([encabezado, separador] + cuerpo)

    def resumen(self) -> str:
        """Qué hay en la tabla, sin volcarla: valores distintos de las columnas de
        baja cardinalidad. Es la respuesta a "¿qué gestores hay?" sin mandar
        2.500 filas, y le da al modelo con qué pedir el dato que le falta."""
        lineas = []
        claves = {c.nombre for c in self.columnas_clave}
        for col in self.columnas:
            # Las columnas clave quedan afuera SIEMPRE, sin mirar cardinalidad: son
            # una por fila (una razón social, un Cl2), así que listarlas es volcar
            # la tabla por la ventana, que es justo lo que este resumen evita.
            if col.nombre in claves:
                continue
            valores = {(f.datos.get(col.nombre) or "").strip() for f in self.filas}
            valores.discard("")
            if 0 < len(valores) <= settings.CHATBOT_TABLA_RESUMEN_MAX_VALORES:
                lineas.append(f"- {col.nombre}: {', '.join(sorted(valores))}")
        if not lineas:
            return ""
        return "Valores posibles de las columnas con pocas opciones:\n" + "\n".join(lineas)


# ------------------------------------------------------------------- resultado

@dataclass
class Consulta:
    """Lo que el ruteo decidió para una consulta."""
    tabla: Tabla
    filas: List[Fila]
    # 'completa' | 'lookup' | 'resumen' (rutéo sin clave sobre una tabla grande)
    forma: str
    motivo: str

    def contexto(self) -> str:
        """El bloque de datos que se le pasa al modelo."""
        partes = [self.tabla._cabecera(), ""]
        if self.forma == "resumen":
            partes.append(
                "No se pudo identificar ninguna fila con lo que dijo el operador: la consulta "
                "no trae ningún valor de las columnas que identifican una fila "
                f"({', '.join(c.nombre for c in self.tabla.columnas_clave)})."
            )
            resumen = self.tabla.resumen()
            if resumen:
                partes += ["", resumen]
        else:
            if self.forma == "lookup":
                partes.append(
                    f"Filas que coinciden con la consulta ({len(self.filas)} de "
                    f"{len(self.tabla.filas)}):"
                )
            else:
                partes.append("Contenido completo de la tabla:")
            partes += ["", self.tabla.como_markdown(self.filas)]
        return "\n".join(partes)

    def para_log(self) -> str:
        """Lo que queda guardado en query_chatbots_logs.context.

        Se guarda el contexto REAL (igual que el camino RAG guarda los chunks):
        es lo único que después permite entender por qué el bot respondió lo que
        respondió."""
        return (
            f"[tabla:{self.tabla.nombre}] forma={self.forma} motivo={self.motivo} "
            f"filas={len(self.filas)}\n\n{self.contexto()}"
        )


# ------------------------------------------------------------------ carga

_CACHE: Dict[int, Tuple[float, str, List[Tabla]]] = {}
_CACHE_LOCK = threading.Lock()

_QUERY_TABLAS = text("""
    SELECT id, chatbot_id, nombre, descripcion, terminos, columnas, claves, nota,
           modo, descripcion_vector, vector_modelo
    FROM pagina_web.ChatbotTabla
    WHERE chatbot_id = :cid AND activo = 1
    ORDER BY id
""")

_QUERY_FILAS = text("""
    SELECT tabla_id, orden, datos, busqueda
    FROM pagina_web.ChatbotTablaFila
    WHERE tabla_id IN (SELECT id FROM pagina_web.ChatbotTabla
                       WHERE chatbot_id = :cid AND activo = 1)
    ORDER BY tabla_id, orden
""")

# Sello barato para saber si algo cambió sin traerse las filas: si la firma es la
# misma que la cacheada, se reusa la carga anterior. Una tabla editada mueve
# updated_at; una fila agregada/borrada mueve la cuenta.
_QUERY_FIRMA = text("""
    SELECT COUNT(*) AS tablas,
           CONVERT(VARCHAR(30), MAX(t.updated_at), 126) AS ultima,
           SUM(CAST(t.filas_total AS BIGINT)) AS filas
    FROM pagina_web.ChatbotTabla t
    WHERE t.chatbot_id = :cid AND t.activo = 1
""")


def _get_engine():
    from app.database import engine
    return engine


def _parsear_columnas(crudo: Optional[str], claves: Optional[str]) -> List[Columna]:
    try:
        datos = json.loads(crudo or "[]")
    except (TypeError, ValueError):
        logger.warning("Columnas de tabla con JSON inválido; se ignora la tabla.")
        return []
    nombres_clave = {normalizar(c) for c in (claves or "").split(",") if c.strip()}
    columnas = []
    for item in datos:
        if isinstance(item, str):
            columnas.append(Columna(nombre=item.strip(),
                                    clave=normalizar(item) in nombres_clave))
        elif isinstance(item, dict) and (item.get("nombre") or "").strip():
            nombre = str(item["nombre"]).strip()
            columnas.append(Columna(
                nombre=nombre,
                descripcion=str(item.get("descripcion") or "").strip(),
                # La marca de clave puede venir por la columna `claves` de la
                # tabla o por el propio JSON; cualquiera de las dos alcanza.
                clave=bool(item.get("clave")) or normalizar(nombre) in nombres_clave,
            ))
    return columnas


def _parsear_vector(crudo: Optional[str], modelo: Optional[str]) -> Optional[List[float]]:
    """El vector solo se usa si lo calculó el modelo de embeddings VIGENTE: uno
    viejo daría similitudes sin sentido contra el embedding de la consulta."""
    if not crudo or (modelo or "") != settings.DEFAULT_REMOTE_EMBED_MODEL:
        return None
    try:
        vector = json.loads(crudo)
        return [float(x) for x in vector] if isinstance(vector, list) and vector else None
    except (TypeError, ValueError):
        return None


def _cargar(chatbot_id: int) -> List[Tabla]:
    engine = _get_engine()
    with engine.connect() as conn:
        filas_tabla = conn.execute(_QUERY_TABLAS, {"cid": chatbot_id}).mappings().all()
        if not filas_tabla:
            return []
        filas_datos = conn.execute(_QUERY_FILAS, {"cid": chatbot_id}).mappings().all()

    por_tabla: Dict[int, List[Fila]] = {}
    for fila in filas_datos:
        try:
            datos = json.loads(fila["datos"])
        except (TypeError, ValueError):
            continue
        if not isinstance(datos, dict):
            continue
        por_tabla.setdefault(fila["tabla_id"], []).append(Fila(
            orden=fila["orden"],
            datos={str(k): ("" if v is None else str(v)) for k, v in datos.items()},
            busqueda=fila["busqueda"] or "",
        ))

    tablas = []
    for row in filas_tabla:
        columnas = _parsear_columnas(row["columnas"], row["claves"])
        if not columnas:
            continue
        tabla = Tabla(
            id=row["id"],
            chatbot_id=row["chatbot_id"],
            nombre=row["nombre"],
            descripcion=row["descripcion"] or "",
            terminos=[normalizar(t) for t in (row["terminos"] or "").split(",") if t.strip()],
            columnas=columnas,
            nota=row["nota"] or "",
            modo_declarado=row["modo"] or "auto",
            filas=por_tabla.get(row["id"], []),
            vector=_parsear_vector(row["descripcion_vector"], row["vector_modelo"]),
        )
        tabla.preparar()
        tablas.append(tabla)
    return tablas


def obtener_tablas(chatbot_id: int) -> List[Tabla]:
    """Tablas activas del bot, cacheadas en memoria del worker.

    Se revalida con una firma barata (COUNT + MAX(updated_at) + filas) como
    máximo una vez por TTL: editar un teléfono no puede obligar a reinstanciar el
    ChatBot entero, que recarga el índice de disco y reconstruye BM25.
    """
    if not settings.CHATBOT_TABLAS_ACTIVO:
        return []

    ahora = time.monotonic()
    with _CACHE_LOCK:
        entrada = _CACHE.get(chatbot_id)
        if entrada and (ahora - entrada[0]) < settings.CHATBOT_TABLAS_TTL_SECONDS:
            return entrada[2]

    try:
        with _get_engine().connect() as conn:
            firma_row = conn.execute(_QUERY_FIRMA, {"cid": chatbot_id}).mappings().first()
        firma = f"{firma_row['tablas']}|{firma_row['ultima']}|{firma_row['filas']}" if firma_row else "0"
    except Exception:
        logger.exception("No se pudo leer la firma de las tablas del bot %s.", chatbot_id)
        # Con la BD caída se sigue sirviendo lo último que se cargó.
        with _CACHE_LOCK:
            entrada = _CACHE.get(chatbot_id)
            return entrada[2] if entrada else []

    with _CACHE_LOCK:
        entrada = _CACHE.get(chatbot_id)
        if entrada and entrada[1] == firma:
            _CACHE[chatbot_id] = (ahora, firma, entrada[2])
            return entrada[2]

    try:
        tablas = _cargar(chatbot_id)
    except Exception:
        logger.exception("No se pudieron cargar las tablas del bot %s.", chatbot_id)
        return []

    with _CACHE_LOCK:
        _CACHE[chatbot_id] = (time.monotonic(), firma, tablas)
    if tablas:
        logger.info(
            "Tablas del bot %s cargadas: %s",
            chatbot_id,
            ", ".join(f"{t.nombre} ({len(t.filas)} filas, modo {t.modo})" for t in tablas),
        )
    return tablas


def invalidar_cache(chatbot_id: Optional[int] = None) -> None:
    """Fuerza la recarga (lo usa el admin al guardar, y los tests)."""
    with _CACHE_LOCK:
        if chatbot_id is None:
            _CACHE.clear()
        else:
            _CACHE.pop(chatbot_id, None)


# ------------------------------------------------------------------- ruteo

def resolver(chatbot_id: int, consulta: str,
             embedding: Optional[Sequence[float]] = None) -> Optional[Consulta]:
    """¿Esta consulta la responde una tabla? Devuelve None si no (y entonces el
    bot sigue por el camino RAG de siempre, sin cambios).

    El orden de las señales no es arbitrario: primero la clave (el operador
    escribió un dato que está literalmente en la tabla, no hay ambigüedad
    posible), después las palabras declaradas, y al final la similitud con la
    descripción, que es la más blanda.
    """
    tablas = obtener_tablas(chatbot_id)
    if not tablas:
        return None

    consulta_norm = normalizar(consulta)
    if not consulta_norm:
        return None

    por_clave: List[Tuple[int, Tabla, List[Fila]]] = []
    por_termino: List[Tabla] = []
    por_similitud: List[Tuple[float, Tabla]] = []

    for tabla in tablas:
        if not tabla.filas:
            continue
        filas = tabla.buscar(consulta)
        if filas:
            por_clave.append((len(filas), tabla, filas))
            continue
        if tabla.menciona_termino(consulta_norm):
            por_termino.append(tabla)
            continue
        sim = tabla.similitud(embedding)
        if sim >= settings.CHATBOT_TABLA_SIMILITUD_MIN:
            por_similitud.append((sim, tabla))

    if por_clave:
        # Ante varias tablas con coincidencia, gana la más específica: la que
        # identificó MENOS filas (un match sobre 2 filas es más informativo que
        # uno sobre 40).
        por_clave.sort(key=lambda p: p[0])
        _, tabla, filas = por_clave[0]
        forma = "completa" if tabla.modo == "completa" else "lookup"
        return Consulta(
            tabla=tabla,
            filas=tabla.filas if forma == "completa" else filas,
            forma=forma,
            motivo="clave",
        )

    candidata = None
    motivo = ""
    if por_termino:
        candidata, motivo = por_termino[0], "termino"
    elif por_similitud:
        por_similitud.sort(key=lambda p: -p[0])
        candidata, motivo = por_similitud[0][1], "descripcion"

    if candidata is None:
        return None

    if candidata.modo == "completa":
        return Consulta(tabla=candidata, filas=candidata.filas, forma="completa", motivo=motivo)

    # Tabla grande sin ninguna clave en la consulta: no hay nada que buscar. Se
    # responde con el resumen en vez de volcar miles de filas o mandar la
    # consulta al RAG, donde el documento ya no está.
    return Consulta(tabla=candidata, filas=[], forma="resumen", motivo=motivo)


# -------------------------------------------------------- ayuda para el prompt

INSTRUCCION = (
    "Los datos de arriba salen de una tabla oficial y son la ÚNICA fuente válida para "
    "responder esta consulta.\n"
    "- Respondé con los valores exactos de la tabla. No los reformules ni los redondeés.\n"
    "- Si la tabla trae varias filas que podrían ser la que se busca, mostralas todas y "
    "pedile al operador que confirme cuál es.\n"
    "- Si la tabla no tiene la columna que se está preguntando, decilo con claridad: "
    "no completes el dato con conocimiento propio ni lo deduzcas.\n"
    "- Si el operador nombra un lugar que no figura tal cual en la tabla, podés usar tu "
    "conocimiento geográfico para indicar cuál de las filas le queda más cerca, pero "
    "ACLARÁ que es una sugerencia por cercanía y no un dato de la tabla.\n"
    "- Si hay una indicación de CÓMO USARLA, respetala aunque el operador insista."
)


def armar_bloque(consulta: Consulta) -> str:
    """El texto que se inyecta como contexto en el prompt del modelo."""
    return f"{consulta.contexto()}\n\n{INSTRUCCION}"
