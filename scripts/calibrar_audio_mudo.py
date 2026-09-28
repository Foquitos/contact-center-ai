#!/usr/bin/env python3
"""Calibra los umbrales del gate de audio mudo contra los audios reales del store.

Los defaults de config.py (AUDIO_MUDO_*) son conservadores y están puestos a ojo: lo
que hay que evitar es el falso positivo, o sea dejar sin auditar una llamada legítima.
Este script mide los audios que YA se conservaron (backend/storage/audios_auditoria/
<entorno>/) y muestra la distribución real, para poder mover los umbrales con datos en
vez de a intuición.

Correr en el servidor donde vive el store del entorno que se quiere medir (prod =
SRV01), desde backend/:

    python ../scripts/calibrar_audio_mudo.py --entorno prod --limite 300

Salida: la distribución de volumen medio y % de silencio, los 20 audios más silenciosos
(candidatos a mudos) y cuántos quedarían clasificados como mudo/sospechoso con los
umbrales vigentes.

No modifica nada: solo lee y mide.
"""
import argparse
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'backend'))

from AuditorIA import audio_calidad  # noqa: E402
from app.config import settings  # noqa: E402


def _percentil(valores, p):
    if not valores:
        return None
    orden = sorted(valores)
    k = max(0, min(len(orden) - 1, int(round((len(orden) - 1) * p))))
    return orden[k]


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--entorno', default=getattr(settings, 'ENVIRONMENT', 'prod'),
                        help="Subcarpeta del store a medir (prod/dev).")
    parser.add_argument('--limite', type=int, default=200,
                        help="Cuántos audios medir (muestra al azar). 0 = todos.")
    parser.add_argument('--semilla', type=int, default=42, help="Semilla de la muestra.")
    args = parser.parse_args()

    carpeta = os.path.join(settings.AUDIO_STORE_DIR, args.entorno)
    if not os.path.isdir(carpeta):
        print(f"No existe el store del entorno '{args.entorno}': {carpeta}")
        return 1

    archivos = [os.path.join(carpeta, f) for f in os.listdir(carpeta) if f.endswith('.ogg')]
    if not archivos:
        print(f"No hay audios conservados en {carpeta}.")
        return 1

    total_en_disco = len(archivos)
    if args.limite and len(archivos) > args.limite:
        random.seed(args.semilla)
        archivos = random.sample(archivos, args.limite)

    print(f"Midiendo {len(archivos)} de {total_en_disco} audios en {carpeta}...\n")

    medidos = []
    for i, ruta in enumerate(archivos, 1):
        analisis = audio_calidad.analizar(ruta)
        if analisis is None:
            continue
        medidos.append((os.path.basename(ruta), analisis))
        if i % 25 == 0:
            print(f"  ... {i}/{len(archivos)}", flush=True)

    if not medidos:
        print("No se pudo medir ningún audio (¿está ffmpeg instalado?).")
        return 1

    means = [a.mean_db for _, a in medidos if a.mean_db is not None]
    ratios = [a.ratio_silencio for _, a in medidos if a.ratio_silencio is not None]

    print(f"\n=== Volumen medio (dB) sobre {len(means)} audios ===")
    print(f"  mínimo {min(means):.1f} | p05 {_percentil(means, 0.05):.1f} | "
          f"mediana {statistics.median(means):.1f} | máximo {max(means):.1f}")
    print(f"=== % del audio en silencio sobre {len(ratios)} audios ===")
    print(f"  mediana {statistics.median(ratios) * 100:.0f}% | p95 {_percentil(ratios, 0.95) * 100:.0f}% | "
          f"máximo {max(ratios) * 100:.0f}%")

    print("\n=== Los 20 más silenciosos (candidatos a audio mudo) ===")
    peores = sorted(medidos, key=lambda x: (-(x[1].ratio_silencio or 0), x[1].mean_db or 0))[:20]
    for nombre, a in peores:
        print(f"  {nombre[:16]}...  {a.resumen()}")

    umbral_ratio = getattr(settings, 'AUDIO_MUDO_RATIO_SILENCIO', 0.98)
    umbral_mean = getattr(settings, 'AUDIO_MUDO_MEAN_DB', -50.0)
    umbral_sosp = getattr(settings, 'AUDIO_SOSPECHOSO_RATIO_SILENCIO', 0.90)

    mudos = sospechosos = 0
    for _, a in medidos:
        ratio, mean_db = a.ratio_silencio, a.mean_db
        if (mean_db is not None and mean_db <= umbral_mean) or (ratio is not None and ratio >= umbral_ratio):
            mudos += 1
        elif ratio is not None and ratio >= umbral_sosp:
            sospechosos += 1

    print(f"\n=== Con los umbrales vigentes (ratio>={umbral_ratio}, mean<={umbral_mean} dB) ===")
    print(f"  MUDOS (no se auditarían): {mudos} de {len(medidos)} ({mudos / len(medidos) * 100:.1f}%)")
    print(f"  sospechosos (se auditan marcados): {sospechosos} ({sospechosos / len(medidos) * 100:.1f}%)")
    print("\nSi el porcentaje de mudos parece alto, escuchá algunos de la lista de arriba")
    print("antes de mover nada: el umbral está para atrapar silencio, no llamadas cortas.")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
