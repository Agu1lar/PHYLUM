# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Tool facade for the patch planner."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from action_models import ActionResult
from patch_planner import FileChange, PatchPlanner
from tool_base import BaseTool

logger = logging.getLogger(__name__)


class PatchPlannerRequest(BaseModel):
    action: str = Field(
        ...,
        pattern="^(plan|assess_risk|order_changes)$",
    )
    changes: Optional[List[Dict[str, Any]]] = None


class PatchPlannerTool(BaseTool):
    InputModel = PatchPlannerRequest

    def __init__(self):
        super().__init__(default_timeout=30, default_retries=1)
        self._planner = PatchPlanner()

    async def _run(self, payload: PatchPlannerRequest) -> ActionResult:
        action = payload.action

        try:
            if action == "plan":
                if not payload.changes:
                    return ActionResult(
                        status="failed",
                        summary="'changes' list is required",
                        tool="patch_planner", action=action,
                    )
                plan = self._planner.plan_from_dicts(payload.changes)
                return ActionResult(
                    status="succeeded",
                    summary=f"Plan '{plan.plan_id}': {plan.total_files} files, "
                            f"risk={plan.overall_risk}, "
                            f"+{plan.total_lines_added}/-{plan.total_lines_removed} lines, "
                            f"{len(plan.warnings)} warning(s)",
                    tool="patch_planner", action=action,
                    data=plan.to_dict(),
                )

            if action == "assess_risk":
                if not payload.changes:
                    return ActionResult(
                        status="failed",
                        summary="'changes' list is required",
                        tool="patch_planner", action=action,
                    )
                risks = []
                for d in payload.changes:
                    change = FileChange(
                        path=d.get("path", ""),
                        change_type=d.get("change_type", "modify"),
                        lines_added=d.get("lines_added", 0),
                        lines_removed=d.get("lines_removed", 0),
                        is_test=d.get("is_test", False),
                        is_config=d.get("is_config", False),
                    )
                    risk = self._planner._risk_assessor.assess(change)
                    risks.append(risk.to_dict())
                return ActionResult(
                    status="succeeded",
                    summary=f"Assessed risk for {len(risks)} file(s)",
                    tool="patch_planner", action=action,
                    data={"risks": risks},
                )

            if action == "order_changes":
                if not payload.changes:
                    return ActionResult(
                        status="failed",
                        summary="'changes' list is required",
                        tool="patch_planner", action=action,
                    )
                changes = [
                    FileChange(
                        path=d.get("path", ""),
                        change_type=d.get("change_type", "modify"),
                        is_test=d.get("is_test", False),
                        is_config=d.get("is_config", False),
                    )
                    for d in payload.changes
                ]
                ordered = self._planner._orderer.order(changes)
                return ActionResult(
                    status="succeeded",
                    summary=f"Ordered {len(ordered)} changes for application",
                    tool="patch_planner", action=action,
                    data={"order": [c.to_dict() for c in ordered]},
                )

            return ActionResult(
                status="failed", summary=f"Unknown action: {action}",
                tool="patch_planner", action=action,
            )

        except Exception as exc:
            return ActionResult(
                status="failed", summary=str(exc),
                tool="patch_planner", action=action,
            )
