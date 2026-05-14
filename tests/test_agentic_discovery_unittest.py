import unittest
from unittest.mock import patch

from agentic_loop import AgenticLoop
from canonical_tools import action_metadata, tool_definitions
from desktop_windows_agent import DesktopWindowsAgent
from planner_agent import PlannerAgent


class AgenticDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_planner_parses_list_explorer_windows_keyword(self):
        agent = PlannerAgent()

        plan, validation = await agent.parse("list explorer windows")

        self.assertTrue(validation.ok)
        self.assertEqual(plan.tasks[0].tool, "desktop")
        self.assertEqual(plan.tasks[0].action, "list_explorer_windows")

    def test_desktop_tool_definition_exposes_list_explorer_windows(self):
        desktop_tool = next(item for item in tool_definitions() if item["function"]["name"] == "desktop")
        actions = desktop_tool["function"]["parameters"]["properties"]["action"]["enum"]

        self.assertIn("list_explorer_windows", actions)
        self.assertIn("open_app", actions)
        self.assertIn("wait_for_window", actions)
        self.assertIn("list_mapped_drives", actions)
        self.assertEqual(action_metadata("desktop", "list_explorer_windows")["approval_mode"], "none")
        self.assertEqual(action_metadata("desktop", "open_app")["approval_mode"], "none")
        self.assertEqual(action_metadata("desktop", "kill_process")["approval_mode"], "single")

    def test_agentic_prompt_explicitly_guides_shell_and_explorer_discovery(self):
        prompt = AgenticLoop(client=None, safety=None, tool_router=None, reflection=None)._system_prompt()

        self.assertIn("shell", prompt)
        self.assertIn("PowerShell", prompt)
        self.assertIn("desktop", prompt)
        self.assertIn("discover", prompt.lower())
        self.assertIn("ConvertTo-Json", prompt)

    async def test_planner_routes_open_word_to_open_app(self):
        plan, validation = await PlannerAgent().parse("open word")

        self.assertTrue(validation.ok)
        self.assertEqual(plan.tasks[0].tool, "desktop")
        self.assertEqual(plan.tasks[0].action, "open_app")
        self.assertEqual(plan.tasks[0].params["app_name"], "word")

    async def test_planner_routes_open_url_to_browser(self):
        plan, validation = await PlannerAgent().parse("open https://example.com")

        self.assertTrue(validation.ok)
        self.assertEqual(plan.tasks[0].tool, "browser")
        self.assertEqual(plan.tasks[0].action, "open_page")
        self.assertEqual(plan.tasks[0].params["url"], "https://example.com")

    async def test_planner_routes_open_unc_document_to_open_file(self):
        plan, validation = await PlannerAgent().parse(r"open \\server\share\contract.docx")

        self.assertTrue(validation.ok)
        self.assertEqual(plan.tasks[0].tool, "desktop")
        self.assertEqual(plan.tasks[0].action, "open_file")
        self.assertEqual(plan.tasks[0].params["path"], r"\\server\share\contract.docx")

    async def test_desktop_agent_lists_explorer_windows_from_powershell(self):
        completed = type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": '[{"hwnd":123,"title":"logistica","process_name":"explorer.exe","visible":true,"location_path":"\\\\\\\\192.168.5.200\\\\logística","location_url":"file://192.168.5.200/log%C3%ADstica","executable_path":"C:\\\\Windows\\\\explorer.exe"}]',
                "stderr": "",
            },
        )()

        with patch("desktop_windows_agent.subprocess.run", return_value=completed):
            result = await DesktopWindowsAgent().list_explorer_windows()

        self.assertEqual(len(result["windows"]), 1)
        self.assertEqual(result["windows"][0]["title"], "logistica")
        self.assertEqual(result["windows"][0]["location_path"], r"\\192.168.5.200\logística")

    async def test_desktop_agent_opens_app_with_resolved_target(self):
        with patch("desktop_windows_agent._resolve_app_launch_target", return_value=r"C:\Program Files\Word\WINWORD.EXE"), patch(
            "desktop_windows_agent._start_process_via_powershell",
            return_value={"pid": 4321, "process_name": "WINWORD", "path": r"C:\Program Files\Word\WINWORD.EXE"},
        ):
            result = await DesktopWindowsAgent().open_app(app_name="word")

        self.assertEqual(result["pid"], 4321)
        self.assertEqual(result["target"], r"C:\Program Files\Word\WINWORD.EXE")

    def test_launch_helper_handles_empty_argument_list(self):
        popen = type("PopenStub", (), {"pid": 4321})
        process = type("ProcessStub", (), {"name": lambda self: "WINWORD.EXE"})()

        with patch("desktop_windows_agent.subprocess.Popen", return_value=popen) as mocked_popen, patch(
            "desktop_windows_agent.psutil.Process",
            return_value=process,
        ):
            result = DesktopWindowsAgent()
            launch = __import__("desktop_windows_agent")._start_process_via_powershell
            payload = launch(r"C:\Program Files\Microsoft Office\Root\Office16\WINWORD.EXE", None)

        mocked_popen.assert_called_once()
        self.assertEqual(payload["pid"], 4321)
        self.assertEqual(payload["process_name"], "WINWORD.EXE")


if __name__ == "__main__":
    unittest.main()
