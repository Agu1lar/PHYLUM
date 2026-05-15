# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tests for workspace awareness and refactor guardrails."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from refactor_guardrails import (
    RefactorScope,
    bind_refactor_scope,
    check_mutation_allowed,
    check_proposed_changes,
    clear_refactor_scope,
    classify_path,
    detect_unrelated_touches,
    record_touch,
    reset_refactor_scope,
)
from workspace_awareness import detect_workspace_context


def _tmp_workspace() -> Path:
    d = Path(tempfile.mkdtemp(prefix="agente_ws_dev_"))
    (d / "package.json").write_text(
        json.dumps({"scripts": {"test": "pytest", "dev": "vite"}}),
        encoding="utf-8",
    )
    (d / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    (d / "core").mkdir()
    (d / "core" / "main.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    (d / "tests").mkdir()
    (d / "tests" / "test_main.py").write_text("def test_run():\n    assert True\n", encoding="utf-8")
    return d


class TestWorkspaceAwareness:
    def setup_method(self):
        self.ws = _tmp_workspace()

    def teardown_method(self):
        shutil.rmtree(str(self.ws), ignore_errors=True)

    def test_detect_context_structure(self):
        snap = detect_workspace_context(str(self.ws))
        data = snap.to_dict()
        assert data["workspace"] == str(self.ws.resolve())
        assert "npm_scripts" in [r["kind"] for r in data["task_runners"]]
        assert data["markers"]["has_package_json"] is True


class TestRefactorGuardrails:
    def setup_method(self):
        self.ws = _tmp_workspace()
        self.scope = RefactorScope(
            workspace=str(self.ws),
            target_files=["core/main.py"],
            allowed_globs=["core/**"],
            strict=True,
            allow_tests=True,
        )

    def teardown_method(self):
        clear_refactor_scope()
        shutil.rmtree(str(self.ws), ignore_errors=True)

    def test_in_scope_target(self):
        v = classify_path("core/main.py", self.scope)
        assert v.allowed and v.classification == "in_scope"

    def test_blocks_unrelated_file(self):
        v = classify_path("README.md", self.scope)
        assert not v.allowed and v.classification == "unrelated"

    def test_allows_test_for_target(self):
        v = classify_path("tests/test_main.py", self.scope)
        assert v.allowed

    def test_check_changes_blocks_out_of_scope(self):
        report = check_proposed_changes(
            [
                {"path": "core/main.py", "change_type": "modify"},
                {"path": "package.json", "change_type": "modify"},
            ],
            self.scope,
        )
        assert not report.ok
        assert len(report.blocked) >= 1

    def test_mutation_hook_blocks_write(self):
        token = bind_refactor_scope(self.scope)
        try:
            ok, reason = check_mutation_allowed(str(self.ws / "package.json"), "write")
            assert not ok
            assert "guardrail" in reason.lower() or "scope" in reason.lower()
            ok2, _ = check_mutation_allowed(str(self.ws / "core" / "main.py"), "write")
            assert ok2
        finally:
            reset_refactor_scope(token)

    def test_audit_detects_unrelated_touch(self):
        token = bind_refactor_scope(self.scope)
        try:
            record_touch(str(self.ws), str(self.ws / "core" / "main.py"))
            record_touch(str(self.ws), str(self.ws / "package.json"))
            report = detect_unrelated_touches(self.scope)
            assert not report.ok
        finally:
            reset_refactor_scope(token)


@pytest.mark.asyncio
async def test_workspace_dev_tool_detect_context():
    from tool_workspace_dev import WorkspaceDevTool, WorkspaceDevRequest

    ws = _tmp_workspace()
    try:
        tool = WorkspaceDevTool()
        result = await tool._run(WorkspaceDevRequest(action="detect_context", workspace=str(ws)))
        assert result.status == "succeeded"
        assert result.data.get("workspace")
    finally:
        shutil.rmtree(str(ws), ignore_errors=True)


@pytest.mark.asyncio
async def test_workspace_dev_tool_set_and_validate():
    from tool_workspace_dev import WorkspaceDevTool, WorkspaceDevRequest

    ws = _tmp_workspace()
    try:
        tool = WorkspaceDevTool()
        await tool._run(WorkspaceDevRequest(
            action="set_scope",
            workspace=str(ws),
            target_files=["core/main.py"],
            allowed_globs=["core/**"],
        ))
        bad = await tool._run(WorkspaceDevRequest(
            action="validate_path",
            path="package.json",
        ))
        assert bad.status == "failed"
        good = await tool._run(WorkspaceDevRequest(
            action="validate_path",
            path="core/main.py",
        ))
        assert good.status == "succeeded"
        await tool._run(WorkspaceDevRequest(action="clear_scope"))
    finally:
        shutil.rmtree(str(ws), ignore_errors=True)
