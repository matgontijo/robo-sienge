"""CLI safety regression tests; no credentials, database or external services."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


class CliSafetyTests(unittest.TestCase):
    def load_cli(self, config, orchestrator):
        path = Path(__file__).resolve().parents[1] / "main.py"
        spec = importlib.util.spec_from_file_location("cli_safety_subject", path)
        module = importlib.util.module_from_spec(spec)
        modules = {"config": config, "orchestrator": orchestrator,
                   "loguru": SimpleNamespace(logger=Mock())}
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(module)
        return module, modules

    def test_dry_run_stops_before_validation_and_processing(self):
        config = SimpleNamespace(validate_config=Mock())
        orchestrator = SimpleNamespace(executar_ciclo=Mock())
        cli, modules = self.load_cli(config, orchestrator)
        with patch.dict(sys.modules, modules), patch.object(sys, "argv", ["main.py", "run", "--dry-run"]):
            with self.assertRaises(SystemExit) as error:
                cli.main()
        self.assertEqual(error.exception.code, 2)
        config.validate_config.assert_not_called()
        orchestrator.executar_ciclo.assert_not_called()

    def test_normal_run_still_forwards_dates(self):
        from datetime import date
        config = SimpleNamespace(validate_config=Mock())
        orchestrator = SimpleNamespace(executar_ciclo=Mock())
        cli, modules = self.load_cli(config, orchestrator)
        args = ["main.py", "run", "--inicio", "2026-01-02", "--fim", "2026-01-03"]
        with patch.dict(sys.modules, modules), patch.object(sys, "argv", args):
            cli.main()
        config.validate_config.assert_called_once_with()
        orchestrator.executar_ciclo.assert_called_once_with(
            date(2026, 1, 2), date(2026, 1, 3), iniciado_por="cli", relatorio_path=None)


if __name__ == "__main__":
    unittest.main()
