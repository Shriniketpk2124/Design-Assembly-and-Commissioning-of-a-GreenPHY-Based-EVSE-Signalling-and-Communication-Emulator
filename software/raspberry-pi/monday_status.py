#!/usr/bin/env python3
"""Display the live host snapshot and the CP state accepted by Josev."""

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import time


MODULE_PATH = (
    Path(__file__).resolve().parent
    / "iso15118-real-ev/iso15118/secc/controller/monday_state.py"
)
SPEC = importlib.util.spec_from_file_location("monday_state_status", MODULE_PATH)
state_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = state_module
SPEC.loader.exec_module(state_module)
decode_snapshot = state_module.decode_snapshot


def show(path):
    try:
        with path.open("r", encoding="utf-8") as stream:
            snapshot = json.load(stream)
    except (OSError, ValueError) as exc:
        print("STATE UNAVAILABLE:", exc)
        return 1
    decoded = decode_snapshot(snapshot)
    age = time.time_ns() // 1_000_000 - snapshot.get("updated_unix_ms", 0)
    print(
        "CP=%s usable=%s age_ms=%s relay=%s mode=%s duty=%s pair=%s "
        "stable=%s fault=%s stop_latch=%s reason=%s"
        % (
            decoded.cp_state,
            decoded.usable,
            age,
            snapshot.get("relay_command"),
            snapshot.get("cp_mode"),
            snapshot.get("cp_positive_duty"),
            snapshot.get("fb_pair"),
            snapshot.get("fb_stable"),
            snapshot.get("fault"),
            snapshot.get("external_stop_latched"),
            decoded.reason,
        )
    )
    return 0 if decoded.usable else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path("/run/evse-monday/state.json"))
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    if not args.watch:
        return show(args.state)
    try:
        while True:
            show(args.state)
            time.sleep(1)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
