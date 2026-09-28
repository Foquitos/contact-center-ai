"""Alta y mantenimiento de las tablas de datos de los chatbots.

Es la contracara de app/chatbot_tablas.py (que solo consulta): acá se parsean, se
verifican y se guardan.

LA REGLA QUE ORDENA TODO ESTE MÓDULO
------------------------------------
**El modelo define el esquema; un parser determinístico mueve los datos.**

La IA decide qué es una tabla, cómo se llama, qué columnas identifican una fila y
con qué palabras se la reconoce en una consulta. Las FILAS no las escribe nunca:
se parsean del markdown con `extraer_tablas`. El motivo es simple — la cartera de
cobranzas de benefix tiene ~2.500 filas; si el modelo las transcribe, se saltea
algunas y nadie se entera hasta que un operador pregunta por el cliente que
faltaba. Un parser, o parsea bien, o falla ruidosamente.

Como corolario, el modelo tampoco necesita VER las 2.500 filas: le alcanza con
los encabezados y una muestra, así que proponer una tabla cuesta una llamada
chica aunque el documento tenga 43k caracteres.

DEDUPLICACIÓN
-------------
Las filas repetidas se descartan por su CLAVE, sin mirar de qué tabla del
markdown salió cada una. Eso arregla solo un problema real: el
documento 68 de benefix tiene la misma cartera volcada DOS veces, una ordenada
por razón social y otra por número de cliente, porque era la única forma de que
el chunk correcto le llegara al retriever según cómo preguntara el operador. Al
pasar a tabla esa duplicación deja de tener sentido y se colapsa sola.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import bindparam, text
from sqlalchemy.types import NVARCHAR

from app import chatbot_tablas
from app.chatbot_tablas import normalizar
from app.config import settings

logger = logging.getLogger(__name__)


# ------------------------------------------------------- parseo de markdown

_RE_FILA = re.compile(r"^\s*\|(.+)\|\s*$")
# Fila separadora de una tabla markdown: |---|:---:|---|
_RE_SEPARADOR = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def _celdas(linea: str) -> List[str]:
    """Celdas de una fila markdown, respetando los pipes escapados (\\|)."""
    cuerpo = _RE_FILA.match(linea).group(1)  # type: ignore[union-attr]
    partes = re.split(r"(?<!\\)\|", cuerpo)
    return [p.replace("\\|", "|").strip() for p in partes]


def _limpiar_encabezado(texto: str) -> str:
    """Saca el énfasis markdown del nombre de una columna: '**Razón Social**'."""
    return re.sub(r"[*_`]", "", texto).strip()


def extraer_tablas(markdown: str) -> List[Dict[str, Any]]:
    """Todas las tablas markdown de un texto, en orden.

    Devuelve, por tabla:
        columnas      nombres del encabezado, o None si el encabezado no sirve
        ancho         cantidad real de columnas (la que dicen los datos)
        filas         [{col: valor}] — vacío cuando `columnas` es None
        filas_crudas  [[celda, ...]] — siempre, en el orden en que están
        descartadas   filas con una cantidad de celdas distinta a `ancho`

    `columnas` puede venir en None con filas igual: pasa de verdad en el corpus
    de benefix, donde los documentos 81 y 82 (1.384 filas entre los dos) tienen
    un encabezado de 1 y 4 columnas sobre filas de 6. Devolver las filas crudas
    permite recuperarlas haciendo que la IA nombre las columnas mirando los
    datos, en vez de perder el 30% de la cartera; y `descartadas` existe para que
    una fila rota se REPORTE en lugar de desaparecer en silencio, que es la forma
    más fácil de cargar una tabla incompleta sin enterarse.
    """
    lineas = (markdown or "").splitlines()
    tablas: List[Dict[str, Any]] = []
    i = 0
    while i < len(lineas) - 1:
        if not (_RE_FILA.match(lineas[i]) and _RE_SEPARADOR.match(lineas[i + 1])):
            i += 1
            continue

        encabezado = [_limpiar_encabezado(c) for c in _celdas(lineas[i])]
        ancho_separador = len(_celdas(lineas[i + 1]))

        crudas: List[List[str]] = []
        j = i + 2
        while j < len(lineas) and _RE_FILA.match(lineas[j]):
            celdas = _celdas(lineas[j])
            if any(c for c in celdas):
                crudas.append(celdas)
            j += 1
        i = j
        if not crudas:
            continue

        # El ancho REAL lo dicen los datos (la moda de las filas), no el
        # encabezado: cuando discrepan, el que está mal es el encabezado.
        conteo: Dict[int, int] = {}
        for fila in crudas:
            conteo[len(fila)] = conteo.get(len(fila), 0) + 1
        # El separador suele conservar el ancho original aunque el encabezado se
        # haya truncado (es lo que pasa en los docs 81/82), así que si coincide con
        # los datos confirma que el roto es el encabezado.
        ancho = max(conteo, key=lambda k: (conteo[k], k))
        if ancho_separador in conteo and conteo[ancho_separador] >= conteo[ancho]:
            ancho = ancho_separador

        alineadas = [f for f in crudas if len(f) == ancho]
        descartadas = len(crudas) - len(alineadas)

        columnas = encabezado if (
            len(encabezado) == ancho and all(encabezado)
            and len(set(encabezado)) == len(encabezado)
        ) else None

        tablas.append({
            "columnas": columnas,
            "ancho": ancho,
            "filas": [dict(zip(columnas, f)) for f in alineadas] if columnas else [],
            "filas_crudas": alineadas,
            "descartadas": descartadas,
        })
    return tablas


def agrupar_tablas(tablas: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Junta las tablas que tienen exactamente la MISMA secuencia de columnas.

    Por secuencia y no por conjunto: las filas se conservan también en crudo
    (posicionales), así que juntar dos tablas con las mismas columnas en otro
    orden dejaría los valores corridos una columna. Las dos vueltas de la cartera
    del documento 68 de benefix —la misma tabla ordenada distinto— quedan
    entonces en grupos separados, y su duplicación se colapsa después en
    `deduplicar`, que compara por clave y no depende del orden.

    Las tablas con el encabezado roto (`columnas` en None) se agrupan por ancho:
    no se pueden comparar por nombre, pero sí juntar las que tienen la misma
    forma para que la IA las nombre una sola vez.
    """
    grupos: Dict[Any, Dict[str, Any]] = {}
    orden: List[Any] = []
    for tabla in tablas:
        if tabla.get("columnas"):
            firma = ("cols", tuple(normalizar(c) for c in tabla["columnas"]))
        else:
            firma = ("sin_encabezado", tabla.get("ancho") or 0)
        if firma not in grupos:
            grupos[firma] = {
                "columnas": list(tabla["columnas"]) if tabla.get("columnas") else None,
                "ancho": tabla.get("ancho") or len(tabla.get("columnas") or []),
                "filas": [],
                "filas_crudas": [],
                "descartadas": 0,
            }
            orden.append(firma)
        grupos[firma]["filas"].extend(tabla.get("filas") or [])
        grupos[firma]["filas_crudas"].extend(tabla.get("filas_crudas") or [])
        grupos[firma]["descartadas"] += tabla.get("descartadas") or 0
    return [grupos[f] for f in orden]


def unificar_grupos(grupos: Sequence[Dict[str, Any]],
                    columnas_canonicas: Sequence[str],
                    mapeos: Dict[int, Sequence[str]]) -> Dict[str, Any]:
    """Funde varios grupos en UNA tabla, renombrando las columnas de cada uno
    según el mapeo que dio la IA (nombre canónico para cada posición).

    Existe por la cartera de cobranzas de benefix: los 10 documentos, uno por
    gestor, NO comparten esquema — `Razón Social | N° Cliente | ...` en el 68,
    `... | Cliente | ...` en el 69/70, `Cliente | SubCta | Cl2 | Razón Social |
    ...` en el 75/78/79/80 y el encabezado roto en el 81/82. Como el gestor es un
    VALOR de columna y no una tabla distinta, todo eso tiene que terminar en una
    sola tabla, y unificar los nombres es una decisión de esquema: la toma la IA,
    y acá solo se aplica.

    Un grupo sin mapeo válido no se adivina: se deja afuera y se reporta, porque
    meter sus filas con las columnas corridas es peor que no meterlas.
    """
    canonicas = list(columnas_canonicas)
    filas: List[Dict[str, str]] = []
    descartados: List[str] = []
    descartadas_filas = 0

    for i, grupo in enumerate(grupos):
        mapeo = list(mapeos.get(i) or [])
        ancho = grupo.get("ancho") or len(grupo.get("columnas") or [])
        if len(mapeo) != ancho or not all(m in canonicas for m in mapeo if m):
            descartados.append(
                f"grupo {i + 1} ({ancho} columnas, {len(grupo.get('filas_crudas') or [])} filas)"
            )
            continue
        for cruda in grupo.get("filas_crudas") or []:
            fila = {}
            for nombre, valor in zip(mapeo, cruda):
                # Una columna mapeada a "" es una que la IA decidió ignorar.
                if nombre:
                    fila[nombre] = valor
            if fila:
                filas.append(fila)
        descartadas_filas += grupo.get("descartadas") or 0

    return {
        "columnas": canonicas,
        "filas": filas,
        "grupos_descartados": descartados,
        "descartadas": descartadas_filas,
    }


def deduplicar(filas: Sequence[Dict[str, str]], claves: Sequence[str]) -> Tuple[List[Dict[str, str]], int]:
    """Saca las filas repetidas por su clave. Devuelve (filas, cuántas se sacaron).

    Si dos filas comparten clave pero tienen datos distintos, se conserva la
    primera: es un conflicto de la fuente y se reporta como nota, no se resuelve
    en silencio inventando un criterio."""
    vistas: Dict[Tuple[str, ...], Dict[str, str]] = {}
    duplicadas = 0
    resultado = []
    for fila in filas:
        firma = tuple(normalizar(fila.get(c, "")) for c in claves)
        if not any(firma):
            continue
        if firma in vistas:
            duplicadas += 1
            continue
        vistas[firma] = fila
        resultado.append(fila)
    return resultado, duplicadas


def filas_no_verificadas(filas: Sequence[Dict[str, str]], claves: Sequence[str],
                         fuente: str, limite: int = 20) -> List[str]:
    """Claves de filas que NO aparecen en el texto de origen.

    Es la guarda contra invenciones y corre siempre, incluso cuando la tabla la
    reescribió el modelo desde prosa: si una base o una razón social no está
    escrita en el material, no puede estar en la tabla. Determinística y gratis.
    """
    fuente_norm = normalizar(fuente)
    faltantes = []
    for fila in filas:
        for clave in claves:
            valor = normalizar(fila.get(clave, ""))
            if valor and len(valor) >= 4 and valor not in fuente_norm:
                faltantes.append(f"{clave}: {fila.get(clave)}")
                break
        if len(faltantes) >= limite:
            break
    return faltantes


# Etiqueta de un bloque "Campo: valor" (con o sin negrita markdown). Es la forma
# en la que están escritas las bases de vantix: cada base es un bloque de líneas
# 'Dirección:', 'Entrecalles:', 'Vehículos aptos:'. Un listado de entidades
# escrito en prosa se reconoce porque las MISMAS etiquetas se repiten.
_RE_ETIQUETA = re.compile(r"^\s*[*_]{0,2}([^:|*_]{3,40})[*_]{0,2}\s*:\s*\S")


def repeticiones_de_etiqueta(markdown: str) -> Dict[str, int]:
    """Cuántas veces se repite cada etiqueta 'Campo: valor' del material.

    Es la huella de un listado de entidades escrito en prosa: las bases de vantix
    son bloques con 'Dirección:', 'Entrecalles:', 'Vehículos aptos:' repetidos una
    vez por base.
    """
    repeticiones: Dict[str, int] = {}
    for linea in (markdown or "").splitlines():
        m = _RE_ETIQUETA.match(linea)
        if m:
            etiqueta = normalizar(m.group(1))
            repeticiones[etiqueta] = repeticiones.get(etiqueta, 0) + 1
    return repeticiones


def entidades_estimadas(markdown: str) -> Tuple[int, str]:
    """Cuántas entidades parece tener un listado escrito en prosa, y con qué
    etiqueta se lo dedujo. (0, "") si no parece un listado.

    Sirve para lo único que un parser NO puede garantizar en el camino de prosa:
    que el modelo no se haya salteado entidades al reescribir el listado como
    tabla. Contar una etiqueta repetida es determinístico y gratis, y alcanza
    para detectar el faltante aunque no diga cuál falta.
    """
    repeticiones = repeticiones_de_etiqueta(markdown)
    if not repeticiones:
        return 0, ""
    etiqueta, veces = max(repeticiones.items(), key=lambda kv: kv[1])
    return (veces, etiqueta) if veces >= settings.CHATBOT_TABLA_MIN_FILAS_DETECCION else (0, "")


def parece_tabla(markdown: str) -> bool:
    """Filtro barato y determinístico: ¿vale la pena preguntarle a la IA si este
    documento es una tabla de datos?

    Existe para no pagar una llamada por cada documento de cada formateo. Da
    positivo en los dos casos reales —una tabla markdown con muchas filas (la
    cartera de benefix) y un listado de bloques 'Campo: valor' repetidos (las
    bases de vantix)— y negativo en un procedimiento, que puede tener cuadros
    pero chicos y sin etiquetas que se repitan.
    """
    grupos = agrupar_tablas(extraer_tablas(markdown))
    # Se cuentan las filas CRUDAS: una tabla con el encabezado roto (docs 81/82 de
    # benefix) tiene 0 filas armadas y aun así es la tabla de datos más grande del
    # documento. Justamente esas son las que hay que mandar a analizar.
    if any(len(g["filas_crudas"]) >= settings.CHATBOT_TABLA_MIN_FILAS_DETECCION for g in grupos):
        return True

    # Dos etiquetas distintas repitiéndose muchas veces = un listado de entidades
    # con los mismos atributos. Una sola etiqueta repetida puede ser cualquier
    # documento con muchos "Importante:".
    frecuentes = [n for n in repeticiones_de_etiqueta(markdown).values()
                  if n >= settings.CHATBOT_TABLA_MIN_FILAS_DETECCION]
    return len(frecuentes) >= 2


def muestra_para_la_ia(filas: Sequence[Any], cantidad: int = 12) -> List[Any]:
    """Primeras y últimas filas (sirve tanto para dicts como para filas crudas).

    El modelo solo necesita ver la FORMA de los datos para nombrar la tabla,
    elegir las claves y unificar los esquemas; mandarle 2.500 filas costaría una
    fortuna y no cambiaría ninguna de esas decisiones."""
    filas = list(filas)
    if len(filas) <= cantidad:
        return filas
    mitad = cantidad // 2
    return filas[:mitad] + filas[-mitad:]


# ------------------------------------------------------------- persistencia

def _busqueda_de(fila: Dict[str, str], claves: Sequence[str]) -> str:
    """Texto normalizado de las columnas clave, precomputado al guardar para no
    normalizar miles de filas en cada consulta."""
    return normalizar(" | ".join(str(fila.get(c, "") or "") for c in claves))[:1000]


def calcular_vector(descripcion: str) -> Tuple[Optional[str], Optional[str]]:
    """Embedding de la descripción, para el ruteo por similitud.

    Se calcula UNA vez, al guardar. Si falla, la tabla se guarda igual sin vector:
    el ruteo por claves y por términos sigue funcionando, solo se pierde la red
    para la consulta que no usa ninguna palabra declarada. Que no se pueda
    embeber una descripción no puede impedir cargar los datos.
    """
    texto = (descripcion or "").strip()
    if not texto:
        return None, None
    try:
        from llama_index.core import Settings

        from app import rag_settings

        rag_settings.configure_global_settings()
        vector = Settings.embed_model.get_text_embedding(texto)
        return json.dumps([float(x) for x in vector]), settings.DEFAULT_REMOTE_EMBED_MODEL
    except Exception:
        logger.exception("No se pudo embeber la descripción de la tabla; se guarda sin vector.")
        return None, None


_INSERT_TABLA = text("""
    INSERT INTO pagina_web.ChatbotTabla
        (chatbot_id, nombre, descripcion, terminos, columnas, claves, nota, modo,
         filas_total, descripcion_vector, vector_modelo, doc_origen_id, created_by)
    OUTPUT INSERTED.id
    VALUES (:cid, :nombre, :descripcion, :terminos, :columnas, :claves, :nota, :modo,
            :filas_total, :vector, :vector_modelo, :doc_origen, :created_by)
""").bindparams(
    bindparam("nombre", type_=NVARCHAR), bindparam("descripcion", type_=NVARCHAR),
    bindparam("terminos", type_=NVARCHAR), bindparam("columnas", type_=NVARCHAR),
    bindparam("claves", type_=NVARCHAR), bindparam("nota", type_=NVARCHAR),
    bindparam("vector", type_=NVARCHAR),
)

_INSERT_FILA = text("""
    INSERT INTO pagina_web.ChatbotTablaFila (tabla_id, orden, datos, busqueda)
    VALUES (:tid, :orden, :datos, :busqueda)
""").bindparams(bindparam("datos", type_=NVARCHAR), bindparam("busqueda", type_=NVARCHAR))


def _get_engine():
    from app.database import engine
    return engine


def _serializar_columnas(columnas: Sequence[Dict[str, Any]]) -> str:
    return json.dumps([
        {
            "nombre": str(c.get("nombre", "")).strip(),
            "descripcion": str(c.get("descripcion", "") or "").strip(),
            "clave": bool(c.get("clave")),
        }
        for c in columnas if str(c.get("nombre", "")).strip()
    ], ensure_ascii=False)


def guardar(chatbot_id: int, propuesta: Dict[str, Any], *, created_by: Optional[int] = None,
            tabla_id: Optional[int] = None) -> int:
    """Crea (o reemplaza) una tabla con sus filas. Devuelve el id.

    Reemplazar es borrar las filas e insertar las nuevas dentro de la MISMA
    transacción: una tabla a medio cargar le daría al bot datos incompletos con
    los que igual respondería, que es peor que no tenerlos.
    """
    columnas = propuesta.get("columnas") or []
    claves = [c["nombre"] for c in columnas if c.get("clave")] or [columnas[0]["nombre"]]
    filas = propuesta.get("filas") or []
    payload = {
        "cid": chatbot_id,
        "nombre": (propuesta.get("nombre") or "Tabla de datos").strip()[:200],
        "descripcion": (propuesta.get("descripcion") or "").strip()[:1000],
        "terminos": ",".join(
            t.strip() for t in (propuesta.get("terminos") or []) if t and t.strip()
        )[:1000],
        "columnas": _serializar_columnas(columnas),
        "claves": ",".join(claves)[:400],
        "nota": (propuesta.get("nota") or "").strip()[:1000] or None,
        "modo": propuesta.get("modo") if propuesta.get("modo") in ("auto", "completa", "lookup") else "auto",
        "filas_total": len(filas),
        # La columna guarda UN documento de origen (trazabilidad); cuando la tabla
        # sale de varios, el primero alcanza para saber de dónde vino.
        "doc_origen": (propuesta.get("doc_origen_ids") or [None])[0],
        "created_by": created_by,
    }
    vector, vector_modelo = calcular_vector(payload["descripcion"])
    payload["vector"] = vector
    payload["vector_modelo"] = vector_modelo

    engine = _get_engine()
    with engine.begin() as conn:
        if tabla_id is None:
            nuevo_id = conn.execute(_INSERT_TABLA, payload).scalar_one()
        else:
            nuevo_id = tabla_id
            conn.execute(text("""
                UPDATE pagina_web.ChatbotTabla
                SET nombre = :nombre, descripcion = :descripcion, terminos = :terminos,
                    columnas = :columnas, claves = :claves, nota = :nota, modo = :modo,
                    filas_total = :filas_total, descripcion_vector = :vector,
                    vector_modelo = :vector_modelo, updated_at = SYSUTCDATETIME()
                WHERE id = :tid AND chatbot_id = :cid
            """).bindparams(
                bindparam("nombre", type_=NVARCHAR), bindparam("descripcion", type_=NVARCHAR),
                bindparam("terminos", type_=NVARCHAR), bindparam("columnas", type_=NVARCHAR),
                bindparam("claves", type_=NVARCHAR), bindparam("nota", type_=NVARCHAR),
                bindparam("vector", type_=NVARCHAR),
            ), {**payload, "tid": tabla_id})
            conn.execute(text("DELETE FROM pagina_web.ChatbotTablaFila WHERE tabla_id = :tid"),
                         {"tid": tabla_id})

        # executemany (una sola llamada con todos los parámetros) y no un INSERT por
        # fila: la cartera de cobranzas son ~2.500 filas y de a una no entra en el
        # timeout del proxy del frontend.
        if filas:
            conn.execute(_INSERT_FILA, [
                {
                    "tid": nuevo_id,
                    "orden": orden,
                    "datos": json.dumps(fila, ensure_ascii=False),
                    "busqueda": _busqueda_de(fila, claves),
                }
                for orden, fila in enumerate(filas)
            ])

    chatbot_tablas.invalidar_cache(chatbot_id)
    logger.info("Tabla '%s' guardada para el bot %s: %d filas.",
                payload["nombre"], chatbot_id, len(filas))
    return nuevo_id


def actualizar_metadata(chatbot_id: int, tabla_id: int, cambios: Dict[str, Any]) -> None:
    """Edita solo la definición (nombre, descripción, términos, nota, modo, claves).

    Cambiar las CLAVES obliga a recalcular la columna `busqueda` de todas las
    filas: es el texto sobre el que se busca, y dejarlo viejo haría que la tabla
    dejara de encontrar sus propias filas sin ningún error visible.
    """
    campos = {
        "nombre": (cambios.get("nombre") or "").strip()[:200] or None,
        "descripcion": (cambios.get("descripcion") or "").strip()[:1000],
        "terminos": ",".join(t.strip() for t in (cambios.get("terminos") or []) if t.strip())[:1000],
        "nota": ((cambios.get("nota") or "").strip()[:1000]) or None,
        "modo": cambios.get("modo") if cambios.get("modo") in ("auto", "completa", "lookup") else "auto",
    }
    claves = [c.strip() for c in (cambios.get("claves") or []) if c and c.strip()]

    engine = _get_engine()
    with engine.begin() as conn:
        fila = conn.execute(text("""
            SELECT columnas, claves FROM pagina_web.ChatbotTabla
            WHERE id = :tid AND chatbot_id = :cid
        """), {"tid": tabla_id, "cid": chatbot_id}).mappings().first()
        if fila is None:
            raise ValueError("La tabla no existe para ese chatbot.")

        columnas = json.loads(fila["columnas"] or "[]")
        if claves:
            nombres = {c.get("nombre") for c in columnas}
            desconocidas = [c for c in claves if c not in nombres]
            if desconocidas:
                raise ValueError(f"Columnas clave inexistentes: {', '.join(desconocidas)}")
            for col in columnas:
                col["clave"] = col.get("nombre") in claves
        claves_finales = [c.get("nombre") for c in columnas if c.get("clave")] or \
            [c.get("nombre") for c in columnas[:1]]

        vector, vector_modelo = calcular_vector(campos["descripcion"])
        conn.execute(text("""
            UPDATE pagina_web.ChatbotTabla
            SET nombre = COALESCE(:nombre, nombre), descripcion = :descripcion,
                terminos = :terminos, nota = :nota, modo = :modo,
                columnas = :columnas, claves = :claves,
                descripcion_vector = :vector, vector_modelo = :vector_modelo,
                updated_at = SYSUTCDATETIME()
            WHERE id = :tid AND chatbot_id = :cid
        """).bindparams(
            bindparam("nombre", type_=NVARCHAR), bindparam("descripcion", type_=NVARCHAR),
            bindparam("terminos", type_=NVARCHAR), bindparam("nota", type_=NVARCHAR),
            bindparam("columnas", type_=NVARCHAR), bindparam("claves", type_=NVARCHAR),
            bindparam("vector", type_=NVARCHAR),
        ), {
            **campos, "tid": tabla_id, "cid": chatbot_id,
            "columnas": json.dumps(columnas, ensure_ascii=False),
            "claves": ",".join(claves_finales)[:400],
            "vector": vector, "vector_modelo": vector_modelo,
        })

        if claves:
            # `busqueda` es el texto sobre el que se busca: si cambian las claves y
            # no se recalcula, la tabla deja de encontrar sus propias filas sin
            # ningún error visible. Va en un solo executemany por lo mismo que el
            # alta: pueden ser miles de filas.
            filas = conn.execute(text(
                "SELECT id, datos FROM pagina_web.ChatbotTablaFila WHERE tabla_id = :tid"
            ), {"tid": tabla_id}).mappings().all()
            actualizaciones = []
            for f in filas:
                try:
                    datos = json.loads(f["datos"])
                except (TypeError, ValueError):
                    continue
                actualizaciones.append(
                    {"b": _busqueda_de(datos, claves_finales), "fid": f["id"]}
                )
            if actualizaciones:
                conn.execute(text(
                    "UPDATE pagina_web.ChatbotTablaFila SET busqueda = :b WHERE id = :fid"
                ).bindparams(bindparam("b", type_=NVARCHAR)), actualizaciones)

    chatbot_tablas.invalidar_cache(chatbot_id)


def borrar(chatbot_id: int, tabla_id: int) -> None:
    """Borrado real (las filas caen por el ON DELETE CASCADE).

    No es soft-delete a propósito: una tabla de datos desactivada no aporta
    historial útil y su volumen (miles de filas) no tiene por qué quedar
    ocupando lugar."""
    with _get_engine().begin() as conn:
        conn.execute(text(
            "DELETE FROM pagina_web.ChatbotTabla WHERE id = :tid AND chatbot_id = :cid"
        ), {"tid": tabla_id, "cid": chatbot_id})
    chatbot_tablas.invalidar_cache(chatbot_id)


def listar(chatbot_id: int) -> List[Dict[str, Any]]:
    """Definición de las tablas del bot (sin las filas), para la pantalla."""
    with _get_engine().connect() as conn:
        filas = conn.execute(text("""
            SELECT t.id, t.chatbot_id, t.nombre, t.descripcion, t.terminos, t.columnas,
                   t.claves, t.nota, t.modo, t.filas_total, t.doc_origen_id, t.updated_at,
                   CASE WHEN t.descripcion_vector IS NULL THEN 0 ELSE 1 END AS tiene_vector,
                   (SELECT COUNT(*) FROM pagina_web.ChatbotTablaFila f
                    WHERE f.tabla_id = t.id AND f.editada_at IS NOT NULL) AS editadas
            FROM pagina_web.ChatbotTabla t
            WHERE t.chatbot_id = :cid AND t.activo = 1
            ORDER BY t.nombre
        """), {"cid": chatbot_id}).mappings().all()

    salida = []
    for f in filas:
        columnas = chatbot_tablas._parsear_columnas(f["columnas"], f["claves"])
        # El modo EFECTIVO (no el declarado) es lo que hay que mostrar: con 'auto'
        # nadie puede saber si su tabla entra entera o se busca por clave, y esa
        # es justo la diferencia que explica cómo va a responder el bot.
        tabla = chatbot_tablas.Tabla(
            id=f["id"], chatbot_id=chatbot_id, nombre=f["nombre"], descripcion=f["descripcion"] or "",
            terminos=[], columnas=columnas, nota=f["nota"] or "",
            modo_declarado=f["modo"] or "auto", filas=[],
        )
        salida.append({
            "id": f["id"],
            "nombre": f["nombre"],
            "descripcion": f["descripcion"] or "",
            "terminos": [t.strip() for t in (f["terminos"] or "").split(",") if t.strip()],
            "columnas": [
                {"nombre": c.nombre, "descripcion": c.descripcion, "clave": c.clave}
                for c in columnas
            ],
            "nota": f["nota"] or "",
            "modo": f["modo"] or "auto",
            "modo_efectivo": "completa" if _entra_entera(f["filas_total"], tabla) else "lookup",
            "filas_total": f["filas_total"],
            "doc_origen_id": f["doc_origen_id"],
            "tiene_vector": bool(f["tiene_vector"]),
            "editadas": f["editadas"],
            "updated_at": f["updated_at"],
        })
    return salida


def _entra_entera(filas_total: int, tabla: "chatbot_tablas.Tabla") -> bool:
    """Aproximación del modo efectivo para el listado, sin traer las filas: el
    modo declarado manda, y con 'auto' se estima por cantidad de filas y ancho de
    las columnas."""
    if tabla.modo_declarado in ("completa", "lookup"):
        return tabla.modo_declarado == "completa"
    ancho_estimado = max(1, len(tabla.columnas)) * 40
    return filas_total * ancho_estimado <= settings.CHATBOT_TABLA_MAX_CHARS_COMPLETA


def obtener_filas(chatbot_id: int, tabla_id: int, *, limit: int = 50, offset: int = 0,
                  q: Optional[str] = None) -> Dict[str, Any]:
    """Filas paginadas para la pantalla, con búsqueda por texto.

    Cada fila viaja con su `id` porque desde la pantalla se editan de a una: sin
    el id no habría contra qué mandar la corrección."""
    where = "t.chatbot_id = :cid AND f.tabla_id = :tid"
    params: Dict[str, Any] = {"cid": chatbot_id, "tid": tabla_id,
                              "limit": max(1, min(limit, 200)), "offset": max(0, offset)}
    if (q or "").strip():
        where += " AND f.busqueda LIKE :q"
        params["q"] = f"%{normalizar(q)}%"

    with _get_engine().connect() as conn:
        total = conn.execute(text(f"""
            SELECT COUNT(*) FROM pagina_web.ChatbotTablaFila f
            JOIN pagina_web.ChatbotTabla t ON t.id = f.tabla_id
            WHERE {where}
        """), params).scalar_one()
        filas = conn.execute(text(f"""
            SELECT f.id, f.datos, f.editada_at FROM pagina_web.ChatbotTablaFila f
            JOIN pagina_web.ChatbotTabla t ON t.id = f.tabla_id
            WHERE {where}
            ORDER BY f.orden, f.id
            OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
        """), params).mappings().all()

    salida = []
    for fila in filas:
        try:
            datos = json.loads(fila["datos"])
        except (TypeError, ValueError):
            continue
        salida.append({
            "id": fila["id"],
            "datos": datos,
            "editada": fila["editada_at"] is not None,
        })
    return {"total": total, "filas": salida}


# ------------------------------------------------- edición fila por fila
#
# Una tabla tiene DOS caminos de escritura y hay que aceptarlo de frente: la carga
# masiva desde la fuente oficial (que reemplaza todo) y la corrección puntual del
# analista que ve un dato mal. El segundo es indispensable — obligar a re-subir
# 2.500 filas para arreglar un teléfono garantiza que el teléfono quede mal — pero
# se pisan entre sí, así que la fila corregida queda MARCADA (`editada_at`) y la
# próxima recarga masiva puede avisar cuántas correcciones está por perder.


def _tabla_para_escribir(conn, chatbot_id: int, tabla_id: int) -> Dict[str, Any]:
    """Valida que la tabla sea de ese bot y devuelve sus columnas y claves."""
    fila = conn.execute(text("""
        SELECT columnas, claves FROM pagina_web.ChatbotTabla
        WHERE id = :tid AND chatbot_id = :cid AND activo = 1
    """), {"tid": tabla_id, "cid": chatbot_id}).mappings().first()
    if fila is None:
        raise ValueError("La tabla no existe para ese chatbot.")
    columnas = chatbot_tablas._parsear_columnas(fila["columnas"], fila["claves"])
    claves = [c.nombre for c in columnas if c.clave] or [columnas[0].nombre]
    return {"columnas": [c.nombre for c in columnas], "claves": claves}


def _tocar_tabla(conn, tabla_id: int) -> None:
    """Recuenta las filas y mueve `updated_at`.

    Las dos cosas son necesarias: `updated_at` es lo que hace que los workers
    reconstruyan la tabla (la firma de la caché lo mira), y sin recontar,
    `filas_total` queda mintiendo en la pantalla y en el cálculo del modo."""
    conn.execute(text("""
        UPDATE pagina_web.ChatbotTabla
        SET filas_total = (SELECT COUNT(*) FROM pagina_web.ChatbotTablaFila WHERE tabla_id = :tid),
            updated_at = SYSUTCDATETIME()
        WHERE id = :tid
    """), {"tid": tabla_id})


def _validar_fila(datos: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, str]:
    """Deja solo las columnas declaradas y exige que las claves tengan valor.

    Lo primero evita que una fila con una columna de más quede con un dato que el
    bot nunca va a mostrar (las columnas del prompt salen del esquema); lo segundo,
    que quede una fila imposible de encontrar."""
    limpia = {c: str(datos.get(c, "") or "").strip() for c in meta["columnas"]}
    if not any(limpia.get(c) for c in meta["claves"]):
        raise ValueError(
            "La fila tiene vacías todas las columnas por las que se busca "
            f"({', '.join(meta['claves'])}): así el chatbot nunca podría encontrarla."
        )
    return limpia


def guardar_fila(chatbot_id: int, tabla_id: int, fila_id: Optional[int],
                 datos: Dict[str, Any], usuario: Optional[int]) -> int:
    """Crea (fila_id None) o actualiza una fila. Devuelve su id."""
    with _get_engine().begin() as conn:
        meta = _tabla_para_escribir(conn, chatbot_id, tabla_id)
        limpia = _validar_fila(datos, meta)
        payload = {
            "tid": tabla_id,
            "datos": json.dumps(limpia, ensure_ascii=False),
            "busqueda": _busqueda_de(limpia, meta["claves"]),
            "uid": usuario,
        }
        if fila_id is None:
            # Al final de la tabla: el orden solo decide cómo se lee, no cómo se busca.
            nuevo = conn.execute(text("""
                INSERT INTO pagina_web.ChatbotTablaFila
                    (tabla_id, orden, datos, busqueda, editada_por, editada_at)
                OUTPUT INSERTED.id
                VALUES (:tid,
                        (SELECT COALESCE(MAX(orden), -1) + 1 FROM pagina_web.ChatbotTablaFila
                         WHERE tabla_id = :tid),
                        :datos, :busqueda, :uid, SYSUTCDATETIME())
            """).bindparams(bindparam("datos", type_=NVARCHAR),
                            bindparam("busqueda", type_=NVARCHAR)), payload).scalar_one()
            fila_id = int(nuevo)
        else:
            actualizado = conn.execute(text("""
                UPDATE pagina_web.ChatbotTablaFila
                SET datos = :datos, busqueda = :busqueda,
                    editada_por = :uid, editada_at = SYSUTCDATETIME()
                WHERE id = :fid AND tabla_id = :tid
            """).bindparams(bindparam("datos", type_=NVARCHAR),
                            bindparam("busqueda", type_=NVARCHAR)),
                {**payload, "fid": fila_id})
            if not actualizado.rowcount:
                raise ValueError("La fila no existe en esa tabla.")
        _tocar_tabla(conn, tabla_id)

    chatbot_tablas.invalidar_cache(chatbot_id)
    return fila_id


def borrar_fila(chatbot_id: int, tabla_id: int, fila_id: int) -> None:
    with _get_engine().begin() as conn:
        _tabla_para_escribir(conn, chatbot_id, tabla_id)
        borrado = conn.execute(text("""
            DELETE FROM pagina_web.ChatbotTablaFila
            WHERE id = :fid AND tabla_id = :tid
        """), {"fid": fila_id, "tid": tabla_id})
        if not borrado.rowcount:
            raise ValueError("La fila no existe en esa tabla.")
        _tocar_tabla(conn, tabla_id)
    chatbot_tablas.invalidar_cache(chatbot_id)


# ============================================ actualizar una tabla con material
#
# La otra mitad del mantenimiento: Cobranzas manda la cartera nueva y hay que
# volcarla sin perder lo que se corrigió a mano ni tener que revisar 2.500 filas.
#
# Dos decisiones de diseño que explican todo lo de abajo:
#
# 1. UNA PLANILLA SE PARSEA, NO SE LE PIDE A UN MODELO QUE LA LEA. Un Excel ya
#    ES una tabla: pasarlo por el asistente para que devuelva markdown es caro y
#    lossy — de hecho así fue como la cartera terminó con dos documentos de
#    encabezado roto. El modelo interviene solo para decir a qué columna de la
#    tabla destino corresponde cada columna del archivo, que es una decisión de
#    esquema y no un traslado de datos.
#
# 2. NO SE APLICA NADA SIN VER EL DIFF. Una carga que borra 400 filas porque el
#    archivo vino cortado es indistinguible de una baja masiva legítima si no se
#    mira antes. El diff se calcula por CLAVE y se aprueba en pantalla.

MIMES_PLANILLA = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
)


def _grupo_desde_filas(columnas: Sequence[str], filas: Sequence[Sequence[Any]]) -> Dict[str, Any]:
    def _texto(valor: Any) -> str:
        if valor is None:
            return ""
        # Los números enteros de una planilla llegan como float (1824.0) y son
        # claves: "1824.0" no matchea con "1824" y la fila se cargaría como alta.
        if isinstance(valor, float) and valor.is_integer():
            return str(int(valor))
        return str(valor).strip()

    nombres = [_texto(c) for c in columnas]
    crudas = [[_texto(c) for c in fila] for fila in filas]
    crudas = [f for f in crudas if any(f)]
    return {
        "columnas": nombres if all(nombres) and len(set(nombres)) == len(nombres) else None,
        "ancho": len(nombres),
        "filas": [],
        "filas_crudas": [f for f in crudas if len(f) == len(nombres)],
        "descartadas": sum(1 for f in crudas if len(f) != len(nombres)),
    }


def grupos_desde_fuentes(fuentes: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Saca los listados de un material crudo, sin llamar a ningún modelo.

    Soporta planilla (xlsx/xls), CSV y texto/markdown con tablas. Un PDF o una
    imagen NO: para actualizar una tabla hace falta la grilla, y reconstruirla
    desde un PDF con un modelo es justo el paso que rompe los datos.
    """
    grupos: List[Dict[str, Any]] = []
    for fuente in fuentes or []:
        nombre = (fuente.get("nombre") or "").lower()
        mime = (fuente.get("mime") or "").lower()

        if fuente.get("tipo") == "texto":
            grupos.extend(agrupar_tablas(extraer_tablas(fuente.get("texto") or "")))
            continue

        datos = fuente.get("datos")
        if not datos:
            continue

        try:
            if mime in MIMES_PLANILLA or nombre.endswith((".xlsx", ".xls", ".xlsm")):
                import io

                import pandas as pd

                # header=0 y dtype=str: los códigos de cliente son texto aunque
                # parezcan números (un "007" no puede volverse 7).
                hojas = pd.read_excel(io.BytesIO(datos), sheet_name=None, dtype=str)
                for df in hojas.values():
                    if df.empty:
                        continue
                    grupos.append(_grupo_desde_filas(
                        list(df.columns), df.fillna("").values.tolist()))
            elif mime in ("text/csv",) or nombre.endswith((".csv", ".txt")):
                import io

                import pandas as pd

                df = pd.read_csv(io.BytesIO(datos), dtype=str, sep=None, engine="python")
                if not df.empty:
                    grupos.append(_grupo_desde_filas(
                        list(df.columns), df.fillna("").values.tolist()))
        except Exception:
            logger.exception("No se pudo leer el material '%s' como planilla.", nombre)
    return [g for g in grupos if g["filas_crudas"]]


def _clave_de(fila: Dict[str, str], claves: Sequence[str]) -> Tuple[str, ...]:
    return tuple(normalizar(fila.get(c, "")) for c in claves)


def diff_contra_tabla(chatbot_id: int, tabla_id: int, filas_entrantes: Sequence[Dict[str, str]],
                      modo_carga: str) -> Dict[str, Any]:
    """Compara el material entrante con la tabla y devuelve qué cambiaría.

    `modo_carga`:
      'reemplazo'   el material es la tabla COMPLETA: lo que no viene se da de baja.
      'incremental' el material trae solo novedades: no se da de baja nada.

    No es un default que se pueda adivinar — son resultados opuestos sobre los
    mismos datos — así que lo elige quien carga, en la pantalla.

    Solo se cuentan como cambio las columnas que REALMENTE cambiaron: sin eso,
    una planilla que trae las mismas 2.500 filas con un espacio de más da 2.500
    cambios y nadie revisa nada.
    """
    with _get_engine().connect() as conn:
        meta = _tabla_para_escribir(conn, chatbot_id, tabla_id)
        existentes = conn.execute(text("""
            SELECT id, datos, editada_at FROM pagina_web.ChatbotTablaFila
            WHERE tabla_id = :tid ORDER BY orden, id
        """), {"tid": tabla_id}).mappings().all()

    claves = meta["claves"]
    columnas = meta["columnas"]
    actuales: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    for fila in existentes:
        try:
            datos = json.loads(fila["datos"])
        except (TypeError, ValueError):
            continue
        actuales[_clave_de(datos, claves)] = {
            "id": fila["id"], "datos": datos, "editada": fila["editada_at"] is not None,
        }

    entrantes, duplicadas = deduplicar(
        [{c: str(f.get(c, "") or "").strip() for c in columnas} for f in filas_entrantes],
        claves,
    )

    altas, cambios = [], []
    vistas = set()
    for fila in entrantes:
        clave = _clave_de(fila, claves)
        vistas.add(clave)
        actual = actuales.get(clave)
        if actual is None:
            altas.append(fila)
            continue
        distintas = [c for c in columnas if (actual["datos"].get(c) or "") != (fila.get(c) or "")]
        if distintas:
            cambios.append({
                "fila_id": actual["id"],
                "clave": " / ".join(v for v in (fila.get(c) for c in claves) if v),
                "antes": actual["datos"],
                "despues": fila,
                "columnas": distintas,
                # Una fila corregida a mano que la planilla vuelve a pisar es
                # exactamente el conflicto que hay que mostrar, no resolver solo.
                "editada_a_mano": actual["editada"],
            })

    bajas = []
    if modo_carga == "reemplazo":
        bajas = [
            {"fila_id": v["id"],
             "clave": " / ".join(x for x in (v["datos"].get(c) for c in claves) if x),
             "datos": v["datos"]}
            for k, v in actuales.items() if k not in vistas
        ]

    notas = []
    if duplicadas:
        notas.append(f"El material traía {duplicadas} fila(s) repetidas; se tomó la primera de cada una.")
    pisadas = sum(1 for c in cambios if c["editada_a_mano"])
    if pisadas:
        notas.append(
            f"{pisadas} de los cambios pisan filas que alguien corrigió a mano. Revisalos: "
            "puede ser que la planilla venga con el dato viejo."
        )

    return {
        "tabla_id": tabla_id,
        "modo_carga": modo_carga,
        "columnas": columnas,
        "claves": claves,
        "altas": altas,
        "cambios": cambios,
        "bajas": bajas,
        "sin_cambios": len(entrantes) - len(altas) - len(cambios),
        "notas": notas,
    }


def aplicar_diff(chatbot_id: int, tabla_id: int, altas: Sequence[Dict[str, str]],
                 cambios: Sequence[Dict[str, Any]], bajas: Sequence[int],
                 usuario: Optional[int]) -> Dict[str, int]:
    """Aplica lo aprobado, todo en una transacción.

    Las filas que entran por acá NO quedan marcadas como editadas a mano: vienen
    de la fuente oficial, así que la marca tiene que seguir señalando solo lo que
    corrigió una persona (que es lo que la próxima carga puede pisar).
    """
    with _get_engine().begin() as conn:
        meta = _tabla_para_escribir(conn, chatbot_id, tabla_id)

        if bajas:
            conn.execute(text("""
                DELETE FROM pagina_web.ChatbotTablaFila
                WHERE tabla_id = :tid AND id IN :ids
            """).bindparams(bindparam("ids", expanding=True)),
                {"tid": tabla_id, "ids": list(bajas)})

        if cambios:
            conn.execute(text("""
                UPDATE pagina_web.ChatbotTablaFila
                SET datos = :datos, busqueda = :busqueda,
                    editada_por = NULL, editada_at = NULL
                WHERE id = :fid AND tabla_id = :tid
            """).bindparams(bindparam("datos", type_=NVARCHAR),
                            bindparam("busqueda", type_=NVARCHAR)),
                [{
                    "fid": c["fila_id"], "tid": tabla_id,
                    "datos": json.dumps(_validar_fila(c["despues"], meta), ensure_ascii=False),
                    "busqueda": _busqueda_de(c["despues"], meta["claves"]),
                } for c in cambios])

        if altas:
            base_orden = conn.execute(text(
                "SELECT COALESCE(MAX(orden), -1) + 1 FROM pagina_web.ChatbotTablaFila WHERE tabla_id = :tid"
            ), {"tid": tabla_id}).scalar_one()
            conn.execute(_INSERT_FILA, [{
                "tid": tabla_id,
                "orden": base_orden + i,
                "datos": json.dumps(_validar_fila(fila, meta), ensure_ascii=False),
                "busqueda": _busqueda_de(fila, meta["claves"]),
            } for i, fila in enumerate(altas)])

        _tocar_tabla(conn, tabla_id)

    chatbot_tablas.invalidar_cache(chatbot_id)
    logger.info("Tabla %s actualizada: %d altas, %d cambios, %d bajas (usuario %s).",
                tabla_id, len(altas), len(cambios), len(bajas), usuario)
    return {"altas": len(altas), "cambios": len(cambios), "bajas": len(bajas)}
