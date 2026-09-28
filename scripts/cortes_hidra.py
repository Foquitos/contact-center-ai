#!/usr/bin/env python3
"""Avisos de corte de Hidra -> planificacion.AvisoCorte (propuesta de ajuste).

QUÉ HACE
--------
1. Baja la página de Novedades de Hidra y, por cada nota que todavía no está en la
   tabla, la lee con Gemini (fecha y hora del corte, instalaciones, zonas,
   escala) y deja un ajuste PROPUESTO para Hidra Técnico. La propuesta la aplica o
   la descarta una persona desde la pantalla del planificador.
2. Mide los cortes que ya pasaron: cuántas llamadas 'Corte programado' trajeron
   de más. Con eso la estimación se recalibra sola.

La web de Hidra sirve el certificado sin la cadena intermedia: se baja sin
verificarlo (es una página pública de sólo lectura, no se manda nada).

Un mail con el aviso (el que le llega a Operación) se carga a mano con
--archivo: .eml (guardado desde el cliente de correo) o .txt con el texto.

    python scripts/cortes_hidra.py                    # cron horario
    python scripts/cortes_hidra.py --archivo aviso.eml
    python scripts/cortes_hidra.py --solo-medir
"""

import argparse
import email
import email.policy
import hashlib
import logging
import sys
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional, Tuple

import requests
import urllib3

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

from app import planificador_cortes as pcortes       # noqa: E402
from app import planificador_datos as pdatos         # noqa: E402
from app.database import engine                      # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cortes_hidra")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CAMPANA = pdatos.CAMPANA_HIDRA_TECNICO
CABECERAS = {"User-Agent": "Mozilla/5.0 (planificador Acme)"}
# Tope por corrida: si la página cambia de formato y todo parece nuevo, no se
# gastan cientos de lecturas de IA de una.
MAX_NOTAS_POR_CORRIDA = 40


def _get(url: str) -> str:
    r = requests.get(url, headers=CABECERAS, timeout=60, verify=False)
    r.raise_for_status()
    r.encoding = "utf-8"
    return r.text


def leer_mail(ruta: Path) -> Tuple[str, Optional[date], Optional[str], str]:
    """(referencia, fecha, asunto, texto) de un .eml o un .txt."""
    crudo = ruta.read_bytes()
    if ruta.suffix.lower() != ".eml":
        texto = crudo.decode("utf-8", errors="replace")
        return f"archivo:{hashlib.sha1(crudo).hexdigest()}", None, ruta.stem, texto
    msg = email.message_from_bytes(crudo, policy=email.policy.default)
    cuerpo = msg.get_body(preferencelist=("plain", "html"))
    texto = cuerpo.get_content() if cuerpo else ""
    if cuerpo is not None and cuerpo.get_content_type() == "text/html":
        texto = pcortes._a_texto(texto)
    fecha = None
    if msg["Date"]:
        try:
            fecha = parsedate_to_datetime(msg["Date"]).date()
        except (TypeError, ValueError):
            fecha = None
    referencia = (msg["Message-ID"] or "").strip() or f"archivo:{hashlib.sha1(crudo).hexdigest()}"
    return referencia, fecha, msg["Subject"], texto


def cargar_web() -> None:
    enlaces = pcortes.enlaces_de_novedades(_get(pcortes.URL_NOVEDADES))
    with engine.connect() as conn:
        cargadas = pcortes.referencias_cargadas(conn, CAMPANA)
    nuevas = [u for u in enlaces if u not in cargadas][:MAX_NOTAS_POR_CORRIDA]
    log.info("Novedades: %d notas, %d nuevas", len(enlaces), len(nuevas))
    for url in nuevas:
        try:
            publicado, titulo, texto = pcortes.leer_nota(_get(url))
        except requests.RequestException as e:
            log.warning("No se pudo bajar %s: %s", url, e)
            continue
        # Una transacción por nota: si una falla, las anteriores quedan.
        with engine.begin() as conn:
            estado = pcortes.procesar_aviso(conn, CAMPANA, "web", url, publicado, titulo, texto)
        log.info("%s -> %s", url, estado)


def cargar_archivo(ruta: Path) -> None:
    referencia, fecha, asunto, texto = leer_mail(ruta)
    if not texto.strip():
        raise SystemExit(f"{ruta}: no se encontró texto.")
    with engine.begin() as conn:
        if referencia in pcortes.referencias_cargadas(conn, CAMPANA):
            log.info("%s ya estaba cargado.", ruta)
            return
        estado = pcortes.procesar_aviso(conn, CAMPANA, "mail", referencia, fecha, asunto, texto)
    log.info("%s -> %s", ruta, estado)


def medir() -> None:
    with engine.begin() as conn:
        n = pcortes.medir_pendientes(conn, CAMPANA)
    if n:
        log.info("Cortes medidos: %d", n)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--archivo", type=Path, help="Cargar un mail (.eml) o texto (.txt) con un aviso")
    ap.add_argument("--solo-medir", action="store_true")
    args = ap.parse_args()

    with engine.connect() as conn:
        if not pcortes.hay_tabla(conn):
            log.error("Falta aplicar scripts/migrations/2026-09-24b_planificador_avisos_corte.sql")
            return 1
    if args.archivo:
        cargar_archivo(args.archivo)
        return 0
    if not args.solo_medir:
        try:
            cargar_web()
        except requests.RequestException as e:
            # La medición no depende de la web: sigue igual.
            log.error("No se pudo leer Novedades de Hidra: %s", e)
    medir()
    return 0


if __name__ == "__main__":
    sys.exit(main())
