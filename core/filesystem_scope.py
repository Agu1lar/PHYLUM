# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Per-run filesystem scopes — limit agent I/O to an isolated directory plus declared roots."""
from __future__ import annotations

import logging
import tempfile
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

RUNS_ROOT = Path(tempfile.gettempdir()) / "agente_run_scopes"
MUTATING_ACTIONS = frozenset({
    "write", "delete", "move", "mkdir", "copy",
    "organize_directory", "organize_downloads", "organize_desktop",
    "clean_temp", "create_structure", "undo",
})
READ_ACTIONS = frozenset({
    "read", "list", "stat", "find_files", "detect_duplicates", "organize_directory",
})
NETWORK_READONLY_ACTIONS = frozenset({"read", "list", "stat", "find_files"})

_current_scope: ContextVar[Optional["RunFilesystemScope"]] = ContextVar("run_filesystem_scope", default=None)


@dataclass
class RunFilesystemScope:
    """Filesystem boundary for a single agent run."""

    request_id: str
    sandbox_dir: str
    read_roots: List[str] = field(default_factory=list)
    write_roots: List[str] = field(default_factory=list)
    extra_read_paths: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._read_resolved: Set[Path] = set()
        self._write_resolved: Set[Path] = set()
        for root in self.read_roots:
            self._read_resolved.add(Path(root).resolve())
        for root in self.write_roots:
            self._write_resolved.add(Path(root).resolve())
        sandbox = Path(self.sandbox_dir).resolve()
        self._read_resolved.add(sandbox)
        self._write_resolved.add(sandbox)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "sandbox_dir": self.sandbox_dir,
            "read_roots": list(self.read_roots),
            "write_roots": list(self.write_roots),
            "extra_read_paths": list(self.extra_read_paths),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RunFilesystemScope":
        return cls(
            request_id=data["request_id"],
            sandbox_dir=data["sandbox_dir"],
            read_roots=list(data.get("read_roots") or []),
            write_roots=list(data.get("write_roots") or []),
            extra_read_paths=list(data.get("extra_read_paths") or []),
        )

    def grant_read_path(self, path: str) -> None:
        resolved = str(Path(path).resolve())
        if resolved not in self.extra_read_paths:
            self.extra_read_paths.append(resolved)
        parent = Path(path).resolve().parent
        if parent not in self._read_resolved:
            self._read_resolved.add(parent)
            self.read_roots.append(str(parent))

    def grant_write_path(self, path: str) -> None:
        resolved = str(Path(path).resolve())
        parent = Path(path).resolve().parent
        if parent not in self._write_resolved:
            self._write_resolved.add(parent)
            self.write_roots.append(str(parent))

    def _path_under(self, path: Path, roots: Set[Path]) -> bool:
        try:
            resolved = path.resolve()
        except OSError:
            return False
        for root in roots:
            try:
                if root == resolved or root in resolved.parents:
                    return True
            except Exception:
                continue
        resolved_str = str(resolved)
        for extra in self.extra_read_paths:
            if resolved_str == extra or resolved_str.startswith(extra.rstrip("\\/") + "\\"):
                return True
        return False

    def allows(self, path_value: str, action: str) -> bool:
        if not path_value:
            return False
        path = Path(path_value)
        action = (action or "").lower()
        if action in MUTATING_ACTIONS:
            return self._path_under(path, self._write_resolved)
        if action in READ_ACTIONS or action in NETWORK_READONLY_ACTIONS:
            return self._path_under(path, self._read_resolved) or self._path_under(path, self._write_resolved)
        return self._path_under(path, self._write_resolved)

    def allows_dest(self, dest_value: str, action: str) -> bool:
        return self.allows(dest_value, action if action in MUTATING_ACTIONS else "write")


def create_run_filesystem_scope(
    request_id: str,
    *,
    inputs: Optional[Dict[str, Any]] = None,
) -> RunFilesystemScope:
    """Create an isolated sandbox directory and optional extra roots from run inputs."""
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    safe_id = request_id.replace("/", "_").replace("\\", "_") or uuid.uuid4().hex[:12]
    sandbox_dir = RUNS_ROOT / safe_id
    sandbox_dir.mkdir(parents=True, exist_ok=True)

    read_roots = [str(sandbox_dir)]
    write_roots = [str(sandbox_dir)]
    scope = RunFilesystemScope(
        request_id=request_id,
        sandbox_dir=str(sandbox_dir),
        read_roots=read_roots,
        write_roots=write_roots,
    )

    inputs = inputs or {}
    for extra in inputs.get("filesystem_read_roots") or []:
        scope.grant_read_path(str(extra))
    for extra in inputs.get("filesystem_write_roots") or []:
        scope.grant_write_path(str(extra))

    return scope


def get_run_filesystem_scope() -> Optional[RunFilesystemScope]:
    return _current_scope.get()


def bind_run_filesystem_scope(scope: Optional[RunFilesystemScope]) -> Token:
    return _current_scope.set(scope)


def reset_run_filesystem_scope(token: Token) -> None:
    _current_scope.reset(token)


def scope_from_state(state: Dict[str, Any]) -> Optional[RunFilesystemScope]:
    raw = state.get("filesystem_scope")
    if isinstance(raw, dict) and raw.get("request_id"):
        return RunFilesystemScope.from_dict(raw)
    request_id = state.get("request_id")
    if request_id:
        return create_run_filesystem_scope(request_id, inputs=state.get("inputs"))
    return None
