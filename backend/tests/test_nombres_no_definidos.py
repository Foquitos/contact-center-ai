"""
Nombres globales usados dentro de funciones que su módulo nunca define.

POR QUÉ EXISTE
--------------
El 2026-08-21 se deployó una revisión de plantillas que moría con
`NameError: name 'logger' is not defined`: se habían agregado unas líneas de log a un
módulo que no tenía `logger`. Lo caro del caso es CUÁNDO fallaba —después de que Gemini
respondiera 200 OK, o sea con el llamado ya facturado— y por qué no lo agarró nada:

- `py_compile` no lo ve: es sintácticamente válido.
- Los tests del asistente mockeaban la función entera para no gastar tokens, así que ese
  código no lo ejecutaba nadie.

Este test cubre justamente lo que no cubre ninguno de los dos: recorre el árbol de
símbolos (`symtable`) de cada módulo del backend y marca los nombres que una función usa
como globales y que el módulo no define ni importa. Es barato, no necesita dependencias y
no ejecuta nada.

LÍMITE CONOCIDO: un módulo con `from x import *` trae nombres que no se pueden resolver
sin importar de verdad, así que se saltea (son 1 o 2 módulos viejos).

Test 100% offline: no toca DB, Gemini ni tokens.
Correr: pytest tests/test_nombres_no_definidos.py -m "not tokens"
"""
import ast
import builtins
import os
import symtable

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Los tests y los __pycache__ quedan afuera: lo que interesa es el código que se deploya.
EXCLUIDOS = ("tests", "__pycache__", ".venv", "storage", "downloads_cache")

CONOCIDOS = set(dir(builtins)) | {"__name__", "__file__", "__doc__", "__spec__", "__package__"}


def _modulos():
    for raiz, directorios, archivos in os.walk(BACKEND):
        directorios[:] = [d for d in directorios if d not in EXCLUIDOS]
        for archivo in archivos:
            if archivo.endswith(".py"):
                yield os.path.join(raiz, archivo)


def _tiene_import_estrella(codigo: str) -> bool:
    try:
        arbol = ast.parse(codigo)
    except SyntaxError:
        return True  # que se queje otro test
    return any(
        isinstance(nodo, ast.ImportFrom) and any(a.name == "*" for a in nodo.names)
        for nodo in ast.walk(arbol)
    )


def _definidos_en_el_modulo(tabla) -> set:
    return {
        simbolo.get_name() for simbolo in tabla.get_symbols()
        if simbolo.is_assigned() or simbolo.is_imported() or simbolo.is_namespace()
    }


def _nombres_sueltos(ruta: str):
    codigo = open(ruta, encoding="utf-8").read()
    if _tiene_import_estrella(codigo):
        return []
    tabla = symtable.symtable(codigo, ruta, "exec")
    definidos = _definidos_en_el_modulo(tabla) | CONOCIDOS
    sueltos = []

    def _recorrer(nodo, camino):
        for hijo in nodo.get_children():
            donde = f"{camino}.{hijo.get_name()}"
            for simbolo in hijo.get_symbols():
                if simbolo.is_global() and simbolo.get_name() not in definidos:
                    sueltos.append((donde, simbolo.get_name()))
            _recorrer(hijo, donde)

    _recorrer(tabla, os.path.relpath(ruta, BACKEND))
    return sueltos


@pytest.mark.parametrize("ruta", sorted(_modulos()), ids=lambda r: os.path.relpath(r, BACKEND))
def test_el_modulo_no_usa_nombres_que_no_define(ruta):
    sueltos = _nombres_sueltos(ruta)
    detalle = "\n".join(f"  {donde}: usa '{nombre}'" for donde, nombre in sueltos)
    assert not sueltos, f"Nombres que el módulo no define:\n{detalle}"
