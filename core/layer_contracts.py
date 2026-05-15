# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Layer contracts — explicit interfaces between runtime architectural layers.

Each layer exposes a Protocol (interface) that callers depend on instead of
concrete implementations.  Contract tests in ``tests/test_layer_contracts.py``
verify that production layers satisfy these protocols and that dependency
direction rules are respected.

Dependency direction (allowed calls):

    StateLayer  ← read/write by all layers (via injection, never imports Cognitive)
    ExecutionLayer ← OperationalLayer, CognitiveLayer (via RuntimeManager)
    CognitiveLayer ← RuntimeManager only (planning / LLM)
    OperationalLayer ← RuntimeManager only (graphs / recovery / scheduling)

Layers must NOT import each other circularly.  ``runtime_layers.py`` may import
implementations but must not import ``runtime_manager``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Cognitive layer — planning, LLM loop, execution-mode strategy
# ---------------------------------------------------------------------------

@runtime_checkable
class PlannerProtocol(Protocol):
    async def parse(self, text: str) -> Any: ...


@runtime_checkable
class ExecutionStrategyProtocol(Protocol):
    def decide_execution_mode(
        self,
        *,
        tool: str,
        action: str,
        params: Dict[str, Any],
        available_tools: List[str],
    ) -> Dict[str, Any]: ...


@runtime_checkable
class CognitiveLayerProtocol(Protocol):
    """Contract for planning and strategy decisions."""

    planner: PlannerProtocol
    execution_strategy: ExecutionStrategyProtocol

    async def parse_plan(self, text: str) -> Any: ...

    def decide_execution_mode(
        self,
        *,
        tool: str,
        action: str,
        params: Dict[str, Any],
        available_tools: List[str],
    ) -> Dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Operational layer — graphs, recovery, task scheduling
# ---------------------------------------------------------------------------

@runtime_checkable
class GraphExecutorProtocol(Protocol):
    def find_recovery_target(
        self, state: Dict[str, Any], classification: Dict[str, Any],
    ) -> Optional[str]: ...


@runtime_checkable
class RecoveryEngineProtocol(Protocol):
    def classify(
        self,
        *,
        task: Dict[str, Any],
        error: str,
        attempt: int,
        max_attempts: int = 2,
    ) -> Dict[str, Any]: ...

    def classify_action_result(
        self,
        *,
        task: Dict[str, Any],
        action_result: Dict[str, Any],
        attempt: int,
        max_attempts: int = 2,
    ) -> Dict[str, Any]: ...


@runtime_checkable
class TaskGraphSchedulerProtocol(Protocol):
    def next_batch(self) -> List[Dict[str, Any]]: ...


@runtime_checkable
class OperationalLayerProtocol(Protocol):
    """Contract for graph execution, recovery and parallel task scheduling."""

    recovery_engine: RecoveryEngineProtocol

    def graph_executor_for(self, name: Optional[str]) -> Optional[GraphExecutorProtocol]: ...

    def task_scheduler(
        self, tasks: List[Dict[str, Any]], *, max_parallel: int = 4,
    ) -> TaskGraphSchedulerProtocol: ...


# ---------------------------------------------------------------------------
# Execution layer — tools, safety, reflection, desktop adapters
# ---------------------------------------------------------------------------

@runtime_checkable
class ToolRouterProtocol(Protocol):
    async def execute(self, payload: Dict[str, Any]) -> Dict[str, Any]: ...

    @property
    def tools(self) -> Dict[str, Any]: ...


@runtime_checkable
class ExecutionLayerProtocol(Protocol):
    """Contract for tool dispatch and desktop automation adapters."""

    tool_router: ToolRouterProtocol

    async def execute_tool(
        self,
        *,
        inputs: Dict[str, Any],
        task: Dict[str, Any],
        cancel_event: Any,
    ) -> Dict[str, Any]: ...

    def wire_world_model(self, world_model: Any) -> None: ...


# ---------------------------------------------------------------------------
# State layer — persistence, memory, goals, sessions
# ---------------------------------------------------------------------------

@runtime_checkable
class PersistenceProtocol(Protocol):
    async def save_kv(self, key: str, value: Any) -> None: ...
    async def get_kv(self, key: str) -> Optional[Any]: ...
    async def list_states(self) -> List[Dict[str, Any]]: ...


@runtime_checkable
class StateLayerProtocol(Protocol):
    """Contract for durable state and memory subsystems."""

    persistence: PersistenceProtocol
    world_model: Any
    strategy_memory: Any
    goal_queue: Any
    session_manager: Any

    async def save_run_state(self, state: Dict[str, Any], jsonable_fn: Any) -> None: ...
    async def get_run_state(self, request_id: str) -> Optional[Dict[str, Any]]: ...
    async def list_run_states(self) -> List[Dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Boundary rules (enforced by contract tests)
# ---------------------------------------------------------------------------

LAYER_MODULES: Dict[str, str] = {
    "cognitive": "core.runtime_layers",
    "operational": "core.runtime_layers",
    "execution": "core.runtime_layers",
    "state": "core.runtime_layers",
}

# Modules that each layer implementation file must NOT import (architectural boundary)
FORBIDDEN_CROSS_LAYER_IMPORTS: Dict[str, frozenset] = {
    "runtime_layers": frozenset({
        "runtime_manager",
    }),
}

# Allowed dependency edges: source layer -> targets it may call (via protocols only)
ALLOWED_LAYER_DEPENDENCIES: Dict[str, frozenset] = {
    "cognitive": frozenset({"state", "execution"}),
    "operational": frozenset({"state", "execution"}),
    "execution": frozenset({"state"}),
    "state": frozenset(),
}
