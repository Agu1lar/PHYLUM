"""Comprehensive test suite for the State Graph engine and related integrations.

Tests:
  - GraphNode / GraphEdge basics
  - StateGraph construction, compilation, validation
  - GraphExecutor traversal (linear, conditional, loops, max_visits)
  - GraphTraversalLog
  - GraphExecutor.find_recovery_target
  - graph_definitions: build_local_graph, build_agentic_graph, build_manual_graph
  - RecoveryEngine target_node attachment
  - RecoveryEngine.resolve_graph_target
  - RuntimeManager._graph_executor_for
"""
from __future__ import annotations

import asyncio
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from state_graph import (
    GraphEdge,
    GraphExecutor,
    GraphNode,
    GraphTraversalLog,
    NodeType,
    StateGraph,
)
from graph_definitions import (
    build_agentic_graph,
    build_local_graph,
    build_manual_graph,
)
from recovery_engine import RecoveryEngine


# ─── Helpers ────────────────────────────────────────────────────────


async def _noop_handler(state):
    return {}


async def _echo_handler(state):
    return {"echo": True}


async def _error_handler(state):
    raise RuntimeError("boom")


async def _counter_handler(state):
    state.setdefault("_counter", 0)
    state["_counter"] += 1
    return {"count": state["_counter"]}


# ─── GraphNode / GraphEdge ──────────────────────────────────────────


def test_graph_node_equality():
    a = GraphNode("a", NodeType.ENTRY)
    b = GraphNode("a", NodeType.PLANNER)
    c = GraphNode("c", NodeType.ENTRY)
    assert a == b
    assert a != c


def test_graph_node_hash():
    a = GraphNode("x", NodeType.ENTRY)
    b = GraphNode("x", NodeType.PLANNER)
    assert hash(a) == hash(b)
    s = {a, b}
    assert len(s) == 1


def test_graph_edge_matches_unconditional():
    edge = GraphEdge("a", "b")
    assert edge.matches({}, {}) is True


def test_graph_edge_matches_condition_true():
    edge = GraphEdge("a", "b", condition=lambda s, r: r.get("ok"))
    assert edge.matches({}, {"ok": True}) is True


def test_graph_edge_matches_condition_false():
    edge = GraphEdge("a", "b", condition=lambda s, r: r.get("ok"))
    assert edge.matches({}, {"ok": False}) is False


def test_graph_edge_condition_exception_returns_false():
    edge = GraphEdge("a", "b", condition=lambda s, r: 1 / 0)
    assert edge.matches({}, {}) is False


# ─── StateGraph construction ───────────────────────────────────────


def test_add_node_and_get():
    g = StateGraph("test")
    g.add_node("start", NodeType.ENTRY)
    assert g.get_node("start") is not None
    assert g.get_node("start").node_type == NodeType.ENTRY
    assert g.get_node("missing") is None


def test_add_edge():
    g = StateGraph("test")
    g.add_node("a", NodeType.ENTRY)
    g.add_node("b", NodeType.COMPLETE)
    g.add_edge("a", "b", label="go")
    assert len(g.edges["a"]) == 1
    assert g.edges["a"][0].target == "b"


def test_compile_sets_entry():
    g = StateGraph("test")
    g.add_node("start", NodeType.ENTRY)
    g.add_node("end", NodeType.COMPLETE)
    g.add_edge("start", "end")
    g.compile()
    assert g.entry_node == "start"
    assert g._compiled is True


def test_compile_no_entry_raises():
    g = StateGraph("test")
    g.add_node("x", NodeType.PLANNER)
    with pytest.raises(ValueError, match="no ENTRY"):
        g.compile()


def test_compile_bad_edge_source():
    g = StateGraph("test")
    g.add_node("a", NodeType.ENTRY)
    g.edges["nonexistent"] = [GraphEdge("nonexistent", "a")]
    with pytest.raises(ValueError, match="not a graph node"):
        g.compile()


def test_compile_bad_edge_target():
    g = StateGraph("test")
    g.add_node("a", NodeType.ENTRY)
    g.add_edge("a", "nonexistent")
    with pytest.raises(ValueError, match="not a graph node"):
        g.compile()


def test_successors_and_predecessors():
    g = StateGraph("test")
    g.add_node("a", NodeType.ENTRY)
    g.add_node("b", NodeType.EXECUTOR)
    g.add_node("c", NodeType.COMPLETE)
    g.add_edge("a", "b")
    g.add_edge("a", "c")
    g.add_edge("b", "c")
    g.compile()
    assert g.successors("a") == ["b", "c"]
    assert g.predecessors("c") == ["a", "b"]
    assert g.predecessors("a") == []


def test_find_nodes_by_type():
    g = StateGraph("test")
    g.add_node("a", NodeType.EXECUTOR)
    g.add_node("b", NodeType.EXECUTOR)
    g.add_node("c", NodeType.PLANNER)
    assert len(g.find_nodes_by_type(NodeType.EXECUTOR)) == 2
    assert len(g.find_nodes_by_type(NodeType.PLANNER)) == 1


def test_to_dict():
    g = StateGraph("demo")
    g.add_node("start", NodeType.ENTRY)
    g.add_node("end", NodeType.COMPLETE)
    g.add_edge("start", "end", label="go")
    g.compile()
    d = g.to_dict()
    assert d["name"] == "demo"
    assert d["entry"] == "start"
    assert "start" in d["nodes"]
    assert d["nodes"]["start"]["type"] == "entry"
    assert len(d["edges"]["start"]) == 1


# ─── GraphTraversalLog ─────────────────────────────────────────────


def test_traversal_log_record():
    log = GraphTraversalLog()
    log.record("a", "entry")
    log.record("b", "executor", result_keys=["data"])
    log.record("c", "fail", error="boom")
    assert log.visited_nodes == ["a", "b", "c"]
    assert log.last_node == "c"


def test_traversal_log_find_last_of_type():
    log = GraphTraversalLog()
    log.record("a", "executor")
    log.record("b", "planner")
    log.record("c", "executor")
    assert log.find_last_of_type("executor") == "c"
    assert log.find_last_of_type("planner") == "b"
    assert log.find_last_of_type("reflection") is None


def test_traversal_log_empty():
    log = GraphTraversalLog()
    assert log.last_node is None
    assert log.visited_nodes == []


# ─── GraphExecutor ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_executor_simple_linear():
    g = StateGraph("test")
    g.add_node("start", NodeType.ENTRY, handler=_echo_handler)
    g.add_node("end", NodeType.COMPLETE, handler=_noop_handler)
    g.add_edge("start", "end")
    g.compile()

    executor = GraphExecutor(g)
    state = await executor.run({})
    assert state["_graph"]["terminal_node"] == "end"
    assert state["_graph"]["terminal_type"] == "complete"
    traversal = state["_graph"]["traversal"]
    assert "start" in traversal.visited_nodes
    assert "end" in traversal.visited_nodes


@pytest.mark.asyncio
async def test_executor_conditional_edges():
    g = StateGraph("test")
    g.add_node("start", NodeType.ENTRY, handler=_echo_handler)
    g.add_node("branch_a", NodeType.COMPLETE, handler=_noop_handler)
    g.add_node("branch_b", NodeType.COMPLETE, handler=_noop_handler)
    g.add_edge("start", "branch_a", condition=lambda s, r: r.get("echo") is True, priority=10)
    g.add_edge("start", "branch_b", condition=lambda s, r: r.get("echo") is False, priority=20)
    g.compile()

    executor = GraphExecutor(g)
    state = await executor.run({})
    assert state["_graph"]["terminal_node"] == "branch_a"


@pytest.mark.asyncio
async def test_executor_conditional_takes_second_branch():
    g = StateGraph("test")
    g.add_node("start", NodeType.ENTRY, handler=_noop_handler)
    g.add_node("branch_a", NodeType.COMPLETE, handler=_noop_handler)
    g.add_node("branch_b", NodeType.COMPLETE, handler=_noop_handler)
    g.add_edge("start", "branch_a", condition=lambda s, r: s.get("go_a", False), priority=10)
    g.add_edge("start", "branch_b", condition=lambda s, r: True, priority=20)
    g.compile()

    executor = GraphExecutor(g)
    state = await executor.run({"go_a": False})
    assert state["_graph"]["terminal_node"] == "branch_b"


@pytest.mark.asyncio
async def test_executor_handles_handler_error():
    g = StateGraph("test")
    g.add_node("start", NodeType.ENTRY, handler=_error_handler)
    g.add_node("catch", NodeType.FAIL, handler=_noop_handler)
    g.add_edge("start", "catch", condition=lambda s, r: bool(r.get("_error")))
    g.compile()

    executor = GraphExecutor(g)
    state = await executor.run({})
    assert state["_graph"]["terminal_node"] == "catch"
    traversal = state["_graph"]["traversal"]
    assert traversal.entries[0]["error"] == "boom"


@pytest.mark.asyncio
async def test_executor_max_visits_guard():
    g = StateGraph("test")
    g.add_node("loop", NodeType.ENTRY, handler=_counter_handler)
    g.add_edge("loop", "loop")
    g.compile()

    executor = GraphExecutor(g, max_visits=5)
    with pytest.raises(RuntimeError, match="max_visits"):
        await executor.run({})


@pytest.mark.asyncio
async def test_executor_loop_with_exit_condition():
    async def loop_handler(state):
        state.setdefault("_loops", 0)
        state["_loops"] += 1
        return {"done": state["_loops"] >= 3}

    g = StateGraph("test")
    g.add_node("loop", NodeType.ENTRY, handler=loop_handler)
    g.add_node("end", NodeType.COMPLETE, handler=_noop_handler)
    g.add_edge("loop", "end", condition=lambda s, r: r.get("done"), priority=10)
    g.add_edge("loop", "loop", condition=lambda s, r: True, priority=50)
    g.compile()

    executor = GraphExecutor(g)
    state = await executor.run({})
    assert state["_graph"]["terminal_node"] == "end"
    assert state["_loops"] == 3


@pytest.mark.asyncio
async def test_executor_handoff_pauses():
    g = StateGraph("test")
    g.add_node("start", NodeType.ENTRY, handler=_noop_handler)
    g.add_node("hand", NodeType.HANDOFF, handler=_noop_handler)
    g.add_edge("start", "hand")
    g.compile()

    executor = GraphExecutor(g)
    state = await executor.run({})
    assert state["_graph"]["paused_at"] == "hand"


@pytest.mark.asyncio
async def test_executor_cancel_event():
    cancel = asyncio.Event()
    cancel.set()

    g = StateGraph("test")
    g.add_node("start", NodeType.ENTRY, handler=_noop_handler)
    g.add_node("end", NodeType.COMPLETE)
    g.add_edge("start", "end")
    g.compile()

    executor = GraphExecutor(g)
    with pytest.raises(asyncio.CancelledError):
        await executor.run({}, cancel_event=cancel)


@pytest.mark.asyncio
async def test_executor_start_node_override():
    g = StateGraph("test")
    g.add_node("start", NodeType.ENTRY, handler=_noop_handler)
    g.add_node("mid", NodeType.EXECUTOR, handler=_echo_handler)
    g.add_node("end", NodeType.COMPLETE, handler=_noop_handler)
    g.add_edge("start", "mid")
    g.add_edge("mid", "end")
    g.compile()

    executor = GraphExecutor(g)
    state = await executor.run({}, start_node="mid")
    traversal = state["_graph"]["traversal"]
    assert "start" not in traversal.visited_nodes
    assert "mid" in traversal.visited_nodes
    assert "end" in traversal.visited_nodes


@pytest.mark.asyncio
async def test_executor_no_matching_edge_raises():
    g = StateGraph("test")
    g.add_node("start", NodeType.ENTRY, handler=_noop_handler)
    g.add_node("end", NodeType.COMPLETE)
    g.add_edge("start", "end", condition=lambda s, r: False)
    g.compile()

    executor = GraphExecutor(g)
    with pytest.raises(RuntimeError, match="No matching edge"):
        await executor.run({})


# ─── find_recovery_target ──────────────────────────────────────────


def test_find_recovery_target_explicit_node():
    g = StateGraph("test")
    g.add_node("entry", NodeType.ENTRY)
    g.add_node("exec", NodeType.EXECUTOR)
    g.add_node("end", NodeType.COMPLETE)
    g.add_edge("entry", "exec")
    g.add_edge("exec", "end")
    g.compile()

    executor = GraphExecutor(g)
    target = executor.find_recovery_target({}, {"target_node": "exec"})
    assert target == "exec"


def test_find_recovery_target_by_suggested_action():
    g = StateGraph("test")
    g.add_node("entry", NodeType.ENTRY)
    g.add_node("plan", NodeType.PLANNER)
    g.add_node("exec", NodeType.EXECUTOR)
    g.add_node("reflect", NodeType.REFLECTION)
    g.add_node("hand", NodeType.HANDOFF)
    g.add_node("script", NodeType.SCRIPT_RECOVERY)
    g.add_node("end", NodeType.COMPLETE)
    for src, tgt in [("entry", "plan"), ("plan", "exec"), ("exec", "reflect"), ("reflect", "end")]:
        g.add_edge(src, tgt)
    g.compile()
    executor = GraphExecutor(g)

    assert executor.find_recovery_target({}, {"suggested_action": "retry"}) == "exec"
    assert executor.find_recovery_target({}, {"suggested_action": "replan"}) == "plan"
    assert executor.find_recovery_target({}, {"suggested_action": "rediscover_target"}) == "plan"
    assert executor.find_recovery_target({}, {"suggested_action": "verify_outcome"}) == "reflect"
    assert executor.find_recovery_target({}, {"suggested_action": "ask_user"}) == "hand"
    assert executor.find_recovery_target({}, {"suggested_action": "execute_script"}) == "script"


def test_find_recovery_target_from_traversal_log():
    log = GraphTraversalLog()
    log.record("entry", "entry")
    log.record("plan", "planner")
    log.record("exec", "executor")
    log.record("reflect", "reflection")

    g = StateGraph("test")
    g.add_node("entry", NodeType.ENTRY)
    g.add_node("exec", NodeType.EXECUTOR)
    g.add_node("plan", NodeType.PLANNER)
    g.add_node("reflect", NodeType.REFLECTION)
    g.add_node("end", NodeType.COMPLETE)
    g.add_edge("entry", "plan")
    g.add_edge("plan", "exec")
    g.add_edge("exec", "reflect")
    g.add_edge("reflect", "end")
    g.compile()
    executor = GraphExecutor(g)

    state = {"_graph": {"traversal": log}}
    target = executor.find_recovery_target(state, {"classification": "retryable", "suggested_action": "unknown_action"})
    assert target == "exec"

    target2 = executor.find_recovery_target(state, {"classification": "replan_required", "suggested_action": "unknown_action"})
    assert target2 == "plan"


def test_find_recovery_target_returns_none_for_unknown():
    g = StateGraph("test")
    g.add_node("entry", NodeType.ENTRY)
    g.add_node("end", NodeType.COMPLETE)
    g.add_edge("entry", "end")
    g.compile()
    executor = GraphExecutor(g)

    target = executor.find_recovery_target({}, {"suggested_action": "unknown_bizarre_action"})
    assert target is None


# ─── Graph definitions ──────────────────────────────────────────────


def test_build_local_graph_compiles():
    g = build_local_graph()
    assert g._compiled is True
    assert g.entry_node == "entry"
    assert g.name == "local_heuristic"
    assert len(g.nodes) >= 10
    assert "planner" in g.nodes
    assert "safety" in g.nodes
    assert "executor" in g.nodes
    assert "reflection" in g.nodes
    assert "recovery" in g.nodes
    assert "script_recovery" in g.nodes
    assert "complete" in g.nodes
    assert "fail" in g.nodes


def test_build_agentic_graph_compiles():
    g = build_agentic_graph()
    assert g._compiled is True
    assert g.entry_node == "entry"
    assert g.name == "agentic"
    assert "llm_loop" in g.nodes
    assert "recovery" in g.nodes
    assert "handoff" in g.nodes
    assert "complete" in g.nodes
    assert "fail" in g.nodes


def test_build_manual_graph_compiles():
    g = build_manual_graph()
    assert g._compiled is True
    assert g.entry_node == "entry"
    assert g.name == "manual_assist"
    assert "planner" in g.nodes
    assert "complete" in g.nodes


def test_local_graph_topology_planner_to_task_picker():
    g = build_local_graph()
    planner_edges = g.edges["planner"]
    target_ids = [e.target for e in planner_edges]
    assert "task_picker" in target_ids
    assert "plan_empty" in target_ids


def test_local_graph_recovery_edges():
    g = build_local_graph()
    recovery_edges = g.edges["recovery"]
    targets = [e.target for e in recovery_edges]
    assert "executor" in targets
    assert "script_recovery" in targets
    assert "handoff" in targets
    assert "planner" in targets
    assert "fail" in targets


def test_agentic_graph_recovery_edges():
    g = build_agentic_graph()
    recovery_edges = g.edges["recovery"]
    targets = [e.target for e in recovery_edges]
    assert "llm_loop" in targets
    assert "script_recovery" in targets
    assert "handoff" in targets
    assert "fail" in targets


def test_local_graph_executor_edges():
    """Executor should route to reflection (success) or recovery (error)."""
    g = build_local_graph()
    executor_edges = g.edges["executor"]
    targets = [e.target for e in executor_edges]
    assert "reflection" in targets
    assert "recovery" in targets


def test_local_graph_safety_edges():
    g = build_local_graph()
    safety_edges = g.edges["safety"]
    targets = [e.target for e in safety_edges]
    assert "fail" in targets
    assert "approval" in targets
    assert "executor" in targets


# ─── RecoveryEngine target_node ─────────────────────────────────────


def test_recovery_engine_attach_target_node_retry():
    engine = RecoveryEngine()
    result = engine.classify(
        task={"tool": "shell", "action": "run"},
        error="timeout connecting",
        attempt=1,
        max_attempts=3,
    )
    assert "target_node" in result
    assert result["target_node"] == "executor"


def test_recovery_engine_attach_target_node_ask_user():
    engine = RecoveryEngine()
    result = engine.classify(
        task={"tool": "shell", "action": "run", "result": {"action_result": {"issue": {"kind": "ambiguous_match", "message": "pick one"}}}},
        error="ambiguous match",
        attempt=5,
        max_attempts=2,
    )
    assert result["target_node"] == "handoff"


def test_recovery_engine_attach_target_node_approval_rejected():
    engine = RecoveryEngine()
    result = engine.classify(
        task={"tool": "shell", "action": "run"},
        error="approval rejected",
        attempt=1,
        max_attempts=2,
    )
    assert result["target_node"] == "fail"


def test_recovery_engine_classify_action_result_replan():
    engine = RecoveryEngine()
    result = engine.classify_action_result(
        task={"tool": "windows_ui", "action": "click"},
        action_result={
            "status": "partial",
            "goal": {"satisfied": False, "rationale": "need more steps"},
        },
        attempt=1,
    )
    assert result["target_node"] == "reflection"


def test_recovery_engine_classify_action_result_switch_tooling():
    engine = RecoveryEngine()
    result = engine.classify_action_result(
        task={"tool": "office", "action": "excel_read"},
        action_result={
            "status": "failed",
            "issue": {"kind": "office_com_unavailable", "message": "COM not available"},
        },
        attempt=1,
    )
    assert result["target_node"] == "planner"


def test_recovery_engine_resolve_graph_target_with_executor():
    g = build_local_graph()
    executor = GraphExecutor(g)
    engine = RecoveryEngine()

    target = engine.resolve_graph_target(
        {},
        {"suggested_action": "retry", "target_node": "executor"},
        graph_executor=executor,
    )
    assert target == "executor"


def test_recovery_engine_resolve_graph_target_without_executor():
    engine = RecoveryEngine()
    target = engine.resolve_graph_target(
        {},
        {"target_node": "planner"},
        graph_executor=None,
    )
    assert target == "planner"


def test_recovery_engine_resolve_graph_target_prefers_executor():
    g = build_local_graph()
    gexec = GraphExecutor(g)
    engine = RecoveryEngine()
    target = engine.resolve_graph_target(
        {},
        {"suggested_action": "replan"},
        graph_executor=gexec,
    )
    assert target == "planner"


# ─── Edge condition functions ───────────────────────────────────────


def test_edge_conditions_from_definitions():
    from graph_definitions import (
        _edge_success, _edge_error, _edge_has_tasks, _edge_no_tasks,
        _edge_denied, _edge_needs_approval, _edge_allowed,
        _edge_approval_granted, _edge_approval_rejected,
        _edge_task_succeeded, _edge_task_partial, _edge_task_failed,
        _edge_retryable, _edge_needs_user, _edge_script_recovery,
        _edge_terminal_failure, _edge_always,
    )
    assert _edge_success({}, {}) is True
    assert _edge_success({}, {"_error": "x"}) is False
    assert _edge_error({}, {"_error": "x"}) is True
    assert _edge_error({}, {}) is False

    assert _edge_has_tasks({}, {"tasks": [1]}) is True
    assert _edge_no_tasks({}, {"tasks": []}) is True
    assert _edge_no_tasks({}, {"tasks": [1]}) is False

    assert _edge_denied({}, {"safety": {"status": "deny"}}) is True
    assert _edge_denied({}, {"safety": {"status": "allow"}}) is False

    assert _edge_needs_approval({}, {"safety": {"status": "require_approval"}}) is True
    assert _edge_allowed({}, {"safety": {"status": "allow"}}) is True
    assert _edge_allowed({}, {"safety": {"status": "deny"}}) is False

    assert _edge_approval_granted({}, {"approval_status": "approved"}) is True
    assert _edge_approval_rejected({}, {"approval_status": "rejected"}) is True

    assert _edge_task_succeeded({}, {"action_result": {"status": "succeeded"}}) is True
    assert _edge_task_partial({}, {"action_result": {"status": "partial"}}) is True
    assert _edge_task_failed({}, {"_error": "x"}) is True

    assert _edge_retryable({}, {"recovery": {"retryable": True}}) is True
    assert _edge_needs_user({}, {"recovery": {"needs_user": True}}) is True
    assert _edge_script_recovery({}, {"recovery": {"classification": "script_recovery", "script": {"tool": "x"}}}) is True

    assert _edge_terminal_failure({}, {"recovery": {"classification": "terminal"}}) is True
    assert _edge_always({}, {}) is True


# ─── Integration: full graph walkthrough ────────────────────────────


@pytest.mark.asyncio
async def test_full_local_graph_success_walkthrough():
    """Simulate a simplified happy path through the local graph."""
    tasks = [{"id": "t1", "status": "pending"}]

    async def planner_handler(state):
        state["tasks"] = tasks
        return {"tasks": tasks}

    async def task_picker_handler(state):
        pending = [t for t in state.get("tasks", []) if t["status"] != "completed"]
        if not pending:
            return {"tasks": []}
        state["current_task"] = pending[0]
        return {"tasks": pending}

    async def safety_handler(state):
        return {"safety": {"status": "allow"}}

    async def executor_handler(state):
        task = state.get("current_task", {})
        task["status"] = "completed"
        return {"action_result": {"status": "succeeded"}}

    async def reflection_handler(state):
        return {"reflection": "ok"}

    async def checkpoint_handler(state):
        return {}

    g = StateGraph("walkthrough")
    g.add_node("entry", NodeType.ENTRY, handler=_noop_handler)
    g.add_node("planner", NodeType.PLANNER, handler=planner_handler)
    g.add_node("task_picker", NodeType.ROUTER, handler=task_picker_handler)
    g.add_node("safety", NodeType.SAFETY, handler=safety_handler)
    g.add_node("executor", NodeType.EXECUTOR, handler=executor_handler)
    g.add_node("reflection", NodeType.REFLECTION, handler=reflection_handler)
    g.add_node("checkpoint", NodeType.CHECKPOINT, handler=checkpoint_handler)
    g.add_node("complete", NodeType.COMPLETE, handler=_noop_handler)

    from graph_definitions import (
        _edge_has_tasks, _edge_no_tasks, _edge_allowed, _edge_success,
        _edge_task_succeeded, _edge_always,
    )

    g.add_edge("entry", "planner")
    g.add_edge("planner", "task_picker", condition=_edge_has_tasks)
    g.add_edge("task_picker", "complete", condition=_edge_no_tasks, priority=10)
    g.add_edge("task_picker", "safety", condition=_edge_has_tasks, priority=20)
    g.add_edge("safety", "executor", condition=_edge_allowed)
    g.add_edge("executor", "reflection", condition=_edge_success)
    g.add_edge("reflection", "checkpoint", condition=_edge_task_succeeded)
    g.add_edge("reflection", "checkpoint", condition=_edge_always, priority=99)
    g.add_edge("checkpoint", "task_picker")
    g.compile()

    executor = GraphExecutor(g)
    state = await executor.run({})

    assert state["_graph"]["terminal_node"] == "complete"
    visited = state["_graph"]["traversal"].visited_nodes
    assert "planner" in visited
    assert "safety" in visited
    assert "executor" in visited
    assert "reflection" in visited
    assert "checkpoint" in visited
    assert "complete" in visited


@pytest.mark.asyncio
async def test_full_graph_with_retry():
    """Simulate a graph where executor fails once, recovery retries, then succeeds."""
    attempt_count = {"n": 0}

    async def executor_handler(state):
        attempt_count["n"] += 1
        if attempt_count["n"] < 2:
            raise RuntimeError("transient failure")
        return {"action_result": {"status": "succeeded"}}

    g = StateGraph("retry_test")
    g.add_node("entry", NodeType.ENTRY, handler=_noop_handler)
    g.add_node("executor", NodeType.EXECUTOR, handler=executor_handler)
    async def recovery_handler(s):
        return {"recovery": {"retryable": True}}

    g.add_node("recovery", NodeType.RECOVERY, handler=recovery_handler)
    g.add_node("complete", NodeType.COMPLETE, handler=_noop_handler)

    from graph_definitions import _edge_success, _edge_error, _edge_retryable, _edge_task_succeeded

    g.add_edge("entry", "executor")
    g.add_edge("executor", "complete", condition=_edge_success, priority=10)
    g.add_edge("executor", "recovery", condition=_edge_error, priority=20)
    g.add_edge("recovery", "executor", condition=_edge_retryable)
    g.compile()

    executor = GraphExecutor(g)
    state = await executor.run({})
    assert state["_graph"]["terminal_node"] == "complete"
    assert attempt_count["n"] == 2
    visited = state["_graph"]["traversal"].visited_nodes
    assert visited.count("executor") == 2
    assert visited.count("recovery") == 1
