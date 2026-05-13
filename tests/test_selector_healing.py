"""Comprehensive test suite for proactive Selector Healing.

Tests:
  - SelectorHealer: heal(), _find_healing_candidates(), record_successful_selector()
  - _selector_intent_key, _selector_similarity helpers
  - HealingResult serialization
  - WindowsUiTool._attempt_selector_healing integration
  - WindowsUiAgent set_world_model / _get_healer / _schedule_world_model_record
  - WorldModel.query_similar_selectors
  - End-to-end flow: fail -> heal from world model -> succeed
"""
from __future__ import annotations

import asyncio
import os
import sys
import pytest
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from selector_healing import (
    HEAL_CONFIDENCE_BOOST,
    HEAL_CONFIDENCE_ON_SUCCESS,
    HEAL_MIN_SCORE,
    HealingResult,
    SelectorHealer,
    _selector_intent_key,
    _selector_similarity,
)
from world_model import WorldEntity, WorldModel
from agent_persistence import Persistence


# ─── Helpers ────────────────────────────────────────────────────────


def _make_entity(
    key: str,
    value: Dict[str, Any],
    *,
    confidence: float = 0.85,
    entity_type: str = "selector",
    app_context: Optional[str] = None,
) -> WorldEntity:
    return WorldEntity(
        entity_type=entity_type,
        key=key,
        value=value,
        confidence=confidence,
        source="test",
        tags=["ui", "selector"],
        app_context=app_context,
    )


class FakeWorldModel:
    """Lightweight world model stub for unit tests."""
    def __init__(self, entities: Optional[List[WorldEntity]] = None):
        self.entities = entities or []
        self.remembered: List[Dict[str, Any]] = []
        self.touched: List[Dict[str, Any]] = []

    async def query(self, entity_type, *, query=None, app_context=None, min_confidence=0.0, limit=50, **kwargs):
        results = []
        for e in self.entities:
            if e.entity_type != entity_type:
                continue
            if min_confidence and e.effective_confidence < min_confidence:
                continue
            if app_context and e.app_context != app_context:
                continue
            if query:
                import json
                searchable = f"{e.key} {json.dumps(e.value, default=str)}".lower()
                if query.lower() not in searchable:
                    continue
            results.append(e)
        results.sort(key=lambda e: -e.effective_confidence)
        return results[:limit]

    async def remember_selector(self, key, data, *, app_context=None, confidence=0.85, source="test"):
        self.remembered.append({"key": key, "data": data, "app_context": app_context, "confidence": confidence, "source": source})
        return _make_entity(key, data, confidence=confidence, app_context=app_context)

    async def touch(self, entity_type, key, *, boost_confidence=0.0):
        self.touched.append({"entity_type": entity_type, "key": key, "boost": boost_confidence})
        return _make_entity(key, {}, confidence=0.85)


class FakeUiAgent:
    """Lightweight WindowsUiAgent stub for unit tests."""
    def __init__(self, *, resolve_result=None):
        self.resolve_result = resolve_result
        self.resolve_calls = 0
        self._world_model = None
        self._selector_healer = None

    def _resolve_candidates(self, *, hwnd=None, title=None, process_name=None, selector=None, include_children=True, max_results=25):
        self.resolve_calls += 1
        if self.resolve_result is None:
            return MagicMock(), []
        window, elements = self.resolve_result
        return window, elements

    def _snapshot(self, wrapper):
        from windows_ui_models import WindowsUiElement
        return WindowsUiElement(
            element_id="healed-element-1",
            title=wrapper.get("title", "Healed"),
            control_type=wrapper.get("control_type", "Button"),
            auto_id=wrapper.get("auto_id"),
            match_score=wrapper.get("match_score", 0.85),
            match_reasons=["healed"],
            selector=wrapper.get("selector", {}),
        )


# ─── _selector_intent_key ──────────────────────────────────────────


def test_intent_key_basic():
    key = _selector_intent_key({"title": "Save", "control_type": "Button"})
    assert "save" in key
    assert "button" in key


def test_intent_key_empty():
    assert _selector_intent_key({}) == ""


def test_intent_key_partial():
    key = _selector_intent_key({"auto_id": "btnSave"})
    assert "btnsave" in key


def test_intent_key_strips_and_lowercases():
    key = _selector_intent_key({"title": "  Save As  ", "near_title": " File "})
    assert "save as" in key
    assert "file" in key


# ─── _selector_similarity ──────────────────────────────────────────


def test_similarity_identical():
    a = {"title": "Save", "control_type": "Button", "auto_id": "btnSave"}
    assert _selector_similarity(a, a) == 1.0


def test_similarity_partial():
    a = {"title": "Save", "control_type": "Button", "auto_id": "btnSave"}
    b = {"title": "Save", "control_type": "Button", "auto_id": "btnSaveAs"}
    sim = _selector_similarity(a, b)
    assert 0.5 < sim < 1.0


def test_similarity_no_overlap():
    a = {"title": "Save", "control_type": "Button"}
    b = {"title": "Open", "control_type": "MenuItem"}
    sim = _selector_similarity(a, b)
    assert sim == 0.0


def test_similarity_empty():
    assert _selector_similarity({}, {}) == 0.0
    assert _selector_similarity({"title": "Save"}, {}) == 0.0


def test_similarity_substring_match():
    a = {"title": "Save", "auto_id": "btn"}
    b = {"title": "Save As", "auto_id": "btnSaveAs"}
    sim = _selector_similarity(a, b)
    assert sim > 0.3


# ─── HealingResult ─────────────────────────────────────────────────


def test_healing_result_defaults():
    r = HealingResult()
    assert r.healed is False
    assert r.candidates_tried == 0
    assert r.reason == ""


def test_healing_result_to_dict():
    r = HealingResult(healed=True, score=0.88, source="world_model", reason="healed_from_world_model", candidates_tried=3)
    d = r.to_dict()
    assert d["healed"] is True
    assert d["score"] == 0.88
    assert d["source"] == "world_model"
    assert d["candidates_tried"] == 3


# ─── SelectorHealer._find_healing_candidates ───────────────────────


@pytest.mark.asyncio
async def test_find_candidates_by_app_context():
    entities = [
        _make_entity("save button", {"title": "Save", "control_type": "Button"}, confidence=0.9, app_context="notepad.exe"),
        _make_entity("open button", {"title": "Open", "control_type": "Button"}, confidence=0.8, app_context="notepad.exe"),
        _make_entity("save button word", {"title": "Save", "control_type": "Button"}, confidence=0.9, app_context="winword.exe"),
    ]
    wm = FakeWorldModel(entities)
    healer = SelectorHealer(wm)

    candidates = await healer._find_healing_candidates(
        intent="save button",
        failed_selector={"title": "Save", "control_type": "Button"},
        app_context="notepad.exe",
    )
    keys = [c[0] for c in candidates]
    assert "save button" in keys
    assert keys[0] == "save button"


@pytest.mark.asyncio
async def test_find_candidates_by_query():
    entities = [
        _make_entity("save button", {"title": "Save", "control_type": "Button"}, confidence=0.9),
    ]
    wm = FakeWorldModel(entities)
    healer = SelectorHealer(wm)

    candidates = await healer._find_healing_candidates(
        intent="save button",
        failed_selector={"title": "Save", "control_type": "Button"},
        app_context=None,
    )
    assert len(candidates) >= 1
    assert candidates[0][0] == "save button"


@pytest.mark.asyncio
async def test_find_candidates_empty():
    wm = FakeWorldModel([])
    healer = SelectorHealer(wm)

    candidates = await healer._find_healing_candidates(
        intent="nonexistent",
        failed_selector={"title": "X"},
        app_context=None,
    )
    assert candidates == []


# ─── SelectorHealer.heal ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_heal_no_candidates():
    wm = FakeWorldModel([])
    healer = SelectorHealer(wm, ui_agent=FakeUiAgent())

    result = await healer.heal(
        failed_selector={"title": "MissingButton"},
        process_name="test.exe",
    )
    assert result.healed is False
    assert result.reason == "no_candidates_in_world_model"


@pytest.mark.asyncio
async def test_heal_candidate_matches_live_ui():
    healed_selector = {"title": "Guardar", "control_type": "Button", "auto_id": "btnSave"}
    entities = [
        _make_entity(
            "save button",
            healed_selector,
            confidence=0.9,
            app_context="notepad.exe",
        ),
    ]
    wm = FakeWorldModel(entities)

    fake_element = {
        "title": "Guardar",
        "control_type": "Button",
        "auto_id": "btnSave",
        "match_score": 0.88,
        "selector": healed_selector,
    }
    fake_agent = FakeUiAgent(resolve_result=(MagicMock(), [fake_element]))

    healer = SelectorHealer(wm, ui_agent=fake_agent)

    result = await healer.heal(
        failed_selector={"title": "Save", "control_type": "Button"},
        process_name="notepad.exe",
    )
    assert result.healed is True
    assert result.score >= HEAL_MIN_SCORE
    assert result.source == "world_model"
    assert result.world_entity_key == "save button"
    assert result.reason == "healed_from_world_model"


@pytest.mark.asyncio
async def test_heal_updates_world_model_on_success():
    healed_selector = {"title": "Guardar", "control_type": "Button"}
    entities = [
        _make_entity("save button", healed_selector, confidence=0.7, app_context="app.exe"),
    ]
    wm = FakeWorldModel(entities)

    fake_element = {
        "title": "Guardar",
        "control_type": "Button",
        "match_score": 0.85,
        "selector": healed_selector,
    }
    fake_agent = FakeUiAgent(resolve_result=(MagicMock(), [fake_element]))
    healer = SelectorHealer(wm, ui_agent=fake_agent)

    await healer.heal(
        failed_selector={"title": "Save", "control_type": "Button"},
        process_name="app.exe",
    )

    assert len(wm.remembered) >= 1
    remembered_keys = [r["key"] for r in wm.remembered]
    assert "save button" in remembered_keys
    assert any(r["confidence"] == HEAL_CONFIDENCE_ON_SUCCESS for r in wm.remembered)

    assert len(wm.touched) >= 1
    assert wm.touched[0]["boost"] == HEAL_CONFIDENCE_BOOST


@pytest.mark.asyncio
async def test_heal_skips_identical_selector():
    same_selector = {"title": "Save", "control_type": "Button"}
    entities = [
        _make_entity("save button", same_selector, confidence=0.9, app_context="app.exe"),
    ]
    wm = FakeWorldModel(entities)

    fake_agent = FakeUiAgent(resolve_result=None)
    healer = SelectorHealer(wm, ui_agent=fake_agent)

    result = await healer.heal(
        failed_selector=same_selector,
        process_name="app.exe",
    )
    assert result.healed is False
    assert result.reason == "no_candidate_matched_live_ui"
    assert fake_agent.resolve_calls == 0


@pytest.mark.asyncio
async def test_heal_candidate_below_min_score():
    healed_selector = {"title": "Guardar", "control_type": "Button"}
    entities = [
        _make_entity("save button", healed_selector, confidence=0.9, app_context="app.exe"),
    ]
    wm = FakeWorldModel(entities)

    fake_element = {
        "title": "Guardar",
        "control_type": "Button",
        "match_score": 0.30,
        "selector": healed_selector,
    }
    fake_agent = FakeUiAgent(resolve_result=(MagicMock(), [fake_element]))
    healer = SelectorHealer(wm, ui_agent=fake_agent)

    result = await healer.heal(
        failed_selector={"title": "Save", "control_type": "Button"},
        process_name="app.exe",
    )
    assert result.healed is False
    assert result.reason == "no_candidate_matched_live_ui"


@pytest.mark.asyncio
async def test_heal_no_ui_agent():
    entities = [
        _make_entity("save button", {"title": "Guardar"}, confidence=0.9),
    ]
    wm = FakeWorldModel(entities)
    healer = SelectorHealer(wm, ui_agent=None)

    result = await healer.heal(
        failed_selector={"title": "Save"},
    )
    assert result.healed is False


@pytest.mark.asyncio
async def test_heal_multiple_candidates_picks_best():
    entities = [
        _make_entity("guardar button", {"title": "Guardar", "control_type": "Button"}, confidence=0.95, app_context="app.exe"),
        _make_entity("salvar button", {"title": "Salvar", "control_type": "Button"}, confidence=0.7, app_context="app.exe"),
    ]
    wm = FakeWorldModel(entities)

    call_count = {"n": 0}
    original_resolve = FakeUiAgent._resolve_candidates

    class MultiAgent(FakeUiAgent):
        def _resolve_candidates(self, **kwargs):
            call_count["n"] += 1
            sel = kwargs.get("selector", {})
            if sel.get("title") == "Guardar":
                element = {"title": "Guardar", "control_type": "Button", "match_score": 0.92, "selector": sel}
                return MagicMock(), [element]
            return MagicMock(), []

    agent = MultiAgent()
    healer = SelectorHealer(wm, ui_agent=agent)

    result = await healer.heal(
        failed_selector={"title": "Save", "control_type": "Button"},
        process_name="app.exe",
    )
    assert result.healed is True
    assert result.score >= 0.90


# ─── SelectorHealer.record_successful_selector ─────────────────────


@pytest.mark.asyncio
async def test_record_successful_selector():
    wm = FakeWorldModel([])
    healer = SelectorHealer(wm)

    await healer.record_successful_selector(
        selector={"title": "OK", "control_type": "Button", "auto_id": "btnOK"},
        process_name="notepad.exe",
        score=0.92,
    )

    assert len(wm.remembered) == 1
    assert wm.remembered[0]["app_context"] == "notepad.exe"
    assert wm.remembered[0]["confidence"] == 0.92


@pytest.mark.asyncio
async def test_record_successful_selector_empty_intent():
    wm = FakeWorldModel([])
    healer = SelectorHealer(wm)

    await healer.record_successful_selector(
        selector={},
        process_name="app.exe",
        score=0.9,
    )
    assert len(wm.remembered) == 0


# ─── WindowsUiAgent integration ────────────────────────────────────


def test_windows_ui_agent_set_world_model():
    from windows_ui_agent import WindowsUiAgent
    agent = WindowsUiAgent()
    assert agent._world_model is None
    assert agent._get_healer() is None

    fake_wm = FakeWorldModel()
    agent.set_world_model(fake_wm)
    assert agent._world_model is fake_wm

    healer = agent._get_healer()
    assert healer is not None
    assert isinstance(healer, SelectorHealer)


def test_windows_ui_agent_get_healer_caches():
    from windows_ui_agent import WindowsUiAgent
    agent = WindowsUiAgent()
    agent.set_world_model(FakeWorldModel())
    h1 = agent._get_healer()
    h2 = agent._get_healer()
    assert h1 is h2


# ─── WindowsUiTool integration ─────────────────────────────────────


def test_tool_set_world_model():
    from tool_windows_ui import WindowsUiTool
    tool = WindowsUiTool()
    fake_wm = FakeWorldModel()
    tool.set_world_model(fake_wm)
    assert tool.agent._world_model is fake_wm


def test_tool_constructor_with_world_model():
    from tool_windows_ui import WindowsUiTool
    fake_wm = FakeWorldModel()
    tool = WindowsUiTool(world_model=fake_wm)
    assert tool.agent._world_model is fake_wm


# ─── WorldModel.query_similar_selectors ─────────────────────────────


@pytest.mark.asyncio
async def test_world_model_query_similar_selectors():
    persistence = Persistence.get()
    wm = WorldModel(persistence)

    await wm.remember_selector("save btn", {"title": "Save", "control_type": "Button"}, app_context="app.exe")
    await wm.remember_selector("open btn", {"title": "Open", "control_type": "Button"}, app_context="app.exe")
    await wm.remember_selector("close btn", {"title": "Close", "control_type": "Button"}, app_context="other.exe")

    results = await wm.query_similar_selectors(app_context="app.exe")
    assert len(results) == 2
    keys = [r.key for r in results]
    assert "save btn" in keys
    assert "open btn" in keys


@pytest.mark.asyncio
async def test_world_model_query_similar_selectors_with_query():
    persistence = Persistence.get()
    wm = WorldModel(persistence)

    await wm.remember_selector("save btn notepad", {"title": "Save"}, app_context="notepad.exe")
    await wm.remember_selector("open btn notepad", {"title": "Open"}, app_context="notepad.exe")

    results = await wm.query_similar_selectors(query="save", app_context="notepad.exe")
    assert len(results) >= 1
    assert results[0].key == "save btn notepad"


# ─── End-to-end healing flow ────────────────────────────────────────


@pytest.mark.asyncio
async def test_end_to_end_healing_flow():
    """Simulate: selector fails -> heal from World Model -> succeed with healed selector."""
    persistence = Persistence.get()
    wm = WorldModel(persistence)

    await wm.remember_selector(
        "guardar button",
        {"title": "Guardar", "control_type": "Button", "auto_id": "btnSave"},
        app_context="myapp.exe",
        confidence=0.90,
        source="previous_run",
    )

    class MockUiAgent:
        def __init__(self):
            self._world_model = wm
            self._selector_healer = None
            self.resolve_calls = []

        def _resolve_candidates(self, *, hwnd=None, title=None, process_name=None, selector=None, include_children=True, max_results=25):
            self.resolve_calls.append(selector)
            if selector and selector.get("title") == "Guardar":
                from windows_ui_models import WindowsUiElement
                element = WindowsUiElement(
                    element_id="el-healed",
                    title="Guardar",
                    control_type="Button",
                    auto_id="btnSave",
                    match_score=0.88,
                    match_reasons=["title", "control_type", "auto_id"],
                    selector=selector,
                )
                return MagicMock(), [element]
            return MagicMock(), []

        def _snapshot(self, wrapper):
            if hasattr(wrapper, "element_id"):
                return wrapper
            from windows_ui_models import WindowsUiElement
            return WindowsUiElement(
                element_id="el-snapshot",
                title="Guardar",
                control_type="Button",
                match_score=0.88,
                selector={},
            )

        def _get_healer(self):
            if self._selector_healer is None:
                self._selector_healer = SelectorHealer(self._world_model, ui_agent=self)
            return self._selector_healer

    agent = MockUiAgent()
    healer = agent._get_healer()

    failed_selector = {"title": "Save", "control_type": "Button"}
    result = await healer.heal(
        failed_selector=failed_selector,
        process_name="myapp.exe",
    )

    assert result.healed is True
    assert result.healed_selector["title"] == "Guardar"
    assert result.score >= 0.60
    assert result.source == "world_model"
    assert result.world_entity_key == "guardar button"

    entity = await wm.find_selector("guardar button", app_context="myapp.exe")
    assert entity is not None
    assert entity.confidence >= 0.85


@pytest.mark.asyncio
async def test_healing_records_alias_for_original():
    """When healing succeeds, the original intent should also be stored as an alias."""
    wm = FakeWorldModel([
        _make_entity(
            "guardar button",
            {"title": "Guardar", "control_type": "Button"},
            confidence=0.9,
            app_context="app.exe",
        ),
    ])

    fake_element = {"title": "Guardar", "control_type": "Button", "match_score": 0.85, "selector": {"title": "Guardar"}}
    fake_agent = FakeUiAgent(resolve_result=(MagicMock(), [fake_element]))
    healer = SelectorHealer(wm, ui_agent=fake_agent)

    await healer.heal(
        failed_selector={"title": "Save", "control_type": "Button"},
        process_name="app.exe",
    )

    remembered_keys = [r["key"] for r in wm.remembered]
    assert "guardar button" in remembered_keys
    assert any(r["source"] == "selector_healing_alias" for r in wm.remembered)


@pytest.mark.asyncio
async def test_healing_counts_candidates_tried():
    entities = [
        _make_entity("btn1", {"title": "B1", "control_type": "Button"}, confidence=0.9, app_context="a.exe"),
        _make_entity("btn2", {"title": "B2", "control_type": "Button"}, confidence=0.8, app_context="a.exe"),
    ]
    wm = FakeWorldModel(entities)

    class CountingAgent(FakeUiAgent):
        def _resolve_candidates(self, **kwargs):
            sel = kwargs.get("selector", {})
            if sel.get("title") == "B2":
                element = {"title": "B2", "control_type": "Button", "match_score": 0.75, "selector": sel}
                return MagicMock(), [element]
            return MagicMock(), []

    agent = CountingAgent()
    healer = SelectorHealer(wm, ui_agent=agent)

    result = await healer.heal(
        failed_selector={"title": "Save", "control_type": "Button"},
        process_name="a.exe",
    )
    assert result.healed is True
    assert result.candidates_tried == 2
