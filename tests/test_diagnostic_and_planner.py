# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tests for test_diagnostic_loop and patch_planner."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from test_diagnostic_loop import (
    DiagnosticIteration,
    DiagnosticSession,
    FailureDiagnosis,
    FailureInterpreter,
    RegressionExpander,
    TestDiagnosticLoop,
    TestResult,
    TestRunSummary,
    TestRunner,
)
from patch_planner import (
    DependencyOrderer,
    FileChange,
    FileRisk,
    OwnerResolver,
    PatchPlan,
    PatchPlanner,
    PatchStep,
    RiskAssessor,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_workspace():
    return Path(tempfile.mkdtemp(prefix="agente_test_diag_"))

PYTEST_OUTPUT_PASS = """\
============================= test session starts =============================
collected 3 items

tests/test_math.py::test_add PASSED
tests/test_math.py::test_sub PASSED
tests/test_math.py::test_mul PASSED

============================== 3 passed in 0.05s ==============================
"""

PYTEST_OUTPUT_FAIL = """\
============================= test session starts =============================
collected 5 items

tests/test_calc.py::test_add PASSED
tests/test_calc.py::test_divide FAILED
tests/test_calc.py::TestCalc::test_multiply PASSED
tests/test_calc.py::TestCalc::test_power FAILED
tests/test_calc.py::test_sqrt PASSED

================================== FAILURES ===================================
___________________________ test_divide ___________________________

    def test_divide():
>       assert divide(10, 0) == 0
E       ZeroDivisionError: division by zero

tests/test_calc.py:15: ZeroDivisionError
___________________________ TestCalc.test_power ___________________________

    def test_power():
>       assert power(2, -1) == 0.5
E       AssertionError: assert 2 == 0.5

tests/test_calc.py:28: AssertionError
=========================== short test summary info ===========================
FAILED tests/test_calc.py::test_divide
FAILED tests/test_calc.py::TestCalc::test_power
=================== 3 passed, 2 failed in 0.10s ============================
"""

PYTEST_OUTPUT_IMPORT_ERROR = """\
============================= test session starts =============================
collected 1 item

tests/test_api.py::test_health ERROR

================================== ERRORS =====================================
___________________________ test_health ___________________________

    import nonexistent_module
E   ModuleNotFoundError: No module named 'nonexistent_module'

tests/test_api.py:1: ModuleNotFoundError
=========================== short test summary info ===========================
FAILED tests/test_api.py::test_health
=================== 0 passed, 1 error in 0.01s ============================
"""


# ---------------------------------------------------------------------------
# TestResult data model
# ---------------------------------------------------------------------------

class TestTestResult:
    def test_to_dict(self):
        r = TestResult(name="test_foo", status="failed", error_message="assert 1 == 2")
        d = r.to_dict()
        assert d["name"] == "test_foo"
        assert d["status"] == "failed"

    def test_defaults(self):
        r = TestResult(name="x", status="passed")
        assert r.duration_ms == 0
        assert r.traceback == ""


class TestTestRunSummary:
    def test_all_passed(self):
        s = TestRunSummary(command="pytest", exit_code=0, passed=3, total=3)
        assert s.all_passed

    def test_not_all_passed(self):
        s = TestRunSummary(command="pytest", exit_code=1, passed=2, failed=1, total=3)
        assert not s.all_passed

    def test_failing_tests(self):
        s = TestRunSummary(
            command="pytest", exit_code=1,
            results=[
                TestResult(name="a", status="passed"),
                TestResult(name="b", status="failed"),
                TestResult(name="c", status="error"),
            ],
        )
        assert len(s.failing_tests) == 2

    def test_to_dict(self):
        s = TestRunSummary(command="pytest", exit_code=0, passed=1, total=1)
        d = s.to_dict()
        assert d["all_passed"] is True


# ---------------------------------------------------------------------------
# TestRunner — parsing
# ---------------------------------------------------------------------------

class TestTestRunnerParsing:
    def setup_method(self):
        self.runner = TestRunner("/tmp")

    def test_parse_passing_output(self):
        summary = self.runner._parse_pytest(PYTEST_OUTPUT_PASS)
        assert summary.passed == 3
        assert summary.failed == 0
        assert len(summary.results) == 3
        assert all(r.status == "passed" for r in summary.results)

    def test_parse_failing_output(self):
        summary = self.runner._parse_pytest(PYTEST_OUTPUT_FAIL)
        assert summary.passed == 3
        assert summary.failed == 2
        assert len(summary.results) == 5
        failing = [r for r in summary.results if r.status == "failed"]
        assert len(failing) == 2

    def test_parse_error_output(self):
        summary = self.runner._parse_pytest(PYTEST_OUTPUT_IMPORT_ERROR)
        assert summary.passed == 0

    def test_extract_failure_blocks(self):
        blocks = TestRunner._extract_failure_blocks(PYTEST_OUTPUT_FAIL)
        assert "test_divide" in blocks
        assert any("test_power" in k for k in blocks)

    def test_extract_error_line(self):
        line = TestRunner._extract_error_line("some traceback\n> assert x == y\nAssertionError: 1 != 2")
        assert "AssertionError" in line or "1 != 2" in line

    def test_build_pytest_command(self):
        cmd = self.runner._build_command("tests/test_foo.py", framework="pytest")
        assert "pytest" in " ".join(cmd)
        assert "tests/test_foo.py" in cmd

    def test_build_jest_command(self):
        cmd = self.runner._build_command("src/app.test.js", framework="jest")
        assert "jest" in " ".join(cmd)

    def test_build_unittest_command(self):
        cmd = self.runner._build_command("tests.test_foo", framework="unittest")
        assert "unittest" in " ".join(cmd)


# ---------------------------------------------------------------------------
# TestRunner — real execution
# ---------------------------------------------------------------------------

class TestTestRunnerExecution:
    def test_run_passing_tests(self):
        ws = _tmp_workspace()
        try:
            test_file = ws / "test_simple.py"
            test_file.write_text("def test_one():\n    assert 1 + 1 == 2\n", encoding="utf-8")
            runner = TestRunner(str(ws), timeout=30)
            summary = runner.run("test_simple.py")
            assert summary.exit_code == 0
            assert summary.passed >= 1
            assert summary.all_passed
        finally:
            shutil.rmtree(str(ws), ignore_errors=True)

    def test_run_failing_test(self):
        ws = _tmp_workspace()
        try:
            test_file = ws / "test_fail.py"
            test_file.write_text("def test_fail():\n    assert False\n", encoding="utf-8")
            runner = TestRunner(str(ws), timeout=30)
            summary = runner.run("test_fail.py")
            assert summary.exit_code != 0
            assert not summary.all_passed
            assert summary.failed >= 1
        finally:
            shutil.rmtree(str(ws), ignore_errors=True)


# ---------------------------------------------------------------------------
# FailureInterpreter
# ---------------------------------------------------------------------------

class TestFailureInterpreter:
    def setup_method(self):
        self.interpreter = FailureInterpreter()

    def test_classify_assertion(self):
        r = TestResult(
            name="test_x", status="failed",
            error_message="AssertionError: assert 1 == 2",
            traceback="assert 1 == 2\nAssertionError",
        )
        diag = self.interpreter.diagnose(r)
        assert diag.error_type == "assertion"
        assert diag.confidence >= 0.5

    def test_classify_import_error(self):
        r = TestResult(
            name="test_x", status="error",
            error_message="ModuleNotFoundError: No module named 'foo'",
            traceback="ModuleNotFoundError: No module named 'foo'",
        )
        diag = self.interpreter.diagnose(r)
        assert diag.error_type == "import"
        assert "foo" in diag.root_cause
        assert diag.confidence >= 0.8

    def test_classify_syntax_error(self):
        r = TestResult(
            name="test_x", status="error",
            error_message="SyntaxError: invalid syntax",
            traceback="SyntaxError: invalid syntax (file.py, line 10)",
        )
        diag = self.interpreter.diagnose(r)
        assert diag.error_type == "syntax"
        assert diag.confidence >= 0.8

    def test_classify_type_error(self):
        r = TestResult(
            name="test_x", status="failed",
            error_message="TypeError: expected int got str",
            traceback='TypeError: expected int got str\nFile "src/calc.py", line 5, in add',
        )
        diag = self.interpreter.diagnose(r)
        assert diag.error_type == "type_error"

    def test_classify_attribute_error(self):
        r = TestResult(
            name="test_x", status="failed",
            error_message="AttributeError: 'NoneType' has no attribute 'foo'",
            traceback="AttributeError: 'NoneType' has no attribute 'foo'",
        )
        diag = self.interpreter.diagnose(r)
        assert diag.error_type == "attribute_error"

    def test_classify_name_error(self):
        r = TestResult(
            name="test_x", status="failed",
            error_message="NameError: name 'xyz' is not defined",
            traceback="NameError: name 'xyz' is not defined",
        )
        diag = self.interpreter.diagnose(r)
        assert diag.error_type == "name_error"

    def test_classify_key_error(self):
        r = TestResult(
            name="test_x", status="failed",
            error_message="KeyError: 'missing_key'",
            traceback="KeyError: 'missing_key'",
        )
        diag = self.interpreter.diagnose(r)
        assert diag.error_type == "key_error"

    def test_classify_timeout(self):
        r = TestResult(
            name="test_x", status="failed",
            error_message="TimeoutError",
            traceback="TimeoutError: operation timed out",
        )
        diag = self.interpreter.diagnose(r)
        assert diag.error_type == "timeout"

    def test_extract_source_location(self):
        r = TestResult(
            name="test_x", status="failed",
            error_message="AssertionError",
            traceback='File "tests/test_calc.py", line 10, in test_x\nFile "src/calc.py", line 5, in add',
            file="tests/test_calc.py",
        )
        diag = self.interpreter.diagnose(r)
        assert diag.source_file == "src/calc.py"
        assert diag.source_line == 5
        assert diag.source_function == "add"

    def test_extract_related_symbols(self):
        r = TestResult(
            name="test_x", status="failed",
            error_message="err",
            traceback='File "a.py", line 1, in foo\nFile "b.py", line 2, in bar',
        )
        diag = self.interpreter.diagnose(r)
        assert "foo" in diag.related_symbols
        assert "bar" in diag.related_symbols

    def test_suggest_fix_import(self):
        r = TestResult(
            name="test_x", status="error",
            error_message="ModuleNotFoundError: No module named 'fastapi'",
            traceback="ModuleNotFoundError: No module named 'fastapi'",
        )
        diag = self.interpreter.diagnose(r)
        assert "fastapi" in diag.suggested_fix

    def test_diagnose_all(self):
        summary = TestRunSummary(
            command="pytest", exit_code=1,
            results=[
                TestResult(name="a", status="passed"),
                TestResult(name="b", status="failed", error_message="AssertionError"),
                TestResult(name="c", status="error", error_message="ImportError: no mod"),
            ],
        )
        diagnoses = self.interpreter.diagnose_all(summary)
        assert len(diagnoses) == 2

    def test_diagnosis_to_dict(self):
        diag = FailureDiagnosis(
            test_name="test_x", error_type="assertion",
            error_message="assert 1 == 2",
            source_file="calc.py", source_line=5,
        )
        d = diag.to_dict()
        assert d["test_name"] == "test_x"
        assert d["source_file"] == "calc.py"


# ---------------------------------------------------------------------------
# RegressionExpander
# ---------------------------------------------------------------------------

class TestRegressionExpander:
    def test_expand_without_codebase_map(self):
        expander = RegressionExpander()
        diag = FailureDiagnosis(
            test_name="test_add", error_type="assertion",
            error_message="", source_file="src/calc.py",
        )
        targets = expander.expand([diag])
        assert "test_calc" in targets

    def test_expand_empty(self):
        expander = RegressionExpander()
        assert expander.expand([]) == []

    def test_expand_with_mock_codebase_map(self):
        class MockMap:
            def find_tests_for(self, module):
                return [{"file": "tests/test_calc.py"}]
            def dependency_graph(self, path):
                return {"imported_by": [{"file": "tests/test_integration.py"}]}
            def find_symbol(self, name):
                return []

        expander = RegressionExpander()
        diag = FailureDiagnosis(
            test_name="test_x", error_type="assertion",
            error_message="", source_file="src/calc.py",
            related_symbols=["add"],
        )
        targets = expander.expand([diag], codebase_map=MockMap())
        assert "tests/test_calc.py" in targets
        assert "tests/test_integration.py" in targets


# ---------------------------------------------------------------------------
# TestDiagnosticLoop
# ---------------------------------------------------------------------------

class TestDiagLoop:
    def test_run_and_diagnose_passing(self):
        ws = _tmp_workspace()
        try:
            (ws / "test_ok.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
            loop = TestDiagnosticLoop(str(ws))
            it = loop.run_and_diagnose("test_ok.py")
            assert it.fixed
            assert it.run_summary is not None
            assert it.run_summary.all_passed
        finally:
            shutil.rmtree(str(ws), ignore_errors=True)

    def test_run_and_diagnose_failing(self):
        ws = _tmp_workspace()
        try:
            (ws / "test_bad.py").write_text("def test_bad(): assert False\n", encoding="utf-8")
            loop = TestDiagnosticLoop(str(ws))
            it = loop.run_and_diagnose("test_bad.py")
            assert not it.fixed
            assert len(it.diagnoses) >= 1
        finally:
            shutil.rmtree(str(ws), ignore_errors=True)

    def test_session_lifecycle(self):
        ws = _tmp_workspace()
        try:
            (ws / "test_ok.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
            loop = TestDiagnosticLoop(str(ws))
            session = loop.get_session("test_ok.py")
            assert session.status == "running"
            it = loop.iterate(session)
            assert session.status == "fixed"
        finally:
            shutil.rmtree(str(ws), ignore_errors=True)

    def test_iteration_to_dict(self):
        it = DiagnosticIteration(iteration=0, phase="run", target_tests=["test_x"])
        d = it.to_dict()
        assert d["iteration"] == 0
        assert d["phase"] == "run"

    def test_session_to_dict(self):
        s = DiagnosticSession(session_id="s1", workspace="/tmp", target="test_x")
        d = s.to_dict()
        assert d["session_id"] == "s1"


# ---------------------------------------------------------------------------
# PATCH PLANNER TESTS
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# FileChange data model
# ---------------------------------------------------------------------------

class TestFileChange:
    def test_to_dict(self):
        c = FileChange(path="src/main.py", change_type="modify", lines_added=10, lines_removed=5)
        d = c.to_dict()
        assert d["path"] == "src/main.py"
        assert d["lines_added"] == 10

    def test_defaults(self):
        c = FileChange(path="x.py", change_type="add")
        assert c.is_test is False
        assert c.is_config is False


# ---------------------------------------------------------------------------
# RiskAssessor
# ---------------------------------------------------------------------------

class TestRiskAssessor:
    def setup_method(self):
        self.assessor = RiskAssessor()

    def test_low_risk_small_change(self):
        c = FileChange(path="src/utils.py", change_type="modify", lines_added=5)
        risk = self.assessor.assess(c, has_tests=True)
        assert risk.risk_level == "low"
        assert risk.risk_score < 0.2

    def test_higher_risk_large_change(self):
        c = FileChange(path="src/core.py", change_type="modify", lines_added=150, lines_removed=100)
        risk = self.assessor.assess(c)
        assert risk.risk_score > 0.1
        assert any("large" in f for f in risk.factors)

    def test_config_file_risk(self):
        c = FileChange(path="pyproject.toml", change_type="modify", is_config=True)
        risk = self.assessor.assess(c)
        assert risk.risk_score >= 0.25
        assert any("config" in f for f in risk.factors)

    def test_delete_risk(self):
        c = FileChange(path="src/old.py", change_type="delete")
        risk = self.assessor.assess(c)
        assert any("deletion" in f for f in risk.factors)

    def test_rename_risk(self):
        c = FileChange(path="src/renamed.py", change_type="rename")
        risk = self.assessor.assess(c)
        assert any("rename" in f for f in risk.factors)

    def test_high_risk_path(self):
        c = FileChange(path="security/auth.py", change_type="modify")
        risk = self.assessor.assess(c)
        assert any("high-risk" in f for f in risk.factors)

    def test_fan_out_risk(self):
        c = FileChange(path="src/base.py", change_type="modify")
        risk = self.assessor.assess(c, dependents_count=15)
        assert any("fan-out" in f for f in risk.factors)

    def test_no_test_coverage(self):
        c = FileChange(path="src/no_tests.py", change_type="modify")
        risk = self.assessor.assess(c, has_tests=False)
        assert any("no test" in f for f in risk.factors)

    def test_test_file_lower_risk(self):
        c = FileChange(path="tests/test_foo.py", change_type="modify", is_test=True, lines_added=200)
        risk = self.assessor.assess(c)
        assert risk.risk_score < 0.5  # halved due to test file

    def test_risk_to_dict(self):
        r = FileRisk(path="x.py", risk_level="medium", risk_score=0.35, factors=["big"])
        d = r.to_dict()
        assert d["risk_level"] == "medium"
        assert d["risk_score"] == 0.35

    def test_score_to_level(self):
        assert RiskAssessor._score_to_level(0.05) == "low"
        assert RiskAssessor._score_to_level(0.3) == "medium"
        assert RiskAssessor._score_to_level(0.5) == "high"
        assert RiskAssessor._score_to_level(0.8) == "critical"


# ---------------------------------------------------------------------------
# DependencyOrderer
# ---------------------------------------------------------------------------

class TestDependencyOrderer:
    def setup_method(self):
        self.orderer = DependencyOrderer()

    def test_heuristic_order(self):
        changes = [
            FileChange(path="tests/test_x.py", change_type="modify", is_test=True),
            FileChange(path="src/main.py", change_type="modify"),
            FileChange(path="pyproject.toml", change_type="modify", is_config=True),
        ]
        ordered = self.orderer._heuristic_order(changes)
        assert ordered[0].is_config
        assert ordered[-1].is_test

    def test_order_without_map(self):
        changes = [
            FileChange(path="test_a.py", change_type="modify", is_test=True),
            FileChange(path="lib.py", change_type="modify"),
        ]
        ordered = self.orderer.order(changes)
        assert ordered[0].path == "lib.py"

    def test_order_preserves_all_files(self):
        changes = [FileChange(path=f"file_{i}.py", change_type="modify") for i in range(5)]
        ordered = self.orderer.order(changes)
        assert len(ordered) == 5


# ---------------------------------------------------------------------------
# OwnerResolver
# ---------------------------------------------------------------------------

class TestOwnerResolver:
    def test_resolve_from_path(self):
        resolver = OwnerResolver()
        owner = resolver.resolve("src/main.py")
        assert owner == "src"

    def test_resolve_root_file(self):
        resolver = OwnerResolver()
        owner = resolver.resolve("main.py")
        assert owner == "unowned"


# ---------------------------------------------------------------------------
# PatchPlanner
# ---------------------------------------------------------------------------

class TestPatchPlanner:
    def setup_method(self):
        self.planner = PatchPlanner()

    def test_plan_empty(self):
        plan = self.planner.plan([])
        assert plan.total_files == 0
        assert plan.steps == []

    def test_plan_single_file(self):
        changes = [FileChange(path="src/main.py", change_type="modify", lines_added=10)]
        plan = self.planner.plan(changes)
        assert plan.total_files == 1
        assert plan.total_lines_added == 10
        assert len(plan.steps) == 1
        assert plan.steps[0].order == 0

    def test_plan_multiple_files(self):
        changes = [
            FileChange(path="src/main.py", change_type="modify", lines_added=20, lines_removed=5),
            FileChange(path="tests/test_main.py", change_type="modify", is_test=True, lines_added=15),
            FileChange(path="pyproject.toml", change_type="modify", is_config=True, lines_added=2),
        ]
        plan = self.planner.plan(changes)
        assert plan.total_files == 3
        assert plan.total_lines_added == 37
        assert plan.total_lines_removed == 5
        assert len(plan.owner_groups) > 0

    def test_plan_generates_warnings(self):
        changes = [
            FileChange(path="src/main.py", change_type="modify", lines_added=10),
        ]
        plan = self.planner.plan(changes)
        # Should warn about no test coverage
        assert any("test coverage" in w.lower() for w in plan.warnings)

    def test_plan_high_risk_warning(self):
        changes = [
            FileChange(path="security/auth.py", change_type="delete", lines_removed=100),
        ]
        plan = self.planner.plan(changes)
        assert any("high-risk" in w.lower() for w in plan.warnings)

    def test_plan_to_dict(self):
        changes = [FileChange(path="x.py", change_type="add")]
        plan = self.planner.plan(changes)
        d = plan.to_dict()
        assert "plan_id" in d
        assert "steps" in d
        assert "warnings" in d

    def test_plan_from_dicts(self):
        dicts = [
            {"path": "src/main.py", "change_type": "modify", "lines_added": 10},
            {"path": "tests/test_main.py", "change_type": "modify"},
            {"path": "pyproject.toml", "change_type": "modify"},
        ]
        plan = self.planner.plan_from_dicts(dicts)
        assert plan.total_files == 3
        test_step = next(s for s in plan.steps if "test" in s.path)
        assert test_step.change.is_test
        config_step = next(s for s in plan.steps if s.path == "pyproject.toml")
        assert config_step.change.is_config

    def test_plan_risk_summary(self):
        changes = [
            FileChange(path="src/a.py", change_type="modify"),
            FileChange(path="security/b.py", change_type="modify"),
        ]
        plan = self.planner.plan(changes)
        assert "low" in plan.risk_summary or "medium" in plan.risk_summary

    def test_patch_step_to_dict(self):
        step = PatchStep(
            order=0, path="x.py",
            change=FileChange(path="x.py", change_type="modify"),
            risk=FileRisk(path="x.py", risk_level="low"),
            owner="team-a",
        )
        d = step.to_dict()
        assert d["order"] == 0
        assert d["owner"] == "team-a"


# ---------------------------------------------------------------------------
# Canonical tools integration
# ---------------------------------------------------------------------------

class TestCanonicalIntegration:
    def test_test_diagnostic_in_supported_tools(self):
        from canonical_tools import supported_tools
        assert "test_diagnostic" in supported_tools()

    def test_patch_planner_in_supported_tools(self):
        from canonical_tools import supported_tools
        assert "patch_planner" in supported_tools()

    def test_action_metadata_test_diagnostic(self):
        from canonical_tools import action_metadata
        meta = action_metadata("test_diagnostic", "run_and_diagnose")
        assert meta["semantic_type"] == "inspection"

    def test_action_metadata_patch_planner(self):
        from canonical_tools import action_metadata
        meta = action_metadata("patch_planner", "plan")
        assert meta["semantic_type"] == "inspection"

    def test_task_title_test_diagnostic(self):
        from canonical_tools import task_title
        title = task_title("test_diagnostic", "run_and_diagnose", {"target": "tests/test_foo.py"})
        assert "test_foo" in title

    def test_task_title_patch_planner(self):
        from canonical_tools import task_title
        title = task_title("patch_planner", "plan", {})
        assert "Patch" in title

    def test_tool_definitions_include_both(self):
        from canonical_tools import tool_definitions
        defs = tool_definitions()
        names = [d["function"]["name"] for d in defs]
        assert "test_diagnostic" in names
        assert "patch_planner" in names

    def test_normalize_test_diagnostic(self):
        from canonical_tools import normalize_agentic_task
        task = normalize_agentic_task(
            "test_diagnostic",
            {"action": "run_and_diagnose", "target": "tests/test_foo.py"},
            task_id="t-1",
        )
        assert task["tool"] == "test_diagnostic"
        assert task["params"]["target"] == "tests/test_foo.py"

    def test_normalize_patch_planner(self):
        from canonical_tools import normalize_agentic_task
        task = normalize_agentic_task(
            "patch_planner",
            {"action": "plan", "changes": [{"path": "x.py"}]},
            task_id="t-1",
        )
        assert task["tool"] == "patch_planner"
        assert len(task["params"]["changes"]) == 1


class TestToolRegistry:
    def test_registry_has_test_diagnostic(self):
        from tool_registry import ToolRegistry
        registry = ToolRegistry()
        assert "test_diagnostic" in registry.tools
        assert registry.supports("test_diagnostic")

    def test_registry_has_patch_planner(self):
        from tool_registry import ToolRegistry
        registry = ToolRegistry()
        assert "patch_planner" in registry.tools
        assert registry.supports("patch_planner")
