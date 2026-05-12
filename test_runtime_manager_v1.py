import asyncio
from pathlib import Path

import pytest

from agent_persistence import Persistence
from nodes_safety import SafetyNode
from planner_agent import PlannerAgent
from runtime_manager import RuntimeManager


@pytest.fixture()
def isolated_persistence(tmp_path):
    previous = Persistence._instance
    Persistence._instance = Persistence(str(tmp_path / "agent_state.db"))
    yield Persistence._instance
    Persistence._instance = previous


@pytest.mark.asyncio
async def test_planner_agent_parses_vertical_slice_commands():
    planner = PlannerAgent(supported_tools=["shell", "filesystem", "memory"])

    shell_plan, shell_validation = await planner.parse('run command Write-Output "hello"')
    fs_plan, fs_validation = await planner.parse(r"write hello to C:\Temp\vertical-slice.txt")
    memory_plan, memory_validation = await planner.parse("remember project is agente")

    assert shell_validation.ok is True
    assert shell_plan.tasks[0].tool == "shell"
    assert shell_plan.tasks[0].params["command"] == 'write-output "hello"'

    assert fs_validation.ok is True
    assert fs_plan.tasks[0].tool == "filesystem"
    assert fs_plan.tasks[0].action == "write"
    assert fs_plan.tasks[0].params["path"].lower().endswith(r"vertical-slice.txt")

    assert memory_validation.ok is True
    assert memory_plan.tasks[0].tool == "memory"
    assert memory_plan.tasks[0].params["key"] == "project"


@pytest.mark.asyncio
async def test_safety_node_requires_approval_for_mutating_filesystem():
    node = SafetyNode("safety")
    result = await node.execute(
        {
            "current_task": {
                "id": "task-1",
                "tool": "filesystem",
                "action": "write",
                "params": {"path": r"C:\Temp\file.txt", "content": "hello"},
            }
        }
    )

    assert result["safety"]["status"] == "require_approval"
    assert result["safety"]["requires_approval"] is True


@pytest.mark.asyncio
async def test_runtime_manager_emits_approval_and_finishes_after_approval(tmp_path, isolated_persistence):
    events = []

    async def emitter(message):
        events.append(message)

    manager = RuntimeManager(emitter)
    target_file = tmp_path / "runtime-manager-v1.txt"

    request_id = await manager.submit_run({"text": f"write hello to {target_file}", "allow_local_execution": True})

    for _ in range(100):
        state = await manager.get_state(request_id)
        if state and state["approvals"]:
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("approval was not requested")

    approval_id = state["approvals"][0]["approval_id"]
    approval_text = (state["approvals"][0]["title"] + " " + state["approvals"][0]["reason"]).lower()
    assert str(target_file).lower() in approval_text
    await manager.resolve_approval(approval_id, "approved")
    final_state = await manager.wait_for_run(request_id, timeout=10)

    assert final_state["status"] == "completed"
    assert Path(target_file).read_text(encoding="utf-8") == "hello"
    assert [event["type"] for event in events] == [
        "run_started",
        "task_planned",
        "approval_requested",
        "approval_resolved",
        "task_started",
        "task_finished",
        "run_finished",
    ]


@pytest.mark.asyncio
async def test_runtime_manager_fails_when_approval_is_rejected(tmp_path, isolated_persistence):
    events = []

    async def emitter(message):
        events.append(message)

    manager = RuntimeManager(emitter)
    target_file = tmp_path / "runtime-manager-rejected.txt"

    request_id = await manager.submit_run({"text": f"write hello to {target_file}", "allow_local_execution": True})

    for _ in range(100):
        state = await manager.get_state(request_id)
        if state and state["approvals"]:
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("approval was not requested")

    approval_id = state["approvals"][0]["approval_id"]
    await manager.resolve_approval(approval_id, "rejected")
    final_state = await manager.wait_for_run(request_id, timeout=10)

    assert final_state["status"] == "failed"
    assert final_state["error"] == "approval rejected"
    assert "Nao continuei porque a acao que precisava da sua aprovacao foi rejeitada." in final_state["outputs"]["final_reflection"]["summary"]
    assert not target_file.exists()
    assert events[-1]["type"] == "run_failed"
    assert "Nao continuei porque a acao que precisava da sua aprovacao foi rejeitada." in events[-1]["payload"]["user_message"]


@pytest.mark.asyncio
async def test_runtime_manager_requests_approval_instead_of_failing_for_outside_sandbox_list(isolated_persistence):
    events = []

    async def emitter(message):
        events.append(message)

    manager = RuntimeManager(emitter)
    request_id = await manager.submit_run({"text": r"list files C:\Windows", "allow_local_execution": True})

    for _ in range(100):
        state = await manager.get_state(request_id)
        if state and state["approvals"]:
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("approval was not requested")

    assert state["status"] == "awaiting_approval"
    assert any(r"c:\windows" in (approval["title"] + " " + approval["reason"]).lower() for approval in state["approvals"])
    assert not any(event["type"] == "run_failed" for event in events)

    approval_id = state["approvals"][0]["approval_id"]
    await manager.resolve_approval(approval_id, "approved")
    final_state = await manager.wait_for_run(request_id, timeout=10)

    assert final_state["status"] == "completed"
    assert final_state["tasks"][0]["result"]["tool_result"]["details"]["items"] is not None


@pytest.mark.asyncio
async def test_runtime_manager_handles_greeting_without_failing(isolated_persistence):
    events = []

    async def emitter(message):
        events.append(message)

    manager = RuntimeManager(emitter)
    request_id = await manager.submit_run({"text": "ola"})
    final_state = await manager.wait_for_run(request_id, timeout=10)

    assert final_state["status"] == "completed"
    assert final_state["tasks"] == []
    assert "Ola!" in final_state["outputs"]["final_reflection"]["summary"]
    assert [event["type"] for event in events] == ["run_started", "run_finished"]
