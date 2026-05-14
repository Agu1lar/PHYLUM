# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tests for the visual perception, grounding, verification, replay and policy modules."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agents"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "safety"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "models"))

from visual_perception import (
    VisualElement,
    ScreenshotState,
    VisualPerceptionEngine,
    redact_screenshot,
)
from visual_grounding import (
    GroundedElement,
    VisualGroundingEngine,
)
from visual_verification import (
    VisualDiff,
    VisualVerifier,
)
from visual_replay import (
    ReplayEntry,
    VisualReplayRecorder,
)
from visual_policy import (
    AutomationMode,
    FallbackDecision,
    VisualPolicy,
)


# ---------------------------------------------------------------------------
# VisualElement tests
# ---------------------------------------------------------------------------

class TestVisualElement:
    def test_center_auto_calculated(self):
        el = VisualElement(text="OK", bbox=(10, 20, 110, 60))
        assert el.center == (60, 40)

    def test_to_dict_structure(self):
        el = VisualElement(text="Cancel", bbox=(0, 0, 50, 20), confidence=0.95, source="ocr")
        d = el.to_dict()
        assert d["text"] == "Cancel"
        assert d["bbox"]["left"] == 0
        assert d["confidence"] == 0.95
        assert d["source"] == "ocr"
        assert "x" in d["center"]

    def test_element_type_default(self):
        el = VisualElement(text="hello", bbox=(0, 0, 10, 10))
        assert el.element_type == "text"


class TestScreenshotState:
    def test_to_dict_includes_element_count(self):
        ss = ScreenshotState(
            image_path="/tmp/test.png",
            timestamp=time.time(),
            width=1920,
            height=1080,
            source="full_screen",
            elements=[VisualElement(text="A", bbox=(0, 0, 10, 10))],
        )
        d = ss.to_dict()
        assert d["element_count"] == 1
        assert d["width"] == 1920
        assert d["source"] == "full_screen"


# ---------------------------------------------------------------------------
# VisualPerceptionEngine tests
# ---------------------------------------------------------------------------

class TestVisualPerceptionEngine:
    def test_available_without_pillow(self):
        engine = VisualPerceptionEngine()
        with patch("visual_perception.Image", None):
            assert not engine._available()

    def test_ocr_unavailable_without_pytesseract(self):
        engine = VisualPerceptionEngine()
        with patch("visual_perception.pytesseract", None):
            assert not engine._ocr_available()

    @pytest.mark.asyncio
    async def test_extract_elements_returns_empty_without_ocr(self):
        engine = VisualPerceptionEngine()
        with patch("visual_perception.pytesseract", None):
            elements = await engine.extract_elements(MagicMock())
            assert elements == []

    @pytest.mark.asyncio
    async def test_detect_ui_patterns_buttons(self):
        engine = VisualPerceptionEngine()
        elements = [
            VisualElement(text="OK", bbox=(0, 0, 40, 20), confidence=0.9),
            VisualElement(text="Cancel", bbox=(50, 0, 120, 20), confidence=0.85),
            VisualElement(text="Some label", bbox=(0, 30, 200, 50), confidence=0.8),
        ]
        patterns = await engine.detect_ui_patterns(elements)
        assert len(patterns["buttons"]) == 2
        assert any(e.text == "OK" for e in patterns["buttons"])
        assert any(e.text == "Cancel" for e in patterns["buttons"])

    @pytest.mark.asyncio
    async def test_detect_ui_patterns_errors(self):
        engine = VisualPerceptionEngine()
        elements = [
            VisualElement(text="Error: file not found", bbox=(0, 0, 200, 20), confidence=0.9),
        ]
        patterns = await engine.detect_ui_patterns(elements)
        assert len(patterns["errors"]) == 1

    @pytest.mark.asyncio
    async def test_detect_ui_patterns_modals(self):
        engine = VisualPerceptionEngine()
        elements = [
            VisualElement(text="Are you sure you want to delete?", bbox=(0, 0, 300, 30), confidence=0.85),
        ]
        patterns = await engine.detect_ui_patterns(elements)
        assert len(patterns["modals"]) == 1


# ---------------------------------------------------------------------------
# VisualGroundingEngine tests
# ---------------------------------------------------------------------------

class TestVisualGroundingEngine:
    def test_text_similarity_exact(self):
        assert VisualGroundingEngine._text_similarity("ok", "ok") == 1.0

    def test_text_similarity_partial(self):
        score = VisualGroundingEngine._text_similarity("save", "save as")
        assert score == 0.8

    def test_text_similarity_empty(self):
        assert VisualGroundingEngine._text_similarity("", "hello") == 0.0

    def test_text_similarity_word_overlap(self):
        score = VisualGroundingEngine._text_similarity("save file", "save document file")
        assert score > 0.5

    def test_bbox_overlap_no_intersection(self):
        score = VisualGroundingEngine._bbox_overlap(
            (0, 0, 10, 10),
            {"left": 100, "top": 100, "right": 200, "bottom": 200},
        )
        assert score == 0.0

    def test_bbox_overlap_full_overlap(self):
        score = VisualGroundingEngine._bbox_overlap(
            (10, 10, 50, 50),
            {"left": 10, "top": 10, "right": 50, "bottom": 50},
        )
        assert score == 1.0

    def test_bbox_overlap_partial(self):
        score = VisualGroundingEngine._bbox_overlap(
            (0, 0, 20, 20),
            {"left": 10, "top": 10, "right": 30, "bottom": 30},
        )
        assert 0.0 < score < 1.0

    def test_bbox_overlap_empty_rect(self):
        assert VisualGroundingEngine._bbox_overlap((0, 0, 10, 10), {}) == 0.0

    def test_build_selector(self):
        uia_el = {"title": "OK", "control_type": "Button", "auto_id": "btn1", "extra": "ignored"}
        sel = VisualGroundingEngine._build_selector(uia_el)
        assert sel == {"title": "OK", "control_type": "Button", "auto_id": "btn1"}

    @pytest.mark.asyncio
    async def test_ground_no_elements(self):
        engine = VisualGroundingEngine()
        ss = ScreenshotState(
            image_path="/tmp/t.png", timestamp=0.0, width=100, height=100,
            source="full_screen", elements=[],
        )
        result = await engine.ground(ss)
        assert result == []

    @pytest.mark.asyncio
    async def test_ground_single_not_found(self):
        engine = VisualGroundingEngine()
        ss = ScreenshotState(
            image_path="/tmp/t.png", timestamp=0.0, width=100, height=100,
            source="full_screen",
            elements=[VisualElement(text="Hello", bbox=(0, 0, 10, 10))],
        )
        result = await engine.ground_single("XYZ_NOT_EXIST", ss)
        assert result is None

    @pytest.mark.asyncio
    async def test_ground_single_found_no_uia(self):
        engine = VisualGroundingEngine()
        ss = ScreenshotState(
            image_path="/tmp/t.png", timestamp=0.0, width=100, height=100,
            source="full_screen",
            elements=[VisualElement(text="Save", bbox=(10, 10, 60, 30), confidence=0.9)],
        )
        result = await engine.ground_single("Save", ss)
        assert result is not None
        assert result.grounding_method == "visual_only"


# ---------------------------------------------------------------------------
# VisualDiff + VisualVerifier tests
# ---------------------------------------------------------------------------

class TestVisualVerifier:
    def _make_state(self, *, elements=None, img_hash="aaa"):
        return ScreenshotState(
            image_path="/tmp/t.png", timestamp=time.time(),
            width=100, height=100, source="full_screen",
            elements=elements or [], image_hash=img_hash,
        )

    def test_compare_no_change(self):
        v = VisualVerifier()
        before = self._make_state(img_hash="same")
        after = self._make_state(img_hash="same")
        diff = v.compare(before, after)
        assert not diff.changed
        assert diff.similarity == 1.0

    def test_compare_changed(self):
        v = VisualVerifier()
        before = self._make_state(
            elements=[VisualElement(text="old", bbox=(0, 0, 10, 10))],
            img_hash="hash1",
        )
        after = self._make_state(
            elements=[VisualElement(text="new", bbox=(0, 0, 10, 10))],
            img_hash="hash2",
        )
        diff = v.compare(before, after)
        assert diff.changed
        assert len(diff.new_elements) == 1
        assert len(diff.removed_elements) == 1

    def test_compare_detects_errors(self):
        v = VisualVerifier()
        before = self._make_state(img_hash="h1")
        after = self._make_state(
            elements=[VisualElement(text="Error: file not found", bbox=(0, 0, 200, 20))],
            img_hash="h2",
        )
        diff = v.compare(before, after)
        assert len(diff.detected_errors) == 1

    def test_compare_detects_modals(self):
        v = VisualVerifier()
        before = self._make_state(img_hash="h1")
        after = self._make_state(
            elements=[VisualElement(text="Are you sure you want to proceed?", bbox=(0, 0, 300, 30))],
            img_hash="h2",
        )
        diff = v.compare(before, after)
        assert len(diff.detected_modals) == 1

    def test_compare_detects_spinners(self):
        v = VisualVerifier()
        before = self._make_state(img_hash="h1")
        after = self._make_state(
            elements=[VisualElement(text="Loading...", bbox=(0, 0, 100, 20))],
            img_hash="h2",
        )
        diff = v.compare(before, after)
        assert diff.detected_spinners is True

    def test_compare_detects_confirmations_only_new(self):
        v = VisualVerifier()
        before = self._make_state(
            elements=[VisualElement(text="Success!", bbox=(0, 0, 100, 20))],
            img_hash="h1",
        )
        after = self._make_state(
            elements=[VisualElement(text="Success!", bbox=(0, 0, 100, 20))],
            img_hash="h2",
        )
        diff = v.compare(before, after)
        assert len(diff.detected_confirmations) == 0

    @pytest.mark.asyncio
    async def test_verify_action_error_detected(self):
        v = VisualVerifier()
        before = self._make_state(img_hash="h1")
        after = self._make_state(
            elements=[VisualElement(text="Error: something failed", bbox=(0, 0, 200, 20))],
            img_hash="h2",
        )
        result = await v.verify_action(before, after)
        assert result["verdict"] == "error_detected"
        assert result["confidence"] > 0.8

    @pytest.mark.asyncio
    async def test_verify_action_confirmed(self):
        v = VisualVerifier()
        before = self._make_state(img_hash="h1")
        after = self._make_state(
            elements=[VisualElement(text="File saved successfully!", bbox=(0, 0, 200, 20))],
            img_hash="h2",
        )
        result = await v.verify_action(before, after)
        assert result["verdict"] == "confirmed"

    @pytest.mark.asyncio
    async def test_verify_action_no_change(self):
        v = VisualVerifier()
        state = self._make_state(img_hash="same")
        result = await v.verify_action(state, state)
        assert result["verdict"] == "no_change"

    @pytest.mark.asyncio
    async def test_verify_action_in_progress(self):
        v = VisualVerifier()
        before = self._make_state(img_hash="h1")
        after = self._make_state(
            elements=[VisualElement(text="Please wait, processing...", bbox=(0, 0, 300, 20))],
            img_hash="h2",
        )
        result = await v.verify_action(before, after)
        assert result["verdict"] == "in_progress"


# ---------------------------------------------------------------------------
# VisualDiff tests
# ---------------------------------------------------------------------------

class TestVisualDiff:
    def test_to_dict(self):
        diff = VisualDiff(
            changed=True,
            similarity=0.5,
            new_elements=[VisualElement(text="New", bbox=(0, 0, 10, 10))],
            detected_spinners=True,
        )
        d = diff.to_dict()
        assert d["changed"] is True
        assert d["new_elements_count"] == 1
        assert d["detected_spinners"] is True


# ---------------------------------------------------------------------------
# VisualReplayRecorder tests
# ---------------------------------------------------------------------------

class TestVisualReplayRecorder:
    def test_record_entry(self):
        recorder = VisualReplayRecorder("test-run-1", replay_dir=Path(os.path.join(os.environ.get("TEMP", "/tmp"), "test_replays")))
        entry = recorder.record(
            action="invoke_element",
            tool="windows_ui",
            task_id="task-1",
            result_status="succeeded",
            result_summary="Clicked OK button",
            annotations=["clicked OK"],
        )
        assert entry.action == "invoke_element"
        assert len(recorder.entries) == 1

    def test_multiple_entries(self):
        recorder = VisualReplayRecorder("test-run-2", replay_dir=Path(os.path.join(os.environ.get("TEMP", "/tmp"), "test_replays")))
        for i in range(5):
            recorder.record(action=f"action_{i}", tool="windows_ui", task_id=f"task-{i}")
        assert len(recorder.entries) == 5

    def test_summary(self):
        recorder = VisualReplayRecorder("test-run-3", replay_dir=Path(os.path.join(os.environ.get("TEMP", "/tmp"), "test_replays")))
        recorder.record(action="a1", tool="t1", task_id="1", result_status="succeeded")
        recorder.record(action="a2", tool="t2", task_id="2", result_status="failed")
        recorder.record(action="a3", tool="t3", task_id="3", result_status="succeeded")
        s = recorder.summary
        assert s["entry_count"] == 3
        assert s["status_counts"]["succeeded"] == 2
        assert s["status_counts"]["failed"] == 1

    def test_save_and_load(self):
        replay_dir = Path(os.path.join(os.environ.get("TEMP", "/tmp"), "test_replays_save"))
        replay_dir.mkdir(parents=True, exist_ok=True)
        recorder = VisualReplayRecorder("test-run-4", replay_dir=replay_dir)
        recorder.record(action="a1", tool="t1", task_id="1", result_status="succeeded")
        path = recorder.save()
        loaded = VisualReplayRecorder.load(path)
        assert loaded["run_id"] == "test-run-4"
        assert loaded["entry_count"] == 1
        # cleanup
        Path(path).unlink(missing_ok=True)

    def test_entry_to_dict(self):
        entry = ReplayEntry(
            timestamp=123.456,
            action="click",
            tool="windows_ui",
            task_id="t1",
            annotations=["clicked button"],
            redacted=True,
        )
        d = entry.to_dict()
        assert d["timestamp"] == 123.456
        assert d["redacted"] is True
        assert d["annotations"] == ["clicked button"]


# ---------------------------------------------------------------------------
# VisualPolicy tests
# ---------------------------------------------------------------------------

class TestVisualPolicy:
    def test_native_available(self):
        policy = VisualPolicy()
        decision = policy.evaluate(
            tool="windows_ui", action="invoke_element",
            native_failed=False,
        )
        assert decision.mode == AutomationMode.NATIVE
        assert decision.allowed

    def test_never_visual_action(self):
        policy = VisualPolicy()
        decision = policy.evaluate(
            tool="filesystem", action="delete",
            native_failed=True,
        )
        assert decision.mode == AutomationMode.DENIED
        assert not decision.allowed

    def test_grounded_safe_action(self):
        policy = VisualPolicy()
        decision = policy.evaluate(
            tool="windows_ui", action="invoke_element",
            native_failed=True,
            native_error="element_not_found",
            grounded=True,
            grounding_score=0.85,
        )
        assert decision.mode == AutomationMode.VISUAL_GROUNDED
        assert decision.allowed
        assert not decision.requires_approval
        assert "use_verified_bbox" in decision.constraints

    def test_grounded_mutation_requires_approval(self):
        policy = VisualPolicy()
        decision = policy.evaluate(
            tool="some_tool", action="dangerous_action",
            native_failed=True,
            grounded=True,
            grounding_score=0.8,
            mutates_state=True,
        )
        assert decision.mode == AutomationMode.VISUAL_GROUNDED
        assert decision.allowed
        assert decision.requires_approval

    def test_low_grounding_score(self):
        policy = VisualPolicy()
        decision = policy.evaluate(
            tool="windows_ui", action="invoke_element",
            native_failed=True,
            grounded=True,
            grounding_score=0.4,
        )
        assert decision.mode == AutomationMode.VISUAL_UNGROUNDED
        assert decision.requires_approval

    def test_no_grounding_read_only(self):
        policy = VisualPolicy()
        decision = policy.evaluate(
            tool="windows_ui", action="inspect_window",
            native_failed=True,
            grounded=False,
            mutates_state=False,
        )
        assert decision.mode == AutomationMode.VISUAL_UNGROUNDED
        assert decision.allowed
        assert decision.requires_approval

    def test_no_grounding_mutation_denied(self):
        policy = VisualPolicy()
        decision = policy.evaluate(
            tool="windows_ui", action="set_text",
            native_failed=True,
            grounded=False,
            mutates_state=True,
        )
        assert decision.mode == AutomationMode.DENIED
        assert not decision.allowed

    def test_should_capture_visual_mode(self):
        policy = VisualPolicy()
        assert policy.should_capture_before_after(
            tool="windows_ui", action="invoke_element",
            mode=AutomationMode.VISUAL_GROUNDED,
        )

    def test_should_capture_desktop_open(self):
        policy = VisualPolicy()
        assert policy.should_capture_before_after(
            tool="desktop", action="open_app",
            mode=AutomationMode.NATIVE,
        )

    def test_should_not_capture_shell(self):
        policy = VisualPolicy()
        assert not policy.should_capture_before_after(
            tool="shell", action="run",
            mode=AutomationMode.NATIVE,
        )

    def test_fallback_decision_to_dict(self):
        d = FallbackDecision(
            mode=AutomationMode.VISUAL_GROUNDED,
            reason="Test",
            allowed=True,
            constraints=["a", "b"],
        )
        result = d.to_dict()
        assert result["mode"] == "visual_grounded"
        assert result["allowed"] is True
        assert len(result["constraints"]) == 2

    def test_shell_run_never_visual(self):
        policy = VisualPolicy()
        decision = policy.evaluate(
            tool="shell", action="run",
            native_failed=True,
        )
        assert decision.mode == AutomationMode.DENIED


# ---------------------------------------------------------------------------
# GroundedElement tests
# ---------------------------------------------------------------------------

class TestGroundedElement:
    def test_to_dict(self):
        ge = GroundedElement(
            visual=VisualElement(text="OK", bbox=(0, 0, 40, 20)),
            uia_selector={"title": "OK", "control_type": "Button"},
            match_score=0.9,
            grounding_method="text_bbox_match",
            verified=True,
        )
        d = ge.to_dict()
        assert d["verified"] is True
        assert d["match_score"] == 0.9
        assert d["uia_selector"]["title"] == "OK"


# ---------------------------------------------------------------------------
# Integration: canonical_tools recognizes "visual"
# ---------------------------------------------------------------------------

class TestVisualCanonicalIntegration:
    def test_visual_in_supported_tools(self):
        from canonical_tools import supported_tools
        assert "visual" in supported_tools()

    def test_visual_action_metadata(self):
        from canonical_tools import action_metadata
        meta = action_metadata("visual", "capture_screen")
        assert meta["semantic_type"] == "inspection"
        assert meta["mutates_state"] is False

    def test_visual_task_title(self):
        from canonical_tools import task_title
        title = task_title("visual", "capture_window", {"title": "Notepad"})
        assert "Notepad" in title
        assert "Visual" in title

    def test_normalize_agentic_task_visual(self):
        from canonical_tools import normalize_agentic_task
        task = normalize_agentic_task(
            "visual",
            {"action": "capture_window", "title": "Notepad", "hwnd": 12345},
            "task-vis-1",
        )
        assert task["tool"] == "visual"
        assert task["action"] == "capture_window"
        assert task["params"]["title"] == "Notepad"
        assert task["params"]["hwnd"] == 12345
