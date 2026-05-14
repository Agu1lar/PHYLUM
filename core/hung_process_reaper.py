# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Hung Process Reaper — kills frozen target processes to unblock leaked threads.

When ``asyncio.wait_for`` fires a ``TimeoutError`` inside ``BaseTool.run()``,
the underlying OS thread from ``asyncio.to_thread`` keeps blocking on a hung
Win32/COM call.  Python cannot forcibly interrupt native threads.

This module solves it by killing the *target process* that is holding the
thread.  Once the process dies, the blocked COM/Win32 call returns an RPC
error, the thread exits, and the thread-pool slot is reclaimed.

Flow:
    1. Tool sets ``self._target_context`` before ``_run()``.
    2. ``BaseTool.run()`` catches ``TimeoutError``.
    3. Calls ``reap_if_hung(target_context)`` from this module.
    4. Reaper confirms freeze via ``FrozenWindowDetector``.
    5. If confirmed, kills the process via ``taskkill /F``.
    6. Logs the event and returns a ``ReapResult``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from process_watchdog import (
        FrozenWindowDetector,
        _kill_process_by_pid,
        _is_process_alive,
        _get_window_pid,
    )
except ImportError:
    FrozenWindowDetector = None  # type: ignore[misc,assignment]
    _kill_process_by_pid = None  # type: ignore[assignment]
    _is_process_alive = None  # type: ignore[assignment]
    _get_window_pid = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class TargetContext:
    """Describes the process/window a tool was operating on when it timed out."""
    hwnd: int = 0
    pid: int = 0
    process_name: str = ""
    title: str = ""
    tool_name: str = ""
    action: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hwnd": self.hwnd,
            "pid": self.pid,
            "process_name": self.process_name,
            "title": self.title,
            "tool_name": self.tool_name,
            "action": self.action,
        }


@dataclass
class ReapResult:
    reaped: bool = False
    confirmed_hung: bool = False
    killed_pid: int = 0
    process_name: str = ""
    reason: str = ""
    elapsed_ms: float = 0.0
    checks: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reaped": self.reaped,
            "confirmed_hung": self.confirmed_hung,
            "killed_pid": self.killed_pid,
            "process_name": self.process_name,
            "reason": self.reason,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "checks": self.checks,
        }


# ---------------------------------------------------------------------------
# Process resolution helpers
# ---------------------------------------------------------------------------

_OFFICE_PROCESS_MAP = {
    "word": "WINWORD.EXE",
    "excel": "EXCEL.EXE",
    "powerpoint": "POWERPNT.EXE",
    "outlook": "OUTLOOK.EXE",
    "onenote": "ONENOTE.EXE",
}


def _resolve_pid_from_context(ctx: TargetContext) -> int:
    """Best-effort PID resolution from available context."""
    if ctx.pid:
        return ctx.pid
    if ctx.hwnd and _get_window_pid:
        pid = _get_window_pid(ctx.hwnd)
        if pid:
            return pid
    if ctx.process_name:
        try:
            import psutil
            pname = ctx.process_name.lower()
            for proc in psutil.process_iter(["pid", "name"]):
                if proc.info["name"] and proc.info["name"].lower() == pname:
                    return proc.info["pid"]
        except Exception:
            pass
    return 0


def _resolve_hwnd_from_pid(pid: int) -> List[int]:
    """Find all visible window handles belonging to a PID."""
    if not FrozenWindowDetector:
        return []
    try:
        detector = FrozenWindowDetector()
        results = detector.check_by_pid(pid)
        return [r["hwnd"] for r in results if r.get("hwnd")]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Core reaper logic
# ---------------------------------------------------------------------------

async def reap_if_hung(
    ctx: TargetContext,
    *,
    skip_confirmation: bool = False,
    event_bus=None,
) -> ReapResult:
    """Check if the target process is hung; if so, kill it to free threads.

    Args:
        ctx: Target context from the tool that timed out.
        skip_confirmation: If True, skip the IsHungAppWindow check and kill
            based solely on the timeout having occurred.  Useful when the
            process is known to be blocking COM without a visible window.
        event_bus: Optional event bus for emitting ``process_reaped`` events.

    Returns:
        ``ReapResult`` describing what happened.
    """
    start = time.time()

    if not _kill_process_by_pid:
        return ReapResult(
            reason="process_watchdog not available (non-Windows?)",
            elapsed_ms=(time.time() - start) * 1000,
        )

    pid = await asyncio.to_thread(_resolve_pid_from_context, ctx)
    if not pid:
        return ReapResult(
            reason=f"could not resolve PID from context: {ctx.to_dict()}",
            elapsed_ms=(time.time() - start) * 1000,
        )

    alive = await asyncio.to_thread(_is_process_alive, pid)
    if not alive:
        return ReapResult(
            reason=f"process {pid} is already dead",
            elapsed_ms=(time.time() - start) * 1000,
        )

    confirmed_hung = False
    checks: Dict[str, Any] = {"pid": pid, "alive": alive}

    if not skip_confirmation and FrozenWindowDetector:
        detector = FrozenWindowDetector()

        if ctx.hwnd:
            check = await asyncio.to_thread(detector.check, ctx.hwnd)
            confirmed_hung = check.get("frozen", False)
            checks["hwnd_check"] = check
        else:
            hwnds = await asyncio.to_thread(_resolve_hwnd_from_pid, pid)
            checks["windows_found"] = len(hwnds)
            for hwnd in hwnds:
                check = await asyncio.to_thread(detector.check, hwnd)
                if check.get("frozen", False):
                    confirmed_hung = True
                    checks["frozen_hwnd"] = hwnd
                    checks["hwnd_check"] = check
                    break

        if not confirmed_hung:
            return ReapResult(
                confirmed_hung=False,
                reason="process is alive but NOT confirmed hung — skipping kill",
                elapsed_ms=(time.time() - start) * 1000,
                checks=checks,
            )

    pname = ctx.process_name or f"pid:{pid}"
    logger.warning(
        "HungProcessReaper: killing %s (pid=%d) — confirmed_hung=%s, tool=%s, action=%s",
        pname, pid, confirmed_hung or skip_confirmation, ctx.tool_name, ctx.action,
    )

    killed = await asyncio.to_thread(_kill_process_by_pid, pid)
    elapsed = (time.time() - start) * 1000

    result = ReapResult(
        reaped=killed,
        confirmed_hung=confirmed_hung or skip_confirmation,
        killed_pid=pid if killed else 0,
        process_name=pname,
        reason="process killed to free blocked thread" if killed else "kill failed",
        elapsed_ms=elapsed,
        checks=checks,
    )

    if event_bus and killed:
        try:
            await event_bus.emit_raw(
                "process_reaped",
                {**result.to_dict(), "target": ctx.to_dict()},
                source="hung_process_reaper",
            )
        except Exception:
            pass

    return result


def resolve_office_process_name(action: str, path: Optional[str] = None) -> str:
    """Infer the Office process name from the tool action or file path."""
    action_lower = action.lower()
    for key, exe in _OFFICE_PROCESS_MAP.items():
        if key in action_lower:
            return exe

    if path:
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        ext_map = {
            "doc": "WINWORD.EXE", "docx": "WINWORD.EXE", "docm": "WINWORD.EXE",
            "xls": "EXCEL.EXE", "xlsx": "EXCEL.EXE", "xlsm": "EXCEL.EXE", "csv": "EXCEL.EXE",
            "ppt": "POWERPNT.EXE", "pptx": "POWERPNT.EXE",
            "msg": "OUTLOOK.EXE",
        }
        return ext_map.get(ext, "")

    return ""
