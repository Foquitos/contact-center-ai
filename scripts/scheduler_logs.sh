#!/usr/bin/env bash
# Seguimiento de las corridas del scheduler en el journal.
#
# El scheduler escribe todos sus jobs al mismo stdout, así que `journalctl -u`
# a secas mezcla la corrida que falló con los ticks de las colas y con las otras
# auditorías que corrían en paralelo. Cada línea sale etiquetada como
# [job/corrida] (ver backend/app/scheduler_logging.py) y este script es el atajo
# para filtrar por esa etiqueta o por severidad sin acordarse de la sintaxis.
#
#   ./scheduler_logs.sh                      # warnings y errores de hoy
#   ./scheduler_logs.sh -f                   # ídem, en vivo
#   ./scheduler_logs.sh -a -f                # todo, en vivo (el journalctl -f de siempre)
#   ./scheduler_logs.sh -j chatbot_doc_jobs  # solo ese job, completo
#   ./scheduler_logs.sh -j 7f3a              # solo esa corrida (id de la etiqueta)
#   ./scheduler_logs.sh -e -j 7f3a           # de esa corrida, solo lo que salió mal
#   ./scheduler_logs.sh -a -s "2 hours ago"  # ventana de tiempo
#
# Sin -j se filtra a warnings/errores (buscás qué falló); con -j se muestra todo
# (ya sabés qué corrida mirar y querés su historia completa). -a y -e fuerzan
# una u otra cosa. -n limita las líneas, -u fija la unidad.
#
# La unidad se toma de $SCHEDULER_UNIT; si no está, se autodetecta.
set -euo pipefail

UNIDAD="${SCHEDULER_UNIT:-}"
DESDE="today"
SEGUIR=0
TODO=-1   # -1 = decidir según haya filtro o no
FILTRO=""
LINEAS=""

uso() {
    # El encabezado de este archivo es la ayuda: se imprime hasta la primera
    # línea que no sea comentario, así no hay dos textos que mantener.
    awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"
    exit "${1:-0}"
}

while getopts ":u:s:j:n:faeh" opcion; do
    case "$opcion" in
        u) UNIDAD="$OPTARG" ;;
        s) DESDE="$OPTARG" ;;
        j) FILTRO="$OPTARG" ;;
        n) LINEAS="$OPTARG" ;;
        f) SEGUIR=1 ;;
        a) TODO=1 ;;
        e) TODO=0 ;;
        h) uso 0 ;;
        *) echo "Opción inválida: -$OPTARG" >&2; uso 1 ;;
    esac
done

if [[ "$TODO" -eq -1 ]]; then
    # Filtrar por corrida Y por severidad a la vez deja afuera justo las líneas
    # INFO que cuentan qué venía haciendo el job antes de romperse.
    [[ -n "$FILTRO" ]] && TODO=1 || TODO=0
fi

if [[ -z "$UNIDAD" ]]; then
    # Autodetección para no hardcodear un nombre de unidad que cambia entre
    # SRV00 y SRV01. Si hay más de una candidata, gana la primera y siempre se
    # puede forzar con -u o SCHEDULER_UNIT.
    UNIDAD="$(systemctl list-units --type=service --all --no-legend '*sched*' 2>/dev/null \
                | awk '{print $1}' | head -n1)"
fi

if [[ -z "$UNIDAD" ]]; then
    echo "No pude detectar la unidad del scheduler." >&2
    echo "Pasala con -u <unidad> o exportá SCHEDULER_UNIT=<unidad>." >&2
    exit 1
fi

cmd=(journalctl -u "$UNIDAD" --since "$DESDE" -o short-precise)
severidad=""
if [[ "$TODO" -ne 1 ]]; then
    cmd+=(-p warning)
    severidad=" · solo warnings/errores"
fi
if [[ "$SEGUIR" -eq 1 ]]; then
    cmd+=(-f)
fi
if [[ -n "$LINEAS" ]]; then
    cmd+=(-n "$LINEAS")
fi

echo "── ${UNIDAD} · desde '${DESDE}'${severidad}${FILTRO:+ · filtro '$FILTRO'}" >&2

if [[ -n "$FILTRO" ]]; then
    # -A 30: el traceback de Python va en líneas propias, que no llevan la
    # etiqueta del job. Sin el contexto posterior el grep muestra el título del
    # error y esconde justamente la parte que explica el fallo.
    "${cmd[@]}" | grep --line-buffered -F -A 30 "$FILTRO"
else
    "${cmd[@]}"
fi
