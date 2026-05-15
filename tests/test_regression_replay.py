# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import pytest

from regression_replay import (
    RegressionReplayEngine,
    RunBaseline,
    extract_cost_from_state,
    extract_run_baseline,
)
from tool_action_confidence import ToolActionConfidenceStore


@pytest.fixture
async def replay_env(tmp_path):
    from agent_persistence import Persistence

    persistence = Persistence(db_path=str(tmp_path / "replay.db"))
    store = ToolActionConfidenceStore(persistence)
    engine = RegressionReplayEngine(persistence=persistence, confidence_store=store)
    return persistence, engine


def _sample_state(request_id: str = "run-sample-1") -> dict:
    return {
        "request_id": request_id,
        "status": "completed",
        "runtime_mode": "heuristic",
        "inputs": {"text": "word find text contrato C:/Reports/contract.docx"},
        "outputs": {"cost": {"total_cost_usd": 0.12, "total_tokens": 800, "tool_steps": 2}},
        "tasks": [
            {
                "id": "task-1",
                "tool": "office",
                "action": "word_find_text",
                "params": {"path": "C:/Reports/contract.docx", "query": "contrato"},
                "status": "completed",
                "result": {"action_result": {"status": "succeeded", "summary": "ok"}},
            },
            {
                "id": "task-2",
                "tool": "memory",
                "action": "remember",
                "params": {"key": "last_doc", "value": "contract.docx"},
                "status": "completed",
                "result": {"action_result": {"status": "succeeded"}},
            },
        ],
        "history": [],
        "agent_session": {},
    }


@pytest.mark.asyncio
async def test_extract_baseline_from_state():
    baseline = extract_run_baseline(_sample_state())
    assert baseline.request_id == "run-sample-1"
    assert len(baseline.plan.steps) == 2
    assert baseline.cost["total_cost_usd"] == 0.12
    assert baseline.task_results["task-1"]["status"] == "succeeded"


def test_extract_cost_fallback_task_count():
    cost = extract_cost_from_state({"tasks": [{}, {}], "outputs": {}, "history": []})
    assert cost["tool_steps"] == 2
    assert cost.get("estimated_from_tasks") is True


@pytest.mark.asyncio
async def test_regression_replay_dry_run(replay_env):
    persistence, engine = replay_env
    state = _sample_state()
    await persistence.save_kv(f"state:{state['request_id']}", state)

    report = await engine.replay(state["request_id"], replan=True, validate_tasks=True)

    assert report.baseline is not None
    assert report.replay_plan is not None
    assert report.replay_task_results
    assert "plan" in {d.category for d in report.diffs}
    assert "cost" in {d.category for d in report.diffs}
    assert "result" in {d.category for d in report.diffs}


@pytest.mark.asyncio
async def test_list_replayable_runs(replay_env):
    persistence, engine = replay_env
    await persistence.save_kv("state:run-a", _sample_state("run-a"))
    runs = await engine.list_replayable_runs()
    assert any(r["request_id"] == "run-a" for r in runs)


@pytest.mark.asyncio
async def test_execution_economics_replay_action(replay_env):
    persistence, _engine = replay_env
    await persistence.save_kv("state:run-eco", _sample_state("run-eco"))

    from tool_execution_economics import ExecutionEconomicsTool

    tool = ExecutionEconomicsTool()
    result = await tool.run(
        {
            "action": "replay_regression",
            "request_id": "run-eco",
            "replan": True,
            "validate_tasks": True,
        }
    )
    assert result.status in {"succeeded", "partial"}
    assert result.data["request_id"] == "run-eco"
    assert "diffs" in result.data


@pytest.mark.asyncio
async def test_execution_economics_confidence_action(replay_env):
    from tool_execution_economics import ExecutionEconomicsTool

    tool = ExecutionEconomicsTool()
    await tool.run(
        {
            "action": "record_tool_outcome",
            "tool": "filesystem",
            "tool_action": "read",
            "status": "succeeded",
        }
    )
    result = await tool.run(
        {
            "action": "get_tool_confidence",
            "tool": "filesystem",
            "tool_action": "read",
        }
    )
    assert result.status == "succeeded"
    assert result.data["confidence"] > 0.5
