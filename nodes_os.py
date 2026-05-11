from nodes_base import BaseNode
from typing import Dict, Any
import platform
import logging

logger = logging.getLogger(__name__)

class OSNode(BaseNode):
    async def validate(self, state: Dict[str, Any]) -> bool:
        return True

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        return {"os": platform.system(), "release": platform.release()}

    async def verify(self, state: Dict[str, Any], result: Dict[str, Any]) -> bool:
        return True
