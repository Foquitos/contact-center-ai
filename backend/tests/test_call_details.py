"""
Catálogo de los datos del llamado que la IA recibe además del audio (Call_details).

`gemini.py::prompt_details` le agrega a cada auditoría un bloque `## Call_details` con una
línea por columna del DataFrame que armó el builder de la campaña. `AuditorIA/call_details.py`
declara qué campos son, por empresa, para que el asistente de plantillas y el chequeo de
salud razonen sobre lo que la IA REALMENTE tiene y no sobre "solo el audio".

Un catálogo declarado se pudre en silencio, y ahí es peor que no tenerlo: el asistente
propondría criterios sobre campos que ya no existen. Por eso este test lee la cláusula
SELECT de cada builder y verifica que el catálogo la cubra. Si alguien agrega una columna
a un builder, este test falla hasta que se sume al catálogo.

Test 100% offline: no toca DB, Gemini ni tokens.
Correr: pytest tests/test_call_details.py -m "not tokens"
"""
import ast
import pathlib
import re

import pytest

from AuditorIA import call_details as cd
from AuditorIA import gemini
from AuditorIA import senales_prompt
from AuditorIA import asistente_plantillas as ap


RUTA_BUILDERS = pathlib.Path(__file__).resolve().parents[1] / "AuditorIA" / "SQL_query.py"

# Builder -> clave del catálogo. Los que no usan `select_clause` (Voltara, que arma la
# query entera en una f-string, y la subida de CSV, que recorta el DataFrame a mano en
# Cardnet.py) tienen su propio test más abajo.
BUILDERS = {
    "get_filtered_data_mitrol_puro": None,          # None = campos por defecto
    "get_filtered_data_Hidra": "HIDRA",
    "get_filtered_data_Hidra_comercial": "HIDRA Comercial",
    "get_filtered_data_Aurora_salud": "AuroraSalud",
    "get_filtered_data_ALARMIX": "ALARMIX",
    "get_filtered_data_Vitalis_Salud": "Vitalis Salud",
    "get_filtered_data_Farmalux": "Farmalux",
    "get_filtered_data_Vantix": "Vantix",
    "get_filtered_data_Benefix": "Benefix",
}


def _sin_comentarios(sql: str) -> str:
    return " ".join(re.sub(r"--.*", "", linea) for linea in sql.splitlines())


def _partir_en_columnas(sql: str) -> list:
    """Corta el SELECT por las comas de nivel 0 (las de adentro de STRING_AGG no cuentan)."""
    partes, prof, actual = [], 0, ""
    for ch in sql:
        if ch == "(":
            prof += 1
        elif ch == ")":
            prof -= 1
        if ch == "," and prof == 0:
            partes.append(actual)
            actual = ""
        else:
            actual += ch
    if actual.strip():
        partes.append(actual)
    return [p.strip() for p in partes if p.strip()]


def _nombre_de_columna(expresion: str) -> str:
    """El nombre con el que la columna llega al DataFrame (y por lo tanto al prompt).

    Tres formas conviven en los builders: `x AS [Nombre]`, `min(x) Nombre` y `tabla.Campo`.
    El `AS` se busca a profundidad 0 para no confundirlo con el de `CAST(x AS VARCHAR)`.
    """
    texto = " ".join(expresion.split())
    prof, corte = 0, -1
    for m in re.finditer(r"[()]|\sAS\s", texto, re.IGNORECASE):
        if m.group() == "(":
            prof += 1
        elif m.group() == ")":
            prof -= 1
        elif prof == 0:
            corte = m.end()
    token = texto[corte:].strip() if corte >= 0 else texto

    if corte < 0:  # sin AS: el alias es lo que va después del último ')' o el campo suelto
        token = token[token.rfind(")") + 1:].strip() or texto
    # Un nombre entre corchetes puede tener espacios (`ir.[Nro. ODT]`), así que se toma
    # entero antes de partir por espacios o por el prefijo de tabla.
    if "[" in token:
        return token[token.rfind("[") + 1:token.rfind("]")]
    return token.split()[-1].split(".")[-1]


def _columnas_del_builder(nombre_funcion: str) -> list:
    arbol = ast.parse(RUTA_BUILDERS.read_text())
    for nodo in arbol.body:
        if not isinstance(nodo, ast.FunctionDef) or nodo.name != nombre_funcion:
            continue
        for sub in ast.walk(nodo):
            if isinstance(sub, ast.Assign) and any(
                    getattr(t, "id", None) == "select_clause" for t in sub.targets):
                valor = sub.value
                if isinstance(valor, ast.Constant):
                    crudo = valor.value
                elif isinstance(valor, ast.JoinedStr):
                    # f-string: los tramos interpolados (un id calculado) se ignoran.
                    crudo = "".join(v.value for v in valor.values if isinstance(v, ast.Constant))
                else:
                    continue
                columnas = [_nombre_de_columna(c) for c in _partir_en_columnas(_sin_comentarios(crudo))]
                return [c for c in columnas if c and c not in cd.COLUMNAS_INTERNAS]
    raise AssertionError(f"No se encontró el select_clause de {nombre_funcion}")


def _columnas_de_la_subida_csv() -> list:
    """Las columnas con las que queda el DataFrame de CSV (AuditorIA/Cardnet.py).

    No sale de un `select_clause` como los demás: `subida_interacciones_csv` recorta con
    un `df[[...]]` y después renombra. Se lee la PRIMERA lista larga (la del recorte,
    que es la que decide qué sobrevive) y se le aplica el mapa de renombres.
    """
    ruta = pathlib.Path(__file__).resolve().parents[1] / "AuditorIA" / "Cardnet.py"
    funcion = next(n for n in ast.parse(ruta.read_text()).body
                   if isinstance(n, ast.FunctionDef) and n.name == "subida_interacciones_csv")
    listas = [[e.value for e in n.elts] for n in ast.walk(funcion)
              if isinstance(n, ast.List) and len(n.elts) > 10
              and all(isinstance(e, ast.Constant) for e in n.elts)]
    renombres = {}
    for sub in ast.walk(funcion):
        if isinstance(sub, ast.Call) and getattr(sub.func, "attr", "") == "rename":
            for kw in sub.keywords:
                if kw.arg == "columns" and isinstance(kw.value, ast.Dict):
                    renombres = {k.value: v.value for k, v in zip(kw.value.keys, kw.value.values)}
    finales = [renombres.get(c, c) for c in listas[0]]
    return [c for c in finales if c not in cd.COLUMNAS_INTERNAS]


def test_el_catalogo_de_csv_cubre_lo_que_arma_la_subida():
    """CSV es la campaña que más audita (unas 7.000 auditorías por mes) y su DataFrame no
    se arma con un SELECT sino a mano en Cardnet.py, así que necesita su propio control."""
    reales = _columnas_de_la_subida_csv()
    catalogo = cd.CAMPOS_POR_EMPRESA["CSV"]
    assert sorted(reales) == sorted(catalogo), (
        f"El Call_details de CSV cambió. Faltan: {[c for c in reales if c not in catalogo]}. "
        f"Sobran: {[c for c in catalogo if c not in reales]}."
    )


def test_el_catalogo_de_voltara_cubre_el_select_final():
    """Voltara tampoco usa `select_clause`: arma la query entera en una f-string. Se toma
    el SELECT externo (el último antes de `FROM voltara_base`), que es el que define las
    columnas del DataFrame — el de adentro del CTE trae auxiliares que no salen."""
    fuente = ast.parse(RUTA_BUILDERS.read_text())
    funcion = next(n for n in fuente.body
                   if isinstance(n, ast.FunctionDef) and n.name == "get_filtered_data_Voltara")
    query = max(("".join(v.value for v in nodo.values if isinstance(v, ast.Constant))
                 for nodo in ast.walk(funcion) if isinstance(nodo, ast.JoinedStr)), key=len)
    fin = query.index("FROM voltara_base")
    ini = query.rindex("SELECT", 0, fin) + len("SELECT")

    reales = [_nombre_de_columna(c) for c in _partir_en_columnas(_sin_comentarios(query[ini:fin]))]
    reales = [c for c in reales if c not in cd.COLUMNAS_INTERNAS]
    catalogo = cd.CAMPOS_POR_EMPRESA["Voltara"]
    assert sorted(reales) == sorted(catalogo), (
        f"El Call_details de Voltara cambió. Faltan: {[c for c in reales if c not in catalogo]}. "
        f"Sobran: {[c for c in catalogo if c not in reales]}."
    )


@pytest.mark.parametrize("funcion,clave", sorted(BUILDERS.items()))
def test_el_catalogo_cubre_lo_que_selecciona_el_builder(funcion, clave):
    catalogo = cd.CAMPOS_POR_EMPRESA[clave] if clave else cd.CAMPOS_POR_DEFECTO
    faltantes = [c for c in _columnas_del_builder(funcion) if c not in catalogo]
    assert not faltantes, (
        f"{funcion} selecciona columnas que no están en el catálogo de Call_details: "
        f"{faltantes}. Sumalas a AuditorIA/call_details.py o el asistente de plantillas "
        "va a razonar sobre datos que la IA no tiene (o al revés)."
    )


def test_las_columnas_internas_son_las_mismas_que_omite_el_prompt():
    """Si gemini deja de omitir una columna, el catálogo tiene que enterarse: esa columna
    empieza a llegarle a la IA y el asistente debería poder usarla."""
    assert cd.COLUMNAS_INTERNAS == gemini.COLUMNAS_OMITIDAS_EN_DETALLES


def test_hidra_comercial_gana_sobre_hidra():
    """El dispatch real compara por pertenencia y en orden: si 'HIDRA' se evaluara primero,
    HIDRA Comercial auditaría creyendo que recibe campos que no recibe."""
    assert cd.campos_de_empresa("HIDRA Comercial") == cd.CAMPOS_POR_EMPRESA["HIDRA Comercial"]
    assert cd.campos_de_empresa("HIDRA") == cd.CAMPOS_POR_EMPRESA["HIDRA"]


def test_una_empresa_desconocida_cae_en_el_builder_generico():
    """Igual que el `else` final del dispatch: Mitrol puro."""
    assert cd.campos_de_empresa("Empresa Nueva SA") == cd.CAMPOS_POR_DEFECTO
    assert cd.campos_de_empresa(None) == cd.CAMPOS_POR_DEFECTO


def test_el_bloque_del_prompt_avisa_lo_que_hace_falta():
    """Las tres advertencias no son decorativas: sin ellas el asistente propone criterios
    sobre campos inventados, o que se rompen cuando el campo viene vacío."""
    bloque = cd.bloque_para_prompt(cd.campos_de_empresa("HIDRA"))
    assert "'Tipificación'" in bloque
    assert "NO inventes campos" in bloque
    assert "VACÍO" in bloque
    assert "no son lo que se dijo en el audio" in bloque
    assert cd.bloque_para_prompt([]) == ""


# --------------------------------------------------------------------------- #
# El catálogo cambia lo que las señales consideran un problema                 #
# --------------------------------------------------------------------------- #
# Era el falso positivo más caro de `pide_dato_externo`: hay campañas donde el CRM, el
# número de caso o los comentarios de Salesforce SÍ viajan en el Call_details. Marcar eso
# como "la IA no puede abrir nada" es marcar como error un prompt que está bien.
PROMPT_CRM = ("Corroborá contra el CRM si el operador dejó registrada la gestión del "
              "reclamo que planteó el cliente durante el llamado.")


def _claves(prompt, campos):
    return {c for c, _m, _s in senales_prompt.senales_de_texto("Gestión", prompt, "boolean",
                                                              campos_contexto=campos)}


def test_no_marca_el_campo_que_si_viaja_con_el_audio():
    """Mitrol puro manda 'CRM' en el Call_details: pedirlo no es pedir lo imposible."""
    assert "CRM" in cd.CAMPOS_POR_DEFECTO
    assert "pide_dato_externo" not in _claves(PROMPT_CRM, cd.CAMPOS_POR_DEFECTO)


def test_sigue_marcando_el_campo_que_no_existe_en_esa_campana():
    """El mismo prompt en ALARMIX sí es un problema: ahí el Call_details no trae ningún CRM."""
    campos = cd.campos_de_empresa("ALARMIX")
    assert "pide_dato_externo" in _claves(PROMPT_CRM, campos)


def test_el_mensaje_dice_con_que_datos_SI_cuenta():
    """Decir «no se puede» no enseña nada; decir con qué datos sí cuenta, sí."""
    campos = cd.campos_de_empresa("ALARMIX")
    mensaje = next(m for c, m, _ in senales_prompt.senales_de_texto(
        "Gestión", PROMPT_CRM, "boolean", campos_contexto=campos) if c == "pide_dato_externo")
    assert "'Tipificación'" in mensaje and "recibe el audio y estos datos" in mensaje


def test_sin_catalogo_se_comporta_como_antes():
    """Los llamadores que todavía no resuelven los campos no pierden la señal."""
    assert "pide_dato_externo" in _claves(PROMPT_CRM, None)


# --------------------------------------------------------------------------- #
# El asistente recibe el catálogo dentro del prompt                            #
# --------------------------------------------------------------------------- #
@pytest.fixture
def prompt_capturado(monkeypatch):
    """Intercepta el llamado al modelo y devuelve la system_instruction que se le mandó."""
    capturado = {}

    def _falso(system_instruction, user_text, response_schema, **kwargs):
        capturado["system"] = system_instruction
        capturado["user"] = user_text
        return {"hay_cambios": False, "prompt_mejorado": "", "resumen_cambios": [], "nota": ""}

    monkeypatch.setattr(ap, "_generar_json", _falso)
    return capturado


def test_el_asistente_sabe_que_datos_recibe_la_ia(prompt_capturado):
    """Es todo el punto: sin esto el asistente propone criterios sobre campos que no
    existen, o le pide a la IA que deduzca del audio algo que ya tiene servido."""
    ap.mejorar_prompt(
        campo="atributo",
        texto_actual="¿Saludó?",
        campos_contexto=cd.campos_de_empresa("HIDRA"),
    )
    system = prompt_capturado["system"]
    assert "Call_details" in system
    assert "'Tipificación'" in system and "'Nro. ODT'" in system


def test_sin_campos_el_prompt_queda_como_estaba(prompt_capturado):
    """Los flujos que no resuelven la campaña (o una empresa sin catálogo) no tienen que
    ver un bloque vacío colgado en el medio del prompt."""
    ap.mejorar_prompt(campo="atributo", texto_actual="¿Saludó?")
    assert "Call_details" not in prompt_capturado["system"]
