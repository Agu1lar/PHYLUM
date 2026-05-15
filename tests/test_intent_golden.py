# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))
sys.path.insert(0, str(ROOT / "core"))

from evaluation.intent_golden_runner import (
    load_intent_golden_benchmarks,
    run_intent_benchmarks,
)


@pytest.mark.golden
class TestIntentGoldenCatalog:
    def test_manifest_lists_all_benchmarks(self):
        manifest = json.loads(
            (ROOT / "evaluation" / "golden" / "manifest.json").read_text(encoding="utf-8")
        )
        loaded = {b.id for b in load_intent_golden_benchmarks()}
        for benchmark_id in manifest["benchmarks"]:
            assert benchmark_id in loaded

    def test_smoke_benchmarks_exist(self):
        smoke = [b for b in load_intent_golden_benchmarks() if "smoke" in b.tags]
        assert len(smoke) >= 4


@pytest.mark.golden
class TestIntentGoldenExecution:
    def test_all_intent_goldens_pass(self):
        results = run_intent_benchmarks(load_intent_golden_benchmarks())
        failures = [r for r in results if not r.passed]
        assert not failures, "\n".join(str(f) for f in failures)

    def test_outlook_unread_accepts_office_not_shell(self):
        benchmarks = {b.id: b for b in load_intent_golden_benchmarks()}
        result = run_intent_benchmarks([benchmarks["in-001-outlook-unread-pt"]])[0]
        assert result.passed


@pytest.mark.golden
def test_intent_golden_cli_returns_zero():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "evaluation" / "intent_golden_runner.py")],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
