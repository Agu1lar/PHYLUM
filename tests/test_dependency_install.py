# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tests for automatic dependency detection and install-with-approval flow."""
from __future__ import annotations

import pytest

from sandbox_executor import SandboxExecutor, SandboxResult


class TestDetectMissingModules:
    def test_detects_module_not_found_error(self):
        stderr = "Traceback (most recent call last):\n  File \"script.py\", line 1\nModuleNotFoundError: No module named 'pandas'"
        result = SandboxExecutor.detect_missing_modules(stderr)
        assert result == [{"module": "pandas", "package": "pandas"}]

    def test_detects_import_error(self):
        stderr = "ImportError: No module named 'yaml'"
        result = SandboxExecutor.detect_missing_modules(stderr)
        assert result == [{"module": "yaml", "package": "pyyaml"}]

    def test_maps_known_module_to_package(self):
        stderr = "ModuleNotFoundError: No module named 'cv2'"
        result = SandboxExecutor.detect_missing_modules(stderr)
        assert result == [{"module": "cv2", "package": "opencv-python"}]

    def test_maps_pil_to_pillow(self):
        stderr = "ModuleNotFoundError: No module named 'PIL'"
        result = SandboxExecutor.detect_missing_modules(stderr)
        assert result == [{"module": "PIL", "package": "Pillow"}]

    def test_maps_sklearn(self):
        stderr = "ModuleNotFoundError: No module named 'sklearn'"
        result = SandboxExecutor.detect_missing_modules(stderr)
        assert result == [{"module": "sklearn", "package": "scikit-learn"}]

    def test_maps_bs4(self):
        stderr = "ImportError: No module named 'bs4'"
        result = SandboxExecutor.detect_missing_modules(stderr)
        assert result == [{"module": "bs4", "package": "beautifulsoup4"}]

    def test_handles_submodule_import(self):
        stderr = "ModuleNotFoundError: No module named 'PIL.Image'"
        result = SandboxExecutor.detect_missing_modules(stderr)
        assert result == [{"module": "PIL", "package": "Pillow"}]

    def test_detects_multiple_missing_modules(self):
        stderr = (
            "ModuleNotFoundError: No module named 'pandas'\n"
            "ImportError: No module named 'numpy'\n"
        )
        result = SandboxExecutor.detect_missing_modules(stderr)
        assert len(result) == 2
        packages = {m["package"] for m in result}
        assert packages == {"pandas", "numpy"}

    def test_deduplicates_same_module(self):
        stderr = (
            "ModuleNotFoundError: No module named 'pandas'\n"
            "ModuleNotFoundError: No module named 'pandas'\n"
        )
        result = SandboxExecutor.detect_missing_modules(stderr)
        assert len(result) == 1

    def test_returns_empty_for_no_import_error(self):
        stderr = "TypeError: unsupported operand type(s) for +: 'int' and 'str'"
        result = SandboxExecutor.detect_missing_modules(stderr)
        assert result == []

    def test_returns_empty_for_empty_stderr(self):
        assert SandboxExecutor.detect_missing_modules("") == []

    def test_unknown_module_uses_module_name_as_package(self):
        stderr = "ModuleNotFoundError: No module named 'some_custom_lib'"
        result = SandboxExecutor.detect_missing_modules(stderr)
        assert result == [{"module": "some_custom_lib", "package": "some_custom_lib"}]


class TestSandboxResultMissingModules:
    def test_to_dict_includes_missing_modules_when_present(self):
        result = SandboxResult(
            ok=False,
            stderr="ModuleNotFoundError: No module named 'pandas'",
            error="ModuleNotFoundError: No module named 'pandas'",
            missing_modules=[{"module": "pandas", "package": "pandas"}],
        )
        d = result.to_dict()
        assert d["missing_modules"] == [{"module": "pandas", "package": "pandas"}]

    def test_to_dict_omits_missing_modules_when_empty(self):
        result = SandboxResult(ok=True, stdout="OK")
        d = result.to_dict()
        assert "missing_modules" not in d


class TestRecoveryEngineClassifiesMissingDependency:
    def test_classify_action_result_detects_missing_dependency_issue_kind(self):
        from recovery_engine import RecoveryEngine

        engine = RecoveryEngine()
        task = {"id": "t1", "tool": "sandbox", "action": "execute_python", "params": {"code": "import pandas"}}
        action_result = {
            "status": "failed",
            "summary": "Script failed",
            "issue": {
                "kind": "missing_dependency",
                "message": "ModuleNotFoundError: No module named 'pandas'",
                "details": {"missing_modules": [{"module": "pandas", "package": "pandas"}]},
            },
            "data": {"missing_modules": [{"module": "pandas", "package": "pandas"}]},
        }

        classification = engine.classify_action_result(task=task, action_result=action_result, attempt=1)

        assert classification["classification"] == "dependency_install"
        assert classification["suggested_action"] == "dependency_install"
        assert classification["retryable"] is True
        assert len(classification["missing_modules"]) == 1
        assert classification["missing_modules"][0]["package"] == "pandas"

    def test_classify_detects_module_not_found_in_error_string(self):
        from recovery_engine import RecoveryEngine

        engine = RecoveryEngine()
        task = {"id": "t2", "tool": "sandbox", "action": "execute_python", "params": {"code": "import requests"}}
        error = "ModuleNotFoundError: No module named 'requests'"

        classification = engine.classify(task=task, error=error, attempt=1)

        assert classification["classification"] == "dependency_install"
        assert classification["missing_modules"][0]["package"] == "requests"

    def test_classify_does_not_detect_missing_dep_on_unrelated_error(self):
        from recovery_engine import RecoveryEngine

        engine = RecoveryEngine()
        task = {"id": "t3", "tool": "sandbox", "action": "execute_python", "params": {}}
        error = "ValueError: invalid literal for int()"

        classification = engine.classify(task=task, error=error, attempt=1)

        assert classification["classification"] != "dependency_install"


class TestToolRegistryInfersIssueKind:
    def test_infer_issue_kind_detects_missing_modules(self):
        from tool_registry import ToolRegistry

        registry = ToolRegistry()
        details = {"missing_modules": [{"module": "pandas", "package": "pandas"}]}
        assert registry._infer_issue_kind(details) == "missing_dependency"

    def test_infer_issue_kind_detects_modulenotfounderror_in_stderr(self):
        from tool_registry import ToolRegistry

        registry = ToolRegistry()
        details = {"stderr": "ModuleNotFoundError: No module named 'requests'"}
        assert registry._infer_issue_kind(details) == "missing_dependency"

    def test_infer_issue_kind_regular_error_unchanged(self):
        from tool_registry import ToolRegistry

        registry = ToolRegistry()
        details = {"error": "timeout exceeded"}
        assert registry._infer_issue_kind(details) == "timeout"
