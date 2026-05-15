# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Export and compare per-run LLM payload/cost metrics (Fase 0.3)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, TextIO


def extract_run_metrics_row(state: Dict[str, Any]) -> Dict[str, Any]:
    """Build a flat metrics record from a persisted run state."""
    outputs = state.get("outputs") or {}
    session = state.get("agent_session") or {}
    cost = outputs.get("cost") or session.get("cost") or {}
    inputs = state.get("inputs") or {}
    text = str(inputs.get("text") or inputs.get("prompt") or "")
    agent_resp = outputs.get("agent_final_response") or {}
    routing = state.get("model_routing") or {}
    step_metrics = cost.get("agent_step_metrics") or cost.get("llm_turn_metrics") or []

    budget = cost.get("budget_compliance") or {}
    row: Dict[str, Any] = {
        "request_id": state.get("request_id"),
        "status": state.get("status"),
        "created_at": state.get("created_at"),
        "last_updated": state.get("last_updated"),
        "runtime_mode": state.get("runtime_mode"),
        "provider": cost.get("provider") or agent_resp.get("provider") or routing.get("provider"),
        "model": cost.get("model") or agent_resp.get("model") or routing.get("selected_model"),
        "routing_tier": routing.get("tier"),
        "prompt_preview": text[:160],
        "prompt_chars": len(text),
        "prompt_tokens": int(cost.get("prompt_tokens") or 0),
        "completion_tokens": int(cost.get("completion_tokens") or 0),
        "total_tokens": int(cost.get("total_tokens") or 0),
        "total_cost_usd": float(cost.get("total_cost_usd") or 0),
        "tools_count": int(cost.get("tools_count") or cost.get("tools_offered") or 0),
        "tools_json_chars": int(cost.get("tools_json_chars") or 0),
        "system_prompt_chars": int(cost.get("system_prompt_chars") or 0),
        "llm_turns": int(cost.get("llm_turns") or 0),
        "agent_steps": len(step_metrics),
        "task_class": cost.get("task_class") or budget.get("task_class"),
        "within_task_budget_targets": budget.get("within_targets"),
        "task_budget_violation_count": budget.get("violation_count", 0),
        "exported_at": datetime.utcnow().isoformat() + "Z",
    }
    if cost.get("budget_targets"):
        row["budget_targets"] = cost["budget_targets"]

    if step_metrics:
        offered = [int(s.get("tools_offered") or s.get("tools_count") or 0) for s in step_metrics]
        called = [int(s.get("tools_called") or 0) for s in step_metrics]
        row["max_tools_offered"] = max(offered) if offered else 0
        row["max_tools_called"] = max(called) if called else 0
        row["avg_tools_utilization_pct"] = round(
            sum(
                float(s.get("tools_utilization_pct") or 0)
                for s in step_metrics
            )
            / max(len(step_metrics), 1),
            1,
        )
        row["step_metrics"] = step_metrics

    return row


def write_jsonl(rows: Iterable[Dict[str, Any]], path: Path, *, append: bool = False) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    count = 0
    with path.open(mode, encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            count += 1
    return count


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
    return rows


def _numeric(row: Dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def summarize_runs(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"count": 0}
    keys = (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "total_cost_usd",
        "tools_json_chars",
        "system_prompt_chars",
        "tools_count",
        "llm_turns",
    )

    def agg(key: str) -> Dict[str, float]:
        values = [_numeric(row, key) for row in rows]
        values.sort()
        mid = len(values) // 2
        median = values[mid] if values else 0.0
        return {
            "sum": round(sum(values), 4),
            "avg": round(sum(values) / max(len(values), 1), 4),
            "median": round(median, 4),
            "max": round(max(values) if values else 0, 4),
        }

    return {
        "count": len(rows),
        "metrics": {key: agg(key) for key in keys},
    }


def compare_run_exports(
    before_rows: List[Dict[str, Any]],
    after_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compare two JSONL snapshots (e.g. before/after an optimization)."""
    before_summary = summarize_runs(before_rows)
    after_summary = summarize_runs(after_rows)
    deltas: Dict[str, Dict[str, float]] = {}
    before_metrics = before_summary.get("metrics") or {}
    after_metrics = after_summary.get("metrics") or {}
    for key in set(before_metrics) | set(after_metrics):
        b_avg = float((before_metrics.get(key) or {}).get("avg") or 0)
        a_avg = float((after_metrics.get(key) or {}).get("avg") or 0)
        delta = a_avg - b_avg
        pct = (100.0 * delta / b_avg) if b_avg else (100.0 if a_avg else 0.0)
        deltas[key] = {
            "before_avg": round(b_avg, 4),
            "after_avg": round(a_avg, 4),
            "delta_avg": round(delta, 4),
            "delta_pct": round(pct, 1),
        }
    return {
        "before": before_summary,
        "after": after_summary,
        "delta_avg": deltas,
    }


def format_comparison_report(comparison: Dict[str, Any]) -> str:
    lines = [
        "Run metrics comparison (avg per run)",
        "=" * 72,
        f"{'metric':<22} {'before':>10} {'after':>10} {'delta':>10} {'delta %':>10}",
        "-" * 72,
    ]
    for key, item in sorted((comparison.get("delta_avg") or {}).items()):
        lines.append(
            f"{key:<22} {item['before_avg']:>10.2f} {item['after_avg']:>10.2f} "
            f"{item['delta_avg']:>+10.2f} {item['delta_pct']:>+9.1f}%"
        )
    lines.append("-" * 72)
    lines.append(
        f"runs: before={comparison.get('before', {}).get('count', 0)} "
        f"after={comparison.get('after', {}).get('count', 0)}"
    )
    return "\n".join(lines)


async def load_run_states_from_db(db_path: str) -> List[Dict[str, Any]]:
    from agent_persistence import Persistence

    persistence = Persistence(db_path)
    return await persistence.list_states()


async def export_runs_to_jsonl(
    output_path: Path,
    *,
    db_path: Optional[str] = None,
    request_ids: Optional[List[str]] = None,
    limit: Optional[int] = None,
    append: bool = False,
    include_without_cost: bool = False,
) -> int:
    if request_ids:
        from agent_persistence import Persistence

        persistence = Persistence(db_path or Persistence.get().db_path)
        states: List[Dict[str, Any]] = []
        for request_id in request_ids:
            state = await persistence.get_kv(f"state:{request_id}")
            if isinstance(state, dict):
                states.append(state)
    else:
        from agent_persistence import Persistence

        resolved_db = db_path or Persistence.get().db_path
        states = await load_run_states_from_db(resolved_db)

    rows: List[Dict[str, Any]] = []
    for state in states:
        row = extract_run_metrics_row(state)
        if not include_without_cost and not row.get("total_tokens") and not row.get("tools_json_chars"):
            continue
        rows.append(row)

    rows.sort(key=lambda item: str(item.get("last_updated") or item.get("created_at") or ""), reverse=True)
    if limit is not None and limit > 0:
        rows = rows[:limit]
    return write_jsonl(rows, output_path, append=append)
