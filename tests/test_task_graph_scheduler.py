import asyncio

import pytest

from task_graph import TaskGraphScheduler


def _task(task_id, *, depends_on=None, status="pending", mutates=False, **extra):
    return {
        "id": task_id,
        "title": task_id,
        "tool": "memory",
        "action": "world_query",
        "params": {},
        "depends_on": depends_on or [],
        "status": status,
        "priority": extra.pop("priority", 50),
        "policy_metadata": {
            "mutates_state": mutates,
            "semantic_type": "mutation" if mutates else "inspection",
        },
        **extra,
    }


def test_dependency_graph_schedules_ready_tasks_before_dependents():
    tasks = [
        _task("discover"),
        _task("inspect", depends_on=["discover"]),
        _task("web"),
    ]
    scheduler = TaskGraphScheduler(tasks, max_parallel=4)
    scheduler.prepare()

    batch = scheduler.next_batch()
    assert {task["id"] for task in batch} == {"discover", "web"}

    tasks[0]["status"] = "completed"
    tasks[2]["status"] = "completed"
    batch = scheduler.next_batch()
    assert [task["id"] for task in batch] == ["inspect"]


def test_dependency_graph_rejects_cycles():
    tasks = [
        _task("a", depends_on=["b"]),
        _task("b", depends_on=["a"]),
    ]
    with pytest.raises(ValueError, match="cycle"):
        TaskGraphScheduler(tasks)


def test_branch_execution_skips_alternatives_after_first_success():
    tasks = [
        _task("path-a", branch_group="install-source"),
        _task("path-b", branch_group="install-source"),
        _task("final", depends_on=["path-a"]),
    ]
    scheduler = TaskGraphScheduler(tasks)
    tasks[0]["status"] = "partial"
    scheduler.resolve_alternative_branches()

    assert tasks[1]["status"] == "skipped"
    assert "install-source" in tasks[1]["skip_reason"]


def test_speculative_read_can_run_before_dependency_finishes():
    tasks = [
        _task("confirm-target", status="running"),
        _task("prefetch-docs", depends_on=["confirm-target"], speculative=True),
        _task("write-file", depends_on=["confirm-target"], speculative=True, mutates=True),
    ]
    scheduler = TaskGraphScheduler(tasks, max_parallel=4)

    batch = scheduler.next_batch()
    assert [task["id"] for task in batch] == ["prefetch-docs"]


def test_partial_completion_unblocks_dependents():
    tasks = [
        _task("discover"),
        _task("use-intermediate", depends_on=["discover"]),
    ]
    scheduler = TaskGraphScheduler(tasks)
    tasks[0]["status"] = "partial"

    batch = scheduler.next_batch()
    assert [task["id"] for task in batch] == ["use-intermediate"]


def test_failed_dependency_blocks_dependent_but_allows_partial_finish():
    tasks = [
        _task("ok", status="partial"),
        _task("bad", status="failed"),
        _task("blocked", depends_on=["bad"]),
    ]
    scheduler = TaskGraphScheduler(tasks)
    scheduler.mark_blocked_by_failed_dependencies()

    assert tasks[2]["status"] == "blocked"
    assert scheduler.can_finish_partially() is True


@pytest.mark.asyncio
async def test_runtime_local_pipeline_uses_task_graph_batches(monkeypatch):
    from runtime_manager import RuntimeManager

    events = []

    async def emit(event):
        events.append(event)

    manager = RuntimeManager(emit)
    state = manager._new_state(
        "req-graph",
        {"text": "test", "max_parallel_tasks": 3},
        runtime_mode="heuristic",
        provider=None,
        model=None,
    )
    tasks = [
        _task("a"),
        _task("b"),
        _task("c", depends_on=["a", "b"]),
    ]

    async def fake_plan(_state):
        return {"tasks": tasks, "kind": "tasks", "message": None}

    async def fake_execute(_state, task):
        await asyncio.sleep(0.01)
        task["status"] = "completed"
        return {"action_result": {"status": "succeeded"}}

    monkeypatch.setattr(manager, "_plan_tasks", fake_plan)
    monkeypatch.setattr(manager, "_execute_task_with_recovery", fake_execute)
    monkeypatch.setattr(manager, "_persist_state", lambda _state: asyncio.sleep(0))
    monkeypatch.setattr(manager, "_finalize_goal_for_run", lambda *args, **kwargs: asyncio.sleep(0))

    await manager._run_local_heuristic_pipeline(state)

    assert state["status"] == "completed"
    assert state["outputs"]["final_reflection"]["details"]["task_graph"]["completed"] == ["a", "b", "c"]
    assert any(event["type"] == "task_branch_batch_started" for event in events)
