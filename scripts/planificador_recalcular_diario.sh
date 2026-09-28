#!/usr/bin/env bash
# ============================================================================
# Recálculo diario automático del planificador (planificacion.Corrida).
#
# Pensado para cron dos veces por día (06:00 y 14:00):
#   - después del cierre del informe IVR de las 04:00
#   - después de la actualización meteorológica de 05:30 y 13:30
# Incluye sábados y domingos a propósito: evita que el plan del fin de semana
# quede congelado con el del viernes anterior.
# ============================================================================
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/.venv/bin/python3"
[ -x "$PY" ] || PY="$REPO/venv/bin/python3"

export PYTHONIOENCODING=utf-8
export PYTHONUNBUFFERED=1
export LANG="${LANG:-C.UTF-8}"

LOG_DIR="$REPO/logs"
mkdir -p "$LOG_DIR"
exec >>"$LOG_DIR/planificador_recalcular_diario.log" 2>&1

echo "=== arranca $(date '+%F %T') ==="
"$PY" "$REPO/scripts/planificador_recalcular_diario.py" "$@"
SALIDA=$?
echo "=== termina $(date '+%F %T') (código $SALIDA) ==="
exit $SALIDA
