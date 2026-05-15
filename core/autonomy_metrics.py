# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Autonomy metrics — steps to success, handoffs, recoveries, interrupted loops."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

KV_PREFIX = "autonomy_run:"


@dataclass
class RunAutonomyMetrics:
    request_id: str
    runtime_mode: str = ""
    provider: str = ""
    model: str = ""
    run_status: str = ""
    success: bool = False
    steps_to_success: int = 0
    agentic_steps: int = 0
    task_count: int = 0
    tasks_succeeded: int = 0
    handoffs_total: int = 0
    avoidable_handoffs: int = 0
    recoveries_total: int = 0
    effective_recoveries: int = 0
    interrupted_loop: bool = False
    interrupt_reason: str = ""
    cost_usd: float = 0.0
    total_tokens: int = 0
    recorded_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "runtime_mode": self.runtime_mode,
            "provider": self.provider,
            "model": self.model,
            "run_status": self.run_status,
            "success": self.success,
            "steps_to_success": self.steps_to_success,
            "agentic_steps": self.agentic_steps,
            "task_count": self.task_count,
            "tasks_succeeded": self.tasks_succeeded,
            "handoffs_total": self.handoffs_total,
            "avoidable_handoffs": self.avoidable_handoffs,
            "recoveries_total": self.recoveries_total,
            "effective_recoveries": self.effective_recoveries,
            "avoidable_handoff_rate": round(self.avoidable_handoffs / self.handoffs_total, 4) if self.handoffs_total else 0.0,
            "recovery_effectiveness_rate": round(self.effective_recoveries / self.recoveries_total, 4) if self.recoveries_total else 0.0,
            "interrupted_loop": self.interrupted_loop,
            "interrupt_reason": self.interrupt_reason,
            "cost_usd": round(self.cost_usd, 6),
            "total_tokens": self.total_tokens,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RunAutonomyMetrics":
        return cls(
            request_id=data["request_id"],
            runtime_mode=data.get("runtime_mode", ""),
            provider=data.get("provider", ""),
            model=data.get("model", ""),
            run_status=data.get("run_status", ""),
            success=bool(data.get("success")),
            steps_to_success=int(data.get("steps_to_success") or 0),
            agentic_steps=int(data.get("agentic_steps") or 0),
            task_count=int(data.get("task_count") or 0),
            tasks_succeeded=int(data.get("tasks_succeeded") or 0),
            handoffs_total=int(data.get("handoffs_total") or 0),
            avoidable_handoffs=int(data.get("avoidable_handoffs") or 0),
            recoveries_total=int(data.get("recoveries_total") or 0),
            effective_recoveries=int(data.get("effective_recoveries") or 0),
            interrupted_loop=bool(data.get("interrupted_loop")),
            interrupt_reason=str(data.get("interrupt_reason") or ""),
            cost_usd=float(data.get("cost_usd") or 0.0),
            total_tokens=int(data.get("total_tokens") or 0),
            recorded_at=float(data.get("recorded_at") or time.time()),
        )


def _task_succeeded(task: Dict[str, Any]) -> bool:
    status = (task.get("status") or "").lower()
    if status in {"completed", "succeeded"}:
        return True
    ar = ((task.get("result") or {}).get("action_result") or {})
    return ar.get("status") in {"succeeded", "partial"}


def _is_avoidable_handoff(handoff: Dict[str, Any], state: Dict[str, Any]) -> bool:
    """Heuristic: handoff that likely did not require human input."""
    if handoff.get("tool_call_id"):
        session = state.get("agent_session") or {}
        if session.get("paused_reason") in {"budget", "budget_exceeded"}:
            return True
        return False

    task_id = handoff.get("task_id")
    task = next((t for t in state.get("tasks", []) if t.get("id") == task_id), None)
    if task is None:
        return handoff.get("kind") in {"clarification", "preference"}

    recovery = task.get("recovery") or {}
    if recovery.get("needs_user"):
        return False
    if recovery.get("classification") in {"ask_user", "handoff", "ambiguous_match"}:
        return False
    if task.get("status") in {"failed", "waiting_approval", "blocked"}:
        return False
    if _task_succeeded(task) and handoff.get("kind") not in {"critical", "approval"}:
        return True
    return handoff.get("kind") in {"clarification", "preference"}


def _count_recoveries(state: Dict[str, Any]) -> tuple[int, int]:
    tasks = state.get("tasks") or []
    total = 0
    effective = 0
    for task in tasks:
        recovery = task.get("recovery")
        if not recovery:
            continue
        total += 1
        if _task_succeeded(task):
            effective += 1
            continue
        if recovery.get("classification") in {"retry", "replan", "script_recovery"}:
            if task.get("attempt", 0) > 1 and _task_succeeded(task):
                effective += 1
    recovery_state = state.get("recovery") or {}
    if recovery_state and recovery_state not in tasks:
        total += 1
    return total, effective


def _detect_interrupted_loop(state: Dict[str, Any], agentic_steps: int) -> tuple[bool, str]:
    status = (state.get("status") or "").lower()
    if status in {"cancelled", "cancelling"}:
        return True, "cancelled"
    error = str(state.get("error") or "")
    if "max_steps" in error.lower():
        return True, "max_steps"
    session = state.get("agent_session") or {}
    if session.get("paused_reason") in {"budget", "budget_exceeded"}:
        return True, "budget_exceeded"
    outputs = state.get("outputs") or {}
    agent_out = outputs.get("agent_final_response") or {}
    if agent_out.get("budget_exceeded"):
        return True, "budget_exceeded"
    agentic_error = state.get("_agentic_error") or {}
    if agentic_error:
        return True, "agentic_fallback"
    if agentic_steps > 0 and status == "failed" and "agentic loop" in error.lower():
        return True, "agentic_loop_failed"
    return False, ""


def _extract_cost(state: Dict[str, Any]) -> tuple[float, int]:
    cost = (state.get("outputs") or {}).get("cost") or (state.get("agent_session") or {}).get("cost") or {}
    if isinstance(cost, dict):
        return float(cost.get("total_cost_usd") or 0.0), int(cost.get("total_tokens") or 0)
    return 0.0, 0


def compute_run_metrics(state: Dict[str, Any]) -> RunAutonomyMetrics:
    """Derive autonomy metrics from a persisted or active run state."""
    tasks = state.get("tasks") or []
    tasks_ok = sum(1 for t in tasks if _task_succeeded(t))
    handoffs = state.get("handoffs") or []
    if state.get("pending_handoff"):
        handoffs = list(handoffs) + [state["pending_handoff"]]

    avoidable = sum(1 for h in handoffs if _is_avoidable_handoff(h, state))
    recoveries_total, effective_recoveries = _count_recoveries(state)

    session = state.get("agent_session") or {}
    agentic_steps = int(session.get("step") or 0)
    agent_final = (state.get("outputs") or {}).get("agent_final_response") or {}
    if agent_final.get("steps"):
        agentic_steps = max(agentic_steps, int(agent_final["steps"]))

    run_status = str(state.get("status") or "")
    success = run_status == "completed"
    interrupted, interrupt_reason = _detect_interrupted_loop(state, agentic_steps)

    if success:
        steps_to_success = agentic_steps if agentic_steps else max(tasks_ok, 1)
    else:
        steps_to_success = agentic_steps or len(tasks)

    cost_usd, total_tokens = _extract_cost(state)

    return RunAutonomyMetrics(
        request_id=state.get("request_id") or "",
        runtime_mode=str(state.get("runtime_mode") or ""),
        provider=str(state.get("provider") or ""),
        model=str(state.get("model") or ""),
        run_status=run_status,
        success=success,
        steps_to_success=steps_to_success,
        agentic_steps=agentic_steps,
        task_count=len(tasks),
        tasks_succeeded=tasks_ok,
        handoffs_total=len(handoffs),
        avoidable_handoffs=avoidable,
        recoveries_total=recoveries_total,
        effective_recoveries=effective_recoveries,
        interrupted_loop=interrupted,
        interrupt_reason=interrupt_reason,
        cost_usd=cost_usd,
        total_tokens=total_tokens,
    )


class AutonomyMetricsStore:
    def __init__(self, persistence=None):
        if persistence is None:
            from agent_persistence import Persistence
            persistence = Persistence.get()
        self.persistence = persistence

    async def save_run_metrics(self, metrics: RunAutonomyMetrics) -> None:
        await self.persistence.save_kv(f"{KV_PREFIX}{metrics.request_id}", metrics.to_dict())

    async def get_run_metrics(self, request_id: str) -> Optional[RunAutonomyMetrics]:
        raw = await self.persistence.get_kv(f"{KV_PREFIX}{request_id}")
        if not raw:
            return None
        return RunAutonomyMetrics.from_dict(raw)

    async def list_run_metrics(self, limit: int = 50) -> List[RunAutonomyMetrics]:
        records = await self.persistence.list_kv(KV_PREFIX)
        out = [RunAutonomyMetrics.from_dict(r["value"]) for r in records if isinstance(r.get("value"), dict)]
        out.sort(key=lambda m: -m.recorded_at)
        return out[:limit]

    async def finalize_from_state(self, state: Dict[str, Any]) -> RunAutonomyMetrics:
        metrics = compute_run_metrics(state)
        await self.save_run_metrics(metrics)
        return metrics
