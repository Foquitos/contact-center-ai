"""
Usuarios reciclados: que la auditoría quede a nombre de quien corresponde.

`calidad.Auditorias` guarda el string de la plataforma (`operadorUsuario`), no a
la persona, y esos usuarios se reciclan: hoy hay 1.429 strings que apuntan a más
de una persona y 18.603 auditorías activas colgando de uno de ellos. El caso
testigo es la auditoría 98046 (usuario '642409', empresa 10 = PAGONET, llamado del
2026-08-20): figuraba a nombre del legajo 6470, que ese día estaba en VOLTARA, en
lugar del legajo 11276, que estaba en Retención Paygo.

Acá se valida:
  * que las tres migraciones parseen contra SQL Server sin aplicarlas,
  * que el SP siga exponiendo lo que esperan sus llamadores y ya no re-infiera a
    la persona en cada lectura,
  * que el INSERT de auditorías congele la persona cuando la migración está
    aplicada (y siga andando igual cuando no lo está),
  * y —si la migración ya corrió— que el 98046 resuelva al legajo correcto.

No gasta tokens.
"""
import os
import re
from contextlib import contextmanager
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import text

from conftest import _validar_parseonly
from AuditorIA import sql_a_Claude

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRACIONES = os.path.join(REPO_ROOT, "scripts", "migrations")

BASE = os.path.join(MIGRACIONES, "2026-08-25_operador_reciclado.sql")
BACKFILL = os.path.join(MIGRACIONES, "2026-08-25b_backfill_operador_nomina.sql")
SP = os.path.join(MIGRACIONES, "2026-08-25c_sp_auditorias_operador_congelado.sql")


def _sql(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _sin_comentarios(path):
    """El script sin los bloques /* ... */, para no contar las menciones de la doc."""
    return re.sub(r"/\*.*?\*/", "", _sql(path), flags=re.DOTALL)


def _batches(sql_text):
    """Divide el script por líneas 'GO' (mismo criterio que el resto de la suite)."""
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
    return [b for b in batches if b.strip()]


# --------------------------------------------------------------------------- #
# 1. Sintaxis contra SQL Server, sin aplicar nada                              #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("archivo", [BASE, BACKFILL, SP])
def test_las_migraciones_parsean(engine, archivo):
    if not os.path.exists(archivo):
        pytest.skip(f"{os.path.basename(archivo)} no está en esta copia local")
    for i, batch in enumerate(_batches(_sql(archivo))):
        resultado = _validar_parseonly(engine, batch)
        assert resultado.ok, f"batch {i} de {os.path.basename(archivo)}: {resultado.error}"


# --------------------------------------------------------------------------- #
# 2. El SP: lo que no puede cambiar y lo que sí tiene que haber cambiado       #
# --------------------------------------------------------------------------- #
PARAMS_HISTORICOS = [
    "@AuditorUsuarioID", "@CampanaID", "@EmpresaID", "@PlantillaID",
    "@FechaDesde", "@FechaHasta", "@FechaInteraccionDesde", "@FechaInteraccionHasta",
    "@IdAplicativo", "@IncluirTranscripcion", "@IncluirResponseThoughts",
    "@Offset", "@Fetch",
]

COLUMNAS_FIJAS = [
    "AuditoriaID", "AuditorUsuarioID", "IdAplicativo", "operadorUsuario",
    "Equipo", "Agente", "Legajo", "sentido_interaccion", "tipificacion_interaccion",
    "duracion_segundos", "comentario_interaccion", "fecha_interaccion",
    "FechaAuditoria", "extras", "PuntajeFinal", "EsErrorCritico",
]

sp_necesario = pytest.mark.skipif(not os.path.exists(SP),
                                  reason="La migración del SP no está en esta copia local")


@sp_necesario
def test_el_sp_conserva_su_interfaz():
    """Sacar o renombrar un parámetro rompe tasks.py, Auditor.py y bandeja.py."""
    sql = _sql(SP)
    for parametro in PARAMS_HISTORICOS:
        assert re.search(rf"{parametro}\s+\w+", sql), f"falta el parámetro {parametro}"
    for columna in COLUMNAS_FIJAS:
        assert columna in sql, f"el SELECT ya no expone {columna}"


@sp_necesario
def test_el_sp_lee_la_persona_congelada():
    sql = _sin_comentarios(SP)
    assert "A_Ext.OperadorNominaID" in sql, "el SP tiene que usar la persona congelada"
    assert "calidad.fn_ResolverOperadorAuditoria" in sql, "falta el camino de las no congeladas"


@sp_necesario
def test_el_sp_ya_no_re_infiere_la_persona_en_cada_lectura():
    """La memoización por (operadorUsuario, EmpresaID) existía para abaratar una
    resolución que ahora no se hace acá. Si vuelve a aparecer, volvió el criterio
    viejo —el que atribuía el llamado a quien tuviera el alta más nueva—."""
    sql = _sin_comentarios(SP)
    for temporal in ("#Par", "#Cand", "#Persona"):
        assert temporal not in sql, f"{temporal} tendría que haber desaparecido del SP"
    assert "JOIN usuarios u ON u.usuario" not in sql
    assert "FROM usuarios u" not in sql


@sp_necesario
def test_el_rollback_del_sp_sigue_disponible():
    """La vuelta atrás es volver a correr la migración anterior, así que tiene que
    seguir existiendo y estar nombrada en el encabezado."""
    anterior = os.path.join(MIGRACIONES, "2026-08-14b_sp_auditorias_filtradas_performance.sql")
    assert "2026-08-14b_sp_auditorias_filtradas_performance.sql" in _sql(SP)
    if not os.path.exists(anterior):
        pytest.skip("la migración anterior no está en esta copia local")


# --------------------------------------------------------------------------- #
# 3. El backfill                                                               #
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not os.path.exists(BACKFILL), reason="no está en esta copia local")
def test_el_backfill_no_pisa_lo_ya_resuelto():
    """Tiene que poder cortarse y retomarse: solo escribe donde está en NULL."""
    sql = _sin_comentarios(BACKFILL)
    assert "WHERE a.OperadorNominaID IS NULL" in sql


@pytest.mark.skipif(not os.path.exists(BACKFILL), reason="no está en esta copia local")
def test_el_backfill_no_escribe_sobre_un_cte():
    """Un `UPDATE ... FROM cte` solo escribe las columnas que el CTE expone en su
    SELECT: así se cayó la cola de transcripciones en producción el 2026-08-25."""
    sql = _sin_comentarios(BACKFILL)
    assert "UPDATE a" in sql and "FROM calidad.Auditorias a" in sql
    assert not re.search(r"WITH\s+\w+\s+AS\s*\(", sql, re.IGNORECASE), \
        "el backfill no tiene que actualizar a través de un CTE"


@pytest.mark.skipif(not os.path.exists(BACKFILL), reason="no está en esta copia local")
def test_el_backfill_termina_aunque_no_escriba_filas():
    """Un usuario que no resuelve a nadie no devuelve fila en el CROSS APPLY. Si el
    corte del WHILE dependiera del @@ROWCOUNT del UPDATE, esas auditorías dejarían
    el loop girando para siempre."""
    sql = _sin_comentarios(BACKFILL)
    assert "WHILE EXISTS (SELECT 1 FROM #Pendientes)" in sql
    assert "DELETE p FROM #Pendientes p JOIN #Lote l" in sql


# --------------------------------------------------------------------------- #
# 4. El INSERT de auditorías congela la persona (offline)                      #
# --------------------------------------------------------------------------- #
class _ConnFalsa:
    """Conexión de mentira: registra las queries en vez de ejecutarlas."""

    def __init__(self, registro, columnas, hay_funcion):
        self.registro = registro
        self.columnas = columnas
        self.hay_funcion = hay_funcion

    def execute(self, query, params=None):
        sql = str(query)
        self.registro.append((sql, params))

        if 'INFORMATION_SCHEMA.COLUMNS' in sql:
            existe = (params or {}).get('columna') in self.columnas
            return SimpleNamespace(first=lambda: (1,) if existe else None)
        if 'OBJECT_ID' in sql and 'fn_ResolverOperadorAuditoria' in sql:
            return SimpleNamespace(scalar=lambda: 999 if self.hay_funcion else None)
        if 'FROM calidad.Atributos' in sql:
            return SimpleNamespace(fetchall=lambda: [(1, 'enum', 1.0)])
        if 'INSERT INTO calidad.Auditorias' in sql:
            return SimpleNamespace(scalar=lambda: 12345)
        return SimpleNamespace(scalar=lambda: None, fetchall=lambda: [])


class _EngineFalso:
    def __init__(self, columnas=('OperadorNominaID',), hay_funcion=True):
        self.registro = []
        self.columnas = set(columnas)
        self.hay_funcion = hay_funcion

    @contextmanager
    def begin(self):
        yield _ConnFalsa(self.registro, self.columnas, self.hay_funcion)

    @contextmanager
    def connect(self):
        yield _ConnFalsa(self.registro, self.columnas, self.hay_funcion)


def _fila(**extra):
    base = {
        'id_aplicativo': 'ABC123', 'operador_usuario': '642409', 'fecha_interaccion': None,
        'sentido_interaccion': 'Entrante', 'tipificacion_interaccion': None,
        'duracion_segundos': 100, 'comentario_interaccion': None, 'campana_id': 19,
        'empresa_id': 10, 'plantilla_id': 12, 'auditor_id': 1, 'input_tokens': 1,
        'output_tokens': 1, 'thoughts_tokens': 0, 'response_thoughts': None,
        'Extras': None, 'Detalle_1': 'Cumple',
    }
    base.update(extra)
    return pd.DataFrame([base])


def _insert_de(engine):
    sql_a_Claude.auditoria_a_SQL(df=_fila(), engine=engine, modo="sync", modelo="gemini-fake")
    return next(s for s, _ in engine.registro if 'INSERT INTO calidad.Auditorias' in s)


def test_con_la_migracion_aplicada_la_persona_queda_congelada():
    engine = _EngineFalso(columnas=('OperadorNominaID',), hay_funcion=True)
    insert = _insert_de(engine)

    assert 'OperadorNominaID' in insert
    assert 'calidad.fn_ResolverOperadorAuditoria(' in insert
    # Se resuelve con la fecha y la empresa del llamado: sin eso vuelve a elegir por
    # "quién tiene el alta más nueva", que es exactamente el bug.
    assert ':operador_usuario, :emp_id, :fecha_interaccion' in insert


def test_sin_la_columna_se_guarda_como_siempre():
    """El backend se deploya por git y la migración la corre Ignacio a mano: entre
    una cosa y la otra el INSERT tiene que seguir funcionando."""
    insert = _insert_de(_EngineFalso(columnas=(), hay_funcion=True))
    assert 'OperadorNominaID' not in insert


def test_con_la_columna_pero_sin_la_funcion_tampoco_se_arriesga():
    """Si alguien aplica media migración, mejor guardar sin congelar que reventar
    en cada INSERT de auditoría."""
    insert = _insert_de(_EngineFalso(columnas=('OperadorNominaID',), hay_funcion=False))
    assert 'OperadorNominaID' not in insert


# --------------------------------------------------------------------------- #
# 5. La query ALARMIX de la Bandeja                                                #
# --------------------------------------------------------------------------- #
def test_el_enriquecido_alarmix_resuelve_el_equipo_a_la_fecha_de_la_grabacion(engine):
    """En ALARMIX el equipo se completa desde la grabación. Tomar la asignación más
    nueva ponía el equipo de HOY en un llamado de hace meses."""
    from app.routers import bandeja

    sql = str(bandeja._RESOLVER_ALARMIX_SQL)
    assert "segmentContactStartTime" in sql, "la resolución tiene que mirar la fecha"
    assert "o.fecha_desde DESC" not in sql, "volvió el criterio de 'la asignación más nueva'"

    # El bindparam expandible se renderiza como `__[POSTCOMPILE_ids]`: se reemplaza
    # por una lista literal para poder pasarle la query al parser.
    literal = re.sub(r"__\[POSTCOMPILE_ids\]|:ids", "('x')", sql)
    resultado = _validar_parseonly(engine, literal)
    assert resultado.ok, f"la query ALARMIX no parsea: {resultado.error}"


# --------------------------------------------------------------------------- #
# 6. Contra la base: el caso testigo (solo si la migración ya corrió)          #
# --------------------------------------------------------------------------- #
def _hay_funcion(engine):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT OBJECT_ID('calidad.fn_ResolverOperadorAuditoria', 'IF')")
        ).scalar() is not None


def test_la_98046_resuelve_al_operador_correcto(engine):
    """El caso que originó todo: llamado de PAGONET del 2026-08-20 con el usuario
    '642409', que pertenece a 4 personas. La correcta es la que ese día estaba en
    Retención Paygo (legajo 11276), no la que estaba en VOLTARA (legajo 6470)."""
    if not _hay_funcion(engine):
        pytest.skip("la migración 2026-08-25 todavía no está aplicada")

    with engine.connect() as conn:
        fila = conn.execute(text("""
            SELECT r.Legajo, r.Criterio
            FROM calidad.Auditorias a
            CROSS APPLY calidad.fn_ResolverOperadorAuditoria(
                a.operadorUsuario, a.EmpresaID, a.fecha_interaccion) r
            WHERE a.AuditoriaID = 98046
        """)).first()

    if fila is None:
        pytest.skip("la auditoría 98046 ya no está en la base")

    assert fila[0] == '11276', f"la 98046 resolvió al legajo {fila[0]}"
    assert fila[1] == 1, "tiene que resolverse por vigencia + empresa, no por proximidad"
