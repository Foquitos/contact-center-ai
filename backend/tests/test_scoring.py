"""
Tests del motor de ponderación dinámica + Errores Críticos. Puros, sin DB ni tokens.

Correr: pytest tests/test_scoring.py -m "not tokens"
"""
from AuditorIA.scoring import (
    calcular_puntaje,
    normalizar_valor_critico,
    OK, NO_OK, EC, NA, TIPO_CRITICAL,
)


def _item(valor, ponderacion, tipo=TIPO_CRITICAL, nombre=None):
    return {"valor": valor, "ponderacion": ponderacion, "tipo": tipo, "nombre": nombre}


# --------------------------------------------------------------------------- #
# Normalización de valores                                                     #
# --------------------------------------------------------------------------- #
def test_normaliza_variantes():
    assert normalizar_valor_critico("ok") == OK
    assert normalizar_valor_critico("OK") == OK
    assert normalizar_valor_critico("no ok") == NO_OK
    assert normalizar_valor_critico("NO_OK") == NO_OK
    assert normalizar_valor_critico("EC") == EC
    assert normalizar_valor_critico("Error Crítico") == EC
    assert normalizar_valor_critico("cualquier cosa") is None
    assert normalizar_valor_critico(None) is None
    assert normalizar_valor_critico(["NO OK", "OK"]) == NO_OK  # primer parseable


# --------------------------------------------------------------------------- #
# Reglas de puntaje                                                            #
# --------------------------------------------------------------------------- #
def test_todos_ok_da_100():
    r = calcular_puntaje([_item(OK, 30), _item(OK, 70)])
    assert r.puntaje == 100.0
    assert r.es_error_critico is False


def test_no_ok_resta_proporcional():
    # 70 OK de 100 -> 70
    r = calcular_puntaje([_item(OK, 70), _item(NO_OK, 30)])
    assert r.puntaje == 70.0
    assert r.es_error_critico is False
    assert r.no_ok_atributos  # registró el NO OK


def test_ec_invalida_todo():
    # Aunque casi todo esté OK, un EC pone el llamado en 0.
    r = calcular_puntaje([_item(OK, 90), _item(EC, 10)])
    assert r.puntaje == 0.0
    assert r.es_error_critico is True
    assert len(r.ec_atributos) == 1


def test_ec_con_pocos_puntos_igual_cero():
    r = calcular_puntaje([_item(OK, 50), _item(OK, 49), _item(EC, 1)])
    assert r.puntaje == 0.0
    assert r.es_error_critico is True


def test_pesos_relativos_se_normalizan():
    # Pesos no suman 100; igual normaliza por la suma (regla de 3).
    # OK=2 de total (2+1+1)=4 -> 50
    r = calcular_puntaje([_item(OK, 2), _item(NO_OK, 1), _item(NO_OK, 1)])
    assert r.puntaje == 50.0
    assert r.ponderacion_total == 4.0
    assert r.ponderacion_ok == 2.0


def test_atributos_no_criticos_se_ignoran():
    # Un atributo informativo (boolean) no participa del puntaje.
    r = calcular_puntaje([
        _item(OK, 100),
        {"valor": True, "ponderacion": 50, "tipo": "boolean", "nombre": "saludo"},
    ])
    assert r.puntaje == 100.0


def test_peso_cero_no_participa():
    r = calcular_puntaje([_item(OK, 100), _item(NO_OK, 0)])
    assert r.puntaje == 100.0  # el NO OK con peso 0 no entra al denominador


def test_plantilla_sin_atributos_criticos_devuelve_none():
    r = calcular_puntaje([
        {"valor": True, "ponderacion": 10, "tipo": "boolean"},
        {"valor": "texto", "ponderacion": 0, "tipo": "string"},
    ])
    assert r.puntaje is None
    assert r.es_error_critico is False


def test_valor_no_parseable_cuenta_como_no_ok():
    # "tal vez" no parsea -> 0 al numerador pero suma al denominador.
    r = calcular_puntaje([_item(OK, 50), _item("tal vez", 50)])
    assert r.puntaje == 50.0
    assert r.es_error_critico is False


def test_sin_tipo_infiere_por_valor():
    # Sin "tipo", se puntúa por el valor OK/NO OK/EC.
    r = calcular_puntaje([
        {"valor": OK, "ponderacion": 1},
        {"valor": NO_OK, "ponderacion": 1},
    ])
    assert r.puntaje == 50.0


# --------------------------------------------------------------------------- #
# N/A: el atributo no aplica y renormaliza el resto                            #
# --------------------------------------------------------------------------- #
def test_na_normaliza_variantes():
    assert normalizar_valor_critico("N/A") == NA
    assert normalizar_valor_critico("na") == NA
    assert normalizar_valor_critico("No Aplica") == NA
    assert normalizar_valor_critico("no aplicable") == NA


def test_na_excluye_del_denominador():
    # 1 OK (peso 50) y 1 N/A (peso 50): el N/A no entra → 100%.
    r = calcular_puntaje([_item(OK, 50), _item(NA, 50, nombre="no_aplica")])
    assert r.puntaje == 100.0
    assert r.es_error_critico is False
    assert r.ponderacion_total == 50.0  # N/A no suma al denominador
    assert r.ponderacion_ok == 50.0
    assert r.na_atributos == ["no_aplica"]


def test_na_renormaliza_con_no_ok():
    # 1 OK (40) + 1 NO_OK (40) + 1 N/A (20) → 40 OK de 80 totales = 50.
    r = calcular_puntaje([_item(OK, 40), _item(NO_OK, 40), _item(NA, 20)])
    assert r.puntaje == 50.0
    assert r.ponderacion_total == 80.0
    assert len(r.na_atributos) == 1


def test_na_no_impide_ec():
    # Aunque haya N/A, un EC sigue mandando todo a 0.
    r = calcular_puntaje([_item(OK, 50), _item(NA, 30), _item(EC, 20)])
    assert r.puntaje == 0.0
    assert r.es_error_critico is True
    assert len(r.na_atributos) == 1
    assert len(r.ec_atributos) == 1


def test_todos_na_devuelve_none():
    # Si TODOS los atributos vinieron N/A, no hay puntaje computable.
    r = calcular_puntaje([_item(NA, 50), _item(NA, 50)])
    assert r.puntaje is None
    assert r.es_error_critico is False
    assert len(r.na_atributos) == 2
