from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class WindowInfo(BaseModel):
    hwnd: int
    title: str
    pid: Optional[int] = None
    process_name: Optional[str] = None
    visible: bool = True


class ServiceInfo(BaseModel):
    name: str
    display_name: Optional[str] = None
    status: Optional[str] = None
    start_type: Optional[str] = None
    username: Optional[str] = None


class DesktopRequest(BaseModel):
    action: str = Field(
        ...,
        pattern='^(list_processes|list_windows|focus_window|clipboard_get|clipboard_set|notify|list_services|service_action)$',
    )
    hwnd: Optional[int] = None
    title: Optional[str] = None
    text: Optional[str] = None
    message: Optional[str] = None
    service_name: Optional[str] = None
    service_action: Optional[str] = None


class DesktopResponse(BaseModel):
    ok: bool
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
