import asyncio

import pytest

from agent_persistence import Persistence
from canonical_tools import supported_tools, tool_definitions
from nodes_reflection import ReflectionNode
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
    assert result["safety"]["approval"]["mode"] == "single"


@pytest.mark.asyncio
async def test_safety_node_requires_double_confirmation_for_delete():
    node = SafetyNode("safety")
    result = await node.execute(
        {
            "runtime_mode": "agentic",
            "current_task": {
                "id": "task-delete",
                "tool": "filesystem",
                "action": "delete",
                "params": {"path": r"C:\Temp\arquivo.txt"},
            },
        }
    )

    assert result["safety"]["status"] == "require_approval"
    assert result["safety"]["approval"]["mode"] == "double"
    assert any(effect["operation"] == "delete_file" for effect in result["safety"]["approval"]["predicted_effects"])


@pytest.mark.asyncio
async def test_reflection_handles_missing_structured_command_result():
    node = ReflectionNode("reflection")

    result = await node.execute(
        {
            "current_task": {"id": "task-1", "recovery": None},
            "current_task_result": {
                "tool_result": {
                    "structured": {
                        "ok": False,
                        "result": None,
                        "risk": {"level": "low", "tags": [], "reason": "no specific match"},
                    }
                }
            },
        }
    )

    assert result["reflection"]["verdict"] == "failed"
    assert result["reflection"]["details"]["stdout"] is None
    assert result["reflection"]["details"]["stderr"] is None


@pytest.mark.asyncio
async def test_reflection_uses_tool_error_in_failed_summary():
    node = ReflectionNode("reflection")

    result = await node.execute(
        {
            "current_task": {"id": "agentic-1-test", "recovery": None},
            "current_task_result": {
                "tool_result": {
                    "success": False,
                    "message": "printer_status",
                    "details": {
                        "error": "validation: provider unavailable",
                        "stderr": "",
                    },
                }
            },
        }
    )

    assert result["reflection"]["verdict"] == "failed"
    assert "validation: provider unavailable" in result["reflection"]["summary"]


@pytest.mark.asyncio
async def test_runtime_manager_preserves_tool_failure_when_structured_result_is_null(monkeypatch, isolated_persistence):
    events = []

    async def emitter(message):
        events.append(message)

    manager = RuntimeManager(emitter)

    async def failing_execute(_state):
        return {
            "tool": "shell",
            "action": "run",
            "task_id": "task-1",
            "tool_result": {
                "structured": {
                    "ok": False,
                    "meta": {"attempt": 1},
                    "result": None,
                    "risk": {"level": "low", "tags": [], "reason": "simulated failure"},
                    "error": "",
                    "cancelled": False,
                    "raw": None,
                }
            },
        }

    monkeypatch.setattr(manager.tool_router, "execute", failing_execute)

    request_id = await manager.submit_run(
        {"text": "run command Get-Date", "allow_local_execution": True},
        runtime_mode="heuristic",
    )
    final_state = await manager.wait_for_run(request_id, timeout=10)

    assert final_state["status"] == "failed"
    assert "NoneType" not in (final_state["error"] or "")
    assert final_state["outputs"]["final_reflection"]["verdict"] == "failed"
    assert "Nao consegui concluir" in final_state["outputs"]["final_reflection"]["summary"]
    assert any(event["type"] == "task_retry_scheduled" for event in events)
    assert any(event["type"] == "run_failed" for event in events)
    assert len([event for event in events if event["type"] == "run_failed"]) == 1
    run_failed_event = next(event for event in events if event["type"] == "run_failed")
    assert run_failed_event["payload"]["user_message"] == final_state["outputs"]["final_reflection"]["summary"]


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


@pytest.mark.asyncio
async def test_runtime_manager_allows_partial_local_desktop_launch(monkeypatch, isolated_persistence):
    events = []

    async def emitter(message):
        events.append(message)

    manager = RuntimeManager(emitter)

    async def partial_execute(_state):
        return {
            "tool": "desktop",
            "action": "open_app",
            "task_id": "task-1",
            "tool_result": {"status": "succeeded", "summary": "Abri o app word.", "tool": "desktop", "action": "open_app", "data": {"pid": 101}},
            "action_result": {
                "status": "succeeded",
                "summary": "Abri o app word.",
                "tool": "desktop",
                "action": "open_app",
                "semantic_type": "execution",
                "target": {"app_name": "word"},
                "data": {"pid": 101},
                "effects": {"changed": False, "predicted_effects": [], "artifacts": [], "before": None, "after": None, "rollback": {"available": False, "reference": None}},
                "diagnostics": {},
            },
        }

    monkeypatch.setattr(manager.tool_router, "execute", partial_execute)

    request_id = await manager.submit_run({"text": "open word", "allow_local_execution": True}, runtime_mode="heuristic")
    final_state = await manager.wait_for_run(request_id, timeout=10)

    assert final_state["status"] == "completed"
    assert final_state["tasks"][0]["status"] == "partial"
    assert final_state["outputs"]["final_reflection"]["verdict"] == "success"
    assert any(event["type"] == "task_finished" and event["payload"]["status"] == "partial" for event in events)


@pytest.mark.asyncio
async def test_runtime_manager_creates_run_scope_grant_from_approval(isolated_persistence):
    events = []

    async def emitter(message):
        events.append(message)

    manager = RuntimeManager(emitter)
    state = manager._new_state("run-1", {"text": "preencher documento"}, runtime_mode="agentic", provider=None, model=None)
    task = {
        "id": "task-approval",
        "title": "Preencher campo do Word",
        "tool": "windows_ui",
        "action": "set_text",
        "params": {"title": "Word", "process_name": "WINWORD.EXE", "selector": {"title": "Nome"}, "text": "Maria"},
        "policy_metadata": {"approval_mode": "single"},
        "status": "waiting_approval",
        "attempt": 0,
        "approval_id": "approval-1",
        "approval_granted": False,
    }
    approval = {
        "approval_id": "approval-1",
        "request_id": "run-1",
        "task_id": "task-approval",
        "title": "Approve task",
        "reason": "Confirmar alteracao",
        "status": "pending",
        "risk": {"level": "medium"},
        "details": {"available_scopes": ["single", "run_scope"]},
        "approval_mode": "single",
    }
    state["tasks"] = [task]
    state["approvals"] = [approval]
    manager.active_runs["run-1"] = state
    await manager.persistence.create_approval("approval-1", "run-1", "", approval, task_id="task-approval")

    result = await manager.resolve_approval("approval-1", "approved", scope="run_scope")

    assert result["scope"] == "run_scope"
    assert result["grant"]["status"] == "active"
    assert state["approval_grants"][0]["family"] == "interactive_desktop"
    assert any(event["type"] == "approval_grant_created" for event in events)


@pytest.mark.asyncio
async def test_runtime_manager_restores_focus_before_continuing(monkeypatch, isolated_persistence):
    async def emitter(_message):
        return None

    manager = RuntimeManager(emitter)
    focus_calls = []

    async def fake_focus_window(*, hwnd=None, title=None):
        focus_calls.append({"hwnd": hwnd, "title": title})
        return {"window": {"hwnd": hwnd, "title": title}}

    monkeypatch.setattr(manager.desktop_agent, "focus_window", fake_focus_window)

    state = manager._new_state("run-ctx", {"text": "continuar word"}, runtime_mode="agentic", provider=None, model=None)
    task = {
        "id": "task-ctx",
        "tool": "windows_ui",
        "action": "invoke_element",
        "params": {"selector": {"title": "Documento em branco"}},
        "pause_context": {"window": {"title": "Word"}, "ui_target": {"selector": {"title": "Documento em branco"}}},
    }

    await manager._restore_execution_context(state, task)

    assert focus_calls == [{"hwnd": None, "title": "Word"}]
