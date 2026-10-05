import importlib.util
from pathlib import Path
import sys
import unittest


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "iso15118/secc/controller/monday_state.py"
)
SPEC = importlib.util.spec_from_file_location("monday_state_under_test", MODULE_PATH)
state_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = state_module
SPEC.loader.exec_module(state_module)
decode_snapshot = state_module.decode_snapshot


NOW = 2_000_000


def snapshot(**updates):
    value = {
        "schema": 1,
        "updated_unix_ms": NOW,
        "running": True,
        "relay_command": "ON",
        "external_stop_latched": False,
        "fault": "NO",
        "remote": "ON",
        "fb_stable": "YES",
        "cp_mode": "PWM",
        "cp_positive_duty": 5.0,
        "fb_d2": 1,
        "fb_d3": 1,
        "fb_pair": "11",
    }
    value.update(updates)
    if "fb_pair" in updates:
        value.setdefault("fb_d2", int(value["fb_pair"][0]))
        value.setdefault("fb_d3", int(value["fb_pair"][1]))
        if "fb_d2" not in updates:
            value["fb_d2"] = int(value["fb_pair"][0])
        if "fb_d3" not in updates:
            value["fb_d3"] = int(value["fb_pair"][1])
    return value


class MondayStateTests(unittest.TestCase):
    def decode(self, value):
        return decode_snapshot(value, now_unix_ms=NOW)

    def test_verified_states(self):
        self.assertEqual(
            self.decode(snapshot(cp_mode="A_POSITIVE_DC", cp_positive_duty=100,
                                 fb_pair="00")).cp_state,
            "A1",
        )
        self.assertEqual(
            self.decode(snapshot(cp_mode="A_POSITIVE_DC", cp_positive_duty=100,
                                 fb_pair="10")).cp_state,
            "B1",
        )
        self.assertEqual(
            self.decode(snapshot(cp_mode="A_POSITIVE_DC", cp_positive_duty=100,
                                 fb_pair="01")).cp_state,
            "C1",
        )
        self.assertEqual(self.decode(snapshot(fb_pair="00")).cp_state, "A2")
        self.assertEqual(self.decode(snapshot(fb_pair="10")).cp_state, "B2")
        self.assertEqual(self.decode(snapshot(fb_pair="11")).cp_state, "C2")

    def test_relay_off_is_a1(self):
        result = self.decode(snapshot(relay_command="OFF", fb_pair="01"))
        self.assertTrue(result.usable)
        self.assertEqual(result.cp_state, "A1")

    def test_fail_safe_inputs(self):
        cases = [
            snapshot(updated_unix_ms=NOW - 2501),
            snapshot(fb_stable="NO"),
            snapshot(fault="YES"),
            snapshot(external_stop_latched=True),
            snapshot(remote="OFF"),
            snapshot(cp_positive_duty=10.0),
            snapshot(fb_pair="01"),
            snapshot(fb_pair="11", fb_d3=0),
        ]
        for value in cases:
            with self.subTest(value=value):
                result = self.decode(value)
                self.assertFalse(result.usable)
                self.assertEqual(result.cp_state, "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
