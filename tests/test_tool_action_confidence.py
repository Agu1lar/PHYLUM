# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import pytest

from tool_action_confidence import ToolActionConfidenceStore, record_outcome_from_task_result_async


@pytest.fixture
async def confidence_store(tmp_path):
    from agent_persistence import Persistence

    store = ToolActionConfidenceStore(Persistence(db_path=str(tmp_path / "conf.db")))
    return store


@pytest.mark.asyncio
async def test_unseen_tool_has_prior_confidence(confidence_store):
    conf = await confidence_store.get_confidence("filesystem", "read")
    assert conf.confidence == pytest.approx(0.5, abs=0.01)
    assert conf.sample_size == 0


@pytest.mark.asyncio
async def test_successes_increase_confidence(confidence_store):
    for _ in range(4):
        await confidence_store.record_outcome("shell", "run", "succeeded")
    await confidence_store.record_outcome("shell", "run", "failed")
    conf = await confidence_store.get_confidence("shell", "run")
    assert conf.sample_size == 5
    assert conf.stats.successes == 4
    assert conf.confidence > 0.5


@pytest.mark.asyncio
async def test_record_from_task_result(confidence_store):
    task = {"id": "t1", "tool": "memory", "action": "remember"}
    result = {"action_result": {"status": "succeeded", "data": {"duration_ms": 50}}}
    await record_outcome_from_task_result_async(confidence_store, task, result)
    conf = await confidence_store.get_confidence("memory", "remember")
    assert conf.sample_size == 1
    assert conf.stats.avg_duration_ms == 50


@pytest.mark.asyncio
async def test_plan_confidence_aggregate(confidence_store):
    await confidence_store.record_outcome("filesystem", "read", "succeeded")
    await confidence_store.record_outcome("filesystem", "write", "failed")
    summary = await confidence_store.plan_confidence([("filesystem", "read"), ("filesystem", "write")])
    assert summary["average"] > 0
    assert summary["minimum"] < summary["average"]
    assert len(summary["pairs"]) == 2
