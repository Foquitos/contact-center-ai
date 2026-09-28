#!/usr/bin/env python3
"""Descarga el clima del área de concesión y lo deja en planificacion.Clima.

QUÉ TRAE
--------
Dos cosas distintas que van a la misma tabla y se distinguen por `EsPronostico`:

  - OBSERVADO  (archive-api): lo que efectivamente pasó. Es con lo único que se
    ENTRENA el modelo. Entrenar con pronósticos meteorológicos le enseñaría al
    planificador el error del meteorólogo además del propio.
  - PRONOSTICADO (forecast api): hasta 16 días hacia adelante. Es lo único que
    hay sobre el futuro, así que es lo que se usa para PRONOSTICAR.

El archivo de observaciones va unos días atrás del presente, así que la corrida
diaria vuelve a pedir los últimos días: los que ya tienen observación pisan al
pronóstico que había quedado, y la fila queda con el dato real. Es el mismo
criterio de recarga que usa el informe IVR.

    python scripts/clima_voltara.py                  # incremental (60 días atrás + 16 adelante)
    python scripts/clima_voltara.py --desde 2024-01-01  # carga histórica completa
    python scripts/clima_voltara.py --campana 20
    python scripts/clima_voltara.py --todas          # las campañas con coordenadas (cron)

SOBRE LA FUENTE
---------------
Open-Meteo no pide API key, pero su plan gratuito es para uso NO COMERCIAL. Antes
de dejar esto en producción hay que resolverlo: o se contrata el plan comercial, o
se cambia la fuente por el Servicio Meteorológico Nacional, que es dato público
argentino. La tabla y el modelo no cambian; sólo este script. Por eso la columna
Origen guarda de dónde salió cada fila: el día que se migre, se sabe qué hay que
volver a bajar.

OTRAS FUENTES QUE SE EVALUARON (2026-09-08)
--------------------------------------------
Probadas de verdad, no listadas de memoria. Queda acá para no volver a
investigarlas desde cero.

  SMN — Servicio Meteorológico Nacional
    https://ws.smn.gob.ar/alerts/type/AL  y  ssl.smn.gob.ar/dpd/descarga_opendata.php
    Es LA fuente que resolvería lo de la licencia (dato público argentino) y
    además publica avisos a corto plazo por tormenta y por ola de calor/frío, que
    es señal hacia adelante y no sólo temperatura. PERO desde SRV00 contesta
    503/522 detrás de Cloudflare, con y sin User-Agent de navegador. Hay que
    reintentar desde SRV01 antes de descartarla: puede ser bloqueo por IP o una
    caída puntual.

  CAMMESA — demanda eléctrica del sistema
    https://api.cammesa.com/demanda-svc/demanda/ObtieneDemandaYTemperaturaRegion?id_region=426
    ANDA, y tiene región GBA (id 426), que es casi exactamente el área de Voltara
    más Norluz. Devuelve demanda cada 5 minutos con `demHoy`, `demAyer`,
    `demSemanaAnt`, `demPrevista` (¡pronóstico de demanda!) y temperatura.
    NO SIRVE PARA ENTRENAR: el parámetro de fecha se ignora y sólo devuelve el
    día en curso, así que no hay histórico con el que ajustar nada. Y aun con
    histórico, la demanda eléctrica es casi una función de la temperatura, que ya
    está modelada.
    DONDE SÍ SERVIRÍA: en el seguimiento INTRADÍA. `demHoy` contra `demPrevista`
    para GBA, en vivo, es una medida directa de estrés de la red — si el sistema
    está tirando más de lo previsto, van a entrar más reclamos en las próximas
    horas. Eso es información que hoy no tenemos por ningún otro lado.

  Open-Meteo, campos que todavía no se usan
    weather_code (código de tormenta), cloud_cover_mean, shortwave_radiation_sum,
    precipitation_probability_max, precipitation_hours. Vienen en la MISMA
    llamada, así que son gratis. El más prometedor es
    `precipitation_probability_max`: para los días futuros el pronóstico da
    probabilidad, mientras que el archivo histórico da milímetros caídos, y esa
    asimetría entre lo que se entrena y lo que se predice hoy no está resuelta.
"""

import argparse
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import engine                      # noqa: E402
from app import planificador_datos as pdatos         # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("clima")

ORIGEN = "open-meteo"
ZONA = "America/Argentina/Buenos_Aires"
DIARIOS = ("temperature_2m_max,temperature_2m_min,temperature_2m_mean,"
           "apparent_temperature_max,apparent_temperature_min,precipitation_sum,"
           "wind_speed_10m_max,wind_gusts_10m_max,relative_humidity_2m_mean")

# El archivo de observaciones (archive-api) llega hasta AYER. Se le pedía hasta
# hace 5 días, suponiendo el retraso del reanálisis, y los días del medio quedaban
# guardados como PRONÓSTICO: el modelo no entrenaba con ellos y la salud del
# planificador marcaba el clima atrasado todas las mañanas. Medido el 2026-09-15,
# del 01 al 14/09 el archivo trae exactamente los mismos valores que el análisis
# de la API de pronóstico. Si el archivo viene con los últimos días vacíos, esos
# días se cierran con ese análisis (ver `cerrar_dias_pasados`).
DIAS_PASADOS_DEL_PRONOSTICO = 7


REINTENTOS = 3
ESPERA_ENTRE_REINTENTOS_SEG = 20


def _pedir(url: str, params: dict) -> dict:
    # El archivo de Open-Meteo a veces contesta 503 o no contesta durante un
    # rato (pasó el 2026-09-14 toda la mañana) mientras la API de pronóstico anda.
    ultimo = None
    for intento in range(1, REINTENTOS + 1):
        try:
            r = requests.get(url, params=params, timeout=90)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            ultimo = e
            log.warning("Clima: %s falló (intento %d de %d): %s",
                        url, intento, REINTENTOS, e)
            if intento < REINTENTOS:
                time.sleep(ESPERA_ENTRE_REINTENTOS_SEG)
    raise ultimo


def _filas(payload: dict, es_pronostico: bool):
    d = payload.get("daily") or {}
    tiempos = d.get("time") or []

    def col(nombre):
        return d.get(nombre) or [None] * len(tiempos)

    for i, t in enumerate(tiempos):
        yield {
            "fecha": date.fromisoformat(t),
            "t_max": col("temperature_2m_max")[i],
            "t_min": col("temperature_2m_min")[i],
            "t_media": col("temperature_2m_mean")[i],
            "ap_max": col("apparent_temperature_max")[i],
            "ap_min": col("apparent_temperature_min")[i],
            "lluvia": col("precipitation_sum")[i],
            "viento": col("wind_speed_10m_max")[i],
            "rafaga": col("wind_gusts_10m_max")[i],
            "humedad": col("relative_humidity_2m_mean")[i],
            "es_pron": es_pronostico,
        }


def cerrar_dias_pasados(observado: list, pronosticado: list, hoy: date) -> list:
    """Lo que se guarda como observado: el archivo sin los días que vinieron vacíos,
    más los días ya pasados que el archivo todavía no tiene, tomados del análisis
    de la API de pronóstico (la mejor estimación de un día que ya pasó, no un
    pronóstico). Así el observado llega a ayer venga como venga el archivo."""
    con_dato = [f for f in observado if f.get("t_max") is not None]
    vistos = {f["fecha"] for f in con_dato}
    faltan = [dict(f, es_pron=False) for f in pronosticado
              if f["fecha"] < hoy and f["fecha"] not in vistos
              and f.get("t_max") is not None]
    return sorted(con_dato + faltan, key=lambda f: f["fecha"])


def descargar(campana_id: int, desde: date, dias_adelante: int = 16) -> dict:
    with engine.connect() as conn:
        cfg = pdatos.clima_de_la_campana(conn, campana_id)
    if not cfg:
        raise SystemExit("Falta correr scripts/migrations/2026-09-07d_planificador_clima.sql")
    if cfg["lat"] is None or cfg["lon"] is None:
        raise SystemExit(f"La campaña {campana_id} no tiene coordenadas de clima cargadas.")

    hoy = date.today()
    comun = {"latitude": cfg["lat"], "longitude": cfg["lon"],
             "daily": DIARIOS, "timezone": ZONA}

    pronosticado = list(_filas(_pedir(
        "https://api.open-meteo.com/v1/forecast",
        {**comun, "past_days": DIAS_PASADOS_DEL_PRONOSTICO,
         "forecast_days": min(16, max(1, dias_adelante))}), True))

    try:
        observado = list(_filas(_pedir(
            "https://archive-api.open-meteo.com/v1/archive",
            {**comun, "start_date": desde.isoformat(),
             "end_date": (hoy - timedelta(days=1)).isoformat()}), False))
    except requests.RequestException as e:
        # Sin el archivo, los días recientes igual se pueden cerrar con el análisis
        # de la API de pronóstico (`past_days`, hasta 92 días): es la mejor
        # estimación del modelo para días que ya pasaron, no un pronóstico. Si no
        # se hiciera, un corte del archivo dejaría la tabla congelada y el
        # planificador corregiría el nivel contra pronósticos viejos (pasó entre
        # el 04 y el 13/09/2026). La próxima corrida con el archivo arriba lo pisa.
        log.warning("Clima: el archivo no respondió (%s); los días pasados se "
                    "cierran con el análisis de la API de pronóstico.", e)
        dias_atras = min(92, max(DIAS_PASADOS_DEL_PRONOSTICO, (hoy - desde).days))
        observado = [f for f in _filas(_pedir(
            "https://api.open-meteo.com/v1/forecast",
            {**comun, "past_days": dias_atras, "forecast_days": 1}), False)
            if f["fecha"] < hoy]
    observado = cerrar_dias_pasados(observado, pronosticado, hoy)

    # El observado se escribe DESPUÉS para que pise al pronóstico en los días que
    # se solapan: el dato real siempre gana.
    with engine.begin() as conn:
        n_p = pdatos.guardar_clima(conn, campana_id, pronosticado, ORIGEN)
        n_o = pdatos.guardar_clima(conn, campana_id, observado, ORIGEN)

    with engine.connect() as conn:
        todo = pdatos.clima_por_dia(conn, campana_id, desde,
                                    hoy + timedelta(days=dias_adelante + 1))
        obs = pdatos.clima_por_dia(conn, campana_id, desde,
                                   hoy + timedelta(days=dias_adelante + 1),
                                   solo_observado=True)
    return {"pronosticados": n_p, "observados": n_o,
            "en_tabla": len(todo), "observados_en_tabla": len(obs),
            "desde": min(todo) if todo else None,
            "hasta": max(todo) if todo else None,
            "lat": cfg["lat"], "lon": cfg["lon"], "activo": cfg["activo"]}


def campanas_con_coordenadas() -> list:
    """Las campañas activas con punto de clima cargado, prendido o no: con el clima
    apagado igual hace falta la serie para poder probarlo en el Laboratorio."""
    with engine.connect() as conn:
        return [c for c in pdatos.campanas_con_fuente(conn)
                if (pdatos.clima_de_la_campana(conn, c) or {}).get("lat") is not None]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--campana", type=int, default=pdatos.CAMPANA_VOLTARA)
    ap.add_argument("--todas", action="store_true",
                    help="todas las campañas con coordenadas (lo usa clima_diario.sh)")
    ap.add_argument("--desde", type=date.fromisoformat,
                    default=date.today() - timedelta(days=60))
    ap.add_argument("--dias-adelante", type=int, default=16)
    args = ap.parse_args()

    campanas = campanas_con_coordenadas() if args.todas else [args.campana]
    errores = 0
    for campana in campanas:
        try:
            r = descargar(campana, args.desde, args.dias_adelante)
        except (SystemExit, requests.RequestException) as e:
            # Una campaña que falla no deja sin clima a las demás.
            log.error("Campaña %d: no se pudo bajar el clima: %s", campana, e)
            errores += 1
            continue
        log.info("Campaña %d, punto (%s, %s). Bajados: %d observados, %d pronosticados.",
                 campana, r["lat"], r["lon"], r["observados"], r["pronosticados"])
        log.info("En la tabla: %d días (%s a %s), de los cuales %d observados.",
                 r["en_tabla"], r["desde"], r["hasta"], r["observados_en_tabla"])
        if not r["activo"]:
            log.warning("El clima está APAGADO para la campaña %d: el pronóstico todavía "
                        "no lo usa. Probalo en la pestaña Laboratorio (Probar un cambio) "
                        "y recién después dejalo vigente.", campana)
    if errores:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
