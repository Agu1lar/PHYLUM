# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from nodes_base import BaseNode
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_meaningful_text(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return None

class ReflectionNode(BaseNode):
    async def validate(self, state: Dict[str, Any]) -> bool:
        return True

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        thinking = state.get("_last_thinking")
        if thinking:
            return self._reflection_from_thinking(state, thinking)

        task = state.get("current_task")
        task_result = state.get("current_task_result")
        task_error = state.get("current_task_error")
        if task_error:
            recovery = (task or {}).get("recovery") if task else None
            task_result_dict = _as_dict(task_result)
            action_result = _as_dict(task_result_dict.get("action_result"))
            issue = _as_dict(action_result.get("issue"))
            summary_text = _first_meaningful_text(
                str(task_error),
                issue.get("message"),
                task_result_dict.get("message"),
            )
            summary = {
                "verdict": "failed",
                "summary": summary_text or f"Task {task.get('id') if task else 'unknown'} failed",
                "details": {"error": str(task_error), "recovery": recovery},
                "recommended_action": recovery or {"action": "stop"},
            }
            return {"reflection": summary}

        verdict = "success"
        details: Dict[str, Any] = {}
        summary_text = None
        if isinstance(task_result, dict):
            action_result = _as_dict(task_result.get("action_result"))
            if action_result:
                action_status = action_result.get("status", "failed")
                verdict = "success" if action_status == "succeeded" else action_status
                details = {
                    "summary": action_result.get("summary"),
                    "target": action_result.get("target"),
                    "data": action_result.get("data"),
                    "effects": action_result.get("effects"),
                    "goal": action_result.get("goal"),
                    "issue": action_result.get("issue"),
                    "diagnostics": action_result.get("diagnostics"),
                }
                summary_text = action_result.get("summary")
            tool_result = _as_dict(task_result.get("tool_result"))
            if not action_result and "structured" in tool_result:
                structured = _as_dict(tool_result.get("structured"))
                command_result = _as_dict(structured.get("result"))
                verdict = "success" if structured.get("ok") else "failed"
                details = {
                    "stdout": command_result.get("stdout"),
                    "stderr": command_result.get("stderr"),
                    "risk": structured.get("risk"),
                    "error": structured.get("error"),
                }
                if verdict != "success":
                    summary_text = _first_meaningful_text(structured.get("error"), command_result.get("stderr"))
            elif not action_result and "success" in tool_result:
                verdict = "success" if tool_result.get("success") else "failed"
                details = _as_dict(tool_result.get("details"))
                if verdict != "success":
                    summary_text = _first_meaningful_text(
                        details.get("error"),
                        details.get("stderr"),
                        tool_result.get("message"),
                    )
            elif not action_result and "ok" in tool_result:
                verdict = "success" if tool_result.get("ok") else "failed"
                details = _as_dict(tool_result.get("details"))
                if verdict != "success":
                    summary_text = _first_meaningful_text(details.get("error"), details.get("stderr"))
        summary = {
            "verdict": verdict,
            "summary": summary_text or f"Task {task.get('id') if task else 'unknown'} completed with verdict {verdict}",
            "details": details,
            "recommended_action": None if verdict == "success" else ((task or {}).get("recovery") or {"action": "stop"}),
        }
        return {"reflection": summary}

    def _reflection_from_thinking(self, state: Dict[str, Any], thinking: str) -> Dict[str, Any]:
        """When extended thinking is available, derive the reflection from the
        model's own reasoning instead of re-evaluating the task result."""
        task = state.get("current_task")
        task_error = state.get("current_task_error")
        failed_keywords = ("failed", "error", "cannot", "unable", "not found", "denied")
        thinking_lower = thinking[:2000].lower()
        looks_failed = task_error or any(kw in thinking_lower for kw in failed_keywords)

        verdict = "failed" if looks_failed else "success"
        recovery = (task or {}).get("recovery") if task else None

        return {
            "reflection": {
                "verdict": verdict,
                "summary": thinking[:300],
                "details": {"source": "extended_thinking", "thinking_length": len(thinking)},
                "recommended_action": (recovery or {"action": "stop"}) if verdict != "success" else None,
            }
        }

    async def verify(self, state: Dict[str, Any], result: Dict[str, Any]) -> bool:
        return result.get("reflection", {}).get("verdict") == "success"
