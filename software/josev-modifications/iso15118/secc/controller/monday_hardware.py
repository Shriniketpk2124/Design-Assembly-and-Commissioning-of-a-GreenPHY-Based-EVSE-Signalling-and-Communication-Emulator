"""Fail-safe EVSE controller for Shri's communication-only real-EV test.

This controller intentionally reuses Josev's simulated protocol data while
replacing every hardware/safety decision that could imply a real DC power
stage.  The host console remains the only process allowed to own the Arduino
serial port and GPIO17/GPIO27.  Communication with that console is through two
small JSON files in /run/evse-monday.

This is not a production EVSE controller.  It has no IMD, no real DC output
contactors and no pre-charge power stage.  Consequently it must never claim
that isolation is valid or that the DC contactors are closed.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Dict

from iso15118.secc.controller.simulator import SimEVSEController
from iso15118.secc.controller.monday_state import decode_snapshot
from iso15118.shared.messages.datatypes import (
    DCEVSEChargeParameter,
    DCEVSEStatus,
    DCEVSEStatusCode,
)
from iso15118.shared.messages.datatypes import EVSENotification as EVSENotificationV2
from iso15118.shared.messages.enums import CpState, IsolationLevel


logger = logging.getLogger(__name__)

RUNTIME_DIR = Path(os.environ.get("MONDAY_RUNTIME_DIR", "/run/evse-monday"))
STATE_PATH = RUNTIME_DIR / "state.json"
COMMAND_PATH = RUNTIME_DIR / "command.json"

# Explicit, temporary communication-only test mode. Disabled by default.
PRECHARGE_PROBE_ENABLED = os.environ.get(
    "MONDAY_PRECHARGE_PROBE", "0"
).strip().lower() in {"1", "true", "yes", "on"}


def _read_snapshot() -> Dict[str, Any]:
    """Return the current console snapshot, or an empty fail-safe value."""

    try:
        with STATE_PATH.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Monday feedback unavailable: %s", exc)
        return {}
    if not isinstance(value, dict):
        logger.warning("Monday feedback is not a JSON object")
        return {}
    return value


def _atomic_json(path: Path, value: Dict[str, Any]) -> None:
    """Write a small JSON request without exposing a partial file."""

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


def request_host_stop(reason: str) -> None:
    """Ask the sole serial/GPIO owner to open K1/K2 and restore State A."""

    request = {
        "schema": 1,
        "command": "STOP",
        "created_unix_ms": time.time_ns() // 1_000_000,
        "reason": reason[:240],
    }
    try:
        _atomic_json(COMMAND_PATH, request)
        logger.warning("Host STOP requested: %s", reason)
    except OSError as exc:
        # The host console also fails safe on stale/missing feedback.  Logging is
        # important here because a container cannot directly guarantee GPIO OFF.
        logger.critical("Could not write host STOP request: %s", exc)


class MondayHardwareEVSEController(SimEVSEController):
    """Josev controller capped at communication-only operation."""

    def _decoded_cp(self):
        result = decode_snapshot(_read_snapshot())
        if not result.usable:
            logger.warning("CP feedback rejected: %s", result.reason)
        return result

    async def get_cp_state(self) -> CpState:
        """Return only a fresh, stable and physically verified CP mapping."""

        return CpState(self._decoded_cp().cp_state)

    def ready_to_charge(self) -> bool:
        """Allow protocol discovery only while verified HLC State B/C exists.

        Josev uses this callback in Authorization.  Here "ready" means only
        that communication may continue; it never means that DC energy can flow.
        """

        result = self._decoded_cp()
        return result.usable and result.cp_state in ("B2", "C2")

    async def is_contactor_closed(self):
        """Emulate contactor feedback only for the explicit PreCharge probe."""

        probe_active = PRECHARGE_PROBE_ENABLED and getattr(
            self, "_monday_cable_check_started", False
        )
        if probe_active:
            logger.warning(
                "TEST-ONLY: emulating closed EVSE DC contactor; "
                "no physical DC contactor is installed"
            )
            return True

        request_host_stop("Josev requested a closed DC contactor")
        return False

    async def is_contactor_opened(self) -> bool:
        """No DC power contactor is installed in this communication-only rig."""

        return True

    async def get_dc_evse_status(self) -> DCEVSEStatus:
        """Return real fail-safe status unless the explicit probe is active."""

        probe_active = PRECHARGE_PROBE_ENABLED and getattr(
            self, "_monday_cable_check_started", False
        )
        if probe_active:
            logger.warning(
                "TEST-ONLY: reporting VALID/READY without an IMD or HV stage"
            )
            return DCEVSEStatus(
                evse_notification=EVSENotificationV2.NONE,
                notification_max_delay=0,
                evse_isolation_status=IsolationLevel.VALID,
                evse_status_code=DCEVSEStatusCode.EVSE_READY,
            )

        return DCEVSEStatus(
            evse_notification=EVSENotificationV2.NONE,
            notification_max_delay=0,
            evse_isolation_status=IsolationLevel.NO_IMD,
            evse_status_code=DCEVSEStatusCode.EVSE_NOT_READY,
        )

    async def get_dc_charge_parameters(self) -> DCEVSEChargeParameter:
        """Retain simulator limits but replace its false ready/valid status."""

        parameters = await super().get_dc_charge_parameters()
        return parameters.copy(
            update={"dc_evse_status": await self.get_dc_evse_status()}
        )

    async def start_cable_check(self):
        """Start only the explicit communication-only PreCharge probe."""

        if PRECHARGE_PROBE_ENABLED:
            self._monday_cable_check_started = True
            logger.warning(
                "TEST-ONLY: CableCheck prerequisites are being emulated; "
                "no voltage or insulation measurement exists"
            )
            return

        logger.warning("CableCheck requested; no IMD/HV stage is installed")
        request_host_stop("CableCheck reached without an IMD or HV stage")

    async def get_cable_check_status(self):
        """Emulate VALID only in the explicit communication-only probe."""

        probe_active = PRECHARGE_PROBE_ENABLED and getattr(
            self, "_monday_cable_check_started", False
        )
        if probe_active:
            logger.warning(
                "TEST-ONLY: returning emulated CableCheck VALID status"
            )
            return IsolationLevel.VALID

        request_host_stop("CableCheck isolation result requested")
        return IsolationLevel.INVALID

    async def set_hlc_charging(self, is_ongoing: bool) -> None:
        """Never interpret Josev's HLC flag as permission to energize outputs."""

        request_host_stop(
            "Josev set_hlc_charging(%s) reached the energy-transfer boundary"
            % is_ongoing
        )

    async def send_charging_command(
        self,
        ev_target_voltage,
        ev_target_current,
        is_precharge: bool = False,
        is_session_bpt: bool = False,
    ):
        """Reject every pre-charge/current command instead of simulating it."""

        request_host_stop(
            "Blocked charging command: voltage=%r current=%r precharge=%r"
            % (ev_target_voltage, ev_target_current, is_precharge)
        )
        raise asyncio.TimeoutError(
            "Monday PreCharge probe stops before voltage control"
        )

    async def stop_charger(self) -> None:
        request_host_stop("Josev requested charger stop")

    async def session_ended(self, current_state: str, reason: str):
        request_host_stop("Session ended in %s: %s" % (current_state, reason))
        await super().session_ended(current_state, reason)
