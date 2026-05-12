import pytest

from agent_persistence import Persistence
from policy_engine import PolicyEngine
from risk_classifier import classify
from runtime_manager import RuntimeManager


@pytest.fixture()
def isolated_persistence(tmp_path):
    previous = Persistence._instance
    Persistence._instance = Persistence(str(tmp_path / "agent_state.db"))
    yield Persistence._instance
    Persistence._instance = previous


def test_risk_classifier_marks_simple_inspection_shell_as_low():
    result = classify("Get-Date")

    assert result["level"] == "low"
    assert "inspection" in result["tags"]


def test_risk_classifier_marks_package_install_as_medium():
    result = classify("winget install Git.Git")

    assert result["level"] == "medium"
    assert "installer" in result["tags"]


def test_risk_classifier_does_not_treat_format_table_as_destructive():
    result = classify("Get-PrinterPort | Select-Object Name, Description, PrinterHostAddress | Format-Table -AutoSize")

    assert result["level"] == "low"
    assert "printer" in result["tags"]


@pytest.mark.asyncio
async def test_policy_engine_allows_low_risk_shell_in_agentic_mode():
    policy = PolicyEngine()

    result = policy.evaluate(
        {
            "runtime_mode": "agentic",
            "current_task": {
                "id": "agentic-shell-1",
                "tool": "shell",
                "action": "run",
                "params": {"command": "Get-Date"},
            },
        }
    )

    assert result["status"] == "allow"
    assert result["risk"]["level"] == "low"


@pytest.mark.asyncio
async def test_policy_engine_requires_approval_for_medium_risk_shell():
    policy = PolicyEngine()

    result = policy.evaluate(
        {
            "runtime_mode": "agentic",
            "current_task": {
                "id": "agentic-shell-2",
                "tool": "shell",
                "action": "run",
                "params": {"command": "winget install Git.Git"},
            },
        }
    )

    assert result["status"] == "require_approval"
    assert result["risk"]["level"] == "medium"


@pytest.mark.asyncio
async def test_runtime_manager_local_shell_run_does_not_wait_for_approval(monkeypatch, isolated_persistence):
    events = []

    async def emitter(message):
        events.append(message)

    manager = RuntimeManager(emitter)

    async def successful_execute(_state):
        return {
            "tool": "shell",
            "action": "run",
            "task_id": "task-shell",
            "tool_result": {
                "structured": {
                    "ok": True,
                    "meta": {"attempt": 1},
                    "result": {"stdout": "now", "stderr": "", "returncode": 0},
                    "risk": {"level": "low", "tags": ["inspection"], "reason": "safe inspection command"},
                    "error": None,
                    "cancelled": False,
                }
            },
            "action_result": {
                "status": "succeeded",
                "summary": "Command executed successfully.",
                "tool": "shell",
                "action": "run",
                "semantic_type": "command",
                "target": {"command": "Get-Date"},
                "data": {"stdout": "now", "stderr": ""},
                "effects": {
                    "changed": False,
                    "predicted_effects": [],
                    "artifacts": [],
                    "before": None,
                    "after": None,
                    "rollback": {"available": False, "reference": None},
                },
                "diagnostics": {},
            },
        }

    monkeypatch.setattr(manager.tool_router, "execute", successful_execute)

    request_id = await manager.submit_run(
        {"text": "run command Get-Date", "allow_local_execution": True},
        runtime_mode="agentic",
    )
    final_state = await manager.wait_for_run(request_id, timeout=10)

    assert final_state["status"] == "completed"
    assert final_state["approvals"] == []
    assert not any(event["type"] == "approval_requested" for event in events)
