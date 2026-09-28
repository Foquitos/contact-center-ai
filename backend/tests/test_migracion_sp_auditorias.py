"""
Valida la migración del SP de auditorías ANTES de aplicarla.

`scripts/migrations/2026-08-14b_sp_auditorias_filtradas_performance.sql` reescribe
`calidad.sp_ObtenerAuditoriasFiltradas` (el motor de Auditorías Realizadas, la
Bandeja, el scheduler y el export de batch). Como el SP vive solo en la base, el
archivo es lo único revisable: acá se lo pasa por `SET PARSEONLY ON`, que valida
sintaxis SIN crear el procedimiento, sin ejecutarlo y sin escanear datos.

También se chequean invariantes del texto que, si se rompen, rompen a los
llamadores actuales (tasks.py / Auditor.py / bandeja.py): que los parámetros
viejos sigan existiendo y que los nuevos sean opcionales.

No gasta tokens. Se saltea solo si la migración ya no está en el repo
(`scripts/migrations/` está gitignoreado: los .sql viven en la copia local).
"""
import os
import re

import pytest

# El validador por defecto (`validar_sql`) elige `describe` cuando la query no
# tiene temp tables, y ese DMV no acepta DDL ("Incorrect syntax near 'OR'" ante un
# CREATE OR ALTER PROCEDURE). Para un script de migración el chequeo correcto es
# PARSEONLY: valida sintaxis sin crear ni ejecutar nada.
from conftest import _validar_parseonly

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRACION = os.path.join(REPO_ROOT, "scripts", "migrations",
                         "2026-08-14b_sp_auditorias_filtradas_performance.sql")
ROLLBACK = os.path.join(REPO_ROOT, "scripts", "migrations",
                        "2026-08-14b_sp_auditorias_filtradas_ROLLBACK.sql")

pytestmark = pytest.mark.skipif(not os.path.exists(MIGRACION),
                                reason="La migración no está en esta copia local")

# Parámetros que los llamadores actuales pasan por nombre: sacar o renombrar
# cualquiera de estos rompe tasks.py, Auditor.py y bandeja.py.
PARAMS_HISTORICOS = [
    "@AuditorUsuarioID", "@CampanaID", "@EmpresaID", "@PlantillaID",
    "@FechaDesde", "@FechaHasta", "@FechaInteraccionDesde", "@FechaInteraccionHasta",
    "@IdAplicativo", "@IncluirTranscripcion", "@IncluirResponseThoughts",
]

# Columnas fijas del result set, en el orden en que las espera el front
# (COLUMNAS_FIJAS_AUDITORIAS de app/routers/auditoria.py).
COLUMNAS_FIJAS = [
    "AuditoriaID", "AuditorUsuarioID", "IdAplicativo", "operadorUsuario",
    "Equipo", "Agente", "Legajo", "sentido_interaccion", "tipificacion_interaccion",
    "duracion_segundos", "comentario_interaccion", "fecha_interaccion",
    "FechaAuditoria", "extras", "PuntajeFinal", "EsErrorCritico",
]


def _sql(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _sql_sin_comentarios(path):
    """El texto del script sin los bloques /* ... */, para contar ocurrencias
    reales de código y no las menciones de la doc de la migración."""
    return re.sub(r"/\*.*?\*/", "", _sql(path), flags=re.DOTALL)


def _batches(sql_text):
    """Divide el script en batches por líneas 'GO' (mismo criterio que el resto
    de la suite de migraciones)."""
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
# Sintaxis contra SQL Server (sin aplicar nada)                                #
# --------------------------------------------------------------------------- #
def test_migracion_parsea(engine):
    for i, batch in enumerate(_batches(_sql(MIGRACION))):
        resultado = _validar_parseonly(engine, batch)
        assert resultado.ok, f"batch {i} de la migración no parsea: {resultado.error}"


def test_rollback_parsea(engine):
    if not os.path.exists(ROLLBACK):
        pytest.skip("El rollback no está en esta copia local")
    for i, batch in enumerate(_batches(_sql(ROLLBACK))):
        resultado = _validar_parseonly(engine, batch)
        assert resultado.ok, f"batch {i} del rollback no parsea: {resultado.error}"


# --------------------------------------------------------------------------- #
# Compatibilidad con los llamadores                                            #
# --------------------------------------------------------------------------- #
def test_conserva_los_parametros_historicos():
    sql = _sql(MIGRACION)
    for parametro in PARAMS_HISTORICOS:
        assert re.search(rf"{parametro}\s+\w+", sql), f"falta el parámetro {parametro}"


def test_los_parametros_nuevos_son_opcionales():
    """Sin @Offset/@Fetch el SP tiene que comportarse igual que antes, así que
    tienen que tener default (los llamadores viejos no los pasan)."""
    sql = _sql(MIGRACION)
    for parametro in ("@Offset", "@Fetch"):
        assert re.search(rf"{parametro}\s+INT\s*=\s*NULL", sql), \
            f"{parametro} tiene que ser opcional (= NULL)"


def test_devuelve_las_columnas_fijas_que_espera_el_front():
    sql = _sql(MIGRACION)
    for columna in COLUMNAS_FIJAS:
        assert columna in sql, f"el SELECT ya no expone {columna}"


def test_el_total_solo_viaja_cuando_hay_paginado():
    """[__Total] es una columna técnica del paginado; si apareciera siempre, se
    colaría en el Excel del scheduler y en el dashboard."""
    sql = _sql(MIGRACION)
    assert "@Paginado = 1 THEN N', CAST(@Total_Param AS INT) AS [__Total]'" in sql


def test_transcripcion_por_apply_top_1():
    """El LEFT JOIN a transcripciones duplicaba auditorías (128 IdAplicativo con
    más de una transcripción). Tiene que quedar como OUTER APPLY TOP 1."""
    sql = _sql_sin_comentarios(MIGRACION)
    assert "LEFT JOIN [Acme].[calidad].[transcripciones]" not in sql
    assert sql.count("FROM [Acme].[calidad].[transcripciones]") == 2  # existencia + texto


def test_orden_estable_para_paginar():
    """Sin desempate, dos filas con la misma FechaAuditoria pueden repetirse o
    perderse al pasar de página."""
    assert "ORDER BY A_Ext.FechaAuditoria DESC, A_Ext.AuditoriaID DESC" in _sql(MIGRACION)


def test_conserva_el_criterio_de_desempate_por_empresa():
    """La resolución memoizada tiene que seguir usando el normalizador de empresa,
    y las parejas ambiguas tienen que caer al camino original."""
    sql = _sql_sin_comentarios(MIGRACION)
    assert sql.count("Normalizador_calidad_omnia") == 2   # memoizado + fallback exacto
    assert "CandidatosMejorRank = 1" in sql
    assert "CandidatosMejorRank > 1" in sql
