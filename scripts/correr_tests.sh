#!/usr/bin/env bash
# Corre pytest del backend con el venv del repo, desde cualquier directorio.
# Los argumentos se pasan tal cual:
#   scripts/correr_tests.sh -m "not tokens"
#   scripts/correr_tests.sh tests/test_batch_cola.py -k claim
set -euo pipefail
raiz="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$raiz/backend"
# -q --tb=short -rfE: salida corta (una línea por falla/error) para que el digest sea barato.
exec "$raiz/.venv/bin/python" -m pytest -p no:cacheprovider -q --tb=short -rfE "$@"
