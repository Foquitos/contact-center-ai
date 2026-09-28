"""Tests de la segunda opinión del nivel diario (app/planificador_nivel.py).

Offline: series sintéticas con una relación conocida entre el clima y la
demanda, así que se puede afirmar que el modelo la recupera —y, sobre todo, que
no ve lo que no tiene que ver y que apagado no cambia nada.

Correr: pytest tests/test_planificador_nivel.py -m "not tokens"
"""
import math
from datetime import date, datetime, time, timedelta

import pytest

from app import planificador as pl
from app import planificador_nivel as pn

sklearn = pytest.importorskip("sklearn")


def _dia_clima(t_max=20.0, t_min=15.0, lluvia=0.0, rafaga=20.0, humedad=60.0,
               viento=10.0):
    return {"t_aparente_max": t_max, "t_aparente_min": t_min,
            "lluvia_mm": lluvia, "rafaga_kmh": rafaga, "humedad_pct": humedad,
            "viento_kmh": viento}


def _clima(desde: date, dias: int, frio_desde=None, frio_hasta=None):
    """Templado, con un tramo de frío si se pide."""
    out = {}
    for i in range(dias):
        d = desde + timedelta(days=i)
        frio = (frio_desde is not None and frio_desde <= d
                and (frio_hasta is None or d <= frio_hasta))
        out[d] = _dia_clima(t_max=8.0, t_min=2.0) if frio else _dia_clima()
    return out


def _serie(desde: date, dias: int, base=1000.0, clima=None, factor_frio=2.0):
    """Demanda con una relación conocida: los días de frío valen el doble."""
    out = {}
    for i in range(dias):
        d = desde + timedelta(days=i)
        c = (clima or {}).get(d)
        frio = bool(c and c["t_aparente_min"] < 14.0)
        # Un poco de forma de día de semana para que el perfil no sea plano.
        dow = 0.55 if d.isoweekday() >= 6 else 1.0
        out[d] = base * dow * (factor_frio if frio else 1.0)
    return out


# --------------------------------------------------------------- la mezcla

def test_peso_cero_devuelve_el_ajuste_INTACTO():
    """La garantía que hace que la migración pueda nacer apagada: con peso 0 el
    pronóstico tiene que ser el de hoy, no uno parecido."""
    assert pl._mezclar_nivel(1.234, 1000.0, 9999.0, 0.0) == 1.234


def test_sin_nivel_del_modelo_tampoco_se_toca_nada():
    """Un día sin clima no tiene nivel del modelo. No se inventa: se deja el
    ajuste como está, igual que hace el factor de clima."""
    assert pl._mezclar_nivel(1.1, 1000.0, None, 0.4) == 1.1
    assert pl._mezclar_nivel(1.1, 1000.0, 0.0, 0.4) == 1.1
    assert pl._mezclar_nivel(1.1, None, 2000.0, 0.4) == 1.1


def test_la_mezcla_es_la_media_GEOMETRICA_de_los_dos_niveles():
    """En logaritmo y no en llamadas: el error del pronóstico es multiplicativo.
    Con perfil 1.000 y ajuste 1,0 el nivel base es 1.000; contra 4.000 del
    modelo y peso 0,5 tiene que dar 2.000, no 2.500."""
    ajuste = pl._mezclar_nivel(1.0, 1000.0, 4000.0, 0.5)
    assert ajuste * 1000.0 == pytest.approx(2000.0)


def test_el_peso_manda_hacia_donde_corresponde():
    base, modelo = 1000.0, 2000.0
    niveles = [pl._mezclar_nivel(1.0, base, modelo, w) * base
               for w in (0.0, 0.25, 0.5, 0.75, 1.0)]
    assert niveles == sorted(niveles)
    assert niveles[0] == pytest.approx(base)
    assert niveles[-1] == pytest.approx(modelo)


# ------------------------------------------------------------------ ajuste

def test_recupera_una_relacion_conocida_con_el_clima():
    """Si los días de frío valen el doble, el modelo tiene que pedir un nivel
    claramente mayor para un día de frío que para uno templado."""
    desde = date(2024, 1, 1)
    clima = _clima(desde, 700)
    for i in range(0, 700, 11):          # días de frío salpicados
        d = desde + timedelta(days=i)
        clima[d] = _dia_clima(t_max=8.0, t_min=2.0)
    series = {6: _serie(desde, 700, clima=clima)}
    corte = desde + timedelta(days=700)
    modelo = pn.ajustar(series, clima, corte, feriados=[], excluidos=[])
    assert modelo is not None

    manana = corte
    clima[manana] = _dia_clima()
    clima[manana - timedelta(days=1)] = _dia_clima()
    templado = pn.niveles_por_skill(modelo, series, [manana], clima, corte)
    clima[manana] = _dia_clima(t_max=8.0, t_min=2.0)
    clima[manana - timedelta(days=1)] = _dia_clima(t_max=8.0, t_min=2.0)
    frio = pn.niveles_por_skill(modelo, series, [manana], clima, corte)

    assert frio[6][manana] > templado[6][manana] * 1.3


def test_no_ve_NADA_posterior_a_su_corte():
    """El corte es duro por la misma razón que en el backtest: un modelo que vio
    los días que después se evalúan da un número espectacular y falso."""
    desde = date(2024, 1, 1)
    clima = _clima(desde, 800)
    serie = _serie(desde, 800, clima=clima)
    corte = desde + timedelta(days=600)
    # Después del corte la demanda se multiplica por diez. El modelo no puede
    # enterarse: se ajusta con lo anterior.
    for i in range(600, 800):
        serie[desde + timedelta(days=i)] *= 10

    modelo = pn.ajustar({6: serie}, clima, corte, feriados=[], excluidos=[])
    assert modelo is not None
    assert modelo.entrenado_hasta == corte
    # Ninguna fila puede venir del tramo multiplicado.
    assert modelo.n <= 600


def test_sin_datos_suficientes_devuelve_None_y_no_se_inventa_nada():
    """Menos de MIN_FILAS_PARA_AJUSTAR es ruido. Vale más no corregir."""
    desde = date(2026, 1, 1)
    clima = _clima(desde, 40)
    series = {6: _serie(desde, 40, clima=clima)}
    assert pn.ajustar(series, clima, desde + timedelta(days=40)) is None
    # Y sin modelo, no hay niveles: el que llama tiene que ver el vacío.
    assert pn.niveles_por_skill(None, series, [desde], clima, desde) == {}


def test_devuelve_un_NIVEL_en_llamadas_y_no_un_factor():
    """La diferencia importa: el perfil con el que entrena (mediana del total
    diario) no es el que arma baseline_estacional (suma de medianas por
    intervalo), y difieren en un factor constante. Devolver el nivel deja esa
    diferencia adentro del módulo."""
    desde = date(2024, 1, 1)
    clima = _clima(desde, 700)
    series = {6: _serie(desde, 700, base=1000.0, clima=clima)}
    corte = desde + timedelta(days=700)
    modelo = pn.ajustar(series, clima, corte)
    clima[corte] = _dia_clima()
    niveles = pn.niveles_por_skill(modelo, series, [corte], clima, corte)
    # Del orden del volumen diario, no del orden de 1.
    assert 300.0 < niveles[6][corte] < 3000.0


def test_en_lote_da_lo_mismo_que_de_a_uno():
    """niveles_por_skill predice todo junto (de a una fila, el recálculo de Voltara
    pasaba el minuto y el frontend lo cortaba). El número tiene que ser el mismo."""
    desde = date(2024, 1, 1)
    clima = _clima(desde, 760, frio_desde=desde + timedelta(days=300),
                   frio_hasta=desde + timedelta(days=330))
    series = {6: _serie(desde, 700, base=1000.0, clima=clima),
              9: _serie(desde, 700, base=200.0, clima=clima)}
    corte = desde + timedelta(days=700)
    modelo = pn.ajustar(series, clima, corte)
    dias = [corte - timedelta(days=30) + timedelta(days=i) for i in range(45)]
    en_lote = pn.niveles_por_skill(modelo, series, dias, clima, corte)
    fer = set()
    for sid, serie in series.items():
        indice = pn.pclima._indice(serie, fer)
        for dia in dias:
            base = pn.pclima._perfil(indice, min(dia, corte), 52, set(), pn.pclima._dow(dia, fer))
            f = modelo.factor(sid, dia, clima, fer, base)
            assert en_lote[sid][dia] == pytest.approx(base * f)


def test_el_factor_esta_topeado():
    """Un día con rasgos fuera de todo lo visto no puede multiplicar por diez.
    Mismo criterio que el modelo de clima."""
    assert pn.FACTOR_MIN > 0 and pn.FACTOR_MAX <= 5.0


def test_la_columna_de_skill_y_la_de_dia_de_semana_son_CATEGORICAS():
    """El día de semana no está ordenado —el domingo no es 'más' que el sábado—
    y el número de cola menos todavía. Tratarlas como continuas deja al árbol
    partiendo por 'skill <= 4,5', que no significa nada."""
    cat = dict(zip(pn.COLUMNAS, pn.CATEGORICAS))
    assert cat["dow"] and cat["skill"]
    assert not cat["log_perfil"] and not cat["cdd"]


def test_NO_lleva_rasgos_de_rezago():
    """Medido: con el nivel de los últimos 7 y 28 días da 21,92% de WAPE y sin
    ellos 21,62%. Duplican lo que ya hace la corrección de nivel y la mezcla los
    contaba dos veces. Si alguien los vuelve a agregar, que sea midiendo."""
    for prohibido in ("r7", "r28", "r_dow", "log_n7", "log_n28"):
        assert prohibido not in pn.COLUMNAS


def test_la_perdida_es_de_POISSON():
    """No es un detalle de tuning: el error cuadrático sobre el logaritmo estima
    la mediana y al volver con la exponencial se queda corto (Jensen). Con
    Poisson sobre el cociente ponderado por el perfil se estima la media, y
    medido mejora el error Y baja el sesgo a la vez."""
    assert pn.HIPERPARAMETROS["loss"] == "poisson"
    assert pn.HIPERPARAMETROS["early_stopping"] is False


# ------------------------------------------------- integración con la línea de base

def _historico_intervalos(desde: date, dias: int, por_dia=480.0):
    """Serie por media hora con una curva intradía marcada."""
    forma = [0.5] * 12 + [1.5] * 24 + [1.0] * 12      # noche / día / tarde
    total = sum(forma)
    out = {}
    for i in range(dias):
        d = desde + timedelta(days=i)
        nivel = por_dia * (0.55 if d.isoweekday() >= 6 else 1.0)
        for k in range(48):
            out[datetime.combine(d, time(k // 2, (k % 2) * 30))] = \
                nivel * forma[k] / total
    return out


def test_la_mezcla_mueve_el_NIVEL_y_deja_la_FORMA_intacta():
    """La etapa 2 no se toca: es lo que dice la medición (con el nivel diario
    perfecto, mejorar la forma da 0,2 puntos). La mezcla escala el día entero."""
    desde = date(2026, 1, 1)
    hist = _historico_intervalos(desde, 200)
    manana = desde + timedelta(days=200)

    sin = pl.baseline_estacional(hist, [manana], hoy=manana, semanas_base=52)
    con = pl.baseline_estacional(hist, [manana], hoy=manana, semanas_base=52,
                                 nivel_diario={manana: sum(sin.values()) * 4},
                                 peso_nivel=0.5)
    assert sum(con.values()) == pytest.approx(sum(sin.values()) * 2, rel=1e-9)
    # Misma curva: cada intervalo escalado por lo mismo.
    razones = [con[m] / sin[m] for m in sin if sin[m] > 0]
    assert max(razones) == pytest.approx(min(razones), rel=1e-9)


def test_apagada_el_pronostico_es_IDENTICO_al_de_hoy():
    """Lo que permite que la migración nazca apagada sin miedo."""
    desde = date(2026, 1, 1)
    hist = _historico_intervalos(desde, 200)
    manana = desde + timedelta(days=200)
    hoy = pl.baseline_estacional(hist, [manana], hoy=manana, semanas_base=52)
    con_nivel = pl.baseline_estacional(
        hist, [manana], hoy=manana, semanas_base=52,
        nivel_diario={manana: 999999.0}, peso_nivel=0.0)
    assert con_nivel == hoy


def test_la_campana_nace_con_la_segunda_opinion_APAGADA():
    """La migración 2026-09-09c la agrega apagada, y el dataclass tiene que decir
    lo mismo: una base sin la migración cae a estos valores, y si el default del
    código fuera True el pronóstico cambiaría solo al deployar."""
    cfg = pl.CampanaCfg(campana_id=20)
    assert cfg.nivel_gbdt is False
    # El peso ya está puesto en el valor medido, listo para cuando se prenda.
    assert cfg.nivel_gbdt_peso == pytest.approx(0.40)


def test_el_peso_por_defecto_es_el_MEDIDO_y_no_uno_redondo():
    """0,40 sale del barrido sobre 464 días (WAPE 21,62% contra 22,69% de
    producción, MAPE diario 17,54% contra 19,71%), no de elegir un número lindo.
    Si alguien lo cambia, que sea midiendo."""
    assert 0.30 <= pl.CampanaCfg(campana_id=1).nivel_gbdt_peso <= 0.50
