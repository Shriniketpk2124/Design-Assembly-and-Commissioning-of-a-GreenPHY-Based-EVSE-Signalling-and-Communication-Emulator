#!/usr/bin/env bash
set -Eeuo pipefail

# Install the public project additions into a separate checkout of the recorded
# EcoG iso15118 revision. Local configuration files are required explicitly;
# the installer never creates operational settings from public placeholders.

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)"
JOSEV_SOURCE="$PROJECT_ROOT/software/josev-modifications"
CONFIG_SOURCE="$PROJECT_ROOT/software/configuration"

MONDAY_HOME="${MONDAY_HOME:-$HOME/thesis-monday-real-ev}"
TARGET="${MONDAY_REPO:-$MONDAY_HOME/iso15118-real-ev}"
LOCAL_ENV_SOURCE="${MONDAY_ENV_FILE:-$CONFIG_SOURCE/.env.monday-real-ev}"
OPS_ENV_SOURCE="${MONDAY_OPS_ENV_FILE:-$CONFIG_SOURCE/monday-ops.env}"
SLAC_SOURCE="${MONDAY_SLAC_PROFILE:-$CONFIG_SOURCE/slac/evse-monday.ini}"
EXPECTED_BASE="76bf85be572d16adbcd009a985d605622c3b7227"

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

require_file() {
    [ -f "$1" ] || die "required file is missing: $1"
}

require_configured_file() {
    require_file "$1"
    if grep -Eq '=[[:space:]]*CHANGE_ME[[:space:]]*$' "$1"; then
        die "replace every CHANGE_ME value before installation: $1"
    fi
}

if [ ! -d "$TARGET/iso15118/secc/controller" ]; then
    die "compatible EcoG iso15118 checkout not found at $TARGET"
fi

if command -v git >/dev/null 2>&1 && git -C "$TARGET" rev-parse HEAD >/dev/null 2>&1; then
    ACTUAL_BASE="$(git -C "$TARGET" rev-parse HEAD)"
    if [ "$ACTUAL_BASE" != "$EXPECTED_BASE" ]; then
        die "upstream checkout is $ACTUAL_BASE; expected $EXPECTED_BASE"
    fi
else
    die "cannot verify the upstream Git revision at $TARGET"
fi

for source in \
    "$JOSEV_SOURCE/iso15118/secc/controller/monday_state.py" \
    "$JOSEV_SOURCE/iso15118/secc/controller/monday_hardware.py" \
    "$JOSEV_SOURCE/iso15118/secc/monday_main.py" \
    "$CONFIG_SOURCE/compose.monday-real-ev.yml" \
    "$SCRIPT_DIR/evse_monday_console.py" \
    "$SCRIPT_DIR/monday_status.py" \
    "$SCRIPT_DIR/monday_ops.sh" \
    "$SCRIPT_DIR/verify_monday.sh" \
    "$SCRIPT_DIR/README.md" \
    "$SCRIPT_DIR/tests/test_monday_state.py" \
    "$SCRIPT_DIR/tests/test_monday_console.py" \
    "$SCRIPT_DIR/tests/test_fail_safe_source.py"
do
    require_file "$source"
done

require_configured_file "$LOCAL_ENV_SOURCE"
require_configured_file "$OPS_ENV_SOURCE"
require_configured_file "$SLAC_SOURCE"

STAMP="$(date -u +%Y%m%d-%H%M%S)"
BACKUP="$MONDAY_HOME/install-backup-$STAMP"
mkdir -p "$BACKUP" "$MONDAY_HOME/slac" "$TARGET/tests"

backup_one() {
    source_path=$1
    backup_name=$2
    if [ -e "$source_path" ]; then
        mkdir -p "$BACKUP/$(dirname -- "$backup_name")"
        cp -a "$source_path" "$BACKUP/$backup_name"
    fi
}

backup_one "$TARGET/iso15118/secc/controller/monday_state.py" \
    "iso15118/secc/controller/monday_state.py"
backup_one "$TARGET/iso15118/secc/controller/monday_hardware.py" \
    "iso15118/secc/controller/monday_hardware.py"
backup_one "$TARGET/iso15118/secc/monday_main.py" \
    "iso15118/secc/monday_main.py"
backup_one "$TARGET/compose.monday-real-ev.yml" "compose.monday-real-ev.yml"
backup_one "$TARGET/.env.monday-real-ev" ".env.monday-real-ev"
backup_one "$MONDAY_HOME/evse_monday_console.py" "evse_monday_console.py"
backup_one "$MONDAY_HOME/monday_status.py" "monday_status.py"
backup_one "$MONDAY_HOME/monday_ops.sh" "monday_ops.sh"
backup_one "$MONDAY_HOME/verify_monday.sh" "verify_monday.sh"
backup_one "$MONDAY_HOME/monday-ops.env" "monday-ops.env"
backup_one "$MONDAY_HOME/slac/evse-monday.ini" "slac/evse-monday.ini"

install -m 0644 \
    "$JOSEV_SOURCE/iso15118/secc/controller/monday_state.py" \
    "$TARGET/iso15118/secc/controller/monday_state.py"
install -m 0644 \
    "$JOSEV_SOURCE/iso15118/secc/controller/monday_hardware.py" \
    "$TARGET/iso15118/secc/controller/monday_hardware.py"
install -m 0644 \
    "$JOSEV_SOURCE/iso15118/secc/monday_main.py" \
    "$TARGET/iso15118/secc/monday_main.py"
install -m 0644 "$CONFIG_SOURCE/compose.monday-real-ev.yml" \
    "$TARGET/compose.monday-real-ev.yml"
install -m 0600 "$LOCAL_ENV_SOURCE" "$TARGET/.env.monday-real-ev"

install -m 0755 "$SCRIPT_DIR/evse_monday_console.py" \
    "$MONDAY_HOME/evse_monday_console.py"
install -m 0755 "$SCRIPT_DIR/monday_status.py" \
    "$MONDAY_HOME/monday_status.py"
install -m 0755 "$SCRIPT_DIR/monday_ops.sh" "$MONDAY_HOME/monday_ops.sh"
install -m 0755 "$SCRIPT_DIR/verify_monday.sh" "$MONDAY_HOME/verify_monday.sh"
install -m 0644 "$SCRIPT_DIR/README.md" "$MONDAY_HOME/README.md"
install -m 0600 "$OPS_ENV_SOURCE" "$MONDAY_HOME/monday-ops.env"
install -m 0600 "$SLAC_SOURCE" "$MONDAY_HOME/slac/evse-monday.ini"

install -m 0644 "$SCRIPT_DIR/tests/test_monday_state.py" \
    "$TARGET/tests/test_monday_state.py"
install -m 0644 "$SCRIPT_DIR/tests/test_monday_console.py" \
    "$TARGET/tests/test_monday_console.py"
install -m 0644 "$SCRIPT_DIR/tests/test_fail_safe_source.py" \
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

printf 'Installed project files into: %s\n' "$TARGET"
printf 'Installed host tools into: %s\n' "$MONDAY_HOME"
printf 'Backup of replaced files: %s\n' "$BACKUP"
printf '\nNext commands:\n'
printf '  set -a\n'
printf '  . "%s/monday-ops.env"\n' "$MONDAY_HOME"
printf '  set +a\n'
printf '  "%s/verify_monday.sh"\n' "$MONDAY_HOME"
