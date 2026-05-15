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

from evaluation.benchmark_domains import DOMAIN_BENCHMARKS, list_domains
from evaluation.golden_runner import GoldenRunner, load_golden_tasks


@pytest.fixture
def golden_work_dir(tmp_path):
    work = tmp_path / "golden_work"
    work.mkdir()
    return work


@pytest.fixture
def golden_runner(golden_work_dir):
    return GoldenRunner(work_dir=golden_work_dir, skip_requires=True)


@pytest.mark.golden
class TestGoldenTaskCatalog:
    def test_manifest_lists_all_domains(self):
        manifest = json.loads((ROOT / "evaluation" / "golden_tasks" / "manifest.json").read_text(encoding="utf-8"))
        tasks = load_golden_tasks()
        task_domains = {t.domain for t in tasks}
        for domain in manifest["domains"]:
            assert domain in task_domains
            assert domain in DOMAIN_BENCHMARKS

    def test_benchmark_registry_covers_every_task(self):
        tasks = {t.id: t for t in load_golden_tasks()}
        registered = {task_id for bench in DOMAIN_BENCHMARKS.values() for task_id in bench.task_ids}
        assert registered == set(tasks.keys())

    def test_at_least_one_smoke_task_per_domain(self):
        by_domain: dict[str, list] = {}
        for task in load_golden_tasks():
            by_domain.setdefault(task.domain, []).append(task)
        for domain in list_domains():
            assert any("smoke" in t.tags for t in by_domain[domain]), f"{domain} missing smoke task"


@pytest.mark.golden
class TestGoldenTaskExecution:
    @pytest.mark.asyncio
    async def test_all_golden_tasks_pass_offline(self, golden_runner):
        results = await golden_runner.run_tasks(load_golden_tasks())
        failures = [r for r in results if not r.passed and not r.skipped]
        assert not failures, "\n".join(str(f) for f in failures)

    @pytest.mark.asyncio
    async def test_smoke_tag_subset(self, golden_runner):
        smoke = [t for t in load_golden_tasks() if "smoke" in t.tags]
        results = await golden_runner.run_tasks(smoke)
        assert all(r.passed for r in results)


@pytest.mark.golden
def test_cli_entrypoint_returns_zero(tmp_path):
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "evaluation" / "golden_runner.py"),
            "--skip-requires",
            "--work-dir",
            str(tmp_path / "cli_work"),
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
