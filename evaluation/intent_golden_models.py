# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class IntentGoldenExpect:
    accepted: Optional[bool] = None
    profile_id: Optional[str] = None
    routing_mode: Optional[str] = None
    min_confidence: Optional[float] = None
    resolved_tool: Optional[str] = None
    resolved_action: Optional[str] = None
    resolved_params_contains: Dict[str, Any] = field(default_factory=dict)
    forbidden_shell_commands: List[str] = field(default_factory=list)
    max_llm_steps_note: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IntentGoldenExpect":
        profile_id = data.get("profile_id")
        if profile_id is None and "profile_id" not in data:
            profile_id = data.get("profile_id")
        return cls(
            accepted=data.get("accepted"),
            profile_id=data.get("profile_id"),
            routing_mode=data.get("routing_mode"),
            min_confidence=data.get("min_confidence"),
            resolved_tool=data.get("resolved_tool"),
            resolved_action=data.get("resolved_action"),
            resolved_params_contains=dict(data.get("resolved_params_contains") or {}),
            forbidden_shell_commands=list(data.get("forbidden_shell_commands") or []),
            max_llm_steps_note=data.get("max_llm_steps_note"),
        )


@dataclass
class IntentGoldenBenchmark:
    id: str
    profile_id: Optional[str]
    title: str
    user_text: str
    tags: List[str] = field(default_factory=list)
    expect: IntentGoldenExpect = field(default_factory=IntentGoldenExpect)
    source_path: Optional[Path] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any], *, source_path: Optional[Path] = None) -> "IntentGoldenBenchmark":
        return cls(
            id=data["id"],
            profile_id=data.get("profile_id"),
            title=data.get("title", data["id"]),
            user_text=data["user_text"],
            tags=list(data.get("tags") or []),
            expect=IntentGoldenExpect.from_dict(data.get("expect") or {}),
            source_path=source_path,
        )


@dataclass
class IntentGoldenResult:
    benchmark: IntentGoldenBenchmark
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        suffix = f" — {self.detail}" if self.detail else ""
        return f"[{status}] {self.benchmark.id}{suffix}"
