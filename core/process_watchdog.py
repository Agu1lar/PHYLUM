# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Frozen window / unresponsive process watchdog with specific recovery.

Detects when a monitored process or window becomes unresponsive and applies
targeted recovery actions:

1. **ProcessWatchdog** — monitors a set of processes by PID or name, detects
   unresponsive state via ``IsHungAppWindow`` / ``SendMessageTimeout``, and
   executes recovery actions (retry message, close, kill, restart).

2. **FrozenWindowDetector** — checks a window for the "Not Responding" state
   using Win32 API or heuristic title check.

3. **WatchdogPolicy** — configurable rules: how long to wait, how many retries,
   which recovery action to take per application/situation.
"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Win32 helpers (lazy imports, graceful on non-Windows)
# ---------------------------------------------------------------------------

_win32_available = False
_user32 = None
_kernel32 = None

try:
    if sys.platform == "win32":
        _user32 = ctypes.windll.user32
        _kernel32 = ctypes.windll.kernel32
        _win32_available = True
except Exception:
    pass


def _is_hung_window(hwnd: int) -> bool:
    """Check if a window is in a 'Not Responding' state via IsHungAppWindow."""
    if not _win32_available or not _user32:
        return False
    try:
        return bool(_user32.IsHungAppWindow(hwnd))
    except Exception:
        return False


def _send_message_timeout(hwnd: int, timeout_ms: int = 3000) -> bool:
    """Try to send WM_NULL to a window with timeout. Returns True if responsive."""
    if not _win32_available or not _user32:
        return True
    try:
        WM_NULL = 0x0000
        SMTO_ABORTIFHUNG = 0x0002
        result = ctypes.c_ulong(0)
        ret = _user32.SendMessageTimeoutW(
            hwnd, WM_NULL, 0, 0, SMTO_ABORTIFHUNG, timeout_ms, ctypes.byref(result),
        )
        return ret != 0
    except Exception:
        return True


def _get_window_title(hwnd: int) -> str:
    if not _win32_available or not _user32:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        _user32.GetWindowTextW(hwnd, buf, 512)
        return buf.value
    except Exception:
        return ""


def _is_window_visible(hwnd: int) -> bool:
    if not _win32_available or not _user32:
        return False
    try:
        return bool(_user32.IsWindowVisible(hwnd))
    except Exception:
        return False


def _get_window_pid(hwnd: int) -> int:
    if not _win32_available or not _user32:
        return 0
    try:
        pid = ctypes.c_ulong(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value
    except Exception:
        return 0


def _close_window_graceful(hwnd: int) -> bool:
    """Post WM_CLOSE to a window."""
    if not _win32_available or not _user32:
        return False
    try:
        WM_CLOSE = 0x0010
        _user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        return True
    except Exception:
        return False


def _kill_process_by_pid(pid: int) -> bool:
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True, check=False, timeout=10,
            )
        else:
            os.kill(pid, 9)
        return True
    except Exception:
        return False


def _is_process_alive(pid: int) -> bool:
    try:
        if sys.platform == "win32":
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            return str(pid) in r.stdout
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class RecoveryAction(str, Enum):
    NONE = "none"
    RETRY_MESSAGE = "retry_message"
    CLOSE_GRACEFUL = "close_graceful"
    KILL = "kill"
    RESTART = "restart"


@dataclass
class WatchTarget:
    pid: int = 0
    hwnd: int = 0
    process_name: str = ""
    title_pattern: str = ""
    restart_command: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pid": self.pid, "hwnd": self.hwnd,
            "process_name": self.process_name,
            "title_pattern": self.title_pattern,
            "restart_command": self.restart_command,
        }


@dataclass
class WatchStatus:
    target: WatchTarget
    responsive: bool = True
    frozen: bool = False
    frozen_since: Optional[float] = None
    frozen_duration_ms: int = 0
    check_count: int = 0
    recovery_actions_taken: List[str] = field(default_factory=list)
    recovered: bool = False
    alive: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target.to_dict(),
            "responsive": self.responsive,
            "frozen": self.frozen,
            "frozen_duration_ms": self.frozen_duration_ms,
            "check_count": self.check_count,
            "recovery_actions_taken": self.recovery_actions_taken,
            "recovered": self.recovered,
            "alive": self.alive,
        }


@dataclass
class WatchdogPolicy:
    check_interval: float = 5.0
    freeze_threshold_ms: int = 10_000
    max_recovery_attempts: int = 3
    recovery_sequence: List[RecoveryAction] = field(default_factory=lambda: [
        RecoveryAction.RETRY_MESSAGE,
        RecoveryAction.CLOSE_GRACEFUL,
        RecoveryAction.KILL,
    ])
    auto_restart: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_interval": self.check_interval,
            "freeze_threshold_ms": self.freeze_threshold_ms,
            "max_recovery_attempts": self.max_recovery_attempts,
            "recovery_sequence": [a.value for a in self.recovery_sequence],
            "auto_restart": self.auto_restart,
        }


@dataclass
class RecoveryResult:
    action: RecoveryAction
    success: bool
    message: str = ""
    new_pid: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action.value,
            "success": self.success,
            "message": self.message,
            "new_pid": self.new_pid,
        }


# ---------------------------------------------------------------------------
# FrozenWindowDetector
# ---------------------------------------------------------------------------

class FrozenWindowDetector:
    """Detects if a window is frozen/unresponsive."""

    def __init__(self, *, message_timeout_ms: int = 3000):
        self.message_timeout_ms = message_timeout_ms

    def check(self, hwnd: int) -> Dict[str, Any]:
        """Check a single window. Returns status dict."""
        if not hwnd:
            return {"hwnd": 0, "frozen": False, "error": "no hwnd"}

        title = _get_window_title(hwnd)
        visible = _is_window_visible(hwnd)
        pid = _get_window_pid(hwnd)

        hung = _is_hung_window(hwnd)
        responsive = _send_message_timeout(hwnd, self.message_timeout_ms)

        frozen = hung or not responsive

        title_hint = "(Not Responding)" in title if title else False
        if title_hint:
            frozen = True

        return {
            "hwnd": hwnd,
            "title": title,
            "pid": pid,
            "visible": visible,
            "frozen": frozen,
            "hung_api": hung,
            "message_responsive": responsive,
            "title_not_responding": title_hint,
        }

    def check_by_title(self, title_pattern: str) -> List[Dict[str, Any]]:
        """Check all visible windows matching a title pattern."""
        results: List[Dict[str, Any]] = []
        if not _win32_available:
            return results

        def _enum_callback(hwnd, _lparam):
            if _is_window_visible(hwnd):
                title = _get_window_title(hwnd)
                if title_pattern.lower() in title.lower():
                    results.append(self.check(hwnd))
            return True

        try:
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            _user32.EnumWindows(WNDENUMPROC(_enum_callback), 0)
        except Exception:
            pass
        return results

    def check_by_pid(self, pid: int) -> List[Dict[str, Any]]:
        """Check all windows belonging to a process."""
        results: List[Dict[str, Any]] = []
        if not _win32_available:
            return results

        def _enum_callback(hwnd, _lparam):
            if _is_window_visible(hwnd) and _get_window_pid(hwnd) == pid:
                results.append(self.check(hwnd))
            return True

        try:
            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            _user32.EnumWindows(WNDENUMPROC(_enum_callback), 0)
        except Exception:
            pass
        return results


# ---------------------------------------------------------------------------
# ProcessWatchdog
# ---------------------------------------------------------------------------

class ProcessWatchdog:
    """Monitors processes/windows and recovers from frozen state.

    Usage::

        watchdog = ProcessWatchdog()
        watchdog.add_target(WatchTarget(pid=1234, process_name="excel.exe"))
        watchdog.start()
        # ... later ...
        statuses = watchdog.get_statuses()
        watchdog.stop()
    """

    def __init__(
        self,
        *,
        policy: Optional[WatchdogPolicy] = None,
        on_frozen: Optional[Callable[["WatchStatus"], Any]] = None,
        on_recovery: Optional[Callable[["RecoveryResult"], Any]] = None,
        event_bus=None,
    ):
        self.policy = policy or WatchdogPolicy()
        self._on_frozen = on_frozen
        self._on_recovery = on_recovery
        self._event_bus = event_bus
        self._targets: Dict[str, WatchTarget] = {}
        self._statuses: Dict[str, WatchStatus] = {}
        self._detector = FrozenWindowDetector()
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    def _key(self, target: WatchTarget) -> str:
        if target.pid:
            return f"pid:{target.pid}"
        if target.hwnd:
            return f"hwnd:{target.hwnd}"
        return f"name:{target.process_name or target.title_pattern}"

    def add_target(self, target: WatchTarget) -> str:
        key = self._key(target)
        self._targets[key] = target
        self._statuses[key] = WatchStatus(target=target)
        return key

    def remove_target(self, key: str) -> None:
        self._targets.pop(key, None)
        self._statuses.pop(key, None)

    def get_statuses(self) -> Dict[str, WatchStatus]:
        return dict(self._statuses)

    def get_status(self, key: str) -> Optional[WatchStatus]:
        return self._statuses.get(key)

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._task = asyncio.ensure_future(self._loop())

    def stop(self) -> None:
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def check_once(self) -> Dict[str, WatchStatus]:
        """Run a single check cycle for all targets."""
        for key, target in list(self._targets.items()):
            status = self._statuses.get(key)
            if not status:
                continue
            await self._check_target(key, target, status)
        return dict(self._statuses)

    async def _loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                await self.check_once()
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.policy.check_interval,
                    )
                    break
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass

    async def _check_target(
        self, key: str, target: WatchTarget, status: WatchStatus,
    ) -> None:
        status.check_count += 1

        alive = True
        if target.pid:
            alive = await asyncio.to_thread(_is_process_alive, target.pid)
        status.alive = alive
        if not alive:
            status.frozen = False
            status.responsive = False
            return

        frozen = False
        if target.hwnd:
            result = await asyncio.to_thread(self._detector.check, target.hwnd)
            frozen = result.get("frozen", False)
        elif target.pid:
            results = await asyncio.to_thread(self._detector.check_by_pid, target.pid)
            frozen = any(r.get("frozen", False) for r in results)
        elif target.title_pattern:
            results = await asyncio.to_thread(
                self._detector.check_by_title, target.title_pattern,
            )
            frozen = any(r.get("frozen", False) for r in results)

        status.responsive = not frozen

        if frozen:
            if not status.frozen:
                status.frozen = True
                status.frozen_since = time.time()
            status.frozen_duration_ms = int(
                (time.time() - (status.frozen_since or time.time())) * 1000
            )

            if self._on_frozen:
                try:
                    result = self._on_frozen(status)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass

            if self._event_bus:
                try:
                    await self._event_bus.emit_raw(
                        "process_frozen",
                        status.to_dict(),
                        source="process_watchdog",
                    )
                except Exception:
                    pass

            if status.frozen_duration_ms >= self.policy.freeze_threshold_ms:
                await self._attempt_recovery(key, target, status)
        else:
            if status.frozen:
                status.recovered = True
            status.frozen = False
            status.frozen_since = None
            status.frozen_duration_ms = 0

    async def _attempt_recovery(
        self, key: str, target: WatchTarget, status: WatchStatus,
    ) -> None:
        attempts = len(status.recovery_actions_taken)
        if attempts >= self.policy.max_recovery_attempts:
            return

        if attempts < len(self.policy.recovery_sequence):
            action = self.policy.recovery_sequence[attempts]
        else:
            action = RecoveryAction.KILL

        result = await self._execute_recovery(target, action)
        status.recovery_actions_taken.append(action.value)

        if self._on_recovery:
            try:
                cb_result = self._on_recovery(result)
                if asyncio.iscoroutine(cb_result):
                    await cb_result
            except Exception:
                pass

        if self._event_bus:
            try:
                await self._event_bus.emit_raw(
                    "process_recovery",
                    {**result.to_dict(), "target": target.to_dict()},
                    source="process_watchdog",
                )
            except Exception:
                pass

        if result.success:
            status.frozen = False
            status.frozen_since = None
            status.frozen_duration_ms = 0
            status.recovered = True

            if action == RecoveryAction.KILL and self.policy.auto_restart and target.restart_command:
                restart = await self._execute_recovery(target, RecoveryAction.RESTART)
                status.recovery_actions_taken.append("restart")
                if restart.new_pid:
                    target.pid = restart.new_pid

    async def _execute_recovery(
        self, target: WatchTarget, action: RecoveryAction,
    ) -> RecoveryResult:
        try:
            if action == RecoveryAction.RETRY_MESSAGE:
                if target.hwnd:
                    ok = await asyncio.to_thread(
                        _send_message_timeout, target.hwnd, 5000,
                    )
                    return RecoveryResult(
                        action=action, success=ok,
                        message="Sent WM_NULL to window" if ok else "Window did not respond",
                    )
                return RecoveryResult(
                    action=action, success=False, message="No hwnd to send message to",
                )

            if action == RecoveryAction.CLOSE_GRACEFUL:
                if target.hwnd:
                    ok = await asyncio.to_thread(_close_window_graceful, target.hwnd)
                    return RecoveryResult(
                        action=action, success=ok,
                        message="Sent WM_CLOSE" if ok else "Failed to send WM_CLOSE",
                    )
                return RecoveryResult(
                    action=action, success=False, message="No hwnd to close",
                )

            if action == RecoveryAction.KILL:
                if target.pid:
                    ok = await asyncio.to_thread(_kill_process_by_pid, target.pid)
                    return RecoveryResult(
                        action=action, success=ok,
                        message=f"Killed pid {target.pid}" if ok else "Kill failed",
                    )
                return RecoveryResult(
                    action=action, success=False, message="No PID to kill",
                )

            if action == RecoveryAction.RESTART:
                cmd = target.restart_command
                if not cmd:
                    return RecoveryResult(
                        action=action, success=False, message="No restart command configured",
                    )
                try:
                    proc = await asyncio.to_thread(
                        subprocess.Popen,
                        cmd,
                        shell=True,
                        creationflags=0x08000000 if sys.platform == "win32" else 0,
                    )
                    return RecoveryResult(
                        action=action, success=True,
                        message=f"Restarted with pid {proc.pid}",
                        new_pid=proc.pid,
                    )
                except Exception as exc:
                    return RecoveryResult(
                        action=action, success=False, message=str(exc),
                    )

            return RecoveryResult(action=action, success=False, message="Unknown action")

        except Exception as exc:
            return RecoveryResult(action=action, success=False, message=str(exc))
