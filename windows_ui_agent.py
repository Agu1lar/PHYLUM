from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import psutil

from windows_ui_models import WindowsUiElement, WindowsUiSelector

logger = logging.getLogger(__name__)

try:  # pragma: no cover - optional dependency
    from pywinauto import Desktop
    from pywinauto.keyboard import send_keys
except Exception:  # pragma: no cover - optional dependency
    Desktop = None
    send_keys = None


class WindowsUiUnavailable(RuntimeError):
    pass


def _ensure_backend() -> None:
    if Desktop is None:
        raise WindowsUiUnavailable(
            "Windows UI Automation backend is unavailable. Install pywinauto to enable native UI automation."
        )


def _safe_rect(wrapper) -> Dict[str, int]:
    try:
        rect = wrapper.rectangle()
        return {"left": int(rect.left), "top": int(rect.top), "right": int(rect.right), "bottom": int(rect.bottom)}
    except Exception:
        return {}


def _safe_process_name(process_id: Optional[int]) -> Optional[str]:
    if not process_id:
        return None
    try:
        return psutil.Process(process_id).name()
    except Exception:
        return None


class WindowsUiAgent:
    def __init__(self):
        self._element_cache: Dict[str, Dict[str, Any]] = {}

    def _desktop(self):
        _ensure_backend()
        return Desktop(backend="uia")

    def _snapshot(self, wrapper) -> WindowsUiElement:
        info = getattr(wrapper, "element_info", None)
        process_id = getattr(info, "process_id", None)
        automation_id = getattr(info, "automation_id", None) or None
        class_name = getattr(info, "class_name", None) or None
        title = getattr(info, "name", None) or None
        control_type = getattr(info, "control_type", None) or None
        handle = getattr(info, "handle", None)
        element_id = "|".join(
            str(part)
            for part in (
                handle or "no-hwnd",
                process_id or "no-pid",
                control_type or "no-ctrl",
                automation_id or "no-autoid",
                title or "no-title",
            )
        )
        element = WindowsUiElement(
            element_id=element_id,
            title=title,
            control_type=control_type,
            auto_id=automation_id,
            class_name=class_name,
            hwnd=int(handle) if handle else None,
            process_id=int(process_id) if process_id else None,
            process_name=_safe_process_name(process_id),
            enabled=bool(wrapper.is_enabled()) if hasattr(wrapper, "is_enabled") else None,
            visible=bool(wrapper.is_visible()) if hasattr(wrapper, "is_visible") else None,
            rectangle=_safe_rect(wrapper),
        )
        self._element_cache[element_id] = {
            "hwnd": element.hwnd,
            "title": element.title,
            "process_name": element.process_name,
            "selector": {
                "title": element.title,
                "control_type": element.control_type,
                "auto_id": element.auto_id,
                "class_name": element.class_name,
                "process_name": element.process_name,
                "hwnd": element.hwnd,
            },
        }
        return element

    def _all_windows(self):
        return list(self._desktop().windows())

    def _resolve_window(self, *, hwnd: Optional[int] = None, title: Optional[str] = None, process_name: Optional[str] = None):
        windows = self._all_windows()
        if hwnd is not None:
            matches = [window for window in windows if getattr(window.element_info, "handle", None) == hwnd]
        else:
            matches = windows
            if title:
                lowered = title.lower()
                matches = [window for window in matches if lowered in str(getattr(window.element_info, "name", "")).lower()]
            if process_name:
                lowered_process = process_name.lower()
                matches = [
                    window
                    for window in matches
                    if lowered_process in str(_safe_process_name(getattr(window.element_info, "process_id", None)) or "").lower()
                ]
        if not matches:
            raise ValueError("window not found")
        return matches[0]

    def _candidate_wrappers(self, window_wrapper, *, include_children: bool = True) -> List[Any]:
        candidates = [window_wrapper]
        if include_children:
            try:
                candidates.extend(window_wrapper.descendants())
            except Exception:
                logger.exception("failed to enumerate descendants for window")
        return candidates

    def _matches_selector(self, wrapper, selector: WindowsUiSelector) -> bool:
        info = self._snapshot(wrapper)
        if selector.hwnd is not None and info.hwnd != selector.hwnd:
            return False
        if selector.title and selector.title.lower() not in str(info.title or "").lower():
            return False
        if selector.control_type and selector.control_type.lower() != str(info.control_type or "").lower():
            return False
        if selector.auto_id and selector.auto_id.lower() != str(info.auto_id or "").lower():
            return False
        if selector.class_name and selector.class_name.lower() != str(info.class_name or "").lower():
            return False
        if selector.process_name and selector.process_name.lower() not in str(info.process_name or "").lower():
            return False
        return True

    def _resolve_candidates(
        self,
        *,
        hwnd: Optional[int] = None,
        title: Optional[str] = None,
        process_name: Optional[str] = None,
        selector: Optional[Dict[str, Any]] = None,
        element_id: Optional[str] = None,
        include_children: bool = True,
        max_results: int = 25,
    ) -> Tuple[Any, List[Any]]:
        window = self._resolve_window(hwnd=hwnd, title=title, process_name=process_name)
        effective_selector = selector
        if element_id and element_id in self._element_cache:
            cached = self._element_cache[element_id]
            effective_selector = cached.get("selector") or effective_selector
        if not effective_selector:
            return window, [window]

        selector_model = WindowsUiSelector(**effective_selector)
        matches: List[Any] = []
        for candidate in self._candidate_wrappers(window, include_children=include_children):
            try:
                if self._matches_selector(candidate, selector_model):
                    matches.append(candidate)
            except Exception:
                logger.exception("failed to inspect UI candidate")
            if len(matches) >= max_results:
                break
        return window, matches

    async def inspect_window(self, *, hwnd: Optional[int] = None, title: Optional[str] = None, process_name: Optional[str] = None, include_children: bool = True, max_results: int = 25) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            window = self._resolve_window(hwnd=hwnd, title=title, process_name=process_name)
            snapshot = self._snapshot(window)
            children = []
            if include_children:
                for child in self._candidate_wrappers(window, include_children=True)[1:max_results + 1]:
                    children.append(self._snapshot(child).dict())
            return {"window": snapshot.dict(), "children": children}

        return await asyncio.to_thread(_run)

    async def list_elements(self, *, hwnd: Optional[int] = None, title: Optional[str] = None, process_name: Optional[str] = None, selector: Optional[Dict[str, Any]] = None, include_children: bool = True, max_results: int = 50) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            _, matches = self._resolve_candidates(
                hwnd=hwnd,
                title=title,
                process_name=process_name,
                selector=selector,
                include_children=include_children,
                max_results=max_results,
            )
            return {"elements": [self._snapshot(match).dict() for match in matches]}

        return await asyncio.to_thread(_run)

    async def find_element(self, *, hwnd: Optional[int] = None, title: Optional[str] = None, process_name: Optional[str] = None, selector: Optional[Dict[str, Any]] = None, element_id: Optional[str] = None, include_children: bool = True, max_results: int = 10) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            _, matches = self._resolve_candidates(
                hwnd=hwnd,
                title=title,
                process_name=process_name,
                selector=selector,
                element_id=element_id,
                include_children=include_children,
                max_results=max_results,
            )
            payload = [self._snapshot(match).dict() for match in matches]
            return {"matches": payload}

        return await asyncio.to_thread(_run)

    async def wait_for_element(self, *, hwnd: Optional[int] = None, title: Optional[str] = None, process_name: Optional[str] = None, selector: Optional[Dict[str, Any]] = None, element_id: Optional[str] = None, include_children: bool = True, timeout_seconds: int = 15) -> Dict[str, Any]:
        deadline = time.monotonic() + max(timeout_seconds, 1)
        while time.monotonic() < deadline:
            result = await self.find_element(
                hwnd=hwnd,
                title=title,
                process_name=process_name,
                selector=selector,
                element_id=element_id,
                include_children=include_children,
                max_results=5,
            )
            if result["matches"]:
                return {"element": result["matches"][0], "matches": result["matches"]}
            await asyncio.sleep(0.35)
        raise TimeoutError("element did not appear before timeout")

    async def invoke_element(self, *, hwnd: Optional[int] = None, title: Optional[str] = None, process_name: Optional[str] = None, selector: Optional[Dict[str, Any]] = None, element_id: Optional[str] = None) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            _, matches = self._resolve_candidates(
                hwnd=hwnd,
                title=title,
                process_name=process_name,
                selector=selector,
                element_id=element_id,
                include_children=True,
                max_results=5,
            )
            if not matches:
                raise ValueError("element not found")
            target = matches[0]
            if hasattr(target, "invoke"):
                target.invoke()
            elif hasattr(target, "click_input"):
                target.click_input()
            else:
                raise RuntimeError("element does not support invoke")
            return {"element": self._snapshot(target).dict()}

        return await asyncio.to_thread(_run)

    async def set_text(self, *, text: str, hwnd: Optional[int] = None, title: Optional[str] = None, process_name: Optional[str] = None, selector: Optional[Dict[str, Any]] = None, element_id: Optional[str] = None) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            _, matches = self._resolve_candidates(
                hwnd=hwnd,
                title=title,
                process_name=process_name,
                selector=selector,
                element_id=element_id,
                include_children=True,
                max_results=5,
            )
            if not matches:
                raise ValueError("element not found")
            target = matches[0]
            if hasattr(target, "set_edit_text"):
                target.set_edit_text(text)
            elif hasattr(target, "type_keys"):
                target.type_keys("^a{BACKSPACE}", set_foreground=True)
                target.type_keys(text, with_spaces=True, pause=0.01)
            else:
                raise RuntimeError("element does not support text input")
            return {"element": self._snapshot(target).dict(), "text": text}

        return await asyncio.to_thread(_run)

    async def select_item(self, *, item_text: str, hwnd: Optional[int] = None, title: Optional[str] = None, process_name: Optional[str] = None, selector: Optional[Dict[str, Any]] = None, element_id: Optional[str] = None) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            _, matches = self._resolve_candidates(
                hwnd=hwnd,
                title=title,
                process_name=process_name,
                selector=selector,
                element_id=element_id,
                include_children=True,
                max_results=5,
            )
            if not matches:
                raise ValueError("element not found")
            target = matches[0]
            if hasattr(target, "select"):
                target.select(item_text)
            elif hasattr(target, "type_keys"):
                target.type_keys(item_text, with_spaces=True)
            else:
                raise RuntimeError("element does not support selection")
            return {"element": self._snapshot(target).dict(), "item_text": item_text}

        return await asyncio.to_thread(_run)

    async def send_hotkey(self, hotkey: str) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            _ensure_backend()
            if send_keys is None:
                raise WindowsUiUnavailable("send_keys backend is unavailable")
            send_keys(hotkey)
            return {"hotkey": hotkey}

        return await asyncio.to_thread(_run)

    async def scroll(self, *, direction: Optional[str] = None, amount: int = 1, hwnd: Optional[int] = None, title: Optional[str] = None, process_name: Optional[str] = None, selector: Optional[Dict[str, Any]] = None, element_id: Optional[str] = None) -> Dict[str, Any]:
        key = "{PGDN}" if str(direction or "down").lower() != "up" else "{PGUP}"

        def _run() -> Dict[str, Any]:
            _, matches = self._resolve_candidates(
                hwnd=hwnd,
                title=title,
                process_name=process_name,
                selector=selector,
                element_id=element_id,
                include_children=True,
                max_results=5,
            )
            if not matches:
                raise ValueError("element not found")
            target = matches[0]
            if not hasattr(target, "type_keys"):
                raise RuntimeError("element does not support keyboard scrolling")
            for _ in range(max(amount, 1)):
                target.type_keys(key, set_foreground=True)
            return {"element": self._snapshot(target).dict(), "direction": direction or "down", "amount": amount}

        return await asyncio.to_thread(_run)

    async def read_element_text(self, *, hwnd: Optional[int] = None, title: Optional[str] = None, process_name: Optional[str] = None, selector: Optional[Dict[str, Any]] = None, element_id: Optional[str] = None) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            _, matches = self._resolve_candidates(
                hwnd=hwnd,
                title=title,
                process_name=process_name,
                selector=selector,
                element_id=element_id,
                include_children=True,
                max_results=5,
            )
            if not matches:
                raise ValueError("element not found")
            target = matches[0]
            text = ""
            if hasattr(target, "window_text"):
                text = target.window_text()
            elif hasattr(target, "texts"):
                text = "\n".join(target.texts())
            return {"element": self._snapshot(target).dict(), "text": text}

        return await asyncio.to_thread(_run)

    async def get_focused_element(self) -> Dict[str, Any]:
        def _run() -> Dict[str, Any]:
            desktop = self._desktop()
            focused = desktop.get_focus()
            return {"element": self._snapshot(focused).dict()}

        return await asyncio.to_thread(_run)

