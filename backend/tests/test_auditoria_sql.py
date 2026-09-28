"""
Valida que el SQL generado por cada builder de campaña sea correcto, para cada
combinación de filtros.

NO ejecuta las queries (no escanea datos ni gasta tokens de IA): captura el SQL
que cada builder pasaría a `pd.read_sql` y lo valida contra SQL Server con
`describe_first_result_set` (sintaxis + nombres) o PARSEONLY (sintaxis), según
corresponda. Ver conftest.py -> validar_sql.
"""
import re
from datetime import datetime

import pandas as pd
import pytest

import AuditorIA.SQL_query as q


# --------------------------------------------------------------------------- #
# Captura del SQL generado sin tocar la base ni descargar nada                #
# --------------------------------------------------------------------------- #
class _SQLCapturado(Exception):
    def __init__(self, sql: str):
        self.sql = sql


_ENGINE_FALSO = object()  # los builders solo lo usan dentro de pd.read_sql, que interceptamos


def capturar_sql(monkeypatch, builder, **kwargs) -> str:
    """Ejecuta el builder pero corta en pd.read_sql, devolviendo el SQL generado."""
    def fake_read_sql(query, *a, **k):
        raise _SQLCapturado(query)

    monkeypatch.setattr(pd, "read_sql", fake_read_sql)
    try:
        builder(engine=_ENGINE_FALSO, **kwargs)
    except _SQLCapturado as e:
        return e.sql
    raise AssertionError(f"{builder.__name__} no llamó a pd.read_sql (no se generó SQL)")


# --------------------------------------------------------------------------- #
# Definición de cada campaña: builder + filtros soportados                     #
# --------------------------------------------------------------------------- #
F_DESDE = datetime(2025, 5, 1)
F_HASTA = datetime(2025, 5, 2)

CAMPANAS = [
    {
        "name": "Mitrol (genérico)",
        "func": q.get_filtered_data_mitrol_puro,
        "filtros": dict(
            idInteraccion=["123", "456"], Segmento=[1, 2], loginid=["op1", "op2"],
            empresa=["Cardnet"], campana=["Camp1"], duracion_min=10, duracion_max=600,
            tipificacion=["Venta", "Reclamo"], sentido=["Entrante", "Saliente", "Interno"],
        ),
        "reauditar": True, "por_tip": True,
    },
    {
        "name": "Hidra",
        "func": q.get_filtered_data_Hidra,
        "filtros": dict(
            idInteraccion=["123"], Segmento=[1, 2], loginid=["op1"],
            duracion_min=10, duracion_max=600,
            tipificacion=["Reclamo"], sentido=["Entrante", "Saliente"],
        ),
        "reauditar": True, "por_tip": True,
    },
    {
        "name": "Hidra Comercial",
        "func": q.get_filtered_data_Hidra_comercial,
        "filtros": dict(
            idInteraccion=["123"], loginid=["op1"], duracion_min=10, duracion_max=600,
        ),
        "reauditar": True, "por_tip": False,
    },
    {
        "name": "Aurora Salud",
        "func": q.get_filtered_data_Aurora_salud,
        "filtros": dict(
            idInteraccion=["123"], Segmento=[1, 2], loginid=["op1"],
            duracion_min=10, duracion_max=600,
            tipificacion=["Consulta"], sentido=["Entrante", "Interno"],
        ),
        "reauditar": True, "por_tip": True,
    },
    {
        "name": "ALARMIX",
        "func": q.get_filtered_data_ALARMIX,
        "filtros": dict(
            idInteraccion=["123"], direccion=["Entrante", "Saliente", "Interno"],
            Empleado=["op1"], tipificacion=["Venta"], skill=["SkillA"],
            duracion_min=10, duracion_max=600,
        ),
        "reauditar": True, "por_tip": True,
    },
    {
        "name": "Vitalis Salud",
        "func": q.get_filtered_data_Vitalis_Salud,
        "filtros": dict(
            idInteraccion=["123"], direccion=["Entrante", "Saliente"], loginid=["op1 Acme"],
            tipificacion=["Consulta"], duracion_min=10, duracion_max=600,
        ),
        "reauditar": True, "por_tip": True,
    },
    # Voltara NO va acá: no usa la firma estándar de muestreo (cantidad/fechas/operador),
    # sino una lista de ConnID de los audios subidos. Se valida en test_sql_voltara_upload.
    {
        "name": "Odonto Plus",
        "func": q.get_filtered_data_Odonto_plus,
        "filtros": dict(
            idInteraccion=["123"], campana=["Camp1"], Segmento=[1, 2], loginid=["op1"],
            duracion_min=10, duracion_max=600, tipificacion=["Turno"],
            sentido=["Entrante", "Saliente", "Interno"],
        ),
        "reauditar": True, "por_tip": True,
    },
    {
        "name": "Farmalux",
        "func": q.get_filtered_data_Farmalux,
        "filtros": dict(
            idInteraccion=["o_d_20250101000000"], loginid=["op1"], campana=["Cola1"],
            duracion_min=10, duracion_max=600,
        ),
        "reauditar": True, "por_tip": False,
    },
    {
        "name": "Vantix (Trackon)",
        "func": q.get_filtered_data_Vantix,
        "filtros": dict(
            idInteraccion=["123"], loginid=["1001"], duracion_min=10, duracion_max=600,
            empresa=["Trackon-Vantix"], cabezal=["Cab1"], campana=["Cat1"],
            tipificacion=["Sub1"], sentido=["Entrante"],
        ),
        "reauditar": True, "por_tip": True,
    },
]


def _generar_escenarios():
    """Genera (id, campaña, kwargs) cubriendo cada rama de filtros relevante."""
    escenarios = []
    for c in CAMPANAS:
        nombre, filtros = c["name"], c["filtros"]

        # 1. Sin filtros (camino base / muestreo simple)
        escenarios.append((f"{nombre} | minimo", c, dict(cantidad=1)))
        # 2. Todos los filtros + rango de fechas
        escenarios.append((f"{nombre} | todos_los_filtros", c,
                           dict(cantidad=5, Fecha_desde=F_DESDE, Fecha_hasta=F_HASTA, **filtros)))
        # 3. Fecha única (rama de fecha sin rango)
        escenarios.append((f"{nombre} | fecha_unica", c,
                           dict(cantidad=1, Fecha_desde=F_DESDE)))
        # 4. Muestreo por operador (CTE de ranking)
        escenarios.append((f"{nombre} | por_operador", c,
                           dict(cantidad=3, por_operador=True)))
        # 5. Muestreo por tipificación (solo donde aplica partición)
        if c["por_tip"]:
            escenarios.append((f"{nombre} | por_tipificacion", c,
                               dict(cantidad=3, por_tipificacion=True)))
            escenarios.append((f"{nombre} | por_operador+tipificacion", c,
                               dict(cantidad=3, por_operador=True, por_tipificacion=True)))
        # 6. Reauditar (cambia el filtro NOT EXISTS)
        if c["reauditar"]:
            escenarios.append((f"{nombre} | reauditar", c,
                               dict(cantidad=2, reauditar=True, **filtros)))
    return escenarios


ESCENARIOS = _generar_escenarios()


@pytest.mark.parametrize("descripcion,campana,kwargs", ESCENARIOS, ids=[e[0] for e in ESCENARIOS])
def test_sql_auditoria_por_campana(descripcion, campana, kwargs, monkeypatch, validar_sql):
    """El SQL generado debe ser válido para SQL Server (sintaxis y, donde se puede, nombres)."""
    sql = capturar_sql(monkeypatch, campana["func"], **kwargs)

    # Chequeos estructurales baratos antes de ir a la base
    assert sql and sql.strip(), "Se generó un SQL vacío"
    assert sql.count("(") == sql.count(")"), f"Paréntesis desbalanceados en:\n{sql}"
    assert ",," not in sql.replace(" ", ""), f"Coma doble (concatenación rota) en:\n{sql}"

    resultado = validar_sql(sql)
    assert resultado.ok, (
        f"\n[{descripcion}] SQL inválido ({resultado.method}): {resultado.error}\n"
        f"--- QUERY ---\n{sql}\n"
    )


# Literal de OPENQUERY: secuencias de no-comilla o comillas duplicadas (escapadas)
# hasta la comilla simple de cierre.
_OPENQUERY_LITERAL = re.compile(r"OPENQUERY\(ORION_LINK, '((?:[^']|'')*)'\)")
# Tope real del literal de OPENQUERY en SQL Server.
_TOPE_OPENQUERY = 8000


def test_vantix_lista_grande_de_ids_se_chunkea(monkeypatch, validar_sql):
    """
    Regresión: pegar muchos IDs (p. ej. una columna de Excel) hacía que el literal
    de OPENQUERY superara los 8000 chars y SQL Server lo rechazara. El builder debe
    repartir los IDs en varios OPENQUERY (UNION ALL) y generar SQL válido.
    """
    ids = [str(173833726250057806 + i) for i in range(600)]

    sql = capturar_sql(
        monkeypatch, q.get_filtered_data_Vantix,
        cantidad=150, idInteraccion=ids,
        Fecha_desde=F_DESDE, Fecha_hasta=F_HASTA,
        campana=["_VantixTurnos"], duracion_min=60, duracion_max=600,
    )

    literales = _OPENQUERY_LITERAL.findall(sql)
    assert len(literales) >= 2, "Una lista grande de IDs debería repartirse en varios OPENQUERY"
    assert sql.count("UNION ALL") == len(literales) - 1, "Los bloques deben unirse con UNION ALL"

    mas_largo = max(len(l) for l in literales)
    assert mas_largo < _TOPE_OPENQUERY, (
        f"Hay un literal de OPENQUERY de {mas_largo} chars (tope {_TOPE_OPENQUERY})"
    )

    # Cada ID debe aparecer exactamente una vez (sin duplicar filas entre bloques).
    assert all(sql.count(i) == 1 for i in ids), "Algún ID quedó duplicado u omitido entre bloques"

    resultado = validar_sql(sql)
    assert resultado.ok, (
        f"\n[Vantix lista grande] SQL inválido ({resultado.method}): {resultado.error}\n"
        f"--- QUERY ---\n{sql}\n"
    )


def test_dental_no_filtra_chats_por_duracion(monkeypatch, validar_sql):
    """
    Regresión: el form manda siempre duración 60-600s (el slider arranca ahí), y un chat
    queda abierto toda la gestión (promedio ~1h). Con la duración aplicada a los chats,
    el 92% quedaba afuera y las auditorías de Odonto Plus chat devolvían "no hay datos".
    La duración debe filtrar llamados, nunca chats.
    """
    sql = capturar_sql(
        monkeypatch, q.get_filtered_data_Odonto_plus,
        cantidad=5, campana=["WhatsappIN"], duracion_min=60, duracion_max=600,
        Fecha_desde=F_DESDE, Fecha_hasta=F_HASTA,
    )

    assert "ISNULL(g.Chat, 0) = 1 OR" in sql, (
        "La duración debe eximir a los chats (flag Chat de [detalle de grabaciones])"
    )
    # La duración no puede aplicarse en el PASO 1 (ahí todavía no se sabe si es chat).
    assert "d.Duración >=" not in sql and "d.Duración <=" not in sql, (
        "La duración se aplica sobre la tabla de interacciones, antes de conocer el flag Chat"
    )

    resultado = validar_sql(sql)
    assert resultado.ok, (
        f"\n[Dental duración/chat] SQL inválido ({resultado.method}): {resultado.error}\n"
        f"--- QUERY ---\n{sql}\n"
    )


def test_hidra_odt_se_desambigua_por_ventana_del_llamado(monkeypatch, validar_sql):
    """
    Regresión: el Nro. ODT de SAR es '<prefijo>-<año>-<dígitos>' y la numeración
    rearranca cada año, pero la columna CRM de Mitrol guarda SOLO los dígitos. Sin
    acotar por fecha, los dígitos matcheaban el ODT de 2024, 2025 y 2026 a la vez
    (1,26M de 2,57M filas de Sar_Ingresos caen en grupos de dígitos repetidos) y el
    RowNum=1 (ORDER BY [Fecha de Ing.]) se quedaba con el MÁS VIEJO: el audio y la
    tipificación de Mitrol quedaban bien y el bloque de SAR era de otro caso.
    El enlace por CRM debe acotar el ingreso a la ventana del llamado.
    """
    sql = capturar_sql(
        monkeypatch, q.get_filtered_data_Hidra,
        cantidad=5, Fecha_desde=F_DESDE, Fecha_hasta=F_HASTA,
    )

    assert "LTRIM(RTRIM(gi.CRM)) = s.ODT_Digitos" in sql, "Se perdió el enlace por dígitos del ODT"
    assert "s.[Fecha de Ing.] <= DATEADD(MINUTE, 30, gi.Inicio)" in sql, (
        "El ingreso no puede ser posterior al llamado (+30' de margen de carga)"
    )
    assert "s.[Fecha de Ing.] > DATEADD(DAY, -180, gi.Inicio)" in sql, (
        "Sin tope de antigüedad vuelve la colisión con el ODT del año anterior"
    )

    resultado = validar_sql(sql)
    assert resultado.ok, (
        f"\n[Hidra ventana ODT] SQL inválido ({resultado.method}): {resultado.error}\n"
        f"--- QUERY ---\n{sql}\n"
    )


def test_sql_voltara_upload(monkeypatch, validar_sql):
    """Voltara audita por subida: recibe una lista de ConnID (de los audios subidos) y
    trae la info del llamado desde [Voltara informe IVR]. El cruce con el caso de
    Salesforce usa [Voltara_Salesforce_casos_cerrados] (NO [..._casos_calidad], que es
    una población casi disjunta de los agentes del IVR, ver la nota en
    get_filtered_data_Voltara) y NO intenta identificar el caso puntual de la llamada:
    el vínculo es por agente (Usuario == Agente del IVR) en una ventana de días
    (VOLTARA_MARGEN_DIAS), porque el caso se abre/cierra días después del llamado."""
    sql = capturar_sql(
        monkeypatch, q.get_filtered_data_Voltara,
        conn_ids=["0ae6036da0eb5eed", "0ae6036da0eb5dd3", "o'brien"],  # incluye comilla a escapar
    )

    assert "[Voltara informe IVR]" in sql
    assert "Voltara_Salesforce_casos_cerrados" in sql
    assert "Voltara_Salesforce_casos_calidad" not in sql, (
        "casos_calidad es una población casi disjunta de los agentes del IVR (equipo de "
        "back-office/QA); no debería reintroducirse sin resolver la clave de vínculo."
    )
    assert "OUTER APPLY" in sql
    # La comilla simple del ConnID debe quedar escapada (duplicada) en el literal.
    assert "o''brien" in sql, "El ConnID con comilla debe escaparse duplicando la comilla"

    resultado = validar_sql(sql)
    assert resultado.ok, (
        f"\n[Voltara upload] SQL inválido ({resultado.method}): {resultado.error}\n"
        f"--- QUERY ---\n{sql}\n"
    )


def test_sql_voltara_sin_connids_no_consulta(monkeypatch):
    """Sin ConnID (Excel vacío o sin match) no se genera SQL: se corta antes de pd.read_sql."""
    def fake_read_sql(*a, **k):
        raise AssertionError("No debería consultarse la base sin ConnID")
    monkeypatch.setattr(pd, "read_sql", fake_read_sql)

    assert q.get_filtered_data_Voltara(engine=_ENGINE_FALSO, conn_ids=[]).empty
    assert q.get_filtered_data_Voltara(engine=_ENGINE_FALSO, conn_ids=None).empty
    assert q.get_filtered_data_Voltara(engine=_ENGINE_FALSO, conn_ids=["", "  ", None]).empty
