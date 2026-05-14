# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tool facade for execution economics."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from action_models import ActionResult
from execution_economics import (
    CostTracker,
    PathComplexityAnalyzer,
    RouteOptimizer,
    StoppingHeuristics,
    TokenUsage,
)
from tool_base import BaseTool

logger = logging.getLogger(__name__)


class ExecutionEconomicsRequest(BaseModel):
    action: str = Field(
        ...,
        pattern="^(create_tracker|record_step|record_llm_usage|get_summary|"
                "check_budget|analyze_complexity|evaluate_stopping|"
                "rank_strategies|compare_routes|best_route)$",
    )
    run_id: Optional[str] = None
    model: Optional[str] = None
    budget_usd: Optional[float] = None
    budget_tokens: Optional[int] = None
    # record_step fields
    step_index: Optional[int] = None
    tool: Optional[str] = None
    tool_action: Optional[str] = None
    status: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    duration_ms: Optional[int] = None
    is_retry: bool = False
    is_replan: bool = False
    error: Optional[str] = None
    # stopping heuristic fields
    current_confidence: Optional[float] = None
    goal_progress: Optional[float] = None
    consecutive_errors: Optional[int] = None
    # strategy ranking
    strategies: Optional[List[Dict[str, Any]]] = None
    # compare routes
    route_a: Optional[Dict[str, Any]] = None
    route_b: Optional[Dict[str, Any]] = None
    # complexity analysis
    steps: Optional[List[Dict[str, Any]]] = None
    branch_count: Optional[int] = None


class ExecutionEconomicsTool(BaseTool):
    InputModel = ExecutionEconomicsRequest

    def __init__(self):
        super().__init__(default_timeout=30, default_retries=1)
        self._trackers: Dict[str, CostTracker] = {}
        self._complexity = PathComplexityAnalyzer()
        self._stopping = StoppingHeuristics()
        self._optimizer = RouteOptimizer()

    def _get_tracker(self, run_id: str) -> Optional[CostTracker]:
        return self._trackers.get(run_id)

    async def _run(self, payload: ExecutionEconomicsRequest) -> ActionResult:
        action = payload.action

        try:
            if action == "create_tracker":
                if not payload.run_id:
                    return ActionResult(
                        status="failed", summary="'run_id' is required",
                        tool="execution_economics", action=action,
                    )
                tracker = CostTracker(
                    payload.run_id,
                    model=payload.model or "",
                    budget_usd=payload.budget_usd,
                    budget_tokens=payload.budget_tokens,
                )
                self._trackers[payload.run_id] = tracker
                return ActionResult(
                    status="succeeded",
                    summary=f"Created cost tracker for run '{payload.run_id}'",
                    tool="execution_economics", action=action,
                    data={"run_id": payload.run_id, "model": payload.model or ""},
                )

            if action == "record_step":
                if not payload.run_id:
                    return ActionResult(
                        status="failed", summary="'run_id' is required",
                        tool="execution_economics", action=action,
                    )
                tracker = self._get_tracker(payload.run_id)
                if not tracker:
                    return ActionResult(
                        status="failed",
                        summary=f"No tracker found for run '{payload.run_id}'",
                        tool="execution_economics", action=action,
                    )
                tokens = TokenUsage(
                    prompt_tokens=payload.prompt_tokens or 0,
                    completion_tokens=payload.completion_tokens or 0,
                )
                record = tracker.record_step(
                    step_index=payload.step_index or len(tracker._steps),
                    tool=payload.tool or "unknown",
                    action=payload.tool_action or "",
                    status=payload.status or "succeeded",
                    tokens=tokens,
                    duration_ms=payload.duration_ms or 0,
                    model=payload.model,
                    is_retry=payload.is_retry,
                    is_replan=payload.is_replan,
                    error=payload.error or "",
                )
                return ActionResult(
                    status="succeeded",
                    summary=f"Recorded step {record.step_index}: {record.tool}:{record.action} "
                            f"({record.tokens.total_tokens} tokens, ${record.cost_usd:.4f})",
                    tool="execution_economics", action=action,
                    data=record.to_dict(),
                )

            if action == "record_llm_usage":
                if not payload.run_id:
                    return ActionResult(
                        status="failed", summary="'run_id' is required",
                        tool="execution_economics", action=action,
                    )
                tracker = self._get_tracker(payload.run_id)
                if not tracker:
                    return ActionResult(
                        status="failed",
                        summary=f"No tracker found for run '{payload.run_id}'",
                        tool="execution_economics", action=action,
                    )
                usage = tracker.record_llm_usage(
                    prompt_tokens=payload.prompt_tokens or 0,
                    completion_tokens=payload.completion_tokens or 0,
                    model=payload.model,
                )
                return ActionResult(
                    status="succeeded",
                    summary=f"Recorded LLM usage: {usage.total_tokens} tokens",
                    tool="execution_economics", action=action,
                    data=usage.to_dict(),
                )

            if action == "get_summary":
                if not payload.run_id:
                    return ActionResult(
                        status="failed", summary="'run_id' is required",
                        tool="execution_economics", action=action,
                    )
                tracker = self._get_tracker(payload.run_id)
                if not tracker:
                    return ActionResult(
                        status="failed",
                        summary=f"No tracker found for run '{payload.run_id}'",
                        tool="execution_economics", action=action,
                    )
                summary = tracker.summary()
                return ActionResult(
                    status="succeeded",
                    summary=f"Run '{payload.run_id}': {summary.total_tokens} tokens, "
                            f"${summary.total_cost_usd:.4f}, {summary.total_steps} steps, "
                            f"{summary.wall_time_ms}ms",
                    tool="execution_economics", action=action,
                    data=summary.to_dict(),
                )

            if action == "check_budget":
                if not payload.run_id:
                    return ActionResult(
                        status="failed", summary="'run_id' is required",
                        tool="execution_economics", action=action,
                    )
                tracker = self._get_tracker(payload.run_id)
                if not tracker:
                    return ActionResult(
                        status="failed",
                        summary=f"No tracker found for run '{payload.run_id}'",
                        tool="execution_economics", action=action,
                    )
                return ActionResult(
                    status="succeeded",
                    summary=f"Budget: {tracker.budget_consumed_pct:.0%} consumed, "
                            f"over_budget={tracker.over_budget}",
                    tool="execution_economics", action=action,
                    data={
                        "budget_consumed_pct": round(tracker.budget_consumed_pct, 4),
                        "over_budget": tracker.over_budget,
                        "total_cost_usd": round(tracker.total_cost_usd, 6),
                        "budget_usd": tracker.budget_usd,
                        "total_tokens": tracker.total_tokens,
                        "budget_tokens": tracker.budget_tokens,
                    },
                )

            if action == "analyze_complexity":
                from execution_economics import StepRecord as SR
                raw_steps = payload.steps or []
                records = [
                    SR(
                        step_index=i,
                        tool=s.get("tool", "unknown"),
                        action=s.get("action", ""),
                        status=s.get("status", "succeeded"),
                        is_retry=bool(s.get("is_retry")),
                        is_replan=bool(s.get("is_replan")),
                        error=s.get("error", ""),
                    )
                    for i, s in enumerate(raw_steps)
                ]
                score = self._complexity.analyze(records, branch_count=payload.branch_count or 1)
                return ActionResult(
                    status="succeeded",
                    summary=f"Path complexity: {score.score:.2f} "
                            f"({score.unique_tools} tools, {score.total_steps} steps, "
                            f"{score.error_rate:.0%} error rate)",
                    tool="execution_economics", action=action,
                    data=score.to_dict(),
                )

            if action == "evaluate_stopping":
                if not payload.run_id:
                    return ActionResult(
                        status="failed", summary="'run_id' is required",
                        tool="execution_economics", action=action,
                    )
                tracker = self._get_tracker(payload.run_id)
                if not tracker:
                    return ActionResult(
                        status="failed",
                        summary=f"No tracker found for run '{payload.run_id}'",
                        tool="execution_economics", action=action,
                    )
                decision = self._stopping.evaluate(
                    tracker,
                    current_confidence=payload.current_confidence or 0.5,
                    goal_progress=payload.goal_progress or 0.0,
                    consecutive_errors=payload.consecutive_errors or 0,
                )
                return ActionResult(
                    status="succeeded",
                    summary=f"Stopping decision: {decision.action} — {decision.reason}",
                    tool="execution_economics", action=action,
                    data=decision.to_dict(),
                )

            if action == "rank_strategies":
                if not payload.strategies:
                    return ActionResult(
                        status="failed", summary="'strategies' list is required",
                        tool="execution_economics", action=action,
                    )
                ranked = self._optimizer.rank_strategies(payload.strategies)
                return ActionResult(
                    status="succeeded",
                    summary=f"Ranked {len(ranked)} strategies by efficiency",
                    tool="execution_economics", action=action,
                    data={"ranked": [r.to_dict() for r in ranked]},
                )

            if action == "compare_routes":
                if not payload.route_a or not payload.route_b:
                    return ActionResult(
                        status="failed", summary="'route_a' and 'route_b' are required",
                        tool="execution_economics", action=action,
                    )
                comparison = self._optimizer.compare_routes(payload.route_a, payload.route_b)
                return ActionResult(
                    status="succeeded",
                    summary=f"Route comparison: {comparison['better']} is more efficient "
                            f"(delta={comparison['delta']:.4f})",
                    tool="execution_economics", action=action,
                    data=comparison,
                )

            if action == "best_route":
                if not payload.strategies:
                    return ActionResult(
                        status="failed", summary="'strategies' list is required",
                        tool="execution_economics", action=action,
                    )
                best = self._optimizer.best_route(payload.strategies)
                if not best:
                    return ActionResult(
                        status="succeeded", summary="No route candidates",
                        tool="execution_economics", action=action,
                        data={},
                    )
                return ActionResult(
                    status="succeeded",
                    summary=f"Best route: {best.strategy_id} "
                            f"(efficiency={best.efficiency_score:.4f})",
                    tool="execution_economics", action=action,
                    data=best.to_dict(),
                )

            return ActionResult(
                status="failed", summary=f"Unknown action: {action}",
                tool="execution_economics", action=action,
            )

        except Exception as exc:
            return ActionResult(
                status="failed", summary=str(exc),
                tool="execution_economics", action=action,
            )
