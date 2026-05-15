# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import json
import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
EVAL_ROOT = Path(__file__).resolve().parent
TASKS_DIR = EVAL_ROOT / "golden_tasks"
FIXTURES_ROOT = EVAL_ROOT / "fixtures"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for sub in ("core", "tools", "agents", "nodes", "models", "providers", "safety", "memory", "execution", "persistence"):
    p = str(ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from evaluation.golden_models import GoldenStepResult, GoldenTask, GoldenTaskResult
from evaluation.handlers import RunContext


def load_golden_tasks(tasks_dir: Optional[Path] = None) -> List[GoldenTask]:
    base = tasks_dir or TASKS_DIR
    tasks: List[GoldenTask] = []
    for path in sorted(base.rglob("*.json")):
        if path.name == "manifest.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        tasks.append(GoldenTask.from_dict(data, source_path=path))
    return sorted(tasks, key=lambda t: (t.domain, t.id))


def _resolve_string(value: str, ctx: RunContext) -> str:
    if "{{tmpdir}}" in value:
        value = value.replace("{{tmpdir}}", str(ctx.work_dir).replace("\\", "/"))
    while "{{fixture:" in value:
        start = value.index("{{fixture:")
        end = value.index("}}", start)
        rel = value[start + len("{{fixture:") : end]
        resolved = str((ctx.fixtures_root / rel).resolve())
        value = value[:start] + resolved + value[end + 2 :]
    return value


def _resolve_value(value: Any, ctx: RunContext) -> Any:
    if isinstance(value, str):
        return _resolve_string(value, ctx)
    if isinstance(value, dict):
        return {k: _resolve_value(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_value(v, ctx) for v in value]
    return value


def _subset_match(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(_subset_match(expected[k], actual.get(k)) for k in expected)
    if isinstance(expected, str) and expected.startswith("contains:"):
        needle = expected[len("contains:") :]
        return needle in str(actual or "")
    return expected == actual


def _unwrap_tool_result(result: Dict[str, Any]) -> Dict[str, Any]:
    action_result = result.get("action_result")
    if isinstance(action_result, dict):
        return action_result
    return result


def _check_expect(expect: Dict[str, Any], result: Dict[str, Any], ctx: RunContext) -> Optional[str]:
    result = _unwrap_tool_result(result)
    expect = _resolve_value(expect, ctx)
    status = result.get("status") or ("succeeded" if result.get("success") else "failed")
    if "status" in expect and status != expect["status"]:
        return f"expected status {expect['status']}, got {status}"
    data = result.get("data") or result.get("details") or {}
    if "data_contains" in expect and not _subset_match(expect["data_contains"], data):
        return f"data mismatch: expected subset {expect['data_contains']}, got {data}"
    if "summary_contains" in expect:
        summary = str(result.get("summary") or result.get("message") or "")
        if expect["summary_contains"] not in summary:
            return f"summary missing {expect['summary_contains']!r}: {summary!r}"
    if "handler_result" in expect:
        if not _subset_match(expect["handler_result"], result):
            return f"handler result mismatch: expected {expect['handler_result']}, got {result}"
    if "field_equals" in expect:
        for field, expected_val in expect["field_equals"].items():
            actual_val = data.get(field) if field in data else result.get(field)
            if isinstance(expected_val, str) and expected_val.startswith("contains:"):
                needle = expected_val[len("contains:") :]
                if needle not in str(actual_val or ""):
                    return f"{field}: expected to contain {needle!r}, got {actual_val!r}"
            elif actual_val != expected_val:
                return f"{field}: expected {expected_val!r}, got {actual_val!r}"
    if "min_count" in expect:
        key = expect.get("count_field", "count")
        val = data.get(key) if key in data else result.get(key)
        count = len(val) if isinstance(val, list) else int(val or 0)
        if count < expect["min_count"]:
            return f"expected min_count {expect['min_count']} for {key}, got {count}"
    return None


def _should_skip(task: GoldenTask, ctx: RunContext) -> Optional[str]:
    if not ctx.skip_requires:
        return None
    for req in task.requires:
        if req == "windows" and ctx.platform != "windows":
            return "requires Windows"
        if req == "playwright" and not ctx.has_playwright:
            return "requires Playwright"
        if req == "office_com" and ctx.platform != "windows":
            return "requires Office COM (Windows)"
        if req == "network" and ctx.skip_requires:
            return "requires network (skipped in offline gate)"
    return None


async def _run_setup(setup: List[Dict[str, Any]], ctx: RunContext) -> None:
    for item in setup:
        kind = item["action"]
        if kind == "mkdir":
            path = Path(_resolve_string(item["path"], ctx))
            path.mkdir(parents=True, exist_ok=True)
        elif kind == "copy_fixture":
            src = ctx.fixtures_root / item["from"]
            dst = Path(_resolve_string(item["to"], ctx))
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        elif kind == "write_text":
            path = Path(_resolve_string(item["path"], ctx))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(item.get("content", ""), encoding=item.get("encoding", "utf-8"))
        else:
            raise ValueError(f"unknown setup action: {kind}")


class GoldenRunner:
    def __init__(self, *, work_dir: Path, skip_requires: bool = False, has_playwright: bool = False):
        self.ctx = RunContext(
            work_dir=work_dir,
            fixtures_root=FIXTURES_ROOT,
            platform=platform.system().lower(),
            skip_requires=skip_requires,
            has_playwright=has_playwright,
        )

    async def run_task(self, task: GoldenTask) -> GoldenTaskResult:
        skip = _should_skip(task, self.ctx)
        if skip:
            return GoldenTaskResult(task=task, passed=True, skipped=True, skip_reason=skip)

        started = time.perf_counter()
        step_results: List[GoldenStepResult] = []

        try:
            await _run_setup(task.setup, self.ctx)

            if task.handler:
                from evaluation.handlers import HANDLERS

                import inspect

                handler = HANDLERS.get(task.handler)
                if handler is None:
                    return GoldenTaskResult(
                        task=task,
                        passed=False,
                        detail=f"unknown handler {task.handler!r}",
                        duration_ms=(time.perf_counter() - started) * 1000,
                    )
                args = _resolve_value(task.handler_args, self.ctx)
                payload = handler(self.ctx, args)
                if inspect.isawaitable(payload):
                    payload = await payload
                err = _check_expect(task.expect, payload, self.ctx)
                if err:
                    return GoldenTaskResult(
                        task=task,
                        passed=False,
                        detail=err,
                        duration_ms=(time.perf_counter() - started) * 1000,
                    )
                return GoldenTaskResult(
                    task=task,
                    passed=True,
                    duration_ms=(time.perf_counter() - started) * 1000,
                )

            from tool_registry import ToolRegistry

            registry = ToolRegistry()
            for index, step in enumerate(task.steps):
                params = _resolve_value(step.params, self.ctx)
                raw = await registry.execute(
                    {
                        "id": f"{task.id}-step-{index}",
                        "tool": step.tool,
                        "action": step.action,
                        "params": params,
                        "approval_granted": step.approval_granted,
                    }
                )
                err = _check_expect(step.expect, raw, self.ctx)
                step_results.append(
                    GoldenStepResult(step_index=index, passed=err is None, detail=err or "", result=raw)
                )
                if err:
                    return GoldenTaskResult(
                        task=task,
                        passed=False,
                        detail=f"step {index}: {err}",
                        step_results=step_results,
                        duration_ms=(time.perf_counter() - started) * 1000,
                    )

            return GoldenTaskResult(
                task=task,
                passed=True,
                step_results=step_results,
                duration_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception as exc:
            return GoldenTaskResult(
                task=task,
                passed=False,
                detail=str(exc),
                step_results=step_results,
                duration_ms=(time.perf_counter() - started) * 1000,
            )

    async def run_tasks(self, tasks: Sequence[GoldenTask]) -> List[GoldenTaskResult]:
        return [await self.run_task(task) for task in tasks]

    async def run_domain(self, domain: str, tasks: Optional[Sequence[GoldenTask]] = None) -> List[GoldenTaskResult]:
        all_tasks = list(tasks) if tasks is not None else load_golden_tasks()
        domain_tasks = [t for t in all_tasks if t.domain == domain]
        return await self.run_tasks(domain_tasks)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    import asyncio

    parser = argparse.ArgumentParser(description="Run PHYLUM golden tasks and domain benchmarks")
    parser.add_argument("--domain", action="append", help="Limit to domain(s)")
    parser.add_argument("--tag", action="append", help="Limit to tag(s)")
    parser.add_argument("--skip-requires", action="store_true", help="Skip tasks needing Windows/Playwright/network")
    parser.add_argument("--work-dir", type=Path, default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)

    work_dir = args.work_dir or Path.cwd() / ".golden_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    tasks = load_golden_tasks()
    if args.domain:
        tasks = [t for t in tasks if t.domain in args.domain]
    if args.tag:
        tasks = [t for t in tasks if any(tag in t.tags for tag in args.tag)]

    runner = GoldenRunner(work_dir=work_dir, skip_requires=args.skip_requires)
    results = asyncio.run(runner.run_tasks(tasks))

    passed = sum(1 for r in results if r.passed and not r.skipped)
    failed = sum(1 for r in results if not r.passed and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)

    for result in results:
        print(result)

    print(f"\n{passed} passed, {failed} failed, {skipped} skipped (total {len(results)})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
