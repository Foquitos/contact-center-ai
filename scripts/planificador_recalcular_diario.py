#!/usr/bin/env python3
"""Recalcula el planificador automáticamente dos veces al día.

POR QUÉ
-------
Ignacio (2026-09-15): 76 corridas desde el 03/09 fueron todas a mano entre las
11 y 20 h, y ninguna en sábado ni domingo: el plan del fin de semana quedaba
congelado con el del viernes. Este script corre desde cron (06:00 y 14:00),
después del IVR nocturno de las 04:00 y del clima de 05:30/13:30.

QUÉ HACE
--------
Para cada campaña activa de `planificacion.Campana` (o con corrida vigente),
ejecuta:
    servicio.recalcular(engine, campana_id, dias=30, horizonte="operativo",
                        usuario=None, origen="automatico")

Un error en una campaña no interrumpe las demás; devuelve código de salida
distinto de cero si alguna falló.

Uso:
    python scripts/planificador_recalcular_diario.py
    python scripts/planificador_recalcular_diario.py --campana 20 --dias 30
"""

import argparse
import logging
import sys
from pathlib import Path

from sqlalchemy import text

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.database import engine                        # noqa: E402
from app import planificador_datos as pdatos           # noqa: E402
from app import planificador_servicio as servicio      # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("recalcular_diario")


def listar_campanas_activas(conn) -> list[int]:
    """Obtiene los IDs de las campañas activas para recalcular.

    Sólo las que tienen fuente de datos: una campaña dada de alta en el
    planificador antes de que exista su fuente fallaría en cada corrida."""
    con_fuente = pdatos.campanas_con_fuente(conn)
    if con_fuente:
        return con_fuente
    if pdatos._tiene_tabla(conn, "planificacion.Campana"):
        if pdatos._tiene_columna(conn, "planificacion.Campana", "Activa"):
            filas = conn.execute(text(
                "SELECT CampanaID FROM planificacion.Campana WHERE Activa = 1 ORDER BY CampanaID"
            )).scalars().all()
            if filas:
                return list(filas)
        else:
            filas = conn.execute(text(
                "SELECT CampanaID FROM planificacion.Campana ORDER BY CampanaID"
            )).scalars().all()
            if filas:
                return list(filas)

    # Fallback: campañas con corrida vigente
    if pdatos._tiene_tabla(conn, "planificacion.Corrida"):
        filas = conn.execute(text(
            "SELECT DISTINCT CampanaID FROM planificacion.Corrida WHERE EsVigente = 1 ORDER BY CampanaID"
        )).scalars().all()
        if filas:
            return list(filas)

    return [pdatos.CAMPANA_VOLTARA]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--campana", type=int, default=None,
                    help="ID de campaña específica (por defecto todas las activas)")
    ap.add_argument("--dias", type=int, default=30,
                    help="Días del horizonte a pronosticar (por defecto 30)")
    ap.add_argument("--horizonte", type=str, default="operativo",
                    help="Horizonte de planificación ('operativo' o 'presupuesto')")
    args = ap.parse_args()

    with engine.connect() as conn:
        if args.campana is not None:
            campanas = [args.campana]
        else:
            campanas = listar_campanas_activas(conn)

    log.info("Iniciando recálculo diario automático para campañas: %s (días=%d, horizonte=%s)",
             campanas, args.dias, args.horizonte)

    errores = 0
    for cid in campanas:
        try:
            log.info("Recalculando campaña %d...", cid)
            resumen = servicio.recalcular(
                engine,
                campana_id=cid,
                dias=args.dias,
                horizonte=args.horizonte,
                usuario=None,
                origen="automatico"
            )
            corrida_id = resumen.get("corrida_id")
            llamadas = resumen.get("llamadas", 0)
            log.info("Campaña %d OK (corrida_id=%s, llamadas=%s, avisos=%d)",
                     cid, corrida_id, llamadas, len(resumen.get("avisos", [])))
        except Exception:
            log.exception("Error al recalcular campaña %d", cid)
            errores += 1

    if errores:
        log.error("Finalizado con %d error(es) sobre %d campaña(s)", errores, len(campanas))
        return 1

    log.info("Finalizado con éxito para todas las campañas (%d)", len(campanas))
    return 0


if __name__ == "__main__":
    sys.exit(main())
