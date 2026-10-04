#!/bin/sh
set -eu

BUNDLE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TARGET=${MONDAY_REPO:-"$HOME/thesis-monday-real-ev/iso15118-real-ev"}
MONDAY_HOME=${MONDAY_HOME:-"$HOME/thesis-monday-real-ev"}

if [ ! -d "$TARGET/iso15118/secc/controller" ]; then
    echo "ERROR: Josev Monday copy not found at $TARGET" >&2
    exit 1
fi

STAMP=$(date -u +%Y%m%d-%H%M%S)
BACKUP="$MONDAY_HOME/install-backup-$STAMP"
mkdir -p "$BACKUP"

backup_one() {
    destination=$1
    if [ -e "$destination" ]; then
        cp -a "$destination" "$BACKUP/"
    fi
}

backup_one "$TARGET/iso15118/secc/controller/monday_state.py"
backup_one "$TARGET/iso15118/secc/controller/monday_hardware.py"
backup_one "$TARGET/iso15118/secc/monday_main.py"
backup_one "$TARGET/compose.monday-real-ev.yml"
backup_one "$TARGET/.env.monday-real-ev"
backup_one "$MONDAY_HOME/evse_monday_console.py"
backup_one "$MONDAY_HOME/monday_status.py"

install -m 0644 "$BUNDLE_DIR/iso15118/secc/controller/monday_state.py" \
    "$TARGET/iso15118/secc/controller/monday_state.py"
install -m 0644 "$BUNDLE_DIR/iso15118/secc/controller/monday_hardware.py" \
    "$TARGET/iso15118/secc/controller/monday_hardware.py"
install -m 0644 "$BUNDLE_DIR/iso15118/secc/monday_main.py" \
    "$TARGET/iso15118/secc/monday_main.py"
install -m 0644 "$BUNDLE_DIR/compose.monday-real-ev.yml" \
    "$TARGET/compose.monday-real-ev.yml"
install -m 0600 "$BUNDLE_DIR/.env.monday-real-ev" \
    "$TARGET/.env.monday-real-ev"
install -m 0755 "$BUNDLE_DIR/evse_monday_console.py" \
    "$MONDAY_HOME/evse_monday_console.py"
install -m 0755 "$BUNDLE_DIR/monday_status.py" \
    "$MONDAY_HOME/monday_status.py"
install -m 0755 "$BUNDLE_DIR/verify_monday.sh" \
    "$TARGET/verify_monday.sh"
install -m 0644 "$BUNDLE_DIR/README_MONDAY.md" \
    "$MONDAY_HOME/README_MONDAY.md"

mkdir -p "$TARGET/tests"
install -m 0644 "$BUNDLE_DIR/tests/test_monday_state.py" \
    "$TARGET/tests/test_monday_state.py"
install -m 0644 "$BUNDLE_DIR/tests/test_monday_console.py" \
    "$TARGET/tests/test_monday_console.py"
install -m 0644 "$BUNDLE_DIR/tests/test_fail_safe_source.py" \
    "$TARGET/tests/test_fail_safe_source.py"

python3 -m py_compile \
    "$MONDAY_HOME/evse_monday_console.py" \
    "$MONDAY_HOME/monday_status.py" \
    "$TARGET/iso15118/secc/controller/monday_state.py" \
    "$TARGET/iso15118/secc/controller/monday_hardware.py" \
    "$TARGET/iso15118/secc/monday_main.py"

PYTHONPATH="$TARGET" python3 "$TARGET/tests/test_monday_state.py"
python3 "$TARGET/tests/test_monday_console.py"
python3 "$TARGET/tests/test_fail_safe_source.py"

echo "Installed only into the Monday copy: $TARGET"
echo "Original thesis-simulation directory was not touched."
echo "Backup of replaced Monday files: $BACKUP"
echo "Next: cd '$TARGET' && ./verify_monday.sh"
