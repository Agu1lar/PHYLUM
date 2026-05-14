# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Visual perception tool — capture, analyze, ground and verify via screenshots."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from action_models import ActionEffects, ActionIssue, ActionResult
from tool_base import BaseTool
from visual_perception import VisualPerceptionEngine
from visual_grounding import VisualGroundingEngine
from visual_verification import VisualVerifier
from visual_policy import VisualPolicy, AutomationMode

logger = logging.getLogger(__name__)


class VisualRequest(BaseModel):
    action: str = Field(
        ...,
        pattern="^(capture_screen|capture_window|analyze_elements|ground_element|verify_action|evaluate_fallback)$",
    )
    hwnd: Optional[int] = None
    title: Optional[str] = None
    process_name: Optional[str] = None
    region: Optional[Dict[str, int]] = None
    text: Optional[str] = None
    # For verify_action: paths to before/after screenshots
    before_path: Optional[str] = None
    after_path: Optional[str] = None
    # For evaluate_fallback
    tool: Optional[str] = None
    target_action: Optional[str] = None
    native_failed: bool = False
    native_error: Optional[str] = None
    grounded: bool = False
    grounding_score: float = 0.0
    semantic_type: str = "inspection"
    mutates_state: bool = False


class VisualTool(BaseTool):
    InputModel = VisualRequest
    OutputModel = ActionResult

    def __init__(self, *, default_timeout: int = 30, default_retries: int = 1, ui_agent=None):
        super().__init__(default_timeout=default_timeout, default_retries=default_retries)
        self.perception = VisualPerceptionEngine()
        self.grounding = VisualGroundingEngine(ui_agent=ui_agent)
        self.verifier = VisualVerifier(engine=self.perception)
        self.policy = VisualPolicy()
        self._last_screenshot = None

    def set_ui_agent(self, ui_agent) -> None:
        self.grounding.set_ui_agent(ui_agent)

    async def validate(self, payload: VisualRequest) -> None:
        if payload.action == "ground_element" and not payload.text:
            raise ValueError("ground_element requires 'text' to search for")
        if payload.action == "evaluate_fallback" and (not payload.tool or not payload.target_action):
            raise ValueError("evaluate_fallback requires 'tool' and 'target_action'")

    def _target(self, payload: VisualRequest) -> Dict[str, object]:
        return {
            k: v for k, v in {
                "hwnd": payload.hwnd,
                "title": payload.title,
                "process_name": payload.process_name,
            }.items() if v is not None
        }

    async def _run(self, payload: VisualRequest) -> ActionResult:
        try:
            if payload.action == "capture_screen":
                return await self._capture_screen(payload)
            if payload.action == "capture_window":
                return await self._capture_window(payload)
            if payload.action == "analyze_elements":
                return await self._analyze_elements(payload)
            if payload.action == "ground_element":
                return await self._ground_element(payload)
            if payload.action == "verify_action":
                return await self._verify_action(payload)
            if payload.action == "evaluate_fallback":
                return await self._evaluate_fallback(payload)
            return ActionResult(
                status="failed",
                summary=f"Unknown action: {payload.action}",
                tool="visual",
                action=payload.action,
            )
        except Exception as exc:
            return ActionResult(
                status="failed",
                summary=str(exc),
                tool="visual",
                action=payload.action,
                issue=ActionIssue(
                    kind="visual_error",
                    message=str(exc),
                    retryable=False,
                ),
            )

    async def _capture_screen(self, payload: VisualRequest) -> ActionResult:
        region = None
        if payload.region:
            region = (
                payload.region.get("left", 0),
                payload.region.get("top", 0),
                payload.region.get("right", 0),
                payload.region.get("bottom", 0),
            )
        screenshot = await self.perception.capture_screen(region=region)
        self._last_screenshot = screenshot
        return ActionResult(
            status="succeeded",
            summary=f"Captured screen ({screenshot.width}x{screenshot.height}), detected {len(screenshot.elements)} text elements.",
            tool="visual",
            action="capture_screen",
            semantic_type="inspection",
            target=self._target(payload),
            data=screenshot.to_dict(),
        )

    async def _capture_window(self, payload: VisualRequest) -> ActionResult:
        screenshot = await self.perception.capture_window(
            hwnd=payload.hwnd,
            title=payload.title,
        )
        self._last_screenshot = screenshot
        return ActionResult(
            status="succeeded",
            summary=f"Captured window '{screenshot.window_title}' ({screenshot.width}x{screenshot.height}), detected {len(screenshot.elements)} text elements.",
            tool="visual",
            action="capture_window",
            semantic_type="inspection",
            target=self._target(payload),
            data=screenshot.to_dict(),
        )

    async def _analyze_elements(self, payload: VisualRequest) -> ActionResult:
        if self._last_screenshot is None:
            screenshot = await self.perception.capture_screen()
        else:
            screenshot = self._last_screenshot

        patterns = await self.perception.detect_ui_patterns(screenshot.elements)
        pattern_counts = {k: len(v) for k, v in patterns.items()}

        return ActionResult(
            status="succeeded",
            summary=f"Analyzed {len(screenshot.elements)} elements: {pattern_counts}",
            tool="visual",
            action="analyze_elements",
            semantic_type="inspection",
            data={
                "element_count": len(screenshot.elements),
                "patterns": {k: [e.to_dict() for e in v[:5]] for k, v in patterns.items()},
                "pattern_counts": pattern_counts,
            },
        )

    async def _ground_element(self, payload: VisualRequest) -> ActionResult:
        if self._last_screenshot is None:
            if payload.hwnd or payload.title:
                screenshot = await self.perception.capture_window(
                    hwnd=payload.hwnd, title=payload.title,
                )
            else:
                screenshot = await self.perception.capture_screen()
            self._last_screenshot = screenshot
        else:
            screenshot = self._last_screenshot

        grounded = await self.grounding.ground_single(
            payload.text,
            screenshot,
            hwnd=payload.hwnd,
            title=payload.title,
            process_name=payload.process_name,
        )

        if grounded is None:
            return ActionResult(
                status="failed",
                summary=f"Text '{payload.text}' not found in screenshot.",
                tool="visual",
                action="ground_element",
                semantic_type="inspection",
                issue=ActionIssue(
                    kind="element_not_found",
                    message=f"Could not find '{payload.text}' in the current visual state.",
                    retryable=True,
                ),
            )

        return ActionResult(
            status="succeeded",
            summary=f"Found '{payload.text}' — grounded={grounded.verified}, method={grounded.grounding_method}, score={grounded.match_score:.2f}",
            tool="visual",
            action="ground_element",
            semantic_type="inspection",
            data=grounded.to_dict(),
        )

    async def _verify_action(self, payload: VisualRequest) -> ActionResult:
        before = await self.verifier.capture_before(
            hwnd=payload.hwnd, title=payload.title,
        )
        return ActionResult(
            status="succeeded",
            summary="Pre-action screenshot captured. Call again after the action to compare.",
            tool="visual",
            action="verify_action",
            semantic_type="inspection",
            data={"before": before.to_dict(), "status": "awaiting_after"},
        )

    async def _evaluate_fallback(self, payload: VisualRequest) -> ActionResult:
        decision = self.policy.evaluate(
            tool=payload.tool or "",
            action=payload.target_action or "",
            native_failed=payload.native_failed,
            native_error=payload.native_error,
            grounded=payload.grounded,
            grounding_score=payload.grounding_score,
            semantic_type=payload.semantic_type,
            mutates_state=payload.mutates_state,
        )
        return ActionResult(
            status="succeeded",
            summary=f"Fallback policy: mode={decision.mode}, allowed={decision.allowed}. {decision.reason}",
            tool="visual",
            action="evaluate_fallback",
            semantic_type="inspection",
            data=decision.to_dict(),
        )
