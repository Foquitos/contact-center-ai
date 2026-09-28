"""
Valida contra SQL Server las consultas de `AuditorIA/evidencia_plantilla.py`
SIN ejecutarlas ni escanear datos (cero tokens, cero escritura).

POR QUÉ
-------
Esas consultas son las únicas del asistente de plantillas que tocan tablas de
auditorías (`calidad.AuditoriaDetalles`, `calidad.Auditorias`) y se escribieron sin
poder abrir la base. Un nombre de columna equivocado no se vería en ningún test offline:
aparecería recién cuando alguien toca "Revisar todo con IA" en producción y, como la
lectura de evidencia es best-effort, ni siquiera fallaría de forma visible — la revisión
saldría muda, sin datos, y nadie se enteraría de por qué.

`sys.dm_exec_describe_first_result_set` valida sintaxis **y binding** (que las tablas y
columnas existan) sin ejecutar la consulta: la query viaja como parámetro al DMV.

Los parámetros con nombre (`:pid`) son de SQLAlchemy, no T-SQL, así que se reemplazan
por literales antes de validar.

Requiere conexión a la base (la fixture `engine` de conftest). No gasta tokens.
Correr: pytest tests/test_evidencia_plantilla_sql.py -m "not tokens"
"""
import re

import pytest

from AuditorIA import evidencia_plantilla as ev

# Literales de reemplazo para los binds. El tipo importa: describe compila la query.
LITERALES = {
    "pid": "1",
    "desde": "'2026-01-01'",
    "marca": "N'… [recortado]'",
    "largo_marca": "13",
    "tope": "2000",
}

TIPOS = ", ".join(f"'{t}'" for t in ev.TIPOS_ACOTADOS)


def _sin_binds(sql: str) -> str:
    return re.sub(r":(\w+)", lambda m: LITERALES[m.group(1)], sql)


@pytest.mark.parametrize("nombre,sql", [
    ("total_auditorias", ev.SQL_TOTAL_AUDITORIAS.format(col_fecha="FechaAuditoria")),
    ("uso_por_atributo", ev.SQL_USO_POR_ATRIBUTO.format(col_fecha="FechaAuditoria")),
    ("distribucion", ev.SQL_DISTRIBUCION.format(col_fecha="FechaAuditoria", tipos=TIPOS)),
])
def test_las_consultas_de_evidencia_compilan_contra_la_base(nombre, sql, validar_sql):
    resultado = validar_sql(_sin_binds(sql))
    assert resultado, f"{nombre}: {resultado.error}"


def test_la_columna_de_fecha_alternativa_tambien_compila(validar_sql):
    """`_columna_fecha` cae a `fecha_interaccion` si no existe `FechaAuditoria`; el
    fallback tiene que ser válido, no un error latente."""
    sql = ev.SQL_TOTAL_AUDITORIAS.format(col_fecha="fecha_interaccion")
    resultado = validar_sql(_sin_binds(sql))
    assert resultado, resultado.error
