#!/usr/bin/env bash
# ============================================================================
# Backfill nocturno del informe IVR de Enerval.
#
# Pensado para cron: la cuenta del portal la usan personas durante el día y
# admite una sola sesión, así que esto arranca de noche y corta solo a la hora
# indicada (--hasta-las), devolviendo la cuenta. El avance queda anotado día por
# día en SQL, así que si no termina, la corrida siguiente sigue donde quedó.
#
# Uso:
#   ./enerval_ivr_nocturno.sh                 # 2023-01-01 .. ayer, corta 08:00
#   CORTE=06:00 ./enerval_ivr_nocturno.sh     # mismo rango, corta a las 06:00
#   DESDE=2026-08-21 HASTA=2026-08-21 ALCANCE=propio ./enerval_ivr_nocturno.sh
#
# Deja el log en logs/enerval_ivr_<fecha>_<hora>.log (logs/ está en .gitignore).
# ============================================================================
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/.venv/bin/python3"
[ -x "$PY" ] || PY="$REPO/venv/bin/python3"   # SRV01 (prod) usa venv/, no .venv/

DESDE="${DESDE:-2023-01-01}"
HASTA="${HASTA:-$(date -d yesterday +%F)}"   # hoy lo mantiene el modo 'diario'
ALCANCE="${ALCANCE:-completo}"
CORTE="${CORTE:-08:00}"

# cron corre con un entorno pelado: sin esto, el primer print con acento
# («días») revienta con UnicodeEncodeError y no queda ni el log.
export PYTHONIOENCODING=utf-8
export PYTHONUNBUFFERED=1
export LANG="${LANG:-C.UTF-8}"

LOG_DIR="$REPO/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/enerval_ivr_$(date +%Y-%m-%d_%H%M).log"
exec >>"$LOG" 2>&1

echo "=== arranca $(date '+%F %T') | $DESDE .. $HASTA | alcance $ALCANCE | corta $CORTE ==="
"$PY" "$REPO/scripts/enerval_informe_ivr.py" historico \
    --desde "$DESDE" --hasta "$HASTA" --alcance "$ALCANCE" --hasta-las "$CORTE" "$@"
SALIDA=$?
echo "=== termina $(date '+%F %T') (código $SALIDA) ==="
exit $SALIDA
