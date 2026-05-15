# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Skill evaluation — minimum tests before a skill is agent-available."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from skill_manifest import PermissionKind, SkillManifest

logger = logging.getLogger(__name__)


def run_async_safe(coro):
    """Run coroutine from sync or async callers."""
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()

TESTS_FILENAME = "tests.json"
DEFAULT_MIN_TESTS = 1
EVALUATION_PASSED = "passed"
EVALUATION_FAILED = "failed"
EVALUATION_PENDING = "pending"


@dataclass
class SkillTestCase:
    name: str
    params: Dict[str, Any] = field(default_factory=dict)
    expect: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkillTestCase":
        return cls(
            name=str(data.get("name") or "unnamed"),
            params=dict(data.get("params") or {}),
            expect=dict(data.get("expect") or {}),
        )


@dataclass
class SkillTestResult:
    name: str
    passed: bool
    detail: str = ""
    output: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail, "output": self.output}


@dataclass
class SkillEvaluationReport:
    skill_name: str
    status: str
    min_tests: int
    passed_count: int
    failed_count: int
    results: List[SkillTestResult] = field(default_factory=list)
    evaluated_at: str = ""

    @property
    def passed(self) -> bool:
        return self.status == EVALUATION_PASSED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "status": self.status,
            "min_tests": self.min_tests,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "agent_available": self.passed,
            "results": [r.to_dict() for r in self.results],
            "evaluated_at": self.evaluated_at,
        }


def load_skill_tests(skill_dir: Path) -> tuple[int, List[SkillTestCase]]:
    path = skill_dir / TESTS_FILENAME
    if not path.exists():
        return DEFAULT_MIN_TESTS, []
    data = json.loads(path.read_text(encoding="utf-8"))
    min_tests = int(data.get("min_tests") or DEFAULT_MIN_TESTS)
    cases = [SkillTestCase.from_dict(t) for t in data.get("tests") or []]
    return min_tests, cases


def save_skill_tests(skill_dir: Path, cases: List[SkillTestCase], *, min_tests: Optional[int] = None) -> Path:
    path = skill_dir / TESTS_FILENAME
    payload = {
        "min_tests": min_tests if min_tests is not None else max(DEFAULT_MIN_TESTS, len(cases)),
        "tests": [
            {"name": c.name, "params": c.params, "expect": c.expect}
            for c in cases
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _subset_match(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(_subset_match(expected[k], actual.get(k)) for k in expected)
    return expected == actual


def check_expectation(expect: Dict[str, Any], *, ok: bool, output: Any, error: Optional[str]) -> tuple[bool, str]:
    if expect.get("ok") is True and not ok:
        return False, f"expected ok=True, got error: {error}"
    if expect.get("ok") is False and ok:
        return False, "expected failure but skill succeeded"
    if "output" in expect and not _subset_match(expect["output"], output):
        return False, f"output mismatch: expected {expect['output']!r}, got {output!r}"
    if "output_has_keys" in expect:
        if not isinstance(output, dict):
            return False, "expected dict output"
        missing = [k for k in expect["output_has_keys"] if k not in output]
        if missing:
            return False, f"missing keys: {missing}"
    return True, ""


class SkillEvaluator:
    """Runs declared tests against a skill via SkillRunner."""

    def __init__(self, registry, *, use_subprocess: bool = False):
        self.registry = registry
        self.use_subprocess = use_subprocess

    async def evaluate(self, skill_name: str) -> SkillEvaluationReport:
        from skill_runner import SkillRunner

        manifest = self.registry.get(skill_name)
        if manifest is None:
            return SkillEvaluationReport(
                skill_name=skill_name,
                status=EVALUATION_FAILED,
                min_tests=DEFAULT_MIN_TESTS,
                passed_count=0,
                failed_count=1,
                results=[SkillTestResult("load", False, "skill not found")],
                evaluated_at=datetime.now(timezone.utc).isoformat(),
            )

        skill_dir = self.registry.skills_dir / skill_name
        min_tests, cases = load_skill_tests(skill_dir)

        if len(cases) < min_tests:
            report = SkillEvaluationReport(
                skill_name=skill_name,
                status=EVALUATION_FAILED,
                min_tests=min_tests,
                passed_count=0,
                failed_count=1,
                results=[
                    SkillTestResult(
                        "coverage",
                        False,
                        f"requires at least {min_tests} test(s), found {len(cases)}",
                    )
                ],
                evaluated_at=datetime.now(timezone.utc).isoformat(),
            )
            self.registry.set_evaluation_status(skill_name, report)
            return report

        runner = SkillRunner(
            registry=self.registry,
            granted_capabilities=set(manifest.permissions) | {PermissionKind.SANDBOX_PYTHON},
            use_subprocess=self.use_subprocess,
        )

        results: List[SkillTestResult] = []
        for case in cases:
            exec_result = await runner.execute(
                skill_name,
                params=case.params,
                skip_integrity_check=True,
                skip_evaluation_check=True,
            )
            passed, detail = check_expectation(
                case.expect,
                ok=exec_result.ok,
                output=exec_result.output,
                error=exec_result.error,
            )
            results.append(
                SkillTestResult(
                    name=case.name,
                    passed=passed,
                    detail=detail or exec_result.error or "",
                    output=exec_result.output,
                )
            )

        passed_count = sum(1 for r in results if r.passed)
        failed_count = len(results) - passed_count
        status = EVALUATION_PASSED if failed_count == 0 and passed_count >= min_tests else EVALUATION_FAILED

        report = SkillEvaluationReport(
            skill_name=skill_name,
            status=status,
            min_tests=min_tests,
            passed_count=passed_count,
            failed_count=failed_count,
            results=results,
            evaluated_at=datetime.now(timezone.utc).isoformat(),
        )
        self.registry.set_evaluation_status(skill_name, report)
        return report
