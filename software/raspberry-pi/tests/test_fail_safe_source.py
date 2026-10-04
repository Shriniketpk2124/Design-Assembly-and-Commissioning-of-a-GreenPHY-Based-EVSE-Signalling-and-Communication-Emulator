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

    def test_never_reports_ready_or_valid_isolation(self):
        self.assertNotIn("DCEVSEStatusCode.EVSE_READY", self.source)
        self.assertNotIn("IsolationLevel.VALID", self.source)
        self.assertIn("DCEVSEStatusCode.EVSE_NOT_READY", self.source)
        self.assertIn("IsolationLevel.NO_IMD", self.source)

    def test_contactor_closed_is_literal_false(self):
        methods = {
            node.name: node
            for node in ast.walk(self.tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        method = methods["is_contactor_closed"]
        returns = [node for node in ast.walk(method) if isinstance(node, ast.Return)]
        self.assertTrue(
            any(
                isinstance(node.value, ast.Constant) and node.value.value is False
                for node in returns
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
