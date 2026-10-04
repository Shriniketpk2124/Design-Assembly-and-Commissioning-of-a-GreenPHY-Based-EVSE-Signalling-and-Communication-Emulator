"""Version 1 Nano telemetry parser; no serial port or GPIO access."""
import binascii
import re
from dataclasses import dataclass

MAX_LINE = 96
PATTERN = re.compile(
    rb"CP1,([0-9]{1,5}),([0-9]{1,10}),([01]),([01]),(DC_POS|PWM_5)"
)

@dataclass(frozen=True)
class Telemetry:
    seq: int
    uptime_ms: int
    d2: int
    d3: int
    mode: str

def frame(payload: bytes) -> bytes:
    crc = binascii.crc_hqx(payload, 0xFFFF)
    return payload + f"*{crc:04X}\n".encode("ascii")

def parse_line(line: bytes) -> Telemetry:
    if not line.endswith(b"\n") or len(line) > MAX_LINE:
        raise ValueError("Incomplete or oversized line")
    body = line[:-1]
    if body.endswith(b"\r"):
        body = body[:-1]
    payload, sep, checksum = body.rpartition(b"*")
    if not sep or not re.fullmatch(rb"[0-9A-F]{4}", checksum):
        raise ValueError("Missing or malformed checksum")
    if binascii.crc_hqx(payload, 0xFFFF) != int(checksum, 16):
        raise ValueError("Checksum mismatch")
    match = PATTERN.fullmatch(payload)
    if not match:
        raise ValueError("Invalid fields or protocol version")
    seq, uptime, d2, d3 = map(int, match.groups()[:4])
    if seq > 65535 or uptime > 4294967295:
        raise ValueError("Counter out of range")
    return Telemetry(seq, uptime, d2, d3, match[5].decode("ascii"))

class StreamParser:
    """Accumulate fragments; discard damaged lines; recover at newline."""
    def __init__(self):
        self.buffer = bytearray()
        self.discarding = False
        self.errors = 0

    def feed(self, chunk: bytes) -> list[Telemetry]:
        packets = []
        for byte in chunk:
            if self.discarding:
                if byte == 10:
                    self.discarding = False
                continue
            self.buffer.append(byte)
            if len(self.buffer) > MAX_LINE:
                self.buffer.clear()
                self.discarding = byte != 10
                self.errors += 1
            elif byte == 10:
                try:
                    packets.append(parse_line(bytes(self.buffer)))
                except ValueError:
                    self.errors += 1
                self.buffer.clear()
        return packets

if __name__ == "__main__":
    sample = frame(b"CP1,42,12345,1,0,PWM_5")
    expected = Telemetry(42, 12345, 1, 0, "PWM_5")
    parser = StreamParser()
    assert parser.feed(sample[:8]) == []
    assert parser.feed(sample[8:]) == [expected]
    assert parser.feed(sample + sample) == [expected, expected]
    assert parse_line(sample[:-1] + b"\r\n") == expected
    bad = sample.replace(b",1,0,", b",0,0,")
    assert parser.feed(bad + sample) == [expected]
    assert parser.feed(b"x" * 120 + b"\n" + sample) == [expected]
    assert parser.feed(frame(b"CP1,42,12345,2,0,PWM_5")) == []
    assert parser.feed(frame(b"CP1,65536,12345,1,0,PWM_5")) == []
    assert parser.errors == 4
    print("Parser checks passed. No hardware was accessed.")
    print("Example wire message:", sample.decode().strip())
    print("Parsed:", expected)
