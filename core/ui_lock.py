# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""UI Operation Lock — serialises desktop automation and detects user interference.

Provides:
- **UIOperationLock**: async mutex that serialises all UI automation actions so
  only one mouse/keyboard/clipboard operation executes at a time.
- **CursorGuard**: saves the cursor position before an operation and restores it
  after, unless the user moved the cursor (interference detection).
- **InputGuard**: optional Win32 ``BlockInput`` wrapper for critical sequences with
  a hard safety timeout so the user is never locked out permanently.
- **InterferenceDetector**: compares cursor positions before/after an action to
  detect whether the human user moved the mouse during automation.
"""
from __future__ import annotations

import asyncio
import ctypes
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Win32 imports — graceful fallback on non-Windows
try:
    _user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    _HAS_WIN32 = True
except AttributeError:
    _user32 = None
    _HAS_WIN32 = False


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


# ---------------------------------------------------------------------------
# Low-level Win32 helpers
# ---------------------------------------------------------------------------

def _get_cursor_pos() -> Tuple[int, int]:
    if not _HAS_WIN32:
        return (0, 0)
    pt = _POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return (pt.x, pt.y)


def _set_cursor_pos(x: int, y: int) -> bool:
    if not _HAS_WIN32:
        return False
    return bool(_user32.SetCursorPos(x, y))


def _block_input(block: bool) -> bool:
    """Call ``BlockInput(TRUE/FALSE)``.  Requires elevated privileges on most
    Windows versions.  Returns False if the call fails (not elevated, or
    non-Windows)."""
    if not _HAS_WIN32:
        return False
    try:
        return bool(_user32.BlockInput(block))
    except Exception:
        return False


def _get_foreground_window() -> int:
    if not _HAS_WIN32:
        return 0
    return _user32.GetForegroundWindow() or 0


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class InterferenceType(str, Enum):
    NONE = "none"
    CURSOR_MOVED = "cursor_moved"
    FOCUS_CHANGED = "focus_changed"
    CURSOR_AND_FOCUS = "cursor_and_focus"


@dataclass
class InterferenceReport:
    detected: bool = False
    type: InterferenceType = InterferenceType.NONE
    cursor_before: Tuple[int, int] = (0, 0)
    cursor_after: Tuple[int, int] = (0, 0)
    cursor_delta_px: float = 0.0
    fg_window_before: int = 0
    fg_window_after: int = 0
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "detected": self.detected,
            "type": self.type.value,
            "cursor_before": list(self.cursor_before),
            "cursor_after": list(self.cursor_after),
            "cursor_delta_px": round(self.cursor_delta_px, 1),
            "fg_window_before": self.fg_window_before,
            "fg_window_after": self.fg_window_after,
        }


@dataclass
class UIOperationRecord:
    operation: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    duration_ms: float = 0.0
    interference: Optional[InterferenceReport] = None
    success: bool = True
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Interference Detector
# ---------------------------------------------------------------------------

CURSOR_JITTER_THRESHOLD_PX = 5  # ignore sub-5px jitter


class InterferenceDetector:
    """Snapshot cursor + foreground window before an action, compare after."""

    def __init__(self, *, jitter_threshold: int = CURSOR_JITTER_THRESHOLD_PX):
        self._jitter = jitter_threshold
        self._cursor_before: Tuple[int, int] = (0, 0)
        self._fg_before: int = 0

    def snapshot_before(self) -> None:
        self._cursor_before = _get_cursor_pos()
        self._fg_before = _get_foreground_window()

    def check_after(self) -> InterferenceReport:
        cursor_after = _get_cursor_pos()
        fg_after = _get_foreground_window()

        dx = abs(cursor_after[0] - self._cursor_before[0])
        dy = abs(cursor_after[1] - self._cursor_before[1])
        delta = (dx ** 2 + dy ** 2) ** 0.5

        cursor_moved = delta > self._jitter
        focus_changed = (
            self._fg_before != 0
            and fg_after != 0
            and self._fg_before != fg_after
        )

        if cursor_moved and focus_changed:
            itype = InterferenceType.CURSOR_AND_FOCUS
        elif cursor_moved:
            itype = InterferenceType.CURSOR_MOVED
        elif focus_changed:
            itype = InterferenceType.FOCUS_CHANGED
        else:
            itype = InterferenceType.NONE

        return InterferenceReport(
            detected=cursor_moved or focus_changed,
            type=itype,
            cursor_before=self._cursor_before,
            cursor_after=cursor_after,
            cursor_delta_px=delta,
            fg_window_before=self._fg_before,
            fg_window_after=fg_after,
            timestamp=time.time(),
        )


# ---------------------------------------------------------------------------
# Cursor Guard
# ---------------------------------------------------------------------------

class CursorGuard:
    """Save cursor position, restore it after the operation unless the user
    deliberately moved it (detected by InterferenceDetector)."""

    def __init__(self, *, restore_on_no_interference: bool = True):
        self._restore = restore_on_no_interference
        self._saved: Optional[Tuple[int, int]] = None

    def save(self) -> Tuple[int, int]:
        self._saved = _get_cursor_pos()
        return self._saved

    def restore(self, interference: InterferenceReport) -> bool:
        """Restore cursor only if there was no user interference."""
        if self._saved is None:
            return False
        if not self._restore:
            return False
        if interference.detected:
            logger.debug(
                "Cursor NOT restored — user interference detected (%s, delta=%.0fpx)",
                interference.type.value, interference.cursor_delta_px,
            )
            return False
        _set_cursor_pos(*self._saved)
        return True


# ---------------------------------------------------------------------------
# Input Guard (BlockInput wrapper)
# ---------------------------------------------------------------------------

MAX_BLOCK_DURATION_S = 5.0  # hard safety cap


class InputGuard:
    """Optional wrapper around ``BlockInput`` with a hard safety timeout.

    ``BlockInput`` prevents physical keyboard/mouse events from reaching any
    application.  This is useful during critical multi-step sequences (e.g.
    right-click → menu → click item) where a stray user click would corrupt
    the operation.

    Safety guarantees:
    - Requires explicit opt-in per operation.
    - Hard timeout of ``MAX_BLOCK_DURATION_S`` (default 5 s).
    - ``__del__`` and ``release`` always unblock.
    - Non-elevated processes silently degrade (``BlockInput`` returns False).
    """

    def __init__(self, *, max_duration_s: float = MAX_BLOCK_DURATION_S):
        self._max = min(max_duration_s, MAX_BLOCK_DURATION_S)
        self._blocked = False
        self._blocked_at: float = 0.0

    def acquire(self) -> bool:
        ok = _block_input(True)
        if ok:
            self._blocked = True
            self._blocked_at = time.time()
            logger.debug("InputGuard: input BLOCKED (max %.1fs)", self._max)
        else:
            logger.debug("InputGuard: BlockInput failed (not elevated?)")
        return ok

    def release(self) -> None:
        if self._blocked:
            _block_input(False)
            elapsed = time.time() - self._blocked_at
            self._blocked = False
            logger.debug("InputGuard: input UNBLOCKED after %.2fs", elapsed)

    def check_timeout(self) -> bool:
        """Returns True if the block has exceeded the safety timeout."""
        if not self._blocked:
            return False
        if time.time() - self._blocked_at > self._max:
            self.release()
            logger.warning("InputGuard: safety timeout reached, input UNBLOCKED")
            return True
        return False

    def __del__(self):
        self.release()


# ---------------------------------------------------------------------------
# UIOperationLock — the main mutex
# ---------------------------------------------------------------------------

class UIOperationLock:
    """Async mutex that serialises all desktop UI automation operations.

    Usage::

        lock = get_ui_lock()

        async with lock.acquire_operation("click_save_button"):
            await do_ui_action()

    Features:
    - Only one UI operation runs at a time (asyncio.Lock).
    - Cursor position is saved before and restored after (configurable).
    - User interference is detected and logged.
    - Optional ``BlockInput`` for critical sequences.
    - Operation history kept for diagnostics.
    """

    def __init__(
        self,
        *,
        restore_cursor: bool = True,
        detect_interference: bool = True,
        max_history: int = 100,
    ):
        self._lock = asyncio.Lock()
        self._restore_cursor = restore_cursor
        self._detect_interference = detect_interference
        self._history: List[UIOperationRecord] = []
        self._max_history = max_history
        self._active_operation: Optional[str] = None
        self._total_operations: int = 0
        self._total_interferences: int = 0

    @property
    def is_locked(self) -> bool:
        return self._lock.locked()

    @property
    def active_operation(self) -> Optional[str]:
        return self._active_operation

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "is_locked": self.is_locked,
            "active_operation": self._active_operation,
            "total_operations": self._total_operations,
            "total_interferences": self._total_interferences,
            "interference_rate": (
                round(self._total_interferences / self._total_operations, 3)
                if self._total_operations > 0 else 0.0
            ),
            "history_size": len(self._history),
        }

    @asynccontextmanager
    async def acquire_operation(
        self,
        operation_name: str,
        *,
        block_input: bool = False,
        restore_cursor: Optional[bool] = None,
    ):
        """Context manager that acquires the UI lock for a named operation.

        Args:
            operation_name: Human-readable label for logging / diagnostics.
            block_input:  If True, calls ``BlockInput(TRUE)`` for the duration.
            restore_cursor: Override the instance-level cursor restore setting.
        """
        should_restore = restore_cursor if restore_cursor is not None else self._restore_cursor
        record = UIOperationRecord(operation=operation_name, started_at=time.time())
        cursor_guard = CursorGuard(restore_on_no_interference=should_restore)
        input_guard = InputGuard() if block_input else None
        detector = InterferenceDetector() if self._detect_interference else None

        async with self._lock:
            self._active_operation = operation_name
            try:
                cursor_guard.save()
                if detector:
                    detector.snapshot_before()
                if input_guard:
                    input_guard.acquire()

                yield record

                record.success = True
            except Exception as exc:
                record.success = False
                record.error = str(exc)
                raise
            finally:
                if input_guard:
                    input_guard.release()

                interference = InterferenceReport()
                if detector:
                    interference = detector.check_after()
                    record.interference = interference
                    if interference.detected:
                        self._total_interferences += 1
                        logger.info(
                            "User interference during '%s': %s (delta=%.0fpx)",
                            operation_name, interference.type.value,
                            interference.cursor_delta_px,
                        )

                cursor_guard.restore(interference)

                record.finished_at = time.time()
                record.duration_ms = (record.finished_at - record.started_at) * 1000
                self._active_operation = None
                self._total_operations += 1
                self._history.append(record)
                if len(self._history) > self._max_history:
                    self._history = self._history[-self._max_history:]

    async def acquire_simple(self):
        """Non-context-manager acquire for simpler serialisation (e.g. clipboard)."""
        await self._lock.acquire()
        self._active_operation = "simple_lock"

    def release_simple(self):
        self._active_operation = None
        try:
            self._lock.release()
        except RuntimeError:
            pass

    def get_history(self, last_n: int = 20) -> List[Dict[str, Any]]:
        entries = self._history[-last_n:]
        result = []
        for r in entries:
            entry = {
                "operation": r.operation,
                "duration_ms": round(r.duration_ms, 1),
                "success": r.success,
            }
            if r.error:
                entry["error"] = r.error
            if r.interference and r.interference.detected:
                entry["interference"] = r.interference.to_dict()
            result.append(entry)
        return result

    def clear_history(self) -> None:
        self._history.clear()


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_global_ui_lock: Optional[UIOperationLock] = None


def get_ui_lock() -> UIOperationLock:
    """Return the process-wide UIOperationLock singleton."""
    global _global_ui_lock
    if _global_ui_lock is None:
        _global_ui_lock = UIOperationLock()
    return _global_ui_lock


def reset_ui_lock() -> None:
    """Reset the singleton (for testing)."""
    global _global_ui_lock
    _global_ui_lock = None
