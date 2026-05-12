import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch

from command_validator import CommandValidator
from models import CommandResult, ExecutionMeta, ExecutionRisk, StructuredResponse
from risk_classifier import explain_command
from shell_executor import ShellExecutor
from tool_driver import DriverManagerTool
from tool_registry import ToolRegistry


class CommandApprovalBehaviorTests(unittest.TestCase):
    def test_validator_no_longer_blocks_previously_blacklisted_commands(self):
        allowed, reason = CommandValidator().validate("shutdown /r /t 0")

        self.assertTrue(allowed)
        self.assertIsNone(reason)

    def test_explain_command_returns_human_summary_for_network_lookup(self):
        explanation = explain_command('ipconfig | findstr /i "IPv4"')

        self.assertIn("IPv4", explanation)

    def test_shell_failure_message_uses_exit_code_when_stderr_is_empty(self):
        registry = ToolRegistry()

        message = registry._shell_failure_message(
            {"error": "non-zero-exit"},
            {"stderr": "", "returncode": 1},
        )

        self.assertEqual(message, "O comando terminou com codigo de saida 1.")


class AsyncCommandBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def test_shell_executor_keeps_exception_type_when_message_is_empty(self):
        async def fake_spawn(*_args, **_kwargs):
            raise RuntimeError()

        with patch.object(ShellExecutor, "_spawn", fake_spawn):
            result = await ShellExecutor(default_retries=1).execute("Get-Date", retries=1)

        self.assertEqual(result.error, "RuntimeError")
        self.assertEqual(result.raw["exception_type"], "RuntimeError")

    async def test_driver_printer_status_without_query_uses_simpler_command(self):
        tool = DriverManagerTool()
        tool.shell.execute = AsyncMock(
            return_value=StructuredResponse(
                ok=True,
                meta=ExecutionMeta(
                    attempted_at=datetime.utcnow(),
                    attempt=1,
                    retries=1,
                    timeout_seconds=30,
                    shell="powershell",
                    command="Get-Printer | Select-Object Name, DriverName, PrinterStatus, PortName | ConvertTo-Json -Depth 3",
                    allowed=True,
                    admin_requested=False,
                    admin_granted=False,
                ),
                result=CommandResult(
                    stdout="[]",
                    stderr="",
                    returncode=0,
                    duration_seconds=0.1,
                    pid=123,
                ),
                risk=ExecutionRisk(level="low", tags=["inspection"], reason="safe inspection command"),
                error=None,
                cancelled=False,
                raw={},
            )
        )

        result = await tool.run({"action": "printer_status"})

        self.assertEqual(result.status, "succeeded")
        self.assertNotIn("Where-Object", result.diagnostics["command"])


if __name__ == "__main__":
    unittest.main()
