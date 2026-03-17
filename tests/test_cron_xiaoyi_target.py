import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODELS_PATH = REPO_ROOT / "jiuwenclaw/gateway/cron/models.py"
INTERFACE_PATH = REPO_ROOT / "jiuwenclaw/agentserver/interface.py"


def _load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load module spec: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class CronXiaoyiTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.models = _load_module("cron_models_under_test", MODELS_PATH)

    def test_cron_target_channel_exposes_xiaoyi(self) -> None:
        self.assertEqual(self.models.CronTargetChannel.XIAOYI.value, "xiaoyi")
        self.assertEqual(self.models._normalize_targets_str("xiaoyi"), "xiaoyi")

    def test_session_prefix_maps_to_xiaoyi_target(self) -> None:
        self.assertIs(
            self.models.resolve_session_target_channel("xiaoyi_session_123"),
            self.models.CronTargetChannel.XIAOYI,
        )
        self.assertIs(
            self.models.resolve_session_target_channel("sess_123"),
            self.models.CronTargetChannel.WEB,
        )
        self.assertIsNone(self.models.resolve_session_target_channel("cron_runner"))

    def test_interface_uses_session_target_resolver(self) -> None:
        source = INTERFACE_PATH.read_text(encoding="utf-8")
        self.assertIn("from jiuwenclaw.gateway.cron import CronController, resolve_session_target_channel", source)
        self.assertIn('channel = str(session_id or "").split(\'_\')[0]', source)
        self.assertIn("target_channel = resolve_session_target_channel(session_id)", source)
        self.assertIn("cron_controller.set_target_channel(target_channel)", source)


if __name__ == "__main__":
    unittest.main()
