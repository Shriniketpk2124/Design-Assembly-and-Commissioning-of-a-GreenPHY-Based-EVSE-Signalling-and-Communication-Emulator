#!/usr/bin/env python3
"""Fail-safe CP console and state publisher for Monday's real-EV test.

This is a separate version of evse_bench_console.py.  It remains the only
owner of /dev/ttyUSB0 and GPIO17/GPIO27, polls Arduino STATUS every second,
publishes a fresh atomic snapshot to /run/evse-monday/state.json, and accepts
only a STOP request from the Josev container.

The STOP request is latched: after Josev reaches a forbidden energy-transfer
boundary, A/HLC/PWM are blocked until the operator types RESET.  This program
does not verify physical contacts, isolation, HV, PP wiring or an IMD.
"""

import argparse
import fcntl
import importlib.util
import json
import math
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time

# SSH_INPUT_UTF8_GUARD
# Replace a malformed SSH-terminal byte instead of terminating the console.
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")


VERSION = "4-monday-confirm1"
_decoder_path = (Path(__file__).resolve().parent / "iso15118-real-ev/iso15118"
                 / "secc/controller/monday_state.py")
_decoder_spec = importlib.util.spec_from_file_location("monday_console_decoder", _decoder_path)
_decoder = importlib.util.module_from_spec(_decoder_spec)
sys.modules[_decoder_spec.name] = _decoder
_decoder_spec.loader.exec_module(_decoder)
if getattr(_decoder, "OPERATOR_CONFIRMATION_VERSION", None) != 1:
    raise RuntimeError("Console/decoder confirmation versions differ; run installer again")
RUNTIME_DIR = Path("/run/evse-monday")
STATE_PATH = RUNTIME_DIR / "state.json"
COMMAND_PATH = RUNTIME_DIR / "command.json"
LOCK_PATH = "/run/lock/evse_bench_console.lock"

HELP = """Commands (case-insensitive):
  A                  positive DC; K1/K2 ON
  HLC                1 kHz, 5% positive duty; K1/K2 ON
  PWM <duty>         5.0 or 10.0..96.0; K1/K2 ON (bench diagnostics only)
  STOP               K1/K2 OFF, upstream A, REMOTE OFF
  STATUS             Arduino state and Pi relay-command state
  CONFIRM C          scope-observed C2 for 60 s; requires stable HLC pair 11
  CONFIRM AUTO       clear scope confirmation; use automatic mapping
  REMOTE ON / OFF    Arduino heartbeat supervision; no relay change
  HB / PING <text>   communication diagnostics; no relay change
  SESSION START      same as HLC
  SESSION STOP       same as STOP
  DISABLE            K1/K2 OFF, upstream negative DC, REMOTE OFF
  FAULT <text>       K1/K2 OFF and latch an Arduino fault
  RESET              K1/K2 OFF; clear Arduino fault and Josev STOP latch
  EXIT / QUIT        STOP and return to the Pi shell
  HELP               show this list

Monday rule: after an external Josev STOP, use RESET before A or HLC.
A connects the positive DC signal. Use STOP to disconnect it.
CONFIRM C is a human observation, not automatic detection or power permission.
Observe CP positive plateau (not the PWM mean). Clear on return to B.
"""


def atomic_json(path, value):
    path.parent.mkdir(mode=0o770, parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_name, 0o660)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def parse_percent(value):
    try:
        result = float(value.removesuffix("%"))
    except (AttributeError, TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def parse_pwm(command):
    match = re.fullmatch(r"PWM +([0-9]{1,3})(?:\.([0-9]+))?", command)
    if not match:
        raise ValueError("Use PWM 5.0 or PWM 10.0..96.0")
    fraction = match[2] or "0"
    if any(char != "0" for char in fraction[1:]):
        raise ValueError("PWM resolution is 0.1%; extra decimal places must be zero")
    tenths = int(match[1]) * 10 + int(fraction[0])
    if tenths != 50 and not 100 <= tenths <= 960:
        raise ValueError("Allowed PWM: 5.0 or 10.0..96.0 percent")
    return tenths


def gpio(on):
    subprocess.run(
        ["pinctrl", "set", "17,27", "op", "dh" if on else "dl"],
        check=True,
        timeout=2,
    )


def fields(line):
    if not line.startswith("STATUS "):
        raise RuntimeError("Expected an Arduino STATUS response")
    return dict(word.split("=", 1) for word in line.split()[1:] if "=" in word)


def check_status(line, expected=None, remote=None, allow_fault=False):
    state = fields(line)
    required = (
        "CP_MODE",
        "CP_POSITIVE_DUTY",
        "D9_LOGIC_HIGH",
        "FAULT",
        "REMOTE",
        "FB_D2",
        "FB_D3",
        "FB_PAIR",
        "FB_STABLE",
        "FB_CHANGES",
    )
    missing = [name for name in required if name not in state]
    if missing:
        raise RuntimeError("Incomplete Arduino STATUS (%s): %s" % (missing, line))
    if state["FAULT"] not in ("NO", "YES") or state["REMOTE"] not in ("OFF", "ON"):
        raise RuntimeError("Incomplete Arduino fault/remote status: " + line)
    if state["FB_D2"] not in ("0", "1") or state["FB_D3"] not in ("0", "1"):
        raise RuntimeError("Invalid comparator feedback: " + line)
    if state["FB_PAIR"] not in ("00", "01", "10", "11"):
        raise RuntimeError("Invalid feedback pair: " + line)
    if state["FB_STABLE"] not in ("YES", "NO"):
        raise RuntimeError("Invalid feedback stability: " + line)
    if state["FAULT"] != "NO" and not allow_fault:
        raise RuntimeError("Arduino reports a fault: " + line)
    if remote is not None and state["REMOTE"] != ("ON" if remote else "OFF"):
        raise RuntimeError("Arduino remote-supervision state changed: " + line)
    if expected:
        mode, duty, logic = expected
        positive = parse_percent(state["CP_POSITIVE_DUTY"])
        high = parse_percent(state["D9_LOGIC_HIGH"])
        if (
            state["CP_MODE"] != mode
            or positive is None
            or high is None
            or abs(positive - duty) > 0.01
            or abs(high - logic) > 0.01
        ):
            raise RuntimeError("Arduino output status changed/mismatched: " + line)
    return state


class Link:
    def __init__(self, port):
        import serial

        self.serial = serial.Serial(
            port, 115200, timeout=0.05, write_timeout=1, exclusive=True
        )
        # Opening this Nano port can reset the MCU. Relays are already OFF.
        until = time.monotonic() + 3
        while time.monotonic() < until:
            line = self.serial.readline().decode("ascii", errors="replace").strip()
            if line:
                print("Arduino:", line, flush=True)
        self.pending = bytearray()

    @staticmethod
    def inspect(line, allow_fault=False):
        if "EVSE CP controller ready" in line or line.startswith("Startup:"):
            raise RuntimeError("Unexpected Arduino restart")
        if line.startswith("ERR") or (line.startswith("FAULT") and not allow_fault):
            raise RuntimeError("Arduino: " + line)
        if line.startswith("STATUS "):
            check_status(line, allow_fault=allow_fault)

    def read_line(self, allow_fault=False):
        if b"\n" not in self.pending:
            self.pending.extend(self.serial.read(self.serial.in_waiting or 1))
        if len(self.pending) > 8192:
            raise RuntimeError("Oversized or unterminated serial response")
        if b"\n" not in self.pending:
            return None
        raw, _, remainder = self.pending.partition(b"\n")
        self.pending = bytearray(remainder)
        line = raw.decode("ascii", errors="replace").strip()
        self.inspect(line, allow_fault=allow_fault)
        return line

    def request(self, command, prefix, show=True, allow_fault=False):
        until = time.monotonic() + 0.3
        while self.serial.in_waiting or self.pending:
            if time.monotonic() > until:
                raise RuntimeError("Serial stream did not become idle")
            self.read_line(allow_fault=allow_fault)
        payload = (command + "\n").encode("ascii")
        if self.serial.write(payload) != len(payload):
            raise RuntimeError("Incomplete serial write")
        until = time.monotonic() + 2
        while time.monotonic() < until:
            line = self.read_line(allow_fault=allow_fault)
            if line:
                if show:
                    print("Arduino:", line, flush=True)
                if line.startswith(prefix):
                    return line
        raise RuntimeError("No expected reply to " + command + " within 2 seconds")

    def close(self):
        self.serial.close()


class Controller:
    def __init__(self, link, relay_setter=gpio):
        self.link = link
        self.relay_setter = relay_setter
        self.on = False
        self.expected = None
        self.remote = False
        self.faulted = False
        self.external_stop_latched = False
        self.last_state = {}
        self.last_status_ms = 0
        self.running = True
        self.operator_c = None
        self.operator_c_until = 0.0

    def snapshot(self):
        state = self.last_state
        snapshot = {
            "schema": 1,
            "updated_unix_ms": self.last_status_ms,
            "running": self.running,
            "pid": os.getpid(),
            "cp_mapping_profile": _decoder.CP_MAPPING_PROFILE,
            "operator_confirmation": self.operator_c,
            "relay_command": "ON" if self.on else "OFF",
            "external_stop_latched": self.external_stop_latched,
            "fault": state.get("FAULT", "UNKNOWN"),
            "remote": state.get("REMOTE", "UNKNOWN"),
            "cp_mode": state.get("CP_MODE", "UNKNOWN"),
            "cp_positive_duty": parse_percent(state.get("CP_POSITIVE_DUTY")),
            "d9_logic_high": parse_percent(state.get("D9_LOGIC_HIGH")),
            "fb_d2": int(state["FB_D2"]) if state.get("FB_D2") in ("0", "1") else None,
            "fb_d3": int(state["FB_D3"]) if state.get("FB_D3") in ("0", "1") else None,
            "fb_pair": state.get("FB_PAIR", "UNKNOWN"),
            "fb_stable": state.get("FB_STABLE", "NO"),
            "fb_changes": int(state["FB_CHANGES"])
            if state.get("FB_CHANGES", "").isdigit()
            else None,
        }
        return snapshot

    def clear_confirmation(self, reason):
        if self.operator_c is not None:
            print("Scope C confirmation cleared: " + reason + "; AUTO restored.", flush=True)
        self.operator_c = None
        self.operator_c_until = 0.0

    def publish(self):
        snapshot = self.snapshot()
        if self.operator_c is not None:
            result = _decoder.decode_snapshot(snapshot)
            if (time.monotonic() >= self.operator_c_until
                    or not result.usable or result.cp_state != "C2"
                    or not result.reason.startswith("OPERATOR CONFIRMED")):
                self.clear_confirmation("expired or feedback/control conditions changed")
                snapshot = self.snapshot()
        atomic_json(STATE_PATH, snapshot)

    def confirm_c(self):
        # Obtain real STATUS; a pending Josev STOP always takes precedence.
        self.status(show=False)
        consume_external_request(self)
        if not self.on or not self.remote or self.faulted or self.external_stop_latched:
            print("CONFIRM C refused: requires active HLC, heartbeat and no latch.", flush=True)
            return
        now = time.time_ns() // 1_000_000
        observation = {
            "state": "C2", "source": "PICOSCOPE_OPERATOR",
            "issued_unix_ms": now, "expires_unix_ms": now + _decoder.CONFIRM_C_TTL_MS,
            "fb_pair": self.last_state.get("FB_PAIR"),
            "fb_changes": self.snapshot()["fb_changes"], "console_pid": os.getpid(),
        }
        candidate = self.snapshot()
        candidate["operator_confirmation"] = observation
        result = _decoder.decode_snapshot(candidate, now_unix_ms=now)
        if not (result.usable and result.cp_state == "C2"
                and result.reason.startswith("OPERATOR CONFIRMED")):
            print("CONFIRM C refused: " + result.reason, flush=True)
            return
        self.operator_c = observation
        self.operator_c_until = time.monotonic() + _decoder.CONFIRM_C_TTL_MS / 1000
        self.publish()
        print(result.reason + "; communication only. CONFIRM AUTO clears it.", flush=True)

    def off(self):
        self.relay_setter(False)
        self.on = False
        self.expected = None
        self.clear_confirmation("relays OFF")
        self.publish()
        print("Pi: K1 and K2 commanded OFF (contacts open)", flush=True)

    def stop(self):
        self.off()  # Open contacts even if USB is already broken.
        if self.faulted:
            print("Fault remains latched; contacts OFF. Use RESET to clear it.", flush=True)
            return
        self.link.request("SESSION STOP", "ACK SESSION STOP;")
        line = self.link.request("STATUS", "STATUS ")
        self._accept_status(
            line, ("A_POSITIVE_DC", 100.0, 0.0), remote=False
        )
        self.remote = False
        self.publish()

    def drive(self, command, prefix, expected):
        self.clear_confirmation("new drive command")
        self.publish()
        if self.external_stop_latched:
            print("External STOP is latched. Type RESET before energizing.", flush=True)
            return
        if not self.remote:
            print("Monday safety rule: enter REMOTE ON before A/HLC/PWM.", flush=True)
            return
        if not self.on:
            self.off()
        self.link.request(command, prefix)
        line = self.link.request("STATUS", "STATUS ")
        self._accept_status(line, expected, remote=self.remote)
        if not self.on:
            self.relay_setter(True)
        self.expected = expected
        self.on = True
        self.publish()
        print(
            "Pi: K1/K2 commanded ON (contacts closed); " + command + " active",
            flush=True,
        )

    def _accept_status(self, line, expected=None, remote=None):
        self.last_state = check_status(
            line,
            expected,
            remote=remote,
            allow_fault=self.faulted,
        )
        self.last_status_ms = time.time_ns() // 1_000_000
        self.publish()
        return self.last_state

    def status(self, show=True):
        line = self.link.request(
            "STATUS", "STATUS ", show=show, allow_fault=self.faulted
        )
        self._accept_status(
            line,
            self.expected if self.on else None,
            remote=None if self.faulted else self.remote,
        )
        if show:
            print(
                "Pi relay command:",
                "ON" if self.on else "OFF",
                "(Arduino CONTACTORS is not Pi contact feedback)",
                flush=True,
            )
            print(
                "Monday state file:",
                STATE_PATH,
                "STOP latch:",
                "ON" if self.external_stop_latched else "OFF",
                flush=True,
            )
            result = _decoder.decode_snapshot(self.snapshot())
            print("CP=" + result.cp_state + " reason=" + result.reason, flush=True)
            print("Mapping profile:", _decoder.CP_MAPPING_PROFILE, flush=True)

    def remote_mode(self, enabled):
        self.clear_confirmation("REMOTE command")
        self.publish()
        command = "REMOTE " + ("ON" if enabled else "OFF")
        self.link.request(command, "ACK " + command)
        line = self.link.request("STATUS", "STATUS ")
        self._accept_status(
            line, self.expected if self.on else None, remote=enabled
        )
        self.remote = enabled
        self.publish()
        print("Automatic heartbeat:", "ON" if enabled else "OFF", flush=True)

    def tick(self):
        if self.remote:
            self.link.request("HB", "ACK HB", show=False)
        # Monday version polls even with relays OFF so stale data is detectable.
        self.status(show=False)

    def reset(self):
        self.off()
        self.link.request("RESET", "ACK RESET;", allow_fault=True)
        line = self.link.request("STATUS", "STATUS ", allow_fault=True)
        self.last_state = check_status(
            line, ("A_POSITIVE_DC", 100.0, 0.0), remote=False
        )
        self.last_status_ms = time.time_ns() // 1_000_000
        self.faulted = False
        self.remote = False
        self.external_stop_latched = False
        self.publish()
        print("Arduino fault and external STOP latch cleared; relays remain OFF.", flush=True)

    def disable(self):
        self.off()
        self.link.request("DISABLE", "ACK DISABLE;")
        line = self.link.request("STATUS", "STATUS ")
        self._accept_status(
            line, ("F_NEGATIVE_DC", 0.0, 100.0), remote=False
        )
        self.remote = False
        self.publish()

    def fault(self, reason):
        self.off()
        self.link.request("FAULT " + reason, "FAULT LATCHED:", allow_fault=True)
        line = self.link.request("STATUS", "STATUS ", allow_fault=True)
        state = check_status(
            line,
            ("FAULT_NEGATIVE_DC", 0.0, 100.0),
            allow_fault=True,
        )
        if state["FAULT"] != "YES":
            raise RuntimeError("Arduino did not latch the requested fault")
        self.last_state = state
        self.last_status_ms = time.time_ns() // 1_000_000
        self.faulted = True
        self.remote = False
        self.publish()

    def external_stop(self, reason):
        self.external_stop_latched = True
        print("\nEXTERNAL STOP from Josev:", reason, flush=True)
        self.stop()
        self.publish()
        print("STOP is latched. Type RESET before another A/HLC attempt.", flush=True)

    def handle(self, command):
        command = command.strip()
        if len(command) > 79 or any(not 32 <= ord(char) <= 126 for char in command):
            print(
                "Use at most 79 printable ASCII characters; no terminal escape keys.",
                flush=True,
            )
            return True
        command = " ".join(command.upper().split())
        blocked = self.faulted or self.external_stop_latched
        permitted = (
            "RESET",
            "STATUS",
            "STOP",
            "SESSION STOP",
            "EXIT",
            "QUIT",
            "HELP",
            "CONFIRM AUTO",
            "",
        )
        if blocked and command not in permitted:
            print("Safety latch active: use STATUS, RESET, STOP or EXIT.", flush=True)
            return True
        if command == "CONFIRM AUTO":
            self.clear_confirmation("operator requested AUTO")
            self.publish()
            print("CP decoding: AUTO; raw feedback unchanged.", flush=True)
        elif command == "CONFIRM C":
            self.confirm_c()
        elif command == "A":
            self.drive("A", "ACK A;", ("A_POSITIVE_DC", 100.0, 0.0))
        elif command in ("HLC", "SESSION START"):
            self.drive("HLC", "ACK HLC;", ("PWM", 5.0, 95.0))
        elif command == "PWM" or command.startswith("PWM "):
            try:
                tenths = parse_pwm(command)
            except ValueError as exc:
                print(str(exc) + "; output unchanged.", flush=True)
                return True
            value = f"{tenths // 10}.{tenths % 10}"
            self.drive(
                "PWM " + value,
                "ACK PWM CP_DUTY=",
                ("PWM", tenths / 10, (1000 - tenths) / 10),
            )
        elif command in ("STOP", "SESSION STOP"):
            self.stop()
        elif command == "STATUS":
            self.status()
        elif command in ("QUIT", "EXIT"):
            self.stop()
            return False
        elif command == "HELP":
            print(HELP, flush=True)
        elif command in ("REMOTE ON", "REMOTE OFF"):
            self.remote_mode(command == "REMOTE ON")
        elif command == "HB":
            self.link.request("HB", "ACK HB")
        elif command == "PING" or command.startswith("PING "):
            self.link.request(command, "ACK PING")
        elif command == "RESET":
            self.reset()
        elif command == "DISABLE":
            self.disable()
        elif command == "FAULT" or command.startswith("FAULT "):
            reason = command[6:] if command.startswith("FAULT ") else "UNSPECIFIED"
            self.fault(reason)
        elif command:
            print("Unknown command. " + HELP, flush=True)
        return True


def consume_external_request(controller):
    try:
        with COMMAND_PATH.open("r", encoding="utf-8") as stream:
            request = json.load(stream)
    except FileNotFoundError:
        return
    except (OSError, ValueError, TypeError) as exc:
        controller.external_stop_latched = True
        controller.off()
        raise RuntimeError("Unreadable external command; fail-safe STOP: %s" % exc)

    try:
        COMMAND_PATH.unlink()
    except FileNotFoundError:
        pass
    if not isinstance(request, dict) or request.get("schema") != 1:
        controller.external_stop_latched = True
        controller.off()
        raise RuntimeError("Invalid external command schema; fail-safe STOP")
    if request.get("command") != "STOP":
        controller.external_stop_latched = True
        controller.off()
        raise RuntimeError("Only an external STOP command is permitted")
    controller.external_stop(str(request.get("reason", "No reason supplied")))


def console_input(commands, completed):
    try:
        while True:
            command = input("bench> ")
            completed.clear()
            commands.put(command)
            completed.wait()
            if command.strip().upper() in ("EXIT", "QUIT"):
                return
    except EOFError:
        commands.put(None)


def interrupted(signum, frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--port", default="/dev/ttyUSB0")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run with sudo so pinctrl can control GPIO17/27")

    RUNTIME_DIR.mkdir(mode=0o770, parents=True, exist_ok=True)
    try:
        COMMAND_PATH.unlink()
    except FileNotFoundError:
        pass

    lock = open(LOCK_PATH, "a", encoding="utf-8")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another bench/Monday console is running. Close it first.", file=sys.stderr)
        lock.close()
        return 1

    link = None
    controller = None
    result = 0
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)
    try:
        gpio(False)
        print(
            "Monday console v"
            + VERSION
            + ". K1/K2 OFF. Close miniterm/other GPIO programs.",
            flush=True,
        )
        link = Link(args.port)
        controller = Controller(link)
        controller.stop()
        print(HELP, flush=True)
        commands = queue.Queue()
        completed = threading.Event()
        threading.Thread(
            target=console_input, args=(commands, completed), daemon=True
        ).start()
        next_poll = time.monotonic() + 1
        while True:
            consume_external_request(controller)
            try:
                command = commands.get(timeout=0.1)
            except queue.Empty:
                command = None
                received = False
            else:
                received = True
            if received:
                if command is None or not controller.handle(command):
                    completed.set()
                    break
                completed.set()
            if time.monotonic() >= next_poll:
                controller.tick()
                next_poll = time.monotonic() + 1
    except KeyboardInterrupt:
        print("\nExit requested.", flush=True)
    except Exception as exc:
        print("\nSTOPPING:", exc, file=sys.stderr, flush=True)
        result = 1
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        try:
            gpio(False)
            print("Cleanup: K1/K2 commanded OFF.", flush=True)
        except Exception as exc:
            print("GPIO OFF FAILED; remove PCB power:", exc, file=sys.stderr, flush=True)
            result = 1
        try:
            if controller:
                controller.on = False
                controller.running = False
                controller.publish()
            if link:
                link.close()
        finally:
            lock.close()
    return result


if __name__ == "__main__":
    sys.exit(main())
