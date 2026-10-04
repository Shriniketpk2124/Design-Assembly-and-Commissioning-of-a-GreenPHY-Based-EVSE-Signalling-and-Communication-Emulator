"""Bench CP decoder. Detector mapping requires physical verification."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CPReading:
    candidate: str
    stack_state: str
    reason: str


def decode_cp(
    d2: int,
    d3: int,
    mode: str,
    age_ms: float,
    mapping_verified: bool = False,
    max_age_ms: float = 500,
) -> CPReading:
    """mode is Nano-reported DC_POS or PWM_5, not a voltage measurement."""

    if not 0 <= age_ms <= max_age_ms:
        return CPReading("UNKNOWN", "UNKNOWN", "Missing or stale feedback")

    if d2 not in (0, 1) or d3 not in (0, 1):
        return CPReading("UNKNOWN", "UNKNOWN", "Invalid digital input")

    if mode not in ("DC_POS", "PWM_5"):
        return CPReading("UNKNOWN", "UNKNOWN", "Unrecognised output mode")

    candidates = {
        (0, 0): "A",
        (1, 0): "B",
        (1, 1): "C_OR_FAULT",
        (0, 1): "INVALID",
    }
    candidate = candidates[(d2, d3)]

    if not mapping_verified:
        return CPReading(
            candidate, "UNKNOWN", "Detector mapping not physically verified"
        )

    if candidate in ("C_OR_FAULT", "INVALID"):
        return CPReading(
            candidate, "UNKNOWN", "Cannot establish a unique CP state"
        )

    suffix = "1" if mode == "DC_POS" else "2"
    return CPReading(
        candidate,
        candidate + suffix,
        "Based on detector bits and reported mode; waveform not measured",
    )


if __name__ == "__main__":
    # Check the cases that must not become false readiness indications.
    assert decode_cp(0, 0, "DC_POS", 0).stack_state == "UNKNOWN"
    assert decode_cp(0, 0, "DC_POS", 0, True).stack_state == "A1"
    assert decode_cp(1, 0, "PWM_5", 0, True).stack_state == "B2"
    assert decode_cp(1, 1, "PWM_5", 0, True).stack_state == "UNKNOWN"
    assert decode_cp(0, 1, "PWM_5", 0, True).stack_state == "UNKNOWN"
    assert decode_cp(1, 0, "PWM_5", 501, True).stack_state == "UNKNOWN"

    print("Decoder checks passed. No hardware was accessed.")
    for bits in ((0, 0), (1, 0), (1, 1), (0, 1)):
        print(bits, decode_cp(*bits, "PWM_5", 0))
