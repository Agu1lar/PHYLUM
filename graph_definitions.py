"""Concrete graph topologies for the agent runtime pipelines.

Replaces the linear _run_agentic_pipeline, _run_local_heuristic_pipeline and
_run_manual_assist_pipeline with explicit state graphs whose nodes correspond
to existing components (planner, safety, tool_router, reflection, recovery).

Each build_* function returns a (StateGraph, Dict[str, NodeHandler]) pair.
The RuntimeManager wires the handlers at construction time so the graph is
self-contained and the executor can walk it without knowing about the manager.
"""
from __future__ import annotations

from state_graph import NodeType, StateGraph


def _edge_success(state, result):
    return not result.get("_error")


def _edge_error(state, result):
    return bool(result.get("_error"))


def _edge_has_tasks(state, result):
    return bool(result.get("tasks"))


def _edge_no_tasks(state, result):
    return not result.get("tasks")


def _edge_denied(state, result):
    safety = result.get("safety") or {}
    return safety.get("status") == "deny"


def _edge_needs_approval(state, result):
    safety = result.get("safety") or {}
    return safety.get("status") == "require_approval"


def _edge_allowed(state, result):
    safety = result.get("safety") or {}
    return safety.get("status") not in ("deny", "require_approval")


def _edge_approval_granted(state, result):
    return result.get("approval_status") == "approved"


def _edge_approval_rejected(state, result):
    return result.get("approval_status") in ("rejected", "cancelled")


def _edge_task_succeeded(state, result):
    ar = (result.get("action_result") or {})
    return ar.get("status") == "succeeded"


def _edge_task_partial(state, result):
    ar = (result.get("action_result") or {})
    return ar.get("status") in ("partial", "needs_input", "blocked")


def _edge_task_failed(state, result):
    return bool(result.get("_error")) or (result.get("action_result") or {}).get("status") in ("failed", None)


def _edge_retryable(state, result):
    recovery = result.get("recovery") or state.get("recovery") or {}
    return recovery.get("retryable", False)


def _edge_needs_user(state, result):
    recovery = result.get("recovery") or state.get("recovery") or {}
    return recovery.get("needs_user", False)


def _edge_script_recovery(state, result):
    recovery = result.get("recovery") or state.get("recovery") or {}
    return recovery.get("classification") == "script_recovery" and bool(recovery.get("script"))


def _edge_replan_required(state, result):
    recovery = result.get("recovery") or state.get("recovery") or {}
    return recovery.get("classification") == "replan_required"


def _edge_terminal_failure(state, result):
    recovery = result.get("recovery") or state.get("recovery") or {}
    return recovery.get("classification") in ("terminal", "blocked_by_policy") or (
        not recovery.get("retryable") and not recovery.get("needs_user") and recovery.get("classification") != "script_recovery"
    )


def _edge_always(state, result):
    return True


# ─── Local heuristic pipeline graph ────────────────────────────────


def build_local_graph() -> StateGraph:
    """Graph for the local heuristic pipeline (plan -> execute each task -> reflect).

    Nodes:
      entry -> planner -> task_picker -> safety -> (approval?) -> executor -> reflection -> recovery -> ...
    """
    g = StateGraph("local_heuristic")

    g.add_node("entry", NodeType.ENTRY)
    g.add_node("planner", NodeType.PLANNER)
    g.add_node("plan_empty", NodeType.COMPLETE)
    g.add_node("task_picker", NodeType.ROUTER)
    g.add_node("safety", NodeType.SAFETY)
    g.add_node("approval", NodeType.APPROVAL)
    g.add_node("executor", NodeType.EXECUTOR)
    g.add_node("reflection", NodeType.REFLECTION)
    g.add_node("recovery", NodeType.RECOVERY)
    g.add_node("script_recovery", NodeType.SCRIPT_RECOVERY)
    g.add_node("checkpoint", NodeType.CHECKPOINT)
    g.add_node("handoff", NodeType.HANDOFF)
    g.add_node("complete", NodeType.COMPLETE)
    g.add_node("fail", NodeType.FAIL)

    g.add_edge("entry", "planner", label="start")
    g.add_edge("planner", "plan_empty", condition=_edge_no_tasks, priority=10, label="no tasks")
    g.add_edge("planner", "task_picker", condition=_edge_has_tasks, priority=20, label="has tasks")
    g.add_edge("planner", "fail", condition=_edge_error, priority=5, label="planner error")

    g.add_edge("task_picker", "complete", condition=_edge_no_tasks, priority=10, label="all done")
    g.add_edge("task_picker", "safety", condition=_edge_has_tasks, priority=20, label="next task")

    g.add_edge("safety", "fail", condition=_edge_denied, priority=10, label="denied")
    g.add_edge("safety", "approval", condition=_edge_needs_approval, priority=20, label="needs approval")
    g.add_edge("safety", "executor", condition=_edge_allowed, priority=30, label="allowed")

    g.add_edge("approval", "executor", condition=_edge_approval_granted, priority=10, label="approved")
    g.add_edge("approval", "fail", condition=_edge_approval_rejected, priority=20, label="rejected")

    g.add_edge("executor", "reflection", condition=_edge_success, priority=10, label="executed")
    g.add_edge("executor", "recovery", condition=_edge_error, priority=20, label="execution failed")

    g.add_edge("reflection", "checkpoint", condition=_edge_task_succeeded, priority=10, label="succeeded")
    g.add_edge("reflection", "recovery", condition=_edge_task_partial, priority=20, label="partial/blocked")
    g.add_edge("reflection", "recovery", condition=_edge_task_failed, priority=30, label="task failed")
    g.add_edge("reflection", "checkpoint", condition=_edge_always, priority=99, label="fallback to checkpoint")

    g.add_edge("checkpoint", "task_picker", label="next task")

    g.add_edge("recovery", "executor", condition=_edge_retryable, priority=10, label="retry")
    g.add_edge("recovery", "script_recovery", condition=_edge_script_recovery, priority=15, label="script recovery")
    g.add_edge("recovery", "handoff", condition=_edge_needs_user, priority=20, label="ask user")
    g.add_edge("recovery", "planner", condition=_edge_replan_required, priority=25, label="replan")
    g.add_edge("recovery", "fail", condition=_edge_terminal_failure, priority=90, label="terminal failure")
    g.add_edge("recovery", "fail", condition=_edge_always, priority=99, label="recovery fallback")

    g.add_edge("script_recovery", "checkpoint", condition=_edge_success, priority=10, label="script succeeded")
    g.add_edge("script_recovery", "fail", condition=_edge_error, priority=20, label="script failed")
    g.add_edge("script_recovery", "fail", condition=_edge_always, priority=99, label="script fallback")

    return g.compile()


# ─── Agentic pipeline graph ────────────────────────────────────────


def build_agentic_graph() -> StateGraph:
    """Graph for the agentic (LLM-driven) pipeline.

    The LLM loop is treated as a single mega-node that internally calls
    task_factory / execute_task.  The graph handles the top-level flow:
    entry -> llm_loop -> (complete | handoff | recovery | fail)
    """
    g = StateGraph("agentic")

    g.add_node("entry", NodeType.ENTRY)
    g.add_node("provider_check", NodeType.ROUTER)
    g.add_node("llm_loop", NodeType.EXECUTOR)
    g.add_node("reflection", NodeType.REFLECTION)
    g.add_node("recovery", NodeType.RECOVERY)
    g.add_node("script_recovery", NodeType.SCRIPT_RECOVERY)
    g.add_node("handoff", NodeType.HANDOFF)
    g.add_node("complete", NodeType.COMPLETE)
    g.add_node("fail", NodeType.FAIL)
    g.add_node("fallback_manual", NodeType.PLANNER)

    g.add_edge("entry", "provider_check", label="start")

    def _has_provider(state, result):
        return bool(state.get("provider"))

    def _no_provider(state, result):
        return not state.get("provider")

    g.add_edge("provider_check", "llm_loop", condition=_has_provider, priority=10, label="has provider")
    g.add_edge("provider_check", "fallback_manual", condition=_no_provider, priority=20, label="no provider")
    g.add_edge("fallback_manual", "complete", label="manual assist done")

    def _llm_completed(state, result):
        return result.get("status") == "completed"

    def _llm_handoff(state, result):
        return result.get("status") == "awaiting_input"

    def _llm_error(state, result):
        return bool(result.get("_error")) or result.get("status") in ("failed", None)

    g.add_edge("llm_loop", "reflection", condition=_llm_completed, priority=10, label="LLM done")
    g.add_edge("llm_loop", "handoff", condition=_llm_handoff, priority=20, label="needs user input")
    g.add_edge("llm_loop", "recovery", condition=_llm_error, priority=30, label="LLM error")

    g.add_edge("reflection", "complete", condition=_edge_success, priority=10, label="success")
    g.add_edge("reflection", "recovery", condition=_edge_error, priority=20, label="reflection error")

    g.add_edge("recovery", "llm_loop", condition=_edge_retryable, priority=10, label="retry LLM")
    g.add_edge("recovery", "script_recovery", condition=_edge_script_recovery, priority=15, label="script recovery")
    g.add_edge("recovery", "handoff", condition=_edge_needs_user, priority=20, label="ask user")
    g.add_edge("recovery", "fail", condition=_edge_terminal_failure, priority=90, label="terminal")
    g.add_edge("recovery", "fail", condition=_edge_always, priority=99, label="recovery fallback")

    g.add_edge("script_recovery", "complete", condition=_edge_success, priority=10, label="script ok")
    g.add_edge("script_recovery", "fail", condition=_edge_always, priority=99, label="script fail")

    return g.compile()


# ─── Manual assist pipeline graph ──────────────────────────────────


def build_manual_graph() -> StateGraph:
    """Graph for the manual assist pipeline (plan -> present tasks -> complete)."""
    g = StateGraph("manual_assist")

    g.add_node("entry", NodeType.ENTRY)
    g.add_node("planner", NodeType.PLANNER)
    g.add_node("present", NodeType.REFLECTION)
    g.add_node("complete", NodeType.COMPLETE)
    g.add_node("fail", NodeType.FAIL)

    g.add_edge("entry", "planner", label="start")
    g.add_edge("planner", "complete", condition=_edge_no_tasks, priority=10, label="no tasks")
    g.add_edge("planner", "present", condition=_edge_has_tasks, priority=20, label="has tasks")
    g.add_edge("planner", "fail", condition=_edge_error, priority=5, label="error")
    g.add_edge("present", "complete", label="presented to user")

    return g.compile()
