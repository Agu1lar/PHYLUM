# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tests for execution economics — cost tracking, path complexity, stopping heuristics, route optimization."""
from __future__ import annotations

import time

import pytest

from execution_economics import (
    CostTracker,
    DEFAULT_PRICING,
    FALLBACK_PRICING,
    PathComplexityAnalyzer,
    PathComplexityScore,
    RouteCandidate,
    RouteOptimizer,
    RunCostSummary,
    StepRecord,
    StoppingDecision,
    StoppingHeuristics,
    TokenUsage,
    extract_token_usage,
)


# ---------------------------------------------------------------------------
# TokenUsage
# ---------------------------------------------------------------------------

class TestTokenUsage:
    def test_defaults(self):
        u = TokenUsage()
        assert u.prompt_tokens == 0
        assert u.completion_tokens == 0
        assert u.total_tokens == 0

    def test_total(self):
        u = TokenUsage(prompt_tokens=100, completion_tokens=50)
        assert u.total_tokens == 150

    def test_to_dict(self):
        u = TokenUsage(prompt_tokens=100, completion_tokens=50, cache_read_tokens=10)
        d = u.to_dict()
        assert d["prompt_tokens"] == 100
        assert d["completion_tokens"] == 50
        assert d["total_tokens"] == 150
        assert d["cache_read_tokens"] == 10


# ---------------------------------------------------------------------------
# StepRecord
# ---------------------------------------------------------------------------

class TestStepRecord:
    def test_to_dict(self):
        r = StepRecord(
            step_index=0, tool="shell", action="run",
            status="succeeded", duration_ms=500, cost_usd=0.001,
        )
        d = r.to_dict()
        assert d["tool"] == "shell"
        assert d["status"] == "succeeded"
        assert d["cost_usd"] == 0.001

    def test_defaults(self):
        r = StepRecord(step_index=0, tool="x", action="y", status="succeeded")
        assert r.is_retry is False
        assert r.is_replan is False
        assert r.error == ""


# ---------------------------------------------------------------------------
# CostTracker
# ---------------------------------------------------------------------------

class TestCostTracker:
    def test_create_tracker(self):
        t = CostTracker("run-1", model="gpt-4o")
        assert t.run_id == "run-1"
        assert t.model == "gpt-4o"
        assert t.total_tokens == 0
        assert t.total_cost_usd == 0.0

    def test_record_step(self):
        t = CostTracker("run-1", model="gpt-4o")
        usage = TokenUsage(prompt_tokens=1000, completion_tokens=500)
        rec = t.record_step(
            step_index=0, tool="shell", action="run",
            status="succeeded", tokens=usage, duration_ms=200,
        )
        assert rec.tokens.total_tokens == 1500
        assert rec.cost_usd > 0
        assert t.total_tokens == 1500
        assert t.total_steps == 1

    def test_record_multiple_steps(self):
        t = CostTracker("run-1", model="gpt-4o")
        for i in range(5):
            t.record_step(
                step_index=i, tool="filesystem", action="read",
                status="succeeded",
                tokens=TokenUsage(prompt_tokens=100, completion_tokens=50),
            )
        assert t.total_steps == 5
        assert t.total_tokens == 750
        assert t.total_cost_usd > 0

    def test_record_step_with_retry(self):
        t = CostTracker("run-1", model="gpt-4o")
        t.record_step(step_index=0, tool="shell", action="run", status="failed", is_retry=True)
        assert t.total_retries == 1
        assert t.total_errors == 1

    def test_record_step_with_replan(self):
        t = CostTracker("run-1", model="gpt-4o")
        t.record_step(step_index=0, tool="shell", action="run", status="succeeded", is_replan=True)
        assert t.total_replans == 1

    def test_record_llm_usage(self):
        t = CostTracker("run-1", model="gpt-4o")
        usage = t.record_llm_usage(prompt_tokens=2000, completion_tokens=1000)
        assert usage.total_tokens == 3000
        assert t.total_tokens == 3000
        assert t.total_steps == 0  # LLM usage doesn't count as tool steps

    def test_estimate_cost_known_model(self):
        t = CostTracker("run-1", model="gpt-4o")
        usage = TokenUsage(prompt_tokens=1_000_000, completion_tokens=1_000_000)
        cost = t.estimate_cost(usage)
        expected = DEFAULT_PRICING["gpt-4o"]["input"] + DEFAULT_PRICING["gpt-4o"]["output"]
        assert abs(cost - expected) < 0.01

    def test_estimate_cost_unknown_model(self):
        t = CostTracker("run-1", model="unknown-model-xyz")
        usage = TokenUsage(prompt_tokens=1_000_000, completion_tokens=1_000_000)
        cost = t.estimate_cost(usage)
        expected = FALLBACK_PRICING["input"] + FALLBACK_PRICING["output"]
        assert abs(cost - expected) < 0.01

    def test_budget_usd(self):
        t = CostTracker("run-1", model="gpt-4o", budget_usd=0.01)
        t.record_step(
            step_index=0, tool="test", action="x", status="succeeded",
            tokens=TokenUsage(prompt_tokens=1_000_000, completion_tokens=1_000_000),
        )
        assert t.budget_consumed_pct > 0
        assert t.over_budget

    def test_budget_tokens(self):
        t = CostTracker("run-1", model="gpt-4o", budget_tokens=1000)
        t.record_step(
            step_index=0, tool="test", action="x", status="succeeded",
            tokens=TokenUsage(prompt_tokens=600, completion_tokens=500),
        )
        assert t.over_budget

    def test_no_budget(self):
        t = CostTracker("run-1", model="gpt-4o")
        assert t.budget_consumed_pct == 0.0
        assert not t.over_budget

    def test_wall_time(self):
        t = CostTracker("run-1")
        time.sleep(0.05)
        assert t.wall_time_ms >= 40

    def test_summary(self):
        t = CostTracker("run-1", model="gpt-4o")
        t.record_step(step_index=0, tool="shell", action="run", status="succeeded",
                       tokens=TokenUsage(prompt_tokens=100, completion_tokens=50))
        t.record_step(step_index=1, tool="filesystem", action="read", status="failed",
                       is_retry=True, error="not found")
        s = t.summary()
        assert isinstance(s, RunCostSummary)
        assert s.total_steps == 2
        assert s.total_retries == 1
        assert s.total_errors == 1
        assert s.unique_tools == 2
        assert s.model == "gpt-4o"

    def test_summary_to_dict(self):
        t = CostTracker("run-1", model="gpt-4o")
        t.record_step(step_index=0, tool="shell", action="run", status="succeeded")
        d = t.to_dict()
        assert d["run_id"] == "run-1"
        assert "steps" in d

    def test_custom_pricing(self):
        pricing = {"my-model": {"input": 100.0, "output": 200.0}}
        t = CostTracker("run-1", model="my-model", pricing=pricing)
        usage = TokenUsage(prompt_tokens=1_000_000, completion_tokens=1_000_000)
        cost = t.estimate_cost(usage)
        assert abs(cost - 300.0) < 0.01

    def test_model_prefix_matching(self):
        t = CostTracker("run-1", model="gpt-4o-2024-08-06")
        usage = TokenUsage(prompt_tokens=1_000_000, completion_tokens=0)
        cost = t.estimate_cost(usage)
        assert cost == DEFAULT_PRICING["gpt-4o"]["input"]


# ---------------------------------------------------------------------------
# PathComplexityAnalyzer
# ---------------------------------------------------------------------------

class TestPathComplexityAnalyzer:
    def setup_method(self):
        self.analyzer = PathComplexityAnalyzer()

    def test_empty_path(self):
        score = self.analyzer.analyze([])
        assert score.score == 0.0
        assert score.total_steps == 0

    def test_simple_path(self):
        steps = [
            StepRecord(step_index=0, tool="shell", action="run", status="succeeded"),
        ]
        score = self.analyzer.analyze(steps)
        assert score.unique_tools == 1
        assert score.total_steps == 1
        assert score.score > 0

    def test_complex_path(self):
        steps = [
            StepRecord(step_index=0, tool="shell", action="run", status="succeeded"),
            StepRecord(step_index=1, tool="filesystem", action="read", status="succeeded"),
            StepRecord(step_index=2, tool="browser", action="navigate", status="failed"),
            StepRecord(step_index=3, tool="browser", action="navigate", status="succeeded", is_retry=True),
            StepRecord(step_index=4, tool="web", action="search", status="succeeded", is_replan=True),
        ]
        score = self.analyzer.analyze(steps)
        assert score.unique_tools == 4
        assert score.total_steps == 5
        assert score.retry_count == 1
        assert score.replan_count == 1
        assert score.error_rate > 0
        assert score.score > 0

    def test_more_complex_scores_higher(self):
        simple = [StepRecord(step_index=0, tool="shell", action="run", status="succeeded")]
        complex_ = [
            StepRecord(step_index=i, tool=f"tool_{i}", action="a", status="succeeded")
            for i in range(10)
        ]
        s1 = self.analyzer.analyze(simple)
        s2 = self.analyzer.analyze(complex_)
        assert s2.score > s1.score

    def test_branching_increases_score(self):
        steps = [StepRecord(step_index=0, tool="shell", action="run", status="succeeded")]
        s1 = self.analyzer.analyze(steps, branch_count=1)
        s2 = self.analyzer.analyze(steps, branch_count=4)
        assert s2.score > s1.score

    def test_analyze_from_strategy(self):
        strategy = {
            "steps": [
                {"tool": "shell", "action": "run", "status": "succeeded"},
                {"tool": "filesystem", "action": "write", "status": "succeeded"},
            ],
        }
        score = self.analyzer.analyze_from_strategy(strategy)
        assert score.total_steps == 2
        assert score.unique_tools == 2

    def test_compare(self):
        a = PathComplexityScore(score=5.0)
        b = PathComplexityScore(score=10.0)
        result = self.analyzer.compare(a, b)
        assert result["simpler"] == "a"
        assert result["delta"] == 5.0

    def test_depth_computation(self):
        steps = [
            StepRecord(step_index=0, tool="a", action="x", status="succeeded"),
            StepRecord(step_index=1, tool="a", action="x", status="succeeded"),
            StepRecord(step_index=2, tool="b", action="x", status="succeeded"),
            StepRecord(step_index=3, tool="c", action="x", status="succeeded"),
        ]
        score = self.analyzer.analyze(steps)
        assert score.depth == 3

    def test_to_dict(self):
        score = PathComplexityScore(unique_tools=2, total_steps=5, score=7.5)
        d = score.to_dict()
        assert d["unique_tools"] == 2
        assert d["score"] == 7.5


# ---------------------------------------------------------------------------
# StoppingHeuristics
# ---------------------------------------------------------------------------

class TestStoppingHeuristics:
    def setup_method(self):
        self.heuristics = StoppingHeuristics()

    def test_continue_when_healthy(self):
        t = CostTracker("run-1", model="gpt-4o", budget_usd=10.0)
        t.record_step(step_index=0, tool="shell", action="run", status="succeeded",
                       tokens=TokenUsage(prompt_tokens=100, completion_tokens=50))
        decision = self.heuristics.evaluate(t, current_confidence=0.8, goal_progress=0.5)
        assert decision.action == "continue"

    def test_stop_on_budget_exhausted(self):
        t = CostTracker("run-1", model="gpt-4o", budget_usd=0.001)
        t.record_step(step_index=0, tool="test", action="x", status="succeeded",
                       tokens=TokenUsage(prompt_tokens=1_000_000, completion_tokens=500_000))
        decision = self.heuristics.evaluate(t)
        assert decision.action == "stop"
        assert "exhausted" in decision.reason.lower()

    def test_ask_user_on_nearly_exhausted(self):
        # gpt-4o: 1M prompt=$2.50, 500K completion=$5.00, total=$7.50
        # budget=$8.00 → 93.75% consumed, above 90% threshold but below 100%
        t = CostTracker("run-1", model="gpt-4o", budget_usd=8.00)
        t.record_step(step_index=0, tool="test", action="x", status="succeeded",
                       tokens=TokenUsage(prompt_tokens=1_000_000, completion_tokens=500_000))
        decision = self.heuristics.evaluate(t)
        assert decision.action == "ask_user"

    def test_ask_user_on_consecutive_errors(self):
        t = CostTracker("run-1", model="gpt-4o", budget_usd=100.0)
        decision = self.heuristics.evaluate(t, consecutive_errors=5)
        assert decision.action == "ask_user"
        assert "consecutive" in decision.reason.lower()

    def test_ask_user_on_too_many_retries(self):
        t = CostTracker("run-1", model="gpt-4o", budget_usd=100.0)
        for i in range(10):
            t.record_step(step_index=i, tool="shell", action="run",
                           status="failed", is_retry=True)
        decision = self.heuristics.evaluate(t, current_confidence=0.8)
        assert decision.action == "ask_user"
        assert "retries" in decision.reason.lower()

    def test_ask_user_on_diminishing_returns(self):
        t = CostTracker("run-1", model="gpt-4o", budget_usd=100.0)
        for i in range(6):
            t.record_step(step_index=i, tool="shell", action="run", status="failed")
        decision = self.heuristics.evaluate(t, current_confidence=0.8)
        assert decision.action == "ask_user"

    def test_ask_user_on_low_confidence(self):
        t = CostTracker("run-1", model="gpt-4o", budget_usd=100.0)
        t.record_step(step_index=0, tool="shell", action="run", status="succeeded")
        decision = self.heuristics.evaluate(t, current_confidence=0.05)
        assert decision.action == "ask_user"
        assert "confidence" in decision.reason.lower()

    def test_no_budget_means_continue(self):
        t = CostTracker("run-1", model="gpt-4o")
        t.record_step(step_index=0, tool="shell", action="run", status="succeeded",
                       tokens=TokenUsage(prompt_tokens=1_000_000, completion_tokens=500_000))
        decision = self.heuristics.evaluate(t, current_confidence=0.8)
        assert decision.action == "continue"

    def test_decision_has_signals(self):
        t = CostTracker("run-1", model="gpt-4o", budget_usd=10.0)
        decision = self.heuristics.evaluate(t, current_confidence=0.7, consecutive_errors=1)
        assert "budget_consumed_pct" in decision.signals
        assert "consecutive_errors" in decision.signals

    def test_to_dict(self):
        d = StoppingDecision(action="continue", reason="ok", confidence=0.9)
        assert d.to_dict()["action"] == "continue"

    def test_custom_thresholds(self):
        h = StoppingHeuristics(max_consecutive_errors=1)
        t = CostTracker("run-1", model="gpt-4o", budget_usd=100.0)
        decision = h.evaluate(t, consecutive_errors=2)
        assert decision.action == "ask_user"


# ---------------------------------------------------------------------------
# RouteOptimizer
# ---------------------------------------------------------------------------

class TestRouteOptimizer:
    def setup_method(self):
        self.optimizer = RouteOptimizer()

    def _strategy(
        self, sid="s1", confidence=0.9, duration_ms=5000, outcome="success",
        steps=None, used_count=1, goal_type="test",
    ):
        return {
            "strategy_id": sid,
            "goal_type": goal_type,
            "goal_summary": "test goal",
            "steps": steps or [
                {"tool": "shell", "action": "run"},
                {"tool": "filesystem", "action": "write"},
            ],
            "outcome": outcome,
            "confidence": confidence,
            "duration_ms": duration_ms,
            "used_count": used_count,
        }

    def test_score_strategy(self):
        s = self._strategy()
        candidate = self.optimizer.score_strategy(s)
        assert isinstance(candidate, RouteCandidate)
        assert candidate.efficiency_score > 0
        assert candidate.strategy_id == "s1"

    def test_rank_strategies(self):
        fast = self._strategy(sid="fast", confidence=0.95, duration_ms=1000)
        slow = self._strategy(sid="slow", confidence=0.5, duration_ms=100000)
        ranked = self.optimizer.rank_strategies([slow, fast])
        assert ranked[0].strategy_id == "fast"

    def test_best_route(self):
        s1 = self._strategy(sid="a", confidence=0.95)
        s2 = self._strategy(sid="b", confidence=0.4)
        best = self.optimizer.best_route([s1, s2])
        assert best is not None
        assert best.strategy_id == "a"

    def test_best_route_empty(self):
        assert self.optimizer.best_route([]) is None

    def test_compare_routes(self):
        a = self._strategy(sid="a", confidence=0.95, duration_ms=1000)
        b = self._strategy(sid="b", confidence=0.4, duration_ms=50000)
        result = self.optimizer.compare_routes(a, b)
        assert result["better"] == "a"
        assert "delta" in result

    def test_failed_strategy_scores_lower(self):
        good = self._strategy(sid="good", outcome="success", confidence=0.9)
        bad = self._strategy(sid="bad", outcome="failed", confidence=0.3)
        c_good = self.optimizer.score_strategy(good)
        c_bad = self.optimizer.score_strategy(bad)
        assert c_good.efficiency_score > c_bad.efficiency_score

    def test_fewer_steps_scores_higher(self):
        short = self._strategy(sid="short", steps=[{"tool": "shell", "action": "run"}])
        long = self._strategy(sid="long", steps=[{"tool": f"t{i}", "action": "a"} for i in range(15)])
        c_short = self.optimizer.score_strategy(short)
        c_long = self.optimizer.score_strategy(long)
        assert c_short.efficiency_score > c_long.efficiency_score

    def test_to_dict(self):
        c = RouteCandidate(strategy_id="x", goal_type="test", efficiency_score=0.85)
        d = c.to_dict()
        assert d["strategy_id"] == "x"
        assert d["efficiency_score"] == 0.85


# ---------------------------------------------------------------------------
# extract_token_usage utility
# ---------------------------------------------------------------------------

class TestExtractTokenUsage:
    def test_openai_format(self):
        data = {"usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}}
        u = extract_token_usage(data)
        assert u.prompt_tokens == 100
        assert u.completion_tokens == 50

    def test_anthropic_format(self):
        data = {"usage": {"input_tokens": 200, "output_tokens": 100, "cache_creation_input_tokens": 50}}
        u = extract_token_usage(data)
        assert u.prompt_tokens == 200
        assert u.completion_tokens == 100
        assert u.cache_creation_tokens == 50

    def test_empty_response(self):
        u = extract_token_usage({})
        assert u.total_tokens == 0

    def test_missing_usage(self):
        u = extract_token_usage({"content": "hello"})
        assert u.total_tokens == 0


# ---------------------------------------------------------------------------
# Multi-provider usage integration
# ---------------------------------------------------------------------------

class TestMultiProviderUsage:
    def test_agent_turn_result_has_usage(self):
        from multi_provider_client import AgentTurnResult
        r = AgentTurnResult(
            content="hello",
            usage={"prompt_tokens": 100, "completion_tokens": 50},
        )
        assert r.usage["prompt_tokens"] == 100

    def test_agent_turn_result_default_usage(self):
        from multi_provider_client import AgentTurnResult
        r = AgentTurnResult(content="hello")
        assert r.usage == {}

    def test_extract_usage_static_method(self):
        from multi_provider_client import MultiProviderClient
        openai_data = {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        u = MultiProviderClient._extract_usage(openai_data)
        assert u["prompt_tokens"] == 100

        anthropic_data = {"usage": {"input_tokens": 200, "output_tokens": 100}}
        u = MultiProviderClient._extract_usage(anthropic_data)
        assert u["prompt_tokens"] == 200
        assert u["completion_tokens"] == 100

        gemini_data = {"usageMetadata": {"promptTokenCount": 300, "candidatesTokenCount": 150}}
        u = MultiProviderClient._extract_usage(gemini_data)
        assert u["prompt_tokens"] == 300
        assert u["completion_tokens"] == 150


# ---------------------------------------------------------------------------
# Canonical tools integration
# ---------------------------------------------------------------------------

class TestCanonicalIntegration:
    def test_supported_tools(self):
        from canonical_tools import supported_tools
        assert "execution_economics" in supported_tools()

    def test_action_metadata(self):
        from canonical_tools import action_metadata
        meta = action_metadata("execution_economics", "create_tracker")
        assert meta["semantic_type"] == "mutation"
        meta2 = action_metadata("execution_economics", "get_summary")
        assert meta2["semantic_type"] == "inspection"

    def test_task_title(self):
        from canonical_tools import task_title
        title = task_title("execution_economics", "create_tracker", {"run_id": "r1"})
        assert "r1" in title

    def test_tool_definitions(self):
        from canonical_tools import tool_definitions
        defs = tool_definitions()
        names = [d["function"]["name"] for d in defs]
        assert "execution_economics" in names

    def test_normalize_agentic_task(self):
        from canonical_tools import normalize_agentic_task
        task = normalize_agentic_task(
            "execution_economics",
            {"action": "create_tracker", "run_id": "r1", "model": "gpt-4o"},
            task_id="t-1",
        )
        assert task["tool"] == "execution_economics"
        assert task["action"] == "create_tracker"
        assert task["params"]["run_id"] == "r1"


class TestToolRegistry:
    def test_registry_has_execution_economics(self):
        from tool_registry import ToolRegistry
        registry = ToolRegistry()
        assert "execution_economics" in registry.tools
        assert registry.supports("execution_economics")
