# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Heartbeat and incremental progress for long-running tools.

Provides:
1. **HeartbeatEmitter** — a background async task that emits periodic heartbeat
   events via the EventBus while a tool/operation is running.
2. **ProgressTracker** — standardized incremental progress reporting with phases,
   percentage, and ETA estimation.
3. **ToolHeartbeatMixin** — mixin for BaseTool subclasses to add automatic
   heartbeat support during ``_run`` execution.

Events emitted:
- ``heartbeat`` — periodic signal that a tool is alive
- ``progress_update`` — incremental progress change (phase, pct, message, ETA)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class HeartbeatStatus:
    tool: str
    run_id: str
    task_id: str = ""
    alive: bool = True
    elapsed_ms: int = 0
    last_beat: float = 0.0
    beat_count: int = 0
    stale: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool, "run_id": self.run_id, "task_id": self.task_id,
            "alive": self.alive, "elapsed_ms": self.elapsed_ms,
            "last_beat": self.last_beat, "beat_count": self.beat_count,
            "stale": self.stale,
        }


@dataclass
class ProgressState:
    tool: str
    run_id: str
    task_id: str = ""
    phase: str = ""
    phase_index: int = 0
    total_phases: int = 0
    percent: float = 0.0
    message: str = ""
    eta_ms: Optional[int] = None
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def elapsed_ms(self) -> int:
        return int((time.time() - self.started_at) * 1000)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool, "run_id": self.run_id, "task_id": self.task_id,
            "phase": self.phase, "phase_index": self.phase_index,
            "total_phases": self.total_phases,
            "percent": round(self.percent, 2),
            "message": self.message,
            "eta_ms": self.eta_ms,
            "elapsed_ms": self.elapsed_ms,
        }


# ---------------------------------------------------------------------------
# HeartbeatEmitter
# ---------------------------------------------------------------------------

class HeartbeatEmitter:
    """Emits periodic heartbeat events for a long-running operation.

    Usage::

        emitter = HeartbeatEmitter("shell", "run-1", interval=5.0)
        emitter.start()        # begins async heartbeat loop
        # ... long operation ...
        emitter.stop()         # stops cleanly
    """

    def __init__(
        self,
        tool: str,
        run_id: str,
        *,
        task_id: str = "",
        interval: float = 5.0,
        stale_threshold: float = 30.0,
        on_heartbeat: Optional[Callable[["HeartbeatStatus"], Any]] = None,
        event_bus=None,
    ):
        self.tool = tool
        self.run_id = run_id
        self.task_id = task_id
        self.interval = max(interval, 0.5)
        self.stale_threshold = stale_threshold
        self._on_heartbeat = on_heartbeat
        self._event_bus = event_bus
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._start_time = 0.0
        self._last_beat = 0.0
        self._beat_count = 0
        self._alive = False

    @property
    def is_running(self) -> bool:
        return self._alive and self._task is not None and not self._task.done()

    @property
    def status(self) -> HeartbeatStatus:
        elapsed = int((time.time() - self._start_time) * 1000) if self._start_time else 0
        stale = (time.time() - self._last_beat > self.stale_threshold) if self._last_beat else False
        return HeartbeatStatus(
            tool=self.tool, run_id=self.run_id, task_id=self.task_id,
            alive=self._alive, elapsed_ms=elapsed,
            last_beat=self._last_beat, beat_count=self._beat_count,
            stale=stale,
        )

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._start_time = time.time()
        self._last_beat = self._start_time
        self._alive = True
        self._beat_count = 0
        self._task = asyncio.ensure_future(self._loop())

    def stop(self) -> None:
        self._alive = False
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    def touch(self) -> None:
        """Manual heartbeat touch — resets the stale timer."""
        self._last_beat = time.time()
        self._beat_count += 1

    async def _loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                self.touch()
                await self._emit()
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.interval,
                    )
                    break
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass
        finally:
            self._alive = False

    async def _emit(self) -> None:
        status = self.status
        if self._on_heartbeat:
            try:
                result = self._on_heartbeat(status)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.debug("Heartbeat callback failed", exc_info=True)

        if self._event_bus:
            try:
                await self._event_bus.emit_raw(
                    "heartbeat",
                    status.to_dict(),
                    source=f"heartbeat:{self.tool}",
                )
            except Exception:
                logger.debug("Heartbeat event emission failed", exc_info=True)


# ---------------------------------------------------------------------------
# ProgressTracker
# ---------------------------------------------------------------------------

class ProgressTracker:
    """Tracks incremental progress for a multi-phase operation.

    Usage::

        tracker = ProgressTracker("shell", "run-1", phases=["download", "install", "verify"])
        tracker.start_phase("download")
        tracker.update(25.0, "downloading package...")
        tracker.update(100.0, "download complete")
        tracker.start_phase("install")
        ...
    """

    def __init__(
        self,
        tool: str,
        run_id: str,
        *,
        task_id: str = "",
        phases: Optional[List[str]] = None,
        on_progress: Optional[Callable[["ProgressState"], Any]] = None,
        event_bus=None,
    ):
        self.tool = tool
        self.run_id = run_id
        self.task_id = task_id
        self.phases = phases or []
        self._on_progress = on_progress
        self._event_bus = event_bus
        self._state = ProgressState(
            tool=tool, run_id=run_id, task_id=task_id,
            total_phases=len(self.phases),
        )
        self._phase_start_times: Dict[str, float] = {}

    @property
    def state(self) -> ProgressState:
        return self._state

    def start_phase(self, phase: str, message: str = "") -> ProgressState:
        self._state.phase = phase
        self._state.message = message or f"Starting {phase}"
        self._state.updated_at = time.time()
        self._phase_start_times[phase] = time.time()

        if phase in self.phases:
            self._state.phase_index = self.phases.index(phase)
            base_pct = (self._state.phase_index / max(len(self.phases), 1)) * 100
            self._state.percent = base_pct
        else:
            self.phases.append(phase)
            self._state.total_phases = len(self.phases)
            self._state.phase_index = len(self.phases) - 1

        self._notify()
        return self._state

    def update(
        self,
        percent: Optional[float] = None,
        message: str = "",
        *,
        eta_ms: Optional[int] = None,
    ) -> ProgressState:
        if percent is not None:
            if self.phases and len(self.phases) > 1:
                phase_weight = 100.0 / len(self.phases)
                base = self._state.phase_index * phase_weight
                self._state.percent = min(base + (percent / 100.0) * phase_weight, 100.0)
            else:
                self._state.percent = min(percent, 100.0)
        if message:
            self._state.message = message
        if eta_ms is not None:
            self._state.eta_ms = eta_ms
        else:
            self._state.eta_ms = self._estimate_eta()
        self._state.updated_at = time.time()
        self._notify()
        return self._state

    def complete(self, message: str = "Completed") -> ProgressState:
        self._state.percent = 100.0
        self._state.message = message
        self._state.eta_ms = 0
        self._state.updated_at = time.time()
        self._notify()
        return self._state

    def _estimate_eta(self) -> Optional[int]:
        if self._state.percent <= 0:
            return None
        elapsed = self._state.elapsed_ms
        if self._state.percent >= 100:
            return 0
        remaining_pct = 100.0 - self._state.percent
        rate = self._state.percent / max(elapsed, 1)
        if rate <= 0:
            return None
        return int(remaining_pct / rate)

    def _notify(self) -> None:
        if self._on_progress:
            try:
                result = self._on_progress(self._state)
                if asyncio.iscoroutine(result):
                    asyncio.ensure_future(result)
            except Exception:
                logger.debug("Progress callback failed", exc_info=True)

        if self._event_bus:
            try:
                asyncio.ensure_future(
                    self._event_bus.emit_raw(
                        "progress_update",
                        self._state.to_dict(),
                        source=f"progress:{self.tool}",
                    )
                )
            except Exception:
                logger.debug("Progress event emission failed", exc_info=True)

    def to_dict(self) -> Dict[str, Any]:
        return self._state.to_dict()
