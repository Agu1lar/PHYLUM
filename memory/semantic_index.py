# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Semantic Index: local vector DB for semantic search over strategies and entities.

Uses LanceDB (embedded, serverless) for vector storage and retrieval.
Embeddings are generated locally via a lightweight bag-of-words TF-IDF
approach by default, with an optional plug point for real embedding models.

Two collections:
  - "strategies": indexes StrategyRecord goal_summary + steps as searchable text
  - "entities": indexes WorldEntity key + value as searchable text

The index is additive: records are upserted on write and queried on search.
Stale entries are pruned periodically.

Features:
  - **Pluggable embeddings**: use feature hashing (default), sentence-transformers,
    or any custom ``EmbeddingFn``.
  - **BM25 + vector hybrid re-ranking**: combine sparse lexical scores with dense
    vector scores using reciprocal rank fusion (RRF).
  - **Incremental batch indexing**: ``batch_upsert_*`` methods for bulk operations
    with configurable batch size.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "agent_workspace" / "semantic_db"
VECTOR_DIM = 128

try:
    import lancedb
    import pyarrow as pa
    _LANCEDB_AVAILABLE = True
except Exception:
    lancedb = None  # type: ignore
    pa = None  # type: ignore
    _LANCEDB_AVAILABLE = False


# ─── Lightweight local embeddings ───────────────────────────────────


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


def _text_to_vector(text: str, dim: int = VECTOR_DIM) -> List[float]:
    """Deterministic bag-of-words embedding via feature hashing.

    Maps each token to a bucket via hash, accumulates counts, then L2-normalizes.
    Not as powerful as transformer embeddings but works offline and is very fast.
    """
    tokens = _tokenize(text)
    if not tokens:
        return [0.0] * dim
    vec = [0.0] * dim
    for token in tokens:
        h = int(hashlib.md5(token.encode()).hexdigest(), 16)
        bucket = h % dim
        sign = 1.0 if (h // dim) % 2 == 0 else -1.0
        vec[bucket] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


EmbeddingFn = Callable[[str], List[float]]


# ─── Pluggable embedding providers ─────────────────────────────────


class EmbeddingProvider:
    """Base class for embedding providers."""

    @property
    def dim(self) -> int:
        return VECTOR_DIM

    def embed(self, text: str) -> List[float]:
        return _text_to_vector(text, self.dim)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]

    @property
    def name(self) -> str:
        return "feature_hash"


class FeatureHashProvider(EmbeddingProvider):
    """Default: fast deterministic feature hashing, no external deps."""

    def __init__(self, dim: int = VECTOR_DIM):
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> List[float]:
        return _text_to_vector(text, self._dim)

    @property
    def name(self) -> str:
        return "feature_hash"


class SentenceTransformerProvider(EmbeddingProvider):
    """Plug-in for sentence-transformers models (e.g., all-MiniLM-L6-v2).

    Loads the model lazily on first use. Supports batch encoding.
    Falls back to FeatureHashProvider if sentence-transformers is not installed.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", *, device: str = "cpu"):
        self._model_name = model_name
        self._device = device
        self._model = None
        self._dim_value: Optional[int] = None
        self._available = False
        self._fallback = FeatureHashProvider()
        self._load_attempted = False

    def _load(self) -> None:
        if self._load_attempted:
            return
        self._load_attempted = True
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_name, device=self._device)
            test_vec = self._model.encode("test", convert_to_numpy=True)
            self._dim_value = len(test_vec)
            self._available = True
            logger.info("Loaded sentence-transformers model %s (dim=%d)",
                        self._model_name, self._dim_value)
        except Exception:
            logger.info("sentence-transformers not available, using feature hash fallback")
            self._available = False

    @property
    def dim(self) -> int:
        self._load()
        if self._available and self._dim_value:
            return self._dim_value
        return self._fallback.dim

    def embed(self, text: str) -> List[float]:
        self._load()
        if not self._available or self._model is None:
            return self._fallback.embed(text)
        try:
            vec = self._model.encode(text, convert_to_numpy=True, normalize_embeddings=True)
            return vec.tolist()
        except Exception:
            return self._fallback.embed(text)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        self._load()
        if not self._available or self._model is None:
            return [self._fallback.embed(t) for t in texts]
        try:
            vecs = self._model.encode(texts, convert_to_numpy=True,
                                      normalize_embeddings=True, batch_size=32)
            return [v.tolist() for v in vecs]
        except Exception:
            return [self._fallback.embed(t) for t in texts]

    @property
    def name(self) -> str:
        self._load()
        return self._model_name if self._available else f"feature_hash(fallback:{self._model_name})"

    @property
    def is_available(self) -> bool:
        self._load()
        return self._available


def create_embedding_provider(
    provider_type: str = "feature_hash",
    *,
    model_name: str = "all-MiniLM-L6-v2",
    device: str = "cpu",
    dim: int = VECTOR_DIM,
) -> EmbeddingProvider:
    """Factory for embedding providers."""
    if provider_type == "sentence_transformers":
        return SentenceTransformerProvider(model_name, device=device)
    return FeatureHashProvider(dim)


# ─── BM25 scoring ──────────────────────────────────────────────────


class BM25:
    """Okapi BM25 scorer for sparse lexical retrieval.

    Builds an inverted index from a corpus of (doc_id, text) pairs,
    then scores queries against it.
    """

    def __init__(self, *, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self._doc_count = 0
        self._avg_dl = 0.0
        self._doc_lens: Dict[str, int] = {}
        self._doc_freqs: Dict[str, int] = {}
        self._inverted_index: Dict[str, Dict[str, int]] = {}
        self._doc_texts: Dict[str, str] = {}

    @property
    def doc_count(self) -> int:
        return self._doc_count

    def add_document(self, doc_id: str, text: str) -> None:
        tokens = _tokenize(text)
        if doc_id in self._doc_texts:
            self._remove_document_internal(doc_id)
        self._doc_texts[doc_id] = text
        self._doc_lens[doc_id] = len(tokens)
        self._doc_count += 1
        total_len = sum(self._doc_lens.values())
        self._avg_dl = total_len / max(self._doc_count, 1)

        seen = set()
        for token in tokens:
            if token not in self._inverted_index:
                self._inverted_index[token] = {}
            self._inverted_index[token][doc_id] = self._inverted_index[token].get(doc_id, 0) + 1
            if token not in seen:
                self._doc_freqs[token] = self._doc_freqs.get(token, 0) + 1
                seen.add(token)

    def add_documents(self, docs: List[Tuple[str, str]]) -> None:
        for doc_id, text in docs:
            self.add_document(doc_id, text)

    def _remove_document_internal(self, doc_id: str) -> None:
        if doc_id not in self._doc_texts:
            return
        old_tokens = _tokenize(self._doc_texts[doc_id])
        seen = set()
        for token in old_tokens:
            if token in self._inverted_index and doc_id in self._inverted_index[token]:
                del self._inverted_index[token][doc_id]
                if not self._inverted_index[token]:
                    del self._inverted_index[token]
            if token not in seen:
                if token in self._doc_freqs:
                    self._doc_freqs[token] -= 1
                    if self._doc_freqs[token] <= 0:
                        del self._doc_freqs[token]
                seen.add(token)
        del self._doc_texts[doc_id]
        del self._doc_lens[doc_id]
        self._doc_count -= 1
        total_len = sum(self._doc_lens.values())
        self._avg_dl = total_len / max(self._doc_count, 1)

    def remove_document(self, doc_id: str) -> None:
        self._remove_document_internal(doc_id)

    def score(self, query: str, *, limit: int = 10) -> List[Tuple[str, float]]:
        query_tokens = _tokenize(query)
        if not query_tokens or self._doc_count == 0:
            return []

        scores: Dict[str, float] = {}
        for token in query_tokens:
            if token not in self._inverted_index:
                continue
            df = self._doc_freqs.get(token, 0)
            idf = math.log((self._doc_count - df + 0.5) / (df + 0.5) + 1.0)
            for doc_id, tf in self._inverted_index[token].items():
                dl = self._doc_lens.get(doc_id, 1)
                num = tf * (self.k1 + 1)
                den = tf + self.k1 * (1 - self.b + self.b * dl / max(self._avg_dl, 1))
                scores[doc_id] = scores.get(doc_id, 0.0) + idf * (num / den)

        ranked = sorted(scores.items(), key=lambda x: -x[1])
        return ranked[:limit]

    def clear(self) -> None:
        self._doc_count = 0
        self._avg_dl = 0.0
        self._doc_lens.clear()
        self._doc_freqs.clear()
        self._inverted_index.clear()
        self._doc_texts.clear()


# ─── Hybrid re-ranker (RRF) ────────────────────────────────────────


def reciprocal_rank_fusion(
    rankings: List[List[Tuple[str, float]]],
    *,
    k: int = 60,
) -> List[Tuple[str, float]]:
    """Merge multiple ranked lists via Reciprocal Rank Fusion.

    Each ranking is a list of (doc_id, score) tuples. RRF assigns
    ``1/(k + rank)`` per list and sums across lists.
    """
    fused: Dict[str, float] = {}
    for ranking in rankings:
        for rank, (doc_id, _score) in enumerate(ranking, start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(fused.items(), key=lambda x: -x[1])


# ─── SemanticIndex ──────────────────────────────────────────────────


class SemanticIndex:
    """Local vector database for semantic search.

    Usage:
        idx = SemanticIndex()
        await idx.upsert_strategy("open_doc", "s1", "Open a Word document using Office COM", {...})
        results = await idx.search_strategies("how to open a doc file", limit=5)

    Supports:
      - Pluggable embedding providers (feature hash, sentence-transformers, custom)
      - BM25 + vector hybrid re-ranking via reciprocal rank fusion
      - Incremental batch indexing for bulk operations
    """

    def __init__(
        self,
        *,
        db_path: Optional[Path] = None,
        embedding_fn: Optional[EmbeddingFn] = None,
        embedding_provider: Optional[EmbeddingProvider] = None,
        vector_dim: int = VECTOR_DIM,
        enable_bm25: bool = True,
    ):
        self._db_path = db_path or DEFAULT_DB_PATH
        if embedding_provider is not None:
            self._provider = embedding_provider
            self._embed = embedding_provider.embed
            self._dim = embedding_provider.dim
        elif embedding_fn is not None:
            self._provider = None
            self._embed = embedding_fn
            self._dim = vector_dim
        else:
            self._provider = FeatureHashProvider(vector_dim)
            self._embed = self._provider.embed
            self._dim = vector_dim
        self._db = None
        self._tables: Dict[str, Any] = {}
        self._available = _LANCEDB_AVAILABLE
        self._bm25_strategies = BM25() if enable_bm25 else None
        self._bm25_entities = BM25() if enable_bm25 else None

    def _ensure_db(self):
        if self._db is not None:
            return
        if not self._available:
            return
        self._db_path.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(self._db_path))

    def _ensure_table(self, table_name: str):
        if table_name in self._tables:
            return self._tables[table_name]
        self._ensure_db()
        if self._db is None:
            return None
        try:
            table = self._db.open_table(table_name)
            self._tables[table_name] = table
            return table
        except Exception:
            return None

    def _create_table(self, table_name: str, data: List[Dict[str, Any]]):
        self._ensure_db()
        if self._db is None:
            return None
        try:
            table = self._db.create_table(table_name, data=data, mode="overwrite")
            self._tables[table_name] = table
            return table
        except Exception:
            logger.debug("Failed to create table %s", table_name, exc_info=True)
            return None

    # ─── Strategy indexing ──────────────────────────────────────────

    def _strategy_text(self, goal_summary: str, metadata: Dict[str, Any]) -> str:
        parts = [goal_summary]
        steps = metadata.get("steps") or []
        for step in steps[:10]:
            if isinstance(step, dict):
                parts.append(f"{step.get('tool', '')} {step.get('action', '')} {step.get('summary', '')}")
            elif isinstance(step, str):
                parts.append(step)
        tags = metadata.get("context_tags") or []
        parts.extend(tags)
        goal_type = metadata.get("goal_type", "")
        if goal_type:
            parts.append(goal_type)
        return " ".join(p for p in parts if p).strip()

    async def upsert_strategy(
        self,
        goal_type: str,
        strategy_id: str,
        goal_summary: str,
        metadata: Dict[str, Any],
    ) -> bool:
        if not self._available:
            return False
        text = self._strategy_text(goal_summary, metadata)
        vector = self._embed(text)
        doc_id = f"{goal_type}:{strategy_id}"

        if self._bm25_strategies is not None:
            self._bm25_strategies.add_document(doc_id, text)

        record = {
            "id": doc_id,
            "goal_type": goal_type,
            "strategy_id": strategy_id,
            "text": text,
            "vector": vector,
            "confidence": float(metadata.get("confidence", 0.8)),
            "used_count": int(metadata.get("used_count", 1)),
            "outcome": str(metadata.get("outcome", "success")),
        }

        table = self._ensure_table("strategies")
        if table is None:
            self._create_table("strategies", [record])
            return True

        try:
            existing = table.search().where(f"id = '{doc_id}'").limit(1).to_list()
            if existing:
                table.delete(f"id = '{doc_id}'")
            table.add([record])
            return True
        except Exception:
            logger.debug("Failed to upsert strategy %s", doc_id, exc_info=True)
            try:
                self._create_table("strategies", [record])
                return True
            except Exception:
                return False

    async def batch_upsert_strategies(
        self,
        items: List[Dict[str, Any]],
        *,
        batch_size: int = 50,
    ) -> int:
        """Upsert multiple strategies in batches.

        Each item must have: goal_type, strategy_id, goal_summary, metadata.
        Returns the number of successfully indexed items.
        """
        if not self._available or not items:
            return 0

        all_texts = []
        all_records = []
        for item in items:
            text = self._strategy_text(item["goal_summary"], item.get("metadata", {}))
            all_texts.append(text)

        if self._provider and hasattr(self._provider, 'embed_batch'):
            all_vectors = self._provider.embed_batch(all_texts)
        else:
            all_vectors = [self._embed(t) for t in all_texts]

        for i, item in enumerate(items):
            doc_id = f"{item['goal_type']}:{item['strategy_id']}"
            metadata = item.get("metadata", {})
            record = {
                "id": doc_id,
                "goal_type": item["goal_type"],
                "strategy_id": item["strategy_id"],
                "text": all_texts[i],
                "vector": all_vectors[i],
                "confidence": float(metadata.get("confidence", 0.8)),
                "used_count": int(metadata.get("used_count", 1)),
                "outcome": str(metadata.get("outcome", "success")),
            }
            all_records.append(record)
            if self._bm25_strategies is not None:
                self._bm25_strategies.add_document(doc_id, all_texts[i])

        indexed = 0
        for start in range(0, len(all_records), batch_size):
            batch = all_records[start:start + batch_size]
            table = self._ensure_table("strategies")
            try:
                if table is None:
                    self._create_table("strategies", batch)
                else:
                    ids_to_delete = [r["id"] for r in batch]
                    for doc_id in ids_to_delete:
                        try:
                            table.delete(f"id = '{doc_id}'")
                        except Exception:
                            pass
                    table.add(batch)
                indexed += len(batch)
            except Exception:
                logger.debug("Batch upsert strategies failed at offset %d", start, exc_info=True)
                if table is None:
                    try:
                        self._create_table("strategies", batch)
                        indexed += len(batch)
                    except Exception:
                        pass
        return indexed

    async def search_strategies(
        self,
        query: str,
        *,
        goal_type: Optional[str] = None,
        limit: int = 5,
        min_score: float = 0.1,
    ) -> List[Dict[str, Any]]:
        if not self._available:
            return []
        table = self._ensure_table("strategies")
        if table is None:
            return []

        query_vector = self._embed(query)
        try:
            search = table.search(query_vector).metric("cosine").limit(limit * 3)
            if goal_type:
                search = search.where(f"goal_type = '{goal_type}'")
            raw_results = search.to_list()
        except Exception:
            logger.debug("Strategy search failed", exc_info=True)
            return []

        results = []
        for row in raw_results:
            cosine_dist = float(row.get("_distance", 1.0))
            score = 1.0 - cosine_dist
            if score < min_score:
                continue
            results.append({
                "id": row.get("id"),
                "goal_type": row.get("goal_type"),
                "strategy_id": row.get("strategy_id"),
                "text": row.get("text"),
                "confidence": row.get("confidence"),
                "used_count": row.get("used_count"),
                "outcome": row.get("outcome"),
                "semantic_score": round(score, 4),
            })

        results.sort(key=lambda r: (-r["semantic_score"], -r.get("confidence", 0)))
        return results[:limit]

    async def hybrid_search_strategies(
        self,
        query: str,
        *,
        goal_type: Optional[str] = None,
        limit: int = 5,
        min_score: float = 0.0,
        vector_weight: float = 0.6,
        bm25_weight: float = 0.4,
    ) -> List[Dict[str, Any]]:
        """Search strategies using both vector similarity and BM25, fused via RRF."""
        vector_results = await self.search_strategies(
            query, goal_type=goal_type, limit=limit * 3, min_score=0.0,
        )
        vector_ranking = [(r["id"], r["semantic_score"]) for r in vector_results]

        bm25_ranking: List[Tuple[str, float]] = []
        if self._bm25_strategies is not None and self._bm25_strategies.doc_count > 0:
            raw_bm25 = self._bm25_strategies.score(query, limit=limit * 3)
            if goal_type:
                prefix = f"{goal_type}:"
                raw_bm25 = [(d, s) for d, s in raw_bm25 if d.startswith(prefix)]
            bm25_ranking = raw_bm25

        if not bm25_ranking:
            return vector_results[:limit]

        fused = reciprocal_rank_fusion(
            [vector_ranking, bm25_ranking],
        )

        id_to_result: Dict[str, Dict[str, Any]] = {r["id"]: r for r in vector_results}
        results = []
        for doc_id, rrf_score in fused:
            if doc_id in id_to_result:
                entry = dict(id_to_result[doc_id])
                entry["hybrid_score"] = round(rrf_score, 6)
                if rrf_score >= min_score:
                    results.append(entry)

        return results[:limit]

    # ─── Entity indexing ────────────────────────────────────────────

    def _entity_text(self, entity_type: str, key: str, value: Any) -> str:
        parts = [entity_type, key]
        if isinstance(value, dict):
            parts.append(json.dumps(value, default=str))
        elif isinstance(value, str):
            parts.append(value)
        elif value is not None:
            parts.append(str(value))
        return " ".join(parts).strip()

    async def upsert_entity(
        self,
        entity_type: str,
        key: str,
        value: Any,
        *,
        confidence: float = 0.8,
        tags: Optional[List[str]] = None,
        app_context: Optional[str] = None,
    ) -> bool:
        if not self._available:
            return False
        text = self._entity_text(entity_type, key, value)
        if tags:
            text += " " + " ".join(tags)
        if app_context:
            text += " " + app_context
        vector = self._embed(text)
        doc_id = f"{entity_type}:{key}"

        if self._bm25_entities is not None:
            self._bm25_entities.add_document(doc_id, text)

        record = {
            "id": doc_id,
            "entity_type": entity_type,
            "key": key,
            "text": text,
            "vector": vector,
            "confidence": float(confidence),
            "app_context": app_context or "",
        }

        table = self._ensure_table("entities")
        if table is None:
            self._create_table("entities", [record])
            return True

        try:
            existing = table.search().where(f"id = '{doc_id}'").limit(1).to_list()
            if existing:
                table.delete(f"id = '{doc_id}'")
            table.add([record])
            return True
        except Exception:
            logger.debug("Failed to upsert entity %s", doc_id, exc_info=True)
            try:
                self._create_table("entities", [record])
                return True
            except Exception:
                return False

    async def batch_upsert_entities(
        self,
        items: List[Dict[str, Any]],
        *,
        batch_size: int = 50,
    ) -> int:
        """Upsert multiple entities in batches.

        Each item must have: entity_type, key, value.
        Optional: confidence, tags, app_context.
        Returns the number of successfully indexed items.
        """
        if not self._available or not items:
            return 0

        all_texts = []
        for item in items:
            text = self._entity_text(item["entity_type"], item["key"], item.get("value"))
            tags = item.get("tags")
            if tags:
                text += " " + " ".join(tags)
            app_ctx = item.get("app_context")
            if app_ctx:
                text += " " + app_ctx
            all_texts.append(text)

        if self._provider and hasattr(self._provider, 'embed_batch'):
            all_vectors = self._provider.embed_batch(all_texts)
        else:
            all_vectors = [self._embed(t) for t in all_texts]

        all_records = []
        for i, item in enumerate(items):
            doc_id = f"{item['entity_type']}:{item['key']}"
            record = {
                "id": doc_id,
                "entity_type": item["entity_type"],
                "key": item["key"],
                "text": all_texts[i],
                "vector": all_vectors[i],
                "confidence": float(item.get("confidence", 0.8)),
                "app_context": item.get("app_context", ""),
            }
            all_records.append(record)
            if self._bm25_entities is not None:
                self._bm25_entities.add_document(doc_id, all_texts[i])

        indexed = 0
        for start in range(0, len(all_records), batch_size):
            batch = all_records[start:start + batch_size]
            table = self._ensure_table("entities")
            try:
                if table is None:
                    self._create_table("entities", batch)
                else:
                    for r in batch:
                        try:
                            table.delete(f"id = '{r['id']}'")
                        except Exception:
                            pass
                    table.add(batch)
                indexed += len(batch)
            except Exception:
                logger.debug("Batch upsert entities failed at offset %d", start, exc_info=True)
                if table is None:
                    try:
                        self._create_table("entities", batch)
                        indexed += len(batch)
                    except Exception:
                        pass
        return indexed

    async def search_entities(
        self,
        query: str,
        *,
        entity_type: Optional[str] = None,
        app_context: Optional[str] = None,
        limit: int = 10,
        min_score: float = 0.1,
    ) -> List[Dict[str, Any]]:
        if not self._available:
            return []
        table = self._ensure_table("entities")
        if table is None:
            return []

        query_vector = self._embed(query)
        try:
            search = table.search(query_vector).metric("cosine").limit(limit * 3)
            filters = []
            if entity_type:
                filters.append(f"entity_type = '{entity_type}'")
            if app_context:
                filters.append(f"app_context = '{app_context}'")
            if filters:
                search = search.where(" AND ".join(filters))
            raw_results = search.to_list()
        except Exception:
            logger.debug("Entity search failed", exc_info=True)
            return []

        results = []
        for row in raw_results:
            cosine_dist = float(row.get("_distance", 1.0))
            score = 1.0 - cosine_dist
            if score < min_score:
                continue
            results.append({
                "id": row.get("id"),
                "entity_type": row.get("entity_type"),
                "key": row.get("key"),
                "text": row.get("text"),
                "confidence": row.get("confidence"),
                "app_context": row.get("app_context"),
                "semantic_score": round(score, 4),
            })

        results.sort(key=lambda r: (-r["semantic_score"], -r.get("confidence", 0)))
        return results[:limit]

    async def hybrid_search_entities(
        self,
        query: str,
        *,
        entity_type: Optional[str] = None,
        app_context: Optional[str] = None,
        limit: int = 10,
        min_score: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Search entities using both vector similarity and BM25, fused via RRF."""
        vector_results = await self.search_entities(
            query, entity_type=entity_type, app_context=app_context,
            limit=limit * 3, min_score=0.0,
        )
        vector_ranking = [(r["id"], r["semantic_score"]) for r in vector_results]

        bm25_ranking: List[Tuple[str, float]] = []
        if self._bm25_entities is not None and self._bm25_entities.doc_count > 0:
            raw_bm25 = self._bm25_entities.score(query, limit=limit * 3)
            if entity_type:
                prefix = f"{entity_type}:"
                raw_bm25 = [(d, s) for d, s in raw_bm25 if d.startswith(prefix)]
            bm25_ranking = raw_bm25

        if not bm25_ranking:
            return vector_results[:limit]

        fused = reciprocal_rank_fusion([vector_ranking, bm25_ranking])

        id_to_result: Dict[str, Dict[str, Any]] = {r["id"]: r for r in vector_results}
        results = []
        for doc_id, rrf_score in fused:
            if doc_id in id_to_result:
                entry = dict(id_to_result[doc_id])
                entry["hybrid_score"] = round(rrf_score, 6)
                if rrf_score >= min_score:
                    results.append(entry)

        return results[:limit]

    # ─── Maintenance ────────────────────────────────────────────────

    async def delete_strategy(self, goal_type: str, strategy_id: str) -> bool:
        if not self._available:
            return False
        table = self._ensure_table("strategies")
        if table is None:
            return False
        doc_id = f"{goal_type}:{strategy_id}"
        if self._bm25_strategies is not None:
            self._bm25_strategies.remove_document(doc_id)
        try:
            table.delete(f"id = '{doc_id}'")
            return True
        except Exception:
            return False

    async def delete_entity(self, entity_type: str, key: str) -> bool:
        if not self._available:
            return False
        table = self._ensure_table("entities")
        if table is None:
            return False
        doc_id = f"{entity_type}:{key}"
        if self._bm25_entities is not None:
            self._bm25_entities.remove_document(doc_id)
        try:
            table.delete(f"id = '{doc_id}'")
            return True
        except Exception:
            return False

    async def count(self, table_name: str) -> int:
        table = self._ensure_table(table_name)
        if table is None:
            return 0
        try:
            return table.count_rows()
        except Exception:
            return 0

    def reset(self) -> None:
        self._tables.clear()
        self._db = None
        if self._bm25_strategies is not None:
            self._bm25_strategies.clear()
        if self._bm25_entities is not None:
            self._bm25_entities.clear()
        if self._db_path.exists():
            shutil.rmtree(self._db_path, ignore_errors=True)

    @property
    def embedding_info(self) -> Dict[str, Any]:
        """Return metadata about the current embedding provider."""
        return {
            "provider": self._provider.name if self._provider else "custom_fn",
            "dim": self._dim,
            "bm25_enabled": self._bm25_strategies is not None,
            "bm25_strategy_docs": self._bm25_strategies.doc_count if self._bm25_strategies else 0,
            "bm25_entity_docs": self._bm25_entities.doc_count if self._bm25_entities else 0,
        }
