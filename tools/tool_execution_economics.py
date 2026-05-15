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
                "rank_strategies|compare_routes|best_route|"
                "record_tool_outcome|get_tool_confidence|list_tool_confidences|"
                "plan_tool_confidence|replay_regression|list_replayable_runs|"
                "get_autonomy_metrics|list_autonomy_metrics|get_quality_dashboard|"
                "list_quality_versions)$",
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
    # tool confidence / regression replay
    request_id: Optional[str] = None
    replan: bool = True
    validate_tasks: bool = True
    min_samples: Optional[int] = None
    limit: Optional[int] = None
    plan_steps: Optional[List[Dict[str, str]]] = None
    runtime_mode: Optional[str] = None
    provider: Optional[str] = None


class ExecutionEconomicsTool(BaseTool):
    InputModel = ExecutionEconomicsRequest
    OutputModel = ActionResult

    def __init__(self):
        super().__init__(default_timeout=30, default_retries=1)
        self._trackers: Dict[str, CostTracker] = {}
        self._complexity = PathComplexityAnalyzer()
        self._stopping = StoppingHeuristics()
        self._optimizer = RouteOptimizer()
        self._confidence_store = None
        self._replay_engine = None
        self._autonomy_store = None
        self._quality_dashboard = None

    def _confidence(self):
        if self._confidence_store is None:
            from tool_action_confidence import ToolActionConfidenceStore
            self._confidence_store = ToolActionConfidenceStore()
        return self._confidence_store

    def _replay(self):
        if self._replay_engine is None:
            from regression_replay import RegressionReplayEngine
            self._replay_engine = RegressionReplayEngine(confidence_store=self._confidence())
        return self._replay_engine

    def _autonomy(self):
        if self._autonomy_store is None:
            from autonomy_metrics import AutonomyMetricsStore
            self._autonomy_store = AutonomyMetricsStore()
        return self._autonomy_store

    def _quality(self):
        if self._quality_dashboard is None:
            from quality_dashboard import QualityDashboard
            self._quality_dashboard = QualityDashboard()
        return self._quality_dashboard

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

            if action == "record_tool_outcome":
                if not payload.tool or not payload.tool_action or not payload.status:
                    return ActionResult(
                        status="failed",
                        summary="'tool', 'tool_action' and 'status' are required",
                        tool="execution_economics",
                        action=action,
                    )
                conf = await self._confidence().record_outcome(
                    payload.tool,
                    payload.tool_action,
                    payload.status,
                    duration_ms=payload.duration_ms,
                )
                return ActionResult(
                    status="succeeded",
                    summary=f"Recorded outcome for {payload.tool}.{payload.tool_action}",
                    tool="execution_economics",
                    action=action,
                    data=conf.to_dict(),
                )

            if action == "get_tool_confidence":
                if not payload.tool or not payload.tool_action:
                    return ActionResult(
                        status="failed",
                        summary="'tool' and 'tool_action' are required",
                        tool="execution_economics",
                        action=action,
                    )
                conf = await self._confidence().get_confidence(payload.tool, payload.tool_action)
                return ActionResult(
                    status="succeeded",
                    summary=f"Confidence {conf.confidence:.2f} for {payload.tool}.{payload.tool_action} ({conf.sample_size} samples)",
                    tool="execution_economics",
                    action=action,
                    data=conf.to_dict(),
                )

            if action == "list_tool_confidences":
                items = await self._confidence().list_confidences(
                    tool=payload.tool,
                    min_samples=int(payload.min_samples or 0),
                    limit=int(payload.limit or 200),
                )
                return ActionResult(
                    status="succeeded",
                    summary=f"Listed {len(items)} tool/action confidence scores",
                    tool="execution_economics",
                    action=action,
                    data={"items": [c.to_dict() for c in items]},
                )

            if action == "plan_tool_confidence":
                steps = payload.plan_steps or []
                pairs = [(s.get("tool", ""), s.get("action", "")) for s in steps if s.get("tool")]
                summary = await self._confidence().plan_confidence(pairs)
                return ActionResult(
                    status="succeeded",
                    summary=f"Plan confidence avg={summary['average']:.2f} min={summary['minimum']:.2f}",
                    tool="execution_economics",
                    action=action,
                    data=summary,
                )

            if action == "list_replayable_runs":
                runs = await self._replay().list_replayable_runs(limit=int(payload.limit or 50))
                return ActionResult(
                    status="succeeded",
                    summary=f"Found {len(runs)} replayable run(s)",
                    tool="execution_economics",
                    action=action,
                    data={"runs": runs},
                )

            if action == "replay_regression":
                if not payload.request_id:
                    return ActionResult(
                        status="failed",
                        summary="'request_id' is required",
                        tool="execution_economics",
                        action=action,
                    )
                report = await self._replay().replay(
                    payload.request_id,
                    replan=bool(payload.replan),
                    validate_tasks=bool(payload.validate_tasks),
                )
                return ActionResult(
                    status="succeeded" if report.passed else "partial",
                    summary=(
                        f"Regression replay {'passed' if report.passed else 'found drift'} "
                        f"for {payload.request_id}"
                    ),
                    tool="execution_economics",
                    action=action,
                    data=report.to_dict(),
                )

            if action == "get_autonomy_metrics":
                rid = payload.request_id or payload.run_id
                if not rid:
                    return ActionResult(
                        status="failed",
                        summary="'request_id' is required",
                        tool="execution_economics",
                        action=action,
                    )
                metrics = await self._autonomy().get_run_metrics(rid)
                if not metrics:
                    return ActionResult(
                        status="failed",
                        summary=f"No autonomy metrics for {rid}",
                        tool="execution_economics",
                        action=action,
                    )
                return ActionResult(
                    status="succeeded",
                    summary=f"Autonomy metrics for {rid}: {metrics.steps_to_success} steps, {metrics.handoffs_total} handoffs",
                    tool="execution_economics",
                    action=action,
                    data=metrics.to_dict(),
                )

            if action == "list_autonomy_metrics":
                items = await self._autonomy().list_run_metrics(limit=int(payload.limit or 50))
                return ActionResult(
                    status="succeeded",
                    summary=f"Listed {len(items)} run autonomy metric record(s)",
                    tool="execution_economics",
                    action=action,
                    data={"items": [m.to_dict() for m in items]},
                )

            if action == "get_quality_dashboard":
                summary = await self._quality().dashboard_summary(
                    runtime_mode=payload.runtime_mode,
                    provider=payload.provider,
                    model=payload.model,
                )
                return ActionResult(
                    status="succeeded",
                    summary=f"Quality dashboard: {summary.get('version_count', 0)} version bucket(s)",
                    tool="execution_economics",
                    action=action,
                    data=summary,
                )

            if action == "list_quality_versions":
                versions = await self._quality().list_versions(limit=int(payload.limit or 100))
                if payload.runtime_mode:
                    versions = [v for v in versions if v.runtime_mode == payload.runtime_mode]
                if payload.provider:
                    versions = [v for v in versions if v.provider == payload.provider]
                if payload.model:
                    versions = [v for v in versions if v.model == payload.model]
                return ActionResult(
                    status="succeeded",
                    summary=f"Listed {len(versions)} quality version aggregate(s)",
                    tool="execution_economics",
                    action=action,
                    data={"versions": [v.to_dict() for v in versions]},
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
