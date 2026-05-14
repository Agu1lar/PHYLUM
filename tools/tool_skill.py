# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tool facade for the local skill manifest system."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from action_models import ActionEffects, ActionIssue, ActionResult
from skill_manifest import (
    IOSchema,
    ParamDescriptor,
    PermissionKind,
    RiskDescriptor,
    RiskLevel,
    SkillManifest,
    compute_code_checksum,
    manifest_from_dynamic_tool,
    validate_manifest,
)
from skill_registry import SkillRegistry
from skill_runner import SkillRunner
from tool_base import BaseTool

logger = logging.getLogger(__name__)


class SkillToolRequest(BaseModel):
    action: str = Field(
        ...,
        pattern="^(register|update|unregister|get|list|search|execute|verify|validate_manifest|upgrade_dynamic)$",
    )
    # Registration fields
    name: Optional[str] = None
    version: Optional[str] = None
    display_name: Optional[str] = None
    description: Optional[str] = None
    author: Optional[str] = None
    language: Optional[str] = None
    code: Optional[str] = None
    permissions: Optional[List[str]] = None
    inputs: Optional[Dict[str, Any]] = None
    outputs: Optional[Dict[str, Any]] = None
    risk: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None
    entry_point: Optional[str] = None
    dependencies: Optional[List[str]] = None
    # Execution fields
    params: Optional[Dict[str, Any]] = None
    timeout: Optional[int] = None
    # Query fields
    tag: Optional[str] = None
    permission: Optional[str] = None
    max_risk: Optional[str] = None
    query: Optional[str] = None
    # Upgrade dynamic tool
    tool_id: Optional[str] = None
    tool_spec: Optional[Dict[str, Any]] = None


class SkillTool(BaseTool):
    InputModel = SkillToolRequest
    OutputModel = ActionResult

    def __init__(self, *, default_timeout: int = 60, default_retries: int = 1):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.registry = SkillRegistry()
        self.runner = SkillRunner(self.registry)

    async def validate(self, payload: SkillToolRequest) -> None:
        if payload.action == "register" and (not payload.name or not payload.code):
            raise ValueError("register requires 'name' and 'code'")
        if payload.action == "update" and (not payload.name or not payload.code):
            raise ValueError("update requires 'name' and 'code'")
        if payload.action in ("unregister", "get", "execute", "verify") and not payload.name:
            raise ValueError(f"{payload.action} requires 'name'")
        if payload.action == "upgrade_dynamic" and not payload.tool_spec:
            raise ValueError("upgrade_dynamic requires 'tool_spec'")

    async def _run(self, payload: SkillToolRequest) -> ActionResult:
        try:
            if payload.action == "register":
                return await self._register(payload)
            if payload.action == "update":
                return await self._update(payload)
            if payload.action == "unregister":
                return await self._unregister(payload)
            if payload.action == "get":
                return await self._get(payload)
            if payload.action == "list":
                return await self._list(payload)
            if payload.action == "search":
                return await self._search(payload)
            if payload.action == "execute":
                return await self._execute(payload)
            if payload.action == "verify":
                return await self._verify(payload)
            if payload.action == "validate_manifest":
                return await self._validate_manifest(payload)
            if payload.action == "upgrade_dynamic":
                return await self._upgrade_dynamic(payload)
            return ActionResult(
                status="failed", summary=f"Unknown action: {payload.action}",
                tool="skill", action=payload.action,
            )
        except Exception as exc:
            return ActionResult(
                status="failed", summary=str(exc),
                tool="skill", action=payload.action,
                issue=ActionIssue(kind="skill_error", message=str(exc), retryable=False),
            )

    def _build_manifest(self, payload: SkillToolRequest) -> SkillManifest:
        perms = []
        for p in (payload.permissions or []):
            try:
                perms.append(PermissionKind(p))
            except ValueError:
                logger.warning("Unknown permission '%s', skipping", p)

        inputs = IOSchema()
        if payload.inputs and "params" in payload.inputs:
            inputs = IOSchema(
                params=[ParamDescriptor(**p) for p in payload.inputs["params"]],
                description=payload.inputs.get("description", ""),
            )

        outputs = IOSchema()
        if payload.outputs and "params" in payload.outputs:
            outputs = IOSchema(
                params=[ParamDescriptor(**p) for p in payload.outputs["params"]],
                description=payload.outputs.get("description", ""),
            )

        risk = RiskDescriptor()
        if payload.risk:
            risk_data = dict(payload.risk)
            if "level" in risk_data and isinstance(risk_data["level"], str):
                risk_data["level"] = RiskLevel(risk_data["level"])
            risk = RiskDescriptor(**risk_data)

        return SkillManifest(
            name=payload.name,
            version=payload.version or "1.0.0",
            display_name=payload.display_name or payload.name or "",
            description=payload.description or "",
            author=payload.author or "",
            language=payload.language or "python",
            permissions=perms,
            inputs=inputs,
            outputs=outputs,
            risk=risk,
            tags=payload.tags or [],
            entry_point=payload.entry_point or "run",
            dependencies=payload.dependencies or [],
        )

    async def _register(self, payload: SkillToolRequest) -> ActionResult:
        manifest = self._build_manifest(payload)
        result = self.registry.register(manifest, payload.code)
        issues = validate_manifest(result, code=payload.code)
        return ActionResult(
            status="succeeded",
            summary=f"Registered skill '{result.name}' v{result.version} (risk={result.effective_risk_level.value})",
            tool="skill", action="register",
            semantic_type="mutation",
            data={**result.to_dict(), "validation_warnings": issues},
            effects=ActionEffects(changed=True),
        )

    async def _update(self, payload: SkillToolRequest) -> ActionResult:
        manifest = self._build_manifest(payload)
        result = self.registry.update(manifest, payload.code)
        return ActionResult(
            status="succeeded",
            summary=f"Updated skill '{result.name}' to v{result.version}",
            tool="skill", action="update",
            semantic_type="mutation",
            data=result.to_dict(),
            effects=ActionEffects(changed=True),
        )

    async def _unregister(self, payload: SkillToolRequest) -> ActionResult:
        deleted = self.registry.unregister(payload.name)
        if not deleted:
            return ActionResult(
                status="failed", summary=f"Skill '{payload.name}' not found",
                tool="skill", action="unregister",
                issue=ActionIssue(kind="not_found", message=f"Skill '{payload.name}' not found", retryable=False),
            )
        return ActionResult(
            status="succeeded", summary=f"Unregistered skill '{payload.name}'",
            tool="skill", action="unregister",
            semantic_type="mutation",
            effects=ActionEffects(changed=True),
        )

    async def _get(self, payload: SkillToolRequest) -> ActionResult:
        manifest = self.registry.get(payload.name)
        if manifest is None:
            return ActionResult(
                status="failed", summary=f"Skill '{payload.name}' not found",
                tool="skill", action="get",
                issue=ActionIssue(kind="not_found", message=f"Skill '{payload.name}' not found", retryable=False),
            )
        return ActionResult(
            status="succeeded", summary=f"Skill '{manifest.name}' v{manifest.version}",
            tool="skill", action="get",
            data=manifest.to_dict(),
        )

    async def _list(self, payload: SkillToolRequest) -> ActionResult:
        perm = PermissionKind(payload.permission) if payload.permission else None
        risk = RiskLevel(payload.max_risk) if payload.max_risk else None
        skills = self.registry.list_skills(
            tag=payload.tag, permission=perm, max_risk=risk, language=payload.language,
        )
        return ActionResult(
            status="succeeded", summary=f"Found {len(skills)} skill(s)",
            tool="skill", action="list",
            data={"skills": skills, "count": len(skills)},
        )

    async def _search(self, payload: SkillToolRequest) -> ActionResult:
        results = self.registry.search(payload.query or "")
        return ActionResult(
            status="succeeded", summary=f"Found {len(results)} skill(s) matching '{payload.query}'",
            tool="skill", action="search",
            data={"skills": results, "count": len(results)},
        )

    async def _execute(self, payload: SkillToolRequest) -> ActionResult:
        result = await self.runner.execute(
            payload.name,
            params=payload.params,
            timeout=payload.timeout,
        )
        if not result.ok:
            return ActionResult(
                status="failed", summary=result.error or "Skill execution failed",
                tool="skill", action="execute",
                data=result.to_dict(),
                issue=ActionIssue(kind="skill_execution_failed", message=result.error or "", retryable=False),
            )
        return ActionResult(
            status="succeeded",
            summary=f"Executed skill '{result.skill_name}' v{result.skill_version} in {result.execution_time_ms}ms",
            tool="skill", action="execute",
            semantic_type="execution",
            data=result.to_dict(),
            effects=ActionEffects(changed=True),
        )

    async def _verify(self, payload: SkillToolRequest) -> ActionResult:
        valid = self.registry.verify_integrity(payload.name)
        return ActionResult(
            status="succeeded",
            summary=f"Skill '{payload.name}' integrity: {'PASS' if valid else 'FAIL'}",
            tool="skill", action="verify",
            data={"name": payload.name, "integrity_valid": valid},
        )

    async def _validate_manifest(self, payload: SkillToolRequest) -> ActionResult:
        manifest = self._build_manifest(payload)
        issues = validate_manifest(manifest, code=payload.code)
        return ActionResult(
            status="succeeded",
            summary=f"Manifest validation: {len(issues)} issue(s)" if issues else "Manifest is valid",
            tool="skill", action="validate_manifest",
            data={"issues": issues, "effective_risk_level": manifest.effective_risk_level.value, "requires_approval": manifest.requires_approval},
        )

    async def _upgrade_dynamic(self, payload: SkillToolRequest) -> ActionResult:
        manifest = manifest_from_dynamic_tool(payload.tool_spec)
        code = payload.tool_spec.get("code", "")
        if not code:
            return ActionResult(
                status="failed", summary="Dynamic tool has no code to upgrade",
                tool="skill", action="upgrade_dynamic",
            )
        result = self.registry.register(manifest, code, overwrite=True)
        return ActionResult(
            status="succeeded",
            summary=f"Upgraded dynamic tool '{payload.tool_spec.get('name')}' to skill '{result.name}' v{result.version}",
            tool="skill", action="upgrade_dynamic",
            semantic_type="mutation",
            data=result.to_dict(),
            effects=ActionEffects(changed=True),
        )
