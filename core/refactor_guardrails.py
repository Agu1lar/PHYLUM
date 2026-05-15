# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Refactor guardrails — scope edits and detect accidental changes to unrelated files."""
from __future__ import annotations

import fnmatch
import logging
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

MUTATING_FS_ACTIONS = frozenset({
    "write", "delete", "move", "copy", "mkdir",
    "organize_directory", "clean_temp", "create_structure",
})

_current_scope: ContextVar[Optional["RefactorScope"]] = ContextVar("refactor_guardrail_scope", default=None)
_session_touches: Dict[str, Set[str]] = {}


@dataclass
class RefactorScope:
    """Allowed edit boundary for a refactor session."""

    workspace: str
    target_files: List[str] = field(default_factory=list)
    allowed_globs: List[str] = field(default_factory=list)
    allowed_directories: List[str] = field(default_factory=list)
    description: str = ""
    strict: bool = True
    allow_tests: bool = True
    allow_same_package: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace": self.workspace,
            "target_files": list(self.target_files),
            "allowed_globs": list(self.allowed_globs),
            "allowed_directories": list(self.allowed_directories),
            "description": self.description,
            "strict": self.strict,
            "allow_tests": self.allow_tests,
            "allow_same_package": self.allow_same_package,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RefactorScope":
        return cls(
            workspace=data["workspace"],
            target_files=list(data.get("target_files") or []),
            allowed_globs=list(data.get("allowed_globs") or []),
            allowed_directories=list(data.get("allowed_directories") or []),
            description=data.get("description") or "",
            strict=bool(data.get("strict", True)),
            allow_tests=bool(data.get("allow_tests", True)),
            allow_same_package=bool(data.get("allow_same_package", True)),
        )


@dataclass
class PathVerdict:
    path: str
    allowed: bool
    classification: str  # in_scope, related, unrelated
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "allowed": self.allowed,
            "classification": self.classification,
            "reason": self.reason,
        }


@dataclass
class GuardrailReport:
    ok: bool
    blocked: List[PathVerdict] = field(default_factory=list)
    warnings: List[PathVerdict] = field(default_factory=list)
    allowed: List[PathVerdict] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "blocked": [v.to_dict() for v in self.blocked],
            "warnings": [v.to_dict() for v in self.warnings],
            "allowed": [v.to_dict() for v in self.allowed],
            "blocked_count": len(self.blocked),
            "warning_count": len(self.warnings),
        }


def bind_refactor_scope(scope: RefactorScope) -> Token:
    return _current_scope.set(scope)


def get_refactor_scope() -> Optional[RefactorScope]:
    return _current_scope.get()


def reset_refactor_scope(token: Token) -> None:
    _current_scope.reset(token)


def clear_refactor_scope() -> None:
    _current_scope.set(None)


def _workspace_root(scope: RefactorScope) -> Path:
    return Path(scope.workspace).resolve()


def _normalize_path(path: str, workspace: Path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = workspace / p
    return p.resolve()


def _relative_posix(path: Path, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace).as_posix()
    except ValueError:
        return path.as_posix()


def _is_test_path(rel: str) -> bool:
    lower = rel.lower().replace("\\", "/")
    return (
        "/tests/" in f"/{lower}/"
        or lower.startswith("tests/")
        or lower.endswith("_test.py")
        or lower.startswith("test_")
        or "/__tests__/" in f"/{lower}/"
    )


def _same_package(rel_a: str, rel_b: str) -> bool:
    pa = Path(rel_a).parent.as_posix()
    pb = Path(rel_b).parent.as_posix()
    return pa == pb and pa not in ("", ".")


def _matches_glob(rel: str, patterns: List[str]) -> bool:
    for pat in patterns:
        p = pat.replace("\\", "/")
        if fnmatch.fnmatch(rel, p):
            return True
        if not p.startswith("**/") and fnmatch.fnmatch(rel, f"**/{p}"):
            return True
    return False


def _matches_directory(rel: str, directories: List[str], workspace: Path) -> bool:
    rel_norm = rel.replace("\\", "/")
    for d in directories:
        d_norm = d.replace("\\", "/").strip("/")
        if rel_norm == d_norm or rel_norm.startswith(d_norm + "/"):
            return True
    return False


def _target_set(scope: RefactorScope, workspace: Path) -> Set[str]:
    return {_relative_posix(_normalize_path(t, workspace), workspace) for t in scope.target_files}


def classify_path(path: str, scope: RefactorScope) -> PathVerdict:
    workspace = _workspace_root(scope)
    resolved = _normalize_path(path, workspace)
    rel = _relative_posix(resolved, workspace)
    targets = _target_set(scope, workspace)

    if rel in targets:
        return PathVerdict(path=rel, allowed=True, classification="in_scope", reason="explicit target file")

    if scope.allowed_globs and _matches_glob(rel, scope.allowed_globs):
        return PathVerdict(path=rel, allowed=True, classification="in_scope", reason="matches allowed_glob")

    if scope.allowed_directories and _matches_directory(rel, scope.allowed_directories, workspace):
        return PathVerdict(path=rel, allowed=True, classification="in_scope", reason="under allowed directory")

    if scope.allow_tests and _is_test_path(rel) and targets:
        for t in targets:
            stem = Path(t).stem.replace("test_", "").replace("_test", "")
            if stem and stem in rel:
                return PathVerdict(path=rel, allowed=True, classification="related", reason="test file for target module")

    if scope.allow_same_package and targets:
        for t in targets:
            if _same_package(rel, t):
                return PathVerdict(path=rel, allowed=True, classification="related", reason="same package as target")

    try:
        resolved.relative_to(workspace)
        inside = True
    except ValueError:
        inside = False

    if not inside:
        return PathVerdict(
            path=rel,
            allowed=False,
            classification="unrelated",
            reason="path outside workspace",
        )

    if scope.strict:
        return PathVerdict(
            path=rel,
            allowed=False,
            classification="unrelated",
            reason="outside refactor scope (strict mode)",
        )

    return PathVerdict(
        path=rel,
        allowed=True,
        classification="related",
        reason="inside workspace but outside explicit scope (non-strict)",
    )


def validate_paths(paths: List[str], scope: RefactorScope) -> GuardrailReport:
    report = GuardrailReport(ok=True)
    for path in paths:
        verdict = classify_path(path, scope)
        if verdict.allowed:
            report.allowed.append(verdict)
            if verdict.classification == "related":
                report.warnings.append(verdict)
        else:
            report.blocked.append(verdict)
            report.ok = False
    return report


def check_proposed_changes(
    changes: List[Dict[str, Any]],
    scope: RefactorScope,
) -> GuardrailReport:
    """Validate a list of proposed file changes [{path, change_type}, ...]."""
    paths: List[str] = []
    for ch in changes:
        p = ch.get("path") or ch.get("dest") or ""
        if p:
            paths.append(p)
        dest = ch.get("dest")
        if dest and ch.get("change_type") in ("move", "rename", "copy"):
            paths.append(dest)
    return validate_paths(paths, scope)


def check_mutation_allowed(
    path: Optional[str],
    action: str,
    *,
    dest: Optional[str] = None,
) -> Tuple[bool, str]:
    """Called from filesystem tool before mutating operations."""
    if action not in MUTATING_FS_ACTIONS:
        return True, ""
    scope = get_refactor_scope()
    if scope is None:
        return True, ""

    paths = [p for p in (path, dest) if p]
    report = validate_paths(paths, scope)
    if report.ok and not report.blocked:
        for p in paths:
            record_touch(scope.workspace, p)
        return True, ""

    if report.blocked:
        v = report.blocked[0]
        return False, f"Refactor guardrail blocked {action} on '{v.path}': {v.reason}"
    return True, ""


def record_touch(workspace: str, path: str) -> None:
    ws = str(Path(workspace).resolve())
    rel = _relative_posix(_normalize_path(path, Path(ws)), Path(ws))
    _session_touches.setdefault(ws, set()).add(rel)


def get_session_touches(workspace: str) -> List[str]:
    ws = str(Path(workspace).resolve())
    return sorted(_session_touches.get(ws, set()))


def detect_unrelated_touches(
    scope: RefactorScope,
    *,
    extra_paths: Optional[List[str]] = None,
) -> GuardrailReport:
    """Compare session touches + extra paths against scope; flag unrelated edits."""
    workspace = _workspace_root(scope)
    touched = set(get_session_touches(str(workspace)))
    if extra_paths:
        for p in extra_paths:
            touched.add(_relative_posix(_normalize_path(p, workspace), workspace))
    return validate_paths(sorted(touched), scope)
