#!/usr/bin/env python3
"""Mide el perfil de presencia por media hora y lo guarda (planificacion.PerfilPresencia).

POR QUÉ
-------
Ignacio (2026-09-15) pidió aplicar el retraso al arrancar el turno: el faltante de
la gente con turno no es parejo en el día. La medición lee 8 semanas del registro
de RRHH y de los conectados —dos lecturas pesadas, cerca de un minuto—, así que no
puede correr en cada recálculo. Corre una vez por semana, antes del recálculo
automático de las 06:00, y el planificador lee la tabla. Ver
backend/app/planificador_presencia.py.

Uso:
    python scripts/planificador_perfil_presencia.py            # mide y guarda
    python scripts/planificador_perfil_presencia.py --dry-run  # mide y muestra
    python scripts/planificador_perfil_presencia.py --campana 20
"""

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import text

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app.database import engine                          # noqa: E402
from app import planificador_datos as pdatos             # noqa: E402
from app import planificador_presencia as ppresencia     # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("perfil_presencia")


def campanas_activas(conn) -> list:
    con_fuente = pdatos.campanas_con_fuente(conn)
    if con_fuente:
        return con_fuente
    if pdatos._tiene_columna(conn, "planificacion.Campana", "Activa"):
        return list(conn.execute(text(
            "SELECT CampanaID FROM planificacion.Campana WHERE Activa = 1 ORDER BY CampanaID"
        )).scalars())
    return [pdatos.CAMPANA_VOLTARA]


def medir(conn, campana_id: int, hoy: date, semanas: int) -> list:
    cfg = pdatos.cargar_config(conn, campana_id)
    desde = hoy - timedelta(weeks=semanas)
    turnos: dict = {}
    for pool in cfg.pools.values():
        for momento, personas in pdatos.dotacion_real(
                conn, pool.pool_id, desde, hoy, cfg.intervalo_min).items():
            turnos[momento] = turnos.get(momento, 0) + personas
    # Los turnos son de la gente del pool; los conectados, los que cubren la
    # línea: pool + otras sub-campañas, sin Digital. Es la misma población contra
    # la que se mide la disponibilidad (menos la palanca). Ver
    # `ppresencia.conectados_que_cubren`.
    conectados = ppresencia.conectados_que_cubren(pdatos.conectados_por_origen(
        conn, campana_id, list(cfg.pools), desde, hoy))
    feriados = pdatos.feriados(conn, desde, hoy)
    return ppresencia.medir_perfil(turnos, conectados, feriados, hoy=hoy)


def resumen(filas: list) -> str:
    nivel = ppresencia.nivel_por_tipo(filas)
    partes = [" · ".join(f"nivel {t} {v['faltante']:.1%}" for t, v in sorted(nivel.items()))]
    for tipo in ("habil", "no_habil"):
        del_tipo = sorted((f for f in filas if f["tipo_dia"] == tipo),
                          key=lambda f: f["exceso"], reverse=True)
        if not del_tipo:
            continue
        hora = lambda f: f"{f['minuto'] // 60:02d}:{f['minuto'] % 60:02d} {f['exceso']:+.1%}"
        partes.append(f"{tipo}: más faltante {', '.join(hora(f) for f in del_tipo[:4])}"
                      f" · menos {', '.join(hora(f) for f in del_tipo[-3:])}")
    return " | ".join(partes)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--campana", type=int, default=None)
    ap.add_argument("--semanas", type=int, default=ppresencia.SEMANAS)
    ap.add_argument("--dry-run", action="store_true", help="mide y muestra, no guarda")
    args = ap.parse_args()
    hoy = date.today()

    with engine.connect() as conn:
        campanas = [args.campana] if args.campana else campanas_activas(conn)

    errores = 0
    for cid in campanas:
        try:
            with engine.connect() as conn:
                filas = medir(conn, cid, hoy, args.semanas)
            if not filas:
                log.warning("Campaña %d: sin datos para medir (¿pool sin origen o sin conectados?)", cid)
                continue
            log.info("Campaña %d: %d medias horas. %s", cid, len(filas), resumen(filas))
            if args.dry_run:
                continue
            with engine.begin() as conn:
                guardadas = pdatos.guardar_perfil_presencia(conn, cid, filas)
            log.info("Campaña %d: perfil guardado (%d filas)", cid, guardadas)
        except pdatos.MigracionPendiente as e:
            log.error("Campaña %d: %s", cid, e)
            errores += 1
        except Exception:
            log.exception("Campaña %d: error midiendo el perfil de presencia", cid)
            errores += 1
    return 1 if errores else 0


if __name__ == "__main__":
    sys.exit(main())
