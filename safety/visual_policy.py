# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Anti-Fragility Visual Policy.

Determines when to use visual (pixel/screenshot) automation vs native
APIs.  The policy is: always prefer native APIs (UIA, COM, DOM); only
fall back to visual when native paths have failed and the action is
safe to perform via coordinates.

The policy also governs mouse/keyboard fallback constraints: actions
via coordinates must have verified bounding boxes from the grounding
engine, and destructive actions are never allowed via pixel automation
without explicit user approval.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AutomationMode:
    NATIVE = "native"
    VISUAL_GROUNDED = "visual_grounded"
    VISUAL_UNGROUNDED = "visual_ungrounded"
    DENIED = "denied"


@dataclass
class FallbackDecision:
    """Result of a policy evaluation for visual fallback."""
    mode: str
    reason: str
    allowed: bool
    requires_approval: bool = False
    constraints: List[str] = None  # type: ignore[assignment]
    recommended_action: Optional[str] = None

    def __post_init__(self):
        if self.constraints is None:
            self.constraints = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "reason": self.reason,
            "allowed": self.allowed,
            "requires_approval": self.requires_approval,
            "constraints": self.constraints,
            "recommended_action": self.recommended_action,
        }


NEVER_VISUAL_ACTIONS = frozenset({
    "filesystem.delete",
    "filesystem.clean_temp",
    "shell.run",
    "env_manager.set",
    "env_manager.delete",
    "memory.delete",
})

SAFE_VISUAL_ACTIONS = frozenset({
    "windows_ui.invoke_element",
    "windows_ui.set_text",
    "windows_ui.select_item",
    "windows_ui.scroll",
    "windows_ui.send_hotkey",
    "desktop.open_app",
    "desktop.open_path",
    "desktop.open_file",
})


class VisualPolicy:
    """Anti-fragility policy for visual automation fallback.

    Decision flow:
    1. If the action has a working native API path → NATIVE (no visual needed)
    2. If native failed but the action is in the safe list and grounding
       produced a verified bounding box → VISUAL_GROUNDED
    3. If grounding failed but the action is non-destructive →
       VISUAL_UNGROUNDED (with approval required)
    4. Otherwise → DENIED
    """

    def evaluate(
        self,
        *,
        tool: str,
        action: str,
        native_failed: bool = False,
        native_error: Optional[str] = None,
        grounded: bool = False,
        grounding_score: float = 0.0,
        semantic_type: str = "inspection",
        mutates_state: bool = False,
    ) -> FallbackDecision:
        action_key = f"{tool}.{action}"

        if not native_failed:
            return FallbackDecision(
                mode=AutomationMode.NATIVE,
                reason="Native API path is available and should be used.",
                allowed=True,
            )

        if action_key in NEVER_VISUAL_ACTIONS:
            return FallbackDecision(
                mode=AutomationMode.DENIED,
                reason=f"Action {action_key} is never allowed via visual automation.",
                allowed=False,
                recommended_action="request_user_input",
            )

        if grounded and grounding_score >= 0.7:
            if action_key in SAFE_VISUAL_ACTIONS or not mutates_state:
                return FallbackDecision(
                    mode=AutomationMode.VISUAL_GROUNDED,
                    reason=f"Native path failed ({native_error}). Grounded element found (score={grounding_score:.2f}).",
                    allowed=True,
                    constraints=[
                        "use_verified_bbox",
                        "verify_after_action",
                        "single_action_only",
                    ],
                )
            return FallbackDecision(
                mode=AutomationMode.VISUAL_GROUNDED,
                reason=f"Native path failed. Grounded element found but action mutates state.",
                allowed=True,
                requires_approval=True,
                constraints=[
                    "use_verified_bbox",
                    "verify_after_action",
                    "single_action_only",
                    "require_approval",
                ],
            )

        if grounded and grounding_score < 0.7:
            return FallbackDecision(
                mode=AutomationMode.VISUAL_UNGROUNDED,
                reason=f"Grounding score too low ({grounding_score:.2f}). Visual fallback needs approval.",
                allowed=True,
                requires_approval=True,
                constraints=[
                    "low_confidence_grounding",
                    "verify_after_action",
                    "single_action_only",
                ],
            )

        if not mutates_state:
            return FallbackDecision(
                mode=AutomationMode.VISUAL_UNGROUNDED,
                reason=f"Native path failed, no grounding available, but action is read-only.",
                allowed=True,
                requires_approval=True,
                constraints=[
                    "no_grounding",
                    "verify_after_action",
                ],
            )

        return FallbackDecision(
            mode=AutomationMode.DENIED,
            reason="Native path failed, no grounding, and action mutates state. Cannot proceed visually.",
            allowed=False,
            recommended_action="request_user_input",
        )

    def should_capture_before_after(
        self,
        *,
        tool: str,
        action: str,
        mode: str,
    ) -> bool:
        """Determine if before/after screenshots should be captured for this action."""
        if mode in (AutomationMode.VISUAL_GROUNDED, AutomationMode.VISUAL_UNGROUNDED):
            return True
        if tool == "desktop" and action in ("open_app", "open_path", "open_file"):
            return True
        if tool == "windows_ui" and action in ("invoke_element", "set_text", "select_item"):
            return True
        return False
