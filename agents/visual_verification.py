# Copyright (C) 2026 Aguilar. This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by the Free Software Foundation,
# either version 3 of the License, or any later version.
"""Post-action Visual Verification.

Captures before/after screenshots around a UI action and compares them
to detect whether the action had visible effect.  Also detects modals,
spinners, error dialogs and confirmation messages that might indicate
the action needs follow-up.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from visual_perception import ScreenshotState, VisualPerceptionEngine, VisualElement

logger = logging.getLogger(__name__)


@dataclass
class VisualDiff:
    """Comparison between two screenshot states."""
    changed: bool = False
    similarity: float = 1.0
    new_elements: List[VisualElement] = field(default_factory=list)
    removed_elements: List[VisualElement] = field(default_factory=list)
    detected_modals: List[VisualElement] = field(default_factory=list)
    detected_errors: List[VisualElement] = field(default_factory=list)
    detected_spinners: bool = False
    detected_confirmations: List[VisualElement] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "changed": self.changed,
            "similarity": self.similarity,
            "new_elements_count": len(self.new_elements),
            "removed_elements_count": len(self.removed_elements),
            "new_elements": [e.to_dict() for e in self.new_elements[:10]],
            "removed_elements": [e.to_dict() for e in self.removed_elements[:10]],
            "detected_modals": [e.to_dict() for e in self.detected_modals],
            "detected_errors": [e.to_dict() for e in self.detected_errors],
            "detected_spinners": self.detected_spinners,
            "detected_confirmations": [e.to_dict() for e in self.detected_confirmations],
        }


class VisualVerifier:
    """Compares before/after visual state to verify action effects."""

    SPINNER_KEYWORDS = {"loading", "please wait", "processing", "saving",
                        "working", "updating", "installing", "downloading"}
    ERROR_KEYWORDS = {"error", "failed", "failure", "exception", "denied",
                     "not found", "access denied", "permission", "invalid",
                     "warning", "critical", "unable", "could not"}
    MODAL_KEYWORDS = {"are you sure", "confirm", "do you want", "would you like",
                     "save changes", "unsaved", "overwrite", "replace"}
    CONFIRM_KEYWORDS = {"success", "completed", "saved", "done", "finished",
                       "created", "updated", "applied", "installed"}

    def __init__(self, engine: Optional[VisualPerceptionEngine] = None):
        self.engine = engine or VisualPerceptionEngine()

    async def capture_before(
        self,
        *,
        hwnd: Optional[int] = None,
        title: Optional[str] = None,
    ) -> ScreenshotState:
        """Capture the pre-action screenshot."""
        if hwnd or title:
            return await self.engine.capture_window(hwnd=hwnd, title=title)
        return await self.engine.capture_screen()

    async def capture_after(
        self,
        *,
        hwnd: Optional[int] = None,
        title: Optional[str] = None,
    ) -> ScreenshotState:
        """Capture the post-action screenshot."""
        if hwnd or title:
            return await self.engine.capture_window(hwnd=hwnd, title=title)
        return await self.engine.capture_screen()

    def compare(self, before: ScreenshotState, after: ScreenshotState) -> VisualDiff:
        """Compare two screenshot states and produce a diff."""
        changed = before.image_hash != after.image_hash
        similarity = 1.0 if not changed else self._element_similarity(before.elements, after.elements)

        before_texts = {el.text.lower().strip() for el in before.elements if el.text.strip()}
        after_texts = {el.text.lower().strip() for el in after.elements if el.text.strip()}

        new_texts = after_texts - before_texts
        removed_texts = before_texts - after_texts

        new_elements = [el for el in after.elements if el.text.lower().strip() in new_texts]
        removed_elements = [el for el in before.elements if el.text.lower().strip() in removed_texts]

        detected_modals = self._detect_keywords(after.elements, self.MODAL_KEYWORDS)
        detected_errors = self._detect_keywords(after.elements, self.ERROR_KEYWORDS)
        detected_spinners = self._detect_keywords_bool(after.elements, self.SPINNER_KEYWORDS)
        detected_confirmations = self._detect_keywords(after.elements, self.CONFIRM_KEYWORDS)

        # Filter out confirmations that were already present before the action
        detected_confirmations = [
            e for e in detected_confirmations
            if e.text.lower().strip() not in before_texts
        ]

        return VisualDiff(
            changed=changed,
            similarity=similarity,
            new_elements=new_elements,
            removed_elements=removed_elements,
            detected_modals=detected_modals,
            detected_errors=detected_errors,
            detected_spinners=detected_spinners,
            detected_confirmations=detected_confirmations,
        )

    async def verify_action(
        self,
        before: ScreenshotState,
        after: ScreenshotState,
    ) -> Dict[str, Any]:
        """High-level verification: did the action succeed visually?"""
        diff = self.compare(before, after)

        verdict = "unknown"
        confidence = 0.5
        issues: List[str] = []

        if diff.detected_errors:
            verdict = "error_detected"
            confidence = 0.85
            issues.extend(f"Error: {e.text}" for e in diff.detected_errors[:3])

        elif diff.detected_modals:
            verdict = "modal_detected"
            confidence = 0.75
            issues.extend(f"Modal: {e.text}" for e in diff.detected_modals[:3])

        elif diff.detected_spinners:
            verdict = "in_progress"
            confidence = 0.60
            issues.append("Spinner/loading indicator detected — action may still be in progress")

        elif diff.detected_confirmations:
            verdict = "confirmed"
            confidence = 0.90

        elif diff.changed:
            verdict = "changed"
            confidence = 0.70

        elif not diff.changed:
            verdict = "no_change"
            confidence = 0.55
            issues.append("No visible change detected after the action")

        return {
            "verdict": verdict,
            "confidence": confidence,
            "issues": issues,
            "diff": diff.to_dict(),
            "before_hash": before.image_hash,
            "after_hash": after.image_hash,
        }

    @staticmethod
    def _element_similarity(before: List[VisualElement], after: List[VisualElement]) -> float:
        if not before and not after:
            return 1.0
        before_set = {el.text.lower().strip() for el in before if el.text.strip()}
        after_set = {el.text.lower().strip() for el in after if el.text.strip()}
        if not before_set and not after_set:
            return 1.0
        intersection = len(before_set & after_set)
        union = len(before_set | after_set)
        return intersection / union if union > 0 else 0.0

    @staticmethod
    def _detect_keywords(elements: List[VisualElement], keywords: set) -> List[VisualElement]:
        found = []
        for el in elements:
            text_lower = el.text.lower()
            if any(kw in text_lower for kw in keywords):
                found.append(el)
        return found

    @staticmethod
    def _detect_keywords_bool(elements: List[VisualElement], keywords: set) -> bool:
        for el in elements:
            text_lower = el.text.lower()
            if any(kw in text_lower for kw in keywords):
                return True
        return False
