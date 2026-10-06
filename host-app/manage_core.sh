#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${DIR}/../.venv/bin/python"
if [ ! -f "$PYTHON" ]; then
    PYTHON="python3"
fi
exec "$PYTHON" "$DIR/../tools/manage_core.py" "$@"
