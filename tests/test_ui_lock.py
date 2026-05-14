# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tests for core/ui_lock.py — UI Operation Lock, cursor guard, interference detection."""
from __future__ import annotations

import asyncio
import sys
import os
import time
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'core'))

from ui_lock import (
    UIOperationLock,
    UIOperationRecord,
    InterferenceDetector,
    InterferenceReport,
    InterferenceType,
    CursorGuard,
    InputGuard,
    MAX_BLOCK_DURATION_S,
    CURSOR_JITTER_THRESHOLD_PX,
    _get_cursor_pos,
    _set_cursor_pos,
    _block_input,
    _get_foreground_window,
    get_ui_lock,
    reset_ui_lock,
)


# ---------------------------------------------------------------------------
# InterferenceType
# ---------------------------------------------------------------------------

class TestInterferenceType:
    def test_values(self):
        assert InterferenceType.NONE.value == "none"
        assert InterferenceType.CURSOR_MOVED.value == "cursor_moved"
        assert InterferenceType.FOCUS_CHANGED.value == "focus_changed"
        assert InterferenceType.CURSOR_AND_FOCUS.value == "cursor_and_focus"


# ---------------------------------------------------------------------------
# InterferenceReport
# ---------------------------------------------------------------------------

class TestInterferenceReport:
    def test_default(self):
        r = InterferenceReport()
        assert r.detected is False
        assert r.type == InterferenceType.NONE
        assert r.cursor_delta_px == 0.0

    def test_to_dict(self):
        r = InterferenceReport(
            detected=True,
            type=InterferenceType.CURSOR_MOVED,
            cursor_before=(100, 200),
            cursor_after=(300, 400),
            cursor_delta_px=282.8,
        )
        d = r.to_dict()
        assert d["detected"] is True
        assert d["type"] == "cursor_moved"
        assert d["cursor_before"] == [100, 200]
        assert d["cursor_after"] == [300, 400]


# ---------------------------------------------------------------------------
# InterferenceDetector
# ---------------------------------------------------------------------------

class TestInterferenceDetector:
    def test_no_interference_when_cursor_static(self):
        with patch("ui_lock._get_cursor_pos", return_value=(100, 100)), \
             patch("ui_lock._get_foreground_window", return_value=12345):
            det = InterferenceDetector()
            det.snapshot_before()
            report = det.check_after()
            assert report.detected is False
            assert report.type == InterferenceType.NONE
            assert report.cursor_delta_px == 0.0

    def test_cursor_moved_detected(self):
        positions = [(100, 100), (200, 200)]
        call_count = [0]

        def mock_pos():
            idx = min(call_count[0], len(positions) - 1)
            call_count[0] += 1
            return positions[idx]

        with patch("ui_lock._get_cursor_pos", side_effect=mock_pos), \
             patch("ui_lock._get_foreground_window", return_value=123):
            det = InterferenceDetector()
            det.snapshot_before()
            report = det.check_after()
            assert report.detected is True
            assert report.type == InterferenceType.CURSOR_MOVED
            assert report.cursor_delta_px > CURSOR_JITTER_THRESHOLD_PX

    def test_jitter_below_threshold_ignored(self):
        positions = [(100, 100), (102, 101)]
        call_count = [0]

        def mock_pos():
            idx = min(call_count[0], len(positions) - 1)
            call_count[0] += 1
            return positions[idx]

        with patch("ui_lock._get_cursor_pos", side_effect=mock_pos), \
             patch("ui_lock._get_foreground_window", return_value=123):
            det = InterferenceDetector(jitter_threshold=5)
            det.snapshot_before()
            report = det.check_after()
            assert report.detected is False

    def test_focus_changed_detected(self):
        with patch("ui_lock._get_cursor_pos", return_value=(100, 100)):
            fg_values = [111, 222]
            call_count = [0]

            def mock_fg():
                idx = min(call_count[0], len(fg_values) - 1)
                call_count[0] += 1
                return fg_values[idx]

            with patch("ui_lock._get_foreground_window", side_effect=mock_fg):
                det = InterferenceDetector()
                det.snapshot_before()
                report = det.check_after()
                assert report.detected is True
                assert report.type == InterferenceType.FOCUS_CHANGED

    def test_cursor_and_focus_changed(self):
        positions = [(100, 100), (300, 300)]
        fg_values = [111, 222]
        pos_count = [0]
        fg_count = [0]

        def mock_pos():
            idx = min(pos_count[0], 1)
            pos_count[0] += 1
            return positions[idx]

        def mock_fg():
            idx = min(fg_count[0], 1)
            fg_count[0] += 1
            return fg_values[idx]

        with patch("ui_lock._get_cursor_pos", side_effect=mock_pos), \
             patch("ui_lock._get_foreground_window", side_effect=mock_fg):
            det = InterferenceDetector()
            det.snapshot_before()
            report = det.check_after()
            assert report.detected is True
            assert report.type == InterferenceType.CURSOR_AND_FOCUS

    def test_zero_fg_ignored(self):
        with patch("ui_lock._get_cursor_pos", return_value=(100, 100)):
            fg_values = [0, 222]
            call_count = [0]

            def mock_fg():
                idx = min(call_count[0], 1)
                call_count[0] += 1
                return fg_values[idx]

            with patch("ui_lock._get_foreground_window", side_effect=mock_fg):
                det = InterferenceDetector()
                det.snapshot_before()
                report = det.check_after()
                assert report.detected is False


# ---------------------------------------------------------------------------
# CursorGuard
# ---------------------------------------------------------------------------

class TestCursorGuard:
    def test_save_returns_position(self):
        with patch("ui_lock._get_cursor_pos", return_value=(150, 250)):
            guard = CursorGuard()
            pos = guard.save()
            assert pos == (150, 250)

    def test_restore_when_no_interference(self):
        with patch("ui_lock._get_cursor_pos", return_value=(150, 250)), \
             patch("ui_lock._set_cursor_pos") as mock_set:
            guard = CursorGuard(restore_on_no_interference=True)
            guard.save()
            no_interference = InterferenceReport(detected=False)
            result = guard.restore(no_interference)
            assert result is True
            mock_set.assert_called_once_with(150, 250)

    def test_no_restore_when_interference(self):
        with patch("ui_lock._get_cursor_pos", return_value=(150, 250)), \
             patch("ui_lock._set_cursor_pos") as mock_set:
            guard = CursorGuard(restore_on_no_interference=True)
            guard.save()
            interference = InterferenceReport(
                detected=True, type=InterferenceType.CURSOR_MOVED,
            )
            result = guard.restore(interference)
            assert result is False
            mock_set.assert_not_called()

    def test_no_restore_when_disabled(self):
        with patch("ui_lock._get_cursor_pos", return_value=(150, 250)), \
             patch("ui_lock._set_cursor_pos") as mock_set:
            guard = CursorGuard(restore_on_no_interference=False)
            guard.save()
            no_interference = InterferenceReport(detected=False)
            result = guard.restore(no_interference)
            assert result is False
            mock_set.assert_not_called()

    def test_restore_without_save_returns_false(self):
        guard = CursorGuard()
        no_interference = InterferenceReport(detected=False)
        assert guard.restore(no_interference) is False


# ---------------------------------------------------------------------------
# InputGuard
# ---------------------------------------------------------------------------

class TestInputGuard:
    def test_acquire_and_release(self):
        with patch("ui_lock._block_input", return_value=True) as mock_bi:
            guard = InputGuard()
            assert guard.acquire() is True
            assert guard._blocked is True
            guard.release()
            assert guard._blocked is False
            assert mock_bi.call_count == 2

    def test_acquire_fails_gracefully(self):
        with patch("ui_lock._block_input", return_value=False):
            guard = InputGuard()
            assert guard.acquire() is False
            assert guard._blocked is False

    def test_safety_timeout(self):
        with patch("ui_lock._block_input", return_value=True):
            guard = InputGuard(max_duration_s=0.01)
            guard.acquire()
            guard._blocked_at = time.time() - 1.0
            assert guard.check_timeout() is True
            assert guard._blocked is False

    def test_no_timeout_when_not_blocked(self):
        guard = InputGuard()
        assert guard.check_timeout() is False

    def test_max_duration_capped(self):
        guard = InputGuard(max_duration_s=999.0)
        assert guard._max == MAX_BLOCK_DURATION_S

    def test_del_releases(self):
        with patch("ui_lock._block_input", return_value=True):
            guard = InputGuard()
            guard.acquire()
            assert guard._blocked is True
            guard.__del__()
            assert guard._blocked is False


# ---------------------------------------------------------------------------
# UIOperationRecord
# ---------------------------------------------------------------------------

class TestUIOperationRecord:
    def test_default_values(self):
        r = UIOperationRecord()
        assert r.operation == ""
        assert r.success is True
        assert r.error is None
        assert r.interference is None


# ---------------------------------------------------------------------------
# UIOperationLock
# ---------------------------------------------------------------------------

class TestUIOperationLock:
    @pytest.mark.asyncio
    async def test_basic_operation(self):
        lock = UIOperationLock(detect_interference=False, restore_cursor=False)
        async with lock.acquire_operation("test_op"):
            assert lock.is_locked
            assert lock.active_operation == "test_op"
        assert not lock.is_locked
        assert lock.active_operation is None

    @pytest.mark.asyncio
    async def test_stats_tracked(self):
        lock = UIOperationLock(detect_interference=False, restore_cursor=False)
        async with lock.acquire_operation("op1"):
            pass
        async with lock.acquire_operation("op2"):
            pass
        stats = lock.stats
        assert stats["total_operations"] == 2
        assert stats["is_locked"] is False

    @pytest.mark.asyncio
    async def test_history_recorded(self):
        lock = UIOperationLock(detect_interference=False, restore_cursor=False)
        async with lock.acquire_operation("click_btn"):
            pass
        history = lock.get_history()
        assert len(history) == 1
        assert history[0]["operation"] == "click_btn"
        assert history[0]["success"] is True

    @pytest.mark.asyncio
    async def test_error_recorded_in_history(self):
        lock = UIOperationLock(detect_interference=False, restore_cursor=False)
        with pytest.raises(ValueError):
            async with lock.acquire_operation("bad_op"):
                raise ValueError("something broke")
        history = lock.get_history()
        assert len(history) == 1
        assert history[0]["success"] is False
        assert "something broke" in history[0].get("error", "")

    @pytest.mark.asyncio
    async def test_serialisation_prevents_concurrent_ops(self):
        lock = UIOperationLock(detect_interference=False, restore_cursor=False)
        order = []

        async def task(name: str, delay: float):
            async with lock.acquire_operation(name):
                order.append(f"{name}_start")
                await asyncio.sleep(delay)
                order.append(f"{name}_end")

        await asyncio.gather(task("A", 0.05), task("B", 0.01))

        assert order[0] == "A_start"
        assert order[1] == "A_end"
        assert order[2] == "B_start"
        assert order[3] == "B_end"

    @pytest.mark.asyncio
    async def test_history_max_limit(self):
        lock = UIOperationLock(
            detect_interference=False, restore_cursor=False, max_history=5,
        )
        for i in range(10):
            async with lock.acquire_operation(f"op_{i}"):
                pass
        assert len(lock.get_history(100)) == 5
        assert lock.stats["total_operations"] == 10

    @pytest.mark.asyncio
    async def test_clear_history(self):
        lock = UIOperationLock(detect_interference=False, restore_cursor=False)
        async with lock.acquire_operation("op"):
            pass
        lock.clear_history()
        assert lock.get_history() == []

    @pytest.mark.asyncio
    async def test_interference_detection_integration(self):
        # CursorGuard.save() calls _get_cursor_pos (1st),
        # InterferenceDetector.snapshot_before() calls it (2nd),
        # InterferenceDetector.check_after() calls it (3rd).
        positions = [(100, 100), (100, 100), (500, 500)]
        call_count = [0]

        def mock_pos():
            idx = min(call_count[0], len(positions) - 1)
            call_count[0] += 1
            return positions[idx]

        with patch("ui_lock._get_cursor_pos", side_effect=mock_pos), \
             patch("ui_lock._set_cursor_pos"), \
             patch("ui_lock._get_foreground_window", return_value=123):
            lock = UIOperationLock(detect_interference=True, restore_cursor=False)
            async with lock.acquire_operation("click"):
                pass
            assert lock.stats["total_interferences"] == 1
            history = lock.get_history()
            assert history[0].get("interference") is not None

    @pytest.mark.asyncio
    async def test_no_interference_no_entry_in_history(self):
        with patch("ui_lock._get_cursor_pos", return_value=(100, 100)), \
             patch("ui_lock._set_cursor_pos"), \
             patch("ui_lock._get_foreground_window", return_value=123):
            lock = UIOperationLock(detect_interference=True, restore_cursor=False)
            async with lock.acquire_operation("click"):
                pass
            history = lock.get_history()
            assert history[0].get("interference") is None

    @pytest.mark.asyncio
    async def test_cursor_restore_on_no_interference(self):
        with patch("ui_lock._get_cursor_pos", return_value=(100, 200)), \
             patch("ui_lock._set_cursor_pos") as mock_set, \
             patch("ui_lock._get_foreground_window", return_value=123):
            lock = UIOperationLock(detect_interference=True, restore_cursor=True)
            async with lock.acquire_operation("op"):
                pass
            mock_set.assert_called_once_with(100, 200)

    @pytest.mark.asyncio
    async def test_cursor_not_restored_on_interference(self):
        # save() gets (100,100), snapshot_before() gets (100,100), check_after() gets (500,500)
        positions = [(100, 100), (100, 100), (500, 500)]
        call_count = [0]

        def mock_pos():
            idx = min(call_count[0], len(positions) - 1)
            call_count[0] += 1
            return positions[idx]

        with patch("ui_lock._get_cursor_pos", side_effect=mock_pos), \
             patch("ui_lock._set_cursor_pos") as mock_set, \
             patch("ui_lock._get_foreground_window", return_value=123):
            lock = UIOperationLock(detect_interference=True, restore_cursor=True)
            async with lock.acquire_operation("op"):
                pass
            mock_set.assert_not_called()

    @pytest.mark.asyncio
    async def test_block_input_mode(self):
        with patch("ui_lock._get_cursor_pos", return_value=(0, 0)), \
             patch("ui_lock._set_cursor_pos"), \
             patch("ui_lock._get_foreground_window", return_value=0), \
             patch("ui_lock._block_input", return_value=True) as mock_bi:
            lock = UIOperationLock(detect_interference=False, restore_cursor=False)
            async with lock.acquire_operation("critical", block_input=True):
                pass
            assert mock_bi.call_count == 2
            mock_bi.assert_any_call(True)
            mock_bi.assert_any_call(False)

    @pytest.mark.asyncio
    async def test_block_input_released_on_error(self):
        with patch("ui_lock._get_cursor_pos", return_value=(0, 0)), \
             patch("ui_lock._set_cursor_pos"), \
             patch("ui_lock._get_foreground_window", return_value=0), \
             patch("ui_lock._block_input", return_value=True) as mock_bi:
            lock = UIOperationLock(detect_interference=False, restore_cursor=False)
            with pytest.raises(RuntimeError):
                async with lock.acquire_operation("critical", block_input=True):
                    raise RuntimeError("boom")
            mock_bi.assert_any_call(False)

    @pytest.mark.asyncio
    async def test_per_operation_cursor_restore_override(self):
        with patch("ui_lock._get_cursor_pos", return_value=(100, 200)), \
             patch("ui_lock._set_cursor_pos") as mock_set, \
             patch("ui_lock._get_foreground_window", return_value=123):
            lock = UIOperationLock(detect_interference=True, restore_cursor=True)
            async with lock.acquire_operation("op", restore_cursor=False):
                pass
            mock_set.assert_not_called()

    @pytest.mark.asyncio
    async def test_simple_lock_acquire_release(self):
        lock = UIOperationLock(detect_interference=False, restore_cursor=False)
        await lock.acquire_simple()
        assert lock.is_locked
        lock.release_simple()
        assert not lock.is_locked

    @pytest.mark.asyncio
    async def test_interference_rate_calculation(self):
        with patch("ui_lock._get_cursor_pos", return_value=(100, 100)), \
             patch("ui_lock._set_cursor_pos"), \
             patch("ui_lock._get_foreground_window", return_value=123):
            lock = UIOperationLock(detect_interference=True, restore_cursor=False)
            for _ in range(4):
                async with lock.acquire_operation("op"):
                    pass
        stats = lock.stats
        assert stats["interference_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_duration_tracked(self):
        lock = UIOperationLock(detect_interference=False, restore_cursor=False)
        async with lock.acquire_operation("slow_op"):
            await asyncio.sleep(0.05)
        history = lock.get_history()
        assert history[0]["duration_ms"] >= 40


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_get_ui_lock_returns_same_instance(self):
        reset_ui_lock()
        lock1 = get_ui_lock()
        lock2 = get_ui_lock()
        assert lock1 is lock2
        reset_ui_lock()

    def test_reset_clears_singleton(self):
        reset_ui_lock()
        lock1 = get_ui_lock()
        reset_ui_lock()
        lock2 = get_ui_lock()
        assert lock1 is not lock2
        reset_ui_lock()


# ---------------------------------------------------------------------------
# Win32 helpers (non-Windows fallback)
# ---------------------------------------------------------------------------

class TestWin32Helpers:
    def test_get_cursor_pos_non_windows(self):
        with patch("ui_lock._HAS_WIN32", False):
            from ui_lock import _get_cursor_pos as gcp
            assert gcp() == (0, 0)

    def test_set_cursor_pos_non_windows(self):
        with patch("ui_lock._HAS_WIN32", False):
            from ui_lock import _set_cursor_pos as scp
            assert scp(100, 200) is False

    def test_block_input_non_windows(self):
        with patch("ui_lock._HAS_WIN32", False):
            from ui_lock import _block_input as bi
            assert bi(True) is False

    def test_get_foreground_window_non_windows(self):
        with patch("ui_lock._HAS_WIN32", False):
            from ui_lock import _get_foreground_window as gfw
            assert gfw() == 0


# ---------------------------------------------------------------------------
# Integration: concurrent operations are serialised
# ---------------------------------------------------------------------------

class TestConcurrencyIntegration:
    @pytest.mark.asyncio
    async def test_three_concurrent_tasks_serialised(self):
        lock = UIOperationLock(detect_interference=False, restore_cursor=False)
        execution_log = []

        async def simulate_ui_action(name: str):
            async with lock.acquire_operation(name):
                execution_log.append(f"{name}_enter")
                await asyncio.sleep(0.02)
                execution_log.append(f"{name}_exit")

        await asyncio.gather(
            simulate_ui_action("click"),
            simulate_ui_action("type"),
            simulate_ui_action("scroll"),
        )

        assert len(execution_log) == 6
        for i in range(0, 6, 2):
            name = execution_log[i].replace("_enter", "")
            assert execution_log[i + 1] == f"{name}_exit"

    @pytest.mark.asyncio
    async def test_read_operations_not_blocked(self):
        """Read-only operations (no lock) should run concurrently."""
        lock = UIOperationLock(detect_interference=False, restore_cursor=False)
        results = []

        async def mutating():
            async with lock.acquire_operation("click"):
                results.append("click_start")
                await asyncio.sleep(0.05)
                results.append("click_end")

        async def readonly():
            await asyncio.sleep(0.01)
            results.append("read")

        await asyncio.gather(mutating(), readonly())
        read_idx = results.index("read")
        click_end_idx = results.index("click_end")
        assert read_idx < click_end_idx
