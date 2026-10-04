"""Bench UART telemetry monitor. No relay control or Josev connection."""
import argparse
import json
import time
from dataclasses import asdict

from cp_feedback import decode_cp
from serial_parser import StreamParser, Telemetry


class Tracker:
    def __init__(self):
        self.last = None
        self.received = None
        self.ready = False
        self.fault = None
        self.duplicates = 0
        self.missed = 0

    def accept(self, packet, now):
        if self.fault:
            return
        if self.last is not None:
            elapsed = now - self.received
            ds = (packet.seq - self.last.seq) & 0xFFFF
            du = (packet.uptime_ms - self.last.uptime_ms) & 0xFFFFFFFF
            if elapsed < 0 or elapsed > 0.5:
                self.fault = "Feedback gap; restart reader to resynchronise"
                return
            if ds == 0:
                if packet == self.last:
                    self.duplicates += 1
                else:
                    self.fault = "Same sequence with changed contents"
                return  # Duplicates never refresh the timestamp.
            if ds >= 32768 or not 0 < du <= 500:
                self.fault = "Counter discontinuity/reset; restart reader"
                return
            self.missed += ds - 1
            self.ready = True
        self.last = packet
        self.received = now

    def snapshot(self, now):
        age = None if self.received is None else (now - self.received) * 1000
        link = "FRESH"
        reason = None
        if self.fault:
            link, reason = "FAULT", self.fault
        elif self.last is None:
            link, reason = "WAITING", "No valid telemetry received"
        elif not 0 <= age <= 500:
            link, reason = "STALE", "No advancing telemetry within 500 ms"
        elif not self.ready:
            link, reason = "SYNCING", "Waiting for a second advancing packet"
        reading = {"candidate": "UNKNOWN", "stack_state": "UNKNOWN", "reason": reason}
        if link == "FRESH":
            reading = asdict(decode_cp(
                self.last.d2, self.last.d3, self.last.mode, age,
                mapping_verified=False,
            ))
        return {
            "link": link, "age_ms": None if age is None else round(age, 1),
            "telemetry": None if self.last is None else asdict(self.last),
            "duplicates": self.duplicates, "missed": self.missed, **reading,
        }


def self_test():
    from serial_parser import frame

    def packet(seq, uptime):
        return Telemetry(seq, uptime, 1, 0, "PWM_5")

    t = Tracker()
    assert t.snapshot(0)["link"] == "WAITING"
    t.accept(packet(1, 100), 0)
    assert t.snapshot(0)["link"] == "SYNCING"
    t.accept(packet(2, 200), 0.1)
    assert t.snapshot(0.1)["candidate"] == "B"
    assert t.snapshot(0.1)["stack_state"] == "UNKNOWN"
    t.accept(packet(2, 200), 0.2)
    assert t.received == 0.1 and t.duplicates == 1
    assert t.snapshot(0.7)["link"] == "STALE"
    t.accept(packet(3, 300), 0.7)
    assert t.snapshot(0.7)["link"] == "FAULT"

    t = Tracker()
    t.accept(packet(65535, 4294967246), 0)
    t.accept(packet(0, 50), 0.1)
    assert t.snapshot(0.1)["link"] == "FRESH"
    t.accept(packet(2, 250), 0.3)
    assert t.missed == 1
    t.accept(packet(0, 0), 0.4)
    assert t.fault

    t = Tracker()
    t.accept(packet(1, 100), 0)
    t.accept(packet(1, 101), 0.1)
    assert t.fault

    parser = StreamParser()
    good = frame(b"CP1,42,12345,1,0,PWM_5")
    assert parser.feed(good[:7]) == []
    assert parser.feed(good[7:]) == [packet(42, 12345)]
    bad = good.replace(b",1,0,", b",0,0,")
    assert parser.feed(bad) == [] and parser.errors == 1
    print("UART tracker checks passed. No hardware was accessed.")


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--self-test", action="store_true")
    args.add_argument("--port")
    options = args.parse_args()
    if options.self_test:
        self_test()
        return
    if not options.port:
        args.error("Specify --self-test or --port DEVICE")

    import serial

    tracker, parser = Tracker(), StreamParser()
    next_report = 0.0
    try:
        with serial.Serial(options.port, baudrate=19200, bytesize=8,
                           parity="N", stopbits=1, timeout=0.05,
                           exclusive=True) as uart:
            uart.reset_input_buffer()
            while True:
                if uart.in_waiting > 4096:
                    raise RuntimeError("Excess UART backlog; restart reader")
                chunk = uart.read(min(max(uart.in_waiting, 1), 256))
                now = time.monotonic()
                for packet in parser.feed(chunk):
                    tracker.accept(packet, now)
                if now >= next_report:
                    status = tracker.snapshot(now)
                    status["parse_errors"] = parser.errors
                    print(json.dumps(status), flush=True)
                    next_report = now + 0.2
    except KeyboardInterrupt:
        pass
    except (serial.SerialException, OSError, RuntimeError) as error:
        print(json.dumps({"link": "DISCONNECTED", "stack_state": "UNKNOWN",
                          "reason": str(error)}), flush=True)
        raise SystemExit(1)
    finally:
        print(json.dumps({"link": "STOPPED", "stack_state": "UNKNOWN"}), flush=True)


if __name__ == "__main__":
    main()
