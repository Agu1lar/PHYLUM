from __future__ import annotations

import asyncio
import ctypes
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil

from desktop_windows_models import ExplorerWindowInfo, ServiceInfo, WindowInfo
from os_inspect_wmi import WmiWrapper

logger = logging.getLogger(__name__)

user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
kernel32 = ctypes.windll.kernel32 if hasattr(ctypes, "windll") else None
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
SW_RESTORE = 9
WM_CLOSE = 0x0010

APP_ALIASES = {
    "word": ["WINWORD.EXE"],
    "microsoft word": ["WINWORD.EXE"],
    "excel": ["EXCEL.EXE"],
    "microsoft excel": ["EXCEL.EXE"],
    "powerpoint": ["POWERPNT.EXE"],
    "microsoft powerpoint": ["POWERPNT.EXE"],
    "outlook": ["OUTLOOK.EXE"],
    "onenote": ["ONENOTE.EXE"],
    "explorer": ["explorer.exe"],
    "file explorer": ["explorer.exe"],
    "notepad": ["notepad.exe"],
    "paint": ["mspaint.exe"],
    "calculator": ["calc.exe"],
    "calc": ["calc.exe"],
    "powershell": ["powershell.exe", "pwsh.exe"],
    "command prompt": ["cmd.exe"],
    "cmd": ["cmd.exe"],
}


def _require_windows() -> None:
    if user32 is None or kernel32 is None:
        raise RuntimeError("desktop tool is only available on Windows")


def _common_executable_roots() -> List[Path]:
    roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))),
        Path.home() / "AppData" / "Roaming",
    ]
    return [root for root in roots if root.exists()]


def _candidate_executables(app_name: str) -> List[str]:
    normalized = (app_name or "").strip()
    if not normalized:
        return []
    lowered = normalized.lower()
    candidates = list(APP_ALIASES.get(lowered, []))
    if normalized.lower().endswith(".exe"):
        candidates.append(normalized)
    else:
        candidates.extend([normalized, f"{normalized}.exe", normalized.replace(" ", "") + ".exe"])
    seen = set()
    unique_candidates: List[str] = []
    for item in candidates:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(item)
    return unique_candidates


def _app_paths_registry_candidates(executable_name: str) -> List[str]:
    paths: List[str] = []
    try:
        import winreg
    except Exception:
        return paths

    registry_roots = [
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    ]
    for root, subkey in registry_roots:
        try:
            with winreg.OpenKey(root, f"{subkey}\\{executable_name}") as key:
                value, _ = winreg.QueryValueEx(key, "")
                if value:
                    paths.append(str(value))
        except OSError:
            continue
    return paths


def _resolve_app_launch_target(app_name: str, app_path: Optional[str] = None) -> str:
    if app_path:
        candidate_path = Path(app_path)
        if candidate_path.exists():
            return str(candidate_path)
        raise FileNotFoundError(f"application path not found: {app_path}")

    if not app_name:
        raise ValueError("app_name is required")

    for candidate in _candidate_executables(app_name):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved

        for registry_path in _app_paths_registry_candidates(candidate):
            if Path(registry_path).exists():
                return registry_path

    exact_targets = [item.lower() for item in _candidate_executables(app_name) if item.lower().endswith(".exe")]
    for root in _common_executable_roots():
        try:
            for executable in root.rglob("*.exe"):
                if executable.name.lower() in exact_targets:
                    return str(executable)
        except Exception:
            logger.exception("failed to scan executable root %s", root)

    raise FileNotFoundError(f"could not resolve an executable for '{app_name}'")


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


def _find_windows(
    *,
    hwnd: Optional[int] = None,
    title: Optional[str] = None,
    process_name: Optional[str] = None,
) -> List[WindowInfo]:
    windows = _enum_windows()
    if hwnd is not None:
        return [item for item in windows if item.hwnd == hwnd]
    if title:
        lowered_title = title.lower()
        windows = [item for item in windows if lowered_title in item.title.lower()]
    if process_name:
        lowered_process = process_name.lower()
        windows = [
            item
            for item in windows
            if item.process_name and lowered_process in item.process_name.lower()
        ]
    return windows


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


def _list_explorer_windows() -> List[ExplorerWindowInfo]:
    _require_windows()
    ps_script = r"""
$shell = New-Object -ComObject Shell.Application
$result = @()
foreach ($window in $shell.Windows()) {
    try {
        $fullName = [string]$window.FullName
        $locationName = [string]$window.LocationName
        $locationUrl = [string]$window.LocationURL
        $path = $null
        $selected = @()
        try { $path = [string]$window.Document.Folder.Self.Path } catch {}
        try { $selected = @($window.Document.SelectedItems() | ForEach-Object { $_.Path }) } catch {}
        if (-not $path -and -not $locationUrl) { continue }
        $result += [pscustomobject]@{
            hwnd = [int64]$window.HWND
            title = $locationName
            process_name = if ($fullName) { [System.IO.Path]::GetFileName($fullName) } else { "explorer.exe" }
            visible = $true
            location_path = $path
            location_url = $locationUrl
            executable_path = $fullName
            selected_items = $selected
        }
    } catch {}
}
$result | ConvertTo-Json -Depth 4 -Compress
"""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "failed to enumerate explorer windows")
    stdout = (completed.stdout or "").strip()
    if not stdout:
        return []
    payload = json.loads(stdout)
    items = payload if isinstance(payload, list) else [payload]
    return [ExplorerWindowInfo(**item) for item in items]


def _list_mapped_drives() -> List[Dict[str, Any]]:
    ps_script = r"""
$drives = Get-PSDrive -PSProvider FileSystem | Select-Object Name, Root, CurrentLocation, Description, DisplayRoot, Used, Free
$drives | ConvertTo-Json -Depth 4 -Compress
"""
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "failed to enumerate mapped drives")
    stdout = (completed.stdout or "").strip()
    if not stdout:
        return []
    payload = json.loads(stdout)
    items = payload if isinstance(payload, list) else [payload]
    return items


def _open_path(path: str) -> Dict[str, Any]:
    target = Path(path)
    if not target.exists() and not path.startswith("\\\\"):
        raise FileNotFoundError(f"path not found: {path}")
    os.startfile(path)
    return {"path": path, "opened": True}


def _start_process_via_powershell(target: str, arguments: Optional[List[str]] = None) -> Dict[str, Any]:
    cmd = [target, *(arguments or [])]
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        proc = subprocess.Popen(cmd, creationflags=creationflags)
    except OSError as exc:
        raise RuntimeError(str(exc).strip() or f"failed to start process for {target}") from exc
    process_name = None
    try:
        process_name = psutil.Process(proc.pid).name()
    except Exception:
        process_name = Path(target).name
    return {"pid": proc.pid, "process_name": process_name, "path": target}


def _close_window(hwnd: Optional[int] = None, title: Optional[str] = None) -> WindowInfo:
    _require_windows()
    matches = _find_windows(hwnd=hwnd, title=title)
    if not matches:
        raise ValueError("window not found")
    selected = matches[0]
    user32.PostMessageW(selected.hwnd, WM_CLOSE, 0, 0)
    return selected


def _kill_process(
    *,
    pid: Optional[int] = None,
    process_name: Optional[str] = None,
    title: Optional[str] = None,
) -> List[Dict[str, Any]]:
    targets: List[psutil.Process] = []
    if pid is not None:
        targets = [psutil.Process(pid)]
    elif title:
        windows = _find_windows(title=title)
        pids = [item.pid for item in windows if item.pid]
        targets = [psutil.Process(item_pid) for item_pid in pids]
    elif process_name:
        lowered = process_name.lower()
        for proc in psutil.process_iter(["pid", "name"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if lowered in name:
                    targets.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    else:
        raise ValueError("pid, process_name or title is required")

    killed: List[Dict[str, Any]] = []
    seen_pids = set()
    for proc in targets:
        try:
            if proc.pid in seen_pids:
                continue
            seen_pids.add(proc.pid)
            info = {"pid": proc.pid, "name": proc.name()}
            proc.terminate()
            killed.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if not killed:
        raise ValueError("no matching process found")
    return killed


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

    async def list_explorer_windows(self) -> Dict[str, Any]:
        windows = await asyncio.to_thread(_list_explorer_windows)
        return {"windows": [item.dict() for item in windows]}

    async def list_mapped_drives(self) -> Dict[str, Any]:
        drives = await asyncio.to_thread(_list_mapped_drives)
        return {"drives": drives}

    async def get_explorer_selection(self) -> Dict[str, Any]:
        windows = await asyncio.to_thread(_list_explorer_windows)
        return {
            "windows": [
                {
                    "hwnd": item.hwnd,
                    "title": item.title,
                    "location_path": item.location_path,
                    "selected_items": list(item.selected_items),
                }
                for item in windows
            ]
        }

    async def open_app(
        self,
        *,
        app_name: Optional[str] = None,
        app_path: Optional[str] = None,
        arguments: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        target = await asyncio.to_thread(_resolve_app_launch_target, app_name or "", app_path)
        result = await asyncio.to_thread(_start_process_via_powershell, target, arguments)
        result["target"] = target
        if app_name:
            result["app_name"] = app_name
        return result

    async def open_path(self, path: str) -> Dict[str, Any]:
        return await asyncio.to_thread(_open_path, path)

    async def open_file(self, path: str) -> Dict[str, Any]:
        return await asyncio.to_thread(_open_path, path)

    async def wait_for_window(
        self,
        *,
        title: Optional[str] = None,
        process_name: Optional[str] = None,
        hwnd: Optional[int] = None,
        timeout_seconds: int = 15,
    ) -> Dict[str, Any]:
        deadline = time.monotonic() + max(timeout_seconds, 1)
        while time.monotonic() < deadline:
            matches = await asyncio.to_thread(_find_windows, hwnd=hwnd, title=title, process_name=process_name)
            if matches:
                return {"window": matches[0].dict(), "matched": True}
            await asyncio.sleep(0.4)
        raise TimeoutError("window did not appear before timeout")

    async def focus_window(self, *, hwnd: Optional[int] = None, title: Optional[str] = None) -> Dict[str, Any]:
        focused = await asyncio.to_thread(_focus_window, hwnd, title)
        return {"window": focused.dict()}

    async def close_window(self, *, hwnd: Optional[int] = None, title: Optional[str] = None) -> Dict[str, Any]:
        window = await asyncio.to_thread(_close_window, hwnd, title)
        return {"window": window.dict()}

    async def kill_process(
        self,
        *,
        pid: Optional[int] = None,
        process_name: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        killed = await asyncio.to_thread(_kill_process, pid=pid, process_name=process_name, title=title)
        return {"processes": killed}

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
