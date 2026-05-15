# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class GoldenStep:
    tool: str = ""
    action: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    expect: Dict[str, Any] = field(default_factory=dict)
    approval_granted: bool = False


@dataclass
class GoldenTask:
    id: str
    domain: str
    title: str
    tags: List[str] = field(default_factory=list)
    requires: List[str] = field(default_factory=list)
    setup: List[Dict[str, Any]] = field(default_factory=list)
    steps: List[GoldenStep] = field(default_factory=list)
    handler: Optional[str] = None
    handler_args: Dict[str, Any] = field(default_factory=dict)
    expect: Dict[str, Any] = field(default_factory=dict)
    source_path: Optional[Path] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any], *, source_path: Optional[Path] = None) -> "GoldenTask":
        steps = [
            GoldenStep(
                tool=step.get("tool", ""),
                action=step.get("action", ""),
                params=dict(step.get("params") or {}),
                expect=dict(step.get("expect") or {}),
                approval_granted=bool(step.get("approval_granted")),
            )
            for step in data.get("steps") or []
        ]
        return cls(
            id=data["id"],
            domain=data["domain"],
            title=data.get("title", data["id"]),
            tags=list(data.get("tags") or []),
            requires=list(data.get("requires") or []),
            setup=list(data.get("setup") or []),
            steps=steps,
            handler=data.get("handler"),
            handler_args=dict(data.get("handler_args") or {}),
            expect=dict(data.get("expect") or {}),
            source_path=source_path,
        )


@dataclass
class GoldenStepResult:
    step_index: int
    passed: bool
    detail: str = ""
    result: Optional[Dict[str, Any]] = None


@dataclass
class GoldenTaskResult:
    task: GoldenTask
    passed: bool
    skipped: bool = False
    skip_reason: str = ""
    duration_ms: float = 0.0
    step_results: List[GoldenStepResult] = field(default_factory=list)
    detail: str = ""

    def __str__(self) -> str:
        if self.skipped:
            return f"[SKIP] {self.task.id} — {self.skip_reason}"
        status = "PASS" if self.passed else "FAIL"
        suffix = f" — {self.detail}" if self.detail else ""
        return f"[{status}] {self.task.domain}/{self.task.id}{suffix}"


@dataclass
class DomainBenchmarkResult:
    domain: str
    total: int
    passed: int
    failed: int
    skipped: int
    task_results: List[GoldenTaskResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        runnable = self.total - self.skipped
        return (self.passed / runnable) if runnable else 1.0
