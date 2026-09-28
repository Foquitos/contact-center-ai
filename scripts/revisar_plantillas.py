"""Revisión automática de TODAS las plantillas de auditoría activas.

POR QUÉ
-------
Las plantillas las escribe Calidad desde el editor, sin nadie que revise el conjunto.
Con 27 plantillas y 359 atributos vivos, los problemas que se acumulan no son de
redacción sino ESTRUCTURALES, y todos invisibles desde la pantalla: un enum cuya lista
de opciones ya no coincide con lo que responde la IA, un atributo de Calidad ponderada
cuyo valor quedó guardado como lista y por lo tanto no puntúa, una opción con la coma
faltante ('50% 60%' en vez de '50%','60%'), o un atributo que pide la transcripción del
llamado (ver AuditorIA/limites_texto.py).

Este script los busca a todos de una y arma un CSV para mandar a corregir.

CÓMO LEE
--------
Solo SELECTs sobre calidad.Plantillas / Atributos / AuditoriaDetalles. NO modifica nada
ni gasta tokens. Cruza la DEFINICIÓN del atributo con lo que la IA respondió realmente,
que es lo que separa una sospecha ("este prompt pide un texto largo") de un hecho
("devuelve 6.031 caracteres promedio en 42 auditorías").

Los chequeos empíricos miran los últimos 90 días y marcan aparte lo que YA NO OCURRE:
cuando alguien cambia las opciones de un enum, el histórico queda con los valores viejos
y sin esa distinción todo enum editado se lee como roto.

Correr:  cd backend && ../.venv/bin/python ../scripts/revisar_plantillas.py
Salida:  hallazgos.csv (junto al script) + resumen por consola.
"""
import csv
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict

from sqlalchemy import text

sys.path.insert(0, os.getcwd())

from app.database import engine  # noqa: E402
from AuditorIA import limites_texto  # noqa: E402

SALIDA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hallazgos.csv")
DATOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plantillas.json")

TIPOS_VALIDOS = {
    "string", "integer", "number", "boolean", "enum", "critical_audit",
    "array_string", "array_integer", "array_number", "array_boolean", "array_enum",
}
TIPOS_TEXTO = {"string", "array_string"}
TIPOS_ENUM = {"enum", "array_enum"}
CANON_CRITICAL = {"OK", "NO OK", "EC", "N/A"}
# Opciones que funcionan como "válvula de escape" para la IA. Incluye las que usan las
# plantillas reales sin llamarlas "Otros": 'No inferible', 'No recuperable', etc.
SALIDA_SEGURA = ("otro", "otros", "otra", "otras", "n/a", "na", "ninguno", "ninguna",
                 "sin datos", "sin especificar", "indeterminado", "no sabe", "no se puede determinar")
SALIDA_SEGURA_PREFIJOS = ("no aplica", "no aplicable", "no corresponde", "no inferible",
                          "no recuperable", "no especificad", "no mencionad", "no determinad",
                          "no identificad", "no detectad", "no informad", "no evaluable")


def es_salida_segura(op):
    o = norm(op)
    return o in SALIDA_SEGURA or o.startswith(SALIDA_SEGURA_PREFIJOS)


def norm(t):
    if not t:
        return ""
    plano = unicodedata.normalize("NFKD", str(t))
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", plano.lower()).strip()


# --------------------------------------------------------------------------- #
# Lectura                                                                      #
# --------------------------------------------------------------------------- #
SQL_ATRIBUTOS = """
SELECT e.Nombre AS Empresa, c.Nombre AS Campana, p.PlantillaID, p.Nombre AS Plantilla,
       p.SystemPrompt, p.Recordatorio, p.ModeloIA,
       a.AtributoID, a.NombreAtributo, a.PromptAdyacente, a.TipoDato, a.Restricciones,
       a.Orden, a.DarAviso, a.FrasesAviso, a.Ponderacion, a.EsOpcional
FROM calidad.Atributos a
JOIN calidad.Plantillas p ON p.PlantillaID = a.PlantillaID
JOIN calidad.Campanas   c ON c.CampanaID   = p.CampanaID
JOIN calidad.Empresas   e ON e.EmpresaID   = c.EmpresaID
WHERE a.IsActive = 1 AND p.IsActive = 1
ORDER BY e.Nombre, c.Nombre, p.PlantillaID, a.Orden, a.AtributoID
"""

# Evidencia real: qué devolvió la IA para cada atributo (largo del texto, cuántas veces
# respondió). Es lo que convierte una sospecha ("este atributo pide la transcripción")
# en un dato ("devuelve 3.500 caracteres promedio en 812 auditorías").
SQL_USO = """
SELECT d.AtributoID,
       COUNT(*)                          AS respuestas,
       AVG(LEN(d.ValorResultado) * 1.0)  AS largo_prom,
       MAX(LEN(d.ValorResultado))        AS largo_max,
       COUNT(DISTINCT d.ValorResultado)  AS valores_distintos,
       MAX(au.fecha_interaccion)         AS ultima
FROM calidad.AuditoriaDetalles d
JOIN calidad.Auditorias au ON au.AuditoriaID = d.AuditoriaID
GROUP BY d.AtributoID
"""

# Valores efectivamente devueltos en los atributos de opciones cerradas: sirve para
# detectar enums cuya lista no cubre lo que la IA responde.
SQL_VALORES = """
SELECT d.AtributoID, d.ValorResultado, COUNT(*) AS veces, MAX(au.fecha_interaccion) AS ultima
FROM calidad.AuditoriaDetalles d
JOIN calidad.Auditorias au ON au.AuditoriaID = d.AuditoriaID
JOIN calidad.Atributos a ON a.AtributoID = d.AtributoID
WHERE a.TipoDato IN ('enum', 'array_enum', 'critical_audit', 'boolean')
  AND au.fecha_interaccion >= DATEADD(day, -90, GETDATE())
GROUP BY d.AtributoID, d.ValorResultado
"""

# Valores que NO son un valor único sino una lista serializada ("['NO OK']"): el puntaje
# no los reconoce y en la grilla se ven con corchetes. Sin ventana: interesa el total.
SQL_SERIALIZADOS = """
SELECT d.AtributoID, COUNT(*) AS veces, MAX(au.fecha_interaccion) AS ultima,
       MIN(d.ValorResultado) AS ejemplo,
       SUM(CASE WHEN au.fecha_interaccion >= DATEADD(day, -30, GETDATE()) THEN 1 ELSE 0 END) AS recientes
FROM calidad.AuditoriaDetalles d
JOIN calidad.Auditorias au ON au.AuditoriaID = d.AuditoriaID
JOIN calidad.Atributos a ON a.AtributoID = d.AtributoID
WHERE a.TipoDato NOT IN ('string', 'array_string')   -- un feedback entre corchetes es texto, no una lista
  AND a.TipoDato NOT LIKE 'array%'
  AND d.ValorResultado LIKE '[[]%]'
  AND LEN(d.ValorResultado) < 60                     -- una lista de valores es corta; un texto largo no lo es
GROUP BY d.AtributoID
"""

with engine.connect() as conn:
    filas = [dict(r._mapping) for r in conn.execute(text(SQL_ATRIBUTOS))]
    uso = {r[0]: dict(respuestas=r[1], largo_prom=float(r[2] or 0), largo_max=int(r[3] or 0),
                      distintos=r[4], ultima=str(r[5]) if r[5] else None)
           for r in conn.execute(text(SQL_USO))}
    valores = defaultdict(list)
    for aid, val, veces, ultima in conn.execute(text(SQL_VALORES)):
        valores[aid].append((val, veces, str(ultima) if ultima else None))
    serializados = {r[0]: dict(veces=r[1], ultima=str(r[2]) if r[2] else None, ejemplo=r[3], recientes=r[4])
                    for r in conn.execute(text(SQL_SERIALIZADOS))}

print(f"Atributos activos leídos: {len(filas)} en "
      f"{len({f['PlantillaID'] for f in filas})} plantillas activas")

# --------------------------------------------------------------------------- #
# Chequeos                                                                     #
# --------------------------------------------------------------------------- #
hallazgos = []
resumen_global = defaultdict(int)


def flag(fila, severidad, categoria, detalle, sugerencia):
    hallazgos.append({
        "severidad": severidad,
        "categoria": categoria,
        "empresa": fila.get("Empresa"),
        "campana": fila.get("Campana"),
        "plantilla_id": fila.get("PlantillaID"),
        "plantilla": fila.get("Plantilla"),
        "atributo_id": fila.get("AtributoID"),
        "atributo": fila.get("NombreAtributo"),
        "tipo": fila.get("TipoDato"),
        "detalle": detalle,
        "sugerencia": sugerencia,
    })


def opciones_de(fila):
    """Lista de opciones del enum, o None si no hay/está rota."""
    crudo = fila.get("Restricciones")
    if not crudo:
        return None
    try:
        data = json.loads(crudo)
    except (TypeError, ValueError):
        return "ROTO"
    if isinstance(data, dict):
        ops = data.get("enum")
    else:
        ops = data
    if ops is None:
        return None
    if not isinstance(ops, list):
        ops = [ops]
    return [str(o) for o in ops]


import datetime
ULTIMOS_30 = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()

por_plantilla = defaultdict(list)
for f in filas:
    por_plantilla[f["PlantillaID"]].append(f)

# --- Chequeos por atributo ---
for f in filas:
    tipo = (f["TipoDato"] or "").strip().lower()
    nombre = f["NombreAtributo"] or ""
    prompt = f["PromptAdyacente"] or ""
    p = norm(prompt)
    n = norm(nombre)
    ops = opciones_de(f)
    u = uso.get(f["AtributoID"], {})

    # 1. Tipo que el sistema no sabe traducir -> la plantilla NO puede auditar.
    if tipo not in TIPOS_VALIDOS:
        flag(f, "BLOQUEANTE", "tipo desconocido",
             f"TipoDato='{f['TipoDato']}' no está entre los tipos soportados",
             "Corregir el tipo: cualquier auditoría con esta plantilla falla antes de llamar a la IA")

    # 2. Enum sin opciones -> schema inválido / campo libre.
    if tipo in TIPOS_ENUM:
        if ops == "ROTO":
            flag(f, "BLOQUEANTE", "restricciones ilegibles",
                 "El JSON de Restricciones no se puede parsear",
                 "Reescribir las opciones desde el editor de plantillas")
        elif not ops:
            flag(f, "BLOQUEANTE", "enum sin opciones",
                 "Tipo de selección pero sin lista de opciones cargada",
                 "Cargar las opciones, o cambiar el tipo a Texto/Sí-No según lo que se quiera medir")

    # 3. Pedido de transcripción dentro de un atributo de texto.
    if tipo in TIPOS_TEXTO:
        motivo = limites_texto.pide_transcripcion(nombre, prompt)
        if motivo:
            largo = f" (devuelve {u.get('largo_prom', 0):.0f} caracteres promedio, máx {u.get('largo_max', 0)}, en {u.get('respuestas', 0)} auditorías)" if u else ""
            flag(f, "ALTA", "pide transcripción",
                 f"El atributo {motivo}{largo}",
                 "Sacar el atributo y usar la transcripción del sistema (tilde 'Transcripción' al auditar, "
                 "o la cola de Auditorías Realizadas)")

    # 4. Texto libre que en los hechos devuelve textos larguísimos (aunque el prompt no
    #    diga "transcribí"): mismo costo, mismo problema.
    if tipo in TIPOS_TEXTO and u.get("largo_prom", 0) > 900:
        flag(f, "MEDIA", "texto muy largo",
             f"Devuelve {u['largo_prom']:.0f} caracteres promedio (máx {u['largo_max']}) en {u['respuestas']} auditorías",
             "Acotar el prompt ('respondé en dos líneas') o pasarlo a un tipo cerrado")

    # 5. Ponderación mal puesta.
    pond = float(f["Ponderacion"] or 0)
    if tipo == "critical_audit" and pond == 0:
        flag(f, "ALTA", "critical sin peso",
             "Atributo de Calidad ponderada con Ponderacion = 0: se evalúa pero NO puntúa",
             "Asignarle un peso, o cambiarlo a Sí/No si no debe puntuar")
    if tipo != "critical_audit" and pond > 0:
        flag(f, "BAJA", "peso inútil",
             f"Ponderacion = {pond:g} en un tipo que no puntúa: el peso se ignora",
             "Poner la ponderación en 0 para no confundir, o convertirlo a Calidad ponderada")

    # 6. Opciones de un critical_audit fuera del canon OK/NO OK/EC/N/A.
    if tipo == "critical_audit" and isinstance(ops, list) and ops:
        raras = [o for o in ops if o.strip().upper() not in CANON_CRITICAL]
        if raras:
            flag(f, "ALTA", "opciones no canónicas",
                 f"Opciones fuera de OK/NO OK/EC/N/A: {raras}",
                 "El puntaje solo entiende OK/NO OK/EC/N/A: cualquier otra respuesta no suma ni resta")

    # 7. Enum sin salida segura.
    if tipo in TIPOS_ENUM and isinstance(ops, list) and ops:
        # Solo cuando hay razón para creer que hace falta: una lista larga (más chances de
        # que el caso real no esté), o un atributo obligatorio de una plantilla que audita
        # mucho. Marcar los 164 enums de dos opciones sería ruido, no un hallazgo.
        sin_salida = not any(es_salida_segura(o) for o in ops)
        if sin_salida and len(ops) >= 4 and not f["EsOpcional"]:
            flag(f, "MEDIA", "enum sin salida segura",
                 f"{len(ops)} opciones y ninguna sirve para 'no aplica / otros': {ops}",
                 "Agregar 'Otros' o 'No aplica' (o marcar el atributo como Opcional): con la lista "
                 "cerrada, la IA tiene que elegir igual una opción aunque ninguna describa el llamado")
        elif sin_salida and not f["EsOpcional"]:
            resumen_global["enum_sin_salida_corto"] += 1
        for o in ops:
            partes = o.split()
            if len(partes) > 1 and sum(1 for x in partes if re.fullmatch(r"\d+%|\d+", x)) > 1:
                flag(f, "MEDIA", "opción con coma faltante",
                     f"La opción {o!r} parece dos opciones pegadas dentro de {ops}",
                     "Separarlas con coma: hoy es UNA sola opción y la IA no puede elegir una de las dos")
        dup = [o for o in {norm(x) for x in ops} if [norm(y) for y in ops].count(o) > 1]
        if dup:
            flag(f, "MEDIA", "opciones duplicadas", f"Opciones repetidas: {dup}",
                 "Dejar una sola por opción")

    # 8. Tipo mal puesto: el prompt pide un Sí/No pero el tipo es texto.
    if tipo in TIPOS_TEXTO:
        if re.search(r"\b(respond[ea]|contest[ae]|indic[ae]|marc[ae])\w*\s+(con\s+)?(si|s[ií]/no|si o no)\b", p) or \
           re.search(r"^\s*[¿?]?\s*(el |la |los |las )?\w+\s+.{0,80}\?\s*$", p) and re.search(r"\b(saludo|ofrecio|cumplio|verifico|menciono|utilizo|realizo|pregunto|informo|se despidio)\b", p):
            flag(f, "MEDIA", "tipo mal puesto",
                 "El prompt plantea una pregunta cerrada (Sí/No) pero el tipo es texto libre",
                 "Pasarlo a Sí/No (o a Calidad ponderada si debe puntuar): en texto no se puede graficar ni promediar")

    # 9. Tipo mal puesto: el prompt enumera opciones cerradas pero el tipo es texto.
    if tipo in TIPOS_TEXTO and re.search(r"(elegi|eleg[ií]|seleccion\w*|responde con|clasific\w*)[^.]{0,60}[:(][^.)]{0,80},[^.)]{0,80}", p):
        flag(f, "MEDIA", "tipo mal puesto",
             "El prompt enumera opciones cerradas pero el tipo es texto libre",
             "Pasarlo a Selección Única (enum) con esas opciones: así se puede filtrar y graficar")

    # 10. Numérico con prompt de Sí/No, o Sí/No con prompt de escala.
    if tipo in {"integer", "number"} and re.search(r"\b(si o no|cumple|saludo|se despidio|verifico)\b", p):
        flag(f, "MEDIA", "tipo mal puesto",
             "Tipo numérico pero el prompt pide un cumple/no cumple",
             "Pasarlo a Sí/No o a Calidad ponderada")
    if tipo == "boolean" and re.search(r"\b(del 1 al|puntaje|escala|cuant[oa]s|nota)\b", p):
        flag(f, "MEDIA", "tipo mal puesto",
             "Tipo Sí/No pero el prompt pide un número o una escala",
             "Pasarlo a Número, o reformular el prompt como pregunta cerrada")

    # 11. Alerta de calidad que nunca puede dispararse.
    if f["DarAviso"] and not (f["FrasesAviso"] or "").strip():
        flag(f, "MEDIA", "alerta sin frases",
             "DarAviso = 1 pero FrasesAviso está vacío: la alerta nunca se dispara",
             "Cargar las palabras/frases que deben disparar el aviso, o apagar la alerta")

    # 12. Prompt vacío o demasiado corto para dar criterio.
    if len(prompt.strip()) < 25:
        flag(f, "MEDIA", "prompt sin criterio",
             f"Prompt de {len(prompt.strip())} caracteres: {prompt.strip()!r}",
             "Explicar qué evidencia buscar y qué cuenta como cumplido: es lo que más impacta en el resultado")

    # 13. EsOpcional en un critical_audit (el editor no lo permite; si está, quedó viejo).
    if tipo == "critical_audit" and f["EsOpcional"]:
        flag(f, "BAJA", "opcional en critical",
             "Marcado como Opcional siendo de Calidad ponderada",
             "Destildar Opcional y usar la opción N/A, que además renormaliza el puntaje")

    # 14. Respuestas fuera de la lista de opciones (evidencia real).
    if tipo in TIPOS_ENUM | {"critical_audit"} and isinstance(ops, list) and ops:
        permitidas = {norm(o) for o in ops}
        fuera = [(v, c, u2) for v, c, u2 in valores.get(f["AtributoID"], [])
                 if v is not None and norm(v) not in permitidas
                 and tipo != "array_enum"  # en listas el valor viaja serializado
                 and not (tipo == "critical_audit" and norm(v) in {"n/a", "na", "no aplica"})]
        total_fuera = sum(c for _, c, _ in fuera)
        if total_fuera:
            muestra = sorted(fuera, key=lambda x: -x[1])[:4]
            ult = max((m[2] or "") for m in fuera if m[2])[:10] if any(m[2] for m in fuera) else ""
            # Si el atributo siguió auditando DESPUÉS del último valor raro, el desajuste
            # ya se corrigió: lo que queda es histórico mezclado, no un problema abierto.
            ult_uso = (u.get("ultima") or "")[:10]
            vigente = ult >= ULTIMOS_30
            flag(f, "ALTA" if vigente else "BAJA", "respuestas fuera de la lista",
                 f"{total_fuera} respuestas no están entre las opciones (última {ult}, el atributo "
                 f"siguió auditando hasta {ult_uso}): {[m[0] for m in muestra]}"
                 + ("" if vigente else " — ya no ocurre, quedó del cambio de opciones"),
                 "La lista de opciones no coincide con lo que pide el prompt: alinearlas. "
                 "Los valores que no están en la lista rompen los gráficos y los filtros")

    # 14.b Valor serializado como lista en un atributo de valor único.
    if f["AtributoID"] in serializados:
        sz = serializados[f["AtributoID"]]
        vigente = sz["recientes"] > 0 and (sz["ultima"] or "") >= ULTIMOS_30
        flag(f, "ALTA" if vigente else "MEDIA", "valor guardado como lista",
             f"{sz['veces']} respuestas guardadas como lista (ej. {sz['ejemplo']!r}), última {str(sz['ultima'])[:10]}"
             + ("" if vigente else " — ya no ocurre, queda el histórico"),
             "Un critical_audit con ese valor NO puntúa (el puntaje solo entiende OK/NO OK/EC/N/A) y en los "
             "gráficos 'Segurar' y \"['Segurar']\" cuentan como dos valores distintos. Viene de cuando el "
             "atributo era de tipo lista: hay que normalizar el histórico")

    # 15. Atributo activo que nunca se respondió (y la plantilla sí auditó).
    if not u.get("respuestas") and any(uso.get(o["AtributoID"], {}).get("respuestas") for o in por_plantilla[f["PlantillaID"]]):
        flag(f, "BAJA", "nunca respondido",
             "La plantilla auditó pero este atributo no tiene ni una respuesta guardada",
             "Revisar si quedó de una versión vieja: si no se usa, desactivarlo")

# --- Chequeos por plantilla ---
for pid, atrs in por_plantilla.items():
    cab = atrs[0]
    criticos = [a for a in atrs if (a["TipoDato"] or "").lower() == "critical_audit"]
    suma = sum(float(a["Ponderacion"] or 0) for a in criticos)

    if criticos and suma == 0:
        flag(cab, "ALTA", "plantilla sin puntaje",
             f"{len(criticos)} atributos de Calidad ponderada pero los pesos suman 0",
             "Asignar pesos: hoy el puntaje del llamado no se puede calcular")
    if not criticos:
        flag(cab, "BAJA", "plantilla sin puntaje",
             "No tiene atributos de Calidad ponderada: las auditorías no llevan nota",
             "Si se espera un puntaje, convertir los criterios de cumplimiento a Calidad ponderada")
    if not (cab["SystemPrompt"] or "").strip():
        flag(cab, "MEDIA", "plantilla sin System Prompt",
             "La plantilla no define el rol/criterio de la IA",
             "Cargar el System Prompt: sin él la IA improvisa el criterio de evaluación")

    nombres = defaultdict(list)
    for a in atrs:
        nombres[norm(a["NombreAtributo"])].append(a["AtributoID"])
    for nom, ids in nombres.items():
        if len(ids) > 1:
            flag(cab, "BLOQUEANTE", "atributos duplicados",
                 f"{len(ids)} atributos con el mismo nombre ({nom!r}, IDs {ids})",
                 "Renombrar: comparten la misma clave en la respuesta de la IA, así que una pisa a la otra "
                 "y se pierde la respuesta")

# --------------------------------------------------------------------------- #
# Salida                                                                       #
# --------------------------------------------------------------------------- #
orden_sev = {"BLOQUEANTE": 0, "ALTA": 1, "MEDIA": 2, "BAJA": 3}
hallazgos.sort(key=lambda h: (orden_sev[h["severidad"]], h["empresa"] or "", h["plantilla"] or "", h["atributo"] or ""))

with open(SALIDA, "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.DictWriter(fh, fieldnames=list(hallazgos[0].keys()) if hallazgos else
                       ["severidad", "categoria", "empresa", "campana", "plantilla_id", "plantilla",
                        "atributo_id", "atributo", "tipo", "detalle", "sugerencia"], delimiter=";")
    w.writeheader()
    w.writerows(hallazgos)

with open(DATOS, "w", encoding="utf-8") as fh:
    json.dump({"atributos": [{k: (str(v) if not isinstance(v, (int, float, type(None), str)) else v)
                              for k, v in f.items()} for f in filas],
               "uso": {str(k): v for k, v in uso.items()}}, fh, ensure_ascii=False, default=str)

print(f"\nHallazgos: {len(hallazgos)} -> {SALIDA}")
por_sev = defaultdict(int)
por_cat = defaultdict(int)
for h in hallazgos:
    por_sev[h["severidad"]] += 1
    por_cat[(h["severidad"], h["categoria"])] += 1
print("\nAgrupados (no se listan uno por uno):")
for k, v in resumen_global.items():
    print(f"   {v:>4}  {k}")

for sev in ("BLOQUEANTE", "ALTA", "MEDIA", "BAJA"):
    if por_sev[sev]:
        print(f"\n{sev}: {por_sev[sev]}")
        for (s, cat), n in sorted(por_cat.items(), key=lambda x: -x[1]):
            if s == sev:
                print(f"   {n:>4}  {cat}")
