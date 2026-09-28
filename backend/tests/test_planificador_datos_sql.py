"""Valida el SQL de la capa de datos del planificador (app/planificador_datos.py).

Dos regímenes distintos, a propósito:

- Las consultas a las tablas que YA existen (el informe por intervalo de Voltara,
  el informe IVR, TMO_Voltara, Feriados) se validan con `describe`, que resuelve
  nombres de tablas y columnas sin ejecutar nada. Es lo que atrapa un nombre de
  columna mal escrito, que en estas tablas abundan los espacios y los acentos.
- Las consultas al schema `planificacion` todavía no pueden validarse con
  `describe` porque las tablas no existen hasta que se corra la migración: van con
  PARSEONLY, que solo ve la sintaxis (ver [[sql-validacion-parseonly-vs-describe]]).

Ninguna consulta se ejecuta: no escanean datos ni gastan tokens.

Correr: pytest tests/test_planificador_datos_sql.py -m "not tokens"
"""
import re
from datetime import date, datetime

import pytest

from app import planificador_datos as pd


def _literales(sql: str) -> str:
    """Reemplaza los parámetros nombrados por literales para poder validar.

    `describe` recibe la consulta como texto y los `:param` de SQLAlchemy no son
    T-SQL válido, así que hay que darle algo que el parser entienda. Los valores
    son irrelevantes: no se ejecuta.
    """
    sql = sql.replace("IN :skills", "IN (1, 2, 6)")
    sql = re.sub(r":(desde|a\b)", "'2026-08-01'", sql)
    sql = re.sub(r":(hasta|b\b)", "'2026-09-01'", sql)
    sql = re.sub(r":(corrida|id|c|h|u|pool|skill)\b", "1", sql)
    sql = re.sub(r":(momento|d|ha)\b", "'2026-08-01'", sql)
    sql = re.sub(r":(m|e|motivo)\b", "'x'", sql)
    sql = re.sub(r":\w+", "1", sql)
    return sql


# ------------------------------------------- consultas a las tablas existentes

FUENTE = pd.FuenteVoltara
CONSULTAS_REALES = [
    ("serie", FUENTE.SERIE),
    ("paciencia", FUENTE.PACIENCIA),
    ("calibracion", FUENTE.CALIBRACION),
    ("conectados", FUENTE.CONECTADOS),
]


@pytest.mark.parametrize("nombre,sql", CONSULTAS_REALES,
                         ids=[n for n, _ in CONSULTAS_REALES])
def test_las_consultas_de_voltara_resuelven_nombres(validar_sql, nombre, sql):
    """Nombres de tablas y columnas reales. En estas tablas las columnas tienen
    espacios, acentos y hasta dobles espacios (`[Contestadas  <= 1]`), así que un
    error de tipeo acá no se ve hasta que revienta en producción."""
    resultado = validar_sql(_literales(sql))
    assert resultado.ok, f"{nombre}: {resultado.error}"


def test_la_calibracion_mide_a_20_segundos():
    """El umbral contractual es 20s. La columna `Contestadas Umbral` del reporte
    está calculada a 30s, así que NO se puede usar: el numerador se arma sumando
    los buckets hasta 20. Si alguien la 'simplifica' usando la columna, el factor
    de disponibilidad sale mal y con él toda la dotación."""
    sql = FUENTE.CALIBRACION
    assert "[Contestadas Umbral]" not in sql
    for bucket in ("[Contestadas  <= 1]", "[Contestadas > 1 <= 5]",
                   "[Contestadas > 5 <= 10]", "[Contestadas > 10 <= 20]"):
        assert bucket in sql
    assert "[Contestadas > 20 <= 30]" not in sql, "ese bucket ya pasa los 20s"


def test_la_serie_se_queda_con_nuestro_bpo():
    """Las consultas al informe IVR tienen que filtrar BPO-04. Sin ese filtro se
    mezclan las llamadas de Startrek con las nuestras, y como la cobertura de los
    otros BPOs es esporádica, el resultado cambia según el mes."""
    assert "BPO-04" in FUENTE.PACIENCIA


def test_el_tmo_va_ponderado_por_llamadas():
    """Un AVG(TMO) sobre los intervalos le da el mismo peso a uno de 3 llamadas
    que a uno de 300."""
    for sql in (FUENTE.SERIE, FUENTE.CALIBRACION):
        assert "SUM(CAST(s.TMO AS float) * CAST(s.[Volumen de llamadas respondidas]" in sql
        assert "AVG(" not in sql.upper().replace("AVG(CAST", "")


# ---------------------------------------------- consultas al schema pendiente

def _consultas_del_schema():
    """Extrae el SQL embebido en el módulo que toca `planificacion.`."""
    import inspect
    fuente = inspect.getsource(pd)
    return [(f"consulta{i}", q) for i, q in enumerate(re.findall(
        r'text\("""(.*?)"""\)', fuente, re.S)) if "planificacion." in q]


CONSULTAS_SCHEMA = _consultas_del_schema()


def test_hay_consultas_al_schema_nuevo():
    assert CONSULTAS_SCHEMA, "No se encontró SQL del schema planificacion."


@pytest.mark.parametrize("nombre,sql", CONSULTAS_SCHEMA,
                         ids=[n for n, _ in CONSULTAS_SCHEMA])
def test_sintaxis_de_las_consultas_del_schema(engine, nombre, sql):
    from conftest import _validar_parseonly

    resultado = _validar_parseonly(engine, _literales(sql))
    assert resultado.ok, f"{nombre}: {resultado.error}"


def test_las_columnas_usadas_existen_en_la_migracion():
    """Red de seguridad de lo que PARSEONLY no ve: que los nombres de tabla que
    usa el código estén realmente creados por alguna de las migraciones.

    Mira TODAS las migraciones del planificador, no una lista escrita a mano: la
    lista se desactualizaba cada vez que se agregaba una y el test empezaba a
    marcar como faltantes tablas que sí existían.
    """
    import glob
    import inspect
    import os
    raiz = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    patron = os.path.join(raiz, "scripts", "migrations", "*planificador*.sql")
    creadas = set()
    for ruta in sorted(glob.glob(patron)):
        creadas |= set(re.findall(r"CREATE TABLE planificacion\.(\w+)",
                                  open(ruta, encoding="utf-8").read()))
    assert creadas, f"No se encontró ninguna migración del planificador en {patron}"
    usadas = set(re.findall(r"planificacion\.(\w+)", inspect.getsource(pd)))
    faltantes = usadas - creadas
    assert not faltantes, f"El código usa tablas que las migraciones no crean: {faltantes}"


class _ConnFalsa:
    """Devuelve filas fijas sin tocar la base."""

    def __init__(self, filas):
        self._filas = filas

    def execute(self, *_args, **_kwargs):
        filas = self._filas

        class _R:
            def mappings(self_inner):
                return filas
        return _R()


def test_las_filas_sin_skill_no_rompen_la_serie():
    """El informe por intervalo trae filas con `Skill ID` en NULL (llamadas que no
    quedaron atribuidas a ninguna cola). Antes reventaban la corrida con un
    TypeError al hacer int(None); ahora se descartan y se pueden contar aparte."""
    filas = [
        {"momento": datetime(2026, 9, 1, 10, 0), "skill_id": 6, "llamadas": 100,
         "atendidas": 98, "tmo_x_llamadas": 98 * 180.0},
        {"momento": datetime(2026, 9, 1, 10, 0), "skill_id": None, "llamadas": 7,
         "atendidas": 0, "tmo_x_llamadas": 0},
    ]
    serie = pd.serie_por_skill(_ConnFalsa(filas), pd.CAMPANA_VOLTARA,
                               date(2026, 9, 1), date(2026, 9, 2))
    assert list(serie) == [(datetime(2026, 9, 1, 10, 0), 6)]
    assert serie[(datetime(2026, 9, 1, 10, 0), 6)] == (100.0, 180.0)
    assert pd.serie_descartada(_ConnFalsa(filas), pd.CAMPANA_VOLTARA,
                               date(2026, 9, 1), date(2026, 9, 2)) == 7.0


def test_el_tmo_es_cero_si_no_hubo_atendidas():
    """Un intervalo con entrantes pero sin atendidas no tiene TMO propio; devolver
    0 es lo que hace que el pronóstico caiga al perfil en vez de inventar uno."""
    filas = [{"momento": datetime(2026, 9, 1, 4, 0), "skill_id": 6, "llamadas": 3,
              "atendidas": 0, "tmo_x_llamadas": 0}]
    serie = pd.serie_por_skill(_ConnFalsa(filas), pd.CAMPANA_VOLTARA,
                               date(2026, 9, 1), date(2026, 9, 2))
    assert serie[(datetime(2026, 9, 1, 4, 0), 6)] == (3.0, 0.0)


def test_hay_demanda_total_depende_de_la_tabla_del_cliente(engine):
    """Con la descarga completa cargada esto pasa a True y el pipeline cambia: el
    pronóstico deja de ser de nuestra porción y pasa a ser de la demanda del
    cliente, con el reparto aplicado aparte.

    Antes este test afirmaba lo contrario y era el recordatorio de revisar el
    pipeline entero el día que se prendiera. Ese día llegó: `dbo.[Voltara Enerval
    informe IVR]` tiene 25,4 millones de llamadas desde 2023-01-01 con BPO y Skill
    poblados para todos los contact centers.
    """
    with engine.connect() as conn:
        assert pd.hay_demanda_total(conn, pd.CAMPANA_VOLTARA) is True
        # Una campaña sin fuente de demanda total declarada sigue en False, y su
        # pronóstico sigue siendo sobre la porción propia.
        assert pd.hay_demanda_total(conn, 999999) is False


# ------------------------------------------------------------------- payroll

def _sql_de_payroll():
    """Las consultas de payroll, en sus DOS variantes.

    Las que filtran por puesto llevan un `{puesto}` que se rellena según esté o
    no aplicada la migración 2026-09-09e, y las dos versiones se despachan contra
    la base: validar sólo una dejaría la otra sin cobertura justo en el período
    en que conviven.
    """
    salida = {}
    for n, v in vars(pd).items():
        if not (n.startswith("_PAYROLL") or n == "_PUESTOS_MALLA"):
            continue
        if not isinstance(v, str):
            continue
        if "{puesto}" in v:
            salida[n + " (sin filtro de puesto)"] = v.format(puesto="")
            salida[n + " (con filtro de puesto)"] = v.format(puesto=pd._FILTRO_PUESTO)
        elif "{en_malla}" in v:
            salida[n + " (sin tabla de puestos)"] = v.format(en_malla="NULL", join="")
            salida[n + " (con tabla de puestos)"] = v.format(
                en_malla="MAX(CAST(m.EnMalla AS tinyint))",
                join="LEFT JOIN planificacion.PuestoMalla m ON m.PuestoID = o.puesto_id")
        else:
            salida[n] = v
    return salida


def test_la_malla_y_el_shrinkage_filtran_por_el_mismo_puesto():
    """Las dos salen de las mismas horas de payroll y el shrinkage DIVIDE la
    dotación (a_planificar = en_línea / (1 - shrinkage)). Medir el ausentismo
    sobre toda la nómina y contar la malla sólo con los que atienden serían dos
    poblaciones distintas en la misma cuenta: 13,6% contra 4,6% medido sobre
    Voltara, y la diferencia son supervisores y el puesto Operador Capacitación.
    """
    assert "{puesto}" in pd._PAYROLL_TURNOS
    assert "{puesto}" in pd._PAYROLL_HORAS


def test_un_puesto_sin_clasificar_no_entra_a_la_malla():
    """`dbo.puestos` crece. Con una lista en el código un puesto nuevo entraría a
    la malla sin que nadie lo decida; con EXISTS sobre la tabla, no cuenta hasta
    que alguien lo marque —y la pantalla lo muestra con las horas que trae."""
    assert "EXISTS" in pd._FILTRO_PUESTO and "EnMalla = 1" in pd._FILTRO_PUESTO
    assert "NOT EXISTS" not in pd._FILTRO_PUESTO


def test_hay_consultas_de_payroll():
    assert _sql_de_payroll(), "No se encontró SQL de payroll en el módulo."


@pytest.mark.parametrize("nombre", sorted(_sql_de_payroll()))
def test_sintaxis_de_las_consultas_de_payroll(engine, nombre):
    """Van con PARSEONLY y no con describe porque tocan planificacion.CodigoPayroll
    y planificacion.PoolOrigen, que las crea la migración 2026-09-03b."""
    from conftest import _validar_parseonly

    sql = _literales(_sql_de_payroll()[nombre].replace("IN :campanas", "IN (1, 2)"))
    resultado = _validar_parseonly(engine, sql)
    assert resultado.ok, f"{nombre}: {resultado.error}"


@pytest.mark.parametrize("nombre", sorted(_sql_de_payroll()))
def test_payroll_no_se_queda_solo_con_la_version_vigente_del_operador(nombre):
    """`dbo.operadores` está versionada (una fila por cambio de turno, equipo o
    campaña) y `payroll.id_operadores` apunta a la versión vigente ESE día.

    Filtrar por `fecha_hasta IS NULL` deja afuera a todo el que haya tenido algún
    cambio: medido sobre Voltara en jun-ago 2026 se perdían 38.419 de 99.722 horas
    programadas, casi el 40%, y con un sesgo nada aleatorio (sobreviven los que no
    se movieron). Este test existe para que no vuelva a colarse.
    """
    sql = _sql_de_payroll()[nombre]
    assert "fecha_hasta IS NULL" not in sql, (
        f"{nombre} filtra por la versión vigente del operador y pierde historia."
    )


def test_el_shrinkage_no_cuenta_las_licencias():
    """Vacaciones y licencias largas no tienen horas programadas: esa gente no
    estaba en la malla. Meterlas inflaría el shrinkage con ausencias que nadie
    había contado como dotación disponible."""
    import inspect
    fuente = inspect.getsource(pd.estimar_shrinkage_payroll)
    assert "licencia" in fuente
    assert 'horas.get("piso"' in fuente and 'horas.get("ausente"' in fuente
    # El denominador son las horas programadas, que excluyen licencia por
    # construcción (piso + ausente + capacitacion + otro).
    assert "piso + ausente + capacitacion + otro" in fuente


# ------------------------------------------------- demanda total del cliente

FUENTE_TOTAL = pd.FuenteVoltaraTotal
CONSULTAS_TOTAL = [("demanda", FUENTE_TOTAL.DEMANDA),
                   ("asignacion", FUENTE_TOTAL.ASIGNACION)]


@pytest.mark.parametrize("nombre,sql", CONSULTAS_TOTAL,
                         ids=[n for n, _ in CONSULTAS_TOTAL])
def test_las_consultas_de_demanda_total_resuelven_nombres(validar_sql, nombre, sql):
    """`dbo.[Voltara Enerval informe IVR]` ya existe, así que se valida con describe:
    resuelve tablas y columnas sin ejecutar nada."""
    resultado = validar_sql(_literales(sql).replace(":minutos", "30").replace(":bpo", "'BPO-04'"))
    assert resultado.ok, f"{nombre}: {resultado.error}"


def test_la_demanda_solo_cuenta_lo_que_llego_a_una_cola():
    """El resto de la tabla no es demanda de los contact centers: las de
    `Resultado IVR = 'AGENT'` sin cola se rutearon pero nunca se conectaron
    (Tiempo Total == Tiempo IVR y Talk = 0 en 185.667 de 185.688 casos de agosto),
    y DISCONNECT/SURVEY se resolvieron dentro del IVR. Contarlas inflaría el
    dimensionamiento con llamadas que ningún operador podría haber atendido."""
    for _, sql in CONSULTAS_TOTAL:
        assert "i.Cola IS NOT NULL" in sql


def test_el_tmo_de_la_demanda_total_es_solo_el_nuestro():
    """El volumen se pronostica sobre el total del cliente, pero el tiempo de
    atención sale sólo de BPO-04: en agosto Comercial nos llevaba 333s y a BPO-05
    402s, así que promediarlos sobredimensionaría."""
    sql = FUENTE_TOTAL.DEMANDA
    assert "es_acme = 1 AND atendida = 1 THEN trabajo" in sql


def test_el_bucketeo_va_en_subconsulta():
    """Repetir la expresión con parámetros en el SELECT y en el GROUP BY hace que
    SQL Server no las reconozca como la misma y rechace la consulta con el error
    8120. Pasó de verdad."""
    sql = FUENTE_TOTAL.DEMANDA
    assert sql.count("DATEDIFF(minute, '2000-01-01'") == 1
    assert "GROUP BY momento, skill_id" in sql
