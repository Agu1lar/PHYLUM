import unittest
from unittest.mock import AsyncMock

from planner_agent import PlannerAgent
from policy_engine import PolicyEngine
from tool_desktop import DesktopTool


class WindowsAutonomyPolicyTests(unittest.TestCase):
    def test_policy_allows_discovery_actions_without_approval(self):
        engine = PolicyEngine()

        desktop_result = engine.evaluate(
            {
                "runtime_mode": "agentic",
                "current_task": {
                    "id": "task-1",
                    "tool": "desktop",
                    "action": "list_mapped_drives",
                    "params": {},
                },
            }
        )
        filesystem_result = engine.evaluate(
            {
                "runtime_mode": "agentic",
                "current_task": {
                    "id": "task-2",
                    "tool": "filesystem",
                    "action": "list",
                    "params": {"path": r"\\server\share"},
                },
            }
        )
        driver_result = engine.evaluate(
            {
                "runtime_mode": "agentic",
                "current_task": {
                    "id": "task-3",
                    "tool": "driver_manager",
                    "action": "printer_status",
                    "params": {"query": "pantum"},
                },
            }
        )

        self.assertEqual(desktop_result["status"], "allow")
        self.assertFalse(desktop_result["requires_approval"])
        self.assertEqual(filesystem_result["status"], "allow")
        self.assertFalse(filesystem_result["requires_approval"])
        self.assertEqual(driver_result["status"], "allow")
        self.assertFalse(driver_result["requires_approval"])

    def test_policy_requires_approval_for_kill_process(self):
        engine = PolicyEngine()

        result = engine.evaluate(
            {
                "runtime_mode": "agentic",
                "current_task": {
                    "id": "task-4",
                    "tool": "desktop",
                    "action": "kill_process",
                    "params": {"process_name": "WINWORD.EXE"},
                },
            }
        )

        self.assertEqual(result["status"], "require_approval")
        self.assertEqual(result["approval"]["mode"], "single")

    def test_shell_require_admin_still_requests_approval(self):
        engine = PolicyEngine()

        result = engine.evaluate(
            {
                "runtime_mode": "agentic",
                "current_task": {
                    "id": "task-5",
                    "tool": "shell",
                    "action": "run",
                    "params": {"command": "Get-Process", "shell": "powershell", "require_admin": True},
                },
            }
        )

        self.assertEqual(result["status"], "require_approval")
        self.assertTrue(result["approval"]["predicted_effects"][0]["requires_admin"])


class WindowsAutonomyPlannerAndToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_planner_routes_open_folder_to_open_path(self):
        plan, validation = await PlannerAgent().parse(r"open C:\Temp")

        self.assertTrue(validation.ok)
        self.assertEqual(plan.tasks[0].tool, "desktop")
        self.assertEqual(plan.tasks[0].action, "open_path")
        self.assertEqual(plan.tasks[0].params["path"].lower(), r"c:\temp")

    async def test_desktop_tool_open_app_uses_agent_method(self):
        tool = DesktopTool()
        tool.agent.open_app = AsyncMock(return_value={"pid": 99, "process_name": "WINWORD"})

        result = await tool.run({"action": "open_app", "app_name": "word"})

        self.assertTrue(result.ok)
        self.assertIn("Opened app", result.message)
        self.assertEqual(result.details["pid"], 99)
        tool.agent.open_app.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
