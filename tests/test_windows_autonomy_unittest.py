import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from planner_agent import PlannerAgent
from policy_engine import PolicyEngine
from tool_desktop import DesktopTool
from windows_ui_agent import WindowsUiAgent
from windows_ui_models import WindowsUiSelector


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

    def test_policy_uses_active_run_scope_grant_for_interactive_desktop(self):
        engine = PolicyEngine()

        result = engine.evaluate(
            {
                "runtime_mode": "agentic",
                "approval_grants": [
                    {
                        "grant_id": "grant-1",
                        "scope": "run_scope",
                        "status": "active",
                        "family": "interactive_desktop",
                        "max_risk_level": "medium",
                    }
                ],
                "current_task": {
                    "id": "task-6",
                    "tool": "windows_ui",
                    "action": "invoke_element",
                    "params": {"title": "Word", "selector": {"title": "Blank document"}},
                },
            }
        )

        self.assertEqual(result["status"], "allow")
        self.assertEqual(result["grant"]["grant_id"], "grant-1")

    def test_policy_does_not_use_run_scope_grant_for_kill_process(self):
        engine = PolicyEngine()

        result = engine.evaluate(
            {
                "runtime_mode": "agentic",
                "approval_grants": [
                    {
                        "grant_id": "grant-1",
                        "scope": "run_scope",
                        "status": "active",
                        "family": "interactive_desktop",
                        "max_risk_level": "medium",
                    }
                ],
                "current_task": {
                    "id": "task-7",
                    "tool": "desktop",
                    "action": "kill_process",
                    "params": {"process_name": "WINWORD.EXE"},
                },
            }
        )

        self.assertEqual(result["status"], "require_approval")


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

        self.assertEqual(result.status, "succeeded")
        self.assertIn("Abri o app", result.summary)
        self.assertEqual(result.data["pid"], 99)
        tool.agent.open_app.assert_awaited_once()

    async def test_desktop_tool_open_app_preserves_not_found_error(self):
        tool = DesktopTool()
        tool.agent.open_app = AsyncMock(side_effect=FileNotFoundError("could not resolve an executable for 'word'"))

        result = await tool.run({"action": "open_app", "app_name": "word"})

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.issue.kind, "app_not_found")
        self.assertIn("could not resolve", result.summary)

    async def test_windows_ui_invoke_falls_back_to_click_input(self):
        agent = WindowsUiAgent()

        class FakeTarget:
            def __init__(self):
                self.clicked = False

            def set_focus(self):
                return None

            def invoke(self):
                raise RuntimeError("invoke failed")

            def click_input(self):
                self.clicked = True

        fake_target = FakeTarget()

        with patch.object(agent, "_resolve_candidates", return_value=(None, [fake_target])), patch.object(
            agent,
            "_snapshot",
            return_value=type("Snapshot", (), {"dict": lambda self: {"title": "Documento em branco"}})(),
        ):
            result = await agent.invoke_element(title="Word", selector={"title": "Documento em branco"})

        self.assertTrue(fake_target.clicked)
        self.assertEqual(result["method"], "click_input")

    async def test_windows_ui_ranks_composite_anchors_before_escalating(self):
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        agent = WindowsUiAgent(selector_memory_path=Path(temp_dir.name) / "selectors.json")

        class FakeInfo:
            def __init__(self, name, control_type, automation_id=None, class_name="Button", handle=None):
                self.name = name
                self.control_type = control_type
                self.automation_id = automation_id
                self.class_name = class_name
                self.handle = handle
                self.process_id = None

        class FakeWrapper:
            def __init__(self, name, control_type, automation_id=None, parent=None, handle=None):
                self.element_info = FakeInfo(name, control_type, automation_id=automation_id, handle=handle)
                self._parent = parent
                self._children = []
                if parent:
                    parent._children.append(self)

            def parent(self):
                return self._parent

            def children(self):
                return list(self._children)

            def descendants(self):
                result = []
                for child in self._children:
                    result.append(child)
                    result.extend(child.descendants())
                return result

            def is_enabled(self):
                return True

            def is_visible(self):
                return True

        window = FakeWrapper("Settings", "Window", handle=1)
        account_panel = FakeWrapper("Account", "Pane", parent=window, handle=2)
        billing_panel = FakeWrapper("Billing", "Pane", parent=window, handle=3)
        FakeWrapper("Email", "Text", parent=account_panel, handle=4)
        save_account = FakeWrapper("Save", "Button", parent=account_panel, handle=5)
        save_billing = FakeWrapper("Save", "Button", parent=billing_panel, handle=6)

        with patch.object(agent, "_resolve_window", return_value=window):
            result = await agent.find_element(
                title="Settings",
                selector={"title": "Save", "control_type": "Button", "parent_title": "Account", "sibling_titles": ["Email"]},
            )

        self.assertTrue(result["ambiguity_resolved"])
        self.assertEqual(result["best_match"]["hwnd"], 5)
        self.assertGreater(result["best_match"]["match_score"], result["matches"][1]["match_score"])

    def test_windows_ui_selector_matching_is_resilient_to_partial_titles(self):
        agent = WindowsUiAgent()

        element = type(
            "Element",
            (),
            {
                "title": "Save changes",
                "control_type": "Button",
                "auto_id": None,
                "class_name": "Button",
                "process_name": "app.exe",
                "hwnd": 10,
                "parent": {},
                "ancestors": [],
                "siblings": [],
            },
        )()

        score, reasons = agent._score_selector(element, WindowsUiSelector(title="Save", control_type="Button"))

        self.assertGreater(score, 0.7)
        self.assertIn("title", reasons)


if __name__ == "__main__":
    unittest.main()
