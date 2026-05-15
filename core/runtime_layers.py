# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Explicit runtime layers.

The RuntimeManager remains the facade used by the API/UI, but these layer
objects make architectural responsibilities explicit and testable.

Each concrete layer class satisfies the corresponding Protocol in
``layer_contracts`` (see ``tests/test_layer_contracts.py``):

- CognitiveLayer / CognitiveLayerProtocol — planning, LLM loop, strategy.
- OperationalLayer / OperationalLayerProtocol — graphs, recovery, scheduling.
- ExecutionLayer / ExecutionLayerProtocol — tool execution and desktop adapters.
- StateLayer / StateLayerProtocol — persistence, world model, strategy memory.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from layer_contracts import (
        CognitiveLayerProtocol,
        ExecutionLayerProtocol,
        OperationalLayerProtocol,
        StateLayerProtocol,
    )

from agentic_loop import AgenticLoop
from credential_store import CredentialStore
from desktop_windows_agent import DesktopWindowsAgent
from durable_queue import DurableQueue
from execution_strategy import ExecutionStrategy
from graph_definitions import build_agentic_graph, build_local_graph, build_manual_graph
from nodes_reflection import ReflectionNode
from nodes_safety import SafetyNode
from nodes_tool_router import ToolRouterNode
from planner_agent import PlannerAgent
from recovery_engine import RecoveryEngine
from semantic_index import SemanticIndex
from session_manager import SessionManager
from state_graph import GraphExecutor
from strategy_memory import StrategyMemory
from task_graph import TaskGraphScheduler
from world_model import WorldModel


@dataclass
class CognitiveLayer:
    planner: PlannerAgent
    agentic_loop: AgenticLoop
    execution_strategy: ExecutionStrategy

    async def parse_plan(self, text: str):
        return await self.planner.parse(text)

    def decide_execution_mode(self, *, tool: str, action: str, params: Dict[str, Any], available_tools: List[str]) -> Dict[str, Any]:
        return self.execution_strategy.decide_execution_mode(
            tool=tool,
            action=action,
            params=params,
            available_tools=available_tools,
        )


@dataclass
class OperationalLayer:
    recovery_engine: RecoveryEngine
    local_graph: Any
    agentic_graph: Any
    manual_graph: Any
    local_executor: GraphExecutor
    agentic_executor: GraphExecutor
    manual_executor: GraphExecutor

    @classmethod
    def build(cls, recovery_engine: Optional[RecoveryEngine] = None) -> "OperationalLayer":
        local_graph = build_local_graph()
        agentic_graph = build_agentic_graph()
        manual_graph = build_manual_graph()
        return cls(
            recovery_engine=recovery_engine or RecoveryEngine(),
            local_graph=local_graph,
            agentic_graph=agentic_graph,
            manual_graph=manual_graph,
            local_executor=GraphExecutor(local_graph),
            agentic_executor=GraphExecutor(agentic_graph),
            manual_executor=GraphExecutor(manual_graph),
        )

    def graph_executor_for(self, name: Optional[str]) -> Optional[GraphExecutor]:
        if name == "local_heuristic":
            return self.local_executor
        if name == "agentic":
            return self.agentic_executor
        if name == "manual_assist":
            return self.manual_executor
        return None

    def task_scheduler(self, tasks: List[Dict[str, Any]], *, max_parallel: int = 4) -> TaskGraphScheduler:
        return TaskGraphScheduler(tasks, max_parallel=max_parallel)


@dataclass
class ExecutionLayer:
    safety: SafetyNode
    tool_router: ToolRouterNode
    reflection: ReflectionNode
    desktop_agent: DesktopWindowsAgent

    async def execute_tool(self, *, inputs: Dict[str, Any], task: Dict[str, Any], cancel_event) -> Dict[str, Any]:
        return await self.tool_router.execute(
            {
                "inputs": inputs,
                "current_task": task,
                "cancel_event": cancel_event,
            }
        )

    def wire_world_model(self, world_model: WorldModel) -> None:
        ui_tool = self.tool_router.tools.get("windows_ui")
        if ui_tool is not None and hasattr(ui_tool, "set_world_model"):
            ui_tool.set_world_model(world_model)


@dataclass
class StateLayer:
    persistence: Any
    credential_store: CredentialStore
    semantic_index: SemanticIndex
    world_model: WorldModel
    strategy_memory: StrategyMemory
    goal_queue: DurableQueue
    session_manager: SessionManager

    @classmethod
    def build(cls, persistence, *, credential_store: Optional[CredentialStore] = None) -> "StateLayer":
        semantic_index = SemanticIndex()
        world_model = WorldModel(persistence, semantic_index=semantic_index)
        strategy_memory = StrategyMemory(persistence, semantic_index=semantic_index)
        return cls(
            persistence=persistence,
            credential_store=credential_store or CredentialStore(persistence),
            semantic_index=semantic_index,
            world_model=world_model,
            strategy_memory=strategy_memory,
            goal_queue=DurableQueue(persistence),
            session_manager=SessionManager(persistence),
        )

    async def save_run_state(self, state: Dict[str, Any], jsonable_fn) -> None:
        await self.persistence.save_kv(f"state:{state['request_id']}", jsonable_fn(state))

    async def get_run_state(self, request_id: str) -> Optional[Dict[str, Any]]:
        return await self.persistence.get_kv(f"state:{request_id}")

    async def list_run_states(self) -> List[Dict[str, Any]]:
        return await self.persistence.list_states()
