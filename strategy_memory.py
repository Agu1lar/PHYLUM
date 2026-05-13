"""Strategy Memory: tracks successful strategies per objective type.

Records which tool sequences, approaches and parameters worked for specific
goal types, enabling the agent to reuse proven strategies and avoid
repeating failed approaches.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from agent_persistence import Persistence

logger = logging.getLogger(__name__)

MAX_STRATEGIES_PER_GOAL = 20
MAX_FAILED_APPROACHES = 10


class StrategyRecord:
    __slots__ = (
        "strategy_id", "goal_type", "goal_summary", "steps", "outcome",
        "confidence", "created_at", "used_count", "last_used_at",
        "context_tags", "duration_ms",
    )

    def __init__(
        self,
        *,
        strategy_id: str,
        goal_type: str,
        goal_summary: str,
        steps: List[Dict[str, Any]],
        outcome: str,
        confidence: float = 0.8,
        created_at: Optional[str] = None,
        used_count: int = 1,
        last_used_at: Optional[str] = None,
        context_tags: Optional[List[str]] = None,
        duration_ms: Optional[int] = None,
    ):
        self.strategy_id = strategy_id
        self.goal_type = goal_type
        self.goal_summary = goal_summary
        self.steps = steps
        self.outcome = outcome
        self.confidence = confidence
        self.created_at = created_at or datetime.utcnow().isoformat()
        self.used_count = used_count
        self.last_used_at = last_used_at or self.created_at
        self.context_tags = context_tags or []
        self.duration_ms = duration_ms

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "goal_type": self.goal_type,
            "goal_summary": self.goal_summary,
            "steps": self.steps,
            "outcome": self.outcome,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "used_count": self.used_count,
            "last_used_at": self.last_used_at,
            "context_tags": self.context_tags,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StrategyRecord":
        return cls(
            strategy_id=data["strategy_id"],
            goal_type=data["goal_type"],
            goal_summary=data.get("goal_summary", ""),
            steps=data.get("steps", []),
            outcome=data.get("outcome", "unknown"),
            confidence=data.get("confidence", 0.8),
            created_at=data.get("created_at"),
            used_count=data.get("used_count", 1),
            last_used_at=data.get("last_used_at"),
            context_tags=data.get("context_tags", []),
            duration_ms=data.get("duration_ms"),
        )


class StrategyMemory:
    KV_PREFIX = "strategy:"
    FAILED_PREFIX = "strategy_failed:"

    def __init__(self, persistence: Optional[Persistence] = None, *, semantic_index=None):
        self.persistence = persistence or Persistence.get()
        self._semantic_index = semantic_index

    def set_semantic_index(self, index) -> None:
        self._semantic_index = index

    def _storage_key(self, goal_type: str, strategy_id: str) -> str:
        return f"{self.KV_PREFIX}{goal_type}:{strategy_id}"

    def _failed_key(self, goal_type: str, approach_hash: str) -> str:
        return f"{self.FAILED_PREFIX}{goal_type}:{approach_hash}"

    async def record_success(
        self,
        *,
        strategy_id: str,
        goal_type: str,
        goal_summary: str,
        steps: List[Dict[str, Any]],
        confidence: float = 0.85,
        context_tags: Optional[List[str]] = None,
        duration_ms: Optional[int] = None,
    ) -> StrategyRecord:
        storage_key = self._storage_key(goal_type, strategy_id)
        existing_raw = await self.persistence.get_kv(storage_key)

        if existing_raw and isinstance(existing_raw, dict):
            record = StrategyRecord.from_dict(existing_raw)
            record.used_count += 1
            record.last_used_at = datetime.utcnow().isoformat()
            record.confidence = min(1.0, record.confidence + 0.05)
            if duration_ms:
                record.duration_ms = duration_ms
            await self.persistence.save_kv(storage_key, record.to_dict())
            await self._index_strategy(record)
            return record

        record = StrategyRecord(
            strategy_id=strategy_id,
            goal_type=goal_type,
            goal_summary=goal_summary,
            steps=steps,
            outcome="success",
            confidence=confidence,
            context_tags=context_tags,
            duration_ms=duration_ms,
        )
        await self.persistence.save_kv(storage_key, record.to_dict())
        await self._index_strategy(record)
        await self._trim_strategies(goal_type)
        return record

    async def record_failure(
        self,
        *,
        goal_type: str,
        approach_summary: str,
        steps: List[Dict[str, Any]],
        error: str,
        context_tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        import hashlib
        approach_hash = hashlib.sha256(
            json.dumps({"steps": steps, "goal": goal_type}, sort_keys=True, default=str).encode()
        ).hexdigest()[:12]

        failed_record = {
            "goal_type": goal_type,
            "approach_summary": approach_summary,
            "approach_hash": approach_hash,
            "steps": steps,
            "error": error,
            "recorded_at": datetime.utcnow().isoformat(),
            "context_tags": context_tags or [],
        }
        storage_key = self._failed_key(goal_type, approach_hash)
        await self.persistence.save_kv(storage_key, failed_record)
        return failed_record

    async def _index_strategy(self, record: StrategyRecord) -> None:
        if self._semantic_index is None:
            return
        try:
            await self._semantic_index.upsert_strategy(
                record.goal_type,
                record.strategy_id,
                record.goal_summary,
                record.to_dict(),
            )
        except Exception:
            logger.debug("Failed to index strategy %s:%s", record.goal_type, record.strategy_id, exc_info=True)

    async def semantic_search(
        self,
        query: str,
        *,
        goal_type: Optional[str] = None,
        limit: int = 5,
        min_score: float = 0.1,
    ) -> List[Dict[str, Any]]:
        """Semantic vector search across strategies. Falls back to typed search if no index."""
        if self._semantic_index is not None:
            try:
                return await self._semantic_index.search_strategies(
                    query,
                    goal_type=goal_type,
                    limit=limit,
                    min_score=min_score,
                )
            except Exception:
                logger.debug("Semantic strategy search failed, falling back", exc_info=True)

        if goal_type:
            results = await self.find_strategies(goal_type, query=query, limit=limit)
        else:
            results = []
        return [r.to_dict() for r in results]

    async def find_strategies(
        self,
        goal_type: str,
        *,
        query: Optional[str] = None,
        min_confidence: float = 0.0,
        context_tags: Optional[List[str]] = None,
        limit: int = 5,
    ) -> List[StrategyRecord]:
        prefix = f"{self.KV_PREFIX}{goal_type}:"
        records = await self.persistence.list_kv(prefix)
        strategies: List[StrategyRecord] = []

        for record in records:
            raw = record.get("value")
            if not isinstance(raw, dict):
                continue
            strategy = StrategyRecord.from_dict(raw)
            if strategy.confidence < min_confidence:
                continue
            if context_tags and not any(t in strategy.context_tags for t in context_tags):
                continue
            if query:
                searchable = f"{strategy.goal_summary} {json.dumps(strategy.steps, default=str)}".lower()
                if query.lower() not in searchable:
                    continue
            strategies.append(strategy)

        strategies.sort(key=lambda s: (-s.confidence, -s.used_count))
        return strategies[:limit]

    async def find_failed_approaches(self, goal_type: str, limit: int = 10) -> List[Dict[str, Any]]:
        prefix = f"{self.FAILED_PREFIX}{goal_type}:"
        records = await self.persistence.list_kv(prefix)
        failed: List[Dict[str, Any]] = []
        for record in records:
            raw = record.get("value")
            if isinstance(raw, dict):
                failed.append(raw)
        return failed[:limit]

    async def best_strategy(self, goal_type: str, *, query: Optional[str] = None) -> Optional[StrategyRecord]:
        results = await self.find_strategies(goal_type, query=query, limit=1)
        return results[0] if results else None

    async def mark_reused(self, goal_type: str, strategy_id: str) -> Optional[StrategyRecord]:
        storage_key = self._storage_key(goal_type, strategy_id)
        raw = await self.persistence.get_kv(storage_key)
        if not raw or not isinstance(raw, dict):
            return None
        record = StrategyRecord.from_dict(raw)
        record.used_count += 1
        record.last_used_at = datetime.utcnow().isoformat()
        record.confidence = min(1.0, record.confidence + 0.03)
        await self.persistence.save_kv(storage_key, record.to_dict())
        return record

    async def _trim_strategies(self, goal_type: str) -> None:
        prefix = f"{self.KV_PREFIX}{goal_type}:"
        records = await self.persistence.list_kv(prefix)
        if len(records) <= MAX_STRATEGIES_PER_GOAL:
            return
        entries = []
        for record in records:
            raw = record.get("value")
            if isinstance(raw, dict):
                entries.append((record["key"], raw.get("confidence", 0), raw.get("used_count", 0)))
        entries.sort(key=lambda e: (e[1], e[2]))
        to_remove = len(entries) - MAX_STRATEGIES_PER_GOAL
        for key, _, _ in entries[:to_remove]:
            await self.persistence.delete_kv(key)

    async def list_goal_types(self) -> List[Dict[str, Any]]:
        records = await self.persistence.list_kv(self.KV_PREFIX)
        goal_types: Dict[str, int] = {}
        for record in records:
            raw = record.get("value")
            if isinstance(raw, dict):
                gt = raw.get("goal_type", "unknown")
                goal_types[gt] = goal_types.get(gt, 0) + 1
        return [{"goal_type": k, "strategy_count": v} for k, v in sorted(goal_types.items())]
