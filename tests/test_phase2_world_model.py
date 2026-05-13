"""Tests for Phase 2: World Model, Strategy Memory, and integration."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# --- Fixtures ---

@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test_phase2.db")


@pytest.fixture
def persistence(tmp_db):
    from agent_persistence import Persistence
    p = Persistence(db_path=tmp_db)
    return p


@pytest.fixture
def world_model(persistence):
    from world_model import WorldModel
    return WorldModel(persistence=persistence)


@pytest.fixture
def strategy_memory(persistence):
    from strategy_memory import StrategyMemory
    return StrategyMemory(persistence=persistence)


# --- WorldEntity unit tests ---

def test_world_entity_creation():
    from world_model import WorldEntity
    entity = WorldEntity(entity_type="share", key="test_share", value={"remote_path": "\\\\server\\share"})
    assert entity.entity_type == "share"
    assert entity.key == "test_share"
    assert entity.confidence == 0.8
    assert entity.is_expired is False
    assert entity.effective_confidence <= 0.8
    assert entity.hit_count == 0


def test_world_entity_confidence_clamping():
    from world_model import WorldEntity
    entity = WorldEntity(entity_type="share", key="test", value={}, confidence=1.5)
    assert entity.confidence == 1.0
    entity2 = WorldEntity(entity_type="share", key="test2", value={}, confidence=-0.5)
    assert entity2.confidence == 0.0


def test_world_entity_expiration():
    from world_model import WorldEntity
    past = (datetime.utcnow() - timedelta(days=1)).isoformat()
    entity = WorldEntity(entity_type="share", key="expired", value={}, expires_at=past)
    assert entity.is_expired is True


def test_world_entity_confidence_decay():
    from world_model import WorldEntity
    old_date = (datetime.utcnow() - timedelta(days=10)).isoformat()
    entity = WorldEntity(entity_type="share", key="old", value={}, confidence=0.8, updated_at=old_date)
    assert entity.effective_confidence < 0.8
    assert entity.effective_confidence >= 0.1


def test_world_entity_to_dict_and_back():
    from world_model import WorldEntity
    entity = WorldEntity(
        entity_type="app_path", key="notepad", value={"exe_path": "C:\\Windows\\notepad.exe"},
        confidence=0.95, source="discovery", tags=["app", "system"],
    )
    d = entity.to_dict()
    assert d["entity_type"] == "app_path"
    assert d["key"] == "notepad"
    assert d["confidence"] == 0.95
    assert "effective_confidence" in d
    assert d["tags"] == ["app", "system"]
    restored = WorldEntity.from_dict(d)
    assert restored.entity_type == entity.entity_type
    assert restored.key == entity.key
    assert restored.confidence == entity.confidence


def test_entity_types_defined():
    from world_model import ENTITY_TYPES
    assert "share" in ENTITY_TYPES
    assert "app_path" in ENTITY_TYPES
    assert "document_alias" in ENTITY_TYPES
    assert "selector" in ENTITY_TYPES
    assert "path_candidate" in ENTITY_TYPES


# --- WorldModel async tests ---

@pytest.mark.asyncio
async def test_world_model_upsert_and_get(world_model):
    entity = await world_model.upsert("share", "my_share", {"remote_path": "\\\\server\\data"}, confidence=0.9)
    assert entity.entity_type == "share"
    assert entity.key == "my_share"
    assert entity.confidence == 0.9

    retrieved = await world_model.get("share", "my_share")
    assert retrieved is not None
    assert retrieved.key == "my_share"
    assert retrieved.hit_count == 1


@pytest.mark.asyncio
async def test_world_model_upsert_updates_existing(world_model):
    await world_model.upsert("app_path", "notepad", {"exe_path": "C:\\Windows\\notepad.exe"}, confidence=0.7)
    updated = await world_model.upsert("app_path", "notepad", {"exe_path": "C:\\Windows\\System32\\notepad.exe"}, confidence=0.9)
    assert updated.confidence == 0.9
    assert updated.hit_count == 1
    assert updated.value["exe_path"] == "C:\\Windows\\System32\\notepad.exe"


@pytest.mark.asyncio
async def test_world_model_query(world_model):
    await world_model.upsert("share", "finance", {"remote_path": "\\\\server\\finance"}, confidence=0.9)
    await world_model.upsert("share", "hr_data", {"remote_path": "\\\\server\\hr"}, confidence=0.7)
    await world_model.upsert("share", "dev_tools", {"remote_path": "\\\\server\\dev"}, confidence=0.5)

    results = await world_model.query("share")
    assert len(results) == 3

    results = await world_model.query("share", min_confidence=0.6)
    assert len(results) >= 2

    results = await world_model.query("share", query="finance")
    assert len(results) == 1
    assert results[0].key == "finance"


@pytest.mark.asyncio
async def test_world_model_query_with_tags(world_model):
    await world_model.upsert("share", "tagged", {"path": "X"}, tags=["important", "network"])
    await world_model.upsert("share", "untagged", {"path": "Y"})
    results = await world_model.query("share", tags=["important"])
    assert len(results) == 1
    assert results[0].key == "tagged"


@pytest.mark.asyncio
async def test_world_model_delete(world_model):
    await world_model.upsert("share", "to_delete", {"path": "X"})
    deleted = await world_model.delete("share", "to_delete")
    assert deleted is True
    result = await world_model.get("share", "to_delete")
    assert result is None

    deleted_again = await world_model.delete("share", "nonexistent")
    assert deleted_again is False


@pytest.mark.asyncio
async def test_world_model_touch_and_boost(world_model):
    await world_model.upsert("app_path", "calc", {"exe_path": "calc.exe"}, confidence=0.7)
    touched = await world_model.touch("app_path", "calc", boost_confidence=0.1)
    assert touched is not None
    assert touched.confidence >= 0.79
    assert touched.hit_count >= 1


@pytest.mark.asyncio
async def test_world_model_expired_entity_pruned_on_get(world_model):
    from world_model import WorldEntity
    past = (datetime.utcnow() - timedelta(seconds=1)).isoformat()
    storage_key = world_model._storage_key("share", "old_share")
    entity_data = WorldEntity(
        entity_type="share", key="old_share", value={"path": "X"},
        expires_at=past,
    ).to_dict()
    await world_model.persistence.save_kv(storage_key, entity_data)

    result = await world_model.get("share", "old_share")
    assert result is None


@pytest.mark.asyncio
async def test_world_model_prune_expired(world_model):
    from world_model import WorldEntity
    past = (datetime.utcnow() - timedelta(seconds=1)).isoformat()
    storage_key = world_model._storage_key("share", "expired1")
    await world_model.persistence.save_kv(storage_key, WorldEntity(
        entity_type="share", key="expired1", value={}, expires_at=past,
    ).to_dict())
    storage_key2 = world_model._storage_key("share", "valid1")
    await world_model.persistence.save_kv(storage_key2, WorldEntity(
        entity_type="share", key="valid1", value={},
    ).to_dict())

    pruned = await world_model.prune_expired("share")
    assert pruned >= 1


@pytest.mark.asyncio
async def test_world_model_best_candidate(world_model):
    await world_model.upsert("app_path", "chrome", {"exe_path": "chrome.exe"}, confidence=0.9)
    await world_model.upsert("app_path", "firefox", {"exe_path": "firefox.exe"}, confidence=0.6)

    best = await world_model.best_candidate("app_path", query="chrome")
    assert best is not None
    assert best.key == "chrome"


# --- Domain-specific convenience methods ---

@pytest.mark.asyncio
async def test_remember_and_find_share(world_model):
    await world_model.remember_share("sales_data", "\\\\fileserver\\sales", local_path="S:")
    found = await world_model.find_share("sales")
    assert found is not None
    assert found.value["remote_path"] == "\\\\fileserver\\sales"


@pytest.mark.asyncio
async def test_remember_and_find_app_path(world_model):
    await world_model.remember_app_path("VS Code", "C:\\Program Files\\VSCode\\code.exe")
    found = await world_model.find_app_path("vs code")
    assert found is not None
    assert "code.exe" in found.value["exe_path"]


@pytest.mark.asyncio
async def test_remember_and_find_document_alias(world_model):
    await world_model.remember_document_alias("relatorio mensal", "C:\\Docs\\relatorio_2024_05.xlsx")
    found = await world_model.find_document_alias("relatorio mensal")
    assert found is not None
    assert "relatorio_2024_05" in found.value["real_path"]


@pytest.mark.asyncio
async def test_remember_and_find_selector(world_model):
    selector_data = {"auto_id": "btn_save", "control_type": "Button", "title": "Save"}
    await world_model.remember_selector("notepad:btn_save", selector_data, app_context="notepad")
    found = await world_model.find_selector("btn_save", app_context="notepad")
    assert found is not None
    assert found.value["auto_id"] == "btn_save"


@pytest.mark.asyncio
async def test_remember_and_find_path_candidate(world_model):
    await world_model.remember_path_candidate("downloads folder", "C:\\Users\\User\\Downloads")
    found = await world_model.find_path_candidate("downloads")
    assert found is not None
    assert "Downloads" in found.value["path"]


@pytest.mark.asyncio
async def test_list_entity_types(world_model):
    types = await world_model.list_entity_types()
    assert len(types) > 0
    type_names = [t["entity_type"] for t in types]
    assert "share" in type_names
    assert "app_path" in type_names


# --- StrategyRecord unit tests ---

def test_strategy_record_creation():
    from strategy_memory import StrategyRecord
    record = StrategyRecord(
        strategy_id="strat_001",
        goal_type="open_document",
        goal_summary="Open a Word document from a network share",
        steps=[{"tool": "share_discovery", "action": "list_mappings"}, {"tool": "office", "action": "open_document"}],
        outcome="success",
    )
    assert record.strategy_id == "strat_001"
    assert record.goal_type == "open_document"
    assert record.used_count == 1
    assert len(record.steps) == 2


def test_strategy_record_to_dict_and_back():
    from strategy_memory import StrategyRecord
    record = StrategyRecord(
        strategy_id="strat_002", goal_type="install_software",
        goal_summary="Install Chrome", steps=[{"tool": "package_manager", "action": "install"}],
        outcome="success", confidence=0.9, context_tags=["browser"],
    )
    d = record.to_dict()
    assert d["strategy_id"] == "strat_002"
    assert d["confidence"] == 0.9
    restored = StrategyRecord.from_dict(d)
    assert restored.strategy_id == record.strategy_id
    assert restored.confidence == record.confidence


# --- StrategyMemory async tests ---

@pytest.mark.asyncio
async def test_strategy_record_success(strategy_memory):
    record = await strategy_memory.record_success(
        strategy_id="strat_open_doc",
        goal_type="open_document",
        goal_summary="Open a Word document",
        steps=[{"tool": "share_discovery", "action": "list_mappings"}, {"tool": "office", "action": "open_document"}],
        context_tags=["office", "word"],
        duration_ms=5000,
    )
    assert record.strategy_id == "strat_open_doc"
    assert record.outcome == "success"
    assert record.used_count == 1


@pytest.mark.asyncio
async def test_strategy_record_success_updates_existing(strategy_memory):
    await strategy_memory.record_success(
        strategy_id="strat_reuse", goal_type="find_file",
        goal_summary="Find file on share", steps=[{"tool": "shell"}],
    )
    updated = await strategy_memory.record_success(
        strategy_id="strat_reuse", goal_type="find_file",
        goal_summary="Find file on share", steps=[{"tool": "shell"}],
        duration_ms=3000,
    )
    assert updated.used_count == 2
    assert updated.confidence > 0.85


@pytest.mark.asyncio
async def test_strategy_record_failure(strategy_memory):
    record = await strategy_memory.record_failure(
        goal_type="install_software",
        approach_summary="Tried winget install",
        steps=[{"tool": "package_manager", "action": "install"}],
        error="package not found",
        context_tags=["winget"],
    )
    assert record["goal_type"] == "install_software"
    assert "approach_hash" in record


@pytest.mark.asyncio
async def test_strategy_find(strategy_memory):
    await strategy_memory.record_success(
        strategy_id="strat_a", goal_type="open_document",
        goal_summary="Open Word doc", steps=[{"tool": "office"}],
        confidence=0.9, context_tags=["word"],
    )
    await strategy_memory.record_success(
        strategy_id="strat_b", goal_type="open_document",
        goal_summary="Open Excel sheet", steps=[{"tool": "office"}],
        confidence=0.7, context_tags=["excel"],
    )
    results = await strategy_memory.find_strategies("open_document")
    assert len(results) == 2
    assert results[0].confidence >= results[1].confidence


@pytest.mark.asyncio
async def test_strategy_find_with_query(strategy_memory):
    await strategy_memory.record_success(
        strategy_id="strat_word", goal_type="open_document",
        goal_summary="Open Word document from share",
        steps=[{"tool": "share_discovery"}, {"tool": "office"}],
    )
    results = await strategy_memory.find_strategies("open_document", query="word")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_strategy_best(strategy_memory):
    await strategy_memory.record_success(
        strategy_id="strat_best_1", goal_type="find_file",
        goal_summary="Find file", steps=[{"tool": "filesystem"}],
        confidence=0.95,
    )
    await strategy_memory.record_success(
        strategy_id="strat_best_2", goal_type="find_file",
        goal_summary="Find file", steps=[{"tool": "shell"}],
        confidence=0.6,
    )
    best = await strategy_memory.best_strategy("find_file")
    assert best is not None
    assert best.strategy_id == "strat_best_1"


@pytest.mark.asyncio
async def test_strategy_mark_reused(strategy_memory):
    await strategy_memory.record_success(
        strategy_id="strat_reuse_test", goal_type="open_app",
        goal_summary="Open app", steps=[{"tool": "desktop"}],
    )
    reused = await strategy_memory.mark_reused("open_app", "strat_reuse_test")
    assert reused is not None
    assert reused.used_count == 2


@pytest.mark.asyncio
async def test_strategy_find_failed_approaches(strategy_memory):
    await strategy_memory.record_failure(
        goal_type="install_driver",
        approach_summary="Tried manual inf install",
        steps=[{"tool": "driver_manager", "action": "install_inf"}],
        error="driver not compatible",
    )
    failed = await strategy_memory.find_failed_approaches("install_driver")
    assert len(failed) >= 1


@pytest.mark.asyncio
async def test_strategy_list_goal_types(strategy_memory):
    await strategy_memory.record_success(
        strategy_id="s1", goal_type="open_doc", goal_summary="Open doc", steps=[],
    )
    await strategy_memory.record_success(
        strategy_id="s2", goal_type="find_file", goal_summary="Find file", steps=[],
    )
    types = await strategy_memory.list_goal_types()
    goal_names = [t["goal_type"] for t in types]
    assert "open_doc" in goal_names
    assert "find_file" in goal_names


# --- MemoryTool integration tests ---

@pytest.fixture
def memory_tool(persistence):
    from tool_memory import MemoryTool
    tool = MemoryTool()
    tool.persistence = persistence
    from world_model import WorldModel
    from strategy_memory import StrategyMemory
    tool.world_model = WorldModel(persistence=persistence)
    tool.strategy_memory = StrategyMemory(persistence=persistence)
    return tool


def _d(result):
    """Convert Pydantic MemoryOutput to dict for easy assertions."""
    return result.dict() if hasattr(result, "dict") else result


@pytest.mark.asyncio
async def test_memory_tool_world_upsert_and_get(memory_tool):
    result = _d(await memory_tool.run({
        "action": "world_upsert",
        "entity_type": "share",
        "key": "finance_share",
        "value": {"remote_path": "\\\\server\\finance"},
        "confidence": 0.9,
    }))
    assert result["success"] is True
    assert result["message"] == "world_entity_upserted"

    result = _d(await memory_tool.run({
        "action": "world_get",
        "entity_type": "share",
        "key": "finance_share",
    }))
    assert result["success"] is True
    assert result["value"]["key"] == "finance_share"


@pytest.mark.asyncio
async def test_memory_tool_world_query(memory_tool):
    await memory_tool.run({
        "action": "world_upsert", "entity_type": "app_path", "key": "notepad",
        "value": {"exe_path": "notepad.exe"}, "confidence": 0.9,
    })
    result = _d(await memory_tool.run({
        "action": "world_query", "entity_type": "app_path",
    }))
    assert result["success"] is True
    assert len(result["items"]) >= 1


@pytest.mark.asyncio
async def test_memory_tool_world_delete(memory_tool):
    await memory_tool.run({
        "action": "world_upsert", "entity_type": "share", "key": "del_me",
        "value": {"path": "x"},
    })
    result = _d(await memory_tool.run({
        "action": "world_delete", "entity_type": "share", "key": "del_me",
    }))
    assert result["success"] is True


@pytest.mark.asyncio
async def test_memory_tool_world_touch(memory_tool):
    await memory_tool.run({
        "action": "world_upsert", "entity_type": "share", "key": "touch_me",
        "value": {"path": "x"}, "confidence": 0.7,
    })
    result = _d(await memory_tool.run({
        "action": "world_touch", "entity_type": "share", "key": "touch_me",
        "boost_confidence": 0.1,
    }))
    assert result["success"] is True
    assert result["value"]["confidence"] >= 0.79


@pytest.mark.asyncio
async def test_memory_tool_world_prune(memory_tool):
    result = _d(await memory_tool.run({"action": "world_prune", "entity_type": "share"}))
    assert result["success"] is True


@pytest.mark.asyncio
async def test_memory_tool_world_types(memory_tool):
    result = _d(await memory_tool.run({"action": "world_types"}))
    assert result["success"] is True
    assert len(result["items"]) > 0


@pytest.mark.asyncio
async def test_memory_tool_remember_share(memory_tool):
    result = _d(await memory_tool.run({
        "action": "world_remember_share", "key": "data_share",
        "value": {"remote_path": "\\\\server\\data", "local_path": "D:"},
    }))
    assert result["success"] is True
    assert result["message"] == "share_remembered"


@pytest.mark.asyncio
async def test_memory_tool_find_share(memory_tool):
    await memory_tool.run({
        "action": "world_remember_share", "key": "hr_share",
        "value": {"remote_path": "\\\\server\\hr"},
    })
    result = _d(await memory_tool.run({"action": "world_find_share", "query": "hr"}))
    assert result["success"] is True
    assert result["value"] is not None


@pytest.mark.asyncio
async def test_memory_tool_remember_app(memory_tool):
    result = _d(await memory_tool.run({
        "action": "world_remember_app", "key": "Calculator",
        "value": {"exe_path": "C:\\Windows\\System32\\calc.exe"},
    }))
    assert result["success"] is True


@pytest.mark.asyncio
async def test_memory_tool_find_app(memory_tool):
    await memory_tool.run({
        "action": "world_remember_app", "key": "Paint",
        "value": {"exe_path": "mspaint.exe"},
    })
    result = _d(await memory_tool.run({"action": "world_find_app", "query": "paint"}))
    assert result["success"] is True
    assert result["value"] is not None


@pytest.mark.asyncio
async def test_memory_tool_remember_alias(memory_tool):
    result = _d(await memory_tool.run({
        "action": "world_remember_alias", "key": "monthly report",
        "value": {"real_path": "C:\\Reports\\monthly_2024.xlsx"},
    }))
    assert result["success"] is True


@pytest.mark.asyncio
async def test_memory_tool_find_alias(memory_tool):
    await memory_tool.run({
        "action": "world_remember_alias", "key": "budget spreadsheet",
        "value": {"real_path": "C:\\Finance\\budget.xlsx"},
    })
    result = _d(await memory_tool.run({"action": "world_find_alias", "query": "budget"}))
    assert result["success"] is True
    assert result["value"] is not None


@pytest.mark.asyncio
async def test_memory_tool_remember_selector(memory_tool):
    result = _d(await memory_tool.run({
        "action": "world_remember_selector", "key": "excel:save_btn",
        "value": {"auto_id": "FileSave", "control_type": "Button"},
        "app_context": "EXCEL.EXE",
    }))
    assert result["success"] is True


@pytest.mark.asyncio
async def test_memory_tool_find_selector(memory_tool):
    await memory_tool.run({
        "action": "world_remember_selector", "key": "word:close_btn",
        "value": {"auto_id": "Close", "control_type": "Button"},
        "app_context": "WINWORD.EXE",
    })
    result = _d(await memory_tool.run({
        "action": "world_find_selector", "query": "close_btn",
        "app_context": "WINWORD.EXE",
    }))
    assert result["success"] is True


@pytest.mark.asyncio
async def test_memory_tool_remember_path(memory_tool):
    result = _d(await memory_tool.run({
        "action": "world_remember_path", "key": "user desktop",
        "value": {"path": "C:\\Users\\User\\Desktop"},
    }))
    assert result["success"] is True


@pytest.mark.asyncio
async def test_memory_tool_find_path(memory_tool):
    await memory_tool.run({
        "action": "world_remember_path", "key": "temp folder",
        "value": {"path": "C:\\Temp"},
    })
    result = _d(await memory_tool.run({"action": "world_find_path", "query": "temp"}))
    assert result["success"] is True


@pytest.mark.asyncio
async def test_memory_tool_strategy_record_success(memory_tool):
    result = _d(await memory_tool.run({
        "action": "strategy_record_success",
        "strategy_id": "open_word_from_share",
        "goal_type": "open_document",
        "goal_summary": "Open Word doc from network share",
        "steps": [
            {"tool": "share_discovery", "action": "list_mappings"},
            {"tool": "office", "action": "open_document"},
        ],
        "confidence": 0.9,
        "tags": ["office", "word"],
        "duration_ms": 4500,
    }))
    assert result["success"] is True
    assert result["message"] == "strategy_recorded"


@pytest.mark.asyncio
async def test_memory_tool_strategy_find(memory_tool):
    await memory_tool.run({
        "action": "strategy_record_success",
        "strategy_id": "install_chrome", "goal_type": "install_software",
        "goal_summary": "Install Chrome browser",
        "steps": [{"tool": "package_manager", "action": "install"}],
    })
    result = _d(await memory_tool.run({
        "action": "strategy_find", "goal_type": "install_software",
    }))
    assert result["success"] is True
    assert len(result["items"]) >= 1


@pytest.mark.asyncio
async def test_memory_tool_strategy_best(memory_tool):
    await memory_tool.run({
        "action": "strategy_record_success",
        "strategy_id": "best_strategy_test", "goal_type": "find_file",
        "goal_summary": "Find file", "steps": [{"tool": "filesystem"}],
        "confidence": 0.95,
    })
    result = _d(await memory_tool.run({"action": "strategy_best", "goal_type": "find_file"}))
    assert result["success"] is True
    assert result["value"] is not None


@pytest.mark.asyncio
async def test_memory_tool_strategy_record_failure(memory_tool):
    result = _d(await memory_tool.run({
        "action": "strategy_record_failure",
        "goal_type": "install_driver",
        "goal_summary": "Tried inf install approach",
        "steps": [{"tool": "driver_manager", "action": "install_inf"}],
        "error": "driver not compatible",
    }))
    assert result["success"] is True
    assert result["message"] == "failure_recorded"


@pytest.mark.asyncio
async def test_memory_tool_strategy_reused(memory_tool):
    await memory_tool.run({
        "action": "strategy_record_success",
        "strategy_id": "reusable_strat", "goal_type": "open_app",
        "goal_summary": "Open app", "steps": [{"tool": "desktop"}],
    })
    result = _d(await memory_tool.run({
        "action": "strategy_reused",
        "goal_type": "open_app", "strategy_id": "reusable_strat",
    }))
    assert result["success"] is True
    assert result["value"]["used_count"] >= 2


@pytest.mark.asyncio
async def test_memory_tool_strategy_goal_types(memory_tool):
    await memory_tool.run({
        "action": "strategy_record_success",
        "strategy_id": "gt_test", "goal_type": "my_goal_type",
        "goal_summary": "Test", "steps": [],
    })
    result = _d(await memory_tool.run({"action": "strategy_goal_types"}))
    assert result["success"] is True
    assert len(result["items"]) >= 1


# --- Canonical tools integration ---

def test_canonical_tools_memory_actions():
    from canonical_tools import ACTION_METADATA
    memory_actions = ACTION_METADATA["memory"]
    assert "world_upsert" in memory_actions
    assert "world_get" in memory_actions
    assert "world_query" in memory_actions
    assert "world_delete" in memory_actions
    assert "world_touch" in memory_actions
    assert "world_prune" in memory_actions
    assert "world_types" in memory_actions
    assert "world_remember_share" in memory_actions
    assert "world_remember_app" in memory_actions
    assert "world_remember_alias" in memory_actions
    assert "world_remember_selector" in memory_actions
    assert "world_remember_path" in memory_actions
    assert "world_find_share" in memory_actions
    assert "world_find_app" in memory_actions
    assert "world_find_alias" in memory_actions
    assert "world_find_selector" in memory_actions
    assert "world_find_path" in memory_actions
    assert "strategy_record_success" in memory_actions
    assert "strategy_record_failure" in memory_actions
    assert "strategy_find" in memory_actions
    assert "strategy_best" in memory_actions
    assert "strategy_reused" in memory_actions
    assert "strategy_goal_types" in memory_actions


def test_canonical_tools_memory_schema():
    from canonical_tools import tool_definitions
    definitions = tool_definitions()
    memory_def = next(d for d in definitions if d["function"]["name"] == "memory")
    actions = memory_def["function"]["parameters"]["properties"]["action"]["enum"]
    assert "world_upsert" in actions
    assert "world_find_share" in actions
    assert "strategy_record_success" in actions
    assert "strategy_best" in actions
    assert "confidence" in memory_def["function"]["parameters"]["properties"]
    assert "goal_type" in memory_def["function"]["parameters"]["properties"]
    assert "strategy_id" in memory_def["function"]["parameters"]["properties"]


def test_canonical_tools_normalize_memory():
    from canonical_tools import normalize_agentic_task
    task = normalize_agentic_task("memory", {
        "action": "world_upsert",
        "entity_type": "share",
        "key": "test",
        "value": {"path": "X"},
        "confidence": 0.9,
        "source": "test",
    }, "task-001")
    assert task["tool"] == "memory"
    assert task["action"] == "world_upsert"
    assert task["params"]["confidence"] == 0.9
    assert task["params"]["source"] == "test"


# --- Planner integration ---

@pytest.mark.asyncio
async def test_planner_world_model_keywords():
    from planner_agent import PlannerAgent
    planner = PlannerAgent()

    plan, v = await planner.parse("find known share finance_data")
    assert len(plan.tasks) >= 1
    assert any(t.action == "world_find_share" for t in plan.tasks)

    plan, v = await planner.parse("remember app path notepad")
    assert len(plan.tasks) >= 1
    assert any(t.action == "world_remember_app" for t in plan.tasks)


@pytest.mark.asyncio
async def test_planner_strategy_keywords():
    from planner_agent import PlannerAgent
    planner = PlannerAgent()

    plan, v = await planner.parse("find strategy for open_document")
    assert len(plan.tasks) >= 1
    assert any(t.action == "strategy_find" for t in plan.tasks)

    plan, v = await planner.parse("list goal types")
    assert len(plan.tasks) >= 1
    assert any(t.action == "strategy_goal_types" for t in plan.tasks)


# --- Validation tests ---

@pytest.mark.asyncio
async def test_planner_validation_world_model():
    from planner_agent import PlannerAgent
    from planner_models import Task, Plan
    planner = PlannerAgent()

    plan = Plan(original_text="test", tasks=[
        Task(id="t1", tool="memory", action="world_upsert", params={}, priority=50),
    ])
    v = planner.validate_plan(plan)
    assert not v.ok
    assert any("entity_type and key" in e for e in v.errors)


@pytest.mark.asyncio
async def test_planner_validation_strategy():
    from planner_agent import PlannerAgent
    from planner_models import Task, Plan
    planner = PlannerAgent()

    plan = Plan(original_text="test", tasks=[
        Task(id="t1", tool="memory", action="strategy_record_success", params={}, priority=50),
    ])
    v = planner.validate_plan(plan)
    assert not v.ok
    assert any("strategy_id and goal_type" in e for e in v.errors)


# --- Legacy memory actions still work ---

@pytest.mark.asyncio
async def test_memory_tool_legacy_set_get(memory_tool):
    await memory_tool.run({"action": "set", "key": "test_key", "value": {"data": "hello"}})
    result = _d(await memory_tool.run({"action": "get", "key": "test_key"}))
    assert result["success"] is True
    assert result["value"]["data"] == "hello"


@pytest.mark.asyncio
async def test_memory_tool_legacy_upsert_entity(memory_tool):
    result = _d(await memory_tool.run({
        "action": "upsert_entity", "entity_type": "device", "key": "printer_hp",
        "attributes": {"model": "HP LaserJet"},
    }))
    assert result["success"] is True
    assert result["message"] == "entity_upserted"
