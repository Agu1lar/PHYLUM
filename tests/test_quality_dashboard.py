# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import pytest

from autonomy_metrics import RunAutonomyMetrics
from quality_dashboard import QualityDashboard, dimension_key


@pytest.mark.asyncio
async def test_quality_dashboard_aggregates_by_version(tmp_path):
    from agent_persistence import Persistence

    persistence = Persistence(db_path=str(tmp_path / "quality.db"))
    dash = QualityDashboard(persistence)

    for i, success in enumerate([True, True, False]):
        metrics = RunAutonomyMetrics(
            request_id=f"run-{i}",
            runtime_mode="agentic",
            provider="openai",
            model="gpt-4o",
            run_status="completed" if success else "failed",
            success=success,
            steps_to_success=5 if success else 8,
            handoffs_total=1,
            avoidable_handoffs=0 if success else 1,
            recoveries_total=1,
            effective_recoveries=1 if success else 0,
            interrupted_loop=not success,
            cost_usd=0.1,
            total_tokens=500,
        )
        await dash.record_run(metrics)

    version = await dash.get_version(runtime_mode="agentic", provider="openai", model="gpt-4o")
    assert version is not None
    assert version.runs_total == 3
    assert version.runs_succeeded == 2
    assert version.runs_failed == 1
    assert version.success_rate == pytest.approx(2 / 3, abs=0.01)
    assert version.interrupted_loops == 1


@pytest.mark.asyncio
async def test_dashboard_summary_filters(tmp_path):
    from agent_persistence import Persistence

    dash = QualityDashboard(Persistence(db_path=str(tmp_path / "quality2.db")))
    await dash.record_run(
        RunAutonomyMetrics(
            request_id="a",
            runtime_mode="heuristic",
            provider="none",
            model="local",
            success=True,
            steps_to_success=2,
        )
    )
    summary = await dash.dashboard_summary(runtime_mode="heuristic")
    assert summary["version_count"] >= 1
    assert summary["totals"]["runs_total"] >= 1


def test_dimension_key_format():
    assert dimension_key(runtime_mode="agentic", provider="anthropic", model="claude") == "agentic|anthropic|claude"


@pytest.mark.asyncio
async def test_execution_economics_dashboard_actions(monkeypatch, tmp_path):
    from agent_persistence import Persistence
    from tool_execution_economics import ExecutionEconomicsTool

    persistence = Persistence(db_path=str(tmp_path / "eco.db"))
    monkeypatch.setattr(Persistence, "get", lambda: persistence)
    dash = QualityDashboard(persistence)
    await dash.record_run(
        RunAutonomyMetrics(
            request_id="eco-1",
            runtime_mode="agentic",
            provider="gemini",
            model="gemini-2.5-flash",
            success=True,
            steps_to_success=4,
        )
    )

    tool = ExecutionEconomicsTool()
    versions = await tool.run(
        {"action": "list_quality_versions", "provider": "gemini", "limit": 10}
    )
    assert versions.status == "succeeded"
    assert versions.data["versions"]

    board = await tool.run({"action": "get_quality_dashboard", "provider": "gemini"})
    assert board.status == "succeeded"
    assert board.data["version_count"] >= 1
