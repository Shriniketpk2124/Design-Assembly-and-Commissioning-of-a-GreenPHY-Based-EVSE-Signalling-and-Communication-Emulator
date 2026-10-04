"""Fail-safe decoder for the Monday communication-only EV test.

The host bench console publishes validated Arduino STATUS fields as JSON.
This module deliberately has no Josev imports so its mapping and freshness
rules can be unit-tested independently.
"""

from dataclasses import dataclass
import math
import time
from typing import Any, Mapping, Optional


MAX_STATE_AGE_MS = 2500
OPERATOR_CONFIRMATION_VERSION = 1
CONFIRM_C_TTL_MS = 60_000
CP_MAPPING_PROFILE = "MEASURED_PWM_B10_C11"


@dataclass(frozen=True)
class DecodedCPState:
    cp_state: str
    usable: bool
    reason: str


def _number(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _operator_c(snapshot, now_unix_ms):
    """Validate a short-lived human observation without modifying raw feedback."""
    observed = snapshot.get("operator_confirmation")
    if not isinstance(observed, Mapping):
        return DecodedCPState("UNKNOWN", False, "Invalid operator confirmation")
    issued = _number(observed.get("issued_unix_ms"))
    expires = _number(observed.get("expires_unix_ms"))
    changes = snapshot.get("fb_changes")
    pid = snapshot.get("pid")
    if (
        observed.get("source") != "PICOSCOPE_OPERATOR"
        or observed.get("state") != "C2"
        or snapshot.get("cp_mode") != "PWM"
        or _number(snapshot.get("cp_positive_duty")) is None
        or abs(float(snapshot["cp_positive_duty"]) - 5.0) > 0.01
        or _number(snapshot.get("d9_logic_high")) is None
        or abs(float(snapshot["d9_logic_high"]) - 95.0) > 0.01
        or snapshot.get("external_stop_latched") is not False
        or snapshot.get("fb_pair") != "11"
        or observed.get("fb_pair") != "11"
        or type(changes) is not int or changes < 0
        or type(observed.get("fb_changes")) is not int
        or observed.get("fb_changes") != changes
        or type(pid) is not int or pid <= 0
        or type(observed.get("console_pid")) is not int
        or observed.get("console_pid") != pid
        or issued is None or expires is None
        or not 0 < expires - issued <= CONFIRM_C_TTL_MS
        or not issued <= now_unix_ms < expires
    ):
        return DecodedCPState("UNKNOWN", False,
                              "Operator C confirmation expired or conditions changed")
    return DecodedCPState(
        "C2", True,
        "OPERATOR CONFIRMED C2 from PicoScope; raw pair=11; %d s remaining"
        % math.ceil((expires - now_unix_ms) / 1000),
    )


def decode_snapshot(
    snapshot: Mapping[str, Any],
    *,
    now_unix_ms: Optional[int] = None,
    max_age_ms: int = MAX_STATE_AGE_MS,
) -> DecodedCPState:
    """Decode a console snapshot without inventing unverified CP states.

    The mapping below is specific to the PCB/Arduino combination physically
    checked by Shri.  A pair is accepted only when the console is running,
    feedback is stable, the Arduino is fault-free, and the snapshot is fresh.
    """

    if not isinstance(snapshot, Mapping):
        return DecodedCPState("UNKNOWN", False, "Snapshot is not an object")
    if snapshot.get("schema") != 1:
        return DecodedCPState("UNKNOWN", False, "Unsupported snapshot schema")
    if snapshot.get("running") is not True:
        return DecodedCPState("UNKNOWN", False, "Bench console is not running")

    updated_ms = _number(snapshot.get("updated_unix_ms"))
    if updated_ms is None:
        return DecodedCPState("UNKNOWN", False, "Missing snapshot timestamp")
    if now_unix_ms is None:
        now_unix_ms = time.time_ns() // 1_000_000
    age_ms = now_unix_ms - updated_ms
    if age_ms < -1000 or age_ms > max_age_ms:
        return DecodedCPState("UNKNOWN", False, "Feedback is stale or future-dated")

    if snapshot.get("fault") != "NO":
        return DecodedCPState("UNKNOWN", False, "Arduino fault is active or unknown")
    if snapshot.get("fb_stable") != "YES":
        return DecodedCPState("UNKNOWN", False, "Comparator feedback is unsettled")

    d2 = snapshot.get("fb_d2")
    d3 = snapshot.get("fb_d3")
    pair = snapshot.get("fb_pair")
    if d2 not in (0, 1) or d3 not in (0, 1):
        return DecodedCPState("UNKNOWN", False, "Invalid comparator bits")
    if pair != f"{d2}{d3}":
        return DecodedCPState("UNKNOWN", False, "Comparator bits and pair disagree")

    relay_command = snapshot.get("relay_command")
    if relay_command == "OFF":
        return DecodedCPState("A1", True, "CP path relays are commanded open")
    if relay_command != "ON":
        return DecodedCPState("UNKNOWN", False, "Relay command is unknown")
    if snapshot.get("external_stop_latched") is True:
        return DecodedCPState("UNKNOWN", False, "Josev STOP is latched")
    if snapshot.get("remote") != "ON":
        return DecodedCPState("UNKNOWN", False, "Arduino heartbeat supervision is off")

    mode = snapshot.get("cp_mode")
    duty = _number(snapshot.get("cp_positive_duty"))

    if snapshot.get("cp_mapping_profile", CP_MAPPING_PROFILE) != CP_MAPPING_PROFILE:
        return DecodedCPState("UNKNOWN", False, "Console/decoder mapping profiles differ")
    if snapshot.get("operator_confirmation") is not None:
        return _operator_c(snapshot, now_unix_ms)

    if mode == "A_POSITIVE_DC":
        mapping = {"00": "A1", "10": "B1", "01": "C1"}
        state = mapping.get(pair)
        if state:
            return DecodedCPState(state, True, "Verified positive-DC mapping")
        return DecodedCPState("UNKNOWN", False, "Unverified pair for positive DC")

    if mode == "PWM" and duty is not None and abs(duty - 5.0) <= 0.01:
        if CP_MAPPING_PROFILE == "MEASURED_PWM_B10_C11":
            mapping = {"00": "A2", "10": "B2", "11": "C2"}
            reason = "Bench-observed 5 percent PWM mapping: B=10 C=11"
        else:
            mapping = {"00": "A2", "11": "B2", "01": "C2"}
            reason = "Original 5 percent PWM mapping; pair 11 cannot distinguish B/C"
        state = mapping.get(pair)
        if state:
            return DecodedCPState(state, True, reason)
        return DecodedCPState("UNKNOWN", False, "Unverified pair for 5 percent PWM")

    return DecodedCPState("UNKNOWN", False, "Output is not A or 5 percent HLC PWM")
