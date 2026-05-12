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


class WindowsUiRequest(BaseModel):
    action: str = Field(
        ...,
        pattern="^(inspect_window|list_elements|find_element|wait_for_element|invoke_element|set_text|select_item|send_hotkey|scroll|read_element_text|get_focused_element)$",
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

