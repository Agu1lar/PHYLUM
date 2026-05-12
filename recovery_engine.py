from __future__ import annotations

from typing import Any, Dict, Optional


class RecoveryEngine:
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

        if tool in {"browser", "web", "driver_manager", "software_inventory"} and any(
            marker in message for marker in ["not found", "no results", "multiple", "choose", "select"]
        ):
            return {
                "classification": "needs_user",
                "retryable": False,
                "needs_user": True,
                "suggested_action": "ask_user",
                "reason": "task needs clarification from the user",
            }

        if tool == "desktop" and action == "focus_window" and "window not found" in message:
            return {
                "classification": "needs_user",
                "retryable": False,
                "needs_user": True,
                "suggested_action": "ask_user",
                "reason": "window title needs clarification",
            }

        retryable_tools = {"shell", "browser", "package_manager", "web"}
        retryable = tool in retryable_tools and attempt < max_attempts
        return {
            "classification": "retryable" if retryable else "terminal",
            "retryable": retryable,
            "needs_user": False,
            "suggested_action": "retry" if retryable else "stop",
            "reason": "default recovery policy",
        }

    def question_for_failure(self, task: Dict[str, Any], error: str) -> Dict[str, Any]:
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
