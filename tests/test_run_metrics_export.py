# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from run_metrics_export import (
    compare_run_exports,
    extract_run_metrics_row,
    format_comparison_report,
    read_jsonl,
    summarize_runs,
    write_jsonl,
)


def _sample_state(**overrides):
    base = {
        "request_id": "run-a",
        "status": "completed",
        "created_at": "2026-05-15T10:00:00",
        "last_updated": "2026-05-15T10:01:00",
        "runtime_mode": "agentic",
        "inputs": {"text": "list outlook emails"},
        "model_routing": {"provider": "groq", "tier": "full", "selected_model": "llama-3.3-70b-versatile"},
        "outputs": {
            "cost": {
                "provider": "groq",
                "model": "llama-3.3-70b-versatile",
                "prompt_tokens": 10000,
                "completion_tokens": 500,
                "total_tokens": 10500,
                "total_cost_usd": 0.02,
                "tools_count": 8,
                "tools_json_chars": 12000,
                "system_prompt_chars": 1800,
                "llm_turns": 2,
                "agent_step_metrics": [
                    {
                        "step": 1,
                        "tools_offered": 8,
                        "tools_called": 1,
                        "tools_utilization_pct": 12.5,
                        "prompt_tokens": 8000,
                        "completion_tokens": 200,
                    },
                    {
                        "step": 2,
                        "tools_offered": 6,
                        "tools_called": 0,
                        "tools_utilization_pct": 0.0,
                        "prompt_tokens": 2000,
                        "completion_tokens": 300,
                    },
                ],
            },
        },
    }
    base.update(overrides)
    return base


class TestExtractRunMetrics:
    def test_extract_flat_metrics(self):
        row = extract_run_metrics_row(_sample_state())
        assert row["request_id"] == "run-a"
        assert row["provider"] == "groq"
        assert row["prompt_tokens"] == 10000
        assert row["tools_json_chars"] == 12000
        assert row["max_tools_offered"] == 8
        assert row["max_tools_called"] == 1
        assert row["agent_steps"] == 2


class TestJsonlRoundTrip:
    def test_write_read_jsonl(self, tmp_path):
        path = tmp_path / "runs.jsonl"
        rows = [extract_run_metrics_row(_sample_state())]
        assert write_jsonl(rows, path) == 1
        loaded = read_jsonl(path)
        assert loaded[0]["request_id"] == "run-a"


class TestCompare:
    def test_compare_shows_delta(self):
        before = [extract_run_metrics_row(_sample_state())]
        after_state = _sample_state(
            request_id="run-b",
            outputs={
                "cost": {
                    **_sample_state()["outputs"]["cost"],
                    "prompt_tokens": 5000,
                    "tools_json_chars": 4000,
                }
            },
        )
        after = [extract_run_metrics_row(after_state)]
        report = compare_run_exports(before, after)
        assert report["before"]["count"] == 1
        assert report["after"]["count"] == 1
        assert report["delta_avg"]["prompt_tokens"]["delta_avg"] < 0
        text = format_comparison_report(report)
        assert "prompt_tokens" in text

    def test_summarize_empty(self):
        assert summarize_runs([])["count"] == 0
