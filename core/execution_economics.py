# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Execution economics — cost tracking, path complexity, stopping heuristics and route optimization.

Provides four interrelated components:

1. **CostTracker**: accumulates per-run costs (tokens, wall-clock time, tool invocations)
   and exposes real-time cost estimates using configurable per-model pricing.

2. **PathComplexityAnalyzer**: scores the complexity of an execution path by counting
   unique tools, total steps, retries, replans, error rate, and branching factor.

3. **StoppingHeuristics**: decides whether the agent should keep exploring, ask for
   help, or stop — based on budget consumption, diminishing returns, error streaks,
   and confidence trends.

4. **RouteOptimizer**: given historical strategy data and live path economics, selects
   the most cost-efficient route for a goal type.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token pricing (USD per 1M tokens) — configurable at runtime
# ---------------------------------------------------------------------------

DEFAULT_PRICING: Dict[str, Dict[str, float]] = {
    # OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    "o3": {"input": 2.00, "output": 8.00},
    "o3-mini": {"input": 1.10, "output": 4.40},
    "o4-mini": {"input": 1.10, "output": 4.40},
    # Anthropic
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
    "claude-opus-4-7": {"input": 15.00, "output": 75.00},
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-5-haiku": {"input": 0.80, "output": 4.00},
    # Google
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
}

FALLBACK_PRICING = {"input": 3.00, "output": 15.00}


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> Dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class StepRecord:
    step_index: int
    tool: str
    action: str
    status: str  # succeeded, failed, retried, skipped
    tokens: TokenUsage = field(default_factory=TokenUsage)
    duration_ms: int = 0
    cost_usd: float = 0.0
    is_retry: bool = False
    is_replan: bool = False
    error: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_index": self.step_index,
            "tool": self.tool, "action": self.action,
            "status": self.status,
            "tokens": self.tokens.to_dict(),
            "duration_ms": self.duration_ms,
            "cost_usd": round(self.cost_usd, 6),
            "is_retry": self.is_retry,
            "is_replan": self.is_replan,
            "error": self.error,
            "timestamp": self.timestamp,
        }


@dataclass
class RunCostSummary:
    run_id: str
    model: str = ""
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_cost_usd: float = 0.0
    total_steps: int = 0
    total_tool_calls: int = 0
    total_retries: int = 0
    total_replans: int = 0
    total_errors: int = 0
    wall_time_ms: int = 0
    unique_tools: int = 0
    steps: List[StepRecord] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id, "model": self.model,
            "total_tokens": self.total_tokens,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "total_steps": self.total_steps,
            "total_tool_calls": self.total_tool_calls,
            "total_retries": self.total_retries,
            "total_replans": self.total_replans,
            "total_errors": self.total_errors,
            "wall_time_ms": self.wall_time_ms,
            "unique_tools": self.unique_tools,
            "steps": [s.to_dict() for s in self.steps],
        }


@dataclass
class PathComplexityScore:
    unique_tools: int = 0
    total_steps: int = 0
    retry_count: int = 0
    replan_count: int = 0
    error_rate: float = 0.0
    branching_factor: float = 1.0
    depth: int = 0
    score: float = 0.0  # lower is simpler

    def to_dict(self) -> Dict[str, Any]:
        return {
            "unique_tools": self.unique_tools,
            "total_steps": self.total_steps,
            "retry_count": self.retry_count,
            "replan_count": self.replan_count,
            "error_rate": round(self.error_rate, 4),
            "branching_factor": round(self.branching_factor, 2),
            "depth": self.depth,
            "score": round(self.score, 4),
        }


@dataclass
class StoppingDecision:
    action: str  # continue, ask_user, stop
    reason: str
    confidence: float = 0.0
    budget_consumed_pct: float = 0.0
    signals: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "confidence": round(self.confidence, 4),
            "budget_consumed_pct": round(self.budget_consumed_pct, 4),
            "signals": self.signals,
        }


@dataclass
class RouteCandidate:
    strategy_id: str
    goal_type: str
    avg_cost_usd: float = 0.0
    avg_duration_ms: int = 0
    avg_steps: int = 0
    success_rate: float = 0.0
    confidence: float = 0.0
    efficiency_score: float = 0.0  # higher is better

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "goal_type": self.goal_type,
            "avg_cost_usd": round(self.avg_cost_usd, 6),
            "avg_duration_ms": self.avg_duration_ms,
            "avg_steps": self.avg_steps,
            "success_rate": round(self.success_rate, 4),
            "confidence": round(self.confidence, 4),
            "efficiency_score": round(self.efficiency_score, 4),
        }


# ---------------------------------------------------------------------------
# CostTracker
# ---------------------------------------------------------------------------

class CostTracker:
    """Accumulates token usage, time and cost for a single run."""

    def __init__(
        self,
        run_id: str,
        *,
        model: str = "",
        pricing: Optional[Dict[str, Dict[str, float]]] = None,
        budget_usd: Optional[float] = None,
        budget_tokens: Optional[int] = None,
    ):
        self.run_id = run_id
        self.model = model
        self._pricing = pricing or dict(DEFAULT_PRICING)
        self.budget_usd = budget_usd
        self.budget_tokens = budget_tokens
        self._steps: List[StepRecord] = []
        self._start_time = time.time()
        self._tools_used: set = set()

    def _model_pricing(self, model: Optional[str] = None) -> Dict[str, float]:
        m = model or self.model
        if m in self._pricing:
            return self._pricing[m]
        for key, prices in self._pricing.items():
            if m.startswith(key):
                return prices
        return FALLBACK_PRICING

    def estimate_cost(self, tokens: TokenUsage, model: Optional[str] = None) -> float:
        prices = self._model_pricing(model)
        input_cost = (tokens.prompt_tokens / 1_000_000) * prices["input"]
        output_cost = (tokens.completion_tokens / 1_000_000) * prices["output"]
        return input_cost + output_cost

    def record_step(
        self,
        *,
        step_index: int,
        tool: str,
        action: str,
        status: str,
        tokens: Optional[TokenUsage] = None,
        duration_ms: int = 0,
        model: Optional[str] = None,
        is_retry: bool = False,
        is_replan: bool = False,
        error: str = "",
    ) -> StepRecord:
        usage = tokens or TokenUsage()
        cost = self.estimate_cost(usage, model)
        record = StepRecord(
            step_index=step_index,
            tool=tool, action=action, status=status,
            tokens=usage, duration_ms=duration_ms,
            cost_usd=cost,
            is_retry=is_retry, is_replan=is_replan,
            error=error,
        )
        self._steps.append(record)
        self._tools_used.add(tool)
        return record

    def record_llm_usage(
        self,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cache_creation_tokens: int = 0,
        cache_read_tokens: int = 0,
        model: Optional[str] = None,
    ) -> TokenUsage:
        """Record LLM token usage that isn't tied to a specific tool step (e.g. planning)."""
        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cache_creation_tokens=cache_creation_tokens,
            cache_read_tokens=cache_read_tokens,
        )
        cost = self.estimate_cost(usage, model)
        record = StepRecord(
            step_index=len(self._steps),
            tool="_llm", action="complete", status="succeeded",
            tokens=usage, cost_usd=cost,
        )
        self._steps.append(record)
        return usage

    @property
    def total_tokens(self) -> int:
        return sum(s.tokens.total_tokens for s in self._steps)

    @property
    def total_prompt_tokens(self) -> int:
        return sum(s.tokens.prompt_tokens for s in self._steps)

    @property
    def total_completion_tokens(self) -> int:
        return sum(s.tokens.completion_tokens for s in self._steps)

    @property
    def total_cost_usd(self) -> float:
        return sum(s.cost_usd for s in self._steps)

    @property
    def total_steps(self) -> int:
        return len([s for s in self._steps if s.tool != "_llm"])

    @property
    def total_retries(self) -> int:
        return sum(1 for s in self._steps if s.is_retry)

    @property
    def total_replans(self) -> int:
        return sum(1 for s in self._steps if s.is_replan)

    @property
    def total_errors(self) -> int:
        return sum(1 for s in self._steps if s.status == "failed")

    @property
    def wall_time_ms(self) -> int:
        return int((time.time() - self._start_time) * 1000)

    @property
    def budget_consumed_pct(self) -> float:
        if self.budget_usd and self.budget_usd > 0:
            return min(self.total_cost_usd / self.budget_usd, 1.0)
        if self.budget_tokens and self.budget_tokens > 0:
            return min(self.total_tokens / self.budget_tokens, 1.0)
        return 0.0

    @property
    def over_budget(self) -> bool:
        if self.budget_usd and self.total_cost_usd >= self.budget_usd:
            return True
        if self.budget_tokens and self.total_tokens >= self.budget_tokens:
            return True
        return False

    def summary(self) -> RunCostSummary:
        return RunCostSummary(
            run_id=self.run_id,
            model=self.model,
            total_tokens=self.total_tokens,
            prompt_tokens=self.total_prompt_tokens,
            completion_tokens=self.total_completion_tokens,
            total_cost_usd=self.total_cost_usd,
            total_steps=self.total_steps,
            total_tool_calls=self.total_steps,
            total_retries=self.total_retries,
            total_replans=self.total_replans,
            total_errors=self.total_errors,
            wall_time_ms=self.wall_time_ms,
            unique_tools=len(self._tools_used - {"_llm"}),
            steps=list(self._steps),
        )

    def to_dict(self) -> Dict[str, Any]:
        return self.summary().to_dict()


# ---------------------------------------------------------------------------
# PathComplexityAnalyzer
# ---------------------------------------------------------------------------

class PathComplexityAnalyzer:
    """Scores execution path complexity from step records or strategy history."""

    TOOL_WEIGHT = 1.5
    STEP_WEIGHT = 1.0
    RETRY_PENALTY = 2.0
    REPLAN_PENALTY = 3.0
    ERROR_PENALTY = 2.5
    BRANCH_WEIGHT = 1.2

    def analyze(
        self,
        steps: List[StepRecord],
        *,
        branch_count: int = 1,
    ) -> PathComplexityScore:
        if not steps:
            return PathComplexityScore()

        tool_steps = [s for s in steps if s.tool != "_llm"]
        unique_tools = len({s.tool for s in tool_steps})
        total_steps = len(tool_steps)
        retry_count = sum(1 for s in tool_steps if s.is_retry)
        replan_count = sum(1 for s in tool_steps if s.is_replan)
        error_count = sum(1 for s in tool_steps if s.status == "failed")
        error_rate = error_count / max(total_steps, 1)

        depth = self._compute_depth(tool_steps)

        score = (
            unique_tools * self.TOOL_WEIGHT
            + total_steps * self.STEP_WEIGHT
            + retry_count * self.RETRY_PENALTY
            + replan_count * self.REPLAN_PENALTY
            + error_rate * self.ERROR_PENALTY * total_steps
            + math.log2(max(branch_count, 1)) * self.BRANCH_WEIGHT
        )

        return PathComplexityScore(
            unique_tools=unique_tools,
            total_steps=total_steps,
            retry_count=retry_count,
            replan_count=replan_count,
            error_rate=error_rate,
            branching_factor=branch_count,
            depth=depth,
            score=score,
        )

    def analyze_from_strategy(self, strategy: Dict[str, Any]) -> PathComplexityScore:
        """Analyze complexity from a StrategyRecord dict."""
        steps_data = strategy.get("steps", [])
        records = []
        for i, step in enumerate(steps_data):
            records.append(StepRecord(
                step_index=i,
                tool=step.get("tool", "unknown"),
                action=step.get("action", ""),
                status=step.get("status", step.get("outcome", "succeeded")),
                is_retry=bool(step.get("is_retry")),
                is_replan=bool(step.get("is_replan")),
                error=step.get("error", ""),
            ))
        return self.analyze(records)

    @staticmethod
    def _compute_depth(steps: List[StepRecord]) -> int:
        """Heuristic depth based on tool transitions."""
        if not steps:
            return 0
        depth = 1
        for i in range(1, len(steps)):
            if steps[i].tool != steps[i - 1].tool:
                depth += 1
        return depth

    def compare(
        self,
        path_a: PathComplexityScore,
        path_b: PathComplexityScore,
    ) -> Dict[str, Any]:
        """Compare two paths. Returns which is simpler."""
        return {
            "simpler": "a" if path_a.score <= path_b.score else "b",
            "score_a": path_a.score,
            "score_b": path_b.score,
            "delta": abs(path_a.score - path_b.score),
            "a": path_a.to_dict(),
            "b": path_b.to_dict(),
        }


# ---------------------------------------------------------------------------
# StoppingHeuristics
# ---------------------------------------------------------------------------

class StoppingHeuristics:
    """Decides whether to continue, ask for help, or stop."""

    def __init__(
        self,
        *,
        max_budget_pct: float = 0.90,
        max_consecutive_errors: int = 3,
        max_retries_per_step: int = 3,
        max_total_retries: int = 8,
        diminishing_returns_window: int = 5,
        min_progress_rate: float = 0.2,
        confidence_floor: float = 0.15,
    ):
        self.max_budget_pct = max_budget_pct
        self.max_consecutive_errors = max_consecutive_errors
        self.max_retries_per_step = max_retries_per_step
        self.max_total_retries = max_total_retries
        self.diminishing_returns_window = diminishing_returns_window
        self.min_progress_rate = min_progress_rate
        self.confidence_floor = confidence_floor

    def evaluate(
        self,
        tracker: CostTracker,
        *,
        current_confidence: float = 0.5,
        goal_progress: float = 0.0,
        consecutive_errors: int = 0,
    ) -> StoppingDecision:
        signals: Dict[str, Any] = {}

        # --- Signal 1: Budget exhaustion ---
        budget_pct = tracker.budget_consumed_pct
        signals["budget_consumed_pct"] = round(budget_pct, 4)
        if tracker.over_budget:
            return StoppingDecision(
                action="stop",
                reason="Budget exhausted",
                confidence=1.0,
                budget_consumed_pct=budget_pct,
                signals=signals,
            )
        if budget_pct >= self.max_budget_pct:
            return StoppingDecision(
                action="ask_user",
                reason=f"Budget nearly exhausted ({budget_pct:.0%})",
                confidence=0.9,
                budget_consumed_pct=budget_pct,
                signals=signals,
            )

        # --- Signal 2: Consecutive error streak ---
        signals["consecutive_errors"] = consecutive_errors
        if consecutive_errors >= self.max_consecutive_errors:
            return StoppingDecision(
                action="ask_user",
                reason=f"Hit {consecutive_errors} consecutive errors",
                confidence=0.85,
                budget_consumed_pct=budget_pct,
                signals=signals,
            )

        # --- Signal 3: Retry saturation ---
        total_retries = tracker.total_retries
        signals["total_retries"] = total_retries
        if total_retries >= self.max_total_retries:
            return StoppingDecision(
                action="ask_user",
                reason=f"Too many retries ({total_retries}/{self.max_total_retries})",
                confidence=0.80,
                budget_consumed_pct=budget_pct,
                signals=signals,
            )

        # --- Signal 4: Diminishing returns ---
        dr = self._diminishing_returns(tracker)
        signals["diminishing_returns"] = dr
        if dr is not None and dr < self.min_progress_rate:
            return StoppingDecision(
                action="ask_user",
                reason=f"Diminishing returns detected (progress rate: {dr:.2f})",
                confidence=0.75,
                budget_consumed_pct=budget_pct,
                signals=signals,
            )

        # --- Signal 5: Confidence collapse ---
        signals["current_confidence"] = round(current_confidence, 4)
        if current_confidence < self.confidence_floor:
            return StoppingDecision(
                action="ask_user",
                reason=f"Confidence too low ({current_confidence:.2f})",
                confidence=0.70,
                budget_consumed_pct=budget_pct,
                signals=signals,
            )

        # --- All clear ---
        signals["goal_progress"] = round(goal_progress, 4)
        return StoppingDecision(
            action="continue",
            reason="Within normal operating parameters",
            confidence=current_confidence,
            budget_consumed_pct=budget_pct,
            signals=signals,
        )

    def _diminishing_returns(self, tracker: CostTracker) -> Optional[float]:
        """Progress rate over the last N steps. None if not enough data."""
        steps = [s for s in tracker._steps if s.tool != "_llm"]
        if len(steps) < self.diminishing_returns_window:
            return None
        window = steps[-self.diminishing_returns_window:]
        successes = sum(1 for s in window if s.status == "succeeded")
        return successes / len(window)


# ---------------------------------------------------------------------------
# RouteOptimizer
# ---------------------------------------------------------------------------

class RouteOptimizer:
    """Selects the most efficient route based on historical data."""

    COST_WEIGHT = 0.30
    DURATION_WEIGHT = 0.15
    STEPS_WEIGHT = 0.15
    SUCCESS_WEIGHT = 0.25
    CONFIDENCE_WEIGHT = 0.15

    def __init__(self, *, pricing: Optional[Dict[str, Dict[str, float]]] = None):
        self._pricing = pricing or dict(DEFAULT_PRICING)
        self._complexity_analyzer = PathComplexityAnalyzer()

    def score_strategy(self, strategy: Dict[str, Any]) -> RouteCandidate:
        """Score a single strategy for efficiency."""
        steps = strategy.get("steps", [])
        duration_ms = strategy.get("duration_ms") or 0
        confidence = strategy.get("confidence", 0.5)
        used_count = strategy.get("used_count", 1)
        outcome = strategy.get("outcome", "")

        complexity = self._complexity_analyzer.analyze_from_strategy(strategy)

        success_rate = 1.0 if outcome == "success" else 0.0
        if used_count > 1:
            success_rate = confidence

        avg_cost = self._estimate_strategy_cost(steps)

        raw_score = (
            (1.0 - self._normalize(avg_cost, 0, 1.0)) * self.COST_WEIGHT
            + (1.0 - self._normalize(duration_ms, 0, 300_000)) * self.DURATION_WEIGHT
            + (1.0 - self._normalize(len(steps), 0, 20)) * self.STEPS_WEIGHT
            + success_rate * self.SUCCESS_WEIGHT
            + confidence * self.CONFIDENCE_WEIGHT
        )

        return RouteCandidate(
            strategy_id=strategy.get("strategy_id", ""),
            goal_type=strategy.get("goal_type", ""),
            avg_cost_usd=avg_cost,
            avg_duration_ms=duration_ms,
            avg_steps=len(steps),
            success_rate=success_rate,
            confidence=confidence,
            efficiency_score=max(0.0, min(1.0, raw_score)),
        )

    def rank_strategies(self, strategies: List[Dict[str, Any]]) -> List[RouteCandidate]:
        """Rank strategies by efficiency. Best first."""
        candidates = [self.score_strategy(s) for s in strategies]
        candidates.sort(key=lambda c: c.efficiency_score, reverse=True)
        return candidates

    def best_route(self, strategies: List[Dict[str, Any]]) -> Optional[RouteCandidate]:
        """Return the best route, or None if no strategies."""
        ranked = self.rank_strategies(strategies)
        return ranked[0] if ranked else None

    def compare_routes(
        self,
        route_a: Dict[str, Any],
        route_b: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Compare two routes and return which is better."""
        a = self.score_strategy(route_a)
        b = self.score_strategy(route_b)
        return {
            "better": "a" if a.efficiency_score >= b.efficiency_score else "b",
            "route_a": a.to_dict(),
            "route_b": b.to_dict(),
            "delta": abs(a.efficiency_score - b.efficiency_score),
        }

    def _estimate_strategy_cost(self, steps: List[Dict[str, Any]]) -> float:
        """Rough cost estimate from strategy steps (no token data available)."""
        total = 0.0
        for step in steps:
            tokens = step.get("tokens")
            if tokens and isinstance(tokens, dict):
                prompt = tokens.get("prompt_tokens", 0)
                completion = tokens.get("completion_tokens", 0)
                usage = TokenUsage(prompt_tokens=prompt, completion_tokens=completion)
                prices = FALLBACK_PRICING
                total += (usage.prompt_tokens / 1_000_000) * prices["input"]
                total += (usage.completion_tokens / 1_000_000) * prices["output"]
            else:
                total += 0.001
        return total

    @staticmethod
    def _normalize(value: float, min_val: float, max_val: float) -> float:
        if max_val <= min_val:
            return 0.0
        return max(0.0, min(1.0, (value - min_val) / (max_val - min_val)))


# ---------------------------------------------------------------------------
# Utility: extract usage from provider response
# ---------------------------------------------------------------------------

def extract_token_usage(provider_response: Dict[str, Any]) -> TokenUsage:
    """Extract token usage from a raw provider API response."""
    usage = provider_response.get("usage", {})
    if not usage:
        return TokenUsage()
    return TokenUsage(
        prompt_tokens=usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("output_tokens", 0) or usage.get("completion_tokens", 0),
        cache_creation_tokens=usage.get("cache_creation_input_tokens", 0),
        cache_read_tokens=usage.get("cache_read_input_tokens", 0),
    )
