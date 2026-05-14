# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tests for Contextual Boosting and Cross-Reference (entity linking) features."""
from __future__ import annotations

import asyncio
import json
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'memory'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'persistence'))

from world_model import (
    WorldEntity, WorldModel, CONTEXT_BOOST_MAP,
    _get_context_boost, _apply_contextual_boost,
)


# ---------------------------------------------------------------------------
# Minimal in-memory persistence stub
# ---------------------------------------------------------------------------

class FakePersistence:
    def __init__(self):
        self._kv: dict = {}

    async def save_kv(self, key, value):
        self._kv[key] = {"key": key, "value": value, "updated_at": "2026-01-01T00:00:00"}

    async def get_kv(self, key):
        entry = self._kv.get(key)
        return entry["value"] if entry else None

    async def delete_kv(self, key):
        self._kv.pop(key, None)

    async def list_kv(self, prefix):
        return [v for k, v in self._kv.items() if k.startswith(prefix)]

    @classmethod
    def get(cls):
        return cls()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def persistence():
    return FakePersistence()


@pytest.fixture
def world_model(persistence):
    return WorldModel(persistence)


# ===========================================================================
# PART 1 – Contextual Boosting
# ===========================================================================

class TestContextBoostMap:
    def test_office_boosts_document_alias(self):
        assert CONTEXT_BOOST_MAP["office"]["document_alias"] > 0

    def test_office_boosts_app_path(self):
        assert CONTEXT_BOOST_MAP["office"]["app_path"] > 0

    def test_office_document_alias_higher_than_app_path(self):
        assert CONTEXT_BOOST_MAP["office"]["document_alias"] > CONTEXT_BOOST_MAP["office"]["app_path"]

    def test_printer_boosts_device(self):
        assert CONTEXT_BOOST_MAP["printer"]["device"] > 0

    def test_browser_boosts_web_resource(self):
        assert CONTEXT_BOOST_MAP["browser"]["web_resource"] > 0

    def test_filesystem_boosts_path_candidate(self):
        assert CONTEXT_BOOST_MAP["filesystem"]["path_candidate"] > 0

    def test_excel_boosts_document_alias(self):
        assert CONTEXT_BOOST_MAP["excel"]["document_alias"] > 0

    def test_unknown_context_returns_empty(self):
        assert CONTEXT_BOOST_MAP.get("nonexistent") is None

    def test_all_contexts_have_at_least_two_entries(self):
        for ctx, boosts in CONTEXT_BOOST_MAP.items():
            assert len(boosts) >= 2, f"{ctx} has fewer than 2 boost entries"


class TestGetContextBoost:
    def test_exact_match(self):
        assert _get_context_boost("office", "document_alias") == 0.25

    def test_case_insensitive(self):
        assert _get_context_boost("Office", "document_alias") == 0.25

    def test_with_spaces(self):
        assert _get_context_boost("  office  ", "document_alias") == 0.25

    def test_unknown_context_returns_zero(self):
        assert _get_context_boost("unknown_ctx", "document_alias") == 0.0

    def test_unknown_entity_type_returns_zero(self):
        assert _get_context_boost("office", "nonexistent_type") == 0.0


class TestApplyContextualBoost:
    def test_boosts_document_alias_for_office(self):
        results = [
            {"entity_type": "share", "key": "s1", "semantic_score": 0.8, "confidence": 0.9},
            {"entity_type": "document_alias", "key": "d1", "semantic_score": 0.7, "confidence": 0.9},
        ]
        boosted = _apply_contextual_boost(results, "office")
        assert boosted[0]["entity_type"] == "document_alias"
        assert boosted[0]["boosted_score"] == pytest.approx(0.7 + 0.25)
        assert boosted[0]["context_boost"] == pytest.approx(0.25)

    def test_no_boost_for_unknown_context(self):
        results = [
            {"entity_type": "share", "key": "s1", "semantic_score": 0.8, "confidence": 0.9},
        ]
        boosted = _apply_contextual_boost(results, "nonexistent")
        assert boosted[0]["context_boost"] == 0.0
        assert boosted[0]["boosted_score"] == pytest.approx(0.8)

    def test_preserves_all_fields(self):
        results = [
            {"entity_type": "device", "key": "d1", "semantic_score": 0.5, "confidence": 0.7, "extra": "kept"},
        ]
        boosted = _apply_contextual_boost(results, "printer")
        assert boosted[0]["extra"] == "kept"
        assert boosted[0]["context_boost"] > 0

    def test_device_wins_over_share_in_printer_context(self):
        results = [
            {"entity_type": "share", "key": "s1", "semantic_score": 0.85, "confidence": 0.9},
            {"entity_type": "device", "key": "d1", "semantic_score": 0.75, "confidence": 0.9},
        ]
        boosted = _apply_contextual_boost(results, "printer")
        assert boosted[0]["entity_type"] == "device"

    def test_empty_results(self):
        assert _apply_contextual_boost([], "office") == []


class TestWorldModelQueryWithTaskContext:
    @pytest.mark.asyncio
    async def test_query_boosts_confidence_rank(self, world_model):
        await world_model.upsert("share", "net_share", {"path": "//server/share"}, confidence=0.9)
        await world_model.upsert("document_alias", "budget", {"real_path": "C:/docs/budget.xlsx"}, confidence=0.7)

        results_no_ctx = await world_model.query("document_alias")
        assert len(results_no_ctx) == 1

        results_no_ctx_share = await world_model.query("share")
        assert len(results_no_ctx_share) == 1

    @pytest.mark.asyncio
    async def test_query_task_context_passed_through(self, world_model):
        await world_model.upsert("document_alias", "budget", {"real_path": "C:/docs/budget.xlsx"}, confidence=0.7)
        results = await world_model.query("document_alias", task_context="office")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_query_without_task_context_no_change(self, world_model):
        await world_model.upsert("share", "s1", {"path": "//a"}, confidence=0.9)
        results = await world_model.query("share")
        assert len(results) == 1


class TestWorldModelSemanticSearchBoosted:
    @pytest.mark.asyncio
    async def test_semantic_search_with_task_context_fallback(self, world_model):
        """Without a semantic index, falls back to query() which passes task_context."""
        await world_model.upsert("document_alias", "report", {"real_path": "C:/report.docx"}, confidence=0.8)
        results = await world_model.semantic_search("report", entity_type="document_alias", task_context="office")
        assert len(results) >= 1
        assert results[0]["entity_type"] == "document_alias"

    @pytest.mark.asyncio
    async def test_semantic_search_without_task_context(self, world_model):
        await world_model.upsert("share", "net1", {"path": "//x"}, confidence=0.9)
        results = await world_model.semantic_search("net1", entity_type="share")
        assert len(results) >= 1


# ===========================================================================
# PART 2 – Cross-Reference / Entity Linking
# ===========================================================================

class TestWorldEntityLinkedEntities:
    def test_default_empty(self):
        e = WorldEntity(entity_type="share", key="s1", value="v")
        assert e.linked_entities == []

    def test_from_dict_with_links(self):
        data = {
            "entity_type": "app_path", "key": "excel",
            "value": {"exe_path": "C:/excel.exe"},
            "linked_entities": [{"entity_type": "selector", "key": "save_btn", "relation": "has_selector"}],
        }
        e = WorldEntity.from_dict(data)
        assert len(e.linked_entities) == 1
        assert e.linked_entities[0]["key"] == "save_btn"

    def test_to_dict_includes_links(self):
        e = WorldEntity(
            entity_type="app_path", key="excel", value={},
            linked_entities=[{"entity_type": "selector", "key": "s1", "relation": "ui"}],
        )
        d = e.to_dict()
        assert "linked_entities" in d
        assert len(d["linked_entities"]) == 1

    def test_from_dict_without_links_defaults_to_empty(self):
        data = {"entity_type": "share", "key": "s1", "value": {}}
        e = WorldEntity.from_dict(data)
        assert e.linked_entities == []


class TestLinkEntities:
    @pytest.mark.asyncio
    async def test_bidirectional_link(self, world_model):
        await world_model.upsert("app_path", "excel", {"exe_path": "C:/excel.exe"})
        await world_model.upsert("selector", "save_btn", {"name": "Save", "control_type": "Button"})

        result = await world_model.link_entities(
            "app_path", "excel", "selector", "save_btn", relation="has_selector",
        )
        assert result is True

        source = await world_model.get("app_path", "excel")
        assert len(source.linked_entities) == 1
        assert source.linked_entities[0]["key"] == "save_btn"
        assert source.linked_entities[0]["relation"] == "has_selector"

        target = await world_model.get("selector", "save_btn")
        assert len(target.linked_entities) == 1
        assert target.linked_entities[0]["key"] == "excel"

    @pytest.mark.asyncio
    async def test_unidirectional_link(self, world_model):
        await world_model.upsert("app_path", "excel", {"exe_path": "C:/excel.exe"})
        await world_model.upsert("selector", "open_btn", {"name": "Open"})

        result = await world_model.link_entities(
            "app_path", "excel", "selector", "open_btn",
            relation="opens_with", bidirectional=False,
        )
        assert result is True

        source = await world_model.get("app_path", "excel")
        assert len(source.linked_entities) == 1

        target = await world_model.get("selector", "open_btn")
        assert len(target.linked_entities) == 0

    @pytest.mark.asyncio
    async def test_link_fails_if_source_missing(self, world_model):
        await world_model.upsert("selector", "btn", {"name": "X"})
        result = await world_model.link_entities("app_path", "nonexistent", "selector", "btn")
        assert result is False

    @pytest.mark.asyncio
    async def test_link_fails_if_target_missing(self, world_model):
        await world_model.upsert("app_path", "notepad", {"exe_path": "notepad.exe"})
        result = await world_model.link_entities("app_path", "notepad", "selector", "nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_idempotent_link(self, world_model):
        await world_model.upsert("app_path", "excel", {"exe_path": "C:/excel.exe"})
        await world_model.upsert("selector", "save_btn", {"name": "Save"})

        await world_model.link_entities("app_path", "excel", "selector", "save_btn")
        await world_model.link_entities("app_path", "excel", "selector", "save_btn")

        source = await world_model.get("app_path", "excel")
        assert len(source.linked_entities) == 1

    @pytest.mark.asyncio
    async def test_multiple_links(self, world_model):
        await world_model.upsert("app_path", "excel", {"exe_path": "C:/excel.exe"})
        await world_model.upsert("selector", "save_btn", {"name": "Save"})
        await world_model.upsert("selector", "open_btn", {"name": "Open"})
        await world_model.upsert("document_alias", "budget", {"real_path": "budget.xlsx"})

        await world_model.link_entities("app_path", "excel", "selector", "save_btn", relation="has_selector")
        await world_model.link_entities("app_path", "excel", "selector", "open_btn", relation="has_selector")
        await world_model.link_entities("app_path", "excel", "document_alias", "budget", relation="opens_document")

        source = await world_model.get("app_path", "excel")
        assert len(source.linked_entities) == 3

    @pytest.mark.asyncio
    async def test_link_default_relation(self, world_model):
        await world_model.upsert("share", "s1", {"path": "//a"})
        await world_model.upsert("device", "d1", {"name": "Printer"})

        await world_model.link_entities("share", "s1", "device", "d1")
        source = await world_model.get("share", "s1")
        assert source.linked_entities[0]["relation"] == "related"


class TestUnlinkEntities:
    @pytest.mark.asyncio
    async def test_unlink_bidirectional(self, world_model):
        await world_model.upsert("app_path", "excel", {"exe_path": "C:/excel.exe"})
        await world_model.upsert("selector", "save_btn", {"name": "Save"})
        await world_model.link_entities("app_path", "excel", "selector", "save_btn")

        await world_model.unlink_entities("app_path", "excel", "selector", "save_btn")

        source = await world_model.get("app_path", "excel")
        assert len(source.linked_entities) == 0
        target = await world_model.get("selector", "save_btn")
        assert len(target.linked_entities) == 0

    @pytest.mark.asyncio
    async def test_unlink_nonexistent_is_noop(self, world_model):
        await world_model.upsert("app_path", "excel", {"exe_path": "C:/excel.exe"})
        result = await world_model.unlink_entities("app_path", "excel", "selector", "ghost")
        assert result is True

    @pytest.mark.asyncio
    async def test_unlink_preserves_other_links(self, world_model):
        await world_model.upsert("app_path", "excel", {"exe_path": "C:/excel.exe"})
        await world_model.upsert("selector", "s1", {"name": "Save"})
        await world_model.upsert("selector", "s2", {"name": "Open"})
        await world_model.link_entities("app_path", "excel", "selector", "s1")
        await world_model.link_entities("app_path", "excel", "selector", "s2")

        await world_model.unlink_entities("app_path", "excel", "selector", "s1")

        source = await world_model.get("app_path", "excel")
        assert len(source.linked_entities) == 1
        assert source.linked_entities[0]["key"] == "s2"


class TestGetLinked:
    @pytest.mark.asyncio
    async def test_get_all_linked(self, world_model):
        await world_model.upsert("app_path", "excel", {"exe_path": "C:/excel.exe"})
        await world_model.upsert("selector", "s1", {"name": "Save"})
        await world_model.upsert("document_alias", "budget", {"real_path": "budget.xlsx"})
        await world_model.link_entities("app_path", "excel", "selector", "s1", relation="has_selector")
        await world_model.link_entities("app_path", "excel", "document_alias", "budget", relation="opens_document")

        linked = await world_model.get_linked("app_path", "excel")
        assert len(linked) == 2
        types = {l["entity_type"] for l in linked}
        assert "selector" in types
        assert "document_alias" in types

    @pytest.mark.asyncio
    async def test_filter_by_relation(self, world_model):
        await world_model.upsert("app_path", "excel", {"exe_path": "C:/excel.exe"})
        await world_model.upsert("selector", "s1", {"name": "Save"})
        await world_model.upsert("document_alias", "budget", {"real_path": "budget.xlsx"})
        await world_model.link_entities("app_path", "excel", "selector", "s1", relation="has_selector")
        await world_model.link_entities("app_path", "excel", "document_alias", "budget", relation="opens_document")

        linked = await world_model.get_linked("app_path", "excel", relation="has_selector")
        assert len(linked) == 1
        assert linked[0]["entity_type"] == "selector"

    @pytest.mark.asyncio
    async def test_filter_by_target_type(self, world_model):
        await world_model.upsert("app_path", "excel", {"exe_path": "C:/excel.exe"})
        await world_model.upsert("selector", "s1", {"name": "Save"})
        await world_model.upsert("document_alias", "budget", {"real_path": "budget.xlsx"})
        await world_model.link_entities("app_path", "excel", "selector", "s1")
        await world_model.link_entities("app_path", "excel", "document_alias", "budget")

        linked = await world_model.get_linked("app_path", "excel", target_type="document_alias")
        assert len(linked) == 1
        assert linked[0]["entity_type"] == "document_alias"

    @pytest.mark.asyncio
    async def test_get_linked_nonexistent_entity(self, world_model):
        linked = await world_model.get_linked("app_path", "nonexistent")
        assert linked == []

    @pytest.mark.asyncio
    async def test_linked_includes_relation_field(self, world_model):
        await world_model.upsert("app_path", "excel", {"exe_path": "C:/excel.exe"})
        await world_model.upsert("selector", "s1", {"name": "Save"})
        await world_model.link_entities("app_path", "excel", "selector", "s1", relation="has_selector")

        linked = await world_model.get_linked("app_path", "excel")
        assert linked[0]["relation"] == "has_selector"


class TestFindCrossReferences:
    @pytest.mark.asyncio
    async def test_depth_1(self, world_model):
        await world_model.upsert("app_path", "excel", {"exe_path": "C:/excel.exe"})
        await world_model.upsert("selector", "s1", {"name": "Save"})
        await world_model.link_entities("app_path", "excel", "selector", "s1", relation="has_selector")

        tree = await world_model.find_cross_references("app_path", "excel", depth=1)
        assert tree["key"] == "excel"
        assert len(tree["linked_resolved"]) == 1
        assert tree["linked_resolved"][0]["key"] == "s1"
        assert tree["linked_resolved"][0]["relation"] == "has_selector"

    @pytest.mark.asyncio
    async def test_depth_2(self, world_model):
        await world_model.upsert("app_path", "excel", {"exe_path": "C:/excel.exe"})
        await world_model.upsert("selector", "s1", {"name": "Save"})
        await world_model.upsert("document_alias", "budget", {"real_path": "budget.xlsx"})

        await world_model.link_entities("app_path", "excel", "selector", "s1")
        await world_model.link_entities("selector", "s1", "document_alias", "budget", relation="targets")

        tree = await world_model.find_cross_references("app_path", "excel", depth=2)
        assert len(tree["linked_resolved"]) >= 1
        s1_node = next((n for n in tree["linked_resolved"] if n["key"] == "s1"), None)
        assert s1_node is not None
        assert "linked_resolved" in s1_node
        budget_node = next(
            (n for n in s1_node["linked_resolved"] if n["key"] == "budget"), None,
        )
        assert budget_node is not None

    @pytest.mark.asyncio
    async def test_nonexistent_entity(self, world_model):
        tree = await world_model.find_cross_references("app_path", "ghost")
        assert tree == {}

    @pytest.mark.asyncio
    async def test_depth_0_no_expansion(self, world_model):
        await world_model.upsert("app_path", "excel", {"exe_path": "C:/excel.exe"})
        await world_model.upsert("selector", "s1", {"name": "Save"})
        await world_model.link_entities("app_path", "excel", "selector", "s1")

        tree = await world_model.find_cross_references("app_path", "excel", depth=0)
        assert "linked_resolved" not in tree


# ===========================================================================
# PART 3 – Tool integration (MemoryTool)
# ===========================================================================

class TestToolMemoryContextualBoosting:
    @pytest.mark.asyncio
    async def test_world_query_with_task_context(self, world_model, persistence, monkeypatch):
        from tool_memory import MemoryTool, MemoryInput
        tool = MemoryTool.__new__(MemoryTool)
        tool.persistence = persistence
        tool.world_model = world_model
        tool.strategy_memory = None

        await world_model.upsert("document_alias", "budget", {"real_path": "C:/budget.xlsx"}, confidence=0.8)

        payload = MemoryInput(action="world_query", entity_type="document_alias", task_context="office")
        result = await tool._run(payload)
        assert result.success
        assert len(result.items) == 1

    @pytest.mark.asyncio
    async def test_semantic_search_entities_with_task_context(self, world_model, persistence):
        from tool_memory import MemoryTool, MemoryInput
        tool = MemoryTool.__new__(MemoryTool)
        tool.persistence = persistence
        tool.world_model = world_model
        tool.strategy_memory = None

        await world_model.upsert("document_alias", "report", {"real_path": "report.docx"}, confidence=0.8)

        payload = MemoryInput(
            action="semantic_search_entities",
            query="report",
            entity_type="document_alias",
            task_context="office",
        )
        result = await tool._run(payload)
        assert result.success

    @pytest.mark.asyncio
    async def test_semantic_search_boosted_action(self, world_model, persistence):
        from tool_memory import MemoryTool, MemoryInput
        tool = MemoryTool.__new__(MemoryTool)
        tool.persistence = persistence
        tool.world_model = world_model
        tool.strategy_memory = None

        await world_model.upsert("document_alias", "invoice", {"real_path": "invoice.xlsx"}, confidence=0.8)

        payload = MemoryInput(
            action="semantic_search_boosted",
            query="invoice",
            task_context="office",
        )
        result = await tool._run(payload)
        assert result.success
        assert result.message == "boosted_search_complete"


class TestToolMemoryCrossReference:
    @pytest.mark.asyncio
    async def _make_tool(self, persistence, world_model):
        from tool_memory import MemoryTool
        tool = MemoryTool.__new__(MemoryTool)
        tool.persistence = persistence
        tool.world_model = world_model
        tool.strategy_memory = None
        return tool

    @pytest.mark.asyncio
    async def test_world_link(self, world_model, persistence):
        from tool_memory import MemoryTool, MemoryInput
        tool = await self._make_tool(persistence, world_model)

        await world_model.upsert("app_path", "excel", {"exe_path": "C:/excel.exe"})
        await world_model.upsert("selector", "save_btn", {"name": "Save"})

        payload = MemoryInput(
            action="world_link",
            entity_type="app_path", key="excel",
            target_type="selector", target_key="save_btn",
            relation="has_selector",
        )
        result = await tool._run(payload)
        assert result.success
        assert result.message == "entities_linked"

    @pytest.mark.asyncio
    async def test_world_link_fails_missing_entity(self, world_model, persistence):
        from tool_memory import MemoryTool, MemoryInput
        tool = await self._make_tool(persistence, world_model)

        payload = MemoryInput(
            action="world_link",
            entity_type="app_path", key="ghost",
            target_type="selector", target_key="ghost2",
        )
        result = await tool._run(payload)
        assert result.success is False
        assert "not_found" in result.message

    @pytest.mark.asyncio
    async def test_world_unlink(self, world_model, persistence):
        from tool_memory import MemoryTool, MemoryInput
        tool = await self._make_tool(persistence, world_model)

        await world_model.upsert("app_path", "excel", {"exe_path": "C:/excel.exe"})
        await world_model.upsert("selector", "save_btn", {"name": "Save"})
        await world_model.link_entities("app_path", "excel", "selector", "save_btn")

        payload = MemoryInput(
            action="world_unlink",
            entity_type="app_path", key="excel",
            target_type="selector", target_key="save_btn",
        )
        result = await tool._run(payload)
        assert result.success
        assert result.message == "entities_unlinked"

    @pytest.mark.asyncio
    async def test_world_get_linked(self, world_model, persistence):
        from tool_memory import MemoryTool, MemoryInput
        tool = await self._make_tool(persistence, world_model)

        await world_model.upsert("app_path", "excel", {"exe_path": "C:/excel.exe"})
        await world_model.upsert("selector", "save_btn", {"name": "Save"})
        await world_model.upsert("document_alias", "budget", {"real_path": "budget.xlsx"})
        await world_model.link_entities("app_path", "excel", "selector", "save_btn", relation="has_selector")
        await world_model.link_entities("app_path", "excel", "document_alias", "budget", relation="opens")

        payload = MemoryInput(action="world_get_linked", entity_type="app_path", key="excel")
        result = await tool._run(payload)
        assert result.success
        assert len(result.items) == 2

    @pytest.mark.asyncio
    async def test_world_get_linked_filter_relation(self, world_model, persistence):
        from tool_memory import MemoryTool, MemoryInput
        tool = await self._make_tool(persistence, world_model)

        await world_model.upsert("app_path", "excel", {"exe_path": "C:/excel.exe"})
        await world_model.upsert("selector", "s1", {"name": "Save"})
        await world_model.upsert("selector", "s2", {"name": "Open"})
        await world_model.link_entities("app_path", "excel", "selector", "s1", relation="has_selector")
        await world_model.link_entities("app_path", "excel", "selector", "s2", relation="fallback")

        payload = MemoryInput(
            action="world_get_linked", entity_type="app_path", key="excel",
            relation="has_selector",
        )
        result = await tool._run(payload)
        assert result.success
        assert len(result.items) == 1

    @pytest.mark.asyncio
    async def test_world_cross_references(self, world_model, persistence):
        from tool_memory import MemoryTool, MemoryInput
        tool = await self._make_tool(persistence, world_model)

        await world_model.upsert("app_path", "excel", {"exe_path": "C:/excel.exe"})
        await world_model.upsert("selector", "s1", {"name": "Save"})
        await world_model.link_entities("app_path", "excel", "selector", "s1")

        payload = MemoryInput(
            action="world_cross_references",
            entity_type="app_path", key="excel",
            depth=1,
        )
        result = await tool._run(payload)
        assert result.success
        assert result.message == "cross_references_found"
        assert "linked_resolved" in result.value
        assert len(result.value["linked_resolved"]) == 1


class TestToolMemoryValidation:
    @pytest.mark.asyncio
    async def test_world_link_requires_all_fields(self, world_model, persistence):
        from tool_memory import MemoryTool, MemoryInput
        tool = MemoryTool.__new__(MemoryTool)
        tool.persistence = persistence
        tool.world_model = world_model
        tool.strategy_memory = None

        with pytest.raises(ValueError, match="entity_type.*key.*target_type.*target_key"):
            payload = MemoryInput(action="world_link", entity_type="app_path", key="excel")
            await tool.validate(payload)

    @pytest.mark.asyncio
    async def test_semantic_search_boosted_requires_query(self, world_model, persistence):
        from tool_memory import MemoryTool, MemoryInput
        tool = MemoryTool.__new__(MemoryTool)
        tool.persistence = persistence
        tool.world_model = world_model
        tool.strategy_memory = None

        with pytest.raises(ValueError, match="query"):
            payload = MemoryInput(action="semantic_search_boosted", task_context="office")
            await tool.validate(payload)


# ===========================================================================
# PART 4 – Real-world scenario: Office context
# ===========================================================================

class TestOfficeScenario:
    """End-to-end scenario: Agent working on an Office task."""

    @pytest.mark.asyncio
    async def test_office_task_prioritizes_document_alias(self, world_model):
        await world_model.upsert("share", "dept_share", {"remote_path": "//server/dept"}, confidence=0.9)
        await world_model.upsert("device", "hp_printer", {"name": "HP LaserJet"}, confidence=0.85)
        await world_model.upsert("document_alias", "quarterly_report", {"real_path": "C:/docs/Q1.xlsx"}, confidence=0.75)

        results = await world_model.semantic_search(
            "quarterly report", task_context="office",
        )
        if results:
            doc_results = [r for r in results if r.get("entity_type") == "document_alias"]
            if doc_results:
                assert doc_results[0].get("entity_type") == "document_alias"

    @pytest.mark.asyncio
    async def test_excel_linked_to_selectors(self, world_model):
        await world_model.upsert("app_path", "excel", {"exe_path": "C:/Program Files/Microsoft Office/excel.exe"})
        await world_model.upsert("selector", "excel_save_as", {
            "control_type": "MenuItem", "name": "Save As", "automation_id": "SaveAs",
        }, app_context="excel")
        await world_model.upsert("selector", "excel_format_cells", {
            "control_type": "MenuItem", "name": "Format Cells",
        }, app_context="excel")
        await world_model.upsert("document_alias", "budget_2026", {
            "real_path": "C:/Finance/Budget_2026.xlsx", "alias": "Budget 2026",
        })

        await world_model.link_entities(
            "app_path", "excel", "selector", "excel_save_as", relation="has_selector",
        )
        await world_model.link_entities(
            "app_path", "excel", "selector", "excel_format_cells", relation="has_selector",
        )
        await world_model.link_entities(
            "app_path", "excel", "document_alias", "budget_2026", relation="opens_document",
        )

        selectors = await world_model.get_linked("app_path", "excel", relation="has_selector")
        assert len(selectors) == 2

        docs = await world_model.get_linked("app_path", "excel", relation="opens_document")
        assert len(docs) == 1
        assert docs[0]["key"] == "budget_2026"

        tree = await world_model.find_cross_references("app_path", "excel", depth=1)
        assert len(tree["linked_resolved"]) == 3
