import unittest
from pathlib import Path
from tempfile import gettempdir
from uuid import uuid4

from agent_persistence import Persistence
from runtime_manager import RuntimeManager


class ChatHistoryBackendTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous = Persistence._instance
        self.db_path = Path(gettempdir()) / f"agent-state-history-{uuid4().hex}.db"
        self.persistence = Persistence(str(self.db_path))
        Persistence._instance = self.persistence
        self.events = []

        async def emitter(message):
            self.events.append(message)

        self.manager = RuntimeManager(emitter)

    async def asyncTearDown(self):
        await self.persistence.delete_kv("state:run-history-test")
        await self.persistence.delete_approvals("run-history-test")
        Persistence._instance = self.previous
        try:
            self.db_path.unlink(missing_ok=True)
        except Exception:
            pass

    async def test_delete_run_removes_state_and_approvals(self):
        state = self.manager._new_state(
            "run-history-test",
            {"text": "oi"},
            runtime_mode="agentic",
            provider=None,
            model=None,
        )
        self.manager.active_runs["run-history-test"] = state
        await self.persistence.save_kv("state:run-history-test", state)
        await self.persistence.create_approval(
            "approval-history-test",
            "run-history-test",
            "",
            {"approval_id": "approval-history-test", "request_id": "run-history-test"},
            task_id=None,
        )

        result = await self.manager.delete_run("run-history-test")

        self.assertTrue(result["deleted"])
        self.assertIsNone(await self.persistence.get_kv("state:run-history-test"))
        self.assertEqual(await self.persistence.list_approvals(request_id="run-history-test"), [])
        self.assertTrue(any(event["type"] == "run_deleted" for event in self.events))


if __name__ == "__main__":
    unittest.main()
