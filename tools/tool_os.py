# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from os_inspect_agent import introspect
from tool_base import BaseTool

logger = logging.getLogger(__name__)


class OSInput(BaseModel):
    action: str = Field(..., pattern='^(overview|apps|processes|full)$')


class OSOutput(BaseModel):
    success: bool
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class OSIntrospectionTool(BaseTool):
    InputModel = OSInput
    OutputModel = OSOutput

    async def _run(self, payload: OSInput) -> OSOutput:
        result = await introspect(full=payload.action == "full")
        data = result.dict()
        if payload.action == "overview":
            details: Dict[str, Any] = {"overview": data.get("overview"), "permissions": data.get("permissions")}
        elif payload.action == "apps":
            details = {"overview": data.get("overview"), "apps": data.get("apps")}
        elif payload.action == "processes":
            details = {"overview": data.get("overview"), "processes": data.get("processes")}
        else:
            details = data
        return OSOutput(success=True, message=payload.action, details=details)
