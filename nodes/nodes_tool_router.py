# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import json
from typing import Any, Dict

from pydantic import BaseModel

from nodes_base import BaseNode
from action_models import action_succeeded
from tool_registry import ToolRegistry


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return json.loads(value.json())
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    return value


class ToolRouterNode(BaseNode):
    def __init__(self, node_id: str = "tool_router"):
        super().__init__(node_id)
        self.registry = ToolRegistry()
        self.tools = self.registry.tools

    async def validate(self, state: Dict[str, Any]) -> bool:
        task = state.get("current_task")
        return bool(task and self.registry.supports(task.get("tool")))

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        task = state["current_task"]
        return await self.registry.execute(task, cancel_event=state.get("cancel_event"))

    async def verify(self, state: Dict[str, Any], result: Dict[str, Any]) -> bool:
        action_result = result.get("action_result", {})
        return action_succeeded(action_result)
