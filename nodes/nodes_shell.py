# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from nodes_base import BaseNode
from typing import Dict, Any
import asyncio
import logging
from agent_tool_call import run_shell

logger = logging.getLogger(__name__)

class ShellNode(BaseNode):
    async def validate(self, state: Dict[str, Any]) -> bool:
        cmd = state.get("inputs", {}).get("command")
        return bool(cmd)

    async def execute(self, state: Dict[str, Any]) -> Dict[str, Any]:
        cmd = state["inputs"].get("command")
        res = await run_shell(cmd, timeout=self.config.timeout, retries=self.config.retries)
        return {"shell": res}

    async def verify(self, state: Dict[str, Any], result: Dict[str, Any]) -> bool:
        return result.get("shell", {}).get("returncode", 1) == 0

    async def rollback(self, state: Dict[str, Any], result: Dict[str, Any]) -> None:
        rb = state.get("inputs", {}).get("rollback_command")
        if rb:
            await run_shell(rb, timeout=10, retries=1)
