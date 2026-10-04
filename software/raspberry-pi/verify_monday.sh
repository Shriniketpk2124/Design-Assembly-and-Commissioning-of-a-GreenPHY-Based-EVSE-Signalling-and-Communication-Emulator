#!/usr/bin/env bash
set -Eeuo pipefail

# Compatibility entry point. The integrated operations preflight is the single
# source of truth for the final plc-utils-based test workflow.

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

if [ -x "$SCRIPT_DIR/monday_ops.sh" ]; then
    OPS="$SCRIPT_DIR/monday_ops.sh"
elif [ -x "$SCRIPT_DIR/../monday_ops.sh" ]; then
    OPS="$SCRIPT_DIR/../monday_ops.sh"
else
    printf '[FAIL] monday_ops.sh was not found beside or above %s\n' \
        "$SCRIPT_DIR" >&2
    exit 1
fi

exec "$OPS" preflight
