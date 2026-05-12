from __future__ import annotations

import json
from typing import Any, Dict

from pydantic import BaseModel

from nodes_base import BaseNode
from tool_browser import BrowserTool
from tool_desktop import DesktopTool
from tool_driver import DriverManagerTool
from tool_env import EnvManagerTool
from tool_filesystem import FileSystemTool
from tool_memory import MemoryTool
from tool_os import OSIntrospectionTool
from tool_package import PackageManagerTool
from tool_shell import ShellTool
from tool_software import SoftwareInventoryTool
from tool_web import WebTool


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
        self.tools = {
            "shell": ShellTool(),
            "filesystem": FileSystemTool(),
            "memory": MemoryTool(),
            "browser": BrowserTool(),
            "web": WebTool(),
            "package_manager": PackageManagerTool(),
            "software_inventory": SoftwareInventoryTool(),
            "env_manager": EnvManagerTool(),
            "driver_manager": DriverManagerTool(),
            "os": OSIntrospectionTool(),
            "desktop": DesktopTool(),
        }

    async def validate(self, state: Dict[str, Any]) -> bool:
        task = state.get("current_task")
        return bool(task and task.get("tool") in self.tools)

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        task = state["current_task"]
        tool_name = task["tool"]
        tool = self.tools[tool_name]
        payload = dict(task.get("params", {}))
        if tool_name == "filesystem":
            payload["action"] = task["action"]
        elif tool_name == "memory":
            payload["action"] = task["action"]
        elif tool_name == "shell":
            payload.setdefault("shell", "powershell")
        elif tool_name in {"browser", "web", "package_manager", "software_inventory", "env_manager", "driver_manager", "os", "desktop"}:
            payload["action"] = task["action"]
        result = await tool.run(payload, cancel_event=state.get("cancel_event"))
        return {
            "tool": tool_name,
            "action": task["action"],
            "task_id": task["id"],
            "tool_result": _to_jsonable(result),
        }

    async def verify(self, state: Dict[str, Any], result: Dict[str, Any]) -> bool:
        tool_result = result.get("tool_result", {})
        if "structured" in tool_result:
            return tool_result["structured"].get("ok", False)
        if "ok" in tool_result:
            return bool(tool_result.get("ok"))
        return bool(tool_result.get("success", False))
