# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class WindowInfo(BaseModel):
    hwnd: int
    title: str
    pid: Optional[int] = None
    process_name: Optional[str] = None
    visible: bool = True


class ExplorerWindowInfo(BaseModel):
    hwnd: int
    title: str
    process_name: Optional[str] = None
    visible: bool = True
    location_path: Optional[str] = None
    location_url: Optional[str] = None
    executable_path: Optional[str] = None
    selected_items: List[str] = Field(default_factory=list)


class ServiceInfo(BaseModel):
    name: str
    display_name: Optional[str] = None
    status: Optional[str] = None
    start_type: Optional[str] = None
    username: Optional[str] = None


class DesktopRequest(BaseModel):
    action: str = Field(
        ...,
        pattern='^(list_processes|list_windows|list_explorer_windows|list_mapped_drives|get_explorer_selection|explorer_context|explorer_select_path|explorer_navigate|explorer_rename_path|explorer_copy_path|explorer_move_path|inspect_installer|open_app|open_path|open_file|wait_for_window|focus_window|close_window|kill_process|clipboard_get|clipboard_set|notify|list_services|service_action)$',
    )
    hwnd: Optional[int] = None
    title: Optional[str] = None
    text: Optional[str] = None
    message: Optional[str] = None
    path: Optional[str] = None
    dest: Optional[str] = None
    new_name: Optional[str] = None
    app_name: Optional[str] = None
    app_path: Optional[str] = None
    arguments: Optional[List[str]] = None
    process_name: Optional[str] = None
    pid: Optional[int] = None
    timeout_seconds: Optional[int] = None
    service_name: Optional[str] = None
    service_action: Optional[str] = None


class DesktopResponse(BaseModel):
    ok: bool
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
