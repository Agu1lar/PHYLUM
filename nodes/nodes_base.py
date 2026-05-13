# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from typing import Any, Dict, Optional
import asyncio
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)

class NodeConfig(BaseModel):
    retries: int = 2
    timeout: int = 30


class BaseNode:
    def __init__(self, node_id: str, config: Optional[NodeConfig] = None):
        self.node_id = node_id
        self.config = config or NodeConfig()

    async def validate(self, state: Dict[str, Any]) -> bool:
        return True

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError()

    async def verify(self, state: Dict[str, Any], result: Dict[str, Any]) -> bool:
        return True

    async def rollback(self, state: Dict[str, Any], result: Dict[str, Any]) -> None:
        logger.info("%s rollback invoked", self.node_id)
        return None

    def summary(self) -> str:
        return f"Node({self.node_id})"
