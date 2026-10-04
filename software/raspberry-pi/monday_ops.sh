#!/usr/bin/env bash
set -Eeuo pipefail

# Operational wrapper for the communication-only Monday EV test.
# This file intentionally never issues A, HLC, PWM, DISABLE, or RESET.
# Those state changes remain deliberate operator actions in the Monday console.

OWNER="${SUDO_USER:-${USER:-$(id -un)}}"
OWNER_HOME="$(getent passwd "$OWNER" 2>/dev/null | awk -F: '{print $6}')"
[ -n "$OWNER_HOME" ] || OWNER_HOME="${HOME:-/home/$OWNER}"
MONDAY_HOME="${MONDAY_HOME:-$OWNER_HOME/thesis-monday-real-ev}"
REPO="${MONDAY_REPO:-$MONDAY_HOME/iso15118-real-ev}"
COMPOSE_FILE="$REPO/compose.monday-real-ev.yml"
PROFILE="$MONDAY_HOME/slac/evse-monday.ini"
CONSOLE="$MONDAY_HOME/evse_monday_console.py"
STATUS_TOOL="$MONDAY_HOME/monday_status.py"
RUNTIME_DIR="/run/evse-monday"
RUNS_DIR="$MONDAY_HOME/runs"
CURRENT_LINK="$MONDAY_HOME/current-run"
LAST_LINK="$MONDAY_HOME/last-run"
DEVOLO_MAC="${DEVOLO_MAC:-CHANGE_ME}"
PROFILE_SHA256="${MONDAY_PROFILE_SHA256:-CHANGE_ME}"
SELF="$(readlink -f "$0")"
TMUX_SESSION="${MONDAY_TMUX_SESSION:-monday-ev}"

OWNER_GROUP="$(id -gn "$OWNER" 2>/dev/null || printf '%s' "$OWNER")"

say() { printf '%s\n' "$*"; }
ok() { printf '[OK] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
die() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: monday_ops.sh COMMAND

Commands:
  preflight     Read-only readiness checks; requires a clean stopped session
  start         Create a run folder, start packet capture, Redis and Josev SECC
  test-day      Run start, then open the single-terminal cockpit
  cockpit       Open/reconnect the four-pane single-terminal cockpit
  console       Run and log the interactive Monday Arduino/GPIO console
  slac          Run and log the sole plc-utils EVSE SLAC owner
  monitor       Watch and log the decoded CP state
  secc-logs     Follow and log Josev SECC output
  status        Show current processes, sockets, CP snapshot and PLC table
  diagnose      Read-only layer-by-layer failure localization
  snapshot      Save a timestamped diagnostic snapshot in the active run
  stop          Request a latched fail-safe STOP and verify relay command OFF
  collect       Copy current logs, state and diagnostics into the active run
  shutdown      Require/command safe state, collect evidence, stop all services
  paths         Print the active and most recent run folders
  help          Show this help

No command in this script produces A, HLC or PWM. Use those only in the
interactive Monday console after checking the physical setup and CP state.
EOF
}

require_file() {
    [ -f "$1" ] || die "Required file is missing: $1"
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "Required command is missing: $1"
}

active_run() {
    [ -L "$CURRENT_LINK" ] || die "No active run. Use: $0 start"
    local resolved
    resolved="$(readlink -f "$CURRENT_LINK")"
    [ -d "$resolved" ] || die "Active-run link is invalid: $CURRENT_LINK"
    printf '%s\n' "$resolved"
}

compose() {
    sudo docker compose -f "$COMPOSE_FILE" "$@"
}

evse_count() {
    { sudo pgrep -x evse 2>/dev/null || true; } | wc -l | tr -d ' '
}

console_count() {
    { pgrep -f '[e]vse_monday_console.py' 2>/dev/null || true; } | wc -l | tr -d ' '
}

capture_running() {
    sudo test -r "$RUNTIME_DIR/tcpdump.pid" || return 1
    local pid
    pid="$(sudo cat "$RUNTIME_DIR/tcpdump.pid" 2>/dev/null || true)"
    [ -n "$pid" ] && sudo kill -0 "$pid" 2>/dev/null
}

state_safe() {
    sudo python3 - "$RUNTIME_DIR/state.json" <<'PY'
import json
import sys
from pathlib import Path

try:
    state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(1)

safe = (
    state.get("relay_command") == "OFF"
    and state.get("remote") == "OFF"
    and state.get("cp_mode") == "A_POSITIVE_DC"
)
raise SystemExit(0 if safe else 1)
PY
}

write_stop_request() {
    local reason="$1"
    sudo env STOP_REASON="$reason" RUNTIME_DIR="$RUNTIME_DIR" python3 - <<'PY'
import json
import os
import tempfile
import time
from pathlib import Path

runtime = Path(os.environ["RUNTIME_DIR"])
runtime.mkdir(mode=0o770, parents=True, exist_ok=True)
target = runtime / "command.json"
payload = {
    "schema": 1,
    "command": "STOP",
    "created_unix_ms": time.time_ns() // 1_000_000,
    "reason": os.environ["STOP_REASON"],
}
fd, temporary = tempfile.mkstemp(prefix="command.json.", suffix=".tmp", dir=runtime)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o660)
    os.replace(temporary, target)
except Exception:
    try:
        os.unlink(temporary)
    except OSError:
        pass
    raise
PY
}

wait_for_safe_state() {
    local seconds="${1:-8}"
    local i
    for ((i = 0; i < seconds * 2; i++)); do
        if state_safe; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

do_preflight() {
    local failures=0

    say "----- MONDAY COMMUNICATION TEST PREFLIGHT -----"
    sudo -v

    for item in "$REPO" "$COMPOSE_FILE" "$PROFILE" "$CONSOLE" "$STATUS_TOOL"; do
        if [ -e "$item" ]; then
            ok "found $item"
        else
            printf '[FAIL] missing %s\n' "$item" >&2
            failures=$((failures + 1))
        fi
    done

    for cmd in docker evse plctool plcstat tcpdump ip ss script stdbuf sha256sum pinctrl tmux; do
        if command -v "$cmd" >/dev/null 2>&1; then
            ok "$cmd is installed"
        else
            printf '[FAIL] required command is missing: %s\n' "$cmd" >&2
            failures=$((failures + 1))
        fi
    done

    if ip link show eth0 >/dev/null 2>&1; then
        ok "eth0 exists"
    else
        printf '[FAIL] eth0 does not exist\n' >&2
        failures=$((failures + 1))
    fi

    if [ -r /sys/class/net/eth0/carrier ] && [ "$(cat /sys/class/net/eth0/carrier)" = 1 ]; then
        ok "eth0 has carrier"
    else
        printf '[FAIL] eth0 has no carrier; check Pi-Devolo Ethernet and Devolo power\n' >&2
        failures=$((failures + 1))
    fi

    if ip -6 addr show dev eth0 scope link 2>/dev/null | grep -q 'inet6 fe80:'; then
        ok "eth0 has an IPv6 link-local address"
    else
        printf '[FAIL] eth0 has no IPv6 link-local address\n' >&2
        failures=$((failures + 1))
    fi

    if [ -c /dev/ttyUSB0 ]; then
        ok "/dev/ttyUSB0 is present"
    else
        printf '[FAIL] /dev/ttyUSB0 is missing; check the Arduino USB connection\n' >&2
        failures=$((failures + 1))
    fi

    if command -v fuser >/dev/null 2>&1 && \
       sudo fuser /dev/ttyUSB0 >/dev/null 2>&1; then
        printf '[FAIL] /dev/ttyUSB0 is already open in another process\n' >&2
        sudo fuser -v /dev/ttyUSB0 >&2 || true
        failures=$((failures + 1))
    else
        ok "Arduino serial port is not already owned"
    fi

    if sudo plctool -i eth0 -r "$DEVOLO_MAC" 2>&1 | tee /tmp/monday-plctool-preflight.txt | grep -q 'QCA7000'; then
        ok "Devolo QCA7000 $DEVOLO_MAC responds"
    else
        printf '[FAIL] Devolo QCA7000 did not respond\n' >&2
        failures=$((failures + 1))
    fi

    if [ -f "$PROFILE" ]; then
        local actual_profile_sha
        actual_profile_sha="$(sha256sum "$PROFILE" | awk '{print $1}')"
        if [ "$actual_profile_sha" = "$PROFILE_SHA256" ]; then
            ok "SLAC profile checksum matches the tested profile"
        else
            printf '[FAIL] SLAC profile checksum changed: %s\n' "$actual_profile_sha" >&2
            failures=$((failures + 1))
        fi
    fi

    if [ -f "$COMPOSE_FILE" ] && compose config --quiet; then
        ok "Monday Compose file is valid"
    else
        printf '[FAIL] Monday Compose validation failed\n' >&2
        failures=$((failures + 1))
    fi

    if sudo docker image inspect iso15118-secc:latest >/dev/null 2>&1; then
        ok "iso15118-secc:latest exists"
    else
        printf '[FAIL] iso15118-secc:latest is missing\n' >&2
        failures=$((failures + 1))
    fi
    if sudo docker image inspect redis:6.2.6-alpine >/dev/null 2>&1; then
        ok "redis:6.2.6-alpine exists"
    else
        printf '[FAIL] redis:6.2.6-alpine is missing\n' >&2
        failures=$((failures + 1))
    fi

    if sudo docker ps --format '{{.Names}} {{.Image}}' | grep -qi 'evcc'; then
        printf '[FAIL] a local EVCC container is running; the real EV must be the only EVCC\n' >&2
        failures=$((failures + 1))
    else
        ok "no local EVCC container is running"
    fi

    if pgrep -af 'pyslac|single_slac_session' >/dev/null 2>&1; then
        printf '[FAIL] another SLAC implementation is running:\n' >&2
        pgrep -af 'pyslac|single_slac_session' >&2 || true
        failures=$((failures + 1))
    else
        ok "no pyslac process is running"
    fi

    local ec cc
    ec="$(evse_count)"
    cc="$(console_count)"
    if [ "$ec" = 0 ]; then
        ok "no old plc-utils evse process is running"
    else
        printf '[FAIL] %s plc-utils evse process(es) already running\n' "$ec" >&2
        failures=$((failures + 1))
    fi
    if [ "$cc" = 0 ]; then
        ok "no old Monday console process is running"
    else
        printf '[FAIL] %s Monday console process(es) already running\n' "$cc" >&2
        failures=$((failures + 1))
    fi

    if sudo docker ps --format '{{.Names}}' | grep -q '^monday-ev-'; then
        printf '[FAIL] Monday containers are already running; use status or shutdown first\n' >&2
        failures=$((failures + 1))
    else
        ok "Monday containers are stopped"
    fi

    if capture_running; then
        printf '[FAIL] a Monday tcpdump capture is already running\n' >&2
        failures=$((failures + 1))
    else
        ok "no old Monday packet capture is running"
    fi

    if sudo ss -lunp 2>/dev/null | grep -q ':15118'; then
        printf '[FAIL] UDP port 15118 is already in use\n' >&2
        sudo ss -lunp 2>/dev/null | grep ':15118' >&2 || true
        failures=$((failures + 1))
    else
        ok "UDP port 15118 is free"
    fi

    if sudo ss -lntp 2>/dev/null | grep -q ':6379'; then
        printf '[FAIL] TCP port 6379 is already in use\n' >&2
        sudo ss -lntp 2>/dev/null | grep ':6379' >&2 || true
        failures=$((failures + 1))
    else
        ok "TCP port 6379 is free"
    fi

    if [ "$failures" -ne 0 ]; then
        die "preflight found $failures problem(s); do not connect the EV"
    fi

    say
    ok "software/network preflight passed"
    say "MANUAL CHECK STILL REQUIRED: HV/DC absent and wiring unpowered before changes."
    say "REAL EV: breadboard loads removed; JP2 open; J3-J1 and CP/PE checked."
    say "BENCH REHEARSAL: EV and Devolo J3 disconnected; only the approved CP load fitted."
}

write_metadata() {
    local run="$1"
    {
        printf 'run_started_local=%s\n' "$(date --iso-8601=seconds)"
        printf 'run_started_utc=%s\n' "$(date -u --iso-8601=seconds)"
        printf 'hostname=%s\n' "$(hostname)"
        printf 'kernel=%s\n' "$(uname -srmo)"
        printf 'operator=%s\n' "$OWNER"
        printf 'repo=%s\n' "$REPO"
        printf 'devolo_mac=%s\n' "$DEVOLO_MAC"
        printf 'slac_profile_sha256=%s\n' "$(sha256sum "$PROFILE" | awk '{print $1}')"
        printf '\n----- IP LINKS -----\n'
        ip -br link
        printf '\n----- ETH0 ADDRESSES -----\n'
        ip addr show dev eth0
        printf '\n----- PLC VERSION -----\n'
        sudo plctool -i eth0 -r "$DEVOLO_MAC"
        printf '\n----- PLC TABLE BEFORE EV -----\n'
        sudo plcstat -t -i eth0
        printf '\n----- COMPOSE SERVICES -----\n'
        compose config --services
    } >"$run/run-metadata.txt" 2>&1
}

start_capture() {
    local run="$1"
    sudo install -d -m 0770 "$RUNTIME_DIR"
    sudo rm -f "$RUNTIME_DIR/tcpdump.pid"
    sudo sh -c '
        nohup tcpdump -i eth0 -nn -e -s0 -U \
          -w "$1" "(ether proto 0x88e1) or ip6" \
          >"$2" 2>&1 &
        echo $! >"$3"
    ' sh "$run/traffic.pcap" "$run/tcpdump.log" "$RUNTIME_DIR/tcpdump.pid"
    sleep 1
    capture_running || die "tcpdump did not remain running"
    ok "packet capture started: $run/traffic.pcap"
}

do_start() {
    do_preflight

    mkdir -p "$RUNS_DIR"
    local stamp run
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    run="$RUNS_DIR/$stamp"
    mkdir -p "$run/snapshots"
    ln -sfnT "$run" "$CURRENT_LINK"
    write_metadata "$run"
    start_capture "$run"

    say "----- STARTING REDIS + JOSEV SECC -----"
    if ! compose up -d; then
        warn "Compose failed; stopping packet capture"
        stop_capture
        exit 1
    fi

    local ready=0 i
    for ((i = 0; i < 45; i++)); do
        if sudo ss -lunp 2>/dev/null | grep -q ':15118'; then
            ready=1
            break
        fi
        sleep 1
    done
    if [ "$ready" -ne 1 ]; then
        compose logs --no-color --tail=200 secc >"$run/secc-startup-failure.log" 2>&1 || true
        warn "Josev SDP socket did not appear; see $run/secc-startup-failure.log"
        compose down || true
        stop_capture
        exit 1
    fi

    compose ps | tee "$run/compose-start.txt"
    ok "Josev SDP is listening on UDP 15118"
    say
    say "Active run: $run"
    say "Next: $0 cockpit"
    say "This opens console, CP monitor, SLAC and Josev logs in one SSH terminal."
}

do_diagnose() {
    local run="" source_label="active"
    if [ -L "$CURRENT_LINK" ]; then
        run="$(readlink -f "$CURRENT_LINK")"
    elif [ -L "$LAST_LINK" ]; then
        run="$(readlink -f "$LAST_LINK")"
        source_label="last completed"
    fi

    say "----- MONDAY LAYER DIAGNOSIS (READ ONLY) -----"
    say "Evidence source: ${run:-none} ($source_label run)"

    local carrier="NO" devolo="NO" console_live="NO" slac_live="NO"
    local sdp_live="NO" capture_live="NO" station_count=0
    [ -r /sys/class/net/eth0/carrier ] && [ "$(cat /sys/class/net/eth0/carrier)" = 1 ] && carrier="YES"
    sudo plctool -i eth0 -r "$DEVOLO_MAC" 2>&1 | grep -q QCA7000 && devolo="YES"
    [ "$(console_count)" -gt 0 ] && console_live="YES"
    [ "$(evse_count)" -gt 0 ] && slac_live="YES"
    sudo ss -lunp 2>/dev/null | grep -q ':15118' && sdp_live="YES"
    capture_running && capture_live="YES"
    station_count="$({ sudo plctool -m -i eth0 2>/dev/null || true; } | \
        awk -F= '/network->STATIONS/{gsub(/[[:space:]]/, "", $2); if ($2+0>m) m=$2+0} END{print m+0}')"

    printf 'Foundation: eth0_carrier=%s devolo=%s console=%s slac=%s sdp=%s capture=%s remote_plc_stations=%s\n' \
        "$carrier" "$devolo" "$console_live" "$slac_live" "$sdp_live" "$capture_live" "$station_count"

    local state_present="NO" running="" relay="" remote="" mode="" duty=""
    local pair="" stable="" fault="" stop_latch="" age_ms="" decoded=""
    if sudo test -f "$RUNTIME_DIR/state.json"; then
        state_present="YES"
        local fields
        fields="$(sudo python3 - "$RUNTIME_DIR/state.json" <<'PY'
import json
import sys
import time
from pathlib import Path

try:
    state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception:
    print("INVALID\t\t\t\t\t\t\t\t")
    raise SystemExit(0)

updated = state.get("updated_unix_ms")
age = ""
if isinstance(updated, (int, float)):
    age = str(max(0, int(time.time() * 1000 - updated)))
values = [
    state.get("running", ""), state.get("relay_command", ""),
    state.get("remote", ""), state.get("cp_mode", ""),
    state.get("cp_positive_duty", ""), state.get("fb_pair", ""),
    state.get("fb_stable", ""), state.get("fault", ""),
    state.get("external_stop_latched", ""), age,
]
print("\t".join(str(value) for value in values))
PY
)"
        IFS=$'\t' read -r running relay remote mode duty pair stable fault stop_latch age_ms <<<"$fields"
        decoded="$(sudo python3 "$STATUS_TOOL" 2>/dev/null || true)"
    fi

    printf 'CP state: present=%s running=%s relay=%s remote=%s mode=%s duty=%s pair=%s stable=%s fault=%s stop_latch=%s age_ms=%s\n' \
        "$state_present" "$running" "$relay" "$remote" "$mode" "$duty" "$pair" "$stable" "$fault" "$stop_latch" "$age_ms"
    [ -z "$decoded" ] || say "$decoded"

    local plc_frames=0 sdp_frames=0 tcp_frames=0 pcap=""
    if [ -n "$run" ] && [ -f "$run/traffic.pcap" ]; then
        pcap="$run/traffic.pcap"
        plc_frames="$({ sudo tcpdump -nn -r "$pcap" 'ether proto 0x88e1' 2>/dev/null || true; } | wc -l | tr -d ' ')"
        sdp_frames="$({ sudo tcpdump -nn -r "$pcap" 'ip6 and udp port 15118' 2>/dev/null || true; } | wc -l | tr -d ' ')"
        tcp_frames="$({ sudo tcpdump -nn -r "$pcap" 'ip6 and tcp' 2>/dev/null || true; } | wc -l | tr -d ' ')"
    fi
    printf 'Captured traffic: homeplug=%s sdp_udp=%s ipv6_tcp=%s\n' \
        "$plc_frames" "$sdp_frames" "$tcp_frames"

    local secc_text="" app_markers=0 secc_errors=0
    if sudo docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^monday-ev-secc-1$'; then
        secc_text="$(compose logs --no-color secc 2>/dev/null || true)"
    elif [ -n "$run" ] && [ -f "$run/final/josev-secc.log" ]; then
        secc_text="$(cat "$run/final/josev-secc.log")"
    fi
    if [ -n "$secc_text" ]; then
        app_markers="$(printf '%s\n' "$secc_text" | grep -Eic \
            'SupportedAppProtocol|SessionSetup|CableCheck|PowerDelivery|CurrentDemand|V2GTP|TCP.*(connect|client)' || true)"
        secc_errors="$(printf '%s\n' "$secc_text" | grep -Eic \
            'ERROR|CRITICAL|Traceback|Exception' || true)"
    fi
    printf 'Josev evidence: application_markers=%s error_markers=%s\n' \
        "$app_markers" "$secc_errors"

    say
    say "----- LOCALIZATION -----"
    if [ "$carrier" != YES ]; then
        say "[BLOCKED: ETHERNET] eth0 has no carrier. Check Pi-Devolo Ethernet and Devolo power."
    elif [ "$devolo" != YES ]; then
        say "[BLOCKED: DEVOLO] Ethernet carrier exists, but the local QCA7000 does not answer."
    elif [ "$source_label" = active ] && [ "$console_live" != YES ]; then
        say "[BLOCKED: CONTROL] The Monday console is not running; CP/relay state is not live."
    elif [ "$state_present" != YES ] || { [ "$running" != True ] && [ "$running" != true ]; }; then
        say "[BLOCKED: CONTROL] No live controller state is available."
    elif [ "$fault" != NO ] || [ "$stable" != YES ]; then
        say "[BLOCKED: CP FEEDBACK] Controller fault or unstable comparator feedback. Keep CP stopped."
    elif [ "$source_label" = active ] && [ "$sdp_live" != YES ]; then
        say "[BLOCKED: JOSEV STARTUP] UDP 15118 is not listening on eth0."
    elif [ "$source_label" = active ] && [ "$slac_live" != YES ]; then
        say "[BLOCKED: SLAC OWNER] The plc-utils EVSE listener is not running."
    elif [ "$relay" = OFF ]; then
        say "[SAFE/IDLE] CP path relays are open. Review any STOP latch before RESET."
    elif [ "$mode" = A_POSITIVE_DC ] && [ "$pair" = 00 ]; then
        say "[CP A1] Waiting for the EV to produce stable State B."
        say "If the EV is connected and this persists, inspect CP/PE connection and cable seating."
    elif [ "$mode" = A_POSITIVE_DC ] && [ "$pair" = 10 ]; then
        say "[CP B1] EV detection succeeded. The operator may issue HLC once, after confirming stability."
    elif [ "$mode" = PWM ] && [ "$duty" = 5.0 ] && [ "$station_count" -eq 0 ]; then
        say "[CP/HLC OK; PLC NOT MATCHED] 5% PWM is active but no remote PLC station is associated."
        say "After allowing the normal matching interval, inspect J3-J1 pair, JP2, coupling path and SLAC log."
    elif [ "$station_count" -gt 0 ] && [ "$sdp_frames" -eq 0 ]; then
        say "[SLAC OK; SDP NOT SEEN] PLC association exists, but no IPv6 SDP exchange was captured."
    elif [ "$sdp_frames" -gt 0 ] && [ "$app_markers" -eq 0 ]; then
        say "[SDP SEEN; APPLICATION NOT STARTED] Inspect IPv6/TCP and Josev protocol negotiation."
    elif [ "$app_markers" -gt 0 ]; then
        say "[APPLICATION REACHED] Josev session markers exist; inspect the last protocol message/error."
    else
        say "[OBSERVE] No single failure boundary is proven yet. Save a snapshot and review all four panes."
    fi

    if [ "$secc_errors" -gt 0 ]; then
        say
        say "Recent Josev errors:"
        printf '%s\n' "$secc_text" | grep -Ei 'ERROR|CRITICAL|Traceback|Exception' | tail -n 8 || true
    fi
}

pause_menu() {
    say
    read -r -p "Press Enter to return to the operator menu ..." _ || true
}

do_menu() {
    while true; do
        clear
        cat <<'EOF'
MONDAY EV OPERATOR MENU

  1  Read-only status
  2  Diagnose current layer
  3  Save diagnostic snapshot
  4  Request latched fail-safe STOP
  5  Show evidence paths
  6  Perform final shutdown (confirmation required)
  q  Close this menu

CP commands RESET / REMOTE ON / A / HLC / STATUS are entered only in the
large Monday-console pane. This menu never issues A, HLC, PWM or RESET.
EOF
        local choice confirm
        read -r -p "Selection: " choice || return 0
        case "$choice" in
            1) do_status; pause_menu ;;
            2) do_diagnose; pause_menu ;;
            3) do_snapshot; pause_menu ;;
            4) do_stop; pause_menu ;;
            5) do_paths; pause_menu ;;
            6)
                say "This will require a safe CP state and stop the complete test stack."
                read -r -p "Type SHUTDOWN to continue: " confirm || true
                if [ "$confirm" = SHUTDOWN ]; then
                    do_shutdown
                else
                    say "Shutdown cancelled."
                fi
                pause_menu
                ;;
            q|Q) return 0 ;;
            *) say "Unknown selection."; sleep 1 ;;
        esac
    done
}

do_cockpit() {
    require_command tmux
    active_run >/dev/null
    sudo ss -lunp 2>/dev/null | grep -q ':15118' || die "Josev SDP is not listening; run start first"

    if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
        say "Reconnecting to existing cockpit: $TMUX_SESSION"
        exec tmux attach-session -t "$TMUX_SESSION"
    fi

    [ "$(console_count)" = 0 ] || die "a standalone Monday console is already running; stop it safely before creating the cockpit"
    [ "$(evse_count)" = 0 ] || die "a standalone SLAC listener is already running; stop it before creating the cockpit"

    local quoted_self control_pane monitor_pane slac_pane secc_pane
    printf -v quoted_self '%q' "$SELF"

    tmux new-session -d -s "$TMUX_SESSION" -n live "$quoted_self console"
    control_pane="$(tmux display-message -p -t "$TMUX_SESSION:live.0" '#{pane_id}')"
    monitor_pane="$(tmux split-window -h -p 43 -t "$control_pane" -P -F '#{pane_id}' "$quoted_self monitor")"
    slac_pane="$(tmux split-window -v -p 66 -t "$monitor_pane" -P -F '#{pane_id}' "$quoted_self slac")"
    secc_pane="$(tmux split-window -v -p 50 -t "$slac_pane" -P -F '#{pane_id}' "$quoted_self secc-logs")"

    tmux set-option -t "$TMUX_SESSION" mouse on
    tmux set-option -t "$TMUX_SESSION" status on
    tmux set-option -t "$TMUX_SESSION" status-left '#[bold] MONDAY EV #[default]'
    tmux set-option -t "$TMUX_SESSION" status-right 'Ctrl-b M menu | Ctrl-b z zoom | %Y-%m-%d %H:%M:%S '
    tmux set-window-option -t "$TMUX_SESSION:live" remain-on-exit on
    tmux set-window-option -t "$TMUX_SESSION:live" pane-border-status top 2>/dev/null || true
    tmux set-window-option -t "$TMUX_SESSION:live" pane-border-format ' #{pane_title} ' 2>/dev/null || true
    tmux select-pane -t "$control_pane" -T 'CONTROL — Monday console'
    tmux select-pane -t "$monitor_pane" -T 'CP — decoded state'
    tmux select-pane -t "$slac_pane" -T 'PLC — SLAC'
    tmux select-pane -t "$secc_pane" -T 'HLC — Josev SECC'

    if tmux list-commands 2>/dev/null | grep -q '^display-popup'; then
        tmux bind-key -T prefix M display-popup -E -w 90% -h 90% "$quoted_self menu"
        tmux bind-key -T prefix D display-popup -E -w 90% -h 90% "$quoted_self diagnose; printf '\nPress Enter ...'; read _"
    fi
    tmux select-pane -t "$control_pane"

    say "Cockpit controls: click a pane, or use Ctrl-b followed by an arrow key."
    say "Ctrl-b z zooms the selected pane. Ctrl-b M opens the guarded operator menu."
    exec tmux attach-session -t "$TMUX_SESSION"
}

do_test_day() {
    require_command tmux
    if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
        die "cockpit session $TMUX_SESSION already exists; use: $SELF cockpit"
    fi
    do_start
    do_cockpit
}

do_console() {
    local run
    run="$(active_run)"
    [ "$(console_count)" = 0 ] || die "Monday console is already running"
    sudo ss -lunp 2>/dev/null | grep -q ':15118' || die "Josev SDP is not listening; run start first"
    say "Logging interactive console to $run/console.log"
    say "At bench>: RESET, REMOTE ON, A, STATUS. Do not enter HLC until stable B1."
    cd "$MONDAY_HOME"
    script -q -f -e -c "sudo python3 '$CONSOLE'" "$run/console.log"
}

do_slac() {
    local run
    run="$(active_run)"
    [ "$(evse_count)" = 0 ] || die "an evse process is already running; never start two SLAC owners"
    sudo ss -lunp 2>/dev/null | grep -q ':15118' || die "Josev SDP is not listening; run start first"
    say "Logging SLAC to $run/slac.log"
    say "Expected before the EV: CM_SET_KEY.RESULT 1, then UnoccupiedState: Listening ..."
    set +e
    sudo stdbuf -oL -eL evse -i eth0 -p "$PROFILE" -s default -d 2>&1 | tee -a "$run/slac.log"
    local rc="${PIPESTATUS[0]}"
    set -e
    if [ "$rc" -eq 0 ] || [ "$rc" -eq 130 ]; then
        ok "SLAC listener stopped"
        return 0
    fi
    warn "SLAC listener exited with code $rc"
    return "$rc"
}

do_monitor() {
    local run
    run="$(active_run)"
    require_file "$STATUS_TOOL"
    say "Logging decoded CP state to $run/cp-monitor.log; stop with Ctrl+C"
    set +e
    sudo python3 -u "$STATUS_TOOL" --watch 2>&1 | tee -a "$run/cp-monitor.log"
    local rc="${PIPESTATUS[0]}"
    set -e
    [ "$rc" -eq 0 ] || [ "$rc" -eq 130 ] || return "$rc"
}

do_secc_logs() {
    local run
    run="$(active_run)"
    say "Following Josev SECC; logging to $run/secc-follow.log; stop with Ctrl+C"
    set +e
    compose logs --no-color --timestamps -f secc 2>&1 | tee -a "$run/secc-follow.log"
    local rc="${PIPESTATUS[0]}"
    set -e
    [ "$rc" -eq 0 ] || [ "$rc" -eq 130 ] || return "$rc"
}

do_status() {
    say "----- PROCESSES -----"
    sudo pgrep -a -x evse 2>/dev/null || say "No evse SLAC process"
    pgrep -af '[e]vse_monday_console.py' 2>/dev/null || say "No Monday console process"
    say
    say "----- CONTAINERS -----"
    compose ps 2>/dev/null || true
    say
    say "----- SDP SOCKET -----"
    sudo ss -lunp 2>/dev/null | grep ':15118' || say "No UDP 15118 socket"
    say
    say "----- CP STATE -----"
    if sudo test -f "$RUNTIME_DIR/state.json"; then
        sudo cat "$RUNTIME_DIR/state.json"
        say
        sudo python3 "$STATUS_TOOL" || true
    else
        say "No state.json"
    fi
    say
    say "----- PLC TABLE -----"
    sudo plcstat -t -i eth0 || true
    say
    say "----- PACKET CAPTURE -----"
    if capture_running; then
        say "tcpdump running with PID $(sudo cat "$RUNTIME_DIR/tcpdump.pid")"
    else
        say "tcpdump not running"
    fi
}

do_snapshot() {
    local run stamp out
    run="$(active_run)"
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    out="$run/snapshots/snapshot-$stamp.txt"
    set +e
    {
        printf 'snapshot_local=%s\n' "$(date --iso-8601=seconds)"
        printf 'snapshot_utc=%s\n\n' "$(date -u --iso-8601=seconds)"
        do_status
        printf '\n----- PLC NETWORK -----\n'
        sudo plctool -m -i eth0
        printf '\n----- LAST 120 SECC LOG LINES -----\n'
        compose logs --no-color --timestamps --tail=120 secc
    } 2>&1 | tee "$out"
    set -e
    ok "snapshot saved: $out"
}

do_stop() {
    say "----- REQUESTING FAIL-SAFE STOP -----"
    if [ "$(console_count)" -gt 0 ]; then
        write_stop_request "Operator requested monday_ops STOP"
        if wait_for_safe_state 8; then
            ok "CP=A, relay command OFF and remote OFF confirmed"
            return 0
        fi
        die "STOP was not confirmed within 8 seconds; remove low-voltage PCB power"
    fi

    if state_safe; then
        ok "console is not running, but the last snapshot is already safe"
        return 0
    fi
    die "console is not running and a safe live state cannot be confirmed; remove low-voltage PCB power"
}

stop_capture() {
    if ! capture_running; then
        sudo rm -f "$RUNTIME_DIR/tcpdump.pid" 2>/dev/null || true
        return 0
    fi
    local pid i
    pid="$(sudo cat "$RUNTIME_DIR/tcpdump.pid")"
    sudo kill -INT "$pid" 2>/dev/null || true
    for ((i = 0; i < 20; i++)); do
        sudo kill -0 "$pid" 2>/dev/null || break
        sleep 0.25
    done
    if sudo kill -0 "$pid" 2>/dev/null; then
        warn "tcpdump did not stop after SIGINT; sending TERM"
        sudo kill -TERM "$pid" 2>/dev/null || true
        sleep 1
    fi
    sudo rm -f "$RUNTIME_DIR/tcpdump.pid"
}

collect_into() {
    local run="$1"
    mkdir -p "$run/final"
    compose logs --no-color --timestamps >"$run/final/docker-all.log" 2>&1 || true
    compose logs --no-color --timestamps secc >"$run/final/josev-secc.log" 2>&1 || true
    compose ps >"$run/final/compose-ps.txt" 2>&1 || true
    sudo cp "$RUNTIME_DIR/state.json" "$run/final/state.json" 2>/dev/null || true
    sudo cp "$RUNTIME_DIR/command.json" "$run/final/unconsumed-command.json" 2>/dev/null || true
    {
        date --iso-8601=seconds
        ip -br link
        ip addr show dev eth0
        sudo ss -lunp
        sudo plcstat -t -i eth0
        sudo plctool -m -i eth0
        sudo plctool -i eth0 -r "$DEVOLO_MAC"
    } >"$run/final/final-diagnostics.txt" 2>&1 || true
    sudo chown -R "$OWNER:$OWNER_GROUP" "$run" 2>/dev/null || true
}

do_collect() {
    local run
    run="$(active_run)"
    collect_into "$run"
    ok "evidence copied into $run/final"
}

do_shutdown() {
    local run
    run="$(active_run)"

    do_stop

    if [ "$(evse_count)" -gt 0 ]; then
        say "Stopping SLAC listener with SIGINT ..."
        sudo pkill -INT -x evse || true
        sleep 1
    fi

    if [ "$(console_count)" -gt 0 ]; then
        say "Stopping Monday console after confirmed safe state ..."
        local console_pid
        console_pid="$(pgrep -o -f '[e]vse_monday_console.py' || true)"
        [ -z "$console_pid" ] || sudo kill -INT "$console_pid" 2>/dev/null || true
        sleep 2
    fi

    stop_capture
    collect_into "$run"

    say "Stopping Josev and Redis ..."
    compose down

    {
        printf 'run_stopped_local=%s\n' "$(date --iso-8601=seconds)"
        printf 'run_stopped_utc=%s\n' "$(date -u --iso-8601=seconds)"
    } >"$run/final/shutdown-time.txt"

    sudo chown -R "$OWNER:$OWNER_GROUP" "$run" 2>/dev/null || true
    (
        cd "$run"
        find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum >SHA256SUMS
    )

    ln -sfnT "$run" "$LAST_LINK"
    rm -f "$CURRENT_LINK"
    ok "shutdown completed"
    say "Evidence folder: $run"
    say "Latest-run link: $LAST_LINK"

    if [ -n "${TMUX:-}" ] && tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
        say "Cockpit will close automatically in five seconds."
        ( sleep 5; tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true ) &
    fi
}

do_paths() {
    if [ -L "$CURRENT_LINK" ]; then
        say "Active: $(readlink -f "$CURRENT_LINK")"
    else
        say "Active: none"
    fi
    if [ -L "$LAST_LINK" ]; then
        say "Last:   $(readlink -f "$LAST_LINK")"
    else
        say "Last:   none"
    fi
}

case "${1:-help}" in
    preflight) do_preflight ;;
    start) do_start ;;
    test-day) do_test_day ;;
    cockpit) do_cockpit ;;
    console) do_console ;;
    slac) do_slac ;;
    monitor) do_monitor ;;
    secc-logs) do_secc_logs ;;
    status) do_status ;;
    diagnose) do_diagnose ;;
    menu) do_menu ;;
    snapshot) do_snapshot ;;
    stop) do_stop ;;
    collect) do_collect ;;
    shutdown) do_shutdown ;;
    paths) do_paths ;;
    help|-h|--help) usage ;;
    *) usage >&2; exit 2 ;;
esac
