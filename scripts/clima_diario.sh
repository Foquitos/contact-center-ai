#!/usr/bin/env bash
# ============================================================================
# Clima del área de concesión para el planificador (planificacion.Clima).
#
# Pensado para cron dos veces por día (05:30 y 13:30): cierra con dato
# observado los días que ya pasaron y trae el pronóstico de los próximos 16.
# Sin esto la tabla se congela y el planificador corrige el nivel contra
# pronósticos viejos: pasó entre el 04 y el 13/09/2026 (la última carga era del
# 08/09) y el sábado 12/09 se pronosticó 5.670 contra 3.474 reales; con el
# clima al día el mismo modelo daba 3.937.
#
# Pide 45 días hacia atrás: el archivo de Open-Meteo corrige días recientes y
# volver a pedirlos no duplica nada (la carga pisa por fecha).
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
exec >>"$LOG_DIR/clima_diario.log" 2>&1

DESDE="$(date -d '45 days ago' +%F)"
echo "=== arranca $(date '+%F %T') | desde $DESDE ==="
"$PY" "$REPO/scripts/clima_voltara.py" --todas --desde "$DESDE" "$@"
SALIDA=$?
echo "=== termina $(date '+%F %T') (código $SALIDA) ==="
exit $SALIDA
