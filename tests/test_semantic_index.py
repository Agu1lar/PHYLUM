"""Comprehensive test suite for Semantic Index (Vector DB integration).

Tests:
  - Local embedding: _text_to_vector, _cosine_similarity, _tokenize
  - SemanticIndex: upsert/search strategies, upsert/search entities, delete, count, reset
  - Semantic similarity: related queries find related results
  - WorldModel.semantic_search integration
  - StrategyMemory.semantic_search integration
  - tool_memory.py semantic_search_strategies / semantic_search_entities actions
  - Fallback when SemanticIndex is not available
"""
from __future__ import annotations

import asyncio
import os
import sys
import shutil
import pytest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from semantic_index import (
    SemanticIndex,
    _cosine_similarity,
    _text_to_vector,
    _tokenize,
    VECTOR_DIM,
)
from world_model import WorldModel
from strategy_memory import StrategyMemory
from agent_persistence import Persistence

TEST_DB_PATH = Path(__file__).resolve().parent / "_test_semantic_db"


def _cleanup():
    if TEST_DB_PATH.exists():
        shutil.rmtree(TEST_DB_PATH, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_db():
    _cleanup()
    yield
    _cleanup()


# ─── Tokenization ──────────────────────────────────────────────────


def test_tokenize_basic():
    tokens = _tokenize("Open a Word document")
    assert "open" in tokens
    assert "word" in tokens
    assert "document" in tokens


def test_tokenize_special_chars():
    tokens = _tokenize("file_system.read_file (path='/tmp/data.csv')")
    assert "file_system" in tokens
    assert "read_file" in tokens
    assert "path" in tokens
    assert "tmp" in tokens
    assert "data" in tokens
    assert "csv" in tokens


def test_tokenize_empty():
    assert _tokenize("") == []
    assert _tokenize("!!!") == []


# ─── Embedding vectors ─────────────────────────────────────────────


def test_vector_dimension():
    vec = _text_to_vector("hello world")
    assert len(vec) == VECTOR_DIM


def test_vector_normalized():
    vec = _text_to_vector("test string")
    norm = sum(v * v for v in vec) ** 0.5
    assert abs(norm - 1.0) < 0.01


def test_vector_empty_text():
    vec = _text_to_vector("")
    assert len(vec) == VECTOR_DIM
    assert all(v == 0.0 for v in vec)


def test_vector_deterministic():
    v1 = _text_to_vector("open word document")
    v2 = _text_to_vector("open word document")
    assert v1 == v2


def test_similar_texts_have_high_similarity():
    v1 = _text_to_vector("open a word document")
    v2 = _text_to_vector("open word doc file")
    sim = _cosine_similarity(v1, v2)
    assert sim >= 0.4


def test_different_texts_have_lower_similarity():
    v1 = _text_to_vector("open a word document")
    v2 = _text_to_vector("install printer driver update")
    sim = _cosine_similarity(v1, v2)
    assert sim < 0.5


def test_identical_texts_have_similarity_one():
    v1 = _text_to_vector("configure network share")
    sim = _cosine_similarity(v1, v1)
    assert abs(sim - 1.0) < 0.01


def test_cosine_zero_vector():
    zero = [0.0] * VECTOR_DIM
    v1 = _text_to_vector("hello")
    assert _cosine_similarity(zero, v1) == 0.0


# ─── SemanticIndex strategies ──────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_and_search_strategy():
    idx = SemanticIndex(db_path=TEST_DB_PATH)
    ok = await idx.upsert_strategy(
        "open_document", "s1",
        "Open a Word document using Office COM",
        {"steps": [{"tool": "office", "action": "open_document"}], "confidence": 0.9, "used_count": 3},
    )
    assert ok is True

    results = await idx.search_strategies("how to open a word file")
    assert len(results) >= 1
    assert results[0]["strategy_id"] == "s1"
    assert results[0]["semantic_score"] > 0


@pytest.mark.asyncio
async def test_search_strategy_by_goal_type():
    idx = SemanticIndex(db_path=TEST_DB_PATH)
    await idx.upsert_strategy("open_doc", "s1", "Open Word doc", {"steps": [], "confidence": 0.9})
    await idx.upsert_strategy("install_sw", "s2", "Install software", {"steps": [], "confidence": 0.8})

    results = await idx.search_strategies("open a file", goal_type="open_doc")
    ids = [r["strategy_id"] for r in results]
    assert "s1" in ids


@pytest.mark.asyncio
async def test_upsert_strategy_updates():
    idx = SemanticIndex(db_path=TEST_DB_PATH)
    await idx.upsert_strategy("open_doc", "s1", "Open Word doc v1", {"steps": [], "confidence": 0.7})
    await idx.upsert_strategy("open_doc", "s1", "Open Word doc v2 improved", {"steps": [], "confidence": 0.95})

    results = await idx.search_strategies("open word doc")
    assert len(results) >= 1
    assert results[0]["confidence"] == 0.95


@pytest.mark.asyncio
async def test_semantic_strategy_ranking():
    idx = SemanticIndex(db_path=TEST_DB_PATH)
    await idx.upsert_strategy("print", "s1", "Configure and install a network printer", {"steps": [{"tool": "driver_manager"}], "confidence": 0.9})
    await idx.upsert_strategy("open_doc", "s2", "Open a PDF document in reader", {"steps": [{"tool": "desktop"}], "confidence": 0.9})

    results = await idx.search_strategies("setup printing device on network")
    assert len(results) >= 1
    assert results[0]["strategy_id"] == "s1"


@pytest.mark.asyncio
async def test_delete_strategy():
    idx = SemanticIndex(db_path=TEST_DB_PATH)
    await idx.upsert_strategy("test", "s1", "Test strategy", {"steps": []})
    assert await idx.count("strategies") >= 1
    ok = await idx.delete_strategy("test", "s1")
    assert ok is True


# ─── SemanticIndex entities ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_and_search_entity():
    idx = SemanticIndex(db_path=TEST_DB_PATH)
    await idx.upsert_entity("share", "finance_share", {"remote_path": "\\\\server\\finance"}, confidence=0.9)

    results = await idx.search_entities("finance network drive")
    assert len(results) >= 1
    assert results[0]["key"] == "finance_share"


@pytest.mark.asyncio
async def test_search_entity_by_type():
    idx = SemanticIndex(db_path=TEST_DB_PATH)
    await idx.upsert_entity("share", "fin_share", {"remote_path": "\\\\srv\\fin"})
    await idx.upsert_entity("app_path", "word_app", {"exe_path": "C:\\Program Files\\word.exe"})

    results = await idx.search_entities("word application", entity_type="app_path")
    keys = [r["key"] for r in results]
    assert "word_app" in keys


@pytest.mark.asyncio
async def test_search_entity_by_app_context():
    idx = SemanticIndex(db_path=TEST_DB_PATH)
    await idx.upsert_entity("selector", "save_btn", {"title": "Save"}, app_context="notepad.exe")
    await idx.upsert_entity("selector", "save_btn_word", {"title": "Save"}, app_context="winword.exe")

    results = await idx.search_entities("save button", app_context="notepad.exe")
    keys = [r["key"] for r in results]
    assert "save_btn" in keys


@pytest.mark.asyncio
async def test_delete_entity():
    idx = SemanticIndex(db_path=TEST_DB_PATH)
    await idx.upsert_entity("share", "temp", {"path": "/tmp"})
    ok = await idx.delete_entity("share", "temp")
    assert ok is True


@pytest.mark.asyncio
async def test_count():
    idx = SemanticIndex(db_path=TEST_DB_PATH)
    await idx.upsert_entity("share", "a", {"p": "1"})
    await idx.upsert_entity("share", "b", {"p": "2"})
    c = await idx.count("entities")
    assert c >= 2


@pytest.mark.asyncio
async def test_reset():
    idx = SemanticIndex(db_path=TEST_DB_PATH)
    await idx.upsert_entity("share", "x", {"p": "v"})
    idx.reset()
    c = await idx.count("entities")
    assert c == 0


# ─── WorldModel integration ────────────────────────────────────────


@pytest.mark.asyncio
async def test_world_model_semantic_search():
    persistence = Persistence.get()
    idx = SemanticIndex(db_path=TEST_DB_PATH)
    wm = WorldModel(persistence, semantic_index=idx)

    await wm.remember_share("finance_reports", "\\\\server\\finance\\reports")
    await wm.remember_share("hr_documents", "\\\\server\\hr\\docs")
    await wm.remember_app_path("Microsoft Word", "C:\\Program Files\\Microsoft Office\\word.exe")

    results = await wm.semantic_search("financial reports network drive")
    assert len(results) >= 1
    assert any("finance" in str(r.get("key", "")).lower() for r in results)


@pytest.mark.asyncio
async def test_world_model_semantic_search_no_index():
    persistence = Persistence.get()
    wm = WorldModel(persistence)

    await wm.remember_share("test_share", "\\\\server\\test")
    results = await wm.semantic_search("test", entity_type="share")
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_world_model_upsert_indexes_automatically():
    persistence = Persistence.get()
    idx = SemanticIndex(db_path=TEST_DB_PATH)
    wm = WorldModel(persistence, semantic_index=idx)

    await wm.upsert("device", "hp_printer", {"model": "HP LaserJet Pro"}, confidence=0.9, tags=["printer"])

    results = await idx.search_entities("laser printer hp")
    assert len(results) >= 1
    assert results[0]["key"] == "hp_printer"


# ─── StrategyMemory integration ────────────────────────────────────


@pytest.mark.asyncio
async def test_strategy_memory_semantic_search():
    persistence = Persistence.get()
    idx = SemanticIndex(db_path=TEST_DB_PATH)
    sm = StrategyMemory(persistence, semantic_index=idx)

    await sm.record_success(
        strategy_id="s_print",
        goal_type="install_printer",
        goal_summary="Install a network printer using driver_manager",
        steps=[{"tool": "driver_manager", "action": "install_driver"}],
        confidence=0.9,
        context_tags=["printer", "network"],
    )

    results = await sm.semantic_search("install network printer driver")
    assert len(results) >= 1
    assert results[0]["strategy_id"] == "s_print"
    assert results[0]["semantic_score"] > 0


@pytest.mark.asyncio
async def test_strategy_memory_semantic_search_no_index():
    persistence = Persistence.get()
    sm = StrategyMemory(persistence)

    await sm.record_success(
        strategy_id="s1",
        goal_type="test_goal",
        goal_summary="Test strategy",
        steps=[],
    )
    results = await sm.semantic_search("test", goal_type="test_goal")
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_strategy_memory_record_indexes_automatically():
    persistence = Persistence.get()
    idx = SemanticIndex(db_path=TEST_DB_PATH)
    sm = StrategyMemory(persistence, semantic_index=idx)

    await sm.record_success(
        strategy_id="s_doc",
        goal_type="open_document",
        goal_summary="Open Word document via COM",
        steps=[{"tool": "office", "action": "open_document", "summary": "Use COM to open"}],
    )

    results = await idx.search_strategies("open a microsoft word file")
    assert len(results) >= 1
    assert results[0]["strategy_id"] == "s_doc"


# ─── tool_memory semantic actions ──────────────────────────────────


@pytest.mark.asyncio
async def test_tool_memory_semantic_search_strategies():
    from tool_memory import MemoryTool
    persistence = Persistence.get()
    idx = SemanticIndex(db_path=TEST_DB_PATH)
    wm = WorldModel(persistence, semantic_index=idx)
    sm = StrategyMemory(persistence, semantic_index=idx)
    tool = MemoryTool()
    tool.world_model = wm
    tool.strategy_memory = sm

    await sm.record_success(
        strategy_id="s_excel",
        goal_type="read_excel",
        goal_summary="Read Excel spreadsheet using openpyxl sandbox script",
        steps=[{"tool": "sandbox", "action": "execute_python"}],
        confidence=0.9,
    )

    result = await tool.run({"action": "semantic_search_strategies", "query": "how to read an xlsx spreadsheet"})
    d = result.dict() if hasattr(result, 'dict') else result
    assert d["success"] is True
    assert len(d.get("items") or []) >= 1


@pytest.mark.asyncio
async def test_tool_memory_semantic_search_entities():
    from tool_memory import MemoryTool
    persistence = Persistence.get()
    idx = SemanticIndex(db_path=TEST_DB_PATH)
    wm = WorldModel(persistence, semantic_index=idx)
    sm = StrategyMemory(persistence, semantic_index=idx)
    tool = MemoryTool()
    tool.world_model = wm
    tool.strategy_memory = sm

    await wm.remember_share("accounting_drive", "\\\\fileserver\\accounting")

    result = await tool.run({"action": "semantic_search_entities", "query": "accounting financial shared folder"})
    d = result.dict() if hasattr(result, 'dict') else result
    assert d["success"] is True
    assert len(d.get("items") or []) >= 1


# ─── Cross-domain semantic search ──────────────────────────────────


@pytest.mark.asyncio
async def test_semantic_finds_related_not_substring():
    """The key test: semantic search finds 'configure printing device' when
    searching for 'setup printer' even though there's no substring match."""
    idx = SemanticIndex(db_path=TEST_DB_PATH)

    await idx.upsert_strategy(
        "printer_setup", "s1",
        "Configure a printing device on the corporate network",
        {"steps": [{"tool": "driver_manager", "action": "install_driver"}], "confidence": 0.9, "context_tags": ["printer", "network", "driver"]},
    )
    await idx.upsert_strategy(
        "open_doc", "s2",
        "Open a spreadsheet in Excel application",
        {"steps": [{"tool": "office", "action": "open_document"}], "confidence": 0.9},
    )

    results = await idx.search_strategies("setup printer")
    assert len(results) >= 1
    assert results[0]["strategy_id"] == "s1"
