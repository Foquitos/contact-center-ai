"""Tests del cupo mensual de auditorías por campaña (app/cuotas.py).

Offline (sin BD ni tokens): todo lo que se prueba acá son las reglas puras — a
quién alcanza el cupo, cuánto se reserva, cuándo entra un pedido y qué hacer con
una reserva cuando la corrida terminó. Lo que toca la BD (reservar/conciliar) se
apoya en estas mismas funciones.
"""
import pytest
from datetime import datetime, timedelta, timezone

from app import cuotas
from app.models import User


def _user(permissions=(), is_super=False):
    return User(usuario=123, Nombre="Test", permissions=list(permissions), is_super_admin=is_super)


# ------------------------------------------------------------------ exención

@pytest.mark.parametrize("perms,is_super,exento", [
    (["audit:cuota_exento"], False, True),
    ([], True, True),                                  # super admin, siempre afuera
    (["audit:execute"], False, False),                 # el supervisor sí consume cupo
    (["audit:execute", "audit:sync"], False, False),
    (["audit:cuotas"], False, False),                  # gestionar cupos no exime de ellos
], ids=["exento", "super-admin", "supervisor", "supervisor-sync", "gerente"])
def test_esta_exento(perms, is_super, exento):
    assert cuotas.esta_exento(_user(perms, is_super)) is exento


def test_puede_gestionar():
    assert cuotas.puede_gestionar(_user(["audit:cuotas"])) is True
    assert cuotas.puede_gestionar(_user([], is_super=True)) is True
    assert cuotas.puede_gestionar(_user(["audit:execute"])) is False


# ------------------------------------------------------------------- período

def test_mes_actual_usa_hora_argentina():
    """A las 22:00 del 31 en Argentina ya es día 1 en UTC: el cupo no se puede
    renovar tres horas antes de que termine el mes para el usuario."""
    fin_de_mes_arg = datetime(2026, 8, 31, 22, 0, tzinfo=cuotas._TZ_ART)
    assert cuotas.mes_actual(fin_de_mes_arg) == "2026-08"
    assert fin_de_mes_arg.astimezone(timezone.utc).strftime("%Y-%m") == "2026-09"


# ------------------------------------------------------- cuánto se reserva

def test_reserva_la_cantidad_pedida():
    assert cuotas.cantidad_a_reservar(50) == 50
    assert cuotas.cantidad_a_reservar(None) == 0
    assert cuotas.cantidad_a_reservar(-5) == 0


@pytest.mark.parametrize("por_operador,por_tipificacion", [(True, False), (False, True), (True, True)])
def test_muestreo_por_grupo_reserva_el_tope(por_operador, por_tipificacion):
    """Con muestreo por grupo `cantidad` es POR GRUPO: el total real puede llegar
    al tope duro de 200, así que se reserva ese techo y se ajusta al cerrar."""
    reservado = cuotas.cantidad_a_reservar(5, por_operador=por_operador,
                                           por_tipificacion=por_tipificacion)
    assert reservado == cuotas.LIMITE_MUESTREO_GRUPO == 200


# --------------------------------------------------------------- saldo/cupo

def test_disponible_sin_tope_es_none():
    assert cuotas.calcular_disponible(None, 900) is None


def test_disponible_no_es_negativo():
    """Si el cupo se bajó por debajo de lo ya consumido, el saldo es 0, no negativo."""
    assert cuotas.calcular_disponible(100, 130) == 0


def _estado(limite=None, consumido=0, limite_usuario=None, consumido_usuario=0):
    return {
        "anio_mes": "2026-08",
        "limite": limite,
        "limite_usuario": limite_usuario,
        "consumido": consumido,
        "consumido_usuario": consumido_usuario,
        "disponible": cuotas.calcular_disponible(limite, consumido),
        "disponible_usuario": cuotas.calcular_disponible(limite_usuario, consumido_usuario),
    }


def test_sin_cupo_configurado_entra_cualquier_cosa():
    """Una campaña sin cupo se mide pero no bloquea (así el deploy no cambia nada)."""
    assert cuotas.entra_en_cupo(_estado(), 5000) is None


def test_entra_lo_que_cabe_en_la_bolsa():
    assert cuotas.entra_en_cupo(_estado(limite=200, consumido=150), 50) is None
    assert cuotas.entra_en_cupo(_estado(limite=200, consumido=150), 51) == "campana"


def test_el_tope_personal_corta_antes_que_la_bolsa():
    """La bolsa de la campaña tiene lugar, pero el usuario ya gastó lo suyo."""
    estado = _estado(limite=200, consumido=50, limite_usuario=30, consumido_usuario=25)
    assert cuotas.entra_en_cupo(estado, 10) == "usuario"
    assert cuotas.entra_en_cupo(estado, 5) is None


def test_mensaje_dice_cuanto_queda_y_a_quien_pedirle():
    estado = _estado(limite=200, consumido=190)
    mensaje = cuotas.mensaje_cupo_agotado(estado, 50, "campana")
    assert "10" in mensaje and "200" in mensaje
    assert "gerente de operaciones" in mensaje


def test_mensaje_personal_habla_del_cupo_propio():
    estado = _estado(limite=200, consumido=50, limite_usuario=30, consumido_usuario=30)
    mensaje = cuotas.mensaje_cupo_agotado(estado, 5, "usuario")
    assert "personal" in mensaje


# ------------------------------------------------------------- referencias

def test_referencia_de_grupo_le_gana_al_task_id():
    """Las tandas CSV/Voltara comparten UNA fila de log: si cada tanda reservara
    por su task_id, solo la primera encontraría con qué conciliar."""
    assert cuotas.referencia_auditoria(task_id="audit_1", upload_group_id="g-9") == "grupo:g-9"
    assert cuotas.referencia_auditoria(task_id="audit_1") == "task:audit_1"


def test_clave_de_referencia_apunta_a_la_columna_del_log():
    assert cuotas._clave_de_referencia("task:audit_1") == ("task_id", "audit_1")
    assert cuotas._clave_de_referencia("grupo:g-9") == ("upload_group_id", "g-9")
    # Una transcripción se cierra en el acto: el barrido no tiene nada que hacer.
    assert cuotas._clave_de_referencia("transcripcion:abc") == (None, None)


# ------------------------------------------------------------ conciliación

def test_corrida_terminada_se_ajusta_a_lo_auditado():
    """Se pidieron 50, se auditaron 30: el cupo tiene que contar 30."""
    assert cuotas.decidir_conciliacion(lotes=1, abiertos=0, auditadas=30, antiguedad_horas=1) \
        == ("confirmar", 30, None)


def test_corrida_en_vuelo_no_se_toca():
    assert cuotas.decidir_conciliacion(lotes=3, abiertos=1, auditadas=20,
                                       antiguedad_horas=2)[0] == "esperar"


def test_corrida_sin_log_espera_un_dia_antes_de_devolver_el_cupo():
    """Un lote puede estar horas haciendo cola en calidad.BatchPendientes antes de
    abrir su fila de log; devolver el cupo antes sería contarlo dos veces."""
    assert cuotas.decidir_conciliacion(lotes=0, abiertos=0, auditadas=0,
                                       antiguedad_horas=3)[0] == "esperar"
    accion, cantidad, _ = cuotas.decidir_conciliacion(lotes=0, abiertos=0, auditadas=0,
                                                      antiguedad_horas=25)
    assert (accion, cantidad) == ("liberar", 0)


def test_corrida_que_no_audito_nada_devuelve_todo_el_cupo():
    accion, cantidad, _ = cuotas.decidir_conciliacion(lotes=2, abiertos=0, auditadas=0,
                                                      antiguedad_horas=1)
    assert (accion, cantidad) == ("liberar", 0)


def test_lote_colgado_se_cierra_con_lo_que_haya():
    accion, cantidad, motivo = cuotas.decidir_conciliacion(lotes=4, abiertos=1, auditadas=45,
                                                           antiguedad_horas=60)
    assert (accion, cantidad) == ("confirmar", 45)
    assert "48" in motivo
