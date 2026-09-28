"""Tests offline de la lógica de tips por rol y herencia de grupos (app/rbac.py).

Sin BD: las funciones puras reciben los mapas de jerarquía y grupos como dicts,
garantizando velocidad y ejecución offline en CI / local.
"""
from datetime import date, timedelta
import hashlib
import pytest
from sqlalchemy import text

from app.rbac import resolver_grupos_efectivos


# Jerarquía de prueba:
# 1 (padre base)
#   └─ 2 (hijo de 1)
#        └─ 3 (hijo de 2, nieto de 1)
# 4 (rol independiente, sin padre)
# 5 (hijo de 4)
# 6 (rol suelto)
PADRES = {
    1: None,
    2: 1,
    3: 2,
    4: None,
    5: 4,
    6: None,
}

# Grupos asignados a roles:
# Grupo 100 asignado a 1 (debe heredarse a 2 y 3)
# Grupo 200 asignado a 2 (debe heredarse a 3, pero NO a 1)
# Grupo 300 asignado a 3 (solo para 3)
# Grupo 400 asignado a 4 (debe heredarse a 5)
GRUPOS_POR_ROL = {
    1: {100},
    2: {200},
    3: {300},
    4: {400},
    5: set(),
    6: set(),
}


def test_herencia_hacia_hijos_y_nietos():
    """Un grupo asignado al rol padre debe aplicarse a él, sus hijos y sus nietos."""
    # Rol 1 (padre)
    assert resolver_grupos_efectivos([1], PADRES, GRUPOS_POR_ROL) == {100}
    # Rol 2 (hijo): propio (200) + heredado del padre (100)
    assert resolver_grupos_efectivos([2], PADRES, GRUPOS_POR_ROL) == {100, 200}
    # Rol 3 (nieto): propio (300) + heredado de 2 (200) + heredado de 1 (100)
    assert resolver_grupos_efectivos([3], PADRES, GRUPOS_POR_ROL) == {100, 200, 300}


def test_no_herencia_hacia_arriba():
    """Los roles padre NO heredan los grupos asignados exclusivamente a sus hijos."""
    grupos_padre = resolver_grupos_efectivos([1], PADRES, GRUPOS_POR_ROL)
    assert 200 not in grupos_padre
    assert 300 not in grupos_padre


def test_roles_independientes():
    """Grupos asignados a un árbol no contaminan otros árboles."""
    # Rol 5 es hijo de 4, hereda grupo 400
    assert resolver_grupos_efectivos([5], PADRES, GRUPOS_POR_ROL) == {400}
    # Rol 6 no tiene grupos ni padre
    assert resolver_grupos_efectivos([6], PADRES, GRUPOS_POR_ROL) == set()


def test_usuario_multi_rol_union_de_grupos():
    """Si un usuario tiene varios roles, recibe la unión de todos los grupos aplicables."""
    # Usuario con rol 3 (árbol 1->2->3) y rol 5 (árbol 4->5)
    grupos = resolver_grupos_efectivos([3, 5], PADRES, GRUPOS_POR_ROL)
    assert grupos == {100, 200, 300, 400}


def test_usuario_sin_roles():
    """Usuario sin roles recibe conjunto vacío."""
    assert resolver_grupos_efectivos([], PADRES, GRUPOS_POR_ROL) == set()


def test_tolerancia_a_ciclos():
    """Un mapa con ciclo preexistente no cuelga la función."""
    padres_ciclo = {10: 11, 11: 10}
    grupos_ciclo = {10: {1}, 11: {2}}
    assert resolver_grupos_efectivos([10], padres_ciclo, grupos_ciclo) == {1, 2}


# -------------------------------------------------- Selección determinística
def seleccionar_tip_deterministico(tips: list, user_id: int, fecha: date):
    """Lógica pura de selección determinística por día y usuario."""
    if not tips:
        return None
    clave = f"{fecha.isoformat()}:{user_id}".encode("utf-8")
    hash_val = int(hashlib.md5(clave).hexdigest(), 16)
    idx = hash_val % len(tips)
    return tips[idx]


def test_seleccion_deterministica_misma_jornada():
    """Para el mismo usuario y el mismo día, siempre devuelve el mismo tip."""
    tips = ["Tip A", "Tip B", "Tip C", "Tip D", "Tip E"]
    hoy = date(2026, 9, 22)
    user_id = 12345678

    res1 = seleccionar_tip_deterministico(tips, user_id, hoy)
    res2 = seleccionar_tip_deterministico(tips, user_id, hoy)
    assert res1 == res2


def test_seleccion_deterministica_rota_otro_dia():
    """Al cambiar el día, la selección rota entre los tips disponibles."""
    tips = [f"Tip {i}" for i in range(10)]
    user_id = 12345678
    dias = [date(2026, 9, 22) + timedelta(days=i) for i in range(7)]

    resultados = [seleccionar_tip_deterministico(tips, user_id, d) for d in dias]
    # No todos los días deberían ser idénticos si hay 10 tips
    assert len(set(resultados)) > 1


# -------------------------------------------------- Validación SQL con PARSEONLY
def test_migracion_sql_sintaxis(engine):
    """Valida la sintaxis T-SQL de la migración con SET PARSEONLY ON contra SQL Server."""
    import os
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    path = os.path.join(repo_root, "scripts", "migrations", "2026-09-22_tips_del_dia_por_rol.sql")
    assert os.path.isfile(path), "El archivo de migración no existe"

    with open(path, encoding="utf-8") as f:
        contenido = f.read()

    batches = []
    batch = []
    for line in contenido.splitlines():
        if line.strip().upper() == "GO":
            if batch:
                batches.append("\n".join(batch))
                batch = []
        else:
            batch.append(line)
    if batch and "\n".join(batch).strip():
        batches.append("\n".join(batch))

    with engine.connect() as conn:
        for b in batches:
            b_clean = b.strip()
            if not b_clean or b_clean.upper().startswith("USE"):
                continue
            conn.execute(text("SET PARSEONLY ON"))
            try:
                conn.execute(text(b_clean))
            finally:
                conn.execute(text("SET PARSEONLY OFF"))


# -------------------------------------------------- Vigencia temporal y tipos
def es_tip_vigente(fecha_desde: date | None, fecha_hasta: date | None, fecha_ref: date) -> bool:
    """Verifica si un tip está vigente para la fecha de referencia."""
    if fecha_desde and fecha_desde > fecha_ref:
        return False
    if fecha_hasta and fecha_hasta < fecha_ref:
        return False
    return True


def test_filtrado_vigencia_fechas():
    """Valida los diferentes casos de vigencia temporal."""
    hoy = date(2026, 9, 22)

    # 1. Permanente (sin fechas)
    assert es_tip_vigente(None, None, hoy) is True

    # 2. Vigencia futura (aún no arrancó)
    manana = hoy + timedelta(days=1)
    assert es_tip_vigente(manana, None, hoy) is False

    # 3. Vigencia ya expirada
    ayer = hoy - timedelta(days=1)
    assert es_tip_vigente(None, ayer, hoy) is False

    # 4. Vigencia en curso (rango que incluye hoy)
    hace_tres_dias = hoy - timedelta(days=3)
    en_tres_dias = hoy + timedelta(days=3)
    assert es_tip_vigente(hace_tres_dias, en_tres_dias, hoy) is True

    # 5. Fechas exactamente iguales a hoy (un solo día de vigencia)
    assert es_tip_vigente(hoy, hoy, hoy) is True


def test_modelos_pydantic_tip_y_grupo():
    """Verifica la serialización y campos de los modelos Pydantic de Tips."""
    from app.models import (
        TipCreate,
        TipGroupResponse,
        TipResponseItem,
        TipsResponse,
        TipUpdate,
    )

    # TipCreate con tipo y vigencia
    tc = TipCreate(
        group_id=1,
        title="Alerta operativa",
        content="Revisar cortes ENRE",
        tipo="warning",
        fecha_desde="2026-09-22",
        fecha_hasta="2026-09-25",
    )
    assert tc.tipo == "warning"
    assert tc.fecha_desde == "2026-09-22"

    # TipResponseItem
    tri = TipResponseItem(
        id=10,
        group_id=1,
        group_name="Operaciones",
        title="Alerta",
        content="Contenido",
        tipo="warning",
        fecha_desde="2026-09-22",
        fecha_hasta="2026-09-25",
        activo=True,
        created_at="2026-09-22 10:00:00",
    )
    assert tri.tipo == "warning"
    assert tri.group_name == "Operaciones"

    # TipsResponse (consumido por la UI en la home y chatbot)
    tr = TipsResponse(
        tips="Cuidar la cordialidad",
        title="Consejo",
        group_name="Calidad",
        id=5,
        tipo="success",
    )
    assert tr.tipo == "success"
    assert tr.group_name == "Calidad"

    # TipGroupResponse con usuarios_alcanzados
    tgr = TipGroupResponse(
        id=1,
        name="Calidad General",
        description="Tips para todos",
        activo=True,
        role_ids=[2, 3],
        roles_nombres=["Supervisor", "Coordinador"],
        tips_count=4,
        usuarios_alcanzados=42,
    )
    assert tgr.usuarios_alcanzados == 42
    assert len(tgr.roles_nombres) == 2


def test_calculo_usuarios_alcanzados_jerarquia():
    """Calcula usuarios únicos alcanzados por un grupo a través de la jerarquía de roles."""
    # Jerarquía: 1 (Jefe) -> 2 (Supervisor) -> 3 (Operador)
    padres = {1: None, 2: 1, 3: 2}
    # Grupo 50 asignado al Jefe (1)
    grupos_por_rol = {1: {50}, 2: set(), 3: set()}

    # Empleados y sus roles:
    # Juan: Jefe (1)
    # Maria: Supervisor (2)
    # Pedro: Operador (3)
    # Lucas: Operador (3)
    # Carlos: Rol 9 (sin relación)
    usuarios_roles = {
        "Juan": [1],
        "Maria": [2],
        "Pedro": [3],
        "Lucas": [3],
        "Carlos": [9],
    }

    # Resolver qué usuarios reciben el grupo 50
    alcanzados = set()
    for user, roles in usuarios_roles.items():
        grupos_usuario = resolver_grupos_efectivos(roles, padres, grupos_por_rol)
        if 50 in grupos_usuario:
            alcanzados.add(user)

    # Debido a la herencia hacia abajo (1 -> 2 -> 3), Juan, Maria, Pedro y Lucas deben recibir el tip
    assert alcanzados == {"Juan", "Maria", "Pedro", "Lucas"}
    assert len(alcanzados) == 4
    assert "Carlos" not in alcanzados


# -------------------------------------------------- Prioridad, Feedback y Acción
def test_seleccion_prioritaria():
    """Si hay un tip prioritario activo y vigente, debe elegirse antes que los tips comunes."""
    class MockTip:
        def __init__(self, id, content, es_prioritario=False):
            self.id = id
            self.content = content
            self.es_prioritario = es_prioritario

    tips_comunes = [MockTip(1, "Tip A"), MockTip(2, "Tip B"), MockTip(3, "Tip C")]
    tip_urgente = MockTip(99, "Alerta Urgente", es_prioritario=True)

    todos = tips_comunes + [tip_urgente]

    # Filtrar prioritarios
    prioritarios = [t for t in todos if t.es_prioritario]
    candidatos = prioritarios if prioritarios else todos

    assert len(candidatos) == 1
    assert candidatos[0].id == 99
    assert candidatos[0].content == "Alerta Urgente"


def test_modelos_prioridad_accion_feedback():
    """Valida los campos es_prioritario, url_accion, texto_accion y feedback en los modelos."""
    from app.models import (
        TipCreate,
        TipFeedbackResponse,
        TipResponseItem,
        TipsResponse,
        TipUpdate,
    )

    tc = TipCreate(
        group_id=1,
        title="Alerta Caída de Sistema",
        content="Registrar en planilla manual",
        tipo="warning",
        es_prioritario=True,
        url_accion="/chatbot",
        texto_accion="Ir al Asistente",
    )
    assert tc.es_prioritario is True
    assert tc.url_accion == "/chatbot"
    assert tc.texto_accion == "Ir al Asistente"

    tri = TipResponseItem(
        id=15,
        group_id=1,
        title="Alerta",
        content="Texto",
        es_prioritario=True,
        url_accion="https://procedimientos.acme.example",
        texto_accion="Ver PDF",
        feedback_count=12,
        activo=True,
    )
    assert tri.es_prioritario is True
    assert tri.feedback_count == 12

    tr = TipsResponse(
        tips="Atender con calma",
        es_prioritario=True,
        url_accion="/solicitudes",
        texto_accion="Revisar solicitudes",
        likes_count=5,
        user_voted=True,
    )
    assert tr.likes_count == 5
    assert tr.user_voted is True

    fb_resp = TipFeedbackResponse(ok=True, likes_count=6, user_voted=True)
    assert fb_resp.ok is True
    assert fb_resp.likes_count == 6


def test_toggle_feedback_logica():
    """Prueba la lógica pura de alternar el voto / feedback de un usuario sobre un tip."""
    votos = set()  # set de (tip_id, user_id)

    def toggle(tip_id, user_id):
        key = (tip_id, user_id)
        if key in votos:
            votos.remove(key)
            return False, len([k for k in votos if k[0] == tip_id])
        else:
            votos.add(key)
            return True, len([k for k in votos if k[0] == tip_id])

    # Usuario 1 vota tip 10
    voted, count = toggle(10, 1001)
    assert voted is True
    assert count == 1

    # Usuario 2 vota tip 10
    voted, count = toggle(10, 1002)
    assert voted is True
    assert count == 2

    # Usuario 1 vuelve a presionar (cancela su voto)
    voted, count = toggle(10, 1001)
    assert voted is False
    assert count == 1



