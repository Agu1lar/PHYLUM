# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

from execution_strategy import ExecutionStrategy

if TYPE_CHECKING:
    from state_graph import GraphExecutor


_SUGGESTED_ACTION_TO_TARGET_NODE = {
    "retry": "executor",
    "execute_script": "script_recovery",
    "rediscover_target": "planner",
    "replan": "planner",
    "switch_tooling": "planner",
    "verify_outcome": "reflection",
    "ask_user": "handoff",
    "narrow_selector": "executor",
    "stop": "fail",
}


class RecoveryEngine:
    def __init__(self):
        self.execution_strategy = ExecutionStrategy()

    def _attach_target_node(self, classification: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich a recovery classification with a target_node hint for graph traversal."""
        suggested = classification.get("suggested_action", "")
        if "target_node" not in classification:
            classification["target_node"] = _SUGGESTED_ACTION_TO_TARGET_NODE.get(suggested)
        return classification

    def resolve_graph_target(
        self,
        state: Dict[str, Any],
        classification: Dict[str, Any],
        graph_executor: Optional[GraphExecutor] = None,
    ) -> Optional[str]:
        """Determine the exact graph node to jump to for recovery.

        Uses the graph executor's find_recovery_target if available,
        otherwise falls back to the target_node hint in the classification.
        """
        if graph_executor:
            target = graph_executor.find_recovery_target(state, classification)
            if target:
                return target
        return classification.get("target_node")
    def classify_action_result(
        self,
        *,
        task: Dict[str, Any],
        action_result: Dict[str, Any],
        attempt: int,
        max_attempts: int = 2,
    ) -> Dict[str, Any]:
        issue = action_result.get("issue") or {}
        issue_kind = str(issue.get("kind") or "").lower()
        issue_message = str(issue.get("message") or "")
        summary = str(action_result.get("summary") or issue_message or "task needs follow-up")
        status = str(action_result.get("status") or "failed").lower()
        tool = str(task.get("tool") or "")
        action = str(task.get("action") or "")
        goal = action_result.get("goal") or {}

        if status == "partial" and goal and not goal.get("satisfied", True):
            return self._attach_target_node({
                "classification": "replan_required",
                "retryable": False,
                "needs_user": False,
                "suggested_action": "verify_outcome",
                "reason": goal.get("rationale") or "action requires additional verification",
                "recommended_followups": goal.get("recommended_followups") or [],
            })

        if issue_kind in {"ambiguous_match", "multiple_candidates"}:
            needs_user = bool(issue.get("candidates"))
            return self._attach_target_node({
                "classification": "needs_user" if needs_user else "replan_required",
                "retryable": False,
                "needs_user": needs_user,
                "suggested_action": "ask_user" if needs_user else "narrow_selector",
                "reason": issue_message or "multiple viable matches were found",
                "recommended_followups": ["windows_ui.find_element", "windows_ui.inspect_window"],
            })

        if issue_kind in {"stale_handle", "element_not_found", "window_not_found"}:
            retryable = attempt < max_attempts
            return self._attach_target_node({
                "classification": "retryable" if retryable else "replan_required",
                "retryable": retryable,
                "needs_user": False,
                "suggested_action": "retry" if retryable else "rediscover_target",
                "reason": issue_message or "UI target moved or disappeared",
                "recommended_followups": ["windows_ui.inspect_window", "desktop.list_windows"],
            })

        if issue_kind in {"locked_file", "inaccessible_share", "access_denied"}:
            retryable = attempt < max_attempts
            return self._attach_target_node({
                "classification": "retryable" if retryable else "needs_user",
                "retryable": retryable,
                "needs_user": not retryable,
                "suggested_action": "retry" if retryable else "ask_user",
                "reason": issue_message or "file or share access failed",
                "recommended_followups": ["share_discovery.inspect_share", "filesystem.stat"],
            })

        if issue_kind in {"office_com_unavailable", "browser_native_bridge_required"}:
            return self._attach_target_node({
                "classification": "replan_required",
                "retryable": False,
                "needs_user": False,
                "suggested_action": "switch_tooling",
                "reason": issue_message or "the current automation path is unavailable",
                "recommended_followups": ["tool_office", "tool_windows_ui", "browser.bridge_native_dialog"],
            })

        if status in {"blocked", "needs_input"}:
            return self._attach_target_node({
                "classification": "needs_user",
                "retryable": False,
                "needs_user": True,
                "suggested_action": "ask_user",
                "reason": summary,
                "recommended_followups": [],
            })

        retryable = issue_kind in {"timeout", "network", "tool_internal"} and attempt < max_attempts
        if not retryable and tool in {"shell", "browser", "web", "windows_ui"} and attempt < max_attempts:
            retryable = True
        if not retryable:
            script_recovery = self.execution_strategy.suggest_script_recovery(
                task=task, error=summary, attempt=attempt,
            )
            if script_recovery:
                script_recovery["recommended_followups"] = []
                return self._attach_target_node(script_recovery)
        return self._attach_target_node({
            "classification": "retryable" if retryable else "terminal",
            "retryable": retryable,
            "needs_user": False,
            "suggested_action": "retry" if retryable else "stop",
            "reason": summary,
            "recommended_followups": [],
        })

    def classify(
        self,
        *,
        task: Dict[str, Any],
        error: str,
        attempt: int,
        max_attempts: int = 2,
    ) -> Dict[str, Any]:
        message = (error or "").lower()
        tool = task.get("tool")
        action = task.get("action")
        action_result = ((task.get("result") or {}).get("action_result") or {})
        issue = action_result.get("issue") or {}
        issue_kind = str(issue.get("kind") or "").lower()
        issue_message = str(issue.get("message") or "").lower()
        issue_user_action = str(issue.get("user_action_required") or "").lower()

        if "approval rejected" in message:
            return self._attach_target_node({
                "classification": "blocked_by_policy",
                "retryable": False,
                "needs_user": False,
                "suggested_action": "stop",
                "reason": "approval rejected",
            })

        if any(marker in message for marker in ["timeout", "temporarily", "connection reset", "network", "dns", "503", "502"]):
            retryable = attempt < max_attempts
            return self._attach_target_node({
                "classification": "retryable",
                "retryable": retryable,
                "needs_user": False,
                "suggested_action": "retry" if retryable else "stop",
                "reason": "transient failure detected",
            })

        if issue_kind in {"ambiguous_match", "missing_input", "approval_rejected"} or issue_user_action in {"provide_input", "select_candidate", "choose_alternative"}:
            return self._attach_target_node({
                "classification": "needs_user",
                "retryable": False,
                "needs_user": True,
                "suggested_action": "ask_user",
                "reason": issue.get("message") or "task needs clarification from the user",
            })

        if issue_kind in {"permission", "requires_confirmation", "blocked"}:
            return self._attach_target_node({
                "classification": "blocked_by_policy",
                "retryable": False,
                "needs_user": False,
                "suggested_action": "stop",
                "reason": issue.get("message") or "action blocked by policy",
            })

        retryable = issue_kind in {"timeout", "network", "tool_internal"} and attempt < max_attempts
        if issue_kind in {"stale_handle", "element_not_found", "window_not_found", "locked_file", "inaccessible_share"}:
            retryable = attempt < max_attempts
        if issue_kind in {"office_com_unavailable", "browser_native_bridge_required"}:
            return self._attach_target_node({
                "classification": "replan_required",
                "retryable": False,
                "needs_user": False,
                "suggested_action": "switch_tooling",
                "reason": issue.get("message") or "automation path unavailable",
            })
        if not retryable and not issue_kind and tool in {"shell", "browser", "package_manager", "web", "windows_ui"} and attempt < max_attempts:
            retryable = True
        if not retryable:
            script_recovery = self.execution_strategy.suggest_script_recovery(
                task=task, error=error, attempt=attempt,
            )
            if script_recovery:
                return self._attach_target_node(script_recovery)
        return self._attach_target_node({
            "classification": "retryable" if retryable else "terminal",
            "retryable": retryable,
            "needs_user": False,
            "suggested_action": "retry" if retryable else "stop",
            "reason": "default recovery policy",
        })

    def question_for_failure(self, task: Dict[str, Any], error: str) -> Dict[str, Any]:
        issue = (((task.get("result") or {}).get("action_result") or {}).get("issue") or {})
        candidates = issue.get("candidates") or []
        missing_fields = issue.get("missing_fields") or []
        if candidates:
            return {
                "kind": "recovery_question",
                "title": f"Escolha como continuar: {task.get('title', task.get('id', 'task'))}",
                "prompt": issue.get("message") or "Encontrei mais de uma opcao possivel. Escolha uma delas para eu continuar.",
                "allow_free_text": True,
                "options": [
                    {"id": str(index), "label": item.get("display_name") or item.get("id") or f"Opcao {index + 1}", "value": item}
                    for index, item in enumerate(candidates)
                ] + [{"id": "abort", "label": "Abortar esta run", "value": "abort"}],
            }
        if missing_fields:
            fields_text = ", ".join(str(field) for field in missing_fields)
            return {
                "kind": "recovery_question",
                "title": f"Preciso de mais dados para {task.get('title', task.get('id', 'task'))}",
                "prompt": issue.get("message") or f"Informe os dados faltantes para continuar: {fields_text}.",
                "allow_free_text": True,
                "options": [
                    {"id": "provide_input", "label": "Vou informar os dados", "value": "clarify"},
                    {"id": "abort", "label": "Abortar esta run", "value": "abort"},
                ],
            }
        title = f"Continuar task: {task.get('title', task.get('id', 'task'))}"
        prompt = (
            f"A task falhou com o erro: {error}. "
            "Se quiser, me diga mais contexto, confirme um alvo especifico ou escolha como devo continuar."
        )
        return {
            "kind": "recovery_question",
            "title": title,
            "prompt": prompt,
            "allow_free_text": True,
            "options": [
                {"id": "retry", "label": "Tentar novamente", "value": "retry"},
                {"id": "change_target", "label": "Vou informar mais contexto", "value": "clarify"},
                {"id": "abort", "label": "Abortar esta run", "value": "abort"},
            ],
        }
