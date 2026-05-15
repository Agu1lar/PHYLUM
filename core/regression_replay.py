# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Regression replay — re-execute persisted runs in dry-run and compare plan, cost, results."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from execution_economics import CostTracker, StepRecord, TokenUsage
from tool_action_confidence import ToolActionConfidenceStore

logger = logging.getLogger(__name__)

READONLY_TOOLS = frozenset({
    "memory", "os", "web", "document_intelligence", "codebase_map",
    "execution_economics", "software_inventory", "share_discovery",
})


@dataclass
class PlanSignature:
    steps: List[Tuple[str, str]]

    def to_dict(self) -> List[Dict[str, str]]:
        return [{"tool": t, "action": a} for t, a in self.steps]

    @classmethod
    def from_tasks(cls, tasks: Sequence[Any]) -> "PlanSignature":
        steps = []
        for task in tasks:
            if hasattr(task, "tool"):
                tool, action = task.tool, task.action
            elif isinstance(task, dict):
                tool, action = task.get("tool"), task.get("action")
            else:
                continue
            if tool and action:
                steps.append((tool, action))
        return cls(steps=steps)


@dataclass
class RunBaseline:
    request_id: str
    runtime_mode: str
    run_status: str
    input_text: str
    plan: PlanSignature
    task_results: Dict[str, Dict[str, Any]]
    cost: Dict[str, Any]
    task_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "runtime_mode": self.runtime_mode,
            "run_status": self.run_status,
            "input_text": self.input_text,
            "plan": self.plan.to_dict(),
            "task_results": self.task_results,
            "cost": self.cost,
            "task_count": self.task_count,
        }


@dataclass
class ReplayDiff:
    category: str
    field: str
    baseline: Any
    replay: Any
    match: bool
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "field": self.field,
            "baseline": self.baseline,
            "replay": self.replay,
            "match": self.match,
            "detail": self.detail,
        }


@dataclass
class RegressionReplayReport:
    request_id: str
    dry_run: bool = True
    baseline: Optional[RunBaseline] = None
    replay_plan: Optional[PlanSignature] = None
    replay_cost: Dict[str, Any] = field(default_factory=dict)
    replay_task_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    diffs: List[ReplayDiff] = field(default_factory=list)
    passed: bool = False
    duration_ms: float = 0.0

    @property
    def plan_match(self) -> bool:
        return all(d.match for d in self.diffs if d.category == "plan")

    @property
    def cost_match(self) -> bool:
        return all(d.match for d in self.diffs if d.category == "cost")

    @property
    def result_match(self) -> bool:
        return all(d.match for d in self.diffs if d.category == "result")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "dry_run": self.dry_run,
            "passed": self.passed,
            "plan_match": self.plan_match,
            "cost_match": self.cost_match,
            "result_match": self.result_match,
            "duration_ms": round(self.duration_ms, 2),
            "baseline": self.baseline.to_dict() if self.baseline else None,
            "replay_plan": self.replay_plan.to_dict() if self.replay_plan else None,
            "replay_cost": self.replay_cost,
            "replay_task_results": self.replay_task_results,
            "diffs": [d.to_dict() for d in self.diffs],
        }


def extract_cost_from_state(state: Dict[str, Any]) -> Dict[str, Any]:
    session = state.get("agent_session") or {}
    if isinstance(session, dict) and session.get("cost"):
        return dict(session["cost"])
    outputs = state.get("outputs") or {}
    if outputs.get("cost"):
        return dict(outputs["cost"])
    details = (outputs.get("agent_final_response") or {}).get("details") or {}
    if isinstance(details, dict) and details.get("cost"):
        return dict(details["cost"])
    for event in reversed(state.get("history") or []):
        payload = (event or {}).get("payload") or {}
        if isinstance(payload, dict) and payload.get("cost"):
            return dict(payload["cost"])
    tasks = state.get("tasks") or []
    return {
        "total_tokens": 0,
        "total_cost_usd": 0.0,
        "tool_steps": len(tasks),
        "estimated_from_tasks": True,
    }


def extract_run_baseline(state: Dict[str, Any]) -> RunBaseline:
    tasks = state.get("tasks") or []
    task_results: Dict[str, Dict[str, Any]] = {}
    for task in tasks:
        tid = task.get("id") or ""
        ar = ((task.get("result") or {}).get("action_result") or {})
        task_results[tid] = {
            "tool": task.get("tool"),
            "action": task.get("action"),
            "status": ar.get("status") or task.get("status"),
            "task_status": task.get("status"),
        }
    inputs = state.get("inputs") or {}
    text = str(inputs.get("text") or inputs.get("prompt") or "")
    return RunBaseline(
        request_id=state.get("request_id") or "",
        runtime_mode=str(state.get("runtime_mode") or ""),
        run_status=str(state.get("status") or ""),
        input_text=text,
        plan=PlanSignature.from_tasks(tasks),
        task_results=task_results,
        cost=extract_cost_from_state(state),
        task_count=len(tasks),
    )


def _plan_diff(baseline: PlanSignature, replay: PlanSignature) -> List[ReplayDiff]:
    diffs: List[ReplayDiff] = []
    base_steps = baseline.steps
    replay_steps = replay.steps
    exact = base_steps == replay_steps
    diffs.append(
        ReplayDiff(
            category="plan",
            field="sequence_exact",
            baseline=baseline.to_dict(),
            replay=replay.to_dict(),
            match=exact,
            detail="exact match" if exact else f"baseline {len(base_steps)} steps vs replay {len(replay_steps)}",
        )
    )
    base_set = set(base_steps)
    replay_set = set(replay_steps)
    diffs.append(
        ReplayDiff(
            category="plan",
            field="tools_coverage",
            baseline=sorted(base_set),
            replay=sorted(replay_set),
            match=base_set == replay_set,
            detail="same tool/action pairs" if base_set == replay_set else "tool/action set differs",
        )
    )
    return diffs


def _cost_diff(baseline_cost: Dict[str, Any], replay_cost: Dict[str, Any], *, tolerance_usd: float = 0.05) -> List[ReplayDiff]:
    diffs: List[ReplayDiff] = []
    base_usd = float(baseline_cost.get("total_cost_usd") or 0.0)
    replay_usd = float(replay_cost.get("total_cost_usd") or 0.0)
    usd_match = abs(base_usd - replay_usd) <= tolerance_usd or (base_usd == 0 and replay_usd == 0)
    diffs.append(
        ReplayDiff(
            category="cost",
            field="total_cost_usd",
            baseline=round(base_usd, 6),
            replay=round(replay_usd, 6),
            match=usd_match,
            detail=f"delta ${abs(base_usd - replay_usd):.4f}",
        )
    )
    base_tokens = int(baseline_cost.get("total_tokens") or 0)
    replay_tokens = int(replay_cost.get("total_tokens") or 0)
    token_match = base_tokens == replay_tokens or (base_tokens == 0 and replay_tokens == 0)
    diffs.append(
        ReplayDiff(
            category="cost",
            field="total_tokens",
            baseline=base_tokens,
            replay=replay_tokens,
            match=token_match,
        )
    )
    base_steps = int(baseline_cost.get("tool_steps") or baseline_cost.get("tool_step_count") or 0)
    replay_steps = int(replay_cost.get("tool_steps") or replay_cost.get("tool_step_count") or 0)
    diffs.append(
        ReplayDiff(
            category="cost",
            field="tool_step_count",
            baseline=base_steps,
            replay=replay_steps,
            match=base_steps == replay_steps,
        )
    )
    return diffs


def _result_diff(
    baseline_results: Dict[str, Dict[str, Any]],
    replay_results: Dict[str, Dict[str, Any]],
) -> List[ReplayDiff]:
    diffs: List[ReplayDiff] = []
    all_ids = sorted(set(baseline_results) | set(replay_results))
    matches = 0
    for tid in all_ids:
        base = baseline_results.get(tid) or {}
        rep = replay_results.get(tid) or {}
        base_status = _normalize_result_status(base.get("status") or base.get("task_status"))
        rep_status = _normalize_result_status(rep.get("status") or rep.get("dry_run_status"))
        ok = _statuses_align(base_status, rep_status)
        if ok:
            matches += 1
        diffs.append(
            ReplayDiff(
                category="result",
                field=f"task:{tid}",
                baseline=base_status,
                replay=rep_status,
                match=ok,
                detail=rep.get("detail", ""),
            )
        )
    overall = matches == len(all_ids) if all_ids else True
    diffs.insert(
        0,
        ReplayDiff(
            category="result",
            field="aggregate",
            baseline=matches,
            replay=len(all_ids),
            match=overall,
            detail=f"{matches}/{len(all_ids)} tasks aligned",
        ),
    )
    return diffs


SUCCESS_EQUIV = frozenset({"succeeded", "completed", "validated", "dry_run_ok"})
DRY_RUN_SUCCESS = frozenset({"validated", "validated_readonly", "dry_run_ok", "skipped_validation"})
DRY_RUN_FAILURE = frozenset({"validation_failed", "unsupported_tool"})


def _statuses_align(base_status: str, replay_status: str) -> bool:
    if base_status == replay_status:
        return True
    if base_status in SUCCESS_EQUIV and replay_status in DRY_RUN_SUCCESS:
        return True
    if base_status in {"failed", "rejected", "blocked"} and replay_status in DRY_RUN_FAILURE:
        return True
    return False


def _normalize_result_status(status: Optional[str]) -> str:
    if not status:
        return "unknown"
    return str(status).strip().lower()


class RegressionReplayEngine:
    def __init__(
        self,
        *,
        persistence=None,
        confidence_store: Optional[ToolActionConfidenceStore] = None,
        cost_tolerance_usd: float = 0.05,
    ):
        if persistence is None:
            from agent_persistence import Persistence
            persistence = Persistence.get()
        self.persistence = persistence
        self.confidence_store = confidence_store or ToolActionConfidenceStore(persistence)
        self.cost_tolerance_usd = cost_tolerance_usd

    async def load_run_state(self, request_id: str) -> Optional[Dict[str, Any]]:
        state = await self.persistence.get_kv(f"state:{request_id}")
        return state if isinstance(state, dict) else None

    async def list_replayable_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        states = await self.persistence.list_states()
        out = []
        for item in states[:limit]:
            value = item.get("value") or item
            if isinstance(value, dict) and value.get("request_id"):
                out.append({
                    "request_id": value["request_id"],
                    "status": value.get("status"),
                    "runtime_mode": value.get("runtime_mode"),
                    "task_count": len(value.get("tasks") or []),
                    "updated_at": item.get("updated_at"),
                })
        return out

    async def replay(
        self,
        request_id: str,
        *,
        replan: bool = True,
        validate_tasks: bool = True,
    ) -> RegressionReplayReport:
        started = time.perf_counter()
        state = await self.load_run_state(request_id)
        if state is None:
            report = RegressionReplayReport(request_id=request_id)
            report.diffs.append(
                ReplayDiff(
                    category="run",
                    field="exists",
                    baseline=request_id,
                    replay=None,
                    match=False,
                    detail="run state not found",
                )
            )
            report.duration_ms = (time.perf_counter() - started) * 1000
            return report

        baseline = extract_run_baseline(state)
        report = RegressionReplayReport(request_id=request_id, baseline=baseline)

        replay_plan = baseline.plan
        if replan and baseline.input_text.strip():
            replay_plan = await self._replan(baseline.input_text)

        report.replay_plan = replay_plan
        report.diffs.extend(_plan_diff(baseline.plan, replay_plan))

        replay_results: Dict[str, Dict[str, Any]] = {}
        tracker = CostTracker(f"replay-{request_id}", model=state.get("model") or "")
        tasks = state.get("tasks") or []

        for index, task in enumerate(tasks):
            tid = task.get("id") or f"task-{index}"
            replay_entry = await self._dry_run_task(task, validate=validate_tasks)
            replay_results[tid] = replay_entry
            conf = await self.confidence_store.get_confidence(
                task.get("tool") or "unknown",
                task.get("action") or "",
            )
            tracker.record_step(
                step_index=index,
                tool=task.get("tool") or "unknown",
                action=task.get("action") or "",
                status=replay_entry.get("dry_run_status") or "dry_run",
                tokens=TokenUsage(),
                duration_ms=int(conf.stats.avg_duration_ms),
            )

        report.replay_task_results = replay_results
        summary = tracker.summary().to_dict()
        summary["tool_steps"] = len(tasks)
        summary["dry_run"] = True
        report.replay_cost = summary
        report.diffs.extend(_cost_diff(baseline.cost, summary, tolerance_usd=self.cost_tolerance_usd))
        report.diffs.extend(_result_diff(baseline.task_results, replay_results))

        report.passed = all(d.match for d in report.diffs)
        report.duration_ms = (time.perf_counter() - started) * 1000
        return report

    async def _replan(self, text: str) -> PlanSignature:
        from planner_agent import PlannerAgent

        plan, validation = await PlannerAgent().parse(text)
        if not validation.ok:
            return PlanSignature(steps=[])
        return PlanSignature.from_tasks(plan.tasks)

    async def _dry_run_task(self, task: Dict[str, Any], *, validate: bool) -> Dict[str, Any]:
        tool = task.get("tool") or ""
        action = task.get("action") or ""
        conf = await self.confidence_store.get_confidence(tool, action)
        entry: Dict[str, Any] = {
            "tool": tool,
            "action": action,
            "confidence": conf.confidence,
            "sample_size": conf.sample_size,
        }
        if not validate:
            entry["dry_run_status"] = "skipped_validation"
            return entry

        if tool in READONLY_TOOLS:
            entry["dry_run_status"] = "validated_readonly"
            entry["detail"] = "readonly tool — dry-run assumes inspection path"
            return entry

        try:
            from tool_registry import ToolRegistry

            registry = ToolRegistry()
            if not registry.supports(tool):
                entry["dry_run_status"] = "unsupported_tool"
                entry["detail"] = f"tool {tool!r} not in registry"
                return entry
            payload = registry.build_payload({**task, "id": task.get("id") or "replay"})
            tool_impl = registry.tools[tool]
            model = tool_impl.InputModel(**payload)
            await tool_impl.validate(model)
            entry["dry_run_status"] = "validated"
            entry["detail"] = "payload validation passed (no mutation executed)"
        except Exception as exc:
            entry["dry_run_status"] = "validation_failed"
            entry["detail"] = str(exc)
        return entry
