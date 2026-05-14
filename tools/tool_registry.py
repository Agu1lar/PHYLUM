# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

import json
import traceback
from typing import Any, Dict

from pydantic import BaseModel, ValidationError

from action_models import ActionEffects, ActionIssue, ActionResult
from canonical_tools import action_metadata, supported_tools
from tool_artifact import ArtifactTool
from tool_browser import BrowserTool
from tool_desktop import DesktopTool
from tool_driver import DriverManagerTool
from tool_document_intelligence import DocumentIntelligenceTool
from tool_dynamic import DynamicToolTool
from tool_env import EnvManagerTool
from tool_filesystem import FileSystemTool
from tool_memory import MemoryTool
from tool_office import OfficeTool
from tool_os import OSIntrospectionTool
from tool_package import PackageManagerTool
from tool_sandbox import SandboxTool
from tool_share_discovery import ShareDiscoveryTool
from tool_shell import ShellTool
from tool_software import SoftwareInventoryTool
from tool_web import WebTool
from tool_skill import SkillTool
from tool_visual import VisualTool
from tool_codebase_map import CodebaseMapTool
from tool_execution_economics import ExecutionEconomicsTool
from tool_test_diagnostic import TestDiagnosticTool
from tool_patch_planner import PatchPlannerTool
from tool_heartbeat import HeartbeatTool
from tool_windows_ui import WindowsUiTool


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return json.loads(value.json())
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


class ToolRegistry:
    def __init__(self):
        self.tools = {
            "shell": ShellTool(),
            "filesystem": FileSystemTool(),
            "memory": MemoryTool(),
            "browser": BrowserTool(),
            "web": WebTool(),
            "package_manager": PackageManagerTool(),
            "software_inventory": SoftwareInventoryTool(),
            "env_manager": EnvManagerTool(),
            "driver_manager": DriverManagerTool(),
            "os": OSIntrospectionTool(),
            "desktop": DesktopTool(),
            "windows_ui": WindowsUiTool(),
            "share_discovery": ShareDiscoveryTool(),
            "document_intelligence": DocumentIntelligenceTool(),
            "office": OfficeTool(),
            "sandbox": SandboxTool(),
            "artifact": ArtifactTool(),
            "dynamic_tool": DynamicToolTool(),
            "visual": VisualTool(),
            "skill": SkillTool(),
            "codebase_map": CodebaseMapTool(),
            "execution_economics": ExecutionEconomicsTool(),
            "test_diagnostic": TestDiagnosticTool(),
            "patch_planner": PatchPlannerTool(),
            "heartbeat": HeartbeatTool(),
        }

    def supports(self, tool_name: str) -> bool:
        return tool_name in self.tools and tool_name in supported_tools()

    def build_payload(self, task: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = task["tool"]
        payload = dict(task.get("params", {}))
        if tool_name == "shell":
            payload.setdefault("shell", "powershell")
        else:
            payload["action"] = task["action"]
        if tool_name == "filesystem":
            payload["allow_outside_sandbox"] = bool(task.get("approval_granted"))
        return payload

    async def execute(self, task: Dict[str, Any], *, cancel_event=None) -> Dict[str, Any]:
        tool_name = task["tool"]
        tool = self.tools[tool_name]
        payload = self.build_payload(task)
        try:
            raw_result = await tool.run(payload, cancel_event=cancel_event)
            raw_payload = _jsonable(raw_result)
            action_result = self.normalize_result(
                tool_name=tool_name,
                action=task["action"],
                params=task.get("params", {}),
                raw_result=raw_payload,
            )
        except Exception as exc:
            raw_payload = self._exception_payload(exc)
            action_result = self._action_result_from_exception(
                tool_name=tool_name,
                action=task["action"],
                params=task.get("params", {}),
                exc=exc,
            )
        return {
            "tool": tool_name,
            "action": task["action"],
            "task_id": task["id"],
            "tool_result": raw_payload,
            "action_result": action_result,
        }

    def normalize_result(self, *, tool_name: str, action: str, params: Dict[str, Any], raw_result: Dict[str, Any]) -> Dict[str, Any]:
        if {"status", "summary", "tool", "action"}.issubset(raw_result.keys()):
            return raw_result

        metadata = action_metadata(tool_name, action)
        target = self._target_from_params(params, metadata)

        if "structured" in raw_result:
            structured = raw_result.get("structured") or {}
            command_result = structured.get("result") or {}
            issue = None
            if not structured.get("ok"):
                message = self._shell_failure_message(structured, command_result)
                issue = ActionIssue(
                    kind="command_failed" if structured.get("error") else "unknown",
                    code=structured.get("error"),
                    message=message,
                    retryable=structured.get("error") in {"timeout"},
                    details={"risk": structured.get("risk"), "meta": structured.get("meta")},
                )
            return ActionResult(
                status="succeeded" if structured.get("ok") else "failed",
                summary="Command executed successfully." if structured.get("ok") else (issue.message if issue else "Command failed."),
                tool=tool_name,
                action=action,
                semantic_type=metadata.get("semantic_type", "command"),
                target=target,
                data={"stdout": command_result.get("stdout"), "stderr": command_result.get("stderr")},
                effects=ActionEffects(changed=bool(metadata.get("mutates_state")) and bool(structured.get("ok"))),
                issue=issue,
                diagnostics={"raw": raw_result},
            ).dict()

        if "ok" in raw_result or "success" in raw_result:
            succeeded = bool(raw_result.get("ok", raw_result.get("success")))
            details = raw_result.get("details") or {}
            summary = raw_result.get("message") or f"{tool_name}.{action}"
            issue = None
            status = "succeeded" if succeeded else "failed"

            if not succeeded:
                message = details.get("error") or details.get("stderr") or raw_result.get("message") or f"{tool_name}.{action} failed"
                issue = ActionIssue(
                    kind=self._infer_issue_kind(details),
                    code=details.get("error"),
                    message=message,
                    retryable="timeout" in str(message).lower(),
                    details=details,
                )
                summary = message

            return ActionResult(
                status=status,
                summary=summary,
                tool=tool_name,
                action=action,
                semantic_type=metadata.get("semantic_type", "inspection"),
                target=target,
                data=details if isinstance(details, dict) else {"value": details},
                effects=ActionEffects(changed=bool(metadata.get("mutates_state")) and succeeded),
                issue=issue,
                diagnostics={"raw": raw_result},
            ).dict()

        return ActionResult(
            status="partial",
            summary=f"{tool_name}.{action} returned an untyped result.",
            tool=tool_name,
            action=action,
            semantic_type=metadata.get("semantic_type", "inspection"),
            target=target,
            data=raw_result if isinstance(raw_result, dict) else {"value": raw_result},
            effects=ActionEffects(changed=False),
            issue=ActionIssue(kind="untyped_result", message="Tool returned data without a semantic envelope."),
            diagnostics={"raw": raw_result},
        ).dict()

    def _shell_failure_message(self, structured: Dict[str, Any], command_result: Dict[str, Any]) -> str:
        error = str(structured.get("error") or "").strip()
        stderr = str(command_result.get("stderr") or "").strip()
        return_code = command_result.get("returncode")

        if stderr:
            return stderr
        if error == "timeout":
            return "O comando excedeu o tempo limite configurado."
        if error == "cancelled":
            return "O comando foi cancelado antes de terminar."
        if error == "non-zero-exit":
            if return_code is not None:
                return f"O comando terminou com codigo de saida {return_code}."
            return "O comando terminou com erro."
        if error:
            return error
        if return_code is not None:
            return f"O comando terminou com codigo de saida {return_code}."
        return "O comando nao foi concluido com sucesso."

    def _root_exception(self, exc: Exception) -> Exception:
        root = exc
        seen = set()
        while root not in seen and getattr(root, "__cause__", None) is not None:
            seen.add(root)
            root = root.__cause__
        return root

    def _exception_payload(self, exc: Exception) -> Dict[str, Any]:
        root = self._root_exception(exc)
        return {
            "success": False,
            "message": str(root).strip() or str(exc).strip() or exc.__class__.__name__,
            "details": {
                "error": str(root).strip() or root.__class__.__name__,
                "exception_type": root.__class__.__name__,
                "exception_repr": repr(root),
                "traceback": traceback.format_exc(),
            },
        }

    def _action_result_from_exception(self, *, tool_name: str, action: str, params: Dict[str, Any], exc: Exception) -> Dict[str, Any]:
        metadata = action_metadata(tool_name, action)
        target = self._target_from_params(params, metadata)
        root = self._root_exception(exc)
        message = str(root).strip() or str(exc).strip() or root.__class__.__name__
        issue_kind = self._issue_kind_for_exception(root)
        return ActionResult(
            status="failed",
            summary=message,
            tool=tool_name,
            action=action,
            semantic_type=metadata.get("semantic_type", "inspection"),
            target=target,
            data={},
            effects=ActionEffects(changed=False),
            issue=ActionIssue(
                kind=issue_kind,
                code=root.__class__.__name__,
                message=message,
                retryable=issue_kind in {"timeout", "tool_internal"},
                details={"exception_type": root.__class__.__name__},
            ),
            diagnostics={"exception_type": root.__class__.__name__},
        ).dict()

    def _target_from_params(self, params: Dict[str, Any], metadata: Dict[str, Any]) -> Dict[str, Any]:
        target: Dict[str, Any] = {}
        for field in metadata.get("target_fields") or []:
            value = params.get(field)
            if value is not None:
                target[field] = value
        return target

    def _infer_issue_kind(self, details: Dict[str, Any]) -> str:
        text = " ".join(str(details.get(key, "")) for key in ("error", "stderr")).lower()
        if "timeout" in text:
            return "timeout"
        if "approval rejected" in text:
            return "approval_rejected"
        if "not found" in text:
            return "not_found"
        if "validation" in text:
            return "validation"
        return "tool_failed"

    def _issue_kind_for_exception(self, exc: Exception) -> str:
        if isinstance(exc, ValidationError):
            return "validation"
        if isinstance(exc, FileNotFoundError):
            return "not_found"
        if isinstance(exc, TimeoutError):
            return "timeout"
        if isinstance(exc, ValueError):
            return "validation"
        return "tool_internal"
