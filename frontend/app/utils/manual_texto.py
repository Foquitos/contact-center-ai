"""Convierte el manual renderizado (templates/documentacion.html) a texto plano
para dárselo al asistente de ayuda (la burbuja de todas las pantallas).

Por qué se extrae del HTML y no hay una copia en markdown del manual: si hubiera dos
fuentes, tarde o temprano el asistente respondería con un manual viejo. Acá la fuente
es LA MISMA que lee el usuario, ya renderizada con sus permisos, así que es imposible
que el asistente sepa algo distinto de lo que dice la pantalla.

De cada `<section class="doc-section">` se conserva el id, que es el ancla con la que
el asistente linkea la respuesta (`/documentacion#plantillas`). El resto se aplana a
markdown simple: títulos, listas, definiciones y tablas. Se descartan el índice
lateral, el buscador y la navegación: son controles, no contenido.
"""
import copy
import re

from bs4 import BeautifulSoup, NavigableString, Tag

# Tags que no aportan contenido al manual (o cuyo texto ya está en otro lado).
_IGNORADOS = {"script", "style", "nav", "aside", "button", "input", "select", "svg"}


def _limpiar(texto: str) -> str:
    """Normaliza espacios. El segundo paso pega la puntuación al texto: separar los
    inline con un espacio (necesario para que "…la<strong>plantilla</strong>" no quede
    pegado) deja "Guardar Cambios ," cada vez que la negrita termina una oración."""
    texto = re.sub(r"\s+", " ", texto or "").strip()
    return re.sub(r"\s+([,.;:!?%)\]])", r"\1", texto)


def _texto_plano(elemento) -> str:
    """Texto de un elemento con sus inline (negritas, links, código) aplanados.

    Los `<i class="bi ...">` de Bootstrap Icons no tienen texto, así que desaparecen
    solos; lo único que hace falta es normalizar los espacios que dejan."""
    if isinstance(elemento, NavigableString):
        return _limpiar(str(elemento))
    return _limpiar(elemento.get_text(" ", strip=True))


def _lista(elemento: Tag, sangria: int, salida: list) -> None:
    """Aplana `<ul>`/`<ol>`, incluidas las anidadas (que se sangran)."""
    ordenada = elemento.name == "ol"
    for i, li in enumerate(elemento.find_all("li", recursive=False), start=1):
        anidadas = li.find_all(["ul", "ol"], recursive=False)
        # El texto propio del <li>, sin el de las listas que cuelgan de él.
        propio = copy.copy(li)
        for sub in propio.find_all(["ul", "ol"], recursive=False):
            sub.decompose()
        texto = _texto_plano(propio)
        vinieta = f"{i}." if ordenada else "-"
        if texto:
            salida.append(f"{' ' * sangria}{vinieta} {texto}")
        for sub in anidadas:
            _lista(sub, sangria + 2, salida)


def _tabla(elemento: Tag, salida: list) -> None:
    """Tabla a markdown. Se arma a mano (y no con get_text) porque las comparaciones
    del manual —planes, columnas del dashboard, estados— se entienden por fila."""
    filas = []
    for tr in elemento.find_all("tr"):
        celdas = [_texto_plano(td) for td in tr.find_all(["th", "td"], recursive=False)]
        if any(celdas):
            filas.append(celdas)
    if not filas:
        return
    ancho = max(len(f) for f in filas)
    for indice, fila in enumerate(filas):
        fila = fila + [""] * (ancho - len(fila))
        salida.append("| " + " | ".join(fila) + " |")
        if indice == 0:
            salida.append("|" + "|".join([" --- "] * ancho) + "|")


def _definiciones(elemento: Tag, salida: list) -> None:
    """`<dl>` (glosario y "problemas frecuentes") a pares término: definición."""
    termino = None
    for hijo in elemento.find_all(["dt", "dd"], recursive=False):
        if hijo.name == "dt":
            termino = _texto_plano(hijo)
        else:
            definicion = _texto_plano(hijo)
            if termino and definicion:
                salida.append(f"- **{termino}**: {definicion}")
            elif definicion:
                salida.append(f"- {definicion}")
            termino = None


def _bloques(nodo: Tag, salida: list, nivel_base: int) -> None:
    """Recorre el árbol emitiendo un bloque de markdown por elemento de contenido.

    `nivel_base` es el nivel de título con el que arranca la sección (2 = '##'), para
    que la jerarquía del manual (h2 > h3 > h4) sobreviva a la conversión: el modelo la
    usa para saber qué es subtema de qué."""
    for hijo in nodo.children:
        if isinstance(hijo, NavigableString):
            texto = _limpiar(str(hijo))
            if texto:
                salida.append(texto)
            continue
        if not isinstance(hijo, Tag) or hijo.name in _IGNORADOS:
            continue

        nombre = hijo.name
        if nombre in ("h1", "h2", "h3", "h4", "h5", "h6"):
            texto = _texto_plano(hijo)
            if texto:
                nivel = min(nivel_base + int(nombre[1]) - 2, 6)
                salida.append(f"\n{'#' * nivel} {texto}")
        elif nombre in ("p", "blockquote"):
            texto = _texto_plano(hijo)
            if texto:
                salida.append(texto)
        elif nombre in ("ul", "ol"):
            _lista(hijo, 0, salida)
        elif nombre == "dl":
            _definiciones(hijo, salida)
        elif nombre == "table":
            _tabla(hijo, salida)
        elif nombre == "pre":
            salida.append(f"```\n{hijo.get_text()}\n```")
        else:
            # Contenedores de maquetado (div, section, main, figure…): se atraviesan.
            _bloques(hijo, salida, nivel_base)


def html_a_texto(html: str) -> str:
    """Manual renderizado -> texto para el prompt del asistente.

    Solo toma las `section.doc-section`: es exactamente lo que el usuario tiene
    habilitado (el gateo por permisos ya lo hizo Jinja al renderizar)."""
    soup = BeautifulSoup(html, "html.parser")
    partes = ["# Manual de uso de Contact Center AI"]

    for seccion in soup.select("section.doc-section"):
        ancla = seccion.get("id") or ""
        encabezado = seccion.find(["h2", "h3"])
        titulo = _texto_plano(encabezado) if encabezado else (ancla or "Sección")
        if encabezado is not None:
            encabezado.decompose()

        referencia = f" (link: /documentacion#{ancla})" if ancla else ""
        bloques: list = [f"\n## {titulo}{referencia}"]
        _bloques(seccion, bloques, nivel_base=2)
        partes.append("\n".join(b for b in bloques if b.strip()))

    return "\n\n".join(partes).strip()
