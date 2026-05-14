"""Test suite for Semantic Verification.

Tests:
  - GoalVerifier: success/failure paths, deferred actions, issue detection, mutation evidence
  - SemanticValidator: mutation evidence, inspection data, summary presence
  - PostconditionChecker: file write/copy/move/delete/mkdir, no-rule fallback, error resilience
  - SemanticVerifier facade: combined pipeline, postcondition overrides, semantic overrides
  - Postcondition decorator: custom rule registration
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Dict

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

from semantic_verifier import (
    GoalVerifier,
    PostconditionChecker,
    SemanticValidator,
    SemanticVerifier,
    postcondition,
    _POSTCONDITION_REGISTRY,
)


# ─── Helpers ─────────────────────────────────────────────────────────

def _task(tool: str = "filesystem", action: str = "write", **params) -> Dict[str, Any]:
    return {"tool": tool, "action": action, "params": params, "id": "test-1"}


def _result(
    status: str = "succeeded",
    semantic_type: str = "mutation",
    *,
    data: Dict[str, Any] | None = None,
    target: Dict[str, Any] | None = None,
    effects: Dict[str, Any] | None = None,
    issue: Dict[str, Any] | None = None,
    summary: str = "done",
) -> Dict[str, Any]:
    ar: Dict[str, Any] = {
        "status": status,
        "summary": summary,
        "tool": "filesystem",
        "action": "write",
        "semantic_type": semantic_type,
        "target": target or {},
        "data": data or {},
        "effects": effects or {},
    }
    if issue:
        ar["issue"] = issue
    return {"action_result": ar, "tool_result": {"success": status == "succeeded"}}


# ═══════════════════════════════════════════════════════════════════════
# GoalVerifier
# ═══════════════════════════════════════════════════════════════════════


class TestGoalVerifier:
    def setup_method(self):
        self.gv = GoalVerifier()

    def test_failed_action_not_satisfied(self):
        result = self.gv.verify(_task(), {"status": "failed", "summary": "err"})
        assert result["satisfied"] is False
        assert result["confidence"] == 0.0

    def test_succeeded_mutation_with_effects(self):
        result = self.gv.verify(
            _task(),
            {"status": "succeeded", "semantic_type": "mutation", "effects": {"changed": True}, "summary": "ok"},
        )
        assert result["satisfied"] is True
        assert result["confidence"] >= 0.90

    def test_succeeded_inspection_with_data(self):
        result = self.gv.verify(
            _task("filesystem", "read"),
            {"status": "succeeded", "semantic_type": "inspection", "data": {"content": "hello"}, "summary": "ok"},
        )
        assert result["satisfied"] is True
        assert result["confidence"] >= 0.80

    def test_succeeded_inspection_without_data(self):
        result = self.gv.verify(
            _task("filesystem", "read"),
            {"status": "succeeded", "semantic_type": "inspection", "data": {}, "summary": "ok"},
        )
        assert result["satisfied"] is True
        assert result["confidence"] < 0.85

    def test_desktop_open_app_is_deferred(self):
        result = self.gv.verify(
            _task("desktop", "open_app"),
            {"status": "succeeded", "summary": "launched"},
        )
        assert result["satisfied"] is False
        assert result["strategy"] == "verify_window_or_process"
        assert len(result["recommended_followups"]) > 0

    def test_desktop_open_path_is_deferred(self):
        result = self.gv.verify(
            _task("desktop", "open_path"),
            {"status": "succeeded", "summary": "opened"},
        )
        assert result["satisfied"] is False

    def test_office_word_create_document_deferred(self):
        result = self.gv.verify(
            _task("office", "word_create_document"),
            {"status": "succeeded", "summary": "created"},
        )
        assert result["satisfied"] is False
        assert "verify_file_created" in result["strategy"]

    def test_office_word_export_pdf_deferred(self):
        result = self.gv.verify(
            _task("office", "word_export_pdf"),
            {"status": "succeeded", "summary": "exported"},
        )
        assert result["satisfied"] is False

    def test_sandbox_run_python_deferred(self):
        result = self.gv.verify(
            _task("sandbox", "run_python"),
            {"status": "succeeded", "summary": "ok"},
        )
        assert result["satisfied"] is False
        assert result["strategy"] == "verify_script_output"

    def test_issue_present_lowers_satisfaction(self):
        result = self.gv.verify(
            _task(),
            {
                "status": "succeeded",
                "semantic_type": "mutation",
                "effects": {"changed": True},
                "issue": {"kind": "partial_failure", "message": "1 of 3 items failed"},
                "summary": "partial",
            },
        )
        assert result["satisfied"] is False
        assert "issue" in result["strategy"]

    def test_generic_succeeded_is_satisfied(self):
        result = self.gv.verify(
            _task("memory", "set"),
            {"status": "succeeded", "semantic_type": "mutation", "summary": "saved"},
        )
        assert result["satisfied"] is True
        assert result["confidence"] == 0.80


# ═══════════════════════════════════════════════════════════════════════
# SemanticValidator
# ═══════════════════════════════════════════════════════════════════════


class TestSemanticValidator:
    def setup_method(self):
        self.sv = SemanticValidator()

    def test_non_success_deferred(self):
        result = self.sv.validate(_task(), {"status": "failed"})
        assert result["valid"] is True

    def test_mutation_with_evidence_valid(self):
        result = self.sv.validate(
            _task(),
            {"status": "succeeded", "semantic_type": "mutation", "effects": {"changed": True}, "summary": "ok"},
        )
        assert result["valid"] is True

    def test_mutation_without_evidence_invalid(self):
        result = self.sv.validate(
            _task(),
            {"status": "succeeded", "semantic_type": "mutation", "effects": {}, "target": {}, "summary": "ok"},
        )
        assert result["valid"] is False
        assert any(c["check"] == "mutation_evidence" for c in result["checks"])

    def test_mutation_with_target_valid(self):
        result = self.sv.validate(
            _task(),
            {"status": "succeeded", "semantic_type": "mutation", "effects": {}, "target": {"path": "/tmp/x"}, "summary": "ok"},
        )
        assert result["valid"] is True

    def test_inspection_with_data_valid(self):
        result = self.sv.validate(
            _task("filesystem", "read"),
            {"status": "succeeded", "semantic_type": "inspection", "data": {"content": "hello"}, "summary": "ok"},
        )
        assert result["valid"] is True

    def test_inspection_without_data_or_summary_invalid(self):
        result = self.sv.validate(
            _task("filesystem", "read"),
            {"status": "succeeded", "semantic_type": "inspection", "data": {}, "target": {}, "summary": "ok"},
        )
        assert result["valid"] is False

    def test_empty_summary_flagged(self):
        result = self.sv.validate(
            _task(),
            {"status": "succeeded", "semantic_type": "mutation", "effects": {"changed": True}, "summary": ""},
        )
        assert result["valid"] is False
        assert any(c["check"] == "summary_present" for c in result["checks"])


# ═══════════════════════════════════════════════════════════════════════
# PostconditionChecker
# ═══════════════════════════════════════════════════════════════════════


class TestPostconditionChecker:
    def setup_method(self):
        self.pc = PostconditionChecker()

    @pytest.mark.asyncio
    async def test_write_file_exists(self, tmp_path):
        target = tmp_path / "output.txt"
        target.write_text("hello")
        task = _task("filesystem", "write", path=str(target))
        result = _result()
        check = await self.pc.check(task, result, effect_kind="write_file")
        assert check["checked"] is True
        assert check["passed"] is True
        assert check["size"] > 0

    @pytest.mark.asyncio
    async def test_write_file_missing(self, tmp_path):
        target = tmp_path / "nonexistent.txt"
        task = _task("filesystem", "write", path=str(target))
        result = _result()
        check = await self.pc.check(task, result, effect_kind="write_file")
        assert check["checked"] is True
        assert check["passed"] is False

    @pytest.mark.asyncio
    async def test_copy_file_dest_exists(self, tmp_path):
        dest = tmp_path / "copy.txt"
        dest.write_text("copy")
        task = _task("filesystem", "copy", dest=str(dest))
        result = _result()
        check = await self.pc.check(task, result, effect_kind="copy_file")
        assert check["passed"] is True

    @pytest.mark.asyncio
    async def test_copy_file_dest_missing(self, tmp_path):
        dest = tmp_path / "missing.txt"
        task = _task("filesystem", "copy", dest=str(dest))
        result = _result()
        check = await self.pc.check(task, result, effect_kind="copy_file")
        assert check["passed"] is False

    @pytest.mark.asyncio
    async def test_move_file_verified(self, tmp_path):
        dest = tmp_path / "moved.txt"
        dest.write_text("moved")
        src = tmp_path / "original.txt"
        task = _task("filesystem", "move", path=str(src), dest=str(dest))
        result = _result()
        check = await self.pc.check(task, result, effect_kind="move_file")
        assert check["passed"] is True

    @pytest.mark.asyncio
    async def test_move_file_source_still_exists(self, tmp_path):
        dest = tmp_path / "moved.txt"
        dest.write_text("moved")
        src = tmp_path / "original.txt"
        src.write_text("still here")
        task = _task("filesystem", "move", path=str(src), dest=str(dest))
        result = _result()
        check = await self.pc.check(task, result, effect_kind="move_file")
        assert check["passed"] is False

    @pytest.mark.asyncio
    async def test_delete_file_removed(self, tmp_path):
        target = tmp_path / "to_delete.txt"
        task = _task("filesystem", "delete", path=str(target))
        result = _result()
        check = await self.pc.check(task, result, effect_kind="delete_file")
        assert check["passed"] is True

    @pytest.mark.asyncio
    async def test_delete_file_still_exists(self, tmp_path):
        target = tmp_path / "to_delete.txt"
        target.write_text("oops")
        task = _task("filesystem", "delete", path=str(target))
        result = _result()
        check = await self.pc.check(task, result, effect_kind="delete_file")
        assert check["passed"] is False

    @pytest.mark.asyncio
    async def test_mkdir_exists(self, tmp_path):
        target = tmp_path / "newdir"
        target.mkdir()
        task = _task("filesystem", "mkdir", path=str(target))
        result = _result()
        check = await self.pc.check(task, result, effect_kind="create_directory")
        assert check["passed"] is True

    @pytest.mark.asyncio
    async def test_mkdir_missing(self, tmp_path):
        target = tmp_path / "nodir"
        task = _task("filesystem", "mkdir", path=str(target))
        result = _result()
        check = await self.pc.check(task, result, effect_kind="create_directory")
        assert check["passed"] is False

    @pytest.mark.asyncio
    async def test_no_rule_returns_not_checked(self):
        task = _task("memory", "set")
        result = _result()
        check = await self.pc.check(task, result, effect_kind="memory_set")
        assert check["checked"] is False

    @pytest.mark.asyncio
    async def test_failed_action_skips_postcondition(self, tmp_path):
        target = tmp_path / "output.txt"
        target.write_text("hello")
        task = _task("filesystem", "write", path=str(target))
        result = _result(status="failed")
        check = await self.pc.check(task, result, effect_kind="write_file")
        assert check["checked"] is False

    def test_registered_effects(self):
        effects = self.pc.registered_effects
        assert "write_file" in effects
        assert "copy_file" in effects
        assert "move_file" in effects
        assert "delete_file" in effects
        assert "create_directory" in effects

    @pytest.mark.asyncio
    async def test_exception_in_postcondition_handled(self):
        async def broken_check(task, result, context):
            raise RuntimeError("probe failed")

        pc = PostconditionChecker(registry={"test_broken": broken_check})
        check = await pc.check(_task(), _result(), effect_kind="test_broken")
        assert check["checked"] is True
        assert check["passed"] is False
        assert "probe failed" in check["reason"]


# ═══════════════════════════════════════════════════════════════════════
# Postcondition decorator
# ═══════════════════════════════════════════════════════════════════════


class TestPostconditionDecorator:
    def test_decorator_registers_rule(self):
        @postcondition("test_custom_effect")
        async def _custom_check(task, result, context):
            return {"passed": True, "reason": "custom", "check": "custom"}

        assert "test_custom_effect" in _POSTCONDITION_REGISTRY
        del _POSTCONDITION_REGISTRY["test_custom_effect"]


# ═══════════════════════════════════════════════════════════════════════
# SemanticVerifier (full pipeline)
# ═══════════════════════════════════════════════════════════════════════


class TestSemanticVerifier:
    def setup_method(self):
        self.sv = SemanticVerifier()

    @pytest.mark.asyncio
    async def test_full_pipeline_succeeded_mutation_with_postcondition(self, tmp_path):
        target = tmp_path / "out.txt"
        target.write_text("hello")
        task = _task("filesystem", "write", path=str(target))
        result = _result(effects={"changed": True}, target={"path": str(target)})
        goal = await self.sv.verify(task, result)
        assert goal["satisfied"] is True
        assert goal["confidence"] >= 0.90
        assert goal["postcondition"]["checked"] is True
        assert goal["postcondition"]["passed"] is True
        assert goal["semantic_validation"]["valid"] is True

    @pytest.mark.asyncio
    async def test_postcondition_failure_overrides_goal(self, tmp_path):
        target = tmp_path / "missing.txt"
        task = _task("filesystem", "write", path=str(target))
        result = _result(effects={"changed": True}, target={"path": str(target)})
        goal = await self.sv.verify(task, result)
        assert goal["satisfied"] is False
        assert goal["strategy"] == "postcondition_failed"
        assert goal["confidence"] <= 0.35

    @pytest.mark.asyncio
    async def test_semantic_validation_failure_overrides_goal(self):
        task = _task("filesystem", "write")
        result = _result(effects={}, target={}, summary="")
        goal = await self.sv.verify(task, result)
        assert goal["satisfied"] is False
        assert "semantic_validation" in goal["strategy"] or "postcondition" in goal["strategy"]

    @pytest.mark.asyncio
    async def test_failed_action_passes_through(self):
        task = _task("filesystem", "write")
        result = _result(status="failed")
        goal = await self.sv.verify(task, result)
        assert goal["satisfied"] is False
        assert goal["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_inspection_without_postcondition(self):
        task = _task("filesystem", "read")
        result = _result(
            semantic_type="inspection",
            data={"content": "file content here"},
            summary="File read successfully with content",
        )
        goal = await self.sv.verify(task, result)
        assert goal["satisfied"] is True
        assert goal["postcondition"]["checked"] is False
        assert goal["semantic_validation"]["valid"] is True

    @pytest.mark.asyncio
    async def test_deferred_action_not_satisfied(self):
        task = _task("desktop", "open_app")
        result = _result(semantic_type="command", summary="launched notepad")
        goal = await self.sv.verify(task, result)
        assert goal["satisfied"] is False
        assert "verify_window" in goal["strategy"]

    @pytest.mark.asyncio
    async def test_result_includes_all_verification_layers(self):
        task = _task("memory", "set")
        result = _result(effects={}, target={"key": "test"}, summary="saved")
        goal = await self.sv.verify(task, result)
        assert "semantic_validation" in goal
        assert "postcondition" in goal
        assert "satisfied" in goal
        assert "confidence" in goal
        assert "strategy" in goal
