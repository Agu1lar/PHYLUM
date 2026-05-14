# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Test diagnostic loop — run test, interpret failure, patch, rerun, expand regression.

Orchestrates an iterative cycle:
1. **Run** a target test (pytest, jest, etc.) and capture structured output.
2. **Interpret** the failure: extract failing tests, error messages, tracebacks,
   and infer which source file/function is responsible.
3. **Suggest patches** based on the failure analysis.
4. **Rerun** the target test to verify the fix.
5. **Expand** to related tests (regression) to ensure nothing else broke.

The loop is designed to be driven by the agentic runtime — each method returns
structured data that the LLM can reason about and act on.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    name: str
    status: str  # passed, failed, error, skipped
    duration_ms: int = 0
    file: str = ""
    line: int = 0
    error_message: str = ""
    traceback: str = ""
    stdout: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name, "status": self.status,
            "duration_ms": self.duration_ms,
            "file": self.file, "line": self.line,
            "error_message": self.error_message,
            "traceback": self.traceback[:2000],
            "stdout": self.stdout[:1000],
        }


@dataclass
class TestRunSummary:
    command: str
    exit_code: int
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_ms: int = 0
    results: List[TestResult] = field(default_factory=list)
    raw_output: str = ""

    @property
    def all_passed(self) -> bool:
        return self.failed == 0 and self.errors == 0

    @property
    def failing_tests(self) -> List[TestResult]:
        return [r for r in self.results if r.status in ("failed", "error")]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command, "exit_code": self.exit_code,
            "total": self.total, "passed": self.passed,
            "failed": self.failed, "errors": self.errors,
            "skipped": self.skipped, "duration_ms": self.duration_ms,
            "all_passed": self.all_passed,
            "results": [r.to_dict() for r in self.results],
            "raw_output": self.raw_output[:5000],
        }


@dataclass
class FailureDiagnosis:
    test_name: str
    error_type: str  # assertion, exception, import, timeout, syntax, unknown
    error_message: str
    source_file: str = ""
    source_line: int = 0
    source_function: str = ""
    root_cause: str = ""
    suggested_fix: str = ""
    related_symbols: List[str] = field(default_factory=list)
    confidence: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        return {
            "test_name": self.test_name,
            "error_type": self.error_type,
            "error_message": self.error_message[:500],
            "source_file": self.source_file,
            "source_line": self.source_line,
            "source_function": self.source_function,
            "root_cause": self.root_cause,
            "suggested_fix": self.suggested_fix,
            "related_symbols": self.related_symbols,
            "confidence": round(self.confidence, 4),
        }


@dataclass
class DiagnosticIteration:
    iteration: int
    phase: str  # run, interpret, patch, rerun, expand
    target_tests: List[str]
    run_summary: Optional[TestRunSummary] = None
    diagnoses: List[FailureDiagnosis] = field(default_factory=list)
    patch_applied: bool = False
    patch_description: str = ""
    regression_tests: List[str] = field(default_factory=list)
    fixed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "iteration": self.iteration, "phase": self.phase,
            "target_tests": self.target_tests,
            "run_summary": self.run_summary.to_dict() if self.run_summary else None,
            "diagnoses": [d.to_dict() for d in self.diagnoses],
            "patch_applied": self.patch_applied,
            "patch_description": self.patch_description,
            "regression_tests": self.regression_tests,
            "fixed": self.fixed,
        }


@dataclass
class DiagnosticSession:
    session_id: str
    workspace: str
    target: str
    iterations: List[DiagnosticIteration] = field(default_factory=list)
    status: str = "running"  # running, fixed, max_iterations, needs_user
    total_duration_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "workspace": self.workspace,
            "target": self.target,
            "iterations": [it.to_dict() for it in self.iterations],
            "status": self.status,
            "total_duration_ms": self.total_duration_ms,
        }


# ---------------------------------------------------------------------------
# TestRunner — executes tests and parses output
# ---------------------------------------------------------------------------

class TestRunner:
    """Runs tests via subprocess and parses structured output."""

    def __init__(self, workspace: str, *, timeout: int = 120):
        self.workspace = workspace
        self.timeout = timeout

    def run(
        self,
        target: str = "",
        *,
        framework: str = "pytest",
        extra_args: Optional[List[str]] = None,
    ) -> TestRunSummary:
        cmd = self._build_command(target, framework=framework, extra_args=extra_args)
        cmd_str = " ".join(cmd)

        start = time.time()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=self.workspace,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            duration_ms = int((time.time() - start) * 1000)
            output = result.stdout + "\n" + result.stderr
            summary = self._parse_output(output, framework=framework)
            summary.command = cmd_str
            summary.exit_code = result.returncode
            summary.duration_ms = duration_ms
            summary.raw_output = output
            return summary
        except subprocess.TimeoutExpired:
            duration_ms = int((time.time() - start) * 1000)
            return TestRunSummary(
                command=cmd_str,
                exit_code=-1,
                duration_ms=duration_ms,
                raw_output="Test run timed out",
            )
        except Exception as exc:
            return TestRunSummary(
                command=cmd_str,
                exit_code=-1,
                raw_output=f"Failed to run tests: {exc}",
            )

    def _build_command(
        self, target: str, *, framework: str, extra_args: Optional[List[str]] = None,
    ) -> List[str]:
        args = extra_args or []
        if framework == "pytest":
            cmd = [sys.executable, "-m", "pytest", "--tb=short", "-v"]
            if target:
                cmd.append(target)
            cmd.extend(args)
            return cmd
        if framework == "jest":
            cmd = ["npx", "jest", "--verbose"]
            if target:
                cmd.append(target)
            cmd.extend(args)
            return cmd
        if framework == "unittest":
            cmd = [sys.executable, "-m", "unittest"]
            if target:
                cmd.append(target)
            cmd.extend(args)
            return cmd
        return [sys.executable, "-m", "pytest", "--tb=short", "-v"] + ([target] if target else []) + args

    def _parse_output(self, output: str, *, framework: str) -> TestRunSummary:
        if framework in ("pytest", "unittest"):
            return self._parse_pytest(output)
        if framework == "jest":
            return self._parse_jest(output)
        return self._parse_pytest(output)

    def _parse_pytest(self, output: str) -> TestRunSummary:
        summary = TestRunSummary(command="", exit_code=0)
        results: List[TestResult] = []

        # Parse individual test lines: "tests/test_foo.py::test_bar PASSED"
        test_line_re = re.compile(
            r"^([\w/\\.-]+\.py)::(\S+)\s+(PASSED|FAILED|ERROR|SKIPPED)",
            re.MULTILINE,
        )
        for m in test_line_re.finditer(output):
            status_map = {"PASSED": "passed", "FAILED": "failed", "ERROR": "error", "SKIPPED": "skipped"}
            results.append(TestResult(
                name=m.group(2),
                status=status_map.get(m.group(3), "unknown"),
                file=m.group(1),
            ))

        # Parse summary line: "X passed, Y failed, Z error in N.NNs"
        passed_m = re.search(r"(\d+)\s+passed", output)
        failed_m = re.search(r"(\d+)\s+failed", output)
        errors_m = re.search(r"(\d+)\s+error", output)
        skipped_m = re.search(r"(\d+)\s+skipped", output)
        if passed_m:
            summary.passed = int(passed_m.group(1))
        if failed_m:
            summary.failed = int(failed_m.group(1))
        if errors_m:
            summary.errors = int(errors_m.group(1))
        if skipped_m:
            summary.skipped = int(skipped_m.group(1))
        summary.total = summary.passed + summary.failed + summary.errors + summary.skipped

        # Extract failure blocks
        failure_blocks = self._extract_failure_blocks(output)
        for test_name, block in failure_blocks.items():
            for r in results:
                if r.name == test_name or test_name.endswith(r.name):
                    r.traceback = block
                    r.error_message = self._extract_error_line(block)
                    break

        summary.results = results
        return summary

    def _parse_jest(self, output: str) -> TestRunSummary:
        summary = TestRunSummary(command="", exit_code=0)

        pass_re = re.compile(r"Tests:\s+(?:(\d+)\s+failed,\s*)?(\d+)\s+passed")
        m = pass_re.search(output)
        if m:
            summary.failed = int(m.group(1) or 0)
            summary.passed = int(m.group(2) or 0)
            summary.total = summary.passed + summary.failed

        fail_re = re.compile(r"●\s+(.+?)$\s+([\s\S]*?)(?=●|\Z)", re.MULTILINE)
        for m in fail_re.finditer(output):
            summary.results.append(TestResult(
                name=m.group(1).strip(),
                status="failed",
                error_message=m.group(2).strip()[:500],
                traceback=m.group(2).strip()[:2000],
            ))

        return summary

    @staticmethod
    def _extract_failure_blocks(output: str) -> Dict[str, str]:
        blocks: Dict[str, str] = {}
        failure_re = re.compile(
            r"_{3,}\s+([\w.]+(?:::[\w.]+)*)\s+_{3,}([\s\S]*?)(?=_{3,}|={3,}\s|$)"
        )
        for m in failure_re.finditer(output):
            name = m.group(1).split("::")[-1]
            blocks[name] = m.group(2).strip()

        short_re = re.compile(r"FAILED\s+([\w/\\.-]+)::(\S+)")
        for m in short_re.finditer(output):
            name = m.group(2)
            if name not in blocks:
                blocks[name] = ""
        return blocks

    @staticmethod
    def _extract_error_line(traceback_text: str) -> str:
        lines = traceback_text.strip().splitlines()
        for line in reversed(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith(("_", "=", "-", ">")):
                return stripped[:300]
        return ""


# ---------------------------------------------------------------------------
# FailureInterpreter — diagnoses test failures
# ---------------------------------------------------------------------------

class FailureInterpreter:
    """Analyzes test failures and produces structured diagnoses."""

    _ASSERTION_RE = re.compile(r"assert(?:ion)?error|assertEqual|assertTrue|assertRaises", re.IGNORECASE)
    _IMPORT_RE = re.compile(r"(?:ModuleNotFoundError|ImportError):\s*(.+)", re.IGNORECASE)
    _SYNTAX_RE = re.compile(r"SyntaxError:\s*(.+)", re.IGNORECASE)
    _TYPE_RE = re.compile(r"TypeError:\s*(.+)", re.IGNORECASE)
    _ATTR_RE = re.compile(r"AttributeError:\s*(.+)", re.IGNORECASE)
    _NAME_RE = re.compile(r"NameError:\s*(.+)", re.IGNORECASE)
    _KEY_RE = re.compile(r"KeyError:\s*(.+)", re.IGNORECASE)
    _TIMEOUT_RE = re.compile(r"TimeoutError|timeout|timed?\s*out", re.IGNORECASE)

    _SOURCE_LOC_RE = re.compile(
        r"(?:File\s+\"([^\"]+)\",\s+line\s+(\d+)(?:,\s+in\s+(\w+))?)"
    )
    _SHORT_LOC_RE = re.compile(
        r"([\w/\\.-]+\.py):(\d+):\s+in\s+(\w+)"
    )

    def diagnose(self, test_result: TestResult) -> FailureDiagnosis:
        tb = test_result.traceback or test_result.error_message or ""
        error_type = self._classify_error(tb, test_result.error_message)
        source_file, source_line, source_func = self._extract_source_location(
            tb, test_result.file,
        )
        root_cause = self._infer_root_cause(error_type, test_result.error_message, tb)
        suggested_fix = self._suggest_fix(error_type, test_result.error_message, tb)
        related = self._extract_related_symbols(tb)
        confidence = self._estimate_confidence(error_type, source_file, root_cause)

        return FailureDiagnosis(
            test_name=test_result.name,
            error_type=error_type,
            error_message=test_result.error_message,
            source_file=source_file,
            source_line=source_line,
            source_function=source_func,
            root_cause=root_cause,
            suggested_fix=suggested_fix,
            related_symbols=related,
            confidence=confidence,
        )

    def diagnose_all(self, summary: TestRunSummary) -> List[FailureDiagnosis]:
        return [self.diagnose(r) for r in summary.failing_tests]

    def _classify_error(self, traceback: str, error_msg: str) -> str:
        text = traceback + "\n" + error_msg
        if self._SYNTAX_RE.search(text):
            return "syntax"
        if self._IMPORT_RE.search(text):
            return "import"
        if self._TIMEOUT_RE.search(text):
            return "timeout"
        if self._ASSERTION_RE.search(text):
            return "assertion"
        if self._TYPE_RE.search(text):
            return "type_error"
        if self._ATTR_RE.search(text):
            return "attribute_error"
        if self._NAME_RE.search(text):
            return "name_error"
        if self._KEY_RE.search(text):
            return "key_error"
        return "exception"

    def _extract_source_location(
        self, traceback: str, test_file: str,
    ) -> Tuple[str, int, str]:
        # Prefer non-test file locations (the actual source, not the test)
        locations = []
        for m in self._SOURCE_LOC_RE.finditer(traceback):
            locations.append((m.group(1), int(m.group(2)), m.group(3) or ""))
        for m in self._SHORT_LOC_RE.finditer(traceback):
            locations.append((m.group(1), int(m.group(2)), m.group(3) or ""))

        non_test = [
            loc for loc in locations
            if not Path(loc[0]).name.startswith("test_")
            and "site-packages" not in loc[0]
        ]
        if non_test:
            return non_test[-1]
        if locations:
            return locations[-1]
        return (test_file, 0, "")

    def _infer_root_cause(self, error_type: str, error_msg: str, tb: str) -> str:
        if error_type == "import":
            m = self._IMPORT_RE.search(tb + "\n" + error_msg)
            return f"Missing module: {m.group(1).strip()}" if m else "Missing import"
        if error_type == "syntax":
            m = self._SYNTAX_RE.search(tb + "\n" + error_msg)
            return f"Syntax error: {m.group(1).strip()}" if m else "Syntax error"
        if error_type == "assertion":
            return f"Assertion failed: {error_msg[:200]}" if error_msg else "Assertion failed"
        if error_type == "type_error":
            m = self._TYPE_RE.search(tb + "\n" + error_msg)
            return f"Type error: {m.group(1).strip()}" if m else "Type mismatch"
        if error_type == "attribute_error":
            m = self._ATTR_RE.search(tb + "\n" + error_msg)
            return f"Missing attribute: {m.group(1).strip()}" if m else "Missing attribute"
        if error_type == "name_error":
            m = self._NAME_RE.search(tb + "\n" + error_msg)
            return f"Undefined name: {m.group(1).strip()}" if m else "Undefined name"
        if error_type == "key_error":
            return f"Missing key: {error_msg[:200]}" if error_msg else "Missing key"
        if error_type == "timeout":
            return "Operation timed out"
        return error_msg[:200] if error_msg else "Unknown error"

    def _suggest_fix(self, error_type: str, error_msg: str, tb: str) -> str:
        if error_type == "import":
            m = self._IMPORT_RE.search(tb + "\n" + error_msg)
            if m:
                mod = m.group(1).strip().strip("'\"")
                return f"Install or add '{mod}' to imports"
            return "Check imports and dependencies"
        if error_type == "syntax":
            return "Fix the syntax error at the indicated line"
        if error_type == "assertion":
            return "Review the assertion — expected vs actual values may have changed"
        if error_type == "type_error":
            return "Check function arguments and return types"
        if error_type == "attribute_error":
            return "Verify the attribute exists on the object, or check for typos"
        if error_type == "name_error":
            return "Define the missing name or add the required import"
        if error_type == "key_error":
            return "Add the missing key or use .get() with a default"
        if error_type == "timeout":
            return "Increase timeout or investigate why the operation hangs"
        return "Investigate the traceback for the root cause"

    def _extract_related_symbols(self, traceback: str) -> List[str]:
        symbols: set = set()
        for m in self._SOURCE_LOC_RE.finditer(traceback):
            if m.group(3):
                symbols.add(m.group(3))
        for m in self._SHORT_LOC_RE.finditer(traceback):
            symbols.add(m.group(3))
        return sorted(symbols)[:10]

    @staticmethod
    def _estimate_confidence(error_type: str, source_file: str, root_cause: str) -> float:
        base = 0.5
        if error_type in ("import", "syntax", "name_error"):
            base = 0.9
        elif error_type in ("assertion", "type_error", "attribute_error", "key_error"):
            base = 0.7
        elif error_type == "timeout":
            base = 0.4
        if source_file and not Path(source_file).name.startswith("test_"):
            base = min(base + 0.1, 1.0)
        if root_cause and len(root_cause) > 10:
            base = min(base + 0.05, 1.0)
        return round(base, 4)


# ---------------------------------------------------------------------------
# RegressionExpander — finds related tests
# ---------------------------------------------------------------------------

class RegressionExpander:
    """Expands a set of failing tests to include related regression tests."""

    def expand(
        self,
        diagnoses: List[FailureDiagnosis],
        *,
        codebase_map=None,
        workspace: str = "",
    ) -> List[str]:
        """Return a list of additional test targets to run for regression."""
        test_targets: set = set()

        for diag in diagnoses:
            if diag.source_file:
                stem = Path(diag.source_file).stem
                test_targets.add(f"test_{stem}")

                if codebase_map:
                    try:
                        tests = codebase_map.find_tests_for(stem)
                        for t in tests:
                            test_targets.add(t.get("file", ""))
                    except Exception:
                        pass

                    try:
                        deps = codebase_map.dependency_graph(diag.source_file)
                        for importer in deps.get("imported_by", []):
                            imp_file = importer.get("file", "")
                            if "test" in imp_file.lower():
                                test_targets.add(imp_file)
                    except Exception:
                        pass

            for sym in diag.related_symbols:
                if codebase_map:
                    try:
                        matches = codebase_map.find_symbol(sym)
                        for match in matches:
                            f = match.get("file", "")
                            if "test" in f.lower():
                                test_targets.add(f)
                    except Exception:
                        pass

        test_targets.discard("")
        return sorted(test_targets)[:20]


# ---------------------------------------------------------------------------
# TestDiagnosticLoop — full orchestrator
# ---------------------------------------------------------------------------

class TestDiagnosticLoop:
    """Orchestrates the run → interpret → patch → rerun → expand cycle."""

    def __init__(
        self,
        workspace: str,
        *,
        max_iterations: int = 5,
        test_timeout: int = 120,
        codebase_map=None,
    ):
        self.workspace = workspace
        self.max_iterations = max_iterations
        self.runner = TestRunner(workspace, timeout=test_timeout)
        self.interpreter = FailureInterpreter()
        self.expander = RegressionExpander()
        self.codebase_map = codebase_map
        self._session_counter = 0

    def run_and_diagnose(
        self,
        target: str = "",
        *,
        framework: str = "pytest",
        extra_args: Optional[List[str]] = None,
    ) -> DiagnosticIteration:
        """Single pass: run tests and diagnose failures."""
        summary = self.runner.run(target, framework=framework, extra_args=extra_args)
        diagnoses = self.interpreter.diagnose_all(summary)

        iteration = DiagnosticIteration(
            iteration=0,
            phase="run",
            target_tests=[target] if target else [],
            run_summary=summary,
            diagnoses=diagnoses,
            fixed=summary.all_passed,
        )

        if not summary.all_passed and diagnoses:
            regression = self.expander.expand(
                diagnoses, codebase_map=self.codebase_map, workspace=self.workspace,
            )
            iteration.regression_tests = regression

        return iteration

    def get_session(self, target: str) -> DiagnosticSession:
        self._session_counter += 1
        return DiagnosticSession(
            session_id=f"diag-{self._session_counter}",
            workspace=self.workspace,
            target=target,
        )

    def iterate(
        self,
        session: DiagnosticSession,
        *,
        framework: str = "pytest",
        extra_args: Optional[List[str]] = None,
    ) -> DiagnosticIteration:
        """Run one iteration of the diagnostic loop within a session."""
        iteration_num = len(session.iterations)
        target = session.target

        if iteration_num > 0:
            prev = session.iterations[-1]
            if prev.fixed:
                # Previous iteration fixed the target — now run regression
                if prev.regression_tests:
                    target = " ".join(prev.regression_tests[:5])
                    it = self.run_and_diagnose(
                        target, framework=framework, extra_args=extra_args,
                    )
                    it.iteration = iteration_num
                    it.phase = "expand"
                    session.iterations.append(it)
                    if it.fixed:
                        session.status = "fixed"
                    return it
                else:
                    session.status = "fixed"
                    return prev

        it = self.run_and_diagnose(
            target, framework=framework, extra_args=extra_args,
        )
        it.iteration = iteration_num
        session.iterations.append(it)

        if it.fixed:
            session.status = "fixed"
        elif iteration_num >= self.max_iterations - 1:
            session.status = "max_iterations"

        return it
