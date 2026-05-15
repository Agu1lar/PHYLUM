# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import pytest

from autonomy_metrics import AutonomyMetricsStore, compute_run_metrics


def _state(
    *,
    request_id: str = "run-1",
    status: str = "completed",
    with_handoff: bool = False,
    with_recovery: bool = False,
    interrupted: bool = False,
) -> dict:
    tasks = [
        {
            "id": "t1",
            "tool": "filesystem",
            "action": "read",
            "status": "completed",
            "result": {"action_result": {"status": "succeeded"}},
        }
    ]
    if with_recovery:
        tasks.append(
            {
                "id": "t2",
                "tool": "shell",
                "action": "run",
                "status": "completed",
                "recovery": {"classification": "retry", "needs_user": False},
                "result": {"action_result": {"status": "succeeded"}},
            }
        )
    handoffs = []
    if with_handoff:
        handoffs.append(
            {
                "handoff_id": "h1",
                "task_id": "t1",
                "kind": "clarification",
                "tool_call_id": None,
            }
        )
    state = {
        "request_id": request_id,
        "status": status,
        "runtime_mode": "agentic",
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "inputs": {"text": "list files"},
        "tasks": tasks,
        "handoffs": handoffs,
        "outputs": {
            "cost": {"total_cost_usd": 0.05, "total_tokens": 400},
            "agent_final_response": {"steps": 3},
        },
        "agent_session": {"step": 3},
        "history": [],
    }
    if interrupted:
        state["error"] = "Agentic loop reached max_steps=10"
        state["status"] = "failed"
    return state


def test_compute_steps_to_success():
    m = compute_run_metrics(_state())
    assert m.success is True
    assert m.steps_to_success == 3
    assert m.tasks_succeeded == 1


def test_avoidable_handoff_heuristic():
    m = compute_run_metrics(_state(with_handoff=True))
    assert m.handoffs_total == 1
    assert m.avoidable_handoffs == 1


def test_effective_recovery_count():
    m = compute_run_metrics(_state(with_recovery=True))
    assert m.recoveries_total >= 1
    assert m.effective_recoveries >= 1


def test_interrupted_loop_detection():
    m = compute_run_metrics(_state(interrupted=True))
    assert m.interrupted_loop is True
    assert m.interrupt_reason == "max_steps"


@pytest.mark.asyncio
async def test_store_finalize_and_load(tmp_path):
    from agent_persistence import Persistence

    store = AutonomyMetricsStore(Persistence(db_path=str(tmp_path / "auto.db")))
    state = _state(request_id="persist-1")
    metrics = await store.finalize_from_state(state)
    loaded = await store.get_run_metrics("persist-1")
    assert loaded is not None
    assert loaded.steps_to_success == metrics.steps_to_success
