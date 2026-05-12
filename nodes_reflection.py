from nodes_base import BaseNode
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

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
        if isinstance(task_result, dict):
            tool_result = task_result.get("tool_result", {})
            if "structured" in tool_result:
                structured = tool_result["structured"]
                verdict = "success" if structured.get("ok") else "failed"
                details = {
                    "stdout": structured.get("result", {}).get("stdout"),
                    "stderr": structured.get("result", {}).get("stderr"),
                    "risk": structured.get("risk"),
                }
            elif "success" in tool_result:
                verdict = "success" if tool_result.get("success") else "failed"
                details = tool_result.get("details") or {}
            elif "ok" in tool_result:
                verdict = "success" if tool_result.get("ok") else "failed"
                details = tool_result.get("details") or {}
        summary = {
            "verdict": verdict,
            "summary": f"Task {task.get('id') if task else 'unknown'} completed with verdict {verdict}",
            "details": details,
            "recommended_action": None if verdict == "success" else ((task or {}).get("recovery") or {"action": "stop"}),
        }
        return {"reflection": summary}

    async def verify(self, state: Dict[str, Any], result: Dict[str, Any]) -> bool:
        return result.get("reflection", {}).get("verdict") == "success"
