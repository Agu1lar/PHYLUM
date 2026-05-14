# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tool facade for heartbeat, progress tracking, and process watchdog."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from action_models import ActionResult
from heartbeat import HeartbeatEmitter, ProgressTracker
from process_watchdog import (
    FrozenWindowDetector,
    ProcessWatchdog,
    RecoveryAction,
    WatchdogPolicy,
    WatchTarget,
)
from tool_base import BaseTool

logger = logging.getLogger(__name__)


class HeartbeatRequest(BaseModel):
    action: str = Field(
        ...,
        pattern="^(start_heartbeat|stop_heartbeat|heartbeat_status|touch|"
                "start_progress|update_progress|complete_progress|progress_status|"
                "add_watch_target|remove_watch_target|start_watchdog|stop_watchdog|"
                "check_frozen|watchdog_status|check_once)$",
    )
    # Heartbeat fields
    tool_name: Optional[str] = None
    run_id: Optional[str] = None
    task_id: Optional[str] = None
    interval: Optional[float] = None
    # Progress fields
    phases: Optional[List[str]] = None
    phase: Optional[str] = None
    percent: Optional[float] = None
    message: Optional[str] = None
    eta_ms: Optional[int] = None
    # Watchdog fields
    pid: Optional[int] = None
    hwnd: Optional[int] = None
    process_name: Optional[str] = None
    title_pattern: Optional[str] = None
    restart_command: Optional[str] = None
    check_interval: Optional[float] = None
    freeze_threshold_ms: Optional[int] = None
    max_recovery_attempts: Optional[int] = None
    auto_restart: bool = False
    target_key: Optional[str] = None


class HeartbeatTool(BaseTool):
    InputModel = HeartbeatRequest

    def __init__(self):
        super().__init__(default_timeout=30, default_retries=1)
        self._emitters: Dict[str, HeartbeatEmitter] = {}
        self._trackers: Dict[str, ProgressTracker] = {}
        self._watchdog: Optional[ProcessWatchdog] = None
        self._detector = FrozenWindowDetector()

    def _emitter_key(self, tool_name: str, run_id: str) -> str:
        return f"{tool_name}:{run_id}"

    async def _run(self, payload: HeartbeatRequest) -> ActionResult:
        action = payload.action
        try:
            # --- Heartbeat actions ---
            if action == "start_heartbeat":
                if not payload.tool_name or not payload.run_id:
                    return ActionResult(status="failed", summary="'tool_name' and 'run_id' required",
                                        tool="heartbeat", action=action)
                key = self._emitter_key(payload.tool_name, payload.run_id)
                emitter = HeartbeatEmitter(
                    payload.tool_name, payload.run_id,
                    task_id=payload.task_id or "",
                    interval=payload.interval or 5.0,
                )
                emitter.start()
                self._emitters[key] = emitter
                return ActionResult(
                    status="succeeded",
                    summary=f"Started heartbeat for {payload.tool_name}:{payload.run_id}",
                    tool="heartbeat", action=action,
                    data={"key": key, "interval": emitter.interval},
                )

            if action == "stop_heartbeat":
                if not payload.tool_name or not payload.run_id:
                    return ActionResult(status="failed", summary="'tool_name' and 'run_id' required",
                                        tool="heartbeat", action=action)
                key = self._emitter_key(payload.tool_name, payload.run_id)
                emitter = self._emitters.pop(key, None)
                if emitter:
                    emitter.stop()
                return ActionResult(
                    status="succeeded",
                    summary=f"Stopped heartbeat for {key}",
                    tool="heartbeat", action=action,
                )

            if action == "heartbeat_status":
                if not payload.tool_name or not payload.run_id:
                    return ActionResult(status="failed", summary="'tool_name' and 'run_id' required",
                                        tool="heartbeat", action=action)
                key = self._emitter_key(payload.tool_name, payload.run_id)
                emitter = self._emitters.get(key)
                if not emitter:
                    return ActionResult(status="failed", summary=f"No heartbeat for {key}",
                                        tool="heartbeat", action=action)
                return ActionResult(
                    status="succeeded", summary=f"Heartbeat {key}: alive={emitter.status.alive}",
                    tool="heartbeat", action=action, data=emitter.status.to_dict(),
                )

            if action == "touch":
                if not payload.tool_name or not payload.run_id:
                    return ActionResult(status="failed", summary="'tool_name' and 'run_id' required",
                                        tool="heartbeat", action=action)
                key = self._emitter_key(payload.tool_name, payload.run_id)
                emitter = self._emitters.get(key)
                if emitter:
                    emitter.touch()
                return ActionResult(
                    status="succeeded", summary=f"Touched heartbeat {key}",
                    tool="heartbeat", action=action,
                )

            # --- Progress actions ---
            if action == "start_progress":
                if not payload.tool_name or not payload.run_id:
                    return ActionResult(status="failed", summary="'tool_name' and 'run_id' required",
                                        tool="heartbeat", action=action)
                key = self._emitter_key(payload.tool_name, payload.run_id)
                tracker = ProgressTracker(
                    payload.tool_name, payload.run_id,
                    task_id=payload.task_id or "",
                    phases=payload.phases,
                )
                if payload.phase:
                    tracker.start_phase(payload.phase, payload.message or "")
                self._trackers[key] = tracker
                return ActionResult(
                    status="succeeded",
                    summary=f"Started progress tracking for {key}",
                    tool="heartbeat", action=action,
                    data=tracker.to_dict(),
                )

            if action == "update_progress":
                if not payload.tool_name or not payload.run_id:
                    return ActionResult(status="failed", summary="'tool_name' and 'run_id' required",
                                        tool="heartbeat", action=action)
                key = self._emitter_key(payload.tool_name, payload.run_id)
                tracker = self._trackers.get(key)
                if not tracker:
                    return ActionResult(status="failed", summary=f"No progress tracker for {key}",
                                        tool="heartbeat", action=action)
                if payload.phase:
                    tracker.start_phase(payload.phase, payload.message or "")
                else:
                    tracker.update(payload.percent, payload.message or "", eta_ms=payload.eta_ms)
                return ActionResult(
                    status="succeeded",
                    summary=f"Progress {key}: {tracker.state.percent:.0f}%",
                    tool="heartbeat", action=action,
                    data=tracker.to_dict(),
                )

            if action == "complete_progress":
                if not payload.tool_name or not payload.run_id:
                    return ActionResult(status="failed", summary="'tool_name' and 'run_id' required",
                                        tool="heartbeat", action=action)
                key = self._emitter_key(payload.tool_name, payload.run_id)
                tracker = self._trackers.get(key)
                if tracker:
                    tracker.complete(payload.message or "Completed")
                return ActionResult(
                    status="succeeded", summary=f"Progress {key} completed",
                    tool="heartbeat", action=action,
                )

            if action == "progress_status":
                if not payload.tool_name or not payload.run_id:
                    return ActionResult(status="failed", summary="'tool_name' and 'run_id' required",
                                        tool="heartbeat", action=action)
                key = self._emitter_key(payload.tool_name, payload.run_id)
                tracker = self._trackers.get(key)
                if not tracker:
                    return ActionResult(status="failed", summary=f"No progress tracker for {key}",
                                        tool="heartbeat", action=action)
                return ActionResult(
                    status="succeeded",
                    summary=f"Progress {key}: {tracker.state.percent:.0f}% — {tracker.state.message}",
                    tool="heartbeat", action=action, data=tracker.to_dict(),
                )

            # --- Watchdog actions ---
            if action == "add_watch_target":
                if not self._watchdog:
                    policy_kwargs: Dict[str, Any] = {}
                    if payload.check_interval:
                        policy_kwargs["check_interval"] = payload.check_interval
                    if payload.freeze_threshold_ms:
                        policy_kwargs["freeze_threshold_ms"] = payload.freeze_threshold_ms
                    if payload.max_recovery_attempts:
                        policy_kwargs["max_recovery_attempts"] = payload.max_recovery_attempts
                    policy_kwargs["auto_restart"] = payload.auto_restart
                    self._watchdog = ProcessWatchdog(policy=WatchdogPolicy(**policy_kwargs))

                target = WatchTarget(
                    pid=payload.pid or 0,
                    hwnd=payload.hwnd or 0,
                    process_name=payload.process_name or "",
                    title_pattern=payload.title_pattern or "",
                    restart_command=payload.restart_command or "",
                )
                key = self._watchdog.add_target(target)
                return ActionResult(
                    status="succeeded",
                    summary=f"Added watch target: {key}",
                    tool="heartbeat", action=action,
                    data={"key": key, "target": target.to_dict()},
                )

            if action == "remove_watch_target":
                if self._watchdog and payload.target_key:
                    self._watchdog.remove_target(payload.target_key)
                return ActionResult(
                    status="succeeded",
                    summary=f"Removed watch target: {payload.target_key}",
                    tool="heartbeat", action=action,
                )

            if action == "start_watchdog":
                if not self._watchdog:
                    self._watchdog = ProcessWatchdog()
                self._watchdog.start()
                return ActionResult(
                    status="succeeded", summary="Watchdog started",
                    tool="heartbeat", action=action,
                )

            if action == "stop_watchdog":
                if self._watchdog:
                    self._watchdog.stop()
                return ActionResult(
                    status="succeeded", summary="Watchdog stopped",
                    tool="heartbeat", action=action,
                )

            if action == "check_frozen":
                if payload.hwnd:
                    result = self._detector.check(payload.hwnd)
                elif payload.title_pattern:
                    result = self._detector.check_by_title(payload.title_pattern)
                elif payload.pid:
                    result = self._detector.check_by_pid(payload.pid or 0)
                else:
                    return ActionResult(status="failed", summary="Need hwnd, pid, or title_pattern",
                                        tool="heartbeat", action=action)
                frozen = False
                if isinstance(result, dict):
                    frozen = result.get("frozen", False)
                elif isinstance(result, list):
                    frozen = any(r.get("frozen", False) for r in result)
                return ActionResult(
                    status="succeeded",
                    summary=f"Frozen check: {'FROZEN' if frozen else 'responsive'}",
                    tool="heartbeat", action=action,
                    data=result if isinstance(result, dict) else {"windows": result, "any_frozen": frozen},
                )

            if action == "watchdog_status":
                if not self._watchdog:
                    return ActionResult(status="succeeded", summary="No watchdog running",
                                        tool="heartbeat", action=action, data={"running": False, "targets": []})
                statuses = self._watchdog.get_statuses()
                return ActionResult(
                    status="succeeded",
                    summary=f"Watchdog: {len(statuses)} target(s), running={self._watchdog.is_running}",
                    tool="heartbeat", action=action,
                    data={
                        "running": self._watchdog.is_running,
                        "targets": {k: v.to_dict() for k, v in statuses.items()},
                    },
                )

            if action == "check_once":
                if not self._watchdog:
                    return ActionResult(status="failed", summary="No watchdog configured",
                                        tool="heartbeat", action=action)
                statuses = await self._watchdog.check_once()
                frozen_count = sum(1 for s in statuses.values() if s.frozen)
                return ActionResult(
                    status="succeeded",
                    summary=f"Check: {len(statuses)} target(s), {frozen_count} frozen",
                    tool="heartbeat", action=action,
                    data={k: v.to_dict() for k, v in statuses.items()},
                )

            return ActionResult(status="failed", summary=f"Unknown action: {action}",
                                tool="heartbeat", action=action)

        except Exception as exc:
            return ActionResult(status="failed", summary=str(exc),
                                tool="heartbeat", action=action)
