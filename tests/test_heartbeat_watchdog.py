# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tests for core/heartbeat.py, core/process_watchdog.py, tools/tool_heartbeat.py
and integration with canonical_tools.py + tool_registry.py."""
from __future__ import annotations

import asyncio
import sys
import time

import pytest

sys.path.insert(0, "core")
sys.path.insert(0, "tools")
sys.path.insert(0, "models")

from heartbeat import HeartbeatEmitter, HeartbeatStatus, ProgressState, ProgressTracker
from process_watchdog import (
    FrozenWindowDetector,
    ProcessWatchdog,
    RecoveryAction,
    RecoveryResult,
    WatchdogPolicy,
    WatchStatus,
    WatchTarget,
    _is_process_alive,
)


# =========================================================================
# HeartbeatStatus
# =========================================================================

class TestHeartbeatStatus:
    def test_to_dict(self):
        s = HeartbeatStatus(tool="shell", run_id="r1", task_id="t1", alive=True, elapsed_ms=100)
        d = s.to_dict()
        assert d["tool"] == "shell"
        assert d["run_id"] == "r1"
        assert d["alive"] is True
        assert d["elapsed_ms"] == 100

    def test_defaults(self):
        s = HeartbeatStatus(tool="x", run_id="y")
        assert s.alive is True
        assert s.stale is False
        assert s.beat_count == 0


# =========================================================================
# ProgressState
# =========================================================================

class TestProgressState:
    def test_to_dict(self):
        s = ProgressState(tool="shell", run_id="r1", percent=42.567)
        d = s.to_dict()
        assert d["tool"] == "shell"
        assert d["percent"] == 42.57

    def test_elapsed_ms(self):
        s = ProgressState(tool="x", run_id="y")
        time.sleep(0.05)
        assert s.elapsed_ms >= 40


# =========================================================================
# HeartbeatEmitter
# =========================================================================

class TestHeartbeatEmitter:
    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        emitter = HeartbeatEmitter("shell", "r1", interval=0.5)
        emitter.start()
        assert emitter.is_running
        await asyncio.sleep(1.3)
        emitter.stop()
        assert not emitter.is_running
        assert emitter.status.beat_count >= 2

    @pytest.mark.asyncio
    async def test_double_start_is_noop(self):
        emitter = HeartbeatEmitter("x", "y", interval=0.1)
        emitter.start()
        task1 = emitter._task
        emitter.start()
        assert emitter._task is task1
        emitter.stop()

    @pytest.mark.asyncio
    async def test_touch_resets_stale(self):
        emitter = HeartbeatEmitter("x", "y", interval=60, stale_threshold=0.05)
        emitter.start()
        await asyncio.sleep(0.1)
        assert emitter.status.stale is True
        emitter.touch()
        assert emitter.status.stale is False
        emitter.stop()

    @pytest.mark.asyncio
    async def test_callback_invoked(self):
        received = []
        emitter = HeartbeatEmitter(
            "shell", "r1", interval=0.5,
            on_heartbeat=lambda s: received.append(s),
        )
        emitter.start()
        await asyncio.sleep(1.3)
        emitter.stop()
        assert len(received) >= 2
        assert all(isinstance(s, HeartbeatStatus) for s in received)

    @pytest.mark.asyncio
    async def test_event_bus_integration(self):
        from event_bus import EventBus, EventType

        bus = EventBus()
        events = []

        async def capture(event):
            events.append(event)

        bus.subscribe(EventType.CUSTOM, capture)

        emitter = HeartbeatEmitter("shell", "r1", interval=0.5, event_bus=bus)
        emitter.start()
        await asyncio.sleep(1.3)
        emitter.stop()

        assert len(events) >= 2
        assert all("heartbeat" in e.payload.get("_original_type", "") for e in events)

    @pytest.mark.asyncio
    async def test_status_reflects_alive(self):
        emitter = HeartbeatEmitter("x", "y", interval=0.1)
        assert emitter.status.alive is False
        emitter.start()
        assert emitter.status.alive is True
        emitter.stop()
        await asyncio.sleep(0.05)
        assert emitter.status.alive is False

    @pytest.mark.asyncio
    async def test_elapsed_ms_grows(self):
        emitter = HeartbeatEmitter("x", "y", interval=0.1)
        emitter.start()
        await asyncio.sleep(0.2)
        assert emitter.status.elapsed_ms >= 150
        emitter.stop()

    @pytest.mark.asyncio
    async def test_min_interval_clamp(self):
        emitter = HeartbeatEmitter("x", "y", interval=0.1)
        assert emitter.interval >= 0.5

        emitter2 = HeartbeatEmitter("x", "y", interval=1.0)
        assert emitter2.interval == 1.0


# =========================================================================
# ProgressTracker
# =========================================================================

class TestProgressTracker:
    def test_single_phase_progress(self):
        tracker = ProgressTracker("shell", "r1")
        tracker.start_phase("run")
        tracker.update(50.0, "halfway")
        assert tracker.state.percent == 50.0
        assert tracker.state.message == "halfway"

    def test_multi_phase_progress(self):
        tracker = ProgressTracker("shell", "r1", phases=["download", "install", "verify"])
        tracker.start_phase("download")
        assert tracker.state.phase_index == 0

        tracker.update(50.0, "downloading...")
        expected_pct = (0 / 3) * 100 + (50.0 / 100.0) * (100.0 / 3)
        assert abs(tracker.state.percent - expected_pct) < 0.1

        tracker.start_phase("install")
        assert tracker.state.phase_index == 1

    def test_complete(self):
        tracker = ProgressTracker("shell", "r1")
        tracker.update(50.0)
        tracker.complete("Done!")
        assert tracker.state.percent == 100.0
        assert tracker.state.message == "Done!"
        assert tracker.state.eta_ms == 0

    def test_eta_estimation(self):
        tracker = ProgressTracker("shell", "r1")
        tracker.update(50.0, "half done")
        eta = tracker.state.eta_ms
        assert eta is not None
        assert eta >= 0

    def test_callback_invoked(self):
        states = []
        tracker = ProgressTracker("shell", "r1", on_progress=lambda s: states.append(s))
        tracker.start_phase("run")
        tracker.update(50.0)
        tracker.complete()
        assert len(states) == 3

    def test_to_dict(self):
        tracker = ProgressTracker("shell", "r1", phases=["a", "b"])
        tracker.start_phase("a")
        d = tracker.to_dict()
        assert d["tool"] == "shell"
        assert d["total_phases"] == 2

    def test_dynamic_phase_addition(self):
        tracker = ProgressTracker("shell", "r1", phases=["a"])
        tracker.start_phase("b")
        assert "b" in tracker.phases
        assert tracker.state.total_phases == 2

    def test_update_no_phase(self):
        tracker = ProgressTracker("shell", "r1")
        tracker.update(30.0, "msg")
        assert tracker.state.percent == 30.0

    def test_update_with_explicit_eta(self):
        tracker = ProgressTracker("shell", "r1")
        tracker.update(50.0, eta_ms=5000)
        assert tracker.state.eta_ms == 5000

    def test_percent_capped_at_100(self):
        tracker = ProgressTracker("shell", "r1")
        tracker.update(200.0)
        assert tracker.state.percent == 100.0


# =========================================================================
# WatchTarget
# =========================================================================

class TestWatchTarget:
    def test_to_dict(self):
        t = WatchTarget(pid=123, process_name="excel.exe")
        d = t.to_dict()
        assert d["pid"] == 123
        assert d["process_name"] == "excel.exe"


# =========================================================================
# WatchStatus
# =========================================================================

class TestWatchStatus:
    def test_to_dict(self):
        t = WatchTarget(pid=1)
        s = WatchStatus(target=t, frozen=True, frozen_duration_ms=5000)
        d = s.to_dict()
        assert d["frozen"] is True
        assert d["frozen_duration_ms"] == 5000


# =========================================================================
# WatchdogPolicy
# =========================================================================

class TestWatchdogPolicy:
    def test_defaults(self):
        p = WatchdogPolicy()
        assert p.check_interval == 5.0
        assert p.freeze_threshold_ms == 10_000
        assert p.max_recovery_attempts == 3
        assert len(p.recovery_sequence) == 3

    def test_to_dict(self):
        p = WatchdogPolicy()
        d = p.to_dict()
        assert d["auto_restart"] is False
        assert "retry_message" in d["recovery_sequence"]


# =========================================================================
# RecoveryResult
# =========================================================================

class TestRecoveryResult:
    def test_to_dict(self):
        r = RecoveryResult(action=RecoveryAction.KILL, success=True, message="killed")
        d = r.to_dict()
        assert d["action"] == "kill"
        assert d["success"] is True


# =========================================================================
# FrozenWindowDetector
# =========================================================================

class TestFrozenWindowDetector:
    def test_check_no_hwnd(self):
        d = FrozenWindowDetector()
        result = d.check(0)
        assert result["frozen"] is False
        assert result["error"] == "no hwnd"

    def test_check_by_title_no_win32(self):
        d = FrozenWindowDetector()
        results = d.check_by_title("Notepad")
        assert isinstance(results, list)

    def test_check_by_pid_no_win32(self):
        d = FrozenWindowDetector()
        results = d.check_by_pid(9999)
        assert isinstance(results, list)


# =========================================================================
# ProcessWatchdog
# =========================================================================

class TestProcessWatchdog:
    def test_add_and_remove_target(self):
        wd = ProcessWatchdog()
        key = wd.add_target(WatchTarget(pid=123))
        assert key == "pid:123"
        assert key in wd.get_statuses()
        wd.remove_target(key)
        assert key not in wd.get_statuses()

    def test_key_by_hwnd(self):
        wd = ProcessWatchdog()
        key = wd.add_target(WatchTarget(hwnd=999))
        assert key == "hwnd:999"

    def test_key_by_name(self):
        wd = ProcessWatchdog()
        key = wd.add_target(WatchTarget(process_name="notepad.exe"))
        assert key == "name:notepad.exe"

    def test_key_by_title(self):
        wd = ProcessWatchdog()
        key = wd.add_target(WatchTarget(title_pattern="Untitled"))
        assert key == "name:Untitled"

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        wd = ProcessWatchdog(policy=WatchdogPolicy(check_interval=0.1))
        wd.start()
        assert wd.is_running
        await asyncio.sleep(0.1)
        wd.stop()
        assert not wd.is_running

    @pytest.mark.asyncio
    async def test_check_once_no_targets(self):
        wd = ProcessWatchdog()
        statuses = await wd.check_once()
        assert statuses == {}

    @pytest.mark.asyncio
    async def test_check_once_dead_process(self):
        wd = ProcessWatchdog()
        wd.add_target(WatchTarget(pid=99999999))
        statuses = await wd.check_once()
        status = list(statuses.values())[0]
        assert status.alive is False

    @pytest.mark.asyncio
    async def test_check_once_with_title_no_match(self):
        wd = ProcessWatchdog()
        wd.add_target(WatchTarget(title_pattern="NONEXISTENT_WINDOW_TITLE_XYZ"))
        statuses = await wd.check_once()
        status = list(statuses.values())[0]
        assert status.responsive is True

    @pytest.mark.asyncio
    async def test_recovery_callbacks(self):
        frozen_events = []
        recovery_events = []

        wd = ProcessWatchdog(
            policy=WatchdogPolicy(freeze_threshold_ms=0, max_recovery_attempts=1,
                                  recovery_sequence=[RecoveryAction.RETRY_MESSAGE]),
            on_frozen=lambda s: frozen_events.append(s),
            on_recovery=lambda r: recovery_events.append(r),
        )
        target = WatchTarget(hwnd=0, pid=99999999)
        wd.add_target(target)
        await wd.check_once()
        assert isinstance(frozen_events, list)

    @pytest.mark.asyncio
    async def test_event_bus_frozen_emission(self):
        from event_bus import EventBus, EventType

        bus = EventBus()
        events = []

        async def capture(event):
            events.append(event)

        bus.subscribe(EventType.CUSTOM, capture)

        wd = ProcessWatchdog(
            policy=WatchdogPolicy(freeze_threshold_ms=0, check_interval=0.1),
            event_bus=bus,
        )
        wd.add_target(WatchTarget(pid=99999999))
        await wd.check_once()

    def test_get_status_unknown(self):
        wd = ProcessWatchdog()
        assert wd.get_status("nonexistent") is None

    @pytest.mark.asyncio
    async def test_double_start(self):
        wd = ProcessWatchdog(policy=WatchdogPolicy(check_interval=0.1))
        wd.start()
        task1 = wd._task
        wd.start()
        assert wd._task is task1
        wd.stop()


# =========================================================================
# RecoveryAction enum
# =========================================================================

class TestRecoveryAction:
    def test_values(self):
        assert RecoveryAction.NONE.value == "none"
        assert RecoveryAction.RETRY_MESSAGE.value == "retry_message"
        assert RecoveryAction.CLOSE_GRACEFUL.value == "close_graceful"
        assert RecoveryAction.KILL.value == "kill"
        assert RecoveryAction.RESTART.value == "restart"


# =========================================================================
# _is_process_alive
# =========================================================================

class TestIsProcessAlive:
    def test_current_process_alive(self):
        import os
        assert _is_process_alive(os.getpid()) is True

    def test_nonexistent_process(self):
        assert _is_process_alive(99999999) is False


# =========================================================================
# Tool facade — HeartbeatTool
# =========================================================================

class TestHeartbeatTool:
    @pytest.fixture
    def tool(self):
        from tool_heartbeat import HeartbeatTool
        return HeartbeatTool()

    @pytest.mark.asyncio
    async def test_start_heartbeat(self, tool):
        from tool_heartbeat import HeartbeatRequest
        req = HeartbeatRequest(action="start_heartbeat", tool_name="shell", run_id="r1")
        result = await tool._run(req)
        assert result.status == "succeeded"
        assert "shell:r1" in result.data["key"]

        req2 = HeartbeatRequest(action="stop_heartbeat", tool_name="shell", run_id="r1")
        result2 = await tool._run(req2)
        assert result2.status == "succeeded"

    @pytest.mark.asyncio
    async def test_start_heartbeat_missing_params(self, tool):
        from tool_heartbeat import HeartbeatRequest
        req = HeartbeatRequest(action="start_heartbeat")
        result = await tool._run(req)
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_heartbeat_status(self, tool):
        from tool_heartbeat import HeartbeatRequest
        await tool._run(HeartbeatRequest(action="start_heartbeat", tool_name="s", run_id="r"))
        req = HeartbeatRequest(action="heartbeat_status", tool_name="s", run_id="r")
        result = await tool._run(req)
        assert result.status == "succeeded"
        assert result.data["alive"] is True
        await tool._run(HeartbeatRequest(action="stop_heartbeat", tool_name="s", run_id="r"))

    @pytest.mark.asyncio
    async def test_heartbeat_status_not_found(self, tool):
        from tool_heartbeat import HeartbeatRequest
        req = HeartbeatRequest(action="heartbeat_status", tool_name="x", run_id="y")
        result = await tool._run(req)
        assert result.status == "failed"

    @pytest.mark.asyncio
    async def test_touch(self, tool):
        from tool_heartbeat import HeartbeatRequest
        await tool._run(HeartbeatRequest(action="start_heartbeat", tool_name="s", run_id="r"))
        req = HeartbeatRequest(action="touch", tool_name="s", run_id="r")
        result = await tool._run(req)
        assert result.status == "succeeded"
        await tool._run(HeartbeatRequest(action="stop_heartbeat", tool_name="s", run_id="r"))

    @pytest.mark.asyncio
    async def test_progress_lifecycle(self, tool):
        from tool_heartbeat import HeartbeatRequest
        r1 = await tool._run(HeartbeatRequest(
            action="start_progress", tool_name="shell", run_id="r1",
            phases=["a", "b"], phase="a",
        ))
        assert r1.status == "succeeded"

        r2 = await tool._run(HeartbeatRequest(
            action="update_progress", tool_name="shell", run_id="r1",
            percent=50.0, message="halfway",
        ))
        assert r2.status == "succeeded"

        r3 = await tool._run(HeartbeatRequest(
            action="progress_status", tool_name="shell", run_id="r1",
        ))
        assert r3.status == "succeeded"

        r4 = await tool._run(HeartbeatRequest(
            action="complete_progress", tool_name="shell", run_id="r1",
        ))
        assert r4.status == "succeeded"

    @pytest.mark.asyncio
    async def test_progress_not_found(self, tool):
        from tool_heartbeat import HeartbeatRequest
        r = await tool._run(HeartbeatRequest(
            action="update_progress", tool_name="x", run_id="y",
        ))
        assert r.status == "failed"

    @pytest.mark.asyncio
    async def test_progress_new_phase(self, tool):
        from tool_heartbeat import HeartbeatRequest
        await tool._run(HeartbeatRequest(
            action="start_progress", tool_name="s", run_id="r", phases=["a"],
        ))
        r = await tool._run(HeartbeatRequest(
            action="update_progress", tool_name="s", run_id="r",
            phase="b", message="new phase",
        ))
        assert r.status == "succeeded"

    @pytest.mark.asyncio
    async def test_watchdog_lifecycle(self, tool):
        from tool_heartbeat import HeartbeatRequest
        r1 = await tool._run(HeartbeatRequest(
            action="add_watch_target", pid=99999999, process_name="fake.exe",
        ))
        assert r1.status == "succeeded"
        key = r1.data["key"]

        r2 = await tool._run(HeartbeatRequest(action="start_watchdog"))
        assert r2.status == "succeeded"

        r3 = await tool._run(HeartbeatRequest(action="watchdog_status"))
        assert r3.status == "succeeded"
        assert r3.data["running"] is True

        r4 = await tool._run(HeartbeatRequest(action="check_once"))
        assert r4.status == "succeeded"

        r5 = await tool._run(HeartbeatRequest(
            action="remove_watch_target", target_key=key,
        ))
        assert r5.status == "succeeded"

        r6 = await tool._run(HeartbeatRequest(action="stop_watchdog"))
        assert r6.status == "succeeded"

    @pytest.mark.asyncio
    async def test_check_frozen_by_pid(self, tool):
        from tool_heartbeat import HeartbeatRequest
        r = await tool._run(HeartbeatRequest(
            action="check_frozen", pid=99999999,
        ))
        assert r.status == "succeeded"

    @pytest.mark.asyncio
    async def test_check_frozen_by_title(self, tool):
        from tool_heartbeat import HeartbeatRequest
        r = await tool._run(HeartbeatRequest(
            action="check_frozen", title_pattern="NONEXISTENT_XYZ",
        ))
        assert r.status == "succeeded"

    @pytest.mark.asyncio
    async def test_check_frozen_missing_params(self, tool):
        from tool_heartbeat import HeartbeatRequest
        r = await tool._run(HeartbeatRequest(action="check_frozen"))
        assert r.status == "failed"

    @pytest.mark.asyncio
    async def test_watchdog_status_no_watchdog(self, tool):
        from tool_heartbeat import HeartbeatRequest
        r = await tool._run(HeartbeatRequest(action="watchdog_status"))
        assert r.status == "succeeded"
        assert r.data["running"] is False

    @pytest.mark.asyncio
    async def test_check_once_no_watchdog(self, tool):
        from tool_heartbeat import HeartbeatRequest
        r = await tool._run(HeartbeatRequest(action="check_once"))
        assert r.status == "failed"

    @pytest.mark.asyncio
    async def test_unknown_action(self, tool):
        from tool_heartbeat import HeartbeatRequest
        try:
            HeartbeatRequest(action="nonexistent")
            assert False, "Should have raised"
        except Exception:
            pass


# =========================================================================
# Canonical tools integration
# =========================================================================

class TestCanonicalIntegration:
    def test_heartbeat_in_supported_tools(self):
        from canonical_tools import SUPPORTED_TOOL_NAMES
        assert "heartbeat" in SUPPORTED_TOOL_NAMES

    def test_heartbeat_metadata_exists(self):
        from canonical_tools import ACTION_METADATA
        assert "heartbeat" in ACTION_METADATA
        assert "start_heartbeat" in ACTION_METADATA["heartbeat"]
        assert "check_frozen" in ACTION_METADATA["heartbeat"]
        assert "add_watch_target" in ACTION_METADATA["heartbeat"]

    def test_tool_definitions_include_heartbeat(self):
        from canonical_tools import tool_definitions
        defs = tool_definitions()
        names = [d["function"]["name"] for d in defs]
        assert "heartbeat" in names

    def test_task_title(self):
        from canonical_tools import task_title
        t = task_title("heartbeat", "start_heartbeat", {"tool_name": "shell"})
        assert "Start Heartbeat" in t
        assert "shell" in t

    def test_normalize_agentic_task(self):
        from canonical_tools import normalize_agentic_task
        result = normalize_agentic_task("heartbeat", {
            "action": "start_heartbeat",
            "tool_name": "shell",
            "run_id": "r1",
            "interval": 5.0,
        }, task_id="test-1")
        assert result["tool"] == "heartbeat"
        assert result["action"] == "start_heartbeat"
        assert result["params"]["tool_name"] == "shell"


# =========================================================================
# Tool registry integration
# =========================================================================

class TestToolRegistryIntegration:
    def test_heartbeat_registered(self):
        from tool_registry import ToolRegistry
        reg = ToolRegistry.__new__(ToolRegistry)
        from tool_heartbeat import HeartbeatTool
        reg.tools = {"heartbeat": HeartbeatTool()}
        assert reg.supports("heartbeat")
