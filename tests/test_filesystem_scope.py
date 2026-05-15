# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

from pathlib import Path

import pytest

from filesystem_scope import (
    RunFilesystemScope,
    bind_run_filesystem_scope,
    create_run_filesystem_scope,
    get_run_filesystem_scope,
    reset_run_filesystem_scope,
)
from tool_filesystem import is_path_allowed_for_run


@pytest.fixture
def run_scope(tmp_path):
    scope = RunFilesystemScope(
        request_id="test-run",
        sandbox_dir=str(tmp_path / "sandbox"),
        read_roots=[str(tmp_path / "sandbox")],
        write_roots=[str(tmp_path / "sandbox")],
    )
    Path(scope.sandbox_dir).mkdir(parents=True, exist_ok=True)
    return scope


def test_scope_allows_paths_inside_sandbox(run_scope):
    inside = run_scope.sandbox_dir + "/out.txt"
    Path(inside).write_text("x", encoding="utf-8")
    assert run_scope.allows(inside, "read")
    assert run_scope.allows(inside, "write")


def test_scope_denies_outside_without_grant(run_scope, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    assert not run_scope.allows(str(outside), "read")


def test_grant_read_path(run_scope, tmp_path):
    file_path = tmp_path / "allowed" / "doc.txt"
    file_path.parent.mkdir(parents=True)
    file_path.write_text("ok", encoding="utf-8")
    run_scope.grant_read_path(str(file_path))
    assert run_scope.allows(str(file_path), "read")


def test_context_var_binding(run_scope):
    token = bind_run_filesystem_scope(run_scope)
    assert get_run_filesystem_scope() is run_scope
    reset_run_filesystem_scope(token)
    assert get_run_filesystem_scope() is None


def test_is_path_allowed_for_run_uses_scope(run_scope):
    inside = Path(run_scope.sandbox_dir) / "scoped.txt"
    inside.write_text("data", encoding="utf-8")
    token = bind_run_filesystem_scope(run_scope)
    try:
        assert is_path_allowed_for_run(str(inside), "read")
    finally:
        reset_run_filesystem_scope(token)


def test_create_run_scope_per_request_id(tmp_path):
    scope = create_run_filesystem_scope("req-abc", inputs={})
    assert scope.request_id == "req-abc"
    assert Path(scope.sandbox_dir).is_dir()
