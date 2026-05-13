"""Comprehensive tests for Phase 4: Release gates de Agente Autonomo.

Covers:
- ExecutionStrategy: decide_execution_mode, suggest_script_recovery,
  generate_orchestration_script, detect_tool_gap
- RecoveryEngine: script_recovery classification integration
- ActionExecutor: script recovery fallback execution path
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from execution_strategy import ExecutionStrategy
from recovery_engine import RecoveryEngine


@pytest.fixture
def strategy():
    return ExecutionStrategy()


@pytest.fixture
def recovery():
    return RecoveryEngine()


# ─── ExecutionStrategy: decide_execution_mode ──────────────────────


class TestDecideExecutionMode:
    def test_internal_tools_stay_internal(self, strategy):
        for tool in ("artifact", "sandbox", "memory", "dynamic_tool"):
            result = strategy.decide_execution_mode(tool=tool, action="any", params={})
            assert result["mode"] == "internal"

    def test_desktop_tool_stays_desktop(self, strategy):
        result = strategy.decide_execution_mode(tool="desktop", action="open_app", params={"app_name": "notepad"})
        assert result["mode"] == "desktop"

    def test_office_excel_read_internalized(self, strategy):
        result = strategy.decide_execution_mode(
            tool="office", action="excel_read_range",
            params={"path": "C:\\data\\report.xlsx"},
        )
        assert result["mode"] == "internal"
        assert result["alternative"] is not None
        assert result["alternative"]["tool"] == "sandbox"

    def test_office_outlook_search_internalized(self, strategy):
        result = strategy.decide_execution_mode(
            tool="office", action="outlook_search_messages",
            params={"query": "invoice"},
        )
        assert result["mode"] == "internal"
        assert result["alternative"]["tool"] == "sandbox"

    def test_office_word_find_internalized(self, strategy):
        result = strategy.decide_execution_mode(
            tool="office", action="word_find_text",
            params={"path": "C:\\docs\\contract.docx"},
        )
        assert result["mode"] == "internal"
        assert result["alternative"]["tool"] == "artifact"

    def test_csv_file_goes_internal(self, strategy):
        result = strategy.decide_execution_mode(
            tool="filesystem", action="read",
            params={"path": "C:\\data\\sales.csv"},
        )
        assert result["mode"] == "internal"
        assert result["alternative"]["tool"] == "artifact"

    def test_json_file_goes_internal(self, strategy):
        result = strategy.decide_execution_mode(
            tool="filesystem", action="read",
            params={"path": "C:\\config.json"},
        )
        assert result["mode"] == "internal"

    def test_pdf_file_goes_internal_artifact(self, strategy):
        result = strategy.decide_execution_mode(
            tool="filesystem", action="read",
            params={"path": "C:\\docs\\report.pdf"},
        )
        assert result["mode"] == "internal"
        assert result["alternative"]["tool"] == "artifact"

    def test_pptx_file_goes_desktop(self, strategy):
        result = strategy.decide_execution_mode(
            tool="filesystem", action="read",
            params={"path": "C:\\presentations\\deck.pptx"},
        )
        assert result["mode"] == "desktop"

    def test_native_shell_stays_native(self, strategy):
        result = strategy.decide_execution_mode(
            tool="shell", action="run",
            params={"command": "Get-Date"},
        )
        assert result["mode"] == "native"

    def test_missing_tool_generates_script(self, strategy):
        result = strategy.decide_execution_mode(
            tool="nonexistent_tool", action="do_thing",
            params={},
            available_tools=["shell", "filesystem"],
        )
        assert result["mode"] in ("script", "native")

    def test_no_path_no_special_handling(self, strategy):
        result = strategy.decide_execution_mode(
            tool="web", action="search_web",
            params={"query": "test"},
        )
        assert result["mode"] == "native"


# ─── ExecutionStrategy: suggest_script_recovery ────────────────────


class TestSuggestScriptRecovery:
    def test_office_com_failure_gets_script(self, strategy):
        task = {"tool": "office", "action": "excel_read_range", "params": {"path": "C:\\data.xlsx"}}
        result = strategy.suggest_script_recovery(task=task, error="COM object unavailable", attempt=2)
        assert result is not None
        assert result["classification"] == "script_recovery"
        assert result["script"]["tool"] == "sandbox"

    def test_office_outlook_com_failure(self, strategy):
        task = {"tool": "office", "action": "outlook_search_messages", "params": {"limit": 10}}
        result = strategy.suggest_script_recovery(task=task, error="COM dispatch failed", attempt=1)
        assert result is not None
        assert result["script"]["tool"] == "sandbox"

    def test_filesystem_read_failure(self, strategy):
        task = {"tool": "filesystem", "action": "read", "params": {"path": "C:\\data.txt"}}
        result = strategy.suggest_script_recovery(task=task, error="access denied", attempt=2)
        assert result is not None
        assert result["script"]["tool"] == "sandbox"

    def test_browser_playwright_failure(self, strategy):
        task = {"tool": "browser", "action": "open_page", "params": {"url": "https://example.com"}}
        result = strategy.suggest_script_recovery(task=task, error="Playwright browser not installed", attempt=1)
        assert result is not None
        assert result["script"]["tool"] == "sandbox"

    def test_document_extract_failure(self, strategy):
        task = {"tool": "document_intelligence", "action": "extract_text", "params": {"path": "C:\\doc.pdf"}}
        result = strategy.suggest_script_recovery(task=task, error="extraction failed", attempt=1)
        assert result is not None
        assert result["script"]["tool"] == "artifact"

    def test_share_access_failure(self, strategy):
        task = {"tool": "share_discovery", "action": "inspect_share", "params": {"path": "\\\\server\\share"}}
        result = strategy.suggest_script_recovery(task=task, error="network access denied", attempt=1)
        assert result is not None
        assert result["script"]["tool"] == "sandbox"

    def test_desktop_open_app_failure(self, strategy):
        task = {"tool": "desktop", "action": "open_app", "params": {"app_name": "notepad"}}
        result = strategy.suggest_script_recovery(task=task, error="app launch failed", attempt=1)
        assert result is not None
        assert result["script"]["tool"] == "sandbox"
        assert result["script"]["action"] == "execute_powershell"

    def test_package_manager_install_failure(self, strategy):
        task = {"tool": "package_manager", "action": "install", "params": {"package": "git"}}
        result = strategy.suggest_script_recovery(task=task, error="choco not found", attempt=1)
        assert result is not None
        assert result["script"]["action"] == "execute_powershell"

    def test_web_search_failure(self, strategy):
        task = {"tool": "web", "action": "search_web", "params": {"query": "test query"}}
        result = strategy.suggest_script_recovery(task=task, error="web search failed", attempt=1)
        assert result is not None
        assert result["script"]["tool"] == "sandbox"

    def test_approval_rejected_no_recovery(self, strategy):
        task = {"tool": "shell", "action": "run", "params": {"command": "rm -rf /"}}
        result = strategy.suggest_script_recovery(task=task, error="approval rejected", attempt=1)
        assert result is None

    def test_blocked_by_policy_no_recovery(self, strategy):
        task = {"tool": "shell", "action": "run", "params": {}}
        result = strategy.suggest_script_recovery(task=task, error="blocked by policy", attempt=1)
        assert result is None

    def test_unknown_tool_no_recovery(self, strategy):
        task = {"tool": "unknown", "action": "do_thing", "params": {}}
        result = strategy.suggest_script_recovery(task=task, error="tool not found", attempt=1)
        assert result is None


# ─── ExecutionStrategy: generate_orchestration_script ──────────────


class TestGenerateOrchestrationScript:
    def test_empty_sources_returns_none(self, strategy):
        result = strategy.generate_orchestration_script(goal="test", data_sources=[])
        assert result is None

    def test_email_plus_excel_generates_script(self, strategy):
        sources = [
            {"type": "outlook_emails", "key": "emails", "count": 10},
            {"type": "excel", "key": "spreadsheet", "path": "C:\\data.xlsx"},
        ]
        result = strategy.generate_orchestration_script(
            goal="read emails and cross with spreadsheet",
            data_sources=sources,
            output_format="report",
        )
        assert result is not None
        assert result["tool"] == "sandbox"
        assert result["action"] == "execute_python"
        assert "win32com" in result["params"]["code"]
        assert "openpyxl" in result["params"]["code"]

    def test_csv_plus_json_generates_script(self, strategy):
        sources = [
            {"type": "csv", "key": "sales", "path": "C:\\sales.csv"},
            {"type": "json", "key": "config", "path": "C:\\config.json"},
        ]
        result = strategy.generate_orchestration_script(
            goal="analyze sales data",
            data_sources=sources,
            output_format="json",
        )
        assert result is not None
        assert "csv" in result["params"]["code"]
        assert "json" in result["params"]["code"]

    def test_text_source_generates_reader(self, strategy):
        sources = [{"type": "text", "key": "readme", "path": "C:\\README.md"}]
        result = strategy.generate_orchestration_script(goal="read readme", data_sources=sources)
        assert result is not None
        assert "open(" in result["params"]["code"]

    def test_output_path_included(self, strategy):
        sources = [{"type": "csv", "key": "data", "path": "C:\\data.csv"}]
        result = strategy.generate_orchestration_script(
            goal="export data",
            data_sources=sources,
            output_format="report",
            output_path="C:\\output\\report.txt",
        )
        assert result is not None
        assert "C:\\output\\report.txt" in result["params"]["code"]

    def test_csv_output_format(self, strategy):
        sources = [{"type": "csv", "key": "data", "path": "C:\\data.csv"}]
        result = strategy.generate_orchestration_script(
            goal="export csv",
            data_sources=sources,
            output_format="csv",
        )
        assert result is not None
        assert "csv" in result["params"]["code"].lower()

    def test_unsupported_source_type_handled(self, strategy):
        sources = [{"type": "database", "key": "db", "path": "C:\\test.db"}]
        result = strategy.generate_orchestration_script(goal="read db", data_sources=sources)
        assert result is not None
        assert "unsupported source type" in result["params"]["code"]


# ─── ExecutionStrategy: detect_tool_gap ────────────────────────────


class TestDetectToolGap:
    def test_existing_tool_no_gap(self, strategy):
        result = strategy.detect_tool_gap(tool="shell", action="run", available_tools=["shell", "filesystem"])
        assert result is None

    def test_missing_tool_detected(self, strategy):
        result = strategy.detect_tool_gap(tool="nonexistent", action="do", available_tools=["shell"])
        assert result is not None
        assert result["gap_detected"] is True
        assert result["missing_tool"] == "nonexistent"

    def test_known_gap_email_read(self, strategy):
        result = strategy.detect_tool_gap(tool="email", action="read", available_tools=["shell"])
        assert result is not None
        assert result["suggestion"] is not None
        assert result["suggestion"]["tool"] == "sandbox"

    def test_known_gap_report_generate(self, strategy):
        result = strategy.detect_tool_gap(tool="report", action="generate", available_tools=["shell"])
        assert result is not None
        assert result["suggestion"] is not None

    def test_unknown_gap_no_suggestion(self, strategy):
        result = strategy.detect_tool_gap(tool="quantum_computer", action="teleport", available_tools=["shell"])
        assert result is not None
        assert result["suggestion"] is None


# ─── RecoveryEngine: script_recovery integration ───────────────────


class TestRecoveryEngineScriptRecovery:
    def test_classify_action_result_script_recovery_on_terminal(self, recovery):
        task = {"tool": "office", "action": "excel_read_range", "params": {"path": "C:\\data.xlsx"}}
        action_result = {
            "status": "failed",
            "issue": {"kind": "office_com_unavailable", "message": "COM not available"},
        }
        result = recovery.classify_action_result(task=task, action_result=action_result, attempt=2, max_attempts=2)
        assert result["classification"] in ("replan_required", "script_recovery")

    def test_classify_terminal_office_failure_gets_script(self, recovery):
        task = {"tool": "office", "action": "excel_read_range", "params": {"path": "C:\\test.xlsx"}}
        result = recovery.classify(task=task, error="COM object not found", attempt=3, max_attempts=2)
        assert result["classification"] == "script_recovery"
        assert result["suggested_action"] == "execute_script"
        assert result["script"]["tool"] == "sandbox"

    def test_classify_terminal_filesystem_failure_gets_script(self, recovery):
        task = {"tool": "filesystem", "action": "read", "params": {"path": "C:\\data.txt"}}
        result = recovery.classify(task=task, error="some weird error", attempt=3, max_attempts=2)
        assert result["classification"] == "script_recovery"
        assert result["script"]["tool"] == "sandbox"

    def test_classify_browser_failure_gets_script(self, recovery):
        task = {"tool": "browser", "action": "open_page", "params": {"url": "https://test.com"}}
        result = recovery.classify(task=task, error="playwright browser crashed", attempt=3, max_attempts=2)
        assert result["classification"] == "script_recovery"

    def test_classify_retryable_does_not_trigger_script(self, recovery):
        task = {"tool": "shell", "action": "run", "params": {"command": "test"}}
        result = recovery.classify(task=task, error="timeout occurred", attempt=1, max_attempts=3)
        assert result["classification"] == "retryable"
        assert result.get("script") is None

    def test_classify_approval_rejected_no_script(self, recovery):
        task = {"tool": "shell", "action": "run", "params": {}}
        result = recovery.classify(task=task, error="approval rejected by user", attempt=1, max_attempts=2)
        assert result["classification"] == "blocked_by_policy"
        assert result.get("script") is None

    def test_classify_needs_user_no_script(self, recovery):
        task = {
            "tool": "desktop", "action": "open_app", "params": {},
            "result": {"action_result": {"issue": {"kind": "ambiguous_match", "user_action_required": "select_candidate"}}},
        }
        result = recovery.classify(task=task, error="ambiguous match found", attempt=1, max_attempts=2)
        assert result["classification"] == "needs_user"

    def test_classify_action_result_terminal_filesystem_gets_script(self, recovery):
        task = {"tool": "filesystem", "action": "list", "params": {"path": "C:\\test"}}
        action_result = {"status": "failed", "issue": {"kind": "", "message": "unknown error"}, "summary": "operation failed"}
        result = recovery.classify_action_result(task=task, action_result=action_result, attempt=3, max_attempts=2)
        assert result["classification"] == "script_recovery"
        assert "script" in result

    def test_classify_web_search_failure_gets_script(self, recovery):
        task = {"tool": "web", "action": "search_web", "params": {"query": "test"}}
        result = recovery.classify(task=task, error="web search provider error", attempt=3, max_attempts=2)
        assert result["classification"] == "script_recovery"


# ─── Integration: strategy redirects tasks ──────────────────────────


class TestExecutionStrategyRedirection:
    def test_excel_redirect_has_openpyxl(self, strategy):
        decision = strategy.decide_execution_mode(
            tool="office", action="excel_read_range",
            params={"path": "C:\\test.xlsx"},
        )
        assert decision["mode"] == "internal"
        alt = decision["alternative"]
        assert alt["tool"] == "sandbox"
        assert "openpyxl" in alt["params"]["code"]

    def test_outlook_redirect_has_win32com(self, strategy):
        decision = strategy.decide_execution_mode(
            tool="office", action="outlook_search_messages",
            params={"query": "invoices"},
        )
        assert decision["mode"] == "internal"
        alt = decision["alternative"]
        assert "win32com" in alt["params"]["code"]

    def test_docx_redirect_to_artifact(self, strategy):
        decision = strategy.decide_execution_mode(
            tool="office", action="word_find_text",
            params={"path": "C:\\docs\\report.docx"},
        )
        assert decision["mode"] == "internal"
        alt = decision["alternative"]
        assert alt["tool"] == "artifact"
        assert alt["action"] == "load"


# ─── Orchestration script structure ─────────────────────────────────


class TestOrchestrationScriptStructure:
    def test_script_has_data_collection(self, strategy):
        sources = [{"type": "csv", "key": "data", "path": "C:\\data.csv"}]
        result = strategy.generate_orchestration_script(goal="test", data_sources=sources)
        assert "collected" in result["params"]["code"]
        assert "Data collection phase" in result["params"]["code"]

    def test_script_has_processing(self, strategy):
        sources = [
            {"type": "csv", "key": "a", "path": "C:\\a.csv"},
            {"type": "json", "key": "b", "path": "C:\\b.json"},
        ]
        result = strategy.generate_orchestration_script(goal="test", data_sources=sources)
        code = result["params"]["code"]
        assert "Processing phase" in code
        assert "cross_reference" in code.lower() or "Cross-reference" in code

    def test_script_has_output(self, strategy):
        sources = [{"type": "text", "key": "readme", "path": "C:\\README.md"}]
        result = strategy.generate_orchestration_script(goal="test", data_sources=sources)
        assert "Output phase" in result["params"]["code"]

    def test_timeout_set(self, strategy):
        sources = [{"type": "csv", "key": "data", "path": "C:\\data.csv"}]
        result = strategy.generate_orchestration_script(goal="test", data_sources=sources)
        assert result["params"]["timeout"] == 120


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
