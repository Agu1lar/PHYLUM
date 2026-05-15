# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Run intent profile golden benchmarks (Fase 1.5)."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "core") not in sys.path:
    sys.path.insert(0, str(ROOT / "core"))

from evaluation.intent_golden_models import IntentGoldenBenchmark, IntentGoldenResult


def load_intent_golden_benchmarks(golden_dir: Optional[Path] = None) -> List[IntentGoldenBenchmark]:
    base = golden_dir or GOLDEN_DIR
    benchmarks: List[IntentGoldenBenchmark] = []
    for path in sorted(base.rglob("*.json")):
        if path.name == "manifest.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        benchmarks.append(IntentGoldenBenchmark.from_dict(data, source_path=path))
    return sorted(benchmarks, key=lambda item: item.id)


def _params_contains(expected: Dict[str, Any], actual: Dict[str, Any]) -> Optional[str]:
    for key, value in expected.items():
        if actual.get(key) != value:
            return f"resolved params: expected {key}={value!r}, got {actual.get(key)!r}"
    return None


def run_intent_benchmark(benchmark: IntentGoldenBenchmark) -> IntentGoldenResult:
    from intent_classifier import build_resolved_tool_arguments, classify_user_intent
    from intent_profile_registry import IntentProfileRegistry
    from intent_routing import intent_fast_path_enabled, resolve_intent_routing

    expect = benchmark.expect
    classification = classify_user_intent(benchmark.user_text)
    routing = resolve_intent_routing(classification)

    if expect.accepted is not None and classification.accepted != expect.accepted:
        return IntentGoldenResult(
            benchmark=benchmark,
            passed=False,
            detail=f"accepted: expected {expect.accepted}, got {classification.accepted} ({classification.reason})",
        )

    if expect.profile_id is not None and classification.profile_id != expect.profile_id:
        return IntentGoldenResult(
            benchmark=benchmark,
            passed=False,
            detail=f"profile_id: expected {expect.profile_id!r}, got {classification.profile_id!r}",
        )

    if expect.min_confidence is not None and classification.confidence < expect.min_confidence:
        return IntentGoldenResult(
            benchmark=benchmark,
            passed=False,
            detail=f"confidence {classification.confidence:.3f} < {expect.min_confidence}",
        )

    if expect.routing_mode is not None:
        actual_mode = routing.get("mode")
        if expect.routing_mode == "fast_path" and not intent_fast_path_enabled():
            actual_mode = "agentic"
        if actual_mode != expect.routing_mode:
            return IntentGoldenResult(
                benchmark=benchmark,
                passed=False,
                detail=f"routing mode: expected {expect.routing_mode!r}, got {actual_mode!r}",
            )

    profile = classification.profile
    if expect.resolved_tool or expect.resolved_action or expect.resolved_params_contains:
        if profile is None:
            return IntentGoldenResult(
                benchmark=benchmark,
                passed=False,
                detail="no profile to verify resolved action",
            )
        if expect.resolved_tool and profile.default_action.tool != expect.resolved_tool:
            return IntentGoldenResult(
                benchmark=benchmark,
                passed=False,
                detail=f"resolved tool: expected {expect.resolved_tool!r}, got {profile.default_action.tool!r}",
            )
        if expect.resolved_action and profile.default_action.action != expect.resolved_action:
            return IntentGoldenResult(
                benchmark=benchmark,
                passed=False,
                detail=f"resolved action: expected {expect.resolved_action!r}, got {profile.default_action.action!r}",
            )
        resolved = build_resolved_tool_arguments(profile)
        err = _params_contains(expect.resolved_params_contains, resolved)
        if err:
            return IntentGoldenResult(benchmark=benchmark, passed=False, detail=err)

    if expect.forbidden_shell_commands and profile is not None:
        if profile.default_action.tool == "shell":
            command = str(build_resolved_tool_arguments(profile).get("command") or "")
            for forbidden in expect.forbidden_shell_commands:
                if forbidden.lower() in command.lower():
                    return IntentGoldenResult(
                        benchmark=benchmark,
                        passed=False,
                        detail=f"forbidden shell command {forbidden!r} in default_action",
                    )
        elif profile.default_action.tool != "office" and expect.forbidden_shell_commands:
            registry_profile = IntentProfileRegistry.default().get(profile.id)
            if registry_profile and registry_profile.default_action.tool == "shell":
                return IntentGoldenResult(
                    benchmark=benchmark,
                    passed=False,
                    detail="outlook-like task must not default to shell",
                )

    if benchmark.profile_id and classification.profile_id == benchmark.profile_id:
        registry_profile = IntentProfileRegistry.default().get(benchmark.profile_id)
        if registry_profile is None:
            return IntentGoldenResult(
                benchmark=benchmark,
                passed=False,
                detail=f"profile {benchmark.profile_id!r} missing from registry",
            )

    return IntentGoldenResult(benchmark=benchmark, passed=True)


def run_intent_benchmarks(
    benchmarks: Sequence[IntentGoldenBenchmark],
) -> List[IntentGoldenResult]:
    return [run_intent_benchmark(item) for item in benchmarks]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Intent profile golden benchmarks")
    parser.add_argument("--golden-dir", type=Path, default=GOLDEN_DIR)
    parser.add_argument("--tag", action="append", default=[], help="Only run benchmarks with tag")
    parser.add_argument("--id", action="append", default=[], help="Only run benchmark id")
    args = parser.parse_args(argv)

    benchmarks = load_intent_golden_benchmarks(args.golden_dir)
    if args.tag:
        tag_set = set(args.tag)
        benchmarks = [b for b in benchmarks if tag_set.intersection(b.tags)]
    if args.id:
        id_set = set(args.id)
        benchmarks = [b for b in benchmarks if b.id in id_set]

    started = time.perf_counter()
    results = run_intent_benchmarks(benchmarks)
    elapsed_ms = (time.perf_counter() - started) * 1000

    passed = sum(1 for r in results if r.passed)
    failed = [r for r in results if not r.passed]
    for result in results:
        print(result)
    print(f"\n{passed}/{len(results)} passed in {elapsed_ms:.0f}ms")
    if failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
