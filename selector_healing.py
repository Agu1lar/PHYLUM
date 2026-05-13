"""Proactive Selector Healing Engine.

When a UI selector fails (element_not_found), this engine:
1. Queries the World Model for previously-known selectors matching the same intent
2. Scores each candidate against the available UI elements
3. If a healed candidate matches, executes the action transparently
4. Updates the World Model with the healed selector at renewed confidence

The healing pipeline is: fail -> search World Model -> try candidates -> heal -> update.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from world_model import WorldEntity, WorldModel
    from windows_ui_agent import WindowsUiAgent

logger = logging.getLogger(__name__)

HEAL_MIN_SCORE = 0.60
HEAL_CONFIDENCE_BOOST = 0.15
HEAL_CONFIDENCE_ON_SUCCESS = 0.90
HEAL_MAX_CANDIDATES = 10


def _selector_intent_key(selector: Dict[str, Any]) -> str:
    """Build a human-readable intent key from selector fields for World Model queries."""
    parts: List[str] = []
    for field in ("title", "auto_id", "control_type", "class_name", "parent_title", "near_title"):
        val = selector.get(field)
        if val:
            parts.append(str(val).lower().strip())
    return " ".join(parts) if parts else ""


def _selector_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    """Quick similarity score between two selector dicts (0.0-1.0)."""
    if not a or not b:
        return 0.0
    fields = ["title", "auto_id", "control_type", "class_name",
              "parent_title", "parent_control_type", "process_name"]
    matches = 0
    total = 0
    for field in fields:
        va = str(a.get(field) or "").lower().strip()
        vb = str(b.get(field) or "").lower().strip()
        if not va and not vb:
            continue
        total += 1
        if va == vb:
            matches += 1
        elif va and vb and (va in vb or vb in va):
            matches += 0.6
    return matches / total if total > 0 else 0.0


class HealingResult:
    """Outcome of a selector healing attempt."""
    __slots__ = ("healed", "original_selector", "healed_selector",
                 "healed_element", "source", "score", "world_entity_key",
                 "candidates_tried", "reason")

    def __init__(
        self,
        *,
        healed: bool = False,
        original_selector: Optional[Dict[str, Any]] = None,
        healed_selector: Optional[Dict[str, Any]] = None,
        healed_element: Optional[Dict[str, Any]] = None,
        source: str = "",
        score: float = 0.0,
        world_entity_key: Optional[str] = None,
        candidates_tried: int = 0,
        reason: str = "",
    ):
        self.healed = healed
        self.original_selector = original_selector
        self.healed_selector = healed_selector
        self.healed_element = healed_element
        self.source = source
        self.score = score
        self.world_entity_key = world_entity_key
        self.candidates_tried = candidates_tried
        self.reason = reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "healed": self.healed,
            "original_selector": self.original_selector,
            "healed_selector": self.healed_selector,
            "source": self.source,
            "score": self.score,
            "world_entity_key": self.world_entity_key,
            "candidates_tried": self.candidates_tried,
            "reason": self.reason,
        }


class SelectorHealer:
    """Proactive selector self-healing using the World Model.

    Usage:
        healer = SelectorHealer(world_model, ui_agent)
        result = await healer.heal(
            failed_selector={"title": "Salvar", "control_type": "Button"},
            window_title="Notepad",
            process_name="notepad.exe",
        )
        if result.healed:
            # Use result.healed_selector and result.healed_element
    """

    def __init__(self, world_model: WorldModel, ui_agent: Optional[WindowsUiAgent] = None):
        self.world_model = world_model
        self.ui_agent = ui_agent

    async def heal(
        self,
        *,
        failed_selector: Dict[str, Any],
        hwnd: Optional[int] = None,
        window_title: Optional[str] = None,
        process_name: Optional[str] = None,
        include_children: bool = True,
    ) -> HealingResult:
        """Attempt to heal a failed selector using World Model knowledge.

        Steps:
        1. Build intent key from the failed selector
        2. Query World Model for similar selectors (same app context, fuzzy match)
        3. For each candidate, try resolving against the live UI tree
        4. If a candidate succeeds, update the World Model and return healed result
        """
        intent = _selector_intent_key(failed_selector)
        app_context = (process_name or "").lower().strip() or None

        candidates = await self._find_healing_candidates(
            intent=intent,
            failed_selector=failed_selector,
            app_context=app_context,
        )

        if not candidates:
            return HealingResult(
                original_selector=failed_selector,
                reason="no_candidates_in_world_model",
            )

        tried = 0
        for entity_key, candidate_selector, entity_confidence in candidates:
            tried += 1
            if candidate_selector == failed_selector:
                continue

            live_result = await self._try_selector_on_ui(
                candidate_selector,
                hwnd=hwnd,
                title=window_title,
                process_name=process_name,
                include_children=include_children,
            )

            if live_result is not None:
                match_element, match_score = live_result
                if match_score >= HEAL_MIN_SCORE:
                    await self._on_heal_success(
                        entity_key=entity_key,
                        healed_selector=candidate_selector,
                        original_selector=failed_selector,
                        app_context=app_context,
                        match_score=match_score,
                    )
                    return HealingResult(
                        healed=True,
                        original_selector=failed_selector,
                        healed_selector=candidate_selector,
                        healed_element=match_element,
                        source="world_model",
                        score=match_score,
                        world_entity_key=entity_key,
                        candidates_tried=tried,
                        reason="healed_from_world_model",
                    )

        return HealingResult(
            original_selector=failed_selector,
            candidates_tried=tried,
            reason="no_candidate_matched_live_ui",
        )

    async def _find_healing_candidates(
        self,
        *,
        intent: str,
        failed_selector: Dict[str, Any],
        app_context: Optional[str],
    ) -> List[Tuple[str, Dict[str, Any], float]]:
        """Query World Model for candidate selectors ordered by relevance."""
        results: List[Tuple[str, Dict[str, Any], float]] = []

        if app_context:
            entities = await self.world_model.query(
                "selector",
                app_context=app_context,
                min_confidence=0.2,
                limit=HEAL_MAX_CANDIDATES * 2,
            )
            for entity in entities:
                sel_data = entity.value if isinstance(entity.value, dict) else {}
                sim = _selector_similarity(failed_selector, sel_data)
                if sim >= 0.3:
                    results.append((entity.key, sel_data, entity.effective_confidence * sim))

        if intent:
            entities_by_query = await self.world_model.query(
                "selector",
                query=intent,
                min_confidence=0.2,
                limit=HEAL_MAX_CANDIDATES,
            )
            existing_keys = {r[0] for r in results}
            for entity in entities_by_query:
                if entity.key in existing_keys:
                    continue
                sel_data = entity.value if isinstance(entity.value, dict) else {}
                sim = _selector_similarity(failed_selector, sel_data)
                if sim >= 0.2:
                    results.append((entity.key, sel_data, entity.effective_confidence * sim))

        results.sort(key=lambda r: -r[2])
        return results[:HEAL_MAX_CANDIDATES]

    async def _try_selector_on_ui(
        self,
        candidate_selector: Dict[str, Any],
        *,
        hwnd: Optional[int],
        title: Optional[str],
        process_name: Optional[str],
        include_children: bool,
    ) -> Optional[Tuple[Dict[str, Any], float]]:
        """Try a candidate selector against the live UI tree via WindowsUiAgent.

        Returns (element_dict, score) if found, None otherwise.
        """
        if self.ui_agent is None:
            return None
        try:
            window, matches = self.ui_agent._resolve_candidates(
                hwnd=hwnd,
                title=title,
                process_name=process_name,
                selector=candidate_selector,
                include_children=include_children,
                max_results=3,
            )
            if not matches:
                return None
            best = self.ui_agent._snapshot(matches[0])
            score = float(best.match_score or 0.0)
            return (best.dict(), score)
        except Exception:
            logger.debug("Healing candidate failed against live UI", exc_info=True)
            return None

    async def _on_heal_success(
        self,
        *,
        entity_key: str,
        healed_selector: Dict[str, Any],
        original_selector: Dict[str, Any],
        app_context: Optional[str],
        match_score: float,
    ) -> None:
        """Update the World Model after a successful heal."""
        await self.world_model.remember_selector(
            entity_key,
            healed_selector,
            app_context=app_context,
            confidence=HEAL_CONFIDENCE_ON_SUCCESS,
            source="selector_healing",
        )

        original_intent = _selector_intent_key(original_selector)
        if original_intent and original_intent != entity_key:
            await self.world_model.remember_selector(
                original_intent,
                healed_selector,
                app_context=app_context,
                confidence=min(0.85, match_score),
                source="selector_healing_alias",
            )

        await self.world_model.touch("selector", entity_key, boost_confidence=HEAL_CONFIDENCE_BOOST)

    async def record_successful_selector(
        self,
        *,
        selector: Dict[str, Any],
        process_name: Optional[str] = None,
        score: float = 0.85,
    ) -> None:
        """Record a selector that succeeded in the World Model for future healing."""
        intent = _selector_intent_key(selector)
        if not intent:
            return
        app_context = (process_name or "").lower().strip() or None
        await self.world_model.remember_selector(
            intent,
            selector,
            app_context=app_context,
            confidence=min(1.0, max(0.5, score)),
            source="successful_resolution",
        )
