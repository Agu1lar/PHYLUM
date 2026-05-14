# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tests for core/hung_process_reaper.py and its integration with BaseTool."""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'core'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))

from hung_process_reaper import (
    TargetContext,
    ReapResult,
    resolve_office_process_name,
    reap_if_hung,
    _resolve_pid_from_context,
    _OFFICE_PROCESS_MAP,
)
from tool_base import BaseTool, ToolExecutionError


# ===========================================================================
# TargetContext
# ===========================================================================

class TestTargetContext:
    def test_defaults(self):
        ctx = TargetContext()
        assert ctx.hwnd == 0
        assert ctx.pid == 0
        assert ctx.process_name == ""

    def test_to_dict(self):
        ctx = TargetContext(hwnd=123, pid=456, process_name="EXCEL.EXE", tool_name="office", action="open_document")
        d = ctx.to_dict()
        assert d["hwnd"] == 123
        assert d["pid"] == 456
        assert d["process_name"] == "EXCEL.EXE"
        assert d["tool_name"] == "office"

    def test_all_fields(self):
        ctx = TargetContext(hwnd=1, pid=2, process_name="p", title="t", tool_name="tn", action="a")
        assert ctx.hwnd == 1 and ctx.pid == 2 and ctx.process_name == "p"
        assert ctx.title == "t" and ctx.tool_name == "tn" and ctx.action == "a"


# ===========================================================================
# ReapResult
# ===========================================================================

class TestReapResult:
    def test_defaults(self):
        r = ReapResult()
        assert r.reaped is False
        assert r.confirmed_hung is False

    def test_to_dict(self):
        r = ReapResult(reaped=True, confirmed_hung=True, killed_pid=999, process_name="EXCEL.EXE", reason="killed", elapsed_ms=123.4)
        d = r.to_dict()
        assert d["reaped"] is True
        assert d["killed_pid"] == 999
        assert d["elapsed_ms"] == 123.4


# ===========================================================================
# resolve_office_process_name
# ===========================================================================

class TestResolveOfficeProcessName:
    def test_excel_from_action(self):
        assert resolve_office_process_name("excel_read_range") == "EXCEL.EXE"

    def test_word_from_action(self):
        assert resolve_office_process_name("word_find_text") == "WINWORD.EXE"

    def test_outlook_from_action(self):
        assert resolve_office_process_name("outlook_read_latest") == "OUTLOOK.EXE"

    def test_powerpoint_from_action(self):
        assert resolve_office_process_name("powerpoint_export") == "POWERPNT.EXE"

    def test_from_file_extension_xlsx(self):
        assert resolve_office_process_name("open_document", "C:/data/budget.xlsx") == "EXCEL.EXE"

    def test_from_file_extension_docx(self):
        assert resolve_office_process_name("open_document", "report.docx") == "WINWORD.EXE"

    def test_from_file_extension_pptx(self):
        assert resolve_office_process_name("save_as_document", "slides.pptx") == "POWERPNT.EXE"

    def test_from_file_extension_csv(self):
        assert resolve_office_process_name("open_document", "data.csv") == "EXCEL.EXE"

    def test_unknown_action_no_path(self):
        assert resolve_office_process_name("some_unknown_action") == ""

    def test_unknown_extension(self):
        assert resolve_office_process_name("open_document", "file.xyz") == ""


# ===========================================================================
# _resolve_pid_from_context
# ===========================================================================

class TestResolvePidFromContext:
    def test_direct_pid(self):
        ctx = TargetContext(pid=1234)
        assert _resolve_pid_from_context(ctx) == 1234

    def test_from_hwnd(self):
        ctx = TargetContext(hwnd=5678)
        with patch("hung_process_reaper._get_window_pid", return_value=9999):
            assert _resolve_pid_from_context(ctx) == 9999

    def test_from_process_name(self):
        ctx = TargetContext(process_name="notepad.exe")
        mock_proc = MagicMock()
        mock_proc.info = {"pid": 4242, "name": "notepad.exe"}
        with patch("psutil.process_iter", return_value=[mock_proc]):
            assert _resolve_pid_from_context(ctx) == 4242

    def test_returns_zero_when_nothing_matches(self):
        ctx = TargetContext()
        assert _resolve_pid_from_context(ctx) == 0


# ===========================================================================
# reap_if_hung
# ===========================================================================

class TestReapIfHung:
    @pytest.mark.asyncio
    async def test_no_pid_resolved(self):
        ctx = TargetContext()
        with patch("hung_process_reaper._resolve_pid_from_context", return_value=0):
            result = await reap_if_hung(ctx)
            assert result.reaped is False
            assert "could not resolve PID" in result.reason

    @pytest.mark.asyncio
    async def test_process_already_dead(self):
        ctx = TargetContext(pid=999)
        with patch("hung_process_reaper._resolve_pid_from_context", return_value=999), \
             patch("hung_process_reaper._is_process_alive", return_value=False):
            result = await reap_if_hung(ctx)
            assert result.reaped is False
            assert "already dead" in result.reason

    @pytest.mark.asyncio
    async def test_process_alive_but_not_hung(self):
        ctx = TargetContext(pid=999, hwnd=111)
        mock_detector = MagicMock()
        mock_detector.check.return_value = {"frozen": False, "hwnd": 111}

        with patch("hung_process_reaper._resolve_pid_from_context", return_value=999), \
             patch("hung_process_reaper._is_process_alive", return_value=True), \
             patch("hung_process_reaper.FrozenWindowDetector", return_value=mock_detector):
            result = await reap_if_hung(ctx)
            assert result.reaped is False
            assert result.confirmed_hung is False
            assert "NOT confirmed hung" in result.reason

    @pytest.mark.asyncio
    async def test_confirmed_hung_and_killed(self):
        ctx = TargetContext(pid=999, hwnd=111, process_name="EXCEL.EXE")
        mock_detector = MagicMock()
        mock_detector.check.return_value = {"frozen": True, "hwnd": 111}

        with patch("hung_process_reaper._resolve_pid_from_context", return_value=999), \
             patch("hung_process_reaper._is_process_alive", return_value=True), \
             patch("hung_process_reaper.FrozenWindowDetector", return_value=mock_detector), \
             patch("hung_process_reaper._kill_process_by_pid", return_value=True):
            result = await reap_if_hung(ctx)
            assert result.reaped is True
            assert result.confirmed_hung is True
            assert result.killed_pid == 999
            assert result.process_name == "EXCEL.EXE"

    @pytest.mark.asyncio
    async def test_confirmed_hung_kill_fails(self):
        ctx = TargetContext(pid=999, hwnd=111)
        mock_detector = MagicMock()
        mock_detector.check.return_value = {"frozen": True, "hwnd": 111}

        with patch("hung_process_reaper._resolve_pid_from_context", return_value=999), \
             patch("hung_process_reaper._is_process_alive", return_value=True), \
             patch("hung_process_reaper.FrozenWindowDetector", return_value=mock_detector), \
             patch("hung_process_reaper._kill_process_by_pid", return_value=False):
            result = await reap_if_hung(ctx)
            assert result.reaped is False
            assert result.confirmed_hung is True
            assert "kill failed" in result.reason

    @pytest.mark.asyncio
    async def test_skip_confirmation_kills_directly(self):
        ctx = TargetContext(pid=999, process_name="EXCEL.EXE")

        with patch("hung_process_reaper._resolve_pid_from_context", return_value=999), \
             patch("hung_process_reaper._is_process_alive", return_value=True), \
             patch("hung_process_reaper._kill_process_by_pid", return_value=True):
            result = await reap_if_hung(ctx, skip_confirmation=True)
            assert result.reaped is True
            assert result.confirmed_hung is True

    @pytest.mark.asyncio
    async def test_no_hwnd_falls_back_to_pid_scan(self):
        ctx = TargetContext(pid=999)
        mock_detector = MagicMock()
        mock_detector.check.return_value = {"frozen": True, "hwnd": 222}
        mock_detector.check_by_pid.return_value = [{"hwnd": 222, "frozen": True}]

        with patch("hung_process_reaper._resolve_pid_from_context", return_value=999), \
             patch("hung_process_reaper._is_process_alive", return_value=True), \
             patch("hung_process_reaper.FrozenWindowDetector", return_value=mock_detector), \
             patch("hung_process_reaper._resolve_hwnd_from_pid", return_value=[222]), \
             patch("hung_process_reaper._kill_process_by_pid", return_value=True):
            result = await reap_if_hung(ctx)
            assert result.reaped is True

    @pytest.mark.asyncio
    async def test_event_bus_emitted_on_kill(self):
        ctx = TargetContext(pid=999, hwnd=111, process_name="EXCEL.EXE")
        mock_detector = MagicMock()
        mock_detector.check.return_value = {"frozen": True, "hwnd": 111}
        mock_bus = MagicMock()
        mock_bus.emit_raw = AsyncMock()

        with patch("hung_process_reaper._resolve_pid_from_context", return_value=999), \
             patch("hung_process_reaper._is_process_alive", return_value=True), \
             patch("hung_process_reaper.FrozenWindowDetector", return_value=mock_detector), \
             patch("hung_process_reaper._kill_process_by_pid", return_value=True):
            result = await reap_if_hung(ctx, event_bus=mock_bus)
            assert result.reaped is True
            mock_bus.emit_raw.assert_called_once()
            call_args = mock_bus.emit_raw.call_args
            assert call_args[0][0] == "process_reaped"


# ===========================================================================
# BaseTool integration
# ===========================================================================

class DummyTool(BaseTool):
    """Tool that hangs on first call, succeeds on second."""

    class InputModel(MagicMock):
        pass

    def __init__(self, *, hang_calls: int = 1):
        super().__init__(default_timeout=1, default_retries=3)
        self._reap_on_timeout = True
        self._call_count = 0
        self._hang_calls = hang_calls

    async def validate(self, payload):
        pass

    async def _run(self, payload):
        self._call_count += 1
        if self._call_count <= self._hang_calls:
            await asyncio.sleep(999)
        return MagicMock(dict=lambda: {"ok": True})


class TestBaseToolReaperIntegration:
    @pytest.mark.asyncio
    async def test_reaper_called_on_timeout(self):
        tool = DummyTool(hang_calls=99)
        tool._target_context = TargetContext(pid=111, process_name="test.exe")

        with patch("tool_base.reap_if_hung", new_callable=AsyncMock) as mock_reap:
            mock_reap.return_value = ReapResult(reaped=True, killed_pid=111, process_name="test.exe", reason="killed")
            with pytest.raises(ToolExecutionError):
                await tool.run({})
            assert mock_reap.call_count >= 1

    @pytest.mark.asyncio
    async def test_reaper_not_called_when_disabled(self):
        tool = DummyTool(hang_calls=99)
        tool._reap_on_timeout = False
        tool._target_context = TargetContext(pid=111)

        with patch("tool_base.reap_if_hung", new_callable=AsyncMock) as mock_reap:
            with pytest.raises(ToolExecutionError):
                await tool.run({})
            mock_reap.assert_not_called()

    @pytest.mark.asyncio
    async def test_reaper_not_called_without_context(self):
        tool = DummyTool(hang_calls=99)
        tool._reap_on_timeout = True
        tool._target_context = None

        with patch("tool_base.reap_if_hung", new_callable=AsyncMock) as mock_reap:
            with pytest.raises(ToolExecutionError):
                await tool.run({})
            mock_reap.assert_not_called()

    @pytest.mark.asyncio
    async def test_context_cleared_after_reap(self):
        tool = DummyTool(hang_calls=99)
        tool._target_context = TargetContext(pid=111)

        with patch("tool_base.reap_if_hung", new_callable=AsyncMock) as mock_reap:
            mock_reap.return_value = ReapResult(reaped=True, killed_pid=111)
            with pytest.raises(ToolExecutionError):
                await tool.run({})
            assert tool._target_context is None

    @pytest.mark.asyncio
    async def test_reaper_failure_does_not_crash_tool(self):
        tool = DummyTool(hang_calls=99)
        tool._target_context = TargetContext(pid=111)

        with patch("tool_base.reap_if_hung", new_callable=AsyncMock) as mock_reap:
            mock_reap.side_effect = RuntimeError("reaper exploded")
            with pytest.raises(ToolExecutionError):
                await tool.run({})


# ===========================================================================
# WindowsUiTool context tracking
# ===========================================================================

class TestWindowsUiToolContext:
    def test_sets_target_context(self):
        from tool_windows_ui import WindowsUiTool
        tool = WindowsUiTool()
        assert tool._reap_on_timeout is True

        mock_payload = MagicMock()
        mock_payload.hwnd = 12345
        mock_payload.title = "Excel - Budget"
        mock_payload.process_name = "EXCEL.EXE"
        mock_payload.action = "invoke_element"

        tool._set_target_ctx(mock_payload)
        assert tool._target_context is not None
        assert tool._target_context.hwnd == 12345
        assert tool._target_context.process_name == "EXCEL.EXE"
        assert tool._target_context.tool_name == "windows_ui"


# ===========================================================================
# OfficeTool context tracking
# ===========================================================================

class TestOfficeToolContext:
    def test_sets_target_context_from_action(self):
        from tool_office import OfficeTool
        tool = OfficeTool()
        assert tool._reap_on_timeout is True

        mock_payload = MagicMock()
        mock_payload.action = "excel_read_range"
        mock_payload.path = "C:/data/budget.xlsx"

        tool._set_target_ctx(mock_payload)
        assert tool._target_context is not None
        assert tool._target_context.process_name == "EXCEL.EXE"
        assert tool._target_context.tool_name == "office"

    def test_sets_target_from_file_extension(self):
        from tool_office import OfficeTool
        tool = OfficeTool()

        mock_payload = MagicMock()
        mock_payload.action = "open_document"
        mock_payload.path = "report.docx"

        tool._set_target_ctx(mock_payload)
        assert tool._target_context.process_name == "WINWORD.EXE"


# ===========================================================================
# End-to-end scenario
# ===========================================================================

class TestEndToEndScenario:
    @pytest.mark.asyncio
    async def test_excel_hangs_gets_reaped(self):
        """Simulate: Excel hangs → timeout → reaper confirms hung → taskkill → thread freed."""
        ctx = TargetContext(
            pid=9999,
            hwnd=5555,
            process_name="EXCEL.EXE",
            title="Excel - Budget.xlsx",
            tool_name="office",
            action="excel_read_range",
        )

        mock_detector = MagicMock()
        mock_detector.check.return_value = {"frozen": True, "hwnd": 5555, "hung_api": True}

        with patch("hung_process_reaper._resolve_pid_from_context", return_value=9999), \
             patch("hung_process_reaper._is_process_alive", return_value=True), \
             patch("hung_process_reaper.FrozenWindowDetector", return_value=mock_detector), \
             patch("hung_process_reaper._kill_process_by_pid", return_value=True):
            result = await reap_if_hung(ctx)

        assert result.reaped is True
        assert result.confirmed_hung is True
        assert result.killed_pid == 9999
        assert result.process_name == "EXCEL.EXE"
        assert "killed" in result.reason.lower() or "free" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_responsive_process_not_killed(self):
        """Simulate: process is alive and responsive → reaper does NOT kill."""
        ctx = TargetContext(pid=8888, hwnd=4444, process_name="EXCEL.EXE")

        mock_detector = MagicMock()
        mock_detector.check.return_value = {"frozen": False, "hwnd": 4444}

        with patch("hung_process_reaper._resolve_pid_from_context", return_value=8888), \
             patch("hung_process_reaper._is_process_alive", return_value=True), \
             patch("hung_process_reaper.FrozenWindowDetector", return_value=mock_detector), \
             patch("hung_process_reaper._kill_process_by_pid") as mock_kill:
            result = await reap_if_hung(ctx)

        assert result.reaped is False
        assert result.confirmed_hung is False
        mock_kill.assert_not_called()
