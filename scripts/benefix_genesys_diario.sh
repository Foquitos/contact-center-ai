#!/usr/bin/env bash
# ============================================================================
# Llamados de Benefix (Genesys Cloud) -> Benefix.Interacciones.
#
# Pensado para cron cada hora (p. ej. `15 * * * *`): recarga ayer y hoy, así lo
# atendido hasta la hora anterior ya se puede auditar, y los wrap-ups que se
# cargan tarde o los llamados que seguían en curso entran en la corrida siguiente.
# La carga reemplaza el día completo: volver a pedirlo no duplica nada.
# Una corrida son ~10 s (login + catálogos + 2 días de ~400 conversaciones).
#
#   DIAS_ATRAS=1 (default) -> ayer + hoy
# ============================================================================
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/.venv/bin/python3"
[ -x "$PY" ] || PY="$REPO/venv/bin/python3"   # SRV01 (prod) usa venv/, no .venv/

DIAS_ATRAS="${DIAS_ATRAS:-1}"

# cron corre con un entorno pelado: sin esto, el primer print con acento revienta
# con UnicodeEncodeError y no queda ni el log.
export PYTHONIOENCODING=utf-8
export PYTHONUNBUFFERED=1
export LANG="${LANG:-C.UTF-8}"

LOG_DIR="$REPO/logs"
mkdir -p "$LOG_DIR"
exec >>"$LOG_DIR/benefix_genesys.log" 2>&1

echo "=== arranca $(date '+%F %T') | ${DIAS_ATRAS} día(s) atrás ==="
"$PY" "$REPO/scripts/benefix_genesys.py" diario --dias-atras "$DIAS_ATRAS" "$@"
SALIDA=$?
echo "=== termina $(date '+%F %T') (código $SALIDA) ==="
exit $SALIDA
