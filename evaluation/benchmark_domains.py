# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Domain benchmark registry — groups golden tasks by automation area."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence

from evaluation.golden_models import DomainBenchmarkResult, GoldenTaskResult
from evaluation.golden_runner import GoldenRunner, load_golden_tasks


@dataclass(frozen=True)
class DomainBenchmark:
    domain: str
    title: str
    description: str
    task_ids: tuple[str, ...]


DOMAIN_BENCHMARKS: Dict[str, DomainBenchmark] = {
    "filesystem": DomainBenchmark(
        domain="filesystem",
        title="Filesystem",
        description="Read, write, list, copy and search under allowed roots.",
        task_ids=(
            "fs-001-write-read",
            "fs-002-list-directory",
            "fs-003-copy-file",
        ),
    ),
    "documents": DomainBenchmark(
        domain="documents",
        title="Document intelligence",
        description="Inspect, extract, index and search local documents.",
        task_ids=(
            "doc-001-inspect-text",
            "doc-002-extract-text",
            "doc-003-index-search",
        ),
    ),
    "web_research": DomainBenchmark(
        domain="web_research",
        title="Web research",
        description="Link extraction and readonly fetch patterns for autonomous discovery.",
        task_ids=(
            "web-001-extract-links-fixture",
            "web-002-validate-search-query",
        ),
    ),
    "drivers": DomainBenchmark(
        domain="drivers",
        title="Drivers & printers",
        description="Driver candidate discovery and installer inspection.",
        task_ids=(
            "drv-001-find-driver-candidates",
            "drv-002-inspect-inf-fixture",
        ),
    ),
    "windows_ui": DomainBenchmark(
        domain="windows_ui",
        title="Windows UI",
        description="Dialog classification and UI automation guardrails (offline).",
        task_ids=(
            "ui-001-classify-print-dialog",
            "ui-002-classify-file-picker",
        ),
    ),
    "office": DomainBenchmark(
        domain="office",
        title="Office",
        description="Planner routing and Office tool validation (COM-free).",
        task_ids=(
            "off-001-planner-routes-word",
            "off-002-validation-missing-path",
        ),
    ),
    "browser": DomainBenchmark(
        domain="browser",
        title="Browser",
        description="Browser tool validation and schema coverage (no live browser).",
        task_ids=(
            "br-001-validation-missing-url",
            "br-002-planner-routes-open-page",
        ),
    ),
    "desktop": DomainBenchmark(
        domain="desktop",
        title="Desktop / Explorer",
        description="Explorer file ops and installer inspection on Windows.",
        task_ids=("desk-001-inspect-msi-fixture",),
    ),
}


def list_domains() -> List[str]:
    return sorted(DOMAIN_BENCHMARKS.keys())


def tasks_for_domain(domain: str) -> List[str]:
    bench = DOMAIN_BENCHMARKS.get(domain)
    return list(bench.task_ids) if bench else []


async def run_domain_benchmark(
    domain: str,
    *,
    runner: GoldenRunner,
    all_tasks: Sequence | None = None,
) -> DomainBenchmarkResult:
    catalog = {t.id: t for t in (all_tasks or load_golden_tasks())}
    bench = DOMAIN_BENCHMARKS[domain]
    selected = [catalog[task_id] for task_id in bench.task_ids if task_id in catalog]
    results = await runner.run_tasks(selected)
    passed = sum(1 for r in results if r.passed and not r.skipped)
    failed = sum(1 for r in results if not r.passed and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    return DomainBenchmarkResult(
        domain=domain,
        total=len(results),
        passed=passed,
        failed=failed,
        skipped=skipped,
        task_results=results,
    )


async def run_all_benchmarks(
    *,
    runner: GoldenRunner,
    domains: Sequence[str] | None = None,
) -> List[DomainBenchmarkResult]:
    target = list(domains) if domains else list_domains()
    catalog = load_golden_tasks()
    out: List[DomainBenchmarkResult] = []
    for domain in target:
        out.append(await run_domain_benchmark(domain, runner=runner, all_tasks=catalog))
    return out
