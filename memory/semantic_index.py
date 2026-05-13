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


# ─── SemanticIndex ──────────────────────────────────────────────────


class SemanticIndex:
    """Local vector database for semantic search.

    Usage:
        idx = SemanticIndex()
        await idx.upsert_strategy("open_doc", "s1", "Open a Word document using Office COM", {...})
        results = await idx.search_strategies("how to open a doc file", limit=5)
    """

    def __init__(
        self,
        *,
        db_path: Optional[Path] = None,
        embedding_fn: Optional[EmbeddingFn] = None,
        vector_dim: int = VECTOR_DIM,
    ):
        self._db_path = db_path or DEFAULT_DB_PATH
        self._embed = embedding_fn or _text_to_vector
        self._dim = vector_dim
        self._db = None
        self._tables: Dict[str, Any] = {}
        self._available = _LANCEDB_AVAILABLE

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

    # ─── Maintenance ────────────────────────────────────────────────

    async def delete_strategy(self, goal_type: str, strategy_id: str) -> bool:
        if not self._available:
            return False
        table = self._ensure_table("strategies")
        if table is None:
            return False
        doc_id = f"{goal_type}:{strategy_id}"
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
        if self._db_path.exists():
            shutil.rmtree(self._db_path, ignore_errors=True)
