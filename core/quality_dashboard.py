# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Quality dashboard — aggregated autonomy metrics per runtime / provider / model."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from autonomy_metrics import RunAutonomyMetrics

logger = logging.getLogger(__name__)

KV_PREFIX = "quality_dashboard:"


def dimension_key(*, runtime_mode: str, provider: str, model: str) -> str:
    return f"{runtime_mode or 'unknown'}|{provider or 'none'}|{model or 'unknown'}"


def parse_dimension_key(key: str) -> Dict[str, str]:
    parts = key.split("|", 2)
    while len(parts) < 3:
        parts.append("unknown")
    return {"runtime_mode": parts[0], "provider": parts[1], "model": parts[2]}


@dataclass
class QualityVersionAggregate:
    dimension: str
    runtime_mode: str
    provider: str
    model: str
    runs_total: int = 0
    runs_succeeded: int = 0
    runs_failed: int = 0
    interrupted_loops: int = 0
    total_steps_to_success: int = 0
    total_handoffs: int = 0
    total_avoidable_handoffs: int = 0
    total_recoveries: int = 0
    total_effective_recoveries: int = 0
    total_cost_usd: float = 0.0
    total_tokens: int = 0
    recent_run_ids: List[str] = field(default_factory=list)
    last_run_at: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.runs_succeeded / self.runs_total if self.runs_total else 0.0

    @property
    def avg_steps_to_success(self) -> float:
        return self.total_steps_to_success / self.runs_succeeded if self.runs_succeeded else 0.0

    @property
    def avoidable_handoff_rate(self) -> float:
        return self.total_avoidable_handoffs / self.total_handoffs if self.total_handoffs else 0.0

    @property
    def recovery_effectiveness_rate(self) -> float:
        return self.total_effective_recoveries / self.total_recoveries if self.total_recoveries else 0.0

    @property
    def interrupt_rate(self) -> float:
        return self.interrupted_loops / self.runs_total if self.runs_total else 0.0

    @property
    def avg_cost_usd(self) -> float:
        return self.total_cost_usd / self.runs_total if self.runs_total else 0.0

    @property
    def avg_tokens(self) -> float:
        return self.total_tokens / self.runs_total if self.runs_total else 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension,
            "runtime_mode": self.runtime_mode,
            "provider": self.provider,
            "model": self.model,
            "runs_total": self.runs_total,
            "runs_succeeded": self.runs_succeeded,
            "runs_failed": self.runs_failed,
            "success_rate": round(self.success_rate, 4),
            "avg_steps_to_success": round(self.avg_steps_to_success, 2),
            "total_handoffs": self.total_handoffs,
            "total_avoidable_handoffs": self.total_avoidable_handoffs,
            "avoidable_handoff_rate": round(self.avoidable_handoff_rate, 4),
            "total_recoveries": self.total_recoveries,
            "total_effective_recoveries": self.total_effective_recoveries,
            "recovery_effectiveness_rate": round(self.recovery_effectiveness_rate, 4),
            "interrupted_loops": self.interrupted_loops,
            "interrupt_rate": round(self.interrupt_rate, 4),
            "avg_cost_usd": round(self.avg_cost_usd, 6),
            "avg_tokens": round(self.avg_tokens, 1),
            "recent_run_ids": list(self.recent_run_ids),
            "last_run_at": self.last_run_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "QualityVersionAggregate":
        dim = data.get("dimension") or ""
        parsed = parse_dimension_key(dim) if dim else {}
        return cls(
            dimension=dim,
            runtime_mode=data.get("runtime_mode") or parsed.get("runtime_mode", ""),
            provider=data.get("provider") or parsed.get("provider", ""),
            model=data.get("model") or parsed.get("model", ""),
            runs_total=int(data.get("runs_total") or 0),
            runs_succeeded=int(data.get("runs_succeeded") or 0),
            runs_failed=int(data.get("runs_failed") or 0),
            interrupted_loops=int(data.get("interrupted_loops") or 0),
            total_steps_to_success=int(data.get("total_steps_to_success") or 0),
            total_handoffs=int(data.get("total_handoffs") or 0),
            total_avoidable_handoffs=int(data.get("total_avoidable_handoffs") or 0),
            total_recoveries=int(data.get("total_recoveries") or 0),
            total_effective_recoveries=int(data.get("total_effective_recoveries") or 0),
            total_cost_usd=float(data.get("total_cost_usd") or 0.0),
            total_tokens=int(data.get("total_tokens") or 0),
            recent_run_ids=list(data.get("recent_run_ids") or []),
            last_run_at=float(data.get("last_run_at") or 0.0),
        )


class QualityDashboard:
    def __init__(self, persistence=None):
        if persistence is None:
            from agent_persistence import Persistence
            persistence = Persistence.get()
        self.persistence = persistence

    def _kv_key(self, dim: str) -> str:
        return f"{KV_PREFIX}{dim}"

    async def record_run(self, metrics: RunAutonomyMetrics) -> QualityVersionAggregate:
        dim = dimension_key(
            runtime_mode=metrics.runtime_mode,
            provider=metrics.provider,
            model=metrics.model,
        )
        raw = await self.persistence.get_kv(self._kv_key(dim))
        agg = QualityVersionAggregate.from_dict(raw) if raw else QualityVersionAggregate(
            dimension=dim,
            runtime_mode=metrics.runtime_mode,
            provider=metrics.provider,
            model=metrics.model,
        )

        agg.runs_total += 1
        if metrics.success:
            agg.runs_succeeded += 1
            agg.total_steps_to_success += metrics.steps_to_success
        else:
            agg.runs_failed += 1
        if metrics.interrupted_loop:
            agg.interrupted_loops += 1
        agg.total_handoffs += metrics.handoffs_total
        agg.total_avoidable_handoffs += metrics.avoidable_handoffs
        agg.total_recoveries += metrics.recoveries_total
        agg.total_effective_recoveries += metrics.effective_recoveries
        agg.total_cost_usd += metrics.cost_usd
        agg.total_tokens += metrics.total_tokens
        agg.recent_run_ids = ([metrics.request_id] + [r for r in agg.recent_run_ids if r != metrics.request_id])[:20]
        agg.last_run_at = metrics.recorded_at or time.time()

        await self.persistence.save_kv(self._kv_key(dim), agg.to_dict())
        return agg

    async def get_version(
        self,
        *,
        runtime_mode: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Optional[QualityVersionAggregate]:
        if not runtime_mode and not provider and not model:
            return None
        dim = dimension_key(
            runtime_mode=runtime_mode or "unknown",
            provider=provider or "none",
            model=model or "unknown",
        )
        raw = await self.persistence.get_kv(self._kv_key(dim))
        return QualityVersionAggregate.from_dict(raw) if raw else None

    async def list_versions(self, limit: int = 100) -> List[QualityVersionAggregate]:
        records = await self.persistence.list_kv(KV_PREFIX)
        out = [QualityVersionAggregate.from_dict(r["value"]) for r in records if isinstance(r.get("value"), dict)]
        out.sort(key=lambda v: (-v.last_run_at, -v.runs_total))
        return out[:limit]

    async def dashboard_summary(
        self,
        *,
        runtime_mode: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        versions = await self.list_versions(limit=500)
        if runtime_mode:
            versions = [v for v in versions if v.runtime_mode == runtime_mode]
        if provider:
            versions = [v for v in versions if v.provider == provider]
        if model:
            versions = [v for v in versions if v.model == model]

        if not versions:
            return {"versions": [], "totals": {}, "filters": {"runtime_mode": runtime_mode, "provider": provider, "model": model}}

        totals = {
            "runs_total": sum(v.runs_total for v in versions),
            "runs_succeeded": sum(v.runs_succeeded for v in versions),
            "runs_failed": sum(v.runs_failed for v in versions),
            "interrupted_loops": sum(v.interrupted_loops for v in versions),
            "total_handoffs": sum(v.total_handoffs for v in versions),
            "total_avoidable_handoffs": sum(v.total_avoidable_handoffs for v in versions),
            "total_recoveries": sum(v.total_recoveries for v in versions),
            "total_effective_recoveries": sum(v.total_effective_recoveries for v in versions),
            "total_cost_usd": round(sum(v.total_cost_usd for v in versions), 6),
        }
        rt = totals["runs_total"]
        totals["success_rate"] = round(totals["runs_succeeded"] / rt, 4) if rt else 0.0
        totals["interrupt_rate"] = round(totals["interrupted_loops"] / rt, 4) if rt else 0.0
        hh = totals["total_handoffs"]
        totals["avoidable_handoff_rate"] = round(totals["total_avoidable_handoffs"] / hh, 4) if hh else 0.0
        rc = totals["total_recoveries"]
        totals["recovery_effectiveness_rate"] = round(totals["total_effective_recoveries"] / rc, 4) if rc else 0.0

        return {
            "filters": {"runtime_mode": runtime_mode, "provider": provider, "model": model},
            "versions": [v.to_dict() for v in versions],
            "totals": totals,
            "version_count": len(versions),
        }
