"""Tests de quién entra a la malla: sólo los puestos que atienden el teléfono.

Por qué existe este archivo. La nómina de las sub-campañas de RRHH que alimentan
el pool no es la gente que atiende: adentro hay supervisores, coordinadores,
anfitriones, back office y el puesto "Operador Capacitación". Contarlos tiene dos
efectos, y el segundo es el grande:

1. La malla citada queda inflada. Medido sobre Voltara, 15 personas de más en un
   pico de 151 para los próximos 14 días: exactamente el tamaño de un refuerzo.
2. El **shrinkage** se mide sobre una población que no es la que se planifica.
   13,6% con toda la nómina contra 4,6% con los dos puestos que atienden, y como
   el shrinkage DIVIDE la dotación (a_planificar = en_línea / (1 - shrinkage)),
   eso mueve la campaña entera.

De ahí las dos invariantes que se defienden acá: la malla y el shrinkage filtran
por la MISMA población, y un puesto que nadie clasificó NO entra.

Offline: no toca ni BD ni red.

Correr: pytest tests/test_planificador_malla.py -m "not tokens"
"""
from datetime import date, datetime, timedelta

import pytest

from app import planificador_datos as pd


# ------------------------------------------------------------------ andamiaje

class _Resultado:
    def __init__(self, filas, rowcount=1):
        self._filas = list(filas)
        self.rowcount = rowcount

    def mappings(self):
        return self

    def all(self):
        return self._filas

    def fetchall(self):
        return self._filas

    def fetchone(self):
        return self._filas[0] if self._filas else None

    def scalar(self):
        return self._filas[0] if self._filas else None


class _Conn:
    """Conexión de mentira que guarda el SQL que le mandaron."""
    def __init__(self, filas=()):
        self.filas = list(filas)
        self.sql = []
        self.params = None

    def execute(self, stmt, params=None):
        self.sql.append(str(stmt))
        self.params = params
        return _Resultado(self.filas)


@pytest.fixture
def con_tabla(monkeypatch):
    """Como si la migración 2026-09-09e ya estuviera aplicada."""
    monkeypatch.setattr(pd, "_tiene_tabla", lambda c, t: True)
    monkeypatch.setattr(pd, "campanas_rrhh_del_pool", lambda c, p: [100, 106])


@pytest.fixture
def sin_tabla(monkeypatch):
    """Como si todavía no se hubiera corrido."""
    monkeypatch.setattr(
        pd, "_tiene_tabla",
        lambda c, t: t != "planificacion.PuestoMalla")
    monkeypatch.setattr(pd, "campanas_rrhh_del_pool", lambda c, p: [100, 106])


# ------------------------------------------------------- el filtro y su guarda

def test_sin_la_migracion_se_cuenta_a_todos(sin_tabla):
    """La alternativa —no contar a nadie— dejaría la malla en cero y una brecha
    inventada en todos los intervalos. Se degrada al comportamiento de siempre y
    la pantalla avisa que falta la migración."""
    assert pd._filtro_de_puesto(_Conn()) == ""


def test_con_la_migracion_el_filtro_pide_el_puesto(con_tabla):
    filtro = pd._filtro_de_puesto(_Conn())
    assert "PuestoMalla" in filtro and "EnMalla = 1" in filtro


def test_un_puesto_sin_clasificar_queda_afuera():
    """EXISTS y no NOT EXISTS: `dbo.puestos` crece, y un puesto nuevo tiene que
    quedar afuera hasta que alguien lo marque. Con la condición al revés entraría
    solo, sin que nadie lo decida."""
    assert "EXISTS (SELECT 1" in pd._FILTRO_PUESTO
    assert "NOT EXISTS" not in pd._FILTRO_PUESTO


# --------------------------------------------------------------------- malla

def test_la_malla_sale_de_una_sola_tabla(con_tabla):
    """`payroll_futuro` tiene 1,2 millones de filas desde 2023-11-23, o sea la
    malla entera —pasada y futura—. Leer también `dbo.payroll` y unirlas contaba
    cada turno pasado DOS VECES: 245 citados en el pico del 8/9 cuando el máximo
    posible eran 124 y los realmente conectados 142."""
    conn = _Conn([(7, datetime(2026, 9, 9, 8, 0), datetime(2026, 9, 9, 9, 0))])

    pd.dotacion_planificada(conn, 1, date(2026, 9, 1), date(2026, 9, 20))

    sql = conn.sql[-1]
    assert "dbo.payroll_futuro" in sql
    assert "FROM dbo.payroll\n" not in sql
    assert "UNION" not in sql


def test_la_malla_cuenta_al_citado_que_falto(con_tabla):
    """La malla es lo que se PROMETIÓ, y contra eso se mide la brecha de
    planificación; quién estuvo de verdad se ve al lado, en los conectados. Es
    además la definición del tablero de la operación, verificada intervalo por
    intervalo: 964 contra 964 el 2026-09-09."""
    conn = _Conn([])

    pd.dotacion_planificada(conn, 1, date(2026, 9, 1), date(2026, 9, 20))

    assert "IN ('piso', 'ausente')" in conn.sql[-1]


def test_las_licencias_no_entran_a_la_malla(con_tabla):
    """Esa gente no estaba citada, así que no es que faltó. Se sacan por las
    horas y no tocando la clasificación."""
    conn = _Conn([])

    pd.dotacion_planificada(conn, 1, date(2026, 9, 1), date(2026, 9, 20))

    assert "f.horas_programadas > 0" in conn.sql[-1]


def test_con_la_migracion_la_malla_filtra_por_puesto(con_tabla):
    conn = _Conn([])

    pd.dotacion_planificada(conn, 1, date(2026, 9, 9), date(2026, 9, 10))

    assert conn.sql[-1].count("PuestoMalla") == 1


def test_sin_la_migracion_la_malla_no_menciona_la_tabla(sin_tabla):
    conn = _Conn([(7, datetime(2026, 9, 9, 8, 0), datetime(2026, 9, 9, 9, 0))])

    pd.dotacion_planificada(conn, 1, date(2026, 9, 9), date(2026, 9, 10))

    assert "PuestoMalla" not in conn.sql[-1]


def test_la_malla_sigue_contando_los_intervalos_igual(con_tabla):
    """El filtro cambia a quién se cuenta, no cómo. Un turno de 8 a 9 son dos
    medias horas, y la de las 9 ya no."""
    conn = _Conn([(7, datetime(2026, 9, 9, 8, 0), datetime(2026, 9, 9, 9, 0))])

    conteo = pd.dotacion_planificada(conn, 1, date(2026, 9, 9), date(2026, 9, 10))

    assert conteo == {datetime(2026, 9, 9, 8, 0): 1,
                      datetime(2026, 9, 9, 8, 30): 1}


# ------------------------------------------ la malla NO se cuenta dos veces

def test_un_turno_cargado_varias_veces_cuenta_una_persona(con_tabla):
    """El operador 107085 tiene el 14/06/2026 el mismo turno de 18 a 6 cargado 16
    veces en el registro y 25 en la malla. Contado por filas eran 16 y 25 personas
    en cada media hora de esa noche."""
    turno = (107085, datetime(2026, 6, 14, 18, 0), datetime(2026, 6, 15, 6, 0))
    conn = _Conn([turno] * 25)

    conteo = pd.dotacion_planificada(conn, 1, date(2026, 6, 14), date(2026, 6, 15))

    assert set(conteo.values()) == {1}
    assert len(conteo) == 24                          # 12 h en medias horas


def test_la_hora_extra_en_fila_aparte_que_se_pisa_cuenta_una_vez(con_tabla):
    """En el registro la extra viene a veces como otra fila del mismo operador y
    día. Si se pisa con el turno, en la media hora compartida es una sola persona;
    donde no se pisa, suma las medias horas de más."""
    conn = _Conn([
        (109606, datetime(2026, 9, 10, 10, 0), datetime(2026, 9, 10, 16, 0)),
        (109606, datetime(2026, 9, 10, 9, 0), datetime(2026, 9, 10, 17, 0)),
    ])

    conteo = pd.dotacion_real(conn, 1, date(2026, 9, 10), date(2026, 9, 11))

    assert set(conteo.values()) == {1}
    assert min(conteo) == datetime(2026, 9, 10, 9, 0)
    assert max(conteo) == datetime(2026, 9, 10, 16, 30)


def test_dos_operadores_en_el_mismo_intervalo_son_dos(con_tabla):
    """Contar personas no puede colapsar a gente distinta."""
    conn = _Conn([
        (1, datetime(2026, 9, 10, 10, 0), datetime(2026, 9, 10, 11, 0)),
        (2, datetime(2026, 9, 10, 10, 0), datetime(2026, 9, 10, 11, 0)),
    ])

    conteo = pd.dotacion_planificada(conn, 1, date(2026, 9, 10), date(2026, 9, 11))

    assert conteo[datetime(2026, 9, 10, 10, 0)] == 2


def test_las_dos_fuentes_traen_el_operador():
    """Sin el operador en la consulta no hay forma de contar personas: se volvería
    a contar filas sin que ningún test de conteo lo note."""
    assert "id_operadores AS operador" in pd._PAYROLL_TURNOS
    assert "id_operadores AS operador" in pd._PAYROLL_TURNOS_REAL

# ----------------------------------------------------------------- shrinkage

def _horas(*filas, dia=date(2026, 6, 2)):
    """Filas de payroll ya agrupadas. Cada una es (codigo, clase, programadas) o
    (codigo, clase, programadas, efectivas) cuando el turno se cumplió a medias."""
    salida = []
    for f in filas:
        codigo, clase, hp = f[0], f[1], f[2]
        trabajadas = f[3] if len(f) > 3 else hp
        salida.append({"fecha": dia, "codigo": codigo, "clase": clase, "filas": 1,
                       "horas_programadas": hp, "horas_trabajadas": trabajadas,
                       # La base ya devuelve el mínimo fila por fila; el fake
                       # replica ese contrato en vez de inventar otro.
                       "horas_efectivas": min(trabajadas, hp)})
    return salida


def test_el_shrinkage_filtra_por_el_mismo_puesto_que_la_malla(con_tabla):
    """Las dos salen de las mismas horas de payroll. Medir el ausentismo sobre
    toda la nómina y contar la malla sólo con los que atienden serían dos
    poblaciones distintas dentro de la misma cuenta de dotación."""
    conn = _Conn(_horas(("P", "piso", 900.0), ("ABS", "ausente", 100.0)))

    pd.estimar_shrinkage_payroll(conn, [1], date(2026, 6, 1), date(2026, 9, 1))

    assert "PuestoMalla" in conn.sql[-1]


def test_el_shrinkage_dice_sobre_que_poblacion_se_midio(con_tabla):
    """Un 4,6% y un 13,6% son los dos correctos y miden cosas distintas. Sin este
    dato al lado el número de la pantalla no se puede interpretar."""
    conn = _Conn(_horas(("P", "piso", 900.0), ("ABS", "ausente", 100.0)))

    r = pd.estimar_shrinkage_payroll(conn, [1], date(2026, 6, 1), date(2026, 9, 1))

    assert r["solo_puestos_de_malla"] is True
    assert r["shrinkage"] == 0.1


def test_sin_la_migracion_el_shrinkage_avisa_que_midio_todo(sin_tabla):
    conn = _Conn(_horas(("P", "piso", 900.0), ("ABS", "ausente", 100.0)))

    r = pd.estimar_shrinkage_payroll(conn, [1], date(2026, 6, 1), date(2026, 9, 1))

    assert r["solo_puestos_de_malla"] is False


def test_el_dia_en_curso_no_entra_al_ausentismo(con_tabla, monkeypatch):
    """Un día abierto dice que faltó todo el mundo. Medido sobre el mismo día:
    leído en curso da piso 156 h contra 724 h de ausente (82%); leído al día
    siguiente, 986 h contra 34 h (3,3%). El recorte va en la función y no en el
    llamador porque el error no se ve: el número que sale es plausible, sólo que
    multiplicado por veinte.
    """
    conn = _Conn(_horas(("P", "piso", 900.0), ("ABS", "ausente", 100.0)))

    r = pd.estimar_shrinkage_payroll(conn, [1], date(2026, 6, 1),
                                     date.today() + timedelta(days=5))

    assert r["hasta_medido"] == date.today()
    assert conn.params["hasta"] == date.today()


def test_sin_ningun_dia_cerrado_no_se_inventa_un_numero(con_tabla):
    conn = _Conn(_horas(("P", "piso", 900.0)))

    r = pd.estimar_shrinkage_payroll(conn, [1], date.today(),
                                     date.today() + timedelta(days=1))

    assert r["shrinkage"] is None
    assert "cerrado" in r["motivo"]


def test_el_faltante_dentro_del_turno_tambien_es_shrinkage(con_tabla):
    """Un turno con código de piso puede haberse cumplido a medias —llegó tarde,
    se fue antes— y el código sigue diciendo "P" con las horas enteras. Medido
    sobre el pool telefónico son 1,4% en un hábil y 4,4% en un domingo: es
    exactamente donde vive la diferencia entre tipos de día que la medición por
    códigos no encuentra."""
    conn = _Conn(_horas(("P", "piso", 900.0, 800.0), ("ABS", "ausente", 100.0)))

    r = pd.estimar_shrinkage_payroll(conn, [1], date(2026, 6, 1), date(2026, 9, 1))

    assert r["ausentismo"] == 0.1
    assert r["dentro_del_turno"] == 0.1
    assert r["shrinkage"] == 0.2


def test_los_componentes_del_shrinkage_suman_el_total(con_tabla):
    conn = _Conn(_horas(("P", "piso", 700.0, 650.0), ("ABS", "ausente", 200.0),
                        ("CAPA", "capacitacion", 100.0), ("XX", "otro", 50.0)))

    r = pd.estimar_shrinkage_payroll(conn, [1], date(2026, 6, 1), date(2026, 9, 1))

    # El que se APLICA: sólo las dos causas que viven adentro del universo de la
    # malla, y sobre ese universo (700 + 200 = 900 h).
    assert round(r["ausentismo"] + r["dentro_del_turno"], 3) == r["shrinkage"]
    assert r["horas_universo_malla"] == 900.0
    assert r["shrinkage"] == round(250.0 / 900.0, 3)
    # El de NÓMINA: los cuatro componentes sobre las 1.050 h programadas.
    assert round(r["capacitacion"] + r["sin_clasificar"]
                 + round(200.0 / 1050.0, 3) + round(50.0 / 1050.0, 3), 3) \
        == r["shrinkage_nomina"]
    assert r["horas_programadas"] == 1050.0


def test_la_capacitacion_no_se_le_descuenta_a_la_malla(con_tabla):
    """La malla —`dotacion_planificada`— cuenta filas de clase 'piso' y 'ausente'
    y NO al que está en capacitación. Descontarle a esa población un shrinkage que
    incluye la capacitación la cuenta dos veces, y en Voltara eran 8,6 puntos de
    dotación pedida de más en todos los intervalos."""
    sin_capa = _Conn(_horas(("P", "piso", 900.0, 900.0), ("ABS", "ausente", 100.0)))
    con_capa = _Conn(_horas(("P", "piso", 900.0, 900.0), ("ABS", "ausente", 100.0),
                            ("CAPA", "capacitacion", 500.0)))

    a = pd.estimar_shrinkage_payroll(sin_capa, [1], date(2026, 6, 1), date(2026, 9, 1))
    b = pd.estimar_shrinkage_payroll(con_capa, [1], date(2026, 6, 1), date(2026, 9, 1))

    # Quinientas horas de capacitación no mueven el número que se aplica...
    assert a["shrinkage"] == b["shrinkage"] == 0.1
    # ...y sí el de nómina, que es donde corresponde verlas.
    assert b["shrinkage_nomina"] > a["shrinkage_nomina"]
    assert b["capacitacion"] == round(500.0 / 1500.0, 3)


def test_una_hora_extra_no_tapa_la_hora_que_falto_otro(con_tabla):
    """`horas_trabajadas` incluye extras. El tope va FILA POR FILA en el SQL: si
    se sumara primero y se topeara después, el que se quedó de más compensaría al
    que no vino y el faltante desaparecería."""
    assert "WHEN ISNULL(p.horas_trabajadas, 0) < ISNULL(p.horas_programadas, 0)" \
        in pd._PAYROLL_HORAS
    assert "AS horas_efectivas" in pd._PAYROLL_HORAS

    conn = _Conn(_horas(("P", "piso", 100.0, 130.0)))
    r = pd.estimar_shrinkage_payroll(conn, [1], date(2026, 6, 1), date(2026, 9, 1))

    assert r["dentro_del_turno"] == 0.0
    assert r["horas_efectivas"] == 100.0


# ------------------------------------------------------- por tipo de día

def test_el_desglose_por_tipo_de_dia_trae_la_dispersion(con_tabla):
    """Va el desvío y no sólo el promedio porque es lo único que deja decidir si
    conviene un número por tipo de día. Medido: hábil 8,6% y domingo 8,7%, con
    desvíos de 4,9 y 8,0 puntos — la diferencia es una décima contra un error
    estándar de punto y medio."""
    filas = []
    for d, ef in ((date(2026, 6, 1), 90.0), (date(2026, 6, 2), 80.0),
                  (date(2026, 6, 6), 50.0), (date(2026, 6, 7), 70.0)):
        filas += _horas(("P", "piso", 100.0, ef), dia=d)
    conn = _Conn(filas)

    r = pd.estimar_shrinkage_payroll(conn, [1], date(2026, 6, 1), date(2026, 9, 1))
    por_tipo = {t["tipo"]: t for t in r["por_tipo_de_dia"]}

    assert por_tipo["habil"]["dias"] == 2
    assert por_tipo["habil"]["shrinkage"] == 0.15      # (0,10 + 0,20) / 2
    assert por_tipo["sabado"]["shrinkage"] == 0.5
    assert por_tipo["domingo"]["shrinkage"] == 0.3
    assert por_tipo["habil"]["error_estandar"] is not None


def test_el_feriado_se_separa_del_dia_de_semana(con_tabla):
    """El feriado SÍ es distinto y de forma grande: 2,1% contra 8,6%. Tiene
    explicación estructural —el que no trabaja un feriado se carga con licencia y
    CERO horas programadas, así que la malla del feriado es de voluntarios— y por
    eso no se puede promediar con un martes."""
    filas = (_horas(("P", "piso", 100.0, 80.0), dia=date(2026, 6, 1))
             + _horas(("P", "piso", 100.0, 99.0), dia=date(2026, 6, 3)))
    conn = _Conn(filas)

    r = pd.estimar_shrinkage_payroll(conn, [1], date(2026, 6, 1), date(2026, 9, 1),
                                     feriados=[date(2026, 6, 3)])
    por_tipo = {t["tipo"]: t for t in r["por_tipo_de_dia"]}

    assert por_tipo["feriado"]["dias"] == 1
    assert por_tipo["feriado"]["shrinkage"] == 0.01
    assert por_tipo["habil"]["shrinkage"] == 0.2


def test_la_capacitacion_se_informa_por_tipo_de_dia(con_tabla):
    """No se dicta capacitación en sábado, domingo ni feriado. Que dé cero no es
    casualidad del promedio: es cómo funciona la operación. Se informa el
    componente por tipo para que se vea, y para que una fila mal cargada un
    domingo salte en vez de diluirse adentro del total."""
    filas = (_horas(("P", "piso", 90.0), ("CAPA", "capacitacion", 10.0),
                    dia=date(2026, 6, 1))                      # lunes
             + _horas(("P", "piso", 100.0), dia=date(2026, 6, 7)))   # domingo
    conn = _Conn(filas)

    r = pd.estimar_shrinkage_payroll(conn, [1], date(2026, 6, 1), date(2026, 9, 1))
    por_tipo = {t["tipo"]: t for t in r["por_tipo_de_dia"]}

    assert por_tipo["habil"]["capacitacion"] == 0.1
    assert por_tipo["domingo"]["capacitacion"] == 0.0


def test_una_capacitacion_cargada_un_domingo_queda_a_la_vista(con_tabla):
    """Si aparece, es una fila mal cargada y no un matiz del promedio."""
    filas = _horas(("P", "piso", 80.0), ("CAPA", "capacitacion", 20.0),
                   dia=date(2026, 6, 7))
    conn = _Conn(filas)

    r = pd.estimar_shrinkage_payroll(conn, [1], date(2026, 6, 1), date(2026, 9, 1))
    por_tipo = {t["tipo"]: t for t in r["por_tipo_de_dia"]}

    assert por_tipo["domingo"]["capacitacion"] == 0.2


def test_los_componentes_de_cada_tipo_suman_su_total(con_tabla):
    filas = _horas(("P", "piso", 70.0, 65.0), ("ABS", "ausente", 20.0),
                   ("CAPA", "capacitacion", 10.0), dia=date(2026, 6, 1))
    conn = _Conn(filas)

    r = pd.estimar_shrinkage_payroll(conn, [1], date(2026, 6, 1), date(2026, 9, 1))
    t = r["por_tipo_de_dia"][0]

    # Igual que el general: el que se aplica son las dos causas de adentro del
    # universo de la malla, y la capacitación queda en el de nómina.
    assert round(t["ausentismo"] + t["dentro_del_turno"], 3) == t["shrinkage"]
    assert t["horas_universo_malla"] == 90.0
    assert t["shrinkage_nomina"] > t["shrinkage"]


def test_sin_feriados_no_se_inventa_la_categoria(con_tabla):
    conn = _Conn(_horas(("P", "piso", 100.0, 90.0), dia=date(2026, 6, 1)))

    r = pd.estimar_shrinkage_payroll(conn, [1], date(2026, 6, 1), date(2026, 9, 1))

    assert [t["tipo"] for t in r["por_tipo_de_dia"]] == ["habil"]


def test_las_licencias_siguen_sin_contar(con_tabla):
    """Esa gente no estaba programada: meterla al denominador mezclaría "no vino
    el que tenía que venir" con "no estaba previsto que viniera"."""
    conn = _Conn(_horas(("P", "piso", 900.0), ("ABS", "ausente", 100.0),
                        ("VAC", "licencia", 500.0)))

    r = pd.estimar_shrinkage_payroll(conn, [1], date(2026, 6, 1), date(2026, 9, 1))

    assert r["horas_programadas"] == 1000.0
    assert r["shrinkage"] == 0.1


# ------------------------------------------------------- catálogo de puestos

_PUESTOS = [
    {"puesto_id": 3, "nombre": "Operador Telefónico", "en_malla": 1,
     "personas": 330, "horas": 52004.0},
    {"puesto_id": 9, "nombre": "Supervisor", "en_malla": 0,
     "personas": 10, "horas": 1449.0},
    {"puesto_id": 24, "nombre": "Operador Postcurso", "en_malla": None,
     "personas": 2, "horas": 80.0},
]


def test_el_catalogo_ordena_por_peso_y_separa_lo_que_entra(con_tabla):
    r = pd.puestos_de_la_malla(_Conn(_PUESTOS), [1],
                               date(2026, 6, 1), date(2026, 9, 1))

    assert [p["nombre"] for p in r["puestos"]][0] == "Operador Telefónico"
    assert r["horas_en_malla"] == 52004.0
    assert r["horas_fuera"] == 1529.0          # supervisor + el sin clasificar


def test_el_catalogo_denuncia_los_puestos_sin_clasificar(con_tabla):
    """Un puesto con horas y sin decisión tomada es lo único que hay que ir a
    mirar: por ahora no cuenta, y si atiende hay que marcarlo."""
    r = pd.puestos_de_la_malla(_Conn(_PUESTOS), [1],
                               date(2026, 6, 1), date(2026, 9, 1))

    assert r["sin_clasificar"] == ["Operador Postcurso"]


def test_sin_la_migracion_el_catalogo_se_muestra_igual(sin_tabla):
    """Hay que poder ver a quién se está contando de más ANTES de aplicar la
    migración: es la información con la que alguien decide aplicarla."""
    filas = [dict(p, en_malla=None) for p in _PUESTOS]

    r = pd.puestos_de_la_malla(_Conn(filas), [1],
                               date(2026, 6, 1), date(2026, 9, 1))

    assert r["disponible"] is False
    assert "2026-09-09e" in r["motivo"]
    assert len(r["puestos"]) == 3
    assert all(p["en_malla"] is None for p in r["puestos"])


def test_el_catalogo_no_pide_la_columna_que_todavia_no_existe(sin_tabla):
    conn = _Conn([])
    pd.puestos_de_la_malla(conn, [1], date(2026, 6, 1), date(2026, 9, 1))
    assert "PuestoMalla" not in conn.sql[-1]


# ------------------------------------------------------------------- edición

def test_no_se_puede_clasificar_sin_la_migracion(sin_tabla):
    with pytest.raises(pd.MigracionPendiente):
        pd.guardar_puesto_malla(_Conn(), 24, True)


def test_clasificar_un_puesto_manda_el_bit(con_tabla):
    conn = _Conn()

    pd.guardar_puesto_malla(conn, 24, True, "Atiende desde septiembre")

    assert conn.params == {"id": 24, "en": 1,
                           "nota": "Atiende desde septiembre"}
    assert "MERGE" in conn.sql[-1]


def test_desmarcar_un_puesto_manda_cero(con_tabla):
    conn = _Conn()
    pd.guardar_puesto_malla(conn, 9, False)
    assert conn.params["en"] == 0


# =========================================================================
# APLICAR LO MEDIDO
# =========================================================================
# Antes el botón respetaba el candado de "puesto a mano" y no pisaba nada. Era
# peor: un valor tocado una sola vez dejaba el botón sin efecto PARA SIEMPRE y en
# silencio. Alguien apretaba "dejar vigentes los valores medidos", veía el número
# medido en pantalla, y el que se usaba para dimensionar seguía siendo otro. Un
# botón que a veces no hace nada y no lo dice es peor que no tenerlo.

class _ConnOrigen(_Conn):
    """Devuelve el origen vigente en el SELECT y registra los UPDATE."""
    def __init__(self, pac="", sh=""):
        super().__init__([{"pac": pac, "sh": sh}])
        self.updates = []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "UPDATE" in sql:
            self.updates.append((sql, params))
            return _Resultado([])
        return super().execute(stmt, params)


def test_aplicar_pisa_el_shrinkage_puesto_a_mano():
    conn = _ConnOrigen(sh="manual")

    pisados = pd.aplicar_calibracion(conn, 20, shrinkage={"shrinkage": 0.1})

    assert pisados == ["shrinkage"]
    assert any("ShrinkageDefault" in sql for sql, _ in conn.updates)


def test_el_update_ya_no_lleva_el_candado_de_manual():
    """El candado en el WHERE era lo que hacía que el botón fallara callado."""
    conn = _ConnOrigen(sh="manual")

    pd.aplicar_calibracion(conn, 20, shrinkage={"shrinkage": 0.1})

    assert all("manual" not in sql for sql, _ in conn.updates)


def test_no_avisa_cuando_no_habia_nada_puesto_a_mano():
    conn = _ConnOrigen(sh="payroll")

    pisados = pd.aplicar_calibracion(conn, 20, shrinkage={"shrinkage": 0.1})

    assert pisados == []


def test_la_paciencia_y_el_shrinkage_se_avisan_por_separado():
    """Cada uno mira SU propio origen: una paciencia fijada a mano no tiene nada
    que ver con el shrinkage medido."""
    conn = _ConnOrigen(pac="manual", sh="payroll")

    pisados = pd.aplicar_calibracion(conn, 20, paciencia_seg=900,
                                     shrinkage={"shrinkage": 0.1})

    assert pisados == ["paciencia"]


def test_avisa_los_dos_cuando_los_dos_estaban_a_mano():
    """Con varios parámetros tocados a mano el botón deja de servir como atajo, y
    eso es exactamente lo que la pantalla tiene que decir."""
    conn = _ConnOrigen(pac="manual", sh="manual")

    pisados = pd.aplicar_calibracion(conn, 20, paciencia_seg=900,
                                     shrinkage={"shrinkage": 0.1})

    assert pisados == ["paciencia", "shrinkage"]


def test_aplicar_deja_tambien_el_shrinkage_de_feriado(con_tabla):
    """El botón dejaba el general nuevo al lado de un feriado viejo. Son la misma
    medición partida: o van los dos o no va ninguno."""
    conn = _ConnOrigen()

    pd.aplicar_calibracion(conn, 20, shrinkage={
        "shrinkage": 0.1, "ausentismo": 0.06, "capacitacion": 0.02,
        "no_habil": 0.095, "feriado": 0.053})

    sql, params = conn.updates[-1]
    assert "ShrinkageFeriado" in sql and "ShrinkageNoHabil" in sql
    assert params["shf"] == 0.053 and params["shnh"] == 0.095


def test_sin_la_migracion_no_se_escriben_las_columnas_nuevas(sin_tabla, monkeypatch):
    """`_tiene_columna` decide, igual que en el resto del módulo: una base con la
    migración vieja tiene que seguir aplicando el general."""
    monkeypatch.setattr(pd, "_tiene_columna", lambda c, t, col: False)
    conn = _ConnOrigen()

    pd.aplicar_calibracion(conn, 20, shrinkage={"shrinkage": 0.1, "feriado": 0.05})

    sql, _ = conn.updates[-1]
    assert "ShrinkageFeriado" not in sql


def test_aplicar_registra_de_que_medicion_sale(con_tabla):
    """La presencia en la línea y los códigos de RRHH son dos mediciones distintas
    del mismo parámetro: la pantalla tiene que poder decir cuál quedó vigente."""
    conn = _ConnOrigen()

    pd.aplicar_calibracion(conn, 20, shrinkage={"shrinkage": 0.064, "no_habil": 0.139,
                                                "origen": "presencia"})

    sql, params = conn.updates[-1]
    assert "ShrinkageOrigen = :sho" in sql and params["sho"] == "presencia"


def test_sin_origen_o_con_uno_desconocido_queda_payroll(con_tabla):
    for shrinkage in ({"shrinkage": 0.1}, {"shrinkage": 0.1, "origen": "inventado"}):
        conn = _ConnOrigen()
        pd.aplicar_calibracion(conn, 20, shrinkage=shrinkage)
        assert conn.updates[-1][1]["sho"] == "payroll"


def test_la_paciencia_se_guarda_por_skill(con_tabla):
    """No es una sola por campaña: el que se quedó sin luz espera mucho más que
    un electrodependiente, y son colas distintas."""
    conn = _ConnOrigen()

    pd.guardar_paciencia_por_skill(conn, 20, {6: 1126, 5: 1267})

    assert len(conn.updates) == 2
    assert {u[1]["p"] for u in conn.updates} == {1126, 1267}


def test_una_paciencia_en_none_limpia_la_del_skill(con_tabla):
    """Vuelve a caer en la de la campaña. Sin esto, un skill que dejó de tener
    muestra suficiente se quedaría con una paciencia vieja para siempre."""
    conn = _ConnOrigen()

    pd.guardar_paciencia_por_skill(conn, 20, {6: None})

    assert conn.updates[-1][1]["p"] is None


# =========================================================================
# EL BREAK Y LA DISPONIBILIDAD NO SE DESCUENTAN DOS VECES
# =========================================================================

def test_sin_break_declarado_la_disponibilidad_no_cambia():
    import app.planificador as pl
    cfg = pl.CampanaCfg(campana_id=20)
    assert cfg.factor_break() == 0.0


def test_el_factor_neto_por_el_break_devuelve_el_medido():
    """Es neutro por construcción: cargar el neto y prender el break tiene que
    dar la MISMA dotación que hoy, sólo que con el 8,33% del break a la vista en
    vez de escondido adentro de un 0,82 salido de invertir una fórmula."""
    import app.planificador as pl
    cfg = pl.CampanaCfg(campana_id=20, break_min_por_hora=5)
    medido = 0.82

    neto = medido / (1 - cfg.factor_break())

    assert neto * (1 - cfg.factor_break()) == pytest.approx(medido, abs=1e-9)
    assert neto > medido        # el neto es MAYOR: el break salió afuera


def test_la_paciencia_medida_se_cruza_con_UPPER_y_no_literal():
    """El informe de IVR dice `Emergencias` y la config dice `EMERGENCIAS`.
    Cruzando los nombres tal cual, el ÚNICO que coincidía era TOC —que está en
    mayúsculas de los dos lados— así que la paciencia medida terminaba aplicada a
    ese skill y a ninguno más. Verificado en la base: 12 de 13 skills en NULL y
    TOC con 5520s.
    """
    from app import planificador as pl
    from app import planificador_servicio as servicio

    cfg = pl.CampanaCfg(campana_id=20, skills=[
        pl.SkillCfg(6, "EMERGENCIAS", 1, 0.80, 20),
        pl.SkillCfg(2, "TOC", 1, 0.80, 20),
        pl.SkillCfg(9, "RECLAMO-DANO", 1, 0.80, 20),
    ])
    medido = {"Emergencias": {"paciencia_seg": 1126, "abandonos": 6183},
              "TOC": {"paciencia_seg": 5520, "abandonos": 8},
              "Reclamo-Dano": {"paciencia_seg": 800, "abandonos": 383},
              "Cola-Que-No-Existe": {"paciencia_seg": 400, "abandonos": 900}}

    servicio._resolver_skills(medido, cfg)

    assert medido["Emergencias"]["skill_id"] == 6
    assert medido["TOC"]["skill_id"] == 2
    assert medido["Reclamo-Dano"]["skill_id"] == 9
    # El que no resuelve queda en None y la pantalla lo muestra sin poder
    # aplicarlo, que es mejor que aplicárselo al que no era.
    assert medido["Cola-Que-No-Existe"]["skill_id"] is None


def test_una_paciencia_de_ocho_abandonos_no_se_aplica():
    """La paciencia sale de los que ABANDONARON. TOC tuvo 5.138 llamadas y ocho
    abandonos, y de esos ocho salían 6.970 segundos —casi dos horas—. No es que
    esperen dos horas: es que casi nadie abandona y el estimador se queda sin
    denominador.

    Y el error va en la dirección peligrosa: una paciencia enorme hace que
    Erlang A diga "van a esperar, no hace falta más gente", o sea que el ruido
    REBAJA la dotación."""
    from app import planificador as pl
    from app import planificador_servicio as servicio

    cfg = pl.CampanaCfg(campana_id=20, skills=[
        pl.SkillCfg(2, "TOC", 1, 0.80, 20),
        pl.SkillCfg(6, "EMERGENCIAS", 1, 0.80, 20),
    ])
    medido = {"TOC": {"paciencia_seg": 6970, "abandonos": 8},
              "Emergencias": {"paciencia_seg": 847, "abandonos": 6183}}

    servicio._resolver_skills(medido, cfg)

    assert medido["TOC"]["aplicable"] is False
    assert medido["TOC"]["skill_id"] == 2       # resuelve, pero no se aplica
    assert medido["Emergencias"]["aplicable"] is True


def test_sin_skill_equivalente_tampoco_es_aplicable():
    from app import planificador as pl
    from app import planificador_servicio as servicio

    medido = {"Cola-Vieja": {"paciencia_seg": 900, "abandonos": 5000}}
    servicio._resolver_skills(medido, pl.CampanaCfg(campana_id=20))

    assert medido["Cola-Vieja"]["aplicable"] is False


# =========================================================================
# LA DISPONIBILIDAD MEDIDA SE APLICA, Y SE APLICA NETA DE BREAK
# =========================================================================
# Medir mostraba 0,82 y aplicar dejaba las franjas como estaban: la
# disponibilidad no se escribía nunca.

def _medicion(por_hora):
    return {"factor": 0.82, "factor_aplicable": 0.894,
            "por_hora": [{"hora": h, "muestras": 100,
                          "factor_medido": round(f * (1 - 5 / 60), 3),
                          "factor_aplicable": f} for h, f in por_hora.items()]}


def _cfg_con_franjas(*franjas):
    import app.planificador as pl
    return pl.CampanaCfg(campana_id=20, disponibilidad=[
        pl.FranjaDisponibilidad(dia_semana=0, hora_desde=a, hora_hasta=b, factor=f)
        for a, b, f in franjas])


def test_se_guarda_el_factor_neto_de_break():
    """El break se descuenta aparte: así, el día que cambien los 5 minutos por
    hora, se toca esa sola perilla y la disponibilidad guardada sigue valiendo."""
    from app import planificador_servicio as servicio
    cfg = _cfg_con_franjas((0, 24, 0.90))

    franjas = servicio._franjas_desde_medicion(cfg, _medicion({h: 0.88 for h in range(24)}))

    assert franjas == [{"dia_semana": 0, "hora_desde": 0, "hora_hasta": 24,
                        "factor": 0.88}]


def test_la_hora_sin_muestra_conserva_lo_que_tenia():
    """De madrugada hay nueve intervalos útiles en 90 días: eso no es una
    medición, es una anécdota. Pisar con eso un valor puesto por una persona
    sería peor que no medir."""
    from app import planificador_servicio as servicio
    cfg = _cfg_con_franjas((0, 8, 0.90), (8, 24, 0.82))

    franjas = servicio._franjas_desde_medicion(
        cfg, _medicion({h: 0.85 for h in range(8, 24)}))

    assert franjas[0] == {"dia_semana": 0, "hora_desde": 0, "hora_hasta": 8,
                          "factor": 0.9}
    assert franjas[1] == {"dia_semana": 0, "hora_desde": 8, "hora_hasta": 24,
                          "factor": 0.85}


def test_las_horas_iguales_se_juntan_en_una_franja():
    """24 filas de una hora son ilegibles y dicen lo mismo que cuatro."""
    from app import planificador_servicio as servicio
    cfg = _cfg_con_franjas((0, 24, 0.90))
    medido = {h: (0.80 if 8 <= h < 18 else 0.95) for h in range(24)}

    franjas = servicio._franjas_desde_medicion(cfg, _medicion(medido))

    assert [(f["hora_desde"], f["hora_hasta"], f["factor"]) for f in franjas] == [
        (0, 8, 0.95), (8, 18, 0.8), (18, 24, 0.95)]


def test_las_franjas_cubren_el_dia_entero_y_no_se_solapan():
    """`guardar_disponibilidad` rechaza solapamientos, así que si esto se rompe
    el guardado falla entero."""
    from app import planificador_servicio as servicio
    cfg = _cfg_con_franjas((0, 24, 0.90))

    franjas = servicio._franjas_desde_medicion(
        cfg, _medicion({h: 0.5 + h / 100 for h in range(24)}))

    assert franjas[0]["hora_desde"] == 0 and franjas[-1]["hora_hasta"] == 24
    for a, b in zip(franjas, franjas[1:]):
        assert a["hora_hasta"] == b["hora_desde"]


def test_sin_medicion_por_hora_no_se_toca_nada():
    from app import planificador_servicio as servicio
    cfg = _cfg_con_franjas((0, 24, 0.90))

    assert servicio._franjas_desde_medicion(cfg, {"por_hora": []}) == []


def test_la_paciencia_se_mide_con_una_ventana_mas_larga_que_el_resto():
    """La paciencia sale SÓLO de los que abandonaron. En 90 días
    Electrodependientes tuvo 14 abandonos, TOC 8 y Emergencias-Empresarial 3; en
    365 son 395, 223 y 91. Y la ventana larga además va para el lado conservador:
    TOC pasa de 6.970s a 1.068s y Electrodependientes de 1.254s a 584s, y MENOS
    paciencia pide MÁS gente en Erlang A.

    Se separa del resto porque miden cosas distintas: el ausentismo y la
    disponibilidad son del régimen actual, y la paciencia es del que llama."""
    import inspect
    from app import planificador_servicio as servicio

    assert servicio.DIAS_PACIENCIA >= 365
    fuente = inspect.getsource(servicio.calibrar)
    assert "desde_paciencia" in fuente
    # Nunca más corta que la ventana general, aunque alguien pida 365 días de
    # shrinkage: sería medir la paciencia con menos datos que antes.
    assert "max(dias_paciencia, dias)" in fuente


def test_no_se_propone_una_disponibilidad_que_no_sostiene_el_break():
    """Un factor neto mayor que 1 no es "disponibilidad perfecta": es que lo que
    se pierde, medido, es MENOS que el break solo. Toparlo en 1 y aplicarlo sería
    sacarle a la dotación el descuento entero, en silencio — la única dirección
    en la que este número puede hacer daño."""
    from app import planificador_servicio as servicio
    cfg = _cfg_con_franjas((0, 24, 0.80))
    medido = {"por_hora": [
        {"hora": h, "muestras": 100, "factor_medido": 0.93,
         "factor_aplicable": None} for h in range(24)]}

    assert servicio._franjas_desde_medicion(cfg, medido) == []


def test_la_hora_que_si_entra_se_aplica_igual():
    from app import planificador_servicio as servicio
    cfg = _cfg_con_franjas((0, 24, 0.80))
    medido = {"por_hora": [
        {"hora": h, "muestras": 100, "factor_medido": 0.75,
         "factor_aplicable": (0.818 if h >= 8 else None)} for h in range(24)]}

    franjas = servicio._franjas_desde_medicion(cfg, medido)

    assert franjas == [{"dia_semana": 0, "hora_desde": 0, "hora_hasta": 8, "factor": 0.8},
                       {"dia_semana": 0, "hora_desde": 8, "hora_hasta": 24, "factor": 0.818}]
