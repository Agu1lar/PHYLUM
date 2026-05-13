# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from nodes_base import BaseNode
from typing import Dict, Any
import logging
from policy_engine import PolicyEngine

logger = logging.getLogger(__name__)

class SafetyNode(BaseNode):
    def __init__(self, node_id: str = "safety"):
        super().__init__(node_id)
        self.policy = PolicyEngine()

    async def validate(self, state: Dict[str, Any]) -> bool:
        return bool(state.get("current_task"))

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return {"safety": self.policy.evaluate(state)}

    async def verify(self, state: Dict[str, Any], result: Dict[str, Any]) -> bool:
        return result.get("safety", {}).get("status") != "deny"
