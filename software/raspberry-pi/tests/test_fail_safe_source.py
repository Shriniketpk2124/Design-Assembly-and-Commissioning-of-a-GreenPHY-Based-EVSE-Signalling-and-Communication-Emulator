import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "iso15118/secc/controller/monday_hardware.py"
COMPOSE = ROOT / "compose.monday-real-ev.yml"


class FailSafeSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = CONTROLLER.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_probe_is_disabled_by_default(self):
        self.assertIn('"MONDAY_PRECHARGE_PROBE", "0"', self.source)
        self.assertIn("PRECHARGE_PROBE_ENABLED", self.source)

    def test_default_status_is_not_ready_and_has_no_imd(self):
        self.assertIn("DCEVSEStatusCode.EVSE_NOT_READY", self.source)
        self.assertIn("IsolationLevel.NO_IMD", self.source)

    def test_contactor_check_requests_stop_outside_probe(self):
        methods = {
            node.name: node
            for node in ast.walk(self.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        method = methods["is_contactor_closed"]
        self.assertTrue(
            any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "request_host_stop"
                for node in ast.walk(method)
            )
        )

    def test_power_command_raises(self):
        methods = {
            node.name: node
            for node in ast.walk(self.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertTrue(
            any(
                isinstance(node, ast.Raise)
                for node in ast.walk(methods["send_charging_command"])
            )
        )

    def test_compose_has_no_evcc_service(self):
        compose = COMPOSE.read_text(encoding="utf-8")
        self.assertNotIn("  evcc:", compose)
        self.assertIn("  secc:", compose)
        self.assertIn("network_mode: host", compose)


if __name__ == "__main__":
    unittest.main()
