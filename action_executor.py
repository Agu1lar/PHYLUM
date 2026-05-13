from __future__ import annotations

import asyncio
from typing import Any, Dict

from action_models import action_needs_model_followup


class RunPausedError(RuntimeError):
    pass


class ActionExecutor:
    def __init__(self, runtime):
        self.runtime = runtime

    async def execute(self, state: Dict[str, Any], task: Dict[str, Any]) -> Dict[str, Any]:
        max_attempts = int(task.get("max_attempts") or 2)
        while True:
            self.runtime._raise_if_cancelled(state)
            state["current_task_id"] = task["id"]
            await self.runtime._set_run_status(state, "running", current_node="policy")
            effective_runtime_mode = state.get("runtime_mode") if self._is_agentic_task(state, task) else "heuristic"
            safety_result = await self.runtime.safety.execute(
                {
                    "inputs": state["inputs"],
                    "current_task": task,
                    "runtime_mode": effective_runtime_mode,
                    "approval_grants": state.get("approval_grants") or [],
                }
            )
            task["safety"] = safety_result["safety"]
            if safety_result["safety"].get("grant"):
                task["approval_grant_id"] = safety_result["safety"]["grant"].get("grant_id")
            if (
                state["inputs"].get("force_approval")
                and safety_result["safety"]["status"] == "allow"
                and not safety_result["safety"].get("grant")
            ):
                safety_result["safety"]["status"] = "require_approval"
                safety_result["safety"]["requires_approval"] = True
                safety_result["safety"]["reason"] = "run configured to await human approval"

            if safety_result["safety"]["status"] == "deny":
                task["status"] = "failed"
                task["error"] = safety_result["safety"]["reason"]
                state["error"] = task["error"]
                await self.runtime._emit(
                    "task_failed",
                    {"request_id": state["request_id"], "task_id": task["id"], "error": task["error"]},
                    state=state,
                )
                await self.runtime._fail_run(state, task["error"])
                raise RuntimeError(task["error"])

            if safety_result["safety"]["status"] == "require_approval":
                approval_status = self.runtime._resolved_approval_status(state, task["id"])
                if approval_status is None:
                    self.runtime._capture_pause_context(state, task)
                    task["status"] = "waiting_approval"
                    await self.runtime._set_run_status(state, "awaiting_approval", current_node="approval")
                    approval = await self.runtime._create_runtime_approval(state, task, safety_result["safety"])
                    task["approval_id"] = approval["approval_id"]
                    approval_status = await self.runtime._wait_for_approval(approval["approval_id"])
                if approval_status == "cancelled":
                    raise asyncio.CancelledError()
                if approval_status != "approved":
                    if self._is_agentic_task(state, task):
                        result = self._approval_rejected_result(task)
                        task["approval_granted"] = False
                        task["result"] = result
                        state["outputs"][task["id"]] = result
                        task["status"] = "blocked"
                        task["error"] = result["action_result"]["summary"]
                        task["reflection"] = {"verdict": "blocked", "summary": result["action_result"]["summary"], "details": result["action_result"], "recommended_action": None}
                        await self.runtime._emit(
                            "task_finished",
                            {
                                "request_id": state["request_id"],
                                "task_id": task["id"],
                                "result": result,
                                "reflection": task["reflection"],
                                "attempt": task.get("attempt", 0),
                                "status": task["status"],
                            },
                            state=state,
                        )
                        return result
                    task["status"] = "rejected"
                    task["approval_granted"] = False
                    task["error"] = "approval rejected"
                    state["error"] = task["error"]
                    await self.runtime._emit(
                        "task_failed",
                        {"request_id": state["request_id"], "task_id": task["id"], "error": task["error"]},
                        state=state,
                    )
                    await self.runtime._fail_run(state, task["error"])
                    raise RuntimeError(task["error"])
                task["approval_granted"] = True

            try:
                task["attempt"] = int(task.get("attempt") or 0) + 1
                task["status"] = "running"
                await self.runtime._set_run_status(state, "running", current_node="tool_execution")
                await self.runtime._restore_execution_context(state, task)
                await self.runtime._emit("task_started", {"request_id": state["request_id"], "task": task}, state=state)
                self.runtime._raise_if_cancelled(state)
                result = await self.runtime.tool_router.execute(
                    {
                        "inputs": state["inputs"],
                        "current_task": task,
                        "cancel_event": self.runtime._cancel_event_for(state["request_id"]),
                    }
                )
                goal_verification = self.runtime._verify_task_goal(task, result)
                action_result = result.get("action_result") or {}
                action_result["goal"] = goal_verification
                if action_result.get("status") == "succeeded" and not goal_verification.get("satisfied", False):
                    action_result["status"] = "partial"
                    action_result["summary"] = (
                        f"{action_result.get('summary', 'A acao foi executada.')} "
                        "A meta ainda precisa de verificacao adicional antes de ser considerada concluida."
                    ).strip()
                    result["action_result"] = action_result
                state["outputs"][task["id"]] = result
                task["result"] = result
                reflection_result = await self.runtime.reflection.execute(
                    {
                        "inputs": state["inputs"],
                        "current_task": task,
                        "current_task_result": result,
                        "current_task_error": None,
                    }
                )
                task["reflection"] = reflection_result["reflection"]
                action_status = action_result.get("status", "failed")

                if self._is_agentic_task(state, task):
                    if action_needs_model_followup(action_result):
                        task["recovery"] = self.runtime.recovery_engine.classify_action_result(
                            task=task,
                            action_result=action_result,
                            attempt=int(task.get("attempt") or 1),
                            max_attempts=max_attempts,
                        )
                        state["recovery"] = {
                            "task_id": task["id"],
                            "classification": task["recovery"]["classification"],
                            "reason": task["recovery"]["reason"],
                            "suggested_action": task["recovery"].get("suggested_action"),
                        }
                        result["recovery"] = task["recovery"]
                    await self.runtime._record_task_observation(
                        state,
                        task=task,
                        result=result,
                        recovery=task.get("recovery"),
                        goal_verification=goal_verification,
                    )
                    task["status"] = self._task_status_for_action_status(action_status)
                    state["error"] = None
                    await self.runtime._emit(
                        "task_finished",
                        {
                            "request_id": state["request_id"],
                            "task_id": task["id"],
                            "result": result,
                            "reflection": task["reflection"],
                            "attempt": task["attempt"],
                            "status": task["status"],
                        },
                        state=state,
                    )
                    return result

                await self.runtime._record_task_observation(
                    state,
                    task=task,
                    result=result,
                    recovery=task.get("recovery"),
                    goal_verification=goal_verification,
                )
                task["status"] = self._task_status_for_action_status(action_status)
                if action_status in {"partial", "needs_input", "blocked"}:
                    state["error"] = None
                    await self.runtime._emit(
                        "task_finished",
                        {
                            "request_id": state["request_id"],
                            "task_id": task["id"],
                            "result": result,
                            "reflection": task["reflection"],
                            "attempt": task["attempt"],
                            "status": task["status"],
                        },
                        state=state,
                    )
                    return result
                if action_status != "succeeded":
                    raise RuntimeError(task["reflection"]["summary"])

                task["status"] = "completed"
                state["error"] = None
                await self.runtime._emit(
                    "task_finished",
                    {
                        "request_id": state["request_id"],
                        "task_id": task["id"],
                        "result": result,
                        "reflection": task["reflection"],
                        "attempt": task["attempt"],
                        "status": task["status"],
                    },
                    state=state,
                )
                return result
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                task["status"] = "failed"
                task["error"] = str(exc)
                reflection_result = await self.runtime.reflection.execute(
                    {
                        "inputs": state["inputs"],
                        "current_task": task,
                        "current_task_result": task.get("result"),
                        "current_task_error": str(exc),
                    }
                )
                task["reflection"] = reflection_result["reflection"]
                task["recovery"] = self.runtime.recovery_engine.classify(
                    task=task,
                    error=str(exc),
                    attempt=int(task.get("attempt") or 1),
                    max_attempts=max_attempts,
                )
                state["recovery"] = {
                    "task_id": task["id"],
                    "classification": task["recovery"]["classification"],
                    "reason": task["recovery"]["reason"],
                    "suggested_action": task["recovery"].get("suggested_action"),
                }
                await self.runtime._record_task_observation(
                    state,
                    task=task,
                    result=task.get("result"),
                    recovery=task["recovery"],
                    goal_verification=((task.get("result") or {}).get("action_result") or {}).get("goal"),
                )
                if task["recovery"]["needs_user"]:
                    handoff = self.runtime._handoff_for_recovery(state, task, str(exc))
                    await self.runtime._pause_for_handoff(state, handoff)
                    raise RunPausedError() from exc
                if task["recovery"]["retryable"]:
                    task["status"] = "retry_scheduled"
                    await self.runtime._set_run_status(state, "recovering", current_node="recovery")
                    await self.runtime._emit(
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
                await self.runtime._emit(
                    "task_failed",
                    {"request_id": state["request_id"], "task_id": task["id"], "error": str(exc), "reflection": task["reflection"]},
                    state=state,
                )
                await self.runtime._fail_run(state, str(exc))
                raise

    def _is_agentic_task(self, state: Dict[str, Any], task: Dict[str, Any]) -> bool:
        return state.get("runtime_mode") == "agentic" and str(task.get("id", "")).startswith("agentic-")

    def _task_status_for_action_status(self, action_status: str) -> str:
        if action_status == "succeeded":
            return "completed"
        if action_status == "needs_input":
            return "needs_input"
        if action_status == "blocked":
            return "blocked"
        if action_status == "partial":
            return "partial"
        return "failed"

    def _approval_rejected_result(self, task: Dict[str, Any]) -> Dict[str, Any]:
        action_result = {
            "status": "blocked",
            "summary": "A acao foi rejeitada na aprovacao. Posso tentar outro caminho se voce quiser.",
            "tool": task["tool"],
            "action": task["action"],
            "semantic_type": "mutation",
            "target": task.get("params") or {},
            "data": {},
            "effects": {"changed": False, "predicted_effects": [], "artifacts": [], "before": None, "after": None, "rollback": {"available": False, "reference": None}},
            "issue": {
                "kind": "approval_rejected",
                "code": "APPROVAL_REJECTED",
                "message": "The user rejected this action during approval.",
                "retryable": False,
                "user_action_required": "choose_alternative",
                "missing_fields": [],
                "candidates": [],
                "details": {},
            },
            "diagnostics": {},
            "approval": None,
        }
        return {
            "tool": task["tool"],
            "action": task["action"],
            "task_id": task["id"],
            "tool_result": {"success": False, "message": "approval_rejected", "details": {"error": "approval rejected"}},
            "action_result": action_result,
        }
