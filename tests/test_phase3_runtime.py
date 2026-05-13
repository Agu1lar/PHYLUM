"""Comprehensive tests for Phase 3: Runtime sempre ativo e Planejamento de Longo Prazo.

Covers:
- DurableQueue: enqueue, dequeue, retry, scheduling, stale recovery
- SessionManager: create, checkpoint, phases, expiration
- Persistence: job checkpoints
- PlannerAgent: goal decomposition into phases
- RuntimeManager: daemon lifecycle, goal-session wiring
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict

import pytest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent_persistence import Persistence
from durable_queue import DurableQueue
from session_manager import SessionManager
from planner_models import GoalPhase, GoalDecomposition
from planner_agent import PlannerAgent


@pytest.fixture
def temp_db(tmp_path):
    db_path = str(tmp_path / f"test_{uuid.uuid4().hex[:8]}.db")
    return db_path


@pytest.fixture
def persistence(temp_db):
    p = Persistence(db_path=temp_db)
    return p


@pytest.fixture
def queue(persistence):
    return DurableQueue(persistence)


@pytest.fixture
def sessions(persistence):
    return SessionManager(persistence)


@pytest.fixture
def planner():
    return PlannerAgent()


# ─── DurableQueue Tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_queue_enqueue_and_dequeue(queue):
    goal = await queue.enqueue({"text": "install git"}, workspace="test-ws", priority=10)
    assert goal["goal_id"].startswith("goal-")
    assert goal["status"] == "queued"
    assert goal["workspace"] == "test-ws"
    assert goal["priority"] == 10
    assert goal["inputs"] == {"text": "install git"}

    dequeued = await queue.dequeue()
    assert dequeued is not None
    assert dequeued["goal_id"] == goal["goal_id"]
    assert dequeued["status"] == "running"
    assert dequeued["attempt"] == 1


@pytest.mark.asyncio
async def test_queue_dequeue_empty(queue):
    result = await queue.dequeue()
    assert result is None


@pytest.mark.asyncio
async def test_queue_priority_order(queue):
    await queue.enqueue({"text": "low priority"}, priority=90)
    await queue.enqueue({"text": "high priority"}, priority=5)
    await queue.enqueue({"text": "medium priority"}, priority=50)

    first = await queue.dequeue()
    assert first["inputs"]["text"] == "high priority"
    second = await queue.dequeue()
    assert second["inputs"]["text"] == "medium priority"
    third = await queue.dequeue()
    assert third["inputs"]["text"] == "low priority"


@pytest.mark.asyncio
async def test_queue_mark_completed(queue):
    goal = await queue.enqueue({"text": "test"})
    await queue.dequeue()
    await queue.mark_completed(goal["goal_id"], result_summary="done")

    result = await queue.get_goal(goal["goal_id"])
    assert result["status"] == "completed"
    assert result["result_summary"] == "done"
    assert result["completed_at"] is not None


@pytest.mark.asyncio
async def test_queue_mark_failed_with_retry(queue):
    goal = await queue.enqueue({"text": "fail me"}, max_retries=3)
    await queue.dequeue()
    result = await queue.mark_failed(goal["goal_id"], "some error")

    assert result is not None
    assert result["status"] == "retrying"
    assert result["error"] == "some error"
    assert result["scheduled_at"] is not None


@pytest.mark.asyncio
async def test_queue_mark_failed_exceeds_retries(queue):
    goal = await queue.enqueue({"text": "fail me"}, max_retries=1)
    dequeued = await queue.dequeue()
    assert dequeued["attempt"] == 1

    result = await queue.mark_failed(goal["goal_id"], "final error")
    assert result is not None
    assert result["status"] == "failed"
    assert result["completed_at"] is not None


@pytest.mark.asyncio
async def test_queue_mark_cancelled(queue):
    goal = await queue.enqueue({"text": "cancel me"})
    await queue.mark_cancelled(goal["goal_id"])
    result = await queue.get_goal(goal["goal_id"])
    assert result["status"] == "cancelled"


@pytest.mark.asyncio
async def test_queue_scheduled_deferred(queue):
    future = (datetime.utcnow() + timedelta(hours=1)).isoformat()
    goal = await queue.enqueue({"text": "later"}, scheduled_at=future)
    assert goal["status"] == "deferred"

    dequeued = await queue.dequeue()
    assert dequeued is None


@pytest.mark.asyncio
async def test_queue_promote_deferred(queue):
    past = (datetime.utcnow() - timedelta(seconds=10)).isoformat()
    goal = await queue.enqueue({"text": "ready now"}, scheduled_at=past)
    assert goal["status"] == "deferred"

    promoted = await queue.promote_deferred()
    assert promoted >= 1

    dequeued = await queue.dequeue()
    assert dequeued is not None
    assert dequeued["goal_id"] == goal["goal_id"]


@pytest.mark.asyncio
async def test_queue_list_goals_filter(queue):
    await queue.enqueue({"text": "a"}, workspace="ws1")
    await queue.enqueue({"text": "b"}, workspace="ws2")
    await queue.enqueue({"text": "c"}, workspace="ws1")

    ws1_goals = await queue.list_goals(workspace="ws1")
    assert len(ws1_goals) == 2

    ws2_goals = await queue.list_goals(workspace="ws2")
    assert len(ws2_goals) == 1


@pytest.mark.asyncio
async def test_queue_pending_count(queue):
    await queue.enqueue({"text": "1"})
    await queue.enqueue({"text": "2"})
    await queue.enqueue({"text": "3"})

    count = await queue.pending_count()
    assert count == 3

    await queue.dequeue()
    count = await queue.pending_count()
    assert count == 2


@pytest.mark.asyncio
async def test_queue_parent_goal_id(queue):
    parent = await queue.enqueue({"text": "parent"})
    child1 = await queue.enqueue({"text": "child1"}, parent_goal_id=parent["goal_id"])
    child2 = await queue.enqueue({"text": "child2"}, parent_goal_id=parent["goal_id"])

    children = await queue.list_goals(parent_goal_id=parent["goal_id"])
    assert len(children) == 2
    assert {c["goal_id"] for c in children} == {child1["goal_id"], child2["goal_id"]}


@pytest.mark.asyncio
async def test_queue_recover_stale_running(queue):
    goal = await queue.enqueue({"text": "stale"}, max_retries=2)
    await queue.dequeue()
    recovered = await queue.recover_stale_running(stale_seconds=0)
    assert recovered == 1

    result = await queue.get_goal(goal["goal_id"])
    assert result["status"] == "retrying"


@pytest.mark.asyncio
async def test_queue_cleanup_old(queue):
    goal = await queue.enqueue({"text": "old"})
    await queue.dequeue()
    await queue.mark_completed(goal["goal_id"])

    cleaned = await queue.cleanup_old(days=0)
    assert cleaned >= 1


# ─── SessionManager Tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_create_and_get(sessions):
    session = await sessions.create_session(
        workspace="test-ws", objective="Install and configure git"
    )
    assert session["session_id"].startswith("sess-")
    assert session["status"] == "active"
    assert session["workspace"] == "test-ws"
    assert session["objective"] == "Install and configure git"
    assert session["expires_at"] is not None

    fetched = await sessions.get_session(session["session_id"])
    assert fetched is not None
    assert fetched["session_id"] == session["session_id"]


@pytest.mark.asyncio
async def test_session_update(sessions):
    session = await sessions.create_session(objective="test")
    updated = await sessions.update_session(session["session_id"], {
        "status": "paused",
        "total_runs": 3,
        "total_steps": 15,
    })
    assert updated["status"] == "paused"
    assert updated["total_runs"] == 3
    assert updated["total_steps"] == 15


@pytest.mark.asyncio
async def test_session_checkpoint(sessions):
    session = await sessions.create_session(objective="test")
    updated = await sessions.checkpoint(session["session_id"], {
        "last_task_id": "task-1",
        "step_index": 3,
    })
    assert updated is not None
    assert updated["checkpoint"]["last_task_id"] == "task-1"
    assert updated["checkpoint"]["step_index"] == 3

    updated2 = await sessions.checkpoint(session["session_id"], {
        "step_index": 5,
        "new_key": "value",
    })
    assert updated2["checkpoint"]["last_task_id"] == "task-1"
    assert updated2["checkpoint"]["step_index"] == 5
    assert updated2["checkpoint"]["new_key"] == "value"


@pytest.mark.asyncio
async def test_session_add_run(sessions):
    session = await sessions.create_session()
    updated = await sessions.add_run(session["session_id"], "run-001")
    assert "run-001" in updated["run_ids"]
    assert updated["total_runs"] == 1

    updated2 = await sessions.add_run(session["session_id"], "run-002")
    assert len(updated2["run_ids"]) == 2
    assert updated2["total_runs"] == 2

    updated3 = await sessions.add_run(session["session_id"], "run-001")
    assert len(updated3["run_ids"]) == 2


@pytest.mark.asyncio
async def test_session_add_goal(sessions):
    session = await sessions.create_session()
    updated = await sessions.add_goal(session["session_id"], "goal-001")
    assert "goal-001" in updated["goal_ids"]


@pytest.mark.asyncio
async def test_session_phases(sessions):
    phases = [
        {"title": "Discovery", "status": "pending"},
        {"title": "Installation", "status": "pending"},
        {"title": "Configuration", "status": "pending"},
    ]
    session = await sessions.create_session(phases=phases)
    assert len(session["phases"]) == 3
    assert session["current_phase"] == 0

    updated = await sessions.advance_phase(session["session_id"])
    assert updated["current_phase"] == 1
    assert updated["phases"][0]["status"] == "completed"
    assert updated["phases"][0].get("completed_at") is not None

    updated2 = await sessions.advance_phase(session["session_id"])
    assert updated2["current_phase"] == 2
    assert updated2["phases"][1]["status"] == "completed"

    updated3 = await sessions.advance_phase(session["session_id"])
    assert updated3["current_phase"] == 3
    assert updated3["status"] == "completed"


@pytest.mark.asyncio
async def test_session_merge_context(sessions):
    session = await sessions.create_session(context={"key1": "val1"})
    updated = await sessions.merge_context(session["session_id"], {"key2": "val2"})
    assert updated["context"]["key1"] == "val1"
    assert updated["context"]["key2"] == "val2"


@pytest.mark.asyncio
async def test_session_find_active(sessions):
    session = await sessions.create_session(workspace="my-ws", objective="obj1")

    found = await sessions.find_active_session(workspace="my-ws", objective="obj1")
    assert found is not None
    assert found["session_id"] == session["session_id"]

    found2 = await sessions.find_active_session(workspace="my-ws")
    assert found2 is not None

    found3 = await sessions.find_active_session(workspace="other-ws")
    assert found3 is None


@pytest.mark.asyncio
async def test_session_list_filter(sessions):
    await sessions.create_session(workspace="ws1")
    await sessions.create_session(workspace="ws2")
    await sessions.create_session(workspace="ws1")

    ws1 = await sessions.list_sessions(workspace="ws1")
    assert len(ws1) == 2

    all_sessions = await sessions.list_sessions()
    assert len(all_sessions) == 3


@pytest.mark.asyncio
async def test_session_expire_stale(sessions):
    session = await sessions.create_session()
    await sessions.update_session(session["session_id"], {})
    import aiosqlite
    async with aiosqlite.connect(sessions.persistence.db_path) as db:
        old_time = (datetime.utcnow() - timedelta(hours=200)).isoformat()
        await db.execute(
            "UPDATE agent_sessions SET last_activity_at = ? WHERE session_id = ?",
            (old_time, session["session_id"]),
        )
        await db.commit()

    expired = await sessions.expire_stale(inactive_hours=168)
    assert expired >= 1

    fetched = await sessions.get_session(session["session_id"])
    assert fetched["status"] == "expired"


@pytest.mark.asyncio
async def test_session_cleanup_old(sessions):
    session = await sessions.create_session()
    await sessions.update_session(session["session_id"], {"status": "completed"})
    import aiosqlite
    async with aiosqlite.connect(sessions.persistence.db_path) as db:
        old_time = (datetime.utcnow() - timedelta(days=100)).isoformat()
        await db.execute(
            "UPDATE agent_sessions SET updated_at = ? WHERE session_id = ?",
            (old_time, session["session_id"]),
        )
        await db.commit()

    cleaned = await sessions.cleanup_old(days=90)
    assert cleaned >= 1


# ─── Persistence Job Checkpoint Tests ───────────────────────────────


@pytest.mark.asyncio
async def test_persistence_job_checkpoint(persistence):
    snapshot = {"status": "running", "tasks": [{"id": "t1"}], "agent_session": {"step": 3}}
    await persistence.save_job_checkpoint(
        "job-001", "req-001", snapshot,
        session_id="sess-001",
        step_index=3,
        total_steps=10,
    )

    cp = await persistence.get_job_checkpoint("job-001")
    assert cp is not None
    assert cp["job_id"] == "job-001"
    assert cp["request_id"] == "req-001"
    assert cp["session_id"] == "sess-001"
    assert cp["step_index"] == 3
    assert cp["total_steps"] == 10
    assert cp["resumable"] is True
    assert cp["state_snapshot"]["status"] == "running"


@pytest.mark.asyncio
async def test_persistence_list_resumable(persistence):
    await persistence.save_job_checkpoint("job-a", "req-a", {"s": 1}, step_index=1, total_steps=5)
    await persistence.save_job_checkpoint("job-b", "req-b", {"s": 2}, step_index=2, total_steps=5, resumable=False)
    await persistence.save_job_checkpoint("job-c", "req-c", {"s": 3}, step_index=3, total_steps=5)

    resumable = await persistence.list_resumable_checkpoints()
    job_ids = {cp["job_id"] for cp in resumable}
    assert "job-a" in job_ids
    assert "job-c" in job_ids
    assert "job-b" not in job_ids


@pytest.mark.asyncio
async def test_persistence_delete_checkpoint(persistence):
    await persistence.save_job_checkpoint("job-x", "req-x", {"s": 1})
    await persistence.delete_job_checkpoint("job-x")
    assert await persistence.get_job_checkpoint("job-x") is None


@pytest.mark.asyncio
async def test_persistence_checkpoint_update(persistence):
    await persistence.save_job_checkpoint("job-u", "req-u", {"step": 1}, step_index=1, total_steps=5)
    await persistence.save_job_checkpoint("job-u", "req-u", {"step": 3}, step_index=3, total_steps=5)

    cp = await persistence.get_job_checkpoint("job-u")
    assert cp["step_index"] == 3
    assert cp["state_snapshot"]["step"] == 3


# ─── PlannerAgent Goal Decomposition Tests ──────────────────────────


@pytest.mark.asyncio
async def test_planner_simple_goal_no_decomposition(planner):
    decomp = await planner.decompose_goal("run command Get-Date")
    assert isinstance(decomp, GoalDecomposition)
    assert len(decomp.phases) == 1
    assert decomp.requires_long_running is False


@pytest.mark.asyncio
async def test_planner_complex_goal_detection(planner):
    assert planner.is_complex_goal("install git and then run command git --version") is not None
    assert planner.is_complex_goal("depois de instalar git, configurar o PATH") is not None
    assert planner.is_complex_goal("setup completo do ambiente de desenvolvimento") is not None
    assert planner.is_complex_goal("migrar tudo para o novo servidor") is not None
    assert planner.is_complex_goal("monitorar a cada 5 minutos") is not None
    assert planner.is_complex_goal("pipeline de backup completo") is not None


@pytest.mark.asyncio
async def test_planner_multi_action_decomposition(planner):
    text = "install git, install vscode, install node, run command git --version, run command node --version"
    decomp = await planner.decompose_goal(text)
    assert len(decomp.phases) >= 1
    assert decomp.total_estimated_steps >= 5


@pytest.mark.asyncio
async def test_planner_decompose_different_tools(planner):
    text = "run command Get-Date and list files C:\\Temp and open app notepad and remember project is test"
    decomp = await planner.decompose_goal(text)
    assert len(decomp.phases) >= 1
    total_tasks = sum(len(p.tasks) for p in decomp.phases)
    assert total_tasks >= 3


@pytest.mark.asyncio
async def test_planner_decompose_with_workspace(planner):
    decomp = await planner.decompose_goal("run command Get-Date", workspace="my-ws")
    assert decomp.workspace == "my-ws"


@pytest.mark.asyncio
async def test_planner_recurring_is_long_running(planner):
    text = "monitorar a cada 5 minutos o status da impressora"
    goal_type = planner.is_complex_goal(text)
    assert goal_type == "recurring"


@pytest.mark.asyncio
async def test_goal_phase_model():
    phase = GoalPhase(
        phase_id="phase-0",
        title="Test Phase",
        description="A test phase",
        priority=10,
        estimated_complexity="low",
    )
    assert phase.status == "pending"
    assert phase.depends_on_phases == []
    assert phase.tasks == []


@pytest.mark.asyncio
async def test_goal_decomposition_model():
    decomp = GoalDecomposition(
        original_text="test",
        goal_type="simple",
        phases=[],
        total_estimated_steps=0,
        requires_long_running=False,
    )
    assert decomp.workspace == "default"


# ─── Integration: Queue + Session wiring ────────────────────────────


@pytest.mark.asyncio
async def test_queue_with_session_flow(queue, sessions):
    session = await sessions.create_session(workspace="dev", objective="setup git")

    goal = await queue.enqueue(
        {"text": "install git"},
        workspace="dev",
    )

    await sessions.add_goal(session["session_id"], goal["goal_id"])

    dequeued = await queue.dequeue()
    assert dequeued is not None

    await sessions.add_run(session["session_id"], "run-fake-001")

    await queue.mark_completed(goal["goal_id"], result_summary="git installed")

    await sessions.checkpoint(session["session_id"], {
        "git_installed": True,
        "last_goal": goal["goal_id"],
    })

    final_session = await sessions.get_session(session["session_id"])
    assert goal["goal_id"] in final_session["goal_ids"]
    assert "run-fake-001" in final_session["run_ids"]
    assert final_session["checkpoint"]["git_installed"] is True


@pytest.mark.asyncio
async def test_full_multi_phase_session(queue, sessions):
    phases = [
        {"title": "Phase 1: Install", "status": "pending"},
        {"title": "Phase 2: Configure", "status": "pending"},
        {"title": "Phase 3: Verify", "status": "pending"},
    ]
    session = await sessions.create_session(
        workspace="dev",
        objective="full git setup",
        phases=phases,
    )

    for i, phase_def in enumerate(phases):
        goal = await queue.enqueue(
            {"text": f"step for {phase_def['title']}"},
            workspace="dev",
        )
        await sessions.add_goal(session["session_id"], goal["goal_id"])
        dequeued = await queue.dequeue()
        assert dequeued is not None
        await queue.mark_completed(goal["goal_id"])
        await sessions.advance_phase(session["session_id"])

    final = await sessions.get_session(session["session_id"])
    assert final["status"] == "completed"
    assert final["current_phase"] == 3
    assert len(final["goal_ids"]) == 3


@pytest.mark.asyncio
async def test_retry_flow_through_queue(queue):
    goal = await queue.enqueue({"text": "flaky task"}, max_retries=3, retry_delay_seconds=0)

    for attempt in range(3):
        dequeued = await queue.dequeue()
        if dequeued is None:
            await queue.promote_deferred()
            dequeued = await queue.dequeue()
        assert dequeued is not None
        await queue.mark_failed(goal["goal_id"], f"error attempt {attempt + 1}")

    final = await queue.get_goal(goal["goal_id"])
    assert final["status"] == "failed"


@pytest.mark.asyncio
async def test_checkpoint_and_resume_flow(persistence):
    await persistence.save_job_checkpoint(
        "job-resume",
        "req-resume",
        {
            "status": "running",
            "agent_session": {"messages": [{"role": "user", "content": "hello"}], "step": 5},
            "autonomy": {"observations": ["saw something"]},
            "tasks": [{"id": "t1", "status": "completed"}, {"id": "t2", "status": "pending"}],
        },
        step_index=1,
        total_steps=2,
    )

    resumable = await persistence.list_resumable_checkpoints()
    assert any(cp["job_id"] == "job-resume" for cp in resumable)

    cp = await persistence.get_job_checkpoint("job-resume")
    assert cp["state_snapshot"]["agent_session"]["step"] == 5
    assert len(cp["state_snapshot"]["tasks"]) == 2

    await persistence.delete_job_checkpoint("job-resume")
    assert await persistence.get_job_checkpoint("job-resume") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
