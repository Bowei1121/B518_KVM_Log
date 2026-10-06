#!/usr/bin/env bash
# Core Service Management Utility for macOS/Linux TE operators
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${DIR}/../.venv/bin/python"
if [ ! -f "$PYTHON" ]; then
    PYTHON="python3"
fi
exec "$PYTHON" "$DIR/manage_core.py" "$@"
