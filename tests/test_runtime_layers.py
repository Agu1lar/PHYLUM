import asyncio

import pytest

from agent_persistence import Persistence


@pytest.fixture()
def isolated_persistence(tmp_path):
    previous = Persistence._instance
    Persistence._instance = Persistence(str(tmp_path / "layers.db"))
    yield Persistence._instance
    Persistence._instance = previous


@pytest.mark.asyncio
async def test_runtime_manager_exposes_explicit_architecture_layers(isolated_persistence):
    from runtime_layers import CognitiveLayer, ExecutionLayer, OperationalLayer, StateLayer
    from runtime_manager import RuntimeManager

    async def emit(_event):
        pass

    manager = RuntimeManager(emit)

    assert isinstance(manager.cognitive_layer, CognitiveLayer)
    assert isinstance(manager.operational_layer, OperationalLayer)
    assert isinstance(manager.execution_layer, ExecutionLayer)
    assert isinstance(manager.state_layer, StateLayer)

    assert manager.planner is manager.cognitive_layer.planner
    assert manager.agentic_loop is manager.cognitive_layer.agentic_loop
    assert manager.execution_strategy is manager.cognitive_layer.execution_strategy
    assert manager.tool_router is manager.execution_layer.tool_router
    assert manager.world_model is manager.state_layer.world_model
    assert manager.strategy_memory is manager.state_layer.strategy_memory


@pytest.mark.asyncio
async def test_state_layer_persists_run_state(isolated_persistence):
    from runtime_manager import RuntimeManager

    async def emit(_event):
        pass

    manager = RuntimeManager(emit)
    state = manager._new_state(
        "req-layer",
        {"text": "hello"},
        runtime_mode="heuristic",
        provider=None,
        model=None,
    )
    await manager._persist_state(state)

    loaded = await manager.state_layer.get_run_state("req-layer")
    assert loaded["request_id"] == "req-layer"
    assert loaded["inputs"]["text"] == "hello"


def test_operational_layer_owns_graph_executors_and_scheduler(isolated_persistence):
    from runtime_manager import RuntimeManager

    async def emit(_event):
        pass

    manager = RuntimeManager(emit)
    assert manager._graph_executor_for({"_pipeline_graph": "local_heuristic"}) is manager.operational_layer.local_executor
    assert manager._graph_executor_for({"_pipeline_graph": "agentic"}) is manager.operational_layer.agentic_executor

    scheduler = manager.operational_layer.task_scheduler(
        [
            {"id": "a", "status": "pending", "depends_on": [], "policy_metadata": {"mutates_state": False}},
            {"id": "b", "status": "pending", "depends_on": ["a"], "policy_metadata": {"mutates_state": False}},
        ]
    )
    assert [task["id"] for task in scheduler.next_batch()] == ["a"]


@pytest.mark.asyncio
async def test_cognitive_layer_plans_and_applies_strategy(isolated_persistence):
    from runtime_manager import RuntimeManager

    async def emit(_event):
        pass

    manager = RuntimeManager(emit)
    plan, validation = await manager.cognitive_layer.parse_plan("run command Get-Date")

    assert validation.ok is True
    assert plan.tasks[0].tool == "shell"

    decision = manager.cognitive_layer.decide_execution_mode(
        tool="filesystem",
        action="read",
        params={"path": "C:\\data.csv"},
        available_tools=["filesystem", "artifact"],
    )
    assert decision["mode"] == "internal"


@pytest.mark.asyncio
async def test_execution_layer_dispatches_tools(monkeypatch, isolated_persistence):
    from runtime_manager import RuntimeManager

    async def emit(_event):
        pass

    manager = RuntimeManager(emit)
    calls = []

    async def fake_execute(payload):
        calls.append(payload)
        return {"action_result": {"status": "succeeded"}}

    monkeypatch.setattr(manager.execution_layer.tool_router, "execute", fake_execute)
    result = await manager.execution_layer.execute_tool(
        inputs={"text": "test"},
        task={"id": "t1", "tool": "memory", "action": "world_query"},
        cancel_event=asyncio.Event(),
    )

    assert result["action_result"]["status"] == "succeeded"
    assert calls[0]["current_task"]["id"] == "t1"
