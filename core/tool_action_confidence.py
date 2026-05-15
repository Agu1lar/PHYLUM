# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Per-tool/action confidence from recorded success history.

Confidence uses a Beta-Binomial posterior (default prior 1,1 → 0.5 for unseen pairs).
Persisted under KV prefix ``tool_action_stats:``.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

KV_PREFIX = "tool_action_stats:"
SUCCESS_STATUSES = frozenset({"succeeded", "completed"})
FAILURE_STATUSES = frozenset({"failed", "rejected", "blocked"})
PARTIAL_STATUSES = frozenset({"partial", "needs_input"})


@dataclass
class ToolActionStats:
    tool: str
    action: str
    successes: int = 0
    failures: int = 0
    partial: int = 0
    total: int = 0
    last_status: str = ""
    last_at: float = 0.0
    avg_duration_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.successes / self.total if self.total else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "action": self.action,
            "successes": self.successes,
            "failures": self.failures,
            "partial": self.partial,
            "total": self.total,
            "success_rate": round(self.success_rate, 4),
            "last_status": self.last_status,
            "last_at": self.last_at,
            "avg_duration_ms": round(self.avg_duration_ms, 2),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolActionStats":
        return cls(
            tool=data["tool"],
            action=data["action"],
            successes=int(data.get("successes") or 0),
            failures=int(data.get("failures") or 0),
            partial=int(data.get("partial") or 0),
            total=int(data.get("total") or 0),
            last_status=str(data.get("last_status") or ""),
            last_at=float(data.get("last_at") or 0.0),
            avg_duration_ms=float(data.get("avg_duration_ms") or 0.0),
        )


@dataclass
class ToolActionConfidence:
    tool: str
    action: str
    confidence: float
    sample_size: int
    success_rate: float
    stats: ToolActionStats

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool": self.tool,
            "action": self.action,
            "confidence": round(self.confidence, 4),
            "sample_size": self.sample_size,
            "success_rate": round(self.success_rate, 4),
            "stats": self.stats.to_dict(),
        }


def _classify_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized in SUCCESS_STATUSES:
        return "success"
    if normalized in FAILURE_STATUSES:
        return "failure"
    if normalized in PARTIAL_STATUSES:
        return "partial"
    return "unknown"


class ToolActionConfidenceStore:
    """Records outcomes and exposes confidence scores per (tool, action)."""

    def __init__(
        self,
        persistence=None,
        *,
        prior_alpha: float = 1.0,
        prior_beta: float = 1.0,
    ):
        if persistence is None:
            from agent_persistence import Persistence
            persistence = Persistence.get()
        self.persistence = persistence
        self.prior_alpha = prior_alpha
        self.prior_beta = prior_beta
        self._cache: Dict[str, ToolActionStats] = {}

    def _key(self, tool: str, action: str) -> str:
        return f"{KV_PREFIX}{tool}:{action}"

    async def get_stats(self, tool: str, action: str) -> ToolActionStats:
        cache_key = f"{tool}:{action}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        raw = await self.persistence.get_kv(self._key(tool, action))
        if raw:
            stats = ToolActionStats.from_dict(raw)
        else:
            stats = ToolActionStats(tool=tool, action=action)
        self._cache[cache_key] = stats
        return stats

    async def record_outcome(
        self,
        tool: str,
        action: str,
        status: str,
        *,
        duration_ms: Optional[int] = None,
    ) -> ToolActionConfidence:
        stats = await self.get_stats(tool, action)
        bucket = _classify_status(status)
        if bucket == "success":
            stats.successes += 1
        elif bucket == "failure":
            stats.failures += 1
        elif bucket == "partial":
            stats.partial += 1
        stats.total += 1
        stats.last_status = status
        stats.last_at = time.time()
        if duration_ms is not None and duration_ms >= 0:
            n = stats.total
            stats.avg_duration_ms = ((stats.avg_duration_ms * (n - 1)) + duration_ms) / n
        await self.persistence.save_kv(self._key(tool, action), stats.to_dict())
        self._cache[f"{tool}:{action}"] = stats
        return self.confidence_from_stats(stats)

    def confidence_from_stats(self, stats: ToolActionStats) -> ToolActionConfidence:
        """Beta posterior mean: (successes + α) / (total + α + β)."""
        denom = stats.total + self.prior_alpha + self.prior_beta
        score = (stats.successes + self.prior_alpha) / denom if denom else 0.5
        return ToolActionConfidence(
            tool=stats.tool,
            action=stats.action,
            confidence=score,
            sample_size=stats.total,
            success_rate=stats.success_rate,
            stats=stats,
        )

    async def get_confidence(self, tool: str, action: str) -> ToolActionConfidence:
        stats = await self.get_stats(tool, action)
        return self.confidence_from_stats(stats)

    async def list_confidences(
        self,
        *,
        tool: Optional[str] = None,
        min_samples: int = 0,
        limit: int = 200,
    ) -> List[ToolActionConfidence]:
        prefix = f"{tool_action_stats_prefix(tool)}" if tool else KV_PREFIX
        records = await self.persistence.list_kv(prefix)
        out: List[ToolActionConfidence] = []
        for record in records:
            value = record.get("value")
            if not isinstance(value, dict):
                continue
            stats = ToolActionStats.from_dict(value)
            if stats.total < min_samples:
                continue
            out.append(self.confidence_from_stats(stats))
        out.sort(key=lambda c: (-c.sample_size, -c.confidence, c.tool, c.action))
        return out[:limit]

    async def plan_confidence(self, steps: List[Tuple[str, str]]) -> Dict[str, Any]:
        """Aggregate confidence for a sequence of (tool, action) pairs."""
        if not steps:
            return {"average": 0.5, "minimum": 0.5, "pairs": []}
        pairs: List[Dict[str, Any]] = []
        scores: List[float] = []
        for tool, action in steps:
            conf = await self.get_confidence(tool, action)
            pairs.append(conf.to_dict())
            scores.append(conf.confidence)
        return {
            "average": round(sum(scores) / len(scores), 4),
            "minimum": round(min(scores), 4),
            "pairs": pairs,
        }


def tool_action_stats_prefix(tool: Optional[str] = None) -> str:
    if tool:
        return f"{KV_PREFIX}{tool}:"
    return KV_PREFIX


async def record_outcome_from_task_result_async(
    store: ToolActionConfidenceStore,
    task: Dict[str, Any],
    result: Optional[Dict[str, Any]] = None,
) -> Optional[ToolActionConfidence]:
    tool = task.get("tool")
    action = task.get("action")
    if not tool or not action:
        return None
    action_result = ((result or {}).get("action_result") or {}) if result else {}
    status = action_result.get("status") or task.get("status") or "unknown"
    duration_ms = None
    data = action_result.get("data") or {}
    if isinstance(data, dict):
        duration_ms = data.get("duration_ms") or data.get("duration_seconds")
        if isinstance(duration_ms, float):
            duration_ms = int(duration_ms * 1000)
    return await store.record_outcome(tool, action, str(status), duration_ms=duration_ms)
