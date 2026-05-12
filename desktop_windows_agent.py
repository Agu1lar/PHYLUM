from __future__ import annotations

import asyncio
import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Any, Dict, List, Optional

import psutil

from desktop_windows_models import ServiceInfo, WindowInfo
from os_inspect_wmi import WmiWrapper

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
kernel32 = ctypes.windll.kernel32 if hasattr(ctypes, "windll") else None
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
SW_RESTORE = 9


def _require_windows() -> None:
    if user32 is None or kernel32 is None:
        raise RuntimeError("desktop tool is only available on Windows")


def _enum_windows() -> List[WindowInfo]:
    _require_windows()
    windows: List[WindowInfo] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd: int, lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        title_buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buffer, length + 1)
        title = title_buffer.value.strip()
        if not title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_name = None
        try:
            process_name = psutil.Process(pid.value).name()
        except Exception:
            process_name = None
        windows.append(
            WindowInfo(
                hwnd=int(hwnd),
                title=title,
                pid=int(pid.value) if pid.value else None,
                process_name=process_name,
                visible=True,
            )
        )
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return windows


def _focus_window(hwnd: Optional[int] = None, title: Optional[str] = None) -> WindowInfo:
    _require_windows()
    candidates = _enum_windows()
    selected: Optional[WindowInfo] = None
    if hwnd is not None:
        selected = next((item for item in candidates if item.hwnd == hwnd), None)
    elif title:
        lower_title = title.lower()
        selected = next((item for item in candidates if lower_title in item.title.lower()), None)
    if selected is None:
        raise ValueError("window not found")
    user32.ShowWindow(selected.hwnd, SW_RESTORE)
    if not user32.SetForegroundWindow(selected.hwnd):
        logger.warning("SetForegroundWindow returned false for hwnd=%s", selected.hwnd)
    return selected


def _clipboard_get() -> str:
    _require_windows()
    if not user32.OpenClipboard(None):
        raise RuntimeError("unable to open clipboard")
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        locked = kernel32.GlobalLock(handle)
        if not locked:
            return ""
        try:
            return ctypes.wstring_at(locked)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _clipboard_set(text: str) -> None:
    _require_windows()
    data = (text + "\x00").encode("utf-16-le")
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise MemoryError("unable to allocate clipboard memory")
    locked = kernel32.GlobalLock(handle)
    ctypes.memmove(locked, data, len(data))
    kernel32.GlobalUnlock(handle)
    if not user32.OpenClipboard(None):
        raise RuntimeError("unable to open clipboard")
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            raise RuntimeError("unable to set clipboard data")
        handle = None
    finally:
        user32.CloseClipboard()
        if handle:
            kernel32.GlobalFree(handle)


def _notify(title: str, message: str) -> None:
    try:
        from win10toast import ToastNotifier  # type: ignore

        toaster = ToastNotifier()
        toaster.show_toast(title, message, threaded=True, duration=5)
        return
    except Exception:
        logger.debug("win10toast not available, falling back to MessageBoxW")
    _require_windows()
    user32.MessageBoxW(0, message, title, 0x40)


def _list_services() -> List[ServiceInfo]:
    services: List[ServiceInfo] = []
    try:
        for service in psutil.win_service_iter():
            data = service.as_dict()
            services.append(
                ServiceInfo(
                    name=data.get("name") or "",
                    display_name=data.get("display_name"),
                    status=data.get("status"),
                    start_type=data.get("start_type"),
                    username=data.get("username"),
                )
            )
    except Exception:
        logger.exception("failed to enumerate services")
    return services


class DesktopWindowsAgent:
    def __init__(self):
        self.wmi = WmiWrapper()

    async def list_processes(self, limit: int = 200) -> Dict[str, Any]:
        processes: List[Dict[str, Any]] = []
        for proc in psutil.process_iter(["pid", "name", "exe", "username", "cpu_percent"]):
            try:
                info = proc.info
                processes.append(
                    {
                        "pid": info.get("pid"),
                        "name": info.get("name"),
                        "exe": info.get("exe"),
                        "username": info.get("username"),
                        "cpu_percent": info.get("cpu_percent"),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if len(processes) >= limit:
                break
        return {"processes": processes}

    async def list_windows(self) -> Dict[str, Any]:
        windows = await asyncio.to_thread(_enum_windows)
        return {"windows": [item.dict() for item in windows]}

    async def focus_window(self, *, hwnd: Optional[int] = None, title: Optional[str] = None) -> Dict[str, Any]:
        focused = await asyncio.to_thread(_focus_window, hwnd, title)
        return {"window": focused.dict()}

    async def clipboard_get(self) -> Dict[str, Any]:
        text = await asyncio.to_thread(_clipboard_get)
        return {"text": text}

    async def clipboard_set(self, text: str) -> Dict[str, Any]:
        await asyncio.to_thread(_clipboard_set, text)
        return {"text": text}

    async def notify(self, message: str, title: str = "Agente Desktop") -> Dict[str, Any]:
        threading.Thread(target=_notify, args=(title, message), daemon=True).start()
        return {"title": title, "message": message}

    async def list_services(self) -> Dict[str, Any]:
        services = await asyncio.to_thread(_list_services)
        return {"services": [item.dict() for item in services]}

    async def service_action(self, service_name: str, action: str) -> Dict[str, Any]:
        if self.wmi.conn is None:
            raise RuntimeError("WMI is not available for service control")

        def _run_action() -> Dict[str, Any]:
            services = self.wmi.conn.Win32_Service(Name=service_name)
            if not services:
                raise ValueError(f"service not found: {service_name}")
            service = services[0]
            if action == "start":
                result = service.StartService()
            elif action == "stop":
                result = service.StopService()
            elif action == "restart":
                service.StopService()
                result = service.StartService()
            else:
                raise ValueError(f"unsupported service action: {action}")
            return {"service_name": service_name, "service_action": action, "result_code": result}

        return await asyncio.to_thread(_run_action)
