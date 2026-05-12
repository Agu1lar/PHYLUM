from __future__ import annotations

from typing import Any, Dict, Optional


class RecoveryEngine:
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
            return {
                "classification": "replan_required",
                "retryable": False,
                "needs_user": False,
                "suggested_action": "verify_outcome",
                "reason": goal.get("rationale") or "action requires additional verification",
                "recommended_followups": goal.get("recommended_followups") or [],
            }

        if issue_kind in {"ambiguous_match", "multiple_candidates"}:
            needs_user = bool(issue.get("candidates"))
            return {
                "classification": "needs_user" if needs_user else "replan_required",
                "retryable": False,
                "needs_user": needs_user,
                "suggested_action": "ask_user" if needs_user else "narrow_selector",
                "reason": issue_message or "multiple viable matches were found",
                "recommended_followups": ["windows_ui.find_element", "windows_ui.inspect_window"],
            }

        if issue_kind in {"stale_handle", "element_not_found", "window_not_found"}:
            retryable = attempt < max_attempts
            return {
                "classification": "retryable" if retryable else "replan_required",
                "retryable": retryable,
                "needs_user": False,
                "suggested_action": "retry" if retryable else "rediscover_target",
                "reason": issue_message or "UI target moved or disappeared",
                "recommended_followups": ["windows_ui.inspect_window", "desktop.list_windows"],
            }

        if issue_kind in {"locked_file", "inaccessible_share", "access_denied"}:
            retryable = attempt < max_attempts
            return {
                "classification": "retryable" if retryable else "needs_user",
                "retryable": retryable,
                "needs_user": not retryable,
                "suggested_action": "retry" if retryable else "ask_user",
                "reason": issue_message or "file or share access failed",
                "recommended_followups": ["share_discovery.inspect_share", "filesystem.stat"],
            }

        if issue_kind in {"office_com_unavailable", "browser_native_bridge_required"}:
            return {
                "classification": "replan_required",
                "retryable": False,
                "needs_user": False,
                "suggested_action": "switch_tooling",
                "reason": issue_message or "the current automation path is unavailable",
                "recommended_followups": ["tool_office", "tool_windows_ui", "browser.bridge_native_dialog"],
            }

        if status in {"blocked", "needs_input"}:
            return {
                "classification": "needs_user",
                "retryable": False,
                "needs_user": True,
                "suggested_action": "ask_user",
                "reason": summary,
                "recommended_followups": [],
            }

        retryable = issue_kind in {"timeout", "network", "tool_internal"} and attempt < max_attempts
        if not retryable and tool in {"shell", "browser", "web", "windows_ui"} and attempt < max_attempts:
            retryable = True
        return {
            "classification": "retryable" if retryable else "terminal",
            "retryable": retryable,
            "needs_user": False,
            "suggested_action": "retry" if retryable else "stop",
            "reason": summary,
            "recommended_followups": [],
        }

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
            return {
                "classification": "blocked_by_policy",
                "retryable": False,
                "needs_user": False,
                "suggested_action": "stop",
                "reason": "approval rejected",
            }

        if any(marker in message for marker in ["timeout", "temporarily", "connection reset", "network", "dns", "503", "502"]):
            retryable = attempt < max_attempts
            return {
                "classification": "retryable",
                "retryable": retryable,
                "needs_user": False,
                "suggested_action": "retry" if retryable else "stop",
                "reason": "transient failure detected",
            }

        if issue_kind in {"ambiguous_match", "missing_input", "approval_rejected"} or issue_user_action in {"provide_input", "select_candidate", "choose_alternative"}:
            return {
                "classification": "needs_user",
                "retryable": False,
                "needs_user": True,
                "suggested_action": "ask_user",
                "reason": issue.get("message") or "task needs clarification from the user",
            }

        if issue_kind in {"permission", "requires_confirmation", "blocked"}:
            return {
                "classification": "blocked_by_policy",
                "retryable": False,
                "needs_user": False,
                "suggested_action": "stop",
                "reason": issue.get("message") or "action blocked by policy",
            }

        retryable = issue_kind in {"timeout", "network", "tool_internal"} and attempt < max_attempts
        if issue_kind in {"stale_handle", "element_not_found", "window_not_found", "locked_file", "inaccessible_share"}:
            retryable = attempt < max_attempts
        if issue_kind in {"office_com_unavailable", "browser_native_bridge_required"}:
            return {
                "classification": "replan_required",
                "retryable": False,
                "needs_user": False,
                "suggested_action": "switch_tooling",
                "reason": issue.get("message") or "automation path unavailable",
            }
        if not retryable and not issue_kind and tool in {"shell", "browser", "package_manager", "web", "windows_ui"} and attempt < max_attempts:
            retryable = True
        return {
            "classification": "retryable" if retryable else "terminal",
            "retryable": retryable,
            "needs_user": False,
            "suggested_action": "retry" if retryable else "stop",
            "reason": "default recovery policy",
        }

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
