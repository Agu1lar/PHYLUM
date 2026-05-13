# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


ActionStatus = Literal["succeeded", "failed", "blocked", "needs_input", "partial"]
ApprovalMode = Literal["none", "single", "double"]


class ActionIntent(BaseModel):
    tool: str
    action: str
    params: Dict[str, Any] = Field(default_factory=dict)
    task_id: Optional[str] = None
    title: Optional[str] = None


class ActionIssue(BaseModel):
    kind: str
    code: Optional[str] = None
    message: str
    retryable: bool = False
    user_action_required: Optional[str] = None
    missing_fields: List[str] = Field(default_factory=list)
    candidates: List[Dict[str, Any]] = Field(default_factory=list)
    details: Dict[str, Any] = Field(default_factory=dict)


class ActionEffects(BaseModel):
    changed: bool = False
    predicted_effects: List[Dict[str, Any]] = Field(default_factory=list)
    artifacts: List[Dict[str, Any]] = Field(default_factory=list)
    before: Optional[Dict[str, Any]] = None
    after: Optional[Dict[str, Any]] = None
    rollback: Dict[str, Any] = Field(default_factory=lambda: {"available": False, "reference": None})


class ApprovalRequirement(BaseModel):
    required: bool = False
    mode: ApprovalMode = "none"
    summary: Optional[str] = None
    reason_code: Optional[str] = None
    risk: Dict[str, Any] = Field(default_factory=dict)
    predicted_effects: List[Dict[str, Any]] = Field(default_factory=list)
    scope: Dict[str, Any] = Field(default_factory=dict)
    reversibility: Optional[str] = None


class GoalVerification(BaseModel):
    satisfied: bool = False
    strategy: Optional[str] = None
    confidence: float = 0.0
    rationale: Optional[str] = None
    evidence: Dict[str, Any] = Field(default_factory=dict)
    recommended_followups: List[str] = Field(default_factory=list)


class ActionResult(BaseModel):
    status: ActionStatus
    summary: str
    tool: str
    action: str
    semantic_type: str = "inspection"
    target: Dict[str, Any] = Field(default_factory=dict)
    data: Dict[str, Any] = Field(default_factory=dict)
    effects: ActionEffects = Field(default_factory=ActionEffects)
    issue: Optional[ActionIssue] = None
    diagnostics: Dict[str, Any] = Field(default_factory=dict)
    approval: Optional[ApprovalRequirement] = None
    goal: Optional[GoalVerification] = None


def action_succeeded(action_result: Dict[str, Any]) -> bool:
    return action_result.get("status") == "succeeded"


def action_needs_model_followup(action_result: Dict[str, Any]) -> bool:
    return action_result.get("status") in {"blocked", "needs_input", "partial", "failed"}
