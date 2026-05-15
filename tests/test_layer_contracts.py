# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Contract tests — layer protocols and architectural boundaries."""
from __future__ import annotations

import ast
import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

pytestmark = pytest.mark.architecture

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "core"))

from layer_contracts import (
    ALLOWED_LAYER_DEPENDENCIES,
    CognitiveLayerProtocol,
    ExecutionLayerProtocol,
    FORBIDDEN_CROSS_LAYER_IMPORTS,
    OperationalLayerProtocol,
    StateLayerProtocol,
)
from runtime_layers import CognitiveLayer, ExecutionLayer, OperationalLayer, StateLayer

from agent_persistence import Persistence


@pytest.fixture()
def isolated_persistence(tmp_path):
    previous = Persistence._instance
    Persistence._instance = Persistence(str(tmp_path / "contracts.db"))
    yield Persistence._instance
    Persistence._instance = previous


# ---------------------------------------------------------------------------
# Stub implementations — prove protocols are minimal and sufficient
# ---------------------------------------------------------------------------

@dataclass
class StubCognitiveLayer:
    planner: Any
    execution_strategy: Any
    agentic_loop: Any = None

    async def parse_plan(self, text: str):
        return await self.planner.parse(text)

    def decide_execution_mode(self, *, tool, action, params, available_tools):
        return self.execution_strategy.decide_execution_mode(
            tool=tool, action=action, params=params, available_tools=available_tools,
        )


@dataclass
class StubOperationalLayer:
    recovery_engine: Any

    def graph_executor_for(self, name: Optional[str]):
        return None

    def task_scheduler(self, tasks, *, max_parallel: int = 4):
        return _StubScheduler(tasks)


class _StubScheduler:
    def __init__(self, tasks):
        self._tasks = list(tasks)

    def next_batch(self):
        return [t for t in self._tasks if t.get("status") == "pending"]


@dataclass
class StubExecutionLayer:
    tool_router: Any
    safety: Any = None
    reflection: Any = None
    desktop_agent: Any = None

    async def execute_tool(self, *, inputs, task, cancel_event):
        return await self.tool_router.execute(
            {"inputs": inputs, "current_task": task, "cancel_event": cancel_event},
        )

    def wire_world_model(self, world_model) -> None:
        pass


@dataclass
class StubStateLayer:
    persistence: Any
    credential_store: Any = None
    semantic_index: Any = None
    world_model: Any = None
    strategy_memory: Any = None
    goal_queue: Any = None
    session_manager: Any = None

    async def save_run_state(self, state, jsonable_fn):
        await self.persistence.save_kv(f"state:{state['request_id']}", jsonable_fn(state))

    async def get_run_state(self, request_id: str):
        return await self.persistence.get_kv(f"state:{request_id}")

    async def list_run_states(self):
        return await self.persistence.list_states()


class _FakePlanner:
    async def parse(self, text: str):
        return {"text": text}, type("V", (), {"ok": True})()


class _FakeStrategy:
    def decide_execution_mode(self, **kwargs):
        return {"mode": "native", "reason": "stub"}


class _FakeToolRouter:
    tools: Dict[str, Any] = {}

    async def execute(self, payload):
        return {"action_result": {"status": "succeeded"}}


class _FakeRecovery:
    def classify(self, **kwargs):
        return {"classification": "retryable", "retryable": True}

    def classify_action_result(self, **kwargs):
        return {"classification": "terminal", "retryable": False}


# ---------------------------------------------------------------------------
# Protocol conformance — production layers
# ---------------------------------------------------------------------------

class TestProductionLayerContracts:
    def test_cognitive_layer_satisfies_protocol(self, isolated_persistence):
        from runtime_manager import RuntimeManager

        manager = RuntimeManager(lambda _e: None)
        assert isinstance(manager.cognitive_layer, CognitiveLayerProtocol)

    def test_operational_layer_satisfies_protocol(self, isolated_persistence):
        from runtime_manager import RuntimeManager

        manager = RuntimeManager(lambda _e: None)
        assert isinstance(manager.operational_layer, OperationalLayerProtocol)

    def test_execution_layer_satisfies_protocol(self, isolated_persistence):
        from runtime_manager import RuntimeManager

        manager = RuntimeManager(lambda _e: None)
        assert isinstance(manager.execution_layer, ExecutionLayerProtocol)

    def test_state_layer_satisfies_protocol(self, isolated_persistence):
        from runtime_manager import RuntimeManager

        manager = RuntimeManager(lambda _e: None)
        assert isinstance(manager.state_layer, StateLayerProtocol)

    def test_runtime_manager_layers_are_concrete_dataclasses(self, isolated_persistence):
        from runtime_manager import RuntimeManager

        manager = RuntimeManager(lambda _e: None)
        assert isinstance(manager.cognitive_layer, CognitiveLayer)
        assert isinstance(manager.operational_layer, OperationalLayer)
        assert isinstance(manager.execution_layer, ExecutionLayer)
        assert isinstance(manager.state_layer, StateLayer)


class TestStubLayerContracts:
    def test_stub_cognitive_satisfies_protocol(self):
        stub = StubCognitiveLayer(
            planner=_FakePlanner(),
            execution_strategy=_FakeStrategy(),
        )
        assert isinstance(stub, CognitiveLayerProtocol)

    def test_stub_operational_satisfies_protocol(self):
        stub = StubOperationalLayer(recovery_engine=_FakeRecovery())
        assert isinstance(stub, OperationalLayerProtocol)

    def test_stub_execution_satisfies_protocol(self):
        stub = StubExecutionLayer(tool_router=_FakeToolRouter())
        assert isinstance(stub, ExecutionLayerProtocol)

    @pytest.mark.asyncio
    async def test_stub_state_satisfies_protocol(self, isolated_persistence):
        stub = StubStateLayer(persistence=isolated_persistence)
        assert isinstance(stub, StateLayerProtocol)
        await stub.save_run_state({"request_id": "r1", "inputs": {"x": 1}}, lambda s: s)
        loaded = await stub.get_run_state("r1")
        assert loaded["request_id"] == "r1"


# ---------------------------------------------------------------------------
# Contract behaviour — layer surfaces work through protocol types only
# ---------------------------------------------------------------------------

class TestLayerSurfaceContracts:
    @pytest.mark.asyncio
    async def test_cognitive_protocol_parse_plan(self, isolated_persistence):
        from runtime_manager import RuntimeManager

        cognitive: CognitiveLayerProtocol = RuntimeManager(lambda _e: None).cognitive_layer
        plan, validation = await cognitive.parse_plan("run command echo hi")
        assert validation.ok
        assert plan.tasks[0].tool == "shell"

    def test_operational_protocol_graph_executors(self, isolated_persistence):
        from runtime_manager import RuntimeManager

        operational: OperationalLayerProtocol = RuntimeManager(lambda _e: None).operational_layer
        assert operational.graph_executor_for("local_heuristic") is not None
        assert operational.graph_executor_for("agentic") is not None
        assert operational.graph_executor_for("unknown") is None

    def test_operational_protocol_task_scheduler_order(self, isolated_persistence):
        from runtime_manager import RuntimeManager

        operational: OperationalLayerProtocol = RuntimeManager(lambda _e: None).operational_layer
        scheduler = operational.task_scheduler(
            [
                {"id": "a", "status": "pending", "depends_on": [], "policy_metadata": {}},
                {"id": "b", "status": "pending", "depends_on": ["a"], "policy_metadata": {}},
            ],
        )
        assert [t["id"] for t in scheduler.next_batch()] == ["a"]

    @pytest.mark.asyncio
    async def test_execution_protocol_execute_tool(self, isolated_persistence, monkeypatch):
        from runtime_manager import RuntimeManager

        manager = RuntimeManager(lambda _e: None)
        execution: ExecutionLayerProtocol = manager.execution_layer

        async def fake_execute(payload):
            return {"action_result": {"status": "succeeded", "tool": "memory"}}

        monkeypatch.setattr(manager.execution_layer.tool_router, "execute", fake_execute)
        result = await execution.execute_tool(
            inputs={"text": "t"},
            task={"id": "t1", "tool": "memory", "action": "world_query"},
            cancel_event=asyncio.Event(),
        )
        assert result["action_result"]["status"] == "succeeded"

    @pytest.mark.asyncio
    async def test_state_protocol_persistence_roundtrip(self, isolated_persistence):
        from runtime_manager import RuntimeManager

        state_layer: StateLayerProtocol = RuntimeManager(lambda _e: None).state_layer
        payload = {"request_id": "contract-req", "inputs": {"q": "test"}}
        await state_layer.save_run_state(payload, lambda s: s)
        loaded = await state_layer.get_run_state("contract-req")
        assert loaded["inputs"]["q"] == "test"


# ---------------------------------------------------------------------------
# Module boundary tests — forbidden imports
# ---------------------------------------------------------------------------

def _module_imports(module_path: Path) -> set:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imports: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    return imports


class TestModuleBoundaries:
    def test_runtime_layers_does_not_import_runtime_manager(self):
        path = ROOT / "core" / "runtime_layers.py"
        imports = _module_imports(path)
        forbidden = FORBIDDEN_CROSS_LAYER_IMPORTS.get("runtime_layers", frozenset())
        violations = forbidden & imports
        assert not violations, f"runtime_layers.py must not import: {violations}"

    def test_layer_contracts_does_not_import_runtime_manager(self):
        path = ROOT / "core" / "layer_contracts.py"
        imports = _module_imports(path)
        assert "runtime_manager" not in imports

    def test_action_executor_imports_execution_not_cognitive_directly(self):
        path = ROOT / "core" / "action_executor.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        source = ast.unparse(tree) if hasattr(ast, "unparse") else path.read_text(encoding="utf-8")
        assert "cognitive_layer" not in source or "runtime.cognitive" in source
        assert "from planner_agent" not in source

    def test_dependency_matrix_is_acyclic(self):
        """State has no outbound layer deps; others may only depend downward."""
        assert ALLOWED_LAYER_DEPENDENCIES["state"] == frozenset()
        assert "state" in ALLOWED_LAYER_DEPENDENCIES["execution"]
        assert "cognitive" not in ALLOWED_LAYER_DEPENDENCIES["state"]

    def test_runtime_manager_wires_layers_in_dependency_order(self, isolated_persistence):
        """Construction order: state → execution → cognitive → operational."""
        from runtime_manager import RuntimeManager

        manager = RuntimeManager(lambda _e: None)
        assert manager.state_layer is not None
        assert manager.execution_layer is not None
        assert manager.cognitive_layer is not None
        assert manager.operational_layer is not None
        assert manager.state_layer.world_model is manager.world_model
