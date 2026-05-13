# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class WindowsUiSelector(BaseModel):
    title: Optional[str] = None
    control_type: Optional[str] = None
    auto_id: Optional[str] = None
    class_name: Optional[str] = None
    process_name: Optional[str] = None
    hwnd: Optional[int] = None
    parent_title: Optional[str] = None
    parent_control_type: Optional[str] = None
    ancestor_titles: List[str] = Field(default_factory=list)
    sibling_titles: List[str] = Field(default_factory=list)
    near_title: Optional[str] = None
    index: Optional[int] = None
    exact_title: Optional[bool] = None


class WindowsUiElement(BaseModel):
    element_id: str
    title: Optional[str] = None
    control_type: Optional[str] = None
    auto_id: Optional[str] = None
    class_name: Optional[str] = None
    hwnd: Optional[int] = None
    process_id: Optional[int] = None
    process_name: Optional[str] = None
    enabled: Optional[bool] = None
    visible: Optional[bool] = None
    rectangle: Dict[str, int] = Field(default_factory=dict)
    parent: Dict[str, Any] = Field(default_factory=dict)
    ancestors: List[Dict[str, Any]] = Field(default_factory=list)
    siblings: List[Dict[str, Any]] = Field(default_factory=list)
    selector: Dict[str, Any] = Field(default_factory=dict)
    match_score: Optional[float] = None
    match_reasons: List[str] = Field(default_factory=list)


class WindowsUiRequest(BaseModel):
    action: str = Field(
        ...,
        pattern="^(inspect_window|inspect_dialog|list_elements|find_element|wait_for_element|invoke_element|set_text|select_item|send_hotkey|scroll|read_element_text|get_focused_element)$",
    )
    hwnd: Optional[int] = None
    title: Optional[str] = None
    process_name: Optional[str] = None
    selector: Optional[Dict[str, Any]] = None
    element_id: Optional[str] = None
    text: Optional[str] = None
    item_text: Optional[str] = None
    hotkey: Optional[str] = None
    direction: Optional[str] = None
    amount: Optional[int] = None
    timeout_seconds: Optional[int] = None
    max_results: Optional[int] = None
    include_children: bool = Field(default=True)


class WindowsUiResponse(BaseModel):
    ok: bool
    message: str
    details: Dict[str, Any] = Field(default_factory=dict)

