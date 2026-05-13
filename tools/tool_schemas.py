# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from pydantic import BaseModel
from typing import Optional, Any, Dict, List
from datetime import datetime


class ToolRequest(BaseModel):
    request_id: Optional[str]
    params: Dict[str, Any]
    timeout: Optional[int] = None
    retries: Optional[int] = None


class ToolResponse(BaseModel):
    ok: bool
    timestamp: datetime
    tool: str
    result: Optional[Dict[str, Any]]
    error: Optional[str]
    meta: Optional[Dict[str, Any]]
    raw: Optional[Dict[str, Any]]


class ToolResult(BaseModel):
    success: bool
    data: Optional[Dict[str, Any]]
    message: Optional[str]
    details: Optional[Dict[str, Any]]
