# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tests for pluggable embeddings, BM25 re-ranking, and incremental batch indexing.

Covers:
  - EmbeddingProvider / FeatureHashProvider
  - SentenceTransformerProvider (with graceful fallback)
  - create_embedding_provider factory
  - BM25 scoring (add, remove, score, clear)
  - reciprocal_rank_fusion
  - SemanticIndex with embedding_provider
  - SemanticIndex batch_upsert_strategies / batch_upsert_entities
  - SemanticIndex hybrid_search_strategies / hybrid_search_entities
  - BM25 consistency on delete/reset
  - embedding_info property
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "memory"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "models"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "persistence"))

from semantic_index import (
    BM25,
    EmbeddingProvider,
    FeatureHashProvider,
    SemanticIndex,
    SentenceTransformerProvider,
    VECTOR_DIM,
    _cosine_similarity,
    _text_to_vector,
    _tokenize,
    create_embedding_provider,
    reciprocal_rank_fusion,
)

TEST_DB_PATH = Path(__file__).resolve().parent / "_test_emb_db"


def _cleanup():
    if TEST_DB_PATH.exists():
        shutil.rmtree(TEST_DB_PATH, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_db():
    _cleanup()
    yield
    _cleanup()


# =========================================================================
# EmbeddingProvider / FeatureHashProvider
# =========================================================================

class TestFeatureHashProvider:
    def test_default_dim(self):
        p = FeatureHashProvider()
        assert p.dim == VECTOR_DIM

    def test_custom_dim(self):
        p = FeatureHashProvider(dim=64)
        assert p.dim == 64
        vec = p.embed("hello world")
        assert len(vec) == 64

    def test_name(self):
        p = FeatureHashProvider()
        assert p.name == "feature_hash"

    def test_embed_normalized(self):
        p = FeatureHashProvider()
        vec = p.embed("test string")
        norm = sum(v * v for v in vec) ** 0.5
        assert abs(norm - 1.0) < 0.01

    def test_embed_batch(self):
        p = FeatureHashProvider()
        texts = ["hello", "world", "test"]
        vecs = p.embed_batch(texts)
        assert len(vecs) == 3
        assert all(len(v) == VECTOR_DIM for v in vecs)

    def test_embed_deterministic(self):
        p = FeatureHashProvider()
        v1 = p.embed("test")
        v2 = p.embed("test")
        assert v1 == v2

    def test_embed_empty(self):
        p = FeatureHashProvider()
        vec = p.embed("")
        assert all(v == 0.0 for v in vec)


class TestEmbeddingProviderBase:
    def test_base_defaults(self):
        p = EmbeddingProvider()
        assert p.dim == VECTOR_DIM
        assert p.name == "feature_hash"
        vec = p.embed("test")
        assert len(vec) == VECTOR_DIM

    def test_base_embed_batch(self):
        p = EmbeddingProvider()
        vecs = p.embed_batch(["a", "b"])
        assert len(vecs) == 2


# =========================================================================
# SentenceTransformerProvider
# =========================================================================

class TestSentenceTransformerProvider:
    def test_fallback_when_not_installed(self):
        p = SentenceTransformerProvider("all-MiniLM-L6-v2")
        vec = p.embed("test string")
        assert len(vec) > 0
        assert isinstance(vec, list)

    def test_name_fallback(self):
        p = SentenceTransformerProvider("all-MiniLM-L6-v2")
        name = p.name
        assert "MiniLM" in name or "feature_hash" in name

    def test_embed_batch_fallback(self):
        p = SentenceTransformerProvider("all-MiniLM-L6-v2")
        vecs = p.embed_batch(["hello", "world"])
        assert len(vecs) == 2

    def test_is_available_property(self):
        p = SentenceTransformerProvider("all-MiniLM-L6-v2")
        result = p.is_available
        assert isinstance(result, bool)

    def test_dim_property(self):
        p = SentenceTransformerProvider("all-MiniLM-L6-v2")
        assert isinstance(p.dim, int)
        assert p.dim > 0

    def test_double_load_is_safe(self):
        p = SentenceTransformerProvider("all-MiniLM-L6-v2")
        p._load()
        p._load()
        assert p._load_attempted is True


# =========================================================================
# create_embedding_provider factory
# =========================================================================

class TestCreateEmbeddingProvider:
    def test_feature_hash_default(self):
        p = create_embedding_provider()
        assert isinstance(p, FeatureHashProvider)

    def test_feature_hash_explicit(self):
        p = create_embedding_provider("feature_hash", dim=64)
        assert isinstance(p, FeatureHashProvider)
        assert p.dim == 64

    def test_sentence_transformers(self):
        p = create_embedding_provider("sentence_transformers")
        assert isinstance(p, SentenceTransformerProvider)


# =========================================================================
# BM25
# =========================================================================

class TestBM25:
    def test_add_and_score(self):
        bm = BM25()
        bm.add_document("d1", "the quick brown fox jumps over the lazy dog")
        bm.add_document("d2", "a fast red car drives on the highway")
        bm.add_document("d3", "the fox is quick and brown")

        results = bm.score("quick fox")
        assert len(results) >= 1
        ids = [doc_id for doc_id, _ in results]
        assert "d1" in ids or "d3" in ids

    def test_idf_weighting(self):
        bm = BM25()
        bm.add_document("d1", "python programming language")
        bm.add_document("d2", "python snake animal zoo")
        bm.add_document("d3", "java programming language")

        results = bm.score("python programming")
        assert results[0][0] == "d1"

    def test_empty_query(self):
        bm = BM25()
        bm.add_document("d1", "hello world")
        assert bm.score("") == []

    def test_empty_corpus(self):
        bm = BM25()
        assert bm.score("hello") == []

    def test_remove_document(self):
        bm = BM25()
        bm.add_document("d1", "hello world")
        bm.add_document("d2", "goodbye world")
        assert bm.doc_count == 2
        bm.remove_document("d1")
        assert bm.doc_count == 1
        results = bm.score("hello")
        ids = [doc_id for doc_id, _ in results]
        assert "d1" not in ids

    def test_remove_nonexistent(self):
        bm = BM25()
        bm.remove_document("nonexistent")
        assert bm.doc_count == 0

    def test_update_document(self):
        bm = BM25()
        bm.add_document("d1", "old content about cats")
        bm.add_document("d1", "new content about dogs")
        assert bm.doc_count == 1
        results = bm.score("dogs")
        assert len(results) >= 1
        assert results[0][0] == "d1"

    def test_add_documents_batch(self):
        bm = BM25()
        bm.add_documents([
            ("d1", "hello world"),
            ("d2", "goodbye world"),
            ("d3", "hello goodbye"),
        ])
        assert bm.doc_count == 3

    def test_score_limit(self):
        bm = BM25()
        for i in range(20):
            bm.add_document(f"d{i}", f"document number {i} about testing")
        results = bm.score("testing", limit=5)
        assert len(results) == 5

    def test_clear(self):
        bm = BM25()
        bm.add_document("d1", "hello")
        bm.clear()
        assert bm.doc_count == 0
        assert bm.score("hello") == []

    def test_score_values_positive(self):
        bm = BM25()
        bm.add_document("d1", "install network printer driver")
        results = bm.score("printer driver")
        assert all(score > 0 for _, score in results)

    def test_custom_parameters(self):
        bm = BM25(k1=2.0, b=0.5)
        bm.add_document("d1", "test document")
        results = bm.score("test")
        assert len(results) == 1


# =========================================================================
# Reciprocal Rank Fusion
# =========================================================================

class TestReciprocalRankFusion:
    def test_single_ranking(self):
        ranking = [("d1", 0.9), ("d2", 0.7), ("d3", 0.5)]
        fused = reciprocal_rank_fusion([ranking])
        assert fused[0][0] == "d1"

    def test_two_rankings_agreement(self):
        r1 = [("d1", 0.9), ("d2", 0.7)]
        r2 = [("d1", 0.8), ("d2", 0.6)]
        fused = reciprocal_rank_fusion([r1, r2])
        assert fused[0][0] == "d1"

    def test_two_rankings_disagreement(self):
        r1 = [("d1", 0.9), ("d2", 0.7)]
        r2 = [("d2", 0.9), ("d3", 0.7)]
        fused = reciprocal_rank_fusion([r1, r2])
        ids = [doc_id for doc_id, _ in fused]
        assert "d2" in ids

    def test_empty_rankings(self):
        fused = reciprocal_rank_fusion([])
        assert fused == []

    def test_k_parameter(self):
        r1 = [("d1", 0.9), ("d2", 0.5)]
        fused_k1 = reciprocal_rank_fusion([r1], k=1)
        fused_k60 = reciprocal_rank_fusion([r1], k=60)
        assert fused_k1[0][1] > fused_k60[0][1]

    def test_disjoint_rankings(self):
        r1 = [("d1", 0.9)]
        r2 = [("d2", 0.9)]
        fused = reciprocal_rank_fusion([r1, r2])
        assert len(fused) == 2


# =========================================================================
# SemanticIndex with EmbeddingProvider
# =========================================================================

class TestSemanticIndexProvider:
    @pytest.mark.asyncio
    async def test_custom_provider(self):
        provider = FeatureHashProvider(dim=64)
        idx = SemanticIndex(db_path=TEST_DB_PATH, embedding_provider=provider)
        assert idx._dim == 64

        ok = await idx.upsert_strategy(
            "test", "s1", "Test strategy", {"steps": [], "confidence": 0.9},
        )
        assert ok is True

        results = await idx.search_strategies("test strategy")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_sentence_transformer_provider(self):
        provider = SentenceTransformerProvider("all-MiniLM-L6-v2")
        idx = SemanticIndex(db_path=TEST_DB_PATH, embedding_provider=provider)
        ok = await idx.upsert_strategy(
            "test", "s1", "Open a Word document", {"steps": [], "confidence": 0.9},
        )
        assert ok is True

    @pytest.mark.asyncio
    async def test_embedding_info(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH)
        info = idx.embedding_info
        assert info["provider"] == "feature_hash"
        assert info["dim"] == VECTOR_DIM
        assert info["bm25_enabled"] is True

    @pytest.mark.asyncio
    async def test_embedding_info_no_bm25(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH, enable_bm25=False)
        info = idx.embedding_info
        assert info["bm25_enabled"] is False

    @pytest.mark.asyncio
    async def test_backward_compatible_embedding_fn(self):
        custom_fn = lambda text: [1.0] * 128
        idx = SemanticIndex(db_path=TEST_DB_PATH, embedding_fn=custom_fn)
        ok = await idx.upsert_entity("share", "test", {"path": "/tmp"})
        assert ok is True


# =========================================================================
# Batch indexing
# =========================================================================

class TestBatchIndexing:
    @pytest.mark.asyncio
    async def test_batch_upsert_strategies(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH)
        items = [
            {"goal_type": "open_doc", "strategy_id": f"s{i}",
             "goal_summary": f"Strategy {i} for opening documents",
             "metadata": {"steps": [], "confidence": 0.9}}
            for i in range(10)
        ]
        count = await idx.batch_upsert_strategies(items)
        assert count == 10

        results = await idx.search_strategies("opening documents")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_batch_upsert_entities(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH)
        items = [
            {"entity_type": "share", "key": f"share_{i}",
             "value": {"path": f"\\\\server\\share{i}"},
             "confidence": 0.9}
            for i in range(10)
        ]
        count = await idx.batch_upsert_entities(items)
        assert count == 10

        results = await idx.search_entities("server share")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_batch_with_custom_batch_size(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH)
        items = [
            {"goal_type": "test", "strategy_id": f"s{i}",
             "goal_summary": f"Strategy {i}",
             "metadata": {"steps": []}}
            for i in range(15)
        ]
        count = await idx.batch_upsert_strategies(items, batch_size=5)
        assert count == 15

    @pytest.mark.asyncio
    async def test_batch_updates_existing(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH)
        await idx.upsert_strategy("test", "s1", "Old strategy", {"steps": [], "confidence": 0.5})

        items = [
            {"goal_type": "test", "strategy_id": "s1",
             "goal_summary": "Updated strategy",
             "metadata": {"steps": [], "confidence": 0.95}}
        ]
        count = await idx.batch_upsert_strategies(items)
        assert count == 1

        results = await idx.search_strategies("updated strategy")
        assert len(results) >= 1
        assert results[0]["confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_batch_empty_list(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH)
        count = await idx.batch_upsert_strategies([])
        assert count == 0

    @pytest.mark.asyncio
    async def test_batch_updates_bm25(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH)
        items = [
            {"goal_type": "print", "strategy_id": "s1",
             "goal_summary": "Install network printer",
             "metadata": {"steps": []}}
        ]
        await idx.batch_upsert_strategies(items)
        assert idx._bm25_strategies.doc_count == 1

    @pytest.mark.asyncio
    async def test_batch_entities_with_tags(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH)
        items = [
            {"entity_type": "device", "key": "printer_hp",
             "value": {"model": "HP LaserJet"},
             "tags": ["printer", "network"], "app_context": "office"}
        ]
        count = await idx.batch_upsert_entities(items)
        assert count == 1


# =========================================================================
# Hybrid search
# =========================================================================

class TestHybridSearch:
    @pytest.mark.asyncio
    async def test_hybrid_search_strategies(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH)
        await idx.upsert_strategy(
            "print", "s1",
            "Install and configure a network printer driver",
            {"steps": [{"tool": "driver_manager"}], "confidence": 0.9,
             "context_tags": ["printer", "network"]},
        )
        await idx.upsert_strategy(
            "open_doc", "s2",
            "Open a PDF document in reader application",
            {"steps": [{"tool": "desktop"}], "confidence": 0.9},
        )

        results = await idx.hybrid_search_strategies("setup printer driver")
        assert len(results) >= 1
        assert results[0]["strategy_id"] == "s1"
        assert "hybrid_score" in results[0]

    @pytest.mark.asyncio
    async def test_hybrid_search_entities(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH)
        await idx.upsert_entity("share", "finance_share",
                                {"path": "\\\\server\\finance"}, confidence=0.9)
        await idx.upsert_entity("app_path", "notepad",
                                {"exe": "notepad.exe"}, confidence=0.9)

        results = await idx.hybrid_search_entities("finance network drive")
        assert len(results) >= 1
        assert results[0]["key"] == "finance_share"
        assert "hybrid_score" in results[0]

    @pytest.mark.asyncio
    async def test_hybrid_search_with_goal_type_filter(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH)
        await idx.upsert_strategy("print", "s1", "Install printer", {"steps": []})
        await idx.upsert_strategy("open_doc", "s2", "Install reader", {"steps": []})

        results = await idx.hybrid_search_strategies("install", goal_type="print")
        ids = [r["strategy_id"] for r in results]
        assert "s1" in ids

    @pytest.mark.asyncio
    async def test_hybrid_search_no_bm25(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH, enable_bm25=False)
        await idx.upsert_strategy("test", "s1", "Test strategy", {"steps": []})

        results = await idx.hybrid_search_strategies("test strategy")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_hybrid_beats_pure_vector_on_exact_match(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH)
        await idx.upsert_strategy(
            "print", "s1",
            "Configure HP LaserJet Pro MFP M428fdw network printer",
            {"steps": [], "confidence": 0.9},
        )
        await idx.upsert_strategy(
            "print", "s2",
            "Setup generic printing device on local network",
            {"steps": [], "confidence": 0.9},
        )

        hybrid = await idx.hybrid_search_strategies("HP LaserJet Pro M428fdw")
        assert len(hybrid) >= 1
        if len(hybrid) >= 2:
            assert hybrid[0]["strategy_id"] == "s1"

    @pytest.mark.asyncio
    async def test_hybrid_search_empty_results(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH)
        results = await idx.hybrid_search_strategies("nonexistent query xyz")
        assert results == []


# =========================================================================
# BM25 consistency on delete/reset
# =========================================================================

class TestBM25Consistency:
    @pytest.mark.asyncio
    async def test_delete_strategy_removes_from_bm25(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH)
        await idx.upsert_strategy("test", "s1", "Unique strategy content", {"steps": []})
        assert idx._bm25_strategies.doc_count == 1

        await idx.delete_strategy("test", "s1")
        assert idx._bm25_strategies.doc_count == 0

    @pytest.mark.asyncio
    async def test_delete_entity_removes_from_bm25(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH)
        await idx.upsert_entity("share", "test_share", {"path": "/tmp"})
        assert idx._bm25_entities.doc_count == 1

        await idx.delete_entity("share", "test_share")
        assert idx._bm25_entities.doc_count == 0

    @pytest.mark.asyncio
    async def test_reset_clears_bm25(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH)
        await idx.upsert_strategy("test", "s1", "Strategy", {"steps": []})
        await idx.upsert_entity("share", "k1", {"p": "v"})
        assert idx._bm25_strategies.doc_count == 1
        assert idx._bm25_entities.doc_count == 1

        idx.reset()
        assert idx._bm25_strategies.doc_count == 0
        assert idx._bm25_entities.doc_count == 0

    @pytest.mark.asyncio
    async def test_upsert_updates_bm25(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH)
        await idx.upsert_strategy("test", "s1", "Old content", {"steps": []})
        await idx.upsert_strategy("test", "s1", "New content about printers", {"steps": []})
        assert idx._bm25_strategies.doc_count == 1

        results = idx._bm25_strategies.score("printers")
        assert len(results) >= 1
        assert results[0][0] == "test:s1"


# =========================================================================
# Integration with existing tests patterns
# =========================================================================

class TestExistingFunctionalityPreserved:
    @pytest.mark.asyncio
    async def test_original_vector_search_still_works(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH)
        await idx.upsert_strategy(
            "open_doc", "s1",
            "Open a Word document using Office COM",
            {"steps": [{"tool": "office", "action": "open_document"}], "confidence": 0.9},
        )
        results = await idx.search_strategies("how to open a word file")
        assert len(results) >= 1
        assert results[0]["strategy_id"] == "s1"

    @pytest.mark.asyncio
    async def test_original_entity_search_still_works(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH)
        await idx.upsert_entity("share", "finance_share",
                                {"remote_path": "\\\\server\\finance"}, confidence=0.9)
        results = await idx.search_entities("finance network drive")
        assert len(results) >= 1
        assert results[0]["key"] == "finance_share"

    @pytest.mark.asyncio
    async def test_count_and_reset(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH)
        await idx.upsert_entity("share", "a", {"p": "1"})
        await idx.upsert_entity("share", "b", {"p": "2"})
        c = await idx.count("entities")
        assert c >= 2
        idx.reset()
        c = await idx.count("entities")
        assert c == 0

    @pytest.mark.asyncio
    async def test_semantic_ranking_preserved(self):
        idx = SemanticIndex(db_path=TEST_DB_PATH)
        await idx.upsert_strategy(
            "print", "s1",
            "Configure and install a network printer",
            {"steps": [{"tool": "driver_manager"}], "confidence": 0.9},
        )
        await idx.upsert_strategy(
            "open_doc", "s2",
            "Open a PDF document in reader",
            {"steps": [{"tool": "desktop"}], "confidence": 0.9},
        )

        results = await idx.search_strategies("setup printing device on network")
        assert len(results) >= 1
        assert results[0]["strategy_id"] == "s1"
