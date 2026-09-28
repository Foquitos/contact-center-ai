#!/usr/bin/env python3
"""Recalcula cada semana cuánto pesa el pronóstico del cliente (domingos y feriados).

POR QUÉ
-------
Ignacio (2026-09-14) pidió combinar nuestro pronóstico con el que manda Voltara
"según quién acertó últimamente". El peso sale de los últimos 15 domingos y
feriados (`planificador_combinacion.pesos_recientes`), así que tiene que
re-medirse solo: si nadie aprieta el botón de calibrar, el peso se queda con el
acierto de hace dos meses. Pensado para el lunes a la mañana, cuando el fin de
semana ya cerró en el informe IVR.

QUÉ HACE
--------
Corre la calibración (`servicio.calibrar_combinacion`, backtest a 2 días de
antelación sobre los últimos 180 días) y GUARDA el peso medido. NO prende la
combinación: esa llave es `CombinarCliente` en la configuración de la campaña, y
sigue siendo una decisión de la operación.

    python scripts/planificador_combinacion_semanal.py
    python scripts/planificador_combinacion_semanal.py --campana 20 --sin-guardar
"""

import argparse
import json
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.database import engine                        # noqa: E402
from app import planificador_datos as pdatos           # noqa: E402
from app import planificador_servicio as servicio      # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("combinacion_semanal")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--campana", type=int, default=pdatos.CAMPANA_VOLTARA)
    ap.add_argument("--dias", type=int, default=180)
    ap.add_argument("--antelacion", type=int, default=2)
    ap.add_argument("--sin-guardar", action="store_true")
    args = ap.parse_args()

    r = servicio.calibrar_combinacion(engine, args.campana, dias=args.dias,
                                      antelacion=args.antelacion)
    pesos = r["pesos"]
    log.info("Pesos medidos: %s", json.dumps(pesos, default=str))
    for t in r["por_tipo_de_dia"]:
        log.info("  %-9s n=%3d  nuestro %s  cliente %s  combinado %s", t["tipo"], t["n"],
                 t["nuestro"].get("mape"), t["cliente"].get("mape"), t["combinado"].get("mape"))
    no_habil = pesos.get("no_habil", {}).get("peso")
    if no_habil is None:
        log.warning("No hubo domingos/feriados suficientes con los dos pronósticos: no se guarda nada.")
        return 1
    if args.sin_guardar:
        return 0
    with engine.begin() as conn:
        # El hábil queda en 0 a propósito: medido, el cliente no le aporta.
        pdatos.guardar_pesos_combinacion(conn, args.campana, 0.0, no_habil)
    log.info("Guardado: peso del cliente en domingos y feriados = %.3f.", no_habil)
    return 0


if __name__ == "__main__":
    sys.exit(main())
