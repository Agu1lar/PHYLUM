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
    manifest_from_dynamic_tool,
    validate_manifest,
)
from skill_registry import SkillRegistry, SkillRegistryError
from skill_runner import SkillRunner
from skill_signing import SkillProvenance, build_provenance
from tool_base import BaseTool

logger = logging.getLogger(__name__)

_SKILL_ACTIONS = (
    "register|update|unregister|get|list|search|discover_objective|"
    "execute|verify|validate_manifest|upgrade_dynamic|approve_trust|verify_trust|"
    "evaluate|export_package|import_package"
)


class SkillToolRequest(BaseModel):
    action: str = Field(..., pattern=f"^({_SKILL_ACTIONS})$")
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
    params: Optional[Dict[str, Any]] = None
    timeout: Optional[int] = None
    tag: Optional[str] = None
    permission: Optional[str] = None
    max_risk: Optional[str] = None
    query: Optional[str] = None
    objective: Optional[str] = None
    limit: Optional[int] = Field(None, ge=1, le=20)
    min_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    trusted_only: Optional[bool] = None
    evaluated_only: Optional[bool] = None
    agent_available_only: Optional[bool] = None
    package_path: Optional[str] = None
    skill_names: Optional[List[str]] = None
    as_zip: Optional[bool] = None
    overwrite: Optional[bool] = None
    run_evaluation: Optional[bool] = None
    tests: Optional[List[Dict[str, Any]]] = None
    min_tests: Optional[int] = None
    tool_id: Optional[str] = None
    tool_spec: Optional[Dict[str, Any]] = None
    review_notes: Optional[str] = None


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
        if payload.action in ("unregister", "get", "execute", "verify", "approve_trust", "verify_trust", "evaluate") and not payload.name:
            raise ValueError(f"{payload.action} requires 'name'")
        if payload.action in ("export_package", "import_package") and not payload.package_path:
            raise ValueError(f"{payload.action} requires 'package_path'")
        if payload.action == "discover_objective" and not payload.objective:
            raise ValueError("discover_objective requires 'objective'")
        if payload.action == "upgrade_dynamic" and not payload.tool_spec:
            raise ValueError("upgrade_dynamic requires 'tool_spec'")

    async def _run(self, payload: SkillToolRequest) -> ActionResult:
        try:
            handlers = {
                "register": self._register,
                "update": self._update,
                "unregister": self._unregister,
                "get": self._get,
                "list": self._list,
                "search": self._search,
                "discover_objective": self._discover_objective,
                "execute": self._execute,
                "verify": self._verify,
                "verify_trust": self._verify_trust,
                "validate_manifest": self._validate_manifest,
                "upgrade_dynamic": self._upgrade_dynamic,
                "approve_trust": self._approve_trust,
                "evaluate": self._evaluate,
                "export_package": self._export_package,
                "import_package": self._import_package,
            }
            handler = handlers.get(payload.action)
            if handler is None:
                return ActionResult(
                    status="failed", summary=f"Unknown action: {payload.action}",
                    tool="skill", action=payload.action,
                )
            return await handler(payload)
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

    def _configure_runner_for_skill(self, manifest: SkillManifest) -> None:
        """Grant declared permissions so execute can proceed."""
        self.runner.granted_capabilities.clear()
        self.runner.grant(*manifest.permissions)

    def _save_tests_if_any(self, name: str, payload: SkillToolRequest) -> None:
        if not payload.tests:
            return
        from skill_evaluation import SkillTestCase, save_skill_tests

        cases = [SkillTestCase.from_dict(t) for t in payload.tests]
        save_skill_tests(
            self.registry.skills_dir / name,
            cases,
            min_tests=payload.min_tests,
        )

    async def _register(self, payload: SkillToolRequest) -> ActionResult:
        manifest = self._build_manifest(payload)
        provenance = build_provenance(
            source="agent_register",
            registered_by=payload.author or "agent",
            notes="Registered via skill tool",
        )
        result = self.registry.register(
            manifest, payload.code, provenance=provenance, trust_on_import=True,
        )
        self._save_tests_if_any(result.name, payload)
        issues = validate_manifest(result, code=payload.code)
        eval_data: Dict[str, Any] = {}
        if payload.tests and payload.run_evaluation is not False:
            from skill_evaluation import SkillEvaluator

            report = await SkillEvaluator(self.registry, use_subprocess=False).evaluate(result.name)
            eval_data = {"evaluation": report.to_dict()}
        return ActionResult(
            status="succeeded",
            summary=f"Registered skill '{result.name}' v{result.version} (signed, risk={result.effective_risk_level.value})",
            tool="skill", action="register",
            semantic_type="mutation",
            data={**result.to_dict(), "validation_warnings": issues, **eval_data},
            effects=ActionEffects(changed=True),
        )

    async def _update(self, payload: SkillToolRequest) -> ActionResult:
        manifest = self._build_manifest(payload)
        result = self.registry.update(manifest, payload.code)
        return ActionResult(
            status="succeeded",
            summary=f"Updated skill '{result.name}' to v{result.version} (re-signed)",
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
            tag=payload.tag,
            permission=perm,
            max_risk=risk,
            language=payload.language,
            agent_available_only=bool(payload.agent_available_only),
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

    async def _discover_objective(self, payload: SkillToolRequest) -> ActionResult:
        matches = self.registry.discover_by_objective(
            payload.objective,
            limit=payload.limit or 5,
            min_score=payload.min_score if payload.min_score is not None else 0.12,
            trusted_only=True if payload.trusted_only is None else bool(payload.trusted_only),
            evaluated_only=True if payload.evaluated_only is None else bool(payload.evaluated_only),
        )
        recommended = [m for m in matches if m.get("recommended")]
        if matches:
            top = matches[0]
            summary = (
                f"Found {len(matches)} skill(s) for objective. "
                f"Best match: '{top['name']}' (score={top['score']}). "
            )
            if recommended:
                summary += f"{len(recommended)} recommended — prefer skill.execute over new scripts."
            else:
                summary += "No strong match; consider sandbox or dynamic_tool only if none fit."
        else:
            summary = "No installed skills match this objective. You may create a new skill or sandbox script."
        return ActionResult(
            status="succeeded",
            summary=summary,
            tool="skill", action="discover_objective",
            data={
                "objective": payload.objective,
                "matches": matches,
                "count": len(matches),
                "recommended": recommended,
            },
        )

    async def _execute(self, payload: SkillToolRequest) -> ActionResult:
        manifest = self.registry.get(payload.name)
        if manifest is None:
            return ActionResult(
                status="failed", summary=f"Skill '{payload.name}' not found",
                tool="skill", action="execute",
                issue=ActionIssue(kind="not_found", message=f"Skill '{payload.name}' not found", retryable=False),
            )
        self._configure_runner_for_skill(manifest)
        result = await self.runner.execute(
            payload.name,
            params=payload.params,
            timeout=payload.timeout,
        )
        if not result.ok:
            issue_kind = "skill_trust_review" if "quarantined" in (result.error or "").lower() or "signature" in (result.error or "").lower() else "skill_execution_failed"
            return ActionResult(
                status="failed", summary=result.error or "Skill execution failed",
                tool="skill", action="execute",
                data=result.to_dict(),
                issue=ActionIssue(kind=issue_kind, message=result.error or "", retryable=issue_kind != "skill_trust_review"),
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
        sig = self.registry.verify_signature(payload.name)
        return ActionResult(
            status="succeeded",
            summary=f"Skill '{payload.name}' integrity={'PASS' if valid else 'FAIL'}, signature={'PASS' if sig else 'FAIL'}",
            tool="skill", action="verify",
            data={"name": payload.name, "code_integrity": valid, "signature_valid": sig},
        )

    async def _verify_trust(self, payload: SkillToolRequest) -> ActionResult:
        trust = self.registry.verify_trust(payload.name)
        ok = trust.get("ok", False)
        return ActionResult(
            status="succeeded",
            summary=f"Skill '{payload.name}' trust: {'OK' if ok else trust.get('reason', 'failed')}",
            tool="skill", action="verify_trust",
            data=trust,
        )

    async def _approve_trust(self, payload: SkillToolRequest) -> ActionResult:
        try:
            result = self.registry.approve_trust(
                payload.name, notes=payload.review_notes or "Approved via skill tool",
            )
        except SkillRegistryError as exc:
            return ActionResult(
                status="failed", summary=str(exc),
                tool="skill", action="approve_trust",
                issue=ActionIssue(kind="not_found", message=str(exc), retryable=False),
            )
        return ActionResult(
            status="succeeded",
            summary=f"Skill '{result.name}' approved and re-signed (trusted)",
            tool="skill", action="approve_trust",
            semantic_type="mutation",
            data=result.to_dict(),
            effects=ActionEffects(changed=True),
        )

    async def _validate_manifest(self, payload: SkillToolRequest) -> ActionResult:
        manifest = self._build_manifest(payload)
        issues = validate_manifest(manifest, code=payload.code)
        return ActionResult(
            status="succeeded",
            summary=f"Manifest validation: {len(issues)} issue(s)" if issues else "Manifest is valid",
            tool="skill", action="validate_manifest",
            data={
                "issues": issues,
                "effective_risk_level": manifest.effective_risk_level.value,
                "requires_approval": manifest.requires_approval,
            },
        )

    async def _evaluate(self, payload: SkillToolRequest) -> ActionResult:
        from skill_evaluation import SkillEvaluator

        report = await SkillEvaluator(self.registry, use_subprocess=False).evaluate(payload.name)
        status = "succeeded" if report.passed else "failed"
        return ActionResult(
            status=status,
            summary=(
                f"Skill '{payload.name}' evaluation: {report.status} "
                f"({report.passed_count}/{report.passed_count + report.failed_count} tests)"
            ),
            tool="skill",
            action="evaluate",
            data=report.to_dict(),
            issue=None if report.passed else ActionIssue(
                kind="skill_evaluation_failed",
                message="One or more skill tests failed",
                retryable=True,
            ),
        )

    async def _export_package(self, payload: SkillToolRequest) -> ActionResult:
        from pathlib import Path

        dest = Path(payload.package_path)
        path = self.registry.export_marketplace_package(
            dest,
            payload.skill_names,
            as_zip=bool(payload.as_zip),
            author=payload.author or "",
            description=payload.description or "",
        )
        return ActionResult(
            status="succeeded",
            summary=f"Exported marketplace package to {path} (offline, no telemetry)",
            tool="skill",
            action="export_package",
            data={"path": str(path), "offline_only": True, "telemetry": False},
            effects=ActionEffects(changed=True),
        )

    async def _import_package(self, payload: SkillToolRequest) -> ActionResult:
        from pathlib import Path
        from skill_evaluation import SkillEvaluator

        result = self.registry.import_marketplace_package(
            Path(payload.package_path),
            overwrite=bool(payload.overwrite),
            run_evaluation=False,
        )
        evaluator = SkillEvaluator(self.registry, use_subprocess=False)
        if payload.run_evaluation is not False:
            for name in result.imported:
                report = await evaluator.evaluate(name)
                if not report.passed:
                    result.errors.append(f"{name}: evaluation failed")
        return ActionResult(
            status="succeeded" if not result.errors else "failed",
            summary=(
                f"Imported {len(result.imported)} skill(s), skipped {len(result.skipped)}"
                + (f", {len(result.errors)} error(s)" if result.errors else "")
            ),
            tool="skill",
            action="import_package",
            data={**result.to_dict(), "offline_only": True, "telemetry": False},
            effects=ActionEffects(changed=True),
        )

    async def _upgrade_dynamic(self, payload: SkillToolRequest) -> ActionResult:
        manifest = manifest_from_dynamic_tool(payload.tool_spec)
        code = payload.tool_spec.get("code", "")
        if not code:
            return ActionResult(
                status="failed", summary="Dynamic tool has no code to upgrade",
                tool="skill", action="upgrade_dynamic",
            )
        provenance = build_provenance(
            source="dynamic_tool_upgrade",
            parent_skill=manifest.name,
            notes=f"Upgraded from dynamic tool {payload.tool_spec.get('name', '')}",
        )
        result = self.registry.register(
            manifest, code, overwrite=True, provenance=provenance, trust_on_import=True,
        )
        return ActionResult(
            status="succeeded",
            summary=f"Upgraded dynamic tool to skill '{result.name}' v{result.version} (signed)",
            tool="skill", action="upgrade_dynamic",
            semantic_type="mutation",
            data=result.to_dict(),
            effects=ActionEffects(changed=True),
        )
