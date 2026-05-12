from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from pydantic import BaseModel

from agent_persistence import Persistence
from agentic_loop import AgenticLoop
from canonical_tools import normalize_agentic_task, supported_tools as canonical_supported_tools, task_title
from credential_store import CredentialStore
from multi_provider_client import MultiProviderClient
from nodes_reflection import ReflectionNode
from nodes_safety import SafetyNode
from nodes_tool_router import ToolRouterNode
from planner_agent import PlannerAgent
from planner_models import Task
from recovery_engine import RecoveryEngine

logger = logging.getLogger(__name__)


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


class RuntimeManager:
    def __init__(
        self,
        emitter: Callable[[Dict[str, Any]], Awaitable[None]],
        *,
        credential_store: Optional[CredentialStore] = None,
        provider_client: Optional[MultiProviderClient] = None,
    ):
        self.persistence = Persistence.get()
        self.emitter = emitter
        self.credential_store = credential_store or CredentialStore(self.persistence)
        self.provider_client = provider_client or MultiProviderClient()
        self.planner = PlannerAgent(supported_tools=canonical_supported_tools())
        self.safety = SafetyNode("safety")
        self.tool_router = ToolRouterNode("tool_router")
        self.reflection = ReflectionNode("reflection")
        self.recovery_engine = RecoveryEngine()
        self.agentic_loop = AgenticLoop(
            client=self.provider_client,
            safety=self.safety,
            tool_router=self.tool_router,
            reflection=self.reflection,
        )
        self.active_runs: Dict[str, Dict[str, Any]] = {}
        self.run_tasks: Dict[str, asyncio.Task] = {}
        self.run_cancel_events: Dict[str, asyncio.Event] = {}
        self.approval_waiters: Dict[str, asyncio.Future] = {}
        self.approval_run_map: Dict[str, str] = {}

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
                await asyncio.wait_for(task, timeout=timeout)
            else:
                await task
        except asyncio.CancelledError:
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

    async def resolve_approval(self, approval_id: str, status: str) -> Dict[str, Any]:
        await self.persistence.set_approval(approval_id, status)
        approval = await self.persistence.get_approval(approval_id)
        if approval is None:
            raise KeyError(approval_id)
        request_id = approval.get("request_id")
        run_state = await self._ensure_active_state(request_id)
        if run_state is not None:
            for existing in run_state["approvals"]:
                if existing["approval_id"] == approval_id:
                    existing["status"] = status
            for task in run_state.get("tasks", []):
                if task.get("approval_id") == approval_id and status == "approved":
                    task["status"] = "approved"
            await self._persist_state(run_state)
        waiter = self.approval_waiters.pop(approval_id, None)
        if waiter is not None and not waiter.done():
            waiter.set_result(status)
        payload = {
            "request_id": request_id,
            "approval_id": approval_id,
            "status": status,
            "task_id": approval.get("task_id"),
        }
        await self._emit("approval_resolved", payload)
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
            "handoffs": [],
            "pending_handoff": None,
            "agent_session": {},
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
                await self._run_local_heuristic_pipeline(state)
                return

            use_manual_assist, reason = await self._should_use_manual_assist(state)
            if use_manual_assist:
                if state["inputs"].get("allow_local_execution"):
                    await self._run_local_heuristic_pipeline(state)
                else:
                    await self._run_manual_assist_pipeline(state, reason=reason)
            elif state["runtime_mode"] == "agentic":
                await self._run_agentic_pipeline(state)
            else:
                await self._run_local_heuristic_pipeline(state)
        except RunPausedError:
            return
        except asyncio.CancelledError:
            await self._mark_run_cancelled(state)
        except Exception as exc:
            logger.exception("Run %s failed unexpectedly", request_id)
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
        for task in state["tasks"]:
            if task.get("status") == "completed":
                continue
            if task.get("status") == "manual_step":
                task["status"] = "pending"
            await self._emit("task_planned", {"request_id": state["request_id"], "task": task}, state=state)
            await self._execute_task_with_recovery(state, task)

        await self._complete_run(
            state,
            summary=f"Completed {len(state['tasks'])} task(s)",
            details={"completed_tasks": [task["id"] for task in state["tasks"]], "execution_mode": "local_heuristic"},
            current_node="reflection",
        )

    async def _run_agentic_pipeline(self, state: Dict[str, Any]) -> None:
        provider = state.get("provider")
        if not provider:
            await self._run_manual_assist_pipeline(state, reason="no provider configured for API-first mode")
            return
        provider_config = await self.credential_store.resolve_runtime_config(provider, model=state.get("model"))
        result = await self.agentic_loop.run(
            state=state,
            provider_config=provider_config,
            emit=lambda event_type, payload: self._emit(event_type, payload, state=state),
            task_factory=self._agentic_task_from_tool_call,
            execute_task=self._execute_task_with_recovery,
            cancel_event=self.run_cancel_events[state["request_id"]],
            session=state.get("agent_session") or None,
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

    async def _fail_run(self, state: Dict[str, Any], error: str) -> None:
        state["error"] = error
        await self._set_run_status(state, "failed")
        await self._emit(
            "run_failed",
            {"request_id": state["request_id"], "status": state["status"], "error": error},
            state=state,
        )

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
        await self.persistence.save_kv(f"state:{state['request_id']}", _jsonable(state))

    async def _emit(self, event_type: str, payload: Dict[str, Any], *, state: Optional[Dict[str, Any]] = None) -> None:
        payload = _jsonable(payload)
        event = {"type": event_type, "payload": payload}
        if state is not None:
            state["history"].append({"type": event_type, "timestamp": _now(), "payload": payload})
            await self._persist_state(state)
        await self.emitter(event)

    async def _execute_task_with_recovery(self, state: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
        max_attempts = int(task.get("max_attempts") or 2)
        while True:
            self._raise_if_cancelled(state)
            state["current_task_id"] = task["id"]
            await self._set_run_status(state, "running", current_node="safety")
            safety_result = await self.safety.execute({"inputs": state["inputs"], "current_task": task})
            task["safety"] = safety_result["safety"]
            if state["inputs"].get("force_approval") and safety_result["safety"]["status"] == "allow":
                safety_result["safety"]["status"] = "require_approval"
                safety_result["safety"]["requires_approval"] = True
                safety_result["safety"]["reason"] = "run configured to await human approval"

            if safety_result["safety"]["status"] == "deny":
                task["status"] = "failed"
                task["error"] = safety_result["safety"]["reason"]
                state["error"] = task["error"]
                await self._emit("task_failed", {"request_id": state["request_id"], "task_id": task["id"], "error": task["error"]}, state=state)
                await self._fail_run(state, task["error"])
                raise RuntimeError(task["error"])

            if safety_result["safety"]["status"] == "require_approval":
                approval_status = self._resolved_approval_status(state, task["id"])
                if approval_status is None:
                    task["status"] = "waiting_approval"
                    await self._set_run_status(state, "awaiting_approval", current_node="safety")
                    approval = await self._create_runtime_approval(state, task, safety_result["safety"])
                    task["approval_id"] = approval["approval_id"]
                    approval_status = await self._wait_for_approval(approval["approval_id"])
                if approval_status == "cancelled":
                    raise asyncio.CancelledError()
                if approval_status != "approved":
                    task["status"] = "rejected"
                    task["error"] = "approval rejected"
                    state["error"] = task["error"]
                    await self._emit(
                        "task_failed",
                        {"request_id": state["request_id"], "task_id": task["id"], "error": task["error"]},
                        state=state,
                    )
                    await self._fail_run(state, task["error"])
                    raise RuntimeError(task["error"])

            try:
                task["attempt"] = int(task.get("attempt") or 0) + 1
                task["status"] = "running"
                await self._set_run_status(state, "running", current_node="tool_router")
                await self._emit("task_started", {"request_id": state["request_id"], "task": task}, state=state)
                self._raise_if_cancelled(state)
                result = await self.tool_router.execute(
                    {
                        "inputs": state["inputs"],
                        "current_task": task,
                        "cancel_event": self._cancel_event_for(state["request_id"]),
                    }
                )
                state["outputs"][task["id"]] = result
                task["result"] = result
                reflection_result = await self.reflection.execute(
                    {
                        "inputs": state["inputs"],
                        "current_task": task,
                        "current_task_result": result,
                        "current_task_error": None,
                    }
                )
                task["reflection"] = reflection_result["reflection"]
                if task["reflection"]["verdict"] != "success":
                    raise RuntimeError(task["reflection"]["summary"])
                task["status"] = "completed"
                state["error"] = None
                await self._emit(
                    "task_finished",
                    {
                        "request_id": state["request_id"],
                        "task_id": task["id"],
                        "result": result,
                        "reflection": task["reflection"],
                        "attempt": task["attempt"],
                    },
                    state=state,
                )
                return result
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                task["status"] = "failed"
                task["error"] = str(exc)
                reflection_result = await self.reflection.execute(
                    {
                        "inputs": state["inputs"],
                        "current_task": task,
                        "current_task_result": task.get("result"),
                        "current_task_error": str(exc),
                    }
                )
                task["reflection"] = reflection_result["reflection"]
                task["recovery"] = self.recovery_engine.classify(
                    task=task,
                    error=str(exc),
                    attempt=int(task.get("attempt") or 1),
                    max_attempts=max_attempts,
                )
                state["recovery"] = {
                    "task_id": task["id"],
                    "classification": task["recovery"]["classification"],
                    "reason": task["recovery"]["reason"],
                }
                if task["recovery"]["needs_user"]:
                    handoff = self._handoff_for_recovery(state, task, str(exc))
                    await self._pause_for_handoff(state, handoff)
                    raise RunPausedError() from exc
                if task["recovery"]["retryable"]:
                    task["status"] = "retry_scheduled"
                    await self._set_run_status(state, "recovering", current_node="recovery")
                    await self._emit(
                        "task_retry_scheduled",
                        {
                            "request_id": state["request_id"],
                            "task_id": task["id"],
                            "attempt": task["attempt"],
                            "classification": task["recovery"],
                        },
                        state=state,
                    )
                    await asyncio.sleep(min(2 ** int(task["attempt"]), 5))
                    continue
                state["error"] = str(exc)
                await self._emit(
                    "task_failed",
                    {"request_id": state["request_id"], "task_id": task["id"], "error": str(exc), "reflection": task["reflection"]},
                    state=state,
                )
                await self._fail_run(state, str(exc))
                raise

    async def _plan_tasks(self, state: Dict[str, Any]) -> Dict[str, Any]:
        inputs = state["inputs"]
        text = inputs.get("text") or inputs.get("prompt")
        if text:
            plan, validation = await self.planner.parse(text)
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
        return normalize_agentic_task(tool_name, arguments, task_id)

    def _task_to_state(self, task: Task) -> Dict[str, Any]:
        title = self._task_title(task)
        return {
            "id": task.id,
            "title": title,
            "tool": task.tool,
            "action": task.action,
            "params": _jsonable(task.params),
            "depends_on": list(task.depends_on),
            "status": "pending",
            "attempt": 0,
            "max_attempts": 2,
            "recovery": None,
            "requires_approval": False,
            "approval_id": None,
            "result": None,
            "error": None,
            "reflection": None,
        }

    def _task_title(self, task: Task) -> str:
        return task_title(task.tool, task.action, task.params)

    async def _create_runtime_approval(self, state: Dict[str, Any], task: Dict[str, Any], safety: Dict[str, Any]) -> Dict[str, Any]:
        approval_id = str(uuid.uuid4())
        approval = {
            "approval_id": approval_id,
            "request_id": state["request_id"],
            "task_id": task["id"],
            "title": f"Approve task: {task['title']}",
            "reason": safety["reason"],
            "status": "pending",
            "risk": safety.get("risk"),
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


class RunPausedError(RuntimeError):
    pass
