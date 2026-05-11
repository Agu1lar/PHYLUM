from nodes_base import BaseNode
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

class SafetyNode(BaseNode):
    async def validate(self, state: Dict[str, Any]) -> bool:
        # Validate high level policy: e.g., no network install without consent
        inputs = state.get("inputs", {})
        action = inputs.get("action", {})
        if action.get("type") == "install" and not inputs.get("allow_installs"):
            logger.warning("Install denied by policy")
            return False
        return True

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        # produce safety verdict and metadata
        verdict = {"ok": True, "reason": "policy passed"}
        return {"safety": verdict}

    async def verify(self, state: Dict[str, Any], result: Dict[str, Any]) -> bool:
        return result.get("safety", {}).get("ok", False)
