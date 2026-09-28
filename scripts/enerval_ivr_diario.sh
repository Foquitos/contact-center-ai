#!/usr/bin/env bash
# ============================================================================
# Mantenimiento diario del informe IVR de Enerval.
#
# Pensado para cron a las 04:00: refresca los últimos días y deja la tabla
# dbo.[Voltara Enerval informe IVR] al día. Como la carga reemplaza el día completo,
# volver a pedir un día ya cargado no duplica nada: lo deja igual a lo que el
# portal dice ahora, así que las correcciones que Enerval hace sobre llamadas
# viejas entran solas.
#
#   DIAS_ATRAS=2 (default) -> anteayer + ayer + hoy
#   DIAS_ATRAS=1           -> ayer + hoy
#
# Por qué 2 y no 1: el portal a veces contesta 404 en un día que sí tiene
# llamadas y ese día queda anotado como VACIO. Pasó 3 veces en los 1.344 días
# del histórico. Pisar un día de más cada madrugada cuesta ~40 segundos y tapa
# ese agujero antes de que nadie lo note.
#
# El día de hoy siempre entra parcial (a las 04:00 van 4 horas de llamadas):
# se completa solo en la corrida de mañana, cuando ya es "ayer".
# ============================================================================
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/.venv/bin/python3"
[ -x "$PY" ] || PY="$REPO/venv/bin/python3"   # SRV01 (prod) usa venv/, no .venv/

DIAS_ATRAS="${DIAS_ATRAS:-2}"
ALCANCE="${ALCANCE:-completo}"

# cron corre con un entorno pelado: sin esto, el primer print con acento
# («días») revienta con UnicodeEncodeError y no queda ni el log.
export PYTHONIOENCODING=utf-8
export PYTHONUNBUFFERED=1
export LANG="${LANG:-C.UTF-8}"

LOG_DIR="$REPO/logs"
mkdir -p "$LOG_DIR"
exec >>"$LOG_DIR/enerval_ivr_diario.log" 2>&1

echo "=== arranca $(date '+%F %T') | ${DIAS_ATRAS} día(s) atrás | alcance $ALCANCE ==="
"$PY" "$REPO/scripts/enerval_informe_ivr.py" diario \
    --alcance "$ALCANCE" --dias-atras "$DIAS_ATRAS" "$@"
SALIDA=$?
echo "=== termina $(date '+%F %T') (código $SALIDA) ==="
exit $SALIDA
