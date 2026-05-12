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
        task = state.get("current_task")
        task_result = state.get("current_task_result")
        task_error = state.get("current_task_error")
        if task_error:
            recovery = (task or {}).get("recovery") if task else None
            summary = {
                "verdict": "failed",
                "summary": f"Task {task.get('id') if task else 'unknown'} failed",
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

    async def verify(self, state: Dict[str, Any], result: Dict[str, Any]) -> bool:
        return result.get("reflection", {}).get("verdict") == "success"
