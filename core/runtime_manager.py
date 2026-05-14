# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from pydantic import BaseModel

from action_executor import ActionExecutor, RunPausedError
from agent_persistence import Persistence
from agentic_loop import AgenticLoop
from canonical_tools import action_metadata, normalize_agentic_task, supported_tools as canonical_supported_tools, task_title
from credential_store import CredentialStore
from desktop_windows_agent import DesktopWindowsAgent
from event_bus import EventBus, EventType, get_event_bus
from semantic_verifier import SemanticVerifier
from multi_provider_client import MultiProviderClient
from nodes_reflection import ReflectionNode
from nodes_safety import SafetyNode
from nodes_tool_router import ToolRouterNode
from planner_agent import PlannerAgent
from planner_models import Task
from recovery_engine import RecoveryEngine
from risk_classifier import explain_command, normalize_command
from world_model import WorldModel
from strategy_memory import StrategyMemory
from durable_queue import DurableQueue
from execution_strategy import ExecutionStrategy
from graph_definitions import build_agentic_graph, build_local_graph, build_manual_graph
from semantic_index import SemanticIndex
from session_manager import SessionManager
from state_graph import GraphExecutor, NodeType
from task_graph import TaskGraphScheduler, SUCCESS_STATUSES
from runtime_layers import CognitiveLayer, ExecutionLayer, OperationalLayer, StateLayer

logger = logging.getLogger(__name__)

DAEMON_POLL_INTERVAL = 5
STALE_RUNNING_THRESHOLD = 600


def _now() -> str:
    return datetime.utcnow().isoformat()


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return json.loads(value.json())
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _first_non_empty(*values: Any) -> Optional[str]:
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return None


class RuntimeManager:
    def __init__(
        self,
        emitter: Callable[[Dict[str, Any]], Awaitable[None]],
        *,
        credential_store: Optional[CredentialStore] = None,
        provider_client: Optional[MultiProviderClient] = None,
        event_bus: Optional[EventBus] = None,
    ):
        self.emitter = emitter
        self.event_bus: EventBus = event_bus or get_event_bus()

        async def _ws_bridge(event):
            await emitter({"type": event.type.value, "payload": event.payload})

        self.event_bus.subscribe_all(_ws_bridge)
        self.persistence = Persistence.get()
        self.provider_client = provider_client or MultiProviderClient()

        state_credential_store = credential_store or CredentialStore(self.persistence)
        self.state_layer = StateLayer.build(self.persistence, credential_store=state_credential_store)
        self.execution_layer = ExecutionLayer(
            safety=SafetyNode("safety"),
            tool_router=ToolRouterNode("tool_router"),
            reflection=ReflectionNode("reflection"),
            desktop_agent=DesktopWindowsAgent(),
        )
        agentic_loop = AgenticLoop(
            client=self.provider_client,
            safety=self.execution_layer.safety,
            tool_router=self.execution_layer.tool_router,
            reflection=self.execution_layer.reflection,
        )
        self.cognitive_layer = CognitiveLayer(
            planner=PlannerAgent(supported_tools=canonical_supported_tools()),
            agentic_loop=agentic_loop,
            execution_strategy=ExecutionStrategy(),
        )
        self.operational_layer = OperationalLayer.build()

        # Backward-compatible aliases for existing runtime code and tests.
        self.credential_store = self.state_layer.credential_store
        self.semantic_index = self.state_layer.semantic_index
        self.world_model = self.state_layer.world_model
        self.strategy_memory = self.state_layer.strategy_memory
        self.goal_queue = self.state_layer.goal_queue
        self.session_manager = self.state_layer.session_manager
        self.safety = self.execution_layer.safety
        self.tool_router = self.execution_layer.tool_router
        self.reflection = self.execution_layer.reflection
        self.desktop_agent = self.execution_layer.desktop_agent
        self.planner = self.cognitive_layer.planner
        self.agentic_loop = self.cognitive_layer.agentic_loop
        self.execution_strategy = self.cognitive_layer.execution_strategy
        self.recovery_engine = self.operational_layer.recovery_engine
        self._wire_world_model_to_ui_tool()
        self.semantic_verifier = SemanticVerifier()
        self.action_executor = ActionExecutor(self, event_bus=self.event_bus)

        self.active_runs: Dict[str, Dict[str, Any]] = {}
        self.run_tasks: Dict[str, asyncio.Task] = {}
        self.run_cancel_events: Dict[str, asyncio.Event] = {}
        self.approval_waiters: Dict[str, asyncio.Future] = {}
        self.approval_run_map: Dict[str, str] = {}
        self._daemon_task: Optional[asyncio.Task] = None
        self._daemon_running = False

        self._local_graph = self.operational_layer.local_graph
        self._agentic_graph = self.operational_layer.agentic_graph
        self._manual_graph = self.operational_layer.manual_graph
        self._local_executor = self.operational_layer.local_executor
        self._agentic_executor = self.operational_layer.agentic_executor
        self._manual_executor = self.operational_layer.manual_executor

    def _wire_world_model_to_ui_tool(self) -> None:
        """Connect the World Model to the WindowsUiTool for selector healing."""
        self.execution_layer.wire_world_model(self.world_model)

    async def submit_run(
        self,
        inputs: Dict[str, Any],
        *,
        runtime_mode: str = "agentic",
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> str:
        request_id = str(uuid.uuid4())
        state = self._new_state(request_id, inputs, runtime_mode=runtime_mode, provider=provider, model=model)
        self.active_runs[request_id] = state
        self.run_cancel_events[request_id] = asyncio.Event()
        await self._persist_state(state)
        self.run_tasks[request_id] = asyncio.create_task(self._run_pipeline(request_id))
        return request_id

    async def rehydrate_runs(self) -> List[Dict[str, Any]]:
        recovered: List[Dict[str, Any]] = []
        for state in await self.persistence.list_states():
            status = state.get("status")
            if status in {"completed", "failed", "cancelled"}:
                continue
            request_id = state["request_id"]
            self.active_runs[request_id] = state
            self.run_cancel_events.setdefault(request_id, asyncio.Event())

            pending_handoff = state.get("pending_handoff")
            pending_approvals = [approval for approval in state.get("approvals", []) if approval.get("status") == "pending"]
            if pending_handoff and not pending_handoff.get("response"):
                state["status"] = "awaiting_input"
            elif pending_approvals:
                state["status"] = "awaiting_approval"
                for approval in pending_approvals:
                    approval_id = approval["approval_id"]
                    self.approval_run_map[approval_id] = request_id
                    self.approval_waiters.setdefault(approval_id, asyncio.get_event_loop().create_future())
            elif status in {"planning", "running", "resuming", "recovering", "cancelling", "queued"}:
                state["status"] = "paused"
                state.setdefault("recovery", {})["rehydrated"] = True
                state.setdefault("recovery", {})["reason"] = "backend restarted before run reached a terminal state"

            await self._persist_state(state)
            recovered.append(state)
        return recovered

    # ── Daemon lifecycle ────────────────────────────────────────────────

    async def start_daemon(self) -> None:
        if self._daemon_running:
            return
        self._daemon_running = True
        self._daemon_task = asyncio.create_task(self._daemon_loop())
        logger.info("Runtime daemon started (poll_interval=%ds)", DAEMON_POLL_INTERVAL)

    async def stop_daemon(self) -> None:
        self._daemon_running = False
        if self._daemon_task and not self._daemon_task.done():
            self._daemon_task.cancel()
            try:
                await self._daemon_task
            except asyncio.CancelledError:
                pass
        self._daemon_task = None
        logger.info("Runtime daemon stopped")

    async def _daemon_loop(self) -> None:
        while self._daemon_running:
            try:
                await self._daemon_tick()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Daemon tick failed")
            await asyncio.sleep(DAEMON_POLL_INTERVAL)

    async def _daemon_tick(self) -> None:
        promoted = await self.goal_queue.promote_deferred()
        if promoted:
            logger.info("Daemon promoted %d deferred/retrying goals to queued", promoted)

        recovered = await self.goal_queue.recover_stale_running(stale_seconds=STALE_RUNNING_THRESHOLD)
        if recovered:
            logger.info("Daemon recovered %d stale running goals", recovered)

        await self._resume_checkpointed_jobs()

        await self._process_goal_queue()

        await self.session_manager.expire_stale(inactive_hours=168)

    async def _process_goal_queue(self) -> None:
        running_count = sum(1 for state in self.active_runs.values() if state.get("status") in {"running", "planning", "resuming"})
        max_concurrent = 3
        while running_count < max_concurrent:
            goal = await self.goal_queue.dequeue()
            if not goal:
                break
            try:
                request_id = await self._submit_goal_run(goal)
                await self.goal_queue.mark_running(goal["goal_id"], request_id=request_id)
                running_count += 1
            except Exception as exc:
                logger.exception("Failed to start run for goal %s", goal["goal_id"])
                await self.goal_queue.mark_failed(goal["goal_id"], str(exc))

    async def _submit_goal_run(self, goal: Dict[str, Any]) -> str:
        request_id = await self.submit_run(
            goal["inputs"],
            runtime_mode=goal.get("runtime_mode", "agentic"),
            provider=goal.get("provider"),
            model=goal.get("model"),
        )
        state = self.active_runs.get(request_id)
        if state:
            state["goal_id"] = goal["goal_id"]
            state["session_id"] = goal.get("session_id")
            await self._persist_state(state)

        session_id = goal.get("session_id")
        if session_id:
            await self.session_manager.add_run(session_id, request_id)
            await self.session_manager.add_goal(session_id, goal["goal_id"])

        return request_id

    # ── Durable goal queue public API ────────────────────────────────

    async def enqueue_goal(
        self,
        inputs: Dict[str, Any],
        *,
        workspace: str = "default",
        priority: int = 50,
        runtime_mode: str = "agentic",
        provider: Optional[str] = None,
        model: Optional[str] = None,
        parent_goal_id: Optional[str] = None,
        max_retries: int = 2,
        retry_delay_seconds: int = 30,
        scheduled_at: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        goal = await self.goal_queue.enqueue(
            inputs,
            workspace=workspace,
            priority=priority,
            runtime_mode=runtime_mode,
            provider=provider,
            model=model,
            parent_goal_id=parent_goal_id,
            max_retries=max_retries,
            retry_delay_seconds=retry_delay_seconds,
            scheduled_at=scheduled_at,
        )
        if session_id:
            goal["session_id"] = session_id
            await self.session_manager.add_goal(session_id, goal["goal_id"])
        await self._emit("goal_enqueued", {"goal": goal})
        return goal

    async def list_goals(self, **kwargs) -> List[Dict[str, Any]]:
        return await self.goal_queue.list_goals(**kwargs)

    async def cancel_goal(self, goal_id: str) -> Dict[str, Any]:
        goal = await self.goal_queue.get_goal(goal_id)
        if not goal:
            raise KeyError(goal_id)
        if goal["status"] in {"completed", "failed", "cancelled"}:
            return {"goal_id": goal_id, "status": goal["status"], "already_terminal": True}
        if goal.get("request_id"):
            try:
                await self.cancel_run(goal["request_id"])
            except (KeyError, Exception):
                pass
        await self.goal_queue.mark_cancelled(goal_id)
        await self._emit("goal_cancelled", {"goal_id": goal_id})
        return {"goal_id": goal_id, "status": "cancelled", "already_terminal": False}

    # ── Job checkpoint & resume ──────────────────────────────────────

    async def save_job_checkpoint(
        self,
        request_id: str,
        *,
        step_index: int = 0,
        total_steps: int = 0,
    ) -> None:
        state = self.active_runs.get(request_id)
        if not state:
            return
        snapshot = {
            "status": state.get("status"),
            "current_node": state.get("current_node"),
            "current_task_id": state.get("current_task_id"),
            "tasks": state.get("tasks"),
            "agent_session": state.get("agent_session"),
            "autonomy": state.get("autonomy"),
            "runtime_context": state.get("runtime_context"),
            "inputs": state.get("inputs"),
            "outputs": state.get("outputs"),
            "runtime_mode": state.get("runtime_mode"),
            "provider": state.get("provider"),
            "model": state.get("model"),
        }
        job_id = f"job-{request_id}"
        await self.persistence.save_job_checkpoint(
            job_id,
            request_id,
            snapshot,
            session_id=state.get("session_id"),
            goal_id=state.get("goal_id"),
            step_index=step_index,
            total_steps=total_steps,
        )

    async def _resume_checkpointed_jobs(self) -> None:
        checkpoints = await self.persistence.list_resumable_checkpoints()
        for cp in checkpoints:
            request_id = cp["request_id"]
            if request_id in self.run_tasks and not self.run_tasks[request_id].done():
                continue
            if request_id in self.active_runs:
                status = self.active_runs[request_id].get("status")
                if status in {"completed", "failed", "cancelled"}:
                    await self.persistence.delete_job_checkpoint(cp["job_id"])
                    continue
                if status in {"running", "planning"}:
                    continue

            state = self.active_runs.get(request_id)
            if not state:
                persisted = await self.persistence.get_kv(f"state:{request_id}")
                if not persisted:
                    await self.persistence.delete_job_checkpoint(cp["job_id"])
                    continue
                if persisted.get("status") in {"completed", "failed", "cancelled"}:
                    await self.persistence.delete_job_checkpoint(cp["job_id"])
                    continue
                state = persisted
                self.active_runs[request_id] = state

            snapshot = cp.get("state_snapshot") or {}
            if snapshot.get("agent_session"):
                state["agent_session"] = snapshot["agent_session"]
            if snapshot.get("autonomy"):
                state["autonomy"] = snapshot["autonomy"]
            if snapshot.get("runtime_context"):
                state["runtime_context"] = snapshot["runtime_context"]

            state.setdefault("recovery", {})["resumed_from_checkpoint"] = True
            state["recovery"]["checkpoint_step"] = cp.get("step_index", 0)

            self.run_cancel_events.setdefault(request_id, asyncio.Event())
            self.run_tasks[request_id] = asyncio.create_task(self._run_pipeline(request_id, resume=True))
            logger.info("Resumed job %s from checkpoint (step %d/%d)", request_id, cp.get("step_index", 0), cp.get("total_steps", 0))
            await self.persistence.delete_job_checkpoint(cp["job_id"])

    # ── Session public API ───────────────────────────────────────────

    async def create_session(self, **kwargs) -> Dict[str, Any]:
        session = await self.session_manager.create_session(**kwargs)
        await self._emit("session_created", {"session": session})
        return session

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        return await self.session_manager.get_session(session_id)

    async def list_sessions(self, **kwargs) -> List[Dict[str, Any]]:
        return await self.session_manager.list_sessions(**kwargs)

    async def close_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        session = await self.session_manager.update_session(session_id, {"status": "completed"})
        if session:
            await self._emit("session_closed", {"session_id": session_id})
        return session

    async def session_checkpoint(self, session_id: str, checkpoint_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return await self.session_manager.checkpoint(session_id, checkpoint_data)

    async def find_or_create_session(
        self,
        *,
        workspace: str = "default",
        objective: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        existing = await self.session_manager.find_active_session(workspace=workspace, objective=objective)
        if existing:
            return existing
        return await self.create_session(workspace=workspace, objective=objective, **kwargs)

    async def submit_run_with_session(
        self,
        inputs: Dict[str, Any],
        *,
        workspace: str = "default",
        objective: Optional[str] = None,
        runtime_mode: str = "agentic",
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        session = await self.find_or_create_session(
            workspace=workspace,
            objective=objective or inputs.get("text") or inputs.get("prompt"),
        )
        request_id = await self.submit_run(
            inputs, runtime_mode=runtime_mode, provider=provider, model=model,
        )
        state = self.active_runs.get(request_id)
        if state:
            state["session_id"] = session["session_id"]
            await self._persist_state(state)
        await self.session_manager.add_run(session["session_id"], request_id)
        return {"request_id": request_id, "session_id": session["session_id"], "session": session}

    async def cancel_run(self, request_id: str) -> Dict[str, Any]:
        state = self.active_runs.get(request_id)
        if state is None:
            persisted = await self.persistence.get_kv(f"state:{request_id}")
            if persisted is None:
                raise KeyError(request_id)
            return {
                "request_id": request_id,
                "status": persisted.get("status"),
                "already_terminal": True,
            }
        if state["status"] in {"completed", "failed", "cancelled"}:
            return {
                "request_id": request_id,
                "status": state["status"],
                "already_terminal": True,
            }

        cancel_event = self.run_cancel_events.setdefault(request_id, asyncio.Event())
        cancel_event.set()
        await self._set_run_status(state, "cancelling")
        await self._emit("run_cancellation_requested", {"request_id": request_id, "status": "cancelling"}, state=state)

        task_to_cancel = None
        current_task_id = state.get("current_task_id")
        if current_task_id:
            task_to_cancel = next((task for task in state.get("tasks", []) if task.get("id") == current_task_id), None)
        if task_to_cancel is None:
            task_to_cancel = next(
                (
                    task
                    for task in state.get("tasks", [])
                    if task.get("status") not in {"completed", "failed", "cancelled", "rejected"}
                ),
                None,
            )
        if task_to_cancel is not None and task_to_cancel.get("status") != "cancelled":
            task_to_cancel["status"] = "cancelled"
            task_to_cancel["error"] = task_to_cancel.get("error") or "cancelled"
            await self._emit(
                "task_cancelled",
                {"request_id": request_id, "task_id": task_to_cancel["id"], "error": "cancelled"},
                state=state,
            )
        elif current_task_id or state.get("tasks"):
            fallback_task_id = current_task_id or state["tasks"][0]["id"]
            await self._emit(
                "task_cancelled",
                {"request_id": request_id, "task_id": fallback_task_id, "error": "cancelled"},
                state=state,
            )

        for approval_id, run_id in list(self.approval_run_map.items()):
            if run_id != request_id:
                continue
            waiter = self.approval_waiters.get(approval_id)
            if waiter is not None and not waiter.done():
                waiter.set_result("cancelled")

        task = self.run_tasks.get(request_id)
        if task is not None and not task.done():
            task.cancel()
        return {"request_id": request_id, "status": "cancelling", "already_terminal": False}

    async def reply_to_run(self, request_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        state = await self._ensure_active_state(request_id)
        handoff = state.get("pending_handoff")
        if not handoff:
            raise ValueError("run does not have a pending handoff")
        handoff["response"] = _jsonable(payload)
        handoff["status"] = "answered"
        state["pending_handoff"] = handoff
        state["last_updated"] = _now()
        await self._set_run_status(state, "paused", current_node="handoff")
        await self._emit(
            "user_input_received",
            {"request_id": request_id, "handoff_id": handoff["handoff_id"], "response": handoff["response"]},
            state=state,
        )
        return {"request_id": request_id, "handoff": handoff}

    async def resume_run(self, request_id: str) -> Dict[str, Any]:
        state = await self._ensure_active_state(request_id)
        if state["status"] in {"completed", "failed", "cancelled"}:
            return {"request_id": request_id, "status": state["status"], "already_terminal": True}

        pending_handoff = state.get("pending_handoff")
        if pending_handoff:
            if not pending_handoff.get("response"):
                raise ValueError("pending handoff requires a user response before resuming")
            await self._apply_handoff_response(state, pending_handoff)

        if any(approval.get("status") == "pending" for approval in state.get("approvals", [])):
            await self._set_run_status(state, "awaiting_approval")
            return {"request_id": request_id, "status": "awaiting_approval", "resumed": False}

        if request_id in self.run_tasks and not self.run_tasks[request_id].done():
            return {"request_id": request_id, "status": state["status"], "resumed": False}

        self.run_cancel_events.setdefault(request_id, asyncio.Event())
        self.run_tasks[request_id] = asyncio.create_task(self._run_pipeline(request_id, resume=True))
        return {"request_id": request_id, "status": "resuming", "resumed": True}

    async def list_runs(self) -> List[Dict[str, Any]]:
        states = {state["request_id"]: state for state in await self.persistence.list_states()}
        for request_id, state in self.active_runs.items():
            states[request_id] = _jsonable(state)
        return sorted(states.values(), key=lambda item: item.get("last_updated") or "", reverse=True)

    async def list_approvals(self, request_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return await self.persistence.list_approvals(request_id=request_id)

    async def list_approval_grants(self, request_id: str) -> List[Dict[str, Any]]:
        state = await self._ensure_active_state(request_id)
        return list(state.get("approval_grants") or [])

    async def revoke_approval_grant(self, request_id: str, grant_id: str) -> Dict[str, Any]:
        state = await self._ensure_active_state(request_id)
        for grant in state.get("approval_grants", []):
            if grant.get("grant_id") == grant_id:
                grant["status"] = "revoked"
                grant["revoked_at"] = _now()
                await self._persist_state(state)
                payload = {"request_id": request_id, "grant": grant}
                await self._emit("approval_grant_revoked", payload, state=state)
                return payload
        raise KeyError(grant_id)

    async def delete_run(self, request_id: str) -> Dict[str, Any]:
        state = self.active_runs.get(request_id)
        task = self.run_tasks.get(request_id)
        if task is not None and not task.done():
            await self.cancel_run(request_id)
            try:
                await self.wait_for_run(request_id, timeout=10)
            except Exception:
                logger.exception("Run %s did not finish cleanly before deletion", request_id)

        persisted = await self.persistence.get_kv(f"state:{request_id}")
        if state is None and persisted is None:
            raise KeyError(request_id)

        self.active_runs.pop(request_id, None)
        self.run_tasks.pop(request_id, None)
        self.run_cancel_events.pop(request_id, None)

        for approval_id, run_id in list(self.approval_run_map.items()):
            if run_id == request_id:
                self.approval_run_map.pop(approval_id, None)
                waiter = self.approval_waiters.pop(approval_id, None)
                if waiter is not None and not waiter.done():
                    waiter.cancel()

        await self.persistence.delete_state(request_id)
        await self.persistence.delete_approvals(request_id)
        await self._emit("run_deleted", {"request_id": request_id})
        return {"request_id": request_id, "deleted": True}

    async def wait_for_run(self, request_id: str, timeout: Optional[float] = None) -> Dict[str, Any]:
        task = self.run_tasks.get(request_id)
        if task is None:
            state = await self.get_state(request_id)
            if state is None:
                raise KeyError(request_id)
            return state
        deadline = None if timeout is None else asyncio.get_event_loop().time() + timeout
        try:
            if timeout is not None:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            else:
                await task
        except asyncio.CancelledError:
            pass
        except asyncio.TimeoutError:
            pass
        while True:
            state = await self.get_state(request_id)
            if state is None:
                raise KeyError(request_id)
            if task.done() and state.get("status") == "cancelling" and request_id in self.active_runs:
                await self._mark_run_cancelled(self.active_runs[request_id])
                state = await self.get_state(request_id)
            if state.get("status") in {"completed", "failed", "cancelled", "awaiting_input", "paused", "awaiting_approval"}:
                return state
            if deadline is not None and asyncio.get_event_loop().time() >= deadline:
                raise asyncio.TimeoutError()
            await asyncio.sleep(0.05)

    async def get_state(self, request_id: str) -> Optional[Dict[str, Any]]:
        if request_id in self.active_runs:
            return _jsonable(self.active_runs[request_id])
        return await self.persistence.get_kv(f"state:{request_id}")

    async def _ensure_active_state(self, request_id: str) -> Dict[str, Any]:
        state = self.active_runs.get(request_id)
        if state is not None:
            return state
        persisted = await self.persistence.get_kv(f"state:{request_id}")
        if persisted is None:
            raise KeyError(request_id)
        self.active_runs[request_id] = persisted
        self.run_cancel_events.setdefault(request_id, asyncio.Event())
        return persisted

    async def request_manual_approval(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        approval_id = str(uuid.uuid4())
        request_id = payload.get("request_id") or approval_id
        approval = {
            "approval_id": approval_id,
            "request_id": request_id,
            "task_id": payload.get("task_id"),
            "title": payload.get("title", "Approval request"),
            "reason": payload.get("reason", ""),
            "status": "pending",
            "payload": payload,
        }
        await self.persistence.create_approval(
            approval_id,
            request_id,
            payload.get("approver", ""),
            approval,
            task_id=payload.get("task_id"),
        )
        await self._emit("approval_requested", {"request_id": request_id, "approval": approval})
        return approval

    async def resolve_approval(self, approval_id: str, status: str, scope: str = "single") -> Dict[str, Any]:
        await self.persistence.set_approval(approval_id, status)
        approval = await self.persistence.get_approval(approval_id)
        if approval is None:
            raise KeyError(approval_id)
        request_id = approval.get("request_id")
        run_state = await self._ensure_active_state(request_id)
        created_grant = None
        if run_state is not None:
            runtime_approval = None
            for existing in run_state["approvals"]:
                if existing["approval_id"] == approval_id:
                    existing["status"] = status
                    existing["resolved_scope"] = scope
                    runtime_approval = existing
            for task in run_state.get("tasks", []):
                if task.get("approval_id") == approval_id:
                    task["approval_granted"] = status == "approved"
                    if status == "approved":
                        task["status"] = "approved"
                        task["approval_scope"] = scope
                        if scope == "run_scope":
                            created_grant = self._activate_run_scope_grant(
                                run_state,
                                task,
                                existing_approval=runtime_approval or approval.get("payload") or {},
                            )
            await self._persist_state(run_state)
        waiter = self.approval_waiters.pop(approval_id, None)
        if waiter is not None and not waiter.done():
            waiter.set_result(status)
        payload = {
            "request_id": request_id,
            "approval_id": approval_id,
            "status": status,
            "task_id": approval.get("task_id"),
            "scope": scope,
            "grant": created_grant,
        }
        await self._emit("approval_resolved", payload)
        if created_grant is not None:
            await self._emit("approval_grant_created", {"request_id": request_id, "grant": created_grant}, state=run_state)
        return payload

    def _new_state(
        self,
        request_id: str,
        inputs: Dict[str, Any],
        *,
        runtime_mode: str,
        provider: Optional[str],
        model: Optional[str],
    ) -> Dict[str, Any]:
        timestamp = _now()
        return {
            "request_id": request_id,
            "created_at": timestamp,
            "last_updated": timestamp,
            "status": "queued",
            "runtime_mode": runtime_mode,
            "provider": provider,
            "model": model,
            "inputs": _jsonable(inputs),
            "outputs": {},
            "current_node": None,
            "current_task_id": None,
            "tasks": [],
            "history": [],
            "approvals": [],
            "approval_grants": [],
            "handoffs": [],
            "pending_handoff": None,
            "session_id": None,
            "goal_id": None,
            "agent_session": {},
            "runtime_context": {
                "window": None,
                "ui_target": None,
                "updated_at": None,
            },
            "autonomy": {
                "pending_subgoals": [],
                "completed_subgoals": [],
                "observations": [],
                "hypotheses": [],
                "strategy_log": [],
                "goal_verifications": [],
            },
            "recovery": {},
            "error": None,
        }

    async def _run_pipeline(self, request_id: str, *, resume: bool = False) -> None:
        state = self.active_runs[request_id]
        try:
            if resume:
                await self._set_run_status(state, "resuming", current_node=state.get("current_node"))
                await self._emit(
                    "run_resumed",
                    {"request_id": request_id, "status": state["status"]},
                    state=state,
                )
            else:
                await self._set_run_status(state, "planning", current_node="planner")
                await self._emit(
                    "run_started",
                    {
                        "request_id": request_id,
                        "status": state["status"],
                        "inputs": state["inputs"],
                        "created_at": state["created_at"],
                        "runtime_mode": state["runtime_mode"],
                        "provider": state.get("provider"),
                        "model": state.get("model"),
                    },
                    state=state,
                )

            if state["inputs"].get("allow_local_execution") and (
                state["runtime_mode"] != "agentic" or not state.get("provider")
            ):
                state["_pipeline_graph"] = "local_heuristic"
                await self._run_local_heuristic_pipeline(state)
                return

            use_manual_assist, reason = await self._should_use_manual_assist(state)
            if use_manual_assist:
                if state["inputs"].get("allow_local_execution"):
                    state["_pipeline_graph"] = "local_heuristic"
                    await self._run_local_heuristic_pipeline(state)
                else:
                    state["_pipeline_graph"] = "manual_assist"
                    await self._run_manual_assist_pipeline(state, reason=reason)
            elif state["runtime_mode"] == "agentic":
                state["_pipeline_graph"] = "agentic"
                await self._run_agentic_pipeline(state)
            else:
                state["_pipeline_graph"] = "local_heuristic"
                await self._run_local_heuristic_pipeline(state)
        except RunPausedError:
            return
        except asyncio.CancelledError:
            await self._mark_run_cancelled(state)
        except Exception as exc:
            logger.exception("Run %s failed unexpectedly", request_id)
            if state.get("status") != "failed":
                await self._fail_run(state, str(exc))
        finally:
            self.run_tasks.pop(request_id, None)
            if state.get("status") in {"completed", "failed", "cancelled"}:
                self.run_cancel_events.pop(request_id, None)

    async def _should_use_manual_assist(self, state: Dict[str, Any]) -> Any:
        if state["inputs"].get("allow_local_execution"):
            return False, None
        if state["runtime_mode"] != "agentic":
            return True, "runtime configured for manual assist fallback"
        provider = state.get("provider")
        if not provider:
            return True, "no provider configured for API-first mode"
        if not await self.credential_store.is_configured(provider):
            return True, f"provider '{provider}' is not configured"
        return False, None

    async def _run_manual_assist_pipeline(self, state: Dict[str, Any], *, reason: Optional[str] = None) -> None:
        plan_result = await self._plan_tasks(state)
        tasks = plan_result["tasks"]
        state["outputs"]["execution_mode"] = "manual_assist"
        if reason:
            state["outputs"]["manual_assist_reason"] = reason
        if not tasks:
            await self._complete_run(
                state,
                summary=plan_result["message"],
                details={"kind": plan_result["kind"], "execution_mode": "manual_assist", "reason": reason},
                current_node="planner",
            )
            return
        state["tasks"] = tasks
        for task in tasks:
            task["status"] = "manual_step"
            await self._emit("task_planned", {"request_id": state["request_id"], "task": task}, state=state)

        summary_lines = [f"- {task['title']}" for task in state["tasks"]]
        summary = "Modo manual assistido ativo."
        if reason:
            summary = f"{summary} Motivo: {reason}."
        summary = f"{summary}\nPassos sugeridos:\n" + "\n".join(summary_lines)
        state["outputs"]["manual_assist_plan"] = {
            "reason": reason,
            "tasks": state["tasks"],
        }
        await self._complete_run(
            state,
            summary=summary,
            details={"execution_mode": "manual_assist", "reason": reason, "task_ids": [task["id"] for task in state["tasks"]]},
            current_node="planner",
        )

    async def _run_local_heuristic_pipeline(self, state: Dict[str, Any]) -> None:
        plan_result = await self._plan_tasks(state)
        tasks = plan_result["tasks"]
        if not tasks:
            await self._complete_run(
                state,
                summary=plan_result["message"],
                details={"kind": plan_result["kind"], "execution_mode": "local_heuristic"},
                current_node="planner",
            )
            return
        if not state.get("tasks"):
            state["tasks"] = tasks
        scheduler = self.operational_layer.task_scheduler(
            state["tasks"],
            max_parallel=int(state.get("inputs", {}).get("max_parallel_tasks") or 4),
        )
        scheduler.prepare()
        state["task_graph"] = scheduler.snapshot()
        await self._emit(
            "task_graph_built",
            {"request_id": state["request_id"], "task_graph": state["task_graph"]},
            state=state,
        )

        planned_ids = set()
        while not scheduler.all_done():
            self._raise_if_cancelled(state)
            batch = scheduler.next_batch()
            state["task_graph"] = scheduler.snapshot()
            if not batch:
                break

            for task in batch:
                if task["id"] not in planned_ids:
                    planned_ids.add(task["id"])
                    await self._emit("task_planned", {"request_id": state["request_id"], "task": task}, state=state)

            if len(batch) > 1:
                await self._emit(
                    "task_branch_batch_started",
                    {
                        "request_id": state["request_id"],
                        "task_ids": [task["id"] for task in batch],
                        "speculative_task_ids": [task["id"] for task in batch if task.get("speculative")],
                    },
                    state=state,
                )

                async def _exec_branch(task):
                    try:
                        return task, await self._execute_task_with_recovery(state, task)
                    except RunPausedError:
                        raise
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        return task, {"_error": str(exc)}

                results = await asyncio.gather(*[_exec_branch(task) for task in batch], return_exceptions=True)
                for item in results:
                    if isinstance(item, RunPausedError):
                        raise item
                    if isinstance(item, asyncio.CancelledError):
                        raise item
                    if isinstance(item, BaseException):
                        raise item
                await self._emit(
                    "task_branch_batch_finished",
                    {
                        "request_id": state["request_id"],
                        "task_ids": [task["id"] for task in batch],
                        "task_graph": scheduler.snapshot(),
                    },
                    state=state,
                )
            else:
                await self._execute_task_with_recovery(state, batch[0])

            scheduler.resolve_alternative_branches()
            scheduler.mark_blocked_by_failed_dependencies()
            state["task_graph"] = scheduler.snapshot()

        status_counts: Dict[str, int] = {}
        for task in state["tasks"]:
            status_counts[task["status"]] = status_counts.get(task["status"], 0) + 1
        completed_or_partial = [
            task["id"] for task in state["tasks"] if task.get("status") in SUCCESS_STATUSES
        ]
        if set(status_counts.keys()) == {"completed"}:
            summary = f"Completed {len(state['tasks'])} task(s)"
        elif scheduler.can_finish_partially():
            summary = "Finished local execution with partial completion and usable intermediate results."
        else:
            summary = "Finished local execution with mixed task outcomes."
        await self._complete_run(
            state,
            summary=summary,
            details={
                "completed_tasks": [task["id"] for task in state["tasks"] if task.get("status") == "completed"],
                "partial_tasks": [task["id"] for task in state["tasks"] if task.get("status") == "partial"],
                "usable_result_tasks": completed_or_partial,
                "task_status_counts": status_counts,
                "task_graph": scheduler.snapshot(),
                "execution_mode": "local_heuristic",
            },
            current_node="reflection",
        )

    async def _run_agentic_pipeline(self, state: Dict[str, Any]) -> None:
        provider = state.get("provider")
        if not provider:
            await self._run_manual_assist_pipeline(state, reason="no provider configured for API-first mode")
            return
        provider_config = await self.credential_store.resolve_runtime_config(provider, model=state.get("model"))
        result = await self.cognitive_layer.agentic_loop.run(
            state=state,
            provider_config=provider_config,
            emit=lambda event_type, payload: self._emit(event_type, payload, state=state),
            task_factory=self._agentic_task_from_tool_call,
            execute_task=self._execute_task_with_recovery,
            cancel_event=self.run_cancel_events[state["request_id"]],
            session=state.get("agent_session") or None,
            checkpoint=lambda session_update: self._checkpoint_agent_session(state, session_update),
        )
        state["agent_session"] = result.get("session") or {}
        if result["status"] == "awaiting_input":
            await self._pause_for_handoff(state, result["handoff"])
            raise RunPausedError()
        state["outputs"]["agent_final_response"] = {
            "provider": provider_config["provider"],
            "model": provider_config["model"],
            "text": result["final_text"],
            "steps": result["steps"],
        }
        state["outputs"]["execution_mode"] = "agentic"
        await self._complete_run(
            state,
            summary=result["final_text"],
            details={
                "runtime_mode": "agentic",
                "provider": provider_config["provider"],
                "model": provider_config["model"],
                "steps": result["steps"],
                "execution_mode": "agentic",
            },
            current_node="reflection",
        )

    def _current_failure_task(self, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        current_task_id = state.get("current_task_id")
        if current_task_id:
            task = next((item for item in state.get("tasks", []) if item.get("id") == current_task_id), None)
            if task is not None:
                return task
        for task in reversed(state.get("tasks", [])):
            if task.get("status") in {"failed", "rejected", "retry_scheduled", "waiting_approval"}:
                return task
        return state.get("tasks", [])[-1] if state.get("tasks") else None

    def _user_facing_failure_reflection(self, state: Dict[str, Any], error: str) -> Dict[str, Any]:
        task = self._current_failure_task(state)
        recovery = (task or {}).get("recovery") or state.get("recovery") or {"action": "stop"}
        tool_result = (((task or {}).get("result") or {}).get("tool_result") or {})
        action_result = (((task or {}).get("result") or {}).get("action_result") or {})
        details = (tool_result.get("details") or {}) if isinstance(tool_result, dict) else {}
        params = (task or {}).get("params") or {}
        task_title = (task or {}).get("title") or "esta tarefa"
        raw_error = _first_non_empty(
            ((action_result.get("issue") or {}).get("message") if isinstance(action_result, dict) else None),
            action_result.get("summary") if isinstance(action_result, dict) else None,
            details.get("error") if isinstance(details, dict) else None,
            details.get("stderr") if isinstance(details, dict) else None,
            error,
            (task or {}).get("error"),
        ) or "erro inesperado"
        message = raw_error.lower()
        tool = (task or {}).get("tool")
        action = (task or {}).get("action")

        next_steps: List[str] = []
        manual_option: Optional[str] = None

        if "approval rejected" in message:
            summary = (
                "Nao continuei porque a acao que precisava da sua aprovacao foi rejeitada. "
                "Se quiser tentar de novo, inicie a tarefa novamente e aprove essa etapa."
            )
            manual_option = "Se preferir, faca essa etapa manualmente fora do agente."
        elif tool == "driver_manager" and action in {"printer_status", "printer_driver_info"}:
            target = params.get("printer_name") or params.get("query") or params.get("device_id")
            if target:
                summary = (
                    f"Nao consegui verificar a impressora '{target}'. "
                    "Confirme o nome exato, o IP ou o caminho compartilhado da impressora para eu tentar novamente."
                )
            else:
                summary = (
                    "Nao consegui continuar a configuracao da impressora porque ainda nao sei qual impressora de rede devo procurar. "
                    "Me informe o nome exibido da impressora, o IP ou o caminho compartilhado."
                )
            next_steps = [
                "Envie o nome da impressora, o endereco IP ou o caminho UNC no formato \\\\servidor\\impressora.",
                "Se souber o modelo, envie tambem o modelo para facilitar a busca do driver correto.",
            ]
            manual_option = (
                "Se preferir configurar manualmente, abra Configuracoes > Bluetooth e dispositivos > Impressoras e scanners > Adicionar dispositivo."
            )
        elif "path not allowed by sandbox" in message:
            target = params.get("path") or params.get("dest") or "o local solicitado"
            summary = (
                f"Nao consegui acessar {target} automaticamente porque esse local exige uma aprovacao explicita ou mais contexto. "
                "Se quiser, tente de novo e aprove a acao quando ela for solicitada."
            )
            manual_option = "Se preferir, abra esse local manualmente no Explorador de Arquivos e execute a etapa por conta propria."
        elif "validation:" in message:
            summary = (
                f"Nao consegui concluir '{task_title}' porque a tarefa foi bloqueada por uma validacao interna antes da execucao. "
                "Normalmente isso acontece quando falta um parametro importante ou quando a acao nao eh segura do jeito solicitado."
            )
            next_steps = ["Reenvie a instrucao com mais detalhes sobre o alvo, caminho, nome ou contexto necessario."]
        elif "timeout" in message:
            summary = (
                f"Nao consegui concluir '{task_title}' a tempo. "
                "A operacao demorou mais do que o esperado e foi interrompida."
            )
            next_steps = ["Tente novamente com mais contexto ou com um alvo mais especifico."]
        elif tool == "desktop" and action == "open_app":
            target = params.get("app_name") or params.get("app_path") or "o aplicativo solicitado"
            summary = (
                f"Nao consegui abrir {target}. "
                "O aplicativo foi localizado, mas a etapa de inicializacao falhou antes que eu pudesse continuar."
            )
            next_steps = [
                "Verifique se o aplicativo abre manualmente nesta sessao do Windows.",
                "Se ele abrir com janela inicial, tente novamente para eu continuar com a automacao.",
            ]
        else:
            summary = (
                f"Nao consegui concluir '{task_title}'. "
                "A tarefa encontrou um problema interno antes de terminar."
            )
            next_steps = ["Se quiser tentar de novo, reenvie a instrucao com mais detalhes sobre o objetivo ou o alvo exato."]

        if manual_option:
            summary = f"{summary} {manual_option}"

        return {
            "verdict": "failed",
            "summary": summary,
            "details": {
                "task_id": (task or {}).get("id"),
                "task_title": task_title,
                "tool": tool,
                "action": action,
                "technical_error": raw_error,
                "next_steps": next_steps,
                "manual_option": manual_option,
                "recovery": recovery,
                "autonomy": state.get("autonomy") or {},
            },
            "recommended_action": recovery,
        }

    async def _fail_run(self, state: Dict[str, Any], error: str) -> None:
        state["error"] = error
        final_reflection = self._user_facing_failure_reflection(state, error)
        state["outputs"]["final_reflection"] = final_reflection
        await self._set_run_status(state, "failed")
        await self._emit(
            "run_failed",
            {
                "request_id": state["request_id"],
                "status": state["status"],
                "error": error,
                "user_message": final_reflection["summary"],
                "reflection": final_reflection,
            },
            state=state,
        )
        await self._finalize_goal_for_run(state, completed=False, error=error)

    async def _finalize_goal_for_run(self, state: Dict[str, Any], *, completed: bool, summary: Optional[str] = None, error: Optional[str] = None) -> None:
        goal_id = state.get("goal_id")
        if not goal_id:
            return
        try:
            if completed:
                await self.goal_queue.mark_completed(goal_id, result_summary=summary)
            else:
                result = await self.goal_queue.mark_failed(goal_id, error or "run failed")
                if result and result.get("status") == "retrying":
                    logger.info("Goal %s scheduled for retry", goal_id)
        except Exception:
            logger.debug("Failed to finalize goal %s", goal_id, exc_info=True)

        session_id = state.get("session_id")
        if session_id:
            try:
                session = await self.session_manager.get_session(session_id)
                if session:
                    total_steps = session.get("total_steps", 0) + len(state.get("tasks", []))
                    await self.session_manager.update_session(session_id, {"total_steps": total_steps})
            except Exception:
                logger.debug("Failed to update session %s step count", session_id, exc_info=True)

        job_id = f"job-{state['request_id']}"
        try:
            await self.persistence.delete_job_checkpoint(job_id)
        except Exception:
            pass

    async def _mark_run_cancelled(self, state: Dict[str, Any]) -> None:
        current_task_id = state.get("current_task_id")
        task_to_cancel = None
        if current_task_id:
            task_to_cancel = next((task for task in state["tasks"] if task["id"] == current_task_id), None)
        if task_to_cancel is None:
            task_to_cancel = next(
                (
                    task
                    for task in state["tasks"]
                    if task.get("status") not in {"completed", "failed", "cancelled", "rejected"}
                ),
                None,
            )
        if task_to_cancel is not None and task_to_cancel.get("status") != "cancelled":
            task_to_cancel["status"] = "cancelled"
            if not task_to_cancel.get("error"):
                task_to_cancel["error"] = "cancelled"
            await self._emit(
                "task_cancelled",
                {"request_id": state["request_id"], "task_id": task_to_cancel["id"], "error": "cancelled"},
                state=state,
            )
        state["error"] = "cancelled"
        await self._set_run_status(state, "cancelled")
        await self._emit(
            "run_cancelled",
            {"request_id": state["request_id"], "status": "cancelled", "error": "cancelled"},
            state=state,
        )

    def _cancel_event_for(self, request_id: str) -> asyncio.Event:
        return self.run_cancel_events.setdefault(request_id, asyncio.Event())

    def _raise_if_cancelled(self, state: Dict[str, Any]) -> None:
        if self._cancel_event_for(state["request_id"]).is_set():
            raise asyncio.CancelledError()

    async def _complete_run(
        self,
        state: Dict[str, Any],
        *,
        summary: str,
        details: Dict[str, Any],
        current_node: str,
    ) -> None:
        await self._set_run_status(state, "completed", current_node=current_node)
        final_reflection = {
            "verdict": "success",
            "summary": summary,
            "details": details,
        }
        state["outputs"]["final_reflection"] = final_reflection
        await self._emit(
            "run_finished",
            {"request_id": state["request_id"], "status": state["status"], "reflection": final_reflection},
            state=state,
        )
        await self._finalize_goal_for_run(state, completed=True, summary=summary)

    async def _set_run_status(
        self,
        state: Dict[str, Any],
        status: str,
        *,
        current_node: Optional[str] = None,
    ) -> None:
        state["status"] = status
        if current_node is not None:
            state["current_node"] = current_node
        state["last_updated"] = _now()
        await self._persist_state(state)

    async def _persist_state(self, state: Dict[str, Any]) -> None:
        state["last_updated"] = _now()
        await self.state_layer.save_run_state(state, _jsonable)

    def _autonomy_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return state.setdefault(
            "autonomy",
            {
                "pending_subgoals": [],
                "completed_subgoals": [],
                "observations": [],
                "hypotheses": [],
                "strategy_log": [],
                "goal_verifications": [],
            },
        )

    def _append_unique(self, items: List[Any], value: Any, *, limit: int = 50) -> None:
        if value in items:
            return
        items.append(value)
        if len(items) > limit:
            del items[0 : len(items) - limit]

    async def _checkpoint_agent_session(self, state: Dict[str, Any], session_update: Dict[str, Any]) -> None:
        session = state.setdefault("agent_session", {})
        session.update({key: _jsonable(value) for key, value in session_update.items() if key not in {"observation", "hypothesis", "strategy", "pending_subgoal", "completed_subgoal", "goal_verification"}})

        autonomy = self._autonomy_state(state)
        observation = session_update.get("observation")
        if observation is not None:
            autonomy["observations"].append(_jsonable(observation))
            autonomy["observations"] = autonomy["observations"][-100:]
        hypothesis = session_update.get("hypothesis")
        if hypothesis:
            self._append_unique(autonomy["hypotheses"], _jsonable(hypothesis))
        strategy = session_update.get("strategy")
        if strategy:
            autonomy["strategy_log"].append(_jsonable(strategy))
            autonomy["strategy_log"] = autonomy["strategy_log"][-100:]
        pending_subgoal = session_update.get("pending_subgoal")
        if pending_subgoal:
            self._append_unique(autonomy["pending_subgoals"], str(pending_subgoal))
        completed_subgoal = session_update.get("completed_subgoal")
        if completed_subgoal:
            completed_subgoal = str(completed_subgoal)
            autonomy["pending_subgoals"] = [item for item in autonomy["pending_subgoals"] if item != completed_subgoal]
            self._append_unique(autonomy["completed_subgoals"], completed_subgoal)
        goal_verification = session_update.get("goal_verification")
        if goal_verification:
            autonomy["goal_verifications"].append(_jsonable(goal_verification))
            autonomy["goal_verifications"] = autonomy["goal_verifications"][-100:]
        await self._persist_state(state)

        completed_tasks = sum(1 for t in state.get("tasks", []) if t.get("status") == "completed")
        total_tasks = len(state.get("tasks", []))
        try:
            await self.save_job_checkpoint(
                state["request_id"],
                step_index=completed_tasks,
                total_steps=total_tasks,
            )
        except Exception:
            logger.debug("Failed to save job checkpoint for %s", state.get("request_id"), exc_info=True)

    async def _record_task_observation(
        self,
        state: Dict[str, Any],
        *,
        task: Dict[str, Any],
        result: Optional[Dict[str, Any]] = None,
        recovery: Optional[Dict[str, Any]] = None,
        goal_verification: Optional[Dict[str, Any]] = None,
    ) -> None:
        action_result = ((result or {}).get("action_result") or {}) if result else {}
        if result and action_result.get("status") in {"succeeded", "partial"}:
            self._update_runtime_context(state, task, result)
            try:
                await self._auto_persist_world_model(task, result)
            except Exception:
                logger.debug("world model auto-persist failed for task %s", task.get("id"), exc_info=True)
        summary = action_result.get("summary") or (result or {}).get("tool_result", {}).get("message")
        observation = {
            "task_id": task.get("id"),
            "tool": task.get("tool"),
            "action": task.get("action"),
            "status": action_result.get("status") or task.get("status"),
            "summary": summary,
            "issue": action_result.get("issue"),
            "recovery": recovery,
            "goal_verification": goal_verification,
        }
        await self._checkpoint_agent_session(
            state,
            {
                "observation": observation,
                "completed_subgoal": task.get("title") if observation["status"] in {"succeeded", "completed"} else None,
                "goal_verification": goal_verification,
            },
        )

    async def _auto_persist_world_model(self, task: Dict[str, Any], result: Dict[str, Any]) -> None:
        """Automatically persist successful discoveries into the world model."""
        action_result = (result.get("action_result") or {})
        if action_result.get("status") != "succeeded":
            return
        tool = task.get("tool")
        action = task.get("action")
        params = task.get("params") or {}
        tool_result = result.get("tool_result") or {}
        details = tool_result.get("details") or tool_result

        if tool == "share_discovery" and action in {"list_mappings", "inspect_share", "inspect_corporate_share"}:
            mappings = details.get("mappings") or []
            for mapping in mappings[:20]:
                remote = mapping.get("RemotePath") or mapping.get("remote_path")
                local = mapping.get("LocalPath") or mapping.get("local_path")
                if remote:
                    name = local or remote.rsplit("\\", 1)[-1]
                    try:
                        await self.world_model.remember_share(
                            str(name), str(remote), local_path=str(local) if local else None,
                            source="share_discovery",
                        )
                    except Exception:
                        pass

        if tool == "desktop" and action == "open_app":
            app_name = params.get("app_name") or ""
            app_path = params.get("app_path") or ""
            if app_name or app_path:
                try:
                    await self.world_model.remember_app_path(
                        app_name or app_path, app_path or app_name,
                        source="desktop_open_app",
                    )
                except Exception:
                    pass

        if tool == "software_inventory" and action in {"find_executable", "find_install_location"}:
            query = params.get("query") or ""
            exe_path = details.get("path") or details.get("exe_path") or details.get("location") or ""
            if query and exe_path:
                try:
                    await self.world_model.remember_app_path(query, str(exe_path), source="software_inventory")
                except Exception:
                    pass

        if tool == "windows_ui" and action in {"find_element", "wait_for_element"}:
            selector = params.get("selector") or {}
            title = params.get("title") or ""
            process_name = params.get("process_name") or ""
            if selector:
                selector_key = f"{process_name or title}:{selector.get('auto_id') or selector.get('title') or selector.get('control_type') or 'unknown'}"
                try:
                    await self.world_model.remember_selector(
                        selector_key, selector,
                        app_context=process_name or title,
                        source="ui_automation",
                    )
                except Exception:
                    pass

    async def _verify_task_goal_async(self, task: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        """Run the full semantic verification pipeline (goal + semantic + postcondition)."""
        return await self.semantic_verifier.verify(task, result)

    def _verify_task_goal(self, task: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
        """Synchronous wrapper kept for backward compatibility.

        Returns a basic goal verification using only GoalVerifier (no async
        postcondition checks). Callers that can ``await`` should prefer
        ``_verify_task_goal_async`` for the full pipeline.
        """
        action_result = (result.get("action_result") or {}) if isinstance(result, dict) else {}
        return self.semantic_verifier.goal_verifier.verify(task, action_result)

    async def _emit(self, event_type: str, payload: Dict[str, Any], *, state: Optional[Dict[str, Any]] = None) -> None:
        payload = _jsonable(payload)
        if state is not None:
            state["history"].append({"type": event_type, "timestamp": _now(), "payload": payload})
            await self._persist_state(state)
        await self.event_bus.emit_raw(event_type, payload, source="runtime_manager")

    def _graph_executor_for(self, state: Dict[str, Any]) -> Optional[GraphExecutor]:
        return self.operational_layer.graph_executor_for(state.get("_pipeline_graph"))

    async def _execute_task_with_recovery(self, state: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
        graph_exec = self._graph_executor_for(state)
        if graph_exec and "_graph" not in state:
            state["_graph"] = {
                "current_node": "executor",
                "traversal_entries": [],
                "traversal_visited": [],
                "visits": 0,
            }
        if graph_exec:
            state["_graph"]["current_node"] = "executor"
            entry = {"node_id": "executor", "node_type": NodeType.EXECUTOR.value, "result_keys": None, "error": None}
            state["_graph"]["traversal_entries"].append(entry)
            state["_graph"]["traversal_visited"].append("executor")
        return await self.action_executor.execute(state, task)

    async def _plan_tasks(self, state: Dict[str, Any]) -> Dict[str, Any]:
        inputs = state["inputs"]
        text = inputs.get("text") or inputs.get("prompt")
        if text:
            plan, validation = await self.cognitive_layer.parse_plan(text)
            if not validation.ok:
                return self._handle_non_actionable_input(text, validation)
            return {
                "tasks": [self._task_to_state(task) for task in plan.tasks],
                "kind": "tasks",
                "message": None,
            }

        if inputs.get("command"):
            return {
                "tasks": [
                    self._task_to_state(
                        Task(id=f"task-{uuid.uuid4().hex[:8]}", tool="shell", action="run", params={"command": inputs["command"]})
                    )
                ],
                "kind": "tasks",
                "message": None,
            }
        raise ValueError("inputs.text or inputs.command is required")

    def _handle_non_actionable_input(self, text: str, validation: Any) -> Dict[str, Any]:
        normalized = text.strip().lower()
        if re.fullmatch(r"(oi|ola|olá|hello|hi|bom dia|boa tarde|boa noite)[!. ]*", normalized):
            return {
                "tasks": [],
                "kind": "greeting",
                "message": (
                    "Ola! Eu executo acoes de shell, filesystem, memory, browser, package_manager, os e desktop. "
                    "Exemplos: 'run command Get-Date', "
                    "'list processes', 'open https://example.com' ou "
                    "'remember project is agente'."
                ),
            }
        return {
            "tasks": [],
            "kind": "unsupported_input",
            "message": (
                "Nao encontrei uma acao executavel nessa mensagem. "
                "Tente um pedido como: 'search web driver hp laserjet', "
                "'write hello to C:\\Temp\\agente.txt', "
                "'list windows' ou "
                "'remember project is agente'."
            ),
        }

    def _agentic_task_from_tool_call(self, tool_name: str, arguments: Dict[str, Any], step: int) -> Dict[str, Any]:
        task_id = f"agentic-{step}-{uuid.uuid4().hex[:6]}"
        task = normalize_agentic_task(tool_name, arguments, task_id)
        task = self._apply_execution_strategy(task)
        return task

    def _apply_execution_strategy(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """Apply execution strategy to potentially redirect a task to a more efficient path."""
        tool = task.get("tool", "")
        action = task.get("action", "")
        params = task.get("params") or {}

        decision = self.cognitive_layer.decide_execution_mode(
            tool=tool, action=action, params=params,
            available_tools=list(canonical_supported_tools()),
        )

        if decision["mode"] == "internal" and decision.get("alternative"):
            alt = decision["alternative"]
            task["original_tool"] = tool
            task["original_action"] = action
            task["tool"] = alt["tool"]
            task["action"] = alt["action"]
            task["params"] = alt.get("params", params)
            task["execution_strategy"] = {
                "mode": decision["mode"],
                "reason": decision["reason"],
                "redirected": True,
            }
            task["title"] = f"{task.get('title', '')} (via {alt['tool']}.{alt['action']})"
        else:
            task["execution_strategy"] = {
                "mode": decision["mode"],
                "reason": decision["reason"],
                "redirected": False,
            }

        return task

    def _task_to_state(self, task: Task) -> Dict[str, Any]:
        title = self._task_title(task)
        params = _jsonable(task.params)
        return {
            "id": task.id,
            "title": title,
            "tool": task.tool,
            "action": task.action,
            "params": params,
            "intent": {
                "tool": task.tool,
                "action": task.action,
                "params": params,
                "task_id": task.id,
                "title": title,
            },
            "policy_metadata": action_metadata(task.tool, task.action),
            "depends_on": list(task.depends_on),
            "status": "pending",
            "attempt": 0,
            "max_attempts": 2,
            "recovery": None,
            "requires_approval": False,
            "approval_granted": False,
            "approval_id": None,
            "approval_grant_id": None,
            "approval_scope": None,
            "pause_context": None,
            "result": None,
            "error": None,
            "reflection": None,
        }

    def _task_title(self, task: Task) -> str:
        return task_title(task.tool, task.action, task.params)

    def _describe_approval(self, task: Dict[str, Any], safety: Dict[str, Any]) -> Dict[str, Any]:
        tool = task.get("tool")
        action = task.get("action")
        params = task.get("params") or {}
        approval_meta = safety.get("approval") or {}
        title = f"Approve task: {task['title']}"
        reason = safety["reason"]
        details: Dict[str, Any] = {
            "tool": tool,
            "action": action,
            "predicted_effects": approval_meta.get("predicted_effects") or [],
            "reversibility": approval_meta.get("reversibility"),
            "confirmation_level": approval_meta.get("mode", "single"),
        }

        if tool == "filesystem":
            path = params.get("path")
            dest = params.get("dest")
            details.update({"path": path, "dest": dest})
            if action == "delete" and path:
                title = f"Approve delete: {path}"
                reason = f"Isto vai excluir: {path}"
            elif action == "move" and path and dest:
                title = f"Approve move: {path} -> {dest}"
                reason = f"Isto vai mover de {path} para {dest}"
            elif action == "copy" and path and dest:
                title = f"Approve copy: {path} -> {dest}"
                reason = f"Isto vai copiar de {path} para {dest}"
            elif action == "write" and path:
                title = f"Approve write: {path}"
                reason = f"Isto vai gravar/alterar: {path}"
            elif action in {"mkdir", "create_structure"} and path:
                title = f"Approve create: {path}"
                reason = f"Isto vai criar conteudo em: {path}"
            elif action in {"clean_temp", "organize_directory", "detect_duplicates", "find_files", "list", "stat", "read"} and path:
                title = f"Approve filesystem access: {path}"
                reason = f"{safety['reason']}. Alvo: {path}"
            elif path:
                reason = f"{safety['reason']}. Alvo: {path}"

        if tool == "shell":
            command = normalize_command(params.get("command", ""))
            command_explanation = explain_command(command, safety.get("risk"))
            title = "Permitir comando do sistema"
            reason = "Revise o comando abaixo e confirme se o agente pode executa-lo."
            details.update(
                {
                    "command": command,
                    "command_explanation": command_explanation,
                    "shell": params.get("shell", "powershell"),
                }
            )

        if approval_meta.get("mode") == "double":
            reason = f"{reason}. Esta acao exige dupla confirmacao porque pode ser destrutiva ou dificil de reverter."

        details["available_scopes"] = self._available_approval_scopes(task, safety)
        return {"title": title, "reason": reason, "details": details}

    def _available_approval_scopes(self, task: Dict[str, Any], safety: Dict[str, Any]) -> List[str]:
        scopes = ["single"]
        if self.safety.policy.supports_run_scope(
            task,
            risk=safety.get("risk"),
            metadata=(task.get("policy_metadata") or action_metadata(task.get("tool"), task.get("action"))),
        ):
            scopes.append("run_scope")
        return scopes

    def _grant_family(self, task: Dict[str, Any]) -> str:
        return self.safety.policy.grant_family(task)

    def _activate_run_scope_grant(self, state: Dict[str, Any], task: Dict[str, Any], *, existing_approval: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        available_scopes = (((existing_approval.get("details") or {}).get("available_scopes")) or [])
        if "run_scope" not in available_scopes:
            return None
        family = self._grant_family(task)
        for grant in state.get("approval_grants", []):
            if grant.get("status") == "active" and grant.get("family") == family:
                grant["last_used_at"] = _now()
                task["approval_grant_id"] = grant["grant_id"]
                return grant
        grant = {
            "grant_id": str(uuid.uuid4()),
            "request_id": state["request_id"],
            "scope": "run_scope",
            "status": "active",
            "family": family,
            "tool": task.get("tool"),
            "action": task.get("action"),
            "max_risk_level": "medium",
            "approved_at": _now(),
            "approved_via_task_id": task.get("id"),
            "approved_via_approval_id": task.get("approval_id"),
            "title": f"Fluxo aprovado para {task.get('title')}",
            "reason": "Grant ativo para continuar este fluxo sem novas aprovacoes compativeis.",
        }
        state.setdefault("approval_grants", []).append(grant)
        task["approval_grant_id"] = grant["grant_id"]
        return grant

    def _context_window_from_value(self, value: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(value, dict):
            return None
        title = value.get("title")
        process_name = value.get("process_name")
        hwnd = value.get("hwnd")
        if title is None and process_name is None and hwnd is None:
            return None
        return {
            "hwnd": hwnd,
            "title": title,
            "process_name": process_name,
        }

    def _derive_runtime_context(self, task: Dict[str, Any], result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        params = task.get("params") or {}
        action_result = ((result or {}).get("action_result") or {}) if result else {}
        data = action_result.get("data") or {}
        window = self._context_window_from_value(
            {
                "hwnd": params.get("hwnd"),
                "title": params.get("title"),
                "process_name": params.get("process_name"),
            }
        )
        if not window:
            window = self._context_window_from_value(data.get("window") or {})
        if not window and isinstance(data.get("element"), dict):
            window = self._context_window_from_value(data["element"])
        if not window and task.get("tool") == "desktop" and task.get("action") == "open_app":
            window = self._context_window_from_value(
                {
                    "process_name": data.get("process_name") or params.get("app_name"),
                    "title": params.get("app_name"),
                }
            )
        ui_target = {
            key: value
            for key, value in {
                "selector": params.get("selector"),
                "element_id": params.get("element_id"),
                "title": params.get("title"),
                "process_name": params.get("process_name"),
                "hwnd": params.get("hwnd"),
            }.items()
            if value is not None
        }
        if not ui_target and isinstance(data.get("element"), dict):
            element = data["element"]
            ui_target = {
                key: value
                for key, value in {
                    "element_id": element.get("element_id"),
                    "title": element.get("title"),
                    "process_name": element.get("process_name"),
                    "hwnd": element.get("hwnd"),
                }.items()
                if value is not None
            }
        return {"window": window, "ui_target": ui_target or None, "updated_at": _now()}

    def _update_runtime_context(self, state: Dict[str, Any], task: Dict[str, Any], result: Optional[Dict[str, Any]] = None) -> None:
        context = self._derive_runtime_context(task, result)
        runtime_context = state.setdefault("runtime_context", {"window": None, "ui_target": None, "updated_at": None})
        if context.get("window"):
            runtime_context["window"] = context["window"]
        if context.get("ui_target"):
            runtime_context["ui_target"] = context["ui_target"]
        runtime_context["updated_at"] = context["updated_at"]

    def _capture_pause_context(self, state: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
        derived = self._derive_runtime_context(task)
        runtime_context = state.get("runtime_context") or {}
        pause_context = {
            "window": derived.get("window") or runtime_context.get("window"),
            "ui_target": derived.get("ui_target") or runtime_context.get("ui_target"),
            "captured_at": _now(),
        }
        task["pause_context"] = pause_context
        return pause_context

    async def _restore_execution_context(self, state: Dict[str, Any], task: Dict[str, Any]) -> None:
        if task.get("tool") not in {"desktop", "windows_ui", "office", "browser"}:
            return
        context = task.get("pause_context") or state.get("runtime_context") or {}
        window = context.get("window") or {}
        if not isinstance(window, dict) or not any(window.get(key) for key in ("hwnd", "title", "process_name")):
            return
        try:
            if window.get("hwnd") or window.get("title"):
                await self.desktop_agent.focus_window(hwnd=window.get("hwnd"), title=window.get("title"))
                return
            if window.get("process_name"):
                match = await self.desktop_agent.wait_for_window(process_name=window["process_name"], timeout_seconds=3)
                matched_window = (match.get("window") or {}) if isinstance(match, dict) else {}
                if matched_window.get("hwnd") or matched_window.get("title"):
                    await self.desktop_agent.focus_window(
                        hwnd=matched_window.get("hwnd"),
                        title=matched_window.get("title"),
                    )
        except Exception:
            logger.debug("Unable to restore execution context before continuing task %s", task.get("id"), exc_info=True)

    async def _create_runtime_approval(self, state: Dict[str, Any], task: Dict[str, Any], safety: Dict[str, Any]) -> Dict[str, Any]:
        approval_id = str(uuid.uuid4())
        approval_copy = self._describe_approval(task, safety)
        approval = {
            "approval_id": approval_id,
            "request_id": state["request_id"],
            "task_id": task["id"],
            "title": approval_copy["title"],
            "reason": approval_copy["reason"],
            "status": "pending",
            "risk": safety.get("risk"),
            "details": approval_copy["details"],
            "approval_mode": (safety.get("approval") or {}).get("mode", "single"),
        }
        task["requires_approval"] = True
        state["approvals"].append(approval)
        self.approval_run_map[approval_id] = state["request_id"]
        self.approval_waiters[approval_id] = asyncio.get_event_loop().create_future()
        await self.persistence.create_approval(
            approval_id,
            state["request_id"],
            "",
            approval,
            task_id=task["id"],
        )
        await self._emit("approval_requested", {"request_id": state["request_id"], "approval": approval}, state=state)
        await self._emit(
            "run_paused",
            {
                "request_id": state["request_id"],
                "status": "awaiting_approval",
                "approval_id": approval_id,
                "task_id": task["id"],
            },
            state=state,
        )
        return approval

    async def _wait_for_approval(self, approval_id: str) -> str:
        future = self.approval_waiters.get(approval_id)
        if future is None:
            future = asyncio.get_event_loop().create_future()
            self.approval_waiters[approval_id] = future
        request_id = self.approval_run_map.get(approval_id)
        cancel_event = self.run_cancel_events.get(request_id) if request_id else None
        if cancel_event is None:
            return await future

        cancel_task = asyncio.create_task(cancel_event.wait())
        done, pending = await asyncio.wait({future, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
        for pending_task in pending:
            pending_task.cancel()
        if cancel_task in done:
            return "cancelled"
        return await future

    def _resolved_approval_status(self, state: Dict[str, Any], task_id: str) -> Optional[str]:
        for approval in state.get("approvals", []):
            if approval.get("task_id") == task_id:
                status = approval.get("status")
                if status in {"approved", "rejected"}:
                    return status
        return None

    async def _pause_for_handoff(self, state: Dict[str, Any], handoff: Dict[str, Any]) -> None:
        existing = next((item for item in state.get("handoffs", []) if item.get("handoff_id") == handoff["handoff_id"]), None)
        if existing is None:
            state.setdefault("handoffs", []).append(handoff)
        else:
            existing.update(handoff)
        state["pending_handoff"] = handoff
        await self._set_run_status(state, "awaiting_input", current_node="handoff")
        await self._emit(
            "user_input_requested",
            {"request_id": state["request_id"], "handoff": handoff},
            state=state,
        )
        await self._emit(
            "run_paused",
            {"request_id": state["request_id"], "status": "awaiting_input", "handoff_id": handoff["handoff_id"]},
            state=state,
        )

    def _handoff_for_recovery(self, state: Dict[str, Any], task: Dict[str, Any], error: str) -> Dict[str, Any]:
        payload = self.recovery_engine.question_for_failure(task, error)
        return {
            "handoff_id": str(uuid.uuid4()),
            "request_id": state["request_id"],
            "task_id": task["id"],
            "tool_call_id": None,
            "kind": payload["kind"],
            "title": payload["title"],
            "prompt": payload["prompt"],
            "reason": error,
            "status": "pending",
            "allow_free_text": payload["allow_free_text"],
            "options": payload["options"],
            "response": None,
        }

    async def _apply_handoff_response(self, state: Dict[str, Any], handoff: Dict[str, Any]) -> None:
        response = handoff.get("response")
        if response is None:
            return
        tool_call_id = handoff.get("tool_call_id")
        if tool_call_id:
            session = state.setdefault("agent_session", {})
            messages = list(session.get("messages") or [])
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps(response, default=str),
                }
            )
            session["messages"] = messages
            session["paused_reason"] = None
        handoff["status"] = "resolved"
        state["pending_handoff"] = None
        await self._persist_state(state)

