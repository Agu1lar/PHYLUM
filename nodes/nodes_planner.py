from nodes_base import BaseNode
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

class PlannerNode(BaseNode):
    async def validate(self, state: Dict[str, Any]) -> bool:
        # ensure inputs present
        return "action" in state.get("inputs", {})

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        action = state["inputs"].get("action", {})
        # simple planning: produce plan steps based on action type
        plan = {"steps": []}
        if action.get("type") == "install":
            plan["steps"] = ["safety", "shell", "filesystem", "reflection"]
        else:
            plan["steps"] = ["safety", "reflection"]
        logger.info("Planned steps: %s", plan["steps"])
        return {"plan": plan}

    async def verify(self, state: Dict[str, Any], result: Dict[str, Any]) -> bool:
        return "plan" in result
