"""Valida la migración del Planificador ANTES de aplicarla.

El schema `planificacion` todavía no existe en la base (la migración la corre
Ignacio a mano), así que se valida con `SET PARSEONLY ON`: chequea la sintaxis
T-SQL sin ejecutar ni escribir nada. Ojo con lo que PARSEONLY NO ve: no resuelve
nombres de tablas ni columnas (ver [[sql-validacion-parseonly-vs-describe]]), así
que acá se cubre la sintaxis, y la coherencia de la siembra se chequea aparte
leyendo el texto.

Correr: pytest tests/test_planificador_migracion_sql.py -m "not tokens"
"""
import os
import re

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRACION = os.path.join(REPO_ROOT, "scripts", "migrations",
                         "2026-09-03_planificador.sql")
# La segunda migración agrega la dotación planificada real (payroll), el catálogo
# de códigos de RRHH y la calibración de la paciencia. Los chequeos genéricos
# —sintaxis y PKs sin columnas nulables— corren sobre las dos.
MIGRACION_B = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-03b_planificador_payroll_y_paciencia.sql")
# La tercera pasa el pronóstico a la demanda TOTAL del cliente: permite el 0 en
# Asignacion y siembra los tramos midiéndolos de la descarga completa.
MIGRACION_C = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-07_planificador_demanda_total.sql")
# La cuarta suma las restricciones que tenía la planilla de Excel (TME, segundo
# nivel de servicio y nivel de atención por Erlang B).
MIGRACION_D = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-07b_planificador_restricciones_planilla.sql")
# La quinta hace configurable la ventana de entrenamiento de la línea de base y
# cambia los valores por defecto (8 semanas -> 52, 14 días de nivel -> 28). Es la
# única de la serie que MUEVE los números, y el encabezado trae la medición que
# lo justifica.
MIGRACION_E = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-07c_planificador_ventana_entrenamiento.sql")
# La sexta suma el clima del área de concesión (tabla + coordenadas por campaña).
# Nace apagada: no cambia ningún número hasta que alguien la active a mano.
MIGRACION_F = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-07d_planificador_clima.sql")
# La séptima separa la corrección de nivel entre días hábiles y fines de semana.
# Es la única que nace ACTIVADA porque no agrega una capacidad: corrige un sesgo
# medido, y dejarla apagada sería dejar el error puesto.
MIGRACION_G = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-08_planificador_nivel_por_tipo_de_dia.sql")
# La octava sigue la deriva reciente del reparto (ventana de 7 días).
MIGRACION_H = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-08b_planificador_deriva_reparto.sql")
# La novena corrige el reparto de los días NO hábiles (nace activada, es un sesgo
# medido) y habilita combinar con el pronóstico que manda el cliente (apagada).
MIGRACION_I = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-09_planificador_reparto_cliente.sql")
# La décima no toca el schema: junta los tres pools de Voltara en uno, porque los
# operadores son multiskill y dimensionar tres colas por separado pide 17% más
# gente. Es la única de la serie que MUEVE DATOS, así que va en transacción.
MIGRACION_J = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-09b_planificador_pool_unico.sql")
# La undécima suma la segunda opinión del nivel diario (GBDT con pérdida de
# Poisson, promediado con el modelo de clima). Dos columnas, nace APAGADA: con
# NivelGbdt = 0 el pronóstico es idéntico al de hoy, y hay test que lo fija.
MIGRACION_K = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-09c_planificador_nivel_gbdt.sql")
# La duodecima atenua el factor de clima de los DOMINGOS por su elasticidad
# medida: un domingo es 98,9% EMERGENCIAS y recibe los factores mas grandes justo
# donde el modelo menos se sostiene. Nace apagada: sobre el ano es neutra y lo
# que arregla es el regimen actual.
MIGRACION_L = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-09d_planificador_elasticidad_domingo.sql")
# La decimotercera saca de la malla a quien no atiende el telefono: la nomina de
# las sub-campanas incluye supervisores, coordinadores y el puesto Operador
# Capacitacion. Es la unica de la serie que NO nace apagada -desde que se aplica
# cambian la malla citada y el shrinkage medido- y por eso el encabezado trae los
# dos numeros: -15 personas en el pico de 151 y 13,6% -> 4,6% de shrinkage.
MIGRACION_M = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-09e_planificador_puestos_malla.sql")
# La decimocuarta deja en el pool SOLO las sub-campanas telefonicas, por
# instruccion de la operacion: la malla mide a los que estan en una campana
# telefonica. Mueve datos (borra 10 de 14 filas de PoolOrigen), asi que va en
# transaccion y con los INSERT de vuelta atras comentados al final.
MIGRACION_N = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-09f_planificador_pool_solo_telefonicas.sql")
# La decimoquinta parte el shrinkage por tipo de dia (el feriado da 5,3% contra
# 8,9% de un habil) y declara el break. Las tres columnas nacen NEUTRAS: el break
# en cero a proposito, porque ese 8,33% ya esta adentro del factor de
# disponibilidad y ponerlo sin volver a medirla lo contaria dos veces.
MIGRACION_O = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-10_planificador_shrinkage_por_dia_y_break.sql")
# La decimosexta alinea la malla citada con dbo.Tablero_Agentes_Voltara, que es la
# definicion que Planificacion mira todos los dias: suma Artefactos Danados, saca
# T1 - Consumo y suma el puesto Operador Capacitacion. Replicado intervalo por
# intervalo antes de escribir: 964 contra 964 el 2026-09-09.
MIGRACION_P = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-10b_planificador_alinear_malla_con_tablero.sql")
# La decimoseptima ensena al modelo que Electrodependientes tiene PRIORIDAD en el
# ACD. Era la restriccion que mas dotacion pedia -unico skill con techo de
# abandono, verificado con la congestion del ultimo de la cola- y con la prioridad
# puesta el mismo techo se cumple con 35 operadores en vez de 40.
MIGRACION_Q = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-10c_planificador_skill_prioritario.sql")
# La decimoctava saca el piso de cobertura del pool General de Voltara. En la
# madrugada el piso de 2 en linea era lo unico que fijaba la dotacion, y la cadena
# de descuentos lo llevaba a 3 personas a citar con 1 llamada y NDS 100%.
MIGRACION_R = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-11_planificador_sin_minimo_pool.sql")
# Digital como palanca: tabla de sub-campanas que se pueden pasar a la linea, dos
# columnas en Requerimiento y la siembra de Voltara (sin Agrupadas ni Ajustes).
MIGRACION_S = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-14_planificador_refuerzo_digital.sql")
# T1 - Consumo vuelve al pool: medido, su gente pasa el 100% de sus horas en el
# telefono. Se desalinea a proposito del tablero, que no la cuenta.
MIGRACION_T = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-14b_planificador_pool_con_t1_consumo.sql")
# Usuarios sin luz segun el ENRE (serie de 5 minutos, foto del mapa e historia
# del repositorio publico). Sólo crea la tabla; la carga es scripts/cortes_enre.py.
MIGRACION_U = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-14c_planificador_cortes_enre.sql")
# Seguimiento de pedidos de refuerzo a RRHH (tabla planificacion.RefuerzoPedido).
MIGRACION_V = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-15_planificador_refuerzo_pedido.sql")
# Perfil de presencia por media hora: el shrinkage del día redistribuido según
# cuándo falta la gente. Sólo crea la tabla; la carga el script semanal.
MIGRACION_W = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-15b_planificador_perfil_presencia.sql")
MIGRACION_X = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-15c_planificador_antiguedad.sql")
# Sub-campañas que atienden la línea a veces (cuentan sólo en las medias horas en
# línea) y digitales que no son palanca (sólo suman a los conectados).
MIGRACION_Y = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-16_planificador_subcampanas_parciales.sql")
# T1 - Consumo sale del pool: cola dedicada (COMERCIAL-CONSUMO) sin llamadas en
# nuestros informes. Deshace la 2026-09-14b.
MIGRACION_Z = os.path.join(REPO_ROOT, "scripts", "migrations",
                           "2026-09-16b_planificador_t1_consumo_dedicada.sql")
# Redondeo para abajo de madrugada (Voltara, 0 a 8).
MIGRACION_AA = os.path.join(REPO_ROOT, "scripts", "migrations",
                            "2026-09-17_planificador_redondeo_nocturno.sql")
MIGRACIONES = [MIGRACION, MIGRACION_B, MIGRACION_C, MIGRACION_D, MIGRACION_E,
               MIGRACION_F, MIGRACION_G, MIGRACION_H, MIGRACION_I, MIGRACION_J,
               MIGRACION_K, MIGRACION_L, MIGRACION_M, MIGRACION_N, MIGRACION_O,
               MIGRACION_P, MIGRACION_Q, MIGRACION_R, MIGRACION_S, MIGRACION_T,
               MIGRACION_U, MIGRACION_V, MIGRACION_W, MIGRACION_X, MIGRACION_Y,
               MIGRACION_Z, MIGRACION_AA]


def _batches(sql_text: str):
    """Divide un script T-SQL en batches usando líneas 'GO'."""
    batch, batches = [], []
    for linea in sql_text.splitlines():
        if linea.strip().upper() == "GO":
            if batch:
                batches.append("\n".join(batch))
                batch = []
        else:
            batch.append(linea)
    if batch and "\n".join(batch).strip():
        batches.append("\n".join(batch))
    return batches


def _contenido(ruta=None):
    with open(ruta or MIGRACION, encoding="utf-8") as f:
        return f.read()


def _batches_de_la_migracion():
    salida = []
    for ruta in MIGRACIONES:
        if not os.path.exists(ruta):
            continue
        etiqueta = os.path.basename(ruta).split("_")[0]
        salida += [(f"{etiqueta}-batch{i}", b)
                   for i, b in enumerate(_batches(_contenido(ruta)))
                   if b.strip() and not b.strip().upper().startswith("USE ")]
    return salida


BATCHES = _batches_de_la_migracion()


def test_la_migracion_existe():
    assert os.path.exists(MIGRACION), f"Falta el script de migración: {MIGRACION}"


@pytest.mark.parametrize("indice,batch", BATCHES, ids=[i for i, _ in BATCHES])
def test_sintaxis_de_cada_batch(engine, indice, batch):
    # PARSEONLY y no la fixture `validar_sql`: esa prefiere `describe`, que
    # resuelve nombres y fallaría por las tablas que esta migración viene a crear.
    from conftest import _validar_parseonly

    resultado = _validar_parseonly(engine, batch)
    assert resultado.ok, f"Error de sintaxis en el {indice}: {resultado.error}"


def test_crea_todas_las_tablas_bajo_guarda():
    """El script se tiene que poder correr dos veces sin romper ni duplicar nada."""
    contenido = _contenido()
    tablas = ["Campana", "Pool", "Skill", "Asignacion", "Disponibilidad",
              "Evento", "Ajuste", "Corrida", "Pronostico", "Requerimiento"]
    for tabla in tablas:
        assert f"IF OBJECT_ID('planificacion.{tabla}', 'U') IS NULL" in contenido, (
            f"planificacion.{tabla} tiene que crearse bajo guarda de existencia."
        )


def _bloques_create_table(contenido: str):
    """(tabla, cuerpo) de cada CREATE TABLE del schema, cortando en el `);`."""
    bloques = []
    for m in re.finditer(r"CREATE TABLE planificacion\.(\w+)\s*\(", contenido):
        cuerpo = contenido[m.end():]
        fin = cuerpo.index("\n    );")
        bloques.append((m.group(1), cuerpo[:fin]))
    return bloques


def _columna_es_nulable(cuerpo: str, columna: str) -> bool:
    """Una columna es nulable si su declaración no dice NOT NULL. SQL Server
    asume NULL cuando no se aclara, así que la ausencia también cuenta."""
    for linea in cuerpo.splitlines():
        limpia = linea.strip()
        if re.match(rf"{re.escape(columna)}\s+\w", limpia):
            return "NOT NULL" not in limpia
    return False   # no se encontró la declaración: no se puede afirmar que sea nulable


_TABLAS = [b for ruta in MIGRACIONES if os.path.exists(ruta)
           for b in _bloques_create_table(_contenido(ruta))]


@pytest.mark.parametrize("tabla,cuerpo", _TABLAS, ids=[t for t, _ in _TABLAS])
def test_ninguna_primary_key_usa_columnas_nulables(tabla, cuerpo):
    """SQL Server rechaza una PRIMARY KEY sobre una columna nulable con el error
    8111, y PARSEONLY no lo ve porque es una restricción semántica y no de
    sintaxis: el script parsea perfecto y falla recién al aplicarse.

    Pasó de verdad con planificacion.Disponibilidad, cuyo PoolID es NULLable a
    propósito (NULL = toda la campaña). La solución fue clave sustituta más un
    índice UNIQUE sobre la clave natural, que sí admite NULLs.
    """
    for pk in re.finditer(r"PRIMARY KEY\s*\(([^)]+)\)", cuerpo):
        for columna in (c.strip() for c in pk.group(1).split(",")):
            assert not _columna_es_nulable(cuerpo, columna), (
                f"{tabla}.{columna} es nulable y está en la PRIMARY KEY: "
                f"SQL Server lo va a rechazar con el error 8111."
            )


def test_la_siembra_es_idempotente():
    """Las tres siembras (campaña, pools, skills, disponibilidad) van bajo
    IF NOT EXISTS: correr la migración de nuevo no puede duplicar la config."""
    contenido = _contenido()
    assert contenido.count("IF NOT EXISTS (SELECT 1 FROM planificacion.") >= 4


def test_los_permisos_nacen_sin_asignar():
    """Mismo criterio que audit:cuotas / audit:scheduler: el INSERT en
    RolePermissions queda comentado para que lo habilite quien corresponda."""
    contenido = _contenido()
    assert contenido.count("IF NOT EXISTS (SELECT 1 FROM pagina_web.Permissions") == 2

    activos = [linea for linea in contenido.splitlines()
               if "INSERT INTO pagina_web.RolePermissions" in linea]
    assert activos, "El bloque opcional de asignación tiene que seguir documentado."
    inicio = contenido.index("nacen SIN ASIGNAR")
    fin = contenido.index("--------------------------------------------------------------------------- */", inicio)
    for linea in activos:
        assert inicio < contenido.index(linea) < fin, (
            "El INSERT en RolePermissions tiene que estar dentro del bloque comentado."
        )


def test_no_toca_la_tabla_forecast_existente():
    """`dbo.Forecast` la sigue usando lo que ya la lee. La migración no la puede
    tocar: el planificador escribe en su propio schema."""
    contenido = _contenido()
    for prohibido in ("DROP TABLE dbo.Forecast", "ALTER TABLE dbo.Forecast",
                      "DELETE FROM dbo.Forecast", "TRUNCATE TABLE dbo.Forecast"):
        assert prohibido.lower() not in contenido.lower()


def test_electrodependientes_tiene_techo_de_abandono():
    """Es el único skill cuyo compromiso es de nivel de ATENCIÓN, no de nivel de
    servicio: si se pierde el MaxAbandono en la siembra, el motor lo dimensiona
    como a cualquier otro y el compromiso no se cumple nunca.

    El techo tiene que ser chico pero NO cero: en una cola el cero exacto pide
    dotación infinita, así que sembrarlo en 0 no dimensiona, cuelga."""
    contenido = _contenido()
    fila = next(l for l in contenido.splitlines() if "'ELECTRODEPENDIENTES'" in l)
    cap = fila.rsplit(",", 1)[0].rsplit(",", 1)[-1].strip()
    assert 0.0 < float(cap) <= 0.01, (
        f"MaxAbandono de ELECTRODEPENDIENTES fuera de rango razonable: {cap}"
    )


def test_el_umbral_sembrado_es_el_contractual():
    """20 segundos, no los 30 con los que el reporte actual calcula el NDS."""
    contenido = _contenido()
    filas = [l for l in contenido.splitlines()
             if l.strip().startswith("(20,") and "0.800" in l]
    assert filas, "Tiene que haber skills sembrados con objetivo de NDS."
    for fila in filas:
        assert ", 20," in fila, f"Umbral distinto de 20s en la siembra: {fila.strip()}"


def test_la_segunda_migracion_existe():
    assert os.path.exists(MIGRACION_B), f"Falta el script: {MIGRACION_B}"


def test_la_siembra_de_payroll_es_idempotente():
    """El catálogo de códigos y el origen de los pools van bajo IF NOT EXISTS, y
    las columnas nuevas bajo COL_LENGTH: correr el script dos veces no puede
    duplicar códigos ni fallar por columna repetida."""
    contenido = _contenido(MIGRACION_B)
    assert contenido.count("IF NOT EXISTS (SELECT 1 FROM planificacion.") >= 2
    assert contenido.count("IF COL_LENGTH(") >= 2


def test_todo_codigo_de_payroll_tiene_clase_valida():
    """Un código mal clasificado mueve el shrinkage de toda la campaña, así que
    las clases son un CHECK y no texto libre."""
    contenido = _contenido(MIGRACION_B)
    clases = set(re.findall(r"'\w[\w \-]*',\s*'(piso|ausente|capacitacion|licencia|otro)'",
                            contenido))
    assert {"piso", "ausente", "capacitacion", "licencia"} <= clases
    assert "CHECK (Clase IN ('piso', 'ausente', 'capacitacion', 'licencia', 'otro'))" in contenido


def test_la_paciencia_sembrada_sale_de_kaplan_meier():
    """El valor viejo (853s) era el MLE exponencial global, que pesa de más la cola
    larga. El nuevo se ajusta al horizonte donde realmente se espera."""
    contenido = _contenido(MIGRACION_B)
    assert "PacienciaOrigen        = 'km'" in contenido
    assert "PacienciaHorizonteSeg  = 60" in contenido
    assert "PacienciaSeg           = 1126" in contenido


def test_el_shrinkage_sembrado_desglosa_sus_partes():
    """El número tiene que poder explicarse: 27,2% = 17,1% de ausentismo + 9,3% de
    capacitación, todo medido sobre dbo.payroll."""
    contenido = _contenido(MIGRACION_B)
    assert "ShrinkageOrigen        = 'payroll'" in contenido
    assert "ShrinkageAusentismo    = 0.171" in contenido
    assert "ShrinkageCapacitacion  = 0.093" in contenido


def test_la_tercera_migracion_existe():
    assert os.path.exists(MIGRACION_C), f"Falta el script: {MIGRACION_C}"


def test_la_asignacion_admite_el_cero():
    """CNR y EMERGENCIAS-EMPRESARIAL hoy se rutean enteras al otro BPO. Sin poder
    guardar un 0 quedaban sin tramo, y el pronóstico no puede distinguir "no nos
    mandan nada" de "falta configurarlo"."""
    contenido = _contenido(MIGRACION_C)
    assert "CHECK (Porcentaje >= 0 AND Porcentaje <= 1)" in contenido
    assert "DROP CONSTRAINT CK_Plan_Asignacion_Pct" in contenido


def test_la_asignacion_se_siembra_midiendo_y_no_a_mano():
    """Los porcentajes salen de la propia tabla de llamadas con INSERT..SELECT, no
    escritos a dedo: así el número es auditable y se puede rehacer."""
    contenido = _contenido(MIGRACION_C)
    assert "INSERT INTO planificacion.Asignacion" in contenido
    assert "[dbo].[Voltara Enerval informe IVR]" in contenido
    assert "HAVING COUNT(*) >= 100" in contenido, (
        "Un skill con pocas llamadas da un share sin sentido (100% con 4 llamadas): "
        "tiene que quedar sin tramo para que el planificador avise en vez de inventar."
    )


def test_la_siembra_de_asignacion_no_pisa_lo_cargado():
    contenido = _contenido(MIGRACION_C)
    assert "IF NOT EXISTS (SELECT 1 FROM planificacion.Asignacion WHERE CampanaID = 20)" in contenido


def test_las_restricciones_de_la_planilla_nacen_sin_restringir():
    """Las cuatro columnas nacen en NULL. Aplicar la migración no puede mover
    ningún número: activar de golpe el criterio de Erlang B subiría la dotación de
    todos los intervalos sin que nadie lo haya decidido."""
    contenido = _contenido(MIGRACION_D)
    assert "MaxAsaSeg          SMALLINT     NULL" in contenido
    assert "MinNivelAtencionB  DECIMAL(5,4) NULL" in contenido
    # Nada de UPDATE ... SET fuera del bloque de ejemplo comentado.
    activos = [l for l in contenido.splitlines()
               if l.strip().startswith("UPDATE planificacion.Skill")]
    inicio = contenido.index("PARA REPRODUCIR EXACTAMENTE")
    for linea in activos:
        assert contenido.index(linea) > inicio, (
            "El UPDATE de ejemplo tiene que quedar dentro del bloque comentado.")


def test_el_segundo_nivel_de_servicio_exige_su_umbral():
    """Un objetivo sin umbral no significa nada, y al revés tampoco."""
    contenido = _contenido(MIGRACION_D)
    assert "(ObjetivoNds2 IS NULL AND UmbralSeg2 IS NULL)" in contenido
    assert "(ObjetivoNds2 IS NOT NULL AND UmbralSeg2 IS NOT NULL)" in contenido



def test_la_migracion_del_piso_solo_toca_el_minimo_del_pool_general():
    """Saca el piso de 2 del pool General y nada mas: ni estructura ni los pools
    inactivos. Y es idempotente: solo toca la fila si todavia no esta en 0."""
    contenido = _contenido(MIGRACION_R)
    sql = re.sub(r"/\*.*?\*/", "", contenido, flags=re.S)

    assert "SET MinOperadores = 0" in sql
    assert "CampanaID = 20" in sql and "Nombre = N'General'" in sql
    assert "AND MinOperadores <> 0" in sql
    for prohibido in ("CREATE TABLE", "ALTER TABLE", "DROP ", "DELETE ", "INSERT "):
        assert prohibido not in sql.upper(), prohibido


def test_la_migracion_del_refuerzo_siembra_digital_sin_agrupadas_ni_ajustes():
    """La operacion dijo que pasan todas las de Digital MENOS Agrupadas - Digital
    (139) y Ajustes - Lecturas (165). La lista va a mano: un LIKE '%Digital%'
    meteria a Agrupadas."""
    sql = re.sub(r"/\*.*?\*/", "", _contenido(MIGRACION_S), flags=re.S)

    sembradas = {int(m) for m in re.findall(r"VALUES \(@pool, (\d+),", sql)}
    assert sembradas == {53, 132, 48, 85}
    assert "LIKE" not in sql.upper()
    for cid in sembradas:
        assert (f"WHERE PoolID = @pool AND CampanaRRHHID = {cid})" in sql), (
            f"la siembra de {cid} tiene que ir bajo IF NOT EXISTS")


def test_la_migracion_del_refuerzo_crea_y_agrega_bajo_guarda():
    contenido = _contenido(MIGRACION_S)

    assert "IF OBJECT_ID('planificacion.PoolRefuerzo', 'U') IS NULL" in contenido
    assert ("IF COL_LENGTH('planificacion.Requerimiento', 'RefuerzoDisponible') IS NULL"
            in contenido)
    assert "RefuerzoCubre" in contenido
    # No borra ni toca el pool: la palanca no cambia quién es citado.
    sql = re.sub(r"/\*.*?\*/", "", contenido, flags=re.S).upper()
    assert "DELETE " not in sql and "POOLORIGEN (" not in sql


def test_la_migracion_de_t1_consumo_solo_la_suma_al_pool():
    """Suma la 177 al pool General y nada mas: no borra ninguna sub-campana (las
    otras cuatro telefonicas se quedan) y no toca la palanca de Digital."""
    assert os.path.exists(MIGRACION_T)
    sql = re.sub(r"/\*.*?\*/", "", _contenido(MIGRACION_T), flags=re.S)

    assert re.findall(r"VALUES \(@pool, (\d+),", sql) == ["177"]
    assert "WHERE PoolID = @pool AND CampanaRRHHID = 177)" in sql
    upper = sql.upper()
    for prohibido in ("DELETE ", "DROP ", "ALTER TABLE", "CREATE TABLE", "LIKE",
                      "POOLREFUERZO ("):
        assert prohibido not in upper, prohibido


def test_la_migracion_de_subcampanas_siembra_la_lista_de_la_operacion():
    """Tres telefónicas a veces y cinco digitales, enumeradas a mano. No toca el
    pool ni la palanca: esas sub-campañas no son citados ni refuerzo."""
    assert os.path.exists(MIGRACION_Y)
    contenido = _contenido(MIGRACION_Y)
    assert "IF OBJECT_ID('planificacion.PoolSubCampana', 'U') IS NULL" in contenido
    assert "CHECK (Clase IN ('telefonica_parcial', 'digital'))" in contenido
    sql = re.sub(r"/\*.*?\*/", "", contenido, flags=re.S)
    sembradas = dict((int(c), clase) for c, clase in
                     re.findall(r"VALUES \(@pool, (\d+), '(\w+)'", sql))
    assert sembradas == {178: "telefonica_parcial", 146: "telefonica_parcial",
                         88: "telefonica_parcial", 131: "digital", 58: "digital",
                         89: "digital", 139: "digital", 165: "digital"}
    upper = sql.upper()
    for prohibido in ("DELETE ", "DROP ", "LIKE", "POOLORIGEN (", "POOLREFUERZO ("):
        assert prohibido not in upper, prohibido


def test_la_migracion_de_t1_consumo_la_saca_del_pool_y_nada_mas():
    """Saca SÓLO la 177 del pool y la carga como dedicada. Ninguna otra telefónica
    se toca, ni la palanca."""
    assert os.path.exists(MIGRACION_Z)
    contenido = _contenido(MIGRACION_Z)
    assert "CHECK (Clase IN ('telefonica_parcial', 'digital', 'dedicada'))" in contenido
    sql = re.sub(r"/\*.*?\*/", "", contenido, flags=re.S)
    assert re.findall(r"DELETE FROM planificacion\.(\w+)\s+WHERE PoolID = @pool AND CampanaRRHHID = (\d+);",
                      sql) == [("PoolOrigen", "177")]
    assert re.findall(r"VALUES \(@pool, (\d+), '(\w+)'", sql) == [("177", "dedicada")]
    upper = sql.upper()
    for prohibido in ("DROP TABLE", "LIKE '%DIGITAL", "POOLREFUERZO"):
        assert prohibido not in upper, prohibido


def test_la_migracion_de_redondeo_nocturno_agrega_la_franja_y_siembra_0_a_8():
    assert os.path.exists(MIGRACION_AA)
    contenido = _contenido(MIGRACION_AA)
    assert "IF COL_LENGTH('planificacion.Campana', 'RedondeoAbajoDesde') IS NULL" in contenido
    assert "CK_Plan_Campana_RedondeoAbajo" in contenido
    sql = re.sub(r"/\*.*?\*/", "", contenido, flags=re.S)
    assert "SET RedondeoAbajoDesde = 0, RedondeoAbajoHasta = 8" in sql
    # No pisa una franja ya configurada.
    assert "AND RedondeoAbajoDesde IS NULL AND RedondeoAbajoHasta IS NULL" in sql


def test_la_migracion_de_refuerzo_pedido_crea_tabla_con_check_e_indices():
    """Crea planificacion.RefuerzoPedido bajo guarda, con CHECK de estados válidos
    e índice por (CampanaID, Dia). Idempotente y aditiva: no borra nada."""
    assert os.path.exists(MIGRACION_V)
    contenido = _contenido(MIGRACION_V)
    assert "IF OBJECT_ID('planificacion.RefuerzoPedido', 'U') IS NULL" in contenido
    assert "CREATE TABLE planificacion.RefuerzoPedido (" in contenido
    assert "RefuerzoPedidoID INT           IDENTITY(1,1) NOT NULL" in contenido
    assert "CHECK (Estado IN ('pedido','cubierto','cubierto_parcial','no_cubierto','descartado'))" in contenido
    assert "IX_Plan_RefuerzoPedido_Campana_Dia" in contenido
    _sin_borrados(contenido)


def _sin_borrados(contenido: str):
    sql = re.sub(r"/\*.*?\*/", "", contenido, flags=re.S).upper()
    for prohibido in ("DROP TABLE", "DELETE ", "TRUNCATE "):
        assert prohibido not in sql, prohibido


def test_la_migracion_de_antiguedad_crea_la_curva_y_la_columna():
    """Crea planificacion.CurvaAntiguedad bajo guarda y agrega
    Requerimiento.CitadosEquivalentes nullable. No siembra ni borra: sin curva cada
    citado cuenta 1 y el plan queda como antes."""
    contenido = _contenido(MIGRACION_X)
    assert "IF OBJECT_ID('planificacion.CurvaAntiguedad', 'U') IS NULL" in contenido
    assert "PRIMARY KEY (CampanaID, DiaDesde)" in contenido
    assert "CHECK (Factor > 0 AND Factor <= 2)" in contenido
    assert "COL_LENGTH('planificacion.Requerimiento', 'CitadosEquivalentes') IS NULL" in contenido
    assert "ADD CitadosEquivalentes DECIMAL(7,2) NULL" in contenido
    sql = re.sub(r"/\*.*?\*/", "", contenido, flags=re.S).upper()
    assert "INSERT " not in sql
    _sin_borrados(contenido)


def test_la_migracion_del_perfil_de_presencia_solo_crea_la_tabla():
    """Crea planificacion.PerfilPresencia bajo guarda, con los dos tipos de día, el
    minuto dentro del día y el exceso acotado. No siembra: sin filas, el shrinkage
    queda parejo como antes."""
    contenido = _contenido(MIGRACION_W)
    assert "IF OBJECT_ID('planificacion.PerfilPresencia', 'U') IS NULL" in contenido
    assert "PRIMARY KEY (CampanaID, TipoDia, Minuto)" in contenido
    assert "CHECK (TipoDia IN ('habil', 'no_habil'))" in contenido
    assert "CHECK (Minuto >= 0 AND Minuto < 1440)" in contenido
    assert "CHECK (Exceso BETWEEN -0.5 AND 0.5)" in contenido
    sql = re.sub(r"/\*.*?\*/", "", contenido, flags=re.S).upper()
    assert "INSERT " not in sql
    _sin_borrados(contenido)
    sql = re.sub(r"/\*.*?\*/", "", contenido, flags=re.S).upper()
    for prohibido in ("DROP TABLE", "DELETE ", "TRUNCATE "):
        assert prohibido not in sql, prohibido

