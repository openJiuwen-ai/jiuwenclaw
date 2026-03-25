import os
import tempfile
import unittest


_TMP_HOME = tempfile.mkdtemp(prefix="jiuwenclaw-test-home-")
os.environ["HOME"] = _TMP_HOME

from jiuwenclaw.agentserver.interface import JiuWenClaw


class _FakeCronBackend:
    async def list_jobs(self, *, include_disabled: bool = True) -> list[dict]:
        return []

    async def get_job(self, job_id: str) -> dict | None:
        return None

    async def create_job(self, params: dict, *, context=None) -> dict:
        return params

    async def update_job(self, job_id: str, patch: dict, *, context=None) -> dict:
        return {"job_id": job_id, **patch}

    async def delete_job(self, job_id: str) -> bool:
        return True

    async def toggle_job(self, job_id: str, enabled: bool) -> dict:
        return {"job_id": job_id, "enabled": enabled}

    async def preview_job(self, job_id: str, count: int = 5) -> list[dict]:
        return []

    async def run_now(self, job_id: str) -> str:
        return "run-id"

    async def status(self) -> dict:
        return {"ok": True}

    async def get_runs(self, job_id: str, limit: int = 20) -> list[dict]:
        return []

    async def wake(self, text: str, *, context=None, mode: str | None = None) -> dict:
        return {"text": text, "mode": mode}


class AgentServerCronToolInjectionTests(unittest.TestCase):
    def test_build_cron_tools_uses_runtime_bridge_backend(self):
        agent = JiuWenClaw()

        agent._cron_runtime.set_backend(_FakeCronBackend())
        tools = agent._build_cron_tools()

        self.assertGreaterEqual(len(tools), 1)
        self.assertEqual(tools[0].card.name, "cron")


if __name__ == "__main__":
    unittest.main()
