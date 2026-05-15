# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evaluation"))

from evaluation.benchmark_domains import DOMAIN_BENCHMARKS, run_all_benchmarks, run_domain_benchmark
from evaluation.golden_runner import GoldenRunner


@pytest.fixture
def benchmark_runner(tmp_path):
    work = tmp_path / "benchmark_work"
    work.mkdir()
    return GoldenRunner(work_dir=work, skip_requires=True)


@pytest.mark.benchmark
class TestDomainBenchmarks:
    def test_eight_automation_domains_registered(self):
        expected = {
            "filesystem",
            "documents",
            "web_research",
            "drivers",
            "windows_ui",
            "office",
            "browser",
            "desktop",
        }
        assert set(DOMAIN_BENCHMARKS.keys()) == expected

    @pytest.mark.asyncio
    async def test_each_domain_benchmark_passes(self, benchmark_runner):
        reports = await run_all_benchmarks(runner=benchmark_runner)
        assert len(reports) == 8
        for report in reports:
            assert report.failed == 0, f"{report.domain} failures: {[r.detail for r in report.task_results if not r.passed]}"
            assert report.passed + report.skipped == report.total

    @pytest.mark.asyncio
    async def test_filesystem_domain_pass_rate(self, benchmark_runner):
        report = await run_domain_benchmark("filesystem", runner=benchmark_runner)
        assert report.pass_rate == 1.0
        assert report.total == 3
