import importlib.util
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODELS_PATH = REPO_ROOT / "jiuwenclaw/gateway/cron/models.py"
INTERFACE_PATH = REPO_ROOT / "jiuwenclaw/agentserver/interface.py"
XIAOYI_TASK_IDS_PATH = REPO_ROOT / "jiuwenclaw/channel/xiaoyi_task_ids.py"
XIAOYI_CHANNEL_PATH = REPO_ROOT / "jiuwenclaw/channel/xiaoyi_channel.py"
SCHEDULER_PATH = REPO_ROOT / "jiuwenclaw/gateway/cron/scheduler.py"


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
        cls.xiaoyi_task_ids = _load_module("xiaoyi_task_ids_under_test", XIAOYI_TASK_IDS_PATH)

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

    def test_proactive_xiaoyi_task_id_increments_sequence(self) -> None:
        next_task_id = self.xiaoyi_task_ids.build_next_proactive_task_id(
            "448f9e71-8b16-4fca-a99a-00675e23351e",
            "448f9e71-8b16-4fca-a99a-00675e23351e&3",
        )
        self.assertEqual(next_task_id, "448f9e71-8b16-4fca-a99a-00675e23351e&4")

    def test_proactive_xiaoyi_task_id_starts_new_sequence(self) -> None:
        next_task_id = self.xiaoyi_task_ids.build_next_proactive_task_id(
            "448f9e71-8b16-4fca-a99a-00675e23351e",
            "",
        )
        self.assertEqual(next_task_id, "448f9e71-8b16-4fca-a99a-00675e23351e&1")

    def test_xiaoyi_channel_rotates_task_id_for_proactive_pushes(self) -> None:
        source = XIAOYI_CHANNEL_PATH.read_text(encoding="utf-8")
        self.assertIn("from jiuwenclaw.channel.xiaoyi_task_ids import build_next_proactive_task_id", source)
        self.assertIn("if msg.session_id is None and session_id and not use_task_id_as_is:", source)
        self.assertIn('use_task_id_as_is = bool(meta.get("xiaoyi_use_task_id_as_is"))', source)
        self.assertIn("and not use_task_id_as_is:", source)
        self.assertIn("self._proactive_push_task_map: dict[str, str] = {}", source)
        self.assertIn("await self._reserve_proactive_receive_info(msg.id, session_id, task_id)", source)
        self.assertIn("cached_task_id = self._proactive_push_task_map.get(message_id)", source)
        self.assertIn("self._proactive_push_task_map[message_id] = next_task_id", source)
        self.assertIn("if msg.session_id is None and not self._should_keep_proactive_task_id(msg):", source)
        self.assertIn('update_channel_in_config(\n                    "xiaoyi",', source)
        self.assertIn("is_placeholder = self._is_placeholder_message(msg)", source)
        self.assertIn("is_final = not is_placeholder", source)
        self.assertIn("await self._send_text_response(session_id, task_id, content, url_key, is_final=is_final)", source)
        self.assertIn("if session_id and is_placeholder:", source)
        self.assertIn("await self._start_session_heartbeat(session_id, task_id)", source)

    def test_xiaoyi_placeholder_push_stays_non_final_until_result_arrives(self) -> None:
        source = XIAOYI_CHANNEL_PATH.read_text(encoding="utf-8")
        self.assertIn("def _is_placeholder_message(self, msg: Message) -> bool:", source)
        self.assertIn('return bool(cron_meta.get("is_placeholder"))', source)

    def test_cron_scheduler_pushes_final_update_immediately(self) -> None:
        source = SCHEDULER_PATH.read_text(encoding="utf-8")
        self.assertIn("should_push_update = False", source)
        self.assertIn("await self._on_push_update(job, run_id)", source)
        self.assertIn('logger.warning("[Cron] immediate push_update failed job=%s run=%s: %s"', source)
        self.assertIn("state.xiaoyi_task_id = build_next_proactive_task_id(last_session_id, last_task_id)", source)
        self.assertIn('"xiaoyi_use_task_id_as_is": True', source)


if __name__ == "__main__":
    unittest.main()
