import asyncio

import pytest

from agent_persistence import Persistence
from canonical_tools import supported_tools, tool_definitions
from nodes_safety import SafetyNode
from nodes_tool_router import ToolRouterNode
from runtime_manager import RuntimeManager


@pytest.fixture()
def isolated_persistence(tmp_path):
    previous = Persistence._instance
    Persistence._instance = Persistence(str(tmp_path / "agent_state.db"))
    yield Persistence._instance
    Persistence._instance = previous


@pytest.mark.asyncio
async def test_tool_router_exposes_canonical_tools():
    router = ToolRouterNode()

    assert set(router.tools.keys()) == set(supported_tools())
    assert {item["function"]["name"] for item in tool_definitions()} == set(supported_tools())


@pytest.mark.asyncio
async def test_safety_node_requires_approval_for_service_action():
    node = SafetyNode("safety")
    result = await node.execute(
        {
            "current_task": {
                "id": "task-1",
                "tool": "desktop",
                "action": "service_action",
                "params": {"service_name": "Spooler", "service_action": "restart"},
            }
        }
    )

    assert result["safety"]["status"] == "require_approval"
    assert result["safety"]["risk"]["level"] == "high"


@pytest.mark.asyncio
async def test_runtime_manager_cancels_running_task(monkeypatch, isolated_persistence):
    events = []

    async def emitter(message):
        events.append(message)

    manager = RuntimeManager(emitter)

    async def slow_execute(state):
        await state["cancel_event"].wait()
        raise asyncio.CancelledError()

    monkeypatch.setattr(manager.tool_router, "execute", slow_execute)

    request_id = await manager.submit_run({"text": "run command Write-Output hello", "allow_local_execution": True})

    for _ in range(100):
        state = await manager.get_state(request_id)
        if state and (
            state.get("current_task_id")
            or any(task.get("status") == "running" for task in state.get("tasks", []))
            or any(event["type"] == "task_started" for event in events)
        ):
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("task did not start")

    result = await manager.cancel_run(request_id)
    final_state = await manager.wait_for_run(request_id, timeout=10)

    assert result["status"] == "cancelling"
    assert final_state["status"] == "cancelled"
    assert final_state["error"] == "cancelled"
    assert any(event["type"] == "run_cancellation_requested" for event in events)
    assert any(event["type"] == "task_cancelled" for event in events)
    assert any(event["type"] == "run_cancelled" for event in events)
