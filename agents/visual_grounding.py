# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Visual Grounding — maps OCR-detected elements to native UIA selectors.

When visual perception detects text or controls in a screenshot, the
grounding layer attempts to match each detection to a real UIA element
from pywinauto.  This bridges the gap between pixel coordinates and
native selectors, enabling fallback mouse/keyboard actions that are
*grounded* in verified bounding boxes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from visual_perception import ScreenshotState, VisualElement

logger = logging.getLogger(__name__)

try:
    from pywinauto import Desktop
except ImportError:
    Desktop = None


@dataclass
class GroundedElement:
    """A visual element matched to a UIA selector with a bounding box."""
    visual: VisualElement
    uia_selector: Optional[Dict[str, Any]] = None
    uia_rect: Optional[Dict[str, int]] = None
    match_score: float = 0.0
    grounding_method: str = "none"
    verified: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "visual": self.visual.to_dict(),
            "uia_selector": self.uia_selector,
            "uia_rect": self.uia_rect,
            "match_score": self.match_score,
            "grounding_method": self.grounding_method,
            "verified": self.verified,
        }


class VisualGroundingEngine:
    """Maps visual detections to native UIA elements.

    Strategy:
    1. For each OCR element, search the UIA tree for elements with
       matching text, overlapping bounding boxes, or matching control type.
    2. Score matches by text similarity + spatial overlap.
    3. Return a list of GroundedElement with the best UIA selector for each.
    """

    def __init__(self, *, ui_agent=None):
        self._ui_agent = ui_agent

    def set_ui_agent(self, ui_agent) -> None:
        self._ui_agent = ui_agent

    async def ground(
        self,
        screenshot: ScreenshotState,
        *,
        hwnd: Optional[int] = None,
        title: Optional[str] = None,
        process_name: Optional[str] = None,
    ) -> List[GroundedElement]:
        """Attempt to ground each visual element to a UIA selector."""
        if not screenshot.elements:
            return []

        uia_elements = await self._get_uia_tree(
            hwnd=hwnd or screenshot.hwnd,
            title=title or screenshot.window_title,
            process_name=process_name,
        )

        grounded: List[GroundedElement] = []
        for visual_el in screenshot.elements:
            best_match = self._find_best_uia_match(visual_el, uia_elements)
            if best_match:
                uia_el, score = best_match
                grounded.append(GroundedElement(
                    visual=visual_el,
                    uia_selector=self._build_selector(uia_el),
                    uia_rect=uia_el.get("rectangle"),
                    match_score=score,
                    grounding_method="text_bbox_match",
                    verified=score >= 0.7,
                ))
            else:
                grounded.append(GroundedElement(
                    visual=visual_el,
                    grounding_method="unmatched",
                ))
        return grounded

    async def ground_single(
        self,
        text: str,
        screenshot: ScreenshotState,
        *,
        hwnd: Optional[int] = None,
        title: Optional[str] = None,
        process_name: Optional[str] = None,
    ) -> Optional[GroundedElement]:
        """Find a single visual element by text and ground it."""
        text_lower = text.lower().strip()
        candidates = [el for el in screenshot.elements if text_lower in el.text.lower()]
        if not candidates:
            return None
        candidates.sort(key=lambda e: -e.confidence)
        best_visual = candidates[0]

        uia_elements = await self._get_uia_tree(
            hwnd=hwnd or screenshot.hwnd,
            title=title or screenshot.window_title,
            process_name=process_name,
        )
        best_match = self._find_best_uia_match(best_visual, uia_elements)
        if best_match:
            uia_el, score = best_match
            return GroundedElement(
                visual=best_visual,
                uia_selector=self._build_selector(uia_el),
                uia_rect=uia_el.get("rectangle"),
                match_score=score,
                grounding_method="text_bbox_match",
                verified=score >= 0.7,
            )
        return GroundedElement(
            visual=best_visual,
            grounding_method="visual_only",
        )

    def _find_best_uia_match(
        self,
        visual_el: VisualElement,
        uia_elements: List[Dict[str, Any]],
    ) -> Optional[Tuple[Dict[str, Any], float]]:
        if not uia_elements:
            return None

        best: Optional[Tuple[Dict[str, Any], float]] = None
        visual_text = visual_el.text.lower().strip()

        for uia_el in uia_elements:
            uia_text = (uia_el.get("title") or "").lower().strip()
            if not uia_text:
                continue

            text_score = self._text_similarity(visual_text, uia_text)
            bbox_score = self._bbox_overlap(visual_el.bbox, uia_el.get("rectangle", {}))
            combined = text_score * 0.6 + bbox_score * 0.4

            if combined > 0.3 and (best is None or combined > best[1]):
                best = (uia_el, combined)

        return best

    @staticmethod
    def _text_similarity(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        if a in b or b in a:
            return 0.8
        a_words = set(a.split())
        b_words = set(b.split())
        if not a_words or not b_words:
            return 0.0
        overlap = len(a_words & b_words)
        return overlap / max(len(a_words), len(b_words))

    @staticmethod
    def _bbox_overlap(visual_bbox: Tuple[int, int, int, int], uia_rect: Dict[str, int]) -> float:
        """Compute IoU between a visual bbox and a UIA rectangle."""
        if not uia_rect:
            return 0.0
        vl, vt, vr, vb = visual_bbox
        ul = uia_rect.get("left", 0)
        ut = uia_rect.get("top", 0)
        ur = uia_rect.get("right", 0)
        ub = uia_rect.get("bottom", 0)

        inter_l = max(vl, ul)
        inter_t = max(vt, ut)
        inter_r = min(vr, ur)
        inter_b = min(vb, ub)

        if inter_r <= inter_l or inter_b <= inter_t:
            return 0.0

        inter_area = (inter_r - inter_l) * (inter_b - inter_t)
        visual_area = max((vr - vl) * (vb - vt), 1)
        uia_area = max((ur - ul) * (ub - ut), 1)
        union_area = visual_area + uia_area - inter_area

        return inter_area / union_area if union_area > 0 else 0.0

    @staticmethod
    def _build_selector(uia_el: Dict[str, Any]) -> Dict[str, Any]:
        selector: Dict[str, Any] = {}
        for key in ("title", "control_type", "auto_id", "class_name", "process_name"):
            val = uia_el.get(key)
            if val:
                selector[key] = val
        return selector

    async def _get_uia_tree(
        self,
        *,
        hwnd: Optional[int] = None,
        title: Optional[str] = None,
        process_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if self._ui_agent is None:
            return []
        try:
            result = await self._ui_agent.inspect_window(
                hwnd=hwnd,
                title=title,
                process_name=process_name,
                include_children=True,
                max_results=100,
            )
            return result.get("elements", []) if isinstance(result, dict) else []
        except Exception:
            logger.debug("UIA tree fetch failed for grounding", exc_info=True)
            return []
