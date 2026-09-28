"""Mide cuánto rinde la gente nueva y lo guarda para el planificador.

Por cada campaña con fuente de datos:
  1. Rinde de cada tramo de antigüedad (días desde el pase a piso) contra los
     antiguos, cola por cola, sobre las últimas semanas del detalle de llamadas.
  2. Cuántas llamadas atendió cada tramo en la historia con la que se mide el TMO
     del pronóstico (las semanas de historia de la campaña).
  3. Peso de cada tramo en la malla = rinde normalizado contra esa mezcla, y se
     reemplaza planificacion.CurvaAntiguedad.

Tarda un par de minutos (lee meses del detalle de llamadas y un año del informe
por agente), por eso no corre en cada recálculo. Cron semanal, antes del
recálculo automático:

    45 5 * * 1  cd /home/app/contact-center-ai && PYTHONIOENCODING=utf-8 \
        .venv/bin/python scripts/planificador_antiguedad.py \
        >> logs/planificador_antiguedad.log 2>&1

Uso:
    python scripts/planificador_antiguedad.py                 # todas
    python scripts/planificador_antiguedad.py --campana 20 --dry-run
"""

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.database import engine                          # noqa: E402
from app import planificador_antiguedad as pant          # noqa: E402
from app import planificador_datos as pdatos             # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("antiguedad")


def medir(conn, campana_id: int, hoy: date, semanas: int) -> list:
    cfg = pdatos.cargar_config(conn, campana_id)
    rinde = pant.medir_rinde(pdatos.rinde_por_antiguedad(
        conn, campana_id, hoy - timedelta(weeks=semanas), hoy))
    # La mezcla contra la que se normaliza es la de la historia del TMO del
    # pronóstico, que es la de las semanas de historia de la campaña.
    historia = pant.llamadas_por_tramo(pdatos.llamadas_por_antiguedad(
        conn, campana_id, hoy - timedelta(weeks=cfg.semanas_base), hoy))
    if len(rinde) <= 1 or not historia:
        return []
    return pant.armar_curva(rinde, historia)


def resumen(filas: list) -> str:
    def tramo(f):
        hasta = f"{f['dia_hasta']}" if f["dia_hasta"] is not None else "+"
        return (f"{f['dia_desde']}-{hasta} d: rinde {f['rinde']:.0%} peso {f['factor']:.3f} "
                f"({f['llamadas']} llam., {f['participacion'] or 0:.1%} de la historia)")
    return " | ".join(tramo(f) for f in filas)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--campana", type=int, default=None)
    ap.add_argument("--semanas", type=int, default=pant.SEMANAS_RINDE,
                    help="semanas de llamadas para medir el rinde")
    ap.add_argument("--dry-run", action="store_true", help="mide y muestra, no guarda")
    args = ap.parse_args()
    hoy = date.today()

    with engine.connect() as conn:
        campanas = [args.campana] if args.campana else pdatos.campanas_con_fuente(conn)

    errores = 0
    for cid in campanas:
        try:
            with engine.connect() as conn:
                filas = medir(conn, cid, hoy, args.semanas)
            if not filas:
                log.warning("Campaña %d: sin datos para medir el rinde por antigüedad", cid)
                continue
            log.info("Campaña %d: %s", cid, resumen(filas))
            if args.dry_run:
                continue
            with engine.begin() as conn:
                guardadas = pdatos.guardar_curva_antiguedad(conn, cid, filas)
            log.info("Campaña %d: curva guardada (%d tramos)", cid, guardadas)
        except pdatos.MigracionPendiente as e:
            log.error("Campaña %d: %s", cid, e)
            errores += 1
        except Exception:
            log.exception("Campaña %d: error midiendo el rinde por antigüedad", cid)
            errores += 1
    return 1 if errores else 0


if __name__ == "__main__":
    sys.exit(main())
