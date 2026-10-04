import importlib.util
from pathlib import Path
import tempfile
import unittest


TEST_ROOT = Path(__file__).resolve().parents[1]
MODULE_CANDIDATES = (
    TEST_ROOT / "evse_monday_console.py",
    TEST_ROOT.parent / "evse_monday_console.py",
)
MODULE_PATH = next((path for path in MODULE_CANDIDATES if path.is_file()), None)
if MODULE_PATH is None:
    raise FileNotFoundError("evse_monday_console.py not found beside Monday repository")
SPEC = importlib.util.spec_from_file_location("evse_monday_console", MODULE_PATH)
console = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(console)


def status_line(mode="A_POSITIVE_DC", duty="100.0%", logic="0.0%", pair="00"):
    return (
        "STATUS CP_MODE=%s CP_POSITIVE_DUTY=%s D9_LOGIC_HIGH=%s "
        "FAULT=NO REASON=NONE CONTACTORS=OFF REMOTE=OFF "
        "FB_D2=%s FB_D3=%s FB_PAIR=%s FB_STABLE=YES FB_CHANGES=1"
        % (mode, duty, logic, pair[0], pair[1], pair)
    )


class FakeLink:
    def __init__(self):
        self.commands = []

    def request(self, command, prefix, show=True, allow_fault=False):
        self.commands.append(command)
        if command == "SESSION STOP":
            return "ACK SESSION STOP; CP=A; CONTACTORS=OFF; REMOTE=OFF"
        if command == "STATUS":
            return status_line()
        if command == "HLC":
            return "ACK HLC; CP=1kHz/5.0%; D9=95.0% HIGH (inverted)"
        raise AssertionError("Unexpected command " + command)


class MondayConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        console.STATE_PATH = Path(self.temporary.name) / "state.json"
        self.relay_calls = []
        self.link = FakeLink()
        self.controller = console.Controller(self.link, self.relay_calls.append)

    def tearDown(self):
        self.temporary.cleanup()

    def test_external_stop_opens_and_latches(self):
        self.controller.on = True
        self.controller.expected = ("PWM", 5.0, 95.0)
        self.controller.external_stop("unit test")
        self.assertFalse(self.controller.on)
        self.assertTrue(self.controller.external_stop_latched)
        self.assertFalse(self.relay_calls[-1])
        count = len(self.link.commands)
        self.controller.drive("HLC", "ACK HLC;", ("PWM", 5.0, 95.0))
        self.assertEqual(count, len(self.link.commands))

    def test_pwm_validation(self):
        self.assertEqual(console.parse_pwm("PWM 5"), 50)
        self.assertEqual(console.parse_pwm("PWM 10.0"), 100)
        self.assertEqual(console.parse_pwm("PWM 96.0"), 960)
        for value in ("PWM 5.1", "PWM 9.9", "PWM 96.1", "PWM nonsense"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    console.parse_pwm(value)


if __name__ == "__main__":
    unittest.main()
